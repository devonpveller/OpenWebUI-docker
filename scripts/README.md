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
| `lib/` | Shared code: `mm_lib.py` (.env credential mechanics — the once-6×-copied line-walk), `portal-alerter-client.ps1`, `stack-services.json` (inventory; hand-maintained, drift-verified by `check-project-configs.ps1`. The generator CLEANUP-PLAN D-12 asked for moved to the `stack-layers` plan in the plan store) | imported |
| `archive/` | Retired code with provenance table — see `archive/README.md` | never |

## `recovery/`

`emergency-recovery.ps1` (canonical; `recover`/`nuclear`/`gpu-reset`; now
pins CWD to the repo root — it was silently CWD-dependent before), plus `status_check.py`, a read-only overview
(`docker ps`, then per-container `docker exec`/`docker inspect` probes and the
`scripts/lib/stack-services.json` inventory - no compose project involved).
`quick-fixes.bat`, `update-stack.bat` and five orphaned Python helpers
were archived 2026-09-25 to `scripts/archive/legacy-recovery/` (see the
provenance row in `archive/README.md` for what replaces each).

`verify-recovery-gates.ps1` is the executable proof for `emergency-recovery.ps1`'s
health gates (ac-recovery-gates, 2026-09-25): it lifts the gate functions out with
the parser (never runs a recovery), drives them against a stubbed `docker`, and with
`-BaseRef <ref>` shows the pre-fix gate returning False for a healthy container.
`-Live` reads real health with `docker inspect` only; `-Live -Negative` adds one
throwaway `--network none` container that exits at once and is removed. It also
fails if a bare `docker compose` (no `-f`, so the zero-service root anchor) other
than `docker compose version` reappears in the script. The anchor itself is never
`up`-ed or `down`-ed (`up -d` on it exits "no service selected"; `down` would drop its
networks): `Confirm-AnchorNetworks` ensures them the way `stack.py` `ensure_networks()`
does, and the drill feeds both the same inputs and compares their `network create`
commands. `-Live` runs that ensure behind a guard that lets only the render and
`network inspect` reach docker, and checks the three network IDs are unchanged.
The legacy scripts that still issued bare `docker compose <service>` verbs
were retired by the follow-up item (ac-legacy-recovery) rather than fixed.

## `checks/`

- `stack-watchdog.ps1` — the 60 s watchdog (Scheduled Task `StackWatchdog`;
  renamed from check-tailscale-health 2026-08-21). Covers: tailnet serves,
  all eight compose projects in `scripts/lib/stack-services.json` (ai-stack,
  frontend, inference, memory, search, coder, open-brain, agent-org — the
  portal is driven separately), Docker-engine restart, backup recency,
  claude-bridge health, Telegram alerting. Log stays at
  `logs/tailscale-health.log` for continuity.
- `check-openbrain-health.ps1`, `check-agent-org-health.ps1` — per-project
  probes (fanned out from the watchdog).
- `check-backup-coverage.ps1` — every stateful path has a sidecar (manual).
- Pre-commit (via `.githooks/`), ten checks in this order:
  `check-staged-secrets.ps1`, `validate-lineendings.ps1`,
  `check-doc-placement.ps1`, `check-llm-gateway-routing.ps1`,
  `check-corpus-exposure-producers.ps1`, `check-project-configs.ps1`,
  `check-env-file-scope.ps1`, `check-ob1-recipe-tests.ps1`,
  `check-ob1-deno-recipes.ps1`, `check-ob1-integration-images.ps1` — then the
  hook appends an attestation line. `.githooks/pre-commit` is the authority.
- `test-quartz4-offline.ps1` — manual dev aid. (`dev-helper.ps1` was archived
  2026-09-25 to `archive/legacy-recovery/`: its compose check validated only the
  service-less root anchor.)

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

## One-time installers / alternates (checks/)

`install-service.ps1` (registers the watchdog as a service/task),
`simple-monitor.ps1` (non-admin loop alternative to the StackWatchdog task),
`setup-prereqs.ps1` (fresh-machine Docker/WSL2 prereq checks, self-elevating).
