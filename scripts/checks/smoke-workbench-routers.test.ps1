# smoke-workbench-routers.test.ps1 - the smoke script's disposable-target check, without a Docker
# daemon (rw-p0b-purge-door attempt 2: attempt 1's label / compose / network half had no automated
# test - reverting Test-DisposableContainer to "return ''" left every automated refusal passing).
#
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.test.ps1 [-Smoke <a copy of the smoke script>]
#
# How: a 'docker' FUNCTION in this session stands in for docker.exe (the smoke script calls a
# 'docker' that is not an application as it is), answering `docker inspect` / `docker network
# inspect` with synthetic JSON from the world below, and `docker exec` with thirteen canned
# responses. Each case runs the smoke script in-process exactly as a caller does
# (-Capture <dir> -ExecContainer <c> -BaseUrl <u>) and checks: refused = exit 2, a REFUSED line
# naming the reason, and the capture directory NOT created; accepted = exit 0 and the capture
# written. Nothing here reaches a container.
#
# Sections: [1] the refusal shapes (compose label, compose network, ai-stack_* / open-brain*
# network, no owner label, NetworkMode host / none / bridge / container:<compose> /
# container:<clean> / container:<missing>, empty networks, a mode none of the joined networks,
# non-bridge driver, the default bridge, PID / IPC / UTS sharing, inspect failing, an unreadable
# answer, the stand-in's "no answer in time"); [2] the base-URL table (the tester's live
# spellings, refused before docker is asked); [3] the accepted shapes (one disposable container,
# five loopback spellings incl. [::1]); [4] a REAL docker.exe against an endpoint that accepts and
# never answers (no daemon involved): refused within the timeout, nothing created; [5] the revert
# check - this test run over two mutated copies of the script (Test-DisposableContainer and
# Test-DisposableTarget each reduced to "return ''") must FAIL.
# Exit 0 = every check passed.
param(
  [string]$Smoke = (Join-Path $PSScriptRoot 'smoke-workbench-routers.ps1'),
  # used by section [5] for its child runs only (a reverted check would accept the timeout case
  # and then wait on the exec); the top-level run never skips anything
  [switch]$ChildOfRevertCheck
)
$ErrorActionPreference = 'Stop'
$script:fail = 0
function Check([bool]$ok, [string]$m) { if ($ok) { Write-Host "  PASS  $m" } else { Write-Host "  FAIL  $m"; $script:fail++ } }
$root = Join-Path $env:TEMP ('smoke-check-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $root | Out-Null

# ---------------------------------------------------------------- the synthetic docker world
$global:SmokeCheckWorld = @{ containers = @(); networks = @(); timeout = @() }
function New-Net([string]$name, [hashtable]$labels = @{}, [string]$driver = 'bridge', [bool]$defaultBridge = $false) {
  $id = -join ((1..64) | ForEach-Object { '0123456789abcdef'[(Get-Random -Maximum 16)] })
  $opts = @{}; if ($defaultBridge) { $opts['com.docker.network.bridge.default_bridge'] = 'true' }
  return [pscustomobject]@{ Name = $name; Id = $id; Driver = $driver; Internal = $true; Labels = [pscustomobject]$labels; Options = [pscustomobject]$opts }
}
function New-Ctr([string]$name, [hashtable]$labels, [string]$mode, [string[]]$nets, [hashtable]$ns = @{}) {
  $id = -join ((1..64) | ForEach-Object { '0123456789abcdef'[(Get-Random -Maximum 16)] })
  $n = [ordered]@{}; foreach ($x in $nets) { $n[$x] = [pscustomobject]@{ NetworkID = ''; IPAddress = '192.0.2.10' } }
  return [pscustomobject]@{
    Id = $id; Name = "/$name"
    Config = [pscustomobject]@{ Labels = [pscustomobject]$labels }
    HostConfig = [pscustomobject]@{ NetworkMode = $mode; PidMode = [string]$ns.pid; IpcMode = $(if ($ns.ipc) { $ns.ipc } else { 'private' }); UTSMode = [string]$ns.uts }
    NetworkSettings = [pscustomobject]@{ Networks = [pscustomobject]$n }
  }
}
function Find-Ctr([string]$ref) {
  foreach ($c in $global:SmokeCheckWorld.containers) {
    if ($c.Name -eq "/$ref" -or $c.Id -eq $ref -or ($ref.Length -ge 12 -and $c.Id.StartsWith($ref))) { return $c }
  }
  return $null
}
function Find-Net([string]$ref) { foreach ($n in $global:SmokeCheckWorld.networks) { if ($n.Name -eq $ref -or $n.Id -eq $ref) { return $n } }; return $null }
# The stand-in. Answers the full inspect (this script's shape) and the -f '{{json ...}}' templates
# attempt 1's script asked for, so the same cases run against either version.
function docker {
  $a = @($args | ForEach-Object { "$_" })
  $global:LASTEXITCODE = 0
  if ($a[0] -eq 'exec') {
    $b64 = ($a | Where-Object { $_ -like 'SMOKE_CASES=*' } | Select-Object -First 1).Substring(12)
    $cases = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($b64)) | ConvertFrom-Json
    $out = @($cases | ForEach-Object { @{ n = $_.n; status = 200; type = 'application/json'; body = '{"stub":true}' } })
    return (ConvertTo-Json -InputObject $out -Compress -Depth 5)
  }
  $tpl = $null; $rest = @()
  for ($i = 0; $i -lt $a.Count; $i++) { if ($a[$i] -eq '-f') { $tpl = $a[$i + 1]; $i++ } else { $rest += $a[$i] } }
  $ref = $rest[-1]
  if ($global:SmokeCheckWorld.timeout -contains $ref) { $global:LASTEXITCODE = 124; return 'stand-in: no answer' }
  if ($rest[0] -eq 'inspect') {
    if ($ref -eq 'rwpt-garbled') { return 'this is not json {' }
    $c = Find-Ctr $ref
    if (-not $c) { $global:LASTEXITCODE = 1; return "Error response from daemon: No such container: $ref" }
    if ($tpl -match 'Config\.Labels') { return (ConvertTo-Json -InputObject $c.Config.Labels -Compress -Depth 10) }
    if ($tpl -match 'NetworkSettings\.Networks') { return (ConvertTo-Json -InputObject $c.NetworkSettings.Networks -Compress -Depth 10) }
    return (ConvertTo-Json -InputObject @($c) -Compress -Depth 10)
  }
  if ($rest[0] -eq 'network' -and $rest[1] -eq 'inspect') {
    $n = Find-Net $ref
    if (-not $n) { $global:LASTEXITCODE = 1; return "Error response from daemon: network $ref not found" }
    if ($tpl -match '\.Labels') { return (ConvertTo-Json -InputObject $n.Labels -Compress -Depth 10) }
    return (ConvertTo-Json -InputObject @($n) -Compress -Depth 10)
  }
  $global:LASTEXITCODE = 1
  return "stand-in: unexpected docker call: $($a -join ' ')"
}

$own = @{ 'ai-stack.harness.owner' = 'smoke-check-test' }
$compose = @{ 'ai-stack.harness.owner' = 'smoke-check-test'; 'com.docker.compose.project' = 'rwpt-fakelive' }
$global:SmokeCheckWorld.networks = @(
  (New-Net 'rwpt-net'),
  (New-Net 'rwpt-net2'),
  (New-Net 'rwpt-cnet' @{ 'com.docker.compose.project' = 'rwpt-probe' }),
  (New-Net 'ai-stack_llm-gateway'),
  (New-Net 'open-brain_app-net'),
  (New-Net 'host' @{} 'host'),
  (New-Net 'none' @{} 'null'),
  (New-Net 'bridge' @{} 'bridge' $true),
  (New-Net 'rwpt-defbr' @{} 'bridge' $true),
  (New-Net 'rwpt-macvlan' @{} 'macvlan')
)
$victim = New-Ctr 'rwpt-victim' $compose 'rwpt-net' @('rwpt-net')
$clean = New-Ctr 'rwpt-ok' $own 'rwpt-net' @('rwpt-net')
$global:SmokeCheckWorld.containers = @(
  $victim, $clean,
  (New-Ctr 'rwpt-twonets' $own 'rwpt-net' @('rwpt-net', 'rwpt-net2')),
  (New-Ctr 'rwpt-cmp' $compose 'rwpt-net' @('rwpt-net')),
  (New-Ctr 'rwpt-oncnet' $own 'rwpt-cnet' @('rwpt-cnet')),
  (New-Ctr 'rwpt-onaistack' $own 'ai-stack_llm-gateway' @('ai-stack_llm-gateway')),
  (New-Ctr 'rwpt-onopenbrain' $own 'rwpt-net' @('rwpt-net', 'open-brain_app-net')),
  (New-Ctr 'rwpt-nolab' @{} 'rwpt-net' @('rwpt-net')),
  (New-Ctr 'rwpt-hostnet' $own 'host' @('host')),
  (New-Ctr 'rwpt-nonenet' $own 'none' @('none')),
  (New-Ctr 'rwpt-defaultbr' $own 'bridge' @('bridge')),
  (New-Ctr 'rwpt-defaultmode' $own 'default' @('bridge')),
  (New-Ctr 'rwpt-side' $own "container:$($victim.Id)" @()),
  (New-Ctr 'rwpt-side-clean' $own "container:$($clean.Id)" @()),
  (New-Ctr 'rwpt-side-missing' $own "container:$('f' * 64)" @()),
  (New-Ctr 'rwpt-side-byname' $own 'container:rwpt-victim' @()),
  (New-Ctr 'rwpt-emptynets' $own 'rwpt-net' @()),
  (New-Ctr 'rwpt-modeelsewhere' $own 'rwpt-net2' @('rwpt-net')),
  (New-Ctr 'rwpt-onmacvlan' $own 'rwpt-macvlan' @('rwpt-macvlan')),
  (New-Ctr 'rwpt-ondefbr' $own 'rwpt-defbr' @('rwpt-defbr')),
  (New-Ctr 'rwpt-pidhost' $own 'rwpt-net' @('rwpt-net') @{ pid = 'host' }),
  (New-Ctr 'rwpt-pidpeer' $own 'rwpt-net' @('rwpt-net') @{ pid = "container:$($victim.Id)" }),
  (New-Ctr 'rwpt-ipchost' $own 'rwpt-net' @('rwpt-net') @{ ipc = 'host' }),
  (New-Ctr 'rwpt-utshost' $own 'rwpt-net' @('rwpt-net') @{ uts = 'host' }),
  # a fixture-NAMED base-URL host that shares a compose container's namespace
  (New-Ctr 'rwp0-wb-sidecar' $own "container:$($victim.Id)" @()),
  (New-Ctr 'rwp0-wb-compose' $compose 'rwpt-net' @('rwpt-net')),
  (New-Ctr 'rwp0-wb-ok' $own 'rwpt-net' @('rwpt-net'))
)
$global:SmokeCheckWorld.timeout = @('rwpt-silent')

$script:n = 0
function Invoke-Smoke([string]$container, [string]$url) {
  $script:n++
  $dir = Join-Path $root ("cap-{0:d3}" -f $script:n)
  $global:LASTEXITCODE = 0
  $o = ''
  try { $o = (& $Smoke -Capture $dir -ExecContainer $container -BaseUrl $url *>&1 | Out-String -Width 4096) } catch { $o += "THREW: $_" }
  return @{ out = $o; rc = $global:LASTEXITCODE; dir = (Test-Path $dir); path = $dir }
}
function Refused([string]$label, [string]$container, [string]$url, [string]$rx) {
  $r = Invoke-Smoke $container $url
  $line = (($r.out -split "`r?`n") | Where-Object { $_ -match 'REFUSED|target:|THREW' } | Select-Object -First 1)
  Check (($r.rc -eq 2) -and -not $r.dir -and ($r.out -match 'REFUSED: not a disposable target without -Live') -and ($r.out -match $rx)) ("{0,-34} rc={1} dir={2} :: {3}" -f $label, $r.rc, $r.dir, $line)
}
function Accepted([string]$label, [string]$container, [string]$url) {
  $r = Invoke-Smoke $container $url
  $tgt = Join-Path $r.path '_target.txt'
  $ok = ($r.rc -eq 0) -and $r.dir -and (Test-Path $tgt) -and ([System.IO.File]::ReadAllText($tgt) -match '(?m)^live: False') -and ($r.out -match 'captured 13 responses')
  Check $ok ("{0,-34} rc={1} dir={2} :: accepted, 13 responses captured" -f $label, $r.rc, $r.dir)
}

try {
  Write-Host "[1] refusal shapes (synthetic docker inspect JSON; smoke script $([System.IO.Path]::GetFileName($Smoke)))"
  $L = 'http://localhost:8000'
  Refused 'compose label (by name)'          'rwpt-cmp'          $L 'belongs to compose project'
  Refused 'compose label (by 12-char id)'    $victim.Id.Substring(0, 12) $L 'belongs to compose project'
  Refused 'on a compose project network'     'rwpt-oncnet'       $L 'of compose project'
  Refused 'on an ai-stack_* network'         'rwpt-onaistack'    $L "live network 'ai-stack_"
  Refused 'on an open-brain* network'        'rwpt-onopenbrain'  $L "live network 'open-brain"
  Refused 'no owner label'                   'rwpt-nolab'        $L 'carries no ai-stack.harness.owner label'
  Refused 'NetworkMode host'                 'rwpt-hostnet'      $L "NetworkMode 'host'"
  Refused 'NetworkMode host, 127.0.0.1'      'rwpt-hostnet'      'http://127.0.0.1:8446' "NetworkMode 'host'"
  Refused 'NetworkMode none'                 'rwpt-nonenet'      $L "NetworkMode 'none'"
  Refused 'NetworkMode bridge (default)'     'rwpt-defaultbr'    $L "NetworkMode 'bridge'"
  Refused 'NetworkMode default'              'rwpt-defaultmode'  $L "NetworkMode 'default'"
  Refused 'container:<compose> (the bypass)' 'rwpt-side'         $L 'shares the network namespace.*that peer: container .* belongs to compose project'
  Refused 'container:<compose> by name'      'rwpt-side-byname'  $L 'shares the network namespace.*belongs to compose project'
  Refused 'container:<clean peer>'           'rwpt-side-clean'   $L 'shares the network namespace.*refused regardless'
  Refused 'container:<missing peer>'         'rwpt-side-missing' $L 'shares the network namespace.*cannot inspect'
  Refused 'empty Networks'                   'rwpt-emptynets'    $L 'attached to no network'
  Refused 'NetworkMode not a joined network' 'rwpt-modeelsewhere' $L 'none of the networks it joined'
  Refused 'non-bridge driver'                'rwpt-onmacvlan'    $L "driver 'macvlan'"
  Refused 'a default-bridge network'         'rwpt-ondefbr'      $L 'default bridge'
  Refused 'PID mode host'                    'rwpt-pidhost'      $L "PID namespace"
  Refused 'PID mode container:<x>'           'rwpt-pidpeer'      $L "PID namespace of another container"
  Refused 'IPC mode host'                    'rwpt-ipchost'      $L "IPC namespace"
  Refused 'UTS mode host'                    'rwpt-utshost'      $L "UTS namespace"
  Refused 'inspect fails (no such)'          'rwpt-nosuch'       $L 'docker cannot inspect container'
  Refused 'unreadable docker answer'         'rwpt-garbled'      $L 'docker cannot inspect container'
  Refused 'docker gives no answer in time'   'rwpt-silent'       $L 'did not answer within'
  Refused 'not a container name'             '/rwpt-ok'          $L 'not a container name or id'
  Refused 'fixture host = compose container' 'rwpt-ok'           'http://rwp0-wb-compose:8000' 'base URL host: container .* belongs to compose project'
  Refused 'fixture host shares a namespace'  'rwpt-ok'           'http://rwp0-wb-sidecar:8000' 'base URL host: container .* shares the network namespace'
  Refused 'fixture host, exec container bad' 'rwpt-side'         'http://rwp0-wb-ok:8000' 'shares the network namespace'

  Write-Host '[2] base-URL spellings of a live target (the tester table; refused before any container check)'
  foreach ($u in @('http://openbrain-workbench:8000', 'http://OPENBRAIN-WORKBENCH:8000', 'http://openbrain-workbench.:8000',
      'http://0123456789ab:8000', 'http://198.51.100.7:8000', 'http://3325256711:8000', 'http://host.docker.internal:8000',
      'http://caddy:8446', 'https://caddy:8446', 'http://openbrain%2Dworkbench:8000', 'http://0.0.0.0:8000', '//openbrain-workbench:8000',
      'http://localhost@openbrain-workbench:8000', 'ftp://localhost/', 'http://localhost.:8000', 'http://[::ffff:198.51.100.7]:8000',
      'http://openbrain-workbench\x:8000', 'http://local host:8000')) {
    Refused "url $u" 'rwpt-ok' $u '(base URL|scheme)'
  }

  Write-Host '[3] accepted: an owner-labelled container in a namespace of its own on a user bridge network'
  Accepted 'disposable, localhost'        'rwpt-ok' 'http://localhost:8000'
  Accepted 'disposable, two user nets'    'rwpt-twonets' 'http://localhost:8000'
  Accepted 'disposable, by full id'       $clean.Id 'http://localhost:8000'
  Accepted 'loopback [::1] (N6)'          'rwpt-ok' 'http://[::1]:8000'
  Accepted 'loopback 127.0.0.1'           'rwpt-ok' 'http://127.0.0.1:8000'
  Accepted 'loopback 127.1'               'rwpt-ok' 'http://127.1:8000'
  Accepted 'loopback LOCALHOST'           'rwpt-ok' 'http://LOCALHOST:8000'
  Accepted 'fixture host, disposable'     'rwpt-ok' 'http://rwp0-wb-ok:8000'

  if (-not $ChildOfRevertCheck) {
    Write-Host '[4] a REAL docker.exe against an endpoint that accepts and never answers (no daemon involved)'
    $exe = @(Get-Command docker -CommandType Application -ErrorAction SilentlyContinue)[0]
    Check ($null -ne $exe) 'docker.exe is on PATH (this case needs the CLI, not a daemon)'
    if ($exe) {
      $listener = New-Object System.Net.Sockets.TcpListener ([System.Net.IPAddress]::Loopback), 0
      $listener.Start()
      $port = $listener.LocalEndpoint.Port
      $dir = Join-Path $root 'cap-silent'
      $outFile = Join-Path $root 'silent.out'
      $saved = $env:DOCKER_HOST
      try {
        $env:DOCKER_HOST = "tcp://127.0.0.1:$port"
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        $p = Start-Process -FilePath powershell -NoNewWindow -PassThru -RedirectStandardOutput $outFile -ArgumentList @(
          '-NoProfile', '-File', "`"$Smoke`"", '-Capture', "`"$dir`"", '-ExecContainer', 'rwpt-silent-real', '-BaseUrl', 'http://localhost:8000', '-DockerTimeoutSec', '3')
        $null = $p.Handle   # (Windows PowerShell: ExitCode stays empty unless the handle is taken while it runs)
        $done = $p.WaitForExit(60000)
        $sw.Stop()
        if (-not $done) { & taskkill /T /F /PID $p.Id 2>&1 | Out-Null }
        $o = if (Test-Path $outFile) { [System.IO.File]::ReadAllText($outFile) } else { '' }
        $line = (($o -split "`r?`n") | Where-Object { $_ -match 'REFUSED' } | Select-Object -First 1)
        Check ($done -and $p.ExitCode -eq 2 -and -not (Test-Path $dir) -and $o -match 'did not answer within 3 s' -and $sw.Elapsed.TotalSeconds -lt 30) ("silent endpoint, -DockerTimeoutSec 3: exited={0} rc={1} in {2:n1} s, dir={3} :: {4}" -f $done, $(if ($done) { $p.ExitCode } else { 'killed' }), $sw.Elapsed.TotalSeconds, (Test-Path $dir), $line)
      } finally {
        $env:DOCKER_HOST = $saved
        $listener.Stop()
      }
    }

    Write-Host '[5] revert check: this test over the script with each half of the check reduced to "return ''"'
    $src = [System.IO.File]::ReadAllText($Smoke)
    foreach ($fn in @('Test-DisposableContainer', 'Test-DisposableTarget')) {
      $rx = "(?m)^(function $fn\([^)]*\) \{)"
      $mut = [regex]::Replace($src, $rx, "`$1 return ''")
      $copy = Join-Path $root "smoke-reverted-$fn.ps1"
      [System.IO.File]::WriteAllText($copy, $mut, (New-Object System.Text.UTF8Encoding $false))
      $o = (& powershell -NoProfile -File $PSCommandPath -Smoke $copy -ChildOfRevertCheck 2>&1 | Out-String -Width 4096); $rc = $LASTEXITCODE
      $fails = ([regex]::Matches($o, '(?m)^\s*FAIL\s')).Count
      Check (($mut -ne $src) -and ($rc -eq 1) -and ($fails -gt 0)) "$fn reduced to return '': this test FAILS (rc $rc, $fails failing case(s))"
    }
  }
} finally {
  Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}
if ($script:fail) { Write-Host "$($script:fail) check(s) FAILED"; exit 1 }
Write-Host 'all checks passed'
exit 0
