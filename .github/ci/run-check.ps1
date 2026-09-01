# run-check.ps1 - RUN ONE WIRED CHECK, AND PROVE IT RAN BEFORE ANY CODE IS CLASSIFIED.
#
# WHY THIS FILE EXISTS. The first shape of this wiring was, in every step:
#
#     $ErrorActionPreference = 'Continue'
#     & ./scripts/checks/<check>.ps1
#     $c = $LASTEXITCODE
#     & ./.github/ci/expected-exit.ps1 -Check <name> -Code $c
#
# and that shape turns "THE CHECK DID NOT RUN" into a PASS. Two independent ways, both
# MEASURED under pwsh 7.4 on Linux (the runner's shell), not reasoned about:
#
#   1. $LASTEXITCODE IS $null WHEN NOTHING SET IT, AND [int]$null IS 0. expected-exit.ps1
#      declared [int]$Code = -1 and refused anything below zero, but $null never reached that
#      test: the binder had already turned it into 0. Every check whose green set contains 0
#      passed on a run that never happened.
#
#   2. WORSE, AND THE ONE A TYPE FIX ALONE DOES NOT CLOSE: $LASTEXITCODE IS STALE, NOT NULL,
#      WHENEVER THE CHECK RAN ANY NATIVE COMMAND BEFORE IT DIED. Measured: a script that runs
#      `bash -c "exit 0"` and then dies inside its own trap leaves the CALLER holding
#      LASTEXITCODE = 0 and $? = True. There is no in-line test that can tell that apart from
#      a check which deliberately exited 0.
#
# This was not hypothetical. drill-app-role-not-superuser.ps1 cleaned up with
# `cmd /c "docker rm -f ..."`; cmd.exe does not exist on ubuntu-latest, every abort path in
# that drill is `Cleanup; exit 2`, and its trap was itself `Say ...; Cleanup; exit 2` - so the
# failure recursed and the script died without ever reaching an exit statement. The first real
# CI run would have reported "the drill RAN and every probe passed ... PULL THIS PIN" and the
# job would have been GREEN. H4's whole premise is that a later refactor breaks one of these
# silently; the wiring was the thing making it silent.
#
# WHAT THIS FILE DOES INSTEAD. "Did not run" is made UNREPRESENTABLE as success, by structure
# rather than by enumerating the ways a script can die - the next shape of "never reached
# exit" has to fail too, and nobody will have thought of it here:
#
#   A. THE CHECK MUST EXIST, as a file, before anything is launched.
#   B. THE CHECK RUNS IN A CHILD PROCESS of this script (this same file, re-entered with
#      -ShimMode). A check that takes its host down cannot take the classifier with it, and
#      the child's exit code belongs to that process - it is never $null and never inherited.
#   C. THE CHILD POISONS $LASTEXITCODE WITH A RESERVED SENTINEL before invoking the check, so
#      an absent code cannot look like a real one.
#   D. THE CHILD CARRIES A TOP-LEVEL trap. Any terminating error that escapes the check - a
#      missing file, a command that does not exist on this platform, a cleanup that threw
#      inside the check's own trap - lands there and is recorded as DID-NOT-RUN. Measured
#      against exactly the cmd-on-Linux shape above.
#   E. THE CHILD MUST LEAVE A COMPLETION MARKER, and this script REQUIRES it. No marker, a
#      marker saying DID-NOT-RUN, a marker holding a non-integer, or the sentinel, are all
#      RED with the words "the check did not run". A code is classified only when the marker
#      says the child came back from the call carrying one.
#   F. THE CODE IS HANDED ON AS A STRING. expected-exit.ps1 validates it as text and refuses
#      an empty one; there is no cast that can turn an absence into a number on the way.
#
# THE ONE THING THIS CHANGES ABOUT HOW A CHECK RUNS, SAID OUT LOUD BECAUSE IT IS A REAL
# CHANGE AND NOT A WRAPPER DETAIL. The shim's top-level trap is what detects "died without
# reaching an exit", and a trap ANYWHERE ON THE CALL STACK makes a statement-terminating
# error unwind to it instead of being written and stepped over. Measured, both directions,
# pwsh 7.4/Linux, with a child script running under $ErrorActionPreference = "Continue":
#
#   Copy-Item on a missing path   NOT affected - non-terminating, written and stepped over
#   Get-Content on a missing path NOT affected - same
#   a native command exiting 3    NOT affected - not an error at all
#   1/0                           WITHOUT a trap the script carried on; WITH one it unwinds
#   [int]::Parse("nope")          same
#
# So a check that throws an unhandled terminating exception halfway and then carries on to
# print a verdict now reports DID NOT RUN instead of that verdict. That is deliberate. It is
# the same defect drill-app-role-not-superuser.ps1's own comments already name - "Exit 2 by
# luck rather than by check" - and a verdict from a half-executed run is exactly what H4
# exists to stop CI reading as green. It is also the SAFE direction: the failure mode this
# introduces is a red that should have been green, never the reverse.
#
# WHAT IT STILL CANNOT TELL YOU, said here rather than discovered later (dfu-done rule 2):
# a check that RUNS ITS LAST LINE AND FALLS OFF THE END, having already run a native command
# that succeeded, reports that command's code. The sentinel catches the fall-off when nothing
# native ran; it cannot catch this one, because in-process PowerShell gives the caller no
# signal that distinguishes `exit 0` from falling off the end (measured - $LASTEXITCODE is
# the stale 0 and $? is True in both). Every wired check today ends at an explicit `exit` on
# every terminal branch, verified 2026-09-01 with the tails recorded in
# documentation/notes/u8h4-findings.md. A refactor that removes one is the residual.
#
# Usage (this is the ONLY shape a wired step should use):
#   ./.github/ci/run-check.ps1 -Check <contract-name> -Script <path> [-ScriptArgs a,b]
#                              [-Command "<text for the annotation>"]
#                              [-Stdout <file>]      tee the check's stdout to a file
#                              [-CodeFile <file>]    write the code and DO NOT classify, for
#                                                    a step that must assert something about
#                                                    the run before the contract is consulted
#
# Exit: 0 the check behaved as pinned (green, or green-with-a-nag). 1 anything else,
#       INCLUDING every way of not running. There is no third value.
#       (-ShimMode is internal and exits 250/251; a step must never see those.)

[CmdletBinding()]
param(
    [string]$Check = "",
    [string]$Script = "",
    [string[]]$ScriptArgs = @(),
    [string]$Command = "",
    [string]$Stdout = "",
    [string]$CodeFile = "",
    [switch]$ShimMode
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"

# The reserved code. A wired check may not use it; nothing in the contract enumerates it, so
# even if one did the classifier would call it red.
$SENTINEL = 199

# =========================================================================================
# SHIM MODE - the child. Everything below runs in its own process, and the parent trusts
# nothing it says except the marker file.
# =========================================================================================
if ($ShimMode) {
    $marker = $env:DFU_CHECK_MARKER
    $target = $env:DFU_CHECK_SCRIPT
    $teeTo  = $env:DFU_CHECK_STDOUT
    $argv   = @()
    if ($env:DFU_CHECK_ARGS) { $argv = @($env:DFU_CHECK_ARGS -split "`n" | Where-Object { $_ -ne "" }) }

    # Arguments travel by ENVIRONMENT, not on the command line. A check argument is a switch
    # like -SelfTestLedger, and passing that as `pwsh -File run-check.ps1 -ScriptArgs
    # -SelfTestLedger` makes the CHILD'S OWN parameter binder read it as a parameter name.
    # The environment has no such parse.
    #
    # AND THEY ARE SPLATTED AS A HASHTABLE, NOT AS AN ARRAY. `& $script @("-SelfTest")`
    # splats POSITIONALLY: the string "-SelfTest" lands in the check's first positional
    # parameter instead of setting the switch. Caught by the first end-to-end run of this
    # wrapper, where expected-exit.ps1 -SelfTest arrived as -Check "-SelfTest" and was
    # correctly reported red - the wiring refusing to pass something it had mangled is the
    # behaviour, but the mangling was still a defect.
    $splat = @{}
    foreach ($a in $argv) { $splat[$a.TrimStart("-")] = $true }

    function Write-Marker([string]$Text) {
        # A marker that cannot be written must not become a second failure inside the trap.
        # The parent already treats an ABSENT marker as "did not run", so swallowing here
        # loses nothing and cannot recurse.
        try { Set-Content -LiteralPath $marker -Value $Text -Encoding utf8 -ErrorAction Stop } catch { }
    }

    trap {
        Write-Marker ("DID-NOT-RUN`n" + $_.Exception.Message)
        Write-Host ("RUN-CHECK SHIM: the check terminated WITHOUT reaching an exit statement: " + $_.Exception.Message)
        exit 250
    }

    # C. POISON THE CODE. If the check never reaches an exit and never runs a native command,
    # this is what comes back, and the parent reads it as "did not run".
    $global:LASTEXITCODE = $SENTINEL

    if ($teeTo) {
        & $target @splat | Tee-Object -FilePath $teeTo
    } else {
        & $target @splat
    }
    $c = $LASTEXITCODE
    Write-Marker ("CODE=" + [string]$c)
    exit 251
}

# =========================================================================================
# PARENT MODE.
# =========================================================================================
function Write-DidNotRun([string]$Message) {
    $flat = ($Message -replace "`r", "" -replace "`n", " ")
    if ($env:GITHUB_ACTIONS -eq "true") { Write-Host "::error::$flat" }
    Write-Host "[ERROR] $flat"
    if ($env:GITHUB_STEP_SUMMARY) {
        Add-Content -LiteralPath $env:GITHUB_STEP_SUMMARY -Value ("| ``$Check`` | - | DID NOT RUN | $flat |")
    }
}

if ([string]::IsNullOrWhiteSpace($Check))  { Write-DidNotRun "run-check.ps1: -Check is required."; exit 1 }
if ([string]::IsNullOrWhiteSpace($Script)) { Write-DidNotRun "run-check.ps1: -Script is required."; exit 1 }

$cmdText = if ($Command) { $Command } else { ($Script + " " + ($ScriptArgs -join " ")).Trim() }

# A. THE CHECK MUST EXIST. This is the shape the verifiers used to break the old wiring -
# rename the script away and watch the step pass - so it gets the first and clearest word.
if (-not (Test-Path -LiteralPath $Script -PathType Leaf)) {
    Write-DidNotRun ("$Check DID NOT RUN: there is no file at '$Script'. Nothing was checked, so nothing here says anything about what that check watches. A check that is not there is not a pass.")
    exit 1
}

# THIS WRAPPER PASSES SWITCHES, AND ONLY SWITCHES. Every wired check today is driven by
# bare switches (-SelfTest, -SelfTestLedger, -SelfTestVacuity, -AcceptDispositionedGaps,
# -SkipLive, -Json), and they are splatted as a hashtable so each one SETS ITS SWITCH
# rather than landing in the check's first positional parameter. A valued argument would
# need this wrapper extended; until then it REFUSES rather than guessing how to pass it,
# because a mis-passed argument is a check running a different test than the one named.
foreach ($a in $ScriptArgs) {
    if ($a -notmatch '^-[A-Za-z][A-Za-z0-9]*$') {
        Write-DidNotRun ("$Check DID NOT RUN: run-check.ps1 was given the argument '$a', and it passes bare switches only. Extend this wrapper rather than letting an argument be passed some other way than the check declares it.")
        exit 1
    }
}

# The host shell, whatever it is. Under `shell: pwsh` on a runner this is pwsh; on the
# Windows ops plane it is powershell.exe. Both accept -NoProfile -NonInteractive -File.
$exe = ""
try { $exe = [System.Diagnostics.Process]::GetCurrentProcess().MainModule.FileName } catch { $exe = "" }
if (-not $exe -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) {
    $g = Get-Command pwsh -ErrorAction SilentlyContinue
    if (-not $g) { $g = Get-Command powershell -ErrorAction SilentlyContinue }
    if ($g) { $exe = $g.Source } else { $exe = "" }
}
if (-not $exe) {
    Write-DidNotRun ("$Check DID NOT RUN: this script could not locate the PowerShell host it needs to launch the check in a child process.")
    exit 1
}

$marker = Join-Path ([System.IO.Path]::GetTempPath()) ("run-check-" + [guid]::NewGuid().ToString("N") + ".marker")
Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue

$env:DFU_CHECK_MARKER = $marker
$env:DFU_CHECK_SCRIPT = $Script
$env:DFU_CHECK_STDOUT = $Stdout
$env:DFU_CHECK_ARGS   = ($ScriptArgs -join "`n")

Write-Host ("RUN-CHECK: $Check -> $cmdText")
& $exe -NoProfile -NonInteractive -File $PSCommandPath -ShimMode
$shimCode = $LASTEXITCODE

$env:DFU_CHECK_MARKER = ""
$env:DFU_CHECK_SCRIPT = ""
$env:DFU_CHECK_STDOUT = ""
$env:DFU_CHECK_ARGS   = ""

# E. THE MARKER IS REQUIRED, and it is read BEFORE any exit code: the marker is the only
# thing here that distinguishes "the check came back from the call" from "something ended,
# and this number was lying around".
$markerText = ""
if (Test-Path -LiteralPath $marker -PathType Leaf) {
    $markerText = (Get-Content -LiteralPath $marker -Raw)
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
}
$firstLine = ""
if ($markerText) { $firstLine = ($markerText -split "`r?`n")[0].Trim() }

if (-not $markerText) {
    Write-DidNotRun ("$Check DID NOT RUN: the child process that runs it left no completion record (that process exited $shimCode). Whatever happened, this run reached no verdict about $Check, and an absent verdict is not a passing one. Command: $cmdText")
    exit 1
}
if ($firstLine -eq "DID-NOT-RUN") {
    $why = (($markerText -split "`r?`n") | Select-Object -Skip 1) -join " "
    Write-DidNotRun ("$Check DID NOT RUN: it terminated without reaching an exit statement. $why -- that is a statement about the CHECK HARNESS, not about the thing the check watches: nothing was measured. Command: $cmdText")
    exit 1
}
if ($shimCode -ne 251) {
    Write-DidNotRun ("$Check DID NOT RUN cleanly: the child process exited $shimCode, which is not the value it reports after returning from the check. Command: $cmdText")
    exit 1
}
if ($firstLine -notmatch '^CODE=(.*)$') {
    Write-DidNotRun ("$Check DID NOT RUN: its completion record is not a code ('$firstLine'). Command: $cmdText")
    exit 1
}
$raw = $Matches[1].Trim()
if ([string]::IsNullOrWhiteSpace($raw)) {
    Write-DidNotRun ("$Check DID NOT RUN: it returned NO EXIT CODE AT ALL. An absent PowerShell exit code is `$null and `$null casts to the integer 0, which is why this is tested as TEXT here and refused, instead of being classified as a pass. Command: $cmdText")
    exit 1
}
$n = 0
if (-not [int]::TryParse($raw, [ref]$n)) {
    Write-DidNotRun ("$Check DID NOT RUN: it returned '$raw', which is not an exit code. Command: $cmdText")
    exit 1
}
if ($n -eq $SENTINEL) {
    Write-DidNotRun ("$Check DID NOT RUN: the exit code is still the sentinel this wrapper wrote before invoking it, so the check never reached an exit statement and never ran a command of its own. Command: $cmdText")
    exit 1
}

# F. HAND IT ON AS TEXT.
if ($CodeFile) {
    Set-Content -LiteralPath $CodeFile -Value ([string]$n) -Encoding utf8
    Write-Host ("RUN-CHECK: $Check RAN and reported exit $n; classification deferred to the caller.")
    exit 0
}

$ee = Join-Path $PSScriptRoot "expected-exit.ps1"
if (-not (Test-Path -LiteralPath $ee -PathType Leaf)) {
    Write-DidNotRun ("$Check ran and reported exit $n, but the exit-code contract at '$ee' is not there to say what that means. An unclassified code is not a pass.")
    exit 1
}
& $ee -Check $Check -Code ([string]$n) -Command $cmdText
$eeCode = $LASTEXITCODE
if ($eeCode -ne 0 -and $eeCode -ne 1) {
    Write-DidNotRun ("$Check reported exit $n, but the exit-code contract itself exited $eeCode - it reports only 0 or 1, so this run reached no verdict.")
    exit 1
}
exit $eeCode
