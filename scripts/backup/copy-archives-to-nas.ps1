# scripts/backup/copy-archives-to-nas.ps1
#
# ONE-TIME (and re-runnable) copy of the cold archives under ./backup/<dir>/ to the
# NAS archive folder, with a sha256 check of every file on BOTH sides. It exists
# because those archives predate the archive pass in backup-to-nas.ps1 and are
# ~17 GB: doing the first transfer by hand, verified file by file, proves the copy
# instead of assuming it (the weekly pass copies but does not hash; at the 77-87
# MB/s the 2026-09-27 runs measured, 17 GB is minutes, so time is not the reason).
# After it, the weekly archive pass copies new files only (/XC /XN /XO) and leaves
# these alone whatever happens to the local copies.
#
# What it does, per file in each -Dirs directory (recursively):
#   1. hashes the LOCAL file; if the directory carries a SHA256SUMS naming the
#      file, the local hash must match it (a local file that has rotted is NOT
#      copied - FAIL LOCAL);
#   2. if the NAS copy exists: hashes it -> VERIFIED (already present) when equal,
#      MISMATCH when not (the NAS file is left exactly as it is; you decide);
#   3. if absent: copies to <name>.cf-partial, preserving the timestamp, hashes
#      the copy, and only then renames it into place -> VERIFIED. A run killed
#      mid-copy leaves at most a *.cf-partial file, which the next run overwrites.
#      A copy whose hash differs is FAIL COPY (left as .cf-partial); a final name
#      that appeared meanwhile is never overwritten (FAIL COPY as well);
#   4. an entry in a SHA256SUMS whose file is not there locally is MISSING LOCAL.
#
# It NEVER deletes or moves anything, local or remote, and never overwrites a
# complete file. -VerifyOnly hashes and reports without writing anything
# (ABSENT for a file not yet on the NAS).
#
# Exit: 0 every file VERIFIED; 1 any ABSENT / MISMATCH / FAIL / MISSING LOCAL;
# 2 setup error (including a destination inside a slot-A / slot-B folder, judged
# on the normalised path: `x\..\slot-A`, `/slot-A`, `slot-A.` and `slot-A ` count).
#
# Usage (the landing step; the NAS path is the job's archive root - use the same
#   spelling as the job's session, i.e. the NAS's IP, to avoid system error 1219):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backup\copy-archives-to-nas.ps1 `
#     -Destination '\\192.0.2.10\backups\ai-stack\archive' -Connect
#
# -Connect opens an SMB session to the destination's \\server\share with
# NAS_BACKUP_USER / NAS_BACKUP_PASSWORD from the root .env - only when the share
# is not already reachable - and closes the session it opened at the end. The
# password is never printed.

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$Destination,

  [string]$Source,

  [string[]]$Dirs = @('orphan-volumes-2026-09-13', 'nas-archive-may2025-owui'),

  [switch]$Connect,

  [switch]$VerifyOnly
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'nas-sync-lib.ps1')
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Source) { $Source = Join-Path $projectRoot 'backup' }
$Source = $Source.TrimEnd('\')
try { $Destination = Get-NasNormalPath $Destination }
catch { Write-Host "ERROR: destination: $($_.Exception.Message)" -ForegroundColor Red; exit 2 }
if (Test-NasSlotSegment $Destination) {
  Write-Host "ERROR: destination $Destination is inside a slot-A/slot-B folder, which the weekly /MIR purges" -ForegroundColor Red
  exit 2
}
if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
  Write-Host "ERROR: source $Source does not exist" -ForegroundColor Red
  exit 2
}

function Get-Sha256Lower([string]$Path) {
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-Sha256Sums([string]$Dir) {
  # "<hash> *<name>" or "<hash>  <name>" (sha256sum text/binary forms)
  $map = @{}
  $f = Join-Path $Dir 'SHA256SUMS'
  if (-not (Test-Path -LiteralPath $f)) { return $map }
  foreach ($line in [System.IO.File]::ReadAllLines($f)) {
    if ($line -match '^([0-9a-fA-F]{64})\s+\*?(.+?)\s*$') { $map[$Matches[2]] = $Matches[1].ToLowerInvariant() }
  }
  return $map
}

$opened = $null
if ($Connect -and -not $VerifyOnly) {
  $share = Get-UncShareRoot $Destination
  if (-not $share) { Write-Host "ERROR: -Connect needs a UNC destination" -ForegroundColor Red; exit 2 }
  if (Test-Path -LiteralPath $share) {
    Write-Host "share $share already reachable - using the existing session"
  } else {
    $envFile = Join-Path $projectRoot '.env'
    $u = Get-DotEnvValue -Path $envFile -Key 'NAS_BACKUP_USER'
    $pw = Get-DotEnvValue -Path $envFile -Key 'NAS_BACKUP_PASSWORD'
    if ([string]::IsNullOrEmpty($u) -or [string]::IsNullOrEmpty($pw)) {
      Write-Host "ERROR: NAS_BACKUP_USER / NAS_BACKUP_PASSWORD missing from $envFile" -ForegroundColor Red
      exit 2
    }
    Write-Host "opening SMB session: net use $share /user:$u <redacted> /persistent:no"
    $out = (& net.exe use $share $pw "/user:$u" /persistent:no 2>&1 | Out-String)
    $rc = $LASTEXITCODE
    $pw = $null
    if ($rc -ne 0) { Write-Host "ERROR: net use failed (exit $rc): $($out.Trim())" -ForegroundColor Red; exit 2 }
    $opened = $share
  }
}

$ok = 0; $bad = 0
try {
  foreach ($d in $Dirs) {
    $srcDir = Join-Path $Source $d
    if (-not (Test-Path -LiteralPath $srcDir -PathType Container)) {
      Write-Host "FAIL  $d  (no such directory under $Source)" -ForegroundColor Red
      $bad++; continue
    }
    $files = @(Get-ChildItem -LiteralPath $srcDir -Recurse -File | Sort-Object FullName)
    Write-Host "== $d ($($files.Count) files)"
    foreach ($sumDir in @(@($srcDir) + @(Get-ChildItem -LiteralPath $srcDir -Recurse -Directory | ForEach-Object { $_.FullName }))) {
      $listed = Read-Sha256Sums $sumDir
      foreach ($name in $listed.Keys) {
        if (-not (Test-Path -LiteralPath (Join-Path $sumDir $name) -PathType Leaf)) {
          Write-Host "MISSING LOCAL  $($sumDir.Substring($Source.Length + 1))\$name  (listed in SHA256SUMS, no such file)" -ForegroundColor Red
          $bad++
        }
      }
    }
    foreach ($f in $files) {
      $rel = $f.FullName.Substring($Source.Length + 1)
      $dst = Join-Path $Destination $rel
      $sums = Read-Sha256Sums $f.DirectoryName
      $lh = Get-Sha256Lower $f.FullName
      if ($sums.ContainsKey($f.Name) -and $sums[$f.Name] -ne $lh) {
        Write-Host "FAIL LOCAL  $rel  local=$lh SHA256SUMS=$($sums[$f.Name]) - not copied" -ForegroundColor Red
        $bad++; continue
      }
      if (Test-Path -LiteralPath $dst -PathType Leaf) {
        $rh = Get-Sha256Lower $dst
        if ($rh -eq $lh) { Write-Host "$rel  $($f.Length)  sha256=$lh  VERIFIED (already present)"; $ok++ }
        else { Write-Host "MISMATCH  $rel  local=$lh nas=$rh - NAS file left untouched" -ForegroundColor Red; $bad++ }
        continue
      }
      if ($VerifyOnly) { Write-Host "ABSENT  $rel  sha256=$lh" -ForegroundColor Yellow; $bad++; continue }
      $dstDir = Split-Path -Parent $dst
      if (-not (Test-Path -LiteralPath $dstDir)) { New-Item -ItemType Directory -Path $dstDir -Force | Out-Null }
      $partial = "$dst.cf-partial"
      [System.IO.File]::Copy($f.FullName, $partial, $true)
      [System.IO.File]::SetLastWriteTimeUtc($partial, $f.LastWriteTimeUtc)
      $rh = Get-Sha256Lower $partial
      if ($rh -ne $lh) {
        Write-Host "FAIL COPY  $rel  local=$lh copy=$rh - left as $partial" -ForegroundColor Red
        $bad++; continue
      }
      try { [System.IO.File]::Move($partial, $dst) }
      catch {
        Write-Host "FAIL COPY  $rel  could not rename into place ($($_.Exception.Message)) - existing file untouched, copy left as $partial" -ForegroundColor Red
        $bad++; continue
      }
      Write-Host "$rel  $($f.Length)  sha256=$lh  VERIFIED (copied)"
      $ok++
    }
  }
} finally {
  if ($opened) {
    & net.exe use $opened /delete /yes 2>&1 | Out-Null
    Write-Host "closed the SMB session this run opened ($opened)"
  }
}

Write-Host ("summary: {0} VERIFIED, {1} not verified" -f $ok, $bad)
if ($bad -gt 0) { exit 1 }
exit 0
