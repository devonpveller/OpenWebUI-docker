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
$EnvFiles = @(".env", ".env.test", "OB1/docker/.env")

function Say([string]$Text, [string]$Color = "Gray") {
    if (-not $Quiet) { Write-Host $Text -ForegroundColor $Color }
}

# No stderr redirect on the native call: PS5.1 wraps redirected native stderr in
# ErrorRecords, which trips $ErrorActionPreference='Stop' on harmless git chatter.
$prevEap = $ErrorActionPreference
$ErrorActionPreference = "Continue"   # capturing native output under Stop makes git stderr fatal
try { $commonDir = (& git rev-parse --path-format=absolute --git-common-dir) | Select-Object -First 1 }
finally { $ErrorActionPreference = $prevEap }
if (-not $commonDir) { Write-Host "ERROR: not inside a git repository" -ForegroundColor Red; exit 1 }
$MainCheckout = Split-Path -Parent $commonDir
$Registry = Join-Path $PSScriptRoot "state\worktrees.json"

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
