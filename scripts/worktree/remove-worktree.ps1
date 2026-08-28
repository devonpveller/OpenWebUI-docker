# remove-worktree.ps1 - retire an agent's worktree WITHOUT eating unlanded work.
#
# Why a script instead of `git worktree remove`: the dangerous case is a worktree that
# still holds the only copy of someone's work. This refuses by default when the tree is
# dirty or the branch has commits not reachable from `development`, and says exactly
# what it found - the repo's "never delete without saying what and why" discipline,
# enforced mechanically. Callers: bridge `close`, the test reaper, and hand cleanup.
#
# Usage:
#   .\remove-worktree.ps1 -Id drill                 # refuses if dirty/unmerged
#   .\remove-worktree.ps1 -Id drill -Force          # discard anyway (say so out loud)
#   .\remove-worktree.ps1 -Id drill -WhatIfOnly     # report only
#   .\remove-worktree.ps1 -PruneRegistry            # drop rows whose path is gone

[CmdletBinding()]
param(
    [string]$Id = "",
    [string]$MergedInto = "development",
    [switch]$Force,
    [switch]$KeepBranch,
    [switch]$WhatIfOnly,
    [switch]$PruneRegistry
)

$ErrorActionPreference = "Stop"
$Registry = Join-Path $PSScriptRoot "state\worktrees.json"

function Fail([string]$Message) { Write-Host "ERROR: $Message" -ForegroundColor Red; exit 1 }

function Read-Registry {
    if (-not (Test-Path $Registry)) { return @{} }
    $out = @{}
    $parsed = Get-Content -Raw -Path $Registry | ConvertFrom-Json
    if ($parsed.worktrees) {
        foreach ($p in $parsed.worktrees.PSObject.Properties) { $out[$p.Name] = $p.Value }
    }
    return $out
}

function Write-Registry([hashtable]$Rows) {
    $tmp = "$Registry.tmp"
    (@{ worktrees = $Rows } | ConvertTo-Json -Depth 6) | Set-Content -Path $tmp -Encoding ASCII
    Move-Item -Path $tmp -Destination $Registry -Force
}

$rows = Read-Registry

if ($PruneRegistry) {
    $gone = @($rows.Keys | Where-Object { -not (Test-Path $rows[$_].path) })
    if (-not $gone.Count) { Write-Host "Registry clean - every row's path exists." -ForegroundColor Green; exit 0 }
    Write-Host ("Dropping {0} row(s) whose worktree is gone: {1}" -f $gone.Count, ($gone -join ", ")) -ForegroundColor Yellow
    if (-not $WhatIfOnly) {
        foreach ($g in $gone) { $rows.Remove($g) }
        Write-Registry $rows
        & git worktree prune
    }
    exit 0
}

if (-not $Id) { Fail "pass -Id <id> (or -PruneRegistry)" }
if (-not $rows.ContainsKey($Id)) { Fail "no registered worktree with id '$Id'" }

$row = $rows[$Id]
$path = $row.path
$branch = $row.branch
if (-not (Test-Path $path)) {
    Write-Host "Worktree path already gone; dropping the registry row." -ForegroundColor Yellow
    if (-not $WhatIfOnly) { $rows.Remove($Id); Write-Registry $rows; & git worktree prune }
    exit 0
}

# --- what would be lost? ----------------------------------------------------------
$dirty = @(& git -C $path status --porcelain)
$unmerged = @()
$null = & git rev-parse --verify --quiet "refs/heads/$MergedInto"
if ($LASTEXITCODE -eq 0) {
    $unmerged = @(& git -C $path log --oneline "$MergedInto..$branch")
} else {
    Write-Host ("  note: no local '{0}' branch to compare against - treating all commits as unmerged" -f $MergedInto) -ForegroundColor Yellow
    $unmerged = @(& git -C $path log --oneline -n 20 $branch)
}

Write-Host ("Worktree {0} ({1})" -f $Id, $path) -ForegroundColor Cyan
Write-Host ("  branch          : {0}" -f $branch)
Write-Host ("  uncommitted     : {0} file(s)" -f $dirty.Count)
Write-Host ("  commits not in {0}: {1}" -f $MergedInto, $unmerged.Count)
foreach ($line in ($dirty | Select-Object -First 8)) { Write-Host ("      " + $line) }
foreach ($line in ($unmerged | Select-Object -First 8)) { Write-Host ("      " + $line) }

$blocked = ($dirty.Count -gt 0 -or $unmerged.Count -gt 0)
if ($WhatIfOnly) {
    Write-Host ("  verdict         : " + $(if ($blocked) { "WOULD REFUSE (needs -Force)" } else { "safe to remove" })) -ForegroundColor $(if ($blocked) { "Yellow" } else { "Green" })
    exit 0
}
if ($blocked -and -not $Force) {
    Write-Host "REFUSED: this worktree still holds work that is nowhere else." -ForegroundColor Red
    Write-Host "  Land it (MERGE-PROTOCOL.md), or re-run with -Force to discard it deliberately." -ForegroundColor Red
    exit 2
}
if ($blocked -and $Force) {
    Write-Host "  -Force given: discarding the above deliberately." -ForegroundColor Yellow
}

& git worktree remove --force $path
if ($LASTEXITCODE -ne 0) { Fail "git worktree remove failed for $path" }
if (-not $KeepBranch) {
    & git branch -D $branch
    if ($LASTEXITCODE -ne 0) { Write-Host ("  WARNING: could not delete branch {0} - remove it by hand" -f $branch) -ForegroundColor Yellow }
}
$rows.Remove($Id)
Write-Registry $rows
& git worktree prune
Write-Host ("Removed worktree {0}{1}." -f $Id, $(if ($KeepBranch) { " (branch kept)" } else { " and branch $branch" })) -ForegroundColor Green
exit 0
