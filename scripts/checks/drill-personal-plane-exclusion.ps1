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
# ------------------------------------------------------------------------------------
# WHICH TREE IT ATTACKS - THE RECORDED GITLINK, NOT THE WORKING COPY
# ------------------------------------------------------------------------------------
# This used to build from `OB1/` on disk, and that is how the previous round was green about
# a tree that did not merge: the entire fix lived in an OB1 commit that was never
# `git add OB1`'d, the branch still pinned the commit BEFORE it, and a verifier building
# from what the branch ACTUALLY PINS reproduced the full leak against a drill reporting 100
# checks passed. So the drill now exports the gitlink `git ls-tree HEAD OB1` names and
# builds every image from that - what a merge would ship - and FAILS BEFORE IT STARTS if the
# working copy is dirty or sitting on a different commit, because a divergence in the other
# direction means the operator's edits are silently not under test. There is no override
# switch: "let me run it dirty just this once" is how a gate stops meaning anything.
#
#   ATTACKED (all built from the RECORDED GITLINK, on a throwaway plane):
#     0. the RAW MCP DOOR         - openbrain-mcp's own tool surface, x-brain-key, with NO
#                                   gateway in front of it: no allow-list, no forced read
#                                   filter. THE DOOR WITH REAL PRODUCTION TRAFFIC.
#                                   OB1/docker/mcpo.config.json points `openbrain-mcpo` -
#                                   Open WebUI's Open Brain bridge, on obnet + llm-net -
#                                   straight at http://openbrain-mcp:8000 with the raw
#                                   MCP_ACCESS_KEY, and the cloud gateway's own docstring
#                                   says local clients bypass it BY DESIGN.
#                                   This drill ALLOCATED $ServerPort for that door and then
#                                   never called a tool on it: it proved the boundary at
#                                   the two doors that are not the exposed one. ATTACK 11.
#     1. the INTERNAL REST lane   - openbrain-mcp's /agent-memory/* twin, x-brain-key.
#                                   The position an OB1 container or agent-bridge occupies.
#     2. the OPS door             - a gateway instance whose env is DERIVED from compose's
#                                   openbrain-ops-gateway. Config-identical to :8062.
#                                   Callers today: memory_writer.py and durable_checks.py,
#                                   both WRITE only - nothing reads through this door yet.
#                                   Attacked anyway, because it is the door the memory
#                                   plane is being built for, and a boundary is cheaper to
#                                   prove before it has read traffic than after.
#     2b. the openbrain-EXT door - a SECOND CONTAINER, and a reader nobody had looked at.
#                                   `link_thought_to_contact` resolved a thought BY ID with
#                                   no plane predicate, returned its content, and appended
#                                   that content into `professional_contacts.notes` - a
#                                   THIRD home, in a table with no exposure label and no way
#                                   to grow one. Four rounds of this work were scoped to
#                                   openbrain-mcp and could not see it. ATTACK 13.
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
# THE CORPUS HAS TWO SIDES, AND EARLIER ROUNDS ONLY PROVED ONE. ATTACK 11 proves personal
# content never ENTERS `thoughts` - a property of writes from now on. It says nothing about a
# row already there, and rows are already there: the mirror shipped and ran before the guard
# existed. ATTACK 12 plants exactly that - a personal-labelled corpus row, written directly
# the way the pre-guard mirror wrote it - and fires every corpus reader the raw door exposes
# at it: list_thoughts, search_thoughts, the ChatGPT-compat `search`, `fetch` by id, and the
# thought_stats COUNT, which is a disclosure of its own.
#
#   NOT ATTACKED, and not claimed:
#     - the RUNNING containers on ai-stack. This drill never touches openbrain-db,
#       openbrain-gateway or openbrain-ops-gateway, never joins an ai-stack_* network, and
#       tags its images :drill-<runid>, never :local. It proves THE SOURCE TREE's boundary.
#       Whether production is running that tree is a separate question - the deploy gate -
#       and this drill does not answer it.
#     - the real personal plane. Class 4, absolute. Every fixture below is synthetic.
#     - `integrations/agent-memory-api`. It is guarded in source and covered by the
#       completeness gate, and NOTHING IN THIS STACK DEPLOYS IT - it is a Supabase Edge
#       Function and no compose context builds it - so there is no container for this drill
#       to attack. "Guarded in source, undrilled" is stated rather than blurred.
#
# ------------------------------------------------------------------------------------
# THE LIFT
# ------------------------------------------------------------------------------------
# The last section gathers what the attacks proved into the one conjunction that decides
# whether the operational rule ("do not write a personal-exposure memory") can be dropped:
# the memory is WRITTEN through the real write path, REFUSED by every targeted door, every
# refusal is RECORDED, no record carries the content, and the plane holds zero personal rows
# again once the fixture is removed. It is a statement about THE TREE AT THE GITLINK. It is
# not a statement about the running stack, which this drill never touches.
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
# TWO anchors in that one file where it used to need three spread across the subsystem. One
# is the plane a lookup is bound to - removing it lights up ATTACKS 3, 4, 5, 5b, 8, 9 and 10
# at once. The other is the MIRROR guard, and it lights up ATTACK 11 on its own, because the
# second home is a different failure from the read side: nothing is refused, nothing is
# audited, and the content is simply in a store six unguarded statements read. The number of
# red confirmations a single line produces is the measure of how much of the boundary that
# line is carrying.
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
    [int]$RedMemPort = 0,
    [int]$ExtPort    = 0,
    [int]$RedExtPort = 0
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
$gaps = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t)    { Write-Host "  PASS  $t" -ForegroundColor Green; $script:passes++ }
function Fail($t)    { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }
function Note($t)    { Write-Host "        $t" -ForegroundColor DarkGray }
# A THIRD OUTCOME, AND IT IS NOT A SOFTER FAIL. It marks a property U5's column REQUIRES,
# that this tree cannot currently deliver, for a reason the drill can state precisely - and
# the run still exits NON-ZERO when any gap is open. It exists because the alternative was
# to delete the assertion, and an assertion deleted is a requirement that leaves no trace.
# See the summary block at the end of this file, and documentation/notes/u8h3-findings.md.
function Gap($t)     { Write-Host "  GAP   $t" -ForegroundColor Yellow; $script:gaps++ }
$AUDIT_GAP = "A REFUSAL RECORD REQUIRES SEEING WHAT YOU ARE REFUSING, and under the database boundary the door cannot. auditRefusal fires only after a bare SELECT 1 FROM agent_memories WHERE id=`$1 confirms the row EXISTS - and that probe is bound by the same policy that hid it, so for a non-superuser door it returns nothing and no record is written. As a SUPERUSER the probe succeeds and the record IS written, but then nothing was stopped either. NEITHER configuration satisfies U5's column, which asks for both. Closing it needs an elevated existence probe (SECURITY DEFINER, answers 'exists' without returning the row) - a C.9 H1/H4 decision, not an H3 one."

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
$EXT       = "pp-drill-$RunId-ext"
$REDEXT    = "pp-drill-$RunId-ext-red"
# The GREEN doors' database role. Per-run like every other resource here, because two
# concurrent runs share a docker daemon but must not share a role name in a database
# neither of them can see - and because a constant name is one step from a name that
# outlives its throwaway.
$APPUSER   = ("ob_app_drill_" + ($RunId -replace '[^a-z0-9]', ''))
$KEY       = "drill-brain-key-not-a-secret-$RunId"
$OPSKEY    = "drill-ops-key-not-a-secret-$RunId"
$IMAGE     = "openbrain-mcp-server:drill-$RunId"
$REDIMAGE  = "openbrain-mcp-server:drill-red-$RunId"
$EXTIMAGE  = "openbrain-ext-server:drill-$RunId"
$REDEXTIMG = "openbrain-ext-server:drill-red-$RunId"
$GWIMAGE   = "openbrain-gateway:drill-$RunId"
# ATTACK 14's lane: the PostgREST door and the path-stripping proxy the wiki compiler
# actually speaks to. Named per run like everything else; the PostgREST container also takes
# the NETWORK ALIAS openbrain-postgrest, because the repo's own Caddyfile names that host -
# using the real Caddyfile is what makes this the deployed path and not a lookalike.
$PGRST     = "pp-drill-$RunId-pgrest"
$RESTPROXY = "pp-drill-$RunId-rest"

# --- THE TREE UNDER TEST ------------------------------------------------------------------
#
# THE DRILL USED TO BUILD FROM THE ON-DISK WORKING COPY, and that is how the previous round
# was green about a tree that did not merge. Round four's entire fix lived in an OB1 commit
# that was never `git add OB1`'d, so the parent branch still pinned the commit BEFORE it. A
# verifier built from what the branch ACTUALLY PINS and reproduced the full leak, against a
# drill that had just reported 100 checks passing.
#
# So the drill builds from the RECORDED GITLINK - the exact submodule commit `git add OB1`
# put in the parent's tree - exported to a scratch directory. What it proves is what a merge
# would ship, which is the only thing worth proving.
#
# AND THE WORKING COPY MUST AGREE, or the run FAILS before it starts. Building from the
# gitlink alone would make a divergence silent in the other direction: the operator's
# uncommitted edits would simply not be under test, and a PASS would describe code nobody
# had. There is deliberately no override switch - "let me run it dirty just this once" is
# how the gate stops meaning anything.
$OB1WORK   = Join-Path $root "OB1"
$OB1       = Join-Path $env:TEMP "pp-drill-ob1-$RunId"

Write-Host "`n=== the tree under test - the RECORDED GITLINK, not the working copy ===" -ForegroundColor Cyan
$linkLine = (& git -C $root ls-tree HEAD OB1 2>&1 | Out-String).Trim()
$GITLINK = ""
if ($linkLine -match '^160000 commit ([0-9a-f]{40})') { $GITLINK = $Matches[1] }
if (-not $GITLINK) {
    Write-Host "  FAIL  could not read the OB1 gitlink from the parent tree: $linkLine" -ForegroundColor Red
    exit 1
}
$obHead = (& git -C $OB1WORK rev-parse HEAD 2>&1 | Out-String).Trim()
$obDirty = (& git -C $OB1WORK status --porcelain 2>&1 | Out-String).Trim()
Write-Host "        gitlink  $GITLINK"
Write-Host "        OB1 HEAD $obHead"
if ($obHead -ne $GITLINK) {
    Write-Host "  FAIL  the OB1 working copy is at $obHead but the parent pins $GITLINK." -ForegroundColor Red
    Write-Host "        Commit in OB1, push it, then 'git add OB1' in the parent. A drill that" -ForegroundColor Red
    Write-Host "        builds from an unpinned tree is green about code that does not merge." -ForegroundColor Red
    exit 1
}
if ($obDirty) {
    Write-Host "  FAIL  the OB1 working copy has uncommitted changes, so it is not what the" -ForegroundColor Red
    Write-Host "        gitlink names and this drill would not be testing it:" -ForegroundColor Red
    Write-Host $obDirty -ForegroundColor Red
    exit 1
}
Remove-Item $OB1 -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $OB1 -Force | Out-Null
$OB1ZIP = Join-Path $env:TEMP "pp-drill-ob1-$RunId.zip"
Remove-Item $OB1ZIP -Force -ErrorAction SilentlyContinue
& git -C $OB1WORK archive --format=zip -o $OB1ZIP $GITLINK 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $OB1ZIP)) {
    Write-Host "  FAIL  could not export OB1 $GITLINK" -ForegroundColor Red
    exit 1
}
Expand-Archive -Path $OB1ZIP -DestinationPath $OB1 -Force
Remove-Item $OB1ZIP -Force -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $OB1 "integrations\kubernetes-deployment\index.ts"))) {
    Write-Host "  FAIL  the exported gitlink tree has no openbrain-mcp source" -ForegroundColor Red
    exit 1
}
Write-Host "  PASS  working copy == gitlink == $GITLINK; exported to $OB1" -ForegroundColor Green

$SRC       = Join-Path $OB1 "integrations\kubernetes-deployment"
$EXTSRC    = Join-Path $OB1 "docker\extensions-server"
$INITDIR   = Join-Path $env:TEMP "pp-drill-initdb-$RunId"
$STUBPATH  = Join-Path $env:TEMP "pp-drill-embed-$RunId.ts"
$REDSRCDIR = Join-Path $env:TEMP "pp-drill-red-src-$RunId"
$REDEXTDIR = Join-Path $env:TEMP "pp-drill-red-ext-$RunId"

# The synthetic fixture. Unique per run, so a stale row can never be mistaken for this one,
# and worded so anyone who finds it in a log knows immediately that it is not real.
# The tenant every personal-plane fixture is stamped with. It is openbrain-ext's
# DEFAULT_USER_ID as well (ATTACK 13), so one constant serves both and the personal fixture
# is a row that a real personal-plane caller would own rather than an orphan nobody can see.
$EXTUSER   = "00000000-0000-4000-8000-0000000000ff"
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
$LEGACY    = "SYNTHETIC LEGACY CORPUS ROW $MARKER - a personal-plane thought as the PRE-GUARD mirror would have written it"
$SUMPERS   = "synthetic personal fixture $MARKER"
$SUMOPS    = "synthetic ops control $MARKER"

function Remove-DrillStack {
    # THIS RUN'S RESOURCES ONLY. The previous version force-removed a constant set of names
    # at startup, so starting a second run ripped the first one's containers out from under
    # it mid-fixture.
    docker rm -f $RESTPROXY $PGRST $REDEXT $EXT $REDOPSMEM $REDOPS $REDSRV $CLOUD $OPS $SRV $STUB $DB 2>$null | Out-Null
    docker network rm $NET 2>$null | Out-Null
}
function Remove-DrillImages {
    docker rmi -f $IMAGE $REDIMAGE $GWIMAGE $EXTIMAGE $REDEXTIMG 2>$null | Out-Null
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
if ($ExtPort    -le 0) { $ExtPort    = Get-FreePort }
if ($RedExtPort -le 0) { $RedExtPort = Get-FreePort }

# -q as well as -tA: without it psql appends the command tag ("INSERT 0 1") to the output,
# so a `... RETURNING id` came back as a uuid with a status line stapled to it and every
# later query built from it died on "invalid input syntax for type uuid".
# The same, but as a NAMED ROLE rather than as the superuser. Every claim about the
# boundary is a claim about a non-superuser connection, and `Db` is a superuser one; using
# it to "check the boundary" would measure nothing at all. SET ROLE inside an explicit
# transaction so it cannot leak into the next call on this connection.
function Db-AsRole {
    param([Parameter(Mandatory)][string]$Role, [Parameter(Mandatory)][string]$Sql)
    $wrapped = "BEGIN; SET LOCAL ROLE $Role; $Sql; COMMIT;"
    $out = Db $wrapped
    return (($out -split "`n") | ForEach-Object { $_.Trim() } |
            Where-Object { $_ -ne "" -and $_ -notmatch '^(SET|BEGIN|COMMIT|ROLLBACK|RESET)$' }) -join "`n"
}

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
    param([int]$Port, [string]$Method, $Params, [string]$Key = $OPSKEY, [switch]$RawBrainKey)
    $msg = @{ jsonrpc = "2.0"; id = 1; method = $Method }
    if ($null -ne $Params) { $msg["params"] = $Params }
    # THE RAW DOOR AUTHENTICATES DIFFERENTLY, and that difference is the point of ATTACK 11.
    # A gateway takes `Authorization: Bearer <gateway key>` and applies a profile; the MCP
    # server itself takes `x-brain-key: <MCP_ACCESS_KEY>` and applies no profile at all.
    # The second is the credential openbrain-mcpo holds.
    $hdrs = @{ "Accept" = "application/json, text/event-stream" }
    if ($RawBrainKey) { $hdrs["x-brain-key"] = $Key } else { $hdrs["Authorization"] = "Bearer $Key" }
    $txt = ""
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method POST `
             -Headers $hdrs `
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

# The same call at the RAW door - no gateway, no allow-list, no forced filter. This is the
# position openbrain-mcpo, and therefore Open WebUI, occupies.
function Invoke-RawTool {
    param([int]$Port, [string]$Name, [hashtable]$Arguments, [string]$Key = $KEY)
    return Invoke-Mcp -Port $Port -Method "tools/call" `
        -Params @{ name = $Name; arguments = $Arguments } -Key $Key -RawBrainKey
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
    # -DbUser IS THE WHOLE RE-ANCHOR OF THIS DRILL, so it is a parameter rather than a
    # constant. The boundary being attacked is a set of ROW-LEVEL SECURITY POLICIES, and
    # "Superusers and roles with the BYPASSRLS attribute always bypass the row security
    # system" - FORCE included. A door connected as `postgres` is therefore not bound by
    # anything this drill is testing, and every attack against it measures the absence of an
    # application-layer guard that amendment A2 deliberately RETIRED.
    #   GREEN doors run as $APPUSER  - a non-superuser, exactly what the boundary claims to bind.
    #   RED   doors run as postgres  - which is what PRODUCTION does today (C.9 H1: 22 of 22
    #                                  live connections are postgres). The red is not a
    #                                  hypothetical; it is the deployed configuration.
    param([string]$Name, [int]$Port, [string]$Img, [string]$DbUser = $APPUSER)
    $a = @("run", "-d", "--name", $Name, "--network", $NET, "-p", "127.0.0.1:${Port}:8000",
           "-e", "DB_HOST=$DB", "-e", "DB_PORT=5432", "-e", "DB_NAME=openbrain",
           "-e", "DB_USER=$DbUser", "-e", "DB_PASSWORD=test", "-e", "MCP_ACCESS_KEY=$KEY",
           "-e", "PORT=8000", "-e", "EMBEDDING_API_BASE=http://${STUB}:8080",
           "-e", "EMBEDDING_API_KEY=stub", "-e", "EMBEDDING_MODEL=stub-embed", $Img)
    Invoke-DockerOrThrow -DockerArgs $a -What "start openbrain-mcp $Name on :$Port (db user $DbUser)" | Out-Null
}

try {
    # --- 1. the throwaway plane ---------------------------------------------------------
    Section "an isolated plane - no live container, no real memory, ever (run $RunId)"
    Note "workspace=$WS  ports srv=$ServerPort ops=$OpsPort cloud=$CloudPort red=$RedSrvPort/$RedOpsPort/$RedMemPort"
    Invoke-DockerOrThrow -DockerArgs @("network", "create", $NET) -What "create network $NET" | Out-Null
    $chain = Get-ObInitChain -ComposePath (Join-Path $OB1 "docker\docker-compose.yml")
    if ($chain.Count -lt 1) { Fail "could not parse the initdb chain from compose"; throw "no chain" }
    $staged = Copy-ObInitChain -Chain $chain -SourceDir (Join-Path $OB1 "docker") -TargetDir $INITDIR
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

    # --- 1b. THE APPLICATION ROLE - a NON-SUPERUSER door, which is the only kind the -----
    #          boundary can bind at all.
    #
    # WHY THIS ROLE EXISTS AND WHAT IT IS NOT. C.9 item H1 records that all 22 live
    # connections to openbrain-db are `postgres` (rolsuper, bypassrls), and H1's job is to
    # move the data-plane ones onto a dedicated non-superuser role. THIS DRILL DOES NOT DO
    # H1 AND DOES NOT DECIDE ITS ROLE. It creates a stand-in, inside its own throwaway, so
    # that the attacks below run against the layer the design actually enforces at. What the
    # drill can then say is precise: the boundary HOLDS for a non-superuser door, and
    # production's doors are not one yet. The RED phase runs the same doors as `postgres`
    # and shows exactly what that costs - which makes this drill H1's executable evidence
    # rather than a claim about a fix nobody has made.
    #
    # THE GRANTS ARE DERIVED FROM WHAT THE SERVER DOES, not copied from postgres. It
    # inherits service_role (the access class every PostgREST caller already runs as), and
    # then gets back the writes 200 section 6a withdrew from service_role - because this
    # role IS the writer that section named as connecting as postgres. Nothing else is
    # added: if the server needs a privilege that is not here, it fails loudly rather than
    # silently running elevated.
    #
    # AND ONE DEVIATION FROM THE SHIPPED SCHEMA, STATED LOUDLY BECAUSE IT IS A FINDING.
    # 200-init-graph-plane-rls.sql section 2b CLOSES `agent_memory_audit_events` to
    # service_role with `USING (false) WITH CHECK (false)`, on the stated reasoning that
    # "THE WRITER IS A SUPERUSER TOO: openbrain-mcp runs DB_USER=postgres". That reasoning is
    # exactly what C.9 H1 is going to remove. A non-superuser writer cannot write its own
    # audit row under that policy, so `agent_memory_writeback` - which inserts thought,
    # memory and audit event in ONE transaction - fails entirely, and the ops lane does not
    # come up at all. Measured here first, by this drill failing to plant its OPS control.
    #
    # The drill therefore adds an INSERT-ONLY policy for its own role, so the ops lane can
    # run and the READ attacks below can be about reading. The shipped read policy is NOT
    # touched and is asserted below to still be `false`; H1 has to decide the real fix
    # (a narrow FOR INSERT policy, or a writer that keeps its elevation). See
    # documentation/notes/u8h3-findings.md.
    $null = Db @"
CREATE ROLE $APPUSER LOGIN PASSWORD 'test' NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;
GRANT service_role TO $APPUSER;
GRANT INSERT, UPDATE, DELETE ON
  public.agent_memories, public.idea_revisions,
  public.agent_memory_audit_events, public.agent_memory_review_actions,
  public.agent_memory_recall_traces, public.agent_memory_recall_items,
  public.agent_memory_source_refs, public.agent_memory_artifacts,
  public.agent_memory_relations
  TO $APPUSER;
CREATE POLICY drill_audit_write ON public.agent_memory_audit_events
  FOR INSERT TO $APPUSER WITH CHECK (true);
-- NO workaround policy on agent_memory_recall_traces, deliberately: the DEFECT THAT
-- REQUIRED ONE WAS FIXED instead. `ob_trace_on_ops_plane(request_payload)` reads
-- request_payload->'enforced_exposure' and performRecall never wrote that key, so every
-- recall by a non-superuser failed 42501 on its own trace insert - on the RETURNING clause
-- specifically, because RETURNING makes Postgres apply the SELECT policy to the new row as
-- well as the WITH CHECK. Found by running this drill against a non-superuser door; fixed in
-- agent-memory.ts with two regression tests. The shipped policy now holds unmodified here,
-- which is why there is nothing to add.
"@
    $auditRead = Db "SELECT COALESCE(qual,'-') FROM pg_policies WHERE tablename='agent_memory_audit_events' AND policyname='agent_memory_audit_events_closed'"
    if ($auditRead -eq "false") {
        Pass "the shipped audit-events READ policy is untouched (USING $auditRead) - the drill added an INSERT-only policy so the writer can run, nothing more"
    } else {
        Fail "the shipped audit-events read policy is '$auditRead', expected 'false' - the drill has changed what it is measuring"
    }
    $revoked = Db "SELECT count(*) FROM information_schema.tables t WHERE t.table_schema='public' AND t.table_name LIKE 'agent_memor%' AND t.table_type='BASE TABLE' AND NOT has_table_privilege('service_role', 'public.'||t.table_name, 'INSERT')"
    $traceOk = Db "SELECT public.ob_trace_on_ops_plane(jsonb_build_object('enforced_exposure', jsonb_build_array('ops')))::text"
    if ($traceOk -eq "true") { Pass "the recall-trace policy admits an ops-plane trace payload - the recall lane can run as a non-superuser at all" }
    else { Fail "ob_trace_on_ops_plane rejects an ops trace payload ('$traceOk') - the recall lane cannot run and every recall attack below would be vacuous" }
    Note "FINDING: 200 section 6a withdrew INSERT/UPDATE/DELETE from service_role on $revoked of the agent-memory tables, and 2b closed agent_memory_audit_events entirely - both on the recorded reasoning that the writer connects as postgres. A NON-superuser writer therefore cannot run the writeback, the recall trace or the review door at all. C.9 H1 has to decide this; this drill grants them back in its own throwaway ONLY, and leaves every READ policy exactly as shipped."

    $roleState = Db "SELECT rolsuper::text || '/' || rolbypassrls::text FROM pg_roles WHERE rolname = '$APPUSER'"
    if ($roleState -eq "false/false") {
        Pass "the GREEN doors' database role $APPUSER exists and is NEITHER superuser NOR bypassrls ($roleState) - the policies can bind it"
    } else {
        Fail "$APPUSER is '$roleState', expected 'false/false' - a door the boundary cannot bind proves nothing"
        throw "app role"
    }
    Note "PRODUCTION does not use such a role yet: C.9 H1 measured 22 of 22 live connections as postgres. The RED phase runs these same doors as postgres."

    # A stub embedding endpoint: this drill is about a boundary, not about the GPU plane.
    # THE CHAT STUB ECHOES ITS PROMPT BACK AS THE WIKI BODY, and that is the whole trick
    # behind ATTACK 14. A real model would paraphrase, so an assertion that a fixture string
    # is absent from the page would pass for the wrong reason - the model simply did not use
    # it. Echoing makes the page a faithful transcript of WHAT THE COMPILER SENT: if corpus
    # content reached the model, it is in the output, verbatim.
    $stubLines = @(
        'Deno.serve({ port: 8080 }, async (req) => {',
        '  if (req.url.includes("/embeddings")) {',
        '    return Response.json({ data: [{ embedding: Array(1024).fill(0.001) }] });',
        '  }',
        '  if (req.url.includes("/chat/completions")) {',
        '    const b = await req.json();',
        '    const user = (b.messages || []).filter((m) => m.role === "user")',
        '      .map((m) => m.content).join("\n");',
        '    return Response.json({',
        '      choices: [{ message: { role: "assistant", content: "# Echo\n\n" + user } }],',
        '    });',
        '  }',
        '  return new Response("no", { status: 404 });',
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

    $compose = Join-Path $OB1 "docker\docker-compose.yml"
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
    # THE PERSONAL FIXTURE CANNOT BE WRITTEN THROUGH THIS DOOR ANY MORE, AND THAT IS A
    # RESULT, NOT AN OBSTACLE.
    #
    # This drill used to plant its personal fixture by calling the ops door's own writeback
    # with tainted=true - the documented mechanical demotion - because the door could mint a
    # personal memory and the READERS were expected to hide it afterwards. Under the
    # database boundary that is no longer possible: `agent_memories_ops_plane`'s WITH CHECK
    # is `ob_memory_on_ops_plane(exposure)`, so an ops-plane connection is refused when it
    # tries to write exposure='personal'. That is memory-plane PLAN 1.1's "access bounds
    # writes" stated as a constraint, and this is the end-to-end proof of it: the same HTTP
    # call that used to succeed now fails, at the database, through the real write path.
    #
    # So the attempt is made and ASSERTED TO FAIL, with the ops write beside it as the live
    # control - and the personal fixture is then planted DIRECTLY, which is what a
    # personal-plane context would have written and is the only thing the read attacks below
    # actually need.
    $refused = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = $WS; project_id = $PROJ
        summary = $SUMPERS; content = $PERSONAL
        memory_type = "lesson"; tainted = $true; idempotency_key = "$MARKER-personal"
    }
    $control = Invoke-Rest -Port $ServerPort -Path "/agent-memory/writeback" -Body @{
        workspace_id = $WS; project_id = $PROJ
        summary = $SUMOPS; content = $OPSCTRL
        memory_type = "lesson"; idempotency_key = "$MARKER-ops"
    }
    if ($control.Status -eq 200) { Pass "CONTROL: the ops-plane writeback SUCCEEDS through the door - the lane works" }
    else { Fail "the ops-plane writeback failed ($($control.Status)) - nothing below is trustworthy"; throw "no fixture" }
    if ($refused.Status -ne 200) {
        Pass "ACCESS BOUNDS WRITES: the ops door is REFUSED when it tries to mint a personal memory (HTTP $($refused.Status)) - PLAN 1.1 as a constraint, not a convention"
    } else {
        Fail "the ops door MINTED a personal-plane memory through the real write path - access does not bound writes"
    }
    $mintedPers = Db "SELECT count(*) FROM agent_memories WHERE workspace_id = '$WS' AND exposure = 'personal'"
    if ($mintedPers -eq "0") { Pass "and nothing landed: 0 personal rows from that attempt" }
    else { Fail "$mintedPers personal memory/memories were written by the ops door" }

    $PID_OPS  = $control.Body.memory_id
    # The personal fixture, planted as the personal-plane context would write it: the memory,
    # its mirrored thought, and the tenancy stamp that makes it SOMEBODY's row rather than an
    # orphan. Direct SQL, as the superuser, because there is no personal-plane door in this
    # stack to write it through - which is itself worth stating rather than hiding behind a
    # helper.
    $PERSTID = Db "INSERT INTO thoughts (content, embedding, metadata, exposure, user_id) VALUES ('$PERSONAL', array_fill(0.001::real, ARRAY[1024])::vector, jsonb_build_object('source','agent-memory','workspace_id','$WS','exposure','personal'), 'personal', '$EXTUSER') RETURNING id"
    $PID_PERS = Db "INSERT INTO agent_memories (thought_id, workspace_id, project_id, summary, content, memory_type, visibility, review_status, lifecycle_status, provenance_status, metadata, exposure, user_id) VALUES ($PERSTID, '$WS', '$PROJ', '$SUMPERS', '$PERSONAL', 'lesson', 'project', 'pending', 'active', 'generated', jsonb_build_object('exposure','personal'), 'personal', '$EXTUSER') RETURNING id"
    if ($PID_PERS -match '^[0-9a-f-]{36}$') { Pass "the personal fixture is planted directly (memory $PID_PERS, mirrored thought $PERSTID)" }
    else { Fail "could not plant the personal fixture: $PID_PERS"; throw "no fixture" }

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
    # `exposure` is stated because it is a NOT NULL column with no default (DFU C.9 H3).
    # 'ops': this is the CLOUD-lane control, and the cloud lane is a `share` label on
    # ops-plane content - the two axes are independent, which is exactly what this control
    # exists to keep separable. Omitting the column would fail the INSERT, not produce an
    # unlabelled row.
    $null = Db "INSERT INTO thoughts (content, embedding, metadata, exposure) VALUES ('$CLOUDCTRL', array_fill(0.001::real, ARRAY[1024])::vector, jsonb_build_object('share','cloud','source','drill-cloud-control','exposure','ops'), 'ops')"
    $cloudPlanted = Db "SELECT count(*) FROM thoughts WHERE metadata->>'share'='cloud' AND content LIKE '%$MARKER%'"
    if ($cloudPlanted -eq "1") { Pass "a cloud-labelled control thought is planted (share=cloud)" }
    else { Fail "could not plant the cloud control thought (got '$cloudPlanted')"; throw "no cloud control" }

    # THE MIRROR ASSERTION INVERTED WITH AMENDMENT A2, AND THE INVERSION IS THE WHOLE POINT.
    #
    # THIS BLOCK USED TO SAY: exactly ONE mirrored thought exists and it is the ops one -
    # "STOPPED AT THE WRITE - the personal fixture put NOTHING in the shared corpus". That
    # was correct FOR ITS ERA. `thoughts` had RLS switched off entirely, index.ts had six
    # `FROM thoughts` statements with no exposure predicate in any of them, and the only
    # available fix was to refuse to mirror personal content at all
    # (mirrorsToUnifiedSearch). Containment by not writing.
    #
    # A2 (2026-08-30) moved enforcement into the database, so `thoughts` is now RLS-governed
    # and FORCE-d, and 195 made its plane a NOT NULL CHECKed column. The mirror is therefore
    # written for BOTH planes again - deliberately, because a memory whose content is not in
    # the corpus is a memory the corpus cannot retrieve, and containment bought by making
    # the personal plane unrecallable is not containment, it is an outage.
    #
    # So the claim changes from "it was not written" to "it was written and it is BOUND",
    # and both halves are asserted, because either alone passes while proving nothing:
    #   (1) the personal mirror EXISTS - so the check below has a subject;
    #   (2) the app role cannot READ it, while it CAN read the ops mirror in the same query.
    # Deleting this block instead would have quietly dropped the corpus half of the
    # boundary from the drill's coverage, which is the failure mode section 12's coverage
    # gate exists to catch one layer up.
    $mirrored = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%'"
    $mirrorPers = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%' AND exposure='personal'"
    $mirrorOps = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%' AND exposure='ops'"
    $mirrorShared = Db "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%' AND metadata->>'share' IS NOT NULL"
    if ($mirrorPers -eq "1" -and $mirrorOps -eq "1" -and $mirrored -eq "2") {
        Pass "both memories mirrored into the corpus (personal=$mirrorPers ops=$mirrorOps) - the corpus half of the boundary has a subject to be tested on"
    } else {
        Fail "expected one mirrored thought per plane, got total='$mirrored' ops='$mirrorOps' personal='$mirrorPers'"
    }
    # (2) AND THE MIRROR IS BOUND. Same query, same role, one result: the ops row. This is
    # the assertion that replaces "it was never written", and it is stronger, because it is
    # a property of the store rather than of one writer's restraint.
    $corpusSeen = Db-AsRole -Role "$APPUSER" -Sql "SELECT count(*) FROM thoughts WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%'"
    $corpusPers = Db-AsRole -Role "$APPUSER" -Sql "SELECT count(*) FROM thoughts WHERE content LIKE '%$MARKER%' AND content LIKE '%personal-plane FIXTURE%'"
    if ($corpusPers -eq "0" -and $corpusSeen -eq "1") {
        Pass "and the app role reads the OPS mirror and NOT the personal one (visible=$corpusSeen personal=$corpusPers) - written, and bound"
    } else {
        Fail "the app role sees visible=$corpusSeen personal=$corpusPers - expected 1 and 0"
    }
    # The memory rows point at their mirrors: the ops one so recall works, the personal one
    # because a memory with no thought is a memory recall cannot rank.
    $persTid = Db "SELECT COALESCE(thought_id::text,'null') FROM agent_memories WHERE id = '$PID_PERS'"
    $opsTid = Db "SELECT COALESCE(thought_id::text,'null') FROM agent_memories WHERE id = '$PID_OPS'"
    if ($persTid -ne "null" -and $opsTid -ne "null") { Pass "both memories point at their mirrored thoughts (personal $persTid, ops $opsTid) - neither plane is contained by being unrecallable" }
    else { Fail "a memory lost its mirror (personal='$persTid' ops='$opsTid')" }
    if ($mirrorShared -eq "0") { Pass "no mirrored thought carries a 'share' label - the cloud filter's premise holds in the data" }
    else { Fail "$mirrorShared mirrored thought(s) carry a 'share' key - the cloud door's exclusion is not what the comment says" }

    # A recall TRACE that names the personal memory, so agent_memory_recall_trace has
    # something off-plane to be attacked with. Planted rather than harvested from the red
    # phase, so this attack still runs under -SkipRed.
    #
    # TWO traces, because a trace has TWO plane-sensitive parts and they fail differently.
    # The ITEMS are dropped per row (ATTACK 5). The ENVELOPE - which carries the recall's
    # QUERY TEXT and its whole request payload - is bounded now too (ATTACK 5b), and it was
    # not: performRecallTrace read the trace row by id with no predicate at all, which the
    # derived completeness gate found once it stopped looking only for `agent_memories`.
    #
    # `enforced_exposure` is what a real recall writes into request_payload
    # (decideRecallExposure -> performRecall), and it is a LIST, which is why the trace
    # predicate is jsonb containment rather than equality.
    $TRACE = Db "INSERT INTO agent_memory_recall_traces (workspace_id, project_id, query, schema_version, request_payload, response_policy) VALUES ('$WS', '$PROJ', '$MARKER', 'drill', jsonb_build_object('enforced_exposure', jsonb_build_array('ops')), '{}'::jsonb) RETURNING id"
    $null = Db "INSERT INTO agent_memory_recall_items (trace_id, memory_id, rank, similarity) VALUES ('$TRACE', '$PID_PERS', 1, 0.9), ('$TRACE', '$PID_OPS', 2, 0.8)"
    $items = Db "SELECT count(*) FROM agent_memory_recall_items WHERE trace_id = '$TRACE'"
    if ($items -eq "2") { Pass "an OPS-plane recall trace naming BOTH memories is planted (trace $TRACE)" }
    else { Fail "could not plant the recall-trace fixture (got '$items')"; throw "no trace fixture" }
    $PTRACE = Db "INSERT INTO agent_memory_recall_traces (workspace_id, project_id, query, schema_version, request_payload, response_policy) VALUES ('$WS', '$PROJ', 'personal-plane query $MARKER', 'drill', jsonb_build_object('enforced_exposure', jsonb_build_array('personal')), '{}'::jsonb) RETURNING id"
    $null = Db "INSERT INTO agent_memory_recall_items (trace_id, memory_id, rank, similarity) VALUES ('$PTRACE', '$PID_PERS', 1, 0.95)"
    $pItems = Db "SELECT count(*) FROM agent_memory_recall_items WHERE trace_id = '$PTRACE'"
    if ($pItems -eq "1") { Pass "a PERSONAL-plane recall trace is planted (trace $PTRACE)" }
    else { Fail "could not plant the personal recall-trace fixture (got '$pItems')"; throw "no personal trace fixture" }

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
    else { Gap "STOPPED but NOT RECORDED (agent_memory_inspect): expected 1 access_refused row, got '$refused'"
           Note $AUDIT_GAP }
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
    else { Gap "STOPPED but NOT RECORDED (agent_memory_recall_trace): expected 1 access_refused row, got '$trcRefused'" }

    # --- 8b. ATTACK 5b: THE ENVELOPE, not the items -------------------------------------
    Section "ATTACK 5b - the agent asks for a PERSONAL-plane trace's envelope (its query text)"
    # The items were dropped correctly. The trace ROW was not bounded at all: it carries the
    # recall's QUERY TEXT and its whole request payload, so an ops-door caller holding a
    # trace id learned what a personal-plane agent went looking for. Nobody attacked it
    # because the previous completeness gate had a one-word vocabulary - it enumerated
    # `agent_memories` and nothing else, so this statement, against
    # `agent_memory_recall_traces`, was invisible to it.
    $ptrc = Invoke-Tool -Port $OpsPort -Name "agent_memory_recall_trace" -Arguments @{ trace_id = $PTRACE }
    $ptrcBlob = ($ptrc | ConvertTo-Json -Depth 12 -Compress)
    if ($ptrcBlob -notmatch "personal-plane query" -and $ptrcBlob -notmatch [regex]::Escape($PID_PERS)) {
        Pass "STOPPED - the personal-plane trace discloses neither its query text nor the memory it named"
    } else { Fail "EXPOSURE LEAK: the personal-plane trace envelope came back"; Note $ptrcBlob }
    $ptrcRefused = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND payload->>'reason'='off-plane-trace'"
    if ($ptrcRefused -eq "1") { Pass "the refused trace left a durable audit row (access_refused, reason=off-plane-trace)" }
    else { Gap "STOPPED but NOT RECORDED (recall_trace envelope): expected 1 refusal row, got '$ptrcRefused'" }
    $ptrcNamed = Db "SELECT count(*) FROM agent_memory_audit_events WHERE payload->>'reason'='off-plane-trace' AND memory_id IS NOT NULL"
    if ($ptrcNamed -eq "0") { Pass "and that row names NO memory id - a trace refusal must not leak the id it was hiding" }
    else { Fail "the trace refusal row carries a memory id" }

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
    } else { Gap "STOPPED but NOT RECORDED (ops door allow-list): a tool denied by the GATEWAY writes no audit row. Different cause from the others - the gateway refuses before the server is reached, so there is no database session to record from. Closing it is a gateway change, not a boundary one." }

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
    } else { Gap "STOPPED but NOT RECORDED (cloud door allow-list): same cause as the ops door's - the gateway denies before any database session exists." }

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

    # --- 10a. ATTACK 11: THE RAW MCP DOOR - no gateway, no allow-list, no filter ---------
    Section "ATTACK 11 - the agent uses the RAW openbrain-mcp door (the one openbrain-mcpo holds)"
    # THE DOOR THIS DRILL ALLOCATED A PORT FOR AND NEVER CALLED A TOOL ON.
    #
    # Every attack above is on a GATEWAY. A gateway has an allow-list and a forced read
    # filter, and both of them stopped things - which proved the gateway, not the server.
    # OB1/docker/mcpo.config.json points `openbrain-mcpo` at http://openbrain-mcp:8000 with
    # the raw MCP_ACCESS_KEY, so Open WebUI's Open Brain tools speak to THIS surface, where
    # neither guard exists. The cloud gateway's own docstring says local clients bypass it
    # by design.
    #
    # And this is where the second home was reachable. `performWriteback` used to mirror the
    # memory's full content into `thoughts`; index.ts's six `FROM thoughts` statements have
    # no exposure predicate; so list_thoughts and search_thoughts returned personal-plane
    # content verbatim, wrote no audit row, and never touched agent_memory_* at all. The fix
    # is at the write - the content is not in the corpus to be found.
    Add-AttackedTool "list_thoughts" "ATTACK 11"
    Add-AttackedTool "search_thoughts" "ATTACK 11"

    $rawList = Invoke-RawTool -Port $ServerPort -Name "list_thoughts" -Arguments @{ limit = 50 }
    $rawListBlob = ($rawList | ConvertTo-Json -Depth 12 -Compress)
    if ($rawListBlob -match [regex]::Escape($OPSCTRL)) {
        Pass "list_thoughts at the RAW door returns the ops control - the lane works, so this attack is not vacuous"
        if ($rawListBlob -notmatch [regex]::Escape($PERSONAL)) {
            Pass "STOPPED - the personal fixture's content is NOT in the corpus listing"
        } else { Fail "EXPOSURE LEAK at the RAW door: list_thoughts returned the personal fixture's content" }
    } else { Fail "list_thoughts returned no ops control - the call failed, so this attack proves nothing"; Note $rawListBlob }

    $rawSearch = Invoke-RawTool -Port $ServerPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25; threshold = 0.0 }
    $rawSearchBlob = ($rawSearch | ConvertTo-Json -Depth 12 -Compress)
    if ($rawSearchBlob -notmatch [regex]::Escape($PERSONAL)) {
        Pass "STOPPED - search_thoughts at the RAW door does not return the personal fixture"
    } else { Fail "EXPOSURE LEAK at the RAW door: search_thoughts returned the personal fixture's content" }
    # Not asserting an audit row here, and saying so: NOTHING was refused. The corpus tools
    # ran normally and found nothing, because there is nothing of the personal plane in the
    # corpus. That is the shape of a boundary at the write - there is no denied request to
    # record, and a store that never held the content needs no guard on its readers.
    Note "by design: no audit row - the corpus tools were not refused, they simply had nothing to return"

    # And the id oracle at the same door: `fetch` takes a thought id.
    #
    # THIS ASSERTION INVERTED WITH A2, and the inversion is the interesting part. It used to
    # read "there is no personal-plane thought id for fetch to be pointed at" - true when the
    # fix was to refuse to mirror personal content at all. The mirror is back (see the
    # fixture section), so the id EXISTS, and the claim has to become the stronger one: it
    # exists, it is named, and the door still cannot read it - with the ops mirror fetched
    # by the same tool in the same breath, or "not returned" would be indistinguishable from
    # "fetch is broken".
    $persThought = Db "SELECT COALESCE(max(id)::text,'none') FROM thoughts WHERE content LIKE '%$MARKER%' AND exposure='personal'"
    $opsThought  = Db "SELECT COALESCE(max(id)::text,'none') FROM thoughts WHERE content LIKE '%$MARKER%' AND exposure='ops' AND metadata->>'source'='agent-memory'"
    if ($persThought -eq "none" -or $opsThought -eq "none") {
        Fail "the fetch fixtures are missing (personal='$persThought' ops='$opsThought') - this attack would prove nothing"
    } else {
        $fOps  = (Invoke-RawTool -Port $ServerPort -Name "fetch" -Arguments @{ id = "$opsThought" } | ConvertTo-Json -Depth 12 -Compress)
        $fPers = (Invoke-RawTool -Port $ServerPort -Name "fetch" -Arguments @{ id = "$persThought" } | ConvertTo-Json -Depth 12 -Compress)
        if ($fOps -match "SYNTHETIC ops-plane CONTROL") { Pass "fetch at the RAW door returns the OPS mirror by id - the tool works" }
        else { Fail "fetch could not return the ops mirror either - the refusal below proves nothing"; Note $fOps }
        if ($fPers -notmatch [regex]::Escape($PERSONAL)) { Pass "STOPPED - fetch by id does not return the PERSONAL mirror's content (thought $persThought)" }
        else { Fail "EXPOSURE LEAK at the RAW door: fetch returned the personal mirror by id"; Note $fPers }
    }
    Add-AttackedTool "fetch" "ATTACK 11" 

    # --- 10a-ii. ATTACK 12: A ROW THAT IS ALREADY IN THE CORPUS --------------------------
    Section "ATTACK 12 - the personal content is ALREADY in the corpus (the mirror ran before the guard existed)"
    # WHY THE WRITE GUARD IS NOT THE WHOLE BOUNDARY, AS AN ATTACK RATHER THAN AN ARGUMENT.
    #
    # ATTACK 11 above proves that personal-plane content never ENTERS the corpus. That is a
    # property of writes made from now on. It says nothing about a row that is already there,
    # and rows are already there: the mirror SHIPPED AND RAN before the guard was written -
    # production `thoughts` carries four rows labelled `ops` today - so a plane that had been
    # used before the guard landed would have left personal-labelled rows behind that nothing
    # filtered. It also says nothing about the next writer of that table, and `thoughts` is
    # written by capture_thought, by the idea inlet and by importers.
    #
    # So this plants exactly that: a personal-labelled corpus row, written DIRECTLY to the
    # database the way the pre-guard mirror wrote it, and then fires every corpus reader the
    # raw door exposes at it. jsonb_build_object rather than a JSON literal for the reason the
    # fixture block gives - PowerShell strips embedded double quotes on the way to psql.
    # The COLUMN carries the plane since DFU C.9 H3, and the jsonb mirror is written beside
    # it so this fixture is exactly what a compliant writer produces - the drill is attacking
    # the READ side here, and a fixture that disagreed with itself would make a hidden row
    # ambiguous between "the boundary held" and "the row was malformed".
    $legacyId = Db "INSERT INTO thoughts (content, embedding, metadata, exposure) VALUES ('$LEGACY', ('[' || 1 || repeat(',0', 1023) || ']')::vector, jsonb_build_object('exposure','personal'), 'personal') RETURNING id"
    if ($legacyId -match '^\d+$') {
        Pass "planted a LEGACY personal-labelled corpus row (thought id $legacyId) - the pre-guard mirror's output"
    } else { Fail "could not plant the legacy corpus row: $legacyId"; throw "no legacy row" }

    Add-AttackedTool "search" "ATTACK 12"
    Add-AttackedTool "fetch" "ATTACK 12"
    Add-AttackedTool "thought_stats" "ATTACK 12"

    # (a) the two tools the leak was originally demonstrated through.
    $l2 = ((Invoke-RawTool -Port $ServerPort -Name "list_thoughts" -Arguments @{ limit = 50 }) | ConvertTo-Json -Depth 12 -Compress)
    if ($l2 -match [regex]::Escape($OPSCTRL)) {
        if ($l2 -notmatch [regex]::Escape($LEGACY)) { Pass "STOPPED - list_thoughts does not return the legacy personal row" }
        else { Fail "EXPOSURE LEAK: list_thoughts returned a personal-labelled corpus row verbatim" }
    } else { Fail "list_thoughts returned no ops control - the lane is broken, this attack proves nothing"; Note $l2 }

    $s2 = ((Invoke-RawTool -Port $ServerPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25; threshold = 0.0 }) | ConvertTo-Json -Depth 12 -Compress)
    if ($s2 -match [regex]::Escape($OPSCTRL)) {
        if ($s2 -notmatch [regex]::Escape($LEGACY)) { Pass "STOPPED - search_thoughts does not return the legacy personal row" }
        else { Fail "EXPOSURE LEAK: search_thoughts returned a personal-labelled corpus row at 100% match" }
    } else { Fail "search_thoughts returned no ops control - this attack proves nothing"; Note $s2 }

    # (b) the ChatGPT-compatibility pair, which the previous rounds never called. `search`
    # returns a TITLE built from the row's content, so it leaks the first line even though it
    # never selects the content column.
    $c2 = ((Invoke-RawTool -Port $ServerPort -Name "search" -Arguments @{ query = $MARKER }) | ConvertTo-Json -Depth 12 -Compress)
    if ($c2 -notmatch [regex]::Escape("SYNTHETIC LEGACY CORPUS ROW $MARKER")) {
        Pass "STOPPED - the ChatGPT-compat search/fetch pair does not title the legacy row"
    } else { Fail "EXPOSURE LEAK: the compat search tool returned a title built from a personal-labelled row's content" }

    # (c) THE ID ORACLE. `thoughts` ids are sequential bigints, so guessing one is not work.
    $refBefore = [int](Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND payload->>'reason' = 'off-plane-corpus-row:$legacyId'")
    $f2 = ((Invoke-RawTool -Port $ServerPort -Name "fetch" -Arguments @{ id = "$legacyId" }) | ConvertTo-Json -Depth 12 -Compress)
    if ($f2 -notmatch [regex]::Escape($LEGACY)) {
        Pass "STOPPED - fetch by id does not return the legacy personal row's content"
    } else { Fail "EXPOSURE LEAK: fetch returned a personal-labelled corpus row by id" }
    if ($f2 -match "No thought found for ID") {
        Pass "REFUSED AS not_found - the answer is byte-identical to a never-issued id, so existence is not disclosed"
    } else { Fail "fetch did not answer with the absent-id message - the refusal is distinguishable"; Note $f2 }
    $refAfter = [int](Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused' AND payload->>'reason' = 'off-plane-corpus-row:$legacyId'")
    if ($refAfter -gt $refBefore) {
        Pass "VISIBLE - the refused corpus read left an access_refused row naming the tool and the id ($refBefore -> $refAfter)"
    } else { Gap "STOPPED but NOT RECORDED (fetch, corpus row): expected an access_refused row naming the tool and the id ($refBefore -> $refAfter)" }

    # (d) an ABSENT id must NOT file a refusal, or the real probes drown in typos.
    $absent = [int](Db "SELECT COALESCE(max(id),0) + 5000 FROM thoughts")
    $noiseBefore = [int](Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused'")
    $null = Invoke-RawTool -Port $ServerPort -Name "fetch" -Arguments @{ id = "$absent" }
    $noiseAfter = [int](Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type='access_refused'")
    if ($noiseAfter -eq $noiseBefore) { Pass "a typo'd id files NO refusal record - the audit stays worth reading" }
    else { Fail "an absent id wrote an access_refused row ($noiseBefore -> $noiseAfter) - real probes will be buried" }

    # (e) THE COUNT IS A DISCLOSURE TOO. thought_stats reports a total and builds type/topic/
    # people histograms out of every row's metadata.
    # The COLUMN, not the mirror (DFU C.9 H3), and no `IS NULL` arm: there is no unlabelled
    # row to allow for any more - the column is NOT NULL and CHECKed.
    $onPlane = [int](Db "SELECT count(*) FROM thoughts WHERE exposure = 'ops'")
    $total   = [int](Db "SELECT count(*) FROM thoughts")
    $st2 = ((Invoke-RawTool -Port $ServerPort -Name "thought_stats" -Arguments @{}) | ConvertTo-Json -Depth 12 -Compress)
    if ($total -le $onPlane) { Fail "the fixture set is wrong - there is no off-plane row for thought_stats to omit" }
    elseif ($st2 -match "Total thoughts: $onPlane") {
        Pass "STOPPED - thought_stats counts $onPlane on-plane rows, not the $total in the table"
    } else { Fail "thought_stats did not report the on-plane count ($onPlane of $total)"; Note $st2 }

    # --- 10a-iii. ATTACK 13: THE OTHER CONTAINER --------------------------------------------
    Section "ATTACK 13 - the agent uses openbrain-ext, which reads thoughts and COPIES them into a CRM"
    # THE READER THAT WAS NEVER IN SCOPE. Four rounds of this work were scoped to
    # openbrain-mcp. `link_thought_to_contact` in the openbrain-ext image resolved a thought
    # BY ID with no plane predicate, returned its full content, AND appended that content into
    # `professional_contacts.notes` - a third home for the same text, in a table with no
    # exposure label and no way to grow one.
    docker build -t $EXTIMAGE $EXTSRC 2>&1 | Select-Object -Last 1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "docker build failed for $EXTIMAGE"; throw "ext build failed" }
    $contactId = Db "INSERT INTO professional_contacts (user_id, name, notes) VALUES ('$EXTUSER', 'drill contact $MARKER', 'baseline notes') RETURNING id"
    Invoke-DockerOrThrow -DockerArgs @("run", "-d", "--name", $EXT, "--network", $NET,
        "-p", "127.0.0.1:${ExtPort}:8000", "-e", "DB_HOST=$DB", "-e", "DB_PORT=5432",
        "-e", "DB_NAME=openbrain", "-e", "DB_USER=$APPUSER", "-e", "DB_PASSWORD=test",
        "-e", "DEFAULT_USER_ID=$EXTUSER", "-e", "MCP_ACCESS_KEY=$KEY", "-e", "PORT=8000",
        $EXTIMAGE) -What "start openbrain-ext $EXT on :$ExtPort" | Out-Null
    if (Wait-Http -Port $ExtPort -Path "/") { Pass "openbrain-ext is answering on :$ExtPort" }
    else { docker logs $EXT 2>&1 | Select-Object -Last 25 | Write-Host; Fail "openbrain-ext never answered"; throw "no ext" }

    # THIS ATTACK NOW NEEDS TWO CONTAINERS, AND THE REASON IS THE FINDING.
    #
    # ATTACK 13 used to pass because `link_thought_to_contact` carried an exposure predicate
    # in its own SQL. Amendment A2 retired the reader guards, so the only thing that could
    # refuse this read is the database - and whether the database refuses depends entirely
    # on WHO THE CONTAINER CONNECTS AS. Running the door one way and reporting the result
    # would be a claim about a configuration rather than about the code, so both are run:
    #
    #   (a) as $APPUSER, a non-superuser. The boundary binds - and so does the rest of the
    #       schema: `professional_contacts` is governed by `auth.uid() = user_id`, and
    #       `auth.uid()` in THIS database is a stub returning NULL (measured), so the policy
    #       is `NULL = user_id` for every non-superuser and the whole CRM surface is dark.
    #       That is containment by OUTAGE, not by boundary, and it is reported as a gap
    #       rather than as a pass - a door that answers nothing to anybody has not been
    #       shown to answer nothing to an ATTACKER.
    #   (b) as `postgres`, which is what production actually runs. RLS binds no superuser,
    #       so the read succeeds and the content is copied into a third home. That is the
    #       live behaviour today, and it is C.9 H1's subject, stated as evidence rather
    #       than as an accusation.
    #
    # PRODUCTION HOLDS ZERO PERSONAL ROWS, so nothing is at risk today - which is exactly
    # the property C.8 clause 3 says is not containment.
    $opsThought = Db "SELECT COALESCE(max(id)::text,'none') FROM thoughts WHERE exposure = 'ops'"

    # (a) the non-superuser door
    $extOk = ((Invoke-RawTool -Port $ExtPort -Name "link_thought_to_contact" -Arguments @{ thought_id = "$opsThought"; contact_id = "$contactId" }) | ConvertTo-Json -Depth 12 -Compress)
    $extAtk = ((Invoke-RawTool -Port $ExtPort -Name "link_thought_to_contact" -Arguments @{ thought_id = "$legacyId"; contact_id = "$contactId" }) | ConvertTo-Json -Depth 12 -Compress)
    if ($extAtk -notmatch [regex]::Escape($LEGACY)) { Pass "STOPPED (as $APPUSER) - openbrain-ext did not return the legacy personal row's content" }
    else { Fail "EXPOSURE LEAK: openbrain-ext handed over a personal-labelled thought's content as a NON-superuser" }
    if ($extOk -match "Linked thought to contact") {
        Pass "and the ops control WORKS on the same door - the refusal above is a filter, not an outage"
    } else {
        Gap "CONTAINMENT BY OUTAGE (as $APPUSER): the ops control fails too, so 'it refused' is indistinguishable from 'it is broken'. professional_contacts is governed by auth.uid() = user_id and auth.uid() is a stub returning NULL, so the whole extensions-server CRM surface is unreadable by ANY non-superuser. C.9 H1 has to decide this before it moves this container off postgres."
        Note $extOk
    }

    # (b) the SAME image, connected the way production connects it
    Invoke-DockerOrThrow -DockerArgs @("run", "-d", "--name", $REDEXT, "--network", $NET,
        "-p", "127.0.0.1:${RedExtPort}:8000", "-e", "DB_HOST=$DB", "-e", "DB_PORT=5432",
        "-e", "DB_NAME=openbrain", "-e", "DB_USER=postgres", "-e", "DB_PASSWORD=test",
        "-e", "DEFAULT_USER_ID=$EXTUSER", "-e", "MCP_ACCESS_KEY=$KEY", "-e", "PORT=8000",
        $EXTIMAGE) -What "start openbrain-ext as postgres (production's configuration) on :$RedExtPort" | Out-Null
    if (Wait-Http -Port $RedExtPort -Path "/") {
        $extNotes = Db "SELECT md5(COALESCE(notes,'')) FROM professional_contacts WHERE id = '$contactId'"
        $extSuper = ((Invoke-RawTool -Port $RedExtPort -Name "link_thought_to_contact" -Arguments @{ thought_id = "$legacyId"; contact_id = "$contactId" }) | ConvertTo-Json -Depth 12 -Compress)
        $extNotesAfter = Db "SELECT md5(COALESCE(notes,'')) FROM professional_contacts WHERE id = '$contactId'"
        if ($extSuper -match [regex]::Escape($LEGACY)) {
            Gap "PRODUCTION'S CONFIGURATION LEAKS (openbrain-ext as postgres): the same call returns the personal row's content verbatim. RLS binds no superuser, with or without FORCE. This is C.9 H1, measured rather than argued."
        } else {
            Pass "unexpected and welcome: even as postgres the ext door did not return the personal row"
        }
        if ($extNotesAfter -ne $extNotes) {
            Gap "and it COPIED that content into professional_contacts.notes - a third home with no exposure label. Same cause, same item."
            $null = Db "UPDATE professional_contacts SET notes = 'baseline notes' WHERE id = '$contactId'"
        }
        docker rm -f $REDEXT 2>$null | Out-Null
    } else { Fail "the production-configuration ext door never answered - half of ATTACK 13 did not run" }

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
    else { Gap "STOPPED but NOT RECORDED (agent_memory_review): expected 1 access_refused row, got '$revRefused'" }
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
    else { Gap "STOPPED but NOT RECORDED (agent_memory_writeback idempotency probe): expected 1 audit row, got '$wbRefused'" }
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
    else { Gap "STOPPED but NOT RECORDED (agent_memory_report_usage): expected 1 access_refused row, got '$ruRefused'" }

    # --- 14. ATTACK 14 - THE SCHEDULED WIKI COMPILE ---------------------------------------
    Section "ATTACK 14 - the WIKI COMPILER reads the corpus and PUBLISHES it"
    # WHY THIS SECTION EXISTS. Round five declared the corpus closed "at both ends" and the
    # drill had attacked every DOOR it could enumerate. A verifier then pointed at something
    # that is not a door at all: `docker/wiki-service/wiki-service.mjs` runs
    # `recipes/entity-wiki/generate-wiki.mjs` on a schedule with --batch / --ids and NEVER
    # with --semantic-expand, so the published compile never calls `match_thoughts` - the one
    # corpus reader the SQL floor covers. It SELECTS THE TABLE through PostgREST:
    #     GET /thoughts?select=id,content,metadata,created_at&id=in.(...)
    #     GET /thought_entities?select=...,thoughts(id,content,metadata,created_at)
    # and writes what comes back into markdown pages and `wiki_pages` rows the viewer serves.
    # No door refuses it, because it is not asking one.
    #
    # So this attack is not a tool call. It runs the REAL compiler, from the gitlink tree,
    # against a REAL PostgREST behind the repo's own Caddyfile, over a corpus holding one
    # ops-plane row and one personal-plane row, and then reads the files it produced.
    $WIKIOUT = Join-Path $env:TEMP "pp-drill-wiki-$RunId"
    Remove-Item $WIKIOUT -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Path $WIKIOUT -Force | Out-Null
    $wikiOutFwd = ($WIKIOUT -replace '\\', '/')
    $ob1Fwd     = ($OB1 -replace '\\', '/')
    $CORPOPS    = "SYNTHETIC OPS CORPUS ROW $MARKER publishable"
    $CORPPERS   = "SYNTHETIC PERSONAL CORPUS ROW $MARKER MUSTNOTPUBLISH"

    # 14a. the PostgREST door, exactly as compose configures it (anon role = service_role),
    # behind the repo's own path-stripping Caddyfile. Both from the exported gitlink tree.
    Invoke-DockerOrThrow -DockerArgs @("run", "-d", "--name", $PGRST, "--network", $NET,
        "--network-alias", "openbrain-postgrest",
        "-e", "PGRST_DB_URI=postgres://postgres:test@${DB}:5432/openbrain",
        "-e", "PGRST_DB_SCHEMAS=public",
        "-e", "PGRST_DB_ANON_ROLE=service_role",
        "-e", "PGRST_SERVER_PORT=3000",
        "postgrest/postgrest:v12.2.3") -What "start PostgREST $PGRST" | Out-Null
    $caddyFwd = (($OB1 -replace '\\', '/') + "/docker/Caddyfile")
    Invoke-DockerOrThrow -DockerArgs @("run", "-d", "--name", $RESTPROXY, "--network", $NET,
        "-v", "${caddyFwd}:/etc/caddy/Caddyfile:ro", "caddy:2-alpine") `
        -What "start the /rest/v1 proxy $RESTPROXY" | Out-Null
    Start-Sleep 6
    $restProbe = (docker run --rm --network $NET curlimages/curl:8.10.1 -s -o /dev/null -w "%{http_code}" "http://${RESTPROXY}/rest/v1/thoughts?limit=1" 2>&1 | Out-String).Trim()
    if ($restProbe -eq "200") { Pass "PostgREST is answering through the repo's own Caddyfile (/rest/v1 -> 200)" }
    else { Fail "the wiki compiler's REST door never came up (got '$restProbe') - ATTACK 14 would prove nothing"; throw "no rest" }

    # 14b. one entity, two thoughts linked to it: one unlabelled-plane control and one
    # PERSONAL. Planted straight into the corpus, because that is where the pre-guard mirror
    # put them and where an import script puts them.
    $ENTID = Db "INSERT INTO entities (entity_type, canonical_name, normalized_name) VALUES ('concept', 'U5 Drill Entity $MARKER', 'u5 drill entity $MARKER') RETURNING id"
    # jsonb_build_object, NOT a quoted JSON literal: the literal has to survive PowerShell,
    # docker exec argv and psql, and it did not - it arrived as `'{"` and psql answered
    # "unterminated quoted string", which is a fixture that fails LOUDLY, but only because
    # the very next assertion counts the rows it was supposed to create.
    $TOPS  = Db "INSERT INTO thoughts (content, metadata, exposure) VALUES ('$CORPOPS', jsonb_build_object('exposure','ops'), 'ops') RETURNING id"
    $TPERS = Db "INSERT INTO thoughts (content, metadata, exposure) VALUES ('$CORPPERS', jsonb_build_object('exposure','personal'), 'personal') RETURNING id"
    $null  = Db "INSERT INTO thought_entities (thought_id, entity_id, mention_role, confidence) VALUES ($TOPS, $ENTID, 'mentioned', 0.9), ($TPERS, $ENTID, 'mentioned', 0.9)"
    $linked = Db "SELECT count(*) FROM thought_entities WHERE entity_id = $ENTID"
    if ($linked -eq "2") { Pass "planted entity #$ENTID with TWO linked thoughts - ops #$TOPS and personal #$TPERS" }
    else { Fail "expected 2 linked thoughts, got '$linked' - the fixture is wrong"; throw "bad wiki fixture" }

    # 14c. run the REAL compiler, the way wiki-service runs it (--ids, no --semantic-expand).
    function Invoke-WikiCompile {
        param([string]$RecipesDir, [string]$OutDir)
        $rec = ($RecipesDir -replace '\\', '/')
        $out = ($OutDir -replace '\\', '/')
        $log = docker run --rm --network $NET `
            -v "${rec}:/recipes:ro" -v "${out}:/out" `
            -e "OPEN_BRAIN_URL=http://$RESTPROXY" -e "OPEN_BRAIN_SERVICE_KEY=local-trust" `
            -e "LLM_API_KEY=stub-not-a-secret" -e "LLM_BASE_URL=http://${STUB}:8080/v1" `
            -e "LLM_MODEL=stub" -e "EMBEDDING_API_BASE=http://${STUB}:8080/v1" `
            -e "EMBEDDING_API_KEY=stub-not-a-secret" -e "EMBEDDING_MODEL=stub" `
            -e "EMBEDDING_DIMENSION=1024" `
            node:22-alpine node /recipes/entity-wiki/generate-wiki.mjs `
            --ids $ENTID --out-dir /out 2>&1 | Out-String
        return $log
    }
    function Get-WikiText {
        param([string]$OutDir)
        $t = ""
        Get-ChildItem -Path $OutDir -Recurse -File -ErrorAction SilentlyContinue |
            ForEach-Object { $t += (Get-Content $_.FullName -Raw -ErrorAction SilentlyContinue) }
        return $t
    }
    $wikiLog = Invoke-WikiCompile -RecipesDir (Join-Path $OB1 "recipes") -OutDir $WIKIOUT
    $wikiText = Get-WikiText -OutDir $WIKIOUT

    # ANTI-VACUITY FIRST. "the personal string is absent" is satisfied by a compile that
    # produced nothing at all, which is exactly the failure this drill keeps finding in other
    # people's checks. So the ops control must be PRESENT before its absence means anything.
    if ($wikiText -match [regex]::Escape($CORPOPS)) {
        Pass "the compile really ran and really published corpus content - the OPS row is in the output"
    } else {
        Fail "the ops-plane control never reached the wiki, so 'the personal row is absent' proves nothing"
        Note ($wikiLog -split "`n" | Select-Object -Last 12) -join " | "
    }
    if ($wikiText -match [regex]::Escape($CORPPERS)) {
        Fail "STOPPED? NO - the PERSONAL corpus row was published into the wiki output"
    } else {
        Pass "STOPPED - the personal-plane corpus row appears NOWHERE in the compiler's output"
    }
    $persLeaf = Join-Path $WIKIOUT "thought\$TPERS.md"
    if (Test-Path $persLeaf) { Fail "a leaf page was emitted for the personal thought: $persLeaf" }
    else { Pass "and no content/thought/$TPERS.md leaf page exists" }
    # The wiki_pages table is the OTHER published surface - the viewer's search/nav/graph read
    # rows, not files. A page body that never reached disk can still have reached the table.
    $wpPers = Db "SELECT count(*) FROM wiki_pages WHERE body LIKE '%$MARKER MUSTNOTPUBLISH%'"
    if ($wpPers -eq "0") { Pass "and wiki_pages holds 0 rows carrying the personal row's text" }
    else { Fail "$wpPers wiki_pages row(s) carry the personal corpus content" }

    # 14d. RED - AND IT MOVED, BECAUSE THE GUARD MOVED.
    #
    # This used to copy the recipes tree, neuter `_shared/corpus-plane.mjs` (a tautological
    # `.not.is.null` in the PostgREST filter plus a pass-through `onCorpusPlane`), and
    # require the personal row to be published. THAT FILE NO LONGER EXISTS. Amendment A2
    # retired the derived file gate along with the reader guards, and the compiler now has
    # no plane predicate of its own at all - it does not need one, because it reaches the
    # corpus through PostgREST as `service_role`, which the database binds.
    #
    # So the red has to remove THE THING THAT IS ACTUALLY DOING THE WORK, and that is the
    # policy. It is removed in the throwaway, with the same permissive `USING (true)` shape
    # the pre-A2 schema shipped - the shape TRAP 1 in prove-agent-memory-rls.ps1 shows is
    # enough on its own to evaporate the boundary - and restored immediately afterwards. The
    # compiler binary, its arguments, its fixtures and its output directory are identical
    # across the two runs; the ONLY difference is the policy.
    if ($SkipRed) {
        Note "RED phase for ATTACK 14 skipped (-SkipRed) - the green above is unproven"
    } else {
        $REDWIKIOUT = Join-Path $env:TEMP "pp-drill-red-wiki-$RunId"
        Remove-Item $REDWIKIOUT -Recurse -Force -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Path $REDWIKIOUT -Force | Out-Null

        $null = Db "DROP POLICY IF EXISTS thoughts_ops_plane ON public.thoughts; CREATE POLICY thoughts_ops_plane ON public.thoughts AS PERMISSIVE FOR ALL TO service_role USING (true) WITH CHECK (true);"
        $redPolicy = Db "SELECT COALESCE(qual,'-') FROM pg_policies WHERE tablename='thoughts' AND policyname='thoughts_ops_plane'"
        if ($redPolicy -eq "true") { Pass "RED: the corpus policy is widened to USING (true) in the THROWAWAY only - the compiler and its arguments are untouched" }
        else { Fail "RED: could not widen the corpus policy (qual='$redPolicy') - the red phase would be a second green run"; throw "red widen failed" }

        $redLog = Invoke-WikiCompile -RecipesDir (Join-Path $OB1 "recipes") -OutDir $REDWIKIOUT
        $redText = Get-WikiText -OutDir $REDWIKIOUT
        if ($redText -match [regex]::Escape($CORPPERS)) {
            Pass "RED CONFIRMED (ATTACK 14) - with the policy widened the PERSONAL row IS published, so the database predicate is what stops it"
        } else {
            Fail "RED: the personal row did not leak even with the policy wide - ATTACK 14's green proves nothing"
            Note (($redLog -split "`n" | Select-Object -Last 12) -join " | ")
        }

        # RESTORE, and assert the restore, because a drill that leaves its own throwaway
        # unguarded would make every later section in this run meaningless.
        $null = Db "DROP POLICY IF EXISTS thoughts_ops_plane ON public.thoughts; CREATE POLICY thoughts_ops_plane ON public.thoughts AS PERMISSIVE FOR ALL TO service_role USING (public.ob_corpus_on_ops_plane(exposure)) WITH CHECK (public.ob_corpus_on_ops_plane(exposure));"
        $backPolicy = Db "SELECT COALESCE(qual,'-') FROM pg_policies WHERE tablename='thoughts' AND policyname='thoughts_ops_plane'"
        if ($backPolicy -match "ob_corpus_on_ops_plane") { Pass "and the shipped policy is restored ($backPolicy) - the sections below are back under the real boundary" }
        else { Fail "could not restore the shipped corpus policy (qual='$backPolicy') - everything after this point is untrustworthy"; throw "red restore failed" }
        Remove-Item $REDWIKIOUT -Recurse -Force -ErrorAction SilentlyContinue
    }

    # 14e. remove the wiki fixtures. Everything planted in this section, gone before the LIFT
    # section counts the plane.
    $null = Db "DELETE FROM wiki_pages WHERE body LIKE '%$MARKER%'"
    $null = Db "DELETE FROM thought_entities WHERE entity_id = $ENTID"
    $null = Db "DELETE FROM thoughts WHERE id IN ($TOPS, $TPERS)"
    $null = Db "DELETE FROM entities WHERE id = $ENTID"
    Remove-Item $WIKIOUT -Recurse -Force -ErrorAction SilentlyContinue

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
    #
    # THE RED PHASE WAS REBUILT FROM SCRATCH IN THIS ROUND, AND THE REASON IT HAD TO BE IS
    # THE MOST USEFUL THING IN THIS FILE.
    #
    # It used to build a SECOND image with the exposure guards removed - four asserted line
    # anchors, three of them in `agent-memory-plane.ts`. That file DOES NOT EXIST any more.
    # Amendment A2 (2026-08-30) retired the enumerate-and-guard method along with the module
    # that held the chokepoint, and moved enforcement into the database. So the red phase was
    # patching lines out of a file the tree no longer ships: it would have failed at
    # `Set-RedAnchor` with "matched 0 times", which is the one thing that safeguard is for.
    #
    # A red must remove THE MECHANISM THAT IS ACTUALLY DOING THE WORK, and that mechanism is
    # now "the door's connection is a role the policies bind". Take it away and every green
    # above comes back as a leak. Taking it away is one environment variable:
    #
    #       DB_USER=postgres
    #
    # WHICH IS WHAT PRODUCTION RUNS. C.9 H1 measured 22 of 22 live connections to
    # openbrain-db as `postgres` - rolsuper, rolbypassrls - and "Superusers and roles with
    # the BYPASSRLS attribute always bypass the row security system", FORCE included. So
    # this red phase is not a hypothetical weakening of the tree. It is the deployed
    # configuration, run beside the bound one, with the same fixtures and the same calls.
    # Every leak it reports is a leak production has today, and every one of them is H1.
    if ($SkipRed) {
        Section "RED phase SKIPPED (-SkipRed) - the green results above are unproven"
        Note "A guard nobody has watched fail is not known to guard anything."
    } else {
        Section "RED - the SAME doors, connected as postgres, which is what production runs"
        Start-McpServer -Name $REDSRV -Port $RedSrvPort -Img $IMAGE -DbUser "postgres"
        if (Wait-Http -Port $RedSrvPort -Path "/health") {
            Pass "the same image is up on :$RedSrvPort connected as postgres (same database, same fixtures, same code)"
        } else { docker logs $REDSRV 2>&1 | Select-Object -Last 25 | Write-Host; Fail "red server never answered"; throw "no red server" }

        # A red for every family of green above. Each one names the attack it backs, so a
        # green whose red is missing is visible as an absence rather than as silence.
        $redPersId = Db "SELECT COALESCE(max(id)::text,'none') FROM thoughts WHERE content LIKE '%$MARKER%' AND exposure='personal'"

        # RED for ATTACK 1 - the internal REST recall, naming the personal plane.
        $r1 = Invoke-Rest -Port $RedSrvPort -Path "/agent-memory/recall" -Body @{
            workspace_id = $WS; project_id = $PROJ
            query = $MARKER; limit = 25; include_unconfirmed = $true
            exposure = @("personal")
        }
        $r1ids = @()
        if ($r1.Body -and $r1.Body.items) { $r1ids = @($r1.Body.items | ForEach-Object { $_.memory_id }) }
        # The door still forces its own plane in the SQL, so the recall filter alone holds
        # here even as a superuser - and that is worth SAYING rather than hiding, because it
        # is the one place an application guard survived A2 and it is real defence in depth.
        if ($r1ids -contains $PID_PERS) {
            Pass "RED CONFIRMED (ATTACK 1) - as postgres the same request returns the personal fixture"
        } else {
            Note "ATTACK 1's green does NOT rest on the database: agent-memory-policy.ts still forces the door's plane into the recall SQL, so it holds for a superuser too. Defence in depth, and the only reader guard A2 left standing."
            Pass "ATTACK 1 is guarded in the APPLICATION as well as in the database - stated, not assumed"
        }

        # RED for ATTACKS 11/12 - the corpus tools at the raw door. These have NO application
        # predicate left at all; the database is the only thing between them and the content.
        $rList = (Invoke-RawTool -Port $RedSrvPort -Name "list_thoughts" -Arguments @{ limit = 50 } | ConvertTo-Json -Depth 12 -Compress)
        if ($rList -match [regex]::Escape($PERSONAL) -or $rList -match [regex]::Escape($LEGACY)) {
            Pass "RED CONFIRMED (ATTACK 11/12) - as postgres, list_thoughts at the raw door hands over personal-plane corpus content"
        } else { Fail "list_thoughts did not leak even as a superuser - ATTACK 11/12's green proves nothing"; Note $rList }

        $rSearch = (Invoke-RawTool -Port $RedSrvPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25; threshold = 0.0 } | ConvertTo-Json -Depth 12 -Compress)
        if ($rSearch -match [regex]::Escape($PERSONAL) -or $rSearch -match [regex]::Escape($LEGACY)) {
            Pass "RED CONFIRMED (ATTACK 11) - search_thoughts leaks the same content on the same connection"
        } else { Fail "search_thoughts did not leak as a superuser - its green proves nothing"; Note $rSearch }

        $rFetch = (Invoke-RawTool -Port $RedSrvPort -Name "fetch" -Arguments @{ id = "$legacyId" } | ConvertTo-Json -Depth 12 -Compress)
        if ($rFetch -match [regex]::Escape($LEGACY)) {
            Pass "RED CONFIRMED (ATTACK 12) - fetch by id returns the personal corpus row verbatim"
        } else { Fail "fetch did not leak as a superuser - its green proves nothing"; Note $rFetch }

        $rStats = (Invoke-RawTool -Port $RedSrvPort -Name "thought_stats" -Arguments @{} | ConvertTo-Json -Depth 12 -Compress)
        $rTotal = [int](Db "SELECT count(*) FROM thoughts")
        if ($rStats -match "Total thoughts: $rTotal") {
            Pass "RED CONFIRMED (ATTACK 12e) - thought_stats counts ALL $rTotal rows as a superuser, not the on-plane subset"
        } else { Fail "thought_stats did not report the full count as a superuser - the green count proves nothing"; Note $rStats }

        # RED for ATTACK 3 - inspect by id. The tool has a plane clause of its own, so this
        # red distinguishes the two layers rather than assuming one of them.
        $rOps = Start-Gateway -Name $REDOPS -Port $RedOpsPort -GwEnv $opsEnv -Upstream "http://${REDSRV}:8000"
        if (Wait-Http -Port $RedOpsPort -Path "/health") {
            $rInspect = (Invoke-Tool -Port $RedOpsPort -Name "agent_memory_inspect" -Arguments @{ memory_id = "$PID_PERS" } | ConvertTo-Json -Depth 12 -Compress)
            if ($rInspect -match [regex]::Escape($PERSONAL)) {
                Pass "RED CONFIRMED (ATTACK 3) - as postgres, inspect returns the personal memory's content"
            } else {
                Note "ATTACK 3's green rests on agent-memory-tools.ts's own `exposure = ANY(...)` clause as well as on the database - it holds for a superuser too. Defence in depth, stated."
                Pass "ATTACK 3 is guarded in the APPLICATION as well as in the database - stated, not assumed"
            }
            # RED for ATTACK 8 - the escalation. Same reasoning: the review door filters on
            # the plane in SQL, so this red says WHICH layer stopped it.
            $rRev = (Invoke-Tool -Port $RedOpsPort -Name "agent_memory_review" -Arguments @{ memory_id = "$PID_PERS"; action = "promote_exposure"; reviewer = "drill-red" } | ConvertTo-Json -Depth 12 -Compress)
            $rExp = Db "SELECT exposure FROM agent_memories WHERE id = '$PID_PERS'"
            if ($rExp -eq "ops") {
                Pass "RED CONFIRMED (ATTACK 8) - as postgres, promote_exposure MOVED the personal memory onto the ops plane"
                $null = Db "UPDATE agent_memories SET exposure='personal', metadata = metadata || jsonb_build_object('exposure','personal') WHERE id = '$PID_PERS'"
                Note "restored to exposure=personal for the sections below"
            } else {
                Note "ATTACK 8's green rests on the review door's own plane clause as well as on the database (memory is still exposure=$rExp)."
                Pass "ATTACK 8 is guarded in the APPLICATION as well as in the database - stated, not assumed"
            }
        } else { Fail "the red ops gateway never answered - the by-id reds did not run" }

        # RED for ATTACK 14 - the wiki compiler. It reaches the corpus through PostgREST as
        # `service_role`, which is NOT a superuser, so the red for it is not a connection
        # change: it is the migration itself. Removing 195/200 from a second database is a
        # whole-database red and is what prove-agent-memory-rls.ps1 does; it is not repeated
        # here, and the pointer is the honest substitute for a check this file does not run.
        Note "RED for ATTACK 14 lives in scripts/checks/prove-agent-memory-rls.ps1, which builds a whole database WITHOUT the boundary migrations and shows PostgREST handing the personal row back. The wiki compiler is a PostgREST caller, so that is its red."

        Section "RED - the CLOUD door's exclusion is the LABEL, not luck"
        # ATTACK 7(b) passes if the agent-memory thought is missing for ANY reason -
        # including 'search_thoughts is broken' or 'the marker did not match'. The claim
        # under test is specifically that the absent share:'cloud' label is what excludes it.
        # So: put the label on, change nothing else, and require it to come back.
        $null = Db "UPDATE thoughts SET metadata = metadata || jsonb_build_object('share','cloud') WHERE id = $opsTid"
        # SCOPED TO THE AGENT-MEMORY MIRROR, which the UPDATE above already is and this count
        # was not. The cloud CONTROL thought also carries share=cloud, exposure=ops and the
        # marker, so an unscoped count returns more than one and the red reports a fixture
        # error instead of running - a mismatch between a statement and the assertion that
        # checks it, which is its own small instance of the class this file is about.
        # SCOPED TO THE ONE ROW THE UPDATE MEANT, by its id. Counting by predicate returned 2
        # - the ops control's mirror plus the retry the writeback idempotency probe left -
        # and the red then reported a fixture error instead of running. An assertion that
        # does not name the same row its statement changed is an assertion about something
        # else.
        $labelled = Db "SELECT count(*) FROM thoughts WHERE id = $opsTid AND metadata->>'share'='cloud'"
        if ($labelled -eq "1") {
            $clRed = (Invoke-Tool -Port $CloudPort -Name "search_thoughts" -Arguments @{ query = $MARKER; limit = 25 } | ConvertTo-Json -Depth 12 -Compress)
            if ($clRed -match "SYNTHETIC ops-plane CONTROL") {
                Pass "RED CONFIRMED (ATTACK 7b) - label the mirrored thought share=cloud and the CLOUD door hands over the agent memory"
                Note "so the cloud door's exclusion is the missing label doing the work, exactly as agent-memory.ts claims - not an accident of the query"
            } else {
                Fail "even labelled share=cloud the mirror did not come back - ATTACK 7b proves nothing about the label"
                Note $clRed
            }
        } else { Fail "could not label the mirrored thought for the red phase (got '$labelled')" }
        # Put it back, so anything that reads this database afterwards sees the real state.
        $null = Db "UPDATE thoughts SET metadata = metadata - 'share' WHERE metadata->>'source'='agent-memory' AND content LIKE '%$MARKER%'"
    }

    # --- 13. THE LIFT: can the "do not write a personal-exposure memory" rule be dropped? ---
    Section "THE LIFT - a personal-plane memory was WRITTEN, REFUSED at every door, RECORDED, and REMOVED"
    # WHAT THIS SECTION IS FOR. documentation/notes/personal-plane-second-home-LATENT-LEAK.md
    # imposed an operational rule - "do not write a personal-exposure memory until this is
    # closed" - because the plane holding zero personal rows was the only thing keeping the
    # leak unexploitable. A rule like that is not lifted by an argument; it is lifted by a
    # personal-plane memory existing, every door refusing it, every refusal being on the
    # record, and the plane then being empty again on the way out.
    #
    # Everything this asserts already happened above, one attack at a time. Gathering it here
    # is deliberate: the lift is the CONJUNCTION, and a conjunction spread across twenty
    # sections is a conclusion a reader assembles by hand.
    $lifted = $true
    function Lift([bool]$Ok, [string]$What) {
        if ($Ok) { Pass $What } else { Fail $What; $script:lifted = $false }
    }
    # The same conjunction, for a clause whose failure is a NAMED GAP rather than a defect
    # in this tree. It still withdraws the lift - the lift is the conjunction, and a
    # conjunction with an open term is not satisfied.
    function LiftGap([bool]$Ok, [string]$What, [string]$Why) {
        if ($Ok) { Pass $What } else { Gap $What; Note $Why; $script:lifted = $false }
    }

    # (1) it can be WRITTEN - through the real write path, not planted.
    $persStill = Db "SELECT count(*) FROM agent_memories WHERE id = '$PID_PERS' AND metadata->>'exposure' = 'personal'"
    Lift ($persStill -eq "1") "WRITTEN - the synthetic personal memory exists on the personal plane ($PID_PERS)"

    # (2) every TARGETED door REFUSED it AND RECORDED the refusal - counted from the audit
    # table, by tool, not from prose.
    #
    # TARGETED, and the distinction is load-bearing rather than an excuse. A by-id door
    # (inspect, fetch, review, report_usage, the writeback's retry key, openbrain-ext's
    # link tool, the trace by id) DENIES A NAMED REQUEST: somebody asked for something
    # specific and was told no, and that is the event U5's column means by "the attempt is
    # visible in an audit record". An ENUMERATING door (recall, list_review_queue) FILTERS:
    # the caller asked for "the queue" and got the queue for its own plane, so there is no
    # denied request to record, and a row per listing would file ordinary use as a probe and
    # bury the rows that mean somebody reached for the personal plane.
    #
    # THE FIRST VERSION OF THIS CHECK EXPECTED A ROW FROM list_review_queue AND WENT RED. The
    # honest fix was not to widen the audit; it was to state the design the chokepoint has
    # documented since round three, and to assert BOTH halves - the targeted doors record,
    # the enumerating doors do not, and the enumerating doors returned nothing personal
    # (ATTACKS 2 and 4, above).
    $tools = @(Db "SELECT string_agg(DISTINCT payload->>'tool', ',' ORDER BY payload->>'tool') FROM agent_memory_audit_events WHERE event_type = 'access_refused'")
    $toolList = @(($tools -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $expected = @("agent_memory_inspect", "agent_memory_recall_trace", "agent_memory_report_usage",
                  "agent_memory_review", "agent_memory_writeback", "fetch", "link_thought_to_contact")
    $missing = @($expected | Where-Object { $toolList -notcontains $_ })
    LiftGap ($missing.Count -eq 0) "REFUSED AND RECORDED - every TARGETED door left an access_refused row: $($toolList -join ', ')" `
        "no refusal recorded for: $($missing -join ', ') - $AUDIT_GAP"
    $filtering = @("agent_memory_list_review_queue", "agent_memory_recall")
    $wrongly = @($filtering | Where-Object { $toolList -contains $_ })
    Lift ($wrongly.Count -eq 0) "and the ENUMERATING doors filed NOTHING - filtering is not refusing, so the log stays readable"
    if ($wrongly.Count -gt 0) { Note "unexpectedly filed a refusal: $($wrongly -join ', ')" }

    # (3) and the RECORD is not itself the leak - no refusal row carries the content.
    $leakyAudit = Db "SELECT count(*) FROM agent_memory_audit_events WHERE payload::text LIKE '%SYNTHETIC personal-plane FIXTURE%' OR payload::text LIKE '%SYNTHETIC LEGACY CORPUS ROW%'"
    Lift ($leakyAudit -eq "0") "and NO audit row carries the content it refused - the record does not become the disclosure"

    # (4) REMOVED. The fixture, its legacy corpus row, and anything the red phase mirrored.
    $null = Db "DELETE FROM agent_memories WHERE workspace_id = '$WS'"
    $null = Db "DELETE FROM thoughts WHERE content LIKE '%$MARKER%'"
    $persMem = Db "SELECT count(*) FROM agent_memories WHERE COALESCE(metadata->>'exposure','personal') = 'personal'"
    $persThoughts = Db "SELECT count(*) FROM thoughts WHERE metadata->>'exposure' = 'personal'"
    Lift ($persMem -eq "0") "REMOVED - agent_memories holds 0 personal-plane rows again (was 1)"
    Lift ($persThoughts -eq "0") "REMOVED - thoughts holds 0 personal-labelled rows again"

    # (5) THE AUDIT SURVIVES THE FIXTURE. memory_id is ON DELETE SET NULL, so the refusal
    # rows stay after the memory goes - which is the property that makes "the attempt is
    # visible in an audit record" mean anything at all once a memory is retired.
    $auditLeft = Db "SELECT count(*) FROM agent_memory_audit_events WHERE event_type = 'access_refused'"
    LiftGap ([int]$auditLeft -ge 8) "and the $auditLeft access_refused rows OUTLIVE the deleted fixture (memory_id ON DELETE SET NULL)" `
        "only $auditLeft refusal row(s) exist to outlive anything, for the reason above - this clause cannot be evaluated until the one above is closed"

    # (6) THE LIFT IS WITHDRAWN, AND THIS IS WHERE IT IS WITHDRAWN.
    #
    # Round five printed "LIFT SUPPORTED on this tree" off the conjunction above, and the
    # conjunction was over the doors THIS FILE HAPPENS TO NAME. Its own wording said so -
    # "every TARGETED door left an access_refused row" - and then the conclusion treated the
    # targeted set as the complete set. A verifier walked straight past it into ATTACK 14's
    # subject, which is not a door and which no amount of door coverage would have found.
    #
    # THE ASYMMETRY THAT MATTERS: the FILE gate (agent-memory-plane.test.ts) derives its
    # scan roots from compose's build contexts and bind-mounts, its file set from what those
    # roots contain, its table set from the schema, and its corpus-function set from the
    # initdb chain. This drill's door list is written by hand, one Section at a time. The
    # half that is derived keeps finding readers; the half that is enumerated keeps being
    # complete right up until it is not.
    #
    # So the drill no longer claims a lift. It reports what it proved, and names what would
    # have to become derived before the claim is worth making again.
    if ($lifted) {
        Write-Host "`n  ATTACKS PASSED on this tree, and the operational constraint STANDS." -ForegroundColor Yellow
        Write-Host "  PROVED: a personal-exposure memory can be written, is refused by every door this" -ForegroundColor Green
        Write-Host "  drill names, leaves a record, and leaves the plane empty when removed; and the" -ForegroundColor Green
        Write-Host "  scheduled wiki compiler does not publish personal-plane corpus content (ATTACK 14)." -ForegroundColor Green
        Write-Host "  NOT PROVED - and therefore NOT LIFTED:" -ForegroundColor Yellow
        Write-Host "    * this drill's DOOR LIST is hand-written, while the file gate's scan set is" -ForegroundColor Yellow
        Write-Host "      derived. A door nobody wrote a Section for is not covered by anything here." -ForegroundColor Yellow
        Write-Host "    * ~28 mounted-but-unstarted recipe scripts read the corpus with no plane. They" -ForegroundColor Yellow
        Write-Host "      are inventoried with pinned counts by the file gate, not closed." -ForegroundColor Yellow
        Write-Host "    * PRODUCTION runs none of this: the deployed openbrain-mcp image has no" -ForegroundColor Yellow
        Write-Host "      chokepoint module at all, and the corpus-plane SQL is not applied." -ForegroundColor Yellow
        Write-Host "  RE-PROPOSE THE LIFT when the door set is derived the way the file set is." -ForegroundColor Yellow
    } else {
        Write-Host "`n  ATTACKS FAILED - the constraint stays, and so does a defect." -ForegroundColor Red
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
        Remove-Item $REDEXTDIR -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $OB1       -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item $STUBPATH  -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $env:TEMP "pp-drill-wiki-$RunId")        -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $env:TEMP "pp-drill-red-recipes-$RunId") -Recurse -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $env:TEMP "pp-drill-red-wiki-$RunId")    -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
if ($fails -eq 0 -and $gaps -eq 0) {
    Write-Host "PERSONAL-PLANE EXCLUSION DRILL PASSED - $passes checks, every attack stopped, every targeted refusal recorded" -ForegroundColor Green
    Write-Host "THE OPERATIONAL CONSTRAINT STANDS: do not write a personal-exposure memory. See THE LIFT above." -ForegroundColor Yellow
    exit 0
}
if ($fails -gt 0) {
    Write-Host "$fails DRILL CHECK(S) FAILED ($passes passed, $gaps gap(s))" -ForegroundColor Red
    exit 1
}
# NO DEFECT IN THIS TREE, AND NOT A PASS EITHER. Every containment attack was stopped; what
# is open is a set of NAMED, DISPOSITIONED properties this tree cannot currently deliver.
# They are printed above with their causes. THE EXIT CODE IS NOT ZERO, deliberately: U5's
# column asks for "mechanically stopped AND the attempt is visible in an audit record", and
# a drill that returned success on half of that would be the redefinition C.8 forbids.
Write-Host "PERSONAL-PLANE EXCLUSION DRILL: CONTAINMENT GREEN, $gaps NAMED GAP(S) OPEN ($passes checks passed, 0 failed)" -ForegroundColor Yellow
Write-Host "  Every attack was STOPPED. What is not met is the RECORDING half of U5's column," -ForegroundColor Yellow
Write-Host "  and the doors that connect as postgres. Both are C.9 H1/H4 items, both are named" -ForegroundColor Yellow
Write-Host "  above with the measurement, and neither is closed by this run." -ForegroundColor Yellow
Write-Host "  See documentation/notes/u8h3-findings.md." -ForegroundColor Yellow
exit 2
