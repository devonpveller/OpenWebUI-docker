# u8floor findings — the done-authority's phase floor, and what A3 cost one reader over

Branch `work/u8floor`. Subject: `scripts/checks/dfu-done.ps1` (the §C.8 done-authority)
and its drill `scripts/checks/verify-dfu-done.ps1`. Nothing in this branch edits
`PLAN.md` or `DECISIONS.md`.

Round 1 landed as `392d170`: U8 pinned into the phase floor, and section 2.1 amendment
A3 — the phase id cell is matched WHOLE instead of by prefix. Round 2 is this file plus
the commit that follows it.

---

## 1. A3 closed a loud refusal and opened a silent drop, one reader over

**What A3 changed.** Before A3, a table carrying `| **U4** |` and
`| **U4 status (2026-08-30)** |` was read as TWO U4 rows; the duplicate guard refused,
and U4's chain was unreconstructable across that revision. A3 makes the annotation a
declined row: it is ignored, recorded in `$Parse.ignored`, and printed.

**Where it broke.** `$Parse.ignored` had exactly ONE consumer in 4,600 lines —
`Add-PhaseFloorProbes` (`dfu-done.ps1:910`) — and that consumer only ever sees the
CURRENT `PLAN.md`. Clause 2's per-revision parses kept `$rp.problems` and dropped
`$rp.ignored`, and the chain builder is `if (-not $t.Contains($id)) { continue }` — no
probe, no note. So in the HISTORICAL reader a revision whose floor-phase id cell carried
trailing text became **silently skipped**.

That is exactly the failure clause 2's own comment names: *"the chain would start after
its own beginning, and every comparison after that is against the wrong text."*

**Proved, not argued — and reproduced here, not relayed.** One fixture repository, three
script revisions, `-Only 2 -SkipLive`. rev1's id cell reads `| **U4 (runner) unification** |`
and its column states *"the quadrant comparison is required; the oracle fires once"*; rev2
is a clean `| **U4** |` whose column has dropped the quadrant requirement, with no
disposition anywhere:

| script | `chain-U4-original-vs-current` | chain detail | clause 2 |
|---|---|---|---|
| pre-A3 `1a6b0b8` | **`[fail]`** — "the quadrant comparison is required :: it is absent from the CURRENT column - and no disposition is recorded" | "2 distinct state(s)" / "U4 ABSENT … [NO DISPOSITION]" | `unmet` |
| round 1 `392d170` | `[pass]` — "1 of 1 ORIGINAL requirement(s) survive VERBATIM" | "**1** distinct state(s)" / "U4 CARRIED" | `unevaluated` |
| round 2 (this) | `[pass]` (unchanged) + **`[fail] chain-U4-declined-rows`** — "U4's chain skips 1 revision(s) where the ONLY cell naming it was declined as an id cell … THE STEP IS LOST" | the declined cell is printed by name | `unmet` |

The middle row is the defect in one line: the original moved forward one revision, the
dropped requirement became "CARRIED", and clause 2 stopped reporting a **definite
failure** at all. This is drilled as step **R2**.

A second shape, same mechanism: an intermediate revision whose column said "the quadrant
comparison is NOT required" is a printed chain link before A3 and silently absent after.

**The fix, per rule 9 (NORMALISE IN EVERY READER, THEN GREP FOR THE SHAPE):**

1. The historical reader now captures `ignored` per revision, attributed to the phase the
   cell names (`dfu-done.ps1`, clause 2's revision loop).
2. It distinguishes two facts that are not the same fact:
   - the phase **also** has a real id-cell row at that revision → the parse still answered
     for the phase, the chain step is intact. **Reported** on
     `chain-<id>-declined-rows` (pass) and printed in the chain detail. This is the live
     `2151193` case, and it is the greening A3 authorised; failing it would be reverting A3.
   - the phase has **no** real row at that revision → the revision is skipped for that
     phase, the ORIGINAL moves forward. **`chain-<id>-declined-rows` FAILS**, in the same
     words this clause uses for a hole.
3. A declined row naming a phase this clause does not iterate is still printed, on
   `chain-revisions-declined-rows` — reported, not failed, because it is not a step of any
   chain being reconstructed.
4. **The shape grep** — `verify-dfu-done.ps1` step **R1** reads `dfu-done.ps1` and asserts
   that EVERY call site of `Get-PhaseTableParse` surfaces `ignored` within its own function
   body (either by handing the parse to `Add-PhaseFloorProbes -Parse`, or by reading
   `.ignored` directly). It also asserts the grep matched at least three call sites, so a
   grep that silently stops matching cannot pass by finding nothing.
5. **`Get-PhaseTable` is removed.** It was a five-line wrapper returning
   `(Get-PhaseTableParse -Text $Text).phases`, discarding both `problems` and `ignored`,
   with **zero callers repo-wide**. It is precisely the "third reader added later" this
   round is about: a convenience door into the parser that drops the refusal on the way
   through. Nothing replaces it — every reader calls `Get-PhaseTableParse` and hands the
   result to a surfacer, and R1 greps for that.

Behavioural drills: **R2** (PROOF A must fail), **R2b** (the annotation-beside-a-real-row
case must still pass — the fix for R2 may not take A3's greening back).

---

## 2. "Ignored, never silent" was false for the cells most likely to matter

`dfu-done.ps1:780` recorded a declined row only when the cell matched
`'^\s*(?:\*\*|__)?\s*U\d\b'`. **`\b` does not fire between a digit and a following word
character.** So `**U4b**` and `**U40**` matched **neither** the id shape **nor** the ignore
shape: they were not read as phase rows and were not listed as ignored either. They
vanished from the output entirely — a silent drop wearing the costume of a safe ignore, in
the very branch whose comment claims nothing there is silent. Measured on the two regexes
themselves (the table under **Validation** below): `**U4b**` and `**U40**` return False for
the id shape and False for the old ignore shape.

Fixed by making the recogniser the bare prefix `U<digit>`: a cell that starts like a phase
id is either a ROW or a NAMED IGNORE, never neither. Drilled at **R3**.

**The same `\b` was in the pre-header alarm** (the check for a phase-looking row ABOVE
section 2's table header), which is documented as deliberately the widest net in the
parser. It had the identical hole — a stray `**U4b**` row above the header tripped nothing.
Fixed the same way, and the alarm now quotes the WHOLE cell rather than a truncated id.

---

## 3. RESIDUAL, STATED: moving one space defeats A3, and the allowance is now visible

A3's literal specification (`PLAN.md:67-70`) says match the id cell exactly `**U<n>**`.
The implementation admits **one parenthesised qualifier**, because the live table writes
U7 as `**U7 (standing)**` and refusing that shape would delete U7 from clauses 2 and 7's
populations.

**The residual is real and it was measured here.** Running `dfu-done.ps1 -Only 7 -SkipLive`
against the live `PLAN.md` with U4's id cell rewritten to `**U4 (status 2026-08-30)**` (the
only U4 row) and its column to `echo weak`: BOTH `phase-table-unambiguous` AND
`phase-floor-present` PASS. So `**U4 status (…)**` is caught and `**U4 (status …)**` is
not, one space apart, for a rule whose entire point is that status must not live in the
anchor.

This branch cannot close it: narrowing the allowance deletes U7, and this branch may not
edit `PLAN.md`. So the allowance is made **visible** instead of quiet — every admitted
qualifier is recorded with its phase and printed on `phase-table-unambiguous`
("… id cell(s) were ADMITTED carrying a parenthesised qualifier …"), so an id cell carrying
STATUS is NAMED in the output even when it is accepted. Drilled at **R4**, which also
asserts the positive control (`**U6 (standing)**` still parses).

**Open, for whoever owns §2.1:** either A3's text should say a parenthesised qualifier is
admitted (making the implementation match the spec), or U7's row should be renamed so the
allowance can be dropped. Until one of those happens, `**U4 (status …)**` in section 2 will
parse as U4 — loudly now, but it will parse.

---

## 4. ERRATUM — the round-1 commit message asserts the opposite of what happened

`392d170`'s message opens: *"Two changes to the done-authority, both of which make it
report MORE, never less."* That claim is **wrong**, and it is wrong in the direction that
flatters the change.

Measured against one tree, both script revisions:

Both script revisions were run `-Only 2 -SkipLive -Json` against ONE clean clone at
`392d170` (`git status --porcelain` empty). Clause 2:

| script revision | probes | FAILING | coverage | the failing probes |
|---|---|---|---|---|
| pre-A3 `1a6b0b8` | 18 | **4** | 10 of 10 | `no-outstanding-parked`, `amendment-A3-accounted`, `chain-revisions-parsable`, `chain-U4-has-a-hole` |
| round 1 `392d170` | 16 | **3** | 9 of 10 | `no-outstanding-parked`, `amendment-A3-accounted`, `phase-floor-matches-plan` |
| round 2 (this branch) | 17 | **3** | 9 of 10 | the same three |

Two failing probes **ceased to exist** — `chain-revisions-parsable` and
`chain-U4-has-a-hole` are in the pre-A3 probe set and in no later one. One probe that
had been *passing* flipped to failing: `phase-floor-matches-plan`, from the U8 pin. Net
4 -> 3.

**The count in the brief was "5 failing probes to 3"; measured here it is 4 to 3.** The
two probes the brief names as lost are exactly the two that were lost, so the finding is
confirmed; the arithmetic is recorded as measured rather than as relayed.

Round 2 adds one probe to clause 2 and changes no verdict. The added probe is
`chain-U4-declined-rows`, and on the real history it **passes**, printing the live case
by name:

> `[pass] chain-U4-declined-rows` — "1 row(s) naming U4 were declined as id cells in the
> history, and U4 has a real row at every one of those revisions, so no chain step was
> lost: 2151193: '**U4 status (2026-08-30)**' [the phase also has a real id-cell row at
> this revision - the step is intact]"

That is the intended shape: the thing A3 made quiet is now said out loud, and it is not
turned into a false red.

The census bucket did not move — which is all the commit message checked, and a bucket that
does not move is not evidence that nothing got greener.

**The greening is AUTHORISED and is NOT reverted here.** Making a duplicate-looking
annotation stop refusing is A3's stated purpose; that is why `chain-revisions-parsable` and
`chain-U4-has-a-hole` went away. What was wrong was the claim, not the change. This entry
is the correction. `392d170` is not amended: two independent verifiers cite that sha in
their round-1 results, and rewriting it would invalidate their citations — so the erratum
lives here and in the round-2 commit message, next to it in `git log`.

What stayed red across the change: `phase-floor-matches-plan` fails on every clause that
carries it (the pinned floor names U8, C.8 clause 1's prose still reads "For U0-U6"), and
the board stays FAILED.

---

## 5. The CI edit changes the behaviour of ZERO pushes this effort will make

`17ac431` corrected `.github/workflows/ci.yml`'s push filter from `develop` (a branch that
does not exist) to `development`. Recorded plainly, because it is a **dependency, not a
completed step**:

- `origin/development`'s `ci.yml` is blob `e9ff281aef1ce7ef5f2b16df610160ef668c4cad` and
  still reads `branches: [main, develop, "feature/**", "refactor/**", "update/**"]`.
  The corrected blob is `1d4013acfed379666a96595790c66dda1944e165`, and it exists only on
  this work branch.
- GitHub resolves a push workflow from the ref being **pushed**. The work line is
  `refactor/ai-stack-cleanup`, which already matched `refactor/**` **before** this commit.
  So the edit changes nothing about any push this effort makes.
- Therefore **H4's "shown green on a CI run" is blocked on an operator promotion of the
  work line to `development`** (or on a push that opens a PR — `pull_request:` carries no
  branch filter, so any PR runs CI; but that too requires a push). §C.8 clause 4 puts that
  out of this effort's scope, and this branch does not push.
- `work/**` is deliberately **not** added to the filter. That is a separate decision and it
  belongs to H4.

---

## 6. `Get-AuditedFingerprint` makes a recurring FALSE accusation under the
   worktree-per-session policy

`Get-AuditedFingerprint` (`dfu-done.ps1:1435-1439`) fingerprints, among the audited
artifacts, two **repo-wide** git probes:

```
git for-each-ref --format=... refs/heads refs/tags
git worktree list --porcelain
```

A CONCURRENT session in a sibling worktree — creating a branch, committing, adding or
removing a worktree — moves both hashes. The consequences:

- the whole-run check at `:4535` reports `INTEGRITY: FAILED - this run CHANGED the world it
  was measuring`, and
- the per-command check at `:1706` names a specific command:
  `clause N / U<n>: '<command>' MOVED the audited tree (git:refs)` — **an innocent
  command, blamed for a neighbour's commit.**

Under CLAUDE.md's worktree-per-session policy (twelve worktrees were live on this repo
while this branch was being written) that is not a rare race; it is a recurring false
accusation, and a false accusation in an integrity report is exactly the kind of noise that
teaches a reader to ignore the report.

**Proposed fix — attribution, not leniency.** Nothing below relaxes the veto:

1. Split the fingerprint into `self` (the three documents, `documentation/notes`, and this
   worktree's own `git status --porcelain` and `HEAD`) and `repo` (`git:refs`,
   `git:worktrees`, `git:submodule`). Both still veto. The split is what lets the report
   say WHICH world moved.
2. Change the per-command blame line so it never asserts causation it cannot show: for a
   `repo`-class key, say the repository-wide refs moved during this command's window and
   that a concurrent worktree can move them without this run touching anything — instead of
   "'<command>' MOVED the audited tree".
3. When zero commands executed (e.g. `-SkipLive`) and only `repo`-class keys moved, state
   that plainly: this run executed 0 commands and cannot have caused it. Still not `ok`,
   still no green — the operator simply gets a true sentence instead of a false one.

Not implemented on this branch; it is a change to the integrity reporter, which is not this
item's subject.

---

## Validation

Run in the worktree `.claude/worktrees/wt-u8floor` at base `1a6b0b8` — 0 behind the work
line (`git rev-list --left-right --count refactor/ai-stack-cleanup...HEAD` = `0  2`), so
no C.7b staleness applies.

- **PowerShell AST parse** of `dfu-done.ps1` and `verify-dfu-done.ps1`: OK.
- **`scripts/checks/verify-dfu-done.ps1` — DRILL GREEN, 216 assertions, 0 failed**, 8 of
  8 declared clauses have a constructed failing case. (Round 1: 204 assertions.) New
  steps R1, R2, R2b, R3, R4; clause 2's census entry is now discharged by R2.
- **R1 has a repro, not just a pass.** Run against `392d170`'s `dfu-done.ps1` the shape
  grep reports **two UNSURFACED call sites** — `:813` (inside `Get-PhaseTable`) and
  `:2244` (clause 2's revision loop) — and the wrapper present. Against this tree: 4 call
  sites, all surfaced, wrapper count 0.
- **The `\b` claim was measured on the regexes themselves, not inferred:**

  | id cell | matches the id shape | old ignore (`\b`) | new ignore |
  |---|---|---|---|
  | `**U4**` | True | True | True |
  | `**U7 (standing)**` | True | True | True |
  | `**U4b**` | False | **False** | True |
  | `**U40**` | False | **False** | True |
  | `**U4 status (2026-08-30)**` | False | True | True |
  | `**U4 (status 2026-08-30)**` | **True** | True | True |
  | `**U4 (runner) unification**` | False | True | True |

  Rows 3 and 4 are item 2: neither a row nor an ignore. Row 6 is item 3's residual: the
  qualifier form is read as U4's row.
- **Item 3 end to end.** `dfu-done.ps1 -Only 7 -SkipLive -PlanPath <live PLAN.md with
  U4's id cell rewritten to `**U4 (status 2026-08-30)**` and its column to `echo weak`>`:
  `[pass] phase-floor-present` and `[pass] phase-table-unambiguous` — the residual is
  real — and the note now reads "2 id cell(s) were ADMITTED carrying a parenthesised
  qualifier ... U4: `**U4 (status 2026-08-30)**` / U7: `**U7 (standing)**`".
- **Item 5** verified directly: `git rev-parse origin/development:.github/workflows/ci.yml`
  = `e9ff281aef1ce7ef5f2b16df610160ef668c4cad`, whose line 10 still reads `develop`.
- Item 4 measurement: the table in section 4 above.

### Clean-checkout validation (§C.7b)

<!-- CLEANCHECKOUT -->
