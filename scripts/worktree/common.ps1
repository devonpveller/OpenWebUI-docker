# common.ps1 - shared resolution for the worktree toolkit. Dot-sourced by the other
# scripts; the FOLDER is the unit of portability, not the individual files.
#
# It exists to answer three questions the same way everywhere, because the scripts
# disagreeing about any of them is silent corruption rather than an error:
#
#   Get-SharedStateDir  WHERE coordination state lives (leases, the worktree registry)
#   Resolve-WorkLine    WHICH branch agents start from and land on
#   Invoke-GitCapture   HOW to read a native command's output without PS5.1 lying about it
#
# ---------------------------------------------------------------------------------
# Why Get-SharedStateDir exists (found by the first soak run, before it could bite):
# coordination state was anchored on $PSScriptRoot. That is correct only while exactly
# one copy of this toolkit exists. The moment it is checked out inside a worktree -
# which is the whole point of the toolkit - that copy resolves its OWN state dir, and
# the dir is gitignored so the copies cannot even see each other. Two agents would each
# take the `merge` lease, each be told "ACQUIRED", and exclude nobody. A lock that
# reports success and protects nothing is worse than no lock, because people trust it.
#
# The fix is to anchor on the repository rather than on the file: `--git-common-dir` is
# the one directory every worktree of a repo shares by definition. State lives beside
# it, so it is shared by construction, never committed, and invisible to `git status`.
# ---------------------------------------------------------------------------------

function Invoke-GitCapture {
    # Capturing a native command's output under $ErrorActionPreference='Stop' makes git's
    # ordinary stderr (progress, warnings) a TERMINATING error. Callers keep 'Stop' for
    # cmdlets; this flips it only around the native call. Trust $LASTEXITCODE.
    param([string[]]$GitArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& git @GitArgs) } finally { $ErrorActionPreference = $prevEap }
}

function Get-GitCommonDir {
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

function Get-SharedStateDir {
    # One coordination namespace per REPOSITORY, shared by the main checkout and every
    # worktree. AI_STACK_WORKTREE_STATE overrides (the tests use it to stay hermetic).
    if ($env:AI_STACK_WORKTREE_STATE) {
        $dir = $env:AI_STACK_WORKTREE_STATE
    } else {
        $common = Get-GitCommonDir
        if (-not $common) {
            # No git: fail LOUDLY rather than silently give this caller a private namespace.
            throw "cannot resolve the shared git dir - refusing to fall back to a script-local state dir, which would make leases exclude nobody. Set AI_STACK_WORKTREE_STATE if this is deliberate."
        }
        $dir = Join-Path $common "agent-worktrees"
    }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return $dir
}

function Resolve-WorkLine {
    # The branch agents branch FROM and land ON. Precedence:
    #   explicit flag  >  AI_STACK_WORK_LINE  >  the main checkout's current branch  >  fallback
    #
    # Defaulting to the operator's ACTIVE branch is deliberate (operator, 2026-08-28):
    # someone running several agents wants them working off whatever is loaded, not off a
    # branch fixed when this was written. It also means agents inherit whatever tooling and
    # docs live on that branch, instead of being told to follow a protocol their worktree
    # does not contain.
    param([string]$Explicit = "")
    if ($Explicit) { return $Explicit }
    if ($env:AI_STACK_WORK_LINE) { return $env:AI_STACK_WORK_LINE }
    $main = Get-MainCheckout
    if ($main) {
        $branch = (Invoke-GitCapture @("-C", $main, "rev-parse", "--abbrev-ref", "HEAD") |
                   Select-Object -First 1)
        # "HEAD" means detached - not a line anything can land on.
        if ($branch -and $branch -ne "HEAD") { return $branch.Trim() }
    }
    if ($env:AI_STACK_WORK_LINE_FALLBACK) { return $env:AI_STACK_WORK_LINE_FALLBACK }
    return "development"
}

function Test-LineCheckedOutElsewhere {
    # A branch checked out in another worktree CANNOT be a merge target: git refuses to
    # check it out twice, and force-moving the ref would leave that tree's index lying.
    # Callers warn at PROVISIONING time so nobody discovers this holding the merge lease.
    param([string]$Line)
    $rows = Invoke-GitCapture @("worktree", "list", "--porcelain")
    $current = ""
    foreach ($row in $rows) {
        if ($row -like "worktree *") { $current = $row.Substring(9) }
        if ($row -eq "branch refs/heads/$Line") { return $current }
    }
    return ""
}
