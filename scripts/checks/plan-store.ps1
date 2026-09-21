#requires -Version 5
<#
.SYNOPSIS
  The plan store in one command: audit both repos, or migrate a feature's plan set
  out of the code repo into documentation-plans-ai-stack and publish it.

.DESCRIPTION
  Companion to check-doc-placement.ps1. That check answers "is planning material
  in the wrong repo?"; this script answers "what do I do about it?" and closes the
  half the pre-commit hook cannot see: plan sets left UNTRACKED in either repo, and
  a plan store sitting ahead of origin with nothing pushed.

  -Audit (default)
     1. check-doc-placement.ps1 -All in the code repo (tracked backlog + untracked
        plan-shaped paths).
     2. In the plan store: untracked directories under implementation-guide/, and
        how many commits main is ahead of origin/main.
     3. Feature directories present in the plan store that have NO status row in
        documentation/implementation-guide/README.md (the seam that must span both
        repos).
     Exit 0 when everything is versioned, pushed and indexed; 1 otherwise. Run it at
     the start of a planning session and before you stop.

  -Migrate <feature>
     Moves documentation/implementation-guide/<feature>/ from the code repo into the
     plan store (refusing if the directory is TRACKED in the code repo - Phase 2 is a
     decision, not a cleanup), rewrites the moved files' self-references from
     documentation/implementation-guide/<feature>/ to
     ../documentation-plans-ai-stack/implementation-guide/<feature>/, prints the
     index row you still have to add by hand, then commits and pushes the store
     unless -NoPush. Nothing in the code repo is staged or committed.

.PARAMETER Store
  Path to the plan store checkout. Defaults to ../documentation-plans-ai-stack
  beside the code repo root.

.EXAMPLE
  .\scripts\checks\plan-store.ps1
  .\scripts\checks\plan-store.ps1 -Migrate research-workbench
  .\scripts\checks\plan-store.ps1 -Migrate my-feature -NoPush

.NOTES
  ASCII, no BOM, PowerShell 5.1. Writes only inside the plan store and the moved
  directory. The commit trailer is the workspace's standard one.
#>
[CmdletBinding(DefaultParameterSetName = 'Audit')]
param(
    [Parameter(ParameterSetName = 'Audit')] [switch]$Audit,
    [Parameter(ParameterSetName = 'Migrate', Mandatory = $true)] [string]$Migrate,
    [Parameter(ParameterSetName = 'Migrate')] [switch]$NoPush,
    [string]$Store
)

$ErrorActionPreference = 'Stop'

$Root = (git rev-parse --show-toplevel 2>$null)
if (-not $Root) { throw "not inside the code repo" }
$Root = $Root.Trim()
# WHERE THE STORE IS WHEN THIS RUNS FROM A WORKTREE. $Root is the toplevel of the checkout
# we are standing in, and for a harness session worktree that is
# `<repo>\.claude\worktrees\<id>` - whose parent is `...\worktrees`, not the directory the
# plan store was cloned beside. Measured 2026-09-20: every worktree run died with
# "plan store not found at D:\...\.claude\worktrees\documentation-plans-ai-stack", and
# CLAUDE.md asks every planning session - which is a worktree session whenever it will
# commit - to run this at its start and again before it stops.
#
# `git rev-parse --git-common-dir` answers with the SHARED git directory: an absolute path
# to the MAIN checkout's .git from a worktree, and a bare relative '.git' from the main
# checkout itself (which is why it is resolved against $Root before its parent is taken).
# So its parent is the main checkout, and the store sits beside THAT. The checkout's own
# parent is still tried first, so a plain clone with the store beside it is unaffected and
# -Store still overrides everything.
if (-not $Store) {
    $storeDirs = @(Split-Path $Root -Parent)
    $common = (git rev-parse --git-common-dir 2>$null)
    if ($common) {
        $common = $common.Trim()
        if (-not [System.IO.Path]::IsPathRooted($common)) { $common = Join-Path $Root $common }
        $mainRoot = Split-Path $common -Parent
        if ($mainRoot) { $storeDirs += (Split-Path $mainRoot -Parent) }
    }
    foreach ($dir in $storeDirs) {
        if (-not $dir) { continue }
        $candidate = Join-Path $dir 'documentation-plans-ai-stack'
        if (Test-Path (Join-Path $candidate '.git')) { $Store = $candidate; break }
    }
    # Nothing found: keep the checkout-relative path so the refusal below names the place a
    # plain clone would have put it.
    if (-not $Store) { $Store = Join-Path (Split-Path $Root -Parent) 'documentation-plans-ai-stack' }
}
if (-not (Test-Path (Join-Path $Store '.git'))) { throw "plan store not found at $Store (clone devonpveller/documentation-plans-ai-stack beside the code repo)" }

$IndexPath = Join-Path $Root 'documentation/implementation-guide/README.md'

function Get-StoreFeatures {
    Get-ChildItem (Join-Path $Store 'implementation-guide') -Directory | ForEach-Object { $_.Name }
}

function Invoke-Audit {
    $bad = 0
    Write-Host "== code repo: check-doc-placement -All"
    & (Join-Path $Root 'scripts/checks/check-doc-placement.ps1') -All -Root $Root
    if ($LASTEXITCODE -ne 0) { $bad++ }

    Write-Host "== plan store: $Store"
    Push-Location $Store
    try {
        $untracked = git ls-files --others --exclude-standard -- implementation-guide | ForEach-Object { ($_ -split '/')[1] } | Sort-Object -Unique
        if ($untracked) { Write-Host "  UNTRACKED plan directories (unversioned, one clean from gone):"; $untracked | ForEach-Object { Write-Host "    $_" }; $bad++ }
        $ahead = 0
        try { $ahead = [int](git rev-list --count 'origin/main..main' 2>$null) } catch { $ahead = 0 }
        if ($ahead -gt 0) { Write-Host "  main is $ahead commit(s) ahead of origin/main - push it"; $bad++ }
        $dirty = git status --porcelain -- implementation-guide
        if ($dirty) { Write-Host "  modified but uncommitted plan files present"; $bad++ }
    } finally { Pop-Location }

    Write-Host "== seam: status rows in $IndexPath"
    $index = Get-Content $IndexPath -Raw
    foreach ($f in Get-StoreFeatures) {
        if ($index -notmatch [regex]::Escape($f)) { Write-Host "  plan store feature '$f' has NO status row in the index"; $bad++ }
    }

    if ($bad -eq 0) { Write-Host "plan store: clean (versioned, pushed, indexed)"; exit 0 }
    Write-Host "plan store: $bad problem group(s) - see above"
    exit 1
}

function Invoke-Migrate([string]$feature) {
    if ($feature -notmatch '^[a-z0-9][a-z0-9-]{0,63}$') { throw "feature name must be kebab-case: $feature" }
    $srcRel = "documentation/implementation-guide/$feature"
    $src = Join-Path $Root $srcRel
    $dst = Join-Path $Store "implementation-guide/$feature"
    if (-not (Test-Path $src)) { throw "nothing at $srcRel" }
    if (Test-Path $dst) { throw "plan store already has implementation-guide/$feature - merge by hand" }
    $tracked = git -C $Root ls-files -- $srcRel
    if ($tracked) { throw "$srcRel is TRACKED in the code repo; moving tracked plans is Phase 2 (a decision). Refusing." }

    Move-Item -LiteralPath $src -Destination $dst
    $old = "documentation/implementation-guide/$feature/"
    $new = "../documentation-plans-ai-stack/implementation-guide/$feature/"
    $rewritten = 0
    Get-ChildItem $dst -Recurse -File | ForEach-Object {
        $text = [IO.File]::ReadAllText($_.FullName)
        if ($text.Contains($old)) {
            [IO.File]::WriteAllText($_.FullName, $text.Replace($old, $new), (New-Object Text.UTF8Encoding($false)))
            $rewritten++
        }
    }
    Write-Host "moved $srcRel -> $dst ($rewritten file(s) had self-references rewritten)"

    Push-Location $Store
    try {
        git add -- "implementation-guide/$feature"
        $msgFile = Join-Path $env:TEMP ("plan-store-" + $feature + ".txt")
        @(
            "$feature`: plan set moved into the plan store from the code repo",
            "",
            "Moved by scripts/checks/plan-store.ps1 -Migrate. Self-references rewritten to",
            "../documentation-plans-ai-stack/implementation-guide/$feature/. The code repo",
            "keeps only the status row in documentation/implementation-guide/README.md.",
            "",
            "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
        ) | Set-Content -Path $msgFile -Encoding ASCII
        git commit -F $msgFile | Out-Host
        Remove-Item $msgFile -ErrorAction SilentlyContinue
        if (-not $NoPush) { git push origin main | Out-Host }
    } finally { Pop-Location }

    Write-Host ""
    Write-Host "NOW add or update the status row in $IndexPath (the seam), e.g.:"
    Write-Host ('| `' + $feature + '/` -> lives in the `documentation-plans-ai-stack` private repo (' + (Get-Date -Format 'yyyy-MM-dd') + ') | <status glyph + one line> | <what it is; which notes in documentation/notes/ it grew from> |')
}

if ($PSCmdlet.ParameterSetName -eq 'Migrate') { Invoke-Migrate $Migrate } else { Invoke-Audit }
