# sync-worktree-env.ps1 - re-copy runtime env files into worktrees when the source moved.
#
# Why: a worktree's .env is a COPY (see new-worktree.ps1 - symlinks need privilege on
# Windows and compose resolves --env-file relative to cwd). Copies go stale, and stale
# credentials are a failure class this stack has already paid for twice: ao-workers kept
# an old LC_DEPLOY_TOKEN after a .env update, and a long-lived container held a rotated
# Mattermost token. Cheap insurance: re-copy when the main checkout's file is newer.
#
# Usage:
#   .\sync-worktree-env.ps1 -All              # every registered worktree
#   .\sync-worktree-env.ps1 -Id wiki-perf
#   .\sync-worktree-env.ps1 -Path <worktree>  # unregistered / ad-hoc worktree
#   .\sync-worktree-env.ps1 -All -WhatIfOnly  # report drift, change nothing

[CmdletBinding()]
param(
    [string]$Id = "",
    [string]$Path = "",
    [switch]$All,
    [switch]$WhatIfOnly,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
# The LIST is configuration, exactly as in new-worktree.ps1 - which is where it
# already lived while this script carried a hardcoded copy of it. The two had
# ALREADY drifted before sl-env-split touched either (this list was missing the
# two OB1 recipe files and agent-org/docker/.env, so a sync silently refreshed
# three of six files and reported success), and the six plane files added on
# 2026-09-19 would have made that four of twelve. Read the setting instead.
$EnvFiles = @(Get-HarnessSetting "worktree.env_files" @(".env"))

function Say([string]$Text, [string]$Color = "Gray") {
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}

$MainCheckout = Get-MainCheckout   # git-io owns the PS5.1 native-stderr handling
if (-not $MainCheckout) { Write-Host "ERROR: not inside a git repository" -ForegroundColor Red; exit 1 }
$Registry = Join-Path (Get-SharedStateDir) "worktrees.json"

# Resolve the target set.
$targets = @()
if ($Path) {
    $targets += [pscustomobject]@{ id = "(ad-hoc)"; path = $Path }
} elseif ($All -or $Id) {
    if (-not (Test-Path $Registry)) { Say "No registry at $Registry - nothing to sync." "Yellow"; exit 0 }
    $reg = Get-Content -Raw -Path $Registry | ConvertFrom-Json
    foreach ($p in $reg.worktrees.PSObject.Properties) {
        if ($Id -and $p.Name -ne $Id) { continue }
        $targets += [pscustomobject]@{ id = $p.Name; path = $p.Value.path }
    }
    if ($Id -and -not $targets) { Write-Host "ERROR: no registered worktree with id '$Id'" -ForegroundColor Red; exit 1 }
} else {
    Write-Host "ERROR: pass -All, -Id <id>, or -Path <worktree path>" -ForegroundColor Red
    exit 1
}

$updated = 0
$stale = 0
foreach ($t in $targets) {
    if (-not (Test-Path $t.path)) {
        Say ("SKIP {0}: path is gone ({1}) - run remove-worktree.ps1 -PruneRegistry to clean the registry" -f $t.id, $t.path) "Yellow"
        continue
    }
    foreach ($rel in $EnvFiles) {
        $src = Join-Path $MainCheckout ($rel -replace "/", "\")
        $dst = Join-Path $t.path ($rel -replace "/", "\")
        if (-not (Test-Path $src)) { continue }
        $needs = $true
        if (Test-Path $dst) {
            # Second-resolution compare: copies are byte-identical, so mtime is the signal.
            $needs = ((Get-Item $src).LastWriteTimeUtc -gt (Get-Item $dst).LastWriteTimeUtc)
        }
        if (-not $needs) { continue }
        $stale++
        if ($WhatIfOnly) {
            Say ("DRIFT {0}: {1} is stale" -f $t.id, $rel) "Yellow"
            continue
        }
        $dstDir = Split-Path -Parent $dst
        if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
        Copy-Item -Path $src -Destination $dst -Force
        $updated++
        Say ("SYNC  {0}: {1}" -f $t.id, $rel) "Green"
    }
}

if ($WhatIfOnly) {
    Say ("Checked {0} worktree(s): {1} stale file(s), nothing changed." -f $targets.Count, $stale) "Cyan"
} else {
    Say ("Checked {0} worktree(s): {1} file(s) refreshed." -f $targets.Count, $updated) "Cyan"
}
exit 0
