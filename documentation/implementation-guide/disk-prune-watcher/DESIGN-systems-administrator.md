# DESIGN — Systems-Administrator Agent (ai-stack)

Status: BUILDING — 2026-07-26. An interactive AI "systems-administrator" persona that operates the
ai-stack through a gated `sysadmin` MCP server. Disk-prune (the 2026-07-26 near-full-C: incident,
memory `[[docker-disk-bloat-ao-worker-tmp]]`) is **capability #1**. Supersedes the script-only
approach in `DESIGN-disk-prune-watcher.md` (kept for the disk capability's threshold/flow detail).

## 1. Architecture (operator-specified)

```
                         #sysadmin  (Mattermost)
                              │  chat + approvals
                  ┌───────────▼───────────┐
                  │  sysadmin PERSONA      │   2nd claude-sessions-bridge instance
                  │  (headless claude -p)  │   own bot (bot-sysadmin), own lock port,
                  │  investigative + acts  │   sysadmin charter
                  └───────────┬───────────┘
             calls gated tools │ mcp__sysadmin__*
                  ┌───────────▼───────────┐        ┌──────────────────────────┐
                  │   sysadmin-mcp SERVER  │        │  approvals MCP (existing)│
                  │  (host python, stdlib) │◄──────►│  permission relay,       │
                  │  READS: safe, anytime  │  gate  │  fail-closed (bridge)    │
                  │  WRITES: gated+determin│        └──────────────────────────┘
                  └───────────┬───────────┘
        docker / wsl / schtasks (host)   elevation via RunLevel-Highest task
```

**Two-layer gating (belt + suspenders):**
1. **Belt (human):** the bridge spawns the persona with `--permission-prompt-tool
   mcp__approvals__permission_prompt`. Any *mutating* `mcp__sysadmin__*` tool that isn't pre-allowed
   falls through to the existing fail-closed approval relay → a `🛑 Approval needed` post in the
   thread → operator `approve`/`deny`. No new approval server needed.
2. **Suspenders (deterministic, in the MCP):** mutating tools enforce their own invariants
   regardless of what the AI asks — idle-worker checks before clearing `/tmp`, **never**
   `docker volume prune`, protected-volume list, `dry_run` default, and a `confirm_token` for the
   elevated compaction. The determinism lives in the tool, so the AI's judgement can't bypass it.

**Elevation boundary:** the MCP runs as a normal (non-elevated) host process. The one elevated
action (vhdx compaction) is a pre-registered **Scheduled Task with RunLevel=Highest** — runs
elevated with no UAC. The MCP's `compact_vhdx` tool only *triggers* it (`schtasks /run`) after the
approval gate; the task body is today's validated `compact-docker-vhdx.ps1`, generalized.

## 2. Why MCP-centric (operator rationale)

Separation from the repo + semantic interaction: the persona reasons in terms of `disk_report` /
`reclaim_safe` / `compact_vhdx`, not repo paths and shell. The MCP is the stable, gated, testable
seam; the persona is swappable/scale-out; determinism and safety are centralized in the tools.

## 3. Tool surface

**READ-ONLY (built, `server.py`, safe anytime — no gate):**
- `disk_report` — C:/D: free, vhdx allocated-vs-used (trapped/compactable), `docker system df`,
  top containers by writable layer, ao-worker `/tmp` bloat, oversized logs → verdict
  (healthy/attention/critical) + recommended_action.
- `container_status` / `stack_health` — container states; flags exited/restarting/unhealthy.
- `container_logs` — tail a container's logs.
- `volume_report` — volumes + dangling set, **flags protected data volumes; report-only**.

**MUTATING (planned, `executor.py`, gated):**
- `reclaim_safe(dry_run=True, workers=?)` — clear idle ao-worker `/tmp/lc-*.jsonl`, truncate
  `*-json.log` > threshold, `docker image/builder prune`. Dry-run reports what *would* free. Real
  run is a gated call; skips busy workers; never touches volumes.
- `compact_vhdx(confirm_token)` — trigger the elevated compaction task after approval; verifies the
  stack returns (running-container count) before declaring success; re-arms `TailscaleHealthCheck`.
- (future) `restart_container(name)`, `truncate_logs(name)`, targeted repairs — each gated.

## 4. Persona (2nd bridge instance)

Run `bridge.py` as a second Scheduled Task with env: `BRIDGE_CHANNEL_ID=<#sysadmin>`,
`BRIDGE_TOKEN_KEY=SYSADMIN_MM_BOT_TOKEN` (a `bot-sysadmin` identity), a distinct
`BRIDGE_LOCK_PORT` (≠ 48291), `BRIDGE_OPERATORS`, and a sysadmin **charter**. Requires a small,
backward-compatible tweak to `bridge.py` to make the appended system prompt env-configurable
(e.g. `BRIDGE_APPEND_PROMPT` / a charter file) — today `REMOTE_NOTE` is a module constant. The
repo `.mcp.json` `sysadmin` entry rides into the persona's turns automatically (bridge does not use
`--strict-mcp-config`).

## 5. Capability roadmap (this is capability #1 of many)

1. **Disk-prune** (in progress) — detector + safe reclaim + gated compaction.
2. Backup-freshness verification (backup sidecars can exit-0 with zero artifacts — memory
   `[[ai-stack-observability-audit]]`).
3. Cert / OAuth expiry (the 7-day digest OAuth death — `[[daily-digest-oauth-7day-expiry]]`).
4. Container restart-loop / unhealthy watch + gated targeted restart.
5. GPU reset trigger; WSL memory-pressure watch (`[[oom-incident-wsl-kernel-wedge]]`).
6. Image-update review (watchtower is auto; make it gated/reviewed for core services).

Each = a detector (read-only tool) + optional gated executor tool, same substrate.

## 6. Scheduled trigger (capability #1)

Weekly Sunday Windows Scheduled Task (Limited) runs a small check that calls `disk_report`; if the
verdict ≠ healthy it posts to `#sysadmin` and (Option B, from the disk-prune doc) wakes the persona
to investigate + propose the gated action. On approval the persona calls `reclaim_safe` /
`compact_vhdx`. Throttled so a pending request doesn't re-post.

## 7. Build status (2026-07-26)

- [x] `scripts/sysadmin-mcp/{server.py,sysadmin.py,config.json}` — 5 read-only tools.
- [x] `executor.py` — gated `reclaim_plan`/`reclaim_execute` (idle+recency guarded, token gate, no volume ops).
- [x] `compaction.py` + `compact-vhdx.ps1` + `register-compaction-task.ps1` — gated
      `compact_plan`/`compact_execute`/`compact_status`; elevated body runs as a RunLevel-Highest task,
      pauses/re-arms the watchdog, verifies the stack returns. **Task not yet registered (needs 1 UAC).**
- [x] Tests: `test_sysadmin.py` 26, `test_executor.py` 24 (incl. real safe reclaim), `test_compaction.py` 21
      (source guards, deterministic tokens, fail-closed gates proven in-proc AND over MCP JSON-RPC). **71/71.**
- [x] Registered `sysadmin` (10 tools) in repo `.mcp.json`.
- [x] Registered elevated compaction task + weekly detector (`register-sysadmin-tasks.ps1`, 1 UAC) —
      both `Ready`. `compact_execute` armed; `compact_plan` now warranted (trapped ~151 GB).
- [x] Weekly detector `check_disk.py` — posts `#sysadmin` alert on threshold, throttled; validated it
      caught the real regrowth (C: 195→51 GB, ao-worker-1 /tmp 92 GB / 1099 files over ~4 days).
- [x] Validated a REAL gated safe reclaim on live bloat: cleared idle ao-worker-1 (92.4 GB), skipped
      busy ao-worker-2, truncated 2.3 GB log — ~94.7 GB freed inside the vhdx.
- [x] Parameterized `bridge.py` charter (`BRIDGE_APPEND_PROMPT`/`BRIDGE_CHARTER_FILE`) — verified
      byte-identical to today when unset (running bridge unaffected).
- [x] Persona scaffolding: `charter.md`, `sysadmin-bridge-launch.ps1`, `register-sysadmin-bridge.ps1`,
      `README.md`. **Activation NEEDS operator:** create `bot-sysadmin` (→ `SYSADMIN_MM_BOT_TOKEN` in
      `agent-org/docker/.env`) + `#sysadmin` channel (→ `config.json sysadmin_channel_id`), then run
      `register-sysadmin-bridge.ps1`.
- [x] Prevention — **log caps (100m×5)** added to the 4 noisy services (`watchtower`, `open_notebook`
      in `docker-compose.yml`; `openbrain-wiki-viewer` in `OB1/docker/docker-compose.yml`;
      `iks-notebook` in the iks-dev compose) — **apply on next recreate**. Stack-wide default = TODO.
      **ao-worker `/tmp` sweep** (`sweep_tmp.py` + `sweep_old_tmp()`): daily task, deletes only
      lc-*.jsonl older than N days (never the active turn's file) on running workers.
- [x] Compaction-script bug found+fixed during first real run: `$PSScriptRoot` is empty in a
      PowerShell 5.1 `param()` default → `compact-vhdx.ps1` exited 1 before writing anything (stack
      never touched — caught because the result file was missing). Now resolves the script dir in the
      body. Re-triggered successfully.
- [ ] Docs/inventory: stack-map note for the compose log-cap edits; recovery-script awareness of the
      new tasks (below).

## 9. Scheduled tasks (registered by register-sysadmin-tasks.ps1)
| Task | Trigger | Runs as | Action |
|------|---------|---------|--------|
| `AI-Stack Sysadmin Compact VHDX` | on-demand (`schtasks /run`) | Highest | `compact-vhdx.ps1` |
| `AI-Stack Sysadmin Disk Check` | weekly Sun 09:00 | Limited | `check_disk.py` |
| `AI-Stack Sysadmin Tmp Sweep` | daily 04:00 | Limited | `sweep_tmp.py` |
| `sysadmin-bridge` (persona) | at logon | Limited | `sysadmin-bridge-launch.ps1` (after operator sets bot+channel) |

## 8. Safety invariants (non-negotiable)

- Reads are always safe; writes always gated (belt) AND deterministic-guarded (suspenders).
- **Never** `docker volume prune`; protected-volume list enforced in code.
- ao-worker `/tmp` cleared only when that worker is idle.
- Compaction only on explicit approval + `confirm_token`, pauses/re-arms the health watchdog, and
  verifies the stack returned before declaring success.
- `bypassPermissions` remains a hard floor in the bridge; the persona cannot self-approve.
- Every mutating action logs an audit line + posts a result summary to `#sysadmin`.
