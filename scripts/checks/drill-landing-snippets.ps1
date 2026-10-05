# drill-landing-snippets.ps1 - runs the read-only snippets of the pantry LANDING.md under Windows PowerShell 5.1
# against LABELLED THROWAWAY containers (a scratch pgvector Postgres, two openwebui:local stand-ins) on a private
# internal network. Never touches the live containers: every snippet has its container names substituted, and the
# compose-up lines are dropped.
#
# ASSERTING (harness-gaps, 2026-10-05). It used to print each snippet's output "to be read against the expected
# values in LANDING.md" - a drill nobody reads is a drill that passes. Every expectation is now an assertion:
#   - where LANDING.md states the expected value in a trailing `# <value>` comment (step 2 checks, step 3 checks),
#     the value is PARSED FROM THE LANDING TEXT and compared with what the snippet printed, so editing either
#     the command or its stated expectation in LANDING.md turns this red;
#   - where the expectation is prose (key generation, the .env check's throw/OK table, the DB identity, the pin),
#     it is asserted here, against the message the snippet itself carries.
# Exit code: 0 = every assertion held; 1 = at least one did not (each failing one is named with its snippet);
# the teardown still runs. Throwaways carry the label ai-stack.harness.owner=<-Id>; the private network is
# --internal and is not an ai-stack_* network.
#   powershell -NoProfile -File scripts\checks\drill-landing-snippets.ps1 [-Id <owner id>] [-Landing <path to LANDING.md>]
param(
  [string]$Id = 'wt-pantry-wire',
  [string]$Landing = ''
)
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
if (-not $Landing) { $Landing = Join-Path (Split-Path -Parent (Split-Path -Parent (git rev-parse --git-common-dir))) 'documentation-plans-ai-stack\implementation-guide\pantry-meal-planner\LANDING.md' }
$label = "ai-stack.harness.owner=$Id"
$rand = -join ((48..57 + 97..102) | Get-Random -Count 5 | ForEach-Object { [char]$_ })
$net = "lt-net-$rand"; $dbN = "lt-db-$rand"; $pN = "lt-pantry-$rand"; $oN = "lt-owui-$rand"
$landing = Get-Content -Raw $Landing
$blocks = @([regex]::Matches($landing, '(?s)```powershell\r?\n(.*?)```') | ForEach-Object { $_.Groups[1].Value })
function Show($t) { Write-Host "---- $t" -ForegroundColor Cyan }
$script:asserted = 0
$script:failures = @()
function Assert([string]$snippet, [string]$what, $ok, [string]$detail = '') {
  $script:asserted++
  if ($ok) { Write-Host ("  [OK]   {0}: {1}" -f $snippet, $what) }
  else { Write-Host ("  [FAIL] {0}: {1} {2}" -f $snippet, $what, $detail) -ForegroundColor Red; $script:failures += ("{0}: {1} {2}" -f $snippet, $what, $detail) }
}
# The `# <value>` expectations a snippet states, in order: only code lines (not whole-line comments), only the LAST
# `  # ` on the line, with a trailing `   (explanation)` parenthetical dropped.
function Get-Expect([string]$block) {
  $o = @()
  foreach ($l in ($block -split "`r?`n")) {
    if ($l -match '^\s*#') { continue }
    $m = [regex]::Matches($l, '\s{2,}#\s+(\S.*?)\s*$')
    if ($m.Count -eq 0) { continue }
    $o += ([regex]::Replace($m[$m.Count - 1].Groups[1].Value, '\s{2,}\(.*\)\s*$', ''))
  }
  return @($o)
}
function Run-Block([string]$snippet, [string]$code) {
  # Output of the snippet as trimmed lines; an exception is an assertion failure, not an abort.
  try { $r = @(Invoke-Expression $code | ForEach-Object { ("$_").TrimEnd() }) }
  catch { Assert $snippet 'the snippet runs without an unexpected error' $false $_.Exception.Message; return @() }
  foreach ($x in $r) { if ($x -ne '') { Write-Host $x } }
  return @($r | Where-Object { $_ -ne '' })
}
$pw = 'pw' + $rand
$made = @()
$tmp = Join-Path $env:TEMP "lt-$rand"
try {
  Assert 'LANDING' 'carries the eight powershell snippets this drill indexes (0..7)' ($blocks.Count -ge 8) ("found " + $blocks.Count + " in $Landing")
  if ($blocks.Count -lt 8) { throw 'LANDING shape changed: fewer than 8 powershell blocks' }
  docker network create --internal --label $label $net | Out-Null; $made += "net:$net"
  docker run -d --name $dbN --label $label --label com.docker.compose.config-hash=feedface1234 --network $net --health-cmd "pg_isready -U postgres" --health-interval 2s -e POSTGRES_PASSWORD=$pw -e POSTGRES_DB=openbrain pgvector/pgvector:pg16 | Out-Null; $made += "c:$dbN"
  for ($i=0;$i -lt 60;$i++){ docker exec $dbN pg_isready -h 127.0.0.1 -U postgres -d openbrain | Out-Null; if($LASTEXITCODE -eq 0){Start-Sleep 3;break}; Start-Sleep 2 }
  foreach ($f in 'init.sql','init-extensions.sql') { docker cp "$root\OB1\docker\$f" "${dbN}:/tmp/$f"; docker exec $dbN psql -U postgres -d openbrain -X -q -v ON_ERROR_STOP=1 -f "/tmp/$f" | Out-Null }
  $srv = "import http.server,json`nclass H(http.server.BaseHTTPRequestHandler):`n  def do_GET(s):`n    ok = s.path == '/health'`n    s.send_response(200 if ok else 401); s.end_headers(); s.wfile.write(json.dumps({'ok':True,'db':True}).encode() if ok else b'{}')`nhttp.server.HTTPServer(('0.0.0.0',8000),H).serve_forever()"
  # --health-cmd true: the stand-in must report `healthy` like the real service does (the image's own healthcheck
  # would leave it `starting`), so the LANDING expectation `healthy restarts=0` is asserted exactly, not excused.
  docker run -d --name $pN --label $label --network $net --health-cmd "true" --health-interval 1s --entrypoint python openwebui:local -c $srv | Out-Null; $made += "c:$pN"
  docker run -d --name $oN --label $label --network $net --entrypoint python openwebui:local -c "import time; time.sleep(600)" | Out-Null; $made += "c:$oN"
  Start-Sleep 3
  for ($i=0;$i -lt 45;$i++) {
    $hs = @($dbN, $pN | ForEach-Object { (docker inspect $_ | ConvertFrom-Json)[0].State.Health.Status })
    if (@($hs | Where-Object { $_ -eq 'healthy' }).Count -eq 2) { break }
    Start-Sleep 2
  }
  function Sub([string]$s) { $s.Replace('openbrain-db', $dbN).Replace('openbrain-pantry', $pN).Replace('openwebui', $oN) }

  New-Item -ItemType Directory "$tmp\OB1\docker" -Force | Out-Null
  Copy-Item "$root\OB1\docker\init-pantry.sql" "$tmp\OB1\docker\"
  Push-Location $tmp
  Show 'block 0: key generation'
  $o = @(Run-Block 'step 1 key generation' $blocks[0])
  Assert 'step 1 key generation' 'prints exactly one 64-hex-char key' ((@($o).Count -eq 1) -and ("$($o[0])" -match '^[0-9a-f]{64}$')) ("got: " + ($o -join ' | '))
  Show 'block 1: key check, seven .env cases (expect: throw x6, then OK)'
  $cases = @(
    @{ env = $null; throws = 'does not exist' },
    @{ env = 'PANTRY_API_KEY='; throws = 'PANTRY_API_KEY is missing or empty' },
    @{ env = 'PANTRY_API_KEY=   '; throws = 'PANTRY_API_KEY is missing or empty' },
    @{ env = 'PANTRY_API_KEY=""'; throws = 'PANTRY_API_KEY is missing or empty' },
    @{ env = "PANTRY_API_KEY=REPLACE_WITH_64_HEX_CHARS`nPANTRY_DB_PASSWORD=x"; throws = 'PANTRY_API_KEY is still the placeholder' },
    @{ env = "PANTRY_API_KEY=`"abc123`"`nPANTRY_DB_PASSWORD=  `"`"  "; throws = 'PANTRY_DB_PASSWORD is missing or empty' },
    @{ env = "PANTRY_API_KEY='abc123'`nPANTRY_DB_PASSWORD=$pw"; throws = $null })
  $ci = 0
  foreach ($case in $cases) {
    $ci++
    if ($null -eq $case.env) { Remove-Item OB1\docker\.env -ErrorAction SilentlyContinue } else { Set-Content OB1\docker\.env $case.env }
    $threw = $null; $out1 = @()
    try { $out1 = @(Invoke-Expression $blocks[1]) } catch { $threw = $_.Exception.Message }
    if ($null -ne $threw) { "THROWS: $threw" }
    if ($case.throws) { Assert 'step 1 key check' "case $ci throws '$($case.throws)'" (($null -ne $threw) -and $threw.Contains($case.throws)) ("threw=" + $threw) }
    else { Assert 'step 1 key check' "case $ci (valid keys) does not throw and prints 'both pantry keys present'" (($null -eq $threw) -and (@($out1 | Where-Object { "$_" -eq 'both pantry keys present' }).Count -eq 1)) ("threw=" + $threw + " out=" + ($out1 -join '|')) }
  }
  Show 'block 2 (1b) with docker compose up line removed'
  Push-Location $root
  $b2 = ((Sub $blocks[2]).Replace("config --hash $dbN",'config --hash openbrain-db')) -split "`r?`n" | Where-Object { $_ -notmatch 'up -d --no-deps' }
  $o2 = @(Invoke-Expression ($b2 -join "`n"))
  $o2 | Out-String | Write-Host
  $hashLine = @($o2 | Where-Object { "$_" -match '^openbrain-db [0-9a-f]{64}$' })
  Assert 'step 1b identity' 'before: the identity carries the container Id and the compose config-hash label' (($db0.Id -match '^[0-9a-f]{64}$') -and ($db0.ConfigHash -eq 'feedface1234')) ("db0=" + ($db0 | Out-String))
  Assert 'step 1b identity' 'before: Health is `healthy`' ($db0.Health -eq 'healthy') ("Health=" + $db0.Health)
  Assert 'step 1b hash' '`config --hash openbrain-db` prints `openbrain-db <64 hex>`' ($hashLine.Count -eq 1) ("lines=" + ($o2 -join '|'))
  Assert 'step 1b hash' 'the wanted hash differs from the running label (the premise of the recreate)' (($hashLine.Count -eq 1) -and (($hashLine[0] -split ' ')[1] -ne $db0.ConfigHash))
  $dbAfter = Get-DbIdentity $dbN
  Assert 'step 1b identity' 'after (re-read): same Id, healthy, same label - nothing was recreated by the read-only part' (($dbAfter.Id -eq $db0.Id) -and ($dbAfter.Health -eq 'healthy') -and ($dbAfter.ConfigHash -eq $db0.ConfigHash))
  Pop-Location
  Show 'block 3 (step 2 apply, run in a temp dir that has the sql + .env)'
  $recipes0 = ("" + (docker exec $dbN psql -U postgres -d openbrain -At -c "select count(*) from recipes")).Trim()
  $o3 = @(Run-Block 'step 2 apply' (Sub $blocks[3]))
  Assert 'step 2 apply' 'psql applied the file with no ERROR line' (($o3.Count -gt 0) -and (@($o3 | Where-Object { $_ -match 'ERROR' }).Count -eq 0)) ("lines=" + $o3.Count)
  Assert 'step 2 apply' 'it created tables and the ob_pantry role (CREATE TABLE and ALTER ROLE seen)' ((@($o3 | Where-Object { $_ -eq 'CREATE TABLE' }).Count -ge 14) -and (@($o3 | Where-Object { $_ -eq 'ALTER ROLE' }).Count -ge 1)) ("CREATE TABLE x" + @($o3 | Where-Object { $_ -eq 'CREATE TABLE' }).Count)
  Show 'block 4 (step 2 checks)'
  $o4 = @(Run-Block 'step 2 checks' (Sub $blocks[4]))
  $e4 = Get-Expect $blocks[4]
  Assert 'step 2 checks' 'LANDING states 4 expected values and the snippet printed 4 lines' (($e4.Count -eq 4) -and ($o4.Count -eq 4)) ("expect=" + ($e4 -join '|') + " out=" + ($o4 -join '|'))
  for ($i = 0; $i -lt [Math]::Min($e4.Count, $o4.Count); $i++) {
    $want = $e4[$i]; if ($want -match '^unchanged') { $want = $recipes0 }
    Assert 'step 2 checks' ("check $($i + 1): printed '$($o4[$i])', LANDING expects '$($e4[$i])'" + $(if ($e4[$i] -match '^unchanged') { " (= $recipes0, counted before step 2)" } else { '' })) ($o4[$i] -eq $want)
  }
  Pop-Location
  Show 'block 6 (step 3) without build/up/Remove-Item lines'
  Push-Location $root
  $b6 = (Sub $blocks[6]) -split "`r?`n" | Where-Object { $_ -notmatch 'compose -f' -and $_ -notmatch 'Remove-Item Env' }
  $db1 = $null; $db2 = $null; $pin = $null
  # inline, not Run-Block: the snippet's variables ($pin, $db1, $db2) must land in THIS scope to be asserted
  try { $o6 = @(Invoke-Expression ($b6 -join "`n")); $o6 | ForEach-Object { "$_" } | Out-String | Write-Host } catch { Assert 'step 3 pin and no-recreate guard' 'the snippet runs (its throw guard did not fire)' $false $_.Exception.Message }
  "pin=$pin"
  $wantPin = ("" + (git rev-parse "HEAD:OB1")).Trim()
  Assert 'step 3 pin' 'the OB1 pin read by `git ls-tree` is the 40-hex gitlink of HEAD' (($pin -match '^[0-9a-f]{40}$') -and ($pin -eq $wantPin)) ("pin=$pin want=$wantPin")
  Assert 'step 3 guard' 'the openbrain-db identity is unchanged across the step (the throw line did not fire)' (($null -ne $db1) -and ($null -ne $db2) -and ($db1.Id -eq $db2.Id) -and ($db1.StartedAt -eq $db2.StartedAt))
  Show 'block 7 (step 3 checks; stand-in service: healthy by --health-cmd, answers /health, 401 elsewhere)'
  $o7 = @(Run-Block 'step 3 checks' (Sub $blocks[7]))
  $e7 = Get-Expect $blocks[7]
  Assert 'step 3 checks' 'LANDING states 4 expected values and the snippet printed 4 lines' (($e7.Count -eq 4) -and ($o7.Count -eq 4)) ("expect=" + ($e7 -join '|') + " out=" + ($o7 -join '|'))
  for ($i = 0; $i -lt [Math]::Min($e7.Count, $o7.Count); $i++) {
    # the JSON the stand-in prints has spaces after the separators; the real service's does not: compare without whitespace
    Assert 'step 3 checks' ("check $($i + 1): printed '$($o7[$i])', LANDING expects '$($e7[$i])'") (($o7[$i] -replace '\s', '') -eq ($e7[$i] -replace '\s', ''))
  }
  Pop-Location
} catch {
  Assert 'drill' 'ran to the end' $false $_.Exception.Message
} finally {
  Show 'teardown'
  foreach ($m in $made) { $k,$n = $m -split ':',2; if ($k -eq 'c') { docker rm -f $n | Out-Null } }
  foreach ($m in $made) { $k,$n = $m -split ':',2; if ($k -eq 'net') { docker network rm $n | Out-Null } }
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  "left: containers=" + (docker ps -a -q --filter "label=$label").Count + " networks=" + (docker network ls -q --filter "label=$label").Count
}
Write-Host ''
if ($script:failures.Count -eq 0) { Write-Host ("LANDING SNIPPET DRILL: {0} assertion(s), 0 failed" -f $script:asserted) -ForegroundColor Green; exit 0 }
Write-Host ("LANDING SNIPPET DRILL: {0} assertion(s), {1} FAILED" -f $script:asserted, $script:failures.Count) -ForegroundColor Red
foreach ($f in $script:failures) { Write-Host ("  FAILED " + $f) -ForegroundColor Red }
exit 1
