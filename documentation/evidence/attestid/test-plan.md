# Test plan - `attestid`

Branch `work/attestid`, worktree `wt-attestid`, base `af974d1`.
Changes: `.githooks/pre-commit` (step 6 only), `.githooks/README.md`,
`scripts/checks/check-hook-attestation.ps1`, plus
`documentation/notes/hook-attestation-identity.md` (findings sink) and this file.

**You are testing whether the ledger now says WHICH hook gated a tree, and whether
adding that column broke anything that already worked.** No containers, no images, no
leases - this is entirely git and PowerShell. Every case is cheap.

## READ THIS BEFORE CASE 2, IT IS THE TRAP THIS ITEM IS ABOUT

`core.hooksPath` in this repo is an **absolute path to the main checkout**:

    git config --get core.hooksPath      # -> D:\Open WebUI\ai-stack\.githooks

So a plain `git commit` inside the worktree runs the **main checkout's** hook, not the
edited one you are testing. Every commit probe below therefore passes
`-c core.hooksPath=<dir>` explicitly, and **your evidence must say which hook file each
probe exercised**. A probe that does not say this proves nothing - that confusion is the
entire reason this item exists.

## Environment

    cd D:\Open WebUI\ai-stack\.claude\worktrees\wt-attestid
    LEDGER="$(git rev-parse --git-common-dir)/hook-attest.log"

Note the ledger line count before you start (`wc -l` on it) so you can show exactly the
lines your probes added. The ledger is append-only; do not edit or truncate it. Cases that
need a controlled ledger use the drill override (case 8), never the real file.

---

## Case 1 - the artifact is well-formed

    sh -n .githooks/pre-commit        # POSIX syntax
    file .githooks/pre-commit         # must NOT say CRLF

Tokenize the changed PowerShell with `[System.Management.Automation.PSParser]::Tokenize`
on `scripts/checks/check-hook-attestation.ps1` and require zero errors.

PASS: syntax OK, LF, tokenize error count 0.

Also read the diff of the hook and confirm the recorded hash comes from `"$0"` and not
from a path the hook computes:

    git diff af974d1..work/attestid -- .githooks/pre-commit

A computed path would be the defect, not the feature: `$0` is the file git actually
executed, and every other way of naming it records the hook we wish had run.

## Case 2 - a hook that CONTAINS check 5b records its own hash

Make any trivial commit exercising the worktree's own hook:

    echo probe > probe1.txt && git add probe1.txt
    git -c core.hooksPath=.githooks commit -m "test probe 1"

**Hook exercised: the worktree's own `.githooks/pre-commit` - the changed one, contains 5b.**

Then look up the tree in the ledger and compare with the hook's hash:

    grep the tree of HEAD in the ledger
    git hash-object .githooks/pre-commit

PASS: the ledger line has FOUR columns - tree, UTC timestamp, branch, hook hash - and the
fourth column equals `git hash-object .githooks/pre-commit`.

Clean up with `git reset --hard HEAD~1`. The ledger line stays; that is correct, it is a
record of a check that really ran.

## Case 3 - a hook that LACKS check 5b records a DIFFERENT hash

Build a copy of the same hook with the 5b block removed, OUTSIDE the repo (a scratch dir,
called H below). An awk that starts skipping at the `5b.` banner and stops at the `5c.`
banner does it in one line.

    grep -c check-ob1-recipe-tests .githooks/pre-commit    # 1
    grep -c check-ob1-recipe-tests H/pre-commit            # 0
    echo probe > probe2.txt && git add probe2.txt
    git -c core.hooksPath=H commit -m "test probe 2"

**Hook exercised: the scratch copy with 5b deleted.**

PASS, and this is the item's core acceptance:

- the commit output shows every other check running and NO `[check-ob1-recipe-tests]` line;
- the ledger line for this tree carries a hook hash **different** from case 2's, and equal
  to `git hash-object H/pre-commit`.

FAIL if the two probes record the same hash, or if either records `?`.

Clean up with `git reset --hard HEAD~1`.

## Case 4 - the lookup answers the question that cost a reviewer an hour

Using case 2's tree, the whole answer is two commands:

    grep "^$(git rev-parse '<rev>^{tree}')" "$LEDGER"     # which hook gated this tree
    git cat-file -p <hook-hash> | grep -c check-ob1-recipe-tests

PASS: for a tree gated by the worktree hook the count is 1 or more; for case 3's hook,
`git cat-file` fails with "Not a valid object name" - that hook state was never committed,
which the README documents as itself an answer. Confirm the README section
"Which hook gated this tree?" states exactly these two commands and that reading.

Do this on a commit made during the item, not on a historical one - the ledger cannot
answer for the past and must not appear to.

## Case 5 - the guard still PASSES on a normal branch

    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/checks/check-hook-attestation.ps1 -Branch work/attestid -Base refactor/ai-stack-cleanup

PASS: exit 0, "[OK] every commit's tree was validated", and a new "gated by N distinct
hook file(s)" block listing the hashes. Confirm the verdict is unchanged in meaning - the
hook block is REPORTING, and must not be able to turn a pass into a fail.

Also check the JSON consumer, because `queue.ps1 -Submit` parses it: run the same command
with `-Json`.

PASS: valid JSON, `unattested` empty, new `gatedBy` array present.

## Case 6 - the guard still REFUSES an unattested tree

    echo x > probe3.txt && git add probe3.txt
    git commit --no-verify -m "TEMP unattested probe"
    powershell ... check-hook-attestation.ps1 -Branch work/attestid -Base refactor/ai-stack-cleanup
    git reset --hard HEAD~1

PASS: exit **1**, the probe commit listed under `[UNATTESTED]`. FAIL if it exits 0 - a
guard that vacuously passes is the failure mode this script's own header warns about.

## Case 7 - a hook-hash lookup failure degrades to `?` and CANNOT fail the commit

Run the real hook with a `$0` that cannot be hashed. Source it from a shell whose `$0` you
set to a nonexistent path, so `git hash-object` genuinely fails:

    sh -c '. "$PWD/.githooks/pre-commit"' "/no/such/hook/pre-commit"
    echo "exit=$?"
    tail -1 on the ledger

PASS: exit **0**, and the appended line's fourth column is `?`. FAIL on any non-zero exit,
a missing line, or a garbled fourth column. This is the acceptance criterion that keeps the
guard from being switched off, so do not accept a reasoned argument in place of running it.

## Case 8 - back-compat, and the override still needs its flag

Build a synthetic ledger in a scratch dir with two lines: one THREE-column line for a tree
on this branch, and one four-column line whose fourth column is `?`. Then run the check
with `AI_STACK_ATTEST_LEDGER` pointed at it AND `-AllowLedgerOverride`.

PASS: exit 0 - three-column lines are still valid attestations - and the report shows
"(not recorded - ledger line predates hook identity)" and `?` rather than failing.

Then the same command WITHOUT `-AllowLedgerOverride`.

PASS: the "ledger:" line names the REAL ledger under the shared git dir. FAIL if the env
var alone redirected it - that would be the one-line off switch the script removed on
purpose.

## Case 9 - the ledger stayed where it belongs

    git rev-parse --git-common-dir       # the SHARED .git, not the worktree
    git status --short                   # no hook-attest.log anywhere in the tree

PASS: the ledger lives at the shared git dir's `hook-attest.log`, nothing named that
appears in the worktree or in any commit on this branch, and the hook only ever appends
(a double right-angle redirect - confirm by reading step 6).

## Case 10 - KNOWN RED, do not attribute it to this change

`scripts/agent-harness/verify-merge-protocol.ps1` reports **60/66** on this line, six
failures cascading from "two divergent commits exist". I established it is pre-existing by
an A/B run: identical 60/66 with this item's `check-hook-attestation.ps1` swapped for the
`af974d1` version.

If you run the drill, expect that number. Diagnosis and evidence are in
`documentation/notes/hook-attestation-identity.md` section 8 - the drill cuts its
worktrees from `development`, which lacks check 3b's script, while the commits inside them
run the main checkout's newer hook that calls it. Re-verify the A/B yourself if you want
to hold me to it: two runs and one `git checkout af974d1 -- <file>`.

## What a FAIL looks like overall

- The two probes record the same hook hash, or either records `?` in normal operation.
- Case 7 fails the commit, or writes no line.
- Case 6 exits 0.
- `check-hook-attestation.ps1` exits non-zero for a reason connected to the hook column
  rather than to attested-vs-not.
- The evidence does not say which hook file each commit probe exercised.
