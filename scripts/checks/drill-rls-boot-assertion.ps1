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
# the boundary can be absent - including the ones that produce a vacuous green if you are not
# looking for them (a governed set derived from a source that itself went missing, an
# assertion that could not reach the database at all, and - added after verifiers found it -
# a PARSE that silently narrowed the governed set while reporting OK).
#
# Sections 0-8 are the assertion's behaviour, 9 is the compose wiring that makes it a refusal,
# and 10-14 are round 2: the declaration forms a regex drops (each proven in BOTH directions,
# because deriving a table is worthless if the catalogue lookup then cannot see it), the
# residue channel that makes an unreadable form loud, the completeness backstop, the file
# types and places the old scan could not reach, and the runtime against the 5s
# healthcheck.timeout.
#
# THROWAWAY ONLY. Containers are named `ob-h2-*-<run id>`, run on docker's default bridge, are
# never attached to any `ai-stack_*` anchor network, build no image and tag nothing `:local`.
# The live openbrain-db is never touched, read or written. The run id exists because fixed
# names made two concurrent drills delete each other's databases.
#
# Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\drill-rls-boot-assertion.ps1
#         -KeepContainers   leave the throwaway DBs up for inspection
#         -SkipComposeGate  skip section 9 (the compose refuse-to-serve wiring), which is the
#                           slowest part - two more full initdb chains
#
# EXIT CODES - a timeout is NOT a finding, and the exit code has to say so.
#
#   0  every scenario behaved as required.
#   1  FAIL - at least one did not. This is a statement ABOUT THE BOUNDARY.
#   3  BLOCKED / CANNOT CHECK - the drill could not build the environment it needs (its own
#      throwaway database would not come up, `docker run` failed, the container died). The
#      boundary was NEVER EXERCISED, so nothing here says it is absent. Wired into CI (H4),
#      3 must be triaged as "the runner could not run the drill", not as a red boundary.
#      This mirrors assert-rls-force.sh, which already reserves 3 for cannot-check.
#
# The wait for initdb is a POLL ON THE ENTRYPOINT'S READY MARKER with a measured ceiling
# (see scripts/checks/lib/ob-initdb.ps1 for the numbers), and every initdb prints its
# ELAPSED time whether it succeeds or not - a future slow run leaves evidence instead of a
# mystery.

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

$script:Pass    = 0
$script:Fail    = 0
$script:Blocked = 0
function Pass($m) { $script:Pass++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Fail($m) { $script:Fail++; Write-Host "  FAIL  $m" -ForegroundColor Red }
# BLOCK is not FAIL. FAIL is a claim about the exposure boundary; BLOCK says the drill never
# got to look at it. Keeping them in one bucket is how "the machine was busy" gets reported
# as "the boundary is absent".
function Block($m) { $script:Blocked++; Write-Host "  BLOCK $m" -ForegroundColor Yellow }
function Section($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

# Abort the drill as CANNOT-CHECK. The prefix is what the catch below classifies on, so a
# blocked run cannot be laundered into a boundary failure by the exit path.
function Stop-Drill($why) { throw "CANNOT-CHECK :: $why" }

# ---------------------------------------------------------------------------------------
# Bring up one throwaway database, print how long it took, and STOP THE DRILL if it did not
# come up - as BLOCKED, naming which of the four ways it failed (see Start-ObInitdbDetailed).
# ---------------------------------------------------------------------------------------
function Start-DrillDb {
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$InitDir,
        [string[]]$DockerArgs = @()
    )
    $r = Start-ObInitdbDetailed -Name $Name -InitDir $InitDir -DockerArgs $DockerArgs
    Write-Host "        initdb $Name : $($r.Outcome) after $($r.ElapsedSec)s of a $($r.BudgetSec)s ceiling" -ForegroundColor DarkGray
    if (-not $r.Ready) {
        Stop-Drill ("$Name never finished initdb: $($r.Outcome) after $($r.ElapsedSec)s of a $($r.BudgetSec)s ceiling. $($r.Detail) " +
                    "The exposure boundary was NOT exercised by this run - this is the drill's own environment failing, not evidence that the assertion is missing. " +
                    "Raise the ceiling with OB_INITDB_TIMEOUT_SEC if the machine is genuinely slower than the measured 6s (16s contended) this budget is built from.")
    }
}

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

# ---------------------------------------------------------------------------------------
# EVERY DOCKER NAME IS RUN-SCOPED. They used to be the fixed strings `ob-h2-nomig`,
# `ob-h2-mig` and project `ob-h2-gate`, and Start-ObInitdb opens with `docker rm -f $Name`.
# Two drills running at once - which is the normal state of this effort, an author and a
# verifier on one machine - therefore DELETE EACH OTHER'S DATABASES mid-run. The victim sees
# its green sections fail against a container that is simply gone, at a sha where the drill is
# fine. "It passes for me" and "it failed for me" were both true. A run id ends that.
# ---------------------------------------------------------------------------------------
$RunId     = [guid]::NewGuid().ToString('N').Substring(0,8)
$NoMig     = "ob-h2-nomig-$RunId"
$Mig       = "ob-h2-mig-$RunId"
$Proj      = "ob-h2-gate-$RunId"
$tmpRoot   = Join-Path $env:TEMP ("ob-h2-drill-" + $RunId)
$srcDir    = Join-Path $tmpRoot 'src'        # the migrations SOURCE dir  -> /opt/ob-migrations
$chainFull = Join-Path $tmpRoot 'chain-full' # every migration compose mounts
$chainNoRl = Join-Path $tmpRoot 'chain-norls'# the same chain MINUS the three *-rls migrations
$containers = @()
Write-Host "run id $RunId  (containers $NoMig / $Mig, compose project $Proj-*)" -ForegroundColor DarkGray

try {
    New-Item -ItemType Directory $tmpRoot -Force | Out-Null

    # -----------------------------------------------------------------------------------
    Section "0. Stage the real initdb chain (derived from compose, not hand-listed)"
    if (-not (Test-Path $AssertSh)) { Fail "assertion script missing at $AssertSh"; throw "cannot drill without the assertion" }
    if (-not (Test-Path $ComposeYml)) { Fail "OB1 compose missing at $ComposeYml (submodule not initialised?)"; throw "cannot drill" }

    $chain = Get-ObInitChain -ComposePath $ComposeYml
    if ($chain.Count -lt 20) { Fail "derived only $($chain.Count) initdb mounts from compose - the chain parse is broken" }
    else { Pass "initdb chain derived from compose ($($chain.Count) migrations)" }

    # ---------------------------------------------------------------------------------
    # THE CHECKOUT IS AN INPUT, AND THIS DRILL USED TO TRUST IT.
    #
    # A verifier's clean-checkout run failed its positive control at a sha where the drill
    # is fine. The mechanism is here. `Copy-ObInitChain` SKIPS a chain file it cannot find
    # and returns a shorter count; the two lines below used to PRINT that count inside an
    # unconditional `Pass`, so a checkout missing migrations staged a SHORT chain, brought
    # up a database missing tables, and only the GREEN sections noticed. Two counts
    # reported and never asserted - the same "a check that passes while checking nothing"
    # this whole effort keeps re-finding, in the drill itself.
    #
    # That is not hypothetical on this machine: documentation/notes/
    # clean-clone-maxpath-validation-trap.md records `git clone` EXITING 0 while leaving
    # 1,108 tracked files absent (MAX_PATH, core.longpaths unset). MEASURED here: hiding 5
    # of OB1/docker's 31 .sql took the staged chain from 28 to 23 with no error and no
    # throw. So both counts are now ASSERTED against the chain compose declares.
    # ---------------------------------------------------------------------------------
    New-Item -ItemType Directory $srcDir -Force | Out-Null
    Copy-Item (Join-Path $Ob1Docker '*.sql') $srcDir
    Copy-Item $AssertSh (Join-Path $srcDir 'assert-rls-force.sh')
    $srcSqlCount = (Get-ChildItem $srcDir -Filter *.sql).Count
    if ($srcSqlCount -ge $chain.Count) {
        Pass "migrations source staged ($srcSqlCount .sql >= the $($chain.Count) compose mounts, including the revert-*.sql the scan must ignore)"
    } else {
        Fail "migrations source has only $srcSqlCount .sql but compose mounts $($chain.Count) - this checkout is INCOMPLETE. A drill run against it proves nothing; see documentation/notes/clean-clone-maxpath-validation-trap.md."
        throw "incomplete checkout"
    }

    # A chain file that is missing must name itself. Get-Content -Raw on an absent path
    # returns $null, and the regex below then threw "Value cannot be null" from inside the
    # Where-Object - an abort that told the reader nothing about which file was gone.
    $missingChain = @($chain | Where-Object { -not (Test-Path (Join-Path $Ob1Docker $_[0])) } | ForEach-Object { $_[0] })
    if ($missingChain.Count) {
        Fail "compose mounts $($chain.Count) migrations but $($missingChain.Count) are absent from $Ob1Docker : $($missingChain -join ', ') - INCOMPLETE checkout, not a drill result"
        throw "incomplete checkout"
    }

    $nFull = Copy-ObInitChain -Chain $chain -SourceDir $Ob1Docker -TargetDir $chainFull

    # "WITHOUT the migration" is itself DERIVED, not a filename guess: a chain file is a
    # boundary migration exactly when it DECLARES a FORCE. Filtering on the substring 'rls'
    # would have kept 190-init-agent-memory-corpus-failclosed.sql (declares none, correctly
    # kept) and would silently drop a future boundary migration named anything else.
    $forceRx = [regex]'(?is)ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(?:[a-z_][a-z0-9_]*\.|"[^"]+"\.)?(?:[a-z_][a-z0-9_]*|"[^"]+")\s+FORCE\s+ROW\s+LEVEL\s+SECURITY'
    $rlsMounts = @($chain | Where-Object { $forceRx.IsMatch((Get-Content -Raw (Join-Path $Ob1Docker $_[0]))) })
    if ($rlsMounts.Count -lt 2) { Fail "expected at least 2 FORCE-declaring migrations in the chain; found $($rlsMounts.Count)" }
    else { Pass "boundary migrations derived by content: $((($rlsMounts | ForEach-Object { $_[1] }) -join ', '))" }
    $rlsNames = @($rlsMounts | ForEach-Object { $_[1] })

    # ---------------------------------------------------------------------------------
    # A MIGRATION THAT CANNOT RUN WITHOUT A DROPPED ONE MUST GO TOO - and this is what
    # actually broke the drill, not a slow machine.
    #
    # `195-init-agent-memory-exposure-column.sql` (H3) opens with a guard that RAISEs:
    #   "the jsonb exposure predicates are not defined. Apply 180-init-agent-memory-rls.sql
    #    first - this file MIGRATES that boundary, it does not create one from nothing."
    # Removing 180 to build the RED chain therefore made initdb ABORT (postgres exits 3),
    # and the old wait - which polled a container name for 180s without asking whether the
    # container was still alive - reported that death as "initdb did not complete in 180s".
    # Two runs, minutes apart, on a verified-complete clean clone, and the sentence blamed
    # the machine. MEASURED here from a clean clone: `exited after 7.3s of a 600s ceiling,
    # status/exitcode = exited/3`, carrying that exact SQL error.
    #
    # DERIVED, not a second hand-list: a chain file is dropped when its text names a
    # dropped migration's MOUNTED FILENAME on a line that is not a `--` comment - which is
    # where a dependency guard lives, because it has to be executable to raise. Applied to
    # a fixpoint, so a dependent of a dependent goes as well.
    #
    # The distinction is load-bearing on the real chain, both ways:
    #   190-init-agent-memory-corpus-failclosed.sql names 180 ONCE, on a `--` line, and the
    #      comment says why: "This file is self-contained even if 180 has not run". KEPT.
    #   195 names 180 on line 181, inside a RAISE EXCEPTION string. DROPPED.
    # LIMIT: only `--` comments are recognised. A mention inside a /* .. */ block would drop
    # a migration that did not need to go - which shortens the RED chain rather than
    # weakening the assertion, and section 1's preconditions (0 FORCEd, agent_memories
    # true/false) would fail loudly if the cascade ever reached something they need.
    # ---------------------------------------------------------------------------------
    $dropNames = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($n in $rlsNames) { [void]$dropNames.Add($n) }
    $cascaded = @()
    $grew = $true
    while ($grew) {
        $grew = $false
        foreach ($m in $chain) {
            if ($dropNames.Contains($m[1])) { continue }
            $hit = $null
            foreach ($line in (Get-Content (Join-Path $Ob1Docker $m[0]))) {
                if ($line.TrimStart().StartsWith('--')) { continue }
                foreach ($d in @($dropNames)) {
                    if ($line -match [regex]::Escape($d)) { $hit = $d; break }
                }
                if ($hit) { break }
            }
            if ($hit) { [void]$dropNames.Add($m[1]); $cascaded += "$($m[1]) (requires $hit)"; $grew = $true }
        }
    }
    if ($cascaded.Count) { Pass "dependent migrations dropped with them, derived from executable references: $($cascaded -join ', ')" }
    else { Pass "no chain migration executably references a boundary migration - nothing cascades" }

    $chainMinus = @($chain | Where-Object { -not $dropNames.Contains($_[1]) })
    $nNoRls = Copy-ObInitChain -Chain $chainMinus -SourceDir $Ob1Docker -TargetDir $chainNoRl
    if ($nFull -eq $chain.Count -and $nNoRls -eq $chainMinus.Count) {
        Pass "staged two chains, both COMPLETE: full=$nFull/$($chain.Count), without-the-migration=$nNoRls/$($chainMinus.Count)"
    } else {
        Fail "staging came up short: full=$nFull/$($chain.Count), without-the-migration=$nNoRls/$($chainMinus.Count) - a short chain silently builds a database missing tables"
        throw "incomplete staging"
    }

    $srcMount   = ($srcDir    -replace '\\', '/')
    $fullMount  = ($chainFull -replace '\\', '/')
    $noRlsMount = ($chainNoRl -replace '\\', '/')

    # -----------------------------------------------------------------------------------
    Section "1. RED FIRST - a database brought up WITHOUT the migration"
    $containers += $NoMig
    Start-DrillDb -Name $NoMig -InitDir $noRlsMount -DockerArgs @('-v', "${srcMount}:/opt/ob-migrations:ro")
    $errA = @(Get-ObInitdbErrors -Name $NoMig)
    if ($errA.Count) { Fail "unexpected errors in the without-migration chain: $($errA -join ' | ')" } else { Pass "without-migration chain initialised clean" }

    $liveForced = Invoke-Sql -Container $NoMig -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relforcerowsecurity;"
    if ($liveForced -eq '0') { Pass "precondition: 0 tables FORCEd on the un-migrated database" }
    else { Fail "precondition broken: $liveForced tables already FORCEd without the migration" }

    $r = Invoke-Assert -Container $NoMig
    Expect -R $r -Code 1 -Contains @('EXPOSURE BOUNDARY NOT ASSERTED', 'relforcerowsecurity=false', 'agent_memories', 'thoughts', 'entity_extraction_queue', 'idea_revisions') `
           -What "assertion FIRES on a DB brought up without the migration, and names the tables"

    # (b) relforcerowsecurity is the thing being checked, not relrowsecurity. On this DB the
    # agent_memory_* tables have relrowsecurity=TRUE already (init-agent-memory.sql enables it)
    # and FORCE false - the exact pair recorded at the top of init-agent-memory-rls.sql.
    $pair = Invoke-Sql -Container $NoMig -Sql "SELECT relrowsecurity::text||'/'||relforcerowsecurity::text FROM pg_class WHERE relname='agent_memories' AND relnamespace='public'::regnamespace;"
    if ($pair -eq 'true/false') { Pass "the case relrowsecurity=t + relforcerowsecurity=f is present and is what fired (agent_memories)" }
    else { Fail "expected agent_memories to be true/false on the un-migrated DB, got '$pair'" }

    # The RAISE WARNING channel: `docker logs` must carry it, because that is what an operator
    # who is not looking for this actually reads.
    Start-Sleep 1
    $dblog = (& docker logs $NoMig 2>&1 | Out-String)
    if ($dblog -match 'assert-rls-force') { Pass "the failure is in the POSTGRES SERVER LOG (docker logs openbrain-db), not only in the healthcheck's stderr" }
    else { Fail "nothing in docker logs names assert-rls-force - the alarm channel an operator actually reads is silent" }

    # -----------------------------------------------------------------------------------
    Section "2. GREEN - the same chain WITH the migration"
    $containers += $Mig
    Start-DrillDb -Name $Mig -InitDir $fullMount -DockerArgs @('-v', "${srcMount}:/opt/ob-migrations:ro")
    $errB = @(Get-ObInitdbErrors -Name $Mig)
    if ($errB.Count) { Fail "unexpected errors in the full chain: $($errB -join ' | ')" } else { Pass "full chain initialised clean" }

    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "assertion PASSES on a correctly migrated database"

    # (a) DERIVED, not hand-listed. The count the assertion checks must equal the count the
    # catalogue actually FORCEs, and it must be the SEVENTEEN the migrations declare - not the
    # NINE the plan's sentence names.
    $forcedLive = Invoke-Sql -Container $Mig -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relforcerowsecurity;"
    if ($forcedLive -eq '17') { Pass "derived set (17) equals the catalogue's FORCEd set (17) - the plan's 'nine' would have checked 9 of 17" }
    else { Fail "catalogue FORCEs $forcedLive tables but the assertion reported 17 - the derivation and the database disagree" }

    # -----------------------------------------------------------------------------------
    Section "3. RLS ENABLED but FORCE OFF - the guarantee that is not the same guarantee"
    Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.thoughts NO FORCE ROW LEVEL SECURITY;" | Out-Null
    $st = Invoke-Sql -Container $Mig -Sql "SELECT relrowsecurity::text||'/'||relforcerowsecurity::text FROM pg_class WHERE relname='thoughts' AND relnamespace='public'::regnamespace;"
    if ($st -eq 'true/false') { Pass "set thoughts to relrowsecurity=t, relforcerowsecurity=f" } else { Fail "could not create the RLS-on/FORCE-off state (got '$st')" }
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on: public.thoughts(relkind=r)') -NotContains @('row level security DISABLED') `
           -What "assertion FIRES on RLS-enabled-but-FORCE-off, and says so as FORCE, not as RLS"
    Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.thoughts FORCE ROW LEVEL SECURITY;" | Out-Null

    # -----------------------------------------------------------------------------------
    Section "4. EIGHT OF NINE - a partial promotion, which is what a hand-list cannot see"
    # 180 + 190 applied, 200 (the graph plane) not. The nine tables the plan's sentence names
    # are ALL correctly FORCEd here; eight others are not. A hand-list of nine passes this.
    $graph = @('thought_entities','entity_extraction_queue','thought_edges','idea_revisions','entities','edges','source_entities','consolidation_log')
    foreach ($t in $graph) { Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.$t NO FORCE ROW LEVEL SECURITY;" | Out-Null }
    $nineOk = Invoke-Sql -Container $Mig -Sql "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relforcerowsecurity AND (c.relname LIKE 'agent_memor%' OR c.relname='thoughts');"
    if ($nineOk -eq '9') { Pass "precondition: the NINE tables the plan names are all still FORCEd (a hand-list of nine passes this state)" }
    else { Fail "expected the nine plan-named tables to remain FORCEd, got $nineOk" }
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on:','thought_entities','entity_extraction_queue','idea_revisions','consolidation_log') -NotContains @('agent_memories') `
           -What "assertion FIRES on the partial promotion and names exactly the eight ungoverned tables"
    foreach ($t in $graph) { Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.$t FORCE ROW LEVEL SECURITY;" | Out-Null }
    $r = Invoke-Assert -Container $Mig
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
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('governed table absent from the database: public.drill_new_table') `
           -What "5a: a newly governed table that does not exist is a VIOLATION, not an omission"

    # 5b - the table exists, RLS on, FORCE never applied.
    Invoke-Sql -Container $Mig -Sql "CREATE TABLE public.drill_new_table (id int); ALTER TABLE public.drill_new_table ENABLE ROW LEVEL SECURITY;" | Out-Null
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('relforcerowsecurity=false on: public.drill_new_table(relkind=r)') `
           -What "5b: a newly governed table whose FORCE was never applied fires - with NO edit to the assertion"

    # 5c - THE VACUOUS GREEN. The declaration is in the source directory but its file is not in
    # the initdb chain: this database is fine, and the next fresh volume is not. An assertion
    # that read only the chain would find nothing to check and report success.
    Remove-Item $newMigChain -Force
    Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.drill_new_table FORCE ROW LEVEL SECURITY;" | Out-Null
    $forcedNow = Invoke-Sql -Container $Mig -Sql "SELECT relforcerowsecurity::text FROM pg_class WHERE relname='drill_new_table' AND relnamespace='public'::regnamespace;"
    if ($forcedNow -eq 'true') { Pass "precondition: this database is now fully compliant (18/18 FORCEd)" } else { Fail "could not FORCE drill_new_table (got '$forcedNow')" }
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('NOT in the initdb chain', 'drill_new_table') `
           -What "5c: a compliant DB still FAILS when a governed table is missing from the chain (the next restore would be unprotected)"

    Remove-Item $newMigSrc -Force
    Invoke-Sql -Container $Mig -Sql "DROP TABLE public.drill_new_table;" | Out-Null
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "and back to green once the scratch migration is withdrawn"

    # -----------------------------------------------------------------------------------
    Section "6. THE ASSERTION CANNOT REACH THE DATABASE - and must not report healthy"
    # This is the trap that matters: every vacuous green in this effort had the shape
    # 'the check could not run, so nothing failed'.
    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','PGHOST=127.0.0.1','-e','PGPORT=1')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'CANNOT CHECK IS NOT PASSED') -NotContains @('OK -') `
           -What "6a: connection refused -> exit 3, explicitly NOT a pass"

    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','POSTGRES_DB=no_such_database')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK') -NotContains @('OK -') -What "6b: database does not exist -> exit 3"

    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','POSTGRES_USER=no_such_role')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK') -NotContains @('OK -') -What "6c: role does not exist -> exit 3"

    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','PSQL_BIN=/nonexistent/psql')
    Expect -R $r -Code 3 -Contains @('no psql on PATH') -What "6d: no psql binary -> exit 3"

    # -----------------------------------------------------------------------------------
    Section "7. THE DERIVATION SOURCE ITSELF GOES MISSING - zero governed tables is not a pass"
    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','RLS_MIGRATIONS_DIR=/opt/does-not-exist')
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'is not a directory') -What "7a: migrations directory absent -> exit 3"

    # An EMPTY but present directory is the nastier one: find succeeds, grep finds nothing,
    # and a naive implementation reports 'all 0 governed tables are compliant'.
    & docker exec $Mig sh -c 'mkdir -p /tmp/emptymig' | Out-Null
    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','RLS_MIGRATIONS_DIR=/tmp/emptymig')
    Expect -R $r -Code 3 -Contains @('derived ZERO governed tables') -NotContains @('OK -') `
           -What "7b: an EMPTY migrations directory -> exit 3, not 'zero tables, all compliant'"

    # And the revert files must not be what the scan reads - revert-*-rls.sql says NO FORCE.
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') `
           -What "7c: revert-*.sql sitting in the same directory does not corrupt the derived set"

    # -----------------------------------------------------------------------------------
    Section "8. THE RESTORE PATH - H2 names 'a restore' first, so restore something"
    # A restore is not the same event as a rebuild: initdb does not run at all, and whether the
    # boundary survives depends entirely on whether the DUMP carried it. pg_dump emits
    # `ALTER TABLE ... FORCE ROW LEVEL SECURITY`, so a dump taken AFTER the migration restores
    # protected - and a dump taken before it restores a database that looks complete, has every
    # table and every row, and is silently FORCE-off. That second case is the H2 scenario.
    $dumpNew = (& docker exec $Mig pg_dump -U postgres -s openbrain 2>&1 | Out-String)
    if ($dumpNew -match 'FORCE ROW LEVEL SECURITY') { Pass "8a: a post-migration pg_dump CARRIES the FORCE clauses (a current backup restores protected)" }
    else { Fail "8a: pg_dump of the migrated DB does not contain FORCE ROW LEVEL SECURITY - the whole restore assumption is wrong" }

    $dumpOldPath = Join-Path $tmpRoot 'stale.sql'
    (& docker exec $NoMig pg_dump -U postgres -s openbrain 2>&1 | Out-String) | Set-Content -LiteralPath $dumpOldPath -Encoding UTF8
    $stale = Get-Content -Raw $dumpOldPath
    if ($stale -notmatch 'FORCE ROW LEVEL SECURITY') { Pass "8b: the pre-migration dump carries no FORCE clauses (this is the stale backup)" }
    else { Fail "8b: the pre-migration dump unexpectedly contains FORCE clauses" }

    # Restore it into a fresh database in the SAME cluster (roles are cluster-wide, so the
    # GRANTs in the dump resolve) and point the assertion at it. Nothing about this database is
    # obviously broken; it just is not protected.
    Invoke-Sql -Container $Mig -Sql "SELECT 1;" | Out-Null
    & docker exec $Mig psql -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS restored;" 2>&1 | Out-Null
    & docker exec $Mig psql -U postgres -d postgres -q -c "CREATE DATABASE restored;" 2>&1 | Out-Null
    & docker cp $dumpOldPath "${Mig}:/tmp/stale.sql" 2>&1 | Out-Null
    & docker exec $Mig sh -c 'psql -U postgres -d restored -q -f /tmp/stale.sql' 2>&1 | Out-Null
    $restoredTables = (& docker exec $Mig psql -U postgres -d restored -tA -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>&1 | Out-String).Trim()
    if ([int]$restoredTables -gt 20) { Pass "8c: the stale dump restored ($restoredTables tables in public) - a database that looks complete" }
    else { Fail "8c: restore produced only $restoredTables tables; the scenario did not set up" }
    $r = Invoke-Assert -Container $Mig -EnvArgs @('-e','POSTGRES_DB=restored')
    Expect -R $r -Code 1 -Contains @('EXPOSURE BOUNDARY NOT ASSERTED', 'relforcerowsecurity=false') `
           -What "8d: the assertion FIRES on a restore from a stale dump - the case where nothing else looks wrong"
    & docker exec $Mig psql -U postgres -d postgres -q -c "DROP DATABASE IF EXISTS restored;" 2>&1 | Out-Null

    # -----------------------------------------------------------------------------------
    if (-not $SkipComposeGate) {
        Section "9. REFUSE TO SERVE - the wiring, not the script"
        # The claim under test is not 'the script exits 1'. It is 'a dependent that gates on
        # openbrain-db's health DOES NOT START'. Proven on a throwaway compose project with the
        # production healthcheck shape and a dependent using condition: service_healthy.
        $composeDir = Join-Path $tmpRoot 'compose'   # NOT $Proj - PowerShell variable names are case-insensitive, and $proj silently overwrote the run-scoped project name
        New-Item -ItemType Directory $composeDir -Force | Out-Null

        # -------------------------------------------------------------------------------
        # THE SECOND COPY OF THE INITDB BUDGET. Sections 1 and 2 wait on the entrypoint's
        # marker; THIS section waits on a docker healthcheck, and it used to carry its own
        # hardcoded pair (start_period 120s, --wait-timeout 200) - the same allowance for
        # the same initdb, written twice, so raising one would have left the other.
        #
        # This budget is NOT free the way the signal-wait is. The RED case is only declared
        # `unhealthy` once start_period has ELAPSED, so every second of it is spent on every
        # run; it cannot simply be made enormous. One relationship has to hold:
        #
        #     wait-timeout > start_period + retries * interval
        #
        # or `compose up --wait` gives up while the container is still `starting`, and the
        # `unhealthy` assertion below never sees the state it is asserting on.
        #
        # What the two numbers buy, stated so they can be audited rather than liked:
        #   GREEN survives until start_period + retries*interval = 195s of initdb before the
        #        healthcheck can mark a correctly migrated database unhealthy. Measured worst
        #        initdb on this machine is 16.2s (eight chains racing; 6.5s sequential - see
        #        lib/ob-initdb.ps1), so that is ~12x headroom.
        #   RED  costs exactly that 195s on every run, because FailingStreak stays 0 for the
        #        whole start_period (verified by `docker inspect` mid-run: `starting
        #        failing=0` at t=156s of a 180s start_period). This is why the number is 180
        #        and not 600 - unlike the signal-wait's ceiling, this one is always spent.
        $HealthStartPeriodSec = 180
        $HealthWaitTimeoutSec = $HealthStartPeriodSec + 60
        # -------------------------------------------------------------------------------
        $composeText = @"
name: PROJNAME
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
      start_period: ${HealthStartPeriodSec}s
  dependent:
    image: pgvector/pgvector:pg16
    depends_on:
      db:
        condition: service_healthy
    command: ["sh", "-c", "echo DEPENDENT-STARTED; sleep 300"]
"@
        foreach ($case in @(@{ n = 'red'; dir = $noRlsMount; want = $false }, @{ n = 'green'; dir = $fullMount; want = $true })) {
            $f = Join-Path $composeDir "docker-compose.$($case.n).yml"
            [IO.File]::WriteAllText($f, ($composeText -replace 'CHAINDIR', $case.dir -replace 'SRCDIR', $srcMount -replace 'PROJNAME', "$Proj-$($case.n)"), (New-Object Text.UTF8Encoding($false)))
            & docker compose -f $f down -v --remove-orphans 2>&1 | Out-Null
            $swUp = [System.Diagnostics.Stopwatch]::StartNew()
            $upOut = (& docker compose -f $f up -d --wait --wait-timeout $HealthWaitTimeoutSec 2>&1 | Out-String)
            $swUp.Stop()
            Write-Host "        compose up --wait ($($case.n)): $([math]::Round($swUp.Elapsed.TotalSeconds,1))s of a $HealthWaitTimeoutSec s wait-timeout (start_period $HealthStartPeriodSec s)" -ForegroundColor DarkGray
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
                $insp = (& docker inspect --format '{{json .State.Health}}' "$Proj-red-db-1" 2>&1 | Out-String)
                if ($insp -match 'EXPOSURE BOUNDARY NOT ASSERTED') { Pass "9-RED: the reason is in docker inspect .State.Health, naming the boundary" }
                else { Fail "9-RED: health output does not carry the reason: $insp" }

                # THE LIMIT OF "REFUSE TO SERVE", MEASURED RATHER THAN ASSUMED. A docker
                # healthcheck gates DEPENDENTS; it does not gate the socket. An unhealthy
                # postgres still answers anyone who connects anyway. So the refusal is real at
                # the boot/dependency edge - which is exactly the event H2 names (a restore, a
                # rebuild, a skipped promotion) - and for a database that goes bad WHILE
                # RUNNING it is an alarm plus a refusal of the next dependent start. Claiming
                # more than that would be a claim nobody ran.
                $stillServes = (& docker exec "$Proj-red-db-1" psql -U postgres -d openbrain -tA -c "SELECT 'served';" 2>&1 | Out-String)
                $hs = (& docker inspect --format '{{.State.Health.Status}}' "$Proj-red-db-1" 2>&1 | Out-String).Trim()
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

    # -----------------------------------------------------------------------------------
    Section "10. THE DECLARATION FORMS A REGEX DROPS - each one must GOVERN and each must RED"
    # This is the verifier's migration, not an invented one. Three forms PostgreSQL accepts
    # (`ALTER TABLE t`, `ALTER TABLE IF EXISTS t`, `ALTER TABLE "Quoted"`), plus the two the
    # old lookup could not resolve at all (a non-public schema, a PARTITIONED table, relkind
    # `p`), plus a FORCE inside a /* */ block comment that must NOT be counted. The old grep
    # derived ONE of the five, invented a sixth from the comment, and REDded the partitioned
    # table forever.
    #
    # BOTH DIRECTIONS ARE PROVEN PER FORM. Deriving a table is worthless if the catalogue
    # lookup then cannot see it (that was defect 2: a correct FORCE read as "absent from the
    # database", exit 1, an unbootable stack from a wrong diagnosis). So for each form:
    # green while protected, RED while genuinely unprotected, green again.
    Invoke-Sql -Container $Mig -Sql @"
CREATE SCHEMA IF NOT EXISTS auth;   -- IF NOT EXISTS: the real chain already creates it
CREATE TABLE public.adv_plain (id int);
CREATE TABLE public.adv_ifexists (id int);
CREATE TABLE public.\"AdvQuoted\" (id int);
CREATE TABLE auth.adv_other_schema (id int);
CREATE TABLE public.adv_partitioned (id int, d date) PARTITION BY RANGE (d);
ALTER TABLE public.adv_plain ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.adv_ifexists ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.\"AdvQuoted\" ENABLE ROW LEVEL SECURITY;
ALTER TABLE auth.adv_other_schema ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.adv_partitioned ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.adv_plain FORCE ROW LEVEL SECURITY;
ALTER TABLE public.adv_ifexists FORCE ROW LEVEL SECURITY;
ALTER TABLE public.\"AdvQuoted\" FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.adv_other_schema FORCE ROW LEVEL SECURITY;
ALTER TABLE public.adv_partitioned FORCE ROW LEVEL SECURITY;
"@ | Out-Null
    $kinds = Invoke-Sql -Container $Mig -Sql "SELECT string_agg(n.nspname||'.'||c.relname||':'||c.relkind::text, ' ' ORDER BY c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relname IN ('adv_plain','adv_ifexists','AdvQuoted','adv_other_schema','adv_partitioned') AND c.relforcerowsecurity;"
    if ($kinds -match 'adv_partitioned:p' -and $kinds -match 'auth\.adv_other_schema') {
        Pass "precondition: all five adversarial tables exist and are correctly FORCEd ($kinds)"
    } else { Fail "precondition: expected five FORCEd adversarial tables incl. a partitioned one; got '$kinds'" }

    $advSql = @"
ALTER TABLE public.adv_plain FORCE ROW LEVEL SECURITY;
ALTER TABLE IF EXISTS public.adv_ifexists FORCE ROW LEVEL SECURITY;
ALTER TABLE public."AdvQuoted" FORCE ROW LEVEL SECURITY;
ALTER TABLE auth.adv_other_schema FORCE ROW LEVEL SECURITY;
ALTER TABLE ONLY public.adv_partitioned FORCE ROW LEVEL SECURITY;
/* a block comment that says
     ALTER TABLE public.adv_ghost FORCE ROW LEVEL SECURITY;
   and must not create a governed table */
"@
    $advSrc   = Join-Path $srcDir    '220-init-drill-adversarial.sql'
    $advChain = Join-Path $chainFull '220-init-drill-adversarial.sql'
    [IO.File]::WriteAllText($advSrc,   $advSql, (New-Object Text.UTF8Encoding($false)))
    [IO.File]::WriteAllText($advChain, $advSql, (New-Object Text.UTF8Encoding($false)))

    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 22 governed tables') -NotContains @('adv_ghost') `
           -What "10a: all FIVE forms are derived (17+5=22) and the block-comment ghost is NOT"

    foreach ($f in @(
        @{ n = 'a partitioned table (relkind p)';   sql = 'public.adv_partitioned';   needle = 'public.adv_partitioned(relkind=p)' },
        @{ n = 'a quoted identifier';               sql = 'public.\"AdvQuoted\"';       needle = 'public.AdvQuoted(relkind=r)' },
        @{ n = 'a NON-PUBLIC schema';               sql = 'auth.adv_other_schema';    needle = 'auth.adv_other_schema(relkind=r)' },
        @{ n = 'an IF EXISTS declaration';          sql = 'public.adv_ifexists';      needle = 'public.adv_ifexists(relkind=r)' },
        @{ n = 'a plain declaration';               sql = 'public.adv_plain';         needle = 'public.adv_plain(relkind=r)' }
    )) {
        Invoke-Sql -Container $Mig -Sql "ALTER TABLE $($f.sql) NO FORCE ROW LEVEL SECURITY;" | Out-Null
        $r = Invoke-Assert -Container $Mig
        Expect -R $r -Code 1 -Contains @('EXPOSURE BOUNDARY NOT ASSERTED', $f.needle) `
               -What "10b: $($f.n) that is genuinely unprotected still REDs, and names its relkind"
        Invoke-Sql -Container $Mig -Sql "ALTER TABLE $($f.sql) FORCE ROW LEVEL SECURITY;" | Out-Null
    }
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 22 governed tables') -What "10c: green again with all five re-FORCEd"

    # -----------------------------------------------------------------------------------
    Section "11. A FORM THE PARSER CANNOT READ MUST BE DETECTED, NOT DROPPED"
    # The defect class, stated: widening the pattern only ever covers the forms someone
    # thought of. The property that survives is that an unrecognised declaration STOPS THE
    # BOOT. These two are deliberately outside the grammar.
    $dynFile = Join-Path $srcDir '221-init-drill-dynamic.sql'
    [IO.File]::WriteAllText($dynFile, "DO `$`$ BEGIN EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', 'adv_plain'); END `$`$;`n", (New-Object Text.UTF8Encoding($false)))
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'DYNAMIC SQL') -NotContains @('OK -') `
           -What "11a: dynamic SQL (DO ... EXECUTE format) cannot be resolved statically -> exit 3, not a silent skip"
    Remove-Item $dynFile -Force

    $weirdFile = Join-Path $srcDir '222-init-drill-unparseable.sql'
    [IO.File]::WriteAllText($weirdFile, ("ALTER TABLE public.tabl" + [char]0x20AC + " FORCE ROW LEVEL SECURITY;`n"), (New-Object Text.UTF8Encoding($false)))
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'UNPARSED') -NotContains @('OK -') `
           -What "11b: a declaration the grammar cannot walk -> exit 3 naming the statement, not a shorter governed set"
    Remove-Item $weirdFile -Force

    $undoFile = Join-Path $srcDir '223-init-drill-undo.sql'
    [IO.File]::WriteAllText($undoFile, "ALTER TABLE public.adv_plain NO FORCE ROW LEVEL SECURITY;`n", (New-Object Text.UTF8Encoding($false)))
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'BOTH FORCE and NO FORCE', 'public.adv_plain') `
           -What "11c: NO FORCE outside a revert-*.sql makes the set order-dependent -> exit 3, not a coin flip"
    Remove-Item $undoFile -Force

    # -----------------------------------------------------------------------------------
    Section "12. THE COMPLETENESS BACKSTOP - what catches a form nobody anticipated"
    # Layer 1 catches statements the parser SEES and cannot read. Layer 2 catches the ones it
    # never saw at all: a .sh in the chain, a file type nobody thought of, a hand-applied
    # FORCE. If the catalogue holds a FORCEd relation the derivation does not, the derived set
    # is not the governed set, and "I checked the ones I found" is exactly the vacuous green.
    Invoke-Sql -Container $Mig -Sql "CREATE TABLE public.adv_undeclared (id int); ALTER TABLE public.adv_undeclared ENABLE ROW LEVEL SECURITY; ALTER TABLE public.adv_undeclared FORCE ROW LEVEL SECURITY;" | Out-Null
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 3 -Contains @('CANNOT CHECK', 'the migration parse did NOT derive', 'public.adv_undeclared') -NotContains @('OK -') `
           -What "12: a FORCEd relation no migration declares -> exit 3 (this is what turns a 17-of-17 into a caught 3-of-5)"
    Invoke-Sql -Container $Mig -Sql "DROP TABLE public.adv_undeclared;" | Out-Null

    # -----------------------------------------------------------------------------------
    Section "13. FILE TYPES AND PLACES THE OLD SCAN COULD NOT SEE"
    # `find -maxdepth 1 -name '*.sql'` missed both of these, and the postgres entrypoint runs
    # the first one.
    $gzPlain = Join-Path $tmpRoot 'gz-src.sql'
    [IO.File]::WriteAllText($gzPlain, "ALTER TABLE public.adv_gzipped FORCE ROW LEVEL SECURITY;`n", (New-Object Text.UTF8Encoding($false)))
    $gzFile = Join-Path $srcDir '224-init-drill-gz.sql.gz'
    $inFs = [IO.File]::OpenRead($gzPlain); $outFs = [IO.File]::Create($gzFile)
    $gz = New-Object IO.Compression.GZipStream($outFs, [IO.Compression.CompressionMode]::Compress)
    $inFs.CopyTo($gz); $gz.Dispose(); $outFs.Dispose(); $inFs.Dispose()
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('public.adv_gzipped') `
           -What "13a: a .sql.gz migration IS read - the entrypoint executes it, so ignoring it was a hole"
    Remove-Item $gzFile -Force

    $subDir = Join-Path $srcDir 'sub'
    New-Item -ItemType Directory $subDir -Force | Out-Null
    [IO.File]::WriteAllText((Join-Path $subDir '225-init-drill-sub.sql'), "ALTER TABLE public.adv_in_subdir FORCE ROW LEVEL SECURITY;`n", (New-Object Text.UTF8Encoding($false)))
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 1 -Contains @('public.adv_in_subdir') `
           -What "13b: a migration in a SUBDIRECTORY of the source dir IS read"
    Remove-Item $subDir -Recurse -Force

    Remove-Item $advSrc -Force
    Remove-Item $advChain -Force
    Invoke-Sql -Container $Mig -Sql 'DROP TABLE public.adv_plain, public.adv_ifexists, public.\"AdvQuoted\", public.adv_partitioned; DROP TABLE auth.adv_other_schema;' | Out-Null
    $r = Invoke-Assert -Container $Mig
    Expect -R $r -Code 0 -Contains @('OK - 17 governed tables') -What "13c: back to the 17 once the adversarial migration is withdrawn"

    # -----------------------------------------------------------------------------------
    Section "14. RUNTIME vs THE HEALTHCHECK TIMEOUT - a false positive here is an outage"
    # The parser replaced a single grep, so the number moved and had to be re-measured rather
    # than re-asserted. `healthcheck.timeout` is 5s; a probe that overruns it is marked
    # unhealthy on a database that is fine, which is defect 2's failure mode by another route.
    $budgetMs = 2500   # half the 5s timeout; anything above this is too close to ship
    foreach ($case in @(@{ n = 'passing path (one psql call)'; env = @() },
                        @{ n = 'failing path (two psql calls)'; env = @('-e','POSTGRES_DB=openbrain') })) {
        if ($case.n -like 'failing*') { Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.thoughts NO FORCE ROW LEVEL SECURITY;" | Out-Null }
        $times = @()
        for ($i = 0; $i -lt 5; $i++) {
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $null = Invoke-Assert -Container $Mig -EnvArgs $case.env
            $times += $sw.ElapsedMilliseconds
        }
        if ($case.n -like 'failing*') { Invoke-Sql -Container $Mig -Sql "ALTER TABLE public.thoughts FORCE ROW LEVEL SECURITY;" | Out-Null }
        $max = ($times | Measure-Object -Maximum).Maximum
        $msg = "14: $($case.n) over the real $srcSqlCount-file migration set: $($times -join ' / ') ms (max $max, budget $budgetMs of a 5s healthcheck.timeout)"
        # NOTE: this includes `docker exec` process start, so it OVERSTATES the in-container
        # cost - which is the safe direction for a budget.
        if ($max -le $budgetMs) { Pass $msg } else { Fail "$msg - TOO CLOSE TO THE TIMEOUT. A probe that overruns marks a healthy database unhealthy." }
    }
}
catch {
    $m = $_.Exception.Message
    if ($m -like 'CANNOT-CHECK ::*') {
        Block ("drill aborted, NOTHING PROVEN EITHER WAY: " + ($m -replace '^CANNOT-CHECK :: ', ''))
    } else {
        Fail "drill aborted: $m"
    }
}
finally {
    if (-not $KeepContainers) {
        foreach ($c in $containers) { & docker rm -f $c 2>&1 | Out-Null }
        # Section 9's compose projects are torn down per case, but an ABORT inside section 9
        # skipped that and left them running. Verifier leftovers accumulating on this machine
        # is a stated problem, so the sweep is unconditional and by name: the run id makes
        # these two projects unambiguously ours.
        foreach ($n in @('red', 'green')) {
            $p = "$Proj-$n"
            $ids = @(& docker ps -aq --filter "label=com.docker.compose.project=$p" 2>&1 | Where-Object { $_ -match '^[0-9a-f]{6,}$' })
            if ($ids.Count) { & docker rm -f @ids 2>&1 | Out-Null }
        }
        Remove-Item $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
    } else {
        Write-Host "`nkept: $($containers -join ', '), compose projects $Proj-red / $Proj-green  (temp: $tmpRoot)" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "passed $script:Pass, failed $script:Fail, blocked $script:Blocked" -ForegroundColor $(if ($script:Fail) { 'Red' } elseif ($script:Blocked) { 'Yellow' } else { 'Green' })
if ($script:Fail) {
    exit 1
} elseif ($script:Blocked) {
    Write-Host "EXIT 3 = CANNOT CHECK. The drill could not build its own environment; the exposure boundary was not exercised. This is NOT a finding that the boundary is absent." -ForegroundColor Yellow
    exit 3
} else {
    exit 0
}
