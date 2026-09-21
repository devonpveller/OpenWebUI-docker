# scripts/checks/check-backup-freshness.ps1
#
# GATE half of the backup pair. The other half is check-backup-coverage.ps1:
#
#   coverage  - is every volume SUPPOSED to be backed up by something?
#   freshness - did that something actually PRODUCE anything, recently?
#
# Coverage has always passed while nothing was being produced. That is not a
# hypothetical: lm-models was recreated on 2026-09-19 with a bind pointing at an
# empty directory, its sidecar declined every run for 350 h with
# `PRECHECK SKIP: /data is empty` in its own log, and coverage stayed green the
# whole time because a *-backup container still existed and still referenced the
# volume.
#
# This script is a thin shim over scripts/sysadmin-mcp/check_backups.py --check
# on purpose: ONE implementation of "is this backup real", used by the daily
# alerting task (bare invocation, exits 0 and posts), by this gate (exits 1),
# and by the watchdog. A second copy of the thresholds here is how the two
# drift.
#
# NOT a pre-commit check: it reads ./backups/, which is host state. A commit on
# a laptop cannot be blocked by a sidecar on this host.
#
# Exit codes:
#   0  every running sidecar has a fresh artifact and is not declining runs
#   1  at least one is stale, or is currently PRECHECK SKIPping
#   2  REFUSED - could not evaluate (no backups directory, or no python)

[CmdletBinding()]
param(
  # The DEPLOYMENT root whose ./backups/ holds the artifacts. Defaults to the
  # repo this script lives in; point it at the main checkout when running the
  # worktree copy, which has no backups/ of its own.
  [string]$RepoRoot
)

$ErrorActionPreference = 'Continue'
$scriptRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $RepoRoot) { $RepoRoot = $scriptRoot }

$checker = Join-Path $scriptRoot 'scripts\sysadmin-mcp\check_backups.py'
if (-not (Test-Path $checker)) {
  Write-Host "REFUSED: check_backups.py not found at $checker" -ForegroundColor Red
  exit 2
}

Write-Host "==> Backup freshness check" -ForegroundColor Cyan
Write-Host ("    Artifacts under: {0}\backups" -f $RepoRoot)
Write-Host ""

# 2>&1 so the REFUSED sentence on stderr reaches the operator rather than being
# swallowed by a redirect nobody reads.
$output = & python $checker --check --repo-root $RepoRoot 2>&1
$code = $LASTEXITCODE
$output | ForEach-Object { Write-Host "  $_" }
Write-Host ""

switch ($code) {
  0 { Write-Host "==> Freshness: CLEAN" -ForegroundColor Green }
  1 { Write-Host "==> Freshness: STALE or SKIPPING - see the rows above" -ForegroundColor Red
      Write-Host "    A row naming a MOUNT is a bind pointing where the data is not:"
      Write-Host "    fix the compose bind and the plane .env, then recreate the sidecar." }
  default { Write-Host ("==> Freshness: REFUSED (exit {0}) - the check could not evaluate" -f $code) -ForegroundColor Red }
}
exit $code
