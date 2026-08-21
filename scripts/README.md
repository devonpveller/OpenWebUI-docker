# scripts/ — the host-side ops plane

> Rewritten 2026-08-20; physical bucket-reorg EXECUTED 2026-08-21 (the
> elevated session re-registered the two path-bound Scheduled Tasks:
> `StackWatchdog` — renamed from `TailscaleHealthCheck` — and the NAS
> backup task).

## Subsystems (self-contained directories)

| Dir | What | Runs as |
|---|---|---|
| `claude-sessions-bridge/` | Mattermost ⟷ headless Claude Code sessions (+ approval relay MCP, session tools, self-restart) | Scheduled Task `claude-sessions-bridge` (logon, lock :48291) |
| `sysadmin-mcp/` | Sysadmin MCP server (disk/compaction/reclaim), Telegram out-of-band channel, scheduled disk/tmp/backup checks | `.mcp.json` stdio + 6 Scheduled Tasks (see its README) |
| `mattermost-mcp/` | Dependency-free Mattermost MCP server + `mm.py` CLI | `.mcp.json` stdio |
| `lib/` | Shared code: `mm_lib.py` (.env credential mechanics — the once-6×-copied line-walk), `portal-alerter-client.ps1`, `stack-services.json` (inventory; wire-or-demote = CLEANUP-PLAN D-12) | imported |
| `archive/` | Retired code with provenance table — see `archive/README.md` | never |

## `recovery/`

`emergency-recovery.ps1` (canonical; `recover`/`nuclear`/`gpu-reset`; now
pins CWD to the repo root — it was silently CWD-dependent before),
`emergency-recovery.bat` (legacy twin), `quick-fixes.bat` (interactive menu),
plus the Python primitives they drive (`namespace_reset.py`,
`nuclear_option.py`, `rebuild_tailscale.py`, `restart_openwebui.py`,
`gpu_check.py`, `status_check.py` — all locate the repo by walking up to
docker-compose.yml) and `update-stack.bat`.

## `checks/`

- `stack-watchdog.ps1` — the 60 s watchdog (Scheduled Task `StackWatchdog`;
  renamed from check-tailscale-health 2026-08-21). Covers: tailnet serves,
  all three compose projects, Docker-engine restart, backup recency,
  claude-bridge health, Telegram alerting. Log stays at
  `logs/tailscale-health.log` for continuity.
- `check-openbrain-health.ps1`, `check-agent-org-health.ps1` — per-project
  probes (fanned out from the watchdog).
- `check-backup-coverage.ps1` — every stateful path has a sidecar (manual).
- Pre-commit (via `.githooks/`): `check-staged-secrets.ps1`,
  `validate-lineendings.ps1`, `check-llm-gateway-routing.ps1`.
- `test-quartz4-offline.ps1`, `dev-helper.ps1` — manual dev aids.

## `portal/`

`portal-on.ps1` / `portal-off.ps1` / `portal-status.ps1`,
`breach-killswitch.ps1`, `access-query.ps1`.

## `backup/` (host side — NAS mirror + DR; container-side sidecar scripts live in ../backup/)

`backup-to-nas.ps1` (weekly NAS mirror; Task via
`install-nas-backup-task.ps1`), `set-nas-credential.ps1`,
`restore-from-snapshot.ps1` (DR driver). Container-side sidecar scripts live
in `../backup/`; conventions in
`../documentation/runbooks/backup-conventions.md`.

## Notifications

`notify-mattermost.sh` — Claude Code Stop/Notification hook target (posts to
#claude-code; per-session allowlist `scripts/.mm-notify-sessions`).

## Rules

- Scheduled-task entry points must not move without re-registering the task
  in the same change (needs elevation).
- Subsystem `state/` dirs are gitignored runtime state — a `git mv` of the
  subsystem leaves them behind; move manually and re-point configs.
- Container-name inventories: the recovery scripts + watchdog carry them
  inline; keep them in sync via the container rule (CLAUDE.md).
