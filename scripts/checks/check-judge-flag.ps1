#  check-judge-flag.ps1 - a commit may not turn observer.judge_enabled on
# without a human rating record. Pre-commit gate + audit record.
#
# WHY THIS EXISTS. JUDGE-CALIBRATION.md and check-judge-dryrun.ps1 are a PLAN
# and an INSTRUMENT. Neither stops anything: a verifier set judge_enabled: true
# in a copy of little-coder.config.yaml, re-ran the drill, and got ALL 13 CASES
# PASS, exit 0 - the artifact whose entire purpose is "do not flip this flag
# until a human rates a dry run" did not notice the flip. Prose plus an
# instrument is not containment; this is the mechanism.
#
# WHAT IT DOES. Staged-aware. For every staged *.yaml / *.yml it reads the
# STAGED content (not the working tree - editing the file back after `git add`
# must not launder it) and looks for `judge_enabled:` set truthy. If none, it
# exits 0 without writing anything, which is the overwhelmingly common case and
# costs one grep. If one is found the commit is DENIED unless the SAME commit
# stages a valid rating record, and either outcome is written to the audit log.
#
# WHAT A VALID RATING RECORD IS. Design section 13 exit criterion 3 is a HUMAN
# rating of the emitted judge prompts. The record must be YAML (or YAML
# frontmatter) carrying rated_by / rated_at / rated_report / verdict, with
# verdict: approve. That rule has exactly ONE definition, in
# scripts/checks/lib/judge_dryrun.py:read_rating_record, which this script
# shells rather than reimplements - two copies drift, and the copy that drifts
# is the one nobody looks at.
#
# FAIL CLOSED. If a candidate is found and the rule cannot be evaluated (python
# missing, the library unreadable), the commit is DENIED and the audit line says
# CANNOT-TELL. A guard that waves things through when its own machinery is
# broken is the failure mode this whole phase exists to stop.
#
# WHAT IT DOES *NOT* CATCH, stated so nothing reads wider than it is:
#   - a commit made with `--no-verify`, or in a clone without core.hooksPath.
#     That is check-hook-attestation.ps1's job, not this one, and it is a
#     separate U5 sub-item on a separate branch.
#   - a flip made at RUNTIME - editing the config inside the container, or an
#     env override - which is not a commit at all. check-judge-dryrun.ps1's
#     exit 7 is what detects that, by reading the config actually in force.
#   - a branch that does not carry this file. core.hooksPath is per-checkout
#     and the hook is version-controlled, so this constrains commits made on
#     branches that have it, and nothing else.
#   The audit log is an OPERATOR-READ record. Nothing consumes it automatically;
#   claiming otherwise would be the doc-claims-a-property-the-code-lacks shape.
#
# Usage:
#   .\check-judge-flag.ps1                 # pre-commit form (staged content)
#   .\check-judge-flag.ps1 -Audit          # print the audit log
#   .\check-judge-flag.ps1 -RepoRoot <dir> # drill form
#
# Exit: 0 = nothing staged turns the flag on, or a valid rating record does it
#       1 = DENIED
#       2 = usage / not a git repository

[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    # The path a rating record must be staged at. A parameter so the drill can
    # exercise the rule without inventing a second definition of it.
    [string]$RatingRecordPath = "little-coder/config/judge-enablement-rating.yaml",
    [switch]$Audit
)

$ErrorActionPreference = 'Stop'

function Invoke-Git {
    param([string[]]$GitArgs)
    if ($script:GitDir) { $GitArgs = @("-C", $script:GitDir) + $GitArgs }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { return @(& git @GitArgs 2>$null) } finally { $ErrorActionPreference = $prev }
}
# NO Set-Location: PowerShell's location is session-scoped, so changing it here
# relocates the caller. -RepoRoot is threaded into git via -C instead.
$script:GitDir = $RepoRoot

$common = (Invoke-Git @("rev-parse", "--git-common-dir") | Select-Object -First 1)
if (-not $common) {
    Write-Host "check-judge-flag: not a git repository" -ForegroundColor Red
    exit 2
}
if (-not [System.IO.Path]::IsPathRooted($common)) {
    # Relative to the repo we asked about, never to wherever the caller stands.
    $anchor = if ($RepoRoot) { $RepoRoot } else { (Get-Location).Path }
    $common = Join-Path $anchor $common
}
# The log lives in the SHARED git dir, like hook-attest.log: it must not be
# committable, and every worktree must append to one record.
$LogPath = Join-Path $common "judge-flag-guard.log"

if ($Audit) {
    if (Test-Path $LogPath) { Get-Content $LogPath } else { Write-Host "no entries: $LogPath" }
    exit 0
}

function Write-AuditLine {
    param([string]$Outcome, [string]$Detail)
    $branch = (Invoke-Git @("rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1)
    if (-not $branch) { $branch = '?' }
    $stamp = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
    $line = "{0} {1} branch={2} {3}" -f $stamp, $Outcome, $branch, $Detail
    try { Add-Content -Path $LogPath -Value $line -Encoding ascii } catch { }
    return $line
}

# --- 1. candidates: staged YAML that turns the flag on ----------------------
$staged = Invoke-Git @("diff", "--cached", "--name-only", "--diff-filter=ACM")
$candidates = @()
foreach ($rel in $staged) {
    if (-not $rel) { continue }
    if ($rel -notmatch '\.ya?ml$') { continue }
    $content = (Invoke-Git @("show", ":$rel")) -join "`n"
    # YAML truthy spellings for a boolean scalar. Deliberately NOT a general
    # YAML parse: this must stay a cheap grep on a hot path, and the shipped
    # schema (little-coder.schema.json:244) types the key as a boolean.
    if ($content -match '(?m)^\s*judge_enabled\s*:\s*(true|True|TRUE|yes|Yes|on|On)\s*(#.*)?$') {
        $candidates += $rel
    }
}

if ($candidates.Count -eq 0) {
    # Nothing to say and nothing to record. A guard that logs on every commit
    # buries the lines that matter.
    exit 0
}

Write-Host "check-judge-flag: this commit turns observer.judge_enabled ON in:" -ForegroundColor Yellow
foreach ($c in $candidates) { Write-Host "    $c" -ForegroundColor Yellow }

# --- 2. is a valid rating record staged in the SAME commit? -----------------
$ratingStaged = $staged | Where-Object { $_ -eq $RatingRecordPath }
if (-not $ratingStaged) {
    $line = Write-AuditLine 'DENY' ("files=" + ($candidates -join ',') + " reason=no-rating-record-staged")
    Write-Host "DENIED: no rating record staged at $RatingRecordPath" -ForegroundColor Red
    Write-Host "  Design section 13 exit criterion 3 is a HUMAN rating of the judge prompts." -ForegroundColor Red
    Write-Host "  Produce them with:  .\scripts\checks\check-judge-dryrun.ps1 -EmitPrompts -OutDir .\dryrun" -ForegroundColor Yellow
    Write-Host "  audit: $line" -ForegroundColor DarkGray
    exit 1
}

# Materialise the STAGED record (not the working tree) and validate it with the
# single definition of the rule.
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-rating-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".yaml")
try {
    $recordText = (Invoke-Git @("show", ":$RatingRecordPath")) -join "`n"
    Set-Content -Path $tmp -Value $recordText -Encoding utf8

    $lib = Join-Path $PSScriptRoot 'lib\judge_dryrun.py'
    if (-not (Test-Path $lib)) {
        $line = Write-AuditLine 'CANNOT-TELL' ("files=" + ($candidates -join ',') + " reason=validator-missing")
        Write-Host "DENIED (cannot tell): validator not found at $lib" -ForegroundColor Red
        Write-Host "  audit: $line" -ForegroundColor DarkGray
        exit 1
    }

    $py = @"
import sys, importlib.util
spec = importlib.util.spec_from_file_location('judge_dryrun', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from pathlib import Path
rec, problem = m.read_rating_record(Path(sys.argv[2]))
print(problem if problem else 'OK')
sys.exit(0 if rec else 3)
"@
    $pyFile = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-rating-val-" + [guid]::NewGuid().ToString('N').Substring(0, 8) + ".py")
    Set-Content -Path $pyFile -Value $py -Encoding ascii
    try {
        $out = (& python $pyFile $lib $tmp 2>&1 | Out-String).Trim()
        $rc = $LASTEXITCODE
    } finally {
        Remove-Item $pyFile -Force -ErrorAction SilentlyContinue
    }

    if ($rc -eq 0) {
        $line = Write-AuditLine 'ALLOW' ("files=" + ($candidates -join ',') + " rating=$RatingRecordPath")
        Write-Host "ALLOWED: a valid rating record is staged at $RatingRecordPath" -ForegroundColor Green
        Write-Host "  audit: $line" -ForegroundColor DarkGray
        exit 0
    }
    if ($rc -eq 3) {
        $line = Write-AuditLine 'DENY' ("files=" + ($candidates -join ',') + " reason=rating-record-invalid: $out")
        Write-Host "DENIED: the staged rating record is not valid - $out" -ForegroundColor Red
        Write-Host "  audit: $line" -ForegroundColor DarkGray
        exit 1
    }
    # FAIL CLOSED. Any other exit means the rule could not be evaluated at all.
    $line = Write-AuditLine 'CANNOT-TELL' ("files=" + ($candidates -join ',') + " reason=validator-exit-$rc : $out")
    Write-Host "DENIED (cannot tell): the validator exited $rc - $out" -ForegroundColor Red
    Write-Host "  A guard that passes when its own machinery is broken is not a guard." -ForegroundColor Yellow
    Write-Host "  audit: $line" -ForegroundColor DarkGray
    exit 1
} finally {
    Remove-Item $tmp -Force -ErrorAction SilentlyContinue
}
