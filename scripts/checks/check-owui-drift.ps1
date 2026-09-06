#requires -Version 5
<#
.SYNOPSIS
  Report which owui/ deploy-by-paste snapshots differ from what Open WebUI is
  actually running right now.

.DESCRIPTION
  These plugins deploy BY PASTE. Nothing links the file in git to the row in the
  live webui.db, so a fix can sit in the repo, committed and reviewed, while the
  container keeps serving the old body - which is exactly what happened to the
  `deep_research` banner between 2026-09-04 and 2026-09-06, and what a manual
  "all 16 files are byte-identical" note in owui/README.md (2026-08-20) stopped
  being able to say the moment it was written.

  For every row of owui/manifest.csv this compares:
    repo side - CR-normalised SHA-256 of the file on disk (bytes read, every
                0x0D dropped, then hashed)
    live side - CR-normalised SHA-256 of that row's `content` column, computed
                INSIDE the container so no plugin body ever crosses the boundary
  and prints one line per row:
    IN SYNC       hashes match
    DIFFERS       both present, hashes differ (see WHAT A GREEN DOES NOT PROVE)
    MISSING LIVE  the manifest names an owui_id with no row in the live table
    MISSING REPO  the manifest names a file that is not on disk
    UNKNOWN TYPE  the manifest's `type` maps to no table - the row was NOT
                  compared, and that is counted as a difference, never as clean

  manifest `type` -> live table: tool -> tool; action/filter/pipe -> function;
  skill -> skill (skills carry a `content` column of their own and are in the
  manifest, so leaving them unchecked would have been a silent hole).

  READ-ONLY BY CONSTRUCTION. The container's DB is opened with the SQLite URI
  `mode=ro`, which refuses any write at the engine level, and the only SQL this
  script builds is `select id,content,updated_at from <table>`. It prints
  hashes, ids, paths and timestamps - never content, never a valve, never an
  environment value.

.NOTES
  WHAT A GREEN PROVES: for every manifest row, the live `content` is
  byte-identical to the repo file after CR-normalisation, at the moment of the
  run.

  WHAT A GREEN DOES NOT PROVE:
    - that the live plugin WORKS, or that OWUI has reloaded it (OWUI reloads a
      tool when the DB content differs from its cached module; content equal to
      the repo does not mean the running module is that content)
    - that VALVES are right. Valves live in a separate column and are never read
      here; a paste through the Admin UI can reset them.
    - that the live instance holds nothing else. This is manifest-driven: a live
      tool or function that no manifest row names is invisible to it.
    - WHICH SIDE IS NEWER when a row DIFFERS. Content hashes cannot answer that.
      The live `updated_at` is printed so the operator can decide; the repo
      mtime is deliberately not, because a checkout rewrites it.
    - anything at all about a run that REFUSED. A refusal is exit 2 and a
      sentence; it is never a clean bill.

  TWO EDGES THAT ARE WRONG IN PRINCIPLE AND UNREACHABLE AGAINST THIS SCHEMA
  (measured by the tester 2026-09-06; recorded so a schema change re-opens them):
    - a SQL NULL live `content` is coerced to '' by `(x or '')`, so it would read
      IN SYNC against a 0-byte repo file - "no content stored" is not "empty
      content". Unreachable: `content` is TEXT NOT NULL on tool, function and
      skill, and no manifest row's file is empty.
    - a live row whose `updated_at` is not an integer, or whose id contains a
      `|`, fails the line filter below and is then reported MISSING LIVE even
      though the row exists with matching content. Fail-SAFE in direction (it
      reports drift, never a clean bill) but the STATUS is wrong. Unreachable:
      `updated_at` is NOT NULL integer on all three tables and no live id
      contains a pipe.

  EXIT CODES
    0  every manifest row IN SYNC
    1  at least one row DIFFERS / MISSING LIVE / MISSING REPO / UNKNOWN TYPE
    2  REFUSED - docker, the container, the DB or the manifest could not be read

  -CountOnly prints just the drifted count on stdout (or the word REFUSED, with
  the sentence on stderr) for scripts/stack/stack.ps1 health.
#>
[CmdletBinding()]
param(
    [string]$Container = 'openwebui',
    [string]$Manifest,
    [string]$DbPath = '/app/backend/data/webui.db',
    [switch]$CountOnly
)

$ErrorActionPreference = 'Stop'

function Say([string]$msg) { if (-not $CountOnly) { Write-Host $msg } }

function Die([string]$sentence) {
    if ($CountOnly) {
        [Console]::Error.WriteLine("REFUSED: $sentence")
        Write-Output 'REFUSED'
    } else {
        Write-Host "REFUSED: $sentence" -ForegroundColor Red
        Write-Host "Nothing was compared, so nothing is known about drift." -ForegroundColor Red
    }
    exit 2
}

# --- inputs ----------------------------------------------------------------
if (-not $Manifest) {
    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $Manifest = Join-Path $repoRoot 'owui\manifest.csv'
}
if (-not (Test-Path -LiteralPath $Manifest)) {
    Die "the manifest '$Manifest' does not exist, so there is no list of files to compare."
}
$manifestDir = Split-Path -Parent (Resolve-Path -LiteralPath $Manifest).Path

$rows = @()
try { $manifestLines = [System.IO.File]::ReadAllLines($Manifest) }
catch { Die "the manifest '$Manifest' could not be read." }
if ($manifestLines.Count -lt 2) { Die "the manifest '$Manifest' has no data rows." }
if ($manifestLines[0] -notmatch '^file,type,name,owui_id,') {
    Die "the manifest header is '$($manifestLines[0])', which is not the expected 'file,type,name,owui_id,sha256'."
}
foreach ($line in $manifestLines[1..($manifestLines.Count - 1)]) {
    if (-not $line.Trim()) { continue }
    $parts = $line.Split(',')
    if ($parts.Count -lt 5) { Die "manifest row '$line' has fewer than 5 columns." }
    # Take the id from the END so a comma inside `name` cannot shift it.
    $rows += [pscustomobject]@{
        File = $parts[0].Trim()
        Type = $parts[1].Trim()
        Id   = $parts[$parts.Count - 2].Trim()
    }
}
if ($rows.Count -eq 0) { Die "the manifest '$Manifest' lists no rows." }

# --- live side -------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Die "the docker CLI is not on PATH, so the live database cannot be read."
}
$state = ''
# Two PowerShell 5.1 traps, both of which turn a healthy read into a wrong answer:
#   - `Select-Object -First 1` stops the pipeline early, which kills the native
#     process and leaves $LASTEXITCODE = -1 for a container that IS running;
#   - under $ErrorActionPreference = 'Stop', anything a native command writes to
#     stderr becomes a terminating NativeCommandError (and $LASTEXITCODE = -1),
#     so a harmless docker warning would read as an unreadable database.
# Hence: capture whole, and drop to 'Continue' around the native calls so the
# real exit code is what decides.
$inspect = @()
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { $inspect = @(& docker inspect -f '{{.State.Running}}' $Container 2>$null) } catch { }
$inspectRc = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($inspectRc -eq 0 -and $inspect.Count -gt 0) { $state = "$($inspect[0])" }
if ($inspectRc -ne 0 -or -not $state) {
    Die "container '$Container' was not found by docker inspect, so the live database cannot be read."
}
if ("$state".Trim() -ne 'true') {
    Die "container '$Container' exists but is not running (State.Running=$("$state".Trim())), so the live database cannot be read."
}

# One statement, single quotes only, so PowerShell hands it to docker unmangled.
# It hashes inside the container: no plugin body ever crosses the boundary.
$py = @'
import sqlite3,hashlib;c=sqlite3.connect('file:DBPATH?mode=ro',uri=True);print(chr(10).join('|'.join([t,i,hashlib.sha256((x or '').encode('utf-8').replace(b'\r',b'')).hexdigest(),str(u)]) for t in ('tool','function','skill') for i,x,u in c.execute('select id,content,updated_at from '+t)))
'@
$py = $py.Replace('DBPATH', $DbPath)

$raw = @()
$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
try { $raw = @(& docker exec $Container python3 -c $py 2>$null) } catch { }
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEap
if ($rc -ne 0) {
    Die "reading '$DbPath' in container '$Container' failed (exit $rc) - the file, a table or python3 is missing, or the database is unreadable."
}

$live = @{}
foreach ($l in $raw) {
    if ("$l" -match '^(tool|function|skill)\|([^|]+)\|([0-9a-f]{64})\|(-?\d+)$') {
        $live["$($Matches[1])|$($Matches[2])"] = [pscustomobject]@{
            Sha = $Matches[3]; Updated = [int64]$Matches[4]
        }
    }
}
if ($live.Count -eq 0) {
    Die "the query against '$DbPath' in container '$Container' returned no readable rows."
}

# --- repo side -------------------------------------------------------------
function Get-CrNormalisedSha([string]$path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $buf = New-Object byte[] ($bytes.Length)
    $n = 0
    foreach ($b in $bytes) { if ($b -ne 13) { $buf[$n] = $b; $n++ } }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { $digest = $sha.ComputeHash($buf, 0, $n) } finally { $sha.Dispose() }
    return ([BitConverter]::ToString($digest).Replace('-', '').ToLower())
}

$tableFor = @{ tool = 'tool'; action = 'function'; filter = 'function'; pipe = 'function'; skill = 'skill' }

$inSync = 0; $differs = 0; $missingLive = 0; $missingRepo = 0; $unknown = 0
Say ("== owui/ snapshots vs live webui.db in container '$Container'")
Say ("   manifest: $Manifest")
Say ''
foreach ($r in ($rows | Sort-Object File)) {
    $table = $tableFor[$r.Type]
    if (-not $table) {
        $unknown++
        Say ('{0,-13} {1,-46} type={2}' -f 'UNKNOWN TYPE', $r.File, $r.Type)
        continue
    }
    $path = Join-Path $manifestDir $r.File
    $key = "$table|$($r.Id)"
    $hasRepo = Test-Path -LiteralPath $path
    $hasLive = $live.ContainsKey($key)

    if (-not $hasRepo) {
        $missingRepo++
        $tail = if ($hasLive) { '' } else { '  (and no live row either)' }
        Say ('{0,-13} {1,-46} {2}{3}' -f 'MISSING REPO', $r.File, $key, $tail)
        continue
    }
    if (-not $hasLive) {
        $missingLive++
        Say ('{0,-13} {1,-46} {2}' -f 'MISSING LIVE', $r.File, $key)
        continue
    }

    $repoSha = Get-CrNormalisedSha $path
    if ($repoSha -eq $live[$key].Sha) {
        $inSync++
        Say ('{0,-13} {1,-46} {2}' -f 'IN SYNC', $r.File, $key)
    } else {
        $differs++
        $when = ([datetimeoffset]::FromUnixTimeSeconds($live[$key].Updated)).UtcDateTime.ToString('yyyy-MM-dd HH:mm:ss')
        Say ('{0,-13} {1,-46} {2}' -f 'DIFFERS', $r.File, $key)
        Say ("              repo $repoSha")
        Say ("              live $($live[$key].Sha)  live updated_at $when UTC")
    }
}

$drifted = $differs + $missingLive + $missingRepo + $unknown
Say ''
Say ("$($rows.Count) manifest rows: $inSync in sync, $differs differ, $missingLive missing live, $missingRepo missing repo, $unknown unknown type.")
if ($drifted -eq 0) {
    Say 'IN SYNC: every manifest row matches the live content. This says nothing about valves, about whether the plugin works, or about live rows the manifest does not name.'
} else {
    Say "DRIFT: $drifted of $($rows.Count) MANIFEST rows do not match the live content. This says nothing about live rows the manifest does not name. Content alone cannot say which side is newer - the live updated_at is above; the operator decides."
}
if ($CountOnly) { Write-Output $drifted }
if ($drifted -gt 0) { exit 1 }
exit 0
