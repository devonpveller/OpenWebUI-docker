#requires -Version 5
<#
.SYNOPSIS
  Block a compose service from granting itself the WHOLE root .env via `env_file`.

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

.NOTES
  STAGED-ONLY, deliberately. This fails on a NEWLY ADDED grant, not on ones already in
  the tree - a guard that blocks unrelated commits over pre-existing debt gets disabled,
  and then it guards nothing. Known remaining grants are reported as a warning so they
  stay visible without being a gate. Run with -All to audit the whole tree.

  Exit code 0 = clean, 1 = a new grant was staged.
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

# A grant is "broad" when the env_file target resolves to a .env that is NOT beside the
# compose file that names it. A plane keeping its own .env next to its compose file is
# scoped by construction and is fine; reaching up the tree for a shared one is not.
function Test-BroadTarget([string]$target) {
    $t = $target.Trim().TrimStart('-').Trim().Trim('"').Trim("'")
    if (-not $t) { return $false }
    return ($t -match '\.\.[\\/]')
}

$violations = @()

function Scan-ComposeText([string]$path, [string[]]$lines) {
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
                if (Test-BroadTarget $inline) { $out += "${path}:$($i+1): env_file: $inline" }
                $inList = $false
            } else { $inList = $true }
            continue
        }
        if ($inList) {
            if ($line -match '^\s*-\s*(\S.*)$') {
                if (Test-BroadTarget $Matches[1]) { $out += "${path}:$($i+1): env_file -> $($Matches[1].Trim())" }
            } else { $inList = $false }
        }
    }
    return $out
}

if ($All) {
    $files = @(Get-ChildItem -Path $Root -Recurse -File -Include "docker-compose*.yml", "compose*.yml" -ErrorAction SilentlyContinue |
               Where-Object { $_.FullName -notmatch '[\\/](\.git|\.claude|node_modules|OB1)[\\/]' })
    foreach ($f in $files) {
        $violations += Scan-ComposeText $f.FullName (Get-Content -Path $f.FullName)
    }
    if ($violations.Count) {
        Write-Host "Compose services granting themselves a shared .env ($($violations.Count)):" -ForegroundColor Yellow
        $violations | ForEach-Object { Write-Host "  $_" }
        Write-Host ""
        Write-Host "THE RULE: a service names the variables it needs." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "env_file scope: clean - no service grants itself a shared .env." -ForegroundColor Green
    exit 0
}

# --- staged mode: only what this commit ADDS ----------------------------------------
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
    $found = Scan-ComposeText $rel $content
    if (-not $found.Count) { continue }
    # Present in HEAD already? Then it is pre-existing debt, not something this commit
    # introduces - report it, do not block on it.
    $prev = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    $head = @(git show "HEAD:$rel" 2>$null)
    $ErrorActionPreference = $prev
    $headFound = if ($LASTEXITCODE -eq 0 -and $head) { @(Scan-ComposeText $rel $head) } else { @() }
    $headTargets = @($headFound | ForEach-Object { ($_ -split ': ', 2)[1] })
    foreach ($f in $found) {
        $target = ($f -split ': ', 2)[1]
        if ($headTargets -contains $target) {
            Write-Host "  (pre-existing, not blocked) $f" -ForegroundColor DarkYellow
        } else {
            $violations += $f
        }
    }
}

if ($violations.Count) {
    Write-Host ""
    Write-Host "NEW env_file grant of a shared .env ($($violations.Count)):" -ForegroundColor Red
    $violations | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    Write-Host ""
    Write-Host "THE RULE: a service names the variables it needs." -ForegroundColor Yellow
    Write-Host "`${VAR} in a compose 'environment:' block is interpolated by the compose CLI from"
    Write-Host "the project environment - it does NOT need env_file. Listing the variables is"
    Write-Host "the whole fix. See the header of this script for what this rule is repaying."
    exit 1
}

Write-Host "env_file scope: no new shared-.env grants staged." -ForegroundColor Green
exit 0
