# FLOOR — Stop-Gate Enforcement (separate from the editable plan)

**This is the enforcement doc for plan-stop-gates (governance §4.5, TASKS P4.1).** It is
kept SEPARATE from the per-worker plan doc so that a single editable plan can't silently
drop its checkpoints. Plan doc = steering (what to do); this doc = floor (where you *must*
stop). Same floor/steering split as the rules layer.

## The invariant

The bridge (`stop_gates.may_proceed`) enforces the halt independently of the plan's `⛔ STOP`
markers. Deleting a stop marker from the editable plan does **NOT** remove the enforced halt
(P4.1 done-when) — the `Checkpoint` row in the bridge is the source of truth, and a worker
cannot take an action past a checkpoint until a review verdict is recorded (P4.2).

## The cadence

1. **A checkpoint exists between every phase** of a worker's plan. Higher-risk efforts
   (irreversible / cross-effort / cascading-refactor) get more checkpoints; routine efforts
   fewer. Checkpoints are registered in the bridge, not just written in the plan.

2. **At each stop the worker EXPLAINS** — a 4-field artifact (intent / goal-as-understood /
   tradeoffs-hit / what-I'd-flag). The explanation is the artifact the reviewer + PM
   evaluate (P4.3).

3. **Verify, don't trust, the explanation.** The judgment-model reviewer cross-checks the
   explanation against the ACTUAL diff/actions (the audit trail). Small models confabulate;
   words are a lead, actions are ground truth (P4.3b).

4. **Differently-goaled review** (P4.4) — an ethics/whole-picture reviewer whose goal is to
   FIND where the deliverable trades safety/scope/correctness for the metric. Reviewers are
   advisory to the PM and cannot self-approve. Same-goal reviewers are rejected by config.

5. **Risk-gated depth** (P4.5) — routine → one reviewer or none; irreversible/cross-effort
   → a multi-lens panel (correctness / security / scope / ethics). Review must not become a
   wake-storm.

6. **Re-ground → refactor → continue** (P4.6) — if a review flags drift, the PM re-grounds
   the goal (§4.3) and the worker refactors the work so far BEFORE resuming the plan. A
   flagged checkpoint stays un-cleared (blocking) until a clean review.
