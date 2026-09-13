<#
.SYNOPSIS
  Tests for compact-lib.ps1's Get-ReclaimVerdict. No elevation, no Docker, no downtime.

.DESCRIPTION
  The regression this file exists for is CASE 1: the real 2026-09-13 03:15 numbers. That run
  measured 54.4 GB trapped, returned 9.9, and reported ok=true. If Get-ReclaimVerdict ever calls
  that combination ok again, case 1 goes red.

  Run:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\sysadmin-mcp\test-compact-lib.ps1
  Exit: 0 all passed, 1 one or more failed.
#>
[CmdletBinding()]
param([string]$Lib = (Join-Path $PSScriptRoot 'compact-lib.ps1'))

. $Lib

$script:pass = 0
$script:fail = 0
function Check($name, $cond, $detail) {
  if ($cond) { $script:pass++; Write-Host "  PASS  $name" -ForegroundColor Green }
  else { $script:fail++; Write-Host "  FAIL  $name  $detail" -ForegroundColor Red }
}

Write-Host 'Get-ReclaimVerdict'

# CASE 1 -- THE REGRESSION. Real numbers from state/compact-result.json, run 2026-09-13T03:15.
$v = Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb 9.9 -FstrimOk $null
Check '2026-09-13 regression (54.4 trapped / 9.9 reclaimed) is NOT ok' (-not $v.ok) "ok=$($v.ok)"
Check '  ... shortfall is 44.5 GB' ($v.shortfall_gb -eq 44.5) "got $($v.shortfall_gb)"
Check '  ... floor is 27.2 GB' ($v.floor_gb -eq 27.2) "got $($v.floor_gb)"
Check '  ... reason names the untrimmed guest' ($v.reason -match 'No fstrim was attempted') $v.reason

# CASE 2 -- same shortfall, but we KNOW fstrim failed: the reason must point at the trim.
$v = Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb 9.9 -FstrimOk $false
Check 'fstrim failed -> reason blames the trim' ($v.reason -match 'fstrim did NOT succeed') $v.reason

# CASE 3 -- fstrim SUCCEEDED and it still fell short: must NOT blame the trim (that would send the
# operator to fix a thing that is already working).
$v = Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb 9.9 -FstrimOk $true
Check 'fstrim ok -> reason does not blame the trim' ($v.reason -notmatch 'fstrim did NOT') $v.reason
Check '  ... points at fragmentation instead' ($v.reason -match 'fragmentation') $v.reason

# CASE 4 -- the run we EXPECT after the fix: 47.8 trapped, most of it returned.
$v = Get-ReclaimVerdict -TrappedGb 47.8 -ReclaimedGb 44.0 -FstrimOk $true
Check 'good run (47.8 / 44.0) is ok' ($v.ok) "ok=$($v.ok) reason=$($v.reason)"

# CASE 5 -- GRACE. A small target missed by a small amount is not an incident: 2 of 6 GB is below
# the 50% floor, but the 4 GB shortfall is inside the 5 GB grace. BOTH conditions must hold.
$v = Get-ReclaimVerdict -TrappedGb 6.0 -ReclaimedGb 2.0
Check 'small target inside grace is ok (2 of 6)' ($v.ok) "ok=$($v.ok) shortfall=$($v.shortfall_gb)"

# CASE 6 -- grace does NOT rescue a big miss: same 50% ratio, ten times the size.
$v = Get-ReclaimVerdict -TrappedGb 60.0 -ReclaimedGb 20.0
Check 'big miss outside grace is NOT ok (20 of 60)' (-not $v.ok) "ok=$($v.ok)"

# CASE 7 -- BOUNDARY. Exactly at the floor passes; a hair under it fails.
$v = Get-ReclaimVerdict -TrappedGb 100.0 -ReclaimedGb 50.0
Check 'exactly at floor is ok (50 of 100)' ($v.ok) "ok=$($v.ok)"
$v = Get-ReclaimVerdict -TrappedGb 100.0 -ReclaimedGb 49.9
Check 'a hair under floor is NOT ok (49.9 of 100)' (-not $v.ok) "ok=$($v.ok)"

# CASE 8 -- NO MEASUREMENT. The trapped probe upstream is in its own try/catch and may be skipped;
# a missing number must never fail a run that actually worked.
$v = Get-ReclaimVerdict -TrappedGb $null -ReclaimedGb 12.0
Check 'null trapped -> ok, not assessed' ($v.ok -and $v.reason -match 'not assessed') $v.reason
$v = Get-ReclaimVerdict -TrappedGb 0.0 -ReclaimedGb 0.0
Check 'zero trapped -> ok, not assessed' ($v.ok) $v.reason
$v = Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb $null
Check 'null reclaimed -> ok, not assessed' ($v.ok) $v.reason

# CASE 9 -- the thresholds are actually honoured, not hard-coded.
$v = Get-ReclaimVerdict -TrappedGb 54.4 -ReclaimedGb 9.9 -MinReclaimFraction 0.1
Check 'a 10% floor passes what a 50% floor failed' ($v.ok) "ok=$($v.ok) floor=$($v.floor_gb)"
$v = Get-ReclaimVerdict -TrappedGb 6.0 -ReclaimedGb 2.0 -ShortfallGraceGb 1.0
Check 'a 1 GB grace fails what a 5 GB grace passed' (-not $v.ok) "ok=$($v.ok)"

Write-Host ''
Write-Host "passed $script:pass, failed $script:fail"
if ($script:fail -gt 0) { exit 1 }
exit 0
