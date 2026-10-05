# lock.ps1 - one exclusive lock for read-modify-write of shared harness state (the worktree
# registry today). Dot-sourced by common.ps1 so every writer uses the same helper.
#
# The lock is a file held open with FileShare.None. The OS drops the handle when the holder
# process dies, so a killed holder can never wedge later callers: there is no stale lock to
# clean up. A second caller retries until -TimeoutSec, then throws a message naming the lock.

function Invoke-WithFileLock {
    param(
        [Parameter(Mandatory)][string]$LockPath,
        [Parameter(Mandatory)][scriptblock]$Action,
        [int]$TimeoutSec = 0
    )
    if ($TimeoutSec -le 0) {
        $TimeoutSec = 30
        $envT = [Environment]::GetEnvironmentVariable("AI_STACK_REGISTRY_LOCK_TIMEOUT_SEC")
        $n = 0
        if ($envT -and [int]::TryParse($envT, [ref]$n) -and $n -gt 0) { $TimeoutSec = $n }
    }
    $dir = Split-Path -Parent $LockPath
    if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    $fs = $null
    $lastErr = ""
    while ($true) {
        try {
            $fs = New-Object System.IO.FileStream($LockPath, [System.IO.FileMode]::OpenOrCreate,
                [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
            break
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            $lastErr = $_.Exception.Message
            if ([DateTime]::UtcNow -ge $deadline) {
                $msg = "could not acquire lock '{0}' within {1}s - another harness process holds it ({2}). " +
                       "The lock frees itself when that process exits; if none is running, delete the file."
                throw ($msg -f $LockPath, $TimeoutSec, $lastErr)
            }
            Start-Sleep -Milliseconds (40 + (Get-Random -Maximum 60))
        }
    }
    try {
        & $Action
    } finally {
        $fs.Dispose()
    }
}

# Swap the finished temp file over the registry with Move-Item -Force (delete the destination,
# then move), retried for a bounded time inside the lock. A reader that has the file open
# without FILE_SHARE_DELETE (Get-Content does) fails the swap for a moment, so the retry is
# what keeps a writer from losing its update. Why not [System.IO.File]::Replace: measured on
# this host, it saved nothing over Move-Item + retry (both 0 writer errors against a tight
# reader) and made raw readers (old toolkits in older worktrees, verify-merge-protocol.ps1)
# fail ~19x more often (1652 vs 87 anomalies per 300 updates); ReplaceFile also parks the
# old file as worktrees.json~RF*.TMP mid-swap.
# Neither swap is crash-atomic: a holder killed between the delete and the move leaves NO
# worktrees.json, with the finished content in worktrees.json.tmp. That is covered by
# Update-WorktreeRegistry, which recovers from .tmp / ~RF*.TMP instead of starting fresh.
# Same volume by construction: the temp file sits beside the registry.
function Move-RegistryIntoPlace {
    param([Parameter(Mandatory)][string]$Tmp, [Parameter(Mandatory)][string]$Dest, [int]$TimeoutSec = -1)
    if ($TimeoutSec -lt 0) {
        $TimeoutSec = 10
        $envS = [Environment]::GetEnvironmentVariable("AI_STACK_REGISTRY_SWAP_TIMEOUT_SEC")
        $nS = 0
        if ($envS -and [int]::TryParse($envS, [ref]$nS) -and $nS -ge 0) { $TimeoutSec = $nS }
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    while ($true) {
        try {
            Move-Item -Path $Tmp -Destination $Dest -Force -ErrorAction Stop
            return
        } catch {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw ("could not swap the new registry into place ('{0}') within {1}s - a reader kept it open ({2}). The row was NOT written; the registry file may be absent, and the next writer recovers it from '{3}'." -f $Dest, $TimeoutSec, $_.Exception.Message, $Tmp)
            }
            Start-Sleep -Milliseconds (20 + (Get-Random -Maximum 40))
        }
    }
}

# Reader side: parsed registry object, or $null when it is absent or stays unreadable. Opens
# with FileShare ReadWrite+Delete so a reader never blocks the writer's swap, and retries a
# short bounded time on a locked, momentarily-missing or partial file instead of throwing.
function Read-RegistryJson {
    param([Parameter(Mandatory)][string]$Path, [int]$TimeoutSec = 3)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    $misses = 0
    while ($true) {
        try {
            if (-not (Test-Path $Path)) {
                $misses++
                if ($misses -ge 3) { return $null }
            } else {
                $misses = 0
                $rfs = New-Object System.IO.FileStream($Path, [System.IO.FileMode]::Open,
                    [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
                try { $rtext = (New-Object System.IO.StreamReader($rfs)).ReadToEnd() } finally { $rfs.Dispose() }
                if ($rtext -and $rtext.Trim()) { return ($rtext | ConvertFrom-Json) }
            }
        } catch { }
        if ([DateTime]::UtcNow -ge $deadline) { return $null }
        Start-Sleep -Milliseconds (20 + (Get-Random -Maximum 40))
    }
}

# When worktrees.json is absent under the lock, the rows may be stranded by a writer that was
# killed between the delete and the move: finished content in worktrees.json.tmp (or, from an
# older toolkit, worktrees.json~RF*.TMP). Returns @{ rows; from } from the newest candidate that
# parses AND has the registry shape, $null when there is no candidate at all (a true first
# write), and throws, naming the files, when candidates exist but none is usable.
function Get-RegistryRecovery {
    param([Parameter(Mandatory)][string]$Registry)
    $wtlDir = Split-Path -Parent $Registry
    $wtlLeaf = Split-Path -Leaf $Registry
    $wtlCands = @()
    if (Test-Path "$Registry.tmp") { $wtlCands += Get-Item "$Registry.tmp" }
    if (Test-Path $wtlDir) { $wtlCands += @(Get-ChildItem -Path $wtlDir -Filter ($wtlLeaf + "~RF*.TMP") -ErrorAction SilentlyContinue) }
    if (-not $wtlCands.Count) { return $null }
    foreach ($wtlC in @($wtlCands | Sort-Object LastWriteTimeUtc -Descending)) {
        try {
            $wtlO = [System.IO.File]::ReadAllText($wtlC.FullName) | ConvertFrom-Json
            if ($wtlO -and ($wtlO.PSObject.Properties.Name -contains "worktrees")) {
                $wtlR = @{}
                if ($wtlO.worktrees) { foreach ($wtlP in $wtlO.worktrees.PSObject.Properties) { $wtlR[$wtlP.Name] = $wtlP.Value } }
                return @{ rows = $wtlR; from = $wtlC.FullName }
            }
        } catch { }
    }
    throw ("'{0}' is absent and none of its leftovers can be read as a registry ({1}). Not starting a fresh registry over them: inspect those files, put a good copy at '{0}' (or delete them if the registry really is empty), then retry." -f $Registry, (($wtlCands | ForEach-Object { $_.FullName }) -join ", "))
}

# Locked read-modify-write of worktrees.json. $Mutate receives the rows hashtable (id -> row),
# edits it in place, and the result is swapped in while the lock is held.
# CONTRACT: $Mutate must be a CLOSURE - pass `{ ... }.GetNewClosure()`. A plain scriptblock runs
# in dynamic scope, so its names ($n, $dir, $fs, $deadline, $rows ...) would silently bind to
# THIS function's and the lock helper's locals instead of the caller's; a closure is bound to
# its own scope and sees only what the caller captured. Enforced below.
function Update-WorktreeRegistry {
    param(
        [Parameter(Mandatory)][string]$Registry,
        [Parameter(Mandatory)][scriptblock]$Mutate
    )
    if (-not $Mutate.Module) { throw "Update-WorktreeRegistry: -Mutate must be a closure ({ ... }.GetNewClosure()) so it cannot bind to this helper's variables." }
    Invoke-WithFileLock -LockPath "$Registry.lock" -Action {
        $wtlRows = @{}
        if (Test-Path $Registry) {
            $wtlParsed = Read-RegistryJson -Path $Registry
            if ($null -ne $wtlParsed) {
                if ($wtlParsed.worktrees) {
                    foreach ($wtlP in $wtlParsed.worktrees.PSObject.Properties) { $wtlRows[$wtlP.Name] = $wtlP.Value }
                }
            } else {
                # Present but unreadable (corrupt, or held exclusively for > 3 s). Set it aside
                # FIRST; only if that works is a fresh registry started. If it cannot be set
                # aside nothing is overwritten and the writer stops.
                try { Copy-Item $Registry "$Registry.bad" -Force -ErrorAction Stop }
                catch { throw ("registry '{0}' is unreadable and could not be set aside ({1}); left untouched, the row was NOT written." -f $Registry, $_.Exception.Message) }
                Write-Host "  WARNING: registry unreadable, started a fresh one (old file kept as worktrees.json.bad)" -ForegroundColor Yellow
            }
        } else {
            $wtlRec = Get-RegistryRecovery -Registry $Registry
            if ($wtlRec) {
                $wtlRows = $wtlRec.rows
                Write-Host ("  WARN: worktrees.json was absent (a writer was killed mid-swap); recovered {0} row(s) from {1}" -f $wtlRows.Count, $wtlRec.from) -ForegroundColor Yellow
            }
        }
        & $Mutate $wtlRows
        $wtlTmp = "$Registry.tmp"
        (@{ worktrees = $wtlRows } | ConvertTo-Json -Depth 6) | Set-Content -Path $wtlTmp -Encoding ASCII
        Move-RegistryIntoPlace -Tmp $wtlTmp -Dest $Registry
        Get-ChildItem -Path (Split-Path -Parent $Registry) -Filter ((Split-Path -Leaf $Registry) + "~RF*.TMP") -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
    }
}
