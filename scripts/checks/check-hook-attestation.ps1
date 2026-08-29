# check-hook-attestation.ps1 - did the pre-commit hooks actually run for these commits?
#
# WHY THIS EXISTS (PLAN 0 A7, dark-factory-unification U5). The audit verdict:
# "Cloud/worktree agents can be governed normatively" - FALSIFIED. An agent reached for
# `--no-verify` on its first commit, and `--no-verify` leaves NO trace in a git object,
# so "the hooks ran" was unprovable after the fact. A rule in a protocol document did not
# stop it; only a mechanism can. The hook now leaves proof (.githooks/pre-commit step 6),
# and this reads it back.
#
# WHAT IT PROVES, AND WHAT IT DOES NOT. An attested tree means the checks passed for that
# exact content. An UNATTESTED tree means one of:
#   - the commit was made with --no-verify (the case this exists for);
#   - it was made before attestation shipped, or in a clone without core.hooksPath set;
#   - a rebase/merge produced NEW content that no hook ever saw (conflict resolution).
# It cannot tell those apart, and it does not pretend to - it reports what is unattested
# and lets the caller decide. Being honest about that is what keeps it trustworthy.
#
# MERGE COMMITS ARE EXEMPT, deliberately. `git merge` runs pre-merge-commit, never
# pre-commit, so a merge tree is never attested. Requiring it would be a permanent false
# positive on the reviewer's own workflow - and a guard that cries wolf gets disabled,
# which is strictly worse than no guard.
#
# Usage:
#   .\check-hook-attestation.ps1 -Branch work/foo -Base refactor/ai-stack-cleanup
#   .\check-hook-attestation.ps1 -Branch work/foo -Base development -Json
#
# Exit: 0 = every non-merge commit attested | 1 = one or more unattested | 2 = usage/env

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [Parameter(Mandatory = $true)][string]$Base,
    [string]$RepoRoot = "",
    [switch]$Json
)

$ErrorActionPreference = "Stop"

function Invoke-GitLines {
    # PS 5.1 trap: with $ErrorActionPreference='Stop', a native command writing to stderr
    # raises NativeCommandError even on exit 0. Flip the preference around the call - the
    # house pattern (see scripts/agent-harness/git-io.ps1).
    param([string[]]$GitArgs)
    # -C rather than a working-directory change, so this script never mutates its caller's
    # location (see the note below the param block).
    if ($script:GitDir) { $GitArgs = @("-C", $script:GitDir) + $GitArgs }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return @(& git @GitArgs 2>$null) } finally { $ErrorActionPreference = $prev }
}
$script:GitDir = $RepoRoot

# NO Set-Location ANYWHERE in this script. PowerShell's current location is SESSION-scoped,
# so changing it here silently relocates whoever called us - which is not theoretical: an
# earlier draft did exactly that and broke an unrelated assertion in
# verify-merge-protocol.ps1 ("operator checkout still on its own branch"), because every
# later `git` in that run executed somewhere else. -RepoRoot is threaded into git via -C
# instead (see Invoke-GitLines), which cannot leak.

$common = (Invoke-GitLines @("rev-parse", "--git-common-dir") | Select-Object -First 1)
if (-not $common) {
    Write-Host "not a git repository (or git unavailable)" -ForegroundColor Red
    exit 2
}
if (-not [System.IO.Path]::IsPathRooted($common)) {
    # Relative to the repo we asked, not to wherever the caller happens to be standing.
    #
    # NOT named $base. PowerShell variable names are CASE-INSENSITIVE, so `$base` IS the
    # `$Base` parameter - an earlier version of this line silently overwrote the base ref
    # with a filesystem path, `rev-list "<path>..<branch>"` then matched nothing, and the
    # check reported "0 commits, all clean" on every branch. It only fired in the MAIN
    # checkout, where --git-common-dir returns a relative ".git"; worktrees return an
    # absolute path and skipped this block entirely, so every test passed. A guard that
    # vacuously passes is worse than no guard, and this one very nearly shipped.
    $commonRoot = if ($RepoRoot) { $RepoRoot } else { (Get-Location).Path }
    $common = Join-Path $commonRoot $common
}
$ledgerPath = Join-Path $common "hook-attest.log"
# One override, for the drill only: it needs to drive this against a controlled ledger
# (attested / unattested / absent) without touching the machine's real one. Deliberately a
# single named variable rather than a general "point anything anywhere" scheme - a knob
# nobody can enumerate is a knob that silently disables a guard.
if ($env:AI_STACK_ATTEST_LEDGER) { $ledgerPath = $env:AI_STACK_ATTEST_LEDGER }

# The ledger is a set of attested tree hashes. It is append-only and one line per
# validated tree; duplicates are expected (the same content committed twice) and harmless.
$attested = @{}
$activatedAt = $null
if (Test-Path $ledgerPath) {
    foreach ($line in (Get-Content -Path $ledgerPath -ErrorAction SilentlyContinue)) {
        $parts = $line -split '\s+'
        $t = $parts[0]
        if ($t) { $attested[$t] = $true }
        # The EARLIEST entry is when attestation started existing on this machine.
        if ($parts.Count -ge 2) {
            # MinValue, not $null: PS 5.1 resolves the [ref] overload from the variable's
            # CURRENT type, and an untyped $null makes TryParse unresolvable at runtime.
            $stamp = [datetime]::MinValue
            if ([datetime]::TryParse($parts[1], [ref]$stamp)) {
                $utc = $stamp.ToUniversalTime()
                if ($null -eq $activatedAt -or $utc -lt $activatedAt) { $activatedAt = $utc }
            }
        }
    }
}

# THE GUARD ACTIVATES PER BRANCH, from the branch's OWN hook.
#
# A branch whose .githooks/pre-commit does not write attestations CANNOT produce them, so
# demanding them would fail every honest commit on it. That is not hypothetical: it is what
# happened the first time this ran - the merge-protocol drill creates its worktrees from the
# main checkout, so they carry the MERGED hook, and the guard flagged the drill's own honest
# work. A guard that fires on correct behaviour is worse than none, because it teaches people
# the alarm is noise.
#
# Reading the hook out of the BRANCH (not the working tree) also means this cannot be
# defeated by editing the file locally without committing it.
$branchHook = (Invoke-GitLines @("show", "${Branch}:.githooks/pre-commit")) -join "`n"
if ($branchHook -notmatch 'hook-attest\.log') {
    if ($Json) {
        [pscustomobject]@{ branch = $Branch; base = $Base; checked = 0; ledger = $ledgerPath
                           ledgerFound = (Test-Path $ledgerPath); inactive = $true
                           reason = "branch hook does not attest"; unattested = @() } |
            ConvertTo-Json -Depth 5 -Compress
    } else {
        Write-Host "Hook attestation: INACTIVE for '$Branch'."
        Write-Host "  That branch's .githooks/pre-commit does not record attestations, so its"
        Write-Host "  commits cannot have them. Nothing was bypassed - the mechanism is simply"
        Write-Host "  not present on this branch yet. It self-activates once the attesting hook"
        Write-Host "  is merged into the line these branches are cut from."
    }
    exit 0
}

# NO LEDGER = THE CHECK IS NOT ACTIVE YET, and it must say so rather than failing everything.
# A guard whose first act is to block every branch in the repo gets reverted within the hour,
# and deservedly - nothing was bypassed, the mechanism simply did not exist yet.
if (-not (Test-Path $ledgerPath)) {
    if ($Json) {
        [pscustomobject]@{ branch = $Branch; base = $Base; checked = 0; ledger = $ledgerPath
                           ledgerFound = $false; inactive = $true; unattested = @() } |
            ConvertTo-Json -Depth 5 -Compress
    } else {
        Write-Host "Hook attestation: INACTIVE - no ledger at $ledgerPath."
        Write-Host "  Nothing has been attested on this machine yet, so nothing can be judged."
        Write-Host "  It starts recording on the next commit that runs .githooks/pre-commit."
    }
    exit 0
}

# Commits on the branch that are NOT on the base - i.e. the work being submitted.
$revs = Invoke-GitLines @("rev-list", "--no-merges", "$Base..$Branch")
$unattested = @()
$checked = 0
$exempt = 0
foreach ($sha in $revs) {
    if (-not $sha) { continue }
    $checked++
    $tree = (Invoke-GitLines @("rev-parse", "$sha^{tree}") | Select-Object -First 1)
    if ($tree -and $attested.ContainsKey($tree)) { continue }

    # PRE-EXISTING HISTORY IS EXEMPT. A commit made before attestation existed cannot
    # possibly have an entry, and blocking it would punish people for the mechanism's own
    # start date. The boundary is the ledger's earliest entry - honest, and it needs no
    # separate state file to go stale.
    if ($activatedAt) {
        $cd = (Invoke-GitLines @("log", "-1", "--format=%cI", $sha) | Select-Object -First 1)
        $when = [datetime]::MinValue      # see the MinValue note above
        if ($cd -and [datetime]::TryParse($cd, [ref]$when)) {
            if ($when.ToUniversalTime() -lt $activatedAt) { $exempt++; continue }
        }
    }

    $subject = (Invoke-GitLines @("log", "-1", "--format=%s", $sha) | Select-Object -First 1)
    $unattested += [pscustomobject]@{ Sha = $sha; Tree = $tree; Subject = $subject }
}

if ($Json) {
    [pscustomobject]@{
        branch      = $Branch
        base        = $Base
        checked     = $checked
        ledger      = $ledgerPath
        ledgerFound = (Test-Path $ledgerPath)
        unattested  = @($unattested | ForEach-Object { @{ sha = $_.Sha; tree = $_.Tree; subject = $_.Subject } })
    } | ConvertTo-Json -Depth 5 -Compress
    exit ($(if ($unattested.Count) { 1 } else { 0 }))
}

Write-Host ("Hook attestation: {0} non-merge commit(s) on {1} not in {2}" -f $checked, $Branch, $Base)
Write-Host ("  ledger: {0}{1}" -f $ledgerPath, $(if (Test-Path $ledgerPath) { "" } else { "  (NOT FOUND)" }))
if ($unattested.Count -eq 0) {
    Write-Host "  [OK] every commit's tree was validated by the pre-commit hooks." -ForegroundColor Green
    exit 0
}

Write-Host ""
Write-Host ("  [UNATTESTED] {0} commit(s) - the pre-commit hooks did not validate this content:" -f $unattested.Count) -ForegroundColor Red
foreach ($u in $unattested) {
    Write-Host ("    {0}  {1}" -f $u.Sha.Substring(0, 8), $u.Subject) -ForegroundColor Red
}
Write-Host ""
Write-Host "  The usual cause is `git commit --no-verify`. Never use it: the checks it skips"
Write-Host "  are the secret guard, the line-ending rule, the LLM-gateway routing rule and"
Write-Host "  the compose/ps1 structural check - each of which exists because it caught a"
Write-Host "  real failure. THE REMEDY is to re-commit the same content so the hooks run:"
Write-Host ""
Write-Host "    git commit --amend --no-edit          # for the tip commit"
Write-Host "    git rebase --exec 'git commit --amend --no-edit' $Base   # for several"
Write-Host ""
Write-Host "  If these commits predate attestation, or came from a clone without"
Write-Host "  core.hooksPath set, say so explicitly in your submission rather than"
Write-Host "  re-writing history you did not author."
exit 1
