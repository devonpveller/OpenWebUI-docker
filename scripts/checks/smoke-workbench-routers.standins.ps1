# smoke-workbench-routers.standins.ps1 - the smoke script against REAL docker on labelled STAND-INS:
# the attempt-2 tester's resolution/routing bypasses A1-A7 (rw-p0b-purge-door, test-attempt2 "A1-A7"),
# each as its own check, and the shapes attempt 3 added (a relay over the target's networks, a
# namespace joiner, a pre-seeded caller container, the subnets, the owner-label case variant).
#
#   powershell -NoProfile -File scripts\checks\smoke-workbench-routers.standins.ps1 -Owner <id> `
#     -SubnetA <a/24> -SubnetB <b/24> -SubnetC <c/24> -SubnetD <d/24> -FetchSubnet <f/24> [-Smoke <a copy of the script>]
#
# NEVER LIVE: every container and network here is created by this script, labelled
# ai-stack.harness.owner=<Owner>, named rwp0b3-*, from the smoke script's own pinned fetch image, on
# networks of the bridge driver ONLY (never macvlan/ipvlan) with the caller's subnets; "compose" here
# is a com.docker.compose.project LABEL on a stand-in - what a live plane looks like to the check.
# Only SubnetC's network is not --internal (A7: a port published on the host's 127.0.0.1, 18899).
# Everything labelled <Owner> is removed at the end (rm -f -v) and the removal is checked.
#
# Each check: refused = exit 2, a REFUSED line naming the reason, the capture directory NOT
# created; captured = exit 0 and every response from the VERIFIED target ({"exec":"self"}), never
# from a stand-in ({"victim":...}), fetched by the script's own fetch container. Exit 0 = all passed.
param(
  [string]$Smoke = (Join-Path $PSScriptRoot 'smoke-workbench-routers.ps1'),
  [string]$Owner = '',
  [string]$SubnetA = '', [string]$SubnetB = '', [string]$SubnetC = '', [string]$SubnetD = '', [string]$FetchSubnet = ''
)
$ErrorActionPreference = 'Continue'
. $Smoke   # Test-Subnet, Get-ComposeWhy, $FetchImage - nothing runs
if ($Owner -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { Write-Host "REFUSED: -Owner <id> is required (got '$Owner')"; exit 2 }
foreach ($s in @($SubnetA, $SubnetB, $SubnetC, $SubnetD, $FetchSubnet)) { $w = Test-Subnet $s 24 24; if ($w) { Write-Host "REFUSED: $w"; exit 2 } }
if (@(@($SubnetA, $SubnetB, $SubnetC, $SubnetD, $FetchSubnet) | Sort-Object -Unique).Count -ne 5) { Write-Host 'REFUSED: the five subnets must differ'; exit 2 }
$L = "ai-stack.harness.owner=$Owner"
$img = $FetchImage
$root = Join-Path $env:TEMP ('rwp0b3-standins-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Force $root | Out-Null
$o3 = { param($s) ($s -split '/')[0] -replace '\.0$', '' }
$victimIp = (& $o3 $SubnetA) + '.50'
$script:fail = 0
function Check([bool]$ok, [string]$m) { if ($ok) { Write-Host "  PASS  $m" } else { Write-Host "  FAIL  $m"; $script:fail++ } }
function D { $o = (& docker @args 2>&1 | ForEach-Object { "$_" }) -join "`n"; if ($LASTEXITCODE -ne 0) { Write-Host "  docker $($args[0]) rc=$LASTEXITCODE :: $(($o -split "`n")[0])" }; return $o }
$srvVictim = "Deno.serve({port:8000,hostname:'0.0.0.0'},(r)=>Response.json({victim:'compose-standin',url:r.url}))"
$srvSelf = "Deno.serve({port:8000,hostname:'0.0.0.0'},(r)=>Response.json({exec:'self',url:r.url}))"
$srvRelay = "Deno.serve({port:8000,hostname:'0.0.0.0'},async (r)=>{try{const u=new URL(r.url);const x=await fetch('http://$($victimIp):8000'+u.pathname);return new Response(await x.text(),{headers:{'content-type':'application/json'}})}catch(e){return Response.json({relay:'failed'})}})"
function Up([string]$name, [string[]]$a, [string]$code = $srvSelf) { $null = D run -d --name $name --label $L @a --entrypoint deno $img eval $code }
function Ready([string[]]$names) {
  foreach ($c in $names) { for ($i = 0; $i -lt 30; $i++) { if ((docker inspect -f '{{.State.Running}}' $c 2>$null) -eq 'true') { break }; Start-Sleep 1 } }
  Start-Sleep 2
}
$script:n = 0
function Smoke([hashtable]$p, [object[]]$extra = @()) {
  $script:n++
  $dir = Join-Path $root ('cap-{0:d2}' -f $script:n)
  $before = @(docker ps -a -q --no-trunc)
  # every argument spelled out (an empty value is left out: Windows PowerShell drops '' on a native call)
  $argv = @('-NoProfile', '-File', $Smoke, '-Capture', $dir) + @($extra)
  foreach ($k in $p.Keys) { if ("$($p[$k])" -ne '') { $argv += @("-$k", [string]$p[$k]) } }
  $o = (& powershell @argv 2>&1 | Out-String -Width 4096)
  $rc = $LASTEXITCODE
  $body = ''; $tgt = ''
  if (Test-Path (Join-Path $dir 'notes-list.txt')) { $body = ([System.IO.File]::ReadAllText((Join-Path $dir 'notes-list.txt')) -replace "`r?`n", ' ').Trim() }
  if (Test-Path (Join-Path $dir '_target.txt')) { $tgt = [System.IO.File]::ReadAllText((Join-Path $dir '_target.txt')) }
  $left = @(docker ps -a --filter "label=$L" --filter label=ai-stack.smoke.fetch --format '{{.Names}}') + @(docker network ls --filter "label=$L" --filter label=ai-stack.smoke.fetch --format '{{.Name}}')
  return @{ out = $o; rc = $rc; dir = (Test-Path $dir); body = $body; target = $tgt; left = @($left | Where-Object { $_ }); before = $before
    line = (($o -split "`r?`n") | Where-Object { $_ -match 'REFUSED|FAILED' } | Select-Object -First 1) }
}
function Refused([string]$label, [hashtable]$p, [string]$rx, [object[]]$extra = @()) {
  $r = Smoke $p $extra
  Check (($r.rc -eq 2) -and -not $r.dir -and ($r.out -match 'REFUSED') -and ($r.out -match $rx) -and $r.left.Count -eq 0 -and $r.body -notmatch 'victim') ("{0,-44} rc={1} dir={2} left={3} :: {4}" -f $label, $r.rc, $r.dir, $r.left.Count, $r.line)
}
function CapturedSelf([string]$label, [hashtable]$p, [string]$verifiedName) {
  $r = Smoke $p
  $fetch = if ($r.target -match '(?m)^fetch: (\S+) \(([0-9a-f]{64})\)') { @($Matches[1], $Matches[2]) } else { @('', '') }
  $fresh = ($fetch[1] -ne '') -and -not (@($r.before | Where-Object { $_ -eq $fetch[1] }).Count)
  $ok = ($r.rc -eq 0) -and $r.dir -and ($r.body -match '"exec":"self"') -and ($r.body -notmatch 'victim') -and ($r.target -match "(?m)^container: $([regex]::Escape($verifiedName))\s*$") -and
    ($fetch[0] -like 'rwp-smoke-fetch-*') -and $fresh -and $r.left.Count -eq 0 -and ($r.target -match '(?m)^live: False')
  Check $ok ("{0,-44} rc={1} body={2} fetch={3} (new: {4}) left={5}" -f $label, $r.rc, $r.body, $fetch[0], $fresh, $r.left.Count)
  return $r
}

try {
  Write-Host "[0] stand-ins (owner $Owner; networks $SubnetA / $SubnetB / $SubnetC (not internal) / $SubnetD; fetch $FetchSubnet)"
  $null = D network create --driver bridge --internal --subnet $SubnetA --label $L rwp0b3-na
  $null = D network create --driver bridge --internal --subnet $SubnetB --label $L rwp0b3-nb
  $null = D network create --driver bridge --subnet $SubnetC --label $L rwp0b3-nc
  $null = D network create --driver bridge --internal --subnet $SubnetD --label $L rwp0b3-nd
  # the compose stand-in on net A, and the tester's exec containers next to it
  Up 'rwp0b3-victim' @('--label', 'com.docker.compose.project=rwp0b3-probe', '--network', 'rwp0b3-na', '--ip', $victimIp, '--network-alias', 'rwp0b3-alias', '--network-alias', 'rwp0b3-dns.evil') $srvVictim
  foreach ($nm in 'rwp0b3-hosts', 'rwp0b3-etc', 'rwp0b3-gw', 'rwp0b3-cx') { $null = D create --name $nm --label $L --network rwp0b3-nb --entrypoint true $img }
  Up 'rwp0b3-exec1' @('--network', 'rwp0b3-na', '--add-host', "rwp0b3-hosts:$victimIp")
  Up 'rwp0b3-exec2' @('--network', 'rwp0b3-na')
  Up 'rwp0b3-exec3' @('--network', 'rwp0b3-na', '-e', "HTTP_PROXY=http://$($victimIp):8000", '-e', "http_proxy=http://$($victimIp):8000")
  Up 'rwp0b3-exec4' @('--network', 'rwp0b3-na', '--dns-search', 'evil')
  Up 'rwp0b3-exec5' @('--network', 'rwp0b3-na')
  # A6: a compose-labelled exec container with extra_hosts; and a service label without a project
  # (on net C, away from the island on net D: their own labels refuse them first)
  Up 'rwp0b3-exec6' @('--label', 'com.docker.compose.project=rwp0b3-ex', '--network', 'rwp0b3-nc', '--add-host', "rwp0b3-cx:$victimIp")
  $null = D create --name rwp0b3-svconly --label $L --label com.docker.compose.service=exec --network rwp0b3-nc --entrypoint true $img
  # (no exact owner label, so it carries a second label the cleanup below also removes by)
  $null = D create --name rwp0b3-casevar --label "Ai-Stack.Harness.Owner=$Owner" --label "ai-stack.harness.standin=$Owner" --network rwp0b3-nc --entrypoint true $img
  # A7: on a NON-internal bridge, a stand-in published on the host's 127.0.0.1 and an exec with host-gateway
  Up 'rwp0b3-victim7' @('--label', 'com.docker.compose.project=rwp0b3-probe', '--network', 'rwp0b3-nc', '-p', '127.0.0.1:18899:8000') $srvVictim
  Up 'rwp0b3-exec7' @('--network', 'rwp0b3-nc', '--add-host', 'rwp0b3-gw:host-gateway')
  # the island: a clean target alone on net D, with HTTP_PROXY / an /etc/hosts edit / --dns-search aimed at the stand-in
  Up 'rwp0b3-island' @('--network', 'rwp0b3-nd', '--add-host', "rwp0b3-island-alias:$victimIp", '--dns-search', 'evil', '-e', "HTTP_PROXY=http://$($victimIp):8000", '-e', 'WORKBENCH_KEY=standin-key')
  # the pre-seeded "caller fetch container": its /etc/hosts and proxy env point at the stand-in
  Up 'rwp0b3-preseed' @('--network', 'rwp0b3-na', '-e', "HTTP_PROXY=http://$($victimIp):8000")
  Ready @('rwp0b3-victim', 'rwp0b3-exec1', 'rwp0b3-exec2', 'rwp0b3-exec3', 'rwp0b3-exec4', 'rwp0b3-exec5', 'rwp0b3-exec6', 'rwp0b3-victim7', 'rwp0b3-exec7', 'rwp0b3-island', 'rwp0b3-preseed')
  foreach ($c in 'rwp0b3-exec5', 'rwp0b3-island', 'rwp0b3-preseed') {
    $w = D exec -u 0 $c deno eval "Deno.writeTextFileSync('/etc/hosts', '$victimIp rwp0b3-etc $c\n', {append:true}); console.log('hosts appended')"
    Write-Host "  $c /etc/hosts edit at run time: $w"
  }
  Write-Host ("  stand-ins: " + ((docker ps -a --filter "label=$L" --format '{{.Names}}:{{.State}}') -join ' '))

  $F = $FetchSubnet
  Write-Host '[1] the attempt-2 call shape is gone: -ExecContainer is an unknown argument'
  Refused 'A1 verbatim (-ExecContainer exec1, fixture URL)' @{ BaseUrl = 'http://rwp0b3-hosts:8000' } "unknown argument.*-ExecContainer" @('-ExecContainer', 'rwp0b3-exec1')
  Refused 'A3 verbatim (-ExecContainer exec3, localhost)' @{ BaseUrl = 'http://localhost:8000' } "unknown argument.*-ExecContainer" @('-ExecContainer', 'rwp0b3-exec3')
  Refused 'A5 pre-seeded caller container as -ExecContainer' @{ Target = 'rwp0b3-island'; Subnet = $F } "unknown argument.*-ExecContainer" @('-ExecContainer', 'rwp0b3-preseed')

  Write-Host '[2] A1-A7 in the new interface: the stand-in is never reached'
  Refused 'A0 the compose stand-in as -Target' @{ Target = 'rwp0b3-victim'; Subnet = $F } 'belongs to compose project'
  Refused 'A1 -Target exec1 (--add-host to the stand-in)' @{ Target = 'rwp0b3-exec1'; Subnet = $F } "reaches container 'rwp0b3-victim'.*compose project"
  Refused 'A1 -Target exec1, URL host = the fixture name' @{ Target = 'rwp0b3-exec1'; Subnet = $F; BaseUrl = 'http://rwp0b3-hosts:8000' } "base URL host 'rwp0b3-hosts' is not the target"
  Refused 'A1 -Target = the fixture-named container' @{ Target = 'rwp0b3-hosts'; Subnet = $F } 'is not running'
  Refused 'A2 -Target exec2, URL host = the alias' @{ Target = 'rwp0b3-exec2'; Subnet = $F; BaseUrl = 'http://rwp0b3-alias:8000' } "base URL host 'rwp0b3-alias' is not the target"
  Refused 'A2 -Target exec2 (the alias is its peer)' @{ Target = 'rwp0b3-exec2'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  Refused 'A3 -Target exec3 (HTTP_PROXY), URL localhost' @{ Target = 'rwp0b3-exec3'; Subnet = $F; BaseUrl = 'http://localhost:8000' } "base URL host 'localhost' is not the target"
  Refused 'A3b -Target exec3, URL 127.0.0.1' @{ Target = 'rwp0b3-exec3'; Subnet = $F; BaseUrl = 'http://127.0.0.1:8000' } "base URL host '127.0.0.1' is not the target"
  Refused 'A3c -Target exec3' @{ Target = 'rwp0b3-exec3'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  Refused 'A4 -Target exec4 (--dns-search), URL dns name' @{ Target = 'rwp0b3-exec4'; Subnet = $F; BaseUrl = 'http://rwp0b3-dns:8000' } "base URL host 'rwp0b3-dns' is not the target"
  Refused 'A4 -Target exec4' @{ Target = 'rwp0b3-exec4'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  Refused 'A5 -Target exec5 (/etc/hosts edited)' @{ Target = 'rwp0b3-exec5'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  Refused 'A5 -Target exec5, URL host rwp0b3-etc' @{ Target = 'rwp0b3-exec5'; Subnet = $F; BaseUrl = 'http://rwp0b3-etc:8000' } "base URL host 'rwp0b3-etc' is not the target"
  Refused 'A6 compose-labelled exec with extra_hosts' @{ Target = 'rwp0b3-exec6'; Subnet = $F } 'belongs to compose project'
  Refused 'A6b a compose service label, no project' @{ Target = 'rwp0b3-svconly'; Subnet = $F } "compose label 'com.docker.compose.service'"
  Refused 'A7 -Target exec7 (non-internal, host-gateway)' @{ Target = 'rwp0b3-exec7'; Subnet = $F } 'not --internal'
  Refused 'A7 -Target exec7, URL rwp0b3-gw:18899' @{ Target = 'rwp0b3-exec7'; Subnet = $F; BaseUrl = 'http://rwp0b3-gw:18899' } "base URL host 'rwp0b3-gw' is not the target"
  Refused 'owner label case variant (Ai-Stack.Harness.Owner)' @{ Target = 'rwp0b3-casevar'; Subnet = $F } 'carries no ai-stack.harness.owner label'

  Write-Host '[3] the island: HTTP_PROXY, --add-host, --dns-search and an /etc/hosts edit in the target change nothing'
  $r1 = CapturedSelf 'island captured: the verified target only' @{ Target = 'rwp0b3-island'; Subnet = $F; KeyEnv = 'WORKBENCH_KEY' } 'rwp0b3-island'
  Check ($r1.target -match "(?m)^base: http://$([regex]::Escape((& $o3 $F)))\.\d+:8000\s*$") "  the fetch went to the island's address on the script's network ($F): $((($r1.target -split "`n") | Where-Object { $_ -like 'base:*' }))"
  Check ($r1.target -notmatch 'rwp0b3-preseed') '  the pre-seeded caller container took no part (not named in _target.txt; the fetch container was new and is gone)'
  $r2 = CapturedSelf 'island with a path prefix and the target name' @{ Target = 'rwp0b3-island'; Subnet = $F; BaseUrl = 'http://rwp0b3-island:8000/' } 'rwp0b3-island'

  Write-Host '[4] relays and namespace joiners'
  Up 'rwp0b3-hop' @('--network', 'rwp0b3-nd') $srvRelay
  $null = D network connect rwp0b3-na rwp0b3-hop
  Ready @('rwp0b3-hop')
  Refused 'two hops: island -> hop (clean) -> stand-in' @{ Target = 'rwp0b3-island'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  Refused 'the relay itself as -Target' @{ Target = 'rwp0b3-hop'; Subnet = $F } "reaches container 'rwp0b3-victim'"
  $null = D rm -f -v rwp0b3-hop
  $null = D create --name rwp0b3-joiner --label $L --label com.docker.compose.project=rwp0b3-probe --network container:rwp0b3-island --entrypoint true $img
  Refused 'a compose container joins the target namespace' @{ Target = 'rwp0b3-island'; Subnet = $F } 'shares the network namespace of'
  $null = D rm -f -v rwp0b3-joiner
  $null = CapturedSelf 'island again after the joiner is removed' @{ Target = 'rwp0b3-island'; Subnet = $F } 'rwp0b3-island'

  Write-Host '[5] -Subnet: required, /24 or narrower, outside the default pools and 192.168.0.0/16'
  foreach ($bad in @('', '172.17.0.0/16', '172.17.5.0/24', '172.31.200.0/24', '10.0.0.0/16', '192.168.7.0/24', ((& $o3 $F) + '.5/24'), '10.218.0.0/29x')) {
    Refused "subnet '$bad'" @{ Target = 'rwp0b3-island'; Subnet = $bad } '(Subnet|subnet)'
  }
  Refused '-Live with -Target' @{ Target = 'rwp0b3-island'; Subnet = $F } '-Live names its own target' @('-Live')
} finally {
  Write-Host '[6] cleanup'
  foreach ($c in @(docker ps -a -q --filter "label=$L") + @(docker ps -a -q --filter "label=ai-stack.harness.standin=$Owner")) { $null = docker rm -f -v $c }
  foreach ($nw in @(docker network ls -q --filter "label=$L")) { $null = docker network rm $nw }
  $leftC = ((@(docker ps -a --filter "label=$L" --format '{{.Names}}') + @(docker ps -a --filter "label=ai-stack.harness.standin=$Owner" --format '{{.Names}}')) -join ' '); $leftN = ((docker network ls --filter "label=$L" --format '{{.Name}}') -join ' ')
  Check (($leftC -eq '') -and ($leftN -eq '')) "nothing labelled $Owner is left (containers: '$leftC', networks: '$leftN')"
  Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}
if ($script:fail) { Write-Host "$($script:fail) check(s) FAILED"; exit 1 }
Write-Host 'all checks passed'
exit 0
