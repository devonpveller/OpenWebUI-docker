# smoke-workbench-routers.ps1 - the eleven workbench routers the image test gate does not cover
# (research-workbench P0, rw-p0-contract): notes, sources, notebooks, import, jobs, grounding,
# export, note-refs, note-history, source-commit, note-commit.
#
# Capture before a change, capture after, compare: a changed response FAILS.
#
# THE TARGET IS NEVER A DEFAULT. A capture names its container and base URL, or says -Live:
#
#   # live, through Caddy's tailnet block (Caddy injects X-Brain-Key; READ-ONLY requests). A
#   # deploy-time check made by the gated deploy, never a test:
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.ps1 -Live -Capture <dir-before>
#   ... deploy ...
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.ps1 -Live -Capture <dir-after>
#
#   # against a disposable :wt container (the contract test run does this), no Caddy:
#   ... -ExecContainer <test container> -BaseUrl http://localhost:8000 -KeyEnv WORKBENCH_KEY -Capture <dir>
#
#   # compare - IN-PROCESS, with ONE array argument (powershell -File would bind "-Compare a b" as
#   # Compare=@(a) and b to -Capture; this script refuses that combination):
#   & scripts\checks\smoke-workbench-routers.ps1 -Compare @('<dir-before>', '<dir-after>')
#
# Refused (exit 2): -Capture with -Compare; -Compare with other than two directories; a capture
# with no -ExecContainer/-BaseUrl and no -Live; without -Live, any target that is not PROVABLY
# disposable (see "THE DISPOSABLE-TARGET CHECK" below: a loopback or fixture-named base URL host,
# an exec container docker reports as harness-labelled, in no compose project, in a network
# namespace of its own on user bridge networks that are no compose project's or ai-stack_*,
# docker answering within -DockerTimeoutSec); -Live together with an explicit target. Every
# capture prints its target and writes it to <dir>\_target.txt, so a caller can check where the
# responses came from.
#
# HOW A REQUEST IS MADE: `docker exec <ExecContainer> deno eval` runs fetch() from inside a
# container that can reach the target (with -Live: the openbrain-workbench container, which
# shares app-net with caddy, fetching http://caddy:8446). The requests are fixed below and NONE of them
# mutates anything: GETs, plus two POSTs the router rejects before any side effect (import with
# no file and no url -> 400 before a job row exists, import.ts; note-commit with a traversal path
# -> 400 before git runs, note-history.ts notePath). source-commit has no rejecting request (any
# POST commits dirty source heads), so it is probed with a GET, which the router does not route
# and the app answers with its fixed 404 - the mount is all a read-only smoke can see there.
param(
  [string]$Capture = '',
  [string[]]$Compare = @(),
  # No defaults: a capture names its target, or says -Live (rw-p0-contract attempt 2, tester F1).
  [string]$ExecContainer = '',
  [string]$BaseUrl = '',
  [string]$KeyEnv = '',
  [switch]$Live,
  # Every docker call is bounded (rw-p0b-purge-door attempt 2, tester finding N5): an inspect
  # that does not answer within -DockerTimeoutSec is a refusal; the capture's docker exec gets
  # -ExecTimeoutSec.
  [int]$DockerTimeoutSec = 20,
  [int]$ExecTimeoutSec = 600
)
$ErrorActionPreference = 'Stop'

$LiveContainer = 'openbrain-workbench'
$LiveBaseUrl = 'http://caddy:8446'
function Refuse([string]$why) { Write-Host "REFUSED: $why"; exit 2 }

# THE DISPOSABLE-TARGET CHECK (a capture without -Live; rw-p0b-purge-door, tester finding T2-2,
# and attempt 2's network-namespace bypass). An allow-list proven against docker, not a list of
# live names:
#   1. the base URL is plain http(s) with no user info, and its host is loopback (localhost, an
#      IPv4 127.x address, ::1 in any spelling - the exec container itself) or a fixture name
#      (^rwp[0-9a-z]*-..., the disposable runs' container names); a fixture-named host must pass
#      2. as a container too;
#   2. docker inspects the exec container (an id or a name) and it must:
#      - carry the harness owner label (ai-stack.harness.owner) and NO com.docker.compose.project
#        label (every live plane - open-brain and the ai-stack planes - is a compose project);
#      - own its network namespace: HostConfig.NetworkMode names a user network the container
#        itself joined. 'host', 'none', 'bridge'/'default' and 'container:<x>' are refused - a
#        container that shares another's namespace answers 'localhost' with THAT container's
#        services (attempt 1's bypass: NetworkMode container:<compose container>, Networks {}).
#        A 'container:<x>' peer is resolved and checked the same way, so the refusal says what
#        it would have reached; it is refused either way;
#      - be attached to at least one network, each a user-defined bridge network (driver bridge,
#        not the default bridge), none named ai-stack_* / open-brain*, none a compose project's;
#      - share no PID / IPC / UTS namespace with the host or another container (defence in depth).
# Docker unreachable, any inspect failing or not answering within -DockerTimeoutSec, or an
# answer that is not the expected JSON, is a refusal (fail closed). Returns '' when the target
# is disposable, else the reason. Dot-source this script to load the check alone (the unit test,
# scripts/checks/smoke-workbench-routers.test.ps1, does): it returns before doing anything.
$FixtureHost = '^rwp[0-9a-z]*-[a-z0-9][a-z0-9-]*$'
$OwnerLabel = 'ai-stack.harness.owner'
$ComposeLabel = 'com.docker.compose.project'
$DockerName = '^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$'

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
function Get-DockerJson([string[]]$dockerArgs) {
  $r = Invoke-DockerCli $dockerArgs $DockerTimeoutSec
  if (-not $r.ok) { return @{ ok = $false; err = (("$($r.err)" -split "`r?`n") | Where-Object { $_.Trim() } | Select-Object -First 1) } }
  try { $v = @($r.out | ConvertFrom-Json) } catch { return @{ ok = $false; err = "unreadable docker answer: $($r.out)" } }
  if ($v.Count -ne 1 -or $null -eq $v[0]) { return @{ ok = $false; err = "docker answered $($v.Count) objects, not one" } }
  return @{ ok = $true; value = $v[0] }
}
function Get-Keys($obj) { if ($null -eq $obj) { return @() }; return @($obj.PSObject.Properties | ForEach-Object { $_.Name }) }
function Test-SharedNamespace([string]$what, [string]$mode) {
  if ($mode -eq 'host') { return "shares the HOST's $what namespace ($what mode 'host')" }
  if ($mode -like 'container:*') { return "shares the $what namespace of another container ($what mode '$mode')" }
  return ''
}
function Test-DisposableContainer([string]$name, [int]$depth = 0) {
  if ($name -notmatch $DockerName) { return "'$name' is not a container name or id" }
  $i = Get-DockerJson @('inspect', '--type', 'container', $name)
  if (-not $i.ok) { return "docker cannot inspect container '$name' ($($i.err))" }
  $c = $i.value
  if ($null -eq $c.Config -or $null -eq $c.HostConfig -or $null -eq $c.NetworkSettings) { return "docker's answer for '$name' carries no Config/HostConfig/NetworkSettings" }
  $labels = Get-Keys $c.Config.Labels
  if ($labels -contains $ComposeLabel) { return "container '$name' belongs to compose project '$($c.Config.Labels.$ComposeLabel)' (a live plane)" }
  if ($labels -notcontains $OwnerLabel) { return "container '$name' carries no $OwnerLabel label (not a disposable harness container)" }
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
  $nets = @(Get-Keys $c.NetworkSettings.Networks)
  if ($nets.Count -eq 0) { return "container '$name' is attached to no network (NetworkSettings.Networks is empty)" }
  $modeIsJoined = $nets -contains $mode
  foreach ($net in $nets) {
    if ($net -notmatch $DockerName) { return "container '$name' is attached to a network named '$net' (not a plain network name)" }
    if ($net -match '^(ai-stack_|open-brain)') { return "container '$name' is attached to the live network '$net'" }
    $ni = Get-DockerJson @('network', 'inspect', $net)
    if (-not $ni.ok) { return "docker cannot inspect network '$net' of '$name' ($($ni.err))" }
    $n = $ni.value
    if ((Get-Keys $n.Labels) -contains $ComposeLabel) { return "container '$name' is attached to network '$net' of compose project '$($n.Labels.$ComposeLabel)'" }
    if ([string]$n.Driver -ne 'bridge') { return "container '$name' is attached to network '$net' with driver '$($n.Driver)' (only a user-defined bridge network is disposable)" }
    if ($net -eq 'bridge' -or [string]$n.Options.'com.docker.network.bridge.default_bridge' -eq 'true') { return "container '$name' is attached to the default bridge network '$net'" }
    if ($mode.Length -ge 12 -and ([string]$n.Id).StartsWith($mode)) { $modeIsJoined = $true }
  }
  if (-not $modeIsJoined) { return "container '$name' has NetworkMode '$mode', which is none of the networks it joined ($($nets -join ', '))" }
  return ''
}
function Test-DisposableTarget([string]$container, [string]$url) {
  $u = $null
  if ($url -match '[\s\\]' -or -not [System.Uri]::TryCreate($url, [System.UriKind]::Absolute, [ref]$u)) { return "base URL '$url' is not a plain absolute URL" }
  if ($u.Scheme -notin @('http', 'https')) { return "base URL scheme '$($u.Scheme)' is not http(s)" }
  if ($u.UserInfo) { return "base URL '$url' carries user info" }
  $h = $u.Host.ToLowerInvariant()
  # Loopback, normalised first (attempt 2, tester finding N6: Windows PowerShell 5.1 expands
  # [::1] to [0000:...:0001], so a literal list entry never matched): an IP address is loopback
  # when .NET says so; a name only when it is exactly 'localhost'.
  $ip = $null
  $loopback = ($h -eq 'localhost') -or
    ([System.Net.IPAddress]::TryParse($h.Trim('[', ']'), [ref]$ip) -and [System.Net.IPAddress]::IsLoopback($ip))
  if (-not $loopback) {
    if ($h -notmatch $FixtureHost) { return "base URL host '$h' is neither loopback nor a fixture name ($FixtureHost)" }
    $why = Test-DisposableContainer $h
    if ($why) { return "base URL host: $why" }
  }
  return (Test-DisposableContainer $container)
}
# Dot-sourced (the unit test): the check is loaded, nothing runs.
if ($MyInvocation.InvocationName -eq '.') { return }
if ($Capture -and $Compare.Count) { Refuse "-Capture and -Compare together (a '-File ... -Compare a b' call binds b to -Capture; compare in-process with -Compare @(a, b))" }
if ($Compare.Count -and $Compare.Count -ne 2) { Refuse "-Compare takes exactly two directories, got $($Compare.Count)" }
if ($Capture) {
  if ($Live) {
    if ($ExecContainer -or $BaseUrl) { Refuse '-Live names its own target; do not pass -ExecContainer/-BaseUrl with it' }
    $ExecContainer = $LiveContainer; $BaseUrl = $LiveBaseUrl
  } else {
    if (-not $ExecContainer -or -not $BaseUrl) { Refuse 'a capture needs -ExecContainer and -BaseUrl (a disposable target), or -Live for the deployed workbench' }
    # rw-p0b-purge-door (tester finding T2-2): a name blocklist let '-ExecContainer <other name>
    # -BaseUrl http://openbrain-workbench:8000' through. Without -Live the target must PROVE it is
    # disposable - by the base URL's host and by what docker says about the exec container -
    # before anything is created.
    $why = Test-DisposableTarget $ExecContainer $BaseUrl
    if ($why) { Refuse "not a disposable target without -Live: $why" }
  }
  Write-Host ("target: container={0} base={1} live={2}" -f $ExecContainer, $BaseUrl, [bool]$Live)
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

function Get-Responses {
  $cases = @($Cases | ForEach-Object { @{ n = $_.n; m = $_.m; p = $_.p; form = [bool]$_.form } })
  $json = ConvertTo-Json -InputObject $cases -Compress -Depth 5
  # base64: JSON's double quotes would not survive PowerShell 5.1's native-argument quoting
  $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
  # Single quotes only inside the JavaScript: Windows PowerShell 5.1 strips embedded double
  # quotes from an argument it passes to a native command.
  $code = @'
const cases = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob(Deno.env.get('SMOKE_CASES')), (ch) => ch.charCodeAt(0))));
const base = Deno.env.get('SMOKE_BASE');
const keyEnv = Deno.env.get('SMOKE_KEY_ENV') || '';
const out = [];
for (const c of cases) {
  const headers = {};
  if (keyEnv && Deno.env.get(keyEnv)) headers['X-Brain-Key'] = Deno.env.get(keyEnv);
  const init = { method: c.m, headers };
  if (c.form) init.body = new FormData();
  const r = await fetch(base + c.p, init);
  out.push({ n: c.n, status: r.status, type: (r.headers.get('content-type') || '').split(';')[0], body: await r.text() });
}
console.log(JSON.stringify(out));
'@
  $r = Invoke-DockerCli @('exec', '-e', "SMOKE_CASES=$b64", '-e', "SMOKE_BASE=$BaseUrl", '-e', "SMOKE_KEY_ENV=$KeyEnv", $ExecContainer, 'deno', 'eval', $code) $ExecTimeoutSec
  if (-not $r.ok) { throw "docker exec $ExecContainer failed: $($r.err)" }
  return ((@($r.out -split "`r?`n") | Where-Object { "$_".StartsWith('[') }) | Select-Object -Last 1 | ConvertFrom-Json)
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
  New-Item -ItemType Directory -Force $Capture | Out-Null
  $resp = Get-Responses
  if (@($resp).Count -ne $Cases.Count) { throw "expected $($Cases.Count) responses, got $(@($resp).Count)" }
  foreach ($r in $resp) {
    $text = "status: $($r.status)`ntype: $($r.type)`n`n" + (Normalize $r.type $r.body) + "`n"
    [System.IO.File]::WriteAllText((Join-Path $Capture "$($r.n).txt"), $text, (New-Object System.Text.UTF8Encoding $false))
    Write-Host ("  {0,-24} {1}" -f $r.n, $r.status)
  }
  [System.IO.File]::WriteAllText((Join-Path $Capture '_target.txt'), "container: $ExecContainer`nbase: $BaseUrl`nlive: $([bool]$Live)`n", (New-Object System.Text.UTF8Encoding $false))
  Write-Host "captured $($Cases.Count) responses from $ExecContainer ($BaseUrl) into $Capture"
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

Refuse 'usage: -Capture <dir> (-ExecContainer <c> -BaseUrl <url> | -Live)   or   -Compare @(<dir-before>, <dir-after>)'
