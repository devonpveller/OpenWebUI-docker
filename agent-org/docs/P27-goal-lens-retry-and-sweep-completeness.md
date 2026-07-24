# P27 — retry the goal lens; never close "done" on an incomplete sweep (design §6.5/§6.6)

## Evidence (recurring, 3 runs)

The `goal_alignment` lens is the convergence **single point of failure**: it is the only goal-aware
lens (§6.5 — its report is the sole input to gap analysis) and it probes the most, so it truncates or
gets **abandoned** more than the others. When it produces no report, the sweep is `swept=False`
(`orchestrator.py`: `swept = bool(reports) and bool(reports.get(goal_alignment))`), gap analysis is
skipped, and the effort **delivers-and-closes "done" on a sweep that never compared the product to the
goal**:
- gym-020 — goal lens truncated, salvage empty → swept=False, delivered.
- gym-024 round 9 — goal lens truncated → swept=False, closed on the plateau.
- gym-025 round 1 — goal lens turn **abandoned** (`worker_turn_abandoned` + `stall_recovered`),
  produced no report → swept=False, delivered PR #25 with 2 tasks open after ONE round.

This is the deferred **P22 F22.2**, now clearly the dominant blocker to a clean multi-round
convergence run (and thus to validating everything downstream, incl. P26's off-theme sort).

## The fix

### F27.1 — retry the goal lens (bounded, once)
Truncation/abandonment is partly stochastic — a fresh session often succeeds. In `_drain_round`, right
after the sweep, if `goal_alignment` produced no report, **re-run just that lens once** before the
round proceeds:
- `_lens_sweep` gains an `only: set[str] | None` filter (run a subset of lenses).
- `_drain_round`: `if goal_alignment not in reports: audit goal_lens_retry; reports.update(await
  _lens_sweep(..., only={goal_alignment}))`.

A retry is one worker turn — cheap, and it directly attacks the root (§7: the small model narrows via
guided search; a lost observation is not a finding of "nothing"). Bounded to one retry per round.

### F27.2 — an incomplete sweep may deliver, but must NOT close "done"
If the goal lens is STILL missing after the retry, the round is honestly incomplete: it never
goal-compared, so it can neither certify convergence (§6.5 — the count is meaningless) nor be trusted.
Per the operator's F22.2 spec, *"the effort may deliver a PR for review but must NOT mark the scope
converged/done on a sweep that never goal-compared."*
- `_finish_effort` carries a `sweep_incomplete` flag (default False), set when the drain round returns
  `swept=False` (and not capped).
- It joins `unmet_or_partial`, so the closure marks the card **needs-attention**, does **not** set
  lifecycle `done`, and posts an honest `done_word` ("delivered — the goal comparison didn't complete;
  not certified converged"). The PR still opens (review value); the effort stays visible for a re-run.
- Safety: the runaway-cap return gains `"swept": False` (it never cleanly swept), which also removes a
  latent `dr["swept"]` KeyError on the capped path.

## Alignment (checked)
- **Design.** §6.5 — a sweep missing the goal-aware lens "is a sweep that did not happen"; the count is
  meaningless without it. §6.6 — Mode A convergence *requires* the goal comparison; an incomplete sweep
  cannot certify. §2.1/§10 — gates produce honesty: this makes the closure honest about an incomplete
  comparison instead of a hollow "done". No new human gate; the human's merge is unchanged.
- **Research.** ANALYSIS-frontier: reliability lives in the deterministic harness — a bounded retry of
  the fragile step + a completeness invariant is exactly that.
- **Fail-safe.** The retry is best-effort (a failure just leaves swept=False → F27.2 handles it);
  `sweep_incomplete` only ever *withholds* a "done", never fabricates one.

## Plan
1. F27.1 — `_lens_sweep` `only=` filter; goal-lens retry in `_drain_round`; audit `goal_lens_retry`.
2. F27.2 — `sweep_incomplete` flag through `_finish_effort`; cap return `"swept": False`.
3. Tests: a sweep missing the goal lens triggers exactly one retry that can recover it; a swept=False
   delivery marks needs-attention (not done) and still opens the PR; a normal swept=True round is
   unchanged. Full drain/lens/closure/execution regressions green.
4. Deploy → wipe arena → gym-026. **Success:** the goal lens recovers via retry so rounds reach
   `swept=True` and the loop runs multi-round (finally exercising P26's off-theme sort on a real tail);
   any residual incomplete sweep closes **needs-attention, never "done"**.

## Deferred (noted)
- Dispatch open tasks on a swept=False round (gym-025's 2 stranded tasks) — with F27.1 the goal lens
  usually recovers so the swept=True path dispatches them normally; revisit only if a residual
  incomplete-sweep effort repeatedly strands real queued work.
