# new-worktree.ps1 - provision an isolated worktree for ONE agent/session.
#
# Why this exists (2026-08-28): worktree-per-session has been CLAUDE.md policy since
# 2026-08-23 but had no mechanism, so nobody used it and two sessions sharing one
# checkout swept each other's staged work. A bare `git worktree add` is NOT enough
# here - it is broken in three verified ways:
#   1. it materializes TRACKED files only, so the worktree has no .env / .env.test /
#      OB1/docker/.env - every compose command in it fails or silently uses defaults;
#   2. it does not populate the OB1 submodule (the worktree gets an empty dir);
#   3. the harness's own EnterWorktree branches from the origin default branch, not the
#      line you are actually working on - so the base must be resolved explicitly.
# By default it branches from the MAIN CHECKOUT'S CURRENT BRANCH (operator, 2026-08-28):
# someone running several agents wants them working off whatever is loaded, and it means
# agents inherit the tooling and docs that live on that branch. Override with -Base or
# AI_STACK_WORK_LINE.
# This script does all three, records the worktree in a registry the bridge and the
# merge queue read, and refuses the footguns.
#
# Usage:
#   .\new-worktree.ps1 -Id wiki-perf
#   .\new-worktree.ps1 -Id mm-p4iic4tr -OwnerKind bridge -Thread p4iic4tr... -Json
#
# Pairs with: sync-worktree-env.ps1 (env freshness), MERGE-PROTOCOL.md (landing it).

[CmdletBinding()]
param(
    # Short and path-safe: worktree dirs carry a full OB1 checkout, and Windows
    # MAX_PATH plus node_modules depth is a real ceiling.
    [Parameter(Mandatory = $true)][string]$Id,
    [string]$Base = "",   # default: the resolved work line (see common.ps1)
    [ValidateSet("extension", "bridge", "manual")][string]$OwnerKind = "manual",
    [string]$OwnerRef = "",
    [string]$Thread = "",
    [switch]$Reuse,
    [switch]$StrictCrlf,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

# Runtime files a worktree needs but git will never give it. Copies, deliberately:
# Windows symlinks need privilege, and compose resolves --env-file relative to cwd.
$EnvFiles = @(".env", ".env.test", "OB1/docker/.env")

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Git {
    # Thin fail-fast wrapper over git-io's Invoke-GitCapture (which owns the PS5.1 stderr
    # handling). Adds only the policy this script wants: a non-zero exit is fatal unless
    # the caller says otherwise.
    param([string[]]$GitArgs, [switch]$AllowFail)
    $out = Invoke-GitCapture $GitArgs
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        Fail ("git " + ($GitArgs -join " ") + " failed with exit code $LASTEXITCODE (message above)")
    }
    return $out
}

if ($Id -notmatch '^[a-z0-9][a-z0-9-]{0,23}$') {
    Fail "Id must be lowercase alphanumeric/dash, max 24 chars (got '$Id'). Keep it short - worktree paths carry a full OB1 checkout."
}

# The MAIN checkout, not wherever this script happens to be copied (git-io resolves this
# from the shared git dir, so it holds even when we are called from inside a worktree).
$commonDir = Get-GitCommonDir
if (-not $commonDir) { Fail "not inside a git repository" }
$MainCheckout = Get-MainCheckout
$WorktreeRoot = Join-Path $MainCheckout ".claude\worktrees"
$Path = Join-Path $WorktreeRoot "wt-$Id"
$Branch = "work/$Id"
$StateDir = Get-SharedStateDir   # shared across worktrees - see common.ps1
if (-not $Base) { $Base = Resolve-WorkLine }
$Registry = Join-Path $StateDir "worktrees.json"

if (Test-Path $Path) { Fail "worktree path already exists: $Path (remove it with 'git worktree remove' or pick another -Id)" }

# Resolve the base ref: prefer the LOCAL branch. The default line is now the operator's
# active branch, where the local tip is the truth - preferring origin/<line> (as this did)
# silently based agents on the last PUSHED commit, dropping any local work they were meant
# to build on. If local and origin have diverged, say which one is being used.
$baseRef = $null
foreach ($candidate in @($Base, "origin/$Base")) {
    $null = Invoke-Git @("rev-parse", "--verify", "--quiet", $candidate) -AllowFail
    if ($LASTEXITCODE -eq 0) { $baseRef = $candidate; break }
}
if (-not $baseRef) { Fail "base ref '$Base' not found locally or on origin" }
$null = Invoke-Git @("rev-parse", "--verify", "--quiet", "origin/$Base") -AllowFail
if ($LASTEXITCODE -eq 0 -and $baseRef -eq $Base) {
    $localSha = (Invoke-Git @("rev-parse", $Base)) | Select-Object -First 1
    $originSha = (Invoke-Git @("rev-parse", "origin/$Base")) | Select-Object -First 1
    if ($localSha -ne $originSha) {
        Write-Host ("  NOTE   : local '{0}' differs from origin/{0} - branching from your LOCAL tip." -f $Base) -ForegroundColor Yellow
    }
}

$branchExists = $false
$null = Invoke-Git @("rev-parse", "--verify", "--quiet", "refs/heads/$Branch") -AllowFail
if ($LASTEXITCODE -eq 0) { $branchExists = $true }
if ($branchExists -and -not $Reuse) {
    Fail "branch $Branch already exists. Pass -Reuse to attach a new worktree to it, or pick another -Id."
}

Write-Host "Creating worktree wt-$Id" -ForegroundColor Cyan
Write-Host ("  base   : {0} ({1})" -f $baseRef, ((Invoke-Git @("rev-parse", "--short", $baseRef)) | Select-Object -First 1))
Write-Host ("  branch : {0}{1}" -f $Branch, $(if ($branchExists) { " (existing, reused)" } else { "" }))
Write-Host ("  path   : {0}" -f $Path)

if ($branchExists) {
    $null = Invoke-Git @("worktree", "add", $Path, $Branch)
} else {
    $null = Invoke-Git @("worktree", "add", $Path, "-b", $Branch, $baseRef)
}

# --- OB1 submodule: git worktree add leaves it empty ------------------------------
$ob1 = Join-Path $Path "OB1"
if (Test-Path $ob1) {
    Write-Host "  submodule OB1: initializing at the pinned SHA" -ForegroundColor Cyan
    $null = Invoke-Git @("-C", $Path, "submodule", "update", "--init", "OB1") -AllowFail
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: OB1 submodule init failed - the worktree is usable for ai-stack work but OB1/ is empty." -ForegroundColor Yellow
    }
}

# --- runtime env files ------------------------------------------------------------
$copied = @()
$missing = @()
foreach ($rel in $EnvFiles) {
    $src = Join-Path $MainCheckout ($rel -replace "/", "\")
    $dst = Join-Path $Path ($rel -replace "/", "\")
    if (-not (Test-Path $src)) { $missing += $rel; continue }
    $dstDir = Split-Path -Parent $dst
    if (-not (Test-Path $dstDir)) { New-Item -ItemType Directory -Force -Path $dstDir | Out-Null }
    Copy-Item -Path $src -Destination $dst -Force
    $copied += $rel
}
Write-Host ("  env    : copied " + ($(if ($copied.Count) { $copied -join ", " } else { "nothing" })))
if ($missing.Count) { Write-Host ("           absent in main checkout (skipped): " + ($missing -join ", ")) -ForegroundColor Yellow }

# --- keep the env COPIES away from the index --------------------------------------
# A worktree based on a branch whose .gitignore lacks an entry shows the copy as
# UNTRACKED - one `git add .` from the accident .gitignore's own comment warns about
# ("Never let an .env copy near the index again"). Found live: `.env.test` is ignored
# on refactor/ai-stack-cleanup but not on development, so a development-based worktree
# listed it as untracked. A per-worktree .git/worktrees/<id>/info/exclude is NOT
# honored (verified); the COMMON info/exclude is, and it is machine-local, never
# committed, and applies to every worktree. Idempotent.
$excludeFile = Join-Path $commonDir "info\exclude"
$excluded = @()
$untracked = @(& git -C $Path status --porcelain --untracked-files=all |
    Where-Object { $_ -match '^\?\? ' } |
    ForEach-Object { ($_ -replace '^\?\? ', '').Trim('"') })
if ($untracked.Count) {
    $existing = @()
    if (Test-Path $excludeFile) { $existing = @(Get-Content $excludeFile) }
    foreach ($rel in $copied) {
        if ($untracked -notcontains $rel) { continue }
        if ($existing -contains $rel) { continue }
        Add-Content -Path $excludeFile -Value $rel -Encoding ASCII
        $existing += $rel
        $excluded += $rel
    }
}
if ($excluded.Count) {
    Write-Host ("           NOT ignored on this base branch, added to .git/info/exclude: " + ($excluded -join ", ")) -ForegroundColor Yellow
}

# --- CRLF guard -------------------------------------------------------------------
# core.autocrlf=true rewrites on checkout; a CRLF *.sh inside a docker build context
# dies as "$'\r': command not found". .gitattributes should prevent it - verify, do
# not assume (this exact trap cost a wiki build day).
$crlf = @()
foreach ($rel in (& git -C $Path ls-files "*.sh")) {
    $full = Join-Path $Path ($rel -replace "/", "\")
    if (-not (Test-Path $full)) { continue }
    $raw = Get-Content -Raw -Path $full -ErrorAction SilentlyContinue
    if ($raw -and $raw.Contains("`r`n")) { $crlf += $rel }
}
if ($crlf.Count) {
    $msg = "CRLF found in tracked *.sh inside the worktree: " + ($crlf -join ", ")
    if ($StrictCrlf) { Fail $msg }
    Write-Host ("  WARNING: " + $msg) -ForegroundColor Yellow
    Write-Host "           docker builds consuming these will fail. Fix .gitattributes, then re-checkout." -ForegroundColor Yellow
} else {
    Write-Host "  crlf   : clean (tracked *.sh are LF)"
}

# --- registry ---------------------------------------------------------------------
if (-not (Test-Path $StateDir)) { New-Item -ItemType Directory -Force -Path $StateDir | Out-Null }
$reg = @{ worktrees = @{} }
if (Test-Path $Registry) {
    try {
        $parsed = Get-Content -Raw -Path $Registry | ConvertFrom-Json
        $reg = @{ worktrees = @{} }
        if ($parsed.worktrees) {
            foreach ($p in $parsed.worktrees.PSObject.Properties) { $reg.worktrees[$p.Name] = $p.Value }
        }
    } catch {
        Write-Host "  WARNING: registry unreadable, starting a fresh one (old file kept as .bad)" -ForegroundColor Yellow
        Copy-Item $Registry "$Registry.bad" -Force
    }
}
$reg.worktrees[$Id] = [ordered]@{
    id         = $Id
    path       = $Path
    branch     = $Branch
    base       = $baseRef
    owner_kind = $OwnerKind
    owner_ref  = $OwnerRef
    thread     = $Thread
    created    = [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}
$tmp = "$Registry.tmp"
($reg | ConvertTo-Json -Depth 6) | Set-Content -Path $tmp -Encoding ASCII
Move-Item -Path $tmp -Destination $Registry -Force

# --- merge-target warnings, raised NOW rather than 40 minutes later ----------------
# Both of these are invisible until the agent is holding the merge lease, which is the
# worst moment to find them.
$holder = Test-LineCheckedOutElsewhere -Line $Base
if ($holder) {
    Write-Host ("  NOTE   : '{0}' is checked out at {1}, so work cannot be MERGED into it" -f $Base, $holder) -ForegroundColor Yellow
    Write-Host "           while it stays there (git refuses a second checkout of one branch)." -ForegroundColor Yellow
    Write-Host "           Park that checkout elsewhere before landing, or land onto a different line." -ForegroundColor Yellow
}
if ($Base -in @("main", "master", "origin/main", "origin/master")) {
    Write-Host ("  NOTE   : '{0}' is a protected line here - it is promoted deliberately, not merged into by agents." -f $Base) -ForegroundColor Yellow
}

Write-Host "Worktree ready." -ForegroundColor Green
Write-Host "  Enter it with:  EnterWorktree path: $Path"
Write-Host "  Land it with:   documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL.md"

if ($Json) {
    [pscustomobject]@{
        id = $Id; path = $Path; branch = $Branch; base = $baseRef
        env_copied = $copied; crlf_dirty = $crlf
    } | ConvertTo-Json -Compress
}
exit 0
