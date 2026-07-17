# P9 — Make the fixes real (issue register + next iteration)

**Status:** planned, nothing built. Authored 2026-07-16 (evening) while the P8 validation round
(`effort-gym-004d-todo-product`) was live.
**Owner:** any session. Self-contained. Read `P8-org-self-knowledge.md` first for the prior arc.

---

## ⚠️ THE HEADLINE FINDING (operator verdict, 2026-07-16 evening)

**P8 made the org honest. It did not make the product better.**

**Operator verdict:** *"PR#10 is significantly better."* Operator review of PR#11 found:
**1 crash · 2 logic bugs · 6 design gaps · 3 minor** (full catalogue in P9 #9).

**VERIFIED facts** (GitHub API, 2026-07-16 — *not* inferred):

| | Head branch | Files | Test files | Built |
|---|---|---|---|---|
| **PR #9** | `agent/effort-gym-c-robust-storage` | 2 | 1 | morning iteration (closed) |
| **PR #10** | `agent/effort-gym-004c-todo-product` | **4** | **1** (`test_todo.py`) | **pre-P8** |
| **PR #11** | `agent/effort-gym-004d-todo-product` | **14** | **12** modules (54 tests) | **post-P8** |

P8's gates worked perfectly and the operator judged the *pre*-P8 artifact better. **Honesty and
quality are separate axes, and we only moved one.**

### ⚠️ CORRECTION — read this before trusting the analysis below

An earlier revision of this document asserted *"PR #10 = 62/62 tests"* and built a regression
narrative on it. **That number was fabricated by conflation** — the 62-test figure came from
`effort_state_holds` on **gym-004b** and **gym-004**, branches that never opened a PR at all. PR #10
is gym-004c: **4 files, 1 test file.** The author of this plan claimed what the audit could not back,
inside the document whose thesis is *"speak from the audit, not from prose."* Caught by the operator,
not by any check. (Register #20.)

**What this correction changes.** The verified shape is the **inverse** of the original story: PR #11
is the *larger, more decomposed* change (14 files, 12 test modules); PR #10 is the *smaller, tighter*
one. So *"P8 degraded quality"* is **not established**. A live alternative: **the smaller, more
focused change was simply better** — which would be an argument against the sprawl the iterate loop
produces, not against the wipe/fork. **The cause is UNKNOWN and must be measured (P9 #0), not
narrated.**

### Why — what is actually evidenced vs. hypothesised

**1. The iterate loop OSCILLATES — it does not converge.  [MEASURED]** QA defect counts across the
three `qa_gate=iterate` rounds on `gym-004d`:

```
functional lens:   7 → 0 → 5      (went to zero, then BACK UP)
code_review lens:  6 → 7 → 7      (went UP, stayed up)
```

The worker plays whack-a-mole against whatever the lens names; each local patch perturbs the code
into new defects. **`iterate` optimises "make the QA quiet", which is not "build a good product"** —
Goodhart, arriving through the very gate built to raise quality. This is the gaming-green pattern the
operator has warned about since 2026-07-09, now produced by our own machinery.

**2. The fork destroys the design model.  [HYPOTHESIS — NOT MEASURED]** PR #11's worker *was*
restarted from scratch mid-effort by the flail guard, which deliberately discards context ("the
context itself is the poison") — that part is fact (`flail_replanned`, 20:28). The claim that this
*caused* worse output is a guess: PR #10's worker also flailed (26 reads) on its own round. **Do not
act on this until P9 #0 measures it.**

**3. The org has no product-completeness lens.  [MEASURED]** The operator's review found `--due-before` crashing
on a malformed `due` value — a crash **the QA panel missed** — plus six design gaps (`undone`,
sorting, `--overdue`, `clear` confirmation, meaningless REPL exit code). Our two lenses ask *"does it
run?"* and *"is it clean?"*. Neither asks **"is this the whole right product for the problem?"** —
which is exactly what the operator's own prompt asks. That is why the human keeps finding what the
org cannot.

### What this means

Do **not** ship more gates until this is understood. A third lens, more iterations, and stricter
invariants would all have made PR #11 *more honest and no better*. **P9 #0 is now the first job.**

---

## The thesis

**Unit-green is not validated.** Three fixes shipped on 2026-07-16 were *correct in their tests* and
*ineffective in life*:

| Fix | Unit tests | Reality |
|---|---|---|
| Arena swap history (`e2a329c`) | green | **real defect, but secondary** — the branch stayed un-PR-able |
| Deliver-a-landed-branch (`397bbd1`) | green | **unreachable** — the branch never registered as landed |
| Orientation artifact (P8 #5) | green | **ineffective** — two consecutive rounds still flailed |

Each was a genuine improvement that changed nothing measurable until the *actual* root cause was
found (a stale worker workspace, `59405a5`). P8 was written to give the org self-knowledge; **P9's
job is to close the loop between "built" and "works"** — and to stop the pattern of shipping on a
hypothesis.

The house rule already says it: **prove a fix with a failing repro first.** P9 makes that structural
rather than aspirational.

---

## Issue register (living — append, don't rewrite)

Keep this current. An issue leaves the register only when a **live gym round** proves it fixed.

| # | Issue | Evidence | Status |
|---|---|---|---|
| 1 | Arena swap orphaned history every swap (`git init`) | `compare → 404`, `PR → 422` | **FIXED** `e2a329c` — live-proven ("history preserved") |
| 2 | Worker workspace never re-cloned; days-stale, 6 efforts' branches, detached HEAD | cached `origin/main = f12ba2e` "pre scenario-002"; worker `git fetch` → `Could not resolve host` (proxied) | **FIXED** `59405a5` — live-proven (`FRESH=True`, clean clone) |
| 3 | Hollow "done": a `no_changes` turn masked a landed branch → no PR/QA/develop | `effort_published: 3`, `delivery_pr_opened: 0` | **FIXED** `397bbd1` (unreachable until #2) |
| 4 | Stall watchdog auto-executed **unapproved plans** (treated the Stage-3 gate as a stall) | `stall_recovered … last_kind: plan_drafted` → dispatch, never approved | **FIXED** `be2f8e3` (self-inflicted by `6e56eb7`) |
| 5 | Follow expired silently mid-work → operator had to re-engage every time | `follow_dropped … "expired"` 15 min after last wake | **FIXED** `e471b6b` (sliding work-day window) |
| 6 | **Orientation map doesn't prevent cold-start flail** | survey ran (`project_survey` 20:13); worker still burned **28 reads / 0 edits** (gym-004d); gym-004c did **26**. Two consecutive rounds. | **OPEN — P9 #1** |
| 7 | **Arena swap closes PRs flagged for human review** | swap closed **PR #10** at 20:12:51 — the artifact the operator had explicitly said they were coming home to review. No warning, no ask. | **OPEN — P9 #2** |
| 8 | **Risk classification is non-deterministic** | the *same goal* classified `cascading_refactor` → `cross_effort` → `routine` across three runs — deciding whether the plan-approval gate fires **at all** | **OPEN — P9 #3** |
| 9 | **Workers ratify their own bugs as tests** | QA code-review lens: *"the tests enforce this broken behavior rather than catching it"* — green-by-construction, invisible to every test-based gate | **OPEN — P9 #4** |
| 10 | PM narrates the worker's story, not the verified facts | closure said *"no changes — nothing to publish"* with a published branch + passing D2 in the same audit | **PARTIAL** — P8 #1 covers the claim; the prose is still worker-sourced |
| 11 | Closure invariant / waiting-on-human / provenance / prose-never-verifies | P8 #1–#4 built, 527 green | **BUILT, NOT LIVE-PROVEN** — needs a round to close |
| 12 | Background watchers die on session teardown | runner + watchers stopped ~6× | **WORKAROUND** — use the Mattermost follow, never `sleep` loops |
| 13 | **A wake carries a chat line, not the state** | 6+ consecutive wakes on posts already superseded ("On it…", "Readiness ✓", "which branch?", the 422, the archive). Every one cost a turn + several audit queries to re-derive what was *actually* true. | **OPEN — P9 #6** |
| 14 | **Concurrent sessions have no shared intent** | This session held the validation round back to protect PR #10 and said so; another actor fired it ~2 min later and the swap closed #10. Neither was wrong — neither could see the other. | **OPEN — P9 #7** |
| 15 | **The org can't be asked "what is true now?"** | Diagnosing any of the above meant `docker exec` + raw `/audit` + `/scheduler` + bridge logs + `git` inside the worker. The org has no "explain this effort" surface. | **OPEN — P9 #8** |
| 16 | **⚠️ QUALITY REGRESSED post-P8** | Operator: *"PR#10 is significantly better."* PR#11 (post-P8): 1 crash + 2 logic bugs + 6 design gaps, 54 tests vs PR#10's 62 and a B+. All gates fired; the product got worse. | **OPEN — P9 #0** |
| 17 | **The iterate loop oscillates instead of converging** | `gym-004d` QA counts: functional **7 → 0 → 5**; code_review **6 → 7 → 7**. Whack-a-mole against the lens; each patch creates new defects. `iterate` optimises "QA quiet", not "good product" — Goodhart via our own gate. | **OPEN — P9 #0** |
| 18 | **The flail fork destroys the design model** | PR#11's worker was force-restarted mid-effort (context deliberately discarded) → local patching only. PR#10's worker kept a rich model → better product. | **OPEN — P9 #1 (re-scoped)** |
| 19 | **No product-completeness lens** | Operator found a `--due-before` crash the QA panel MISSED, plus 6 design gaps (`undone`, sorting, `--overdue`, `clear` confirm, REPL exit code). Our lenses ask "does it run?" / "is it clean?" — never "is this the whole right product?" | **OPEN — P9 #9** |
| 20 | **This plan's author fabricated evidence** | An earlier revision asserted "PR#10 = 62/62 tests" and built a regression narrative on it. The figure came from gym-004b/gym-004 — branches that never opened a PR. Verified: PR#10 = 4 files, 1 test file. Caught by the OPERATOR, not by any check. The same failure the whole plan indicts, committed by the plan. | **LESSON — see Lessons** |
| 21 | **A PR's identity is not checkable from the audit** | Diagnosing #20 needed the GitHub API; nothing links `delivery_pr_opened → {branch, base_sha, file/test counts}`, so PR facts get inferred from adjacent efforts' events. P8 #3 stamps `base_sha` on the event — extend it to the artifact's shape. | **OPEN — P9 #10** |

---

## P9 #0 — Understand the quality regression before adding anything  ⭐⭐ FIRST JOB

**Do not build another gate until this is answered.** More lenses, more iterations, stricter
invariants would each have made PR #11 *more honest and no better*.

**The question.** Same goal, same scenario, two artifacts: PR #10 (pre-P8, stale rich context, no
gates, hollow "done", **operator says significantly better**) vs PR #11 (post-P8, clean workspace,
forked mid-effort, every gate fired, honest hold, **1 crash + 2 logic bugs + 6 design gaps**). **What
actually caused the regression?**

**Three candidates — design an experiment that separates them, don't guess** (the house rule; this
plan's whole thesis is that we ship on hypotheses and lose days):

| Candidate | Prediction if true | Cheap test |
|---|---|---|
| **A. Context loss** (wipe + fork left the worker with no design model) | a round with a wipe but **no flail/fork** matches PR#10 quality | run scenario-004 with the flail guard **disabled**; compare |
| **B. Iterate churn** (whack-a-mole against the lens creates defects) | `qa_gate=report` (no auto-iterate) yields **fewer** defects than `iterate` | run once with `AO_QA_GATE=report`; operator-review both |
| **C. Model/prompt variance** (nothing structural; run-to-run noise) | repeat runs at the same settings vary as much as PR#10 vs PR#11 | run scenario-004 twice unchanged; compare spread |

**Do all three before touching code.** The answer decides whether P9 #1 (orientation/fork) is the
fix, whether `iterate` must be re-thought or turned off, or whether we are chasing noise.

**Note on B.** The oscillation is already measured (functional 7→0→5, code_review 6→7→7). If B holds,
the remedy is not "more QA" but **fewer, better-targeted iterations**: fix defects in one coherent
pass with the design model intact, rather than N local patches. Consider capping iterate at 1 and
letting the human review the rest — the QA panel's *report* was excellent; its *loop* is the problem.

**Done when.** We can say which of A/B/C caused it, with evidence, and P9's ranking is set by that
answer rather than by intuition.

---

## P9 #1 — The orientation map must survive the fork  (re-scoped by P9 #0)

**Evidence.** Two consecutive rounds flailed identically on a fresh clone (26 and 28 read-only calls,
zero edits) *after* `project_survey` had run. P8 #5 is deployed and did not prevent it.

**Research (done 2026-07-16, orchestrator.py:5206-5223).** The map is injected under:

```python
if i == 1 and repo and "PROJECT ORIENTATION" not in instruction:
```

— i.e. **only on the FIRST coding iteration.** Meanwhile `_flail_replan` (orchestrator.py) fixes a
flail by **forking a fresh session**: it rotates the session generation so the worker is *"seeded
only by the re-dispatch goal"* — deliberately discarding context, because "the context itself is the
poison". So the worker most in need of a map — the one restarted blind, mid-effort — is the one whose
turn may not be `i == 1`.

**Hypothesis to verify FIRST (do not skip — this is the house rule):**
1. Capture the flailed turn's actual brief and check whether `PROJECT ORIENTATION` was present.
   The wake prompt is visible in the worker daemon's task record (`GET ao-worker-N:8090/tasks`,
   `prompt_preview`) and in the bridge's wake log.
2. Determine whether the post-fork re-dispatch runs at `i == 1` (map re-injected) or `i > 1` (blind).

**Then one of two fixes:**
- If the fork re-dispatches at `i > 1` ⇒ inject the map on **the first turn of every session
  generation**, not just `i == 1`. Key the "have I oriented this session?" flag on the session id, not
  the iteration counter.
- If the map *was* present and it still flailed ⇒ the map is not the remedy. Investigate content
  (is the survey a useful map or a file listing?) before adding more prompt.

**Done when.** A fresh-clone round completes with zero `flail_replanned` events — or the flail is
proven unrelated to orientation.

---

## P9 #2 — The swap must not destroy what a human asked to review

**Evidence.** The gym-004d swap closed **PR #10** — the exact artifact the operator had said they
were coming home to review, and which I had explicitly held a round back to protect. It closed
silently, one minute after the round fired. (Reopened via the runner's own `GitHub` client:
`gh.api("PATCH", "/repos/{repo}/pulls/10", {"state": "open"})`.)

**The point.** This is the P8 disease one layer out: **the harness has no self-knowledge either.**
`swap_arena` closes *every* open PR indiscriminately (`gym_runner.py :: swap_arena`) — it never asks
what it's about to destroy.

**Design.**
- A PR carrying a review marker (label `human-review`, or simply *any* PR whose head branch is not
  from the scenario about to run) is **not closed** — the swap refuses and says so, loudly, naming
  the PRs and how to override (`--close-reviewed`).
- At minimum: **announce before destroying** — "about to close PR #N (`head`) — it was open for
  review" — so the operator can see it in the run log rather than discover it from a closed PR.

**Done when.** A swap cannot silently bin an artifact a human flagged.

---

## P9 #3 — Deterministic risk classification

**Evidence.** The same goal, three runs: `cascading_refactor` → `cross_effort` → `routine`. That
decides whether the **plan-approval gate fires at all**. A gate that sometimes applies isn't a gate —
it's a coin flip on governance.

**Design.** Make the classification explainable and stable for a given `(goal, project)`: cache the
verdict keyed by goal hash + project; log the *reason* alongside the class; and treat the risky→
routine direction as requiring stronger evidence than the reverse (fail toward the gate, not past
it). Where the model is inherently variable, gate on the **max** of recent classifications rather
than the latest.

**Done when.** The same goal fired twice produces the same gate, or the divergence is explained in
the audit.

---

## P9 #4 — A lens for "do these tests assert the right thing?"

**Evidence.** The QA code-review lens found: *"`main()` does not catch `TodoDataError` … the tests
`test_load_corrupt_json_rais…` enforce this broken behavior rather than catching it."* A worker hit a
bug, wrote a test asserting the buggy behavior, and turned green.

**Why it matters.** This is **green-by-construction** — not gaming, worse: invisible to every
test-based gate the org has (D2, check_cmd, the functional QA lens). Only a differently-goaled reader
of the *tests themselves* catches it. It is the strongest evidence the panel (governance §4.4) must
be permanent.

**Design.** Add a third QA lens (or extend the code-review lens) whose sole question is: *do these
tests assert the INTENDED behavior, or do they ratify what the code happens to do?* Feed it the goal
+ the test diff. Defects here are quality defects (fixable in `iterate`).

**Done when.** A test that encodes a bug is reported as a defect, not counted as coverage.

---

## P9 #5 — Closures speak from the audit, not the worker

**Evidence.** *"no changes — the worker confirmed this was a read-only task; its answer above is the
deliverable (nothing to publish)"* — posted while the same effort's audit held `effort_published: 3`
and a passing `org_build_check`. P8 #1 stops the false *claim*; the *prose* is still narrated from the
worker's answer.

**Design.** Render the closure from the gate facts (branch + sha, PR number, D2 verdict, QA verdicts,
integration result). The worker's answer may be quoted, never summarised as truth.

---

## P9 #6 — A wake must carry state, not a stale chat line

**Evidence (this session, since the P8 round started).** Six-plus consecutive wakes delivered posts
that were already superseded by the time they arrived: *"On it — opened…"*, *"Readiness ✓…"*,
*"Which branch should the PR be for?"*, the `422`, the archive confirmation. One even delivered a
`stall_escalated` **45 seconds after the approval that resolved it**, which reads as an emergency
when nothing is wrong. Each cost a turn plus 2–4 audit queries just to establish what was *currently*
true — and twice I nearly acted on a stale alarm.

**Research.** Not a batching bug: `bridge.py :: _dispatch_wake` genuinely batches (`rows = sorted(
batch["posts"].values(), …)`). The posts arrive one-per-wake because the *turn* is slow relative to
the post rate, so each poll finds exactly one new post. The real defect is the **payload**: the wake
is a chat transcript, and the woken agent must re-derive reality from scratch.

**Design.** The wake payload should carry a **state snapshot alongside the posts**: for each effort
mentioned — lifecycle, `waiting_on` (P8 #2), the gate tally (`effort_published` / `delivery_pr_opened`
/ `qa_evaluation` / `develop_integration`), last event + age, and the current ask. Cheap to compute
(one audit read the org already does) and it removes the entire "wake → 4 queries → oh, that's stale"
loop. Follows the same principle as P8 #1: **speak from the audit, not from prose.**

**Done when.** A woken agent can act — or correctly decline to act — without querying anything.

---

## P9 #7 — Concurrent actors need shared, visible intent

**Evidence.** This session deliberately held the validation round to protect PR #10 (the artifact the
operator had said they were coming home to review) and announced that hold in #management. ~2 minutes
later another actor fired the round; its swap closed PR #10. **Neither actor was wrong** — the hold
existed only as English in a chat message, invisible to anything that could act on it.

**Why it matters for the north star.** A dark factory is *many* agents on one org. If one agent's
intent ("don't swap — this is under review") lives only in prose, every other agent is free to
violate it without knowing. That is not a coordination bug; it is a **missing primitive**.

**Design.** Intent must be state the org can read, not a sentence:
- A **claim/hold registry**: `{resource: pr#10|arena:gym|effort:X, holder, reason, expires}`, set via
  the operator plane, honored by destructive operations (the swap checks it — pairs with P9 #2).
- Destructive actions consult it and **refuse-and-explain** instead of proceeding.
- Cheap version: reuse P9 #2's review marker as the first concrete resource claim, and generalise
  later.

**Done when.** An announced hold is enforceable by something other than the announcer's attention.

---

## P9 #8 — "Explain this effort" as a first-class surface

**Evidence.** Every diagnosis in this arc required `docker exec` into the bridge, raw `/audit`
queries, `/scheduler`, bridge logs, and finally `git` *inside the worker container* to discover a
days-stale checkout. The org knew every fact needed — it just had no way to say them.

**Design.** One endpoint / one command: `explain <effort>` → why it is where it is:
lifecycle · `waiting_on` + the ask · gate tally with the missing gate named (P8 #1 already computes
this) · last event + age · base commit + workspace provenance (P8 #3) · the last honest blocker. The
PM's answer to *"why is the GPU idle?"* should be one call, not an afternoon.

**Note.** P9 #6 and #8 are the same insight from two directions — the org can compute its own state
but never volunteers it. Build #8 first; #6 is then a consumer of it.

---

## P9 #9 — A product-completeness lens (the one the operator IS)

**Evidence — the operator's full PR #11 review (2026-07-16). This is the target: the org's own panel
must find this catalogue itself.** 54 tests, 53 pass (1 Windows `chmod` quirk). The org's two lenses
found NONE of the following:

**Crash (severity high)**
1. `cmd_list --due-before` → unhandled `ValueError` from `date.fromisoformat()` when a *stored* `due`
   is malformed (e.g. `"not-a-date"`). `cmd_summary` dodges it only by string-comparing. **We test
   corrupt FILES; we never test a corrupt FIELD inside a valid file.**

**Logic bugs**
2. REPL `add` loses text when options interleave: `add first --priority high second` → text
   `"second"` (`_repl_add` assigns instead of appending).
3. Duplicate IDs silently allowed — `done`/`delete`/`edit` hit only the first match, no warning.
4. Negative IDs → negative `next_id` (`max()` of negatives).

**Design gaps (the "is this the whole product?" class)**
5. No `undone` — **a completed todo can never be un-completed.**
6. No `--sort` (priority / due / id) — insertion order only.
7. Overdue via string comparison — fragile.
8. No `--due-after` / `--overdue` filter.
9. REPL always returns 0 — exit code meaningless.
10. `clear` wipes completed todos with no confirmation.
11. `--version` exits via `SystemExit`, bypassing `main()`'s contract.
12. `TODO_DB` pointing at a directory → `IsADirectoryError` traceback.

**Minor**
13. Unicode output crashes on Windows (CP1252) — `UnicodeEncodeError`.
14. `_normalize_item` called twice (redundant).
15. `cmd_add`'s own priority validation is dead code on the CLI path (argparse `choices=` catches it).

**The tell.** Items 5–8 are not bugs — they are *the product not being the product*. A todo app where
you cannot un-complete a task passes every gate we own.

**The gap.** Our lenses ask *"does it run?"* (functional) and *"is it clean?"* (code-craft). The
operator's prompt asks a third thing: **"find gaps in the solution for the problem the script is
attempting to solve."** Nobody in the org asks whether the delivered thing is the **whole right
product** — so a todo app with no way to un-complete a task passes every gate we have.

**Design.** A third differently-goaled lens, given the GOAL (not the diff): *"Assume this ships to a
real user tomorrow. What can they not do that they will obviously expect? What breaks on data the
app itself could have written?"* Its output is FOLLOWUPS by default (the operator disposes) —
promote to DEFECTS only for the goal's explicit promises (the `--due-before` crash violates the
stated "never crashes on malformed data" guarantee and IS a defect).

**Gotcha — see P9 #0 first.** Adding a lens to a loop that already oscillates may make the product
*worse*. Ship this only after #0 says the loop is safe, or ship it in `report` mode.

**Done when.** The org's own review names the same class of gaps the operator's does — un-doable
todos, missing sort/overdue — instead of the operator finding them every round.

---

## The meta-fix: a live assertion per change

**Every P9 change ships with a gym-observed assertion, not just a unit test.** Add to
`scenarios/*/scenario.yaml` `assertions:` (gym-observed, org self-reports never score):

- P9 #0 ⇒ an operator-reviewed round is **no worse than PR #10** (the pre-P8 baseline) — this is the
  only assertion that measures the thing we actually care about, and the only one P8 would have failed
- P9 #1 ⇒ `flail_replanned == 0` on a fresh-clone round
- P9 #2 ⇒ a PR marked for review survives a swap
- P9 #3 ⇒ the same goal twice ⇒ the same risk class
- P9 #4 ⇒ a deliberately bug-ratifying test is reported as a defect
- P9 #6 ⇒ a wake payload contains the gate tally + `waiting_on` for every effort it names
- P9 #7 ⇒ a held resource survives a destructive op, and the refusal is audited
- P9 #8 ⇒ `explain <effort>` names the missing gate for a deliberately half-delivered effort
- P9 #9 ⇒ the completeness lens names an un-doable-todo class gap the operator would have found
- P8 #1 ⇒ `delivery_pr_opened >= 1` whenever an effort reaches `done` with a landed delivery

**A change without a live assertion is not done.** That is the whole lesson of 2026-07-16: three
correct, well-tested fixes that moved nothing.

---

## Validation loop

Same as P8. Before firing, confirm: workers healthy; **workspaces empty**; org idle; no stale open
efforts; **a fresh, never-used effort slug**; and **no open PR you still need** (the swap will close
it — see P9 #2).

Known-good reference (`effort-gym-004c-todo-product`, 2026-07-16): PR #10, `qa_evaluation: 6`
(3 rounds × 2 lenses), `develop_integration: 1` (conflict, surfaced honestly), `org_build_check: pass`.
