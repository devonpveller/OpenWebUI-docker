# P18 — observation fidelity: what the loop sees, keeps, and invents

Evidence base: **gym-016** (`effort-gym-016-todo-product`), 2026-07-20, two drain rounds, PR #17 /
PR #18. Goal byte-identical to gym-008..gym-015 except the effort name, so the orchestration was
the only variable.

gym-016 was the first round to run with P17 deployed (12 of 16 findings). **Six of those fixes
held under live conditions, one failed outright, and the run surfaced three new defects.** This
document records all three groups, because the negative result is as load-bearing as the
positives: it tells us which class of fix works.

---

## The headline, stated honestly

**The loop stopped lying. It is not yet demonstrated to converge.**

```
gym-015   r1 0 new / 3 open   r2 1/4   r3 1/5   r4 2/3   r5 0/6      ASCENDING, invented work
gym-016   r1 3 new / 3 open   r2 3 new / 3 open  (r2 sweep INCOMPLETE)
```

gym-016's round 1 produced three real, located defects instead of gym-015's packaging metadata and
a no-op. But round 2's sweep did not complete, so its count is not evidence of anything — and the
trajectory question therefore remains open. **A clean convergence claim needs a run where every
sweep completes.** That is P18's primary objective.

---

## Part 1 — what held (do not regress these)

Recorded with the event that proves it, so a future change that breaks one is detectable.

### F3 — a partial sweep is not a sweep ✅ HELD UNDER THE REAL CONDITION

Round 2 reproduced gym-015's exact failure and was caught:

```
lens_report_truncated  {"lens": "goal_alignment", "round": 2, "chars": 44,
                        "body": "Now let me test malformed database handling:"}
lens_sweep             {"round": 2, "lenses": ["clean_code", "project_documentation"]}
drain_round            {"round": 2, "new_tasks": 3, "open": 3, "swept": FALSE}
                       ← and NO gap_analysis event for round 2
```

The org posted, unprompted:

> ⚠️ **Drain round 2 — the sweep did not complete.** The **goal_alignment** lens produced no
> report (only clean_code, project_documentation reported), so the product was never compared
> against the goal this round. This is **not** a clean sweep and says nothing about whether the
> work is finished.

In gym-015 the identical condition recorded `swept: true` and passed as a normal round. This is
the single most important thing P17 bought.

### F16 — the plan is observed, not recalled ✅ HELD

Every plan turn ran in its own session and produced cited observations:

```
worker_acquire  effort-gym-016-todo-product~plan       ← plan, isolated session
worker_acquire  effort-gym-016-todo-product~planclean  ← workspace probe (F1)
worker_plan_approved
worker_acquire  effort-gym-016-todo-product            ← execution, base session
```

> ALREADY DONE: Basic `add`, `list`, `done` commands work (`todo.py`, 5 tests pass) / JSON
> file-backed storage with `TODO_DB` env override / All 5 existing tests pass green

gym-015's equivalent turn ran in the builder's session and replied *"the work is already complete
— all 51 tests passing"* in 21 seconds, from memory.

### F9 — a child scope must narrow ✅ FIRED CORRECTLY

```
scope_child_rejected_not_narrower {"title": "todo command logic",
  "scope": "add, list, done, delete, edit, reopen, clear-completed, summary, priorities,
            due dates, and filters"}
scope_decomposed_live {"children": 3, "source": "reports", "evidence": 3}
```

The rejected child restated the parent as a feature list. The three survivors are genuinely
narrower and well separated — `testing and documentation`, `cli and repl interface`,
`json data storage`.

### F8 — diff-aware lenses ✅ HELD

All three round-2 lenses opened with `git diff --stat HEAD~1 HEAD && git diff HEAD~1 HEAD`, then
probed the changed surface. The `goal_alignment` lens went straight to
`todo.py add "test" --due "2026-2-1"` — the exact input round 1's fix had changed behaviour on.
The `clean_code` lens independently critiqued the same fix.

gym-015's lens had no such signal, probed `_parse_repl_line` eight times with well-formed input,
and certified the surface the loop had just broken.

### F10 / F11 — no invented work, no false absences ✅ HELD (round 1)

Round 1's three tasks, each with a file and line:

```
Fix `_repair_item` to validate and handle invalid priority values      todo.py:55-62
Fix `_parse_date` to enforce strict zero-padded date formatting        todo.py:108-111
Refactor cmd_repl to dispatch without private argparse internals       todo.py:215-247
```

`false_absence_rejected`: **0 events** — nothing false was derived, so the filter had nothing to
reject. Compare gym-015: `pyproject.toml`, a version string, mypy config (none in the goal), plus
"Implement database path configuration" for a `db_path()` that already existed.

### F2 / F13 — delivery integrity ✅ CLEAN (untested)

`delivery_orphans_head`: 0. `plan_turn_wrote_files`: 0. Linear history, no rework residue — the
commit-history lens found three clean commits where in gym-015 it twice detected *"a merge that
silently absorbed two identical efforts"*.

**Caveat: these guards were never exercised.** No stale workspace and no writing plan turn
occurred, so their correctness under fire is still unproven. Do not read 0 as validation.

---

## Part 2 — what failed

### F4 — incremental lens emission ❌ FAILED

The instruction added in P17:

> *"write each finding as a complete, self-contained sentence AS SOON AS you establish it, before
> moving to the next check. Do not save your findings for a summary at the end."*

Round 2's `goal_alignment` lens ran ~30 well-chosen probes over ~4 minutes and emitted **44
characters**: `"Now let me test malformed database handling:"`. Identical failure to gym-015's
70-character truncation.

**Diagnosis.** This was a prompt-level fix for a structural problem. The model's natural rhythm is
probe-then-report, and one instruction line does not override it — the same budget that funds
probing funds reporting, so a lens that probes to exhaustion has nothing left to report with. It
is also NOT a timeout (harness bound is 5400s; the turn died at ~4 minutes), so no watchdog or
limit change touches it.

**Fix (P18.1) — make it structural, not textual.** Options in preference order:

1. **Bounded probe/report passes.** Run the lens as N short turns: each is given the prior turn's
   findings, probes a bounded number of new things, and returns findings. The report is the
   accumulation across turns. A dead turn loses one pass, not the sweep.
2. **Stream findings out of the turn.** Have the lens write each finding to a file
   (`>> /workspace/.lens-findings`) as it establishes it, and read that file after the turn
   regardless of how the turn ended. The harness already runs shell commands; this needs no model
   cooperation beyond one instruction, and unlike P17's wording it leaves an artifact that
   survives truncation.
3. Retry a truncated lens once with an explicit "report only, do not probe" instruction.

(2) is the smallest change and the most robust — it converts a claim held in context into an
artifact on disk, which is this codebase's recurring answer (see ORCHESTRATION-DESIGN §11).

**Test:** a lens turn killed mid-probe still yields every finding it had established.

---

## Part 3 — new findings

### F17 — a false DEFECT passes unfiltered (severity: high)

Round 2's `clean_code` lens reported:

> *"`_parse_date` ... However, `strptime` still accepts invalid dates like `2025-02-30` — the
> regex checks shape but not semantic validity ... the function claims to validate but does not
> fully validate."*

Verified false:

```
$ python3 -c "datetime.strptime('2025-02-30','%Y-%m-%d')"
  -> rejected: day is out of range for month
$ python3 todo.py add t --due 2025-02-30
  invalid date format: 2025-02-30 (use YYYY-MM-DD)     exit=1
```

It became a task, and it is open on the delivered branch:

```
open | clean_code | Reject semantically invalid dates in _parse_date
```

That is fabricated work against correct code — the same waste F11 was built to stop, arriving by
the opposite route.

**Cause.** P17's `_drop_false_absences` fires only on bodies matching
`add|create|implement|introduce|expose|define|provide|include|write`, and only when every
identifier it names is already present. That restriction was deliberate: a filter that also fired
on `fix`/`remove` could delete real repair work, which is a worse failure. The consequence is a
blind spot exactly the size of the exclusion — **an asserted absence is checked; an asserted
misbehaviour is not.**

**Why the fix works.** A finding that names an input and a claimed outcome is mechanically
checkable in precisely the way an absence is. "`strptime` accepts `2025-02-30`" is settled by
running it. This is ORCHESTRATION-DESIGN §11 again — the boundary is an executable contract — and
it is the same move as F5 (carry the exit code) and F11 (grep for the symbol).

**Fix (P18.2).**
- Require a lens finding that alleges a misbehaviour to name the **command or call** that
  demonstrates it. The lens prompts already ask for observed evidence; make the reproduction
  explicit and machine-extractable.
- Before filing such a task, RUN the named reproduction. If it does not reproduce, drop the task
  and log `false_defect_rejected`.
- Fail OPEN: an unrunnable or unparseable reproduction files the task as today. Never delete work
  on a failed check.
- **Test:** a report claiming `_parse_date` accepts `2025-02-30`, against code that rejects it,
  yields zero tasks and logs the rejection.

### F18 — turns assert verification they did not perform (severity: medium)

Three instances in one run:

| turn | ran | claimed |
|---|---|---|
| step 3/4 | `git log --oneline -5` | "All 31 tests pass, acceptance corpus passes" |
| drain no-op | `git log --oneline -3` | "31/31 tests pass, acceptance corpus passes" |
| drain plan | full suite | "The existing test suite (**28 tests**) passes" — it is **31** |

The third is the interesting one: a checkable number, stated wrong, from an agent that had just
run the command printing the right one. Verified: `Ran 31 tests ... OK`, and 31 `def test_` on the
remote head.

All three were harmless — nothing had changed, and the count went unused. **But the org cannot
know that**, and this is the same mechanism by which gym-015's delivery claim outlived the commit
it referenced (P17 F2).

**Fix (P18.3).**
- A turn reporting a verification result must either have run it in that turn, or say
  "unchanged since `<sha>`" explicitly.
- Prefer the machine path: the org already runs `check_cmd` itself at the delivery chokepoint
  (`_publish_and_verify`). Extend that to the drain's per-round reporting rather than quoting the
  worker.
- **Test:** a turn that claims a suite result without a suite invocation in its command log is
  re-asked or has the claim stripped.

### F19 — a sweep's observations are spent on one scope and discarded (severity: high)

Round 1's `goal_alignment` lens found and precisely diagnosed a serious defect:

> *"**The REPL is broken for any command that takes flags.** `line.split(None, 1)` means
> `add test --priority high` becomes `["add", "test --priority high"]` ... `list --status active`
> is rejected by argparse as an unrecognized argument."*

It never became a task. Verified still broken at the delivered head `23ebdc3`:

```
$ printf 'add buy milk --priority high\nquit\n' | python3 todo.py repl
$ cat todos.json
  text    : 'buy milk --priority high'      ← flags swallowed into the text
  priority: medium                           ← never applied
```

**Cause.** The lens sweeps the WHOLE branch, but gap analysis mines its report against ONE
selected scope:

```
scope_decomposed_live  {"children": 3}      ← testing+docs | cli and repl | json data storage
gap_analysis           {"tasks": 2, "goal_chars": 68}   ← ran against `json data storage` only
drain_round            {"round": 1, "scope": "sn-c294780e72cc"}
```

The REPL finding belongs to `cli and repl interface`, which was not the selected scope, so the
observation was discarded. It must be re-derived from scratch when (if) that scope is selected.

This is not the tier walk misbehaving — bounded scopes are correct and P17 F9 made them sharper.
The defect is that **the org already has routing for this one step later and not here.**
`_seam_owner` / `_best_scope_for` file a TASK to the scope that owns it; nothing does the
equivalent for an OBSERVATION. A report that covers three scopes is mined for one and thrown away.

**Consequences.** (a) A diagnosed, serious defect sits in nobody's queue and ships in the PR.
(b) Every scope costs a full three-lens sweep to re-observe what an earlier sweep already saw.
(c) It inflates round count and wall-clock, which is part of why "does it converge" is hard to
answer.

**Fix (P18.4).**
- Run gap analysis once per OPEN scope against the same report set, not once per round against the
  selected scope. The report is already in hand; this is extraction, not observation.
- File each resulting task through the existing `_seam_owner` / `_best_scope_for` routing.
- Keep the selected scope for DISPATCH (one scope's work at a time is the point of the tier walk);
  only the extraction fans out.
- **Test:** a report describing defects in two sibling scopes produces tasks in both, with the
  non-selected scope's task filed to its owner and left open.

---

## Part 4 — carried forward from P17, still unbuilt

| id | what | why it is still open |
|---|---|---|
| **F1/F15 prevention** | read-only workspace for `plan_only` turns | enforcement lives in the worker image (`/opt/git-proxy/git_proxy.py`); needs a daemon change + image rebuild that this test suite cannot validate. The P17 revert guard is the interim, and it never fired in gym-016 |
| **F12 refile** | route scope-refused tasks to their owner | drain dispatch is fire-and-forget (`_spawn`), so the WON'T DO reply is never in scope. `dispatched` stops the false claim; routing still missing. **Note: F19's fix creates the extraction fan-out that F12's refile could reuse** |
| **F13 test-count monotonicity** | store the delivery test count, block a decrease | specified in P17, not built. gym-016 showed why it matters: a turn misreported 31 as 28 and nothing noticed. Cheapest remaining detector |
| **H1** | lint + typecheck in `check_cmd` | no linter in the worker image and no package egress (`pip install mypy` fails). Same image blocker as F1. Adding a token `compileall` pass would be the "checker that cannot run" theatre this plan warns about |
| **D1** | GAP tasks excluded from the propagation count | operator decision, unchanged |

---

## Build status (2026-07-20)

| Finding | State | Where |
|---|---|---|
| **F19** — observations spent on one scope | ✅ **SHIPPED** | `_extraction_scopes` fans gap analysis across every OPEN scope in the tier; existing `_seam_owner` routing files each task to its owner. Dispatch still works one scope at a time |
| **F4** — findings die with the turn | ✅ **SHIPPED** | lenses append `FINDING:` lines to `/tmp/lens-findings.txt`; `_salvage_lens_findings` reads them back when a turn truncates, `_clear_lens_findings` wipes between lenses so one lens can never inherit another's |
| **F17** — false defects pass unfiltered | ✅ **SHIPPED** | `_drop_false_defects` runs the `REPRO:` command the LENS named; drops the task only on positive evidence the input is handled |
| **F13** — test-count monotonicity | ✅ **SHIPPED** | `_check_test_count_regression` at the delivery chokepoint; a drop raises `test_count_regressed` and posts to the operator. Flags, never blocks |
| **F18** — turns assert unverified results | ✅ **SHIPPED** | `WorkResult.commands` carries what the turn actually ran (the daemon already reported it; the harness already streamed it). `_flag_unverified_claim` compares a verification CLAIM against that record and posts when a turn reports a suite result with no test invocation in it |
| F12 refile, F1/F15 prevention, H1 | ⬜ not built | carried from P17; F1/H1 remain image-blocked |

Tests: `tests/test_p18_observation.py` (17, all green).

### F18's three deliberate silences

The check flags and never blocks, and stays quiet in three cases where firing would be worse than
missing:

- **An honest carry-forward.** "already delivered in the previous turn ... 31/31 tests pass" is
  legitimate and is most of what no-op turns say. The defect is silence about provenance, not the
  carry-forward, so a turn that states where the result came from passes.
- **An unreadable command record.** `_command_texts` returns `[]` both for "ran nothing" and for
  "a shape we could not parse". Flagging the second would cry wolf on every daemon whose activity
  schema drifts, so empty always means "cannot tell".
- **Intent rather than claim.** "Next I will run the test suite" is not a result.

The delivery chokepoint still runs `check_cmd` itself, so this adds visibility without becoming a
second, weaker arbiter of whether the build is good.

### Two design errors caught while building, both by the tests

1. **The F17 check was gym-overfitted.** The first draft synthesised a probe —
   `python3 todo.py add probe --due <literal>` — which works on the gym's todo CLI and is
   meaningless on any other project. An orchestrator-level gate cannot know how to drive an
   arbitrary product. Replaced with running the `REPRO:` command the lens itself names; a finding
   with no named reproduction is unchecked and therefore KEPT.
2. **`REPRO:` lines became their own tasks.** `_plain_tasks` splits on newlines, so a repro line
   was content-addressed into a task body and would have been dispatched to a worker — worse than
   the fabricated task F17 exists to prevent. Now folded into the finding above it, which also
   keeps the reproduction where `_drop_false_defects` looks for it.

Both would have shipped silently without a test that exercised the whole path.

## Implementation order for P18

1. **F19** — extraction fan-out across open scopes. Highest value: it recovers observations the
   org already paid for, stops diagnosed defects vanishing, and lays the routing groundwork F12
   needs.
2. **F4 (P18.1)** — findings streamed to a file that survives truncation. The one P17 fix that
   failed, and the reason round 2's count is uninterpretable.
3. **F17** — run the named reproduction before filing an alleged misbehaviour.
4. **F13 test-count monotonicity** — one integer, blocks a whole class of silent regression.
5. **F18** — a verification claim must carry a verification, or an explicit "unchanged since".
6. Then the image-blocked work (F1/F15 prevention, H1) as one batch, when an image rebuild can be
   tested properly.

**Gate:** full suite green, deploy, wipe the arena, and run a scenario whose success criterion is
narrower than before — **every round's sweep completes, and the count descends to an evidenced
zero.** gym-016 could not answer that; gym-017 must.

---

## Method note

Three claims in the working draft of this document were checked and corrected before it was
written:

1. *"the REPL fix may have incidentally fixed the flag bug"* — refuted by running it at `23ebdc3`.
2. *"my F11 filter suppressed the REPL finding"* — refuted: `false_absence_rejected` has zero
   events; the cause was scope selection (F19), not filtering.
3. *"tests regressed 31 → 28"* — refuted: 31 on the remote head and in a live run; the worker
   simply misreported (F18).

Each was a plausible reading of partial evidence. The pattern that catches them is the same one
this plan keeps prescribing for the org: run the thing rather than reason about it.
