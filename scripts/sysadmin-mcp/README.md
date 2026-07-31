# sysadmin-mcp — ai-stack systems-administrator

A dependency-free (stdlib-only) MCP server + gated executors that let an AI **systems-administrator
persona** operate this stack through semantic, safety-gated tools. Capability #1 is disk-prune
(motivated by the 2026-07-26 near-full-C: incident). Design:
[../../documentation/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md](../../documentation/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md).

## Files
| File | Role |
|------|------|
| `server.py` | MCP stdio server (registered as `sysadmin` in repo `.mcp.json`) — 10 tools |
| `sysadmin.py` | read-only probes (disk_report, container/stack/logs/volume) |
| `executor.py` | gated safe reclaim (`reclaim_plan`/`reclaim_execute`) — idle+recency guarded, no volume ops |
| `compaction.py` | gated vhdx compaction (`compact_plan`/`compact_execute`/`compact_status`) |
| `compact-vhdx.ps1` | the elevated compaction body (runs as a RunLevel-Highest task) |
| `check_disk.py` | weekly detector → posts a `#sysadmin` alert when a threshold trips |
| `register-sysadmin-tasks.ps1` | ONE-TIME (elevated): registers the compaction task + weekly detector |
| `charter.md` | the @sysadmin persona charter (appended to the bridge's system prompt) |
| `sysadmin-bridge-launch.ps1` / `register-sysadmin-bridge.ps1` | run/register the persona (2nd bridge instance) |
| `config.json` | thresholds + machine facts + channel/operators |
| `test_*.py` | 71 tests (unit parsers + live probes + stdio round-trip + fail-closed gates + source guards) |

## Tools (surface)
Read-only: `disk_report`, `container_status`, `stack_health`, `container_logs`, `volume_report`,
`reclaim_plan`, `compact_plan`, `compact_status`.
Gated/mutating: `reclaim_execute(confirm_token)`, `compact_execute(confirm_token)`.

## Gating (belt + suspenders)
1. **Belt (human):** in a bridge session, mutating `mcp__sysadmin__*` tools fall through the
   existing fail-closed approval relay (`--permission-prompt-tool`) → operator approve/deny.
2. **Suspenders (deterministic, in-code):** plan-bound `confirm_token`, idle+recency worker checks,
   never-touch-volumes (source-guarded by tests), compaction requires warranted + registered task.

## Run the tests
```
python scripts/sysadmin-mcp/test_sysadmin.py     # read-only + stdio
python scripts/sysadmin-mcp/test_executor.py     # safe-reclaim gate (+ --live-exec for a real run)
python scripts/sysadmin-mcp/test_compaction.py   # compaction gate (non-destructive)
```

## Activate the weekly detector + arm compaction (one elevated run)
```
powershell -File scripts/sysadmin-mcp/register-sysadmin-tasks.ps1   # run elevated
```
Registers `AI-Stack Sysadmin Compact VHDX` (on-demand, RunLevel Highest) and
`AI-Stack Sysadmin Disk Check` (weekly Sunday 09:00).

## Activate the @sysadmin persona (operator prerequisites)
1. Create a Mattermost bot `bot-sysadmin`; add its token to `agent-org/docker/.env` as
   `SYSADMIN_MM_BOT_TOKEN=...`.
2. Create the `#sysadmin` channel; add bot-sysadmin + the operator; put its 26-char id in
   `config.json → sysadmin_channel_id`.
3. Run elevated: `powershell -File scripts/sysadmin-mcp/register-sysadmin-bridge.ps1`, then
   `schtasks /run /tn sysadmin-bridge`.

## Safety notes
- Never `docker volume prune`; `volume_report` is report-only and flags protected data volumes.
- Reclaim clears only IDLE ao-worker `/tmp/lc-*.jsonl` (busy workers skipped); logs are truncated,
  not deleted; images/build-cache prune is dangling-only.
- Compaction takes the whole stack down ~10–15 min; it pauses/re-arms the health watchdog and
  verifies the stack returns. Only run in a quiet window (no active ao-worker effort).
- Runtime state (audit log, compaction result, alert throttle) lives in `state/` (gitignored).
