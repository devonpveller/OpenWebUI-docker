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
# an exec container docker reports as harness-labelled, in no compose project and on no compose
# or ai-stack_* network); -Live together with an explicit target. Every
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
  [switch]$Live
)
$ErrorActionPreference = 'Stop'

$LiveContainer = 'openbrain-workbench'
$LiveBaseUrl = 'http://caddy:8446'
function Refuse([string]$why) { Write-Host "REFUSED: $why"; exit 2 }

# THE DISPOSABLE-TARGET CHECK (a capture without -Live; rw-p0b-purge-door, tester finding T2-2).
# An allow-list proven against docker, not a list of live names:
#   1. the base URL is plain http(s) with no user info, and its host is loopback (localhost,
#      127.0.0.1, ::1 - the exec container itself) or a fixture name (^rwp[0-9a-z]*-..., the
#      disposable runs' container names); a fixture-named host must pass 2. as a container too;
#   2. docker inspects the exec container (an id or a name): it carries the harness owner label
#      (ai-stack.harness.owner), carries NO com.docker.compose.project label (every live plane -
#      open-brain and the ai-stack planes - is a compose project), and is attached to no network
#      that is a compose project's or is named ai-stack_* / open-brain*.
# Docker unreachable, or any inspect failing, is a refusal (fail closed). Returns '' when the
# target is disposable, else the reason.
$FixtureHost = '^rwp[0-9a-z]*-[a-z0-9][a-z0-9-]*$'
$OwnerLabel = 'ai-stack.harness.owner'
$ComposeLabel = 'com.docker.compose.project'
function Get-DockerJson([string[]]$dockerArgs) {
  $ErrorActionPreference = 'Continue'
  $raw = (& docker @dockerArgs 2>&1 | ForEach-Object { "$_" }) -join "`n"
  if ($LASTEXITCODE -ne 0) { return @{ ok = $false; err = (($raw -split "`n") | Where-Object { $_.Trim() } | Select-Object -First 1) } }
  try { return @{ ok = $true; value = ($raw | ConvertFrom-Json) } } catch { return @{ ok = $false; err = "unreadable docker answer: $raw" } }
}
function Get-Keys($obj) { if ($null -eq $obj) { return @() }; return @($obj.PSObject.Properties | ForEach-Object { $_.Name }) }
function Test-DisposableContainer([string]$name) {
  $l = Get-DockerJson @('inspect', '--type', 'container', '-f', '{{json .Config.Labels}}', $name)
  if (-not $l.ok) { return "docker cannot inspect container '$name' ($($l.err))" }
  $labels = Get-Keys $l.value
  if ($labels -contains $ComposeLabel) { return "container '$name' belongs to compose project '$($l.value.$ComposeLabel)' (a live plane)" }
  if ($labels -notcontains $OwnerLabel) { return "container '$name' carries no $OwnerLabel label (not a disposable harness container)" }
  $n = Get-DockerJson @('inspect', '--type', 'container', '-f', '{{json .NetworkSettings.Networks}}', $name)
  if (-not $n.ok) { return "docker cannot read the networks of '$name' ($($n.err))" }
  foreach ($net in (Get-Keys $n.value)) {
    if ($net -match '^(ai-stack_|open-brain)') { return "container '$name' is attached to the live network '$net'" }
    $nl = Get-DockerJson @('network', 'inspect', '-f', '{{json .Labels}}', $net)
    if (-not $nl.ok) { return "docker cannot inspect network '$net' of '$name' ($($nl.err))" }
    if ((Get-Keys $nl.value) -contains $ComposeLabel) { return "container '$name' is attached to network '$net' of compose project '$($nl.value.$ComposeLabel)'" }
  }
  return ''
}
function Test-DisposableTarget([string]$container, [string]$url) {
  $u = $null
  if ($url -match '[\s\\]' -or -not [System.Uri]::TryCreate($url, [System.UriKind]::Absolute, [ref]$u)) { return "base URL '$url' is not a plain absolute URL" }
  if ($u.Scheme -notin @('http', 'https')) { return "base URL scheme '$($u.Scheme)' is not http(s)" }
  if ($u.UserInfo) { return "base URL '$url' carries user info" }
  $h = $u.Host.ToLowerInvariant()
  $loopback = $h -in @('localhost', '127.0.0.1', '[::1]', '::1')
  if (-not $loopback) {
    if ($h -notmatch $FixtureHost) { return "base URL host '$h' is neither loopback nor a fixture name ($FixtureHost)" }
    $why = Test-DisposableContainer $h
    if ($why) { return "base URL host: $why" }
  }
  return (Test-DisposableContainer $container)
}
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
  $raw = & docker exec -e "SMOKE_CASES=$b64" -e "SMOKE_BASE=$BaseUrl" -e "SMOKE_KEY_ENV=$KeyEnv" $ExecContainer deno eval $code 2>&1
  if ($LASTEXITCODE -ne 0) { throw "docker exec $ExecContainer failed: $raw" }
  return ((@($raw) | Where-Object { "$_".StartsWith('[') }) | Select-Object -Last 1 | ConvertFrom-Json)
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
