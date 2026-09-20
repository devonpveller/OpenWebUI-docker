#requires -Version 5
<#
.SYNOPSIS
  Block a compose service from granting itself a shared .env via `env_file`.

.DESCRIPTION
  THE RULE: a service names the variables it needs.

  `env_file: ../.env` injects every variable in the root .env into a container's
  environment. It is not about file access - `printenv` inside the container returns
  the lot. On 2026-08-28 that meant 111 variables spanning every plane (the Cloudflare
  tunnel token, the Authelia secrets, the Mullvad key, the Tailscale auth key, the
  Mattermost bot tokens) reaching `open-terminal` and `little-coder`, of which the
  little-coder source referenced NONE.

  How it got there is the part worth not repeating. The line was introduced with
  open-terminal (commit 0bc099e) alongside an explicit `API_KEY=${OPEN_TERMINAL_API_KEY}` -
  and `${...}` is interpolated by the compose CLI from the project environment, never by
  `env_file`. So it was redundant on the day it was written, copied from the `openwebui`
  service above it, which legitimately consumes a large slice of .env. It then survived
  two file moves, because a move preserves lines; it does not audit them. Meanwhile .env
  grew from a handful of variables into the whole stack.

  A WILDCARD GRANT IS SAFE UNTIL THE THING IT WILDCARDS GROWS. Nothing was watching the
  size of the blast radius, so nothing complained.

  WHAT COUNTS AS A GRANT (2026-09-19, sl-ao-envfile). The target is resolved against the
  directory of the compose file that names it, and the verdict is about WHERE it lands:

    * the repo root `.env`                     -> REFUSED. The shared file every plane
                                                  used to reach into; per D10 no plane
                                                  reads it any more.
    * outside the repo, or an absolute path    -> REFUSED. Nothing in-tree can audit it.
    * a directory that is neither the compose
      file's own nor one of its parents
      (a cross-plane borrow)                   -> REFUSED. Same wildcard, other plane.
    * the compose file's own directory, or a
      parent that is not the repo root
      (a plane's own .env)                     -> ALLOWED. Scoped by construction.

  So `agent-org/docker/docker-compose.yml` may name `- .env` (its own) and could name
  `- ../.env` (agent-org's own), but not `- ../../.env` (the repo root).

.NOTES
  STAGED-ONLY, deliberately: it fails on what a commit leaves in a compose file it
  touches, and -All audits the whole tree. Until 2026-09-19 it ALSO grandfathered any
  grant already present in HEAD, which is why `env_file: ../../.env` on ao-worker-1 and
  ao-worker-2 passed pre-commit for weeks while -All reported them red. Item
  sl-ao-envfile removed those two grants, leaving the tree with none - so the debt clause
  now has nothing to protect, and its only possible effect would be to re-admit the exact
  thing that was just paid off. It is gone: a grant in a staged compose file blocks
  whether or not HEAD carries it. There is no per-service exemption list, on purpose.

  Exit code 0 = clean, 1 = a grant was staged (or, with -All, found in the tree).
#>
[CmdletBinding()]
param(
    [string]$Root,
    [switch]$All          # audit the whole tree instead of the staged diff
)

$ErrorActionPreference = 'Stop'

if (-not $Root) { $Root = (git rev-parse --show-toplevel 2>$null) }
if (-not $Root) { $Root = (Get-Location).Path }
$Root = $Root.Trim()
$RootFull = $Root
try { $RootFull = (Resolve-Path -LiteralPath $Root).Path } catch { }
$RootFull = $RootFull.TrimEnd('\', '/')

# Repo-relative directory of a repo-relative file path ('' when the file sits at the root).
function Get-ParentDir([string]$relPath) {
    $p = ($relPath -replace '\\', '/')
    $i = $p.LastIndexOf('/')
    if ($i -lt 0) { return '' }
    return $p.Substring(0, $i)
}

# Resolve an env_file target against the compose file's directory, both repo-relative with
# '/' separators. Returns $null when it is absolute or climbs out of the repo - neither is
# something this tree can audit, and both are refused by the caller.
function Resolve-RepoRelative([string]$baseDir, [string]$target) {
    $t = ($target -replace '\\', '/')
    if ($t -match '^[A-Za-z]:' -or $t.StartsWith('/')) { return $null }
    $parts = @()
    if ($baseDir) { $parts = @(($baseDir -split '/') | Where-Object { $_ -ne '' }) }
    foreach ($seg in ($t -split '/')) {
        if ($seg -eq '' -or $seg -eq '.') { continue }
        if ($seg -eq '..') {
            if ($parts.Count -eq 0) { return $null }
            if ($parts.Count -eq 1) { $parts = @() }
            else { $parts = @($parts[0..($parts.Count - 2)]) }
        } else {
            $parts += $seg
        }
    }
    if ($parts.Count -eq 0) { return $null }
    return ($parts -join '/')
}

# $composeRel is the compose file's repo-relative path. Returns '' when the target is
# scoped, otherwise the reason it is refused.
function Get-GrantVerdict([string]$composeRel, [string]$target) {
    $t = $target.Trim().TrimStart('-').Trim().Trim('"').Trim("'")
    if (-not $t) { return '' }
    $composeDir = Get-ParentDir $composeRel
    $resolved = Resolve-RepoRelative $composeDir $t
    if ($null -eq $resolved) { return 'resolves outside the repository' }
    $targetDir = Get-ParentDir $resolved
    if ($targetDir -eq '') { return 'is the repo root env file' }
    if ($targetDir -eq $composeDir) { return '' }
    if (($composeDir + '/').StartsWith($targetDir + '/')) { return '' }
    return "belongs to another directory ($targetDir)"
}

$violations = @()

function Scan-ComposeText([string]$displayPath, [string]$composeRel, [string[]]$lines) {
    # Walk the file rather than regex it whole: `env_file:` and its list items are on
    # separate lines, and a service's own comment mentioning env_file must not match.
    $out = @()
    $inList = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*env_file\s*:\s*(\S.*)?$') {
            $inline = $Matches[1]
            if ($inline) {
                $why = Get-GrantVerdict $composeRel $inline
                if ($why) { $out += "${displayPath}:$($i+1): env_file: $($inline.Trim()) -- $why" }
                $inList = $false
            } else { $inList = $true }
            continue
        }
        if ($inList) {
            if ($line -match '^\s*-\s*(\S.*)$') {
                $item = $Matches[1]
                $why = Get-GrantVerdict $composeRel $item
                if ($why) { $out += "${displayPath}:$($i+1): env_file -> $($item.Trim()) -- $why" }
            } else { $inList = $false }
        }
    }
    return $out
}

function Write-TheRule {
    Write-Host ""
    Write-Host "THE RULE: a service names the variables it needs." -ForegroundColor Yellow
    Write-Host "`${VAR} in a compose 'environment:' block is interpolated by the compose CLI from"
    Write-Host "the project environment - it does NOT need env_file. Listing the variables is"
    Write-Host "the whole fix. See the header of this script for what this rule is repaying."
}

if ($All) {
    $files = @(Get-ChildItem -Path $RootFull -Recurse -File -Include "docker-compose*.yml", "compose*.yml" -ErrorAction SilentlyContinue)
    foreach ($f in $files) {
        # Skip vendored/nested trees by their position UNDER the scan root, not by their
        # absolute path: a worktree lives under .claude\worktrees\, so matching the
        # ABSOLUTE path excluded every file in it - the audit reported a clean tree from
        # inside a worktree that held two grants (found 2026-09-19, sl-ao-envfile).
        $rel = $f.FullName
        if ($rel.StartsWith($RootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = $rel.Substring($RootFull.Length)
        }
        $rel = $rel.TrimStart('\', '/') -replace '\\', '/'
        if ($rel -match '(^|/)(\.git|\.claude|node_modules|OB1)/') { continue }
        $violations += Scan-ComposeText $f.FullName $rel (Get-Content -Path $f.FullName)
    }
    if ($violations.Count) {
        Write-Host "Compose services granting themselves a shared .env ($($violations.Count)):" -ForegroundColor Yellow
        $violations | ForEach-Object { Write-Host "  $_" }
        Write-TheRule
        exit 1
    }
    Write-Host "env_file scope: clean - no service grants itself a shared .env." -ForegroundColor Green
    exit 0
}

# --- staged mode: what this commit leaves in a compose file it touches ---------------
$staged = @(git diff --cached --name-only --diff-filter=ACMR 2>$null) |
          Where-Object { $_ -match '(^|/)(docker-)?compose[^/]*\.ya?ml$' -or $_ -match '(^|/)docker-compose[^/]*\.ya?ml$' }
if (-not $staged -or -not $staged.Count) {
    Write-Host "env_file scope: no compose files staged - skipped." -ForegroundColor DarkGray
    exit 0
}

foreach ($rel in $staged) {
    # Read the STAGED blob, not the working tree: the working tree may hold edits that
    # are not part of this commit, and the commit is what the guard is about.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $content = @(git show ":$rel" 2>$null)
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $content) { continue }
    $violations += Scan-ComposeText $rel $rel $content
}

if ($violations.Count) {
    Write-Host ""
    Write-Host "env_file grant of a shared .env in a staged compose file ($($violations.Count)):" -ForegroundColor Red
    $violations | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-TheRule
    exit 1
}

Write-Host "env_file scope: no shared-.env grants in the staged compose files." -ForegroundColor Green
exit 0
