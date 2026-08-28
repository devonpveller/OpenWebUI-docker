# resolve.ps1 - POLICY: where shared state lives, and which branch agents work against.
#
# Single responsibility: turn environment + repository facts into the two decisions every
# script here must agree on. It asks `git-io.ps1` for facts and never runs git itself, so
# the policy is testable by pointing the environment overrides somewhere else.
#
# Both decisions are overridable by environment variable, which is what makes this toolkit
# portable: another distribution changes the variables, not the code.

. (Join-Path $PSScriptRoot "git-io.ps1")

function Get-SharedStateDir {
    # ONE coordination namespace per REPOSITORY, shared by the main checkout and every
    # worktree.
    #
    # This was originally anchored on $PSScriptRoot, which is correct only while exactly one
    # copy of this toolkit exists. The moment it is checked out inside a worktree - the whole
    # point of the toolkit - that copy resolved its OWN state dir, and the dir is gitignored
    # so the copies could not even see each other. Two agents would each be told they held a
    # claim while excluding nobody. A lock that reports success and protects nothing is worse
    # than no lock, because people trust it.
    if ($env:AI_STACK_WORKTREE_STATE) {
        $dir = $env:AI_STACK_WORKTREE_STATE
    } else {
        $common = Get-GitCommonDir
        if (-not $common) {
            # Fail LOUDLY. Falling back to a script-local dir would hand this caller a
            # private namespace and silently restore the exact bug described above.
            throw ("cannot resolve the shared git dir - refusing to fall back to a " +
                   "script-local state dir, which would make claims exclude nobody. " +
                   "Set AI_STACK_WORKTREE_STATE if this is deliberate.")
        }
        $dir = Join-Path $common "agent-worktrees"
    }
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return $dir
}

function Resolve-WorkLine {
    # The branch agents branch FROM and land ON. Precedence:
    #   explicit argument > AI_STACK_WORK_LINE > the main checkout's current branch > fallback
    #
    # Defaulting to the operator's ACTIVE branch is deliberate (operator, 2026-08-28):
    # someone running several agents wants them working off whatever is loaded, not off a
    # branch fixed when this was written. It also means agents inherit the tooling and docs
    # that live on that branch, instead of being told to follow a protocol their worktree
    # does not contain.
    param([string]$Explicit = "")
    if ($Explicit) { return $Explicit }
    if ($env:AI_STACK_WORK_LINE) { return $env:AI_STACK_WORK_LINE }
    $main = Get-MainCheckout
    if ($main) {
        $branch = Get-CurrentBranch -RepoPath $main
        if ($branch) { return $branch }
    }
    if ($env:AI_STACK_WORK_LINE_FALLBACK) { return $env:AI_STACK_WORK_LINE_FALLBACK }
    return "development"
}

function Test-LineCheckedOutElsewhere {
    # Thin policy wrapper over the git fact, kept so callers read as intent ("can this be a
    # merge target?") rather than as plumbing. Returns the holding worktree, or "".
    param([string]$Line)
    return (Get-WorktreeHoldingBranch -Branch $Line)
}
