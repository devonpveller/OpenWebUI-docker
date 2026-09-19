# census-db-connection-roles.ps1 - H1 (DFU section C.9) live census.
#
# "a census of live connections by role with zero unexplained superuser app clients."
#
# READ-ONLY. Three catalogue SELECTs against a running database, one `docker network
# inspect` to turn client_addr into a container name, and one `docker compose config` per
# compose file - which is client-side parsing, not a deployment. It starts nothing, stops
# nothing, changes nothing. It is safe to run against production and is meant to be.
#
# WHAT "UNEXPLAINED" MEANS HERE. A superuser connection is EXPLAINED only if the container it
# comes from is in the allow-list below WITH a reason. The allow-list is short on purpose and
# each entry names the privilege it actually needs, so a new superuser client cannot join the
# fleet by being forgotten.
#
# THE DENOMINATOR IS pg_stat_activity, NOT THE COMPOSE TEXT. C.9's H1 asks for a census of
# LIVE CONNECTIONS by role, and live connections are ground truth: they need no parsing and
# cannot be hidden by how a file is formatted. Compose is kept only as a SECONDARY half - it
# is the one thing pg_stat_activity cannot show, a client CONFIGURED to connect that happens
# to be idle (openbrain-ext and openbrain-suggestion-worker hold lazy pools and were both
# absent from the very first run of this script while configured as `postgres`). To earn that
# place the compose half has to be measured rather than pattern-matched, so:
#
#   1. it is parsed by `docker compose config`, the real parser, per file. An earlier version
#      enumerated services with the line regex `^  ([A-Za-z0-9_.-]+):\s*$`. A compose file
#      that is present, readable, valid YAML and accepted by docker but INDENTED differently
#      matches none of it: re-indenting the real OB1/docker/docker-compose.yml by two spaces
#      (still valid, `docker compose config --services` still lists every service) took the
#      census from "12 of 13 recognised clients across 41 service blocks" to "1 of 1 across
#      9" - 92% of the denominator gone, verdict issued anyway. Enumerating the shapes a
#      regex must survive is a losing game; the fix is to stop parsing YAML by hand.
#   2. every file must contribute a MEASURED count or the run aborts. Per file, not summed:
#      the aggregate guard this replaces only fired when BOTH files recognised nothing, so
#      one file collapsing to zero while the other still matched one client sailed through.
#      `services: {}`, a 0-byte file and a file that is not YAML all land here.
#   3. `--profile *`, because a profile-gated service is invisible to a default `config` -
#      openbrain-idea-refinery is exactly that and holds a live superuser connection today.
#      And `--no-interpolate`, because OB1's compose has a `${OPS_GATEWAY_KEY:?...}` required
#      variable: with substitution ON, a checkout without `OB1/docker/.env` - which is every
#      clean clone, and C.7b validates from a clean clone - cannot be parsed at all. Off, the
#      parse is identical everywhere and no secret is ever resolved into this script's memory.
#      The cost is that a value which IS a variable stays a variable, so any user or host this
#      script actually consults that still contains `${` is a cannot-measure, named, rather
#      than a value quietly read as "not postgres".
#   4. on the way to a GREEN, every live client backend must be identifiable and present in
#      that compose set. This is the check that ties the two halves together: a client the
#      database can see and the compose half cannot is proof the compose denominator is
#      short, and "zero unexplained" over a short denominator is not a pass. It runs AFTER
#      the unexplained set is computed, so a real finding still reports as a finding
#      (exit 1) rather than being downgraded to cannot-measure.
#
# Exit 0 = every superuser backend is explained, over inputs that were all measured. Exit 1 =
# at least one is not. Exit 2 = could not measure - which is NOT a pass. "Could not measure"
# means: the network cannot be inspected, the container is down, psql failed, a compose file
# this census needs is missing or unreadable, a compose file could not be parsed or yielded
# no services, the census query returned no client backend at all, the compose sweep
# recognised no database client, or a live client backend is missing from the compose
# denominator. A verdict is only worth its exit code if the inputs behind it were actually
# measured, so the exit 0 below states what it measured.

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
# 0. PREFLIGHT - the configured-client half has to be READABLE and PARSEABLE before anything
# ------------------------------------------------------------------------------------------
# This census has two halves - who is connected NOW, and who is CONFIGURED to connect - and
# either half alone is wrong, so a half that could not be read is a cannot-measure rather
# than a footnote. This used to be a `Say "(missing: ...)"` and a `continue` down in section
# 3, which meant a repo root with no OB1/ - exactly the state `git clone` without
# --recurse-submodules leaves, and the state U4 exists for - printed two notes, recognised
# zero configured clients, and exited 0 with "zero unexplained superuser application
# clients". A missing input is not evidence of absence.
#
# It runs BEFORE the database calls on purpose: a checkout that cannot be measured has no
# business opening a connection to a production database in order to find that out.
$composeFiles = @(
    (Join-Path $repoRoot "OB1\docker\docker-compose.yml"),
    (Join-Path $repoRoot "OB1\docker\docker-compose.scheduled.yml")
)
$unreadable  = @()
foreach ($cf in $composeFiles) {
    if (-not (Test-Path $cf)) { $unreadable += ("{0}  (missing)" -f $cf); continue }
    # -ErrorAction Stop, because $ErrorActionPreference is "Continue" here: without it a
    # permission-denied file prints a red error, contributes zero services, and the run
    # carries on to a verdict as though that file had simply held nothing. Checked here
    # rather than left to docker so the message names the cause instead of quoting a parser
    # error about a file the operator can see is present.
    try   { Get-Content $cf -TotalCount 1 -ErrorAction Stop | Out-Null }
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

# Now the real parser, one file at a time. `--profile *` is built as a char rather than
# written as a literal so nothing on the way to docker treats it as a glob. `--no-interpolate`
# for the reason in point 3 of the header - it is unconditional, not a fallback, so this
# script behaves identically in the operator's checkout and in a clean clone rather than
# taking a branch in one that was never exercised in the other. Stderr goes to a file via cmd
# because PowerShell 5.1 wraps a native command's stderr in ErrorRecords, which would print a
# wall of red above this script's own message.
$starArg    = [char]42
$cfgErrFile = Join-Path $env:TEMP ("h1census-cfg-" + [guid]::NewGuid().ToString("N") + ".txt")
$parsedSvcs = @{}   # compose file -> array of service properties from the resolved project
$svcCount   = @{}   # compose file -> how many services the real parser found in it
foreach ($cf in $composeFiles) {
    $stdout = & cmd /c "docker compose --profile $starArg -f ""$cf"" config --no-interpolate --format json 2>""$cfgErrFile"""
    $code   = $LASTEXITCODE
    $text   = ($stdout | Out-String)
    $perr   = ""
    if (Test-Path $cfgErrFile) { $perr = (("" + (Get-Content $cfgErrFile -Raw -ErrorAction SilentlyContinue)).Trim()) }

    $why = ""
    $cfg = $null
    if ($code -ne 0) {
        $why = "docker compose config exited $code"
    } else {
        try { $cfg = $text | ConvertFrom-Json } catch { $why = "its JSON did not parse: $($_.Exception.Message)" }
    }
    if ($why -eq "") {
        if ($null -eq $cfg -or -not $cfg.PSObject.Properties['services']) {
            $why = "the parsed project has no services key at all"
        } else {
            $props = @()
            if ($null -ne $cfg.services) { $props = @($cfg.services.PSObject.Properties) }
            if ($props.Count -eq 0) {
                $why = "the real parser accepted it and found ZERO services in it"
            } else {
                $parsedSvcs[$cf] = $props
                $svcCount[$cf]   = $props.Count
            }
        }
    }
    if ($why -ne "") {
        Remove-Item $cfgErrFile -Force -ErrorAction SilentlyContinue
        Say "ABORT: CANNOT MEASURE - a compose file this census requires could not be parsed:"
        Say ("  {0}" -f $cf)
        Say ("  {0}" -f $why)
        if ($perr -ne "") { foreach ($l in ($perr -split "`n")) { Say ("  | " + $l.Trim()) } }
        Say ""
        Say "Every compose file has to contribute a MEASURED service count or this run is over."
        Say "A file that yields no services is not a file with no database clients in it - it"
        Say "is a file this census did not read, and the configured-client half would be short"
        Say "by however much it holds while the verdict came out looking whole."
        exit 2
    }
}
Remove-Item $cfgErrFile -Force -ErrorAction SilentlyContinue

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
# 2. The census - THIS is the denominator
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
# 3. CONFIGURED clients - the half a census cannot see, read from the RESOLVED project
# ------------------------------------------------------------------------------------------
# openbrain-ext and openbrain-suggestion-worker hold LAZY pools: they open no connection until
# their first request, so both were absent from the very first run of this script while being
# configured to connect as `postgres` exactly like the eight that showed. A census alone would
# have declared them out of scope. Everything below reads the resolved project from
# `docker compose config` - service names, container names and the environment map after
# substitution and env_file merging - never the raw file text.
Say ""
Say "configured clients (compose), independent of who is connected:"
$configured   = @{}
$dbHosted     = @{}
$composeNames = @{}
$unresolved   = @()
foreach ($cf in $composeFiles) {
    foreach ($sp in $parsedSvcs[$cf]) {
        $svcName = $sp.Name
        $svc     = $sp.Value
        $composeNames[$svcName] = $true
        # The census names containers; compose names services. They are equal across this
        # fleet today, but container_name is what a live backend matches on if they ever
        # diverge, so both go into the set the coverage check below compares against.
        if ($svc.PSObject.Properties['container_name'] -and $svc.container_name) {
            $composeNames[[string]$svc.container_name] = $true
        }
        $envMap = $null
        if ($svc.PSObject.Properties['environment']) { $envMap = $svc.environment }
        if ($null -eq $envMap) { continue }
        # BOTH compose environment forms, flattened to key/value/was-a-value-given. With
        # substitution ON compose normalises the list form to an object; with
        # --no-interpolate it does not, and open-notebook-backup writes
        # `- OB1_DB_USER=postgres`. A shape this script cannot read is a cannot-measure, not
        # an empty environment - the first draft of this section only handled the object form
        # and aborted here rather than reading that service as not-a-client, which is the
        # behaviour being asked for even when it costs a round.
        $pairs = @()
        if ($envMap -is [System.Management.Automation.PSCustomObject]) {
            foreach ($ep in $envMap.PSObject.Properties) {
                $pairs += ,@($ep.Name, ("" + $ep.Value), ($null -ne $ep.Value))
            }
        } elseif ($envMap -is [System.Array]) {
            foreach ($e in $envMap) {
                $t = "" + $e
                $eq = $t.IndexOf("=")
                # No "=" means the value is inherited from the host environment at run time.
                if ($eq -ge 0) { $pairs += ,@($t.Substring(0, $eq), $t.Substring($eq + 1), $true) }
                else           { $pairs += ,@($t, "", $false) }
            }
        } else {
            Say ""
            Say ("ABORT: CANNOT MEASURE - service '{0}' in {1} has an environment this script" -f $svcName, $cf)
            Say ("cannot read: expected a JSON object or a list, got {0}. Its database user" -f $envMap.GetType().Name)
            Say "would be invisible and the service would be counted as not-a-client."
            exit 2
        }
        $userExplicit = ""
        $userUri      = ""
        $hostsDb      = $false
        foreach ($pair in $pairs) {
            $k       = $pair[0]
            $v       = $pair[1]
            $hasVal  = $pair[2]
            # A consulted key with no value at all is inherited from whatever environment the
            # container is started in - unknowable from here, and not the same as "not set".
            if (-not $hasVal -and ($k -eq "POSTGRES_USER" -or $k -like "*DB_USER" -or
                                   $k -eq "PGHOST" -or $k -like "*DB_HOST" -or $k -eq "PGRST_DB_URI")) {
                $unresolved += ("{0}  {1} (no value; inherited from the host environment)" -f $svcName, $k)
            }
            if ($k -eq "POSTGRES_USER" -or $k -like "*DB_USER") {
                if ($v -ne "") { $userExplicit = $v }
            } elseif ($k -eq "PGRST_DB_URI") {
                if ($v -match '^postgres(?:ql)?://([^:@/]+)[:@]') { $userUri = $Matches[1] }
            }
            if ($k -eq "PGHOST" -or $k -like "*DB_HOST") {
                if ($v -like "*openbrain-db*") { $hostsDb = $true }
                # A host that is still a variable cannot be compared to anything. Only the
                # keys this script actually consults are checked - PGRST_DB_URI's password
                # placeholder is none of its business, and the username in front of it is
                # literal.
                elseif ($v -like '*${*') { $unresolved += ("{0}  {1}={2}" -f $svcName, $k, $v) }
            }
        }
        if     ($userExplicit -ne "") { $configured[$svcName] = $userExplicit }
        elseif ($userUri      -ne "") { $configured[$svcName] = $userUri }
        if ($hostsDb) { $dbHosted[$svcName] = $true }
        if ($configured.ContainsKey($svcName) -and $configured[$svcName] -like '*${*') {
            $unresolved += ("{0}  database user={1}" -f $svcName, $configured[$svcName])
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
# A value this script consults that is STILL a variable was never resolved, and reading it as
# "not postgres" would be a guess wearing a measurement's clothes. Nothing in this fleet hits
# it - every DB_USER and DB_HOST in both files is a literal - so it fires only if that changes.
if ($unresolved.Count -gt 0) {
    Say ""
    Say ("ABORT: CANNOT MEASURE - {0} value(s) this census reads are unresolved variables:" -f $unresolved.Count)
    foreach ($u in $unresolved) { Say ("  {0}" -f $u) }
    Say ""
    Say "The compose files are parsed with --no-interpolate so that a checkout without"
    Say "OB1/docker/.env can be measured at all (a required variable otherwise fails the"
    Say "whole parse). The cost is this case: a user or host that is itself a variable is"
    Say "unknown, not clean. Re-run where the env file is, or make the value a literal."
    exit 2
}

$configuredSuper = @()
foreach ($k in ($configured.Keys | Sort-Object)) {
    $u = $configured[$k]
    Say ("  {0,-32} {1}" -f $k, $u)
    if ($u -like "postgres*" -and $k -ne "openbrain-db") { $configuredSuper += $k }
}
$totalSvcs = 0
foreach ($cf in $composeFiles) {
    $totalSvcs += $svcCount[$cf]
    Say ("  parsed {0,-4} service(s) from {1}" -f $svcCount[$cf], (Split-Path -Leaf $cf))
}
Say ("  -> {0} of {1} recognised database client(s), across {2} parsed service(s), configured as postgres" -f
     $configuredSuper.Count, $configured.Count, $totalSvcs)

# MEASURED-AND-ZERO vs COULD-NOT-MEASURE. $configuredSuper reaching zero is the GOAL of the
# promotion and is a real pass. $configured reaching zero is not: every compose file was
# parsed successfully above and every one of them yielded services, so recognising no database
# client at all in this fleet means the environment keys this section looks for are the wrong
# ones - not that the fleet stopped talking to postgres. Both used to print "0 service(s)
# configured to connect as postgres" and exit 0, and only one is a clean bill of health.
if ($configured.Count -eq 0) {
    Say ""
    Say ("ABORT: CANNOT MEASURE - parsed {0} compose file(s) and {1} service(s), and" -f $composeFiles.Count, $totalSvcs)
    Say "recognised NO database client among them. The candidate set is empty because nothing"
    Say "matched, not because nothing connects, and an empty denominator cannot produce a"
    Say "verdict. Check the DB_USER / PGRST_DB_URI / DB_HOST keys in this section against"
    Say "the resolved project (docker compose config) before trusting any result."
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

if ($unexplained.Count -gt 0) {
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
}

# THE TWO HALVES, TIED TOGETHER. Everything above says the superuser connections are all
# accounted for. That is only a pass if the compose half actually covers the fleet the
# database can see, so before the green: every live client backend must be identifiable
# (mapped to a container, not UNKNOWN@ip) and present in the parsed compose set. A client
# postgres reports and compose does not contain is proof the configured-client denominator is
# short by at least that much - the same defect this section exists to prevent, arriving from
# the other direction. `(local)` is excluded: it is a docker-exec psql over the unix socket
# and has no compose service by construction.
#
# Deliberately AFTER the unexplained set, not before it. A real superuser finding is the
# stronger signal and must still report as exit 1; downgrading it to cannot-measure would
# trade a finding for a shrug.
$liveNamed = @($rows | Where-Object { $_.Container -ne "(local)" } |
                Select-Object -ExpandProperty Container -Unique)
$uncovered = @($liveNamed | Where-Object { -not $composeNames.ContainsKey($_) })
if ($uncovered.Count -gt 0) {
    Say ""
    Say ("ABORT: CANNOT MEASURE - {0} live client backend(s) are absent from the compose" -f $uncovered.Count)
    Say "denominator this run was about to declare clean:"
    foreach ($c in $uncovered) {
        $n = @($rows | Where-Object { $_.Container -eq $c }).Count
        Say ("  {0}  ({1} connection(s))" -f $c, $n)
    }
    Say ""
    Say "pg_stat_activity is ground truth and it can see clients the configured-client half"
    Say "cannot. Either a client connects from outside these compose files, or its address"
    Say "did not map to a container on $Network. Either way the compose half is short, and"
    Say "'zero unexplained' measured over a short denominator is not a pass."
    exit 2
}

Say ""
Say "VERDICT: zero unexplained superuser application clients."
# Printed because "zero unexplained" only means something next to what was examined to
# get there. Every number here is asserted above: a compose file that cannot be parsed or
# yields no services, zero recognised clients, zero live backends, or a live backend the
# compose set does not contain all exit 2 rather than reaching this line.
Say ("  parsed {0} compose file(s) / {1} service(s), {2} recognised database client(s)," -f
     $composeFiles.Count, $totalSvcs, $configured.Count)
Say ("  {0} live client backend(s) - all covered, {1} superuser/bypassrls backend(s), {2} candidate(s) - each explained above." -f
     $rows.Count, $superConns, $candidates.Count)
exit 0
