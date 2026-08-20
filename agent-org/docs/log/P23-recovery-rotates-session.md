# P23 — F1-redux: the AUTO-RECOVERY must start clean too (evidence: gym-021, 2026-07-22)

gym-021 validated the whole P19→P22 stack and converged the queue 15 → 2, then wedged on the last 2
tasks and escalated twice. The audit isolates one clean gap — the direct sibling of P21 F1.

## Evidence (gym-021)

A worker hung mid-turn, the stall watchdog re-engaged it (`stall_recovered`), it hung AGAIN, was
recovered AGAIN, then hit the cap and escalated (`stall_escalated`, ~26 min per cycle, twice):

```
stall_recovered: 2   stall_escalated: 2   (on the final 2 tasks; open never got below 2)
```

P21 F1 taught us EXACTLY this failure: an ABANDONED turn rots its session, so a re-engage that
reuses it returns EMPTY/hangs — the fix was to count `worker_turn_abandoned` in `_session_for` so a
re-engage after an abandon starts FRESH. But the **silent-worker recovery path (`stall_recovered`)
is not in that set**, so the watchdog re-engages a hung worker into the SAME (accumulated, 5-hours-
deep) session — and it hangs again. The recovery is doing the very thing F1 forbade for re-runs.

## Fix — F1-redux

Add `stall_recovered` to `_session_for`'s counted "failed-attempt-END" set (`orchestrator.py`,
alongside `worker_turn_abandoned`, `worker_plan_empty`, …). The `stall_recovered` event is logged
BEFORE `_reengage` dispatches, so counting it means the re-engage's `_session_for` returns a bumped
generation → a FRESH session. A silent-worker recovery then restarts from a clean context instead of
re-entering the rotted one, so it can clear the hang before it escalates (and before the "silent-hung
`computing` worker blocks its own re-run" second-order problem is ever reached).

## Alignment (checked)

- **Design.** §8 *"silence detection, not a timer"* — a silent worker is a hung worker; the recovery
  must not restart it in the context that hung it. §5 *"the environment remembers"* — a fresh session
  is the clean environment. §2.2 — a rotted, incoherent context *"oscillates and converges on
  nothing"* (the exact re-hang loop).
- **Research.** ANALYSIS-frontier-vs-small: small models suffer *"context rot on long context"* — so
  a recovery that reuses a 5-hour session is the failure mode; rotating is the remedy. No new gate,
  no human removed from any decision — pure reliability on the recovery path.
- This is literally F1 applied to the second recovery path; it inherits F1's alignment wholesale.

## Plan
1. Add `stall_recovered` to `_session_for`'s counted set (one line + comment).
2. Test: an effort with a `stall_recovered` event gets a bumped generation (fresh session) on its
   next dispatch — the F1 test, for the recovery path.
3. Full suite green → deploy → wipe arena → gym-022. Success: the silent-worker recovery clears the
   hang in a fresh session and the loop reaches an evidenced zero (`scope_completed`) instead of
   escalating on the last tasks.

Deferred (only if gym-022 still wedges): a silent-hung `computing` worker blocking its own re-run
(the busy-defer treating a hung turn as live work) — address if F1-redux alone doesn't clear it.
