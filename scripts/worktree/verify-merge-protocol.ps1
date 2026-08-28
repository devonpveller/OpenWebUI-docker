# verify-merge-protocol.ps1 - executable proof of MERGE-PROTOCOL.md's two-agent path.
#
#   .\scripts\worktree\verify-merge-protocol.ps1     # 21 checks, ~1 min, cleans up after itself
#
# Run this after changing lease.ps1, the worktree scripts, or the protocol itself. The unit
# tests (test_worktree.py) cover lease SEMANTICS; this covers the end-to-end CHOREOGRAPHY
# two agents actually perform - which is where the protocol's real defects turned out to be
# (its first run proved Step 4's `git checkout development` was unsafe).
#
# Runs against a SCRATCH line (drill/verify-d), never `development`: the drill validates
# protocol mechanics, and the target branch is a parameter. The operator's checkout is
# never switched (a bridge turn could land mid-switch). Idempotent: the preamble clears
# anything a previous failed run left behind.
#
# Covers: lease mutual exclusion under real contention, rebase conflict, later-merger-adapts,
# --no-ff history, and full cleanup. Does NOT cover Tier-2 thread negotiation (needs two live
# sessions) - the human half is asserted by inspection, not by this script.
#
# NOTE: no `2>&1` on any git call. PS5.1 wraps redirected native stderr in ErrorRecords, and
# with $ErrorActionPreference='Stop' git's ordinary progress chatter becomes a terminating
# error - this script died on exactly that on its first run, the same trap new-worktree.ps1
# hit. Stderr flows to the console; $LASTEXITCODE is the only honest signal.

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here
$repo = "d:\Open WebUI\ai-stack"
$wtScripts = Join-Path $repo "scripts\worktree"
$lease = Join-Path $wtScripts "lease.ps1"
. (Join-Path $wtScripts "common.ps1")
# Resolve the lock path rather than hardcoding it: the lease dir moved to the shared
# per-repository namespace, and this drill asserted on the old location for one run.
$mergeLockFile = Join-Path (Join-Path (Get-SharedStateDir) "locks") "merge.json"
$results = @()

function Step($n, $text) { Write-Host "`n=== $n. $text ===" -ForegroundColor Cyan }
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = $ok; detail = $detail }
    $c = if ($ok) { "Green" } else { "Red" }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) -ForegroundColor $c
}
# TWO scoping traps hit live while writing this drill:
#  1. PowerShell is case-insensitive, so `& git` inside a function named Invoke-DrillGit calls ITSELF
#     -> call-depth overflow. Helpers must invoke git.EXE explicitly.
#  2. Functions leak into scripts you invoke. A helper named `Git` shadowed the real binary
#     INSIDE new-worktree.ps1, which then reported "not inside a git repository". Never name
#     a wrapper after the command it wraps.
#  3. Even without a redirect, CAPTURING or piping native output under 'Stop' makes git's
#     stderr (warnings, progress) terminating - so both helpers flip the pref themselves.
function Invoke-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { & git.exe @args | Out-Null } finally { $ErrorActionPreference = $prev }
}
function Get-DrillGit {
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { return (& git.exe @args) } finally { $ErrorActionPreference = $prev }
}

Set-Location $repo

# --- idempotent preamble: clear anything a previous failed run left -----------------
foreach ($p in @("merge-line", "wt-drilla", "wt-drillb")) {
    $full = Join-Path $repo ".claude\worktrees\$p"
    if (Test-Path $full) { & git.exe worktree remove --force $full | Out-Null }
}
& git.exe worktree prune | Out-Null
foreach ($b in @("work/drilla", "work/drillb", "drill/verify-d")) {
    & git.exe rev-parse --verify --quiet "refs/heads/$b" | Out-Null
    if ($LASTEXITCODE -eq 0) { & git.exe branch -D $b | Out-Null }
}
foreach ($o in @("wt-drilla", "wt-drillb")) { & $lease -Release -Name merge -Owner $o | Out-Null }
$reg = Join-Path (Get-SharedStateDir) "worktrees.json"
if (Test-Path $reg) { '{ "worktrees": {} }' | Set-Content $reg -Encoding ASCII }

$devBefore = (Get-DrillGit rev-parse development).Trim()
$mainBranch = (Get-DrillGit rev-parse --abbrev-ref HEAD).Trim()
Write-Host ("development before: {0} | operator checkout on: {1}" -f $devBefore.Substring(0, 8), $mainBranch)

Step 1 "scratch line + two agent worktrees (via the real provisioning script)"
Invoke-DrillGit branch drill/verify-d development
foreach ($id in @("drilla", "drillb")) {
    & (Join-Path $wtScripts "new-worktree.ps1") -Id $id -Base "drill/verify-d" -OwnerKind manual -OwnerRef "verify-d" | Out-Null
    Check "worktree wt-$id provisioned" (Test-Path (Join-Path $repo ".claude\worktrees\wt-$id"))
}
$wtA = Join-Path $repo ".claude\worktrees\wt-drilla"
$wtB = Join-Path $repo ".claude\worktrees\wt-drillb"

Step 2 "both agents edit THE SAME file with conflicting intent"
Set-Content -Path (Join-Path $wtA "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "", "Agent A owns this line: A needs the timeout raised to 60s.")
Set-Content -Path (Join-Path $wtB "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "", "Agent B owns this line: B needs the timeout lowered to 5s.")
Invoke-DrillGit -C $wtA add DRILL-NOTE.md
Invoke-DrillGit -C $wtA commit -q -m "drill A: raise timeout to 60s"
Invoke-DrillGit -C $wtB add DRILL-NOTE.md
Invoke-DrillGit -C $wtB commit -q -m "drill B: lower timeout to 5s"
Check "two divergent commits exist" ((Get-DrillGit -C $wtA rev-parse HEAD) -ne (Get-DrillGit -C $wtB rev-parse HEAD))

Step 3 "lease mutual exclusion under real contention"
& $lease -Acquire -Name merge -Owner wt-drilla -Thread drill-thread-a | Out-Null
Check "agent A acquired the merge lease" ($LASTEXITCODE -eq 0)
& $lease -Acquire -Name merge -Owner wt-drillb | Out-Null
Check "agent B is BLOCKED while A holds it (exit 3)" ($LASTEXITCODE -eq 3)
$holder = (Get-Content $mergeLockFile -Raw | ConvertFrom-Json).owner
Check "lease names exactly one owner" ($holder -eq "wt-drilla") "holder=$holder"

Step 4 "agent A rebases and merges into the shared line"
Invoke-DrillGit -C $wtA rebase drill/verify-d
# The shared line is merged in a DEDICATED merge worktree - never by switching the
# operator's checkout, and never by switching the agent's own worktree off its branch.
$wtMerge = Join-Path $repo ".claude\worktrees\merge-line"
Invoke-DrillGit worktree add $wtMerge drill/verify-d
Check "merge worktree created for the shared line" (Test-Path $wtMerge)
Invoke-DrillGit -C $wtMerge merge --no-ff work/drilla -m "merge drill A: raise timeout to 60s (evidence: drill)"
Check "agent A's merge landed" ((Get-DrillGit -C $wtMerge log --oneline -1 --format=%s) -like "merge drill A*")
& $lease -Release -Name merge -Owner wt-drilla | Out-Null
Check "agent A released the lease" (-not (Test-Path $mergeLockFile))

Step 5 "agent B acquires, rebases -> CONFLICT (the point of the drill)"
& $lease -Acquire -Name merge -Owner wt-drillb -Thread drill-thread-b | Out-Null
Check "agent B can now acquire" ($LASTEXITCODE -eq 0)
Invoke-DrillGit -C $wtB rebase drill/verify-d   # expected to fail: conflict
$conflicted = @(Get-DrillGit -C $wtB diff --name-only --diff-filter=U)
Check "rebase produced a real conflict" ($conflicted -contains "DRILL-NOTE.md") ("files: " + ($conflicted -join ","))

Step 6 "LATER-MERGER-ADAPTS: B keeps A's landed line and adds its own intent"
Set-Content -Path (Join-Path $wtB "DRILL-NOTE.md") -Encoding ascii -Value @(
    "# Drill note", "",
    "Agent A owns this line: A needs the timeout raised to 60s.",
    "Agent B (later merger, adapted): B's 5s need is served by a per-caller override,",
    "so A's landed 60s default is untouched.")
Invoke-DrillGit -C $wtB add DRILL-NOTE.md
$env:GIT_EDITOR = "true"
Invoke-DrillGit -C $wtB rebase --continue
Check "B's rebase completed after adapting" (-not (Test-Path (Join-Path $wtB ".git\rebase-merge")))
Invoke-DrillGit -C $wtMerge merge --no-ff work/drillb -m "merge drill B: per-caller override, A's default kept (evidence: drill)"
Check "agent B's merge landed" ((Get-DrillGit -C $wtMerge log --oneline -1 --format=%s) -like "merge drill B*")
& $lease -Release -Name merge -Owner wt-drillb | Out-Null

Step 7 "outcome: both intents survive, history is readable, development untouched"
$final = Get-Content (Join-Path $wtMerge "DRILL-NOTE.md") -Raw
Check "A's intent survived the later merge" ($final -match "raised to 60s")
Check "B's adapted intent is present" ($final -match "per-caller override")
# Scope to commits ADDED by the drill. `--merges <branch>` walks all reachable history
# (25 merges from development) - the first run of this drill asserted on that and failed.
$merges = @(Get-DrillGit -C $wtMerge log --oneline --merges "$devBefore..drill/verify-d")
Check "two --no-ff merge commits on the line" ($merges.Count -eq 2) ("count=" + $merges.Count)
$devAfter = (Get-DrillGit rev-parse development).Trim()
Check "development NEVER moved" ($devAfter -eq $devBefore) ("{0} -> {1}" -f $devBefore.Substring(0, 8), $devAfter.Substring(0, 8))
Check "operator checkout still on its own branch" ((Get-DrillGit rev-parse --abbrev-ref HEAD).Trim() -eq $mainBranch)

Step 8 "cleanup - leave nothing behind"
Invoke-DrillGit worktree remove --force $wtMerge
foreach ($id in @("drilla", "drillb")) {
    & (Join-Path $wtScripts "remove-worktree.ps1") -Id $id -MergedInto "drill/verify-d" -Force | Out-Null
}
Invoke-DrillGit branch -D drill/verify-d
Invoke-DrillGit worktree prune
Check "all drill worktrees gone" (@(Get-ChildItem (Join-Path $repo ".claude\worktrees") -ErrorAction SilentlyContinue).Count -eq 0)
Check "no drill/work branches left" (@(Get-DrillGit branch --list "work/*" "drill/*").Count -eq 0)
Check "no leases held" (-not (Test-Path $mergeLockFile))

Write-Host "`n================ DRILL SUMMARY ================" -ForegroundColor Cyan
$fail = @($results | Where-Object { -not $_.pass })
$results | ForEach-Object { Write-Host ("  {0,-6} {1}" -f $(if ($_.pass) { "PASS" } else { "FAIL" }), $_.check) }
Write-Host ("`n{0}/{1} checks passed" -f ($results.Count - $fail.Count), $results.Count) -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
if ($fail.Count) { exit 1 }
