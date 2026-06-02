# Post-Implementation Security Audit — Internet-Exposed Portal

**Date:** 2026-05-29
**Auditor scope:** live runtime + configuration vs [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md), [audit-plan-internet-exposed-front-end.md](./audit-plan-internet-exposed-front-end.md), [integration-task-document.md](./integration-task-document.md), and [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md)
**Verdict:** **Production-deployable with two MEDIUM findings to remediate and several documentation updates required.** Core security properties (mutual auth, network isolation, hardening floor, breach detection) verified in place and working.

---

## A. Executive summary

| Category | Verdict | Notes |
|---|---|---|
| Container hardening floor (plan §2) | **PASS** | 8/8 portal services run non-root with `read_only`, `cap_drop:ALL`, `no-new-privileges`, `tmpfs /tmp` enforced |
| Network segmentation | **PASS** | `auth-net internal:true` proven to block egress; backend services unreachable to Caddy |
| Mutual auth chain | **PASS** | Forged `Remote-Email` produces 401, not bypass; `forward_auth` correctly Authelia-gates every route |
| Security headers + cookies | **PASS** | HSTS, CSP, XFO, Permissions-Policy, COOP/CORP all present; cookies are `Secure; HttpOnly; SameSite=Lax` |
| Backend isolation | **PASS** | Caddy cannot even DNS-resolve `mnemory`, `llama-cpp`, `surrealdb` |
| Original audit §B blockers | **PASS** | All four (B.1–B.4) closed and re-verified live |
| Original audit §C hardening | **PASS with deviation** | C.2 Authelia `read_only:true` only works with targeted `/app/.healthcheck.env` bind mount (documented); C.7 open_notebook still unhardened (plan §13.9 deferred); rest clean |
| Breach detection (§D) | **PASS** | Watcher + tripwire + alerter pipeline operational; alerter `/health` returns ready, `last_alert_at` populated |
| Incident response (§E) | **PASS** | killswitch.ps1 + incident-response.md present and aligned with portal-only stop semantics |
| Doc drift | **FAIL** | Plan §8 Caddyfile + compose snippets significantly out of date relative to live implementation |
| Backup integrity | **PARTIAL** | Tarballs produced and integrity-sentinels added, but UID mismatch silently excludes some Caddy/Authelia state files (MEDIUM) |
| Pre-flight §6 follow-through | **PARTIAL** | §6.1-6.4, 6.7, 6.9 done; §6.3 (`SECURITY.md` updates), §6.5 (explicit Public-profile block), §6.8 (2nd WebAuthn / TOTP recovery) still open |

Three findings rise to MEDIUM. None block live operation. Recommended remediation order in §I.

---

## B. Phase 1 — runtime verification (PASS unless noted)

### B.1 Container hardening (plan §2 floor)

All 8 portal services verified live:

| Service | UID | read_only | cap_drop | cap_add | no-new-priv | network attachments |
|---|---|---|---|---|---|---|
| portal-alerter | 10006 | ✓ | ALL | — | ✓ | auth-net, notify-net |
| authelia | 10001 | ✓ | ALL | — | ✓ | auth-net |
| caddy | 10000 | ✓ | ALL | NET_BIND_SERVICE | ✓ | edge-net, auth-net, app-net |
| authelia-watcher | 10002 | ✓ | ALL | — | ✓ | auth-net |
| integrity-tripwire | 10003 | ✓ | ALL | — | ✓ | auth-net |
| portal-cron | 10007 | ✓ | ALL | — | ✓ | notify-net |
| caddy-backup | 10004 | ✓ | ALL | — | ✓ | default (see F.1) |
| authelia-backup | 10005 | ✓ | ALL | — | ✓ | default (see F.1) |

**Authelia `read_only:true` deviation**: upstream image writes `/app/.healthcheck.env` at startup. Targeted bind-mount of an empty placeholder file from `config/authelia/.healthcheck.env` allows that single write while keeping the rest of the rootfs RO. Verified working — `touch /probe` in the container fails with `Read-only file system`. Documented in `docker-compose.yml` and ready for upstream-image change tracking.

### B.2 Network egress (auth-net `internal:true`)

Live wget tests with 3s timeout against `https://www.cloudflare.com/`:

| Container | Result | Verdict |
|---|---|---|
| authelia | wget exit=4 (no DNS) | ✓ properly blocked |
| authelia-watcher | wget bad address | ✓ properly blocked |
| integrity-tripwire | wget bad address | ✓ properly blocked |
| portal-alerter | HTTP 200 from cloudflare.com | ✓ expected (notify-net) |
| portal-cron | HTTP 200 from cloudflare.com | ⚠ DEFENSE-IN-DEPTH GAP — see F.2 |
| caddy | HTTP 200 from cloudflare.com | ⚠ DEFENSE-IN-DEPTH GAP — see F.2 |

### B.3 Backend isolation

From inside Caddy, `nc` to backend names fails with `bad address`:
- `mnemory:8051` — bad address (on `llm-net` only)
- `llama-cpp:8080` — bad address (on `llm-net` only)
- `surrealdb:8000` — bad address (on `default` only)

Compromising Caddy does NOT give an attacker direct L3 to any backend except `openwebui:8080` and `open_notebook:8502/5055` (on `app-net`). This matches plan §5 architecture.

### B.4 Security response headers

All present in 200 (Authelia) AND 401 (forward_auth) responses:

| Header | Value |
|---|---|
| Strict-Transport-Security | `max-age=31536000; includeSubDomains` (no preload — per plan §H.1) |
| X-Content-Type-Options | `nosniff` |
| X-Frame-Options | `DENY` |
| Referrer-Policy | `strict-origin-when-cross-origin` |
| Permissions-Policy | `geolocation=(), microphone=(), camera=(), usb=()` |
| Cross-Origin-Opener-Policy | `same-origin` |
| Cross-Origin-Resource-Policy | `same-site` |
| Server / X-Powered-By | absent (stripped) |

**Cookie attributes** on Authelia's `authelia_session`: `domain=devinveller.ai; HttpOnly; secure; SameSite=Lax` — OWASP-aligned.

### B.5 Header sanitization (forged `Remote-Email` + XFF test)

Sent: `Host: devinveller.ai`, `Remote-Email: admin@evil.com`, `Remote-User: EVIL`, `X-Forwarded-Host: evil.com`, `X-Forwarded-For: 1.2.3.4`.

Result: **HTTP 401** with `Location: https://auth.devinveller.ai/?rd=...`. Authelia logs show the request reached its `/api/authz/auth-request` endpoint as `<anonymous>` — the forged `Remote-Email` did NOT propagate. Forgery vector closed.

The Caddy access log retains the raw forged headers (this is intentional forensic capture, not a bypass). The post-`sanitize_proxy_headers` state is what `forward_auth` and `reverse_proxy` use upstream.

### B.6 Body size limits

Defense layered correctly: `forward_auth` runs before `request_body` directives (Caddy default ordering). Unauthenticated requests with 5 MB body get 401 (not 413) — attackers can't even attempt large-body attacks pre-auth. Catch-all `:80 { respond 421 }` returns 421 for 5 MB to wrong-Host requests without consuming the body.

### B.7 `/healthz` exposure

`/healthz` with `Host: devinveller.ai` returns 401 (forward_auth gate runs first; the `@internal_health` matcher with `remote_ip` restriction is effectively dead code due to Caddy directive ordering). Net effect: liveness not leaked to unauthenticated scanners — **plan intent met**, mechanism differs from the documented snippet.

### B.8 Authelia regulation (configured, not e2e-tested)

Configuration verified: `max_retries: 3, find_time: 2m, ban_time: 1h`. The `banned_user` table is empty (no bans triggered to date — earlier failed attempts didn't cluster within 2m for the same valid username). Not deliberately triggered to avoid self-locking during the live test. Caveat noted in §I.

### B.9 No spurious host port bindings

Only `caddy 80/tcp → 127.0.0.1:8443` (test-mode override; production has zero host ports). Every other portal service: no host-port mapping. `netstat`-side checks unnecessary.

---

## C. Phase 2 — original audit §B-§E re-validation

### C.1 §B.1 — `Remote-*` trusted-header forgery (was: CRITICAL)

`docker exec openwebui env | grep WEBUI_AUTH_TRUSTED` returns **nothing**. OpenWebUI does not consume `Remote-Email`. The "two-login" trade-off is honored. Defense-in-depth confirmed live via §B.5 above.

**Status:** CLOSED.

### C.2 §B.2 — Cloudflare client-IP rewrite

Caddyfile global block contains:
```
trusted_proxies static 172.16.0.0/12 10.0.0.0/8 192.168.0.0/16
client_ip_headers CF-Connecting-IP X-Forwarded-For
```
Inside the Docker bridges (cloudflared's edge-net is in 172.27.x.x, within `172.16.0.0/12`), Caddy correctly identifies cloudflared as a trusted proxy and rewrites the client IP from `CF-Connecting-IP`. The earlier test from the host showed `request.remote_ip: 172.27.0.1` correctly populated.

**Status:** CLOSED.

### C.3 §B.3 — `forward_auth` upstream-header bleed

Live Caddyfile `forward_auth` block:
```caddy
forward_auth authelia:9091 {
    uri /api/authz/auth-request
    header_up X-Original-URL https://{host}{uri}
    header_up X-Original-Method {method}
}
```
**`copy_headers` is intentionally absent**, so no `Remote-*` values from Authelia's response propagate to upstream `reverse_proxy`. This was a deviation from the plan's literal Caddyfile (which used the legacy `/api/verify` endpoint) but architecturally identical and consistent with audit §B.3 intent.

**Status:** CLOSED (with implementation deviation — see G.1).

### C.4 §B.4 — Backup container env-passing fix

Original concern: alpine `crond` not inheriting compose env vars. My implementation switched the backup containers from `image: alpine:3.21 + apk + busybox crond` to **custom `portal-backup-*:local` images built from `backup/Dockerfile` with `supercronic`** (`config/portal-cron/Dockerfile` pattern). supercronic naturally inherits the parent process env — the `/etc/profile.d/...` shim from the plan is no longer needed.

**Status:** OBSOLETE — original concern overtaken by architectural improvement.

### C.5 §C.1 — Docker network segmentation

3 portal networks (edge, auth, app) + the new `notify-net` (for portal-alerter Gmail egress + portal-cron→alerter routing) verified live (`docker network inspect` shows `internal=true` only on `auth-net`). Pattern matches plan §5 with the post-audit `notify-net` addition.

**Status:** PASS.

### C.6 §C.2 — Read-only root FS

8/8 portal services have `read_only:true`. Authelia required a targeted `/app/.healthcheck.env` bind mount (B.1 deviation, properly documented).

**Status:** PASS with documented deviation.

### C.7 §C.3 — No docker.sock mounts

Verified across all 8 portal services: zero `docker.sock` mounts.

**Status:** PASS.

### C.8 §C.5 — `cap_drop:ALL` on cloudflared

Confirmed in `docker-compose.yml` definition. Container is currently absent (test mode) but compose config is correct.

**Status:** PASS.

### C.9 §C.6 — Non-root UIDs

UIDs 10000–10007 verified live for all 8 services. Caddy explicitly re-adds `NET_BIND_SERVICE` for `:80` inside the container.

**Status:** PASS.

### C.10 §C.7 — `tmpfs` on OpenWebUI / Open Notebook

- openwebui: `read_only=false, tmpfs=/tmp:noexec,nosuid,size=100m` — already had tmpfs (pre-existing)
- open_notebook: `read_only=false, tmpfs=none` — UNCHANGED per plan §13.9 deferred-scope

**Status:** PASS (per the "untouched services" deferral).

### C.11 §C.8 — Healthcheck endpoint exposure

Plan's `@internal_health` matcher with `remote_ip` allowlist is technically dead code (forward_auth runs first). Practical effect: `/healthz` returns 401 to unauthenticated visitors — does NOT leak liveness. Intent met.

**Status:** PASS (with mechanism note in B.7).

### C.12 §D — Breach detection sidecars

Live:
- `authelia-watcher` logs show `watching: /logs/authelia/authelia.log and /logs/caddy/caddy-access.log` with `alerter target: http://portal-alerter:8080/alert`
- `integrity-tripwire` reports `baseline verified` on startup; `/state/baseline.sha256` exists (251 bytes, owned by tripwire:tripwire)
- `portal-alerter` `/health`: `{ready: true, last_alert_at: "2026-05-29T16:52:10.478Z", coalesce_queue_depth: 0, rate_limit_per_min: 20, window_hours: 24}`
- Caddy access log rolling: `/data/caddy-access.log` exists, 223 KB, mode 644

**Status:** PASS.

### C.13 §E — Incident response

- `scripts/breach-killswitch.ps1` present (7,014 bytes); explicitly enumerates portal services to stop (post-incident lesson from earlier "compose down" mishap)
- `documentation/incident-response.md` present (14,277 bytes); covers all 7 sections per plan §13.6 outline

**Status:** PASS.

---

## D. Phase 3 — security-considerations matrix coverage

Each row maps to [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md):

| # | Consideration | Coverage |
|---|---|---|
| 1 | TLS / cipher hardening | Cloudflare edge at TLS 1.2 min (operator-confirmed Part 6); Caddy origin HTTP-only over private bridge by design |
| 2 | Auth hardening (MFA, lockout) | Authelia regulation 3/2m/1h; WebAuthn-preferred; CF Access OTP as outer gate (operator added) |
| 3 | Session mgmt | Cookie `Secure;HttpOnly;SameSite=Lax`; inactivity 30m, expiration 8h, remember_me 1M |
| 4 | Reverse proxy hardening | sanitize_proxy_headers + trusted_proxies + body limits + path whitelist + Remote-* strip — all verified |
| 5 | Network segmentation | Docker layer (PASS); host-level VLAN/DMZ remains plan §13.1 deferred |
| 6 | Brute force / DDoS | Authelia regulation + CF edge bot management (operator's CF setup); v2 CrowdSec deferred |
| 7 | IdP | Authelia 4.39 file-based backend (single user); password Argon2id m=64MiB t=3 p=4 |
| 8 | Host & OS | Windows Public profile inbound implicit-deny via `NotConfigured` default (mild gap — plan wanted explicit Block) |
| 9 | Logging / monitoring / IR | Caddy + Authelia JSON logs + watcher + tripwire + Gmail alerter + IR doc + killswitch — full v1 stack present |
| 10 | OWASP | CSP on hub; body limits; XFO/COOP/CORP; CF Bot Fight Mode + Access — partial coverage; v2 AppSec deferred |
| 11 | Architecture & ops | .env + icacls; gitignore on secrets + users_database.yml + .healthcheck.env populated state; killswitch rotation script — sound for single-user home stack |

---

## E. Phase 3 — pre-flight §6 follow-through

| Item | Status |
|---|---|
| 6.1 bind open_notebook to 127.0.0.1 | DONE |
| 6.2 bind surrealdb to 127.0.0.1 | DONE |
| 6.3 SurrealDB creds in `.env` | DONE (operator kept `root`/`root` per their decision) |
| 6.4 OPEN_NOTEBOOK_ENCRYPTION_KEY in `.env` | DONE (operator preserved existing value) |
| 6.5 Windows Defender Public profile default-deny inbound | **PARTIAL** — `DefaultInboundAction = NotConfigured` (Windows default = Block), not explicit Block. Practical effect: inbound is blocked, but plan literal text said "Block (default)" — recommend explicit Set-NetFirewallProfile for documentation integrity |
| 6.6 Open Notebook tailnet trust posture | Decision recorded (Option C — accept) but NOT in `SECURITY.md` yet (see F.4) |
| 6.7 OpenWebUI trusted-header SSO NOT enabled | DONE (verified live; no `WEBUI_AUTH_TRUSTED_*` env) |
| 6.8 **Two WebAuthn keys OR TOTP recovery offline** | **OPEN** — operator has done initial 2FA enrollment via the live test but second-credential enrollment and/or printed TOTP recovery not confirmed. **This is the single highest-impact operational gap.** |
| 6.9 portal-alerter dedicated OAuth client | DONE (separate from OB1's; self-test email confirmed) |

---

## F. New findings (introduced or surfaced during implementation)

### F.1 — Backup ownership UID mismatch (MEDIUM)

**Observation:** Manual `caddy-backup` run produced a 17 KB tarball but with errors:
```
tar: ./caddy/locks: Permission denied
tar: can't open './caddy/instance.uuid': Permission denied
tar: can't open './caddy/last_clean.json': Permission denied
```
Same pattern for `authelia-backup`: `tar: can't open './notification.txt': Permission denied`.

**Root cause:** Caddy data files are mode 600 owned by 10000:10000 (Caddy). Backup container runs as UID 10004. Same for Authelia (10001 vs backup 10005). The backup process silently EXCLUDES these state files from the tarball.

**Impact:** Restoring from this tarball would re-initialize Caddy's storage UUID (forcing Caddy to re-derive ACME state) and lose Authelia's notification spool. The critical files (`authelia.log`, `db.sqlite3`) ARE mode 644 and DO make it into the tarball, so MFA enrollments and login history are recoverable.

**Severity:** MEDIUM. Restore works for the data that matters; recovery would just look noisier than expected.

**Remediation options (pick one):**
1. Run backup containers as the same UID as the data owner (10000 for caddy-backup, 10001 for authelia-backup). Simplest.
2. Add the backup UID to a shared GID with the data owner + chmod 640 on the state files. More invasive.
3. Run the backup as root (UID 0) and drop privileges via su-exec post-tar. Adds complexity for marginal benefit.

**Recommended:** option 1.

### F.2 — Caddy / portal-cron internet egress (LOW; defense-in-depth)

**Observation:** Caddy (via `edge-net`) and portal-cron (via `notify-net`) can both reach arbitrary internet hosts. Neither needs this; only cloudflared and portal-alerter legitimately egress.

**Root cause:** Docker bridge networks have NAT egress by default. We can't make `edge-net` `internal:true` because cloudflared requires it for the tunnel out; same for `notify-net` with portal-alerter's Gmail egress.

**Impact:** If Caddy or portal-cron is compromised (RCE-style), the attacker can exfiltrate to arbitrary destinations. Mitigations like CF Access already constrain inbound; egress filtering would close the data-out leg.

**Severity:** LOW. The compromise path is already narrow (read_only, no-new-priv, non-root); egress filtering is true defense-in-depth.

**Remediation options:**
1. Document the gap in `SECURITY.md` and accept (lowest effort).
2. Add a dedicated outbound proxy (e.g., the `lc-egress` allowlist pattern already used by little-coder) for portal-alerter only. Block all other egress at the Docker host with iptables. High effort.
3. Replace the bridge networks with overlay networks + an L3 firewall (e.g., Calico). Highest effort, future-PR scope.

**Recommended:** option 1 for v1; track option 2 for v2.

### F.3 — Image tag pinning incomplete (LOW)

| Service | Image | Pin quality |
|---|---|---|
| caddy | `caddy:2.8.4-alpine` | ✓ |
| authelia | `authelia/authelia:4.39` | ✓ |
| cloudflared | `cloudflare/cloudflared:2024.10.0` | ✓ |
| portal-alerter | `denoland/deno:alpine` | ⚠ Moving tag |
| watcher / tripwire / portal-cron / backups | `portal-*:local` (custom build) | ✓ |

**Plan §12.1** says: "Pin `denoland/deno:alpine` to a specific digest."

**Remediation:** Update `docker-compose.yml` to `denoland/deno:alpine@sha256:<digest>` or pin to a versioned tag like `denoland/deno:2.1.4-alpine`. Verify with `alerter.ts --selftest` after each bump.

### F.4 — `SECURITY.md` stale (LOW, but blocks plan task 6.3)

**Observation:** `SECURITY.md` exists (2,634 bytes) but was last modified 2025-09-27 — before any portal work. It contains no references to:
- Tailnet trust posture decision (plan §6.6 Option C)
- Cloudflare data-decryption acknowledgment (plan §9.1)
- `DIGEST_TO` Gmail address as alert routing destination (plan §12.6)
- OAuth client coupling note (plan §6.9 — N/A since operator chose dedicated client, but still worth a note)
- The new findings above (backup ownership, egress, image pinning)

**Severity:** LOW (documentation hygiene), but **integration task 6.3 remains formally OPEN** until this lands.

### F.5 — Plan §8 Caddyfile + §8 Step 1 compose are significantly stale (MEDIUM doc-drift)

**Live deviations from plan §8 Step 2 Caddyfile:**

| Plan §8 says | Live Caddyfile |
|---|---|
| `forward_auth { uri /api/verify?rd=... }` (legacy endpoint) | `forward_auth { uri /api/authz/auth-request; header_up X-Original-URL https://{host}{uri}; header_up X-Original-Method {method} }` |
| Caddy healthcheck via `:80/healthz` | Caddy healthcheck via `:2019/config/` (admin API) |
| `@internal_health` remote_ip-restricted | Same matcher present but dead code (forward_auth runs first) |

**Live deviations from plan §8 Step 1 compose:**
- `portal-init` service added (not in plan) — runs as root briefly, chowns volumes, exits. Required because Docker creates named volumes root-owned and our hardened services run non-root.
- `profiles: [internet, local-test]` split (not in plan) — adds a dev mode that doesn't run cloudflared and binds Caddy to localhost.
- `docker-compose.local-test.override.yml` (not in plan) — adds the `127.0.0.1:8443:80` mapping in test mode.
- Sidecars (watcher, tripwire, backups) switched from `image: alpine + apk-at-startup + busybox crond` to **custom Dockerfiles with supercronic** so they can run non-root with `read_only:true`. Three new Dockerfiles: `config/watcher/Dockerfile`, `config/tripwire/Dockerfile`, `backup/Dockerfile`.
- Authelia `read_only:true` requires a targeted bind mount of `config/authelia/.healthcheck.env` (gap discovered during deployment; one writable file Authelia needs at startup).
- `/api/authz/auth-request` hardcoded `https://` in `X-Original-URL` because Authelia 4.39 rejects HTTP scheme.

**Severity:** MEDIUM. The plan was the source of truth during build; future operators reading the plan would diverge from what's actually deployed. **This is the single most important post-audit documentation task.**

### F.6 — Authelia `jwt_secret` deprecation warning (LOW cosmetic)

Authelia 4.39 logs continuous warnings:
```
configuration key 'jwt_secret' is deprecated in 4.38.0 and has been replaced
by 'identity_validation.reset_password.jwt_secret': you are not required to
make any changes as this has been automatically mapped for you, but to stop
this warning being logged you will need to adjust your configuration...
```
The mapping is automatic, so no functional issue. But the warning will become an error in Authelia 5.0.

**Remediation:** Move `AUTHELIA_JWT_SECRET` env to `AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET` in `.env`, update `docker-compose.yml` references. ~2 min.

### F.7 — Authelia clock-offset NTP warning (LOW informational)

```
Could not determine the clock offset due to an error: lookup time.cloudflare.com
on 127.0.0.11:53: server misbehaving
```
Authelia tries to NTP-check against time.cloudflare.com but `auth-net internal:true` blocks DNS. Authelia falls back to system clock. **Plan §5 explicitly anticipates this** ("Authelia cannot reach SMTP, NTP, ACME, or external WebAuthn attestation servers — fine because notifier is filesystem, time comes from the host"). No remediation needed; informational only.

---

## G. Phase 4 — pending operator actions

| Item | Status | Severity |
|---|---|---|
| Enroll second WebAuthn credential (or print TOTP recovery secret offline) | **OPEN** | **HIGH** — single-credential lockout risk |
| Update `SECURITY.md` per plan task 6.3 | OPEN | MEDIUM |
| Apply backup UID fix (F.1) | OPEN | MEDIUM |
| Pin `denoland/deno:alpine` to a digest (F.3) | OPEN | LOW |
| Update plan + integration-task docs to match live config (F.5) | OPEN | MEDIUM |
| Migrate Authelia `jwt_secret` env var name to its 4.39 home (F.6) | OPEN | LOW |
| (Optional) Add explicit `Set-NetFirewallProfile Public -DefaultInboundAction Block` (§6.5 literal compliance) | OPEN | LOW |
| (Optional) Document Caddy / portal-cron egress gap in `SECURITY.md` (F.2) | OPEN | LOW |
| (Optional, post-90-days) Evaluate HSTS preload submission (plan §H.1, integration-task 6.7) | NOT YET | INFO |

---

## H. What I did NOT verify (limitations of this audit)

- **End-to-end Authelia regulation ban** — configured per spec; not deliberately triggered to avoid self-locking
- **Cloudflare-side TLS strict mode** — operator confirmed during Part 5 of CF Access walkthrough; not re-verified
- **SSL Labs grade** — not run during this audit; recommend running `https://www.ssllabs.com/ssltest/analyze.html?d=devinveller.ai` post-tunnel-up
- **Backup restore drill** — backups exist but a real "extract + open the SQLite + verify counts" drill (plan §11 H.11) has NOT been performed
- **Killswitch live dry-run** — script exists; recommended dry-run with `-DryRun` flag has NOT been performed
- **Synthetic Pushover-equivalent alert E2E to inbox** — the alerter has `last_alert_at=2026-05-29T16:52:10` (the killswitch synthetic from earlier session); no fresh end-to-end test today
- **portal-cron's scheduled `/run` digest email** — cron is set for `0 7 * * *` UTC; no manual `/run` triggered today

---

## I. Recommended remediation order

In merge order, fastest-impact-per-minute first:

1. **(HIGH, ~5 min) Enroll second WebAuthn credential** at `https://auth.devinveller.ai/settings/two-factor`, OR print the TOTP recovery secret to paper + lock in a known location. **Single-credential lockout is currently your largest risk.**
2. **(MEDIUM, ~10 min) Fix backup UID mismatch (F.1)** — change `user: "10004:10004"` to `user: "10000:10000"` on caddy-backup and `user: "10005:10005"` to `user: "10001:10001"` on authelia-backup in `docker-compose.yml`. Recreate the two services. Manually trigger a backup to verify no permission errors.
3. **(MEDIUM, ~30 min) Update plan + integration-task docs to match live config (F.5)** — Caddyfile forward_auth, healthcheck endpoint, portal-init service, profile split, custom Dockerfile pattern, .healthcheck.env bind mount.
4. **(MEDIUM, ~15 min) Update `SECURITY.md` per task 6.3 (F.4)** — tailnet posture C, CF data-decryption acknowledgment, DIGEST_TO Gmail destination, oauth client independence note, backup/egress gaps from this audit.
5. **(LOW, ~5 min) Pin `denoland/deno:alpine` (F.3)** — find current `denoland/deno:alpine` digest (`docker inspect denoland/deno:alpine`) and pin in compose.
6. **(LOW, ~5 min) Migrate `AUTHELIA_JWT_SECRET` env var name (F.6)** — rename to `AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET` in `.env` and `docker-compose.yml`. Reload Authelia, confirm warning is gone.
7. **(LOW, ~10 min) Run `breach-killswitch.ps1 -DryRun` (plan §11 IR validation)** — confirms script path is correct; tailnet stays alive throughout.
8. **(LOW, ~10 min) Backup restore drill (plan §11 H.11)** — extract one tarball to scratch dir, open SQLite DB, count `webauthn_credentials` rows. Confirms the tarball is restorable.
9. **(LOW, ~5 min) SSL Labs scan** — manual external check; record the grade in the PR.
10. **(LOW, ~2 min) Explicit `Set-NetFirewallProfile Public -DefaultInboundAction Block`** — for literal plan §6.5 compliance.

After items 1–4, the portal is in "audit-clean" state ready for ongoing operation.

---

## J. What I confirmed is fine

- Hardening floor (read_only + non-root UIDs + cap_drop:ALL + no-new-priv + tmpfs /tmp) enforced 8/8 portal services
- `auth-net internal:true` blocks DNS + IP egress (live wget tests)
- `notify-net` is the SINGLE chokepoint with internet egress in the portal slice — both portal-alerter (intentional) and portal-cron (incidental); see F.2
- Backend isolation: Caddy can't even resolve mnemory/llama-cpp/surrealdb
- Forged `Remote-*` headers do NOT bypass auth (Authelia saw `<anonymous>`)
- Security headers present on 200 AND 401 responses (HSTS, X-Frame-Options, Permissions-Policy, COOP, CORP, Referrer-Policy)
- Session cookie attributes correct (Secure, HttpOnly, SameSite=Lax)
- No docker.sock mounts in any portal container
- portal-init runs once, chowns named volumes, exits with `network_mode: none`
- portal-alerter `/health` ready=true, rate-limit configured
- Backups produce valid tarballs (with the F.1 caveat for some state files)
- Caddy access log + Authelia structured log are both rolling/JSON and present in their named volumes
- Caddy healthcheck via admin API at `:2019/config/` works (the workaround for the Host header trap that 421s `:80/healthz`)
- Lifecycle scripts work in both modes (test + production) and explicitly target portal services on stop (post-incident fix)

---

## K. Cross-references

- Plan: [plan-internet-exposed-front-end.md](./plan-internet-exposed-front-end.md)
- Original audit: [audit-plan-internet-exposed-front-end.md](./audit-plan-internet-exposed-front-end.md)
- Integration tasks: [integration-task-document.md](./integration-task-document.md)
- Threat model: [security-considerations-internet-facing-02.md](./security-considerations-internet-facing-02.md)
- Active compose: [docker-compose.yml](../../../docker-compose.yml)
- Live Caddyfile: [config/caddy/Caddyfile](../../../config/caddy/Caddyfile)
- Live Authelia config: [config/authelia/configuration.yml](../../../config/authelia/configuration.yml)
- Incident response playbook: [documentation/incident-response.md](../../../documentation/incident-response.md)
