# smoke-workbench-routers.ps1 - the eleven workbench routers the image test gate does not cover
# (research-workbench P0, rw-p0-contract): notes, sources, notebooks, import, jobs, grounding,
# export, note-refs, note-history, source-commit, note-commit.
#
# Capture before a change, capture after, compare: a changed response FAILS.
#
# THE TARGET IS NEVER A DEFAULT. A capture names its disposable target, or says -Live:
#
#   # live, through Caddy's tailnet block (Caddy injects X-Brain-Key; READ-ONLY requests). A
#   # deploy-time check made by the gated deploy, never a test:
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.ps1 -Live -Capture <dir-before>
#   ... deploy ...
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.ps1 -Live -Capture <dir-after>
#
#   # against a disposable :wt container (the contract test runs do this), no Caddy:
#   ... -Capture <dir> -Target <test container> -Subnet <a.b.c.d/24..28> [-BaseUrl http://<test container>:8000] [-KeyEnv WORKBENCH_KEY]
#
#   # compare - IN-PROCESS, with ONE array argument (powershell -File would bind "-Compare a b" as
#   # Compare=@(a) and b to -Capture; this script refuses that combination):
#   & scripts\checks\smoke-workbench-routers.ps1 -Compare @('<dir-before>', '<dir-after>')
#
# THE SCRIPT OWNS THE FETCH PATH (rw-p0b-purge-door attempt 3). Attempt 2 inspected the container a
# base URL NAMED and fetched from inside a caller-supplied exec container - so --add-host, a network
# alias, HTTP_PROXY, --dns-search, an /etc/hosts edit at run time or host-gateway to a port published
# on 127.0.0.1 decided where the request went, and no docker inspect of anything could see the last
# of those. Now, without -Live:
#   1. -Target names the container to capture; it is VERIFIED (THE DISPOSABLE-TARGET CHECK below);
#   2. the script creates its OWN network (--internal, the bridge driver, the caller's -Subnet, which
#      must be /24 or narrower and outside Docker's default pools and 192.168.0.0/16), CONNECTS the
#      verified target to it, and reads the target's address there from docker inspect;
#   3. the script creates its OWN fetch container from a pinned image ($FetchImage, the workbench's own
#      base image by digest, never pulled) on that network only: read-only rootfs, a non-root user,
#      every capability dropped, no-new-privileges, no --add-host / --dns / mounts, every *_PROXY
#      variable empty and NO_PROXY=*; it inspects what it created and refuses anything else;
#   4. the fetch goes to http://<the target's address on that network>:<port><prefix> - never a name,
#      never a published port, never the host - with redirects not followed;
#   5. the fetch container, the connection and the network are removed (rm -f -v), the target is
#      verified AGAIN, and only then is anything written under -Capture.
# -ExecContainer is gone: no caller container takes part in a capture. -BaseUrl is only a scheme, a
# port and a path prefix on the verified target; a base URL naming any other host is refused.
#
# Refused (exit 2, nothing written): -Capture with -Compare; -Compare with other than two
# directories; an unknown argument (-ExecContainer included); a capture with no -Target and no -Live;
# -Live together with -Target/-BaseUrl/-Subnet; a -Subnet that is missing, wider than /24, not a
# network address, or inside 172.16.0.0/12, 192.168.0.0/16, 0/8, 127/8, 169.254/16 or 224/3; a target
# that is not PROVABLY disposable; a fetch container or network that is not what the script asked
# for; docker not answering within -DockerTimeoutSec. Every capture prints its target and writes it
# to <dir>\_target.txt (the container, its id, the address fetched, the fetch container).
#
# HOW A REQUEST IS MADE: fetch() in deno, in the script's fetch container (with -Live: `docker exec
# openbrain-workbench`, which shares app-net with caddy, fetching http://caddy:8446 - the gated
# deploy's path, unchanged). The requests are fixed below and NONE of them mutates anything: GETs,
# plus two POSTs the router rejects before any side effect (import with no file and no url -> 400
# before a job row exists, import.ts; note-commit with a traversal path -> 400 before git runs,
# note-history.ts notePath). source-commit has no rejecting request (any POST commits dirty source
# heads), so it is probed with a GET, which the router does not route and the app answers with its
# fixed 404 - the mount is all a read-only smoke can see there.
param(
  [string]$Capture = '',
  [string[]]$Compare = @(),
  # No defaults: a capture names its target, or says -Live (rw-p0-contract attempt 2, tester F1).
  [string]$Target = '',
  [string]$BaseUrl = '',
  [string]$Subnet = '',
  [string]$KeyEnv = '',
  [switch]$Live,
  # Every docker call is bounded (rw-p0b-purge-door attempt 2, tester finding N5): a docker call
  # that does not answer within -DockerTimeoutSec is a refusal; the fetch gets -ExecTimeoutSec.
  [int]$DockerTimeoutSec = 20,
  [int]$ExecTimeoutSec = 600
)
$ErrorActionPreference = 'Stop'

$LiveContainer = 'openbrain-workbench'
$LiveBaseUrl = 'http://caddy:8446'
# The fetch image: denoland/deno:2.3.3 (OB1/docker/workbench/Dockerfile's FROM) by digest; never pulled.
$FetchImage = 'denoland/deno@sha256:a651423f2bb5aff4f15e86bcbaf364db7b7df4aab52dbab8d6fef932d5d81da1'
$FetchUser = '1993:1993'
$DefaultPort = 8000
$ProxyVars = @('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy')
function Refuse([string]$why) { Write-Host "REFUSED: $why"; exit 2 }

# THE DISPOSABLE-TARGET CHECK (a capture without -Live; rw-p0b-purge-door T2-2, attempt 2's
# network-namespace bypass, attempt 3's resolution/routing bypasses). An allow-list proven against
# docker, not a list of live names. The target:
#   - carries the harness owner label (ai-stack.harness.owner, matched case-SENSITIVELY, non-empty)
#     and NO com.docker.compose.* label at all (every live plane is a compose project);
#   - owns its network namespace: HostConfig.NetworkMode names a user network it joined ('host',
#     'none', 'bridge'/'default' and 'container:<x>' are refused; a 'container:<x>' peer is resolved
#     so the refusal says what it would have reached); shares no PID / IPC / UTS namespace;
#   - is not privileged, adds no NET_ADMIN / NET_RAW / SYS_ADMIN / ALL capability, mounts no docker
#     socket and no volume of a compose project or named ai-stack* / open-brain*;
#   - is attached only to user-defined bridge networks that are --internal (no route to the host:
#     the A7 shape), none the default bridge, none named ai-stack_* / open-brain*, none a compose
#     project's;
#   - and so is EVERY container reachable from it over those networks (THE COMPONENT: a clean target
#     relaying to a compose peer is refused), each owned by the SAME owner value; and no container
#     anywhere shares the network namespace of a container in the component (its listeners would
#     answer on that container's address).
# Docker unreachable, any inspect failing or not answering within -DockerTimeoutSec, or an answer
# that is not the expected JSON, is a refusal (fail closed). Each function returns '' when the
# target is disposable, else the reason. Dot-source this script to load the check alone (the unit
# test, scripts/checks/smoke-workbench-routers.test.ps1, does): it returns before doing anything.
$OwnerLabel = 'ai-stack.harness.owner'
$ComposeLabel = 'com.docker.compose.project'
$DockerName = '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'
$MaxComponent = 64
$script:Inspected = @{}
$script:NetCache = @{}
$script:Fetch = $null

# A Windows command-line argument (CommandLineToArgvW rules: docker.exe parses its arguments so).
function ConvertTo-WinArg([string]$s) {
  if ($s.Length -gt 0 -and $s -notmatch '[\s"]') { return $s }
  $sb = New-Object System.Text.StringBuilder
  [void]$sb.Append('"'); $bs = 0
  foreach ($ch in $s.ToCharArray()) {
    if ($ch -eq '\') { $bs++; continue }
    if ($ch -eq '"') { [void]$sb.Append('\' * (2 * $bs + 1)); [void]$sb.Append('"') } else { [void]$sb.Append('\' * $bs); [void]$sb.Append($ch) }
    $bs = 0
  }
  [void]$sb.Append('\' * (2 * $bs)); [void]$sb.Append('"')
  return $sb.ToString()
}

# Every docker call of this script goes through here, bounded by $timeoutSec (attempt 2, tester
# finding N5: a docker endpoint that accepted and never answered hung the check for 120 s+).
# Returns @{ ok; rc; out; err; timedOut }. A 'docker' that is not an application on PATH (a
# function in the calling session - the unit test's stand-in) is called as it is; its exit
# code 124 stands for "did not answer in time".
function Invoke-DockerCli([string[]]$dockerArgs, [int]$timeoutSec) {
  $cmd = @(Get-Command docker -ErrorAction SilentlyContinue)[0]
  if (-not $cmd) { return @{ ok = $false; rc = -1; out = ''; err = 'docker is not on PATH'; timedOut = $false } }
  if ($cmd.CommandType -ne 'Application') {
    $ErrorActionPreference = 'Continue'
    $global:LASTEXITCODE = 0
    $raw = (& $cmd @dockerArgs 2>&1 | ForEach-Object { "$_" }) -join "`n"
    $rc = $global:LASTEXITCODE
    if ($rc -eq 124) { return @{ ok = $false; rc = $rc; out = ''; err = "docker did not answer within $timeoutSec s"; timedOut = $true } }
    return @{ ok = ($rc -eq 0); rc = $rc; out = $raw; err = $raw; timedOut = $false }
  }
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = $cmd.Source
  $psi.Arguments = (@($dockerArgs | ForEach-Object { ConvertTo-WinArg $_ }) -join ' ')
  $psi.UseShellExecute = $false
  $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true; $psi.RedirectStandardInput = $true
  $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding $false
  $psi.StandardErrorEncoding = New-Object System.Text.UTF8Encoding $false
  $psi.CreateNoWindow = $true
  $p = New-Object System.Diagnostics.Process
  $p.StartInfo = $psi
  try { [void]$p.Start() } catch { return @{ ok = $false; rc = -1; out = ''; err = "docker did not start: $($_.Exception.Message)"; timedOut = $false } }
  $p.StandardInput.Close()
  $so = $p.StandardOutput.ReadToEndAsync(); $se = $p.StandardError.ReadToEndAsync()
  if (-not $p.WaitForExit([Math]::Max(1, $timeoutSec) * 1000)) {
    try { $p.Kill() } catch { }
    [void]$p.WaitForExit(5000)
    return @{ ok = $false; rc = -1; out = ''; err = "docker did not answer within $timeoutSec s (killed)"; timedOut = $true }
  }
  $p.WaitForExit()
  $out = $so.Result; $err = $se.Result
  return @{ ok = ($p.ExitCode -eq 0); rc = $p.ExitCode; out = $out; err = $(if ($err) { $err } else { $out }); timedOut = $false }
}
function First-Line([string]$s) { return ((("$s" -split "`r?`n") | Where-Object { $_.Trim() }) | Select-Object -First 1) }
function Get-DockerJson([string[]]$dockerArgs) {
  $r = Invoke-DockerCli $dockerArgs $DockerTimeoutSec
  if (-not $r.ok) { return @{ ok = $false; err = (First-Line $r.err) } }
  try { $v = @($r.out | ConvertFrom-Json) } catch { return @{ ok = $false; err = "unreadable docker answer: $(First-Line $r.out)" } }
  if ($v.Count -ne 1 -or $null -eq $v[0]) { return @{ ok = $false; err = "docker answered $($v.Count) objects, not one" } }
  return @{ ok = $true; value = $v[0] }
}
function Get-Keys($obj) { if ($null -eq $obj) { return @() }; return @($obj.PSObject.Properties | ForEach-Object { $_.Name }) }
# Docker label keys are case-sensitive; PowerShell property access is not (attempt 2 near-miss:
# 'Ai-Stack.Harness.Owner' passed -contains). Matched with -ceq.
function Get-LabelValue($labels, [string]$key) {
  if ($null -eq $labels) { return $null }
  foreach ($p in @($labels.PSObject.Properties)) { if ($p.Name -ceq $key) { return [string]$p.Value } }
  return $null
}
# ANY com.docker.compose.* key (attempt 2 near-miss: a service label without a project label passed).
function Get-ComposeWhy($labels) {
  if ($null -eq $labels) { return '' }
  $props = @($labels.PSObject.Properties)
  foreach ($p in $props) { if ($p.Name -eq $ComposeLabel) { return "belongs to compose project '$($p.Value)' (a live plane)" } }
  foreach ($p in $props) { if ($p.Name -like 'com.docker.compose.*') { return "carries the compose label '$($p.Name)' (a compose container)" } }
  return ''
}
function Test-SharedNamespace([string]$what, [string]$mode) {
  if ($mode -eq 'host') { return "shares the HOST's $what namespace ($what mode 'host')" }
  if ($mode -like 'container:*') { return "shares the $what namespace of another container ($what mode '$mode')" }
  return ''
}
# IPv4 a.b.c.d -> [int64], or -1.
function ConvertTo-IPv4Int([string]$s) {
  if ($s -notmatch '^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$') { return [int64]-1 }
  $o = @([int64]$Matches[1], [int64]$Matches[2], [int64]$Matches[3], [int64]$Matches[4])
  if (@($o | Where-Object { $_ -gt 255 }).Count) { return [int64]-1 }
  return ($o[0] * 16777216 + $o[1] * 65536 + $o[2] * 256 + $o[3])
}
# The fetch network's subnet (attempt 2 near-miss: 172.17.0.0/16 was accepted). '' or the reason.
function Test-Subnet([string]$s, [int]$widest = 24, [int]$narrowest = 28) {
  if ($s -notmatch '^([0-9.]+)/(\d{1,2})$') { return "-Subnet '$s' is not a.b.c.d/nn" }
  $v = ConvertTo-IPv4Int $Matches[1]; $p = [int]$Matches[2]
  if ($v -lt 0) { return "-Subnet '$s' is not a.b.c.d/nn" }
  if ($p -lt $widest -or $p -gt $narrowest) { return "-Subnet '$s' is /$p (only /$widest$(if ($narrowest -ne $widest) { " to /$narrowest" }); never wider than /$widest)" }
  $size = [int64][Math]::Pow(2, 32 - $p)
  if ($v % $size -ne 0) { return "-Subnet '$s' is not a network address (host bits set)" }
  foreach ($r in @(@('172.16.0.0', 12, "Docker's default address pools (172.17-172.31)"), @('192.168.0.0', 16, "192.168.0.0/16 (the LAN; Docker's 192.168 /20 pools)"),
      @('0.0.0.0', 8, '0.0.0.0/8'), @('127.0.0.0', 8, 'loopback'), @('169.254.0.0', 16, 'link-local'), @('224.0.0.0', 3, 'multicast / reserved'))) {
    $rs = ConvertTo-IPv4Int $r[0]; $rsize = [int64][Math]::Pow(2, 32 - $r[1])
    if ($v -ge $rs -and $v -lt ($rs + $rsize)) { return "-Subnet '$s' is inside $($r[0])/$($r[1]): $($r[2])" }
  }
  return ''
}
function Test-InSubnet([string]$ip, [string]$subnet) {
  $v = ConvertTo-IPv4Int $ip
  if ($v -lt 0 -or $subnet -notmatch '^([0-9.]+)/(\d{1,2})$') { return $false }
  $base = ConvertTo-IPv4Int $Matches[1]; $size = [int64][Math]::Pow(2, 32 - [int]$Matches[2])
  return ($v -gt $base -and $v -lt ($base + $size - 1))
}
# One network of the target or of its component. '' or the reason; the answer is cached.
function Test-NetworkClean([string]$net, [string]$who) {
  if ($net -notmatch $DockerName) { return "$who is attached to a network named '$net' (not a plain network name)" }
  if ($net -match '^(ai-stack_|open-brain)') { return "$who is attached to the live network '$net'" }
  $ni = Get-DockerJson @('network', 'inspect', $net)
  if (-not $ni.ok) { return "docker cannot inspect network '$net' of $who ($($ni.err))" }
  $n = $ni.value
  $cw = Get-ComposeWhy $n.Labels
  if ($cw) { return "$who is attached to network '$net' of compose project '$($n.Labels.$ComposeLabel)' ($cw)" }
  if ([string]$n.Driver -ne 'bridge') { return "$who is attached to network '$net' with driver '$($n.Driver)' (only a user-defined bridge network is disposable)" }
  if ($net -eq 'bridge' -or [string]$n.Options.'com.docker.network.bridge.default_bridge' -eq 'true') { return "$who is attached to the default bridge network '$net'" }
  if (-not ($n.Internal -eq $true)) { return "$who is attached to network '$net', which is not --internal (a container on it can reach the host, e.g. a port published on 127.0.0.1 through host-gateway)" }
  $script:NetCache[$net] = $n
  return ''
}
function Test-DisposableContainer([string]$name, [int]$depth = 0) {
  if ($name -notmatch $DockerName) { return "'$name' is not a container name or id" }
  $i = Get-DockerJson @('inspect', '--type', 'container', $name)
  if (-not $i.ok) { return "docker cannot inspect container '$name' ($($i.err))" }
  $c = $i.value
  if ($null -eq $c.Config -or $null -eq $c.HostConfig -or $null -eq $c.NetworkSettings) { return "docker's answer for '$name' carries no Config/HostConfig/NetworkSettings" }
  $cw = Get-ComposeWhy $c.Config.Labels
  if ($cw) { return "container '$name' $cw" }
  $owner = Get-LabelValue $c.Config.Labels $OwnerLabel
  if (-not $owner) { return "container '$name' carries no $OwnerLabel label (exact key, non-empty; not a disposable harness container)" }
  $mode = [string]$c.HostConfig.NetworkMode
  if ($mode -like 'container:*') {
    $peer = $mode.Substring(10)
    $peerWhy = if ($depth -ge 3) { 'not resolved (chain too deep)' } else {
      $w = Test-DisposableContainer $peer ($depth + 1)
      if ($w) { "that peer: $w" } else { 'that peer passes the check itself, but a shared namespace is refused regardless' }
    }
    return "container '$name' shares the network namespace of container '$peer' (NetworkMode $mode): its 'localhost' is that container; $peerWhy"
  }
  if ($mode -in @('host', 'none', 'bridge', 'default', '')) { return "container '$name' has NetworkMode '$mode', not a user network of its own" }
  foreach ($ns in @(@('PID', [string]$c.HostConfig.PidMode), @('IPC', [string]$c.HostConfig.IpcMode), @('UTS', [string]$c.HostConfig.UTSMode))) {
    $w = Test-SharedNamespace $ns[0] $ns[1]
    if ($w) { return "container '$name' $w" }
  }
  if ($c.HostConfig.Privileged -eq $true) { return "container '$name' is privileged" }
  foreach ($cap in @($c.HostConfig.CapAdd | Where-Object { $_ })) {
    if (("$cap".ToUpperInvariant() -replace '^CAP_', '') -in @('NET_ADMIN', 'NET_RAW', 'SYS_ADMIN', 'ALL')) { return "container '$name' adds the capability $cap" }
  }
  foreach ($m in @($c.Mounts | Where-Object { $_ })) {
    if ("$($m.Source) $($m.Destination)" -match 'docker\.sock|docker_engine') { return "container '$name' mounts the docker socket ($($m.Source))" }
    if ([string]$m.Type -eq 'volume') {
      if ([string]$m.Name -match '^(ai-stack|open-brain)') { return "container '$name' mounts the live volume '$($m.Name)'" }
      $vi = Get-DockerJson @('volume', 'inspect', [string]$m.Name)
      if (-not $vi.ok) { return "docker cannot inspect volume '$($m.Name)' of '$name' ($($vi.err))" }
      $vw = Get-ComposeWhy $vi.value.Labels
      if ($vw) { return "container '$name' mounts volume '$($m.Name)', which $vw" }
    }
  }
  $nets = @(Get-Keys $c.NetworkSettings.Networks)
  if ($nets.Count -eq 0) { return "container '$name' is attached to no network (NetworkSettings.Networks is empty)" }
  $modeIsJoined = $nets -contains $mode
  foreach ($net in $nets) {
    $w = Test-NetworkClean $net "container '$name'"
    if ($w) { return $w }
    if ($mode.Length -ge 12 -and ([string]$script:NetCache[$net].Id).StartsWith($mode)) { $modeIsJoined = $true }
  }
  if (-not $modeIsJoined) { return "container '$name' has NetworkMode '$mode', which is none of the networks it joined ($($nets -join ', '))" }
  if ($depth -eq 0) { $script:Inspected[$name] = $c }
  return ''
}
# THE COMPONENT: every container reachable from the verified target over its networks, and every
# container sharing the network namespace of one of them. Needs Test-DisposableContainer first.
function Test-TargetComponent([string]$name) {
  $c = $script:Inspected[$name]
  if ($null -eq $c) { return "container '$name' was not verified first" }
  $owner = Get-LabelValue $c.Config.Labels $OwnerLabel
  $seenC = @{}; $seenC[[string]$c.Id] = $true
  $members = @([string]$c.Id); $memberNames = @(([string]$c.Name).TrimStart('/'))
  $queue = New-Object System.Collections.Queue
  foreach ($net in @(Get-Keys $c.NetworkSettings.Networks)) { $queue.Enqueue($net) }
  $seenN = @{}
  while ($queue.Count) {
    $net = [string]$queue.Dequeue()
    if ($seenN.ContainsKey($net)) { continue }
    $seenN[$net] = $true
    if ($seenN.Count -gt $MaxComponent) { return "the networks reachable from '$name' are more than $MaxComponent (not a disposable run)" }
    $w = Test-NetworkClean $net "the component of '$name'"
    if ($w) { return $w }
    foreach ($id in @(Get-Keys $script:NetCache[$net].Containers)) {
      if ($seenC.ContainsKey($id)) { continue }
      $seenC[$id] = $true
      if ($seenC.Count -gt $MaxComponent) { return "the containers reachable from '$name' are more than $MaxComponent (not a disposable run)" }
      $pi = Get-DockerJson @('inspect', '--type', 'container', $id)
      if (-not $pi.ok) { return "docker cannot inspect container $id on network '$net' (a peer of '$name'): $($pi.err)" }
      $p = $pi.value
      $pn = ([string]$p.Name).TrimStart('/')
      $cw = Get-ComposeWhy $p.Config.Labels
      if ($cw) { return "container '$name' reaches container '$pn' on network '$net', which $cw" }
      $po = Get-LabelValue $p.Config.Labels $OwnerLabel
      if ($po -cne $owner) { return "container '$name' reaches container '$pn' on network '$net', which is not owned by '$owner' ($OwnerLabel = '$po')" }
      if ([string]$p.HostConfig.NetworkMode -eq 'host' -or $p.HostConfig.Privileged -eq $true) { return "container '$name' reaches container '$pn' on network '$net', which is on the host network or privileged" }
      $members += [string]$p.Id; $memberNames += $pn
      foreach ($pnet in @(Get-Keys $p.NetworkSettings.Networks)) { $queue.Enqueue($pnet) }
    }
  }
  # No container shares the network namespace of a member: a process there listens on the member's
  # address. A container in 'container:<x>' mode has no network endpoints of its own, so the listing's
  # network column is empty for exactly those (and for a few odd ones); each of them is inspected.
  $ps = Invoke-DockerCli @('ps', '-a', '--no-trunc', '--format', '{{.ID}}|{{.Networks}}') $DockerTimeoutSec
  if (-not $ps.ok) { return "docker cannot list containers ($(First-Line $ps.err))" }
  $rows = @(("$($ps.out)" -split "`r?`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  foreach ($row in $rows) {
    if ($row -notmatch '^([0-9a-f]{12,64})\|(.*)$') { return "docker answered an unreadable container listing row '$row'" }
    $id = $Matches[1]
    if ($Matches[2].Trim()) { continue }
    $one = Invoke-DockerCli @('inspect', '--type', 'container', '-f', '{{.Id}} {{.HostConfig.NetworkMode}}', $id) $DockerTimeoutSec
    if (-not $one.ok) {
      if ("$($one.err)" -match 'No such') { continue }   # removed since the listing
      return "docker cannot read the network mode of container $id ($(First-Line $one.err))"
    }
    if ("$($one.out)".Trim() -notmatch '^([0-9a-f]{64}) (\S+)$') { return "docker answered an unreadable network mode for container $id" }
    $k = $Matches[1]; $m = $Matches[2]
    if ($m -notlike 'container:*') { continue }
    $ref = $m.Substring(10)
    $hit = @($members | Where-Object { $_ -eq $ref -or ($ref.Length -ge 12 -and $_.StartsWith($ref)) }) + @($memberNames | Where-Object { $_ -ceq $ref })
    if ($hit.Count) { return "container $($k.Substring(0, 12)) shares the network namespace of '$ref' in the component of '$name' (its listeners answer on that container's address)" }
  }
  return ''
}
# -BaseUrl: a scheme, a port and a path prefix on the verified target. '' or the reason; sets $script:Fetch.
function Test-BaseUrl([string]$target, [string]$url) {
  $script:Fetch = $null
  if (-not $url) { $script:Fetch = @{ scheme = 'http'; port = $DefaultPort; prefix = '' }; return '' }
  $u = $null
  if ($url -match '[\s\\%]' -or -not [System.Uri]::TryCreate($url, [System.UriKind]::Absolute, [ref]$u)) { return "base URL '$url' is not a plain absolute URL" }
  if ($u.Scheme -notin @('http', 'https')) { return "base URL scheme '$($u.Scheme)' is not http(s)" }
  if ($u.UserInfo) { return "base URL '$url' carries user info" }
  if ($u.Query -or $u.Fragment) { return "base URL '$url' carries a query or fragment" }
  $h = $u.Host.ToLowerInvariant()
  if ($h -cne $target.ToLowerInvariant()) { return "base URL host '$h' is not the target '$target' (the script fetches the verified target by its address on a network of its own; a base URL names no other host)" }
  $prefix = $u.AbsolutePath.TrimEnd('/')
  if ($url -match '/\.\.?(/|$)' -or $prefix -notmatch '^(/[A-Za-z0-9._~-]+)*$') { return "base URL path '$($u.AbsolutePath)' is not a plain path prefix" }
  $script:Fetch = @{ scheme = $u.Scheme; port = $u.Port; prefix = $prefix }
  return ''
}
function Test-DisposableTarget([string]$target, [string]$url) {
  $w = Test-BaseUrl $target $url
  if ($w) { return $w }
  $w = Test-DisposableContainer $target
  if ($w) { return $w }
  return (Test-TargetComponent $target)
}
# What the script created must be what it asked for (a docker CLI config can inject proxy variables
# into every container it creates, for one). '' or the reason.
function Test-FetchContainer($f, [string]$net) {
  if ([string]$f.Config.Image -cne $FetchImage) { return "image '$($f.Config.Image)', not $FetchImage" }
  if ([string]$f.HostConfig.NetworkMode -cne $net) { return "NetworkMode '$($f.HostConfig.NetworkMode)', not '$net'" }
  $fn = @(Get-Keys $f.NetworkSettings.Networks)
  if ($fn.Count -ne 1 -or $fn[0] -cne $net) { return "networks '$($fn -join ', ')', not only '$net'" }
  foreach ($k in @('ExtraHosts', 'Dns', 'DnsSearch', 'DnsOptions', 'CapAdd', 'Binds', 'VolumesFrom', 'Links')) {
    if (@($f.HostConfig.$k | Where-Object { $_ }).Count) { return "HostConfig.$k is set ($(@($f.HostConfig.$k) -join ', '))" }
  }
  if (@($f.Mounts | Where-Object { $_ }).Count) { return 'it has mounts' }
  if ($f.HostConfig.Privileged -eq $true) { return 'it is privileged' }
  if ($f.HostConfig.ReadonlyRootfs -ne $true) { return 'its root filesystem is not read-only' }
  if (@($f.HostConfig.CapDrop) -notcontains 'ALL') { return 'it does not drop every capability' }
  foreach ($ns in @(@('PID', [string]$f.HostConfig.PidMode), @('IPC', [string]$f.HostConfig.IpcMode), @('UTS', [string]$f.HostConfig.UTSMode))) {
    $w = Test-SharedNamespace $ns[0] $ns[1]
    if ($w) { return "it $w" }
  }
  if ([string]$f.Config.User -cne $FetchUser) { return "user '$($f.Config.User)', not $FetchUser" }
  $env = @($f.Config.Env)
  foreach ($v in $ProxyVars) {
    $set = @($env | Where-Object { "$_".StartsWith("$v=") -and "$_".Length -gt ($v.Length + 1) })
    if ($set.Count) { return "$v is set in its environment" }
  }
  if (@($env | Where-Object { $_ -ceq 'NO_PROXY=*' }).Count -ne 1) { return 'NO_PROXY=* is not in its environment' }
  if ((@($f.Config.Entrypoint) -join ' ') -cne 'deno') { return "entrypoint '$(@($f.Config.Entrypoint) -join ' ')', not deno" }
  return ''
}
# Dot-sourced (the unit test): the check is loaded, nothing runs.
if ($MyInvocation.InvocationName -eq '.') { return }
if ($args.Count) { Refuse "unknown argument(s) '$($args -join ' ')' (-ExecContainer was removed in rw-p0b-purge-door attempt 3: the script runs the fetch itself; name the disposable target with -Target <container> -Subnet <a.b.c.d/24>)" }
if ($Capture -and $Compare.Count) { Refuse "-Capture and -Compare together (a '-File ... -Compare a b' call binds b to -Capture; compare in-process with -Compare @(a, b))" }
if ($Compare.Count -and $Compare.Count -ne 2) { Refuse "-Compare takes exactly two directories, got $($Compare.Count)" }
if ($Capture) {
  if ($Live) {
    if ($Target -or $BaseUrl -or $Subnet) { Refuse '-Live names its own target; do not pass -Target/-BaseUrl/-Subnet with it' }
  } else {
    if (-not $Target) { Refuse 'a capture needs -Target <disposable container> and -Subnet <a.b.c.d/24>, or -Live for the deployed workbench' }
    if (-not $Subnet) { Refuse "a capture needs -Subnet <a.b.c.d/24> for the script's own fetch network (never one from Docker's default pools)" }
    $w = Test-Subnet $Subnet
    if ($w) { Refuse $w }
    if ($KeyEnv -and $KeyEnv -notmatch '^[A-Za-z_][A-Za-z0-9_]{0,63}$') { Refuse "-KeyEnv '$KeyEnv' is not a variable name" }
    # rw-p0b-purge-door: the target must PROVE it is disposable before anything is created.
    $why = Test-DisposableTarget $Target $BaseUrl
    if ($why) { Refuse "not a disposable target without -Live: $why" }
  }
  if ($Live) { Write-Host ("target: container={0} base={1} live=True" -f $LiveContainer, $LiveBaseUrl) }
  else { Write-Host ("target: container={0} base={1}://<its address on the script's network>:{2}{3} live=False" -f $Target, $script:Fetch.scheme, $script:Fetch.port, $script:Fetch.prefix) }
}

# name | method | path | form = send an EMPTY multipart body
$Cases = @(
  @{ n = 'notes-list';          m = 'GET';  p = '/workbench/notes' },
  @{ n = 'notes-folders';       m = 'GET';  p = '/workbench/notes/folders' },
  @{ n = 'sources-search';      m = 'GET';  p = '/workbench/sources?q=zz-rw-p0-smoke-no-match' },
  @{ n = 'sources-bad-id';      m = 'GET';  p = '/workbench/sources/not-a-uuid' },
  @{ n = 'notebooks-list';      m = 'GET';  p = '/workbench/notebooks' },
  @{ n = 'import-empty';        m = 'POST'; p = '/workbench/import'; form = $true },
  @{ n = 'jobs-unknown-id';     m = 'GET';  p = '/workbench/jobs/00000000-0000-4000-8000-000000000000' },
  @{ n = 'grounding-bad-id';    m = 'GET';  p = '/workbench/grounding/not-an-int' },
  @{ n = 'export-bad-format';   m = 'GET';  p = '/workbench/export?format=xyz' },
  @{ n = 'note-refs-no-path';   m = 'GET';  p = '/workbench/note-refs' },
  @{ n = 'note-history-no-path'; m = 'GET'; p = '/workbench/note-history' },
  @{ n = 'source-commit-mount'; m = 'GET';  p = '/workbench/source-commit' },
  @{ n = 'note-commit-traversal'; m = 'POST'; p = '/workbench/note-commit?path=../../etc/passwd' }
)

# Single quotes only inside the JavaScript (it is passed as one native argument).
$FetchCode = @'
const cases = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(Deno.env.get('SMOKE_CASES')), (ch) => ch.charCodeAt(0))));
const base = Deno.env.get('SMOKE_BASE');
if (Deno.env.get('SMOKE_OWNED') === '1') {
  for (const v of ['HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy']) {
    if (Deno.env.get(v)) throw new Error('refusing to fetch: ' + v + ' is set in the fetch container');
  }
}
const keyEnv = Deno.env.get('SMOKE_KEY_ENV') || '';
const key = Deno.env.get('SMOKE_KEY') || (keyEnv ? (Deno.env.get(keyEnv) || '') : '');
const out = [];
for (const c of cases) {
  const headers = {};
  if (key) headers['X-Brain-Key'] = key;
  const init = { method: c.m, headers, redirect: 'manual' };
  if (c.form) init.body = new FormData();
  const r = await fetch(base + c.p, init);
  out.push({ n: c.n, status: r.status, type: (r.headers.get('content-type') || '').split(';')[0], body: await r.text() });
}
console.log(JSON.stringify(out));
'@
function Get-CasesB64 {
  $cases = @($Cases | ForEach-Object { @{ n = $_.n; m = $_.m; p = $_.p; form = [bool]$_.form } })
  $json = ConvertTo-Json -InputObject $cases -Compress -Depth 5
  return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}
function ConvertFrom-FetchOutput([string]$out) {
  $line = (@($out -split "`r?`n") | Where-Object { "$_".StartsWith('[') }) | Select-Object -Last 1
  if (-not $line) { throw "the fetch printed no response list: $(First-Line $out)" }
  return ($line | ConvertFrom-Json)
}
# -Live: the gated deploy's path (docker exec in the live workbench, through Caddy).
function Get-LiveResponses {
  $r = Invoke-DockerCli @('exec', '-e', "SMOKE_CASES=$(Get-CasesB64)", '-e', "SMOKE_BASE=$LiveBaseUrl", '-e', "SMOKE_KEY_ENV=$KeyEnv", $LiveContainer, 'deno', 'eval', $FetchCode) $ExecTimeoutSec
  if (-not $r.ok) { throw "docker exec $LiveContainer failed: $(First-Line $r.err)" }
  return (ConvertFrom-FetchOutput $r.out)
}
# Without -Live: the script's own network and fetch container (see the header). Throws 'REFUSE: ...'
# for a verification failure (exit 2) and anything else for a failed run (exit 1); cleans up always.
function Invoke-OwnedFetch {
  $c = $script:Inspected[$Target]
  if ($c.State.Running -ne $true) { throw "REFUSE: target '$Target' is not running" }
  $owner = Get-LabelValue $c.Config.Labels $OwnerLabel
  $key = ''
  if ($KeyEnv) {
    $hit = @(@($c.Config.Env) | Where-Object { "$_".StartsWith("$KeyEnv=") })
    if ($hit.Count -ne 1) { throw "REFUSE: target '$Target' has no single $KeyEnv in its environment" }
    $key = $hit[0].Substring($KeyEnv.Length + 1)
  }
  $rand = -join ((48..57 + 97..102) | Get-Random -Count 10 | ForEach-Object { [char]$_ })
  $net = "rwp-smoke-net-$rand"; $fc = "rwp-smoke-fetch-$rand"
  $labels = @('--label', "$OwnerLabel=$owner", '--label', 'ai-stack.smoke.fetch=true')
  $made = @{ net = $false; connected = $false; ctr = $false }
  $res = @{}
  $cleanupProblems = @()
  try {
    $r = Invoke-DockerCli (@('network', 'create', '--driver', 'bridge', '--internal', '--subnet', $Subnet) + $labels + @($net)) $DockerTimeoutSec
    if (-not $r.ok) { throw "REFUSE: the script's fetch network could not be created ($(First-Line $r.err))" }
    $made.net = $true
    $ni = Get-DockerJson @('network', 'inspect', $net)
    if (-not $ni.ok) { throw "REFUSE: docker cannot inspect the script's network $net ($($ni.err))" }
    $sub = @($ni.value.IPAM.Config | ForEach-Object { [string]$_.Subnet })
    if ([string]$ni.value.Driver -ne 'bridge' -or $ni.value.Internal -ne $true -or $sub.Count -ne 1 -or $sub[0] -ne $Subnet) { throw "REFUSE: the script's network $net is not an internal bridge on $Subnet (driver '$($ni.value.Driver)', internal '$($ni.value.Internal)', subnet '$($sub -join ',')')" }
    $r = Invoke-DockerCli @('network', 'connect', $net, [string]$c.Id) $DockerTimeoutSec
    if (-not $r.ok) { throw "REFUSE: the target could not be connected to the script's network ($(First-Line $r.err))" }
    $made.connected = $true
    # verified AGAIN, now on the script's network too (the target may have changed since)
    $script:Inspected.Remove($Target); $script:NetCache.Clear()
    $w = Test-DisposableContainer $Target
    if (-not $w) { $w = Test-TargetComponent $Target }
    if ($w) { throw "REFUSE: not a disposable target without -Live (on the script's network): $w" }
    $c2 = $script:Inspected[$Target]
    if ([string]$c2.Id -ne [string]$c.Id) { throw "REFUSE: '$Target' is now another container ($($c2.Id))" }
    $ip = [string]$c2.NetworkSettings.Networks.$net.IPAddress
    if (-not (Test-InSubnet $ip $Subnet)) { throw "REFUSE: the target's address on the script's network is '$ip', not inside $Subnet" }
    $members = @(Get-Keys $script:NetCache[$net].Containers)
    if ($members.Count -ne 1 -or $members[0] -ne [string]$c.Id) { throw "REFUSE: the script's network $net has members other than the target ($($members -join ', '))" }
    $base = '{0}://{1}:{2}{3}' -f $script:Fetch.scheme, $ip, $script:Fetch.port, $script:Fetch.prefix
    $envArgs = @()
    foreach ($v in $ProxyVars) { $envArgs += @('-e', "$v=") }
    $envArgs += @('-e', 'NO_PROXY=*', '-e', 'no_proxy=*', '-e', 'DENO_DIR=/tmp/deno-dir', '-e', 'SMOKE_OWNED=1',
      '-e', "SMOKE_CASES=$(Get-CasesB64)", '-e', "SMOKE_BASE=$base", '-e', "SMOKE_KEY=$key")
    $r = Invoke-DockerCli (@('create', '--name', $fc, '--pull', 'never', '--network', $net, '--read-only', '--tmpfs', '/tmp:rw,size=64m',
        '--user', $FetchUser, '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '128', '--memory', '512m') +
      $labels + $envArgs + @('--entrypoint', 'deno', $FetchImage, 'eval', $FetchCode)) $DockerTimeoutSec
    if (-not $r.ok) { throw "REFUSE: the fetch container could not be created from $FetchImage ($(First-Line $r.err))" }
    $made.ctr = $true
    $fi = Get-DockerJson @('inspect', '--type', 'container', $fc)
    if (-not $fi.ok) { throw "REFUSE: docker cannot inspect the fetch container ($($fi.err))" }
    $w = Test-FetchContainer $fi.value $net
    if ($w) { throw "REFUSE: the fetch container is not what the script asked for: $w" }
    $res.fetchId = [string]$fi.value.Id
    $r = Invoke-DockerCli @('start', '-a', $fc) $ExecTimeoutSec
    if (-not $r.ok) { throw "the fetch failed (rc $($r.rc)): $(First-Line $r.err)" }
    $res.resp = ConvertFrom-FetchOutput $r.out
    $res.ip = $ip; $res.base = $base; $res.net = $net; $res.fetch = $fc
  } finally {
    if ($made.ctr) {
      $r = Invoke-DockerCli @('rm', '-f', '-v', $fc) $DockerTimeoutSec
      if (-not $r.ok) { $cleanupProblems += "rm -f -v $fc failed: $(First-Line $r.err)" }
    }
    if ($made.connected) {
      $r = Invoke-DockerCli @('network', 'disconnect', '-f', $net, [string]$c.Id) $DockerTimeoutSec
      if (-not $r.ok) { $cleanupProblems += "network disconnect $net failed: $(First-Line $r.err)" }
    }
    if ($made.net) {
      $r = Invoke-DockerCli @('network', 'rm', $net) $DockerTimeoutSec
      if (-not $r.ok) { $cleanupProblems += "network rm $net failed: $(First-Line $r.err)" }
    }
    $script:CleanupProblems = $cleanupProblems
  }
  return $res
}

# Order-stable form of a body: JSON is re-serialised with sorted keys, text kept as is.
function Normalize([string]$type, [string]$body) {
  if ($type -ne 'application/json') { return $body }
  try {
    $o = $body | ConvertFrom-Json
    return (Sort-Json $o | ConvertTo-Json -Depth 30 -Compress)
  } catch { return $body }
}

function Sort-Json($o) {
  if ($null -eq $o) { return $null }
  if ($o -is [System.Management.Automation.PSCustomObject]) {
    $h = [ordered]@{}
    foreach ($k in ($o.PSObject.Properties.Name | Sort-Object)) { $h[$k] = Sort-Json $o.$k }
    return [pscustomobject]$h
  }
  if ($o -is [System.Array]) { return , @($o | ForEach-Object { Sort-Json $_ }) }
  return $o
}

if ($Capture) {
  $script:CleanupProblems = @()
  try {
    if ($Live) {
      $resp = Get-LiveResponses
      $record = "container: $LiveContainer`nbase: $LiveBaseUrl`nlive: True`n"
    } else {
      $f = Invoke-OwnedFetch
      $resp = $f.resp
      # verified a THIRD time, after the fetch and the cleanup: a target that changed meanwhile
      # is refused and nothing is written
      $script:Inspected.Remove($Target); $script:NetCache.Clear()
      $w = Test-DisposableContainer $Target
      if (-not $w) { $w = Test-TargetComponent $Target }
      if ($w) { throw "REFUSE: not a disposable target without -Live (after the fetch): $w" }
      $record = "container: $Target`nid: $($script:Inspected[$Target].Id)`naddress: $($f.ip) on the script's network $($f.net) ($Subnet), removed after`n" +
        "fetch: $($f.fetch) ($($f.fetchId)) from $FetchImage, created by this script, removed after`nbase: $($f.base)`nlive: False`n"
    }
  } catch {
    $m = "$_"
    foreach ($x in @($script:CleanupProblems)) { Write-Host "CLEANUP FAILED: $x" }
    if ($m.StartsWith('REFUSE: ')) { Refuse $m.Substring(8) }
    Write-Host "FAILED: $m"; exit 1
  }
  if (@($script:CleanupProblems).Count) { foreach ($x in $script:CleanupProblems) { Write-Host "CLEANUP FAILED: $x" }; Write-Host 'FAILED: the script could not remove what it created; nothing written'; exit 1 }
  if (@($resp).Count -ne $Cases.Count) { Write-Host "FAILED: expected $($Cases.Count) responses, got $(@($resp).Count)"; exit 1 }
  New-Item -ItemType Directory -Force $Capture | Out-Null
  foreach ($r in $resp) {
    $text = "status: $($r.status)`ntype: $($r.type)`n`n" + (Normalize $r.type $r.body) + "`n"
    [System.IO.File]::WriteAllText((Join-Path $Capture "$($r.n).txt"), $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host ("  {0,-24} {1}" -f $r.n, $r.status)
  }
  [System.IO.File]::WriteAllText((Join-Path $Capture '_target.txt'), $record, (New-Object System.Text.UTF8Encoding $false))
  Write-Host "captured $($Cases.Count) responses from $(if ($Live) { $LiveContainer } else { $Target }) into $Capture"
  exit 0
}

if ($Compare.Count -eq 2) {
  $fail = 0
  foreach ($c in $Cases) {
    $a = Join-Path $Compare[0] "$($c.n).txt"
    $b = Join-Path $Compare[1] "$($c.n).txt"
    if (-not (Test-Path $a) -or -not (Test-Path $b)) { Write-Host "  FAIL  $($c.n): missing capture"; $fail++; continue }
    $ta = [System.IO.File]::ReadAllText($a); $tb = [System.IO.File]::ReadAllText($b)
    if ($ta -ceq $tb) { Write-Host "  PASS  $($c.n)" } else {
      Write-Host "  FAIL  $($c.n): the response changed"
      Write-Host "    before: $($ta.Substring(0, [Math]::Min(300, $ta.Length)))"
      Write-Host "    after:  $($tb.Substring(0, [Math]::Min(300, $tb.Length)))"
      $fail++
    }
  }
  if ($fail) { Write-Host "$fail of $($Cases.Count) router responses changed"; exit 1 }
  Write-Host "all $($Cases.Count) router responses identical"
  exit 0
}

Refuse 'usage: -Capture <dir> (-Target <c> -Subnet <a.b.c.d/24> [-BaseUrl <url>] [-KeyEnv <var>] | -Live)   or   -Compare @(<dir-before>, <dir-after>)'
