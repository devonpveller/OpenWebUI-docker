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

if (-not $Id) { $Id = "$Item-probe" }
if (-not $Branch) { $Branch = "work/$Item-probe" }
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

GitCap branch -f $Branch $shas[0] | Out-Null
& $queue -Propose -Id $Id -Anchor $anchorFile -Developer "observer" | Out-Null
# NOT an operator confirmation, and the -By string says so rather than forging one. PLAN C.1:
# U0-U7 items do not run through queue.ps1's human gates; this is satisfied mechanically only
# because -Submit refuses without it.
& $queue -ConfirmAnchor -Id $Id `
         -By "observe-oracle-on-stall.ps1 (scratch namespace; PLAN C.1 - NOT an operator confirmation)" | Out-Null
& $queue -Submit -Id $Id -Branch $Branch -Developer "observer" -TestPlan $planFile `
         -RunnerProfile "local-work-cloud-review" | Out-Null

for ($i = 0; $i -lt $roundList.Count; $i++) {
    Say ("=== ROUND {0}  head {1}  ({2})" -f ($i + 1), $shas[$i].Substring(0, 12), $roundList[$i].run)
    if ($i -gt 0) {
        GitCap branch -f $Branch $shas[$i] | Out-Null
        & $queue -Resubmit -Id $Id -By "observer" | Out-Null
    }
    & $queue -Claim -Id $Id -Role tester -By "observer-tester" | Out-Null
    & $queue -Fail -Id $Id -By "observer-tester" -Reason $roundList[$i].reason `
             -Evidence $roundList[$i].evidence -PlanAdequate 6>&1 |
        Where-Object { "$_" -match "stall|ORACLE|round \d|ledger" } |
        ForEach-Object { Write-Host "  $_" }
}

Say "=== THE LEDGER"
& $queue -Oracle -Id $Id

$rows = @(& python (Join-Path $harness "oracle_on_stall.py") report --repo $repo --item $Id --json |
          ConvertFrom-Json)
$fired = @($rows | Where-Object { $_.outcome -eq "escalate" })
if (-not $KeepBranch) { GitCap branch -D $Branch | Out-Null }

Write-Host ""
if ($fired.Count -eq 1) {
    Write-Host ("OBSERVED: the oracle fired once on a REAL stall - {0}/{1} -> {2}/{3}, hand back to {4}." -f `
        $fired[0].stalled_runner, $fired[0].stalled_model, $fired[0].oracle_runner,
        $fired[0].oracle_model, $fired[0].hand_back_to) -ForegroundColor Green
    Write-Host ("  rounds={0} stall={1}/{2} distinct-signatures={3}   ledger: {4}" -f `
        $fired[0].rounds, $fired[0].stall, $fired[0].threshold, $fired[0].signatures_seen,
        (Join-Path $StateDir "oracle-escalations.jsonl"))
    Write-Host "  The task was chosen to be impossible; the failure to converge was the runner's." -ForegroundColor Yellow
    exit 0
}
Write-Host ("NOT OBSERVED: {0} escalation row(s) for '{1}'. The rounds are on disk and the " +
            "scratch namespace is kept - read the trail before re-running." -f $fired.Count, $Id) -ForegroundColor Red
exit 1
