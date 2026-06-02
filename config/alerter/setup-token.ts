#!/usr/bin/env -S deno run --allow-net --allow-read --allow-write --allow-env

/**
 * One-time OAuth bootstrap for the portal alerter.
 *
 * Run this on the HOST (not in a container). It will:
 *   1. open the Google consent screen in your default browser
 *   2. capture the redirect on http://127.0.0.1:8765
 *   3. exchange the code for tokens with the gmail.send scope
 *   4. write secrets/google/portal-alerter/token.json
 *
 * After this completes once, the portal-alerter container can run
 * unattended — it only needs to refresh the access token (the refresh
 * token persists in token.json).
 *
 * Prereq: credentials.json next to this file. Provision a DEDICATED OAuth
 * client in Google Cloud Console (separate from OB1's open-brain-email
 * client) and save the JSON to secrets/google/portal-alerter/credentials.json,
 * then copy it here:
 *   Copy-Item `
 *     secrets/google/portal-alerter/credentials.json `
 *     config/alerter/credentials.json
 *
 * The OAuth consent screen for that client must include the gmail.send scope.
 * If you provision the client in OB1's existing GCP project (recommended),
 * the consent screen already lists gmail.send because OB1's daily-digest
 * uses it — no scope changes needed.
 *
 * Usage (PowerShell, from the workspace root):
 *   deno run --allow-net --allow-read --allow-write --allow-env config/alerter/setup-token.ts
 */

// URL objects work across Windows + Linux without URL-encoding bugs.
// Deno.readTextFile / writeTextFile / mkdir all accept URL directly.
const CREDENTIALS_URL = new URL("./credentials.json", import.meta.url);
const TOKEN_OUT_URL = new URL("../../secrets/google/portal-alerter/", import.meta.url);
const TOKEN_URL = new URL("token.json", TOKEN_OUT_URL);
const SCOPES = ["https://www.googleapis.com/auth/gmail.send"];
const REDIRECT_PORT = 8765;
const REDIRECT_URI = `http://127.0.0.1:${REDIRECT_PORT}`;
const FLOW_TIMEOUT_MS = 5 * 60 * 1000;

interface OAuthCredentials {
  installed: { client_id: string; client_secret: string; redirect_uris: string[] };
}

async function main() {
  let creds: OAuthCredentials;
  try {
    creds = JSON.parse(await Deno.readTextFile(CREDENTIALS_URL));
  } catch {
    console.error(
      `\nNo credentials.json at ${CREDENTIALS_URL}.\n` +
        `Copy your DEDICATED portal-alerter OAuth client secret here, then re-run:\n` +
        `  Copy-Item secrets/google/portal-alerter/credentials.json config/alerter/credentials.json\n`,
    );
    Deno.exit(1);
  }

  const authUrl = new URL("https://accounts.google.com/o/oauth2/v2/auth");
  authUrl.searchParams.set("client_id", creds.installed.client_id);
  authUrl.searchParams.set("redirect_uri", REDIRECT_URI);
  authUrl.searchParams.set("response_type", "code");
  authUrl.searchParams.set("scope", SCOPES.join(" "));
  authUrl.searchParams.set("access_type", "offline");
  authUrl.searchParams.set("prompt", "consent");

  console.log(
    `\nOpen this URL in your browser to grant gmail.send to the portal alerter:\n  ${authUrl}\n` +
      `\nWaiting for the redirect on ${REDIRECT_URI} (5 min timeout)...\n`,
  );

  const code = await new Promise<string>((resolve, reject) => {
    const ac = new AbortController();
    const timer = setTimeout(() => {
      ac.abort();
      reject(new Error("OAuth flow timed out — no redirect received."));
    }, FLOW_TIMEOUT_MS);

    Deno.serve(
      { port: REDIRECT_PORT, signal: ac.signal, onListen: () => {} },
      (req) => {
        const u = new URL(req.url);
        const c = u.searchParams.get("code");
        const err = u.searchParams.get("error");
        if (err) {
          clearTimeout(timer);
          ac.abort();
          reject(new Error(`OAuth error: ${err}`));
          return new Response(`OAuth error: ${err}`, { status: 400 });
        }
        if (c) {
          clearTimeout(timer);
          resolve(c);
          setTimeout(() => ac.abort(), 100);
          return new Response(
            "Portal alerter authorization complete. You may close this tab.",
            { headers: { "Content-Type": "text/plain" } },
          );
        }
        return new Response("No code in request.", { status: 400 });
      },
    );
  });

  console.log("Exchanging code for tokens...");
  const tokenRes = await fetch("https://oauth2.googleapis.com/token", {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      code,
      client_id: creds.installed.client_id,
      client_secret: creds.installed.client_secret,
      redirect_uri: REDIRECT_URI,
      grant_type: "authorization_code",
    }),
  });
  const tokenData = await tokenRes.json();
  if (tokenData.error) {
    throw new Error(`Token exchange failed: ${tokenData.error_description || tokenData.error}`);
  }
  if (!tokenData.refresh_token) {
    throw new Error(
      "No refresh_token returned. Revoke the existing consent for this OAuth client " +
        "(https://myaccount.google.com/permissions) and re-run to force a fresh consent.",
    );
  }

  const token = {
    access_token: tokenData.access_token,
    refresh_token: tokenData.refresh_token,
    token_type: tokenData.token_type,
    expiry_date: Date.now() + tokenData.expires_in * 1000,
  };

  await Deno.mkdir(TOKEN_OUT_URL, { recursive: true });
  await Deno.writeTextFile(TOKEN_URL, JSON.stringify(token, null, 2));
  console.log(`\nWrote ${TOKEN_URL}.`);
  console.log(`Next: run the alerter self-test to confirm Gmail delivery (plan §6.9 step 4).`);
}

await main();
