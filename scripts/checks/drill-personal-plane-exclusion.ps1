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
# THE GAP IT WAS BUILT TO CLOSE. The stopping was already real and already covered
# (smoke-agent-memory.ps1 section 6 proves the write-side stamping, and that a personal
# memory is absent from a default recall). The VISIBILITY was not there at all:
#   - a recall whose caller asked for exposure:['personal'] was silently forced back to
#     'ops', producing a record byte-identical to an agent that asked for nothing;
#   - a tool denied at the ops door produced a JSON-RPC -32601 and no record whatsoever.
# Both are recorded now - the first as a row in agent_memory_audit_events (event_type
# 'recall_requested', payload.exposure_override_denied), the second as an audit line on the
# gateway's stdout - and this drill asserts both.
#
# CLASS 4, ABSOLUTE: THE FIXTURE IS SYNTHETIC. Nothing here reads, writes or recalls a real
# personal memory. It builds a throwaway database on the real initdb chain, plants an
# obviously-fake record on the personal plane, and attacks that. It never touches
# openbrain-db, never joins an ai-stack_* network, and tags its images :drill - never
# :local, which is what production runs from.
#
# RED BEFORE GREEN. A guard nobody has watched fail is not known to guard anything, so the
# drill builds a SECOND image with the exposure guard removed (one asserted line, in a
# scratch copy - the repo tree is never weakened) and REQUIRES the synthetic record to come
# back through it. If the red phase does not leak, the green phase is proving nothing, and
# the drill says so and fails.
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
    [int]$ServerPort  = 18094,
    [int]$OpsPort     = 18095,
    [int]$RedSrvPort  = 18096,
    [int]$RedOpsPort  = 18097
)

# PS 5.1: native stderr (docker) must never be fatal, and capturing native output under
# 'Stop' turns a clean exit into a terminating error. Continue, and judge exit codes.
$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")

$fails = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t)    { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t)    { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }
function Note($t)    { Write-Host "        $t" -ForegroundColor DarkGray }

$NET      = "pp-drill-net"
$DB       = "pp-drill-db"
$STUB     = "pp-drill-embed"
$SRV      = "pp-drill-mcp"
$OPS      = "pp-drill-ops"
$REDSRV   = "pp-drill-mcp-red"
$REDOPS   = "pp-drill-ops-red"
$KEY      = "drill-brain-key-not-a-secret"
$OPSKEY   = "drill-ops-key-not-a-secret"
$IMAGE    = "openbrain-mcp-server:drill"
$REDIMAGE = "openbrain-mcp-server:drill-red"
$GWIMAGE  = "openbrain-gateway:drill"
$SRC      = Join-Path $root "OB1\integrations\kubernetes-deployment"

# The synthetic fixture. Unique per run, so a stale row can never be mistaken for this one,
# and worded so anyone who finds it in a log knows immediately that it is not real.
$MARKER   = "ppdrill" + ([guid]::NewGuid().ToString("N").Substring(0, 8))
$PERSONAL = "SYNTHETIC personal-plane FIXTURE $MARKER - not a real memory, planted by drill-personal-plane-exclusion.ps1"
$OPSCTRL  = "SYNTHETIC ops-plane CONTROL $MARKER - not a real memory, planted by drill-personal-plane-exclusion.ps1"

function Remove-DrillStack {
    docker rm -f $REDOPS $REDSRV $OPS $SRV $STUB $DB 2>$null | Out-Null
    docker network rm $NET 2>$null | Out-Null
}
Remove-DrillStack

# --- helpers ----------------------------------------------------------------------------

function Db([string]$Sql) {
    return (docker exec $DB psql -U postgres -d openbrain -tA -c $Sql | Out-String).Trim()
}

# The REST twin (x-brain-key). This is the INTERNAL lane - the position an OB1 container or
# agent-bridge occupies, which does not pass through the ops gateway at all.
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

# The MCP door (Bearer + JSON-RPC), which is what a host-side code agent actually speaks to.
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

# The ops door's policy is DERIVED FROM COMPOSE, never restated here. A drill carrying its
# own copy of the allow-list would keep passing after compose widened the real one, which is
# the exact shape of a check that checks nothing.
function Get-OpsGatewayEnv {
    param([Parameter(Mandatory)][string]$ComposePath)
    $txt = Get-Content -Raw $ComposePath
    $m = [regex]::Match($txt, "(?ms)^  openbrain-ops-gateway:\r?\n(.*?)(?=^  [a-z])")
    if (-not $m.Success) { return @{} }
    $found = @{}
    foreach ($line in ($m.Groups[1].Value -split "`n")) {
        $e = [regex]::Match($line, "^\s{6}(GATEWAY_[A-Z_]+):\s*(.+?)\s*$")
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

function Start-OpsGateway {
    param([string]$Name, [int]$Port, [hashtable]$GwEnv, [string]$Upstream)
    $a = @("run", "-d", "--name", $Name, "--network", $NET, "-p", "127.0.0.1:${Port}:8061",
           "-e", "OPENBRAIN_URL=$Upstream", "-e", "OPENBRAIN_KEY=$KEY",
           "-e", "GATEWAY_KEY=$OPSKEY")
    foreach ($k in $GwEnv.Keys) { $a += @("-e", "$k=$($GwEnv[$k])") }
    $a += $GWIMAGE
    docker @a | Out-Null
}

try {
    # --- 1. the throwaway plane ---------------------------------------------------------
    Section "an isolated plane - no live container, no real memory, ever"
    docker network create $NET 2>$null | Out-Null
    $chain = Get-ObInitChain -ComposePath (Join-Path $root "OB1\docker\docker-compose.yml")
    if ($chain.Count -lt 1) { Fail "could not parse the initdb chain from compose"; throw "no chain" }
    $tmp = Join-Path $env:TEMP "pp-drill-initdb"
    $staged = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $root "OB1\docker") -TargetDir $tmp
    if ($staged -ne $chain.Count) { Fail "staged $staged of $($chain.Count) migrations - a mount names a missing file" }
    else { Pass "staged the full initdb chain ($staged migrations)" }
    if (Start-ObInitdb -Name $DB -InitDir $tmp -DockerArgs @("--network", $NET)) {
        Pass "throwaway database is up on the real schema"
    } else { Fail "initdb did not complete - nothing below is trustworthy"; throw "db not ready" }
    $initErrs = Get-ObInitdbErrors -Name $DB
    if ($initErrs) { Write-Host ($initErrs -join "`n") -ForegroundColor Red; Fail "init chain had errors" }

    # A stub embedding endpoint: this drill is about a boundary, not about the GPU plane.
    $stubLines = @(
        'Deno.serve({ port: 8080 }, (req) => {',
        '  if (!req.url.includes("/embeddings")) return new Response("no", { status: 404 });',
        '  return Response.json({ data: [{ embedding: Array(1024).fill(0.001) }] });',
        '});'
    )
    $stubPath = Join-Path $env:TEMP "pp-drill-embed.ts"
    Set-Content -Path $stubPath -Value $stubLines -Encoding ASCII
    $stubFwd = ($stubPath -replace '\\', '/')
    docker run -d --name $STUB --network $NET -v "${stubFwd}:/stub.ts:ro" `
        denoland/deno:2.3.3 run --allow-net /stub.ts | Out-Null
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

    docker run -d --name $SRV --network $NET -p "127.0.0.1:${ServerPort}:8000" `
        -e DB_HOST=$DB -e DB_PORT=5432 -e DB_NAME=openbrain -e DB_USER=postgres `
        -e DB_PASSWORD=test -e MCP_ACCESS_KEY=$KEY -e PORT=8000 `
        -e "EMBEDDING_API_BASE=http://${STUB}:8080" -e EMBEDDING_API_KEY=stub `
        -e EMBEDDING_MODEL=stub-embed $IMAGE | Out-Null
    if (Wait-Http -Port $ServerPort -Path "/health") { Pass "openbrain-mcp (door exposure 'ops') is answering" }
    else { docker logs $SRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "server never answered"; throw "no server" }

    $opsEnv = Get-OpsGatewayEnv -ComposePath (Join-Path $root "OB1\docker\docker-compose.yml")
    if ($opsEnv.ContainsKey("GATEWAY_READ_TOOLS") -and $opsEnv["GATEWAY_PROFILE"] -eq "ops") {
        Pass "ops-door policy DERIVED from compose (read tools: $($opsEnv['GATEWAY_READ_TOOLS']))"
    } else {
        Fail "could not derive openbrain-ops-gateway's env from compose - the drill would be testing its own opinion"
        throw "no ops env"
    }
    Start-OpsGateway -Name $OPS -Port $OpsPort -GwEnv $opsEnv -Upstream "http://${SRV}:8000"
    if (Wait-Http -Port $OpsPort -Path "/health") { Pass "ops door is answering on :$OpsPort" }
    else { docker logs $OPS 2>&1 | Select-Object -Last 25 | Write-Host; Fail "ops gateway never answered"; throw "no gateway" }

    # --- 3. plant the SYNTHETIC fixture -------------------------------------------------
    Section "plant a synthetic personal-plane record, and an ops-plane control beside it"
    # tainted=true is the documented mechanical demotion: the calling runtime reports that
    # this effort consumed personal-plane input, and stampExposure has no path that widens.
    $planted = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-drill"; project_id = "proj-drill"
        summary = "synthetic personal fixture"; content = $PERSONAL
        memory_type = "lesson"; tainted = $true; idempotency_key = "$MARKER-personal"
    }
    $control = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = "ws-drill"; project_id = "proj-drill"
        summary = "synthetic ops control"; content = $OPSCTRL
        memory_type = "lesson"; idempotency_key = "$MARKER-ops"
    }
    if ($planted.Status -eq 200 -and $control.Status -eq 200) { Pass "both fixtures written" }
    else { Fail "could not plant the fixtures ($($planted.Status)/$($control.Status))"; throw "no fixture" }

    $exp = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$($planted.Body.memory_id)'"
    if ($exp -eq "personal") { Pass "the fixture really is ON the personal plane (exposure=$exp)" }
    else { Fail "the fixture is exposure='$exp' - the drill would be attacking nothing"; throw "bad fixture" }
    $expC = Db "SELECT metadata->>'exposure' FROM agent_memories WHERE id = '$($control.Body.memory_id)'"
    if ($expC -eq "ops") { Pass "the control really is on the ops plane (exposure=$expC)" }
    else { Fail "the control is exposure='$expC', expected ops" }

    # --- 4. ATTACK 1: the internal lane, naming the personal plane outright --------------
    Section "ATTACK 1 - an in-container agent names the personal plane in its recall"
    # include_unconfirmed on every recall below: both fixtures are review_status 'pending',
    # and a 'not returned' that was really the REVIEW gate firing would prove nothing about
    # exposure. This makes review status a non-factor, so the only variable is the plane.
    $probe = Invoke-Rest -Port $ServerPort -Path "/agent-memory/recall" -Body @{
        workspace_id = "ws-drill"; project_id = "proj-drill"
        query = $MARKER; limit = 25; include_unconfirmed = $true
        exposure = @("personal")
    }
    $probeIds = @()
    if ($probe.Body -and $probe.Body.items) { $probeIds = @($probe.Body.items | ForEach-Object { $_.memory_id }) }
    if ($probeIds -notcontains $planted.Body.memory_id) {
        Pass "STOPPED - the personal fixture was not returned, despite the caller naming its plane"
    } else { Fail "EXPOSURE LEAK: exposure:['personal'] reached the personal plane" }
    # And the probe COULD have found something: the control comes back on the same query.
    if ($probeIds -contains $control.Body.memory_id) {
        Pass "the ops control DID come back - so 'stopped' means filtered, not 'nothing matched'"
    } else { Fail "the control was not returned either - this recall proves nothing" }

    Section "ATTACK 1, the other half - is the attempt VISIBLE?"
    $flagged = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='ws-drill' AND event_type='recall_requested' AND payload->>'exposure_override_denied'='true'"
    if ($flagged -eq "1") { Pass "a durable audit row records the attempt (recall_requested, exposure_override_denied=true)" }
    else { Fail "expected exactly 1 flagged audit row, got '$flagged' - the attempt is invisible" }
    $asked = Db "SELECT payload->>'requested_exposure' FROM agent_memory_audit_events WHERE workspace_id='ws-drill' AND payload->>'exposure_override_denied'='true' LIMIT 1"
    if ($asked -match "personal") { Pass "the audit row says WHAT was asked for ($asked), not merely that something was refused" }
    else { Fail "the flagged audit row does not record the requested plane (got '$asked')" }

    # A benign recall must NOT be flagged. Without this, the assertion above passes just as
    # well against an audit writer that hardcodes 'true' - which would make the signal noise.
    $benign = Invoke-Rest -Port $ServerPort -Path "/agent-memory/recall" -Body @{
        workspace_id = "ws-drill"; project_id = "proj-drill"
        query = $MARKER; limit = 25; include_unconfirmed = $true
    }
    $unflagged = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='ws-drill' AND event_type='recall_requested' AND payload->>'exposure_override_denied'='false'"
    if ($benign.Status -eq 200 -and [int]$unflagged -ge 1) {
        Pass "an ordinary recall is recorded UNFLAGGED - the flag discriminates, it is not a constant"
    } else { Fail "an ordinary recall did not produce an unflagged audit row (got '$unflagged')" }

    $traced = Db "SELECT count(*) FROM agent_memory_recall_traces WHERE workspace_id='ws-drill' AND request_payload->>'exposure_override_denied'='true'"
    if ($traced -eq "1") { Pass "the trace carries requested vs enforced exposure too, so the audit row can be corroborated" }
    else { Fail "expected the recall trace to record the refused exposure, got '$traced'" }

    # --- 5. ATTACK 2: the ops door, the lane a host-side code agent uses -----------------
    Section "ATTACK 2 - a code agent at the ops door names the personal plane"
    $gwProbe = Invoke-Mcp -Port $OpsPort -Method "tools/call" -Params @{
        name = "agent_memory_recall"
        arguments = @{ workspace_id = "ws-drill"; project_id = "proj-drill"
                       query = $MARKER; limit = 25; include_unconfirmed = $true
                       exposure = @("personal") }
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
    $flaggedAfter = Db "SELECT count(*) FROM agent_memory_audit_events WHERE workspace_id='ws-drill' AND payload->>'exposure_override_denied'='true'"
    if ($flaggedAfter -eq "1") {
        Pass "still exactly 1 flagged durable row - the MCP lane is stopped at the tool schema and recorded at the door, not in the database"
    } else { Fail "expected the flagged-row count to still be 1 after the MCP probe, got '$flaggedAfter'" }

    # --- 6. ATTACK 3: go around agent-memory entirely, at the thoughts lane --------------
    Section "ATTACK 3 - the agent gives up on agent_memory_* and reaches for search_thoughts"
    # The smarter attack, and the one the allow-list exists for. Every agent memory also
    # writes a THOUGHT carrying the same content, and search_thoughts reads thoughts.
    $st = Invoke-Mcp -Port $OpsPort -Method "tools/call" -Params @{
        name = "search_thoughts"; arguments = @{ query = $MARKER; limit = 10 }
    }
    if ($st -and $st.error -and $st.error.code -eq -32601) {
        Pass "STOPPED - search_thoughts is not on the ops door's allow-list (-32601)"
    } else { Fail "search_thoughts was NOT denied at the ops door"; Note ($st | ConvertTo-Json -Depth 8 -Compress) }
    $gwLog = (docker logs $OPS 2>&1 | Out-String)
    if ($gwLog -match "tool_denied" -and $gwLog -match "search_thoughts") {
        Pass "the denial left an audit line naming the tool (tool_denied)"
    } else { Fail "the denied tool call left NO record - stopped, but invisible" }

    # --- 7. RED: prove every green above could have failed -------------------------------
    if ($SkipRed) {
        Section "RED phase SKIPPED (-SkipRed) - the green results above are unproven"
        Note "A guard nobody has watched fail is not known to guard anything."
    } else {
        Section "RED - remove the guard in a SCRATCH copy, and require the fixture to leak"
        $redSrc = Join-Path $env:TEMP "pp-drill-red-src"
        Remove-Item $redSrc -Recurse -Force -ErrorAction SilentlyContinue
        Copy-Item $SRC $redSrc -Recurse -Force
        $polPath = Join-Path $redSrc "agent-memory-policy.ts"
        $pol = [IO.File]::ReadAllText($polPath)
        $anchor = "  const enforced: Exposure[] = doorExposure ? [doorExposure] : [...DEFAULT_RECALL_EXPOSURES];"
        $hits = ([regex]::Matches($pol, [regex]::Escape($anchor))).Count
        if ($hits -ne 1) {
            # A search-and-replace that silently matched nothing is exactly how a red phase
            # turns into a second green phase without anyone noticing.
            Fail "the red patch anchor matched $hits times, expected 1 - refusing to build a 'red' image that is really green"
            throw "anchor drift"
        }
        $pol = $pol.Replace($anchor,
            "  const enforced: Exposure[] = (requested && requested.length ? [...requested] : (doorExposure ? [doorExposure] : [...DEFAULT_RECALL_EXPOSURES])) as Exposure[];")
        [IO.File]::WriteAllText($polPath, $pol)
        Pass "scratch copy patched: the door no longer overrides what the caller asked for"
        Note "the repo tree is untouched - this lives in $redSrc"

        docker build -t $REDIMAGE $redSrc 2>&1 | Select-Object -Last 1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $REDIMAGE"; throw "red build failed" }
        docker run -d --name $REDSRV --network $NET -p "127.0.0.1:${RedSrvPort}:8000" `
            -e DB_HOST=$DB -e DB_PORT=5432 -e DB_NAME=openbrain -e DB_USER=postgres `
            -e DB_PASSWORD=test -e MCP_ACCESS_KEY=$KEY -e PORT=8000 `
            -e "EMBEDDING_API_BASE=http://${STUB}:8080" -e EMBEDDING_API_KEY=stub `
            -e EMBEDDING_MODEL=stub-embed $REDIMAGE | Out-Null
        if (Wait-Http -Port $RedSrvPort -Path "/health") { Pass "the unguarded server is up on :$RedSrvPort (same database, same fixture)" }
        else { docker logs $REDSRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "red server never answered"; throw "no red server" }

        $red = Invoke-Rest -Port $RedSrvPort -Path "/agent-memory/recall" -Body @{
            workspace_id = "ws-drill"; project_id = "proj-drill"
            query = $MARKER; limit = 25; include_unconfirmed = $true
            exposure = @("personal")
        }
        $redIds = @()
        if ($red.Body -and $red.Body.items) { $redIds = @($red.Body.items | ForEach-Object { $_.memory_id }) }
        if ($redIds -contains $planted.Body.memory_id) {
            Pass "RED CONFIRMED - without the door override, the SAME request DOES return the personal fixture"
        } else {
            Fail "the unguarded server did not leak either - ATTACK 1's pass proves nothing"
            Note ($red | ConvertTo-Json -Depth 8 -Compress)
        }

        Section "RED - dismantle the search_thoughts lane's guards, one at a time"
        # No code patch needed here: BOTH guards on this lane are configuration, so removing
        # one means changing an env value.
        #
        # A CORRECTION THIS DRILL PAID FOR. The first version of this section assumed one
        # guard - the allow-list - on the reasoning that search_thoughts applies no exposure
        # filter of its own (index.ts:497, and it does not). Allowing the tool therefore had
        # to leak. It did not: only the ops control came back. The reason is the door's
        # SECOND guard, which the compose comment calls "belt-and-braces" and undersells -
        # _force_read_filter injects metadata_filter={exposure:'ops'}, search_thoughts DOES
        # honour metadata_filter (`metadata @> $4::jsonb`), and the exposure label mirrored
        # onto the thought is what that clause matches. It is belt-and-braces for
        # agent_memory_recall, whose zod schema has no metadata_filter field and strips it;
        # for search_thoughts it is the whole boundary. So the two are asserted separately.
        $redEnv = @{}
        foreach ($k in $opsEnv.Keys) { $redEnv[$k] = $opsEnv[$k] }
        $redEnv["GATEWAY_READ_TOOLS"] = $opsEnv["GATEWAY_READ_TOOLS"] + ",search_thoughts"
        Start-OpsGateway -Name $REDOPS -Port $RedOpsPort -GwEnv $redEnv -Upstream "http://${SRV}:8000"
        if (Wait-Http -Port $RedOpsPort -Path "/health") {
            $redSt = Invoke-Mcp -Port $RedOpsPort -Method "tools/call" -Params @{
                name = "search_thoughts"; arguments = @{ query = $MARKER; limit = 10 }
            }
            $redBlob = ($redSt | ConvertTo-Json -Depth 12 -Compress)
            if ($redBlob -match "SYNTHETIC ops-plane CONTROL") {
                Pass "with search_thoughts allowed the call runs and returns the ops control"
                if ($redBlob -notmatch "SYNTHETIC personal-plane FIXTURE") {
                    Pass "DEFENCE IN DEPTH - the allow-list alone was not the boundary; the forced read filter still holds"
                } else { Fail "the allow-list was the only guard on this lane" }
            } else { Fail "the widened door returned nothing at all - this sub-check proves nothing"; Note $redBlob }
        } else { Fail "red ops gateway never answered" }

        # Now take the SECOND guard away too, and require the leak. If this does not leak,
        # ATTACK 3 is asserting a denial that was never protecting anything.
        docker rm -f $REDOPS 2>$null | Out-Null
        $redEnv["GATEWAY_READ_FILTER_VALUE"] = "personal"
        Start-OpsGateway -Name $REDOPS -Port $RedOpsPort -GwEnv $redEnv -Upstream "http://${SRV}:8000"
        if (Wait-Http -Port $RedOpsPort -Path "/health") {
            $redSt2 = Invoke-Mcp -Port $RedOpsPort -Method "tools/call" -Params @{
                name = "search_thoughts"; arguments = @{ query = $MARKER; limit = 10 }
            }
            $redBlob2 = ($redSt2 | ConvertTo-Json -Depth 12 -Compress)
            if ($redBlob2 -match "SYNTHETIC personal-plane FIXTURE") {
                Pass "RED CONFIRMED - allow the tool AND point its forced filter at the personal plane, and the fixture is readable"
            } else {
                Fail "even with both guards off the fixture did not come back - ATTACK 3 proves nothing"
                Note $redBlob2
            }
        } else { Fail "red ops gateway (second variant) never answered" }
    }

} catch {
    Write-Host ("  aborted: " + $_.Exception.Message) -ForegroundColor Red
    $fails++
} finally {
    if ($KeepUp) {
        Write-Host "`n-KeepUp: leaving the drill stack on network $NET" -ForegroundColor Yellow
        Write-Host "  marker: $MARKER"
        Write-Host "  tear down with: docker rm -f $REDOPS $REDSRV $OPS $SRV $STUB $DB; docker network rm $NET"
    } else {
        Remove-DrillStack
    }
}

Write-Host ""
if ($fails -eq 0) {
    Write-Host "PERSONAL-PLANE EXCLUSION DRILL PASSED - every attack stopped, every attempt recorded" -ForegroundColor Green
    exit 0
}
Write-Host "$fails DRILL CHECK(S) FAILED" -ForegroundColor Red
exit 1
