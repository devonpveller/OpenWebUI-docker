All verification is complete. Final message is the gate verdict.

## Verdict: RECOMMEND-MERGE

## Rubric
- Solves the issue INTENT (not merely tests-green): **pass** — a user-visible MM notification with flag timestamp, est start, and est completion for long-queued lanes; the issue's "MM, and/or OWUI" is satisfied by the MM lane, and the OWUI lane is honestly deferred as the OB1-submodule follow-up in both the CLEANUP-PLAN tick and the runbook.
- Evidence quality (failing→passing repro shown): **pass** — attempt-2's staleness is cured: the attempt-1 evidence block was removed, fixture asserts were regenerated at head against the real route contract (which I re-ran the logic of against `control.py`/`scheduler.py` — exact match), and the pwsh-7 smoke is explicitly disclaimed as not the PS 5.1 tier. RED/GREEN correctly ride the operator's merge window per the plan's own evidence assignment and GPU-load interlock — not a worker deficiency.
- Scope discipline (no drive-by changes): **pass** — exactly the 5 plan-sanctioned files; the fixtures beyond frontmatter `touched_paths` were pre-flagged as expected by the plan gate (gate-plan-25.md caveat 1); the CLEANUP-PLAN hunk touches only the K.10 lines.
- SERVICE-LIFECYCLE compliance (if service-shaped): **n/a** — no container added/changed/restarted; host-side script whose scheduled-task install is operator-gated and refuses to clobber an existing task.
- Security (secrets, gateway-only routing, branch policy): **pass** — token runtime-read from the gitignored env file (exact `notify-mattermost.sh` pattern); only the sanctioned GET-only `/observe/queue` operator lane is touched (mutating verbs never referenced); `$badPattern` in `check-llm-gateway-routing.ps1:49` fires only on `*-upstream` base/host assignments, which this diff never makes; PR targets `development`.

## Reasoning

Both prior deny causes are verifiably fixed in this diff:

1. **Attempt-1 (route vs helper shape):** the fixtures are now board-shaped and match the live contract key-for-key. `control.py:34-38` returns exactly `{models, held_total, max_total_connections}`; per-model `snapshot()` returns exactly `model/running/waiting/avg_T_s/P/permits_free/inflight_by_key` with waiting rows `id/key/prio/waited_s/est_wait_s` (`scheduler.py:284-303`). Both fixtures replicate this, multi-model, and `Get-FlaggedLanes` iterates `$Snapshot.models.PSObject.Properties` — never a top-level `waiting` — with the contract documented in the function header.
2. **Attempt-2 (PSCommandPath + stale evidence):** `Install-ScheduledTask` now uses `$PSCommandPath` with the rationale comment (a function-scoped `$MyInvocation.MyCommand.Path` would register the task with `-File ''`); the PR description's evidence block was regenerated at head and honestly quarantines the worker's pwsh-7 smoke run.

I independently re-derived the logic tier statically (script execution is approval-gated in this gate session, so I could not re-execute the transcript — noted): on the busy fixture, w1 (95.2+40.5=135.7s) and w2 (130+25=155s) flag at the 120s threshold, w3 (15s) does not → exactly the 2 claimed lanes (`owui-chat|qwen36-27b`, `little-coder|qwen36-27b`); the dedup round-trip is sound in PS 5.1 (`Save-DedupState` re-serializes with `.ToString('o')` — dodging the `/Date(...)/` ConvertTo-Json hazard — and `[DateTime]::Parse` of the Z-suffixed string converts to local, so elapsed math against `Get-Date` is correct across passes); the idle fixture takes the all-clear branch and drops state lanes. The known PS 5.1 traps are all handled: native-stderr-under-`-Stop` is bracketed around the `docker exec` (with `$LASTEXITCODE` checked), a corrupt state lane is log-and-skip rather than a mass re-notify, and Mattermost posting is fail-soft in every branch. MM mechanics match `notify-mattermost.sh:14,18-20,42-51` literally (same token var, env path, channel id, API URL, 8s timeout). State/log land under `logs/`, gitignored at `.gitignore:15`.

Residual nits, none gate-failing: the runbook's "lanes that clear are removed" is slightly stronger than the code (entries drop only on a fully-idle pass; a lingering entry is inert past the 10-min cooldown anyway); the "same as line 49" comment is off by one; the runbook's log-format line brackets the timestamp the code doesn't. Cosmetic.

**Merge-window conditions for the operator (restating the plan's own assignment, not new asks):** run the pre-commit hooks on the branch (that is where ASCII/no-BOM + line-endings + PSParser are actually enforced — the diff text cannot prove byte encoding), confirm the target channel before any `install-task` (the `#claude-code` default is a placeholder, per interlock 1), and execute the RED/GREEN priority-demo tiers in the coordinated window before pushing the merge. I could not cross-check head SHA `b058450` or the attached PS 5.1 transcript comment from this session (git/gh gated); if either is absent at merge time, treat this recommendation as void and re-gate.

## If DENY: orchestration adjustment plan
n/a — verdict is RECOMMEND-MERGE.
