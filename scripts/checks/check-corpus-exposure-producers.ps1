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
  Two verifiers independently planted producers in a temp root that this gate did not flag,
  did not warn about, and did not even COUNT as sites:

      const TABLE = "thoughts";  ...  fetch(`${REST}/${TABLE}`, { method: "POST", ... })
      fetch(REST_BASE + "/" + "thoughts", { method: "POST", ... })
      insertRows("thoughts", rows)   /   obPost("thoughts", rows)
      a byte-identical copy named .mts, and another named .tsx  (OB1 ships 57 .tsx files)
      curl -X POST "$SUPABASE_URL/rest/v1/thoughts" -d "$row"     (in a .sh)
      supabase.table("thoughts").insert(rows)                     (supabase-py)

  THAT LIST IS HISTORY, AND IT WAS ALREADY HISTORY WHEN IT SHIPPED - SO IT IS DATED NOW.
  Those verdicts were TRUE AT 819b5fe. They were written to MOTIVATE the widening of the
  alphabet and of the ARG/ORM/VERB patterns - and that widening landed in the SAME COMMIT,
  c192041. The paragraph therefore described a gate that had ceased to exist by the time it
  was committed, and then stood for two rounds as a live claim about what this gate cannot
  see. Nobody re-read the motivation after acting on it. Re-measured 2026-08-31 at 5c81f97,
  one UNLABELLED fixture per shape, the historical blob run against the same fixtures:

      shape, as listed above                          819b5fe        5c81f97
      insertRows("thoughts", rows)                    0 sites        FLAGGED 1 of 1
      obPost("thoughts", rows)                        0 sites        FLAGGED 1 of 1
      the byte-identical .mts copy                    0 files        FLAGGED 1 of 1
      the byte-identical .tsx copy                    0 files        FLAGGED 1 of 1
      curl -X POST ".../thoughts" in a .sh            0 files        FLAGGED 1 of 1
      supabase.table("thoughts").insert(rows)         0 sites        FLAGGED 1 of 1
      fetch(BASE + "/" + "thoughts", ..)              FLAGGED        FLAGGED  (see below)
      const TABLE = "thoughts" .. `${BASE}/${TABLE}`  0 sites        0 sites  (see below)

  SIX OF THE EIGHT ARE NOW FLAGGED AND COUNTED, ONE ALWAYS WAS, AND ONE IS STILL MISSED. The
  "one always was" is worth saying out loud: the CONCATENATED path was FLAGGED at 819b5fe too,
  so the original list was over-broad even for the sha it was written at - "did not flag them"
  was true of five of the six bullets, not six. Fixtures are reconstructions from the bullets
  above, not the verifiers' original files, which were not kept; each is the smallest producer
  that spells the bullet, and both blobs were run against the SAME file.

  THE LAST TWO ROWS ARE ONE CASE, NOT TWO, and neither is coverage: both are decided by
  LAYOUT rather than by shape. This gate resolves no values, so it sees either of them only
  when the quoted literal "thoughts" happens to fall within $argWindow (2) lines of a
  post/insert verb. Measured both ways at 5c81f97: the CONCATENATED path is FLAGGED with the
  verb on the next line and reports ZERO SITES with five lines between them; the VARIABLE-held
  name is FLAGGED adjacent and ZERO SITES at three lines. Same producer, same file, opposite
  verdict, decided by whitespace. $SHAPES_BLIND says exactly this, and it is why widening the
  alphabet was worth doing and was still NOT the fix.

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

  AND THAT FIXED THE ORM VICTIM ONLY - SAY SO. The second half of that sentence (the fence)
  does far less than it reads: it clips the window at another table's SITE LINE, and a
  donor's `exposure` key is in its BODY, one to three lines BELOW that line, INSIDE the clip.
  Measured 2026-08-31: a `thoughts` POST stating its plane above an `agent_memories` POST
  stating none still CLEARS it, at separations of 0, 3, 10 and 25 lines - 8 sites, 0
  violations, green. index.ts:491 was an ORM site, and the ORM shape is immune because its
  evidence IS the statement. `-SelfTest` case 3's victim is an ORM site too, so it cannot
  catch this; case 4 plants the URL case and RECORDS the miss instead of pretending.

  TWO MORE FALSE GREENS ON COUNTED SITES, FOUND THE SAME WAY AND FIXED. The evidence test was
  a BARE SUBSTRING match on the word `exposure`, so a COMMENT (`// exposure is applied
  downstream`) and an unrelated identifier (`const exposureMetricsCounter = 0;`) each cleared
  a bare POST that stated no plane at all. Not misses: the run COUNTED those sites and
  reported them as stating their plane. It now requires a key or an assignment
  (Test-StatesPlane), and whole-line comments are not evidence. `-SelfTest` cases 3b/3c.

  AND THE THIRD FALSE GREEN OF THAT CLASS WAS A CASE FOLD. PowerShell's `-match` is
  case-INSENSITIVE, so `Exposure: "ops"` cleared a bare POST. PostgREST REFUSES that key - the
  column is `exposure` - so the gate was green on a producer the DATABASE rejects, which is the
  worst direction a false green can point: it is not a miss and not even a wrong-but-harmless
  pass, it is a green over a row that will never be written. Measured 2026-08-31 at 5c81f97:
  `OK - all 1 RECOGNISED corpus insert site(s) state their plane`, exit 0. Test-StatesPlane
  now uses `-cmatch`; the same fixture FAILs 1 of 1. Every `exposure` key in this tree is
  lowercase (checked); only the TypeScript TYPE name `Exposure` is capitalised, and a type
  name is not a key.

  STOP ENUMERATING THESE. THE BLIND SPOT IS A CATEGORY, AND THE CATEGORY IS THE DECLARATION.
  Four rounds have now closed false greens on COUNTED sites one shape at a time - a comment, a
  look-alike identifier, a semicolon in a comment, a case fold - and a verifier has produced a
  fresh set each round. That is the ENUMERATE-AND-PATCH method A2 abandoned, and the seventh
  shape will win exactly as the sixth did. So the honest declaration is not a longer list, it
  is the property that GENERATES the list, and it is this:

      THE EVIDENCE TEST IS A TEXT MATCH WITHIN A WINDOW AROUND THE SITE. ANY OCCURRENCE OF THE
      KEY THAT IS NOT THIS STATEMENT'S OWN PLANE DECLARATION CAN CLEAR IT. Type annotations,
      sibling objects, string literals, SQL text and comment continuations are KNOWN INSTANCES.
      THE LIST IS ILLUSTRATIVE, NOT EXHAUSTIVE. A green from this gate is never evidence that a
      row carries a plane; only the NOT NULL + CHECK in the database is that.

  That statement stays true as new shapes appear, which is worth more than a list that is
  complete for one afternoon. It is printed on every run (see $EVIDENCE_CLEARS) with the
  measured instances underneath it, so a reader gets the CATEGORY first and the examples
  second. Two narrowings ARE in place and bound it: the token must be a key or an assignment
  (not a bare substring), and a WHOLE-LINE comment is not evidence. Neither narrows it to a
  declaration - only to something that LOOKS like one.

  AND THAT TIGHTENING FOUND A LIVE ONE IN THIS TREE. OB1/recipes/schema-aware-routing's
  index.ts:298 states its plane correctly at line 308 - but the ORM statement slice stopped
  at the first `;`, and the eight-line comment between them contains "...widens nothing;
  where...". The key was never inside the evidence, and the site passed off the word
  `exposure` IN THAT COMMENT. Delete both real keys, leave the comment, and the old gate is
  still green: measured. Get-Statement no longer takes a terminator from a comment line.

  AND THE LINE SPLITTER REQUIRED A CARRIAGE RETURN - ON THIS PLATFORM, NOT THE OTHER ONE.
  A bare CR sat in this file's split pattern. The repo blob is "<CR>?<LF>", i.e. optional-CR
  then LF, which is CORRECT. But core.autocrlf=true rewrites that trailing LF on checkout, so
  the WINDOWS working copy reads "<CR>?<CR><LF>", which REQUIRES a CR. The broken
  configuration is therefore a WINDOWS checkout of this script scanning an LF-only FILE:
  nothing split, one line per file, no fences, and the evidence window was the WHOLE FILE.
  All four combinations measured 2026-08-31 - the Windows-checkout gate against an LF fixture
  is the ONLY false green; the Linux/LF checkout is correct on both fixtures. An earlier
  draft of this comment had the direction BACKWARDS. The runs recorded in rounds 3-5 were
  unaffected, and NOT because this worktree is CRLF - that is the AFFECTED side. They were
  unaffected because of the 44 LF-majority files in the scan set, ZERO name a corpus table
  (measured). The vacuity guard would not have caught it either way - one site is not zero
  sites.

  WHAT IT DELIBERATELY DOES NOT DO: it does not check the VALUE. Which plane a corpus belongs
  on is a PLAN 1.1 decision for the operator, not a property a pre-commit hook can assert. It
  checks only that the producer STATES one, which is what the NOT NULL column asks for.

  Exit 0 = no violation in the recognised shapes, 1 = at least one, or the scan was vacuous.

.PARAMETER SelfTest
  Write synthetic producers into a temp directory and require the scan to behave. SIX
  assertions that must hold (a failure exits 1): a violating producer is FLAGGED; the same
  one WITH `exposure` is not; a producer whose only nearby `exposure` key belongs to a
  DIFFERENT table's STATEMENT is still FLAGGED (case 3, the ORM refutation); and a bare POST
  is still FLAGGED when the only `exposure` in range is a COMMENT (3b) or an unrelated
  IDENTIFIER (3c). Proves the gate can go red without editing the tree.

  THEN THREE CASES THAT ASSERT NOTHING AND MEASURE THE BLIND SPOTS (4, 5, 6): a URL victim
  with a labelled donor above it, a backtick-quoted table argument, and a producer under an
  allow-listed path. Each plants an UNLABELLED producer and RECORDS what the gate does. They
  cannot fail the self-test - the miss is the documented behaviour - but if one is ever
  FLAGGED the run says so and asks for the blind-spot entry to be struck. A blind spot
  measured every run is a scope; one asserted in a comment is a claim nobody re-checks.
  Exit 0 when the six assertions behave, whatever the three records say.

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

# --- WHAT COUNTS AS STATING THE PLANE ------------------------------------------------------
# This was `$evidence -match 'exposure'` - a BARE SUBSTRING - and it produced FALSE GREENS
# ON COUNTED SITES, which is worse than a miss: the run reports the site as examined AND as
# stating its plane. Two were planted and measured 2026-08-31, both reported
# "OK - all 1 RECOGNISED corpus insert site(s) state their plane":
#
#     // exposure is applied downstream by the ingest worker     <- a COMMENT clears it
#     const exposureMetricsCounter = 0;                          <- an unrelated IDENTIFIER
#
# Same class as index.ts:491 one layer down: a green off text that is not this statement's
# plane declaration. So the token must be the whole word `exposure`, optionally quoted or
# backslash-escaped, followed by a key separator or an assignment - which is what a
# declaration looks like in every language this gate scans:
#     exposure: "ops"   "exposure": "ops"   exposure="ops"   \"exposure\":\"ops\"
# The lookbehind is what rejects `exposureMetricsCounter` and `enforced_exposure`; requiring
# `[:=]` after it is what rejects the comment.
$statesPlane = '(?<![A-Za-z0-9_])[\x22\x27\x60\x5C]?exposure[\x22\x27\x60\x5C]*\s*[:=]'
# WHOLE-LINE COMMENTS ARE NOT EVIDENCE. `// exposure: "ops"` above a bare POST declares
# nothing to the database. Only lines whose TRIMMED start is a comment marker are dropped -
# never a trailing `//`, because a URL literal contains one and cutting there would discard
# a real key on the same line. COST, STATED: a plane stated in a TRAILING comment is
# therefore still counted as evidence, and a declaration sitting inside a multi-line string
# literal on a line that begins with # or // is dropped (a false RED - the safe direction).
function Test-StatesPlane([string]$evidence) {
    $code = ($evidence -split "\r\n|\n|\r" | Where-Object {
        $t = $_.TrimStart()
        -not ($t.StartsWith('#') -or $t.StartsWith('//') -or $t.StartsWith('*') -or $t.StartsWith('/*'))
    }) -join "`n"
    # CASE-SENSITIVE (-cmatch, not -match). PowerShell's -match is case-INSENSITIVE, so
    # `Exposure: "ops"` cleared a bare POST - and PostgREST REFUSES that key, because the
    # column is `exposure`. That was a green on a producer the DATABASE rejects, which is
    # the worst direction a false green can point. Measured 2026-08-31 at 5c81f97: the
    # fixture below reported `OK - all 1 RECOGNISED corpus insert site(s) state their
    # plane`, exit 0, under -match, and FAILs 1 of 1 under -cmatch.
    #     body: JSON.stringify({ content: row.content, Exposure: "ops" }),
    # Every `exposure` key in this tree is lowercase (checked); only the TypeScript TYPE
    # name `Exposure` is capitalised, and a type name is not a key.
    return ($code -cmatch $statesPlane)
}

# --- THE STATEMENT SLICE, AND WHERE IT MUST NOT STOP ---------------------------------------
# An ORM site is read over its own statement: from the builder to the terminating `;`. The
# terminator was found with a bare IndexOf(';'), and a `;` INSIDE A COMMENT ended the slice
# early. Found the moment the evidence test above stopped accepting substrings, and it was a
# LIVE FALSE GREEN in this tree, not a hypothetical:
#
#   OB1/recipes/schema-aware-routing/index.ts:298 states its plane at line 308. The eight-line
#   comment between them contains "...widens nothing; where this corpus belongs...", so the
#   slice ended at THAT semicolon and the real `exposure: "ops"` key was never inside the
#   evidence. The site passed anyway - cleared by the word `exposure` in that same comment.
#   A real producer, correctly labelled, reported green FOR THE WRONG REASON. Delete the key
#   and leave the comment and the gate would not have noticed.
#
# So: a line whose TRIMMED start is a comment marker carries no terminator. It is still
# APPENDED - Test-StatesPlane drops it as evidence - because dropping it here would silently
# renumber nothing but would make this function two rules instead of one.
# NOT FIXED, AND DECLARED: a `;` inside a STRING LITERAL still ends the slice early, and a
# trailing `// ...;` on a code line does too. Both truncate toward RED, which is the safe
# direction now that the evidence test is a key test rather than a substring.
function Get-Statement {
    param([string[]]$Lines, [int]$Start, [string]$FirstLine, [int]$MaxLines)
    $out = New-Object System.Collections.Generic.List[string]
    $end = [Math]::Min($Lines.Count - 1, $Start + $MaxLines)
    for ($k = $Start; $k -le $end; $k++) {
        $raw = if ($k -eq $Start) { $FirstLine } else { $Lines[$k] }
        $t = $raw.TrimStart()
        if ($t.StartsWith('//') -or $t.StartsWith('#') -or $t.StartsWith('*') -or $t.StartsWith('/*')) {
            $out.Add($raw); continue
        }
        $semi = $raw.IndexOf(';')
        if ($semi -ge 0) { $out.Add($raw.Substring(0, $semi)); break }
        $out.Add($raw)
    }
    return ($out -join "`n")
}

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
    'ARG   the table name as its own SINGLE- or DOUBLE-quoted argument beside a post/insert call: obFetch("POST","thoughts",..), insertRows(''thoughts'',..) - a BACKTICK-quoted argument is NOT recognised, see below'
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
    'a DIFFERENT table''s insert ABOVE a URL or ARG site, stating its own plane: the fence clips at the donor''s SITE LINE, and the donor''s exposure key sits in its BODY below that line, INSIDE the clip. Measured at 0/3/10/25 lines of separation: green. Only the ORM shape is immune, because its evidence is the statement itself. -SelfTest case 4 plants it and records the miss'
    'a BACKTICK-quoted table argument: obFetch("POST", `thoughts`, ..). The ARG pattern accepts '' and " only, and a backtick is a quote in JS - such a producer is not flagged and is not COUNTED as a site at all. -SelfTest case 5 plants it and records the miss'
    'anything under a directory whose name ends in -data, or that is a reparse point (junction/symlink): both are pruned in addition to the named list, and both are printed below'
    'anything under a path on the ALLOW-LIST below - notably documentation\ and docs\. A producer living under either is never scanned. -SelfTest case 6 plants one and records the miss'
    'a plane stated in a TRAILING comment on a code line: only WHOLE-LINE comments are dropped from the evidence, because cutting at a mid-line // would discard a real key sharing a line with a URL literal'
)

# WHAT CAN CLEAR A SITE THAT STATES NO PLANE - A CATEGORY, PRINTED FIRST, WITH EXAMPLES UNDER
# IT. $SHAPES_BLIND above is about DETECTION: producers this gate never sees. This list is the
# other failure, and it is the worse one, because the site IS counted and IS reported as
# stating its plane. Four rounds closed these one shape at a time and a verifier produced a
# fresh set each round, so the declaration is the PROPERTY, not the list. The instances below
# were each measured 2026-08-31 at 5c81f97 - one fixture apiece, an unlabelled POST plus the
# clearing text - and each reported `OK - all 1 RECOGNISED corpus insert site(s) state their
# plane`, exit 0. A sixth, the mis-cased key `Exposure: "ops"`, was in this list and is NOT
# any more: it is FIXED (Test-StatesPlane is -cmatch now), because PostgREST refuses that key
# and a green on a row the database rejects is not a declaration gap, it is a defect.
$EVIDENCE_CATEGORY = @(
    'THE EVIDENCE TEST IS A TEXT MATCH WITHIN A WINDOW AROUND THE SITE. ANY occurrence of the key that'
    'is not THIS STATEMENT''S OWN PLANE DECLARATION can clear it. The instances below are KNOWN and'
    'MEASURED; THE LIST IS ILLUSTRATIVE, NOT EXHAUSTIVE, and the next shape is not on it. A green here'
    'is never evidence that a row carries a plane - only the NOT NULL + CHECK in the database is that.'
)
$EVIDENCE_CLEARS = @(
    'a TYPE ANNOTATION near the site: interface CorpusRow { exposure: "ops" | "personal" }'
    'a SIBLING OBJECT in the same function: const audit = { actor: "cron", exposure: "ops" };'
    'the word inside a plain STRING LITERAL: const ERR = "row rejected: exposure: label missing";'
    'the word inside SQL TEXT: "select id from thoughts where exposure = ''ops''"'
    'a BLOCK-COMMENT CONTINUATION line carrying no marker of its own, inside /* .. */ - only a line whose TRIMMED start is a comment marker is dropped, and a continuation line''s is not'
    'anything else with the same property, which is the point of the statement above this list'
)

# Trees with no first-party source in them. Same list as check-llm-gateway-routing.ps1, for
# the same reason: walking node_modules pushes the pre-commit hook past two minutes.
# `worktrees` is pruned because the main checkout carries `.claude/worktrees/<id>/` - whole
# second copies of this repo. Scanning them doubles the work and reports another session's
# in-progress edits as this tree's violations.
$pruneDirNames = @('.git', '.venv', '.testvenv', 'node_modules', '.next', 'dist', 'build',
                   'backups', 'tiktoken-cache', 'notebook_data', 'data', 'coverage',
                   'worktrees')
# AND THE PRUNES THAT ARE NOT NAMES. Get-ScanFiles also skips any directory matching these
# globs, and any directory that is a REPARSE POINT (a junction or symlink - it would
# otherwise be walked twice, or out of the tree entirely). These were invisible: the run's
# 'DIRECTORIES PRUNED' line printed $pruneDirNames alone, so a producer under `foo-data/`
# was excluded by a rule the output did not mention. Measured - a planted producer under
# `scratch-data/` was neither scanned nor named. A blind spot that is stated is a scope; a
# blind spot that is silent is a false green.
$pruneDirGlobs = @('*-data')

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
    Write-Host "  WHAT CAN CLEAR A COUNTED SITE THAT STATES NO PLANE - this is the WORSE failure, because the run" -ForegroundColor DarkGray
    Write-Host "  reports such a site as examined AND as stating its plane. It is a CATEGORY, not a list:" -ForegroundColor DarkGray
    foreach ($e in $EVIDENCE_CATEGORY) { Write-Host "      $e" -ForegroundColor DarkGray }
    Write-Host "  KNOWN INSTANCES, each measured - illustrative, NOT exhaustive:" -ForegroundColor DarkGray
    foreach ($e in $EVIDENCE_CLEARS)   { Write-Host "    ~ $e" -ForegroundColor DarkGray }
    Write-Host ("  EXTENSIONS SCANNED: " + (($exts | ForEach-Object { $_.TrimStart('*') }) -join ' ')) -ForegroundColor DarkGray
    Write-Host ("  DIRECTORIES PRUNED (by name): " + ($pruneDirNames -join ' ')) -ForegroundColor DarkGray
    Write-Host ("  DIRECTORIES PRUNED (by glob): " + ($pruneDirGlobs -join ' ') + "   plus ANY reparse point (junction/symlink)") -ForegroundColor DarkGray
    Write-Host   "  PATHS ALLOWED WITHOUT SCANNING - a producer under any of these is never seen:" -ForegroundColor DarkGray
    foreach ($a in $allowPathLike) { Write-Host "    ! $a" -ForegroundColor DarkGray }
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
                $skip = $false
                foreach ($g in $pruneDirGlobs) { if ($name -like $g) { $skip = $true; break } }
                if ($skip) { continue }
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
        # LINE SPLIT, AND IT WAS SILENTLY BROKEN ON WINDOWS - THE PLATFORM THIS RAN ON.
        # This read -split "<CR>?<newline>", with a BARE CR before the `?`. The repo blob is
        # 22 0d 3f 0a 22 = "<CR>?<LF>" = optional-CR then LF, which is CORRECT. But
        # core.autocrlf=true (set on this machine) rewrites that trailing LF on checkout, so
        # the WINDOWS working copy is 22 0d 3f 0d 0a 22 = "<CR>?<CR><LF>", which REQUIRES a CR.
        #
        # So the false green needs BOTH: a Windows checkout of THIS SCRIPT and an LF-only
        # SCANNED FILE. Then nothing splits - the whole file becomes one line, every fence
        # collapses into it, the evidence window becomes the ENTIRE FILE, and any `exposure`
        # anywhere in that file clears every producer in it. All four combinations measured
        # 2026-08-31 on one fixture (a labelled `thoughts` POST 40 lines below an unlabelled
        # `agent_memories` POST):
        #
        #   script checkout   scanned file   old gate
        #   CRLF (Windows)    LF             'all 1 site(s) state their plane'  <- FALSE GREEN
        #   CRLF (Windows)    CRLF           FAIL 1 of 2                        correct
        #   LF   (Linux CI)   LF             FAIL 1 of 2                        correct
        #   LF   (Linux CI)   CRLF           FAIL 1 of 2                        correct
        #
        # An earlier draft of this comment said the OPPOSITE - that an LF checkout was the
        # broken one and 'this worktree is CRLF so the runs here were unaffected'. CRLF is
        # the AFFECTED side. The runs here were unaffected for a different reason, measured:
        # of the 44 LF-majority files in the scan set, ZERO name a corpus table. The vacuity
        # guard would not have caught it either way - one site is not zero sites.
        $lines = [regex]::Split($text, "\r\n|\n|\r")
        $n = $lines.Count

        # EVERY corpus-site line in the file, in ANY shape, with the TABLE it names.
        # These are the FENCES: a site's evidence may not be read across a corpus site that
        # names a DIFFERENT table.
        #
        # WHAT THE FENCE ACTUALLY FIXES, MEASURED - AND IT IS LESS THAN THIS COMMENT USED
        # TO CLAIM. It said the fence stops "an `exposure` key belonging to one table's
        # statement clearing another table's statement below it". Running it refutes that.
        # The fence clips the evidence window at the donor's SITE LINE, and a donor's
        # `exposure` key is almost never ON its site line - it is in the BODY, one to three
        # lines BELOW it, which is INSIDE the clip. So a `thoughts` POST stating its plane
        # ABOVE an `agent_memories` POST that states none still CLEARS it. Measured
        # 2026-08-31 at separations of 0, 3, 10 and 25 lines: 8 sites, 0 violations, green.
        #
        # WHAT IS FIXED IS THE ORM VICTIM, and not by this fence at all - by the
        # statement-scoped evidence below, which REPLACES the window with the builder-to-`;`
        # slice and so cannot read a neighbour's body at any distance. index.ts:491 was an
        # ORM site, which is why that site went red and why -SelfTest case 3 passes: its
        # victim is an ORM site too, so it cannot catch the URL/ARG case. Case 4 plants
        # exactly that case and RECORDS the known-bad verdict rather than pretending.
        #
        # THE FENCE IS KEPT because it is not useless: it stops a donor whose key IS on its
        # own site line, and it bounds the window. It is simply not the fix for URL and ARG
        # victims with a donor above, and $SHAPES_BLIND now says so.
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
                $tail = Get-Statement -Lines $lines -Start $i -FirstLine $line.Substring($fromAt) -MaxLines $ormWindow
                if ($tail -match $ormInsert) {
                    $isSite = $true
                    # EVIDENCE: the WHOLE statement, builder to terminating `;`, however long.
                    # Not a line count - a 33-line insert body is one statement, and a window
                    # of 30 would have cut off the very key it is looking for.
                    $evidence = Get-Statement -Lines $lines -Start $i -FirstLine $line.Substring($fromAt) -MaxLines $stmtMaxLines
                }
            } elseif ($line -match $siteArg) {
                $alo = [Math]::Max(0, $i - $argWindow); $ahi = [Math]::Min($n - 1, $i + $argWindow)
                if (($lines[$alo..$ahi] -join "`n") -match $postVerb) { $isSite = $true }
            }
            if (-not $isSite) { continue }

            $script:InsertSites++
            if (Test-StatesPlane $evidence) { continue }       # the plane is stated

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

        # CASES 3b/3c - EVIDENCE THAT IS NOT A DECLARATION. Cases 1-3 all state the plane
        # with a real key, so all three passed while the evidence test was a bare substring
        # match on the word `exposure`. These two are the shapes that exposed it: a COMMENT
        # and an unrelated IDENTIFIER, each above a bare POST with no plane anywhere. Both
        # were reported as COUNTED SITES THAT STATE THEIR PLANE. They are FALSE GREENS, not
        # misses, which is why they are hard assertions here and not blind-spot records.
        Remove-Item (Join-Path $tmp "neighbour-key.mjs") -Force
        $notEvidence = @(
            @{ N = "3b"; P = "comment-only.mjs"; D = "a COMMENT mentioning exposure"
               B = "const U = process.env.SUPABASE_URL;|// exposure is applied downstream by the ingest worker|async function ingest(row) {|  return await fetch(`${U}/rest/v1/thoughts`, {|    method: `"POST`",|    body: JSON.stringify({ content: row.content }),|  });|}" }
            @{ N = "3c"; P = "identifier-only.mjs"; D = "an unrelated IDENTIFIER containing the word"
               B = "const U = process.env.SUPABASE_URL;|const exposureMetricsCounter = 0;|async function ingest(row) {|  return await fetch(`${U}/rest/v1/thoughts`, {|    method: `"POST`",|    body: JSON.stringify({ content: row.content }),|  });|}" }
        )
        foreach ($ne in $notEvidence) {
            $nedir = Join-Path $tmp ("notev" + $ne.N)
            New-Item -ItemType Directory -Path $nedir -Force | Out-Null
            Set-Content -Path (Join-Path $nedir $ne.P) -Value (($ne.B -split [regex]::Escape("|")) -join [Environment]::NewLine) -Encoding ASCII
            $nev = @(Find-Violations -ScanRoot $nedir)
            if ($nev.Count -lt 1) {
                Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - case $($ne.N): a producer with NO plane anywhere PASSED, cleared by $($ne.D). That is a FALSE GREEN on a COUNTED site - the run reports it as examined and as stating its plane." -ForegroundColor Red
                $selfFail++
            } else {
                Write-Host "[check-corpus-exposure-producers] SELF-TEST red: case $($ne.N) - $($ne.D) does NOT count as stating the plane ($($nev[0].File):$($nev[0].Line))" -ForegroundColor Yellow
            }
        }

        # --- CASES 4-6: THE DECLARED BLIND SPOTS, RECORDED RATHER THAN PRETENDED ----------
        #
        # Cases 1-3 prove the three RECOGNISED shapes behave. They proved nothing about the
        # blind list, and the blind list is where this gate is weakest - a verifier planted
        # all three of these and the gate reported OK, exit 0, with unlabelled producers on
        # disk. A blind spot that is stated is a scope; a blind spot that is silent is a
        # false green, and a blind spot stated ONLY in a comment is one nobody re-measures.
        #
        # THESE CASES DO NOT FAIL THE SELF-TEST WHEN THE GATE MISSES - the miss IS the
        # documented behaviour, and it is what $SHAPES_BLIND claims. They report the verdict
        # either way. If one ever starts being FLAGGED, that is good news and the run says
        # so, with the instruction to strike the entry from $SHAPES_BLIND. Closing a blind
        # spot must not turn a check red, or the next person stops closing them.
        $blindCases = @(
            @{ N = 4
               T = "URL victim with a DIFFERENT table's labelled producer ABOVE it"
               P = "donor-above.mjs"
               W = "the agent_memories POST states no plane; the fence clips at the thoughts SITE LINE and its exposure key sits BELOW that line, inside the clip"
               B = @'
const U = process.env.SUPABASE_URL;
async function writeThought(row) {
  return await fetch(`${U}/rest/v1/thoughts`, {
    method: "POST",
    body: JSON.stringify({ content: row.content, exposure: "ops" }),
  });
}
async function writeMemory(row) {
  return await fetch(`${U}/rest/v1/agent_memories`, {
    method: "POST",
    body: JSON.stringify({ summary: row.summary }),
  });
}
'@ }
            @{ N = 5
               T = "BACKTICK-quoted table argument"
               P = "backtick-arg.mjs"
               W = "a backtick is a quote in JS, and the ARG pattern accepts single and double quotes only - so this is not flagged and is not COUNTED as a site at all"
               B = @'
async function ingest(row) {
  return await obFetch("POST", `thoughts`, { content: row.content });
}
'@ }
            @{ N = 6
               T = "a producer under an ALLOW-LISTED path (docs\)"
               P = "docs\ingest.mjs"
               W = "the allow-list excludes *\docs\* and *\documentation\*, so the file is never read - the run now prints that list"
               B = @'
const U = process.env.SUPABASE_URL;
async function ingest(row) {
  return await fetch(`${U}/rest/v1/thoughts`, {
    method: "POST",
    body: JSON.stringify({ content: row.content }),
  });
}
'@ }
        )
        Write-Host "[check-corpus-exposure-producers] SELF-TEST blind spots - each plants an UNLABELLED producer and records what the gate does with it:" -ForegroundColor DarkGray
        foreach ($bc in $blindCases) {
            $bdir  = Join-Path $tmp ("blind" + $bc.N)
            $bfile = Join-Path $bdir $bc.P
            New-Item -ItemType Directory -Path (Split-Path -Parent $bfile) -Force | Out-Null
            Set-Content -Path $bfile -Value $bc.B -Encoding ASCII
            $bv = @(Find-Violations -ScanRoot $bdir)
            $bs = $script:InsertSites
            if ($bv.Count -gt 0) {
                Write-Host "  CASE $($bc.N) NOW FLAGGED - $($bc.T). This blind spot has CLOSED: strike its entry from SHAPES_BLIND and correct the header. This is NOT a failure." -ForegroundColor Green
            } else {
                Write-Host "  CASE $($bc.N) MISSED, as documented - $($bc.T): $bs site(s) counted, 0 flagged." -ForegroundColor Yellow
                Write-Host "        why: $($bc.W)" -ForegroundColor DarkGray
            }
        }

        if ($selfFail -gt 0) {
            Write-Host "[check-corpus-exposure-producers] SELF-TEST FAILED - $selfFail case(s) took the wrong branch." -ForegroundColor Red
            exit 1
        }
        Write-Host "[check-corpus-exposure-producers] SELF-TEST PASSED - red on a violation, red on a neighbour's key, red on a comment and on a look-alike identifier, green on a real fix." -ForegroundColor Green
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
