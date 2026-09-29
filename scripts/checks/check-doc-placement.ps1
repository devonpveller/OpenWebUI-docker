#requires -Version 5
<#
.SYNOPSIS
  Keep NEW planning material and NEW operator journal out of the code repo - both
  belong in the private plan store, documentation-plans-ai-stack.

.DESCRIPTION
  THE RULE (CLAUDE.md, "Where documentation goes - the plan store", and its routing
  table): a plan, a build log, a task list or a numbered plan set is written in the
  sibling repo `documentation-plans-ai-stack` under implementation-guide/<feature>/;
  so is the operator journal - notes, findings, evidence, test plans, retired docs -
  under implementation-guide/<feature>/findings|test-plans/ or journal/. The code repo
  carries only a one-row status entry per feature in
  `documentation/implementation-guide/README.md`.

  WHAT IT REFUSES at commit time (staged additions; renames and copies count for the
  journal rules):
    1. a new file under documentation/notes/, documentation/evidence/ or
       documentation/archive/ - the three journal directories that moved to the store
       on 2026-09-25 (adoption-closeout ac-journal-move). The refusal names the store
       path to use instead.
    2. a new ROOT-LEVEL plan or journal DOCUMENT (files only, not directories): names starting PLAN, TEST-PLAN (TEST_PLAN, TESTPLAN), CLEANUP-PLAN, BUILD-LOG or
       FINDINGS, or carrying -FINDINGS / _FINDINGS - as a plain prefix for .md .markdown
       .rst .adoc and extensionless names, at a word boundary for .txt, never for code or
       config extensions; and TASKS.md / ROADMAP.md / bare TASKS / ROADMAP. See the
       $RootDocExt block for the history. Rules 1 and 2 admit NO exemption.
    3. under documentation/implementation-guide/: a file in a NEW feature directory,
       or a plan-shaped file (plan / build log / task list / NN-name.md) anywhere.

  WHY THIS EXISTS. The plan store was created 2026-08-29 precisely because plan
  directories were accumulating UNTRACKED in the code repo - unversioned, and one
  `git clean` from gone. By 2026-09-18 a 24-file, 1300-line plan set had been written to
  `documentation/implementation-guide/research-workbench/` anyway, while the store sat
  unpushed. Rule 3 is that lesson. Rules 1 and 2 are the 2026-09-25 one: by then about
  92k of the code repo's ~121k markdown lines were operator journal, burying the docs a
  newcomer needs; the journal moved, and without a gate it would start growing back the
  next time an agent wrote a finding where it was standing.

  WHAT THIS CHECK CANNOT SEE, and it is the larger half. A pre-commit hook fires on
  STAGED content. Material written into the working tree and left there never stages.
  That half is reported by -All (and by scripts/checks/plan-store.ps1, which runs it).

  Legacy feature directories already tracked under implementation-guide/ are NOT blocked
  (editing established work is not new material). The journal directories have no legacy
  exemption: they are empty in the tree since the move, so anything in them is new.

.PARAMETER All
  Audit the whole tree instead of the staged diff: tracked feature directories, any
  journal file still TRACKED in the code repo, and UNTRACKED plan- or journal-shaped
  paths that no commit-time check will ever see. Exit 1 when journal or untracked plan
  material is present.

.PARAMETER Root
  Repo root. Defaults to `git rev-parse --show-toplevel`.

.NOTES
  Escape hatch: set AI_STACK_PLAN_IN_CODE_REPO=1 for a deliberate exception (material
  that must travel with the checkout, like MERGE-PROTOCOL.md). It warns and passes, and
  the reason belongs in the commit message.

  Every verdict says how many paths it examined. Exit 0 = clean, 1 = material in the
  wrong repo.
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
$StoreRel = '../documentation-plans-ai-stack'

# Names that are planning material wherever they appear under implementation-guide/.
$PlanShaped = '(?i)(^|/)(\d{2}-[^/]+\.md|[^/]*PLAN[^/]*\.md|BUILD-LOG\.md|TASKS?\.md|ROADMAP\.md|PHASE[^/]*\.md)$'

# The journal directories that moved to the store (2026-09-25), and where each goes.
$JournalDirs = [ordered]@{
    'documentation/notes/'    = 'journal/notes/'
    'documentation/evidence/' = 'journal/evidence/'
    'documentation/archive/'  = 'journal/archive/'
}

# Root-level plan / journal files. History, because each version was wrong in a direction a
# tester measured:
#   attempt 1 matched `.md` only - TEST-PLAN-foo.txt, TEST-PLAN-foo, PLAN-foo.txt walked
#             through while every routing surface promised refusal (probe X1);
#   attempt 2 took ANY extension and a bare prefix - tasks.py, Tasks.json, PLANNER.py,
#             planets.txt, plantuml.cfg, roadmap.png ... would have been refused (A2-2);
#   attempt 3 required a word boundary after the stem - PLANS.md, PLANNING.md, Test-Plans.md,
#             BUILD-LOGS.md, 'PLAN v2.md', PLAN(1).md walked through (A3-1).
# The rule now (orchestrator decision after A3-1), case-insensitive throughout:
#   * DOCUMENT extension (.md .markdown .rst .adoc) or NO extension: the stem is a plain
#     PREFIX - PLAN, TEST-PLAN, TEST_PLAN, TESTPLAN, CLEANUP-PLAN, BUILD-LOG, FINDINGS - or
#     FINDINGS appears after `-` / `_`. So PLANNING.md and FINDINGS.md are refused.
#   * .txt: the same stems but only at a word boundary (followed by `-`, `_`, `.` or the end),
#     so planets.txt passes and PLAN-foo.txt does not.
#   * any other extension (.py .json .cfg .yml .png ...): never. PLANNER.py passes ONLY
#     because .py is not a document extension - PLANNER.md would be refused.
#   * TASKS.md / ROADMAP.md / bare TASKS / ROADMAP, exactly.
#   * FILES AT THE ROOT ONLY. Attempt 4 also refused a root-level DIRECTORY named with a stem;
#     that refused code folders sharing a prefix (planner/__init__.py, plantuml/x.cfg,
#     Plans/tool.py - A4-1) and the anchor scopes this rule to files, so it was dropped
#     (orchestrator decision). A plan-set directory at the root is therefore NOT refused.
$RootDocExt   = @('md', 'markdown', 'rst', 'adoc')
$RootStems    = '(TEST[-_]?PLAN|CLEANUP-PLAN|BUILD-LOG|PLAN|FINDINGS)'
$RootPrefix   = '(?i)^(' + $RootStems + '|.*[-_]FINDINGS)'
$RootWord     = '(?i)^(' + $RootStems + '|.*[-_]FINDINGS)([-_.].*)?$'
$RootExactDoc = '(?i)^(TASKS?|ROADMAP)(\.md)?$'

function Test-RootJournal([string]$p) {
    if ($p -match '/') { return $false }   # root FILES only - see the note above
    $name = $p
    if ($name -match $RootExactDoc) { return $true }
    $dot = $name.LastIndexOf('.')
    if ($dot -le 0) { return ($name -match $RootPrefix) }
    $ext = $name.Substring($dot + 1).ToLowerInvariant()
    $base = $name.Substring(0, $dot)
    if ($RootDocExt -contains $ext) { return ($base -match $RootPrefix) }
    if ($ext -eq 'txt') { return ($base -match $RootWord) }
    return $false
}

# Deliberately kept in the code repo (plan store README, "What stays in the code repo").
# THESE EXEMPT FROM THE implementation-guide RULES ONLY. They are consulted after the journal
# and root rules have had their say, so no exemption can admit a new file under
# documentation/notes|evidence|archive or a root journal file. Each is pinned to the exact
# place it names: the MERGE-PROTOCOL entry used to be `(^|/)MERGE-PROTOCOL\.md$`, matching at
# any depth, which let documentation/notes/MERGE-PROTOCOL.md through (A2-1).
$Exempt = @(
    '(?i)^documentation/implementation-guide/README\.md$',
    '(?i)^documentation/implementation-guide/multi-agent-concurrency/',
    '(?i)^documentation/implementation-guide/multi-agent-concurrency/MERGE-PROTOCOL\.md$'
)

function Test-Exempt([string]$path) {
    foreach ($rx in $Exempt) { if ($path -match $rx) { return $true } }
    return $false
}

# The store path a journal-shaped file should have been written to, or $null.
function Get-JournalTarget([string]$p) {
    foreach ($k in $JournalDirs.Keys) {
        if ($p.StartsWith($k, [System.StringComparison]::OrdinalIgnoreCase)) {
            return ($StoreRel + '/' + $JournalDirs[$k] + $p.Substring($k.Length))
        }
    }
    if (Test-RootJournal $p) {
        if ($p -match '(?i)^TEST[-_]?PLAN') { return ($StoreRel + '/implementation-guide/<feature>/test-plans/' + $p + '  (or journal/test-plans/' + $p + ')') }
        if ($p -match '(?i)(^|[-_])FINDINGS') { return ($StoreRel + '/implementation-guide/<feature>/findings/' + $p + '  (or journal/notes/' + $p + ')') }
        return ($StoreRel + '/implementation-guide/<feature>/' + $p)
    }
    return $null
}

function Split-Nul([string]$text) {
    if (-not $text) { return @() }
    return @($text -split "`0" | Where-Object { $_ })
}

Push-Location $Root
try {
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
        Write-Host ("Tracked feature directories under implementation-guide/: " + $knownDirs.Count) -ForegroundColor Yellow
        $bad = 0

        $tracked = Split-Nul ((& git ls-files -z) -join "`0")
        $trackedJournal = @($tracked | Where-Object { Get-JournalTarget $_ })
        Write-Host ("Tracked files examined for journal placement: " + $tracked.Count)
        if ($tracked.Count -eq 0) {
            Write-Host "FAIL: git ls-files returned nothing - this audit examined no files, which is not a clean result." -ForegroundColor Red
            exit 1
        }
        if ($trackedJournal.Count -gt 0) {
            $bad++
            Write-Host ""
            Write-Host "TRACKED journal material in the code repo (belongs in the plan store):" -ForegroundColor Red
            foreach ($p in $trackedJournal) { Write-Host ("  " + $p + "  ->  " + (Get-JournalTarget $p)) -ForegroundColor Red }
        }

        $untracked = Split-Nul ((& git ls-files -z --others --exclude-standard) -join "`0")
        $misplaced = @()
        foreach ($p in $untracked) {
            if (Get-JournalTarget $p) { $misplaced += $p; continue }
            if (Test-Exempt $p) { continue }
            if ($p -like ($GuidePrefix + '*')) { $misplaced += $p }
        }
        Write-Host ("Untracked files examined: " + $untracked.Count)
        if ($misplaced.Count -gt 0) {
            $bad++
            Write-Host ""
            Write-Host "UNTRACKED plan or journal material - unversioned, and no commit-time check can see these:" -ForegroundColor Red
            foreach ($p in $misplaced) {
                $t = Get-JournalTarget $p
                if ($t) { Write-Host ("  " + $p + "  ->  " + $t) -ForegroundColor Red }
                else    { Write-Host ("  " + $p) -ForegroundColor Red }
            }
            Write-Host ""
            Write-Host "Move them to documentation-plans-ai-stack (commit AND push there)." -ForegroundColor Yellow
        } else {
            Write-Host "No untracked plan or journal material." -ForegroundColor Green
        }
        if ($bad -gt 0) { exit 1 }
        exit 0
    }

    # Journal rules count renames and copies INTO the journal places; the
    # implementation-guide rules keep their original added-only scope.
    $stagedA   = Split-Nul ((& git diff --cached --name-only -z --diff-filter=A) -join "`0")
    $stagedACR = Split-Nul ((& git diff --cached --name-only -z -M --diff-filter=ACR) -join "`0")
} finally {
    Pop-Location
}

$violations = @()
foreach ($p in $stagedACR) {
    # NO exemption is consulted here - see the note on $Exempt.
    $target = Get-JournalTarget $p
    if ($target) {
        $violations += [pscustomobject]@{ Path = $p; Why = 'operator journal (notes / findings / evidence / test plans / archive / closed plans)'; Use = $target }
    }
}
foreach ($p in $stagedA) {
    if (Test-Exempt $p) { continue }
    if (-not ($p -like ($GuidePrefix + '*'))) { continue }
    $rest = $p.Substring($GuidePrefix.Length)
    $dir = ($rest -split '/')[0]
    if ($rest -match '/' -and -not $knownDirs.ContainsKey($dir.ToLowerInvariant())) {
        $violations += [pscustomobject]@{ Path = $p; Why = "new feature directory '$dir' in the code repo"; Use = ($StoreRel + '/implementation-guide/' + $rest) }
        continue
    }
    if ($p -match $PlanShaped) {
        $violations += [pscustomobject]@{ Path = $p; Why = 'planning material (plan / build log / task list / numbered plan set)'; Use = ($StoreRel + '/implementation-guide/<feature>/' + (Split-Path $p -Leaf)) }
    }
}

$examined = @($stagedACR + $stagedA | Sort-Object -Unique).Count
if ($violations.Count -eq 0) {
    Write-Host ("SUCCESS: no new planning or journal material staged into the code repo ({0} staged addition(s)/rename(s) examined)" -f $examined) -ForegroundColor Green
    exit 0
}

$escape = $env:AI_STACK_PLAN_IN_CODE_REPO
if ($escape -eq '1' -or $escape -eq 'true') {
    Write-Host "WARNING: planning or journal material staged into the code repo, allowed by AI_STACK_PLAN_IN_CODE_REPO:" -ForegroundColor Yellow
    foreach ($v in $violations) { Write-Host ("  " + $v.Path + "  (" + $v.Why + ")") -ForegroundColor Yellow }
    Write-Host "State the reason in the commit message." -ForegroundColor Yellow
    exit 0
}

Write-Host ""
Write-Host ("FAIL: {0} of {1} staged path(s) belong in documentation-plans-ai-stack, not here." -f $violations.Count, $examined) -ForegroundColor Red
foreach ($v in $violations) {
    Write-Host ("  " + $v.Path) -ForegroundColor Red
    Write-Host ("      " + $v.Why) -ForegroundColor DarkGray
    Write-Host ("      write it at: " + $v.Use) -ForegroundColor Yellow
}
Write-Host ""
Write-Host "The plan store is the sibling checkout <workspace>\documentation-plans-ai-stack\:" -ForegroundColor Yellow
Write-Host "  implementation-guide/<feature>/            plans, build logs, task lists" -ForegroundColor Yellow
Write-Host "  implementation-guide/<feature>/findings/   a work item's findings sink" -ForegroundColor Yellow
Write-Host "  implementation-guide/<feature>/test-plans/ a work item's test plan" -ForegroundColor Yellow
Write-Host "  journal/notes|evidence|archive|test-plans/ anything with no feature" -ForegroundColor Yellow
Write-Host "Commit AND push it there (git pull --rebase first), then add or update the feature's" -ForegroundColor Yellow
Write-Host "status row here in documentation/implementation-guide/README.md." -ForegroundColor Yellow
Write-Host "Deliberate exception: AI_STACK_PLAN_IN_CODE_REPO=1, with the reason in the commit message." -ForegroundColor DarkGray
exit 1
