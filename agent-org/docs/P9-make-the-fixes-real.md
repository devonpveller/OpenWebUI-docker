# P9 — Make the fixes real (issue register + next iteration)

**Status:** planned, nothing built. Authored 2026-07-16 (evening) while the P8 validation round
(`effort-gym-004d-todo-product`) was live.
**Owner:** any session. Self-contained. Read `P8-org-self-knowledge.md` first for the prior arc.

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

---

## P9 #1 — The orientation map must survive the fork  ⭐ start here

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

## The meta-fix: a live assertion per change

**Every P9 change ships with a gym-observed assertion, not just a unit test.** Add to
`scenarios/*/scenario.yaml` `assertions:` (gym-observed, org self-reports never score):

- P9 #1 ⇒ `flail_replanned == 0` on a fresh-clone round
- P9 #2 ⇒ a PR marked for review survives a swap
- P9 #3 ⇒ the same goal twice ⇒ the same risk class
- P9 #4 ⇒ a deliberately bug-ratifying test is reported as a defect
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
