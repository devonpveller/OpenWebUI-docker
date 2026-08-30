# verify-oracle-on-stall.ps1 - executable proof that a stall reaches the frontier oracle.
#
#   .\scripts\agent-harness\verify-oracle-on-stall.ps1     # a few seconds, cleans up after itself
#
# dark-factory-unification U4; ORCHESTRATION-DESIGN sec 7 ("the frontier is an oracle
# invoked on a stall signal - not a better worker"). U4's Validated-by column reads
# "stall -> oracle observed firing at least once", so this drill exists to BE that
# observation: it drives real queue.ps1 rounds until the detector fires, and points at the
# ledger row afterwards.
#
# The unit tests (test_oracle_on_stall.py) cover the DEFINITION. This covers the
# CHOREOGRAPHY - a tester recording a verdict through the real tool, on a real branch whose
# head really moves, with the detector wired into the real -Fail path. That is where the
# defects have historically been: a check that is correct in isolation and never runs.
#
# BOTH DIRECTIONS, in one run. A detector that always fires is as useless as one that never
# does, so the drill runs a STALLED item (same failure, three rounds) and a MOVING item
# (a different failure each round, same number of rounds) and asserts the ledger has a row
# for the first and none for the second.
#
# ISOLATION: the drill points AI_STACK_WORKTREE_STATE at a scratch namespace. The escalation
# ledger is audit evidence - PLAN sec C.7 calls the audit trail "the deliverable's twin" -
# and a drill that writes invented firings into it would corrupt the exact record the phase
# is validated against. Nothing here touches the live queue, the live ledger, or any branch
# but its own two scratch refs.
#
# NOTE: no `2>&1` on any git call, and the helpers flip $ErrorActionPreference themselves -
# in PS5.1 capturing a native command's stderr under 'Stop' turns git's ordinary chatter
# into a terminating error.

$ErrorActionPreference = "Continue"
$wtScripts = $PSScriptRoot
$queue = Join-Path $wtScripts "queue.ps1"
. (Join-Path $wtScripts "common.ps1")

# THE CHECKOUT THIS SCRIPT LIVES IN - not the main one. Run from a worktree, the drill must
# exercise the code under test and move ITS refs, never the operator's checkout: a drill that
# reaches into the main checkout to make branches is a drill that can disturb whoever is
# working there.
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not (Test-Path (Join-Path $repo ".git"))) {
    throw "cannot locate the checkout containing this script - run it from inside the repository"
}
$results = @()
$DEV = "wt-oracledev"
$TESTER = "wt-oracletest"
$STALL_ID = "oracle-drill-stall"
$MOVE_ID = "oracle-drill-move"
$STALL_BRANCH = "drill/oracle-stall"
$MOVE_BRANCH = "drill/oracle-move"

function Step($n, $text) { Write-Host "`n=== $n. $text ===" -ForegroundColor Cyan }
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function Invoke-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & git.exe @args | Out-Null } finally { $ErrorActionPreference = $prev }
}
function Get-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return (& git.exe @args) } finally { $ErrorActionPreference = $prev }
}

Set-Location $repo

# --- scratch namespace --------------------------------------------------------------
$prevState = $env:AI_STACK_WORKTREE_STATE
$scratch = Join-Path $env:TEMP ("oracle-drill-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$env:AI_STACK_WORKTREE_STATE = $scratch
$QueueDir = Join-Path $scratch "queue"

function Get-Ledger {
    # Read the ledger the way an operator would - through the module, not by parsing the
    # file here. A drill that reimplements the reader can pass while the reader is broken.
    #
    # `ConvertFrom-Json ($json)`, NOT `$json | ConvertFrom-Json`. In PS5.1 the PIPELINE form
    # emits a JSON array as ONE object, so `@(... | ConvertFrom-Json)` yields a one-element
    # array whose single element is the whole array. Every row lookup then ran against the
    # array itself - and `.item` on a .NET collection resolves to the IList indexer, so the
    # comparison was PSMethod -eq String: false, always, silently. The drill's control
    # checks ("no escalation") passed for that reason and not because they were true.
    $mod = Join-Path $wtScripts "oracle_on_stall.py"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $raw = & python $mod report --repo $repo --json } finally { $ErrorActionPreference = $prev }
    if (-not $raw) { return @() }
    $rows = @()
    try { foreach ($r in (ConvertFrom-Json ($raw -join "`n"))) { $rows += $r } } catch { return @() }
    return $rows
}
function Get-LedgerRow([string]$wanted) {
    # `item_id`, not `item` - see above. The field was renamed in the ledger for exactly
    # this reason, so no future PowerShell reader can trip the same silent false.
    foreach ($r in (Get-Ledger)) { if ($r.item_id -eq $wanted) { return $r } }
    return $null
}

try {
    # --- idempotent preamble ---------------------------------------------------------
    foreach ($b in @($STALL_BRANCH, $MOVE_BRANCH)) {
        Get-DrillGit rev-parse --verify --quiet "refs/heads/$b" | Out-Null
        if ($LASTEXITCODE -eq 0) { Invoke-DrillGit branch -D $b }
    }

    Step 1 "preconditions - python, the module, and three commits to move a branch across"
    Check "python is on PATH" ([bool](Get-Command python -ErrorAction SilentlyContinue))
    Check "oracle_on_stall.py sits beside queue.ps1" (Test-Path (Join-Path $wtScripts "oracle_on_stall.py"))
    $line = Resolve-WorkLine
    $c = @((Get-DrillGit rev-parse "HEAD~2").Trim(), (Get-DrillGit rev-parse "HEAD~1").Trim(),
           (Get-DrillGit rev-parse "HEAD").Trim())
    Check "three distinct commits available on '$line'" (($c | Select-Object -Unique).Count -eq 3)
    Check "the scratch ledger starts EMPTY" (@(Get-Ledger).Count -eq 0) $scratch

    Step 2 "the anchor gate, then submission - the real tool, on real branches"
    $planFile = Join-Path $env:TEMP "oracle-drill-plan.md"
    Set-Content -Path $planFile -Encoding ascii -Value @(
        "# Drill test plan",
        "Case 1: the stall detector fires on a converging-nowhere item. Pass: it fires. Fail: silence.",
        "Case 2: it stays silent on a progressing item. Pass: silence. Fail: it fires.")
    $anchorFile = Join-Path $env:TEMP "oracle-drill-anchor.json"
    Set-Content -Path $anchorFile -Encoding ascii -Value @(
        "{",
        "  ""goal"": ""Drive the stall detector through the real queue pipeline."",",
        "  ""artifact"": ""A ledger row, or the absence of one."",",
        "  ""audience"": ""The reviewer checking U4's Validated-by column."",",
        "  ""acceptance"": [",
        "    ""A stalled item produces exactly one escalation row."",",
        "    ""A progressing item produces none.""",
        "  ],",
        "  ""out_of_scope"": [ ""Anything outside this drill's scratch namespace."" ],",
        "  ""findings_sink"": ""documentation/notes/u4oracle-findings.md""",
        "}")
    foreach ($pair in @(@($STALL_ID, $STALL_BRANCH), @($MOVE_ID, $MOVE_BRANCH))) {
        Invoke-DrillGit branch -f $pair[1] $c[0]
        & $queue -Propose -Id $pair[0] -Anchor $anchorFile -Developer $DEV | Out-Null
        & $queue -ConfirmAnchor -Id $pair[0] -By "operator" | Out-Null
        # THE PROFILE IS THE POINT. sec 7's split is 95% small model / 5% frontier unstick,
        # so the worker must be the LOCAL runner for there to be an oracle above it. Under
        # the shipped all-cloud default the honest answer is 'no-oracle-above', which is a
        # different assertion - covered by the unit tests, not by this drill.
        & $queue -Submit -Id $pair[0] -Branch $pair[1] -Developer $DEV -TestPlan $planFile `
                 -RunnerProfile "local-work-cloud-review" | Out-Null
    }
    $submitted = (Get-Content -Raw -Path (Join-Path $QueueDir "$STALL_ID.json") | ConvertFrom-Json)
    Check "both items are queued for testing" ($submitted.state -eq "ready-to-test")
    Check "the item records the runner profile it is worked under" `
        ($submitted.profile -eq "local-work-cloud-review")

    # --- the rounds ------------------------------------------------------------------
    # One round = a tester's failing verdict. The developer then moves the branch (a real
    # commit is not needed for the SIGNAL - what the detector reads is that the head moved)
    # and re-submits.
    function Invoke-Round([string]$id, [string]$branch, [string]$sha, [string]$reason, [bool]$first) {
        if (-not $first) {
            Invoke-DrillGit branch -f $branch $sha
            & $queue -Resubmit -Id $id -By $DEV | Out-Null
        }
        & $queue -Claim -Id $id -Role tester -By $TESTER | Out-Null
        # `6>&1` because queue.ps1 reports through Write-Host, which in PS5.1 goes to the
        # INFORMATION stream and is invisible to a plain assignment. The detector's own
        # lines come from python's stdout and would be captured either way - but a drill
        # that can only see half the output would silently stop checking the other half.
        $out = & $queue -Fail -Id $id -By $TESTER -Reason $reason `
                        -Evidence "ran the plan, case 2 failed" -PlanAdequate 6>&1
        return (($out | ForEach-Object { $_.ToString() }) -join "`n")
    }

    Step 3 "STALLED item - the same failure, three rounds, the branch moving each time"
    $sameReason = "case 2 - the guard never fires"
    $r1 = Invoke-Round $STALL_ID $STALL_BRANCH $c[0] $sameReason $true
    Check "round 1 records no escalation" ($null -eq (Get-LedgerRow $STALL_ID))
    $r2 = Invoke-Round $STALL_ID $STALL_BRANCH $c[1] $sameReason $false
    Check "round 2 is a stall of 1 - still no escalation (the threshold is 2, not 1)" `
        ($null -eq (Get-LedgerRow $STALL_ID))
    Check "round 2 says so out loud" ([bool]($r2 -match "stall 1/2"))
    $r3 = Invoke-Round $STALL_ID $STALL_BRANCH $c[2] $sameReason $false

    Step 4 "THE FIRING - observed in the -Fail output and on the ledger"
    Check "the -Fail path itself announced the escalation" ([bool]($r3 -match "ORACLE-ON-STALL"))
    Check "it names the hand-back, not a takeover" ([bool]($r3 -match "hand back to little-coder"))
    $row = Get-LedgerRow $STALL_ID
    Check "a ledger row exists for the stalled item" ($null -ne $row)
    if ($row) {
        Check "outcome is 'escalate'" ($row.outcome -eq "escalate") $row.outcome
        Check "the runner that stalled is the LOCAL one" ($row.stalled_runner -eq "little-coder") $row.stalled_runner
        Check "the oracle is the frontier runner" ($row.oracle_runner -eq "claude-code") $row.oracle_runner
        Check "it hands back to the worker (sec 7: an oracle, not a better worker)" `
            ($row.hand_back_to -eq "little-coder") $row.hand_back_to
        Check "it fired at the threshold, not later" ($row.stall -eq 2) ("stall=" + $row.stall)
        Check "the record carries what the detector SAW (a round-by-round trail)" `
            (@($row.trail).Count -eq 3) ("rounds=" + @($row.trail).Count)
        Check "the trail's first round was progress and the rest were not" `
            ((@($row.trail)[0].progress -eq $true) -and (@($row.trail)[1].progress -eq $false) `
             -and (@($row.trail)[2].progress -eq $false))
    }

    Step 5 "an unconsumed escalation is PENDING - the handle a dispatcher reads"
    # Through the module's own CLI, not a `python -c` snippet: the repo path contains a
    # space, and snippet quoting is exactly what broke -ScopeNodes twice.
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    $pend = & python (Join-Path $wtScripts "oracle_on_stall.py") pending $STALL_ID --repo $repo
    $none = & python (Join-Path $wtScripts "oracle_on_stall.py") pending $MOVE_ID --repo $repo
    $ErrorActionPreference = $prev
    Check "pending() returns the oracle target for the stalled item" `
        ((($pend -join "").Trim()) -eq "claude-code") ($pend -join "")
    Check "pending() returns NONE for an item that never stalled" `
        ((($none -join "").Trim()) -eq "NONE") ($none -join "")

    Step 6 "CONTROL - a progressing item, same number of rounds, must NOT fire"
    $m1 = Invoke-Round $MOVE_ID $MOVE_BRANCH $c[0] "case 1 - the guard is missing entirely" $true
    $m2 = Invoke-Round $MOVE_ID $MOVE_BRANCH $c[1] "case 2 - the guard fires on the wrong branch" $false
    $m3 = Invoke-Round $MOVE_ID $MOVE_BRANCH $c[2] "case 3 - the exit code is 0 when it should be 4" $false
    Check "three failing rounds and NO escalation" ($null -eq (Get-LedgerRow $MOVE_ID))
    Check "the -Fail path said 'no stall' rather than staying silent" ([bool]($m3 -match "no stall"))
    Check "the ledger holds exactly ONE row across both items" (@(Get-Ledger).Count -eq 1)

    Step 7 "re-running the detector on unchanged rounds does not grow the ledger"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & python (Join-Path $wtScripts "oracle_on_stall.py") check $QueueDir $STALL_ID --repo $repo | Out-Null
    & python (Join-Path $wtScripts "oracle_on_stall.py") check $QueueDir $STALL_ID --repo $repo | Out-Null
    $ErrorActionPreference = $prev
    Check "still exactly one row (a ledger that grows when read is not evidence)" (@(Get-Ledger).Count -eq 1)
}
finally {
    # --- cleanup ---------------------------------------------------------------------
    Set-Location $repo
    foreach ($b in @($STALL_BRANCH, $MOVE_BRANCH)) {
        Get-DrillGit rev-parse --verify --quiet "refs/heads/$b" | Out-Null
        if ($LASTEXITCODE -eq 0) { Invoke-DrillGit branch -D $b }
    }
    if ($prevState) { $env:AI_STACK_WORKTREE_STATE = $prevState }
    else { Remove-Item Env:\AI_STACK_WORKTREE_STATE -ErrorAction SilentlyContinue }
    if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue }
}

Write-Host ""
$failed = @($results | Where-Object { -not $_.pass })
Write-Host ("{0}/{1} checks passed." -f ($results.Count - $failed.Count), $results.Count) `
    -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
if ($failed.Count) {
    foreach ($f in $failed) { Write-Host ("  FAILED: " + $f.check + " " + $f.detail) -ForegroundColor Red }
    exit 1
}
Write-Host "frontier-oracle-on-stall: stall detected, oracle escalation observed, control silent." -ForegroundColor Green
exit 0
