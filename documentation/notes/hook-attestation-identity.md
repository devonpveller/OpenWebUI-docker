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
