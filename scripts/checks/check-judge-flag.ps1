#  check-judge-flag.ps1 - a commit may not turn observer.judge_enabled on
# without a human rating record. Pre-commit gate + audit record.
#
# WHY THIS EXISTS. JUDGE-CALIBRATION.md and check-judge-dryrun.ps1 are a PLAN
# and an INSTRUMENT. Neither stops anything: a verifier set judge_enabled: true
# in a copy of little-coder.config.yaml, re-ran the drill, and got ALL 13 CASES
# PASS, exit 0 - the artifact whose entire purpose is "do not flip this flag
# until a human rates a dry run" did not notice the flip. Prose plus an
# instrument is not containment; this is the commit-time half of the mechanism.
#
# WHY IT NO LONGER GREPS (2026-08-30). The first version of this script decided
# "is the flag being turned on?" with a REGEX over staged text while the daemon
# decided it with yaml.safe_load + pydantic. Two parsers, two answers: one
# verifier turned the flag on for the daemon with three ordinary YAML spellings
# and walked past the pattern, and another did it with two quote characters.
# Patching the pattern buys one round; there is always another spelling. So the
# decision MOVED: it is made by littlecoder.judge_gate - the same module the
# daemon calls at boot - reached through scripts/checks/lib/judge_flag_decide.py.
# This script no longer contains any notion of what YAML true looks like.
# Fourteen spellings the old pattern missed are pinned in
# scripts/checks/fixtures/judge-flag-corpus.json and asserted by
# little-coder/tests/test_judge_flag_corpus.py and by
# verify-judge-flag-guard.ps1, which drives THIS script over all of them.
#
# WHAT IT DOES. Staged-aware. For every staged *.yaml / *.yml the decider reads
# the STAGED bytes (git show :path - not the working tree; editing the file back
# after `git add` must not launder it) and asks judge_gate whether that text
# turns the flag on. If none do it exits 0 without writing anything, which is
# the overwhelmingly common case. If one does, the commit is DENIED unless the
# SAME commit stages a valid rating record, and either outcome is written to the
# audit log.
#
# WHAT A VALID RATING RECORD IS. Design section 13 exit criterion 3 is a HUMAN
# rating of the emitted judge prompts. The record must be YAML (or YAML
# frontmatter) carrying rated_by / rated_at / rated_report / verdict, with
# verdict: approve. That rule has exactly ONE definition, in
# little-coder/src/littlecoder/judge_gate.py:read_rating_record, which the
# daemon, this guard and the dry-run instrument all call - two copies drift, and
# the copy that drifts is the one nobody looks at.
#
# FAIL CLOSED. If the rule cannot be evaluated - python missing, littlecoder not
# importable, the decider unreadable, a staged blob unreadable - the commit is
# DENIED and the audit line says CANNOT-TELL. A guard that waves things through
# when its own machinery is broken is the failure mode this whole phase exists
# to stop. Note the cost that buys: on a machine with no working python, a
# commit that stages ANY yaml is refused. That is deliberate, and it is the same
# posture the previous version took once it had found a candidate.
#
# WHAT IT DOES *NOT* CATCH, stated so nothing reads wider than it is:
#   - a commit made with `--no-verify`, or in a clone without core.hooksPath.
#     That is check-hook-attestation.ps1's job, not this one, and it is a
#     separate U5 sub-item on a separate branch.
#   - a branch that does not carry this file. core.hooksPath is per-checkout
#     and the hook is version-controlled, so this constrains commits made on
#     branches that have it, and nothing else.
#   NEITHER OF THOSE REACHES THE JUDGE ANY MORE, which is the point of moving
#   the decision: littlecoder.judge_gate.require is called by
#   meta_wiring.build_meta_runner at daemon boot, so a flag that gets past this
#   script - by --no-verify, by another branch, or by being edited straight into
#   the container at runtime - still cannot start a judging daemon without a
#   valid rating record at /app/config/judge-enablement-rating.yaml. This script
#   is the perimeter and the audit trail; the chokepoint is in the daemon.
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
    # exercise the rule without inventing a second definition of it. The
    # default is asserted against judge_gate.RATING_RECORD_REPO_PATH and against
    # the coder compose mount by verify-judge-flag-guard.ps1 - three readers of
    # one fact, so the record a commit stages is the record the daemon reads.
    [string]$RatingRecordPath = "little-coder/config/judge-enablement-rating.yaml",
    # Where littlecoder is importable from. Defaults to this repository's copy.
    [string]$SrcPath = "",
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
$anchor = if ($RepoRoot) { $RepoRoot } else { (Get-Location).Path }
if (-not [System.IO.Path]::IsPathRooted($common)) {
    # Relative to the repo we asked about, never to wherever the caller stands.
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

# --- 1. what is staged ------------------------------------------------------
$staged = Invoke-Git @("diff", "--cached", "--name-only", "--diff-filter=ACM")
$yamlStaged = @($staged | Where-Object { $_ -and ($_ -match '\.ya?ml$') })
if ($yamlStaged.Count -eq 0) {
    # Nothing to say and nothing to record. A guard that logs on every commit
    # buries the lines that matter.
    exit 0
}
$ratingStaged = [bool]($staged | Where-Object { $_ -eq $RatingRecordPath })

# --- 2. ask the daemon's own decision, never a pattern ----------------------
if (-not $SrcPath) { $SrcPath = Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'little-coder\src' }
$decider = Join-Path $PSScriptRoot 'lib\judge_flag_decide.py'
$tmpDir = Join-Path ([System.IO.Path]::GetTempPath()) ("judge-flag-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null

function Deny-CannotTell {
    param([string]$Reason)
    $line = Write-AuditLine 'CANNOT-TELL' ("files=" + ($yamlStaged -join ',') + " reason=$Reason")
    Write-Host "DENIED (cannot tell): $Reason" -ForegroundColor Red
    Write-Host "  A guard that passes when its own machinery is broken is not a guard." -ForegroundColor Yellow
    Write-Host "  audit: $line" -ForegroundColor DarkGray
    exit 1
}

try {
    if (-not (Test-Path $decider)) { Deny-CannotTell "decider not found at $decider" }

    $request = [ordered]@{
        src                  = $SrcPath
        repo                 = $anchor
        paths                = $yamlStaged
        rating_record_path   = $RatingRecordPath
        rating_record_staged = $ratingStaged
        tmp                  = $tmpDir
    } | ConvertTo-Json -Compress -Depth 4
    $reqFile = Join-Path $tmpDir 'request.json'
    # WriteAllText, not Set-Content -Encoding utf8: PowerShell 5.1 writes a
    # UTF-8 BOM there and json.load rejects it. Found by running the drill,
    # where every DENY case still exited 1 - as CANNOT-TELL, for the wrong
    # reason - and only the audit-line assertions showed it.
    [System.IO.File]::WriteAllText($reqFile, $request)

    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = (Get-Content -Raw $reqFile | & python $decider 2>&1 | Out-String)
        $rc = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }

    if ($null -eq $rc) { Deny-CannotTell "python did not run: $($out.Trim())" }
    if ($rc -ne 0) { Deny-CannotTell "decider exited $rc : $($out.Trim())" }

    try { $result = $out | ConvertFrom-Json } catch { Deny-CannotTell "decider output is not JSON: $($out.Trim())" }
    if (-not $result.ok) { Deny-CannotTell "decider could not decide: $($result.error)" }

    $candidates = @($result.candidates)
    if ($candidates.Count -eq 0) { exit 0 }

    $files = ($candidates | ForEach-Object { $_.path }) -join ','
    Write-Host "check-judge-flag: this commit turns observer.judge_enabled ON in:" -ForegroundColor Yellow
    foreach ($c in $candidates) {
        $at = if ($c.where) { " (at $($c.where))" } else { "" }
        Write-Host "    $($c.path)$at" -ForegroundColor Yellow
    }

    # A value the daemon's own type cannot read is not a pass - it is a config
    # that would fail the daemon's boot, and the guard must not guess.
    $undecidable = @($candidates | Where-Object { $_.undecidable })
    if ($undecidable.Count -gt 0) {
        Deny-CannotTell ("value not readable as a boolean: " + ($undecidable[0].undecidable))
    }

    # --- 3. is a valid rating record staged in the SAME commit? -------------
    if (-not $result.rating.present) {
        $line = Write-AuditLine 'DENY' ("files=$files reason=no-rating-record-staged")
        Write-Host "DENIED: no rating record staged at $RatingRecordPath" -ForegroundColor Red
        Write-Host "  Design section 13 exit criterion 3 is a HUMAN rating of the judge prompts." -ForegroundColor Red
        Write-Host "  Produce them with:  .\scripts\checks\check-judge-dryrun.ps1 -EmitPrompts -OutDir .\dryrun" -ForegroundColor Yellow
        Write-Host "  audit: $line" -ForegroundColor DarkGray
        exit 1
    }
    if (-not $result.rating.valid) {
        $line = Write-AuditLine 'DENY' ("files=$files reason=rating-record-invalid: $($result.rating.problem)")
        Write-Host "DENIED: the staged rating record is not valid - $($result.rating.problem)" -ForegroundColor Red
        Write-Host "  audit: $line" -ForegroundColor DarkGray
        exit 1
    }

    $line = Write-AuditLine 'ALLOW' ("files=$files rating=$RatingRecordPath")
    Write-Host "ALLOWED: a valid rating record is staged at $RatingRecordPath" -ForegroundColor Green
    Write-Host "  audit: $line" -ForegroundColor DarkGray
    exit 0
} finally {
    Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
}
