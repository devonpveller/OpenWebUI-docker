# scripts/check-openbrain-health.ps1
#
# Health probe for the **Open Brain** compose project (project = "open-brain"),
# which a plain `docker compose ...` from the ai-stack project dir CANNOT see.
# This is the single, canonical Open Brain probe — called by both:
#   - the autonomous monitor: scripts\checks\stack-watchdog.ps1 (formerly
#     check-tailscale-health.ps1), Invoke-OpenBrainHealth, with
#     -Repair -Quiet -LogPath logs\tailscale-health.log, on every pass of the
#     host scheduled task \StackWatchdog (every 10 min, default -Mode check).
#     That 10-minute cadence is what the CPU-spin guard's window is built on.
#   - by hand, with or without -Repair. (quick-fixes.bat :status_check /
#     :openbrain_check named it as %SCRIPT_DIR%check-openbrain-health.ps1,
#     i.e. under scripts/recovery/, where it is not; that .bat was archived
#     2026-09-25.)
#
# It addresses a class of failure that simple liveness checks MISS: a container
# that is "Up" (green) yet functionally dead. The signature case (2026-06-05):
# openbrain-db restarted, openbrain-mcp kept a long-lived DB connection that died,
# and every MCP tool call (Open WebUI tools + Claude connector) returned
# "Broken pipe (os error 32)" -> mcpo surfaced HTTP 500 -- while `docker ps` showed
# openbrain-mcp healthy. See memory: openbrain-mcp-stale-db-connection.
#
# A second instance of the same "green but functionally dead" class (2026-06-07):
# openbrain-mcpo-ext (mcpo :latest) entered an async read-loop CPU busy-spin and
# pinned a FULL CORE for ~13 days (top host CPU consumer via vmmemwsl). Its
# healthcheck only fetches the CACHED /open-brain-extensions/openapi.json, so it
# reported healthy the whole time. Remediation: `docker restart openbrain-mcpo-ext`
# (100% -> ~0.2% instantly). NOT triggered by a graceful upstream restart/pause --
# it's a latent no-backoff spin in mcpo's streamable-http client, tripped by an
# ungraceful disconnect (netns break / hard kill). It came back 2026-10-04 (a
# Docker restart) and half-pegged the host for about a day; on 2026-10-05 the
# operator reversed the 2026-06-07 document-only decision. Upstream: the spin
# is anyio's (agronholm/anyio#1111, a done task re-cancelled via call_soon
# forever; fixed in anyio 4.14.x); mcpo v0.0.20 ships anyio 4.12.1 and no newer
# mcpo exists (open-webui/mcpo#302 is open). So this probe now has a CPU-SPIN
# GUARD for openbrain-mcpo and openbrain-mcpo-ext (same image): an average at or
# above -CpuSpinPercent (80% of one core) over at least -CpuSpinMinIntervals (3)
# consecutive intervals of at most -CpuSpinMaxIntervalMinutes (20) each, spanning
# at least -CpuSpinWindowMinutes (30) -> under -Repair, `docker restart` of THAT container only, one log line,
# one alert (#sysadmin, else Telegram); at most one auto-restart per container
# per hour, a spin back inside the hour alerts only; no docker data, no action.
# The rule and the measurement are above Invoke-CpuSpinGuard (item ef-mcpo-spin).
# See memory: openbrain-mcpo-ext-cpu-spin.
#
# Probes (by container NAME, project-agnostic — never `docker compose`):
#   - openbrain-db          running               (the dependency)
#   - openbrain-mcp         running + STALE-POOL guard (db started after mcp -> restart)
#   - openbrain-mcpo[-ext]  running + CPU-SPIN guard (>= 80% of a core over 3+ intervals
#                           of <= 20 min spanning 30+ min -> restart that one,
#                           capped 1/h, alert;
#                           state in logs\.openbrain-cpu-spin-state.json)
#   - openbrain-research    http://127.0.0.1:8818/health "db":true  (STALE-POOL guard, same class as mcp)
#   - openbrain-curator     http://127.0.0.1:8816/health "db":true  (same guard; absent = the 2026-09-05 loop)
#   - openbrain-gateway     http://127.0.0.1:8061/health == "ok"   (functional, no secret)
#   - openbrain-rest        http://127.0.0.1:3001/   (PostgREST proxy reachable)
#   - openbrain-postgrest / -wiki / -wiki-viewer / -entity-worker  running
#   - openbrain-idea-refinery  running (Idea Refinery drain; profile-gated, liveness only)
#   - openbrain-pantry      /health {"ok":true,"db":true} via docker exec (NO host port);
#                           profile-gated 'pantry', OFF by default: ABSENT is a skip, never a fault
#   - research_jobs         status='error' rows in the last 24 h -> WARN naming count +
#                           newest id + left(error,80); none -> OK. Queried with
#                           `docker exec <db> psql -U postgres` over the container's
#                           unix socket - no password leaves this script (the same
#                           pattern scripts/checks/smoke-agent-memory-live.ps1 uses).
#
# Usage:
#   .\scripts\check-openbrain-health.ps1            # detect + report, exit 0/1
#   .\scripts\check-openbrain-health.ps1 -Repair    # also auto-restart broken pieces
#   .\scripts\check-openbrain-health.ps1 -Quiet     # only WARN/ERROR lines (for daemon)
#
# Exit code: 0 = all healthy (after any repairs), 1 = at least one unresolved fault.

[CmdletBinding()]
param(
  [switch]$Repair,
  [switch]$Quiet,
  # When set, non-suppressed status lines are ALSO appended (timestamped) to this
  # file. The autonomous monitor passes its own logs\tailscale-health.log so the
  # per-container detail survives — Write-Host output is not capturable via 2>&1.
  [string]$LogPath,
  # The Postgres container the research_jobs query runs in. Defaults to the
  # production database; a tester points it at an isolated copy so no production
  # row is ever created. The liveness / stale-pool probes stay on 'openbrain-db'
  # - they describe THIS stack, the query describes a table.
  [string]$DbContainer = 'openbrain-db',
  [string]$DbName = 'openbrain',
  # CPU-spin guard (see Invoke-CpuSpinGuard). Which containers are MEASURED;
  # restart is still refused for any name not on $SpinRestartAllowList below,
  # so adding a name here only adds a report + alert.
  [string[]]$CpuSpinContainers = @('openbrain-mcpo', 'openbrain-mcpo-ext'),
  # Average CPU over a run interval, in % of ONE core, at or above which the
  # interval counts as hot, and how long a hot streak must last to be a spin.
  # Calibration 2026-10-05: idle mcpo 0.2%; the spin pins ~100%.
  [ValidateRange(10, 1000)][int]$CpuSpinPercent = 80,
  [ValidateRange(10, 1440)][int]$CpuSpinWindowMinutes = 30,
  # A spin needs this many consecutive hot intervals, and an interval longer than
  # the bound (missed passes, a NO DATA streak) is never counted: it re-baselines.
  # The \StackWatchdog cadence is 10 min; 20 tolerates one skipped trigger.
  [ValidateRange(2, 20)][int]$CpuSpinMinIntervals = 3,
  [ValidateRange(2, 60)][int]$CpuSpinMaxIntervalMinutes = 20,
  # Bound on each docker inspect / exec the guard makes (restart: 60 s).
  [ValidateRange(1, 300)][int]$DockerTimeoutSeconds = 20
)

$ErrorActionPreference = 'Continue'
$script:Faults = 0
# The ONLY containers the CPU-spin guard may restart. Exact, case-sensitive
# names; never openwebui, tailscale or anything else.
$SpinRestartAllowList = @('openbrain-mcpo', 'openbrain-mcpo-ext')
# scripts\checks\ -> repo root (state file under logs\, the alert senders' .venv).
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# `powershell -File` hands "a,b" over as ONE string; split it here.
$CpuSpinContainers = @($CpuSpinContainers | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })

function Write-Ob {
  param([string]$Name, [string]$State, [string]$Detail = '')
  # State: ok | down | warn | fix
  $colors  = @{ ok = 'Green'; down = 'Red'; warn = 'Yellow'; fix = 'Cyan' }
  $symbols = @{ ok = '[OK]';  down = '[DOWN]'; warn = '[WARN]'; fix = '[FIX]' }
  if ($Quiet -and $State -eq 'ok') { return }
  $color = $colors[$State]; if (-not $color) { $color = 'Gray' }
  $sym   = $symbols[$State]; if (-not $sym) { $sym = '[..]' }
  Write-Host ("  {0,-7} {1,-26} {2}" -f $sym, $Name, $Detail) -ForegroundColor $color
  if ($LogPath) {
    $level = switch ($State) { 'down' { 'ERROR' } 'warn' { 'WARN' } 'fix' { 'WARN' } default { 'INFO' } }
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    try { "$ts [$level] OpenBrain $($symbols[$State]) $Name $Detail" | Out-File -FilePath $LogPath -Append -Encoding UTF8 } catch { }
  }
}

# Bounded since ef-mcpo-spin: a wedged docker used to hang the whole probe (and
# the watchdog pass that calls it) here. 'unknown' = docker did not answer
# within -DockerTimeoutSeconds or failed for a reason other than "no such
# container"; callers take no action on it.
function Get-CState {
  param([string]$Name)
  $r = Invoke-ObDocker @('inspect', '--format', '{{.State.Status}}', $Name)
  if ($r.Ok) { return ((@($r.Lines) | Select-Object -First 1) -as [string]).Trim() }
  if ($r.Why -match '(?i)no such (object|container)') { return 'absent' }
  $script:CStateWhy[$Name] = $r.Why
  return 'unknown'
}
$script:CStateWhy = @{}

# Raw ISO-8601 UTC start timestamp. UTC ISO strings are lexicographically
# ordered, so plain string comparison is a safe "started after" test (avoids
# PS 5.1 choking on docker's 9-digit nanosecond fraction).
function Get-CStartedAt {
  param([string]$Name)
  $r = Invoke-ObDocker @('inspect', '--format', '{{.State.StartedAt}}', $Name)
  if (-not $r.Ok) { return $null }
  return ((@($r.Lines) | Select-Object -First 1) -as [string]).Trim()
}

function Test-HttpOk {
  param([string]$Url, [int]$TimeoutSec = 5, [string]$MustContain = $null)
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    if ($r.StatusCode -ne 200) { return $false }
    if ($MustContain -and ($r.Content -notmatch [regex]::Escape($MustContain))) { return $false }
    return $true
  } catch {
    return $false
  }
}

# Confirm a container is running; optionally start/restart it under -Repair.
# Returns $true if running at the end.
function Confirm-ObContainer {
  param(
    [string]$Name,
    [switch]$Critical
  )
  $state = Get-CState $Name
  if ($state -eq 'running') { Write-Ob $Name ok 'running'; return $true }
  if ($state -eq 'absent')  { Write-Ob $Name down 'not present (container missing)'; $script:Faults++; return $false }
  if ($state -eq 'unknown') { Write-Ob $Name warn "NO DATA (docker inspect $($script:CStateWhy[$Name])) - no action"; $script:Faults++; return $false }

  # exited / created / restarting / paused
  if ($Repair) {
    Write-Ob $Name fix "state=$state -> docker start"
    docker start $Name 2>&1 | Out-Null
    Start-Sleep 3
    if ((Get-CState $Name) -eq 'running') { Write-Ob $Name ok 'started'; return $true }
    Write-Ob $Name down "could not start (check: docker logs $Name)"; $script:Faults++; return $false
  }

  Write-Ob $Name down "state=$state (run with -Repair to start)"; $script:Faults++; return $false
}

# ---- CPU-spin guard helpers (ef-mcpo-spin, 2026-10-05) ----------------------
# Every docker call the guard makes goes through Invoke-ObProcess: a .NET
# Process with both streams read asynchronously and a hard WaitForExit bound,
# the process tree killed on timeout (the stack-watchdog.ps1 Invoke-BoundedProcess
# pattern, without its job object). A call that times out or fails returns
# Ok=$false with a reason, and the guard then does NOTHING (no data = no action).
function ConvertTo-ObArgument {
  param([AllowEmptyString()][string]$Value)
  if ($Value -eq '') { return '""' }
  if ($Value -notmatch '[\s"]') { return $Value }
  $e = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
  $e = [regex]::Replace($e, '(\\+)$', '$1$1')
  return '"' + $e + '"'
}

function Invoke-ObProcess {
  param([string]$FilePath, [string[]]$ProcArgs = @(), [int]$TimeoutSeconds = 20)
  $res = [pscustomobject]@{ Ok = $false; Lines = @(); Why = '' }
  $proc = New-Object System.Diagnostics.Process
  try {
    $psi = $proc.StartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ((@($ProcArgs) | ForEach-Object { ConvertTo-ObArgument $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.RedirectStandardInput = $true
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    [void]$proc.Start()
    $outTask = $proc.StandardOutput.ReadToEndAsync()
    $errTask = $proc.StandardError.ReadToEndAsync()
    $proc.StandardInput.Close()
    if (-not $proc.WaitForExit($TimeoutSeconds * 1000)) {
      & taskkill.exe /PID $proc.Id /T /F 2>$null | Out-Null
      $res.Why = "did not answer within ${TimeoutSeconds}s"
      return $res
    }
    $proc.WaitForExit()
    $leftMs = [int][Math]::Max(0, ($TimeoutSeconds * 1000) - $sw.ElapsedMilliseconds)
    if (-not [System.Threading.Tasks.Task]::WaitAll(@($outTask, $errTask), $leftMs)) {
      $res.Why = "did not answer within ${TimeoutSeconds}s (a child kept its output open)"
      return $res
    }
    $out = @(@($outTask.Result -split "`r?`n") | Where-Object { $_ -ne '' })
    $err = @(@($errTask.Result -split "`r?`n") | Where-Object { $_ -and $_.Trim() })
    if ($proc.ExitCode -ne 0) {
      $first = @($err + $out) | Select-Object -First 1
      $res.Why = "exited $($proc.ExitCode)" + $(if ($first) { ": $first" } else { '' })
      return $res
    }
    $res.Ok = $true; $res.Lines = $out
    return $res
  } catch {
    $res.Why = "could not run: $($_.Exception.Message)"
    return $res
  } finally {
    try { if (-not $proc.HasExited) { $proc.Kill() } } catch { }
    try { $proc.Dispose() } catch { }
  }
}

function Invoke-ObDocker {
  param([string[]]$DockerArgs, [int]$TimeoutSeconds = $DockerTimeoutSeconds)
  return Invoke-ObProcess -FilePath 'docker' -ProcArgs $DockerArgs -TimeoutSeconds $TimeoutSeconds
}

# The container's own cumulative CPU time in microseconds, from its cgroup
# (cgroup v2 cpu.stat usage_usec; v1 cpuacct.usage in ns as the fallback).
# Read INSIDE the container: Docker Desktop's cgroups are not visible from the
# Windows host, and the docker CLI exposes only a percentage. Returns
# @{ Usec = <int64> } or @{ Why = <reason> }.
function Get-ObCpuUsec {
  param([string]$Name)
  $r = Invoke-ObDocker @('exec', $Name, 'cat', '/sys/fs/cgroup/cpu.stat')
  if ($r.Ok) {
    foreach ($l in $r.Lines) {
      if ($l -match '^usage_usec\s+(\S+)\s*$') {
        $v = ConvertTo-ObInt64 $Matches[1]
        if ($null -ne $v) { return @{ Usec = $v } }
        # a usage_usec line whose value is not a 64-bit count: say so, do not guess
        return @{ Why = ("cpu.stat usage_usec is not a 64-bit count: '{0}'" -f ($Matches[1].Substring(0, [Math]::Min(40, $Matches[1].Length)))) }
      }
    }
    $why = 'cpu.stat has no usage_usec line'
  } else {
    $why = "docker exec $($r.Why)"
    if ($r.Why -match 'did not answer') { return @{ Why = $why } }
  }
  $r1 = Invoke-ObDocker @('exec', $Name, 'cat', '/sys/fs/cgroup/cpuacct/cpuacct.usage')
  if ($r1.Ok) {
    $v = (@($r1.Lines) | Select-Object -First 1)
    $ns = ConvertTo-ObInt64 "$v"
    if ($null -ne $ns) { return @{ Usec = [int64]($ns / 1000) } }
  }
  return @{ Why = $why }
}

# The house alert path for a standalone check (the scripts\maintenance\disk-guard.ps1
# shape): #sysadmin via mm_post.py, else Telegram via telegram_notify.py, both
# through the repo .venv python and bounded. Returns the channel that took it,
# or '' when none did - never claims a send that did not happen.
function Send-ObAlert {
  param([string]$Text)
  $py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
  if (-not (Test-Path $py)) { return '' }
  $r = Invoke-ObProcess -FilePath $py -ProcArgs @((Join-Path $RepoRoot 'scripts\sysadmin-mcp\mm_post.py'), $Text) -TimeoutSeconds 45
  if ($r.Ok) { return '#sysadmin' }
  $r = Invoke-ObProcess -FilePath $py -ProcArgs @((Join-Path $RepoRoot 'scripts\sysadmin-mcp\telegram_notify.py'), ("ai-stack (Mattermost post failed): " + $Text)) -TimeoutSeconds 45
  if ($r.Ok) { return 'Telegram' }
  return ''
}

function ConvertFrom-ObInstant {
  param($Value)
  if (-not $Value) { return $null }
  try { return [DateTime]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() }
  catch { return $null }
}

# A non-negative 64-bit count from a state field or a counter line, or $null.
# Never casts blindly: a hand-edited "abc" or an overflowing value is $null.
function ConvertTo-ObInt64 {
  param($Value)
  if ($null -eq $Value) { return $null }
  $o = [int64]0
  if ([int64]::TryParse(([string]$Value).Trim(), [Globalization.NumberStyles]::None, [Globalization.CultureInfo]::InvariantCulture, [ref]$o)) { return $o }
  return $null
}

# THE GUARD. openbrain-mcpo[-ext] (mcpo v0.0.20, anyio 4.12.1) can busy-spin
# one core for days while its healthcheck stays green (header, and memory
# openbrain-mcpo-ext-cpu-spin). Rule, per container, across runs:
#   - each run reads the container's cumulative CPU counter; the AVERAGE over
#     the interval since the previous run is (delta CPU time / delta wall time),
#     in % of one core. Not a `docker stats` snapshot: that is a ~1 s sample
#     and catches the 30 s python healthcheck or one tool call as a spike.
#   - an interval COUNTS only when it is 60 s to $CpuSpinMaxIntervalMinutes
#     (20) long. A longer one - missed passes, a NO DATA streak, a restored
#     state file - is not evidence: one average over a long gap hides its shape
#     (a 7-min 400% burst averages 90% over 31 min), so it re-baselines and any
#     streak starts over.
#   - a counted interval at or above $CpuSpinPercent is "hot"; the streak starts
#     at the start of its first hot interval; any cool interval ends it.
#   - SPIN = at least $CpuSpinMinIntervals (3) consecutive hot intervals AND at
#     least $CpuSpinWindowMinutes (30) since the streak began. Never one
#     sample, never one long gap.
#   - action (under -Repair, and only for a name on $SpinRestartAllowList):
#     `docker restart <that container>`, one log line, one alert. At most one
#     auto-restart per container per hour, stamped BEFORE the attempt so a
#     failed restart counts too: a spin back inside the hour gets an alert only
#     (itself at most one per hour), never a second restart.
#   - no data (docker inspect/exec timed out or failed, or the counter is not a
#     number) = no action: a clear log line, a fault, the state not advanced.
#   - a state timestamp more than 5 min in the FUTURE (clock skew, a hand edit)
#     is corrupt: that container re-baselines rather than going blind until the
#     clock catches up. A last_restart that is unreadable or in the future is
#     read as "restarted now" - the safe side: alert only for the next hour.
# State: logs\.openbrain-cpu-spin-state.json (the watchdog's logs\ state-file
# convention), keyed by container name; the restart/alert stamps survive the
# restart that resets the counter.
function Invoke-CpuSpinGuard {
  param([string[]]$Names)
  $statePath = Join-Path $RepoRoot 'logs\.openbrain-cpu-spin-state.json'
  $st = @{}
  if (Test-Path $statePath) {
    try {
      $raw = Get-Content -LiteralPath $statePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
      foreach ($p in $raw.PSObject.Properties) {
        $h = @{}
        foreach ($q in $p.Value.PSObject.Properties) { $h[$q.Name] = $q.Value }
        $st[$p.Name] = $h
      }
    } catch {
      Write-Ob 'cpu-spin-guard' warn "state file unreadable, starting fresh: $($_.Exception.Message)"
      $st = @{}
    }
  }
  $now = [DateTime]::UtcNow
  $nowS = $now.ToString('o')
  $skew = $now.AddMinutes(5)

  foreach ($name in $Names) {
    $label = "$name cpu"
    $insp = Invoke-ObDocker @('inspect', '--format', '{{.State.Status}}|{{.Id}}|{{.State.StartedAt}}', $name)
    if (-not $insp.Ok) {
      if ($insp.Why -match '(?i)no such (object|container)') { continue }   # absent: reported by the liveness probe
      Write-Ob $label warn "NO DATA (docker inspect $($insp.Why)) - no action"; $script:Faults++; continue
    }
    $f = ((@($insp.Lines) | Select-Object -First 1) -split '\|')
    if ($f[0] -ne 'running') { continue }                                   # not running: the liveness probe owns it
    $identity = "$($f[1])|$($f[2])"
    $cpu = Get-ObCpuUsec $name
    if ($null -eq $cpu.Usec) {
      Write-Ob $label warn "NO DATA (cpu counter: $($cpu.Why)) - no action"; $script:Faults++; continue
    }
    $usec = [int64]$cpu.Usec

    $prev = $st[$name]
    if (-not ($prev -is [hashtable])) { $prev = @{} }
    $st[$name] = $prev
    if ($prev.last_restart) {
      $lr = ConvertFrom-ObInstant $prev.last_restart
      if (-not $lr -or $lr -gt $skew) {
        Write-Ob $label warn "last_restart '$($prev.last_restart)' is unreadable or in the future - read as restarted now (no auto-restart for an hour)"
        $prev.last_restart = $nowS
      }
    }
    $prevAt = ConvertFrom-ObInstant $prev.at
    $prevUsec = ConvertTo-ObInt64 $prev.usec
    $hotSince = ConvertFrom-ObInstant $prev.hot_since
    $rebase = $null
    if (-not $prevAt -or $null -eq $prevUsec) {
      $rebase = 'baseline recorded (CPU average is measured from the next run)'
    } elseif ($prevAt -gt $skew -or ($hotSince -and $hotSince -gt $skew)) {
      $rebase = 'baseline recorded: a state timestamp is in the future (clock skew or a corrupt file) - re-baselined'
    } elseif ($prev.identity -ne $identity) {
      $rebase = 'baseline recorded (new container identity: it was restarted or recreated)'
    } elseif ($usec -lt $prevUsec) {
      $rebase = 'baseline recorded (the counter went backwards)'
    } else {
      $elapsed = ($now - $prevAt).TotalSeconds
      if ($elapsed -lt 60) {
        Write-Ob $label ok ("interval {0:N0}s too short to average; baseline kept" -f $elapsed)
        continue
      }
      if ($elapsed -gt ($CpuSpinMaxIntervalMinutes * 60)) {
        $rebase = ("baseline recorded: the interval was {0:N0} min, over the {1}-min bound - not counted, streak reset" -f ($elapsed / 60), $CpuSpinMaxIntervalMinutes)
      }
    }
    if ($rebase) {
      $prev.identity = $identity; $prev.usec = $usec; $prev.at = $nowS; $prev.hot_since = $null; $prev.hot_count = 0
      Write-Ob $label ok $rebase
      continue
    }

    $pct = 100.0 * ($usec - $prevUsec) / ($elapsed * 1000000.0)
    $hot = ($pct -ge $CpuSpinPercent)
    $hotCount = ConvertTo-ObInt64 $prev.hot_count
    if ($null -eq $hotCount) { $hotCount = 0 }
    if ($hot) {
      # a streak with no counted hot interval behind it (old/restored state) starts here
      if (-not $hotSince -or $hotCount -lt 1) { $prev.hot_since = $prev.at; $hotCount = 0 }
      $hotCount++
    } else {
      $prev.hot_since = $null; $hotCount = 0
    }
    $prev.hot_count = $hotCount
    $prev.usec = $usec; $prev.at = $nowS; $prev.identity = $identity
    $avg = "{0:N1}% of one core over {1:N0} min" -f $pct, ($elapsed / 60)
    if (-not $hot) { Write-Ob $label ok $avg; continue }
    $hotMin = ($now - (ConvertFrom-ObInstant $prev.hot_since)).TotalMinutes
    if ($hotCount -lt $CpuSpinMinIntervals -or $hotMin -lt $CpuSpinWindowMinutes) {
      Write-Ob $label warn ("{0}; hot for {1:N0} min over {2} interval(s) (a spin needs {3} min and {4} intervals) - watching" -f $avg, $hotMin, $hotCount, $CpuSpinWindowMinutes, $CpuSpinMinIntervals)
      continue
    }

    # --- sustained spin --------------------------------------------------------
    $what = "CPU SPIN on ${name}: {0}, at or above {1}% for {2:N0} min over {3} intervals" -f $avg, $CpuSpinPercent, $hotMin, $hotCount
    $lastRestart = ConvertFrom-ObInstant $prev.last_restart
    # Throttle for the ALERT-ONLY paths (one per container per hour). The restart
    # alert does not set it: a spin back inside the hour must page once more.
    $lastAlert = ConvertFrom-ObInstant $prev.last_hold_alert
    $alertDue = (-not $lastAlert) -or ($lastAlert -gt $skew) -or (($now - $lastAlert).TotalMinutes -ge 60)
    if (-not $Repair) {
      Write-Ob $label warn "$what - run with -Repair to act (restart + alert)"; $script:Faults++; continue
    }
    $why = $null
    if ($SpinRestartAllowList -cnotcontains $name) {
      $why = 'not on the restart allow-list - alert only'
    } elseif ($lastRestart -and (($now - $lastRestart).TotalMinutes -lt 60)) {
      $why = "back within the hour of the last auto-restart ($($prev.last_restart)) - alert only, no restart"
    }
    if ($why) {
      $sent = ''
      if ($alertDue) {
        $sent = Send-ObAlert "openbrain: $what; $why. Manual fix: docker restart $name"
        if ($sent) { $prev.last_hold_alert = $nowS }
      }
      $tail = if (-not $alertDue) { 'alert already sent this hour' } elseif ($sent) { "alert -> $sent" } else { 'alert NOT delivered (no channel took it)' }
      Write-Ob $label warn "$what; $why; $tail"; $script:Faults++
      continue
    }

    # restart: only this container, at most once an hour (stamped before the attempt)
    $prev.last_restart = $nowS; $prev.hot_since = $null; $prev.hot_count = 0; $prev.usec = $null
    $rs = Invoke-ObDocker @('restart', $name) 60
    $outcome = if ($rs.Ok) { 'docker restart ok' } else { "docker restart FAILED ($($rs.Why))" }
    $sent = Send-ObAlert "openbrain: $what; auto-restarted it ($outcome; cap 1 per hour). A spin back inside the hour alerts without restarting."
    if ($sent) { $prev.last_restart_alert = $nowS }
    $tail = if ($sent) { "alert -> $sent" } else { 'alert NOT delivered (no channel took it)' }
    if ($rs.Ok) {
      Write-Ob $label fix "$what -> docker restart $name ($outcome); $tail"
    } else {
      Write-Ob $label down "$what -> docker restart $name ($outcome); $tail"; $script:Faults++
    }
  }

  try {
    $dir = Split-Path -Parent $statePath
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $o = [ordered]@{}
    foreach ($k in ($st.Keys | Sort-Object)) { $o[$k] = $st[$k] }
    [IO.File]::WriteAllText($statePath, ($o | ConvertTo-Json -Depth 4), (New-Object System.Text.UTF8Encoding $false))
  } catch {
    Write-Ob 'cpu-spin-guard' warn "state file not written: $($_.Exception.Message)"
  }
}

Write-Host "==> Open Brain stack health (project: open-brain)" -ForegroundColor Cyan

# ---- 1. Database (the dependency the stale-pool bug hinges on) --------------
$dbUp = Confirm-ObContainer 'openbrain-db' -Critical

# ---- 1b. Failed research runs --------------------------------------------
# A research job that ended status='error' is invisible on every surface above:
# the service is running, /health says db:true, the job row just sits there.
# One query, newest error first; updated_at is stamped by the table's touch
# trigger when the status flips, so "last 24 h" means "failed in the last 24 h",
# not "was submitted then". A query failure (table missing, psql absent) is a
# fault too - an unreadable table is not a clean one.
if ((Get-CState $DbContainer) -eq 'running') {
  $rjSql = "SELECT count(*) OVER (), id, left(regexp_replace(coalesce(error, ''), '[[:space:]|]+', ' ', 'g'), 80) FROM public.research_jobs WHERE status = 'error' AND updated_at >= now() - interval '24 hours' ORDER BY updated_at DESC LIMIT 1"
  $rjOut = (& docker exec $DbContainer psql -U postgres -d $DbName -tA -v ON_ERROR_STOP=1 -c $rjSql 2>&1 | Out-String)
  $rjRow = ($rjOut -split "`r?`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
  if ($LASTEXITCODE -ne 0) {
    # (a native stderr line arrives as 'docker.exe : ERROR: ...' under 2>&1 - drop the prefix)
    $rjRow = $rjRow -replace '^docker(\.exe)? : ', ''
    Write-Ob 'research_jobs' warn "query failed on ${DbContainer}: $rjRow"; $script:Faults++
  } elseif (-not $rjRow) {
    Write-Ob 'research_jobs' ok "no status='error' rows in 24 h ($DbContainer)"
  } else {
    $rjF = $rjRow -split '\|', 3
    Write-Ob 'research_jobs' warn ("{0} error(s) in 24 h; newest {1}: {2}" -f $rjF[0], $rjF[1], $rjF[2]); $script:Faults++
  }
} else {
  Write-Ob 'research_jobs' warn "not queried ($DbContainer is not running)"; $script:Faults++
}

# ---- 2. MCP server + the stale-pool guard (today's failure mode) -----------
$mcpUp = Confirm-ObContainer 'openbrain-mcp' -Critical
if ($dbUp -and $mcpUp) {
  $dbStarted  = Get-CStartedAt 'openbrain-db'
  $mcpStarted = Get-CStartedAt 'openbrain-mcp'
  if ($dbStarted -and $mcpStarted -and ($dbStarted -gt $mcpStarted)) {
    # DB came up AFTER mcp -> mcp's pooled connection is stale -> broken-pipe 500s.
    Write-Ob 'openbrain-mcp' warn "STALE DB POOL: db started $dbStarted > mcp $mcpStarted"
    if ($Repair) {
      Write-Ob 'openbrain-mcp' fix 'docker restart openbrain-mcp (re-open DB connection)'
      docker restart openbrain-mcp 2>&1 | Out-Null
      Start-Sleep 4
      $mcpStarted2 = Get-CStartedAt 'openbrain-mcp'
      if ((Get-CState 'openbrain-mcp') -eq 'running' -and $mcpStarted2 -gt $dbStarted) {
        Write-Ob 'openbrain-mcp' ok 'restarted; connection now fresher than db'
      } else {
        Write-Ob 'openbrain-mcp' down 'restart did not resolve stale pool'; $script:Faults++
      }
    } else {
      Write-Ob 'openbrain-mcp' warn 'run with -Repair to restart (fixes OWUI 500 / broken-pipe)'
      $script:Faults++
    }
  } else {
    Write-Ob 'openbrain-mcp' ok 'DB connection fresher than db restart (no stale pool)'
  }
}

# ---- 3. The Open WebUI tool bridge -----------------------------------------
Confirm-ObContainer 'openbrain-mcpo'     | Out-Null
Confirm-ObContainer 'openbrain-mcpo-ext' | Out-Null
# ... and the CPU-spin guard for both (green-but-spinning; rule above Invoke-CpuSpinGuard).
Invoke-CpuSpinGuard -Names $CpuSpinContainers

# ---- 4. Functional probes on host-published endpoints (no secret needed) ----
# openbrain-research /health does a live `SELECT 1` and returns 503 when its
# Postgres pool has gone stale after an openbrain-db restart -- the SAME
# "green but functionally dead" stale-pool class as openbrain-mcp above. It
# surfaces to callers as a research 500 (the OWUI deep_research tool reports
# "Research engine error polling job: 500") while web search/SearXNG is fine.
# The 503 is a direct functional signal, so we probe /health rather than
# compare StartedAt. See memory: openbrain-mcp-stale-db-connection.
if ((Get-CState 'openbrain-research') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:8818/health' 5 '"db":true') {
    Write-Ob 'openbrain-research' ok '/health db ok (:8818)'
  } else {
    Write-Ob 'openbrain-research' warn 'STALE DB POOL: /health not db-ok on :8818'
    if ($Repair) {
      Write-Ob 'openbrain-research' fix 'docker restart openbrain-research (re-open DB pool)'
      docker restart openbrain-research 2>&1 | Out-Null
      Start-Sleep 5
      if (Test-HttpOk 'http://127.0.0.1:8818/health' 5 '"db":true') { Write-Ob 'openbrain-research' ok '/health recovered' }
      else { Write-Ob 'openbrain-research' down '/health still failing'; $script:Faults++ }
    } else {
      Write-Ob 'openbrain-research' warn 'run with -Repair to restart (fixes research 500 / stale pool)'
      $script:Faults++
    }
  }
} else {
  Confirm-ObContainer 'openbrain-research' | Out-Null
}

# openbrain-curator /health is the same shape as research's (`SELECT 1` through
# the ResilientPool -> {"ok","db"}, 503 when the pool is dead) and it publishes
# 127.0.0.1:8816 unauthenticated. It was absent from this script entirely while
# it crash-looped for 14 h on 2026-09-05 ("Module not found file:///app/pool.ts",
# an image built without a module index.ts imports) -- the operator learned of
# it from an unrelated disk check. A looping container is never `running`, so
# the Confirm-ObContainer branch catches that case; the /health branch catches a
# running curator whose DB pool has gone stale.
if ((Get-CState 'openbrain-curator') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:8816/health' 5 '"db":true') {
    Write-Ob 'openbrain-curator' ok '/health db ok (:8816)'
  } else {
    Write-Ob 'openbrain-curator' warn 'STALE DB POOL: /health not db-ok on :8816'
    if ($Repair) {
      Write-Ob 'openbrain-curator' fix 'docker restart openbrain-curator (re-open DB pool)'
      docker restart openbrain-curator 2>&1 | Out-Null
      Start-Sleep 5
      if (Test-HttpOk 'http://127.0.0.1:8816/health' 5 '"db":true') { Write-Ob 'openbrain-curator' ok '/health recovered' }
      else { Write-Ob 'openbrain-curator' down '/health still failing'; $script:Faults++ }
    } else {
      Write-Ob 'openbrain-curator' warn 'run with -Repair to restart (fixes research ingest 5xx / stale pool)'
      $script:Faults++
    }
  }
} else {
  Confirm-ObContainer 'openbrain-curator' | Out-Null
}

# Gateway /health is the privacy proxy Claude/cloud clients reach at :8061.
if ((Get-CState 'openbrain-gateway') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:8061/health' 5 'ok') {
    Write-Ob 'openbrain-gateway' ok '/health == ok (:8061)'
  } else {
    Write-Ob 'openbrain-gateway' warn '/health not OK on :8061'
    if ($Repair) {
      Write-Ob 'openbrain-gateway' fix 'docker restart openbrain-gateway'
      docker restart openbrain-gateway 2>&1 | Out-Null
      Start-Sleep 3
      if (Test-HttpOk 'http://127.0.0.1:8061/health' 5 'ok') { Write-Ob 'openbrain-gateway' ok '/health recovered' }
      else { Write-Ob 'openbrain-gateway' down '/health still failing'; $script:Faults++ }
    } else { $script:Faults++ }
  }
} else {
  Confirm-ObContainer 'openbrain-gateway' | Out-Null
}

# PostgREST proxy reachable (exercises the DB read path independently of mcp).
if ((Get-CState 'openbrain-rest') -eq 'running') {
  if (Test-HttpOk 'http://127.0.0.1:3001/' 5) { Write-Ob 'openbrain-rest' ok 'PostgREST proxy reachable (:3001)' }
  else { Write-Ob 'openbrain-rest' warn 'PostgREST proxy not answering on :3001'; $script:Faults++ }
} else {
  Confirm-ObContainer 'openbrain-rest' | Out-Null
}

# ---- 5. Remaining OB containers — liveness only ----------------------------
foreach ($svc in @('openbrain-postgrest','openbrain-wiki','openbrain-wiki-viewer','openbrain-entity-worker')) {
  Confirm-ObContainer $svc | Out-Null
}

# ---- 6. Idea Refinery drain (profile-gated 'idea-refinery'; nightly batch) --
# Liveness only: no host port (obnet-internal) so no functional /health probe from
# the host, and it's idle between the 03:00-UTC cron fire + on-demand /run. States:
#   running -> ok; exited/created/paused -> fault (repaired via `docker start`);
#   absent  -> WARN not a hard fault (a profile-gated service is legitimately not
#              present on a stack brought up without --profile idea-refinery).
$irState = Get-CState 'openbrain-idea-refinery'
if ($irState -eq 'running') {
  Write-Ob 'openbrain-idea-refinery' ok 'running (drain; nightly 03:00 UTC + on-demand)'
} elseif ($irState -eq 'absent') {
  Write-Ob 'openbrain-idea-refinery' warn 'not present -- enable: docker compose -f OB1/docker/docker-compose.yml --profile idea-refinery up -d openbrain-idea-refinery'
} else {
  Confirm-ObContainer 'openbrain-idea-refinery' | Out-Null
}

# ---- 7. Household pantry service (profile-gated 'pantry'; pantry-wire) -----
# OFF by default: the container exists only after `stack.py enable pantry` + the
# operator's deploy steps, so ABSENT is a quiet skip - not a WARN, not a fault - and
# the stack-health line must not change on a host that never turned it on. When it IS
# there it is checked like research/curator: it publishes NO host port (obnet +
# ai-stack_llm-net only), so /health is read INSIDE the container with the same deno
# one-liner its compose healthcheck uses; the service answers {"ok":true,"db":true}
# (503 + db:false when its pool is dead, the stale-pool shape after an openbrain-db
# restart). Repair is a restart, which re-opens the pool.
$pantryProbe = "const r = await fetch('http://127.0.0.1:8000/health'); const j = await r.json(); Deno.exit(r.ok && j.db === true ? 0 : 1)"
$pantryState = Get-CState 'openbrain-pantry'
if ($pantryState -eq 'absent') {
  if (-not $Quiet) { Write-Ob 'openbrain-pantry' ok 'not deployed (profile pantry is off) -- skipped' }
} elseif ($pantryState -eq 'running') {
  docker exec openbrain-pantry deno eval $pantryProbe 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) {
    Write-Ob 'openbrain-pantry' ok '/health db ok (in-container; no host port)'
  } else {
    Write-Ob 'openbrain-pantry' warn 'STALE DB POOL or service down: /health not db-ok'
    if ($Repair) {
      Write-Ob 'openbrain-pantry' fix 'docker restart openbrain-pantry (re-open DB pool)'
      docker restart openbrain-pantry 2>&1 | Out-Null
      Start-Sleep 8
      docker exec openbrain-pantry deno eval $pantryProbe 2>$null | Out-Null
      if ($LASTEXITCODE -eq 0) { Write-Ob 'openbrain-pantry' ok '/health recovered' }
      else { Write-Ob 'openbrain-pantry' down '/health still failing'; $script:Faults++ }
    } else {
      Write-Ob 'openbrain-pantry' warn 'run with -Repair to restart (fixes pantry 5xx / stale pool)'
      $script:Faults++
    }
  }
} else {
  Confirm-ObContainer 'openbrain-pantry' | Out-Null
}

Write-Host ""
if ($script:Faults -eq 0) {
  Write-Host "==> Open Brain: all checks passed" -ForegroundColor Green
  exit 0
} else {
  $hint = if ($Repair) { 'some faults unresolved' } else { 're-run with -Repair to auto-fix' }
  Write-Host ("==> Open Brain: {0} fault(s) -- {1}" -f $script:Faults, $hint) -ForegroundColor Yellow
  exit 1
}
