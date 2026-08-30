# verify-merge-protocol.ps1 - executable proof of MERGE-PROTOCOL.md's pipeline.
#
#   .\scripts\worktree\verify-merge-protocol.ps1     # ~1 min, cleans up after itself
#
# Run after changing queue.ps1, lease.ps1, the worktree scripts, or the protocol. The unit
# tests cover SEMANTICS; this covers the CHOREOGRAPHY the roles actually perform - which is
# where the protocol's real defects have turned out to be (its first run proved the old
# Step 4's `git checkout development` was unsafe).
#
# What it drives: two developers produce conflicting work in isolated worktrees; both queue
# it with test plans; a tester who wrote neither executes and passes them; a reviewer who is
# neither developer merges the first; the second's rebase CONFLICTS, is adapted, and because
# the rebase changed the tested content the reviewer returns it to test (the stale-pass rule)
# before it can land. Separation of duties is asserted, not assumed.
#
# Runs against a SCRATCH line (drill/verify-d), never `development`. The operator's checkout
# is never switched (a bridge turn could land mid-switch). Idempotent: the preamble clears
# anything a previous failed run left behind.
#
# SINGLE FLIGHT (added 2026-08-30, after this drill was measured at 2/8 green). Everything
# above is only true of ONE running copy. This is the one component in the toolkit that
# mutates shared git state under FIXED global names - it creates and force-deletes
# `drill/verify-d`, `work/drilla`, `work/drillb` and three worktree paths in the OPERATOR'S
# checkout, and its preamble deletes them unconditionally so a previous crash cannot wedge
# it. Two copies therefore destroy each other. Measured burst, 8 consecutive runs on a
# machine where other agents were also running the harness: 66, 66, 63, 59, 39, 34, 40 of 66
# - and a second `verify-merge-protocol.ps1` (pid 137560) was caught running concurrently in
# `Get-CimInstance Win32_Process` mid-burst. Worse than the noise, the collision left the
# operator's checkout MID-REBASE, because `git -C <path>` ASCENDS when <path> is not a
# worktree root: once a leftover plain directory sits at .claude\worktrees\wt-drillb, every
# `git -C $wtB ...` below operates on the main checkout instead. So there are two guards,
# and they are different guards: a LEASE stops a second copy starting, and
# Test-IsWorktreeRoot stops a failed provision from redirecting git at the operator.
#
# lease-names.conf says "git state needs no lease (the worktree is the isolation)". That is
# right for every other caller and wrong here: there is no worktree to isolate a run whose
# job is to CREATE worktrees. `merge-protocol-drill` is listed there for this reason.
#
#   -LockProbe   take the single-flight decision, print it, and exit WITHOUT touching
#                anything. Exit 0 = would have run, 3 = another copy holds it. This is how
#                test_drill_single_flight.py exercises the guard without a 90-second run.
#
# NOTE: no `2>&1` on any git call, and the helpers flip $ErrorActionPreference themselves. In
# PS5.1, redirecting OR capturing a native command's stderr under 'Stop' turns git's ordinary
# progress chatter into a terminating error - this script died on exactly that once.

[CmdletBinding()]
param([switch]$LockProbe)

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here
# The toolkit is wherever THIS script is - it is part of the module. Rebuilding the path
# from the repo root hardcoded the directory name, which broke the whole drill the moment
# the module was renamed (2026-08-28) while every message still said "not recognized".
$wtScripts = $PSScriptRoot
$queue = Join-Path $wtScripts "queue.ps1"
. (Join-Path $wtScripts "common.ps1")
# Asked, not assumed: the drill used to carry this machine's absolute path as a literal,
# which is the kind of value that makes a toolkit non-portable for no benefit.
$repo = Get-MainCheckout
if (-not $repo) { throw "cannot locate the main checkout - run this from inside the repository" }
$QueueDir = Join-Path (Get-SharedStateDir) "queue"
$results = @()

# ---- single flight: refuse to start rather than corrupt a run already in progress -------
# Held for the whole drill and released at the summary. A crashed run does NOT strand it:
# lease.ps1 expires it after the TTL and `lease.ps1 -Takeover -Name merge-protocol-drill`
# reclaims an EXPIRED one only, so the recovery path cannot be used to jump a live run.
$LeaseScript = Join-Path $wtScripts "lease.ps1"
$LeaseName = "merge-protocol-drill"
$LeaseOwner = "verify-merge-protocol-$PID"
$LeaseHeld = $false
function Release-DrillLease {
    if ($script:LeaseHeld) {
        & $LeaseScript -Release -Name $LeaseName -Owner $LeaseOwner | Out-Null
        $script:LeaseHeld = $false
    }
}
& $LeaseScript -Acquire -Name $LeaseName -Owner $LeaseOwner -TtlMin 20 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "REFUSED: another copy of this drill is running (lease '$LeaseName' is held)." -ForegroundColor Yellow
    Write-Host "  This drill deletes and recreates FIXED branch and worktree names in the" -ForegroundColor DarkGray
    Write-Host "  operator's checkout, so two copies destroy each other's run and can leave" -ForegroundColor DarkGray
    Write-Host "  the main checkout mid-rebase. Wait, or:  lease.ps1 -Status" -ForegroundColor DarkGray
    exit 3
}
$LeaseHeld = $true
if ($LockProbe) {
    Write-Host "LOCK PROBE: acquired '$LeaseName' - a real run would proceed. Nothing was touched." -ForegroundColor Green
    Release-DrillLease
    exit 0
}

function Step($n, $text) { Write-Host "`n=== $n. $text ===" -ForegroundColor Cyan }
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = $ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
# Helpers must call git.EXE: PowerShell is case-insensitive, so `& git` inside a function
# named Git calls itself (call-depth overflow), and functions leak into invoked scripts -
# a helper named `Git` once shadowed the real binary INSIDE new-worktree.ps1.
function Invoke-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & git.exe @args | Out-Null } finally { $ErrorActionPreference = $prev }
}
function Get-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return (& git.exe @args) } finally { $ErrorActionPreference = $prev }
}
function Get-QueueState([string]$id) {
    $f = Join-Path $QueueDir "$id.json"
    if (-not (Test-Path $f)) { return "(missing)" }
    return (Get-Content -Raw -Path $f | ConvertFrom-Json).state
}
function Clear-DrillQueue {
    Get-ChildItem -Path $QueueDir -Filter "drill-*" -ErrorAction SilentlyContinue |
        ForEach-Object { Remove-Item $_.FullName -Force }
}

Set-Location $repo

# --- idempotent preamble ------------------------------------------------------------
foreach ($p in @("merge-line", "wt-drilla", "wt-drillb")) {
    $full = Join-Path $repo ".claude\worktrees\$p"
    if (Test-Path $full) { & git.exe worktree remove --force $full | Out-Null }
}
& git.exe worktree prune | Out-Null
foreach ($b in @("work/drilla", "work/drillb", "drill/verify-d")) {
    & git.exe rev-parse --verify --quiet "refs/heads/$b" | Out-Null
    if ($LASTEXITCODE -eq 0) { & git.exe branch -D $b | Out-Null }
}
Clear-DrillQueue
# Drop ONLY this drill's rows. This used to reset the whole file, which silently
# destroyed the registry rows of real agents working alongside it - the drill wiped
# `wt-search-readme` and `wt-coder-readme` mid-run, so their own developers could no
# longer retire their worktrees by id. A test fixture that clobbers shared state is
# the exact failure this toolkit exists to prevent, and it was mine.
$reg = Join-Path (Get-SharedStateDir) "worktrees.json"
if (Test-Path $reg) {
    try {
        $rows = (Get-Content -Raw -Path $reg | ConvertFrom-Json).worktrees
        $keep = [ordered]@{}
        if ($rows) {
            foreach ($prop in $rows.PSObject.Properties) {
                if ($prop.Name -notin @("drilla", "drillb")) { $keep[$prop.Name] = $prop.Value }
            }
        }
        (@{ worktrees = $keep } | ConvertTo-Json -Depth 6) | Set-Content $reg -Encoding ASCII
    } catch { }   # unreadable registry: leave it alone rather than truncate someone else's state
}

$devBefore = (Get-DrillGit rev-parse development).Trim()
$mainBranch = (Get-DrillGit rev-parse --abbrev-ref HEAD).Trim()
Write-Host ("development before: {0} | operator checkout on: {1}" -f $devBefore.Substring(0, 8), $mainBranch)

Step 1 "two developers, two isolated worktrees (via the real provisioning script)"
Invoke-DrillGit branch drill/verify-d development
foreach ($id in @("drilla", "drillb")) {
    & (Join-Path $wtScripts "new-worktree.ps1") -Id $id -Base "drill/verify-d" -OwnerKind manual -OwnerRef "verify-d" | Out-Null
    Check "worktree wt-$id provisioned" (Test-Path (Join-Path $repo ".claude\worktrees\wt-$id"))
}
$wtA = Join-Path $repo ".claude\worktrees\wt-drilla"
$wtB = Join-Path $repo ".claude\worktrees\wt-drillb"
# Test-Path above says the DIRECTORY exists; it does not say git will treat it as a working
# tree. If provisioning half-failed and left a plain directory, `git -C $wtA ...` ascends to
# the operator's checkout and every mutation below - up to and including `rebase` - lands
# there. That happened. Stop here instead: a wrong answer from this drill is recoverable, a
# rebase in the operator's checkout is the thing the whole toolkit exists to prevent.
foreach ($p in @($wtA, $wtB)) {
    if (-not (Test-IsWorktreeRoot $p)) {
        Write-Host ""
        Write-Host "ABORT: '$p' is not a git worktree root." -ForegroundColor Red
        Write-Host "  git -C would ASCEND from there to the main checkout, so continuing would" -ForegroundColor DarkGray
        Write-Host "  run this drill's commits, rebases and branch deletions in the operator's" -ForegroundColor DarkGray
        Write-Host "  tree. Remove the leftover path and re-run." -ForegroundColor DarkGray
        Release-DrillLease
        exit 1
    }
}

Step 2 "both edit THE SAME file with conflicting intent, and commit"
Set-Content -Path (Join-Path $wtA "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "", "Agent A owns this line: A needs the timeout raised to 60s.")
Set-Content -Path (Join-Path $wtB "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "", "Agent B owns this line: B needs the timeout lowered to 5s.")
Invoke-DrillGit -C $wtA add DRILL-NOTE.md
Invoke-DrillGit -C $wtA commit -q -m "drill A: raise timeout to 60s"
Invoke-DrillGit -C $wtB add DRILL-NOTE.md
Invoke-DrillGit -C $wtB commit -q -m "drill B: lower timeout to 5s"
Check "two divergent commits exist" ((Get-DrillGit -C $wtA rev-parse HEAD) -ne (Get-DrillGit -C $wtB rev-parse HEAD))

Step 3 "developers QUEUE their work with a test plan - they never merge it themselves"
# The plan must be a FILE that exists - queue.ps1 now proves it, so the drill writes one
# rather than passing a sentence. That the drill had to change is the contract working.
$planFile = Join-Path $env:TEMP "drill-test-plan.md"
Set-Content -Path $planFile -Encoding ascii -Value @(
    "# Drill test plan",
    "Case 1: DRILL-NOTE.md exists and states a timeout. Pass: it does. Fail: absent or silent.",
    "Case 2: the file names exactly one owner. Pass: one. Fail: contradictory owners.")
# THE ANCHOR GATE comes first: work is agreed before it is built. A submit with no anchor,
# and a submit against an UNCONFIRMED anchor, must both be refused with exit 5.
& $queue -Submit -Id "drill-a" -Branch "work/drilla" -Developer "wt-drilla" -TestPlan $planFile 2>&1 | Out-Null
Check "-Submit with no anchor at all is refused (exit 5)" ($LASTEXITCODE -eq 5)

$anchorFile = Join-Path $env:TEMP "drill-anchor.json"
$vagueAnchor = Join-Path $env:TEMP "drill-anchor-vague.json"
Set-Content -Path $vagueAnchor -Encoding ascii -Value '{ "goal": "make it better", "acceptance": ["ok"] }'
& $queue -Propose -Id "drill-vague" -Anchor $vagueAnchor -Developer "wt-drilla" 2>&1 | Out-Null
Check "an anchor missing its fields is refused" ($LASTEXITCODE -ne 0)

foreach ($id in @("a", "b")) {
    Set-Content -Path $anchorFile -Encoding ascii -Value @(
        "{",
        "  ""goal"": ""DRILL-NOTE.md states one owner and one timeout, unambiguously."",",
        "  ""artifact"": ""DRILL-NOTE.md - a one-line note naming an owner and a timeout."",",
        "  ""audience"": ""The next agent to read the file with no other context."",",
        "  ""acceptance"": [",
        "    ""The file exists and names a timeout value in seconds."",",
        "    ""Exactly one owner is named; two contradictory owners is a failure.""",
        "  ],",
        "  ""out_of_scope"": [ ""Changing anything outside DRILL-NOTE.md."" ],",
        "  ""findings_sink"": ""DRILL-FINDINGS.md""",
        "}")
    & $queue -Propose -Id "drill-$id" -Anchor $anchorFile -Developer "wt-drill$id" | Out-Null
}
Check "both anchors are PROPOSED, not agreed" ((Get-QueueState "drill-a") -eq "anchor-draft")
& $queue -Submit -Id "drill-a" -Branch "work/drilla" -Developer "wt-drilla" -TestPlan $planFile 2>&1 | Out-Null
Check "-Submit before the operator confirms is refused (exit 5)" ($LASTEXITCODE -eq 5)
foreach ($id in @("a", "b")) { & $queue -ConfirmAnchor -Id "drill-$id" -By "operator" | Out-Null }
Check "the operator confirmed both anchors" ((Get-QueueState "drill-a") -eq "anchor-confirmed")
Check "the anchor was COPIED beside the item (survives worktree removal)" `
    (Test-Path (Join-Path $QueueDir "drill-a.anchor.json"))

# --- HOOK-BYPASS CONTAINMENT (U5; PLAN 0 A7) ---------------------------------------
# A7 is FALSIFIED: an agent reached for --no-verify on its first commit, and --no-verify
# leaves NO trace in a git object. A guard is only worth having if it actually fires, so
# this makes a genuinely bypassed commit and proves the queue refuses it - and, just as
# important, proves an HONEST commit is not refused. A guard that flags everything gets
# switched off within a day, and then it protects nothing.
# Runs BEFORE the real submissions so the bypass probe never pollutes drill-a's own flow.
# Resolved from $wtScripts, the code UNDER TEST - the same way $queue is. Pointing at the
# main checkout would test whatever is already merged there, which is never the point of a
# drill run from a work branch.
$attest = Join-Path (Split-Path $wtScripts -Parent) "checks\check-hook-attestation.ps1"
Check "the attestation checker exists" (Test-Path $attest)

# These cases drive the CHECKER against a controlled ledger rather than depending on the
# hook the drill worktree happens to carry: a worktree is created from the main checkout,
# so it inherits the MERGED .githooks, not this branch's. Testing the hook's own recording
# through a worktree that cannot have it would prove nothing. What matters here is that the
# checker's verdict and the queue's response are right; the hook's recording is proven
# separately (see TESTPLAN case 2, which runs the real hook).
# ADOPTION SAFETY is what the drill can honestly cover, and it is the property that decides
# whether this guard survives its first day. The drill's branches are cut from the main
# checkout, so they carry whatever hook is MERGED - which, until this lands, cannot attest.
# The guard must therefore be INACTIVE for them. If it were not, every branch in the repo
# would be blocked the moment this merged, and the guard would be reverted by lunchtime.
& $attest -Branch "work/drilla" -Base "drill/verify-d" -RepoRoot $repo -AllowLedgerOverride | Out-Null
Check "INACTIVE for a branch whose own hook cannot attest (exit 0)" ($LASTEXITCODE -eq 0)

# The base ref must survive INTO the report. This caught a real one: a local $base variable
# silently overwrote the $Base PARAMETER (PowerShell names are case-insensitive), so
# rev-list ran "<filesystem path>..<branch>", matched nothing, and the checker cheerfully
# reported "0 commits, all clean" for every branch. It only reproduced in the main checkout,
# where --git-common-dir is relative - so every worktree test passed and it nearly shipped.
$attestJson = (& $attest -Branch "work/drilla" -Base "drill/verify-d" -RepoRoot $repo -AllowLedgerOverride -Json) | ConvertFrom-Json
Check "the base ref round-trips into the report (not clobbered)" ($attestJson.base -eq "drill/verify-d") ("base=" + $attestJson.base)

& $queue -Propose -Id "drill-bypass" -Anchor $anchorFile -Developer "wt-drilla" | Out-Null
& $queue -ConfirmAnchor -Id "drill-bypass" -By "operator" | Out-Null
& $queue -Submit -Id "drill-bypass" -Branch "work/drilla" -Developer "wt-drilla" -TestPlan $planFile 2>&1 | Out-Null
Check "-Submit is NOT blocked by the inactive guard" ($LASTEXITCODE -eq 0)

# And with no ledger at all it must also stay quiet rather than failing everything.
$env:AI_STACK_ATTEST_LEDGER = Join-Path $env:TEMP "drill-attest-absent.log"
Remove-Item $env:AI_STACK_ATTEST_LEDGER -ErrorAction SilentlyContinue
& $attest -Branch "work/drilla" -Base "drill/verify-d" -RepoRoot $repo -AllowLedgerOverride | Out-Null
Check "no ledger => INACTIVE (exit 0), never a repo-wide block" ($LASTEXITCODE -eq 0)

# THE ENV VAR ALONE MUST NOT DISABLE THE GUARD. It used to: any agent running
# `queue.ps1 -Submit` sets its own environment, so AI_STACK_ATTEST_LEDGER pointing at a
# nonexistent file turned the check off for exactly the party it constrains. The override
# now needs -AllowLedgerOverride, which only this drill passes.
& $attest -Branch "work/drilla" -Base "drill/verify-d" -RepoRoot $repo | Out-Null
$envOnlyExit = $LASTEXITCODE
Remove-Item Env:\AI_STACK_ATTEST_LEDGER -ErrorAction SilentlyContinue
& $attest -Branch "work/drilla" -Base "drill/verify-d" -RepoRoot $repo | Out-Null
Check "AI_STACK_ATTEST_LEDGER alone does NOT change the verdict" ($envOnlyExit -eq $LASTEXITCODE)

# THE ACTIVE CASE, in a throwaway repository of our own.
#
# This used to carry a note saying the active case "cannot be driven from a drill worktree
# until the attesting hook is on the line those worktrees are cut from" - true, and a
# permanent gap, because the drill's branches inherit whatever hooks the main checkout has.
# The way out is not to wait for adoption: build a repository with the history the case
# needs. Three cases, each proven RED before it was proven GREEN.
$mrepo = Join-Path $env:TEMP ("drill-merge-attest-" + $PID)
Remove-Item -Recurse -Force $mrepo -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $mrepo ".githooks") | Out-Null
function Build-AttestRepo([string]$root, [bool]$withMergeHook) {
    # The pre-commit stub MUST mention hook-attest.log. The checker's per-branch gate reads
    # that string out of the branch's own hook, and a stub without it turns the whole check
    # INACTIVE - which is exactly how the first version of these cases passed: all three
    # returned 0 because nothing was ever examined. Two of them were asserting the absence
    # of a failure in a check that had switched itself off.
    Set-Content -Path (Join-Path $root ".githooks\pre-commit") -Value "#!/bin/sh`n# writes hook-attest.log`nexit 0" -Encoding ASCII
    if ($withMergeHook) {
        Set-Content -Path (Join-Path $root ".githooks\pre-merge-commit") -Value "#!/bin/sh`nexec `"`$(dirname `"`$0`")/pre-commit`"" -Encoding ASCII
    }
    Invoke-DrillGit -C $root init -q .
    Invoke-DrillGit -C $root config user.email "drill@local"
    Invoke-DrillGit -C $root config user.name  "drill"
    Set-Content -Path (Join-Path $root "f.txt") -Value "base" -Encoding ASCII
    Invoke-DrillGit -C $root add -A
    Invoke-DrillGit -C $root commit -q -m base
    Invoke-DrillGit -C $root branch base-line
    Invoke-DrillGit -C $root checkout -q -b feature
    Set-Content -Path (Join-Path $root "a.txt") -Value "a" -Encoding ASCII
    Invoke-DrillGit -C $root add -A
    Invoke-DrillGit -C $root commit -q -m a
    Invoke-DrillGit -C $root checkout -q base-line
    Set-Content -Path (Join-Path $root "b.txt") -Value "b" -Encoding ASCII
    Invoke-DrillGit -C $root add -A
    Invoke-DrillGit -C $root commit -q -m b
    Invoke-DrillGit -C $root checkout -q -B work-line base-line
    # --no-ff: the merge commit is the object under test, so it must exist.
    Invoke-DrillGit -C $root merge --no-ff feature --no-edit -q
}
# Attest the NON-merge commits only - the state a repo is in when every ordinary commit ran
# the hooks and the merge did not.
function Write-AttestLedger([string]$root, [string]$ledger, [bool]$includeMergeTree) {
    $trees = @(Get-DrillGit -C $root rev-list --no-merges base-line..work-line |
               ForEach-Object { Get-DrillGit -C $root rev-parse "$_^{tree}" })
    if ($includeMergeTree) { $trees += (Get-DrillGit -C $root rev-parse "work-line^{tree}") }
    Set-Content -Path $ledger -Value $trees -Encoding ASCII
}
# Returns BOTH the exit code and the parsed report. Asserting on the exit code alone is what
# let the broken version through: 0 is equally what "all attested" and "the check switched
# itself off" look like, and only the report tells them apart.
function Invoke-AttestCheck([string]$root, [string]$ledger) {
    $env:AI_STACK_ATTEST_LEDGER = $ledger
    try {
        $out  = (& $attest -Branch "work-line" -Base "base-line" -RepoRoot $root -AllowLedgerOverride -Json)
        $code = $LASTEXITCODE
    } finally { Remove-Item Env:\AI_STACK_ATTEST_LEDGER -ErrorAction SilentlyContinue }
    return [pscustomobject]@{ Code = $code; Report = ($out | ConvertFrom-Json) }
}
$mledger = Join-Path $env:TEMP ("drill-merge-attest-" + $PID + ".log")

# CASE A - no pre-merge-commit at the fork point. The gate must stay off and the unattested
# merge must be SKIPPED, or this guard blocks every branch cut before the hook existed.
Build-AttestRepo $mrepo $false
Write-AttestLedger $mrepo $mledger $false
$rA = Invoke-AttestCheck $mrepo $mledger
# The check must be RUNNING (not inactive) and must have skipped the merge: 1 commit looked
# at, not 2. Without the count this case cannot tell "merges skipped" from "nothing ran".
Check "merge gate INACTIVE without pre-merge-commit at the fork point (exit 0)" `
    ($rA.Code -eq 0 -and -not $rA.Report.inactive -and -not $rA.Report.mergesChecked -and $rA.Report.checked -eq 1) `
    ("checked=" + $rA.Report.checked + " mergesChecked=" + $rA.Report.mergesChecked)

# CASE B - the hook IS at the fork point and the merge tree is NOT attested. This is the
# hole the gate exists to close: a clean merge is new content that no check ever saw.
$mrepo2 = $mrepo + "-b"
Remove-Item -Recurse -Force $mrepo2 -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path (Join-Path $mrepo2 ".githooks") | Out-Null
Build-AttestRepo $mrepo2 $true
Write-AttestLedger $mrepo2 $mledger $false
$rB = Invoke-AttestCheck $mrepo2 $mledger
Check "an UNATTESTED merge commit is CAUGHT once the gate is active (exit 1)" `
    ($rB.Code -eq 1 -and $rB.Report.mergesChecked -and $rB.Report.checked -eq 2) `
    ("checked=" + $rB.Report.checked + " mergesChecked=" + $rB.Report.mergesChecked)

# CASE C - same repository, merge tree attested. Proves case B failed for the merge and not
# for something incidental about the repo; without this, B could be passing by accident.
Write-AttestLedger $mrepo2 $mledger $true
$rC = Invoke-AttestCheck $mrepo2 $mledger
Check "the same branch PASSES once the merge tree is attested (exit 0)" `
    ($rC.Code -eq 0 -and $rC.Report.mergesChecked -and $rC.Report.checked -eq 2) `
    ("checked=" + $rC.Report.checked + " mergesChecked=" + $rC.Report.mergesChecked)

# And the report must NAME it as a merge - the remedy for a merge is not the remedy for an
# ordinary commit, and an operator who is told to rebase across a merge will damage history.
Write-AttestLedger $mrepo2 $mledger $false
$env:AI_STACK_ATTEST_LEDGER = $mledger
$mjson = (& $attest -Branch "work-line" -Base "base-line" -RepoRoot $mrepo2 -AllowLedgerOverride -Json) | ConvertFrom-Json
Remove-Item Env:\AI_STACK_ATTEST_LEDGER -ErrorAction SilentlyContinue
Check "the report marks the offender as a merge (mergesChecked + isMerge)" `
    ($mjson.mergesChecked -and $mjson.unattested.Count -eq 1 -and $mjson.unattested[0].isMerge) `
    ("mergesChecked=" + $mjson.mergesChecked)
Remove-Item -Recurse -Force $mrepo, $mrepo2 -ErrorAction SilentlyContinue
Remove-Item $mledger -ErrorAction SilentlyContinue

# --- a gitlink bump may not name a commit that does not exist -------------------------
#
# NOT DRILLED HERE, deliberately, and this note is the reason rather than an omission.
#
# .githooks/commit-msg is verified - RED on the exact bogus message that prompted it, GREEN
# on the true SHA, no false positive on hex-looking English - but the verification was done
# by running the hook directly against a scratch repository, not from this drill.
#
# Three attempts to drive it from here each passed or failed for a reason that had nothing
# to do with the hook: a relative GIT_DIR that git re-resolved under `-C` so the bug could
# not reproduce; PowerShell's Push-Location not being the working directory sh.exe
# inherits; and a `-replace` regex of a single backslash that threw on every call. Each
# looked like a verdict about the hook and was a verdict about the harness.
#
# Shipping three red checks would teach whoever runs this drill that red is normal, which
# costs more than the missing coverage. The gap is written up in
# documentation/notes/commit-msg-hook-drill-gap.md with the working manual procedure.

# --- retirement must not leave a stale registry row -------------------------------------
#
# THE ACCUMULATION BUG, as an executable case. The operator found "10 worktrees not merged";
# most were already merged, and none had been deregistered. The cause was not forgetfulness:
# on Windows `git worktree remove` drops its administrative record BEFORE deleting the
# directory, so any open handle - a shell whose working directory is inside the worktree -
# fails the delete and git exits non-zero AFTER it has stopped tracking the worktree.
# remove-worktree.ps1 then bailed out, skipping the branch delete, the registry row and the
# prune. git said gone, the registry said live, the branch survived. Four such rows were
# sitting in worktrees.json when this was written.
#
# The probe HOLDS A REAL HANDLE rather than simulating the condition, and captures the
# script's stderr - which is the other half of the bug: the bare `& git` ran under
# $ErrorActionPreference='Stop', so a capturing caller (the bridge `close` path, the test
# reaper) turned git's chatter into a terminating error that killed the script AT the git
# line, before its own error handling could run.
$rmScript = Join-Path $wtScripts "remove-worktree.ps1"
$newScript = Join-Path $wtScripts "new-worktree.ps1"
& $newScript -Id leakprobe | Out-Null
$lpPath = Join-Path $repo ".claude\worktrees\wt-leakprobe"
if (Test-Path $lpPath) {
    $held = [System.IO.File]::Open((Join-Path $lpPath "CLAUDE.md"), 'Open', 'Read', 'None')
    try { & $rmScript -Id leakprobe -Force 2>&1 | Out-Null } finally { $held.Close() }
    $lpRows = (Get-Content -Raw (Join-Path (Get-SharedStateDir) "worktrees.json") | ConvertFrom-Json).worktrees
    $lpRegistered = [bool]($lpRows.PSObject.Properties.Name -contains "leakprobe")
    $lpTracked = [bool](Get-DrillGit -C $repo worktree list --porcelain | Select-String -SimpleMatch "wt-leakprobe")
    $lpBranch = [bool](Get-DrillGit -C $repo branch --list "work/leakprobe")
    Check "a held handle does NOT leave a stale registry row, ref or branch" `
        (-not $lpRegistered -and -not $lpTracked -and -not $lpBranch) `
        ("registry=" + $lpRegistered + " tracked=" + $lpTracked + " branch=" + $lpBranch)
    Remove-Item -Recurse -Force $lpPath -ErrorAction SilentlyContinue
    Invoke-DrillGit -C $repo worktree prune
    if ($lpBranch) { Invoke-DrillGit -C $repo branch -D work/leakprobe }
} else {
    Check "a held handle does NOT leave a stale registry row, ref or branch" $false "probe worktree was not created"
}
& $queue -Reject -Id "drill-bypass" -By "wt-reviewer" -Reason "drill probe, not real work" 2>&1 | Out-Null

foreach ($id in @("a", "b")) {
    & $queue -Submit -Id "drill-$id" -Branch "work/drill$id" -Developer "wt-drill$id" -TestPlan $planFile | Out-Null
}
foreach ($probe in @("drill-badplan", "drill-noplan")) {
    & $queue -Propose -Id $probe -Anchor $anchorFile -Developer "wt-drilla" | Out-Null
    & $queue -ConfirmAnchor -Id $probe -By "operator" | Out-Null
}
Check "a plan that is not a file is refused" $(
    (& $queue -Submit -Id "drill-badplan" -Branch "work/drilla" -Developer "wt-drilla" -TestPlan "I tested it myself" 2>&1 | Out-Null); $LASTEXITCODE -ne 0)
Check "the submitted plan was COPIED beside the item (survives worktree removal)" `
    (Test-Path (Join-Path $QueueDir "drill-a.plan.md"))
Check "both items queued for testing" ((Get-QueueState "drill-a") -eq "ready-to-test" -and (Get-QueueState "drill-b") -eq "ready-to-test")
& $queue -Submit -Id "drill-noplan" -Branch "work/drilla" -Developer "wt-drilla" 2>&1 | Out-Null
Check "a submission with NO test plan is refused" ($LASTEXITCODE -ne 0)

# An anchor is confirmed against what was KNOWN then. When a scope justification turns out
# false, the alternative to amending is carrying a known-wrong anchor to the reviewer.
# Amending costs a cycle on purpose - see the handler.
& $queue -AmendAnchor -Id drill-noplan -By operator -Anchor $anchorFile 2>&1 | Out-Null
Check "-AmendAnchor without a -Reason is refused" ($LASTEXITCODE -ne 0)
& $queue -Claim -Id drill-a -Role tester -By wt-tester | Out-Null
& $queue -AmendAnchor -Id drill-a -By operator -Anchor $anchorFile -Reason "the world turned out different" | Out-Null
Check "amending sends the item BACK to the developer" ((Get-QueueState "drill-a") -eq "anchor-confirmed")
Check "amending DROPS the claim (a verdict describes the old target)" `
    (-not (Test-Path (Join-Path $QueueDir "drill-a.tester.claim")))
Check "the amendment and its reason are in the history" `
    ((Get-Content -Raw (Join-Path $QueueDir "drill-a.json")) -match "anchor AMENDED")
# put drill-a back where the rest of the drill expects it
& $queue -Submit -Id drill-a -Branch "work/drilla" -Developer "wt-drilla" -TestPlan $planFile | Out-Null
Check "the item re-submits normally after an amendment" ((Get-QueueState "drill-a") -eq "ready-to-test")

Step 4 "separation of duties is ENFORCED, not trusted"
& $queue -Claim -Id drill-a -Role tester -By wt-drilla 2>&1 | Out-Null
Check "the developer cannot TEST their own work (exit 4)" ($LASTEXITCODE -eq 4)
& $queue -Claim -Id drill-a -Role reviewer -By wt-drilla 2>&1 | Out-Null
Check "the developer cannot REVIEW their own work (exit 4)" ($LASTEXITCODE -eq 4)
# The guard is a name comparison, so the other form of your own name must not defeat it.
& $queue -Claim -Id drill-a -Role tester -By drilla 2>&1 | Out-Null
Check "the un-prefixed form of the developer's own id is also refused" ($LASTEXITCODE -eq 4)

Step 5 "a case FAILS - the cycle exists because the test found something"
& $queue -Claim -Id drill-a -Role tester -By wt-tester | Out-Null
& $queue -Fail -Id drill-a -By wt-tester -Reason "case 2: the note does not state the unit" 2>&1 | Out-Null
Check "a verdict with no evidence is refused - on the FAIL path too" ($LASTEXITCODE -ne 0)
& $queue -Fail -Id drill-a -By wt-tester -Reason "case 2: no unit" -Evidence "read the file" 2>&1 | Out-Null
Check "a verdict with no plan judgement is refused" ($LASTEXITCODE -ne 0)
& $queue -Fail -Id drill-a -By wt-tester -Reason "case 2: the note does not state the unit" `
    -Evidence "read DRILL-NOTE.md at HEAD; it names a number with no unit" -PlanInadequate | Out-Null
Check "a failing case sends it back to the developer" ((Get-QueueState "drill-a") -eq "test-failed")
Check "the failure records WHERE it was found" `
    ((Get-Content -Raw (Join-Path $QueueDir "drill-a.json") | ConvertFrom-Json).tested_at_sha -ne "")
& $queue -Resubmit -Id drill-a -By wt-tester 2>&1 | Out-Null
Check "only the DEVELOPER may re-submit their item" ($LASTEXITCODE -eq 4)
$revisedPlan = Join-Path $env:TEMP "drill-test-plan-v2.md"
Set-Content -Path $revisedPlan -Encoding ascii -Value @(
    "# Drill test plan, attempt 2",
    "Case 3 (new): the case attempt 1's plan was missing.")
& $queue -Resubmit -Id drill-a -By wt-drilla -TestPlan $revisedPlan | Out-Null
Check "-Resubmit REPLACES the queued plan when one is offered" `
    ((Get-Content -Raw -Path (Join-Path $QueueDir "drill-a.plan.md")) -match "attempt 2")
Check "the developer re-submits on the SAME item (attempt 2)" ((Get-QueueState "drill-a") -eq "ready-to-test")

Step 6 "the tester passes both - and they STOP at the human gate"
foreach ($id in @("a", "b")) {
    & $queue -Claim -Id "drill-$id" -Role tester -By wt-tester | Out-Null
    & $queue -Pass -Id "drill-$id" -By wt-tester -Evidence "every case green" -PlanAdequate | Out-Null
}
Check "a pass does NOT queue for review by itself" ((Get-QueueState "drill-a") -eq "test-passed" -and (Get-QueueState "drill-b") -eq "test-passed")
& $queue -Claim -Id drill-a -Role reviewer -By wt-reviewer 2>&1 | Out-Null
Check "a reviewer cannot claim before the operator releases it" ($LASTEXITCODE -ne 0)
& $queue -Approve -Id drill-a -By wt-drilla 2>&1 | Out-Null
Check "the developer cannot release their OWN work (exit 4)" ($LASTEXITCODE -eq 4)
foreach ($id in @("a", "b")) { & $queue -Approve -Id "drill-$id" -By profnovice | Out-Null }
Check "the operator released both for review" ((Get-QueueState "drill-a") -eq "ready-review" -and (Get-QueueState "drill-b") -eq "ready-review")

Step 7 "the reviewer - neither developer - lands the first item"
& $queue -Claim -Id drill-a -Role reviewer -By wt-reviewer | Out-Null
Check "reviewer claimed drill-a" ((Get-QueueState "drill-a") -eq "reviewing")
Invoke-DrillGit -C $wtA rebase drill/verify-d
$wtMerge = Join-Path $repo ".claude\worktrees\merge-line"
Invoke-DrillGit worktree add $wtMerge drill/verify-d
Check "merge worktree created (never the operator's checkout)" (Test-Path $wtMerge)
Invoke-DrillGit -C $wtMerge merge --no-ff work/drilla -m "merge drill A: raise timeout to 60s (evidence: drill)"
$mergeSha = (Get-DrillGit -C $wtMerge rev-parse HEAD).Trim()
& $queue -Merged -Id drill-a -By wt-reviewer -Sha $mergeSha 2>&1 | Out-Null
Check "a merge with NO fitness verdict is refused" ((Get-QueueState "drill-a") -ne "merged")
& $queue -Merged -Id drill-a -By wt-reviewer -Sha $mergeSha -Misfits 2>&1 | Out-Null
Check "-Misfits cannot be merged (exit 4) - it is a rejection" ($LASTEXITCODE -eq 4)
& $queue -Merged -Id drill-a -By wt-reviewer -Sha $mergeSha -FitsAnchor 2>&1 | Out-Null
Check "the RETIRED -FitsAnchor spelling is not accepted" ((Get-QueueState "drill-a") -ne "merged")

# THE SHA-CONTAINMENT GUARD. Added 2026-08-29: this guard was written after a merge that
# silently failed was recorded as merged, and it had ZERO drill coverage - an edit that
# inverted it would have left this drill 51/51 green. It is the one check standing between
# "the queue says merged" and "nothing merged", so it gets exercised, not trusted.
$notAMerge = (Get-DrillGit -C $wtMerge rev-parse HEAD~1).Trim()
& $queue -Merged -Id drill-a -By wt-reviewer -Sha $notAMerge -FitsCodebase 2>&1 | Out-Null
Check "a sha that does NOT contain the branch is refused" ((Get-QueueState "drill-a") -ne "merged")
$bogus = "0000000000000000000000000000000000000000"
& $queue -Merged -Id drill-a -By wt-reviewer -Sha $bogus -FitsCodebase 2>&1 | Out-Null
Check "a nonexistent sha is refused, not recorded" ((Get-QueueState "drill-a") -ne "merged")

& $queue -Merged -Id drill-a -By wt-reviewer -Sha $mergeSha -FitsCodebase | Out-Null
Check "drill-a merged by the reviewer" ((Get-QueueState "drill-a") -eq "merged")
Check "the verdict recorded is fits_codebase, not the retired fits_anchor" (
    (Get-Content -Raw -Path (Join-Path $QueueDir "drill-a.json") | ConvertFrom-Json).fits_codebase -eq $true)

Step 8 "the second item's rebase CONFLICTS - the later merger adapts"
& $queue -Claim -Id drill-b -Role reviewer -By wt-reviewer | Out-Null
$testedAt = (Get-Content -Raw -Path (Join-Path $QueueDir "drill-b.json") | ConvertFrom-Json).tested_at_sha
Invoke-DrillGit -C $wtB rebase drill/verify-d   # expected to conflict
$conflicted = @(Get-DrillGit -C $wtB diff --name-only --diff-filter=U)
Check "rebase produced a real conflict" ($conflicted -contains "DRILL-NOTE.md") ("files: " + ($conflicted -join ","))
Set-Content -Path (Join-Path $wtB "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "",
    "Agent A owns this line: A needs the timeout raised to 60s.",
    "Agent B (later merger, adapted): B's 5s need is served by a per-caller override,",
    "so A's landed 60s default is untouched.")
Invoke-DrillGit -C $wtB add DRILL-NOTE.md
$env:GIT_EDITOR = "true"
Invoke-DrillGit -C $wtB rebase --continue
Check "B's rebase completed after adapting" (-not (Test-Path (Join-Path $wtB ".git\rebase-merge")))

Step 9 "STALE PASS: the rebase changed the tested content, so it goes BACK to test"
$afterRebase = (Get-DrillGit -C $wtB rev-parse HEAD).Trim()
Check "the tested sha is no longer what would land" ($afterRebase -ne $testedAt)
& $queue -Requeue -Id drill-b -By wt-reviewer -Reason "rebase onto A's merge changed the file; the pass no longer describes it" | Out-Null
Check "reviewer returned it to testing rather than merging" ((Get-QueueState "drill-b") -eq "ready-to-test")
& $queue -Claim -Id drill-b -Role tester -By wt-tester | Out-Null
& $queue -Pass -Id drill-b -By wt-tester -Evidence "re-read the adapted file; both intents present" -PlanAdequate | Out-Null
& $queue -Approve -Id drill-b -By profnovice | Out-Null
Check "re-tested and re-released at the new content" ((Get-QueueState "drill-b") -eq "ready-review")

Step 10 "the reviewer lands the adapted work"
& $queue -Claim -Id drill-b -Role reviewer -By wt-reviewer | Out-Null
Invoke-DrillGit -C $wtMerge merge --no-ff work/drillb -m "merge drill B: per-caller override, A's default kept (evidence: drill)"
& $queue -Merged -Id drill-b -By wt-reviewer -Sha ((Get-DrillGit -C $wtMerge rev-parse HEAD).Trim()) -FitsCodebase | Out-Null
Check "drill-b merged after re-test" ((Get-QueueState "drill-b") -eq "merged")

Step 11 "outcome: both intents survive, history readable, development untouched"
$final = Get-Content (Join-Path $wtMerge "DRILL-NOTE.md") -Raw
Check "A's intent survived the later merge" ($final -match "raised to 60s")
Check "B's adapted intent is present" ($final -match "per-caller override")
$merges = @(Get-DrillGit -C $wtMerge log --oneline --merges "$devBefore..drill/verify-d")
Check "two --no-ff merge commits on the line" ($merges.Count -eq 2) ("count=" + $merges.Count)
Check "development NEVER moved" ((Get-DrillGit rev-parse development).Trim() -eq $devBefore)
Check "operator checkout still on its own branch" ((Get-DrillGit rev-parse --abbrev-ref HEAD).Trim() -eq $mainBranch)

Step 12 "cleanup - leave nothing behind"
Invoke-DrillGit worktree remove --force $wtMerge
foreach ($id in @("drilla", "drillb")) {
    & (Join-Path $wtScripts "remove-worktree.ps1") -Id $id -MergedInto "drill/verify-d" -Force | Out-Null
}
Invoke-DrillGit branch -D drill/verify-d
Invoke-DrillGit worktree prune
Clear-DrillQueue
# Scoped to the DRILL's own artifacts. These asserted the whole worktree directory was
# empty, which failed the moment real agents had work in flight - the drill must not
# require an idle repo to pass, and must never look like it cleaned up someone else's work.
$leftWorktrees = @("wt-drilla", "wt-drillb", "merge-line") |
    Where-Object { Test-Path (Join-Path $repo ".claude\worktrees\$_") }
Check "all drill worktrees gone" ($leftWorktrees.Count -eq 0) ("left: " + ($leftWorktrees -join ","))
$leftBranches = @(Get-DrillGit branch --list "work/drilla" "work/drillb" "drill/verify-d")
Check "no drill branches left" ($leftBranches.Count -eq 0) ("left: " + ($leftBranches -join ","))
Check "queue emptied of drill items" (@(Get-ChildItem -Path $QueueDir -Filter "drill-*" -ErrorAction SilentlyContinue).Count -eq 0)

Write-Host "`n================ DRILL SUMMARY ================" -ForegroundColor Cyan
$fail = @($results | Where-Object { -not $_.pass })
$results | ForEach-Object { Write-Host ("  {0,-6} {1}" -f $(if ($_.pass) { "PASS" } else { "FAIL" }), $_.check) }
Write-Host ("`n{0}/{1} checks passed" -f ($results.Count - $fail.Count), $results.Count) `
    -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
Release-DrillLease
if ($fail.Count) { exit 1 }
