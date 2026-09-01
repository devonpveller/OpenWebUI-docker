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
# A fix to that is only worth its commit message if the failing case is reproducible, so this
# script IS the reproduction. It builds five repo roots whose only difference is the state of
# the compose input, points the REAL census script at a throwaway database, and asserts the
# exit code of each. Three of the five returned 0 before the fix; the table below records
# that, so the contrast is visible without checking out the old script.
#
# EVERYTHING IS THROWAWAY. Its own docker network, a stock postgres:16-alpine, fixture repo
# roots under TEMP. It never reads openbrain-db, never attaches to an ai-stack_* network,
# never builds or tags an image.
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
# 1. Fixtures - five repo roots differing only in the compose input
# ------------------------------------------------------------------------------------------
# Each case is a MINIMAL repo root: <root>\<case>\scripts\checks\census-...ps1 plus whatever
# OB1\docker holds, because the census derives its repo root two levels up from its own
# location. Nothing here is a copy of the real compose files - the point is the SHAPE of the
# input, and a fixture that tracked the real fleet would go stale and start testing nothing.
$cases = @(
    @{ Name = "missing";     Expect = 2; Was = 0
       Why  = "no OB1/ at all - the plain git-clone state, and U4's whole scenario" }
    @{ Name = "unreadable";  Expect = 2; Was = 0
       Why  = "compose present but permission-denied - the half that IS readable is clean" }
    @{ Name = "empty";       Expect = 2; Was = 0
       Why  = "both files read fine and no database client is recognised in either" }
    @{ Name = "honest-zero"; Expect = 0; Was = 0
       Why  = "real clients, all on non-superuser roles - a measured zero, and a real pass" }
    @{ Name = "unexplained"; Expect = 1; Was = 1
       Why  = "a client configured as postgres and not on the allow-list" }
)

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
                "services:", "  openbrain-api:", "    environment:", "      DB_HOST: openbrain-db")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    environment:",
                "      DB_HOST: openbrain-db", "      DB_USER: ob_app")
        }
        "empty" {
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value @(
                "services:", "  some-frontend:", "    image: nginx", "    environment:", "      PORT: 8080")
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  some-cron:", "    image: busybox")
        }
        default {
            # honest-zero and unexplained share a shape; only the extra service differs.
            $svc = @("services:", "  openbrain-api:", "    environment:",
                     "      DB_HOST: openbrain-db", "      DB_USER: ob_app",
                     "  openbrain-mcp:", "    environment:",
                     "      DB_HOST: openbrain-db", "      DB_USER: ob_app_memory")
            if ($c.Name -eq "unexplained") {
                $svc += @("  openbrain-rogue:", "    environment:",
                          "      DB_HOST: openbrain-db", "      DB_USER: postgres")
            }
            Set-Content (Join-Path $od "docker-compose.yml") -Encoding Ascii -Value $svc
            Set-Content (Join-Path $od "docker-compose.scheduled.yml") -Encoding Ascii -Value @(
                "services:", "  openbrain-cron:", "    environment:",
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
Say "  5 fixture repo roots built; the unreadable one verified unreadable"

# ------------------------------------------------------------------------------------------
# 2. A throwaway database - the census needs a live one for its other half
# ------------------------------------------------------------------------------------------
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
Say ("-" * 108)

$fail = 0
$ran  = 0
foreach ($c in $cases) {
    $script = Join-Path $root $("case-" + $c.Name + "\scripts\checks\census-db-connection-roles.ps1")
    $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $script `
              -DbContainer $dbName -Database openbrain -Network $netName 2>&1 | Out-String
    $code = $LASTEXITCODE
    $ran++
    $mark = if ($code -eq $c.Expect) { "  " } else { $fail++; "!!" }
    Say ("{0,-14} {1,-5} {2,-6} {3,-7} {4} {5}" -f $c.Name, $code, $c.Expect, $c.Was, $c.Why, $mark)
    if ($code -ne $c.Expect) {
        foreach ($l in ($out -split "`n" | Where-Object { $_ -match "ABORT|VERDICT|measured " })) {
            Say ("      | " + $l.Trim())
        }
    }
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
    Say "The three pre-fix zeroes are now cannot-measures, the honest zero is still a pass,"
    Say "and a real unexplained client is still exit 1."
    exit 0
}
Say ("RED-PROOF FAILED - {0} of {1} cases disagreed." -f $fail, $ran)
exit 1
