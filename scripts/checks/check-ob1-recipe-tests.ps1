#requires -Version 5
<#
.SYNOPSIS
  Run the OB1 recipe unit tests whenever an OB1 gitlink bump is staged.
  Refuse the commit if any test fails - or if the gate cannot honestly run.

.DESCRIPTION
  Why this exists (2026-09-03): OB1 commit dfc6228 (2026-08-28) shipped a
  one-line binding bug in recipes/_shared/wiki-pages.mjs that starved the
  wiki_pages mirror for 5 days under clean "compile ok" logs - while a unit
  test that caught it (5/10 failing) sat in the tree, run by nothing. Fixed
  in OB1 09f70f4 / parent merge 443c5d8. This gate closes the "run by
  nothing" half: the parent repo deploys OB1 code exclusively by committing
  a gitlink bump (the live containers bind-mount OB1/recipes from this
  checkout), so the staged-gitlink moment IS the deploy chokepoint.

  What it does, in order:
    1. If no OB1 gitlink is staged, exit 0 without invoking node - ordinary
       commits stay cheap.
    2. If the staged gitlink SHA differs from the OB1 working tree's HEAD,
       FAIL: the files on disk are not the tree being pinned, so a green run
       would prove the wrong code. (Stage the intended OB1 state first.)
    3. Run `node --test` over every *.test.mjs under OB1/recipes. Any
       failure, a missing node, or an empty test set fails the commit -
       a gate that cannot run is a gate that is off, and silence is how
       the original bug survived.
    3b. Shrink floor: refuse a bump whose tree has fewer test FILES *or*
       fewer test CASES than the pin it replaces. Cases matter because a
       revert removes a fix together with its own catching test, leaving
       the file count untouched. Override: AI_STACK_OB1_TESTS_ALLOW_SHRINK=1,
       printed loudly. See the block comment on 3b for the measured instance.
    3c. Self-consistency: 3b's case count is LEXICAL (a regex over the staged
       tree) and node's "# tests" below is the runtime truth, and until
       2026-09-04 the same run printed both without ever comparing them.
       They can disagree honestly, so a disagreement WARNS on the green path;
       it does not refuse. See the block comment on 3c for the smuggle that
       walked through the gap.

  The tests are Node-builtins-only by OB1 convention (no npm install), so
  host node is sufficient. Exit code 0 = clean or not applicable, 1 = refuse.

.EXAMPLE
  powershell -File scripts/checks/check-ob1-recipe-tests.ps1
#>
[CmdletBinding()]
param(
    [string]$Root
)

$ErrorActionPreference = 'Stop'

function Fail([string]$msg) {
    Write-Host "[check-ob1-recipe-tests] FAIL: $msg" -ForegroundColor Red
    exit 1
}

# Query the OB1 SUBMODULE repo with a clean git environment. Under a hook, git
# exports GIT_DIR (absolute, the PARENT repo - in a linked worktree its admin
# dir) and GIT_INDEX_FILE; those override `-C`, so `git -C OB1 rev-parse HEAD`
# silently answered with the PARENT's HEAD. Found by this gate's own first
# live run (2026-09-03): it refused a correct commit claiming the OB1 tree was
# at the parent's SHA. Interactive runs never see this - only hook runs do.
function Git-InOB1([string[]]$GitArgs) {
    $saved = @{}
    foreach ($k in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') {
        $saved[$k] = [Environment]::GetEnvironmentVariable($k)
        [Environment]::SetEnvironmentVariable($k, $null)
    }
    try {
        # Under $ErrorActionPreference='Stop', PS 5.1 THROWS a NativeCommandError the
        # moment a redirected native command writes to stderr - which made the
        # "is the submodule initialized?" message below unreachable (reviewer catch,
        # 2026-09-03): the raw exception fired before the friendly Fail. Relax the
        # preference for the native call only; this assignment is function-scoped.
        $ErrorActionPreference = 'Continue'
        & git -C (Join-Path $Root 'OB1') @GitArgs 2>$null
    }
    finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

if (-not $Root) {
    $Root = (& git rev-parse --show-toplevel 2>$null)
    if (-not $Root) { Fail "not inside a git repository and no -Root given" }
}
$Root = (Resolve-Path $Root).Path

# --- 1. Is an OB1 gitlink bump staged? -------------------------------------
$staged = @(& git -C $Root diff --cached --name-only)
if ($staged -notcontains 'OB1') {
    Write-Host "[check-ob1-recipe-tests] no OB1 gitlink staged - skipped."
    exit 0
}

# --- 2. The tree on disk must BE the tree being pinned ---------------------
# `git ls-files -s OB1` reads the INDEX: "160000 <sha> 0\tOB1".
$lsLine = (& git -C $Root ls-files -s OB1) | Select-Object -First 1
if (-not $lsLine -or $lsLine -notmatch '^160000\s+([0-9a-f]{40})\s') {
    Fail "could not read the staged OB1 gitlink from the index (got '$lsLine')"
}
$stagedSha = $Matches[1]
# An UNINITIALIZED submodule is an empty dir with no .git - and `git -C` on it
# does not fail, it walks UP and answers from the PARENT repo (probed
# 2026-09-03: the mismatch message then presented the parent's HEAD as OB1's).
# So the uninit case must be caught explicitly, before any git query.
if (-not (Test-Path (Join-Path $Root 'OB1\.git'))) {
    Fail ("OB1/ has no .git - the submodule is not initialized, so there is no disk tree to " +
          "test against the staged gitlink. Run: git submodule update --init OB1")
}
$diskSha = (Git-InOB1 @('rev-parse', 'HEAD'))
if (-not $diskSha) { Fail "OB1/ has no readable git HEAD - is the submodule initialized?" }
if ($stagedSha -ne $diskSha) {
    Fail ("staged OB1 gitlink is $($stagedSha.Substring(0,7)) but the OB1 working tree is at " +
          "$($diskSha.Substring(0,7)). The tests below would run against the DISK tree and prove " +
          "nothing about the tree this commit deploys. Align them first (git -C OB1 checkout " +
          "$($stagedSha.Substring(0,7)), or re-stage: git add OB1).")
}
# Same clause, stricter reading: dirty TRACKED files inside OB1 mean the disk
# tree (what the live bind mounts serve, and what the tests below read) is not
# the commit being pinned - the "deployed from an uncommitted tree" trap.
# -uno is a deliberate trade, stated honestly (corrected 2026-09-03 - the
# original rationale cited OB1/docker/.env, which is gitignored and would not
# be listed anyway): untracked-NOT-ignored files under OB1 are invisible to
# this check, yet they ARE served by the live bind mounts, and an untracked
# *.test.mjs is even run by this gate while absent from the pinned commit.
# Checking them would refuse commits over scratch files; this gate pins the
# COMMIT's honesty and leaves untracked-file hygiene to the operator.
$dirty = @(Git-InOB1 @('status', '--porcelain', '--untracked-files=no'))
if ($dirty.Count -gt 0) {
    $dirty | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
    Fail ("OB1 has $($dirty.Count) uncommitted tracked change(s) - the disk tree the tests (and " +
          "the live bind mounts) see is not the commit $($stagedSha.Substring(0,7)) this gitlink " +
          "pins. Commit or stash them in OB1 first.")
}

# --- 3. Run every recipe test ----------------------------------------------
$nodeCmd = Get-Command node -ErrorAction SilentlyContinue
if (-not $nodeCmd) {
    Fail ("node is not on PATH, so the recipe tests cannot run. A gate that silently skips is " +
          "how the 08-28 wiki-pages bug shipped - install node (or commit from a shell that has " +
          "it) rather than bypassing this.")
}

# node_modules is gitignored, so anything under it is vendored, not ours - and one
# `npm install` inside a recipe (8 package.json files live under OB1/recipes) would
# otherwise feed foreign tests to this gate and turn it red on innocent commits:
# the false-positive-disables-the-guard failure this hook file warns about twice.
$testFiles = @(Get-ChildItem -Path (Join-Path $Root 'OB1\recipes') -Recurse -Filter '*.test.mjs' |
    Where-Object { $_.FullName -notmatch '\\node_modules\\' } |
    ForEach-Object { $_.FullName })
if ($testFiles.Count -eq 0) {
    Fail "found ZERO *.test.mjs under OB1/recipes - an empty test set passing is vacuous, refusing."
}

# --- 3b. Test floor: a bump may not silently SHRINK the test set -----------
# "Zero tests" is not the only vacuity: deleting 46 of 47 files also passes a
# bare test run (tester finding, 2026-09-03). Compare the tree being REPLACED
# (HEAD:OB1) with the tree being PINNED (the staged SHA), both read out of git
# objects with ls-tree/show - disk state cannot dodge this.
#
# COUNT CASES, NOT ONLY FILES (2026-09-04). The floor originally compared only
# the NUMBER of *.test.mjs files, which is blind to the exact move it was
# written to stop: a gitlink bump that REVERTS a fix takes the fix's catching
# test with it, INSIDE A FILE THAT STILL EXISTS - so the file count does not
# move and the gate goes green. Measured on this repo's own pins:
# recipes/_shared/citations.test.mjs carries 11 cases at 48c0363 (with the
# linkSafeLabel '#' wikilink fix) and 10 at f3ded84 (without), while the file
# count is 8 either way; totals 48 -> 47. Both counts are now compared, a
# shrink in EITHER refuses, and the refusal names both.
#
# The case count is LEXICAL: lines starting (after optional whitespace and an
# optional `await`) with test( / it( / t.test(. That approximates what
# `node --test` reports, and the approximation is ASYMMETRIC. The text here
# used to say flatly "a commented-out case still counts", which is true of one
# comment syntax and false of the other (corrected 2026-09-04):
#   - a case generated in a loop counts ONCE here and runs many times;
#   - a case inside a /* block comment */ COUNTS here and never runs - the
#     regex sees the test( line and knows nothing about the enclosing block;
#   - a case behind a // line comment does NOT count, because only whitespace
#     and an optional `await` may precede test( and // is neither. Commenting
#     a case out with // therefore LOWERS this count.
# So it is deliberately never presented as a coverage number. It is applied
# IDENTICALLY to both trees, so what the floor actually reads is the DELTA,
# and a revert is exactly what moves the delta. Which of the two comment
# syntaxes you use stops being trivia at step 3c: a block-commented test( line
# is precisely how you put back a lexical count you just reverted away.
#
# Deliberate removals: AI_STACK_OB1_TESTS_ALLOW_SHRINK=1 for that one commit.
# An env var leaves no git trace (the --no-verify lesson), so the override is
# PRINTED loudly here and the shrink itself is visible in the OB1 diff forever.
#
# The counter is defined out here, and the STAGED tree is counted
# unconditionally, because step 3c needs the staged count even on the
# first-bump path where there is no old pin to compare it against.
$countTests = {
    param($sha)
    # A failed ls-tree/show yields an EMPTY array, and 0 would read as "no
    # tests" - making the floor silently vacuous exactly when it cannot see.
    # Return the -1 sentinel in BOTH fields so the caller can tell "could not
    # look" from "looked, found none": a failed lookup must never read as a pass.
    $blind = [pscustomobject]@{ Files = -1; Cases = -1 }
    # QUOTEPATH (fixed 2026-09-04; latent, not live - no non-ASCII test
    # filename exists yet, which is the only reason it is being fixed calmly).
    # This used to be `ls-tree -r --name-only` followed by `git show "$sha:$path"`.
    # core.quotepath is unset in this repo and therefore ON, so git C-quotes any
    # path containing a byte above 0x7f: a test file whose name carries an
    # accented letter comes back as the LITERAL text
    #     "recipes/_shared/caf\303\251.test.mjs"
    # - surrounding double quotes included. The filter below anchors on
    # `.test.mjs$`; that string ends in a double quote, so the file fell out of
    # the list, and the floor went on to print a clean, lower total. A floor
    # that silently counts less than it claims is worse than no floor at all.
    # Two changes, and both are needed:
    #   -c core.quotepath=false  - the name comes back raw, so the suffix matches.
    #   read each blob BY OBJECT ID, not by "$sha:$path" - even with quotepath
    #     off, a non-ASCII name still round-trips out through the console
    #     codepage and back into git as an argument, and a mojibaked name is a
    #     FAILED lookup (the -1 sentinel: loud, but still not a count). A blob
    #     id is 40 ASCII hex characters and no codepage can mangle it.
    $entries = @(Git-InOB1 @('-c', 'core.quotepath=false', 'ls-tree', '-r', $sha, 'recipes'))
    if ($LASTEXITCODE -ne 0) { return $blind }
    $blobs = @()
    foreach ($e in $entries) {
        if ($e -match '^\d{6}\s+blob\s+([0-9a-f]{40})\t(.+)$') {
            # Copy BOTH groups out before the next -match runs: $Matches is a
            # single automatic variable that every -match overwrites, so
            # `if ($Matches[2] -match ...) { $blobs += $Matches[1] }` appends
            # $null - and `git show` with a null argument shows HEAD, exits 0,
            # and contributes 0 cases. Written that way first; the scratch-tree
            # proof for this very fix reported "CASES 0 -> 0" and caught it.
            $blobSha = $Matches[1]
            $blobPath = $Matches[2]
            if ($blobPath -match '\.test\.mjs$') { $blobs += $blobSha }
        }
    }
    $cases = 0
    foreach ($b in $blobs) {
        # A blank id here would make `git show` print HEAD and exit 0 - a lookup
        # that failed while looking like it worked. Refuse to guess.
        if ($b -notmatch '^[0-9a-f]{40}$') { return $blind }
        $lines = @(Git-InOB1 @('show', $b))
        if ($LASTEXITCODE -ne 0) { return $blind }
        $cases += @($lines | Where-Object { $_ -match '^\s*(await\s+)?(t\.)?(test|it)\s*\(' }).Count
    }
    [pscustomobject]@{ Files = $blobs.Count; Cases = $cases }
}
$new = & $countTests $stagedSha
$newShort = $stagedSha.Substring(0, 7)

$oldPin = (& git -C $Root rev-parse -q --verify 'HEAD:OB1' 2>$null)
if ($oldPin -and $oldPin -match '^[0-9a-f]{40}$') {
    $old = & $countTests $oldPin
    $oldShort = $oldPin.Substring(0, 7)
    if ($old.Files -lt 0 -or $new.Files -lt 0) {
        # Best-effort floor: an unreadable tree must not refuse legitimate bumps,
        # but it must never look like a passed comparison either.
        Write-Host ("[check-ob1-recipe-tests] WARNING: could not count tests in " +
            "$oldShort or $newShort - the shrink floor " +
            "DID NOT RUN this commit.") -ForegroundColor Yellow
    } elseif ($new.Files -lt $old.Files -or $new.Cases -lt $old.Cases) {
        $delta = ("test FILES $($old.Files) -> $($new.Files), test CASES " +
                  "$($old.Cases) -> $($new.Cases) ($oldShort -> $newShort)")
        if ($env:AI_STACK_OB1_TESTS_ALLOW_SHRINK -eq '1') {
            Write-Host ("[check-ob1-recipe-tests] OVERRIDE: the OB1 test set SHRANK - $delta - " +
                "and AI_STACK_OB1_TESTS_ALLOW_SHRINK=1 waved it through. If you did not set " +
                "this deliberately for THIS commit, stop and look.") -ForegroundColor Yellow
        } else {
            Fail ("the staged OB1 tree has FEWER tests than the pin it replaces: $delta. A " +
                  "shrink in CASES with the FILE count unchanged is the shape of a REVERT - " +
                  "the fix and its own catching test leave together, inside a file that still " +
                  "exists. A test that quietly stops existing is the 08-28 failure with an " +
                  "extra step. If the removal is deliberate, re-run with " +
                  "AI_STACK_OB1_TESTS_ALLOW_SHRINK=1 (printed loudly, so it cannot hide).")
        }
    } else {
        Write-Host ("[check-ob1-recipe-tests] shrink floor OK - test FILES $($old.Files) -> " +
            "$($new.Files), test CASES $($old.Cases) -> $($new.Cases) ($oldShort -> $newShort).")
    }
} else {
    Write-Host "[check-ob1-recipe-tests] NOTE: no previous OB1 pin in HEAD - shrink floor skipped (first bump)."
}

$out = & $nodeCmd.Source --test @testFiles 2>&1
$code = $LASTEXITCODE
$summary = ($out | Where-Object { $_ -match '^# (tests|pass|fail) ' }) -join ' | '

if ($code -ne 0) {
    # Show the failing tests, not the whole TAP stream.
    $out | Where-Object { $_ -match '^not ok|^# Subtest.*fail|AssertionError|ReferenceError|Error \[' } |
        Select-Object -First 12 | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Fail ("recipe tests FAILED ($summary) for staged OB1 $($stagedSha.Substring(0,7)). " +
          "Fix the tree (or the test) in OB1, push, re-stage the gitlink, and commit again.")
}

# --- 3c. Self-consistency: does node agree with the lexical count? ---------
# Until 2026-09-04 this script printed two counts of the same thing and never
# compared them. The tester's smuggle rode straight through the gap: a revert
# that removed one real case, plus a block-commented test( line put back in
# its place, restored the LEXICAL count to 48 - so 3b printed
# "test CASES 48 -> 48" and passed - while node, two lines further down,
# printed "# tests 47". Both numbers were on the operator's screen at once.
#
# This CANNOT be an equality gate. The two numbers diverge honestly: a case
# generated in a loop is one lexical line and many runtime tests, test.skip
# still counts as a test to node, a block-commented case is lexical only. So a
# disagreement is a WARNING - it says "look at the diff", not "you are wrong".
#
# GREEN PATH ONLY, deliberately: on a red run the commit is already refused
# and the operator has real failures to read, and a file that throws on import
# makes the two counts disagree for a reason that is already on screen.
$nodeTests = $null
foreach ($line in $out) {
    if ([string]$line -match '^# tests ([0-9]+)\s*$') { $nodeTests = [int]$Matches[1] }
}
if ($null -eq $nodeTests -or $new.Cases -lt 0) {
    Write-Host ("[check-ob1-recipe-tests] WARNING: self-consistency check DID NOT RUN - " +
        "lexical case count " + $(if ($new.Cases -lt 0) { 'unreadable' } else { "$($new.Cases)" }) +
        ", node '# tests' " + $(if ($null -eq $nodeTests) { 'not found in the TAP output' } else { "$nodeTests" }) +
        ". The two counts below were not compared.") -ForegroundColor Yellow
} elseif ($nodeTests -ne $new.Cases) {
    Write-Host ("[check-ob1-recipe-tests] WARNING: the lexical case count and node DISAGREE - " +
        "3b counted $($new.Cases) test case(s) in staged OB1 $newShort, node ran $nodeTests. " +
        "This is not proof of anything on its own (loops, test.skip and /* block-commented */ " +
        "cases all diverge honestly), but it is the ONE signal that separates an honest " +
        "refactor from a revert with a commented-out case pasted in to hold the count up. " +
        "Read the OB1 diff for this bump before you accept the OK below.") -ForegroundColor Yellow
}

Write-Host "[check-ob1-recipe-tests] OK - $($testFiles.Count) test file(s), $summary (staged OB1 $($stagedSha.Substring(0,7)))."
exit 0
