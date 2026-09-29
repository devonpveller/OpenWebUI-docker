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

function Get-FsOverheadGb {
  <#
  .SYNOPSIS
    The filesystem metadata inside the vhdx that `df` never counts: the block device's size minus
    `df -k`'s Size column, in decimal GB. $null when either number is missing or the difference is
    negative (a probe that read the wrong device).
  .DESCRIPTION
    ac-sysadmin-reclaim finding F1 (2026-09-27): "trapped" = vhdx length - df Used, so it includes
    ext4's inode tables, journal and bitmaps - 18.41 GB on this host's 1,099.51 GB device (2,147,483,648
    sectors vs 263,940,717 x 4096-byte blocks). No trim or Optimize-VHD can return that part, and the
    09-27 run passed its 50% floor EXACTLY because of it. Measured per run, not hard-coded: the figure
    follows the device size.
  #>
  param(
    [AllowNull()] [System.Nullable[double]]$DeviceBytes,
    [AllowNull()] [System.Nullable[double]]$DfSizeKb
  )
  if ($null -eq $DeviceBytes -or $null -eq $DfSizeKb -or $DeviceBytes -le 0 -or $DfSizeKb -le 0) { return $null }
  $o = ($DeviceBytes - ($DfSizeKb * 1024)) / 1000000000
  if ($o -lt 0) { return $null }
  return [math]::Round($o, 1)
}

function Get-ReclaimVerdict {
  <#
  .SYNOPSIS
    Did a compaction return enough of the space it measured as trapped?
  .DESCRIPTION
    The target is what a compaction CAN return: trapped minus -FsOverheadGb, the filesystem metadata
    `df` does not count (Get-FsOverheadGb). Without that, a run whose whole shortfall is metadata was
    reported as a failed compaction (cf-small-fixes, from ac-sysadmin-reclaim F1). With no overhead
    measurement the target is trapped, as before.
  .OUTPUTS
    [pscustomobject] ok, floor_gb, shortfall_gb, target_gb, reason
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory)] [AllowNull()] [System.Nullable[double]]$TrappedGb,
    [Parameter(Mandatory)] [AllowNull()] [System.Nullable[double]]$ReclaimedGb,
    [double]$MinReclaimFraction = 0.5,
    [double]$ShortfallGraceGb = 5.0,
    [AllowNull()] [System.Nullable[bool]]$FstrimOk = $null,
    [AllowNull()] [System.Nullable[double]]$FsOverheadGb = $null
  )

  # No measurement -> no verdict. Never fail a run for a number we could not take: the trapped
  # probe is wrapped in its own try/catch upstream and is allowed to be skipped.
  if ($null -eq $TrappedGb -or $null -eq $ReclaimedGb -or $TrappedGb -le 0) {
    return [pscustomobject]@{
      ok = $true; floor_gb = $null; shortfall_gb = $null; target_gb = $null
      reason = 'no trapped measurement; shortfall not assessed'
    }
  }

  $overhead = if ($null -ne $FsOverheadGb -and $FsOverheadGb -gt 0) { [double]$FsOverheadGb } else { 0.0 }
  $target = [math]::Round([math]::Max(0.0, $TrappedGb - $overhead), 1)
  $of = if ($overhead -gt 0) { "$target GB returnable ($TrappedGb trapped less $overhead GB filesystem metadata)" }
        else { "$TrappedGb GB trapped" }
  if ($target -le 0) {
    return [pscustomobject]@{
      ok = $true; floor_gb = 0.0; shortfall_gb = 0.0; target_gb = 0.0
      reason = "all $TrappedGb GB trapped is filesystem metadata ($overhead GB) that no compaction returns"
    }
  }

  $floor     = [math]::Round($target * $MinReclaimFraction, 1)
  $shortfall = [math]::Round($target - $ReclaimedGb, 1)

  # Two conditions, deliberately BOTH required. The fraction catches "returned a token amount of a
  # big target"; the grace stops a small target (trapped 6, reclaimed 2) from raising an incident
  # over 4 GB. A run must miss proportionally AND miss by an amount worth downtime.
  if ($ReclaimedGb -lt $floor -and $shortfall -gt $ShortfallGraceGb) {
    $why = "compaction UNDER-RECLAIMED: returned $ReclaimedGb GB of $of " +
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
    return [pscustomobject]@{ ok = $false; floor_gb = $floor; shortfall_gb = $shortfall; target_gb = $target; reason = $why }
  }

  return [pscustomobject]@{
    ok = $true; floor_gb = $floor; shortfall_gb = $shortfall; target_gb = $target
    reason = "reclaim within target: $ReclaimedGb of $of (floor $floor GB)"
  }
}
