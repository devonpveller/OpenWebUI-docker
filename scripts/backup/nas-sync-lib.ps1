# scripts/backup/nas-sync-lib.ps1
#
# Pure helpers shared by backup-to-nas.ps1 (the weekly job), copy-archives-to-nas.ps1
# (the one-time archive copy) and test-nas-sync.ps1 (their tests against local
# stand-in directories). Dot-source it; it defines functions and runs nothing.
#
# The NAS layout:
#
#   <NasUncRoot>\slot-A|slot-B   /MIR of ./backups/  (robocopy, Get-NasSlotMirrorArgs;
#                                 a mirror, so a slot tracks ./backups/ exactly)
#   <archive root>               the ARCHIVES under ./backup/<subdir>/ (not the
#                                 git-tracked top-level sidecar sources), copied by
#                                 Invoke-NasArchivePass one file at a time: temp name,
#                                 sha256 verified, renamed into place. A complete NAS
#                                 copy is never replaced and nothing is ever deleted;
#                                 an INCOMPLETE one (robocopy's 1980 stamp) is
#                                 re-copied; a complete one whose content differs is
#                                 a MISMATCH the job turns into an alerted failure.
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
    The path Windows will actually open, as a string:
    [System.IO.Path]::GetFullPath resolves `.`, `..`, `/` and doubled separators and
    drops the trailing dots and spaces of the LAST segment; this function then also
    trims trailing dots and spaces from EVERY segment (Win32 does not always, but a
    comparison that over-matches only over-refuses) and drops a trailing separator.
    Refuses a relative path (it would resolve against the current directory) and the
    \\?\ and \\.\ device forms (they bypass that normalisation).
    Not I/O-free: for a segment with `~` GetFullPath asks the filesystem for the long
    name (8.3 expansion), so a short-name spelling of a slot is refused too when the
    folder exists; nothing is created or written.
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

function Get-NasSha256 {
  param([string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-NasSha256Sums {
  # "<hash> *<name>" / "<hash>  <name>" lines of <Dir>\SHA256SUMS -> @{ name = hash }.
  param([string]$Dir)
  $map = @{}
  $f = Join-Path $Dir 'SHA256SUMS'
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $map }
  foreach ($line in (Get-Content -LiteralPath $f)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') { $map[$Matches[2]] = $Matches[1].ToLowerInvariant() }
  }
  return $map
}

function Get-NasRecordedHash {
  # The checksum recorded BESIDE a local archive: its SHA256SUMS entry, else the first
  # token of <file>.sha256. $null when there is none.
  param([string]$LocalFile)
  $dir = Split-Path -Parent $LocalFile
  $name = Split-Path -Leaf $LocalFile
  $sums = Read-NasSha256Sums $dir
  if ($sums.ContainsKey($name)) { return $sums[$name] }
  $side = "$LocalFile.sha256"
  if (Test-Path -LiteralPath $side -PathType Leaf) {
    $tok = ((Get-Content -LiteralPath $side -TotalCount 1) -split '\s+')[0]
    if ($tok -match '^[0-9a-fA-F]{64}$') { return $tok.ToLowerInvariant() }
  }
  return $null
}

function Test-NasIncompleteStamp {
  # robocopy /Z marks a restartable, UNFINISHED copy with a 1980-01-01/02 timestamp
  # (measured: a killed copy leaves a full-length file with the wrong content and
  # that stamp). No archive here is genuinely from 1980.
  param([datetime]$LastWriteTimeUtc)
  return ($LastWriteTimeUtc -lt [datetime]'1980-01-03')
}

function Sync-NasArchiveFile {
  <#
    One archive file, local -> NAS, in the only safe order:
      absent on the NAS          -> copy to <name>.cf-partial (a leftover one from a
                                    killed run is overwritten), set its timestamp,
                                    sha256 it against the local file, then rename it
                                    into place - never over an existing name.  COPIED
      NAS copy INCOMPLETE        -> (robocopy's 1980 stamp) the same temp copy +
                                    verify, then it REPLACES the incomplete file.  REPAIRED
      NAS copy complete, same size and timestamp (+-2 s) and not -AlwaysHash
                                 -> left alone, not hashed.                    PRESENT
      NAS copy complete otherwise -> both sha256'd: equal -> PRESENT (a timestamp-only
                                    difference is harmless); different -> MISMATCH,
                                    NOTHING is written, and the Trust field says which
                                    side the recorded checksum (SHA256SUMS or
                                    <file>.sha256) vouches for.
    A local file that contradicts its own SHA256SUMS entry is FAIL-LOCAL and never
    copied. -VerifyOnly writes nothing (ABSENT / INCOMPLETE are reported). -DryRun
    reports WOULD-COPY / WOULD-REPAIR and writes nothing. File I/O goes through
    cmdlets only (Copy-Item, Move-Item, New-Item, Get-Item, Get-FileHash), never
    [System.IO.File], so a test harness can map a UNC path onto a stand-in.
    Returns @{ Rel; Status; Detail; Trust }. Status in: COPIED REPAIRED PRESENT
    WOULD-COPY WOULD-REPAIR ABSENT INCOMPLETE MISMATCH FAIL-LOCAL FAIL-COPY.
  #>
  param([string]$LocalFile, [string]$NasFile, [string]$Rel, [switch]$AlwaysHash, [switch]$VerifyOnly, [switch]$DryRun)
  $r = @{ Rel = $Rel; Status = ''; Detail = ''; Trust = '' }
  $li = Get-Item -LiteralPath $LocalFile -Force
  $recorded = Get-NasRecordedHash $LocalFile
  $sums = Read-NasSha256Sums (Split-Path -Parent $LocalFile)
  $lh = $null
  if ($sums.ContainsKey($li.Name)) {
    $lh = Get-NasSha256 $LocalFile
    if ($lh -ne $sums[$li.Name]) {
      $r.Status = 'FAIL-LOCAL'; $r.Detail = "local=$lh SHA256SUMS=$($sums[$li.Name]) - not copied"; return $r
    }
  }
  $present = Test-Path -LiteralPath $NasFile -PathType Leaf
  $incomplete = $false
  if ($present) {
    $ni = Get-Item -LiteralPath $NasFile -Force
    $incomplete = Test-NasIncompleteStamp $ni.LastWriteTimeUtc
    if (-not $incomplete) {
      $dt = [math]::Abs(($ni.LastWriteTimeUtc - $li.LastWriteTimeUtc).TotalSeconds)
      if (-not $AlwaysHash -and $ni.Length -eq $li.Length -and $dt -le 2) {
        $r.Status = 'PRESENT'; $r.Detail = 'same size and timestamp (not hashed)'; return $r
      }
      if (-not $lh) { $lh = Get-NasSha256 $LocalFile }
      $nh = Get-NasSha256 $NasFile
      if ($nh -eq $lh) { $r.Status = 'PRESENT'; $r.Detail = "sha256=$lh"; return $r }
      $r.Status = 'MISMATCH'
      $r.Detail = "local=$lh ($($li.Length) B) nas=$nh ($($ni.Length) B)"
      if ($recorded -and $recorded -eq $lh) { $r.Trust = 'LOCAL (it matches its recorded checksum; the NAS copy is damaged - re-copy it by hand after checking)' }
      elseif ($recorded -and $recorded -eq $nh) { $r.Trust = 'NAS (it matches the recorded checksum; the LOCAL file is damaged - restore it from the NAS)' }
      elseif ($recorded) { $r.Trust = 'NEITHER (neither side matches the recorded checksum)' }
      else { $r.Trust = 'UNKNOWN (no recorded checksum beside the local file - compare by hand)' }
      return $r
    }
  }
  if ($VerifyOnly) {
    $r.Status = $(if ($incomplete) { 'INCOMPLETE' } else { 'ABSENT' })
    $r.Detail = $(if ($incomplete) { 'NAS copy carries robocopy''s unfinished-copy stamp (1980)' } else { '' })
    return $r
  }
  if ($DryRun) { $r.Status = $(if ($incomplete) { 'WOULD-REPAIR' } else { 'WOULD-COPY' }); return $r }
  if (-not $lh) { $lh = Get-NasSha256 $LocalFile }
  $tmp = "$NasFile.cf-partial"
  $dir = Split-Path -Parent $NasFile
  try {
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item -LiteralPath $LocalFile -Destination $tmp -Force -ErrorAction Stop
    (Get-Item -LiteralPath $tmp -Force).LastWriteTimeUtc = $li.LastWriteTimeUtc
  } catch {
    $r.Status = 'FAIL-COPY'; $r.Detail = "copy to $tmp failed: $($_.Exception.Message)"; return $r
  }
  $th = Get-NasSha256 $tmp
  if ($th -ne $lh) { $r.Status = 'FAIL-COPY'; $r.Detail = "local=$lh copy=$th - left as $tmp"; return $r }
  try {
    if ($incomplete) { Move-Item -LiteralPath $tmp -Destination $NasFile -Force -ErrorAction Stop; $r.Status = 'REPAIRED' }
    else { Move-Item -LiteralPath $tmp -Destination $NasFile -ErrorAction Stop; $r.Status = 'COPIED' }
  } catch {
    $r.Status = 'FAIL-COPY'; $r.Detail = "could not rename into place ($($_.Exception.Message)) - existing file untouched, copy left as $tmp"; return $r
  }
  $r.Detail = "sha256=$lh"
  return $r
}

function Get-NasArchiveFiles {
  <#
    The files the weekly archive pass covers: every file BELOW a subdirectory of
    $Source. Top-level files of ./backup/ are the git-tracked sidecar sources
    (Dockerfile, README.md, *.sh, .dockerignore) - git keeps their history, and a
    never-replacing archive would turn every edit of one into a MISMATCH failure -
    so they are skipped by position, with no dependency on git being installed
    (test-nas-sync.ps1 G1/G2 check that every tracked file there IS top-level).
    Returns @{ File; Rel } in path order.
  #>
  param([string]$Source)
  $src = $Source.TrimEnd('\')
  $out = @()
  foreach ($d in @(Get-ChildItem -LiteralPath $src -Directory -Force | Sort-Object Name)) {
    foreach ($f in @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force | Sort-Object FullName)) {
      $out += @{ File = $f.FullName; Rel = $f.FullName.Substring($src.Length + 1) }
    }
  }
  return $out
}

function Invoke-NasArchivePass {
  # The weekly archive pass: Sync-NasArchiveFile over Get-NasArchiveFiles. Returns the
  # per-file results; the caller decides what is a failure (MISMATCH, FAIL-*).
  param([string]$Source, [string]$Destination, [switch]$DryRun)
  $results = @()
  foreach ($e in (Get-NasArchiveFiles -Source $Source)) {
    $results += Sync-NasArchiveFile -LocalFile $e.File -NasFile (Join-Path $Destination $e.Rel) -Rel $e.Rel -DryRun:$DryRun
  }
  return $results
}
