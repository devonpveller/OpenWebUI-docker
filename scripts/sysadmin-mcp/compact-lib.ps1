<#
.SYNOPSIS
  Pure decision helpers for compact-vhdx.ps1. NO side effects, NO elevation, NO Docker.

.DESCRIPTION
  compact-vhdx.ps1 can only be run elevated, with the whole stack down, for ~15 minutes. That is
  not a thing a test can drive, so the judgement that matters -- did this compaction actually
  return the space it measured? -- lives here instead, as a function over numbers. test-compact-lib.ps1
  drives it directly.

  This exists because of the 2026-09-13 03:15 run: it measured 54.4 GB trapped, returned 9.9, and
  wrote ok=true. Nothing in the script compared the two numbers, so the operator was told the
  compaction succeeded and 44.5 GB stayed trapped.
#>

function Get-ReclaimVerdict {
  <#
  .SYNOPSIS
    Did a compaction return enough of the space it measured as trapped?
  .OUTPUTS
    [pscustomobject] ok, floor_gb, shortfall_gb, reason
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)] [AllowNull()] [System.Nullable[double]]$TrappedGb,
    [Parameter(Mandatory)] [AllowNull()] [System.Nullable[double]]$ReclaimedGb,
    [double]$MinReclaimFraction = 0.5,
    [double]$ShortfallGraceGb = 5.0,
    [AllowNull()] [System.Nullable[bool]]$FstrimOk = $null
  )

  # No measurement -> no verdict. Never fail a run for a number we could not take: the trapped
  # probe is wrapped in its own try/catch upstream and is allowed to be skipped.
  if ($null -eq $TrappedGb -or $null -eq $ReclaimedGb -or $TrappedGb -le 0) {
    return [pscustomobject]@{
      ok = $true; floor_gb = $null; shortfall_gb = $null
      reason = 'no trapped measurement; shortfall not assessed'
    }
  }

  $floor     = [math]::Round($TrappedGb * $MinReclaimFraction, 1)
  $shortfall = [math]::Round($TrappedGb - $ReclaimedGb, 1)

  # Two conditions, deliberately BOTH required. The fraction catches "returned a token amount of a
  # big target"; the grace stops a small target (trapped 6, reclaimed 2) from raising an incident
  # over 4 GB. A run must miss proportionally AND miss by an amount worth downtime.
  if ($ReclaimedGb -lt $floor -and $shortfall -gt $ShortfallGraceGb) {
    $why = "compaction UNDER-RECLAIMED: returned $ReclaimedGb GB of $TrappedGb GB trapped " +
           "(floor $floor GB, shortfall $shortfall GB)."
    if ($FstrimOk -eq $false) {
      $why += ' fstrim did NOT succeed, so the guest never discarded the free blocks and ' +
              'Optimize-VHD had nothing to reclaim -- fix the trim, then re-run.'
    } elseif ($null -eq $FstrimOk) {
      $why += ' No fstrim was attempted (pre-2026-09-13 behaviour): Optimize-VHD cannot read ext4 ' +
              'and reclaims only blocks the guest already discarded.'
    } else {
      $why += ' fstrim reported success, so the shortfall is NOT an untrimmed-guest problem -- ' +
              'suspect vhdx fragmentation or a failed Optimize-VHD pass.'
    }
    return [pscustomobject]@{ ok = $false; floor_gb = $floor; shortfall_gb = $shortfall; reason = $why }
  }

  return [pscustomobject]@{
    ok = $true; floor_gb = $floor; shortfall_gb = $shortfall
    reason = "reclaim within target: $ReclaimedGb of $TrappedGb GB trapped (floor $floor GB)"
  }
}
