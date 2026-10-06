# smoke-openbrain-personal-lane.ps1 - item amp-owui-deny (memory-plane PLAN 1.1, operator D1
# 2026-10-06). Proves, on a DISPOSABLE plane, which Open Brain callers can reach the seven
# agent_memory_* tools:
#
#   the OWUI path   openbrain-mcpo -> openbrain-mcp, holding whatever key the tree's
#                   OB1/docker/mcpo.config.json.example tells it to hold   -> must be DENIED
#   agent-bridge    its own client code (openbrain_memory.py), MCP_ACCESS_KEY -> must WORK
#   the ops door    an openbrain-gateway with compose's ops profile         -> must WORK
#
#   -Expect red    the bug is present: the 7 tools are in mcpo's openapi.json and a writeback
#                  through mcpo lands a row stamped ops. (BASE: -Ob1Rev b0033a1)
#   -Expect green  the 7 tools are absent from openapi.json and from the lane's tools/list,
#                  every direct call with mcpo's key is refused with a clear error, nothing
#                  is written, and the non-agent tools still work. (TIP: the working tree)
#
# The agent-bridge and ops-door checks run, and must pass, in BOTH modes.
#
# ISOLATION (CLAUDE.md; incident 2026-09-27): one `--internal` network named <Prefix>-net,
# never an ai-stack_* network; every container is <Prefix>-*; images are built ONLY as
# :<Tag>, never :local. agent-bridge:local is RUN (never rebuilt or retagged) purely as a
# python runtime with the tree's agent-bridge code mounted over it. No live container, DB or
# config is touched. Everything is removed in the finally (unless -KeepUp), and the host
# container count is printed before and after.
#
#   .\scripts\checks\smoke-openbrain-personal-lane.ps1 -Expect red -Ob1Rev b0033a1
#   .\scripts\checks\smoke-openbrain-personal-lane.ps1 -Expect green
#
# Exit: 0 = every check for the chosen -Expect passed | 1 = otherwise

[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("red", "green")][string]$Expect,
    # An OB1 revision to export with `git archive` (e.g. b0033a1), or empty for the OB1
    # working tree as it is on disk.
    [string]$Ob1Rev = "",
    [string]$Prefix = "t-amp-owui",
    [string]$Tag = "wt-amp-owui-deny",
    [switch]$KeepUp,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Continue"   # native docker stderr must never be fatal (PS 5.1)
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")
. (Join-Path $PSScriptRoot "lib\harness-owner.ps1")

if ($Prefix -notmatch '^t-') { throw "Prefix must start with 't-' (disposable names only)" }
if ($Tag -eq "local") { throw "never build :local from a test" }

$fails = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t) { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }
function Info($t) { Write-Host "  INFO  $t" }

$OWNER = $Prefix
Write-Host (Format-HarnessOwnerBanner $OWNER)
$NET   = "$Prefix-net"
$DB    = "$Prefix-db"
$STUB  = "$Prefix-embed"
$SRV   = "$Prefix-mcp"
$MCPO  = "$Prefix-mcpo"
$GW    = "$Prefix-gw"
# Throwaway values, generated per run, never printed. The two MCP keys must differ.
function New-RigKey { -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) }
$FULL_KEY = New-RigKey
$PERSONAL_KEY = New-RigKey
$MCPO_API_KEY = New-RigKey
$OPS_KEY = New-RigKey
$IMG_MCP  = "openbrain-mcp-server:$Tag"
$IMG_MCPO = "openbrain-mcpo:$Tag"
$IMG_GW   = "openbrain-gateway:$Tag"
$IMG_BRIDGE_RUNTIME = "agent-bridge:local"   # RUN only, as a python runtime
$WORK = Join-Path $env:TEMP "$Prefix-rig"

function Get-HostContainerCount { @(docker ps -aq 2>$null).Count }
function Remove-Rig {
    docker rm -f $GW $MCPO $SRV $STUB $DB "$Prefix-probe" "$Prefix-bridge" 2>$null | Out-Null
    docker network rm $NET 2>$null | Out-Null
}
function Psql([string]$sql) {
    ((docker exec $DB psql -U postgres -d openbrain -tA -c $sql 2>&1) | Out-String).Trim()
}
function Wait-Log([string]$name, [string]$pattern, [int]$sec = 90) {
    for ($i = 0; $i -lt $sec; $i++) {
        Start-Sleep 1
        if ((docker logs $name 2>&1 | Out-String) -match $pattern) { return $true }
        $st = (docker inspect --format '{{.State.Status}}' $name 2>$null | Out-String).Trim()
        if ($st -eq "exited" -or $st -eq "") { return $false }
    }
    return $false
}
function ConvertTo-Fwd([string]$p) { $p -replace '\\', '/' }

$before = Get-HostContainerCount
Write-Host "host containers before: $before"
Remove-Rig

try {
    # --- 0. the OB1 tree under test --------------------------------------------------------
    Section "the OB1 tree under test"
    if (Test-Path $WORK) { Remove-Item -Recurse -Force $WORK }
    New-Item -ItemType Directory -Force $WORK | Out-Null
    $ob1Live = Join-Path $root "OB1"
    if ($Ob1Rev) {
        $tree = Join-Path $WORK "ob1"
        New-Item -ItemType Directory -Force $tree | Out-Null
        $tar = Join-Path $WORK "ob1.tar"
        git -C $ob1Live archive --format=tar -o $tar $Ob1Rev integrations/kubernetes-deployment docker
        if ($LASTEXITCODE -ne 0) { Fail "git archive $Ob1Rev failed"; throw "no tree" }
        tar -xf $tar -C $tree
        if ($LASTEXITCODE -ne 0) { Fail "tar extract failed"; throw "no tree" }
        $sha = (git -C $ob1Live rev-parse --short $Ob1Rev | Out-String).Trim()
        Pass "exported OB1 $Ob1Rev ($sha)"
    } else {
        $tree = $ob1Live
        $sha = (git -C $ob1Live rev-parse --short HEAD | Out-String).Trim()
        $dirty = (git -C $ob1Live status --porcelain -- integrations/kubernetes-deployment docker | Out-String).Trim()
        Pass ("OB1 working tree at $sha" + $(if ($dirty) { " (DIRTY: uncommitted changes are under test)" } else { "" }))
    }

    # --- 1. images (only :$Tag) ------------------------------------------------------------
    Section "build test images as :$Tag"
    if (-not $SkipBuild) {
        foreach ($b in @(
            @{ Img = $IMG_MCP;  Ctx = (Join-Path $tree "integrations\kubernetes-deployment") },
            @{ Img = $IMG_MCPO; Ctx = (Join-Path $tree "docker\mcpo") },
            @{ Img = $IMG_GW;   Ctx = (Join-Path $root "openbrain-gateway") })) {
            docker build -q -t $b.Img $b.Ctx 2>&1 | Select-Object -Last 1 | Out-Null
            if ($LASTEXITCODE -ne 0) { Fail "docker build $($b.Img) failed"; throw "build" }
            Pass "built $($b.Img)"
        }
    } else { Info "-SkipBuild: using existing :$Tag images" }
    docker image inspect $IMG_BRIDGE_RUNTIME 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "$IMG_BRIDGE_RUNTIME not present (needed as a runtime only)"; throw "no runtime" }

    # --- 2. network + database + stub embeddings --------------------------------------------
    Section "disposable plane on an --internal network"
    docker network create --internal (Get-HarnessOwnerLabel $OWNER) $NET 2>&1 | Out-Null
    $internal = (docker network inspect $NET --format '{{.Internal}}' | Out-String).Trim()
    if ($internal -eq "true") { Pass "$NET is internal" } else { Fail "$NET is not internal"; throw "net" }

    $chain = Get-ObInitChain -ComposePath (Join-Path $tree "docker\docker-compose.yml")
    $initDir = Join-Path $WORK "initdb"
    $staged = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $tree "docker") -TargetDir $initDir
    if ($staged -ne $chain.Count -or $staged -lt 1) { Fail "staged $staged of $($chain.Count) migrations"; throw "chain" }
    if (Start-ObInitdb -Name $DB -InitDir $initDir -DockerArgs @("--network", $NET) -Owner $OWNER) {
        Pass "database initialised on the tree's initdb chain ($staged migrations)"
    } else { Fail "initdb did not complete"; throw "db" }
    $initErrs = Get-ObInitdbErrors -Name $DB
    if ($initErrs) { Write-Host ($initErrs -join "`n") -ForegroundColor Red; Fail "init chain had errors" }

    $stubFile = Join-Path $WORK "embed.ts"
    Set-Content -Path $stubFile -Encoding ASCII -Value @(
        'Deno.serve({ port: 8080 }, (req) => {',
        '  if (!req.url.includes("/embeddings")) return new Response("no", { status: 404 });',
        '  return Response.json({ data: [{ embedding: Array(1024).fill(0.001) }] });',
        '});')
    docker run -d --name $STUB (Get-HarnessOwnerLabel $OWNER) --network $NET `
        -v "$(ConvertTo-Fwd $stubFile):/stub.ts:ro" denoland/deno:2.3.3 run --allow-net /stub.ts | Out-Null
    if (Wait-Log $STUB "Listening") { Pass "stub embeddings up" } else { Fail "stub embeddings never came up"; throw "stub" }

    # --- 3. openbrain-mcp (alias openbrain-mcp, as compose names it) ------------------------
    Section "openbrain-mcp ($IMG_MCP)"
    docker run -d --name $SRV (Get-HarnessOwnerLabel $OWNER) --network $NET --network-alias openbrain-mcp `
        -e DB_HOST=$DB -e DB_PORT=5432 -e DB_NAME=openbrain -e DB_USER=postgres -e DB_PASSWORD=test `
        -e "MCP_ACCESS_KEY=$FULL_KEY" -e "MCP_PERSONAL_ACCESS_KEY=$PERSONAL_KEY" -e PORT=8000 `
        -e "EMBEDDING_API_BASE=http://${STUB}:8080" -e EMBEDDING_API_KEY=stub -e EMBEDDING_MODEL=stub-embed `
        -e "CHAT_API_BASE=http://${STUB}:8080" -e CHAT_API_KEY=stub -e CHAT_MODEL=stub `
        $IMG_MCP | Out-Null
    if (Wait-Log $SRV "Listening on") { Pass "openbrain-mcp listening" }
    else { docker logs $SRV 2>&1 | Select-Object -Last 20 | Write-Host; Fail "openbrain-mcp never listened"; throw "srv" }

    # --- 4. the OWUI bridge, configured from THE TREE'S OWN TEMPLATE ------------------------
    Section "openbrain-mcpo ($IMG_MCPO), config rendered from the tree's mcpo.config.json.example"
    $tpl = Get-Content -Raw (Join-Path $tree "docker\mcpo.config.json.example")
    $rendered = $tpl.Replace("REPLACE_WITH_MCP_PERSONAL_ACCESS_KEY", $PERSONAL_KEY).Replace("REPLACE_WITH_MCP_ACCESS_KEY", $FULL_KEY)
    $mcpoCfg = Join-Path $WORK "mcpo.config.json"
    [System.IO.File]::WriteAllText($mcpoCfg, $rendered, (New-Object System.Text.UTF8Encoding($false)))
    $laneKey = ((ConvertFrom-Json $rendered).mcpServers.'open-brain'.headers.'x-brain-key')
    $laneName = if ($laneKey -eq $FULL_KEY) { "MCP_ACCESS_KEY (full)" } elseif ($laneKey -eq $PERSONAL_KEY) { "MCP_PERSONAL_ACCESS_KEY (personal)" } else { "UNKNOWN" }
    Info "the template tells mcpo to hold: $laneName"
    docker run -d --name $MCPO (Get-HarnessOwnerLabel $OWNER) --network $NET `
        -v "$(ConvertTo-Fwd $mcpoCfg):/config/mcpo.config.json:ro" $IMG_MCPO `
        --config /config/mcpo.config.json --host 0.0.0.0 --port 8000 --api-key $MCPO_API_KEY | Out-Null
    if (Wait-Log $MCPO "Uvicorn running|Application startup complete" 120) { Pass "mcpo up" }
    else { docker logs $MCPO 2>&1 | Select-Object -Last 20 | Write-Host; Fail "mcpo never came up"; throw "mcpo" }

    # --- 5. the ops door, env as compose's openbrain-ops-gateway ----------------------------
    Section "ops door ($IMG_GW, GATEWAY_PROFILE=ops)"
    docker run -d --name $GW (Get-HarnessOwnerLabel $OWNER) --network $NET `
        -e OPENBRAIN_URL=http://openbrain-mcp:8000 -e "OPENBRAIN_KEY=$FULL_KEY" -e "GATEWAY_KEY=$OPS_KEY" `
        -e GATEWAY_PROFILE=ops `
        -e GATEWAY_READ_TOOLS=agent_memory_recall,agent_memory_inspect,agent_memory_recall_trace,agent_memory_list_review_queue `
        -e GATEWAY_WRITE_TOOLS=agent_memory_writeback,agent_memory_review,agent_memory_report_usage `
        -e GATEWAY_READ_FILTER_FIELD=exposure -e GATEWAY_READ_FILTER_VALUE=ops -e GATEWAY_WRITE_ORIGIN=ops `
        -e GATEWAY_WRITE_STAMP_FIELD=exposure -e GATEWAY_WRITE_STAMP_VALUE=ops `
        $IMG_GW | Out-Null
    if (Wait-Log $GW "Uvicorn running|Application startup complete") { Pass "ops door up" }
    else { docker logs $GW 2>&1 | Select-Object -Last 20 | Write-Host; Fail "ops door never came up"; throw "gw" }

    $probe = Join-Path $root "scripts\checks\lib\personal_lane_probe.py"
    function Invoke-Probe([string]$stage) {
        $txt = (docker run --rm --name "$Prefix-probe" --network $NET --entrypoint /app/.venv/bin/python `
            -v "$(ConvertTo-Fwd $probe):/probe.py:ro" `
            -e "MCPO_URL=http://${MCPO}:8000/open-brain" -e "MCPO_API_KEY=$MCPO_API_KEY" `
            -e "RAW_URL=http://openbrain-mcp:8000/" -e "LANE_KEY=$laneKey" `
            -e "GW_URL=http://${GW}:8061/mcp" -e "GW_KEY=$OPS_KEY" `
            $IMG_MCPO /probe.py $stage 2>$null | Out-String).Trim()
        try { return ($txt | ConvertFrom-Json) } catch { Write-Host $txt; return $null }
    }
    function Get-Exposure([string]$summary) { Psql "SELECT coalesce(string_agg(exposure, ','), '') FROM agent_memories WHERE summary = '$summary'" }
    $countSql = "SELECT (SELECT count(*) FROM agent_memories) || '/' || (SELECT count(*) FROM agent_memory_audit_events)"

    # --- 6. THE OWUI PATH --------------------------------------------------------------------
    Section "the OWUI path (mcpo, and mcpo's own key straight at openbrain-mcp)"
    $c0 = Psql $countSql
    $o = Invoke-Probe "owui"
    $c1 = Psql $countSql
    if (-not $o) { Fail "owui probe returned nothing"; throw "probe" }
    $amTools = @($o.openapi_tools | Where-Object { $_ -like "agent_memory_*" })
    $laneAm = @($o.lane_tools | Where-Object { $_ -like "agent_memory_*" })
    $laneOther = @($o.lane_tools | Where-Object { $_ -notlike "agent_memory_*" })
    Info "openapi.json: status $($o.openapi_status), $(@($o.openapi_tools).Count) tools, $($amTools.Count) agent_memory_*"
    Info "mcpo writeback: HTTP $($o.mcpo_writeback.status)"
    Info "rows/audit before -> after the OWUI stage: $c0 -> $c1"
    $owuiExp = Get-Exposure "amp-owui-deny probe via owui-mcpo"
    $directExp = (Get-Exposure "amp-owui-deny probe via owui-direct") + (Get-Exposure "amp-owui-deny probe via owui-rest") + (Get-Exposure "amp-owui-deny probe via owui-batch")
    Info "row written via mcpo: exposure='$owuiExp'; via the lane key directly: exposure='$directExp'"

    if ($Expect -eq "red") {
        if ($amTools.Count -eq 7) { Pass "RED: all 7 agent_memory_* tools are in mcpo's openapi.json" }
        else { Fail "RED not reproduced: openapi.json lists $($amTools.Count) agent_memory_* tools" }
        if ($o.mcpo_writeback.status -eq 200 -and $owuiExp -eq "ops") { Pass "RED: a writeback through mcpo succeeded and the row is stamped ops" }
        else { Fail "RED not reproduced: mcpo writeback HTTP $($o.mcpo_writeback.status), exposure '$owuiExp'" }
    } else {
        if ($o.openapi_status -eq 200 -and $amTools.Count -eq 0) { Pass "GREEN: no agent_memory_* tool in mcpo's openapi.json" }
        else { Fail "openapi.json (HTTP $($o.openapi_status)) still lists: $($amTools -join ', ')" }
        $keep = @("search_thoughts", "list_thoughts", "capture_thought", "fetch", "search")
        $missing = @($keep | Where-Object { @($o.openapi_tools) -notcontains $_ })
        if ($missing.Count -eq 0) { Pass "the other tools are still served to OWUI ($(@($o.openapi_tools).Count) tools)" }
        else { Fail "OWUI lost non-agent tools: $($missing -join ', ')" }
        if ($o.mcpo_list_thoughts.status -eq 200) { Pass "a non-agent tool (list_thoughts) still works through mcpo" }
        else { Fail "list_thoughts through mcpo returned HTTP $($o.mcpo_list_thoughts.status)" }
        if ($o.mcpo_writeback.status -ge 400) { Pass "a direct POST to mcpo's agent_memory_writeback is refused (HTTP $($o.mcpo_writeback.status))" }
        else { Fail "mcpo accepted agent_memory_writeback (HTTP $($o.mcpo_writeback.status))" }
        if ($laneAm.Count -eq 0 -and $laneOther.Count -gt 0) { Pass "tools/list with mcpo's key: 0 agent_memory_*, $($laneOther.Count) others" }
        else { Fail "tools/list with mcpo's key: $($laneAm.Count) agent_memory_*, $($laneOther.Count) others" }
        foreach ($p in $o.lane_calls.PSObject.Properties) {
            $err = $p.Value.msg.error
            if ($err -and "$($err.message)" -match "personal lane" -and "$($err.message)" -match "Nothing was written") {
                Pass "$($p.Name) with mcpo's key: refused - $(("$($err.message)").Substring(0, 60))..."
            } else { Fail "$($p.Name) with mcpo's key was NOT refused: $($p.Value | ConvertTo-Json -Depth 6 -Compress)" }
        }
        $rw = $o.lane_rest.'/agent-memory/writeback'; $rr = $o.lane_rest.'/agent-memory/recall'
        if ($rw -eq 401 -and $rr -eq 401) { Pass "the REST twins refuse mcpo's key (writeback $rw, recall $rr)" }
        else { Fail "REST twins with mcpo's key: writeback $rw, recall $rr (expected 401)" }
        $b = $o.lane_batch
        $bErr = @($b.msg) | Where-Object { $_.error -and "$($_.error.message)" -match "personal lane" }
        if ($bErr) { Pass "a JSON-RPC batch carrying agent_memory_writeback is refused" }
        else { Fail "batch not refused: $($b | ConvertTo-Json -Depth 6 -Compress)" }
        if ($c0 -eq $c1 -and -not $owuiExp -and -not $directExp) { Pass "NOTHING was written on the OWUI path (memories/audit $c0 -> $c1)" }
        else { Fail "the OWUI path wrote: memories/audit $c0 -> $c1, rows '$owuiExp' / '$directExp'" }
    }

    # --- 7. THE AGENT-BRIDGE PATH (its own client code) --------------------------------------
    Section "the agent-bridge path (agent-org/agent-bridge/app, MCP_ACCESS_KEY)"
    $bridgeApp = Join-Path $root "agent-org\agent-bridge\app"
    $bridgeProbe = Join-Path $root "scripts\checks\lib\personal_lane_bridge_probe.py"
    function Invoke-Bridge([string]$usageId) {
        $txt = (docker run --rm --name "$Prefix-bridge" --network $NET --entrypoint python -w /src `
            -v "$(ConvertTo-Fwd $bridgeApp):/src/app:ro" -v "$(ConvertTo-Fwd $bridgeProbe):/src/bridge_probe.py:ro" `
            -e PYTHONPATH=/src -e "RAW_URL=http://openbrain-mcp:8000" -e "FULL_KEY=$FULL_KEY" -e "USAGE_MEMORY_ID=$usageId" `
            $IMG_BRIDGE_RUNTIME /src/bridge_probe.py 2>$null | Out-String).Trim()
        try { return ($txt | ConvertFrom-Json) } catch { Write-Host $txt; return $null }
    }
    $br = Invoke-Bridge ""
    $brExp = Get-Exposure "amp-owui-deny probe via agent-bridge"
    if ($br -and $br.write -eq $true -and $brExp -eq "ops") { Pass "agent-bridge writeback (MCP tools/call) wrote a row stamped ops" }
    else { Fail "agent-bridge writeback: $($br | ConvertTo-Json -Compress), exposure '$brExp'" }
    if ($br -and $br.recall_trace_id) { Pass "agent-bridge recall (REST twin) answered with a trace" }
    else { Fail "agent-bridge recall returned no trace: $($br | ConvertTo-Json -Compress)" }
    $mid = Psql "SELECT id FROM agent_memories WHERE summary = 'amp-owui-deny probe via agent-bridge' LIMIT 1"
    $br2 = Invoke-Bridge $mid
    if ($br2 -and $br2.report_usage -eq $true) { Pass "agent-bridge report_usage (MCP tools/call) recorded" }
    else { Fail "agent-bridge report_usage: $($br2 | ConvertTo-Json -Compress)" }

    # --- 8. THE OPS DOOR ----------------------------------------------------------------------
    Section "the ops door (gateway, ops profile)"
    $g = Invoke-Probe "ops"
    $gwAm = @($g.gw_tools | Where-Object { $_ -like "agent_memory_*" })
    if ($gwAm.Count -eq 7) { Pass "ops door lists all 7 agent_memory_* tools" }
    else { Fail "ops door lists $($gwAm.Count) agent_memory_* tools: $($g.gw_tools -join ', ')" }
    $gwExp = Get-Exposure "amp-owui-deny probe via ops-door"
    $wb = $g.gw_writeback.msg
    if ($g.gw_writeback.status -eq 200 -and -not $wb.error -and -not $wb.result.isError -and $gwExp -eq "ops") {
        Pass "ops door writeback wrote a row stamped ops"
    } else { Fail "ops door writeback: $($g.gw_writeback | ConvertTo-Json -Depth 6 -Compress), exposure '$gwExp'" }
    $q = $g.gw_queue.msg
    if ($g.gw_queue.status -eq 200 -and -not $q.error -and -not $q.result.isError) { Pass "ops door list_review_queue answered" }
    else { Fail "ops door list_review_queue: $($g.gw_queue | ConvertTo-Json -Depth 6 -Compress)" }
}
catch {
    if ("$_" -notmatch '^(no tree|build|no runtime|net|chain|db|stub|srv|mcpo|gw|probe)$') { Fail "rig error: $_" }
}
finally {
    if ($KeepUp) { Write-Host "`n-KeepUp: leaving $Prefix-* up. Remove: docker rm -f $GW $MCPO $SRV $STUB $DB; docker network rm $NET" -ForegroundColor Yellow }
    else {
        Remove-Rig
        if (Test-Path $WORK) { Remove-Item -Recurse -Force $WORK -ErrorAction SilentlyContinue }
    }
    $after = Get-HostContainerCount
    Write-Host "host containers after: $after (before: $before)"
    $left = @(docker ps -aq --filter "name=$Prefix-" 2>$null).Count
    $leftNet = @(docker network ls -q --filter "name=$NET" 2>$null).Count
    if (-not $KeepUp) {
        if ($left -eq 0 -and $leftNet -eq 0 -and $after -eq $before) { Pass "teardown: no $Prefix-* container or network left; host count back to $before" }
        else { Fail "teardown: $left container(s), $leftNet network(s) left; host $before -> $after" }
    }
}

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL PASSED (-Expect $Expect, OB1 $sha)" -ForegroundColor Green; exit 0 }
Write-Host "$fails FAILED (-Expect $Expect, OB1 $sha)" -ForegroundColor Red
exit 1
