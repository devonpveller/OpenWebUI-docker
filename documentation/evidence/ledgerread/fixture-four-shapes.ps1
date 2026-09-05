# fixture-four-shapes.ps1 - the construction proof for item 'ledgerread'.
#
# WHY A FIXTURE AND NOT AN ARGUMENT. Three patches on this line of work read correctly and
# did nothing; both were caught by building the input and running the real code over it.
# The two PS 5.1 traps that ate them - $Matches clobbered by a nested -match, and an empty
# object's member enumeration yielding $null so @(...).Count is 1 rather than 0 - are
# invisible to reading and obvious to running.
#
# WHAT IT BUILDS. A scratch git repository under $env:TEMP whose branch carries, on purpose,
# one commit per column-4 state plus a REAL merge commit, and a crafted ledger naming their
# trees. It then drives the REAL check-hook-attestation.ps1 over it.
#
#   (b) 40 hex     -> commit a-hex.txt        column 4 = the real pre-commit blob 519ad9f...
#   (c) '?'        -> commit b-question.txt   column 4 = the hook's degradation sentinel
#   (d) MALFORMED  -> commit c-branchname.txt column 4 = work/u5proxy, copied verbatim from
#                                             the real ledger's line 124 (the reverted
#                                             commit-msg attester bd4d891, 2026-08-30)
#   (a) ABSENT     -> commit d-threecol.txt   a three-column line, no column 4 at all
#   merge          -> a --no-ff merge of a side branch, attested with a 40-hex hash
#
# IT TOUCHES NOTHING REAL. No write leaves $env:TEMP; the ai-stack repository and its
# .git/hook-attest.log are never opened. The fixture repo sets core.hooksPath to a directory
# that does not exist, so no commit below needs --no-verify (which the harness forbids) -
# there are simply no hooks to skip.
#
# USE:
#   .\fixture-four-shapes.ps1 -Script <path to check-hook-attestation.ps1>
#   .\fixture-four-shapes.ps1 -Script <path> -AddUnattested   # expect exit 1
#   .\fixture-four-shapes.ps1 -Script <path> -Json            # the queue.ps1 consumer shape
#
# Point -Script at the version you edited, and then at the version you did not, to see the
# four states collapse back into four bare values under one "gated by N distinct hook
# file(s)" heading. That A/B is the regression proof.
param(
    [Parameter(Mandatory = $true)][string]$Script,
    [switch]$AddUnattested,
    [switch]$Json
)
$ErrorActionPreference = 'Stop'

function G {
    # Never 2>&1 a native command in PS 5.1 with $ErrorActionPreference='Stop': git's
    # ordinary stderr chatter becomes a terminating error. Flip the preference instead.
    param([string[]]$a)
    $p = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & git @a 2>$null
        if ($LASTEXITCODE -ne 0) { throw ("git " + ($a -join ' ') + " -> exit " + $LASTEXITCODE) }
        return $out
    } finally { $ErrorActionPreference = $p }
}

$root = Join-Path $env:TEMP ("ledgerread-fx-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $root | Out-Null
G @('-C', $root, 'init', '-q') | Out-Null
G @('-C', $root, 'config', 'user.name', 'fixture') | Out-Null
G @('-C', $root, 'config', 'user.email', 'fixture@example.invalid') | Out-Null
G @('-C', $root, 'config', 'core.hooksPath', '.no-such-hooks-dir') | Out-Null
G @('-C', $root, 'checkout', '-q', '-b', 'base') | Out-Null

New-Item -ItemType Directory -Path (Join-Path $root '.githooks') | Out-Null
# The checker's per-branch activation gate greps the BRANCH's own hook for 'hook-attest.log'
# and skips the branch if it is absent; the merge gate needs .githooks/pre-merge-commit to
# exist AT THE FORK POINT or merges are dropped from rev-list. Both must be here, on base.
Set-Content -Path (Join-Path $root '.githooks/pre-commit') -Encoding ascii -Value @(
    '#!/bin/sh',
    ': >> "$_git_common/hook-attest.log"')
Set-Content -Path (Join-Path $root '.githooks/pre-merge-commit') -Encoding ascii -Value @(
    '#!/bin/sh',
    'exec "$(dirname "$0")/pre-commit"')
G @('-C', $root, 'add', '-A') | Out-Null
G @('-C', $root, 'commit', '-q', '-m', 'base: hooks present at the fork point') | Out-Null

G @('-C', $root, 'checkout', '-q', '-b', 'work') | Out-Null
function Commit1 {
    param([string]$Name)
    Set-Content -Path (Join-Path $root $Name) -Value $Name -Encoding ascii
    G @('-C', $root, 'add', '-A') | Out-Null
    G @('-C', $root, 'commit', '-q', '-m', ("commit " + $Name)) | Out-Null
    return (G @('-C', $root, 'rev-parse', 'HEAD^{tree}') | Select-Object -First 1)
}
$treeHex       = Commit1 'a-hex.txt'
$treeSentinel  = Commit1 'b-question.txt'
$treeMalformed = Commit1 'c-branchname.txt'
$treeAbsent    = Commit1 'd-threecol.txt'

G @('-C', $root, 'checkout', '-q', '-b', 'side', 'base') | Out-Null
Set-Content -Path (Join-Path $root 'side.txt') -Value 'side' -Encoding ascii
G @('-C', $root, 'add', '-A') | Out-Null
G @('-C', $root, 'commit', '-q', '-m', 'side commit') | Out-Null
$treeSide = (G @('-C', $root, 'rev-parse', 'HEAD^{tree}') | Select-Object -First 1)
G @('-C', $root, 'checkout', '-q', 'work') | Out-Null
G @('-C', $root, 'merge', '-q', '--no-ff', '-m', 'Merge side into work', 'side') | Out-Null
$treeMerge = (G @('-C', $root, 'rev-parse', 'HEAD^{tree}') | Select-Object -First 1)
$shaMerge  = (G @('-C', $root, 'rev-parse', 'HEAD') | Select-Object -First 1)

$treeUnatt = ''
if ($AddUnattested) { $treeUnatt = Commit1 'e-unattested.txt' }

$hookHash = '519ad9f6cefd714b7f15339d9c1aeefacda0b10a'
$ledger = Join-Path $root 'crafted-ledger.log'
Set-Content -Path $ledger -Encoding ascii -Value @(
    "$treeHex 2026-09-05T10:00:00Z work/ledgerread $hookHash",
    "$treeSentinel 2026-09-05T10:01:00Z work/ledgerread ?",
    "$treeMalformed 2026-08-30T15:15:07Z 0cb0b6a895488517150c7cd589f624bcd6e284ef work/u5proxy",
    "$treeAbsent 2026-08-29T21:28:34Z work/hookattest",
    "$treeSide 2026-09-05T10:02:00Z work/ledgerread $hookHash",
    "$treeMerge 2026-09-05T10:03:00Z work/ledgerread $hookHash")

Write-Host "--- FIXTURE ---"
Write-Host ("  repo          : {0}" -f $root)
Write-Host ("  (b) 40-hex    : tree {0}  <- commit a-hex.txt" -f $treeHex)
Write-Host ("  (c) sentinel  : tree {0}  <- commit b-question.txt (col4 = ?)" -f $treeSentinel)
Write-Host ("  (d) malformed : tree {0}  <- commit c-branchname.txt (col4 = work/u5proxy)" -f $treeMalformed)
Write-Host ("  (a) 3-column  : tree {0}  <- commit d-threecol.txt (no col4)" -f $treeAbsent)
Write-Host ("  merge         : tree {0}  <- commit {1}" -f $treeMerge, $shaMerge.Substring(0, 8))
if ($AddUnattested) { Write-Host ("  UNATTESTED    : tree {0}  <- commit e-unattested.txt (no ledger line)" -f $treeUnatt) }
Write-Host "--- CRAFTED LEDGER ---"
Get-Content $ledger | ForEach-Object { Write-Host ("  " + $_) }
Write-Host "--- CHECKER OUTPUT ---"

# -AllowLedgerOverride is required as well as the env var: the override is deliberately not
# a one-line environment switch, because the party it constrains sets its own environment.
$env:AI_STACK_ATTEST_LEDGER = $ledger
$callArgs = @('-Branch', 'work', '-Base', 'base', '-RepoRoot', $root, '-AllowLedgerOverride')
if ($Json) { $callArgs += '-Json' }
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Script @callArgs
$code = $LASTEXITCODE
Remove-Item Env:\AI_STACK_ATTEST_LEDGER
Write-Host ("--- EXIT: {0} ---" -f $code)
Write-Host ("fixture repo left at {0} (delete it when you are done)" -f $root)
exit $code
