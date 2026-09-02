# redprove-fixture-cleanup.ps1
#
# Prove that dfu-done.ps1's `fixture-cleaned-up` probe fails on the thing it is FOR, and
# does not fail on the thing it is not for. Both halves, because this probe was changed
# after it produced a wrong verdict, and "I changed it and it went green" is the weakest
# possible evidence for a check.
#
# WHAT WENT WRONG. The probe counted `metadata->>'exposure' = 'personal'` across the whole
# of thoughts and agent_memories and required ZERO. The 2026-09-01 live run reported
# `0/0/0/1129/0 - the plane was left dirty` while its own fixtures were 0/0/0 and the
# cleanup had worked perfectly: the 1,129 rows are the operator's deliberate resolution of
# a personal-exposure incident. A checker that demands their absence is demanding that
# personal data be deleted to make a check go green.
#
# THE TWO CASES, run against the REAL dfu-done.ps1 pointed at a throwaway database:
#
#   CASE A  legitimate personal rows are present and the probe cleaned up after itself
#           -> MUST PASS. (Under the old code this failed.)
#   CASE B  the probe's OWN fixture rows are stranded
#           -> MUST FAIL, and the note must name the fixture counts, not the personal ones.
#
# CASE B strands rows with a BEFORE DELETE trigger that returns NULL, which is how a
# cleanup that silently fails to remove its own rows actually looks. Nothing is stubbed and
# no verdict is injected: this file chooses the WORLD, dfu-done.ps1 decides the answer.
#
# It never touches production. `-DbContainer` points the script under test at the throwaway.
#
#   .\redprove-fixture-cleanup.ps1
#
# Exit 0 = both cases behaved. 1 = a case did not. 2 = could not measure.

[CmdletBinding()]
param([int]$DbTimeoutSec = 600)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

$script:net     = "wt-dfuc3-rp-net"
$script:db      = "wt-dfuc3-rp-db"
$script:initDir = Join-Path $env:TEMP "dfuc3-rp-initdb"
$script:target  = Join-Path $PSScriptRoot "dfu-done.ps1"
$script:fail    = 0

function Say  { param([string]$m) Write-Host $m }
function Head { param([string]$m) Write-Host ""; Write-Host $m -ForegroundColor Cyan }

function Cleanup {
    & docker rm -f $script:db 2>&1 | Out-Null
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

function Psql {
    param([string]$Query)
    return (& docker exec $script:db psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -Atc $Query 2>&1 | Out-String)
}

function Get-CleanupProbe {
    # Run the REAL script under test and pull the one probe out of its JSON. Anything that
    # stops it producing that probe is a CANNOT MEASURE, never a pass: a red-prove that
    # silently measured nothing is the defect it exists to catch, wearing a rosette.
    $raw = & $script:target -Only 3 -Json -DbContainer $script:db -ObNetwork $script:net 2>&1 | Out-String
    $jsonText = ($raw -split "(?m)^(?=\{)" | Select-Object -Last 1)
    try { $j = $jsonText | ConvertFrom-Json } catch {
        Say "  CANNOT MEASURE - dfu-done.ps1 did not emit parseable JSON."
        Cleanup; exit 2
    }
    $c = $j.clauses | Where-Object { $_.id -eq 3 }
    if (-not $c) { Say "  CANNOT MEASURE - clause 3 absent from the JSON."; Cleanup; exit 2 }
    $p = $c.probes | Where-Object { $_.name -eq "fixture-cleaned-up" }
    if (-not $p) {
        Say "  CANNOT MEASURE - clause 3 produced no fixture-cleaned-up probe. Clause verdict was '$($c.verdict)':"
        $c.probes | ForEach-Object { Say ("    {0}: {1}" -f $_.name, $_.note) }
        Cleanup; exit 2
    }
    return $p
}

function Assert-Case {
    param([string]$Case, [string]$Want, $Probe)
    if ($Probe.verdict -eq $Want) {
        Say ("  [ok]   {0}: verdict '{1}' as required" -f $Case, $Probe.verdict)
    } else {
        $script:fail++
        Say ("  [FAIL] {0}: wanted '{1}', got '{2}'" -f $Case, $Want, $Probe.verdict)
    }
    Say ("         note: {0}" -f $Probe.note)
}

# ==========================================================================================
Say "redprove-fixture-cleanup - can dfu-done.ps1's fixture-cleaned-up probe still fail?"

if (-not (Test-Path $script:target)) { Say "CANNOT MEASURE - $script:target missing"; exit 2 }

Head "0. throwaway database from the compose init chain"
. (Join-Path $PSScriptRoot "lib\ob-initdb.ps1")
$compose = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "OB1\docker\docker-compose.yml"
if (-not (Test-Path $compose)) { Say "  CANNOT MEASURE - $compose missing (uninitialised OB1 submodule?)"; exit 2 }
if (Test-Path $script:initDir) { Remove-Item -Recurse -Force $script:initDir }
New-Item -ItemType Directory -Force $script:initDir | Out-Null
$chain = @(Get-ObInitChain -ComposePath $compose)
if ($chain.Count -eq 0) { Say "  CANNOT MEASURE - derived no init files from compose"; Cleanup; exit 2 }
$null = Copy-ObInitChain -Chain $chain -SourceDir (Split-Path $compose -Parent) -TargetDir $script:initDir
& docker rm -f $script:db 2>&1 | Out-Null
& docker network rm $script:net 2>&1 | Out-Null
& docker network create $script:net 2>&1 | Out-Null
$boot = Start-ObInitdbDetailed -Name $script:db -InitDir $script:initDir -TimeoutSec $DbTimeoutSec -DockerArgs @("--network", $script:net)
if (-not $boot.Ready) { Say "  CANNOT MEASURE - throwaway did not initialise: $($boot.Detail)"; Cleanup; exit 2 }
Say "  up in $($boot.ElapsedSec)s"

# The legitimate personal rows. SEVEN, not zero, because the whole point is that a plane
# holding personal data is a NORMAL state that this probe must not object to.
$null = Psql "INSERT INTO thoughts (content, metadata, exposure, user_id) SELECT 'REDPROVE legitimate personal row '||g, jsonb_build_object('exposure','personal'), 'personal', 'operator' FROM generate_series(1,7) g;"
$n = (Psql "SELECT count(*) FROM thoughts WHERE exposure='personal';").Trim()
Say "  seeded $n legitimate personal row(s) that the probe did not write and must not remove"

Head "1. CASE A - legitimate personal rows present, probe cleans up after itself -> PASS"
Assert-Case -Case "case A" -Want "pass" -Probe (Get-CleanupProbe)

Head "2. CASE B - the probe's own fixture rows stranded -> FAIL"
# A cleanup that cannot delete is exactly what a stranded fixture is. Both a fixture THOUGHT
# and a recall TRACE are stranded, so the case covers the counter that always existed and
# the one this change added - a widened counter nobody red-proves is a widened counter that
# might not be wired to anything.
$null = Psql "CREATE OR REPLACE FUNCTION redprove_block_delete() RETURNS trigger LANGUAGE plpgsql AS `$f`$ BEGIN RETURN NULL; END `$f`$;"
$null = Psql "DROP TRIGGER IF EXISTS redprove_block_thoughts ON thoughts; CREATE TRIGGER redprove_block_thoughts BEFORE DELETE ON thoughts FOR EACH ROW WHEN (OLD.metadata->>'dfu_done_fixture'='true') EXECUTE FUNCTION redprove_block_delete();"
$null = Psql "DROP TRIGGER IF EXISTS redprove_block_traces ON agent_memory_recall_traces; CREATE TRIGGER redprove_block_traces BEFORE DELETE ON agent_memory_recall_traces FOR EACH ROW EXECUTE FUNCTION redprove_block_delete();"
$null = Psql "INSERT INTO agent_memory_recall_traces (workspace_id, query, schema_version) VALUES ('dfu-done-fixture','stranded trace from a previous run', 1);"
$probeB = Get-CleanupProbe
Assert-Case -Case "case B" -Want "fail" -Probe $probeB
# The note must blame the FIXTURE counters. A run that fails while quoting the personal
# count would be the old defect returning in a new spelling.
if ($probeB.note -match 'THIS PROBE left its own fixture rows behind') {
    Say "  [ok]   case B blames its own fixture rows, not production's personal rows"
} else {
    $script:fail++
    Say "  [FAIL] case B failed for the wrong reason - the note does not blame the fixture counters"
}

Head "result"
Say "  $(if ($script:fail -eq 0) { 'both cases behaved' } else { "$script:fail assertion(s) failed" })"
Cleanup
if ($script:fail -gt 0) { exit 1 }
exit 0
