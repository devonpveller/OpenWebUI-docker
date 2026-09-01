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
# not measure (container down, psql failed) - which is NOT a pass.

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
$composeFiles = @(
    (Join-Path $repoRoot "OB1\docker\docker-compose.yml"),
    (Join-Path $repoRoot "OB1\docker\docker-compose.scheduled.yml")
)
$configured = @{}
$dbHosted   = @{}
foreach ($cf in $composeFiles) {
    if (-not (Test-Path $cf)) { Say "  (missing: $cf)"; continue }
    $svc = ""
    foreach ($line in (Get-Content $cf)) {
        if ($line -match '^  ([A-Za-z0-9_.-]+):\s*$') { $svc = $Matches[1]; continue }
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
Say ("  -> {0} service(s) configured to connect as postgres" -f $configuredSuper.Count)

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
