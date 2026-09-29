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
#   <archive root>               /E of ./backup/     (cold archives kept on D: -
#                                 ADDITIVE ONLY: never /MIR, never /PURGE, so an
#                                 archive removed from D: stays on the NAS)
#
# The archive root defaults to a SIBLING of <NasUncRoot> ("archive" next to
# "portal"), the same level where the 2026-09-27 orphan-volume archives were put by
# hand. Nothing the slot mirror does can reach it: /MIR only ever deletes inside
# its own destination, and Resolve-NasArchiveRoot refuses an archive root inside
# (or equal to) either slot.

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

function Get-UncShareRoot {
  # '\\server\share\a\b' -> '\\server\share'; $null when the path is not UNC.
  param([string]$Path)
  if (-not $Path -or -not $Path.StartsWith('\\')) { return $null }
  $parts = $Path.TrimStart('\') -split '\\', 3
  if ($parts.Count -lt 2 -or -not $parts[0] -or -not $parts[1]) { return $null }
  return "\\$($parts[0])\$($parts[1])"
}

function Test-NasPathWithin {
  # True when $Path equals $Container or lies below it (case-insensitive, no I/O).
  param([string]$Path, [string]$Container)
  $p = $Path.TrimEnd('\').ToLowerInvariant()
  $c = $Container.TrimEnd('\').ToLowerInvariant()
  return ($p -eq $c) -or $p.StartsWith($c + '\')
}

function Resolve-NasArchiveRoot {
  <#
    Where ./backup/ goes on the NAS. Default: a sibling "archive" folder of
    $NasUncRoot (\\nas\backups\ai-stack\portal -> \\nas\backups\ai-stack\archive);
    when $NasUncRoot IS a bare share (\\nas\share), "archive" inside it, next to
    the slots. An explicit $NasArchiveRoot is honoured but must (a) sit on the
    same \\server\share, because the job opens exactly one SMB session, and (b)
    not be inside or equal to slot-A/slot-B, where the weekly /MIR would purge
    it. Throws on either violation. Works for local stand-in paths too (the
    tests), where rule (a) compares drive roots.
  #>
  param([Parameter(Mandatory = $true)][string]$NasUncRoot, [string]$NasArchiveRoot)
  $root = $NasUncRoot.TrimEnd('\')
  if (-not $NasArchiveRoot) {
    $share = Get-UncShareRoot $root
    if ($share -and ($share.ToLowerInvariant() -eq $root.ToLowerInvariant())) {
      $NasArchiveRoot = "$root\archive"
    } else {
      $NasArchiveRoot = (Split-Path -Parent $root) + '\archive'
    }
  }
  $NasArchiveRoot = $NasArchiveRoot.TrimEnd('\')
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
    robocopy arguments for the ARCHIVE pass: /E copies the tree, adds new files and
    replaces changed ones, and deletes NOTHING at the destination (there is no /MIR
    and no /PURGE, and the function refuses to emit either). /FFT tolerates the
    2-second timestamp granularity some NAS filesystems report, so a file already
    placed there (by copy-archives-to-nas.ps1, which preserves the timestamp) is
    recognised as the same file and not re-sent every week. /XX excludes NAS-side
    extras from the run, which is a second lock: even if /MIR or /PURGE were ever
    added here, robocopy would still remove nothing at the destination (verified
    against local stand-ins by test-nas-sync.ps1).
  #>
  param([string]$Source, [string]$Destination, [string]$LogFile, [switch]$DryRun)
  $a = @($Source, $Destination)
  if ($DryRun) { $a += '/L' }
  $a += @('/E', '/XX', '/FFT', '/R:3', '/W:5', '/Z', '/MT:8', '/XJ')
  if ($LogFile) { $a += "/LOG+:$LogFile" }
  $a += '/NDL'
  foreach ($x in $a) {
    if ($x -match '^/(MIR|PURGE|MOV|MOVE)$') { throw "archive pass must never delete: refusing $x" }
  }
  return , $a
}

function Get-RobocopyExitSummary {
  param([int]$Code)
  $d = @()
  if ($Code -band 1) { $d += 'files copied' }
  if ($Code -band 2) { $d += 'extras detected' }
  if ($Code -band 4) { $d += 'mismatches' }
  if ($d.Count -eq 0) { $d = @('no changes') }
  return ($d -join ', ')
}
