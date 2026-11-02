# Security Configuration for AI Stack

Documents security posture, decisions, and known gaps. Last updated 2026-11-01.

**2026-11-01: the workstation portal plane is retired** (edge-b5-portal-retire): Caddy,
Authelia, cloudflared and their sidecars (watchers, tripwire, `portal-alerter`, `portal-cron`,
backups). Its code and config are archived in `scripts/archive/portal-caddy-authelia/`. The
internet edge - a Cloudflare tunnel, Cloudflare Access with Authentik as its login, Traefik and
Authentik - runs on the Proxmox nodes and is managed outside this repo. Each ai-stack service is
reached from there only through its own opt-in `<SERVICE>_LAN_IP` door (section 2), so nothing on
this host takes traffic from the internet. The portal sections below were removed; their
2026-05 history is in the archive and in the plan store's audit (section 10).

---

## 0. 2026-08-20 posture changes (CLEANUP-PLAN v3 execution day)

- **Whole-repo mount into OWUI REMOVED.** `.:/host_project:ro` on the
  internet-facing frontend (which shipped `.env`, `secrets/`, tailscale
  certs, the GitHub App key into the container) is gone — replaced by three
  narrow read-only mounts (`frontend/status-pipe/`, `frontend/system-prompts/`,
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
  (`inference/config/litellm/custom_callbacks.py`). Verified: junk key → 401,
  virtual key → 200 with correct lane attribution. Runbook:
  `../documentation-plans-ai-stack/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md`.
- SurrealDB image pinned by digest; its datastore still accepts the
  persisted first-boot root login (network isolation is the boundary) — a
  DEFINE USER rotation pass remains open.

---

## 1. Authentication & Secrets Management

### Environment Variables
- ✅ All sensitive values live in `.env` (gitignored)
- ✅ `.env.example` checked in as the schema
- ✅ `secrets/` stays gitignored. `secrets/google/portal-alerter/` (the retired alerter's Gmail OAuth files) may still be on a host; nothing reads it since 2026-11-01 - revoke that OAuth client in the Google account and delete the directory when convenient
- ⚠️ Tailscale auth key rotation cadence: check expiry in Tailscale admin; rotate proactively
- **Research front end sign-in (rw-identity, rw-identity2)** - shipped inert: with no
  `WORKSPACE_FORWARD_AUTH_*` in `OB1/docker/.env` the workspace is one default user, as before.
  Configured, Authentik signs users in and out at the operator's Traefik (forward-auth, a proxy
  provider); the workbench keeps no login, session or cookie and verifies the outpost's
  `X-authentik-jwt` on every request: HS256 only (Authentik signs a proxy provider's token with its
  client secret), `iss`, `aud` (the client id), `exp`. `WORKSPACE_FORWARD_AUTH_CLIENT_SECRET` is that
  client secret: whoever holds it can mint a sign-in, so it lives only in `OB1/docker/.env` (rotate it
  in Authentik if it leaks). The unsigned `X-authentik-*` headers are never trusted; Traefik must
  strip any client-sent `X-authentik-*` before the forward-auth check. `WORKSPACE_DOOR_KEY` (required
  then) is the key the viewer's forwarder injects for browsers; `MCP_ACCESS_KEY` becomes an internal
  service with no user front end. An admin (`WORKSPACE_FORWARD_AUTH_ADMIN_GROUP` in the token, read
  on every request; a removal takes effect within the provider's token validity) can impersonate a
  user only after giving a reason; the switch and every request during it go to the append-only
  `admin_audit` table (readable at `/workbench/auth/audit`, admins only). **Edge-strip rule:** while
  `WORKSPACE_IDENTITY_HEADER` is used (no sign-in) every proxy in front of the workbench must delete a
  client-sent copy of that header and set it itself; with sign-in on the workbench ignores the header
  entirely, and the viewer's forwarder drops it from every client request. The viewer's forwarder is
  the only browser door to `/workbench/*` (the portal Caddy route that injected `MCP_ACCESS_KEY` was removed in
  rw-identity-harden, and the portal itself retired 2026-11-01). Details: `OB1/docker/workbench/README.md` "Sign-in (Authentik forward-auth)".
- **agent-bridge HTTP tokens (ao-auth, 2026-10-07)** - `AO_OPERATOR_TOKEN` and `AO_WORKER_TOKEN`
  in `agent-org/docker/.env`. Before them every route on `agent-bridge:8000` (operator verbs via
  `POST /nl`, gate decisions, the kill switch, lane moves) answered anyone who could reach it,
  the worker command sandboxes `ao-ot-1/2` included.

  | Token | Accepted on | Held by | Unset / wrong |
  |---|---|---|---|
  | `AO_OPERATOR_TOKEN` | every bridge route except `GET /health` (the one table: `ROUTE_ACCESS` in `agent-org/agent-bridge/app/main.py`) | `agent-bridge`; host tooling on `127.0.0.1:8830` (gym runner, `issue_ops.py`, `disk-guard.ps1`, curl) as `Authorization: Bearer` | unset: those routes refuse **503** (fail closed); wrong/missing: **401**; the worker token there: **403** |
  | `AO_WORKER_TOKEN` | `POST /hook/floor-check` only | `agent-bridge`, `ao-worker-1/2` - never an `ao-ot` sandbox | the floor hook blocks irreversible actions (fail closed) |

  Equal values are refused (both discarded). `ao-ot-1/2` are also off `ai-stack_llm-net`, so a
  worker command cannot open a connection to the bridge at all.
  **Key rotation:** both tokens are on the rotation list - new values in `agent-org/docker/.env`,
  `--force-recreate` `agent-bridge` + `ao-worker-1/2`, then any copy host tooling holds; rotate
  at once if either is ever seen in a log, a chat post, or an `ao-ot` environment.
- **little-coder daemon token (ao-dauth, 2026-10-07)** - `LC_DAEMON_TOKEN`. Before it every
  route on a little-coder daemon (`:8090`: run a task, `/tasks/{id}/confirm`, `/admin/approve`,
  `/project`, `/admin/shutdown`) answered anyone who could reach it - for the agent-org
  workers, that included `ao-ot-1/2`, where the worker's own shell commands run.

  | Token | Accepted on | Held by | Unset / wrong |
  |---|---|---|---|
  | little-coder daemon token (a FILE, not an env var) | every little-coder daemon route except `GET /health` (the one table: `ROUTE_ACCESS` in `little-coder/src/littlecoder/daemon_auth.py`) | two files, two values: `agent-org/agent-bridge/secrets/lc-daemon-token` (mounted root-only into `ao-worker-1/2`; read by `agent-bridge`) and `secrets/little-coder/daemon-token` (mounted root-only into `little-coder`; Open WebUI's `little_coder` pipe valve `daemon_token`) - never `ao-ot-1/2`, `open-terminal` or `lc-egress` | missing/blank: those routes refuse **503** (fail closed); wrong/missing header: **401** |

  The daemon starts as root, reads `/etc/lc-secret/token` (inside a root `0700` directory) and
  drops to `lc` itself; from then on it is non-dumpable. The token is in no environment - not the
  container's configured env, so no `docker exec` process of any uid carries it - and the agent
  (uid `lc`) can read it from nothing: its env, `/proc/*` or the secret directory (measured with a
  shell, a concurrent `docker exec -u lc` and pi's own read tool, both layouts, ao-dauth round 3).
  It exists only in the file and in the daemon's memory. The workers set
  `LC_DAEMON_HIDE_FROM=ao-ot-N`, so neither the daemon (`:8090`) nor its metrics (`:9090`) listen on
  the network shared with the executor: a worker command cannot connect to them at all.
  **Key rotation:** on the rotation list - new content in the token file, `--force-recreate` the
  daemon(s) and their caller (`agent-bridge`; for the coder plane, update the pipe valve);
  rotate at once if it is ever seen in a log, a chat post, or an `ao-ot`/`open-terminal`
  environment.

### Container Security
- ✅ No `docker.sock` mounts anywhere in the workspace (since 2026-08-20 — previously the root compose's watchtower held one; §0)
- The portal slice's hardening floor (read-only roots, `cap_drop: [ALL]`, non-root UIDs) retired with it, 2026-11-01

---

## 2. Network Security

### Port Exposure
- No service on this host takes internet traffic; the edge is on the Proxmox nodes (see the top of this file)
- Backend services (`llama-cpp`, `surrealdb`, etc.) are bound to `127.0.0.1` only. The one exception is the opt-in LAN ingress (shipped off): with `OWUI_LAN_IP`, `MATTERMOST_LAN_IP`, `LITELLM_UI_LAN_IP` or `WIKI_VIEWER_LAN_IP` set, Open WebUI (`:3000`), Mattermost (`:8065`), the LiteLLM Admin UI (`:8445`) and the wiki viewer with the research workspace (`:8812`; it forwards `/workbench/*` to the workbench with the key server-side, never exposing a backend, and only for an allow-listed Host and, on a write, the viewer's own or an allow-listed edge Origin - `WIKI_VIEWER_ALLOWED_HOSTS` / `WIKI_VIEWER_ALLOWED_ORIGINS`, the CSRF and DNS-rebinding guard) also listen on that one LAN address, to be firewall-scoped to the Proxmox Traefik nodes before it is set (the cluster-transition B1 guard), with Authentik in front; `scripts/checks/check_lan_bindings.py` refuses any other binding
- Pre-flight §6.1/§6.2 verified: `open_notebook` and `surrealdb` are LAN-unreachable

### Docker network segmentation
- `app-net` (anchor) carries the app seam: openwebui (and the tailscale serve routes in its namespace) to the wiki viewer, workbench, Open Notebook and the LiteLLM Admin UI. `llm-net` stays `internal: true`; nothing gives `llm-gateway` an egress path
- The portal's `auth-net`, `edge-net` and `notify-net` retired with the portal, 2026-11-01

### Tailscale (unchanged from existing stack)
- Network namespace shared with OpenWebUI for `tailscale serve`
- Tailnet users see OpenWebUI's own login directly (per plan §6.6 trust posture Option C, see §3 below)
- The tailnet wiki route (`:8444`) goes straight to `openbrain-wiki-viewer:8080` over `app-net` (through the portal Caddy until 2026-11-01). The viewer injects the workbench key server-side; `/workbench/*` over the tailnet needs the tailnet host name in `WIKI_VIEWER_ALLOWED_HOSTS`, else 421. The header hygiene the portal Caddy did on that route is the viewer's own since OB1 `d166ed7` (edge-b5-portal-retire round 2): it drops every client-sent unsigned identity or proxy-chain header (`Remote-*`, `X-Forwarded-*`, `X-Real-IP`, `Forwarded`, ...) before forwarding - only the signed `X-authentik-jwt` passes, which the workbench verifies - and sets HSTS, `X-Frame-Options: DENY` and `nosniff` on every response; the workbench takes a change author from its identity seam only, never from a header

---

## 3. Tailnet trust posture (plan §6.6 — Option C, accepted)

**Open Notebook has no native authentication.** The existing tailnet path at `tailscale serve --https=8443 -> open_notebook:8502` (configured in [frontend/entrypoint.sh](frontend/entrypoint.sh)) means **anyone on the tailnet can reach Open Notebook unauthenticated.**

**Decision (2026-05-28):** accept as-is. The implicit posture is that tailnet members are trusted equivalently to a single household user. Internet users face Cloudflare Access + Authentik on the nodes' edge (Authelia until 2026-11-01); tailnet is the trusted-side bypass for the operator's own access.

If this trust model changes (e.g., adding tailnet users you don't fully trust), revisit:
- Option A: remove the Open Notebook tailnet serve from `frontend/entrypoint.sh`
- Option B: Tailscale ACL rule restricting which identities can hit port 8443

---

## 4. Cloudflare Tunnel — data exposure acknowledgment (plan §9.1)

**Decision (2026-05-28):** Cloudflare sees every byte of decrypted traffic through the tunnel,
accepted in exchange for DDoS absorption, bot filtering, free edge TLS, IP hiding and no router
port-forwarding. The tunnel moved off this host to the Proxmox nodes (the workstation
`cloudflared` retired 2026-11-01); the trade-off itself is unchanged and is now managed there.

---

## 5. Known defense-in-depth gaps (audit 2026-05-29 findings)

Documented for visibility, not blockers for v1 operation.

Sections 5.1-5.4 (Caddy and portal-cron egress, the moving `denoland/deno:alpine` tag of the
portal alerter, Authelia's deprecated `jwt_secret` name, a dead Caddyfile matcher) closed with
the portal's retirement on 2026-11-01: every one was a property of the archived portal plane.

---

## 6. Logging & Monitoring

### Authentication and access monitoring
- Internet sign-in and access logs live on the edge (Authentik events, Traefik and Cloudflare logs on the Proxmox nodes), outside this repo
- Retired 2026-11-01 with the portal: the Authelia/Caddy logs, `access-query.ps1`, `known-ips.txt`, `tunnel-watcher`, and the `portal-alerter` Gmail alerts and daily digest (all in `scripts/archive/portal-caddy-authelia/`)

### Backup state
- **Full-stack coverage** (post-2026-05-30): nightly logical/tar backups for openwebui, little-coder (5 volumes), tailscale, openbrain-db (`pg_dump -Fc`), openbrain-wiki (volume tar), open-notebook (surreal export + notebook_data tar) — plus weekly lm-models tar (Sundays 01:00 UTC). (smolcrawl was in this set until its service and backup were retired 2026-08-21; caddy and authelia until the portal retired 2026-11-01.)
- Every backup writes a `.sha256` sentinel beside the archive; restore tooling verifies before touching anything
- Convention for new services: [documentation/runbooks/backup-conventions.md](documentation/runbooks/backup-conventions.md). Coverage check: `.\scripts\check-backup-coverage.ps1`
- Restore workflow: per-service in [documentation/runbooks/restore-from-snapshot.md](documentation/runbooks/restore-from-snapshot.md); disaster recovery via `.\scripts\restore-from-snapshot.ps1 -SnapshotRoot ... -Date ... -Apply`
- Off-host sync: `scripts/backup/backup-to-nas.ps1` runs weekly (Sundays 04:00 UTC), alternating slot-A/slot-B on the NAS using DPAPI-encrypted credentials (LocalMachine scope). Failures are reported to Mattermost and Telegram (`scripts/sysadmin-mcp/mm_post.py`, `telegram_notify.py`); the portal-alerter leg retired with the portal, 2026-11-01
- SurrealDB note: backups authenticate as `root/root` regardless of the `--user`/`--pass` startup args; SurrealDB v2 grants any caller these credentials when no `DEFINE USER` exists. This is acceptable because the surrealdb port is bound to 127.0.0.1 (not LAN-reachable)

---

## 7. Compliance & Best Practices

### Hardening floor and image pinning
The portal-specific floor and its pinned `caddy` / `authelia` / `cloudflared` images retired with
the portal, 2026-11-01. Image pinning for the remaining planes is per plane (see each compose file).

### Pre-flight §6 follow-through
- ✅ §6.1 open_notebook ports 127.0.0.1
- ✅ §6.2 surrealdb port 127.0.0.1
- ✅ §6.3 SurrealDB creds via env (`SURREAL_USER=root`, `SURREAL_PASSWORD=root` — kept by operator's decision; LAN-unreachable so creds aren't an attacker-reachable surface)
- ✅ §6.4 `OPEN_NOTEBOOK_ENCRYPTION_KEY` via env (existing value preserved)
- ✅ §6.5 Windows Defender Public profile default-deny inbound (NotConfigured = Windows default = Block; recommended to make explicit via `Set-NetFirewallProfile Public -DefaultInboundAction Block`)
- ✅ §6.6 tailnet trust posture documented above (Option C)
- ✅ §6.7 OpenWebUI trusted-header SSO disabled (no `WEBUI_AUTH_TRUSTED_*` env)
- §6.8 (a second Authelia WebAuthn key) and §6.9 (the portal-alerter OAuth client) closed with the portal, 2026-11-01. The equivalent for the new edge - a second Authentik passkey or recovery code kept offline - is managed with Authentik on the nodes

---

## 8. Incident Response

The portal's killswitch, `portal-on.ps1` / `portal-off.ps1` / `portal-status.ps1` and its
incident-response runbook retired 2026-11-01 (the scripts are archived in `scripts/archive/portal-caddy-authelia/`; the incident-response and monitoring-access runbooks moved to the plan store as `journal/archive/portal-incident-response-runbook.md` and `portal-monitoring-access-runbook.md`).
An internet-side incident is handled at the edge on the Proxmox nodes: cut the tunnel or the
Cloudflare Access app there, then stop the affected service here with the driver
(`python scripts/stack/stack.py down <plane>`) or `scripts/recovery/emergency-recovery.ps1`.

---

## 9. Open follow-up items

In approximate priority order:

1. ~~**HIGH:** Enroll second WebAuthn credential (§6.8)~~ closed with the portal, 2026-11-01 (see §7).
2. ~~**MEDIUM:** Sync `./backups/` off-host (daily)~~ ✅ DONE 2026-05-30 — NAS sync via two-slot weekly rotation; alerter silent-failure bug fixed.
3. ~~**LOW:** Pin `denoland/deno:alpine` (§5.2); rename `AUTHELIA_JWT_SECRET` (§5.3)~~ closed with the portal, 2026-11-01.
4. **LOW:** Revoke the retired portal-alerter's Google OAuth client and delete `secrets/google/portal-alerter/` (§1).
5. **LOW:** Make Windows firewall Public profile explicit Block (§6.5).
6. **LOW:** Clean up 4 orphaned Docker volumes (`openwebui_data`, `openwebui_sessions`, `tailscale_state`, `tailscale-state`) flagged by `check-backup-coverage.ps1`. Confirm contents are not needed, then `docker volume rm <name>`.
7. **LOW (~90 days post-launch):** Evaluate HSTS preload submission per plan §H.1.

---

## 10. References

- Implementation plan (the retired portal; history): [../documentation-plans-ai-stack/journal/archive/implementation-guide/auth-front-end/plan-internet-exposed-front-end.md](../documentation-plans-ai-stack/journal/archive/implementation-guide/auth-front-end/plan-internet-exposed-front-end.md)
- Post-implementation audit (2026-05-29): [../documentation-plans-ai-stack/implementation-guide/open-source authentication front ends for ai stack/audit-post-implementation-2026-05-29.md](../documentation-plans-ai-stack/implementation-guide/open-source%20authentication%20front%20ends%20for%20ai%20stack/audit-post-implementation-2026-05-29.md)
- The retired portal's code and config: [scripts/archive/portal-caddy-authelia/](scripts/archive/README.md)
- Backup conventions (new services): [documentation/runbooks/backup-conventions.md](documentation/runbooks/backup-conventions.md)
- Restore workflow: [documentation/runbooks/restore-from-snapshot.md](documentation/runbooks/restore-from-snapshot.md) + [scripts/backup/restore-from-snapshot.ps1](scripts/backup/restore-from-snapshot.ps1)
- Active compose: [docker-compose.yml](docker-compose.yml)
