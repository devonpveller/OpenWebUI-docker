# P25 — the drain loop must survive a transient unverifiable delivery (evidence: gym-023, 2026-07-23)

## Evidence (gym-023 audit)

gym-023 validated the P22 salvage path beautifully (BOTH `goal_alignment` and `project_documentation`
truncated, BOTH salvaged from the command stream, `swept=True`) and P21/P20/P17/P19 all reproduced.
Then it **closed prematurely after 5 of 16 tasks**, with no round-2 re-sweep and no `scope_completed`:

```
01:04:26 delivery 0 (initial goal)  → landed → re-sweep → 16 tasks → drain_dispatch #1
01:22:37 delivery 1 (task 1)        → landed → drain_dispatch #2   (+ test_count_regressed 67→42)
01:26:09 delivery 2 (task 2)        → landed → org_build_check pass → drain_dispatch #3
01:28:10 delivery 3 (task 3)        → landed → org_build_check pass → drain_dispatch #4
01:30:46 delivery 4 (task 4)        → landed → org_build_check pass → drain_dispatch #5
01:32:51 delivery 5 (task 5)        → UNVERIFIABLE → bare "(done)" closure, DRAIN SKIPPED
         11 tasks still queued · no drain_round #2 · no scope_completed · both workers suspended
```

The closure bot-pm posted is the tell — a bare echo of the worker's self-report, with none of the
drain-loop machinery gym-021 showed (gym-021 closed at *"Drain round 7 … 15 tasks open"* + a PR):

> ✅ **effort-gym-023-todo-product** finished (**done**): Done. REPL input parsing fixed — `shlex.split`…
> _The worker reports it pushed …, which I could **not independently verify** (this repo isn't on the App's account)._

## Root cause — the entire drain loop is nested inside `elif delivery.landed:`

`_finish_effort` (`orchestrator.py`) branches on the delivery verdict:

```
if   delivery.no_changes:                          → read-only close
elif delivery.landed:                              → THE DRAIN LOOP (re-sweep, one-task dispatch,
                                                       complete-scope) + PR/D2/develop  ← 11083
elif not delivery.verifiable and self_reported:    → set `where` to the honest "unverified" text
                                                       and NOTHING ELSE — no drain           ← 11220
```

`_verify_delivery` catches **any exception** from `read_branch_delivery` and returns
`landed=False, verifiable=False` (orchestrator.py:6865-6867). So a single transient GitHub read blip
(rate-limit, 5xx, network, propagation race) on any drain round routes that round to the unverifiable
branch, which **skips the whole convergence engine** and closes the effort — abandoning every task
still in its queue. On gym-023 deliveries 0–4 verified fine (drain ran) and delivery 5 blipped; the
effort dropped 11 queued tasks and closed after one round.

This is not a gym artifact. The arena repo IS readable by the App (deliveries 0–4 proved it); the
gym merely exercises the verify path enough times to hit a transient failure. In a real repo the
drain re-verifies the remote on **every** round, so over a 10+ round drain the probability of one
transient failure — and thus a premature close — is real. It is exactly the dark-factory failure
class: convergence coupled to a flaky external signal instead of living in the deterministic bridge.

## The fix — F25.1: convergence drains the queue regardless of remote verification

The task queue is the org's **own** memory (§5, "the environment remembers"); dispatching the next
queued task needs the workspace, not the remote branch. `_drain_iterate` already closes each task it
dispatches as `status="dispatched"` immediately (orchestrator.py:7476), so the queue drains
monotonically — draining pending work cannot loop, whatever the verify verdict.

1. **Extract `_drain_next_pending(effort_id) -> bool`** — the verification-independent "dispatch the
   next queued task if any" step (drain_loop on, effort thread resolvable, `_dispatchable_tasks`
   non-empty → `_drain_iterate` one task, return True). This is exactly the block the landed branch
   already runs at 11144-11152, lifted into one shared method.
2. **Call it in the landed branch** (replacing the inline block) — behaviourally identical.
3. **Call it FIRST in the unverifiable branch** (11220): if a task dispatched, `return` (the effort
   re-enters when that task delivers) instead of closing. Only a genuinely empty queue falls through
   to the honest "unverified — here is the worker's self-reported branch" closure.

Net: a transient verify failure on a mid-drain round now dispatches the next queued task instead of
abandoning the effort. Because the failure is transient, later rounds verify normally and the full
re-sweep / complete-scope / PR path (which legitimately needs a verified branch) runs as before.

## Alignment (checked)

- **Design.** §5 *"the model proposes, the environment remembers"* — the queue is the environment;
  convergence must read it, not a remote-verify verdict. §10.4 — an effort with a non-empty task
  queue is by definition not converged and must not close `done`. §3 — loops are affordable *only if
  they converge*; a loop that abandons on a transient blip does not. §4.2 — the PM's remote verify is
  still the acceptance signal for *delivery* (PR/merge stay gated on `landed`); this only stops it
  from gating *convergence*.
- **Research.** ANALYSIS-frontier: reliability for small-model orgs must live in the deterministic
  harness. Decoupling the convergence loop from a flaky external read is precisely that. No new gate,
  no human removed from any decision — a reliability fix on the drain half of the loop.
- **Fail-safe.** The helper no-ops (returns False → existing behaviour) if drain_loop is off, the
  thread can't be resolved, or the queue is empty. It can never dispatch a task that isn't already
  queued, and never bypasses the human merge gate (that lives in the landed PR path, untouched).

## Plan
1. Add `_drain_next_pending`; refactor the landed branch to call it; call it first in the
   unverifiable branch.
2. Tests: (a) an unverifiable delivery with pending tasks dispatches the next one instead of closing;
   (b) an unverifiable delivery with an EMPTY queue still closes honestly (no regression); (c) the
   landed branch still drains as before.
3. Full suite green → deploy → wipe arena → gym-024. Success: a mid-drain unverifiable delivery no
   longer abandons the effort — the queue drains to completion and the loop can reach an evidenced
   zero.

## Observed-but-not-fixed this pass (evidence for later)
- **test_count_regressed 67→42→45.** A drain worker deleted ~22 tests; P19 F13 correctly *flagged*
  it (by design it never blocks — a count can legitimately fall) but it was never restored. This is
  the P16-discard-class quality concern; watch gym-024 and address if a drain worker again ships a
  net test loss the org accepts silently.
- **Re-sweep on an unverifiable queue-empty round.** If the round where the queue finally empties is
  itself unverifiable, the re-sweep (in the landed branch) still won't run, so the effort closes
  unverified without a final `scope_completed`. F25.1 covers the proven bug (mid-drain abandonment);
  a full re-sweep-on-unverifiable is the follow-up if gym-024 shows a fully-drained effort closing
  without an evidenced zero.
- **P24 not exercised.** gym-023 had zero abandons/stalls, so the silence-keyed watchdog (P24) was
  deployed + unit-tested but not live-triggered this run. Still pending a run that actually abandons.
