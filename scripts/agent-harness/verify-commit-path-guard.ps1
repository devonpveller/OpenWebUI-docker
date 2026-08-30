# verify-commit-path-guard.ps1 - executable proof of .githooks/reference-transaction.
#
#   .\scripts\agent-harness\verify-commit-path-guard.ps1          # ~30s, cleans up after itself
#   .\scripts\agent-harness\verify-commit-path-guard.ps1 -Audit   # print this machine's guard log
#
# WHAT IT PROVES (dark-factory-unification U5). The commit-path guard must do three things,
# and the second and third are the ones guards usually fail:
#
#   1. FAIL RED on a real bypass. `git commit --no-verify`, `git merge --no-verify` and
#      `git commit --amend --no-verify` must not be able to advance a branch - including
#      the RETRY shape, where an earlier attempt was refused by commit-msg.
#   2. NOT fire on honest work. Every ordinary git operation in MERGE-PROTOCOL.md - clean
#      merge, conflicted merge resolution, reviewer rebase, cherry-pick, reset, branch
#      creation, fast-forward pull, partial commit - must pass untouched. A guard that
#      false-positives is switched off within a day and then protects nothing.
#   3. STATE ITS OWN BOUNDARY TRUTHFULLY. Step 11 executes every route the documentation
#      admits survives, and records for each one whether it lands, whether a guard-log line
#      is written, and whether the commit is still UNATTESTED (which is what
#      scripts/checks/check-hook-attestation.ps1 reads at the submit gate). A boundary
#      claimed in prose is how a guard gets oversold; this is the executable version of it.
#      The branch shipped a table saying "two routes survive" and a reviewer measured three
#      more, so the table is now generated from measurement instead of from memory.
#
# It also carries a NEGATIVE CONTROL (step 12): with the hook removed, the same bypass
# SUCCEEDS. Without that, a drill that passed would be equally consistent with a guard
# that checks nothing - which is this repo's most-repeated defect (CLAUDE.md: "a check
# that passes while checking nothing").
#
# HOW REAL IS THE FIXTURE. reference-transaction, attest-lib.sh AND commit-msg are copied
# VERBATIM from .githooks - no reassembly, no stand-ins - and step 11 arms the real
# gitlink-SHA veto with a real submodule and a real bogus SHA.
#
# It was not always so, and the earlier shortcut is worth recording because it is this
# repo's signature defect. The fixture used to REASSEMBLE commit-msg: extract its
# attestation block by marker, paste it under a hand-written `REJECTME` veto. That fixture
# could not see this file's control flow, so a mutation that moved the attestation block
# ABOVE the veto - re-introducing the exact hole the item exists to close - left the drill
# 62/62 GREEN. Chasing that mutation found a live bug of the same shape in the shipped
# hook: the gitlink check returned early whenever no submodule was staged, which with a
# spliced attester would have attested gitlink bumps ONLY and made the guard refuse every
# ordinary commit in the repo. Neither was visible to a reassembled fixture, and neither
# was found by reading. Only pre-commit is a stub here (its five PowerShell validations
# need the real repo and none of them is what this guard depends on).
#
# CONCURRENCY. Two agents run this at once as a matter of course - a tester and a reviewer
# are the normal case in this factory - so every path this script touches is unique to the
# run: the scratch root carries PID + ticks + a random suffix, nothing under $env:TEMP is
# matched by pattern, and the only removal is of this run's own root. It does not take a
# lease and does not need one: it never touches the real repo (it only READS .githooks) and
# never touches a container or a plane.

[CmdletBinding()]
param([switch]$Audit, [switch]$Json)

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here

$repo = (& git.exe rev-parse --show-toplevel 2>$null | Select-Object -First 1)
if (-not $repo) { Write-Host "not inside a git repository" -ForegroundColor Red; exit 2 }
$hookSrc   = Join-Path $repo ".githooks/reference-transaction"
$libSrc    = Join-Path $repo ".githooks/attest-lib.sh"
$commitMsg = Join-Path $repo ".githooks/commit-msg"

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

# RUN-UNIQUE SCRATCH ROOT. $PID alone is not enough: Windows reuses process ids, and the
# harness starts each run as a fresh powershell.exe, so two runs a few minutes apart can
# collide - and the old code deleted whatever it found at that path before starting, which
# turns a collision into a peer's drill being torn down mid-flight and going red on correct
# code. Ticks + a random suffix make the name unique per INVOCATION, not per process.
function New-ScratchRootName {
    return ("cpg-drill-{0}-{1}-{2}" -f $PID, [DateTime]::UtcNow.Ticks, (Get-Random -Maximum 1000000))
}

# --- fixture ----------------------------------------------------------------------
Step 0 "fixture: the REAL hook, the REAL attestation block, a run-unique scratch root"
Check "the guard hook exists" (Test-Path $hookSrc)
if (-not (Test-Path $hookSrc)) { exit 1 }
Check "the shared attestation library exists" (Test-Path $libSrc) "attest-lib.sh"
if (-not (Test-Path $libSrc)) { exit 1 }
$hookText = Get-Content -Raw -Path $hookSrc
Check "the hook is LF-only (CRLF breaks it under Git Bash)" (-not ($hookText -match "`r"))
$libText = Get-Content -Raw -Path $libSrc
Check "the library is LF-only" (-not ($libText -match "`r"))

# The attester is commit-msg, NOT pre-commit, and that ORDERING IS the mechanism: pre-commit
# runs first, so a tree it attested survived a commit-msg refusal and the immediate
# --no-verify retry was waved through.
#
# THE FIXTURE COPIES commit-msg VERBATIM. An earlier version REASSEMBLED it - extracting the
# attestation block by marker and pasting it after a hand-written veto - and that fixture
# was blind by construction to the two things that matter most about this file: where the
# attestation sits relative to the refusal, and whether the code above it exits early. A
# mutation that moved the block ABOVE the veto (i.e. re-introduced the exact shipped defect)
# left the drill at 62/62 green. Chasing that revealed a live bug of the same shape: the
# gitlink check `exit 0`d whenever no submodule was staged, so a spliced-in attester would
# have run on gitlink bumps only and the guard would have refused every ordinary commit.
# Verbatim is the only fixture that can see either. Step 11 drives it with a REAL submodule
# and a REAL bogus SHA, so the veto under test is the shipped one.
$cmLines = @(Get-Content -Path $commitMsg)
$marker = $cmLines | Select-String -SimpleMatch "--- ATTESTATION" | Select-Object -First 1
Check "the attestation block is in commit-msg, the LAST veto hook" ([bool]$marker) "marker: '--- ATTESTATION'"
if (-not $marker) { exit 1 }
$attestText = ($cmLines[($marker.LineNumber - 1)..($cmLines.Count - 1)] -join "`n")
Check "the block writes the ledger and records BOTH tree and message" `
      (($attestText -match 'hook-attest\.log') -and ($attestText -match 'git write-tree') -and ($attestText -match '_attest_digest_file'))
# Cheap structural companions to the behavioural test in step 11: the ledger must not be
# written anywhere above the marker, and no refusal may follow it.
$ledgerWrites = @($cmLines | Select-String -SimpleMatch "hook-attest.log")
Check "commit-msg touches the ledger ONLY inside that block (nothing attests earlier)" `
      (@($ledgerWrites | Where-Object { $_.LineNumber -lt $marker.LineNumber }).Count -eq 0)
$lastRefusal = $cmLines | Select-String -Pattern '^\s*exit 1\s*$' | Select-Object -Last 1
Check "every refusal in commit-msg comes BEFORE the attestation block" `
      ((-not $lastRefusal) -or ($lastRefusal.LineNumber -lt $marker.LineNumber)) `
      ("refusal at " + $(if ($lastRefusal) { $lastRefusal.LineNumber } else { "none" }) + ", block at " + $marker.LineNumber)
$pcText = Get-Content -Raw -Path (Join-Path $repo ".githooks/pre-commit")
Check "pre-commit no longer attests (attesting before commit-msg was the hole)" `
      (-not ($pcText -match '>>\s*"\$_git_common/hook-attest\.log"'))

$rootA = New-ScratchRootName; $rootB = New-ScratchRootName
Check "the scratch root name is unique per invocation (two agents can drill at once)" `
      ($rootA -ne $rootB) ("e.g. " + $rootA)

$root = Join-Path $env:TEMP $rootA
$null = New-Item -ItemType Directory -Force -Path (Join-Path $root "hooks")
$hooks = Join-Path $root "hooks"

# LF, no BOM - these are shell scripts run by Git Bash.
function Write-Sh([string]$path, [string[]]$lines) {
    $text = ($lines -join "`n") + "`n"
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
}
Copy-Item $hookSrc (Join-Path $hooks "reference-transaction")
Copy-Item $libSrc  (Join-Path $hooks "attest-lib.sh")
Write-Sh (Join-Path $hooks "pre-commit") @('#!/bin/sh', 'echo "PRECOMMIT-STUB"', 'exit 0')
Write-Sh (Join-Path $hooks "pre-merge-commit") @('#!/bin/sh', 'exec "$(dirname "$0")/pre-commit"')
Copy-Item $commitMsg (Join-Path $hooks "commit-msg")

$work = Join-Path $root "repo"
function G {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return (& git.exe -C $work @args 2>&1) } finally { $ErrorActionPreference = $prev }
}
function Head { return ((& git.exe -C $work rev-parse HEAD 2>$null) | Select-Object -First 1) }
function TreeOf([string]$rev) { return ((& git.exe -C $work rev-parse "$rev^{tree}" 2>$null) | Select-Object -First 1) }
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
$guardLog  = Join-Path $work ".git/hook-guard.log"
$ledgerPath = Join-Path $work ".git/hook-attest.log"
function DenyCount { if (Test-Path $guardLog) { return @(Select-String -Path $guardLog -SimpleMatch " DENY ").Count } else { return 0 } }
function SkipCount { if (Test-Path $guardLog) { return @(Select-String -Path $guardLog -SimpleMatch " SKIP-SEQUENCER ").Count } else { return 0 } }
function LogLineCount { if (Test-Path $guardLog) { return @(Get-Content $guardLog).Count } else { return 0 } }
# "Is this tree in the ledger at all?" - i.e. would check-hook-attestation.ps1 accept the
# commit at the submit gate. That question is what separates a route that is merely
# unlogged from one that is genuinely invisible.
function Attested([string]$tree) {
    if (-not (Test-Path $ledgerPath)) { return $false }
    return [bool](@(Select-String -Path $ledgerPath -Pattern ("^" + $tree + " ")).Count)
}
function Touch([string]$name, [string]$content) {
    [System.IO.File]::WriteAllText((Join-Path $work $name), $content + "`n", (New-Object System.Text.UTF8Encoding($false)))
}

# --- GREEN: honest work must pass ---------------------------------------------------
Step 1 "honest work passes (the false-positive half)"
Touch "a.txt" "a"; $null = G add a.txt
$r = Commit "honest one"
Check "first honest commit lands (the ledger is created by the hook itself)" ($r.exit -eq 0 -and (Head))
Check "the ledger has an entry" (Test-Path $ledgerPath)
# The ledger line is (tree, message-digest, timestamp, branch). On an unborn HEAD an earlier
# version wrote "HEAD" AND "?" on separate lines, because `rev-parse --abbrev-ref HEAD`
# prints to stdout AND exits non-zero, so both sides of a `|| echo '?'` ran.
$firstLine = (Get-Content $ledgerPath | Select-Object -First 1)
Check "the ledger line is well formed: <tree> <msg-digest> <iso> <branch>" `
      ($firstLine -match '^[0-9a-f]{40} [0-9a-f]{40} \S+ \S+$') ("line: " + $firstLine)

Step 1.5 "a PARTIAL commit is attested against the tree git actually commits"
# `git commit -- path` hands the hooks a TEMPORARY index. If the attester read the wrong
# index the pair would never match and every partial commit would be refused.
Touch "p1.txt" "p1"; Touch "p2.txt" "p2"; $null = G add p1.txt p2.txt; $null = G reset -q p2.txt
Touch "p1.txt" "p1-changed"
$before = Head
$out = G commit -q -m "partial commit" -- p1.txt
Check "a path-limited commit with other content unstaged lands" ($LASTEXITCODE -eq 0 -and (Head) -ne $before) ("exit=" + $LASTEXITCODE)
$null = G reset -q

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
# RESOLVING sh.exe IS PATH-DEPENDENT, and the first version of this step got it wrong in a
# way that only showed up in one shell. Under a PowerShell/cmd PATH git.exe is
# ...\Git\cmd\git.exe, so `Split-Path (Split-Path $git)` lands on ...\Git and finds
# usr\bin\sh.exe. Under GIT BASH's PATH the same command resolves to
# ...\Git\mingw64\bin\git.exe, the two Split-Paths land on ...\Git\mingw64, there is no
# sh.exe under it, and the drill exited 1 with a FAIL for a NON-GUARD reason. It failed
# closed, but a tester reproducing from bash saw red and had every reason to blame the
# guard. Walk the ancestors of every root git tells us about instead of assuming a depth.
function Resolve-PosixSh {
    $cands = @()
    $direct = (Get-Command sh.exe -ErrorAction SilentlyContinue)
    if ($direct) { $cands += $direct.Source }
    $roots = @()
    $gitExe = (Get-Command git.exe -ErrorAction SilentlyContinue).Source
    if ($gitExe) { $roots += (Split-Path $gitExe) }
    $ep = (& git.exe --exec-path 2>$null | Select-Object -First 1)
    if ($ep) { $roots += ($ep -replace '/', '\') }
    $seen = @{}
    foreach ($r in $roots) {
        $d = $r
        for ($i = 0; $i -lt 6 -and $d; $i++) {
            if (-not $seen.ContainsKey($d)) {
                $seen[$d] = $true
                foreach ($rel in @("usr\bin\sh.exe", "bin\sh.exe")) {
                    $p = Join-Path $d $rel
                    if (Test-Path $p) { $cands += $p }
                }
            }
            $d = Split-Path $d
        }
    }
    return ($cands | Where-Object { $_ } | Select-Object -First 1)
}
$shExe = Resolve-PosixSh
Check "a POSIX sh was located to drive the hook directly (PATH-independently)" ([bool]$shExe) ("sh=" + $shExe)
if ($shExe) {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $fake = "0000000000000000000000000000000000000000 " + (Head) + " refs/heads/main"
    Push-Location $work
    try {
        $null = ($fake | & $shExe (Join-Path $hooks "reference-transaction") committed 2>&1)
        $stateCommitted = $LASTEXITCODE
        # ... and the SAME shape at `prepared`, carrying a tree that was never attested, must
        # be judged. Without this pair the case above proves only that the hook did nothing.
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
    } finally { Pop-Location; $ErrorActionPreference = $prev }
    Check "state=committed exits 0 without judging anything" ($stateCommitted -eq 0) ("exit=" + $stateCommitted)
    Check "state=prepared REFUSES the same shape when the tree is unattested" `
          ($statePrepared -ne 0) ("exit=" + $statePrepared)
}

# --- THE HOLE THIS ITEM WAS RETURNED FOR --------------------------------------------
Step 11 "the RETRY after a commit-msg refusal is REFUSED (the ordering hole)"
# THE DEFECT, reproduced by two reviewers and by this drill before the fix: attestation was
# written by pre-commit, the FIRST hook. A commit whose MESSAGE commit-msg refused had
# already had its tree attested, and the ledger entry survived the abort - so the immediate
# `--no-verify` retry with the identical message LANDED, past the guard and with no
# guard-log line. Unstopped AND unaudited, on the exact channel (the commit message) that
# PLAN.md section C.7 makes the operator's audit surface.
#
# THE VETO IS THE REAL ONE. commit-msg only refuses when a submodule gitlink is staged and
# the message names a SHA that does not resolve, so the fixture stages a real submodule and
# uses the real shape of the message that caused this hook to exist ("OB1 -> 5a54f18,
# pushed before this bump", where the SHA was typed from memory). A hand-written stand-in
# veto would have proved the drill's own stub, not this repo's gate.
$null = G checkout -q main
$sub = Join-Path $root "sub"
$null = & git.exe init -q -b main $sub 2>&1
$null = & git.exe -C $sub config user.email drill@local 2>&1
$null = & git.exe -C $sub config user.name drill 2>&1
[System.IO.File]::WriteAllText((Join-Path $sub "s.txt"), "s`n", (New-Object System.Text.UTF8Encoding($false)))
$null = & git.exe -C $sub add s.txt 2>&1
$null = & git.exe -C $sub -c core.hooksPath=/nonexistent commit -q -m "sub base" 2>&1
$null = G -c protocol.file.allow=always submodule add --quiet $sub sub
$subStaged = @(G diff --cached --raw) -join "`n"
Check "a real submodule gitlink is staged (so the real commit-msg gate is armed)" `
      ($subStaged -match '160000') ("staged: " + $(if ($subStaged -match '160000') { "gitlink" } else { $subStaged }))
$before = Head
$badMsg = "OB1 -> deadbee1, pushed before this bump"
$r = Commit $badMsg
Check "the REAL commit-msg refuses a message naming a SHA that does not exist (HEAD unmoved)" `
      ($r.exit -ne 0 -and (Head) -eq $before) ("exit=" + $r.exit)
$stagedTree = ((& git.exe -C $work write-tree 2>$null) | Select-Object -First 1)
Check "and the refused tree was NOT attested (nothing survives the abort)" `
      (-not (Attested $stagedTree)) ("tree=" + $stagedTree)
$denyBefore = DenyCount
$r = Commit $badMsg -NoVerify
Check "the --no-verify RETRY with the identical message is REFUSED" `
      ($r.exit -eq 128 -and (Head) -eq $before) ("exit=" + $r.exit)
Check "and the retry IS audited (this route used to write no line at all)" `
      ((DenyCount) -eq ($denyBefore + 1)) ("deny=" + (DenyCount))
# ... and the honest bump of the SAME gitlink, naming the SHA that does exist, lands. The
# refusal above must be of the claim, not of gitlink bumps in general.
$subHead = ((& git.exe -C $sub rev-parse HEAD 2>$null) | Select-Object -First 1)
$r = Commit ("OB1 -> " + $subHead.Substring(0, 10) + ", pushed before this bump")
Check "the honest bump naming the REAL SHA lands" ($r.exit -eq 0 -and (Head) -ne $before) ("exit=" + $r.exit)
$null = G reset -q

Step 11.5 "a message-only --amend --no-verify is REFUSED"
# The same hole in its other shape: the tree is unchanged and already attested, so a
# tree-only ledger let the message be rewritten to claim anything - including a gitlink SHA
# that does not exist, which is the rule commit-msg exists to enforce.
Touch "am.txt" "am"; $null = G add am.txt; $null = Commit "honest subject, honest content"
$before = Head
$beforeTree = TreeOf "HEAD"
$denyBefore = DenyCount
$null = G commit -q --amend --no-verify -m "rewritten claim: OB1 -> deadbee1"
Check "message-only amend bypass exits 128" ($LASTEXITCODE -eq 128) ("exit=" + $LASTEXITCODE)
Check "HEAD did NOT move" ((Head) -eq $before)
Check "the tree IS attested, so only the message can have been refused" (Attested $beforeTree)
Check "the record names the cause: reason=message-not-attested" `
      ([bool](Test-Path $guardLog) -and @(Select-String -Path $guardLog -SimpleMatch "reason=message-not-attested").Count -ge 1)

# --- THE BOUNDARY, MEASURED ---------------------------------------------------------
Step 12 "the documented BOUNDARY is measured, not asserted"
# .githooks/README.md and the hook header enumerate the routes that survive. Claims in prose
# are how a guard ends up oversold - the shipped version said "two routes survive" and a
# reviewer measured three more - so every entry in that table is executed here, and for each
# one the drill records the three facts an operator actually needs: does it LAND, is a
# guard-log line written, and is the resulting commit still UNATTESTED (which is what
# check-hook-attestation.ps1 reads at the submit gate, and queue.ps1 -Submit runs it).
$null = G checkout -q main

# (a) a MISSING ledger must fail CLOSED, not open. The obvious guess - "delete the ledger
#     and the guard has nothing to check against" - is wrong, and being wrong in the safe
#     direction is worth a check.
Move-Item $ledgerPath ($ledgerPath + ".bak") -Force
$before = Head
Touch "noledger.txt" "x"; $null = G add noledger.txt
$r = Commit "no ledger" -NoVerify
Check "a missing ledger DENIES (fails closed)" ($r.exit -eq 128 -and (Head) -eq $before) ("exit=" + $r.exit)
Check "and it is recorded as reason=no-ledger" `
      ([bool](Test-Path $guardLog) -and @(Select-String -Path $guardLog -SimpleMatch "reason=no-ledger").Count -ge 1)
Move-Item ($ledgerPath + ".bak") $ledgerPath -Force

# (R1) the operator's escape hatch, which is also the honest admission: turning every hook
#      off DOES land. There is no privilege boundary here to prevent it.
$before = Head; $linesBefore = LogLineCount
$null = G -c core.hooksPath=/nonexistent commit -q -m "R1 hooks off entirely"
$r1Landed = ($LASTEXITCODE -eq 0 -and (Head) -ne $before)
Check "R1 core.hooksPath=/nonexistent LANDS (the documented escape, and the honest limit)" `
      $r1Landed ("exit=" + $LASTEXITCODE)
Check "R1 writes NO guard line, and leaves the commit UNATTESTED (caught at the submit gate)" `
      ((LogLineCount) -eq $linesBefore -and -not (Attested (TreeOf "HEAD")))

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

# (R2) ... and the residual that is deliberately left open, because closing it would start
#      refusing an honest `git checkout -b rel <tag>`.
$null = G update-ref refs/pre/parked $pcommit
$parkExit = $LASTEXITCODE
$null = G update-ref refs/heads/plumbed $pcommit
Check "R2 parking under a NON-refs/heads ref first LANDS (known residual)" `
      ($parkExit -eq 0 -and $LASTEXITCODE -eq 0) ("park=" + $parkExit + " move=" + $LASTEXITCODE)

# (R5) forging the sequencer state. The exemption itself is not optional - git runs no
#      pre-commit for replayed commits - and a hook that can only read .git cannot tell a
#      forged CHERRY_PICK_HEAD from a real one. It is priced, not closed: it costs an extra
#      command, it writes a SKIP-SEQUENCER line, and the commit is still UNATTESTED.
$before = Head; $skipBefore = SkipCount
[System.IO.File]::WriteAllText((Join-Path $work ".git/CHERRY_PICK_HEAD"), $before + "`n", (New-Object System.Text.UTF8Encoding($false)))
Touch "forged.txt" "forged"; $null = G add forged.txt
$r = Commit "R5 forged sequencer state" -NoVerify
Remove-Item (Join-Path $work ".git/CHERRY_PICK_HEAD") -Force -ErrorAction SilentlyContinue
Check "R5 a forged CHERRY_PICK_HEAD LANDS" ($r.exit -eq 0 -and (Head) -ne $before) ("exit=" + $r.exit)
Check "R5 is logged SKIP-SEQUENCER and leaves the commit UNATTESTED (caught at the submit gate)" `
      ((SkipCount) -gt $skipBefore -and -not (Attested (TreeOf "HEAD")))
$null = G reset -q --hard $before

# (R6) the one --no-verify shape that survives on purpose: nothing changed, so the
#      attestation genuinely covers the new commit object. It re-parents and re-dates a
#      commit; it cannot alter what the commit says or what it holds.
#      A DIFFERENT COMMITTER DATE IS REQUIRED, and its absence made this step flaky. An
#      amend that changes nothing AND lands in the same second reproduces the identical
#      commit object, so `git commit --amend --no-edit` is a no-op: HEAD does not move,
#      exit is 0, and the ref transaction never judges anything. The check then passed or
#      failed on the clock. Pinning the date guarantees a NEW object to judge.
Touch "r6.txt" "r6"; $null = G add r6.txt; $null = Commit "R6 honest base"
$before = Head; $linesBefore = LogLineCount
$env:GIT_COMMITTER_DATE = "2030-01-01T00:00:00Z"
$null = G commit -q --amend --no-verify --no-edit
$r6Exit = $LASTEXITCODE
Remove-Item env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue
# CAPTURE THE EXIT CODE FIRST. PowerShell evaluates the detail string after the condition,
# and the condition calls Head, which runs git and resets $LASTEXITCODE - so a check that
# passed printed "exit=0" for a refusal. The verdict was right and the evidence beside it
# was wrong, which is the worse half of that pair.
Check "R6 --amend --no-verify --no-edit of an ATTESTED commit LANDS (identical tree AND message)" `
      ($r6Exit -eq 0 -and (Head) -ne $before) ("exit=" + $r6Exit)
Check "R6 the tree stays ATTESTED and no guard line is written" `
      ((TreeOf "HEAD") -eq (TreeOf $before) -and (Attested (TreeOf "HEAD")) -and (LogLineCount) -eq $linesBefore)
# R6 IS BOUNDED, and that bound is the load-bearing part - it must not become a laundry for
# a commit that got in through R1/R3/R5. The same shape on an UNATTESTED commit is refused,
# because the attestation is of the tree, not of the act of amending.
$attestedTip = Head
# A REAL change, not --allow-empty: amending an empty commit makes git itself exit 1 with
# "nothing to commit", which looks like a refusal and is not one. The first version of this
# check read that 1 as the guard firing.
Touch "r6u.txt" "r6u"; $null = G add r6u.txt
$null = G -c core.hooksPath=/nonexistent commit -q -m "unattested tip"
$before = Head
$env:GIT_COMMITTER_DATE = "2030-01-02T00:00:00Z"
$null = G commit -q --amend --no-verify --no-edit
$r6uExit = $LASTEXITCODE
Remove-Item env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue
Check "R6 does NOT launder: the same shape on an UNATTESTED commit is REFUSED" `
      ($r6uExit -eq 128 -and (Head) -eq $before) ("exit=" + $r6uExit)
$null = G reset -q --hard $attestedTip

# (R4) THE GUARD IS PER-WORKTREE. core.hooksPath is `.githooks`, a RELATIVE path resolved
#      against each worktree's own top level, so a worktree whose branch does not carry the
#      file is unguarded - one repo, one config, opposite outcomes. Measured in a separate
#      repo configured exactly the way the real one is.
$bare = Join-Path $root "nohooks"
$null = & git.exe init -q -b main $bare 2>&1
$null = & git.exe -C $bare config core.hooksPath .githooks 2>&1
$null = & git.exe -C $bare config user.email drill@local 2>&1
$null = & git.exe -C $bare config user.name drill 2>&1
[System.IO.File]::WriteAllText((Join-Path $bare "n.txt"), "n`n", (New-Object System.Text.UTF8Encoding($false)))
$null = & git.exe -C $bare add n.txt 2>&1
$null = & git.exe -C $bare commit -q --no-verify -m "R4 no hooks in this tree" 2>&1
$r4Exit = $LASTEXITCODE
$r4Head = ((& git.exe -C $bare rev-parse HEAD 2>$null) | Select-Object -First 1)
Check "R4 a tree without .githooks/ is unguarded: --no-verify LANDS there" `
      ($r4Exit -eq 0 -and $r4Head) ("exit=" + $r4Exit)
Check "R4 writes no guard line (its git dir has none)" (-not (Test-Path (Join-Path $bare ".git/hook-guard.log")))

# --- NEGATIVE CONTROL + R3 -----------------------------------------------------------
Step 13 "NEGATIVE CONTROL: with the hook removed, the same bypass SUCCEEDS (this is also R3)"
# Two things at once, and they are the same operation: it proves the earlier RED checks were
# measuring THIS hook and not something incidental, and it measures the cheapest surviving
# route - deleting the guard file, which core.hooksPath being a relative in-tree path makes
# possible in two words.
Remove-Item (Join-Path $hooks "reference-transaction") -Force
$null = G checkout -q main
$before = Head; $linesBefore = LogLineCount
Touch "neg.txt" "neg"; $null = G add neg.txt
$r = Commit "R3 bypass with the guard removed" -NoVerify
Check "the bypass lands when the guard is gone (so the RED steps measured the guard)" `
      ($r.exit -eq 0 -and (Head) -ne $before) ("exit=" + $r.exit)
Check "R3 writes NO guard line, and leaves the commit UNATTESTED (caught at the submit gate)" `
      ((LogLineCount) -eq $linesBefore -and -not (Attested (TreeOf "HEAD")))

Step 14 "the documentation states this drill's REAL size"
# Both READMEs shipped saying "33 checks" while the drill emitted 38. A number in prose next
# to evidence is a claim like any other, and a stale one invites a reader to assume the rest
# of the page is stale too - so it is checked rather than remembered. +1 because this check
# is not in $results until Check returns.
#
# THE PHRASE IS THE CONTRACT. A bare `(\d+) checks` regex is not usable here: this harness
# README also documents verify-merge-protocol.ps1's own count on the line above, and the
# .githooks page wrote it as "33-check drill", which such a regex misses entirely - a
# doc-drift check that cannot see the drifted number is exactly the vacuous check this repo
# keeps finding. Both pages must carry the literal phrase below, once each.
$expectedCount = $results.Count + 1
$docs = @(".githooks/README.md", "scripts/agent-harness/README.md")
$badDocs = @()
foreach ($d in $docs) {
    $full = Join-Path $repo $d
    if (-not (Test-Path $full)) { $badDocs += ($d + " missing"); continue }
    $text = Get-Content -Raw -Path $full
    $ms = [regex]::Matches($text, 'commit-path drill runs (\d+) checks')
    if ($ms.Count -ne 1) { $badDocs += ($d + " carries " + $ms.Count + " copies of the phrase, expected 1"); continue }
    if ([int]$ms[0].Groups[1].Value -ne $expectedCount) { $badDocs += ($d + " says " + $ms[0].Groups[1].Value) }
}
Check ("both READMEs state this drill's real check count (" + $expectedCount + ")") `
      ($badDocs.Count -eq 0) ($badDocs -join "; ")

# --- report --------------------------------------------------------------------------
# NO Set-Location. It is SESSION-scoped in PowerShell, so parking the caller in $env:TEMP to
# make a delete succeed silently relocates whoever invoked this - the same class of leak
# that check-hook-attestation.ps1 documents at its own param block. Nothing in this script
# ever makes $root the current directory (step 10's Push-Location is paired in a finally),
# so the delete needs no help.
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
