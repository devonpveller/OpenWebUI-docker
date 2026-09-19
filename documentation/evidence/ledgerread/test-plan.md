# Test plan - `ledgerread`

Branch `work/ledgerread`, worktree `wt-ledgerread`, base `refactor/ai-stack-cleanup`
(`14c3d14` at branch time).

Changed: `scripts/checks/check-hook-attestation.ps1` (the ledger PARSE and the report
block), a pointer paragraph in `.githooks/README.md`, plus
`documentation/notes/hook-attestation-identity.md` (findings sink),
`documentation/evidence/ledgerread/fixture-four-shapes.ps1` (the construction proof) and
this file. **`.githooks/pre-commit` is UNCHANGED** - confirm that first, it is the item's
main out-of-scope boundary.

**You are testing whether the checker stops rendering four different things as one, and
whether saying so broke the verdict.** No containers, no images, no leases - this is git
and PowerShell only, and every case is seconds.

## READ THIS FIRST: what a WRONG fix looks like here

The naive repair is a symmetric guard - "column 4 is not 40 hex, therefore the line
predates the column". It is wrong in two directions and **both are worse than the odd
output it replaces**, because the reader came for an answer and will accept the one they
are given:

- `?` is the hook's **own deliberate degradation sentinel** (`.githooks/pre-commit` step 6
  writes it when `git hash-object "$0"` fails). It means the hook RAN and could not hash
  itself - the opposite of "no hook identity was recorded".
- Four **real** ledger lines (124/125/156/157, 2026-08-30) carry a **branch name** in
  column 4, from the reverted commit-msg attester `bd4d891` whose shape was
  `<tree> <msg-hash> <ts> <branch>`. They are genuine attestations.

**The item FAILS if (c) or (d) is reported as (a).** Case 1 is the case that catches it.

## Environment

    $CHK = "<worktree>\scripts\checks\check-hook-attestation.ps1"
    $FIX = "<worktree>\documentation\evidence\ledgerread\fixture-four-shapes.ps1"

`fixture-four-shapes.ps1` builds a scratch repo under `$env:TEMP` and never opens the
ai-stack repository or its real ledger. Its own header says what it builds and why. Nothing
in cases 1-3 can touch the real `.git/hook-attest.log`.

---

## Case 1 - FOUR states, four distinct renderings, run not reasoned

    & $FIX -Script $CHK

The fixture prints the crafted ledger it wrote, so you can see the input beside the output.
It has one commit per state plus a real `--no-ff` merge.

PASS requires **all four** of these in the `gated by 4 distinct hook file(s)` list, and no
two of them the same string:

    (not recorded - predates the hook-identity column)
    ? (hook could not hash itself - the hook's own degradation sentinel; this line still attests)
    519ad9f6cefd714b7f15339d9c1aeefacda0b10a
    MALFORMED: 'work/u5proxy' (column 4 is not a hook hash; this line still attests, its hook identity is unreadable)

and exit **0**.

FAIL if `?` or `work/u5proxy` renders as the "(not recorded - predates...)" string; that is
the regression the item exists to prevent. FAIL if the malformed value is described rather
than **quoted verbatim** - the offending text must be visible.

## Case 2 - the `?` regression specifically, and the A/B that shows the defect was real

Confirm case 1's `?` line by construction, then run the SAME fixture against the base
version of the script, so you see what it used to print:

    git show 14c3d14:scripts/checks/check-hook-attestation.ps1 > $env:TEMP\chk-base.ps1
    & $FIX -Script $env:TEMP\chk-base.ps1

PASS: the base run prints, under the identical heading `gated by 4 distinct hook file(s)`,
the four values **bare** -

    (not recorded - ledger line predates hook identity)
    ?
    519ad9f6cefd714b7f15339d9c1aeefacda0b10a
    work/u5proxy

That is the defect: `?` and a branch name listed as though they were hook files. Both runs
exit 0, which is the point - **the bug was never in the verdict, only in the rendering**, so
a test that only checks exit codes proves nothing here.

## Case 3 - THE VERDICT PATH MUST NOT MOVE

    & $FIX -Script $CHK -AddUnattested

This adds one commit with no ledger line, leaving the other four states untouched.

PASS: exit **1**, and **exactly one** commit under `[UNATTESTED]` - `commit e-unattested.txt`.

FAIL if the malformed line's commit (`c-branchname.txt`), the sentinel's
(`b-question.txt`), or the three-column line's (`d-threecol.txt`) appears there. Each of
those is a genuine attestation and must keep setting `attested[tree]`; if the change made
any of them stop attesting, the item exceeded its anchor and this is where it shows.

Then the JSON shape `queue.ps1 -Submit` consumes:

    & $FIX -Script $CHK -Json

PASS: valid JSON, `unattested` empty, `gatedBy` present with the rendered `hooks` strings
and a new `isMerge` field. FAIL if `ConvertFrom-Json` chokes.

## Case 4 - the MERGE clause, against a REAL merge and not a fixture

The clause is a point-of-output requirement. Run the checker across the real merge
`9684bc4` (`Merge work/attestid: ...`), read from the REAL ledger:

    & powershell -NoProfile -ExecutionPolicy Bypass -File $CHK `
        -Branch 9684bc459613c5ece2fa974d6f08fd3c5636753c `
        -Base   6dc5e210b6f716e7e1b95a2ab45bc95703e6eaa6 `
        -RepoRoot "D:\Open WebUI\ai-stack"

PASS: exit 0, and the report block contains a per-merge hash line

    9684bc45 (merge)  519ad9f6cefd714b7f15339d9c1aeefacda0b10a

together with the explanation that a merge's hash names `pre-commit`, not
`pre-merge-commit`, because `pre-merge-commit` `exec`s `pre-commit` and `exec` replaces the
process. Confirm the recorded hash really is `pre-commit`'s and really is not
`pre-merge-commit`'s:

    git rev-parse 9684bc4:.githooks/pre-commit         # -> 519ad9f6cefd714b7f15339d9c1aeefacda0b10a
    git rev-parse 9684bc4:.githooks/pre-merge-commit   # -> 66588bfbf7760a764af9df5c008cf99da22c0b56

FAIL if the explanation appears only in `.githooks/README.md`. The README paragraph is a
**pointer** and must read as one; the answer has to reach a reader who is looking at the
merge line, because by the time they go looking for a reference they have already formed
the false belief. Check `.githooks/README.md`'s "Which hook gated this tree?" section says
the checker's output is where the four states and the merge fact live, and does not
restate them.

## Case 5 - same block, one read

In case 1's and case 4's output, confirm the malformed note, the `?` note and the merge
note print **inside** the `gated by ...` block, above `[OK] ...`, and not as a separate
warning stream elsewhere in the output.

## Case 6 - the guard still passes on this branch, and `-Submit` is unchanged

    & powershell -NoProfile -ExecutionPolicy Bypass -File $CHK -Branch work/ledgerread -Base refactor/ai-stack-cleanup -RepoRoot <worktree>

PASS: exit **0**, `[OK] every commit's tree was validated`. This exercises the REAL ledger
with no override.

    scripts\agent-harness\queue.ps1 -List

PASS: `ledgerread` is `ready-to-test`. The `-Submit` that put it there ran the checker
through its JSON path; if the JSON shape had broken, `-Submit` would have died at gate 4.

## Case 7 - the artifact is well-formed, and the write side did NOT move

    git diff refactor/ai-stack-cleanup..work/ledgerread --stat

PASS: `.githooks/pre-commit` is **not** in the list. Tokenize both changed/added `.ps1`
files with `[System.Management.Automation.PSParser]::Tokenize` and require zero errors.
Confirm `scripts/checks/check-hook-attestation.ps1` is ASCII, no BOM (PS 5.1 convention).

## Case 8 - the override still needs its flag

    $env:AI_STACK_ATTEST_LEDGER = "C:\nonexistent\fake.log"
    & powershell -NoProfile -ExecutionPolicy Bypass -File $CHK -Branch work/ledgerread -Base refactor/ai-stack-cleanup -RepoRoot <worktree>
    Remove-Item Env:\AI_STACK_ATTEST_LEDGER

PASS: the `ledger:` line still names the REAL ledger under the shared git dir. FAIL if the
env var alone redirected it - that would restore the one-line off switch the script removed
on purpose. (Unchanged by this item; verified because it sits two lines from the edit.)

## Case 9 - KNOWN RED, do not attribute it to this change

`scripts/agent-harness/verify-merge-protocol.ps1` was reported at **60/66** on this line by
item `attestid`, six failures cascading from "two divergent commits exist", diagnosed in
`documentation/notes/hook-attestation-identity.md` section 8 (the drill cuts worktrees from
`development`, which lacks check 3b's script, while commits inside them run the main
checkout's newer hook that calls it). I did not re-run it: this item changes only what the
attestation checker PRINTS, and the drill's failure is at commit time in a different script.
If you do run it and want to hold me to that, the A/B is two runs and one
`git checkout refactor/ai-stack-cleanup -- scripts/checks/check-hook-attestation.ps1`.

## What a FAIL looks like overall

- `?` or a branch name rendered as "(not recorded - predates the hook-identity column)".
- A malformed value summarised instead of quoted verbatim.
- Any of the three odd states no longer attesting (case 3 lists more than one commit).
- The merge fact reachable only from `.githooks/README.md`.
- `.githooks/pre-commit` in the diff.
- Evidence that reads correctly but names no command that was run - three patches on this
  line of work already passed that bar and did nothing.
