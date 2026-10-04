# drill-landing-snippets.ps1 - runs the read-only snippets of the pantry LANDING.md under Windows PowerShell 5.1
# against LABELLED THROWAWAY containers (a scratch pgvector Postgres, two openwebui:local stand-ins) on a private
# internal network. Never touches the live containers: every snippet has its container names substituted, and the
# compose-up lines are dropped. Output is meant to be read against the expected values in LANDING.md.
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
$pw = 'pw' + $rand
$made = @()
try {
  docker network create --internal --label $label $net | Out-Null; $made += "net:$net"
  docker run -d --name $dbN --label $label --label com.docker.compose.config-hash=feedface1234 --network $net -e POSTGRES_PASSWORD=$pw -e POSTGRES_DB=openbrain pgvector/pgvector:pg16 | Out-Null; $made += "c:$dbN"
  for ($i=0;$i -lt 60;$i++){ docker exec $dbN pg_isready -h 127.0.0.1 -U postgres -d openbrain | Out-Null; if($LASTEXITCODE -eq 0){Start-Sleep 3;break}; Start-Sleep 2 }
  foreach ($f in 'init.sql','init-extensions.sql') { docker cp "$root\OB1\docker\$f" "${dbN}:/tmp/$f"; docker exec $dbN psql -U postgres -d openbrain -X -q -v ON_ERROR_STOP=1 -f "/tmp/$f" | Out-Null }
  $srv = "import http.server,json`nclass H(http.server.BaseHTTPRequestHandler):`n  def do_GET(s):`n    ok = s.path == '/health'`n    s.send_response(200 if ok else 401); s.end_headers(); s.wfile.write(json.dumps({'ok':True,'db':True}).encode() if ok else b'{}')`nhttp.server.HTTPServer(('0.0.0.0',8000),H).serve_forever()"
  docker run -d --name $pN --label $label --network $net --entrypoint python openwebui:local -c $srv | Out-Null; $made += "c:$pN"
  docker run -d --name $oN --label $label --network $net --entrypoint python openwebui:local -c "import time; time.sleep(600)" | Out-Null; $made += "c:$oN"
  Start-Sleep 3
  function Sub([string]$s) { $s.Replace('openbrain-db', $dbN).Replace('openbrain-pantry', $pN).Replace('openwebui', $oN) }

  $tmp = Join-Path $env:TEMP "lt-$rand"; New-Item -ItemType Directory "$tmp\OB1\docker" -Force | Out-Null
  Copy-Item "$root\OB1\docker\init-pantry.sql" "$tmp\OB1\docker\"
  Push-Location $tmp
  Show 'block 0: key generation'; Invoke-Expression $blocks[0]
  Show 'block 1: key check, six .env cases (expect: throw x5, then OK)'
  foreach ($case in @($null, 'PANTRY_API_KEY=', 'PANTRY_API_KEY=   ', 'PANTRY_API_KEY=""', "PANTRY_API_KEY=REPLACE_WITH_64_HEX_CHARS`nPANTRY_DB_PASSWORD=x", "PANTRY_API_KEY=`"abc123`"`nPANTRY_DB_PASSWORD=  `"`"  ", "PANTRY_API_KEY='abc123'`nPANTRY_DB_PASSWORD=$pw")) {
    if ($null -eq $case) { Remove-Item OB1\docker\.env -ErrorAction SilentlyContinue } else { Set-Content OB1\docker\.env $case }
    try { Invoke-Expression $blocks[1] } catch { "THROWS: $($_.Exception.Message)" }
  }
  "(case 6 had an empty quoted DB password, case 7 is the passing one)"
  Show 'block 2 (1b) with docker compose up line removed'
  Push-Location $root
  $b2 = ((Sub $blocks[2]).Replace("config --hash $dbN",'config --hash openbrain-db')) -split "`r?`n" | Where-Object { $_ -notmatch 'up -d --no-deps' }
  Invoke-Expression ($b2 -join "`n")
  'Get-DbIdentity after:'; Get-DbIdentity $dbN | Format-List
  Pop-Location
  Show 'block 3 (step 2 apply, run in a temp dir that has the sql + .env)'
  Invoke-Expression (Sub $blocks[3])
  Show 'block 4 (step 2 checks)'
  Invoke-Expression (Sub $blocks[4])
  Pop-Location
  Show 'block 6 (step 3) without build/up/Remove-Item lines'
  Push-Location $root
  $b6 = (Sub $blocks[6]) -split "`r?`n" | Where-Object { $_ -notmatch 'compose -f' -and $_ -notmatch 'Remove-Item Env' }
  Invoke-Expression ($b6 -join "`n"); "pin=$pin"
  Show 'block 7 (step 3 checks; stand-in server, so Health is empty by design)'
  Invoke-Expression (Sub $blocks[7])
  Pop-Location
} finally {
  Show 'teardown'
  foreach ($m in $made) { $k,$n = $m -split ':',2; if ($k -eq 'c') { docker rm -f $n | Out-Null } }
  foreach ($m in $made) { $k,$n = $m -split ':',2; if ($k -eq 'net') { docker network rm $n | Out-Null } }
  Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
  "left: containers=" + (docker ps -a -q --filter "label=$label").Count + " networks=" + (docker network ls -q --filter "label=$label").Count
}
