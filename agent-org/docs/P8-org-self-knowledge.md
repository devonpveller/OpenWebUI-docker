# P8 — Org self-knowledge

**Status:** planned, none of it built. Authored 2026-07-16 after a full day of live gym runs.
**Owner:** any session. Self-contained — you need no prior context beyond this file.

---

## The thesis (read this first)

The org **built beautifully and delivered nothing, and nobody noticed for hours.** On 2026-07-16 it
produced a complete, green todo product **twice** (62/62 tests, every feature incl. an interactive
REPL and atomic writes) and closed both efforts as **“done”** with `delivery_pr_opened = 0` — no PR,
no QA panel, no develop-integration. Every failure was invisible until a human hand-queried the
audit table.

**Capability is not the gap. Self-knowledge is.** The org never asks itself *“I just claimed done —
did a PR actually open?”*

These five changes close that gap. They are independent and individually shippable; ship them in
order — **#1 alone would have caught every failure of that day, at the moment it happened.**

---

## Orientation for a fresh session

| Thing | Where |
|---|---|
| Orchestrator (the ~9k-line core) | `agent-org/agent-bridge/app/orchestrator.py` |
| Worker dispatch / focus | `agent-org/agent-bridge/app/modules/router.py` |
| Worker daemon client | `agent-org/agent-bridge/app/worker/harness.py` (`FakeHarness` = the test double) |
| Config (pydantic, env prefix `AO_`) | `agent-org/agent-bridge/app/config.py` |
| Tests | `agent-org/agent-bridge/tests/` |
| Run tests | `cd agent-org/agent-bridge && .venv-test\Scripts\python.exe -m pytest tests -q` (~6 min, 513 green) |
| Deploy | `cd agent-org/docker && docker compose build agent-bridge && docker compose up -d agent-bridge` |
| Health | `docker exec agent-bridge python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').status)"` |
| Audit for one effort | `GET http://localhost:8000/audit?effort_id=<id>` (inside the container) |
| Gym arena (validation) | `d:\Open WebUI\ai-orchestration-gym` — `python runner/gym_runner.py --auth app run scenario-004-python-todo-product --yes-provision` |

**House rules (non-negotiable):**
- Never commit or push on the user's behalf unless explicitly asked.
- Merges to `main` stay human-gated. The gym is the only place autonomy may be widened.
- Prove a fix with a **failing repro first**. (The author of this plan violated that rule and burned
  hours fixing a *secondary* cause — see "Lessons" at the bottom.)
- Default new config fields **off**; enable via `AO_*` in `agent-org/docker/docker-compose.yml`. The
  unit suite counts worker wakes, so a new wake with a default-on flag breaks ~30 tests.

---

## 1. Closure invariant — the PM may not claim "done" without proof  ⭐ highest leverage

**Evidence.** `effort-gym-004b-todo-product` closed **“done — read-only, nothing to publish”** while
its own audit read `effort_published: 3`, `delivery_pr_opened: 0`, `qa_evaluation: 0`,
`develop_integration: 0`. The product was real (62/62 tests) and completely undelivered. Same for
`effort-gym-004-todo-product`. The org's *report* and its *audit* disagreed and nothing noticed.

**Where.** `orchestrator.py :: _finish_effort` — the single closure chokepoint every effort passes
through.

**Design.** Immediately before the effort is marked `done`, assert the gates that *should* have run
for this delivery actually did, by reading the effort's own audit:

- delivery claimed (a branch was published / `delivery.landed`) ⇒ `delivery_pr_opened >= 1`
- `qa_gate != off` and a delivery landed ⇒ `qa_evaluation >= 1`
- `develop_integration` on and delivery accepted ⇒ a `develop_integration` event exists (ok **or**
  an honestly-surfaced conflict — attempted is the invariant, not success)

If an invariant fails: **do not close done.** Audit `closure_invariant_failed`, post an honest
needs-attention naming exactly which gate is missing, and leave the effort open.

**Test.** `tests/test_closure_invariant.py` — a landed delivery whose PR never opened must NOT reach
`lifecycle == done`; it must surface `closure_invariant_failed` and name the missing gate. Assert the
happy path (all gates ran) still closes clean.

**Gotcha.** A genuine read-only/no-changes completion has no delivery and therefore no gates to
assert — invariants apply **only when a delivery landed**. See `_no_changes_acceptable` and the
`no_changes` branch of `_finish_effort` for that distinction (and note commit `397bbd1`, which
re-verifies the branch before honoring a read-only close).

**Done when.** An effort that fails to open a PR reports *"I could not deliver"* instead of *"done"*.

---

## 2. WAITING-ON-HUMAN as a first-class state

**Evidence.** `plan_drafted` is the **Stage-3 plan-approval gate** — the effort is parked in
`_pending_plan` correctly awaiting the operator's `approve <effort>`. Nothing in the audit,
`/scheduler`, or the status view distinguishes that from a *wedge*. A previous session misread it as
a stall and shipped commit `6e56eb7`, which added `plan_drafted` to `_STALL_MIDDISPATCH_KINDS` — so
the stall watchdog **auto-executed unapproved plans after 15 minutes**. It really did that to
`gym-004b` (`stall_recovered … last_kind: plan_drafted` → dry_run → dispatch, never approved).
Reverted in `be2f8e3`. The system *invited* the mistake: an idle GPU looks identical whether the org
is waiting on a human or broken.

**Where.** `orchestrator.py` — `_pending`, `_pending_plan`, `_pending_capability`,
`_pending_lifecycle` (the four hold dicts, ~line 863-874); `_sweep_stalled_efforts`;
`_STALL_MIDDISPATCH_KINDS`.

**Design.** Make "blocked on a human" explicit rather than inferred:
- Persist a `waiting_on` field per effort: `{gate: plan_approval|clarification|capability|merge,
  asked_at, ask}` — set when an effort parks in any `_pending_*`, cleared on decision.
- The stall watchdog **skips any effort with `waiting_on` set**, however long it idles — no timeout
  may ever bypass a human gate (§4.5). This makes rule-by-event-kind (fragile) unnecessary.
- Surface it: `/status` and the PM's tidy/board output separate **"waiting on you (N)"** from
  **"working (N)"** from **"wedged (N)"**, each with the ask.

**Test.** Extend `tests/test_stall_watchdog.py` — an effort with `waiting_on` set is never
`stall_recovered` at any age. Keep `test_watchdog_never_bypasses_the_plan_approval_gate`.

**Gotcha.** Dry-run/worker-plan states (`dry_run_*`, `worker_plan_approved`) genuinely auto-advance
with no human in the loop — they must **stay** watchdog-covered. Don't over-correct.

**Done when.** "Why is the GPU idle?" is answerable from the org in one call, and no watchdog can
ever advance an effort a human hasn't cleared.

---

## 3. Provenance on every claim

**Evidence — the root cause of the whole day.** Both workers were running **days-old checkouts**:
`ao-worker-1` had branches from `gym-002`/`gym-004`/`gym-004b` with HEAD on gym-004b's commits;
`ao-worker-2` was **detached at FETCH_HEAD**. Cached `origin/main` was `f12ba2e` — *"arena (pre
scenario-002)"*. The worker **cannot** refresh it: `git fetch` → `Could not resolve host: github.com`
(its git is proxied). Every arena swap re-rooted the real `main`, so branches pushed off that dead
lineage had **no common ancestor** with `main` → `compare → 404`, `POST /pulls → 422` → no PR → no
QA → hollow "done". A reopened effort's worker even read the *previous* round's finished branch and
reported *"all phases complete — no changes"*.

Partly fixed already: `59405a5` makes the router wipe + re-clone unless `(effort_id, repo)` proves
the checkout is for the same task (`router.py :: _ws_focus`). **Verified live:** `FRESH=True`, and
the workspace came back with only `main` off the live base.

**Remaining work — generalise it.** Provenance is still *assumed* everywhere else:
- Hand the worker its **expected base commit** in the brief and have it **assert** the checkout
  matches before doing work (it cannot discover this itself — proxied git).
- Stamp `base_sha` on `effort_published` / delivery events so a delivery states what it was built on.
- Refuse to act on unprovenanced state rather than inferring reality from a directory listing.

**Test.** A focus whose workspace base ≠ expected base must re-clone (extend
`tests/test_workspace_provenance.py`, which already covers task-change / reopen / failed-focus).

**Done when.** No claim ("built", "tested", "delivered") exists without the base commit it was made
against.

---

## 4. Prose never verifies

**Evidence.** `_no_changes_acceptable` (orchestrator.py ~4082) closes a **behavioral** goal when the
worker's answer text contains `REPRO:` and `AFTER: PASS`. That is the worker's *prose*, not an
org-observed fact — and it closed `gym-004b` while `effort_reproduction_verified: 0`. The org already
has a real red→green harness (`_org_reproduction_verified`, base=RED / head=GREEN); the no-changes
path simply bypasses it.

**Where.** `orchestrator.py :: _no_changes_acceptable` (behavioral branch), and the matching
backstop inside `_finish_effort`.

**Design.** Replace the marker regex with the **org-observed** harness. If the org cannot run the
reproduction, the honest outcome is *"not verified — needs your runtime check"* (that ladder already
exists and works well). General rule: **no worker sentence may cause a state change.**

**Test.** A behavioral goal whose worker output contains `REPRO:` + `AFTER: PASS` but which the org
did **not** independently verify must NOT close done.

**Gotcha.** Don't regress the honest-hold path — "not verified" is a *good* outcome, not a failure.

---

## 5. Orientation artifact per base commit

**Evidence.** Wiping the workspace (fix #3) removed contamination but introduced **cold-start
thrash**: on a freshly cloned, *tiny* template (one `todo.py` + tests) a worker burned **26 read-only
tool calls with zero edits** and tripped the flail guard (`flail_replanned`, 16:58). The guard
recovered it well (fork → plan mode → `worker_plan_approved` attempt 1 → building, ~90s, no human).
But the tension is real: stale context is poison, and no context is thrash.

**Design.** The answer is not to keep stale state — it's to make "clean" not mean "blind". The org
already runs a `project_survey`; cache that survey **keyed by base commit** and inject it into the
worker's brief on a fresh clone. Same base ⇒ reuse the map; base moved ⇒ re-survey once, share it
across efforts.

**Test.** Two efforts on the same base ⇒ one survey. Base moves ⇒ re-survey.

**Done when.** A wiped workspace costs a map lookup, not 26 blind reads.

---

## Bonus (cheap, real, found the same day)

- **PM narrates the worker's story, not the verified facts.** Closure text said *"no changes —
  nothing to publish"* while the audit had a published branch **and** a passing D2. Derive closure
  prose from the gates, not from the worker's answer. (Largely subsumed by #1.)
- **Risk classification is a coin flip.** The *same goal* classified `cascading_refactor`, then
  `cross_effort`, then `routine` across three runs — and that decides whether the plan-approval gate
  fires at all. A gate that sometimes applies isn't a gate. Make it deterministic/explainable per
  `(goal, project)`.
- **Tests can ratify bugs.** The QA code-review lens found *"the tests enforce this broken behavior
  rather than catching it"* — a worker hit a bug, wrote a test asserting the buggy behavior, and went
  green. This is **green-by-construction**, invisible to every test-based gate. Consider a panel lens
  that asks *"do these tests assert the RIGHT thing?"*. It's also the strongest evidence the
  differently-goaled panel must stay permanent (governance §4.4).

---

## Validation loop

Ship a change → `pytest tests -q` (513 green baseline) → deploy → fire a gym round:

```
cd "d:\Open WebUI\ai-orchestration-gym"
python runner/gym_runner.py --auth app preflight scenario-004-python-todo-product
python runner/gym_runner.py --auth app run scenario-004-python-todo-product --yes-provision --commit
```

**Before firing, confirm the starting state** (this matters — a bad start invalidates the run):
workers healthy; **workspaces empty** (`docker exec ao-worker-N sh -lc 'ls -A /workspace | wc -l'`);
org idle; no stale open efforts on `gym`; and a **fresh, never-used effort slug** in the scenario goal
(a reused slug reopens the old effort and collides with its stale remote branch).

**Score gym-observed, never from the org's self-report** — read the audit gate tally:

```
delivery_pr_opened >= 1 · qa_evaluation >= 2 (both lenses) · develop_integration >= 1 · org_build_check pass
```

Known-good reference (2026-07-16, `effort-gym-004c-todo-product`): PR #10 opened, `qa_evaluation: 6`
(3 rounds × 2 lenses), `develop_integration: 1` (conflict, surfaced honestly), D2 pass.

---

## Lessons worth not relearning

- **Reproduce before fixing.** The author fixed the arena swap (`e2a329c`) on a hypothesis. It was a
  real defect but **secondary**; the actual cause was the stale workspace, and hours were lost.
- **A quiet GPU is not evidence of a bug.** Twice it was the org correctly waiting at a human gate.
- **Background bash watchers do not survive session teardown.** Use the Mattermost follow
  (`follow_thread`, wakes on bot-pm posts) to observe async work — not `sleep` loops.
- **The org's building is not the problem.** It has produced a complete, tested product three times.
  Every hour lost this day went to *delivery plumbing* and *not knowing what it had done*.
