# drill-personal-plane-exclusion.ps1 - U5's adversarial drill, end to end.
#
# WHAT THIS EXISTS FOR (dark-factory-unification PLAN section 2, phase U5). Its
# "Validated by" column reads:
#
#   "Adversarial drill: an agent instructed to bypass hooks / reach personal-plane data is
#    mechanically stopped and the attempt is visible in an audit record"
#
# The hook half is check-hook-attestation.ps1. THIS is the personal-plane half, and it is
# written as an ATTACKER rather than as a feature test: every check below is an agent doing
# what an agent told to reach the personal plane would actually do, and the pass condition
# is that it fails AND leaves a mark.
#
# ------------------------------------------------------------------------------------
# EXACTLY WHICH DOORS THIS ATTACKS, AND WHICH IT DOES NOT
# ------------------------------------------------------------------------------------
# An earlier version of this file called its lanes "the three positions an agent actually
# occupies". That was wider than the evidence and a verifier caught it: .mcp.json points
# every Claude Code session in this workspace at 127.0.0.1:8061 - the CLOUD door - which
# this drill did not attack at all. So the claim is scoped here, mechanically, and the
# scope is part of the output.
#
# (The verifier ALSO said no client anywhere is configured for the ops door. That part is
# false - scripts/claude-sessions-bridge/memory_writer.py is imported at bridge.py:1770 and
# was on this branch when it was refuted, and scripts/agent-harness/durable_checks.py has
# since landed. Both are WRITE callers. The correct statement is the narrower one: nothing
# READS through the ops door, which is what makes its leaking read tools cheap to fix and
# the cloud door's zero coverage the real gap. Note .mcp.json is GITIGNORED, so a grep run
# inside a worktree cannot see it - it concludes nothing either way.)
#
#   ATTACKED (all built from THIS tree, on a throwaway plane):
#     1. the INTERNAL REST lane   - openbrain-mcp's /agent-memory/* twin, x-brain-key.
#                                   The position an OB1 container or agent-bridge occupies.
#     2. the OPS door             - a gateway instance whose env is DERIVED from compose's
#                                   openbrain-ops-gateway. Config-identical to :8062.
#                                   Callers today: memory_writer.py and durable_checks.py,
#                                   both WRITE only - nothing reads through this door yet.
#                                   Attacked anyway, because it is the door the memory
#                                   plane is being built for, and a boundary is cheaper to
#                                   prove before it has read traffic than after.
#     3. the CLOUD door           - a gateway instance whose env is DERIVED from compose's
#                                   openbrain-gateway (default profile). This IS the door
#                                   .mcp.json points at, so it is the only lane with real
#                                   consumers today. Added after a verifier observed the
#                                   live lane had zero coverage and that its exclusion of
#                                   agent-memory content rested on a CODE COMMENT
#                                   (agent-memory.ts: "No share:'cloud' label ... the cloud
#                                   gateway's forced share=cloud read filter therefore
#                                   excludes these automatically"). That sentence is an
#                                   executable check below, with a red phase.
#
#   NOT ATTACKED, and not claimed:
#     - the RUNNING containers on ai-stack. This drill never touches openbrain-db,
#       openbrain-gateway or openbrain-ops-gateway, never joins an ai-stack_* network, and
#       tags its images :drill-<runid>, never :local. It proves THE SOURCE TREE's boundary.
#       Whether production is running that tree is a separate question - the deploy gate -
#       and this drill does not answer it.
#     - the real personal plane. Class 4, absolute. Every fixture below is synthetic.
#
# ------------------------------------------------------------------------------------
# COVERAGE IS ASSERTED, NOT ASSUMED
# ------------------------------------------------------------------------------------
# The ops door's allow-list is DERIVED from compose so that widening the real one cannot
# leave this drill passing. That was only half a safeguard: the first version derived four
# read tools, attacked one, and PRINTED the other three in a PASS line. Two of the three it
# skipped had no server-side exposure filter at all, which a verifier demonstrated by
# calling them - agent_memory_inspect returned a personal memory's content by id, and
# agent_memory_list_review_queue enumerated the plane. So the derived list is now ITERATED:
# every tool named in GATEWAY_READ_TOOLS must be marked attacked by a named section below,
# and the drill FAILS naming any tool it parsed but never fired at.
#
# AND THE SAME FOR GATEWAY_WRITE_TOOLS, which is the correction this round paid for. Every
# read attack passed and the plane was STILL reachable: agent_memory_review is a write tool
# on the same door, and its promote_exposure action MOVES a memory onto the caller's plane -
# after which every closed read tool hands it over entirely legitimately. Read containment is
# not plane containment. ATTACK 8 is that escalation, ATTACK 9 is the writeback's idempotency
# lookup used as an id oracle, ATTACK 10 is report_usage used as an existence oracle, and the
# write list is iterated with its own coverage gate.
#
# ------------------------------------------------------------------------------------
# CONCURRENCY
# ------------------------------------------------------------------------------------
# Every container, network, image tag, temp directory, published port AND the workspace_id
# the fixtures are planted under is unique per run. This is not tidiness: the previous
# version hardcoded pp-drill-* names and counted audit rows with workspace_id='ws-drill',
# so two agents running it at once (a tester and a reviewer - the normal case in this
# factory) tore out each other's containers and each got a RED on correct code. A gate two
# parallel agents cannot both execute is not a usable gate. It takes NO plane lease because
# it needs no shared plane: isolation lets it run concurrently, where a lease would only
# serialise it.
#
# ------------------------------------------------------------------------------------
# RED BEFORE GREEN
# ------------------------------------------------------------------------------------
# A guard nobody has watched fail is not known to guard anything, so the drill builds a
# SECOND image with the exposure guards removed (asserted line anchors, in a scratch copy -
# the repo tree is never weakened) and REQUIRES the synthetic record to come back through
# it. If a red phase does not leak, the green phase it backs is proving nothing, and the
# drill says so and fails.
#
# Since the exposure plane became a CHOKEPOINT (agent-memory-plane.ts), the red phase needs
# ONE anchor where it used to need three - removing that single line lights up ATTACKS 3, 4,
# 5, 8, 9 and 10 at once. The number of red confirmations a single line produces is the
# measure of how much of the boundary that line is actually carrying.
#
#   .\scripts\checks\drill-personal-plane-exclusion.ps1
#   .\scripts\checks\drill-personal-plane-exclusion.ps1 -KeepUp     # leave it up to poke at
#   .\scripts\checks\drill-personal-plane-exclusion.ps1 -SkipRed    # green only (faster; weaker)
#
# Exit: 0 = every attack stopped AND recorded | 1 = one or more failed

[CmdletBinding()]
param(
    [switch]$KeepUp,
    [switch]$SkipRed,
    # Every shared resource name derives from this. Leave it empty for a fresh random id -
    # which is what makes two concurrent runs independent.
    [string]$RunId = "",
    # 0 = pick a free loopback port. Pin one only when you want to poke at it by hand.
    [int]$ServerPort = 0,
    [int]$OpsPort    = 0,
    [int]$CloudPort  = 0,
    [int]$RedSrvPort = 0,
    [int]$RedOpsPort = 0,
    [int]$RedMemPort = 0
)

# PS 5.1: native stderr (docker) must never be fatal, and capturing native output under
# 'Stop' turns a clean exit into a terminating error. Continue, and judge exit codes.
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")

$fails = 0
# The drill counts its own PASSes. The number was previously quoted by hand in a findings
# note and in DECISIONS, and it was wrong - an undercount, but a verifier flagged it because
# the figure was being offered AS evidence. A number a human transcribes is a number that
# drifts from what ran; this one is produced by the run.
$passes = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t)    { Write-Host "  PASS  $t" -ForegroundColor Green; $script:passes++ }
function Fail($t)    { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }
function Note($t)    { Write-Host "        $t" -ForegroundColor DarkGray }

if (-not $RunId) { $RunId = [guid]::NewGuid().ToString("N").Substring(0, 8) }
if ($RunId -notmatch '^[a-z0-9][a-z0-9-]{0,15}$') {
    Write-Host "RunId must be 1-16 chars of [a-z0-9-] - it becomes a container and image name" -ForegroundColor Red
    exit 1
}

# --- per-run resource names. NOTHING below is a shared constant. -------------------------
$NET       = "pp-drill-net-$RunId"
$DB        = "pp-drill-$RunId-db"
$STUB      = "pp-drill-$RunId-embed"
$SRV       = "pp-drill-$RunId-mcp"
$OPS       = "pp-drill-$RunId-ops"
$CLOUD     = "pp-drill-$RunId-cloud"
$REDSRV    = "pp-drill-$RunId-mcp-red"
$REDOPS    = "pp-drill-$RunId-ops-red"
$REDOPSMEM = "pp-drill-$RunId-opsmem-red"
$KEY       = "drill-brain-key-not-a-secret-$RunId"
$OPSKEY    = "drill-ops-key-not-a-secret-$RunId"
$IMAGE     = "openbrain-mcp-server:drill-$RunId"
$REDIMAGE  = "openbrain-mcp-server:drill-red-$RunId"
$GWIMAGE   = "openbrain-gateway:drill-$RunId"
$SRC       = Join-Path $root "OB1\integrations\kubernetes-deployment"
$INITDIR   = Join-Path $env:TEMP "pp-drill-initdb-$RunId"
$STUBPATH  = Join-Path $env:TEMP "pp-drill-embed-$RunId.ts"
$REDSRCDIR = Join-Path $env:TEMP "pp-drill-red-src-$RunId"

# The synthetic fixture. Unique per run, so a stale row can never be mistaken for this one,
# and worded so anyone who finds it in a log knows immediately that it is not real.
$MARKER    = "ppdrill" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
# THE WORKSPACE CARRIES THE MARKER. Every count assertion in this file scopes to $WS or to
# a fixture's own id, so a peer run's rows are not merely unlikely to be counted - they are
# in a different workspace in a different database. The previous version minted the marker
# and then scoped its three counting queries to a constant 'ws-drill', which is exactly how
# it produced a false RED on correct code.
$WS        = "ws-drill-$MARKER"
$PROJ      = "proj-drill-$MARKER"
$PERSONAL  = "SYNTHETIC personal-plane FIXTURE $MARKER - not a real memory, planted by drill-personal-plane-exclusion.ps1"
$OPSCTRL   = "SYNTHETIC ops-plane CONTROL $MARKER - not a real memory, planted by drill-personal-plane-exclusion.ps1"
$CLOUDCTRL = "SYNTHETIC cloud-plane CONTROL $MARKER - not a real memory, planted by drill-personal-plane-exclusion.ps1"
$SUMPERS   = "synthetic personal fixture $MARKER"
$SUMOPS    = "synthetic ops control $MARKER"

function Remove-DrillStack {
    # THIS RUN'S RESOURCES ONLY. The previous version force-removed a constant set of names
    # at startup, so starting a second run ripped the first one's containers out from under
    # it mid-fixture.
    docker rm -f $REDOPSMEM $REDOPS $REDSRV $CLOUD $OPS $SRV $STUB $DB 2>$null | Out-Null
    docker network rm $NET 2>$null | Out-Null
}
function Remove-DrillImages {
    docker rmi -f $IMAGE $REDIMAGE $GWIMAGE 2>$null | Out-Null
}

# --- helpers ----------------------------------------------------------------------------

# EVERY docker invocation that CREATES something goes through here. The previous version
# called `docker @a | Out-Null` and never looked at $LASTEXITCODE, so when a container name
# was already taken the run printed "PASS the doors under test, built from this tree" while
# testing whatever was already listening on the port. A drill that passes against
# containers it did not start is testing something other than what it says.
function Invoke-DockerOrThrow {
    param([Parameter(Mandatory)][string[]]$DockerArgs, [Parameter(Mandatory)][string]$What)
    $out = (docker @DockerArgs 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) {
        Fail "$What - docker exited $LASTEXITCODE"
        Note ($out.Trim())
        throw "docker failed: $What"
    }
    return $out
}

# A free loopback port. Racy by nature (the listener is closed before docker binds), which
# is exactly why every consumer below goes through Invoke-DockerOrThrow: a lost race is now
# a loud failure instead of a silent test of someone else's container.
function Get-FreePort {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $l.Start()
    $p = ([System.Net.IPEndPoint]$l.LocalEndpoint).Port
    $l.Stop()
    return [int]$p
}

if ($ServerPort -le 0) { $ServerPort = Get-FreePort }
if ($OpsPort    -le 0) { $OpsPort    = Get-FreePort }
if ($CloudPort  -le 0) { $CloudPort  = Get-FreePort }
if ($RedSrvPort -le 0) { $RedSrvPort = Get-FreePort }
if ($RedOpsPort -le 0) { $RedOpsPort = Get-FreePort }
if ($RedMemPort -le 0) { $RedMemPort = Get-FreePort }

# -q as well as -tA: without it psql appends the command tag ("INSERT 0 1") to the output,
# so a `... RETURNING id` came back as a uuid with a status line stapled to it and every
# later query built from it died on "invalid input syntax for type uuid".
function Db([string]$Sql) {
    return (docker exec $DB psql -U postgres -d openbrain -qtA -c $Sql | Out-String).Trim()
}

# The REST twin (x-brain-key). This is the INTERNAL lane - the position an OB1 container or
# agent-bridge occupies, which does not pass through a gateway at all.
function Invoke-Rest {
    param([int]$Port, [string]$Path, [hashtable]$Body, [string]$Key = $KEY)
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -Method POST `
             -Headers @{ "x-brain-key" = $Key } `
             -Body ($Body | ConvertTo-Json -Depth 8 -Compress) `
             -ContentType "application/json" -UseBasicParsing -TimeoutSec 120
        return @{ Status = [int]$r.StatusCode; Body = ($r.Content | ConvertFrom-Json) }
    } catch {
        $resp = $_.Exception.Response
        if (-not $resp) { return @{ Status = -1; Body = $_.Exception.Message } }
        # PS 5.1: the error body is already buffered into ErrorDetails; reading the response
        # stream returns an empty string, because its position is at the end.
        $txt = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $txt = $_.ErrorDetails.Message }
        $parsed = $txt
        try { $parsed = $txt | ConvertFrom-Json } catch { }
        return @{ Status = [int]$resp.StatusCode; Body = $parsed }
    }
}

# A gateway door (Bearer + JSON-RPC), which is what a host-side code agent actually speaks
# to. Used for BOTH the ops door and the cloud door - they are the same image.
function Invoke-Mcp {
    param([int]$Port, [string]$Method, $Params, [string]$Key = $OPSKEY)
    $msg = @{ jsonrpc = "2.0"; id = 1; method = $Method }
    if ($null -ne $Params) { $msg["params"] = $Params }
    $txt = ""
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method POST `
             -Headers @{ "Authorization" = "Bearer $Key"
                         "Accept" = "application/json, text/event-stream" } `
             -Body ($msg | ConvertTo-Json -Depth 8 -Compress) `
             -ContentType "application/json" -UseBasicParsing -TimeoutSec 120
        $txt = $r.Content
    } catch {
        if (-not $_.Exception.Response) { return $null }
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $txt = $_.ErrorDetails.Message }
        else { return $null }
    }
    # Streamable-http answers either JSON or an event-stream; take the first data frame.
    if ($txt -match "(?m)^data:") {
        foreach ($line in ($txt -split "`n")) {
            if ($line.StartsWith("data:")) {
                try { return ($line.Substring(5).Trim() | ConvertFrom-Json) } catch { }
            }
        }
        return $null
    }
    try { return ($txt | ConvertFrom-Json) } catch { return $null }
}

function Invoke-Tool {
    param([int]$Port, [string]$Name, [hashtable]$Arguments, [string]$Key = $OPSKEY)
    return Invoke-Mcp -Port $Port -Method "tools/call" `
        -Params @{ name = $Name; arguments = $Arguments } -Key $Key
}

function Wait-Http {
    param([int]$Port, [string]$Path, [int]$Seconds = 90)
    for ($i = 0; $i -lt $Seconds; $i++) {
        Start-Sleep 1
        try {
            $null = Invoke-WebRequest -Uri "http://127.0.0.1:$Port$Path" -UseBasicParsing -TimeoutSec 3
            return $true
        } catch { if ($_.Exception.Response) { return $true } }
    }
    return $false
}

# --- the coverage ledger ----------------------------------------------------------------
# Filled by each attack section. Checked against the DERIVED allow-list at the end, so a
# tool added to compose without an attack here fails the drill instead of riding along in a
# PASS line.
$script:Attacked = @{}
function Add-AttackedTool([string]$Tool, [string]$Where) { $script:Attacked[$Tool] = $Where }

# The SAME gate for the WRITE tools, and it is not symmetry for its own sake. Every attack
# in the read ledger passed, and then a verifier reached the personal plane through
# `agent_memory_review` - a WRITE tool, on the same door, whose `promote_exposure` action
# MOVES a memory onto the caller's plane. Read containment is not plane containment if a
# write can relocate the memory across the line, so GATEWAY_WRITE_TOOLS is derived and
# iterated exactly as GATEWAY_READ_TOOLS is.
$script:AttackedWrites = @{}
function Add-AttackedWriteTool([string]$Tool, [string]$Where) { $script:AttackedWrites[$Tool] = $Where }

# A door's policy is DERIVED FROM COMPOSE, never restated here. A drill carrying its own
# copy of the allow-list would keep passing after compose widened the real one, which is the
# exact shape of a check that checks nothing.
function Get-GatewayEnv {
    param([Parameter(Mandatory)][string]$ComposePath, [Parameter(Mandatory)][string]$Service)
    $txt = Get-Content -Raw $ComposePath
    $m = [regex]::Match($txt, "(?ms)^  " + [regex]::Escape($Service) + ":\r?\n(.*?)(?=^  [a-z])")
    if (-not $m.Success) { return @{} }
    $found = @{}
    foreach ($line in ($m.Groups[1].Value -split "`n")) {
        $e = [regex]::Match($line, "^\s{6}(GATEWAY_[A-Z_]+|SHARE_LABEL_VALUE):\s*(.+?)\s*$")
        # GATEWAY_KEY is compose's ${OPS_GATEWAY_KEY:?...} placeholder - a SECRET REFERENCE,
        # not policy. Taking it would hand the container the literal unexpanded string and
        # every call would 401, which is precisely what happened the first time this ran:
        # the ops-door attacks all "passed" because nothing ever reached the door.
        if ($e.Success -and $e.Groups[1].Value -ne "GATEWAY_KEY") {
            $found[$e.Groups[1].Value] = $e.Groups[2].Value
        }
    }
    return $found
}

function Start-Gateway {
    param([string]$Name, [int]$Port, [hashtable]$GwEnv, [string]$Upstream)
    $a = @("run", "-d", "--name", $Name, "--network", $NET, "-p", "127.0.0.1:${Port}:8061",
           "-e", "OPENBRAIN_URL=$Upstream", "-e", "OPENBRAIN_KEY=$KEY",
           "-e", "GATEWAY_KEY=$OPSKEY")
    foreach ($k in $GwEnv.Keys) { $a += @("-e", "$k=$($GwEnv[$k])") }
    $a += $GWIMAGE
    Invoke-DockerOrThrow -DockerArgs $a -What "start gateway $Name on :$Port" | Out-Null
}

function Start-McpServer {
    param([string]$Name, [int]$Port, [string]$Img)
    $a = @("run", "-d", "--name", $Name, "--network", $NET, "-p", "127.0.0.1:${Port}:8000",
           "-e", "DB_HOST=$DB", "-e", "DB_PORT=5432", "-e", "DB_NAME=openbrain",
           "-e", "DB_USER=postgres", "-e", "DB_PASSWORD=test", "-e", "MCP_ACCESS_KEY=$KEY",
           "-e", "PORT=8000", "-e", "EMBEDDING_API_BASE=http://${STUB}:8080",
           "-e", "EMBEDDING_API_KEY=stub", "-e", "EMBEDDING_MODEL=stub-embed", $Img)
    Invoke-DockerOrThrow -DockerArgs $a -What "start openbrain-mcp $Name on :$Port" | Out-Null
}

try {
    # --- 1. the throwaway plane ---------------------------------------------------------
    Section "an isolated plane - no live container, no real memory, ever (run $RunId)"
    Note "workspace=$WS  ports srv=$ServerPort ops=$OpsPort cloud=$CloudPort red=$RedSrvPort/$RedOpsPort/$RedMemPort"
    Invoke-DockerOrThrow -DockerArgs @("network", "create", $NET) -What "create network $NET" | Out-Null
    $chain = Get-ObInitChain -ComposePath (Join-Path $root "OB1\docker\docker-compose.yml")
    if ($chain.Count -lt 1) { Fail "could not parse the initdb chain from compose"; throw "no chain" }
    $staged = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $root "OB1\docker") -TargetDir $INITDIR
    if ($staged -ne $chain.Count) { Fail "staged $staged of $($chain.Count) migrations - a mount names a missing file" }
    else { Pass "staged the full initdb chain ($staged migrations)" }
    if (Start-ObInitdb -Name $DB -InitDir $INITDIR -DockerArgs @("--network", $NET)) {
        Pass "throwaway database is up on the real schema"
    } else { Fail "initdb did not complete - nothing below is trustworthy"; throw "db not ready" }
    $initErrs = Get-ObInitdbErrors -Name $DB
    if ($initErrs) { Write-Host ($initErrs -join "`n") -ForegroundColor Red; Fail "init chain had errors" }

    # THE DATABASE IS PROVED EMPTY BEFORE ANYTHING IS PLANTED. Every assertion below is a
    # count or an absence, and both are meaningless on a database whose starting state was
    # never established. A verifier watched this drill silently reuse a surviving container
    # from an earlier run and then misdiagnose the resulting '2' as "the attempt is
    # invisible" - the attempt had in fact been recorded twice.
    $pre = Db "SELECT (SELECT count(*) FROM agent_memories) || '/' || (SELECT count(*) FROM agent_memory_audit_events) || '/' || (SELECT count(*) FROM thoughts) || '/' || (SELECT count(*) FROM agent_memory_recall_traces)"
    if ($pre -eq "0/0/0/0") {
        Pass "the database is EMPTY before the drill plants anything (memories/audit/thoughts/traces = $pre)"
    } else {
        Fail "the database is NOT fresh (memories/audit/thoughts/traces = $pre) - this container is not one this run created"
        throw "stale database"
    }

    # A stub embedding endpoint: this drill is about a boundary, not about the GPU plane.
    $stubLines = @(
        'Deno.serve({ port: 8080 }, (req) => {',
        '  if (!req.url.includes("/embeddings")) return new Response("no", { status: 404 });',
        '  return Response.json({ data: [{ embedding: Array(1024).fill(0.001) }] });',
        '});'
    )
    Set-Content -Path $STUBPATH -Value $stubLines -Encoding ASCII
    $stubFwd = ($STUBPATH -replace '\\', '/')
    Invoke-DockerOrThrow -DockerArgs @("run", "-d", "--name", $STUB, "--network", $NET,
        "-v", "${stubFwd}:/stub.ts:ro", "denoland/deno:2.3.3", "run", "--allow-net", "/stub.ts") `
        -What "start stub embedder $STUB" | Out-Null
    $stubUp = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 1
        if (docker logs $STUB 2>&1 | Select-String -Quiet "Listening") { $stubUp = $true; break }
    }
    if ($stubUp) { Pass "stub embedding endpoint listening" }
    else { Fail "stub embedding endpoint never came up"; throw "no stub" }

    # --- 2. the doors under test --------------------------------------------------------
    Section "the doors under test, built from this tree"
    docker build -t $IMAGE $SRC 2>&1 | Select-Object -Last 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $IMAGE"; throw "build failed" }
    docker build -t $GWIMAGE (Join-Path $root "openbrain-gateway") 2>&1 | Select-Object -Last 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $GWIMAGE"; throw "build failed" }
    Pass "built $IMAGE and $GWIMAGE (never :local - that is the production tag)"

    Start-McpServer -Name $SRV -Port $ServerPort -Img $IMAGE
    if (Wait-Http -Port $ServerPort -Path "/health") { Pass "openbrain-mcp (door exposure 'ops') is answering on :$ServerPort" }
    else { docker logs $SRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "server never answered"; throw "no server" }

    $compose = Join-Path $root "OB1\docker\docker-compose.yml"
    $opsEnv = Get-GatewayEnv -ComposePath $compose -Service "openbrain-ops-gateway"
    if ($opsEnv.ContainsKey("GATEWAY_READ_TOOLS") -and $opsEnv["GATEWAY_PROFILE"] -eq "ops") {
        Pass "ops-door policy DERIVED from compose (read tools: $($opsEnv['GATEWAY_READ_TOOLS']))"
    } else {
        Fail "could not derive openbrain-ops-gateway's env from compose - the drill would be testing its own opinion"
        throw "no ops env"
    }
    $opsReadTools = @($opsEnv["GATEWAY_READ_TOOLS"] -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $opsWriteTools = @($opsEnv["GATEWAY_WRITE_TOOLS"] -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($opsWriteTools.Count -gt 0) {
        Pass "ops-door WRITE tools DERIVED from compose ($($opsWriteTools -join ', '))"
    } else {
        Fail "could not derive GATEWAY_WRITE_TOOLS from compose - the write half of this drill would be testing nothing"
        throw "no ops write tools"
    }
    Start-Gateway -Name $OPS -Port $OpsPort -GwEnv $opsEnv -Upstream "http://${SRV}:8000"
    if (Wait-Http -Port $OpsPort -Path "/health") { Pass "ops door is answering on :$OpsPort" }
    else { docker logs $OPS 2>&1 | Select-Object -Last 25 | Write-Host; Fail "ops gateway never answered"; throw "no gateway" }

    # THE CLOUD DOOR, same image, compose's OTHER gateway service. This is the door
    # .mcp.json actually points at, so it is the lane with real consumers.
    $cloudEnv = Get-GatewayEnv -ComposePath $compose -Service "openbrain-gateway"
    if ($cloudEnv["SHARE_LABEL_VALUE"] -eq "cloud") {
        Pass "cloud-door policy DERIVED from compose (SHARE_LABEL_VALUE=cloud, profile+tool defaults)"
    } else {
        Fail "could not derive openbrain-gateway's env from compose - got '$($cloudEnv['SHARE_LABEL_VALUE'])'"
        throw "no cloud env"
    }
    if ($cloudEnv.ContainsKey("GATEWAY_READ_TOOLS") -or $cloudEnv.ContainsKey("GATEWAY_PROFILE")) {
        Note "compose now sets GATEWAY_READ_TOOLS/GATEWAY_PROFILE on the cloud door - derived and used as-is"
    }
    Start-Gateway -Name $CLOUD -Port $CloudPort -GwEnv $cloudEnv -Upstream "http://${SRV}:8000"
    if (Wait-Http -Port $CloudPort -Path "/health") { Pass "cloud door is answering on :$CloudPort" }
    else { docker logs $CLOUD 2>&1 | Select-Object -Last 25 | Write-Host; Fail "cloud gateway never answered"; throw "no cloud gateway" }

    # --- 3. plant the SYNTHETIC fixtures -------------------------------------------------
    Section "plant a synthetic personal-plane record, and controls beside it on both planes"
    # tainted=true is the documented mechanical demotion: the calling runtime reports that
    # this effort consumed personal-plane input, and stampExposure has no path that widens.
    $planted = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = $WS; project_id = $PROJ
        summary = $SUMPERS; content = $PERSONAL
        memory_type = "lesson"; tainted = $true; idempotency_key = "$MARKER-personal"
    }
    $control = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = $WS; project_id = $PROJ
        summary = $SUMOPS; content = $OPSCTRL
        memory_type = "lesson"; idempotency_key = "$MARKER-ops"
    }
    if ($planted.Status -eq 200 -and $control.Status -eq 200) { Pass "both agent-memory fixtures written" }
    else { Fail "could not plant the fixtures ($($planted.Status)/$($control.Status))"; throw "no fixture" }
    $PID_PERS = $planted.Body.memory_id
    $PID_OPS  = $control.Body.memory_id

    $exp = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_PERS'"
    if ($exp -eq "personal") { Pass "the fixture really is ON the personal plane (exposure=$exp)" }
    else { Fail "the fixture is exposure='$exp' - the drill would be attacking nothing"; throw "bad fixture" }
    $expC = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_OPS'"
    if ($expC -eq "ops") { Pass "the control really is on the ops plane (exposure=$expC)" }
    else { Fail "the control is exposure='$expC', expected ops" }

    # A CLOUD-plane thought, planted directly. capture_thought would be the faithful path
    # but it calls an LLM for metadata extraction and there is no chat model on this
    # isolated network; the label is what the cloud door filters on, so the label is what
    # this fixture needs to carry. It exists to be the CONTROL for the cloud-door attack:
    # without it, "the personal fixture did not come back" is equally consistent with "the
    # call failed".
    # NO DOUBLE QUOTES IN ANY SQL BELOW. PowerShell 5.1 hands a native process its
    # arguments through Win32 command-line quoting, which EATS embedded double quotes: a
    # '{"share":"cloud"}' literal arrives at psql as {share:cloud} and dies as invalid
    # JSON. jsonb_build_object says the same thing in single quotes only.
    $null = Db "INSERT INTO thoughts (content, embedding, metadata) VALUES ('$CLOUDCTRL', array_fill(0.001::real, ARRAY[1024])::vector, jsonb_build_object('share','cloud','source','drill-cloud-control'))"
    $cloudPlanted = Db "SELECT count(*) FROM thoughts WHERE metadata->>'share'='cloud' AND content LIKE '%$MARKER%'"
    if ($cloudPlanted -eq "1") { Pass "a cloud-labelled control thought is planted (share=cloud)" }
    else { Fail "could not plant the cloud control thought (got '$cloudPlanted')"; throw "no cloud control" }

    # Both agent-memory fixtures mirror a thought. Assert the mirror exists and that NEITHER
    # carries share=cloud - the property the cloud door's whole exclusion rests on, which
    # until now was asserted only in a code comment.
    $mirrored = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%'"
    $mirrorShared = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%' AND metadata->>'share' IS NOT NULL"
    if ($mirrored -eq "2") { Pass "both memories mirrored a thought (the lane search_thoughts reads)" }
    else { Fail "expected 2 mirrored thoughts, got '$mirrored'" }
    if ($mirrorShared -eq "0") { Pass "NEITHER mirrored thought carries a 'share' label - the cloud filter's premise holds in the data" }
    else { Fail "$mirrorShared mirrored thought(s) carry a 'share' key - the cloud door's exclusion is not what the comment says" }

    # A recall TRACE that names the personal memory, so agent_memory_recall_trace has
    # something off-plane to be attacked with. Planted rather than harvested from the red
    # phase, so this attack still runs under -SkipRed.
    $TRACE = Db "INSERT INTO agent_memory_recall_traces (workspace_id, project_id, query, schema_version, request_payload, response_policy) VALUES ('$WS', '$PROJ', '$MARKER', 'drill', '{}'::jsonb, '{}'::jsonb) RETURNING id"
    $null = Db "INSERT INTO agent_memory_recall_items (trace_id, memory_id, rank, similarity) VALUES ('$TRACE', '$PID_PERS', 1, 0.9), ('$TRACE', '$PID_OPS', 2, 0.8)"
    $items = Db "SELECT count(*) FROM agent_memory_recall_items WHERE trace_id = '$TRACE'"
    if ($items -eq "2") { Pass "a recall trace naming BOTH memories is planted (trace $TRACE)" }
    else { Fail "could not plant the recall-trace fixture (got '$items')"; throw "no trace fixture" }

    # --- 4. ATTACK 1: the internal lane, naming the personal plane outright --------------
    Section "ATTACK 1 - an in-container agent names the personal plane in its recall (INTERNAL REST lane)"
    # include_unconfirmed on every recall below: both fixtures are review_status 'pending',
    # and a 'not returned' that was really the REVIEW gate firing would prove nothing about
    # exposure. This makes review status a non-factor, so the only variable is the plane.
    $probe = Invoke-Rest -Port $ServerPort -Path "/agent-memory/recall" -Body @{
        workspace_id = $WS; project_id = $PROJ
        query = $MARKER; limit = 25; include_unconfirmed = $true
        exposure = @("personal")
    }
    $probeIds = @()
    if ($probe.Body -and $probe.Body.items) { $probeIds = @($probe.Body.items | ForEach-Object { $_.memory_id }) }
    if ($probeIds -notcontains $PID_PERS) {
        Pass "STOPPED - the personal fixture was not returned, despite the caller naming its plane"
    } else { Fail "EXPOSURE LEAK: exposure:['personal'] reached the personal plane" }
    # And the probe COULD have found something: the control comes back on the same query.
    if ($probeIds -contains $PID_OPS) {
        Pass "the ops control DID come back - so 'stopped' means filtered, not 'nothing matched'"
    } else { Fail "the control was not returned either - this recall proves nothing" }

    Section "ATTACK 1, the other half - is the attempt VISIBLE?"
    $flagged = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='$WS' AND event_type='recall_requested' AND payload->>'exposure_override_denied'='true'"
    if ($flagged -eq "1") { Pass "a durable audit row records the attempt (recall_requested, exposure_override_denied=true)" }
    else { Fail "expected exactly 1 flagged audit row in $WS, got '$flagged' - the attempt is invisible" }
    $asked = Db "SELECT payload->>'requested_exposure' FROM agent_memory_audit_events WHERE workspace_id='$WS' AND payload->>'exposure_override_denied'='true' LIMIT 1"
    if ($asked -match "personal") { Pass "the audit row says WHAT was asked for ($asked), not merely that something was refused" }
    else { Fail "the flagged audit row does not record the requested plane (got '$asked')" }

    # A benign recall must NOT be flagged. Without this, the assertion above passes just as
    # well against an audit writer that hardcodes 'true' - which would make the signal noise.
    $benign = Invoke-Rest -Port $ServerPort -Path "/agent-memory/recall" -Body @{
        workspace_id = $WS; project_id = $PROJ
        query = $MARKER; limit = 25; include_unconfirmed = $true
    }
    $unflagged = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='$WS' AND event_type='recall_requested' AND payload->>'exposure_override_denied'='false'"
    if ($benign.Status -eq 200 -and [int]$unflagged -ge 1) {
        Pass "an ordinary recall is recorded UNFLAGGED - the flag discriminates, it is not a constant"
    } else { Fail "an ordinary recall did not produce an unflagged audit row (got '$unflagged')" }

    $traced = Db "SELECT count(*) FROM agent_memory_recall_traces WHERE workspace_id='$WS' AND request_payload->>'exposure_override_denied'='true'"
    if ($traced -eq "1") { Pass "the trace carries requested vs enforced exposure too, so the audit row can be corroborated" }
    else { Fail "expected the recall trace to record the refused exposure, got '$traced'" }

    # --- 5. ATTACK 2: the ops door, agent_memory_recall ----------------------------------
    Section "ATTACK 2 - a code agent at the OPS door names the personal plane (agent_memory_recall)"
    Add-AttackedTool "agent_memory_recall" "ATTACK 2"
    $gwProbe = Invoke-Tool -Port $OpsPort -Name "agent_memory_recall" -Arguments @{
        workspace_id = $WS; project_id = $PROJ
        query = $MARKER; limit = 25; include_unconfirmed = $true
        exposure = @("personal")
    }
    $gwBlob = ($gwProbe | ConvertTo-Json -Depth 12 -Compress)
    # THE CONTROL IS CHECKED FIRST, DELIBERATELY. An absent fixture is only evidence if the
    # call succeeded; a 401 also contains no fixture. The first run of this drill "passed"
    # this attack while every request was rejected at the door for a bad key.
    if ($gwBlob -match "SYNTHETIC ops-plane CONTROL") {
        Pass "the ops control came back through the door - so the call actually ran"
        if ($gwBlob -notmatch "SYNTHETIC personal-plane FIXTURE") {
            Pass "STOPPED - the personal fixture is not in the ops door's response"
        } else { Fail "EXPOSURE LEAK through the ops door: the personal fixture came back" }
    } else {
        Fail "the ops door did not return the CONTROL - the call failed, so this attack proves nothing"
        Note $gwBlob
    }

    $gwLog = (docker logs $OPS 2>&1 | Out-String)
    if ($gwLog -match "exposure_override_attempt") {
        Pass "the DOOR recorded the attempt (gateway audit line: exposure_override_attempt)"
    } else { Fail "the ops door logged nothing about a caller naming another plane" }

    # WHICH RECORD COVERS WHICH LANE - asserted, because otherwise it is only implied.
    # `exposure` is NOT a field of RECALL_SCHEMA (agent-memory-tools.ts), so on the MCP lane
    # the tool's own zod validation strips it before performRecall is reached: the durable
    # row for THIS call records requested_exposure null, and the gateway line above is what
    # makes the attempt visible. The REST twin takes the raw body, which is why ATTACK 1
    # produced the flagged durable row. If that ever stops being true - if a schema gains
    # the field, or the SDK stops stripping - this count moves and says so.
    $flaggedAfter = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='$WS' AND payload->>'exposure_override_denied'='true'"
    if ($flaggedAfter -eq "1") {
        Pass "still exactly 1 flagged durable row in $WS - the MCP lane is stopped at the tool schema and recorded at the door, not in the database"
    } else { Fail "expected the flagged-row count to still be 1 after the MCP probe, got '$flaggedAfter'" }

    # --- 6. ATTACK 3: the ops door, agent_memory_inspect ---------------------------------
    Section "ATTACK 3 - the agent stops searching and asks for the personal memory BY ID (agent_memory_inspect)"
    # THE ATTACK A VERIFIER FOUND AND THIS DRILL DID NOT MAKE. Recall is the tool a drill
    # thinks of; inspect is the tool an attacker thinks of, because by then it has an id -
    # from a trace, from a queue listing, from a log. Against the merged-and-reviewed code
    # this returned the fixture's full `content` with `"exposure": "personal"` in the same
    # payload, and wrote no audit row.
    Add-AttackedTool "agent_memory_inspect" "ATTACK 3"
    $insPers = Invoke-Tool -Port $OpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_PERS }
    $insBlob = ($insPers | ConvertTo-Json -Depth 12 -Compress)
    # Control FIRST, again: prove inspect works at all through this door before reading
    # anything into a refusal.
    $insCtrl = Invoke-Tool -Port $OpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_OPS }
    $insCtrlBlob = ($insCtrl | ConvertTo-Json -Depth 12 -Compress)
    if ($insCtrlBlob -match "SYNTHETIC ops-plane CONTROL") {
        Pass "inspect on the ops control returns its content - the tool is reachable and working at this door"
        if ($insBlob -notmatch "SYNTHETIC personal-plane FIXTURE") {
            Pass "STOPPED - inspect on the personal fixture returns no content"
        } else { Fail "EXPOSURE LEAK: agent_memory_inspect returned the personal fixture's content by id" }
        if ($insBlob -match "not_found") {
            Pass "the refusal is not_found, not 'forbidden' - it does not confirm the id exists"
        } else { Fail "the refusal does not read as not_found (got: $insBlob)" }
    } else {
        Fail "inspect did not return the CONTROL - the call failed, so this attack proves nothing"
        Note $insCtrlBlob
    }
    $refused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$PID_PERS' AND payload->>'tool'='agent_memory_inspect'"
    if ($refused -eq "1") { Pass "the refusal left a durable audit row (access_refused, tool=agent_memory_inspect)" }
    else { Fail "expected exactly 1 access_refused row for this memory+tool, got '$refused' - stopped, but invisible" }
    $refusedCtrl = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$PID_OPS'"
    if ($refusedCtrl -eq "0") { Pass "the ALLOWED inspect wrote no refusal row - access_refused means refused, it is not a per-call constant" }
    else { Fail "the allowed inspect also wrote $refusedCtrl refusal row(s) - the signal is noise" }

    # A memory that genuinely does not exist must NOT produce a refusal row. Without this,
    # every typo becomes a refusal record and the rows that matter are buried in them.
    $ghost = [guid]::NewGuid().ToString()
    $insGhost = Invoke-Tool -Port $OpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $ghost }
    $ghostRows = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$ghost'"
    if (($insGhost | ConvertTo-Json -Depth 8 -Compress) -match "not_found" -and $ghostRows -eq "0") {
        Pass "an id that does not exist is also not_found and writes NO refusal row - a probe is distinguishable from a typo"
    } else { Fail "the absent-id case did not behave as documented (rows=$ghostRows)" }

    # --- 7. ATTACK 4: the ops door, agent_memory_list_review_queue -----------------------
    Section "ATTACK 4 - the agent ENUMERATES instead of searching (agent_memory_list_review_queue)"
    # The other tool the verifier used. It needs no id at all: both fixtures are
    # review_status 'pending', which is this tool's own default, so a bare call listed the
    # personal plane. Attacked twice: scoped to the drill workspace, and UNSCOPED - the
    # bare call an agent would actually make.
    Add-AttackedTool "agent_memory_list_review_queue" "ATTACK 4"
    $qScoped = Invoke-Tool -Port $OpsPort -Name "agent_memory_list_review_queue" -Arguments @{ workspace_id = $WS; limit = 200 }
    $qBlob = ($qScoped | ConvertTo-Json -Depth 12 -Compress)
    if ($qBlob -match [regex]::Escape($SUMOPS)) {
        Pass "the queue lists the ops control - the call ran and this workspace is in scope"
        if ($qBlob -notmatch [regex]::Escape($SUMPERS) -and $qBlob -notmatch [regex]::Escape($PID_PERS)) {
            Pass "STOPPED - the personal fixture is not enumerable in the review queue (no summary, no id)"
        } else { Fail "EXPOSURE LEAK: the review queue enumerated the personal plane" }
    } else { Fail "the queue did not list the CONTROL - the call failed, so this attack proves nothing"; Note $qBlob }

    $qBare = Invoke-Tool -Port $OpsPort -Name "agent_memory_list_review_queue" -Arguments @{ limit = 200 }
    $qBareBlob = ($qBare | ConvertTo-Json -Depth 12 -Compress)
    if ($qBareBlob -match [regex]::Escape($SUMOPS)) {
        if ($qBareBlob -notmatch [regex]::Escape($PID_PERS)) {
            Pass "STOPPED - the UNSCOPED queue (no workspace_id at all) still excludes the personal plane"
        } else { Fail "EXPOSURE LEAK: dropping workspace_id enumerated the personal plane" }
    } else { Fail "the unscoped queue did not list the control either - this sub-check proves nothing" }
    # NO AUDIT ROW IS ASSERTED HERE, ON PURPOSE. This tool FILTERS, it does not REFUSE: the
    # caller asked for "the queue" and got the queue for its own plane. There is no denied
    # request to record, and writing a row per listing would file ordinary use as a probe.
    # U5's "the attempt is visible in an audit record" attaches to a TARGETED access that
    # was denied - which is ATTACK 3's shape, and is asserted there.
    Note "by design: an enumeration that is filtered writes no audit row - nothing was asked for and denied"

    # --- 8. ATTACK 5: the ops door, agent_memory_recall_trace ----------------------------
    Section "ATTACK 5 - the agent reads back a TRACE that named the personal memory (agent_memory_recall_trace)"
    # The third unattacked read tool. A trace is the natural place to harvest an id from,
    # and its join reaches memory summaries - so it is a read path onto the plane that does
    # not go through recall at all.
    Add-AttackedTool "agent_memory_recall_trace" "ATTACK 5"
    $trc = Invoke-Tool -Port $OpsPort -Name "agent_memory_recall_trace" -Arguments @{ trace_id = $TRACE }
    $trcBlob = ($trc | ConvertTo-Json -Depth 12 -Compress)
    if ($trcBlob -match [regex]::Escape($SUMOPS)) {
        Pass "the trace read back and carries the ops control's summary - the call ran"
        if ($trcBlob -notmatch [regex]::Escape($SUMPERS)) {
            Pass "STOPPED - the personal memory's summary is not in the trace response"
        } else { Fail "EXPOSURE LEAK: the recall trace returned the personal memory's summary" }
        if ($trcBlob -notmatch [regex]::Escape($PID_PERS)) {
            Pass "STOPPED - the personal memory's ID is not in the trace response either"
        } else { Fail "EXPOSURE LEAK: the recall trace discloses the off-plane memory's id (an id is what ATTACK 3 needs)" }
    } else { Fail "the trace did not return the CONTROL - the call failed, so this attack proves nothing"; Note $trcBlob }
    $trcRefused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$PID_PERS' AND payload->>'tool'='agent_memory_recall_trace'"
    if ($trcRefused -eq "1") { Pass "withholding the off-plane item left a durable audit row (access_refused, tool=agent_memory_recall_trace)" }
    else { Fail "expected exactly 1 access_refused row for recall_trace, got '$trcRefused' - stopped, but invisible" }

    # --- 9. ATTACK 6: go around agent-memory entirely, at the thoughts lane --------------
    Section "ATTACK 6 - the agent gives up on agent_memory_* and reaches for search_thoughts (OPS door)"
    # The smarter attack, and the one the allow-list exists for. Every agent memory also
    # writes a THOUGHT carrying the same content, and search_thoughts reads thoughts.
    $st = Invoke-Tool -Port $OpsPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 10 }
    if ($st -and $st.error -and $st.error.code -eq -32601) {
        Pass "STOPPED - search_thoughts is not on the ops door's allow-list (-32601)"
    } else { Fail "search_thoughts was NOT denied at the ops door"; Note ($st | ConvertTo-Json -Depth 8 -Compress) }
    $gwLog = (docker logs $OPS 2>&1 | Out-String)
    if ($gwLog -match "tool_denied" -and $gwLog -match "search_thoughts") {
        Pass "the denial left an audit line naming the tool (tool_denied)"
    } else { Fail "the denied tool call left NO record - stopped, but invisible" }

    # --- 10. ATTACK 7: THE CLOUD DOOR - the only lane with configured consumers ----------
    Section "ATTACK 7 - the CLOUD door (.mcp.json points every agent here)"
    # WHY THIS SECTION EXISTS. Every attack above is on a door with no configured client.
    # A repo-wide grep for 8062 finds one hit and it is a documentation table; .mcp.json
    # points at 8061. So the lane an agent demonstrably occupies had ZERO coverage, and its
    # exclusion of agent-memory content rested on one sentence in a code comment. Two
    # separate boundaries hold here and both are asserted.
    #
    # (a) the ALLOW-LIST: the agent-memory tools are simply not on the cloud door.
    $clIns = Invoke-Tool -Port $CloudPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_PERS }
    if ($clIns -and $clIns.error -and $clIns.error.code -eq -32601) {
        Pass "STOPPED - agent_memory_inspect is not on the cloud door's allow-list (-32601)"
    } else { Fail "the cloud door did not deny agent_memory_inspect"; Note ($clIns | ConvertTo-Json -Depth 8 -Compress) }
    $clRec = Invoke-Tool -Port $CloudPort -Name "agent_memory_recall" -Arguments @{ workspace_id = $WS; query = $MARKER }
    if ($clRec -and $clRec.error -and $clRec.error.code -eq -32601) {
        Pass "STOPPED - agent_memory_recall is not on the cloud door's allow-list either"
    } else { Fail "the cloud door did not deny agent_memory_recall"; Note ($clRec | ConvertTo-Json -Depth 8 -Compress) }
    $clQ = Invoke-Tool -Port $CloudPort -Name "agent_memory_list_review_queue" -Arguments @{ limit = 200 }
    if ($clQ -and $clQ.error -and $clQ.error.code -eq -32601) {
        Pass "STOPPED - agent_memory_list_review_queue is not on the cloud door's allow-list either"
    } else { Fail "the cloud door did not deny agent_memory_list_review_queue"; Note ($clQ | ConvertTo-Json -Depth 8 -Compress) }
    $clLog = (docker logs $CLOUD 2>&1 | Out-String)
    if ($clLog -match "tool_denied" -and $clLog -match "agent_memory_inspect") {
        Pass "the cloud door's denials left audit lines naming the tools (tool_denied)"
    } else { Fail "the cloud door denied silently - stopped, but invisible" }

    # (b) the FORCED SHARE FILTER on the tool the cloud door DOES allow. This is the claim
    # that lived only in a comment: the agent-memory mirror writes no share:'cloud' label,
    # so the cloud door's forced metadata_filter excludes it. Now executable.
    $clSt = Invoke-Tool -Port $CloudPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25 }
    $clStBlob = ($clSt | ConvertTo-Json -Depth 12 -Compress)
    if ($clStBlob -match "SYNTHETIC cloud-plane CONTROL") {
        Pass "search_thoughts at the cloud door returns the cloud-labelled control - the lane works"
        if ($clStBlob -notmatch "SYNTHETIC personal-plane FIXTURE") {
            Pass "STOPPED - the personal fixture's mirrored thought is not in the cloud door's results"
        } else { Fail "EXPOSURE LEAK at the CLOUD door: the personal fixture came back through search_thoughts" }
        if ($clStBlob -notmatch "SYNTHETIC ops-plane CONTROL") {
            Pass "STOPPED - the OPS-plane memory is excluded from the cloud door too (agent-memory content is not cloud content)"
        } else { Fail "the cloud door returned an ops-plane agent memory - the planes are not separated on this lane" }
    } else {
        Fail "the cloud door returned no cloud control - the call failed, so this attack proves nothing"
        Note $clStBlob
    }

    # --- 10b. ATTACK 8: STOP READING, MOVE THE MEMORY INSTEAD ----------------------------
    Section "ATTACK 8 - the agent WIDENS the plane instead of reading it (agent_memory_review / promote_exposure)"
    # THE NEIGHBOURING DOOR, AND THE REASON THIS DRILL NOW ITERATES THE WRITE LIST TOO.
    # Every attack above is a READ and every one of them is stopped. This one does not
    # defeat that boundary at all - it MOVES THE MEMORY TO THE OTHER SIDE OF IT.
    # promote_exposure is the only action in the system that widens exposure
    # (agent-memory-review.ts sets exposure: "ops"), agent_memory_review is on the ops
    # door's GATEWAY_WRITE_TOOLS, and performReview used to resolve the row by id with no
    # plane predicate whatsoever - it SELECTed exposure so it could report it and never
    # filtered on it. So an ops-door caller could promote a PERSONAL memory onto its own
    # plane, after which ATTACK 3's inspect returns it entirely legitimately.
    Add-AttackedWriteTool "agent_memory_review" "ATTACK 8"

    # CONTROL FIRST: the tool has to work at this door, or a refusal proves nothing. A
    # state-neutral action on the ops control - not promote_exposure, which would change
    # the control the later phases depend on.
    $revCtrl = Invoke-Tool -Port $OpsPort -Name "agent_memory_review" -Arguments @{
        memory_id = $PID_OPS; action = "evidence_only"
        actor = @{ label = "drill-control" }; note = "reachability control" }
    $revCtrlBlob = ($revCtrl | ConvertTo-Json -Depth 12 -Compress)
    if ($revCtrlBlob -match "evidence_only" -and $revCtrlBlob -notmatch "not_found") {
        Pass "agent_memory_review works at the ops door on the ops control - the tool is reachable"
    } else {
        Fail "agent_memory_review did not act on the CONTROL - the attack below proves nothing"
        Note $revCtrlBlob
    }

    $revAtk = Invoke-Tool -Port $OpsPort -Name "agent_memory_review" -Arguments @{
        memory_id = $PID_PERS; action = "promote_exposure"
        actor = @{ label = "drill-attacker" }; note = "widen the personal fixture onto the ops plane" }
    $revBlob = ($revAtk | ConvertTo-Json -Depth 12 -Compress)
    # THE DATABASE IS THE VERDICT, not the response. A tool that answers "ok" while changing
    # nothing and a tool that answers "refused" while changing the row are both possible,
    # and only one of the two is visible in the reply.
    $expAfter = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_PERS'"
    if ($expAfter -eq "personal") {
        Pass "STOPPED - the personal fixture is STILL exposure=personal after promote_exposure"
    } else {
        Fail "PLANE ESCALATION: promote_exposure moved the personal fixture to exposure='$expAfter' - every read tool now returns it legitimately"
    }
    if ($revBlob -match "not_found") {
        Pass "the refusal is not_found, not 'forbidden' - it does not confirm the id exists"
    } else { Fail "the review refusal does not read as not_found (got: $revBlob)" }
    $revRefused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$PID_PERS' AND payload->>'tool'='agent_memory_review'"
    if ($revRefused -eq "1") { Pass "the refused review left a durable audit row (access_refused, tool=agent_memory_review)" }
    else { Fail "expected exactly 1 access_refused row for agent_memory_review, got '$revRefused' - stopped, but invisible" }
    # No review-action row either: a refused decision that files paperwork is a decision.
    $revActions = Db "SELECT count(*) FROM agent_memory_review_actions WHERE memory_id='$PID_PERS'"
    if ($revActions -eq "0") { Pass "no review-action row was written for the refused promotion" }
    else { Fail "$revActions review-action row(s) exist for a memory this door may not see" }

    # THE FOLLOW-THROUGH. The escalation's whole value is what it unlocks, so assert that
    # the door it was aimed at is still shut afterwards.
    $insAfter = (Invoke-Tool -Port $OpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_PERS } | ConvertTo-Json -Depth 12 -Compress)
    if ($insAfter -notmatch "SYNTHETIC personal-plane FIXTURE") {
        Pass "and inspect STILL refuses the fixture afterwards - the escalation unlocked nothing"
    } else { Fail "EXPOSURE LEAK: after the promotion attempt, inspect returns the personal fixture" }

    # Restore unconditionally: if the attack DID succeed, the phases below (and the red
    # phase in particular) need the fixture back on the personal plane or they test nothing.
    $null = Db "UPDATE agent_memories SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('exposure','personal'), provenance_status = 'generated', last_confirmed_at = NULL WHERE id = '$PID_PERS'"
    $expRestored = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_PERS'"
    if ($expRestored -eq "personal") { Pass "fixture restored to the personal plane for the phases below" }
    else { Fail "could not restore the fixture (exposure='$expRestored') - later phases are unreliable"; throw "fixture not restored" }

    # --- 10c. ATTACK 9: the WRITE path as an id oracle -----------------------------------
    Section "ATTACK 9 - the agent asks the WRITE path who owns a key (agent_memory_writeback idempotency)"
    # NOT FOUND BY A VERIFIER - found by the completeness test that enumerates every
    # agent_memories statement in the subsystem. The writeback's idempotency lookup matched
    # on (workspace_id, idempotency_key) with no plane predicate and returned the hit's id
    # and thought_id as duplicate:true. An id is exactly what agent_memory_inspect consumes,
    # so the WRITE tool was an id oracle for the personal plane, reachable from a door with
    # no read access to it at all.
    Add-AttackedWriteTool "agent_memory_writeback" "ATTACK 9"
    $wbCtrl = Invoke-Tool -Port $OpsPort -Name "agent_memory_writeback" -Arguments @{
        workspace_id = $WS; project_id = $PROJ
        summary = "$SUMOPS retry"; content = $OPSCTRL
        memory_type = "lesson"; idempotency_key = "$MARKER-ops" }
    $wbCtrlBlob = ($wbCtrl | ConvertTo-Json -Depth 12 -Compress)
    if ($wbCtrlBlob -match [regex]::Escape($PID_OPS)) {
        Pass "an ON-plane retry still returns its own memory id - idempotency is not broken by the fix"
    } else {
        Fail "the on-plane retry did not return the control's id - the attack below proves nothing"
        Note $wbCtrlBlob
    }
    $wbAtk = Invoke-Tool -Port $OpsPort -Name "agent_memory_writeback" -Arguments @{
        workspace_id = $WS; project_id = $PROJ
        summary = "probe"; content = "SYNTHETIC probe $MARKER - guessing another plane's retry key"
        memory_type = "lesson"; idempotency_key = "$MARKER-personal" }
    $wbBlob = ($wbAtk | ConvertTo-Json -Depth 12 -Compress)
    if ($wbBlob -notmatch [regex]::Escape($PID_PERS)) {
        Pass "STOPPED - guessing the personal fixture's idempotency_key does not disclose its id"
    } else { Fail "ID DISCLOSURE: the writeback handed back the personal fixture's memory id" }
    $wbRefused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND payload->>'tool'='agent_memory_writeback' AND payload->>'reason'='off-plane-idempotency-key'"
    if ($wbRefused -eq "1") { Pass "the refused key lookup left a durable audit row" }
    else { Fail "expected exactly 1 off-plane-idempotency-key audit row, got '$wbRefused'" }
    $wbNoId = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND payload->>'tool'='agent_memory_writeback' AND memory_id IS NOT NULL"
    if ($wbNoId -eq "0") { Pass "and the audit row itself names no memory - the record does not become the leak" }
    else { Fail "$wbNoId writeback refusal row(s) carry a memory_id" }

    # --- 10d. ATTACK 10: report_usage as an existence oracle -----------------------------
    Section "ATTACK 10 - the agent probes with report_usage (agent_memory_report_usage)"
    # The third write tool. It already filtered on the plane, and it wrote NO audit row when
    # it refused - so a probing agent and a stale trace_id looked identical. U5's contract is
    # "mechanically stopped AND visible in an audit record"; this was the half that was
    # missing, and it was missing because the audit was the CALLER's job. It is the
    # chokepoint's job now.
    Add-AttackedWriteTool "agent_memory_report_usage" "ATTACK 10"
    $ruCtrl = Invoke-Tool -Port $OpsPort -Name "agent_memory_report_usage" -Arguments @{
        memory_id = $PID_OPS; used = $true; workspace_id = $WS; note = "control" }
    $ruCtrlBlob = ($ruCtrl | ConvertTo-Json -Depth 12 -Compress)
    if ($ruCtrlBlob -notmatch "not_found") {
        Pass "report_usage works at the ops door on the ops control - the tool is reachable"
    } else { Fail "report_usage refused the CONTROL - the attack below proves nothing"; Note $ruCtrlBlob }
    $ruAtk = Invoke-Tool -Port $OpsPort -Name "agent_memory_report_usage" -Arguments @{
        memory_id = $PID_PERS; used = $true; workspace_id = $WS; note = "probe" }
    $ruBlob = ($ruAtk | ConvertTo-Json -Depth 12 -Compress)
    if ($ruBlob -match "not_found") { Pass "STOPPED - report_usage on the personal fixture is not_found" }
    else { Fail "report_usage did not refuse the off-plane memory (got: $ruBlob)" }
    $ruUsed = Db "SELECT count(*) FROM agent_memory_audit_events WHERE memory_id='$PID_PERS' AND event_type IN ('memory_used','memory_ignored')"
    if ($ruUsed -eq "0") { Pass "and no memory_used row was written for a memory this door cannot see" }
    else { Fail "$ruUsed usage row(s) exist for the off-plane fixture" }
    $ruRefused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND memory_id='$PID_PERS' AND payload->>'tool'='agent_memory_report_usage'"
    if ($ruRefused -eq "1") { Pass "the refusal left a durable audit row (access_refused, tool=agent_memory_report_usage)" }
    else { Fail "expected exactly 1 access_refused row for report_usage, got '$ruRefused' - stopped, but invisible" }

    # --- 11. THE COVERAGE GATE -----------------------------------------------------------
    Section "COVERAGE - every read tool compose puts on the ops door must have been attacked"
    # The safeguard the derived allow-list was supposed to be, actually closed. Deriving the
    # list and attacking one of it is worth less than hardcoding it, because it reads as
    # coverage in the output while providing none.
    $missed = @($opsReadTools | Where-Object { -not $script:Attacked.ContainsKey($_) })
    if ($missed.Count -eq 0) {
        Pass "all $($opsReadTools.Count) derived read tool(s) were attacked: $(($opsReadTools | ForEach-Object { $_ + ' (' + $script:Attacked[$_] + ')' }) -join ', ')"
    } else {
        Fail "compose allows read tool(s) this drill never attacks: $($missed -join ', ') - the allow-list is derived but not exercised"
        Note "add an ATTACK section for each, or the next tool added to the door rides in unexamined"
    }

    Section "COVERAGE - every WRITE tool compose puts on the ops door must have been attacked too"
    # THE HALF THAT DID NOT EXIST, and its absence is what let the escalation through. The
    # read ledger above was complete and every read attack passed; the plane was still
    # reachable, because agent_memory_review could MOVE a memory onto the caller's plane and
    # nothing here iterated the write list. Read containment is not plane containment.
    $missedW = @($opsWriteTools | Where-Object { -not $script:AttackedWrites.ContainsKey($_) })
    if ($missedW.Count -eq 0) {
        Pass "all $($opsWriteTools.Count) derived write tool(s) were attacked: $(($opsWriteTools | ForEach-Object { $_ + ' (' + $script:AttackedWrites[$_] + ')' }) -join ', ')"
    } else {
        Fail "compose allows write tool(s) this drill never attacks: $($missedW -join ', ') - a write can relocate a memory across the plane, so an unattacked one is an unexamined door"
        Note "add an ATTACK section for each; ATTACK 8 is the shape (act on the personal fixture, then read the DATABASE, not the response)"
    }

    # --- 12. RED: prove every green above could have failed -------------------------------
    if ($SkipRed) {
        Section "RED phase SKIPPED (-SkipRed) - the green results above are unproven"
        Note "A guard nobody has watched fail is not known to guard anything."
    } else {
        Section "RED - remove the exposure guards in a SCRATCH copy, and require the fixture to leak"
        Remove-Item $REDSRCDIR -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $SRC $REDSRCDIR -Recurse -Force

        # Each anchor is asserted to match EXACTLY ONCE before it is replaced. A
        # search-and-replace that silently matched nothing is exactly how a red phase turns
        # into a second green phase without anyone noticing.
        function Set-RedAnchor {
            param([string]$File, [string]$Anchor, [string]$Replacement, [string]$What)
            $p = Join-Path $REDSRCDIR $File
            $t = [IO.File]::ReadAllText($p)
            $n = ([regex]::Matches($t, [regex]::Escape($Anchor))).Count
            if ($n -ne 1) {
                Fail "red anchor for $What matched $n times in $File, expected 1 - refusing to build a 'red' image that is really green"
                throw "anchor drift"
            }
            [IO.File]::WriteAllText($p, $t.Replace($Anchor, $Replacement))
            Pass "scratch copy patched: $What"
        }

        # (i) the recall path's door override - what ATTACK 1 and ATTACK 2 rest on.
        Set-RedAnchor -File "agent-memory-policy.ts" `
            -Anchor "  const enforced: Exposure[] = doorExposure ? [doorExposure] : [...DEFAULT_RECALL_EXPOSURES];" `
            -Replacement "  const enforced: Exposure[] = (requested && requested.length ? [...requested] : (doorExposure ? [doorExposure] : [...DEFAULT_RECALL_EXPOSURES])) as Exposure[];" `
            -What "the recall door no longer overrides what the caller asked for"

        # (ii) THE CHOKEPOINT ITSELF - ONE LINE, and it is what ATTACKS 3, 4, 5, 8, 9 and 10
        # all rest on. This used to be two anchors, one per file, because the plane was
        # forced separately in agent-memory-tools.ts and agent-memory-ops.ts and not at all
        # in performReview or the writeback's idempotency lookup. That arrangement is the
        # defect: a guard repeated per call site is a guard that can be omitted at the next
        # one, and it was, three rounds running. Every lookup now goes through
        # agent-memory-plane.ts, so there is exactly one line to take away - and the number
        # of attacks that light up when it goes is the measure of how much this file was
        # carrying.
        Set-RedAnchor -File "agent-memory-plane.ts" `
            -Anchor "  return { exposures: door ? [door] : [...DEFAULT_DOOR_PLANE], door } as unknown as DoorPlane;" `
            -Replacement "  return { exposures: [`"ops`", `"personal`"], door } as unknown as DoorPlane;" `
            -What "the chokepoint no longer bounds any lookup to the door's plane"
        Note "the repo tree is untouched - this lives in $REDSRCDIR"

        docker build -t $REDIMAGE $REDSRCDIR 2>&1 | Select-Object -Last 1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $REDIMAGE"; throw "red build failed" }
        Start-McpServer -Name $REDSRV -Port $RedSrvPort -Img $REDIMAGE
        if (Wait-Http -Port $RedSrvPort -Path "/health") { Pass "the unguarded server is up on :$RedSrvPort (same database, same fixtures)" }
        else { docker logs $REDSRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "red server never answered"; throw "no red server" }

        # RED for ATTACK 1 - the internal REST lane.
        $red = Invoke-Rest -Port $RedSrvPort -Path "/agent-memory/recall" -Body @{
            workspace_id = $WS; project_id = $PROJ
            query = $MARKER; limit = 25; include_unconfirmed = $true
            exposure = @("personal")
        }
        $redIds = @()
        if ($red.Body -and $red.Body.items) { $redIds = @($red.Body.items | ForEach-Object { $_.memory_id }) }
        if ($redIds -contains $PID_PERS) {
            Pass "RED CONFIRMED (ATTACK 1) - without the door override, the SAME request DOES return the personal fixture"
        } else {
            Fail "the unguarded server did not leak either - ATTACK 1's pass proves nothing"
            Note ($red | ConvertTo-Json -Depth 8 -Compress)
        }

        # RED for ATTACKS 3, 4 and 5 - the same ops-door env, pointed at the unguarded server.
        Start-Gateway -Name $REDOPSMEM -Port $RedMemPort -GwEnv $opsEnv -Upstream "http://${REDSRV}:8000"
        if (Wait-Http -Port $RedMemPort -Path "/health") {
            $rIns = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_PERS } | ConvertTo-Json -Depth 12 -Compress)
            if ($rIns -match "SYNTHETIC personal-plane FIXTURE") {
                Pass "RED CONFIRMED (ATTACK 3) - unguarded, agent_memory_inspect returns the personal fixture's content by id"
            } else { Fail "the unguarded inspect did not leak - ATTACK 3's pass proves nothing"; Note $rIns }

            $rQ = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_list_review_queue" -Arguments @{ limit = 200 } | ConvertTo-Json -Depth 12 -Compress)
            if ($rQ -match [regex]::Escape($PID_PERS)) {
                Pass "RED CONFIRMED (ATTACK 4) - unguarded, the review queue enumerates the personal plane"
            } else { Fail "the unguarded queue did not enumerate - ATTACK 4's pass proves nothing"; Note $rQ }

            $rT = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_recall_trace" -Arguments @{ trace_id = $TRACE } | ConvertTo-Json -Depth 12 -Compress)
            if ($rT -match [regex]::Escape($SUMPERS)) {
                Pass "RED CONFIRMED (ATTACK 5) - unguarded, the trace returns the personal memory's summary"
            } else { Fail "the unguarded trace did not leak - ATTACK 5's pass proves nothing"; Note $rT }

            # RED for ATTACK 9 - the WRITE path's id oracle. Run BEFORE ATTACK 8's red, which
            # moves the fixture onto the ops plane and would make this succeed for the wrong
            # reason.
            $rWb = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_writeback" -Arguments @{
                workspace_id = $WS; project_id = $PROJ
                summary = "red probe"; content = "SYNTHETIC red probe $MARKER"
                memory_type = "lesson"; idempotency_key = "$MARKER-personal" } | ConvertTo-Json -Depth 12 -Compress)
            if ($rWb -match [regex]::Escape($PID_PERS)) {
                Pass "RED CONFIRMED (ATTACK 9) - unguarded, guessing the retry key hands back the personal fixture's id"
            } else { Fail "the unguarded writeback disclosed no id - ATTACK 9's pass proves nothing"; Note $rWb }

            # RED for ATTACK 10 - report_usage as an existence oracle.
            $rRu = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_report_usage" -Arguments @{
                memory_id = $PID_PERS; used = $true; workspace_id = $WS; note = "red probe" } | ConvertTo-Json -Depth 12 -Compress)
            $rRuRows = Db "SELECT count(*) FROM agent_memory_audit_events WHERE memory_id='$PID_PERS' AND event_type IN ('memory_used','memory_ignored')"
            if ($rRu -notmatch "not_found" -and $rRuRows -ne "0") {
                Pass "RED CONFIRMED (ATTACK 10) - unguarded, report_usage confirms the personal fixture exists and files a usage row for it"
            } else { Fail "the unguarded report_usage refused anyway - ATTACK 10's pass proves nothing"; Note "$rRu rows=$rRuRows" }

            # RED for ATTACK 8 - THE ESCALATION. Last, because it is the one that changes the
            # fixture's plane; everything above needs it still personal.
            $rRev = (Invoke-Tool -Port $RedMemPort -Name "agent_memory_review" -Arguments @{
                memory_id = $PID_PERS; action = "promote_exposure"
                actor = @{ label = "drill-red" }; note = "red: widen the personal fixture" } | ConvertTo-Json -Depth 12 -Compress)
            $rExp = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_PERS'"
            if ($rExp -eq "ops") {
                Pass "RED CONFIRMED (ATTACK 8) - unguarded, promote_exposure MOVES the personal fixture onto the ops plane"
                # And the payoff, which is the whole point of the escalation: once moved, the
                # tool that refuses it in the green phase hands it over on the GUARDED door.
                $rIns2 = (Invoke-Tool -Port $OpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = $PID_PERS } | ConvertTo-Json -Depth 12 -Compress)
                if ($rIns2 -match "SYNTHETIC personal-plane FIXTURE") {
                    Pass "RED CONFIRMED (ATTACK 8, payoff) - after the promotion the GUARDED door's inspect returns the fixture, containment intact and bypassed"
                } else { Note "the promoted fixture did not come back through the guarded door: $rIns2" }
            } else { Fail "the unguarded promote_exposure did not move the fixture (exposure='$rExp') - ATTACK 8's pass proves nothing"; Note $rRev }
            # Put it back. Everything after this point assumes the fixture is personal.
            $null = Db "UPDATE agent_memories SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('exposure','personal'), provenance_status = 'generated', last_confirmed_at = NULL WHERE id = '$PID_PERS'"
            $rBack = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$PID_PERS'"
            if ($rBack -eq "personal") { Pass "fixture restored to the personal plane after the red escalation" }
            else { Fail "could not restore the fixture after the red phase (exposure='$rBack')" }
        } else { Fail "red ops gateway (agent-memory variant) never answered" }

        Section "RED - dismantle the search_thoughts lane's guards, one at a time (OPS door)"
        # No code patch needed here: BOTH guards on this lane are configuration, so removing
        # one means changing an env value.
        #
        # A CORRECTION THIS DRILL PAID FOR. The first version of this section assumed one
        # guard - the allow-list - on the reasoning that search_thoughts applies no exposure
        # filter of its own (index.ts, and it does not). Allowing the tool therefore had to
        # leak. It did not: only the ops control came back. The reason is the door's SECOND
        # guard, which the compose comment calls "belt-and-braces" and undersells -
        # _force_read_filter injects metadata_filter={exposure:'ops'}, search_thoughts DOES
        # honour metadata_filter (metadata @> $4::jsonb), and the exposure label mirrored
        # onto the thought is what that clause matches. It is belt-and-braces for
        # agent_memory_recall, whose zod schema has no metadata_filter field and strips it;
        # for search_thoughts it is the whole boundary. So the two are asserted separately.
        $redEnv = @{}
        foreach ($k in $opsEnv.Keys) { $redEnv[$k] = $opsEnv[$k] }
        $redEnv["GATEWAY_READ_TOOLS"] = $opsEnv["GATEWAY_READ_TOOLS"] + ",search_thoughts"
        Start-Gateway -Name $REDOPS -Port $RedOpsPort -GwEnv $redEnv -Upstream "http://${SRV}:8000"
        if (Wait-Http -Port $RedOpsPort -Path "/health") {
            $redBlob = (Invoke-Tool -Port $RedOpsPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 10 } | ConvertTo-Json -Depth 12 -Compress)
            if ($redBlob -match "SYNTHETIC ops-plane CONTROL") {
                Pass "with search_thoughts allowed the call runs and returns the ops control"
                if ($redBlob -notmatch "SYNTHETIC personal-plane FIXTURE") {
                    Pass "DEFENCE IN DEPTH - the allow-list alone was not the boundary; the forced read filter still holds"
                } else { Fail "the allow-list was the only guard on this lane" }
            } else { Fail "the widened door returned nothing at all - this sub-check proves nothing"; Note $redBlob }
        } else { Fail "red ops gateway never answered" }

        # Now take the SECOND guard away too, and require the leak. If this does not leak,
        # ATTACK 6 is asserting a denial that was never protecting anything.
        docker rm -f $REDOPS 2>$null | Out-Null
        $redEnv["GATEWAY_READ_FILTER_VALUE"] = "personal"
        Start-Gateway -Name $REDOPS -Port $RedOpsPort -GwEnv $redEnv -Upstream "http://${SRV}:8000"
        if (Wait-Http -Port $RedOpsPort -Path "/health") {
            $redBlob2 = (Invoke-Tool -Port $RedOpsPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 10 } | ConvertTo-Json -Depth 12 -Compress)
            if ($redBlob2 -match "SYNTHETIC personal-plane FIXTURE") {
                Pass "RED CONFIRMED (ATTACK 6) - allow the tool AND point its forced filter at the personal plane, and the fixture is readable"
            } else {
                Fail "even with both guards off the fixture did not come back - ATTACK 6 proves nothing"
                Note $redBlob2
            }
        } else { Fail "red ops gateway (second variant) never answered" }

        Section "RED - the CLOUD door's exclusion is the LABEL, not luck"
        # ATTACK 7(b) passes if the mirrored thought is missing for ANY reason - including
        # 'search_thoughts is broken' or 'the marker did not match'. The claim under test is
        # specifically that the absent share:'cloud' label is what excludes it. So: put the
        # label on, change nothing else, and require it to come back.
        $null = Db "UPDATE thoughts SET metadata = metadata || jsonb_build_object('share','cloud') WHERE metadata->>'source'='agent-memory' AND metadata->>'exposure'='personal' AND content LIKE '%$MARKER%'"
        $labelled = Db "SELECT count(*) FROM thoughts WHERE metadata->>'share'='cloud' AND metadata->>'exposure'='personal' AND content LIKE '%$MARKER%'"
        if ($labelled -eq "1") {
            $clRed = (Invoke-Tool -Port $CloudPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25 } | ConvertTo-Json -Depth 12 -Compress)
            if ($clRed -match "SYNTHETIC personal-plane FIXTURE") {
                Pass "RED CONFIRMED (ATTACK 7b) - label the mirrored thought share=cloud and the CLOUD door hands over the personal fixture"
                Note "so the cloud door's exclusion is the missing label doing the work, exactly as agent-memory.ts claims - not an accident of the query"
            } else {
                Fail "even labelled share=cloud the fixture did not come back - ATTACK 7b proves nothing about the label"
                Note $clRed
            }
        } else { Fail "could not label the mirrored thought for the red phase (got '$labelled')" }
        # Put it back, so anything that reads this database afterwards sees the real state.
        $null = Db "UPDATE thoughts SET metadata = metadata - 'share' WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%'"
    }

} catch {
    Write-Host ("  aborted: " + $_.Exception.Message) -ForegroundColor Red
    $fails++
} finally {
    if ($KeepUp) {
        Write-Host "`n-KeepUp: leaving the drill stack on network $NET" -ForegroundColor Yellow
        Write-Host "  run id: $RunId   marker: $MARKER   workspace: $WS"
        Write-Host "  ports:  mcp=$ServerPort ops=$OpsPort cloud=$CloudPort redmcp=$RedSrvPort redops=$RedOpsPort redopsmem=$RedMemPort"
        Write-Host "  tear down with: docker rm -f $REDOPSMEM $REDOPS $REDSRV $CLOUD $OPS $SRV $STUB $DB; docker network rm $NET; docker rmi -f $IMAGE $REDIMAGE $GWIMAGE"
    } else {
        Remove-DrillStack
        Remove-DrillImages
        Remove-Item $INITDIR   -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $REDSRCDIR -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $STUBPATH  -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
if ($fails -eq 0) {
    Write-Host "PERSONAL-PLANE EXCLUSION DRILL PASSED - $passes checks, every attack stopped, every targeted refusal recorded" -ForegroundColor Green
    exit 0
}
Write-Host "$fails DRILL CHECK(S) FAILED ($passes passed)" -ForegroundColor Red
exit 1
