#requires -Version 5
<#
.SYNOPSIS
  Enforce gateway-only LLM routing: fail if anything points an inference or
  embedding endpoint (or a tailnet serve route) directly at the *-upstream real
  servers instead of the LiteLLM gateway aliases.

.DESCRIPTION
  Policy (CLAUDE.md): every LLM chat + embedding call goes through the LiteLLM
  gateway `llm-gateway`, reached via its network aliases `llama-cpp:8080` /
  `llama-cpp-embed:8080`. The renamed real servers `llama-cpp-upstream:8080` /
  `llama-cpp-embed-upstream:8080` may ONLY be referenced by:
    - the gateway's own config (inference/config/litellm.config.yaml api_base),
    - health / GPU / recovery probes and the upstream service definitions.

  This guard flags the dangerous pattern: an inference/serve HOST or BASE-URL
  assigned to a *-upstream server (a bypass). It does NOT flag probes
  (HEALTH_TCP, healthchecks), service definitions, or comments.

  Exit code 0 = clean, 1 = bypass(es) found. Wire into pre-commit / CI.

.EXAMPLE
  pwsh scripts/check-llm-gateway-routing.ps1
#>
[CmdletBinding()]
param(
    [string]$Root
)

$ErrorActionPreference = 'Stop'

# Resolve the repo root robustly. The script lives in scripts/, so the repo root
# is its parent's parent. $PSScriptRoot is empty in some invocation contexts
# (e.g. `powershell -File ./relative.ps1` from a git hook), so fall back to
# $MyInvocation and finally the current directory.
if (-not $Root) {
    if ($PSScriptRoot) {
        $scriptDir = $PSScriptRoot
    } elseif ($MyInvocation.MyCommand.Path) {
        $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        $scriptDir = (Get-Location).Path
    }
    $Root = Split-Path -Parent (Split-Path -Parent $scriptDir)
    if (-not $Root) { $Root = (Get-Location).Path }
}

# The bypass smell: an inference/serve host or base-url variable set to an upstream.
$badPattern = '(?i)(_HOST|_BASE|API_BASE|BASE_URL|api_base|CHAT_API_BASE|OPENAI[A-Z_]*BASE|EMBED[A-Z_]*BASE|LLM_BASE)\b[^\r\n]*[:=][^\r\n]*(llama-cpp-upstream|llama-cpp-embed-upstream)'

# SANCTIONED upstream caller (B2, design section 3.2): the llm-queue admission controller
# sits BEHIND LiteLLM and forwards to *-upstream. Its forward target is carried in
# the LLM_QUEUE_UPSTREAM_BASE_URL / LLM_QUEUE_EMBED_UPSTREAM_BASE_URL vars. This is
# the queue's ONE legitimate *-upstream reference (LiteLLM's api_base now points at
# llm-queue, not the upstream - guard stays green there). Narrowly allow exactly
# these vars wherever they appear (compose env or the queue's own config); every
# OTHER *-upstream base/host still flags, so the queue's other files stay scanned.
$queueUpstreamAllow = '(?i)LLM_QUEUE(_EMBED)?_UPSTREAM_BASE_URL'

# Legit direct-upstream references (NOT bypasses): the gateway's own forwarding
# config, recovery/health probe scripts, host monitor modules, docs, this guard.
#
# EVERY GLOB HERE IS MATCHED AGAINST THE PATH RELATIVE TO $Root, NEVER AGAINST THE
# ABSOLUTE PATH, and that is a fix rather than a style. A harness session worktree lives
# at `<main checkout>\.claude\worktrees\<id>\`
# (scripts/agent-harness/harness.config.json -> worktree.root), so the absolute path of
# every file inside one contains `\.claude\`. With absolute matching, the
# `*\.claude\*` entry that used to sit in this list allowed the ENTIRE tree:
# the guard filtered every candidate away, read none of them, and exited 0. Measured
# 2026-09-19 by planting a bypass in a worktree and watching it pass - see
# documentation/notes/routing-check-worktree-blindspot-2026-09-19.md.
# check-env-file-scope.ps1 was fixed the same way on 2026-09-20, and
# check-corpus-exposure-producers.ps1 documents why it never copied the glob in.
#
# The globs THEMSELVES are unchanged, and they keep working because Test-Allowed prefixes
# the relative path with a `\` and PowerShell's -like lets `*` match the empty
# string: `*\scripts\checks\stack-watchdog.ps1` still matches that
# file at the root, and `*\documentation\*` still matches a nested
# `OB1\documentation\`. What no longer matches is a directory name that
# happens to appear ABOVE the scan root.
$allowPathLike = @(
    # (path refreshed 2026-09-19: sl-colo-inference moved the gateway config
    #  into the plane, config\ -> inference\config\. Only the BASE config is
    #  allowed; the model_list fragments under inference\config\litellm\ are
    #  deliberately NOT listed, so a bypass planted in a fragment still flags.)
    '*\inference\config\litellm.config.yaml'
    # (paths refreshed 2026-08-21: G.2 moved these under scripts\recovery\
    #  and scripts\checks\; the .bat twin was archived at K.8)
    '*\scripts\recovery\emergency-recovery.ps1'
    '*\scripts\checks\stack-watchdog.ps1'
    '*\scripts\checks\check-backup-coverage.ps1'
    '*\scripts\checks\check-llm-gateway-routing.ps1'
    '*\modules\system-health\*'
    '*\modules\gpu-status\*'
    '*\documentation\*'
    # (`*\.claude\*` was here until 2026-09-20. It is now a TRAVERSAL prune
    #  by directory name below, which expresses the same rule - skip this root's own
    #  .claude tree - in the one place where 'this root's own' is what the words mean.)
    '*\node_modules\*'
    '*\.git\*'
    '*\.next\*'
    '*\data\*'
    '*\notebook_data\*'
    '*-data\*'
    '*\backups\*'
    '*\tiktoken-cache\*'
)

$exts = @('*.yml', '*.yaml', '*.env', '.env', '*.ts', '*.js', '*.py', '*.sh', '*.toml', '*.json', '*.conf')

# The scan root with no trailing separator, so Get-RootRelativePath can cut it off a full
# path and be left with a leading-separator remainder.
$script:RootPrefix = $Root.TrimEnd('\', '/')

# `<full path>` -> `\<path relative to $Root>`. A path that does not start with the
# root is returned whole rather than silently truncated - the walk always starts at $Root,
# but -Root can be handed anything and a mangled path would be matched against, not seen.
function Get-RootRelativePath([string]$path) {
    if ($path.StartsWith($script:RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        $rel = $path.Substring($script:RootPrefix.Length)
    } else {
        $rel = $path
    }
    return '\' + ($rel.Replace('/', '\').TrimStart('\'))
}

function Test-Allowed([string]$path) {
    $rel = Get-RootRelativePath $path
    foreach ($glob in $allowPathLike) { if ($rel -like $glob) { return $true } }
    return $false
}

$violations = New-Object System.Collections.Generic.List[object]

# Directories we never DESCEND into. Pruning at traversal time (not just filtering
# after) is the whole speedup: `Get-ChildItem -Recurse` used to walk every file in
# node_modules/.venv/.testvenv/site-packages (tens of thousands) and ReadAllLines
# each one BEFORE the allow-filter ran, pushing the pre-commit hook past 2 min. These
# trees hold only vendored/generated/data files - no first-party source a bypass
# could live in - so skipping them is a pure win (and .venv site-packages is third-
# party library code we never want to flag). Matched by exact dir name, plus the
# `*-data` volume-mount suffix. Junctions/symlinks are skipped to avoid loops.
#
# `.claude` IS PRUNED HERE, AT TRAVERSAL, AND THAT IS WHAT MAKES IT THIS ROOT'S OWN.
# The walk starts AT $Root, so a directory name can only be pruned when it sits inside
# the tree being scanned: from the main checkout that is `<repo>\.claude\`
# (settings, skills, and `worktrees/<id>/` - whole second copies of this repo, carrying
# another session's in-progress edits); from a worktree it is that worktree's own
# `.claude\`, and the `.claude\` the worktree HANGS OFF is above the root,
# so the walk never reaches it. That is the entire difference from the path glob this
# replaces, and it is the difference between scanning this tree and scanning nothing.
$pruneDirNames = @('.git', '.claude', '.venv', '.testvenv', 'node_modules', '.next',
                   'backups', 'tiktoken-cache', 'notebook_data', 'data')

function Get-ScanFiles {
    param([string]$RootDir, [string[]]$ExtPatterns, [string[]]$PruneNames)
    $results = New-Object System.Collections.Generic.List[string]
    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($RootDir)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try {
            foreach ($sub in [System.IO.Directory]::EnumerateDirectories($dir)) {
                $name = [System.IO.Path]::GetFileName($sub)
                if ($PruneNames -contains $name) { continue }
                if ($name -like '*-data') { continue }
                # don't traverse reparse points (junctions/symlinks) - avoids loops
                # and walking into mounted/duplicated trees.
                if ([System.IO.File]::GetAttributes($sub) -band [System.IO.FileAttributes]::ReparsePoint) { continue }
                $stack.Push($sub)
            }
        } catch { continue }
        try {
            foreach ($file in [System.IO.Directory]::EnumerateFiles($dir)) {
                $leaf = [System.IO.Path]::GetFileName($file)
                foreach ($pat in $ExtPatterns) {
                    if ($leaf -like $pat) { $results.Add($file); break }
                }
            }
        } catch { continue }
    }
    return $results
}

$files = @(Get-ScanFiles -RootDir $Root -ExtPatterns $exts -PruneNames $pruneDirNames |
    Where-Object { -not (Test-Allowed $_) })
$scanned = $files.Count

# A GUARD THAT EXAMINED NOTHING IS NOT GREEN, AND UNTIL 2026-09-20 THIS ONE WAS. That is
# the whole shape of the worktree blind spot: the filter removed every candidate and the
# run still exited 0, so the only thing separating a clean tree from an unexamined one was
# a number nobody printed. It is printed on every verdict below now, and zero refuses.
# check-corpus-exposure-producers.ps1 carries the same assertion, for the same reason.
if ($scanned -eq 0) {
    Write-Host "[check-llm-gateway-routing] FAIL - scanned 0 files under $Root." -ForegroundColor Red
    Write-Host "  Nothing was examined, so this run proves nothing about the tree. Check the" -ForegroundColor Red
    Write-Host "  scan root and the prune/allow lists before reading any green here as cover." -ForegroundColor Red
    exit 1
}

foreach ($f in $files) {
    $n = 0
    try {
        $lines = [System.IO.File]::ReadAllLines($f)
    } catch {
        continue   # locked / in-use / binary - skip
    }
    foreach ($line in $lines) {
        $n++
        $trimmed = $line.TrimStart()
        if ($trimmed.StartsWith('#') -or $trimmed.StartsWith('//')) { continue }   # comments
        # Sanctioned: the llm-queue admission controller's forward target (section 3.2).
        if ($line -match $queueUpstreamAllow) { continue }
        if ($line -match $badPattern) {
            $rel = $f.Substring($Root.Length).TrimStart('\')
            $violations.Add([pscustomobject]@{ File = $rel; Line = $n; Text = $line.Trim() })
        }
    }
}

if ($violations.Count -eq 0) {
    Write-Host "[check-llm-gateway-routing] OK - no LLM gateway bypasses found. $scanned file(s) scanned under $Root." -ForegroundColor Green
    exit 0
}

Write-Host "[check-llm-gateway-routing] FAIL - $($violations.Count) gateway bypass(es) found in $scanned file(s) scanned under ${Root}:" -ForegroundColor Red
Write-Host "  An inference/serve endpoint points at a *-upstream real server instead of the" -ForegroundColor Red
Write-Host "  gateway alias (llama-cpp / llama-cpp-embed). Route it through llm-gateway.`n" -ForegroundColor Red
foreach ($v in $violations) {
    Write-Host ("  {0}:{1}" -f $v.File, $v.Line) -ForegroundColor Yellow
    Write-Host ("      {0}" -f $v.Text)
}
Write-Host "`n  If a hit is a legitimate health/recovery probe, add its path to `$allowPathLike." -ForegroundColor DarkGray
exit 1
