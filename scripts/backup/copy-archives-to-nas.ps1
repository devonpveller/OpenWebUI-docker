# scripts/backup/copy-archives-to-nas.ps1
#
# ONE-TIME (and re-runnable) copy of the cold archives under ./backup/<dir>/ to the
# NAS archive folder, with a sha256 check of every file on BOTH sides. It exists
# because those archives predate the archive pass in backup-to-nas.ps1; the first
# transfer is done by hand so every file is PROVEN on the NAS (at the 77-87 MB/s the
# 2026-09-27 runs measured, 17 GB is minutes - time is not the reason).
#
# Per file it runs Sync-NasArchiveFile from nas-sync-lib.ps1 - the same code the
# weekly archive pass runs - with -AlwaysHash, so an existing NAS copy is always
# re-hashed:
#   - absent on the NAS: copied to <name>.cf-partial, timestamp preserved, sha256
#     checked against the local file, renamed into place          VERIFIED (copied)
#   - NAS copy left incomplete by an interrupted robocopy (stamped inside its
#     1979-12-31..1980-01-02 unfinished-copy window): the
#     same verified temp copy replaces it                          VERIFIED (repaired)
#   - NAS copy complete and equal                                  VERIFIED (already present)
#   - NAS copy complete and different: MISMATCH, nothing written, and which side the
#     recorded checksum (SHA256SUMS / <file>.sha256) vouches for
#   - local file contradicting its recorded checksum (SHA256SUMS entry or
#     <file>.sha256): FAIL LOCAL, not copied, with a Trust verdict for the NAS copy
#   - copy hash wrong, or the final name appeared meanwhile: FAIL COPY, nothing
#     overwritten, our temp removed
#   - a stale *.cf-partial beside a verified copy is removed
# and an entry in a SHA256SUMS whose file is not there locally is MISSING LOCAL.
#
# It deletes nothing but its own *.cf-partial temps and never overwrites a complete
# file; a finished copy never carries a stamp before 1980-01-03. -VerifyOnly hashes
# and reports without writing anything (ABSENT / INCOMPLETE for what is not there).
#
# Exit: 0 every file VERIFIED; 1 any ABSENT / INCOMPLETE / MISMATCH / FAIL /
# MISSING LOCAL; 2 setup error (including a destination inside a slot-A / slot-B
# folder, judged on the normalised path: `x\..\slot-A`, `/slot-A`, `slot-A.`,
# `slot-A ` and any letter case count).
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
    # -Force: hidden files too, exactly as the weekly pass (Get-NasArchiveFiles) lists them.
    $files = @(Get-ChildItem -LiteralPath $srcDir -Recurse -File -Force | Sort-Object FullName)
    Write-Host "== $d ($($files.Count) files)"
    foreach ($sumDir in @(@($srcDir) + @(Get-ChildItem -LiteralPath $srcDir -Recurse -Directory -Force | ForEach-Object { $_.FullName }))) {
      $listed = Read-NasSha256Sums $sumDir
      foreach ($name in $listed.Keys) {
        if (-not (Test-Path -LiteralPath (Join-Path $sumDir $name) -PathType Leaf)) {
          Write-Host "MISSING LOCAL  $($sumDir.Substring($Source.Length + 1))\$name  (listed in SHA256SUMS, no such file)" -ForegroundColor Red
          $bad++
        }
      }
    }
    foreach ($f in $files) {
      $rel = $f.FullName.Substring($Source.Length + 1)
      try { $x = Sync-NasArchiveFile -LocalFile $f.FullName -NasFile (Join-Path $Destination $rel) -Rel $rel -AlwaysHash -VerifyOnly:$VerifyOnly }
      catch { $x = @{ Rel = $rel; Status = 'FAIL-COPY'; Detail = "$($_.Exception.Message)"; Trust = '' } }
      switch ($x.Status) {
        'COPIED'   { Write-Host "$rel  $($f.Length)  $($x.Detail)  VERIFIED (copied)"; $ok++ }
        'REPAIRED' { Write-Host "$rel  $($f.Length)  $($x.Detail)  VERIFIED (repaired - the NAS copy was incomplete)"; $ok++ }
        'PRESENT'  { Write-Host "$rel  $($f.Length)  $($x.Detail)  VERIFIED (already present)"; $ok++ }
        'MISMATCH' { Write-Host "MISMATCH  $rel  $($x.Detail) - NAS file left untouched. Trust: $($x.Trust)" -ForegroundColor Red; $bad++ }
        'FAIL-LOCAL' { Write-Host "FAIL LOCAL  $rel  $($x.Detail). Trust: $($x.Trust)" -ForegroundColor Red; $bad++ }
        'FAIL-COPY' { Write-Host "FAIL COPY  $rel  $($x.Detail)" -ForegroundColor Red; $bad++ }
        default    { Write-Host "$($x.Status)  $rel  $($x.Detail)" -ForegroundColor Yellow; $bad++ }
      }
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
