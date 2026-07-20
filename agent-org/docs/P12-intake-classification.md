# P12 — Intake classification: stop reading a build request as a bug report

**Status:** planned, nothing built. Authored 2026-07-19.
**Owner:** any session. **Self-contained.**
**Evidence base:** gym-010 attempt 1 — [`gym-010-ground-truth.md`](gym-010-ground-truth.md).
**Prerequisite for:** gym-010 attempt 2 (E1 and E5 remain unmeasured).

---

## The thesis

A greenfield feature build was routed through the org's **error-report intake path**. The scenario
text is clean; everything wrong was appended by the org on the way in:

> **THIS IS A RUNTIME / BEHAVIORAL SYMPTOM** … REPRODUCE the symptom … confirm it **FAILS** …
> `BEFORE:` FAIL — *the failing run's evidence on the unfixed code*
> **PRIOR ATTEMPTS AT THIS SAME ERROR** (the operator reports it **AGAIN**) …
> First fetch and READ those branches … **BUILD ON IT**

There was no symptom, no unfixed code, no prior attempt, and no error. Two independent classifiers
misfired on ordinary English, and their outputs compounded: the first opened the error-report
branch, the second filled it with false history.

**This is a classification defect, not a prompt-wording defect.** The prompts are correct *for a bug
report*. They were shown to a build.

---

## Evidence

All figures below are reproducible against the stored objective for
`effort-gym-010-todo-product` (`goal_versions`, 5458 chars).

### Cause A — `_runtime_symptom_phrase` matched two words of prose

`orchestrator.py:9124` (`_RUNTIME_SYMPTOM_RE`), branch taken at `:5367`.

Running the live regex over the scenario text yields exactly two matches:

| match | source line | what it actually is |
|---|---|---|
| `crashes` | *"…never corrupts or **crashes** on malformed or missing-field data"* | a **quality requirement** — the product must NOT crash |
| `hang` | *"…it will **hang** your turn"* | a **warning about the worker's own turn** — not about the product at all |

Neither is a reported symptom. The regex is keyword-only: it carries no polarity (so *"must never
crash"* reads the same as *"it crashes"*) and no subject (so *"your turn will hang"* reads as a
product defect). One match appends the whole `_REPRO_CLAUSE`.

**Downstream cost, observed:** the worker was required to produce a `BEFORE: FAIL` line evidencing a
failure that does not exist, and emitted the contorted
`BEFORE: 5/5 pass (original tests only — reopen command missing…)`. It manufactured a "before
failure" because the goal demanded one.

### Cause B — `_attempt_history` treats slashes and apostrophes as tool output

`orchestrator.py:5042` (`_sig`).

A line counts as an "error signature" if it is ≥30 chars **and** contains an error keyword **or**
`'` **or** `\` **or** `/`. Measured on the scenario text: **8 of 23 lines qualified**, including:

```
Beyond add/list/done,
levels (low/medium/high), due dates, search by text, and filters (by
a clear-completed action. Give it a real UI/UX: an interactive REPL mode -
final-product quality bar: every command exposes clear --help/usage, all
deliver a full feature set: delete a todo, edit a todo's text, priority
```

English prose uses slashes and apostrophes constantly. Those lines were then matched against other
efforts' goals — and because gym-004…009 carry **byte-identical goal text by design** (so rounds are
comparable), every line matched, producing three "prior attempts at this same error."

### Cause C — a DELETED branch qualifies an effort instead of disqualifying it

`orchestrator.py:5079-5083`:

```python
if d.verifiable and d.exists:
    fact = f"branch `{branch}` … UNMERGED"
elif d.verifiable:                       # exists == False
    fact = f"branch `{branch}` never reached `{repo}`"
```

Branch absence only changes the **wording**; the effort stays in the list and the closing
instruction — *"First fetch and READ those branches"* — is emitted regardless. So a greenfield wipe
**re-arms** this block: deleting the branches is precisely what makes prior efforts read as
unpublished failed attempts.

**Observed:** the worker dutifully ran
`git fetch origin agent/effort-gym-008-todo-product agent/effort-gym-004d-todo-product
agent/effort-gym-007-todo-product` → failed (deleted), then created
`agent/effort-gym-008-todo-product` as its own working branch.

### Cause D — lifecycle is not consulted at all

`orchestrator.py:5052`: `await self.gate.snapshot(open_only=False)`.

**This corrects an earlier proposal in this investigation.** The first draft of this plan suggested
"close the stale gym efforts" as a fix. It would not have worked: `open_only=False` means every
effort is scanned regardless of lifecycle.

**Proof:** the emitted block listed `effort-gym-004d-todo-product`, whose lifecycle is **`aborted`**.
Current gym efforts: 5 `open`, 8 `aborted`, 8 `done` — and an aborted one still appeared.
Closing efforts changes nothing while the filter ignores the field.

---

## THE PLAN

Four increments. **P12.1 and P12.2 are independent and each removes one necessary condition** —
either alone would have prevented gym-010's misclassification.

---

### P12.1 — A symptom must be REPORTED, not FORBIDDEN  ⭐ start here

**Cause:** A (keyword match with no polarity, no subject).

**Change:** gate `_RUNTIME_SYMPTOM_RE` behind two rejections, applied per match:

1. **Negated / aspirational context** — reject when the window preceding the keyword contains
   `never`, `not`, `must not`, `n't`, `should not`, `avoid`, `prevent`, `without`, `no`, or
   `instead of`. *"never corrupts or crashes"* is a requirement; *"it crashes on startup"* is a
   report.
2. **Second-person process warnings** — reject a match whose sentence addresses the worker rather
   than the product (`your turn`, `you are`, `your workspace`, `hang your`, `it will hang`).
   *"it will hang your turn"* describes the harness, never the deliverable.

**Why this fixes it:** both of gym-010's matches fail one of the two tests — `crashes` is negated by
*"never"*; `hang` is a second-person process warning. With either rejection in place the REPRO
clause does not fire, and Cause A's downstream (the manufactured `BEFORE: FAIL`) disappears with it.

**Assertions:** gym-010's goal text yields **no** runtime-symptom match; a genuine report
(*"the editor crashes when I click the toolbar"*) still matches; *"must not crash"* does not;
*"it will hang your turn"* does not.

---

### P12.2 — A signature line must look like tool output

**Cause:** B (`'`, `\`, `/` treated as evidence of machine output).

**Change:** drop the bare `'` / `\` / `/` triggers. Require a positive signal:

- an error keyword **plus** a path-or-identifier token (`foo/bar.py`, `Foo.Bar`, `*.sln`), **or**
- a `file:line[:col]` pattern, **or**
- a stack-frame marker (`at `, `File "…", line`, `  at Namespace.Type`), **or**
- a compiler/tool code (`CS1234`, `error CS`, `error:`, `E\d+`), **or**
- a quoted symbol adjacent to an error word.

**Why this fixes it:** every one of the 8 false signatures is prose whose only qualification was a
slash or apostrophe. None carries an error keyword, a file:line, a stack frame, or a tool code —
so all 8 stop qualifying, `lines` comes back empty, and `_attempt_history` returns `""` at its own
early guard (`if not lines: return ""`), regardless of what other efforts contain.

**Assertions:** the gym-010 scenario yields **zero** signature lines; a real pasted build error
(`Program.cs(42,17): error CS0246: type not found`) still yields one; `Beyond add/list/done,` does
not.

---

### P12.3 — Prior attempts must be REACHABLE and RELEVANT

**Cause:** C (absence qualifies) and D (lifecycle ignored).

**Change:**
1. **Skip an effort whose branch does not exist on the remote.** Absence is not evidence of an
   attempt worth reading — it is evidence there is nothing to read. Where the code currently
   substitutes *"never reached"* wording, it should instead **drop the entry**.
2. **Skip `aborted` efforts.** An aborted effort was withdrawn, not attempted-and-failed. `done`
   efforts stay eligible — a delivered-but-unmerged attempt is exactly the case this block exists
   for.
3. If nothing survives both filters, emit **no block at all** rather than a header with an empty
   list.

**Why this fixes it:** gym-010's three entries were `gym-008` (branch deleted), `gym-004d` (branch
deleted **and** aborted), `gym-007` (branch deleted). All three fail filter 1; two also fail filter
2. The block would not have been emitted, and a wipe can no longer re-arm it.

**Assertions:** an effort whose branch was deleted is not listed; an `aborted` effort is not listed;
a `done` effort with a live unmerged branch **is** listed; zero survivors ⇒ empty string.

**Note:** P12.3 is what makes the greenfield wipe safe to repeat. Without it, every reset increases
the number of efforts that read as failed attempts.

---

### P12.4 — Carry the intent instead of re-deriving it

**Cause:** the structural one beneath A and B — **the org guesses the request's kind from prose when
the caller already knows it.** `scenario.yaml` declares `axis.task: feature-add`; the gym runner
sends only `goal.message` through `POST /nl`; the org then reconstructs "is this a bug report?" by
regex.

**Change:** let a request carry an explicit intent (`build` | `fix` | `investigate`), set by the
caller when known, inferred only as a fallback. `fix` enables the REPRO clause and attempt-history;
`build` disables both.

**Why this fixes it:** it removes the guess from the path entirely for callers that know the answer.
P12.1–P12.3 make the guess *better*; P12.4 makes it *unnecessary* where the information already
exists. Regex hardening is a rearguard action — prose will always find a new way to look like a
stack trace.

**Scope note:** larger than the others (touches intake, the NL schema, and the gym runner). Ship
P12.1–P12.3 first, measure gym-010 attempt 2, then do this as the durable fix.

---

## Traps

1. **Do not weaken genuine error-report handling.** These paths exist because real re-reported build
   errors were being re-derived blind (live 2026-07-05). Every assertion above pairs a
   false-positive test with a **true-positive** test; keep both.
2. **`_attempt_history`'s early guard is the cheap win.** `if not lines: return ""` — fixing `_sig`
   (P12.2) short-circuits the whole function without touching its matching logic.
3. **Config defaults OFF** where a flag is introduced (the unit suite counts worker wakes).
4. **Byte-identical gym goals are deliberate** — they make rounds comparable. The fix belongs in the
   classifier, never in de-duplicating the scenarios.
5. **Do not "fix" this by closing efforts.** Cause D shows lifecycle is not consulted; that change
   is a no-op until P12.3 lands.
6. **Never commit or push on the operator's behalf unless asked.**

---

## Definition of done

1. The gym-010 goal text produces **no** runtime-symptom match and **no** `_REPRO_CLAUSE`.
2. The gym-010 goal text produces **zero** signature lines and **no** `PRIOR ATTEMPTS` block.
3. A genuine pasted build error still produces both, with its prior attempts listed.
4. A deleted-branch or aborted effort is never offered as a prior attempt.
5. Full unit suite green.

## Validation

Re-run scenario-010 on the reset arena and read the stored objective for the new effort. Success is
a goal that contains the scenario text plus standing intent, acceptance corpus and the check note —
**and nothing about symptoms, reproduction, or prior attempts.** Then measure E1 and E5, which
gym-010 attempt 1 never reached.
