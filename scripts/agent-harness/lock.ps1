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

# Swap the finished temp file over the registry. [System.IO.File]::Replace never deletes the
# destination first (Move-Item -Force did: delete, then move), so there is no instant with no
# registry, and a holder killed mid-swap cannot leave none. A reader that has the file open
# without FILE_SHARE_DELETE (Get-Content does) still makes the swap fail for a moment, so the
# swap is retried for a bounded time instead of failing the whole update. Same volume by
# construction: the temp file sits beside the registry. No destination yet = plain Move.
function Move-RegistryIntoPlace {
    param([Parameter(Mandatory)][string]$Tmp, [Parameter(Mandatory)][string]$Dest, [int]$TimeoutSec = 10)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSec)
    while ($true) {
        try {
            if (Test-Path $Dest) { [System.IO.File]::Replace($Tmp, $Dest, [NullString]::Value) }
            else { [System.IO.File]::Move($Tmp, $Dest) }
            return
        } catch [System.IO.IOException], [System.UnauthorizedAccessException] {
            if ([DateTime]::UtcNow -ge $deadline) {
                throw ("could not swap the new registry into place ('{0}') within {1}s - a reader kept it open ({2}). The row was NOT written." -f $Dest, $TimeoutSec, $_.Exception.Message)
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

# Locked read-modify-write of worktrees.json. $Mutate receives the rows hashtable (id -> row),
# edits it in place, and the result is swapped in while the lock is held. The helper's own
# locals carry a wtl prefix because $Mutate runs in dynamic scope: a plain $rows or $tmp in a
# caller's block would otherwise bind to the helper's variable, not the caller's.
function Update-WorktreeRegistry {
    param(
        [Parameter(Mandatory)][string]$Registry,
        [Parameter(Mandatory)][scriptblock]$Mutate
    )
    Invoke-WithFileLock -LockPath "$Registry.lock" -Action {
        $wtlRows = @{}
        if (Test-Path $Registry) {
            $wtlParsed = $null
            try {
                $wtlParsed = Read-RegistryJson -Path $Registry
                if ($null -eq $wtlParsed) { throw "unreadable" }
                if ($wtlParsed.worktrees) {
                    foreach ($wtlP in $wtlParsed.worktrees.PSObject.Properties) { $wtlRows[$wtlP.Name] = $wtlP.Value }
                }
            } catch {
                Write-Host "  WARNING: registry unreadable, starting a fresh one (old file kept as .bad)" -ForegroundColor Yellow
                Copy-Item $Registry "$Registry.bad" -Force
                $wtlRows = @{}
            }
        }
        & $Mutate $wtlRows
        $wtlTmp = "$Registry.tmp"
        (@{ worktrees = $wtlRows } | ConvertTo-Json -Depth 6) | Set-Content -Path $wtlTmp -Encoding ASCII
        Move-RegistryIntoPlace -Tmp $wtlTmp -Dest $Registry
    }
}
