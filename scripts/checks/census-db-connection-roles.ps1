# census-db-connection-roles.ps1 - H1 (DFU section C.9) live census.
#
# "a census of live connections by role with zero unexplained superuser app clients."
#
# READ-ONLY. Three catalogue SELECTs against a running database and one `docker network
# inspect` to turn client_addr into a container name. It starts nothing, stops nothing,
# changes nothing. It is safe to run against production and is meant to be.
#
# WHAT "UNEXPLAINED" MEANS HERE. A superuser connection is EXPLAINED only if the container it
# comes from is in the allow-list below WITH a reason. The allow-list is short on purpose and
# each entry names the privilege it actually needs, so a new superuser client cannot join the
# fleet by being forgotten.
#
# Exit 0 = every superuser backend is explained. Exit 1 = at least one is not. Exit 2 = could
# not measure - which is NOT a pass. "Could not measure" means: the network cannot be
# inspected, the container is down, psql failed, a compose file this census needs is missing
# or unreadable, the census query returned no client backend at all, or the compose sweep
# recognised no database client. The last three are the ones this script used to get wrong -
# it degraded them to a printed note and then reported a clean bill of health over a
# denominator it had never read. A verdict is only worth its exit code if the inputs behind
# it were actually measured, so the exit 0 below states what it measured.

[CmdletBinding()]
param(
    [string]$DbContainer = "openbrain-db",
    [string]$Database    = "openbrain",
    [string]$Network     = "open-brain_obnet"
)

$ErrorActionPreference = "Continue"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# ------------------------------------------------------------------------------------------
# The allow-list: superuser connections that are CORRECT, each with the reason.
# ------------------------------------------------------------------------------------------
# Anything not in here that connects as a superuser is a finding, not a footnote.
$explained = @{
    "openbrain-postgrest" = "PostgREST authenticator. Switches role to PGRST_DB_ANON_ROLE (service_role) for every request, so its statements ARE bound; the connection itself is still superuser and moving it to a dedicated authenticator role is promotion step 5."
    "openbrain-db-backup" = "pg_dump. A non-BYPASSRLS dumper silently omits every row its policies hide, which would turn the backup into a partial one WITHOUT failing. This one must stay superuser."
    "openbrain-ext"       = "BLOCKED, not exempt. Its 15 extension/CRM tables are governed only by auth.uid() = user_id against a stub that returns NULL, so a non-superuser role reads them as EMPTY, silently. See H1-APP-ROLE-PROMOTION.md."
    "(local)"             = "Operator psql over the unix socket (docker exec). DDL, health probes, restores and the promotion itself. Not an application."
}

function Say([string]$m) { Write-Host $m }

# ------------------------------------------------------------------------------------------
# 0. PREFLIGHT - the configured-client denominator has to be READABLE before anything else
# ------------------------------------------------------------------------------------------
# This census has two halves - who is connected NOW, and who is CONFIGURED to connect - and
# either half alone is wrong, so a half that could not be read is a cannot-measure rather
# than a footnote. This used to be a `Say "(missing: ...)"` and a `continue` down in section
# 3, which meant a repo root with no OB1/ - exactly the state `git clone` without
# --recurse-submodules leaves, and the state U4 exists for - printed two notes, recognised
# zero configured clients, and exited 0 with "zero unexplained superuser application
# clients". A missing input is not evidence of absence.
#
# It runs BEFORE the docker calls on purpose: a checkout that cannot be measured has no
# business opening a connection to a production database in order to find that out.
$composeFiles = @(
    (Join-Path $repoRoot "OB1\docker\docker-compose.yml"),
    (Join-Path $repoRoot "OB1\docker\docker-compose.scheduled.yml")
)
$composeText = @{}
$unreadable  = @()
foreach ($cf in $composeFiles) {
    if (-not (Test-Path $cf)) { $unreadable += ("{0}  (missing)" -f $cf); continue }
    # -ErrorAction Stop, because $ErrorActionPreference is "Continue" here: without it a
    # permission-denied file prints a red error, contributes zero services, and the run
    # carries on to a verdict as though that file had simply held nothing.
    try   { $composeText[$cf] = @(Get-Content $cf -ErrorAction Stop) }
    catch { $unreadable += ("{0}  ({1})" -f $cf, $_.Exception.Message) }
}
if ($unreadable.Count -gt 0) {
    Say "ABORT: CANNOT MEASURE - a compose file this census requires could not be read:"
    foreach ($u in $unreadable) { Say "  $u" }
    Say ""
    Say "The configured-client half of the census would be absent or short, and a verdict"
    Say "over a denominator this script never read is not a pass. If OB1/ is empty this is"
    Say "an uninitialised submodule: git submodule update --init"
    exit 2
}

# ------------------------------------------------------------------------------------------
# 1. client_addr -> container name
# ------------------------------------------------------------------------------------------
$ipToName = @{}
# A ";" separator, not a docker newline template: PowerShell rewrites the backslash in
# that template before docker sees it and the format string fails to parse.
$raw = docker network inspect $Network --format '{{range .Containers}}{{.Name}} {{.IPv4Address}};{{end}}' 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Say "ABORT: cannot inspect network $Network"
    Say $raw
    exit 2
}
foreach ($line in ($raw -split ";")) {
    $t = $line.Trim()
    if ($t -eq "") { continue }
    $parts = $t -split "\s+"
    if ($parts.Count -ge 2) { $ipToName[($parts[1] -split "/")[0]] = $parts[0] }
}

# ------------------------------------------------------------------------------------------
# 2. The census
# ------------------------------------------------------------------------------------------
$q = @"
SELECT COALESCE(a.usename,'?') || '|' ||
       COALESCE(r.rolsuper::text,'?') || '|' ||
       COALESCE(r.rolbypassrls::text,'?') || '|' ||
       COALESCE(a.application_name,'-') || '|' ||
       COALESCE(host(a.client_addr),'(local)')
  FROM pg_stat_activity a
  LEFT JOIN pg_roles r ON r.rolname = a.usename
 WHERE a.backend_type = 'client backend';
"@
$out = ($q | docker exec -i $DbContainer psql -U postgres -d $Database -At -q 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) {
    Say "ABORT: census query failed"
    Say $out
    exit 2
}

$rows = @()
foreach ($line in ($out -split "`n")) {
    $t = $line.Trim()
    if ($t -eq "" -or $t -notmatch "\|") { continue }
    $f = $t -split "\|"
    $addr = $f[4]
    $name = if ($addr -eq "(local)") { "(local)" }
            elseif ($ipToName.ContainsKey($addr)) { $ipToName[$addr] }
            else { "UNKNOWN@$addr" }
    $rows += [PSCustomObject]@{
        Role = $f[0]; Super = ($f[1] -eq "true"); Bypass = ($f[2] -eq "true")
        App = $f[3]; Addr = $addr; Container = $name
    }
}

# psql is itself a client backend, so this collection is never legitimately empty: it is 0
# only if the output stopped parsing. Left alone, an empty $rows makes every superuser count
# below read "0 of 0" and drives the verdict green.
if ($rows.Count -eq 0) {
    Say "ABORT: CANNOT MEASURE - the census query returned no client backend rows."
    Say "The psql running this query is itself a client backend, so zero is not a possible"
    Say "true answer: the query succeeded but its output did not parse. Raw output:"
    Say $out
    exit 2
}

Say "H1 census - connections to $DbContainer by role"
Say ("measured {0}  ({1} client backends)" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $rows.Count)
Say ""
Say ("{0,-28} {1,-16} {2,-18} {3,-6} {4,-8} {5}" -f "container", "role", "application_name", "super", "bypass", "n")
Say ("-" * 100)

$groups = $rows | Group-Object Container, Role, App, Super, Bypass | Sort-Object Name
foreach ($g in $groups) {
    $r = $g.Group[0]
    Say ("{0,-28} {1,-16} {2,-18} {3,-6} {4,-8} {5}" -f
         $r.Container, $r.Role, $r.App, $r.Super.ToString().ToLower(), $r.Bypass.ToString().ToLower(), $g.Count)
}

# ------------------------------------------------------------------------------------------
# 3. CONFIGURED clients - because a census only sees who is connected RIGHT NOW
# ------------------------------------------------------------------------------------------
# openbrain-ext and openbrain-suggestion-worker hold LAZY pools: they open no connection until
# their first request, so both were absent from the very first run of this script while being
# configured to connect as `postgres` exactly like the eight that showed. A census alone would
# have declared them out of scope. This section reads the compose files instead, so the
# denominator is what is CONFIGURED, not what happens to be busy.
Say ""
Say "configured clients (compose), independent of who is connected:"
# Read in the preflight, so there is no path from here to a verdict that skipped a file.
$configured   = @{}
$dbHosted     = @{}
$servicesSeen = 0
foreach ($cf in $composeFiles) {
    $svc = ""
    foreach ($line in $composeText[$cf]) {
        if ($line -match '^  ([A-Za-z0-9_.-]+):\s*$') { $svc = $Matches[1]; $servicesSeen++; continue }
        if ($svc -eq "") { continue }
        if ($line -match '(DB_USER|OB1_DB_USER|POSTGRES_USER)\s*[:=]\s*(\S+)') {
            $configured[$svc] = $Matches[2]
        } elseif ($line -match 'PGRST_DB_URI\s*:\s*postgres://([^:]+):') {
            $configured[$svc] = $Matches[1]
        } elseif ($line -match '(DB_HOST|OB1_DB_HOST|PGHOST)\s*[:=]\s*(\S*openbrain-db\S*)') {
            $dbHosted[$svc] = $true
        }
    }
}
# A service that names the database but sets NO user is not unconfigured - EVERY client in
# this fleet hardcodes `Deno.env.get("DB_USER") || "postgres"`, so a missing DB_USER IS
# postgres. openbrain-idea-refinery is exactly that case, and a sweep that only read explicit
# DB_USER lines would have reported it as not-a-client while it held a live superuser
# connection. Measured 2026-08-31.
foreach ($k in $dbHosted.Keys) {
    if (-not $configured.ContainsKey($k)) { $configured[$k] = "postgres (implicit: no DB_USER set)" }
}
$configuredSuper = @()
foreach ($k in ($configured.Keys | Sort-Object)) {
    $u = $configured[$k]
    Say ("  {0,-32} {1}" -f $k, $u)
    if ($u -like "postgres*" -and $k -ne "openbrain-db") { $configuredSuper += $k }
}
Say ("  -> {0} of {1} recognised database client(s), across {2} service block(s), configured as postgres" -f
     $configuredSuper.Count, $configured.Count, $servicesSeen)

# MEASURED-AND-ZERO vs COULD-NOT-MEASURE. $configuredSuper reaching zero is the GOAL of the
# promotion and is a real pass. $configured reaching zero is not: every compose file was read
# successfully above, so recognising no database client at all in this fleet means the parse
# matched nothing - a renamed env var, a restructured compose, the wrong file - not that the
# fleet stopped talking to postgres. Both used to print "0 service(s) configured to connect
# as postgres" and exit 0, and only one of them is a clean bill of health.
if ($configured.Count -eq 0) {
    Say ""
    Say ("ABORT: CANNOT MEASURE - read {0} compose file(s) and {1} service block(s), and" -f $composeFiles.Count, $servicesSeen)
    Say "recognised NO database client among them. The candidate set is empty because nothing"
    Say "matched, not because nothing connects, and an empty denominator cannot produce a"
    Say "verdict. Check the DB_USER / PGRST_DB_URI / DB_HOST patterns in this section against"
    Say "the compose files before trusting any result from this script."
    exit 2
}

# ------------------------------------------------------------------------------------------
# 4. The verdict
# ------------------------------------------------------------------------------------------
Say ""
$superRows = @($rows | Where-Object { $_.Super -or $_.Bypass })
$superConns = $superRows.Count
Say ("superuser / bypassrls backends: {0} of {1}" -f $superConns, $rows.Count)

$unexplained = @()
$candidates = @(@($superRows | Select-Object -ExpandProperty Container -Unique) + $configuredSuper |
                Select-Object -Unique)
foreach ($c in $candidates) {
    if ($explained.ContainsKey($c)) {
        Say ("  EXPLAINED  {0}" -f $c)
        Say ("             {0}" -f $explained[$c])
    } else {
        $unexplained += $c
    }
}

if ($unexplained.Count -eq 0) {
    Say ""
    Say "VERDICT: zero unexplained superuser application clients."
    # Printed because "zero unexplained" only means something next to what was examined to
    # get there. Every number here is guarded above: zero compose files, zero recognised
    # clients or zero live backends exit 2 rather than reaching this line.
    Say ("  measured {0} compose file(s), {1} service block(s), {2} recognised database client(s)," -f
         $composeFiles.Count, $servicesSeen, $configured.Count)
    Say ("  {0} live client backend(s), {1} superuser/bypassrls backend(s), {2} candidate(s) - each explained above." -f
         $rows.Count, $superConns, $candidates.Count)
    exit 0
}

Say ""
Say ("VERDICT: {0} UNEXPLAINED superuser application client(s):" -f $unexplained.Count)
foreach ($c in $unexplained) {
    $n = @($superRows | Where-Object { $_.Container -eq $c }).Count
    $note = if ($n -eq 0) { "configured as postgres; idle at census time" } else { "$n connection(s)" }
    Say ("  {0}  ({1})" -f $c, $note)
}
Say ""
Say "Each of these connects with rolsuper/rolbypassrls, so every RLS policy in"
Say "init-agent-memory-rls.sql / init-graph-plane-rls.sql is inert for it."
Say "Fix: documentation/implementation-guide/dark-factory-unification/H1-APP-ROLE-PROMOTION.md"
exit 1
