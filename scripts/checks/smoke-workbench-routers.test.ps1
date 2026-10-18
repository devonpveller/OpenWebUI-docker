# smoke-workbench-routers.test.ps1 - the smoke script's disposable-target check and its owned fetch
# path, without a Docker daemon (rw-p0b-purge-door attempts 2 and 3).
#
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.test.ps1 [-Smoke <a copy of the smoke script>]
#
# How: a 'docker' FUNCTION in this session stands in for docker.exe (the smoke script calls a
# 'docker' that is not an application as it is). It keeps a small synthetic docker world - containers,
# networks, volumes - and answers what the script asks: inspect (full, and the NetworkMode template),
# network inspect (with its endpoints), volume inspect, ps, network create / connect / disconnect / rm,
# create / start -a / rm of the script's fetch container (start -a answers thirteen canned responses
# naming the address they were fetched from). Hooks make docker misbehave: a CLI that injects
# HTTP_PROXY into what it creates, another container joining the script's network, an address
# outside the subnet, a target that changes during the fetch, another image. Each case runs the
# smoke script in-process exactly as a caller does and checks: refused = exit 2, a REFUSED line
# naming the reason, the capture directory NOT created, and nothing of the script's left in the
# world; accepted = exit 0, the capture written, every response fetched from the target's address
# on the script's network, nothing left. Nothing here reaches a container.
#
# Sections: [1] the target refusal shapes; [2] the base-URL and argument table; [3] the owned fetch
# path's own refusals (tampering); [4] the accepted shapes; [5] a REAL docker.exe against an endpoint
# that accepts and never answers (no daemon involved); [6] the revert check - this test over mutated
# copies of the script (Test-DisposableContainer, Test-TargetComponent, Test-DisposableTarget and
# Test-FetchContainer each reduced to "return ''") must FAIL.
# Exit 0 = every check passed.
param(
  [string]$Smoke = (Join-Path $PSScriptRoot 'smoke-workbench-routers.ps1'),
  # used by section [6] for its child runs only; the top-level run never skips anything
  [switch]$ChildOfRevertCheck
)
$ErrorActionPreference = 'Stop'
$script:fail = 0
function Check([bool]$ok, [string]$m) { if ($ok) { Write-Host "  PASS  $m" } else { Write-Host "  FAIL  $m"; $script:fail++ } }
$root = Join-Path $env:TEMP ('smoke-check-test-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $root | Out-Null
$FetchImageExpected = 'denoland/deno@sha256:a651423f2bb5aff4f15e86bcbaf364db7b7df4aab52dbab8d6fef932d5d81da1'

# ---------------------------------------------------------------- the synthetic docker world
function New-Id { return -join ((1..64) | ForEach-Object { '0123456789abcdef'[(Get-Random -Maximum 16)] }) }
$global:SW = @{ containers = [System.Collections.ArrayList]@(); networks = [System.Collections.ArrayList]@(); volumes = @{}; timeout = @(); hooks = @{}; calls = [System.Collections.ArrayList]@() }
function New-Net([string]$name, [hashtable]$labels = @{}, [string]$driver = 'bridge', [bool]$defaultBridge = $false, [bool]$internal = $true, [string]$subnet = '') {
  $opts = @{}; if ($defaultBridge) { $opts['com.docker.network.bridge.default_bridge'] = 'true' }
  $n = [pscustomobject]@{ Name = $name; Id = (New-Id); Driver = $driver; Internal = $internal; Labels = $labels; Options = $opts; Subnet = $subnet; next = 2 }
  [void]$global:SW.networks.Add($n); return $n
}
function New-Ctr([string]$name, [hashtable]$labels, [string]$mode, [string[]]$nets, [hashtable]$x = @{}) {
  $n = [ordered]@{}; foreach ($v in $nets) { $n[$v] = '192.0.2.10' }
  $c = [pscustomobject]@{ Id = (New-Id); Name = $name; Labels = $labels; Mode = $mode; Nets = $n
    Running = $(if ($x.ContainsKey('running')) { $x.running } else { $true }); Env = @($x.env); Pid = [string]$x.pid; Ipc = $(if ($x.ipc) { $x.ipc } else { 'private' }); Uts = [string]$x.uts
    Privileged = [bool]$x.privileged; CapAdd = @($x.capadd); Mounts = @($x.mounts); Image = 'rwpt-image'; User = ''; Readonly = $false; CapDrop = @(); Entrypoint = @(); ExtraHosts = @(); Dns = @()
  }
  [void]$global:SW.containers.Add($c); return $c
}
function Find-Ctr([string]$ref) {
  foreach ($c in $global:SW.containers) { if ($c.Name -ceq $ref -or $c.Id -eq $ref -or ($ref.Length -ge 12 -and $c.Id.StartsWith($ref))) { return $c } }
  return $null
}
function Find-Net([string]$ref) { foreach ($n in $global:SW.networks) { if ($n.Name -ceq $ref -or $n.Id -eq $ref) { return $n } }; return $null }
function ConvertTo-Inspect($c) {
  $nets = [ordered]@{}; foreach ($k in $c.Nets.Keys) { $nets[$k] = [pscustomobject]@{ IPAddress = $c.Nets[$k] } }
  return [pscustomobject]@{
    Id = $c.Id; Name = "/$($c.Name)"; State = [pscustomobject]@{ Running = $c.Running }
    Config = [pscustomobject]@{ Labels = [pscustomobject]$c.Labels; Env = @($c.Env); Image = $c.Image; User = $c.User; Entrypoint = @($c.Entrypoint) }
    HostConfig = [pscustomobject]@{ NetworkMode = $c.Mode; PidMode = $c.Pid; IpcMode = $c.Ipc; UTSMode = $c.Uts; Privileged = $c.Privileged; CapAdd = @($c.CapAdd); CapDrop = @($c.CapDrop)
      ReadonlyRootfs = $c.Readonly; ExtraHosts = @($c.ExtraHosts); Dns = @($c.Dns); DnsSearch = @(); DnsOptions = @(); Binds = $null; VolumesFrom = $null; Links = $null }
    Mounts = @($c.Mounts); NetworkSettings = [pscustomobject]@{ Networks = [pscustomobject]$nets }
  }
}
function ConvertTo-NetInspect($n) {
  $eps = [ordered]@{}
  foreach ($c in $global:SW.containers) { if ($c.Running -and $c.Nets.Contains($n.Name)) { $eps[$c.Id] = [pscustomobject]@{ Name = $c.Name } } }
  $ipam = @(); if ($n.Subnet) { $ipam = @([pscustomobject]@{ Subnet = $n.Subnet }) }
  return [pscustomobject]@{ Name = $n.Name; Id = $n.Id; Driver = $n.Driver; Internal = $n.Internal; Labels = [pscustomobject]$n.Labels; Options = [pscustomobject]$n.Options
    IPAM = [pscustomobject]@{ Config = $ipam }; Containers = [pscustomobject]$eps }
}
function Get-ArgValue([string[]]$a, [string]$flag) { $i = [array]::IndexOf($a, $flag); if ($i -ge 0) { return $a[$i + 1] }; return $null }
function Get-ArgValues([string[]]$a, [string]$flag) { $out = @(); for ($i = 0; $i -lt $a.Count - 1; $i++) { if ($a[$i] -eq $flag) { $out += $a[$i + 1]; $i++ } }; return $out }
function Get-Labels([string[]]$a) { $h = @{}; foreach ($l in (Get-ArgValues $a '--label')) { $kv = $l -split '=', 2; $h[$kv[0]] = $kv[1] }; return $h }
# The stand-in.
function docker {
  $a = @($args | ForEach-Object { "$_" })
  [void]$global:SW.calls.Add(($a -join ' '))
  $global:LASTEXITCODE = 0
  $ref = $a[-1]
  if ($global:SW.timeout -contains $ref) { $global:LASTEXITCODE = 124; return 'stand-in: no answer' }
  switch ($a[0]) {
    'inspect' {
      $tpl = Get-ArgValue $a '-f'
      $c = Find-Ctr $ref
      if ($ref -eq 'rwpt-garbled') { return 'this is not json {' }
      if (-not $c) { $global:LASTEXITCODE = 1; return "Error response from daemon: No such container: $ref" }
      if ($tpl) { return "$($c.Id) $($c.Mode)" }
      return (ConvertTo-Json -InputObject @(ConvertTo-Inspect $c) -Compress -Depth 10)
    }
    'ps' { return (@($global:SW.containers | ForEach-Object { "$($_.Id)|$(@($_.Nets.Keys) -join ',')" }) -join "`n") }
    'volume' {
      $v = $global:SW.volumes[$ref]
      if ($null -eq $v) { $global:LASTEXITCODE = 1; return "Error response from daemon: get $ref`: no such volume" }
      return (ConvertTo-Json -InputObject @([pscustomobject]@{ Name = $ref; Labels = [pscustomobject]$v }) -Compress -Depth 5)
    }
    'network' {
      switch ($a[1]) {
        'inspect' {
          $n = Find-Net $ref
          if (-not $n) { $global:LASTEXITCODE = 1; return "Error response from daemon: network $ref not found" }
          return (ConvertTo-Json -InputObject @(ConvertTo-NetInspect $n) -Compress -Depth 10)
        }
        'create' {
          if (Find-Net $ref) { $global:LASTEXITCODE = 1; return 'Error response from daemon: network exists' }
          $n = New-Net $ref (Get-Labels $a) (Get-ArgValue $a '--driver') $false ($a -contains '--internal') (Get-ArgValue $a '--subnet')
          if ($global:SW.hooks.netDriver) { $n.Driver = $global:SW.hooks.netDriver }
          return $n.Id
        }
        'connect' {
          $n = Find-Net $a[2]; $c = Find-Ctr $a[3]
          if (-not $n -or -not $c) { $global:LASTEXITCODE = 1; return 'Error response from daemon: no such network or container' }
          $ip = ($n.Subnet -replace '\.0/\d+$', '') + ".$($n.next)"; $n.next++
          if ($global:SW.hooks.badIp) { $ip = '198.51.100.9' }
          $c.Nets[$n.Name] = $ip
          if ($global:SW.hooks.joinOnConnect) { $j = Find-Ctr $global:SW.hooks.joinOnConnect; $j.Nets[$n.Name] = ($n.Subnet -replace '\.0/\d+$', '') + '.99' }
          return ''
        }
        'disconnect' {
          $n = Find-Net $a[3]; $c = Find-Ctr $a[4]
          if ($n -and $c) { $c.Nets.Remove($n.Name) }
          return ''
        }
        'rm' {
          $n = Find-Net $ref
          if (-not $n) { $global:LASTEXITCODE = 1; return "Error response from daemon: network $ref not found" }
          foreach ($c in $global:SW.containers) { $c.Nets.Remove($n.Name) }
          $global:SW.networks.Remove($n); return $ref
        }
      }
    }
    'create' {
      $i = [array]::IndexOf($a, '--entrypoint')
      $c = New-Ctr (Get-ArgValue $a '--name') (Get-Labels $a) (Get-ArgValue $a '--network') @((Get-ArgValue $a '--network')) @{ running = $false; env = @(Get-ArgValues $a '-e') }
      $c.Nets[(Get-ArgValue $a '--network')] = ''
      $c.Image = $a[$i + 2]; $c.User = Get-ArgValue $a '--user'; $c.Readonly = ($a -contains '--read-only'); $c.CapDrop = @(Get-ArgValues $a '--cap-drop'); $c.Entrypoint = @($a[$i + 1])
      $c.ExtraHosts = @(Get-ArgValues $a '--add-host'); $c.Dns = @(Get-ArgValues $a '--dns')
      if ($global:SW.hooks.injectProxy) { $c.Env += 'HTTP_PROXY=http://198.51.100.7:3128' }
      if ($global:SW.hooks.otherImage) { $c.Image = 'rwpt-other:latest' }
      $global:SW.lastFetch = $c
      return $c.Id
    }
    'start' {
      $c = Find-Ctr $ref
      if (-not $c) { $global:LASTEXITCODE = 1; return 'Error response from daemon: no such container' }
      $envh = @{}; foreach ($e in $c.Env) { $kv = "$e" -split '=', 2; $envh[$kv[0]] = $kv[1] }
      $cases = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($envh['SMOKE_CASES'])) | ConvertFrom-Json
      $body = ConvertTo-Json -InputObject @{ stub = $true; base = $envh['SMOKE_BASE']; key = $envh['SMOKE_KEY'] } -Compress
      $out = @($cases | ForEach-Object { @{ n = $_.n; status = 200; type = 'application/json'; body = $body } })
      if ($global:SW.hooks.afterStart) { & $global:SW.hooks.afterStart }
      return (ConvertTo-Json -InputObject $out -Compress -Depth 5)
    }
    'rm' {
      $c = Find-Ctr $ref
      if ($c) { $global:SW.containers.Remove($c) }
      return $ref
    }
  }
  $global:LASTEXITCODE = 1
  return "stand-in: unexpected docker call: $($a -join ' ')"
}

$own = @{ 'ai-stack.harness.owner' = 'smoke-check-test' }
$other = @{ 'ai-stack.harness.owner' = 'another-session' }
$compose = @{ 'ai-stack.harness.owner' = 'smoke-check-test'; 'com.docker.compose.project' = 'rwpt-fakelive' }
$null = New-Net 'rwpt-net'; $null = New-Net 'rwpt-net2'; $null = New-Net 'rwpt-shared'; $null = New-Net 'rwpt-isle'; $null = New-Net 'rwpt-isle2'; $null = New-Net 'rwpt-isle3'; $null = New-Net 'rwpt-isle4'
$null = New-Net 'rwpt-cnet' @{ 'com.docker.compose.project' = 'rwpt-probe' }
$null = New-Net 'rwpt-svcnet' @{ 'com.docker.compose.network' = 'default' }
$null = New-Net 'ai-stack_llm-gateway'; $null = New-Net 'open-brain_app-net'
$null = New-Net 'host' @{} 'host'; $null = New-Net 'none' @{} 'null'; $null = New-Net 'bridge' @{} 'bridge' $true $false
$null = New-Net 'rwpt-defbr' @{} 'bridge' $true; $null = New-Net 'rwpt-macvlan' @{} 'macvlan'; $null = New-Net 'rwpt-open' @{} 'bridge' $false $false
# the accepted target and its clean neighbourhood
$clean = New-Ctr 'rwpt-ok' $own 'rwpt-net' @('rwpt-net') @{ env = @('WORKBENCH_KEY=unit-key') }
$null = New-Ctr 'rwpt-ok-peer' $own 'rwpt-net' @('rwpt-net')
$null = New-Ctr 'rwpt-twonets' $own 'rwpt-net' @('rwpt-net', 'rwpt-net2')
# the stand-in "live" containers live on rwpt-cnet / alone; the shapes below reach them
$victim = New-Ctr 'rwpt-victim' $compose 'rwpt-cnet' @('rwpt-cnet', 'rwpt-shared')
# a clean container nobody captures, for the container:<clean peer> shape
$clean2 = New-Ctr 'rwpt-ok2' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-cmp' $compose 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-svconly' @{ 'ai-stack.harness.owner' = 'smoke-check-test'; 'com.docker.compose.service' = 'exec' } 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-casevar' @{ 'Ai-Stack.Harness.Owner' = 'smoke-check-test' } 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-emptyowner' @{ 'ai-stack.harness.owner' = '' } 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-nolab' @{} 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-oncnet' $own 'rwpt-cnet' @('rwpt-cnet') @{ running = $false }
$null = New-Ctr 'rwpt-onsvcnet' $own 'rwpt-svcnet' @('rwpt-svcnet') @{ running = $false }
$null = New-Ctr 'rwpt-onaistack' $own 'ai-stack_llm-gateway' @('ai-stack_llm-gateway') @{ running = $false }
$null = New-Ctr 'rwpt-onopenbrain' $own 'rwpt-isle' @('rwpt-isle', 'open-brain_app-net') @{ running = $false }
$null = New-Ctr 'rwpt-hostnet' $own 'host' @('host') @{ running = $false }
$null = New-Ctr 'rwpt-nonenet' $own 'none' @('none') @{ running = $false }
$null = New-Ctr 'rwpt-defaultbr' $own 'bridge' @('bridge') @{ running = $false }
$null = New-Ctr 'rwpt-defaultmode' $own 'default' @('bridge') @{ running = $false }
$null = New-Ctr 'rwpt-side' $own "container:$($victim.Id)" @() @{ running = $false }
$null = New-Ctr 'rwpt-side-clean' $own "container:$($clean2.Id)" @() @{ running = $false }
$null = New-Ctr 'rwpt-side-missing' $own "container:$('f' * 64)" @() @{ running = $false }
$null = New-Ctr 'rwpt-side-byname' $own 'container:rwpt-victim' @() @{ running = $false }
$null = New-Ctr 'rwpt-emptynets' $own 'rwpt-isle' @() @{ running = $false }
$null = New-Ctr 'rwpt-modeelsewhere' $own 'rwpt-net2' @('rwpt-isle') @{ running = $false }
$null = New-Ctr 'rwpt-onmacvlan' $own 'rwpt-macvlan' @('rwpt-macvlan') @{ running = $false }
$null = New-Ctr 'rwpt-ondefbr' $own 'rwpt-defbr' @('rwpt-defbr') @{ running = $false }
$null = New-Ctr 'rwpt-onopen' $own 'rwpt-open' @('rwpt-open') @{ running = $false }
$null = New-Ctr 'rwpt-pidhost' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; pid = 'host' }
$null = New-Ctr 'rwpt-pidpeer' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; pid = "container:$($victim.Id)" }
$null = New-Ctr 'rwpt-ipchost' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; ipc = 'host' }
$null = New-Ctr 'rwpt-utshost' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; uts = 'host' }
$null = New-Ctr 'rwpt-priv' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; privileged = $true }
$null = New-Ctr 'rwpt-netadmin' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; capadd = @('NET_ADMIN') }
$null = New-Ctr 'rwpt-capall' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; capadd = @('CAP_SYS_ADMIN') }
$null = New-Ctr 'rwpt-sock' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; mounts = @([pscustomobject]@{ Type = 'bind'; Source = '/var/run/docker.sock'; Destination = '/var/run/docker.sock' }) }
$global:SW.volumes['rwpt-composevol'] = @{ 'com.docker.compose.project' = 'rwpt-probe'; 'com.docker.compose.volume' = 'data' }
$global:SW.volumes['rwpt-ownvol'] = @{ 'ai-stack.harness.owner' = 'smoke-check-test' }
$null = New-Ctr 'rwpt-cvol' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; mounts = @([pscustomobject]@{ Type = 'volume'; Name = 'rwpt-composevol'; Source = '/x'; Destination = '/d' }) }
$null = New-Ctr 'rwpt-livevol' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false; mounts = @([pscustomobject]@{ Type = 'volume'; Name = 'open-brain_db-data'; Source = '/x'; Destination = '/d' }) }
# the component shapes: a relay next to a compose container, another owner's peer, two hops, a namespace joiner
$null = New-Ctr 'rwpt-relay' $own 'rwpt-shared' @('rwpt-shared')
$null = New-Ctr 'rwpt-nextto-other' $own 'rwpt-isle2' @('rwpt-isle2')
$null = New-Ctr 'rwpt-others' $other 'rwpt-isle2' @('rwpt-isle2')
$null = New-Ctr 'rwpt-twohop' $own 'rwpt-isle3' @('rwpt-isle3')
$null = New-Ctr 'rwpt-hop' $own 'rwpt-isle3' @('rwpt-isle3', 'rwpt-shared')
$joinTarget = New-Ctr 'rwpt-joined' $own 'rwpt-isle4' @('rwpt-isle4')
$null = New-Ctr 'rwpt-joiner' $compose "container:$($joinTarget.Id)" @() @{ running = $false }
$null = New-Ctr 'rwpt-stopped' $own 'rwpt-isle' @('rwpt-isle') @{ running = $false }
$global:SW.timeout = @('rwpt-silent')
$Sub = '10.99.0.0/24'

$script:n = 0
function Invoke-Smoke([hashtable]$p, [object[]]$extra = @()) {
  $script:n++
  $dir = Join-Path $root ("cap-{0:d3}" -f $script:n)
  $global:LASTEXITCODE = 0
  $o = ''
  # (an array splat binds '-Name' strings POSITIONALLY in-process, so the two extra shapes are spelled out)
  try {
    if ($extra -contains '-ExecContainer') { $o = (& $Smoke -Capture $dir @p -ExecContainer $extra[1] *>&1 | Out-String -Width 4096) }
    elseif ($extra -contains '-Live') { $o = (& $Smoke -Capture $dir @p -Live *>&1 | Out-String -Width 4096) }
    else { $o = (& $Smoke -Capture $dir @p *>&1 | Out-String -Width 4096) }
  } catch { $o += "THREW: $_" }
  $left = @($global:SW.networks | Where-Object { $_.Name -like 'rwp-smoke-*' }) + @($global:SW.containers | Where-Object { $_.Name -like 'rwp-smoke-*' -or @($_.Nets.Keys | Where-Object { $_ -like 'rwp-smoke-*' }).Count })
  return @{ out = $o; rc = $global:LASTEXITCODE; dir = (Test-Path $dir); path = $dir; left = $left.Count }
}
function Refused([string]$label, [hashtable]$p, [string]$rx, [object[]]$extra = @()) {
  $r = Invoke-Smoke $p $extra
  $lines = @($r.out -split "`r?`n")
  $line = @(@($lines | Where-Object { $_ -match 'REFUSED|THREW|FAILED' }) + @($lines | Where-Object { $_ -match 'target:' }))[0]
  Check (($r.rc -eq 2) -and -not $r.dir -and ($r.out -match 'REFUSED') -and ($r.out -match $rx) -and $r.left -eq 0) ("{0,-40} rc={1} dir={2} left={3} :: {4}" -f $label, $r.rc, $r.dir, $r.left, $line)
}
function T([string]$target, [hashtable]$more = @{}) { $h = @{ Target = $target; Subnet = $Sub }; foreach ($k in $more.Keys) { $h[$k] = $more[$k] }; return $h }
function Accepted([string]$label, [hashtable]$p, [string]$basePath = '', [string]$key = '') {
  $global:SW.lastFetch = $null
  $r = Invoke-Smoke $p
  $tgt = Join-Path $r.path '_target.txt'
  $t = if (Test-Path $tgt) { [System.IO.File]::ReadAllText($tgt) } else { '' }
  $nl = Join-Path $r.path 'notes-list.txt'
  $body = if (Test-Path $nl) { [System.IO.File]::ReadAllText($nl) } else { '' }
  $f = $global:SW.lastFetch
  $baseRx = '^http://10\.99\.0\.\d+:8000' + [regex]::Escape($basePath) + '$'
  $ok = ($r.rc -eq 0) -and $r.dir -and ($t -match '(?m)^live: False') -and ($t -match "(?m)^container: $([regex]::Escape($p.Target))\s*$") -and ($r.out -match 'captured 13 responses') -and
    ($body -match '"base":"(http://[^"]+)"') -and ($Matches[1] -match $baseRx) -and ($body -match ('"key":' + $(if ($key) { '"' + $key + '"' } else { '(""|null)' }))) -and
    $f -and ($f.Image -ceq $FetchImageExpected) -and $f.Readonly -and ($f.User -eq '1993:1993') -and $r.left -eq 0
  Check $ok ("{0,-40} rc={1} dir={2} left={3} :: fetched from {4}" -f $label, $r.rc, $r.dir, $r.left, $(if ($body -match '"base":"([^"]+)"') { $Matches[1] } else { '?' }))
}

try {
  Write-Host "[1] target refusal shapes (synthetic docker; smoke script $([System.IO.Path]::GetFileName($Smoke)))"
  Refused 'compose label (by name)'           (T 'rwpt-cmp') 'belongs to compose project'
  Refused 'compose label (by 12-char id)'     (T $victim.Id.Substring(0, 12)) 'belongs to compose project'
  Refused 'compose service label, no project' (T 'rwpt-svconly') "compose label 'com.docker.compose.service'"
  Refused 'owner label in another case'       (T 'rwpt-casevar') 'carries no ai-stack.harness.owner label'
  Refused 'owner label empty'                 (T 'rwpt-emptyowner') 'carries no ai-stack.harness.owner label'
  Refused 'no owner label'                    (T 'rwpt-nolab') 'carries no ai-stack.harness.owner label'
  Refused 'on a compose project network'      (T 'rwpt-oncnet') 'of compose project'
  Refused 'on a compose-labelled network'     (T 'rwpt-onsvcnet') "compose label 'com.docker.compose.network'"
  Refused 'on an ai-stack_* network'          (T 'rwpt-onaistack') "live network 'ai-stack_"
  Refused 'on an open-brain* network'         (T 'rwpt-onopenbrain') "live network 'open-brain"
  Refused 'NetworkMode host'                  (T 'rwpt-hostnet') "NetworkMode 'host'"
  Refused 'NetworkMode none'                  (T 'rwpt-nonenet') "NetworkMode 'none'"
  Refused 'NetworkMode bridge (default)'      (T 'rwpt-defaultbr') "NetworkMode 'bridge'"
  Refused 'NetworkMode default'               (T 'rwpt-defaultmode') "NetworkMode 'default'"
  Refused 'container:<compose> (the bypass)'  (T 'rwpt-side') 'shares the network namespace.*that peer: container .* belongs to compose project'
  Refused 'container:<compose> by name'       (T 'rwpt-side-byname') 'shares the network namespace.*belongs to compose project'
  Refused 'container:<clean peer>'            (T 'rwpt-side-clean') 'shares the network namespace.*refused regardless'
  Refused 'container:<missing peer>'          (T 'rwpt-side-missing') 'shares the network namespace.*cannot inspect'
  Refused 'empty Networks'                    (T 'rwpt-emptynets') 'attached to no network'
  Refused 'NetworkMode not a joined network'  (T 'rwpt-modeelsewhere') 'none of the networks it joined'
  Refused 'non-bridge driver'                 (T 'rwpt-onmacvlan') "driver 'macvlan'"
  Refused 'a default-bridge network'          (T 'rwpt-ondefbr') 'default bridge'
  Refused 'a network that is not --internal'  (T 'rwpt-onopen') 'not --internal'
  Refused 'PID mode host'                     (T 'rwpt-pidhost') 'PID namespace'
  Refused 'PID mode container:<x>'            (T 'rwpt-pidpeer') 'PID namespace of another container'
  Refused 'IPC mode host'                     (T 'rwpt-ipchost') 'IPC namespace'
  Refused 'UTS mode host'                     (T 'rwpt-utshost') 'UTS namespace'
  Refused 'privileged'                        (T 'rwpt-priv') 'is privileged'
  Refused 'NET_ADMIN added'                   (T 'rwpt-netadmin') 'adds the capability NET_ADMIN'
  Refused 'CAP_SYS_ADMIN added'               (T 'rwpt-capall') 'adds the capability CAP_SYS_ADMIN'
  Refused 'docker socket mounted'             (T 'rwpt-sock') 'mounts the docker socket'
  Refused 'a compose volume mounted'          (T 'rwpt-cvol') "mounts volume 'rwpt-composevol'.*compose project"
  Refused 'a live-named volume mounted'       (T 'rwpt-livevol') "live volume 'open-brain_db-data'"
  Refused 'a relay next to a compose peer'    (T 'rwpt-relay') "reaches container 'rwpt-victim'.*compose project"
  Refused "a peer of another owner"           (T 'rwpt-nextto-other') "reaches container 'rwpt-others'.*not owned by 'smoke-check-test'"
  Refused 'two hops to a compose container'   (T 'rwpt-twohop') "reaches container 'rwpt-victim' on network 'rwpt-shared'"
  Refused 'a compose container joins its ns'  (T 'rwpt-joined') 'shares the network namespace of'
  Refused 'not running'                       (T 'rwpt-stopped') 'is not running'
  Refused 'KeyEnv not in the target'          (T 'rwpt-ok' @{ KeyEnv = 'NO_SUCH_KEY' }) 'has no single NO_SUCH_KEY'
  Refused 'inspect fails (no such)'           (T 'rwpt-nosuch') 'docker cannot inspect container'
  Refused 'unreadable docker answer'          (T 'rwpt-garbled') 'docker cannot inspect container'
  Refused 'docker gives no answer in time'    (T 'rwpt-silent') 'did not answer within'
  Refused 'not a container name'              (T '/rwpt-ok') 'not a container name or id'

  Write-Host '[2] base URLs and arguments (refused before anything is created)'
  foreach ($u in @('http://openbrain-workbench:8000', 'http://OPENBRAIN-WORKBENCH:8000', 'http://openbrain-workbench.:8000', 'http://localhost:8000',
      'http://127.0.0.1:8000', 'http://[::1]:8000', 'http://0123456789ab:8000', 'http://198.51.100.7:8000', 'http://3325256711:8000',
      'http://host.docker.internal:8000', 'http://caddy:8446', 'https://caddy:8446', 'http://openbrain%2Dworkbench:8000', 'http://0.0.0.0:8000',
      '//rwpt-ok:8000', 'http://localhost@rwpt-ok:8000', 'ftp://rwpt-ok/', 'http://rwpt-ok.:8000', 'http://rwpt-ok-peer:8000',
      'http://rwpt-ok\x:8000', 'http://rwpt ok:8000', 'http://rwpt-ok:8000/a?b=1', 'http://rwpt-ok:8000/#x', 'http://rwpt-ok:8000/../x', 'http://rwpt-ok:8000/%2e%2e/')) {
    Refused "url $u" (T 'rwpt-ok' @{ BaseUrl = $u }) '(base URL|scheme)'
  }
  Refused '-ExecContainer (removed)'          (T 'rwpt-ok') "unknown argument.*-ExecContainer" @('-ExecContainer', 'rwpt-ok')
  Refused 'no -Target, no -Live'              @{ Subnet = $Sub } 'needs -Target'
  Refused 'no -Subnet'                        @{ Target = 'rwpt-ok' } 'needs -Subnet'
  foreach ($s in @('172.17.0.0/16', '172.17.0.0/24', '172.20.1.0/24', '172.31.255.0/24', '192.168.5.0/24', '10.0.0.0/8', '10.99.0.0/23', '10.99.0.0/29', '198.18.0.7/24', '127.0.0.0/24', '198.18.0.0', '10.999.0.0/24')) {
    Refused "subnet $s"                       @{ Target = 'rwpt-ok'; Subnet = $s } 'Subnet'
  }
  Refused '-Live with -Target'                (T 'rwpt-ok') '-Live names its own target' @('-Live')
  Refused '-KeyEnv not a variable name'       (T 'rwpt-ok' @{ KeyEnv = 'A;B' }) 'not a variable name'

  Write-Host '[3] the owned fetch path refuses what it did not ask for (and removes what it created)'
  $global:SW.hooks = @{ injectProxy = $true }
  Refused 'the CLI injects HTTP_PROXY'        (T 'rwpt-ok') 'fetch container is not what the script asked for: HTTP_PROXY is set'
  $global:SW.hooks = @{ otherImage = $true }
  Refused 'the fetch container has another image' (T 'rwpt-ok') "fetch container is not what the script asked for: image 'rwpt-other:latest'"
  $global:SW.hooks = @{ joinOnConnect = 'rwpt-victim' }
  Refused 'a compose container joins our net' (T 'rwpt-ok') "reaches container 'rwpt-victim' on network 'rwp-smoke-net-"
  $global:SW.hooks = @{ joinOnConnect = 'rwpt-ok-peer' }
  Refused 'a clean container joins our net'   (T 'rwpt-ok') 'has members other than the target'
  $global:SW.hooks = @{ badIp = $true }
  Refused 'the target address is not ours'    (T 'rwpt-ok') "address on the script's network is '198.51.100.9'"
  $global:SW.hooks = @{ netDriver = 'macvlan' }
  Refused 'our network is not a bridge'       (T 'rwpt-ok') 'is not an internal bridge'
  $global:SW.hooks = @{ afterStart = { $v = Find-Ctr 'rwpt-victim'; $v.Nets['rwpt-net'] = '192.0.2.77' } }
  Refused 'the target changes during the fetch' (T 'rwpt-ok') 'after the fetch.*reaches container .rwpt-victim.'
  (Find-Ctr 'rwpt-victim').Nets.Remove('rwpt-net')
  $global:SW.hooks = @{}

  Write-Host '[4] accepted: an owned, compose-free, internal component; fetched by its address on the script network'
  Accepted 'disposable (default base)'        (T 'rwpt-ok')
  Accepted 'disposable, by full id'           (T $clean.Id)
  Accepted 'base URL = the target, port'      (T 'rwpt-ok' @{ BaseUrl = 'http://rwpt-ok:8000' })
  Accepted 'base URL with a path prefix'      (T 'rwpt-ok' @{ BaseUrl = 'http://rwpt-ok:8000/api/v1/' }) '/api/v1'
  Accepted 'with -KeyEnv'                     (T 'rwpt-ok' @{ KeyEnv = 'WORKBENCH_KEY' }) '' 'unit-key'
  Accepted 'disposable, two user nets'        (T 'rwpt-twonets')
  Accepted 'a /28 fetch subnet'               @{ Target = 'rwpt-ok'; Subnet = '10.99.0.0/28' }
  $nets = @((Find-Ctr 'rwpt-ok').Nets.Keys) -join ','
  Check ($nets -eq 'rwpt-net') "the target is back on its own networks only ($nets)"

  if (-not $ChildOfRevertCheck) {
    Write-Host '[5] a REAL docker.exe against an endpoint that accepts and never answers (no daemon involved)'
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
          '-NoProfile', '-File', "`"$Smoke`"", '-Capture', "`"$dir`"", '-Target', 'rwpt-silent-real', '-Subnet', $Sub, '-DockerTimeoutSec', '3')
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

    Write-Host '[6] revert check: this test over the script with each check reduced to "return ''"'
    $src = [System.IO.File]::ReadAllText($Smoke)
    foreach ($fn in @('Test-DisposableContainer', 'Test-TargetComponent', 'Test-DisposableTarget', 'Test-FetchContainer')) {
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
