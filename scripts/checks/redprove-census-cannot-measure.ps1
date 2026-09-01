# redprove-census-cannot-measure.ps1 - H1 (DFU section C.9).
#
# THE CLAIM UNDER TEST:
#   census-db-connection-roles.ps1 cannot report a verdict it did not measure.
#
# WHY THIS EXISTS. The census used to exit 0 on an empty denominator. A compose file it
# expected and could not read was degraded to a printed note and a `continue`; an empty
# candidate set fell straight through to "VERDICT: zero unexplained superuser application
# clients." Copied into a repo root with no OB1/ - exactly what `git clone` without
# --recurse-submodules leaves - it printed two "(missing: ...)" notes, recognised zero
# configured clients, and exited 0. A green whose entire configured-client denominator was
# unavailable.
#
# It then kept doing the same thing ONE LEVEL DOWN. The guard added for the above was
# aggregate - it fired only when BOTH compose files recognised nothing - and the service
# enumeration was a line regex, `^  ([A-Za-z0-9_.-]+):\s*$`. So a file that is present,
# readable, valid YAML and accepted by docker, but indented differently, contributed ZERO to
# the denominator in silence while the other file kept the aggregate off zero. Measured on
# the real OB1 compose: re-indent docker-compose.yml by two spaces and the census went from
# "12 of 13 recognised clients" to "1 of 1", and issued a verdict. The cases below cover both
# levels, because closing one and not the other is what happened the first time.
#
# A fix is only worth its commit message if the failing case is reproducible, so this script
# IS the reproduction. It builds repo roots whose only difference is the state of the compose
# input, points the REAL census script at a throwaway database, and asserts the exit code of
# each. The `pre-fix` column records what each case returned against the census BEFORE the
# fix it names, so the contrast is visible without checking out the old script.
#
# EVERYTHING IS THROWAWAY. Its own docker network, a stock postgres:16-alpine, a throwaway
# client container, fixture repo roots under TEMP. It never reads openbrain-db, never attaches
# to an ai-stack_* network, never builds or tags an image.
#
# Exit 0 = every case behaved as specified. Exit 1 = a case disagreed - the census reported a
# verdict it had not measured, or stopped reporting one it had. Exit 2 = this harness could
# not run, or could not establish a fixture, which is NOT a pass.

[CmdletBinding()]
param(
    [string]$Id = "h1rp",
    [switch]$KeepFixtures
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$census  = Join-Path $PSScriptRoot "census-db-connection-roles.ps1"
$dbName  = "wt-$Id-rpdb"
$ghostNm = "wt-$Id-ghost"
$netName = "wt-$Id-rpnet"
$root    = Join-Path $env:TEMP "wt-$Id-redprove"

function Say([string]$m) { Write-Host $m }

function Cleanup {
    # The unreadable-file fixture carries a DENY ACE; without removing it the tree will not
    # delete and the next run inherits a half-built fixture.
    $denied = Join-Path $root "case-unreadable\OB1\docker\docker-compose.yml"
    if (Test-Path $denied) { & icacls $denied /remove:d "$env:USERNAME" 2>&1 | Out-Null }
    if (-not $KeepFixtures) { Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue }
    else { Say "  (fixtures kept: $root)" }
    & cmd /c "docker rm -f $ghostNm 2>nul" | Out-Null
    & cmd /c "docker rm -f $dbName 2>nul" | Out-Null
    & cmd /c "docker network rm $netName 2>nul" | Out-Null
}

trap { Say "HARNESS ERROR: $_"; Cleanup; exit 2 }

Say "H1 red-proof - the census cannot report a verdict it did not measure"
if (-not (Test-Path $census)) {
    Say "ABORT: cannot find the script under test: $census"
    exit 2
}

# ------------------------------------------------------------------------------------------
# 1. Fixtures - repo roots differing only in the compose input
# ------------------------------------------------------------------------------------------
# Each case is a MINIMAL repo root: <root>\<case>\scripts\checks\census-...ps1 plus whatever
# OB1\docker holds, because the census derives its repo root two levels up from its own
# location. Nothing here is a copy of the real compose files - the point is the SHAPE of the
# input, and a fixture that tracked the real fleet would go stale and start testing nothing.
# Every service carries an `image:`, because the census now hands these files to the real
# compose parser and a service with neither image nor build is not a valid project.
$cases = @(
    @{ Name = "missing";     Expect = 2; Was = 0; Ghost = $false
       Why  = "no OB1/ at all - the plain git-clone state, and U4's whole scenario" }
    @{ Name = "unreadable";  Expect = 2; Was = 0; Ghost = $false
       Why  = "compose present but permission-denied - the half that IS readable is clean" }
    @{ Name = "empty";       Expect = 2; Was = 0; Ghost = $false
       Why  = "both files parse fine and no database client is recognised in either" }
    @{ Name = "no-services"; Expect = 2; Was = 0; Ghost = $false
       Why  = 'one file is "services: {}" - valid, accepted, contributes nothing' }
    @{ Name = "reindent";    Expect = 1; Was = 0; Ghost = $false
       Why  = "one file indented two spaces deeper - still valid, still parsed by docker" }
    @{ Name = "honest-zero"; Expect = 0; Was = 0; Ghost = $false
       Why  = "real clients, all on non-superuser roles - a measured zero, and a real pass" }
    @{ Name = "unexplained"; Expect = 1; Was = 1; Ghost = $false
       Why  = "a client configured as postgres and not on the allow-list" }
    @{ Name = "unresolved";  Expect = 2; Was = 1; Ghost = $false
       Why  = "a client whose DB_USER is still a variable - unknown, not clean" }
    @{ Name = "live-ghost";  Expect = 2; Was = 0; Ghost = $true
       Why  = "honest-zero's compose, plus a live client backend no compose file contains" }
)

# The two-service shape both honest-zero and reindent build on. The rogue is FIRST and the
# clean client LAST on purpose: the regex this fix removed collapsed a re-indented file into
# one pseudo-service keyed on the literal string "services", whose user was whichever
# DB_USER line came last. With the clean client last, the pre-fix census saw one non-superuser
# client and exited 0 - which is the green this case exists to turn red.
$rogueFirst = @(
    "services:",
    "  openbrain-rogue:", "    image: busybox", "    environment:",
    "      DB_HOST: openbrain-db", "      DB_USER: postgres",
    "  openbrain-api:", "    image: busybox", "    environment:",
    "      DB_HOST: openbrain-db", "      DB_USER: ob_app")

Remove-Item $root -Recurse -Force -ErrorAction SilentlyContinue
foreach ($c in $cases) {
    $dir = Join-Path $root $("case-" + $c.Name)
    New-Item -ItemType Directory (Join-Path $dir "scripts\checks") -Force | Out-Null
    Copy-Item $census (Join-Path $dir "scripts\checks") -Force
    if ($c.Name -eq "missing") { continue }   # the fixture IS the absent directory
    $od = Join-Path $dir "OB1\docker"
    New-Item -ItemType Directory $od -Force | Out-Null

    switch ($c.Name) {
        "unreadable" {
            # openbrain-api reaches postgres implicitly (DB_HOST, no DB_USER). It sits in the
            # file that will be made unreadable, so if the census silently skips that file the
            # ONLY thing left is a clean client and the run goes green on half a denominator.
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-api:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        "empty" {
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                "services:", "  some-frontend:", "    image: nginx", "    environment:", "      PORT: 8080")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  some-cron:", "    image: busybox")
        }
        "no-services" {
            # Accepted by the parser, and holds nothing. Its partner file still recognises a
            # client, so the aggregate "recognised nothing at all" guard never fires: this is
            # the per-file assertion's case and nothing else's.
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @("services: {}")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        "reindent" {
            # Every line two spaces deeper. Valid YAML, `docker compose config --services`
            # lists both services, and the line regex the census used to enumerate with
            # matches none of them.
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                ($rogueFirst | ForEach-Object { "  " + $_ }))
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        "unresolved" {
            # The census parses with --no-interpolate so that a checkout with no
            # OB1/docker/.env can be measured at all. This is that choice's cost, asserted:
            # a DB_USER that is still ${...} is unknown, and reading it as "not postgres"
            # would be a guess. Pre-fix the census substituted it to an empty string, fell
            # through to the implicit-postgres rule and called it an unexplained client -
            # the right exit code for the wrong reason, from a value it never resolved.
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-api:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: " + '${OB_APP_USER}')
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        "unexplained" {
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value $rogueFirst
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        default {
            # honest-zero and live-ghost share a compose: real clients, none of them
            # superuser. openbrain-mcp uses compose's LIST form on purpose. With
            # --no-interpolate docker does not normalise it to a map, and the first draft of
            # this fix only read the map - it aborted on the real files rather than reading
            # open-notebook-backup as not-a-client, but a fixture that only ever feeds one
            # form would not have said so.
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                "services:",
                "  openbrain-api:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app",
                "  openbrain-mcp:", "    image: busybox", "    environment:",
                "      - DB_HOST=openbrain-db", "      - DB_USER=ob_app_memory")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    image: busybox", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
    }
}

# The DENY ACE, and then a PROOF that it took. Running elevated, or on a volume that ignores
# it, would leave the file readable - and the case would quietly stop testing what it names.
# A fixture that could not be established is a cannot-measure, which is this whole item.
$denyTarget = Join-Path $root "case-unreadable\OB1\docker\docker-compose.yml"
& icacls $denyTarget /deny "$($env:USERNAME):(R)" 2>&1 | Out-Null
$stillReadable = $true
try { Get-Content $denyTarget -ErrorAction Stop | Out-Null } catch { $stillReadable = $false }
if ($stillReadable) {
    Say "ABORT: could not make the unreadable fixture unreadable - the DENY ACE did not take"
    Say "       (elevated shell, or a filesystem that ignores it). The case would pass by"
    Say "       reading the file it is supposed to fail on, so this run measures nothing."
    Cleanup; exit 2
}
Say ("  {0} fixture repo roots built; the unreadable one verified unreadable" -f $cases.Count)

# The re-indented fixture is only a test of anything if the real parser still accepts it. If
# docker rejects it the case would pass for the wrong reason - a parse failure is exit 2 as
# well - so establish it, the same way the DENY ACE is established.
$riFile = Join-Path $root "case-reindent\OB1\docker\docker-compose.yml"
$star   = [char]42
$riErr  = Join-Path $env:TEMP "wt-$Id-ri-err.txt"
$riSvcs = & cmd /c "docker compose --profile $star -f ""$riFile"" config --services 2>""$riErr"""
$riCode = $LASTEXITCODE
Remove-Item $riErr -Force -ErrorAction SilentlyContinue
if ($riCode -ne 0 -or @($riSvcs).Count -ne 2) {
    Say "ABORT: the re-indented fixture is not a valid compose project after all"
    Say ("       docker compose config --services exited {0} and listed {1} service(s)." -f $riCode, @($riSvcs).Count)
    Say "       The case would then be testing 'docker rejects garbage', not 'a differently"
    Say "       indented file is still fully measured'."
    Cleanup; exit 2
}
Say "  the re-indented fixture verified: still a valid project, still 2 services to docker"

# ------------------------------------------------------------------------------------------
# 2. A throwaway database - the census needs a live one for its other half
# ------------------------------------------------------------------------------------------
& cmd /c "docker rm -f $ghostNm 2>nul" | Out-Null
& cmd /c "docker rm -f $dbName 2>nul" | Out-Null
& cmd /c "docker network rm $netName 2>nul" | Out-Null
& docker network create $netName 2>&1 | Out-Null
& docker run -d --name $dbName --network $netName -e POSTGRES_PASSWORD=redprove `
    -e POSTGRES_DB=openbrain postgres:16-alpine 2>&1 | Out-Null
$ready = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep 1
    & docker exec $dbName pg_isready -U postgres -d openbrain 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
}
if (-not $ready) {
    Say "ABORT: throwaway database $dbName never became ready"
    & docker logs $dbName 2>&1 | Select-Object -Last 20 | ForEach-Object { Say "    $_" }
    Cleanup; exit 2
}
Say "  throwaway $dbName up on $netName (no ai-stack_* attachment, no :local tag)"

# ------------------------------------------------------------------------------------------
# 3. The cases
# ------------------------------------------------------------------------------------------
Say ""
Say ("{0,-14} {1,-5} {2,-6} {3,-7} {4}" -f "case", "exit", "want", "pre-fix", "what it feeds the census")
Say ("-" * 112)

$fail = 0
$ran  = 0
foreach ($c in $cases) {

    # The live-ghost case needs a client backend that is NOT superuser (so the verdict comes
    # out clean) and NOT in any compose file (so the compose half is provably short). It runs
    # last and is torn down immediately, because while it is up every other case would see it
    # too.
    if ($c.Ghost) {
        & docker exec $dbName psql -U postgres -d openbrain -q -c `
            "CREATE ROLE ob_ghost LOGIN PASSWORD 'redprove';" 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Say "ABORT: could not create the non-superuser ghost role"; Cleanup; exit 2 }
        & docker run -d --name $ghostNm --network $netName -e PGPASSWORD=redprove postgres:16-alpine `
            psql -h $dbName -U ob_ghost -d openbrain -c "SELECT pg_sleep(300)" 2>&1 | Out-Null
        $seen = $false
        for ($i = 0; $i -lt 30; $i++) {
            Start-Sleep 1
            $n = (& docker exec $dbName psql -U postgres -d openbrain -At -q -c `
                  "SELECT count(*) FROM pg_stat_activity WHERE usename='ob_ghost';" 2>&1 | Out-String).Trim()
            if ($n -match '^[1-9]') { $seen = $true; break }
        }
        if (-not $seen) {
            Say "ABORT: the ghost client never appeared in pg_stat_activity, so the case would"
            Say "       pass by there being nothing uncovered - which is not what it tests."
            & docker logs $ghostNm 2>&1 | Select-Object -Last 10 | ForEach-Object { Say "    $_" }
            Cleanup; exit 2
        }
    }

    $script = Join-Path $root $("case-" + $c.Name + "\scripts\checks\census-db-connection-roles.ps1")
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $script `
              -DbContainer $dbName -Database openbrain -Network $netName 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ran++
    $mark = if ($code -eq $c.Expect) { "  " } else { $fail++; "!!" }
    Say ("{0,-14} {1,-5} {2,-6} {3,-7} {4} {5}" -f $c.Name, $code, $c.Expect, $c.Was, $c.Why, $mark)
    if ($code -ne $c.Expect) {
        foreach ($l in ($out -split "`n" | Where-Object { $_ -match "ABORT|VERDICT|measured |parsed " })) {
            Say ("      | " + $l.Trim())
        }
    }

    if ($c.Ghost) { & cmd /c "docker rm -f $ghostNm 2>nul" | Out-Null }
}

Cleanup

# The count is asserted for the same reason the census now asserts its denominator: "0 cases
# failed" is also what a run that executed no cases reports. This file is the one place where
# that would be an embarrassing way to be wrong.
Say ""
if ($ran -ne $cases.Count) {
    Say ("RED-PROOF CANNOT MEASURE - ran {0} of {1} cases." -f $ran, $cases.Count)
    exit 2
}
if ($fail -eq 0) {
    Say ("RED-PROOF PASSED - {0} cases, 0 disagreements." -f $ran)
    Say "The pre-fix zeroes are now cannot-measures or findings, the honest zero is still a"
    Say "pass, and a real unexplained client is still exit 1 - including one in a file the"
    Say "old line regex could not see."
    exit 0
}
Say ("RED-PROOF FAILED - {0} of {1} cases disagreed." -f $fail, $ran)
exit 1
