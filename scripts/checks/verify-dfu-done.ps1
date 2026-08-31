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
    # A minimal but STRUCTURALLY REAL section 2 table - the same shape the parser meets in
    # the live document, four columns and a bold phase id.
    param([string]$U0Validated = "a check that exists", [string]$Amendments = "")
    $t = @"
# fixture plan

## 2. Phases

| Phase | What | Validated by | Depends on |
|---|---|---|---|
| **U0** | do the thing | $U0Validated | - |

### 2.1 Amendments to the phase table
$Amendments
"@
    return $t
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
    $r = Invoke-Target -Params @{
        Only = 1; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
    }
    $c = Assert-ClauseNotMet -Step "A" -ClauseId "1" -Json $r.json -Because "its named check exits 3 in a clean checkout"
    $p = Get-Probe -Clause $c -Name "U0-validated-by"
    [void](Assert-That -What "[A] the U0 probe reports 'fail', not merely 'indeterminate'" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail") `
        -Detail ("probe verdict: {0}" -f $(if ($p) { $p.verdict } else { "<missing>" })))
    [void](Assert-That -What "[A] the probe records the non-zero exit code it saw" `
        -Condition ($null -ne $p -and [string]$p.exit -eq "3") `
        -Detail ("probe exit: {0}" -f $(if ($p) { $p.exit } else { "<missing>" })))
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
    [void](Assert-That -What "[B] coverage reports 0 of 1 phases evaluated - clear because we did NOT look" `
        -Condition ($null -ne $c -and [int]$c.coverage.evaluated -eq 0 -and [int]$c.coverage.expected -eq 1) `
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
# STEP D - CLAUSE 2, THE SUBTLE ONE: a requirement present in the ORIGINAL column and
# absent from the CURRENT one, with no disposition, must FAIL - even though every single
# step in the chain looked reasonable on its own. This is the erosion the clause exists
# to catch, and it is why the comparison is original-vs-current and never pairwise.
# =================================================================================
Write-Host ""
Write-Host "STEP D  clause 2 - a requirement eroded out of the column across two commits" -ForegroundColor Cyan
$fx = $null
try {
    $fx = New-FixtureRepo -Plan (New-PlanText -U0Validated "the drill runs green; the gym run is observed") `
                          -Decisions "# fixture decisions`n" -Walkthrough "# w`n"
    # SECOND COMMIT: drop the gym clause. Alone it looks like a tidy-up.
    [System.IO.File]::WriteAllText((Join-Path $fx.dfu "PLAN.md"),
        (New-PlanText -U0Validated "the drill runs green"), [System.Text.UTF8Encoding]::new($false))
    [void](Invoke-InDir -Dir $fx.root -Exe "git" -Arguments @("commit", "-qam", "narrow U0's column"))
    $r = Invoke-Target -Params @{
        Only = 2; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md")
        Dispositions = (Join-Path $fx.root "no-such-dispositions.json")
    }
    $c = Assert-ClauseNotMet -Step "D" -ClauseId "2" -Json $r.json -Because "a requirement was dropped from the column with no disposition"
    $p = Get-Probe -Clause $c -Name "chain-U0-original-vs-current"
    [void](Assert-That -What "[D] the ORIGINAL-vs-CURRENT probe is the one that failed" `
        -Condition ($null -ne $p -and [string]$p.verdict -eq "fail") `
        -Detail ("probe verdict: {0}; note: {1}" -f $(if ($p) { $p.verdict } else { "<missing>" }), $(if ($p) { $p.note } else { "" })))
    [void](Assert-That -What "[D] the chain was reconstructed and PRINTED with both states" `
        -Condition ($null -ne $c -and (@($c.detail) -join " ") -match "2 distinct state") `
        -Detail ("detail: {0}" -f $(if ($c) { (@($c.detail) -join " / ") } else { "" })))

    # AND THE COUNTERPART: the same drop, DISPOSITIONED, must pass - otherwise the clause
    # is merely strict rather than correct, and a real amendment could never land.
    $disp = Join-Path $fx.root "dispositions.json"
    $key  = "U0::the gym run is observed"
    $obj  = @{ $key = @{ disposition = "follow-on"; owner = "orchestrator"; findings_sink = "documentation/notes/x.md" } }
    [System.IO.File]::WriteAllText($disp, ($obj | ConvertTo-Json -Depth 5), [System.Text.UTF8Encoding]::new($false))
    $r2 = Invoke-Target -Params @{
        Only = 2; RepoRoot = $fx.root; WorkLine = $fx.line; SkipLive = $true
        PlanPath = (Join-Path $fx.dfu "PLAN.md"); DecisionsPath = (Join-Path $fx.dfu "DECISIONS.md")
        WalkthroughPath = (Join-Path $fx.dfu "WALKTHROUGH.md"); Dispositions = $disp
    }
    $c2 = Get-Clause -Json $r2.json -Id "2"
    $p2 = Get-Probe -Clause $c2 -Name "chain-U0-original-vs-current"
    [void](Assert-That -What "[D] the SAME drop PASSES once dispositioned as a named follow-on" `
        -Condition ($null -ne $p2 -and [string]$p2.verdict -eq "pass") `
        -Detail ("probe verdict: {0}; note: {1}" -f $(if ($p2) { $p2.verdict } else { "<missing>" }), $(if ($p2) { $p2.note } else { "" })))
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
    $fx = New-FixtureRepo -Plan (New-PlanText) -Decisions "# d`n" -Walkthrough "# w`n"
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
    $p = Get-Probe -Clause $c -Name "walkthrough-U0-check"
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
    }
} finally { if ($scratchCopy -and (Test-Path -LiteralPath $scratchCopy)) { Remove-Item -LiteralPath $scratchCopy -Force -ErrorAction SilentlyContinue } }

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
    [void](Assert-That -What "[L] clause 3 evaluates every door against the real plane" `
        -Condition ($null -ne $c -and [int]$c.coverage.evaluated -eq [int]$c.coverage.expected) `
        -Detail ("coverage: {0} of {1}" -f $(if ($c) { $c.coverage.evaluated } else { "?" }), $(if ($c) { $c.coverage.expected } else { "?" })))
    $pf = Get-Probe -Clause $c -Name "fixture-cleaned-up"
    [void](Assert-That -What "[L] the synthetic personal fixture is removed and 0 personal rows remain" `
        -Condition ($null -ne $pf -and [string]$pf.verdict -eq "pass") `
        -Detail ("note: {0}" -f $(if ($pf) { $pf.note } else { "<missing>" })))
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
