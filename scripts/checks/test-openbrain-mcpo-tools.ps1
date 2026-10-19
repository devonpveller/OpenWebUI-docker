# test-openbrain-mcpo-tools.ps1 - the test for check-openbrain-health.ps1's mcpo TOOL-COUNT
# guard (openbrain-mcpo / openbrain-mcpo-ext). Item mcpo-resilience, 2026-10-19.
#
# NOTHING HERE TOUCHES THE HOST DAEMON (the test-openbrain-cpu-spin.ps1 pattern):
#   - -Script is COPIED into a temp sandbox tree, so its repo root is the sandbox;
#   - docker is a compiled STUB (<sbx>\stubs\docker.exe), first on PATH, answering `inspect`,
#     `exec <name> python -c ...` (the tool count) and `restart` from <sbx>\stubs\scenario.txt
#     and appending every call to <sbx>\stubs\calls.log; anything else exits 1;
#   - DOCKER_HOST=tcp://127.0.0.1:1 in every child: should the stub ever not be the docker that
#     runs, the real CLI fails closed against a dead endpoint.
#
# Cases (each is one full pass of the script):
#   T1 both proxies serve tools        -> counts reported, no restart, no mutating call
#   T2 mcpo serves 0, -Repair          -> `restart openbrain-mcpo` ONCE and only it; ends with tools
#   T3 mcpo serves 0, no -Repair       -> reported FAIL, NO mutating call
#   T4 mcpo serves 0, upstream absent  -> NO restart (a proxy is not restarted into an empty load)
#   T5 restart does not help           -> exactly one restart, then the failure is reported
#   T6 count unreadable (exec fails / non-numeric) -> NO DATA, no restart
#   T7 ext serves 0, -Repair           -> `restart openbrain-mcpo-ext` only (upstream openbrain-ext)
#   T8 0 tools never reads as ok       -> no "[OK] ... serves" line for the empty proxy
#
# Usage (repo root):   powershell -NoProfile -File scripts\checks\test-openbrain-mcpo-tools.ps1
#   ... -Script <another check-openbrain-health.ps1>   (e.g. the base, to see it go RED)
# Exit code = number of failed cases.

[CmdletBinding()]
param(
  [string]$Script = '',
  [switch]$Keep
)

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path $here 'check-openbrain-health.ps1' }
$Script = (Resolve-Path $Script).Path

$StubSrc = @'
using System; using System.IO; using System.Collections.Generic;
public static class McpoStubDocker__SFX__ {
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
      Console.WriteLine(fmt.Replace("{{.State.Status}}", sc[name + ".status"]).Replace("{{.State.StartedAt}}", "2026-10-19T00:00:00.000000001Z"));
      return 0;
    }
    if (a[0] == "exec" && a.Length >= 5 && a[2] == "python" && a[3] == "-c") {
      name = a[1];
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error response from daemon: No such container: " + name); return 1; }
      string t = Get(sc, name + ".tools", "0");
      if (t == "error") { Console.Error.WriteLine("Error response from daemon: stub exec failure"); return 1; }
      if (t == "nan") { Console.WriteLine("abc"); return 0; }
      Console.WriteLine(t); return 0;
    }
    if (a[0] == "restart") {
      if (!sc.ContainsKey(name + ".status")) { Console.Error.WriteLine("Error: No such container: " + name); return 1; }
      File.AppendAllText(Path.Combine(Dir, "scenario.txt"), name + ".tools=" + Get(sc, name + ".restart_tools", "0") + "\n");
      Console.WriteLine(name); return 0;
    }
    Console.Error.WriteLine("stub docker: unsupported: " + string.Join(" ", a)); return 1;
  }
}
'@
$Cache = Join-Path ([IO.Path]::GetTempPath()) ("obmcpo-bin-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $Cache -Force | Out-Null
Add-Type -TypeDefinition ($StubSrc.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))) -OutputAssembly (Join-Path $Cache 'docker.exe') -OutputType ConsoleApplication

function Invoke-Case {
  param([hashtable]$Scenario, [switch]$NoRepair, [int]$Wait = 6)
  $root = Join-Path ([IO.Path]::GetTempPath()) ("obmcpo-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
  foreach ($d in @('logs', 'scripts\checks', 'stubs')) { New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null }
  Copy-Item -LiteralPath $Script -Destination (Join-Path $root 'scripts\checks\check-openbrain-health.ps1')
  Copy-Item (Join-Path $Cache 'docker.exe') (Join-Path $root 'stubs\docker.exe')
  $lines = @($Scenario.Keys | ForEach-Object { "$_=$($Scenario[$_])" })
  [IO.File]::WriteAllText((Join-Path $root 'stubs\scenario.txt'), (($lines -join "`n") + "`n"))
  $oldPath = $env:PATH; $oldHost = $env:DOCKER_HOST
  $env:PATH = (Join-Path $root 'stubs') + ';' + $env:PATH
  $env:DOCKER_HOST = 'tcp://127.0.0.1:1'
  try {
    $resolved = (Get-Command docker -CommandType Application | Select-Object -First 1).Source
    if ($resolved -ne (Join-Path $root 'stubs\docker.exe')) { throw "FAIL-CLOSED: docker resolves to $resolved, not the stub" }
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'scripts\checks\check-openbrain-health.ps1'), '-DockerTimeoutSeconds', '5')
    if (-not $NoRepair) { $a += '-Repair' }
    if ((Get-Content $Script -Raw) -match 'McpoRepairWaitSeconds') { $a += @('-McpoRepairWaitSeconds', "$Wait") }
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'powershell.exe'; $psi.Arguments = ($a -join ' ')
    $psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
    $pr = [System.Diagnostics.Process]::Start($psi)
    $o = $pr.StandardOutput.ReadToEndAsync(); $e = $pr.StandardError.ReadToEndAsync()
    if (-not $pr.WaitForExit(240000)) { & taskkill.exe /PID $pr.Id /T /F 2>$null | Out-Null; $out = 'CHILD TIMED OUT' }
    else { $pr.WaitForExit(); $out = $o.Result + $e.Result }
  } finally {
    $env:PATH = $oldPath
    if ($null -eq $oldHost) { Remove-Item Env:\DOCKER_HOST -ErrorAction SilentlyContinue } else { $env:DOCKER_HOST = $oldHost }
  }
  $cf = Join-Path $root 'stubs\calls.log'
  $calls = @(); if (Test-Path $cf) { $calls = @(Get-Content $cf | Where-Object { $_ }) }
  $res = [pscustomobject]@{
    Out = $out; Calls = $calls
    Restarts = @($calls | Where-Object { $_ -match '^restart ' })
    Mutating = @($calls | Where-Object { $_ -match '^(restart|start|stop|kill|rm|pause|unpause|update|compose|run|create)\b' })
  }
  if (-not $Keep) { Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue }
  return $res
}

$script:Failures = 0
function Write-Case([string]$Id, [string]$Title, [bool]$Pass, $R) {
  if (-not $Pass) { $script:Failures++ }
  Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $(if ($Pass) { 'PASS' } else { 'FAIL' }))
  $show = @($R.Out -split "`r?`n" | Where-Object { $_ -match 'openbrain-mcpo' })
  Write-Host ("    restarts: [{0}]  mutating: [{1}]" -f ($R.Restarts -join '; '), ($R.Mutating -join '; '))
  foreach ($l in $show) { Write-Host "    | $($l.Trim())" }
}

$Up = @{ 'openbrain-mcpo.status' = 'running'; 'openbrain-mcpo-ext.status' = 'running'
         'openbrain-mcp.status' = 'running'; 'openbrain-ext.status' = 'running' }
function Scn([hashtable]$Extra) { $h = [ordered]@{}; foreach ($k in $Up.Keys) { $h[$k] = $Up[$k] }; foreach ($k in $Extra.Keys) { $h[$k] = $Extra[$k] }; return $h }

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '48'; 'openbrain-mcpo-ext.tools' = '45' })
Write-Case 'T1' 'both proxies serve tools: counts reported, nothing restarted' `
  (($r.Out -match 'openbrain-mcpo\s+serves 48 tools') -and ($r.Out -match 'openbrain-mcpo-ext\s+serves 45 tools') -and $r.Mutating.Count -eq 0) $r

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '0'; 'openbrain-mcpo.restart_tools' = '48'; 'openbrain-mcpo-ext.tools' = '45' })
Write-Case 'T2' 'mcpo serves 0, -Repair: one restart of that proxy, ends with tools' `
  (($r.Restarts.Count -eq 1) -and ($r.Restarts[0] -ceq 'restart openbrain-mcpo') -and $r.Mutating.Count -eq 1 -and
   ($r.Out -match 'SERVES 0 TOOLS') -and ($r.Out -match 'restarted; now serves 48 tools')) $r

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '0'; 'openbrain-mcpo-ext.tools' = '45' }) -NoRepair
Write-Case 'T3' 'mcpo serves 0, no -Repair: reported, nothing touched' `
  (($r.Mutating.Count -eq 0) -and ($r.Out -match 'SERVES 0 TOOLS') -and ($r.Out -match 'run with -Repair')) $r

$s = Scn @{ 'openbrain-mcpo.tools' = '0'; 'openbrain-mcpo-ext.tools' = '45' }; $s.Remove('openbrain-mcp.status')
$r = Invoke-Case $s
Write-Case 'T4' 'mcpo serves 0 but openbrain-mcp is absent: no restart' `
  (($r.Mutating.Count -eq 0) -and ($r.Out -match 'not restarting a proxy into an empty load')) $r

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '0'; 'openbrain-mcpo.restart_tools' = '0'; 'openbrain-mcpo-ext.tools' = '45' }) -Wait 4
Write-Case 'T5' 'restart does not help: exactly one restart, failure reported' `
  (($r.Restarts.Count -eq 1) -and ($r.Out -match 'still 0 tools')) $r

foreach ($mode in @('error', 'nan')) {
  $r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = $mode; 'openbrain-mcpo-ext.tools' = '45' })
  Write-Case "T6-$mode" "count unreadable ($mode): NO DATA, no restart" `
    (($r.Mutating.Count -eq 0) -and ($r.Out -match 'NO DATA \(tool count')) $r
}

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '48'; 'openbrain-mcpo-ext.tools' = '0'; 'openbrain-mcpo-ext.restart_tools' = '45' })
Write-Case 'T7' 'ext serves 0, -Repair: restarts openbrain-mcpo-ext only' `
  (($r.Restarts.Count -eq 1) -and ($r.Restarts[0] -ceq 'restart openbrain-mcpo-ext') -and ($r.Out -match 'restarted; now serves 45 tools')) $r

$r = Invoke-Case (Scn @{ 'openbrain-mcpo.tools' = '0'; 'openbrain-mcpo.restart_tools' = '48'; 'openbrain-mcpo-ext.tools' = '45' }) -NoRepair
Write-Case 'T8' 'an empty proxy never gets an OK line' `
  (-not ($r.Out -match '\[OK\]\s+openbrain-mcpo\s+(serves|restarted)') -and ($r.Out -match '\[DOWN\]\s+openbrain-mcpo\s+SERVES 0 TOOLS')) $r

Remove-Item -Recurse -Force $Cache -ErrorAction SilentlyContinue
Write-Host ""
Write-Host ("FAILED CASES: {0}" -f $script:Failures)
exit $script:Failures
