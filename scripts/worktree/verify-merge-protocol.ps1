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
# NOTE: no `2>&1` on any git call, and the helpers flip $ErrorActionPreference themselves. In
# PS5.1, redirecting OR capturing a native command's stderr under 'Stop' turns git's ordinary
# progress chatter into a terminating error - this script died on exactly that once.

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here
$repo = "d:\Open WebUI\ai-stack"
$wtScripts = Join-Path $repo "scripts\worktree"
$queue = Join-Path $wtScripts "queue.ps1"
. (Join-Path $wtScripts "common.ps1")
$QueueDir = Join-Path (Get-SharedStateDir) "queue"
$results = @()

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
$reg = Join-Path (Get-SharedStateDir) "worktrees.json"
if (Test-Path $reg) { '{ "worktrees": {} }' | Set-Content $reg -Encoding ASCII }

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
foreach ($id in @("a", "b")) {
    & $queue -Submit -Id "drill-$id" -Branch "work/drill$id" -Developer "wt-drill$id" `
        -TestPlan "DRILL-NOTE.md states the timeout; verify the file reads as intended" | Out-Null
}
Check "both items queued for testing" ((Get-QueueState "drill-a") -eq "ready-to-test" -and (Get-QueueState "drill-b") -eq "ready-to-test")
& $queue -Submit -Id "drill-noplan" -Branch "work/drilla" -Developer "wt-drilla" 2>&1 | Out-Null
Check "a submission with NO test plan is refused" ($LASTEXITCODE -ne 0)

Step 4 "separation of duties is ENFORCED, not trusted"
& $queue -Claim -Id drill-a -Role tester -By wt-drilla 2>&1 | Out-Null
Check "the developer cannot TEST their own work (exit 4)" ($LASTEXITCODE -eq 4)
& $queue -Claim -Id drill-a -Role reviewer -By wt-drilla 2>&1 | Out-Null
Check "the developer cannot REVIEW their own work (exit 4)" ($LASTEXITCODE -eq 4)

Step 5 "an independent tester executes both plans"
foreach ($id in @("a", "b")) {
    & $queue -Claim -Id "drill-$id" -Role tester -By wt-tester | Out-Null
    & $queue -Pass -Id "drill-$id" -By wt-tester -Evidence "read the file; content matches the plan" -PlanAdequate | Out-Null
}
Check "both items passed testing" ((Get-QueueState "drill-a") -eq "ready-review" -and (Get-QueueState "drill-b") -eq "ready-review")
& $queue -Claim -Id drill-a -Role tester -By wt-tester2 2>&1 | Out-Null
Check "a tester claim on an already-passed item is refused" ($LASTEXITCODE -ne 0)

Step 6 "the reviewer - neither developer - lands the first item"
& $queue -Claim -Id drill-a -Role reviewer -By wt-reviewer | Out-Null
Check "reviewer claimed drill-a" ((Get-QueueState "drill-a") -eq "reviewing")
Invoke-DrillGit -C $wtA rebase drill/verify-d
$wtMerge = Join-Path $repo ".claude\worktrees\merge-line"
Invoke-DrillGit worktree add $wtMerge drill/verify-d
Check "merge worktree created (never the operator's checkout)" (Test-Path $wtMerge)
Invoke-DrillGit -C $wtMerge merge --no-ff work/drilla -m "merge drill A: raise timeout to 60s (evidence: drill)"
& $queue -Merged -Id drill-a -By wt-reviewer -Sha ((Get-DrillGit -C $wtMerge rev-parse HEAD).Trim()) | Out-Null
Check "drill-a merged by the reviewer" ((Get-QueueState "drill-a") -eq "merged")

Step 7 "the second item's rebase CONFLICTS - the later merger adapts"
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

Step 8 "STALE PASS: the rebase changed the tested content, so it goes BACK to test"
$afterRebase = (Get-DrillGit -C $wtB rev-parse HEAD).Trim()
Check "the tested sha is no longer what would land" ($afterRebase -ne $testedAt)
& $queue -Requeue -Id drill-b -By wt-reviewer -Reason "rebase onto A's merge changed the file; the pass no longer describes it" | Out-Null
Check "reviewer returned it to testing rather than merging" ((Get-QueueState "drill-b") -eq "ready-to-test")
& $queue -Claim -Id drill-b -Role tester -By wt-tester | Out-Null
& $queue -Pass -Id drill-b -By wt-tester -Evidence "re-read the adapted file; both intents present" -PlanAdequate | Out-Null
Check "re-tested at the new content" ((Get-QueueState "drill-b") -eq "ready-review")

Step 9 "the reviewer lands the adapted work"
& $queue -Claim -Id drill-b -Role reviewer -By wt-reviewer | Out-Null
Invoke-DrillGit -C $wtMerge merge --no-ff work/drillb -m "merge drill B: per-caller override, A's default kept (evidence: drill)"
& $queue -Merged -Id drill-b -By wt-reviewer -Sha ((Get-DrillGit -C $wtMerge rev-parse HEAD).Trim()) | Out-Null
Check "drill-b merged after re-test" ((Get-QueueState "drill-b") -eq "merged")

Step 10 "outcome: both intents survive, history readable, development untouched"
$final = Get-Content (Join-Path $wtMerge "DRILL-NOTE.md") -Raw
Check "A's intent survived the later merge" ($final -match "raised to 60s")
Check "B's adapted intent is present" ($final -match "per-caller override")
$merges = @(Get-DrillGit -C $wtMerge log --oneline --merges "$devBefore..drill/verify-d")
Check "two --no-ff merge commits on the line" ($merges.Count -eq 2) ("count=" + $merges.Count)
Check "development NEVER moved" ((Get-DrillGit rev-parse development).Trim() -eq $devBefore)
Check "operator checkout still on its own branch" ((Get-DrillGit rev-parse --abbrev-ref HEAD).Trim() -eq $mainBranch)

Step 11 "cleanup - leave nothing behind"
Invoke-DrillGit worktree remove --force $wtMerge
foreach ($id in @("drilla", "drillb")) {
    & (Join-Path $wtScripts "remove-worktree.ps1") -Id $id -MergedInto "drill/verify-d" -Force | Out-Null
}
Invoke-DrillGit branch -D drill/verify-d
Invoke-DrillGit worktree prune
Clear-DrillQueue
Check "all drill worktrees gone" (@(Get-ChildItem (Join-Path $repo ".claude\worktrees") -ErrorAction SilentlyContinue).Count -eq 0)
Check "no drill/work branches left" (@(Get-DrillGit branch --list "work/*" "drill/*").Count -eq 0)
Check "queue emptied of drill items" (@(Get-ChildItem -Path $QueueDir -Filter "drill-*" -ErrorAction SilentlyContinue).Count -eq 0)

Write-Host "`n================ DRILL SUMMARY ================" -ForegroundColor Cyan
$fail = @($results | Where-Object { -not $_.pass })
$results | ForEach-Object { Write-Host ("  {0,-6} {1}" -f $(if ($_.pass) { "PASS" } else { "FAIL" }), $_.check) }
Write-Host ("`n{0}/{1} checks passed" -f ($results.Count - $fail.Count), $results.Count) `
    -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
if ($fail.Count) { exit 1 }
