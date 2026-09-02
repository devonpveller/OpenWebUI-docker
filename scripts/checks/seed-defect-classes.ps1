# seed-defect-classes.ps1 - put this effort's defect-class list ON THE MEMORY PLANE.
#
# PLAN.md C.8 clause 8 is not about plumbing. U1 built the plumbing; this clause is met when
# the plane is USED - "real efforts write to it as they run, and at least one recall
# demonstrably informed a later effort". The content that actually compounded across this
# effort's rounds is its DEFECT-CLASS LIST, and it compounded BY HAND, in DECISIONS.md,
# because the plane was empty (DECISIONS.md 2026-08-30, "THE EFFORT RAN THE LOOP ITS OWN PLAN
# EXISTS TO FIX"). This script moves that list to where a later attempt can be TOLD about it.
#
# WHAT IT DOES, and every step is verified rather than assumed:
#   1. Writes each entry of defect-classes.json through the OPS DOOR
#      (openbrain-ops-gateway, 127.0.0.1:8062) using agent_memory_writeback, keyed by
#      idempotency_key so a re-run returns the original row instead of writing a second.
#   2. Transitions each from 'pending' to 'evidence_only' via agent_memory_review. This is
#      NOT a formality: WRITEBACK_DEFAULTS.review_status is 'pending' and the default recall
#      gate admits only 'confirmed' and 'evidence_only', so a memory written and left alone
#      is written and unrecallable. 'evidence_only' is the honest rung - an agent wrote these,
#      nobody is vouching for them as instruction-grade, and `confirm` is the action whose
#      meaning is "a human vouches".
#   3. ASSERTS THE STAMP LANDED. exposure is stamped BY THE DOOR (stampExposure), never by the
#      writer, and PII in the content DEMOTES to the personal plane. So the write half needs a
#      positive control like any other probe: every seeded row is read back and must be 'ops',
#      and the whole agent_memories table must hold ZERO personal rows when this exits.
#
# CLASS-4 BOUNDARY (PLAN C.2): ops plane only. This script cannot write the personal plane -
# the door forces the stamp - and it FAILS if a personal row exists anywhere in the table.
#
# THE KEY IS NEVER PERSISTED. It is read from the running container's environment at run time
# (or from OPS_GATEWAY_KEY), held in memory, and never written to a file or the console.
#
#   .\seed-defect-classes.ps1              # write + transition + verify
#   .\seed-defect-classes.ps1 -DryRun      # parse and report, touch nothing
[CmdletBinding()]
param(
    [string]$Data = "",
    [string]$Url = "http://127.0.0.1:8062/mcp",
    [string]$Container = "openbrain-ops-gateway",
    [string]$DbContainer = "openbrain-db",
    [string]$Actor = "wt-c8plane (agent; PLAN C.8 clause 8 seeding)",
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $Data) { $Data = Join-Path $PSScriptRoot "defect-classes.json" }
if (-not (Test-Path $Data)) { Write-Host "no payload file at $Data" -ForegroundColor Red; exit 2 }

$payload = Get-Content -Raw -Path $Data | ConvertFrom-Json
$ws = [string]$payload.workspace_id
if (-not $ws) { Write-Host "payload has no workspace_id" -ForegroundColor Red; exit 2 }

# --- the door key, read at run time -------------------------------------------------
function Get-DoorKey {
    if ($env:OPS_GATEWAY_KEY) { return $env:OPS_GATEWAY_KEY }
    $envs = & docker inspect $Container --format '{{range .Config.Env}}{{println .}}{{end}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $envs) { return $null }
    foreach ($line in @($envs)) {
        if ($line -match '^GATEWAY_KEY=(.+)$') { return $Matches[1].Trim() }
    }
    return $null
}

# --- one MCP tool call, over the door ------------------------------------------------
# The gateway answers text/event-stream, so the JSON is on a `data:` line. A caller that
# assumed application/json would silently read nothing and - per this effort's own class
# list - would then report a pass because nothing objected.
function Invoke-OpsTool {
    param([string]$Key, [string]$Tool, $Arguments)
    $body = @{
        jsonrpc = "2.0"; id = 1; method = "tools/call"
        params  = @{ name = $Tool; arguments = $Arguments }
    } | ConvertTo-Json -Depth 12 -Compress
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Post -Body $bytes -UseBasicParsing `
                -ContentType "application/json" -Headers @{
                    "Authorization" = "Bearer $Key"
                    "Accept"        = "application/json, text/event-stream"
                }
    } catch {
        return @{ ok = $false; error = ("transport: " + $_.Exception.Message); text = "" }
    }
    $raw = [string]$resp.Content
    $json = $raw
    if ($raw -match '(?m)^data:\s*(\{.*\})\s*$') { $json = $Matches[1] }
    try { $obj = $json | ConvertFrom-Json } catch {
        return @{ ok = $false; error = "unparseable response"; text = $raw }
    }
    if ($obj.PSObject.Properties.Name -contains "error" -and $obj.error) {
        return @{ ok = $false; error = ("rpc: " + $obj.error.message); text = $raw }
    }
    $text = ""
    if ($obj.PSObject.Properties.Name -contains "result" -and $obj.result -and
        $obj.result.PSObject.Properties.Name -contains "content") {
        $text = (@($obj.result.content) | ForEach-Object { [string]$_.text }) -join "`n"
    }
    return @{ ok = $true; error = ""; text = $text }
}

function Invoke-Psql {
    param([string]$Sql)
    $out = & docker exec $DbContainer psql -U postgres -d openbrain -tAc $Sql 2>&1
    return @{ exit = $LASTEXITCODE; out = (@($out) -join "`n") }
}

$mems = @($payload.memories)
Write-Host ""
Write-Host "SEEDING THE DEFECT-CLASS CORPUS onto the ops plane" -ForegroundColor Cyan
Write-Host ("  payload   : {0}" -f $Data)
Write-Host ("  entries   : {0}" -f $mems.Count)
Write-Host ("  door      : {0} (profile ops - exposure is stamped by the door)" -f $Url)
Write-Host ("  workspace : {0}" -f $ws)
Write-Host ""

if ($DryRun) {
    foreach ($m in $mems) {
        Write-Host ("  [dry-run] {0}  {1,-10}  {2} chars" -f $m.key, $m.memory_type, ([string]$m.content).Length)
    }
    Write-Host ""
    Write-Host "-DryRun: nothing was written." -ForegroundColor Yellow
    exit 0
}

$key = Get-DoorKey
if (-not $key) {
    Write-Host ("could not read the ops door key from container '{0}' and OPS_GATEWAY_KEY is unset" -f $Container) -ForegroundColor Red
    exit 2
}

$written = 0; $failed = 0; $rows = @()
foreach ($m in $mems) {
    $wbArgs = @{
        workspace_id    = $ws
        summary         = [string]$m.summary
        content         = [string]$m.content
        memory_type     = [string]$m.memory_type
        idempotency_key = [string]$m.key
        channel_kind    = "plan"
        channel_id      = "dark-factory-unification"
        metadata        = @{
            source_of_record = [string]$payload.source_of_record
            class_key        = [string]$m.key
            seeded_by        = "scripts/checks/seed-defect-classes.ps1"
        }
    }
    $r = Invoke-OpsTool -Key $key -Tool "agent_memory_writeback" -Arguments $wbArgs
    if (-not $r.ok) {
        Write-Host ("  FAIL  {0}: {1}" -f $m.key, $r.error) -ForegroundColor Red
        $failed++; continue
    }
    # The tool answers with the memory id in its text. Pull the uuid rather than trusting a
    # shape: a writeback that was REFUSED (secret-shaped content, PII, too large) answers with
    # prose and no id, and treating that as success is exactly the guard-deciding-by-exception
    # class this corpus is about.
    $mid = ""
    if ($r.text -match '([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})') { $mid = $Matches[1] }
    if (-not $mid) {
        Write-Host ("  FAIL  {0}: no memory id in the response -- {1}" -f $m.key, ($r.text -replace "`n", " ")) -ForegroundColor Red
        $failed++; continue
    }
    $rows += [pscustomobject]@{ key = $m.key; id = $mid; type = [string]$m.memory_type }
    $written++
    Write-Host ("  wrote {0}  {1}" -f $mid, $m.key)
}

Write-Host ""
Write-Host ("transitioning pending -> evidence_only ({0} row(s))" -f $rows.Count) -ForegroundColor Cyan
$moved = 0
foreach ($row in $rows) {
    $r = Invoke-OpsTool -Key $key -Tool "agent_memory_review" -Arguments @{
        memory_id = $row.id
        action    = "evidence_only"
        actor     = @{ label = $Actor }
        note      = "Defect class extracted from DECISIONS.md and seeded onto the plane so a later attempt is TOLD the shape instead of rediscovering it. evidence_only, not confirmed: an agent wrote it and nobody has vouched for it as instruction-grade."
    }
    if ($r.ok) { $moved++ } else { Write-Host ("  FAIL  {0}: {1}" -f $row.key, $r.error) -ForegroundColor Red }
}
Write-Host ("  {0} of {1} now evidence_only" -f $moved, $rows.Count)

# --- THE POSITIVE CONTROL ON THE WRITE ------------------------------------------------
# Asserting the rows are absent from the personal plane is not enough on its own: a write
# that never landed is also absent. So both directions are measured - the seeded rows must be
# PRESENT and stamped 'ops', and the personal plane must be EMPTY.
Write-Host ""
Write-Host "VERIFYING (both directions - present-and-ops, and personal-plane empty)" -ForegroundColor Cyan
$rc = 0

$q = Invoke-Psql -Sql ("SELECT count(*) FROM agent_memories WHERE metadata->>'class_key' IS NOT NULL AND metadata->>'exposure' = 'ops' AND review_status = 'evidence_only';")
$okOps = ($q.exit -eq 0 -and ($q.out.Trim() -match '^\d+$'))
if (-not $okOps) {
    Write-Host "  INDETERMINATE: could not read agent_memories" -ForegroundColor Yellow; $rc = 3
} else {
    $n = [int]$q.out.Trim()
    if ($n -eq $rows.Count -and $n -gt 0) {
        Write-Host ("  PASS  {0} seeded row(s) present, exposure=ops, review_status=evidence_only" -f $n) -ForegroundColor Green
    } else {
        Write-Host ("  FAIL  expected {0} seeded ops/evidence_only row(s), found {1}" -f $rows.Count, $n) -ForegroundColor Red
        $rc = 1
    }
}

$q2 = Invoke-Psql -Sql "SELECT count(*) FROM agent_memories WHERE COALESCE(metadata->>'exposure','personal') = 'personal';"
if ($q2.exit -ne 0 -or -not ($q2.out.Trim() -match '^\d+$')) {
    Write-Host "  INDETERMINATE: could not count personal rows" -ForegroundColor Yellow; $rc = 3
} elseif ([int]$q2.out.Trim() -ne 0) {
    Write-Host ("  FAIL  agent_memories holds {0} PERSONAL row(s) - class-4 boundary" -f $q2.out.Trim()) -ForegroundColor Red
    $rc = 1
} else {
    Write-Host "  PASS  agent_memories personal rows = 0" -ForegroundColor Green
}

Write-Host ""
Write-Host ("written {0}, failed {1}, evidence_only {2}" -f $written, $failed, $moved)
if ($failed -gt 0 -and $rc -eq 0) { $rc = 1 }
exit $rc
