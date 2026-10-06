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
      if (a[a.Length - 1] == "/sys/fs/cgroup/cpu.stat") {
        string u = Get(sc, name + ".usec", "0");
        Console.WriteLine("usage_usec " + u); Console.WriteLine("user_usec " + u); Console.WriteLine("system_usec 0");
        return 0;
      }
      Console.Error.WriteLine("stub docker: exec not supported"); return 1;
    }
    if (a[0] == "restart") {
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error: No such container: " + name); return 1; }
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
        $t = [DateTime]::Parse([string]$v, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).ToUniversalTime()
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
  foreach ($k in @($sc.Keys)) { if ($k -match '\.(exec|inspect)$') { $sc.Remove($k) } }
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
    $ok = ($f.Restarts.Count -eq 0 -and $f.Alerts.Count -eq 0 -and $nd.Count -eq 3 -and $c.Seconds -lt 60 -and $f.Foreign.Count -eq 0)
    Write-Case 'C4' 'docker timeout / error: no restart, a clear NO DATA line, bounded' $ok ((Show $f) + "`nslowest pass: {0:N1}s" -f $c.Seconds)
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
  $defs = [ordered]@{
    'M1-window'    = @('if ($hotMin -lt $CpuSpinWindowMinutes) {', 'if ($false) {')
    'M2-hourly-cap' = @('} elseif ($lastRestart -and (($now - $lastRestart).TotalMinutes -lt 60)) {', '} elseif ($false) {')
    'M3-allow-list' = @('if ($SpinRestartAllowList -cnotcontains $name) {', 'if ($false) {')
    # "a hung docker must not hide a spin": extrapolate a full core instead of acting on nothing
    'M4-no-data'   = @('Write-Ob $label warn "NO DATA (cpu counter: $($cpu.Why)) - no action"; $script:Faults++; continue',
                       '$cpu = @{ Usec = [int64]$st[$name].usec + [int64](([DateTime]::UtcNow - (ConvertFrom-ObInstant $st[$name].at)).TotalSeconds * 1000000) }')
  }
  foreach ($k in $defs.Keys) {
    $from = $defs[$k][0]; $to = $defs[$k][1]
    $n = ([regex]::Matches($src, [regex]::Escape($from))).Count
    if ($n -ne 1) { Write-Host "## $k INVALID: the target text occurs $n times (expected 1)"; $total++; continue }
    $mp = Join-Path $StubCache "mutant-$k.ps1"
    [IO.File]::WriteAllText($mp, $src.Replace($from, $to), (New-Object System.Text.UTF8Encoding $false))
    Write-Host "==> mutant $k"
    $mf = Invoke-Suite $mp
    $killed = ($mf -gt 0)
    Write-Host ("## {0} {1} (failed cases: {2})" -f $k, $(if ($killed) { 'KILLED' } else { 'SURVIVED' }), (($script:Results | Where-Object { $_ -match 'FAIL' }) -join ', '))
    if (-not $killed) { $total++ }
  }
}

Remove-Item -LiteralPath $StubCache -Recurse -Force -ErrorAction SilentlyContinue
exit $total
