# P10 — The drain loop: objective lenses, gap-derived tasks, propagation to zero

**Status:** **BUILT + DEPLOYED 2026-07-18** — all six increments; full unit suite green (40 new in
`agent-bridge/tests/test_lenses.py`); `agent-bridge:local` rebuilt and the live bridge recreated
with `AO_DRAIN_LOOP=1`. **Not yet gym-validated** — see *Validation* below; the org's self-report
never scores, and neither does this one. Authored 2026-07-18.

**The plan's own first line of defence held, but only after an adversarial review.** The first build
was 27/27 green and still had **four reachable paths that reported "complete" for a reason unrelated
to the product** — the precise failure this plan exists to eliminate, reintroduced by the
implementation of it. Green tests did not catch any of them (trap 2, exactly as written). They are
now fixed and each has a named test under *FALSE-GREEN GUARDS* in `tests/test_lenses.py`:

| Path | Why it read as "complete" |
|---|---|
| All three lens wakes fail / return empty | `new_tasks == 0` → "nothing further to do". An absence of OUTPUT read as an absence of WORK. Now a round carries `swept`; an unswept round completes nothing. |
| Worker pool saturated | `_lens_sweep`'s blanket `except` swallowed `NoCapacityError`, converting the park-and-resume contract into a silent completion. Now re-raised (trap 3: a round needs 4 slots). |
| A task the implementer failed to land | Re-derived next round → reopened but **not counted** (correctly — it isn't new information). Dispatch keyed on `new_tasks` alone then closed the effort with its queue visibly non-empty. Now dispatch keys on open work; completion needs the queue drained **and** the sweep silent. |
| A parent whose findings all seam-routed down | Its own queue is empty while the work it discovered is outstanding below it. Now a parent cannot complete over an unfinished child. |

**A second review round found the fixes had relocated the same failure**, which is worth recording
as its own lesson: the naive reading of "a parent never fixes its children's insides" filtered a
scope's dispatch to its OWN node — so the round that DECOMPOSES a scope routed every task it had
just derived into the brand-new children, returned an empty list, and closed the effort reporting
*"a full, independent lens sweep found nothing further to do"* with all of its work sitting
unreachable one tier down. Nothing was attached to those children, so nobody could ever pick it up.
The encapsulation rule presumes there IS another owner; an **unowned** child has none. Dispatch now
excludes a descendant's tasks only when that descendant belongs to a **different effort**, and
`_complete_scope` closes an unowned, drained child rather than deadlocking its parent on it.

A fifth was a **stranding** bug, not a false green: `_drain_iterate` queued into `_iterate_after`,
which only `delegate`'s `finally` drains — but `_finish_effort` is also reached from
`_burndown_loop` and `_run_in_host_context`, where that finally has already run. Because the drain
path *returns*, the effort was left with no dispatch, no PR and no closure, its tasks already
closed. `_drain_iterate` now dispatches directly unless it is inside delegate's single-flight.

**Two bugs the build surfaced that the plan did not anticipate:**
1. **The goal leaked into the lenses through the standing wake preamble.** Withholding it from the
   instruction is worthless while `charters.build_context` injects `# GOAL` two lines above. Fixed
   with `build_context(..., withhold_goal=True)` → `router.wake(..., withhold_goal=True)`, which
   drops GOAL + SCOPE SLICE + STEERING and keeps floor + charter (conduct, not outcome). It fails
   CLOSED: an older/duck-typed builder yields empty context rather than a leaked goal.
2. **An undecomposed ROOT scope's text *is* the effort goal**, so injecting `_scope_context` into
   the implementer brief restated the whole goal — the exact gym-008 construction P10.5 retires.
   Scope context is now injected only from a genuine sub-scope (`depth > 0`); at a root there is no
   sibling to be bounded away from.
**Owner:** any session. **Self-contained** — you need no prior conversation, but read
[`ORCHESTRATION-DESIGN.md`](ORCHESTRATION-DESIGN.md) **§4, §5, §6.5** first; this plan implements them.
**Execution record / issue register:** [`P9-make-the-fixes-real.md`](P9-make-the-fixes-real.md).

> **We are building the ORCHESTRATION, not the test project.** The todo CLI in the gym is a *probe*.
> Nothing here is about making a todo app good.

---

## The thesis

The org currently **runs out of work before the project is done**. It QA's once or twice, the loop
hits a hard cap (`n >= 2`), and it stops — or a worker shrugs and the effort strands. There is no
task queue, no notion of "next item / next module / next tier", and "nothing left to do" is an
*accident of an empty model reply* rather than a computed fact.

**The fix is a drain loop with a counted termination.** Objective lenses observe what exists; a
separate step compares that to the scope's goal and derives gaps; gaps become plainly-stated tasks;
tasks are worked; repeat. **A scope is complete when a full lens sweep propagates ZERO new tasks** —
a counted quantity, never a model's opinion.

### The live evidence this is the right fix

| Observation (gym-007 / gym-008, 2026-07-17/18) | What it shows |
|---|---|
| QA prompt literally says *"Say `none` ONLY if…"*, and gym-008's functional lens returned **"no defects"** on a codebase where the code-review lens found real ones and an operator review of a comparable product found **5 bugs + 3 gaps** | A prompt that sanctions "nothing" gets told "nothing" (§6.5) |
| Defects trickled **6 → 5 → none** across rounds | The lens re-derives its own scope each round; the count drifts |
| `_auto_iterate` re-sent the **entire original goal** + one defect, and stripped the prior iteration | The worker was asked to plan work it had just done, against a goal it believed satisfied → **empty plan → "abandoned"** |
| The whole loop is capped at `n >= 2` | Stops for a reason unrelated to whether the work is finished |

---

## Current state (verified 2026-07-18)

**Built + deployed + live-proven**
- **§8 Liveness** — silence detector (`worker_silence_s`), plus `wake_done` in `_STALL_MIDDISPATCH_KINDS`
  (an effort stranded after an abandoned turn is now recovered; live-verified).
- **§10 Acceptance corpus** — durable per-project executable checks, enforced as a hard merge gate
  *and* injected upstream at plan/build time. **Proven:** gym-007 needed a fix round for a missing
  `reopen`; gym-008 planned and built it first time, corpus passed clean.
- **§5–6 CDCL** — failures become durable `EffortConstraint` clauses (infra failures never learned),
  injected into retries; burn-down terminates on a `seen_sigs` fixed point.

**Built but INERT (no caller — do not assume these work)**
- ~~**§4 `ScopeNode`**~~ — **NOW LIVE (2026-07-18).** `_ensure_scope_node` attaches every drained
  effort to a node, `decompose_scope` splits a tier, `_scope_context` bounds a sub-scope worker,
  `_complete_scope` walks completion up, `_seam_owner` + `_reopen_scope` route a parent's seam
  defect down into the owning child.
- **§11 producer** — `_verifiable_concern_blocks_clear` is wired into `apply_operator_decision`, but
  `raise_verifiable_concern` has **no caller**, so no concern carries a check. (See *Traps* — a naive
  wiring deadlocks.)

**Not built:** §7 frontier/OpenRouter oracle, §9 security adversary, and everything in this plan.

---

## THE PLAN

Six increments. **Each is independently shippable and independently valuable** — ship in order, run
the suite between each, and do not start the next until the previous is green and deployed.

---

### P10.1 — The three standing lenses, de-biased  ⭐ start here

**Why first:** it is the smallest change, and it is the one currently producing *false greens*. Until
the lenses are objective, every count downstream is noise.

**Where:** `orchestrator.py :: _qa_evaluation` (~line 8253) builds a single instruction and parses
`DEFECTS:` / `FOLLOWUPS:` / `VERDICT:` blocks. Replace with three lens runs.

**The three lenses — use these prompts VERBATIM (operator's, proven in manual PR review):**

1. **`goal_alignment`** — *objective observation, then reasoned gaps (P10.2)*
   > test the codebase thoroughly treating as a final product, checking each function, find gaps in
   > the solution for the problem the codebase is attempting to solve and write a short report. Do not
   > edit files in this codebase, this is just evaluative.

   **The goal MUST NOT appear in this prompt.** That is the structural debias: a goal in an observation
   prompt invites the model to reason *toward* it and declare it met. The report is compared to the
   goal in P10.2, not here.

2. **`clean_code`** — *objective task generation*
   > evaluate the codebase code cleanliness, is the code practicing SOLID, industry standard
   > programming patterns, clear naming conventions and does the code support good documentation? How
   > does or doesn't this codebase support documentation for its code?

3. **`project_documentation`** — *reasoning task generation*
   > evaluate the comments in the git repo through its history here. Are the titles and descriptions
   > clear with intent focused and enough to grasp an evolving projects history? how does is the
   > information helpful and how could the information be better written for you to be able to pick up
   > the project where it left off?

**Rules (from §6.5) — these are the acceptance criteria:**
- **No "nothing" affordance.** No "say none if none", no "only if genuinely zero".
- **No verdict framing.** No "grade to a bar", no `VERDICT:` line.
- Every lens is **read-only** and runs in a **fresh session** (`~qa{seq}` already does this — keep it).
- **Run all three fresh EVERY round.** Never feed a previous report back into a lens; the propagation
  count is only meaningful if each sweep is independent.

**Persist each report** in a new `LensReport` row: `(id, effort_id, scope_node_id|None, lens, body,
created_at)`. **For human history only** — reports are *never* injected into a worker prompt. They let
us diff rounds and audit where a gap came from.

**Assertions:**
- `test_lenses.py`: all three prompts are issued per round; none contains the literal strings
  `none`-affordance or `VERDICT`; the `goal_alignment` prompt does **not** contain the effort's goal
  text; three `LensReport` rows are persisted per round.

---

### P10.2 — Gap analysis: report + scoped goal → plainly-stated tasks

**Why:** this step does not exist today at all. It is where tasks are *discovered* rather than invented,
and it is the only place the goal is allowed to enter.

**Design:**
```
INPUT   goal_alignment report (objective, from P10.1)   +   THIS SCOPE'S goal
STEP    compare → what the goal requires that the report does not evidence
OUTPUT  GAPS → tasks, each stated PLAINLY as work to do
```
- Lenses 2 and 3 produce tasks **directly** from their findings (no goal comparison needed).
- **Scope is the constraint that makes this tractable for a small model** (§4). Never run gap analysis
  against the whole project goal — always one scope's goal.
- **Tasks are stated plainly.** No rationale chains, no "why this change". A small model reasons worse
  than a frontier one, so we ask it for *less* reasoning: give it a legitimate, relevant, plainly
  stated task. Rationale lives in the audit/report, not in the task handed to a worker.

**Assertions:**
- A report that omits a goal component yields a task naming that component.
- A report that evidences every component yields **zero** tasks.
- The task body contains no "because/why" rationale block (plain statement only).

---

### P10.3 — The scoped task queue

**Why:** the org has no task list. This is the substrate the drain drains.

**Model** — `ScopeTask`: `(id, scope_node_id, effort_id|None, body, source_lens, status[open|done|
dropped], created_at, closed_at)`. Content-address `id` on `(scope_node_id, body)` so the same gap
re-derived next round is **not** a duplicate — that idempotency is what makes the propagation count
honest (mirror `AcceptanceCheck` / `EffortConstraint`, both already do this).

**Accessors:** `add_task` (idempotent), `list_open_tasks(scope)`, `close_task`, `count_new_tasks(round)`.

**Assertions:** re-deriving the same gap produces no new row; `count_new_tasks` returns only rows
created in that round.

---

### P10.4 — Propagation-count termination (replaces the `n >= 2` cap)

**Why:** this is the actual termination rule. Delete the arbitrary cap.

```
round:  run 3 lenses (fresh) → gap analysis → tasks
        work the open tasks
        count NEW tasks propagated this round
        > 0  → another round
        == 0 → scope COMPLETE (goal met AND all lenses green)
```
- **Keep a runaway guard** (a high round cap, e.g. 40, like `burndown_round_cap`) as a backstop only —
  documented as a safety net, *not* the termination condition.
- **Retire `_auto_iterate`'s `n >= 2`** and its "restate the whole goal" evolved-goal construction
  (`orchestrator.py :: _auto_iterate` ~5834). That construction is what caused gym-008's empty plan.

**Assertions:** a scope with a stubbed lens returning 2 gaps then 1 then 0 completes after exactly 3
rounds; the cap is never reached; `auto_iteration`'s old cap no longer gates the loop.

---

### P10.5 — Plan / implement split

**Why:** a worker asked to plan work it just performed has context bias by construction (gym-008).

```
PLANNER      tasks + codebase (+ scope goal) → plan
IMPLEMENTER  a FRESH worker instance receives the plan + tasks and implements
```
The implementer never plans its own completed work, and starts with a clean session so it is not
defending prior output. **CDCL clauses (§5–6) carry the learning across that fresh session**, which is
what makes the split safe — this was not true before the clause set existed.

**Assertions:** the implementer wake's session id differs from the planner's; the implementer prompt
contains the plan + tasks and **not** the whole project goal.

---

### P10.6 — The tier walk (makes §4 live)

**Why:** this is what stops the loop running out of work before the project is done.

```
START      scope the whole project scaffold (top)
           → split into module scaffolds → subdivide → down to worker tasks
DRAIN      a scope completes when its tasks drain AND its lens sweep propagates zero
BUBBLE UP  child complete → re-evaluate parent → parent complete → … → project
REOPEN     a parent-level lens sweep that finds a seam defect writes a task into the
           OWNING child scope and flips that child back to `open`
```
**"Complete" is a current state, not a terminal one** — a neighbour's later QA can reopen a finished
scope. That is the integration/seam check, and it is the answer to "the loop should never be idle
while the project is unfinished".

**Wire the existing inert pieces:** `add_scope_node` (decomposition), `_scope_context` (the bounded
worker brief — its own scope + contract, the border named, **the rest of the tree withheld**),
`_escalation_target` (route a cross-scope issue to the adjacent-scope owner).

**Assertions:** a child completing marks the parent for re-evaluation; a parent lens finding a seam
defect reopens the child and writes the task there; a worker brief never contains a sibling's detail.

---

## Traps — read before you build (each cost real time)

1. **Raising a concern FREEZES the effort.** A naive §11 producer on a red path deadlocks: the
   burn-down queued alongside it cannot dispatch (governance §3.0 — a frozen effort's agents may never
   compute), and the clear-gate then cannot be satisfied because nothing can run to make the check
   green. If you wire a verifiable-concern producer, put it where freezing is *already* correct (a
   genuine give-up point), and note the gate blocks only `approve` (`modify`/`abort` must pass).
2. **Unit tests will not catch freeze/dispatch interactions** — the fakes do not model them. Reason
   about the state machine, do not trust green alone.
3. **`_run_check` needs a worker slot**, so it cannot run while an effort is frozen. Consult the
   *recorded* `check_exec` result instead of re-running.
4. **Config defaults OFF.** The unit suite counts worker wakes; a default-on flag breaks ~30 tests.
   Enable via `AO_*` in `agent-org/docker/docker-compose.yml`.
5. **`WinError 10055` in the full suite is environmental** (host socket exhaustion), not your bug.
   It cascades into `pytest_asyncio` setup errors. Re-run the affected file in isolation to confirm.
6. **Backticks in a `git commit -F -` heredoc trigger shell substitution.** Use a quoted delimiter
   (`<<'EOF'`).
7. **Never commit or push on the operator's behalf unless asked.** Merges to `main` stay human-gated.

---

## Known gaps carried forward (2026-07-18)

Deliberately not closed in this iteration — recorded so the next session does not assume otherwise:

- **`_auto_iterate`'s `n >= 2` and its whole-goal restatement are still live** for the non-drain
  paths, including the ERROR-VERDICTS branch *inside* `_finish_effort`, which can still fire on the
  same delivery the drain just handled. The drain loop itself never calls it, so P10.4's assertion
  holds, but the old construction has not been deleted from the codebase.
- **`drain_plan_split` / `drain_tier_walk` default `True`**, against trap 4's "config defaults OFF".
  Harmless to the unit suite because both are only read on the drain path and `drain_loop` itself
  defaults off — but it is a deviation, not an oversight.
- **`_maybe_decompose` splits on the evidence of ONE round** (`>= 5` derived tasks). A single noisy
  sweep can therefore create a tier. The children are cheap and unowned, so the cost is structural
  clutter rather than lost work, but a two-round confirmation would be sounder.
- **Seam routing is lexical** (`_seam_owner` matches distinctive title tokens on word boundaries and
  gives ambiguity back to the parent). It will under-route rather than mis-route, which is the safe
  direction, but it is not semantic.

## Definition of done for this iteration

1. All three lenses run fresh per round, contain no "nothing" affordance, no verdict framing, and the
   goal-alignment lens does not contain the goal.
2. Gap analysis produces plainly-stated tasks from `report × scoped goal`.
3. Tasks live in a queue, idempotent per scope.
4. A scope completes on **zero propagation**, not on a cap or a model's "none".
5. Planning and implementation are separate workers.
6. Scopes nest, complete bottom-up, and reopen on a neighbour's seam defect.

## Validation

Run a gym round (`d:\Open WebUI\ai-orchestration-gym`,
`python runner/gym_runner.py --auth app run <scenario> --yes-provision`). **The org's self-report never
scores** — verify against the GitHub remote and the audit. Success looks like:
- more than 2 QA rounds when work remains (the old cap is gone),
- a task count that **decreases to zero** across rounds,
- no `worker_plan_empty`, no `abandoned` turn from "nothing to do",
- the delivered product no worse than the operator's PR#15/#16 baseline.

**The measurement is the operator's product judgement, not the gym score.** The gym assertions only
prove a PR opened and the tests ran.
