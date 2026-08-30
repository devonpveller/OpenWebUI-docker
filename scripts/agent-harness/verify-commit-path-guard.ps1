# verify-commit-path-guard.ps1 - executable proof of .githooks/reference-transaction.
#
#   .\scripts\agent-harness\verify-commit-path-guard.ps1          # ~20s, cleans up after itself
#   .\scripts\agent-harness\verify-commit-path-guard.ps1 -Audit   # print this machine's guard log
#
# WHAT IT PROVES (dark-factory-unification U5). The commit-path guard must do two things,
# and the second is the one guards usually fail:
#
#   1. FAIL RED on a real bypass. `git commit --no-verify`, `git merge --no-verify` and
#      `git commit --amend --no-verify` must not be able to advance a branch.
#   2. NOT fire on honest work. Every ordinary git operation in MERGE-PROTOCOL.md - clean
#      merge, conflicted merge resolution, reviewer rebase, cherry-pick, reset, branch
#      creation, fast-forward pull - must pass untouched. A guard that false-positives is
#      switched off within a day and then protects nothing.
#
# It also carries a NEGATIVE CONTROL (step 11): with the hook removed, the same bypass
# SUCCEEDS. Without that, a drill that passed would be equally consistent with a guard
# that checks nothing - which is this repo's most-repeated defect (CLAUDE.md: "a check
# that passes while checking nothing").
#
# HOW REAL IS THE FIXTURE. The hook under test is copied VERBATIM from .githooks. The
# stub pre-commit is not hand-written either: its body is the real .githooks/pre-commit's
# attestation block, extracted by marker, so the ledger this drill writes is produced by
# the same code the real hook runs. If that block ever moves or is renamed, the extraction
# fails and the drill fails loudly rather than testing a divergent copy. The five
# PowerShell validations pre-commit runs before that block are deliberately NOT reproduced
# - they need the real repo, and none of them is what this guard depends on.

[CmdletBinding()]
param([switch]$Audit, [switch]$Json)

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here

$repo = (& git.exe rev-parse --show-toplevel 2>$null | Select-Object -First 1)
if (-not $repo) { Write-Host "not inside a git repository" -ForegroundColor Red; exit 2 }
$hookSrc   = Join-Path $repo ".githooks/reference-transaction"
$precommit = Join-Path $repo ".githooks/pre-commit"

if ($Audit) {
    $common = (& git.exe rev-parse --git-common-dir 2>$null | Select-Object -First 1)
    if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $repo $common }
    $log = Join-Path $common "hook-guard.log"
    if (-not (Test-Path $log)) {
        Write-Host "no guard log at $log (nothing has been refused on this machine)"
        exit 0
    }
    Write-Host "commit-path guard audit record: $log"
    Get-Content $log | ForEach-Object { Write-Host ("  " + $_) }
    exit 0
}

$results = @()
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function Step($n, $t) { Write-Host "`n=== $n. $t ===" -ForegroundColor Cyan }

# --- fixture ----------------------------------------------------------------------
Step 0 "fixture: the REAL hook plus the REAL attestation block"
Check "the guard hook exists" (Test-Path $hookSrc)
if (-not (Test-Path $hookSrc)) { exit 1 }
$hookText = Get-Content -Raw -Path $hookSrc
Check "the hook is LF-only (CRLF breaks it under Git Bash)" (-not ($hookText -match "`r"))

$pcLines = @(Get-Content -Path $precommit)
$marker = $pcLines | Select-String -SimpleMatch "--- 6. ATTESTATION" | Select-Object -First 1
Check "the attestation block is still findable in the real pre-commit" ([bool]$marker) "marker: '--- 6. ATTESTATION'"
if (-not $marker) { exit 1 }
$attestBlock = $pcLines[($marker.LineNumber - 1)..($pcLines.Count - 1)]
$attestText = ($attestBlock -join "`n")
Check "the extracted block writes the ledger" (($attestText -match 'hook-attest\.log') -and ($attestText -match 'git write-tree'))

$root = Join-Path $env:TEMP ("cpg-drill-" + $PID)
if (Test-Path $root) { Remove-Item -Recurse -Force $root }
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root "hooks")
$hooks = Join-Path $root "hooks"

# LF, no BOM - these are shell scripts run by Git Bash.
function Write-Sh([string]$path, [string[]]$lines) {
    $text = ($lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}
Copy-Item $hookSrc (Join-Path $hooks "reference-transaction")
Write-Sh (Join-Path $hooks "pre-commit") (@('#!/bin/sh', 'echo "PRECOMMIT-STUB"') + $attestBlock + @('exit 0'))
Write-Sh (Join-Path $hooks "pre-merge-commit") @('#!/bin/sh', 'exec "$(dirname "$0")/pre-commit"')

$work = Join-Path $root "repo"
function G {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return (& git.exe -C $work @args 2>&1) } finally { $ErrorActionPreference = $prev }
}
function Head { return ((& git.exe -C $work rev-parse HEAD 2>$null) | Select-Object -First 1) }
function GuardFired([string[]]$out) { return [bool](($out -join "`n") -match "COMMIT-PATH GUARD") }
function Commit([string]$msg, [switch]$NoVerify) {
    $a = @("commit", "-q", "-m", $msg)
    if ($NoVerify) { $a = @("commit", "-q", "--no-verify", "-m", $msg) }
    $o = G @a
    return [pscustomobject]@{ exit = $LASTEXITCODE; out = @($o) }
}
$null = & git.exe init -q -b main $work 2>&1
$null = G config core.hooksPath ../hooks
$null = G config user.email drill@local
$null = G config user.name  drill
$guardLog = Join-Path $work ".git/hook-guard.log"
function DenyCount { if (Test-Path $guardLog) { return @(Select-String -Path $guardLog -SimpleMatch " DENY ").Count } else { return 0 } }
function SkipCount { if (Test-Path $guardLog) { return @(Select-String -Path $guardLog -SimpleMatch " SKIP-SEQUENCER ").Count } else { return 0 } }
function Touch([string]$name, [string]$content) {
    [System.IO.File]::WriteAllText((Join-Path $work $name), $content + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

# --- GREEN: honest work must pass ---------------------------------------------------
Step 1 "honest work passes (the false-positive half)"
Touch "a.txt" "a"; $null = G add a.txt
$r = Commit "honest one"
Check "first honest commit lands (the ledger is created by the hook itself)" ($r.exit -eq 0 -and (Head))
Check "the ledger has an entry" (Test-Path (Join-Path $work ".git/hook-attest.log"))

# --- RED: the bypasses --------------------------------------------------------------
Step 2 "git commit --no-verify is REFUSED, and the branch does not move"
$before = Head
Touch "b.txt" "b"; $null = G add b.txt
$denyBefore = DenyCount
$r = Commit "bypass attempt" -NoVerify
Check "exit 128 (git's ref-update abort)" ($r.exit -eq 128) ("exit=" + $r.exit)
Check "the guard identified itself on stderr" (GuardFired $r.out)
Check "HEAD did NOT move" ((Head) -eq $before)
# EXACTLY one record, not "at least one". git calls this hook three times per transaction
# (prepared / committed / aborted); a hook that judged all three would write three lines
# and abort twice. One line is the proof that it acts only at `prepared`.
Check "exactly one audit record was appended (so the hook acts only at state=prepared)" `
      ((DenyCount) -eq ($denyBefore + 1)) ("deny=" + (DenyCount))

Step 3 "the SAME content commits fine through the hooks (step 2 refused the bypass, not the content)"
$r = Commit "same content, honest path"
Check "honest commit of the identical tree lands" ($r.exit -eq 0 -and (Head) -ne $before)

Step 4 "git commit --amend --no-verify with changed content is REFUSED"
$before = Head
Touch "b.txt" "b-changed"; $null = G add b.txt
$null = G commit -q --amend --no-edit --no-verify
Check "amend bypass exits 128" ($LASTEXITCODE -eq 128) ("exit=" + $LASTEXITCODE)
Check "HEAD did NOT move" ((Head) -eq $before)
$null = G commit -q --amend --no-edit
Check "the honest amend of the same content lands" ($LASTEXITCODE -eq 0 -and (Head) -ne $before)

Step 5 "git merge --no-verify is REFUSED; the clean merge through the hook is not"
# BOTH SIDES MUST MOVE, or the merge tree is identical to the side branch's tree - which is
# already attested, and the guard correctly lets it through. The first version of this step
# did exactly that and reported the guard broken; the fixture was wrong, not the guard. A
# merge is only new content when the two branches diverged.
$null = G checkout -qb side
Touch "s.txt" "s"; $null = G add s.txt; $null = Commit "side work"
$null = G checkout -q main
Touch "m.txt" "m"; $null = G add m.txt; $null = Commit "main moves too"
$before = Head
$null = G merge -q --no-ff --no-verify -m "merge bypass" side
Check "merge bypass exits non-zero" ($LASTEXITCODE -ne 0) ("exit=" + $LASTEXITCODE)
Check "HEAD did NOT move" ((Head) -eq $before)
$null = G merge --abort
$null = G merge -q --no-ff -m "merge honest" side
Check "the same merge through pre-merge-commit lands" ($LASTEXITCODE -eq 0 -and (Head) -ne $before)

Step 6 "a CONFLICTED merge, resolved with git commit, passes"
$null = G checkout -q main
Touch "conf.txt" "base"; $null = G add conf.txt; $null = Commit "conf base"
$null = G checkout -qb cx
Touch "conf.txt" "X"; $null = G add conf.txt; $null = Commit "cx"
$null = G checkout -q main
Touch "conf.txt" "Y"; $null = G add conf.txt; $null = Commit "cy"
$null = G merge --no-ff -m "merge cx" cx
Check "the merge conflicted (the fixture is real)" ($LASTEXITCODE -ne 0)
Touch "conf.txt" "resolved"; $null = G add conf.txt
$before = Head
$null = G commit -q --no-edit
Check "the resolution commit lands" ($LASTEXITCODE -eq 0 -and (Head) -ne $before) ("exit=" + $LASTEXITCODE)

Step 7 "ref MOVES are not commits and must not fire"
$tip = Head
$null = G branch oldpt HEAD~2
Check "branch created at an older commit" ($LASTEXITCODE -eq 0)
$null = G reset -q --hard HEAD~2
Check "reset --hard backwards" ($LASTEXITCODE -eq 0)
$null = G reset -q --hard $tip
Check "reset --hard forwards to the old tip" ($LASTEXITCODE -eq 0 -and (Head) -eq $tip)
Touch "st.txt" "st"; $null = G add st.txt
$null = G stash
Check "git stash (refs/stash, not refs/heads)" ($LASTEXITCODE -eq 0)
$null = G stash pop
Check "git stash pop" ($LASTEXITCODE -eq 0)
$null = G reset -q

Step 8 "the sequencer (rebase / cherry-pick) passes, and is LOGGED rather than passed silently"
$skipBefore = SkipCount
$null = G checkout -qb rb HEAD~1
Touch "rb.txt" "rb"; $null = G add rb.txt; $null = Commit "rb work"
$null = G rebase main
Check "reviewer-style rebase completes" ($LASTEXITCODE -eq 0) ("exit=" + $LASTEXITCODE)
$null = G checkout -q main
$null = G cherry-pick rb
Check "cherry-pick completes" ($LASTEXITCODE -eq 0) ("exit=" + $LASTEXITCODE)
Check "both were recorded as SKIP-SEQUENCER (visible, not silent)" ((SkipCount) -ge ($skipBefore + 2)) ("skips=" + (SkipCount))

Step 9 "a fast-forward pull from another repo passes"
$clone = Join-Path $root "clone"
$null = & git.exe clone -q $work $clone 2>&1
$null = & git.exe -C $clone config core.hooksPath ../hooks 2>&1
$null = & git.exe -C $clone config user.email drill@local 2>&1
$null = & git.exe -C $clone config user.name drill 2>&1
Touch "ff.txt" "ff"; $null = G add ff.txt; $null = Commit "ff commit"
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
$null = & git.exe -C $clone pull -q --ff-only 2>&1
$ffExit = $LASTEXITCODE
$ErrorActionPreference = $prev
Check "ff pull lands in the clone" ($ffExit -eq 0) ("exit=" + $ffExit)

Step 10 "the hook is inert outside state=prepared, driven directly"
# sh.exe is NOT on PowerShell's PATH (only inside Git Bash), and `& sh.exe` failing raises
# CommandNotFoundException WITHOUT touching $LASTEXITCODE - so the first version of this
# step read a stale 0 from the previous command and reported PASS while executing nothing.
# Resolve the interpreter from git's own installation and assert we found it.
$gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
$gitRoot = if ($gitExe) { Split-Path (Split-Path $gitExe) } else { "" }
$shExe = @("usr/bin/sh.exe", "bin/sh.exe") |
    ForEach-Object { if ($gitRoot) { Join-Path $gitRoot $_ } } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
Check "a POSIX sh was located to drive the hook directly" ([bool]$shExe) ("sh=" + $shExe)
if ($shExe) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $fake = "0000000000000000000000000000000000000000 " + (Head) + " refs/heads/main"
    Push-Location $work
    $null = ($fake | & $shExe (Join-Path $hooks "reference-transaction") committed 2>&1)
    $stateCommitted = $LASTEXITCODE
    # ... and the SAME shape at `prepared`, carrying a tree that was never attested, must be
    # judged. Without this pair the case above proves only that the hook did nothing.
    # NO PIPING INTO git's stdin. PS 5.1 prefixes a UTF-8 BOM when it pipes a string to a
    # native process, and `git mktree` rejects the line as "input format error" - the tree
    # came back empty, the crafted update line lost a field, and the hook read a refname
    # that did not start with refs/heads, so it exited 0 and this check PASSED while
    # proving nothing. A temporary index needs no stdin at all.
    Touch "unattested.txt" "never attested by any hook"
    $idx = Join-Path $root "tmp.index"
    $env:GIT_INDEX_FILE = $idx
    $null = & git.exe -C $work read-tree HEAD 2>&1
    $null = & git.exe -C $work add unattested.txt 2>&1
    $tree = ((& git.exe -C $work write-tree 2>$null) | Select-Object -First 1)
    Remove-Item env:GIT_INDEX_FILE
    Remove-Item (Join-Path $work "unattested.txt") -Force -ErrorAction SilentlyContinue
    $orphan = ((& git.exe -C $work commit-tree $tree -m "unreachable, unattested" 2>$null) | Select-Object -First 1)
    Check "the crafted unattested commit was actually built" ([bool]$orphan -and $orphan.Length -eq 40) ("orphan=" + $orphan)
    $fake2 = "0000000000000000000000000000000000000000 " + $orphan + " refs/heads/main"
    $null = ($fake2 | & $shExe (Join-Path $hooks "reference-transaction") prepared 2>&1)
    $statePrepared = $LASTEXITCODE
    Pop-Location
    $ErrorActionPreference = $prev
    Check "state=committed exits 0 without judging anything" ($stateCommitted -eq 0) ("exit=" + $stateCommitted)
    Check "state=prepared REFUSES the same shape when the tree is unattested" `
          ($statePrepared -ne 0) ("exit=" + $statePrepared)
}

Step 11 "the documented BOUNDARY is measured, not asserted"
# .githooks/README.md and the hook header both make claims about what this guard does and
# does not stop. Claims in prose are how a guard ends up oversold; these four are the
# executable versions of them, so a future change that silently moves the boundary in
# either direction shows up as a red check rather than as stale documentation.
$null = G checkout -q main
# (a) a MISSING ledger must fail CLOSED, not open. The obvious guess - "delete the ledger
#     and the guard has nothing to check against" - is wrong, and being wrong in the safe
#     direction is worth a check.
$ledgerPath = Join-Path $work ".git/hook-attest.log"
Move-Item $ledgerPath ($ledgerPath + ".bak") -Force
$before = Head
Touch "noledger.txt" "x"; $null = G add noledger.txt
$r = Commit "no ledger" -NoVerify
Check "a missing ledger DENIES (fails closed)" ($r.exit -eq 128 -and (Head) -eq $before) ("exit=" + $r.exit)
Check "and it is recorded as reason=no-ledger" `
      ([bool](Test-Path $guardLog) -and @(Select-String -Path $guardLog -SimpleMatch "reason=no-ledger").Count -ge 1)
Move-Item ($ledgerPath + ".bak") $ledgerPath -Force
# (b) the operator's escape hatch, which is also the honest admission: turning every hook
#     off DOES land. There is no privilege boundary here to prevent it.
$before = Head
$null = G -c core.hooksPath=/nonexistent commit -q -m "hooks off entirely"
Check "core.hooksPath override lands (the documented escape, and the honest limit)" `
      ($LASTEXITCODE -eq 0 -and (Head) -ne $before) ("exit=" + $LASTEXITCODE)
# (c) the DIRECT plumbing route is closed - refs/heads is what the hook watches.
$idx2 = Join-Path $root "tmp2.index"
Touch "plumb.txt" "plumb"
$env:GIT_INDEX_FILE = $idx2
$null = & git.exe -C $work read-tree HEAD 2>&1
$null = & git.exe -C $work add plumb.txt 2>&1
$ptree = ((& git.exe -C $work write-tree 2>$null) | Select-Object -First 1)
Remove-Item env:GIT_INDEX_FILE
$pcommit = ((& git.exe -C $work commit-tree $ptree -p (Head) -m "crafted" 2>$null) | Select-Object -First 1)
$null = G update-ref refs/heads/plumbed $pcommit
Check "git update-ref straight into refs/heads is REFUSED" ($LASTEXITCODE -ne 0) ("exit=" + $LASTEXITCODE)
# (d) ... and the residual that is deliberately left open, because closing it would start
#     refusing an honest `git checkout -b rel <tag>`.
$null = G update-ref refs/pre/parked $pcommit
$parkExit = $LASTEXITCODE
$null = G update-ref refs/heads/plumbed $pcommit
Check "parking it under a NON-refs/heads ref first does land (known residual)" `
      ($parkExit -eq 0 -and $LASTEXITCODE -eq 0) ("park=" + $parkExit + " move=" + $LASTEXITCODE)

# --- NEGATIVE CONTROL ---------------------------------------------------------------
Step 12 "NEGATIVE CONTROL: with the hook removed, the same bypass SUCCEEDS"
Remove-Item (Join-Path $hooks "reference-transaction") -Force
$null = G checkout -q main
$before = Head
Touch "neg.txt" "neg"; $null = G add neg.txt
$r = Commit "bypass with the guard removed" -NoVerify
Check "the bypass lands when the guard is gone (so steps 2/4/5 measured the guard)" `
      ($r.exit -eq 0 -and (Head) -ne $before) ("exit=" + $r.exit)

# --- report --------------------------------------------------------------------------
Set-Location $env:TEMP
Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue

$fail = @($results | Where-Object { -not $_.pass })
Write-Host ""
if ($Json) {
    [pscustomobject]@{
        total  = $results.Count
        failed = $fail.Count
        checks = @($results | ForEach-Object { @{ check = $_.check; pass = $_.pass; detail = $_.detail } })
    } | ConvertTo-Json -Depth 5 -Compress
} elseif ($fail.Count -eq 0) {
    Write-Host ("ALL {0} CHECKS PASSED - the commit-path guard fails red on bypass and green on honest work." -f $results.Count) -ForegroundColor Green
} else {
    Write-Host ("{0} of {1} CHECKS FAILED:" -f $fail.Count, $results.Count) -ForegroundColor Red
    $fail | ForEach-Object { Write-Host ("  - " + $_.check + " " + $_.detail) -ForegroundColor Red }
}
exit ($(if ($fail.Count) { 1 } else { 0 }))
