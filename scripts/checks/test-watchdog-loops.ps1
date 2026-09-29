# test-watchdog-loops.ps1 - the FIXED-SCOPE test for stack-watchdog.ps1's
# container-loop section (crash loops + orphaned network namespaces + bounded
# docker calls). Item cf-watchdog, 2026-09-28.
#
# Two parts, and nothing else - this test does not grow (the item it came from
# was stopped after its verifier grew to 2,484 lines):
#
#   -Part pure   Loads the watchdog's FUNCTIONS (never its main block) from
#                -Script into a sandbox, with DOCKER_HOST forced to the dead
#                endpoint tcp://127.0.0.1:1, and feeds the loop / netns
#                decisions recorded `docker inspect` JSON from
#                fixtures\watchdog-loops\. Plus the bounded-call proof against a
#                stub docker that never returns.
#   -Part dind   Starts a DISPOSABLE Docker-in-Docker (labelled
#                ai-stack.harness.owner=<Owner>), seeds a crash-looping
#                container, orphaned netns joiners and healthy/stopped
#                containers INSIDE it, and runs a relocated copy of -Script
#                with `-Mode loops` three times against it, DOCKER_HOST pointing
#                at the DinD and verified before every pass.
#
# THE ALERT TRANSPORT IS FAKED AT ITS LEAVES in both parts. The watchdog is run
# from (or its functions bound to) a sandbox tree whose PROJECT_DIR is a temp
# directory, so:
#   - Send-TelegramAlert runs <sandbox>\.venv\Scripts\python.exe, which here is
#     a compiled recorder that appends the message to transport-telegram.log;
#     the real scripts\sysadmin-mcp\telegram_notify.py is never on its path.
#   - the Mattermost mirror runs <sandbox>\scripts\notify-mattermost.sh, a
#     two-line fake that appends to transport-mattermost.log.
#   - every sentinel / state file lands in <sandbox>\logs, never the real logs\.
# Nothing is posted anywhere, and nothing here touches the host daemon except
# creating, loading and removing the one labelled DinD container.
#
# NEVER point -Script at a copy and run it with -Mode check: the full check
# restarts services, host scheduled tasks and even Docker Desktop.
#
# Usage (from the repo root):
#   powershell -NoProfile -File scripts\checks\test-watchdog-loops.ps1 -Part pure
#   powershell -NoProfile -File scripts\checks\test-watchdog-loops.ps1 -Part dind -Owner <your-wt-id>
#   ... -Script <path to another stack-watchdog.ps1>   (e.g. the base, for RED)
# Exit code = number of failed cases.

[CmdletBinding()]
param(
    [ValidateSet('pure', 'dind', 'all')][string]$Part = 'all',
    [string]$Script = '',
    [string]$Owner = 'cf-watchdog-test',
    [string]$AlpineImage = 'alpine:3.20',
    [string]$DindImage = 'docker:27-dind',
    [string]$RecordFixtures = ''
)

$ErrorActionPreference = 'Stop'
# The fixtures are `docker inspect` of the DinD's seeded containers, recorded with
# -RecordFixtures, then with each record's NetworkSettings removed (the parser
# does not read it, and its bridge IPs trip the identity gate). Re-record the same way.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$FixtureDir = Join-Path $here 'fixtures\watchdog-loops'
if (-not $Script) { $Script = Join-Path $here 'stack-watchdog.ps1' }  # ($PSScriptRoot is empty in a 5.1 param default)
$Script = (Resolve-Path $Script).Path
$script:Failures = 0
$script:Results = @()

function Write-Case {
    param([string]$Id, [string]$Title, [bool]$Pass, [string]$Detail)
    $verdict = if ($Pass) { 'PASS' } else { 'FAIL' }
    if (-not $Pass) { $script:Failures++ }
    $script:Results += "$Id $verdict"
    Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $verdict)
    if ($Detail) { foreach ($l in ($Detail -split "`n")) { Write-Host "    $l" } }
}

# --- sandbox with the faked transport leaves ---------------------------------
function New-Sandbox {
    $root = Join-Path ([IO.Path]::GetTempPath()) ("cfwd-sbx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    foreach ($d in @('logs', 'scripts\checks', 'scripts\sysadmin-mcp', '.venv\Scripts', 'stubs')) {
        New-Item -ItemType Directory -Path (Join-Path $root $d) -Force | Out-Null
    }
    # Present only so Send-TelegramAlert's Test-Path passes; never executed.
    [IO.File]::WriteAllText((Join-Path $root 'scripts\sysadmin-mcp\telegram_notify.py'), "# fake - never run`n")
    $sh = "#!/bin/sh`nprintf '%s\n' `"`$1`" >> `"`$(dirname `"`$0`")/../transport-mattermost.log`"`n"
    [IO.File]::WriteAllText((Join-Path $root 'scripts\notify-mattermost.sh'), $sh)
    $rec = @"
using System; using System.IO;
public static class CfwdRecorder__SFX__ { public static int Main(string[] a) {
  string f = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", "..", "transport-telegram.log"));
  File.AppendAllText(f, (a.Length > 1 ? a[1] : "") + "\n"); return 0; } }
"@
    $rec = $rec.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))
    Add-Type -TypeDefinition $rec -OutputAssembly (Join-Path $root '.venv\Scripts\python.exe') -OutputType ConsoleApplication
    return $root
}

function Get-Transport {
    param([string]$Root)
    $tg = Join-Path $Root 'transport-telegram.log'
    $mm = Join-Path $Root 'transport-mattermost.log'
    return [pscustomobject]@{
        Telegram   = @(if (Test-Path $tg) { Get-Content $tg | Where-Object { $_ } })
        Mattermost = @(if (Test-Path $mm) { Get-Content $mm | Where-Object { $_ } })
    }
}

function Measure-Alerts {
    # How many messages on EACH transport match a pattern.
    param($Transport, [string]$Pattern)
    $t = @($Transport.Telegram | Where-Object { $_ -match $Pattern }).Count
    $m = @($Transport.Mattermost | Where-Object { $_ -match $Pattern }).Count
    return "tg=$t mm=$m"
}

# =============================================================================
# PART 1 - pure functions on recorded docker inspect JSON
# =============================================================================
function Invoke-PurePart {
    $env:DOCKER_HOST = 'tcp://127.0.0.1:1'   # dead endpoint: nothing here can reach a daemon
    Remove-Item Env:\DOCKER_CONTEXT -ErrorAction SilentlyContinue
    $sbx = New-Sandbox
    Write-Host "pure part: sandbox $sbx ; DOCKER_HOST=$env:DOCKER_HOST ; script $Script"

    # Load FUNCTIONS and top-level assignments only - the main switch (which
    # would run a health check) is never executed. Paths are re-pointed at the
    # sandbox afterwards.
    $tokens = $null; $perr = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, [ref]$perr)
    if ($perr.Count -gt 0) { throw "parse errors in ${Script}: $($perr[0].Message)" }
    $skipVars = @('SCRIPT_DIR', 'PROJECT_DIR', 'LOG_FILE', 'LogDir', 'ErrorActionPreference', 'ProgressPreference')
    foreach ($st in $ast.EndBlock.Statements) {
        if ($st -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
            . ([scriptblock]::Create($st.Extent.Text))
        } elseif ($st -is [System.Management.Automation.Language.AssignmentStatementAst] -and
                  $st.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
                  $skipVars -notcontains $st.Left.VariablePath.UserPath) {
            . ([scriptblock]::Create($st.Extent.Text))
        }
    }
    $script:SCRIPT_DIR = Join-Path $sbx 'scripts\checks'
    $script:PROJECT_DIR = $sbx
    $script:LOG_FILE = Join-Path $sbx 'logs\tailscale-health.log'
    # Quiet logger (not under test): the file only, which P4 reads. The real one
    # also echoes every line to the console.
    function Write-LogEntry { param([string]$Message, [string]$Level = 'INFO')
        "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') [$Level] $Message" | Out-File -FilePath $LOG_FILE -Append -Encoding UTF8 }

    $need = @('Test-ContainerRestartLoops', 'Test-NetnsJoinedContainers', 'Test-UnboundedRestartPolicy',
              'ConvertTo-ContainerFact', 'Invoke-BoundedDocker', 'Get-ContainerRuntimeFacts', 'Send-LoopAlert')
    $missing = @($need | Where-Object { -not (Get-Command $_ -CommandType Function -ErrorAction SilentlyContinue) })
    if ($missing.Count -gt 0) {
        foreach ($id in 'P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8', 'P9', 'P10', 'P11') {
            Write-Case $id 'container-loop detection' $false ("the watchdog under test defines none of: " + ($missing -join ', '))
        }
        Remove-Item $sbx -Recurse -Force -ErrorAction SilentlyContinue
        return
    }

    # ConvertFrom-Json in 5.1 emits a JSON array as ONE object; unroll it.
    $j1 = Get-Content (Join-Path $FixtureDir 'inspect-pass1.json') -Raw | ConvertFrom-Json
    $j2 = Get-Content (Join-Path $FixtureDir 'inspect-pass2.json') -Raw | ConvertFrom-Json
    $pass1 = @($j1 | ForEach-Object { $_ })
    $pass2 = @($j2 | ForEach-Object { $_ })
    $f1 = @($pass1 | ForEach-Object { ConvertTo-ContainerFact -Record $_ })
    $f2 = @($pass2 | ForEach-Object { ConvertTo-ContainerFact -Record $_ })
    $loop1 = $f1 | Where-Object Name -eq 'cfwd-loop'
    $loop2 = $f2 | Where-Object Name -eq 'cfwd-loop'

    # P1: a loop is detected from the delta between two recorded passes, once.
    $r1 = [bool](@(Test-ContainerRestartLoops -Facts $f1) | Select-Object -Last 1)
    $afterBaseline = Get-Transport $sbx
    $r2 = [bool](@(Test-ContainerRestartLoops -Facts $f2) | Select-Object -Last 1)
    $t = Get-Transport $sbx
    $loopCount = Measure-Alerts $t "container 'cfwd-loop' is CRASH-LOOPING"
    $ok = ($r1 -eq $true) -and ($afterBaseline.Telegram.Count -eq 0) -and ($r2 -eq $false) -and ($loopCount -eq 'tg=1 mm=1')
    Write-Case 'P1' 'crash loop detected from the recorded pass1->pass2 delta, alerted once' $ok `
        ("recorded RestartCount cfwd-loop: pass1=$($loop1.RestartCount) pass2=$($loop2.RestartCount) policy=$($loop2.RestartPolicy)`n" +
         "pass1 (baseline) returned $r1 with $($afterBaseline.Telegram.Count) telegram msg(s); pass2 returned $r2; alerts $loopCount")

    # P2: healthy, intentionally stopped and exited-no-policy containers stay quiet.
    $quietNames = @('cfwd-healthy', 'cfwd-stopped', 'cfwd-exited', 'cfwd-owner', 'cfwd-owner-ok', 'cfwd-joiner-ok')
    $allMsgs = @($t.Telegram + $t.Mattermost)
    $noisy = @(foreach ($n in $quietNames) { if (@($allMsgs | Where-Object { $_ -match ("'" + [regex]::Escape($n) + "'") }).Count -gt 0) { $n } })
    $present = @($quietNames | Where-Object { $nm = $_; @($f2 | Where-Object Name -eq $nm).Count -eq 1 })
    Write-Case 'P2' 'healthy / stopped / exited containers do not alert' (($noisy.Count -eq 0) -and ($present.Count -eq $quietNames.Count)) `
        ("present in pass2 fixture: $($present -join ', ')`nalerted: $(if ($noisy) { $noisy -join ', ' } else { 'none' })")

    # P3: netns - the joiner whose owner restarted after it is orphaned; the
    # intact pair is not; the pair whose owner is GONE, with docker unreachable
    # (dead DOCKER_HOST), is NOT paged on an unconfirmed absence.
    $n1 = [bool](@(Test-NetnsJoinedContainers -Facts $f2) | Select-Object -Last 1)
    $t = Get-Transport $sbx
    $orph = Measure-Alerts $t "container 'cfwd-joiner' is ORPHANED"
    $okPair = Measure-Alerts $t "'cfwd-joiner-ok'"
    $gone = Measure-Alerts $t "'cfwd-joiner-gone'"
    $j = $f2 | Where-Object Name -eq 'cfwd-joiner'
    $o = $f2 | Where-Object Name -eq 'cfwd-owner'
    Write-Case 'P3' 'orphaned netns joiner detected; intact pair and unconfirmed-gone owner not paged' `
        (($n1 -eq $false) -and ($orph -eq 'tg=1 mm=1') -and ($okPair -eq 'tg=0 mm=0') -and ($gone -eq 'tg=0 mm=0')) `
        ("cfwd-joiner started $($j.StartedAt); its owner cfwd-owner started $($o.StartedAt)`nresult $n1; orphan $orph; intact pair $okPair; gone-owner (docker unreachable) $gone")

    # P4: a second run inside the cooldown does not re-alert either key.
    $bump = @($pass2 | ForEach-Object { $_ | ConvertTo-Json -Depth 20 | ConvertFrom-Json })
    ($bump | Where-Object { $_.Name -eq '/cfwd-loop' }).RestartCount = $loop2.RestartCount + 5
    $f3 = @($bump | ForEach-Object { ConvertTo-ContainerFact -Record $_ })
    $before = Get-Transport $sbx
    $r3 = [bool](@(Test-ContainerRestartLoops -Facts $f3) | Select-Object -Last 1)
    $n3 = [bool](@(Test-NetnsJoinedContainers -Facts $f3) | Select-Object -Last 1)
    $after = Get-Transport $sbx
    $logTxt = Get-Content (Join-Path $sbx 'logs\tailscale-health.log') -Raw
    $ok = ($r3 -eq $false) -and ($n3 -eq $false) -and ($after.Telegram.Count -eq $before.Telegram.Count) -and
          ($after.Mattermost.Count -eq $before.Mattermost.Count) -and ($logTxt -match 'LOOP \[crashloop-cfwd-loop\] still firing')
    Write-Case 'P4' 'second pass inside the cooldown: still detected, not re-alerted' $ok `
        ("pass3 loops=$r3 netns=$n3; transport before tg=$($before.Telegram.Count) mm=$($before.Mattermost.Count), after tg=$($after.Telegram.Count) mm=$($after.Mattermost.Count)")

    # P5: the decision edges, each starting from a RECORDED record.
    $base = $pass2 | Where-Object { $_.Name -eq '/cfwd-healthy' }
    $mk = {
        param($name, $id, $count, $policy, $max)
        $r = $base | ConvertTo-Json -Depth 20 | ConvertFrom-Json
        $r.Name = "/$name"; $r.Id = $id; $r.RestartCount = $count
        $r.HostConfig.RestartPolicy.Name = $policy; $r.HostConfig.RestartPolicy.MaximumRetryCount = $max
        ConvertTo-ContainerFact -Record $r
    }
    $cases = @(
        @{ N = 'edge-historic';  A = @('edge-historic', 'h1', 5000, 'unless-stopped', 0); B = @('edge-historic', 'h1', 5000, 'unless-stopped', 0); Expect = $false },
        @{ N = 'edge-recreated'; A = @('edge-recreated', 'r1', 40, 'always', 0);          B = @('edge-recreated', 'r2', 45, 'always', 0);          Expect = $false },
        @{ N = 'edge-policy-no'; A = @('edge-policy-no', 'n1', 0, 'no', 0);               B = @('edge-policy-no', 'n1', 9, 'no', 0);               Expect = $false },
        @{ N = 'edge-onfail-5';  A = @('edge-onfail-5', 'o5', 0, 'on-failure', 5);        B = @('edge-onfail-5', 'o5', 5, 'on-failure', 5);        Expect = $false },
        @{ N = 'edge-onfail-0';  A = @('edge-onfail-0', 'o0', 0, 'on-failure', 0);        B = @('edge-onfail-0', 'o0', 6, 'on-failure', 0);        Expect = $true }
    )
    $lines = @(); $allOk = $true
    foreach ($c in $cases) {
        $argA = $c.A; $argB = $c.B
        $fa = & $mk @argA; $fb = & $mk @argB
        Test-ContainerRestartLoops -Facts @($fa) | Out-Null
        Test-ContainerRestartLoops -Facts @($fb) | Out-Null
        $tt = Get-Transport $sbx
        $hit = @($tt.Telegram | Where-Object { $_ -match ("container '" + $c.N + "' is CRASH-LOOPING") }).Count -gt 0
        if ($hit -ne $c.Expect) { $allOk = $false }
        $lines += "$($c.N): alerted=$hit expected=$($c.Expect)"
    }
    Write-Case 'P5' 'decision edges: historic count, recreate, policy no, bounded and unbounded on-failure' $allOk ($lines -join "`n")

    # Helpers for P7-P10: a fact from a RECORDED record with fields overridden.
    $iso = { param([double]$minsAgo) (Get-Date).ToUniversalTime().AddMinutes(-$minsAgo).ToString('yyyy-MM-ddTHH:mm:ss.fffffffZ') }
    $fact = {
        param([string]$from, [string]$name, [string]$id, [int]$count, [string]$status, [string]$started, [string]$netmode)
        $r = ($pass2 | Where-Object { $_.Name -eq "/$from" }) | ConvertTo-Json -Depth 20 | ConvertFrom-Json
        $r.Name = "/$name"; $r.Id = $id; $r.RestartCount = $count
        $r.State.Status = $status; $r.State.StartedAt = $started
        if ($netmode) { $r.HostConfig.NetworkMode = $netmode }
        ConvertTo-ContainerFact -Record $r
    }
    $said = { param([string]$pattern) @((Get-Transport $sbx).Telegram | Where-Object { $_ -match $pattern }).Count }

    # P7: the all-clear waits until a looping container has SETTLED (stopped,
    # or running $LoopSettledMinutes). Fires, then: running 2 min -> none;
    # restarting -> none; running 11 min -> RESOLVED.
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p7-loop' 'p7' 0 'running' (& $iso 1) '') | Out-Null
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p7-loop' 'p7' 5 'running' (& $iso 1) '') | Out-Null
    $fired = & $said "ALERT ai-stack: container 'p7-loop' is CRASH-LOOPING"
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p7-loop' 'p7' 5 'running' (& $iso 2) '') | Out-Null
    $r2min = & $said "RESOLVED ai-stack: container 'p7-loop'"
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p7-loop' 'p7' 5 'restarting' (& $iso 3) '') | Out-Null
    $rBack = & $said "RESOLVED ai-stack: container 'p7-loop'"
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p7-loop' 'p7' 5 'running' (& $iso 11) '') | Out-Null
    $r11 = & $said "RESOLVED ai-stack: container 'p7-loop'"
    Write-Case 'P7' 'all-clear only once settled: not at 2 min running, not in backoff, yes at 11 min' `
        (($fired -eq 1) -and ($r2min -eq 0) -and ($rBack -eq 0) -and ($r11 -eq 1)) `
        "alert sent $fired; RESOLVED after running 2 min: $r2min; after a restarting pass: $rBack; after running 11 min: $r11"

    # P8: only a RUNNING joiner can be orphaned: an exited joiner whose owner
    # started after it is not paged.
    $p8o = & $fact 'cfwd-owner-ok' 'p8-owner' 'p8o' 0 'running' (& $iso 1) ''
    $p8j = & $fact 'cfwd-joiner-ok' 'p8-joiner' 'p8j' 0 'exited' (& $iso 30) 'container:p8o'
    $p8 = [bool](@(Test-NetnsJoinedContainers -Facts @($p8o, $p8j)) | Select-Object -Last 1)
    $p8n = & $said "'p8-joiner'"
    Write-Case 'P8' 'a stopped joiner is not paged, even with an owner that restarted after it' (($p8 -eq $true) -and ($p8n -eq 0)) `
        "result $p8; messages naming p8-joiner: $p8n"

    # P9: a RUNNING joiner whose owner is not running - stopped (exited), or in
    # restart backoff - with an OLDER owner start time is orphaned (it has only
    # lo). The owner-restarted-after rule alone misses both. A paused owner is not.
    $p9 = @(
        (& $fact 'cfwd-owner-ok' 'p9-owner-stopped' 'p9os' 0 'exited' (& $iso 60) ''),
        (& $fact 'cfwd-joiner-ok' 'p9-joiner-stopped' 'p9js' 0 'running' (& $iso 30) 'container:p9os'),
        (& $fact 'cfwd-owner-ok' 'p9-owner-backoff' 'p9ob' 4 'restarting' (& $iso 60) ''),
        (& $fact 'cfwd-joiner-ok' 'p9-joiner-backoff' 'p9jb' 0 'running' (& $iso 30) 'container:p9ob'),
        (& $fact 'cfwd-owner-ok' 'p9-owner-paused' 'p9op' 0 'paused' (& $iso 60) ''),
        (& $fact 'cfwd-joiner-ok' 'p9-joiner-paused' 'p9jp' 0 'running' (& $iso 30) 'container:p9op')
    )
    $p9r = [bool](@(Test-NetnsJoinedContainers -Facts $p9) | Select-Object -Last 1)
    $tt = Get-Transport $sbx
    $sStop = Measure-Alerts $tt "container 'p9-joiner-stopped' is ORPHANED: .*'p9-owner-stopped', is exited"
    $sBack = Measure-Alerts $tt "container 'p9-joiner-backoff' is ORPHANED: .*'p9-owner-backoff', is restarting"
    $sPaus = Measure-Alerts $tt "'p9-joiner-paused'"
    Write-Case 'P9' 'running joiner of a stopped owner and of a backing-off owner (older start) is orphaned; paused owner is not' `
        (($p9r -eq $false) -and ($sStop -eq 'tg=1 mm=1') -and ($sBack -eq 'tg=1 mm=1') -and ($sPaus -eq 'tg=0 mm=0')) `
        "result $p9r; stopped owner $sStop; backing-off owner $sBack; paused owner $sPaus"

    # P10: SLOW loop - one restart every other pass (the attempt-1 tester's A3,
    # ~72/day) never reaches the fast rule; the window rule pages it at the
    # 6th restart inside $SlowLoopWindowHours. And restarts older than the
    # window do not count.
    $seq = @(); $count = 0; $firstAt = 0
    for ($i = 1; $i -le 14; $i++) {
        if ($i % 2 -eq 0) { $count++ }
        Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p10-slow' 'p10' $count 'running' (& $iso 5) '') | Out-Null
        $n = & $said "container 'p10-slow' is CRASH-LOOPING: \d+ restart\(s\) in the last ${SlowLoopWindowHours}h \(a slow loop\)"
        if ($n -gt 0 -and $firstAt -eq 0) { $firstAt = $i }
        $seq += "$i/$count"
    }
    $slowMsgs = & $said "container 'p10-slow' is CRASH-LOOPING"
    # Old history: a state entry carrying 10 restarts from 7h ago, then 1 new.
    $statePath = Join-Path $sbx 'logs\.watchdog-restart-state.json'
    $st = Get-Content $statePath -Raw | ConvertFrom-Json
    $old = [DateTimeOffset]::UtcNow.AddHours(-7).ToUnixTimeSeconds()
    $st | Add-Member -NotePropertyName 'p10-old' -NotePropertyValue ([pscustomobject]@{
        Count = 20; Id = 'p10old'; Streak = 0; Accum = 0; Missed = 0; Hist = @(1..10 | ForEach-Object { "${old}:1" }) }) -Force
    ($st | ConvertTo-Json -Depth 5) | Out-File $statePath -Encoding utf8 -Force
    Test-ContainerRestartLoops -Facts @(& $fact 'cfwd-healthy' 'p10-old' 'p10old' 21 'running' (& $iso 5) '') | Out-Null
    $oldMsgs = & $said "container 'p10-old'"
    Write-Case 'P10' "slow loop (a restart every other pass) pages at the ${SlowLoopThreshold}th restart in ${SlowLoopWindowHours}h; history older than the window does not count" `
        (($firstAt -eq 12) -and ($slowMsgs -eq 1) -and ($oldMsgs -eq 0)) `
        ("pass/restarts: $($seq -join ' ')`nfirst slow-loop page at pass $firstAt (expected 12, the 6th restart); pages in total $slowMsgs`n" +
         "container with 10 restarts 7h ago + 1 now: messages $oldMsgs")

    # P6: every docker call the section makes is bounded - a stub docker that
    # answers `ps` and never returns from anything else. On `inspect` it also
    # starts a grandchild first, so the timeout must kill the whole TREE.
    $hangSrc = @"
public static class CfwdHang__SFX__ { public static int Main(string[] a) {
  string me = System.Reflection.Assembly.GetExecutingAssembly().Location;
  if (a.Length > 0 && a[0] == "ps") { System.Console.WriteLine("cfwd-hang"); return 0; }
  if (a.Length > 0 && a[0] == "gc") { System.Threading.Thread.Sleep(60000); return 0; }
  var psi = new System.Diagnostics.ProcessStartInfo(me, "gc"); psi.UseShellExecute = false;
  System.Diagnostics.Process.Start(psi);   // inherits this process's stdout/stderr
  if (a.Length > 0 && a[0] == "logs") { System.Console.WriteLine("fatal: from a docker that left a child"); System.Console.Out.Flush(); return 0; }
  System.Threading.Thread.Sleep(-1); return 0; } }
"@
    $hangExe = Join-Path $sbx 'stubs\docker-hang.exe'
    $hangSrc = $hangSrc.Replace('__SFX__', [guid]::NewGuid().ToString('N').Substring(0, 8))
    Add-Type -TypeDefinition $hangSrc -OutputAssembly $hangExe -OutputType ConsoleApplication
    # Plain assignments: the loaded settings live in THIS function's scope.
    $WatchdogDockerExe = $hangExe
    $DockerCallTimeoutSeconds = 2; $DockerProbeTimeoutSeconds = 2; $DockerLogsTimeoutSeconds = 2
    $FallbackBudgetSeconds = 5; $NetnsConfirmBudgetSeconds = 5
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $raw = Invoke-BoundedDocker -DockerArgs @('inspect', 'x'); $tRaw = $sw.Elapsed.TotalSeconds; $why = $script:BoundedFailureReason
    $sw.Restart(); $facts = @(Get-ContainerRuntimeFacts); $tFacts = $sw.Elapsed.TotalSeconds
    $sw.Restart(); $fl = Invoke-BoundedDocker -DockerArgs @('inspect', 'y'); $fl = "$($script:BoundedFailureReason)"; $tLog = $sw.Elapsed.TotalSeconds
    $orphanOwnerless = [pscustomobject]@{ Name = 'j'; Id = 'jid'; RestartCount = 0; Status = 'running'; StartedAt = '2026-09-28T00:00:00Z'
                                          RestartPolicy = 'no'; MaxRetries = 0; NetworkMode = 'container:ownerid' }
    $sw.Restart(); $nn = [bool](@(Test-NetnsJoinedContainers -Facts @($orphanOwnerless)) | Select-Object -Last 1); $tNet = $sw.Elapsed.TotalSeconds
    $sw.Stop()
    $left = @(Get-Process -Name 'docker-hang' -ErrorAction SilentlyContinue).Count
    $tt = Get-Transport $sbx
    $unread = Measure-Alerts $tt 'docker cannot describe 1 container'
    Start-Sleep -Milliseconds 500
    $ok = ($null -eq $raw) -and ($why -eq 'did not answer within 2s') -and ($tRaw -lt 6) -and
          ($facts.Count -eq 0) -and ($tFacts -lt 12) -and ($fl -eq 'did not answer within 2s') -and ($tLog -lt 6) -and
          ($nn -eq $true) -and ($tNet -lt 6) -and ($left -eq 0) -and ($unread -eq 'tg=1 mm=1')
    Write-Case 'P6' 'a docker that never returns cannot hang the section' $ok `
        ("Invoke-BoundedDocker: null=$($null -eq $raw) in $([math]::Round($tRaw,1))s reason='$why'`n" +
         "Get-ContainerRuntimeFacts: $($facts.Count) fact(s) in $([math]::Round($tFacts,1))s (batch + per-container probe both timed out); unreadable alert $unread`n" +
         "a second hanging inspect: '$fl' in $([math]::Round($tLog,1))s`n" +
         "Test-NetnsJoinedContainers (owner probe hangs): ok=$nn in $([math]::Round($tNet,1))s`n" +
         "stub processes left running (children AND grandchildren): $left")

    # P11: a docker that EXITS at once but leaves a grandchild holding its
    # stdout (the attempt-1 tester's A14: 29.7s under a 2s bound). The call
    # must return inside the bound, and the grandchild must be killed.
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $fl = Get-ContainerFaultLine -Name 'cfwd-hang'; $tGc = $sw.Elapsed.TotalSeconds; $sw.Stop()
    Start-Sleep -Milliseconds 500
    $leftGc = @(Get-Process -Name 'docker-hang' -ErrorAction SilentlyContinue).Count
    Write-Case 'P11' 'a grandchild holding the output open cannot stretch the bound, and is killed' `
        (($tGc -lt 4) -and ($fl -match 'kept its output open') -and ($leftGc -eq 0)) `
        "Get-ContainerFaultLine returned in $([math]::Round($tGc,1))s: '$fl'; stub processes left: $leftGc"
    Get-Process -Name 'docker-hang' -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    $WatchdogDockerExe = 'docker'
    Remove-Item $sbx -Recurse -Force -ErrorAction SilentlyContinue
}

# =============================================================================
# PART 2 - one proof in a disposable Docker-in-Docker
# =============================================================================
function Invoke-HostDocker {
    # The HOST daemon, used ONLY to create / load into / remove our labelled
    # DinD container. DOCKER_HOST is cleared for the call and restored.
    param([string[]]$DockerArgs)
    $saved = $env:DOCKER_HOST
    Remove-Item Env:\DOCKER_HOST -ErrorAction SilentlyContinue
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        $out = & docker @DockerArgs 2>&1
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
        return [pscustomobject]@{ Code = $code; Out = (@($out) | ForEach-Object { "$_" }) -join "`n" }
    } finally { if ($saved) { $env:DOCKER_HOST = $saved } }
}

function Invoke-Dind {
    param([string[]]$DockerArgs)
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $out = & docker -H $script:DindHost @DockerArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prev
    return [pscustomobject]@{ Code = $code; Out = (@($out) | ForEach-Object { "$_" }) -join "`n" }
}

function Assert-DindTarget {
    # Refuse to run a pass unless DOCKER_HOST names the DinD and the daemon
    # that answers there IS the DinD (its hostname), not Docker Desktop.
    $saved = $env:DOCKER_HOST
    $env:DOCKER_HOST = $script:DindHost
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        $info = & docker info --format '{{.Name}}|{{.OperatingSystem}}' 2>$null
        $ErrorActionPreference = $prev
    } finally { $env:DOCKER_HOST = $saved }
    $parts = "$info" -split '\|'
    if ($parts[0] -ne $script:DindHostname -or "$info" -match 'Docker Desktop') {
        throw "DOCKER_HOST=$script:DindHost answered '$info', expected the DinD '$script:DindHostname' - refusing to run the watchdog"
    }
    return "$info"
}

function Invoke-WatchdogPass {
    param([string]$Sandbox, [string]$Label)
    $target = Assert-DindTarget
    $saved = $env:DOCKER_HOST
    $env:DOCKER_HOST = $script:DindHost
    Remove-Item Env:\DOCKER_CONTEXT -ErrorAction SilentlyContinue
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $out = @(& $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Sandbox 'scripts\checks\stack-watchdog.ps1') -Mode loops 2>&1 | ForEach-Object { "$_" })
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prev
    } finally { if ($saved) { $env:DOCKER_HOST = $saved } else { Remove-Item Env:\DOCKER_HOST -ErrorAction SilentlyContinue } }
    Write-Host "  $Label : DOCKER_HOST=$script:DindHost -> '$target' ; watchdog -Mode loops exit $code"
    if ($code -ne 0 -and $code -ne 1) { $out | Select-Object -First 3 | ForEach-Object { Write-Host "    | $_" } }
    $script:LastPassErr = @($out | Where-Object { $_ -match '(?i)cannot validate|error' } | Select-Object -First 1)
    return $code
}

function Invoke-DindPart {
    $script:DindName = "cfwd-dind-" + [guid]::NewGuid().ToString('N').Substring(0, 6)
    $port = Get-Random -Minimum 23700 -Maximum 23800
    $script:DindHost = "tcp://127.0.0.1:$port"
    $sbx = $null
    $tar = Join-Path ([IO.Path]::GetTempPath()) ("cfwd-alpine-" + [guid]::NewGuid().ToString('N').Substring(0, 6) + ".tar")
    try {
        $have = Invoke-HostDocker @('image', 'inspect', '--format', '{{.Id}}', $AlpineImage)
        if ($have.Code -ne 0) { throw "$AlpineImage is not on the host; this test does not pull on the host. Provide it or pass -AlpineImage." }
        $r = Invoke-HostDocker @('run', '--privileged', '-d', '--name', $script:DindName, '--label', "ai-stack.harness.owner=$Owner",
                                 '-p', "127.0.0.1:${port}:2375", '-e', 'DOCKER_TLS_CERTDIR=', $DindImage)
        if ($r.Code -ne 0) { throw "could not start the DinD: $($r.Out)" }
        $script:DindHostname = (Invoke-HostDocker @('inspect', '--format', '{{.Config.Hostname}}', $script:DindName)).Out.Trim()
        $up = $false
        for ($i = 0; $i -lt 60; $i++) {
            if ((Invoke-Dind @('version', '--format', '{{.Server.Version}}')).Code -eq 0) { $up = $true; break }
            Start-Sleep 1
        }
        if (-not $up) { throw "DinD at $script:DindHost did not answer within 60s" }
        Write-Host "dind part: $script:DindName at $script:DindHost (hostname $script:DindHostname), owner label $Owner"
        $s = Invoke-HostDocker @('save', '-o', $tar, $AlpineImage)
        if ($s.Code -ne 0) { throw "docker save failed: $($s.Out)" }
        $l = Invoke-Dind @('load', '-i', $tar)
        if ($l.Code -ne 0) { throw "docker load into the DinD failed: $($l.Out)" }

        # Seed. The loop runs 11s and exits 1: past Docker's 10s backoff
        # reset, so it restarts steadily (~5 a minute) instead of backing off
        # towards a minute - pass 2, 60s after pass 1, sees a clear delta.
        # (No embedded double quotes: PS 5.1 mangles them on a native call.)
        $A = $AlpineImage
        $seed = @(
            @('run', '-d', '--name', 'cfwd-loop', '--restart', 'always', $A, 'sh', '-c', "echo 'fatal: invalid key: API key does not exist' >&2; sleep 11; exit 1"),
            @('run', '-d', '--name', 'cfwd-healthy', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-stopped', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('stop', '-t', '0', 'cfwd-stopped'),
            @('run', '-d', '--name', 'cfwd-exited', '--restart', 'no', $A, 'sh', '-c', 'exit 3'),
            @('run', '-d', '--name', 'cfwd-owner', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-joiner', '--restart', 'unless-stopped', '--network', 'container:cfwd-owner', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-owner-ok', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-joiner-ok', '--restart', 'unless-stopped', '--network', 'container:cfwd-owner-ok', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-owner-stop', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-joiner-stop', '--restart', 'unless-stopped', '--network', 'container:cfwd-owner-stop', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-owner-gone', '--restart', 'unless-stopped', $A, 'sleep', '3600'),
            @('run', '-d', '--name', 'cfwd-joiner-gone', '--restart', 'unless-stopped', '--network', 'container:cfwd-owner-gone', $A, 'sleep', '3600')
        )
        foreach ($cmd in $seed) {
            $x = Invoke-Dind $cmd
            if ($x.Code -ne 0) { throw "seed '$($cmd -join ' ')' failed: $($x.Out)" }
        }
        Start-Sleep 2
        # Orphan cfwd-joiner (its owner restarts AFTER it started) and remove
        # cfwd-owner-gone out from under cfwd-joiner-gone.
        foreach ($cmd in @(@('restart', '-t', '0', 'cfwd-owner'), @('rm', '-f', 'cfwd-owner-gone'), @('stop', '-t', '0', 'cfwd-owner-stop'))) {
            $x = Invoke-Dind $cmd
            if ($x.Code -ne 0) { throw "'$($cmd -join ' ')' failed: $($x.Out)" }
        }
        $gone = Invoke-Dind @('inspect', '--format', '{{.State.Status}}', 'cfwd-joiner-gone')
        Write-Host "  cfwd-joiner-gone after its owner was removed: $($gone.Out.Trim())"
        $stopSt = Invoke-Dind @('inspect', '--format', '{{.State.Status}}', 'cfwd-joiner-stop')
        $stopIf = Invoke-Dind @('exec', 'cfwd-joiner-stop', 'ip', '-o', 'link')
        $script:StopEvidence = "cfwd-joiner-stop is $($stopSt.Out.Trim()) with its owner stopped; its interfaces: " + (($stopIf.Out -split "`n" | ForEach-Object { ($_ -split ':')[1].Trim() }) -join ', ')
        Write-Host "  $script:StopEvidence"

        # (The committed fixtures were recorded before the stopped-owner pair
        # was added; re-recording adds it, which the pure part ignores.)
        $seeded = @('cfwd-loop', 'cfwd-healthy', 'cfwd-stopped', 'cfwd-exited', 'cfwd-owner', 'cfwd-joiner',
                    'cfwd-owner-ok', 'cfwd-joiner-ok', 'cfwd-joiner-gone', 'cfwd-owner-stop', 'cfwd-joiner-stop')

        # Relocated copy of the watchdog under test, with the faked leaves.
        $sbx = New-Sandbox
        Copy-Item $Script (Join-Path $sbx 'scripts\checks\stack-watchdog.ps1')

        $c1 = Invoke-WatchdogPass -Sandbox $sbx -Label 'pass 1'
        if ($RecordFixtures) {
            (Invoke-Dind (@('inspect') + $seeded)).Out | Out-File (Join-Path $RecordFixtures 'inspect-pass1.json') -Encoding ascii
        }
        $t1 = Get-Transport $sbx
        Write-Host "  waiting 60s so the loop accumulates restarts between passes..."
        Start-Sleep 60
        $c2 = Invoke-WatchdogPass -Sandbox $sbx -Label 'pass 2'
        if ($RecordFixtures) {
            (Invoke-Dind (@('inspect') + $seeded)).Out | Out-File (Join-Path $RecordFixtures 'inspect-pass2.json') -Encoding ascii
        }
        $t2 = Get-Transport $sbx
        Write-Host "  waiting 25s so pass 3 sees NEW restarts (a loop still live) inside the cooldown..."
        Start-Sleep 25
        $c3 = Invoke-WatchdogPass -Sandbox $sbx -Label 'pass 3 (inside the cooldown)'
        $t3 = Get-Transport $sbx
        $counts = Invoke-Dind @('inspect', '--format', '{{.Name}} restarts={{.RestartCount}} status={{.State.Status}} started={{.State.StartedAt}}', 'cfwd-loop', 'cfwd-owner', 'cfwd-joiner')

        $loopA = Measure-Alerts $t3 "container 'cfwd-loop' is CRASH-LOOPING"
        $orphA = Measure-Alerts $t3 "container 'cfwd-joiner' is ORPHANED"
        $goneA = Measure-Alerts $t3 "container 'cfwd-joiner-gone' shares the network namespace of a container that NO LONGER EXISTS"
        $stopA = Measure-Alerts $t3 "container 'cfwd-joiner-stop' is ORPHANED: .*'cfwd-owner-stop', is exited"
        $fault = @($t3.Telegram | Where-Object { $_ -match "cfwd-loop" -and $_ -match 'invalid key' }).Count
        $all = @($t3.Telegram + $t3.Mattermost)
        $wlogPath = Join-Path $sbx 'logs\tailscale-health.log'
        $wlog = if (Test-Path $wlogPath) { Get-Content $wlogPath -Raw } else { '' }
        $still = @([regex]::Matches($wlog, 'LOOP \[crashloop-cfwd-loop\] still firing')).Count
        $quiet = @('cfwd-healthy', 'cfwd-stopped', 'cfwd-exited', 'cfwd-owner-ok', 'cfwd-joiner-ok')
        $noisy = @(foreach ($n in $quiet) { if (@($all | Where-Object { $_ -match ("'" + [regex]::Escape($n) + "'") }).Count -gt 0) { $n } })
        $detail = ("passes exited $c1 / $c2 / $c3" + $(if ($script:LastPassErr) { " (watchdog said: $($script:LastPassErr[0]))" } else { "" }) + "`n" +
                   "transport after pass1 tg=$($t1.Telegram.Count) mm=$($t1.Mattermost.Count); pass2 tg=$($t2.Telegram.Count) mm=$($t2.Mattermost.Count); pass3 tg=$($t3.Telegram.Count) mm=$($t3.Mattermost.Count)`n" +
                   "crash loop $loopA (fault line carries 'invalid key': $fault); orphaned joiner $orphA; joiner of a removed owner $goneA; joiner of a STOPPED owner $stopA`n" +
                   "$script:StopEvidence`n" +
                   "pass 3 logged 'LOOP [crashloop-cfwd-loop] still firing' (detected, cooldown held): $still time(s)`n" +
                   "alerted among healthy/stopped/exited/intact: $(if ($noisy) { $noisy -join ', ' } else { 'none' })`n" +
                   "DinD state: " + ($counts.Out -replace "`n", '; ') + "`n" +
                   "telegram messages:`n  " + ($t3.Telegram -join "`n  ") + "`nmattermost messages:`n  " + ($t3.Mattermost -join "`n  "))
        $ok = ($loopA -eq 'tg=1 mm=1') -and ($orphA -eq 'tg=1 mm=1') -and ($goneA -eq 'tg=1 mm=1') -and ($stopA -eq 'tg=1 mm=1') -and ($fault -eq 1) -and
              ($noisy.Count -eq 0) -and ($still -ge 1) -and ($t3.Telegram.Count -eq $t2.Telegram.Count) -and ($t3.Mattermost.Count -eq $t2.Mattermost.Count)
        Write-Case 'D1' 'DinD: crash loop and orphaned netns (owner restarted / removed / stopped) alerted once each, healthy/stopped quiet, no re-alert inside the cooldown' $ok $detail
    } catch {
        Write-Case 'D1' 'DinD proof' $false "harness error: $($_.Exception.Message)"
    } finally {
        if ($script:DindName) {
            $rm = Invoke-HostDocker @('rm', '-f', '-v', $script:DindName)
            Write-Host "  teardown: docker rm -f -v $script:DindName -> exit $($rm.Code)"
        }
        Remove-Item $tar -Force -ErrorAction SilentlyContinue
        if ($sbx) { Remove-Item $sbx -Recurse -Force -ErrorAction SilentlyContinue }
    }
}

if ($Part -in 'pure', 'all') { Invoke-PurePart }
if ($Part -in 'dind', 'all') { Invoke-DindPart }
Write-Host ""
Write-Host ("RESULT: {0} case(s), {1} failed - {2}" -f $script:Results.Count, $script:Failures, ($script:Results -join ', '))
exit $script:Failures
