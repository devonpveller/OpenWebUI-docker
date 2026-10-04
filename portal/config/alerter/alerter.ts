#!/usr/bin/env -S deno run --allow-net --allow-read --allow-write --allow-env

/**
 * Portal Alerter - the single alert egress point for the internet-exposed portal.
 *
 * Every /alert goes to EVERY enabled channel (pa-channels, 2026-10-04):
 *   telegram    PRIMARY. The operator's sysadmin bot -> the operator's chat.
 *   mattermost  PRIMARY. A bot post into one channel (e.g. #sysadmin).
 *   email       SECOND COPY. Gmail self-send (OAuth, modeled on OB1's
 *               send-digest.ts); the only channel that carries the full detail.
 * The caller gets success if AT LEAST ONE channel delivered, so a sender's
 * "alerter POST failed" now means "no channel delivered". Each outcome is logged
 * per channel and written to a delivery-state file the HOST watchdog reads
 * (scripts/checks/stack-watchdog.ps1, Test-PortalAlertDelivery): "no channel
 * delivered" and "a configured channel keeps failing" become the watchdog's own
 * alert, through its own path - never through this service.
 *
 * Why: 09-29..10-04 the integrity tripwire saw the Authelia users DB vanish every
 * night and every alert died in here ("Token refresh failed: Bad Request" - the
 * Gmail OAuth files were 0-byte placeholders). Email was the only channel and
 * nothing recorded the failure anywhere a human or a watchdog would look.
 *
 * What third parties see. Telegram and Mattermost (whose push notifications go
 * through Mattermost's push proxy) get ONE minimal line: severity, a sanitized
 * event name, a short host label and the time. Never source_ip, username or
 * log_line: the Authelia notification bridge's log_line carries one-time codes
 * and reset links. Email keeps the full detail, as before.
 *
 * Email turns itself on and off. credentials.json / token.json are re-read on
 * every send: missing, empty (the 0-byte placeholders), unparseable or lacking
 * client_id/client_secret/refresh_token = "email disabled: OAuth not configured",
 * logged ONCE per change, and that is not a failure. Valid files appearing later
 * (setup-token.ts on the host writes token.json in place) re-enable it on the
 * next send, with no code change and no restart. Google REFUSING the stored
 * refresh token (invalid_grant: revoked, or a Testing-mode app's 7-day expiry -
 * what the live "Token refresh failed: Bad Request" was) is the same "not
 * configured" state, remembered against those exact file contents so it is not
 * retried on every alert; a new token.json turns email back on. A configured
 * email whose SENDS fail (Gmail down, quota) IS a failure, counted like any other
 * channel.
 *
 * Endpoints:
 *   POST /alert     - instant alert for a discrete event from a watcher
 *   POST /run       - scheduled traffic + threats digest (EMAIL ONLY: the digest
 *                     lists source IPs, which do not go to Telegram/Mattermost)
 *   GET  /health    - liveness + per-channel status (no secret values)
 *
 * CLI:
 *   --selftest      - send one test alert through every enabled channel, print
 *                     each outcome, exit 0 if at least one delivered.
 *
 * Environment (names only; values live in portal/.env):
 *   PORTAL_ALERT_TELEGRAM_BOT_TOKEN  Telegram bot token (the sysadmin bot).
 *   PORTAL_ALERT_TELEGRAM_CHAT_ID    The operator's chat id with that bot.
 *   PORTAL_ALERT_MM_URL              Mattermost base URL, e.g.
 *                                    http://host.docker.internal:8065.
 *   PORTAL_ALERT_MM_TOKEN            Bot token allowed to post in the channel.
 *   PORTAL_ALERT_MM_CHANNEL_ID       Channel id (not name) to post into.
 *   PORTAL_ALERT_MM_MENTION          Optional, e.g. "@you" - prefixed so the post
 *                                    notifies instead of sitting unread.
 *   PORTAL_ALERT_HOST_LABEL          Short host label in the minimal text
 *                                    (default "portal").
 *   PORTAL_ALERT_STATE_FILE          Delivery-state file (default
 *                                    /reports/alerter-delivery-state.json =
 *                                    <repo>/reports/portal-digest/ on the host).
 *   PORTAL_ALERT_TELEGRAM_API, PORTAL_ALERT_GOOGLE_TOKEN_URL,
 *   PORTAL_ALERT_GMAIL_SEND_URL      Endpoint overrides for the tests' fakes;
 *                                    leave unset in production.
 *   DIGEST_TO                  Operator's Gmail address. Unset = email disabled.
 *   DIGEST_FROM                Must match the consented Google account.
 *   DIGEST_WINDOW_HOURS        Default 24. Window the /run digest covers.
 *   ALERT_RATE_LIMIT_PER_MIN   Default 20. Max /alert sends per rolling minute;
 *                              excess are coalesced into one summary.
 *   PUBLIC_DOMAIN              Optional. Appears in email footers.
 *
 * Paths inside the container (matches docker-compose mounts):
 *   /app/credentials.json       - OAuth client (read-only)
 *   /app/token.json             - refresh token (writable)
 *   /logs/authelia/authelia.log - Authelia JSON log (read-only)
 *   /logs/caddy/caddy-access.log- Caddy JSON access log (read-only)
 *   /reports                    - markdown audit copies + delivery state (writable)
 */

// --- Paths -------------------------------------------------------------------

// URLs, not pathname strings: Deno's file APIs take a file: URL directly, which
// also works when the tests run this file from a Windows temp directory.
const CREDENTIALS_URL = new URL("./credentials.json", import.meta.url);
const TOKEN_URL = new URL("./token.json", import.meta.url);
const REPORT_DIR = "/reports";
const AUTHELIA_LOG = "/logs/authelia/authelia.log";
const CADDY_LOG = "/logs/caddy/caddy-access.log";

// --- Configuration -----------------------------------------------------------

function env(name: string): string {
  return (Deno.env.get(name) ?? "").trim();
}

const TO_EMAIL = env("DIGEST_TO");
const FROM_EMAIL = env("DIGEST_FROM") || TO_EMAIL;
const WINDOW_HOURS = parseInt(env("DIGEST_WINDOW_HOURS") || "24", 10);
const RATE_LIMIT_PER_MIN = parseInt(env("ALERT_RATE_LIMIT_PER_MIN") || "20", 10);
const PUBLIC_DOMAIN = env("PUBLIC_DOMAIN");
const PORT = parseInt(env("DIGEST_PORT") || "8080", 10);

const HOST_LABEL = (env("PORTAL_ALERT_HOST_LABEL") || "portal")
  .replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 32);
const STATE_FILE = env("PORTAL_ALERT_STATE_FILE") || "/reports/alerter-delivery-state.json";
const SEND_TIMEOUT_MS = 10_000;

const TG_TOKEN = env("PORTAL_ALERT_TELEGRAM_BOT_TOKEN");
const TG_CHAT = env("PORTAL_ALERT_TELEGRAM_CHAT_ID");
const TG_API = (env("PORTAL_ALERT_TELEGRAM_API") || "https://api.telegram.org").replace(/\/+$/, "");

const MM_URL = env("PORTAL_ALERT_MM_URL").replace(/\/+$/, "");
const MM_TOKEN = env("PORTAL_ALERT_MM_TOKEN");
const MM_CHANNEL = env("PORTAL_ALERT_MM_CHANNEL_ID");
const MM_MENTION = env("PORTAL_ALERT_MM_MENTION").replace(/[^@A-Za-z0-9._ -]/g, "").slice(0, 64);

const GOOGLE_TOKEN_URL = env("PORTAL_ALERT_GOOGLE_TOKEN_URL") || "https://oauth2.googleapis.com/token";
const GMAIL_SEND_URL = env("PORTAL_ALERT_GMAIL_SEND_URL") ||
  "https://gmail.googleapis.com/gmail/v1/users/me/messages/send";

// --- Secret redaction ----------------------------------------------------------
// Error text can carry a secret: Deno's fetch errors quote the request URL, and
// the Telegram URL embeds the bot token. Everything that reaches a log line, the
// state file or an HTTP response goes through redact().

const secretValues = new Set<string>([TG_TOKEN, MM_TOKEN].filter((s) => s.length >= 6));

function addSecret(s: string | undefined | null) {
  if (s && s.length >= 6) secretValues.add(s);
}

function redact(text: string): string {
  let out = text;
  for (const s of secretValues) out = out.split(s).join("[redacted]");
  out = out.replace(/bot\d+:[A-Za-z0-9_-]+/g, "bot[redacted]");
  out = out.replace(/Bearer\s+\S+/gi, "Bearer [redacted]");
  return out.replace(/\s+/g, " ").slice(0, 300);
}

function errText(err: unknown): string {
  return redact(err instanceof Error ? err.message : String(err));
}

// --- OAuth (mirrors OB1 send-digest.ts) --------------------------------------

interface OAuthClient {
  client_id: string;
  client_secret: string;
}

interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expiry_date: number;
}

type EmailConfig =
  | { ok: true; client: OAuthClient; token: TokenData; raw: string }
  | { ok: false; reason: string };

// A channel that turns out, mid-send, to be unusable for a CONFIGURATION reason
// (not an outage). dispatch() reports it as "off", not as a failed send.
class ChannelNotConfigured extends Error {}

// Google refusing the stored refresh token (revoked, or expired - a Testing-mode
// OAuth app's tokens die after 7 days) is a configuration fact, not an outage:
// retrying it on every alert only repeats "Token refresh failed: Bad Request",
// the 09-29..10-04 incident line. Remembered against the exact file contents,
// so writing a new token.json (setup-token.ts) turns email back on by itself.
let refusedOAuth: { raw: string; reason: string } | null = null;
const REFUSED_OAUTH_ERRORS = new Set(["invalid_grant", "invalid_client", "unauthorized_client"]);

class OAuthRefused extends Error {}

// Re-read on every call: this is what lets email turn itself back on when valid
// files appear. The reasons name files and fields, never values.
async function loadEmailConfig(): Promise<EmailConfig> {
  if (!TO_EMAIL) return { ok: false, reason: "DIGEST_TO not set" };
  let credsRaw: string;
  try {
    credsRaw = await Deno.readTextFile(CREDENTIALS_URL);
  } catch {
    return { ok: false, reason: "credentials.json missing" };
  }
  if (!credsRaw.trim()) return { ok: false, reason: "credentials.json is empty" };
  let client: OAuthClient | undefined;
  try {
    const j = JSON.parse(credsRaw);
    client = j?.installed ?? j?.web;
  } catch {
    return { ok: false, reason: "credentials.json is not valid JSON" };
  }
  if (!client?.client_id || !client?.client_secret) {
    return { ok: false, reason: "credentials.json has no client_id/client_secret" };
  }
  let tokenRaw: string;
  try {
    tokenRaw = await Deno.readTextFile(TOKEN_URL);
  } catch {
    return { ok: false, reason: "token.json missing" };
  }
  if (!tokenRaw.trim()) return { ok: false, reason: "token.json is empty" };
  let token: TokenData;
  try {
    token = JSON.parse(tokenRaw);
  } catch {
    return { ok: false, reason: "token.json is not valid JSON" };
  }
  if (!token?.refresh_token) return { ok: false, reason: "token.json has no refresh_token" };
  addSecret(client.client_secret);
  addSecret(token.refresh_token);
  addSecret(token.access_token);
  const raw = JSON.stringify([credsRaw, tokenRaw]);
  if (refusedOAuth) {
    if (refusedOAuth.raw === raw) return { ok: false, reason: refusedOAuth.reason };
    refusedOAuth = null; // the files changed: try them
  }
  return { ok: true, client, token, raw };
}

let emailEnabledLogged: boolean | null = null;

// Logs ONLY on a change of state, so a long-disabled email is one line, not one
// per alert.
function noteEmailState(cfg: EmailConfig) {
  if (cfg.ok && emailEnabledLogged !== true) {
    console.log("email enabled: OAuth credentials present");
    emailEnabledLogged = true;
  } else if (!cfg.ok && emailEnabledLogged !== false) {
    console.log(
      `email disabled: OAuth not configured (${cfg.reason}). Alerts still go to the other channels; ` +
        "email turns back on by itself when valid credentials.json + token.json are in place.",
    );
    emailEnabledLogged = false;
  }
}

async function refreshAccessToken(client: OAuthClient, token: TokenData): Promise<TokenData> {
  const res = await fetch(GOOGLE_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: client.client_id,
      client_secret: client.client_secret,
      refresh_token: token.refresh_token,
      grant_type: "refresh_token",
    }),
    signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
  });
  const data = await res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  if (data.error) {
    const msg = `Token refresh failed: ${data.error_description || data.error}`;
    if (REFUSED_OAUTH_ERRORS.has(String(data.error))) throw new OAuthRefused(msg);
    throw new Error(msg);
  }
  addSecret(data.access_token);
  const updated: TokenData = {
    access_token: data.access_token,
    refresh_token: token.refresh_token,
    token_type: data.token_type,
    expiry_date: Date.now() + data.expires_in * 1000,
  };
  await Deno.writeTextFile(TOKEN_URL, JSON.stringify(updated, null, 2));
  return updated;
}

async function getAccessToken(cfg: { client: OAuthClient; token: TokenData }): Promise<string> {
  const t = cfg.token;
  if (t.access_token && t.expiry_date && Date.now() < t.expiry_date - 60_000) return t.access_token;
  return (await refreshAccessToken(cfg.client, t)).access_token;
}

// --- Gmail send ----------------------------------------------------------------

function base64UrlEncode(text: string): string {
  const utf8 = unescape(encodeURIComponent(text));
  return btoa(utf8).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sendEmail(accessToken: string, subject: string, html: string): Promise<void> {
  const encodedSubject =
    `=?UTF-8?B?${base64UrlEncode(subject).replace(/-/g, "+").replace(/_/g, "/")}?=`;
  const raw = [
    `From: ${FROM_EMAIL}`,
    `To: ${TO_EMAIL}`,
    `Subject: ${encodedSubject}`,
    `MIME-Version: 1.0`,
    `Content-Type: text/html; charset=utf-8`,
    `Content-Transfer-Encoding: 8bit`,
    "",
    html,
  ].join("\r\n");

  const res = await fetch(GMAIL_SEND_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ raw: base64UrlEncode(raw) }),
    signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
  });

  if (!res.ok) {
    const err = await res.text().catch(() => "");
    throw new Error(`Gmail send failed: ${res.status} ${err.slice(0, 200)}`);
  }
}

// --- Channels ------------------------------------------------------------------

type ChannelName = "telegram" | "mattermost" | "email";

interface OutboundMessage {
  text: string; // the minimal line: Telegram + Mattermost
  subject: string; // email
  html: string; // email (full detail)
}

interface ChannelStatus {
  configured: boolean;
  reason?: string;
}

interface Channel {
  name: ChannelName;
  status(): Promise<ChannelStatus>;
  send(m: OutboundMessage): Promise<void>;
}

const telegram: Channel = {
  name: "telegram",
  status() {
    if (!TG_TOKEN && !TG_CHAT) return Promise.resolve({ configured: false, reason: "not set" });
    if (!TG_TOKEN) return Promise.resolve({ configured: false, reason: "PORTAL_ALERT_TELEGRAM_BOT_TOKEN not set" });
    if (!TG_CHAT) return Promise.resolve({ configured: false, reason: "PORTAL_ALERT_TELEGRAM_CHAT_ID not set" });
    return Promise.resolve({ configured: true });
  },
  async send(m) {
    const res = await fetch(`${TG_API}/bot${TG_TOKEN}/sendMessage`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: TG_CHAT, text: m.text, disable_web_page_preview: true }),
      signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
    });
    const body = await res.json().catch(() => null);
    if (!res.ok || !body?.ok) {
      throw new Error(`Telegram HTTP ${res.status}${body?.description ? ` ${body.description}` : ""}`);
    }
  },
};

const mattermost: Channel = {
  name: "mattermost",
  status() {
    const missing = [
      ["PORTAL_ALERT_MM_URL", MM_URL],
      ["PORTAL_ALERT_MM_TOKEN", MM_TOKEN],
      ["PORTAL_ALERT_MM_CHANNEL_ID", MM_CHANNEL],
    ].filter(([, v]) => !v).map(([k]) => k);
    if (missing.length === 3) return Promise.resolve({ configured: false, reason: "not set" });
    if (missing.length > 0) return Promise.resolve({ configured: false, reason: `${missing.join(", ")} not set` });
    return Promise.resolve({ configured: true });
  },
  async send(m) {
    const message = MM_MENTION ? `${MM_MENTION} ${m.text}` : m.text;
    const res = await fetch(`${MM_URL}/api/v4/posts`, {
      method: "POST",
      headers: { Authorization: `Bearer ${MM_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ channel_id: MM_CHANNEL, message }),
      signal: AbortSignal.timeout(SEND_TIMEOUT_MS),
    });
    if (res.status !== 201 && res.status !== 200) {
      const t = await res.text().catch(() => "");
      throw new Error(`Mattermost HTTP ${res.status} ${t.slice(0, 120)}`);
    }
    await res.body?.cancel().catch(() => {});
  },
};

const email: Channel = {
  name: "email",
  async status() {
    const cfg = await loadEmailConfig();
    noteEmailState(cfg);
    return cfg.ok ? { configured: true } : { configured: false, reason: `OAuth not configured (${cfg.reason})` };
  },
  async send(m) {
    const cfg = await loadEmailConfig();
    if (!cfg.ok) throw new ChannelNotConfigured(`OAuth not configured (${cfg.reason})`);
    let accessToken: string;
    try {
      accessToken = await getAccessToken(cfg);
    } catch (err) {
      if (!(err instanceof OAuthRefused)) throw err;
      const reason = `Google refused the stored OAuth token: ${errText(err)}; re-authorize with setup-token.ts`;
      refusedOAuth = { raw: cfg.raw, reason };
      noteEmailState({ ok: false, reason });
      throw new ChannelNotConfigured(`OAuth not configured (${reason})`);
    }
    await sendEmail(accessToken, m.subject, m.html);
  },
};

const CHANNELS: Channel[] = [telegram, mattermost, email];

// --- Delivery state (read by the HOST watchdog) ------------------------------

interface ChannelState {
  configured: boolean;
  disabled_reason: string | null;
  consecutive_failures: number;
  last_ok_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
}

interface DeliveryState {
  schema: 1;
  updated_at: string;
  // Set when an alert reached NO channel; cleared by the next one that reaches any.
  undelivered_since: string | null;
  undelivered_count: number;
  last_undelivered_event: string | null;
  last_delivered_at: string | null;
  last_attempt: {
    at: string;
    event: string;
    delivered: string[];
    failed: string[];
    skipped: string[];
  } | null;
  channels: Record<string, ChannelState>;
}

function emptyState(): DeliveryState {
  return {
    schema: 1,
    updated_at: new Date().toISOString(),
    undelivered_since: null,
    undelivered_count: 0,
    last_undelivered_event: null,
    last_delivered_at: null,
    last_attempt: null,
    channels: {},
  };
}

function channelState(s: DeliveryState, name: string): ChannelState {
  s.channels[name] ??= {
    configured: false,
    disabled_reason: null,
    consecutive_failures: 0,
    last_ok_at: null,
    last_error_at: null,
    last_error: null,
  };
  return s.channels[name];
}

async function readState(): Promise<DeliveryState> {
  try {
    const s = JSON.parse(await Deno.readTextFile(STATE_FILE));
    if (s && s.schema === 1 && typeof s.channels === "object") return s as DeliveryState;
  } catch { /* missing or torn: start fresh */ }
  return emptyState();
}

// Read-modify-write, serialized in this process. Re-reading first keeps a
// concurrent --selftest (a second process via docker exec) from being erased.
let stateChain: Promise<void> = Promise.resolve();
let lastWriteError: string | null = null;

function updateState(mutate: (s: DeliveryState) => void): Promise<void> {
  stateChain = stateChain.then(async () => {
    const s = await readState();
    mutate(s);
    s.updated_at = new Date().toISOString();
    try {
      const tmp = `${STATE_FILE}.tmp`;
      await Deno.writeTextFile(tmp, JSON.stringify(s, null, 2) + "\n");
      await Deno.rename(tmp, STATE_FILE);
      lastWriteError = null;
    } catch (err) {
      const msg = errText(err);
      if (msg !== lastWriteError) console.error(`delivery state not written to ${STATE_FILE}: ${msg}`);
      lastWriteError = msg;
    }
  });
  return stateChain;
}

// --- Dispatch: every enabled channel, success if any delivered ---------------

interface DispatchResult {
  delivered: ChannelName[];
  failed: { channel: ChannelName; error: string }[];
  skipped: { channel: ChannelName; reason: string }[];
}

async function dispatch(label: string, m: OutboundMessage, only?: ChannelName[]): Promise<DispatchResult> {
  const result: DispatchResult = { delivered: [], failed: [], skipped: [] };
  const statuses = new Map<ChannelName, ChannelStatus>();
  const chans = CHANNELS.filter((c) => !only || only.includes(c.name));
  await Promise.all(chans.map(async (c) => {
    const st = await c.status();
    statuses.set(c.name, st);
    if (!st.configured) {
      result.skipped.push({ channel: c.name, reason: st.reason ?? "not configured" });
      return;
    }
    try {
      await c.send(m);
      result.delivered.push(c.name);
    } catch (err) {
      if (err instanceof ChannelNotConfigured) {
        const reason = errText(err);
        statuses.set(c.name, { configured: false, reason });
        result.skipped.push({ channel: c.name, reason });
      } else {
        result.failed.push({ channel: c.name, error: errText(err) });
      }
    }
  }));

  const parts = chans.map((c) => {
    if (result.delivered.includes(c.name)) return `${c.name}=delivered`;
    const f = result.failed.find((x) => x.channel === c.name);
    if (f) return `${c.name}=FAILED(${f.error})`;
    return `${c.name}=off(${statuses.get(c.name)?.reason ?? "not configured"})`;
  });
  const line = `${label}: ${parts.join(" ")}`;
  if (result.delivered.length > 0) console.log(line);
  else console.error(`${line} -> NO CHANNEL DELIVERED`);

  const now = new Date().toISOString();
  await updateState((s) => {
    for (const c of chans) {
      const cs = channelState(s, c.name);
      const st = statuses.get(c.name)!;
      cs.configured = st.configured;
      cs.disabled_reason = st.configured ? null : (st.reason ?? "not configured");
      if (result.delivered.includes(c.name)) {
        cs.consecutive_failures = 0;
        cs.last_ok_at = now;
      } else {
        const f = result.failed.find((x) => x.channel === c.name);
        if (f) {
          cs.consecutive_failures += 1;
          cs.last_error_at = now;
          cs.last_error = f.error;
        } else {
          // Not configured: not a failure, and a stale count must not page.
          cs.consecutive_failures = 0;
        }
      }
    }
    // Only full alert dispatches decide "undelivered" (the email-only digest
    // does not: its channel outcome above is enough).
    if (!only) {
      s.last_attempt = {
        at: now,
        event: label,
        delivered: result.delivered,
        failed: result.failed.map((f) => f.channel),
        skipped: result.skipped.map((x) => x.channel),
      };
      if (result.delivered.length > 0) {
        s.last_delivered_at = now;
        s.undelivered_since = null;
        s.undelivered_count = 0;
        s.last_undelivered_event = null;
      } else {
        s.undelivered_since ??= now;
        s.undelivered_count += 1;
        s.last_undelivered_event = label;
      }
    }
  });
  return result;
}

// --- HTML helpers --------------------------------------------------------------

function escHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

const SEVERITY_COLOR: Record<string, string> = {
  critical: "#b00020",
  high: "#d93025",
  medium: "#f9a825",
  low: "#1967d2",
};

function severityBadge(sev: string): string {
  const color = SEVERITY_COLOR[sev] ?? "#5f6368";
  return `<span style="display:inline-block;background:${color};color:#fff;padding:2px 10px;border-radius:4px;font-size:12px;font-weight:600;letter-spacing:0.04em;text-transform:uppercase;">${escHtml(sev)}</span>`;
}

function footer(): string {
  const domain = PUBLIC_DOMAIN || "(PUBLIC_DOMAIN not set)";
  return `<div style="color:#999;font-size:12px;margin-top:32px;padding-top:12px;border-top:1px solid #eee;">
    Portal alerter · ${escHtml(domain)} · ${escHtml(new Date().toISOString())}
  </div>`;
}

// --- /alert payload --------------------------------------------------------------

interface AlertPayload {
  severity: string;
  event: string;
  source_ip?: string | null;
  username?: string | null;
  timestamp_utc?: string;
  log_line?: string;
}

function renderAlertHtml(p: AlertPayload): string {
  const rows: string[] = [];
  const add = (k: string, v: string) =>
    rows.push(
      `<tr><td style="padding:4px 12px 4px 0;color:#666;white-space:nowrap;">${escHtml(k)}</td><td style="padding:4px 0;">${v}</td></tr>`,
    );
  add("Severity", severityBadge(p.severity));
  add("Event", escHtml(p.event));
  if (p.source_ip) add("Source IP", `<code>${escHtml(p.source_ip)}</code>`);
  if (p.username) add("Username", `<code>${escHtml(p.username)}</code>`);
  if (p.timestamp_utc) add("Timestamp (UTC)", escHtml(p.timestamp_utc));
  if (p.log_line) {
    add(
      "Log line",
      `<pre style="margin:0;padding:8px;background:#f5f5f5;border-radius:4px;font-size:12px;white-space:pre-wrap;word-break:break-all;">${escHtml(p.log_line.slice(0, 800))}</pre>`,
    );
  }
  return `<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:720px;margin:0 auto;padding:16px;color:#333;line-height:1.5;">
<h2 style="margin:0 0 16px;">Portal alert</h2>
<table style="border-collapse:collapse;font-size:14px;">${rows.join("")}</table>
${footer()}
</body></html>`;
}

function alertSubject(p: AlertPayload): string {
  const ip = p.source_ip ? ` ${p.source_ip}` : "";
  return `[${p.severity.toUpperCase()}] ${p.event}${ip}`;
}

// The ONLY text Telegram/Mattermost get: severity, event, host label, time.
// The event name is caller-supplied, so it is reduced to a short identifier.
const SEVERITIES = new Set(["critical", "high", "medium", "low", "info"]);

function safeEvent(ev: string): string {
  return String(ev).replace(/[^A-Za-z0-9._:\/-]/g, "_").slice(0, 64) || "unknown";
}

function safeTime(ts?: string): string {
  if (ts && /^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9:.]+Z?$/.test(ts)) return ts;
  return new Date().toISOString().replace(/\.\d+Z$/, "Z");
}

function minimalText(p: AlertPayload): string {
  const sev = String(p.severity).toLowerCase();
  const s = SEVERITIES.has(sev) ? sev.toUpperCase() : "ALERT";
  return `Portal alert [${s}] ${safeEvent(p.event)} on ${HOST_LABEL} at ${safeTime(p.timestamp_utc)}`;
}

// --- Rate limiter for /alert -----------------------------------------------------

const recentAlerts: { sentAt: number; payload: AlertPayload }[] = [];
const coalescedQueue: AlertPayload[] = [];
let coalesceTimer: ReturnType<typeof setTimeout> | null = null;

function pruneOldAlerts() {
  const cutoff = Date.now() - 60_000;
  while (recentAlerts.length > 0 && recentAlerts[0].sentAt < cutoff) {
    recentAlerts.shift();
  }
}

async function flushCoalesced(): Promise<void> {
  if (coalescedQueue.length === 0) return;
  const items = coalescedQueue.splice(0, coalescedQueue.length);
  const html = `<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:720px;margin:0 auto;padding:16px;color:#333;line-height:1.5;">
<h2 style="margin:0 0 16px;">Coalesced alerts (${items.length})</h2>
<p style="color:#666;font-size:14px;margin:0 0 16px;">Rate limit of ${RATE_LIMIT_PER_MIN}/min exceeded in the last minute. The following alerts were combined into this single email.</p>
<table style="border-collapse:collapse;font-size:13px;width:100%;">
  <thead><tr style="border-bottom:1px solid #ddd;"><th style="text-align:left;padding:6px 8px;">Severity</th><th style="text-align:left;padding:6px 8px;">Event</th><th style="text-align:left;padding:6px 8px;">Source IP</th><th style="text-align:left;padding:6px 8px;">Time UTC</th></tr></thead>
  <tbody>
    ${items.map((i) => `<tr style="border-bottom:1px solid #f0f0f0;"><td style="padding:6px 8px;">${severityBadge(i.severity)}</td><td style="padding:6px 8px;">${escHtml(i.event)}</td><td style="padding:6px 8px;"><code>${escHtml(i.source_ip ?? "")}</code></td><td style="padding:6px 8px;">${escHtml(i.timestamp_utc ?? "")}</td></tr>`).join("")}
  </tbody>
</table>
${footer()}
</body></html>`;
  const events = [...new Set(items.map((i) => safeEvent(i.event)))].slice(0, 5).join(", ");
  const text = `Portal alerts: ${items.length} coalesced (rate limit) on ${HOST_LABEL} at ${safeTime()}: ${events}`;
  try {
    await dispatch(`coalesced(${items.length})`, { text, subject: `[COALESCED] ${items.length} alerts`, html });
  } catch (err) {
    console.error(`Coalesced flush failed: ${errText(err)}`);
  }
}

async function handleAlert(
  payload: AlertPayload,
): Promise<{ delivered: boolean; coalesced: boolean; channels: Record<string, string> }> {
  pruneOldAlerts();
  if (recentAlerts.length >= RATE_LIMIT_PER_MIN) {
    coalescedQueue.push(payload);
    if (coalesceTimer === null) {
      coalesceTimer = setTimeout(async () => {
        coalesceTimer = null;
        await flushCoalesced();
      }, 60_000);
    }
    return { delivered: false, coalesced: true, channels: {} };
  }
  // Counted as an attempt whatever the outcome: the limit protects the
  // channels from a log flood whether or not the sends succeed.
  recentAlerts.push({ sentAt: Date.now(), payload });
  const r = await dispatch(safeEvent(payload.event), {
    text: minimalText(payload),
    subject: alertSubject(payload),
    html: renderAlertHtml(payload),
  });
  const channels: Record<string, string> = {};
  for (const c of r.delivered) channels[c] = "delivered";
  for (const f of r.failed) channels[f.channel] = "failed";
  for (const x of r.skipped) channels[x.channel] = "off";
  return { delivered: r.delivered.length > 0, coalesced: false, channels };
}

// ─── /run digest: log scanning ───────────────────────────────────────────────

interface CaddyAccessEntry {
  ts?: number;
  status?: number;
  request?: {
    remote_ip?: string;
    headers?: Record<string, string[]>;
    uri?: string;
    method?: string;
  };
  duration?: number;
}

interface AutheliaEntry {
  time?: string;
  level?: string;
  msg?: string;
  remote_ip?: string;
  username?: string;
}

async function readJsonLines<T>(path: string, sinceMs: number): Promise<T[]> {
  let raw = "";
  try {
    raw = await Deno.readTextFile(path);
  } catch {
    return [];
  }
  const out: T[] = [];
  for (const line of raw.split("\n")) {
    if (!line.trim()) continue;
    let obj: T & { ts?: number; time?: string };
    try {
      obj = JSON.parse(line);
    } catch {
      continue;
    }
    const ts = (() => {
      if (typeof obj.ts === "number") return obj.ts * 1000;
      if (obj.time) {
        const t = Date.parse(obj.time);
        return Number.isFinite(t) ? t : 0;
      }
      return 0;
    })();
    if (ts === 0 || ts >= sinceMs) out.push(obj);
  }
  return out;
}

function getClientIp(e: CaddyAccessEntry): string {
  const xff = e.request?.headers?.["X-Forwarded-For"]?.[0];
  if (xff) return xff.split(",")[0].trim();
  return e.request?.remote_ip ?? "";
}

function getCountry(e: CaddyAccessEntry): string {
  return e.request?.headers?.["Cf-Ipcountry"]?.[0] ?? "";
}

interface DigestStats {
  totalRequests: number;
  byStatus: Record<string, number>;
  byRoute: Record<string, number>;
  fourOhOneByRoute: Record<string, number>;
  topSourceIps: Array<{ ip: string; count: number; country: string }>;
  top401Ips: Array<{ ip: string; count: number }>;
  authSuccess: number;
  authFail: number;
  regulationBans: number;
  webauthnChanges: number;
  totpChanges: number;
  configReloads: number;
  newIps: string[];
  failedLoginsByIp: Array<{ ip: string; count: number }>;
}

function routeOf(uri: string): string {
  if (uri.startsWith("/openwebui")) return "/openwebui/*";
  if (uri.startsWith("/api/notebook")) return "/api/notebook/*";
  if (uri.startsWith("/notebook")) return "/notebook/*";
  if (uri.startsWith("/api/")) return "/api/* (authelia)";
  if (uri === "/" || uri === "/index.html") return "/ (hub)";
  return "other";
}

function buildDigestStats(
  caddy: CaddyAccessEntry[],
  authelia: AutheliaEntry[],
  knownIps: Set<string>,
): DigestStats {
  const byStatus: Record<string, number> = {};
  const byRoute: Record<string, number> = {};
  const fourOhOneByRoute: Record<string, number> = {};
  const ipCounts = new Map<string, { count: number; country: string }>();
  const four01Ips = new Map<string, number>();

  for (const e of caddy) {
    const ip = getClientIp(e);
    const country = getCountry(e);
    const status = String(e.status ?? 0);
    const route = routeOf(e.request?.uri ?? "");
    byStatus[status] = (byStatus[status] ?? 0) + 1;
    byRoute[route] = (byRoute[route] ?? 0) + 1;
    if (e.status === 401) {
      fourOhOneByRoute[route] = (fourOhOneByRoute[route] ?? 0) + 1;
      if (ip) four01Ips.set(ip, (four01Ips.get(ip) ?? 0) + 1);
    }
    if (ip) {
      const prev = ipCounts.get(ip) ?? { count: 0, country };
      prev.count += 1;
      if (!prev.country && country) prev.country = country;
      ipCounts.set(ip, prev);
    }
  }

  const topSourceIps = [...ipCounts.entries()]
    .map(([ip, v]) => ({ ip, count: v.count, country: v.country }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  const top401Ips = [...four01Ips.entries()]
    .map(([ip, count]) => ({ ip, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  let authSuccess = 0;
  let authFail = 0;
  let regulationBans = 0;
  let webauthnChanges = 0;
  let totpChanges = 0;
  let configReloads = 0;
  const successIps = new Set<string>();
  const failedLogins = new Map<string, number>();

  for (const e of authelia) {
    const msg = (e.msg ?? "").toLowerCase();
    if (msg.includes("successful 1fa") || msg.includes("successful 2fa")) {
      authSuccess += 1;
      if (e.remote_ip) successIps.add(e.remote_ip);
    } else if (msg.includes("unsuccessful 1fa") || msg.includes("unsuccessful 2fa")) {
      authFail += 1;
      if (e.remote_ip) {
        failedLogins.set(e.remote_ip, (failedLogins.get(e.remote_ip) ?? 0) + 1);
      }
    } else if (msg.includes("banned")) {
      regulationBans += 1;
    } else if (msg.includes("webauthn")) {
      webauthnChanges += 1;
    } else if (msg.includes("totp")) {
      totpChanges += 1;
    } else if (msg.includes("config_file_loaded") || msg.includes("configuration reloaded")) {
      configReloads += 1;
    }
  }

  const newIps = [...successIps].filter((ip) => !knownIps.has(ip));
  const failedLoginsByIp = [...failedLogins.entries()]
    .map(([ip, count]) => ({ ip, count }))
    .sort((a, b) => b.count - a.count)
    .slice(0, 10);

  return {
    totalRequests: caddy.length,
    byStatus,
    byRoute,
    fourOhOneByRoute,
    topSourceIps,
    top401Ips,
    authSuccess,
    authFail,
    regulationBans,
    webauthnChanges,
    totpChanges,
    configReloads,
    newIps,
    failedLoginsByIp,
  };
}

function renderDigestHtml(s: DigestStats, windowHours: number): string {
  const section = (title: string, body: string) =>
    `<h2 style="margin:24px 0 8px;font-size:18px;border-bottom:1px solid #ddd;padding-bottom:4px;">${escHtml(title)}</h2>${body}`;

  const traffic = (() => {
    const rows = Object.entries(s.byStatus)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(
        ([status, count]) =>
          `<tr><td style="padding:2px 12px 2px 0;"><code>${escHtml(status)}</code></td><td>${count}</td></tr>`,
      )
      .join("");
    return `<p style="margin:0 0 8px;"><strong>${s.totalRequests}</strong> total requests in window.</p><table style="border-collapse:collapse;font-size:13px;">${rows}</table>`;
  })();

  const routes = (() => {
    const rows = Object.entries(s.byRoute)
      .sort(([, a], [, b]) => b - a)
      .map(([route, count]) => {
        const fourOhOne = s.fourOhOneByRoute[route] ?? 0;
        return `<tr><td style="padding:2px 12px 2px 0;"><code>${escHtml(route)}</code></td><td>${count}</td><td style="color:#d93025;">${fourOhOne > 0 ? `${fourOhOne} × 401` : ""}</td></tr>`;
      })
      .join("");
    return `<table style="border-collapse:collapse;font-size:13px;">${rows}</table>`;
  })();

  const topIps = s.topSourceIps.length === 0
    ? "<p style='color:#888;font-size:13px;margin:0;'>No requests in window.</p>"
    : `<table style="border-collapse:collapse;font-size:13px;width:100%;">
        <thead><tr style="border-bottom:1px solid #ddd;"><th style="text-align:left;padding:4px 8px;">IP</th><th style="text-align:left;padding:4px 8px;">Country</th><th style="text-align:right;padding:4px 8px;">Requests</th></tr></thead>
        <tbody>${s.topSourceIps.map((r) => `<tr><td style="padding:4px 8px;"><code>${escHtml(r.ip)}</code></td><td style="padding:4px 8px;">${escHtml(r.country || "—")}</td><td style="padding:4px 8px;text-align:right;">${r.count}</td></tr>`).join("")}</tbody>
      </table>`;

  const authSummary = `
    <ul style="margin:0;padding-left:20px;font-size:14px;">
      <li>Successful logins: <strong>${s.authSuccess}</strong></li>
      <li>Failed logins: <strong>${s.authFail}</strong></li>
      <li>Regulation bans applied: <strong>${s.regulationBans}</strong></li>
      <li>WebAuthn credential changes: <strong>${s.webauthnChanges}</strong></li>
      <li>TOTP credential changes: <strong>${s.totpChanges}</strong></li>
      <li>Authelia config reloads: <strong>${s.configReloads}</strong></li>
      <li>New source IPs with successful login: <strong>${s.newIps.length}</strong>${s.newIps.length > 0 ? ` — ${s.newIps.map((ip) => `<code>${escHtml(ip)}</code>`).join(", ")}` : ""}</li>
    </ul>`;

  const threats = (() => {
    const items: string[] = [];
    if (s.top401Ips.length > 0) {
      items.push(
        `<p style="margin:0 0 4px;font-size:14px;"><strong>Top 401 source IPs:</strong></p><ul style="margin:0 0 12px;padding-left:20px;font-size:13px;">${s.top401Ips.map((r) => `<li><code>${escHtml(r.ip)}</code> — ${r.count} × 401</li>`).join("")}</ul>`,
      );
    }
    if (s.failedLoginsByIp.length > 0) {
      items.push(
        `<p style="margin:0 0 4px;font-size:14px;"><strong>Top failed-login source IPs (Authelia):</strong></p><ul style="margin:0 0 12px;padding-left:20px;font-size:13px;">${s.failedLoginsByIp.map((r) => `<li><code>${escHtml(r.ip)}</code> — ${r.count} fails</li>`).join("")}</ul>`,
      );
    }
    if (items.length === 0) {
      items.push(`<p style="color:#888;font-size:13px;margin:0;">No notable patterns in window.</p>`);
    }
    return items.join("");
  })();

  return `<!DOCTYPE html>
<html><body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:720px;margin:0 auto;padding:16px;color:#333;line-height:1.5;">
<div style="background:#f5f5f5;padding:14px 18px;border-radius:6px;margin-bottom:16px;">
  <div style="font-size:13px;color:#666;margin-bottom:4px;">Window: last ${windowHours}h, ending ${escHtml(new Date().toISOString())}</div>
  <div style="font-size:14px;"><strong>${s.totalRequests}</strong> requests · <strong>${s.authSuccess}</strong> logins · <strong>${s.authFail}</strong> failed · <strong>${s.regulationBans}</strong> bans · <strong>${s.newIps.length}</strong> new IPs</div>
</div>
${section("Traffic", traffic)}
${section("Routes", routes)}
${section("Top source IPs", topIps)}
${section("Authentication summary", authSummary)}
${section("Threats / anomalies", threats)}
${footer()}
</body></html>`;
}

function renderDigestMarkdown(s: DigestStats, windowHours: number): string {
  const lines: string[] = [];
  lines.push(`# Portal digest — last ${windowHours}h`);
  lines.push("");
  lines.push(`Generated: ${new Date().toISOString()}`);
  lines.push("");
  lines.push(`- Total requests: **${s.totalRequests}**`);
  lines.push(`- Logins: ${s.authSuccess} successful, ${s.authFail} failed`);
  lines.push(`- Regulation bans: ${s.regulationBans}`);
  lines.push(`- WebAuthn changes: ${s.webauthnChanges}; TOTP changes: ${s.totpChanges}`);
  lines.push(`- Config reloads: ${s.configReloads}`);
  lines.push(`- New IPs: ${s.newIps.length}${s.newIps.length > 0 ? ` (${s.newIps.join(", ")})` : ""}`);
  lines.push("");
  lines.push("## Status breakdown");
  for (const [k, v] of Object.entries(s.byStatus).sort()) lines.push(`- ${k}: ${v}`);
  lines.push("");
  lines.push("## Routes");
  for (const [k, v] of Object.entries(s.byRoute).sort(([, a], [, b]) => b - a)) {
    const four = s.fourOhOneByRoute[k] ?? 0;
    lines.push(`- ${k}: ${v}${four > 0 ? ` (${four} × 401)` : ""}`);
  }
  lines.push("");
  lines.push("## Top source IPs");
  for (const r of s.topSourceIps) lines.push(`- ${r.ip} ${r.country ? `[${r.country}]` : ""} — ${r.count}`);
  lines.push("");
  lines.push("## Top 401 source IPs");
  for (const r of s.top401Ips) lines.push(`- ${r.ip} — ${r.count}`);
  lines.push("");
  return lines.join("\n");
}

async function readKnownIps(): Promise<Set<string>> {
  try {
    const raw = await Deno.readTextFile("/data/known-ips.txt");
    return new Set(raw.split("\n").map((s) => s.trim()).filter(Boolean));
  } catch {
    return new Set();
  }
}

async function writeAuditTrail(markdown: string, subject: string): Promise<void> {
  try {
    await Deno.mkdir(REPORT_DIR, { recursive: true });
    await Deno.writeTextFile(`${REPORT_DIR}/digest-latest.md`, `# ${subject}\n\n${markdown}`);
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    await Deno.writeTextFile(`${REPORT_DIR}/digest-${ts}.md`, `# ${subject}\n\n${markdown}`);
  } catch (err) {
    console.warn(`Could not write report to ${REPORT_DIR}: ${err}`);
  }
}

async function runDigest(
  windowHoursOverride?: number,
): Promise<{ stats: DigestStats; email: string }> {
  const windowHours = windowHoursOverride ?? WINDOW_HOURS;
  const sinceMs = Date.now() - windowHours * 3600_000;
  const [caddy, authelia, knownIps] = await Promise.all([
    readJsonLines<CaddyAccessEntry>(CADDY_LOG, sinceMs),
    readJsonLines<AutheliaEntry>(AUTHELIA_LOG, sinceMs),
    readKnownIps(),
  ]);
  const stats = buildDigestStats(caddy, authelia, knownIps);
  const subject = `Portal digest — ${new Date().toISOString().slice(0, 10)} (${windowHours}h)`;
  const html = renderDigestHtml(stats, windowHours);
  const markdown = renderDigestMarkdown(stats, windowHours);
  await writeAuditTrail(markdown, subject);
  // EMAIL ONLY (the digest lists source IPs). Email not configured = the
  // markdown copy above is the digest, and that is not an error; email
  // configured but failing IS one (and counts toward the channel's failures).
  const r = await dispatch("digest", { text: "", subject, html }, ["email"]);
  if (r.delivered.length > 0) return { stats, email: "delivered" };
  if (r.failed.length > 0) throw new Error(`digest email failed: ${r.failed[0].error}`);
  return { stats, email: `off (${r.skipped[0]?.reason ?? "not configured"})` };
}

// --- --selftest mode -------------------------------------------------------------

if (Deno.args.includes("--selftest")) {
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  const html = `<!DOCTYPE html><html><body style="font-family:sans-serif;padding:16px;">
<h2>Portal alerter self-test</h2>
<p>Every enabled channel was sent this test; this copy is the email one.</p>
<p>Generated: ${escHtml(now)}</p>
</body></html>`;
  const r = await dispatch("selftest", {
    text: `Portal alerter self-test on ${HOST_LABEL} at ${now}`,
    subject: "Portal alerter self-test",
    html,
  });
  for (const c of r.delivered) console.log(`  ${c}: delivered`);
  for (const f of r.failed) console.log(`  ${f.channel}: FAILED ${f.error}`);
  for (const x of r.skipped) console.log(`  ${x.channel}: off (${x.reason})`);
  if (r.delivered.length > 0) {
    console.log(`Self-test delivered via: ${r.delivered.join(", ")}`);
    Deno.exit(0);
  }
  console.error("Self-test FAILED: no channel delivered.");
  Deno.exit(1);
}

// --- HTTP server -------------------------------------------------------------------

let lastAlertAt: string | null = null;
let lastDigestAt: string | null = null;
let lastError: string | null = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function channelSummary(): Promise<Record<string, unknown>> {
  const state = await readState();
  const out: Record<string, unknown> = {};
  for (const c of CHANNELS) {
    const st = await c.status();
    out[c.name] = {
      configured: st.configured,
      reason: st.reason ?? null,
      consecutive_failures: state.channels[c.name]?.consecutive_failures ?? 0,
    };
  }
  return out;
}

Deno.serve({ port: PORT, hostname: "0.0.0.0", onListen: () => {} }, async (req) => {
  const url = new URL(req.url);

  if (req.method === "GET" && url.pathname === "/health") {
    const state = await readState();
    return json({
      service: "portal-alerter",
      ready: true,
      last_alert_at: lastAlertAt,
      last_digest_at: lastDigestAt,
      last_error: lastError,
      rate_limit_per_min: RATE_LIMIT_PER_MIN,
      window_hours: WINDOW_HOURS,
      coalesce_queue_depth: coalescedQueue.length,
      undelivered_since: state.undelivered_since,
      channels: await channelSummary(),
    });
  }

  if (req.method === "POST" && url.pathname === "/alert") {
    let payload: AlertPayload;
    // Read body as text first so we can log a prefix on parse failure --
    // makes it possible to identify which sender (script, watcher, etc.)
    // is POSTing malformed JSON. Without this, the 400 is silent about
    // who's at fault.
    const raw = await req.text();
    try {
      payload = JSON.parse(raw);
    } catch (e) {
      const ip = req.headers.get("x-forwarded-for") ?? "(local)";
      const ua = req.headers.get("user-agent") ?? "(no UA)";
      const prefix = raw.slice(0, 200).replace(/\n/g, "\\n").replace(/\r/g, "\\r");
      console.error(
        `/alert 400 invalid JSON from ${ip} ua="${ua}" body[0..200]="${prefix}" err=${e instanceof Error ? e.message : e}`,
      );
      return json({ error: "invalid JSON" }, 400);
    }
    if (!payload || !payload.severity || !payload.event) {
      const ip = req.headers.get("x-forwarded-for") ?? "(local)";
      console.error(`/alert 400 missing required fields from ${ip}: severity=${payload?.severity} event=${payload?.event}`);
      return json({ error: "severity and event required" }, 400);
    }
    try {
      const result = await handleAlert(payload);
      lastAlertAt = new Date().toISOString();
      if (!result.delivered && !result.coalesced) {
        lastError = "no channel delivered";
        return json({ ok: false, error: "no channel delivered", ...result }, 500);
      }
      return json({ ok: true, ...result });
    } catch (err) {
      lastError = errText(err);
      console.error(`/alert failed: ${lastError}`);
      return json({ error: lastError }, 500);
    }
  }

  if (req.method === "POST" && url.pathname === "/run") {
    let windowOverride: number | undefined;
    try {
      const body = await req.json().catch(() => ({}));
      if (typeof body.window_hours === "number") windowOverride = body.window_hours;
    } catch { /* empty body is fine */ }
    try {
      const { stats, email: emailOutcome } = await runDigest(windowOverride);
      lastDigestAt = new Date().toISOString();
      return json({ ok: true, email: emailOutcome, stats });
    } catch (err) {
      lastError = errText(err);
      console.error(`/run failed: ${lastError}`);
      return json({ error: lastError }, 500);
    }
  }

  return json({ error: "not found", path: url.pathname }, 404);
});

{
  const parts: string[] = [];
  for (const c of CHANNELS) {
    const st = await c.status();
    parts.push(st.configured ? `${c.name}=on` : `${c.name}=off(${st.reason})`);
  }
  console.log(`portal-alerter listening on :${PORT}; channels: ${parts.join(" ")}`);
  if (!parts.some((p) => p.endsWith("=on"))) {
    console.error("WARNING: no alert channel is configured - every /alert will fail and be recorded as undelivered.");
  }
}
