# drill-rls-boot-assertion.ps1 - THE DELIVERABLE FOR dark-factory-unification C.9 H2.
#
# H2's validated-by clause, quoted literally:
#
#   "a startup assertion (health gate or migration check) that refuses to serve - or alarms
#    at minimum - if `relforcerowsecurity` is false on any of the nine governed tables;
#    proven by a drill that brings a DB up without the migration and shows the assertion
#    fires."
#
# The assertion is scripts/checks/assert-rls-force.sh. THIS is the deliverable: it brings
# databases up on THROWAWAY containers, RED first, and shows the assertion firing on each way
# the boundary can be absent - including the two that produce a vacuous green if you are not
# looking for them (a governed set derived from a source that itself went missing, and an
# assertion that could not reach the database at all).
#
# THROWAWAY ONLY. Containers are named `ob-h2-*`, run on docker's default bridge, are never
# attached to any `ai-stack_*` anchor network, build no image and tag nothing `:local`. The
# live openbrain-db is never touched, read or written.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\drill-rls-boot-assertion.ps1
#         -KeepContainers   leave the throwaway DBs up for inspection
#         -SkipComposeGate  skip section 9 (the compose refuse-to-serve wiring), which is the
#                           slowest part - two more full initdb chains
#
# Exit 0 = every scenario behaved as required. Exit 1 = at least one did not.

[CmdletBinding()]
param(
    [switch]$KeepContainers,
    [switch]$SkipComposeGate
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'lib\ob-initdb.ps1')

$RepoRoot   = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Ob1Docker  = Join-Path $RepoRoot 'OB1\docker'
$ComposeYml = Join-Path $Ob1Docker 'docker-compose.yml'
$AssertSh   = Join-Path $PSScriptRoot 'assert-rls-force.sh'

$script:Pass = 0
$script:Fail = 0
function Pass($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Fail($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Section($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

# ---------------------------------------------------------------------------------------
# Run the assertion INSIDE a container, which is the shape production uses (the healthcheck
# runs it in openbrain-db). Returns @{ Code; Out }.
# ---------------------------------------------------------------------------------------
function Invoke-Assert {
    param(
        [Parameter(Mandatory)][string]$Container,
        [string[]]$EnvArgs = @()
    )
    $argv = @('exec') + $EnvArgs + @($Container, 'sh', '/opt/ob-migrations/assert-rls-force.sh')
    $out = (& docker @argv 2>&1 | Out-String)
    return @{ Code = $LASTEXITCODE; Out = $out }
}

function Invoke-Sql {
    param([Parameter(Mandatory)][string]$Container, [Parameter(Mandatory)][string]$Sql)
    $out = (& docker exec $Container psql -U postgres -d openbrain -tA -v ON_ERROR_STOP=1 -c $Sql 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { throw "psql failed on ${Container}: $out" }
    return $out.Trim()
}

# Assert on the SHAPE of the result, never on "it did not crash".
function Expect {
    param(
        [Parameter(Mandatory)][hashtable]$R,
        [Parameter(Mandatory)][int]$Code,
        [string[]]$Contains = @(),
        [string[]]$NotContains = @(),
        [Parameter(Mandatory)][string]$What
    )
    $ok = $true
    $why = @()
    # Collapse whitespace before matching. docker writes the assertion's stderr as PowerShell
    # error records, which Out-String HARD-WRAPS at the console width - so a needle like
    # "absent from the database: drill_new_table" is split by a line break that has nothing to
    # do with the assertion. Matching the raw text made a correct assertion look broken.
    $flat = ($R.Out -replace '\s+', ' ')
    if ($R.Code -ne $Code) { $ok = $false; $why += "exit $($R.Code), wanted $Code" }
    foreach ($c in $Contains)    { $n = ($c -replace '\s+', ' '); if ($flat -notmatch [regex]::Escape($n)) { $ok = $false; $why += "output lacks '$c'" } }
    foreach ($c in $NotContains) { $n = ($c -replace '\s+', ' '); if ($flat -match  [regex]::Escape($n))   { $ok = $false; $why += "output should not name '$c'" } }
    if ($ok) { Pass $What } else { Fail "$What -- $($why -join '; ')`n        $(($R.Out -split "`n" | Where-Object { $_.Trim() }) -join "`n        ")" }
}

$tmpRoot   = Join-Path $env:TEMP ("ob-h2-drill-" + [guid]::NewGuid().ToString('N').Substring(0,8))
$srcDir    = Join-Path $tmpRoot 'src'        # the migrations SOURCE dir  -> /opt/ob-migrations
$chainFull = Join-Path $tmpRoot 'chain-full' # every migration compose mounts
$chainNoRl = Join-Path $tmpRoot 'chain-norls'# the same chain MINUS the three *-rls migrations
$containers = @()

try {
    New-Item -ItemType Directory $tmpRoot -Force | Out-Null

    # -----------------------------------------------------------------------------------
    Section "0. Stage the real initdb chain (derived from compose, not hand-listed)"
    if (-not (Test-Path $AssertSh)) { Fail "assertion script missing at $AssertSh"; throw "cannot drill without the assertion" }
    if (-not (Test-Path $ComposeYml)) { Fail "OB1 compose missing at $ComposeYml (submodule not initialised?)"; throw "cannot drill" }

    $chain = Get-ObInitChain -ComposePath $ComposeYml
    if ($chain.Count -lt 20) { Fail "derived only $($chain.Count) initdb mounts from compose - the chain parse is broken" }
    else { Pass "initdb chain derived from compose ($($chain.Count) migrations)" }

    # The migrations SOURCE directory: every .sql OB1/docker holds, plus the assertion itself,
    # exactly as production would mount `./` at /opt/ob-migrations.
    New-Item -ItemType Directory $srcDir -Force | Out-Null
    Copy-Item (Join-Path $Ob1Docker '*.sql') $srcDir
    Copy-Item $AssertSh (Join-Path $srcDir 'assert-rls-force.sh')
    $srcSqlCount = (Get-ChildItem $srcDir -Filter *.sql).Count
    Pass "migrations source staged ($srcSqlCount .sql, including the revert-*.sql the scan must ignore)"

    $nFull = Copy-ObInitChain -Chain $chain -SourceDir $Ob1Docker -TargetDir $chainFull

    # "WITHOUT the migration" is itself DERIVED, not a filename guess: a chain file is a
    # boundary migration exactly when it DECLARES a FORCE. Filtering on the substring 'rls'
    # would have kept 190-init-agent-memory-corpus-failclosed.sql (declares none, correctly
    # kept) and would silently drop a future boundary migration named anything else.
    $forceRx = [regex]'(?is)ALTER\s+TABLE\s+(?:ONLY\s+)?(?:public\.)?[a-z_][a-z0-9_]*\s+FORCE\s+ROW\s+LEVEL\s+SECURITY'
    $rlsMounts = @($chain | Where-Object { $forceRx.IsMatch((Get-Content -Raw (Join-Path $Ob1Docker $_[0]))) })
    if ($rlsMounts.Count -lt 2) { Fail "expected at least 2 FORCE-declaring migrations in the chain; found $($rlsMounts.Count)" }
    else { Pass "boundary migrations derived by content: $((($rlsMounts | ForEach-Object { $_[1] }) -join ', '))" }
    $rlsNames = @($rlsMounts | ForEach-Object { $_[1] })
    $chainMinus = @($chain | Where-Object { $rlsNames -notcontains $_[1] })
    $nNoRls = Copy-ObInitChain -Chain $chainMinus -SourceDir $Ob1Docker -TargetDir $chainNoRl
    Pass "staged two chains: full=$nFull, without-the-migration=$nNoRls"

    $srcMount   = ($srcDir    -replace '\\', '/')
    $fullMount  = ($chainFull -replace '\\', '/')
    $noRlsMount = ($chainNoRl -replace '\\', '/')

    # -----------------------------------------------------------------------------------
    Section "1. RED FIRST - a database brought up WITHOUT the migration"
    $containers += 'ob-h2-nomig'
    $upA = Start-ObInitdb -Name 'ob-h2-nomig' -InitDir $noRlsMount -DockerArgs @('-v', "${srcMount}:/opt/ob-migrations:ro")
    if (-not $upA) { Fail "ob-h2-nomig initdb did not complete in 180s"; throw "cannot drill" }
    $errA = @(Get-ObInitdbErrors -Name 'ob-h2-nomig')
    if ($errA.Count) { Fail "unexpected errors in the without-migration chain: $($errA -join ' | ')" } else { Pass "without-migration chain initialised clean" }

    $liveForced = Invoke-Sql -Container 'ob-h2-nomig' -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity;"
    if ($liveForced -eq '0') { Pass "precondition: 0 tables FORCEd on the un-migrated database" }
    else { Fail "precondition broken: $liveForced tables already FORCEd without the migration" }

    $r = Invoke-Assert -Container 'ob-h2-nomig'
    Expect -R $r -Code 1 -Contains @('EXPOSURE BOUNDARY NOT ASSERTED', 'relforcerowsecurity=false', 'agent_memories', 'thoughts', 'entity_extraction_queue', 'idea_revisions') `
           -What "assertion FIRES on a DB brought up without the migration, and names the tables"

    # (b) relforcerowsecurity is the thing being checked, not relrowsecurity. On this DB the
    # agent_memory_* tables have relrowsecurity=TRUE already (init-agent-memory.sql enables it)
    # and FORCE false - the exact pair recorded at the top of init-agent-memory-rls.sql.
    $pair = Invoke-Sql -Container 'ob-h2-nomig' -Sql "SELECT relrowsecurity::text||'/'||relforcerowsecurity::text FROM pg_class WHERE relname='agent_memories' AND relnamespace='public'::regnamespace;"
    if ($pair -eq 'true/false') { Pass "the case relrowsecurity=t + relforcerowsecurity=f is present and is what fired (agent_memories)" }
    else { Fail "expected agent_memories to be true/false on the un-migrated DB, got '$pair'" }

    # The RAISE WARNING channel: `docker logs` must carry it, because that is what an operator
    # who is not looking for this actually reads.
    Start-Sleep 1
    $dblog = (& docker logs ob-h2-nomig 2>&1 | Out-String)
    if ($dblog -match 'assert-rls-force') { Pass "the failure is in the POSTGRES SERVER LOG (docker logs openbrain-db), not only in the healthcheck's stderr" }
    else { Fail "nothing in docker logs names assert-rls-force - the alarm channel an operator actually reads is silent" }

    # -----------------------------------------------------------------------------------
    Section "2. GREEN - the same chain WITH the migration"
    $containers += 'ob-h2-mig'
    $upB = Start-ObInitdb -Name 'ob-h2-mig' -InitDir $fullMount -DockerArgs @('-v', "${srcMount}:/opt/ob-migrations:ro")
    if (-not $upB) { Fail "ob-h2-mig initdb did not complete in 180s"; throw "cannot drill" }
    $errB = @(Get-ObInitdbErrors -Name 'ob-h2-mig')
    if ($errB.Count) { Fail "unexpected errors in the full chain: $($errB -join ' | ')" } else { Pass "full chain initialised clean" }

    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "assertion PASSES on a correctly migrated database"

    # (a) DERIVED, not hand-listed. The count the assertion checks must equal the count the
    # catalogue actually FORCEs, and it must be the SEVENTEEN the migrations declare - not the
    # NINE the plan's sentence names.
    $forcedLive = Invoke-Sql -Container 'ob-h2-mig' -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity;"
    if ($forcedLive -eq '17') { Pass "derived set (17) equals the catalogue's FORCEd set (17) - the plan's 'nine' would have checked 9 of 17" }
    else { Fail "catalogue FORCEs $forcedLive tables but the assertion reported 17 - the derivation and the database disagree" }

    # -----------------------------------------------------------------------------------
    Section "3. RLS ENABLED but FORCE OFF - the guarantee that is not the same guarantee"
    Invoke-Sql -Container 'ob-h2-mig' -Sql "ALTER TABLE public.thoughts NO FORCE ROW LEVEL SECURITY;" | Out-Null
    $st = Invoke-Sql -Container 'ob-h2-mig' -Sql "SELECT relrowsecurity::text||'/'||relforcerowsecurity::text FROM pg_class WHERE relname='thoughts' AND relnamespace='public'::regnamespace;"
    if ($st -eq 'true/false') { Pass "set thoughts to relrowsecurity=t, relforcerowsecurity=f" } else { Fail "could not create the RLS-on/FORCE-off state (got '$st')" }
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on: thoughts') -NotContains @('row level security DISABLED') `
           -What "assertion FIRES on RLS-enabled-but-FORCE-off, and says so as FORCE, not as RLS"
    Invoke-Sql -Container 'ob-h2-mig' -Sql "ALTER TABLE public.thoughts FORCE ROW LEVEL SECURITY;" | Out-Null

    # -----------------------------------------------------------------------------------
    Section "4. EIGHT OF NINE - a partial promotion, which is what a hand-list cannot see"
    # 180 + 190 applied, 200 (the graph plane) not. The nine tables the plan's sentence names
    # are ALL correctly FORCEd here; eight others are not. A hand-list of nine passes this.
    $graph = @('thought_entities','entity_extraction_queue','thought_edges','idea_revisions','entities','edges','source_entities','consolidation_log')
    foreach ($t in $graph) { Invoke-Sql -Container 'ob-h2-mig' -Sql "ALTER TABLE public.$t NO FORCE ROW LEVEL SECURITY;" | Out-Null }
    $nineOk = Invoke-Sql -Container 'ob-h2-mig' -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND c.relforcerowsecurity AND (c.relname LIKE 'agent_memor%' OR c.relname='thoughts');"
    if ($nineOk -eq '9') { Pass "precondition: the NINE tables the plan names are all still FORCEd (a hand-list of nine passes this state)" }
    else { Fail "expected the nine plan-named tables to remain FORCEd, got $nineOk" }
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on:','thought_entities','entity_extraction_queue','idea_revisions','consolidation_log') -NotContains @('agent_memories') `
           -What "assertion FIRES on the partial promotion and names exactly the eight ungoverned tables"
    foreach ($t in $graph) { Invoke-Sql -Container 'ob-h2-mig' -Sql "ALTER TABLE public.$t FORCE ROW LEVEL SECURITY;" | Out-Null }
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "and goes green again once the eight are FORCEd (the check tracks state, it is not stuck)"

    # -----------------------------------------------------------------------------------
    Section "5. A TABLE ADDED TO THE GOVERNED SET BUT NEVER MIGRATED"
    # The growth case (a) asks about: a tenth/eighteenth table becomes governed. Nobody edits
    # the assertion; a migration declares FORCE on it. Three sub-cases, because they fail
    # differently and an assertion that only catches one of them is a trap.
    $newMigSrc   = Join-Path $srcDir    '210-init-drill-newtable.sql'
    $newMigChain = Join-Path $chainFull '210-init-drill-newtable.sql'
    $newSql = "ALTER TABLE public.drill_new_table FORCE ROW LEVEL SECURITY;`n"
    [IO.File]::WriteAllText($newMigSrc, $newSql, (New-Object Text.UTF8Encoding($false)))

    # 5a - declared, in the chain, but the table does not exist at all.
    [IO.File]::WriteAllText($newMigChain, $newSql, (New-Object Text.UTF8Encoding($false)))
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 1 -Contains @('governed table absent from the database: drill_new_table') `
           -What "5a: a newly governed table that does not exist is a VIOLATION, not an omission"

    # 5b - the table exists, RLS on, FORCE never applied.
    Invoke-Sql -Container 'ob-h2-mig' -Sql "CREATE TABLE public.drill_new_table (id int); ALTER TABLE public.drill_new_table ENABLE ROW LEVEL SECURITY;" | Out-Null
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on: drill_new_table') `
           -What "5b: a newly governed table whose FORCE was never applied fires - with NO edit to the assertion"

    # 5c - THE VACUOUS GREEN. The declaration is in the source directory but its file is not in
    # the initdb chain: this database is fine, and the next fresh volume is not. An assertion
    # that read only the chain would find nothing to check and report success.
    Remove-Item $newMigChain -Force
    Invoke-Sql -Container 'ob-h2-mig' -Sql "ALTER TABLE public.drill_new_table FORCE ROW LEVEL SECURITY;" | Out-Null
    $forcedNow = Invoke-Sql -Container 'ob-h2-mig' -Sql "SELECT relforcerowsecurity::text FROM pg_class WHERE relname='drill_new_table' AND relnamespace='public'::regnamespace;"
    if ($forcedNow -eq 'true') { Pass "precondition: this database is now fully compliant (18/18 FORCEd)" } else { Fail "could not FORCE drill_new_table (got '$forcedNow')" }
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 1 -Contains @('NOT in the initdb chain', 'drill_new_table') `
           -What "5c: a compliant DB still FAILS when a governed table is missing from the chain (the next restore would be unprotected)"

    Remove-Item $newMigSrc -Force
    Invoke-Sql -Container 'ob-h2-mig' -Sql "DROP TABLE public.drill_new_table;" | Out-Null
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "and back to green once the scratch migration is withdrawn"

    # -----------------------------------------------------------------------------------
    Section "6. THE ASSERTION CANNOT REACH THE DATABASE - and must not report healthy"
    # This is the trap that matters: every vacuous green in this effort had the shape
    # 'the check could not run, so nothing failed'.
    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','PGHOST=127.0.0.1','-e','PGPORT=1')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'CANNOT CHECK IS NOT PASSED') -NotContains @('OK -') `
           -What "6a: connection refused -> exit 3, explicitly NOT a pass"

    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','POSTGRES_DB=no_such_database')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK') -NotContains @('OK -') -What "6b: database does not exist -> exit 3"

    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','POSTGRES_USER=no_such_role')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK') -NotContains @('OK -') -What "6c: role does not exist -> exit 3"

    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','PSQL_BIN=/nonexistent/psql')
    Expect -R $r -Code 3 -Contains @('no psql on PATH') -What "6d: no psql binary -> exit 3"

    # -----------------------------------------------------------------------------------
    Section "7. THE DERIVATION SOURCE ITSELF GOES MISSING - zero governed tables is not a pass"
    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','RLS_MIGRATIONS_DIR=/opt/does-not-exist')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'is not a directory') -What "7a: migrations directory absent -> exit 3"

    # An EMPTY but present directory is the nastier one: find succeeds, grep finds nothing,
    # and a naive implementation reports 'all 0 governed tables are compliant'.
    & docker exec ob-h2-mig sh -c 'mkdir -p /tmp/emptymig' | Out-Null
    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','RLS_MIGRATIONS_DIR=/tmp/emptymig')
    Expect -R $r -Code 3 -Contains @('derived ZERO governed tables') -NotContains @('OK -') `
           -What "7b: an EMPTY migrations directory -> exit 3, not 'zero tables, all compliant'"

    # And the revert files must not be what the scan reads - revert-*-rls.sql says NO FORCE.
    $r = Invoke-Assert -Container 'ob-h2-mig'
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') `
           -What "7c: revert-*.sql sitting in the same directory does not corrupt the derived set"

    # -----------------------------------------------------------------------------------
    Section "8. THE RESTORE PATH - H2 names 'a restore' first, so restore something"
    # A restore is not the same event as a rebuild: initdb does not run at all, and whether the
    # boundary survives depends entirely on whether the DUMP carried it. pg_dump emits
    # `ALTER TABLE ... FORCE ROW LEVEL SECURITY`, so a dump taken AFTER the migration restores
    # protected - and a dump taken before it restores a database that looks complete, has every
    # table and every row, and is silently FORCE-off. That second case is the H2 scenario.
    $dumpNew = (& docker exec ob-h2-mig pg_dump -U postgres -s openbrain 2>&1 | Out-String)
    if ($dumpNew -match 'FORCE ROW LEVEL SECURITY') { Pass "8a: a post-migration pg_dump CARRIES the FORCE clauses (a current backup restores protected)" }
    else { Fail "8a: pg_dump of the migrated DB does not contain FORCE ROW LEVEL SECURITY - the whole restore assumption is wrong" }

    $dumpOldPath = Join-Path $tmpRoot 'stale.sql'
    (& docker exec ob-h2-nomig pg_dump -U postgres -s openbrain 2>&1 | Out-String) | Set-Content -LiteralPath $dumpOldPath -Encoding UTF8
    $stale = Get-Content -Raw $dumpOldPath
    if ($stale -notmatch 'FORCE ROW LEVEL SECURITY') { Pass "8b: the pre-migration dump carries no FORCE clauses (this is the stale backup)" }
    else { Fail "8b: the pre-migration dump unexpectedly contains FORCE clauses" }

    # Restore it into a fresh database in the SAME cluster (roles are cluster-wide, so the
    # GRANTs in the dump resolve) and point the assertion at it. Nothing about this database is
    # obviously broken; it just is not protected.
    Invoke-Sql -Container 'ob-h2-mig' -Sql "SELECT 1;" | Out-Null
    & docker exec ob-h2-mig psql -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS restored;" 2>&1 | Out-Null
    & docker exec ob-h2-mig psql -U postgres -d postgres -q -c "CREATE DATABASE restored;" 2>&1 | Out-Null
    & docker cp $dumpOldPath ob-h2-mig:/tmp/stale.sql 2>&1 | Out-Null
    & docker exec ob-h2-mig sh -c 'psql -U postgres -d restored -q -f /tmp/stale.sql' 2>&1 | Out-Null
    $restoredTables = (& docker exec ob-h2-mig psql -U postgres -d restored -tA -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>&1 | Out-String).Trim()
    if ([int]$restoredTables -gt 20) { Pass "8c: the stale dump restored ($restoredTables tables in public) - a database that looks complete" }
    else { Fail "8c: restore produced only $restoredTables tables; the scenario did not set up" }
    $r = Invoke-Assert -Container 'ob-h2-mig' -EnvArgs @('-e','POSTGRES_DB=restored')
    Expect -R $r -Code 1 -Contains @('EXPOSURE BOUNDARY NOT ASSERTED', 'relforcerowsecurity=false') `
           -What "8d: the assertion FIRES on a restore from a stale dump - the case where nothing else looks wrong"
    & docker exec ob-h2-mig psql -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS restored;" 2>&1 | Out-Null

    # -----------------------------------------------------------------------------------
    if (-not $SkipComposeGate) {
        Section "9. REFUSE TO SERVE - the wiring, not the script"
        # The claim under test is not 'the script exits 1'. It is 'a dependent that gates on
        # openbrain-db's health DOES NOT START'. Proven on a throwaway compose project with the
        # production healthcheck shape and a dependent using condition: service_healthy.
        $proj = Join-Path $tmpRoot 'compose'
        New-Item -ItemType Directory $proj -Force | Out-Null
        $composeText = @"
name: ob-h2-gate
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: openbrain
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: test
    volumes:
      - CHAINDIR:/docker-entrypoint-initdb.d:ro
      - SRCDIR:/opt/ob-migrations:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d openbrain && sh /opt/ob-migrations/assert-rls-force.sh"]
      interval: 5s
      timeout: 20s
      retries: 3
      start_period: 120s
  dependent:
    image: pgvector/pgvector:pg16
    depends_on:
      db:
        condition: service_healthy
    command: ["sh", "-c", "echo DEPENDENT-STARTED; sleep 300"]
"@
        foreach ($case in @(@{ n = 'red'; dir = $noRlsMount; want = $false }, @{ n = 'green'; dir = $fullMount; want = $true })) {
            $f = Join-Path $proj "docker-compose.$($case.n).yml"
            [IO.File]::WriteAllText($f, ($composeText -replace 'CHAINDIR', $case.dir -replace 'SRCDIR', $srcMount -replace 'ob-h2-gate', "ob-h2-gate-$($case.n)"), (New-Object Text.UTF8Encoding($false)))
            & docker compose -f $f down -v --remove-orphans 2>&1 | Out-Null
            $upOut = (& docker compose -f $f up -d --wait --wait-timeout 200 2>&1 | Out-String)
            $depOut = (& docker compose -f $f logs dependent 2>&1 | Out-String)
            $started = $depOut -match 'DEPENDENT-STARTED'
            $health = (& docker compose -f $f ps --format json 2>&1 | Out-String)
            if ($case.want) {
                if ($started) { Pass "9-GREEN: with the migration, db goes healthy and the dependent STARTS" }
                else { Fail "9-GREEN: dependent never started against a correctly migrated db`n        up: $upOut`n        ps: $health" }
            } else {
                if (-not $started) { Pass "9-RED: WITHOUT the migration the dependent NEVER STARTS - this is the refusal, not a log line" }
                else { Fail "9-RED: dependent started against an unprotected db - the health gate did not refuse" }
                if ($upOut -match 'unhealthy|timeout|dependency failed') { Pass "9-RED: `docker compose up` fails loudly (dependency failed / unhealthy)" }
                else { Fail "9-RED: compose up did not report the failure: $upOut" }
                $insp = (& docker inspect --format '{{json .State.Health}}' "ob-h2-gate-red-db-1" 2>&1 | Out-String)
                if ($insp -match 'EXPOSURE BOUNDARY NOT ASSERTED') { Pass "9-RED: the reason is in docker inspect .State.Health, naming the boundary" }
                else { Fail "9-RED: health output does not carry the reason: $insp" }

                # THE LIMIT OF "REFUSE TO SERVE", MEASURED RATHER THAN ASSUMED. A docker
                # healthcheck gates DEPENDENTS; it does not gate the socket. An unhealthy
                # postgres still answers anyone who connects anyway. So the refusal is real at
                # the boot/dependency edge - which is exactly the event H2 names (a restore, a
                # rebuild, a skipped promotion) - and for a database that goes bad WHILE
                # RUNNING it is an alarm plus a refusal of the next dependent start. Claiming
                # more than that would be a claim nobody ran.
                $stillServes = (& docker exec ob-h2-gate-red-db-1 psql -U postgres -d openbrain -tA -c "SELECT 'served';" 2>&1 | Out-String)
                $hs = (& docker inspect --format '{{.State.Health.Status}}' "ob-h2-gate-red-db-1" 2>&1 | Out-String).Trim()
                if ($stillServes -match 'served' -and $hs -eq 'unhealthy') {
                    Pass "9-RED: MEASURED LIMIT - the unhealthy db still answers a direct connection, so the refusal is at the DEPENDENCY edge, not the socket"
                } else {
                    Fail "9-RED: expected an unhealthy-but-answering db (health='$hs', query='$($stillServes.Trim())') - the documented limit could not be measured"
                }
            }
            if (-not $KeepContainers) { & docker compose -f $f down -v --remove-orphans 2>&1 | Out-Null }
        }
    } else {
        Write-Host "`n== 9. SKIPPED (-SkipComposeGate): the refuse-to-serve wiring was NOT proven this run" -ForegroundColor Yellow
    }
}
catch {
    Fail "drill aborted: $($_.Exception.Message)"
}
finally {
    if (-not $KeepContainers) {
        foreach ($c in $containers) { & docker rm -f $c 2>&1 | Out-Null }
        Remove-Item $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "`nkept: $($containers -join ', ')  (temp: $tmpRoot)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "passed $script:Pass, failed $script:Fail" -ForegroundColor $(if ($script:Fail) { 'Red' } else { 'Green' })
if ($script:Fail) { exit 1 } else { exit 0 }
