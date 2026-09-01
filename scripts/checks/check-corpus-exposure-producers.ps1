#requires -Version 5
<#
.SYNOPSIS
  Authoring-time convenience check: flag a file that INSERTS into the corpus tables
  (`thoughts`, `agent_memories`) over PostgREST without stating the plane (`exposure`) at its
  own call site, in one of the three shapes this gate can recognise.

.DESCRIPTION
  WHAT ENFORCES THE RULE, AND WHAT THIS FILE IS.

  THE DATABASE IS THE ENFORCEMENT. `init-agent-memory-exposure-column.sql` makes
  `thoughts.exposure` and `agent_memories.exposure` NOT NULL with no default and CHECKed to
  ('ops','personal'), and makes `upsert_thought` refuse a payload that omits it. Those refuse
  an unlabelled write UNCONDITIONALLY - in every shape, from every language, through every
  client, forever, including shapes nobody has thought of. Nothing in this file adds to that
  guarantee.

  THIS FILE IS AUTHORING-TIME CONVENIENCE, NOTHING MORE. It moves SOME of those refusals from
  runtime to commit time: the producers written in the shapes it happens to recognise, in the
  file extensions it happens to scan. That is worth having - a refusal at 05:00 inside a daily
  cron is expensive and one at `git commit` is free - but it is a CONVENIENCE, and the honest
  statement of its power is the blind-spot list it prints on every run.

  WHAT AN UNSEEN PRODUCER BREAKS. An earlier version of this header said "an eleventh producer
  written next year is in the universe the moment it is written, and it breaks this gate
  instead of breaking production." THAT SENTENCE WAS FALSE, and it is why this one is long.
  Two verifiers independently planted producers in a temp root; this gate did not flag them,
  did not warn about them, and did not even COUNT them as sites:

      const TABLE = "thoughts";  ...  fetch(`${REST}/${TABLE}`, { method: "POST", ... })
      fetch(REST_BASE + "/" + "thoughts", { method: "POST", ... })
      insertRows("thoughts", rows)   /   obPost("thoughts", rows)
      a byte-identical copy named .mts, and another named .tsx  (OB1 ships 57 .tsx files)
      curl -X POST "$SUPABASE_URL/rest/v1/thoughts" -d "$row"     (in a .sh)
      supabase.table("thoughts").insert(rows)                     (supabase-py)

  A producer this gate cannot see breaks PRODUCTION, not this gate - and per the section 16
  finding in documentation/notes/u8h3-findings.md it breaks it QUIETLY, because the producers
  that fail this way CATCH the 42501 and carry on. `openbrain-gmail-pull` ran for a day
  logging `Ingested: 0 email(s)` and exiting 0. Fail-closed is not fail-visibly.

  So: a green here means "no producer IN THE RECOGNISED SHAPES omits the plane". It does not
  mean "no producer omits the plane", and it never could. The recognised and unrecognised
  shapes are printed by every run so a reader learns this gate's scope FROM THE RUN rather
  than from its author's confidence.

  HOW IT DERIVES ITS SET (rather than carrying a hand-list). `195-` section 7's post-condition
  said "Every caller of this rpc in the tree was found and given an explicit exposure: 'ops'",
  and it was true of the RPC callers, because the sweep behind it was
  `grep -rn 'rpc("upsert_thought"' OB1`. The DIRECT-table producers were never searched for.
  There were twelve, and `openbrain-gmail-pull` had been refused 42501 daily since U5's
  ops-plane policy landed. A hand-list is not the answer to a bad sweep, so within its
  recognised shapes this gate derives its set on every run:

    1. find every BARE corpus-table path or table-name argument - see THE THREE SHAPES below;
    2. decide it is an INSERT (not a read, a patch or a delete) from the verbs near it;
    3. read the STATEMENT it belongs to for an `exposure` key. Absent = violation.

  EVIDENCE IS SCOPED TO THE STATEMENT, NOT TO A LINE DISTANCE. It was not, and that produced
  the second refutation: `OB1/integrations/agent-memory-api/index.ts` holds the only
  `agent_memories` INSERT in the tree, it carried neither `exposure` nor `metadata.exposure`,
  and it PASSED - cleared by a DIFFERENT table's statement 20 lines above (an `upsert_thought`
  RPC payload) whose own key said `exposure: "ops"`. Renaming that unrelated key turned this
  gate red on a line it had never examined. A green off a neighbour's key is worth less than
  no check at all, because it reads as coverage. Now: an ORM site is read over ITS OWN
  STATEMENT, and no site's evidence window may cross another corpus site in either direction.

  WHAT IT DELIBERATELY DOES NOT DO: it does not check the VALUE. Which plane a corpus belongs
  on is a PLAN 1.1 decision for the operator, not a property a pre-commit hook can assert. It
  checks only that the producer STATES one, which is what the NOT NULL column asks for.

  Exit 0 = no violation in the recognised shapes, 1 = at least one, or the scan was vacuous.

.PARAMETER SelfTest
  Write synthetic producers into a temp directory and require the scan to behave: a violating
  one is FLAGGED, the same one WITH `exposure` is not, and - the case that matters - a
  producer whose only nearby `exposure` key belongs to a DIFFERENT table's statement is still
  FLAGGED. Proves the gate can go red without editing the tree. Exit 0 when all behave.

.PARAMETER ShowShapes
  Print the shape/extension disclosure and exit 0 without scanning.

.EXAMPLE
  powershell -File scripts/checks/check-corpus-exposure-producers.ps1
  powershell -File scripts/checks/check-corpus-exposure-producers.ps1 -SelfTest
  powershell -File scripts/checks/check-corpus-exposure-producers.ps1 -ShowShapes
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$SelfTest,
    [switch]$ShowShapes
)

$ErrorActionPreference = 'Stop'

if (-not $Root) {
    if ($PSScriptRoot) { $scriptDir = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $scriptDir = (Get-Location).Path }
    $Root = Split-Path -Parent (Split-Path -Parent $scriptDir)
    if (-not $Root) { $Root = (Get-Location).Path }
}

# --- THE THREE SHAPES ---------------------------------------------------------------------
# This tree writes the corpus three ways, and each needs its own proximity rule. A single
# loose pattern was tried first and produced 15 false positives on the real tree
# (`{"thoughts": []}`, `.from("thoughts").update(...)`, a citation base URL ending in
# `/thoughts`), and a gate that cries wolf is a gate somebody deletes.
#
#   URL  - a PostgREST collection URL with no filter on it. `/rest/v1/thoughts` or
#          `${REST_BASE}/thoughts`. Anchored on `/rest/v1/` or on the closing `}` of a base
#          variable, so `https://openbrain.local/thoughts` (a citation link) is not a hit.
#          In PostgREST, POST to the bare collection IS the insert; a path carrying
#          `?id=eq.` or `?select=` is a read, a patch or a delete, and the lookahead drops it.
$siteUrl = '(?:/rest/v1/|\}/)(thoughts|agent_memories)(?![A-Za-z0-9_?/])'
#   ARG  - the table as its own quoted argument next to an insert verb:
#          `obFetch("POST", "thoughts", body)`, `sb.post("thoughts", body)`,
#          `insertRows("thoughts", rows)` - including the form where the argument sits on its
#          own line. `(?!\s*:)` drops a JSON KEY of the same name (`{"thoughts": []}`), which
#          is the commonest false positive here.
$siteArg = '(?<![A-Za-z0-9_])["''](thoughts|agent_memories)["''](?!\s*:)'
#   ORM  - a client builder naming the table literally: supabase-js `.from("thoughts")` and
#          supabase-py `.table("thoughts")`. Only an `.insert(` in the SAME STATEMENT counts -
#          the detection slice is taken FORWARD from the builder and cut at the first `;`,
#          because `.from("agent_memories").update(...);` followed three lines later by
#          `.from("agent_memory_review_actions").insert({...})` is two statements about two
#          tables, and reading the second as the first's insert is a false positive that would
#          have this gate switched off within a week. Measured on this tree.
$siteOrm = '\.(?:from|table)\(\s*["''](thoughts|agent_memories)["'']'

# What turns a URL site into an INSERT rather than a read: any POST verb near it.
# CASE-INSENSITIVE, and deliberately loose about the CALLER's spelling, because the first two
# versions of this pattern MISSED a real producer: import-google-activity.mjs calls a local
# helper named `httpPost(` - no dot, no underscore - so the gate walked past a file that had
# just had to be fixed by hand. A detection pattern that only recognises the spellings you
# happened to look at is the same defect as a hand-list of producers, one layer down. So: any
# identifier CONTAINING `post` or `insert` and called as a function, plus curl's flags.
$insertHint = '(?i)(?:"POST"|''POST''|method\s*:\s*"POST"|requests\.post\s*\(|-X\s*POST|--request[= ]POST|[\w.]*post\w*\s*\(|[\w.]*insert\w*\s*\()'
# Tighter, for the ARG shape - the verb has to be right there, not somewhere in the file.
$postVerb   = '(?i)(?:[\w.]*post\w*\s*\(|[\w.]*insert\w*\s*\(|"POST"|''POST''|-X\s*POST|--request[= ]POST)'
$ormInsert  = '\.insert\s*\('

# The window a claim is read over, in lines. Sized from the widest real case in the tree
# (pull-gmail.ts: the URL and the row literal are 12 lines apart, with the retry handshake
# 20 lines further on). The DETECTION windows are much tighter - see $argWindow / $ormWindow -
# because they decide whether something IS a producer; this one only decides whether the
# producer states its plane, where being generous is the safe direction.
#
# BUT NOT UNBOUNDEDLY GENEROUS, AND THIS IS THE FIX FOR THE FALSE GREEN ON index.ts:491.
# However wide the window, it is CLIPPED at the nearest other corpus site in each direction: a
# statement's evidence may not be read across another corpus statement. An ORM site is read
# over its own statement outright - from the builder to the terminating `;`, however far that
# is - which is the tightest scope available and the one the refutation asked for.
$window       = 30
$argWindow    = 2
$ormWindow    = 5
$stmtMaxLines = 200   # a statement longer than this is not a statement, it is a file

# --- THE ALPHABET, AND WHAT IS OUTSIDE IT --------------------------------------------------
# `.mts`, `.cts`, `.tsx`, `.jsx`, `.sh` and `.bash` were added after two verifiers planted
# byte-identical copies of a flagged producer under extensions this list did not carry and
# watched them pass. That was the SAME alphabet error as the `.ts`-only scan root in A2, one
# layer down. Widening it is cheap and worth doing; it is NOT the fix, because the NEXT
# extension is also not on the list. The fix is that the database refuses the row.
$exts = @('*.ts', '*.mts', '*.cts', '*.tsx', '*.mjs', '*.js', '*.cjs', '*.jsx',
          '*.py', '*.sh', '*.bash')

# WHAT THIS GATE RECOGNISES, AND WHAT IT DOES NOT - printed on every run, pass or fail, so a
# reader learns its scope from the output rather than from this comment.
$SHAPES_SEEN = @(
    'URL   a bare PostgREST collection path written as a literal: /rest/v1/thoughts, ${BASE}/agent_memories'
    'ARG   the table name as its own quoted argument beside a post/insert call: obFetch("POST","thoughts",..), insertRows("thoughts",..)'
    'ORM   a client builder naming the table literally: supabase-js .from("thoughts").insert(..), supabase-py .table("thoughts").insert(..)'
    'VERB  POST spelled as "POST", method:"POST", requests.post(, curl -X POST, or ANY identifier containing post/insert called as a function'
)
# NOT-FOLLOWED, NOT "NEVER FLAGGED", AND THE DIFFERENCE IS MEASURED. This gate resolves no
# value: it never learns that `TABLE` holds "thoughts". When it does flag one of these it is
# an ACCIDENT of layout - an unrelated literal landing inside the 2-line ARG window - and the
# accident is not a property anyone should rely on. Proof, run 2026-08-31: the producer
# `const TABLE = "thoughts"` + `fetch(\`${REST}/${TABLE}\`, {method:"POST"})` is FLAGGED when
# the two lines are adjacent and, byte-for-byte the same producer, reported
# `OK - all 1 RECOGNISED corpus insert site(s) state their plane` when 40 filler lines are
# inserted between them. Same defect, same file, opposite verdict, decided by whitespace.
$SHAPES_BLIND = @(
    'the table name held in a VARIABLE or constant: const T = "thoughts"; fetch(`${BASE}/${T}`, {method:"POST"}) - flagged only if the literal happens to sit within 2 lines of a verb'
    'a path ASSEMBLED by concatenation or by a helper: BASE + "/" + name, urljoin(BASE, name), buildUrl(name)'
    'a WRAPPER whose table comes from ITS caller: writeCorpus(table, rows) - the literal is at one site, the POST at another'
    'the table name held in config, JSON, YAML or an environment variable'
    'any file extension not in the list below - .go .rs .rb .java .php .ipynb .yml .sql, and every extension not yet invented'
    'anything under a pruned directory (see below), including a vendored copy of a producer'
    'a write that does not go over PostgREST at all - psql, a direct pg driver, a SQL migration'
    'a producer CORRECT here and WRONG at runtime: this reads source text, never the value actually sent'
    'two inserts into the SAME table close together: the fence separates TABLES, not statements, so outside the ORM shape one of them stating the plane can clear the other'
)

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
#   * this file - it contains every pattern it looks for.
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

function Write-Disclosure {
    Write-Host "  ---- WHAT THIS GATE IS, AND WHAT IT CANNOT SEE ----" -ForegroundColor DarkGray
    Write-Host "  THE DATABASE IS THE ENFORCEMENT: thoughts.exposure / agent_memories.exposure are NOT NULL +" -ForegroundColor DarkGray
    Write-Host "  CHECK, and upsert_thought refuses a payload without them. That refuses an unlabelled write in" -ForegroundColor DarkGray
    Write-Host "  EVERY shape and language, always. This gate only moves SOME of those refusals to commit time." -ForegroundColor DarkGray
    Write-Host "  A producer it cannot see breaks PRODUCTION, not this gate - and QUIETLY, because the producers" -ForegroundColor DarkGray
    Write-Host "  that fail this way catch the 42501 and log a zero-row success." -ForegroundColor DarkGray
    Write-Host "  SHAPES IT RECOGNISES:" -ForegroundColor DarkGray
    foreach ($s in $SHAPES_SEEN)  { Write-Host "    + $s" -ForegroundColor DarkGray }
    Write-Host "  SHAPES IT DOES NOT FOLLOW - it resolves no values, so a producer written any of these ways is" -ForegroundColor DarkGray
    Write-Host "  seen only by accident of layout. Measured: the SAME variable-table producer is flagged when the" -ForegroundColor DarkGray
    Write-Host "  literal is adjacent and reported OK when 40 lines separate it. Do not read a green as coverage here:" -ForegroundColor DarkGray
    foreach ($s in $SHAPES_BLIND) { Write-Host "    - $s" -ForegroundColor DarkGray }
    Write-Host ("  EXTENSIONS SCANNED: " + (($exts | ForEach-Object { $_.TrimStart('*') }) -join ' ')) -ForegroundColor DarkGray
    Write-Host ("  DIRECTORIES PRUNED: " + ($pruneDirNames -join ' ')) -ForegroundColor DarkGray
}

if ($ShowShapes) { Write-Disclosure; exit 0 }

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
        try { $text = [System.IO.File]::ReadAllText($f) } catch { continue }
        $script:FilesScanned++
        # EARLY-OUT, AND IT IS A PERFORMANCE FIX ONLY - the table names are lowercase in every
        # pattern above, so an ordinal search for them admits exactly the files those patterns
        # could match, and no others. Without it the fence pre-pass runs three regexes over
        # every line of 742 files and the pre-commit hook takes 75 seconds, which is how a
        # check gets deleted. If a shape is ever added that does NOT spell the table name in
        # the file, this line has to go with it.
        if ($text.IndexOf('thoughts') -lt 0 -and $text.IndexOf('agent_memories') -lt 0) { continue }
        $lines = $text -split "?
"
        $n = $lines.Count

        # EVERY corpus-site line in the file, in ANY shape, with the TABLE it names.
        # These are the FENCES: a site's evidence may not be read across a corpus site that
        # names a DIFFERENT table. Without this, an `exposure` key belonging to one table's
        # statement clears another table's statement below it, and the gate is green on a
        # site it never examined. (index.ts:491, refuted.)
        #
        # TABLE-AWARE, AND THE FIRST VERSION WAS NOT - it fenced at EVERY corpus site, which
        # turned pull-gmail.ts:856 red. That site is the RETRY leg: it re-POSTs the very same
        # `row` object built above the first POST, `exposure` and all, and a blind fence cut
        # the row literal out of its own retry's evidence. Two POSTs of one row into ONE
        # table are one producer; a `thoughts` key clearing an `agent_memories` insert is the
        # defect. The fence now separates exactly those.
        $fences = New-Object System.Collections.Generic.List[int]
        $fenceTables = @{}
        for ($k = 0; $k -lt $n; $k++) {
            $lk = $lines[$k]
            $t = @()
            foreach ($rx in @($siteUrl, $siteOrm, $siteArg)) {
                foreach ($m in [regex]::Matches($lk, $rx)) { $t += $m.Groups[1].Value }
            }
            if ($t.Count -gt 0) { $fences.Add($k); $fenceTables[$k] = @($t | Select-Object -Unique) }
        }

        for ($i = 0; $i -lt $n; $i++) {
            $line = $lines[$i]
            $trimmed = $line.TrimStart()
            # A path inside a comment is documentation of a call, not a call.
            if ($trimmed.StartsWith('#') -or $trimmed.StartsWith('//') -or $trimmed.StartsWith('*')) { continue }

            # The DETECTION chunk is UNFENCED: whether something IS a POST is a fact about the
            # code around it, and a neighbouring corpus statement does not change it. Only the
            # EVIDENCE for `exposure` is fenced.
            $dlo = [Math]::Max(0, $i - $window)
            $dhi = [Math]::Min($n - 1, $i + $window)
            $dchunk = ($lines[$dlo..$dhi] -join "`n")

            # The EVIDENCE window, clipped at the neighbouring OTHER-TABLE fences.
            $lo = $dlo; $hi = $dhi
            $myTables = if ($fenceTables.ContainsKey($i)) { $fenceTables[$i] } else { @() }
            foreach ($fl in $fences) {
                $shared = $false
                foreach ($ft in $fenceTables[$fl]) { if ($myTables -contains $ft) { $shared = $true; break } }
                if ($shared) { continue }
                if ($fl -lt $i -and ($fl + 1) -gt $lo) { $lo = $fl + 1 }
                if ($fl -gt $i -and ($fl - 1) -lt $hi) { $hi = $fl - 1 }
            }
            if ($hi -lt $i) { $hi = $i }
            if ($lo -gt $i) { $lo = $i }
            $evidence = ($lines[$lo..$hi] -join "`n")

            # IS THIS AN INSERT SITE? Each shape brings its own proximity rule; the order is
            # most-specific-first so a `.from("thoughts").update()` is judged as an ORM site
            # (and dismissed) rather than falling through to the loose ARG shape.
            $isSite = $false
            if ($line -match $siteUrl) {
                if ($dchunk -match $insertHint) { $isSite = $true }
            } elseif ($line -match $siteOrm) {
                $fromAt = $line.IndexOf('.from(')
                if ($fromAt -lt 0) { $fromAt = $line.IndexOf('.table(') }
                if ($fromAt -lt 0) { $fromAt = 0 }
                # DETECTION: forward from the builder, no further than $ormWindow lines and no
                # further than the statement's `;`.
                $ohi  = [Math]::Min($n - 1, $i + $ormWindow)
                if ($i -eq $ohi) { $tail = $line.Substring($fromAt) }
                else { $tail = (@($line.Substring($fromAt)) + @($lines[($i + 1)..$ohi])) -join "`n" }
                $semi = $tail.IndexOf(';')
                if ($semi -ge 0) { $tail = $tail.Substring(0, $semi) }
                if ($tail -match $ormInsert) {
                    $isSite = $true
                    # EVIDENCE: the WHOLE statement, builder to terminating `;`, however long.
                    # Not a line count - a 33-line insert body is one statement, and a window
                    # of 30 would have cut off the very key it is looking for.
                    $ehi = [Math]::Min($n - 1, $i + $stmtMaxLines)
                    if ($i -eq $ehi) { $stmt = $line.Substring($fromAt) }
                    else { $stmt = (@($line.Substring($fromAt)) + @($lines[($i + 1)..$ehi])) -join "`n" }
                    $esemi = $stmt.IndexOf(';')
                    if ($esemi -ge 0) { $stmt = $stmt.Substring(0, $esemi) }
                    $evidence = $stmt
                }
            } elseif ($line -match $siteArg) {
                $alo = [Math]::Max(0, $i - $argWindow); $ahi = [Math]::Min($n - 1, $i + $argWindow)
                if (($lines[$alo..$ahi] -join "`n") -match $postVerb) { $isSite = $true }
            }
            if (-not $isSite) { continue }

            $script:InsertSites++
            if ($evidence -match 'exposure') { continue }       # the plane is stated

            $rel = $f
            if ($f.StartsWith($ScanRoot)) { $rel = $f.Substring($ScanRoot.Length).TrimStart('\') }
            $found.Add([pscustomobject]@{ File = $rel; Line = ($i + 1); Text = $line.Trim() })
        }
    }
    return $found
}

# --- THE GATE'S OWN RED -------------------------------------------------------------------
# A guard nobody has watched fail is not known to guard anything, and this one is cheap to
# watch. Three cases, and the third is the one a verifier had to find by hand.
if ($SelfTest) {
    $tmp = Join-Path $env:TEMP ("corpus-exposure-selftest-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $tmp -Force | Out-Null
    $selfFail = 0
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
            $selfFail++
        } else {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST red: the planted producer is flagged at $($red[0].File):$($red[0].Line)" -ForegroundColor Yellow
        }

        $fixed = $violating.Replace('body: JSON.stringify({ content, embedding, metadata }),',
                                    'body: JSON.stringify({ content, embedding, metadata: { ...metadata, exposure: "ops" }, exposure: "ops" }),')
        Set-Content -Path (Join-Path $tmp "eleventh-producer.mjs") -Value $fixed -Encoding ASCII
        $green = @(Find-Violations -ScanRoot $tmp)
        if ($green.Count -ne 0) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - the SAME producer still flags after stating exposure. The gate cannot be satisfied, so it will be deleted rather than obeyed." -ForegroundColor Red
            $selfFail++
        } else {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST green: stating the plane clears it." -ForegroundColor Green
        }

        # CASE 3 - THE NEIGHBOUR'S KEY. The shape that made this gate's whole agent_memories
        # coverage a false positive: an `exposure` key belonging to a DIFFERENT table's
        # statement, close enough to fall inside a line-distance window. Cases 1 and 2 both
        # passed while this was broken, which is exactly why case 3 exists.
        Remove-Item (Join-Path $tmp "eleventh-producer.mjs") -Force
        $neighbour = @'
async function writeBoth(sb, row) {
  const { data: t } = await sb.rpc("upsert_thought", {
    p_content: row.content,
    p_payload: { metadata: { exposure: "ops", source: "selftest" } },
  });
  const { data: m } = await sb.from("agent_memories").insert({
    thought_id: t?.id ?? null,
    summary: row.summary,
    content: row.content,
    metadata: { source_refs: [] },
  }).select("*").single();
  return m;
}
'@
        Set-Content -Path (Join-Path $tmp "neighbour-key.mjs") -Value $neighbour -Encoding ASCII
        $nb = @(Find-Violations -ScanRoot $tmp)
        if ($nb.Count -lt 1) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - an agent_memories insert with NO exposure of its own PASSED, cleared by an exposure key that belongs to the upsert_thought statement above it. This is the false green that was refuted on index.ts:491." -ForegroundColor Red
            $selfFail++
        } else {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST red: a neighbouring statement's exposure key does NOT clear this insert ($($nb[0].File):$($nb[0].Line))" -ForegroundColor Yellow
        }
        $nbFixed = $neighbour.Replace('    metadata: { source_refs: [] },',
                                      '    exposure: "ops",' + "`r`n" + '    metadata: { source_refs: [], exposure: "ops" },')
        Set-Content -Path (Join-Path $tmp "neighbour-key.mjs") -Value $nbFixed -Encoding ASCII
        $nbg = @(Find-Violations -ScanRoot $tmp)
        if ($nbg.Count -ne 0) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - the same insert still flags after stating its OWN exposure ($($nbg[0].File):$($nbg[0].Line))." -ForegroundColor Red
            $selfFail++
        } else {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST green: stating exposure IN THE STATEMENT clears it." -ForegroundColor Green
        }

        if ($selfFail -gt 0) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - $selfFail case(s) took the wrong branch." -ForegroundColor Red
            exit 1
        }
        Write-Host "[check-corpus-exposure-producers] SELF-TEST PASSED - red on a violation, red on a neighbour's key, green on a real fix." -ForegroundColor Green
        Write-Host "  It proves the three RECOGNISED shapes behave. It proves NOTHING about the blind spots below." -ForegroundColor DarkGray
        Write-Disclosure
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
    Write-Host "[check-corpus-exposure-producers] OK - all $sites RECOGNISED corpus insert site(s) state their plane ($scanned file(s) scanned)." -ForegroundColor Green
    Write-Host "  This is NOT 'no producer omits the plane'. It is 'no producer IN THE SHAPES BELOW omits it'." -ForegroundColor DarkGray
    Write-Disclosure
    exit 0
}

Write-Host "[check-corpus-exposure-producers] FAIL - $($violations.Count) of $sites recognised corpus insert site(s) do not state a plane:" -ForegroundColor Red
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
Write-Disclosure
exit 1
