<#
.SYNOPSIS
  Compact the Docker WSL2 data vhdx to return freed space to C:. The elevated body of the
  systems-administrator "compact_vhdx" capability — run as a RunLevel=Highest Scheduled Task so it
  executes elevated with no UAC. Generalized + hardened from the 2026-07-26 manual compaction.

.DESCRIPTION
  Pauses the health watchdog, stops Docker, `wsl --shutdown`, compacts the vhdx (Optimize-VHD Full,
  diskpart fallback), restarts Docker, WAITS for the daemon AND for the running-container count to
  return to its pre-shutdown value, then re-arms the watchdog. Always restores Docker + the watchdog
  even on error. Writes a machine-readable JSON result the MCP polls.

  SAFETY: refuses unless trapped space >= -MinTrappedGb (a no-op compaction just wastes downtime).
#>
[CmdletBinding()]
param(
  [string]$VhdxPath = (Join-Path $env:LOCALAPPDATA 'Docker\wsl\disk\docker_data.vhdx'),
  [string]$Watchdog = 'TailscaleHealthCheck',
  [string]$ResultFile = '',
  [double]$MinTrappedGb = 1.0,
  [int]$DaemonWaitSec = 180,
  [int]$StackWaitSec = 300
)

$ErrorActionPreference = 'Continue'
# $PSScriptRoot is not reliably populated inside param() defaults on PowerShell 5.1, so resolve
# the script dir (and the default result path) here in the body instead.
$scriptDir = if ($PSScriptRoot) { $PSScriptRoot }
             elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath }
             else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if ([string]::IsNullOrWhiteSpace($ResultFile)) { $ResultFile = Join-Path $scriptDir 'state\compact-result.json' }
$GB = 1000000000
New-Item -ItemType Directory -Force -Path (Split-Path $ResultFile) | Out-Null
$result = [ordered]@{
  ok = $false; started = (Get-Date).ToString('o'); finished = $null
  vhdx_before_gb = $null; vhdx_after_gb = $null; reclaimed_gb = $null
  pre_running = $null; post_running = $null; stack_returned = $false
  c_free_before_gb = $null; c_free_after_gb = $null; error = $null; notes = @()
}
function Save {
  $script:result.finished = (Get-Date).ToString('o')
  # WriteAllText writes UTF-8 WITHOUT a BOM; Set-Content -Encoding utf8 on PS 5.1 adds a BOM that
  # breaks the Python json.load in compaction.compact_status.
  [System.IO.File]::WriteAllText($ResultFile, ($script:result | ConvertTo-Json -Depth 5))
}
function Note($m) { $script:result.notes += "$((Get-Date).ToString('HH:mm:ss')) $m"; Save }

Save
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { $result.error = 'not elevated'; Note 'ERROR not elevated'; exit 1 }
if (-not (Test-Path $VhdxPath)) { $result.error = "vhdx not found: $VhdxPath"; Note 'ERROR vhdx missing'; exit 1 }

$cDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$result.c_free_before_gb = [math]::Round($cDrive.FreeSpace / $GB, 1)
$result.vhdx_before_gb = [math]::Round((Get-Item $VhdxPath).Length / $GB, 1)

# trapped-space guard (used inside the vhdx via the docker-desktop distro)
try {
  $df = (wsl -d docker-desktop -e df -k /mnt/docker-desktop-disk) 2>$null
  $usedKb = ($df | Select-Object -Last 1).Split(' ', [StringSplitOptions]::RemoveEmptyEntries)[2]
  $usedGb = [math]::Round(([double]$usedKb * 1024) / $GB, 1)
  $trapped = [math]::Round($result.vhdx_before_gb - $usedGb, 1)
  Note "trapped ~= $trapped GB (vhdx $($result.vhdx_before_gb) - used $usedGb)"
  if ($trapped -lt $MinTrappedGb) { $result.error = "trapped $trapped GB < MinTrappedGb $MinTrappedGb; refusing no-op compaction"; Note 'REFUSED tiny trapped'; exit 2 }
} catch { Note "trapped check skipped: $($_.Exception.Message)" }

$docker = (Get-Command docker -ErrorAction SilentlyContinue).Source
if (-not $docker) { $docker = 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' }
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $vp = Join-Path (Split-Path (Split-Path $scriptDir -Parent) -Parent) '.venv\Scripts\python.exe'; if (Test-Path $vp) { $py = $vp } }
$mmpost = Join-Path $scriptDir 'mm_post.py'
$tgnotify = Join-Path $scriptDir 'telegram_notify.py'   # DOCKER-INDEPENDENT out-of-band alert (Telegram)
# pre-flight: never compact if the engine is already down/wedged -- it would only deepen the hole.
& $docker version --format '{{.Server.Version}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) { $result.error = 'aborted: Docker engine not responding at start (fix Docker first)'; Note 'ABORT: engine unhealthy at pre-flight'; Save; exit 3 }
try { $result.pre_running = [int]((& $docker ps -q 2>$null | Measure-Object).Count) } catch { $result.pre_running = $null }
Note "pre-shutdown running containers: $($result.pre_running)"

# Pre-flight handshake (out-of-band): announce the downtime window the instant we
# commit, BEFORE the engine goes down. Docker + Mattermost are about to disappear,
# so this Telegram ping is the only way to say "expect ~10-15 min of silence" while
# you're away. The matching "OK / ALERT" ping fires from the finally block.
if ($py -and (Test-Path $tgnotify)) {
  try {
    & $py $tgnotify "ai-stack compaction STARTING now: Docker (and Mattermost) go DOWN ~10-15 min, then auto-recover. You'll get an out-of-band ping when it's back -- or if it gets stuck. Reply 'status' anytime." 2>$null | Out-Null
  } catch {}
}

$dd = Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
$ddPath = if ($dd) { $dd.Path } else { 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }

try {
  try { Stop-ScheduledTask -TaskName $Watchdog -ErrorAction SilentlyContinue; Disable-ScheduledTask -TaskName $Watchdog -ErrorAction Stop | Out-Null; Note "watchdog $Watchdog DISABLED" }
  catch { Note "WARN could not disable watchdog: $($_.Exception.Message)" }

  # Stop the engine GRACEFULLY via the supported CLI. Force-killing the Docker Desktop processes
  # (the old approach) left the backend half-written and wedged it on relaunch ("already running"
  # yet engine down) -- the 2026-07-31 incident. Graceful stop cleans backend state so start works.
  Note 'docker desktop stop (graceful)'
  & $docker desktop stop 2>&1 | Out-Null
  $downOk = $false
  for ($k = 0; $k -lt 24; $k++) {   # wait up to ~120s for the engine to actually be down
    Start-Sleep 5
    & $docker version --format '{{.Server.Version}}' 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { $downOk = $true; break }
  }
  if (-not $downOk) {
    Note 'WARN engine still responding after graceful stop; last-resort process stop'
    Get-Process 'Docker Desktop','com.docker.backend','com.docker.build','com.docker.dev-envs','com.docker.extensions' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  }
  Note 'engine stopped'
  Start-Sleep 3
  wsl --shutdown 2>&1 | Out-Null   # belt: ensure the docker-desktop VM is down so the vhdx is released
  Note 'wsl --shutdown; waiting for handles'
  Start-Sleep 12

  try {
    Mount-VHD -Path $VhdxPath -ReadOnly -ErrorAction Stop | Out-Null
    Note 'Optimize-VHD -Mode Full (minutes)'
    Optimize-VHD -Path $VhdxPath -Mode Full -ErrorAction Stop
    Dismount-VHD -Path $VhdxPath -ErrorAction SilentlyContinue
    Note 'Optimize-VHD complete'
  } catch {
    Note "Optimize-VHD failed: $($_.Exception.Message); diskpart fallback"
    Dismount-VHD -Path $VhdxPath -ErrorAction SilentlyContinue
    $dp = Join-Path $env:TEMP 'sysadmin-compact-dp.txt'
    "select vdisk file=`"$VhdxPath`"`nattach vdisk readonly`ncompact vdisk`ndetach vdisk`nexit" | Set-Content -Path $dp -Encoding ascii
    diskpart /s $dp 2>&1 | Out-Null
    Note 'diskpart compact complete'
  }
  $result.vhdx_after_gb = [math]::Round((Get-Item $VhdxPath).Length / $GB, 1)
  $result.reclaimed_gb = [math]::Round($result.vhdx_before_gb - $result.vhdx_after_gb, 1)
  Note "vhdx $($result.vhdx_before_gb) -> $($result.vhdx_after_gb) GB (reclaimed $($result.reclaimed_gb))"
}
finally {
  # Restart the engine GRACEFULLY, with retries. `docker desktop start` cleans up and starts dockerd;
  # if it doesn't come up, reset (stop + wsl --shutdown) and retry before giving up.
  $up = $false
  for ($attempt = 1; $attempt -le 3 -and -not $up; $attempt++) {
    if ($attempt -gt 1) {
      Note "engine not up; resetting for attempt $attempt"
      & $docker desktop stop 2>&1 | Out-Null; Start-Sleep 5
      wsl --shutdown 2>&1 | Out-Null; Start-Sleep 8
    }
    if (-not (Get-Process 'Docker Desktop' -ErrorAction SilentlyContinue)) {
      if (Test-Path $ddPath) { Start-Process -FilePath $ddPath }; Start-Sleep 8
    }
    Note "docker desktop start (attempt $attempt)"
    & $docker desktop start 2>&1 | Out-Null
    for ($i = 0; $i -lt [math]::Ceiling($DaemonWaitSec / 5); $i++) {
      Start-Sleep 5
      $v = & $docker version --format '{{.Server.Version}}' 2>$null
      if ($LASTEXITCODE -eq 0 -and $v) { $up = $true; Note "daemon up (server $v) on attempt $attempt"; break }
    }
  }
  if (-not $up) { Note 'ERROR daemon NOT up after retries -- needs a manual Docker quit/restart or reboot' }

  # verify the stack RETURNED: running-container count back to pre-shutdown value
  if ($result.pre_running) {
    for ($j = 0; $j -lt [math]::Ceiling($StackWaitSec / 10); $j++) {
      $now = [int]((& $docker ps -q 2>$null | Measure-Object).Count)
      $result.post_running = $now
      if ($now -ge $result.pre_running) { $result.stack_returned = $true; Note "stack returned: $now/$($result.pre_running) running"; break }
      Start-Sleep 10
    }
    if (-not $result.stack_returned) { Note "WARN stack NOT fully returned: $($result.post_running)/$($result.pre_running)" }
  }

  try { Enable-ScheduledTask -TaskName $Watchdog -ErrorAction Stop | Out-Null; Note "watchdog $Watchdog RE-ENABLED" }
  catch { Note "WARN could not re-enable watchdog: $($_.Exception.Message) -- RE-ENABLE MANUALLY" }

  $cAfter = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
  $result.c_free_after_gb = [math]::Round($cAfter.FreeSpace / $GB, 1)
  $result.ok = ($null -ne $result.reclaimed_gb) -and ($up) -and ($result.stack_returned -or -not $result.pre_running)
  Note "DONE ok=$($result.ok) C: $($result.c_free_before_gb) -> $($result.c_free_after_gb) GB"

  # Notify. On success the engine (and Mattermost) is back -> post a #sysadmin summary. On FAILURE the
  # engine is down so Mattermost (a container) is unreachable -> fire a Windows msg popup so the
  # incident is LOUD, not silent (a compaction must never strand Docker quietly again).
  if ($result.ok) {
    $sum = "[sysadmin] compaction OK: reclaimed $($result.reclaimed_gb) GB, C: $($result.c_free_before_gb)->$($result.c_free_after_gb) GB, stack $($result.post_running)/$($result.pre_running) running."
    if ($py -and (Test-Path $mmpost)) { try { & $py $mmpost $sum 2>$null | Out-Null } catch {} }
    # Out-of-band confirmation (works whether or not you're at the machine).
    if ($py -and (Test-Path $tgnotify)) { try { & $py $tgnotify $sum 2>$null | Out-Null } catch {} }
  } else {
    if (-not $up) { $result.error = "compaction left the Docker engine DOWN (reclaimed $($result.reclaimed_gb) GB). Quit and restart Docker Desktop, or reboot." }
    elseif (-not $result.stack_returned) { $result.error = "engine came back but only $($result.post_running)/$($result.pre_running) containers returned." }
    try { msg * /time:0 ("ai-stack sysadmin ALERT: " + $result.error) 2>$null } catch {}
    if ($py -and (Test-Path $mmpost)) { try { & $py $mmpost ("[sysadmin] ALERT: " + $result.error) 2>$null | Out-Null } catch {} }
    # CRITICAL out-of-band alert: on failure Docker (and Mattermost) is down, so the MM post above
    # cannot land. Telegram posts straight to the operator's phone and is actionable -- the listener
    # accepts these commands as replies. This is the fix for "compaction stranded Docker silently".
    if ($py -and (Test-Path $tgnotify)) {
      try {
        $tgmsg = "ALERT ai-stack compaction FAILED: " + $result.error + " -- reply 'docker up', 'recover', or 'status' to act."
        & $py $tgnotify $tgmsg 2>$null | Out-Null
      } catch {}
    }
  }
  Save
}
