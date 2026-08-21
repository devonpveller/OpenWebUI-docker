# DESIGN — Docker Disk Prune Watcher (weekly, Mattermost-gated)

Status: DESIGN DRAFT — 2026-07-26. Author: operator + Claude.
Motivated by the 2026-07-26 incident: C: fell to ~2 GB free. Root cause was **not**
volumes — it was **container writable layers** (two idle `ao-worker`s holding
117 GB + 37 GB of `/tmp/lc-pi-*.jsonl` little-coder session logs) plus an unrotated
18 GB `openbrain-wiki-viewer` json log. Reclaimed ~176 GB inside Docker, then
compacted the `docker_data.vhdx` (370 → 187 GB) to return 183 GB to C:.
See memory `[[docker-disk-bloat-ao-worker-tmp]]`.

## 1. Goal

A weekly (Sunday) watcher that measures Docker disk pressure, and **when a
threshold trips, posts a Mattermost message asking the operator to approve a
prune**. On approval it runs the same reclaim + optional vhdx compaction we did
by hand today — deterministically and safely. No automatic destructive action;
the operator is always the gate.

## 2. Feasibility verdict

**Feasible with existing infrastructure + 2 new Scheduled Tasks and 2 new scripts.**
Almost everything is already in place:

| Need | Already exists | Reuse |
|------|----------------|-------|
| Scheduled detector | `StackWatchdog` task → `scripts/checks/stack-watchdog.ps1` | Clone the pattern |
| Post to Mattermost | `scripts/notify-mattermost.sh` (bot token from `agent-org/docker/.env`, `#claude-code`) | Call directly |
| Throttled alerting | stale-backup alert logic in the health script | Copy the throttle idiom |
| Approval-from-MM → action | claude-sessions bridge (reply→resume) + agent-bridge `pending_approvals` table | See §5, Option A/B |
| The executor | today's `compact-docker-vhdx.ps1` (pause watchdog → stop Docker → `wsl --shutdown` → `Optimize-VHD` → restart → re-arm) | Generalize into `prune-execute.ps1` |

The **only** genuinely new problem is running the elevated compaction **without a
human clicking UAC**. Solved by a Scheduled Task registered with **RunLevel =
Highest** ("Run with highest privileges"): it runs with a full admin token and
**no UAC prompt**. Registering it needs admin once; triggering it later
(`schtasks /run`) does not.

## 3. What the watcher actually measures

Today's blow-up was container writable layers + logs + trapped vhdx space — *not*
volumes. So the detector tracks the real failure modes, not just "large volumes":

1. **C: free space** — warn `< 60 GB`, critical `< 25 GB`.
2. **Per-container writable layer** (`docker ps -a --size`) — flag any `> 40 GB`
   (this is what caught the ao-workers).
3. **Total container size** (`docker system df`) — flag `> 90 GB`.
4. **`ao-worker` `/tmp` session logs** — `du` of `/tmp/lc-*.jsonl`; flag a worker `> 20 GB`.
5. **Oversized container json logs** — any `*-json.log > 2 GB` (the wiki-viewer case).
6. **vhdx trapped space** — `docker_data.vhdx` allocated size − used-inside; flag gap
   `> 60 GB` (⇒ a compaction would pay off).
7. **Unused volumes** — total size, **report-only, NEVER auto-pruned** (the dangling
   list includes live named data volumes: `ai-stack_openwebui_data`,
   `mnemory_mnemory-data`, both `tailscale*state`, …).
8. **Reclaimable images / build cache** — report if `> 25 GB`.

Thresholds live in one config block (`scripts/disk-prune.config.psd1`) so tuning
doesn't touch logic.

## 4. Components

```
scripts/
  check-docker-disk.ps1     # DETECTOR  (weekly task, Limited) — measure, decide, post request
  prune-execute.ps1         # EXECUTOR  (on-approval task, Highest) — reclaim + optional compact
  disk-prune.config.psd1    # thresholds + toggles
  state/
    prune-pending.json      # open request: {nonce, created, tier, findings}  (gitignored)
    prune-last.json         # last run / throttle bookkeeping                (gitignored)
```
`prune-execute.ps1` = today's `compact-docker-vhdx.ps1` generalized:
- **Safe reclaim (no downtime)** — always the first tier:
  - Clear `/tmp/lc-pi-*.jsonl` + `/tmp/lc-ot-*.jsonl` on each ao-worker **only if that
    worker is idle** (`docker exec … ps` shows no active claude/git/build); busy workers
    are skipped and reported.
  - Truncate `*-json.log > 100 MB`.
  - `docker image prune -f` + `docker builder prune -f`.
  - **Never** `docker volume prune`; never touch named data volumes.
- **Compaction tier (has downtime)** — only if approved AND vhdx-trapped threshold met:
  pause `StackWatchdog`, stop Docker, `wsl --shutdown`, `Optimize-VHD -Mode Full`
  (fallback `diskpart compact vdisk`), restart Docker, wait for daemon, **verify the
  running-container count matches the pre-shutdown count**, re-arm the watchdog. Alerts
  to MM if any container fails to return.

## 5. The approval gate — the one design decision

A Mattermost message is posted with a short **nonce** and tiered choices, e.g.:

> ⚠️ **Disk prune check** — C: 22 GB free; `ao-worker-2` /tmp = 96 GB; vhdx trapped ≈ 140 GB.
> Reclaim now? Reply in-thread:
> `approve safe` (no downtime) · `approve compact` (adds vhdx compaction, ~10 min Docker down) · `skip`
> _(request `a1b9`; expires in 48 h)_

Three ways to turn that reply into execution — pick one:

- **Option A — reuse the claude-sessions bridge (fastest to ship).** Post into
  `#claude-sessions`; operator replies `approve compact`; the bridge resumes a
  headless `claude -p` that validates the nonce and runs `schtasks /run` on the
  elevated executor task. Pro: ~zero new infra. Con: an AI turn sits in a
  destructive path (mitigated — it only launches a fixed, self-contained task).

- **Option B — dedicated bounded poller (most deterministic, recommended).** After
  posting, the detector registers a **one-shot approval-poller** Scheduled Task that
  polls that MM thread every ~2 min for up to 48 h for `approve …`/`skip` + matching
  nonce (via `GET /api/v4/channels/{id}/posts`). On `approve` it triggers the elevated
  executor task and clears the pending file; on `skip`/timeout it clears and reports.
  No AI in the loop, no always-on service. Con: a little more code than A.

- **Option C — Mattermost slash command / outgoing webhook → tiny host HTTP trigger
  (cleanest long-term, most setup).** Register `/prune-approve <nonce> <tier>` in
  Mattermost pointing at a small host endpoint (host.docker.internal / tailnet) that
  validates and fires the executor. Event-driven, no polling. Con: new endpoint +
  MM slash-command registration.

**Recommendation:** ship **Option B** (deterministic, self-contained, matches the
existing "everything is a Scheduled Task" shape), with Option A as a manual
fallback since the bridge already exists.

## 6. Safety invariants (encode today's manual judgement)

- Operator approval is mandatory; nothing destructive runs unattended.
- **Nonce binds approval to the exact request**; expires 48 h; a stale/again-posted
  approval never fires.
- **Never** `docker volume prune`; named data volumes are untouchable.
- ao-worker `/tmp` cleared only when that worker is idle.
- Compaction only on explicit `approve compact`, only when the trapped-space threshold
  is met, always pausing/re-arming `StackWatchdog`, and it **verifies the stack
  came back** (container count) before declaring success.
- Throttle: if a request is already pending, the weekly check does not re-post.
- Every action is logged and a result summary is posted back to MM.

## 7. Prevention (reduces how often the watcher fires) — operator already approved

- **Container log caps:** add `logging: { driver: json-file, options: { max-size: "50m",
  max-file: "5" } }` to the noisy services (start with `openbrain-wiki-viewer`, then
  stack-wide via a compose default). ⚠️ Only applies to **newly created** containers →
  needs a recreate to take effect; schedule with a normal deploy, not mid-incident.
- **ao-worker `/tmp` sweep:** a lightweight daily cleanup (cron sidecar or a step in the
  safe-reclaim tier) removing `lc-*.jsonl` older than N days on idle workers. Better
  still, fix little-coder to cap/rotate its own `/tmp/lc-*.jsonl` at the source.

## 8. Work needed to build it

1. Write `disk-prune.config.psd1` (thresholds/toggles).
2. Write `check-docker-disk.ps1` (detector + MM post + pending/nonce state + throttle).
3. Generalize `compact-docker-vhdx.ps1` → `scripts/prune-execute.ps1` (safe tier + compact tier).
4. **[admin, one-time]** Register `AI-Stack Disk Prune Execute` Scheduled Task, RunLevel Highest.
5. Register `AI-Stack Disk Prune Check` weekly Sunday task (Limited).
6. Approval intake per §5 (Option B: the one-shot poller task).
7. Prevention (§7): log caps on recreate + `/tmp` sweep.
8. Document the two tasks + scripts; add state files to `.gitignore`.

## 9. Open decisions

- **Approval transport:** A / B / C (recommend B).
- **Channel:** `#claude-code` (current alert channel) vs a dedicated `#ops`/`#claude-sessions`.
- **Thresholds:** confirm the §3 numbers for this 930 GB C: machine.
- **Compaction cadence policy:** allow `approve compact` any Sunday, or only when
  trapped-space > threshold (default: only when it pays off).
