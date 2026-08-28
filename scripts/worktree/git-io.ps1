# git-io.ps1 - the ONLY place this toolkit knows how to talk to git.
#
# Single responsibility: adapt a native command to PowerShell safely, and answer questions
# about repository topology. It holds no policy - it will tell you which branch is checked
# out where, never which branch you ought to use. `resolve.ps1` owns that decision, and
# depends on this; nothing here depends on resolve.ps1 (the dependency points one way).
#
# Isolating this is not ceremony: every caller that reimplemented "run git and read the
# output" reimplemented the same PS5.1 trap with it (see Invoke-GitCapture).

function Invoke-GitCapture {
    # Capturing a native command's output under $ErrorActionPreference='Stop' makes git's
    # ordinary stderr (progress, warnings) a TERMINATING error. Callers keep 'Stop' for
    # their cmdlets; this flips it only around the native call. Trust $LASTEXITCODE - it is
    # the only honest success signal for a native command.
    param([string[]]$GitArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& git @GitArgs) } finally { $ErrorActionPreference = $prevEap }
}

function Get-GitCommonDir {
    # The directory every worktree of one repository shares. The anchor for anything that
    # must be per-REPOSITORY rather than per-checkout.
    $out = Invoke-GitCapture @("rev-parse", "--path-format=absolute", "--git-common-dir")
    if ($LASTEXITCODE -ne 0 -or -not $out) { return "" }
    return ($out | Select-Object -First 1)
}

function Get-MainCheckout {
    # The primary working tree - the operator's checkout. The common git dir sits inside it.
    $common = Get-GitCommonDir
    if (-not $common) { return "" }
    return (Split-Path -Parent $common)
}

function Get-CurrentBranch {
    param([string]$RepoPath = "")
    $gitArgs = if ($RepoPath) { @("-C", $RepoPath, "rev-parse", "--abbrev-ref", "HEAD") }
               else { @("rev-parse", "--abbrev-ref", "HEAD") }
    $branch = (Invoke-GitCapture $gitArgs | Select-Object -First 1)
    if (-not $branch) { return "" }
    $branch = $branch.Trim()
    # "HEAD" means detached - a state, not a branch name. Callers must not treat it as one.
    if ($branch -eq "HEAD") { return "" }
    return $branch
}

function Get-WorktreeHoldingBranch {
    # Which worktree, if any, has this branch checked out. A branch checked out anywhere
    # CANNOT be a merge target: git refuses a second checkout, and force-moving the ref
    # would leave that tree's index describing content it no longer has.
    param([string]$Branch)
    $rows = Invoke-GitCapture @("worktree", "list", "--porcelain")
    $current = ""
    foreach ($row in $rows) {
        if ($row -like "worktree *") { $current = $row.Substring(9) }
        if ($row -eq "branch refs/heads/$Branch") { return $current }
    }
    return ""
}
