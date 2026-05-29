# Integration Tasks — Internet-Exposed Front-End

**Companion to:** [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md)
**Status:** v1 ready for execution (post-audit revision 2026-05-14)
**Audience:** the agent (or human) implementing the plan

This document is the **operational task list** that the plan generates. Each task is sized to be checkable: it either succeeds or fails. Every task references the plan section that defines its intent.

If you find yourself adding scope not in this list, **stop**. Either it belongs in a future PR, or the plan needs to be revised first.

---

## Phase 0 — Decision gates and pre-flight prep

These happen **before** any file is created. They have user-facing impact and may invalidate the plan.

| # | Task | Owner | Plan ref | Done when |
|---|---|---|---|---|
| 0.1 | Confirm the user has read [§9.1 Cloudflare privacy callout](./plan-internet-exposed-front-end.md#91-critical-privacy-callout--do-not-skip) and accepts that Cloudflare will see decrypted traffic. | implementer + user | §9.1 | User has explicitly said "yes, proceed with Cloudflare Tunnel" in writing (PR description or chat). |
| 0.2 | Confirm tailnet trust posture for Open Notebook (§6.6 Option A / B / C). Default is C. | implementer + user | §6.6 | Decision recorded in PR description. |
| 0.3 | Confirm Pushover is the chosen notifier (vs ntfy, webhook, etc.). | implementer + user | §2, §8 Step 6 | Pushover application created at https://pushover.net; user-key + API token captured (not yet pasted — see 0.5). |
| 0.4 | Confirm `${PUBLIC_DOMAIN}` is a domain the user owns and can move to Cloudflare DNS. | implementer + user | §9.3 | `dig NS` shows registrar nameservers (pre-migration baseline). |
| 0.5 | Generate all secrets locally and have the user paste them into `.env`. **Do not commit `.env`.** Lock with `icacls .env`. | implementer + user | §6.4, §8 Step 8, §12.5 | `.env` exists with: `PUBLIC_DOMAIN`, `ACME_EMAIL`, `CLOUDFLARE_TUNNEL_TOKEN` (deferred until phase 1), `AUTHELIA_JWT_SECRET`, `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN`, `SURREAL_USER`, `SURREAL_PASSWORD`, `OPEN_NOTEBOOK_ENCRYPTION_KEY`. Permissions verified. |
| 0.6 | Confirm the admin will enroll **two** WebAuthn credentials (or print TOTP recovery secret) before going live. | implementer + user | §6.8 | Recorded in PR description; physical hardware key situation documented. |

**Gate:** if any of 0.1–0.6 are unresolved, stop. Do not start Phase 1.

---

## Phase 1 — Cloudflare-side setup (zero local changes)

Done entirely in the Cloudflare dashboard + DNS registrar.

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 1.1 | Move `${PUBLIC_DOMAIN}` nameservers to Cloudflare. Wait for propagation. | §9.3 step 1 | `dig NS ${PUBLIC_DOMAIN}` returns Cloudflare nameservers from at least 3 distinct resolver IPs (1.1.1.1, 8.8.8.8, 9.9.9.9). |
| 1.2 | Add CAA records. | §9.3 step 2 | `dig CAA ${PUBLIC_DOMAIN}` returns the two `issue` records. |
| 1.3 | In Cloudflare Zero Trust, create a tunnel named `ai-stack`. | §9.3 step 3 | Tunnel visible in dashboard, status "INACTIVE" (no connector yet). Token copied for §1.5. |
| 1.4 | Add public hostnames in the tunnel: `${PUBLIC_DOMAIN}` → `http://caddy:80` and `auth.${PUBLIC_DOMAIN}` → `http://caddy:80`. | §9.3 step 4 | Both visible in tunnel config. DNS records auto-created by Cloudflare. |
| 1.5 | Paste tunnel token into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`. | §9.3 step 3 | `.env` contains the token; permissions still locked. |
| 1.6 | In the Cloudflare zone for `${PUBLIC_DOMAIN}`: SSL/TLS mode = **Full (strict)**, min TLS = 1.2, Always Use HTTPS = on, edge HSTS = off. | §9.3 step 5 | All four settings visible in dashboard. |
| 1.7 | (Optional, strongly recommended) Cloudflare Access policy: country allow-list or email-OTP gate in front of the tunnel. | §9.3 step 6 | Policy active OR explicit user decision to skip recorded. |
| 1.8 | Test Pushover end-to-end with a manual curl, capturing a phone notification, before any container work. | §8 Step 6 (watcher spec) | Phone received a test message from the implementer's curl. |

---

## Phase 2 — Pre-flight exposure fixes (no front-end yet)

These fixes close pre-existing exposures and are safe to ship even if the front-end is never built. **They are the audit's "block v1 until done" items.**

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 2.1 | Bind `open_notebook` ports to `127.0.0.1` in [docker-compose.yml](../../../docker-compose.yml). | §6.1 | `docker compose config` shows `127.0.0.1:8503:8502` and `127.0.0.1:5055:5055`. |
| 2.2 | Bind `surrealdb` ports to `127.0.0.1`. | §6.2 | `docker compose config` shows `127.0.0.1:8003:8000`. |
| 2.3 | **Stop and ask the user** before destroying `surreal_data` for credential migration. | §6.3, §15 STOP list | User has chosen: keep data (write the **existing** root/root creds into `.env`) OR migrate (export → wipe → re-import). |
| 2.4 | Move SurrealDB creds to `.env`: replace `root` / `root` in compose with `${SURREAL_USER}` / `${SURREAL_PASSWORD}`. | §6.3 | `docker compose config` shows env-var references; raw `root` is gone from compose. |
| 2.5 | **Stop and ask the user** before changing `OPEN_NOTEBOOK_ENCRYPTION_KEY` if API keys are stored. | §6.4, §15 STOP list | User has chosen: keep existing key (paste verbatim into `.env`) OR rotate (and accept loss of encrypted-at-rest API keys). |
| 2.6 | Move Open Notebook encryption key to `.env`. | §6.4 | `docker compose config` shows `${OPEN_NOTEBOOK_ENCRYPTION_KEY}`. |
| 2.7 | Verify Windows Defender Firewall: Public profile = default-deny inbound. | §6.5 | `Get-NetFirewallProfile` shows `DefaultInboundAction = Block` on Public profile. |
| 2.8 | Smoke-test the existing stack with pre-flight fixes applied (no front-end yet): `docker compose up -d`, confirm OpenWebUI and Open Notebook still work via tailnet. | implicit | Tailnet smoke test passes. |

**Gate:** if 2.8 fails, stop. Pre-flight fixes broke an existing path — investigate before adding the front-end.

---

## Phase 3 — Configuration files (still no containers running)

Write every config file the front-end will load. Validate syntax before bringing any new container up.

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 3.1 | Create directory tree `config/caddy/`, `config/caddy/site/`, `config/authelia/`, `config/watcher/`, `config/tripwire/`, `backup/`, `scripts/`, `documentation/`. | §7.1 | All directories exist; `ls -la` clean. |
| 3.2 | Write `config/caddy/Caddyfile` per §8 Step 2. | §8 Step 2 | File present; `docker run --rm -v ./config/caddy:/etc/caddy caddy:2.8.4-alpine caddy validate --config /etc/caddy/Caddyfile` exits 0. |
| 3.3 | Write `config/caddy/site/index.html` (hub with double-login UX copy). | §8 Step 5 | File present; opens in a browser with no console errors. |
| 3.4 | Write `config/caddy/site/hub.css` (port the existing prior-plan CSS, no functional change). | §8 Step 5 | File present. |
| 3.5 | Write `config/authelia/configuration.yml` per §8 Step 3. | §8 Step 3 | File present; `docker run --rm -v ./config/authelia:/config authelia/authelia:4.39 authelia validate-config --config /config/configuration.yml` exits 0. |
| 3.6 | Generate Argon2id hash for admin password via `docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2 --password <pw>`. Verify pw is NOT in HIBP first. | §8 Step 4, §6.8 | Hash captured; pw confirmed not in HIBP. |
| 3.7 | Write `config/authelia/users_database.yml` with the hash. Add `config/authelia/.gitignore` containing `users_database.yml`. Append `/config/authelia/users_database.yml` to top-level `.gitignore`. | §8 Step 4 | `git status` shows users_database.yml is ignored. |
| 3.8 | Write `config/watcher/authelia-watch.sh` per spec in §8 Step 6 (Pushover sidecar). | §8 Step 6 | Script present; static review against spec table; `sh -n /path/to/script` exits 0. |
| 3.9 | Write `config/watcher/known-ips.txt` (empty initially; LAN IPs may be pre-seeded one per line). | §8 Step 6 | File present. |
| 3.10 | Write `config/tripwire/integrity-tripwire.sh` per spec in §8 Step 6 (init/check/accept modes). | §8 Step 6 | Script present; `sh -n` exits 0; all three modes documented. |
| 3.11 | Add `/config/tripwire/baseline.sha256` to `.gitignore` (this file is per-deployment state). | §7.2 | `git status` confirms ignore. |
| 3.12 | Write `backup/caddy-backup.sh` and `backup/authelia-backup.sh` per §8 Step 7 (including `.sha256` sentinel). | §8 Step 7 | Both scripts present, executable; `sh -n` exits 0. |
| 3.13 | Write `scripts/breach-killswitch.ps1` per spec in §8 Step 6. | §8 Step 6 | Script present; `powershell -NoProfile -File scripts/breach-killswitch.ps1 -WhatIf` (or `-DryRun` mode you build) prints the intended actions without executing them. |
| 3.14 | Write `documentation/incident-response.md` per outline in §13.6. | §13.6 | File present; covers all 7 outline sections. Implementer walks the user through it end-to-end before merge. |

---

## Phase 4 — docker-compose.yml changes

Apply every compose change in one PR-staged edit. Validate before running.

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 4.1 | Add 3 new networks: `edge-net` (bridge), `auth-net` (bridge, `internal: true`), `app-net` (bridge), and `notify-net` (bridge, NOT internal) per §8 Step 1 note. | §7.4, §8 Step 1 | `networks:` block contains all four. |
| 4.2 | Add `app-net` to `openwebui.networks` (additive, do not remove existing). | §7.4 | `docker compose config` shows openwebui on `default`, `llm-net`, `app-net`. |
| 4.3 | Add `app-net` to `open_notebook.networks` (additive). | §7.4 | Same as 4.2 for open_notebook. |
| 4.4 | Add `cloudflared` service per §8 Step 1. | §8 Step 1 | `docker compose config` includes `cloudflared`. |
| 4.5 | Add `caddy` service per §8 Step 1 (no host port bindings; on `edge-net` + `auth-net` + `app-net`; read-only FS; non-root UID; cap_drop ALL + NET_BIND_SERVICE). | §8 Step 1 | Present and configured. |
| 4.6 | Add `authelia` service per §8 Step 1 (`auth-net` only; read-only FS; non-root UID; cap_drop ALL). | §8 Step 1 | Present and configured. |
| 4.7 | Add `authelia-watcher` service (`auth-net` + `notify-net`; cap_drop ALL; read-only). | §8 Step 1 | Present and configured. |
| 4.8 | Add `integrity-tripwire` service (`auth-net` + `notify-net`; cap_drop ALL). | §8 Step 1 | Present and configured. |
| 4.9 | Add `caddy-backup` and `authelia-backup` services with the cron env-passing fix from [audit §B.4](./audit-plan-internet-exposed-front-end.md#b4--backup-containers-do-not-run-their-secrets-through-the-cron-environment). | §8 Step 1, §8 Step 7 | Present; the `printenv | grep | /etc/profile.d/...` shim is in place. |
| 4.10 | Add new volumes: `caddy-data`, `caddy-config`, `authelia-data`, `tripwire-data`. | §7.3 | All four listed in `volumes:`. |
| 4.11 | `docker compose config` exits 0 with no warnings. | implicit | Confirmed. |
| 4.12 | `docker compose pull` succeeds for all new images. | implicit | Confirmed. |

---

## Phase 5 — Bring-up and validation

Stage-by-stage start. **Don't** `docker compose up -d` everything at once on first run.

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 5.1 | Start authelia first: `docker compose up -d authelia`. Tail logs. | §15 step 7 | "Authelia is listening on tcp://0.0.0.0:9091" in `docker logs authelia`. No errors. |
| 5.2 | Start caddy: `docker compose up -d caddy`. Tail logs. | §15 step 7 | Caddy logs show config loaded, listening on `:80`. No errors. |
| 5.3 | Start cloudflared: `docker compose up -d cloudflared`. Wait for tunnel connection. | §15 step 7 | `docker logs cloudflared` shows "Connection registered" + "Updated to new configuration" against at least one CF edge. Tunnel dashboard shows "HEALTHY". |
| 5.4 | First Authelia login from a LAN browser: `https://auth.${PUBLIC_DOMAIN}/`. | §11 functional | Login screen renders. Existing admin user + Argon2id hash from 3.6 authenticates. |
| 5.5 | Enroll first WebAuthn credential. Confirm flow works under `same_site: lax`. | §11 pre-deploy WebAuthn | One credential enrolled. |
| 5.6 | Enroll **second** WebAuthn credential (or print TOTP recovery). | §6.8 | Recovery path verified. |
| 5.7 | Test the portal end-to-end: `https://${PUBLIC_DOMAIN}/` → Authelia → hub → OpenWebUI (with its own login) → back to hub → Open Notebook (no second login). | §11 functional | All routes work. |
| 5.8 | Test the tailnet path is unaffected: from a tailnet device, `https://<tailnet-host>.ts.net/` still hits OpenWebUI's native login directly. **Canary test.** | §11 functional, §15 step 8 | Tailnet flow unchanged. |
| 5.9 | Start `authelia-watcher` and `integrity-tripwire`: `docker compose up -d authelia-watcher integrity-tripwire`. | §11 deploy | Both show "watching" / "baseline established". |
| 5.10 | Start backup containers: `docker compose up -d caddy-backup authelia-backup`. | §11 deploy | Both running; first manual backup confirmed: `docker exec caddy-backup sh /scripts/backup.sh`. |
| 5.11 | Walk every box in §11 "Security smoke tests". | §11 | All boxes ticked. Specifically: §11 `Remote-*` strip test, X-Forwarded-For from CF-Connecting-IP, network policy LAN-vs-internet, backend isolation, `auth-net` no-egress test, healthcheck not publicly exposed, body limit, backup integrity. |
| 5.12 | Walk every box in §11 "Breach detection / IR validation". | §11 | Pushover end-to-end, new-IP alert, config-drift alert, killswitch dry-run, IR doc walked. |
| 5.13 | Walk the recovery test in §11 (restore from yesterday's backup, confirm enrollments survive). | §11 | Confirmed. |

**Gate:** if **any** box in 5.11 / 5.12 / 5.13 fails, stop and fix before declaring v1 done. The plan's "Definition of Done" in §15 is the merge criterion.

---

## Phase 6 — Operational handover

| # | Task | Plan ref | Done when |
|---|---|---|---|
| 6.1 | Verify off-host backup sync of `./backups/` is configured (rclone / OneDrive / B2 / restic / etc.). | §12.2 | Implementer can show one successful off-host sync. |
| 6.2 | Document any departures from the plan in the PR description (sub-path subdomain fallback, deferred tasks, etc.). | §15 | PR description complete. |
| 6.3 | Update `SECURITY.md` with: tailnet trust posture decision (§6.6), Cloudflare-sees-decrypted-traffic acknowledgment (§9.1), Pushover alert routing destination (which phone / which user). | §12.6, §13 | `SECURITY.md` updated and merged with the PR. |
| 6.4 | Walk the user through the IR playbook (`documentation/incident-response.md`) one time, in front of the actual stack. | §13.6, §11 IR validation | User signs off in PR comments. |
| 6.5 | Merge PR. Tag the release. | implicit | Done. |
| 6.6 | (Post-merge) Wait 90 days of stable operation. **Then** evaluate HSTS preload-list submission ([audit §H.1](./audit-plan-internet-exposed-front-end.md#h-smaller-findings-in-order-of-priority)). Do not submit before 90 days. | §H.1, §12.8 | Calendar reminder set; do not submit early. |

---

## Out of this PR — deliberately

Each of these is in scope **somewhere**, just not this PR. Resist the urge to bundle.

| Item | Why deferred | Future home |
|---|---|---|
| CrowdSec + AppSec + ratelimit | Separate PR per plan §10 | v2 PR |
| `tmpfs` and `pull_policy` hardening on `openwebui`, `open_notebook`, `surrealdb` | "Untouched services" requirement | §13.9 |
| Grafana Loki / centralized log aggregation | Tier 2 | §13.2 |
| HashiCorp Vault | Tier 2 | §13.4 |
| Wazuh / host-level FIM | Tier 2 | §13.5 |
| HIBP integration in Authelia | Authelia has no native HIBP | §13.7 |
| pfSense / OPNsense / VLAN segmentation | Tier 2 — requires new hardware | §13.1 |
| SMTP notifier for Authelia | Requires moving Authelia off `auth-net internal: true` | §8 Step 3 footnote |

---

## Quick command reference

These are the commands the implementer will run most frequently. Capture them here so they don't drift across sub-tasks.

```powershell
# Validate Caddyfile before reload
docker run --rm -v ${PWD}/config/caddy:/etc/caddy caddy:2.8.4-alpine caddy validate --config /etc/caddy/Caddyfile

# Validate Authelia config
docker run --rm -v ${PWD}/config/authelia:/config authelia/authelia:4.39 authelia validate-config --config /config/configuration.yml

# Generate Argon2id hash
docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2 --password '<pw>'

# Generate Authelia secret
docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64

# Inspect a Docker network's actual CIDR (for the Caddyfile trusted_proxies)
docker network inspect edge-net | Select-String "Subnet"

# Force a backup right now
docker exec caddy-backup sh /scripts/backup.sh
docker exec authelia-backup sh /scripts/backup.sh

# Re-baseline integrity tripwire after a deliberate config change
docker exec integrity-tripwire /scripts/tripwire.sh accept

# Killswitch (REAL — only run during a confirmed incident or a dry-run window)
powershell -NoProfile -File scripts/breach-killswitch.ps1
```

---

## Cross-references

- Plan: [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md)
- Audit: [audit-plan-internet-exposed-front-end.md](./audit-plan-internet-exposed-front-end.md)
- Threat model: [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md)
- Active compose: [docker-compose.yml](../../../docker-compose.yml)
- Tailscale entrypoint (unchanged): [entrypoint.sh](../../../entrypoint.sh)
