# Findings sink - hook attestation identity (item `attestid`, 2026-09-04)

Things established while adding the hook-identity column to `.githooks/pre-commit`
step 6 that are TRUE but OUTSIDE that item's scope. Nothing here was changed.

Each entry says what was checked and how.

## 1. A hook hash is only readable back if that hook state was ever committed

`git hash-object` **does not write** the object (no `-w`), so the fourth ledger column
can name a blob that is not in the object database. Checked: hashing a scratch copy of
the hook and then `git cat-file -p` on the result - the hash is produced, the read
fails.

This is the honest behaviour and the README says so: an unreadable hook hash means the
gating hook was an uncommitted local edit, which is a *stronger* signal than a readable
one. Adding `-w` would make every commit write a loose blob that nothing references,
which `git gc` prunes after two weeks anyway - so it would buy a guarantee it cannot
keep. Left alone deliberately.

## 2. A clean merge records the hash of `pre-commit`, not of `pre-merge-commit`

`.githooks/pre-merge-commit` is `exec "$(dirname "$0")/pre-commit"`, and `exec` replaces
the process, so `$0` inside the running hook is the **pre-commit** path. A clean merge
therefore attests the identity of the file that did the checking, which is the useful
answer today, because `pre-merge-commit` contains no checks of its own.

It stops being the useful answer the moment `pre-merge-commit` grows logic that
`pre-commit` does not have: the ledger would then be unable to distinguish "gated by
pre-commit" from "gated by pre-merge-commit's own additional rules". Low priority - it
is a latent gap, not a live one.

## 3. The recorded hash is of the NORMALIZED content, not the literal bytes executed

`git hash-object` applies the end-of-line filter. Checked directly: a two-line CRLF file
and its LF twin hash identically on this machine. That is what makes the column useful -
the recorded hash equals the blob hash the same content has inside a commit, so
`git cat-file -p` works - but it means a hook file sitting on disk with CRLF would be
recorded under its LF identity, and the bytes `sh` actually executed are not what the
hash names.

For this repo the distinction cannot bite: `.gitattributes` pins `.githooks/* text
eol=lf`, and a CRLF hook does not run under Git Bash at all (`$'\r': command not
found`), so there is no scenario where a CRLF hook silently gates a commit. Noted
because the property is not obvious from reading the hook.

## 4. The ledger still cannot answer for the past, and should not be made to

Every commit before 2026-09-04 has a three-column ledger line, or none. Retroactive
attribution was explicitly out of scope for this item and should stay out: the only
inputs available are reflog and commit dates, and reflog is local, prunable and
per-clone. The reconstruction that motivated this item was possible **once**, by a
reviewer with an hour, on one machine.

## 5. `check-hook-attestation.ps1` reports the gating hook; it does not judge it

The verdict is still attested-vs-not. Making it *enforce* a hook - "every commit on this
branch must be gated by a hook containing check 5b", or "by the hook at the base tip" -
is a real and possibly desirable next step, and it is a separate decision: it would fail
honest branches cut before a check landed, which is the cry-wolf failure mode the script
already warns about twice in its own header. Not attempted here.

## 6. Unrelated, observed in passing: `commit-msg` rejects SHA-shaped tokens naming trees

`.githooks/commit-msg` tests every hex token with `^{commit}`, so a message citing a real
**tree** object is refused as "a commit that does not exist". Seen while another item was
landing; already filed low-priority by that item; not touched here. Recorded so the two
sightings are connected rather than rediscovered a third time.

## 7. The root cause this item routes around is `core.hooksPath` being absolute

A worktree's own edited `.githooks/pre-commit` is not what runs for that worktree's
commits, which is why every probe for this item had to pass `-c core.hooksPath=.githooks`
explicitly and say which of the two hooks it was exercising. The anchor rules a fix out
of scope, correctly - it changes how every agent in this repo commits and needs its own
decision. The new column at least makes the confusion *visible* after the fact instead of
requiring reflog archaeology to notice.

## 8. `verify-merge-protocol.ps1` is RED on this line, for the same root cause

Measured 2026-09-04 on `work/attestid` (base `af974d1`): **60/66**, six failures, all
cascading from one. Established as PRE-EXISTING by an A/B run - the identical 60/66 with
this item's `check-hook-attestation.ps1` replaced by the base version, so the change did
not cause it.

The first failure is `two divergent commits exist`, and the drill's own output says why:

```
=== 2. both edit THE SAME file with conflicting intent, and commit ===
The argument './scripts/checks/check-corpus-exposure-producers.ps1' to the -File
parameter does not exist.
Pre-commit validation failed (a corpus insert does not state its plane)!
```

The drill cuts `drill/verify-d` from **`development`**, and `development` has neither
check 3b's script nor the hook line that calls it:

```
git cat-file -e development:scripts/checks/check-corpus-exposure-producers.ps1  -> absent
git show development:.githooks/pre-commit | grep -c check-corpus-exposure-producers -> 0
```

But the commit inside that worktree runs the hook at **`core.hooksPath`**, which is an
absolute path to the main checkout - currently on `refactor/ai-stack-cleanup`, which
*does* have 3b. So the newer hook runs against the older tree, calls a script that is not
there, and refuses both of the drill's commits. Every later failure follows from those two
commits never existing.

This is finding 7 with teeth: a hook is paired with a checkout, not with the tree it is
checking, and the two can be from different lines. Note that the failure mode is
LOUD here only by luck - `powershell.exe -File <missing>` exits non-zero, so it read as
"the check failed". A check that exited 0 when its script was missing would have read as
"the check passed", on a tree it never looked at.

Not fixed here: it is neither this item's artifact nor a change this item is allowed to
make (the anchor rules out touching the checks, and relativising `core.hooksPath` needs
its own decision). Filed so the next person to run the drill knows the red is not theirs.

---

# Added by item `ledgerread` (2026-09-05)

`ledgerread` gave the READ side of the ledger a four-way discrimination on column 4.
These were established while doing it, are true, and are outside that item's scope
(which was rendering only). Nothing below was changed.

## 9. What column 4 actually holds, counted rather than assumed

**The durable fact here is the set of SHAPES, not any count: all four of column 4's
states are present in the live ledger.** That is what the checker's old rendering
flattened, and it is why this item's fixture could be built out of real material instead
of imagined values.

The numbers below are a snapshot, and saying so is part of the finding. The ledger is
append-only - `.githooks/pre-commit` step 6 appends on every hook run and never rewrites
- so the totals climb every working day. Four different counts were taken on 2026-09-05
alone (543, 545, 547 and 549 lines, hours apart) and none of them was wrong. **A later
reader who counts a different number has not found a discrepancy; they have found a
ledger that grew.** What does *not* move: the line numbers cited below and in section 10
(new lines land at the end), the 523 three-column lines (step 6's `printf` emits four
fields unconditionally, so nothing writes a three-column line any more), and the two
non-hex shapes. Only the hex count grows.

As of **2026-09-05T18:40:27Z** the ledger at `.git/hook-attest.log` is **549 lines**:

```
awk '{print NF}' .git/hook-attest.log | sort | uniq -c
    523 3          <- three-column, predating the column
     26 4
```

Of those 26 four-column lines, **20** carry a 40-hex hook hash, **4** carry a branch name
(`work/u5proxy`, lines 124/125/156/157, 2026-08-30, from the reverted commit-msg attester
`bd4d891` whose shape was `<tree> <msg-hash> <ts> <branch>`), and **2** carry the literal
`?` (lines 509/533, `work/attestid`, 2026-09-05). Recount the breakdown with:

```
awk 'NF==4 {print $4}' .git/hook-attest.log | sort | uniq -c
```

## 10. A `?` line today is NOT evidence that any commit was gated by an unnameable hook

Both `?` trees also carry a **hex** line earlier in the same session - 103 seconds and
7m19s earlier respectively (`sed -n '508p;509p;525p;533p' .git/hook-attest.log`; trees
abbreviated below for width, everything else verbatim):

```
508: d5b9e56f... 2026-09-05T02:24:24Z work/attestid 6d40f4800ae58f3edebd3d61c5141235ac19950e
509: d5b9e56f... 2026-09-05T02:26:07Z work/attestid ?
525: d721a421... 2026-09-05T02:42:03Z work/attestid 6d40f4800ae58f3edebd3d61c5141235ac19950e
533: d721a421... 2026-09-05T02:49:22Z work/attestid ?
```

`d5b9e56f` is the tree of commit `787dbff` ("notes(attestid): what the hook-identity column
cannot answer"), authored 2026-09-04 22:24:12 -0400, which is 2026-09-05T**02:24:12**Z -
the sign matters, and applying it the wrong way would put the commit twelve hours from its
own attestation. The hex line 12s later at 02:24:24Z is that commit's real attestation, and
the 12s gap is what a real commit looks like rather than an anomaly: git stamps the commit
date when `git commit` STARTS, before the hook returns.

The `?` line 103 seconds after it is `attestid`'s own test-plan **case 7**, which sources
the hook with a bogus `$0` (`sh -c '. "$PWD/.githooks/pre-commit"'
"/no/such/hook/pre-commit"`) and therefore writes a line **without making a commit at all**.
`d721a421` fits the same reading: it is the tree of no commit anywhere. Checked, not
assumed - `git log --all --format='%H %T'` contains `d5b9e56f...` exactly once (against
`787dbff`) and `d721a421...` not at all, and resolving `^{tree}` for every commit in
`git reflog --all` finds no match for `d721a421` either.

Two consequences, neither of them this item's business:

- **The ledger records HOOK RUNS, not commits.** Anything that executes step 6 appends a
  line, committed or not. A reader who assumes one line per commit will over-count.
- **A tree can carry SEVERAL column-4 values**, which is why `$hookOf[$tree]` was already an
  array and why the report prints all of them. Verified in the checker's own output:
  `.\check-hook-attestation.ps1 -Branch 9684bc4 -Base 6dc5e21`, run against the real
  ledger at 2026-09-05T18:40Z, reports "gated by 3 distinct hook file(s)" over those 5
  commits and lists the `?` sentinel as one of the three. (That range is pinned by two
  SHAs, so it is stable in a way section 9's whole-file counts are not - though a future
  hook run on one of those same trees would still add a fourth entry.)

Whether a `?` line should record that it was a non-committing probe is a WRITE-side question
and belongs with `.githooks/pre-commit`, not here.

## 11. The hook's filter and the checker's are different predicates, and neither contains the other

Column 4 is validated at both ends, and the two validations disagree in **both**
directions. Worth stating precisely, because "the read side is simply the stricter one" is
the natural assumption and it is false:

| value reaching column 4 | hook (`.githooks/pre-commit` step 6) | checker (`Get-HookIdentity`) |
|---|---|---|
| 40 lowercase hex | written verbatim | rendered as a hook hash |
| lowercase hex of another length (`6d40f48`, `0`) | **written verbatim** - it passes `case "$_attest_hook" in "" \| *[!0-9a-f]* )` | **`MALFORMED`**, value quoted |
| 40 UPPERCASE hex | **rewritten to `?`** - `[!0-9a-f]` rejects `A-F` | **rendered as a hook hash** - PS 5.1 `-match` is case-INSENSITIVE |

So the checker is stricter on **length** (it demands exactly 40 where the hook admits any
length) and looser on **case** (it accepts uppercase where the hook refuses it). Checked by
running each predicate on those inputs rather than by reading them: the `case` statement
maps `ABCDEF0123456789ABCDEF0123456789ABCDEF01` to `?` and `6d40f48` to itself, while in
PowerShell 5.1 that same uppercase string `-match '^[0-9a-f]{40}$'` is `True` and
`-cmatch '^[0-9a-f]{40}$'` is `False`.

Neither divergence is live today, for different reasons:

- **Length.** No line in the ledger carries a non-40 hex value (see the property below).
  If one appeared, the checker's `MALFORMED` already quotes it and tells the reader the
  truth, so tightening the hook's filter would buy nothing the reader does not already
  get - and changing the hook is out of `ledgerread`'s scope regardless. This is the
  inference this section exists for, and it is a length argument, so it stands.
- **Case.** No writer available can produce uppercase: `git hash-object` prints lowercase,
  and the hook's own filter rewrites an uppercase value to `?` before it could ever be
  appended. The looseness is a mis-render waiting on a writer that does not exist. It is
  recorded as a follow-up, with `-cmatch` as the fix, in this item's merge commit message.

The property that DOES hold in the live ledger, as of **2026-09-05T18:40:27Z**: every hex
value in column 4 is exactly 40 characters and entirely lowercase. The *count* of such
values grows (section 9); the property is the thing to re-check, and it is two commands:

```
awk 'NF==4 && $4 ~ /^[0-9a-f]+$/ && length($4)!=40' .git/hook-attest.log   # -> no output
awk 'NF==4 && $4 ~ /[A-F]/'                         .git/hook-attest.log   # -> no output
```

## 12. Section 2 above is now delivered at the point of output, not only here

Section 2 (a clean merge records `pre-commit`'s hash, because `pre-merge-commit` `exec`s
it and `exec` replaces the process, so `$0` is `pre-commit`) was recorded here as a note.
`ledgerread` moved that sentence into the checker's report block, printed beside the merge
commit's own hash line, on the reviewer's rule that a rule whose absence produces a
CONFIDENT WRONG CONCLUSION rather than a blank must be delivered where the output is read.
This section stays as provenance; it is no longer the only place the fact lives.

Verified against the real merge `9684bc4` - the first two rows are
`git rev-parse 9684bc4:.githooks/pre-commit` and `git rev-parse
9684bc4:.githooks/pre-merge-commit`, the third is column 4 of the ledger line for
`9684bc4^{tree}` (`aaedaca4...`, line 538):

```
pre-commit blob at 9684bc4       519ad9f6cefd714b7f15339d9c1aeefacda0b10a
pre-merge-commit blob at 9684bc4 66588bfbf7760a764af9df5c008cf99da22c0b56
recorded in column 4             519ad9f6cefd714b7f15339d9c1aeefacda0b10a
```

## 13. Unrelated, observed in passing: `queue.ps1 -Claim` has no developer role

`$RoleRules` (`scripts/agent-harness/queue.ps1:162`) has exactly two keys, `tester` and
`reviewer`, so `-Claim -Role developer` hits the `Contains` guard at line 968 and exits 1
with "-Role must be one of: tester, reviewer". Claims are per-role, and only those two
roles have one.

The developer is recorded as a *field* instead: `-Developer <id>` is an optional parameter
of `-Propose` (written to `item.developer`, line 663) and a mandatory one of `-Submit`
(line 754). So an item can show who its developer is; what it cannot show is that someone
is working on it *right now*. There is no in-progress claim for the developer role between
`-ConfirmAnchor` and `-Submit`, and nothing stops or flags a second agent starting on the
same confirmed anchor.

Not touched: it is a harness question, not an attestation one, and it is recorded here only
so the next person who is told to claim an item as developer does not spend time deciding
whether the refusal is their mistake.
