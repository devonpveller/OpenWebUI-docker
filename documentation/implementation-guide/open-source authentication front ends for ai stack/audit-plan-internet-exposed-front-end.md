# Audit — Internet-Exposed Front-End Plan

**Target:** [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md)
**Auditor scope:** cohesion, watertight policies, Docker-native security, breach detection, incident response, untouched-services guarantee
**Audit date:** 2026-05-14
**Verdict:** **Plan is structurally sound but NOT ready to merge.** Four blocker-class issues, twelve high-priority gaps, and pervasive section-number drift. See §A.

---

## A. Executive summary

| Category | Verdict | Action |
|---|---|---|
| Cohesion / structure | **Drift** | Renumber §3 matrix, §2/§4 cross-refs; one PR (§G) |
| Watertight auth policies | **Has a critical hole** | §B.1 trusted-header forgery — must fix before v1 ships |
| Docker-native hardening | **Underused** | §C — segment networks, enforce read-only FS, lock egress |
| Breach detection (v1) | **Absent** | §D — add a detection sidecar; do not defer to v2 |
| Incident response | **Absent** | §E — write `incident-response.md` and a kill-switch script |
| Untouched-services guarantee | **Partially honored** | §F — tighten the OpenWebUI / Open Notebook changes |
| Tailnet path | **Intact** | No `entrypoint.sh` or tailscale-container changes; PASS |

The plan correctly preserves the tailnet path as a parallel entry point and explicitly forbids modifying the tailscale container, its `serve` config, or the network namespace sharing with OpenWebUI. That part of the requirement is met.

---

## B. Blockers (must fix before merge)

### B.1 — `Remote-*` trusted-header forgery via the tailnet path (CRITICAL)

**Location:** plan §7 Step 6, plan §7.2 Caddyfile `sanitize_proxy_headers` snippet.

**The setup**

- Plan adds `WEBUI_AUTH_TRUSTED_EMAIL_HEADER=Remote-Email` and `WEBUI_AUTH_TRUSTED_NAME_HEADER=Remote-Name` to OpenWebUI's environment ([docker-compose.yml:18-37](../../../docker-compose.yml#L18-L37)).
- The Caddyfile's `sanitize_proxy_headers` snippet strips `X-Forwarded-For`, `X-Real-IP`, `X-Forwarded-Host` from inbound requests, but **does not strip `Remote-User`, `Remote-Name`, `Remote-Email`, `Remote-Groups`**.
- Tailscale serve proxies tailnet traffic directly into OpenWebUI's port 8080 ([entrypoint.sh](../../../entrypoint.sh)).
- Once trusted-header SSO is enabled, OpenWebUI auto-authenticates **any** request whose `Remote-Email` header matches a known user.

**The hole**

1. **Via Caddy:** if a client adds `Remote-Email: admin@example.com` to a request, Caddy's `forward_auth` calls Authelia which sets the headers on success — but Caddy never explicitly drops the client-supplied versions before `forward_auth` overwrites them. Caddy's `forward_auth` *does* set headers in the copied response, but the original request still passes through to `reverse_proxy` carrying any client-supplied `Remote-*`. Behavior depends on Caddy version and directive order — relying on implicit overwrite is unsafe.
2. **Via the tailnet:** anyone on the tailnet (or anyone who reaches OpenWebUI's `127.0.0.1:3000` if they pivot from a compromised LAN host) can send `Remote-Email: admin@example.com` directly and bypass OpenWebUI's native login. Tailscale serve does not sanitize HTTP headers; it is a transport-level proxy.

**Required fix (v1, non-optional):**

1. In `sanitize_proxy_headers`, add:
   ```caddy
   request_header -Remote-User
   request_header -Remote-Groups
   request_header -Remote-Name
   request_header -Remote-Email
   ```
   This guarantees the only `Remote-*` headers reaching OpenWebUI come from Authelia via `forward_auth`.
2. **Decide and document the tailnet posture.** Three options:
   - **(Preferred) Tailnet header strip:** add a thin proxy (Caddy or a `--header` rule) between `tailscale serve` and OpenWebUI that drops `Remote-*` and `X-Forwarded-*` from inbound tailnet requests. This requires adding logic to `entrypoint.sh` — which contradicts the "untouched" requirement. Trade-off must be surfaced.
   - **Bind trusted headers to a single source:** OpenWebUI supports `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` but does not natively pin it to a source CIDR. Wrap the trust by setting a `WEBUI_AUTH_TRUSTED_GROUPS_HEADER` Authelia-signed value the tailnet path cannot mint. Validate against the OpenWebUI docs first (see §H references).
   - **Accept and document:** mark the tailnet path as "trust-equivalent to admin on the host" and recommend Option C in §6.6. Add an explicit warning in [SECURITY.md](../../../SECURITY.md) that **anyone reachable on the tailnet can become any OpenWebUI user by setting `Remote-Email`**. This is a real expansion of the existing tailnet trust model and must not slip past review.

**Why this is a blocker:** the plan as written grants a silent global-admin path to any tailnet member. The pre-flight §6.6 Option C ("accept tailnet members as trusted-equivalent") was written for the *Open Notebook* unauthenticated path, not for OpenWebUI; this change **expands the trust footprint to OpenWebUI without the user signing off on it**.

### B.2 — Cloudflare Tunnel mode breaks per-IP regulation and LAN bypass

**Location:** plan §9.

When `cloudflared` fronts Caddy, every request Caddy sees has source IP `cloudflared`'s container IP. Effects:

- Authelia's `access_control.networks.home_lan` rule never matches → **internet-MFA policy applies to LAN users too** (acceptable, but unintentional).
- Authelia's `regulation` ban-by-IP becomes "ban Caddy from talking to Authelia for 1 hour" the moment 3 fails occur — possibly a self-DoS.
- Caddy's future v2 rate-limit (`key {remote_host}`) keys on `cloudflared` for everyone — same DoS pattern.

**Required fix:** in Cloudflare Tunnel mode, the Caddyfile must add a `trusted_proxies` directive (or equivalent) that honors `CF-Connecting-IP`, and Authelia's `server` block must trust the forwarded IP. Add explicit config snippets to §9 for tunnel mode; do not leave this as "update forward_auth URLs."

### B.3 — Caddy `forward_auth` upstream-header bleed-through

**Location:** plan §7.2 site-level `forward_auth` block.

The Caddyfile uses:
```
forward_auth authelia:9091 {
    uri /api/verify?rd=https://auth.{$PUBLIC_DOMAIN}/
    copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
}
```

`copy_headers` copies from **Authelia's response** to the request forwarded upstream. It does NOT remove client-supplied versions of those headers from the inbound request. This is the same root cause as B.1. The fix is the explicit `request_header -Remote-*` block. List both fixes together in the same PR.

### B.4 — Backup containers do not run their secrets through the cron environment

**Location:** plan §8 Step 1 `caddy-backup` and `authelia-backup` blocks.

The backup containers set `BACKUP_DIR`, `DATA_DIR`, `RETAIN_DAYS`, `BACKUP_CRON` as compose env vars. `crond` then writes the crontab and executes `/scripts/backup.sh`. **alpine's `crond` runs cron jobs with a minimal environment — compose env vars are NOT inherited by the cron-spawned process.** The existing pattern in `mnemory-backup` may or may not hit this; verify before copying.

**Required fix:** either (a) hardcode paths in the backup scripts (kill the env-var indirection), or (b) write the env into `/etc/profile.d` and source it in the cron entry, or (c) use `BusyBox`'s `crond` env-passing flag `crond -f -l 2 -L /dev/stdout` with a `crontab` entry that uses `env BACKUP_DIR=... DATA_DIR=... /scripts/backup.sh`. Option (c) is cleanest.

---

## C. Docker-native security: the plan underuses the container boundary

The plan treats Docker as a packaging mechanism, not a security boundary. Several high-leverage hardening levers are unused.

### C.1 — Network segmentation **inside** Docker

Currently the plan places `caddy`, `authelia`, and the backend containers (`openwebui`, `open_notebook`) on the same `default` bridge. A Caddy compromise → direct L3 access to Authelia AND to both backends.

**Recommended layout (no extra hosts, no VLANs — just Docker networks):**

```yaml
networks:
  edge-net:
    driver: bridge        # caddy + cloudflared
  auth-net:
    driver: bridge        # caddy + authelia ONLY
    internal: true        # no egress to internet
  app-net:
    driver: bridge        # caddy + openwebui + open_notebook
  llm-net:
    internal: true        # unchanged
```

Attach:
- `caddy`: `edge-net`, `auth-net`, `app-net`
- `cloudflared` (if used): `edge-net`
- `authelia`: `auth-net` only (cannot egress to internet — no ACME, no SMTP, no telemetry; matches §13.4 zero-trust intent)
- `openwebui`: `app-net`, `llm-net` (add `app-net`; remove from `default` if currently there)
- `open_notebook`: `app-net`, `llm-net`

**Effect:** if Caddy is RCE'd the attacker still cannot pivot directly to Authelia's storage from the openwebui container's namespace, and Authelia loses outbound internet entirely. The blast radius drops from "everything on default" to "the specific neighbor each network grants."

**Caveat:** if Authelia is `internal: true`, the notifier cannot reach SMTP. v1 uses `filesystem` notifier — fine. Document that switching to SMTP in v2 requires adding a controlled egress path.

### C.2 — Read-only root filesystem with explicit writable tmpfs

The plan currently sets `read_only: false` on both Caddy and Authelia with the comment "Caddy needs to write to /data and /config." That's true, but `/` itself does not need to be writable. Pattern:

```yaml
caddy:
  read_only: true
  tmpfs:
    - /tmp:noexec,nosuid,size=64m
  # /data and /config are bind-mounted volumes — writable by mount
```

Same for Authelia. This blocks the classic post-RCE "drop a webshell into `/usr/local/bin`" move.

### C.3 — Drop the docker socket from ALL containers

Verify: no container currently mounts `/var/run/docker.sock` **except** `watchtower` (which mounts it read-only). The plan adds `caddy`, `authelia`, `caddy-backup`, `authelia-backup`, optionally `cloudflared`. **None** should mount the docker socket. Explicit assertion in the plan would prevent accidental drift.

### C.4 — `pids_limit` + `ulimits` + `mem_limit` already present, but not on every new container

`caddy` has CPU/memory limits via `deploy.resources.limits`. `authelia` does. But `caddy-backup`, `authelia-backup`, `cloudflared` do not. Add:

```yaml
pids_limit: 100
ulimits:
  nofile:
    soft: 1024
    hard: 2048
```

Prevents a hostile (or buggy) backup container from fork-bombing.

### C.5 — `cap_drop: ALL` missing from `cloudflared` example

Plan §9 Tunnel block omits `cap_drop`. Add:
```yaml
    cap_drop:
      - ALL
```

`cloudflared` does not need any capabilities — it makes outbound TCP connections, which a non-root user can do with no caps.

### C.6 — Non-root UID on Caddy / Authelia / backups

Caddy's official image runs as root by default (to bind 80/443). With `NET_BIND_SERVICE` capability granted explicitly, Caddy can run as non-root. The plan grants `NET_BIND_SERVICE` already — add:

```yaml
caddy:
  user: "10000:10000"
  cap_drop: [ALL]
  cap_add: [NET_BIND_SERVICE]
```

Authelia's image supports `user:` directly. Backup containers run as root by default — switch to a non-root UID matching the backup directory's owner.

**Verify on Windows + Docker Desktop:** UID mapping with bind mounts on Windows is finicky. Test on the target machine before committing.

### C.7 — `tmpfs` for OpenWebUI is already there but inconsistent with the new pattern

OpenWebUI has `tmpfs: - /tmp:noexec,nosuid,size=100m`. Apply the same pattern to `open_notebook` while the implementing agent is in the file. (Note: the user's "untouched" rule means this should be a SEPARATE PR — flag it in `documentation/incident-response.md` follow-ups.)

### C.8 — Healthcheck endpoint exposure

`/healthz` returns `200 ok` publicly. This leaks "this server is up" to internet scanners. Two options:

- (Preferred) Make `/healthz` a **path-restricted route**: only respond 200 to requests from inside the container network. Caddy can match on `remote_host` ranges.
- Keep public but add a per-route response delay or rate-limit so it is not useful as a fast keep-alive probe for botnets.

### C.9 — `Dockerfile.caddy` (v2) reproducibility

§10.4 says `FROM caddy:2.8-builder AS builder`. Two issues:

- The `2.8-builder` tag tracks 2.8.x — newer patch levels may break the build. Pin to a specific patch (e.g., `caddy:2.8.4-builder`).
- The `xcaddy build` line pulls Go modules from GitHub **at build time** with no integrity verification. Add `--with <module>@<commit-sha>` pinning, and consider mirroring deps to a private registry for v2.

---

## D. Breach detection — v1 must not defer this to v2

The user asked specifically about breach detection. The plan defers all detection to v2 CrowdSec. That is unacceptable for an internet-exposed surface — v1 needs **at minimum** the ability to notice an active intrusion attempt within minutes, not days.

### D.1 — Authelia event-tail sidecar (v1, lightweight)

Add a small sidecar container that tails `authelia.log` (already JSON, already in `/data`) and pushes events to a user-controlled notifier. Reference implementation:

```yaml
authelia-watcher:
  image: alpine:3.21
  container_name: authelia-watcher
  networks:
    - auth-net
  volumes:
    - authelia-data:/logs:ro
    - ./scripts/authelia-watch.sh:/scripts/watch.sh:ro
  environment:
    - NTFY_URL=${BREACH_NTFY_URL}    # https://ntfy.sh/<your-topic>
    - WEBHOOK_URL=${BREACH_WEBHOOK_URL:-}
  entrypoint: /bin/sh
  command: ["/scripts/watch.sh"]
  restart: unless-stopped
  security_opt: [no-new-privileges:true]
  cap_drop: [ALL]
  read_only: true
  tmpfs: [/tmp:noexec,nosuid,size=16m]
  pids_limit: 50
```

`scripts/authelia-watch.sh` watches for these structured-log events and pushes a notification:

| Event | Trigger | Severity |
|---|---|---|
| `regulation` ban applied | username locked after 3 failures | **medium** |
| `authentication.success` from new IP | source-IP not seen in last 30 days | **high** |
| WebAuthn credential added/removed | enrollment delta | **high** |
| TOTP credential added/removed | enrollment delta | **high** |
| Authelia config reload | someone touched `configuration.yml` | **high** |
| Users-database modified | hash of `users_database.yml` changes outside an approved window | **critical** |
| Repeated `authentication.failed` from same IP | >5 in 5 min, regardless of username | **medium** |

ntfy.sh is free, requires no API key, and runs on a phone. Pushover and a self-hosted Gotify alternative are also acceptable. **The user must pick one before merge and put the URL/token in `.env`.**

### D.2 — Caddy access log enabled in v1 (currently deferred)

The Caddyfile in §7 Step 2 does not enable an access log. Without one, post-incident forensics is blind. Add to each site block:

```caddy
log {
    output file /data/caddy-access.log {
        roll_size 100MiB
        roll_keep 7
        roll_keep_for 30d
    }
    format json
    level INFO
}
```

The plan documents `/data` as Caddy's writable volume (`caddy-data`). Logs join the backup set automatically.

### D.3 — Config-file integrity tripwire

A second small sidecar that nightly checksums:
- `config/caddy/Caddyfile`
- `config/authelia/configuration.yml`
- `config/authelia/users_database.yml`

…and alerts if any hash changes outside an "approved-change" window (e.g., a `.maintenance` file present). Pseudocode is 15 lines of shell. This is the home-stack equivalent of AIDE.

### D.4 — Container restart anomaly detection

Any unexpected restart of `caddy`, `authelia`, or a backend container is a signal. Either:

- A sidecar that polls `docker events` (requires socket — **do not do this**), or
- A sidecar that exec's `docker inspect` via SSH back to the host (also bad), or
- (Recommended) Each container's healthcheck failures already log to the container's lifecycle. Wire `restart` events into the watcher via Caddy's admin API → custom Caddy module is overkill. **Acceptable v1 alternative:** add to `scripts/check-tailscale-health.ps1` (existing pattern) a check that all expected containers have been up >5 min; alert on flap.

### D.5 — TLS certificate expiry monitor

Caddy auto-renews but failures are silent unless someone tails the log. Add to the same daily check script: query the live cert's `notAfter`; alert if <14 days remain. One-line in PowerShell:

```powershell
(New-Object System.Net.Sockets.TcpClient).Connect($env:PUBLIC_DOMAIN, 443)
# … parse cert, compare $cert.NotAfter to (Get-Date).AddDays(14)
```

---

## E. Incident response — what happens when a breach is detected

The plan defers IR to Tier 2 (§13.6). The user explicitly asked. v1 needs a written playbook **and** an automated kill-switch.

### E.1 — `scripts/breach-killswitch.ps1`

A single command the user can run from anywhere they have SSH/RDP into the host:

```powershell
# Stops only the internet-exposed path. Tailnet path remains alive.
docker stop caddy cloudflared authelia
# Rotate Authelia secrets in-place
$NEW_JWT     = (docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64).Trim()
$NEW_SESSION = (docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64).Trim()
# … patch .env, restart authelia with new secrets (invalidates all sessions)
# Snapshot logs before any restart
Copy-Item -Recurse \\wsl$\docker-desktop-data\... ./incident-$(Get-Date -Format yyyyMMdd-HHmmss)
```

(The Windows path above is illustrative — the real implementation should use `docker cp` to grab `authelia-data/authelia.log` and `caddy-data/caddy-access.log` before any volume modification.)

The killswitch must:

1. Stop `caddy` and `cloudflared` (kills internet access). **Leave** `tailscale`, `openwebui`, `open_notebook`, `surrealdb` alive — tailnet admin access must survive a breach.
2. Snapshot Authelia + Caddy logs to a timestamped directory **outside** the volumes (so they're not overwritten by recovery).
3. Rotate `AUTHELIA_JWT_SECRET` + `AUTHELIA_SESSION_SECRET` (invalidates all sessions).
4. Optionally rotate `OPEN_NOTEBOOK_ENCRYPTION_KEY` if Open Notebook is implicated — **but** see plan §6.4 about API-key invalidation.
5. Print clear next-step instructions: "tailnet is still up; reach OpenWebUI via tailscale; re-issue Authelia password via admin path; re-enroll WebAuthn."

### E.2 — `documentation/incident-response.md`

Write the playbook now, not later. Suggested table of contents:

1. **Detection signals** — what each ntfy alert means and which action it maps to
2. **Containment** — run the killswitch; confirm tailnet still serves OpenWebUI
3. **Eradication** — rotate JWT/session/storage secrets; regenerate Argon2 hashes; wipe WebAuthn enrollments; re-issue Let's Encrypt certs if private key may be exposed
4. **Recovery** — restore Authelia from the **previous-day** backup (assume current is poisoned); validate user list
5. **Forensics** — preserve logs; check container filesystem deltas (`docker diff caddy`, `docker diff authelia`); look for unexpected mounts / processes
6. **Notification** — what to tell household users; what to put in `SECURITY.md` updates
7. **Post-mortem** — root cause; update detection rules; close the gap

This is ~2 hours of writing. It cannot be deferred and still call the plan "watertight."

### E.3 — Backup restore is in the validation checklist but not under stress

Plan §11 has "Recovery test (do once, then document the procedure)." That's a healthy start but a real IR drill restores from a backup that's **N days old**, not yesterday's. Add to the checklist:

- Restore Authelia from a 7-day-old backup; verify TOTP/WebAuthn enrollments from that snapshot still validate.
- Confirm a stale session cookie (from before secret rotation) is rejected.

---

## F. "Untouched services" guarantee — partially honored

The user requirement: *"open webui and open notebook must remain otherwise untouched."*

The plan does touch both. Some changes are unavoidable; some are arguably scope creep.

| File | Change | Necessary? | Verdict |
|---|---|---|---|
| OpenWebUI env: `WEBUI_AUTH_TRUSTED_*_HEADER` | Adds 2 env vars | Yes — trusted-header SSO requires it | **OK but flag B.1** |
| Open Notebook `ports:` (§6.1) | `8503:8502` → `127.0.0.1:8503:8502` | Yes — leaves public bind otherwise | **OK** |
| SurrealDB `ports:` (§6.2) | Same as above | Yes — open DB on LAN is bad | **OK** |
| SurrealDB credentials (§6.3) | hardcoded `root` → `${SURREAL_USER}` | Yes — default creds are unsafe | **OK; user-approval gate already present** |
| Open Notebook encryption key (§6.4) | hardcoded `Im-a-secret-key-for-secrets-Tcd3` → env | Yes — secret-in-repo | **OK; user-approval gate already present** |

The plan is honest about all of these and gates the destructive ones (data migration). My read: **the "untouched" requirement is honored in spirit** — every touch is a pre-existing-exposure fix, not a feature change. But the trusted-header SSO change in §F row 1 is a behavioral change to OpenWebUI's auth model that must be called out more prominently than it currently is. Add to plan §6 a new sub-section **6.7 — Behavioral change to OpenWebUI authentication** that explicitly states:

- "Enabling `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` makes OpenWebUI accept the `Remote-Email` header as authoritative. This is a behavioral change. Per the audit's §B.1, the tailnet path must be considered: anyone reaching `openwebui:8080` who can set HTTP headers can impersonate any OpenWebUI user."
- "Implementing agent MUST do one of: (a) strip `Remote-*` headers from the tailnet path; (b) accept and document the expanded trust model; (c) abandon trusted-header SSO and require users to log in twice (Authelia, then OpenWebUI native)."

---

## G. Cohesion / numbering fixes

Section references inside the plan are drifted. The implementing agent will hit confused cross-refs and trust the wrong section. Fix in a single pass:

| Currently says | Should say | Locations |
|---|---|---|
| "§9 CrowdSec" | "§10 CrowdSec" | §3 matrix row 2, 6, 10; §4 v2 scope intro |
| "see section 8 Cloudflare Tunnel" | "see section 9 Cloudflare Tunnel" | §2 decision row "Inbound exposure mode" |
| "(§8)" Cloudflare Tunnel | "(§9)" | §4 v1 optional deployment variant |
| "§7 Step 2 Caddyfile" | "§8 Step 2 Caddyfile" | §3 matrix rows 1, 4, 6, 10 — Step 2 is under §8 "v1 implementation steps" |
| "§7 Step 3 Authelia" | "§8 Step 3 Authelia" | §3 matrix rows 2, 3 |
| "§9 v2 for the custom Caddy build" | "§10.4 for the custom Caddy build" | §8 Step 2 Caddyfile comment block |
| "§5.5 Windows firewall" | "§6.5 Windows firewall" | §3 matrix row 8 |
| "§11 update policy" | "§12 update policy" | §3 matrix row 8 |
| "§11 secrets-management posture" | "§12.5 secrets-management posture" | §3 matrix row 11 |
| "§12 for Linux-host migration" | "§13 for Linux-host migration" | §3 matrix row 8 |
| "§9 validation" CrowdSec | "§10.8 validation" | §3 matrix row 1 |

Plus: §2 has "Cloudflare Tunnel — see section 8" but the actual section is 9. Several similar drifts. A clean renumber after the audit changes are in is the cheapest fix.

---

## H. Smaller findings (in order of priority)

1. **HSTS preload irreversibility.** `max-age=63072000; includeSubDomains; preload` with the `preload` keyword is HSTS-preload-list-eligible. **Once submitted to the list, the domain cannot be removed for years.** Document this in §7 Step 2 and recommend **not** submitting to the preload list until the deployment is proven stable for 90+ days. The header alone (without list submission) is fine.

2. **CSP stripped from OpenWebUI responses (`header_down -Content-Security-Policy`).** The plan removes the upstream's CSP without setting a replacement on the OpenWebUI route. This is a defense-in-depth regression. Either preserve the upstream CSP (drop the `header_down` line) or set a Caddy-level CSP appropriate for OpenWebUI's resource needs. Document why this strip exists or remove it.

3. **DNS CAA records.** Plan does not mention CAA. Add to the pre-deploy checklist: `dig CAA ${PUBLIC_DOMAIN}` should return `0 issue "letsencrypt.org"` (and `0 issuewild ";"` if no wildcards). Prevents an attacker who gets a registrar-side foothold from issuing certs through a different CA.

4. **Argon2 parallelism vs CPU count.** §7 Step 3 sets `parallelism: 4`. On a 2-core host this is fine but undersized; on a 16-core host it's wasteful. Tune to `≤ floor(cores/2)`. Document.

5. **Password reset disabled + only one WebAuthn key = bricking risk.** §7 Step 3 disables password reset. If the admin loses their hardware key and forgets the password, the only recovery path is manually editing `users_database.yml` with a shell — requires host access. Add to §6 a pre-flight that mandates **two enrolled WebAuthn keys** and a **printed TOTP secret stored offline** before going live.

6. **Backups stored on same host.** §12.2 acknowledges this but the recommendation is verbal only. Add a concrete item to the v1 checklist: "Verify `./backups/` is synced to ≥1 off-host destination (S3, Backblaze B2, NAS, USB drive) on a daily cadence." Without offsite, a ransomware event takes the backups too.

7. **`pull_policy: always` on `surrealdb` and `open_notebook`.** Pre-existing in [docker-compose.yml:420, 447](../../../docker-compose.yml#L420). Combined with `restart: always`, an unattended restart can pick up a breaking upstream image. Pin tags (`surrealdb:v2.0.4`, `open_notebook:v1.2.3` — check actual versions) and change to `pull_policy: missing`. This is touching Open Notebook, so file it separately per §F, but note the risk.

8. **`server.endpoints.authz.auth-request.implementation: AuthRequest`** in Authelia. Correct choice for Caddy `forward_auth`. Make sure to also enable `ForwardAuth` if any backend uses the Traefik convention — not relevant here, but worth a note.

9. **`session.same_site: lax`.** Acceptable for browser flows that include subdomains (`auth.${PUBLIC_DOMAIN}` ↔ `${PUBLIC_DOMAIN}`). If `strict` is feasible, prefer it. Test the WebAuthn enrollment flow under `strict` before committing.

10. **Cloudflare Tunnel sees decrypted traffic.** §9 mentions this. Strengthen the language: "Cloudflare can read every chat message, every uploaded document, every API call. For a self-hosted AI portal this is the central privacy trade-off — the user must decide explicitly." Right now it's a one-liner; should be a callout box.

11. **Volume backup integrity.** §11 verifies "a `.tar.gz` exists." Add: "extract one backup to a scratch dir; verify the SQLite DB opens and `SELECT COUNT(*) FROM webauthn_credentials;` returns a non-zero number." Backups that exist but are corrupt are worse than no backups.

12. **`identity_validation.reset_password` is defined despite `password_reset.disable: true`.** Harmless but contradictory. Either remove the `identity_validation.reset_password` block or set `password_reset.disable: false` and connect a notifier. As written it implies a feature that doesn't work.

---

## I. Recommended next steps

In merge order:

1. **Fix B.1, B.3** (header sanitization + tailnet posture decision). Cannot ship without this.
2. **Fix B.2, B.4** (Cloudflare Tunnel trusted-proxies + cron env passing).
3. **Apply C.1–C.6** (Docker network segmentation, read-only FS, non-root UIDs, missing `cap_drop` on cloudflared).
4. **Add D.1, D.2, D.3** (Authelia watcher sidecar, Caddy access log, config tripwire). Pick a notifier (ntfy/Pushover/Gotify); add the URL to `.env`.
5. **Write E.1 and E.2** (`breach-killswitch.ps1` and `incident-response.md`).
6. **Add F sub-section 6.7** to the plan explicitly calling out the OpenWebUI auth-model change.
7. **Renumber per §G**.
8. **Walk the H list** and patch each item.
9. Re-run plan §11 validation checklist against the patched plan.

Estimated effort: 1–2 focused days for items 1–4, half a day for 5, an hour each for 6–8.

---

## J. What I confirmed is fine

- Tailscale container, `entrypoint.sh`, and openwebui's network-namespace sharing are not modified by this plan. **PASS.**
- `llm-net` stays `internal: true`. **PASS.**
- The two-paths-no-overlap architecture (tailnet via tailscale, internet via Caddy/Authelia) is sound.
- Authelia choice over Authentik/Keycloak is correct for a single-user home stack.
- TLS posture (1.3 preferred, 1.2 floor, AEAD ciphers, x25519/secp384r1/secp256r1 curves) is correct.
- HSTS header value is correct (note H.1 about preload list submission).
- Argon2id with 64 MiB memory + 3 iterations exceeds OWASP minimums.
- Body-size limits (`100MB` for uploads, `1–2MB` for auth/hub) match real-world OpenWebUI/Open Notebook upload patterns.
- v2 CrowdSec scope is sensible; should NOT be bundled into v1.
- Pre-flight §6.1–§6.4 fixes are necessary regardless of this plan and should land first.
- The "stop and ask the user" gates on data-destructive operations are correctly placed.
- The Definition of Done in §15 is rigorous.

---

## K. Cross-references

- Plan: [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md)
- Research: [open-source0authentication-front-end-research.md](../open-source0authentication-front-end-research.md)
- Threat model: [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md)
- Active compose: [docker-compose.yml](../../../docker-compose.yml)
- Tailscale entrypoint (unchanged): [entrypoint.sh](../../../entrypoint.sh)
- OpenWebUI trusted-header docs: https://docs.openwebui.com/getting-started/env-configuration/#webui_auth_trusted_email_header
- Caddy `forward_auth` security note: https://caddyserver.com/docs/caddyfile/directives/forward_auth#security-considerations
- Authelia regulation reference: https://www.authelia.com/configuration/security/regulation/
- ntfy.sh for the watcher notifier: https://ntfy.sh/
- OWASP password storage cheat sheet (Argon2 params): https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
