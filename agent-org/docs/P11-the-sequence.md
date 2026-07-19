# P11 — The sequence: make the order of operations mean what it says

**Status:** planned, nothing built. Authored 2026-07-19.
**Owner:** any session. **Self-contained** — read [`P10-the-drain-loop.md`](P10-the-drain-loop.md)
first (this fixes what P10's first live run exposed) and the gym-009 sequencing audit below.
**Execution record:** gym-009, 2026-07-19 (`runs/20260719T011557Z-scenario-009-drain-loop`).

> **We are building the ORCHESTRATION, not the test project.** The todo CLI is a probe.

---

## The thesis

P10 shipped a drain loop that **generates work well and cannot decide it is finished**. gym-009
produced eleven real fixes, three new tests and a decomposed commit history — quality gym-008 never
reached — and then propagated `21 → 23 → ascending` and would have run to the 40-round cap.

Every failure traced back to the same root: **steps run in an order that contradicts what they
claim to do.** A "plan" step implements. An "implementer" runs second and can only find work
already done. A decomposition runs *after* the analysis it was supposed to scope. None of this is a
model behaving badly; each is the sequence guaranteeing the outcome.

**The fix is not smarter models or better dedupe. It is putting the steps in the right order and
phrasing each one as what it actually is.**

---

## The evidence (gym-009 audit, event-by-event)

| Observation | What it shows |
|---|---|
| Org plan gate: `worker_acquire` 01:18:02 → `wake_done` 01:27:27 (**9 min: full build + push**) → `plan_approved` 01:27:39 | The plan gate approves work that has already shipped |
| Drain planner: `worker_acquire` 01:49:00 → `wake_done` 01:54:58 (**6 min, 5 commits**) → implementer dispatched | Same inversion, one layer down (`_drain_plan`) |
| Implementer turn: 33 seconds, "already complete at `7304b93`" | `_drain_plan` is **awaited to completion** before dispatch, so the implementer is *structurally* redundant |
| Six `worker_acquire`/`wake_done` cycles 01:30–01:42, ~2 min each | **~12 min of GPU** re-verifying a finished deliverable |
| `worker_plan_rejected` 02:07:23 → next turn ends **02:28:17** | A **21-minute** turn — the run's longest — chasing a demand to implement features that already existed |
| `gap_analysis` 01:47:45, `scope_decomposed_live` 01:48:59 | Decomposition runs **after** the analysis it should have scoped |
| `add_scope_node` creates children with **no `effort_id`**; `_ensure_scope_node` selects `WHERE effort_id == <effort>` | **The children are never selectable.** Every round gap-analysed against the whole 5417-char project goal |
| `goal_alignment` report: 72 / 48 / one narration line across three rounds | The lens whose report is gap analysis's *only* input never produced one |
| Round 1 vs 2 goal-alignment tasks: exact-string overlap **0**, semantic overlap 4 | Re-derivation is not textually stable |

---

## THE PLAN

Six increments. **P11.1 first** — until the arena is greenfield we cannot tell "the org should fix
this" from "this was already here," and every later measurement is contaminated.

---

### P11.1 — A greenfield arena per rotation  ⭐ start here

**Why first:** gym-009's `todo.py` was an *edit of an existing file*, on a repo carrying history from
gym-004 through gym-008. We could not cleanly separate what the org introduced from what it
inherited. Every finding below had to be argued around that ambiguity.

**Where:** `ai-orchestration-gym/runner/gym_runner.py`, the `provision.mode: swap` path (today it
resets `main` to the template and logs *"history preserved"*).

Add `provision.mode: greenfield`:
- force-push an **orphan** commit containing only the template (no ancestry),
- delete every ref except `main` and `harness` (the ledger — **never** touched),
- close any open PRs.

`harness` is the gym's own ledger and is excluded by name, not by pattern. Deleting it destroys the
run history.

**Assertions:** after provisioning, `git log --oneline main | wc -l` is 1; the only refs are `main`
and `harness`; no open PRs.

---

### P11.2 — Both plan turns become evaluative, not imperative

**Why:** two independent "read-only" mechanisms, at two layers, both executed. `plan_only=True`
excludes the edit/write *tools* — but a worker writes files with `cat > file << EOF` and ships with
`git push`. Bash was never gated.

**The fix is BOTH mechanism and phrasing, and the phrasing matters more.** The three lens prompts
are read-only *in practice*, every round, with no enforcement at all — because they ask the worker
to **evaluate and report**, not to act. `_drain_plan` says *"PLANNING TURN — you will CHANGE
NOTHING"* and then *"turn the task list into an ordered implementation plan"*, which is a call to
action wearing a prohibition.

1. Re-phrase `_drain_plan` on the lens template: read the codebase, **write a report** describing
   what each task requires and which files it touches. A report is the deliverable, not a plan to
   execute.
2. Pass `plan_only=True` on the wake (it exists; it is used at orchestrator.py:10558 and was simply
   omitted).
3. Same treatment for the org worker plan gate.

**Assertions:** across a drain round, the planner's turn produces **no commits** (`git log` on the
branch is unchanged across the planner's acquire→wake_done window); the implementer's turn produces
them.

---

### P11.3 — Re-sequence the round: decompose → scope → analyse

**Why:** this is the defect that made every other one unreadable. Today:

```
lens sweep → gap analysis (ROOT goal) → decompose → queue → dispatch
                        ^^^^^^^^^^^^^ always the whole project goal
```

Two compounding causes: `_maybe_decompose` runs *after* `_gap_analysis`, and children are created
without `effort_id` so `_ensure_scope_node` can never select one. The tier walk builds a tree and
never uses it.

```
lens sweep → decompose (if warranted) → SELECT the working scope → gap analysis vs THAT scope's goal
```

- Move `_maybe_decompose` **before** `_gap_analysis`.
- Set `effort_id` on children created for an effort, so `_ensure_scope_node` can return them.
- Gap-analyse **per scope**, not once against the root.

**Assertions:** after a decomposing round, `_ensure_scope_node` returns a `depth > 0` node; the
`gap_analysis` audit payload records a `scope_goal` shorter than the project goal; a storage-scope
round does not derive UX tasks.

---

### P11.4 — Gap analysis asks "what remains **for this scope**"

**Why:** with P11.3 the input is right; the question still needs to be. The current prompt compares
a report to a goal and lists what the report doesn't evidence — which, given a thin report, restates
the goal. It must lean on **what the report observed to exist**.

- State plainly that the report describes a codebase that **may already implement much of the goal**.
- Ask only for what the report shows is **absent or incomplete**, scoped to this tier.
- Keep the plain-statement rule (no rationale) — that part worked.

**NON-GOAL — do not build semantic dedupe.** It was proposed and rejected: a re-derived gap is a
*symptom* of the report not observing the codebase. Dedupe would hide the defect P11.3/P11.4 fix.

**Assertions:** a report that describes an implemented feature yields no task for that feature.

---

### P11.5 — The goal-alignment lens must survive to report

**Why:** 3/3 rounds it produced no report (72 chars, 48 chars, one narration line) — while the other
two lenses reported fully every round. Its prompt drives *acting* ("test thoroughly, checking each
function" → 30+ commands) where the others drive *reading*. The turn ends before the write-up. In
round 3 it traced a real `_repl_quote_text` parsing bug and lost it.

Split observation from reporting:
1. an exploration turn (act, exercise the product), then
2. a **fresh** turn that writes the report from what was found.

Or require incremental findings. Either way the report must not depend on surviving to the end of a
long acting turn.

**Plus a substance floor:** a lens body below a minimum length, or matching a narration shape
("let me…", "now I'll…"), is **not a report** — the round is `swept: false` and completes nothing.
P10's `swept = bool(reports)` guard defends the zero and leaves the near-zero open.

**Assertions:** a 72-char preamble body yields `swept: false`; gap analysis does not run on it.

---

### P11.6 — Bound the subjective lenses

**Why:** by round 3 `project_documentation` called the history *"above average"* and still emitted
four more suggestions. An aesthetic observer asked "how could this be better?" always answers, so
`clean_code` and `project_documentation` have **no fixed point** — they alone prevent zero.

Options (pick one, measure):
- a **severity floor** — only defects, never preferences, reach the queue;
- **retire a lens for the scope** once a round yields no *defect-grade* finding;
- cap each lens's lifetime contribution per scope.

**Assertions:** a lens reporting only preferences propagates zero tasks.

---

## Traps

1. **`harness` is the gym ledger.** Exclude it by name in the wipe. Losing it loses every run record.
2. **A greenfield force-push is irreversible on the remote.** Operator-gated; never inside a
   default `up` or an unattended path.
3. **`plan_only` gates tools, not shell.** Phrasing carries the load; mechanism is the backstop.
4. **Config defaults OFF** (P10 trap 4) — the unit suite counts worker wakes.
5. **A vanished report is not an empty one.** Distinguish "the lens found nothing" from "the lens
   never spoke" — that distinction is the whole of P11.5.
6. **Do not add semantic dedupe.** See P11.4.
7. **Never commit or push on the operator's behalf unless asked.**

---

## Definition of done

1. A gym rotation starts on a single-commit, no-history arena.
2. Neither plan turn produces commits; the implementer does.
3. Decomposition precedes gap analysis, and gap analysis runs against the selected scope's goal.
4. A report describing an implemented feature yields no task for it.
5. A truncated lens body is `swept: false`, not a silent zero.
6. A round of pure preference propagates zero tasks.

## Validation

Re-run the gym on a greenfield arena with a goal byte-identical to gym-009. Success is **a task
count that decreases to zero** and a scope that completes on zero propagation rather than the cap —
verified against the GitHub remote and the audit, never the org's self-report. The product
judgement remains the operator's.
