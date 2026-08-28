# new-worktree.ps1 - provision an isolated worktree for ONE agent/session.
#
# Why this exists (2026-08-28): worktree-per-session has been CLAUDE.md policy since
# 2026-08-23 but had no mechanism, so nobody used it and two sessions sharing one
# checkout swept each other's staged work. A bare `git worktree add` is NOT enough
# here - it is broken in three verified ways:
#   1. it materializes TRACKED files only, so the worktree has no .env / .env.test /
#      OB1/docker/.env - every compose command in it fails or silently uses defaults;
#   2. it does not populate the OB1 submodule (the worktree gets an empty dir);
#   3. the harness's own EnterWorktree branches from the origin default branch, not
#      `development` - this repo's work line - so the base must be passed explicitly.
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
    [string]$Base = "development",
    [ValidateSet("extension", "bridge", "manual")][string]$OwnerKind = "manual",
    [string]$OwnerRef = "",
    [string]$Thread = "",
    [switch]$Reuse,
    [switch]$StrictCrlf,
    [switch]$Json
)

$ErrorActionPreference = "Stop"

# Runtime files a worktree needs but git will never give it. Copies, deliberately:
# Windows symlinks need privilege, and compose resolves --env-file relative to cwd.
$EnvFiles = @(".env", ".env.test", "OB1/docker/.env")

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Invoke-Git {
    param([string[]]$GitArgs, [switch]$AllowFail)
    # NO `2>&1` here. In PS5.1 redirecting a native exe's stderr wraps every line in an
    # ErrorRecord, and with $ErrorActionPreference='Stop' git's ORDINARY progress chatter
    # ("Preparing worktree (new branch ...)") becomes a terminating error - this script
    # died on exactly that on its first run. Let stderr flow to the console; trust
    # $LASTEXITCODE, which is the only honest success signal for a native command.
    $out = & git @GitArgs
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) {
        Fail ("git " + ($GitArgs -join " ") + " failed with exit code $LASTEXITCODE (message above)")
    }
    return $out
}

if ($Id -notmatch '^[a-z0-9][a-z0-9-]{0,23}$') {
    Fail "Id must be lowercase alphanumeric/dash, max 24 chars (got '$Id'). Keep it short - worktree paths carry a full OB1 checkout."
}

# The MAIN checkout, not wherever this script happens to be copied. `--git-common-dir`
# points at the shared .git even when we are called from inside another worktree.
$commonDir = (Invoke-Git @("rev-parse", "--path-format=absolute", "--git-common-dir")) | Select-Object -First 1
if (-not $commonDir) { Fail "not inside a git repository" }
$MainCheckout = Split-Path -Parent $commonDir
$WorktreeRoot = Join-Path $MainCheckout ".claude\worktrees"
$Path = Join-Path $WorktreeRoot "wt-$Id"
$Branch = "work/$Id"
$StateDir = Join-Path $PSScriptRoot "state"
$Registry = Join-Path $StateDir "worktrees.json"

if (Test-Path $Path) { Fail "worktree path already exists: $Path (remove it with 'git worktree remove' or pick another -Id)" }

# Resolve the base ref: prefer the remote tip so a stale local branch cannot silently
# become the base of new work.
$baseRef = $null
foreach ($candidate in @("origin/$Base", $Base)) {
    $null = Invoke-Git @("rev-parse", "--verify", "--quiet", $candidate) -AllowFail
    if ($LASTEXITCODE -eq 0) { $baseRef = $candidate; break }
}
if (-not $baseRef) { Fail "base ref '$Base' not found locally or on origin" }

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
