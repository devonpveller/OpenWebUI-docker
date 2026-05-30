#!/usr/bin/env -S deno run --allow-net --allow-read --allow-write --allow-env

/**
 * Portal Alerter — single Gmail egress point for the internet-exposed portal.
 *
 * Modeled on OB1's send-digest.ts (OB1/recipes/daily-digest/send-digest.ts).
 * Reuses the same OAuth client (open-brain-email) by default; the refresh
 * token is portal-specific (secrets/google/portal-alerter/token.json) so the
 * two senders can be revoked independently.
 *
 * Endpoints:
 *   POST /alert     — instant email for a discrete event from a watcher
 *   POST /run       — scheduled traffic + threats digest (DIGEST_WINDOW_HOURS)
 *   GET  /health    — liveness for healthcheck, portal-status.ps1, killswitch
 *
 * CLI:
 *   --selftest      — send one test email with subject "Portal alerter self-test"
 *                     and exit 0. Used by plan §6.9 step 4.
 *
 * Environment:
 *   DIGEST_TO                  Required. Operator's own Gmail address.
 *   DIGEST_FROM                Required. Must match the consented Google
 *                              account; mismatches produce Gmail errors.
 *   DIGEST_WINDOW_HOURS        Default 24. Window the /run digest covers.
 *   ALERT_RATE_LIMIT_PER_MIN   Default 20. Max /alert emails per rolling
 *                              minute; excess are coalesced.
 *   PUBLIC_DOMAIN              Optional. Appears in email footers.
 *
 * Paths inside the container (matches docker-compose mounts):
 *   /app/credentials.json       — OAuth client (read-only, from secrets/google/open-brain-email)
 *   /app/token.json             — refresh token (writable, from secrets/google/portal-alerter)
 *   /logs/authelia/authelia.log — Authelia JSON log (read-only)
 *   /logs/caddy/caddy-access.log— Caddy JSON access log (read-only)
 *   /reports                    — markdown audit copies (writable)
 *
 * The container's root FS is read-only; only the four paths above are
 * writable per the compose bind mounts.
 */

// ─── Paths ───────────────────────────────────────────────────────────────────

const SCRIPT_DIR = new URL(".", import.meta.url).pathname;
const CREDENTIALS_PATH = `${SCRIPT_DIR}credentials.json`;
const TOKEN_PATH = `${SCRIPT_DIR}token.json`;
const REPORT_DIR = "/reports";
const AUTHELIA_LOG = "/logs/authelia/authelia.log";
const CADDY_LOG = "/logs/caddy/caddy-access.log";

// ─── Configuration ───────────────────────────────────────────────────────────

const TO_EMAIL = Deno.env.get("DIGEST_TO") || "";
const FROM_EMAIL = Deno.env.get("DIGEST_FROM") || TO_EMAIL;
const WINDOW_HOURS = parseInt(Deno.env.get("DIGEST_WINDOW_HOURS") || "24", 10);
const RATE_LIMIT_PER_MIN = parseInt(
  Deno.env.get("ALERT_RATE_LIMIT_PER_MIN") || "20",
  10,
);
const PUBLIC_DOMAIN = Deno.env.get("PUBLIC_DOMAIN") || "";
const PORT = parseInt(Deno.env.get("DIGEST_PORT") || "8080", 10);

if (!TO_EMAIL) {
  console.error("DIGEST_TO is required (your own Gmail address).");
  Deno.exit(1);
}

// ─── OAuth (mirrors OB1 send-digest.ts) ──────────────────────────────────────

interface OAuthCredentials {
  installed: { client_id: string; client_secret: string; redirect_uris: string[] };
}

interface TokenData {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expiry_date: number;
}

async function refreshAccessToken(
  creds: OAuthCredentials,
  token: TokenData,
): Promise<TokenData> {
  const res = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      client_id: creds.installed.client_id,
      client_secret: creds.installed.client_secret,
      refresh_token: token.refresh_token,
      grant_type: "refresh_token",
    }),
  });
  const data = await res.json();
  if (data.error) {
    throw new Error(`Token refresh failed: ${data.error_description || data.error}`);
  }
  const updated: TokenData = {
    access_token: data.access_token,
    refresh_token: token.refresh_token,
    token_type: data.token_type,
    expiry_date: Date.now() + data.expires_in * 1000,
  };
  await Deno.writeTextFile(TOKEN_PATH, JSON.stringify(updated, null, 2));
  return updated;
}

async function getAccessToken(): Promise<string> {
  let creds: OAuthCredentials;
  try {
    creds = JSON.parse(await Deno.readTextFile(CREDENTIALS_PATH));
  } catch {
    throw new Error(
      `No credentials.json at ${CREDENTIALS_PATH}. Mount the OAuth client secret from secrets/google/open-brain-email/.`,
    );
  }

  let token: TokenData;
  try {
    token = JSON.parse(await Deno.readTextFile(TOKEN_PATH));
  } catch {
    throw new Error(
      `No token.json at ${TOKEN_PATH}. Run setup-token.ts on the host once to bootstrap (one-time OAuth consent for gmail.send scope).`,
    );
  }

  if (Date.now() < token.expiry_date - 60_000) return token.access_token;
  return (await refreshAccessToken(creds, token)).access_token;
}

// ─── Gmail send ──────────────────────────────────────────────────────────────

function base64UrlEncode(text: string): string {
  const utf8 = unescape(encodeURIComponent(text));
  return btoa(utf8).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sendEmail(
  accessToken: string,
  subject: string,
  html: string,
): Promise<void> {
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

  const res = await fetch(
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${accessToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ raw: base64UrlEncode(raw) }),
    },
  );

  if (!res.ok) {
    const err = await res.text().catch(() => "");
    throw new Error(`Gmail send failed: ${res.status} ${err}`);
  }
}

// ─── HTML helpers ────────────────────────────────────────────────────────────

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

// ─── /alert payload ──────────────────────────────────────────────────────────

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

// ─── Rate limiter for /alert ─────────────────────────────────────────────────

const recentAlerts: { sentAt: number; payload: AlertPayload }[] = [];
const coalescedQueue: AlertPayload[] = [];
let coalesceTimer: number | null = null;

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
  try {
    const token = await getAccessToken();
    await sendEmail(token, `[COALESCED] ${items.length} alerts`, html);
  } catch (err) {
    console.error(`Coalesced flush failed: ${err instanceof Error ? err.message : err}`);
  }
}

async function handleAlert(payload: AlertPayload): Promise<{ delivered: boolean; coalesced: boolean }> {
  pruneOldAlerts();
  if (recentAlerts.length >= RATE_LIMIT_PER_MIN) {
    coalescedQueue.push(payload);
    if (coalesceTimer === null) {
      coalesceTimer = setTimeout(async () => {
        coalesceTimer = null;
        await flushCoalesced();
      }, 60_000);
    }
    return { delivered: false, coalesced: true };
  }
  const subject = alertSubject(payload);
  const html = renderAlertHtml(payload);
  const token = await getAccessToken();
  await sendEmail(token, subject, html);
  recentAlerts.push({ sentAt: Date.now(), payload });
  return { delivered: true, coalesced: false };
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

async function runDigest(windowHoursOverride?: number): Promise<DigestStats> {
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
  const token = await getAccessToken();
  await sendEmail(token, subject, html);
  return stats;
}

// ─── --selftest mode ─────────────────────────────────────────────────────────

if (Deno.args.includes("--selftest")) {
  try {
    const token = await getAccessToken();
    const html = `<!DOCTYPE html><html><body style="font-family:sans-serif;padding:16px;">
<h2>Portal alerter self-test</h2>
<p>OAuth refresh succeeded; Gmail send succeeded.</p>
<p>From: <code>${escHtml(FROM_EMAIL)}</code><br>To: <code>${escHtml(TO_EMAIL)}</code></p>
<p>Generated: ${escHtml(new Date().toISOString())}</p>
</body></html>`;
    await sendEmail(token, "Portal alerter self-test", html);
    console.log(`Self-test sent to ${TO_EMAIL}.`);
    Deno.exit(0);
  } catch (err) {
    console.error(`Self-test failed: ${err instanceof Error ? err.message : err}`);
    Deno.exit(1);
  }
}

// ─── HTTP server ─────────────────────────────────────────────────────────────

let lastAlertAt: string | null = null;
let lastDigestAt: string | null = null;
let lastError: string | null = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

Deno.serve({ port: PORT, hostname: "0.0.0.0" }, async (req) => {
  const url = new URL(req.url);

  if (req.method === "GET" && url.pathname === "/health") {
    return json({
      service: "portal-alerter",
      ready: true,
      last_alert_at: lastAlertAt,
      last_digest_at: lastDigestAt,
      last_error: lastError,
      rate_limit_per_min: RATE_LIMIT_PER_MIN,
      window_hours: WINDOW_HOURS,
      coalesce_queue_depth: coalescedQueue.length,
    });
  }

  if (req.method === "POST" && url.pathname === "/alert") {
    let payload: AlertPayload;
    try {
      payload = await req.json();
    } catch (e) {
      // Log 400s too. The original implementation only logged 500s, which
      // made it impossible to see why client requests (e.g., malformed JSON
      // from a script with backslash-escaping bugs) were being rejected.
      // Surface via stderr so `docker logs portal-alerter` shows it.
      const ip = req.headers.get("x-forwarded-for") ?? "(local)";
      console.error(`/alert 400 invalid JSON from ${ip}: ${e instanceof Error ? e.message : e}`);
      return json({ error: "invalid JSON" }, 400);
    }
    if (!payload.severity || !payload.event) {
      const ip = req.headers.get("x-forwarded-for") ?? "(local)";
      console.error(`/alert 400 missing required fields from ${ip}: severity=${payload.severity} event=${payload.event}`);
      return json({ error: "severity and event required" }, 400);
    }
    try {
      const result = await handleAlert(payload);
      lastAlertAt = new Date().toISOString();
      return json({ ok: true, ...result });
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
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
      const stats = await runDigest(windowOverride);
      lastDigestAt = new Date().toISOString();
      return json({ ok: true, stats });
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err);
      console.error(`/run failed: ${lastError}`);
      return json({ error: lastError }, 500);
    }
  }

  return json({ error: "not found", path: url.pathname }, 404);
});

console.log(`portal-alerter listening on :${PORT} (DIGEST_TO=${TO_EMAIL})`);
