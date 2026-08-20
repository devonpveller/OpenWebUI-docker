# P30 — Mode B: adversarial hardening + data ledger (design §9.5, §6.6, §9, §10)

Mode B is a whole loop, so it ships in staged slices — each a working, gym-validated increment. It
REUSES what already exists rather than inventing:
- **§10 acceptance corpus (BUILT):** `projects.add_acceptance_check` / `list_acceptance_checks` /
  `set_acceptance_check_active`, and `_acceptance_corpus_gate` already red-gates every delivery on the
  corpus. Mode B makes the org GENERATE corpus checks (the operator-in-the-loop fork already resolved
  for the manual path).
- **The reproduction harness (BUILT):** `_repro_red_green` / `_org_reproduction_verified` — the org-run
  RED→GREEN check. Mode B's reproducibility gate is this: a finding counts only if the org sees its test
  FAIL on the current code (§6 hygiene — noise never registers).
- **The reviewer charter (BUILT):** the differently-goaled "optimize to refute" agent — Mode B's lens.
- **The drain (BUILT):** patch tasks from Mode-B findings fix via Mode A's one-task dispatch.

## Slice 1 (P30) — a minimal Mode-B phase, end to end, ONE lens

After a Mode-A increment converges (`scope_completed`) or delivers, run a single **contrarian lens** (a
refute-framed reviewer turn, read-only) against the delivered branch. For each finding:
1. **Reproducibility gate** — the lens must emit an executable reproduction; the org runs it and keeps
   the finding ONLY if it FAILS on the current code (a real break, not an opinion). Reuses the red→green
   harness. Un-reproduced findings are dropped (audited, not queued).
2. A reproduced finding → **(a)** an org-generated **acceptance-corpus check** (`add_acceptance_check`,
   origin=`mode_b`) so it's enforced on every future delivery and **cannot regress**, and **(b)** a
   **patch task** queued to the drain to fix it now.
3. Audit `mode_b_finding` / `mode_b_check_added` / `mode_b_finding_unreproduced`.

**Success (the acid test):** on the gym product, Mode B autonomously surfaces ≥1 of the operator's
2026-07-26 bugs (`db_path("")`, unpersisted schema version, per-call parser rebuild) or a real edge
case, reproduces it, banks a corpus check, and queues the fix — WITHOUT being told the finding.

## Slice 2 — diverse lenses + diminishing-returns completion

Add the reference lens set (§9.5): correctness/logic, edge-case/robustness, performance, fragility/DRY,
security-at-the-seams. Run them as diverse approaches; **Mode B is complete for the increment when new
reproduced findings/round → ~0 across K diverse lenses** (not one drying up) — the loop-until-dry pattern
over *perspectives*. Bounded (a round cap + escalation, like the drain).

## Slice 3 — the data ledger + the "improved?" reading

Per round, tagged by lens: findings, reproduced, fixed, corpus delta. Expose the **diminishing-returns
curve** and the completion reading (findings/round→0 across lenses + corpus growth plateau + zero
regressions). This is the data that proves the product *measurably improved* (§6.6 / §2.3), not asserted.

## Alignment (checked)
- **Design.** §9.5 (this is its build), §9 (adversary-as-tests, generalized), §10 (corpus = the durable
  ledger; org-generated now), §6 (reproducibility hygiene; only reproduced findings register), §6.6
  (Mode B approaches zero, by diverse approaches, measured). Distinct phase after Mode A (§9.5).
- **Governance (§9.4).** Scoped by the floor; attacks only the org's own product in the controlled arena;
  never real data/external systems. Human governs the standing lens set, not each finding.
- **Fail-safe.** The reproducibility gate means Mode B can only ADD a check for a break it actually
  reproduced — it cannot fabricate work or a false "hardened". No new human gate; the merge stays human.

## Plan
1. **Slice 1** — the single-lens Mode-B phase + reproducibility gate + corpus check + patch task; deploy;
   gym-030. Validate the acid test (re-derives an operator-found bug).
2. **Slice 2** — diverse lenses + diminishing-returns completion; gym.
3. **Slice 3** — the data ledger + completion reading; gym.
Each slice: tests green → deploy → gym → analyze, one change at a time.
