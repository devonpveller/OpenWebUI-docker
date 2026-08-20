# P13 — Scope and gate coherence: stop giving the worker contradictory instructions

**Status:** planned, nothing built. Authored 2026-07-19.
**Owner:** any session. **Self-contained.**
**Evidence:** gym-010 and gym-011 (2026-07-19) — [`gym-010-ground-truth.md`](gym-010-ground-truth.md).
**Blocks:** E1 and E5, unmeasured across three consecutive rounds.

---

## The thesis

gym-011 ended **blocked at the plan gate after three rejections**, with the worker behaving
correctly the whole time. It had been handed two instructions it could not both satisfy:

- the task list said *"work these 12 tasks"* (7 of them commit-message rewrites)
- the scope context said *"your scope is JSON data persistence; anything outside it is NOT yours"*

It worked its 5 persistence tasks and escalated the other 7 — exactly what `_scope_context`
prescribes. The plan gate judged that against the **goal** rather than the **scope**, called it a
refusal, and stopped the effort.

**Nothing misbehaved. The instructions were incoherent.** That is the pattern uniting almost every
finding below: a component doing its job correctly against an input another component built wrong.

**P13 is about coherence between the parts, not about making any single part smarter.**

---

## The issue register — gym-010 → gym-011

Legend: **FIXED** = shipped and verified · **OPEN** = in this plan · **NOTED** = recorded, not
scheduled.

| # | Issue | Status | Evidence |
|---|---|---|---|
| 1 | Intake read a feature build as a bug report | **FIXED** (P12) | 5458→4836 chars; symptom/REPRO/PRIOR-ATTEMPTS all absent on the gym-011 goal |
| 2 | `_sig` read prose as error signatures | **FIXED** (P12) | 8 of 23 lines → 0 |
| 3 | Deleted branches *qualified* efforts as prior attempts | **FIXED** (P12) | filter added; block absent in gym-011 |
| 4 | **Task list contradicts the scope border** | **OPEN — P13.1** | `drain_dispatch {tasks: 12}` vs `drain_scope_selected {scope_goal_chars: 63}` |
| 5 | **Plan gate judges against the goal, not the scope** | **OPEN — P13.2** | 3 × `worker_plan_rejected`; effort blocked |
| 6 | **Plan gate has no substance floor** | **OPEN — P13.3** | narration `"Final test run and commit:"` read as a plan → complete work rejected |
| 7 | **Gate claims "no code has been touched" without checking** | **OPEN — P13.4** | `d3de299` committed at 18:48:26; gate asserted otherwise at 18:48:39 |
| 8 | Abort suppression is path-dependent | **OPEN — P13.5** | aborted effort dispatched for 19 min; `aborted_dispatch_suppressed` fired only on the drain path |
| 9 | Subjective lenses have no fixed point | **OPEN — P13.6** | 7 of 12 gym-011 tasks were commit-message preferences |
| 10 | Tasks closed at dispatch though not worked | **NOTED** | 12 `done`, ~5 worked; next sweep is designed to reopen them |
| 11 | Org plan gate implements during its read-only turn | **NOTED** | gym-009/010 yes; gym-011 **no** (38 s, no commits) — P12 appears to have removed the imperatives that drove it. Unproven mechanism; watch it. |
| 12 | Worker committed to `main`, no agent branch | **NOTED** | self-corrected at publish; no action |

**E1 (task count → zero) and E5 (completion on zero, not the cap) remain unmeasured.** gym-010 was
aborted after one round; gym-011 blocked after one round. Three rounds have produced four passing
evals (E2, E3, E4, E6 — each reproduced twice) and no trajectory data at all.

---

## THE PLAN

**P13.1 and P13.2 are the blocker.** Ship them together; either alone leaves the contradiction.

---

### P13.1 — Dispatch only the selected scope's tasks  ⭐

**Cause.** `_drain_iterate` builds its brief from `open_tasks[:20]` — every open task belonging to
the *effort* — while injecting `_scope_context(node_id)` for the *selected child scope*.
`_dispatchable_tasks` excludes only scopes owned by a **different effort**, so sibling scopes of the
same effort come back in the list. I scoped the context and forgot to scope the task list.

**Evidence.** Round 1: `drain_scope_selected {"scope_goal_chars": 63}` (JSON data persistence) and
`drain_dispatch {"tasks": 12}`, of which 7 were `project_documentation` commit-message tasks.

**Change.** Filter dispatched tasks to `scope_node_id == node_id`. Other scopes' tasks stay queued
and are worked when the walk selects those scopes — which is the entire purpose of the tier walk.

**Why this fixes it.** The brief and the border then describe the same work, so a scope-respecting
plan is also a goal-complete plan and there is nothing to reject.

**Assertions.** A round whose selected scope holds 5 of 12 open tasks dispatches exactly those 5;
the other 7 remain `open`; a later round selecting their scope dispatches them.

---

### P13.2 — The gate judges a plan against the scope in force

**Cause.** The alignment check compares the plan to the **effort goal**. When a bounded scope is in
force, correctly declining out-of-scope work reads as refusing the goal.

**Evidence.** *"The worker explicitly refuses to implement multiple requested tasks … claiming they
are out of scope. This directly contradicts the goal's explicit instruction to work all listed
outstanding items."* — the worker had written `ESCALATE: REPL scope worker needed`, the prescribed
protocol.

**Change.** When the dispatch carries a scope, the gate compares against **that scope's** goal and
task subset. An explicit `ESCALATE:` for out-of-scope work is **compliance**, never a refusal.

**Why this fixes it.** P13.1 makes the lists agree; P13.2 stops the gate punishing the escalation
protocol if they ever diverge again. Defence in depth — the failure cost a whole round.

**Assertions.** A plan covering all in-scope tasks and escalating the rest is APPROVED; a plan
skipping an **in-scope** task is still REJECTED.

---

### P13.3 — A substance floor on the plan gate

**Cause.** The gate treats a turn's final output as the plan, with no test that it *is* one. The
worker's last line was the narration `"Final test run and commit:"` — after it had implemented,
tested and committed the work.

**Evidence.** `worker_plan_rejected` at 18:48:39: *"severely incomplete and only states 'Final test
run and commit:'"* — while `d3de299` sat in the workspace, committed 13 seconds earlier.

**This is the third instance of one pattern.** gym-009: a 72-char lens narration read as findings →
12 phantom tasks. gym-010: the same lens, twice more. gym-011: narration read as a plan → complete
work rejected. **P11.5 fixed it for lenses only.** Reuse `_is_lens_report`'s discipline — a length
floor plus a narration-shape check — and generalise it: **a truncated turn is a MISSING artifact,
not an empty one.** Re-ask rather than adjudicate the stub.

**Assertions.** `"Final test run and commit:"` is not a plan → re-ask, not reject; a real plan
passes; the re-ask is bounded (once) so it cannot loop.

---

### P13.4 — Don't assert "no code has been touched" without checking

**Cause.** The rejection message emits that phrase unconditionally.

**Evidence.** Emitted at 18:48:39; `git log` in the worker's workspace showed `d3de299` at 18:48:26.
The claim was false when made.

**Change.** Either verify (compare the branch head / working tree before and after the turn) or drop
the claim. An org that reports gate outcomes wrongly is exactly the failure class P8's closure
invariant exists to prevent — *the report and the audit must agree*.

**Assertions.** The phrase appears only when the turn genuinely produced no commit and no dirty tree.

---

### P13.5 — Abort must suppress on every dispatch path

**Cause.** `lifecycle='aborted'` is consulted on the drain-dispatch path but not on the
worker-plan-gate → execution path.

**Evidence.** gym-010: abort at ~14:11, `worker_acquire` at 14:15:18 and repeatedly after, no
`aborted_dispatch_suppressed` until 14:37:44 on the drain path. ~19 minutes of worker turns on an
aborted effort.

**Change.** Check the abort at the single dispatch chokepoint, not per-path. Emit
`aborted_dispatch_suppressed` wherever it fires.

**Assertions.** After an abort, no path acquires a worker; the event is emitted once per suppressed
dispatch.

---

### P13.6 — Bound the subjective lenses

**Cause.** `clean_code` and `project_documentation` are asked *"how could this be better?"*, which
always has an answer. Identified in gym-009 as P11.6; never implemented.

**Evidence.** gym-011 round 1: **7 of 12** tasks were commit-message preferences (*"rewrite subject
lines to state intent"*, *"restructure bodies into bullet points"*, *"include verification
commands"*). gym-009 round 3: the documentation lens called the history *"above average"* and still
emitted four more suggestions.

**Change.** A **severity floor** — only defect-grade findings become tasks; preferences are recorded
in the report for human reading but never queued. Simplest viable rule: the lens states a severity
per finding and only `defect` propagates.

**Why this matters for E1/E5.** With no fixed point on two of three lenses, the count cannot reach
zero on quality grounds alone — E5 would be unmeasurable even with everything else correct. **P13.6
is a precondition for E5 meaning anything.**

**Assertions.** A round of pure preference propagates zero tasks; a real defect still propagates.

---

## Traps

1. **P13.1 and P13.2 ship together.** P13.1 alone leaves the gate able to reject on a stale reading;
   P13.2 alone leaves the lists disagreeing.
2. **Do not weaken the gate into a rubber stamp.** Every assertion pairs a should-approve case with
   a **should-still-reject** case (a skipped in-scope task).
3. **The substance floor is a RE-ASK, not a pass.** Treating a stub as approval reintroduces the
   false-green class from the other side.
4. **Config defaults OFF** for any new flag (the unit suite counts worker wakes).
5. **`_drain_iterate` closes tasks at dispatch.** With P13.1 it closes only the dispatched subset —
   check the arithmetic so untouched scopes' tasks are not silently closed.
6. **Never commit or push on the operator's behalf unless asked.**

---

## Definition of done

1. A round dispatches only the selected scope's tasks; siblings stay queued.
2. A scope-respecting plan that escalates out-of-scope work is approved.
3. A narration stub triggers a bounded re-ask, not a rejection.
4. "No code has been touched" is verified or absent.
5. An aborted effort acquires no worker on any path.
6. A round of pure preference propagates zero tasks.
7. Full unit suite green.

## Validation

Reset the arena, run the scenario, and measure **E1 and E5** — the task count across ≥3 rounds and
whether the scope completes on zero propagation rather than the cap. Everything else (E2, E3, E4,
E6) has now passed twice and is regression-watch only.
