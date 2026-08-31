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

# The ONE reason a work branch may be excluded from clause 4, stated here rather than
# inline so it appears in -ListClauses and in the JSON, where a reader can object to it.
$script:DfuExcludedBranches = [ordered]@{
    "work/pod-key" = "unrelated podcast effort, not part of dark-factory unification (operator, 2026-08-31)"
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
    $pendingManual = @($Clause.manual | Where-Object { $_.state -ne "recorded" })

    if ($fails.Count -gt 0) {
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
    if (-not $Store.PSObject.Properties.Name -contains $Name) { return $null }
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
    param($Ctx, [string]$Url)
    if ($Ctx.skiplive) { return @{ exit = $null; out = ""; ran = $false; why = "-SkipLive" } }
    $r = Invoke-Native -Exe "docker" -Arguments @("run", "--rm", "--network", $Ctx.obnet, "curlimages/curl:latest",
                                                  "-s", "-w", "\nHTTP:%{http_code}", $Url)
    return @{ exit = $r.exit; out = ($r.stdout + $r.stderr); ran = $r.ran; command = $r.command }
}

# =================================================================================
# THE CLAUSES. One function each, each RUNNING something.
# =================================================================================

function New-CleanCheckout {
    # Clause 1 says "from a CLEAN CHECKOUT of the work line - not from a developer's
    # worktree, not from cached output". So the script makes one itself. A detached
    # worktree of the work line is the cheapest honest form: it has the work line's tree
    # and none of this session's edits.
    param($Ctx)
    $path = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-done-clean-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    $g = Invoke-Git -Arguments @("worktree", "add", "--detach", $path, $Ctx.workline) -WorkDir $Ctx.root
    $sub = $null
    if ($g.exit -eq 0) {
        # A CLEAN CHECKOUT OF THE WORK LINE INCLUDES THE SUBMODULE IT PINS. `git worktree
        # add` does not populate submodules, so without this OB1/ is an empty directory and
        # any check that reads it fails with ENOENT - a FALSE RED that says nothing about
        # the phase it is attributed to. The init's own exit code is recorded, so a
        # checkout that could not be completed is visible rather than silently degraded.
        $sub = Invoke-Git -Arguments @("submodule", "update", "--init", "--recursive") -WorkDir $path
    }
    return @{
        path = $path; exit = $g.exit; command = $g.command
        err  = ($g.stderr + $g.stdout)
        submodule_exit = $(if ($sub) { $sub.exit } else { $null })
        submodule_err  = $(if ($sub) { (($sub.stderr + $sub.stdout) -replace "\s+", " ").Trim() } else { "" })
    }
}

function Remove-CleanCheckout {
    param($Ctx, [string]$Path)
    if (-not $Path) { return }
    [void](Invoke-Git -Arguments @("worktree", "remove", "--force", $Path) -WorkDir $Ctx.root)
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue }
    [void](Invoke-Git -Arguments @("worktree", "prune") -WorkDir $Ctx.root)
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
            # One command per phase decides it; the first is the one the walkthrough leads with.
            $cmd = $cmds[0]
            $r = Invoke-Native -Exe "cmd.exe" -Arguments @("/c", $cmd) -WorkDir $cleanPath
            if (-not $r.ran -or $null -eq $r.exit) {
                $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
                $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                        -Note ("the command could not be started in the clean checkout: {0}" -f (($r.stderr -replace "\s+", " ").Trim()))
            } elseif ([int]$r.exit -eq 0) {
                $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                $body = New-VerdictProbeBody -Verdict "pass" -Exit ([int]$r.exit) -Note "re-ran GREEN in the clean checkout"
            } else {
                $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                $tail = @(($r.stdout + "`n" + $r.stderr) -split "`n" | Where-Object { $_.Trim() } | Select-Object -Last 1)
                $body = New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) `
                        -Note ("exited {0} in the clean checkout: {1}" -f $r.exit, (($tail -join " ") -replace "\s+", " ").Trim())
            }
            $c.probes += (New-Probe -Name ("{0}-validated-by" -f $id) -Command $cmd -Run $body)
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
        $curNorm  = ConvertTo-Normalised -s $current
        $dropped  = @()
        foreach ($req in (Split-Requirements -cell $original)) {
            if (-not $curNorm.Contains($req)) { $dropped += $req }
        }
        $cmdLabel = ("compare ORIGINAL({0} {1}) vs CURRENT({2} {3}) for {4}" -f `
                     $chain[0].date, $chain[0].sha.Substring(0, 7), $chain[-1].date, $chain[-1].sha.Substring(0, 7), $id)
        if ($dropped.Count -eq 0) {
            # ADDITIONS NEVER FAIL THIS CLAUSE - only requirements that LEFT do.
            $c.probes += (New-Probe -Name ("chain-{0}-original-vs-current" -f $id) -Command $cmdLabel `
                -Run (New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                      -Note ("all {0} requirement(s) in the ORIGINAL column survive in the CURRENT column; additions do not fail this clause" -f `
                             @(Split-Requirements -cell $original).Count)))
            continue
        }
        # A dropped requirement must be dispositioned: (a) incoherent-as-written WITH the
        # evidence, or (b) carried forward as a named follow-on WITH an owner and a
        # findings sink. NEITHER = FAIL.
        $undispositioned = @()
        foreach ($d in $dropped) {
            $key = "{0}::{1}" -f $id, $d
            $rec = $null
            if ($dispositions -and ($dispositions.PSObject.Properties.Name -contains $key)) { $rec = $dispositions.$key }
            $ok = $false
            if ($rec) {
                $kind = [string]$rec.disposition
                if ($kind -eq "incoherent" -and -not [string]::IsNullOrWhiteSpace([string]$rec.evidence)) { $ok = $true }
                elseif ($kind -eq "follow-on" -and
                        -not [string]::IsNullOrWhiteSpace([string]$rec.owner) -and
                        -not [string]::IsNullOrWhiteSpace([string]$rec.findings_sink)) { $ok = $true }
            }
            if (-not $ok) { $undispositioned += $d }
        }
        if ($undispositioned.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                    -Note ("{0} requirement(s) changed or dropped between ORIGINAL and CURRENT; each carries a valid disposition" -f $dropped.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $undispositioned.Count `
                    -Note ("{0} requirement(s) present in the ORIGINAL column and absent from the CURRENT one with no valid disposition: {1}" -f `
                           $undispositioned.Count, (($undispositioned | Select-Object -First 3) -join " | "))
        }
        $c.probes += (New-Probe -Name ("chain-{0}-original-vs-current" -f $id) -Command $cmdLabel -Run $body)
    }
    return (Resolve-ClauseVerdict -Clause $c)
}

# ---------------------------------------------------------------------------------
# CLAUSE 3's DOOR SET IS PINNED IN CODE, for the same reason andon.ps1 pins its
# required conditions: a door list that lived in a config could be thinned to the ones
# that pass. Section C.8 names these doors, so they are the FLOOR - a run that cannot
# reach one reports it as unevaluated and the clause refuses. Doors discovered in the
# tree beyond this set are additional, never substitutes.
# ---------------------------------------------------------------------------------
$script:DfuRequiredDoors = [ordered]@{
    "postgrest-thoughts"          = "PostgREST on the thoughts corpus"
    "postgrest-agent-memories"    = "PostgREST on agent_memories"
    "postgrest-thought-entities"  = "the thought_entities join, which can project thought content"
    "postgrest-derived-queue"     = "entity_extraction_queue - DERIVED data about a protected row"
    "wiki-compiler-output"        = "the wiki compiler's published output (wiki_pages)"
    "openbrain-mcp-door"          = "the raw openbrain-mcp door - the agent plane's own connection"
    "cloud-search-thoughts"       = "cloud search_thoughts, via the gateway that fronts it"
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

function Test-Clause3 {
    # C.8.3 - LIFT BY VALIDATION, NEVER BY EMPTINESS. "0 personal rows" is an absence of
    # data, not containment: the property it claims dies the instant a personal row
    # exists. So a synthetic personal row is WRITTEN, every door is attacked, and none may
    # return it. The fixture is removed afterwards and its removal is itself verified.
    param($Ctx, $Store)
    $c = New-ClauseResult -Id 3
    $doors = @($script:DfuRequiredDoors.Keys)
    $c.coverage.subject  = "doors named by C.8 clause 3, plus the corpus predicate"
    $c.coverage.expected = $doors.Count + 1

    if ($Ctx.skiplive) {
        foreach ($d in $doors) {
            $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: -SkipLive)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "-SkipLive was passed, so this door was NOT attacked - unevaluated, never assumed closed"))
        }
        $c.coverage.not_evaluated = @($doors)
        $c.probes += (New-Probe -Name "corpus-predicate-fail-closed" -Command "(not run: -SkipLive)" `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive was passed"))
        return (Resolve-ClauseVerdict -Clause $c)
    }

    # --- the corpus predicate must be FAIL-CLOSED --------------------------------
    # An unlabelled row must NOT be on the ops plane. This is measured by BEHAVIOUR - a
    # row is inserted inside a transaction and rolled back - not by reading the function
    # body, because a predicate can be correct in text and shadowed in effect.
    $sqlPred = "BEGIN; INSERT INTO thoughts (content, metadata) VALUES ('DFU-DONE-UNLABELLED-CANARY','{}'::jsonb); SET ROLE service_role; SELECT count(*) FROM thoughts WHERE content='DFU-DONE-UNLABELLED-CANARY'; RESET ROLE; ROLLBACK;"
    $rp = Invoke-Psql -Ctx $Ctx -Sql $sqlPred
    if (-not $rp.ran -or $null -eq $rp.exit) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "the database could not be reached, so the predicate was not tested"
    } elseif ([int]$rp.exit -ne 0) {
        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note ("psql exited non-zero: {0}" -f (($rp.out -replace "\s+", " ").Trim()))
    } else {
        $seen = @(($rp.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' })
        if ($seen.Count -lt 1) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit ([int]$rp.exit) -Note "the query returned no count at all - nothing was decided"
        } elseif ([int]$seen[0] -eq 0) {
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "an UNLABELLED row is invisible to the agent plane - the predicate is fail-closed"
        } else {
            $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 `
                    -Note ("an UNLABELLED row is VISIBLE to the agent plane ({0} row(s)) - unlabelled defaults to fine, which is the class this clause names" -f $seen[0])
        }
    }
    $c.probes += (New-Probe -Name "corpus-predicate-fail-closed" -Command ("docker exec {0} psql -tAc <insert-unlabelled-in-tx; SET ROLE service_role; count; ROLLBACK>" -f $Ctx.db) -Run $body)

    # --- write the synthetic personal fixture ------------------------------------
    $marker = "DFU-DONE-PERSONAL-FIXTURE-" + [guid]::NewGuid().ToString("N").Substring(0, 10)
    # NO DOUBLE QUOTES IN THE SQL, deliberately. PowerShell 5.1 does not escape embedded
    # double quotes when it hands an argument to a native executable, so a JSON literal
    # like '{"exposure":"personal"}' arrives at psql as '{exposure:personal}' and is
    # rejected as invalid JSON. The insert then failed, every door reported "the fixture
    # could not be written", and the clause looked merely unevaluated rather than broken.
    # jsonb_build_object expresses the same object using single quotes only, so nothing
    # depends on a quoting layer that silently rewrites it.
    $ins = "INSERT INTO thoughts (content, metadata) VALUES ('$marker personal fixture', " +
           "jsonb_build_object('exposure','personal','dfu_done_fixture',true)) RETURNING id;"
    $ri = Invoke-Psql -Ctx $Ctx -Sql $ins
    $fixtureId = $null
    if ($ri.ran -and $ri.exit -eq 0) {
        $m = [regex]::Match(($ri.out -replace "\s+", " "), '(\d+)')
        if ($m.Success) { $fixtureId = $m.Groups[1].Value }
    }
    $c.detail += ("personal fixture: marker={0} thought_id={1}" -f $marker, $fixtureId)

    try {
        if (-not $fixtureId) {
            foreach ($d in $doors) {
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(not run: the fixture could not be written)" `
                    -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                          -Note "no personal row could be written, so no door was actually attacked - this clause cannot be met by an empty plane"))
            }
            $c.coverage.not_evaluated = @($doors)
            return (Resolve-ClauseVerdict -Clause $c)
        }

        # Each door is attacked with the marker. A door PASSES only when it demonstrably
        # does NOT return the fixture; a door that errors is indeterminate, never "closed".
        $urls = [ordered]@{
            "postgrest-thoughts"         = ("http://{0}/thoughts?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $marker)
            "postgrest-agent-memories"   = ("http://{0}/agent_memories?content=like.*{1}*&select=id,content" -f $Ctx.postgrest, $marker)
            "postgrest-thought-entities" = ("http://{0}/thought_entities?select=thoughts(content)&limit=200" -f $Ctx.postgrest)
            "postgrest-derived-queue"    = ("http://{0}/entity_extraction_queue?thought_id=eq.{1}&select=thought_id,source_fingerprint" -f $Ctx.postgrest, $fixtureId)
            "wiki-compiler-output"       = ("http://{0}/wiki_pages?or=(title.like.*{1}*,body.like.*{1}*)&select=slug" -f $Ctx.postgrest, $marker)
        }
        # The fingerprint of the hidden content. A HASH IS A DISCLOSURE: a door that
        # returns a digest of a protected row has leaked it, and comparing the marker text
        # alone would call that door closed.
        $fp = ""
        $rf = Invoke-Psql -Ctx $Ctx -Sql ("SELECT encode(digest(content,'sha256'),'hex') FROM thoughts WHERE id={0};" -f $fixtureId)
        if ($rf.ran -and $rf.exit -eq 0) {
            $mm = [regex]::Match($rf.out, '([0-9a-f]{64})')
            if ($mm.Success) { $fp = $mm.Groups[1].Value }
        }

        foreach ($d in $doors) {
            if ($urls.Contains($d)) {
                $u = [string]$urls[$d]
                $rc = Invoke-Curl -Ctx $Ctx -Url $u
                if (-not $rc.ran -or $null -eq $rc.exit -or [int]$rc.exit -ne 0) {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rc.exit `
                            -Note "the door could not be reached, so it was NOT proven closed"
                } else {
                    $leakMarker = ($rc.out -match [regex]::Escape($marker))
                    $leakFp     = ($fp -and ($rc.out -match [regex]::Escape($fp)))
                    $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                    if ($leakMarker) {
                        $body = New-VerdictProbeBody -Verdict "fail" -Exit 0 -Note "the door RETURNED the personal fixture's content"
                    } elseif ($leakFp) {
                        $body = New-VerdictProbeBody -Verdict "fail" -Exit 0 `
                                -Note "the door returned the SHA-256 of the personal fixture - derived data escaping the boundary; a hash is a disclosure"
                    } else {
                        $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "attacked with the fixture and it did not come back"
                    }
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command ("curl (from {0}) '{1}'" -f $Ctx.obnet, $u) -Run $body)
                continue
            }

            if ($d -eq "openbrain-mcp-door") {
                # The agent plane's OWN door. What decides it is not a tool response but
                # whether the connection it uses is BOUND: row-level security does not
                # bind a superuser, FORCE or not. So the door is judged by the role it
                # connects as - a fact, checked in two commands.
                $ru = Invoke-Native -Exe "docker" -Arguments @("exec", "openbrain-mcp", "printenv", "DB_USER")
                $role = ""
                if ($ru.ran -and $ru.exit -eq 0) { $role = $ru.stdout.Trim() }
                if (-not $role) {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $ru.exit `
                            -Note "could not read the door's DB_USER, so its binding is unknown - unknown is not closed"
                } else {
                    $rr = Invoke-Psql -Ctx $Ctx -Sql ("SELECT rolsuper::text||'/'||rolbypassrls::text FROM pg_roles WHERE rolname='{0}';" -f $role)
                    $flags = ""
                    if ($rr.ran -and $rr.exit -eq 0) { $flags = (($rr.out -split "`n") | Where-Object { $_ -match 't|f' } | Select-Object -First 1).Trim() }
                    $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                    if ($flags -match '^t' -or $flags -match '/t$') {
                        $body = New-VerdictProbeBody -Verdict "fail" -Exit 0 `
                                -Note ("openbrain-mcp connects as '{0}', which is rolsuper/rolbypassrls = {1} - RLS does not bind it, so the boundary is void at the agent plane's own door" -f $role, $flags)
                    } else {
                        $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 `
                                -Note ("openbrain-mcp connects as '{0}' (rolsuper/rolbypassrls = {1}) - bound by the boundary" -f $role, $flags)
                    }
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "docker exec openbrain-mcp printenv DB_USER ; psql -tAc 'SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=...'" -Run $body)
                continue
            }

            if ($d -eq "cloud-search-thoughts") {
                # Reached through the gateway that fronts it. If that container is not
                # running, the door is UNEVALUATED - it is not absent, it is unchecked.
                $rg = Invoke-Native -Exe "docker" -Arguments @("ps", "--filter", "name=openbrain-gateway", "--format", "{{.Names}}")
                $names = ""
                if ($rg.ran -and $rg.exit -eq 0) { $names = $rg.stdout }
                if ($names -notmatch 'openbrain-gateway') {
                    $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                    $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rg.exit `
                            -Note "the cloud gateway is not running, so cloud search_thoughts was NOT attacked"
                } else {
                    $rs = Invoke-Psql -Ctx $Ctx -Sql ("SET ROLE service_role; SELECT count(*) FROM thoughts WHERE content LIKE '%{0}%';" -f $marker)
                    $cnt = @(($rs.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+$' })
                    if (-not $rs.ran -or $rs.exit -ne 0 -or $cnt.Count -lt 1) {
                        $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
                        $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rs.exit -Note "the search lane could not be exercised"
                    } else {
                        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
                        if ([int]$cnt[0] -eq 0) {
                            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "the search lane's role cannot see the fixture"
                        } else {
                            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("the search lane's role sees the fixture ({0} row(s))" -f $cnt[0])
                        }
                    }
                }
                $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "docker ps --filter name=openbrain-gateway ; psql 'SET ROLE service_role; SELECT count(*) FROM thoughts WHERE content LIKE fixture'" -Run $body)
                continue
            }

            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($d)
            $c.probes += (New-Probe -Name ("door-{0}" -f $d) -Command "(no probe implemented)" `
                -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null `
                      -Note "this door is declared in code but has no probe - declared and unimplemented is UNEVALUATED, never closed"))
        }
    } finally {
        # CLEAN UP. Production must show 0 personal rows when this finishes, and the
        # cleanup is VERIFIED rather than assumed - a fixture left behind would be this
        # script creating the exposure it exists to detect.
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM entity_extraction_queue WHERE thought_id IN (SELECT id FROM thoughts WHERE metadata->>'dfu_done_fixture'='true');")
        [void](Invoke-Psql -Ctx $Ctx -Sql "DELETE FROM thoughts WHERE metadata->>'dfu_done_fixture'='true';")
        $rv = Invoke-Psql -Ctx $Ctx -Sql "SELECT (SELECT count(*) FROM thoughts WHERE metadata->>'dfu_done_fixture'='true')::text||'/'||(SELECT count(*) FROM thoughts WHERE metadata->>'exposure'='personal')::text;"
        $line = (($rv.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^\d+/\d+$' } | Select-Object -First 1)
        if (-not $line) {
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $rv.exit -Note "could not confirm the fixture was removed"
        } elseif ($line -eq "0/0") {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "fixture removed; production shows 0 personal rows"
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("fixture/personal rows remaining: {0} - the plane was left dirty" -f $line)
        }
        $c.probes += (New-Probe -Name "fixture-cleaned-up" -Command "psql DELETE fixture rows; then count fixture/personal rows" -Run $body)
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

    # --- unmerged work/* branches, EXCLUDING the one named exclusion --------------
    $branches = Get-WorkBranches -Ctx $Ctx
    if ($null -eq $branches) {
        $c.coverage.not_evaluated += "work-branches"
        $c.probes += (New-Probe -Name "no-unmerged-work-branches" -Command "git for-each-ref refs/heads/work/" `
            -Run (New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "could not enumerate work branches"))
    } else {
        $unmerged = @()
        $skipped  = @()
        foreach ($b in $branches) {
            if ($script:DfuExcludedBranches.Contains($b)) { $skipped += ("{0} ({1})" -f $b, $script:DfuExcludedBranches[$b]); continue }
            if ($b -eq ("work/" + $Ctx.workline)) { continue }
            $g = Invoke-Git -Arguments @("rev-list", "--count", ("{0}..{1}" -f $Ctx.workline, $b)) -WorkDir $Ctx.root
            if ($g.exit -ne 0) { $unmerged += ("{0} (ahead=UNKNOWN, rev-list exit {1})" -f $b, $g.exit); continue }
            $ahead = 0
            if ($g.stdout.Trim() -match '^\d+$') { $ahead = [int]$g.stdout.Trim() }
            if ($ahead -gt 0) { $unmerged += ("{0} (ahead {1})" -f $b, $ahead) }
        }
        foreach ($s in $skipped) { $c.detail += ("EXCLUDED from clause 4 by name: {0}" -f $s) }
        $c.coverage.evaluated = [int]$c.coverage.evaluated + 1
        if ($unmerged.Count -eq 0) {
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("no unmerged work/* branch (excluding {0} named exclusion(s))" -f $script:DfuExcludedBranches.Count)
        } else {
            $body = New-VerdictProbeBody -Verdict "fail" -Exit $unmerged.Count `
                    -Note ("{0} unmerged work/* branch(es): {1}" -f $unmerged.Count, ($unmerged -join ", "))
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
                # A commit can be reachable without being a tip; ask the remote directly.
                $fe = Invoke-Git -Arguments @("fetch", "--dry-run", "origin", $pin) -WorkDir $ob1
                if ($fe.exit -eq 0) {
                    $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note ("the pinned OB1 commit {0} is reachable on the remote" -f $pin.Substring(0, 7))
                } else {
                    $body = New-VerdictProbeBody -Verdict "fail" -Exit $fe.exit `
                            -Note ("the pinned OB1 commit {0} is NOT reachable on the OB1 remote - a fresh --recurse-submodules clone would break" -f $pin.Substring(0, 7))
                }
            }
        }
    }
    $c.probes += (New-Probe -Name "gitlink-reachable-on-remote" -Command ("git ls-tree {0} OB1 ; git -C OB1 ls-remote origin" -f $Ctx.workline) -Run $body)

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
                $cmd = ("psql: relforcerowsecurity on thoughts ; git ls-tree {0} OB1 -> submodule cat-file" -f $Ctx.workline)
                if ($Ctx.skiplive) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "-SkipLive" }
                else {
                    # boolean::text is 'true'/'false', NOT 't'/'f' - psql's display form and
                    # its cast form differ, and matching the display form against a cast
                    # produced a permanent 'could not read' that looked like an outage.
                    $r = Invoke-Psql -Ctx $Ctx -Sql "SELECT (CASE WHEN relrowsecurity THEN 't' ELSE 'f' END)||'/'||(CASE WHEN relforcerowsecurity THEN 't' ELSE 'f' END) FROM pg_class WHERE relname='thoughts';"
                    $flags = (($r.out -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[tf]/[tf]$' } | Select-Object -First 1)
                    $srcOk = $false
                    $srcWhy = ""
                    if ($pin) {
                        $cf = Invoke-Git -Arguments @("cat-file", "-e", ("{0}:docker/init-agent-memory-rls.sql" -f $pin)) -WorkDir $ob1
                        $srcOk = ($cf.exit -eq 0)
                        if (-not $srcOk) { $srcWhy = "its defining SQL is NOT in the OB1 tree the work line pins" }
                    } else { $srcWhy = "the OB1 gitlink could not be read" }
                    if (-not $flags) { $c.coverage.not_evaluated += $svc; $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $r.exit -Note "could not read RLS flags for thoughts" }
                    else {
                        $c.coverage.evaluated++
                        if ($flags -eq "t/t" -and $srcOk) { $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "RLS enabled and FORCED on thoughts, and its source is on the work line" }
                        elseif ($flags -ne "t/t") { $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("thoughts relrowsecurity/relforcerowsecurity = {0}, expected t/t" -f $flags) }
                        else { $body = New-VerdictProbeBody -Verdict "fail" -Exit 1 -Note ("the boundary is LIVE but {0} - deployed from code that has not landed" -f $srcWhy) }
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
        $cmd = $cmds[0]
        $r = Invoke-Native -Exe "cmd.exe" -Arguments @("/c", $cmd) -WorkDir $Ctx.root
        if (-not $r.ran -or $null -eq $r.exit) {
            $c.coverage.not_evaluated = @($c.coverage.not_evaluated) + @($id)
            $body = New-VerdictProbeBody -Verdict "indeterminate" -Exit $null -Note "the named check could not be started"
        } elseif ([int]$r.exit -eq 0) {
            $c.coverage.evaluated++
            $body = New-VerdictProbeBody -Verdict "pass" -Exit 0 -Note "the row's named check re-runs green"
        } else {
            $c.coverage.evaluated++
            $body = New-VerdictProbeBody -Verdict "fail" -Exit ([int]$r.exit) -Note ("the row's named check exited {0}" -f $r.exit)
        }
        $c.probes += (New-Probe -Name ("walkthrough-{0}-check" -f $id) -Command $cmd -Run $body)
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
    Write-Host "  Branches excluded from clause 4, with the reason:" -ForegroundColor DarkGray
    foreach ($b in $script:DfuExcludedBranches.Keys) { Write-Host ("     {0} - {1}" -f $b, $script:DfuExcludedBranches[$b]) -ForegroundColor DarkGray }
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
