# resolve.ps1 - POLICY: where shared state lives, and which branch agents work against.
#
# Single responsibility: turn environment + repository facts into the two decisions every
# script here must agree on. It asks `git-io.ps1` for facts and never runs git itself, so
# the policy is testable by pointing the environment overrides somewhere else.
#
# Both decisions are overridable by environment variable, which is what makes this toolkit
# portable: another distribution changes the variables, not the code. Which variables, and
# what to fall back to, are themselves configuration - this file reads them from
# config.ps1 rather than hardcoding the names it happens to have been written with.

. (Join-Path $PSScriptRoot "config.ps1")
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
    $stateEnv = Get-HarnessSetting "worktree.state_dir_env" "AI_STACK_WORKTREE_STATE"
    $override = [Environment]::GetEnvironmentVariable($stateEnv)
    if ($override) {
        $dir = $override
    } else {
        $common = Get-GitCommonDir
        if (-not $common) {
            # Fail LOUDLY. Falling back to a script-local dir would hand this caller a
            # private namespace and silently restore the exact bug described above.
            throw ("cannot resolve the shared git dir - refusing to fall back to a " +
                   "script-local state dir, which would make claims exclude nobody. " +
                   "Set $stateEnv if this is deliberate.")
        }
        $dir = Join-Path $common (Get-HarnessSetting "worktree.state_dir_name" "agent-worktrees")
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
    $lineEnv = Get-HarnessSetting "worktree.work_line_env" "AI_STACK_WORK_LINE"
    $requested = [Environment]::GetEnvironmentVariable($lineEnv)
    if ($requested) { return $requested }
    $main = Get-MainCheckout
    if ($main) {
        $branch = Get-CurrentBranch -RepoPath $main
        if ($branch) { return $branch }
    }
    return (Get-HarnessSetting "worktree.work_line_fallback" "development")
}

function Test-LineCheckedOutElsewhere {
    # Thin policy wrapper over the git fact, kept so callers read as intent ("can this be a
    # merge target?") rather than as plumbing. Returns the holding worktree, or "".
    param([string]$Line)
    return (Get-WorktreeHoldingBranch -Branch $Line)
}

function Get-LeaseDir {
    # ONE lock namespace per repository - the same decision Get-SharedStateDir makes, named
    # once so lease.ps1 and its readers cannot drift onto two different directories.
    # AI_STACK_LEASE_DIR still overrides (the drills use it to stay hermetic).
    if ($env:AI_STACK_LEASE_DIR) { return $env:AI_STACK_LEASE_DIR }
    return (Join-Path (Get-SharedStateDir) "locks")
}

function Get-HeldLease {
    # The lease record ONLY if it is currently held: present and not past its TTL.
    # Returns $null for "free" and for "expired", because a caller asking "may I mutate this
    # plane?" must treat both the same way. lease.ps1 keeps its own reader because its
    # -Status display has to tell FREE and EXPIRED apart; this one deliberately does not.
    param([Parameter(Mandatory = $true)][string]$Name)
    $f = Join-Path (Get-LeaseDir) "$Name.json"
    if (-not (Test-Path $f)) { return $null }
    try { $l = Get-Content -Raw -Path $f | ConvertFrom-Json } catch { return $null }
    if (-not $l) { return $null }
    $age = [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [int64]$l.taken_at
    # -ge matches lease.ps1's Test-Expired: at exactly the TTL the lease IS expired.
    if ($age -ge ([int]$l.ttl_min * 60)) { return $null }
    return $l
}
