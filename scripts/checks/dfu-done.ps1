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
# ABOUT RULE 3, PRECISELY. Phases, branches, worktrees, walkthrough rows, the PostgREST
# surface and the RLS stage set are all read from the tree or the live schema. Two sets are
# NOT derived and must not pretend to be: clause 3's door floor and clause 4's service set
# come from section C.8's own prose, because C.8 is the specification and a config file
# could otherwise be thinned to the doors that pass. What keeps them honest is that they
# are CHECKED BACK against the plan's words at run time (`door-set-matches-plan`), so a
# door the plan names and this file does not probe turns the clause red instead of
# disappearing. A pinned floor plus a drift check is a different thing from a hand list.
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
# Exit codes: 0 every clause MET - the factory stops and hands over (C.8's handover
#               point, not the finish line; the operator's walkthrough is the last gate)
#             1 usage or configuration error - nothing was judged
#             7 the plan is NOT met. The headline word comes from the most severe
#               non-empty bucket, and each refuses:
#                 unaccounted     a clause produced a verdict this script does not
#                                 enumerate, or the census did not balance
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

function Get-PhaseTable {
    # Parse section 2's phase table out of a PLAN.md TEXT (not a path), so the same
    # parser reads the working file and any historical revision from git.
    # Returns @{ U0 = @{ what=..; validated=..; depends=.. }; ... }
    param([string]$Text)
    $out = [ordered]@{}
    if (-not $Text) { return $out }
    foreach ($line in ($Text -split "`n")) {
        $l = $line.TrimEnd("`r")
        if ($l -notmatch '^\|\s*\*\*(U\d)') { continue }
        $id = $Matches[1]
        # A markdown row: | phase | what | validated by | depends on |
        $cells = @($l -split '\|')
        # split leaves an empty first and last element for a well-formed row
        if ($cells.Count -lt 6) { continue }
        $out[$id] = [ordered]@{
            what      = $cells[2].Trim()
            validated = $cells[3].Trim()
            depends   = $cells[4].Trim()
        }
    }
    return $out
}

function Get-WalkthroughRuns {
    # Every "How to run:" command in the walkthrough, keyed by the phase whose section it
    # sits in. DERIVED: the phase headings and the run lines both come from the file, so a
    # phase that grows a section, or loses one, changes this map without anyone editing a
    # list here.
    #
    # IT MUST READ ACROSS LINE BREAKS. The walkthrough wraps long commands inside a single
    # backtick span, so a line-oriented parser silently captures the FIRST LINE ONLY and
    # then "runs" a truncated command - which is the alphabet-too-narrow class producing a
    # confident wrong answer rather than an error. The span is therefore matched whole,
    # newlines included, and re-flowed to one line.
    param([string]$Text)
    $out = [ordered]@{}
    if (-not $Text) { return $out }
    # Split into sections on level-2 headings, keeping the heading with its body.
    $parts = [regex]::Split($Text, '(?m)^(?=##\s)')
    foreach ($part in $parts) {
        if ($part -notmatch '(?m)^##\s+\**(U\d)') { continue }
        $id = $Matches[1]
        if (-not $out.Contains($id)) { $out[$id] = @() }
        $rx = [regex]'(?s)\*\*How to run:?\*\*\s*`([^`]+)`'
        foreach ($m in $rx.Matches($part)) {
            $cmd = ($m.Groups[1].Value -replace '\s+', ' ').Trim()
            if ($cmd) { $out[$id] = @($out[$id]) + @($cmd) }
        }
    }
    return $out
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

    $planText = Read-TextFile -Path $Ctx.plan
    if (-not $planText) {
        $c.probes += (New-Probe -Name "read-plan" -Command ("read {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "PLAN.md is unreadable or missing - the phase set could not be derived, so nothing was judged"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    $phases = Get-PhaseTable -Text $planText
    # U0-U6 are this clause's subjects; U7 is standing by design and belongs to clause 6.
    $ids = @($phases.Keys | Where-Object { $_ -match '^U[0-6]$' } | Sort-Object)
    $c.coverage.subject  = "U-phases U0-U6, derived from section 2's table in PLAN.md"
    $c.coverage.expected = $ids.Count
    if ($ids.Count -lt 1) {
        $c.probes += (New-Probe -Name "derive-phases" -Command ("parse section 2 table in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "the phase table parsed to ZERO rows - the parser and the document disagree, which is not a pass"))
        return (Resolve-ClauseVerdict -Clause $c)
    }

    $runs = Get-WalkthroughRuns -Text (Read-TextFile -Path $Ctx.walkthrough)

    $clean = New-CleanCheckout -Ctx $Ctx
    $cleanPath = $clean.path
    $c.detail += ("clean checkout of '{0}': {1} (exit {2})" -f $Ctx.workline, $clean.command, $clean.exit)
    $c.detail += ("clean checkout submodules: git submodule update --init --recursive (exit {0}) {1}" -f `
                  $clean.submodule_exit, $clean.submodule_err)
    if ($clean.exit -ne 0) {
        # NO CLEAN CHECKOUT = NOTHING WAS PROVEN. Falling back to the current working tree
        # would be exactly the substitution this clause forbids, so it refuses instead.
        $c.probes += (New-Probe -Name "clean-checkout" -Command $clean.command `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $clean.exit `
                  -Note ("could not create a clean checkout of '{0}', so NO phase check was re-run: {1}" -f `
                         $Ctx.workline, (($clean.err -replace "\s+", " ").Trim()))))
        $c.coverage.not_evaluated = @($ids)
        return (Resolve-ClauseVerdict -Clause $c)
    }

    try {
        foreach ($id in $ids) {
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
            $n = 0
            $ranAll = $true
            $anyRed = $false
            $refs   = @()
            foreach ($cmd in $cmds) {
                $n++
                $pname = ("{0}-validated-by-{1}" -f $id, $n)
                $refs += (Get-NamedArtifacts -Text $cmd)
                $r = Invoke-Native -Exe "cmd.exe" -Arguments @("/c", $cmd) -WorkDir $cleanPath
                if (-not $r.ran -or $null -eq $r.exit) {
                    $ranAll = $false
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                            -Note ("the command could not be started in the clean checkout: {0}" -f (($r.stderr -replace "\s+", " ").Trim()))
                } elseif ([int]$r.exit -eq 0) {
                    $body = New-VerdictProbeBody -Verdict "pass" -Exit ([int]$r.exit) -Note "re-ran GREEN in the clean checkout"
                } else {
                    $anyRed = $true
                    $tail = @(($r.stdout + "`n" + $r.stderr) -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
                    $body = New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) `
                            -Note ("exited {0} in the clean checkout: {1}" -f $r.exit, (($tail -join " ") -replace "\s+", " ").Trim())
                }
                $c.probes += (New-Probe -Name $pname -Command $cmd -Run $body)
            }
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
        # ALWAYS removed. A leaked scratch worktree would be counted by clause 4 as
        # unfinished work - this script must not manufacture the defect it reports.
        Remove-CleanCheckout -Ctx $Ctx -Path $cleanPath
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
    $decText = Read-TextFile -Path $Ctx.decisions
    if (-not $decText) {
        $c.probes += (New-Probe -Name "decisions-readable" -Command ("read {0}" -f $Ctx.decisions) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "DECISIONS.md is unreadable or missing - parked entries could not be counted"))
    } else {
        $headings = @()
        foreach ($line in ($decText -split "`n")) {
            $l = $line.TrimEnd("`r")
            if ($l -match '^##\s+(.*)$') { $headings += $Matches[1].Trim() }
        }
        $parked = @($headings | Where-Object { $_ -match '(?i)\bPARKED\b' })
        # A phase is un-parked only by a LATER heading that says so for the same phase.
        $outstanding = @()
        foreach ($p in $parked) {
            $phase = ""
            if ($p -match '\b(U\d)\b') { $phase = $Matches[1] }
            $idx = [array]::IndexOf($headings, $p)
            $closedLater = $false
            if ($phase) {
                for ($i = $idx + 1; $i -lt $headings.Count; $i++) {
                    if ($headings[$i] -match ('\b' + $phase + '\b') -and
                        $headings[$i] -match '(?i)(CLOSES|CLOSED|DISCHARGED|UNPARKED)') { $closedLater = $true; break }
                }
            }
            if (-not $closedLater) { $outstanding += $p }
        }
        if ($outstanding.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "no outstanding PARKED entry in DECISIONS.md"
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $outstanding.Count `
                    -Note ("{0} outstanding PARKED entry/entries, none closed by a later heading: {1}" -f `
                           $outstanding.Count, ($outstanding -join " | "))
        }
        $c.probes += (New-Probe -Name "no-outstanding-parked" `
                      -Command ("grep '^## ' {0} | grep -i PARKED" -f $Ctx.decisions) -Run $body)
    }

    # --- (b) every section 2.1 amendment carries evidence + revert path -----------
    $planText = Read-TextFile -Path $Ctx.plan
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
    $revs = Get-PlanRevisions -Ctx $Ctx
    $curPhases = Get-PhaseTable -Text $planText
    $ids = @($curPhases.Keys | Sort-Object)
    $c.coverage.subject  = "phases whose Validated-by chain must be reconstructable, derived from section 2's table"
    $c.coverage.expected = $ids.Count

    if ($null -eq $revs -or @($revs).Count -lt 1) {
        $c.probes += (New-Probe -Name "chain-history" -Command ("git log --follow -- {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "no revision history for PLAN.md could be read - no chain can be reconstructed, which is not a pass"))
        $c.coverage.not_evaluated = @($ids)
        return (Resolve-ClauseVerdict -Clause $c)
    }
    $unreadable = @($revs | Where-Object { -not $_.readable })
    if ($unreadable.Count -gt 0) {
        $shas = (($unreadable | ForEach-Object { $_.sha.Substring(0, 7) }) -join ",")
        $c.probes += (New-Probe -Name "chain-revisions-readable" `
            -Command ("git show <sha>:<plan> across {0} revisions" -f @($revs).Count) `
            -Run (New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                  -Note ("PLAN.md could not be read at {0} revision(s) ({1}) - an unreconstructable history is indistinguishable from an unrecorded one" -f $unreadable.Count, $shas)))
    }

    foreach ($id in $ids) {
        $chain = @()
        foreach ($r in $revs) {
            if (-not $r.readable) { continue }
            $t = Get-PhaseTable -Text $r.text
            if (-not $t.Contains($id)) { continue }
            $vb = [string]$t[$id].validated
            if ($chain.Count -eq 0 -or (ConvertTo-Normalised -s $chain[-1].text) -ne (ConvertTo-Normalised -s $vb)) {
                $chain += @{ sha = $r.sha; date = $r.date; subject = $r.subject; text = $vb }
            }
        }
        # THE CHAIN IS PRINTED, dated and verbatim at each step - that is the clause's
        # own requirement, and it is what lets a reader disagree with the verdict.
        $c.detail += ("chain {0}: {1} distinct state(s)" -f $id, $chain.Count)
        foreach ($step in $chain) {
            $c.detail += ("    {0} {1} : {2}" -f $step.date, $step.sha.Substring(0, 7), $step.text)
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
                     $chain[0].date, $chain[0].sha.Substring(0, 7), $chain[-1].date, $chain[-1].sha.Substring(0, 7), $id)

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
    $c.coverage.expected = $doors.Count + 3

    # --- (0) THE DOOR SET STILL MATCHES THE PLAN'S OWN WORDS ----------------------
    # A pinned list is only honest while something checks it against what it is pinned to.
    # Every backticked identifier inside C.8 clause 3, plus the three door names that
    # section writes in prose, must be claimed by a subject above.
    $planTxt = Read-TextFile -Path $Ctx.plan
    $sec = ""
    if ($planTxt) {
        $m = [regex]::Match($planTxt, '(?s)3\.\s\*\*The personal-plane constraint.*?(?=\n4\.\s\*\*Nothing is left in flight)')
        if ($m.Success) { $sec = $m.Value }
    }
    if (-not $sec) {
        $c.probes += (New-Probe -Name "door-set-matches-plan" -Command ("locate C.8 clause 3 in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note "C.8 clause 3's text could not be located in the plan, so the door set could not be checked against it"))
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

    if ($Ctx.skiplive) {
        foreach ($d in $doors) {
            $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: -SkipLive)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "-SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed"))
        }
        $c.coverage.not_evaluated = @($doors)
        foreach ($n in @("corpus-predicate-fail-closed", "postgrest-surface-sweep")) {
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
    $canary = "DFU-DONE-CANARY-" + [guid]::NewGuid().ToString("N").Substring(0, 8)
    $sqlPred = ("BEGIN; " +
        "INSERT INTO thoughts (content, metadata) VALUES ('{0}-UNLABELLED','{{}}'::jsonb); " +
        "INSERT INTO thoughts (content, metadata) VALUES ('{0}-OPS', jsonb_build_object('exposure','ops')); " +
        "SET ROLE service_role; " +
        "SELECT 'U:'||count(*) FROM thoughts WHERE content='{0}-UNLABELLED'; " +
        "SELECT 'O:'||count(*) FROM thoughts WHERE content='{0}-OPS'; " +
        "RESET ROLE; ROLLBACK;") -f $canary
    $rp = Invoke-Psql -Ctx $Ctx -Sql $sqlPred
    if (-not $rp.ran -or $null -eq $rp.exit) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "the database could not be reached, so the predicate was not tested"
    } elseif ([int]$rp.exit -ne 0) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note ("psql exited non-zero: {0}" -f (($rp.out -replace "\s+", " ").Trim()))
    } else {
        $u = (($rp.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^U:\d+$' } | Select-Object -First 1)
        $o = (($rp.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^O:\d+$' } | Select-Object -First 1)
        if (-not $u -or -not $o) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note "the query returned no counts at all - nothing was decided"
        } else {
            $un = [int]($u -replace '^U:', ''); $on = [int]($o -replace '^O:', '')
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            if ($on -lt 1) {
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit 0 `
                        -Note "the OPS control row was invisible too, so this query cannot see anything - the unlabelled row's absence proves nothing"
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
    # The ops twin carries share=cloud as well, because the cloud door's forced read filter
    # is `share`, not `exposure` - a control the cloud gateway would filter out is not a
    # control.
    $ids = [ordered]@{}
    foreach ($pair in @(@("p", $pmark, "personal"), @("o", $omark, "ops"))) {
        $tag = $pair[0]; $mk = $pair[1]; $exp = $pair[2]
        $share = $(if ($exp -eq "ops") { ",'share','cloud'" } else { "" })
        $q = "INSERT INTO thoughts (content, metadata) VALUES ('{0} {1} fixture', jsonb_build_object('exposure','{1}','dfu_done_fixture',true{2})) RETURNING id;" -f $mk, $exp, $share
        $r = Invoke-Psql -Ctx $Ctx -Sql $q
        if ($r.ran -and $r.exit -eq 0) {
            $mm = [regex]::Match(($r.out -replace "\s+", " "), '(\d+)')
            if ($mm.Success) { $ids[($tag + "thought")] = $mm.Groups[1].Value }
        }
        $q2 = ("INSERT INTO agent_memories (workspace_id, memory_type, summary, content, metadata) " +
               "VALUES ('dfu-done-fixture','check','{0} {1} twin','{0} {1} fixture body', " +
               "jsonb_build_object('exposure','{1}','dfu_done_fixture',true)) RETURNING id;") -f $mk, $exp
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
                    $r = Invoke-McpTool -Ctx $Ctx -Url "http://openbrain-mcp:8000/mcp" -Header ("x-brain-key: " + $key) `
                                        -Secret $key -Tool "list_thoughts" -ArgumentsJson '{"limit":25}'
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
                    $r = Invoke-McpTool -Ctx $Ctx -Url "http://openbrain-gateway:8061/mcp" -Header ("Authorization: Bearer " + $key) `
                                        -Secret $key -Tool "list_thoughts" -ArgumentsJson '{"limit":25}'
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
        # Text-ish columns per relation, from information_schema - the same reflection
        # PostgREST itself does.
        $colMap = @{}
        $rcols = Invoke-Psql -Ctx $Ctx -Sql ("SELECT table_name||'|'||string_agg(column_name, ',' ORDER BY ordinal_position) " +
                                             "FROM information_schema.columns WHERE table_schema='public' " +
                                             "AND data_type IN ('text','character varying','character','name') GROUP BY table_name;")
        if ($rcols.ran -and $rcols.exit -eq 0) {
            foreach ($line in ($rcols.out -split "`n")) {
                $l = $line.Trim()
                if ($l -notmatch '^([A-Za-z0-9_]+)\|(.+)$') { continue }
                $colMap[$Matches[1]] = @(($Matches[2] -split ',') | Where-Object { $_ } | Select-Object -First 4)
            }
        }
        if ($paths.Count -lt 1 -or $colMap.Count -lt 1) {
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @("postgrest-surface-sweep")
            $c.probes += (New-Probe -Name "postgrest-surface-sweep" -Command ("curl 'http://{0}/' ; psql information_schema.columns" -f $Ctx.postgrest) `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$sp.status) `
                      -Note ("the exposed surface could not be enumerated ({0} path(s) from PostgREST, {1} relation(s) with text columns from the schema)" -f $paths.Count, $colMap.Count)))
        } else {
            $urls = @()
            foreach ($t in $paths) {
                $cols = @()
                if ($colMap.ContainsKey($t)) { $cols = @($colMap[$t]) }
                if ($cols.Count -lt 1) { $noText += $t; continue }
                $filter = (($cols | ForEach-Object { "{0}.like.*{1}*" -f $_, $pmark }) -join ",")
                $urls += ("http://{0}/{1}?or=({2})&select={3}&limit=1" -f $Ctx.postgrest, $t, $filter, $cols[0])
                $swept += $t
            }
            $c.detail += ("surface sweep: {0} exposed path(s) from PostgREST, {1} swept, {2} with no text column" -f $paths.Count, $swept.Count, $noText.Count)
            $control = ("http://{0}/thoughts?content=like.*{1}*&select=id&limit=1" -f $Ctx.postgrest, $omark)
            $all = @($urls) + @($control)
            $rs = Invoke-Curl -Ctx $Ctx -Url $all
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
                        -Note ("{0} exposed table(s) swept for the personal marker and none returned it, with the ops control returning; {1} table(s) have no text column to sweep: {2}" -f `
                               $swept.Count, $noText.Count, ($noText -join ", "))
            }
            $c.probes += (New-Probe -Name "postgrest-surface-sweep" `
                          -Command ("curl (from {0}) one filtered query per exposed table, derived from PostgREST's own OpenAPI document, plus an ops-twin control" -f $Ctx.obnet) -Run $body)
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
    $subjects = @("work-branches", "worktrees", "clean-repo", "clean-submodules", "gitlink-reachable") + @($script:DfuRequiredServices.Keys)
    $c.coverage.subject  = "in-flight checks plus the services this plan adds"
    $c.coverage.expected = $subjects.Count

    # --- unmerged work/* branches ------------------------------------------------
    # An exclusion is only honoured while the LEDGER records it (see DfuExcludedBranches).
    $decForBranches = Read-TextFile -Path $Ctx.decisions
    $branches = Get-WorkBranches -Ctx $Ctx
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
                if ($decForBranches -and $decForBranches.Contains($b)) {
                    $skipped += ("{0} ({1}; recorded in {2})" -f $b, $script:DfuExcludedBranches[$b], $Ctx.decisions)
                    continue
                }
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
    $wts = Get-Worktrees -Ctx $Ctx
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
    $gs = Invoke-Git -Arguments @("status", "--porcelain") -WorkDir $Ctx.root
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

    $gm = Invoke-Git -Arguments @("submodule", "status", "--recursive") -WorkDir $Ctx.root
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
    $gl = Invoke-Git -Arguments @("ls-tree", $Ctx.workline, "OB1") -WorkDir $Ctx.root
    $pin = ""
    if ($gl.exit -eq 0) {
        $m = [regex]::Match($gl.stdout, 'commit\s+([0-9a-f]{40})')
        if ($m.Success) { $pin = $m.Groups[1].Value }
    }
    $ob1 = Join-Path $Ctx.root "OB1"
    if (-not $pin) {
        $c.coverage.not_evaluated += "gitlink-reachable"
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $gl.exit -Note "could not read the OB1 gitlink from the work line"
    } else {
        $lr = Invoke-Git -Arguments @("ls-remote", "origin") -WorkDir $ob1
        if ($lr.exit -ne 0) {
            # A GATE THAT CANNOT SEE THE REMOTE MUST REFUSE, NOT PASS.
            $c.coverage.not_evaluated += "gitlink-reachable"
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $lr.exit `
                    -Note "the OB1 remote could not be queried - 'could not check' is not 'fine'"
        } else {
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            if ($lr.stdout -match [regex]::Escape($pin)) {
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("the pinned OB1 commit {0} is a ref tip on the remote" -f $pin.Substring(0, 7))
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
                    if (-not $r.ran) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "docker unavailable" }
                    elseif ($r.stdout -match 'openbrain-ops-gateway') { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "running" }
                    else { $c.coverage.evaluated++; $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("{0} is NOT running" -f $what) }
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
                $cmd = ("psql: relrowsecurity/relforcerowsecurity for the corpus tables AND every table with a foreign key into them ; git ls-tree {0} OB1 -> submodule cat-file" -f $Ctx.workline)
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
                    if (-not $flags) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $r.exit -Note "could not read the RLS flags for any stage table" }
                    else {
                        $c.coverage.evaluated++
                        if ($unbound.Count -eq 0 -and $srcOk) {
                            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                                    -Note ("RLS is enabled and FORCED on all {0} stage table(s), and its source is on the work line" -f $stages.Count)
                        } elseif ($unbound.Count -gt 0) {
                            $body = New-VerdictProbeBody -Verdict "fail" -Exit $unbound.Count `
                                    -Note ("{0} of {1} stage table(s) are not relrowsecurity/relforcerowsecurity = t/t: {2}" -f `
                                           $unbound.Count, $stages.Count, ($unbound -join ", "))
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
    $text = Read-TextFile -Path $Ctx.walkthrough
    if (-not $text) {
        $c.probes += (New-Probe -Name "walkthrough-readable" -Command ("read {0}" -f $Ctx.walkthrough) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "WALKTHROUGH.md is unreadable or missing"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    # Subjects DERIVED: every phase section the walkthrough actually has.
    $sections = @()
    foreach ($part in [regex]::Split($text, '(?m)^(?=##\s)')) {
        if ($part -match '(?m)^##\s+\**(U\d)') { $sections += $Matches[1] }
    }
    $sections = @($sections | Sort-Object -Unique)
    $runs = Get-WalkthroughRuns -Text $text
    $c.coverage.subject  = "phase sections in WALKTHROUGH.md"
    $c.coverage.expected = $sections.Count
    if ($sections.Count -lt 1) {
        $c.probes += (New-Probe -Name "walkthrough-sections" -Command ("parse '## U<n>' sections in {0}" -f $Ctx.walkthrough) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "no phase sections were parsed - the parser and the document disagree"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    foreach ($id in $sections) {
        $cmds = @()
        if ($runs.Contains($id)) { $cmds = @($runs[$id]) }
        if ($cmds.Count -lt 1) {
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
            $c.probes += (New-Probe -Name ("walkthrough-{0}-names-a-check" -f $id) `
                -Command ("(none - no 'How to run' in {0}'s section)" -f $id) `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "this row names NO check, so there is nothing to re-run - a row whose check does not run is worse than a missing row"))
            continue
        }
        # EVERY check the row names runs. Taking $cmds[0] left the section's additional
        # named checks unrun while the coverage line still read full - and this is the
        # document the operator reviews by, so a row whose second command was never
        # executed is a row that says something this script did not verify.
        $n = 0
        $ranAll = $true
        foreach ($cmd in $cmds) {
            $n++
            $r = Invoke-Native -Exe "cmd.exe" -Arguments @("/c", $cmd) -WorkDir $Ctx.root
            if (-not $r.ran -or $null -eq $r.exit) {
                $ranAll = $false
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "the named check could not be started"
            } elseif ([int]$r.exit -eq 0) {
                $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "the row's named check re-runs green"
            } else {
                $body = New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) -Note ("the row's named check exited {0}" -f $r.exit)
            }
            $c.probes += (New-Probe -Name ("walkthrough-{0}-check-{1}" -f $id, $n) -Command $cmd -Run $body)
        }
        if ($ranAll) { $c.coverage.evaluated++ }
        else { $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id) }
    }
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

    $decText = Read-TextFile -Path $Ctx.decisions
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

function Test-Clause7 {
    # C.8.7 - the audit trail is complete, because it is what the operator reads instead
    # of the diffs: every phase has its DECISIONS entries, its findings note, and commit
    # messages stating what was validated and by which check.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 7
    $planText = Read-TextFile -Path $Ctx.plan
    $phases = Get-PhaseTable -Text $planText
    $ids = @($phases.Keys | Sort-Object)
    $c.coverage.subject  = "phases from section 2's table, each needing a ledger entry and a findings note"
    $c.coverage.expected = $ids.Count
    if ($ids.Count -lt 1) {
        $c.probes += (New-Probe -Name "audit-phases" -Command ("parse section 2 table in {0}" -f $Ctx.plan) `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "no phases parsed, so no audit trail could be checked"))
        return (Resolve-ClauseVerdict -Clause $c)
    }
    $decText = Read-TextFile -Path $Ctx.decisions
    $noteFiles = @()
    if (Test-Path -LiteralPath $Ctx.notes) {
        $noteFiles = @(Get-ChildItem -LiteralPath $Ctx.notes -Filter "*.md" -File -ErrorAction SilentlyContinue)
    }
    foreach ($id in $ids) {
        $headings = @()
        if ($decText) {
            foreach ($line in ($decText -split "`n")) {
                $l = $line.TrimEnd("`r")
                # TWO THINGS GO WRONG HERE IF YOU ARE CASUAL, and both did:
                #   1. the SECOND -match REPLACES $Matches, and the phase pattern has no
                #      capture group, so $Matches[1] became null and .Trim() threw;
                #   2. -notmatch does NOT populate $Matches at all, so guarding with it and
                #      then reading $Matches[1] silently read a STALE match - every phase
                #      then reported "no DECISIONS.md entry" against a file full of them.
                # So: match positively, capture immediately, compare afterwards.
                if ($l -match '^##\s+(.*)$') {
                    $head = $Matches[1].Trim()
                    if ($head -match ('\b' + $id + '\b')) { $headings += $head }
                }
            }
        }
        # A findings note "for" a phase is one whose NAME or BODY names it.
        $notes = @($noteFiles | Where-Object {
            $_.BaseName -match ('(?i)\b' + $id + '\b') -or
            ((Read-TextFile -Path $_.FullName) -match ('\b' + $id + '\b'))
        })
        $c.coverage.evaluated++
        $missing = @()
        if ($headings.Count -lt 1) { $missing += "no DECISIONS.md entry" }
        if ($notes.Count -lt 1)    { $missing += "no findings note" }
        if ($missing.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("{0} ledger entry/entries and {1} findings note(s)" -f $headings.Count, $notes.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $missing.Count `
                    -Note ("{0} for {1}" -f ($missing -join " and "), $id)
        }
        $c.probes += (New-Probe -Name ("audit-trail-{0}" -f $id) `
                      -Command ("grep '^## .*{0}' {1} ; grep -l '{0}' {2}/*.md" -f $id, $Ctx.decisions, $Ctx.notes) -Run $body)
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
        $hay = ""
        $hay += ([string](Read-TextFile -Path $Ctx.decisions))
        if (Test-Path -LiteralPath $Ctx.notes) {
            foreach ($f in (Get-ChildItem -LiteralPath $Ctx.notes -Filter "*.md" -File -ErrorAction SilentlyContinue)) {
                $hay += ([string](Read-TextFile -Path $f.FullName))
            }
        }
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
    Write-Host "  Clause 3's doors are checked back against C.8's own words by door-set-matches-plan." -ForegroundColor DarkGray
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
        $c.probes += (New-Probe -Name ("clause-{0}-threw" -f $k) -Command $fn `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                  -Note ("the clause evaluator threw, so nothing was decided: {0}" -f $msg)))
        $results += (Resolve-ClauseVerdict -Clause $c)
    }
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
           ([int]$census[$script:DfuClearBucket] -eq @($script:DfuClauses.Keys).Count))

$board   = "done"
$reasons = @()
if (-not $isDone) {
    if ((-not $censusBalances) -or (@($unaccounted).Count -gt 0)) {
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
