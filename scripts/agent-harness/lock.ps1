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

# Locked read-modify-write of worktrees.json. $Mutate receives the rows hashtable (id -> row),
# edits it in place, and the result is written via temp file + rename while the lock is held.
function Update-WorktreeRegistry {
    param(
        [Parameter(Mandatory)][string]$Registry,
        [Parameter(Mandatory)][scriptblock]$Mutate
    )
    Invoke-WithFileLock -LockPath "$Registry.lock" -Action {
        $rows = @{}
        if (Test-Path $Registry) {
            try {
                $parsed = Get-Content -Raw -Path $Registry | ConvertFrom-Json
                if ($parsed.worktrees) {
                    foreach ($p in $parsed.worktrees.PSObject.Properties) { $rows[$p.Name] = $p.Value }
                }
            } catch {
                Write-Host "  WARNING: registry unreadable, starting a fresh one (old file kept as .bad)" -ForegroundColor Yellow
                Copy-Item $Registry "$Registry.bad" -Force
                $rows = @{}
            }
        }
        & $Mutate $rows
        $tmp = "$Registry.tmp"
        (@{ worktrees = $rows } | ConvertTo-Json -Depth 6) | Set-Content -Path $tmp -Encoding ASCII
        Move-Item -Path $tmp -Destination $Registry -Force
    }
}
