# P14 — Escalation routing: a bounded worker must be able to hand work sideways

**Status:** planned, nothing built. Authored 2026-07-19.
**Owner:** any session. **Self-contained.**
**Evidence:** gym-012 (2026-07-19), frozen `ambiguous_scope` after one drain round.
**Blocks:** E1 and E5 — now unmeasured across **four** consecutive rounds.

---

## The thesis

P13 fixed the scope contradiction, and gym-012 proved it: the gate **approved** a plan that
completed its in-scope task and wrote `ESCALATE:` for the rest, where gym-011 rejected the identical
shape three times and blocked. The protocol now works — right up to the last step, where it dies:

```
1. dispatch matches the scope                     P13.1  ✅
2. the gate approves an escalating plan           P13.2  ✅
3. the ESCALATE marker routes to the adjacent scope       ❌  NOTHING WIRES THIS
4. → the org freezes and asks a human
```

`_escalation_target` was built in P10.6 to do exactly step 3 — *"the tier that owns the ADJACENT
scope is the only place with the standing to decide a cross-scope issue"* — and **nothing calls it
for this path.** So a worker that follows the protocol correctly reaches a human every time. With a
four-child tree, the org cannot work its own decomposition unaided.

**The deeper cause is one layer up.** Task 2 was filed to the *selected* scope, not the scope it
belongs to. `_drain_round` files each task to `seam_owner or node_id`, and the lexical seam matcher
did not recognise "add stdout assertions to filter tests" as belonging to a test/output scope. Had
assignment been content-driven, there would have been nothing to escalate at all.

**P14 closes the loop: file work where it belongs, and when a worker hands it sideways, route it —
don't wake a human.**

---

## Evidence

| Observation | Source |
|---|---|
| `worker_plan_approved: 2`, `worker_plan_rejected: 0` | audit — P13.2 works; gym-011 had 3 rejections then blocked |
| `effort_frozen {"trigger": "ambiguous_scope", "level": "steering"}` | audit, 22:46 |
| concern: *"completed Task 1 but escalated Task 2, asserting it falls outside its 'data storage & persistence' scope"* | `concern_posted` payload |
| Both tasks filed to the SAME node `sn-ea7c29ce0a59` | `scope_tasks` — including the test-assertions task |
| `scope_decomposed_live {"children": 4}` | a 4-child tree existed; neither task was routed into it |
| Round 1: `new_tasks: 2`, both defect-grade | P13.6's severity floor working (gym-011: 12, of which 7 preferences) |

**What gym-012 did prove** (worth keeping): P11.5's substance floor fired in production for the
first time — a 56-char narration stub (*"All 31 tests pass. Now let me probe edge cases manually."*)
was recorded as `goal_alignment:truncated` and excluded from the sweep, where in gym-009 the same
shape manufactured 12 phantom tasks. And P13.6 cut round 1 from a preference-heavy 12 to 2 real
defects.

---

## THE PLAN

**P14.1 is the blocker. P14.2 removes most of the need for it.**

---

### P14.1 — Route an ESCALATE to the adjacent scope, don't freeze  ⭐

**Cause.** An `ESCALATE:` marker in a plan or report is treated as a generic blocker, so it lands in
`_elevate_blocker` → freeze → operator. Nothing consults `_escalation_target`.

**Change.** When an escalation arises inside a drain dispatch with a scope in force:

1. Resolve the target scope — the sibling whose `scope` text best matches the escalated work, else
   the parent (`_escalation_target`).
2. **Re-file the task** to that scope (`scope_node_id`), leaving it `open`.
3. Continue the round. The task is worked when the walk selects that scope.
4. Freeze **only** when there is no target — i.e. the escalation is from the root with no sibling
   that fits. That is a genuine human question; the sibling case is not.

**Why this fixes it.** The tier walk already visits every open scope; an escalated task simply needs
to be in the right queue when it does. Freezing asks a human to do the routing the tree already
encodes.

**Assertions.** An escalated task with a matching sibling is re-filed there and the effort stays
`open`; the effort is NOT frozen; a later round selecting that scope dispatches it; an escalation
with no plausible target still freezes.

---

### P14.2 — File a task to the scope it belongs to, not the scope that found it

**Cause.** `_drain_round` files every derived task to `seam_owner or node_id`. `_seam_owner` is
lexical (distinctive title tokens, word-boundary, ambiguity → parent), so anything it doesn't match
lands in whichever scope happened to be selected.

**Evidence.** *"Add assertions to filter tests in test_todo_extended.py"* was filed to the
**data storage & persistence** scope, with a 4-child tree available.

**Change.** Assign each derived task to the child scope whose `scope` text best matches the task
body, falling back to the selected node when nothing matches clearly. Cheapest sound approach: one
batched model call per round — *"which of these scopes does each task belong to?"* — with the
existing lexical matcher as the fallback. It is one call per round, not per task.

**Why this fixes it.** Most escalations in gym-012 existed only because assignment was
selection-driven. Correct filing removes the need to escalate at all; P14.1 then handles the
residue.

**Assertions.** A test-output task and a persistence task derived in the same round land in
different scopes; an unmatched task falls back to the selected scope; the fallback never throws.

---

### P14.3 — Push at commit time, not at the end of the walk

**Cause.** Work lives on an unpushed local branch until the phase walk finishes.

**Evidence.** gym-012: `worker-1` went unreachable holding 3 commits and 42 tests; `worker-2`
re-cloned from `main` and **rebuilt the product from scratch** (~10 min lost). `worker_dispatch_failed
22:30:08`. gym-011 carried a complete deliverable unpushed for ~20 minutes and survived on luck.

**Change.** Push the agent branch as soon as a commit lands, not only at publish. The org already
force-corrects the branch name at publish; do that at first commit instead, so the remote always
holds the work.

**Why this fixes it.** A worker handoff re-clones from the remote. Making the remote authoritative
at commit time makes handoff lossless — the recovery machinery already works, it just had nothing
to recover from.

**Assertions.** After the first commit of a dispatch, the branch exists on the remote at that SHA;
a worker handoff mid-build resumes from it rather than from the template.

---

## Traps

1. **Do not make freeze unreachable.** A genuine cross-project or cross-team escalation must still
   reach a human. Only the *sibling-scope* case is auto-routed; every assertion pairs an auto-route
   case with a still-freezes case.
2. **Re-filing is not closing.** An escalated task moves scope and stays `open`. Closing it at
   dispatch (the current `_drain_iterate` behaviour) would lose it — gym-012 marked the escalated
   task `done` despite it never being worked.
3. **One model call per ROUND for assignment**, not per task. This is a small model on a shared GPU.
4. **Config defaults OFF** for any new flag (the unit suite counts worker wakes).
5. **P14.3 touches the git-proxy path** — the production-branch guard must still deny `main`/`master`.
6. **Never commit or push on the operator's behalf unless asked.**

---

## Definition of done

1. A worker escalating to a sibling scope re-files the task and the round continues; no freeze.
2. An escalation with no plausible target still freezes for a human.
3. Tasks derived in one round land in the scopes they belong to.
4. An escalated task remains `open` until actually worked.
5. A commit reaches the remote before the phase walk ends; a mid-build handoff is lossless.
6. Full unit suite green.

## Validation

Reset the arena and run. Success is **≥3 drain rounds with a task count decreasing toward zero**
(E1) and a scope that **completes on zero propagation rather than the cap** (E5) — neither of which
has been measured in four attempts. E2/E3/E4/E6 are regression-watch; P11.5 and P13.6 both fired
correctly in gym-012 and should keep doing so.
