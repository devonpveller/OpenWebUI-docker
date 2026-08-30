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

function Test-IsWorktreeRoot {
    # Is this path the ROOT of a working tree, rather than merely a directory that happens
    # to sit inside one?
    #
    # WHY THIS IS A FACT WORTH OWNING (2026-08-30). `git -C <dir>` chdirs and then ASCENDS
    # to the nearest enclosing repository. So when a worktree failed to provision - or a
    # previous run left a plain directory behind at .claude/worktrees/wt-x - every
    # subsequent `git -C <that path> ...` silently operates on the MAIN CHECKOUT instead of
    # on nothing. verify-merge-protocol.ps1 hit exactly this: its `git -C $wtB rebase` ran
    # in the operator's checkout and left it mid-rebase (.git/rebase-merge, head-name
    # refs/heads/refactor/ai-stack-cleanup). Git does not consider that an error, so there
    # is no exit code to trust; the containment has to be an explicit assertion before the
    # first mutating call, which is what this function is for.
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $false }
    $top = (Invoke-GitCapture @("-C", $Path, "rev-parse", "--show-toplevel") | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $top) { return $false }
    # Compare resolved full paths: git answers with forward slashes, callers pass Windows
    # separators, and either side may be a substituted or differently-cased drive path.
    $a = [System.IO.Path]::GetFullPath((Resolve-Path -LiteralPath $Path).Path)
    $b = [System.IO.Path]::GetFullPath($top.Trim().Replace("/", [string][char]92))
    return ($a.TrimEnd([char]92) -eq $b.TrimEnd([char]92))
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
