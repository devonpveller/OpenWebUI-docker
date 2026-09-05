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

## 9. What column 4 actually holds today, counted rather than assumed

The real ledger at `.git/hook-attest.log`, 2026-09-05, is **543 lines**:

```
awk '{print NF}' .git/hook-attest.log | sort | uniq -c
    523 3          <- three-column, predating the column
     20 4
```

Of the 20 four-column lines: **14** carry a 40-hex hook hash, **4** carry a branch name
(`work/u5proxy`, lines 124/125/156/157, 2026-08-30, from the reverted commit-msg attester
`bd4d891` whose shape was `<tree> <msg-hash> <ts> <branch>`), and **2** carry the literal
`?` (lines 509/533, `work/attestid`, 2026-09-05).

All four shapes therefore exist in the live ledger. This is the fact the checker's old
rendering flattened, and the reason the item's fixture could be built from real material
rather than from imagined values.

## 10. A `?` line today is NOT evidence that any commit was gated by an unnameable hook

Both `?` trees also carry a **hex** line, minutes earlier:

```
508: d5b9e56f... 2026-09-05T02:24:24Z work/attestid 6d40f4800ae58f3edebd3d61c5141235ac19950e
509: d5b9e56f... 2026-09-05T02:26:07Z work/attestid ?
525: d721a421... 2026-09-05T02:42:03Z work/attestid 6d40f4800ae58f3edebd3d61c5141235ac19950e
533: d721a421... 2026-09-05T02:49:22Z work/attestid ?
```

`d5b9e56f` is the tree of commit `787dbff` ("notes(attestid): what the hook-identity column
cannot answer", 2026-09-04 22:24:12 -0400 = 02:24:12Z). The hex line at 02:24:24Z is that
commit's real attestation; the `?` two minutes later is `attestid`'s own test-plan **case 7**,
which sources the hook with a bogus `$0` (`sh -c '. "$PWD/.githooks/pre-commit"'
"/no/such/hook/pre-commit"`) and therefore writes a line **without making a commit at all**.
`d721a421` matches no commit reachable from any ref or reflog, which fits the same reading.

Two consequences, neither of them this item's business:

- **The ledger records HOOK RUNS, not commits.** Anything that executes step 6 appends a
  line, committed or not. A reader who assumes one line per commit will over-count.
- **A tree can carry SEVERAL column-4 values**, which is why `$hookOf[$tree]` was already an
  array and why the report prints all of them. Verified in the output: running the checker
  across `6dc5e21..9684bc4` on the real ledger lists three distinct entries, one of which is
  the `?`.

Whether a `?` line should record that it was a non-committing probe is a WRITE-side question
and belongs with `.githooks/pre-commit`, not here.

## 11. The hook's own column-4 filter accepts hex of any length, not just 40

Step 6 rewrites anything matching `*[!0-9a-f]*` to `?`. A bare-hex string that is not
40 characters (a truncated hash, an abbreviated one, an empty-ish `0`) passes that filter
and would be written to column 4 verbatim. The checker now renders such a value as
`MALFORMED` and quotes it, which is the honest outcome and needs no write-side change --
but it means the hook's validation and the checker's are not the same predicate, and the
checker's is the stricter one. Not observed in the live ledger (all 14 hex values are
exactly 40). Left alone: changing the hook is explicitly out of `ledgerread`'s scope, and
tightening the filter would buy nothing the reader does not already get from `MALFORMED`.

## 12. Section 2 above is now delivered at the point of output, not only here

Section 2 (a clean merge records `pre-commit`'s hash, because `pre-merge-commit` `exec`s
it and `exec` replaces the process, so `$0` is `pre-commit`) was recorded here as a note.
`ledgerread` moved that sentence into the checker's report block, printed beside the merge
commit's own hash line, on the reviewer's rule that a rule whose absence produces a
CONFIDENT WRONG CONCLUSION rather than a blank must be delivered where the output is read.
This section stays as provenance; it is no longer the only place the fact lives.

Verified against the real merge `9684bc4`:

```
pre-commit blob at 9684bc4       519ad9f6cefd714b7f15339d9c1aeefacda0b10a
pre-merge-commit blob at 9684bc4 66588bfbf7760a764af9df5c008cf99da22c0b56
recorded in column 4             519ad9f6cefd714b7f15339d9c1aeefacda0b10a
```

## 13. Unrelated, observed in passing: `queue.ps1 -Claim` has no developer role

`queue.ps1 -Claim -Role developer` exits 1 with "-Role must be one of: tester, reviewer".
The developer is recorded by `-Submit -Developer <id>` instead, so there is no way for a
developer to register an in-progress claim on an item between `-ConfirmAnchor` and
`-Submit` -- two agents could start the same confirmed anchor and neither would see the
other. Not touched: it is a harness question, not an attestation one, and it is recorded
here only so the next person who is told to claim an item as developer does not spend time
deciding whether the refusal is their mistake.
