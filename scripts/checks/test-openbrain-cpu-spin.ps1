# test-openbrain-cpu-spin.ps1 - the test for check-openbrain-health.ps1's CPU-spin
# guard (openbrain-mcpo / openbrain-mcpo-ext). Item ef-mcpo-spin, 2026-10-05.
#
# NOTHING HERE TOUCHES THE HOST DAEMON OR SENDS AN ALERT:
#   - -Script is COPIED into a temp sandbox tree (<sbx>\scripts\checks\), so its
#     repo root - the state file under logs\ and the alert senders under .venv\ -
#     is the sandbox, never the repo.
#   - docker is a compiled STUB (<sbx>\stubs\docker.exe), first on PATH. It answers
#     inspect / exec cat cpu.stat / restart from <sbx>\stubs\scenario.txt with
#     SYNTHETIC cumulative CPU counters, and appends every call to
#     <sbx>\stubs\calls.log. Anything else it is asked exits 1.
#   - DOCKER_HOST=tcp://127.0.0.1:1 in every child: should the stub ever not be
#     the docker that runs, the real CLI fails closed against a dead endpoint.
#   - the alert senders are faked at the leaf: <sbx>\.venv\Scripts\python.exe is a
#     compiled recorder that appends "<script> | <text>" to <sbx>\alerts.log and
#     exits 0. The real mm_post.py / telegram_notify.py are never on its path.
#
# Time is simulated, not waited: between two runs the harness moves every
# timestamp in the sandbox state file back by the step (default 10 min, the
# \StackWatchdog cadence) and advances each container's synthetic counter by
# <pct> of one core over that step.
#
# Usage (from the repo root):
#   powershell -NoProfile -File scripts\checks\test-openbrain-cpu-spin.ps1
#   ... -Script <another check-openbrain-health.ps1>   (e.g. the base, for RED)
#   ... -Mutants      also builds the four mutants (window, hourly cap, allow-list,
#                     no-data-no-action) from -Script and expects each to fail >=1 case
# Exit code = number of failed cases (with -Mutants: + surviving mutants).

[CmdletBinding()]
param(
  [string]$Script = '',
  [string]$Watchdog = '',
  [switch]$Mutants,
  [string[]]$Cases = @(),
  [switch]$Keep
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path $here 'check-openbrain-health.ps1' }
if (-not $Watchdog) { $Watchdog = Join-Path $here 'stack-watchdog.ps1' }
$Script = (Resolve-Path $Script).Path
$Watchdog = (Resolve-Path $Watchdog).Path
$Allow = @('openbrain-mcpo', 'openbrain-mcpo-ext')
$Cases = @($Cases | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })   # -File passes 'C1,C3' as one string

# ---------------------------------------------------------------- the stubs --
$StubSrc = @'
using System; using System.IO; using System.Collections.Generic; using System.Threading;
public static class ObSpinStubDocker__SFX__ {
  static string Dir { get { return AppDomain.CurrentDomain.BaseDirectory; } }
  static Dictionary<string,string> Read() {
    var d = new Dictionary<string,string>();
    string f = Path.Combine(Dir, "scenario.txt");
    if (File.Exists(f)) foreach (var l in File.ReadAllLines(f)) {
      int i = l.IndexOf('='); if (i > 0) d[l.Substring(0, i).Trim()] = l.Substring(i + 1).Trim(); }
    return d;
  }
  static string Get(Dictionary<string,string> d, string k, string def) { string v; return d.TryGetValue(k, out v) ? v : def; }
  public static int Main(string[] a) {
    File.AppendAllText(Path.Combine(Dir, "calls.log"), string.Join(" ", a) + "\n");
    var sc = Read();
    if (a.Length == 0) return 1;
    string name = a[a.Length - 1];
    if (a[0] == "inspect") {
      string fmt = "";
      for (int i = 0; i < a.Length - 1; i++) if (a[i] == "--format" || a[i] == "-f") fmt = a[i + 1];
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error: No such object: " + name); return 1; }
      string mode = Get(sc, name + ".inspect", "ok");
      if (mode == "timeout") { Thread.Sleep(600000); return 0; }
      if (mode == "error") { Console.Error.WriteLine("Error response from daemon: stub inspect failure"); return 1; }
      string gen = Get(sc, name + ".gen", "1");
      Console.WriteLine(fmt.Replace("{{.State.Status}}", sc[name + ".status"]).Replace("{{.Id}}", "stubid-" + name)
        .Replace("{{.State.StartedAt}}", "2026-10-05T00:00:00." + gen.PadLeft(9, '0') + "Z"));
      return 0;
    }
    if (a[0] == "exec" && a.Length >= 3) {
      name = a[1];
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error response from daemon: No such container: " + name); return 1; }
      string mode = Get(sc, name + ".exec", "ok");
      if (mode == "timeout") { Thread.Sleep(600000); return 0; }
      if (mode == "error") { Console.Error.WriteLine("Error response from daemon: stub exec failure"); return 1; }
      if (mode == "overflow") { Console.WriteLine("usage_usec 99999999999999999999999"); return 0; }
      if (a[a.Length - 1] == "/sys/fs/cgroup/cpu.stat") {
        string u = Get(sc, name + ".usec", "0");
        Console.WriteLine("usage_usec " + u); Console.WriteLine("user_usec " + u); Console.WriteLine("system_usec 0");
        return 0;
      }
      Console.Error.WriteLine("stub docker: exec not supported"); return 1;
    }
    if (a[0] == "restart") {
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error: No such container: " + name); return 1; }
      if (Get(sc, name + ".restart", "ok") == "error") { Console.Error.WriteLine("Error response from daemon: stub restart failure"); return 1; }
      int g = int.Parse(Get(sc, name + ".gen", "1")) + 1;
      File.AppendAllText(Path.Combine(Dir, "scenario.txt"), name + ".gen=" + g + "\n" + name + ".usec=0\n");
      Console.WriteLine(name); return 0;
    }
    Console.Error.WriteLine("stub docker: unsupported: " + string.Join(" ", a)); return 1;
  }
}
'@
$RecorderSrc = @'
using System; using System.IO;
public static class ObSpinRecorder__SFX__ { public static int Main(string[] a) {
  string f = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "alerts.log"));
  File.AppendAllText(f, (a.Length > 0 ? Path.GetFileName(a[0]) : "") + " | " + (a.Length > 1 ? a[1] : "") + "\n"); return 0; } }
'@
$StubCache = Join-Path ([IO.Path]::GetTempPath()) ("obspin-bin-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $StubCache -Force | Out-Null
function New-Exe([string]$Src, [string]$Out) {
  Add-Type -TypeDefinition ($Src.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))) -OutputAssembly $Out -OutputType ConsoleApplication
}
New-Exe $StubSrc (Join-Path $StubCache 'docker.exe')
New-Exe $RecorderSrc (Join-Path $StubCache 'python.exe')

function New-Sandbox([string]$ScriptPath) {
  $root = Join-Path ([IO.Path]::GetTempPath()) ("obspin-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  foreach ($d in @('logs', 'scripts\checks', 'scripts\sysadmin-mcp', '.venv\Scripts', 'stubs')) {
    New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
  }
  Copy-Item -LiteralPath $ScriptPath -Destination (Join-Path $root 'scripts\checks\check-openbrain-health.ps1')
  Copy-Item (Join-Path $StubCache 'docker.exe') (Join-Path $root 'stubs\docker.exe')
  Copy-Item (Join-Path $StubCache 'python.exe') (Join-Path $root '.venv\Scripts\python.exe')
  foreach ($f in @('mm_post.py', 'telegram_notify.py')) {
    [IO.File]::WriteAllText((Join-Path $root "scripts\sysadmin-mcp\$f"), "# fake - never run`n")
  }
  return $root
}

function Read-Scenario([string]$Root) {
  $h = [ordered]@{}
  $f = Join-Path $Root 'stubs\scenario.txt'
  if (Test-Path $f) { foreach ($l in Get-Content $f) { $i = $l.IndexOf('='); if ($i -gt 0) { $h[$l.Substring(0, $i)] = $l.Substring($i + 1) } } }
  return $h
}
function Write-Scenario([string]$Root, $H) {
  $lines = @($H.Keys | ForEach-Object { "$_=$($H[$_])" })
  [IO.File]::WriteAllText((Join-Path $Root 'stubs\scenario.txt'), (($lines -join "`n") + "`n"))
}

# Move every timestamp in the sandbox state file back by $Minutes (= time passing).
function Move-StateClock([string]$Root, [int]$Minutes) {
  $p = Join-Path $Root 'logs\.openbrain-cpu-spin-state.json'
  if (-not (Test-Path $p)) { return }
  $j = Get-Content $p -Raw | ConvertFrom-Json
  foreach ($c in $j.PSObject.Properties) {
    foreach ($k in @('at', 'hot_since', 'last_restart', 'last_hold_alert', 'last_restart_alert')) {
      $v = $c.Value.$k
      if ($v) {
        # an unparseable value (a case that corrupts it on purpose) is left as it is
        try { $t = [DateTime]::Parse([string]$v, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime() } catch { continue }
        $c.Value.$k = $t.AddMinutes(-$Minutes).ToString('o')
      }
    }
  }
  [IO.File]::WriteAllText($p, ($j | ConvertTo-Json -Depth 4))
}

# A child powershell.exe with a hard bound (a hung pass is a FAIL, never a hang).
function Invoke-Child([string[]]$ArgList, [int]$TimeoutSeconds = 180) {
  $q = @($ArgList | ForEach-Object { if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ } })
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = 'powershell.exe'; $psi.Arguments = ($q -join ' ')
  $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
  $pr = [System.Diagnostics.Process]::Start($psi)
  $o = $pr.StandardOutput.ReadToEndAsync(); $e = $pr.StandardError.ReadToEndAsync()
  if (-not $pr.WaitForExit($TimeoutSeconds * 1000)) {
    & taskkill.exe /PID $pr.Id /T /F 2>$null | Out-Null
    return "CHILD TIMED OUT after ${TimeoutSeconds}s"
  }
  $pr.WaitForExit()
  return ($o.Result + $e.Result)
}

# One simulated pass. $Pct: container -> % of one core over the step (only
# running containers listed in the scenario). $Set: scenario overrides for THIS
# step only (e.g. 'openbrain-mcpo-ext.exec' = 'timeout').
function Invoke-Pass {
  param($Ctx, [hashtable]$Pct = @{}, [int]$Minutes = 10, [hashtable]$Set = @{}, [string[]]$ExtraArgs = @(), [switch]$NoRepair, [switch]$ViaWatchdog)
  $root = $Ctx.Root
  $sc = Read-Scenario $root
  foreach ($k in @($sc.Keys)) { if ($k -match '\.(exec|inspect|restart)$') { $sc.Remove($k) } }
  if ($Ctx.Passes -gt 0 -and $Minutes -gt 0) {
    Move-StateClock $root $Minutes
    foreach ($n in $Pct.Keys) {
      $u = [int64]0; if ($sc.Contains("$n.usec")) { $u = [int64]$sc["$n.usec"] }
      $sc["$n.usec"] = [string]([int64]($u + ($Pct[$n] / 100.0) * $Minutes * 60 * 1000000))
    }
  }
  foreach ($k in $Set.Keys) { $sc[$k] = $Set[$k] }
  Write-Scenario $root $sc
  $Ctx.Passes++

  $log = Join-Path $root 'logs\ob.log'
  $oldPath = $env:PATH; $oldHost = $env:DOCKER_HOST
  $env:PATH = (Join-Path $root 'stubs') + ';' + $env:PATH
  $env:DOCKER_HOST = 'tcp://127.0.0.1:1'
  try {
    $resolved = (Get-Command docker -CommandType Application | Select-Object -First 1).Source
    if ($resolved -ne (Join-Path $root 'stubs\docker.exe')) { throw "FAIL-CLOSED: docker resolves to $resolved, not the stub" }
    $target = Join-Path $root 'scripts\checks\check-openbrain-health.ps1'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    if ($ViaWatchdog) {
      $runner = Join-Path $root 'wd-runner.ps1'
      $r = @"
`$ErrorActionPreference = 'Stop'
`$src = [IO.File]::ReadAllText('$($Watchdog -replace "'", "''")')
`$ast = [System.Management.Automation.Language.Parser]::ParseInput(`$src, [ref]`$null, [ref]`$null)
`$fn = `$ast.FindAll({ `$args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and `$args[0].Name -eq 'Invoke-OpenBrainHealth' }, `$true) | Select-Object -First 1
if (-not `$fn) { Write-Output 'NO Invoke-OpenBrainHealth in the watchdog'; exit 9 }
`$SCRIPT_DIR = '$((Join-Path $root 'scripts\checks') -replace "'", "''")'
`$LOG_FILE = '$($log -replace "'", "''")'
function Write-LogEntry { param([string]`$Message, [string]`$Level = 'INFO') Add-Content -Path `$LOG_FILE -Value "WD [`$Level] `$Message" }
. ([scriptblock]::Create(`$fn.Extent.Text))
Invoke-OpenBrainHealth
"@
      [IO.File]::WriteAllText($runner, $r)
      $out = Invoke-Child @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner)
    } else {
      $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $target)
      if (-not $NoRepair) { $a += '-Repair' }
      $a += @('-LogPath', $log)
      if ((Get-Content $target -Raw) -match 'DockerTimeoutSeconds') { $a += @('-DockerTimeoutSeconds', '2') }
      $a += $ExtraArgs
      $out = Invoke-Child $a
    }
    $sw.Stop()
    $Ctx.Out += $out
    $Ctx.Seconds = [Math]::Max($Ctx.Seconds, $sw.Elapsed.TotalSeconds)
  } finally {
    $env:PATH = $oldPath
    if ($null -eq $oldHost) { Remove-Item Env:\DOCKER_HOST -ErrorAction SilentlyContinue } else { $env:DOCKER_HOST = $oldHost }
  }
}

function New-Ctx([string]$ScriptPath, [hashtable]$Running) {
  $root = New-Sandbox $ScriptPath
  $sc = [ordered]@{}
  foreach ($n in $Running.Keys) { $sc["$n.status"] = $Running[$n]; $sc["$n.usec"] = '1000000'; $sc["$n.gen"] = '1' }
  Write-Scenario $root $sc
  return [pscustomobject]@{ Root = $root; Passes = 0; Out = ''; Seconds = 0.0 }
}

function Get-Facts($Ctx) {
  $calls = @(); $cf = Join-Path $Ctx.Root 'stubs\calls.log'
  if (Test-Path $cf) { $calls = @(Get-Content $cf | Where-Object { $_ }) }
  $alerts = @(); $af = Join-Path $Ctx.Root 'alerts.log'
  if (Test-Path $af) { $alerts = @(Get-Content $af | Where-Object { $_ }) }
  $log = @(); $lf = Join-Path $Ctx.Root 'logs\ob.log'
  if (Test-Path $lf) { $log = @(Get-Content $lf | Where-Object { $_ }) }
  $mutating = @($calls | Where-Object { $_ -match '^(restart|start|stop|kill|rm|pause|unpause|update|compose|run|create)\b' })
  $foreign = @($mutating | Where-Object { $Allow -cnotcontains (($_ -split ' ')[-1]) })
  return [pscustomobject]@{
    Calls = $calls; Alerts = $alerts; Log = $log; Mutating = $mutating; Foreign = $foreign
    Restarts = @($calls | Where-Object { $_ -match '^restart ' })
  }
}

$script:Failures = 0
$script:Results = @()
function Write-Case([string]$Id, [string]$Title, [bool]$Pass, [string]$Detail) {
  if (-not $Pass) { $script:Failures++ }
  $script:Results += "$Id $(if ($Pass) { 'PASS' } else { 'FAIL' })"
  Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $(if ($Pass) { 'PASS' } else { 'FAIL' }))
  if ($Detail) { foreach ($l in ($Detail -split "`n")) { Write-Host "    $l" } }
}
function Show($F) {
  return ("restarts: [{0}]`nmutating calls: [{1}]`nalerts ({2}): {3}`nguard log lines:`n  {4}" -f `
      ($F.Restarts -join '; '), ($F.Mutating -join '; '), $F.Alerts.Count, ($F.Alerts -join ' || '),
      ((@($F.Log | Where-Object { $_ -match ' cpu |cpu-spin|\] openbrain-mcpo(-ext)? running' })) -join "`n  "))
}
function Want([string]$Id) { return ($Cases.Count -eq 0 -or $Cases -contains $Id) }

$Both = @{ 'openbrain-mcpo' = 'running'; 'openbrain-mcpo-ext' = 'running' }

function Invoke-Suite([string]$ScriptPath) {
  $script:Failures = 0; $script:Results = @()
  $made = @()

  if (Want 'C1') {
    # Sustained ~100% on mcpo-ext: baseline, then 10-min passes. Hot streak reaches
    # the 30-min window on pass 3 -> exactly one restart, one FIX line, one alert;
    # then the restarted container re-baselines and idles.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100; 'openbrain-mcpo' = 0.2 } }
    foreach ($i in 1..2) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 0.2; 'openbrain-mcpo' = 0.2 } }
    $f = Get-Facts $c
    $fix = @($f.Log | Where-Object { $_ -match '\[FIX\].*docker restart openbrain-mcpo-ext' })
    $ok = ($f.Restarts.Count -eq 1 -and $f.Restarts[0] -ceq 'restart openbrain-mcpo-ext' -and $fix.Count -eq 1 -and
      $f.Alerts.Count -eq 1 -and $f.Alerts[0] -match '^mm_post\.py \| openbrain: CPU SPIN on openbrain-mcpo-ext' -and $f.Foreign.Count -eq 0)
    Write-Case 'C1' 'sustained spin on mcpo-ext: exactly one restart, one log line, one alert' $ok (Show $f)
  }

  if (Want 'C2') {
    # A burst shorter than the window, twice, with a cool pass between: never restarted.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    $seq = @(100, 100, 0.2, 100, 100, 0.2)
    foreach ($p in $seq) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = $p; 'openbrain-mcpo' = 0.2 } }
    $f = Get-Facts $c
    $watch = @($f.Log | Where-Object { $_ -match 'hot for 20 min' })
    $ok = ($f.Restarts.Count -eq 0 -and $f.Alerts.Count -eq 0 -and $watch.Count -ge 2 -and $f.Foreign.Count -eq 0)
    Write-Case 'C2' 'bursts of 20 min (< 30-min window) separated by a cool pass: no restart, no alert' $ok (Show $f)
  }

  if (Want 'C3') {
    # Spin -> restart (pass 3); it spins again right away: the second window
    # completes 40 min after the restart -> alert only, then 50 min -> nothing new.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } }
    Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 }          # post-restart baseline (t+40)
    foreach ($i in 1..4) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } }  # t+50..t+80
    $f = Get-Facts $c
    $alertOnly = @($f.Log | Where-Object { $_ -match 'within the hour of the last auto-restart - alert only|back within the hour of the last auto-restart' })
    $ok = ($f.Restarts.Count -eq 1 -and $f.Alerts.Count -eq 2 -and $f.Alerts[1] -match 'alert only, no restart' -and
      $alertOnly.Count -ge 2 -and $f.Foreign.Count -eq 0)
    Write-Case 'C3' 'spin back within the hour: alert only (once), no second restart' $ok (Show $f)
  }

  if (Want 'C4') {
    # Hot for 20 min, then the counter read TIMES OUT on the pass that would
    # complete the window, and again; then docker inspect errors. No restart,
    # NO DATA lines, each pass bounded.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    foreach ($i in 1..2) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } }
    Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } -Set @{ 'openbrain-mcpo-ext.exec' = 'timeout' }
    Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } -Set @{ 'openbrain-mcpo-ext.exec' = 'error' }
    Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } -Set @{ 'openbrain-mcpo-ext.inspect' = 'timeout' }
    $f = Get-Facts $c
    $nd = @($f.Log | Where-Object { $_ -match 'openbrain-mcpo-ext cpu NO DATA .* - no action' })
    # no restart AND no start / stop / anything: Mutating is every mutating verb the stub saw
    $ok = ($f.Mutating.Count -eq 0 -and $f.Alerts.Count -eq 0 -and $nd.Count -eq 3 -and $c.Seconds -lt 60)
    Write-Case 'C4' 'docker timeout / error: no restart and no start, a clear NO DATA line, bounded' $ok ((Show $f) + "`nslowest pass: {0:N1}s" -f $c.Seconds)
  }

  if (Want 'C5') {
    # openbrain-mcpo gets the same rule.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo' = 100; 'openbrain-mcpo-ext' = 0.2 } }
    $f = Get-Facts $c
    $ok = ($f.Restarts.Count -eq 1 -and $f.Restarts[0] -ceq 'restart openbrain-mcpo' -and $f.Alerts.Count -eq 1 -and $f.Foreign.Count -eq 0)
    Write-Case 'C5' 'sustained spin on openbrain-mcpo: exactly one restart of it' $ok (Show $f)
  }

  if (Want 'C6') {
    # A measured name that is NOT on the restart allow-list spins: alert only.
    $c = New-Ctx $ScriptPath @{ 'openbrain-mcpo' = 'running'; 'openbrain-mcpo-ext' = 'running'; 'openwebui' = 'running' }; $made += $c
    $x = @('-CpuSpinContainers', 'openbrain-mcpo-ext,openwebui')
    Invoke-Pass $c -ExtraArgs $x
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openwebui' = 100; 'openbrain-mcpo-ext' = 0.2 } -ExtraArgs $x }
    $f = Get-Facts $c
    $al = @($f.Log | Where-Object { $_ -match 'openwebui cpu .*not on the restart allow-list - alert only' })
    $ok = ($f.Restarts.Count -eq 0 -and $f.Mutating.Count -eq 0 -and $al.Count -eq 1 -and $f.Alerts.Count -eq 1)
    Write-Case 'C6' 'allow-list: a spinning openwebui is never restarted (alert only)' $ok (Show $f)
  }

  if (Want 'C7') {
    # Without -Repair: report + fault, no restart, no alert.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c -NoRepair
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } -NoRepair }
    $f = Get-Facts $c
    $w = @($f.Log | Where-Object { $_ -match 'CPU SPIN on openbrain-mcpo-ext.*run with -Repair' })
    $ok = ($f.Restarts.Count -eq 0 -and $f.Alerts.Count -eq 0 -and $w.Count -eq 1)
    Write-Case 'C7' 'no -Repair: the spin is reported, nothing restarted or alerted' $ok (Show $f)
  }

  if (Want 'C8') {
    # The scheduled path: stack-watchdog.ps1's own Invoke-OpenBrainHealth (lifted
    # from the file by AST, run against the sandbox copy) passes -Repair, so a
    # sustained spin restarts mcpo-ext through it.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c -ViaWatchdog
    foreach ($i in 1..3) { Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = 100 } -ViaWatchdog }
    $f = Get-Facts $c
    $wdSrc = Get-Content $Watchdog -Raw
    $called = ($wdSrc -match '(?m)^\s*Invoke-OpenBrainHealth\s*$') -and ($wdSrc -match "& \`$obScript -Repair")
    $ok = ($called -and $f.Restarts.Count -eq 1 -and $f.Restarts[0] -ceq 'restart openbrain-mcpo-ext' -and $f.Alerts.Count -eq 1 -and $f.Foreign.Count -eq 0)
    Write-Case 'C8' 'scheduled path: the watchdog''s Invoke-OpenBrainHealth runs the guard with -Repair' $ok ((Show $f) + "`nwatchdog calls Invoke-OpenBrainHealth with -Repair: $called")
  }

  # ---- attempt 2 (tester findings A3, A3d, A12, A13, A4/A5) ---------------------
  $X = 'openbrain-mcpo-ext'
  $HotX = @{ 'openbrain-mcpo-ext' = 100; 'openbrain-mcpo' = 0.2 }

  if (Want 'C9') {
    # A3: ONE long interval (missed passes) never completes the window, at any
    # length, and a 7-min 400% burst inside a 31-min gap (avg ~90%) is not a
    # spin. The guard is not blinded: three ordinary hot passes later it acts.
    $det = @(); $okAll = $true
    foreach ($gap in @(31, 45, 300)) {
      $c = New-Ctx $ScriptPath $Both; $made += $c
      Invoke-Pass $c; Invoke-Pass $c -Pct $HotX -Minutes $gap
      $f = Get-Facts $c
      $bound = @($f.Log | Where-Object { $_ -match "$X cpu .*over the 20-min bound - not counted" })
      $r1 = $f.Restarts.Count
      foreach ($i in 1..3) { Invoke-Pass $c -Pct $HotX }
      $r2 = (Get-Facts $c).Restarts.Count
      $ok = ($r1 -eq 0 -and $bound.Count -eq 1 -and $r2 -eq 1)
      if (-not $ok) { $okAll = $false }
      $det += "gap ${gap} min at 100%: restarts after the gap $r1 (want 0), bound line $($bound.Count) (want 1), after 3 more hot passes $r2 (want 1)"
    }
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct @{ 'openbrain-mcpo-ext' = (400.0 * 7 / 31) } -Minutes 31
    $f = Get-Facts $c
    if ($f.Restarts.Count -ne 0 -or $f.Alerts.Count -ne 0) { $okAll = $false }
    $det += "burst 7 min x 400% in one 31-min interval (avg {0:N1}%): restarts $($f.Restarts.Count), alerts $($f.Alerts.Count) (want 0, 0)" -f (400.0 * 7 / 31)
    Write-Case 'C9' 'one long interval (31 / 45 / 300 min, or a burst inside one) never completes the window' $okAll ($det -join "`n")
  }

  if (Want 'C10') {
    # A3d: a NO DATA streak (state not advanced) then ONE good hot read spanning
    # 40 min: re-baselined, not a spin. Then a real spin is still caught.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c
    foreach ($i in 1..3) { Invoke-Pass $c -Pct $HotX -Set @{ "$X.exec" = 'error' } }
    Invoke-Pass $c -Pct $HotX
    $f = Get-Facts $c; $r1 = $f.Restarts.Count
    $bound = @($f.Log | Where-Object { $_ -match "$X cpu .*over the 20-min bound - not counted" })
    foreach ($i in 1..3) { Invoke-Pass $c -Pct $HotX }
    $f = Get-Facts $c
    $ok = ($r1 -eq 0 -and $bound.Count -eq 1 -and $f.Restarts.Count -eq 1 -and $f.Foreign.Count -eq 0)
    Write-Case 'C10' 'NO DATA x3 then one 40-min hot read: no restart; a later real spin still is' $ok ("restarts after the long read: $r1 (want 0), bound lines $($bound.Count)`n" + (Show $f))
  }

  if (Want 'C11') {
    $det = @(); $okAll = $true
    # (a) three hot intervals of 25 min (each over the bound): never counted
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; foreach ($i in 1..3) { Invoke-Pass $c -Pct $HotX -Minutes 25 }
    $a = (Get-Facts $c).Restarts.Count; if ($a -ne 0) { $okAll = $false }
    $det += "(a) 3 hot intervals of 25 min: restarts $a (want 0)"
    # (b) two hot intervals of 19 min (38 min, under the bound): too few intervals
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; foreach ($i in 1..2) { Invoke-Pass $c -Pct $HotX -Minutes 19 }
    $b = (Get-Facts $c).Restarts.Count
    Invoke-Pass $c -Pct $HotX -Minutes 19
    $b3 = (Get-Facts $c).Restarts.Count
    if ($b -ne 0 -or $b3 -ne 1) { $okAll = $false }
    $det += "(b) 2 hot intervals of 19 min: restarts $b (want 0); a third: $b3 (want 1)"
    # (c) 5-min intervals (manual runs between scheduled ones): 5 hot = 25 min, not
    #     yet the window; the 6th makes 30 min
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; foreach ($i in 1..5) { Invoke-Pass $c -Pct $HotX -Minutes 5 }
    $c5 = (Get-Facts $c).Restarts.Count
    Invoke-Pass $c -Pct $HotX -Minutes 5
    $c6 = (Get-Facts $c).Restarts.Count
    if ($c5 -ne 0 -or $c6 -ne 1) { $okAll = $false }
    $det += "(c) 5-min hot intervals: after 5 (25 min) restarts $c5 (want 0); after 6 (30 min) $c6 (want 1)"
    Write-Case 'C11' 'the window needs 3+ hot intervals, each <= 20 min, spanning 30+ min' $okAll ($det -join "`n")
  }

  if (Want 'C12') {
    # A12: docker restart FAILS. The attempt still counts toward the hourly cap:
    # the spin continuing inside the hour gets a hold alert, not a second attempt.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct $HotX; Invoke-Pass $c -Pct $HotX
    Invoke-Pass $c -Pct $HotX -Set @{ "$X.restart" = 'error' }
    foreach ($i in 1..4) { Invoke-Pass $c -Pct $HotX }
    $f = Get-Facts $c
    $failed = @($f.Log | Where-Object { $_ -match 'docker restart FAILED' })
    $hold = @($f.Log | Where-Object { $_ -match "$X cpu .*back within the hour of the last auto-restart" })
    $ok = ($f.Restarts.Count -eq 1 -and $failed.Count -eq 1 -and $hold.Count -ge 1 -and $f.Alerts.Count -eq 2)
    Write-Case 'C12' 'a failed docker restart counts toward the cap: no second attempt inside the hour' $ok (Show $f)
  }

  if (Want 'C13') {
    # A13 S3: docker inspect of mcpo-ext ERRORS. The liveness probe says NO DATA
    # (not "not present") and nothing - start, restart - is called.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c -Set @{ "$X.inspect" = 'error' }
    $f = Get-Facts $c
    $live = @($f.Log | Where-Object { $_ -match "\] $X NO DATA \(docker inspect .*- no action" })
    $absent = @($f.Log | Where-Object { $_ -match "\] $X not present" })
    $ok = ($live.Count -eq 1 -and $absent.Count -eq 0 -and $f.Mutating.Count -eq 0)
    Write-Case 'C13' 'docker inspect error on mcpo-ext: NO DATA, never "absent", never a docker start' $ok ((Show $f) + "`nliveness lines: " + ((@($f.Log | Where-Object { $_ -match "\] $X " })) -join ' / '))
  }

  if (Want 'C14') {
    $det = @(); $okAll = $true
    $sp = { param($Ctx, [string]$Field, $Value)
      $p = Join-Path $Ctx.Root 'logs\.openbrain-cpu-spin-state.json'
      $j = Get-Content $p -Raw | ConvertFrom-Json
      $j.$X | Add-Member -NotePropertyName $Field -NotePropertyValue $Value -Force
      [IO.File]::WriteAllText($p, ($j | ConvertTo-Json -Depth 4)) }
    # (a) `at` in year 9999: re-baselined at once (not blind), then a spin is caught
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; & $sp $c 'at' '9999-12-31T00:00:00.0000000Z'
    Invoke-Pass $c -Pct $HotX
    $fut = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu baseline recorded: a state timestamp is in the future" })
    foreach ($i in 1..3) { Invoke-Pass $c -Pct $HotX }
    $ra = (Get-Facts $c).Restarts.Count
    if ($fut.Count -ne 1 -or $ra -ne 1) { $okAll = $false }
    $det += "(a) at=9999: future line $($fut.Count) (want 1), restarts after 3 hot passes $ra (want 1)"
    # (b) hot_since a day ahead mid-streak: re-baselined
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct $HotX
    & $sp $c 'hot_since' ([DateTime]::UtcNow.AddDays(1).ToString('o'))
    Invoke-Pass $c -Pct $HotX
    $fb = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu baseline recorded: a state timestamp is in the future" })
    if ($fb.Count -ne 1) { $okAll = $false }
    $det += "(b) hot_since +1 day: future line $($fb.Count) (want 1)"
    # (c) usec "abc": a clean baseline, no cast error on any stream
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; & $sp $c 'usec' 'abc'
    Invoke-Pass $c -Pct $HotX
    $castErr = ($c.Out -match '(?i)cannot convert|InvalidCast')
    $bl = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu baseline recorded" })
    if ($castErr -or $bl.Count -ne 2) { $okAll = $false }
    $det += "(c) usec='abc': cast error printed $castErr (want False), baseline lines $($bl.Count) (want 2)"
    # (d) last_restart unreadable during a spin: read as "restarted now" - alert only
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct $HotX; Invoke-Pass $c -Pct $HotX
    & $sp $c 'last_restart' 'garbage'
    Invoke-Pass $c -Pct $HotX
    $fd = Get-Facts $c
    $ul = @($fd.Log | Where-Object { $_ -match "$X cpu last_restart 'garbage' is unreadable or in the future - read as restarted now" })
    $hd = @($fd.Log | Where-Object { $_ -match "$X cpu CPU SPIN .*alert only, no restart" })
    if ($fd.Restarts.Count -ne 0 -or $ul.Count -ne 1 -or $hd.Count -ne 1 -or $fd.Alerts.Count -ne 1) { $okAll = $false }
    $det += "(d) last_restart='garbage': restarts $($fd.Restarts.Count) (want 0), unreadable line $($ul.Count), hold line $($hd.Count), alerts $($fd.Alerts.Count) (want 1)"
    # (e) a usage_usec that overflows int64: NO DATA naming the value, not "no usage_usec line"
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct $HotX -Set @{ "$X.exec" = 'overflow' }
    $fe = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu NO DATA \(cpu counter: cpu.stat usage_usec is not a 64-bit count" })
    if ($fe.Count -ne 1 -or (Get-Facts $c).Mutating.Count -ne 0) { $okAll = $false }
    $det += "(e) overflowing usage_usec: labelled NO DATA lines $($fe.Count) (want 1)"
    Write-Case 'C14' 'corrupt / future state and odd counters: safe and self-healing' $okAll ($det -join "`n")
  }

  if (Want 'C15') {
    # A1/A2: an external restart (new StartedAt) with a HIGHER counter, and a
    # counter that drops: both re-baseline, so the streak does not carry over.
    $c = New-Ctx $ScriptPath $Both; $made += $c
    Invoke-Pass $c; Invoke-Pass $c -Pct $HotX; Invoke-Pass $c -Pct $HotX
    Invoke-Pass $c -Pct $HotX -Set @{ "$X.gen" = '7' }
    $r3 = (Get-Facts $c).Restarts.Count
    $idl = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu baseline recorded \(new container identity" })
    Invoke-Pass $c -Pct $HotX -Set @{ "$X.usec" = '500' }
    $back = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu baseline recorded \(the counter went backwards\)" })
    $neg = @((Get-Facts $c).Log | Where-Object { $_ -match "$X cpu -\d" })
    foreach ($i in 1..2) { Invoke-Pass $c -Pct $HotX }
    $r6 = (Get-Facts $c).Restarts.Count
    Invoke-Pass $c -Pct $HotX
    $r7 = (Get-Facts $c).Restarts.Count
    $ok = ($r3 -eq 0 -and $idl.Count -eq 1 -and $back.Count -eq 1 -and $neg.Count -eq 0 -and $r6 -eq 0 -and $r7 -eq 1)
    Write-Case 'C15' 'external restart / counter drop: re-baseline, the streak starts over' $ok ("restarts after the identity change $r3 (want 0), identity lines $($idl.Count), backwards lines $($back.Count), negative-% lines $($neg.Count), after 2 more $r6 (want 0), after 3 $r7 (want 1)`n" + (Show (Get-Facts $c)))
  }

  if (-not $Keep) { foreach ($m in $made) { Remove-Item -LiteralPath $m.Root -Recurse -Force -ErrorAction SilentlyContinue } }
  else { Write-Host ("sandboxes kept: " + (($made | ForEach-Object Root) -join ', ')) }
  return $script:Failures
}

Write-Host "==> CPU-spin guard test against $Script"
$fails = Invoke-Suite $Script
$summary = $script:Results -join ', '
Write-Host "==> $fails failed case(s): $summary"
$total = $fails

if ($Mutants) {
  $src = Get-Content -LiteralPath $Script -Raw
  # Each mutant is one or more (from, to) pairs; every `from` must occur exactly once.
  $defs = [ordered]@{
    # the 30-minute span is dropped: 3 hot intervals of any length are a spin
    'M1-window'    = @('if ($hotCount -lt $CpuSpinMinIntervals -or $hotMin -lt $CpuSpinWindowMinutes) {', 'if ($hotCount -lt $CpuSpinMinIntervals) {')
    'M2-hourly-cap' = @('} elseif ($lastRestart -and (($now - $lastRestart).TotalMinutes -lt 60)) {', '} elseif ($false) {')
    'M3-allow-list' = @('if ($SpinRestartAllowList -cnotcontains $name) {', 'if ($false) {')
    # "a hung docker must not hide a spin": extrapolate a full core instead of acting on nothing
    'M4-no-data'   = @('Write-Ob $label warn "NO DATA (cpu counter: $($cpu.Why)) - no action"; $script:Faults++; continue',
                       '$cpu = @{ Usec = [int64]$st[$name].usec + [int64](([DateTime]::UtcNow - (ConvertFrom-ObInstant $st[$name].at)).TotalSeconds * 1000000) }')
    # (tester M5) a new container identity no longer re-baselines
    'M5-identity'  = @('} elseif ($prev.identity -ne $identity) {', '} elseif ($false) {')
    # the over-long-interval bound is dropped: a long gap counts as an interval
    'M6-interval-bound' = @('if ($elapsed -gt ($CpuSpinMaxIntervalMinutes * 60)) {', 'if ($false) {')
    # (tester M7) the cap is stamped only when the restart succeeded
    'M7-cap-stamp-after-success' = @(
      '$prev.last_restart = $nowS; $prev.hot_since = $null; $prev.hot_count = 0; $prev.usec = $null', '$prev.hot_since = $null; $prev.hot_count = 0; $prev.usec = $null',
      '$rs = Invoke-ObDocker @(''restart'', $name) 60', '$rs = Invoke-ObDocker @(''restart'', $name) 60; if ($rs.Ok) { $prev.last_restart = $nowS }')
    # (tester M8) any docker failure in Get-CState reads as "absent"
    'M8-nodata-as-absent' = @("  return 'unknown'", "  return 'absent'")
    # (tester M9) 'unknown' falls through to the exited path -> docker start
    'M9-unknown-docker-starts' = @('  if ($state -eq ''unknown'') { Write-Ob $Name warn "NO DATA (docker inspect $($script:CStateWhy[$Name])) - no action"; $script:Faults++; return $false }', '')
    # the 3-interval minimum is dropped: 30 min over 2 long-ish intervals is a spin
    'M10-min-intervals' = @('if ($hotCount -lt $CpuSpinMinIntervals -or $hotMin -lt $CpuSpinWindowMinutes) {', 'if ($hotMin -lt $CpuSpinWindowMinutes) {')
    # future timestamps are trusted (the guard goes blind until the clock catches up)
    'M11-future-trusted' = @('} elseif ($prevAt -gt $skew -or ($hotSince -and $hotSince -gt $skew)) {', '} elseif ($false) {')
    # an unreadable last_restart reads as "never restarted" (no cap)
    'M12-bad-cap-uncapped' = @('if (-not $lr -or $lr -gt $skew) {', 'if ($false) {')
    # a counter that went backwards is averaged (negative %) instead of re-baselined
    'M13-counter-backwards' = @('} elseif ($usec -lt $prevUsec) {', '} elseif ($false) {')
  }
  foreach ($k in $defs.Keys) {
    $pairs = $defs[$k]; $msrc = $src; $bad = ''
    for ($i = 0; $i -lt $pairs.Count; $i += 2) {
      $n = ([regex]::Matches($msrc, [regex]::Escape($pairs[$i]))).Count
      if ($n -ne 1) { $bad = "pair $($i / 2): the target text occurs $n times (expected 1)"; break }
      $msrc = $msrc.Replace($pairs[$i], $pairs[$i + 1])
    }
    if ($bad) { Write-Host "## $k INVALID: $bad"; $total++; continue }
    $mp = Join-Path $StubCache "mutant-$k.ps1"
    [IO.File]::WriteAllText($mp, $msrc, (New-Object System.Text.UTF8Encoding $false))
    Write-Host "==> mutant $k"
    $mf = Invoke-Suite $mp
    $killed = ($mf -gt 0)
    Write-Host ("## {0} {1} (failed cases: {2})" -f $k, $(if ($killed) { 'KILLED' } else { 'SURVIVED' }), (($script:Results | Where-Object { $_ -match 'FAIL' }) -join ', '))
    if (-not $killed) { $total++ }
  }
}

Remove-Item -LiteralPath $StubCache -Recurse -Force -ErrorAction SilentlyContinue
exit $total
