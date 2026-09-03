# check-bridge-freshness.ps1 - the claude-sessions bridge is running CURRENT code, and is
# actually completing turns.
#
# WHY THIS EXISTS. On 2026-09-03 the bridge had been running 2026-08-28 code for six days,
# straight through the commits that added the durable inbox (cac1f85) and the gate reporting.
# Every operator-visible symptom - no hourglass, no stats footer, sequential messages unclear,
# approval relay mismatched - was that one fact. It was invisible because:
#
#   1. NOTHING RESTARTS THE BRIDGE WHEN ITS CODE CHANGES. `git pull` / a merge updates
#      bridge.py on disk; the running process keeps its six-day-old copy in memory forever.
#   2. health.json REPORTED GREEN THE WHOLE TIME. `consecutive_failures: 0` and a fresh `ts`,
#      while `last_turn_ok_ts` sat five days in the past. A heartbeat is not a liveness proof -
#      "the process is up" and "the process is working" are different claims, and only one of
#      them was being made.
#
# The singleton guard was NOT the problem and is not touched here: acquire_single_instance_lock
# binds 127.0.0.1:48291 and a second bridge exits cleanly (verified 2026-09-03 - three lock
# ports held by three distinct services). The extra pythonw entries seen that day were
# parent+child pairs of ONE bridge, not competing bridges. Fixing a working guard would have
# been the wrong repair.
#
# WHAT IT DOES, and the split is deliberate (operator, 2026-09-03):
#   STALE CODE  -> SELF-HEAL. The delivery is "the bridge runs current code"; a restart IS the
#                  fix, not a notification about the fix. Restarting is safe: the durable inbox
#                  replays any message whose turn did not finish.
#   STALE TURNS -> ALERT ONLY. A restart cannot fix "nobody has messaged it", so restarting on
#                  quiet would be a reboot loop chasing an absence. Say it; do not act.
#
# Exit codes:  0 fresh (or healed)   3 stale turns (alert)   4 could not decide   5 heal failed

[CmdletBinding()]
param(
    # Restart the Scheduled Task when the running bridge predates bridge.py. Default ON: the
    # whole point is that a stale bridge repairs itself rather than waiting to be noticed.
    [switch]$NoHeal,
    # Hours without a COMPLETED turn before saying so. Not a failure of the bridge per se -
    # a quiet channel looks identical - which is why it alerts and never restarts.
    [int]$StaleTurnHours = 48,
    # Report what it would do, change nothing.
    [switch]$WhatIfOnly
)

$ErrorActionPreference = 'Stop'
$TaskName = 'claude-sessions-bridge'
$exit = 0

# ── WHICH TREE IS THE DEPLOYMENT? ────────────────────────────────────────────
# NOT this script's own tree. Measured while building this check (2026-09-03): run from a
# worktree it reported STALE CODE against a perfectly current bridge, because git checkout
# stamps bridge.py with the checkout time - so every worktree copy looks newer than every
# running process. It also found no state/health.json, because runtime state exists only in
# the deployment, and silently degraded to "cannot judge" rather than saying why.
#
# Both are one error: a check that resolves what it audits from ITSELF audits the wrong thing.
# The deployment is the tree the RUNNING PROCESS was launched from - ask the process (or the
# Scheduled Task), never $PSScriptRoot.
function Resolve-DeployedBridgePath {
    param($OwnerProcess)
    # 1. The running process's own command line is the authority.
    if ($OwnerProcess) {
        try {
            $cl = (Get-CimInstance Win32_Process -Filter "ProcessId=$($OwnerProcess.Id)" -ErrorAction Stop).CommandLine
            if ($cl -match '"?([A-Za-z]:\\[^"]*?bridge\.py)"?') { return $Matches[1] }
        } catch { }
    }
    # 2. Not running: the Scheduled Task's action says what WOULD run.
    try {
        $act = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).Actions | Select-Object -First 1
        if ($act.Arguments -match '"?([A-Za-z]:\\[^"]*?bridge\.py)"?') { return $Matches[1] }
    } catch { }
    return $null
}

function Say($s) { Write-Host $s }

# ── 1. WHICH PROCESS IS THE BRIDGE? ──────────────────────────────────────────
# The lock HOLDER is the bridge, not "any process whose command line matches". A pythonw that
# lost the race for 48291 has already exited; counting it as a bridge is how 2026-09-03's first
# diagnosis concluded "four competing bridges" when there was one. Ask the authority (the port),
# not the directory.
$lockPort = if ($env:BRIDGE_LOCK_PORT) { $env:BRIDGE_LOCK_PORT } else { '48291' }
$owner = $null
try {
    $conn = Get-NetTCPConnection -LocalPort $lockPort -State Listen -ErrorAction Stop |
            Select-Object -First 1
    if ($conn) { $owner = Get-Process -Id $conn.OwningProcess -ErrorAction SilentlyContinue }
} catch { $owner = $null }

if (-not $owner) {
    Say "BRIDGE NOT RUNNING - nothing holds the lock port $lockPort."
    if ($NoHeal -or $WhatIfOnly) { Say '  (would start the Scheduled Task)'; exit 3 }
    try { Start-ScheduledTask -TaskName $TaskName; Say "  STARTED $TaskName."; exit 0 }
    catch { Say "  COULD NOT START: $_"; exit 5 }
}

$BridgePy = Resolve-DeployedBridgePath -OwnerProcess $owner
if (-not $BridgePy -or -not (Test-Path $BridgePy)) {
    Say "CANNOT DECIDE - could not resolve the DEPLOYED bridge.py from the running process or the task."
    Say '  (Deliberately refusing to fall back to this script''s own tree: a worktree copy has a'
    Say '   fresh checkout mtime and would report STALE CODE against a current bridge.)'
    exit 4
}
$HealthJson = Join-Path (Split-Path -Parent $BridgePy) 'state\health.json'
Say "deployment: $BridgePy"

# ── 2. STALE CODE? ───────────────────────────────────────────────────────────
# The comparison is bridge.py's mtime vs the process START time. Not a hash, not a git sha:
# what matters is whether this PROCESS could have loaded the file as it now stands. A file
# written after the process started is code the process has never seen, whatever it contains.
$codeMtime = (Get-Item $BridgePy).LastWriteTime
$started   = $owner.StartTime
$staleCode = $codeMtime -gt $started

Say ("bridge pid {0} started {1:yyyy-MM-dd HH:mm}; bridge.py modified {2:yyyy-MM-dd HH:mm}" -f `
     $owner.Id, $started, $codeMtime)

if ($staleCode) {
    $age = [int]([DateTime]::Now - $codeMtime).TotalHours
    Say "STALE CODE - the running bridge predates bridge.py by $([int]($codeMtime - $started).TotalHours)h (file is ${age}h old)."
    Say '  Every symptom of a stale bridge looks like a bridge bug. It is not one.'
    if ($NoHeal -or $WhatIfOnly) {
        Say '  SELF-HEAL SUPPRESSED - would restart the Scheduled Task.'
        $exit = 3
    } else {
        try {
            Say '  Restarting (the durable inbox replays any unfinished turn) ...'
            Stop-ScheduledTask  -TaskName $TaskName -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 2
            # Only the lock holder, and only if it survived Stop-ScheduledTask. Never a
            # command-line sweep: that would kill a SECOND service whose bridge.py path matches
            # (the sysadmin bridge on 48292 is exactly that shape).
            if (Get-Process -Id $owner.Id -ErrorAction SilentlyContinue) {
                Stop-Process -Id $owner.Id -Force -Confirm:$false -ErrorAction SilentlyContinue
            }
            Start-Sleep -Seconds 2
            Start-ScheduledTask -TaskName $TaskName
            Start-Sleep -Seconds 6

            $newConn = Get-NetTCPConnection -LocalPort $lockPort -State Listen -ErrorAction SilentlyContinue |
                       Select-Object -First 1
            $new = if ($newConn) { Get-Process -Id $newConn.OwningProcess -ErrorAction SilentlyContinue } else { $null }
            if ($new -and $new.StartTime -gt $codeMtime) {
                Say ("  HEALED - pid {0} started {1:HH:mm}, now newer than the code." -f $new.Id, $new.StartTime)
            } else {
                # Verify the REPAIR, not just that the command ran. A restart that silently
                # failed to take the lock leaves no bridge at all, which is worse than stale.
                Say '  HEAL FAILED - no process holds the lock port after restart.'
                exit 5
            }
        } catch { Say "  HEAL FAILED: $_"; exit 5 }
    }
} else {
    Say 'code: CURRENT (the running process is at or newer than bridge.py).'
}

# ── 3. STALE TURNS? ALERT ONLY ───────────────────────────────────────────────
# health.json's own `ts` is a heartbeat and proves only that the loop is spinning.
# last_turn_ok_ts is the liveness claim that matters, and on 2026-09-03 they disagreed by five
# days while the file read green.
if (Test-Path $HealthJson) {
    try {
        $h = Get-Content -Raw $HealthJson | ConvertFrom-Json
        if ($h.last_turn_ok_ts -and $h.last_turn_ok_ts -gt 0) {
            $last = [DateTimeOffset]::FromUnixTimeSeconds([int64]$h.last_turn_ok_ts).LocalDateTime
            $hrs  = [int]([DateTime]::Now - $last).TotalHours
            if ($hrs -ge $StaleTurnHours) {
                Say ("STALE TURNS - last COMPLETED turn {0:yyyy-MM-dd HH:mm} ({1}h ago), threshold ${StaleTurnHours}h." -f $last, $hrs)
                Say '  NOT restarting: a quiet channel and a broken bridge look identical here,'
                Say '  and a restart cannot manufacture a message. Send one to tell them apart.'
                if ($exit -eq 0) { $exit = 3 }
            } else {
                Say ("turns: last completed {0:yyyy-MM-dd HH:mm} ({1}h ago)." -f $last, $hrs)
            }
        } else { Say 'turns: health.json states no completed turn yet - nothing to judge.' }
        if ($h.bin_exists -eq $false) {
            Say "CLAUDE BINARY MISSING: $($h.claude_bin) - the bridge cannot spawn a session."
            $exit = 3
        }
    } catch { Say "turns: could not read health.json ($_)"; if ($exit -eq 0) { $exit = 4 } }
} else {
    Say 'turns: no health.json - cannot judge liveness.'
    if ($exit -eq 0) { $exit = 4 }
}

exit $exit
