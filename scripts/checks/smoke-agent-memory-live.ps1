# smoke-agent-memory-live.ps1 - PLAN 3's acceptance, against the LIVE plane.
#
# WHAT WAS UNPROVEN BEFORE THIS. `scripts/checks/smoke-agent-memory.ps1` starts a THROWAWAY
# server on a throwaway database with a STUB embedding endpoint, which is the right shape for
# proving the doors exist. It cannot prove the acceptance memory-plane PLAN 3 actually names:
# "a confirmed memory measurably appears in a worker brief; a pending one never does". Every
# other proof on this branch used httpx.MockTransport, and `agent_memory_recall_traces` had
# ZERO rows - recall had never run against a real Open Brain on any branch.
#
# So this is the opposite trade: the REAL openbrain-mcp, the REAL bge-m3 embedding lane, the
# REAL review gate in SQL, and the REAL orchestrator intake path. Only the worker harness and
# the chat adapter are fakes, because a GPU turn proves nothing about recall.
#
# ISOLATION IS BY FIXTURE, NOT BY ENVIRONMENT. Two SYNTHETIC memories are written through the
# live writeback door, stamped `ops` by the server, tagged with a run id, and DELETED here
# when the run finishes - pass or fail. The personal plane is never read or written, and the
# run fails if a personal row exists before or after (PLAN C.2 class 4). The recall TRACE rows
# are deliberately kept: they are the evidence that recall executed at all.
#
# The probe runs INSIDE a container on `obnet` because openbrain-mcp publishes no host port.
# It uses the agent-bridge image only as a Python runtime with the right dependencies; the
# code it runs is this worktree, bind-mounted. It is not a deploy.
#
#   .\scripts\checks\smoke-agent-memory-live.ps1
#   .\scripts\checks\smoke-agent-memory-live.ps1 -KeepFixtures   # leave them to inspect
#
# Exit: 0 = all checks passed | 1 = one or more failed

[CmdletBinding()]
param(
    [switch]$KeepFixtures,
    [string]$Image = "agent-bridge:local",
    [string]$Network = "open-brain_obnet"
)

$ErrorActionPreference = "Continue"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path

$fails = 0
function Section($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Pass($t) { Write-Host "  PASS  $t" -ForegroundColor Green }
function Fail($t) { Write-Host "  FAIL  $t" -ForegroundColor Red; $script:fails++ }

function Invoke-Psql([string]$Sql) {
    (docker exec openbrain-db psql -U postgres -d openbrain -tA -c $Sql | Out-String).Trim()
}

# LETTERS, NOT A TIMESTAMP. The first version tagged the fixtures with a 14-digit
# timestamp; `detectPii` treats any 13-16 digit run as a payment-card shape, so both
# fixtures were demoted to the personal plane, became invisible to the default recall
# scope, and the smoke failed for a reason that had nothing to do with recall.
$TAG = "u6live-" + (-join ((97..122) | Get-Random -Count 8 | ForEach-Object { [char]$_ }))

Section "preconditions"
$mcp = (docker ps --filter "name=^openbrain-mcp$" --format "{{.Names}}" | Out-String).Trim()
if ($mcp -eq "openbrain-mcp") { Pass "openbrain-mcp is running" }
else { Fail "openbrain-mcp is not running - start the Open Brain plane first"; exit 1 }

$hasRanking = (docker exec openbrain-mcp sh -c "ls /app" | Out-String)
if ($hasRanking -match "agent-memory-ranking.ts") {
    Pass "the deployed image carries the two-phase ranking module"
} else {
    Fail "the deployed openbrain-mcp predates agent-memory-ranking.ts - rebuild before smoking"
    exit 1
}

# CLASS-4 GUARD (PLAN C.2). There are zero personal-plane rows and there must be zero when
# this finishes. Asserted BEFORE anything is written, so a pre-existing row is never blamed
# on this run - and never quietly tolerated either.
$personalBefore = Invoke-Psql "SELECT count(*) FROM agent_memories WHERE COALESCE(metadata->>'exposure','personal') = 'personal'"
if ($personalBefore -eq "0") { Pass "zero personal-plane rows before the run" }
else { Fail "the plane holds $personalBefore personal rows - this smoke will not run against it"; exit 1 }

$memBefore   = Invoke-Psql "SELECT count(*) FROM agent_memories"
$traceBefore = Invoke-Psql "SELECT count(*) FROM agent_memory_recall_traces"
Write-Host "  corpus before: $memBefore memories, $traceBefore recall traces" -ForegroundColor DarkGray

$envFile = Join-Path $root "OB1\docker\.env"
if (-not (Test-Path $envFile)) { Fail "OB1/docker/.env not found - cannot read MCP_ACCESS_KEY"; exit 1 }
$key = ""
foreach ($line in (Get-Content $envFile)) {
    if ($line -match '^\s*MCP_ACCESS_KEY\s*=\s*(.+)\s*$') { $key = $Matches[1].Trim().Trim('"') }
}
if ($key) { Pass "MCP_ACCESS_KEY read from OB1/docker/.env (value never printed or persisted)" }
else { Fail "MCP_ACCESS_KEY is not set in OB1/docker/.env"; exit 1 }

Section "one real effort, against the real plane"
$out = docker run --rm --network $Network `
    -v "${root}:/w:ro" -w /w `
    -e "OB_URL=http://openbrain-mcp:8000" -e "OB_KEY=$key" `
    -e "OB_TAG=$TAG" -e "OB_PROJECT=u6-live-smoke" `
    -e "PYTHONDONTWRITEBYTECODE=1" `
    $Image python /w/scripts/checks/live_recall_probe.py 2>&1
$probeExit = $LASTEXITCODE
$text = ($out | Out-String)
Write-Host $text

if ($probeExit -eq 0) { Pass "every probe assertion held (see the JSON above)" }
else { Fail "the probe reported a failure (exit $probeExit)" }

Section "the plane recorded what happened"
$traceAfter = Invoke-Psql "SELECT count(*) FROM agent_memory_recall_traces"
if ([int]$traceAfter -gt [int]$traceBefore) {
    Pass "recall wrote a trace row on the live plane ($traceBefore -> $traceAfter)"
} else {
    Fail "no recall trace was written - the seam never reached the real server"
}
$items = Invoke-Psql "SELECT count(*) FROM agent_memory_recall_items i JOIN agent_memories m ON m.id = i.memory_id WHERE m.idempotency_key LIKE '$TAG%'"
if ([int]$items -ge 1) { Pass "the trace records which fixture was returned ($items recall item(s))" }
else { Fail "the recall trace has no items for this run's fixtures" }

Section "cleanup - the fixtures leave, the evidence stays"
if ($KeepFixtures) {
    Write-Host "  -KeepFixtures: leaving rows tagged $TAG in place" -ForegroundColor Yellow
} else {
    # Order matters: the memories first (their review actions and recall items cascade), then
    # the thoughts they were embedded into. Recall TRACES are kept on purpose - they are the
    # record that recall executed, which is the thing this run exists to produce.
    $thoughtIds = Invoke-Psql "SELECT string_agg(thought_id::text, ',') FROM agent_memories WHERE idempotency_key LIKE '$TAG%'"
    $null = Invoke-Psql "DELETE FROM agent_memories WHERE idempotency_key LIKE '$TAG%'"
    if ($thoughtIds) { $null = Invoke-Psql "DELETE FROM thoughts WHERE id IN ($thoughtIds)" }
    $left = Invoke-Psql "SELECT count(*) FROM agent_memories WHERE idempotency_key LIKE '$TAG%'"
    if ($left -eq "0") { Pass "both synthetic fixtures deleted" }
    else { Fail "$left fixture row(s) survived cleanup" }

    $memAfter = Invoke-Psql "SELECT count(*) FROM agent_memories"
    if ($memAfter -eq $memBefore) { Pass "the corpus is back where it started ($memAfter memories)" }
    else { Fail "corpus was $memBefore before and is $memAfter after" }
}

$personalAfter = Invoke-Psql "SELECT count(*) FROM agent_memories WHERE COALESCE(metadata->>'exposure','personal') = 'personal'"
if ($personalAfter -eq "0") { Pass "still zero personal-plane rows" }
else { Fail "this run left $personalAfter personal-plane rows behind" }

Write-Host ""
if ($fails -eq 0) { Write-Host "ALL LIVE CHECKS PASSED" -ForegroundColor Green; exit 0 }
Write-Host "$fails LIVE CHECK(S) FAILED" -ForegroundColor Red
exit 1
