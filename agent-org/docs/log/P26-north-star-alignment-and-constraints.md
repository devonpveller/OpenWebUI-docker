# P26 — the North Star sorts generation: aligned→task, off-theme→constraint (design §6.6)

## Grounding (design + evidence)

ORCHESTRATION-DESIGN §6.6 (added 2026-07-23 from the operator discussion): the drain has two
convergence modes. **Mode A (generative discovery)** propagates *toward the North Star theme* — the
original prompt — and must NOT be forced to zero; aligned ideas become tasks that sharpen the path,
**misaligned ideas become constraints that narrow it**. §6.5 already warns the propagation count is
meaningless if it isn't anchored to a stable goal. §6 (CDCL) already turns *reproducible failures*
into durable constraints that narrow the search; §6.6 extends "what becomes a constraint" to
*off-theme drift*.

**Evidence (gym-024, 2026-07-23):** the effort delivered a complete, D2-passing product PR (#23) but
its propagation count *plateaued at 2–4 for rounds 6–9 and never reached a meaningful signal* because
an **off-theme tail kept inflating it** — "add descriptive bodies to merge commits", "split the
scaffold commit b3de9e3" (re-derived, re-worded, round after round). That is process/meta work, not
the product theme (a todo CLI). It should have been constraints that narrow the path, not counted
tasks that stall termination.

**Code mechanism (verified):** in `_drain_round`, the `goal_alignment` lens is already mined against
`product_goal` (the effort's root goal = the North Star; `orchestrator.py:10648,10677` — P19 F19-redux),
so its tasks are theme-aligned by construction. But the **`clean_code` and `project_documentation`
lenses derive tasks with NO goal comparison** (`_tasks_from_lens`, `orchestrator.py:10679-10682`) —
objective task-gen (correct per §6.5's observe-without-the-goal rule). That is exactly where off-theme
drift enters the counted queue unchecked against the theme.

## The fix

### F26.1 — the North-Star off-theme sort
After the `clean_code`/`project_documentation` lenses derive candidate tasks, **sort them against the
North Star** (`product_goal`, the original prompt) before they become counted tasks:
- A candidate that **advances the product toward the goal** stays a task (as today).
- A candidate that is **off-theme** — meta/process/cosmetic work that does not move the product toward
  the goal (git-history housekeeping, commit-message rewrites, tooling unrelated to the product's
  purpose) — is recorded as a **constraint** (`_record_constraint`, `kind="off_theme"`) and **excluded
  from `derived`**, so it never inflates the propagation count.

Framed per §6.5 (observe, don't ask a verdict; the goal enters at the *compare* step, not the observe
step): the lens still observes objectively; the sort is a *comparison* against the North Star — the
same shape as `_gap_analysis` Step 2. **Fail-safe direction:** the sort identifies the clearly
off-theme minority and *keeps everything else* — an uncertain candidate stays a task, so real
product-quality work (a genuine refactor `clean_code` surfaces) is never amputated; only clear
meta/process drift is pruned. `goal_alignment` tasks are NOT re-sorted (already goal-gated).

### F26.2 — the constraint narrows future generation (HELD pending F26.1's gym validation)
The off-theme constraints don't just get recorded — they should **narrow the path** (§6.6): inject the
accumulated `off_theme` constraints into the lens/gap generation prompts as a standing narrowing clause
(*"prior rounds classified these directions as off-theme — do not re-propose them"*), so the same tail
is not re-derived every round (the gym-024 churn) — guided search narrowing over time (§7).

**Held for the next pass (P22 discipline — ship one change, validate, then the next).** F26.1 already
makes the count correct (off-theme is pruned to a constraint every round regardless of re-derivation),
and F26.2 is a *generation* change that risks over-suppressing the lenses, so it needs its own gym
validation. F26.1 records the constraints (`kind="off_theme"`), so the data F26.2 consumes already
exists; wiring it in is a bounded follow-up once F26.1 is proven not to amputate real work.

## Alignment (checked)

- **Design.** §6.6 verbatim — misalignment→constraint, the count must measure theme-progress, Mode A
  never forced to zero (this does not force zero; it makes the count *honest*, letting the generative
  loop deliver on a meaningful signal). §6.5 — the goal enters at compare, not observe (the lenses stay
  objective; only the sort sees the goal). §6 — extends CDCL constraints from failures to off-theme
  drift, reusing `_record_constraint`/`_constraints_context`. §1 — the North Star is the human's
  standing governance (the original prompt), spent once.
- **Research.** ANALYSIS-frontier: the small model narrows via accumulated determinism (§7) — a
  constraint is that determinism. No new human gate; a reliability/meaningfulness fix on the generative
  half of the loop. Fail-safe keep-by-default preserves the generative value the operator prized.
- **Not this pass:** Mode-B adversarial hardening and its data ledger (§6.6) — the next iteration.
  This pass makes Mode A's count meaningful first; you cannot separate polish from generation until
  generation is properly aligned.

## Plan
1. **F26.1** — add `_sort_off_theme(effort_id, candidates, product_goal)`; in `_drain_round`, sort the
   `clean_code`/`project_documentation` derivations; off-theme → `_record_constraint(kind="off_theme")`
   + drop from `derived`; audit `off_theme_pruned`.
2. **F26.2** — inject `off_theme` constraints into `_tasks_from_lens` generation for those two lenses.
3. Tests: an off-theme candidate (commit-hygiene) against a product goal is pruned to a constraint and
   not counted; a real product-quality candidate is kept; `goal_alignment` tasks are untouched; the
   narrowing clause reaches the generation prompt. Full drain/lens regressions green.
4. Deploy → wipe arena → gym-025. **Success:** the off-theme tail is filed as constraints (audit
   `off_theme_pruned > 0`), the propagation count reflects theme-progress (no 2–4 commit-hygiene
   plateau), and the loop delivers on a count that means what §6.6/§10.4 need it to mean.
