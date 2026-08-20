# P20 — one task at a time (evidence: gym-018, 2026-07-21)

## The directive

Operator, 2026-07-21, watching gym-018 abandon: *"As per orchestration_design.md the desire is
one task at a time. Multi task runs don't typically work reliably."*

This is ORCHESTRATION-DESIGN.md restated — and the doc is unambiguous:

- §4: *"The goal decomposes into **single tasks, each delegated to a single worker.** From the
  worker's view it holds **one task at a time and is unaware of the bigger picture.** This
  deliberately sidesteps the small-model long-horizon failure: the worker never carries the
  horizon."*
- §5: *"The composition sits on one repeated unit: **how ONE worker executes ONE bounded task.**"*

The one-task-per-worker rule is the architecture's load-bearing move for small models. The code
was not honouring it.

## The evidence

gym-018 delivered a test-green product and converged cleanly through two drain rounds (12 → 7 new
tasks — the P19 fix working). Then round 2 dispatched its **whole open queue** to one implementer
turn, and the worker went silent mid-turn:

> *"All tests pass. Now I'll implement all 6 remaining tasks. Let me start with the edits: Task 7:
> Fix `load_items`…"* → silence. The stall guard killed and re-engaged it once, then it **abandoned**.

The small model could not hold six tasks in one turn. Same class as gym-017's repeated
worker-silence stalls. This is not a P19 convergence problem — it is downstream of a converging
loop, in the dispatch cadence.

## The cause

`_drain_iterate` handed the implementer **up to 20 tasks in one turn**
(`listed = "\n".join(... for t in open_tasks[:20])`) and closed them all. Every drain round was a
multi-task turn — exactly what the design forbids and what "don't typically work reliably."

## The fix (two parts)

1. **`_drain_iterate` dispatches ONE task.** The first task of the queue goes to a fresh
   implementer turn (brief: *"Work the following SINGLE task … do only this, then stop"*); the rest
   stay queued. The task it dispatches is closed `dispatched`; the siblings stay open.

2. **`_finish_effort` drains the queue one task at a time, sweeping only at the round boundary.**
   Before running a new lens sweep, it checks for tasks a prior sweep already produced
   (`_dispatchable_tasks`). If any are queued, it dispatches the NEXT single one and re-enters —
   **no re-sweep**. The lens sweep is a whole-product observation and belongs at the round boundary
   (an empty queue); running it between every task would cost three lens turns per fix for no new
   information. So the loop is: **sweep once → derive N tasks → work them one at a time (N
   implementer turns) → re-sweep.**

The SWEEP cadence and the propagation-count termination (P10.4) are unchanged — those are
`_drain_round`, independent of how many tasks a turn carries. Only the *dispatch* changed.

## Tests

- `test_drain_iterate_dispatches_one_task_and_leaves_the_rest_queued` — three open tasks → one
  dispatched, two remain queued; the brief names only the dispatched one.
- `test_implementer_gets_one_task_and_the_plan_not_the_whole_goal` — the brief carries one task and
  the plan, never the sibling task, never the whole goal.
- `test_a_scope_completes_after_exactly_three_rounds_of_2_then_1_then_0` — reworked to drain each
  round's queue one at a time (`_drain_queue` helper); the 2 → 1 → 0 termination is unchanged.
- 166 tests green across the drain / dispatch / execution / delivery / lifecycle files.

## Next gate

Deploy, fresh gym (gym-019) on the same convergence scenario. Success: the loop converges to an
evidenced zero *without* a worker-silence abandon — the reliability the one-task rule buys.
Watch that a round's tasks each get their own `drain_dispatch` (payload `task`, not `tasks`), and
that no implementer turn is handed more than one.

## Deferred / follow-up

- The **drain planner** (`_drain_plan`) still runs per dispatched task. For a single bounded task
  the separate planning turn is arguably redundant (the task body is the unit of work); skipping it
  for one-task dispatch would halve the turns. Left in for now (read-only, low risk); revisit if
  turn count matters.
- Carries forward from P19: F4-redux, F20 (worker foreign-uid recovery — related to the stall
  class, still unbuilt).
