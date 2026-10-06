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

# A registry is an object whose `worktrees` is an object of row objects. Anything else (a string,
# an array, a number, null, rows that are not objects) is NOT a registry: writing into it would
# produce junk rows, so callers refuse or set it aside instead.
function Test-RegistryShape {
    param($Parsed)
    if (-not $Parsed -or -not ($Parsed.PSObject.Properties.Name -contains "worktrees")) { return $false }
    $wtlW = $Parsed.worktrees
    if (-not ($wtlW -is [System.Management.Automation.PSCustomObject])) { return $false }
    foreach ($wtlP in $wtlW.PSObject.Properties) {
        if (-not ($wtlP.Value -is [System.Management.Automation.PSCustomObject])) { return $false }
    }
    return $true
}

# A name for a set-aside copy: <Path>.bad-<UTC stamp, ms>, and when that name already exists
# (two incidents in the same millisecond, a repeated clock) <...>-2, -3, ... The name is
# checked to be free here, and the caller still creates it without overwriting (File.Copy with
# overwrite=false, or a Rename-Item, which refuses an existing target), so an earlier .bad-* is
# never overwritten whatever the clock does.
function Get-AsideName {
    param([Parameter(Mandatory)][string]$Path)
    # $global:WtlAsideStamp is a TEST seam (verify-registry-lock.ps1 pins the clock with it); unset in use.
    $wtlStamp = [DateTime]::UtcNow.ToString("yyyyMMddHHmmssfff")
    if ($global:WtlAsideStamp) { $wtlStamp = [string]$global:WtlAsideStamp }
    $wtlBase = $Path + ".bad-" + $wtlStamp
    $wtlName = $wtlBase
    $wtlN = 1
    while (Test-Path -LiteralPath $wtlName) { $wtlN++; $wtlName = $wtlBase + "-" + $wtlN }
    return $wtlName
}

# When worktrees.json is absent under the lock, the rows may be stranded by a writer that was
# killed between the delete and the move: finished content in worktrees.json.tmp (or, from an
# older toolkit, worktrees.json~RF*.TMP). Picks the newest leftover that parses AND has the
# registry shape. Returns @{ from; count; skipped } (skipped = newer leftovers that were
# unusable, renamed to <name>.bad-<stamp> so a rollback is visible and the bytes are kept),
# $null when there is no leftover at all (a true first write), and throws, naming the files,
# when leftovers exist but none is usable. It does NOT write or truncate anything it reads from:
# the caller moves the chosen file into place first.
function Get-RegistryRecovery {
    param([Parameter(Mandatory)][string]$Registry)
    $wtlDir = Split-Path -Parent $Registry
    $wtlLeaf = Split-Path -Leaf $Registry
    $wtlCands = @()
    if (Test-Path "$Registry.tmp") { $wtlCands += Get-Item "$Registry.tmp" }
    if (Test-Path $wtlDir) { $wtlCands += @(Get-ChildItem -Path $wtlDir -Filter ($wtlLeaf + "~RF*.TMP") -ErrorAction SilentlyContinue) }
    if (-not $wtlCands.Count) { return $null }
    $wtlBadNew = @()
    foreach ($wtlC in @($wtlCands | Sort-Object LastWriteTimeUtc -Descending)) {
        try {
            $wtlO = [System.IO.File]::ReadAllText($wtlC.FullName) | ConvertFrom-Json
            if (Test-RegistryShape $wtlO) {
                $wtlSkipped = @()
                foreach ($wtlB in $wtlBadNew) {
                    # a unique target: a rename onto an existing .bad-<stamp> would fail and leave the
                    # skipped file in place, to be overwritten by this writer's own .tmp
                    $wtlAside = Get-AsideName -Path $wtlB
                    try { Rename-Item -LiteralPath $wtlB -NewName (Split-Path -Leaf $wtlAside) -ErrorAction Stop; $wtlSkipped += $wtlAside }
                    catch { $wtlSkipped += $wtlB }
                }
                return @{ from = $wtlC.FullName; count = @($wtlO.worktrees.PSObject.Properties).Count; skipped = $wtlSkipped }
            }
        } catch { }
        $wtlBadNew += $wtlC.FullName
    }
    throw ("'{0}' is absent and none of its leftovers can be read as a registry ({1}). Not starting a fresh registry over them: inspect those files, put a good copy at '{0}' (or delete them if the registry really is empty), then retry." -f $Registry, ($wtlBadNew -join ", "))
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
        if (-not (Test-Path $Registry)) {
            # Absent: recover. The chosen leftover is MOVED into place first (a rename onto an
            # absent name), so the rows are durable under their real name before anything is
            # rewritten; the normal update below then runs on a present registry. Rewriting the
            # leftover in place instead would truncate the only copy.
            $wtlRec = Get-RegistryRecovery -Registry $Registry
            if ($wtlRec) {
                Move-RegistryIntoPlace -Tmp $wtlRec.from -Dest $Registry
                $wtlMsg = "  WARN: worktrees.json was absent (a writer was killed mid-swap); recovered {0} row(s) from {1}" -f $wtlRec.count, $wtlRec.from
                if ($wtlRec.skipped.Count) { $wtlMsg += "; NEWER unusable leftover(s) skipped (kept): " + ($wtlRec.skipped -join ", ") }
                Write-Host $wtlMsg -ForegroundColor Yellow
            }
        }
        $wtlRows = @{}
        if (Test-Path $Registry) {
            $wtlParsed = Read-RegistryJson -Path $Registry
            if (Test-RegistryShape $wtlParsed) {
                foreach ($wtlP in $wtlParsed.worktrees.PSObject.Properties) { $wtlRows[$wtlP.Name] = $wtlP.Value }
            } else {
                # Present but unreadable (corrupt, wrong shape, or held exclusively for > 3 s).
                # Set it aside under a unique timestamped name FIRST (an earlier .bad is never
                # overwritten: Get-AsideName picks a free name and File.Copy refuses to
                # overwrite); only if that works is a fresh registry started. If it cannot be
                # set aside nothing is overwritten and the writer stops.
                $wtlBad = Get-AsideName -Path $Registry
                try { [System.IO.File]::Copy($Registry, $wtlBad, $false) }
                catch { throw ("registry '{0}' is unreadable and could not be set aside ({1}); left untouched, the row was NOT written." -f $Registry, $_.Exception.Message) }
                Write-Host ("  WARNING: registry unreadable, started a fresh one (old file kept as {0})" -f $wtlBad) -ForegroundColor Yellow
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
