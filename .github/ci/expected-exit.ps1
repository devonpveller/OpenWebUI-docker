# expected-exit.ps1 - THE EXIT-CODE CONTRACT CI READS, WRITTEN DOWN ONCE.
#
# dark-factory-unification PLAN.md C.9 H4 wires the verification machinery into CI so a
# later refactor cannot break one of these checks silently. Wiring is NOT just
# `- run: ./drill.ps1`, because NOT ALL OF THESE CHECKS RETURN 0 WHEN THEY ARE WORKING:
#
#   * drill-personal-plane-exclusion.ps1 exits 2 with dispositioned gaps open and 0 only
#     under -AcceptDispositionedGaps; a NEW gap is 2 even with the flag, and a
#     dispositioned gap that stopped being measured is 1.
#   * drill-rls-boot-assertion.ps1 (and assert-rls-force.sh, which it drills) reserve
#     exit 3 for CANNOT-CHECK. "the runner could not run the drill" and "the boundary is
#     broken" are different sentences; H2 spent three rounds separating them, and a build
#     that prints the second when the first is true throws that work away.
#   * drill-app-role-not-superuser.ps1 exits 2 = CANNOT MEASURE while H1's two migration
#     files are absent from the recorded OB1 gitlink. That is the state of the tree today.
#   * dfu-done.ps1 exits 7 while the board is FAILED, and its own header says so: "A
#     non-zero exit naming unmet clauses is this script WORKING." Exit 7 is not a broken
#     build; exit 1 (usage or configuration error - nothing was judged) is.
#
# So "did this check behave?" is a per-check CONTRACT, and it lives here rather than in
# eight `if ($LASTEXITCODE -ne 0)` lines across the workflow that drift apart.
#
# THE THREE RULES, borrowed rather than reinvented - each is already earned somewhere in
# this repo, and a second home-grown verdict function would be one more place for the same
# defect to live (dfu-done.ps1's opening argument, applied to itself):
#
#   1. NEVER DEFAULT TO PASS (dfu-done.ps1 rule 1). A check name with no pinned contract,
#      or an exit code the contract does not enumerate, is RED. Green is decided
#      positively; it is never what you get because nothing objected.
#
#   2. A PIN THAT ROTS IS A NAG, NOT A FAILURE (PROMOTION-RUNBOOK.md, "The drill's exit
#      code, and what CI reads", CORRECTED ROUND 4). When a check pinned as expected-
#      non-zero starts returning 0, the thing it was blocked on got FIXED. Turning the
#      build red for that teaches people to stop fixing things. It is a loud warning that
#      names the pin to pull, and it is worth zero failures.
#
#   3. CANNOT-CHECK IS REPORTED AS CANNOT-CHECK. A blocked run fails the build - CI could
#      not do its job, and a silent green there is the vacuity defect this whole effort
#      exists to stop - but the message says the drill never reached the boundary. It
#      never says the boundary is broken.
#
# Usage:
#   .\expected-exit.ps1 -Check dfu-done -Code 7 -Command "dfu-done.ps1 -SkipLive"
#   .\expected-exit.ps1 -List        # print the whole contract
#   .\expected-exit.ps1 -SelfTest    # force the classifier through every outcome
#
# Exit: 0 the check behaved as pinned (green, or green-with-a-nag). 1 it did not, or this
#       file has no contract for it. There is no third value and no override switch.

[CmdletBinding()]
param(
    [string]$Check = "",
    [int]$Code = -1,
    [string]$Command = "",
    [switch]$List,
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# =========================================================================================
# THE CONTRACT. One entry per wired check.
#
#   Green   exit codes that mean "behaved as pinned" -> build stays green
#   Nag     exit codes that mean "BETTER than pinned" -> green, plus PULL THIS PIN
#   Meaning what each code this file knows about actually means, so the annotation for a
#           red never has to guess (and never mislabels a CANNOT-CHECK as a failure)
#   Doc     where a human reads the contract in prose
#
# Anything not in Green or Nag is RED. Adding a check means adding an entry; until then the
# classifier refuses it by name rather than guessing a convention.
#
# EVERY GREEN AND NAG CODE BELOW WAS MEASURED, not inferred - see the H4 evidence table in
# documentation/notes/u8h4-findings.md for the run, the elapsed time and the tree sha.
# =========================================================================================
$script:Contract = [ordered]@{

    "boundary-drill" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "containment green and every gap that fired is dispositioned in the drill's own GAP LEDGER"
            1 = "FAILURE - a drill check failed, or a dispositioned gap stopped being measured (a rotted ledger)"
            2 = "an UNDISPOSITIONED gap fired. Either the tree regressed or a new property went unmet; name it in GAP_DISPOSITIONS before this run can be read as expected"
        }
        Doc     = "documentation/implementation-guide/agent-memory-plane/PROMOTION-RUNBOOK.md, 'The drill's exit code, and what CI reads (C.9 H4)'"
    }

    "boundary-drill-selftest-ledger" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "a CLOSED gap contributes 0 failures; a VANISHED one still fails; every dispositioned id can reach CLOSED"
            1 = "the ledger reconciliation no longer holds - closing a gap could now turn the build red"
        }
        Doc     = "scripts/checks/drill-personal-plane-exclusion.ps1 -SelfTestLedger"
    }

    "boundary-drill-selftest-vacuity" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "an empty universe cannot reach PASS"
            1 = "the vacuity guard can pass off an empty universe again"
        }
        Doc     = "scripts/checks/drill-personal-plane-exclusion.ps1 -SelfTestVacuity"
    }

    "rls-boot-drill" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "every scenario behaved as required - the boot assertion fires on each way the boundary can be absent"
            1 = "FAILURE - at least one scenario did not. This is a statement ABOUT THE BOUNDARY."
            3 = "CANNOT-CHECK - the drill could not build the throwaway environment it needs (docker run failed, the database never came up). The boundary was NEVER EXERCISED, so nothing here says it is absent."
        }
        Doc     = "scripts/checks/drill-rls-boot-assertion.ps1 header, 'EXIT CODES - a timeout is NOT a finding'"
    }

    # THE ONE DISPOSITIONED BLOCK, pinned BY NAME with the condition that lifts it - the
    # same shape as the boundary drill's gap ledger, for the same reason. Measured
    # 2026-09-01 at gitlink b604d55: OB1/docker/init-app-role.sql and
    # init-app-role-passwords.sh do not exist at the recorded commit, so the drill aborts
    # in 1 second with "CANNOT MEASURE - the migration under test is not in this checkout".
    # It lifts when H1's OB1 commit is pushed and the gitlink is bumped to it; the drill
    # then runs, and 0 arrives here as a NAG telling whoever sees it to pull this pin.
    "app-role-drill" = @{
        Green   = @(2)
        Nag     = @{
            0 = "the drill RAN and every probe passed - H1's migration is now in the recorded gitlink. PULL THIS PIN: move 0 into Green, drop 2, and record the change in PROMOTION-RUNBOOK.md."
        }
        Meaning = @{
            0 = "every probe as expected"
            1 = "a probe disagreed - the app role is not bound the way H1 claims"
            2 = "CANNOT MEASURE - the harness could not run (image pull, initdb failure), or H1's migration files are absent from the recorded OB1 gitlink. EXPECTED TODAY."
        }
        Doc     = "scripts/checks/drill-app-role-not-superuser.ps1 header; DFU PLAN.md C.9 H1"
    }

    "prove-rls" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "every green had a red beside it and both agreed"
            1 = "a check failed, OR the run aborted. NOTE: this script has no cannot-check code - a docker failure lands in its catch and reports as 1. Recorded in documentation/notes/u8h4-findings.md."
        }
        Doc     = "scripts/checks/prove-agent-memory-rls.ps1"
    }

    "corpus-exposure-producers" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "every RECOGNISED corpus insert site states its plane"
            1 = "a recognised insert site does not state its plane"
        }
        Doc     = "scripts/checks/check-corpus-exposure-producers.ps1"
    }

    "corpus-exposure-producers-selftest" = @{
        Green   = @(0)
        Nag     = @{}
        Meaning = @{
            0 = "the gate's own planted cases classify as recorded, misses included"
            1 = "a planted case no longer classifies as recorded"
        }
        Doc     = "scripts/checks/check-corpus-exposure-producers.ps1 -SelfTest"
    }

    # 7 IS THE PINNED STATE, AND IT IS THE SCRIPT WORKING. C.8 forbids amending a plan
    # column to make this go green, so CI must not treat the honest report as a broken
    # build either. What CI DOES assert about this run is checked separately, by the
    # census assertions in the workflow (the census balances, and no clause lands in the
    # unrecognised bucket) - properties that hold on any platform.
    "dfu-done" = @{
        Green   = @(7)
        Nag     = @{
            0 = "every clause MET. The board is DONE. PULL THIS PIN: move 0 into Green, drop 7, and hand over (PLAN.md C.10, 'THE STOP IS REAL')."
        }
        Meaning = @{
            0 = "every clause MET"
            1 = "usage or configuration error - NOTHING WAS JUDGED. This is a red build: the board did not run."
            7 = "the plan is NOT met, and the run says which clauses. EXPECTED TODAY."
        }
        Doc     = "scripts/checks/dfu-done.ps1 header, 'Exit codes'; DFU PLAN.md C.8"
    }
}

# =========================================================================================
# OUTPUT. GitHub annotations when running under Actions, plain text otherwise, and a row in
# the job summary either way so a reader sees the contract beside the result.
# =========================================================================================
function Write-Annotation {
    param([ValidateSet("error", "warning", "notice")][string]$Level, [string]$Message)
    $flat = ($Message -replace "`r", "" -replace "`n", " ")
    if ($env:GITHUB_ACTIONS -eq "true") { Write-Host "::${Level}::${flat}" }
    Write-Host ("[{0}] {1}" -f $Level.ToUpper(), $flat)
}

function Write-Summary {
    param([string]$Line)
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value $Line
    }
}

function Show-Contract {
    Write-Host "EXIT-CODE CONTRACT (DFU C.9 H4)"
    foreach ($name in $script:Contract.Keys) {
        $e = $script:Contract[$name]
        Write-Host ""
        Write-Host ("  {0}" -f $name)
        Write-Host ("    green : {0}" -f (($e.Green | Sort-Object) -join ", "))
        $nagKeys = @($e.Nag.Keys | Sort-Object)
        Write-Host ("    nag   : {0}" -f $(if ($nagKeys.Count -gt 0) { $nagKeys -join ", " } else { "(none)" }))
        Write-Host ("    doc   : {0}" -f $e.Doc)
        foreach ($k in ($e.Meaning.Keys | Sort-Object)) {
            Write-Host ("      {0} = {1}" -f $k, $e.Meaning[$k])
        }
    }
}

# THE CLASSIFIER, PURE. It returns a word and never writes or exits, so -SelfTest can force
# it through every outcome without a runner, a docker daemon or a database. The exit block
# at the bottom is the only place that turns a word into a build outcome.
function Get-Outcome {
    param([string]$CheckName, [int]$ExitCode)
    if (-not $script:Contract.Contains($CheckName)) { return "no-contract" }
    $e = $script:Contract[$CheckName]
    if ($e.Green -contains $ExitCode) { return "green" }
    if ($e.Nag.Contains($ExitCode))   { return "nag" }
    return "red"
}

function Get-Meaning {
    param([string]$CheckName, [int]$ExitCode)
    if (-not $script:Contract.Contains($CheckName)) { return "" }
    $m = $script:Contract[$CheckName].Meaning
    if ($m.Contains($ExitCode)) { return [string]$m[$ExitCode] }
    return ""
}

# =========================================================================================
# THE CLASSIFIER'S OWN RED. Every outcome, including the two that must REFUSE, forced with
# no runner and no docker. A verdict function nobody exercised is one more place for the
# defect rule 1 exists to stop.
# =========================================================================================
if ($SelfTest) {
    $cases = @(
        @{ Check = "dfu-done";       Code = 7;  Want = "green";       Why = "the pinned FAILED board is the expected state today, not a broken build" },
        @{ Check = "dfu-done";       Code = 0;  Want = "nag";         Why = "the board went DONE - better than pinned, so a nag and not a failure" },
        @{ Check = "dfu-done";       Code = 1;  Want = "red";         Why = "usage or configuration error - nothing was judged" },
        @{ Check = "dfu-done";       Code = 99; Want = "red";         Why = "an exit code the contract does not enumerate must never pass" },
        @{ Check = "app-role-drill"; Code = 2;  Want = "green";       Why = "CANNOT MEASURE is the dispositioned state while H1's migration is not at the gitlink" },
        @{ Check = "app-role-drill"; Code = 0;  Want = "nag";         Why = "the drill started running - PULL THE PIN" },
        @{ Check = "app-role-drill"; Code = 1;  Want = "red";         Why = "a probe disagreed" },
        @{ Check = "rls-boot-drill"; Code = 0;  Want = "green";       Why = "every scenario behaved" },
        @{ Check = "rls-boot-drill"; Code = 3;  Want = "red";         Why = "CANNOT-CHECK fails the build, and the annotation says the drill never reached the boundary" },
        @{ Check = "rls-boot-drill"; Code = 1;  Want = "red";         Why = "a real boundary failure" },
        @{ Check = "boundary-drill"; Code = 0;  Want = "green";       Why = "containment green, every gap dispositioned" },
        @{ Check = "boundary-drill"; Code = 2;  Want = "red";         Why = "an undispositioned gap under -AcceptDispositionedGaps is the regression the wiring exists to catch" },
        @{ Check = "boundary-drill"; Code = 1;  Want = "red";         Why = "the ledger rotted" },
        @{ Check = "no-such-check";  Code = 0;  Want = "no-contract"; Why = "a check with no pinned contract REFUSES - exit 0 must not buy a pass by default" }
    )
    $bad = 0
    Write-Host "=== -SelfTest: the classifier forced through every outcome ==="
    foreach ($c in $cases) {
        $got = Get-Outcome -CheckName $c.Check -ExitCode $c.Code
        if ($got -eq $c.Want) {
            Write-Host ("  OK   {0,-22} code {1,-3} -> {2,-11} ({3})" -f $c.Check, $c.Code, $got, $c.Why)
        } else {
            Write-Host ("  BAD  {0,-22} code {1,-3} -> {2}, wanted {3}" -f $c.Check, $c.Code, $got, $c.Want)
            $bad++
        }
    }
    # STRUCTURAL PROPERTIES OF THE TABLE ITSELF. A green or nag code with no documented
    # meaning would force an annotation to invent one, which is how a CANNOT-CHECK gets
    # printed as a boundary failure; a check with no green code could never pass at all.
    foreach ($name in $script:Contract.Keys) {
        $e = $script:Contract[$name]
        foreach ($k in $e.Meaning.Keys) {
            if ([string]::IsNullOrWhiteSpace([string]$e.Meaning[$k])) {
                Write-Host ("  BAD  {0}: code {1} has an empty meaning" -f $name, $k); $bad++
            }
        }
        foreach ($g in $e.Green) {
            if (-not $e.Meaning.Contains($g)) {
                Write-Host ("  BAD  {0}: green code {1} has no documented meaning" -f $name, $g); $bad++
            }
        }
        foreach ($n in $e.Nag.Keys) {
            if (-not $e.Meaning.Contains($n)) {
                Write-Host ("  BAD  {0}: nag code {1} has no documented meaning" -f $name, $n); $bad++
            }
            if ($e.Green -contains $n) {
                Write-Host ("  BAD  {0}: code {1} is both green and nag" -f $name, $n); $bad++
            }
        }
        if (@($e.Green).Count -eq 0) {
            Write-Host ("  BAD  {0}: no green code at all - this check could never pass" -f $name); $bad++
        }
    }
    if ($bad -eq 0) {
        Write-Host "EXPECTED-EXIT SELF-TEST PASSED - green, nag, red and no-contract all reachable, and no code passes by default."
        exit 0
    }
    Write-Host "EXPECTED-EXIT SELF-TEST FAILED - $bad problem(s)"
    exit 1
}

if ($List) { Show-Contract; exit 0 }

if ([string]::IsNullOrWhiteSpace($Check)) {
    Write-Annotation -Level error -Message "expected-exit.ps1: -Check is required (or pass -List / -SelfTest). Nothing was classified."
    exit 1
}
if ($Code -lt 0) {
    Write-Annotation -Level error -Message "expected-exit.ps1: -Code is required and must be >= 0. Nothing was classified."
    exit 1
}

$outcome = Get-Outcome -CheckName $Check -ExitCode $Code
$meaning = Get-Meaning -CheckName $Check -ExitCode $Code
$cmdText = $(if ($Command) { $Command } else { "(command not recorded)" })

switch ($outcome) {
    "green" {
        Write-Annotation -Level notice -Message "$Check exited $Code as pinned: $meaning"
        Write-Summary ("| ``$Check`` | $Code | PASS | $meaning |")
        Write-Host "  ran: $cmdText"
        exit 0
    }
    "nag" {
        $why = [string]$script:Contract[$Check].Nag[$Code]
        Write-Annotation -Level warning -Message "$Check exited $Code, which is BETTER than the pinned state. $why"
        Write-Summary ("| ``$Check`` | $Code | PASS (PULL THIS PIN) | $why |")
        Write-Host "  ran: $cmdText"
        Write-Host "  contract: $($script:Contract[$Check].Doc)"
        exit 0
    }
    "no-contract" {
        Write-Annotation -Level error -Message "expected-exit.ps1 has NO CONTRACT for check '$Check'. A check nobody pinned cannot pass by default - add an entry, do not route around this."
        Write-Summary ("| ``$Check`` | $Code | REFUSED - no pinned contract | add an entry to .github/ci/expected-exit.ps1 |")
        exit 1
    }
    default {
        $detail = $(if ($meaning) { $meaning } else { "an exit code this contract does not enumerate. It is NOT a pass: see rule 1." })
        Write-Annotation -Level error -Message "$Check exited $Code. $detail"
        Write-Summary ("| ``$Check`` | $Code | FAIL | $detail |")
        Write-Host "  ran: $cmdText"
        Write-Host "  contract: $($script:Contract[$Check].Doc)"
        Write-Host ("  expected: {0}" -f (($script:Contract[$Check].Green | Sort-Object) -join ", "))
        exit 1
    }
}
