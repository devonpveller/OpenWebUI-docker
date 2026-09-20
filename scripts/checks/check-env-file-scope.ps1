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

  THE VERDICT IS ABOUT THE PATH, SO THE PATH IS PARSED FIRST. Compose accepts the value
  as a scalar, a flow sequence `[a, b]`, a block sequence, and long-form entries
  (`- path: x` / `required: false`, or `- {path: x, required: false}`), any of them
  quoted and any of them with a trailing comment. All of those shapes are read; see
  Get-EntryPath. A value that does NOT parse into plain path text - an unterminated
  sequence, a `${VAR}` whose target is not knowable from the file - is REFUSED rather
  than normalized, and so is a value that climbs with `..` and lands back inside the
  compose file's own directory. Fail closed: a value this script cannot read is a value
  it cannot vouch for, and reading one wrongly is exactly how it was caught out once.

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

# --- reading an env_file VALUE -------------------------------------------------------
# THE REGRESSION THIS REPAYS (found by the tester, attempt 1 of sl-ao-envfile). The first
# version of the resolving verdict below took the raw token and split it on '/'. For the
# two shapes where the token is not bare path text - the flow sequence
# `env_file: [../../.env]` and the compose-spec long form `- path: ../../.env` - the
# unparsed leading fragment ('[..', 'path: ..') was swallowed as an ordinary path SEGMENT,
# which the following '..' then popped. The target therefore resolved to the compose
# file's OWN directory and was allowed. Both are real grants: docker rendered them and
# injected the root file's variables. The predecessor matched '\.\.[\\/]' on the raw text
# and caught both, so this was a regression, not an inherited hole.
#
# So: parse the value into PATHS first, resolve second. Compose accepts
#   env_file: ../.env                     scalar
#   env_file: "../.env"                   quoted scalar
#   env_file: [a, "b"]                    flow sequence
#   env_file:                             block sequence
#     - a
#     - path: b                           long form, optionally with `required:` after it
#       required: false
#     - {path: c, required: false}        long form as a flow mapping
# and any of them may carry a trailing comment.
#
# Anything that does not parse into plain path text is REFUSED, not normalized - see
# Get-GrantVerdict. Fail closed: a value this script cannot read is a value it cannot
# vouch for, and the failure mode above was precisely a value read wrongly and passed.

# Cut a trailing YAML comment, respecting quotes. A '#' only starts a comment when it is
# at the start or preceded by whitespace, which is what keeps a '#' inside a path intact.
function Remove-YamlComment([string]$s) {
    $inSingle = $false
    $inDouble = $false
    for ($i = 0; $i -lt $s.Length; $i++) {
        $c = $s[$i]
        if ($c -eq "'" -and -not $inDouble) { $inSingle = -not $inSingle; continue }
        if ($c -eq '"' -and -not $inSingle) { $inDouble = -not $inDouble; continue }
        if ($c -eq '#' -and -not $inSingle -and -not $inDouble) {
            if ($i -eq 0) { return '' }
            $prev = $s[$i - 1]
            if ($prev -eq ' ' -or $prev -eq "`t") { return $s.Substring(0, $i) }
        }
    }
    return $s
}

function Remove-Quotes([string]$s) {
    $t = $s.Trim()
    if ($t.Length -ge 2) {
        $a = $t[0]
        $b = $t[$t.Length - 1]
        if (($a -eq '"' -and $b -eq '"') -or ($a -eq "'" -and $b -eq "'")) {
            return $t.Substring(1, $t.Length - 2)
        }
    }
    return $t
}

# One sequence ENTRY -> the path it names. A long-form entry is a mapping whose `path` key
# carries it; `required:`/`format:` entries carry none and return ''. An entry this cannot
# read returns the special marker '?' so the caller refuses it rather than guessing.
function Get-EntryPath([string]$entry) {
    $e = (Remove-YamlComment $entry).Trim()
    if (-not $e) { return '' }
    if ($e.StartsWith('{')) {
        if (-not $e.EndsWith('}')) { return '?' }
        $inner = $e.Substring(1, $e.Length - 2)
        foreach ($kv in ($inner -split ',')) {
            if ($kv -match '^\s*(["'']?)path\1\s*:\s*(.+)$') { return (Remove-Quotes $Matches[2]) }
        }
        return ''
    }
    if ($e -match '^(["'']?)path\1\s*:\s*(.+)$') { return (Remove-Quotes $Matches[2]) }
    if ($e -match '^(required|format)\s*:') { return '' }
    return (Remove-Quotes $e)
}

# The value on the `env_file:` line itself, split into ENTRIES: a flow sequence yields one
# per item, anything else yields itself. The caller reads each entry's path out of it, so
# a two-item sequence with one bad item reports the bad ITEM rather than the whole value.
function Get-InlineValueEntries([string]$value) {
    $v = (Remove-YamlComment $value).Trim()
    if (-not $v) { return @() }
    if ($v.StartsWith('[')) {
        # An unterminated flow sequence is handed back whole: the '[' it still carries
        # trips the plain-path-text guard, which refuses it and PRINTS it.
        if (-not $v.EndsWith(']')) { return @($v) }
        $inner = $v.Substring(1, $v.Length - 2)
        return @(($inner -split ',') | Where-Object { $_.Trim() })
    }
    return @($v)
}

# $composeRel is the compose file's repo-relative path. $path is already EXTRACTED from
# the YAML value; $raw is the text it came from, used only for the fail-closed check.
# Returns '' when the target is scoped, otherwise the reason it is refused.
function Get-GrantVerdict([string]$composeRel, [string]$path, [string]$raw) {
    if ($path -eq '?') { return 'is an env_file value this check cannot parse' }
    $t = $path.Trim()
    if (-not $t) { return '' }
    # Plain path text only. A bracket, a brace, a comma, whitespace or an interpolation
    # means either an unparsed value or one whose target is not knowable from the file.
    if ($t -match '[\[\]{},]' -or $t -match '\s' -or $t -match '\$') {
        return "is not plain path text ('$t') - unparsed or interpolated, so its target cannot be checked"
    }
    $composeDir = Get-ParentDir $composeRel
    $resolved = Resolve-RepoRelative $composeDir $t
    if ($null -eq $resolved) { return 'resolves outside the repository' }
    $targetDir = Get-ParentDir $resolved
    if ($targetDir -eq '') { return 'is the repo root env file' }
    if ($targetDir -ne $composeDir -and -not (($composeDir + '/').StartsWith($targetDir + '/'))) {
        return "belongs to another directory ($targetDir)"
    }
    # FAIL CLOSED. The raw value climbed, and the result did not: either the value was
    # read wrongly (the regression above), or it is spelled `../<thisdir>/.env`, which
    # should be written `.env`. Either way this script will not vouch for it.
    if ($raw -match '\.\.' -and (($targetDir + '/').StartsWith($composeDir + '/'))) {
        return 'climbs with .. yet resolves back inside the compose file''s own directory - write it without the ..'
    }
    return ''
}

$violations = @()

function Scan-ComposeText([string]$displayPath, [string]$composeRel, [string[]]$lines) {
    # Walk the file rather than regex it whole: `env_file:` and its list entries are on
    # separate lines, and a service's own comment mentioning env_file must not match.
    $out = @()
    $inList = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*#') { continue }
        if ($line -match '^\s*env_file\s*:\s*(\S.*)?$') {
            $inline = $Matches[1]
            if ($inline) {
                foreach ($entry in (Get-InlineValueEntries $inline)) {
                    $raw = $entry.Trim()
                    $p = ''
                    if ($raw -eq '?') { $p = '?' } else { $p = Get-EntryPath $raw }
                    if (-not $p) { continue }
                    $why = Get-GrantVerdict $composeRel $p $raw
                    if ($why) { $out += "${displayPath}:$($i+1): env_file: $raw -- $why" }
                }
                $inList = $false
            } else { $inList = $true }
            continue
        }
        if ($inList) {
            if ($line -match '^\s*-\s*(\S.*)$') {
                $entry = $Matches[1]
                $raw = (Remove-YamlComment $entry).Trim()
                $p = Get-EntryPath $entry
                if ($p) {
                    $why = Get-GrantVerdict $composeRel $p $raw
                    if ($why) { $out += "${displayPath}:$($i+1): env_file -> $raw -- $why" }
                }
            } elseif ($line -match '^\s+(path|required|format)\s*:\s*(\S.*)?$') {
                # A long-form entry whose keys span lines: `- required: false` on one line
                # and `  path: ../../.env` on the next. The path key is the only one that
                # names a file; the others are read and ignored so the list stays open.
                if ($Matches[1] -eq 'path') {
                    $raw = (Remove-YamlComment $Matches[2]).Trim()
                    $why = Get-GrantVerdict $composeRel (Remove-Quotes $raw) $raw
                    if ($why) { $out += "${displayPath}:$($i+1): env_file -> path: $raw -- $why" }
                }
            } elseif ($line -match '^\s*$') {
                # A blank line inside a block sequence does not end it.
            } else {
                $inList = $false
            }
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
    # A plane split its compose file by service group in 2026-09 (inference/compose/*.yml),
    # and none of those four basenames matches "compose*.yml" - so the four files that
    # define the inference plane's services were invisible to this guard. .gitattributes
    # already carries a */compose/*.yml rule for the same reason. Override files are in
    # for the same reason: an override is exactly where a grant would be added quietly.
    $files = @(Get-ChildItem -Path $RootFull -Recurse -File -Include "docker-compose*.yml", "compose*.yml", "*.override.yml", "*.override.yaml" -ErrorAction SilentlyContinue)
    $files += @(Get-ChildItem -Path $RootFull -Recurse -File -Include "*.yml", "*.yaml" -ErrorAction SilentlyContinue |
                Where-Object { $_.Directory -and $_.Directory.Name -eq "compose" })
    $files = @($files | Sort-Object -Property FullName -Unique)
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
          Where-Object { $_ -match '(^|/)(docker-)?compose[^/]*\.ya?ml$' -or
                         $_ -match '(^|/)compose/[^/]+\.ya?ml$' -or
                         $_ -match '\.override\.ya?ml$' }
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
