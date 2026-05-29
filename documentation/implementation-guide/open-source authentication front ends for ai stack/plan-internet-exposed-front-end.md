# Internet-Exposed Front-End — Implementation Plan

**Status:** Ready for implementation (post-audit revision 2026-05-14)
**Target stack:** [docker-compose.yml](../../../docker-compose.yml) on Windows + Docker Desktop
**Companion docs:**
- [open-source0authentication-front-end-research.md](../open-source0authentication-front-end-research.md) — option evaluation
- [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md) — threat model and hardening checklist
- [audit-plan-internet-exposed-front-end.md](./audit-plan-internet-exposed-front-end.md) — security audit driving this revision
- [integration-task-document.md](./integration-task-document.md) — step-by-step task list for the implementing agent (companion to this plan)
- `documentation/incident-response.md` — IR playbook for a portal breach (v1 deliverable; written by the implementing agent, see §7.1 and §13.6)

---

## 1. Goal

Expose **OpenWebUI** and **Open Notebook** to the public internet behind a single authentication portal, running in parallel to the existing Tailscale path. Tailnet access continues to work unchanged.

Two access paths to the same backend containers, no overlap:

| Path | Edge | Auth | DNS |
|---|---|---|---|
| **Internet** (new) | Caddy via Cloudflare Tunnel (this plan) | Authelia at the edge + **each service's native login behind it** | `${PUBLIC_DOMAIN}` (managed in Cloudflare) |
| **Tailnet** (existing) | [tailscale](../../../docker-compose.yml#L77-L120) container, `tailscale serve` in [entrypoint.sh](../../../entrypoint.sh) | Each service's native login | `*.tail-xxxxx.ts.net` |

The Tailscale container, its serve configuration, and its network namespace sharing with OpenWebUI are **never modified**. OpenWebUI and Open Notebook are **never modified beyond pre-flight exposure fixes in §6** — no trusted-header SSO, no auth-mode env changes. Internet users complete a second login at OpenWebUI / Open Notebook after passing Authelia. This is the deliberate trade-off (see §2 row "OpenWebUI / Open Notebook auth handoff") that closes the header-forgery vector documented in [audit §B.1](./audit-plan-internet-exposed-front-end.md#b1--remote-trusted-header-forgery-via-the-tailnet-path-critical).

---

## 2. Architecture decisions (locked)

| Decision | Choice | Reason |
|---|---|---|
| Reverse proxy | **Caddy 2.x** | Apache 2.0, automatic Let's Encrypt, clean config syntax, runs cleanly as a container alongside Tailscale |
| Auth gateway | **Authelia 4.39 (pinned tag)** | Apache 2.0, forward-auth model, TOTP + WebAuthn, Argon2id, mature |
| Primary MFA factor | **WebAuthn / FIDO2 hardware key (preferred), TOTP fallback** | Hardware keys defeat phishing entirely; TOTP only for cases without a key |
| Session store | **Filesystem (v1)** | Single-user, single-node — Redis is unnecessary complexity for v1 |
| Navigation pattern | **Pattern B: launcher hub** | Simpler than iframe shell, no response-body manipulation, doesn't fight OpenWebUI/Streamlit SPA behaviors |
| Default landing | **Hub with auto-redirect to OpenWebUI after 3s** | Honors "default = OpenWebUI" without injecting chrome into service pages |
| TLS policy | **TLS 1.3 preferred, 1.2 minimum, strong ciphers only** | Explicit pinning in Caddyfile — never rely on defaults staying secure |
| Request body limits | **Per-route caps** | OpenWebUI/Open Notebook need ~100 MB for uploads; hub/auth need ~1 MB. Prevents memory-exhaustion DoS. |
| `X-Forwarded-For` posture | **Drop-then-set at edge; trust only `CF-Connecting-IP` as the real client** | Never trust client-supplied `X-Forwarded-For`. Under Cloudflare Tunnel the immediate remote is `cloudflared`; Cloudflare sets `CF-Connecting-IP` to the real client. Caddy's `trusted_proxies` honors only the tunnel and rewrites XFF from `CF-Connecting-IP`. |
| `Remote-*` header posture | **Strip on every inbound request** | `Remote-User/Email/Name/Groups` are populated by Authelia via `forward_auth` only. Any client-supplied value is dropped before any handler runs. Defense in depth even though SSO is not enabled. |
| IP-level dynamic banning | **Deferred to v2 (CrowdSec + AppSec)** | v1 covers account lockout + static IP rules + Caddy rate-limit. v2 adds per-IP auto-ban and WAF. |
| **OpenWebUI / Open Notebook auth handoff** | **None — users authenticate twice.** Authelia at the internet edge, then OpenWebUI's native login (Open Notebook has none, so Authelia is its only auth). **Trusted-header SSO is explicitly NOT enabled.** | Closes the tailnet header-forgery vector ([audit §B.1](./audit-plan-internet-exposed-front-end.md#b1--remote-trusted-header-forgery-via-the-tailnet-path-critical)). Cost: one extra password prompt on OpenWebUI for internet users. Honors the user's "OpenWebUI must remain otherwise untouched" requirement literally. |
| Inbound exposure mode | **Cloudflare Tunnel (mandatory)** | Hides home IP, absorbs DDoS, removes router port-forwarding, removes the Windows Defender inbound-rule surface. Trade-off: Cloudflare can read decrypted traffic (see §9 callout). Port-forward mode was removed from the plan during the post-audit revision; baseline route is no longer offered. |
| Internal Docker network layout | **Three networks: `edge-net`, `auth-net` (`internal: true`), `app-net`** | A Caddy compromise no longer grants direct L3 to all backends. Authelia is on an `internal: true` network — no internet egress from the auth service. ([audit §C.1](./audit-plan-internet-exposed-front-end.md#c1--network-segmentation-inside-docker)) |
| Container hardening floor | **Read-only root FS + non-root UID + `cap_drop: ALL` + `pids_limit` + tmpfs `/tmp`** on every new container | Standard Docker boundary hardening. Caddy adds `NET_BIND_SERVICE` for 80/443 binding inside the container; everything else drops all caps. |
| Breach detection (v1) | **`authelia-watcher` sidecar → Pushover** | Tails `authelia.log` + Caddy access log + config-file hashes; pushes alerts on regulation bans, new-IP logins, credential enrollments, config drift, repeated failures. Pushover chosen for reliability and acknowledgment support. ([audit §D](./audit-plan-internet-exposed-front-end.md#d-breach-detection--v1-must-not-defer-this-to-v2)) |
| Breach response (v1) | **`scripts/breach-killswitch.ps1` + `documentation/incident-response.md`** | Killswitch stops only the internet-exposed path (caddy + cloudflared + authelia), snapshots logs, rotates secrets. Tailnet path stays alive so admin retains access during incident. ([audit §E](./audit-plan-internet-exposed-front-end.md#e-incident-response--what-happens-when-a-breach-is-detected)) |
| Open Notebook auth | **Authelia-only at the edge; no native auth behind it** | Open Notebook has no native auth — Authelia is mandatory. On the tailnet, the pre-existing trust model is unchanged ([§6.6](#66-tailnet-trust-posture-statement-open-notebook)). |
| Watchtower | **Disabled for all new containers** | Match the existing pattern at [docker-compose.yml:114, 238, 273, 308](../../../docker-compose.yml#L114). Pin tags, update manually. |
| Backups | **`caddy-data` + `authelia-data` covered in v1; integrity-verified** | Loss of `authelia-data` invalidates TOTP enrollments; loss of `caddy-data` triggers ACME re-issue (rate-limited). Validation extracts one backup and runs `SELECT COUNT(*)` to prove it isn't a corrupt tarball ([audit §H.11](./audit-plan-internet-exposed-front-end.md#h-smaller-findings-in-order-of-priority)). |

---

## 3. Security considerations coverage matrix

Maps each top-level item in [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md) to where it lands in this plan. Status: **v1** = baked into this PR; **v2** = next PR (CrowdSec); **Tier 2** = out of scope, see §13; **N/A** = handled by another layer.

| # | Consideration | Status | Where addressed |
|---|---|---|---|
| 1 | TLS / cipher hardening | v1 | §8 Step 2 Caddyfile `tls` block; §11 validation; §12.3 CAA |
| 2 | Authentication hardening (MFA, lockout, rate-limit) | v1 (+v2 enhancements) | §8 Step 3 Authelia; v2 §10 CrowdSec |
| 3 | Session management | v1 | §8 Step 3 Authelia `session:` block |
| 4 | Reverse proxy hardening | v1 | §8 Step 2 Caddyfile (`Remote-*` strip, `X-Forwarded-*` rewrite via `trusted_proxies`, body limits, path whitelist) |
| 5 | Network segmentation | **v1 partial (Docker networks), Tier 2 (VLAN/DMZ)** | §8 Step 1 `edge-net` / `auth-net` (`internal: true`) / `app-net`; §13.1 for full VLAN/DMZ |
| 6 | Brute force / DDoS mitigation | v1 partial, v2 full | §8 Caddyfile rate-limit (commented in v1) + §9 Cloudflare Tunnel (mandatory) + §10 CrowdSec |
| 7 | Identity provider selection | v1 | §2 decision: Authelia |
| 8 | Host & OS hardening | v1 partial | §6.5 Windows firewall + §12.1 update policy; §13 for Linux-host migration discussion |
| 9 | Logging, monitoring, IR | **v1 (detection + IR baked in)** | §8 Step 8 `authelia-watcher` + `integrity-tripwire` sidecars; §12.4 log retention; §12.6 killswitch ops; deliverable `documentation/incident-response.md` (§7.1, §13.6) |
| 10 | Application security (OWASP) | v1 partial, v2 WAF | §8 Step 2 Caddyfile CSP + input limits + `Remote-*` sanitization; §10 v2 CrowdSec AppSec |
| 11 | Architecture & ops (zero-trust, secrets) | v1 partial | §6 pre-flight secrets to `.env`; §8 Step 1 read-only FS / non-root UID / `cap_drop: ALL` / `pids_limit`; §12.5 secrets-management posture |

---

## 4. Scope

### v1 — this plan, ship as one PR
- Pre-flight fixes (§6)
- Caddy + Authelia + `cloudflared` containers in [docker-compose.yml](../../../docker-compose.yml)
- Three new Docker networks: `edge-net`, `auth-net` (`internal: true`), `app-net` (§8 Step 1)
- Hardened Caddyfile (TLS pinning, CSP, body limits, `X-Forwarded-For` rewrite from `CF-Connecting-IP`, `Remote-*` header strip, JSON access log, rate-limit hooks commented for v2)
- Authelia config (single user, WebAuthn-preferred + TOTP fallback, Argon2id, regulation, `access_control.networks`, no password reset)
- Launcher hub static page (with explicit "you will be prompted to sign in to each service" copy, since SSO is not enabled)
- `.env.example` patch (Cloudflare Tunnel token, Pushover keys, Authelia secrets, pre-flight secrets)
- Backup containers for `caddy-data` and `authelia-data` (cron env-passing fix from [audit §B.4](./audit-plan-internet-exposed-front-end.md#b4--backup-containers-do-not-run-their-secrets-through-the-cron-environment))
- **Breach detection (new in v1):** `authelia-watcher` sidecar tailing Authelia + Caddy logs → Pushover; `integrity-tripwire` sidecar hashing config files
- **Incident response (new in v1):** `scripts/breach-killswitch.ps1` + `documentation/incident-response.md` deliverables (drafted by implementing agent; outline in §7.1, §13.6)

### Out of scope (do NOT include in v1 or v2)
- Trusted-header SSO into OpenWebUI (explicitly removed during audit revision — see §2 and [audit §B.1](./audit-plan-internet-exposed-front-end.md#b1--remote-trusted-header-forgery-via-the-tailnet-path-critical))
- Port-forward (router 80/443) deployment mode — removed during audit revision; Cloudflare Tunnel is the only mode
- Replacing Tailscale
- Exposing llama-cpp / llama-cpp-embed / mnemory / smolcrawl / open-terminal / surrealdb (these stay internal)
- Multi-user provisioning UI (file-based users DB is fine for v1)
- Email/SMTP notifier (use `filesystem` notifier; document upgrade path)
- OIDC federation to external IdPs

### v2 — separate follow-up PR (§10)
- CrowdSec + caddy-bouncer (per-IP dynamic ban)
- CrowdSec AppSec (WAF, OWASP CRS equivalent)
- Geo-blocking via CrowdSec scenarios
- Log shipping from Authelia + Caddy to CrowdSec
- Migration of v1's `authelia-watcher` notification pipeline to CrowdSec profiles where applicable

### Tier 2 hardening — documented as gaps in §13 (no implementation)
- VLAN / DMZ network segmentation (requires pfSense/OPNsense + multi-NIC). v1 Docker-network segmentation is partial mitigation.
- Centralized log aggregation (Grafana Loki / ELK)
- File integrity monitoring at host level (AIDE/OSSEC). v1's `integrity-tripwire` covers only the auth-portal config files.
- Hardware secrets management (Vault, SOPS)
- HSM-backed TLS keys
- OpenWebUI / Open Notebook / SurrealDB hardening (tmpfs, pin tags away from `pull_policy: always`). Deferred to a separate PR per the "untouched" requirement on those services.

---

## 5. Final architecture

```
Internet
  │
  ▼  (NO inbound host ports — Cloudflare Tunnel is the only ingress)
┌─────────────────────────────────────────────────────────────┐
│ Cloudflare edge (Cloudflare Tunnel + DNS)                   │
│  - DDoS absorption, bot challenge, IP hiding                │
│  - Outbound-only tunnel from cloudflared container          │
│  - Sets CF-Connecting-IP = real client IP                   │
└────────────────────────┬────────────────────────────────────┘
                         │ outbound-initiated tunnel
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ cloudflared (new)             [network: edge-net]           │
│  - no host port bindings, no inbound listeners              │
│  - cap_drop: ALL, no-new-privileges, non-root UID           │
└────────────────────────┬────────────────────────────────────┘
                         │ http://caddy:80 over edge-net
                         ▼
┌─────────────────────────────────────────────────────────────┐
│ caddy (new)                   [networks: edge, auth, app]   │
│  - TLS 1.3 / 1.2 floor, AEAD ciphers                        │
│  - HSTS (no preload submission), CSP, CORP, XFO             │
│  - trusted_proxies = cloudflared CIDR → rewrites XFF        │
│    from CF-Connecting-IP                                    │
│  - STRIPS inbound Remote-User/Email/Name/Groups headers     │
│  - request_body limits per route                            │
│  - forward_auth → authelia (Authelia issues session)        │
│  - logs JSON access log to /data/caddy-access.log           │
│  - read_only root FS, tmpfs /tmp, cap_drop ALL +            │
│    NET_BIND_SERVICE, non-root UID, pids_limit               │
└────┬──────────────────────────────────────┬─────────────────┘
     │ forward_auth (auth-net)              │ reverse_proxy (app-net)
     ▼                                      │
┌──────────────────────────────────┐        │
│ authelia (new)  [auth-net only]  │        │
│  - network is internal: true     │        │
│    (no internet egress)          │        │
│  - WebAuthn + TOTP, Argon2id     │        │
│  - regulation (per-username)     │        │
│  - access_control on real IP     │        │
│    via Caddy XFF                 │        │
│  - JSON log to /data/authelia.log│        │
└──────────────────────────────────┘        │
                                            ▼
       ┌────────────────┬────────────────┬─────────────────┐
       ▼                ▼                ▼                 ▼
   /  (hub)      /openwebui/*    /notebook/*        /api/notebook/*
   static HTML   openwebui:8080  open_notebook      open_notebook
   from caddy    (existing —     :8502 (existing    :5055 (existing
                 user logs in    — Authelia is      — Authelia is
                 again with      the only auth)     the only auth)
                 OpenWebUI's
                 native form)

       [Detection sidecars — all on auth-net, internal: true]
       ┌───────────────────────────────┐  ┌─────────────────────────────┐
       │ authelia-watcher (new)        │  │ integrity-tripwire (new)    │
       │  tails authelia.log,          │  │  nightly checksum of        │
       │  caddy-access.log;            │  │  Caddyfile, configuration.  │
       │  POSTs Pushover on ban /      │  │  yml, users_database.yml;   │
       │  new-IP / credential change / │  │  POSTs Pushover on drift    │
       │  config reload                │  │                             │
       └───────────────────────────────┘  └─────────────────────────────┘

Tailscale path (unchanged — completely separate ingress):
  tailnet  →  tailscale container (shared netns with openwebui)
            →  tailscale serve  →  127.0.0.1 ports inside openwebui ns
            →  openwebui native auth / open_notebook unauth (per existing trust)
```

**Docker network topology:**
- `edge-net` (bridge): `cloudflared`, `caddy`. Only path between cloudflared and caddy.
- `auth-net` (bridge, `internal: true`): `caddy`, `authelia`, `authelia-watcher`, `integrity-tripwire`. **No internet egress.** Authelia cannot reach SMTP, NTP, ACME, or external WebAuthn attestation servers — fine because notifier is `filesystem`, time comes from the host, ACME runs in Caddy on `edge-net`.
- `app-net` (bridge): `caddy`, `openwebui`, `open_notebook`. Backends reachable only from Caddy.
- `llm-net` (bridge, `internal: true` — existing): unchanged, never exposed to Caddy.
- The existing `default` bridge keeps everything else. `caddy` is **not** placed on `default`.

If `caddy` is compromised, the attacker has L3 reach to Authelia and the two backends — but **not** to llama-cpp/mnemory/surrealdb, and Authelia's container cannot dial out to the internet to exfiltrate.

---

## 6. Pre-flight fixes (REQUIRED — block v1 until done)

These are pre-existing exposure issues. They must land before Caddy opens ports 80/443.

### 6.1 Bind `open_notebook` to localhost
[docker-compose.yml:428-430](../../../docker-compose.yml#L428-L430)
```yaml
    ports:
      - "127.0.0.1:8503:8502"  # was: "8503:8502"
      - "127.0.0.1:5055:5055"  # was: "5055:5055"
```

### 6.2 Bind `surrealdb` to localhost
[docker-compose.yml:411-413](../../../docker-compose.yml#L411-L413)
```yaml
    ports:
      - "127.0.0.1:8003:8000"  # was: "8003:8000"
```

### 6.3 Move SurrealDB credentials to `.env`
[docker-compose.yml:411, 439-440](../../../docker-compose.yml#L411)
- Add to `.env`: `SURREAL_USER=<new>`, `SURREAL_PASSWORD=<strong random>`
- Replace hardcoded `root` / `root` in compose `command:` and `SURREAL_PASSWORD` env with `${SURREAL_USER}` / `${SURREAL_PASSWORD}`
- **Stop and ask the user** before destroying `D:\Open WebUI\open-notebook\surreal_data` to migrate. Offer migration via `surreal sql` or accept data loss.

### 6.4 Move Open Notebook encryption key to `.env`
[docker-compose.yml:434](../../../docker-compose.yml#L434)
- Add to `.env`: `OPEN_NOTEBOOK_ENCRYPTION_KEY=<random 32+ char>`
- **Stop and ask the user** before changing the key value — it invalidates already-stored encrypted API keys. If keeping existing data, move the *existing* value to `.env` verbatim.

### 6.5 Windows Defender Firewall posture (host)
Because v1 uses Cloudflare Tunnel only (no inbound port-forward), the firewall posture is **default-deny inbound on the Public profile**. The portal does **not** require any inbound exception. The host's outbound path to Cloudflare must remain open.

| Direction | Rule | State |
|---|---|---|
| Inbound | All ports from internet (Public profile) | **Block (default)** |
| Inbound | TCP 3389 (RDP) from internet | **Block** (use Tailscale for admin access) |
| Inbound | SMB / NetBIOS / WinRM from internet | **Block**, LAN-only |
| Outbound | TCP 7844 → Cloudflare (cloudflared QUIC/HTTP/2 tunnel) | **Allow** — required for the tunnel to dial out |
| Outbound | TCP 443 → Cloudflare API / Let's Encrypt | **Allow** — required for ACME (Caddy) and tunnel control plane |

Verify with:
```powershell
Get-NetFirewallProfile | Select Name, Enabled, DefaultInboundAction
# Expect: DefaultInboundAction = Block on Public profile
```

If the user ever reverts to port-forward mode (not in this plan's scope), the inbound 80/443/UDP-443 exceptions must be re-added at that time.

### 6.6 Tailnet trust posture statement (Open Notebook)
**Open Notebook has no native authentication.** The existing tailnet path at `tailscale serve --https=8443 -> open_notebook:8502` ([entrypoint.sh:438-442](../../../entrypoint.sh#L438-L442)) means **anyone on the tailnet can reach Open Notebook unauthenticated.**

This plan does **not** change that. The implicit posture is: *tailnet members are trusted equivalently to a single household user.* Confirm this is acceptable before merge. If not, options:
- **Option A** (smallest change): remove the Open Notebook tailnet serve from [entrypoint.sh](../../../entrypoint.sh) and require all Open Notebook access to go through the Authelia front-end (tailnet users included).
- **Option B** (most isolation): keep both paths but add Tailscale ACL rules to restrict which tailnet identities can hit port 8443/5055 on the openwebui node.
- **Option C** (accept as-is): document the trust assumption in `SECURITY.md` and move on.

Default: **Option C** unless the user objects.

### 6.7 OpenWebUI authentication is NOT changed by this plan
Earlier drafts of this plan added `WEBUI_AUTH_TRUSTED_EMAIL_HEADER` / `WEBUI_AUTH_TRUSTED_NAME_HEADER` to OpenWebUI to enable SSO from Authelia. The audit found that this creates a header-forgery vector on the tailnet path: anyone on the tailnet could send `Remote-Email: admin@example.com` directly to `openwebui:8080` and silently become any user ([audit §B.1](./audit-plan-internet-exposed-front-end.md#b1--remote-trusted-header-forgery-via-the-tailnet-path-critical)).

**Decision (locked):** trusted-header SSO is **not** enabled. OpenWebUI's environment is unchanged from its current state in [docker-compose.yml:18-37](../../../docker-compose.yml#L18-L37). Internet users complete two logins:

1. Authelia at the edge (gates whether the request reaches `openwebui:8080` at all)
2. OpenWebUI's native login form (validates the user against OpenWebUI's own user database)

UX cost: one extra password prompt for internet users on each new browser/session. Tailnet users see no change.

Caddy still strips inbound `Remote-*` headers as defense-in-depth (§8 Step 2 `sanitize_proxy_headers`), so even if someone re-enables trusted-header SSO in a future change without thinking, the header-forgery vector via the internet path is blocked by the proxy. The tailnet path remains a documented trust assumption (§6.6).

### 6.8 Pre-deployment WebAuthn / TOTP enrollment safety
Authelia's `password_reset.disable: true` means losing all 2FA enrollments + forgetting the password = no path back in without editing `users_database.yml` directly on the host. Before opening the portal to the internet:

- Enroll **at least two WebAuthn credentials** for the admin user (e.g., a primary hardware key + a backup hardware key stored in a different location)
- **Or** print the TOTP recovery secret and store it offline (paper, locked box) so it can re-seed an authenticator app
- Test recovery from the secondary credential **before** trusting it

This is a v1 pre-deploy checklist item (§11) but called out here so it isn't missed.

---

## 7. v1 file changes

### 7.1 New files
```
config/
  caddy/
    Caddyfile
    site/
      index.html                  # the launcher hub (with double-login UX copy)
      hub.css                     # minimal styling
  authelia/
    configuration.yml
    users_database.yml            # gitignored after template
    .gitignore
  watcher/
    authelia-watch.sh             # Pushover sidecar — tails authelia.log + caddy-access.log
    known-ips.txt                 # seed file of known source IPs (one per line; empty initially)
  tripwire/
    integrity-tripwire.sh         # nightly hash check on Caddyfile / configuration.yml / users_database.yml
    baseline.sha256               # generated on first run by the script
backup/
  caddy-backup.sh
  authelia-backup.sh
scripts/
  breach-killswitch.ps1           # emergency stop for internet-exposed path only
documentation/
  incident-response.md            # v1 IR playbook (outline in §13.6; full content written by implementing agent)
```

### 7.2 Modified files
```
docker-compose.yml                # add cloudflared, caddy, authelia, watcher, tripwire, backups + 3 new networks + named volumes
.env.example                      # add Cloudflare Tunnel token, Pushover keys, Authelia secrets, pre-flight secrets
.gitignore                        # ensure config/authelia/users_database.yml + config/tripwire/baseline.sha256 are ignored
```

### 7.3 Volumes added to [docker-compose.yml](../../../docker-compose.yml)
```yaml
volumes:
  caddy-data:        # ACME certs (if any — under Cloudflare Tunnel, Cloudflare terminates TLS), access logs, OCSP staples
  caddy-config:      # Caddy runtime config
  authelia-data:     # Authelia notifications + sqlite storage + JSON authelia.log
  tripwire-data:     # baseline.sha256 + state file for the integrity tripwire sidecar
```

### 7.4 Networks added to [docker-compose.yml](../../../docker-compose.yml)
```yaml
networks:
  edge-net:
    driver: bridge        # cloudflared + caddy
  auth-net:
    driver: bridge
    internal: true        # caddy + authelia + watcher + tripwire; NO internet egress
  app-net:
    driver: bridge        # caddy + openwebui + open_notebook
  # llm-net stays as-is (internal: true)
  # default stays as-is (other services unchanged)
```

`openwebui` and `open_notebook` gain `app-net` as an **additional** network attachment (do not remove their existing networks). This is the only change to those services' compose entries and is required for Caddy to reach them; their environment, volumes, and ports remain unchanged.

---

## 8. v1 implementation steps

### Step 1 — Add new services, networks, and volumes to [docker-compose.yml](../../../docker-compose.yml)

The only modification to existing services is **attaching `openwebui` and `open_notebook` to `app-net`** (§7.4). All else is additive. Every new container honors the hardening floor from §2:

- `read_only: true` with `tmpfs` for any writable scratch
- non-root `user:` (verify the chosen UID has read access to bind mounts on Windows + Docker Desktop)
- `security_opt: [no-new-privileges:true]`
- `cap_drop: [ALL]` (Caddy re-adds `NET_BIND_SERVICE`; nothing else)
- `pids_limit: 100`
- explicit `cpus`/`memory` deploy limits
- `labels: ["com.centurylinklabs.watchtower.enable=false"]`

Append to the `networks:` block:
```yaml
networks:
  edge-net:
    driver: bridge
  auth-net:
    driver: bridge
    internal: true        # no internet egress for authelia + watcher + tripwire
  app-net:
    driver: bridge
  # llm-net and default keep their current definitions
```

Append two new attachments to existing services (this is the **only** change to OpenWebUI and Open Notebook compose entries — purely additive, no env/volume/port changes):
```yaml
  openwebui:
    networks:
      - default     # existing
      - llm-net     # existing
      - app-net     # NEW — required for caddy to reach openwebui:8080

  open_notebook:
    networks:
      - default     # existing
      - llm-net     # existing
      - app-net     # NEW — required for caddy to reach :8502 and :5055
```

Append to the `services:` block:

```yaml
  cloudflared:
    image: cloudflare/cloudflared:2024.10.0       # pin, do not use :latest in prod
    container_name: cloudflared
    networks:
      - edge-net
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=${CLOUDFLARE_TUNNEL_TOKEN}
    user: "65532:65532"     # nonroot UID from distroless convention; verify
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=16m
    pids_limit: 100
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: 128M
    depends_on:
      caddy:
        condition: service_healthy
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  caddy:
    image: caddy:2.8.4-alpine                     # pin patch; v2 uses custom build (§10.4)
    container_name: caddy
    networks:
      - edge-net          # cloudflared → caddy
      - auth-net          # caddy → authelia (forward_auth)
      - app-net           # caddy → openwebui + open_notebook
    # NO host port bindings — Cloudflare Tunnel is the only ingress
    volumes:
      - ./config/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - ./config/caddy/site:/srv/site:ro
      - caddy-data:/data
      - caddy-config:/config
    environment:
      - PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
      - ACME_EMAIL=${ACME_EMAIL}
    user: "10000:10000"
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    cap_add:
      - NET_BIND_SERVICE   # binds :80 inside the container; not exposed on host
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=64m
    pids_limit: 200
    deploy:
      resources:
        limits:
          cpus: '1.5'
          memory: 512M
    depends_on:
      authelia:
        condition: service_started
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:80/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  authelia:
    image: authelia/authelia:4.39
    container_name: authelia
    networks:
      - auth-net          # internal: true — no internet egress
    volumes:
      - ./config/authelia:/config:ro
      - authelia-data:/data
    environment:
      - TZ=UTC
      - PUBLIC_DOMAIN=${PUBLIC_DOMAIN}
      - AUTHELIA_JWT_SECRET=${AUTHELIA_JWT_SECRET}
      - AUTHELIA_SESSION_SECRET=${AUTHELIA_SESSION_SECRET}
      - AUTHELIA_STORAGE_ENCRYPTION_KEY=${AUTHELIA_STORAGE_ENCRYPTION_KEY}
    user: "10001:10001"
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=32m
    pids_limit: 100
    deploy:
      resources:
        limits:
          cpus: '1.0'
          memory: 256M
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
    healthcheck:
      test: ["CMD", "wget", "-q", "-O", "/dev/null", "http://127.0.0.1:9091/api/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 30s

  authelia-watcher:
    image: alpine:3.21
    container_name: authelia-watcher
    networks:
      - auth-net          # internal: true — relies on Pushover via... see note below
    volumes:
      - authelia-data:/logs/authelia:ro
      - caddy-data:/logs/caddy:ro
      - ./config/watcher/authelia-watch.sh:/scripts/watch.sh:ro
      - ./config/watcher/known-ips.txt:/data/known-ips.txt
    environment:
      - PUSHOVER_USER_KEY=${PUSHOVER_USER_KEY}
      - PUSHOVER_API_TOKEN=${PUSHOVER_API_TOKEN}
      - ALERT_BASE_URL=${PUBLIC_DOMAIN}
    user: "10002:10002"
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    read_only: true
    tmpfs:
      - /tmp:noexec,nosuid,size=16m
    pids_limit: 50
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 64M
    depends_on:
      authelia:
        condition: service_started
    entrypoint: /bin/sh
    command:
      - -c
      - |
        apk add --no-cache curl jq inotify-tools >/dev/null 2>&1
        chmod +x /scripts/watch.sh
        exec /scripts/watch.sh
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  integrity-tripwire:
    image: alpine:3.21
    container_name: integrity-tripwire
    networks:
      - auth-net          # internal: true
    volumes:
      - ./config/caddy/Caddyfile:/watch/Caddyfile:ro
      - ./config/authelia/configuration.yml:/watch/configuration.yml:ro
      - ./config/authelia/users_database.yml:/watch/users_database.yml:ro
      - ./config/tripwire/integrity-tripwire.sh:/scripts/tripwire.sh:ro
      - tripwire-data:/state
    environment:
      - PUSHOVER_USER_KEY=${PUSHOVER_USER_KEY}
      - PUSHOVER_API_TOKEN=${PUSHOVER_API_TOKEN}
      - CHECK_CRON=${TRIPWIRE_CRON:-0 4 * * *}
    user: "10003:10003"
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 50
    deploy:
      resources:
        limits:
          cpus: '0.25'
          memory: 64M
    entrypoint: /bin/sh
    command:
      - -c
      - |
        apk add --no-cache curl >/dev/null 2>&1
        chmod +x /scripts/tripwire.sh
        # Run once at startup to establish or verify baseline
        /scripts/tripwire.sh init || true
        # Then schedule via crond; export env into the cron environment
        printenv | grep -E '^(PUSHOVER_|CHECK_)' > /etc/profile.d/tripwire-env.sh
        echo "$${CHECK_CRON} . /etc/profile.d/tripwire-env.sh; /scripts/tripwire.sh check >> /var/log/tripwire.log 2>&1" > /etc/crontabs/root
        crond -f -l 2
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  caddy-backup:
    image: alpine:3.21
    container_name: caddy-backup
    volumes:
      - caddy-data:/data:ro
      - ./backups/caddy:/backups
      - ./backup/caddy-backup.sh:/scripts/backup.sh:ro
    environment:
      - BACKUP_DIR=/backups
      - DATA_DIR=/data
      - RETAIN_DAYS=${CADDY_BACKUP_RETAIN_DAYS:-7}
      - BACKUP_CRON=${CADDY_BACKUP_CRON:-0 3 * * *}
    user: "10004:10004"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 50
    entrypoint: /bin/sh
    command:
      - -c
      - |
        chmod +x /scripts/backup.sh
        # Export compose env into cron's environment (audit §B.4 fix)
        printenv | grep -E '^(BACKUP_|DATA_|RETAIN_)' > /etc/profile.d/backup-env.sh
        echo "$${BACKUP_CRON} . /etc/profile.d/backup-env.sh; sh /scripts/backup.sh >> /var/log/backup.log 2>&1" > /etc/crontabs/root
        echo "[$(date -u +%FT%TZ)] Caddy backup scheduler started"
        crond -f -l 2
    restart: unless-stopped
    depends_on:
      caddy:
        condition: service_healthy
    labels:
      - "com.centurylinklabs.watchtower.enable=false"

  authelia-backup:
    image: alpine:3.21
    container_name: authelia-backup
    volumes:
      - authelia-data:/data:ro
      - ./backups/authelia:/backups
      - ./backup/authelia-backup.sh:/scripts/backup.sh:ro
    environment:
      - BACKUP_DIR=/backups
      - DATA_DIR=/data
      - RETAIN_DAYS=${AUTHELIA_BACKUP_RETAIN_DAYS:-14}
      - BACKUP_CRON=${AUTHELIA_BACKUP_CRON:-0 3 * * *}
    user: "10005:10005"
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 50
    entrypoint: /bin/sh
    command:
      - -c
      - |
        chmod +x /scripts/backup.sh
        printenv | grep -E '^(BACKUP_|DATA_|RETAIN_)' > /etc/profile.d/backup-env.sh
        echo "$${BACKUP_CRON} . /etc/profile.d/backup-env.sh; sh /scripts/backup.sh >> /var/log/backup.log 2>&1" > /etc/crontabs/root
        echo "[$(date -u +%FT%TZ)] Authelia backup scheduler started"
        crond -f -l 2
    restart: unless-stopped
    depends_on:
      authelia:
        condition: service_started
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

**On `authelia-watcher` and the `internal: true` network for Pushover:** Pushover requires an HTTPS POST to `api.pushover.net`. A network with `internal: true` blocks that. Two options — the implementing agent must pick one and apply:

1. **Move `authelia-watcher` to a separate `notify-net` (bridge, NOT internal)** dedicated to outbound notifications. The watcher keeps `auth-net` as well so it can read the log volumes via shared bind mounts. Pushover is the only egress allowed.
2. **Keep `auth-net` internal** and proxy outbound Pushover calls through Caddy (`reverse_proxy` to `api.pushover.net`). Adds Caddy as a notification bottleneck — if Caddy is the compromise, alerts about that compromise can't be delivered. Not recommended.

**Preferred:** option 1. The plan's §8 Step 1 example above assumes option 1; add the second network attachment:

```yaml
  authelia-watcher:
    networks:
      - auth-net
      - notify-net      # add this; notify-net is a new non-internal bridge for Pushover
```

And:
```yaml
networks:
  notify-net:
    driver: bridge      # NOT internal: true
```

Same applies to `integrity-tripwire` (it also needs to POST to Pushover).

Then append to the `volumes:` block:
```yaml
  caddy-data:
  caddy-config:
  authelia-data:
  tripwire-data:
```

### Step 2 — Create `config/caddy/Caddyfile`

This Caddyfile is written for Cloudflare Tunnel ingress: Caddy listens on `:80` inside its container only (no host bind), and `cloudflared` reaches it over `edge-net`. TLS terminates at Cloudflare; Caddy speaks HTTP on the origin side.

```caddy
{
    email {$ACME_EMAIL}
    # Under Cloudflare Tunnel, ACME is unnecessary if Cloudflare provides the
    # public cert. Leave email present so a future revert to direct exposure
    # works without further config changes.
    # acme_ca https://acme-staging-v02.api.letsencrypt.org/directory  # uncomment for staging

    servers {
        # Trust cloudflared's container IP range for X-Forwarded-* and
        # CF-Connecting-IP. Without this, every client appears to come from
        # cloudflared's IP and the access_control / regulation rules misfire
        # ([audit §B.2]).
        # Adjust the CIDR to your edge-net subnet — `docker network inspect
        # edge-net` will show the actual range. Typical Docker default is
        # 172.16.0.0/12.
        trusted_proxies static 172.16.0.0/12 10.0.0.0/8 192.168.0.0/16
        client_ip_headers CF-Connecting-IP X-Forwarded-For
        protocols h1 h2
        strict_sni_host on
    }
}

# ─── Snippets ─────────────────────────────────────────────────────────────

# TLS policy — only meaningful if Caddy ever serves TLS directly. Under
# Cloudflare Tunnel, Cloudflare handles TLS at the edge; this snippet is kept
# so reverting to direct exposure is one config change away.
(tls_strong) {
    tls {
        protocols tls1.2 tls1.3
        ciphers TLS_AES_256_GCM_SHA384 TLS_CHACHA20_POLY1305_SHA256 TLS_AES_128_GCM_SHA256 TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384 TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384 TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305 TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305
        curves x25519 secp384r1 secp256r1
    }
}

# Security response headers shared by every site
(security_headers) {
    header {
        # HSTS — 1 year, includeSubDomains. NO `preload` keyword: HSTS preload
        # list submission is irreversible for years ([audit §H.1]). Enable
        # preload by hand only after 90+ days of stable operation.
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options    "nosniff"
        X-Frame-Options           "DENY"
        Referrer-Policy           "strict-origin-when-cross-origin"
        Permissions-Policy        "geolocation=(), microphone=(), camera=(), usb=()"
        Cross-Origin-Opener-Policy   "same-origin"
        Cross-Origin-Resource-Policy "same-site"
        # Strip server / backend identification
        -Server
        -X-Powered-By
    }
}

# Sanitize incoming proxy headers.
# - Strip client-supplied X-Forwarded-* (CF-Connecting-IP is the source of truth
#   via the global trusted_proxies block; Caddy will rewrite X-Forwarded-For
#   from it before passing upstream).
# - Strip client-supplied Remote-* — Authelia is the ONLY source. Even though
#   trusted-header SSO is not enabled (§6.7), this stays as defense in depth.
(sanitize_proxy_headers) {
    request_header -X-Forwarded-Host
    request_header -X-Real-IP
    request_header -Remote-User
    request_header -Remote-Groups
    request_header -Remote-Name
    request_header -Remote-Email
    # X-Forwarded-For and X-Forwarded-Proto are set by Caddy itself from
    # trusted_proxies — do NOT strip them here.
}

# Access log to a rolling JSON file in /data
(access_log) {
    log {
        output file /data/caddy-access.log {
            roll_size 100MiB
            roll_keep 7
            roll_keep_for 720h     # 30d
        }
        format json
        level INFO
    }
}

# ─── Authelia portal ──────────────────────────────────────────────────────
http://auth.{$PUBLIC_DOMAIN} {
    import security_headers
    import sanitize_proxy_headers
    import access_log
    encode zstd gzip

    request_body {
        max_size 1MB
    }

    reverse_proxy authelia:9091
}

# ─── Main app domain ──────────────────────────────────────────────────────
http://{$PUBLIC_DOMAIN} {
    import security_headers
    import sanitize_proxy_headers
    import access_log
    encode zstd gzip

    # Health endpoint — restricted to container-local sources only so it does
    # not leak liveness to internet scanners ([audit §C.8]).
    @internal_health {
        path /healthz
        remote_ip 127.0.0.1/32 172.16.0.0/12
    }
    handle @internal_health {
        respond "ok" 200
    }
    # Public hits to /healthz that don't match the source-IP rule get 404.
    handle /healthz {
        respond 404
    }

    # Per-route request body caps
    @uploads path /openwebui/* /notebook/* /api/notebook/*
    request_body @uploads {
        max_size 100MB
    }
    request_body {
        max_size 2MB
    }

    # Edge rate-limit on Authelia auth endpoints.
    # Requires the caddy-ratelimit module — see §10.4 v2 for the custom Caddy
    # build. Without it, Authelia regulation is the only rate-limit until v2.
    # @auth_post {
    #     method POST
    #     path /api/firstfactor /api/secondfactor/*
    # }
    # rate_limit @auth_post {
    #     zone auth_login
    #     key  {client_ip}        # client_ip is set from CF-Connecting-IP
    #     events 5
    #     window 1m
    # }

    # Forward-auth: every request below this point flows through Authelia.
    # NOTE: copy_headers omitted intentionally — trusted-header SSO is NOT
    # enabled (§6.7). Authelia only returns 200/401 here; we do not propagate
    # Remote-* upstream.
    forward_auth authelia:9091 {
        uri /api/verify?rd=http://auth.{$PUBLIC_DOMAIN}/
    }

    # ─── Routes ──────────────────────────────────────────────────────────

    # OpenWebUI
    # The upstream's CSP / XFO are preserved — do NOT strip them here.
    # Removing them was a defense-in-depth regression flagged in [audit §H.2].
    handle_path /openwebui/* {
        reverse_proxy openwebui:8080 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
        }
    }

    # Open Notebook UI
    # NOTE: If Streamlit subpath proves brittle, fall back to subdomain
    # (notebook.{$PUBLIC_DOMAIN}) — see "Subpath caveat" below.
    handle_path /notebook/* {
        reverse_proxy open_notebook:8502 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
        }
    }

    # Open Notebook API
    handle_path /api/notebook/* {
        reverse_proxy open_notebook:5055 {
            header_up Host {upstream_hostport}
            header_up X-Forwarded-Proto https
        }
    }

    # Hub — static landing page
    handle {
        header Content-Security-Policy "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        root * /srv/site
        templates
        try_files {path} /index.html
        file_server
    }
}

# Default-deny stub for any other Host header reaching this Caddy
:80 {
    respond 421
}
```

**Why HTTP-only origin under Cloudflare Tunnel:** Cloudflare terminates TLS at its edge and re-encrypts to the tunnel; the tunnel hop from Cloudflare to `cloudflared` is TLS. `cloudflared` → `caddy` is HTTP-only over `edge-net` (private bridge inside the Docker host). The `tls_strong` snippet stays in the file so reverting to direct exposure later is a one-site-block change.

**Subpath caveat (unchanged):**
If subpath proves brittle for Open Notebook (Streamlit) or OpenWebUI:
- **Fallback A:** subdomains `app.{$PUBLIC_DOMAIN}` and `notebook.{$PUBLIC_DOMAIN}`. Add public hostnames in Cloudflare Tunnel; each subdomain routes to a separate Caddy site block.
- **Fallback B:** set `--server.baseUrlPath=/notebook` on the `open_notebook` container. **Caution:** this is a behavioral change to Open Notebook and conflicts with the "untouched" rule. Discuss with the user before applying — option A is preferred.

### Step 3 — Create `config/authelia/configuration.yml`

Under Cloudflare Tunnel, Authelia receives requests through Caddy; Caddy sets `X-Forwarded-For` from `CF-Connecting-IP` (Caddyfile `client_ip_headers`). Authelia's `access_control.networks` therefore evaluates against real client IPs, not `cloudflared`'s container IP.

```yaml
---
theme: dark
default_2fa_method: webauthn   # hardware key preferred over TOTP
server:
  address: tcp://0.0.0.0:9091
  endpoints:
    authz:
      auth-request:
        implementation: AuthRequest

log:
  level: info
  format: json                  # structured logs for the authelia-watcher sidecar
  file_path: /data/authelia.log
  keep_stdout: true

telemetry:
  metrics:
    enabled: false              # turn on if §13.2 centralized logging lands

totp:
  issuer: AI Stack
  algorithm: sha1
  digits: 6
  period: 30

webauthn:
  display_name: AI Stack
  attestation_conveyance_preference: indirect
  user_verification: preferred

# Password reset is disabled (no SMTP) — but the identity_validation block is
# omitted entirely to avoid the contradiction flagged in [audit §H.12]. With
# password_reset.disable: true, the reset_password JWT settings are unused;
# keeping them in config implied a feature that doesn't work.

authentication_backend:
  password_reset:
    disable: true               # admin resets manually; see §12.6
  refresh_interval: 5m
  file:
    path: /config/users_database.yml
    watch: true
    password:
      algorithm: argon2
      argon2:
        variant: argon2id
        iterations: 3
        memory: 65536       # 64 MiB
        parallelism: 4      # tune to ≤ floor(cpu_cores/2); see [audit §H.4]
        key_length: 32
        salt_length: 16

password_policy:
  standard:
    enabled: true
    min_length: 14
    max_length: 128
    require_uppercase: true
    require_lowercase: true
    require_number: true
    require_special: true
  zxcvbn:
    enabled: false

# IP-aware access control. Source IPs here are the real client (Caddy sets
# X-Forwarded-For from CF-Connecting-IP via client_ip_headers).
access_control:
  default_policy: deny
  networks:
    - name: home_lan
      networks:
        - 192.168.0.0/16
        - 10.0.0.0/8
        # 172.16.0.0/12 intentionally NOT included here: that range is used
        # by Docker bridges. If we whitelisted it, anyone reaching Authelia
        # via a Docker network would be classified as LAN.
  rules:
    # LAN: one factor is enough
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      policy: one_factor
      networks: ['home_lan']
    # Internet: enforce 2FA
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      policy: two_factor
    # Authelia portal itself: bypass (it does its own auth)
    - domain: 'auth.{{ env "PUBLIC_DOMAIN" }}'
      policy: bypass

session:
  name: authelia_session
  same_site: lax              # tested under WebAuthn cross-subdomain flow
  inactivity: 30m
  expiration: 8h
  remember_me: 1M
  cookies:
    - domain: '{{ env "PUBLIC_DOMAIN" }}'
      authelia_url: 'https://auth.{{ env "PUBLIC_DOMAIN" }}'
      default_redirection_url: 'https://{{ env "PUBLIC_DOMAIN" }}/'

# Account-level lockout (per-username). Per-IP banning is v2 CrowdSec.
# Find-time / ban-time tuned so a single sustained attacker locks the
# account quickly but a typo doesn't lock for hours.
regulation:
  max_retries: 3
  find_time: 2m
  ban_time: 1h

storage:
  encryption_key: "{{ env \"AUTHELIA_STORAGE_ENCRYPTION_KEY\" }}"
  local:
    path: /data/db.sqlite3

notifier:
  disable_startup_check: false
  filesystem:
    filename: /data/notification.txt
  # v2 / production: swap for SMTP, see https://www.authelia.com/configuration/notifications/smtp/
  # Note: SMTP requires Authelia to dial out. auth-net is internal: true —
  # adding SMTP means either moving Authelia to a non-internal network or
  # routing through a notify-net like authelia-watcher.
```

### Step 4 — Create `config/authelia/users_database.yml`

Template only — file is **gitignored**:

```yaml
---
users:
  yourusername:
    disabled: false
    displayname: "Your Name"
    # Generate with:
    #   docker run --rm authelia/authelia:4.39 \
    #     authelia crypto hash generate argon2 --password <strong-password>
    # Requirements: 14+ chars, upper, lower, number, special.
    # Verify the password is NOT in haveibeenpwned.com before using:
    #   https://haveibeenpwned.com/Passwords
    password: "$argon2id$v=19$m=65536,t=3,p=4$..."
    email: yourusername@example.com
    groups:
      - admins
```

`config/authelia/.gitignore`:
```
users_database.yml
```

Append to top-level [.gitignore](../../../.gitignore):
```
/config/authelia/users_database.yml
```

### Step 5 — Launcher hub

The hub copy is updated to set expectations for the double-login flow (§6.7): after the user authenticates via Authelia and lands on the hub, each service still presents its own native login.

`config/caddy/site/index.html`:
```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Stack</title>
  <link rel="stylesheet" href="/hub.css">
  <meta http-equiv="refresh" content="3; url=/openwebui/">
</head>
<body>
  <header class="hub-header">
    <h1>AI Stack</h1>
    <p class="hint">Redirecting to Open WebUI in 3 seconds &mdash; or pick a service below.<br>
       You will be prompted to sign in once more at the service you choose.</p>
  </header>
  <main class="hub-grid">
    <a class="hub-card primary" href="/openwebui/">
      <h2>Open WebUI</h2>
      <p>Chat, models, and tools &mdash; sign in with your OpenWebUI account.</p>
    </a>
    <a class="hub-card" href="/notebook/">
      <h2>Open Notebook</h2>
      <p>Documents and notebooks &mdash; protected by the Authelia gate only.</p>
    </a>
  </main>
  <footer class="hub-footer">
    <a href="https://auth.{{ env "PUBLIC_DOMAIN" }}/logout">Sign out of Authelia</a>
  </footer>
  <script>
    document.querySelectorAll('.hub-card').forEach(c => {
      c.addEventListener('mouseenter', () => {
        const meta = document.querySelector('meta[http-equiv="refresh"]');
        if (meta) meta.remove();
      });
    });
  </script>
</body>
</html>
```

The `{{ env "PUBLIC_DOMAIN" }}` token is substituted by Caddy's `templates` directive in the hub `handle` block. CSP allows `'self'` for scripts so the inline `<script>` works — if you tighten CSP further to disallow inline scripts, move the JS to `/hub.js` and adjust `script-src` accordingly.

`config/caddy/site/hub.css` — unchanged from prior plan; use the version already drafted.

### Step 6 — Sidecar scripts (specification for implementing agent)

This plan **does not include the script bodies** — the implementing agent writes them as part of v1 deliverables (see [integration-task-document.md](./integration-task-document.md)). The specifications below pin behavior the scripts must implement.

**`config/watcher/authelia-watch.sh` (Pushover sidecar):**

| Trigger | Source | Pushover priority |
|---|---|---|
| `authentication.failed` repeats × ≥ 5 within 5 min from same IP | `/logs/authelia/authelia.log` JSON `time` + `remote_ip` | 1 (high) |
| `authentication.success` from an IP not in `/data/known-ips.txt` | same | 1 (high) |
| `regulation` ban applied | same | 0 (normal) |
| WebAuthn / TOTP credential added or removed | same | 1 (high) |
| Authelia config reload (`config_file_loaded` event) | same | 0 (normal) |
| Caddy access log shows ≥ 10 × 401 from same IP within 1 min | `/logs/caddy/caddy-access.log` JSON | 0 (normal) |

Each alert message includes: timestamp (UTC), event type, username (if known), source IP, the relevant log line trimmed to 200 chars. The script reads `PUSHOVER_USER_KEY` and `PUSHOVER_API_TOKEN` from env and POSTs to `https://api.pushover.net/1/messages.json`. The script must use `inotifywait` (from `inotify-tools`) for log rotation safety, not naïve `tail -F`.

**`config/tripwire/integrity-tripwire.sh`:**

| Mode | Behavior |
|---|---|
| `init` | If `/state/baseline.sha256` doesn't exist, compute SHA-256 of each `/watch/*` file and write to baseline. On every subsequent `init` (container start), verify current hashes against baseline; alert via Pushover on mismatch. |
| `check` (cron) | Verify current hashes against baseline; alert via Pushover on mismatch. Do NOT auto-update the baseline. Updating requires manual ack: an operator must `docker exec integrity-tripwire /scripts/tripwire.sh accept` to re-baseline after a deliberate config change. |
| `accept` | Re-compute and overwrite `/state/baseline.sha256`. Used after intentional changes to Caddyfile, configuration.yml, or users_database.yml. |

The "accept" mode is intentionally manual so any config change that bypasses the operator's awareness fires an alert.

**`scripts/breach-killswitch.ps1`:**

Single PowerShell script the operator runs from the Windows host (or via Tailscale-reachable RDP) when the watcher / tripwire fires an alert deemed credible. Behaviors required:

1. Stop the internet-exposed path only: `docker stop cloudflared caddy authelia authelia-watcher integrity-tripwire`
2. Snapshot logs to `./incident/<UTC-timestamp>/`:
   - `docker cp caddy:/data/caddy-access.log ./incident/<ts>/`
   - `docker cp authelia:/data/authelia.log ./incident/<ts>/`
3. Rotate `AUTHELIA_JWT_SECRET` and `AUTHELIA_SESSION_SECRET` in `.env` (preserve old values commented out for the IR record)
4. Print recovery steps (referencing `documentation/incident-response.md`) to the console:
   - "Tailnet path is still up — reach OpenWebUI via your tailnet host"
   - "Restore Authelia from yesterday's backup before restarting (today's may be poisoned)"
   - "Re-enroll WebAuthn keys via `https://auth.<your-domain>/settings/two-factor` after recovery"
5. **Do not** automatically restart anything. Bringing the portal back online is a human decision.

The script must NOT touch: `tailscale`, `openwebui`, `open_notebook`, `surrealdb`, `llama-cpp`, `llama-cpp-embed`, `mnemory`, `smolcrawl`, `open-terminal`, or any backup container. Tailnet access must survive the killswitch.

**`documentation/incident-response.md`:** see §13.6 for the required outline. The implementing agent writes the full document; this plan reviews it before merge.

### Step 7 — Backup scripts

`backup/caddy-backup.sh`:
```sh
#!/bin/sh
set -eu
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/caddy-${TS}.tar.gz"
cd "${DATA_DIR}"
tar czf "${OUT}" .
# Integrity sentinel — also computes a hash so a corrupted tarball is detectable
sha256sum "${OUT}" > "${OUT}.sha256"
find "${BACKUP_DIR}" -name 'caddy-*.tar.gz' -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -name 'caddy-*.tar.gz.sha256' -mtime "+${RETAIN_DAYS}" -delete
echo "[$(date -u +%FT%TZ)] Backed up to ${OUT}"
```

`backup/authelia-backup.sh`:
```sh
#!/bin/sh
set -eu
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="${BACKUP_DIR}/authelia-${TS}.tar.gz"
cd "${DATA_DIR}"
tar czf "${OUT}" .
sha256sum "${OUT}" > "${OUT}.sha256"
find "${BACKUP_DIR}" -name 'authelia-*.tar.gz' -mtime "+${RETAIN_DAYS}" -delete
find "${BACKUP_DIR}" -name 'authelia-*.tar.gz.sha256' -mtime "+${RETAIN_DAYS}" -delete
echo "[$(date -u +%FT%TZ)] Backed up to ${OUT}"
```

Both scripts mirror the pattern of [backup/mnemory-backup.sh](../../../backup/mnemory-backup.sh) (existing). Compose env-passing is handled in §8 Step 1 via the `printenv | grep | /etc/profile.d/...` shim, fixing [audit §B.4](./audit-plan-internet-exposed-front-end.md#b4--backup-containers-do-not-run-their-secrets-through-the-cron-environment).

### Step 8 — `.env.example` patch

Append to `.env.example`:
```bash
# === Internet-exposed front-end ===
PUBLIC_DOMAIN=ai.example.com
ACME_EMAIL=you@example.com

# Cloudflare Tunnel — required (port-forward mode removed from this plan)
# Create the tunnel in Cloudflare Zero Trust dashboard, paste token here.
CLOUDFLARE_TUNNEL_TOKEN=

# Authelia secrets — generate each with:
#   docker run --rm authelia/authelia:4.39 authelia crypto rand --length 64
AUTHELIA_JWT_SECRET=
AUTHELIA_SESSION_SECRET=
AUTHELIA_STORAGE_ENCRYPTION_KEY=

# Pushover (breach detection — required for v1)
# Sign up at https://pushover.net; create an Application to get the API token.
PUSHOVER_USER_KEY=
PUSHOVER_API_TOKEN=

# Integrity tripwire schedule (cron in UTC)
TRIPWIRE_CRON=0 4 * * *

# SurrealDB credentials (pre-flight 6.3)
SURREAL_USER=
SURREAL_PASSWORD=

# Open Notebook DB encryption key (pre-flight 6.4)
OPEN_NOTEBOOK_ENCRYPTION_KEY=

# Backup retention
CADDY_BACKUP_RETAIN_DAYS=7
CADDY_BACKUP_CRON=0 3 * * *
AUTHELIA_BACKUP_RETAIN_DAYS=14
AUTHELIA_BACKUP_CRON=0 3 * * *
```

Set `.env` file permissions on Windows: right-click → Properties → Security → remove inherited permissions, allow only the current user. Verify with:
```powershell
icacls .env
```

---

## 9. Cloudflare Tunnel configuration (mandatory)

Cloudflare Tunnel is the **only** ingress mode for this plan. Port-forward mode was removed during the audit revision because it leaves the home IP exposed and removes Cloudflare's DDoS/bot buffer.

### 9.1 Critical privacy callout — do not skip

> **Cloudflare sees every byte of decrypted traffic that flows through the tunnel.** That includes every chat message in OpenWebUI, every document uploaded to Open Notebook, every API call, every cookie. Cloudflare's privacy policy and terms of service apply. For a self-hosted AI portal this is the central trade-off — confirm the user has chosen Cloudflare with this in mind, not just because the plan recommends it.

If this is unacceptable, the alternatives are:
- **Stay tailnet-only** (cancel the front-end entirely; reach services via tailscale serve as today)
- **Self-host the edge** (port-forward 80/443 with a static IP or DDNS, accept home-IP exposure, lose DDoS protection). This requires reverting the plan's audit-driven decisions about `trusted_proxies`, port bindings, and the Windows firewall — not in scope for v1.

### 9.2 Tunnel container
Defined in §8 Step 1. The `cloudflared` block there uses:
- A pinned image tag (not `:latest`), per [audit §H.7](./audit-plan-internet-exposed-front-end.md#h-smaller-findings-in-order-of-priority).
- `cap_drop: ALL`, `no-new-privileges: true`, read-only root FS with `tmpfs /tmp`, non-root user, `pids_limit: 100`. ([audit §C.5](./audit-plan-internet-exposed-front-end.md#c5--cap_drop-all-missing-from-cloudflared-example))
- Attachment only to `edge-net` — `cloudflared` cannot reach `auth-net` or `app-net` directly; it can only reach `caddy`.

### 9.3 Tunnel configuration (one-time, in Cloudflare Zero Trust dashboard)
1. Move DNS for `${PUBLIC_DOMAIN}` to Cloudflare (full nameserver migration). Verify with `dig NS ${PUBLIC_DOMAIN}` returning Cloudflare nameservers.
2. Add a **CAA record** before issuing any certs: `${PUBLIC_DOMAIN}. CAA 0 issue "letsencrypt.org"` and `${PUBLIC_DOMAIN}. CAA 0 issue "pki.goog"` (Cloudflare uses Google for many edge certs). Verify with `dig CAA ${PUBLIC_DOMAIN}`. ([audit §H.3](./audit-plan-internet-exposed-front-end.md#h-smaller-findings-in-order-of-priority))
3. Create a tunnel named `ai-stack`. Copy the token into `.env` as `CLOUDFLARE_TUNNEL_TOKEN`. Treat the token as a **secret on par with the Authelia JWT secret** — anyone with it can impersonate the tunnel.
4. Add two public hostnames in the tunnel:
   - `${PUBLIC_DOMAIN}` → service `http://caddy:80`
   - `auth.${PUBLIC_DOMAIN}` → service `http://caddy:80`
5. Enable in the Cloudflare zone:
   - SSL/TLS mode: **Full (strict)** — Cloudflare terminates TLS at the edge; the tunnel hop is TLS. The Caddy origin is HTTP-only over `edge-net` (a private Docker bridge), which is acceptable because that bridge has no internet path.
   - Minimum TLS version: **TLS 1.2** (set at the Cloudflare edge, mirrors Caddy's floor)
   - Always Use HTTPS: **on**
   - HSTS at the Cloudflare edge: **off** (we set HSTS in Caddy; layering it at CF complicates rollback)
6. (Strongly recommended) Cloudflare Access policy in front of the tunnel: limit access by country, by managed-list IPs, or by adding a Cloudflare-side Access app gate before traffic ever reaches Authelia. This is **additive** defense — Authelia still gates the application even if Access is bypassed.

### 9.4 Verify the tunnel sees real client IPs at Caddy
After bringing the stack up, hit the portal from a phone on cellular data (a known external IP). In Caddy's access log (`./backups/caddy/...` or `docker exec caddy cat /data/caddy-access.log`), confirm:
- `request.headers.Cf-Connecting-Ip` matches your phone's real IP
- `request.headers.X-Forwarded-For` (which Caddy rewrote) matches the same

If both show `cloudflared`'s internal Docker IP instead, the Caddyfile `trusted_proxies` CIDR is wrong — adjust to match the actual `edge-net` subnet (find with `docker network inspect edge-net`).

### 9.5 Failure-mode considerations
- **Cloudflare outage:** the portal is unreachable until Cloudflare recovers. Tailnet path is unaffected. Document this in `documentation/incident-response.md` as a non-breach incident path.
- **Token leak:** rotate the tunnel token in the Cloudflare dashboard, paste new value into `.env`, `docker compose up -d cloudflared`. Old tunnel disconnects within minutes.
- **DNS migration failure (Step 1):** if Cloudflare nameservers don't fully propagate, the portal is partially reachable. Verify with `dig +short ${PUBLIC_DOMAIN}` from multiple resolvers.

---

## 10. v2 — CrowdSec follow-up (separate PR)

### 10.1 What CrowdSec adds beyond v1

| Function | v1 (Authelia alone) | v2 (with CrowdSec) |
|---|---|---|
| Account lockout after N failures (per-username) | Yes | Yes (unchanged) |
| Static IP allow/deny | Yes (`access_control.networks`) | Yes (unchanged) |
| Caddy edge rate-limit on `/api/firstfactor` | Optional (custom Caddy build) | Yes (uncommented + CrowdSec faster) |
| **Per-IP dynamic ban after N failures** | **No** | **Yes** — CrowdSec parses logs, caddy-bouncer enforces |
| Community threat-intel feed (pre-block known-bad IPs) | No | Yes (opt-in, free) |
| **Web Application Firewall (CrowdSec AppSec)** | **No** | **Yes** — OWASP CRS-equivalent at Caddy |
| Geo-blocking by country | Manual (CIDR lists) | Native scenarios |
| Centralized decisions visible via `cscli` | No | Yes |

### 10.2 Files added in v2

```
config/
  crowdsec/
    acquis.yaml              # log sources
    profiles.yaml            # decision profiles
    appsec-configs/          # AppSec rule packs
  caddy/
    Caddyfile                # ← MODIFIED to load the bouncer + ratelimit plugins
Dockerfile.caddy             # ← NEW: custom Caddy build with xcaddy
```

### 10.3 Containers added in v2

```yaml
  crowdsec:
    image: crowdsecurity/crowdsec:v1.6.4   # pin a specific version
    container_name: crowdsec
    networks:
      - auth-net           # log volumes are on auth-net via caddy / authelia
      - notify-net         # outbound to Central API for community feed
    volumes:
      - ./config/crowdsec/acquis.yaml:/etc/crowdsec/acquis.yaml:ro
      - ./config/crowdsec/profiles.yaml:/etc/crowdsec/profiles.yaml:ro
      - crowdsec-data:/var/lib/crowdsec/data
      - crowdsec-config:/etc/crowdsec
      - authelia-data:/logs/authelia:ro
      - caddy-data:/logs/caddy:ro
    environment:
      - TZ=UTC
      - COLLECTIONS=crowdsecurity/caddy crowdsecurity/authelia crowdsecurity/appsec-virtual-patching crowdsecurity/appsec-generic-rules
    user: "10006:10006"
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    pids_limit: 200
    labels:
      - "com.centurylinklabs.watchtower.enable=false"
```

### 10.4 Custom Caddy build (`Dockerfile.caddy`)

```dockerfile
FROM caddy:2.8.4-builder AS builder
# Pin module versions to specific commit shas in production. Listed as @latest
# here for readability — replace with @<sha> before merging the v2 PR.
RUN xcaddy build \
      --with github.com/hslatman/caddy-crowdsec-bouncer/http@<pinned-sha> \
      --with github.com/mholt/caddy-ratelimit@<pinned-sha>

FROM caddy:2.8.4-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
```

Replace `image: caddy:2.8.4-alpine` on the `caddy` service with:
```yaml
    build:
      context: .
      dockerfile: Dockerfile.caddy
```

### 10.5 Caddyfile changes in v2
- Uncomment the `rate_limit` block from §8 Step 2.
- Add `order crowdsec first` and the `crowdsec` global block.
- Insert `crowdsec` directive at the top of each site block.
- Enable `appsec` if using AppSec virtual patching.

(Full Caddyfile delta lives in the v2 PR.)

### 10.6 Bouncer registration

After `crowdsec` is up:
```powershell
docker exec crowdsec cscli bouncers add caddy
```
Capture the key into `.env` as `CROWDSEC_BOUNCER_KEY=...`, restart Caddy.

### 10.7 Suggested scenarios

```powershell
docker exec crowdsec cscli collections install crowdsecurity/authelia
docker exec crowdsec cscli collections install crowdsecurity/caddy
docker exec crowdsec cscli collections install crowdsecurity/http-cve
docker exec crowdsec cscli collections install crowdsecurity/base-http-scenarios
```

### 10.8 Relationship to v1 detection
- The v1 `authelia-watcher` sidecar (§8 Step 1) overlaps with CrowdSec's Authelia parser. In v2, the watcher can be **retired** once CrowdSec's notification profile is wired to Pushover via the `http` notification plugin — or kept as a redundant alert path. Decide at v2 design time.
- The v1 `integrity-tripwire` is **complementary** and remains in v2: CrowdSec does not monitor config-file integrity.

### 10.9 v2 validation
- Synthetic brute-force from a non-LAN IP (phone hotspot or external VM):
  ```bash
  for i in $(seq 1 10); do curl -s -X POST https://auth.${PUBLIC_DOMAIN}/api/firstfactor \
    -H 'Content-Type: application/json' \
    -d '{"username":"nope","password":"nope","targetURL":"https://${PUBLIC_DOMAIN}/"}'; done
  ```
- `docker exec crowdsec cscli decisions list` shows the IP banned.
- Same IP retrying gets Caddy 403 (NOT Authelia 401).
- AppSec test: send an obvious SQLi probe (`?q=' OR 1=1--`) and confirm CrowdSec AppSec blocks it.
- LAN IPs whitelisted in `access_control.networks.home_lan` still pass.

---

## 11. v1 validation checklist

### Pre-deploy
- [ ] `.env` has all new vars populated (random 64-char strings for Authelia secrets; Pushover keys; Cloudflare Tunnel token)
- [ ] `.env` file permissions locked to current user (`icacls .env`)
- [ ] `users_database.yml` has at least one user with an Argon2id-hashed password ≥14 chars
- [ ] Password verified NOT in haveibeenpwned.com (https://haveibeenpwned.com/Passwords)
- [ ] **Two WebAuthn credentials enrolled** for the admin user OR TOTP recovery secret printed and stored offline (§6.8)
- [ ] `PUBLIC_DOMAIN` is in a Cloudflare-managed zone; nameservers verified with `dig NS ${PUBLIC_DOMAIN}`
- [ ] CAA records published (§9.3 step 2)
- [ ] Cloudflare Tunnel created in dashboard; token in `.env`; public hostnames `${PUBLIC_DOMAIN}` and `auth.${PUBLIC_DOMAIN}` both point to `http://caddy:80`
- [ ] Pushover application created; user-key and API token in `.env`; test message verified received
- [ ] Pre-flight §6.1–§6.8 all complete
- [ ] Windows Defender Firewall: Public profile default-deny inbound (§6.5)

### Deploy
- [ ] `docker compose pull && docker compose up -d authelia caddy cloudflared authelia-watcher integrity-tripwire caddy-backup authelia-backup`
- [ ] `docker logs caddy --tail 100` shows no errors, listening on `:80`
- [ ] `docker logs authelia --tail 100` shows "Authelia is listening on tcp://0.0.0.0:9091"
- [ ] `docker logs cloudflared --tail 100` shows "Connection registered" against Cloudflare edge
- [ ] `docker logs authelia-watcher --tail 100` shows "Watching authelia.log and caddy-access.log"
- [ ] `docker logs integrity-tripwire --tail 100` shows "Baseline established" (first run) or "Baseline verified" (later runs)
- [ ] Backup containers running (`docker ps | grep backup`)

### Functional
- [ ] `https://auth.${PUBLIC_DOMAIN}/` loads Authelia login screen (Cloudflare cert at the edge)
- [ ] `https://${PUBLIC_DOMAIN}/` redirects to Authelia → after login, hub appears
- [ ] Hub auto-redirects to OpenWebUI after 3 s
- [ ] Hovering a hub card cancels the auto-redirect
- [ ] **OpenWebUI loads at `https://${PUBLIC_DOMAIN}/openwebui/` and presents its OWN native login form (this is the expected double-login UX per §6.7)**
- [ ] Open Notebook loads at `https://${PUBLIC_DOMAIN}/notebook/` — Streamlit assets load, sidebar works
- [ ] Open Notebook API calls succeed (browser devtools → network → no failed XHRs)
- [ ] **Tailnet path unaffected:** `https://<tailnet-host>.ts.net/` still loads OpenWebUI's native login directly (no Authelia in the way)
- [ ] **Tailnet Open Notebook unaffected:** `https://<tailnet-host>.ts.net:8443/` still serves Open Notebook without Authelia (pre-existing trust per §6.6)
- [ ] Authelia logout (`https://auth.${PUBLIC_DOMAIN}/logout`) invalidates the session — next request to `${PUBLIC_DOMAIN}/openwebui/` re-prompts at the Authelia gate

### Security smoke tests
- [ ] **TLS at the Cloudflare edge:** `curl -v https://${PUBLIC_DOMAIN}/ 2>&1 | grep "SSL connection"` shows TLSv1.3 (or 1.2)
- [ ] **TLS floor:** `curl --tls-max 1.1 https://${PUBLIC_DOMAIN}/` fails with handshake error (rejection happens at Cloudflare)
- [ ] **SSL Labs:** scan at `https://www.ssllabs.com/ssltest/analyze.html?d=${PUBLIC_DOMAIN}` → grade A or A+
- [ ] **Headers:** `curl -sI https://${PUBLIC_DOMAIN}/` includes:
  - `Strict-Transport-Security: max-age=31536000; includeSubDomains` (NO `preload` keyword — §H.1)
  - `X-Content-Type-Options: nosniff`
  - `X-Frame-Options: DENY`
  - `Referrer-Policy: strict-origin-when-cross-origin`
  - NO `Server:` header from origin (Cloudflare may add its own)
- [ ] **HTTP→HTTPS:** `curl -I http://${PUBLIC_DOMAIN}/` returns 301/308 (handled by Cloudflare)
- [ ] **CSP:** `curl -sI https://${PUBLIC_DOMAIN}/` includes a `Content-Security-Policy` header on the hub route
- [ ] **Account lockout:** 4 failed logins in 2 min lock the account for 1 hour (verify `docker logs authelia | grep regulation`)
- [ ] **`Remote-*` strip:** send a request with `Remote-Email: admin@example.com`; verify Caddy access log shows the strip, OpenWebUI never receives it (read OpenWebUI's request log if available, or confirm via behavioral test — even with `Remote-Email` set, OpenWebUI still presents its native login because trusted-header SSO is disabled by §6.7)
- [ ] **X-Forwarded-For from CF-Connecting-IP:** hit the portal from a phone on cellular; in Caddy access log, confirm `X-Forwarded-For` matches the phone's public IP, not `cloudflared`'s container IP (§9.4)
- [ ] **Body limit:** `curl -X POST --data-binary @<5MB-file> https://${PUBLIC_DOMAIN}/api/something` returns 413
- [ ] **Network policy:** LAN IPs hit `one_factor`, external IPs hit `two_factor` (test from phone on cell data)
- [ ] **Backend isolation:** confirm llama-cpp / llama-cpp-embed / mnemory / surrealdb / open-terminal are NOT reachable from internet:
  - `curl https://${PUBLIC_DOMAIN}/api/embeddings` → 404 from Caddy or 401 from Authelia (neither = exposed)
  - `curl http://<host-public-ip-if-known>:8003/` → connection refused (host has no inbound 8003 binding; surrealdb is bound to 127.0.0.1)
  - `curl http://<host-public-ip>:8050/` → connection refused
- [ ] **Docker network isolation:** `docker exec authelia ping -c 1 8.8.8.8` **fails** (auth-net is `internal: true`); `docker exec caddy ping -c 1 8.8.8.8` succeeds (caddy on edge-net)
- [ ] **Healthcheck not publicly exposed:** `curl https://${PUBLIC_DOMAIN}/healthz` returns 404 (only container-local sources see 200)
- [ ] **Backup:** verify `./backups/caddy/` and `./backups/authelia/` get a `.tar.gz` + `.sha256` sentinel after the first scheduled run, OR force one with:
  ```powershell
  docker exec caddy-backup sh /scripts/backup.sh
  docker exec authelia-backup sh /scripts/backup.sh
  ```
- [ ] **Backup integrity:** extract one backup to a scratch dir; for `authelia`, open the SQLite DB and run `SELECT COUNT(*) FROM webauthn_credentials;` — non-zero count proves the tarball isn't a corrupt stub (§H.11)

### Breach detection / IR validation
- [ ] **Pushover end-to-end test:** trigger a synthetic ban — 4 failed logins from one IP. Pushover phone notification arrives within 60 s. Message includes timestamp, event type, source IP.
- [ ] **New-IP alert:** log in from a previously-unseen source IP. Pushover notification arrives with "new IP" event.
- [ ] **Config-drift alert:** edit `config/caddy/Caddyfile` (any cosmetic change), run `docker exec integrity-tripwire /scripts/tripwire.sh check`. Pushover notification arrives reporting drift. Restore file; run `docker exec integrity-tripwire /scripts/tripwire.sh accept` to re-baseline.
- [ ] **Killswitch dry-run:** review `scripts/breach-killswitch.ps1` end-to-end with the user. Run in a maintenance window: confirm only caddy/cloudflared/authelia/watchers stop, logs snapshot to `./incident/...`, and `.env` secret rotation is offered (do NOT commit the rotation in the dry-run unless you intend to). Confirm tailnet path remains alive throughout: `curl https://<tailnet-host>.ts.net/` succeeds during the killswitch state.
- [ ] **`documentation/incident-response.md` exists** and has been walked through end-to-end at least once by the user.

### Recovery test (do once, then document the procedure)
- [ ] Stop the stack, restore `authelia-data` from **yesterday's** backup (not today's — simulate "today's may be poisoned"), restart, confirm TOTP/WebAuthn enrollments from yesterday's snapshot survive
- [ ] Confirm an existing session cookie issued before the restore is rejected (post-restore, JWT/session secret has rotated or sessions are invalidated by config reload)

---

## 12. Operational notes

### 12.1 Update policy
- **Caddy:** safe to bump minors. Pin patch tag in [docker-compose.yml](../../../docker-compose.yml), bump explicitly in a PR.
- **Authelia:** historically renames config fields between minors. **Always read the changelog** before bumping past `4.39`. Stay on a pinned minor.
- **CrowdSec (v2):** safe to bump minors; scenarios update independently via `cscli`.
- **cloudflared:** pin a dated tag (e.g., `2024.10.0`). Do not run `:latest` in production — Cloudflare can ship breaking config changes. Bump in a deliberate PR.
- **Alpine sidecars (`authelia-watcher`, `integrity-tripwire`, backups):** bump minor releases together with the Caddy/Authelia PR cadence. Alpine patches are usually safe but verify `inotify-tools` / `curl` are still present after a bump.

### 12.2 Backup discipline (v1 required, not deferred)
- `authelia-data` — losing this means re-enrolling TOTP/WebAuthn for every user. Backed up nightly by `authelia-backup` (§8 Step 7).
- `caddy-data` — losing this loses ACME state + access logs. Backed up nightly by `caddy-backup`.
- `tripwire-data` — small (kilobytes); losing it forces re-baseline of `integrity-tripwire`. Add to a future backup pass or accept re-baseline as the recovery path.
- `openwebui-data`, `mnemory-data` — already covered by existing backup containers.
- `D:\Open WebUI\open-notebook\notebook_data` and `D:\Open WebUI\open-notebook\surreal_data` — currently **NOT backed up** by any compose service. Add to a future backup PR; flag in `SECURITY.md`.

Backup target should be **offsite** (or at least off-volume). The current backup pattern writes to `./backups/*` on the same host — that's better than nothing but doesn't survive disk failure or ransomware. **v1 deploy checklist (§11) requires:** verify `./backups/` is synced to ≥1 off-host destination (S3, Backblaze B2, NAS, USB drive) on a daily cadence. Tooling is left to the user (`rclone`, `restic`, OneDrive sync, etc.).

### 12.3 Cert renewal monitoring
Under Cloudflare Tunnel, **Cloudflare owns the public certificate** at the edge. Caddy itself doesn't issue certs unless it's reverted to direct exposure. Failure modes that matter under Tunnel:

- **Cloudflare edge cert renewal failure** — visible in the Cloudflare dashboard. Cloudflare emails the account owner.
- **Tunnel disconnection** — `cloudflared` logs show reconnection attempts. The portal is unreachable but the home stack is unaffected.

Add to [scripts/check-tailscale-health.ps1](../../../scripts/check-tailscale-health.ps1)-style daily check:
```powershell
# Cert expiry sentinel (audit §D.5)
$cert = (Invoke-WebRequest -Uri "https://$env:PUBLIC_DOMAIN" -UseBasicParsing).BaseResponse.ResponseUri
# Actual TCP-level cert inspection — leave as a stub here, the implementing
# agent expands this in scripts/check-portal-health.ps1
```

### 12.4 Log retention
- Authelia logs to `/data/authelia.log` (JSON) inside `authelia-data`. Caddy access log → `/data/caddy-access.log` inside `caddy-data`. Both roll at 100 MiB / 7 files / 30 days (Caddy's roller; Authelia rotates manually).
- Manually rotate Authelia logs:
  ```sh
  docker exec authelia find /data -name 'authelia.log.*' -mtime +30 -delete
  ```
- These logs are forensic evidence — keep at least 30 days, prefer 90.

### 12.5 Secrets management posture (v1 acceptance)
- `.env` stores all secrets; permissions locked to the current user.
- `.env` is in [.gitignore](../../../.gitignore).
- Argon2id hashes in `users_database.yml` are not committed.
- Pushover tokens, Cloudflare Tunnel token, Authelia secrets — all in `.env`.
- **No Vault, no SOPS.** Acceptable for single-user home stack. If multi-user ever happens, revisit (§13 Tier 2).

### 12.6 Breach response operations
- **Killswitch:** `scripts/breach-killswitch.ps1` (spec in §8 Step 6) stops only the internet-exposed path. Tailnet access survives. Run it the moment a Pushover alert looks credible — false-positive cost is low (5 minutes of downtime), missed-true-positive cost is high.
- **After killswitch:**
  - Read `documentation/incident-response.md` end to end
  - Snapshot `./incident/<UTC-timestamp>/` to an off-host destination before any further mutation
  - Restore from yesterday's backup (not today's) before restarting
  - Re-enroll WebAuthn / TOTP credentials
  - Rotate `CLOUDFLARE_TUNNEL_TOKEN` if the tunnel container appeared compromised
- **Password reset (no SMTP):** admin manually edits `users_database.yml` and re-hashes via `docker run --rm authelia/authelia:4.39 authelia crypto hash generate argon2 --password <new>`. Then `docker restart authelia` (the file is watched but a restart guarantees pickup).

### 12.7 User-facing notes
- One bookmark: `https://${PUBLIC_DOMAIN}/`
- First login: TOTP enrollment QR appears; **prefer WebAuthn** — register at least two hardware keys at `https://auth.${PUBLIC_DOMAIN}/settings/two-factor`
- **Expected UX:** Authelia login → hub → (auto-redirect or click OpenWebUI) → OpenWebUI's own login form → in. Open Notebook does not have a second login — it is unauthenticated behind Authelia (§2 decision row).
- Sign out: hub footer link or `https://auth.${PUBLIC_DOMAIN}/logout`

### 12.8 Surprises documented for the implementing agent
- Caddy's `forward_auth` requires `/api/verify?rd=...`, not `/api/authz/...`
- Subpath proxying may need subdomain fallback (Streamlit + OpenWebUI both have edge cases). Subdomain fallback in CF Tunnel mode = add another public hostname in the Cloudflare dashboard.
- Open Notebook's frontend auto-detects API URL from `X-Forwarded-Proto`/`Host` ([entrypoint.sh:384-389](../../../entrypoint.sh#L384-L389)) — `X-Forwarded-Proto https` is non-optional on the `/notebook/*` and `/api/notebook/*` routes
- Authelia `default_policy: deny` means **everything denied unless allowed by a rule** — test changes with an existing session open in another tab to avoid lockout
- Custom Caddy build (v2) makes Watchtower auto-update impossible; that's already the policy anyway (`watchtower.enable=false`)
- Tailscale serve operates inside the openwebui container's network namespace; the new Caddy is on `edge-net`/`auth-net`/`app-net` — they never see each other directly, which is exactly what we want
- The `Remote-*` strip in `sanitize_proxy_headers` is **defense in depth even though trusted-header SSO is disabled.** Do not remove it. If a future change re-enables trusted-header SSO without re-evaluating §6.7, the strip prevents the internet path from forging headers — though the tailnet path would still need to be addressed separately.
- Under Cloudflare Tunnel, Caddy's `:80` is reached over the Docker `edge-net` bridge only. The container does not bind any host port. `netstat -an` on the Windows host should show **no** new listeners.

---

## 13. Known gaps and Tier 2 hardening (not in v1 or v2)

These items from [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md) are **knowingly not addressed** in v1/v2. Listed here so they are visible and reviewable, not forgotten.

### 13.1 Network segmentation (DMZ / VLAN)
**Considerations §5.** Recommends placing the auth portal in a DMZ VLAN, separated from internal LAN by pfSense/OPNsense rules.

**v1 partial mitigation:** Three Docker networks (`edge-net`, `auth-net` `internal: true`, `app-net`) constrain blast radius at the container layer. A Caddy compromise no longer grants direct L3 to all backends; Authelia cannot egress to the internet from `auth-net`.

**Remaining gap (Tier 2):** Windows host with a single NIC on a flat home LAN. The Docker host itself reaches the LAN. If the **host** is compromised (not just a container), there is no L2 boundary protecting other LAN devices.

**Tier 2 upgrade path:**
- Add a pfSense/OPNsense VM with VLANs, move the Docker host onto a DMZ VLAN
- OR move the entire AI stack to a dedicated host (Raspberry Pi 5, mini-PC) on a guest network with router rules blocking it from reaching the main LAN

### 13.2 Centralized log aggregation
**Considerations §9.** Recommends Grafana Loki / ELK for aggregation, alerting on geographic anomalies, cert expiry, etc.

**v1 partial mitigation:** the `authelia-watcher` sidecar (§8 Step 1) provides real-time alerting via Pushover for the high-value events: regulation bans, new-IP logins, credential enrollment changes, config reloads, repeated 401s. This is **alerting**, not aggregation — long-form analytics (geographic heatmaps, multi-day trend lines) still require centralized logs.

**Tier 2 upgrade path:**
- Add Grafana + Loki + Promtail containers
- Ship Authelia + Caddy + CrowdSec logs to Loki
- Migrate Pushover alerts to Alertmanager → webhook → Pushover (consolidating the notification path)

### 13.3 Web Application Firewall in v1
**Considerations §4, §10.** Recommends ModSecurity + OWASP CRS.

**Current v1 posture:** no application-layer WAF. Authelia + Caddy headers + body limits + `Remote-*` strip + Cloudflare's edge bot management provide partial coverage. Cloudflare also offers Cloudflare WAF (paid tier) and Bot Fight Mode (free) — enable these in the dashboard for additional v1 coverage with no compose changes.

**v2 closes this:** CrowdSec AppSec ships scenarios equivalent to OWASP CRS. Plan §10 includes AppSec collections.

### 13.4 Hardware secrets / Vault
**Considerations §11.** Recommends Vault / SOPS.

**Current posture:** `.env` with filesystem permissions. Acceptable for single-host single-user.

**Tier 2 upgrade path:** HashiCorp Vault container + `vault-agent` injecting secrets into Authelia/Caddy at runtime.

### 13.5 Host-level file integrity monitoring
**Considerations §8.** AIDE / OSSEC.

**v1 partial mitigation:** `integrity-tripwire` sidecar monitors the auth-portal config files (`Caddyfile`, `configuration.yml`, `users_database.yml`). This is **scoped to the portal**, not the host.

**Tier 2 upgrade path:** Wazuh agent on the Windows host for system-wide file integrity monitoring + log forwarding.

### 13.6 Formal incident response — v1 deliverable, not Tier 2
**Considerations §9.** Documented IR playbook.

**v1 posture (NEW — moved from Tier 2 during audit revision):** the implementing agent writes `documentation/incident-response.md` as part of v1. Required outline:

1. **Detection signals** — Pushover alert types from §8 Step 6 and what each maps to
2. **Containment** — run `scripts/breach-killswitch.ps1`; confirm tailnet still serves OpenWebUI; snapshot logs
3. **Eradication** — rotate `AUTHELIA_JWT_SECRET`, `AUTHELIA_SESSION_SECRET`, `AUTHELIA_STORAGE_ENCRYPTION_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`; regenerate Argon2 hashes; wipe and re-enroll WebAuthn
4. **Recovery** — restore Authelia from yesterday's backup (today's may be poisoned); validate user list; bring `caddy`, `cloudflared`, `authelia` back up one at a time
5. **Forensics** — preserve `./incident/<timestamp>/` logs off-host; run `docker diff caddy` and `docker diff authelia` to inspect filesystem deltas; check `authelia.log` for the first authenticated request from the attacker IP
6. **Notification** — what to tell household users; what to add to `SECURITY.md` updates
7. **Post-mortem** — root cause; tune `authelia-watcher` alert thresholds; close the gap

[emergency_recovery_access_guide.py](../../../emergency_recovery_access_guide.py) covers stack-recovery from non-breach incidents (disk failure, container crash); the new IR doc covers breach scenarios specifically.

### 13.7 HIBP (Have-I-Been-Pwned) password check at change time
**Considerations §2.** Recommends API check against known-breached passwords.

**Current posture:** manual — user is instructed to check their password at haveibeenpwned.com before hashing.

**Tier 2 upgrade path:** Authelia does not natively integrate HIBP. Either swap to an IdP that does (Keycloak with the HIBP plugin) or accept manual.

### 13.8 SSL Labs A+ grade automation
**Current posture:** validation checklist (§11) includes a one-time SSL Labs check. Under Cloudflare Tunnel, the public TLS posture is mostly Cloudflare's responsibility.

**Tier 2 upgrade path:** scheduled `sslyze` or `testssl.sh` run via GitHub Actions on a cron, alerting if grade drops.

### 13.9 OpenWebUI / Open Notebook / SurrealDB hardening
**Pre-existing posture:** these services run without `tmpfs`, with `pull_policy: always` (open_notebook + surrealdb), and SurrealDB runs as root inside its container ([docker-compose.yml:411-413](../../../docker-compose.yml#L411-L413)).

**Why deferred:** the "OpenWebUI and Open Notebook must remain otherwise untouched" requirement bars hardening passes on them as part of this PR. The only touches this plan makes to them are §6 pre-flight (network bind addresses) and §7.4 (additional Docker network attachment).

**Future PR:** add `tmpfs /tmp`, pin tags away from `pull_policy: always`, drop SurrealDB to non-root UID. Out of scope here.

---

## 14. References

- Authelia configuration reference: https://www.authelia.com/configuration/
- Authelia + Caddy integration: https://www.authelia.com/integration/proxies/caddy/
- Authelia regulation: https://www.authelia.com/configuration/security/regulation/
- Caddy `forward_auth` directive: https://caddyserver.com/docs/caddyfile/directives/forward_auth
- Caddy TLS configuration: https://caddyserver.com/docs/caddyfile/directives/tls
- Caddy `trusted_proxies` and `client_ip_headers`: https://caddyserver.com/docs/caddyfile/options#trusted-proxies
- Caddy request body limits: https://caddyserver.com/docs/caddyfile/directives/request_body
- Caddy access log: https://caddyserver.com/docs/caddyfile/directives/log
- CrowdSec + Caddy bouncer: https://github.com/hslatman/caddy-crowdsec-bouncer
- CrowdSec AppSec docs: https://docs.crowdsec.net/docs/appsec/intro
- Cloudflare Tunnel quick start: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/
- Cloudflare CF-Connecting-IP header: https://developers.cloudflare.com/fundamentals/reference/http-request-headers/#cf-connecting-ip
- Cloudflare TLS SSL/TLS modes: https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/
- Pushover API: https://pushover.net/api
- OWASP Top 10 (current): https://owasp.org/www-project-top-ten/
- OWASP password-storage cheat sheet (Argon2 params): https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- Mozilla SSL Configuration Generator: https://ssl-config.mozilla.org/
- HIBP Passwords API: https://haveibeenpwned.com/Passwords
- DNS CAA records (RFC 8659): https://datatracker.ietf.org/doc/html/rfc8659
- Docker `internal: true` networks: https://docs.docker.com/compose/compose-file/06-networks/#internal

---

## 15. Handoff notes for the implementing agent

**Order of operations:**
1. Read [docker-compose.yml](../../../docker-compose.yml), [entrypoint.sh](../../../entrypoint.sh), `SECURITY.md`, [audit-plan-internet-exposed-front-end.md](./audit-plan-internet-exposed-front-end.md), and [integration-task-document.md](./integration-task-document.md) end-to-end. Confirm your mental model matches §5.
2. **Confirm Cloudflare Tunnel is acceptable** to the user given §9.1 privacy callout. If not, this plan does not apply — escalate.
3. **Decide tailnet trust posture** (§6.6) — Option A / B / C. Document in the PR description. Note: trusted-header SSO into OpenWebUI is NOT enabled (§6.7), so the tailnet posture question is reduced to Open Notebook only.
4. Land **§6 pre-flight fixes** in the same PR. The exposure they close is unsafe to leave once Caddy is reachable.
5. Generate secrets (`AUTHELIA_*`, `SURREAL_PASSWORD`, `OPEN_NOTEBOOK_ENCRYPTION_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `PUSHOVER_*`) and have the user paste them into their `.env`. Do not commit `.env`. Lock file permissions with `icacls`.
6. Configure Cloudflare side first (§9.3): DNS migration, CAA records, tunnel creation, public hostnames. Confirm `dig` results before starting any containers.
7. Bring containers up in order: `authelia` → `caddy` → `cloudflared` → `authelia-watcher` + `integrity-tripwire` → backups. Confirm health at each step.
8. Walk the §11 validation checklist. Every box. The **"Tailscale path still works"** and **"Pushover end-to-end test"** checks are the canaries — neither may be skipped.
9. v2 (CrowdSec) is a **separate PR**. Do not bundle.

**When to STOP and ASK the user:**
- Before destroying `D:\Open WebUI\open-notebook\surreal_data` (pre-flight §6.3)
- Before changing `OPEN_NOTEBOOK_ENCRYPTION_KEY` if Open Notebook has stored API keys (pre-flight §6.4)
- Before settling on tailnet trust posture (§6.6 Option A/B/C)
- If subpath routing for Open Notebook / OpenWebUI doesn't work and the fallback is subdomain — confirm the user owns/controls the subdomains and wants to add Cloudflare public hostnames
- Before applying `--server.baseUrlPath=/notebook` to Open Notebook (subpath Fallback B in §8 Step 2) — this is a touch to Open Notebook's behavior and conflicts with the "untouched" rule
- If the **`auth-net internal: true` network blocks Pushover** (§8 Step 1 note) — confirm with the user whether to add the dedicated `notify-net` network or to proxy via Caddy
- If any §11 security smoke test fails — diagnose and fix, do not declare done
- Before submitting `${PUBLIC_DOMAIN}` to the HSTS preload list (§H.1) — this is irreversible for years
- Before applying any container-level change to `openwebui`, `open_notebook`, or `surrealdb` beyond what is explicitly documented in §6 / §7.4

**Definition of done for v1:**
A fresh device, NOT on the user's tailnet, can browse to `https://${PUBLIC_DOMAIN}/`, authenticate via Authelia with WebAuthn (or TOTP fallback), land on the hub, auto-redirect to OpenWebUI, complete OpenWebUI's native login, use OpenWebUI normally, navigate back to the hub, switch to Open Notebook, use it normally without a second login (Authelia is its only gate). Concurrently, a separate device on the tailnet reaches `https://<tailnet-host>.ts.net/` without ever seeing Authelia, and lands on OpenWebUI's native login as today. Every §11 box is checked. The Pushover end-to-end test produced a real phone notification. The killswitch dry-run completed and tailnet stayed alive. Backup containers have produced at least one archive with valid `.sha256` sentinel.
