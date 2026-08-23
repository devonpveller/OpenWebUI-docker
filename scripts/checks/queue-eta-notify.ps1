# Queue ETA notifier for the llm-queue admission queue (issue #25).
#
# Mirrors scripts/checks/stack-watchdog.ps1: -Mode check|daemon|install-service.
#
# Each pass polls the queue snapshot:
#   docker exec llm-queue curl -fsS http://localhost:8080/observe/queue
# (or -SnapshotFile <json> to feed a fixture instead of docker exec), flags
# waiting rows where waited_s + est_wait_s >= -ThresholdSeconds (default 120),
# and posts ONE Mattermost message per (caller key, model) with the flag
# timestamp, est start and est completion (est_wait_s + avg_T_s).
#
# Dedup: state JSON under logs/ with a 10-minute per-lane cooldown, so a
# persistently queued caller is not re-pinged on every poll.
#
# Mattermost posting mirrors scripts/notify-mattermost.sh: the bot token is
# read from agent-org/docker/.env (AO_MATTERMOST_BOT_TOKEN) at RUN TIME --
# never hardcoded or committed; the channel id is a parameter; fail-soft --
# a down Mattermost (or a missing token) is logged and never fails the check.

[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [ValidateSet("check", "daemon", "install-service")]
    [string]$Mode = "check",

    [Parameter(Mandatory=$false)]
    [ValidateRange(10, 3600)]
    [int]$IntervalSeconds = 60,

    [Parameter(Mandatory=$false)]
    [ValidateRange(1, 86400)]
    [double]$ThresholdSeconds = 120,

    [Parameter(Mandatory=$false)]
    [string]$ChannelId = 'qqq97fwxd3f8ufenjybrf5w1yr',  # #claude-code (notify-mattermost.sh default)

    [Parameter(Mandatory=$false)]
    [switch]$DryRun,

    [Parameter(Mandatory=$false)]
    [string]$SnapshotFile
)

# Set strict error handling
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# Constants
$SCRIPT_DIR = Split-Path -Parent $PSCommandPath
$PROJECT_DIR = Split-Path -Parent (Split-Path -Parent $SCRIPT_DIR)
$LOG_FILE = Join-Path $PROJECT_DIR "logs\queue-eta-notify.log"
$STATE_FILE = Join-Path $PROJECT_DIR "logs\queue-eta-notify-state.json"
$ENV_FILE = Join-Path $PROJECT_DIR "agent-org\docker\.env"
$MM_API = "http://localhost:8065/api/v4/posts"
$QUEUE_URL = "http://localhost:8080/observe/queue"
$SERVICE_NAME = "QueueEtaNotifier"
$COOLDOWN_MINUTES = 10

# Create logs directory if it doesn't exist
$LogDir = Split-Path -Parent $LOG_FILE
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# Function to write structured log entries
function Write-LogEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)]
        [string]$Message,

        [Parameter()]
        [ValidateSet("INFO", "WARN", "ERROR", "SUCCESS", "DEBUG")]
        [string]$Level = "INFO"
    )

    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $LogEntry = "$Timestamp [$Level] $Message"

    try {
        $LogEntry | Out-File -FilePath $LOG_FILE -Append -Encoding UTF8
        Write-Information $LogEntry -InformationAction Continue
    }
    catch {
        Write-Warning "Failed to write to log file: $_"
        Write-Host $LogEntry
    }
}

# Fetch the queue snapshot. Returns a parsed object, or $null on failure.
# With -SnapshotFile the fixture is read from disk instead of docker exec
# (sandbox/host-harness testing without Docker).
function Get-QueueSnapshot {
    [CmdletBinding()]
    param()
    try {
        if ($SnapshotFile) {
            if (-not (Test-Path $SnapshotFile)) {
                Write-LogEntry "SnapshotFile not found: $SnapshotFile" "ERROR"
                return $null
            }
            $raw = Get-Content $SnapshotFile -Raw
            return ($raw | ConvertFrom-Json)
        }
        # Native docker stderr must not throw under -Stop (PS 5.1 gotcha, see
        # stack-watchdog.ps1): a stopped queue container writes to stderr and
        # would otherwise abort the whole pass.
        $prevEAP = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $raw = docker exec llm-queue curl -fsS $QUEUE_URL 2>$null
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevEAP
        if ($code -ne 0 -or -not $raw) {
            Write-LogEntry "queue snapshot probe failed (exit $code) -- llm-queue container down or /observe/queue unreachable" "WARN"
            return $null
        }
        return (($raw -join "`n") | ConvertFrom-Json)
    }
    catch {
        Write-LogEntry "queue snapshot probe error: $($_.Exception.Message)" "WARN"
        return $null
    }
}

# Group flagged waiting rows into ONE lane per (caller key, model).
# A row is flagged when waited_s + est_wait_s >= $ThresholdSeconds.
# The lane's est start / est completion come from its worst row:
#   est start      = now + est_wait_s
#   est completion = now + est_wait_s + avg_T_s (per-model mean service time)
function Get-FlaggedLanes {
    [CmdletBinding()]
    param($Snapshot)
    $model = if ($Snapshot.model) { [string]$Snapshot.model } else { 'unknown' }
    $avgT = 0.0
    if ($null -ne $Snapshot.avg_T_s) { $avgT = [double]$Snapshot.avg_T_s }
    $now = Get-Date

    $waiting = @()
    if ($null -ne $Snapshot.waiting) { $waiting = @($Snapshot.waiting) }

    $flagged = @()
    foreach ($row in $waiting) {
        $waited = [double]$row.waited_s
        $estWait = [double]$row.est_wait_s
        if (($waited + $estWait) -lt $ThresholdSeconds) { continue }
        $key = if ($row.key) { [string]$row.key } else { 'unknown' }
        $flagged += [pscustomobject]@{
            Key         = $key
            Model       = $model
            Row         = $row
            Total       = $waited + $estWait
            EstStart    = $now.AddSeconds($estWait)
            EstCompletion = $now.AddSeconds($estWait + $avgT)
        }
    }
    if ($flagged.Count -eq 0) { return ,@() }

    $lanes = @()
    foreach ($group in ($flagged | Group-Object { "$($_.Key)|$($_.Model)" })) {
        $worst = $group.Group | Sort-Object Total -Descending | Select-Object -First 1
        $lanes += [pscustomobject]@{
            Key           = $worst.Key
            Model         = $worst.Model
            LaneId        = $group.Name
            Rows          = @($group.Group)
            WorstTotal    = $worst.Total
            EstStart      = $worst.EstStart
            EstCompletion = $worst.EstCompletion
        }
    }
    return ,@($lanes)
}

# Load dedup state: hashtable laneId -> last-notified DateTime.
function Get-DedupState {
    [CmdletBinding()]
    param()
    $state = @{}
    try {
        if (Test-Path $STATE_FILE) {
            $raw = Get-Content $STATE_FILE -Raw | ConvertFrom-Json
            if ($raw -and $raw.lanes) {
                foreach ($prop in $raw.lanes.PSObject.Properties) {
                    try { $state[$prop.Name] = [DateTime]::Parse($prop.Value.last_notified) } catch { }
                }
            }
        }
    }
    catch {
        Write-LogEntry "dedup state unreadable ($($_.Exception.Message)) -- starting fresh" "WARN"
    }
    return $state
}

# Persist dedup state as JSON under logs/ (laneId -> last_notified, ISO-8601).
function Save-DedupState {
    [CmdletBinding()]
    param($Lanes)
    try {
        $lanesObj = [ordered]@{}
        foreach ($k in ($Lanes.Keys | Sort-Object)) {
            $lanesObj[$k] = [pscustomobject]@{ last_notified = $Lanes[$k] }
        }
        $state = [pscustomobject]@{ version = 1; lanes = $lanesObj }
        $state | ConvertTo-Json -Depth 4 | Out-File -FilePath $STATE_FILE -Encoding UTF8
    }
    catch {
        Write-LogEntry "failed to save dedup state: $($_.Exception.Message)" "WARN"
    }
}

# True when this lane was already notified within the per-lane cooldown.
function Test-LaneInCooldown {
    [CmdletBinding()]
    param($LaneId, $State)
    if (-not $State.ContainsKey($LaneId)) { return $false }
    return ((Get-Date) - $State[$LaneId]).TotalMinutes -lt $COOLDOWN_MINUTES
}

# Format the ONE message for a (caller key, model) lane.
function Format-LaneMessage {
    [CmdletBinding()]
    param($Lane)
    $nl = [Environment]::NewLine
    $rows = ($Lane.Rows | ForEach-Object {
        "    - waited $([math]::Round($_.Row.waited_s, 1))s + est wait $([math]::Round($_.Row.est_wait_s, 1))s = $([math]::Round($_.Total, 1))s (prio $($_.Row.prio))"
    }) -join $nl
    @(
        "Queue ETA: $($Lane.Key) on $($Lane.Model)"
        "  flagged: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') (waited + est wait >= $([math]::Round($ThresholdSeconds, 0))s threshold)"
        "  est start: $($Lane.EstStart.ToString('yyyy-MM-dd HH:mm:ss'))"
        "  est completion: $($Lane.EstCompletion.ToString('yyyy-MM-dd HH:mm:ss'))"
        "  waiting rows: $($Lane.Rows.Count)"
        $rows
    ) -join $nl
}

# Post to Mattermost exactly like scripts/notify-mattermost.sh: runtime token
# from agent-org/docker/.env, channel id parameter, fail-soft (any failure is
# logged and swallowed -- a down Mattermost must not break the check).
# -DryRun prints the message instead of posting.
function Send-Mattermost {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Message)
    if ($DryRun) {
        Write-LogEntry "DRYRUN would post to channel $ChannelId: $Message" "INFO"
        return $true
    }
    try {
        if (-not (Test-Path $ENV_FILE)) {
            Write-LogEntry "Mattermost env file missing ($ENV_FILE) -- not posting" "WARN"
            return $false
        }
        $tokLine = Select-String -Path $ENV_FILE -Pattern '^AO_MATTERMOST_BOT_TOKEN=' | Select-Object -First 1
        if (-not $tokLine) {
            Write-LogEntry "AO_MATTERMOST_BOT_TOKEN not set in $ENV_FILE -- not posting" "WARN"
            return $false
        }
        $tok = ($tokLine.Line -split '=', 2)[1].Trim().TrimEnd("`r")
        if (-not $tok) {
            Write-LogEntry "AO_MATTERMOST_BOT_TOKEN empty in $ENV_FILE -- not posting" "WARN"
            return $false
        }
        $payload = @{ channel_id = $ChannelId; message = $Message } | ConvertTo-Json -Depth 2
        $null = Invoke-RestMethod -Uri $MM_API -Method Post -Headers @{ Authorization = "Bearer $tok" } -ContentType "application/json" -Body $payload -TimeoutSec 8
        return $true
    }
    catch {
        Write-LogEntry "Mattermost post failed (fail-soft): $($_.Exception.Message)" "WARN"
        return $false
    }
}

# One notifier pass: poll, flag, dedup, post. Returns $true when the probe
# itself succeeded (regardless of whether anything was posted), $false when
# the snapshot could not be fetched.
function Invoke-QueueEtaCheck {
    [CmdletBinding()]
    param()
    $snapshot = Get-QueueSnapshot
    if ($null -eq $snapshot) { return $false }

    $lanes = Get-FlaggedLanes -Snapshot $snapshot
    $state = Get-DedupState

    if ($lanes.Count -eq 0) {
        Write-LogEntry "queue idle or nothing over the $([math]::Round($ThresholdSeconds, 0))s threshold" "DEBUG"
        # All-clear: drop lanes that are no longer flagged so they can
        # re-notify after the cooldown once they queue up again.
        if ($state.Count -gt 0) {
            $cleared = @($state.Keys)
            foreach ($k in $cleared) { $state.Remove($k) }
            Save-DedupState -Lanes $state
            Write-LogEntry "all-clear: cleared $($cleared.Count) lane(s) from dedup state" "DEBUG"
        }
        return $true
    }

    $posted = 0
    foreach ($lane in $lanes) {
        if (Test-LaneInCooldown -LaneId $lane.LaneId -State $state) {
            Write-LogEntry "lane $($lane.LaneId) still flagged but in ${COOLDOWN_MINUTES}m cooldown -- suppressed" "DEBUG"
            continue
        }
        $message = Format-LaneMessage -Lane $lane
        Write-LogEntry "lane $($lane.LaneId) flagged (worst total $([math]::Round($lane.WorstTotal, 1))s)" "INFO"
        if (Send-Mattermost -Message $message) {
            $state[$lane.LaneId] = (Get-Date).ToUniversalTime().ToString("o")
            $posted++
        }
    }
    Save-DedupState -Lanes $state
    Write-LogEntry "pass complete: $($lanes.Count) lane(s) flagged, $posted posted" "SUCCESS"
    return $true
}

# Function to run as a daemon
function Start-Daemon {
    Write-LogEntry "Starting Queue ETA Notifier daemon (interval: ${IntervalSeconds}s, threshold: ${ThresholdSeconds}s, channel: $ChannelId$(if ($DryRun) { ' [DRYRUN]' }))"
    while ($true) {
        try {
            Invoke-QueueEtaCheck | Out-Null
            Start-Sleep $IntervalSeconds
        }
        catch {
            Write-LogEntry "Daemon error: $($_.Exception.Message)" "ERROR"
            Start-Sleep 30
        }
    }
}

# Function to install as Windows Service
function Install-WindowsService {
    $ServicePath = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -Mode daemon -IntervalSeconds $IntervalSeconds -ThresholdSeconds $ThresholdSeconds -ChannelId $ChannelId"

    # Check if service already exists. We do NOT auto-remove an existing
    # service (a destructive action without explicit operator intent): the
    # operator removes it first (see runbook), then re-runs this mode.
    if (Get-Service -Name $SERVICE_NAME -ErrorAction SilentlyContinue) {
        Write-LogEntry "Service $SERVICE_NAME already exists -- stop and remove it first (see runbook), then re-run -Mode install-service" "ERROR"
        exit 1
    }

    # Create the service
    Write-LogEntry "Installing Windows Service: $SERVICE_NAME"
    sc.exe create $SERVICE_NAME binPath= $ServicePath start= auto
    sc.exe description $SERVICE_NAME "Queue ETA notifier for the llm-queue admission queue (issue #25)"

    # Start the service
    Start-Service -Name $SERVICE_NAME
    Write-LogEntry "Service installed and started successfully"
}

# Main execution logic
switch ($Mode.ToLower()) {
    "check" {
        $Success = Invoke-QueueEtaCheck
        exit $(if ($Success) { 0 } else { 1 })
    }

    "daemon" {
        Start-Daemon
    }

    "install-service" {
        if (-not ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole] "Administrator")) {
            Write-LogEntry "Administrator privileges required to install service" "ERROR"
            exit 1
        }
        Install-WindowsService
    }

    default {
        Write-Host "Usage: queue-eta-notify.ps1 [-Mode check|daemon|install-service] [-IntervalSeconds 60] [-ThresholdSeconds 120] [-ChannelId <id>] [-DryRun] [-SnapshotFile <json>]"
        Write-Host ""
        Write-Host "Modes:"
        Write-Host "  check           - Run single notifier pass (default)"
        Write-Host "  daemon          - Run continuously as daemon"
        Write-Host "  install-service - Install as Windows Service (requires admin)"
        exit 1
    }
}
