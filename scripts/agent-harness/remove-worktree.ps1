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
    [string]$MergedInto = "",   # default: the resolved work line (see common.ps1)
    [switch]$Force,
    [switch]$KeepBranch,
    [switch]$WhatIfOnly,
    [switch]$PruneRegistry
)

# 'Stop' for cmdlets. Every captured `git` call goes through git-io's Invoke-GitCapture,
# which owns the PS5.1 rule that capturing native stderr under 'Stop' is fatal.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
$Registry = Join-Path (Get-SharedStateDir) "worktrees.json"
# Comparing against the wrong line would misjudge "unmerged work" and could green-light
# deleting a worktree whose commits are nowhere else.
if (-not $MergedInto) { $MergedInto = Resolve-WorkLine }


function Fail([string]$Message) { Write-Host "ERROR: $Message" -ForegroundColor Red; exit 1 }

# Every bare `& git` in this script used to run under $ErrorActionPreference='Stop'. That is
# fine until a CALLER captures our output: `remove-worktree.ps1 ... 2>&1 | ...` turns git's
# ordinary stderr into a terminating NativeCommandError, and the script dies AT the git line
# - before the error handling written for exactly that git call can run. The bridge `close`
# path and the test reaper both invoke this script and read its output, so this was a live
# defect, not a theoretical one; it surfaced when the probe for the leak fix below captured
# stderr and killed the script mid-removal.
#
# Invoke-GitCapture (git-io.ps1) owns this rule for calls we read. This is its sibling for
# calls we only need the EXIT CODE from, which is why it deliberately returns that.
function Invoke-GitQuiet {
    param([string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & git @GitArgs 2>&1 | Out-Null; return $LASTEXITCODE } finally { $ErrorActionPreference = $prev }
}

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
        $null = Invoke-GitQuiet @('worktree','prune')
    }
    exit 0
}

if (-not $Id) { Fail "pass -Id <id> (or -PruneRegistry)" }
if (-not $rows.ContainsKey($Id)) {
    # The registry is convenience, not truth - git is. A row can be missing because it
    # was never written, or because something clobbered the file (a drill did exactly
    # that mid-run, and this script was then the only way to retire the worktree and
    # could not). Reconstruct from `git worktree list` instead of refusing.
    $wtPath = ""
    $current = ""
    foreach ($row in (Invoke-GitCapture @("worktree", "list", "--porcelain"))) {
        if ($row -like "worktree *") { $current = $row.Substring(9) }
        if ($row -eq "branch refs/heads/work/$Id") { $wtPath = $current }
    }
    if (-not $wtPath) { Fail "no registered worktree with id '$Id', and git knows no worktree on work/$Id" }
    Write-Host ("  note: no registry row for '{0}' - recovered its path from git ({1})" -f $Id, $wtPath) -ForegroundColor Yellow
    $rows[$Id] = [pscustomobject]@{ path = $wtPath.Replace("/", "\"); branch = "work/$Id" }
}

$row = $rows[$Id]
$path = $row.path
$branch = $row.branch
if (-not (Test-Path $path)) {
    Write-Host "Worktree path already gone; dropping the registry row." -ForegroundColor Yellow
    if (-not $WhatIfOnly) { $rows.Remove($Id); Write-Registry $rows; $null = Invoke-GitQuiet @('worktree','prune') }
    exit 0
}

# --- what would be lost? ----------------------------------------------------------
$dirty = Invoke-GitCapture @("-C", $path, "status", "--porcelain")
$unmerged = @()
$refExit = Invoke-GitQuiet @('rev-parse','--verify','--quiet',"refs/heads/$MergedInto")
if ($refExit -eq 0) {
    $unmerged = Invoke-GitCapture @("-C", $path, "log", "--oneline", "$MergedInto..$branch")
} else {
    Write-Host ("  note: no local '{0}' branch to compare against - treating all commits as unmerged" -f $MergedInto) -ForegroundColor Yellow
    $unmerged = Invoke-GitCapture @("-C", $path, "log", "--oneline", "-n", "20", $branch)
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

$removeExit = Invoke-GitQuiet @('worktree','remove','--force',$path)
if ($removeExit -ne 0) {
    # A FAILED DIRECTORY DELETE IS NOT A FAILED REMOVAL, and treating it as one is what
    # produced the mess this script exists to prevent.
    #
    # On Windows `git worktree remove` drops its administrative record FIRST and then
    # deletes the directory. Any open handle - a shell whose working directory is inside
    # the worktree, an editor, a container bind mount - fails that delete with "Permission
    # denied" and git exits non-zero, AFTER git has already stopped tracking the worktree.
    # Bailing out here skipped the branch delete, the registry row and the prune, so git
    # said the worktree was gone, the registry said it was live, the branch survived, and
    # a directory nobody owned sat on disk. That is exactly the "10 worktrees not merged"
    # the operator found - most of them already merged, none of them deregistered.
    #
    # So: ask git whether it STILL TRACKS this worktree. If it does, the removal really
    # failed and we stop. If it does not, finish the job and be honest about the leftover.
    $tracked = @(Invoke-GitCapture @("worktree", "list", "--porcelain") |
                 Where-Object { $_ -like "worktree *" } |
                 ForEach-Object { $_.Substring(9) })
    # git reports forward slashes here and the registry stores backslashes; compare
    # normalised or this always says "still tracked" and never deregisters anything.
    $norm = { param($x) $x.Replace("\", "/").TrimEnd("/").ToLowerInvariant() }
    $wanted = & $norm $path
    if ($tracked | Where-Object { (& $norm $_) -eq $wanted }) {
        Fail "git worktree remove failed for $path (git still tracks it)"
    }
    Write-Host ("  NOTE: git released this worktree but could not delete {0}." -f $path) -ForegroundColor Yellow
    Write-Host  "        Something still holds a handle (usually a shell sitting inside it)."
    Write-Host  "        Deregistering anyway - a stale row is worse than a stale directory."
}
if (-not $KeepBranch) {
    $branchExit = Invoke-GitQuiet @('branch','-D',$branch)
    if ($branchExit -ne 0) { Write-Host ("  WARNING: could not delete branch {0} - remove it by hand" -f $branch) -ForegroundColor Yellow }
}
$rows.Remove($Id)
Write-Registry $rows
$null = Invoke-GitQuiet @('worktree','prune')
# One more attempt at the directory, now that git has let go of it. Best-effort: the point
# of the retry is that the common holder (a shell that has since moved) is often gone by
# now, not that the delete is guaranteed.
if (Test-Path $path) { Remove-Item -Recurse -Force $path -ErrorAction SilentlyContinue }
$leftover = Test-Path $path
Write-Host ("Removed worktree {0}{1}." -f $Id, $(if ($KeepBranch) { " (branch kept)" } else { " and branch $branch" })) -ForegroundColor Green
if ($leftover) {
    # Said out loud rather than swallowed: the registry is now clean, so nothing else will
    # ever mention this directory again.
    Write-Host ("  LEFTOVER DIRECTORY: {0}" -f $path) -ForegroundColor Yellow
    Write-Host  "  It is deregistered and no longer a worktree - delete it by hand once the"
    Write-Host  "  process holding it exits. Nothing tracks it any more, so nothing will remind you."
}
exit 0
