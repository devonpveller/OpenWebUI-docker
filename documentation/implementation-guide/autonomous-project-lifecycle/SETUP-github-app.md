# P-APL.0 — one-time GitHub App setup (the only human step)

This is the single manual step in the whole autonomous-project-lifecycle build: register a GitHub App,
install it on your account, and drop its key. ~15 minutes, once. Everything after is automated.

Why an App and not a token: the App's **private key** is the only durable secret, and it can't act on
its own — it just signs a request to mint **short-lived, revocable, per-repo installation tokens**. No
long-lived admin PAT ever sits at rest (this is also the durable fix for the old at-rest-token
concern). See [DESIGN.md §2](DESIGN.md).

---

## 1. Register the App

GitHub → **Settings → Developer settings → GitHub Apps → New GitHub App**.

- **GitHub App name:** anything (e.g. `yourname-agent-org`).
- **Homepage URL:** anything (e.g. your tailnet URL) — not used.
- **Webhook:** **uncheck "Active"** (the bridge polls; it doesn't need webhooks).
- **Repository permissions** (least-privilege — set exactly these):
  - **Administration:** Read & write   ← create/fork repos
  - **Contents:** Read & write         ← push commits/branches
  - **Metadata:** Read-only            ← (mandatory, auto-selected)
  - **Pull requests:** Read & write    ← PR-based delivery
  - leave everything else "No access".
- **Where can this App be installed?** → **Only on this account.**
- Click **Create GitHub App**.

## 2. Note the App ID + generate the private key

On the App's page after creation:
- Copy the **App ID** (a number near the top) → you'll set it as `AO_GITHUB_APP_ID`.
- Scroll to **Private keys → Generate a private key**. A `.pem` downloads. **This is the secret.**

## 3. Install the App on your account

App page → **Install App** (left sidebar) → **Install** on your account. Choose either **All
repositories** or **Only select repositories** (you can add repos later — but note the App can only
fork/create/touch repos it's granted; "All" is simplest for a personal automation account).

## 4. Drop the key + set the env

On the machine running the stack (Windows / PowerShell):

```powershell
# put the downloaded key here (this exact filename; the folder is gitignored):
Move-Item "$HOME\Downloads\your-app.*.private-key.pem" `
  "d:\Open WebUI\ai-stack\agent-org\agent-bridge\secrets\github-app-key.pem"
```

Then in `agent-org/docker/.env` set:

```dotenv
AO_GITHUB_APP_ID=<the App ID number from step 2>
AO_GITHUB_APP_OWNER=<your GitHub login, e.g. devonpveller>
```

## 5. Recreate the bridge + verify

```powershell
cd "d:\Open WebUI\ai-stack\agent-org\docker"
docker compose up -d agent-bridge
docker logs agent-bridge 2>&1 | Select-String "github app"
```

You want to see:

```
github app VERIFIED — capability plane online (slug=... owner=... installation=...)
```

If instead you see `VERIFY FAILED` or `not configured`, the log line says why (usually: the App isn't
installed on that account, the owner login is wrong, or the key file isn't at the expected path).

---

Once you see **VERIFIED**, tell me — that's when I build + validate the capability handlers
(`fork` / `create` / `add_submodule` / `compose`) against the live API (P-APL.1), and you'll be able
to scaffold your monogame + murder + engine + game structure by talking to the PM and approving.
