# scripts/backup-to-nas.ps1
#
# Weekly NAS sync of the portal backup archives, two-slot alternating.
# Mirrors `./backups/` to `<NasUncRoot>\slot-A\` or `<NasUncRoot>\slot-B\`
# depending on the ISO week number's parity. After two weeks of operation
# you have one ~1-week-old archive set AND one ~2-week-old archive set on
# the NAS -- if one slot gets corrupted, the other is still good.
#
# Sources:
#   ./backups/caddy/
#   ./backups/authelia/
#   ./backups/mnemory/        (existing -- pre-portal)
#   ./backups/openwebui/      (existing -- pre-portal)
#   ./backups/little-coder/   (existing -- pre-portal)
# All of `./backups/` gets mirrored, so any future backup container
# (writing under ./backups/) is included automatically.
#
# Parameters:
#   -NasUncRoot    Required. e.g. \\192.168.1.50\backups\portal
#                  The script appends \slot-A or \slot-B based on the
#                  ISO week.
#   -NasVaultPath  Optional. Path to the DPAPI-encrypted NAS credentials
#                  file. Default: secrets/nas-backup-vault.dat under the
#                  project root. Created by scripts/set-nas-credential.ps1.
#                  The file is DPAPI-encrypted with LocalMachine scope so
#                  the scheduled task (running under S4U logon with no
#                  password) can still decrypt it.
#   -DryRun        Optional. Prints what Robocopy would do, does nothing.
#
# Logs:
#   ./logs/nas-sync-YYYY-MM-DD.log    (per-day)
#
# Failure alerting:
#   On non-zero Robocopy exit codes (>= 8), the script POSTs a JSON alert
#   to the portal-alerter at http://portal-alerter:8080/alert IF the alerter
#   is reachable from the host -- operator gets an email about the failure.
#
# Exit codes:
#   0  - sync succeeded (Robocopy exit 0-7 are "success or minor warnings")
#   1  - parameter / setup error
#   2  - Robocopy reported a real failure (>= 8)
#   3  - integrity verification of a .sha256 sentinel failed
#
# Run manually:
#   .\scripts\backup-to-nas.ps1 -NasUncRoot "\\192.168.1.50\backups\portal"
#
# Install as scheduled task:
#   .\scripts\install-nas-backup-task.ps1 -NasUncRoot "\\192.168.1.50\backups\portal"

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$NasUncRoot,

  [Parameter(Mandatory = $false)]
  [string]$NasVaultPath,

  [switch]$DryRun
)

# Native command stderr is benign in this script (Robocopy writes status to
# both streams). Keep Continue and check exit codes explicitly.
$ErrorActionPreference = 'Continue'

$projectRoot = Split-Path -Parent $PSScriptRoot
$backupSrc = Join-Path $projectRoot 'backups'
$logDir = Join-Path $projectRoot 'logs'
$dateStamp = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $logDir "nas-sync-$dateStamp.log"

# Use ISO-week parity for slot selection. Culture-invariant; works year-round
# regardless of locale.
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$cal = $culture.Calendar
$rule = [System.Globalization.CalendarWeekRule]::FirstFourDayWeek
$weekNum = $cal.GetWeekOfYear((Get-Date), $rule, [DayOfWeek]::Monday)
$slot = if ($weekNum % 2 -eq 0) { 'slot-A' } else { 'slot-B' }
$nasDest = Join-Path $NasUncRoot $slot

# Ensure log dir exists
if (-not (Test-Path $logDir)) {
  New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

function Write-LogLine {
  param([string]$Msg, [string]$Level = 'INFO')
  $ts = (Get-Date -Format 'yyyy-MM-ddTHH:mm:sszzz')
  $line = "[$ts] [$Level] $Msg"
  Write-Host $line
  Add-Content -Path $logFile -Value $line
}

# Source the shared portal-alerter client. Provides Send-PortalAlert which
# (a) builds the JSON body via ConvertTo-Json -- avoiding the double-escape
# bug that the inline implementation had with UNC paths, and (b) actually
# checks wget's exit code + the alerter's response body, so we know whether
# the email truly dispatched. See scripts/lib/portal-alerter-client.ps1.
. (Join-Path $PSScriptRoot 'lib\portal-alerter-client.ps1')

function Send-AlerterFailure {
  param([string]$Reason)
  $logLine = "nas-sync to $nasDest failed: $Reason"
  $result = Send-PortalAlert -Severity 'high' `
    -Event 'nas-backup.failure' `
    -LogLine $logLine
  if ($result.Ok) {
    Write-LogLine "alert dispatched to portal-alerter (Gmail send confirmed)" 'INFO'
  } else {
    Write-LogLine "alert dispatch FAILED: $($result.Reason)" 'WARN'
  }
}

Write-LogLine "=== NAS sync start ==="
Write-LogLine "source        : $backupSrc"
Write-LogLine "destination   : $nasDest"
Write-LogLine "iso week      : $weekNum -> $slot"
Write-LogLine "dry-run mode  : $($DryRun.IsPresent)"

# Verify the source exists and contains files
if (-not (Test-Path $backupSrc)) {
  Write-LogLine "backup source $backupSrc does not exist" 'ERROR'
  Send-AlerterFailure -Reason "backup source missing"
  exit 1
}
$srcFileCount = (Get-ChildItem -Path $backupSrc -Recurse -File | Measure-Object).Count
Write-LogLine "source file count: $srcFileCount"
if ($srcFileCount -eq 0) {
  Write-LogLine "backup source is empty - nothing to sync (this would mirror-empty the slot, which is destructive). Aborting." 'ERROR'
  Send-AlerterFailure -Reason "backup source empty"
  exit 1
}

# Establish the SMB session explicitly via `net use` with EXPLICIT
# credentials read from a DPAPI-LocalMachine-encrypted vault file.
#
# Why explicit creds instead of relying on cmdkey:
#   - cmdkey-stored credentials live in the user's Credential Vault, which
#     S4U-logon scheduled tasks cannot read.
#   - The operator may have other (e.g., personal admin) SMB credentials
#     cached for this NAS that Windows would prefer over the dedicated
#     backup user. Explicit creds at net-use time override that.
#   - The NAS audit log shows exactly the dedicated backup user as the
#     authenticator.
#
# Extract \\server\share from the full UNC path. Anything beyond the share
# name is a subdirectory.
$uncParts = $NasUncRoot.TrimStart('\') -split '\\', 3
if ($uncParts.Count -lt 2) {
  Write-LogLine "NasUncRoot doesn't look like a valid UNC path (\\server\share[\subpath]): $NasUncRoot" 'ERROR'
  Send-AlerterFailure -Reason "invalid UNC: $NasUncRoot"
  exit 1
}
$nasServer = $uncParts[0]
$nasShare = $uncParts[1]
$shareRoot = "\\$nasServer\$nasShare"

# Resolve and read the DPAPI vault file
if (-not $NasVaultPath) {
  $NasVaultPath = Join-Path $projectRoot 'secrets\nas-backup-vault.dat'
}
if (-not (Test-Path $NasVaultPath)) {
  Write-LogLine "NAS credentials vault not found: $NasVaultPath" 'ERROR'
  Write-LogLine "Run: .\scripts\set-nas-credential.ps1   (one-time setup, then re-run this)" 'INFO'
  Send-AlerterFailure -Reason "vault missing at $NasVaultPath"
  exit 1
}

Write-LogLine "loading NAS credentials from vault: $NasVaultPath"
try {
  Add-Type -AssemblyName System.Security
  $encryptedBytes = [System.IO.File]::ReadAllBytes($NasVaultPath)
  $decryptedBytes = [System.Security.Cryptography.ProtectedData]::Unprotect(
    $encryptedBytes,
    $null,
    [System.Security.Cryptography.DataProtectionScope]::LocalMachine
  )
  $payload = [System.Text.Encoding]::UTF8.GetString($decryptedBytes)
  # Zero the byte arrays so the plaintext password isn't sitting in memory
  for ($i = 0; $i -lt $decryptedBytes.Length; $i++) { $decryptedBytes[$i] = 0 }
  $decryptedBytes = $null
}
catch {
  Write-LogLine "failed to decrypt vault: $($_.Exception.Message)" 'ERROR'
  Send-AlerterFailure -Reason "vault decrypt failed: $($_.Exception.Message)"
  exit 1
}

$lines = $payload -split "`n", 2
$nasUser = $lines[0]
$nasPassPlain = $lines[1]
$payload = $null
if ([string]::IsNullOrEmpty($nasUser) -or [string]::IsNullOrEmpty($nasPassPlain)) {
  Write-LogLine "vault decoded but user or password is empty" 'ERROR'
  Send-AlerterFailure -Reason "vault decoded with empty fields"
  exit 1
}
Write-LogLine "vault loaded (user: $nasUser)"

# `net use` with positional <password> and named /user:<user>. We pass the
# password as the second positional arg, /user: as a named arg. This is the
# documented net-use syntax for ad-hoc credentials.
Write-LogLine "establishing SMB session: net use $shareRoot /user:$nasUser <pass-redacted> /persistent:no"
& net.exe use $shareRoot $nasPassPlain "/user:$nasUser" /persistent:no 2>&1 | ForEach-Object { Write-LogLine "  net use: $_" }
$netExit = $LASTEXITCODE
# Clear plaintext password from memory immediately after net use returned
$nasPassPlain = $null
[System.GC]::Collect()

if ($netExit -ne 0) {
  Write-LogLine "net use failed (exit $netExit). Check: vault has correct dedicated-user creds; NAS user has SMB access to the share; share path is reachable." 'ERROR'
  Write-LogLine "Diagnose with: Test-NetConnection $nasServer -Port 445" 'INFO'
  Send-AlerterFailure -Reason "net use to $shareRoot failed (exit $netExit)"
  exit 1
}
Write-LogLine "SMB session established"

# Run Robocopy. Use PowerShell's call operator (&) rather than Start-Process
# because Start-Process -ArgumentList does NOT quote args containing spaces,
# which trips on `D:\Open WebUI\...` paths (Robocopy parses them as two
# separate paths and returns exit 16 "no files copied"). The call operator
# passes each element of an array as a separate, properly-quoted argument
# to the native exe.
#
# /MIR mirrors (deletes files in dest not in source),
# /R:3 /W:5 retries, /Z restartable, /MT:8 parallel copies, /LOG+ append.
# /XJ excludes junction points just in case. /NDL hides directory listings.
$logArg = "/LOG+:$logFile"

if ($DryRun) {
  Write-LogLine "(DRY RUN -- /L added; no files will be written)"
  Write-LogLine "robocopy: `"$backupSrc`" `"$nasDest`" /L /MIR /R:3 /W:5 /Z /MT:8 /XJ $logArg /NDL"
  & robocopy.exe $backupSrc $nasDest /L /MIR /R:3 /W:5 /Z /MT:8 /XJ $logArg /NDL | Out-Null
}
else {
  Write-LogLine "robocopy: `"$backupSrc`" `"$nasDest`" /MIR /R:3 /W:5 /Z /MT:8 /XJ $logArg /NDL"
  & robocopy.exe $backupSrc $nasDest /MIR /R:3 /W:5 /Z /MT:8 /XJ $logArg /NDL | Out-Null
}
$rcExit = $LASTEXITCODE
Write-LogLine "robocopy exit code: $rcExit"

if ($rcExit -ge 8) {
  Write-LogLine "robocopy reported a failure (exit >= 8)" 'ERROR'
  Send-AlerterFailure -Reason "robocopy exit $rcExit"
  exit 2
}

# Bit map for Robocopy exit codes (for the log):
#   1 = files were copied
#   2 = extra files/dirs detected (in dest, removed by /MIR)
#   4 = mismatched files/dirs
#   0 = nothing changed (no-op sync)
$exitDescription = @()
if ($rcExit -band 1) { $exitDescription += 'files copied' }
if ($rcExit -band 2) { $exitDescription += 'extras removed (mirror)' }
if ($rcExit -band 4) { $exitDescription += 'mismatches handled' }
if ($exitDescription.Count -eq 0) { $exitDescription = @('no changes') }
Write-LogLine "robocopy summary: $($exitDescription -join ', ')"

# If this was a dry run, stop here without verification.
if ($DryRun) {
  Write-LogLine "=== NAS sync (DRY RUN) complete ==="
  exit 0
}

# Spot-check integrity: pick the newest .sha256 sentinel in the dest and
# verify the tarball it references hashes correctly. This catches NAS-side
# corruption (e.g., partial copy, bit rot on a single file).
Write-LogLine "verifying a sample .sha256 sentinel at destination..."
$sample = Get-ChildItem -Path $nasDest -Recurse -Filter '*.sha256' -ErrorAction SilentlyContinue |
Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $sample) {
  Write-LogLine "no .sha256 sentinels found in $nasDest -- skipping integrity check" 'WARN'
}
else {
  $sentinelLine = (Get-Content -Path $sample.FullName -First 1).Trim()
  # sentinel format: "<hash>  <absolute-path>" (sha256sum convention)
  $expectedHash = ($sentinelLine -split '\s+', 2)[0]
  # The path in the sentinel is the in-container absolute path; we resolve
  # to a sibling file with the same basename.
  $tarballName = ($sample.Name -replace '\.sha256$', '')
  $tarballPath = Join-Path $sample.DirectoryName $tarballName
  if (-not (Test-Path $tarballPath)) {
    Write-LogLine "sentinel found but expected tarball $tarballPath missing" 'ERROR'
    Send-AlerterFailure -Reason "sentinel orphaned, tarball missing at NAS"
    exit 3
  }
  $actualHash = (Get-FileHash -Path $tarballPath -Algorithm SHA256).Hash.ToLower()
  if ($actualHash -eq $expectedHash.ToLower()) {
    Write-LogLine "integrity check OK ($($sample.Name))" 'INFO'
  }
  else {
    Write-LogLine "integrity check FAILED: expected $expectedHash, got $actualHash" 'ERROR'
    Send-AlerterFailure -Reason "integrity verification failed at $tarballPath"
    exit 3
  }
}

# Tear down the SMB session we established. Doesn't affect any pre-existing
# mappings; only removes the one this script created. Failure here is
# non-fatal (we're at the exit anyway), so log and move on.
Write-LogLine "tearing down SMB session: net use $shareRoot /delete"
& net.exe use $shareRoot /delete /yes 2>&1 | ForEach-Object { Write-LogLine "  net use /delete: $_" }

Write-LogLine "=== NAS sync complete ==="
exit 0
