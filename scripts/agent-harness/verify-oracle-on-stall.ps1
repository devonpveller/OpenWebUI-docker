# verify-oracle-on-stall.ps1 - executable proof that a stall reaches the frontier oracle.
#
#   .\scripts\agent-harness\verify-oracle-on-stall.ps1     # ~13s measured here 2026-08-30
#
# It removes its scratch namespace and its three per-run refs ON SUCCESS ONLY. A FAILING
# run deliberately KEEPS the scratch dir - see "ON FAILURE" further down in this header.
#
# dark-factory-unification U4; ORCHESTRATION-DESIGN sec 7 ("the frontier is an oracle
# invoked on a stall signal - not a better worker").
#
# WHAT THIS DRILL DOES AND DOES NOT PROVE. It CONSTRUCTS a stall - a scripted tester fails
# the same case three times against a branch head the script moves itself - and proves the
# detector fires on it, records it, and stays silent on the control. That is a mechanism
# test, and it is how the detector is proven RED->GREEN. It is NOT an observation of the
# oracle firing on a REAL stalled item: no real item has stalled here, and nothing yet runs
# the oracle's round. U4's "stall -> oracle observed firing at least once" is only half met
# by this file, and documentation/notes/u4oracle-findings.md F4 says which half.
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
# ISOLATION, and why it is stricter than it looks. Three separate shared things can reach in
# and change what this drill measures, and all three are pinned here:
#
#   1. THE STATE DIR. AI_STACK_WORKTREE_STATE points at a scratch namespace. The escalation
#      ledger is audit evidence - PLAN sec C.7 calls the audit trail "the deliverable's
#      twin" - and a drill writing invented firings into it would corrupt the record the
#      phase is validated against.
#   2. THE GIT REFS, which the state dir does NOT cover. Branches live in the repository's
#      SHARED ref store, visible to every worktree and every other session on this machine.
#      This drill used to use fixed names (drill/oracle-stall) and force-DELETE them in its
#      preamble - so a second run, anywhere, tore the branch out from under a running one.
#      Every ref and every queue id is now suffixed with a per-run token.
#   3. THE WORK LINE. Resolve-WorkLine falls back to THE OPERATOR'S MAIN CHECKOUT's current
#      branch - out-of-process global state that other sessions change while this runs
#      (observed 2026-08-30: the main checkout went from a branch, to detached, to a
#      different commit, inside ten minutes, because a sibling drill was rebasing in it).
#      When it resolved to a line that does not contain these branches, -Submit was refused
#      by the hook-attestation guard and the run failed in a way that looked like a detector
#      bug. AI_STACK_WORK_LINE is pinned to this checkout's own HEAD for the run.
#
# ON FAILURE THE SCRATCH NAMESPACE IS KEPT, and its ledger and queue items are printed.
# "Could not reproduce it in fifteen attempts" is what happens when a drill deletes its own
# evidence on the way out; that cost a full verification cycle on 2026-08-30.
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

# PER-RUN NAMES. See isolation note 2: refs are repository-global, so fixed names made two
# runs - concurrent, or one crashed and one fresh - silently share and delete each other's
# branches. The stall signal is computed from those branch heads, so that was not a tidiness
# problem, it was a correctness one.
$RUN = [guid]::NewGuid().ToString("N").Substring(0, 8)
$DEV = "wt-oracledev"
$TESTER = "wt-oracletest"
$STALL_ID = "oracle-drill-stall-$RUN"
$MOVE_ID = "oracle-drill-move-$RUN"
$GONE_ID = "oracle-drill-gone-$RUN"
$STALL_BRANCH = "drill/oracle-stall-$RUN"
$MOVE_BRANCH = "drill/oracle-move-$RUN"
$GONE_BRANCH = "drill/oracle-gone-$RUN"

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

# --- scratch namespace, and a pinned work line -----------------------------------------
$prevState = $env:AI_STACK_WORKTREE_STATE
$prevLine = $env:AI_STACK_WORK_LINE
$scratch = Join-Path $env:TEMP ("oracle-drill-" + $RUN)
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
$env:AI_STACK_WORKTREE_STATE = $scratch
# See isolation note 3. This checkout's own HEAD contains every commit the drill branches
# point at, so the hook-attestation guard has nothing to attest and -Submit is decided by
# the queue's own rules rather than by whatever the operator happens to have loaded.
$headLine = (Get-DrillGit rev-parse --abbrev-ref HEAD | Select-Object -First 1)
if (-not $headLine -or $headLine.Trim() -eq "HEAD") {
    $headLine = (Get-DrillGit rev-parse HEAD | Select-Object -First 1)
}
$env:AI_STACK_WORK_LINE = $headLine.Trim()
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
function Get-LedgerRows([string]$wanted) {
    # ALL rows for an item, not the first. `Get-LedgerRow` used to return the first match,
    # so if a bug ever produced two firings for one item the drill silently inspected one of
    # them and reported its contents as "the" row - which is how a trail length got read as
    # a round count. Count first, then look.
    # `item_id`, not `item` - see above. The field was renamed in the ledger for exactly
    # this reason, so no future PowerShell reader can trip the same silent false.
    $out = @()
    foreach ($r in (Get-Ledger)) { if ($r.item_id -eq $wanted) { $out += $r } }
    return $out
}
function Get-QueueItem([string]$id) {
    $p = Join-Path $QueueDir "$id.json"
    if (-not (Test-Path $p)) { return $null }
    return (ConvertFrom-Json ((Get-Content -Raw -Path $p)))
}

try {
    Step 1 "preconditions - python, the module, and three commits to move a branch across"
    Check "python is on PATH" ([bool](Get-Command python -ErrorAction SilentlyContinue))
    Check "oracle_on_stall.py sits beside queue.ps1" (Test-Path (Join-Path $wtScripts "oracle_on_stall.py"))
    $c = @((Get-DrillGit rev-parse "HEAD~2").Trim(), (Get-DrillGit rev-parse "HEAD~1").Trim(),
           (Get-DrillGit rev-parse "HEAD").Trim())
    Check "three distinct commits available on '$($env:AI_STACK_WORK_LINE)'" `
        (($c | Select-Object -Unique).Count -eq 3)
    Check "the scratch ledger starts EMPTY" (@(Get-Ledger).Count -eq 0) $scratch
    # The refs are per-run, so nothing of this drill's can already exist. If one does, the
    # token collided or a previous run of THIS token is live - either way, stop guessing.
    foreach ($b in @($STALL_BRANCH, $MOVE_BRANCH, $GONE_BRANCH)) {
        Get-DrillGit rev-parse --verify --quiet "refs/heads/$b" | Out-Null
        Check "ref '$b' does not already exist (per-run names are the isolation)" ($LASTEXITCODE -ne 0)
    }

    Step 2 "the anchor gate, then submission - the real tool, on real branches"
    $planFile = Join-Path $scratch "oracle-drill-plan.md"
    Set-Content -Path $planFile -Encoding ascii -Value @(
        "# Drill test plan",
        "Case 1: the stall detector fires on a converging-nowhere item. Pass: it fires. Fail: silence.",
        "Case 2: it stays silent on a progressing item. Pass: silence. Fail: it fires.",
        "Case 3: a verdict against a vanished branch is refused. Pass: refused. Fail: recorded.")
    $anchorFile = Join-Path $scratch "oracle-drill-anchor.json"
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
    foreach ($pair in @(@($STALL_ID, $STALL_BRANCH), @($MOVE_ID, $MOVE_BRANCH), @($GONE_ID, $GONE_BRANCH))) {
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
    $submitted = Get-QueueItem $STALL_ID
    Check "all three items are queued for testing" ($submitted -and $submitted.state -eq "ready-to-test") `
        $(if ($submitted) { $submitted.state } else { "no item written" })
    Check "the item records the runner profile it is worked under" `
        ($submitted -and $submitted.profile -eq "local-work-cloud-review")

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
    Invoke-Round $STALL_ID $STALL_BRANCH $c[0] $sameReason $true | Out-Null
    Check "round 1 records no escalation" (@(Get-LedgerRows $STALL_ID).Count -eq 0)
    $r2 = Invoke-Round $STALL_ID $STALL_BRANCH $c[1] $sameReason $false
    Check "round 2 is a stall of 1 - still no escalation (the threshold is 2, not 1)" `
        (@(Get-LedgerRows $STALL_ID).Count -eq 0)
    Check "round 2 says so out loud" ([bool]($r2 -match "stall 1/2"))
    $r3 = Invoke-Round $STALL_ID $STALL_BRANCH $c[2] $sameReason $false

    Step 4 "THE FIRING - observed in the -Fail output and on the ledger"
    Check "the -Fail path itself announced the escalation" ([bool]($r3 -match "ORACLE-ON-STALL"))
    Check "it names the hand-back, not a takeover" ([bool]($r3 -match "hand back to little-coder"))
    $rows = @(Get-LedgerRows $STALL_ID)
    Check "EXACTLY ONE ledger row for the stalled item" ($rows.Count -eq 1) ("rows=" + $rows.Count)
    if ($rows.Count -ge 1) {
        $row = $rows[0]
        Check "outcome is 'escalate'" ($row.outcome -eq "escalate") $row.outcome
        Check "the runner that stalled is the LOCAL one" ($row.stalled_runner -eq "little-coder") $row.stalled_runner
        Check "the oracle is the frontier runner" ($row.oracle_runner -eq "claude-code") $row.oracle_runner
        Check "it hands back to the worker (sec 7: an oracle, not a better worker)" `
            ($row.hand_back_to -eq "little-coder") $row.hand_back_to
        Check "it fired at the threshold, not later" ($row.stall -eq 2) ("stall=" + $row.stall)
        Check "the firing is over THREE rounds - two is structurally impossible" `
            ($row.rounds -eq 3) ("rounds=" + $row.rounds)
        Check "the record carries what the detector SAW (a round-by-round trail)" `
            (@($row.trail).Count -eq 3) ("trail_entries=" + @($row.trail).Count)
        Check "the trail's first round was progress and the rest were not" `
            ((@($row.trail)[0].progress -eq $true) -and (@($row.trail)[1].progress -eq $false) `
             -and (@($row.trail)[2].progress -eq $false))
        Check "every round the detector scored carries a real git object name" `
            (@(@($row.trail) | Where-Object { $_.sha -notmatch '^[0-9a-f]{7,40}$' }).Count -eq 0) `
            (("shas=" + ((@($row.trail) | ForEach-Object { $_.sha }) -join ",")))
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
    Invoke-Round $MOVE_ID $MOVE_BRANCH $c[0] "case 1 - the guard is missing entirely" $true | Out-Null
    Invoke-Round $MOVE_ID $MOVE_BRANCH $c[1] "case 2 - the guard fires on the wrong branch" $false | Out-Null
    $m3 = Invoke-Round $MOVE_ID $MOVE_BRANCH $c[2] "case 3 - the exit code is 0 when it should be 4" $false
    Check "three failing rounds and NO escalation" (@(Get-LedgerRows $MOVE_ID).Count -eq 0)
    Check "the -Fail path said 'no stall' rather than staying silent" ([bool]($m3 -match "no stall"))
    Check "the ledger holds exactly ONE row across both items" (@(Get-Ledger).Count -eq 1)

    Step 7 "re-running the detector on unchanged rounds does not grow the ledger"
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & python (Join-Path $wtScripts "oracle_on_stall.py") check $QueueDir $STALL_ID --repo $repo | Out-Null
    & python (Join-Path $wtScripts "oracle_on_stall.py") check $QueueDir $STALL_ID --repo $repo | Out-Null
    $ErrorActionPreference = $prev
    Check "still exactly one row (a ledger that grows when read is not evidence)" (@(Get-Ledger).Count -eq 1)

    Step 8 "A VANISHED BRANCH IS NOT A STALL - the round that manufactured escalations"
    # THE DEFECT THIS CLOSES. `git rev-parse <missing-ref>` prints the REF NAME on stdout and
    # exits 128. -Fail did not check the exit code, so a deleted branch was recorded as
    # `sha: "drill/oracle-gone-..."` - identical on every subsequent round, which the
    # detector reads as "the code did not move" and escalates on. A tooling failure
    # manufacturing a frontier escalation, in the false-positive direction, silently.
    & $queue -Claim -Id $GONE_ID -Role tester -By $TESTER | Out-Null
    Invoke-DrillGit branch -D $GONE_BRANCH
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    & $queue -Fail -Id $GONE_ID -By $TESTER -Reason "case 3 - the branch is gone" `
             -Evidence "ran the plan, case 3" -PlanAdequate 2>&1 | Out-Null
    $goneExit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    Check "-Fail REFUSES a verdict against a branch that does not resolve" ($goneExit -ne 0) `
        ("exit=" + $goneExit)
    $goneItem = Get-QueueItem $GONE_ID
    Check "nothing was appended to that item's results" (@($goneItem.results).Count -eq 0) `
        ("results=" + @($goneItem.results).Count)
    Check "the branch NAME was not stored anywhere as a sha" `
        (($goneItem.tested_at_sha -ne $GONE_BRANCH) -and
         (@(@($goneItem.results) | Where-Object { $_.sha -eq $GONE_BRANCH }).Count -eq 0))
    Check "and no escalation was invented for it" (@(Get-LedgerRows $GONE_ID).Count -eq 0)
}
finally {
    # --- cleanup ---------------------------------------------------------------------
    Set-Location $repo
    foreach ($b in @($STALL_BRANCH, $MOVE_BRANCH, $GONE_BRANCH)) {
        Get-DrillGit rev-parse --verify --quiet "refs/heads/$b" | Out-Null
        if ($LASTEXITCODE -eq 0) { Invoke-DrillGit branch -D $b }
    }
    if ($prevState) { $env:AI_STACK_WORKTREE_STATE = $prevState }
    else { Remove-Item Env:\AI_STACK_WORKTREE_STATE -ErrorAction SilentlyContinue }
    if ($prevLine) { $env:AI_STACK_WORK_LINE = $prevLine }
    else { Remove-Item Env:\AI_STACK_WORK_LINE -ErrorAction SilentlyContinue }
}

Write-Host ""
$failed = @($results | Where-Object { -not $_.pass })
Write-Host ("{0}/{1} checks passed." -f ($results.Count - $failed.Count), $results.Count) `
    -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
if ($failed.Count) {
    foreach ($f in $failed) { Write-Host ("  FAILED: " + $f.check + " " + $f.detail) -ForegroundColor Red }
    # KEEP THE EVIDENCE. The first version deleted the scratch namespace unconditionally, so
    # the one run that ever failed took its ledger and its queue items with it and could not
    # be diagnosed afterwards - fifteen further attempts to reproduce it, and nothing to
    # read. A failing drill's scratch dir is the only place the answer lives.
    Write-Host ""
    Write-Host "  scratch namespace KEPT for diagnosis: $scratch" -ForegroundColor Yellow
    $ledgerFile = Join-Path $scratch "oracle-escalations.jsonl"
    if (Test-Path $ledgerFile) {
        Write-Host "  --- oracle-escalations.jsonl (raw, every append) ---" -ForegroundColor Yellow
        Get-Content -Path $ledgerFile | ForEach-Object { Write-Host "  $_" }
    } else {
        Write-Host "  (no ledger file was written)" -ForegroundColor Yellow
    }
    foreach ($id in @($STALL_ID, $MOVE_ID, $GONE_ID)) {
        $p = Join-Path $QueueDir "$id.json"
        if (Test-Path $p) {
            Write-Host "  --- $id.json ---" -ForegroundColor Yellow
            Get-Content -Raw -Path $p | Write-Host
        }
    }
    exit 1
}
if (Test-Path $scratch) { Remove-Item -Recurse -Force $scratch -ErrorAction SilentlyContinue }
Write-Host "frontier-oracle-on-stall: constructed stall detected, escalation recorded, control silent." -ForegroundColor Green
Write-Host "  This is a MECHANISM proof. No real item has stalled here - see u4oracle-findings.md F4." -ForegroundColor Yellow
exit 0
