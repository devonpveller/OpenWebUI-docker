#requires -Version 5
<#
.SYNOPSIS
  Keep NEW planning material out of the code repo - it belongs in the private
  plan store, documentation-plans-ai-stack.

.DESCRIPTION
  THE RULE (CLAUDE.md, "Plans live in the plan store"): a plan, a build log, a
  task list or a numbered plan set is written in the sibling repo
  `documentation-plans-ai-stack`; the code repo carries only a one-row status
  entry for it in `documentation/implementation-guide/README.md`.

  WHY THIS EXISTS. The plan store was created 2026-08-29 precisely because plan
  directories were accumulating UNTRACKED in the code repo - unversioned, and one
  `git clean` from gone. Its README wrote the routing rule down, and the rule then
  held for nothing: by 2026-09-18 a 24-file, 1300-line plan set had been written to
  `documentation/implementation-guide/research-workbench/` in this repo, while three
  September plan directories sat untracked in the plan store itself and that repo's
  last commit (2026-08-30) had never been pushed. Nothing anywhere failed. CLAUDE.md
  - the file every agent reads first - did not mention the plan store at all, so each
  session either rediscovered it from a note or wrote its plan wherever it was
  standing.

  WHAT THIS CHECK CANNOT SEE, and it is the larger half. A pre-commit hook fires on
  STAGED content. The research-workbench drift never staged anything: the files were
  written into the working tree and left there. This gate would not have fired on it,
  and will not fire on the next one either. It closes the path where drift gets
  COMMITTED into the code repo permanently; the path where it rots untracked is
  closed by the policy in CLAUDE.md and by running this script with -All, which
  reports both.

  Legacy directories already tracked here are NOT blocked. Migrating them is the plan
  store's Phase 2 (a decision, not a cleanup), and a guard that fails on pre-existing
  debt gets switched off, after which it guards nothing.

.PARAMETER All
  Audit the whole tree instead of the staged diff: tracked feature directories (the
  Phase-2 backlog) and, more importantly, UNTRACKED plan-shaped paths that no
  commit-time check will ever see.

.PARAMETER Root
  Repo root. Defaults to `git rev-parse --show-toplevel`.

.NOTES
  Escape hatch: set AI_STACK_PLAN_IN_CODE_REPO=1 for a deliberate exception (a plan
  that must travel with the checkout, like MERGE-PROTOCOL.md). It warns and passes,
  and the reason belongs in the commit message.

  Exit code 0 = clean, 1 = new planning material staged into the code repo.
#>
[CmdletBinding()]
param(
    [switch]$All,
    [string]$Root
)

$ErrorActionPreference = 'Stop'

if (-not $Root) { $Root = (git rev-parse --show-toplevel 2>$null) }
if (-not $Root) { $Root = (Get-Location).Path }
$Root = $Root.Trim()

$GuidePrefix = 'documentation/implementation-guide/'

# Names that are planning material wherever they appear: the plan itself, its build
# log, its task list, and the NN-NAME.md numbered plan-set convention.
$PlanShaped = '(?i)(^|/)(\d{2}-[^/]+\.md|[^/]*PLAN[^/]*\.md|BUILD-LOG\.md|TASKS?\.md|ROADMAP\.md|PHASE[^/]*\.md)$'

# Deliberately kept in the code repo (plan store README, "What stays in the code repo").
$Exempt = @(
    '(?i)^documentation/implementation-guide/README\.md$',
    '(?i)^documentation/implementation-guide/multi-agent-concurrency/',
    '(?i)(^|/)MERGE-PROTOCOL\.md$'
)

function Test-Exempt([string]$path) {
    foreach ($rx in $Exempt) { if ($path -match $rx) { return $true } }
    return $false
}

Push-Location $Root
try {
    # Feature directories that already exist in HEAD. An added file under one of these
    # is an edit to established work; an added file under a NEW one is a whole plan
    # directory landing in the wrong repo.
    $headEntries = @()
    $headOut = & git ls-tree --name-only HEAD $GuidePrefix 2>$null
    if ($LASTEXITCODE -eq 0 -and $headOut) { $headEntries = @($headOut) }
    $knownDirs = @{}
    foreach ($e in $headEntries) {
        $leaf = ($e -replace [regex]::Escape($GuidePrefix), '').TrimEnd('/')
        if ($leaf) { $knownDirs[$leaf.ToLowerInvariant()] = $true }
    }

    if ($All) {
        Write-Host "Documentation placement audit" -ForegroundColor Cyan
        Write-Host ""
        Write-Host ("Tracked feature directories here (plan store Phase-2 backlog): " + $knownDirs.Count) -ForegroundColor Yellow

        $untracked = @()
        $porcelain = & git status --porcelain --untracked-files=all -- $GuidePrefix 2>$null
        foreach ($line in @($porcelain)) {
            if ($line -match '^\?\?\s+(.*)$') {
                $p = $Matches[1].Trim('"')
                if (-not (Test-Exempt $p)) { $untracked += $p }
            }
        }
        if ($untracked.Count -gt 0) {
            Write-Host ""
            Write-Host "UNTRACKED under implementation-guide - unversioned, and no commit-time check can see these:" -ForegroundColor Red
            foreach ($p in $untracked) { Write-Host ("  " + $p) -ForegroundColor Red }
            Write-Host ""
            Write-Host "Move them to documentation-plans-ai-stack, or commit them deliberately with a reason." -ForegroundColor Yellow
        } else {
            Write-Host "No untracked paths under implementation-guide." -ForegroundColor Green
        }
        exit 0
    }

    $staged = @()
    $stagedOut = & git diff --cached --name-only --diff-filter=A 2>$null
    if ($stagedOut) { $staged = @($stagedOut) }
} finally {
    Pop-Location
}

$violations = @()
foreach ($path in $staged) {
    $p = $path.Trim().Trim('"')
    if (-not $p) { continue }
    if (Test-Exempt $p) { continue }
    if (-not ($p -like ($GuidePrefix + '*'))) { continue }

    $rest = $p.Substring($GuidePrefix.Length)
    $dir = ($rest -split '/')[0]
    if ($rest -match '/' -and -not $knownDirs.ContainsKey($dir.ToLowerInvariant())) {
        $violations += [pscustomobject]@{ Path = $p; Why = "new feature directory '$dir' in the code repo" }
        continue
    }

    if ($p -match $PlanShaped) {
        $violations += [pscustomobject]@{ Path = $p; Why = 'planning material (plan / build log / task list / numbered plan set)' }
    }
}

if ($violations.Count -eq 0) {
    Write-Host "SUCCESS: no new planning material staged into the code repo" -ForegroundColor Green
    exit 0
}

$escape = $env:AI_STACK_PLAN_IN_CODE_REPO
if ($escape -eq '1' -or $escape -eq 'true') {
    Write-Host "WARNING: planning material staged into the code repo, allowed by AI_STACK_PLAN_IN_CODE_REPO:" -ForegroundColor Yellow
    foreach ($v in $violations) { Write-Host ("  " + $v.Path + "  (" + $v.Why + ")") -ForegroundColor Yellow }
    Write-Host "State the reason in the commit message." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host "FAIL: planning material belongs in documentation-plans-ai-stack, not here." -ForegroundColor Red
foreach ($v in $violations) {
    Write-Host ("  " + $v.Path) -ForegroundColor Red
    Write-Host ("      " + $v.Why) -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "Write it in the plan store instead:" -ForegroundColor Yellow
Write-Host "  <workspace>\documentation-plans-ai-stack\implementation-guide\<feature>\" -ForegroundColor Yellow
Write-Host "  commit AND push it there (that repo has been left unpushed before)," -ForegroundColor Yellow
Write-Host "  then add one status row here in documentation/implementation-guide/README.md." -ForegroundColor Yellow
Write-Host ""
Write-Host "Findings and evidence still belong in this repo (documentation/notes/)." -ForegroundColor Yellow
Write-Host "Deliberate exception: AI_STACK_PLAN_IN_CODE_REPO=1, with the reason in the commit message." -ForegroundColor DarkGray
exit 1
