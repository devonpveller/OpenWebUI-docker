# merge-lock.ps1 - serialize merges into the shared work line (development).
#
# Why a lock at all: worktrees stop agents corrupting each other's FILES, but they do
# not stop two agents merging into `development` at the same moment - the exact class
# of collision that already cost this repo a swept gitlink and duplicate org intents
# (2026-08-23). Merges are short; serializing them is cheap and removes the whole
# race.
#
# Why a FILE and not a port (the bridge uses ports for single-instance): both entry
# points - the VS Code extension session and the Mattermost bridge - share this
# filesystem, and a lock needs to say WHO holds it and SINCE WHEN so a stuck lock can
# be taken over with the owner named. A port only says "someone is alive".
#
# Atomicity: CreateNew on NTFS fails if the file exists - no read-then-write window.
#
# Usage:
#   .\merge-lock.ps1 -Acquire -Owner wt-wiki-perf -Thread <mm-root-id>
#   .\merge-lock.ps1 -Refresh -Owner wt-wiki-perf      # while a long rebase runs
#   .\merge-lock.ps1 -Release -Owner wt-wiki-perf
#   .\merge-lock.ps1 -Status
#   .\merge-lock.ps1 -Takeover -Owner wt-other         # only if the lock is expired
#
# Exit codes: 0 ok | 1 usage/error | 3 held by someone else (WAIT, do not force)

[CmdletBinding()]
param(
    [switch]$Acquire,
    [switch]$Release,
    [switch]$Refresh,
    [switch]$Status,
    [switch]$Takeover,
    [string]$Owner = "",
    [string]$Worktree = "",
    [string]$Thread = "",
    [int]$TtlMin = 30
)

$ErrorActionPreference = "Stop"
$StateDir = Join-Path $PSScriptRoot "state"
$Lock = Join-Path $StateDir "merge-lock.json"

function Now() { return [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }

function Read-Lock() {
    if (-not (Test-Path $Lock)) { return $null }
    try { return Get-Content -Raw -Path $Lock | ConvertFrom-Json } catch { return $null }
}

function Show-Lock($L) {
    if (-not $L) { Write-Host "merge lock: FREE" -ForegroundColor Green; return }
    $age = (Now) - [int64]$L.taken_at
    $ttl = [int]$L.ttl_min * 60
    # -ge, not -gt: at exactly the TTL the lock IS expired. With -gt a ttl of 0 ("treat me as
    # already dead") never expired, so a dead agent could hold the queue forever - caught by
    # test_expired_lock_needs_explicit_takeover.
    $state = if ($age -ge $ttl) { "EXPIRED" } else { "HELD" }
    $color = if ($age -ge $ttl) { "Yellow" } else { "Cyan" }
    Write-Host ("merge lock: {0} by {1}" -f $state, $L.owner) -ForegroundColor $color
    Write-Host ("  worktree : {0}" -f $L.worktree)
    Write-Host ("  thread   : {0}" -f $L.thread)
    Write-Host ("  age      : {0}m of {1}m ttl" -f [int]($age / 60), $L.ttl_min)
}

if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Force -Path $StateDir | Out-Null }

if ($Status) { Show-Lock (Read-Lock); exit 0 }

if (-not $Owner) { Write-Host "ERROR: -Owner is required (use your worktree id)" -ForegroundColor Red; exit 1 }

if ($Acquire -or $Takeover) {
    $existing = Read-Lock
    if ($existing) {
        $age = (Now) - [int64]$existing.taken_at
        $expired = $age -ge ([int]$existing.ttl_min * 60)  # -ge: see Show-Lock
        if ($existing.owner -eq $Owner) {
            Write-Host "You already hold the merge lock." -ForegroundColor Green
            exit 0
        }
        if (-not $expired) {
            Show-Lock $existing
            Write-Host "WAIT: another agent is merging. Poll no more than once a minute; do not force." -ForegroundColor Yellow
            exit 3
        }
        if (-not $Takeover) {
            Show-Lock $existing
            Write-Host "The lock is EXPIRED. Re-run with -Takeover to claim it, AND post a note in the" -ForegroundColor Yellow
            Write-Host "previous owner's thread saying you did - a stuck lock usually means a dead agent," -ForegroundColor Yellow
            Write-Host "and its half-finished rebase lives only in ITS worktree (development is untouched)." -ForegroundColor Yellow
            exit 3
        }
        Write-Host ("Taking over an expired lock from {0} (age {1}m)." -f $existing.owner, [int]($age / 60)) -ForegroundColor Yellow
        Remove-Item $Lock -Force
    }

    $payload = [ordered]@{
        owner    = $Owner
        worktree = $Worktree
        thread   = $Thread
        taken_at = Now
        ttl_min  = $TtlMin
        pid      = $PID
    } | ConvertTo-Json -Depth 4

    # CreateNew is the atomic part: if another agent won the race, this throws.
    try {
        $fs = [System.IO.File]::Open($Lock, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try {
            $bytes = [System.Text.Encoding]::ASCII.GetBytes($payload)
            $fs.Write($bytes, 0, $bytes.Length)
        } finally { $fs.Close() }
    } catch [System.IO.IOException] {
        Write-Host "WAIT: another agent claimed the lock a moment ago." -ForegroundColor Yellow
        exit 3
    }
    Write-Host ("Merge lock ACQUIRED by {0} (ttl {1}m)." -f $Owner, $TtlMin) -ForegroundColor Green
    Write-Host "  Release it as soon as the merge lands - other agents are waiting."
    exit 0
}

if ($Refresh) {
    $existing = Read-Lock
    if (-not $existing) { Write-Host "ERROR: no lock to refresh" -ForegroundColor Red; exit 1 }
    if ($existing.owner -ne $Owner) {
        Show-Lock $existing
        Write-Host ("ERROR: the lock belongs to {0}, not {1}" -f $existing.owner, $Owner) -ForegroundColor Red
        exit 3
    }
    $existing.taken_at = Now
    $tmp = "$Lock.tmp"
    ($existing | ConvertTo-Json -Depth 4) | Set-Content -Path $tmp -Encoding ASCII
    Move-Item -Path $tmp -Destination $Lock -Force
    Write-Host ("Merge lock refreshed for {0}." -f $Owner) -ForegroundColor Green
    exit 0
}

if ($Release) {
    $existing = Read-Lock
    if (-not $existing) { Write-Host "merge lock already free." -ForegroundColor Green; exit 0 }
    if ($existing.owner -ne $Owner) {
        Show-Lock $existing
        Write-Host ("ERROR: refusing to release a lock held by {0} (you are {1}). Use -Takeover only when it is EXPIRED." -f $existing.owner, $Owner) -ForegroundColor Red
        exit 3
    }
    Remove-Item $Lock -Force
    Write-Host ("Merge lock released by {0}." -f $Owner) -ForegroundColor Green
    exit 0
}

Write-Host "ERROR: pass one of -Acquire | -Refresh | -Release | -Status | -Takeover" -ForegroundColor Red
exit 1
