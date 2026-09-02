# drill-mcp-door-not-superuser.ps1
#
# DFU C.8 clause 3. The live run found the personal-plane boundary OPEN at two doors:
#
#   [fail] door-openbrain-mcp-door     HTTP 200; the door RETURNED the personal fixture;
#                                      the door connects as 'postgres' (rolsuper/rolbypassrls = t/t)
#   [fail] door-cloud-search-thoughts  HTTP 200; the door RETURNED the personal fixture
#
# This drill reproduces BOTH failures on a throwaway and shows the same doors refusing the
# same row after one change - `openbrain-mcp`'s DB_USER. It writes nothing to production,
# starts nothing on an `ai-stack_*` network, and builds no `:local` image.
#
# WHY ONE CHANGE CLOSES TWO DOORS. `openbrain-gateway` (cloud) holds no database connection
# at all: it is an HTTP proxy whose OPENBRAIN_URL is `http://openbrain-mcp:8000`
# (openbrain-gateway/app.py:41). `openbrain-ops-gateway`, which fronts clause 3's third MCP
# door, is a second instance of the same image pointed at the same upstream. All three doors
# therefore traverse ONE database connection. This drill runs the REAL gateway image in
# front of the REAL MCP image so that claim is measured rather than asserted.
#
# WHAT MAKES THIS A BOUNDARY AND NOT A BLACKOUT. Every GREEN probe carries a positive
# control - the ops twin of the same fixture, written in the same statement. A role that
# returns nothing at all would pass a "the personal row is gone" check while being a total
# outage, and that is the shape of green this project keeps finding. If the control does not
# come back, the drill reports CANNOT MEASURE rather than success.
#
#   .\drill-mcp-door-not-superuser.ps1
#
# Exit 0 = every probe as expected. 1 = a probe disagreed. 2 = could not measure.

[CmdletBinding()]
param(
    [int]$DbTimeoutSec = 600,
    [switch]$KeepUp          # leave the throwaway running for inspection
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$script:repo    = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$script:obDocker= Join-Path $script:repo "OB1\docker"
$script:compose = Join-Path $script:obDocker "docker-compose.yml"
$script:sqlDir  = Join-Path $PSScriptRoot "sql"

# Every name this drill creates carries the worktree id, so a leftover is attributable and
# `docker rm -f` can never reach a production container by accident.
$script:net = "wt-dfuc3-drill-net"
$script:db  = "wt-dfuc3-drill-db"
$script:mcp = "wt-dfuc3-drill-mcp"
$script:gw  = "wt-dfuc3-drill-gw"
$script:initDir = Join-Path $env:TEMP "dfuc3-drill-initdb"

$script:mark    = "DFUC3-DRILL"
$script:ftype   = "dfuc3-drill-fixture"
$script:mcpKey  = "drill-mcp-key-not-a-secret"
$script:gwKey   = "drill-gw-key-not-a-secret"
$script:appPw   = "drill-app-pw-not-a-secret"

$script:pass = 0
$script:fail = 0

function Say  { param([string]$m) Write-Host $m }
function Head { param([string]$m) Write-Host ""; Write-Host $m -ForegroundColor Cyan }

function Probe {
    param([string]$Name, [string]$Expected, [string]$Actual)
    $a = ($Actual -replace "\s+", " ").Trim()
    $e = ($Expected -replace "\s+", " ").Trim()
    if ($a -eq $e) { $script:pass++; Write-Host ("  [ok]   {0}" -f $Name) -ForegroundColor Green }
    else {
        $script:fail++
        Write-Host ("  [FAIL] {0}" -f $Name) -ForegroundColor Red
        Write-Host ("         expected: {0}" -f $e)
        Write-Host ("         actual  : {0}" -f $a)
    }
}

function ProbeMatch {
    param([string]$Name, [string]$Pattern, [string]$Actual)
    if ($Actual -match $Pattern) { $script:pass++; Write-Host ("  [ok]   {0}" -f $Name) -ForegroundColor Green }
    else {
        $script:fail++
        Write-Host ("  [FAIL] {0}" -f $Name) -ForegroundColor Red
        Write-Host ("         wanted /{0}/ in: {1}" -f $Pattern, (($Actual -replace "\s+"," ").Trim()))
    }
}

function Cleanup {
    if ($KeepUp) { Say ""; Say "-KeepUp: leaving $script:db / $script:mcp / $script:gw on $script:net"; return }
    foreach ($c in @($script:gw, $script:mcp, $script:db)) { & docker rm -f $c 2>&1 | Out-Null }
    & docker network rm $script:net 2>&1 | Out-Null
    if (Test-Path $script:initDir) { Remove-Item -Recurse -Force $script:initDir -ErrorAction SilentlyContinue }
}

$script:inTrap = $false
trap {
    if ($script:inTrap) { Write-Host "HARNESS ERROR while handling a harness error: $_"; exit 2 }
    $script:inTrap = $true
    Say "HARNESS ERROR: $_"
    Cleanup
    exit 2
}

function Sql {
    # -h 127.0.0.1 FORCES a TCP connection with password auth. Without it psql uses the
    # local socket, where the image's `trust` rule lets ANY role in without a password -
    # so "connected as ob_app_memory" could silently be "connected as postgres wearing a
    # different -U", and every GREEN probe below would be measuring the superuser.
    param([string]$Role, [string]$Password, [string]$Query)
    $args = @("exec")
    if ($Password) { $args += @("-e", "PGPASSWORD=$Password") }
    $args += @($script:db, "psql", "-U", $Role, "-h", "127.0.0.1", "-d", "openbrain", "-Atc", $Query)
    $out = (& docker @args 2>&1 | Out-String)
    return @{ Out = $out; Exit = $LASTEXITCODE }
}

function McpCall {
    # One JSON-RPC tools/call over HTTP, the way a client makes it.
    param([string]$Url, [string]$Header, [string]$Tool, [string]$ArgumentsJson)
    $payload = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"' + $Tool + '","arguments":' + $ArgumentsJson + '}}'
    # PowerShell 5.1 does not escape embedded double quotes when handing an argument to a
    # native executable; without this the body arrives unquoted and every door answers
    # HTTP 400 while the drill reports "the door refused the fixture".
    $escaped = $payload -replace '"', '\"'
    $out = (& docker run --rm --network $script:net curlimages/curl:latest -sS -X POST `
              -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" `
              -H $Header -d $escaped -w "\n#HTTP %{http_code}\n" $Url 2>&1 | Out-String)
    return $out
}

function DoorSees {
    # Turn a door's response into the two facts that matter: did it return the personal
    # fixture, and did it return the ops twin. Both, because only the pair distinguishes a
    # boundary from an outage.
    param([string]$Body)
    $p = if ($Body -match [regex]::Escape("$script:mark personal fixture")) { 1 } else { 0 }
    $o = if ($Body -match [regex]::Escape("$script:mark ops twin"))         { 1 } else { 0 }
    $h = if ($Body -match '#HTTP\s+(\d+)') { $Matches[1] } else { "none" }
    return "http=$h personal=$p ops=$o"
}

function StartMcp {
    param([string]$DbUser, [string]$DbPassword)
    & docker rm -f $script:mcp 2>&1 | Out-Null
    $null = & docker run -d --name $script:mcp --network $script:net `
        -e "DB_HOST=$script:db" -e "DB_PORT=5432" -e "DB_NAME=openbrain" `
        -e "DB_USER=$DbUser" -e "DB_PASSWORD=$DbPassword" `
        -e "MCP_ACCESS_KEY=$script:mcpKey" -e "PORT=8000" `
        -e "EMBEDDING_API_BASE=http://127.0.0.1:9/v1" -e "EMBEDDING_API_KEY=x" -e "EMBEDDING_MODEL=bge-m3" `
        -e "CHAT_API_BASE=http://127.0.0.1:9/v1" -e "CHAT_API_KEY=x" -e "CHAT_MODEL=none" `
        openbrain-mcp-server:local 2>&1
    # Wait for the listener rather than sleeping a guessed interval: a door asked before it
    # is up answers nothing, which this drill would otherwise read as "refused the fixture".
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 500
        if ((& docker logs $script:mcp 2>&1 | Out-String) -match 'Listening on') { return $true }
    }
    return $false
}

# ==========================================================================================
Say "drill-mcp-door-not-superuser - DFU C.8 clause 3"
Say "repo: $script:repo"

# ------------------------------------------------------------------------------------------
Head "0. preconditions"
# Every one of these is a way the drill could run and measure nothing.
foreach ($img in @("openbrain-mcp-server:local", "openbrain-gateway:local")) {
    $null = & docker image inspect $img 2>&1
    if ($LASTEXITCODE -ne 0) {
        Say "  ABORT: CANNOT MEASURE - image $img is not present. This drill runs the REAL"
        Say "         doors; without them there is nothing to attack."
        Cleanup; exit 2
    }
}
Say "  images: openbrain-mcp-server:local, openbrain-gateway:local present"

$migration = Join-Path $script:sqlDir "init-app-role-memory.sql"
if (-not (Test-Path $migration)) {
    Say "  ABORT: CANNOT MEASURE - the migration under test is missing: $migration"
    Cleanup; exit 2
}

if (-not (Test-Path $script:compose)) {
    Say "  ABORT: CANNOT MEASURE - $script:compose does not exist. The whole init chain is"
    Say "         derived from it; an empty OB1/ is an uninitialised submodule:"
    Say "         git submodule update --init"
    Cleanup; exit 2
}

. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")

# ------------------------------------------------------------------------------------------
Head "1. stage the init chain"
if (Test-Path $script:initDir) { Remove-Item -Recurse -Force $script:initDir }
New-Item -ItemType Directory -Force $script:initDir | Out-Null
$chain = @(Get-ObInitChain -ComposePath $script:compose)
# Zero is not an empty chain, it is a regex that stopped matching - and a drill against a
# schemaless database reports every probe as a failure for a reason unrelated to the claim.
if ($chain.Count -eq 0) {
    Say "  ABORT: CANNOT MEASURE - derived NO init files from compose."
    Cleanup; exit 2
}
$staged = Copy-ObInitChain -Chain $chain -SourceDir $script:obDocker -TargetDir $script:initDir
if ($staged -ne $chain.Count) {
    Say "  ABORT: CANNOT MEASURE - $($chain.Count) mounted but $staged staged."
    Cleanup; exit 2
}
Say "  staged $staged init file(s) from compose"
Copy-Item $migration (Join-Path $script:initDir "210-init-app-role-memory.sql")
Say "  staged 210-init-app-role-memory.sql (NOT mounted by compose - the promotion is gated)"

# ------------------------------------------------------------------------------------------
Head "2. throwaway database"
& docker rm -f $script:db 2>&1 | Out-Null
& docker network rm $script:net 2>&1 | Out-Null
& docker network create $script:net 2>&1 | Out-Null
$boot = Start-ObInitdbDetailed -Name $script:db -InitDir $script:initDir -TimeoutSec $DbTimeoutSec `
                               -DockerArgs @("--network", $script:net)
if (-not $boot.Ready) {
    Say "  ABORT: CANNOT MEASURE - the throwaway did not initialise ($($boot.Outcome)): $($boot.Detail)"
    Cleanup; exit 2
}
Say "  up in $($boot.ElapsedSec)s on $script:net"
$initErr = @(Get-ObInitdbErrors -Name $script:db)
if ($initErr.Count -gt 0) {
    Say "  ABORT: CANNOT MEASURE - the init chain logged errors, so the schema is not the one under test:"
    $initErr | Select-Object -First 5 | ForEach-Object { Say "         $_" }
    Cleanup; exit 2
}

# The migration ran as part of the chain. If its own assertions had failed the entrypoint
# would have stopped above - but assert the role's shape here too, because "the file ran"
# and "the role is bound" are different claims.
$r = Sql -Role "postgres" -Password "test" -Query "SELECT rolsuper::text||'|'||rolbypassrls::text FROM pg_roles WHERE rolname='ob_app_memory';"
Probe "P1  init-app-role-memory.sql created ob_app_memory, not super, not bypassrls" "false|false" $r.Out

$null = Sql -Role "postgres" -Password "test" -Query "ALTER ROLE ob_app_memory PASSWORD '$script:appPw';"

# ------------------------------------------------------------------------------------------
Head "3. the fixture - a synthetic personal row and its ops twin"
# BOTH the `exposure` column and the jsonb mirror are written from the same value. The
# fresh chain's policy binds the COLUMN; production's binds `metadata->>'exposure'`
# (measured 2026-09-01, and recorded as drift in the findings note). A fixture that named
# only one of them would be attacking nothing on the other database.
$seed = @"
INSERT INTO thoughts (content, metadata, exposure, user_id) VALUES
 ('$script:mark personal fixture', jsonb_build_object('exposure','personal','share','cloud','type','$script:ftype'), 'personal', 'drill-tenant'),
 ('$script:mark ops twin',         jsonb_build_object('exposure','ops','share','cloud','type','$script:ftype'),      'ops',      NULL);
SELECT count(*)::text FROM thoughts WHERE content LIKE '$script:mark%';
"@
$r = Sql -Role "postgres" -Password "test" -Query $seed
ProbeMatch "P2  both fixture rows written" "2" $r.Out

# SCOPED TO THE TWO SEEDED ROWS BY EXACT CONTENT, not `LIKE '<mark>%'`. The loose form
# counted every later probe's own writes too: G7 writes an ops thought carrying the same
# mark, so H3 read ops=2 and failed a boundary that was in fact intact. A fixture predicate
# that drifts as the drill proceeds is a probe measuring the drill instead of the subject.
$countQ = "SELECT 'personal='||count(*) FILTER (WHERE exposure='personal')||' ops='||count(*) FILTER (WHERE exposure='ops') FROM thoughts WHERE content IN ('$script:mark personal fixture','$script:mark ops twin');"

# ------------------------------------------------------------------------------------------
Head "4. RED - the boundary as it stands: the door connects as postgres"
$r = Sql -Role "postgres" -Password "test" -Query $countQ
Probe "R1  as postgres the personal fixture IS visible in SQL" "personal=1 ops=1" $r.Out

if (-not (StartMcp -DbUser "postgres" -DbPassword "test")) {
    Say "  ABORT: CANNOT MEASURE - the MCP door never started as postgres."
    Cleanup; exit 2
}
$body = McpCall -Url "http://${script:mcp}:8000/mcp" -Header "x-brain-key: $script:mcpKey" `
                -Tool "list_thoughts" -ArgumentsJson ('{"limit":25,"type":"' + $script:ftype + '"}')
Probe "R2  door-openbrain-mcp-door RETURNS the personal fixture (the live failure)" "http=200 personal=1 ops=1" (DoorSees -Body $body)

& docker rm -f $script:gw 2>&1 | Out-Null
$null = & docker run -d --name $script:gw --network $script:net `
    -e "OPENBRAIN_URL=http://${script:mcp}:8000" -e "OPENBRAIN_KEY=$script:mcpKey" -e "GATEWAY_KEY=$script:gwKey" `
    openbrain-gateway:local 2>&1
Start-Sleep -Seconds 6
$body = McpCall -Url "http://${script:gw}:8061/mcp" -Header "Authorization: Bearer $script:gwKey" `
                -Tool "list_thoughts" -ArgumentsJson ('{"limit":25,"type":"' + $script:ftype + '"}')
Probe "R3  door-cloud-search-thoughts RETURNS it too, through the real gateway" "http=200 personal=1 ops=1" (DoorSees -Body $body)

# ------------------------------------------------------------------------------------------
Head "5. GREEN - one change: openbrain-mcp connects as ob_app_memory"
$r = Sql -Role "ob_app_memory" -Password $script:appPw -Query "SELECT current_user||'|'||rolsuper::text||'|'||rolbypassrls::text FROM pg_roles WHERE rolname=current_user;"
Probe "G1  the door's role is not super and not bypassrls" "ob_app_memory|false|false" $r.Out

$r = Sql -Role "ob_app_memory" -Password $script:appPw -Query $countQ
Probe "G2  in SQL the personal row is gone AND the ops twin remains (boundary, not blackout)" "personal=0 ops=1" $r.Out

if (-not (StartMcp -DbUser "ob_app_memory" -DbPassword $script:appPw)) {
    Say "  ABORT: CANNOT MEASURE - the MCP door never started as ob_app_memory."
    Cleanup; exit 2
}
$body = McpCall -Url "http://${script:mcp}:8000/mcp" -Header "x-brain-key: $script:mcpKey" `
                -Tool "list_thoughts" -ArgumentsJson ('{"limit":25,"type":"' + $script:ftype + '"}')
Probe "G3  door-openbrain-mcp-door NO LONGER returns it, and still returns the control" "http=200 personal=0 ops=1" (DoorSees -Body $body)

$body = McpCall -Url "http://${script:gw}:8061/mcp" -Header "Authorization: Bearer $script:gwKey" `
                -Tool "list_thoughts" -ArgumentsJson ('{"limit":25,"type":"' + $script:ftype + '"}')
Probe "G4  door-cloud-search-thoughts closes with it - one connection, two doors" "http=200 personal=0 ops=1" (DoorSees -Body $body)

# ------------------------------------------------------------------------------------------
Head "6. the role cannot simply step around the boundary"
$r = Sql -Role "ob_app_memory" -Password $script:appPw -Query "SET ROLE ob_plane_personal; SELECT 1;"
ProbeMatch "G5  ob_app_memory CANNOT SET ROLE into the personal plane" "permission denied to set role" $r.Out

$r = Sql -Role "ob_app_memory" -Password $script:appPw `
        -Query "INSERT INTO thoughts (content, metadata, exposure) VALUES ('$script:mark personal write', jsonb_build_object('exposure','personal'), 'personal');"
ProbeMatch "G6  a personal WRITE is refused LOUDLY, not silently dropped" "violates row-level security policy" $r.Out

# ------------------------------------------------------------------------------------------
Head "7. what still works - the ops plane is not collateral damage"
$r = Sql -Role "ob_app_memory" -Password $script:appPw `
        -Query "INSERT INTO thoughts (content, metadata, exposure) VALUES ('$script:mark ops write', jsonb_build_object('exposure','ops'), 'ops') RETURNING 'wrote';"
ProbeMatch "G7  an ops thought write still succeeds as ob_app_memory" "wrote" $r.Out

# ------------------------------------------------------------------------------------------
Head "8. THE ORDERING HAZARD, measured on both grant shapes"
# This drill's database has `init-graph-plane-rls.sql` (200-) applied, because it is in the
# compose chain. PRODUCTION DOES NOT: `ob_relation_governed` is absent there and 200 has
# written none of its comments (measured 2026-09-01). 200 REVOKEs INSERT/UPDATE/DELETE on
# the agent-memory corpus FROM service_role, which is where ob_app_memory's write privilege
# comes from - so the SAME role behaves differently on the two databases, and the promotion
# is safe today for a reason that expires when 200 lands.
#
# Both shapes are measured here so the promotion plan's ordering claim is a result and not
# a prediction.
$r = Sql -Role "ob_app_memory" -Password $script:appPw `
        -Query "INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata, exposure) VALUES ('$script:ftype','check','s','c', jsonb_build_object('exposure','ops'),'ops');"
ProbeMatch "H1  WITH 200 applied (fresh volume): the agent-memory write is DENIED" "permission denied for table agent_memories" $r.Out

$null = Sql -Role "postgres" -Password "test" -Query "GRANT INSERT, UPDATE, DELETE ON agent_memories TO service_role;"
$r = Sql -Role "ob_app_memory" -Password $script:appPw `
        -Query "INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata, exposure) VALUES ('$script:ftype','check','s','c', jsonb_build_object('exposure','ops'),'ops') RETURNING 'wrote';"
ProbeMatch "H2  WITHOUT 200 (production's shape today): the same write SUCCEEDS" "wrote" $r.Out

$r = Sql -Role "ob_app_memory" -Password $script:appPw -Query $countQ
Probe "H3  ...and re-opening that grant does NOT re-open the read boundary" "personal=0 ops=1" $r.Out

# ------------------------------------------------------------------------------------------
Head "9. revert round-trips"
& docker rm -f $script:mcp 2>&1 | Out-Null
& docker rm -f $script:gw 2>&1 | Out-Null
$revert = Join-Path $script:sqlDir "revert-app-role-memory.sql"
if (Test-Path $revert) {
    $null = & docker cp $revert "$($script:db):/tmp/revert.sql" 2>&1
    $out = (& docker exec $script:db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/revert.sql 2>&1 | Out-String)
    $r = Sql -Role "postgres" -Password "test" -Query "SELECT count(*)::text FROM pg_roles WHERE rolname='ob_app_memory';"
    Probe "V1  revert-app-role-memory.sql removes the role cleanly" "0" $r.Out
} else {
    Say "  (revert-app-role-memory.sql not present - skipped)"
}

# ------------------------------------------------------------------------------------------
Head "result"
Say "  $script:pass passed, $script:fail failed"
Cleanup
if ($script:fail -gt 0) { exit 1 }
exit 0
