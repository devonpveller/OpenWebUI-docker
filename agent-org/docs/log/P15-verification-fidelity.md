# P15 — Verification fidelity: the loop converged on the wrong evidence

**Status:** planned, nothing built. **Do not implement until the next operator test report.**
Authored 2026-07-19.
**Evidence:** the operator's own evaluation of gym-013's delivery, 2026-07-19.
**Supersedes the reading of:** E5 in [`gym-010-ground-truth.md`](gym-010-ground-truth.md).

---

## The thesis

gym-013 was the first run where the drain loop converged: round 1 propagated 1 task, round 2
propagated 0, `scope_completed` fired at round 2 of a 40-round cap. E1 and E5 passed.

**The operator then tested the delivered product and found 5 bugs and 10 design gaps — including a
failing test.**

Both statements are true, and that is the finding. The loop terminated correctly *on the evidence it
had*. The evidence was wrong in three separate ways:

1. it was gathered on the **wrong platform**,
2. it was **filtered too aggressively** before becoming work,
3. it was never checked for whether the code under test **can actually run**.

**P15 is about the quality of what the loop counts, not about the counting.** P10–P14 fixed the
mechanism. This is about its inputs.

---

## Evidence

The operator ran the delivered branch on Windows. The org ran it in a Linux container.

| Operator finding | What the org did | Orchestration defect |
|---|---|---|
| **B1 — `UnicodeEncodeError` on `★` (U+2605) under CP1252. Test suite 47/48, one FAILING.** Any list/search/summary with a high-priority item crashes on Windows. | Org ran **48/48 PASS**, six lens sweeps, two drain rounds. Never saw it. | **P15.1** — the org's verification environment is not the operator's, and nothing surfaces the difference |
| **G5 — REPL swallows error output** (`if rc != 0: pass`) | The `clean_code` lens **found this exact line** in gym-012 and again here. Graded a preference. **Dropped.** | **P15.2** — severity floor miscalibrated |
| **G8/G9 — `done`/`reopen` on an already-done/active item silently succeed** | The `goal_alignment` lens found these in **both** rounds. **Dropped as preferences.** | **P15.2** |
| **G6 — empty text accepted** | Lens found it. **Dropped.** | **P15.2** |
| **G1/G7 — no sorting, no `--sort` flag** | Lens found it. **Dropped.** | **P15.2** |
| **B5 — `cmd_add`'s priority check is dead code** (argparse `choices` validates first) | The drain's **only task of the entire run** was "add a test for the invalid priority error path". The worker discovered argparse raises `SystemExit(2)` first — and **wrote a test for an unreachable branch** without flagging the dead code. | **P15.3** — a task can be satisfied against code that cannot execute |
| **B4 — `save_items` crashes when `TODO_DB`'s parent directory is missing** | Six lens sweeps exercised ~80 CLI invocations. None set `TODO_DB` to a path with a missing parent. | **P15.4** — lenses probe inputs, never the environment |
| **B2/B3 — `_validate_item` doesn't coerce `done` to bool; accepts `id=False`** | Not found by any lens. | **P15.4** |

**Round 1 dropped 11 of 12 findings** (`clean_code` 6 dropped/1 kept; `project_documentation` 5
dropped/0 kept). **At least four of those dropped findings appear in the operator's report as real
gaps.** The calibration question flagged during the run is now answered with evidence: the floor is
too aggressive.

---

## Evidence, part 2 — the clean-code report (operator, 2026-07-19)

The org's `clean_code` lens and the operator evaluated the same file against the same prompt. They
**disagree systematically, in one direction**: the lens is more generous, and it grades against a
bar it invents rather than the one it was asked about.

| Criterion | Org's `clean_code` lens | Operator | |
|---|---|---|---|
| SOLID | *"Strong… good separation of concerns, extensible dispatch"* | **2/5** — *"Open/Closed ❌ Weak — no plugin/dispatch registry"*; *"Dependency Inversion ❌ Weak"* | **direct contradiction** |
| `Dict[str, Any]` data model | noted; graded **PREFERENCE, dropped** | **Top recommendation #1** — *"the single biggest structural weakness"* | found, then discarded |
| Documentation | *"Good"* | **6/10** — *"the code doesn't invite documentation growth"* | |
| Display-formatting duplication in `cmd_list`/`cmd_search` | **not found** | **Top recommendation #3** | missed outright |
| Overall | *"production-quality for its scope"* | **7/10** — *"works at its scale but doesn't architect for growth"* | |

Three distinct defects show up here:

**(a) The lens found the operator's #1 item and P13.6 dropped it.** `TypedDict`/`dataclass` was
graded a preference. The operator's reasoning is exactly why that grading is wrong: the raw dict
*"makes it impossible to auto-generate API docs for the data shape"* — i.e. it is the direct answer
to the lens's own question, *"does the code support good documentation?"*

**(b) The lens self-limits with "for its scope".** The operator's verbatim prompt asks whether the
code practises SOLID, industry-standard patterns, clear naming, and documentation support. It sets
no scale qualifier. The lens repeatedly supplies one — *"for its scope"*, *"at this scale"*,
*"aspirational here"* — and grades generously against it. **That is verdict framing**, the thing
P10.1 removed from the prompt, reappearing in the answer.

**(c) The lens answered a different question than the one asked.** It inventoried what
documentation *exists* (docstrings, README, `--help`). The prompt asks how the codebase *supports*
documentation. The operator answered the actual question and reached a different conclusion.

---

## Evidence, part 3 — the commit-history report (operator, 2026-07-19)

| Dimension | Operator | Org's `project_documentation` lens |
|---|---|---|
| Context linking | **2/10** — *"No references to specs, scenarios, issues, or the harness branch"* | never raised |
| Handoff readiness | **4/10** — *"Commit 1 is a black box"* | *"Passable for a seed commit"* |
| Intent clarity | **5/10** — *"none say why at a strategic level"* | found it — *"the why is thin"*, *"reads like a changelog"* — **5 findings, 0 kept** |
| Overall | **5/10** — *"mechanically sound, narratively thin"* | *"strong commit message… well above average for agent-generated history"* |

Two new items, and one confirmation:

**(a) The genesis commit is ours and it is empty.** `greenfield_arena` (gym runner) writes
`"gym: greenfield arena from template python-todo for scenario-013-drain"` **with no body**. The
operator calls it *"the most important commit"* and *"a black box"* — it can't say what the scenario
is, what the template provides, or what the agent is expected to do. That is my code, not the org's.

**(b) The org never links context it already holds.** It knows the scenario id, the goal, the
acceptance corpus and the harness branch, and puts **none** of it in the commit. Context linking
scores 2/10 for information the org had the whole time.

**(c) Same compounding failure, third time.** The `project_documentation` lens found the "why is
thin / feature dump" problem in **both rounds**, and P13.6 dropped **5 of 5**.

---

## The cumulative finding across all three reports

The three operator reports are the reference standard for the three standing lenses. Compared
side by side, **the org's lens is more generous than the operator on every single one**:

| Lens | Org's verdict | Operator's verdict |
|---|---|---|
| `goal_alignment` | *"No crashes, no data corruption, no security issues"* | **5 bugs**, one High — a crash that fails the test suite on the target platform |
| `clean_code` | *"Strong" across every criterion; "production-quality for its scope"* | **7/10**; SOLID **2/5** |
| `project_documentation` | *"well above average for agent-generated history"* | **5/10**; context linking **2/10** |

This is not three separate calibration errors. It is one systematic bias with two compounding
mechanisms:

1. **The lens softens its own findings** by inventing a scale bar the prompt never set
   (*"for its scope"*, *"at this scale"*) — P15.2b.
2. **P13.6 then drops the softened findings** as preferences — P15.2.

Between them, **the operator's top recommendation from two of the three reports left no trace in the
task queue at all.** The drain loop converged to zero in gym-013 partly because the evidence had
been filtered twice before it was counted.

**The mechanism is sound; the evidence pipeline is not.** That is the whole of P15.

---

## THE PLAN

---

### P15.1 — Verify on the target platform, or say you didn't  ⭐ highest severity

**Cause.** Workers, lenses and the org's own `check_cmd` all run in Linux containers. The operator
runs Windows. A product can be 48/48 green in the org and crash on first use.

**This is not a todo-app bug.** It is the org declaring a scope complete on evidence gathered
somewhere the product will never run. Every future delivery has this exposure.

**Options, cheapest first:**
1. **Declare the gap.** The closure/PR note states the verification platform explicitly:
   *"verified on linux/x86_64 (python 3.12); NOT verified on the operator's platform."* Honest, ~zero
   cost, and it puts the caveat where the human reads it.
2. **Encode the target in the acceptance corpus.** A durable check that forces the failure mode —
   e.g. run the suite with `PYTHONIOENCODING=cp1252` — so a platform-specific crash is caught by the
   existing gate machinery.
3. **A second verification lane** on the target platform. Correct, expensive, later.

**Recommendation: (1) now, (2) as the durable fix.** (1) makes the limit visible immediately; (2)
turns this specific class into a permanent gate.

**Assertions.** A delivery note names the verification platform; a corpus check running the suite
under a non-UTF-8 encoding fails on the current delivery.

---

### P15.2 — Recalibrate the severity floor

**Cause.** P13.6 grades each finding DEFECT or PREFERENCE and queues only defects. It cut round 1
from 12 findings to 1 — and dropped at least four the operator counts as real.

The floor was built because two of three lenses had no fixed point (gym-009: 21 → 23 → ascending;
gym-011: 7 of 12 tasks were commit-message preferences). **That problem was real.** But the current
grading treats "the program silently does the wrong thing" as taste.

**Change — sharpen the grading, don't remove it:**
- **DEFECT** explicitly includes: a command that reports success while doing nothing (`done` on a
  done item), swallowed error output, accepted invalid input (empty text), a crash on any input, an
  untested error path, a documented behaviour that isn't true.
- **PREFERENCE** is narrowed to: naming, formatting, structure, wording, and *additive* features
  the goal never asked for (sorting flags, colour, backups).
- **A third grade — `GAP`** for goal-relevant work that is neither: queued, but ranked below
  defects, and **excluded from the propagation count** so it cannot prevent termination.

**Why the third grade.** The floor's job is to let the count converge. A GAP that is queued but
uncounted keeps real work visible without reintroducing the non-terminating loop.

**Assertions.** "`done` on an already-done item silently succeeds" grades DEFECT; "add a `--sort`
flag" grades PREFERENCE; a round of pure PREFERENCE still propagates zero; a GAP is queued but does
not increment `new_tasks`.

---

### P15.2b — A lens must not invent a bar to grade against

**Cause.** The operator's lens prompts deliberately carry **no scale qualifier** — P10.1 stripped
verdict framing precisely so a lens would observe rather than adjudicate. The `clean_code` lens
reintroduces it in its answer: *"for its scope"*, *"at this scale"*, *"SOLID is somewhat
aspirational here"* — then grades **Strong** where the operator, asked the same question, grades
**2/5**.

**A generous grade is not a neutral one.** It flows into P13.6, which is asked to sort findings into
DEFECT and PREFERENCE. A lens that has already decided the code is *"production-quality for its
scope"* supplies findings pre-softened, and the floor then drops them. **The two mechanisms compound
in the same direction**, which is how the operator's #1 recommendation left no trace in the queue.

**Change.** In the wrapper around the operator's verbatim prompt (not the prompt itself):
- state that the assessment is against the **named criteria as written**, not against a bar the lens
  chooses for the project's size;
- ask it to **answer each question the prompt asks**, in order — for `clean_code` that is SOLID,
  industry-standard patterns, naming conventions, and *how the codebase supports documentation*;
- keep the existing ban on verdicts and on "nothing" affordances.

**Why this is safe.** It changes the framing the org adds, not the operator's three verbatim
prompts, so the structural debias is untouched.

**Assertions.** A lens report contains no self-supplied scale qualifier; a report addresses each
named criterion; the `Dict[str, Any]`-blocks-documentation finding survives to the queue.

---

### P15.3 — A task must not be satisfied against unreachable code

**Cause.** The drain's single task was to test `cmd_add`'s invalid-priority error path. The worker
found argparse rejects the value first (`SystemExit(2)`), wrote a test asserting `SystemExit`, and
reported done. The branch it was asked to cover **cannot execute**. Nobody flagged the dead code —
the operator did, as B5.

**Change.** When a task names a specific code path, the implementer must state whether that path is
**reachable**. Unreachable ⇒ the honest deliverable is *"this branch is dead; remove it"*, not a test
that exercises something else. This is a prompt-level change to the drain implementer brief, in the
same register as the `ESCALATE:` protocol.

**Assertions.** A task naming an unreachable branch produces a report saying so; the resulting work
removes the dead code or explains why it stays.

---

### P15.4 — Lenses probe inputs; nothing probes the environment

**Cause.** Six sweeps ran ~80 CLI invocations covering empty strings, invalid dates, bad ids,
special characters, and REPL piping — all *argument* space. None touched *environment* space, so
`TODO_DB` with a missing parent directory (B4) survived every round, as did the `_validate_item`
type-coercion gaps (B2/B3).

**Change.** Extend the `goal_alignment` lens prompt to include environment and precondition probing:
a missing/unwritable target directory, a read-only path, a pre-existing file of the wrong type, an
absent env var, a partially-written file. **This is the operator's own prompt to amend** — it is one
of the three verbatim lens prompts, and changing it is a deliberate act, not a refactor.

**Assertions.** A round's goal-alignment report references at least one environment/precondition
probe; the B4 class (missing parent directory) is caught.

---

### P15.5 — Commits must carry the context the org already holds

**Cause.** The org knows the scenario id, the goal, the acceptance corpus and the branch, and puts
none of it into the commit message. Context linking scored **2/10**.

**Change (two parts):**
1. **The genesis commit** — `greenfield_arena` in the gym runner writes a subject with no body. Give
   it one: what the scenario is, what the template provides, what the agent is expected to do, and
   where the harness definition lives. This is a gym-runner change, not an org change.
2. **The worker's commit brief** — the drain implementer and the delivery step should be told to
   record *why*, not only *what*: the goal this serves, the acceptance check it satisfies, design
   decisions taken and their alternatives. The operator's report contains model text for all three
   commits; use its shape.

**Why this matters beyond tidiness.** The operator's closing line is the requirement: *"for an AI
orchestration gym where the whole point is reproducible agent evaluation, the commit history should
be a traceable record of the agent's reasoning — not just its output."*

**Assertions.** The greenfield commit body names the scenario, the template and the expectation; a
drain commit body states the goal it serves and the check it satisfies.

---

## What P15 does NOT change

**The drain loop's mechanism is sound and should not be touched.** gym-013 demonstrated: three
lenses reporting fresh (6/6, zero truncations), gap analysis scoped to a 67-char child scope, zero
phantom tasks across both rounds, a scope-respecting plan approved first pass, and termination on a
counted zero at round 2 of 40. P10–P14 did what they set out to do.

**The result to carry forward, stated precisely:** *the loop converges correctly on the evidence it
is given, and gym-013 proved the convergence. P15 is about the evidence.*

---

## Traps

1. **Do not remove the severity floor.** Without it the count cannot converge — that is measured, not
   theoretical (gym-009, gym-011). Sharpen the grading; keep the mechanism.
2. **P15.4 edits one of the operator's verbatim lens prompts.** Those are the debias; changing them
   is a deliberate decision, and the change should be minimal and additive.
3. **Config defaults OFF** for any new flag.
4. **Never commit or push on the operator's behalf unless asked.**

---

## Definition of done

1. A delivery states the platform it was verified on.
2. "Silently succeeds while doing nothing" grades DEFECT, not PREFERENCE.
3. A GAP grade is queued without blocking termination.
4. A task naming an unreachable code path is reported as such.
5. A lens sweep probes environment preconditions, not only arguments.
6. Full unit suite green.

## Validation

The operator's next test report is the measurement — the gym never scores product quality. Success
is a **shorter operator report** on the following run: no High-severity platform crash, and the
silent-no-op / swallowed-error class caught by the loop rather than by the human.
