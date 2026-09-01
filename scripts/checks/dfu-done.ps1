# dfu-done.ps1 - THE AUTHORITY FOR "the dark-factory-unification plan is 100% met".
#
# Not a summary. Not a phase report. It exits 0 only when every one of PLAN.md section
# C.8's eight clauses holds, and EVERY CLAUSE IS DECIDED BY RUNNING SOMETHING. A claim in
# a document is an input to a check here, never the check itself - section 0 A6 of the
# same plan records the verdict on prose verification: FALSIFIED.
#
# WHY THE SHAPE IS THE ANDON BOARD'S. This effort found eleven checks that were green
# while checking nothing, and the fix that finally held was not another patch: it was
# scripts/agent-harness/andon.ps1's EXHAUSTIVE CENSUS, where every outcome lands in
# exactly one counted bucket, the buckets must sum to the subjects in scope, and the
# passing verdict requires every failing bucket to be PROVABLY EMPTY. That shape is
# reused here deliberately rather than reinvented, and the reuse is the point: a second
# home-grown verdict function would be a twelfth place for the same defect to live.
#
# THE FOUR RULES THIS FILE IS BUILT ON, each earned by a named failure in DECISIONS.md:
#
#   1. NEVER DEFAULT TO PASS. A command that errors, a file that is missing, a query that
#      returns nothing, a verdict word nobody enumerated - each lands in a REFUSING
#      bucket. `met` is decided positively; it is never what you get because nothing
#      objected. (Class: a guard deciding by exception.)
#   2. SAY WHAT WAS NOT CHECKED. Every clause carries a COVERAGE record - subjects
#      expected vs subjects actually evaluated - so "clear because we looked" and "clear
#      because we didn't" are different words in the output and different buckets in the
#      census. A clause that evaluated 0 of its subjects can never be met, however
#      little objected. (Class: a check green while checking nothing.)
#   3. DERIVE ENUMERATIONS FROM THE TREE. Phases come from parsing section 2's table,
#      branches from git for-each-ref, worktrees from git worktree list, walkthrough rows
#      from the walkthrough. A hand-written list is a list with a spell-checker, and it is
#      how this effort's completeness tests kept passing over the file nobody added to
#      them. (Class: a derived gate whose alphabet is too narrow.)
#   4. RECORD THE COMMAND AND ITS EXIT CODE. Every probe prints what was run and what it
#      returned, so a reader re-runs it instead of trusting this script. A verdict nobody
#      can reproduce is a claim.
#   5. EVERY NEGATIVE PROBE CARRIES A POSITIVE CONTROL. "I attacked the door and nothing
#      came back" is what a bound door says AND what a broken query says. So each probe
#      that asserts an absence also asserts a PRESENCE it must see - an ops-labelled twin
#      of the same fixture, written the same way at the same moment - and a probe that
#      sees NEITHER is indeterminate. Three of clause 3's doors could not have failed
#      under any boundary state before this rule; one of them reported "pass" against a
#      PostgREST path that does not exist. (Class: a check green while checking nothing,
#      in its hardest-to-see form - the subject was never in the query.)
#
# ABOUT RULE 3, PRECISELY, AND ITS LIMIT. Branches, worktrees, walkthrough rows, the
# PostgREST surface, the RLS stage set and the direct DB clients are all read from the tree
# or the live system. THREE sets are NOT derived and must not pretend to be: clause 3's
# door floor, clause 4's service set, and the U0-U6 + U8 PHASE FLOOR. They come from section
# C.8's own prose, because C.8 is the specification. What keeps them honest is that each is
# CHECKED BACK against the plan's words at run time (`door-set-matches-plan`,
# `service-set-matches-plan`, `phase-floor-matches-plan`), so a subject the plan names and
# this file does not probe turns the clause red instead of disappearing. A pinned floor
# plus a drift check is a different thing from a hand list.
#
#   6. NEVER DERIVE THE POPULATION FROM THE DOCUMENT UNDER TEST. Clauses 1, 2 and 7 took
#      their subjects from the CURRENT PLAN.md, so a phase could delete itself out of its
#      own population: remove U1's row and clause 2 read "met, coverage 1/1", U1's chain
#      never reconstructed and no not_evaluated entry naming it. MERELY REMOVING THE BOLD -
#      `| **U1** |` -> `| U1 |`, invisible to a reader because the row is still printed -
#      did the same. Coverage still said "N of N" because N had shrunk: the failure this
#      script exists to prevent, operating on the script. The floor above is the fix, and
#      a floor phase missing from the table is a FAILURE, never a smaller N.
#   8. A CHECKER MUST NOT MEASURE A WORLD ITS SUBJECT CAN CHANGE. Clauses 1 and 5 EXECUTE
#      every backtick span under a "How to run:" marker in WALKTHROUGH.md, and clause 5 ran
#      them with the AUDITED REPOSITORY as their working directory. A `## U0` section whose
#      marker wrote a file into documentation/notes reported "exit 0 - re-runs green", and
#      clause 7's U0 then fell from exit 3 to exit 2: its findings-note artifact discharged
#      by a file the run itself had created. Rules 1-7 all constrain what the checker READS,
#      and every one of them can be satisfied while this stands - it is about EFFECTS.
#      The fix is ordering, not filtering: every artifact any clause reads is SNAPSHOTTED
#      BEFORE THE FIRST COMMAND RUNS, commands execute only in a disposable clone with the
#      audited documents write-locked, the command set is REPORTED, and a run that moved the
#      audited tree is refused as an authority. See "THE FIFTEENTH CLASS" below.
#   9. NORMALISE IN EVERY READER, THEN GREP FOR THE SHAPE. `Remove-NonProse` strips HTML
#      comments and code fences, and it had exactly ONE call site in 3,866 lines - inside
#      Get-DfuSection, which serves PLAN.md alone. WALKTHROUGH.md and DECISIONS.md were read
#      RAW, so the comment attacks fixed for the plan worked unchanged one file over: five
#      walkthrough phase sections inside a closed comment gave clause 5 "met, 8 of 8"; a
#      commented ledger entry closed a PARKED entry, granted a clause-4 carve-out and
#      discharged clause 7's ledger artifact; and Get-WalkthroughRuns parsed AND EXECUTED a
#      commented `## U<n>` section. Fixing one reader is not fixing the shape. And an
#      UNTERMINATED `<!--` defeated the stripping outright, because the regex requires the
#      closer - malformed markup now FAILS CLOSED and is reported, never ignored.
#  10. A SUBSTRING IS NOT A STRUCTURE. `git ls-remote` prints "<sha>TAB<ref>" and the
#      gitlink gate searched the whole output for the pinned sha, so a tag NAMED after that
#      sha - `git tag rollback-$(git rev-parse HEAD)` - turned the gate green for a commit
#      the remote could not serve. `docker ps --format {{.Names}}` prints one name per line
#      and the ops-gateway probe matched it as a blob, so any container whose name merely
#      CONTAINED the service's answered for it. Both read a structured output as an
#      undifferentiated blob; both are now parsed by column and compared whole. (Prose is a
#      different case: a phrase looked for in PLAN.md or DECISIONS.md has no columns to
#      parse, and those tests stay what they are.)
#  11. A PARSER IS ONLY AS HONEST AS ITS LEAST CAREFUL CONSUMER. Amendment A3 replaced a
#      LOUD refusal - a duplicated phase id REFUSES - with a QUIET one: a cell that only
#      LOOKS like an id cell is declined and recorded in `$Parse.ignored`. That field had
#      exactly ONE consumer in 4,600 lines, `Add-PhaseFloorProbes`, which only ever sees the
#      CURRENT PLAN.md. Clause 2's HISTORICAL reader kept `problems` and dropped `ignored`,
#      so a revision whose id cell carried trailing text was skipped in silence and the
#      chain started AFTER ITS OWN BEGINNING - the exact failure that reader's own comment
#      names. Rule 9's shape, one field over. Every consumer surfaces `ignored` now, the
#      dead `Get-PhaseTable` wrapper that structurally could not is removed, and
#      verify-dfu-done.ps1 step R1 GREPS THIS FILE so a third reader cannot repeat it.
#      Its twin: the parenthesised-qualifier ALLOWANCE (`**U7 (standing)**` must parse) is
#      one space away from admitting `**U4 (status 2026-08-30)**`, which is the shape A3
#      exists to keep out of the anchor. It cannot be narrowed here without deleting U7 and
#      this script may not edit PLAN.md, so every admitted qualifier is NAMED on the probe.
#
# WHAT IT MAY NOT DO, from section C.8 itself: "If a clause cannot be met, that is a
# REPORT, not a redefinition." A non-zero exit naming unmet clauses is this script
# WORKING. Amending a plan column so this script goes green is the single move section
# C.8 exists to forbid, and nothing in here reads a column it is allowed to edit.
#
# MANUAL CHECKS. A clause that genuinely cannot be machine-evaluated is printed as a
# NAMED manual check and lands in manual_pending - a refusing bucket - until a result for
# that exact name is recorded in the manual-results file (-ManualResults). The recorded
# result must carry a verdict, who ran it, when, and its evidence; a malformed record is
# NOT a result. There is no path from "unrecorded" to "fine".
#
# TESTABILITY IS A FEATURE, NOT A BACK DOOR. Every clause reads its inputs from a context
# (repo root, plan path, work line, database container, ...) so the drill
# verify-dfu-done.ps1 can point the SAME code at a fixture in which a clause MUST fail.
# The context selects the SUBJECT; it cannot select the VERDICT, and there is no switch
# anywhere in this file that turns a clause off or forces it green.
#
#   .\dfu-done.ps1                       # evaluate every clause; exit 7 unless all met
#   .\dfu-done.ps1 -Json                 # machine-readable verdict on stdout
#   .\dfu-done.ps1 -Only 3               # one clause (still refuses to call the plan done)
#   .\dfu-done.ps1 -SkipLive             # no Docker/DB probes; live clauses go UNEVALUATED
#   .\dfu-done.ps1 -ListClauses          # what is declared, and what implements it
#
# EVERY RUN PRINTS THE COMMAND SET IT EXECUTED - what ran, where it ran, what it returned,
# and whether the audited tree moved while it did. Clauses 1 and 5 take those commands from
# the document under test, so that list is the part of a run which could have had effects,
# and a reader is entitled to see it beside the verdict.
#
# Exit codes: 0 every clause MET - the factory stops and hands over (C.8's handover
#               point, not the finish line; the operator's walkthrough is the last gate)
#             1 usage or configuration error - nothing was judged
#             7 the plan is NOT met. The headline word comes from the most severe
#               non-empty bucket, and each refuses:
#                 unaccounted     a clause produced a verdict this script does not
#                                 enumerate, the census did not balance, or the run
#                                 CHANGED the audited tree it was measuring
#                 failed          a clause was evaluated and is NOT satisfied
#                 unevaluated     a clause could not be decided, or was decided over
#                                 only part of its subjects
#                 manual-pending  a named manual check has no recorded result
#
[CmdletBinding()]
param(
    [switch]$Json,
    [switch]$ListClauses,
    [switch]$SkipLive,
    [int[]]$Only,
    [string]$RepoRoot,
    [string]$WorkLine,
    [string]$PlanPath,
    [string]$DecisionsPath,
    [string]$WalkthroughPath,
    [string]$NotesDir,
    [string]$ManualResults,
    [string]$Dispositions,
    [string]$DbContainer   = "openbrain-db",
    [string]$DbName        = "openbrain",
    [string]$ObNetwork     = "open-brain_obnet",
    [string]$PostgrestHost = "openbrain-postgrest:3000"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# Clause 2 requires each chain step to be quoted VERBATIM. The console default codepage
# mangles the plan's em-dashes and arrows, which would make a correctly-read quotation
# look corrupted and an actually-corrupted one indistinguishable from it. Display
# fidelity is part of the evidence here, so the output encoding is set explicitly.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------------------------
# THE CLAUSE SET IS PINNED IN CODE, because it is section C.8's and not this run's. A
# clause cannot be deleted by a config file, an environment variable or a parameter -
# that is the conditions-deleted route andon.ps1 had to close, and it is closed here by
# construction: -Only NARROWS what is evaluated and is refused as evidence of doneness
# (the clauses it skipped land in unevaluated, so the board never reads "done").
# ---------------------------------------------------------------------------------
# THE KEYS ARE STRINGS ON PURPOSE. An [ordered] dictionary indexed with an INTEGER
# resolves by POSITION, not by key - so integer clause ids silently returned the wrong
# clause and ran off the end of the collection. Keying by string makes the lookup a
# lookup. (Found by running this file, which is the only way that class ever surfaces.)
$script:DfuClauses = [ordered]@{
    "1" = "Every U-phase column is satisfied by a check that RAN, from a clean checkout"
    "2" = "No phase is parked, and every amendment chain is reconstructable and accounted for"
    "3" = "The personal-plane constraint is lifted by VALIDATION, never by emptiness"
    "4" = "Nothing is left in flight, and everything is DEPLOYED AND RUNNING"
    "5" = "The walkthrough is true - every row names a check and that check re-runs green"
    "6" = "U7 is ARMED - its loop has run one full cycle on the record"
    "7" = "The audit trail is complete"
    "8" = "THE MEMORY PLANE COMPOUNDS - a recall demonstrably informed a later effort"
}

# THE VERDICT VOCABULARY. A clause evaluator may return exactly these words. Anything
# else - including a word added by a future clause and not added here - falls to
# unrecognised, which REFUSES. No branch below names the new word, and none has to.
$script:DfuVerdictBuckets = [ordered]@{
    "met"            = "met"
    "unmet"          = "unmet"
    "unevaluated"    = "unevaluated"
    "manual-pending" = "manual_pending"
}
$script:DfuClearBucket        = "met"
$script:DfuUnrecognisedBucket = "unrecognised"

# Bucket -> headline word, in SEVERITY ORDER (most severe first). This is ALSO the
# declared bucket set: a bucket absent here is not a bucket, and a clause classified into
# one is unaccounted. Adding a bucket later means adding a word for it here; until then
# the board refuses rather than guesses.
$script:DfuBucketBoard = [ordered]@{
    "unrecognised"   = "unaccounted"
    "unmet"          = "failed"
    "unevaluated"    = "unevaluated"
    "manual_pending" = "manual-pending"
    "met"            = "done"
}
function Get-DfuBucketNames { return @($script:DfuBucketBoard.Keys) }
function Get-DfuBucket([string]$verdict) {
    if ($script:DfuVerdictBuckets.Contains($verdict)) { return [string]$script:DfuVerdictBuckets[$verdict] }
    return $script:DfuUnrecognisedBucket
}

# THE PROBE VOCABULARY, same reasoning one level down. A probe answers pass / fail /
# indeterminate. indeterminate is NOT a pass - it is the word for "this could not be
# decided", and it is what an errored command, a missing file and an empty result set all
# produce. A probe returning any other word cannot let its clause be met.
$script:DfuProbeVerdicts = @("pass", "fail", "indeterminate")

# The ONE work branch this script is willing to exclude from clause 4, stated here rather
# than inline so it appears in -ListClauses and in the JSON, where a reader can object.
#
# AND IT IS NOT APPLIED ON THIS FILE'S SAY-SO. The previous version attributed the
# carve-out to "operator, 2026-08-31" - a ruling that appears nowhere in DECISIONS.md or
# PLAN.md, so the script was excusing a branch on the strength of a sentence it contained
# about itself. That is the same defect as a green check that checks nothing, moved into a
# citation. The exclusion is now CONDITIONAL: it applies only while the ledger the operator
# actually reads names the branch. If DECISIONS.md does not, the branch is counted like any
# other and the probe says why - the carve-out has to be earned each run.
$script:DfuExcludedBranches = [ordered]@{
    "work/pod-key" = "an unrelated podcast effort - applied ONLY if DECISIONS.md records it"
}

# =================================================================================
# PLUMBING
# =================================================================================

function Read-TextFile {
    # Always UTF-8. PowerShell 5.1's Get-Content guesses at the ANSI codepage, which
    # mangles the em-dashes and section signs in PLAN.md - and a mangled read is how a
    # regex silently matches nothing and the check passes over an empty haystack.
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try { return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8) }
    catch { return $null }
}

function Invoke-Native {
    # Run an external command and RETURN ITS EXIT CODE. Never swallow it: a command whose
    # failure is invisible is this effort's own git-error-swallowed andon condition, and
    # this script would be a poor place to reproduce it.
    #
    # WHY THE CALL OPERATOR AND NOT Start-Process: Start-Process -ArgumentList JOINS the
    # array with spaces and quotes nothing, so any argument containing a space is split
    # into several. A SQL statement passed as one argument arrived as thirty, psql still
    # exited 0, and every clause-3 door reported "could not be written" - a wrong answer
    # delivered confidently. The call operator passes each array element as exactly one
    # argument, which is what every caller here assumes.
    param([string]$Exe, [string[]]$Arguments, [string]$WorkDir = $null)
    $res = [ordered]@{
        command = ("{0} {1}" -f $Exe, ($Arguments -join " "))
        exit    = $null
        stdout  = ""
        stderr  = ""
        ran     = $false
    }
    $prev = $null
    if ($WorkDir) {
        if (-not (Test-Path -LiteralPath $WorkDir)) {
            $res.stderr = "working directory does not exist: $WorkDir"
            return $res
        }
        $prev = (Get-Location).Path
        Set-Location -LiteralPath $WorkDir
    }
    try {
        $global:LASTEXITCODE = 0
        $raw = & $Exe @Arguments 2>&1
        $res.exit = [int]$global:LASTEXITCODE
        $res.ran  = $true
        $outLines = @()
        $errLines = @()
        foreach ($item in @($raw)) {
            if ($item -is [System.Management.Automation.ErrorRecord]) { $errLines += [string]$item }
            else { $outLines += [string]$item }
        }
        $res.stdout = ($outLines -join "`n")
        $res.stderr = ($errLines -join "`n")
    } catch {
        # The executable could not be started at all. That is INDETERMINATE for every
        # caller - it is not a zero and it is certainly not a pass.
        $res.stderr = ("could not run '{0}': {1}" -f $Exe, $_.Exception.Message)
        $res.exit   = $null
        $res.ran    = $false
    } finally {
        if ($prev) { Set-Location -LiteralPath $prev }
    }
    return $res
}

function Invoke-Git {
    param([string[]]$Arguments, [string]$WorkDir)
    return (Invoke-Native -Exe "git" -Arguments $Arguments -WorkDir $WorkDir)
}

function New-Probe {
    # A probe is the unit that RUNS. It always carries the command a reader can re-run and
    # the exit code it produced - including when it could not run at all, which is an
    # indeterminate, never an absence.
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Command,
        [Parameter(Mandatory = $true)][scriptblock]$Run
    )
    $probe = [ordered]@{
        name    = $Name
        command = $Command
        exit    = $null
        verdict = "indeterminate"
        note    = ""
    }
    try {
        $r = & $Run
        if ($null -eq $r) {
            $probe.note = "the probe body returned nothing - treated as INDETERMINATE, not as a pass"
            return $probe
        }
        $v = [string]$r["verdict"]
        if ($script:DfuProbeVerdicts -notcontains $v) {
            $probe.verdict = "indeterminate"
            $probe.note = ("the probe answered '{0}', which is not one of {1} - refused rather than guessed" -f `
                           $v, ($script:DfuProbeVerdicts -join "/"))
            return $probe
        }
        $probe.verdict = $v
        if ($r.Contains("note")) { $probe.note = [string]$r["note"] }
        if ($r.Contains("exit")) { $probe.exit = $r["exit"] }
    } catch {
        $probe.verdict = "indeterminate"
        $probe.note    = ("the probe threw, so nothing was decided: {0}" -f $_.Exception.Message)
    }
    return $probe
}

function New-ClauseResult {
    param([string]$Id)
    return [ordered]@{
        id        = $Id
        title     = [string]$script:DfuClauses[$Id]
        verdict   = "unevaluated"
        probes    = @()
        manual    = @()
        coverage  = [ordered]@{ subject = "subjects"; expected = 0; evaluated = 0; not_evaluated = @() }
        detail    = @()
    }
}

function Resolve-ClauseVerdict {
    # THE PER-CLAUSE CENSUS, and it decides in this order for a reason:
    #   a definite failure outranks incomplete coverage (we know it is broken),
    #   incomplete coverage outranks a clean sweep (we do not know it is whole),
    #   and a clean sweep over ZERO subjects is never met - that is rule 2, and it is the
    #   exact distinction section C.8 exists to enforce.
    param($Clause)
    $probes = @($Clause.probes)
    $fails  = @($probes | Where-Object { $_.verdict -eq "fail" })
    $indet  = @($probes | Where-Object { $_.verdict -eq "indeterminate" })
    $passes = @($probes | Where-Object { $_.verdict -eq "pass" })
    # A RECORDED FAILING MANUAL RESULT IS A DEFINITE FAILURE, not a pending one. Someone
    # ran the check and it came back fail; filing that under "still waiting" would record
    # a known red with the softer word.
    $failedManual  = @($Clause.manual | Where-Object { $_.state -eq "recorded-fail" })
    $pendingManual = @($Clause.manual | Where-Object { $_.state -eq "PENDING" })

    if ($fails.Count -gt 0 -or $failedManual.Count -gt 0) {
        $Clause.verdict = "unmet"
        return $Clause
    }
    if ($indet.Count -gt 0) {
        $Clause.verdict = "unevaluated"
        return $Clause
    }
    if ([int]$Clause.coverage.expected -lt 1) {
        $Clause.verdict = "unevaluated"
        $Clause.detail += "the clause declared no subjects at all - nothing was in scope, so nothing was proven"
        return $Clause
    }
    if ([int]$Clause.coverage.evaluated -lt 1) {
        $Clause.verdict = "unevaluated"
        $Clause.detail += "0 of $($Clause.coverage.expected) subjects were evaluated - clear because we did not look"
        return $Clause
    }
    if ([int]$Clause.coverage.evaluated -lt [int]$Clause.coverage.expected) {
        $Clause.verdict = "unevaluated"
        $Clause.detail += ("only {0} of {1} subjects were evaluated - partial coverage is not satisfaction" -f `
                           $Clause.coverage.evaluated, $Clause.coverage.expected)
        return $Clause
    }
    if ($pendingManual.Count -gt 0) {
        $Clause.verdict = "manual-pending"
        return $Clause
    }
    if ($passes.Count -lt 1) {
        $Clause.verdict = "unevaluated"
        $Clause.detail += "no probe reported a pass - met is decided positively, never by the absence of an objection"
        return $Clause
    }
    $Clause.verdict = "met"
    return $Clause
}

function Get-ManualResult {
    # A recorded manual result, or $null. INCOMPLETE IS NOT RECORDED: a record missing its
    # verdict, its runner, its date or its evidence is a note somebody left, not a check
    # somebody ran, and treating the two the same is how prose verification got its
    # FALSIFIED verdict in the first place.
    param($Store, [string]$Name)
    if ($null -eq $Store) { return $null }
    if ($null -eq $Store.PSObject) { return $null }
    # THE PARENTHESES ARE THE WHOLE FIX, and their absence made the manual mechanism
    # UNREACHABLE. `-not $Store.PSObject.Properties.Name -contains $Name` binds as
    # `(-not <the array of names>) -contains $Name`; a non-empty array is truthy, so the
    # guard became `$false -contains $Name` = $false, never returned, and the next line
    # read a property that does not exist. Under Set-StrictMode that THROWS, the clause
    # evaluator's catch turned the whole clause into `clause-N-threw`, and the machine
    # probe that had already decided its half was DISCARDED. C.8 requires the script to
    # REFUSE without a recorded result; crashing is not refusing. The drill never saw it
    # because every fixture pointed at a path with no file at all.
    if (-not ($Store.PSObject.Properties.Name -contains $Name)) { return $null }
    $r = $Store.$Name
    if ($null -eq $r) { return $null }
    foreach ($f in @("verdict", "by", "date", "evidence")) {
        if (-not ($r.PSObject.Properties.Name -contains $f)) { return $null }
        if ([string]::IsNullOrWhiteSpace([string]$r.$f)) { return $null }
    }
    if ([string]$r.verdict -ne "pass") { return $r }
    return $r
}

function Add-ManualCheck {
    param($Clause, $Store, [string]$Name, [string]$What)
    $rec = Get-ManualResult -Store $Store -Name $Name
    $entry = [ordered]@{ name = $Name; what = $What; state = "PENDING"; recorded = $null }
    if ($null -eq $rec) {
        $entry.state = "PENDING"
    } elseif ([string]$rec.verdict -eq "pass") {
        $entry.state    = "recorded"
        $entry.recorded = ("{0} by {1} on {2}: {3}" -f $rec.verdict, $rec.by, $rec.date, $rec.evidence)
    } else {
        $entry.state    = "recorded-fail"
        $entry.recorded = ("{0} by {1} on {2}: {3}" -f $rec.verdict, $rec.by, $rec.date, $rec.evidence)
    }
    $Clause.manual += $entry
    return $entry
}

# =================================================================================
# CONTEXT - resolved once, printed, and injectable so the drill can aim the same code
# at a fixture. Every path is RESOLVED AND CHECKED here; a missing input is a refusal at
# the top rather than an empty string that quietly matches nothing further down.
# =================================================================================

function Get-Bound {
    # $PSBoundParameters is a DICTIONARY, and it only holds what the caller actually
    # passed. Property access on a missing key THROWS under Set-StrictMode, so every read
    # goes through here and an unpassed parameter is simply an empty string.
    param($P, [string]$Name)
    if ($null -eq $P) { return "" }
    if ($P.ContainsKey($Name)) { return [string]$P[$Name] }
    return ""
}

function Resolve-DfuContext {
    param($P)
    $root = (Get-Bound -P $P -Name "RepoRoot")
    if (-not $root) {
        $g = Invoke-Git -Arguments @("rev-parse", "--show-toplevel") -WorkDir $PSScriptRoot
        if ($g.exit -eq 0 -and $g.stdout.Trim()) { $root = $g.stdout.Trim() }
    }
    if (-not $root) { throw "could not resolve a repository root (pass -RepoRoot)" }
    $root = (Resolve-Path -LiteralPath $root).Path

    $dfu = Join-Path $root "documentation\implementation-guide\dark-factory-unification"
    $ctx = [ordered]@{
        root          = $root
        dfu           = $dfu
        plan          = $(if ((Get-Bound -P $P -Name "PlanPath"))        { (Get-Bound -P $P -Name "PlanPath") }        else { Join-Path $dfu "PLAN.md" })
        decisions     = $(if ((Get-Bound -P $P -Name "DecisionsPath"))   { (Get-Bound -P $P -Name "DecisionsPath") }   else { Join-Path $dfu "DECISIONS.md" })
        walkthrough   = $(if ((Get-Bound -P $P -Name "WalkthroughPath")) { (Get-Bound -P $P -Name "WalkthroughPath") } else { Join-Path $dfu "WALKTHROUGH.md" })
        notes         = $(if ((Get-Bound -P $P -Name "NotesDir"))        { (Get-Bound -P $P -Name "NotesDir") }        else { Join-Path $root "documentation\notes" })
        manual        = $(if ((Get-Bound -P $P -Name "ManualResults"))   { (Get-Bound -P $P -Name "ManualResults") }   else { Join-Path $dfu "dfu-done-manual.json" })
        dispositions  = $(if ((Get-Bound -P $P -Name "Dispositions"))    { (Get-Bound -P $P -Name "Dispositions") }    else { Join-Path $dfu "dfu-done-dispositions.json" })
        workline      = (Get-Bound -P $P -Name "WorkLine")
        db            = (Get-Bound -P $P -Name "DbContainer")
        dbname        = (Get-Bound -P $P -Name "DbName")
        obnet         = (Get-Bound -P $P -Name "ObNetwork")
        postgrest     = (Get-Bound -P $P -Name "PostgrestHost")
        skiplive      = [bool](Get-Bound -P $P -Name "SkipLive")
    }
    if (-not $ctx.workline) {
        # The work line is whatever the MAIN checkout has loaded - the same rule
        # new-worktree.ps1 uses. Derived, never assumed to be a particular branch name.
        $g = Invoke-Git -Arguments @("rev-parse", "--abbrev-ref", "HEAD") -WorkDir $root
        if ($g.exit -eq 0 -and $g.stdout.Trim() -and $g.stdout.Trim() -ne "HEAD") { $ctx.workline = $g.stdout.Trim() }
    }
    return $ctx
}

function Read-JsonStore {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    $raw = Read-TextFile -Path $Path
    if (-not $raw) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

# ---------------------------------------------------------------------------------
# DERIVED ENUMERATIONS. Everything below reads the tree. Nothing below is a list
# somebody maintains by hand - rule 3.
# ---------------------------------------------------------------------------------

function Remove-NonProse {
    # Strip HTML comments and fenced code blocks before ANY structure is read out of a
    # markdown document. Both can carry text that LOOKS like a table row, a heading or a
    # command and that NO READER SEES as one, which is what makes them a forgery surface
    # rather than a formatting detail.
    #
    # THIS FUNCTION HAD EXACTLY ONE CALL SITE IN 3,866 LINES - inside Get-DfuSection, which
    # serves PLAN.md and nothing else. So WALKTHROUGH.md and DECISIONS.md were read RAW,
    # and round 4's own RESURRECTION attack worked unchanged one file over: five phase
    # sections of WALKTHROUGH.md inside a properly CLOSED comment gave clause 5 "met,
    # 8 of 8" while the document the operator reviews showed two; a commented `## ` entry
    # carrying `**Un-parks:**` closed a PARKED entry; a commented `## ... clause 4
    # exclusion` granted the carve-out; a commented heading discharged clause 7's ledger
    # artifact; and Get-WalkthroughRuns PARSED AND EXECUTED a `## U<n>` section that lived
    # inside a comment. The rule adopted this week - fix the shape, then grep for it - is
    # now applied: EVERY markdown reader in this file normalises first - Get-DfuSection,
    # Get-LedgerSections, Get-WalkthroughRuns, Get-WalkthroughSectionIds - and the ONE door
    # a document comes through is New-DfuSnapshot, which stores the normalised form beside
    # the raw one so an inline scan in a clause body cannot accidentally get the raw text.
    # `grep -n 'Read-TextFile' dfu-done.ps1` is the check: outside the plumbing it appears
    # only in the snapshot and the fingerprint.
    #
    # AND A MALFORMED COMMENT FAILS CLOSED. The regex requires the closer, so an
    # UNTERMINATED `<!--` was simply not a comment: a row deleted from section 2 and copied
    # after a bare `<!--` inside section 2 was read as a row. An unterminated comment now
    # swallows the rest of the document exactly as an unterminated fence does - refusing
    # wide is the safe direction, because the alternative is reading text the renderer
    # hides. `Get-MarkdownDefects` reports the malformation so a clause can go RED rather
    # than merely go quiet.
    param([string]$Text)
    if (-not $Text) { return "" }
    # Closed comments first, then anything after an unterminated opener is discarded.
    $t = [regex]::Replace($Text, '(?s)<!--.*?-->', "`n")
    $bare = $t.IndexOf('<!--')
    if ($bare -ge 0) { $t = $t.Substring(0, $bare) }
    $out = New-Object System.Collections.Generic.List[string]
    $fence = ""
    foreach ($line in ($t -split "`n")) {
        $l = $line.TrimEnd("`r")
        if (-not $fence) {
            if ($l -match '^\s*(```+|~~~+)') { $fence = $Matches[1]; [void]$out.Add(""); continue }
            [void]$out.Add($l)
        } else {
            if ($l -match ('^\s*' + [regex]::Escape($fence))) { $fence = "" }
            [void]$out.Add("")
        }
    }
    return ($out -join "`n")
}

function Get-MarkdownDefects {
    # The malformations that make a document say one thing to a reader and another to a
    # parser. Reported, never silently absorbed: `Remove-NonProse` refuses WIDE (it drops
    # everything after a bare `<!--`), which stops the forgery but would otherwise look
    # exactly like a shorter document.
    param([string]$Text)
    $out = @()
    if ($null -eq $Text) { return $out }
    $stripped = [regex]::Replace($Text, '(?s)<!--.*?-->', "`n")
    $bare = $stripped.IndexOf('<!--')
    if ($bare -ge 0) {
        $line = 1 + @([regex]::Matches($stripped.Substring(0, $bare), "`n")).Count
        $out += ("an UNTERMINATED HTML comment opens at line {0} and is never closed - everything after it is discarded, because text a renderer hides must not be read as structure" -f $line)
    }
    $fence = ""
    $ln = 0
    $fenceAt = 0
    foreach ($l in ($stripped -split "`n")) {
        $ln++
        $t = $l.TrimEnd("`r")
        if (-not $fence) { if ($t -match '^\s*(```+|~~~+)') { $fence = $Matches[1]; $fenceAt = $ln } }
        else { if ($t -match ('^\s*' + [regex]::Escape($fence))) { $fence = "" } }
    }
    if ($fence) { $out += ("an UNTERMINATED code fence opens at line {0} and is never closed - everything after it is discarded" -f $fenceAt) }
    return @($out)
}

function Split-TableRow {
    # A markdown row's cells, trimmed, with the empty elements the leading and trailing
    # pipes produce removed. The cells are returned as a LIST so a caller can address them
    # by the header's name for that column instead of by a hard-coded index.
    #
    # GFM'S ESCAPED PIPE IS PART OF THE GRAMMAR, and splitting on every `|` ignored it. A
    # cell written `foo \| bar` is ONE cell to every renderer and was TWO cells here, so
    # every column after it shifted: an original requirement parked in the What cell behind
    # an escaped pipe let the VISIBLE Validated-by column be weakened while
    # `chain-U0-original-vs-current` still found the original. The header names a column and
    # that name selects an INDEX - so an index applied to cells a splitter had already
    # misaligned meant "found BY NAME" was not true end to end. Split on an UNESCAPED pipe,
    # then unescape.
    param([string]$Row)
    $r = $Row.Trim()
    $cells = @($r -split '(?<!\\)\|')
    if ($cells.Count -ge 1 -and $cells[0].Trim() -eq "") { $cells = @($cells | Select-Object -Skip 1) }
    if ($cells.Count -ge 1 -and $cells[$cells.Count - 1].Trim() -eq "") { $cells = @($cells | Select-Object -First ($cells.Count - 1)) }
    return @($cells | ForEach-Object { ($_ -replace '\\\|', '|').Trim() })
}

function Get-DfuSection {
    # The text of ONE section, located by its heading and terminated at the next heading of
    # the same or a higher level. Returns @{ text; count; heading } - and `text` is $null
    # unless the heading matched EXACTLY ONCE.
    #
    # WHY IT REFUSES ON AMBIGUITY. Two floors were located by a lazy first-match regex over
    # the WHOLE plan (`Get-PlanPhaseFloor`, `service-set-matches-plan`), so a decoy passage
    # earlier in the document - a quotation, an example, a superseded draft - becomes the
    # text the floor is checked back against, and the drift check that is supposed to stop
    # a plan drifting away from this script silently checks the wrong paragraph. One match
    # is a location; two is a question nobody answered.
    # -StopAtAnyHeading ends the section at the FIRST heading of any level. Section 2's
    # phase table needs that: `### 2.1 Amendments` is a DEEPER heading, so a same-or-higher
    # rule would run the table's region on through the amendments and out the far side, and
    # a row written anywhere in there would be read as a row of the table.
    param([string]$Text, [string]$HeadingPattern, [switch]$StopAtAnyHeading)
    $res = @{ text = $null; count = 0; heading = "" }
    if (-not $Text) { return $res }
    $clean = Remove-NonProse -Text $Text
    $ms = @([regex]::Matches($clean, $HeadingPattern))
    $res.count = $ms.Count
    if ($ms.Count -ne 1) { return $res }
    $m = $ms[0]
    $res.heading = $m.Value.Trim()
    $lvl = ([regex]::Match($m.Value, '^#+')).Value.Length
    if ($StopAtAnyHeading) { $lvl = 6 }
    $start = $m.Index + $m.Length
    $rest  = $clean.Substring($start)
    $stop  = [regex]::Match($rest, ('(?m)^#{1,' + $lvl + '}\s'))
    if ($stop.Success) { $res.text = $rest.Substring(0, $stop.Index) } else { $res.text = $rest }
    return $res
}

# ---------------------------------------------------------------------------------
# SECTION 2'S TABLE, AND ONLY SECTION 2'S TABLE.
#
# WHAT THE PREVIOUS PARSER DID, AND THE THREE ATTACKS THAT LANDED ON IT. It line-scanned
# the ENTIRE file for a row naming U<n>, honoured no section boundary, no code fence and
# no HTML comment, took LAST-WINS on a duplicated id, and indexed cells POSITIONALLY
# (cell 3) without ever parsing the header row. Round 3 pinned WHICH ids must exist; it
# did not pin WHERE the row that defines an id may live, or WHICH CELL is Validated by.
#
#   SUBSTITUTION - rewrite U4's visible Validated-by to "spot-checked by the author", then
#     append the ORIGINAL row verbatim inside an HTML comment at the end of the file.
#     Last-wins read the commented row and clause 2 reported both original requirements
#     CARRIED. The identical edit WITHOUT the comment failed correctly, which is the tell.
#   RESURRECTION - delete U1's row from section 2 and put a one-line row for it inside an
#     HTML comment at the end. `phase-floor-present` passed for a row no reader sees, and
#     the chain passed with it.
#   COLUMN SHIFT - insert a column: Phase | What | Original validation (historical) |
#     Validated by | Depends on. Cell 3 then read the decoy while the VISIBLE Validated by
#     column said something weaker.
#
# So this parser: ANCHORS to section 2's own heading and stops at the next heading; strips
# fenced blocks and HTML comments first; finds the Validated-by column BY PARSING THE
# HEADER ROW for its name; and REFUSES on a duplicated id instead of picking a winner.
# Every refusal is returned as a PROBLEM, and `Add-PhaseFloorProbes` turns problems into a
# red probe - an ambiguity is never resolved silently in either direction.
# ---------------------------------------------------------------------------------

function Get-PhaseTableParse {
    # Returns @{ phases = [ordered]{ id -> @{what;validated;depends} }; problems = @();
    #            anchored = [bool]; header = "" }.
    param([string]$Text)
    $res = @{ phases = [ordered]@{}; problems = @(); anchored = $false; header = ""; ignored = @(); qualified = @() }
    if (-not $Text) { $res.problems += "the document is empty or could not be read"; return $res }

    $sec = Get-DfuSection -Text $Text -HeadingPattern '(?m)^##\s+2\.\s+[^\r\n]*' -StopAtAnyHeading
    if ($sec.count -lt 1) {
        $res.problems += "section 2's heading ('## 2. ...') was not found, so no table could be anchored to it - a phase row is only a phase row where section 2 says it is"
        return $res
    }
    if ($sec.count -gt 1) {
        $res.problems += ("section 2's heading appears {0} times - which one owns the phase table is ambiguous, so this REFUSES rather than picking one" -f $sec.count)
        return $res
    }
    $res.anchored = $true

    $rows = @()
    foreach ($line in ($sec.text -split "`n")) {
        $l = $line.TrimEnd("`r").Trim()
        if ($l -notmatch '^\|') { continue }
        $rows += $l
    }
    if ($rows.Count -lt 1) { $res.problems += "section 2 contains no table rows at all"; return $res }

    # --- THE HEADER ROW NAMES THE COLUMNS ----------------------------------------
    $hIdx = -1
    for ($i = 0; $i -lt $rows.Count; $i++) {
        $hc = @(Split-TableRow -Row $rows[$i])
        if (@($hc | Where-Object { $_ -match '(?i)validated' }).Count -ge 1) { $hIdx = $i; break }
    }
    if ($hIdx -lt 0) {
        $res.problems += "section 2's table has no header row naming a 'Validated by' column, so its cells could only be read POSITIONALLY - refused"
        return $res
    }
    $hcells = @(Split-TableRow -Row $rows[$hIdx])
    $res.header = ($hcells -join " | ")
    $col = @{ phase = @(); what = @(); validated = @(); depends = @() }
    for ($i = 0; $i -lt $hcells.Count; $i++) {
        $h = (($hcells[$i] -replace '[^A-Za-z]', ' ') -replace '\s+', ' ').Trim().ToLowerInvariant()
        if     ($h -eq 'validated' -or $h -eq 'validated by') { $col.validated += $i }
        elseif ($h -eq 'phase')   { $col.phase   += $i }
        elseif ($h -eq 'what')    { $col.what    += $i }
        elseif ($h -eq 'depends' -or $h -eq 'depends on') { $col.depends += $i }
    }
    foreach ($need in @("phase", "validated")) {
        $n = @($col[$need]).Count
        if ($n -ne 1) {
            $res.problems += ("section 2's header names the '{0}' column {1} time(s) (header: {2}) - a cell is read by NAME here, and {3}, so this REFUSES" -f `
                              $need, $n, $res.header, $(if ($n -lt 1) { "there is no such column to read" } else { "which one is meant is ambiguous" }))
        }
    }
    if ($res.problems.Count -gt 0) { return $res }
    $pi = [int]$col.phase[0]
    $vi = [int]$col.validated[0]
    $wi = $(if (@($col.what).Count -eq 1) { [int]$col.what[0] } else { -1 })
    $di = $(if (@($col.depends).Count -eq 1) { [int]$col.depends[0] } else { -1 })

    # --- ROWS BEFORE THE HEADER ARE NOT ROWS OF THIS TABLE ------------------------
    # This one stays a PREFIX match on purpose. It is an ALARM, not a reader: anything
    # that even looks like a phase row sitting above the header is a question about which
    # table it belongs to, and narrowing it to exact ids would silence that question.
    for ($i = 0; $i -lt $hIdx; $i++) {
        $pc = @(Split-TableRow -Row $rows[$i])
        # AND THE ALARM MAY NOT END IN `\b` EITHER - ROUND 2, ITEM 2. `\b` does not fire
        # between a digit and a following word character, so a stray `**U4b**` row sitting
        # above the header did not trip this alarm at all: the WIDEST net in the parser had a
        # hole shaped exactly like the one the id-cell recogniser had. Bare prefix now, and
        # the WHOLE cell is quoted so a reader sees what was found, not a truncated id.
        if (@($pc).Count -ge 1 -and $pc[0] -match '^\s*(?:\*\*|__)?\s*(U\d)') {
            $res.problems += ("a row whose first cell is '{0}' - it begins with {1} - appears BEFORE section 2's table header - it is not a row of the table whose columns this parser read, so this REFUSES rather than reading it positionally" -f $pc[0].Trim(), $Matches[1])
        }
    }
    if ($res.problems.Count -gt 0) { return $res }

    # --- THE DATA ROWS ------------------------------------------------------------
    $seen = @{}
    for ($i = $hIdx + 1; $i -lt $rows.Count; $i++) {
        $cells = @(Split-TableRow -Row $rows[$i])
        if (@($cells).Count -lt 1) { continue }
        # the delimiter row |---|---| is not data
        if (@($cells | Where-Object { $_ -match '^:?-{2,}:?$' }).Count -eq @($cells).Count) { continue }
        if (@($cells).Count -le $pi) { continue }
        # THE ID CELL IS MATCHED WHOLE, NOT BY PREFIX - section 2.1 amendment A3.
        # Reading the id off the FRONT of the cell made every row that merely STARTS with
        # a phase id a row FOR that phase. At revision 2151193 section 2 carried
        # `| **U4** |` AND `| **U4 status (2026-08-30)** |` - a status annotation that was
        # never a phase row - so the prefix read saw TWO U4 rows, the duplicate guard
        # refused, and U4's chain was unreconstructable across that revision. A3 disposes
        # of it: the authoritative row is the one whose id cell is EXACTLY the id; a row
        # that only LOOKS like one is ignored; and a genuine duplicate - the same exact id
        # twice - must still REFUSE. Both halves are drilled (verify-dfu-done.ps1 P4/P4b).
        #
        # WHAT "EXACTLY" ADMITS, AND WHY IT IS NOT A LOOPHOLE. Emphasis is formatting, not
        # identity, so `**U1**`, `__U1__` and `U1` are one cell shape - rule 6: unbolding a
        # row must not drop the phase. A single PARENTHESISED QUALIFIER is part of the id
        # cell's own name: the live table writes U7 as `**U7 (standing)**`, and refusing
        # that shape would delete U7 from clauses 2 and 7's population - rule 6's failure
        # wearing the opposite face, and already drilled at N5. Anything ELSE trailing the
        # id - a word, a date, a status - means this is not the row that defines the phase.
        if ($cells[$pi] -notmatch '^(?:\*\*|__)?\s*(U\d)(?:\s*\([^()]*\))?\s*(?:\*\*|__)?$') {
            # AND NOT SILENTLY. A row this parser declined to read is RECORDED and printed
            # on the phase-table-unambiguous probe, so "ignored" can never be read as
            # "there was nothing there" - the very class this file exists to prevent.
            #
            # THE RECOGNISER MAY NOT END IN `\b` - ROUND 2, ITEM 2. `\b` does not fire between
            # a digit and a following word character, so `**U4b**` and `**U40**` matched NEITHER
            # the id shape above NOR this one: they were not read as phase rows and were not
            # listed as ignored either. They vanished from the output entirely - a silent drop
            # wearing the costume of a safe ignore, in the very branch whose comment claims
            # nothing here is silent. Verified on a 14-row fixture that reported "10 phase rows,
            # 2 ignored" with those two cells printed nowhere. So the recogniser is the BARE
            # PREFIX `U<digit>`: a cell that starts like a phase id is either a ROW or a NAMED
            # IGNORE, never neither. Drilled at verify-dfu-done.ps1 step R3.
            if ($cells[$pi] -match '^\s*(?:\*\*|__)?\s*U\d') { $res.ignored += $cells[$pi] }
            continue
        }
        $id = $Matches[1]
        # AN ADMITTED QUALIFIER IS NAMED - ROUND 2, ITEM 3. The parenthesised allowance above
        # is REQUIRED (the live table writes `**U7 (standing)**`, and refusing that shape
        # deletes U7 from clauses 2 and 7's population) and it is also THIN:
        # `**U4 status (2026-08-30)**` is caught by A3 and `**U4 (status 2026-08-30)**` is
        # ADMITTED - one space apart, for a rule whose whole point is that status must not
        # live in the anchor. Constructed and run: with that cell as the only U4 row, both
        # phase-table-unambiguous and phase-floor-present PASS. This script cannot narrow the
        # allowance without deleting U7, and it may not edit PLAN.md - so the allowance is made
        # VISIBLE rather than quiet: every accepted qualifier is recorded with its phase and
        # printed on phase-table-unambiguous, so an id cell carrying STATUS is NAMED in the
        # output even though it was admitted. The residual is stated in the artifact.
        if ($cells[$pi] -match '\(') { $res.qualified += ("{0}: {1}" -f $id, $cells[$pi].Trim()) }
        if ($seen.ContainsKey($id)) {
            $res.problems += ("{0} has {1} rows in section 2's table - a duplicated id is a question about which row defines the phase, and LAST-WINS answers it by accident. REFUSED." -f $id, ($seen[$id] + 1))
            $seen[$id] = $seen[$id] + 1
            continue
        }
        $seen[$id] = 1
        if (@($cells).Count -le $vi) {
            $res.problems += ("{0}'s row has {1} cell(s) but the header declares {2} - the Validated-by column is not present in this row, so it could not be read by name" -f $id, @($cells).Count, $hcells.Count)
            continue
        }
        $res.phases[$id] = [ordered]@{
            what      = $(if ($wi -ge 0 -and @($cells).Count -gt $wi) { $cells[$wi] } else { "" })
            validated = $cells[$vi]
            depends   = $(if ($di -ge 0 -and @($cells).Count -gt $di) { $cells[$di] } else { "" })
        }
    }
    # A duplicated id must not leave a WINNER behind: whichever row was read first is as
    # arbitrary as the last one, so the phase is dropped from the result entirely and the
    # PROBLEM is what the caller sees.
    foreach ($k in @($seen.Keys)) {
        if ([int]$seen[$k] -gt 1 -and $res.phases.Contains($k)) { $res.phases.Remove($k) }
    }
    return $res
}

# REMOVED 2026-08-31 (round 2, item 1): `Get-PhaseTable`, a five-line wrapper that returned
# `(Get-PhaseTableParse -Text $Text).phases` and DISCARDED both `problems` and `ignored`.
# It had ZERO callers repo-wide - grep for it and only its own definition came back - and
# it was precisely the "third reader added later" this round is about: a convenience door
# into the parser that drops the refusal and the declined rows on the way through, sitting
# there for the next author to reach for. Nothing replaces it: every reader calls
# `Get-PhaseTableParse` and hands the result to a surfacer, and verify-dfu-done.ps1 step R1
# greps this file to prove that stays true.

# ---------------------------------------------------------------------------------
# THE PHASE FLOOR. C.8.1 names U0-U6 LITERALLY, so the population of clauses 1, 2 and 7 is
# not "whatever section 2's table happens to contain today".
#
# WHY IT EXISTS - THE CHECKER WAS DERIVING ITS POPULATION FROM THE DOCUMENT UNDER TEST.
# Each of those clauses took its subjects from the CURRENT PLAN.md, so a phase could delete
# itself out of its own population: remove U1's row and clause 2 reported "met, coverage
# 1/1", with no probe, no not_evaluated entry and U1's chain never reconstructed. Merely
# REMOVING THE BOLD did the same while the row stayed visible in the document. Coverage
# still read "N of N" because N had shrunk - which is the failure this script exists to
# prevent, operating on the script.
#
# The fix is the guard clause 3 already carries for its door set (`door-set-matches-plan`):
# a PINNED floor, CHECKED BACK against the plan's own words. A phase in the floor and
# missing from the table is a FAILURE, never a smaller population; and the floor itself is
# compared with the range C.8.1 writes, so a plan that stops naming U0-U6 turns this red
# instead of drifting.
#
# U8 IS IN THE FLOOR AND C.8 CLAUSE 1 DOES NOT NAME IT YET. Section C.9 (2026-08-31)
# added phase U8, and section 2's U8 row states its own validation as "`dfu-done.ps1`'s
# pinned phase floor + clause 1 EXTENDED to include U8". C.8 clause 1's PROSE still reads
# "For U0-U6", so `phase-floor-matches-plan` now FAILS, naming U8 as pinned-but-unnamed.
# That red is the drift check WORKING: the plan says two different things about clause
# 1's population, and the honest response is to report it, not to pick the smaller
# answer. Closing it is a PLAN.md edit - C.8 clause 1 naming U8, which section 2 already
# demands - and it belongs to whoever owns that section. Narrowing this check to "pinned
# is a superset of named" would clear the red and would also re-open the exact hole the
# check exists to close: a plan that stopped naming U5 would then pass.
# ---------------------------------------------------------------------------------
$script:DfuPhaseFloor = @("U0", "U1", "U2", "U3", "U4", "U5", "U6", "U8")

function Get-ShortRef {
    # A chain step's ref, printable. Not every step is a 40-character sha - the working
    # tree is a step too - and Substring(0,7) on a short one throws.
    param([string]$Sha)
    if (-not $Sha) { return "-------" }
    if ($Sha.Length -le 7) { return $Sha }
    return $Sha.Substring(0, 7)
}

function Get-PlanPhaseFloor {
    # The phase ids C.8 clause 1 ITSELF names, ranges expanded. $null when that clause's
    # text could not be located UNAMBIGUOUSLY - "could not check", never "fine".
    #
    # IT IS ANCHORED TO SECTION C.8 NOW. The previous version ran a lazy first-match regex
    # over the ENTIRE plan, so a decoy passage anywhere earlier in PLAN.md - a quotation of
    # the clause, an example, a superseded draft - became the text this floor was checked
    # back against, and the drift check that exists to stop the plan drifting away from
    # this script would have been comparing against the wrong paragraph. So: locate the
    # C.8 SECTION (exactly one heading, terminated at the next heading of the same or
    # higher level), then locate clause 1 INSIDE it, and refuse if either is ambiguous.
    param([string]$PlanText)
    if (-not $PlanText) { return $null }
    $sec = Get-DfuSection -Text $PlanText -HeadingPattern '(?m)^###\s+C\.8\b[^\r\n]*'
    if ($sec.count -ne 1) { return $null }
    $ms = @([regex]::Matches($sec.text, '(?sm)^\s*1\.\s\*\*Every U-phase column.*?(?=^\s*2\.\s\*\*No phase is parked)'))
    if ($ms.Count -ne 1) { return $null }
    $clause = $ms[0].Value
    $set = @()
    # A RANGE IS A SET. "U0-U6" must EXPAND, or a floor derived from the plan would shrink
    # to its two endpoints and the middle phases would vanish from the comparison.
    foreach ($r in [regex]::Matches($clause, 'U(\d)\s*[^A-Za-z0-9\s]\s*U(\d)')) {
        $a = [int]$r.Groups[1].Value; $b = [int]$r.Groups[2].Value
        if ($b -ge $a) { for ($i = $a; $i -le $b; $i++) { $set += ("U{0}" -f $i) } }
    }
    foreach ($r in [regex]::Matches($clause, '(?<![A-Za-z0-9])U(\d)(?![0-9])')) { $set += ("U" + $r.Groups[1].Value) }
    return @($set | Sort-Object -Unique)
}

function Add-PhaseFloorProbes {
    # Adds the probes every phase-derived clause carries and returns the SUBJECT SET:
    #   phase-table-unambiguous  - section 2's table parsed to ONE unambiguous answer
    #                              (only when the caller passes the parse it used)
    #   phase-floor-matches-plan - the pinned floor still equals the phase set C.8.1 names
    #   phase-floor-present      - every floor phase still HAS a row where it must
    # The returned ids are the floor UNIONED with the parsed set, so a phase can be added
    # to this script's population but never subtracted from it by editing the document.
    param($Clause, $Ctx, [string]$PlanText, $Phases, [string]$Restrict = "", $Parse = $null,
          [string]$Where = "section 2's table")
    $floor = @($script:DfuPhaseFloor)
    if ($Restrict) { $floor = @($floor | Where-Object { $_ -match $Restrict }) }
    $parsed = @($Phases.Keys)
    if ($Restrict) { $parsed = @($parsed | Where-Object { $_ -match $Restrict }) }

    # --- THE PARSE ITSELF IS A SUBJECT -------------------------------------------
    # A parser that REFUSED - no section 2 heading, two of them, a duplicated phase id, a
    # header with two Validated-by columns - has not produced a smaller table, it has
    # produced no answer. Reporting that refusal as a clean population is the same defect
    # as a shrunken N, one layer down.
    if ($null -ne $Parse) {
        $probs = @($Parse.problems)
        # A row the parser DECLINED to read as a phase is named here (A3). Ignoring it is
        # correct; ignoring it SILENTLY would be a population that shrank without saying so.
        $ign = ""
        if (@($Parse.ignored).Count -gt 0) {
            $ign = ("; {0} row(s) begin with a phase id but are NOT id cells, so they were NOT read as phase rows (A3): {1}" -f `
                    @($Parse.ignored).Count, ((@($Parse.ignored)) -join " / "))
        }
        # AND AN ADMITTED QUALIFIER IS NAMED TOO - ROUND 2, ITEM 3. A3's literal spec says
        # match the id cell EXACTLY; the parenthesised allowance widens that, and it must
        # widen it OUT LOUD. Reported on the SAME probe as the declined rows so the two
        # edges of the id-cell rule - what was refused, and what was let through - are read
        # in one place.
        $qual = ""
        if (@($Parse.qualified).Count -gt 0) {
            $qual = ("; {0} id cell(s) were ADMITTED carrying a parenthesised qualifier - the qualifier is treated as part of the id cell's own name, and it is named here so an id carrying STATUS is visible even when it was accepted: {1}" -f `
                     @($Parse.qualified).Count, ((@($Parse.qualified)) -join " / "))
        }
        if ($probs.Count -eq 0) {
            $b = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                 -Note ("{0} parsed to ONE unambiguous table: header [{1}], {2} phase row(s), no duplicate id, nothing read from a code fence or an HTML comment{3}{4}" -f `
                        $Where, $Parse.header, @($Parse.phases.Keys).Count, $ign, $qual)
        } else {
            $b = New-VerdictProbeBody -Verdict "fail" -Exit $probs.Count `
                 -Note ("{0} did not parse to one unambiguous table: {1}{2}{3}" -f $Where, (($probs) -join " || "), $ign, $qual)
        }
        $Clause.probes += (New-Probe -Name "phase-table-unambiguous" `
            -Command ("parse {0} in {1}: anchor on the section heading, strip fenced blocks and HTML comments, read the Validated-by column BY NAME from the header row, refuse on a duplicate id" -f $Where, $Ctx.plan) -Run $b)
    }

    $named = Get-PlanPhaseFloor -PlanText $PlanText
    if ($null -eq $named) {
        $Clause.probes += (New-Probe -Name "phase-floor-matches-plan" `
            -Command ("locate section C.8 in {0}, then clause 1 inside it" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "C.8 clause 1's text could not be located UNAMBIGUOUSLY inside section C.8, so the pinned phase floor could not be checked back against it"))
    } else {
        $pinnedNotNamed = @($script:DfuPhaseFloor | Where-Object { $named -notcontains $_ })
        $namedNotPinned = @($named | Where-Object { $script:DfuPhaseFloor -notcontains $_ })
        if ($pinnedNotNamed.Count -eq 0 -and $namedNotPinned.Count -eq 0) {
            $Clause.coverage.evaluated = [int]$Clause.coverage.evaluated + 1
            $b = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                 -Note ("the pinned floor {0} is exactly the phase set C.8 clause 1 names" -f ($script:DfuPhaseFloor -join ","))
        } else {
            $b = New-VerdictProbeBody -Verdict "fail" -Exit ($pinnedNotNamed.Count + $namedNotPinned.Count) `
                 -Note ("the pinned floor and C.8 clause 1 disagree - the plan names {0}; pinned but unnamed: {1}; named but unpinned: {2}" -f `
                        ($named -join ","), `
                        $(if ($pinnedNotNamed.Count) { $pinnedNotNamed -join "," } else { "none" }), `
                        $(if ($namedNotPinned.Count) { $namedNotPinned -join "," } else { "none" }))
        }
        $Clause.probes += (New-Probe -Name "phase-floor-matches-plan" `
            -Command ("extract the phase ids C.8 clause 1 names in {0} and compare them with the pinned floor" -f $Ctx.plan) -Run $b)
    }

    $missing = @($floor | Where-Object { $parsed -notcontains $_ })
    if ($missing.Count -eq 0) {
        $b = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
             -Note ("every floor phase ({0}) is present in {1}" -f ($floor -join ","), $Where)
    } else {
        $b = New-VerdictProbeBody -Verdict "fail" -Exit $missing.Count `
             -Note ("{0} floor phase(s) are NOT present in {1}: {2} - a phase does not leave this clause's population by leaving the document, and a row that merely lost its bold is still a row" -f `
                    $missing.Count, $Where, ($missing -join ","))
    }
    $Clause.probes += (New-Probe -Name "phase-floor-present" `
        -Command ("read {0} and compare it with the pinned floor {1}" -f $Where, ($script:DfuPhaseFloor -join ",")) -Run $b)

    return @{ ids = @(@($floor + $parsed) | Sort-Object -Unique); missing = @($missing) }
}

function Get-WalkthroughRuns {
    # Every command a "How to run:" marker names, keyed by the phase whose section it sits
    # in. DERIVED: the phase headings and the run lines both come from the file, so a phase
    # that grows a section, or loses one, changes this map without anyone editing a list.
    #
    # IT NORMALISES FIRST, AND THAT IS NOT COSMETIC - THIS FUNCTION EXECUTES WHAT IT
    # PARSES. It read the walkthrough RAW, so a `## U<n>` section inside an HTML comment
    # was parsed AND ITS COMMANDS WERE RUN under cmd.exe with the operator's privileges,
    # from a document the operator reviews by reading - where that section is invisible.
    # Remove-NonProse now runs on the way in, here and in every other markdown reader.
    #
    # IT MUST READ ACROSS LINE BREAKS. The walkthrough wraps long commands inside a single
    # backtick span, so a line-oriented parser silently captures the FIRST LINE ONLY and
    # then "runs" a truncated command - the alphabet-too-narrow class producing a confident
    # wrong answer rather than an error. The span is therefore matched whole, newlines
    # included, and re-flowed to one line.
    #
    # AND ONE MARKER MAY NAME SEVERAL COMMANDS. This used to take the FIRST backtick span
    # after each marker and stop. WALKTHROUGH.md's U6 row names TWO commands under one
    # marker - "`drill.py`, and `pytest ...`" - and the second was never run: a verifier
    # ran it by hand and it FAILED while clause 5 reported `walkthrough-U6-check-1 = pass`.
    #
    # A BLANK LINE IS NOT A TERMINATOR, and while it was one the sentence above was false.
    # The block ended at `(?m)^\s*$`, so a second command under the SAME marker separated by
    # a blank line was silently never executed - "EVERY COMMAND UNDER A MARKER RUNS" was a
    # claim the code did not implement, which is this file's own recurring defect wearing a
    # commit message. The block now ends at a STRUCTURE: the next bold label at the start of
    # a line, the next heading, the next table row, a horizontal rule, or the next marker.
    # Those are the boundaries a reader sees; a paragraph break inside one marker's block is
    # not. The direction of the error matters - stopping early SKIPS a named check and
    # reports full coverage (a silent false green), while running one span too many produces
    # a LOUD red the operator can read and fix, and it now runs inside a disposable clone.
    param([string]$Text)
    $out = [ordered]@{}
    if (-not $Text) { return $out }
    $clean = Remove-NonProse -Text $Text
    $parts = [regex]::Split($clean, '(?m)^(?=##\s)')
    foreach ($part in $parts) {
        if ($part -notmatch '(?m)^##\s+\**(U\d)') { continue }
        $id = $Matches[1]
        if (-not $out.Contains($id)) { $out[$id] = @() }
        foreach ($m in [regex]::Matches($part, '(?m)^\s*\*\*How to run:?\*\*')) {
            $from = $m.Index + $m.Length
            $rest = $part.Substring($from)
            $len  = $rest.Length
            foreach ($term in @('(?m)^\s*\*\*', '(?m)^\s*#{1,6}\s', '(?m)^\s*\|', '(?m)^\s*(-{3,}|\*{3,}|_{3,})\s*$')) {
                $t = [regex]::Match($rest, $term)
                if ($t.Success -and $t.Index -lt $len) { $len = $t.Index }
            }
            $block = $rest.Substring(0, $len)
            foreach ($b in [regex]::Matches($block, '(?s)`([^`]+)`')) {
                $cmd = ($b.Groups[1].Value -replace '\s+', ' ').Trim()
                if ($cmd) { $out[$id] = @($out[$id]) + @($cmd) }
            }
        }
    }
    return $out
}

function Get-WalkthroughSectionIds {
    # The phase ids WALKTHROUGH.md has a visible section for. Clause 5 split the raw text
    # itself, so five phase sections wrapped in a properly closed HTML comment gave it
    # "coverage 8 of 8, verdict met" over a document showing two - round 4's RESURRECTION
    # attack, one file over, against the clause whose own justification is "the operator
    # reviews by reading it". One reader, normalising, used by the clause.
    param([string]$Text)
    $ids = [ordered]@{}
    if (-not $Text) { return $ids }
    foreach ($part in [regex]::Split((Remove-NonProse -Text $Text), '(?m)^(?=##\s)')) {
        if ($part -match '(?m)^##\s+\**(U\d)') {
            $sid = $Matches[1]
            if (-not $ids.Contains($sid)) { $ids[$sid] = $true }
        }
    }
    return $ids
}

function Get-LedgerSections {
    # DECISIONS.md split into its `## ` entries, each with its heading, its BODY and its
    # position. The body matters because an un-parking is a directive INSIDE an entry, not
    # a word in a heading - see the un-parking rule in Test-Clause2.
    #
    # NORMALISED FIRST. Read raw, this function handed clause 2 and clause 4 entries that
    # NO READER SEES: a `## ` entry inside an HTML comment carrying `**Un-parks:** <heading>`
    # closed a PARKED entry, and a commented `## ... clause 4 exclusion` entry granted the
    # pod-key carve-out and took the unmerged branch count from 8 to 7. An exemption and a
    # discharge are exactly the two things that must never come from invisible text.
    param([string]$Text)
    $out = @()
    if (-not $Text) { return $out }
    $clean = Remove-NonProse -Text $Text
    $i = 0
    foreach ($part in [regex]::Split($clean, '(?m)^(?=##\s)')) {
        if ($part -notmatch '(?m)^##\s+(.*)$') { continue }
        $head = $Matches[1].Trim()
        $body = $part
        $nl = $part.IndexOf("`n")
        if ($nl -ge 0) { $body = $part.Substring($nl + 1) }
        $out += @{ heading = $head; body = $body; index = $i }
        $i++
    }
    return @($out)
}

function Get-BranchExclusionGrant {
    # DOES THE LEDGER GRANT THIS BRANCH A CARVE-OUT FROM C.8 CLAUSE 4? - and the answer is
    # a STRUCTURED RECORD, never a substring.
    #
    # THE DEFECT THIS REPLACES. The test was `$decForBranches.Contains($b)` - a raw
    # substring search of the whole of DECISIONS.md. ANY sentence anywhere containing the
    # branch name granted the exemption, INCLUDING one saying it must NOT be excused: a
    # verifier proved it by appending this effort's own findings note, whose text names the
    # branch while arguing against excusing it. An exemption read out of prose is granted
    # by whoever last mentioned the string.
    #
    # The grant now requires BOTH halves of a record: a `## ` entry whose heading is
    # recognisable as an exclusion record, AND an explicit directive line inside it naming
    # the branch:
    #
    #     ## <date> - clause 4 exclusion - work/example
    #     **Excluded from C.8 clause 4:** `work/example`
    #     **Why:** ...
    #
    # The directive must name the branch EXACTLY. Nothing else in the file grants anything.
    param([string]$DecisionsText, [string]$Branch)
    $res = @{ granted = $false; heading = ""; why = "" }
    if (-not $DecisionsText) { $res.why = "DECISIONS.md could not be read, so no carve-out could be granted"; return $res }
    $mentioned = $DecisionsText.Contains($Branch)
    foreach ($sec in @(Get-LedgerSections -Text $DecisionsText)) {
        if ($sec.heading -notmatch '(?i)clause\s*4\s*exclusion') { continue }
        # THE EMPHASIS IS FORMATTING, NOT SYNTAX. `**Excluded from C.8 clause 4:**` puts the
        # colon INSIDE the bold, so a pattern that expects `**...**:` reads nothing and the
        # grant silently never fires - the same alphabet-too-narrow shape this file keeps
        # finding. The asterisks and backticks come off the line first, then the directive
        # is matched on its words.
        $flat = ($sec.body -replace '\*', '') -replace '`', ''
        foreach ($m in [regex]::Matches($flat, '(?im)^\s*Excluded from C\.8 clause 4\s*:\s*([^\r\n]+?)\s*$')) {
            if ($m.Groups[1].Value.Trim() -eq $Branch) {
                $res.granted = $true
                $res.heading = $sec.heading
                return $res
            }
        }
    }
    if ($mentioned) {
        $res.why = "the branch name appears in DECISIONS.md, but no '## ... clause 4 exclusion' entry carries an 'Excluded from C.8 clause 4:' directive naming it - a mention is not a grant"
    } else {
        $res.why = "no '## ... clause 4 exclusion' entry in DECISIONS.md carries an 'Excluded from C.8 clause 4:' directive naming it"
    }
    return $res
}

function Get-WorkBranches {
    param($Ctx)
    $g = Invoke-Git -Arguments @("for-each-ref", "--format=%(refname:short)", "refs/heads/work/") -WorkDir $Ctx.root
    if ($g.exit -ne 0) { return $null }
    return @(($g.stdout -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function Get-Worktrees {
    param($Ctx)
    $g = Invoke-Git -Arguments @("worktree", "list", "--porcelain") -WorkDir $Ctx.root
    if ($g.exit -ne 0) { return $null }
    $out = @()
    foreach ($line in ($g.stdout -split "`n")) {
        $l = $line.Trim()
        if ($l -match '^worktree\s+(.+)$') { $out += $Matches[1].Trim() }
    }
    return @($out)
}

function Invoke-Psql {
    # One query, one exit code. Returns @{ exit; out; ran }.
    param($Ctx, [string]$Sql)
    if ($Ctx.skiplive) { return @{ exit = $null; out = ""; ran = $false; why = "-SkipLive" } }
    $r = Invoke-Native -Exe "docker" -Arguments @("exec", $Ctx.db, "psql", "-U", "postgres", "-d", $Ctx.dbname, "-tAc", $Sql)
    return @{ exit = $r.exit; out = ($r.stdout + $r.stderr); ran = $r.ran; command = $r.command }
}

function Invoke-Curl {
    # HTTP from inside the Open Brain network - the doors this plan cares about have no
    # host binding, so a probe from the host would test nothing and report 000.
    #
    # IT READS THE STATUS CODE NOW, AND THAT IS THE POINT. The previous version asked curl
    # for %{http_code} and then never looked at it: the only tests were curl's exit code
    # and whether the marker appeared in the body. PostgREST answers a missing table with
    # 404 and a JSON error body, and curl still exits 0 - so pointing this at
    # `openbrain-postgrest:3000/nosuch` made all five PostgREST doors report "attacked with
    # the fixture and it did not come back", INCLUDING the door that correctly fails
    # against the real endpoint. A renamed table, a schema change, or auth being switched
    # on would have turned this clause green in exactly the same way.
    #
    # -g (globoff) is required, not cosmetic: PostgREST filters carry * ( ) [ ] and curl
    # parses those as URL globs, rejecting the URL as "malformed input to a URL function"
    # - a refusal that arrives as exit 3 with no request ever sent.
    #
    # Several URLs may be passed; curl walks them in order and this returns one result per
    # URL in that order, each with its own status and body.
    param($Ctx, [string[]]$Url)
    $urls = @($Url | Where-Object { $_ })
    if ($Ctx.skiplive) { return @{ exit = $null; ran = $false; why = "-SkipLive"; results = @(); command = "(not run: -SkipLive)"; stderr = "" } }
    if ($urls.Count -lt 1) { return @{ exit = $null; ran = $false; why = "no url"; results = @(); command = "(no url)"; stderr = "" } }
    $argv = @("run", "--rm", "--network", $Ctx.obnet, "curlimages/curl:latest",
              "-sS", "-g", "-w", "\n#DFU %{http_code}\n") + $urls
    $r = Invoke-Native -Exe "docker" -Arguments $argv
    $results = @()
    $body = @()
    foreach ($line in (($r.stdout) -split "`n")) {
        $l = $line.TrimEnd("`r")
        if ($l -match '^#DFU\s+(\d+)\s*$') {
            $results += @{ status = [int]$Matches[1]; body = ($body -join "`n") }
            $body = @()
            continue
        }
        $body += $l
    }
    return @{
        exit = $r.exit; ran = $r.ran; command = $r.command; stderr = $r.stderr
        results = @($results); urls = @($urls)
    }
}

function Invoke-CurlMany {
    # Invoke-Curl, but CHUNKED. The surface sweep now carries every text column and every
    # jsonb key of every exposed table, and one docker argv holding all of that runs past
    # the operating system's command-line limit - at which point the process does not
    # start, curl reports nothing, and a sweep that measured NOTHING is indistinguishable
    # from a clean surface. Results stay in URL order across chunks, and a chunk that could
    # not run contributes status-0 placeholders, so the caller counts those URLs as unread
    # instead of silently shifting every later answer onto the wrong URL.
    param($Ctx, [string[]]$Url, [int]$MaxChars = 6000)
    $urls = @($Url | Where-Object { $_ })
    if ($Ctx.skiplive) { return @{ exit = $null; ran = $false; results = @(); command = "(not run: -SkipLive)"; stderr = ""; urls = $urls } }
    if ($urls.Count -lt 1) { return @{ exit = $null; ran = $false; results = @(); command = "(no url)"; stderr = ""; urls = $urls } }
    $out    = @()
    $cmds   = @()
    $errs   = @()
    $anyRan = $false
    $batch  = @()
    $len    = 0
    foreach ($u in $urls) {
        if ($batch.Count -ge 1 -and ($len + $u.Length) -gt $MaxChars) {
            $r = Invoke-Curl -Ctx $Ctx -Url $batch
            $cmds += $r.command
            if ($r.stderr) { $errs += $r.stderr }
            if ($r.ran) { $anyRan = $true }
            $got = @($r.results)
            for ($i = 0; $i -lt $batch.Count; $i++) {
                if ($r.ran -and $i -lt $got.Count) { $out += $got[$i] } else { $out += @{ status = 0; body = "" } }
            }
            $batch = @()
            $len = 0
        }
        $batch += $u
        $len += ($u.Length + 1)
    }
    if ($batch.Count -ge 1) {
        $r = Invoke-Curl -Ctx $Ctx -Url $batch
        $cmds += $r.command
        if ($r.stderr) { $errs += $r.stderr }
        if ($r.ran) { $anyRan = $true }
        $got = @($r.results)
        for ($i = 0; $i -lt $batch.Count; $i++) {
            if ($r.ran -and $i -lt $got.Count) { $out += $got[$i] } else { $out += @{ status = 0; body = "" } }
        }
    }
    return @{ exit = 0; ran = $anyRan; results = @($out); urls = $urls
              command = (("{0} chunk(s), first: " -f $cmds.Count) + [string]($cmds | Select-Object -First 1)); stderr = ($errs -join [string][char]10) }
}

function Get-CurlResult {
    # ONE URL'S ANSWER, BY POSITION. curl walks the URLs in the order given, so the nth
    # #DFU line belongs to the nth URL. A missing nth result means that transfer never
    # completed - reported as unreachable, never as an empty (and therefore clean) body.
    param($Res, [int]$Index)
    if ($null -eq $Res -or -not $Res.ran) { return @{ reachable = $false; status = 0; body = "" } }
    $rs = @($Res.results)
    if ($Index -ge $rs.Count) { return @{ reachable = $false; status = 0; body = "" } }
    return @{ reachable = $true; status = [int]$rs[$Index].status; body = [string]$rs[$Index].body }
}

function Resolve-DoorVerdict {
    # THE RULE FOR EVERY NEGATIVE PROBE IN THIS FILE, and the reason clause 3 previously
    # could not fail at three of its doors.
    #
    # "I attacked the door and the personal fixture did not come back" is not a
    # measurement. It is what a door says when it is bound, and ALSO what it says when the
    # table was renamed, the filter matched nothing, the window of rows returned happened
    # not to contain the fixture, or the row was never written there in the first place.
    # Three doors could not have failed under ANY boundary state for exactly that reason.
    #
    # So EVERY negative probe carries a POSITIVE CONTROL: an ops-labelled twin of the
    # fixture, written the same way at the same moment, which the door MUST return. A door
    # passes only when it returns the ops twin AND refuses the personal one. A door that
    # returns NEITHER is INDETERMINATE - that is the only way to tell "bound" from
    # "broken", and it is the same distinction C.8 draws between clear because we looked
    # and clear because we did not.
    param(
        [bool]$Reachable, [int]$Status, [bool]$SawPersonal, [bool]$SawOps,
        [string]$Leak = "the personal fixture", [string]$Control = "its ops-labelled twin"
    )
    if (-not $Reachable) {
        return @{ verdict = "indeterminate"; note = "the door could not be reached, so it was NOT proven closed" }
    }
    if ($Status -ne 0 -and $Status -ne 200) {
        return @{ verdict = "indeterminate"
                  note = ("the door answered HTTP {0}, so nothing was measured - a non-200 body cannot show a leak, and the absence of one in it is not containment" -f $Status) }
    }
    if ($SawPersonal) {
        return @{ verdict = "fail"; note = ("the door RETURNED {0}" -f $Leak) }
    }
    if (-not $SawOps) {
        return @{ verdict = "indeterminate"
                  note = ("the door returned NEITHER {0} NOR {1} - with no positive control this probe cannot tell a bound door from a broken query, so it refuses" -f $Leak, $Control) }
    }
    return @{ verdict = "pass"
              note = ("the door returned {0} and REFUSED {1} - measured against a positive control, not merely silent" -f $Control, $Leak) }
}

# =================================================================================
# THE FIFTEENTH CLASS - A CHECKER THAT EXECUTES INSTRUCTIONS FROM THE ARTIFACT UNDER
# TEST, AND THEN MEASURES A WORLD THOSE INSTRUCTIONS CAN CHANGE.
#
# The fourteen classes on this file's record all constrain what the checker READS - where
# a row may live, which cell is authoritative, whether an absence was measured or assumed.
# Every one of them can be closed perfectly and leave this untouched, because this one is
# not about inputs. It is about EFFECTS.
#
# WHAT WAS DEMONSTRATED, EXACTLY. Clauses 1 and 5 execute every backtick span under a
# `**How to run:**` marker in WALKTHROUGH.md, under `cmd.exe /c`, with the operator's
# privileges - clause 5 with -WorkDir $Ctx.root, i.e. INSIDE THE AUDITED REPOSITORY - and
# the clauses evaluate in order 1..8. A `## U0` section whose marker read
# `cmd /c echo ## U0 findings > <repo>\documentation\notes\u0-findings.md` produced
# `walkthrough-U0-check-1 (exit 0) - the row's named check re-runs green`; the file did
# not exist before the run and did after; and CLAUSE 7 FOR U0 WENT FROM EXIT 3 TO EXIT 2,
# its findings-note artifact discharged by a file the run itself had created. The same
# channel reaches clause 4's subjects - worktrees, work/* branches, tree cleanliness -
# because clause 1 runs first and cmd.exe is not confined to the clean clone.
#
# THE FIX HAS THREE PARTS, and only the second one is load-bearing.
#
#   ISOLATION. Every walkthrough command runs in the CLEAN CLONE clause 1 already builds -
#     never in $Ctx.root - and the documents later clauses depend on are made read-only to
#     that process for the duration of the call (a Deny ACE for the running identity,
#     removed in a finally, and swept at startup so a killed run cannot leave the
#     operator's PLAN.md locked). The sandbox is reset between phases so one phase's
#     command cannot manufacture the artifact another phase's command needs.
#
#   EFFECT-NULLIFICATION, WHICH IS THE ACTUAL FIX. Isolation is a wall, and a wall can be
#     walked around - an absolute path, a `git -C`, a network push. So EVERY artifact any
#     clause depends on is SNAPSHOTTED BEFORE THE FIRST COMMAND RUNS - the three documents,
#     the findings notes, the commit log, the branch list, the worktree list, the working
#     tree's cleanliness, the submodule states, PLAN.md's whole revision history - and
#     every clause decides over the snapshot. Nothing created during the run can discharge
#     anything, because nothing created during the run is ever read. That is why this is
#     not another filter: it does not have to enumerate the ways a command reaches the tree.
#
#   DISCLOSURE. The command set the authority executed is RECORDED and PRINTED - what ran,
#     where, and what it returned - and a fingerprint of the audited artifacts is compared
#     before and after every command AND once at the end of the run. A command that moves
#     the audited tree turns its own probe RED and the run's integrity record false; a run
#     whose integrity record is false can never be `done`, whatever the clauses said.
#
# WHAT THIS DOES NOT CLAIM. The Deny ACE covers the documents and the notes directory, not
# `.git` - denying writes there would break the very git commands clause 4 runs. Git state
# is therefore protected by the snapshot and the fingerprint, not by the filesystem; and a
# command that pushes to a FOREIGN remote (OB1's, say) is outside both - `ls-remote` is
# snapshotted before any command runs, which closes the READ, but this file cannot un-push.
# That limit is stated here rather than papered over.
# =================================================================================

$script:DfuSnap      = $null
$script:DfuExecLog   = @()
$script:DfuSandbox   = $null
$script:DfuIntegrity = @()

function Get-DfuHash {
    param([string]$Text)
    if ($null -eq $Text) { return "<absent>" }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $b = [System.Text.Encoding]::UTF8.GetBytes($Text)
        return ([System.BitConverter]::ToString($sha.ComputeHash($b)) -replace '-', '').Substring(0, 16).ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Get-DfuProtectedDirs {
    # The directories holding the artifacts a later clause reads: the plan's own directory
    # and documentation/notes. Derived from the context, so a drill fixture protects ITS
    # documents and never the operator's.
    param($Ctx)
    $out = @()
    foreach ($f in @($Ctx.plan, $Ctx.decisions, $Ctx.walkthrough)) {
        if (-not $f) { continue }
        $d = Split-Path -Path $f -Parent
        if ($d -and (Test-Path -LiteralPath $d)) { $out += (Resolve-Path -LiteralPath $d).Path }
    }
    if ($Ctx.notes -and (Test-Path -LiteralPath $Ctx.notes)) { $out += (Resolve-Path -LiteralPath $Ctx.notes).Path }
    return @($out | Sort-Object -Unique)
}

function Get-AuditedFingerprint {
    # WHAT THE AUDITED TREE LOOKS LIKE RIGHT NOW, over exactly the artifacts a clause can be
    # discharged by. Compared before/after every executed command and once at the end of the
    # run, so an effect the isolation missed is still REPORTED rather than absorbed.
    param($Ctx)
    $fp = [ordered]@{}
    foreach ($k in @("plan", "decisions", "walkthrough")) {
        $fp[("doc:" + $k)] = (Get-DfuHash -Text (Read-TextFile -Path $Ctx[$k]))
    }
    $notes = @()
    if ($Ctx.notes -and (Test-Path -LiteralPath $Ctx.notes)) {
        foreach ($f in (Get-ChildItem -LiteralPath $Ctx.notes -Filter "*.md" -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
            $notes += ("{0}:{1}" -f $f.Name, $f.Length)
        }
    }
    $fp["notes"] = (Get-DfuHash -Text ($notes -join "|"))
    $probes = @(
        @("git:refs",      @("for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags")),
        @("git:status",    @("status", "--porcelain")),
        @("git:worktrees", @("worktree", "list", "--porcelain")),
        @("git:submodule", @("submodule", "status", "--recursive")))
    foreach ($pair in $probes) {
        $g = Invoke-Git -Arguments @($pair[1]) -WorkDir $Ctx.root
        $fp[$pair[0]] = (Get-DfuHash -Text (("{0}|{1}" -f $g.exit, $g.stdout)))
    }
    return $fp
}

function Compare-DfuFingerprint {
    # The NAMES of the artifacts that moved. Never a boolean: "the audited tree changed" and
    # "documentation/notes changed" are different facts, and the operator needs the second.
    param($Before, $After)
    $moved = @()
    foreach ($k in @($Before.Keys)) {
        $b = [string]$Before[$k]
        $a = $(if ($After.Contains($k)) { [string]$After[$k] } else { "<gone>" })
        if ($a -ne $b) { $moved += $k }
    }
    foreach ($k in @($After.Keys)) { if (-not $Before.Contains($k)) { $moved += ("new:" + $k) } }
    return @($moved | Sort-Object -Unique)
}

function New-DfuSnapshot {
    # EVERY ARTIFACT ANY CLAUSE READS, TAKEN ONCE, BEFORE THE FIRST COMMAND RUNS.
    # This is the whole fix for class fifteen: a clause cannot be discharged by something
    # the run created, because a clause never reads anything the run could have created.
    param($Ctx)
    $snap = [ordered]@{
        taken_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
        docs = [ordered]@{}; notes = @(); git = [ordered]@{}
    }
    foreach ($k in @("plan", "decisions", "walkthrough")) {
        $raw = Read-TextFile -Path $Ctx[$k]
        $snap.docs[$k] = [ordered]@{
            path    = [string]$Ctx[$k]
            raw     = $raw
            md      = $(if ($null -eq $raw) { $null } else { (Remove-NonProse -Text $raw) })
            defects = @(Get-MarkdownDefects -Text $raw)
        }
    }
    if ($Ctx.notes -and (Test-Path -LiteralPath $Ctx.notes)) {
        foreach ($f in (Get-ChildItem -LiteralPath $Ctx.notes -Filter "*.md" -File -ErrorAction SilentlyContinue | Sort-Object Name)) {
            $raw = Read-TextFile -Path $f.FullName
            $snap.notes += @{ name = $f.Name; base = $f.BaseName; full = $f.FullName
                              raw = $raw; md = $(if ($null -eq $raw) { "" } else { (Remove-NonProse -Text $raw) }) }
        }
    }
    $gs = Invoke-Git -Arguments @("status", "--porcelain") -WorkDir $Ctx.root
    $snap.git["status"]      = $gs.stdout
    $snap.git["status_exit"] = $gs.exit
    $gm = Invoke-Git -Arguments @("submodule", "status", "--recursive") -WorkDir $Ctx.root
    $snap.git["submodule"]      = $gm.stdout
    $snap.git["submodule_exit"] = $gm.exit
    $snap.git["worktrees"] = (Get-Worktrees -Ctx $Ctx)
    $snap.git["branches"]  = (Get-WorkBranches -Ctx $Ctx)

    # The OB1 gitlink AND the remote's advertised refs, both before any command runs.
    $gl = Invoke-Git -Arguments @("ls-tree", $Ctx.workline, "OB1") -WorkDir $Ctx.root
    $snap.git["gitlink_exit"] = $gl.exit
    $snap.git["gitlink"] = ""
    if ($gl.exit -eq 0) {
        $m = [regex]::Match($gl.stdout, 'commit\s+([0-9a-f]{40})')
        if ($m.Success) { $snap.git["gitlink"] = $m.Groups[1].Value }
    }
    $snap.git["lsremote"] = $null
    $snap.git["lsremote_exit"] = $null
    $ob1 = Join-Path $Ctx.root "OB1"
    if (Test-Path -LiteralPath $ob1) {
        $lr = Invoke-Git -Arguments @("ls-remote", "origin") -WorkDir $ob1
        $snap.git["lsremote"] = $lr.stdout
        $snap.git["lsremote_exit"] = $lr.exit
    }

    # THE COMMIT LOG, parsed here so clause 7 cannot be handed a commit the run produced.
    $RS = [string][char]30
    $US = [string][char]31
    $commits = @()
    $glog = Invoke-Git -Arguments @("log", "--format=%x1e%H%x1f%s%x1f%b%x1f", "--name-only", $Ctx.workline) -WorkDir $Ctx.root
    $snap.git["log_ok"]   = ($glog.exit -eq 0)
    $snap.git["log_exit"] = $glog.exit
    $snap.git["log_err"]  = (($glog.stderr -replace '\s+', ' ').Trim())
    if ($glog.exit -eq 0) {
        foreach ($rec in ($glog.stdout -split $RS)) {
            if (-not $rec.Trim()) { continue }
            $f = $rec -split $US
            if ($f.Count -lt 2) { continue }
            $msg = [string]$f[1]
            if ($f.Count -gt 2) { $msg = $msg + "`n" + [string]$f[2] }
            $files = @()
            if ($f.Count -gt 3) { $files = @(([string]$f[3] -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ }) }
            $commits += @{ sha = ([string]$f[0]).Trim(); message = $msg; files = @($files) }
        }
    }
    $snap.git["log"] = @($commits)
    # PLAN.md's whole revision history, for clause 2's chain. A commit made DURING the run
    # would otherwise become a step of the chain the run is judging.
    $snap.git["revisions"] = (Get-PlanRevisions -Ctx $Ctx)

    $snap["fingerprint"] = (Get-AuditedFingerprint -Ctx $Ctx)
    return $snap
}

function Get-DfuSnapOrFail {
    # FAIL-CLOSED. A clause that ran without a snapshot would silently fall back to reading
    # the live tree, which is the defect. It throws instead, and the dispatcher's catch turns
    # that into clause-N-threw - indeterminate, never a pass.
    if ($null -eq $script:DfuSnap) { throw "no pre-execution snapshot was taken - this clause refuses to read the live tree" }
    return $script:DfuSnap
}
function Get-SnapDoc     { param([string]$Which) return (Get-DfuSnapOrFail).docs[$Which].raw }
function Get-SnapMd      { param([string]$Which) return (Get-DfuSnapOrFail).docs[$Which].md }
function Get-SnapDefects { param([string]$Which) return @((Get-DfuSnapOrFail).docs[$Which].defects) }
function Get-SnapNotes   { return @((Get-DfuSnapOrFail).notes) }
function Get-SnapGit     { param([string]$Key) $g = (Get-DfuSnapOrFail).git; if ($g.Contains($Key)) { return $g[$Key] } ; return $null }

function Add-MarkdownHygieneProbe {
    # A DOCUMENT THAT IS MALFORMED WHERE IT MATTERS IS A RED, NOT A SHORTER DOCUMENT.
    # Remove-NonProse discards everything after an unterminated `<!--` - the correct
    # direction, because text a renderer hides must not be read as structure - and that is
    # INDISTINGUISHABLE from a document which simply ends there. So the malformation is
    # stated, and the clause that reads the document goes red on it.
    param($Clause, $Ctx, [string]$Which)
    $defects = @(Get-SnapDefects -Which $Which)
    if ($defects.Count -eq 0) { return }
    $Clause.probes += (New-Probe -Name ("markdown-well-formed-{0}" -f $Which) `
        -Command ("scan {0} for an unterminated HTML comment or code fence" -f $Ctx[$Which]) `
        -Run (New-VerdictProbeBody -Verdict "fail" -Exit $defects.Count `
              -Note ("{0} is malformed and this reader FAILS CLOSED on it: {1}. Everything after the opener is discarded, so a row or a section hidden there is NOT read - and a document that hides structure from its own renderer is not one this authority will decide over." -f `
                     $Ctx[$Which], ($defects -join " ; "))))
}

# ---------------------------------------------------------------------------------
# THE SANDBOX, AND THE LOCK ON THE AUDITED ARTIFACTS.
# ---------------------------------------------------------------------------------

function Clear-DfuTreeProtection {
    # A KILLED RUN MUST NOT LEAVE THE OPERATOR'S PLAN.md READ-ONLY. Every explicit Deny ACE
    # for the running identity on the protected directories is removed at startup, before
    # anything else happens. It is idempotent, and it is the only reason applying an ACE at
    # all is a defensible thing for a checker to do.
    param($Ctx)
    $cleared = @()
    $me   = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $sid  = [string]$me.User.Value
    $name = [string]$me.Name
    foreach ($d in @(Get-DfuProtectedDirs -Ctx $Ctx)) {
        try {
            $acl = Get-Acl -LiteralPath $d
            $drop = @($acl.Access | Where-Object {
                $_.AccessControlType -eq [System.Security.AccessControl.AccessControlType]::Deny -and
                (-not $_.IsInherited) -and
                ([string]$_.IdentityReference.Value -eq $sid -or [string]$_.IdentityReference.Value -eq $name)
            })
            if ($drop.Count -lt 1) { continue }
            foreach ($r in $drop) { [void]$acl.RemoveAccessRuleSpecific($r) }
            Set-Acl -LiteralPath $d -AclObject $acl
            $cleared += $d
        } catch { }
    }
    return @($cleared)
}

function New-DfuDenyRule {
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
    $rights = [System.Security.AccessControl.FileSystemRights]"CreateFiles,AppendData,WriteData,WriteAttributes,WriteExtendedAttributes,Delete,DeleteSubdirectoriesAndFiles"
    return (New-Object System.Security.AccessControl.FileSystemAccessRule(
                $sid, $rights, "ContainerInherit,ObjectInherit", "None",
                [System.Security.AccessControl.AccessControlType]::Deny))
}

function Protect-AuditedArtifacts {
    # READ-ONLY TO THE PROCESS THIS AUTHORITY IS ABOUT TO START. A Deny ACE outranks every
    # Allow, so the child cmd.exe cannot write the plan, the ledger, the walkthrough or a
    # findings note however it addresses them - relative path, absolute path or otherwise.
    # Changing an ACL needs WRITE_DAC, which the owner keeps, so this stays removable.
    param($Ctx)
    $res = @{ applied = @(); failed = @() }
    foreach ($d in @(Get-DfuProtectedDirs -Ctx $Ctx)) {
        try {
            $acl = Get-Acl -LiteralPath $d
            $acl.AddAccessRule((New-DfuDenyRule))
            Set-Acl -LiteralPath $d -AclObject $acl
            $res.applied += $d
        } catch { $res.failed += ("{0}: {1}" -f $d, $_.Exception.Message) }
    }
    return $res
}

function Unprotect-AuditedArtifacts {
    param($Ctx, $Applied)
    foreach ($d in @($Applied)) {
        try {
            $acl = Get-Acl -LiteralPath $d
            [void]$acl.RemoveAccessRule((New-DfuDenyRule))
            Set-Acl -LiteralPath $d -AclObject $acl
        } catch { }
    }
}

function Get-DfuSandbox {
    # ONE clean clone of the work line, built BEFORE any command runs, and the only place a
    # walkthrough command is ever executed. Clause 5 used to run its commands with
    # -WorkDir $Ctx.root, which made the audited repository the command's working directory.
    param($Ctx)
    if ($null -ne $script:DfuSandbox) { return $script:DfuSandbox }
    $clean = New-CleanCheckout -Ctx $Ctx
    $script:DfuSandbox = @{
        ok      = ($clean.exit -eq 0)
        path    = $clean.path
        command = $clean.command
        why     = (($clean.err -replace "\s+", " ").Trim())
        submodule_exit = $clean.submodule_exit
        submodule_err  = $clean.submodule_err
    }
    return $script:DfuSandbox
}

function Reset-DfuSandbox {
    # BETWEEN PHASES the sandbox goes back to the work line's committed state. Otherwise one
    # phase's command can manufacture the artifact another phase's command needs - class
    # fifteen again, with the clone as its world instead of the repository. Submodule working
    # trees are left alone: `clean -fd` does not descend into them, and re-materialising OB1
    # between phases would cost minutes to prevent nothing.
    if ($null -eq $script:DfuSandbox) { return }
    if (-not $script:DfuSandbox.ok) { return }
    [void](Invoke-Git -Arguments @("reset", "--hard", "-q") -WorkDir $script:DfuSandbox.path)
    [void](Invoke-Git -Arguments @("clean", "-qfd") -WorkDir $script:DfuSandbox.path)
}

function Remove-DfuSandbox {
    param($Ctx)
    if ($null -eq $script:DfuSandbox) { return }
    Remove-CleanCheckout -Ctx $Ctx -Path $script:DfuSandbox.path
    $script:DfuSandbox = $null
}

function Invoke-AuditedCommand {
    # RUN A COMMAND THE DOCUMENT UNDER TEST NAMED - in the sandbox, with the audited
    # artifacts locked, and with whatever moved recorded either way. Returns
    # @{ result; drift; sandbox } and appends to the executed-command record the report
    # prints, because a reader must be able to see what the authority DID as well as what
    # it concluded.
    param($Ctx, [string]$Command, [string]$Clause, [string]$Phase)
    $sb = Get-DfuSandbox -Ctx $Ctx
    $entry = [ordered]@{ clause = $Clause; phase = $Phase; command = $Command
                         ran_in = ""; exit = $null; drift = @(); locked = @(); lock_failed = @() }
    if (-not $sb.ok) {
        $entry.ran_in = "(NOT RUN - no clean checkout could be built)"
        $script:DfuExecLog += $entry
        return @{ sandbox = $sb; drift = @()
                  result = @{ ran = $false; exit = $null; stdout = ""; stderr = $sb.why; command = $Command } }
    }
    $entry.ran_in = $sb.path
    $before = Get-AuditedFingerprint -Ctx $Ctx
    $prot   = Protect-AuditedArtifacts -Ctx $Ctx
    $entry.locked      = @($prot.applied)
    $entry.lock_failed = @($prot.failed)
    try {
        $r = Invoke-Native -Exe "cmd.exe" -Arguments @("/c", $Command) -WorkDir $sb.path
    } finally {
        Unprotect-AuditedArtifacts -Ctx $Ctx -Applied $prot.applied
    }
    $drift = @(Compare-DfuFingerprint -Before $before -After (Get-AuditedFingerprint -Ctx $Ctx))
    $entry.exit  = $r.exit
    $entry.drift = @($drift)
    $script:DfuExecLog += $entry
    if ($drift.Count -gt 0) {
        $script:DfuIntegrity += ("clause {0} / {1}: '{2}' MOVED the audited tree ({3})" -f $Clause, $Phase, $Command, ($drift -join ", "))
    }
    return @{ sandbox = $sb; drift = @($drift); result = $r }
}

function New-CommandProbeBody {
    # ONE PLACE TURNS AN EXECUTED COMMAND INTO A VERDICT, so clauses 1 and 5 cannot drift
    # apart on what "green" means - and CONTAMINATION OUTRANKS THE EXIT CODE. A command that
    # moved the audited tree has not passed, whatever it returned: the measurement it was
    # part of is no longer a measurement of the world the run started in.
    param($Run, [string]$GreenNote)
    $r = $Run.result
    if (@($Run.drift).Count -gt 0) {
        return (New-VerdictProbeBody -Verdict "fail" -Exit $r.exit `
                -Note ("this command CHANGED THE AUDITED TREE ({0}) - a checker that mutates what it measures is not an authority, so this is a failure whatever its exit code was ({1})" -f `
                       (@($Run.drift) -join ", "), $(if ($null -ne $r.exit) { $r.exit } else { "not started" })))
    }
    if (-not $r.ran -or $null -eq $r.exit) {
        return (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                -Note ("the command could not be started in the clean checkout: {0}" -f (($r.stderr -replace "\s+", " ").Trim())))
    }
    if ([int]$r.exit -eq 0) { return (New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note $GreenNote) }
    $tail = @(($r.stdout + "`n" + $r.stderr) -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
    return (New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) `
            -Note ("exited {0} in the clean checkout: {1}" -f $r.exit, (($tail -join " ") -replace "\s+", " ").Trim()))
}

# =================================================================================
# THE CLAUSES. One function each, each RUNNING something.
# =================================================================================

function New-CleanCheckout {
    # Clause 1 says "from a CLEAN CHECKOUT of the work line - not from a developer's
    # worktree, not from cached output". So the script makes one itself.
    #
    # IT IS A CLONE, NOT A WORKTREE, AND THAT IS A CORRECTNESS FIX RATHER THAN A
    # PREFERENCE. The previous version used `git worktree add`, which REGISTERS the
    # scratch checkout with the repository - so while clause 1 was running, clause 4's
    # `git worktree list` counted it and reported this script's own temporary directory as
    # unfinished work left in flight. A run of this script was manufacturing the defect it
    # exists to report, and a second concurrent run had its scratch counted too.
    #
    # Excluding it by name would have been the same defect with a filter in front of it:
    # any path that happened to match would vanish from clause 4, and any that did not
    # would still be counted. A clone is a SEPARATE REPOSITORY, so `git worktree list` in
    # the work repo cannot see it at all - the exclusion is by construction and there is
    # no list to get wrong.
    #
    # --shared keeps it cheap (objects are borrowed, not copied) and the checkout is
    # deleted at the end of the clause. core.longpaths is set explicitly because a fresh
    # clone does NOT inherit the work repo's config, and without it the checkout dies part
    # way through on this tree's longest paths - a half-populated checkout that then fails
    # phase checks for a reason that has nothing to do with the phase.
    param($Ctx)
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-done-clean-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    $g = Invoke-Git -Arguments @("-c", "core.longpaths=true", "clone", "--quiet", "--shared",
                                 "--branch", $Ctx.workline, "--single-branch", $Ctx.root, $path) -WorkDir $Ctx.root
    $sub = $null
    if ($g.exit -eq 0) {
        # A CLEAN CHECKOUT OF THE WORK LINE INCLUDES THE SUBMODULES IT PINS, or OB1/ is an
        # empty directory and every check that reads it fails with ENOENT - a FALSE RED
        # that says nothing about the phase it is attributed to.
        #
        # Each submodule is sourced from the WORK REPO's own copy rather than the network:
        # the pinned commit is by definition present there, the update takes seconds
        # instead of minutes, and an outage cannot turn clause 1 red. The submodule NAMES
        # are read from .gitmodules - derived, never a hard-coded "OB1".
        $cfg = Invoke-Git -Arguments @("config", "-f", ".gitmodules", "--get-regexp", '^submodule\..*\.path$') -WorkDir $path
        if ($cfg.exit -eq 0) {
            foreach ($line in (($cfg.stdout) -split "`n")) {
                $l = $line.Trim()
                if ($l -notmatch '^submodule\.(.+)\.path\s+(.+)$') { continue }
                $name = $Matches[1]; $rel = $Matches[2].Trim()
                $local = Join-Path $Ctx.root ($rel -replace '/', '\')
                if (Test-Path -LiteralPath $local) {
                    [void](Invoke-Git -Arguments @("config", ("submodule.{0}.url" -f $name), $local) -WorkDir $path)
                }
            }
        }
        $sub = Invoke-Git -Arguments @("-c", "protocol.file.allow=always", "-c", "core.longpaths=true",
                                       "submodule", "update", "--init", "--recursive") -WorkDir $path
    }
    return @{
        path = $path; exit = $g.exit; command = $g.command
        err  = ($g.stderr + $g.stdout)
        submodule_exit = $(if ($sub) { $sub.exit } else { $null })
        submodule_err  = $(if ($sub) { (($sub.stderr + $sub.stdout) -replace "\s+", " ").Trim() } else { "" })
    }
}

function Remove-CleanCheckout {
    # A CLONE IS JUST A DIRECTORY. Nothing is registered with the work repo, so nothing has
    # to be de-registered - the failure mode where a crashed run leaves a worktree entry
    # behind for clause 4 to find cannot happen here.
    param($Ctx, [string]$Path)
    if (-not $Path) { return }
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # Git marks pack files read-only; Remove-Item refuses those without help.
    Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object { try { $_.Attributes = "Normal" } catch { } }
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
}

function Test-Clause1 {
    # C.8.1 - every U-phase's section 2 "Validated by" check re-runs GREEN from a clean
    # checkout of the work line. "Code landed" is not satisfaction.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 1

    # THE SNAPSHOT, NOT THE DISK. Every document this clause reads was captured before the
    # first walkthrough command ran - see New-DfuSnapshot and class fifteen above.
    $planText = Get-SnapMd -Which "plan"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "plan"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "walkthrough"
    if (-not $planText) {
        $c.probes += (New-Probe -Name "read-plan" -Command ("read {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "PLAN.md is unreadable or missing - the phase set could not be derived, so nothing was judged"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    $parse  = Get-PhaseTableParse -Text $planText
    $phases = $parse.phases
    # U0-U6 AND U8 are this clause's subjects; U7 is standing by design and belongs to
    # clause 6. U8 was added by section C.9 (2026-08-31), and its own section 2 row makes
    # this extension part of its validation - U8 is NOT done, so it lands here as an
    # OUTSTANDING subject, which is exactly why it is in.
    #
    # AND THE POPULATION IS NOT THE DOCUMENT'S TO CHOOSE. Taking $ids from the CURRENT
    # PLAN.md let a phase delete itself out of this clause: drop U1's row and the expected
    # count fell 3 -> 2 in silence. The floor is pinned and checked back against C.8.1's
    # own words - see Add-PhaseFloorProbes.
    $floor = Add-PhaseFloorProbes -Clause $c -Ctx $Ctx -PlanText $planText -Phases $phases -Restrict '^U(?:[0-6]|8)$' -Parse $parse
    $ids = @($floor.ids)
    $c.coverage.subject  = "U-phases U0-U6 (named by C.8.1) and U8 (added by C.9), unioned with section 2's table - plus the floor's own drift check"
    $c.coverage.expected = $ids.Count + 1
    # A floor phase with no row cannot have its column re-run. It is NAMED here and the
    # phase-floor-present probe has already failed the clause; it is never a smaller N.
    foreach ($miss in @($floor.missing)) { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($miss) }
    if ($ids.Count -lt 1) {
        $c.probes += (New-Probe -Name "derive-phases" -Command ("parse section 2 table in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "the phase table parsed to ZERO rows - the parser and the document disagree, which is not a pass"))
        return (Resolve-ClauseVerdict -Clause $c)
    }

    $runs = Get-WalkthroughRuns -Text (Get-SnapDoc -Which "walkthrough")

    # THE SANDBOX IS SHARED WITH CLAUSE 5 AND BUILT BEFORE ANY COMMAND RUNS. It is the ONLY
    # place either clause executes anything; clause 5 used to run its commands in $Ctx.root.
    $clean = Get-DfuSandbox -Ctx $Ctx
    $cleanPath = $clean.path
    $c.detail += ("clean checkout of '{0}': {1} (exit {2})" -f $Ctx.workline, $clean.command, $(if ($clean.ok) { 0 } else { 1 }))
    $c.detail += ("clean checkout submodules: git submodule update --init --recursive (exit {0}) {1}" -f `
                  $clean.submodule_exit, $clean.submodule_err)
    if (-not $clean.ok) {
        # NO CLEAN CHECKOUT = NOTHING WAS PROVEN. Falling back to the current working tree
        # would be exactly the substitution this clause forbids, so it refuses instead.
        $c.probes += (New-Probe -Name "clean-checkout" -Command $clean.command `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note ("could not create a clean checkout of '{0}', so NO phase check was re-run: {1}" -f `
                         $Ctx.workline, $clean.why)))
        $c.coverage.not_evaluated = @($ids)
        return (Resolve-ClauseVerdict -Clause $c)
    }

    try {
        foreach ($id in $ids) {
            # A FLOOR PHASE THAT IS NOT IN THE TABLE HAS NO COLUMN TO RE-RUN - already
            # named in not_evaluated and already failed by phase-floor-present.
            if (-not $phases.Contains($id)) { continue }
            # SECTION 2 IS READ, not assumed. This clause claims to re-run "the section 2
            # Validated by check", and the previous version never opened section 2 at all -
            # it ran the first How-to-run span in the walkthrough and reported that as the
            # column being satisfied. Two different documents, one claim.
            $col = [string]$phases[$id].validated
            $c.detail += ("{0} section 2 Validated by: {1}" -f $id, $col)

            $cmds = @()
            if ($runs.Contains($id)) { $cmds = @($runs[$id]) }
            if ($cmds.Count -lt 1) {
                # NOT A PASS. A phase with no runnable command is exactly what this clause
                # exists to catch: its column is satisfied by a paragraph, and a paragraph
                # cannot re-run. It is NAMED, counted as not-evaluated, and the clause
                # cannot be met while it is here.
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
                $c.probes += (New-Probe -Name ("{0}-validated-by" -f $id) `
                    -Command ("(none - no 'How to run' recorded for {0} in {1})" -f $id, $Ctx.walkthrough) `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "no executable check is recorded for this phase, so its column was NOT re-run - its Validated-by is prose only"))
                continue
            }

            # EVERY command in the phase's section runs, not just the first. Taking $cmds[0]
            # silently DROPPED the additional named checks in the same section while the
            # coverage line still read "evaluated 7 of 7" - a claim wider than its evidence
            # in the one field that exists to stop exactly that.
            # THE SANDBOX IS RESET BETWEEN PHASES, so U0's command cannot leave behind the
            # artifact U3's command needs and call it a pass.
            Reset-DfuSandbox
            $n = 0
            $ranAll = $true
            $anyRed = $false
            $refs   = @()
            $moved  = @()
            foreach ($cmd in $cmds) {
                $n++
                $pname = ("{0}-validated-by-{1}" -f $id, $n)
                $refs += (Get-NamedArtifacts -Text $cmd)
                $run = Invoke-AuditedCommand -Ctx $Ctx -Command $cmd -Clause "1" -Phase $id
                $r   = $run.result
                $moved += @($run.drift)
                if (@($run.drift).Count -gt 0) { $anyRed = $true }
                elseif (-not $r.ran -or $null -eq $r.exit) { $ranAll = $false }
                elseif ([int]$r.exit -ne 0) { $anyRed = $true }
                $c.probes += (New-Probe -Name $pname -Command $cmd `
                              -Run (New-CommandProbeBody -Run $run -GreenNote "re-ran GREEN in the clean checkout"))
            }
            # AND THE CLAUSE STATES, PER PHASE, THAT RUNNING ITS CHECKS LEFT THE AUDITED TREE
            # WHERE IT FOUND IT. This is a POSITIVE assertion, not the absence of an
            # objection: it is printed green when nothing moved, which is what makes its red
            # meaningful.
            $c.probes += (New-Probe -Name ("{0}-left-the-audited-tree-unchanged" -f $id) `
                -Command ("fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of {0}'s {1} command(s)" -f $id, $cmds.Count) `
                -Run $(if (@($moved).Count -gt 0) {
                          New-VerdictProbeBody -Verdict "fail" -Exit (@($moved).Count) `
                          -Note ("running {0}'s checks CHANGED the audited tree: {1}. Later clauses still decide over the pre-run snapshot, so nothing was discharged by it - but a command the document names must not be able to move the world this run measures." -f $id, ((@($moved) | Sort-Object -Unique) -join ", "))
                      } else {
                          New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                          -Note ("the plan, the ledger, the walkthrough, documentation/notes and git's refs, status, worktrees and submodules are byte-identical before and after {0}'s {1} command(s), which ran in {2} with the audited documents locked" -f $id, $cmds.Count, $cleanPath)
                      }))
            # A PHASE COUNTS AS EVALUATED ONLY WHEN ALL OF ITS CHECKS RAN. One command that
            # could not start leaves the phase partly measured, and partly measured is the
            # word "unevaluated", not the word "green".
            if ($ranAll) { $c.coverage.evaluated = [int]$c.coverage.evaluated + 1 }
            else { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id) }

            # AND THE TIE BETWEEN THE TWO DOCUMENTS. The column names artifacts; the
            # commands reference artifacts; if they do not overlap, this clause ran
            # something, but not demonstrably the thing section 2 asked for. Where the
            # column names no artifact at all the correspondence cannot be machine-decided,
            # so it becomes a NAMED MANUAL CHECK rather than an assumption.
            $wanted = @(Get-NamedArtifacts -Text $col)
            $refs   = @($refs | Sort-Object -Unique)
            $mcmd   = ("compare section 2's {0} column against the {1} command(s) run for it" -f $id, $cmds.Count)
            if ($wanted.Count -lt 1) {
                [void](Add-ManualCheck -Clause $c -Store $Store -Name ("section-2-column-mapping-{0}" -f $id) `
                    -What ("Section 2's {0} column names no runnable artifact ('{1}'), so no machine can confirm the walkthrough command(s) re-run THAT column. Confirm by hand which command satisfies it, or make the column name its check." -f $id, $col))
                $c.probes += (New-Probe -Name ("{0}-check-matches-section-2" -f $id) -Command $mcmd `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "section 2's column names no runnable artifact, so the correspondence is a NAMED MANUAL CHECK - see the manual entry for this phase"))
            } else {
                $hit = @($wanted | Where-Object { $refs -contains $_ })
                if ($hit.Count -ge 1) {
                    $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                            -Note ("the command(s) run reference {0}, which section 2's column names" -f ($hit -join ", "))
                } else {
                    $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                            -Note ("section 2's column names {0} but the walkthrough command(s) reference {1} - what re-ran is not what the column asked for" -f `
                                   ($wanted -join ", "), $(if ($refs.Count) { $refs -join ", " } else { "no named artifact" }))
                }
                $c.probes += (New-Probe -Name ("{0}-check-matches-section-2" -f $id) -Command $mcmd -Run $body)
            }
            if ($anyRed) { $c.detail += ("{0}: at least one recorded check went RED in the clean checkout" -f $id) }
        }
    } finally {
        # THE SANDBOX IS SHARED WITH CLAUSE 5 AND REMOVED BY THE RUN, not by this clause -
        # see the finally around the clause loop. It is a CLONE, so `git worktree list` in
        # the audited repo cannot see it and clause 4 cannot count it as unfinished work.
        Reset-DfuSandbox
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

function Get-PlanRevisions {
    # Every commit that touched PLAN.md, oldest first, WITH the path it had in that
    # commit. --follow is used because this plan moved directory once; a reader that
    # assumed today's path would find nothing before the move and would silently
    # reconstruct a chain that starts after its own beginning.
    param($Ctx)
    $rel = $Ctx.plan
    if ($rel.StartsWith($Ctx.root)) { $rel = $rel.Substring($Ctx.root.Length).TrimStart('\', '/') }
    $rel = $rel -replace '\\', '/'
    $g = Invoke-Git -Arguments @("log", "--follow", "--reverse", "--date=short", "--format=%H%x09%ad%x09%s", "--", $rel) -WorkDir $Ctx.root
    if ($g.exit -ne 0) { return $null }
    $revs = @()
    foreach ($line in ($g.stdout -split "`n")) {
        $l = $line.TrimEnd("`r")
        if (-not $l.Trim()) { continue }
        $f = $l -split "`t"
        if ($f.Count -lt 3) { continue }
        $sha = $f[0]; $date = $f[1]; $subj = $f[2]
        # Resolve the path this file had IN THIS COMMIT. Never assume today's path.
        $path = $rel
        $show = Invoke-Git -Arguments @("show", ("{0}:{1}" -f $sha, $rel)) -WorkDir $Ctx.root
        if ($show.exit -ne 0) {
            $ls = Invoke-Git -Arguments @("ls-tree", "-r", "--name-only", $sha) -WorkDir $Ctx.root
            $cand = @(($ls.stdout -split "`n") | ForEach-Object { $_.Trim() } |
                      Where-Object { $_ -like "*dark-factory-unification/PLAN.md" })
            if ($cand.Count -ge 1) {
                $path = $cand[0]
                $show = Invoke-Git -Arguments @("show", ("{0}:{1}" -f $sha, $path)) -WorkDir $Ctx.root
            }
        }
        if ($show.exit -ne 0) {
            # RECORDED AS A HOLE, not skipped. A revision this script could not read is
            # exactly the "unreconstructable history" the clause fails on, and swallowing
            # it would turn a gap into a clean-looking chain.
            $revs += @{ sha = $sha; date = $date; subject = $subj; path = $path; text = $null; readable = $false }
            continue
        }
        $revs += @{ sha = $sha; date = $date; subject = $subj; path = $path; text = $show.stdout; readable = $true }
    }
    return @($revs)
}

function ConvertTo-Normalised {
    # Strip markdown noise so a requirement is compared on its WORDS, not its emphasis.
    param([string]$s)
    if (-not $s) { return "" }
    $t = $s
    $t = $t -replace '\*\*', ''
    $t = $t -replace '\*', ''
    $t = $t -replace '`', ''
    $t = $t -replace '\s+', ' '
    return $t.Trim().ToLowerInvariant()
}

# THE TWO WORD-LISTS BELOW ARE A BACKSTOP, NOT THE GATE. The gate is structural (a
# requirement is carried forward only if it survives VERBATIM as a requirement); these
# only add a second, harder refusal for the two rewrites that are unmistakably a
# retraction or a hedge, so that no ledger entry can call them "kept".
$script:DfuRetractionMarkers = @(
    'no longer required', 'no longer needed', 'no longer applies', 'not required',
    'dropped', 'retracted', 'removed', 'withdrawn', 'rescinded', 'waived', 'superseded',
    'abandoned', 'de-scoped', 'descoped', 'out of scope', 'cancelled', 'canceled',
    'obsolete', 'struck', 'unnecessary', 'no longer part of'
)
$script:DfuHedgeMarkers = @(
    'where feasible', 'where possible', 'where practical', 'if feasible', 'if practical',
    'if possible', 'best effort', 'best-effort', 'as time permits', 'time permitting',
    'optional', 'nice to have', 'aspirational', 'when convenient', 'advisory',
    'not mandatory', 'need not', 'may be skipped'
)

function Get-MarkersIn {
    param([string]$Text, [string[]]$Markers)
    $out = @()
    if (-not $Text) { return $out }
    foreach ($m in $Markers) { if ($Text.Contains($m)) { $out += $m } }
    return @($out)
}

function Resolve-CarryForward {
    # DOES THE CURRENT COLUMN STILL REQUIRE THIS? - the question clause 2 actually asks,
    # and the question a substring test does not answer.
    #
    # THE DEFECT THIS REPLACES. The old test was `if (-not $curNorm.Contains($req))`. A
    # requirement rewritten in place from "the gym run is observed" to "the gym run is
    # observed is NO LONGER REQUIRED - dropped as unnecessary" still CONTAINS the original
    # words, so the clause reported "all 2 requirement(s) in the ORIGINAL column survive in
    # the CURRENT column" and passed. The one edit shape this clause exists to catch - an
    # erosion that stays defensible at every step - was the one shape it could not see,
    # while the identical removal written as a DELETION was caught correctly.
    #
    # THE RULE NOW IS STRUCTURAL, so it does not depend on guessing English: a requirement
    # is CARRIED forward only when the current column still contains it as a requirement
    # VERBATIM - one of the current column's own semicolon-separated requirements is
    # exactly it. Anything else is a rewrite of that requirement, and a rewrite must be
    # dispositioned on the record rather than inferred to be harmless. Additions do not
    # touch existing requirements, so a chain that only ADDS leaves every original
    # requirement CARRIED - which is C.8's "additions never fail it", preserved.
    #
    # The word lists are the second, harder refusal: a rewrite that says the requirement is
    # no longer required, or that hedges it, can NEVER be dispositioned "kept", because the
    # column's own words say it is not kept.
    param([string]$Requirement, [string[]]$CurrentSegments)
    $segs = @($CurrentSegments)
    foreach ($seg in $segs) {
        if ($seg -eq $Requirement) {
            return @{ state = "carried"; segment = $seg; markers = @() }
        }
    }
    $hosts = @($segs | Where-Object { $_.Contains($Requirement) })
    if ($hosts.Count -lt 1) {
        return @{ state = "absent"; segment = ""; markers = @() }
    }
    $seg  = [string]$hosts[0]
    # Markers already present in the requirement itself are the ORIGINAL's own wording and
    # are not an erosion - only what the rewrite ADDED counts.
    $ret  = @(Get-MarkersIn -Text $seg -Markers $script:DfuRetractionMarkers |
              Where-Object { -not $Requirement.Contains($_) })
    $hed  = @(Get-MarkersIn -Text $seg -Markers $script:DfuHedgeMarkers |
              Where-Object { -not $Requirement.Contains($_) })
    if ($ret.Count -gt 0) { return @{ state = "retracted"; segment = $seg; markers = @($ret) } }
    if ($hed.Count -gt 0) { return @{ state = "weakened";  segment = $seg; markers = @($hed) } }
    return @{ state = "rewritten"; segment = $seg; markers = @() }
}

function Get-NamedArtifacts {
    # The runnable artifacts a piece of prose NAMES - script and source file names. Used by
    # clause 1 to tie a walkthrough command back to the section 2 column it claims to be
    # re-running, and by clause 2 to decide whether an amendment cites something checkable.
    # Derived from the text, so a column that starts naming a different script changes the
    # answer without anyone editing a list here.
    param([string]$Text)
    if (-not $Text) { return @() }
    $out = @()
    foreach ($m in [regex]::Matches($Text, '(?i)\b([A-Za-z0-9_.\-]+\.(?:ps1|py|ts|mjs|js|sh|sql|psm1))\b')) {
        $out += $m.Groups[1].Value.ToLowerInvariant()
    }
    return @($out | Sort-Object -Unique)
}

function Split-Requirements {
    # A "Validated by" cell is a list of requirements separated by semicolons. That is the
    # unit the clause's disposition rule talks about, so it is the unit compared.
    param([string]$cell)
    $n = ConvertTo-Normalised -s $cell
    if (-not $n) { return @() }
    return @(($n -split ';') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}

function New-VerdictProbeBody {
    # Build a probe body from an ALREADY-DECIDED verdict. The decision is made in real
    # PowerShell above; this only carries it into the probe record, so no string is ever
    # re-parsed as logic.
    param([string]$Verdict, $Exit, [string]$Note)
    $v = $Verdict; $e = $Exit; $n = $Note
    return {
        $r = @{ verdict = $v; note = $n }
        if ($null -ne $e) { $r["exit"] = $e }
        $r
    }.GetNewClosure()
}

function Test-Clause2 {
    # C.8.2 - no phase parked, every amendment accounted for, and THE CHAIN judged:
    # original -> A1 -> A2 -> ... -> current, quoted at each step, with the CURRENT column
    # judged against the ORIGINAL rather than against its immediate predecessor.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 2
    $dispositions = Read-JsonStore -Path $Ctx.dispositions

    # --- (a) outstanding PARKED entries in DECISIONS.md ---------------------------
    # NORMALISED, AND FROM THE SNAPSHOT. Raw, a "## " entry inside an HTML comment carrying
    # an "Un-parks:" directive closed a PARKED entry that no reader can see closed; and read
    # from the disk after clause 1 has run, a ledger entry the RUN created would have closed
    # one too.
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "decisions"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "plan"
    $decText = Get-SnapMd -Which "decisions"
    if (-not $decText) {
        $c.probes += (New-Probe -Name "decisions-readable" -Command ("read {0}" -f $Ctx.decisions) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "DECISIONS.md is unreadable or missing - parked entries could not be counted"))
    } else {
        # UN-PARKING IS A STRUCTURED ACT, NOT A LATER MENTION.
        #
        # The previous test accepted ANY later `## ` heading that contained the phase id
        # and one of CLOSES/CLOSED/DISCHARGED/UNPARKED anywhere in it - regardless of what
        # was closed. On this very ledger that is not hypothetical: U4's PARKED entry was
        # discharged by "U4 clause 3 - final state - two residual defects closed", a
        # heading about THIS CHECKER's clause 3, which closed nothing about U4's phase. A
        # heading that happens to carry two words is not a decision to un-park.
        #
        # So a parked entry is closed only by a LATER entry that CITES IT: a directive line
        #
        #     **Un-parks:** <enough of the parked entry's heading to identify it>
        #
        # (UNPARKS / UN-PARKS / CLOSES are accepted as the directive word). The citation is
        # matched against the parked headings, and it must identify EXACTLY ONE of them -
        # a citation matching two entries closes neither, because which one was meant is
        # the question the record is supposed to answer.
        $sections = @(Get-LedgerSections -Text $decText)
        $headings = @($sections | ForEach-Object { $_.heading })
        $parked = @($sections | Where-Object { $_.heading -match '(?i)\bPARKED\b' })
        $outstanding = @()
        $ambiguous   = @()
        foreach ($ps in $parked) {
            $pnorm = ConvertTo-Normalised -s $ps.heading
            $closedBy = ""
            for ($i = $ps.index + 1; $i -lt $sections.Count; $i++) {
                foreach ($dm in [regex]::Matches($sections[$i].body, '(?im)^\s*\*{0,2}(?:UN-?PARKS|CLOSES)\*{0,2}\s*:\s*(.+?)\s*$')) {
                    $cited = ConvertTo-Normalised -s $dm.Groups[1].Value
                    if ($cited.Length -lt 12) { continue }
                    $hits = @($parked | Where-Object { (ConvertTo-Normalised -s $_.heading).Contains($cited) })
                    if ($hits.Count -ne 1) {
                        $ambiguous += ("'{0}' in '{1}' matches {2} parked entries" -f $dm.Groups[1].Value.Trim(), $sections[$i].heading, $hits.Count)
                        continue
                    }
                    if ((ConvertTo-Normalised -s $hits[0].heading) -eq $pnorm) { $closedBy = $sections[$i].heading; break }
                }
                if ($closedBy) { break }
            }
            if ($closedBy) { $c.detail += ("UN-PARKED: '{0}' cited by '{1}'" -f $ps.heading, $closedBy) }
            else { $outstanding += $ps.heading }
        }
        foreach ($a in $ambiguous) { $c.detail += ("un-parking citation IGNORED - {0}" -f $a) }
        if ($outstanding.Count -eq 0 -and $parked.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "DECISIONS.md carries no PARKED entry at all"
        } elseif ($outstanding.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("all {0} PARKED entry/entries are closed by a later entry that CITES them by an Un-parks directive" -f $parked.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $outstanding.Count `
                    -Note ("{0} of {1} PARKED entry/entries are outstanding - no later entry carries an 'Un-parks:' directive citing them: {2}{3}" -f `
                           $outstanding.Count, $parked.Count, ($outstanding -join " | "), `
                           $(if ($ambiguous.Count) { " -- and " + $ambiguous.Count + " citation(s) were ignored as ambiguous" } else { "" }))
        }
        $c.probes += (New-Probe -Name "no-outstanding-parked" `
                      -Command ("split {0} on '^## ' headings; for each PARKED heading look for a LATER section carrying '**Un-parks:** <that heading>'" -f $Ctx.decisions) -Run $body)
    }

    # --- (b) every section 2.1 amendment carries evidence + revert path -----------
    $planText = Get-SnapMd -Which "plan"
    $amendments = @()
    if ($planText) {
        foreach ($part in [regex]::Split($planText, '(?m)^(?=####\s)')) {
            if ($part -notmatch '(?m)^####\s+(A\d+)\b') { continue }
            $aid = $Matches[1]
            # "CARRIES ITS EVIDENCE" IS JUDGED BY WHAT IT CITES, NOT BY A HEADING NAME.
            # Requiring a literal "**Evidence:**" line is a derived gate whose alphabet is
            # too narrow - the exact class this effort kept finding - and it fails A2, which
            # carries a full measurement under a different heading. So the test is for a
            # CITED, CHECKABLE ARTIFACT: a file path, a commit sha, or a file:line. The
            # alphabet is reported in the probe note, so a reader can see what was accepted
            # and object to it rather than having to trust it.
            $citesPath = [bool]($part -match '[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]+')
            $citesSha  = [bool]($part -match '(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])')
            $citesLine = [bool]($part -match '[A-Za-z0-9_.\-]+\.(ps1|py|ts|mjs|sql|json|md):\d+')
            $amendments += @{
                id      = $aid
                hasEvid = ($citesPath -or $citesSha -or $citesLine)
                evidWhy = ("cites path={0} sha={1} file:line={2}" -f $citesPath, $citesSha, $citesLine)
                hasRev  = [bool](($part -match '(?im)^\*\*Revert path') -or ($part -match '(?im)^REVERT:'))
            }
        }
    }
    foreach ($a in $amendments) {
        $miss = @()
        if (-not $a.hasEvid) { $miss += "Evidence" }
        if (-not $a.hasRev)  { $miss += "Revert path" }
        if ($miss.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("carries a checkable evidence citation and a revert path ({0})" -f $a.evidWhy)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("amendment is missing: {0}" -f ($miss -join " and "))
        }
        $c.probes += (New-Probe -Name ("amendment-{0}-accounted" -f $a.id) `
                      -Command ("parse section 2.1 block {0} in {1}" -f $a.id, $Ctx.plan) -Run $body)
    }
    if ($amendments.Count -lt 1) {
        $c.probes += (New-Probe -Name "amendments-derived" -Command ("parse '#### A<n>' blocks in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "no section 2.1 amendment blocks were found - either there are none or the parser missed them, and those are different facts"))
    }

    # --- (c) THE CHAIN, per phase -------------------------------------------------
    # FROM THE SNAPSHOT: a commit made DURING this run must not become a step of the chain
    # this run is judging.
    $revs = Get-SnapGit -Key "revisions"
    $curParse  = Get-PhaseTableParse -Text $planText
    $curPhases = $curParse.phases
    # THE FLOOR AGAIN. Deleting U1's row from section 2 used to make this clause report
    # "met, coverage 1/1" with U1's chain never reconstructed - silence about a link in the
    # chain, which is the failure this clause names in its own words.
    $floor2 = Add-PhaseFloorProbes -Clause $c -Ctx $Ctx -PlanText $planText -Phases $curPhases -Parse $curParse
    $ids = @($floor2.ids)
    $c.coverage.subject  = "phases whose Validated-by chain must be reconstructable - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with section 2's table - plus the floor's own drift check"
    $c.coverage.expected = $ids.Count + 1

    if ($null -eq $revs -or @($revs).Count -lt 1) {
        $c.probes += (New-Probe -Name "chain-history" -Command ("git log --follow -- {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "no revision history for PLAN.md could be read - no chain can be reconstructed, which is not a pass"))
        $c.coverage.not_evaluated = @($ids)
        return (Resolve-ClauseVerdict -Clause $c)
    }
    # A REVISION WHOSE TABLE DOES NOT PARSE IS A HOLE, exactly like one that could not be
    # read at all. Silently skipping it would move the ORIGINAL later in the chain - the
    # chain would start after its own beginning, and every comparison after that is
    # against the wrong text. The parse is done ONCE per revision here and reused below.
    $revParse = @{}
    $unparsable = @()
    # AND THE HOLE IS ATTRIBUTED TO THE PHASE IT BELONGS TO. A duplicated U4 row makes that
    # revision unreadable FOR U4; it says nothing about U2. A problem that names no phase at
    # all - no section 2 heading, two of them, a header with no Validated-by column - holes
    # EVERY phase at that revision, because none of them was located.
    $revHole = @{}
    # AND A DECLINED ROW IS A HOLE IN THE HISTORICAL READER TOO - ROUND 2, ITEM 1.
    # `$Parse.ignored` had exactly ONE consumer in this file - Add-PhaseFloorProbes - and
    # that consumer only ever sees the CURRENT PLAN.md. So amendment A3 closed a LOUD
    # REFUSAL in the current reader and opened a SILENT DROP one reader over: a revision
    # whose phase-id cell carried trailing text stopped being a duplicate-refusal and started
    # being skipped HERE without a word, by the `if (-not $t.Contains($id)) { continue }`
    # below. Constructed and proved: a revision whose id cell read
    # `**U4 (runner) unification**` took clause 2 from "[fail] chain-U4-original-vs-current,
    # 2 distinct states, U4 ABSENT [NO DISPOSITION]" to "[pass], 1 distinct state, U4
    # CARRIED" - the ORIGINAL moved forward one revision, which is exactly what this
    # loop's own comment above says must never happen. This is rule 9's class (NORMALISE IN
    # EVERY READER, THEN GREP FOR THE SHAPE); the grep is verify-dfu-done.ps1 step R1, which
    # fails if a THIRD reader of this parser is added that does not surface `ignored`.
    $revIgnored = @{}
    foreach ($r in $revs) {
        if (-not $r.readable) { continue }
        $rp = Get-PhaseTableParse -Text $r.text
        $revParse[$r.sha] = $rp
        foreach ($cell in @($rp.ignored)) {
            # `U40` is not `U4`, so a cell whose id token runs on into more digits is
            # attributed to NO phase rather than to the wrong one - it lands in the
            # unattributed bucket and is still printed.
            $nid = "*"
            if ($cell -match '^\s*(?:\*\*|__)?\s*(U\d)(?![0-9])') { $nid = $Matches[1] }
            if (-not $revIgnored.ContainsKey($nid)) { $revIgnored[$nid] = @() }
            $revIgnored[$nid] = @($revIgnored[$nid]) + @(@{
                sha    = $r.sha
                cell   = $cell
                hasRow = [bool]($nid -ne "*" -and $rp.phases.Contains($nid))
            })
        }
        if (@($rp.problems).Count -gt 0) {
            $unparsable += ("{0} ({1})" -f (Get-ShortRef -Sha $r.sha), (@($rp.problems)[0]))
            foreach ($prob in @($rp.problems)) {
                $named = @([regex]::Matches($prob, '(?<![A-Za-z0-9])(U\d)(?![0-9])') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
                if ($named.Count -lt 1) { $named = @("*") }
                foreach ($nid in $named) {
                    if (-not $revHole.ContainsKey($nid)) { $revHole[$nid] = @() }
                    $revHole[$nid] = @($revHole[$nid]) + @(("{0}: {1}" -f (Get-ShortRef -Sha $r.sha), $prob))
                }
            }
        }
    }
    if ($unparsable.Count -gt 0) {
        $c.probes += (New-Probe -Name "chain-revisions-parsable" `
            -Command ("parse section 2's table at each of {0} PLAN.md revision(s)" -f @($revs).Count) `
            -Run (New-VerdictProbeBody -Verdict "fail" -Exit $unparsable.Count `
                  -Note ("section 2's table did not parse to one unambiguous answer at {0} revision(s): {1} - a chain reconstructed across a hole is indistinguishable from one that was never recorded" -f `
                         $unparsable.Count, (($unparsable | Select-Object -First 5) -join " ; "))))
    }
    $unreadable = @($revs | Where-Object { -not $_.readable })
    if ($unreadable.Count -gt 0) {
        $shas = (($unreadable | ForEach-Object { Get-ShortRef -Sha $_.sha }) -join ",")
        $c.probes += (New-Probe -Name "chain-revisions-readable" `
            -Command ("git show <sha>:<plan> across {0} revisions" -f @($revs).Count) `
            -Run (New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                  -Note ("PLAN.md could not be read at {0} revision(s) ({1}) - an unreconstructable history is indistinguishable from an unrecorded one" -f $unreadable.Count, $shas)))
    }

    # A DECLINED ROW THAT NAMES NO PHASE THIS CLAUSE ITERATES still has to be said out
    # loud, or the population's own edge becomes the hiding place: a `**U40**` cell, or a
    # cell naming an id that is neither in the pinned floor nor in the current table, would
    # otherwise be captured above and printed by nobody. It is REPORTED rather than failed -
    # it is not a step of any chain this clause reconstructs, so calling it a failure would
    # be inventing one. Parity with the current reader, where a declined row is a named note
    # on a probe that still passes.
    $orphanDecl = @()
    foreach ($k in @($revIgnored.Keys)) {
        if ($ids -contains $k) { continue }
        foreach ($d in @($revIgnored[$k])) {
            $orphanDecl += ("{0}: '{1}' (begins like a phase id but names {2}, which is neither in the pinned floor nor in the current table)" -f `
                            (Get-ShortRef -Sha $d.sha), $d.cell, $(if ($k -eq "*") { "no single-digit phase" } else { $k }))
        }
    }
    if ($orphanDecl.Count -gt 0) {
        $c.probes += (New-Probe -Name "chain-revisions-declined-rows" `
            -Command ("list the rows section 2's table DECLINED to read as phase rows across {0} PLAN.md revision(s)" -f @($revs).Count) `
            -Run (New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                  -Note ("{0} declined row(s) in the history name no phase this clause reconstructs - listed so 'ignored' is never read as 'there was nothing there': {1}" -f `
                         $orphanDecl.Count, ((@($orphanDecl) | Select-Object -First 5) -join " ; "))))
    }

    foreach ($id in $ids) {
        $chain = @()
        foreach ($r in $revs) {
            if (-not $r.readable) { continue }
            if (-not $revParse.ContainsKey($r.sha)) { continue }
            $t = $revParse[$r.sha].phases
            if (-not $t.Contains($id)) { continue }
            $vb = [string]$t[$id].validated
            if ($chain.Count -eq 0 -or (ConvertTo-Normalised -s $chain[-1].text) -ne (ConvertTo-Normalised -s $vb)) {
                $chain += @{ sha = $r.sha; date = $r.date; subject = $r.subject; text = $vb }
            }
        }
        # AND THE WORKING TREE IS A STATE OF THE CHAIN. The chain was built from git while
        # the phase set, the amendments and the parked check are read from DISK, so an
        # UNCOMMITTED weakening of a column was invisible here: the chain ended at the last
        # commit and the erosion sat in the file nobody compared. It is appended as the
        # final step, labelled for what it is.
        if ($curPhases.Contains($id)) {
            $onDisk = [string]$curPhases[$id].validated
            if ($chain.Count -eq 0 -or (ConvertTo-Normalised -s $chain[-1].text) -ne (ConvertTo-Normalised -s $onDisk)) {
                $chain += @{ sha = "worktree"; date = "uncommitted"; subject = "the working tree, not yet committed"; text = $onDisk }
            }
        }

        # THE CHAIN IS PRINTED, dated and verbatim at each step - that is the clause's
        # own requirement, and it is what lets a reader disagree with the verdict.
        $c.detail += ("chain {0}: {1} distinct state(s)" -f $id, $chain.Count)
        foreach ($step in $chain) {
            $c.detail += ("    {0} {1} : {2}" -f $step.date, (Get-ShortRef -Sha $step.sha), $step.text)
        }
        # A HOLE THIS PHASE'S CHAIN CROSSES IS THIS PHASE'S PROBLEM. Skipping the revision
        # would move the ORIGINAL later and every comparison after it would be against the
        # wrong text - silence about a link in the chain, which is the failure this clause
        # names in its own words.
        $holes = @()
        foreach ($hk in @("*", $id)) { if ($revHole.ContainsKey($hk)) { $holes += @($revHole[$hk]) } }
        if ($holes.Count -gt 0) {
            $c.probes += (New-Probe -Name ("chain-{0}-has-a-hole" -f $id) `
                -Command ("parse section 2's table for {0} at each of {1} PLAN.md revision(s)" -f $id, @($revs).Count) `
                -Run (New-VerdictProbeBody -Verdict "fail" -Exit $holes.Count `
                      -Note ("{0}'s chain crosses {1} revision(s) where section 2's table did not define it unambiguously: {2}" -f `
                             $id, $holes.Count, ((@($holes) | Select-Object -First 3) -join " ; "))))
        }
        # AND A ROW THE PARSER DECLINED IS AS LOUD HERE AS IN THE CURRENT FILE - ROUND 2,
        # ITEM 1. Two shapes, and they are NOT the same fact:
        #   - the phase ALSO has a real id-cell row at that revision: the parse still
        #     answered for the phase, the chain step is intact, and this is A3 doing its
        #     job (that is the live `2151193` case - `**U4**` plus a `**U4 status (...)**`
        #     annotation). REPORTED, not failed; failing it would revert A3's authorised
        #     greening.
        #   - the phase has NO real row at that revision: the revision is skipped FOR THIS
        #     PHASE, which moves the ORIGINAL forward and compares every later step against
        #     the wrong text. FAILED, in the same words this clause uses for a hole.
        $decl = @()
        if ($revIgnored.ContainsKey($id)) { $decl = @($revIgnored[$id]) }
        if ($decl.Count -gt 0) {
            $lost  = @($decl | Where-Object { -not $_.hasRow })
            $descr = @()
            foreach ($d in $decl) {
                $descr += ("{0}: '{1}'{2}" -f (Get-ShortRef -Sha $d.sha), $d.cell, `
                           $(if ($d.hasRow) { " [the phase also has a real id-cell row at this revision - the step is intact]" }
                             else { " [NO id-cell row for the phase at this revision - THE STEP IS LOST]" }))
            }
            foreach ($t in $descr) { $c.detail += ("    {0} DECLINED ROW : {1}" -f $id, $t) }
            if ($lost.Count -gt 0) {
                $db = New-VerdictProbeBody -Verdict "fail" -Exit $lost.Count `
                      -Note ("{0}'s chain skips {1} revision(s) where the ONLY cell naming it was declined as an id cell, so no step was recorded there and the chain's ORIGINAL moved forward: {2}" -f `
                             $id, $lost.Count, ((@($descr) | Select-Object -First 3) -join " ; "))
            } else {
                $db = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                      -Note ("{0} row(s) naming {1} were declined as id cells in the history, and {1} has a real row at every one of those revisions, so no chain step was lost: {2}" -f `
                             $decl.Count, $id, ((@($descr) | Select-Object -First 3) -join " ; "))
            }
            $c.probes += (New-Probe -Name ("chain-{0}-declined-rows" -f $id) `
                -Command ("list the rows naming {0} that section 2's table DECLINED to read as id cells across {1} PLAN.md revision(s), and check {0} still had a real row at each" -f $id, @($revs).Count) `
                -Run $db)
        }
        if ($chain.Count -lt 1) {
            # UNRECONSTRUCTABLE => FAIL, in the clause's own words.
            $c.probes += (New-Probe -Name ("chain-{0}" -f $id) `
                -Command ("reconstruct {0} Validated-by across {1} revisions" -f $id, @($revs).Count) `
                -Run (New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                      -Note ("the chain for {0} could not be reconstructed from the record at all" -f $id)))
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
            continue
        }
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1

        # JUDGE CURRENT AGAINST ORIGINAL - never against the immediate predecessor. That
        # pairwise comparison is precisely how a column erodes while every single step
        # stays defensible, and it is the shape of this effort's own
        # ENUMERATE-AND-PATCH LOSES entry: every fix correct, the sequence still lost.
        $original = [string]$chain[0].text
        $current  = [string]$chain[-1].text
        $origReqs = @(Split-Requirements -cell $original)
        $curReqs  = @(Split-Requirements -cell $current)

        # IS THE DROP ON THE RECORD AT ALL? A disposition lives in a side file this script
        # reads; C.8 asks for the CHAIN to be accounted for, so a disposition is only
        # accepted when the phase's change is also visible where the operator reads -
        # section 2.1 or DECISIONS.md. A ledger entry nobody could find by reading the
        # documents is a deletion with a receipt filed somewhere else.
        $onRecord = $false
        if ($planText -and ($planText -match ('(?m)^####\s+A\d+[^\n]*\b' + $id + '\b'))) { $onRecord = $true }
        if (-not $onRecord -and $decText) {
            foreach ($line in ($decText -split "`n")) {
                if ($line -match '^##\s+(.*)$' -and $Matches[1] -match ('\b' + $id + '\b')) { $onRecord = $true; break }
            }
        }

        $cmdLabel = ("compare ORIGINAL({0} {1}) vs CURRENT({2} {3}) for {4}, requirement by requirement" -f `
                     $chain[0].date, (Get-ShortRef -Sha $chain[0].sha), $chain[-1].date, (Get-ShortRef -Sha $chain[-1].sha), $id)

        $carried  = @()
        $problems = @()
        foreach ($req in $origReqs) {
            $cf   = Resolve-CarryForward -Requirement $req -CurrentSegments $curReqs
            $key  = "{0}::{1}" -f $id, $req
            $rec  = $null
            if ($dispositions -and $dispositions.PSObject -and ($dispositions.PSObject.Properties.Name -contains $key)) {
                $rec = $dispositions.$key
            }
            $kind = ""
            if ($rec -and $rec.PSObject.Properties.Name -contains "disposition") { $kind = [string]$rec.disposition }

            if ($cf.state -eq "carried") {
                $carried += $req
                $c.detail += ("    {0} CARRIED : {1}" -f $id, $req)
                continue
            }

            # Everything below is a requirement the CURRENT column no longer states as it
            # stood. It needs an explicit, checkable disposition - never an inference.
            $why = switch ($cf.state) {
                "absent"    { "it is absent from the CURRENT column" }
                "retracted" { ("the CURRENT column RETRACTS it in place ({0}): '{1}'" -f (($cf.markers) -join ", "), $cf.segment) }
                "weakened"  { ("the CURRENT column HEDGES it ({0}): '{1}'" -f (($cf.markers) -join ", "), $cf.segment) }
                default     { ("the CURRENT column rewrites it: '{0}'" -f $cf.segment) }
            }
            if (-not $rec) {
                $problems += ("{0} :: {1} - and no disposition is recorded" -f $req, $why)
                $c.detail  += ("    {0} {1} : {2}  [NO DISPOSITION]" -f $id, $cf.state.ToUpperInvariant(), $req)
                continue
            }
            $bad = ""
            if ($kind -eq "kept") {
                # A "kept" record is a CLAIM, and it is checked against the column's own
                # words. It can cover a rewording; it can never cover a retraction or a
                # hedge, because there the column itself says the requirement is not kept.
                if ($cf.state -eq "retracted" -or $cf.state -eq "weakened") {
                    $bad = ("the ledger records it KEPT, but {0} - a retraction cannot be dispositioned as a keep" -f $why)
                } elseif ($cf.state -eq "absent") {
                    $bad = "the ledger records it KEPT, but it is absent from the CURRENT column"
                }
            } elseif ($kind -eq "incoherent") {
                if ([string]::IsNullOrWhiteSpace([string]$rec.evidence)) {
                    $bad = "dispositioned 'incoherent' with no evidence"
                } elseif (@(Get-NamedArtifacts -Text ([string]$rec.evidence)).Count -lt 1 -and
                          ([string]$rec.evidence) -notmatch '(?<![0-9a-zA-Z])[0-9a-f]{7,40}(?![0-9a-zA-Z])') {
                    $bad = "dispositioned 'incoherent' but its evidence cites nothing checkable - no file and no commit"
                } elseif (-not $onRecord) {
                    $bad = ("dispositioned 'incoherent' in {0}, but {1}'s change appears in neither section 2.1 nor DECISIONS.md" -f $Ctx.dispositions, $id)
                }
            } elseif ($kind -eq "follow-on") {
                if ([string]::IsNullOrWhiteSpace([string]$rec.owner) -or [string]::IsNullOrWhiteSpace([string]$rec.findings_sink)) {
                    $bad = "dispositioned 'follow-on' without both an owner and a findings sink - a follow-on with neither is a deletion wearing a promise"
                } elseif (-not (Test-Path -LiteralPath (Join-Path $Ctx.root ([string]$rec.findings_sink -replace '/', '\')))) {
                    $bad = ("dispositioned 'follow-on' but its findings sink does not exist: {0}" -f $rec.findings_sink)
                } elseif (-not $onRecord) {
                    $bad = ("dispositioned 'follow-on' in {0}, but {1}'s change appears in neither section 2.1 nor DECISIONS.md" -f $Ctx.dispositions, $id)
                }
            } else {
                $bad = ("the disposition '{0}' is not one of kept / incoherent / follow-on - an unrecognised disposition decides nothing" -f $kind)
            }
            if ($bad) {
                $problems += ("{0} :: {1}" -f $req, $bad)
                $c.detail  += ("    {0} {1} : {2}  [BAD DISPOSITION: {3}]" -f $id, $cf.state.ToUpperInvariant(), $req, $kind)
            } else {
                $c.detail += ("    {0} {1} : {2}  [dispositioned {3}]" -f $id, $cf.state.ToUpperInvariant(), $req, $kind)
            }
        }
        # ADDITIONS ARE REPORTED AND NEVER FAIL - C.8 is explicit that a chain which added
        # requirements is the chain working as intended.
        $added = @($curReqs | Where-Object { $origReqs -notcontains $_ })
        foreach ($a in $added) { $c.detail += ("    {0} ADDED   : {1}" -f $id, $a) }

        if ($problems.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("{0} of {1} ORIGINAL requirement(s) survive VERBATIM in the CURRENT column and the rest carry a valid disposition; {2} addition(s), which never fail this clause" -f `
                           $carried.Count, $origReqs.Count, $added.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $problems.Count `
                    -Note ("{0} of {1} ORIGINAL requirement(s) are not carried forward and not validly dispositioned: {2}" -f `
                           $problems.Count, $origReqs.Count, (($problems | Select-Object -First 3) -join " || "))
        }
        $c.probes += (New-Probe -Name ("chain-{0}-original-vs-current" -f $id) -Command $cmdLabel -Run $body)
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

# ---------------------------------------------------------------------------------
# CLAUSE 3's DOOR SET. C.8 clause 3 NAMES the doors, so this is the plan's list, not a
# list this script invented - and it is checked back against the plan's own words by
# `door-set-matches-plan` below, so a door the plan names and this file does not probe
# turns the clause red instead of disappearing. That check is what makes a pinned floor
# different from a hand-written one: the tree cannot drift away from it silently.
#
# Doors DISCOVERED beyond the floor are additional, never substitutes: the whole PostgREST
# surface is enumerated from the live schema and swept (`postgrest-surface-sweep`).
# ---------------------------------------------------------------------------------
# THE NON-DOOR SUBJECTS OF CLAUSE 3, named once so the coverage arithmetic cannot drift
# from what is actually probed. Two of them do not need the live plane and are evaluated
# even under -SkipLive; the rest are listed as not_evaluated there.
$script:DfuClause3Extras = @(
    "door-set-matches-plan",
    "corpus-predicate-source-on-work-line",
    "corpus-predicate-fail-closed",
    "corpus-backfill-landed",
    "fixture-write-landed",
    "postgrest-surface-sweep"
)
$script:DfuClause3ExtrasOffline = @("door-set-matches-plan", "corpus-predicate-source-on-work-line")

$script:DfuRequiredDoors = [ordered]@{
    "postgrest-thoughts"          = "PostgREST on the thoughts corpus"
    "postgrest-agent-memories"    = "PostgREST on agent_memories"
    "postgrest-thought-entities"  = "the thought_entities join, which can project thought content"
    "postgrest-derived-queue"     = "entity_extraction_queue - DERIVED data about a protected row"
    "wiki-compiler-output"        = "the wiki compiler's published output (wiki_pages)"
    "openbrain-mcp-door"          = "the raw openbrain-mcp door - the agent plane's own connection"
    "cloud-search-thoughts"       = "cloud search_thoughts, via the gateway that fronts it"
    "mcp-read-tools"              = "the MCP read tools as a client calls them (the ops door's recall lane)"
}

# WHICH OF THE PLAN'S OWN WORDS EACH SUBJECT CLAIMS TO COVER. Every identifier C.8 clause 3
# writes in backticks is extracted from PLAN.md at run time and must be claimed by
# something here; an unclaimed one is a door the plan names and this script does not
# attack, and that FAILS rather than going unmentioned.
$script:DfuDoorAnchors = [ordered]@{
    "postgrest-thoughts"         = @("thoughts")
    "postgrest-agent-memories"   = @("agent_memories")
    "postgrest-thought-entities" = @("thought_entities")
    "postgrest-derived-queue"    = @()
    "wiki-compiler-output"       = @("wiki compiler")
    "openbrain-mcp-door"         = @("openbrain-mcp")
    "cloud-search-thoughts"      = @("search_thoughts")
    "mcp-read-tools"             = @("MCP read tools")
    "_corpus-predicate"          = @("ob_corpus_on_ops_plane", "exposure='ops'", "IS NULL OR = 'ops'")
    "_the-fixture-itself"        = @("personal")
}

# The services this plan ADDS, which clause 4 requires to be RUNNING LIVE from the work
# line's code. Pinned in code and each with its own probe - "deployed" is a fact about a
# running system, and no document can be asked for it.
$script:DfuRequiredServices = [ordered]@{
    "ops-gateway"   = "the memory plane's ops door (U1)"
    "andon-board"   = "the andon board (U6 clauses 1-3)"
    "gate-profiles" = "dark vs attended gate profiles (U6)"
    "rls-boundary"  = "the exposure boundary as row-level security (U5, PLAN A2)"
}

# WHICH OF C.8 CLAUSE 4's OWN WORDS EACH SERVICE CLAIMS. The header of this file says both
# pinned sets "are CHECKED BACK against the plan's words at run time" - and only clause 3's
# was. Clause 4's service floor had NO drift check at all, so a service the plan started
# naming and this file did not probe would have disappeared instead of turning the clause
# red, which is the difference between a pinned floor and a hand-written list.
$script:DfuServiceAnchors = [ordered]@{
    "ops-gateway"   = @("ops gateway")
    "andon-board"   = @("andon board")
    "gate-profiles" = @("gate profiles")
    "rls-boundary"  = @("RLS boundary", "direct clients")
}

function Get-DirectDbClients {
    # WHO TALKS TO THE CORPUS DATABASE DIRECTLY, AND AS WHICH ROLE - derived from the
    # running system (the containers on the Open Brain network and what their environment
    # says about how they connect), never from a list in here.
    #
    # Returns $null when the question could not be asked at all, otherwise
    #   @{ roles = @{ role -> @(containers) }   the clients whose DB role IS determinable
    #      unknown  = @(containers)             clients that reach the DB with NO readable role
    #      silent   = @(containers)             containers on the network whose environment
    #                                           shows no connection to the DB at all
    #      considered = @(containers) }
    #
    # WHY THE ALPHABET WIDENED, AND WHY `unknown` EXISTS. The previous version matched only
    # `^(DB_USER|PGUSER|POSTGRES_USER)=` or a `proto://user:` URI. Two live clients fell
    # straight through it:
    #   - `open_notebook` reaches openbrain-db as `postgres` (rolsuper/rolbypassrls = t/t)
    #     via **OB1_DB_USER** - a prefixed name the anchored pattern could not see - and was
    #     never enumerated at all;
    #   - `openbrain-idea-refinery` carries `DB_HOST=openbrain-db` and NO role variable, so
    #     it was silently skipped rather than reported as undeterminable.
    # So `service-rls-boundary` decided its pass condition over an INCOMPLETE set with no
    # record of what could not be determined - the exact "claim wider than its evidence"
    # shape this script exists to catch. A client whose role cannot be read is now
    # INDETERMINATE, never absent.
    #
    # THE STATED RESTRICTION. Evidence here is the container's ENVIRONMENT. A client that
    # hardcodes the host in its code, with nothing in its environment naming it, is not
    # visible to this enumeration - those containers are returned in `silent` and the probe
    # says so in its note rather than implying they were cleared.
    #
    # The database container itself is excluded: it is the server, not a client of itself,
    # and counting its own POSTGRES_USER would make the boundary unmeetable for a reason
    # that has nothing to do with the boundary.
    param($Ctx)
    $r = Invoke-Native -Exe "docker" -Arguments @("network", "inspect", $Ctx.obnet, "--format", "{{range .Containers}}{{.Name}} {{end}}")
    if (-not $r.ran -or $r.exit -ne 0) { return $null }
    $names = @(($r.stdout -split '[ \t\r\n]+') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($names.Count -lt 1) { return $null }
    $argv = @("inspect", "--format", "{{.Name}}|{{range .Config.Env}}{{.}};{{end}}") + $names
    $ri = Invoke-Native -Exe "docker" -Arguments $argv
    if (-not $ri.ran -or $ri.exit -ne 0) { return $null }

    $dbhost = [string]$Ctx.db
    $out = @{ roles = @{}; unknown = @(); silent = @(); considered = @() }
    foreach ($line in ($ri.stdout -split "`n")) {
        $l = $line.Trim()
        if (-not $l) { continue }
        $parts = $l -split '\|', 2
        if ($parts.Count -lt 2) { continue }
        $cname = $parts[0].TrimStart('/')
        if ($cname -eq $dbhost) { continue }
        $out.considered += $cname
        $isClient = $false
        $roles = @()
        foreach ($kv in ($parts[1] -split ';')) {
            $kvt = $kv.Trim()
            if (-not $kvt) { continue }
            $eq = $kvt.IndexOf('=')
            if ($eq -lt 1) { continue }
            $k = $kvt.Substring(0, $eq)
            $v = $kvt.Substring($eq + 1)
            # (a) does anything in this container's environment NAME the corpus database?
            if ($v -match ('(?<![A-Za-z0-9_.\-])' + [regex]::Escape($dbhost) + '(?![A-Za-z0-9_\-])')) { $isClient = $true }
            # (b) a role variable, WHATEVER it is prefixed with - OB1_DB_USER is the case
            #     the anchored pattern missed.
            if ($k -match '(?i)(^|_)(DB_USER|DB_USERNAME|DB_ROLE|PGUSER|PG_USER|POSTGRES_USER|DATABASE_USER)$' -and $v -match '^[A-Za-z0-9_]+$') {
                $isClient = $true
                if ($roles -notcontains $v) { $roles += $v }
            }
            # (c) a connection URI pointing AT THIS HOST, with or without a user in it
            elseif ($v -match '(?i)^[a-z][a-z0-9+.\-]*://(?:([^:/@\s]+?)(?::[^@\s]*)?@)?([^/:?\s]+)') {
                $uUser = $Matches[1]
                $uHost = $Matches[2]
                if ($uHost -eq $dbhost) {
                    $isClient = $true
                    if ($uUser -and ($roles -notcontains $uUser)) { $roles += $uUser }
                }
            }
        }
        if (-not $isClient) { $out.silent += $cname; continue }
        if ($roles.Count -lt 1) { $out.unknown += $cname; continue }
        foreach ($role in $roles) {
            if (-not $out.roles.ContainsKey($role)) { $out.roles[$role] = @() }
            if (@($out.roles[$role]) -notcontains $cname) { $out.roles[$role] = @($out.roles[$role]) + @($cname) }
        }
    }
    return $out
}

function Get-ContainerEnv {
    # One environment variable out of a running container, or "". Used for the keys the
    # MCP doors authenticate with, so this script never carries a secret of its own.
    param([string]$Container, [string]$Name)
    $r = Invoke-Native -Exe "docker" -Arguments @("exec", $Container, "printenv", $Name)
    if ($r.ran -and $r.exit -eq 0) { return $r.stdout.Trim() }
    return ""
}

function Invoke-McpTool {
    # CALL AN MCP DOOR THE WAY A CLIENT DOES - one JSON-RPC tools/call over HTTP, from
    # inside obnet. This is what turns "the door's DB role has BYPASSRLS" (an inference
    # about a door) into "the door returned the personal fixture" (a measurement of it).
    #
    # THE KEY IS REDACTED OUT OF THE RECORDED COMMAND. Every probe prints what was run so a
    # reader can re-run it; printing a live gateway key would make this script an exposure
    # of its own.
    param($Ctx, [string]$Url, [string]$Header, [string]$Secret, [string]$Tool, [string]$ArgumentsJson)
    if ($Ctx.skiplive) { return @{ ran = $false; status = 0; body = ""; command = "(not run: -SkipLive)" } }
    $payload = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"' + $Tool + '","arguments":' + $ArgumentsJson + '}}'
    # EVERY DOUBLE QUOTE IS ESCAPED, and without this the probe measures nothing.
    # PowerShell 5.1 does not escape embedded double quotes when it hands an argument to a
    # native executable, so this JSON-RPC body arrives at curl as
    # {jsonrpc:2.0,id:1,...} - the server answers -32700 Parse error with HTTP 400, and
    # every MCP door reports "answered HTTP 400, nothing was measured". It is the same
    # quoting trap this file already documents for psql, one transport over: a wrong answer
    # delivered confidently by a layer nobody looked at.
    $escaped = $payload -replace '"', '\"'
    $argv = @("run", "--rm", "--network", $Ctx.obnet, "curlimages/curl:latest", "-sS", "-g", "-X", "POST",
              "-H", "Content-Type: application/json",
              "-H", "Accept: application/json, text/event-stream",
              "-H", $Header, "-d", $escaped, "-w", "\n#DFU %{http_code}\n", $Url)
    $r = Invoke-Native -Exe "docker" -Arguments $argv
    $status = 0
    $body = @()
    foreach ($line in (($r.stdout) -split "`n")) {
        $l = $line.TrimEnd("`r")
        if ($l -match '^#DFU\s+(\d+)\s*$') { $status = [int]$Matches[1]; continue }
        $body += $l
    }
    $cmd = $r.command
    if ($Secret) { $cmd = $cmd.Replace($Secret, "<redacted>") }
    return @{ ran = $r.ran; status = $status; body = ($body -join "`n"); command = $cmd; stderr = $r.stderr }
}

function Test-Ob1SourceOnWorkLine {
    # IS THE SQL THAT DEFINES A LIVE BOUNDARY ACTUALLY ON THE WORK LINE? A migration
    # applied to production from an unmerged branch is deployed-but-not-landed - the mirror
    # of C.8.4's "nothing in flight", and just as much a thing in flight. Clause 4's RLS
    # probe carried this guard; the corpus-predicate probe did not, and passed while its
    # defining SQL was absent from the pinned OB1 tree.
    #
    # Returns known=$false when the question could not be ASKED - a missing submodule or an
    # unreadable gitlink is "could not check", never "fine".
    param($Ctx, [string]$RelPath)
    $ob1 = Join-Path $Ctx.root "OB1"
    $gl = Invoke-Git -Arguments @("ls-tree", $Ctx.workline, "OB1") -WorkDir $Ctx.root
    $pin = ""
    if ($gl.exit -eq 0) {
        $m = [regex]::Match($gl.stdout, 'commit[ 	]+([0-9a-f]{40})')
        if ($m.Success) { $pin = $m.Groups[1].Value }
    }
    if (-not $pin) { return @{ ok = $false; known = $false; pin = ""; why = "the OB1 gitlink could not be read from the work line" } }
    if (-not (Test-Path -LiteralPath $ob1)) {
        return @{ ok = $false; known = $false; pin = $pin; why = "the OB1 submodule checkout is not present, so the pinned tree could not be inspected" }
    }
    $have = Invoke-Git -Arguments @("cat-file", "-e", ($pin + "^{commit}")) -WorkDir $ob1
    if ($have.exit -ne 0) {
        return @{ ok = $false; known = $false; pin = $pin
                  why = ("the pinned OB1 commit {0} is not present in this checkout, so its tree could not be read" -f (Get-ShortRef -Sha $pin)) }
    }
    $cf = Invoke-Git -Arguments @("cat-file", "-e", ("{0}:{1}" -f $pin, $RelPath)) -WorkDir $ob1
    if ($cf.exit -eq 0) { return @{ ok = $true; known = $true; pin = $pin; why = "" } }
    return @{ ok = $false; known = $true; pin = $pin
              why = ("{0} is NOT in the OB1 tree the work line pins ({1})" -f $RelPath, (Get-ShortRef -Sha $pin)) }
}

function Test-Clause3 {
    # C.8.3 - LIFT BY VALIDATION, NEVER BY EMPTINESS. "0 personal rows" is an absence of
    # data, not containment: the property it claims dies the instant a personal row exists.
    # So a synthetic personal row is WRITTEN, every door is attacked, and none may return
    # it. The fixture is removed afterwards and its removal is itself verified.
    #
    # AND EVERY NEGATIVE PROBE CARRIES A POSITIVE CONTROL - see Resolve-DoorVerdict. An
    # OPS-labelled twin of the fixture is written at the same moment, through the same
    # statement shape, into the same table. A door passes only when it RETURNS the ops twin
    # and REFUSES the personal one. Without that control, three of this clause's doors
    # could not have failed under any boundary state: agent_memories was never written to,
    # the thought_entities probe was an unfiltered 200-row window over 54,050 rows that
    # could not reach the fixture's id, and wiki_pages cannot hold a page for a row
    # inserted seconds earlier. All three reported "attacked and it did not come back".
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 3
    $doors = @($script:DfuRequiredDoors.Keys)
    $c.coverage.subject  = "the doors C.8 clause 3 names, the corpus predicate, the PostgREST surface derived from the live schema, and the door set itself"
    $c.coverage.expected = $doors.Count + @($script:DfuClause3Extras).Count

    # --- (0) THE DOOR SET STILL MATCHES THE PLAN'S OWN WORDS ----------------------
    # A pinned list is only honest while something checks it against what it is pinned to.
    # Every backticked identifier inside C.8 clause 3, plus the three door names that
    # section writes in prose, must be claimed by a subject above.
    # AND THIS WAS THE SIBLING THAT GOT LEFT. Round 4 anchored phase-floor-matches-plan and
    # service-set-matches-plan to section C.8 and made ambiguity REFUSE - and left this third
    # instance of the identical read running a lazy FIRST-MATCH regex over the WHOLE plan. A
    # decoy passage earlier in PLAN.md - a quotation of clause 3, an example, a superseded
    # draft - becomes the text the door floor is checked back against, and the drift check
    # reports agreement over the wrong paragraph. Three sites, one shape; all three are now
    # anchored and all three refuse on ambiguity.
    $planTxt = Get-SnapMd -Which "plan"
    $sec = ""
    $whyDoor = "C.8 clause 3's text could not be located in the plan, so the door set could not be checked against it"
    if ($planTxt) {
        $c8d = Get-DfuSection -Text $planTxt -HeadingPattern '(?m)^###\s+C\.8\b[^\r\n]*'
        if ($c8d.count -ne 1) {
            $whyDoor = ("section C.8's heading matched {0} time(s) in the plan, so clause 3 could not be located UNAMBIGUOUSLY - one match is a location, two is a question nobody answered" -f $c8d.count)
        } else {
            $m3s = @([regex]::Matches($c8d.text, '(?s)3\.\s\*\*The personal-plane constraint.*?(?=\n4\.\s\*\*Nothing is left in flight)'))
            if ($m3s.Count -ne 1) {
                $whyDoor = ("clause 3's text matched {0} time(s) inside section C.8, so the door floor could not be checked back against ONE paragraph" -f $m3s.Count)
            } else { $sec = $m3s[0].Value }
        }
    }
    if (-not $sec) {
        $c.probes += (New-Probe -Name "door-set-matches-plan" -Command ("locate section C.8 in {0}, then clause 3 inside it" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note $whyDoor))
    } else {
        $claimed = @()
        foreach ($k in $script:DfuDoorAnchors.Keys) { $claimed += @($script:DfuDoorAnchors[$k]) }
        $claimed = @($claimed | Where-Object { $_ })
        $named = @()
        foreach ($mm in [regex]::Matches($sec, '`([^`]+)`')) { $named += $mm.Groups[1].Value }
        # The three doors C.8 names in prose rather than in backticks. Quoted here and
        # asserted to be PRESENT, so a plan that stops naming them turns this red rather
        # than quietly shrinking the floor.
        foreach ($phrase in @("openbrain-mcp", "wiki compiler", "MCP read tools")) {
            if ($sec.Contains($phrase)) { $named += $phrase }
        }
        $named = @($named | Sort-Object -Unique)
        $unclaimed = @($named | Where-Object { $claimed -notcontains $_ })
        if ($unclaimed.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("every one of the {0} door/predicate name(s) C.8 clause 3 writes is claimed by a subject of this clause" -f $named.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $unclaimed.Count `
                    -Note ("C.8 clause 3 names {0} that no probe here claims: {1} - a door the plan names and this script does not attack" -f `
                           $unclaimed.Count, ($unclaimed -join ", "))
        }
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        $c.probes += (New-Probe -Name "door-set-matches-plan" `
                      -Command ("extract backticked identifiers from C.8 clause 3 in {0} and compare with this clause's subjects" -f $Ctx.plan) -Run $body)
    }

    # --- (0b) THE PREDICATE'S SOURCE MUST BE ON THE WORK LINE --------------------
    # C.8.3 requires the backfill AND the flip to LAND. This is the "landed" half in the
    # repository sense, and it is the SAME guard clause 4's RLS twin already carried and
    # this clause did not: the behaviour probe below passed while
    # docker/init-agent-memory-corpus-failclosed.sql was ABSENT from the pinned OB1 tree
    # and its OB1 commit was not even on the OB1 remote - a boundary running in production
    # from code no fresh clone of this repository can reproduce.
    $srcRel = "docker/init-agent-memory-corpus-failclosed.sql"
    $src = Test-Ob1SourceOnWorkLine -Ctx $Ctx -RelPath $srcRel
    if (-not $src.known) {
        $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("corpus-predicate-source-on-work-line")
        $bsrc = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                -Note ("the pinned OB1 tree could not be inspected, so the predicate's source was NOT checked: {0}" -f $src.why)
    } elseif ($src.ok) {
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        $bsrc = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                -Note ("{0} is in the OB1 tree the work line pins ({1})" -f $srcRel, (Get-ShortRef -Sha $src.pin))
    } else {
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        $bsrc = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                -Note ("{0} - the fail-closed predicate is not defined by any code this work line pins, so whatever is live came from somewhere else" -f $src.why)
    }
    $c.probes += (New-Probe -Name "corpus-predicate-source-on-work-line" `
                  -Command ("git ls-tree {0} OB1 ; git -C OB1 cat-file -e <pin>:{1}" -f $Ctx.workline, $srcRel) -Run $bsrc)

    if ($Ctx.skiplive) {
        foreach ($d in $doors) {
            $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: -SkipLive)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "-SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed"))
        }
        $c.coverage.not_evaluated = @($doors)
        foreach ($n in @($script:DfuClause3Extras | Where-Object { $script:DfuClause3ExtrasOffline -notcontains $_ })) {
            $c.probes += (New-Probe -Name $n -Command "(not run: -SkipLive)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive was passed"))
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($n)
        }
        return (Resolve-ClauseVerdict -Clause $c)
    }

    # --- the corpus predicate must be FAIL-CLOSED, MEASURED AGAINST A CONTROL -----
    # An unlabelled row must NOT be on the ops plane. Measured by BEHAVIOUR - rows inserted
    # inside a transaction and rolled back - never by reading the function body, because a
    # predicate can be correct in text and shadowed in effect. The ops-labelled row in the
    # same transaction is the control: if IT is invisible too, the query is broken and the
    # unlabelled row's invisibility proves nothing.
    #
    # THE PROBE BRANCHES ON THE SCHEMA, AND THE BRANCH IS DERIVED, NOT ASSUMED. C.9 H3
    # (operator 2026-08-31) made `exposure` a NOT NULL CHECKed COLUMN, which closes this
    # class one layer earlier: an unlabelled row is not invisible, it is UNWRITABLE. So on a
    # database where the column is enforced this probe asserts the stronger property - the
    # write is REFUSED - and on a database where it is not, it asserts the original one. The
    # branch is read from information_schema at run time; it is not a flag, and neither arm
    # can be reached by choice. A probe that only knew the older world would go
    # INDETERMINATE against H3 (the INSERT raises, psql exits non-zero) and this clause
    # would silently stop being decided - which is rule 2's failure, arriving through a
    # schema change rather than through a code change.
    $canary = "DFU-DONE-CANARY-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    # TWO facts, not one, because they differ and the difference matters. The column can be
    # PRESENT and inert - which is exactly what reverting 195 leaves behind, since class 4
    # forbids dropping a column - and a fixture that named it would then still work while the
    # predicate arm below must not take the enforced branch.
    $rCol = Invoke-Psql -Ctx $Ctx -Sql ("SELECT count(*) FILTER (WHERE true) || '/' || " +
        "count(*) FILTER (WHERE is_nullable='NO') FROM information_schema.columns " +
        "WHERE table_schema='public' AND table_name='thoughts' AND column_name='exposure';")
    $colState = if ($rCol.ran -and $rCol.exit -eq 0) { ($rCol.out -replace "\s+", "") } else { "" }
    $script:DfuH3ColPresent = ($colState -match '^1/')
    $h3Enforced = ($colState -eq '1/1')

    if ($h3Enforced) {
        # THE H3 WORLD. One transaction, three statements, and the exception handler is what
        # makes the refusal a RESULT rather than an aborted script: a bare failing INSERT
        # would abort the transaction and psql would exit non-zero, which this file's rule 1
        # correctly reads as indeterminate rather than as a pass.
        #   U: did the unlabelled write get REFUSED (1) or accepted (0)?
        #   O: is an ops-labelled row visible to the agent plane? (the live positive control)
        # A run where O is 0 is indeterminate exactly as before - a query that can see
        # nothing proves nothing about what it cannot see.
        $sqlPred = ("BEGIN; " +
            "DO $x$ BEGIN " +
            "  INSERT INTO thoughts (content, metadata) VALUES ('{0}-UNLABELLED', jsonb_build_object('exposure','ops')); " +
            "  RAISE NOTICE 'DFU-U:0'; " +
            "EXCEPTION WHEN not_null_violation THEN RAISE NOTICE 'DFU-U:1'; END $x$; " +
            "INSERT INTO thoughts (content, metadata, exposure) VALUES ('{0}-OPS', jsonb_build_object('exposure','ops'), 'ops'); " +
            "SET ROLE service_role; " +
            "SELECT 'O:'||count(*) FROM thoughts WHERE content='{0}-OPS'; " +
            "RESET ROLE; ROLLBACK;") -f $canary
    } else {
        $sqlPred = ("BEGIN; " +
            "INSERT INTO thoughts (content, metadata) VALUES ('{0}-UNLABELLED','{{}}'::jsonb); " +
            "INSERT INTO thoughts (content, metadata) VALUES ('{0}-OPS', jsonb_build_object('exposure','ops')); " +
            "SET ROLE service_role; " +
            "SELECT 'U:'||count(*) FROM thoughts WHERE content='{0}-UNLABELLED'; " +
            "SELECT 'O:'||count(*) FROM thoughts WHERE content='{0}-OPS'; " +
            "RESET ROLE; ROLLBACK;") -f $canary
    }
    $rp = Invoke-Psql -Ctx $Ctx -Sql $sqlPred
    if (-not $rp.ran -or $null -eq $rp.exit) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "the database could not be reached, so the predicate was not tested"
    } elseif ([int]$rp.exit -ne 0) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note ("psql exited non-zero: {0}" -f (($rp.out -replace "\s+", " ").Trim()))
    } else {
        # In the H3 arm the unlabelled result arrives as a psql NOTICE (`DFU-U:1`), because a
        # refusal cannot be a SELECT: the statement that would have produced one is the
        # statement that raised. Both spellings are accepted here and the arm that produced
        # it is named in the verdict, so the output says WHICH property was measured.
        $u = (($rp.out -split "`n") | ForEach-Object { $_.Trim() } |
              Where-Object { $_ -match '^U:\d+$' } | Select-Object -First 1)
        if (-not $u) {
            $un = (($rp.out -split "`n") | ForEach-Object { $_.Trim() } |
                   Where-Object { $_ -match 'DFU-U:\d+' } | Select-Object -First 1)
            if ($un) { $u = "U:" + ([regex]::Match($un, 'DFU-U:(\d+)').Groups[1].Value) }
        }
        $o = (($rp.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^O:\d+$' } | Select-Object -First 1)
        if (-not $u -or -not $o) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note "the query returned no counts at all - nothing was decided"
        } else {
            $un = [int]($u -replace '^U:', ''); $on = [int]($o -replace '^O:', '')
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            if ($on -lt 1) {
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit 0 `
                        -Note "the OPS control row was invisible too, so this query cannot see anything - the unlabelled row's absence proves nothing"
            } elseif ($h3Enforced -and $un -eq 1) {
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                        -Note "the ops control row IS visible to the agent plane, and an UNLABELLED write is REFUSED BY THE DATABASE (not_null_violation on thoughts.exposure) - the class is closed at the write, one layer earlier than at the read. C.9 H3, measured."
            } elseif ($h3Enforced) {
                $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                        -Note "thoughts.exposure is NOT NULL, yet an UNLABELLED write was ACCEPTED - the column is present and not enforcing, which is worse than its absence because everything downstream reads it as authoritative"
            } elseif ($un -eq 0) {
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                        -Note "the ops control row IS visible to the agent plane and the UNLABELLED row is not - the predicate is fail-closed, measured"
            } else {
                $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                        -Note ("an UNLABELLED row is VISIBLE to the agent plane ({0} row(s)) - unlabelled defaults to fine, which is the class this clause names" -f $un)
            }
        }
    }
    $c.probes += (New-Probe -Name "corpus-predicate-fail-closed" `
                  -Command ("docker exec {0} psql -tAc <in one tx: insert an UNLABELLED row and an OPS control row; SET ROLE service_role; count both; ROLLBACK>" -f $Ctx.db) -Run $body)

    # --- AND THE BACKFILL MUST HAVE LANDED --------------------------------------
    # A FLIP WITHOUT A BACKFILL SILENTLY HIDES THE UNLABELLED CORPUS. The probe above shows
    # only that the predicate is fail-closed; C.8.3 asks for both, in its own words - "a
    # one-time backfill of unlabelled rows to exposure='ops', then flipping the function.
    # Until both land, this clause fails." Measuring one and reporting on both is how
    # ~13k rows could go invisible while this clause read green.
    $sqlBf = ("SELECT 'T:'||count(*) FROM thoughts WHERE metadata->>'exposure' IS NULL; " +
              "SELECT 'M:'||count(*) FROM agent_memories WHERE metadata->>'exposure' IS NULL;")
    $rb = Invoke-Psql -Ctx $Ctx -Sql $sqlBf
    $bt = (($rb.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^T:[0-9]+$' } | Select-Object -First 1)
    $bm = (($rb.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^M:[0-9]+$' } | Select-Object -First 1)
    if (-not $rb.ran -or $null -eq $rb.exit -or [int]$rb.exit -ne 0 -or -not $bt -or -not $bm) {
        $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("corpus-backfill-landed")
        $bodyBf = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rb.exit `
                  -Note "the unlabelled-row counts could not be read, so whether the backfill landed was NOT decided"
    } else {
        $nt = [int]($bt -replace '^T:', ''); $nm = [int]($bm -replace '^M:', '')
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if (($nt + $nm) -eq 0) {
            $bodyBf = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                      -Note "no unlabelled row remains in either corpus - the one-time backfill C.8.3 names has landed, so the flip is not hiding rows nobody labelled"
        } else {
            $bodyBf = New-VerdictProbeBody -Verdict "fail" -Exit ($nt + $nm) `
                      -Note ("{0} unlabelled thought(s) and {1} unlabelled agent_memory row(s) remain - the predicate was FLIPPED without the backfill C.8.3 requires, so those rows are now hidden rather than labelled" -f $nt, $nm)
        }
    }
    $c.probes += (New-Probe -Name "corpus-backfill-landed" `
                  -Command ("docker exec {0} psql -tAc <count rows with metadata->>'exposure' IS NULL in thoughts and agent_memories>" -f $Ctx.db) -Run $bodyBf)

    # --- write the synthetic fixture AND ITS OPS-LABELLED TWIN -------------------
    $stamp = [guid]::NewGuid().ToString("N").Substring(0, 10)
    $pmark = "DFU-DONE-PERSONAL-FIXTURE-" + $stamp
    $omark = "DFU-DONE-OPS-TWIN-" + $stamp
    # NO DOUBLE QUOTES IN THE SQL, deliberately. PowerShell 5.1 does not escape embedded
    # double quotes when it hands an argument to a native executable, so a JSON literal
    # like '{"exposure":"personal"}' arrives at psql as '{exposure:personal}' and is
    # rejected as invalid JSON. The insert then failed, every door reported "the fixture
    # could not be written", and the clause looked merely unevaluated rather than broken.
    # jsonb_build_object expresses the same object using single quotes only.
    #
    # THE TWINS DIFFER ONLY IN THE VARIABLE UNDER TEST, and the previous pair did not.
    # The ops twin was given share=cloud and the personal fixture was NOT, while the cloud
    # gateway's forced read filter is `share`, not `exposure` (openbrain-gateway/app.py:83,
    # _force_read_filter). So the cloud door's "pass" was produced by the WRONG VARIABLE:
    # it excluded the personal row because it lacked share=cloud, and would have done that
    # whatever the exposure boundary did. Proved live - a thought with exposure=personal
    # AND share=cloud IS returned by that door, HTTP 200.
    #
    # Both twins now carry share=cloud and the same fixture type; the ONLY field that
    # differs is `exposure`, which is the field every door here is supposed to bind on. A
    # control the gateway would filter out is not a control, and neither is a fixture the
    # gateway filters out for a reason the boundary had no part in.
    #
    # metadata.type is the FILTER every probe uses. list_thoughts takes `type`
    # (OB1 integrations/kubernetes-deployment/index.ts, metadata @> {type}), so the MCP
    # doors can be aimed at the fixture instead of asking for an unfiltered newest-first
    # window that concurrent ingest can push the fixture out of.
    $ftype = "dfu-done-fixture-" + $stamp
    $ids = [ordered]@{}
    foreach ($pair in @(@("p", $pmark, "personal"), @("o", $omark, "ops"))) {
        $tag = $pair[0]; $mk = $pair[1]; $exp = $pair[2]
        # `exposure` IS A COLUMN since DFU C.9 H3 (operator 2026-08-31): NOT NULL, CHECKed
        # IN ('ops','personal'), no default. A fixture that named only the jsonb mirror
        # would not be written at all - and the door probes below would then be attacking
        # nothing, which is this file's own rule 5 failing from the fixture end. Both are
        # written, from the SAME variable, so the column and the mirror cannot disagree.
        $q = if ($script:DfuH3ColPresent) {
            "INSERT INTO thoughts (content, metadata, exposure) VALUES ('{0} {1} fixture', jsonb_build_object('exposure','{1}','share','cloud','type','{2}','dfu_done_fixture',true), '{1}') RETURNING id;" -f $mk, $exp, $ftype
        } else {
            "INSERT INTO thoughts (content, metadata) VALUES ('{0} {1} fixture', jsonb_build_object('exposure','{1}','share','cloud','type','{2}','dfu_done_fixture',true)) RETURNING id;" -f $mk, $exp, $ftype
        }
        $r = Invoke-Psql -Ctx $Ctx -Sql $q
        if ($r.ran -and $r.exit -eq 0) {
            $mm = [regex]::Match(($r.out -replace "\s+", " "), '(\d+)')
            if ($mm.Success) { $ids[($tag + "thought")] = $mm.Groups[1].Value }
        }
        $q2 = if ($script:DfuH3ColPresent) {
            ("INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata, exposure) " +
             "VALUES ('dfu-done-fixture','check','{0} {1} twin','{0} {1} fixture body', " +
             "jsonb_build_object('exposure','{1}','share','cloud','type','{2}','dfu_done_fixture',true), '{1}') RETURNING id;") -f $mk, $exp, $ftype
        } else {
            ("INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata) " +
             "VALUES ('dfu-done-fixture','check','{0} {1} twin','{0} {1} fixture body', " +
             "jsonb_build_object('exposure','{1}','share','cloud','type','{2}','dfu_done_fixture',true)) RETURNING id;") -f $mk, $exp, $ftype
        }
        $r2 = Invoke-Psql -Ctx $Ctx -Sql $q2
        if ($r2.ran -and $r2.exit -eq 0) {
            $mm2 = [regex]::Match($r2.out, '([0-9a-f-]{36})')
            if ($mm2.Success) { $ids[($tag + "mem")] = $mm2.Groups[1].Value }
        }
    }
    # ONE entity, linked to BOTH thoughts. The join door is then asked about that entity,
    # which is a MEASUREMENT of the fixture rather than a window of unrelated rows: the
    # previous probe asked for 200 unfiltered thought_entities rows covering thought_ids
    # 3..71 while the fixture sat at ~13,386, so it could not have returned the fixture
    # under any boundary state and its pass was STRUCTURAL.
    $ent = ""
    # THE ENTITY'S NAME MUST NOT CARRY THE PERSONAL MARKER. It did, and the surface sweep
    # then found the marker in `entities` and reported a leak - this script detecting a row
    # it had just written itself. A probe that manufactures its own hit is as useless as one
    # that cannot hit at all, in the opposite direction.
    $re = Invoke-Psql -Ctx $Ctx -Sql ("INSERT INTO entities (entity_type, canonical_name, normalized_name, metadata) VALUES ('tool','DFU-DONE-FIXTURE-ENTITY-{0}','dfu-done-fixture-entity-{0}', jsonb_build_object('dfu_done_fixture',true)) RETURNING id;" -f $stamp)
    if ($re.ran -and $re.exit -eq 0) {
        $mm3 = [regex]::Match(($re.out -replace "\s+", " "), '(\d+)')
        if ($mm3.Success) { $ent = $mm3.Groups[1].Value }
    }
    if ($ent) {
        [void](Invoke-Psql -Ctx $Ctx -Sql ("INSERT INTO thought_entities (thought_id, entity_id) SELECT t.id, {0} FROM thoughts t WHERE t.metadata->>'dfu_done_fixture'='true' ON CONFLICT DO NOTHING;" -f $ent))
    }
    [void](Invoke-Psql -Ctx $Ctx -Sql "INSERT INTO entity_extraction_queue (thought_id) SELECT id FROM thoughts WHERE metadata->>'dfu_done_fixture'='true' ON CONFLICT DO NOTHING;")

    # NOT $pid. That is a PowerShell AUTOMATIC variable holding this process's id: the
    # assignment fails with a non-terminating error, the name keeps the process id, and the
    # "could the fixture be written?" guard below sees a non-empty string and marches on to
    # attack every door with a thought_id that does not exist. Found by the drill's
    # unreachable-plane step, which is the only reason it is not still here.
    $pRow = $(if ($ids.Contains("pthought")) { [string]$ids["pthought"] } else { "" })
    $oRow = $(if ($ids.Contains("othought")) { [string]$ids["othought"] } else { "" })
    $c.detail += ("personal fixture: marker={0} thought_id={1} memory_id={2}" -f $pmark, $pRow, $(if ($ids.Contains("pmem")) { $ids["pmem"] } else { "-" }))
    $c.detail += ("ops CONTROL twin: marker={0} thought_id={1} memory_id={2} entity_id={3}" -f $omark, $oRow, $(if ($ids.Contains("omem")) { $ids["omem"] } else { "-" }), $ent)

    # --- A POSITIVE CONTROL ON THE **WRITE**, not only on the read ---------------
    # EVERY FIXTURE IS READ BACK BEFORE ANYTHING ASSERTS ON ITS ABSENCE. The guard above
    # checked the THOUGHTS ids only: the agent_memories ids were printed and never gated,
    # and the insert result was never verified at all. If a WRITE-side boundary rejects
    # exposure='personal' - which is exactly the direction U5 is briefed to build - the
    # personal row never reaches the table, the agent_memories door finds nothing, and the
    # door reports "attacked with the fixture and it did not come back". A read-side
    # control cannot see that; only reading the write back can.
    $landed = @{}
    $sqlLanded = ("SELECT 'TP:'||count(*) FROM thoughts WHERE content LIKE '%{0}%'; " +
                  "SELECT 'TO:'||count(*) FROM thoughts WHERE content LIKE '%{1}%'; " +
                  "SELECT 'MP:'||count(*) FROM agent_memories WHERE content LIKE '%{0}%'; " +
                  "SELECT 'MO:'||count(*) FROM agent_memories WHERE content LIKE '%{1}%';") -f $pmark, $omark
    $rl = Invoke-Psql -Ctx $Ctx -Sql $sqlLanded
    foreach ($ln in ($rl.out -split "`n")) {
        $t = $ln.Trim()
        if ($t -match '^(TP|TO|MP|MO):([0-9]+)$') { $landed[$Matches[1]] = [int]$Matches[2] }
    }
    $wantKeys = @("TP", "TO", "MP", "MO")
    $absent = @($wantKeys | Where-Object { -not $landed.ContainsKey($_) -or [int]$landed[$_] -lt 1 })
    $memOk = ($landed.ContainsKey("MP") -and $landed.ContainsKey("MO") -and [int]$landed["MP"] -ge 1 -and [int]$landed["MO"] -ge 1)
    if (-not $rl.ran -or $null -eq $rl.exit -or [int]$rl.exit -ne 0 -or $landed.Count -lt 4) {
        $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("fixture-write-landed")
        $bodyW = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rl.exit `
                 -Note "the fixture rows could not be read back, so it is not known whether anything this clause asserts about was ever written"
    } elseif ($absent.Count -gt 0) {
        # NOT a fail: nothing about the PLAN was decided. A fixture that did not land means
        # this clause could not run, and "could not run" refuses.
        $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("fixture-write-landed")
        $bodyW = New-VerdictProbeBody -Verdict "indeterminate" -Exit 1 `
                 -Note ("{0} of the 4 fixture rows did not land ({1}) - a write-side boundary may have rejected them, and a door asked about a row that was never written cannot show anything" -f `
                        $absent.Count, ($absent -join ","))
    } else {
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        $bodyW = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                 -Note "both twins landed in BOTH corpora - every absence asserted below is an absence of something that exists"
    }
    $c.probes += (New-Probe -Name "fixture-write-landed" `
                  -Command ("docker exec {0} psql -tAc <read back both markers from thoughts AND agent_memories>" -f $Ctx.db) -Run $bodyW)

    try {
        if (-not $pRow -or -not $oRow) {
            foreach ($d in $doors) {
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: the fixture or its control could not be written)" `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "the personal fixture or its ops control could not be written, so no door was actually attacked - this clause cannot be met by an empty plane"))
            }
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($doors)
            $c.probes += (New-Probe -Name "postgrest-surface-sweep" -Command "(not run: no fixture)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "no fixture to sweep for"))
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("postgrest-surface-sweep")
            return (Resolve-ClauseVerdict -Clause $c)
        }

        # The fingerprint of the hidden content. A HASH IS A DISCLOSURE: a door that
        # returns a digest of a protected row has leaked it, and comparing the marker text
        # alone would call that door closed.
        $fp = ""
        $rf = Invoke-Psql -Ctx $Ctx -Sql ("SELECT encode(digest(content,'sha256'),'hex') FROM thoughts WHERE id={0};" -f $pRow)
        if ($rf.ran -and $rf.exit -eq 0) {
            $mm = [regex]::Match($rf.out, '([0-9a-f]{64})')
            if ($mm.Success) { $fp = $mm.Groups[1].Value }
        }

        # EVERY POSTGREST PROBE IS FILTERED TO THE FIXTURE, and every one is paired with the
        # same query aimed at the ops twin.
        $pairs = [ordered]@{
            "postgrest-thoughts"         = @(("http://{0}/thoughts?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $pmark),
                                             ("http://{0}/thoughts?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $omark))
            "postgrest-agent-memories"   = @(("http://{0}/agent_memories?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $pmark),
                                             ("http://{0}/agent_memories?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $omark))
            "postgrest-thought-entities" = @(("http://{0}/thought_entities?entity_id=eq.{1}&select=thought_id,thoughts(content)" -f $Ctx.postgrest, $ent),
                                             ("http://{0}/thought_entities?entity_id=eq.{1}&select=thought_id,thoughts(content)" -f $Ctx.postgrest, $ent))
            "postgrest-derived-queue"    = @(("http://{0}/entity_extraction_queue?thought_id=eq.{1}&select=thought_id,source_fingerprint" -f $Ctx.postgrest, $pRow),
                                             ("http://{0}/entity_extraction_queue?thought_id=eq.{1}&select=thought_id,source_fingerprint" -f $Ctx.postgrest, $oRow))
        }

        foreach ($d in $doors) {
            # THE agent_memories DOOR NEEDS ITS OWN FIXTURE, not the thoughts one. Its ids
            # were printed and never gated; with the personal row absent the door had
            # nothing to return and said so as containment.
            if ($d -eq "postgrest-agent-memories" -and -not $memOk) {
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: the agent_memories fixture or its control did not land)" `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "the agent_memories twins were not both written, so this door was asked about a row that does not exist - unevaluated, never closed"))
                continue
            }
            if ($pairs.Contains($d)) {
                $u = @($pairs[$d])
                $rc = Invoke-Curl -Ctx $Ctx -Url $u
                $a = Get-CurlResult -Res $rc -Index 0
                $b = Get-CurlResult -Res $rc -Index 1
                $leak = "the personal fixture"
                $sawP = $false
                $sawO = $false
                if ($d -eq "postgrest-thought-entities") {
                    # BOTH halves come from the SAME response here - the join is asked about
                    # the one entity that links both thoughts. A leak is either the personal
                    # content itself or the mere existence of a row pointing at the hidden
                    # thought, which is derived data about a protected row - the same
                    # standard C.8 already applies to entity_extraction_queue.
                    $sawContent = ($a.body -match [regex]::Escape($pmark)) -or ($fp -and ($a.body -match [regex]::Escape($fp)))
                    $sawLink    = ($a.body -match ('"thought_id"\s*:\s*' + [regex]::Escape($pRow) + '\b'))
                    $sawP = ($sawContent -or $sawLink)
                    if ($sawContent) { $leak = "the personal fixture's CONTENT through the join" }
                    elseif ($sawLink) { $leak = ("a row pointing at the hidden thought {0} - derived data about a protected row" -f $pRow) }
                    $sawO = ($a.body -match [regex]::Escape($omark)) -or ($a.body -match ('"thought_id"\s*:\s*' + [regex]::Escape($oRow) + '\b'))
                    $b = $a
                } elseif ($d -eq "postgrest-derived-queue") {
                    $sawP = ($a.body -match ('"thought_id"\s*:\s*' + [regex]::Escape($pRow) + '\b'))
                    $sawO = ($b.body -match ('"thought_id"\s*:\s*' + [regex]::Escape($oRow) + '\b'))
                    $leak = ("the queue row for the hidden thought {0} - derived data about a protected row" -f $pRow)
                } else {
                    $sawP = ($a.body -match [regex]::Escape($pmark)) -or ($fp -and ($a.body -match [regex]::Escape($fp)))
                    $sawO = ($b.body -match [regex]::Escape($omark))
                }
                $reach = ($a.reachable -and $b.reachable)
                $st = $a.status
                if ($reach -and $b.status -ne 200) { $st = $b.status }
                $v = Resolve-DoorVerdict -Reachable $reach -Status $st -SawPersonal $sawP -SawOps $sawO -Leak $leak
                if ($v.verdict -eq "indeterminate") { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d) }
                else { $c.coverage.evaluated = [int]$c.coverage.evaluated + 1 }
                $body = New-VerdictProbeBody -Verdict $v.verdict -Exit $st -Note ("HTTP {0}; {1}" -f $st, $v.note)
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command ("curl (from {0}) '{1}' AND its ops-control twin '{2}'" -f $Ctx.obnet, $u[0], $u[1]) -Run $body)
                continue
            }

            if ($d -eq "openbrain-mcp-door") {
                # THE DOOR IS CALLED, not inferred about. The previous probe read the DB
                # role's flags and decided from them - and its own comparison was wrong:
                # `rolsuper::text||'/'||rolbypassrls::text` yields "false/true", which
                # matched neither '^t' nor '/t$', so a role with BYPASSRLS was printed as
                # "bound by the boundary". The flags are still reported, as corroboration,
                # but the verdict now comes from what the door RETURNED.
                $key = Get-ContainerEnv -Container "openbrain-mcp" -Name "BRAIN_KEY"
                if (-not $key) { $key = Get-ContainerEnv -Container "openbrain-gateway" -Name "OPENBRAIN_KEY" }
                $role = Get-ContainerEnv -Container "openbrain-mcp" -Name "DB_USER"
                $flags = "unknown"
                if ($role -match '^[A-Za-z0-9_]+$') {
                    $rr = Invoke-Psql -Ctx $Ctx -Sql ("SELECT (CASE WHEN rolsuper THEN 't' ELSE 'f' END)||'/'||(CASE WHEN rolbypassrls THEN 't' ELSE 'f' END) FROM pg_roles WHERE rolname='{0}';" -f $role)
                    $f = (($rr.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[tf]/[tf]$' } | Select-Object -First 1)
                    if ($f) { $flags = $f }
                }
                $c.detail += ("openbrain-mcp connects as '{0}' (rolsuper/rolbypassrls = {1})" -f $role, $flags)
                if (-not $key) {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                            -Note "the door's key could not be read, so the door was NOT called - unknown is not closed"
                    $cmd = "docker exec openbrain-mcp printenv BRAIN_KEY"
                } else {
                    # FILTERED TO THE FIXTURE. `{"limit":25}` is an unfiltered
                    # newest-first window, and because the personal fixture is written
                    # BEFORE the ops twin it is always the first of the pair to fall out of
                    # it - so concurrent ingest manufactures sawP=false, sawO=true, which
                    # this file reads as "the door REFUSED the personal fixture". A door
                    # that passes because the plane was busy is a green check checking
                    # nothing, in the form hardest to notice.
                    $r = Invoke-McpTool -Ctx $Ctx -Url "http://openbrain-mcp:8000/mcp" -Header ("x-brain-key: " + $key) `
                                        -Secret $key -Tool "list_thoughts" `
                                        -ArgumentsJson ('{"limit":25,"type":"' + $ftype + '"}')
                    $sawP = ($r.body -match [regex]::Escape($pmark))
                    $sawO = ($r.body -match [regex]::Escape($omark))
                    $v = Resolve-DoorVerdict -Reachable ([bool]$r.ran) -Status ([int]$r.status) -SawPersonal $sawP -SawOps $sawO
                    if ($v.verdict -eq "indeterminate") { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d) }
                    else { $c.coverage.evaluated = [int]$c.coverage.evaluated + 1 }
                    $body = New-VerdictProbeBody -Verdict $v.verdict -Exit ([int]$r.status) `
                            -Note ("HTTP {0}; {1}; the door connects as '{2}' (rolsuper/rolbypassrls = {3})" -f $r.status, $v.note, $role, $flags)
                    $cmd = $r.command
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command $cmd -Run $body)
                continue
            }

            if ($d -eq "cloud-search-thoughts") {
                # THROUGH THE GATEWAY THAT FRONTS IT, which is what C.8 asks for. The
                # previous probe ran `SET ROLE service_role` in the database - a role the
                # cloud lane does not use at all (the gateway proxies openbrain-mcp, which
                # connects as postgres), so it measured a boundary that is not this door's.
                #
                # list_thoughts rather than search_thoughts: search is embedding-backed and
                # returns nothing for a row inserted seconds ago, so it can produce no
                # positive control. A tool that cannot return the control cannot show the
                # door is bound.
                $key = Get-ContainerEnv -Container "openbrain-gateway" -Name "GATEWAY_KEY"
                if (-not $key) {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                            -Note "the cloud gateway's key could not be read (is the container running?), so cloud reads were NOT attacked"
                    $cmd = "docker exec openbrain-gateway printenv GATEWAY_KEY"
                } else {
                    # FILTERED TO THE FIXTURE, same reason as the door above. The
                    # gateway's forced metadata_filter (share=cloud) is applied on top of
                    # this type filter, and BOTH twins carry share=cloud - so the only
                    # thing left that can separate them is `exposure`, which is what this
                    # door is supposed to bind on.
                    $r = Invoke-McpTool -Ctx $Ctx -Url "http://openbrain-gateway:8061/mcp" -Header ("Authorization: Bearer " + $key) `
                                        -Secret $key -Tool "list_thoughts" `
                                        -ArgumentsJson ('{"limit":25,"type":"' + $ftype + '"}')
                    $sawP = ($r.body -match [regex]::Escape($pmark))
                    $sawO = ($r.body -match [regex]::Escape($omark))
                    $v = Resolve-DoorVerdict -Reachable ([bool]$r.ran) -Status ([int]$r.status) -SawPersonal $sawP -SawOps $sawO
                    if ($v.verdict -eq "indeterminate") { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d) }
                    else { $c.coverage.evaluated = [int]$c.coverage.evaluated + 1 }
                    $body = New-VerdictProbeBody -Verdict $v.verdict -Exit ([int]$r.status) -Note ("HTTP {0}; {1}" -f $r.status, $v.note)
                    $cmd = $r.command
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command $cmd -Run $body)
                continue
            }

            if ($d -eq "mcp-read-tools") {
                # The ops door's recall lane - the MCP read tools as an agent actually calls
                # them. It CAN fail (the personal twin coming back is a fail); it currently
                # cannot pass, because recall returns neither twin and a probe with no
                # positive control refuses. That asymmetry is the correct one.
                $key = Get-ContainerEnv -Container "openbrain-ops-gateway" -Name "GATEWAY_KEY"
                if (-not $key) {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                            -Note "the ops gateway's key could not be read (is the container running?), so its read tools were NOT attacked"
                    $cmd = "docker exec openbrain-ops-gateway printenv GATEWAY_KEY"
                } else {
                    $r = Invoke-McpTool -Ctx $Ctx -Url "http://openbrain-ops-gateway:8061/mcp" -Header ("Authorization: Bearer " + $key) `
                                        -Secret $key -Tool "agent_memory_recall" `
                                        -ArgumentsJson ('{"workspace_id":"dfu-done-fixture","query":"' + $pmark + '","limit":10,"include_unconfirmed":true}')
                    $sawP = ($r.body -match [regex]::Escape($pmark))
                    $sawO = ($r.body -match [regex]::Escape($omark))
                    $v = Resolve-DoorVerdict -Reachable ([bool]$r.ran) -Status ([int]$r.status) -SawPersonal $sawP -SawOps $sawO
                    if ($v.verdict -eq "indeterminate") { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d) }
                    else { $c.coverage.evaluated = [int]$c.coverage.evaluated + 1 }
                    $body = New-VerdictProbeBody -Verdict $v.verdict -Exit ([int]$r.status) -Note ("HTTP {0}; {1}" -f $r.status, $v.note)
                    $cmd = $r.command
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command $cmd -Run $body)
                continue
            }

            if ($d -eq "wiki-compiler-output") {
                # A NAMED MANUAL CHECK, because the machine probe that used to stand here
                # COULD NOT FAIL: the compiler cannot have published a page for a row
                # inserted seconds earlier, so querying wiki_pages for the fixture returned
                # nothing no matter what the boundary did. C.8 allows a clause that cannot
                # be machine-evaluated to be a named manual check the script refuses to pass
                # without a recorded result; it does not allow a probe that is green by
                # construction.
                [void](Add-ManualCheck -Clause $c -Store $Store -Name "wiki-compiler-personal-exclusion" `
                    -What ("Drive the wiki compiler over a corpus containing a personal-labelled thought and confirm no published wiki_pages row carries its content. This script cannot do it: the compiler runs on its own schedule, so a query for a seconds-old fixture returns nothing whatever the boundary does. Record the run, the fixture id, and the query you ran."))
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(no probe: see the named manual check wiki-compiler-personal-exclusion)" `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "this door cannot be driven from here, so it is a NAMED MANUAL CHECK - a probe that cannot fail was removed rather than kept"))
                continue
            }

            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
            $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(no probe implemented)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "this door is declared in code but has no probe - declared and unimplemented is UNEVALUATED, never closed"))
        }

        # --- THE WHOLE POSTGREST SURFACE, DERIVED FROM THE LIVE SCHEMA ------------
        # The floor above is C.8's list. This sweep is the answer to "and what about the
        # table nobody thought of": every path PostgREST exposes is read from its own
        # OpenAPI document, every text-ish column of each is filtered for the personal
        # marker, and ANY row coming back fails. Its own positive control is the same sweep
        # aimed at the ops twin - if that returns nothing, the sweep is not working and it
        # refuses rather than reporting a clean surface.
        # WHAT POSTGREST EXPOSES comes from PostgREST, and the columns come from the live
        # schema. The OpenAPI document is read for its PATH LIST with a regex rather than a
        # JSON parser on purpose: PowerShell 5.1's ConvertFrom-Json rejects this document
        # outright ("the value of argument name is not valid"), and a parse failure here
        # silently degraded the whole sweep to "could not be enumerated". Two narrow reads
        # that work beat one wide one that does not.
        $spec = Invoke-Curl -Ctx $Ctx -Url @(("http://{0}/" -f $Ctx.postgrest))
        $sp = Get-CurlResult -Res $spec -Index 0
        $swept = @()
        $noText = @()
        $paths = @()
        if ($sp.reachable -and $sp.status -eq 200) {
            foreach ($mm in [regex]::Matches($sp.body, '"/([A-Za-z0-9_]+)"\s*:\s*\{')) { $paths += $mm.Groups[1].Value }
            $paths = @($paths | Sort-Object -Unique)
        }
        # EVERY COLUMN THAT CAN CARRY TEXT, JSONB INCLUDED, AND NO CAP. The previous sweep
        # took text/varchar/char/name and then only the FIRST FOUR such columns per table,
        # and never swept jsonb at all - while its verdict said "56 exposed table(s) swept
        # for the personal marker and none returned it". That sentence was wider than the
        # evidence in the two directions that matter most here: the fifth text column of a
        # wide table, and metadata, which is where this corpus actually keeps its prose.
        #
        # jsonb IS SWEPT BY KEY, and the keys come from the LIVE DATA rather than a guess
        # list: PostgREST accepts `col->>key=like.*x*` but rejects a cast in a filter
        # (`col::text=like.*` answers 42883, measured), so a key path is the only filter
        # this door actually offers. A jsonb column that is not an object - an array like
        # wiki_pages.tags - has no keys to walk, and those are NAMED IN THE VERDICT rather
        # than silently counted as swept.
        $colMap  = @{}
        $jsonMap = @{}
        $rcols = Invoke-Psql -Ctx $Ctx -Sql ("SELECT table_name||'|'||string_agg(column_name, ',' ORDER BY ordinal_position) " +
                                             "FROM information_schema.columns WHERE table_schema='public' " +
                                             "AND data_type IN ('text','character varying','character','name') GROUP BY table_name;")
        if ($rcols.ran -and $rcols.exit -eq 0) {
            foreach ($line in ($rcols.out -split "`n")) {
                $l = $line.Trim()
                if ($l -notmatch '^([A-Za-z0-9_]+)\|(.+)$') { continue }
                $colMap[$Matches[1]] = @(($Matches[2] -split ',') | Where-Object { $_ })
            }
        }
        # table|column|key1,key2,... - one row per jsonb column, keys read from the data.
        $jsonSql = ("SELECT c.table_name||'|'||c.column_name||'|'||coalesce((" +
                    "SELECT string_agg(DISTINCT k, ',') FROM (" +
                    "SELECT (xpath('/row/k/text()', x))[1]::text AS k FROM unnest(xpath('/table/row', " +
                    "query_to_xml(format('SELECT DISTINCT k FROM (SELECT jsonb_object_keys(%I) k FROM public.%I " +
                    "WHERE jsonb_typeof(%I)=''object'' LIMIT 20000) s', c.column_name, c.table_name, c.column_name), " +
                    "false, false, ''))) AS x) y WHERE k ~ '^[A-Za-z0-9_]+$'), '') " +
                    "FROM information_schema.columns c WHERE c.table_schema='public' AND c.data_type='jsonb';")
        $rjson = Invoke-Psql -Ctx $Ctx -Sql $jsonSql
        $jsonReadable = ($rjson.ran -and $rjson.exit -eq 0)
        if ($jsonReadable) {
            foreach ($line in ($rjson.out -split "`n")) {
                $l = $line.Trim()
                if ($l -notmatch '^([A-Za-z0-9_]+)\|([A-Za-z0-9_]+)\|(.*)$') { continue }
                $tn = $Matches[1]; $cn = $Matches[2]
                $ks = @(($Matches[3] -split ',') | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                if (-not $jsonMap.ContainsKey($tn)) { $jsonMap[$tn] = @() }
                $jsonMap[$tn] = @($jsonMap[$tn]) + @(@{ column = $cn; keys = $ks })
            }
        }
        if ($paths.Count -lt 1 -or $colMap.Count -lt 1) {
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("postgrest-surface-sweep")
            $c.probes += (New-Probe -Name "postgrest-surface-sweep" -Command ("curl 'http://{0}/' ; psql information_schema.columns" -f $Ctx.postgrest) `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$sp.status) `
                      -Note ("the exposed surface could not be enumerated ({0} path(s) from PostgREST, {1} relation(s) with text columns from the schema)" -f $paths.Count, $colMap.Count)))
        } else {
            $urls = @()
            $nCols = 0
            $nKeys = 0
            $unsweepable = @()
            foreach ($t in $paths) {
                $conds = @()
                $first = ""
                foreach ($col in @($(if ($colMap.ContainsKey($t)) { $colMap[$t] } else { @() }))) {
                    if (-not $first) { $first = $col }
                    $conds += ("{0}.like.*{1}*" -f $col, $pmark)
                    $nCols++
                }
                foreach ($jc in @($(if ($jsonMap.ContainsKey($t)) { $jsonMap[$t] } else { @() }))) {
                    if (@($jc.keys).Count -lt 1) {
                        # A jsonb column with no object keys in the data - an array, a
                        # scalar, or empty. NAMED in the verdict, never counted as swept.
                        $unsweepable += ("{0}.{1}" -f $t, $jc.column)
                        continue
                    }
                    foreach ($k in @($jc.keys)) {
                        if (-not $first) { $first = $jc.column }
                        $conds += ("{0}->>{1}.like.*{2}*" -f $jc.column, $k, $pmark)
                        $nKeys++
                    }
                }
                if ($conds.Count -lt 1) { $noText += $t; continue }
                $urls += ("http://{0}/{1}?or=({2})&select={3}&limit=1" -f $Ctx.postgrest, $t, ($conds -join ","), $first)
                $swept += $t
            }
            if (-not $jsonReadable) { $unsweepable += "(every jsonb column - their keys could not be enumerated)" }
            $c.detail += ("surface sweep: {0} exposed path(s) from PostgREST, {1} swept over {2} text column(s) and {3} jsonb key(s), {4} with nothing text-bearing to filter on" -f `
                          $paths.Count, $swept.Count, $nCols, $nKeys, $noText.Count)
            foreach ($u in @($unsweepable)) { $c.detail += ("    NOT swept: {0}" -f $u) }
            $control = ("http://{0}/thoughts?content=like.*{1}*&select=id&limit=1" -f $Ctx.postgrest, $omark)
            $all = @($urls) + @($control)
            # BATCHED, because the argv would otherwise grow past what a process can be
            # started with once every column and key is in it - and a sweep that fails to
            # START measured nothing while looking exactly like a clean surface.
            $rs = Invoke-CurlMany -Ctx $Ctx -Url $all
            $hits = @()
            $unread = @()
            for ($i = 0; $i -lt $urls.Count; $i++) {
                $one = Get-CurlResult -Res $rs -Index $i
                if (-not $one.reachable -or $one.status -ne 200) { $unread += ("{0} (HTTP {1})" -f $swept[$i], $one.status); continue }
                if ($one.body -notmatch '^\s*\[\s*\]\s*$') { $hits += $swept[$i] }
            }
            $ctl = Get-CurlResult -Res $rs -Index $urls.Count
            $ctlOk = ($ctl.reachable -and $ctl.status -eq 200 -and ($ctl.body -notmatch '^\s*\[\s*\]\s*$'))
            if ($hits.Count -gt 0) {
                $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                $body = New-VerdictProbeBody -Verdict "fail" -Exit $hits.Count `
                        -Note ("{0} exposed table(s) returned the personal fixture: {1}" -f $hits.Count, ($hits -join ", "))
            } elseif (-not $ctlOk) {
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("postgrest-surface-sweep")
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$ctl.status) `
                        -Note "the sweep's own positive control did not come back, so the sweep is not measuring anything - a clean surface here would be clear because we did not look"
            } elseif ($unread.Count -gt 0) {
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("postgrest-surface-sweep")
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit 0 `
                        -Note ("{0} of {1} exposed table(s) could not be read, so the surface was only partly swept: {2}" -f `
                               $unread.Count, $swept.Count, (($unread | Select-Object -First 5) -join ", "))
            } else {
                $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                        -Note ("{0} exposed table(s) swept for the personal marker across {1} text column(s) and {2} jsonb key(s) and none returned it, with the ops control returning. NOT SWEPT, which is the restriction on that sentence: {3} table(s) expose nothing text-bearing ({4}); {5} jsonb column(s) hold no object keys to filter on ({6})" -f `
                               $swept.Count, $nCols, $nKeys, $noText.Count, `
                               $(if ($noText.Count) { $noText -join ", " } else { "none" }), `
                               @($unsweepable).Count, `
                               $(if (@($unsweepable).Count) { (@($unsweepable) -join ", ") } else { "none" }))
            }
            $c.probes += (New-Probe -Name "postgrest-surface-sweep" `
                          -Command ("curl (from {0}) one filtered query per exposed table - EVERY text column plus every jsonb key read from the live data - derived from PostgREST's own OpenAPI document, plus an ops-twin control" -f $Ctx.obnet) -Run $body)
        }
    } finally {
        # CLEAN UP. Production must show 0 personal rows when this finishes, and the
        # cleanup is VERIFIED rather than assumed - a fixture left behind would be this
        # script creating the exposure it exists to detect.
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM thought_entities WHERE entity_id IN (SELECT id FROM entities WHERE metadata->>'dfu_done_fixture'='true') OR thought_id IN (SELECT id FROM thoughts WHERE metadata->>'dfu_done_fixture'='true');")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM entities WHERE metadata->>'dfu_done_fixture'='true';")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM entity_extraction_queue WHERE thought_id IN (SELECT id FROM thoughts WHERE metadata->>'dfu_done_fixture'='true');")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM agent_memory_recall_items WHERE memory_id IN (SELECT id FROM agent_memories WHERE metadata->>'dfu_done_fixture'='true');")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM agent_memories WHERE metadata->>'dfu_done_fixture'='true';")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM thoughts WHERE metadata->>'dfu_done_fixture'='true';")
        $rv = Invoke-Psql -Ctx $Ctx -Sql ("SELECT (SELECT count(*) FROM thoughts WHERE metadata->>'dfu_done_fixture'='true')::text" +
                                          "||'/'||(SELECT count(*) FROM agent_memories WHERE metadata->>'dfu_done_fixture'='true')::text" +
                                          "||'/'||(SELECT count(*) FROM entities WHERE metadata->>'dfu_done_fixture'='true')::text" +
                                          "||'/'||(SELECT count(*) FROM thoughts WHERE metadata->>'exposure'='personal')::text" +
                                          "||'/'||(SELECT count(*) FROM agent_memories WHERE metadata->>'exposure'='personal')::text;")
        $line = (($rv.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+/\d+/\d+/\d+/\d+$' } | Select-Object -First 1)
        if (-not $line) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rv.exit -Note "could not confirm the fixture was removed"
        } elseif ($line -eq "0/0/0/0/0") {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "fixture and control removed from thoughts, agent_memories and entities; production shows 0 personal rows in either corpus"
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                    -Note ("fixture thoughts/memories/entities and personal thoughts/memories remaining: {0} - the plane was left dirty" -f $line)
        }
        $c.probes += (New-Probe -Name "fixture-cleaned-up" -Command "psql DELETE every fixture row; then count fixture and personal rows in both corpora" -Run $body)
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

function Test-Clause4 {
    # C.8.4 - nothing in flight, and everything DEPLOYED AND RUNNING from the work line's
    # code. A deliverable that merges but does not run is not done; a stale worktree is
    # unfinished work wearing a finished face.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 4
    $subjects = @("service-set-matches-plan", "work-branches", "worktrees", "clean-repo", "clean-submodules", "gitlink-reachable") + @($script:DfuRequiredServices.Keys)
    $c.coverage.subject  = "in-flight checks, the services this plan adds, and the service floor's own drift check"
    $c.coverage.expected = $subjects.Count

    # --- (0) THE SERVICE SET STILL MATCHES THE PLAN'S OWN WORDS ------------------
    # The same guard clause 3 carries for its doors. C.8 clause 4 ENUMERATES the services
    # it means, so that list is read back out of the plan and every item must be claimed by
    # a subject here - a service the plan names and this script does not probe turns the
    # clause red rather than disappearing from it.
    $planTxt4 = Get-SnapMd -Which "plan"
    $sec4 = ""
    $why4 = "C.8 clause 4's text could not be located in the plan, so the pinned service set could not be checked back against it"
    if ($planTxt4) {
        # ANCHORED TO SECTION C.8, AND AMBIGUITY REFUSES. This ran a lazy first-match regex
        # over the WHOLE plan, so a decoy passage earlier in PLAN.md - a quotation of the
        # clause, an example, a superseded draft - became the text the pinned service set
        # was compared against. The drift check would then have been checking the wrong
        # paragraph while reporting a clean comparison.
        $c8 = Get-DfuSection -Text $planTxt4 -HeadingPattern '(?m)^###\s+C\.8\b[^\r\n]*'
        if ($c8.count -ne 1) {
            $why4 = ("section C.8's heading matched {0} time(s) in the plan, so clause 4 could not be located UNAMBIGUOUSLY" -f $c8.count)
        } else {
            $m4s = @([regex]::Matches($c8.text, '(?sm)^\s*4\.\s\*\*Nothing is left in flight.*?(?=^\s*5\.\s\*\*The walkthrough is true)'))
            if ($m4s.Count -ne 1) {
                $why4 = ("clause 4's text matched {0} time(s) inside section C.8 - one match is a location, two is a question nobody answered" -f $m4s.Count)
            } else {
                # ALL whitespace collapses, not just newlines. The plan WRAPS the phrase the
                # enumeration regex looks for across a line and the continuation carries an
                # indent, so a newline-only normalisation left a run of spaces mid-phrase,
                # matched nothing, and this subject reported 'could not be parsed' against a
                # plan that says it perfectly well - a drift check that never checks.
                $sec4 = ($m4s[0].Value -replace '\s+', ' ')
            }
        }
    }
    if (-not $sec4) {
        $body4 = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note $why4
        $c.coverage.not_evaluated += "service-set-matches-plan"
    } else {
        $listed = @()
        # A PERIOD IS NOT ALWAYS A SENTENCE END. `([^.]+)\.` truncated the enumeration at
        # the FIRST period, so a service named after one - `openbrain-gateway.ps1`, a
        # version, a hostname - silently dropped every item after it out of the comparison
        # and the drift check reported agreement over a list it had cut in half. A period
        # closes the sentence only when whitespace or the end of the text follows it.
        $mls = @([regex]::Matches($sec4, "running live from the work line's code\*\*\s*[^A-Za-z0-9\s]\s*((?:[^.]|\.(?=\S))+)\.(?:\s|$)"))
        if ($mls.Count -eq 1) {
            $listed = @(($mls[0].Groups[1].Value -split ',') | ForEach-Object { ($_ -replace '\s+', ' ').Trim() } | Where-Object { $_ })
        } elseif ($mls.Count -gt 1) {
            $c.detail += ("clause 4's service enumeration phrase appears {0} times in the clause - refusing to pick one" -f $mls.Count)
        }
        $claimedPhrases = @()
        foreach ($k in $script:DfuServiceAnchors.Keys) { $claimedPhrases += @($script:DfuServiceAnchors[$k]) }
        $unclaimed = @($listed | Where-Object { $item = $_; -not (@($claimedPhrases) | Where-Object { $item -match [regex]::Escape($_) }) })
        $absentPhrases = @($claimedPhrases | Where-Object { -not $sec4.Contains($_) })
        if ($listed.Count -lt 1) {
            $c.coverage.not_evaluated += "service-set-matches-plan"
            $body4 = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                     -Note "C.8 clause 4's enumeration of the services it means could not be parsed, so the pinned set was compared against nothing"
        } elseif ($unclaimed.Count -eq 0 -and $absentPhrases.Count -eq 0) {
            $c.coverage.evaluated++
            $body4 = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                     -Note ("all {0} service(s) C.8 clause 4 enumerates are claimed by a subject of this clause: {1}" -f $listed.Count, ($listed -join " | "))
        } else {
            $c.coverage.evaluated++
            $body4 = New-VerdictProbeBody -Verdict "fail" -Exit ($unclaimed.Count + $absentPhrases.Count) `
                     -Note ("the pinned service set and C.8 clause 4 disagree - named but unclaimed: {0}; pinned phrases the clause no longer contains: {1}" -f `
                            $(if ($unclaimed.Count) { $unclaimed -join " | " } else { "none" }), `
                            $(if ($absentPhrases.Count) { $absentPhrases -join " | " } else { "none" }))
        }
    }
    $c.probes += (New-Probe -Name "service-set-matches-plan" `
                  -Command ("read C.8 clause 4's own enumeration of services from {0} and compare it with the pinned set" -f $Ctx.plan) -Run $body4)

    # --- unmerged work/* branches ------------------------------------------------
    # An exclusion is only honoured while the LEDGER records it (see DfuExcludedBranches).
    # FROM THE SNAPSHOT, ALL OF IT. This clause's subjects - the branch list, the worktree
    # list, the working tree's cleanliness, the submodule states, the gitlink and the
    # remote's refs - are exactly what a walkthrough command running under cmd.exe could
    # change, and clause 1 runs first. They are read as they stood before the first command.
    $decForBranches = Get-SnapMd -Which "decisions"
    $branches = Get-SnapGit -Key "branches"
    if ($null -eq $branches) {
        $c.coverage.not_evaluated += "work-branches"
        $c.probes += (New-Probe -Name "no-unmerged-work-branches" -Command "git for-each-ref refs/heads/work/" `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "could not enumerate work branches"))
    } else {
        $unmerged = @()
        $skipped  = @()
        $unbacked = @()
        foreach ($b in $branches) {
            if ($script:DfuExcludedBranches.Contains($b)) {
                $grant = Get-BranchExclusionGrant -DecisionsText $decForBranches -Branch $b
                if ($grant.granted) {
                    $skipped += ("{0} ({1}; granted by '{2}' in {3})" -f $b, $script:DfuExcludedBranches[$b], $grant.heading, $Ctx.decisions)
                    continue
                }
                if ($grant.why) { $c.detail += ("carve-out for {0} REFUSED: {1}" -f $b, $grant.why) }
                # NOT EXCLUDED. Falls through and is counted like any other branch.
                $unbacked += $b
            }
            if ($b -eq ("work/" + $Ctx.workline)) { continue }
            $g = Invoke-Git -Arguments @("rev-list", "--count", ("{0}..{1}" -f $Ctx.workline, $b)) -WorkDir $Ctx.root
            if ($g.exit -ne 0) { $unmerged += ("{0} (ahead=UNKNOWN, rev-list exit {1})" -f $b, $g.exit); continue }
            $ahead = 0
            if ($g.stdout.Trim() -match '^\d+$') { $ahead = [int]$g.stdout.Trim() }
            if ($ahead -gt 0) { $unmerged += ("{0} (ahead {1})" -f $b, $ahead) }
        }
        foreach ($s in $skipped) { $c.detail += ("EXCLUDED from clause 4, and the ledger records it: {0}" -f $s) }
        foreach ($u in $unbacked) { $c.detail += ("NOT EXCLUDED - {0} is a declared carve-out but {1} does not record it, so it is counted" -f $u, $Ctx.decisions) }
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if ($unmerged.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("no unmerged work/* branch ({0} exclusion(s) applied, each backed by an entry in DECISIONS.md)" -f $skipped.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $unmerged.Count `
                    -Note ("{0} unmerged work/* branch(es): {1}{2}" -f $unmerged.Count, ($unmerged -join ", "), `
                           $(if ($unbacked.Count) { " -- including " + ($unbacked -join ", ") + ", whose carve-out is not recorded in DECISIONS.md" } else { "" }))
        }
        $c.probes += (New-Probe -Name "no-unmerged-work-branches" -Command ("git for-each-ref refs/heads/work/ ; git rev-list --count {0}..<branch>" -f $Ctx.workline) -Run $body)
    }

    # --- worktrees ---------------------------------------------------------------
    $wts = Get-SnapGit -Key "worktrees"
    if ($null -eq $wts) {
        $c.coverage.not_evaluated += "worktrees"
        $c.probes += (New-Probe -Name "no-worktrees" -Command "git worktree list --porcelain" `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "could not enumerate worktrees"))
    } else {
        # The main checkout is listed first by git and is not a worktree in this sense.
        $extra = @($wts | Select-Object -Skip 1)
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if ($extra.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "no worktrees beyond the main checkout"
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $extra.Count `
                    -Note ("{0} worktree(s) still present: {1}" -f $extra.Count, (($extra | ForEach-Object { Split-Path $_ -Leaf }) -join ", "))
        }
        $c.probes += (New-Probe -Name "no-worktrees" -Command "git worktree list --porcelain" -Run $body)
    }

    # --- clean repo and clean submodules -----------------------------------------
    $gs = @{ exit = (Get-SnapGit -Key "status_exit"); stdout = [string](Get-SnapGit -Key "status") }
    if ($gs.exit -ne 0) {
        $c.coverage.not_evaluated += "clean-repo"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $gs.exit -Note "git status failed"
    } else {
        $dirty = @(($gs.stdout -split "`n") | ForEach-Object { $_.TrimEnd() } | Where-Object { $_.Trim() })
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if ($dirty.Count -eq 0) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "working tree clean" }
        else { $body = New-VerdictProbeBody -Verdict "fail" -Exit $dirty.Count -Note ("{0} dirty path(s): {1}" -f $dirty.Count, (($dirty | Select-Object -First 5) -join " ; ")) }
    }
    $c.probes += (New-Probe -Name "clean-repo" -Command "git status --porcelain" -Run $body)

    $gm = @{ exit = (Get-SnapGit -Key "submodule_exit"); stdout = [string](Get-SnapGit -Key "submodule") }
    if ($gm.exit -ne 0) {
        $c.coverage.not_evaluated += "clean-submodules"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $gm.exit -Note "git submodule status failed"
    } else {
        # A leading '+' or '-' means the submodule is not at the recorded commit.
        $bad = @(($gm.stdout -split "`n") | Where-Object { $_ -match '^\s*[+-]' })
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if ($bad.Count -eq 0) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "every submodule is at its recorded commit" }
        else { $body = New-VerdictProbeBody -Verdict "fail" -Exit $bad.Count -Note ("{0} submodule(s) not at the recorded commit: {1}" -f $bad.Count, (($bad | ForEach-Object { $_.Trim() }) -join " ; ")) }
    }
    $c.probes += (New-Probe -Name "clean-submodules" -Command "git submodule status --recursive" -Run $body)

    # --- the OB1 gitlink must be reachable ON THE REMOTE --------------------------
    # QUERY THE REMOTE. Local remote-tracking refs are not evidence: they can be absent in
    # a given clone (a branch pushed from a worktree this clone never fetched), which
    # produces false failures, and stale, which produces a false PASS. DECISIONS.md
    # records both halves of that lesson.
    $pin = [string](Get-SnapGit -Key "gitlink")
    $ob1 = Join-Path $Ctx.root "OB1"
    if (-not $pin) {
        $c.coverage.not_evaluated += "gitlink-reachable"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit (Get-SnapGit -Key "gitlink_exit") -Note "could not read the OB1 gitlink from the work line"
    } else {
        $lrExit = Get-SnapGit -Key "lsremote_exit"
        $lr = @{ exit = $(if ($null -eq $lrExit) { 1 } else { [int]$lrExit }); stdout = [string](Get-SnapGit -Key "lsremote") }
        if ($lr.exit -ne 0) {
            # A GATE THAT CANNOT SEE THE REMOTE MUST REFUSE, NOT PASS.
            $c.coverage.not_evaluated += "gitlink-reachable"
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $lr.exit `
                    -Note "the OB1 remote could not be queried - 'could not check' is not 'fine'"
        } else {
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            # PARSE THE SHA COLUMN. `git ls-remote` prints "<sha>TAB<refname>", and the
            # test here was a raw SUBSTRING search over the WHOLE output - so the pin
            # matched anywhere, including inside a REF NAME. A bare remote that does not
            # contain the pinned commit but carries one tag NAMED after it - the shape
            # `git tag rollback-$(git rev-parse HEAD)` produces - reported
            # gitlink-reachable-on-remote = pass for a commit a fresh
            # --recurse-submodules clone could not fetch.
            #
            # ROUND 2 REPLACED THIS EXACT SUBSTRING-FOR-STRUCTURE TEST IN CLAUSE 2 AND LEFT
            # IT HERE. The pattern, not the line, is the defect: a structured output read
            # as an undifferentiated blob. Its sibling is fixed in the same commit - the
            # ops-gateway probe below matched `docker ps --format {{.Names}}` as a blob,
            # where any container whose name merely CONTAINED the service's would do.
            $tipShas = @()
            foreach ($lrline in ($lr.stdout -split "`n")) {
                $lrl = $lrline.Trim()
                if ($lrl -match '^([0-9a-f]{40})[ 	]+.+$') { $tipShas += $Matches[1] }
            }
            if ($tipShas -contains $pin) {
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                        -Note ("the pinned OB1 commit {0} is a ref tip on the remote - matched in the SHA COLUMN of {1} advertised ref(s), not anywhere in the output" -f (Get-ShortRef -Sha $pin), $tipShas.Count)
            } else {
                # A commit can be reachable without being a tip, so the remote is asked
                # directly - and it must actually be ASKED.
                #
                # THE FALLBACK USED TO BE `git fetch --dry-run origin <sha>` RUN IN OB1
                # ITSELF, which is decided by whether the object is in the LOCAL clone: git
                # sees it already has the commit and does nothing, exit 0. It passed against
                # a bare remote that provably lacked the commit. The comment three lines up
                # says "QUERY THE REMOTE. Local remote-tracking refs are not evidence" - and
                # this was exactly that, in a different costume.
                #
                # The fetch now runs in an EMPTY scratch repository, so there is no local
                # object to satisfy it: only the remote can. "not our ref" is the remote
                # answering that it will not serve the commit - that is the failure this
                # gate exists for. Anything else non-zero is a remote we could not ask, and
                # "could not check" is never "fine".
                $url = ""
                $ru = Invoke-Git -Arguments @("remote", "get-url", "origin") -WorkDir $ob1
                if ($ru.exit -eq 0) { $url = $ru.stdout.Trim() }
                $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-done-gitlink-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
                $fe = $null
                if ($url) {
                    $init = Invoke-Git -Arguments @("init", "--quiet", "--bare", $scratch) -WorkDir $Ctx.root
                    if ($init.exit -eq 0) {
                        $fe = Invoke-Git -Arguments @("fetch", "--quiet", "--depth=1", "--no-tags", $url, $pin) -WorkDir $scratch
                    }
                }
                if (Test-Path -LiteralPath $scratch) {
                    Get-ChildItem -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue |
                        ForEach-Object { try { $_.Attributes = "Normal" } catch { } }
                    Remove-Item -LiteralPath $scratch -Recurse -Force -ErrorAction SilentlyContinue
                }
                if ($null -eq $fe) {
                    $c.coverage.evaluated = [int]$c.coverage.evaluated - 1
                    $c.coverage.not_evaluated += "gitlink-reachable"
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                            -Note "the OB1 remote URL or a scratch repository to ask it from could not be prepared, so the remote was never queried"
                } elseif ($fe.exit -eq 0) {
                    $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                            -Note ("the OB1 remote SERVED the pinned commit {0} into an empty scratch repository - it is reachable there, not merely present here" -f $pin.Substring(0, 7))
                } else {
                    $errText = (($fe.stderr + " " + $fe.stdout) -replace "\s+", " ").Trim()
                    if ($errText -match '(?i)(not our ref|unadvertised object|upload-pack: not our ref)') {
                        $body = New-VerdictProbeBody -Verdict "fail" -Exit $fe.exit `
                                -Note ("the OB1 remote REFUSED the pinned commit {0} ('{1}') - a fresh --recurse-submodules clone would break" -f $pin.Substring(0, 7), $errText)
                    } else {
                        $c.coverage.evaluated = [int]$c.coverage.evaluated - 1
                        $c.coverage.not_evaluated += "gitlink-reachable"
                        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $fe.exit `
                                -Note ("the OB1 remote could not be queried ('{0}') - 'could not check' is not 'fine'" -f $errText)
                    }
                }
            }
        }
    }
    $c.probes += (New-Probe -Name "gitlink-reachable-on-remote" `
                  -Command ("git ls-tree {0} OB1 ; git -C OB1 ls-remote origin ; then, in an EMPTY scratch repo, git fetch --depth=1 <ob1-remote> <pinned-sha>" -f $Ctx.workline) -Run $body)

    # --- the services this plan adds, RUNNING LIVE -------------------------------
    foreach ($svc in @($script:DfuRequiredServices.Keys)) {
        $what = [string]$script:DfuRequiredServices[$svc]
        $body = $null
        $cmd  = ""
        switch ($svc) {
            "ops-gateway" {
                $cmd = "docker ps --filter name=openbrain-ops-gateway --format {{.Names}}"
                if ($Ctx.skiplive) {
                    $c.coverage.not_evaluated += $svc
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive"
                } else {
                    $r = Invoke-Native -Exe "docker" -Arguments @("ps", "--filter", "name=openbrain-ops-gateway", "--format", "{{.Names}}")
                    # ONE NAME PER LINE, COMPARED WHOLE. `--filter name=` is a SUBSTRING
                    # filter and the old test was a substring match over its output, so
                    # `openbrain-ops-gateway-backup` - or any container whose name merely
                    # contains this one - answered for the service. Same class as the
                    # gitlink gate above, one output format over.
                    $names = @(($r.stdout -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
                    if (-not $r.ran) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "docker unavailable" }
                    elseif ($names -contains "openbrain-ops-gateway") { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("running (exact container name among {0} matched by the filter: {1})" -f $names.Count, ($names -join ",")) }
                    else { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("{0} is NOT running - the name filter matched {1} container(s): {2}" -f $what, $names.Count, $(if ($names.Count) { $names -join "," } else { "none" })) }
                }
            }
            "andon-board" {
                $andon = Join-Path $Ctx.root "scripts\agent-harness\andon.ps1"
                $cmd = ("powershell -NoProfile -File {0} -List" -f $andon)
                if (-not (Test-Path -LiteralPath $andon)) {
                    $c.coverage.evaluated++
                    $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note "andon.ps1 is not present on the work line"
                } else {
                    $r = Invoke-Native -Exe "powershell" -Arguments @("-NoProfile", "-File", $andon, "-List") -WorkDir $Ctx.root
                    if (-not $r.ran -or $null -eq $r.exit) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "could not run the board" }
                    elseif ([int]$r.exit -eq 0) { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "the board lists its conditions and exits 0" }
                    else { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) -Note ("andon -List exited {0}" -f $r.exit) }
                }
            }
            "gate-profiles" {
                $cfg = Join-Path $Ctx.root "scripts\agent-harness\harness.config.json"
                $cmd = ("read gate_profiles from {0}" -f $cfg)
                $j = Read-JsonStore -Path $cfg
                if ($null -eq $j) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "harness.config.json unreadable" }
                else {
                    $names = @()
                    if ($j.PSObject.Properties.Name -contains "gate_profiles") { $names = @($j.gate_profiles.PSObject.Properties.Name) }
                    $missing = @(@("dark", "attended") | Where-Object { $names -notcontains $_ })
                    $c.coverage.evaluated++
                    if ($missing.Count -eq 0) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("both gate profiles are declared: {0}" -f ($names -join ",")) }
                    else { $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("missing gate profile(s): {0}" -f ($missing -join ",")) }
                }
            }
            "rls-boundary" {
                # TWO facts, and both are required: the boundary is live, AND its source is
                # on the work line. A migration applied to production from an unmerged
                # branch is deployed-but-not-landed, which is the mirror of the failure
                # this clause names and is just as much "in flight".
                $cmd = ("psql: relrowsecurity/relforcerowsecurity for the corpus tables AND every table with a foreign key into them ; docker network/inspect -> the DB role every direct client connects as -> pg_roles.rolsuper/rolbypassrls ; git ls-tree {0} OB1 -> submodule cat-file" -f $Ctx.workline)
                if ($Ctx.skiplive) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive" }
                else {
                    # boolean::text is 'true'/'false', NOT 't'/'f' - psql's display form and
                    # its cast form differ, and matching the display form against a cast
                    # produced a permanent 'could not read' that looked like an outage.
                    # EVERY STAGE, NOT ONE TABLE. C.8.4 asks for "the RLS boundary at
                    # every stage"; the previous probe read relforcerowsecurity on
                    # `thoughts` alone and reported the boundary from it. The stage set is
                    # DERIVED from the live schema - the two corpus tables, the published
                    # output C.8.3 names, and every table carrying a foreign key into a
                    # corpus table, which is what "a later stage of the same row" means in
                    # this database. thought_entities and entity_extraction_queue are t/f
                    # and wiki_pages has RLS off; a probe that looked only at `thoughts`
                    # could not say so.
                    $stageSql = "SELECT c.relname||'/'||(CASE WHEN c.relrowsecurity THEN 't' ELSE 'f' END)||'/'||(CASE WHEN c.relforcerowsecurity THEN 't' ELSE 'f' END) " +
                                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace " +
                                "WHERE n.nspname='public' AND c.relkind='r' AND (" +
                                "c.relname IN ('thoughts','agent_memories','wiki_pages') OR c.oid IN (" +
                                "SELECT conrelid FROM pg_constraint WHERE contype='f' AND confrelid IN (" +
                                "SELECT oid FROM pg_class WHERE relname IN ('thoughts','agent_memories')))) ORDER BY 1;"
                    $r = Invoke-Psql -Ctx $Ctx -Sql $stageSql
                    $stages = @(($r.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[A-Za-z0-9_]+/[tf]/[tf]$' })
                    $flags = $(if ($stages.Count -ge 1) { "read" } else { "" })
                    $unbound = @($stages | Where-Object { $_ -notmatch '/t/t$' })
                    foreach ($st in $stages) { $c.detail += ("RLS stage {0}" -f $st) }
                    $srcOk = $false
                    $srcWhy = ""
                    if ($pin) {
                        $cf = Invoke-Git -Arguments @("cat-file", "-e", ("{0}:docker/init-agent-memory-rls.sql" -f $pin)) -WorkDir $ob1
                        $srcOk = ($cf.exit -eq 0)
                        if (-not $srcOk) { $srcWhy = "its defining SQL is NOT in the OB1 tree the work line pins" }
                    } else { $srcWhy = "the OB1 gitlink could not be read" }
                    # AND THE DIRECT CLIENTS, WHICH C.8.4 NAMES EXPLICITLY. relrowsecurity
                    # and relforcerowsecurity are STRUCTURAL facts about a table, and a role
                    # with rolbypassrls is unaffected by both - so once the five t/f tables
                    # go t/t this subject would have gone green over a boundary that is void
                    # at the client. The same run already MEASURED that:
                    # door-openbrain-mcp-door connects as `postgres` (rolsuper/rolbypassrls
                    # = t/t) and RETURNED the personal fixture, and clause 4 never consulted
                    # it. "The RLS boundary at every stage including the direct clients" is
                    # the column's own wording; a table-flag reading offered as that
                    # measurement is the round-2 send-back in a new probe.
                    #
                    # So the clients are enumerated from the running system and their roles
                    # read from pg_roles. A direct client that bypasses RLS FAILS this
                    # subject however the table flags read.
                    #
                    # AND AN UNDETERMINABLE ROLE IS INDETERMINATE, NOT ABSENT. The client
                    # enumeration used to match only three unprefixed role variables, so
                    # `open_notebook` (OB1_DB_USER=postgres, rolsuper/rolbypassrls = t/t)
                    # was never enumerated and `openbrain-idea-refinery` (DB_HOST=openbrain-db,
                    # no role variable) was silently skipped - the pass condition was then
                    # decidable over an INCOMPLETE set with no record of what could not be
                    # determined. See Get-DirectDbClients.
                    $clients = Get-DirectDbClients -Ctx $Ctx
                    $bypass = @()
                    $clientRoles = @()
                    $clientsKnown = $false
                    $undet = @()
                    $silentCount = 0
                    if ($null -ne $clients) {
                        $undet = @($clients.unknown)
                        $silentCount = @($clients.silent).Count
                        foreach ($u in $undet) { $c.detail += ("direct client UNDETERMINED role: {0} reaches {1} and its environment names no DB role" -f $u, $Ctx.db) }
                        $clientRoles = @($clients.roles.Keys | Sort-Object)
                        if ($clientRoles.Count -ge 1) {
                            $inList = (($clientRoles | ForEach-Object { "'" + $_ + "'" }) -join ",")
                            $rrq = ("SELECT rolname||'/'||(CASE WHEN rolsuper THEN 't' ELSE 'f' END)||'/'||(CASE WHEN rolbypassrls THEN 't' ELSE 'f' END) " +
                                    "FROM pg_roles WHERE rolname IN ({0});" -f $inList)
                            $rrc = Invoke-Psql -Ctx $Ctx -Sql $rrq
                            $rows = @(($rrc.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[A-Za-z0-9_]+/[tf]/[tf]$' })
                            # EVERY enumerated role must come back from pg_roles. A role the
                            # query did not answer for is a role whose privileges were never
                            # read, and dropping it would shrink the set again.
                            $answered = @($rows | ForEach-Object { ($_ -split '/')[0] })
                            $unanswered = @($clientRoles | Where-Object { $answered -notcontains $_ })
                            foreach ($ua in $unanswered) { $c.detail += ("direct client UNDETERMINED privileges: role '{0}' (used by {1}) is not in pg_roles" -f $ua, ((@($clients.roles[$ua])) -join ",")) }
                            if ($rows.Count -ge 1 -and $unanswered.Count -eq 0) { $clientsKnown = $true }
                            foreach ($row in $rows) {
                                $rn = ($row -split '/')[0]
                                $c.detail += ("direct client role {0} (used by {1})" -f $row, ((@($clients.roles[$rn])) -join ","))
                                if ($row -notmatch '/f/f$') { $bypass += ("{0} used by {1}" -f $row, ((@($clients.roles[$rn])) -join ",")) }
                            }
                            $undet += $unanswered
                        }
                    }
                    if ($undet.Count -gt 0) { $clientsKnown = $false }

                    if (-not $flags) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $r.exit -Note "could not read the RLS flags for any stage table" }
                    elseif (-not $clientsKnown) {
                        $c.coverage.not_evaluated += $svc
                        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                                -Note ("the RLS flags read t/t on {0} of {1} stage table(s), but the DIRECT CLIENTS C.8.4 names are not fully determined: {2}. A table-flag reading is not the boundary this column asks about, and a boundary decided over an INCOMPLETE client set is a claim wider than its evidence - so this REFUSES rather than reporting the structural half as the measurement" -f `
                                       ($stages.Count - $unbound.Count), $stages.Count, `
                                       $(if ($undet.Count) { ("{0} client(s) reach {1} with no determinable role: {2}" -f $undet.Count, $Ctx.db, ($undet -join ", ")) } else { "the client set could not be enumerated at all" }))
                    }
                    else {
                        $c.coverage.evaluated++
                        if ($unbound.Count -eq 0 -and $bypass.Count -eq 0 -and $srcOk) {
                            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                                    -Note ("RLS is enabled and FORCED on all {0} stage table(s), none of the {1} direct client role(s) carries rolsuper or rolbypassrls, and its source is on the work line. THE RESTRICTION ON THAT SENTENCE: clients are identified from container ENVIRONMENT, so {2} container(s) on {3} whose environment names neither {4} nor a DB role were not cleared - they were not visible to this enumeration" -f `
                                           $stages.Count, $clientRoles.Count, $silentCount, $Ctx.obnet, $Ctx.db)
                        } elseif ($unbound.Count -gt 0) {
                            $body = New-VerdictProbeBody -Verdict "fail" -Exit $unbound.Count `
                                    -Note ("{0} of {1} stage table(s) are not relrowsecurity/relforcerowsecurity = t/t: {2}" -f `
                                           $unbound.Count, $stages.Count, ($unbound -join ", "))
                        } elseif ($bypass.Count -gt 0) {
                            $body = New-VerdictProbeBody -Verdict "fail" -Exit $bypass.Count `
                                    -Note ("the table flags are t/t on all {0} stage(s), but {1} DIRECT CLIENT role(s) are unaffected by them: {2} - C.8.4 asks for the boundary at every stage INCLUDING the direct clients, and a BYPASSRLS client makes the flags decorative" -f `
                                           $stages.Count, $bypass.Count, ($bypass -join " ; "))
                        } else {
                            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("the boundary is LIVE but {0} - deployed from code that has not landed" -f $srcWhy)
                        }
                    }
                }
            }
            default {
                $c.coverage.not_evaluated += $svc
                $cmd  = "(no probe implemented)"
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                        -Note "this service is declared in code but has no probe - declared and unimplemented is UNEVALUATED"
            }
        }
        $c.probes += (New-Probe -Name ("service-{0}" -f $svc) -Command $cmd -Run $body)
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

function Test-Clause5 {
    # C.8.5 - the walkthrough is true. Every row names a check and that check re-runs
    # green. The operator reviews by reading it, so a row whose check does not run is
    # worse than a missing row.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 5
    # THE SNAPSHOT, NOT THE DISK - and the commands below run in the SANDBOX, not in
    # $Ctx.root. This clause used to execute every backtick span under a How-to-run marker
    # with the audited repository as the working directory, which is class fifteen's
    # demonstrated channel.
    $text = Get-SnapDoc -Which "walkthrough"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "walkthrough"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "plan"
    if (-not $text) {
        $c.probes += (New-Probe -Name "walkthrough-readable" -Command ("read {0}" -f $Ctx.walkthrough) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "WALKTHROUGH.md is unreadable or missing"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    # THE SECTIONS THE WALKTHROUGH HAPPENS TO HAVE ARE NOT THE POPULATION.
    #
    # This clause set coverage.expected to the number of `## U<n>` sections parsed out of
    # WALKTHROUGH.md - THE DOCUMENT UNDER TEST. Deleting the six phase sections that name
    # no check made it report MET at "evaluated 2 of 2" with no not_evaluated entry, on a
    # trimmed copy of the real walkthrough. That is rule 6 - never derive the population
    # from the document under test - and `Add-PhaseFloorProbes` was applied to clauses 1,
    # 2 and 7 and NOT to this one. The same fix belongs here: the C.8.1 floor is pinned,
    # checked back against the plan's words, and UNIONED with whatever sections the
    # walkthrough actually has, so a phase can be added to this population but never
    # subtracted from it by editing the walkthrough.
    # AND THE SECTION SCAN NORMALISES. Splitting the RAW text here meant five phase
    # sections inside a properly CLOSED HTML comment counted as sections: verdict met,
    # coverage 8 of 8, floor pass, every walkthrough-U<n>-check-1 green - over a document
    # showing two sections to the operator this clause exists to serve.
    $sections = Get-WalkthroughSectionIds -Text $text
    $planText5 = Get-SnapMd -Which "plan"
    $floor5 = Add-PhaseFloorProbes -Clause $c -Ctx $Ctx -PlanText $planText5 -Phases $sections `
                                   -Where "WALKTHROUGH.md's phase sections"
    $ids = @($floor5.ids)
    $runs = Get-WalkthroughRuns -Text $text
    $c.coverage.subject  = "phases - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with WALKTHROUGH.md's own sections - each of which must name a check that re-runs green, plus the floor's own drift check"
    $c.coverage.expected = $ids.Count + 1
    if ($ids.Count -lt 1) {
        $c.probes += (New-Probe -Name "walkthrough-sections" -Command ("parse '## U<n>' sections in {0}" -f $Ctx.walkthrough) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "no phase sections were parsed and the floor is empty - the parser and the document disagree"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    foreach ($id in $ids) {
        $cmds = @()
        if ($runs.Contains($id)) { $cmds = @($runs[$id]) }
        if ($cmds.Count -lt 1) {
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
            $why = $(if ($sections.Contains($id)) {
                        "this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row"
                     } else {
                        "the walkthrough has NO section for this floor phase at all, so the operator's review document is silent about it - an absent row is not a satisfied one"
                     })
            $c.probes += (New-Probe -Name ("walkthrough-{0}-names-a-check" -f $id) `
                -Command ("(none - no 'How to run' recorded for {0} in {1})" -f $id, $Ctx.walkthrough) `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note $why))
            continue
        }
        # EVERY check the row names runs, and every command under EVERY marker - see
        # Get-WalkthroughRuns. Taking $cmds[0], or the first backtick span after a marker,
        # left named checks unrun while the coverage line still read full; this is the
        # document the operator reviews by, so a command that was never executed is a
        # sentence this script did not verify.
        Reset-DfuSandbox
        $n = 0
        $ranAll = $true
        $moved  = @()
        foreach ($cmd in $cmds) {
            $n++
            $run = Invoke-AuditedCommand -Ctx $Ctx -Command $cmd -Clause "5" -Phase $id
            $r   = $run.result
            $moved += @($run.drift)
            if (@($run.drift).Count -lt 1 -and (-not $r.ran -or $null -eq $r.exit)) { $ranAll = $false }
            $c.probes += (New-Probe -Name ("walkthrough-{0}-check-{1}" -f $id, $n) -Command $cmd `
                          -Run (New-CommandProbeBody -Run $run -GreenNote "the row's named check re-runs green"))
        }
        $c.probes += (New-Probe -Name ("walkthrough-{0}-left-the-audited-tree-unchanged" -f $id) `
            -Command ("fingerprint the plan, the ledger, the walkthrough, documentation/notes, git refs/status/worktrees/submodules before and after each of {0}'s {1} command(s)" -f $id, $cmds.Count) `
            -Run $(if (@($moved).Count -gt 0) {
                      New-VerdictProbeBody -Verdict "fail" -Exit (@($moved).Count) `
                      -Note ("the check this row NAMES changed the audited tree: {0}. It ran in the clean checkout with the audited documents locked, so this is a command reaching past both - and the row is not true merely because its command exited 0." -f ((@($moved) | Sort-Object -Unique) -join ", "))
                  } else {
                      New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                      -Note ("nothing this row's {0} command(s) did moved the plan, the ledger, the walkthrough, documentation/notes or git's refs, status, worktrees or submodules" -f $cmds.Count)
                  }))
        if ($ranAll) { $c.coverage.evaluated++ }
        else { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id) }
    }
    Reset-DfuSandbox
    return (Resolve-ClauseVerdict -Clause $c)
}

function Test-Clause6 {
    # C.8.6 - U7 is ARMED, not complete: its loop has run ONE full cycle on the record -
    # a real outcome, a proposed design change, judged against a pinned section 0/B
    # anchor, adopted or refused, with the citation or the ledger amendment.
    # A loop that has never run is not a standing process, it is an intention.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 6
    $c.coverage.subject  = "one complete U7 cycle on the record"
    $c.coverage.expected = 1

    # NORMALISED, AND FROM THE SNAPSHOT - a U7 "cycle" recorded inside an HTML comment is
    # not on the record the operator reads, and one written by this run is not on it either.
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "decisions"
    $decText = Get-SnapMd -Which "decisions"
    if (-not $decText) {
        $c.probes += (New-Probe -Name "u7-cycle-recorded" -Command ("read {0}" -f $Ctx.decisions) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "DECISIONS.md unreadable - the ledger is where a cycle would be recorded"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    # A cycle leaves a ledger entry that names U7 AND records an adopt/refuse outcome.
    $cycles = @()
    foreach ($part in [regex]::Split($decText, '(?m)^(?=##\s)')) {
        if ($part -notmatch '(?m)^##\s+(.*)$') { continue }
        $head = $Matches[1].Trim()
        if ($head -notmatch '\bU7\b') { continue }
        if ($part -match '(?i)\b(adopted|refused)\b') { $cycles += $head }
    }
    $c.coverage.evaluated = 1
    if ($cycles.Count -ge 1) {
        $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("{0} U7 cycle entry/entries on the record: {1}" -f $cycles.Count, ($cycles -join " | "))
    } else {
        $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                -Note "no DECISIONS.md entry records a U7 cycle reaching adopted-or-refused - the standing loop has never run"
    }
    $c.probes += (New-Probe -Name "u7-cycle-recorded" -Command ("grep '^## ' {0} | grep U7, then check for adopted/refused" -f $Ctx.decisions) -Run $body)

    # The JUDGEMENT half - "was it judged against a pinned anchor?" - is not decidable by
    # grep, so it is a NAMED MANUAL CHECK and the clause refuses without a recorded result.
    [void](Add-ManualCheck -Clause $c -Store $Store -Name "u7-cycle-judged-against-pinned-anchor" `
        -What "Confirm the recorded U7 cycle was judged against a PINNED section 0/B research anchor, and that the entry carries the anchor citation or the ledger amendment.")
    return (Resolve-ClauseVerdict -Clause $c)
}

function Get-CommitValidationClaims {
    # THE VALIDATION STATEMENTS A COMMIT MESSAGE MAKES, as structures rather than as a
    # co-occurrence of two words somewhere in a page of prose.
    #
    # THE LIVE FALSE PASS THIS REPLACES. `audit-trail-U2 = pass` rested entirely on 8b477a9
    # - a commit about U4's audit-trail round whose own summary line says "No code behaviour
    # changed". It mentions U2 in one sentence ("2 pre-existing F811s from U2's 86ffa62")
    # and `test_anchor_schema.py` in the NEXT sentence, describing a lint finding in someone
    # else's file. The phase-id match and the artifact match were INDEPENDENT substring
    # searches over the whole message, so two incidental mentions in unrelated sentences
    # discharged "commit messages stating what was validated and by which check". That is a
    # substring standing in for a structure - class 7 on this file's own list - in the one
    # clause whose subject is the record the operator reads INSTEAD of the diffs.
    #
    # A CLAIM IS A DIRECTIVE, LIKE EVERY OTHER RECORD THIS FILE ACCEPTS. The ledger's
    # un-parking is `Un-parks: <entry>`; clause 4's carve-out is `Excluded from C.8 clause
    # 4: <branch>`; this is the same shape, and for the same reason - a record has to be
    # something an author WROTE ON PURPOSE and a reader can find:
    #
    #     Validated: U2 - the anchor-schema cross-reader, by scripts/agent-harness/test_anchor_schema.py
    #     Verified by: U5 step 1, scripts/checks/personal-plane-drill.ps1
    #
    # The directive word (validated / verified / proved / proven, with or without "by",
    # optionally introduced by a list marker)
    # opens the claim; the claim runs to the end of its line plus any INDENTED continuation
    # lines, so a wrapped or bulleted list under one heading is one claim. What the claim
    # must then contain is BOTH halves in the SAME claim: the phase it is about, and a check
    # that phase names. Neither half alone, and never one half from one sentence and the
    # other from another.
    #
    # THE COST IS HONEST AND IS THE POINT. No commit currently on this work line carries the
    # directive, so this half of clause 7 goes RED for every phase - which is the true
    # statement about a history that never wrote it down, and C.8's own instruction is that
    # a clause which cannot be met is a REPORT and not a redefinition. The drill proves the
    # rule is a measurement rather than a wall: X4 constructs a commit that DOES carry the
    # directive and asserts it discharges, beside one that co-mentions and does not.
    param([string]$Message)
    $out = @()
    if (-not $Message) { return @($out) }
    $flat = (($Message -replace '\*', '') -replace '`', '')
    $lines = @($flat -split "`n")
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $l = [string]$lines[$i]
        if ($l -notmatch '(?i)^\s{0,3}(?:[-*]\s+)?(?:validated|verified|proved|proven)(?:\s+by)?\b[^\r\n:]{0,60}:\s*(.*)$') { continue }
        $body = [string]$Matches[1]
        for ($j = $i + 1; $j -lt $lines.Count; $j++) {
            $n = [string]$lines[$j]
            if (-not $n.Trim()) { break }
            # A line starting at the left margin begins a new paragraph, not a continuation
            # of this claim - which is what keeps a claim from absorbing the rest of the
            # message and becoming the substring search it replaced.
            if ($n -notmatch '^\s') { break }
            if ($n -match '(?i)^\s{0,3}(?:[-*]\s+)?(?:validated|verified|proved|proven)(?:\s+by)?\b[^\r\n:]{0,60}:') { break }
            $body = $body + " " + $n.Trim()
        }
        $body = ($body -replace '\s+', ' ').Trim()
        if ($body) { $out += $body }
    }
    return @($out)
}

function Test-Clause7 {
    # C.8.7 - the audit trail is complete, because it is what the operator reads instead
    # of the diffs: every phase has its DECISIONS entries, its findings note, and commit
    # messages stating what was validated and by which check.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 7
    # EVERY DOCUMENT FROM THE SNAPSHOT, AND NORMALISED. Both halves are load-bearing here:
    # read RAW, a `## ` heading inside an HTML comment discharged a phase's ledger artifact;
    # read from DISK after clause 1 had run, a findings note the RUN ITSELF created did -
    # U0 went from exit 3 to exit 2 that way, which is the demonstration that named class
    # fifteen.
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "decisions"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "plan"
    Add-MarkdownHygieneProbe -Clause $c -Ctx $Ctx -Which "walkthrough"
    $planText = Get-SnapMd -Which "plan"
    $parse7 = Get-PhaseTableParse -Text $planText
    $phases = $parse7.phases
    # THE FLOOR. Unbolding a row - `| **U1** |` -> `| U1 |`, invisible to a reader - used
    # to drop the phase from this clause's population, which then reported "1 of 1".
    $floor7 = Add-PhaseFloorProbes -Clause $c -Ctx $Ctx -PlanText $planText -Phases $phases -Parse $parse7
    $ids = @($floor7.ids)
    $c.coverage.subject  = "phases - the pinned floor (C.8.1's U0-U6 plus C.9's U8) unioned with section 2's table - each needing a ledger entry, a findings note AND a commit message, plus the floor's own drift check"
    $c.coverage.expected = $ids.Count + 1
    if ($ids.Count -lt 1) {
        $c.probes += (New-Probe -Name "audit-phases" -Command ("parse section 2 table in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "no phases parsed, so no audit trail could be checked"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    $decSections = @(Get-LedgerSections -Text (Get-SnapMd -Which "decisions"))
    # THE CHECKS A PHASE NAMES, from both places the plan names them: section 2's column
    # and the walkthrough's How-to-run commands for that phase (which clause 1 ties back to
    # the column). That union is what "by which check" means for this phase - not "any
    # script anywhere in the message".
    $runs7 = Get-WalkthroughRuns -Text (Get-SnapDoc -Which "walkthrough")
    $noteFiles = @(Get-SnapNotes)

    # THE THIRD ARTIFACT, and there was no `git log` ANYWHERE in this file before round 4.
    # C.8.7 names three per phase - DECISIONS entries, a findings note, and "commit messages
    # stating what was validated and by which check" - and this clause could reach `met`
    # with the commit-message half never examined.
    #
    # THE LOG COMES FROM THE SNAPSHOT, taken before the first walkthrough command ran: a
    # commit this run produced must not be able to discharge the phase this run is judging.
    $commits = @(Get-SnapGit -Key "log")
    $logOk = [bool](Get-SnapGit -Key "log_ok")
    if (-not $logOk) {
        # COULD NOT READ IS NOT FINE. Every phase below is left unevaluated for this half.
        $c.probes += (New-Probe -Name "audit-commit-log" `
            -Command ("git log --format=%x1e%H%x1f%s%x1f%b%x1f --name-only {0}" -f $Ctx.workline) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit (Get-SnapGit -Key "log_exit") `
                  -Note ("the work line's commit log could not be read, so the third artifact C.8.7 names was NOT examined: {0}" -f (Get-SnapGit -Key "log_err"))))
    }
    # THE DONE-AUTHORITY'S OWN FILES. A commit that touches nothing but this script and its
    # drill is a commit ABOUT THE CHECKER. It may mention any phase it likes; it is not
    # evidence that the phase was validated. Derived from this script's own name so it
    # cannot go stale if the file is renamed.
    $selfLeaf = ""
    try { $selfLeaf = [System.IO.Path]::GetFileName($PSCommandPath) } catch { }
    $selfNames = @()
    if ($selfLeaf) { $selfNames = @($selfLeaf, ("verify-" + $selfLeaf)) }

    foreach ($id in $ids) {
        $headings = @($decSections | Where-Object { $_.heading -match ('(?<![A-Za-z0-9])' + $id + '(?![0-9])') } |
                      ForEach-Object { $_.heading })
        # A findings note "for" a phase is one whose NAME or whose HEADINGS name it - and
        # the headings are read from the NORMALISED body, so a heading inside an HTML
        # comment discharges nothing.
        $notes = @($noteFiles | Where-Object {
            [string]$_.base -match ('(?i)(?<![A-Za-z0-9])' + $id + '(?![0-9])') -or
            [string]$_.md   -match ('(?m)^#{1,6}\s[^\r\n]*(?<![A-Za-z0-9])' + $id + '(?![0-9])')
        })
        # THE COMMIT-MESSAGE HALF, per phase - "commit messages stating what was validated
        # and BY WHICH CHECK", which is more than a mention of the phase plus a mention of
        # some script SOMEWHERE ELSE in the same message. See Get-CommitValidationClaims.
        $wantedCol = @()
        if ($phases.Contains($id)) { $wantedCol += @(Get-NamedArtifacts -Text ([string]$phases[$id].validated)) }
        if ($runs7.Contains($id)) { foreach ($rc in @($runs7[$id])) { $wantedCol += @(Get-NamedArtifacts -Text $rc) } }
        $wantedCol = @($wantedCol | Sort-Object -Unique)
        $cmsgs = @()
        $selfOnly = 0
        $coMention = 0
        foreach ($cm in $commits) {
            if ($cm.message -notmatch ('(?<![A-Za-z0-9])' + $id + '(?![0-9])')) { continue }
            if ($wantedCol.Count -lt 1) { continue }
            # THE STRUCTURED RELATIONSHIP. One CLAIM must carry both halves: the phase, and
            # a check that phase names. Two mentions in two unrelated sentences is what the
            # previous test accepted and is exactly what 8b477a9 was.
            $claims = @(Get-CommitValidationClaims -Message ([string]$cm.message))
            $hit = @($claims | Where-Object {
                $cl = $_
                ($cl -match ('(?<![A-Za-z0-9])' + $id + '(?![0-9])')) -and
                (@(Get-NamedArtifacts -Text $cl | Where-Object { $wantedCol -contains $_ }).Count -ge 1)
            })
            if ($hit.Count -lt 1) {
                # It names the phase and it names one of the phase's checks, but not in the
                # same claim - the shape that produced the false pass. Counted and reported,
                # so the difference between "no such commit" and "a commit that co-mentions"
                # is visible rather than collapsed into one word.
                $named = @(Get-NamedArtifacts -Text ([string]$cm.message))
                if (@($named | Where-Object { $wantedCol -contains $_ }).Count -ge 1) { $coMention++ }
                continue
            }
            $leaves = @(@($cm.files) | ForEach-Object { ($_ -split '/')[-1] })
            if ($leaves.Count -ge 1 -and $selfNames.Count -ge 1 -and
                @($leaves | Where-Object { $selfNames -notcontains $_ }).Count -eq 0) { $selfOnly++; continue }
            $cmsgs += ("{0} {1} :: {2}" -f (Get-ShortRef -Sha $cm.sha), (($cm.message -split "`n")[0]).Trim(), (@($hit)[0]))
        }
        if ($selfOnly -gt 0) { $c.detail += ("    {0}: {1} commit(s) claiming it touch ONLY the done-authority's own files - a commit about the checker does not discharge a phase" -f $id, $selfOnly) }
        if ($coMention -gt 0) { $c.detail += ("    {0}: {1} commit(s) mention the phase AND one of its checks, but in different statements - a co-mention is not a claim that THIS check validated THIS phase" -f $id, $coMention) }
        foreach ($cmline in @($cmsgs | Select-Object -First 3)) { $c.detail += ("    {0} commit: {1}" -f $id, $cmline) }

        $missing = @()
        if ($headings.Count -lt 1) { $missing += "no DECISIONS.md entry" }
        if ($notes.Count -lt 1)    { $missing += "no findings note" }
        if ($logOk -and $cmsgs.Count -lt 1) {
            if ($wantedCol.Count -lt 1) {
                $missing += ("this phase names NO runnable check anywhere - neither section 2's column nor a 'How to run' line in the walkthrough - so no commit message can state 'by which check'")
            } else {
                $missing += ("no commit message on the work line carries a validation claim naming the phase AND one of the checks this phase names ({0}) in the SAME statement - the shape is a directive line, e.g. 'Validated: {1} ... by {2}'{3}" -f `
                             ($wantedCol -join ", "), $id, (@($wantedCol)[0]), `
                             $(if ($coMention -gt 0) { (" ({0} commit(s) co-mention both without claiming one validated the other)" -f $coMention) } else { "" }))
            }
        }
        if ($logOk) { $c.coverage.evaluated++ }
        else { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id) }

        if ($missing.Count -gt 0) {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $missing.Count `
                    -Note ("{0} for {1}" -f ($missing -join " and "), $id)
        } elseif (-not $logOk) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                    -Note ("{0} ledger entry/entries and {1} findings note(s), but the commit log could not be read - two of the three artifacts C.8.7 names is not the audit trail it asks for" -f $headings.Count, $notes.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("{0} ledger entry/entries, {1} findings note(s) whose name or headings are ABOUT this phase, and {2} commit message(s) carrying a validation claim that names the phase AND one of the checks this phase names ({3}) in the same statement" -f `
                           $headings.Count, $notes.Count, $cmsgs.Count, ($wantedCol -join ", "))
        }
        $c.probes += (New-Probe -Name ("audit-trail-{0}" -f $id) `
                      -Command ("'^## .*{0}' in {1} ; a note in {2} whose FILENAME or a HEADING names {0} ; and a commit on {3} whose message carries a Validated/Verified claim naming {0} and one of its own checks, excluding commits that touch only the done-authority" -f $id, $Ctx.decisions, $Ctx.notes, $Ctx.workline) -Run $body)
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

function Test-Clause8 {
    # C.8.8 - THE MEMORY PLANE COMPOUNDS. Building the plumbing is U1's column; this
    # clause is about USE: real efforts write to the plane as they run, and at least one
    # recall demonstrably INFORMED a later effort - traceable through
    # agent_memory_recall_traces to the work that consumed it, with that effort's own
    # record citing what it was told.
    #
    # SECTION C.8 SAYS EXPLICITLY THIS CLAUSE MAY FAIL, AND THAT IS WHY IT IS THERE. It is
    # not papered over here: a plane that is built but not used has not been shown to work.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 8
    $c.coverage.subject  = "the write half, the recall half, and the consumer link"
    $c.coverage.expected = 3

    if ($Ctx.skiplive) {
        foreach ($n in @("plane-written-to", "recall-returned-something", "recall-informed-a-later-effort")) {
            $c.probes += (New-Probe -Name $n -Command "(not run: -SkipLive)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive - the plane was not measured"))
        }
        $c.coverage.not_evaluated = @("plane-written-to", "recall-returned-something", "recall-informed-a-later-effort")
        return (Resolve-ClauseVerdict -Clause $c)
    }

    # (1) written to
    $r1 = Invoke-Psql -Ctx $Ctx -Sql "SELECT count(*) FROM agent_memories;"
    $n1 = @(($r1.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' })
    if (-not $r1.ran -or $r1.exit -ne 0 -or $n1.Count -lt 1) {
        $c.coverage.not_evaluated += "plane-written-to"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $r1.exit -Note "could not count agent_memories"
    } else {
        $c.coverage.evaluated++
        if ([int]$n1[0] -gt 0) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("agent_memories holds {0} row(s)" -f $n1[0]) }
        else { $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note "agent_memories is empty - nothing writes to the plane" }
    }
    $c.probes += (New-Probe -Name "plane-written-to" -Command ("docker exec {0} psql -tAc 'SELECT count(*) FROM agent_memories;'" -f $Ctx.db) -Run $body)

    # (2) recall actually RETURNED something. A trace that examined 0 and returned 0 is a
    #     recall that ran, not a recall that informed anything - counting traces alone
    #     would be a claim wider than its evidence.
    $r2 = Invoke-Psql -Ctx $Ctx -Sql "SELECT count(*)::text||'/'||count(*) FILTER (WHERE (response_policy->>'returned')::int > 0)::text FROM agent_memory_recall_traces;"
    $n2 = (($r2.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+/\d+$' } | Select-Object -First 1)
    if (-not $r2.ran -or $r2.exit -ne 0 -or -not $n2) {
        $c.coverage.not_evaluated += "recall-returned-something"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $r2.exit -Note "could not measure recall traces"
    } else {
        $parts = $n2 -split '/'
        $c.coverage.evaluated++
        if ([int]$parts[1] -gt 0) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("{0} trace(s), {1} of which returned at least one memory" -f $parts[0], $parts[1]) }
        else { $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("{0} trace(s) and NONE returned a memory - recall ran but told nobody anything" -f $parts[0]) }
    }
    $c.probes += (New-Probe -Name "recall-returned-something" -Command ("docker exec {0} psql -tAc 'SELECT count(*), count(*) FILTER (WHERE returned>0) FROM agent_memory_recall_traces;'" -f $Ctx.db) -Run $body)

    # (3) THE LINK THAT DECIDES THE CLAUSE: a recall traced to the work that CONSUMED it,
    #     with that effort's own record citing what it was told. Searched for in the record
    #     the operator actually reads - the ledger and the findings notes - by the trace's
    #     own request_id or by the id of a memory that recall returned.
    $r3 = Invoke-Psql -Ctx $Ctx -Sql "SELECT DISTINCT t.request_id::text FROM agent_memory_recall_traces t WHERE (t.response_policy->>'returned')::int > 0;"
    $ids = @(($r3.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[0-9a-f-]{36}$' })
    $r3b = Invoke-Psql -Ctx $Ctx -Sql "SELECT DISTINCT memory_id::text FROM agent_memory_recall_items;"
    $mids = @(($r3b.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[0-9a-f-]{36}$' })
    $needles = @($ids + $mids | Sort-Object -Unique)

    if ($needles.Count -lt 1) {
        $c.coverage.not_evaluated += "recall-informed-a-later-effort"
        $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                -Note "no recall returned a memory, so there is no recall for any later effort to have been informed by"
        $c.coverage.evaluated++
    } else {
        # THE AUDIT RECORD AS IT STOOD BEFORE THE RUN, normalised. A recall id written into
        # a note by a command this authority executed would otherwise prove that the memory
        # plane compounds - by citing evidence the authority manufactured - and a citation
        # inside an HTML comment is not in the record the operator reads.
        $hay = ""
        $hay += ([string](Get-SnapMd -Which "decisions"))
        foreach ($f in @(Get-SnapNotes)) { $hay += ([string]$f.md) }
        $hits = @($needles | Where-Object { $hay -match [regex]::Escape($_) })
        $c.coverage.evaluated++
        if ($hits.Count -ge 1) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("{0} recall id(s) are cited in the audit record, linking a recall to the work that consumed it" -f $hits.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                    -Note ("none of the {0} recall/memory id(s) is cited anywhere in DECISIONS.md or the findings notes - the trace-to-consumer link cannot be demonstrated, so the plane is not shown to COMPOUND" -f $needles.Count)
        }
    }
    $c.probes += (New-Probe -Name "recall-informed-a-later-effort" `
        -Command "psql: request_ids of traces that returned + memory_ids from agent_memory_recall_items ; then search DECISIONS.md and documentation/notes for those ids" -Run $body)
    return (Resolve-ClauseVerdict -Clause $c)
}

# =================================================================================
# THE BOARD. Every clause lands in exactly one counted bucket, the buckets must SUM to
# the clauses in scope, and "done" requires every bucket except `met` to be empty AND
# every one of section C.8's clauses to be in it. Stated positively - never as the
# absence of an objection.
# =================================================================================

$script:ClauseImpl = [ordered]@{
    "1" = "Test-Clause1"; "2" = "Test-Clause2"; "3" = "Test-Clause3"; "4" = "Test-Clause4"
    "5" = "Test-Clause5"; "6" = "Test-Clause6"; "7" = "Test-Clause7"; "8" = "Test-Clause8"
}

if ($ListClauses) {
    Write-Host ""
    Write-Host "DFU-DONE - PLAN.md section C.8, the eight clauses and what implements each" -ForegroundColor Cyan
    Write-Host ""
    foreach ($k in $script:DfuClauses.Keys) {
        $impl = "(NOT IMPLEMENTED)"
        if ($script:ClauseImpl.Contains($k)) { $impl = [string]$script:ClauseImpl[$k] }
        Write-Host ("  {0}. {1}" -f $k, $script:DfuClauses[$k])
        Write-Host ("     implemented by: {0}" -f $impl) -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "  Clause 3's required doors (pinned in code, not config):" -ForegroundColor DarkGray
    foreach ($d in $script:DfuRequiredDoors.Keys) { Write-Host ("     {0} - {1}" -f $d, $script:DfuRequiredDoors[$d]) -ForegroundColor DarkGray }
    Write-Host "  Clause 4's required services:" -ForegroundColor DarkGray
    foreach ($s in $script:DfuRequiredServices.Keys) { Write-Host ("     {0} - {1}" -f $s, $script:DfuRequiredServices[$s]) -ForegroundColor DarkGray }
    Write-Host "  Branches clause 4 MAY exclude - each applied only while DECISIONS.md records it:" -ForegroundColor DarkGray
    foreach ($b in $script:DfuExcludedBranches.Keys) { Write-Host ("     {0} - {1}" -f $b, $script:DfuExcludedBranches[$b]) -ForegroundColor DarkGray }
    Write-Host "  The three pinned sets are checked back against C.8's own words at run time:" -ForegroundColor DarkGray
    Write-Host "    door-set-matches-plan (clause 3), service-set-matches-plan (clause 4)," -ForegroundColor DarkGray
    Write-Host "    phase-floor-matches-plan (clauses 1, 2 and 7 - the U0-U6 floor)." -ForegroundColor DarkGray
    Write-Host ""
    exit 0
}

try { $ctx = Resolve-DfuContext -P $PSBoundParameters }
catch {
    Write-Host ("CONFIG ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}
# Fill defaults the parameter binder did not supply (PSBoundParameters only holds what
# was passed, so an unpassed -DbContainer must still reach the context).
if (-not $ctx.db)        { $ctx.db = $DbContainer }
if (-not $ctx.dbname)    { $ctx.dbname = $DbName }
if (-not $ctx.obnet)     { $ctx.obnet = $ObNetwork }
if (-not $ctx.postgrest) { $ctx.postgrest = $PostgrestHost }
$ctx.skiplive = [bool]$SkipLive
if (-not $ctx.workline) {
    Write-Host "CONFIG ERROR: could not determine the work line (pass -WorkLine)" -ForegroundColor Red
    exit 1
}

$manualStore = Read-JsonStore -Path $ctx.manual

# --- BEFORE ANYTHING RUNS -----------------------------------------------------------
# A killed run must not leave the operator's documents read-only, so any Deny ACE this
# script could have left behind is swept first - then every artifact any clause reads is
# captured, and only then is a single command allowed to execute. The ORDER is the fix:
# see "THE FIFTEENTH CLASS" above.
$protectionCleared = @(Clear-DfuTreeProtection -Ctx $ctx)
$script:DfuSnap = New-DfuSnapshot -Ctx $ctx

# WHICH CLAUSES ARE IN SCOPE. -Only NARROWS the run; the clauses it skips are still
# counted, as `unevaluated`, so a narrowed run can never report "done". There is no way
# to make this script pass by asking it fewer questions.
$inScope = @($script:DfuClauses.Keys)
$skippedByOnly = @()
if ($Only -and @($Only).Count -gt 0) {
    # Compared as STRINGS, because that is what the clause keys are now.
    $onlyStr = @($Only | ForEach-Object { [string]$_ })
    $skippedByOnly = @($inScope | Where-Object { $onlyStr -notcontains $_ })
}

$results = @()
try {
foreach ($k in $inScope) {
    if ($skippedByOnly -contains $k) {
        $c = New-ClauseResult -Id $k
        $c.coverage.subject  = "not requested in this run"
        $c.coverage.expected = 1
        $c.probes += (New-Probe -Name ("clause-{0}-not-requested" -f $k) -Command ("(skipped: -Only {0})" -f ($Only -join ",")) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "this clause was not requested, so it was NOT evaluated - a narrowed run cannot report the plan done"))
        $results += (Resolve-ClauseVerdict -Clause $c)
        continue
    }
    $fn = $null
    if ($script:ClauseImpl.Contains($k)) { $fn = [string]$script:ClauseImpl[$k] }
    if (-not $fn -or -not (Get-Command $fn -ErrorAction SilentlyContinue)) {
        # A CLAUSE WITH NO IMPLEMENTATION REFUSES. It does not vanish and it does not pass.
        $c = New-ClauseResult -Id $k
        $c.coverage.expected = 1
        $c.probes += (New-Probe -Name ("clause-{0}-unimplemented" -f $k) -Command "(none)" `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "section C.8 declares this clause and nothing in this script implements it"))
        $results += (Resolve-ClauseVerdict -Clause $c)
        continue
    }
    try {
        $results += (& $fn -Ctx $ctx -Store $manualStore)
    } catch {
        $c = New-ClauseResult -Id $k
        $c.coverage.expected = 1
        $msg = $_.Exception.Message
        # DFU_TRACE=1 also prints WHERE it threw. A clause that threw reports
        # `clause-N-threw` with the exception message and nothing else, and that message
        # ("the property 'x' cannot be found") is not enough to find the line - this run
        # lost twenty minutes to exactly that. It only PRINTS; no verdict depends on it.
        if ($env:DFU_TRACE) { Write-Host ("TRACE: " + $_.ScriptStackTrace + " :: " + $_.InvocationInfo.PositionMessage) -ForegroundColor Magenta }
        $c.probes += (New-Probe -Name ("clause-{0}-threw" -f $k) -Command $fn `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note ("the clause evaluator threw, so nothing was decided: {0}" -f $msg)))
        $results += (Resolve-ClauseVerdict -Clause $c)
    }
}
} finally {
    # THE SANDBOX BELONGS TO THE RUN, not to a clause - clauses 1 and 5 share it and it is
    # removed exactly once, however the run ends. It is a CLONE, so `git worktree list` in
    # the audited repository never saw it and clause 4 could not have counted it.
    Remove-DfuSandbox -Ctx $ctx
    [void](Clear-DfuTreeProtection -Ctx $ctx)
}

# --- THE INTEGRITY OF THE MEASUREMENT ----------------------------------------------
# A checker that mutates what it measures is not an authority. Every artifact any clause
# reads was fingerprinted before the first command ran; it is fingerprinted again here,
# and the two must be identical. This is NOT a clause of C.8 - it is this script's
# statement about its own run - so it does not enter the census; it VETOES it. `done`
# requires it, and it is printed whether it holds or not.
$finalFp   = Get-AuditedFingerprint -Ctx $ctx
$runMoved  = @(Compare-DfuFingerprint -Before $script:DfuSnap.fingerprint -After $finalFp)
$integrity = [ordered]@{
    snapshot_taken_at = [string]$script:DfuSnap.taken_at
    commands_executed = @($script:DfuExecLog).Count
    protection_swept_at_start = @($protectionCleared)
    moved_during_run  = @($runMoved)
    per_command_drift = @($script:DfuIntegrity)
    ok                = ((@($runMoved).Count -eq 0) -and (@($script:DfuIntegrity).Count -eq 0))
}

# --- THE CENSUS -------------------------------------------------------------------
$census    = [ordered]@{}
$censusIds = [ordered]@{}
foreach ($b in (Get-DfuBucketNames)) { $census[$b] = 0; $censusIds[$b] = @() }
$unaccounted = @()
foreach ($r in $results) {
    $b = Get-DfuBucket ([string]$r.verdict)
    $r["bucket"] = $b
    if (-not $census.Contains($b)) {
        $unaccounted += ("clause {0}: verdict '{1}' classified as '{2}', which is not a declared census bucket" -f $r.id, $r.verdict, $b)
        continue
    }
    $census[$b]    = [int]$census[$b] + 1
    $censusIds[$b] = @($censusIds[$b]) + @(("clause {0}" -f $r.id))
}
$censusTotal = 0
foreach ($k in $census.Keys) { $censusTotal += [int]$census[$k] }
$censusBalances = ($censusTotal -eq @($results).Count)

# `done` requires ALL of: the census balances, nothing escaped it, every bucket but
# `met` is empty, and `met` holds every clause section C.8 declares. The last conjunct
# is what stops a run that evaluated three clauses from reporting the plan complete.
$nonClear = @()
foreach ($k in $census.Keys) {
    if ($k -eq $script:DfuClearBucket) { continue }
    if ([int]$census[$k] -gt 0) { $nonClear += $k }
}
$isDone = ($censusBalances -and (@($unaccounted).Count -eq 0) -and (@($nonClear).Count -eq 0) -and
           ([int]$census[$script:DfuClearBucket] -eq @($script:DfuClauses.Keys).Count) -and
           [bool]$integrity.ok)

$board   = "done"
$reasons = @()
if (-not $isDone) {
    if (-not $integrity.ok) {
        # THE VETO IS NAMED FIRST, because every clause verdict below it was decided over a
        # world this run is no longer able to vouch for.
        $board = "unaccounted"
        foreach ($x in @($integrity.per_command_drift)) { $reasons += $x }
        if (@($integrity.moved_during_run).Count -gt 0) {
            $reasons += ("the audited tree MOVED during the run: {0} - a checker that changes what it measures is not an authority, so no verdict here is offered as one" -f (@($integrity.moved_during_run) -join ", "))
        }
    } elseif ((-not $censusBalances) -or (@($unaccounted).Count -gt 0)) {
        $board = "unaccounted"
        $reasons = @($unaccounted)
        if (-not $censusBalances) {
            $reasons += ("the census counted {0} outcome(s) for {1} clause(s) - something landed in no bucket" -f $censusTotal, @($results).Count)
        }
    } else {
        foreach ($k in $census.Keys) {
            if ($k -eq $script:DfuClearBucket) { continue }
            if ([int]$census[$k] -lt 1) { continue }
            if (-not $reasons) { $board = [string]$script:DfuBucketBoard[$k] }
            $reasons += ("{0} clause(s) in the '{1}' bucket: {2}" -f [int]$census[$k], $k, (@($censusIds[$k]) -join ", "))
        }
        if (-not $reasons) {
            $board = "unaccounted"
            $reasons += ("every bucket but '{0}' is empty, yet it holds {1} of {2} declared clauses" -f `
                         $script:DfuClearBucket, [int]$census[$script:DfuClearBucket], @($script:DfuClauses.Keys).Count)
        }
    }
}

$verdict = [ordered]@{
    board        = $board
    done         = $isDone
    work_line    = $ctx.workline
    evaluated_at = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ssK")
    skip_live    = [bool]$ctx.skiplive
    census       = $census
    census_ids   = $censusIds
    census_total = $censusTotal
    balances     = $censusBalances
    integrity    = $integrity
    executed_commands = @($script:DfuExecLog)
    unaccounted  = @($unaccounted)
    reasons      = @($reasons)
    clauses      = @($results)
}

if ($Json) {
    $verdict | ConvertTo-Json -Depth 8
    if ($isDone) { exit 0 } else { exit 7 }
}

Write-Host ""
Write-Host "=========================================================================" -ForegroundColor Cyan
Write-Host " DFU-DONE - is the dark-factory-unification plan 100% met?" -ForegroundColor Cyan
Write-Host ("   work line : {0}" -f $ctx.workline)
Write-Host ("   plan      : {0}" -f $ctx.plan)
if ($ctx.skiplive) { Write-Host "   -SkipLive : live probes were NOT run; those clauses are UNEVALUATED" -ForegroundColor Yellow }
Write-Host "=========================================================================" -ForegroundColor Cyan

foreach ($r in $results) {
    $colour = "Green"
    if ($r.verdict -ne "met") { $colour = "Yellow" }
    if ($r.verdict -eq "unmet" -or $r.bucket -eq $script:DfuUnrecognisedBucket) { $colour = "Red" }
    Write-Host ""
    Write-Host ("CLAUSE {0} [{1}] {2}" -f $r.id, $r.verdict.ToUpperInvariant(), $r.title) -ForegroundColor $colour
    # COVERAGE IS PRINTED FOR EVERY CLAUSE - this is the "clear because we looked" vs
    # "clear because we didn't" distinction, and it is the whole reason the clause exists.
    Write-Host ("   coverage: evaluated {0} of {1} {2}" -f $r.coverage.evaluated, $r.coverage.expected, $r.coverage.subject) -ForegroundColor DarkGray
    if (@($r.coverage.not_evaluated).Count -gt 0) {
        Write-Host ("   NOT evaluated: {0}" -f (@($r.coverage.not_evaluated) -join ", ")) -ForegroundColor Yellow
    }
    foreach ($p in $r.probes) {
        $pc = "Gray"
        if ($p.verdict -eq "pass") { $pc = "Green" }
        elseif ($p.verdict -eq "fail") { $pc = "Red" }
        else { $pc = "Yellow" }
        $ex = "n/a"
        if ($null -ne $p.exit) { $ex = [string]$p.exit }
        Write-Host ("   [{0}] {1} (exit {2})" -f $p.verdict, $p.name, $ex) -ForegroundColor $pc
        Write-Host ("        $ {0}" -f $p.command) -ForegroundColor DarkGray
        if ($p.note) { Write-Host ("        {0}" -f $p.note) -ForegroundColor DarkGray }
    }
    foreach ($m in $r.manual) {
        $mc = "Yellow"; if ($m.state -eq "recorded") { $mc = "Green" } elseif ($m.state -eq "recorded-fail") { $mc = "Red" }
        Write-Host ("   [MANUAL:{0}] {1}" -f $m.state, $m.name) -ForegroundColor $mc
        Write-Host ("        {0}" -f $m.what) -ForegroundColor DarkGray
        if ($m.recorded) { Write-Host ("        recorded: {0}" -f $m.recorded) -ForegroundColor DarkGray }
        else { Write-Host ("        NO RECORDED RESULT - record one in {0} under this exact name" -f $ctx.manual) -ForegroundColor DarkGray }
    }
    foreach ($d in $r.detail) { Write-Host ("   . {0}" -f $d) -ForegroundColor DarkGray }
}

# --- WHAT THE AUTHORITY DID, not only what it concluded -----------------------------
# This section exists because clauses 1 and 5 EXECUTE instructions taken from the document
# under test. A reader is entitled to see that command set - it is the part of this run
# that could have had effects - and to see, per command, where it ran and whether the
# audited tree moved while it did.
Write-Host ""
Write-Host "-------------------------------------------------------------------------"
Write-Host " COMMANDS THIS AUTHORITY EXECUTED (taken from WALKTHROUGH.md, run in a clone)" -ForegroundColor Cyan
if (@($script:DfuExecLog).Count -lt 1) {
    Write-Host "   (none - no walkthrough command was executed in this run)" -ForegroundColor DarkGray
} else {
    foreach ($e in @($script:DfuExecLog)) {
        $ex = "n/a"; if ($null -ne $e.exit) { $ex = [string]$e.exit }
        $mark = "   "
        $col  = "DarkGray"
        if (@($e.drift).Count -gt 0) { $mark = " ! "; $col = "Red" }
        Write-Host ("{0}clause {1} / {2} (exit {3})" -f $mark, $e.clause, $e.phase, $ex) -ForegroundColor $col
        Write-Host ("        $ {0}" -f $e.command) -ForegroundColor DarkGray
        Write-Host ("        in {0}" -f $e.ran_in) -ForegroundColor DarkGray
        if (@($e.lock_failed).Count -gt 0) {
            Write-Host ("        COULD NOT LOCK: {0} - containment fell back to before/after fingerprinting" -f (@($e.lock_failed) -join " ; ")) -ForegroundColor Yellow
        }
        if (@($e.drift).Count -gt 0) {
            Write-Host ("        MOVED THE AUDITED TREE: {0}" -f (@($e.drift) -join ", ")) -ForegroundColor Red
        }
    }
}
Write-Host ""
Write-Host ("   snapshot of every artifact a clause reads was taken at {0}, BEFORE the first command" -f $integrity.snapshot_taken_at) -ForegroundColor DarkGray
if (@($integrity.protection_swept_at_start).Count -gt 0) {
    Write-Host ("   stale write-locks removed at startup: {0}" -f (@($integrity.protection_swept_at_start) -join " ; ")) -ForegroundColor Yellow
}
if ($integrity.ok) {
    Write-Host "   INTEGRITY: the audited tree is byte-identical before and after this run." -ForegroundColor Green
} else {
    Write-Host "   INTEGRITY: FAILED - this run CHANGED the world it was measuring:" -ForegroundColor Red
    foreach ($x in @($integrity.per_command_drift)) { Write-Host ("     - {0}" -f $x) -ForegroundColor Red }
    if (@($integrity.moved_during_run).Count -gt 0) {
        Write-Host ("     - net change over the whole run: {0}" -f (@($integrity.moved_during_run) -join ", ")) -ForegroundColor Red
    }
    Write-Host "     Every clause above still decided over the PRE-RUN snapshot, so nothing" -ForegroundColor Yellow
    Write-Host "     was discharged by this - but the run cannot be offered as an authority." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "-------------------------------------------------------------------------"
Write-Host " CENSUS (every clause in exactly one bucket; the buckets must sum)" -ForegroundColor Cyan
foreach ($k in $census.Keys) {
    Write-Host ("   {0,-16} {1}" -f $k, [int]$census[$k])
}
Write-Host ("   total {0} for {1} clause(s) - balances: {2}" -f $censusTotal, @($results).Count, $censusBalances)
Write-Host ""
if ($isDone) {
    Write-Host " PLAN 100% MET - every C.8 clause is satisfied by a check that ran." -ForegroundColor Green
    Write-Host " The factory STOPS and hands over. This is the handover point, not the" -ForegroundColor Green
    Write-Host " finish line: the operator's walkthrough is the last gate and it is theirs." -ForegroundColor Green
    Write-Host ""
    exit 0
}
Write-Host (" NOT DONE - board: {0}" -f $board.ToUpperInvariant()) -ForegroundColor Red
foreach ($x in $reasons) { Write-Host ("   - {0}" -f $x) -ForegroundColor Red }
Write-Host ""
Write-Host " This is a REPORT, not a redefinition (C.8). Amending a plan column so this" -ForegroundColor Yellow
Write-Host " script goes green is the one move that section exists to forbid." -ForegroundColor Yellow
Write-Host ""
exit 7
