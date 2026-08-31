# verify-dfu-done.ps1 - THE DRILL. Prove that dfu-done.ps1 CAN FAIL, clause by clause.
#
# WHY THIS EXISTS, and why it ships beside the thing it tests. A "done" script that cannot
# fail is the perfect form of the defect this whole effort spent eleven findings on: a
# check that is green while checking nothing. dfu-done.ps1 is the authority on whether a
# plan is complete, so "it printed DONE" is worth exactly as much as the demonstration
# that it would have printed something else had the world been different. That
# demonstration is this file.
#
# THE CONTRACT, and it is stricter than "some tests pass":
#
#   FOR EVERY CLAUSE, this drill CONSTRUCTS a condition under which that clause MUST NOT
#   be met, runs the REAL dfu-done.ps1 against it, and asserts the clause did not pass.
#   A CLAUSE WITH NO FAILING CASE HERE IS A CLAUSE THAT HAS NOT BEEN IMPLEMENTED, and the
#   drill FAILS on that alone - it does not quietly cover seven of eight.
#
# That last rule is itself a census (step Z): the set of clauses this drill constructed a
# failure for must EQUAL the set of clauses dfu-done.ps1 declares. Adding a ninth clause
# to the authority without adding a case here turns this drill red, which is the only way
# a completeness claim stays true after the person who wrote it has gone.
#
# WHAT A STEP ASSERTS. Not merely "the run exited non-zero" - that is satisfiable by a
# typo in a path. Each step asserts on the JSON verdict:
#   - the target clause's verdict is NOT "met", and
#   - where the step constructs a SPECIFIC defect, the SPECIFIC probe that should have
#     caught it reports the expected word.
# Asserting only the headline would let a clause fail for the wrong reason and still look
# tested, which is the "counterfactual measuring the wrong thing" class from our own list.
#
# THE FIXTURES ARE REAL, NOT MOCKS. Steps build throwaway git repositories with real
# commits, real branches and real worktrees, and point dfu-done.ps1's context at them. No
# function is stubbed and no verdict is injected: the drill can only choose the WORLD, and
# the script decides the answer. If there were a switch that forced a clause green, this
# drill could not detect it - so there is no such switch (see dfu-done.ps1's header).
#
#   .\verify-dfu-done.ps1            # run every step; exit 0 only if all assertions hold
#   .\verify-dfu-done.ps1 -Verbose   # print each assertion as it is made
#   .\verify-dfu-done.ps1 -Live      # ALSO assert the live-plane failures (needs Docker)
#
# Exit codes: 0 every step passed and every clause has a failing case
#             1 an assertion failed, or a clause has no failing case in this drill
#
[CmdletBinding()]
param(
    [switch]$Live,
    [string]$Target
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if (-not $Target) { $Target = Join-Path $PSScriptRoot "dfu-done.ps1" }
if (-not (Test-Path -LiteralPath $Target)) {
    Write-Host ("FATAL: cannot find the script under test: {0}" -f $Target) -ForegroundColor Red
    exit 1
}

$script:Checks   = 0
$script:Failures = @()
# WHICH CLAUSES THIS DRILL CONSTRUCTED A FAILURE FOR. Recorded as steps run, then compared
# against the authority's own clause set in step Z. Never hand-maintained.
$script:ClausesCovered = @{}

function Assert-That {
    param([string]$What, [bool]$Condition, [string]$Detail = "")
    $script:Checks++
    if ($Condition) {
        Write-Verbose ("  ok   {0}" -f $What)
        return $true
    }
    $script:Failures += ("{0}{1}" -f $What, $(if ($Detail) { " -- $Detail" } else { "" }))
    Write-Host ("  FAIL {0}" -f $What) -ForegroundColor Red
    if ($Detail) { Write-Host ("       {0}" -f $Detail) -ForegroundColor DarkGray }
    return $false
}

function New-Scratch {
    $p = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-drill-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $p -Force | Out-Null
    return $p
}

function Remove-Scratch {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    # Worktrees registered inside a fixture repo must be released or the directory is
    # locked; the fixture is disposable, so force is correct here and only here.
    Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue |
        ForEach-Object { $_.Attributes = "Normal" }
    Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
}

function Invoke-InDir {
    param([string]$Dir, [string]$Exe, [string[]]$Arguments)
    $prev = (Get-Location).Path
    Set-Location -LiteralPath $Dir
    try {
        $global:LASTEXITCODE = 0
        $out = & $Exe @Arguments 2>&1
        return @{ exit = [int]$global:LASTEXITCODE; out = (@($out) -join "`n") }
    } finally { Set-Location -LiteralPath $prev }
}

function New-FixtureRepo {
    # A real git repository with the three documents dfu-done.ps1 reads, so the chain in
    # clause 2 has genuine history to reconstruct rather than a mocked list.
    param([string]$Plan, [string]$Decisions, [string]$Walkthrough)
    $root = New-Scratch
    $dfu  = Join-Path $root "documentation\implementation-guide\dark-factory-unification"
    New-Item -ItemType Directory -Path $dfu -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root "documentation\notes") -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $root "scripts\agent-harness") -Force | Out-Null
    [System.IO.File]::WriteAllText((Join-Path $dfu "PLAN.md"), $Plan, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText((Join-Path $dfu "DECISIONS.md"), $Decisions, [System.Text.UTF8Encoding]::new($false))
    [System.IO.File]::WriteAllText((Join-Path $dfu "WALKTHROUGH.md"), $Walkthrough, [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $root -Exe "git" -Arguments @("init", "-q", "-b", "fixture-line"))
    [void](Invoke-InDir -Dir $root -Exe "git" -Arguments @("config", "user.email", "drill@example.invalid"))
    [void](Invoke-InDir -Dir $root -Exe "git" -Arguments @("config", "user.name", "dfu drill"))
    [void](Invoke-InDir -Dir $root -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $root -Exe "git" -Arguments @("commit", "-q", "-m", "fixture: the original plan"))
    return @{ root = $root; dfu = $dfu; line = "fixture-line" }
}

function New-PlanText {
    # A STRUCTURALLY REAL fixture plan. Section 2's table carries the WHOLE U0-U6 floor -
    # the same population dfu-done.ps1 pins - and section C.8's clauses 1, 2 and 4 are
    # present so the pinned floors have the plan's own words to be checked back against.
    #
    # IT USED TO HOLD ONE PHASE, and that is why the floor defect survived here: a fixture
    # with a single row cannot express "a phase deleted itself out of the population",
    # because there is no population to shrink. The parameters below exist to construct
    # exactly the three shapes that defect takes - a row DELETED, a row that merely loses
    # its BOLD while staying visible, and a row RENAMED out of the floor.
    param(
        [string]$U0Validated = "a check that exists",
        [string]$Amendments = "",
        [string[]]$OmitPhases = @(),
        [string[]]$UnboldPhases = @(),
        [hashtable]$RenamePhases = @{},
        [hashtable]$SuffixPhases = @{},
        [string]$ServiceList = "the ops gateway, the andon board, the gate profiles, the RLS boundary at every stage including the direct clients"
    )
    $rows = @()
    foreach ($n in 0..6) {
        $id = "U$n"
        if ($OmitPhases -contains $id) { continue }
        $shown = $id
        if ($RenamePhases.ContainsKey($id)) { $shown = [string]$RenamePhases[$id] }
        $cell = "**$shown**"
        if ($SuffixPhases.ContainsKey($id)) { $cell = "**$shown " + [string]$SuffixPhases[$id] + "**" }
        if ($UnboldPhases -contains $id) { $cell = $shown }
        $vb = $(if ($n -eq 0) { $U0Validated } else { "a check that exists" })
        $rows += ("| {0} | do the thing | {1} | - |" -f $cell, $vb)
    }
    $body = ($rows -join "`n")
    $t = @"
# fixture plan

### C.8 The success condition

1. **Every U-phase column is satisfied by a check that RAN.** For U0-U6, the section 2
   Validated by check re-runs green from a clean checkout of the work line.
2. **No phase is parked, and every amendment is ACCOUNTED FOR.**
4. **Nothing is left in flight, and everything is DEPLOYED AND RUNNING**. Every service
   this plan adds is **running live from the work line's code** - $ServiceList.
5. **The walkthrough is true.**

## 2. Phases

| Phase | What | Validated by | Depends on |
|---|---|---|---|
$body

### 2.1 Amendments to the phase table
$Amendments
"@
    return $t
}

function New-ChainFixture {
    # A repository whose U0 column was AMENDED once: $Original in the first commit,
    # $Current in the second. That is the shape clause 2 judges - and the shape in which an
    # erosion stays defensible at every individual step.
    param([string]$Original, [string]$Current, [string]$Decisions = "")
    if (-not $Decisions) { $Decisions = "# fixture decisions`n`n## 2026-08-30 - U0 - the column was amended`nwhy`n" }
    $fx = New-FixtureRepo -Plan (New-PlanText -U0Validated $Original) -Decisions $Decisions -Walkthrough "# w`n"
    [System.IO.File]::WriteAllText((Join-Path $fx.dfu "PLAN.md"),
        (New-PlanText -U0Validated $Current), [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-qam", "amend U0's column"))
    return $fx
}

function Get-ChainProbe {
    # Run clause 2 against a chain fixture and hand back its ORIGINAL-vs-CURRENT probe.
    param($Fx, [string]$DispositionsPath = "")
    $p = @{
        Only = 2; RepoRoot = $Fx.root; WorkLine = $Fx.line; SkipLive = $true
        PlanPath = (Join-Path $Fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $Fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $Fx.dfu "WALKTHROUGH.md")
        Dispositions = $(if ($DispositionsPath) { $DispositionsPath } else { (Join-Path $Fx.root "no-such-dispositions.json") })
    }
    $r = Invoke-Target -Params $p
    $c = Get-Clause -Json $r.json -Id "2"
    return @{ run = $r; clause = $c; probe = (Get-Probe -Clause $c -Name "chain-U0-original-vs-current") }
}

function Invoke-Target {
    # Run the REAL script and return its parsed JSON verdict. Nothing is stubbed.
    param([hashtable]$Params)
    $argv = @("-NoProfile", "-File", $Target, "-Json")
    foreach ($k in $Params.Keys) {
        $v = $Params[$k]
        if ($v -is [bool] -or $v -is [switch]) { if ($v) { $argv += ("-" + $k) } }
        else { $argv += ("-" + $k); $argv += [string]$v }
    }
    $global:LASTEXITCODE = 0
    $raw = & powershell @argv 2>&1
    $code = [int]$global:LASTEXITCODE
    $text = (@($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] }) -join "`n")
    $json = $null
    try { $json = $text | ConvertFrom-Json } catch { }
    return @{ exit = $code; json = $json; text = $text }
}

function Get-Clause {
    param($Json, [string]$Id)
    if ($null -eq $Json) { return $null }
    foreach ($c in @($Json.clauses)) { if ([string]$c.id -eq $Id) { return $c } }
    return $null
}

function Get-Probe {
    param($Clause, [string]$Name)
    if ($null -eq $Clause) { return $null }
    foreach ($p in @($Clause.probes)) { if ([string]$p.name -eq $Name) { return $p } }
    return $null
}

function Assert-ClauseNotMet {
    # THE CORE ASSERTION. Records the clause as covered ONLY when the assertion is real -
    # a step that could not run its clause does not get to claim coverage of it.
    param([string]$Step, [string]$ClauseId, $Json, [string]$Because)
    $c = Get-Clause -Json $Json -Id $ClauseId
    if ($null -eq $c) {
        [void](Assert-That -What ("[$Step] clause $ClauseId is present in the verdict") -Condition $false -Detail "the run produced no clause $ClauseId at all")
        return $null
    }
    $ok = [bool](Assert-That -What ("[$Step] clause $ClauseId is NOT met when $Because") -Condition ([string]$c.verdict -ne "met") `
                 -Detail ("verdict was '{0}'" -f $c.verdict))
    if ($ok) { $script:ClausesCovered[$ClauseId] = $Step }
    return $c
}

Write-Host ""
Write-Host "=========================================================================" -ForegroundColor Cyan
Write-Host " VERIFY-DFU-DONE - can the done-authority actually fail?" -ForegroundColor Cyan
Write-Host ("   under test: {0}" -f $Target)
Write-Host "=========================================================================" -ForegroundColor Cyan

# =================================================================================
# STEP A - CLAUSE 1: a phase whose Validated-by check EXITS NON-ZERO in the clean
# checkout must not be satisfied. This is the clause's whole point: "code landed" is not
# satisfaction, and a red check is not a green one.
# =================================================================================
Write-Host ""
Write-Host "STEP A  clause 1 - a phase check that exits 1 from the clean checkout" -ForegroundColor Cyan
$fx = $null
try {
    $wt = @"
# fixture walkthrough

## U0 - do the thing
**How to run:** ``cmd /c exit 3``
"@
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# fixture decisions`n" -Walkthrough $wt
    $wtBefore = (Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("worktree", "list")).out
    $r = Invoke-Target -Params @{
        Only = 1; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "A" -ClauseId "1" -Json $r.json -Because "its named check exits 3 in a clean checkout"
    $p = Get-Probe -Clause $c -Name "U0-validated-by-1"
    [void](Assert-That -What "[A] the U0 probe reports 'fail', not merely 'indeterminate'" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail") `
        -Detail ("probe verdict: {0}" -f $(if ($p) { $p.verdict } else { "<missing>" })))
    [void](Assert-That -What "[A] the probe records the non-zero exit code it saw" `
        -Condition ($null -ne $p -and [string]$p.exit -eq "3") `
        -Detail ("probe exit: {0}" -f $(if ($p) { $p.exit } else { "<missing>" })))
    # THE SCRIPT MUST NOT MANUFACTURE THE DEFECT IT REPORTS. Clause 1 builds a clean
    # checkout; while that ran, `git worktree add` registered it and clause 4 counted the
    # scratch directory as unfinished work in flight - including another run's. It is a
    # CLONE now, so the work repo has no worktree to list, and this asserts that rather
    # than trusting the comment.
    $wtAfter = (Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("worktree", "list")).out
    [void](Assert-That -What "[A] clause 1's clean checkout registers NO worktree with the repo under test" `
        -Condition (($wtAfter -split "`n").Count -eq ($wtBefore -split "`n").Count) `
        -Detail ("before: {0} / after: {1}" -f ($wtBefore -replace "`n", " | "), ($wtAfter -replace "`n", " | ")))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP A2 - CLAUSE 1: the clause CLAIMS to re-run "the section 2 Validated by check". If
# section 2 names an artifact and the walkthrough runs a different one, that claim is
# false - and it used to be unexaminable, because the clause never opened section 2.
# =================================================================================
Write-Host ""
Write-Host "STEP A2 clause 1 - the walkthrough runs something section 2 does not name" -ForegroundColor Cyan
$fx = $null
try {
    $wt2 = "# fixture walkthrough`n`n## U0 - do the thing`n**How to run:** ``cmd /c exit 0```n"
    $fx = New-FixtureRepo -Plan (New-PlanText -U0Validated "verify-the-thing.ps1 runs green") `
                          -Decisions "# d`n" -Walkthrough $wt2
    $r = Invoke-Target -Params @{
        Only = 1; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "A2" -ClauseId "1" -Json $r.json -Because "the command run is not the check section 2 names"
    $p = Get-Probe -Clause $c -Name "U0-check-matches-section-2"
    [void](Assert-That -What "[A2] the section-2 correspondence probe is the one that failed" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "verify-the-thing.ps1") `
        -Detail ("verdict={0} note={1}" -f $(if ($p) { $p.verdict } else { "?" }), $(if ($p) { $p.note } else { "" })))
    [void](Assert-That -What "[A2] the phase's green command is still recorded as having passed" `
        -Condition ($null -ne (Get-Probe -Clause $c -Name "U0-validated-by-1")) `
        -Detail "the correspondence failure must not hide the run itself")
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP A3 - CLAUSE 1 and 5: a section naming TWO checks must run BOTH. Taking the first
# and reporting full coverage is a claim wider than its evidence in the one field that
# exists to prevent exactly that.
# =================================================================================
Write-Host ""
Write-Host "STEP A3 clause 5 - a row whose SECOND named check is red" -ForegroundColor Cyan
$fx = $null
try {
    $wt3 = "# fixture walkthrough`n`n## U0 - do the thing`n**How to run:** ``cmd /c exit 0```n`nand also`n`n**How to run:** ``cmd /c exit 9```n"
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough $wt3
    $r = Invoke-Target -Params @{
        Only = 5; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "A3" -ClauseId "5" -Json $r.json -Because "the row's SECOND named check exits 9"
    $p1 = Get-Probe -Clause $c -Name "walkthrough-U0-check-1"
    $p2 = Get-Probe -Clause $c -Name "walkthrough-U0-check-2"
    [void](Assert-That -What "[A3] BOTH named checks in the row ran" `
        -Condition ($null -ne $p1 -and $null -ne $p2) `
        -Detail ("probes: {0}" -f ((@($c.probes) | ForEach-Object { $_.name }) -join ",")))
    [void](Assert-That -What "[A3] the second one is reported red with its exit code" `
        -Condition ($null -ne $p2 -and [string]$p2.verdict -eq "fail" -and [string]$p2.exit -eq "9") `
        -Detail ("verdict={0} exit={1}" -f $(if ($p2) { $p2.verdict } else { "?" }), $(if ($p2) { $p2.exit } else { "?" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP B - CLAUSE 1: a phase with NO runnable check must NOT pass. A column satisfied by
# prose is the exact thing section 0 A6 records as falsified, and "nothing objected" must
# not read as "green".
# =================================================================================
Write-Host ""
Write-Host "STEP B  clause 1 - a phase whose column is prose only" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# fixture decisions`n" `
                          -Walkthrough "# fixture walkthrough`n`n## U0 - do the thing`nNo command here, only a paragraph.`n"
    $r = Invoke-Target -Params @{
        Only = 1; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "B" -ClauseId "1" -Json $r.json -Because "the phase names no executable check at all"
    [void](Assert-That -What "[B] coverage does not claim the phases it never ran - clear because we did NOT look" `
        -Condition ($null -ne $c -and [int]$c.coverage.evaluated -lt [int]$c.coverage.expected -and [int]$c.coverage.expected -ge 8) `
        -Detail ("coverage: {0} of {1}" -f $(if ($c) { $c.coverage.evaluated } else { "?" }), $(if ($c) { $c.coverage.expected } else { "?" })))
    [void](Assert-That -What "[B] the un-run phase is NAMED in not_evaluated" `
        -Condition ($null -ne $c -and (@($c.coverage.not_evaluated) -contains "U0")) `
        -Detail ("not_evaluated: {0}" -f $(if ($c) { (@($c.coverage.not_evaluated) -join ",") } else { "?" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP C - CLAUSE 2: an outstanding PARKED entry must fail the clause.
# =================================================================================
Write-Host ""
Write-Host "STEP C  clause 2 - an un-closed PARKED entry in the ledger" -ForegroundColor Cyan
$fx = $null
try {
    $dec = "# fixture decisions`n`n## 2026-08-30 - U0 - PARKED, the column cannot be met`nsome reason`n"
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions $dec -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 2; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "C" -ClauseId "2" -Json $r.json -Because "a PARKED entry is never closed by a later heading"
    $p = Get-Probe -Clause $c -Name "no-outstanding-parked"
    [void](Assert-That -What "[C] the parked probe is the one that failed" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail") `
        -Detail ("probe verdict: {0}" -f $(if ($p) { $p.verdict } else { "<missing>" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP D - CLAUSE 2, THE SUBTLE ONE, IN FIVE SHAPES. A requirement can leave a column by
# being deleted, by being retracted where it stands, by being hedged into optionality, or
# by being moved out of the cell into a footnote. Only the FIRST of those was ever caught:
# the test was `if (-not $curNorm.Contains($req))`, a raw substring test, so a column
# rewritten from "the gym run is observed" to "the gym run is observed is NO LONGER
# REQUIRED - dropped as unnecessary" still CONTAINED the words and the clause reported
# "all 2 requirement(s) in the ORIGINAL column survive". The one edit shape this clause
# exists to catch - an erosion that stays defensible at every step - was the one shape it
# could not see.
#
# The fifth shape is the one that must NOT fail: an ADDITION.
# =================================================================================
Write-Host ""
Write-Host "STEP D  clause 2 - deletion, retraction, hedge, footnote, and an addition" -ForegroundColor Cyan
$orig = "the drill runs green; the gym run is observed"

# D-1 DELETION - the shape that always worked. Kept so a fix cannot lose it.
$fx = $null
try {
    $fx = New-ChainFixture -Original $orig -Current "the drill runs green"
    $g = Get-ChainProbe -Fx $fx
    $c = Assert-ClauseNotMet -Step "D" -ClauseId "2" -Json $g.run.json -Because "a requirement was DELETED from the column with no disposition"
    [void](Assert-That -What "[D-1] a deleted requirement fails the ORIGINAL-vs-CURRENT probe" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "fail" -and [string]$g.probe.note -match "gym run is observed") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
    [void](Assert-That -What "[D-1] the chain was reconstructed and PRINTED with both states" `
        -Condition ($null -ne $c -and (@($c.detail) -join " ") -match "2 distinct state") `
        -Detail ("detail: {0}" -f $(if ($c) { (@($c.detail) -join " / ") } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# D-2 RETRACTION IN PLACE - THE ONE THAT USED TO PASS. The words are all still there.
$fx = $null
try {
    $fx = New-ChainFixture -Original $orig `
          -Current "the drill runs green; the gym run is observed is no longer required - dropped as unnecessary"
    $g = Get-ChainProbe -Fx $fx
    [void](Assert-ClauseNotMet -Step "D" -ClauseId "2" -Json $g.run.json -Because "a requirement was RETRACTED where it stood")
    [void](Assert-That -What "[D-2] a requirement retracted IN PLACE fails, though every one of its words survives" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "fail" -and [string]$g.probe.note -match "gym run is observed") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# D-3 HEDGED INTO OPTIONALITY - still there, no longer required.
$fx = $null
try {
    $fx = New-ChainFixture -Original $orig -Current "the drill runs green; the gym run is observed where feasible"
    $g = Get-ChainProbe -Fx $fx
    [void](Assert-ClauseNotMet -Step "D" -ClauseId "2" -Json $g.run.json -Because "a requirement was hedged with 'where feasible'"
    )
    [void](Assert-That -What "[D-3] a requirement WEAKENED by an added qualifier fails" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "fail" -and [string]$g.probe.note -match "gym run is observed") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# D-4 MOVED TO A FOOTNOTE - out of the cell, still in the document.
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -U0Validated $orig) `
                          -Decisions "# fixture decisions`n`n## 2026-08-30 - U0 - column amended`nwhy`n" -Walkthrough "# w`n"
    [System.IO.File]::WriteAllText((Join-Path $fx.dfu "PLAN.md"),
        (New-PlanText -U0Validated "the drill runs green[^gym]" -Amendments "[^gym]: the gym run is observed"),
        [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-qam", "move the gym requirement to a footnote"))
    $g = Get-ChainProbe -Fx $fx
    [void](Assert-ClauseNotMet -Step "D" -ClauseId "2" -Json $g.run.json -Because "a requirement was moved out of the column into a footnote")
    [void](Assert-That -What "[D-4] a requirement moved to a footnote is NOT carried forward" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "fail" -and [string]$g.probe.note -match "gym run is observed") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# D-5 AN ADDITION MUST STILL PASS. C.8 is explicit: a chain that ADDED requirements is the
# chain working as intended, and a gate that punished additions would push every honest
# amendment towards silence.
$fx = $null
try {
    $fx = New-ChainFixture -Original $orig -Current ($orig + "; the oracle fires at least once")
    $g = Get-ChainProbe -Fx $fx
    [void](Assert-That -What "[D-5] a chain that only ADDS a requirement passes the ORIGINAL-vs-CURRENT probe" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
    [void](Assert-That -What "[D-5] the addition is REPORTED, not silently accepted" `
        -Condition ($null -ne $g.clause -and (@($g.clause.detail) -join " ") -match "ADDED") `
        -Detail ("detail: {0}" -f $(if ($g.clause) { (@($g.clause.detail) -join " / ") } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP D2 - THE DISPOSITION LEDGER IS CHECKED, NOT TAKEN AT ITS WORD. A drop dispositioned
# as a named follow-on must pass, or no honest amendment could ever land; a retraction
# recorded as "kept" must NOT, because the column's own words say it is not kept.
# =================================================================================
Write-Host ""
Write-Host "STEP D2 clause 2 - a valid disposition lands; a false one does not" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-ChainFixture -Original $orig -Current "the drill runs green"
    $sink = Join-Path $fx.root "documentation\notes\u0-followon.md"
    [System.IO.File]::WriteAllText($sink, "the gym run is carried forward here", [System.Text.UTF8Encoding]::new($false))
    $disp = Join-Path $fx.root "dispositions.json"
    $key  = "U0::the gym run is observed"
    [System.IO.File]::WriteAllText($disp,
        (@{ $key = @{ disposition = "follow-on"; owner = "orchestrator"; findings_sink = "documentation/notes/u0-followon.md" } } | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false))
    $g = Get-ChainProbe -Fx $fx -DispositionsPath $disp
    [void](Assert-That -What "[D2] the SAME drop PASSES once dispositioned as a named follow-on with an owner and a sink that exists" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))

    # ...and the same record with a sink that does NOT exist must not.
    [System.IO.File]::WriteAllText($disp,
        (@{ $key = @{ disposition = "follow-on"; owner = "orchestrator"; findings_sink = "documentation/notes/nowhere.md" } } | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false))
    $g2 = Get-ChainProbe -Fx $fx -DispositionsPath $disp
    [void](Assert-That -What "[D2] a follow-on whose findings sink does not exist is NOT a disposition" `
        -Condition ($null -ne $g2.probe -and [string]$g2.probe.verdict -eq "fail") `
        -Detail ("verdict={0} note={1}" -f $(if ($g2.probe) { $g2.probe.verdict } else { "?" }), $(if ($g2.probe) { $g2.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

$fx = $null
try {
    # A REWRITE dispositioned "kept" is legitimate - the ledger is asserting the reword
    # preserves the requirement, and that assertion is now on the record where it can be
    # disagreed with.
    $fx = New-ChainFixture -Original $orig -Current "the drill runs green; the gym run is observed twice"
    $disp = Join-Path $fx.root "dispositions.json"
    $key  = "U0::the gym run is observed"
    [System.IO.File]::WriteAllText($disp, (@{ $key = @{ disposition = "kept" } } | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false))
    $g = Get-ChainProbe -Fx $fx -DispositionsPath $disp
    [void](Assert-That -What "[D2] a REWORDED requirement recorded as 'kept' passes" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

$fx = $null
try {
    # ...but a RETRACTION recorded as "kept" must not. This is the ledger lying, and a
    # ledger this script simply believed would be the substring test again with a JSON file
    # in front of it.
    $fx = New-ChainFixture -Original $orig `
          -Current "the drill runs green; the gym run is observed is no longer required - dropped as unnecessary"
    $disp = Join-Path $fx.root "dispositions.json"
    $key  = "U0::the gym run is observed"
    [System.IO.File]::WriteAllText($disp, (@{ $key = @{ disposition = "kept" } } | ConvertTo-Json -Depth 5),
        [System.Text.UTF8Encoding]::new($false))
    $g = Get-ChainProbe -Fx $fx -DispositionsPath $disp
    [void](Assert-That -What "[D2] a RETRACTED requirement recorded as 'kept' still FAILS - the ledger cannot overrule the column" `
        -Condition ($null -ne $g.probe -and [string]$g.probe.verdict -eq "fail" -and [string]$g.probe.note -match "(?i)kept") `
        -Detail ("verdict={0} note={1}" -f $(if ($g.probe) { $g.probe.verdict } else { "?" }), $(if ($g.probe) { $g.probe.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP E - CLAUSE 3: with the database unreachable, NOT ONE door was attacked. The clause
# must refuse. This is the "clear because we didn't look" case in its purest form: no
# probe objected, and that must not read as containment.
# =================================================================================
Write-Host ""
Write-Host "STEP E  clause 3 - no door could be attacked at all" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 3; RepoRoot = $fx.root; WorkLine = $fx.line
        DbContainer = "dfu-drill-no-such-container"
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "E" -ClauseId "3" -Json $r.json -Because "the plane is unreachable so no door was attacked"
    [void](Assert-That -What "[E] every required door is named as NOT evaluated" `
        -Condition ($null -ne $c -and @($c.coverage.not_evaluated).Count -ge 7) `
        -Detail ("not_evaluated count: {0}" -f $(if ($c) { @($c.coverage.not_evaluated).Count } else { "?" })))
    [void](Assert-That -What "[E] no door probe claims 'pass' when nothing could be reached" `
        -Condition ($null -ne $c -and (@($c.probes | Where-Object { $_.name -like "door-*" -and $_.verdict -eq "pass" }).Count -eq 0)) `
        -Detail "a door that could not be reached must never report pass")
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP F - CLAUSE 4: an unmerged work/* branch and a live worktree must both be caught,
# and the named exclusion must NOT be counted against the plan.
# =================================================================================
Write-Host ""
Write-Host "STEP F  clause 4 - an unmerged work branch, a worktree, and the excluded branch" -ForegroundColor Cyan
$fx = $null
try {
    # The ledger RECORDS the carve-out. That is what makes it a carve-out - see the second
    # half of this step, where the same tree without the entry counts the branch.
    $decF = "# fixture decisions`n`n## 2026-08-31 - work/pod-key is an unrelated podcast effort`nnot part of this plan`n"
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions $decF -Walkthrough "# w`n"
    # A work branch genuinely AHEAD of the work line.
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("checkout", "-q", "-b", "work/leftover"))
    [System.IO.File]::WriteAllText((Join-Path $fx.root "extra.txt"), "x", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "leftover work"))
    # The named exclusion, also ahead - it must be reported as excluded, not as a failure.
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("checkout", "-q", "-b", "work/pod-key", $fx.line))
    [System.IO.File]::WriteAllText((Join-Path $fx.root "pod.txt"), "x", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "podcast work"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("checkout", "-q", $fx.line))
    # A worktree left behind.
    $wtPath = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-drill-wt-" + [guid]::NewGuid().ToString("N").Substring(0, 6))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("worktree", "add", "-q", "--detach", $wtPath))
    try {
        $r = Invoke-Target -Params @{
            Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
            PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
            WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        }
        $c = Assert-ClauseNotMet -Step "F" -ClauseId "4" -Json $r.json -Because "a work branch is unmerged and a worktree is still present"
        $pb = Get-Probe -Clause $c -Name "no-unmerged-work-branches"
        [void](Assert-That -What "[F] the unmerged branch is caught and NAMED" `
            -Condition ($null -ne $pb -and [string]$pb.verdict -eq "fail" -and [string]$pb.note -match "work/leftover") `
            -Detail ("note: {0}" -f $(if ($pb) { $pb.note } else { "<missing>" })))
        [void](Assert-That -What "[F] the EXCLUDED branch work/pod-key is not counted as unmerged" `
            -Condition ($null -ne $pb -and [string]$pb.note -notmatch "work/pod-key") `
            -Detail ("note: {0}" -f $(if ($pb) { $pb.note } else { "<missing>" })))
        [void](Assert-That -What "[F] the exclusion is RECORDED with its reason, not silently applied" `
            -Condition ($null -ne $c -and (@($c.detail) -join " ") -match "work/pod-key") `
            -Detail ("detail: {0}" -f $(if ($c) { (@($c.detail) -join " / ") } else { "" })))
        $pw = Get-Probe -Clause $c -Name "no-worktrees"
        [void](Assert-That -What "[F] the leftover worktree is caught" `
            -Condition ($null -ne $pw -and [string]$pw.verdict -eq "fail") `
            -Detail ("probe verdict: {0}" -f $(if ($pw) { $pw.verdict } else { "<missing>" })))

        # AND THE CARVE-OUT MUST BE EARNED. The exclusion used to be applied on this
        # script's own say-so, attributed to an operator ruling that appears in neither
        # DECISIONS.md nor PLAN.md. Strip the ledger entry and the branch must be counted
        # like any other - otherwise "excluded" is just a name in a file exempting itself.
        [System.IO.File]::WriteAllText((Join-Path $fx.dfu "DECISIONS.md"), "# fixture decisions`n",
            [System.Text.UTF8Encoding]::new($false))
        $r2 = Invoke-Target -Params @{
            Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
            PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
            WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        }
        $c2 = Get-Clause -Json $r2.json -Id "4"
        $pb2 = Get-Probe -Clause $c2 -Name "no-unmerged-work-branches"
        [void](Assert-That -What "[F] with NO ledger entry the carve-out does not apply and work/pod-key is counted" `
            -Condition ($null -ne $pb2 -and [string]$pb2.verdict -eq "fail" -and [string]$pb2.note -match "work/pod-key") `
            -Detail ("note: {0}" -f $(if ($pb2) { $pb2.note } else { "<missing>" })))
    } finally {
        [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("worktree", "remove", "--force", $wtPath))
        Remove-Scratch -Path $wtPath
    }
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP G - CLAUSE 5: a walkthrough row whose named check goes RED must fail the clause.
# The operator reviews by reading this document, so a false row is the worst kind.
# =================================================================================
Write-Host ""
Write-Host "STEP G  clause 5 - a walkthrough row whose named check is red" -ForegroundColor Cyan
$fx = $null
try {
    $wt = "# fixture walkthrough`n`n## U0 - do the thing`n**How to run:** ``cmd /c exit 4```n"
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough $wt
    $r = Invoke-Target -Params @{
        Only = 5; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "G" -ClauseId "5" -Json $r.json -Because "a row's named check exits 4"
    $p = Get-Probe -Clause $c -Name "walkthrough-U0-check-1"
    [void](Assert-That -What "[G] the failing row's own probe reports fail with its exit code" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.exit -eq "4") `
        -Detail ("verdict={0} exit={1}" -f $(if ($p) { $p.verdict } else { "?" }), $(if ($p) { $p.exit } else { "?" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP H - CLAUSE 6: a ledger with no U7 cycle must fail; and a ledger WITH one must
# still refuse while the named manual check has no recorded result. Both halves matter -
# the second is what stops a manual check from being decorative.
# =================================================================================
Write-Host ""
Write-Host "STEP H  clause 6 - no U7 cycle, then a cycle with no recorded manual result" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# fixture decisions`n`n## 2026-08-30 - U0 - something else`nno u7 here`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        ManualResults = (Join-Path $fx.root "no-such-manual.json")
    }
    $c = Assert-ClauseNotMet -Step "H" -ClauseId "6" -Json $r.json -Because "the ledger records no U7 cycle at all"

    # Now give it a cycle, but no recorded manual result.
    $dec2 = "# fixture decisions`n`n## 2026-08-30 - U7 - design change adopted against the pinned anchor`nadopted`n"
    [System.IO.File]::WriteAllText((Join-Path $fx.dfu "DECISIONS.md"), $dec2, [System.Text.UTF8Encoding]::new($false))
    $r2 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        ManualResults = (Join-Path $fx.root "no-such-manual.json")
    }
    $c2 = Get-Clause -Json $r2.json -Id "6"
    [void](Assert-That -What "[H] with a cycle but NO recorded manual result the clause is still not met" `
        -Condition ($null -ne $c2 -and [string]$c2.verdict -ne "met") `
        -Detail ("verdict: {0}" -f $(if ($c2) { $c2.verdict } else { "?" })))
    [void](Assert-That -What "[H] the manual check is NAMED and marked PENDING" `
        -Condition ($null -ne $c2 -and (@($c2.manual | Where-Object { $_.state -eq "PENDING" }).Count -ge 1)) `
        -Detail ("manual: {0}" -f $(if ($c2) { (@($c2.manual | ForEach-Object { $_.name + "=" + $_.state }) -join ",") } else { "" })))

    # And an INCOMPLETE record must not count as a result.
    $mf = Join-Path $fx.root "manual.json"
    [System.IO.File]::WriteAllText($mf, (@{ "u7-cycle-judged-against-pinned-anchor" = @{ verdict = "pass" } } | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
    $r3 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); ManualResults = $mf
    }
    $c3 = Get-Clause -Json $r3.json -Id "6"
    [void](Assert-That -What "[H] a manual record missing by/date/evidence is NOT a recorded result" `
        -Condition ($null -ne $c3 -and [string]$c3.verdict -ne "met") `
        -Detail ("verdict: {0}" -f $(if ($c3) { $c3.verdict } else { "?" })))

    # H-4: A MANUAL FILE THAT EXISTS BUT HOLDS NO RESULT. This is the state the shipped
    # manual-results file is actually in, and it made the mechanism UNREACHABLE: a
    # precedence bug in Get-ManualResult read a property that does not exist, Set-StrictMode
    # turned that into a throw, and the clause evaluator's catch replaced the whole clause
    # with `clause-6-threw` - discarding the machine probe that had already decided its
    # half. C.8 requires the script to REFUSE without a recorded result; crashing is not
    # refusing. Every fixture above passes a path with NO FILE, which is why the drill was
    # green over it.
    $mfEmpty = Join-Path $fx.root "manual-keyless.json"
    [System.IO.File]::WriteAllText($mfEmpty, (@{ _comment = "no results recorded here" } | ConvertTo-Json -Depth 3),
        [System.Text.UTF8Encoding]::new($false))
    $r4 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); ManualResults = $mfEmpty
    }
    $c4 = Get-Clause -Json $r4.json -Id "6"
    [void](Assert-That -What "[H-4] a manual file with NO keys leaves the clause manual-pending, not thrown" `
        -Condition ($null -ne $c4 -and [string]$c4.verdict -eq "manual-pending") `
        -Detail ("verdict: {0}" -f $(if ($c4) { $c4.verdict } else { "?" })))
    [void](Assert-That -What "[H-4] the clause's MACHINE probe survives - it is not discarded by a crash" `
        -Condition ($null -ne (Get-Probe -Clause $c4 -Name "u7-cycle-recorded")) `
        -Detail ("probes: {0}" -f $(if ($c4) { ((@($c4.probes) | ForEach-Object { $_.name }) -join ",") } else { "?" })))
    [void](Assert-That -What "[H-4] no probe reports that the clause evaluator threw" `
        -Condition ($null -ne $c4 -and (@($c4.probes | Where-Object { $_.name -match "threw" }).Count -eq 0)) `
        -Detail "a throw is not a refusal")

    # H-5: A KEY WITH AN EMPTY RESULT is a note somebody left, not a check somebody ran.
    $mfEmptyVal = Join-Path $fx.root "manual-empty.json"
    [System.IO.File]::WriteAllText($mfEmptyVal,
        (@{ "u7-cycle-judged-against-pinned-anchor" = @{ verdict = ""; by = ""; date = ""; evidence = "" } } | ConvertTo-Json -Depth 4),
        [System.Text.UTF8Encoding]::new($false))
    $r5 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); ManualResults = $mfEmptyVal
    }
    $c5 = Get-Clause -Json $r5.json -Id "6"
    [void](Assert-That -What "[H-5] a key whose fields are EMPTY is still pending" `
        -Condition ($null -ne $c5 -and [string]$c5.verdict -eq "manual-pending") `
        -Detail ("verdict: {0}" -f $(if ($c5) { $c5.verdict } else { "?" })))

    # H-6: A COMPLETE RESULT MUST ACTUALLY LAND. A gate that can never be satisfied is not
    # a gate, it is a wall - and nobody would trust the rest of the file's refusals if its
    # one recorded-result path had never been shown to work.
    $mfOk = Join-Path $fx.root "manual-ok.json"
    [System.IO.File]::WriteAllText($mfOk,
        (@{ "u7-cycle-judged-against-pinned-anchor" = @{ verdict = "pass"; by = "a tester who did not build it"
                                                         date = "2026-08-31"; evidence = "DECISIONS.md 2026-08-30 U7 entry cites anchor A3" } } | ConvertTo-Json -Depth 4),
        [System.Text.UTF8Encoding]::new($false))
    $r6 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); ManualResults = $mfOk
    }
    $c6 = Get-Clause -Json $r6.json -Id "6"
    [void](Assert-That -What "[H-6] a COMPLETE recorded result satisfies the manual check and the clause is met" `
        -Condition ($null -ne $c6 -and [string]$c6.verdict -eq "met") `
        -Detail ("verdict: {0}; manual: {1}" -f $(if ($c6) { $c6.verdict } else { "?" }), `
                 $(if ($c6) { (@($c6.manual | ForEach-Object { $_.name + "=" + $_.state }) -join ",") } else { "" })))

    # H-7: A RECORDED *FAILING* RESULT IS A FAILURE, not a pending one.
    $mfBad = Join-Path $fx.root "manual-fail.json"
    [System.IO.File]::WriteAllText($mfBad,
        (@{ "u7-cycle-judged-against-pinned-anchor" = @{ verdict = "fail"; by = "a tester"; date = "2026-08-31"
                                                         evidence = "the entry cites no anchor" } } | ConvertTo-Json -Depth 4),
        [System.Text.UTF8Encoding]::new($false))
    $r7 = Invoke-Target -Params @{
        Only = 6; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); ManualResults = $mfBad
    }
    $c7 = Get-Clause -Json $r7.json -Id "6"
    [void](Assert-That -What "[H-7] a recorded FAILING manual result makes the clause unmet, not manual-pending" `
        -Condition ($null -ne $c7 -and [string]$c7.verdict -eq "unmet") `
        -Detail ("verdict: {0}" -f $(if ($c7) { $c7.verdict } else { "?" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP I - CLAUSE 7: a phase with no ledger entry and no findings note must fail.
# =================================================================================
Write-Host ""
Write-Host "STEP I  clause 7 - a phase with no audit trail" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# fixture decisions`n`n## unrelated entry`nnothing`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 7; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c = Assert-ClauseNotMet -Step "I" -ClauseId "7" -Json $r.json -Because "the phase has neither a ledger entry nor a findings note"
    $p = Get-Probe -Clause $c -Name "audit-trail-U0"
    [void](Assert-That -What "[I] the probe names BOTH missing artefacts" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "DECISIONS" -and [string]$p.note -match "findings note") `
        -Detail ("note: {0}" -f $(if ($p) { $p.note } else { "<missing>" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP J - CLAUSE 8: with the plane unreachable the clause must refuse. Section C.8 says
# this clause MAY fail; what it must never do is pass because nothing answered.
# =================================================================================
Write-Host ""
Write-Host "STEP J  clause 8 - the memory plane cannot be measured" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 8; RepoRoot = $fx.root; WorkLine = $fx.line
        DbContainer = "dfu-drill-no-such-container"
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c = Assert-ClauseNotMet -Step "J" -ClauseId "8" -Json $r.json -Because "the plane could not be measured at all"
    [void](Assert-That -What "[J] no compounding probe claims 'pass' against an unreachable plane" `
        -Condition ($null -ne $c -and (@($c.probes | Where-Object { $_.verdict -eq "pass" }).Count -eq 0)) `
        -Detail "an unmeasurable plane must not read as a compounding one")
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP K - THE CENSUS ITSELF. An outcome word nobody enumerated must REFUSE, with no
# branch anywhere naming it. This is the generalisation the whole design rests on: if a
# future clause returns a verdict this script has never heard of, the board must not wave
# it through. Tested against the SHIPPED classification functions, dot-sourced from a
# scratch copy whose function region is asserted byte-identical to the original.
# =================================================================================
Write-Host ""
Write-Host "STEP K  the census refuses a verdict word nobody enumerated" -ForegroundColor Cyan
$scratchCopy = $null
try {
    $full = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
    $marker = "# THE BOARD. Every clause lands in exactly one counted bucket"
    $idx = $full.IndexOf($marker)
    if ($idx -lt 0) {
        [void](Assert-That -What "[K] the target's board section can be located for isolation" -Condition $false -Detail "marker not found")
    } else {
        # Everything BEFORE the board is definitions only; dot-sourcing it runs no checks.
        $defs = $full.Substring(0, $idx)
        $scratchCopy = Join-Path ([System.IO.Path]::GetTempPath()) ("dfu-defs-" + [guid]::NewGuid().ToString("N").Substring(0, 8) + ".ps1")
        # Strip the param() block so the copy dot-sources cleanly as plain definitions.
        $defs2 = [regex]::Replace($defs, '(?s)\[CmdletBinding\(\)\]\s*param\(.*?\n\)\s*\n', "")
        [System.IO.File]::WriteAllText($scratchCopy, $defs2, [System.Text.UTF8Encoding]::new($false))
        [void](Assert-That -What "[K] the isolated copy is the SHIPPED definitions, not a rewrite" `
            -Condition ($defs.Contains('$script:DfuBucketBoard') -and $defs.Contains('function Get-DfuBucket')) `
            -Detail "the copy must contain the real classification functions")
        . $scratchCopy
        $invented = Get-DfuBucket "a-word-this-script-has-never-heard-of"
        [void](Assert-That -What "[K] an unenumerated clause verdict lands in 'unrecognised'" `
            -Condition ($invented -eq "unrecognised") -Detail ("got: {0}" -f $invented))
        [void](Assert-That -What "[K] 'unrecognised' is a REFUSING bucket - it is not the clear bucket" `
            -Condition ($invented -ne $script:DfuClearBucket) -Detail ("clear bucket is: {0}" -f $script:DfuClearBucket))
        [void](Assert-That -What "[K] every declared bucket has a headline word, so a new bucket cannot be silent" `
            -Condition (@(Get-DfuBucketNames) -contains "unrecognised") `
            -Detail ("buckets: {0}" -f ((Get-DfuBucketNames) -join ",")))
        # And the probe vocabulary, one level down.
        $pr = New-Probe -Name "invented" -Command "n/a" -Run { @{ verdict = "definitely-fine"; note = "" } }
        [void](Assert-That -What "[K] a probe answering an unenumerated word becomes 'indeterminate', never a pass" `
            -Condition ([string]$pr.verdict -eq "indeterminate") -Detail ("got: {0}" -f $pr.verdict))
        $pr2 = New-Probe -Name "thrower" -Command "n/a" -Run { throw "boom" }
        [void](Assert-That -What "[K] a probe that THROWS becomes 'indeterminate', never a pass" `
            -Condition ([string]$pr2.verdict -eq "indeterminate") -Detail ("got: {0}" -f $pr2.verdict))

        # AND THE DOOR RULE ITSELF, against the shipped function. These four rows are the
        # whole of R2: a door that returns neither the fixture nor its control is not a
        # bound door, and a non-200 body cannot show a leak. Pointing the real script at
        # `openbrain-postgrest:3000/nosuch` made all five PostgREST doors report "attacked
        # with the fixture and it did not come back" - coverage 8 of 8 - because the status
        # code was requested from curl and never read.
        $dv1 = Resolve-DoorVerdict -Reachable $true -Status 200 -SawPersonal $false -SawOps $true
        [void](Assert-That -What "[K] a door that refuses the fixture AND returns the ops control passes" `
            -Condition ([string]$dv1.verdict -eq "pass") -Detail ("got: {0}" -f $dv1.verdict))
        $dv2 = Resolve-DoorVerdict -Reachable $true -Status 200 -SawPersonal $false -SawOps $false
        [void](Assert-That -What "[K] a door that returns NEITHER is indeterminate, never a pass" `
            -Condition ([string]$dv2.verdict -eq "indeterminate") -Detail ("got: {0}" -f $dv2.verdict))
        $dv3 = Resolve-DoorVerdict -Reachable $true -Status 404 -SawPersonal $false -SawOps $false
        [void](Assert-That -What "[K] a NON-200 answer is indeterminate - a 404 body proves nothing" `
            -Condition ([string]$dv3.verdict -eq "indeterminate") -Detail ("got: {0}" -f $dv3.verdict))
        $dv4 = Resolve-DoorVerdict -Reachable $true -Status 200 -SawPersonal $true -SawOps $true
        [void](Assert-That -What "[K] a door that returns the personal fixture fails" `
            -Condition ([string]$dv4.verdict -eq "fail") -Detail ("got: {0}" -f $dv4.verdict))
        $dv5 = Resolve-DoorVerdict -Reachable $false -Status 0 -SawPersonal $false -SawOps $false
        [void](Assert-That -What "[K] an unreachable door is indeterminate, never closed" `
            -Condition ([string]$dv5.verdict -eq "indeterminate") -Detail ("got: {0}" -f $dv5.verdict))
    }
} finally { if ($scratchCopy -and (Test-Path -LiteralPath $scratchCopy)) { Remove-Item -LiteralPath $scratchCopy -Force -ErrorAction SilentlyContinue } }

# =================================================================================
# STEP M - CLAUSE 4: THE GITLINK GATE MUST ASK THE REMOTE. The tip-branch path did query
# it; the fallback for a non-tip commit was `git fetch --dry-run origin <sha>` run inside
# OB1 itself, which git answers from the LOCAL object store - it sees it already has the
# commit and does nothing, exit 0. So a commit that exists only in this clone passed a gate
# whose whole purpose is "a fresh --recurse-submodules clone would break".
#
# The fixture is that exact situation, built from real repositories: a bare remote, a
# submodule whose local checkout holds one more commit than it ever pushed, and a parent
# pinning that commit.
# =================================================================================
Write-Host ""
Write-Host "STEP M  clause 4 - a gitlink pinning a commit the remote does not have" -ForegroundColor Cyan
$fx = $null
$sandbox = $null
try {
    $sandbox = New-Scratch
    $bare = Join-Path $sandbox "sub-remote.git"
    $work = Join-Path $sandbox "sub-work"
    [void](Invoke-InDir -Dir $sandbox -Exe "git" -Arguments @("init", "-q", "--bare", $bare))
    New-Item -ItemType Directory -Path $work -Force | Out-Null
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("config", "user.email", "drill@example.invalid"))
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("config", "user.name", "dfu drill"))
    $shas = @()
    foreach ($n in @(1, 2, 3)) {
        [System.IO.File]::WriteAllText((Join-Path $work "f.txt"), ("rev " + $n), [System.Text.UTF8Encoding]::new($false))
        [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("add", "-A"))
        [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("commit", "-q", "-m", ("c" + $n)))
        $shas += (Invoke-InDir -Dir $work -Exe "git" -Arguments @("rev-parse", "HEAD")).out.Trim()
    }
    # PUSHED: c1..c3. The remote's only tip is c3, so c2 is reachable-but-not-a-tip - the
    # case the fallback exists for.
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("push", "-q", $bare, "main"))
    # NOT PUSHED: c4. It exists in this clone and nowhere else.
    [System.IO.File]::WriteAllText((Join-Path $work "f.txt"), "rev 4 - never pushed", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("commit", "-q", "-m", "c4"))
    $unpushed = (Invoke-InDir -Dir $work -Exe "git" -Arguments @("rev-parse", "HEAD")).out.Trim()

    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("-c", "protocol.file.allow=always", "submodule", "add", "-q", $bare, "OB1"))
    $ob1 = Join-Path $fx.root "OB1"

    # (a) THE NEGATIVE: pin the commit the remote never received.
    [void](Invoke-InDir -Dir $ob1 -Exe "git" -Arguments @("fetch", "-q", $work, $unpushed))
    [void](Invoke-InDir -Dir $ob1 -Exe "git" -Arguments @("checkout", "-q", $unpushed))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1", ".gitmodules"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "pin an OB1 commit that was never pushed"))
    $r = Invoke-Target -Params @{
        Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "M" -ClauseId "4" -Json $r.json -Because "the gitlink pins a commit the remote does not have"
    $pg = Get-Probe -Clause $c -Name "gitlink-reachable-on-remote"
    [void](Assert-That -What "[M] a gitlink the REMOTE cannot serve fails, even though the commit is here locally" `
        -Condition ($null -ne $pg -and [string]$pg.verdict -eq "fail") `
        -Detail ("verdict={0} note={1}" -f $(if ($pg) { $pg.verdict } else { "?" }), $(if ($pg) { $pg.note } else { "" })))
    [void](Assert-That -What "[M] the failing probe says the REMOTE refused it, not that a local ref was missing" `
        -Condition ($null -ne $pg -and [string]$pg.note -match "(?i)remote") `
        -Detail ("note: {0}" -f $(if ($pg) { $pg.note } else { "" })))

    # (b) THE POSITIVE: pin a commit that is reachable on the remote but is NOT a tip, so
    # the fallback is the thing being exercised. A gate that can only fail is a wall.
    [void](Invoke-InDir -Dir $ob1 -Exe "git" -Arguments @("checkout", "-q", $shas[1]))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "pin a reachable non-tip OB1 commit"))
    $r2 = Invoke-Target -Params @{
        Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c2 = Get-Clause -Json $r2.json -Id "4"
    $pg2 = Get-Probe -Clause $c2 -Name "gitlink-reachable-on-remote"
    [void](Assert-That -What "[M] a reachable NON-TIP commit passes - the fallback asks the remote and gets a yes" `
        -Condition ($null -ne $pg2 -and [string]$pg2.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($pg2) { $pg2.verdict } else { "?" }), $(if ($pg2) { $pg2.note } else { "" })))

    # (c) THE SUBSTRING. The gate's FIRST path tested `git ls-remote origin` output for the
    # pinned sha as a raw SUBSTRING of the whole blob rather than a match on the SHA COLUMN,
    # so a REF NAMED AFTER the commit satisfied it. That is not hypothetical: a
    # `git tag rollback-$(git rev-parse HEAD)` pushed to OB1 would turn this gate green for
    # a commit a fresh --recurse-submodules clone could not fetch. Round 2 replaced this
    # exact substring-for-structure test in clause 2 and left it here.
    #
    # The fixture is that: a bare remote that does NOT have the commit, carrying one tag
    # whose NAME contains the pinned sha.
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("tag", ("rollback-" + $unpushed), $shas[2]))
    [void](Invoke-InDir -Dir $work -Exe "git" -Arguments @("push", "-q", $bare, ("refs/tags/rollback-" + $unpushed)))
    [void](Invoke-InDir -Dir $ob1 -Exe "git" -Arguments @("checkout", "-q", $unpushed))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "re-pin the unpushed commit, with a tag NAMED after it on the remote"))
    $r3 = Invoke-Target -Params @{
        Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c3 = Assert-ClauseNotMet -Step "M2" -ClauseId "4" -Json $r3.json -Because "the remote carries only a TAG NAMED after the pinned sha, not the commit"
    $pg3 = Get-Probe -Clause $c3 -Name "gitlink-reachable-on-remote"
    [void](Assert-That -What "[M2] a ref NAMED after the pinned sha does not make it reachable" `
        -Condition ($null -ne $pg3 -and [string]$pg3.verdict -eq "fail") `
        -Detail ("verdict={0} note={1}" -f $(if ($pg3) { $pg3.verdict } else { "?" }), $(if ($pg3) { $pg3.note } else { "" })))
    [void](Assert-That -What "[M2] and it failed because the REMOTE refused the commit, not because a name was missing" `
        -Condition ($null -ne $pg3 -and [string]$pg3.note -match "(?i)refused") `
        -Detail ("note: {0}" -f $(if ($pg3) { $pg3.note } else { "" })))

    # (d) THE TIP PATH STILL WORKS, and says which column it matched in. A gate that can
    # only fail is a wall.
    [void](Invoke-InDir -Dir $ob1 -Exe "git" -Arguments @("checkout", "-q", $shas[2]))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "pin the remote's actual tip"))
    $r4 = Invoke-Target -Params @{
        Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $pg4 = Get-Probe -Clause (Get-Clause -Json $r4.json -Id "4") -Name "gitlink-reachable-on-remote"
    [void](Assert-That -What "[M2] a commit that IS an advertised tip passes, matched in the sha column" `
        -Condition ($null -ne $pg4 -and [string]$pg4.verdict -eq "pass" -and [string]$pg4.note -match "SHA COLUMN") `
        -Detail ("verdict={0} note={1}" -f $(if ($pg4) { $pg4.verdict } else { "?" }), $(if ($pg4) { $pg4.note } else { "" })))
} finally {
    if ($fx) { Remove-Scratch -Path $fx.root }
    if ($sandbox) { Remove-Scratch -Path $sandbox }
}

# =================================================================================
# STEP E2 - CLAUSE 3: THE PREDICATE'S SOURCE MUST BE ON THE WORK LINE. C.8.3 requires the
# backfill AND the flip to LAND; the probe only inserted two rows in a rolled-back
# transaction, so a boundary live in production from code no clone of this repository
# pins read as "the predicate is fail-closed, measured". The identical guard already
# existed on the RLS twin in clause 4 and was not applied here.
#
# This runs under -SkipLive on purpose: the question is about the PINNED TREE, not the
# database, so it must be answerable without one - and it must still refuse when the tree
# does not carry the SQL.
# =================================================================================
Write-Host ""
Write-Host "STEP E2 clause 3 - the fail-closed predicate's SQL is not in the pinned OB1 tree" -ForegroundColor Cyan
$fx = $null
$sandbox2 = $null
try {
    $sandbox2 = New-Scratch
    $bare2 = Join-Path $sandbox2 "sub-remote.git"
    $work2 = Join-Path $sandbox2 "sub-work"
    [void](Invoke-InDir -Dir $sandbox2 -Exe "git" -Arguments @("init", "-q", "--bare", $bare2))
    New-Item -ItemType Directory -Path $work2 -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $work2 "docker") -Force | Out-Null
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("init", "-q", "-b", "main"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("config", "user.email", "drill@example.invalid"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("config", "user.name", "dfu drill"))
    # A tree WITHOUT the fail-closed SQL - exactly the live situation the send-back names.
    [System.IO.File]::WriteAllText((Join-Path $work2 "docker\init-agent-memory.sql"), "-- base`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("commit", "-q", "-m", "no fail-closed sql here"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("push", "-q", $bare2, "main"))

    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("-c", "protocol.file.allow=always", "submodule", "add", "-q", $bare2, "OB1"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1", ".gitmodules"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "pin an OB1 tree with no fail-closed predicate"))
    $r = Invoke-Target -Params @{
        Only = 3; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "E2" -ClauseId "3" -Json $r.json -Because "the predicate's defining SQL is absent from the pinned OB1 tree"
    $p = Get-Probe -Clause $c -Name "corpus-predicate-source-on-work-line"
    [void](Assert-That -What "[E2] the source probe FAILS and names the missing SQL" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "init-agent-memory-corpus-failclosed.sql") `
        -Detail ("verdict={0} note={1}" -f $(if ($p) { $p.verdict } else { "?" }), $(if ($p) { $p.note } else { "" })))

    # THE POSITIVE: add the file, re-pin, and the same probe must pass - otherwise this is
    # a probe that can only say no.
    [System.IO.File]::WriteAllText((Join-Path $work2 "docker\init-agent-memory-corpus-failclosed.sql"),
        "-- the flip`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("commit", "-q", "-m", "land the fail-closed predicate"))
    [void](Invoke-InDir -Dir $work2 -Exe "git" -Arguments @("push", "-q", $bare2, "main"))
    $ob1b = Join-Path $fx.root "OB1"
    [void](Invoke-InDir -Dir $ob1b -Exe "git" -Arguments @("fetch", "-q", "origin"))
    [void](Invoke-InDir -Dir $ob1b -Exe "git" -Arguments @("checkout", "-q", "origin/main"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "OB1"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "bump the gitlink to the tree that carries it"))
    $r2 = Invoke-Target -Params @{
        Only = 3; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $p2 = Get-Probe -Clause (Get-Clause -Json $r2.json -Id "3") -Name "corpus-predicate-source-on-work-line"
    [void](Assert-That -What "[E2] and it PASSES once the pinned tree carries the SQL" `
        -Condition ($null -ne $p2 -and [string]$p2.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($p2) { $p2.verdict } else { "?" }), $(if ($p2) { $p2.note } else { "" })))
} finally {
    if ($fx) { Remove-Scratch -Path $fx.root }
    if ($sandbox2) { Remove-Scratch -Path $sandbox2 }
}

# =================================================================================
# STEP N - THE CHECKER MUST NOT TAKE ITS POPULATION FROM THE DOCUMENT UNDER TEST.
#
# Clauses 1, 2 and 7 derived their subjects from the CURRENT PLAN.md, so a phase could
# delete itself out of its own population. Three shapes, all constructed here:
#
#   N1  the row is DELETED       -> the phase vanished, coverage read "1 of 1"
#   N2  the row loses its BOLD   -> `| **U1** |` -> `| U1 |`. A change a reader cannot
#                                    see, because the row is still printed. Clause 7 went
#                                    unmet -> met over a phase still in the document.
#   N3  the row is RENAMED       -> `| **UX** |`. Same disappearance, different edit.
#
# In every shape the clause must be NOT MET and phase-floor-present must FAIL naming the
# phase - never a smaller N. N2 additionally asserts the positive: the unbolded row is
# still PARSED, so the subject survives the formatting change instead of being rescued
# only by the floor.
# =================================================================================
Write-Host ""
Write-Host "STEP N  clauses 1, 2, 7 - a phase deleted, unbolded, or renamed out of the table" -ForegroundColor Cyan
foreach ($shape in @(
    @{ tag = "N1"; what = "U1's row is DELETED from section 2"; plan = (New-PlanText -OmitPhases @("U1")) },
    @{ tag = "N3"; what = "U1's row is RENAMED to UX";          plan = (New-PlanText -RenamePhases @{ "U1" = "UX" }) }
)) {
    $fx = $null
    try {
        $fx = New-FixtureRepo -Plan $shape.plan -Decisions "# d`n`n## U0 U1 U2 U3 U4 U5 U6 - everything`nnote`n" -Walkthrough "# w`n"
        foreach ($cl in @("1", "2", "7")) {
            $r = Invoke-Target -Params @{
                Only = [int]$cl; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
                PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
                WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
            }
            $c = Assert-ClauseNotMet -Step $shape.tag -ClauseId $cl -Json $r.json -Because $shape.what
            $pf = Get-Probe -Clause $c -Name "phase-floor-present"
            [void](Assert-That -What ("[{0}] clause {1}: phase-floor-present FAILS and names U1" -f $shape.tag, $cl) `
                -Condition ($null -ne $pf -and [string]$pf.verdict -eq "fail" -and [string]$pf.note -match "U1") `
                -Detail ("verdict={0} note={1}" -f $(if ($pf) { $pf.verdict } else { "?" }), $(if ($pf) { $pf.note } else { "" })))
            [void](Assert-That -What ("[{0}] clause {1}: the population did NOT shrink - U1 is still a declared subject" -f $shape.tag, $cl) `
                -Condition ($null -ne $c -and [int]$c.coverage.expected -ge 8) `
                -Detail ("expected: {0}" -f $(if ($c) { $c.coverage.expected } else { "?" })))
        }
    } finally { if ($fx) { Remove-Scratch -Path $fx.root } }
}

# N2 - THE FORMATTING CHANGE. The row is still in the document, so the SUBJECT must
# survive: the floor must be satisfied AND clause 7 must still probe U1 by name.
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -UnboldPhases @("U1")) -Decisions "# d`n`n## unrelated`nnothing`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 7; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c = Assert-ClauseNotMet -Step "N2" -ClauseId "7" -Json $r.json -Because "an unbolded row is still a row and still needs an audit trail"
    $pf = Get-Probe -Clause $c -Name "phase-floor-present"
    [void](Assert-That -What "[N2] removing the bold does NOT remove the phase from the table" `
        -Condition ($null -ne $pf -and [string]$pf.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($pf) { $pf.verdict } else { "?" }), $(if ($pf) { $pf.note } else { "" })))
    [void](Assert-That -What "[N2] the unbolded phase is still probed BY NAME, not silently dropped" `
        -Condition ($null -ne (Get-Probe -Clause $c -Name "audit-trail-U1")) `
        -Detail ("probes: {0}" -f (@($c.probes | ForEach-Object { $_.name }) -join ",")))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# N5 - THE CELL MAY SAY MORE THAN THE ID. The live table writes `| **U7 (standing)** |`,
# and the first attempt at N2's fix anchored the closing pipe straight after the emphasis -
# which dropped that phase out of the population, fixing one way for a phase to vanish by
# introducing another. Both shapes are asserted together here so neither can be traded for
# the other again.
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -SuffixPhases @{ "U2" = "(standing)" } -UnboldPhases @("U1")) `
                          -Decisions "# d`n`n## unrelated`nnothing`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 7; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c = Get-Clause -Json $r.json -Id "7"
    [void](Assert-That -What "[N5] a cell that says more than the id - `| **U2 (standing)** |` - is still parsed" `
        -Condition ($null -ne (Get-Probe -Clause $c -Name "audit-trail-U2")) `
        -Detail ("probes: {0}" -f (@($c.probes | ForEach-Object { $_.name }) -join ",")))
    $pf = Get-Probe -Clause $c -Name "phase-floor-present"
    [void](Assert-That -What "[N5] and the floor is satisfied by both cell shapes at once" `
        -Condition ($null -ne $pf -and [string]$pf.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($pf) { $pf.verdict } else { "?" }), $(if ($pf) { $pf.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# N4 - THE FLOOR ITSELF DRIFTS. If C.8 clause 1 stops naming U0-U6, the pinned floor is no
# longer pinned TO anything, and that is a red - the same guarantee door-set-matches-plan
# gives clause 3's doors.
$fx = $null
try {
    $planDrift = (New-PlanText) -replace "For U0-U6, the section 2", "For U0-U5, the section 2"
    $fx = New-FixtureRepo -Plan $planDrift -Decisions "# d`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 1; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "N4" -ClauseId "1" -Json $r.json -Because "C.8 clause 1 no longer names the phase set this script pins"
    $pd = Get-Probe -Clause $c -Name "phase-floor-matches-plan"
    [void](Assert-That -What "[N4] the floor's own drift check is what failed, naming U6" `
        -Condition ($null -ne $pd -and [string]$pd.verdict -eq "fail" -and [string]$pd.note -match "U6") `
        -Detail ("verdict={0} note={1}" -f $(if ($pd) { $pd.verdict } else { "?" }), $(if ($pd) { $pd.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP I2 - CLAUSE 7: C.8.7 names THREE artifacts per phase, and the third - "commit
# messages stating what was validated and by which check" - had no `git log` anywhere in
# the authority. Coverage reported 8 of 8 with that half never examined. Here every phase
# has a ledger entry and a findings note and NO qualifying commit message, so the clause
# must fail for exactly that reason and say so.
# =================================================================================
Write-Host ""
Write-Host "STEP I2 clause 7 - a phase with a ledger entry and a note but no commit message" -ForegroundColor Cyan
$fx = $null
try {
    $dec = "# fixture decisions`n`n## U0 U1 U2 U3 U4 U5 U6 - all phases`nrecorded`n"
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions $dec -Walkthrough "# w`n"
    [System.IO.File]::WriteAllText((Join-Path $fx.root "documentation\notes\dfu-drill-note.md"),
        "# findings`n`nU0 U1 U2 U3 U4 U5 U6 all appear here.`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "-A"))
    # THE COMMIT MESSAGE NAMES THE PHASES AND NOTHING ELSE - no check, no artifact. That is
    # not "stating what was validated and by which check".
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m", "U0 U1 U2 U3 U4 U5 U6 done"))
    $r = Invoke-Target -Params @{
        Only = 7; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c = Assert-ClauseNotMet -Step "I2" -ClauseId "7" -Json $r.json -Because "no commit message says what was validated or by which check"
    $p = Get-Probe -Clause $c -Name "audit-trail-U0"
    [void](Assert-That -What "[I2] the ledger and note halves are satisfied, and the COMMIT half is what failed" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "commit message" -and `
                    [string]$p.note -notmatch "DECISIONS" -and [string]$p.note -notmatch "findings note") `
        -Detail ("note: {0}" -f $(if ($p) { $p.note } else { "<missing>" })))

    # AND THE POSITIVE: a message that names the phase AND the check satisfies it. A gate
    # that can only fail is a wall.
    [System.IO.File]::WriteAllText((Join-Path $fx.root "documentation\notes\dfu-drill-note2.md"),
        "# more`n`nU0 U1 U2 U3 U4 U5 U6`n", [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("add", "-A"))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-q", "-m",
        "U0 U1 U2 U3 U4 U5 U6: validated by verify-the-thing.ps1 re-running green"))
    $r2 = Invoke-Target -Params @{
        Only = 7; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); NotesDir = (Join-Path $fx.root "documentation\notes")
    }
    $c2 = Get-Clause -Json $r2.json -Id "7"
    $p2 = Get-Probe -Clause $c2 -Name "audit-trail-U0"
    [void](Assert-That -What "[I2] a commit naming the phase AND a check does satisfy the third artifact" `
        -Condition ($null -ne $p2 -and [string]$p2.verdict -eq "pass" -and [string]$p2.note -match "commit message") `
        -Detail ("verdict={0} note={1}" -f $(if ($p2) { $p2.verdict } else { "?" }), $(if ($p2) { $p2.note } else { "" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP D3 - CLAUSE 2: AN UNCOMMITTED WEAKENING. The chain was built from git while the
# phase set, the amendments and the parked check were read from DISK, so a column eroded
# in the working tree and not yet committed left the chain probe passing over the
# committed text. The working tree is a state of the chain.
# =================================================================================
Write-Host ""
Write-Host "STEP D3 clause 2 - a weakening that is on disk but not yet committed" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -U0Validated "the gym run is observed; the drill runs green") `
                          -Decisions "# d`n" -Walkthrough "# w`n"
    # NOT COMMITTED. The file on disk drops one requirement outright.
    [System.IO.File]::WriteAllText((Join-Path $fx.dfu "PLAN.md"),
        (New-PlanText -U0Validated "the drill runs green"), [System.Text.UTF8Encoding]::new($false))
    $r = Invoke-Target -Params @{
        Only = 2; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        Dispositions = (Join-Path $fx.root "no-such-dispositions.json")
    }
    $c = Assert-ClauseNotMet -Step "D3" -ClauseId "2" -Json $r.json -Because "the requirement was dropped on disk and never committed"
    $p = Get-Probe -Clause $c -Name "chain-U0-original-vs-current"
    [void](Assert-That -What "[D3] the chain probe sees the UNCOMMITTED column as the current one" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "gym run") `
        -Detail ("verdict={0} note={1}" -f $(if ($p) { $p.verdict } else { "?" }), $(if ($p) { $p.note } else { "" })))
    [void](Assert-That -What "[D3] and the working tree is printed as a step of the chain" `
        -Condition ($null -ne $c -and ((@($c.detail) -join " ") -match "uncommitted")) `
        -Detail ("detail: {0}" -f ((@($c.detail) | Select-Object -First 12) -join " | ")))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP F2 - CLAUSE 4: the service floor had NO drift check while this file's header
# claimed both pinned sets were checked back against the plan's words. A plan that names a
# service this script does not probe must turn the clause red, not lose the service.
# =================================================================================
Write-Host ""
Write-Host "STEP F2 clause 4 - the plan names a service the pinned set does not claim" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -ServiceList "the ops gateway, the andon board, the gate profiles, the RLS boundary at every stage including the direct clients, the quarantine broker") `
                          -Decisions "# d`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 4; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "F2" -ClauseId "4" -Json $r.json -Because "C.8 clause 4 names a service no probe here claims"
    $p = Get-Probe -Clause $c -Name "service-set-matches-plan"
    [void](Assert-That -What "[F2] the drift check is what failed, and it names the unclaimed service" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail" -and [string]$p.note -match "quarantine broker") `
        -Detail ("verdict={0} note={1}" -f $(if ($p) { $p.verdict } else { "?" }), $(if ($p) { $p.note } else { "" })))

    # THE POSITIVE: the plan as written must PASS the drift check, or the gate is a wall.
    $fx2 = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    try {
        $r2 = Invoke-Target -Params @{
            Only = 4; RepoRoot = $fx2.root; WorkLine = $fx2.line; SkipLive = $true
            PlanPath = (Join-Path $fx2.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx2.dfu "DECISIONS.md")
            WalkthroughPath = (Join-Path $fx2.dfu "WALKTHROUGH.md")
        }
        $p2 = Get-Probe -Clause (Get-Clause -Json $r2.json -Id "4") -Name "service-set-matches-plan"
        [void](Assert-That -What "[F2] and a plan whose service list matches the pinned set PASSES the drift check" `
            -Condition ($null -ne $p2 -and [string]$p2.verdict -eq "pass") `
            -Detail ("verdict={0} note={1}" -f $(if ($p2) { $p2.verdict } else { "?" }), $(if ($p2) { $p2.note } else { "" })))
    } finally { Remove-Scratch -Path $fx2.root }
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP Y - A NARROWED RUN MUST NEVER REPORT THE PLAN DONE. -Only is a convenience, not a
# route to a green board: the clauses it skips are counted as unevaluated.
# =================================================================================
Write-Host ""
Write-Host "STEP Y  asking fewer questions cannot produce a 'done' board" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
    $r = Invoke-Target -Params @{
        Only = 2; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    [void](Assert-That -What "[Y] a narrowed run exits non-zero" -Condition ($r.exit -ne 0) -Detail ("exit: {0}" -f $r.exit))
    [void](Assert-That -What "[Y] the board is not 'done' when clauses were skipped" `
        -Condition ($null -ne $r.json -and [string]$r.json.board -ne "done") `
        -Detail ("board: {0}" -f $(if ($r.json) { $r.json.board } else { "<no json>" })))
    [void](Assert-That -What "[Y] the census still balances across all eight clauses" `
        -Condition ($null -ne $r.json -and [bool]$r.json.balances -and [int]$r.json.census_total -eq 8) `
        -Detail ("balances={0} total={1}" -f $(if ($r.json) { $r.json.balances } else { "?" }), $(if ($r.json) { $r.json.census_total } else { "?" })))
} finally { if ($fx) { Remove-Scratch -Path $fx.root } }

# =================================================================================
# STEP Z - THE COMPLETENESS CENSUS. Every clause the AUTHORITY declares must have a
# constructed failing case above. This is derived from the authority itself, never from a
# list maintained here - add a clause there without adding a case here and this goes red.
# =================================================================================
Write-Host ""
Write-Host "STEP Z  every declared clause has a constructed failing case" -ForegroundColor Cyan
$declared = @()
$full = [System.IO.File]::ReadAllText($Target, [System.Text.Encoding]::UTF8)
foreach ($m in [regex]::Matches($full, '(?m)^\s{4}"(\d)"\s*=\s*"')) { $declared += $m.Groups[1].Value }
$declared = @($declared | Sort-Object -Unique)
[void](Assert-That -What "[Z] the authority's clause set could be derived from its own source" `
    -Condition ($declared.Count -ge 8) -Detail ("derived: {0}" -f ($declared -join ",")))
$uncovered = @($declared | Where-Object { -not $script:ClausesCovered.ContainsKey($_) })
[void](Assert-That -What "[Z] EVERY declared clause has a failing case in this drill" `
    -Condition ($uncovered.Count -eq 0) `
    -Detail ("clauses with NO constructed failure: {0} -- a clause with no failing case is a clause you have not implemented" -f ($uncovered -join ",")))
foreach ($k in ($script:ClausesCovered.Keys | Sort-Object)) {
    Write-Host ("   clause {0} <- failing case constructed in step {1}" -f $k, $script:ClausesCovered[$k]) -ForegroundColor DarkGray
}

# =================================================================================
# OPTIONAL: the LIVE failures. Off by default so the drill is deterministic, because a
# step that needs Docker would otherwise turn an infrastructure outage into a red drill.
# =================================================================================
if ($Live) {
    Write-Host ""
    Write-Host "STEP L  (live) the real plane reproduces its known-open failures" -ForegroundColor Cyan
    $r = Invoke-Target -Params @{ Only = 3 }
    $c = Get-Clause -Json $r.json -Id "3"
    # NOT "coverage is full". It is not, and it should not be: one door is a named manual
    # check and one returns neither twin, so both refuse. Asserting full coverage here is
    # what would push someone to make an unmeasurable door pass. What must be true is that
    # the live probes CAN measure - at least one door refuses the fixture while returning
    # its control - and that every subject they could not measure is NAMED.
    $doorProbes = @($c.probes | Where-Object { $_.name -like "door-*" })
    [void](Assert-That -What "[L] at least one door is measured against its positive control on the real plane" `
        -Condition (@($doorProbes | Where-Object { $_.verdict -eq "pass" }).Count -ge 1) `
        -Detail ("verdicts: {0}" -f (($doorProbes | ForEach-Object { $_.name + "=" + $_.verdict }) -join ", ")))
    $unnamed = @($doorProbes | Where-Object { $_.verdict -eq "indeterminate" -and
                 (@($c.coverage.not_evaluated) -notcontains ($_.name -replace "^door-", "")) })
    [void](Assert-That -What "[L] every door that could NOT be measured is named in not_evaluated" `
        -Condition ($unnamed.Count -eq 0) `
        -Detail ("unnamed: {0}; not_evaluated: {1}" -f (($unnamed | ForEach-Object { $_.name }) -join ","), (@($c.coverage.not_evaluated) -join ",")))
    [void](Assert-That -What "[L] the clause cannot be met while a door is unmeasured" `
        -Condition ($null -ne $c -and [string]$c.verdict -ne "met") `
        -Detail ("verdict: {0}" -f $(if ($c) { $c.verdict } else { "?" })))
    $pf = Get-Probe -Clause $c -Name "fixture-cleaned-up"
    [void](Assert-That -What "[L] the synthetic personal fixture is removed and 0 personal rows remain" `
        -Condition ($null -ne $pf -and [string]$pf.verdict -eq "pass") `
        -Detail ("note: {0}" -f $(if ($pf) { $pf.note } else { "<missing>" })))

    # THE LIVE REPRODUCTION OF R2. Aim the PostgREST doors at a path that does not exist.
    # Every request comes back 404 with a JSON error body and curl exits 0, so the old
    # probe - which asked curl for %{http_code} and never read it - reported every door as
    # "attacked with the fixture and it did not come back", coverage 8 of 8, including the
    # door that correctly fails against the real endpoint. Not one door may pass here.
    Write-Host ""
    Write-Host "STEP L2 (live) a PostgREST host that answers 404 must not read as containment" -ForegroundColor Cyan
    $r2 = Invoke-Target -Params @{ Only = 3; PostgrestHost = "openbrain-postgrest:3000/nosuch" }
    $c2 = Get-Clause -Json $r2.json -Id "3"
    $bogus = @($c2.probes | Where-Object { $_.name -like "door-postgrest-*" })
    [void](Assert-That -What "[L2] no PostgREST door passes when every request answers 404" `
        -Condition (@($bogus | Where-Object { $_.verdict -eq "pass" }).Count -eq 0) `
        -Detail ("verdicts: {0}" -f (($bogus | ForEach-Object { $_.name + "=" + $_.verdict }) -join ", ")))
    [void](Assert-That -What "[L2] and the clause does not claim to have evaluated them" `
        -Condition ($null -ne $c2 -and [int]$c2.coverage.evaluated -lt [int]$c2.coverage.expected) `
        -Detail ("coverage: {0} of {1}" -f $(if ($c2) { $c2.coverage.evaluated } else { "?" }), $(if ($c2) { $c2.coverage.expected } else { "?" })))

    # =============================================================================
    # STEP L3 (live) - EVERY DOOR PROBE IS FILTERED TO THE FIXTURE. Two of them asked for
    # `list_thoughts {"limit":25}` - an unfiltered newest-first window - while claiming
    # "every probe filtered to the fixture". Because the personal fixture is written BEFORE
    # its ops twin it is always the first of the pair pushed out of that window, so
    # concurrent ingest manufactures sawP=false / sawO=true, which reads as "the door
    # REFUSED the personal fixture". The assertion is on the RECORDED COMMAND, which is
    # what a reader would re-run.
    # =============================================================================
    Write-Host ""
    Write-Host "STEP L3 (live) every door probe's recorded command is aimed at the fixture" -ForegroundColor Cyan
    # door-set-matches-plan is the FLOOR's drift check, not a door - it has no fixture to
    # be aimed at, and counting it here would fail this assertion for the wrong reason.
    $doorCmds = @($c.probes | Where-Object { $_.name -like "door-*" -and $_.name -ne "door-set-matches-plan" -and $_.command -notmatch '^\(no probe' })
    $unfiltered = @($doorCmds | Where-Object { $_.command -notmatch '(?i)(dfu-done|eq\.[0-9]+)' })
    [void](Assert-That -What "[L3] no door is attacked with an unfiltered window" `
        -Condition ($unfiltered.Count -eq 0) `
        -Detail ("unfiltered: {0}" -f (($unfiltered | ForEach-Object { $_.name }) -join ", ")))
    $fw = Get-Probe -Clause $c -Name "fixture-write-landed"
    [void](Assert-That -What "[L3] the fixture is READ BACK before any absence is asserted" `
        -Condition ($null -ne $fw) -Detail "fixture-write-landed is the positive control on the WRITE")
    $bf = Get-Probe -Clause $c -Name "corpus-backfill-landed"
    $sp = Get-Probe -Clause $c -Name "corpus-predicate-source-on-work-line"
    [void](Assert-That -What "[L3] C.8.3's other two halves - the backfill and the predicate's source - are both probed" `
        -Condition ($null -ne $bf -and $null -ne $sp) `
        -Detail ("backfill={0} source={1}" -f $(if ($bf) { $bf.verdict } else { "<missing>" }), $(if ($sp) { $sp.verdict } else { "<missing>" })))

    # =============================================================================
    # STEP L4 (live) - CLAUSE 4's RLS SUBJECT MUST CONSULT THE DIRECT CLIENTS. relrowsecurity
    # and relforcerowsecurity are table flags, and a rolbypassrls role is unaffected by
    # both; C.8.4 asks for the boundary "at every stage including the direct clients". The
    # invariant asserted here holds whatever the table flags currently read: while a direct
    # client carries rolsuper or rolbypassrls, this subject may not be green.
    # =============================================================================
    Write-Host ""
    Write-Host "STEP L4 (live) the RLS subject cannot be green over a BYPASSRLS client" -ForegroundColor Cyan
    $r4 = Invoke-Target -Params @{ Only = 4 }
    $c4 = Get-Clause -Json $r4.json -Id "4"
    $p4 = Get-Probe -Clause $c4 -Name "service-rls-boundary"
    $clientLines = @(@($c4.detail) | Where-Object { $_ -match '^direct client role ' })
    [void](Assert-That -What "[L4] the direct clients were enumerated and their roles read" `
        -Condition ($clientLines.Count -ge 1) `
        -Detail ("detail lines: {0}" -f ((@($c4.detail) | Select-Object -First 20) -join " | ")))
    $bypassing = @($clientLines | Where-Object { $_ -notmatch '/f/f' })
    if ($bypassing.Count -ge 1) {
        [void](Assert-That -What "[L4] a direct client bypasses RLS, so the subject is NOT green" `
            -Condition ($null -ne $p4 -and [string]$p4.verdict -ne "pass") `
            -Detail ("verdict={0} ; bypassing: {1}" -f $(if ($p4) { $p4.verdict } else { "?" }), ($bypassing -join " ; ")))
    } else {
        [void](Assert-That -What "[L4] no direct client bypasses RLS - the subject may stand on the table flags" `
            -Condition $true -Detail "nothing to refute here")
    }
    $ps4 = Get-Probe -Clause $c4 -Name "service-set-matches-plan"
    [void](Assert-That -What "[L4] and clause 4's service floor is checked back against the plan's words" `
        -Condition ($null -ne $ps4 -and [string]$ps4.verdict -eq "pass") `
        -Detail ("verdict={0} note={1}" -f $(if ($ps4) { $ps4.verdict } else { "<missing>" }), $(if ($ps4) { $ps4.note } else { "" })))
}

Write-Host ""
Write-Host "-------------------------------------------------------------------------"
if ($script:Failures.Count -eq 0) {
    Write-Host (" DRILL GREEN - {0} assertions, 0 failed." -f $script:Checks) -ForegroundColor Green
    Write-Host (" {0} of {1} declared clauses have a constructed failing case." -f `
                @($script:ClausesCovered.Keys).Count, $declared.Count) -ForegroundColor Green
    Write-Host " The done-authority demonstrably CAN fail, clause by clause." -ForegroundColor Green
    Write-Host ""
    exit 0
}
Write-Host (" DRILL RED - {0} assertions, {1} failed:" -f $script:Checks, $script:Failures.Count) -ForegroundColor Red
foreach ($f in $script:Failures) { Write-Host ("   - {0}" -f $f) -ForegroundColor Red }
Write-Host ""
exit 1
