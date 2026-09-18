# sysadmin-mcp — ai-stack systems-administrator

A dependency-free (stdlib-only) MCP server + gated executors that let an AI **systems-administrator
persona** operate this stack through semantic, safety-gated tools. Capability #1 is disk-prune
(motivated by the 2026-07-26 near-full-C: incident). Design:
[../../../documentation-plans-ai-stack/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md](../../../documentation-plans-ai-stack/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md).

## Files
| File | Role |
|------|------|
| `server.py` | MCP stdio server (registered as `sysadmin` in repo `.mcp.json`) — 10 tools |
| `sysadmin.py` | read-only probes (disk_report, container/stack/logs/volume) |
| `executor.py` | gated safe reclaim (`reclaim_plan`/`reclaim_execute`) — idle+recency guarded, no volume ops |
| `compaction.py` | gated vhdx compaction (`compact_plan`/`compact_execute`/`compact_status`) |
| `compact-vhdx.ps1` | the elevated compaction body (runs as a RunLevel-Highest task) |
| `compact-lib.ps1` | pure decision helpers for the above (`Get-ReclaimVerdict`) — no elevation, no Docker, so the judgement is testable without 15 min of downtime |
| `test-compact-lib.ps1` | 17 checks on that verdict; case 1 is the real 2026-09-13 under-reclaim |
| `check_disk.py` | weekly detector → posts a `#sysadmin` alert when a threshold trips |
| `register-sysadmin-tasks.ps1` | ONE-TIME (elevated): registers the compaction task + weekly detector |
| `charter.md` | the @sysadmin persona charter (appended to the bridge's system prompt) |
| `sysadmin-bridge-launch.ps1` / `register-sysadmin-bridge.ps1` | run/register the persona (2nd bridge instance) |
| `config.json` | thresholds + machine facts + channel/operators |
| `test_*.py` | 102 tests (unit parsers + volume-age classification + live probes + stdio round-trip + fail-closed gates + source/ordering guards) — 36 + 42 + 24, run each file directly |

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

## Compaction: trim first, then judge the result (2026-09-13)
- **`fstrim` runs BEFORE the engine stop**, while the docker-desktop distro is up and the disk
  mounted. `Optimize-VHD` cannot read ext4 — it reclaims only blocks the guest has already
  discarded, and `/mnt/docker-desktop-disk` is mounted `rw,relatime` with no `discard`. Without
  the trim, a run returns whatever Docker Desktop's own periodic trim happened to mark: on
  2026-09-13 that was **9.9 GB of a measured 54.4 GB**, reported as success.
- **The result is judged against its own target.** `trapped_before_gb`, `fstrim_ok` and
  `shortfall_gb` are result fields, and `Get-ReclaimVerdict` sets `ok=false` with a named reason
  when the return misses proportionally (`-MinReclaimFraction`, default 0.5) *and* by more than
  `-ShortfallGraceGb` (default 5). Both conditions are required so a small target missed by a
  small amount is not an incident.
- **WARN and ACT are different numbers.** `vhdx_trapped_warn_gb` (60) decides when `disk_report`
  mentions compaction; `vhdx_compact_min_gb` (20) decides when `compact_execute` will run. They
  were the same key until 47.8 GB trapped left the stack simultaneously "HEALTHY" and refused.

## Safety notes
- Never `docker volume prune`; `volume_report` is report-only and flags protected data volumes.
- **A protected name is not proof of life.** `volume_report` splits `DO_NOT_PRUNE` (protected and
  recently written, or of unknown age) from `dangling_protected_cold` (protected name, no
  container references it, nothing written for `volume_orphan_cold_days`+). Cold entries are
  orphan *candidates*: verify contents against the live volume and back up before removing. If
  the age probe fails, nothing is classified cold — the conservative side.
- Reclaim clears only IDLE ao-worker `/tmp/lc-*.jsonl` (busy workers skipped); logs are truncated,
  not deleted; images/build-cache prune is dangling-only.
- Compaction takes the whole stack down ~10–15 min; it pauses/re-arms the health watchdog and
  verifies the stack returns. Only run in a quiet window (no active ao-worker effort).
- Runtime state (audit log, compaction result, alert throttle) lives in `state/` (gitignored).
