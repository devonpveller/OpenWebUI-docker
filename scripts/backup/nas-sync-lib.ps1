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
#                                 copy is never replaced (one exception, see
#                                 Test-NasIncompleteStamp) and nothing but our own
#                                 *.cf-partial temps is ever deleted; an INCOMPLETE
#                                 one (stamped inside robocopy's 1980 unfinished-copy
#                                 window) is re-copied; a complete
#                                 one whose content differs is a MISMATCH, and a local
#                                 file contradicting its recorded checksum is a
#                                 FAIL-LOCAL - the job turns both into alerted failures.
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
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
}

function Read-NasSha256Sums {
  # "<hash> *<name>" / "<hash>  <name>" lines of <Dir>\SHA256SUMS -> @{ name = hash }
  # (hash lower-cased; upper-case files are accepted).
  param([string]$Dir)
  $map = @{}
  $f = Join-Path $Dir 'SHA256SUMS'
  if (-not (Test-Path -LiteralPath $f -PathType Leaf)) { return $map }
  foreach ($line in (Get-Content -LiteralPath $f -ErrorAction Stop)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') { $map[$Matches[2]] = $Matches[1].ToLowerInvariant() }
  }
  return $map
}

function Get-NasRecordedHash {
  <#
    The checksum recorded BESIDE a local archive: its entry in <dir>\SHA256SUMS and/or
    the first token of <file>.sha256. Returns @{ Hash; Source; Conflict }:
    Hash = $null when neither exists; when BOTH exist and disagree, Conflict names
    both values and Hash is $null - neither wins, the record itself is broken and the
    file is not copied (FAIL-LOCAL, Trust RECORD).
  #>
  param([string]$LocalFile)
  $dir = Split-Path -Parent $LocalFile
  $name = Split-Path -Leaf $LocalFile
  $sums = Read-NasSha256Sums $dir
  $a = $null; $b = $null
  if ($sums.ContainsKey($name)) { $a = $sums[$name] }
  $side = "$LocalFile.sha256"
  if (Test-Path -LiteralPath $side -PathType Leaf) {
    $tok = ((Get-Content -LiteralPath $side -TotalCount 1 -ErrorAction Stop) -split '\s+')[0]
    if ($tok -match '^[0-9a-fA-F]{64}$') { $b = $tok.ToLowerInvariant() }
  }
  if ($a -and $b -and $a -ne $b) { return @{ Hash = $null; Source = ''; Conflict = "SHA256SUMS=$a $name.sha256=$b" } }
  if ($a) { return @{ Hash = $a; Source = 'SHA256SUMS'; Conflict = '' } }
  if ($b) { return @{ Hash = $b; Source = "$name.sha256"; Conflict = '' } }
  return @{ Hash = $null; Source = ''; Conflict = '' }
}

function Test-NasIncompleteStamp {
  <#
    robocopy /Z marks a restartable, UNFINISHED copy with a timestamp at the start of
    1980 (measured twice: 1980-01-02T00:00Z; allowing for time-zone shifts of the
    FAT-epoch value, the window is [1979-12-31, 1980-01-03) UTC). ONLY that window
    means "unfinished": an older stamp (1975, 1601 ...) is something robocopy never
    writes and is treated as a complete file (hashed: PRESENT or MISMATCH). Our own
    finished copies never land in the window (Get-NasCopyStamp). The one ambiguity
    that cannot be removed: a complete copy that some OTHER tool stamped inside the
    window is indistinguishable from robocopy's unfinished one (and is re-copied).
    The window's lower edge is inclusive: 1979-12-31T00:00:00Z is inside.
  #>
  param([datetime]$LastWriteTimeUtc)
  return ($LastWriteTimeUtc -ge [datetime]'1979-12-31' -and $LastWriteTimeUtc -lt [datetime]'1980-01-03')
}

function Get-NasCopyStamp {
  # The timestamp a finished NAS copy gets: the local file's, CLAMPED to 1980-01-04
  # 00:00 UTC when it is earlier (an epoch-0 or FAT "no date" local stamp would
  # otherwise land a complete copy in robocopy's unfinished-copy window). The clamp is
  # a full day past the window's end (1980-01-03), so no timestamp rounding on the
  # share (2 s FAT-style, time-zone shifted) can move our copy into the window.
  param([datetime]$LocalUtc)
  $floor = [datetime]::SpecifyKind([datetime]'1980-01-04', [System.DateTimeKind]::Utc)
  if ($LocalUtc -lt $floor) { return $floor }
  return $LocalUtc
}

function Test-NasOddEntry {
  # $true when the path exists as a DIRECTORY or a REPARSE POINT (junction, symlink):
  # never written through, never removed.
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) { return $false }
  $i = Get-Item -LiteralPath $Path -Force
  return ($i.PSIsContainer -or (($i.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0))
}

function Remove-NasTemp {
  # Our own temp FILE only (<name>.cf-partial) - never a directory or reparse point,
  # never anything else. Returns $true when nothing of ours is left.
  param([string]$Tmp)
  if (-not $Tmp.EndsWith('.cf-partial')) { throw "refusing to remove '$Tmp': not a .cf-partial temp" }
  try {
    if (-not (Test-Path -LiteralPath $Tmp)) { return $true }
    if (Test-NasOddEntry $Tmp) { return $false }
    # -Force also removes a read-only file (suite M35).
    Remove-Item -LiteralPath $Tmp -Force -ErrorAction Stop
    return $true
  } catch { return $false }
}

function Sync-NasArchiveFile {
  <#
    One archive file, local -> NAS. Order of decisions:
      0. Odd entries: if the NAS name or its temp name is a DIRECTORY or a REPARSE
         POINT (junction/symlink) -> FAIL-COPY naming it; nothing is written through
         it or removed.
      1. The checksum RECORDED beside the local file (Get-NasRecordedHash): if the
         local file contradicts it, or SHA256SUMS and <file>.sha256 disagree ->
         FAIL-LOCAL, nothing copied, and Trust says what the NAS copy tells us:
         NAS (it matches the record - restore the local file from it), RECORD (local
         and NAS agree with each other, not with the record - the record is probably
         wrong), NONE ON NAS (not archived; or the record is wrong), NEITHER.
      2. NAS copy absent -> copy to <name>.cf-partial (a leftover temp FILE is
         replaced), clear ReadOnly on the temp, stamp it (Get-NasCopyStamp), sha256
         it against the local file, rename it into place - never over an existing
         name - and restore ReadOnly if the local file has it.            COPIED
      3. NAS copy INCOMPLETE = stamped inside robocopy's unfinished-copy window while
         the local stamp is not -> the same verified temp copy; right before
         replacing, the NAS file is read AGAIN and only replaced if still
         incomplete (another run may have finished it).                  REPAIRED
      4. NAS copy complete, same size and (clamped) timestamp, not -AlwaysHash
         -> left alone, not hashed.                                      PRESENT
      5. NAS copy complete otherwise -> both hashed: equal -> PRESENT; different ->
         MISMATCH, NOTHING written; Trust LOCAL (the local file matches its record) or
         UNKNOWN (no record).
    A stale <name>.cf-partial FILE beside a PRESENT file is removed (not under
    -VerifyOnly / -DryRun). Whatever goes wrong after our temp was created, the temp
    is removed (finally). -VerifyOnly writes nothing (ABSENT / INCOMPLETE reported);
    -DryRun reports WOULD-COPY / WOULD-REPAIR and writes nothing. File I/O goes
    through cmdlets only, so a test harness can map a UNC path onto a stand-in.
    Returns @{ Rel; Status; Detail; Trust }.
  #>
  param([string]$LocalFile, [string]$NasFile, [string]$Rel, [switch]$AlwaysHash, [switch]$VerifyOnly, [switch]$DryRun)
  $r = @{ Rel = $Rel; Status = ''; Detail = ''; Trust = '' }
  $tmp = "$NasFile.cf-partial"
  foreach ($odd in @($NasFile, $tmp)) {
    if (Test-NasOddEntry $odd) {
      $r.Status = 'FAIL-COPY'; $r.Detail = "refusing: $odd is a directory or a reparse point (junction/symlink) - left untouched"; return $r
    }
  }
  $li = Get-Item -LiteralPath $LocalFile -Force
  $stamp = Get-NasCopyStamp $li.LastWriteTimeUtc
  $clampNote = $(if ($stamp -ne $li.LastWriteTimeUtc) { " (NAS stamp clamped to 1980-01-04; local stamp $($li.LastWriteTimeUtc.ToString('s'))Z)" } else { '' })
  $rec = Get-NasRecordedHash $LocalFile
  $recorded = $rec.Hash
  $present = Test-Path -LiteralPath $NasFile -PathType Leaf
  $lh = $null
  if ($rec.Conflict -or $recorded) {
    $lh = Get-NasSha256 $LocalFile
    if ($rec.Conflict -or $lh -ne $recorded) {
      $r.Status = 'FAIL-LOCAL'
      if ($rec.Conflict) {
        $r.Detail = "the recorded checksums disagree ($($rec.Conflict)); local=$lh - not copied"
        $r.Trust = 'RECORD (fix the recorded checksum first; neither record is trusted)'
        return $r
      }
      $r.Detail = "local=$lh recorded=$recorded ($($rec.Source)) - the LOCAL file contradicts its recorded checksum; not copied"
      if ($present) {
        $nh = Get-NasSha256 $NasFile
        if ($nh -eq $recorded) { $r.Trust = 'NAS (the NAS copy matches the recorded checksum - restore the local file from it)' }
        elseif ($nh -eq $lh) { $r.Trust = 'RECORD (the local file and the NAS copy agree with each other but not with the record - the recorded checksum is probably wrong)' }
        else { $r.Trust = 'NEITHER (local, NAS copy and record all differ - investigate; the record itself may be wrong)' }
      } else { $r.Trust = 'NONE ON NAS (never archived - recover the file from another copy, or check whether the recorded checksum is wrong)' }
      return $r
    }
  }
  $incomplete = $false
  if ($present) {
    $ni = Get-Item -LiteralPath $NasFile -Force
    $incomplete = (Test-NasIncompleteStamp $ni.LastWriteTimeUtc) -and -not (Test-NasIncompleteStamp $li.LastWriteTimeUtc)
    if (-not $incomplete) {
      $dt = [math]::Abs(($ni.LastWriteTimeUtc - $stamp).TotalSeconds)
      if (-not $AlwaysHash -and $ni.Length -eq $li.Length -and $dt -le 2) {
        $r.Detail = 'same size and timestamp (not hashed)'
      } else {
        if (-not $lh) { $lh = Get-NasSha256 $LocalFile }
        $nh = Get-NasSha256 $NasFile
        if ($nh -eq $lh) { $r.Detail = "sha256=$lh" }
        else {
          $r.Status = 'MISMATCH'
          $r.Detail = "local=$lh ($($li.Length) B) nas=$nh ($($ni.Length) B)"
          if ($recorded) { $r.Trust = 'LOCAL (it matches its recorded checksum; the NAS copy is damaged - re-copy it by hand after checking)' }
          else { $r.Trust = 'UNKNOWN (no recorded checksum beside the local file - compare by hand)' }
          return $r
        }
      }
      $r.Status = 'PRESENT'
      if (-not $VerifyOnly -and -not $DryRun -and (Test-Path -LiteralPath $tmp -PathType Leaf)) {
        if (Remove-NasTemp $tmp) { $r.Detail += '; removed a stale .cf-partial' }
      }
      return $r
    }
  }
  if ($VerifyOnly) {
    $r.Status = $(if ($incomplete) { 'INCOMPLETE' } else { 'ABSENT' })
    $r.Detail = $(if ($incomplete) { 'NAS copy carries robocopy''s unfinished-copy stamp (1980)' } else { '' })
    return $r
  }
  if ($DryRun) { $r.Status = $(if ($incomplete) { 'WOULD-REPAIR' } else { 'WOULD-COPY' }); return $r }
  $done = $false
  try {
    if (-not $lh) { $lh = Get-NasSha256 $LocalFile }
    $dir = Split-Path -Parent $NasFile
    if (-not (Test-Path -LiteralPath $dir -PathType Container)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (-not (Remove-NasTemp $tmp)) { throw "a leftover $tmp could not be removed" }
    Copy-Item -LiteralPath $LocalFile -Destination $tmp -Force -ErrorAction Stop
    $ti = Get-Item -LiteralPath $tmp -Force
    if ($ti.IsReadOnly) { $ti.IsReadOnly = $false }
    $ti.LastWriteTimeUtc = $stamp
    $th = Get-NasSha256 $tmp
    if ($th -ne $lh) { throw "the copy does not verify: local=$lh copy=$th" }
    if ($incomplete) {
      # Look again: another run may have finished the file since we decided.
      $ni2 = Get-Item -LiteralPath $NasFile -Force
      if (-not (Test-NasIncompleteStamp $ni2.LastWriteTimeUtc)) {
        $nh2 = Get-NasSha256 $NasFile
        if ($nh2 -eq $lh) { $r.Status = 'PRESENT'; $r.Detail = "sha256=$lh (completed by another run meanwhile)"; return $r }
        $r.Status = 'MISMATCH'; $r.Detail = "local=$lh nas=$nh2 (written by another run meanwhile)"
        $r.Trust = $(if ($recorded) { 'LOCAL (it matches its recorded checksum)' } else { 'UNKNOWN (no recorded checksum - compare by hand)' })
        return $r
      }
      Move-Item -LiteralPath $tmp -Destination $NasFile -Force -ErrorAction Stop; $r.Status = 'REPAIRED'
    } else {
      try { Move-Item -LiteralPath $tmp -Destination $NasFile -ErrorAction Stop }
      catch { throw "could not rename into place ($($_.Exception.Message)) - existing file untouched" }
      $r.Status = 'COPIED'
    }
    $done = $true
    if ($li.IsReadOnly) {
      try { (Get-Item -LiteralPath $NasFile -Force).IsReadOnly = $true } catch { }
    }
    $r.Detail = "sha256=$lh$clampNote"
    return $r
  } catch {
    $r.Status = 'FAIL-COPY'; $r.Detail = "$($_.Exception.Message)"
    return $r
  } finally {
    if (-not $done -and -not (Remove-NasTemp $tmp)) { $r.Detail += " - WARNING: temp $tmp could not be removed" }
    elseif (-not $done -and $r.Status -eq 'FAIL-COPY') { $r.Detail += ' - temp removed' }
  }
}

function Find-NasLinks {
  <#
    Every reparse point (junction, directory or file symbolic link) at or below
    $Path, the root itself included, at ANY depth. PS 5.1's recursive listing lists a
    link but does not descend into a nested one and raises no error, so a folder
    reached through a link would silently drop out of an archive pass - the pass
    refuses links instead of following them. Returns the full paths.
  #>
  param([string]$Path)
  $found = @()
  $top = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
  if (($top.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { return @($top.FullName) }
  foreach ($i in @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)) {
    if (($i.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { $found += $i.FullName }
  }
  return $found
}

function Get-NasArchiveFiles {
  <#
    The files the weekly archive pass covers: every file BELOW a subdirectory of
    $Source. Top-level files of ./backup/ are the git-tracked sidecar sources
    (Dockerfile, README.md, *.sh, .dockerignore) - git keeps their history, and a
    never-replacing archive would turn every edit of one into a MISMATCH failure -
    so they are skipped by position, with no dependency on git being installed
    (test-nas-sync.ps1 G1/G2 check that every tracked file there IS top-level).
    Hidden files are included (-Force). A directory that cannot be listed (access
    denied, ...) THROWS, and so does ANY junction or symbolic link anywhere under
    $Source (Find-NasLinks), naming it: a partial listing must fail the pass, never
    shrink it, and a link is refused, never followed.
    Returns @{ File; Rel } in path order.
  #>
  param([string]$Source)
  $src = $Source.TrimEnd('\')
  $links = @(Find-NasLinks $src)
  if ($links.Count -gt 0) {
    throw ("refusing to archive through links - replace each with the real folder/file: " + ($links -join ', '))
  }
  $out = @()
  foreach ($d in @(Get-ChildItem -LiteralPath $src -Directory -Force -ErrorAction Stop | Sort-Object Name)) {
    foreach ($f in @(Get-ChildItem -LiteralPath $d.FullName -Recurse -File -Force -ErrorAction Stop | Sort-Object FullName)) {
      $out += @{ File = $f.FullName; Rel = $f.FullName.Substring($src.Length + 1) }
    }
  }
  return $out
}

function Invoke-NasArchivePass {
  # The weekly archive pass: Sync-NasArchiveFile over Get-NasArchiveFiles, each file on
  # its own - one that throws (locked, unreadable ...) is a FAIL-COPY naming it and the
  # pass goes on. A listing failure throws (the caller turns it into a failed run).
  param([string]$Source, [string]$Destination, [switch]$DryRun)
  $results = @()
  foreach ($e in (Get-NasArchiveFiles -Source $Source)) {
    try {
      $results += Sync-NasArchiveFile -LocalFile $e.File -NasFile (Join-Path $Destination $e.Rel) -Rel $e.Rel -DryRun:$DryRun
    } catch {
      $results += @{ Rel = $e.Rel; Status = 'FAIL-COPY'; Detail = "$($_.Exception.Message)"; Trust = '' }
    }
  }
  return $results
}
