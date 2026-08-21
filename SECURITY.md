# Security Configuration for AI Stack

Documents security posture, decisions, and known gaps. Last updated 2026-08-20.

---

## 0. 2026-08-20 posture changes (CLEANUP-PLAN v3 execution day)

- **Whole-repo mount into OWUI REMOVED.** `.:/host_project:ro` on the
  internet-facing frontend (which shipped `.env`, `secrets/`, tailscale
  certs, the GitHub App key into the container) is gone — replaced by three
  narrow read-only mounts (`status-pipe/`, `system-prompts/`,
  `data/tailscale/`). Verified in-container.
- **Watchtower RETIRED.** The workspace now has **zero** `docker.sock`
  mounts anywhere (previously one, on watchtower, with an unpinned `:latest`
  image and a second whole-repo mount). Updates are manual + verified per
  `documentation/runbooks/UPDATE-MANAGEMENT.md`.
- **Committed gateway key defanged; rotation DECLINED (operator, 2026-08-20).**
  The live `gw-…` Open Brain key was tracked in `.vscode/mcp.json` and
  `openbrain-gateway/smoke_test.py` — both untracked/env-indirected now. The
  operator explicitly declined rotating the key (it would break every
  external MCP client until re-keyed). **Accepted-risk posture:** the key
  remains valid AND present in git history (with the two 2026-08 `.env.bak`
  commits, ~25 live credentials each; scrub = CLEANUP-PLAN D-1, deferred).
  Standing conditions of that acceptance: the repo remote stays private, and
  the pre-commit `gw-` guard stays in force. Revisit before any change to
  either condition.
- **Pre-commit secret guard hardened**: the `gw-` gateway-key pattern was
  added (the one token class this repo actually leaked was the one the guard
  couldn't see). Hooks bootstrap for fresh clones:
  `git config core.hooksPath .githooks`.
- **LiteLLM master_key FLIPPED ON 2026-08-21 (J.1 executed):** per-caller
  `sk-` virtual keys enforced at the gateway; caller identity reaches
  llm-queue via the `x-ai-stack-caller` header injected by the pre-call hook
  (`config/litellm/custom_callbacks.py`). Verified: junk key → 401, virtual
  key → 200 with correct lane attribution. Runbook:
  `documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md`.
- SurrealDB image pinned by digest; its datastore still accepts the
  persisted first-boot root login (network isolation is the boundary) — a
  DEFINE USER rotation pass remains open.

---

## 1. Authentication & Secrets Management

### Environment Variables
- ✅ All sensitive values live in `.env` (gitignored)
- ✅ `.env.example` checked in as the schema
- ✅ `users_database.yml` (Authelia) gitignored; only the `.template` is tracked
- ✅ `secrets/google/portal-alerter/credentials.json` and `token.json` gitignored
- ✅ `config/authelia/.healthcheck.env` is a runtime-populated bind mount (also fine to track since Authelia overwrites at startup)
- ⚠️ Tailscale auth key rotation cadence: check expiry in Tailscale admin; rotate proactively

### Container Security (portal slice)
- ✅ Every portal container: `read_only: true`, `cap_drop: [ALL]`, `no-new-privileges: true`, `tmpfs /tmp`, non-root UID (10000–10007)
- ✅ Authelia exception: `read_only: true` works via a targeted `/app/.healthcheck.env` bind mount (the only writable path Authelia 4.39 needs at startup)
- ✅ Sidecars (watcher, tripwire, backups) run from CUSTOM Dockerfiles with deps pre-installed at build time — no `apk add` at runtime as a non-root UID
- ✅ Backup containers run with the SAME UID as the data owner (caddy-backup as 10000, authelia-backup as 10001) so they can read mode-0600 state files. Pre-2026-05-29 audit, UIDs were 10004/10005 and SILENTLY excluded `instance.uuid`, `locks/`, `last_clean.json`, `notification.txt` from tarballs.
- ✅ No `docker.sock` mounts anywhere in the workspace (since 2026-08-20 — previously the root compose's watchtower held one; §0)
- ✅ `portal-init` one-shot container chowns the named volumes at portal-on; runs with `network_mode: none` + only `CHOWN/FOWNER/DAC_OVERRIDE` caps

---

## 2. Network Security

### Port Exposure (production mode)
- **Zero** host port bindings on portal services — Cloudflare Tunnel is the only ingress
- Backend services (`openwebui`, `llama-cpp`, `mnemory`, `surrealdb`, etc.) all bound to `127.0.0.1` only
- Pre-flight §6.1/§6.2 verified: `open_notebook` and `surrealdb` are LAN-unreachable

### Port Exposure (local-test mode)
- Caddy binds `127.0.0.1:8443` only (localhost) via `docker-compose.local-test.override.yml`
- `cloudflared` is not started — portal is NOT internet-reachable in this mode

### Docker network segmentation
- `auth-net` is `internal: true` — Authelia, watcher, tripwire have **no internet egress** (DNS resolution fails). Verified live 2026-05-29.
- `app-net` is the **only** path from Caddy to backends; Caddy cannot DNS-resolve `mnemory`, `llama-cpp`, `surrealdb`
- `notify-net` is the only egress chokepoint in the portal slice. portal-alerter (intentional) and portal-cron (incidental — see §5 below) can both reach the internet from here
- `edge-net` carries cloudflared ↔ caddy only

### Tailscale (unchanged from existing stack)
- Network namespace shared with OpenWebUI for `tailscale serve`
- Tailnet users see OpenWebUI's native login directly — no Authelia in the way (per plan §6.6 trust posture Option C, see §3 below)

---

## 3. Tailnet trust posture (plan §6.6 — Option C, accepted)

**Open Notebook has no native authentication.** The existing tailnet path at `tailscale serve --https=8443 -> open_notebook:8502` (configured in [entrypoint.sh](entrypoint.sh)) means **anyone on the tailnet can reach Open Notebook unauthenticated.**

**Decision (2026-05-28):** accept as-is. The implicit posture is that tailnet members are trusted equivalently to a single household user. Internet users still face full Authelia + Cloudflare Access gating; tailnet is the trusted-side bypass for the operator's own access.

If this trust model changes (e.g., adding tailnet users you don't fully trust), revisit:
- Option A: remove the Open Notebook tailnet serve from `entrypoint.sh`
- Option B: Tailscale ACL rule restricting which identities can hit port 8443

---

## 4. Cloudflare Tunnel — data exposure acknowledgment (plan §9.1)

**Cloudflare sees every byte of decrypted traffic** flowing through the tunnel: chat messages in OpenWebUI, documents uploaded to Open Notebook, API calls, cookies. Cloudflare's privacy policy and TOS apply.

**Decision (2026-05-28):** accepted in exchange for: DDoS absorption, bot filtering, free TLS at the edge, IP hiding (no home-IP exposure), no router port-forwarding.

**Additional layer (added by operator 2026-05-29):** Cloudflare Access policy on top of the tunnel, restricting access to email `Yamaoka01@gmail.com` via one-time PIN before any request reaches Authelia.

If the data-exposure trade-off becomes unacceptable, the alternatives are: tailnet-only (kill the portal) or self-hosted edge (port-forward with all the IP-exposure and DDoS costs).

---

## 5. Known defense-in-depth gaps (audit 2026-05-29 findings)

Documented for visibility, not blockers for v1 operation.

### 5.1 Caddy + portal-cron internet egress (LOW)
Both can reach arbitrary internet hosts via their non-`internal` bridge networks (edge-net and notify-net respectively). Cannot be closed without breaking cloudflared (needs edge-net egress to CF) or portal-alerter (needs notify-net egress to Gmail). Compromise of Caddy or portal-cron → data exfiltration is theoretically possible. Mitigations already in place: read_only FS, non-root UID, cap_drop ALL, no-new-privileges. v2 candidate: dedicated outbound proxy with allowlist (like little-coder's `lc-egress` pattern).

### 5.2 `denoland/deno:alpine` is a moving tag (LOW)
Plan §12.1 says to pin Deno to a specific digest. Currently using the moving `:alpine` tag. Open follow-up: pin via `docker inspect denoland/deno:alpine` → use the digest.

### 5.3 Authelia `jwt_secret` env var name deprecated (LOW cosmetic)
Authelia 4.39 logs warnings; auto-mapped to `AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET`. Becomes an error in 5.0. Open follow-up: rename in `.env` and compose.

### 5.4 `@internal_health` Caddyfile matcher is dead code (INFO)
`forward_auth` runs before `handle` in Caddy's directive ordering, so `/healthz` returns 401 to all unauthenticated visitors. The remote_ip restriction was a no-op. Net effect still meets the plan's goal (don't leak liveness to scanners) via a different mechanism.

---

## 6. Logging & Monitoring

### Authentication audit trail
- Authelia `db.sqlite3` `authentication_logs` table records every login attempt (successful + failed) with timestamp, username, source IP, success bit
- Live verified: forged-credential attempts are recorded; regulation table fires bans after 3 fails in 2 min (config: `max_retries: 3, find_time: 2m, ban_time: 1h`)
- Authelia structured JSON log at `/data/authelia.log` (in `authelia-data` volume)
- Caddy JSON access log at `/data/caddy-access.log` (in `caddy-data` volume); rolls at 100 MiB / 7 files / 30 days

### Access monitoring (single source of truth)
- Full reference: [documentation/runbooks/monitoring-access.md](documentation/runbooks/monitoring-access.md)
- Operator tool: [scripts/portal/access-query.ps1](scripts/portal/access-query.ps1) for interactive review of recent activity (filters: Hours, Subdomain, Status, IP, UniqueIPs)
- `config/watcher/known-ips.txt` is the "trusted source IP" list. Auto-populated by `authelia-watcher` after the first new-IP alert; edit by hand to remove stale entries
- `tunnel-watcher` polls `cloudflared:2000/ready` every 30s and alerts HIGH after 3 consecutive failures (~90s default); INFO on recovery; hourly heartbeat to docker logs

### Real-time alerting (Gmail via portal-alerter)
- All operator alerts land in **`Yamaoka01@gmail.com`** via the `portal-alerter` Deno sidecar
- OAuth client: **dedicated** GCP OAuth 2.0 client (`portal-alerter`), separate from OB1's `open-brain-email` client. Revoking either side at https://myaccount.google.com/permissions does NOT affect the other.
- Refresh token: `secrets/google/portal-alerter/token.json` (gitignored)
- Alert triggers (from `authelia-watcher` + `integrity-tripwire`): regulation bans, new-IP login successes, repeated 1FA failures from same IP, WebAuthn/TOTP credential changes, config-file drift
- Scheduled traffic digest: `portal-cron` fires `POST /run` on the alerter daily 07:00 UTC by default

### Backup state
- **Full-stack coverage** (post-2026-05-30): nightly logical/tar backups for caddy, authelia, openwebui, mnemory, little-coder (5 volumes), smolcrawl, tailscale, openbrain-db (`pg_dump -Fc`), openbrain-wiki (volume tar), open-notebook (surreal export + notebook_data tar) — plus weekly lm-models tar (Sundays 01:00 UTC)
- Every backup writes a `.sha256` sentinel beside the archive; restore tooling verifies before touching anything
- Convention for new services: [documentation/runbooks/backup-conventions.md](documentation/runbooks/backup-conventions.md). Coverage check: `.\scripts\check-backup-coverage.ps1`
- Restore workflow: per-service in [documentation/runbooks/restore-from-snapshot.md](documentation/runbooks/restore-from-snapshot.md); disaster recovery via `.\scripts\restore-from-snapshot.ps1 -SnapshotRoot ... -Date ... -Apply`
- Off-host sync: `scripts/backup/backup-to-nas.ps1` runs weekly (Sundays 04:00 UTC), alternating slot-A/slot-B on the NAS using DPAPI-encrypted credentials (LocalMachine scope). Failures are now reported via portal-alerter (2026-05-30 alerter wiring fix — silent-failure bug closed)
- SurrealDB note: backups authenticate as `root/root` regardless of the `--user`/`--pass` startup args; SurrealDB v2 grants any caller these credentials when no `DEFINE USER` exists. This is acceptable because the surrealdb port is bound to 127.0.0.1 (not LAN-reachable)

---

## 7. Compliance & Best Practices

### Hardening floor (plan §2)
Every portal container honors: `read_only: true` + non-root UID + `cap_drop: [ALL]` + `no-new-privileges` + `tmpfs /tmp` + per-container `pids` limit. Audit-verified 2026-05-29.

### Image pinning
- `caddy:2.8.4-alpine` ✓ pinned
- `authelia/authelia:4.39` ✓ pinned
- `cloudflare/cloudflared:2024.10.0` ✓ pinned
- `denoland/deno:alpine` ⚠️ moving (see §5.2)
- `portal-*:local` ✓ locally built, immutable per build
- `watchtower` disabled for ALL portal containers (`com.centurylinklabs.watchtower.enable=false`)

### Pre-flight §6 follow-through
- ✅ §6.1 open_notebook ports 127.0.0.1
- ✅ §6.2 surrealdb port 127.0.0.1
- ✅ §6.3 SurrealDB creds via env (`SURREAL_USER=root`, `SURREAL_PASSWORD=root` — kept by operator's decision; LAN-unreachable so creds aren't an attacker-reachable surface)
- ✅ §6.4 `OPEN_NOTEBOOK_ENCRYPTION_KEY` via env (existing value preserved)
- ✅ §6.5 Windows Defender Public profile default-deny inbound (NotConfigured = Windows default = Block; recommended to make explicit via `Set-NetFirewallProfile Public -DefaultInboundAction Block`)
- ✅ §6.6 tailnet trust posture documented above (Option C)
- ✅ §6.7 OpenWebUI trusted-header SSO disabled (no `WEBUI_AUTH_TRUSTED_*` env)
- 🟡 §6.8 **second WebAuthn key / TOTP recovery offline — PENDING.** Cannot be done in local-test mode (Authelia /settings/two-factor needs HTTPS cookies which require Cloudflare). Required before relying on production exclusively. **Single-credential lockout is the highest active risk.**
- ✅ §6.9 portal-alerter dedicated OAuth client provisioned

---

## 8. Incident Response

### Emergency stop (portal only)
```powershell
.\scripts\breach-killswitch.ps1     # email final notice, stop portal, snapshot logs, rotate Authelia JWT
```
Distinct from `portal-off.ps1` (planned downtime, no secret rotation). See [documentation/runbooks/incident-response.md](documentation/runbooks/incident-response.md) for the full playbook.

### Planned shutdown (any mode)
```powershell
.\scripts\portal-off.ps1     # stops the 10 portal services explicitly. Does NOT use `docker compose down`
                             # which would stop the whole ai-stack project (verified by hard lesson 2026-05-29).
```

### Routine operation
```powershell
.\scripts\portal-on.ps1 -Test     # local development mode (no internet exposure)
.\scripts\portal-on.ps1           # production (with cloudflared)
.\scripts\portal-status.ps1       # read-only health check
```

---

## 9. Open follow-up items

In approximate priority order:

1. **HIGH:** Enroll second WebAuthn credential or print TOTP recovery secret offline (§6.8). Requires production mode.
2. ~~**MEDIUM:** Sync `./backups/` off-host (daily)~~ ✅ DONE 2026-05-30 — NAS sync via two-slot weekly rotation; alerter silent-failure bug fixed.
3. **LOW:** Pin `denoland/deno:alpine` to a digest (§5.2).
4. **LOW:** Rename `AUTHELIA_JWT_SECRET` env to `AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET` (§5.3).
5. **LOW:** Make Windows firewall Public profile explicit Block (§6.5).
6. **LOW:** Clean up 4 orphaned Docker volumes (`openwebui_data`, `openwebui_sessions`, `tailscale_state`, `tailscale-state`) flagged by `check-backup-coverage.ps1`. Confirm contents are not needed, then `docker volume rm <name>`.
7. **LOW (~90 days post-launch):** Evaluate HSTS preload submission per plan §H.1.

---

## 10. References

- Implementation plan: [documentation/implementation-guide/open-source authentication front ends for ai stack/plan-internet-exposed-front-end.md](documentation/implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/plan-internet-exposed-front-end.md)
- Post-implementation audit (2026-05-29): [documentation/implementation-guide/open-source authentication front ends for ai stack/audit-post-implementation-2026-05-29.md](documentation/implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/audit-post-implementation-2026-05-29.md)
- Incident response playbook: [documentation/runbooks/incident-response.md](documentation/runbooks/incident-response.md)
- Backup conventions (new services): [documentation/runbooks/backup-conventions.md](documentation/runbooks/backup-conventions.md)
- Restore workflow: [documentation/runbooks/restore-from-snapshot.md](documentation/runbooks/restore-from-snapshot.md) + [scripts/backup/restore-from-snapshot.ps1](scripts/backup/restore-from-snapshot.ps1)
- Active compose: [docker-compose.yml](docker-compose.yml)
- Live Caddyfile: [config/caddy/Caddyfile](config/caddy/Caddyfile)
