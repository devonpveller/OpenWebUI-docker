#requires -Version 5
<#
.SYNOPSIS
  Fail when a file INSERTS into the corpus tables (`thoughts`, `agent_memories`) over
  PostgREST without stating the plane (`exposure`) at its own call site.

.DESCRIPTION
  WHY THIS EXISTS, AND WHAT IT IS PAYING FOR.

  `init-agent-memory-exposure-column.sql` section 7 made `thoughts.exposure` a NOT NULL
  column with no default and made `upsert_thought` refuse a payload that omits it. Its
  post-condition said "Every caller of this rpc in the tree was found and given an explicit
  exposure: 'ops'", and it was true - of the RPC callers, because the sweep that produced it
  was `grep -rn 'rpc("upsert_thought"' OB1`. The DIRECT-table producers were never searched
  for. There are seven of them, they POST straight at the table, and the search term had
  defined the finding.

  That was not hypothetical. `openbrain-gmail-pull` runs daily and has been refused
  `42501 new row violates row-level security policy for table "thoughts"` since U5's
  already-live ops-plane policy landed (measured 2026-08-31, first failure 05:00; the seven
  days before it are green). The other producers are LATENT, not safe - they simply had not
  run.

  A HAND-LIST OF PRODUCERS IS THE SAME MISTAKE ONE ROUND LATER. So this gate does not carry
  one. It DERIVES the producer set from the tree on every run:

    1. find every BARE corpus-table path - a PostgREST collection URL with no filter or
       query on it. In PostgREST, POST to the bare collection IS the insert; a path carrying
       `?id=eq.`, `?select=` or any other filter is a read, a patch or a delete. The forms
       differ by file (`${SUPABASE_URL}/rest/v1/thoughts`, `${REST_BASE}/thoughts`,
       `obFetch("POST", "thoughts", ...)`) so the pattern matches the PATH, not one spelling
       of the base;
    2. take a window of lines around each hit - the statement it belongs to, generously
       sized, because the URL and the request body are rarely on one line;
    3. a window that contains an INSERT indicator (a POST verb, `requests.post`,
       `http_post`, `.post(`) and does NOT mention `exposure` is a VIOLATION.

  An eleventh producer written next year is in the universe the moment it is written, and it
  breaks this gate instead of breaking production.

  WHAT IT DELIBERATELY DOES NOT DO: it does not check the VALUE. Which plane a corpus
  belongs on is a PLAN 1.1 decision for the operator, not a property a pre-commit hook can
  assert. It checks only that the producer STATES one, which is exactly what the NOT NULL
  column asks for.

  Exit 0 = clean, 1 = at least one producer inserts without stating a plane.

.PARAMETER SelfTest
  Write a synthetic violating file into a temp directory, scan it, and require the scan to
  FAIL - then require the same file WITH `exposure` to pass. Proves the gate can go red
  without editing the tree. Exits 0 when both halves behave, 1 otherwise.

.EXAMPLE
  powershell -File scripts/checks/check-corpus-exposure-producers.ps1
  powershell -File scripts/checks/check-corpus-exposure-producers.ps1 -SelfTest
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$SelfTest
)

$ErrorActionPreference = 'Stop'

if (-not $Root) {
    if ($PSScriptRoot) { $scriptDir = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $scriptDir = (Get-Location).Path }
    $Root = Split-Path -Parent (Split-Path -Parent $scriptDir)
    if (-not $Root) { $Root = (Get-Location).Path }
}

# THREE SITE SHAPES, BECAUSE THIS TREE WRITES THE CORPUS THREE WAYS - and each needs its
# own proximity rule. A single loose pattern was tried first and produced 15 false positives
# on the real tree (`{"thoughts": []}`, `.from("thoughts").update(...)`, a citation base URL
# ending in `/thoughts`), and a gate that cries wolf is a gate somebody deletes.
#
#   URL  - a PostgREST collection URL with no filter on it. `/rest/v1/thoughts` or
#          `${REST_BASE}/thoughts`. Anchored on `/rest/v1/` or on the closing `}` of a base
#          variable, so `https://openbrain.local/thoughts` (a citation link) is not a hit.
#          In PostgREST, POST to the bare collection IS the insert; a path carrying
#          `?id=eq.` or `?select=` is a read, a patch or a delete, and the lookahead drops it.
$siteUrl = '(?:/rest/v1/|\}/)(thoughts|agent_memories)(?![A-Za-z0-9_?/])'
#   ARG  - the table as its own string argument next to a POST verb:
#          `obFetch("POST", "thoughts", body)`, `sb.post("thoughts", body)` - including the
#          form where the argument sits on its own line. `(?!\s*:)` drops a JSON KEY of the
#          same name (`{"thoughts": []}`), which is the commonest false positive here.
$siteArg = '(?<![A-Za-z0-9_])["''](thoughts|agent_memories)["''](?!\s*:)'
#   ORM  - the supabase-js builder. Only an `.insert(` in the SAME STATEMENT counts - the
#          slice is taken FORWARD from the `.from(...)` and cut at the first `;`, because
#          `.from("agent_memories").update(...);` followed three lines later by
#          `.from("agent_memory_review_actions").insert({...})` is two statements about two
#          tables, and reading the second as the first's insert is a false positive that
#          would have this gate switched off within a week. Measured on this tree.
$siteOrm = '\.from\(\s*["''](thoughts|agent_memories)["'']'

# What turns a URL site into an INSERT rather than a read: any POST verb in the statement.
# CASE-INSENSITIVE, and `http_?postw*` rather than `http_post`, because the first two versions
# this pattern MISSED a real producer: import-google-activity.mjs calls a local helper named
# `httpPost(` - no dot, no underscore - so the gate walked past a file I had just had to fix
# by hand. A detection pattern that only recognises the spellings you happened to look at is
# the same defect as a hand-list of producers, one layer down.
$insertHint = '(?i)(?:"POST"|''POST''|method\s*:\s*"POST"|requests\.post\s*\(|http_?post\w*\s*\(|\.post\s*\()'
# Tighter, for the ARG shape - the verb has to be right there, not somewhere in the file.
$postVerb   = '(?i)(?:\.post\s*\(|http_?post\w*\s*\(|"POST"|''POST'')'
$ormInsert  = '\.insert\s*\('

# The window a claim is read over. Sized from the widest real case in the tree
# (pull-gmail.ts: the URL and the row literal are 12 lines apart, with the retry handshake
# 20 lines further on). The DETECTION windows are much tighter - see $argWindow / $ormWindow
# - because they decide whether something IS a producer; this one only decides whether the
# producer states its plane, where being generous is the safe direction.
$window    = 30
$argWindow = 2
$ormWindow = 5

$exts = @('*.ts', '*.mjs', '*.js', '*.cjs', '*.py')

# Trees with no first-party source in them. Same list as check-llm-gateway-routing.ps1, for
# the same reason: walking node_modules pushes the pre-commit hook past two minutes.
# `worktrees` is pruned because the main checkout carries `.claude/worktrees/<id>/` - whole
# second copies of this repo. Scanning them doubles the work and reports another session's
# in-progress edits as this tree's violations.
$pruneDirNames = @('.git', '.venv', '.testvenv', 'node_modules', '.next', 'dist', 'build',
                   'backups', 'tiktoken-cache', 'notebook_data', 'data', 'coverage',
                   'worktrees')

# Paths that reference the corpus tables but are not producers of corpus rows.
#   * documentation and archives - prose, and retired code kept for provenance;
#   * this file - it contains every pattern it looks for;
#   * the drills and migrations - they write fixtures through SQL, and where they use REST
#     it is to PROVE the door's behaviour, including the refusal a missing key must cause.
#
# THERE IS NO `*\.claude\*` GLOB HERE, AND THE ABSENCE IS DELIBERATE. It was copied in from
# check-llm-gateway-routing.ps1's list, and it made this entire gate VACUOUS when run from a
# worktree: a session worktree lives at `<repo>\.claude\worktrees\<id>\`, so every path under
# it matched, every file was allowed, and the gate reported "OK - every direct corpus insert
# states its plane" over a scan of nothing. Measured, by planting a violating producer and
# watching it pass. The universe assertion near the exit exists because of that, and would
# have caught it.
$allowPathLike = @(
    '*\documentation\*'
    '*\docs\*'
    '*\scripts\archive\*'
    '*\scripts\checks\check-corpus-exposure-producers.ps1'
    '*\node_modules\*'
)

function Test-Allowed([string]$path) {
    foreach ($glob in $allowPathLike) { if ($path -like $glob) { return $true } }
    return $false
}

function Get-ScanFiles {
    param([string]$RootDir, [string[]]$ExtPatterns, [string[]]$PruneNames)
    $results = New-Object System.Collections.Generic.List[string]
    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($RootDir)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try {
            foreach ($sub in [System.IO.Directory]::EnumerateDirectories($dir)) {
                $name = [System.IO.Path]::GetFileName($sub)
                if ($PruneNames -contains $name) { continue }
                if ($name -like '*-data') { continue }
                if ([System.IO.File]::GetAttributes($sub) -band [System.IO.FileAttributes]::ReparsePoint) { continue }
                $stack.Push($sub)
            }
        } catch { continue }
        try {
            foreach ($file in [System.IO.Directory]::EnumerateFiles($dir)) {
                $leaf = [System.IO.Path]::GetFileName($file)
                foreach ($pat in $ExtPatterns) {
                    if ($leaf -like $pat) { $results.Add($file); break }
                }
            }
        } catch { continue }
    }
    return $results
}

# THE UNIVERSE THIS GATE QUANTIFIES OVER - the number of INSERT SITES it actually examined.
# "no violation" is a verdict only if there was something available to violate it; with zero
# insert sites the green below says nothing about the tree, only about the scan. Set by
# Find-Violations, asserted before the exit.
$script:InsertSites = 0
$script:FilesScanned = 0
function Find-Violations {
    param([string]$ScanRoot)

    $script:InsertSites = 0
    $script:FilesScanned = 0
    $found = New-Object System.Collections.Generic.List[object]
    $files = Get-ScanFiles -RootDir $ScanRoot -ExtPatterns $exts -PruneNames $pruneDirNames |
        Where-Object { -not (Test-Allowed $_) }

    foreach ($f in $files) {
        try { $lines = [System.IO.File]::ReadAllLines($f) } catch { continue }
        $script:FilesScanned++
        $n = $lines.Count
        for ($i = 0; $i -lt $n; $i++) {
            $line = $lines[$i]
            $trimmed = $line.TrimStart()
            # A path inside a comment is documentation of a call, not a call.
            if ($trimmed.StartsWith('#') -or $trimmed.StartsWith('//') -or $trimmed.StartsWith('*')) { continue }
            $lo = [Math]::Max(0, $i - $window)
            $hi = [Math]::Min($n - 1, $i + $window)
            $chunk = ($lines[$lo..$hi] -join "`n")

            # IS THIS AN INSERT SITE? Each shape brings its own proximity rule; the order is
            # most-specific-first so a `.from("thoughts").update()` is judged as an ORM site
            # (and dismissed) rather than falling through to the loose ARG shape.
            $isSite = $false
            if ($line -match $siteUrl) {
                if ($chunk -match $insertHint) { $isSite = $true }
            } elseif ($line -match $siteOrm) {
                # FORWARD from the .from(...) only, and no further than the statement's `;`.
                $ohi  = [Math]::Min($n - 1, $i + $ormWindow)
                $tail = (@($line.Substring($line.IndexOf('.from('))) + @($lines[($i + 1)..$ohi])) -join "`n"
                if ($i -eq $ohi) { $tail = $line.Substring($line.IndexOf('.from(')) }
                $semi = $tail.IndexOf(';')
                if ($semi -ge 0) { $tail = $tail.Substring(0, $semi) }
                if ($tail -match $ormInsert) { $isSite = $true }
            } elseif ($line -match $siteArg) {
                $alo = [Math]::Max(0, $i - $argWindow); $ahi = [Math]::Min($n - 1, $i + $argWindow)
                if (($lines[$alo..$ahi] -join "`n") -match $postVerb) { $isSite = $true }
            }
            if (-not $isSite) { continue }

            $script:InsertSites++
            if ($chunk -match 'exposure') { continue }          # the plane is stated

            $rel = $f
            if ($f.StartsWith($ScanRoot)) { $rel = $f.Substring($ScanRoot.Length).TrimStart('\') }
            $found.Add([pscustomobject]@{ File = $rel; Line = ($i + 1); Text = $line.Trim() })
        }
    }
    return $found
}

# --- THE GATE'S OWN RED -------------------------------------------------------------------
# A guard nobody has watched fail is not known to guard anything, and this one is cheap to
# watch: plant a producer, require red; state the plane, require green. It runs against a
# temp directory, so it neither edits the tree nor depends on the tree's current state.
if ($SelfTest) {
    $tmp = Join-Path $env:TEMP ("corpus-exposure-selftest-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    try {
        $violating = @'
const SUPABASE_URL = process.env.SUPABASE_URL;
async function ingest(content, embedding, metadata) {
  return await fetch(`${SUPABASE_URL}/rest/v1/thoughts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, embedding, metadata }),
  });
}
'@
        Set-Content -Path (Join-Path $tmp "eleventh-producer.mjs") -Value $violating -Encoding ASCII
        # @() OR THE COUNT LIES. A List[object] returned from a function is unrolled by the
        # pipeline, and a SINGLE PSCustomObject has no .Count in PS 5.1 - so `$red.Count -lt 1`
        # was $null -lt 1, which is TRUE, and the self-test reported the gate broken while the
        # gate was working. Found by running it.
        $red = @(Find-Violations -ScanRoot $tmp)
        if ($red.Count -lt 1) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - a producer that POSTs to /rest/v1/thoughts with no exposure key was NOT flagged. The gate does not gate." -ForegroundColor Red
            exit 1
        }
        Write-Host "[check-corpus-exposure-producers] SELF-TEST red: the planted producer is flagged at $($red[0].File):$($red[0].Line)" -ForegroundColor Yellow

        $fixed = $violating.Replace('body: JSON.stringify({ content, embedding, metadata }),',
                                    'body: JSON.stringify({ content, embedding, metadata: { ...metadata, exposure: "ops" }, exposure: "ops" }),')
        Set-Content -Path (Join-Path $tmp "eleventh-producer.mjs") -Value $fixed -Encoding ASCII
        $green = @(Find-Violations -ScanRoot $tmp)
        if ($green.Count -ne 0) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - the SAME producer still flags after stating exposure. The gate cannot be satisfied, so it will be deleted rather than obeyed." -ForegroundColor Red
            exit 1
        }
        Write-Host "[check-corpus-exposure-producers] SELF-TEST green: stating the plane clears it." -ForegroundColor Green
        Write-Host "[check-corpus-exposure-producers] SELF-TEST PASSED - the gate goes red on a violation and green on a fix." -ForegroundColor Green
        exit 0
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

$violations = @(Find-Violations -ScanRoot $Root)
$sites = $script:InsertSites
$scanned = $script:FilesScanned

# THE GREEN IS NOT ALLOWED TO BE VACUOUS. This gate exists because a sweep's search term
# defined its finding; a sweep that matched nothing at all would repeat that in the loudest
# possible way, and it already did once (see $allowPathLike). This tree HAS corpus producers,
# so a run that finds none of them is measuring its own configuration, not the code.
if ($sites -eq 0) {
    Write-Host "[check-corpus-exposure-producers] FAIL - the scan examined $scanned file(s) and found ZERO corpus insert sites." -ForegroundColor Red
    Write-Host "  This tree has corpus producers, so a clean result here is VACUOUS, not green:" -ForegroundColor Red
    Write-Host "  the prune list, the allow-list or the scan root has excluded everything." -ForegroundColor Red
    Write-Host "  Root scanned: $Root" -ForegroundColor DarkGray
    exit 1
}

if ($violations.Count -eq 0) {
    Write-Host "[check-corpus-exposure-producers] OK - all $sites direct corpus insert site(s) state their plane ($scanned file(s) scanned)." -ForegroundColor Green
    exit 0
}

Write-Host "[check-corpus-exposure-producers] FAIL - $($violations.Count) of $sites corpus insert site(s) do not state a plane:" -ForegroundColor Red
Write-Host "  thoughts.exposure / agent_memories.exposure is NOT NULL with no default, and the" -ForegroundColor Red
Write-Host "  deployed RLS policy refuses a row that omits it (42501). State it at the call site:" -ForegroundColor Red
Write-Host "      { content, metadata: { ...metadata, exposure: `"ops`" }, exposure: `"ops`" }" -ForegroundColor DarkGray
Write-Host "  BOTH halves - the column is what the H3 policies read, the metadata mirror is what the" -ForegroundColor DarkGray
Write-Host "  currently deployed U5 policy reads.`n" -ForegroundColor DarkGray
foreach ($v in $violations) {
    Write-Host ("  {0}:{1}" -f $v.File, $v.Line) -ForegroundColor Yellow
    Write-Host ("      {0}" -f $v.Text)
}
Write-Host "`n  If a hit is genuinely not an insert, it is a bare collection path used for a read -" -ForegroundColor DarkGray
Write-Host "  give it its filter, or add its path to `$allowPathLike with the reason." -ForegroundColor DarkGray
exit 1
