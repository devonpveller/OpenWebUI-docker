# drill-pantry-e2e.ps1 - the end-to-end drill for the pantry (item pantry-wire).
#
#   powershell -NoProfile -File scripts\checks\drill-pantry-e2e.ps1 [-Id <owner id>] [-Taste run|skip]
#
# WHAT IT PROVES. The OWUI tool (frontend/owui/tools/pantry.py), the openbrain-pantry
# service image and a real Postgres complete one scripted household evening together:
# household (2 adults + a 4.5-year-old with a peanut allergy), add stock, save household
# recipes, plan two dinners, shopping list, restock (bought all EXCEPT one, a substitution,
# a pack-size actual), an allergen-refused cook, cook, evaluate with a CHILD REFUSAL,
# guidance shows the COOLDOWN, CSV export -> import = EMPTY DIFF. The steps are in
# scripts\checks\pantry_e2e_drive.py (one [PASS]/[FAIL] line each).
#
# WHY HERE. scripts\checks\ is where the repo's drills and gates live, and this one spans
# TWO repos (the OB1 service image and the ai-stack tool), so it belongs to the superproject,
# not to OB1\docker\pantry-server\test\ (that one tests the service alone).
#
# WHAT IT CREATES, and ONLY this (every resource carries ai-stack.harness.owner=<Id>):
#   1 internal docker network      (no route out, no host port)
#   1 pgvector/pgvector:pg16 container   (a scratch Postgres; init.sql + init-extensions.sql
#                                        + init-pantry.sql applied, the pantry role gets a password)
#   1 image  openbrain-pantry:wt-<Id>    (built from OB1\docker\pantry-server; NEVER a :local tag)
#   1 openbrain-pantry container   (the service, as the least-privilege ob_pantry role)
#   1 openwebui:local container    (entrypoint python; runs the drive script - the tool's own
#                                  functions - attached ONLY to the private network)
# It never attaches anything to an ai-stack_* or open-brain_* network, publishes no port, runs
# no compose command, touches no live container, volume or .env, and tears down in `finally`
# exactly what it made (docker rm/rmi by the names it generated, never by pattern, never a prune).
#
# -Ob1Docker <dir>  drills another export of OB1's docker\ directory instead of the pinned one.
# -Taste skip  leaves out the two steps that need the pantry-taste routes (evaluation,
#              guidance). Use it only for a core-only baseline; the real drill is -Taste run.
# Exit code: the number of failed checks (0 = all green).
param(
  [string]$Id = 'wt-pantry-wire',                  # owner id (a tester passes their own worktree id)
  [ValidateSet('run', 'skip')][string]$Taste = 'run',
  [string]$DenoImage = '',                         # base image for the service build; default: a LOCAL deno image
  [string]$PgImage = 'pgvector/pgvector:pg16',     # must already be on the host; never pulled here
  [string]$OwuiImage = 'openwebui:local',          # must already be on the host; only READ (python + pydantic)
  # The OB1 docker directory to drill (init-*.sql + pantry-server\). Default: this checkout's
  # pinned OB1. A tester can point it at an export of another OB1 commit (git archive) to drill
  # a service build before the gitlink moves; the files are only READ.
  [string]$Ob1Docker = ''
)
$ErrorActionPreference = 'Continue'
$label = "ai-stack.harness.owner=$Id"
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dockerDir = if ($Ob1Docker) { $Ob1Docker } else { Join-Path $repo 'OB1\docker' }
$server = Join-Path $dockerDir 'pantry-server'
$rand = -join ((48..57 + 97..102) | Get-Random -Count 6 | ForEach-Object { [char]$_ })
$net = "pantry-e2e-net-$Id-$rand"
$dbName = "pantry-e2e-db-$Id-$rand"
$svcName = "pantry-e2e-svc-$Id-$rand"
$runName = "pantry-e2e-run-$Id-$rand"
$tag = 'openbrain-pantry:wt-' + ($Id -replace '^wt-', '')
$pw = -join ((48..57 + 97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
$pantryPw = -join ((48..57 + 97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
$apiKey = -join ((48..57 + 97..102) | Get-Random -Count 32 | ForEach-Object { [char]$_ })
$userId = '11111111-1111-4111-8111-111111111111'

$script:fail = 0
function Pass([string]$m) { Write-Host "  PASS  $m" }
function Fail([string]$m) { Write-Host "  FAIL  $m"; $script:fail++ }
function Check([bool]$ok, [string]$m) { if ($ok) { Pass $m } else { Fail $m } }
function Dk { $o = (& docker @args 2>&1 | ForEach-Object { "$_" }) -join "`n"; $script:rc = $LASTEXITCODE; return $o }
function Redact([string]$s) { return ($s -replace [regex]::Escape($pw), '***' -replace [regex]::Escape($pantryPw), '***' -replace [regex]::Escape($apiKey), '***') }

if ($tag -match ':local$') { throw 'refusing to build a :local tag' }
if ($Id -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') { throw "bad -Id '$Id'" }

Write-Host "== pantry end-to-end drill  owner=$Id  net=$net  taste=$Taste"
$null = Dk version --format '{{.Server.Version}}'
if ($script:rc -ne 0) { Write-Host 'docker is not reachable'; exit 2 }

function LocalSnapshot { (Dk images --filter 'reference=*:local' --format '{{.Repository}}:{{.Tag}} {{.ID}}' | Out-String).Trim() -split "`r?`n" | Sort-Object }
$localBefore = LocalSnapshot
$made = @{ net = $false; db = $false; svc = $false; run = $false; image = $false }
$drive = Join-Path $PSScriptRoot 'pantry_e2e_drive.py'

try {
  # ------------------------------------------------------------ 1. preconditions
  Write-Host '[1] preconditions (images must already be on the host; nothing is pulled)'
  foreach ($im in $PgImage, $OwuiImage) {
    $null = Dk image inspect $im
    Check ($script:rc -eq 0) "$im is on the host"
    if ($script:rc -ne 0) { throw 'image missing' }
  }
  Check (Test-Path $drive) 'pantry_e2e_drive.py exists'
  Check (Test-Path (Join-Path $repo 'frontend\owui\tools\pantry.py')) 'the tool exists'
  Check (Test-Path (Join-Path $server 'Dockerfile')) 'OB1\docker\pantry-server is checked out (submodule initialised)'
  if ($script:fail -gt 0) { throw 'preconditions' }

  # ------------------------------------------------------------ 2. network + postgres
  Write-Host '[2] private network + scratch postgres'
  $null = Dk network create --internal --label $label $net
  Check ($script:rc -eq 0) "network $net created (internal, labelled)"
  if ($script:rc -ne 0) { throw 'network' }
  $made.net = $true
  $null = Dk run -d --name $dbName --label $label --network $net -e "POSTGRES_PASSWORD=$pw" -e POSTGRES_DB=openbrain $PgImage
  Check ($script:rc -eq 0) "postgres container $dbName started"
  if ($script:rc -ne 0) { throw 'pg' }
  $made.db = $true
  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    $null = Dk exec $dbName pg_isready -h 127.0.0.1 -U postgres -d openbrain
    if ($script:rc -eq 0) { Start-Sleep -Seconds 2; $null = Dk exec $dbName pg_isready -h 127.0.0.1 -U postgres -d openbrain; if ($script:rc -eq 0) { $ready = $true; break } }
    Start-Sleep -Seconds 2
  }
  Check $ready 'postgres is accepting TCP connections'
  if (-not $ready) { throw 'pg not ready' }
  function Psql([string[]]$more) { Dk exec $dbName psql -U postgres -d openbrain -X -q -v ON_ERROR_STOP=1 @more }
  foreach ($f in 'init.sql', 'init-extensions.sql', 'init-pantry.sql') {
    $null = Dk cp (Join-Path $dockerDir $f) "${dbName}:/tmp/$f"
  }
  foreach ($f in 'init.sql', 'init-extensions.sql') {
    $o = Psql @('-f', "/tmp/$f")
    Check (($script:rc -eq 0) -and ($o -cnotmatch 'ERROR:')) "upstream $f applied"
    if (($script:rc -ne 0) -or ($o -cmatch 'ERROR:')) { Write-Host (Redact $o); throw 'init' }
  }
  $o = Psql @('-v', "pantry_db_password=$pantryPw", '-f', '/tmp/init-pantry.sql')
  Check (($script:rc -eq 0) -and ($o -cnotmatch 'ERROR:')) 'init-pantry.sql applied with the role password (the operator step, on a scratch DB)'
  if (($script:rc -ne 0) -or ($o -cmatch 'ERROR:')) { Write-Host (Redact $o); throw 'init' }

  # ------------------------------------------------------------ 3. the service image
  Write-Host "[3] build $tag from OB1\docker\pantry-server"
  if (-not $DenoImage) {
    foreach ($cand in 'denoland/deno:2.3.3', 'denoland/deno:alpine') {
      $null = Dk image inspect $cand
      if ($script:rc -eq 0) { $DenoImage = $cand; break }
    }
  }
  if (-not $DenoImage) { Fail 'no local deno base image (pass -DenoImage)'; throw 'no deno' }
  Write-Host "      base image: $DenoImage"
  $o = Dk build --progress=plain -t $tag --label $label --build-arg "DENO_IMAGE=$DenoImage" $server
  Check ($script:rc -eq 0) "image $tag built"
  if ($script:rc -ne 0) { Write-Host (($o -split "`r?`n" | Select-Object -Last 25) -join "`n"); throw 'build' }
  $made.image = $true

  # ------------------------------------------------------------ 4. the service
  Write-Host '[4] start the service on the private network (no published port)'
  $null = Dk run -d --name $svcName --label $label --network $net `
    -e "DB_HOST=$dbName" -e DB_PORT=5432 -e DB_NAME=openbrain -e DB_USER=ob_pantry -e "DB_PASSWORD=$pantryPw" `
    -e "DEFAULT_USER_ID=$userId" -e "PANTRY_API_KEY=$apiKey" -e TZ=UTC -e PORT=8000 $tag
  Check ($script:rc -eq 0) "service container $svcName started"
  if ($script:rc -ne 0) { throw 'svc' }
  $made.svc = $true
  Start-Sleep -Seconds 4
  $running = (Dk inspect -f '{{.State.Running}}' $svcName).Trim()
  Check ($running -eq 'true') 'the service is still running 4 s after start'
  if ($running -ne 'true') { Write-Host (Redact (Dk logs $svcName)); throw 'svc exited' }

  # ------------------------------------------------------------ 5. the evening
  Write-Host '[5] the scripted evening (the tool functions -> the service -> postgres)'
  $null = Dk create --name $runName --label $label --network $net `
    -v "$(Join-Path $repo 'frontend\owui'):/o:ro" -v "${drive}:/d/drive.py:ro" `
    -e PYTHONDONTWRITEBYTECODE=1 -e "PANTRY_URL=http://${svcName}:8000" -e "PANTRY_KEY=$apiKey" -e PANTRY_TOOL=/o/tools/pantry.py `
    --entrypoint python $OwuiImage /d/drive.py --taste $Taste
  Check ($script:rc -eq 0) "driver container $runName created from $OwuiImage"
  if ($script:rc -ne 0) { throw 'create' }
  $made.run = $true
  $out = Dk start -a $runName
  $code = (Dk inspect -f '{{.State.ExitCode}}' $runName).Trim()
  Write-Host (Redact $out)
  Check ($code -eq '0') "the evening completed: drive script exit code $code (0 = every step passed)"
  if ($out -match '\[SKIP\]') { Write-Host '  NOTE  steps were SKIPPED (-Taste skip): this is a core-only baseline, not the full drill' }

  # ------------------------------------------------------------ 6. isolation
  Write-Host '[6] isolation: labelled, private network only, no published port'
  foreach ($c in $dbName, $svcName, $runName) {
    # `docker inspect` JSON parsed here: PS 5.1 strips the double quotes out of a native
    # argument, so a Go template with `index .Labels "k"` cannot be passed reliably.
    $o = Dk inspect $c
    $ci = ($o | ConvertFrom-Json)[0]
    $nets = @($ci.NetworkSettings.Networks.PSObject.Properties.Name)
    Check (($nets.Count -eq 1) -and ($nets[0] -eq $net)) "$c is attached to the private network ONLY ($($nets -join ','))"
    Check (-not ($nets | Where-Object { $_ -match '^(ai-stack_|open-brain_)' })) "$c is on no ai-stack_* / open-brain_* network"
    Check ($ci.Config.Labels.'ai-stack.harness.owner' -eq $Id) "$c carries $label"
    $pb = @($ci.HostConfig.PortBindings.PSObject.Properties).Count
    Check ($pb -eq 0) "$c publishes no host port"
  }
  $ni = ((Dk network inspect $net) | ConvertFrom-Json)[0]
  Check (($ni.Internal -eq $true) -and ($ni.Labels.'ai-stack.harness.owner' -eq $Id)) 'the network is internal and labelled'
  $ii = ((Dk image inspect $tag) | ConvertFrom-Json)[0]
  Check ($ii.Config.Labels.'ai-stack.harness.owner' -eq $Id) "image $tag carries the owner label"
}
catch {
  if ("$_" -notin @('image missing', 'preconditions', 'network', 'pg', 'pg not ready', 'init', 'build', 'no deno', 'svc', 'svc exited', 'create')) { Fail "unexpected error: $_" }
}
finally {
  Write-Host '[7] teardown (only what this run created, by the names it generated)'
  if ($made.run) { $null = Dk rm -f $runName }
  if ($made.svc) { $null = Dk rm -f $svcName }
  if ($made.db) { $null = Dk rm -f $dbName }
  if ($made.net) { $null = Dk network rm $net }
  if ($made.image) { $null = Dk rmi $tag }
  $left = (Dk ps -a -q --filter "label=$label").Trim()
  Check ($left -eq '') "no container labelled $label is left"
  $leftNet = (Dk network ls -q --filter "label=$label").Trim()
  Check ($leftNet -eq '') "no network labelled $label is left"
  $leftImg = (Dk images -q --filter "label=$label").Trim()
  Check ($leftImg -eq '') "no image labelled $label is left"
  $localAfter = LocalSnapshot
  Check ((($localBefore -join '|') -eq ($localAfter -join '|'))) 'no :local image was created or changed'
}
Write-Host ''
if ($script:fail -eq 0) { Write-Host 'RESULT: PASS (every check green)'; exit 0 }
Write-Host "RESULT: FAIL ($($script:fail) check(s) failed)"
exit $script:fail
