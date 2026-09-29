# scripts/backup/nas-sync-lib.ps1
#
# Pure helpers shared by backup-to-nas.ps1 (the weekly job), copy-archives-to-nas.ps1
# (the one-time archive copy) and test-nas-sync.ps1 (their tests against local
# stand-in directories). Dot-source it; it defines functions and runs nothing.
#
# The two robocopy argument builders are the whole contract of the NAS layout:
#
#   <NasUncRoot>\slot-A|slot-B   /MIR of ./backups/  (the nightly sidecar output;
#                                 a mirror, so a slot tracks ./backups/ exactly)
#   <archive root>               ./backup/, NEW FILES ONLY (cold archives kept on D:):
#                                 never /MIR or /PURGE, so an archive removed from D:
#                                 stays on the NAS; and /XC /XN /XO, so a file that
#                                 is already on the NAS is never replaced - not by a
#                                 re-export under the same name, not by a local copy
#                                 truncated by a disk fault. Such a difference is
#                                 reported (Get-NasArchiveDrift), never copied.
#
# The archive root defaults to a SIBLING of <NasUncRoot> ("archive" next to
# "portal"), the same level where the 2026-09-27 orphan-volume archives were put by
# hand. Nothing the slot mirror does can reach it: /MIR only ever deletes inside
# its own destination, and Resolve-NasArchiveRoot refuses an archive root inside
# (or equal to) either slot - compared after normalisation, so `x\..\slot-A`,
# `.\slot-A`, `/slot-A`, `slot-A.` and `slot-A ` are all the slot.

function Get-DotEnvValue {
  # KEY=value from a .env file; quotes stripped; comments and blank lines skipped.
  param([string]$Path, [string]$Key)
  if (-not (Test-Path -LiteralPath $Path)) { return $null }
  foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
    $t = $line.Trim()
    if ($t.StartsWith('#') -or -not $t.Contains('=')) { continue }
    $eq = $t.IndexOf('=')
    if ($t.Substring(0, $eq).Trim() -ne $Key) { continue }
    $v = $t.Substring($eq + 1).Trim()
    if ($v.Length -ge 2 -and (($v[0] -eq '"' -and $v[-1] -eq '"') -or ($v[0] -eq "'" -and $v[-1] -eq "'"))) {
      $v = $v.Substring(1, $v.Length - 2)
    }
    return $v
  }
  return $null
}

function Get-NasSlotName {
  # ISO-week parity: even -> slot-A, odd -> slot-B. Culture-invariant.
  param([datetime]$Date = (Get-Date))
  $cal = [System.Globalization.CultureInfo]::InvariantCulture.Calendar
  $week = $cal.GetWeekOfYear($Date, [System.Globalization.CalendarWeekRule]::FirstFourDayWeek, [DayOfWeek]::Monday)
  if ($week % 2 -eq 0) { return 'slot-A' }
  return 'slot-B'
}

function Get-NasNormalPath {
  <#
    The path Windows will actually open, as a string, with no I/O:
    [System.IO.Path]::GetFullPath resolves `.`, `..`, `/` and doubled separators and
    drops the trailing dots and spaces of the LAST segment; this function then also
    trims trailing dots and spaces from EVERY segment (Win32 does not always, but a
    comparison that over-matches only over-refuses) and drops a trailing separator.
    Refuses a relative path (it would resolve against the current directory) and the
    \\?\ and \\.\ device forms (they bypass that normalisation).
  #>
  param([Parameter(Mandatory = $true)][string]$Path)
  if ($Path.StartsWith('\\?\') -or $Path.StartsWith('\\.\') -or $Path.StartsWith('//?/') -or $Path.StartsWith('//./')) {
    throw "device-path form is not accepted: '$Path'"
  }
  if (-not [System.IO.Path]::IsPathRooted($Path) -or ($Path -match '^[A-Za-z]:(?![\\/])')) {
    throw "path must be absolute: '$Path'"
  }
  $full = [System.IO.Path]::GetFullPath($Path)
  $unc = $full.StartsWith('\\')
  $segs = @(($full.TrimStart('\') -split '\\') | ForEach-Object { $_.TrimEnd('.', ' ') } | Where-Object { $_ -ne '' })
  if ($unc) { return '\\' + ($segs -join '\') }
  return ($segs -join '\') + $(if ($segs.Count -eq 1) { '\' } else { '' })
}

function Get-UncShareRoot {
  # '\\server\share\a\b' -> '\\server\share'; $null when the path is not UNC.
  param([string]$Path)
  if (-not $Path -or -not $Path.StartsWith('\\')) { return $null }
  $parts = $Path.TrimStart('\') -split '\\', 3
  if ($parts.Count -lt 2 -or -not $parts[0] -or -not $parts[1]) { return $null }
  return "\\$($parts[0])\$($parts[1])"
}

function Test-NasPathWithin {
  # True when $Path equals $Container or lies below it: both normalised
  # (Get-NasNormalPath), compared case-insensitively. No I/O.
  param([string]$Path, [string]$Container)
  $p = (Get-NasNormalPath $Path).TrimEnd('\').ToLowerInvariant()
  $c = (Get-NasNormalPath $Container).TrimEnd('\').ToLowerInvariant()
  return ($p -eq $c) -or $p.StartsWith($c + '\')
}

function Test-NasSlotSegment {
  # True when any segment of the normalised path IS a slot folder name. Used where
  # the slot root is unknown (copy-archives-to-nas.ps1): over-refuses by design.
  param([string]$Path)
  foreach ($s in ((Get-NasNormalPath $Path) -split '\\')) {
    if ($s -ieq 'slot-A' -or $s -ieq 'slot-B') { return $true }
  }
  return $false
}

function Resolve-NasArchiveRoot {
  <#
    Where ./backup/ goes on the NAS. Default: a sibling "archive" folder of
    $NasUncRoot (\\nas\backups\ai-stack\portal -> \\nas\backups\ai-stack\archive);
    when $NasUncRoot IS a bare share (\\nas\share), "archive" inside it, next to
    the slots. An explicit $NasArchiveRoot is honoured but must (a) sit on the
    same \\server\share, because the job opens exactly one SMB session, and (b)
    not be inside or equal to slot-A/slot-B, where the weekly /MIR would purge
    it. Both are judged on the NORMALISED paths (Get-NasNormalPath); the
    normalised archive root is what is returned. Throws on any violation. Works
    for local stand-in paths too (the tests), where rule (a) compares drive roots.
  #>
  param([Parameter(Mandatory = $true)][string]$NasUncRoot, [string]$NasArchiveRoot)
  $root = Get-NasNormalPath $NasUncRoot
  if (-not $NasArchiveRoot) {
    $share = Get-UncShareRoot $root
    if ($share -and ($share.ToLowerInvariant() -eq $root.ToLowerInvariant())) {
      $NasArchiveRoot = "$root\archive"
    } else {
      $NasArchiveRoot = (Split-Path -Parent $root) + '\archive'
    }
  }
  $NasArchiveRoot = Get-NasNormalPath $NasArchiveRoot
  $shareA = Get-UncShareRoot $root
  $shareB = Get-UncShareRoot $NasArchiveRoot
  if ($shareA -or $shareB) {
    if (-not $shareA -or -not $shareB -or $shareA.ToLowerInvariant() -ne $shareB.ToLowerInvariant()) {
      throw "archive root '$NasArchiveRoot' is not on the same share as '$NasUncRoot' (the job opens one SMB session, to $shareA)"
    }
  } else {
    $qa = Split-Path -Qualifier $root -ErrorAction SilentlyContinue
    $qb = Split-Path -Qualifier $NasArchiveRoot -ErrorAction SilentlyContinue
    if ($qa -and $qb -and $qa.ToLowerInvariant() -ne $qb.ToLowerInvariant()) {
      throw "archive root '$NasArchiveRoot' is not on the same drive as '$NasUncRoot'"
    }
  }
  foreach ($slot in @('slot-A', 'slot-B')) {
    if (Test-NasPathWithin -Path $NasArchiveRoot -Container "$root\$slot") {
      throw "archive root '$NasArchiveRoot' is inside $slot, which the weekly /MIR purges - refusing"
    }
  }
  return $NasArchiveRoot
}

function Get-NasSlotMirrorArgs {
  # robocopy arguments for the slot mirror (unchanged since the job was written).
  param([string]$Source, [string]$Destination, [string]$LogFile, [switch]$DryRun)
  $a = @($Source, $Destination)
  if ($DryRun) { $a += '/L' }
  $a += @('/MIR', '/R:3', '/W:5', '/Z', '/MT:8', '/XJ')
  if ($LogFile) { $a += "/LOG+:$LogFile" }
  $a += '/NDL'
  return , $a
}

function Get-NasArchiveCopyArgs {
  <#
    robocopy arguments for the ARCHIVE pass - NEW FILES ONLY:
      /E        walk the tree (subdirectories, empty ones included);
      /XC /XN /XO  exclude changed, newer and older files: a file that already
                exists at the destination is never replaced, whatever happened to
                the local copy (a re-export, a truncation). Only absent files copy;
      /XX       exclude destination extras - with no /MIR or /PURGE nothing is
                deleted anyway, and /XX keeps that true even if one were added;
      /FFT      2-second timestamp tolerance, so a file placed by
                copy-archives-to-nas.ps1 (timestamp preserved) is not "changed".
    The function REFUSES to return a set that lacks any of /E /XC /XN /XO /XX or
    that carries /MIR, /PURGE, /MOV or /MOVE.
  #>
  param([string]$Source, [string]$Destination, [string]$LogFile, [switch]$DryRun)
  $a = @($Source, $Destination)
  if ($DryRun) { $a += '/L' }
  $a += @('/E', '/XC', '/XN', '/XO', '/XX', '/FFT', '/R:3', '/W:5', '/Z', '/MT:8', '/XJ')
  if ($LogFile) { $a += "/LOG+:$LogFile" }
  $a += '/NDL'
  foreach ($x in $a) {
    if ($x -match '^/(MIR|PURGE|MOV|MOVE)$') { throw "archive pass must never delete: refusing $x" }
  }
  foreach ($req in @('/E', '/XC', '/XN', '/XO', '/XX')) {
    if ($a -notcontains $req) { throw "archive pass must copy new files only: $req missing" }
  }
  return , $a
}

function Get-NasArchiveDrift {
  <#
    Files under $Source that ALSO exist under $Destination with a different size or
    a last-write time more than 2 s apart - the files the new-files-only archive
    pass deliberately does NOT copy. Returns their paths relative to $Source. Stats
    only (no hashing); a missing $Destination returns nothing.
  #>
  param([string]$Source, [string]$Destination)
  $out = @()
  if (-not (Test-Path -LiteralPath $Destination)) { return }
  $src = $Source.TrimEnd('\')
  foreach ($f in @(Get-ChildItem -LiteralPath $src -Recurse -File -Force -ErrorAction SilentlyContinue)) {
    $rel = $f.FullName.Substring($src.Length + 1)
    $d = Join-Path $Destination $rel
    if (-not (Test-Path -LiteralPath $d -PathType Leaf)) { continue }
    $di = Get-Item -LiteralPath $d -Force
    $dt = [math]::Abs(($di.LastWriteTimeUtc - $f.LastWriteTimeUtc).TotalSeconds)
    if ($di.Length -ne $f.Length -or $dt -gt 2) { $out += $rel }
  }
  return $out   # callers wrap in @(): nothing when there is no drift
}

function Get-RobocopyExitSummary {
  param([int]$Code)
  $d = @()
  if ($Code -band 1) { $d += 'files copied' }
  if ($Code -band 2) { $d += 'extras detected' }
  if ($Code -band 4) { $d += 'mismatches' }
  if ($Code -band 8) { $d += 'some copies FAILED' }
  if ($Code -band 16) { $d += 'FATAL error' }
  if ($d.Count -eq 0) { $d = @('no changes') }
  return ($d -join ', ')
}
