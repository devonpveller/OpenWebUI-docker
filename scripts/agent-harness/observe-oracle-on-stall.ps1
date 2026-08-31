# observe-oracle-on-stall.ps1 - fire the frontier oracle from a stall that REALLY HAPPENED.
#
#   .\scripts\agent-harness\observe-oracle-on-stall.ps1
#   .\scripts\agent-harness\observe-oracle-on-stall.ps1 -Reuse   # skip dispatch, use existing runs
#
# THIS IS NOT verify-oracle-on-stall.ps1, AND THE DIFFERENCE IS THE WHOLE POINT.
# That file is the MECHANISM drill: a scripted tester fails the same case three times against
# a branch head the script moves itself, and the detector is proven RED->GREEN against it. It
# runs in seconds, needs nothing but git and python, and the stall it detects is one it built.
#
# This file OBSERVES. It dispatches the SAME unsatisfiable item to the live little-coder
# runner N times, takes the failure text the item's own guards printed for each attempt,
# commits each attempt's real artifact, and drives those rounds through the shipped
# `queue.ps1 -Fail`. Nothing about the stall is written by this script: the rounds are real
# dispatches, the failures are real guard output, the commits carry real bytes, and the
# DETECTOR decides whether they constitute a stall.
#
# dark-factory-unification PLAN sec 2, U4: "stall -> oracle observed firing at least once".
#
# WHAT IS INDUCED, said plainly rather than buried: the item is `quadrant/items/u4-stall`,
# which is unsatisfiable by construction - two of its tests demand different outputs for the
# same input. The runner therefore cannot converge. A stall induced by choosing an impossible
# task is still a stall the runner really had; a stall SIMULATED by writing the stall state
# into a fixture is not, and this script does not do that.
#
# WHAT IT NEEDS, and why it can never be a CI check:
#   * the little-coder plane UP, with a focused project (POST /tasks is 409 without one);
#   * the inference plane UP (the local model does the work);
#   * the `coder` lease, HELD BY YOU - each round mirrors a workspace into the runner's
#     container, which wipes what is there. Pass -LeaseOwner or set AI_STACK_LEASE_OWNER.
#   * several minutes. Measured 2026-08-30: ~80-170s per round.
# A drill that needs a live local model is not a drill. This is an experiment you run and
# read.
#
# ISOLATION: the queue and the escalation ledger are written to a SCRATCH state namespace
# (.quadrant/stall/state by default), never to the operator's live queue. PLAN sec C.7 calls
# the audit trail the deliverable's twin; an observation run must not append to the ledger the
# phase is judged by unless someone chose that with -StateDir.
#
# ON FAILURE nothing is cleaned up. The run directories, the commits and the scratch queue are
# the evidence, and a run that could not be diagnosed afterwards has taught nobody anything.
#
# THE DEFECT THIS FILE SHIPPED, and what now makes it unrepresentable (2026-08-30, found and
# reproduced by a verifier - the ELEVENTH check-that-checks-nothing of this effort).
#
#   Every queue call was piped to Out-Null with no return-code check. Run in -Reuse mode -
#   its DOCUMENTED default invocation - against a state namespace that already held the
#   item, all six calls errored ("queue item 'u4-stall-probe' already exists", "not
#   'ready-to-test'", "you do not hold the tester claim"), the ledger was never appended
#   to... and the script printed "OBSERVED: the oracle fired once on a REAL stall" and
#   exited 0. It did that because the success test read the ledger and found the row a
#   PREVIOUS run had left there. A script that reports someone else's observation as its own
#   is worse than one that reports nothing.
#
#   Two mechanisms, both structural rather than a patch to the one line that lied:
#     1. NOTHING IS UNCHECKED. `Invoke-Queue` and `Invoke-GitOrDie` fail the run on any
#        non-zero exit, naming the step. There is no path from a failed step to a verdict.
#     2. THE VERDICT DERIVES FROM WHAT THIS RUN APPENDED. The ledger's row ids are snapshot
#        BEFORE the rounds; the success line is built from the row that is NEW afterwards,
#        by that row's own fields. A pre-existing row cannot be reported as an observation
#        because the only row this script will speak about is one that was not there when it
#        started.
#
#   Each run also takes its own item id and its own probe branch (a UTC stamp), so a second
#   observation in the same namespace is a second item rather than a collision - which is
#   what made the documented invocation fail in the first place.

[CmdletBinding()]
param(
    [int]$Rounds = 3,
    [string]$Item = "u4-stall",
    [string]$Runner = "little-coder",
    [string]$Target = "project",
    [string]$Id = "",
    [string]$Branch = "",
    [string]$ResultsDir = "",
    [string]$StateDir = "",
    [string]$LeaseOwner = "",
    [int]$TimeoutSeconds = 420,
    [switch]$Reuse,          # do not dispatch; use the failing runs already in -ResultsDir
    [switch]$KeepBranch      # do not delete the probe branch at the end
)

# NOTE for anyone editing: PowerShell variable names are CASE-INSENSITIVE, so a local
# `$rounds` IS the `[int]$Rounds` parameter and assigning a list to it fails at runtime with
# "Cannot convert System.Object[] to System.Int32" - reported against the SCRIPT's parameter
# binding, which is nowhere near the line at fault. The list is `$roundList` for that reason.

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$repo = (& git rev-parse --show-toplevel) | Select-Object -First 1
if (-not $repo) { Write-Host "not a git repository" -ForegroundColor Red; exit 2 }
Set-Location $repo

# ONE OBSERVATION, ONE IDENTITY. The queue is a state machine and an item id is a key in
# it: reusing one across runs is what made -Reuse fail every call in a namespace that had
# already been used. A UTC stamp makes each observation its own item and its own branch.
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
if (-not $Id) { $Id = "$Item-probe-$stamp" }
if (-not $Branch) { $Branch = "work/$Item-probe-$stamp" }
if (-not $ResultsDir) { $ResultsDir = Join-Path $repo ".quadrant\stall" }
if (-not $StateDir) { $StateDir = Join-Path $ResultsDir "state" }
if (-not $LeaseOwner) { $LeaseOwner = "$($env:AI_STACK_LEASE_OWNER)" }

New-Item -ItemType Directory -Force -Path $ResultsDir, $StateDir | Out-Null
$env:AI_STACK_WORKTREE_STATE = $StateDir
$env:AI_STACK_LEASE_OWNER = $LeaseOwner

$queue = Join-Path $PSScriptRoot "queue.ps1"
$harness = $PSScriptRoot

function Say($t, $c = "Cyan") { Write-Host ""; Write-Host $t -ForegroundColor $c }
function GitCap { $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
                  try { return @(& git.exe @args) } finally { $ErrorActionPreference = $p } }

# EVERY EXTERNAL CALL GOES THROUGH ONE OF THESE. Not style: the shipped defect was six
# `| Out-Null`s with no return-code check, and a list of call sites somebody has to remember
# to keep checking is the same guard that failed. There is no unchecked path to a verdict.
# A HASHTABLE, not an array. MEASURED while writing this: splatting an ARRAY to a
# PowerShell SCRIPT binds POSITIONALLY - `& $script @("-Propose","-Id","x")` sets
# `$Id = "-Propose"` and leaves the switch $false. (Native executables are unaffected;
# git takes an array below.) Without the exit-code check this would have produced a run
# that called nothing it meant to call and still reached a verdict - the same defect
# class, arrived at from the other direction.
# A run that refuses must not leave its probe branch behind. Two REFUSED runs during this
# fix left `work/u4-stall-probe-<stamp>` refs in the operator's branch list, because the
# branch is created before the queue pipeline and the old exit path knew nothing about it.
$script:BranchCreated = $false
function Remove-ProbeBranch {
    if (-not $script:BranchCreated -or $KeepBranch) { return }
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & git.exe branch -D $Branch 2>&1 | Out-Null } finally { $ErrorActionPreference = $p }
    $script:BranchCreated = $false
}

function Invoke-Queue([string]$What, [hashtable]$QArgs) {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = @(& $queue @QArgs 2>&1) } finally { $ErrorActionPreference = $p }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host ("REFUSED: {0} failed (queue.ps1 exit {1}). This run has observed" -f $What, $LASTEXITCODE) -ForegroundColor Red
        Write-Host ("NOTHING and says so rather than reading a ledger row it did not write.") -ForegroundColor Red
        $out | ForEach-Object { Write-Host "  $_" }
        Write-Host ("  state namespace: {0}" -f $StateDir) -ForegroundColor Yellow
        Remove-ProbeBranch
        exit 3
    }
    return $out
}

function Invoke-GitOrDie([string]$What, [string[]]$GArgs) {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = @(& git.exe @GArgs 2>&1) } finally { $ErrorActionPreference = $p }
    if ($LASTEXITCODE -ne 0) {
        Write-Host ("REFUSED: {0} failed (git exit {1})" -f $What, $LASTEXITCODE) -ForegroundColor Red
        $out | ForEach-Object { Write-Host "  $_" }
        Remove-ProbeBranch
        exit 3
    }
    return $out
}

# The ledger rows that exist BEFORE this run touches anything. The verdict at the bottom is
# allowed to speak only about rows that are NOT in this set.
function Read-LedgerIds {
    $f = Join-Path $StateDir "oracle-escalations.jsonl"
    if (-not (Test-Path $f)) { return @() }
    $ids = @()
    foreach ($line in (Get-Content $f -ErrorAction SilentlyContinue)) {
        if (-not "$line".Trim()) { continue }
        try { $ids += "$(($line | ConvertFrom-Json).id)" } catch { }
    }
    return $ids
}

# --- 1. the rounds: real dispatches, or the real ones already on disk -------------------
if (-not $Reuse) {
    if (-not $LeaseOwner) {
        Write-Host ("Each round mirrors a workspace into the runner's container, which WIPES what " +
                    "is there. Hold the lease first:`n  .\lease.ps1 -Acquire -Name coder -Owner <id>`n" +
                    "then pass -LeaseOwner <id>.") -ForegroundColor Red
        exit 2
    }
    for ($i = 1; $i -le $Rounds; $i++) {
        Say "=== DISPATCH $i/$Rounds - $Runner x $Target, item '$Item'"
        # `python -m quadrant.cli` resolves the package from the CWD, which has to be the
        # harness directory - while everything else here is git plumbing that has to run in
        # the repository. Hence the push/pop rather than one Set-Location for the script.
        Push-Location $harness
        try {
            & python -m quadrant.cli run --runner $Runner --target $Target --item $Item `
                     --results-dir $ResultsDir --timeout $TimeoutSeconds 2>&1 |
                Select-Object -First 2 | ForEach-Object { Write-Host "  $_" }
        } finally { Pop-Location }
    }
}

# A FAILING round is one whose record says `failed` AND carries a criterion with a non-zero
# exit code. `error` records are NOT rounds: they are the harness breaking, and feeding one to
# the detector would sign a tooling failure as evidence about the runner.
$runs = @(Get-ChildItem -Path $ResultsDir -Directory -ErrorAction SilentlyContinue |
          Where-Object { $_.Name -like "*-$Runner-$Target" } | Sort-Object Name)
$roundList = @()
foreach ($d in $runs) {
    $recPath = Join-Path $d.FullName "record.json"
    if (-not (Test-Path $recPath)) { continue }
    $rec = Get-Content $recPath -Raw | ConvertFrom-Json
    if ($rec.status -ne "failed") { continue }
    $failing = @($rec.acceptance | Where-Object { $_.exit_code -ne 0 })
    if (-not $failing.Count) { continue }
    $roundList += [pscustomobject]@{
        run = $d.Name; dir = $d.FullName
        reason = $failing[0].criterion
        evidence = (($failing | ForEach-Object { "$($_.check)`nexit $($_.exit_code)`n$($_.output)" }) -join "`n")
    }
}
$roundList = @($roundList | Select-Object -Last $Rounds)
Say ("FAILING ROUNDS ON DISK: {0} (need {1})" -f $roundList.Count, $Rounds)
foreach ($r in $roundList) { Write-Host ("  {0}  {1}" -f $r.run, $r.reason) }
if ($roundList.Count -lt $Rounds) {
    Write-Host ("Not enough FAILING rounds. `error` records are excluded deliberately - a " +
                "harness fault is not a round. Re-run without -Reuse.") -ForegroundColor Red
    exit 1
}

# --- 2. one real commit per round, carrying that round's real artifact ------------------
# The detector's second axis is "did the code move". Answering it with commits of what the
# runner actually produced is what separates this from the drill, which moves a marker along
# commits that already existed. Plumbing only: the working tree is never touched.
Say "=== COMMITS - one per round, containing that round's artifact"
$prefix = ((Get-Content (Join-Path $harness "quadrant\items\$Item\item.json") -Raw) |
           ConvertFrom-Json).plant_prefix
$parent = ""
$shas = @()
foreach ($r in $roundList) {
    $ws = Join-Path $r.dir "workspace\$prefix"
    $files = @(Get-ChildItem -Path $ws -File -Recurse | Sort-Object FullName)
    if (-not $files.Count) { Write-Host "  no planted files under $ws" -ForegroundColor Red; exit 1 }
    $idx = Join-Path $ResultsDir ".round-index"
    Remove-Item $idx -Force -ErrorAction SilentlyContinue
    $env:GIT_INDEX_FILE = $idx
    foreach ($f in $files) {
        # --no-filters: store the runner's bytes VERBATIM. Without it git applies the
        # checkout filters, warns "LF will be replaced by CRLF" on every file, and the commit
        # records a line-ending-normalised copy of the artifact rather than the artifact. This
        # commit is evidence about what a runner produced; a filter has no business in it.
        $blob = (GitCap hash-object -w --no-filters $f.FullName | Select-Object -First 1)
        $rel = "$prefix/" + ($f.FullName.Substring($ws.Length + 1) -replace '\\', '/')
        GitCap update-index --add --cacheinfo "100644,$blob,$rel" | Out-Null
    }
    $tree = (GitCap write-tree | Select-Object -First 1)
    Remove-Item Env:\GIT_INDEX_FILE
    $msg = "$Item round $($shas.Count + 1): the artifact $Runner produced in run $($r.run)"
    $c = if ($parent) { (GitCap commit-tree $tree -p $parent -m $msg | Select-Object -First 1) }
         else { (GitCap commit-tree $tree -m $msg | Select-Object -First 1) }
    if (-not $c) { Write-Host "  commit-tree produced nothing" -ForegroundColor Red; exit 1 }
    $parent = $c; $shas += $c
    Write-Host ("  round {0}  {1}  <- {2}" -f $shas.Count, $c.Substring(0, 12), $r.run)
}
Remove-Item (Join-Path $ResultsDir ".round-index") -Force -ErrorAction SilentlyContinue
if (@($shas | Select-Object -Unique).Count -ne $shas.Count) {
    Write-Host ("Two rounds produced the SAME commit - the runner emitted identical bytes. The " +
                "movement axis cannot be observed on this set; re-run for fresh attempts.") -ForegroundColor Red
    exit 1
}

# --- 3. the shipped queue pipeline, with those rounds ------------------------------------
$planFile = Join-Path $ResultsDir "$Item-test-plan.md"
Set-Content -Path $planFile -Encoding ascii -Value @(
    "# Test plan - $Item observation",
    "Round n: dispatch the $Item item to $Runner, run the item's PRISTINE guards against what",
    "it produced, and record the verdict with the guard output as evidence.",
    "PASS: every pristine test passes. FAIL: any does not.",
    "The item is unsatisfiable by construction, so every round is expected to FAIL. What is",
    "under observation is whether the stall detector fires on the real rounds.")
$anchorFile = Join-Path $ResultsDir "$Item-anchor.json"
Set-Content -Path $anchorFile -Encoding ascii -Value @(
    "{",
    "  ""goal"": ""Observe frontier-oracle-on-stall firing on a stall that happened."",",
    "  ""artifact"": ""An oracle-escalation ledger row produced by queue.ps1 -Fail from real rounds."",",
    "  ""audience"": ""Whoever checks U4's Validated-by column."",",
    "  ""acceptance"": [",
    "    ""The rounds before the threshold record no escalation."",",
    "    ""The threshold round records exactly one escalation naming the local runner.""",
    "  ],",
    "  ""out_of_scope"": [ ""The live queue namespace - this probe runs in a scratch state dir."" ],",
    "  ""findings_sink"": ""documentation/notes/u4close-findings.md""",
    "}")

# THE BASELINE. Everything the verdict is allowed to claim is measured against this.
$ledgerBefore = @(Read-LedgerIds)
Say ("=== LEDGER BEFORE: {0} row(s) in {1}" -f $ledgerBefore.Count,
     (Join-Path $StateDir "oracle-escalations.jsonl"))

Invoke-GitOrDie "pointing $Branch at round 1" @("branch", "-f", $Branch, $shas[0]) | Out-Null
$script:BranchCreated = $true
Invoke-Queue "queue -Propose" @{ Propose = $true; Id = $Id; Anchor = $anchorFile
                                 Developer = "observer" } | Out-Null
# NOT an operator confirmation, and the -By string says so rather than forging one. PLAN C.1:
# U0-U7 items do not run through queue.ps1's human gates; this is satisfied mechanically only
# because -Submit refuses without it.
Invoke-Queue "queue -ConfirmAnchor" @{
    ConfirmAnchor = $true; Id = $Id
    By = "observe-oracle-on-stall.ps1 (scratch namespace; PLAN C.1 - NOT an operator confirmation)"
} | Out-Null
Invoke-Queue "queue -Submit" @{ Submit = $true; Id = $Id; Branch = $Branch
                                Developer = "observer"; TestPlan = $planFile
                                RunnerProfile = "local-work-cloud-review" } | Out-Null

for ($i = 0; $i -lt $roundList.Count; $i++) {
    Say ("=== ROUND {0}  head {1}  ({2})" -f ($i + 1), $shas[$i].Substring(0, 12), $roundList[$i].run)
    if ($i -gt 0) {
        Invoke-GitOrDie ("pointing $Branch at round " + ($i + 1)) @("branch", "-f", $Branch, $shas[$i]) | Out-Null
        Invoke-Queue ("queue -Resubmit (round " + ($i + 1) + ")") @{
            Resubmit = $true; Id = $Id; By = "observer" } | Out-Null
    }
    Invoke-Queue ("queue -Claim (round " + ($i + 1) + ")") @{
        Claim = $true; Id = $Id; Role = "tester"; By = "observer-tester" } | Out-Null
    Invoke-Queue ("queue -Fail (round " + ($i + 1) + ")") @{
        Fail = $true; Id = $Id; By = "observer-tester"; Reason = $roundList[$i].reason
        Evidence = $roundList[$i].evidence; PlanAdequate = $true } |
        Where-Object { "$_" -match "stall|ORACLE|round |ledger" } |
        ForEach-Object { Write-Host "  $_" }
}

Say "=== THE LEDGER"
Invoke-Queue "queue -Oracle" @{ Oracle = $true; Id = $Id } | ForEach-Object { Write-Host $_ }

# THE VERDICT IS DERIVED FROM WHAT THIS RUN APPENDED, and from nothing else. Reading the
# ledger for "a row about this item" is what let a previous run's row be reported as this
# run's observation; the ids that existed before are excluded by construction.
$ledgerAfter = @(Read-LedgerIds)
$newIds = @($ledgerAfter | Where-Object { $ledgerBefore -notcontains $_ })

$rows = @(& python (Join-Path $harness "oracle_on_stall.py") report --repo $repo --item $Id --json |
          ConvertFrom-Json)
if ($LASTEXITCODE -ne 0) {
    Write-Host "REFUSED: oracle_on_stall.py report failed - this run has no verdict." -ForegroundColor Red
    Remove-ProbeBranch
    exit 3
}
$fired = @($rows | Where-Object { $_.outcome -eq "escalate" -and $newIds -contains $_.id })

if (-not $KeepBranch) {
    Invoke-GitOrDie "deleting the probe branch $Branch" @("branch", "-D", $Branch) | Out-Null
    $still = @(& git.exe branch --list $Branch)
    if ($still.Count -gt 0) {
        Write-Host ("REFUSED: {0} still exists after 'git branch -D'." -f $Branch) -ForegroundColor Red
        exit 3
    }
    $script:BranchCreated = $false
    Write-Host ("  probe branch {0} deleted (verified: 'git branch --list {0}' is empty)" -f $Branch)
}

Write-Host ""
if ($fired.Count -eq 1) {
    Write-Host ("OBSERVED: the oracle fired once on a REAL stall - {0}/{1} -> {2}/{3}, hand back to {4}." -f `
        $fired[0].stalled_runner, $fired[0].stalled_model, $fired[0].oracle_runner,
        $fired[0].oracle_model, $fired[0].hand_back_to) -ForegroundColor Green
    Write-Host ("  rounds={0} stall={1}/{2} distinct-signatures={3}" -f `
        $fired[0].rounds, $fired[0].stall, $fired[0].threshold, $fired[0].signatures_seen)
    Write-Host ("  ledger row {0} - APPENDED BY THIS RUN ({1} row(s) before it started, {2} after)" -f `
        $fired[0].id, $ledgerBefore.Count, $ledgerAfter.Count)
    Write-Host ("  ledger: {0}" -f (Join-Path $StateDir "oracle-escalations.jsonl"))
    Write-Host "  The task was chosen to be impossible; the failure to converge was the runner's." -ForegroundColor Yellow
    exit 0
}
Write-Host ("NOT OBSERVED: this run appended {0} escalation row(s) for '{1}'. The ledger held {2} " +
            "row(s) when it started and {3} now; rows written by an earlier run are NOT this run's " +
            "observation and are excluded by id. The rounds are on disk and the scratch namespace " +
            "is kept - read the trail before re-running." -f `
            $fired.Count, $Id, $ledgerBefore.Count, $ledgerAfter.Count) -ForegroundColor Red
exit 1
