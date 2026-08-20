# P17 — plan-gate, sweep and delivery integrity

Evidence base: **gym-015** (`effort-gym-015-todo-product`), 2026-07-20, five drain rounds.
Baseline for contrast: gym-013.

**This document was audited after drafting.** Every claim below was re-verified against the
event log and the git trees by an independent pass. Three claims from the working draft were
**refuted** and have been corrected or removed — they are listed in "Corrections from audit" at
the end, because the errors are themselves informative about how easily an observer overstates.

Findings are marked VERIFIED with the command or event that establishes them.

---

## Why gym-015 was productive

It was a **degraded** run: a lens truncated, a plan turn overstepped, a re-clone landed between
a commit and its push, and a dispatch went to a stale workspace. Several findings are only
reachable once something else has already gone wrong, which is why six cleaner rounds never
surfaced them.

**gym-013 remains the trustworthy convergence baseline** — all three lenses reported in both
rounds, `gap_analysis` ran both times, and it converged 1 → 0 with `scope_completed` at
round 2/40.

---

## F16 — the plan turn inherits the builder's session, so it plans from memory (VERIFIED, severity: high)

**Distinct from F15.** F15 is about *when* steps run; F16 is about *what context* they run in. The
sequence can be perfectly ordered and still be worthless if the planning step is looking at its
own memory instead of the codebase.

### What happened

The build turn and the plan turn share one session — `effort-gym-015-todo-product`, no suffix:

```
4888  10:38:29  acquire worker-2  session=effort-gym-015-todo-product   ← BUILD (7 min)
4890  10:45:33  wake_done
4892  10:45:50  worker_plan_rejected  attempt=1
4893  10:45:50  acquire worker-2  session=effort-gym-015-todo-product   ← RE-PLAN, same session
4895  10:46:11  wake_done                                               ← 21 SECONDS
4897  10:46:23  worker_plan_approved  attempt=2
4898  10:46:23  acquire worker-2  session=effort-gym-015-todo-product   ← EXECUTE, same session
```

A 21-second "plan" following a 7-minute build in the same session, whose content was:

> *"The work is already complete — all features implemented, all 51 tests passing, acceptance
> corpus verified, and committed."*

That is **recall, not observation.** The worker was not examining the repository; it was
reporting its own prior turn from context.

### It violates the design's own principle

`ORCHESTRATION-DESIGN.md` §11:

> *"escalation is **cleared-context adversarial review** ... A fresh reviewer isn't carrying the
> builder's rationalizations, and it emits concrete test results, not softened prose — which
> defeats the *generation* half of the loss."*

P10.1 applied exactly this to the lenses — each gets its own session (`~lens541485`, `~lens541486`,
`~lens541487`) and they alternate across workers. **The plan step never received the same
treatment**, despite being the step whose entire job is to assess what remains.

### What it produces

- **Zero-change plans.** A worker asked to plan against work it just completed answers "already
  done" — correctly, from its own memory, and uselessly, because nothing independently verified
  the claim.
- **It is the hidden driver of F7.** The plan gate's contradictory behaviour makes more sense in
  this light: the rejected plan ("fix two test issues") was the builder accurately reporting the
  *remainder* it remembered; the approved restatement ("already executed and committed") was the
  same memory re-narrated. The gate was adjudicating between two recollections, neither anchored
  to the repository. Fixing F7 without F16 leaves the gate reading better-worded memory.
- **It silently couples plan quality to session health.** A long or degraded builder session
  produces a degraded plan, with no signal that the plan was never grounded.

### Reconciling with F15 — workspace state vs session context

These two fixes pull in apparently opposite directions and must be specified together. They are
not in conflict because **workspace state and session context are different things**:

| | F15 says | F16 says |
|---|---|---|
| **Workspace** | execution continues in the workspace the plan was written against — do **not** refresh between gate and execute | (no constraint) |
| **Session** | (no constraint) | the plan turn runs in a **fresh** session, not the builder's |

Combined target sequence:

```
refresh workspace
  → plan turn:    FRESH session, read-only workspace, must observe the codebase
  → gate
  → execute turn: may carry the plan's session, SAME workspace, write access
```

The plan is grounded because its session is clean; the execution is coherent because its
workspace is the one the plan described.

### Fix

- Give the plan turn its own session id (`~planNNN`), as the drain loop already does for lenses
  and as it partially does for drain plan turns (`~plan541488`) — the gap is the **initial**
  build→plan→execute cycle, which reuses the bare effort session throughout.
- Prefer a **different worker** from the one that built, matching the lens rotation, so filesystem
  and session state are both fresh.
- Require the plan turn to cite observed evidence (a command it ran, a file it read) rather than
  asserting completeness — per §11, "concrete test results, not softened prose". This is the same
  move as F5/F11: convert a read into an execution.
- Test: a plan turn dispatched immediately after a build turn on the same effort runs in a
  different session id, and a plan asserting "already complete" without a cited observation is
  returned for evidence.

### Note on scope

This applies to the **plan/assessment** step specifically. The lenses already have it (P10.1).
The execution step should *not* get a fresh session — it needs the plan's context. The reviewer
already runs cleared-context by design (§11).

---

## F15 — the dispatch sequence is inverted (VERIFIED, severity: high — supersedes F1/F2 framing)

**This is the root ordering defect. F1 and F2 are its two symptoms.** An earlier draft of this
document described both as independent bugs (a permissions gap and a validation gap); they are
one wrongly-ordered pipeline.

### The observed sequence

```
4939  10:58:52  acquire  ~plan541488     ← plan turn
4941  10:59:43  wake_done
4943  10:59:44  goal_change
4944  10:59:45  acquire  ~r1             ← turn that implemented and committed 33eae95
4946  11:02:34  wake_done
4948  11:02:57  worker_plan_approved     ← GATE runs here, after the work
4949  11:02:57  acquire  ~r1
4950  11:02:58  worker_project_set  fresh=TRUE   ← workspace WIPED here, after the gate
4951  11:10:07  wake_done                ← "execution" turn, starting from nothing
```

Round 5 reproduces it with a different write mechanism (`python3 -c` instead of `sed -i`),
committing `1b04400` before its approval.

### The inversion

```
actual :  plan(writable) → gate → refresh → execute
correct:  refresh → plan(read-only) → gate → execute(same workspace)
```

Two failures fall out of this directly, and neither is fixable at its own site:

1. **The gate is positioned after the side effects it authorises.** The PM's own words —
   *"I check it against the goal before any code changes"* — are false by construction. The turn
   being judged has already had write access to the workspace. This is F1.
2. **The refresh is positioned between the gate and execution.** Anything the prior turn left on
   disk is destroyed at that point. `33eae95` did not vanish because the refresh is buggy; it
   vanished because the refresh is scheduled at the wrong moment. This is F2.

Patching F1 alone (read-only plan turns) leaves a refresh that still discards state from any turn
that legitimately holds uncommitted work. Patching F2 alone (hash re-validation) detects the loss
after the fact instead of preventing it. **Re-ordering the pipeline removes both.**

### Fix

- Move the workspace refresh to **before** the plan turn. A turn should begin from a known state,
  not be reset partway through the plan→gate→execute cycle.
- Make the plan turn read-only (F1's filesystem-level fix), so nothing of value can exist on disk
  when the gate runs.
- Do **not** refresh between gate and execution — the execution turn continues in the workspace
  the plan was written against, which is also what makes the plan meaningful.
- Keep F2's hash re-validation as a backstop, not as the primary control.
- Test: assert event ordering per round — `project_set` precedes the plan turn's `worker_acquire`,
  and no `project_set` occurs between `worker_plan_approved` and the execution turn's
  `wake_done`.

### Related ordering smell (low severity, noted not filed)

Every checkpoint is dispositioned twice, two seconds apart, with opposite verdicts:

```
4903  10:49:39  checkpoint_dispositioned  {"checkpoint": "...:cp1", "cleared": false}
4904  10:49:41  checkpoint_dispositioned  {"checkpoint": "...:cp1", "cleared": true,
                                           "advisory": true, "reason": "machine-verified effort — review is advisory (D3)"}
```

Same at 4954/4955. This is D3 behaving as designed (advisory clearing for machine-verified
efforts), but the audit trail records a contradiction rather than a single reasoned disposition.
Emitting one `cleared=true, advisory=true` event, or a distinct
`checkpoint_advisory_override`, would make the log honest without changing behaviour.

### Checked and NOT a defect

All five `effort_published` events carry `firm=false`, which resembles publishing before
verification. It is not: `firm=True` is only the PM's re-engage path when verification found no
landed branch (`orchestrator.py:6570`, `_publish_effort(..., firm=True)`). `firm=false` on a
first publish is correct.

---

## F1 — `plan_only` does not restrict writes (VERIFIED, severity: high — symptom of F15)

The drain-round plan turn is announced as *"the worker maps its approach in a **read-only** turn,
and I check it against the goal before any code changes."* It wrote and committed. **Twice, by
two different mechanisms.**

**Instance 1 (round 3)** — shell:

```
$ sed -i '13a import shlex' todo.py
$ sed -i 's/tokens = rest\.split()/tokens = shlex.split(rest)/' todo.py
$ git add todo.py tests/test_todo.py && git commit -m "Fix REPL parser for quoted strings; ..."
```

Event ordering (ids 4944–4950) — the gate approved *after* the work:

```
10:59:45  4945 worker_project_set  fresh=false     ← plan turn on delivered code
11:01-02       (sed edits, suite run, git commit 33eae95)
11:02:34  4946 wake_done
11:02:57  4948 worker_plan_approved  attempt=1     ← approval AFTER the implementation
11:02:58  4950 worker_project_set  fresh=true      ← re-clone discards 33eae95
```

**Instance 2 (round 5)** — interpreter, defeating any command deny-list:

```python
python3 -c "
with open('todo.py', 'r') as f:
    content = f.read()
# 1. Add __version__ after imports
old1 = 'from typing import Any, Dict, List, Optional\n\n\n# ---...
```

followed by a test run and `git commit` → `1b04400`.

### Cause

`plan_only` is a flag posted to the little-coder daemon (`app/worker/harness.py:143-146`), which
enforces it by **excluding edit/write tools**. `sed -i`, `python3 -c`, `git commit` are Bash, not
tools. The restriction never reaches the shell.

### Fix — a deny-list is NOT sufficient

The first draft of this fix proposed blocking mutating shell verbs. **Round 5 refutes that
approach**: it would stop `sed -i` and miss `python3 -c`. `perl -e`, `awk`, heredocs and
`python - <<EOF` are all equivalent. Enforcement must be at the filesystem, not the command
string.

- **Mount the workspace read-only for the duration of a `plan_only` turn** (or run it against a
  throwaway copy). This is the load-bearing change.
- Deny `git commit` / `git push` at the git-proxy during `plan_only` — the proxy already
  hard-denies `git reflog` (`git-proxy: DENIED (blocklist:reflog)`), so the mechanism exists.
- Return denials to the worker so it self-corrects into a plan rather than failing opaquely.
- Test both shapes: a turn issuing `sed -i` **and** a turn issuing
  `python3 -c "open('f','w').write(...)"` must both fail with a byte-identical workspace. A
  command-pattern fix passes the first and fails the second.

### The artifact carries the trace

The `project_documentation` lens diagnosed this incident twice from git history alone, reaching
the correct cause in round 4:

> *"`0f375e0` and `3540574` appear to be two parallel attempts at the same work (possibly from
> two agent sessions or a re-do), merged together in `0a10a29`."*

**Implication:** the commit-history lens is a working detector for orchestration pathologies, not
just a documentation grader. Duplicate subjects, redundant merges and re-do branches are the
visible residue of dispatch defects. Treat its "process signal" observations as org telemetry.

---

## F2 — a reported commit hash is never re-validated (VERIFIED, severity: high)

The plan turn reported: *"**Commit:** `33eae95` ... **55/55 pass.**"* Twenty-four seconds later
the re-clone (event 4950, `fresh=true`) destroyed it. Verified across all four containers —
`git cat-file -t 33eae95` fails everywhere; the commit was never pushed.

The org's record claimed a delivery that existed nowhere. The work was later redone, so nothing
was permanently lost, but **the record was false at the moment it was written and nothing
detected that.**

### Cause

`_finish_effort` requires REPRO/AFTER evidence and P8's closure invariant requires audit proof of
gates — but both trust that a **reported hash still exists**. Nothing re-resolves it.

### Fix

- On any delivery claim carrying a hash, verify it is reachable on the remote delivery branch
  before accepting.
- On failure: do not close, re-dispatch, and log `delivery_hash_unreachable`.
- **Build this together with F13** — same check, same code path.

---

## F3 — `swept = bool(reports)` accepts a partial sweep (VERIFIED, severity: high)

Round 1's `goal_alignment` lens truncated at 70 characters. The round proceeded and recorded
itself as swept. **Audited event sequence — note the absence of any `gap_analysis` for round 1:**

```
lens_report_truncated  {"lens": "goal_alignment", "round": 1, "chars": 70}
lens_sweep             {"round": 1, "lenses": ["clean_code", "project_documentation"]}
drain_round            {"round": 1, "new_tasks": 0, "open": 3, "swept": true}
                       ← no gap_analysis event
lens_sweep             {"round": 2, "lenses": [all three]}
gap_analysis           {"tasks": 1}          ← runs in every round where goal_alignment reported
```

Rounds 2, 3 and 4 each emit `gap_analysis`. Round 1 does not. The round completed without ever
comparing the deliverable to the goal.

### Cause

`orchestrator.py:9870` — `swept = bool(reports)`, true if *any* lens reported. Two lines above it:

```
9865: # ZERO MUST BE EVIDENCED. A sweep that did not run is not a sweep that found nothing...
9870: swept = bool(reports)
```

P13.3 correctly excluded the truncated lens from `reports` (`:9610-9618`) — **that guard worked.**
But `goal_alignment` is the sole input to gap analysis (`:9817`), so losing it silently skips the
only step permitted to see the goal. The `NoCapacityError` path at `:9600` already implements the
correct contract.

### Second instance — round 5

It recurred at the end of the run, confirming this is not a one-off:

```
5134 | drain_round {"round": 5, "new_tasks": 0, "open": 6,
                    "lenses": ["clean_code", "project_documentation"], "swept": true}
```

`goal_alignment` absent again, `swept: true` again, `new_tasks: 0` again. Two of five rounds
(40%) recorded a complete sweep without the only lens that feeds gap analysis.

### Blast radius

False-green only when the open queue is *also* empty. GAP tasks kept the loop alive in both
instances, so it never terminated falsely — the hole was exposed, not fatal. But at a 40% recur
rate, the combination of an empty queue and a truncated `goal_alignment` is a matter of time.

### Fix

- `swept` requires `goal_alignment` present.
- A round missing it retries that lens once, then parks. Never terminates.
- Test: a round where `goal_alignment` truncates cannot reach `scope_completed`.

---

## F12 — a scope-refused task is closed as done, not refiled (VERIFIED, severity: high)

Round 3 dispatched 5 tasks to a worker scoped to `data_layer` (`sn-d50bdd5c407e`). Two belonged
to other layers. The worker identified this correctly and refused them explicitly:

> **Not touched (outside `data_layer` scope):**
> - **Task 2** (remove `except Exception` in `cmd_repl`) — REPL/command layer, not mine.
> - **Task 4** (`--version` CLI flag) — CLI/argparse layer, not mine.

Both were closed as **done** at the same timestamp, with no refiling:

```
st-29afe75 | r0 | done | closed=2026-07-20T11:30:16 | Implement a --version CLI flag ...
st-19ee694 | r3 | done | closed=2026-07-20T11:30:16 | Remove the redundant top-level except Exception ...

task_refiled / escalation_routed events:  NONE
```

**One of the two is still not done, and still closed.** Audited at the current branch head
`fb3657a`:

```
fb3657a:450    except Exception as e:        ← inside cmd_repl (def at 411, next def at 540)
```

The `--version` task recurred in round 4 as a **new** task with different wording, defeating
`sha1(owner|body)` dedup, and was eventually implemented:

```
st-29afe75  r0  "Implement a --version CLI flag to surface the existing __version__ string"   (closed, not done)
st-1783517  r0  "Expose the __version__ string via a --version CLI flag"                      (closed, done)
```

### Cause — corrected during implementation

The draft said "turn completion is read as task completion". Reading the code while fixing it
showed the truth is blunter: **`_drain_iterate` closes the whole queue at HAND-OVER**
(`orchestrator.py`, immediately after the dispatch), so the tasks were marked `done` *before the
implementer executed a single step*. The worker's refusal was never consulted because nothing was
waiting for it. Any completion claim at that point is a guess about the future.

The second half stands: `_seam_owner` filed CLI and REPL work to the `data_layer` scope, which is
what gave the worker something to refuse.

The original code carried an explicit justification for closing early — "a task that isn't really
finished is re-derived by the next independent sweep, so closing cannot hide unfinished work".
gym-015 falsified it twice over:

1. **Re-derivation is not idempotent.** The sweep re-words the finding, so `sha1(owner|body)` sees
   a NEW task instead of reopening the old one: `st-29afe75` ("Implement a `--version` CLI flag
   to surface the existing `__version__` string") came back as `st-1783517` ("Expose the
   `__version__` string via a `--version` CLI flag"). The queue churns rather than corrects.
2. **It does not always come back.** `st-19ee694` (remove the broad `except Exception` in
   `cmd_repl`) was closed `done`, was never re-derived as actionable, and is still undone on the
   delivered branch — verified at `fb3657a:450`.

### Why this ranks high

It discards correctly-identified work while reporting success, and it is triggered **by the
worker doing the right thing**. A worker that silently overstepped its scope would have left the
codebase correct. The org currently rewards boundary violations and punishes compliance.

It also defeats the point of bounded scopes: without a return path, decomposition becomes a way
to lose tasks.

### Fix — SHIPPED (partial), with the remainder named

**Shipped.** A third closed state, `dispatched`, distinct from `done`:

- `close_task` accepts `done` / `dropped` / `dispatched`; `_drain_iterate` now closes its queue as
  `dispatched`, because "handed over" is the only thing the org actually knows at that moment.
- `add_task` reopens on `done` **or** `dispatched` when a later sweep re-derives the gap. This
  matters more than it looks: the first implementation reopened only `done`, which silently broke
  the reopen invariant and was caught by an existing test
  (`test_a_reopened_task_keeps_the_loop_running`). `dropped` still stays shut — that is a
  deliberate decision an automatic re-derivation must not overturn.
- All three remain closed for dispatch purposes, so nothing is re-dispatched forever. Only the
  CLAIM changed — and the claim is what the audit reads.

Covered by `tests/test_p17_integrity.py`: `test_dispatch_closes_tasks_as_dispatched_not_done`,
`test_a_dispatched_task_still_reopens_when_re_derived`,
`test_a_dropped_task_is_not_resurrected_by_re_derivation`.

**Not yet shipped — the refile half.** The dispatch is fire-and-forget (`_spawn(self.delegate…)`),
so there is no return point at which the worker's WON'T DO reply is in scope. Doing this properly
needs a reconciliation step after the implementer turn:

- Parse the WON'T DO / "not mine" channel the charter already asks workers to emit.
- Route refused tasks through `_refile_task` / `_seam_owner`; escalate to the parent when no scope
  owns them.
- Log `task_refused` / `task_refiled`.
- Test: a turn declining 2 of 5 tasks closes 3 as `dispatched`, refiles 2, and closes zero
  unperformed tasks.

Stated plainly: **the `dispatched` state stops the org LYING about the outcome; it does not yet
route the refused work to an owner.** A refused task now sits `dispatched` and is reopened by the
next sweep that still sees it, which is strictly better than being recorded `done` and forgotten,
but it is not the full fix.

---

## F13 — a stale workspace produces a divergent commit (VERIFIED with correction, severity: high)

**Severity corrected after audit — see "Corrections" below. This is a near-miss, not data loss.**

Round 5 dispatched to worker-2, whose workspace was four commits behind the delivery branch. The
resulting commit `1b04400` has `0f375e0` as its parent, not the head `1ed9da6`:

```
$ git rev-list --parents -n 1 1b04400
1b04400... 0f375e0...

$ git merge-base --is-ancestor 1ed9da6 1b04400     →  exit=1  (not an ancestor)

$ git rev-list --count 0f375e0..1ed9da6            →  4
```

Tree delta between the real head and the divergent commit:

```
                    1ed9da6      1b04400
shlex                  3            0
TodoItem               5            0
pyproject.toml       yes           NO
docs/architecture.md yes           NO
tests                 55           51
```

**What actually happened next:** the branch head is `fb3657a`, a merge commit with **both**
`1b04400` and `1ed9da6` as parents, whose tree carries everything from both sides. The work was
reconciled, not lost.

### The provenance guard cannot detect staleness — VERIFIED

```
$ git rev-list -n 500 0f375e0 | grep -q ^8158508a814d09a1d9e810bca1abaa0101604270
exit=0
```

The base SHA **is** an ancestor of the four-commits-stale HEAD. P8's check asks *"is the declared
base in my history?"* — which a stale workspace answers correctly. It never asks *"is my HEAD
current with the delivery branch?"*

**The gap is precise: base ancestry is verified, head currency is not.**

### Why it still matters at high severity

The recovery was a merge that happened to succeed on non-overlapping changes. Nothing in the
system detected or intended it. Had the two sides touched the same lines, the resolution would
have been a worker's `checkout --ours`-style guess — which is exactly what happened earlier in
this same run. The org should not be relying on merge luck to preserve delivered work.

### Fix

- Before dispatching work that builds on a delivery branch, require the worker's HEAD to equal
  the remote branch head; fast-forward or re-clone otherwise, and fail the dispatch if it cannot
  be made current.
- Extend provenance from base-ancestry to **head-currency**: assert
  `git merge-base --is-ancestor <prev_head> <new_head>` before accepting a delivery; log
  `delivery_orphans_head` on failure.
- Pin drain rounds to one worker per effort where possible so the workspace advances
  monotonically.
- **This is the same check as F2** — resolve the delivery against the remote and verify ancestry.
  Build once.

### Cheapest independent detector: test-count monotonicity

```
round 1 delivery : 51 tests
round 1 drain    : 55 tests   (+4)
round 5 delivery : 51 tests   ← regression, accepted as "51/51 pass ✅"
```

The org keeps no memory of the previous count. One stored integer compared round-over-round
catches this with no git reasoning, and catches any silent revert or narrowed suite as a side
effect. Strictly weaker than head-currency (a revert preserving the count defeats it) — build
both.

---

## F10 — gap analysis converts scope wording into tasks (VERIFIED, severity: high)

Round 2's gap analysis ran against the `d=2` data-layer scope and produced:

```
- Implement database path configuration          ← ALREADY IMPLEMENTED
- Add a `pyproject.toml` or equivalent            ← not in scope, not in goal
- Add a project version string                    ← not in scope, not in goal
- Add static type-checking configuration          ← not in scope, not in goal
```

`db_path()` exists at `todo.py:36`, reads `TODO_DB`, verified working. The worker's own
assessment: **"✅ Already satisfied."** The phrase "database path configuration" appears verbatim
in the scope text — the task is the scope's own wording reflected back as work.

Tasks 2–4 are packaging concerns in neither the data-layer scope nor the effort goal (*"a
POLISHED, FINAL-QUALITY todo application that a real person would use"*).

### Cause

P11.4 fixed this at the effort-goal level (`orchestrator.py:9654-9668`). But a **scope
description** is prose characterising an area, not a specification of deliverables. "handles ...
database path configuration ..." describes what the layer does; extraction reads it as a
requirement list.

### Fix

- Distinguish scope-as-boundary from goal-as-specification: the scope constrains *where* a gap
  may be found; only the root goal specifies *what* must exist.
- Re-assert the P11.4 already-built rule at every tier, not just the root.
- Reject tasks whose subject lies outside the scope's boundary.
- Test: gap analysis over a scope naming an implemented capability, with a report evidencing it,
  yields zero tasks.

---

## F11 — observation agents assert unchecked absences (VERIFIED ×3, severity: high)

**Three instances, two agent roles, three unrelated subjects.** All three refuted by the audit:

| # | Agent | Claim | Reality |
|---|---|---|---|
| 1 | round-3 lens | *"No type annotations on function signatures"* | 17 of 17 defs annotated at every commit |
| 2 | round-4 assessment | *"There is no `__version__` variable defined anywhere"* | `1ed9da6:20  __version__ = "0.1.0"` |
| 3 | **reviewer** | *"`os.makedirs` lacks explicit `exist_ok=True`"* | `1b04400:82  os.makedirs(dir_name, exist_ok=True)` |

Instance 1 also self-contradicted within the same paragraph (*"Functions like
`load_items() -> List[Dict[str, Any]]` have return annotations"*) and contradicted round 2's
lens, which had it right.

**This is not lens-specific.** Instance 3 is the reviewer — the component whose accurate catches
(unrunnable mypy, unverified delivery claims, TypedDict being static-only) sit alongside this
fabrication with nothing distinguishing them. The fix must sit in the **consumer** — anything
turning an observation into work or a verdict — not in any agent's prompt.

### Confirmed propagation to dispatched work

Instance 2 became a real task (`st-0082c85`) and was dispatched. Downstream cost: the worker
spent its plan turn hex-dumping lines 17–20 (`xxd`, `cat -A`, `repr(lines[i])`) because the
task's premise contradicted the file in front of it. **A false absence wastes the good judgement
of the layer below it.**

### Compounding cause: a check that reported success without running

```
$ python3 -m mypy todo.py 2>&1 || true      ← recorded ✅ (twice, two workers)
$ python3 -m mypy --version                 → No module named mypy
```

`|| true` converts a missing tool into a green tick, and the lens then wrote conclusions about
type checking having consulted a tool that never executed.

### Fix

- Before creating a task from an asserted absence, verify it against the tree (grep/AST for the
  named symbol, file, flag or annotation). Drop and log `false_absence_rejected` if present.
- Treat cross-round contradiction as a trigger for verification.
- Treat `cmd || true` and other failure-masking constructs as MISSING checks, not passing ones —
  the contract P13.3 already applies to truncated lens reports.
- Test: a report asserting "no type annotations" against an annotated file yields zero tasks.

---

## F7 — the plan gate does not measure alignment (VERIFIED, severity: medium)

**It fails in both directions, within one effort.**

**Rejected a correct plan.** The gate rejected a plan for *"completely fail[ing] to address the
core goal"* when every named feature was implemented, committed and passing (`0f375e0`, 51 tests,
all 10 commands present). The worker restated the same situation as *"already executed and
committed"* and the gate approved it. **Same commit, same tests — only the prose differed.**

**Approved a drifting plan.** Round 2's plan proposed `pyproject.toml`, `__version__` and
`[tool.mypy]` against a goal about a polished todo CLI. Verdict: *"✅ Plan reviewed — aligned with
the goal."*

It is reacting to how much the plan's prose resembles the goal's prose.

### The worker layer gets this right

Handed the no-op task, the worker replied *"✅ Already satisfied — no changes needed"* and changed
nothing. The P11.4 discipline is live one layer down and missing here and in F10.

### Fix

- Before rejecting for omission, check whether the omitted items exist in the delivered branch.
- Before approving, check the converse — every proposed item must trace to scope or goal.
- Test: a plan proposing work absent from goal and scope is rejected; a plan omitting
  already-delivered work is approved.

---

## F8 — lenses are not diff-aware; the loop certified its own regression (VERIFIED, severity: medium)

Round 1's drain fixed the quoting bug with a bare `shlex.split()`. Verified behaviour at
`1ed9da6`:

```
$ python3 -c "print(todo._parse_repl_line('add \"hello world\"'))"
['add', 'hello world']                      ← the fix works

$ printf 'add "unterminated\nlist\nquit\n' | python3 todo.py repl
Todo REPL — type 'help' for commands, 'quit' to exit.
> error: No closing quotation
(exit 1 — `list` and `quit` never processed)
```

The `ValueError` propagates into `cmd_repl`'s broad `except Exception`, which prints and returns
1 — **one mistyped quote ends the session.** Previously `rest.split()` never raised.

Round 2's `goal_alignment` lens then swept that code, probed `_parse_repl_line` eight times
including quotes **twice** (both balanced), and reported under *What Works Well*:

> **REPL parser** handles quoted strings, flags in any order, and all commands correctly.

**The loop introduced a regression, shipped it through every gate at 55/55 green, then certified
the broken surface as correct** — three rounds running.

### Cause

The lens is not lazy: the same report carried 12 legitimate findings, and in round 1 this lens
found the original quoting bug that nine hand-written probes missed. It has **no signal about
what changed**. `shlex.split()` was the newest, least-exercised line in the file and received no
more scrutiny than code that had survived several rounds.

A pre-existing broad `except` also masked the severity of the new bug — and round 3's lens
flagged that handler (*"a single bad command can terminate the entire REPL session"*) without
connecting it to the new exception source it now swallows. Two halves of one defect, in one
report, unjoined.

### Fix

- Pass the diff since the last swept commit to each lens as **additional** context, with the
  standing note that recently-changed lines are the least-exercised surface.
- Prioritisation, not narrowing — full-scope coverage is unaffected, so F3's contract holds.
- A diff is an objective artifact; the debias is intact.
- Test: a round introducing an unguarded exception in changed code produces a DEFECT-grade task
  on the following sweep.

---

## F4 — lens reports are all-or-nothing (VERIFIED, severity: medium)

`goal_alignment` ran ~9 minutes of strong adversarial probing (hostile inputs, a directory where
the data file belongs, `--due 2026-02-30` — a real calendar check the implementation passed) and
lost **every** finding when the turn ended, emitting 70 characters of preamble.

The instinct to "add a stopping rule" is **wrong** — that same probing found the quoted-string
bug. The problem is that findings are held until the end.

### It is context exhaustion, not a timeout — verified

This distinction determines which fixes can possibly work. Round 1's three lens turns:

```
lens541485 (goal_alignment)  10:50:16 → 10:55:43   5m27s   TRUNCATED at 70 chars
lens541486 (clean_code)      10:55:43 → 10:56:46   1m03s   full report
lens541487 (project_docs)    10:56:46 → 10:57:31   0m45s   full report
```

The harness poll timeout is `worker_poll_timeout_s = 5400.0` — **90 minutes**. The lens died at
five and a half. It was never close to a harness bound; it ran out of its own turn/context budget
after ~17 tool calls of heavy probing, leaving nothing for the report.

Consequences:

- **Timeout tuning and watchdogs cannot fix this.** Raising a limit that was never reached does
  nothing.
- **Incremental emission is the only remedy** — findings must leave the turn as they are
  established, because the turn's capacity to *report* is consumed by the same budget as its
  capacity to *probe*.
- **The flail guard being unarmed on lens turns is correct** (`_lens_sweep` calls `router.wake()`
  without `flail_guard`, which defaults False at `router.py:310`). A lens is legitimately a
  read-without-edit loop; arming the guard would kill the exact behaviour that found two real
  bugs. Do not change this.

This was reached by way of a wrong hypothesis — see the hypothesis audit below.

### Fix

- Instruct lenses to emit each finding as a self-contained line as it is established.
- Treat a report as accumulated findings, not the final message.
- Keep P13.3: zero findings emitted is still MISSING, not clean.

---

## F5 — the severity floor grades prose, not evidence (VERIFIED, severity: medium)

Round 1's sweep produced ~9 red marks. **Every one was correct behaviour**, reproduced by hand:

| probe | rc | output |
|---|---|---|
| double `delete 1` | 1 | `no such todo: #1` |
| `--priority invalid` | 2 | argparse: `choose from low, medium, high` |
| `--due 2026-02-30` | 1 | `invalid date ...: use YYYY-MM-DD` |
| `done abc` | 2 | `invalid int value: 'abc'` |
| `_parse_repl_line('add --priority high')` | — | `None`, no raise |

The floor graded them correctly (`open: 3`, all PREFERENCE/GAP) — but from the lens's **prose**,
not the exit codes, which are gone by then. `rc=2` on bad input is correct; `rc=0` would be
broken. The floor cannot distinguish them.

### Fix

- Carry each probe's command, exit code and output alongside the description.
- Grade against evidence: a non-zero exit with a clear diagnostic is not a defect.
- Test: a corpus of correct-rejection probes yields zero DEFECT-grade tasks.

---

## F9 — decomposition can restate its parent (VERIFIED, severity: low)

```
d=1  Data persistence layer :: handles loading, saving, atomic file writes, and robust parsing
d=2  data_layer             :: handles loading, saving, atomic file writes, database path
                               configuration, and malformed data resilience
```

`scope_decomposed_live {"parent": "sn-defa6ba4eeab", "children": 4, "evidence": 3}` — the tier
walk descended and narrowed nothing.

### Fix

- Reject a child whose scope text is not materially narrower than its parent.
- On rejection treat the parent as atomic.
- Test: a decomposition returning a near-copy of the parent creates no tier.

---

## F6 — P16 discards on the wrong worker (VERIFIED, severity: low)

`_discard_uncommitted` calls `router.exec_check(...)`, which **acquires any free worker** rather
than targeting the hung one:

```
hung worker           : worker-2   (dirty tree)
discard ran on        : worker-1   (already clean)
stall_tree_discarded  : never fired
audit 4878            : actor=worker-1, session "effort-gym-015-todo-product~clean"
```

The gym-015 recovery succeeded **in spite of** P16. Note this is *not* what destroyed `33eae95`
(that was the ordinary dispatch re-clone — F1/F2). Distinct mechanisms.

### Fix

- Pin the discard to the hung worker's base URL instead of the acquire path.
- Test: with two workers where one is dirty, the discard targets the dirty one and
  `stall_tree_discarded` fires.

---

## F14 — the NL inlet acknowledges instructions it did not understand (VERIFIED, severity: high)

Stopping this run surfaced a control-plane defect. An explicit operator abort was accepted and
silently discarded.

**Attempt 1** — via `POST /nl`, the documented operator inlet:

```
message: "Stop and abort effort-gym-015-todo-product. The gym diagnostic run is
          complete and I do not want any further rounds, dispatches or pushes on it."
response: {"ok": true, "channel_id": "uh6nk7x9w7n45efoszmbmo6ste"}
```

Result: **nothing.** No intent event, no abort event, no acknowledgement of failure. Queried the
event log for any `%intent%`, `%abort%`, `%nl%` or `%operator%` event after that timestamp —
zero rows. Seven minutes later the effort ran another full round:

```
5134 | 12:01:01 | drain_round    {"round": 5, "new_tasks": 0, "open": 6, "swept": true}
5135 | 12:01:01 | drain_dispatch {"round": 5, "tasks": 6}
```

**Attempt 2** — same endpoint, command-shaped:

```
message: "archive effort-gym-015-todo-product"
```

Result: `lifecycle=aborted`, dispatch suppressed.

### Cause

`nl_intake` classifies to a structured `OperatorIntent`. When classification fails to match, the
message is dropped — but the endpoint has already returned `{"ok": true}`, which reports
*receipt*, not *comprehension*. There is no unmatched-intent path: no event, no reply, no error.

### Why this is high severity

The org's stated design is NL-first (all operator inlets via natural language → structured
intent → governed handlers, with slash commands as fallback). **An NL-first control plane that
silently ignores unparsed natural language is failing at its primary interface** — and it fails
in the worst direction, on a stop instruction. An operator issuing that first message would
reasonably believe the effort had halted, and would not discover otherwise unless they went
looking at the event log.

Note the shape: the more explicit, more human, more unambiguous phrasing failed; the terse
command form succeeded. That is the opposite of the intended affordance.

### Fix

- On classification failure, emit `operator_intent_unmatched` with the raw message, and reply in
  the operator's channel: "I did not understand that — did you mean …?" Never drop silently.
- `{"ok": true}` from `/nl` must mean *understood and queued*, not *received*. Return the matched
  intent (or a 422) so automation can tell the difference.
- Treat stop/abort/halt as a high-recall intent class — prefer a clarifying question over a
  silent no-op when a message plausibly asks the org to stop.
- Test: a verbose, correctly-formed abort instruction aborts the effort; an unparseable message
  produces an unmatched event and an operator-visible reply.

---

## D1 — design decision: propagation count vs work queue

**Not a defect — P15.2 as specified. Operator's call.**

GAP tasks are stamped `round_no = 0` and excluded from `count_new_tasks`. Observed: 9 of 14 task
rows carry `round_no = 0`; round 3 reported *"1 new task(s), 5 open"* while dispatching five.
Termination keys on a counter that cannot see GAP work, so **a "zero new tasks" termination can
fire with a GAP queue in flight.**

Options: (1) leave as-is — GAPs are advisory polish, the PR is the stopping point; (2) require the
GAP queue empty or explicitly deferred before `scope_completed`; (3) count GAPs at a discount.

No change made pending a decision.

---

## H1 — harness: deterministic checks are empty

The reviewer caught this unprompted, in every round:

> *"Deterministic checks section is completely empty (`{}`) ... Test pass rates alone do not
> validate correctness, edge-case handling, or absence of safety trade-offs."*

The scenario's only gate is a unittest suite **written by the same worker that wrote the code**.
Concrete cost: the `shlex.split()` regression shipped at 55/55 green because the implementer's
own tests cover only the balanced cases it had in mind.

**A checker that cannot run is not a check.** Round 2 dispatched "add static type-checking
configuration"; the worker failed twice to install mypy (no package egress), wrote a
`[tool.mypy]` section, and reported ✅ done. The configuration was never executed.

### Fix

- Add lint + typecheck to the scenario's `check_cmd`.
- **Bake the tools into the worker image** — do not rely on workers installing them.
- Treat a verification step that could not run as MISSING, not satisfied.

---

## What is confirmed working — do not touch

- **P13.3 substance floor** — correctly classified the 70-char lens output as MISSING. F3 is the
  *consumer* of that signal being wrong, not the floor.
- **P15.2 severity grading** — three PREFERENCE findings queued as `open: 3`, none miscounted as
  DEFECT. Style/docs lenses manufactured no chores.
- **Production-branch guard** — a recovery worker committed onto `main` locally; the push was
  hard-denied and `origin/main` verified clean at `8158508`.
- **NO-CHANGES backstop** — the org ran the build itself rather than trusting a worker's
  "nothing needed changing".
- **Tier-walk boundary at the worker layer** — the `data_layer` worker correctly refused two
  out-of-scope tasks and said so. F12 is the missing return path, not a failure of bounded scopes.
- **The lenses as bug-finders** — two genuine defects found across the run: the quoted-string
  parser, and an uncaught `FileNotFoundError` when `TODO_DB`'s parent directory is missing
  (correct diagnosis and fix supplied). The observation layer works; the accounting layer around
  it is what needs work.

---

## Build status (2026-07-20)

| Finding | State | Where |
|---|---|---|
| **F1** — `plan_only` doesn't restrict writes | 🟡 **MITIGATED** | `_revert_plan_turn_writes` detects + reverts after every plan turn; instruction names the shell routes. **Prevention (read-only workspace) still needs a worker-image change** |
| **F2 / F13** — hash + head-currency never validated | ✅ **SHIPPED** | `capabilities.sha_is_ancestor` + `_delivery_orphans_previous_head`; an orphaning delivery escalates and does NOT close |
| **F3** — partial sweep counted as swept | ✅ **SHIPPED** | `swept` requires `_LENS_GOAL_ALIGNMENT_KEY`; the note names the missing lens |
| **F4** — lens reports all-or-nothing | ✅ **SHIPPED** | lens instruction: write each finding as it is established, never save for a summary |
| **F6** — discard cleans the wrong worker | ✅ **SHIPPED** | targeted `harness.run_check(worker_url, …)`; pool fallback only when no url is known |
| **F7** — plan gate measures prose | ✅ **SHIPPED** | judges BOTH directions: omission excused by an `ALREADY DONE` observation; drift rejected as firmly as omission |
| **F8** — lenses not diff-aware | ✅ **SHIPPED** | rounds ≥2 get the diff since the last round, framed as least-exercised surface (prioritisation, not narrowing) |
| **F9** — child scope restates parent | ✅ **SHIPPED** | novel-token test; a decomposition that narrows nothing leaves the node atomic |
| **F10** — scope wording becomes tasks | ✅ **SHIPPED** | gap analysis reframed: the scope is a BOUNDARY, only the report is evidence; hygiene work explicitly excluded |
| **F11** — asserted absences become work | ✅ **SHIPPED** | `_drop_false_absences` — batch grep per round, conservative |
| **F12** — handed over recorded as done | 🟡 **PARTIAL** | `dispatched` state shipped; **refile of scope-refused tasks NOT built** |
| **F14** — stop instruction silently dropped | ✅ **SHIPPED** | `_STOP_INTENT_RE` → `operator_intent_unmatched` + a reply naming the exact command |
| **F15** — pipeline order inverted | 🟡 **PARTIAL** | F16 fixed the session half; the revert (F1) gives the "nothing of value on disk at the gate" property. **Moving the refresh before the plan turn not built** |
| **F16** — plan turn recalls instead of observes | ✅ **SHIPPED** | `~plan` session + plan returned as an artifact + instruction requiring cited observation |
| D1, H1 | ⬜ open | D1 is an operator decision; H1 is the gym repo |

Tests: `tests/test_p17_integrity.py` (19, all green) plus updated assertions in
`tests/test_lenses.py`, `test_worker_plan_gate.py`, `test_flail_replan.py`.

### What is deliberately NOT shipped, and why

**F1/F15 prevention — a read-only workspace for plan turns.** The enforcement chain runs
agent-bridge → little-coder daemon → `/opt/git-proxy/git_proxy.py` (baked into the worker image).
Closing it properly means a daemon change plus an image rebuild, neither of which this repo's test
suite can validate, and a mistake there breaks git for every worker on every effort. Shipping that
untested immediately before a gym run trades a bounded, observable problem for an unbounded one.

The interim guard targets the property rather than the mechanism: after every plan turn the
workspace is probed and reverted, so the gate cannot judge a fait accompli and the post-gate
re-clone cannot destroy a commit the org has already reported (which is exactly how `33eae95` was
lost). A plan turn can still *write*; it can no longer make that writing count.

**F12 refile.** The drain dispatch is fire-and-forget (`_spawn(self.delegate…)`), so there is no
point at which the worker's WON'T DO reply is in scope. `dispatched` stops the org lying about the
outcome; routing refused work to its owner needs a reconciliation step that does not exist yet.

Two brittle assertions in `test_lenses.py` were rewritten to check MEANING rather than exact
wording — they pinned the phrase "could not run" and failed on a behaviour-identical message
change. That is the same prose-over-evidence anti-pattern this plan documents, in our own tests.

## Implementation order

1. **F15 + F16 (+ F1, F2)** — fix the plan/execute cycle as one unit, because sequence and context
   are only correct together:

   ```
   refresh workspace
     → plan turn:    FRESH session, read-only workspace, must cite observed evidence
     → gate
     → execute turn: plan's session, SAME workspace, write access
   ```

   This dissolves F1 and F2 as a class (nothing of value can exist on disk when the gate runs, and
   no refresh discards it afterwards) and removes the zero-change-plan failure mode. Keep F2's
   hash re-validation as a backstop. **Do not ship F15 without F16** — a correctly ordered
   pipeline whose plan step recalls instead of observes is still worthless.
2. **F13** — head-currency + ancestry check against the remote (shares F2's code path). Add
   test-count monotonicity as an independent, near-free detector.
3. **F14** — never acknowledge an operator instruction that did not classify. `{"ok": true}` must
   mean understood, not received.
4. **F12** — refused ≠ done; per-task evidence before closure; refile via `_seam_owner`.
5. **F10 + F11** — gap analysis must check the tree before converting text into work. Shared fix
   surface: both are "trusting an assertion over the repository".
6. **F3** — `swept` requires `goal_alignment`. Smallest diff; mirrors `:9600`.
7. **F7** — plan gate rejects drift as firmly as omission. **Sequence after F16**: with the plan
   turn grounded in observation, the gate is finally adjudicating a claim about the repository
   rather than choosing between two recollections. Fixing F7 first would only improve the gate's
   reading of ungrounded memory.
8. **F8** — diff-aware lenses.
9. **F5, F4** — evidence plumbing, then incremental emission (F5 supplies what F4 emits).
10. **F9, F6** — mechanical.
11. **H1** — gym repo: lint + typecheck in `check_cmd`, tools baked into the image.

### Ordering note

F15 is first not because it is the most damaging in isolation, but because the pipeline order is
load-bearing for several other fixes. F12's "per-task evidence before closure" and F13's
head-currency assertion both need a well-defined point in the cycle at which the workspace state
is known good. Fixing the sequence first gives those checks somewhere honest to stand.

Gate: full suite green before deploy, then a fresh gym round on a wiped arena.

---

## Convergence status

```
gym-013   r1: 1 new / 1 open    r2: 0 new / 0 open   → scope_completed   (trustworthy)
gym-015   r1: 0 new / 3 open    (gap_analysis never ran — F3)
          r2: 1 new / 4 open    (invented packaging work — F10)
          r3: 1 new / 5 open    (false absence dispatched — F11)
          r4: 2 new / 3 open    (F11 again + F12 recurrence re-entering as new)
```

gym-015 did not converge. Round 1's zero was hollow; subsequent growth was substantially
invented. The round cap (40) would have stopped it, not a countable zero. This is gym-009's
pathology re-entering through the tier walk rather than the root goal — which is why F10 and F11
are grouped and why F3 must be fixed alongside them.

---

## The organizing principle: gates that execute work, gates that read do not

Sorting the findings by *how the component establishes truth* produces a clean split, and it is
predictive rather than descriptive.

**Components that execute something and consume the result — all working:**

| Component | What it executes | Outcome |
|---|---|---|
| Lenses (as bug-finders) | probes against the running program | found 2 genuine bugs — the quoted-string parser, and an uncaught `FileNotFoundError` |
| NO-CHANGES backstop | runs the build itself rather than trusting the claim | correctly accepted a real no-op |
| Production-branch guard | intercepts at the git-proxy | denied a push to `main`; `origin/main` verified clean |
| P13.3 substance floor | measures a structural property of the output | correctly classified a 70-char report as MISSING |

**Components that read a natural-language assertion — all failing:**

| Component | What it reads | Failure |
|---|---|---|
| Plan gate (F7) | plan prose vs goal prose | rejected a correct plan; approved a drifting one; flipped on rewording alone |
| Gap analysis (F10) | scope description as if a spec | emitted packaging tasks from a data-layer boundary |
| Gap analysis (F11) | a lens's asserted absence | dispatched work to add things that already existed, ×3 |
| Severity floor (F5) | a lens's description of a probe | cannot distinguish `rc=2` (correct rejection) from `rc=0` (broken) |
| `swept` (F3) | truthiness of a dict | counted a 2-of-3 sweep as complete |

**The partial counterexample is the most instructive.** F13's base-SHA check *does* execute git —
and still fails, because it asks an under-specified question ("is the base in my history?")
rather than the load-bearing one ("is my HEAD current?"). Executing is necessary but not
sufficient; the check must also be *specific enough to be falsifiable by the failure you care
about*.

### Why this matters for the fixes

Several fixes in this plan are, at bottom, the same move: **convert a read into an execution.**

- F5: carry the probe's exit code, not the lens's description of it.
- F11: grep the tree for the asserted-absent symbol instead of believing the assertion.
- F7: read the delivered branch instead of comparing prose.
- F12: require per-task evidence instead of inferring completion from turn completion.
- F13: resolve the hash against the remote instead of trusting the report.

### This is the design's own spine, not a new rule

`ORCHESTRATION-DESIGN.md` §11 already states it:

> *"**every boundary in the system — module interface, escalation, human finding — is an
> executable contract.** That single principle is the spine of the design."*

and §12 records the operator's fork as already leaning to *"executable from day one — prose scopes
judged by an LLM reintroduce the ambiguous handoff."*

**Every failing component in the table above is a boundary that was implemented as a prose scope
judged by an LLM.** The plan gate compares prose; gap analysis reads a scope description as a
spec; the severity floor reads a lens's narration; `swept` reads a dict's truthiness. P17 is
therefore not a new direction — it is **closing the gap between the design's stated spine and the
implementation**, at the five boundaries where prose was used instead of an executable contract.

Restated for implementation:

> **A gate may not decide on the basis of another agent's prose when the claim is mechanically
> checkable.** If a claim can be settled by running a command, the gate runs the command.

§11's companion mechanism — *"the escalated concern is a **ticket that cannot close until its own
failing test passes**"* — is the same idea applied to closure, and is precisely what F12 needs
(a task closed without evidence) and what F16 needs (a plan asserting completeness without
observation).

This also predicts where the next defect will be: any new gate that adjudicates by reading model
output will fail the same way. Worth applying as a review criterion on the P17 implementation
itself.

---

## Hypothesis audit — wrong calls made during the run, and what they yielded

Recorded because several fixes in this plan exist *only* because a hypothesis was wrong and the
refutation was informative. This is also an honest account of observer reliability: fifteen
findings required roughly a dozen corrections.

| # | Hypothesis | Verdict | What the refutation yielded |
|---|---|---|---|
| 1 | "P16 is deployed, therefore working" | **Wrong** — presence ≠ correctness | F6: `exec_check` acquires *any* free worker; the discard ran on the clean one |
| 2 | "The flail guard will kill thrashing lens turns" | **Wrong** — `_lens_sweep` never arms it | Led to measuring the actual bound: **context exhaustion at 5m27s vs a 90-min timeout** → F4's fix must be incremental emission, and the guard must stay unarmed |
| 3 | "`33eae95` is permanently lost" | **Wrong** — the work was redone | F2 refocused from "data loss" to the real defect: the org *recorded a delivery that did not exist* |
| 4 | "The push wedged; history diverged" | **Wrong** — push succeeded, history linear | Revealed the production-branch guard firing correctly on the worker's accidental `main` commit |
| 5 | "Round 1 will terminate and declare convergence" | **Wrong** — it dispatched 3 GAP tasks | F3's blast radius is conditional on an empty queue; and surfaced **D1** (propagation count decoupled from the work queue) |
| 6 | "The `shlex` regression is an uncaught traceback" | **Wrong** — caught by a broad `except` | Sharpened F8: a *pre-existing* broad handler masked the severity of a *new* bug, and the lens flagged the handler without connecting it to what it now swallows |
| 7 | "A command deny-list fixes F1" | **Wrong** — round 5 used `python3 -c` | F1's fix moved to **filesystem-level** read-only, and ultimately to F15 (the sequence is the real defect) |
| 8 | "F13 silently reverted 4 commits" | **Wrong** — merge `fb3657a` reconciled both | F13 restated as a near-miss surviving on *merge luck*; the guard gap (base-ancestry vs head-currency) is the durable finding |
| 9 | "`__version__` was defined twice" | **Wrong** — my `grep -c` counted a usage line | Claim withdrawn entirely. Method error, not an org defect |
| 10 | "The `--version` task was never done" | **Partly wrong** — it recurred and *was* done | Exposed the paraphrase weakness in `sha1(owner|body)` dedup, and isolated `st-19ee694` as the genuinely permanent false-close |
| 11 | "The reviewer is the best component" | **Wrong** — it fabricated `exist_ok` too | F11 generalised from lens-specific to **all observation agents**, moving the fix to the consumer |
| 12 | "The db-path task recurs forever" | **Wrong** — one row, closed | Led to reading `scope_tasks` properly, which surfaced the `round_no = 0` GAP stamping behind **D1** |
| 13 | "The scope-node mismatch is a defect" | **Wrong** — same pattern in gym-013 | Correctly dropped. `drain_round.scope` records the *next* round's scope — see telemetry note below |
| 14 | "gym-013's convergence may be void" | **Wrong** — all 3 lenses reported both rounds | Confirmed gym-013 as a trustworthy baseline, which anchors the whole convergence comparison |
| 15 | "My `/nl` abort worked (`ok:true`)" | **Wrong** — silently dropped | **F14** — the NL inlet acknowledges instructions it never understood |

### The error pattern, and its org-relevant twin

Twelve of these came from the same mistake: **treating a partial view as settled state** — a
mid-operation container snapshot, a single commit, a `grep -c` total, an HTTP 200. Every one was
caught by driving the code or reading the event log end to end.

That is precisely the failure mode F11 documents in the org's own agents (asserting an absence
from a partial read) and F13 documents in its provenance check (a partial question answered
correctly). The observer and the observed failed the same way, which is some evidence the
principle above is real rather than a post-hoc tidy-up.

### Telemetry note (low severity, not filed)

`drain_round.scope` records the scope selected for the **next** round, not the scope that was
swept. Verified consistent across gym-013 and gym-015 — round N's `drain_round.scope` equals
round N+1's `lens_sweep.scope`. Not a defect, but it cost an investigation here and will cost
another. Rename to `next_scope`, or emit both `swept_scope` and `next_scope`.

---

## Corrections from audit

The working draft of this document contained three claims the audit refuted. They are recorded
because the failure mode — an observer overstating from a partial view — is the same one F11
documents in the org's own agents.

1. **"F13 orphans four commits of work / silent data loss" — REFUTED.** The branch head is
   `fb3657a`, a merge with both `1b04400` and `1ed9da6` as parents, carrying both trees. The
   divergent commit and the guard gap are real; the data loss is not. Severity restated as a
   near-miss that succeeded on merge luck.

2. **"The `--version` task was never performed" — PARTIALLY REFUTED.** True at `1ed9da6` and true
   of task `st-29afe75` (closed 11:30:16 without being done). But it recurred as `st-1783517` and
   *was* implemented; `python3 todo.py --version` prints `todo 1.0.0` at the current head. The
   permanently false-closed task is `st-19ee694` — `except Exception` is still present at
   `fb3657a:450`.

3. **"F1 and F11 compounded into a duplicate `__version__` definition" — REFUTED.** `1b04400`
   defines `__version__` exactly once (line 19); line 449 is a *usage* inside
   `add_argument("--version", ...)`. The draft counted a `grep -c` total as a definition count.
   Claim removed entirely.

Method note: all three errors came from reading a partial view — a mid-operation container
snapshot, a single commit, a grep count — as settled state. The findings that survived audit are
those established by driving the code or reading the event log end to end.
