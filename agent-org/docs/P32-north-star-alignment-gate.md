# P32 — the North-Star alignment gate (ORCHESTRATION-DESIGN §6.6.1)

*Supersedes the "F31.5 drain runaway bound" idea (task #18). A runaway is a **misalignment symptom**,
not a too-many-rounds problem; an arbitrary round cap punishes project complexity. The correct bound is
convergence by exhausting the **aligned** task list — so the fix is an alignment gate, not a counter.*

## Evidence — two precise gaps let off-North-Star work reach the workers

The plan gate (`_worker_plan_gate` → `_plan_misalignment`) already has a drift check ("work nobody asked
for — packaging metadata, version strings, linter/CI config … is a DEVIATION"), and even cites the exact
gym-015 failure. Yet gym-035 dispatched packaging/linting/`__version__` tasks (its own workers escalated
them as off-scope) and an unbounded REPL corner-case tail — the ~13h / 2109-event runaway. Why the check
missed them:

1. **It's skipped for routine work.** `_worker_plan_required` runs the gate only for `risky`
   blast-radius (default) or when the flail-guard forces it. Routine drain tasks — the whole convergence
   tail — never reach it.
2. **It judges the plan against the LOCAL task, not the North Star.** `_plan_misalignment(goal, plan)` is
   handed the *task* as `goal`. A plan to "add packaging metadata" is perfectly aligned with the task
   "add packaging metadata" — so an off-North-Star *task* passes, because nothing checks the task itself
   against the original prompt.

## The fix — a GROUP-level North-Star alignment gate at task generation

*(First cut checked each task's plan in isolation at dispatch. The operator flagged the flaw, 2026-07-29:
alignment is a property of the plan, not of a line item — an **enabling** task ("add a `_normalize_priority`
helper") is a tangent alone but essential to an aligned group ("sort by priority"), and isolation would
falsely prune it, so the aligned work it enables could never land. The gate is therefore judged over the
**whole group**, at generation.)*

The round's candidate tasks are generated in gap analysis; `_sort_off_theme` already splits that list
(deterministic git-meta → constraint). The North-Star gate slots **immediately after it**, on the same
whole list:

```
gap_analysis:
    derived = _tasks_from_lens(...)                       # the round's candidate group
    derived, off_theme = _sort_off_theme(derived)         # P28 deterministic git-meta → constraints
    ── NEW: North-Star alignment gate, over the GROUP ──
    derived, off_ns = await _sort_off_north_star(effort_id, derived)
    for _l, body in off_ns:
        _record_constraint(..., kind="off_north_star")    # a tangent → a constraint, never a task
    # `derived` (the aligned + enabling tasks) is queued as usual
```

- **Judged as a GROUP.** `_sort_off_north_star` hands the model the North Star + the WHOLE numbered
  candidate list, and asks for the 1-based indices of *only* the candidates that serve **no part of the
  North Star even given the rest of the group**. An enabling task is kept (the group shows its purpose);
  a genuine tangent (packaging/CI/version, commit rewrites, corner-cases no real user hits) is flagged.
  "2 of 12 off" → the 10 aligned + any enabling steps stay; only the 2 tangents become constraints.

- **Context-isolated (the anti-mirror, §6.6.1).** The check sees **(North Star, the candidate list) ONLY** —
  no current-state summary, no codebase, no generator reasoning. That asymmetry with the generator (which
  HAS the current state) is the independence; P26 inverted from a *shared frame*, not from using an LLM.

- **Verdict → action.** Flagged index → `_record_constraint(kind="off_north_star")` (distinct from P28's
  `off_theme`) + audit `off_north_star_pruned`; it drops out of the counted task queue and narrows later
  rounds (§6). Auditable, reversible, never a silent drop.

- **North Star = the effort's original prompt.** `_north_star` = `current_goal` split on `"\n\nITERATION "`
  (the same base-goal the flail-replan uses) — never a re-derived scope goal (whose drift §6.5 warns of).

- **Fail-safe toward KEEP.** Gate off / no North Star / a model hiccup / an out-of-range index → keep the
  whole group. A candidate is dropped only when the model affirmatively lists its index. `AlignmentVerdict`
  defaults to an empty flag list, so the Fake's default (and any malformed reply) prunes nothing.

- Gated by `AO_NORTH_STAR_GATE` (off by default in code like the other drain features, ON in prod).

## Why this bounds the runaway without a cap
Off-tangent plans (dev-tooling, git-meta, "no real person cares" corner-cases) become constraints instead
of work, so the loop tightens on the theme and terminates by exhausting the *aligned* list. A large project
still gets every round it legitimately needs; only drift is cut. This is §6.6's *misaligned → constraint*
made general — the P28 git-meta filter is one deterministic special case of the same gate.

## Plan
1. `_plan_serves_north_star(effort_id, plan) -> bool` — context-isolated reasoning pass (North Star + plan),
   fail-open. `_north_star(effort_id)` helper. Config `north_star_gate: bool = True`.
2. Wire it into `_drain_iterate` after `_drain_plan`; misaligned → `_add_constraint(kind="off_north_star")`
   + close task + audit `plan_off_north_star` + note; no dispatch.
3. Tests: an off-North-Star plan → constraint + no dispatch; an aligned plan → dispatches; fail-open on a
   model hiccup; the gate anchors on the ORIGINAL prompt, not the iteration/scope goal.
4. Deploy → gym: re-run the todo scenario; confirm the dev-tooling/git-meta/endless-corner-case tail turns
   into constraints (not work), and the run converges by exhausting the aligned list — no runaway.
5. Later (separate): whether to inject `off_north_star` constraints into gap-generation (narrow at the
   source), and the plan-vs-task check merge. Not needed for the core.
