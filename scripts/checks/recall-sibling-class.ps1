# recall-sibling-class.ps1 - THE SEAM. Before a fix round declares a defect fixed, ask the
# memory plane whether this defect's class has a known sibling pattern.
#
# WHY THIS SEAM AND NOT ANOTHER. PLAN C.8 clause 8 asks for a recall that DEMONSTRABLY INFORMED
# a later effort, and the moment this effort has repeatedly needed one is exactly here: six
# times in one run a defect was correctly and locally fixed while the identical construct sat a
# few hundred lines away (DECISIONS.md, "FOURTH/FIFTH/SIXTH INSTANCE"). The rule that would have
# caught all six was adopted at instance 4 and violated at 5 and 6 - "a discipline that depends
# on remembering is the normative governance section 0 A7 already recorded as FALSIFIED". So the
# reminder has to arrive from somewhere that is not the fixer's memory. That is what a memory
# plane is for, and this is the seam where it pays.
#
# THE FOUR OUTCOMES ARE FOUR DIFFERENT WORDS, AND FOUR DIFFERENT EXIT CODES. Threshold
# calibration for this corpus is blocked (bge-m3's 0.7 default is OpenAI-tuned and the corpus is
# tiny), so a recall CAN legitimately return nothing. The one thing that must never happen is
# for "the plane had nothing to say" to look like "the plane was consulted and agreed" - that
# collapse is the whole clause, and it is this repo's own "a check green while checking nothing"
# class wearing a recall's clothes.
#
#   exit 0  INFORMED     - the plane returned at least one memory. Read them, then report usage.
#   exit 4  EMPTY        - the plane was reached and matched nothing. NOT a pass. Recorded.
#   exit 5  UNAVAILABLE  - the door could not be reached, or answered something unreadable.
#   exit 2  usage error.
#
# A SUBSTITUTION THIS TOOL MAKES, NAMED RATHER THAN HIDDEN (the class-14 refinement: any step
# where a harness substitutes for the real environment re-introduces the model, and that step is
# where the residual bug lives). The MCP tool `agent_memory_recall` DISCARDS the identifiers its
# own implementation produces: `performRecall` returns { trace_id, items[].memory_id } and the
# tool handler renders only summary, content and use-policy (agent-memory.ts:558-563). So a
# caller that recalls through the door CANNOT name what it was told, and cannot call
# `agent_memory_report_usage`, which requires memory_id. This tool therefore recovers the ids
# from agent_memory_recall_traces / _recall_items directly, and PRINTS that it did so. It is a
# workaround for a real defect in the door, not a design; see the findings note.
#
#   .\recall-sibling-class.ps1 -Defect "<what you are about to declare fixed>"
#   .\recall-sibling-class.ps1 -ReportUsed <memory-id> -Trace <trace-id> -Note "<what it changed>"
#   .\recall-sibling-class.ps1 -ReportIgnored <memory-id> -Trace <trace-id> -Note "<why not>"
[CmdletBinding()]
param(
    [string]$Defect = "",
    [string]$ReportUsed = "",
    [string]$ReportIgnored = "",
    [string]$Trace = "",
    [string]$Note = "",
    [int]$Limit = 5,
    [string]$Workspace = "ai-stack",
    [string]$Url = "http://127.0.0.1:8062/mcp",
    [string]$Container = "openbrain-ops-gateway",
    [string]$DbContainer = "openbrain-db",
    [switch]$Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-DoorKey {
    if ($env:OPS_GATEWAY_KEY) { return $env:OPS_GATEWAY_KEY }
    $envs = & docker inspect $Container --format '{{range .Config.Env}}{{println .}}{{end}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $envs) { return $null }
    foreach ($line in @($envs)) { if ($line -match '^GATEWAY_KEY=(.+)$') { return $Matches[1].Trim() } }
    return $null
}

function Invoke-OpsTool {
    param([string]$Key, [string]$Tool, $Arguments)
    $body = @{ jsonrpc = "2.0"; id = 1; method = "tools/call"
               params = @{ name = $Tool; arguments = $Arguments } } | ConvertTo-Json -Depth 12 -Compress
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Post -UseBasicParsing `
                -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) -ContentType "application/json" `
                -Headers @{ "Authorization" = "Bearer $Key"; "Accept" = "application/json, text/event-stream" }
    } catch { return @{ ok = $false; error = ("transport: " + $_.Exception.Message); text = "" } }
    $raw = [string]$resp.Content
    $json = $raw
    if ($raw -match '(?m)^data:\s*(\{.*\})\s*$') { $json = $Matches[1] }
    try { $obj = $json | ConvertFrom-Json } catch { return @{ ok = $false; error = "unparseable response"; text = $raw } }
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
    return @{ exit = $LASTEXITCODE; out = ((@($out) -join "`n").Trim()) }
}

$key = Get-DoorKey
if (-not $key) {
    Write-Host "RECALL UNAVAILABLE - no ops door key (container '$Container' unreadable and OPS_GATEWAY_KEY unset)." -ForegroundColor Red
    Write-Host "  This is NOT 'the plane had nothing to say'. The plane was not asked." -ForegroundColor Red
    exit 5
}

# --- report modes -------------------------------------------------------------------
if ($ReportUsed -or $ReportIgnored) {
    $mid = if ($ReportUsed) { $ReportUsed } else { $ReportIgnored }
    $used = [bool]$ReportUsed
    if (-not $Note) { Write-Host "-Note is required: a usage report with no reason records that something happened without recording what." -ForegroundColor Red; exit 2 }
    $a = @{ memory_id = $mid; used = $used; workspace_id = $Workspace; note = $Note }
    if ($Trace) { $a["trace_id"] = $Trace }
    $r = Invoke-OpsTool -Key $key -Tool "agent_memory_report_usage" -Arguments $a
    if (-not $r.ok) { Write-Host ("report_usage FAILED: {0}" -f $r.error) -ForegroundColor Red; exit 5 }

    # POSITIVE CONTROL ON THE REPORT. "Recorded." is what the tool says; whether anything
    # landed is a separate question, and this effort's own class list says a probe that
    # accepts the answer instead of the effect is a check green while checking nothing.
    $ev = Invoke-Psql -Sql ("SELECT count(*) FROM agent_memory_audit_events WHERE memory_id='{0}' AND event_type='{1}';" -f `
                            ($mid -replace "'", "''"), $(if ($used) { "memory_used" } else { "memory_ignored" }))
    Write-Host ("usage reported: {0} used={1}" -f $mid, $used) -ForegroundColor Green
    if ($ev.exit -eq 0 -and $ev.out -match '^\d+$' -and [int]$ev.out -gt 0) {
        Write-Host ("  audit events for this memory of that kind: {0}" -f $ev.out)
    } else {
        Write-Host "  WARNING: the tool said Recorded and no audit row is visible." -ForegroundColor Yellow
    }
    # KNOWN GAP, printed every time so it is not forgotten: performReportUsage writes an
    # audit event and does NOT update agent_memory_recall_items.used / ignored_reason, which
    # is the column the schema designates for exactly this. Measured, not assumed.
    $it = Invoke-Psql -Sql ("SELECT count(*) FROM agent_memory_recall_items WHERE memory_id='{0}' AND used IS NOT NULL;" -f ($mid -replace "'", "''"))
    if ($it.exit -eq 0 -and $it.out -match '^\d+$' -and [int]$it.out -eq 0) {
        Write-Host "  NOTE: agent_memory_recall_items.used is still NULL for this memory." -ForegroundColor Yellow
        Write-Host "        agent_memory_report_usage writes an audit event only; the trace ITEM row is" -ForegroundColor Yellow
        Write-Host "        written by nothing. Recorded as a defect, not worked around silently." -ForegroundColor Yellow
    }
    exit 0
}

if (-not $Defect) {
    Write-Host "usage: -Defect '<what you are about to declare fixed>' | -ReportUsed <id> -Trace <id> -Note '<why>'" -ForegroundColor Red
    exit 2
}

# --- the recall ----------------------------------------------------------------------
$before = Invoke-Psql -Sql "SELECT count(*) FROM agent_memory_recall_traces;"
$r = Invoke-OpsTool -Key $key -Tool "agent_memory_recall" -Arguments @{
    workspace_id = $Workspace; query = $Defect; limit = $Limit
}
if (-not $r.ok) {
    Write-Host ("RECALL UNAVAILABLE - {0}" -f $r.error) -ForegroundColor Red
    Write-Host "  This is NOT 'the plane had nothing to say'." -ForegroundColor Red
    exit 5
}

# Recover the ids the door discards. Named substitution - see the header.
$traceId = ""; $items = @()
$q = ($Defect -replace "'", "''")
$t = Invoke-Psql -Sql ("SELECT id::text FROM agent_memory_recall_traces WHERE workspace_id='{0}' AND query='{1}' ORDER BY created_at DESC LIMIT 1;" -f ($Workspace -replace "'", "''"), $q)
if ($t.exit -eq 0 -and $t.out -match '^[0-9a-f-]{36}$') {
    $traceId = $t.out
    $i = Invoke-Psql -Sql ("SELECT i.rank||'|'||i.memory_id::text||'|'||round(i.similarity,4)||'|'||m.memory_type||'|'||replace(m.summary,'|','/') FROM agent_memory_recall_items i JOIN agent_memories m ON m.id=i.memory_id WHERE i.trace_id='{0}' ORDER BY i.rank;" -f $traceId)
    if ($i.exit -eq 0 -and $i.out) { $items = @($i.out -split "`n" | Where-Object { $_ -match '\S' }) }
}

$returned = $items.Count
$outcome = if ($returned -gt 0) { "INFORMED" } else { "EMPTY" }

if (-not $Quiet) {
    Write-Host ""
    Write-Host "ASKED THE MEMORY PLANE BEFORE DECLARING THIS FIXED" -ForegroundColor Cyan
    Write-Host ("  query    : {0}" -f $Defect)
    Write-Host ("  trace    : {0}" -f $(if ($traceId) { $traceId } else { "(not recoverable)" }))
    Write-Host ("  outcome  : {0} ({1} memory/memories returned)" -f $outcome, $returned)
    Write-Host ""
    Write-Host "  SUBSTITUTION, named: the door's recall response carries no memory_id and no trace_id" -ForegroundColor DarkGray
    Write-Host "  (agent-memory.ts:558-563 discards what performRecall returns), so the ids above were" -ForegroundColor DarkGray
    Write-Host "  read from agent_memory_recall_traces/_items by matching this exact query text." -ForegroundColor DarkGray
    Write-Host ""
    if ($returned -gt 0) {
        foreach ($line in $items) {
            $p = $line -split '\|', 5
            Write-Host ("  {0}. [{1}] sim {2}  {3}" -f $p[0], $p[3], $p[2], $p[1]) -ForegroundColor Green
            Write-Host ("     {0}" -f $p[4])
        }
        Write-Host ""
        Write-Host $r.text
        Write-Host ""
        Write-Host "  NOW REPORT USAGE. A memory returned and never reported is invisible to the plane," -ForegroundColor Yellow
        Write-Host "  and one recalled repeatedly and never used is the only signal that recall is" -ForegroundColor Yellow
        Write-Host "  surfacing the wrong thing:" -ForegroundColor Yellow
        Write-Host ("    .\recall-sibling-class.ps1 -ReportUsed <id> -Trace {0} -Note '<what it changed>'" -f $traceId)
        Write-Host ("    .\recall-sibling-class.ps1 -ReportIgnored <id> -Trace {0} -Note '<why not>'" -f $traceId)
    } else {
        Write-Host "  THE PLANE RETURNED NOTHING." -ForegroundColor Yellow
        Write-Host "  That is a MEASUREMENT, not a clearance. It means one of: the class is genuinely new;" -ForegroundColor Yellow
        Write-Host "  the query did not describe the shape the way the corpus does; or the similarity floor" -ForegroundColor Yellow
        Write-Host "  is mis-set for this corpus (bge-m3's default is OpenAI-tuned and calibration is" -ForegroundColor Yellow
        Write-Host "  blocked on corpus size). Do NOT read it as 'no known sibling'." -ForegroundColor Yellow
    }
    Write-Host ""
}

# Machine-readable line, for a caller that wires this into a gate.
Write-Host ("RECALL_OUTCOME={0} returned={1} trace={2} memories={3}" -f `
    $outcome, $returned, $traceId, (($items | ForEach-Object { ($_ -split '\|')[1] }) -join ","))

if ($outcome -eq "INFORMED") { exit 0 } else { exit 4 }
