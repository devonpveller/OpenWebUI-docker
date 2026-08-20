# P29 — goal-lens resilience: a bounded retry that completes, and no infinite incomplete-sweep loop

## Evidence

The `goal_alignment` lens (the only goal-aware lens, §6.5) does an EXHAUSTIVE adversarial probe
("test the codebase thoroughly, checking each function"). On a matured product that exhausts its
budget before it can report → no report → `swept=False`. This has now *directly blocked a clean
convergence*, not just wasted cycles:

- **gym-028 round 5:** the queue was EMPTY (`open=0`) and propagation ZERO — the convergence
  condition — but the round delivered "not certified converged" instead of `scope_completed` **only
  because the goal lens produced no report that round**. F27.1's single retry didn't recover it.
- **gym-026 rounds 7-8:** repeated `swept=False` rounds *looped* (churned toward the runaway cap until
  manually aborted).
- **gym-020 / 024-r9 / 025-r1 / 027-r3:** the same lens exhaustion, survived only by luck (a later
  round's lens happened to complete).

P28 closed the off-theme thread; this is now the dominant reliability gap on the convergence path.

## The fix

### F29.1 — the retry is BOUNDED and goal-coverage-focused (so it completes)
F27.1 retries the goal lens once, but with the SAME exhaustive prompt, so it hits the same budget wall.
P29 makes the retry a FOCUSED re-check and allows a small number of attempts:
- `_lens_sweep` gains a `focused` flag. When set (only on the goal-lens retry), it wraps the operator's
  verbatim §6.5 prompt with a mechanical BOUNDING preamble (same pattern as P17 F8's "recently-changed"
  wrapper — the operator's prompt is untouched): *"budget is limited; this is a FOCUSED goal-coverage
  re-check — does the product MISS or MISHANDLE anything the goal requires? check the key paths, do NOT
  exhaustively probe every function, echo each finding immediately, and finish."* The full exhaustive
  probe is what exhausts; the retry only needs the goal comparison, which a focused pass completes.
- `_drain_round` retries up to `goal_lens_retries` (default 2) FOCUSED attempts if the goal lens is
  still missing. Cheap, and each focused pass has a real chance to land where the exhaustive one can't.

### F29.2 — bound repeated incomplete sweeps (no infinite loop)
If the goal lens is STILL missing after the focused retries, the round is honestly incomplete (F27.2
already withholds "done"). But the effort must not churn `swept=False` forever (gym-026). Track
CONSECUTIVE incomplete sweeps per effort; after `incomplete_sweep_cap` (default 2) in a row, ESCALATE
(needs-attention, "the goal comparison keeps failing — I can't certify convergence; a human look or a
re-run is needed") and stop auto-re-engaging. A `swept=True` round resets the counter.

## Alignment (checked)
- **Design.** §6.5 — the goal comparison is the load-bearing lens; a bounded re-check that COMPLETES
  serves the count better than an exhaustive one that never reports (the count is meaningless without
  it). §8 — silence/failure detection with a bounded recovery, then escalate (don't loop). The operator's
  verbatim lens prompt is NOT changed — only a mechanical wrapper on the retry (P17 F8 precedent).
- **Research.** ANALYSIS-frontier: the small model narrows via guided search; a focused, bounded probe
  is more reliable for it than an open exhaustive one. No new human gate; a reliability fix.
- **Fail-safe.** The focused retry only ADDS a chance to complete (a failure just leaves swept=False →
  F27.2/F29.2 handle it honestly). The incomplete-sweep cap only ESCALATES (never fabricates a "done").

## Plan
1. Config: `goal_lens_retries: int = 2`, `incomplete_sweep_cap: int = 2`.
2. F29.1: `_lens_sweep` `focused` flag + bounding wrapper; `_drain_round` retries up to N focused.
3. F29.2: consecutive-incomplete-sweep counter in `_finish_effort`; escalate past the cap; reset on swept.
4. Tests: a focused retry that reports recovers the sweep (swept=True); the wrapper reaches the prompt
   only on the retry; N focused attempts are bounded; K consecutive incomplete sweeps escalate (and a
   swept=True resets the count).
5. Deploy → wipe arena → gym-029. **Success:** an empty-queue round certifies `scope_completed` (the
   goal lens recovers via the focused retry) instead of "not certified converged"; a persistent lens
   failure escalates instead of looping.
