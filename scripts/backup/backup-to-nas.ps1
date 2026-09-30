# scripts/backup/backup-to-nas.ps1
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
# Second pass - the ARCHIVES (since 2026-09-29, closeout-followups cf-nas):
#   ./backup/ (singular) holds cold archives that exist nowhere else - the
#   2026-09-13 orphan-volume tars, the May-2025 Open WebUI volumes, OWUI model
#   exports - in its SUBDIRECTORIES; its top-level files are the git-tracked
#   sidecar sources and are not archived (git keeps them). The archives go to a
#   folder OUTSIDE the slots - by default a sibling of -NasUncRoot named
#   `archive` (\\nas\backups\ai-stack\portal -> \\nas\backups\ai-stack\archive) -
#   one file at a time (Invoke-NasArchivePass in nas-sync-lib.ps1): a missing
#   file is copied to a temp name, sha256-verified, then renamed into place; a
#   NAS copy left INCOMPLETE by an interrupted robocopy (stamped inside its
#   unfinished-copy window, 1979-12-31..1980-01-02; older stamps are complete) is
#   re-copied the same way (our own copies are never stamped before 1980-01-04,
#   so they can never look like that); a COMPLETE NAS copy is never replaced
#   (one exception: a copy ANOTHER tool stamped inside that window looks
#   unfinished and is re-copied) and
#   nothing but our own temps is deleted, so an archive outlives its local file.
#   A complete NAS copy whose content differs from the local file (MISMATCH), a
#   local file that contradicts its recorded checksum (FAIL-LOCAL) and a copy
#   that could not be written or verified (FAIL-COPY) are each an [ERROR] line -
#   with a Trust verdict where there is one - an alert, exit 2 and no completion
#   marker. Why not move the archives under ./backups/ instead: both
#   slots would hold them (~17 GB each) and the slot MIRROR would drop an archive
#   two weeks after it left D:. Threat model (stated in nas-sync-lib.ps1):
#   ordinary conditions are refused loudly; an active local writer rearranging
#   ./backup/ during a pass is out of scope - a later run reports a resulting
#   difference as a MISMATCH when size or timestamp differ; an equal-size,
#   equal-timestamp one only `copy-archives-to-nas.ps1 -VerifyOnly` catches.
#   The layout rules (sibling default, same share,
#   never inside a slot, all judged on normalised paths) are in nas-sync-lib.ps1.
#
# Parameters:
#   -NasUncRoot    Required. e.g. \\nas.example.lan\backups\portal
#                  The script appends \slot-A or \slot-B based on the
#                  ISO week.
#   -NasVaultPath  Optional. Path to the DPAPI-encrypted NAS credentials
#                  file. Default: secrets/nas-backup-vault.dat under the
#                  project root. Created by scripts/backup/set-nas-credential.ps1.
#                  The file is DPAPI-encrypted with LocalMachine scope so
#                  the scheduled task (running under S4U logon with no
#                  password) can still decrypt it.
#   -NasArchiveRoot Optional. Where the ./backup/ archives go (see above).
#                  Default: <parent of NasUncRoot>\archive. Must be absolute, on
#                  the same \\server\share and not inside slot-A/slot-B, judged
#                  after normalisation (`..`, `.`, `/`, trailing dots/spaces) and
#                  again after the IP rewrite; refused with exit 1 before any
#                  SMB session otherwise.
#   -NoArchive     Optional. Skip the archive pass (slot mirror only).
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
#   2  - Robocopy reported a real failure (>= 8), in the slot mirror or the
#        archive pass (the completion marker is then NOT written)
#   3  - integrity verification of a .sha256 sentinel failed
#
# Run manually:
#   .\scripts\backup\backup-to-nas.ps1 -NasUncRoot "\\nas.example.lan\backups\portal"
#
# Install as scheduled task:
#   .\scripts\backup\install-nas-backup-task.ps1 -NasUncRoot "\\nas.example.lan\backups\portal"

[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$NasUncRoot,

  [Parameter(Mandatory = $false)]
  [string]$NasVaultPath,

  # Open the SMB session against the NAS's IP rather than its hostname. Windows
  # keys sessions by SERVER NAME, and permits only one credential set per name,
  # so an operator with the NAS mapped (e.g. M: -> \\nas-host\share)
  # makes the backup's own `net use` fail with system error 1219. Connecting by
  # IP gives this script a separate session identity and the two coexist.
  # Resolution happens at run time, so a DHCP change is followed automatically.
  # -NoIpResolve restores the old hostname behaviour.
  [switch]$NoIpResolve,

  [Parameter(Mandatory = $false)]
  [string]$NasArchiveRoot,

  [switch]$NoArchive,

  [switch]$DryRun
)

# Native command stderr is benign in this script (Robocopy writes status to
# both streams). Keep Continue and check exit codes explicitly.
$ErrorActionPreference = 'Continue'

$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$backupSrc = Join-Path $projectRoot 'backups'
$archiveSrc = Join-Path $projectRoot 'backup'
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

# Shared helpers: .env reader, archive-root rules, the two robocopy argument sets.
. (Join-Path $PSScriptRoot 'nas-sync-lib.ps1')
$archiveDest = $null
$archiveRootError = $null
if (-not $NoArchive) {
  try { $archiveDest = Resolve-NasArchiveRoot -NasUncRoot $NasUncRoot -NasArchiveRoot $NasArchiveRoot }
  catch { $archiveRootError = $_.Exception.Message }
}

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
. (Join-Path $PSScriptRoot '..\lib\portal-alerter-client.ps1')

# Resolve an interpreter for the sysadmin-mcp notifiers (Mattermost / Telegram).
# Same resolution compact-vhdx.ps1 uses: PATH python, else the repo venv.
$pyExe = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pyExe) {
  $venvPy = Join-Path $projectRoot '.venv\Scripts\python.exe'
  if (Test-Path $venvPy) { $pyExe = $venvPy }
}
$mmPost = Join-Path $projectRoot 'scripts\sysadmin-mcp\mm_post.py'
$tgNotify = Join-Path $projectRoot 'scripts\sysadmin-mcp\telegram_notify.py'

function Send-AlerterFailure {
  <#
    ESCALATE ACROSS CHANNELS, and say which ones answered.

    This used to POST to the portal-alerter and, if that failed, write one WARN
    line to a log file nobody reads. On 2026-08-21 the alerter's Gmail OAuth
    refresh token died; it kept answering /health with ready:true and HTTP 200
    while returning 500 to every /alert. When the NAS backup-user password
    expired ~2026-09-01, the 09-06 and 09-13 failures each dutifully called this
    function, each got a 500, each logged a WARN -- and the operator found out
    23 days later by looking at the NAS by hand.

    One channel's failure must not end the escalation. Mattermost and Telegram
    are independent of the alerter (different process, different credentials,
    different transport) and the sysadmin bridge already uses both.
  #>
  param([string]$Reason)
  $logLine = "nas-sync to $nasDest failed: $Reason"
  $delivered = @()
  $failed = @()

  $result = Send-PortalAlert -Severity 'high' -Event 'nas-backup.failure' -LogLine $logLine
  if ($result.Ok) { $delivered += 'portal-alerter' }
  else {
    $failed += "portal-alerter ($($result.Reason))"
    Write-LogLine "alert dispatch FAILED via portal-alerter: $($result.Reason) - escalating" 'WARN'
  }

  # Fan out to the out-of-band channels REGARDLESS of the alerter's result. A
  # backup failure is worth two notifications; missing it entirely is not.
  $msg = "[nas-backup] ALERT: $logLine"
  foreach ($ch in @(@{ n = 'mattermost'; p = $mmPost }, @{ n = 'telegram'; p = $tgNotify })) {
    if (-not $pyExe -or -not (Test-Path $ch.p)) {
      $failed += "$($ch.n) (no interpreter or script missing)"
      continue
    }
    try {
      & $pyExe $ch.p $msg 2>&1 | Out-Null
      if ($LASTEXITCODE -eq 0) { $delivered += $ch.n } else { $failed += "$($ch.n) (exit $LASTEXITCODE)" }
    } catch {
      $failed += "$($ch.n) ($($_.Exception.Message))"
    }
  }

  if ($delivered.Count -gt 0) {
    Write-LogLine "alert delivered via: $($delivered -join ', ')" 'INFO'
  } else {
    # Every channel is down. Say so in the loudest terms available, and make it
    # unmissable in the log -- this line is what a future investigation greps for.
    Write-LogLine "ALERT UNDELIVERABLE - every channel failed: $($failed -join '; ')" 'ERROR'
    try { & msg.exe * /time:0 "ai-stack NAS BACKUP FAILED and no alert channel is reachable: $Reason" 2>$null } catch {}
  }
  if ($failed.Count -gt 0) { Write-LogLine "alert channels that failed: $($failed -join '; ')" 'WARN' }
}

Write-LogLine "=== NAS sync start ==="
Write-LogLine "source        : $backupSrc"
Write-LogLine "destination   : $nasDest"
Write-LogLine "iso week      : $weekNum -> $slot"
Write-LogLine "dry-run mode  : $($DryRun.IsPresent)"
if ($NoArchive) { Write-LogLine "archive pass  : skipped (-NoArchive)" }
else {
  Write-LogLine "archive source: $archiveSrc"
  Write-LogLine "archive dest  : $archiveDest (verified copies; a complete file is never replaced, nothing is purged)"
}

if ($archiveRootError) {
  Write-LogLine "archive root rejected: $archiveRootError" 'ERROR'
  Send-AlerterFailure -Reason "archive root rejected: $archiveRootError"
  exit 1
}

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

# -- credentials: .env FIRST, DPAPI vault as fallback --------------------------
# The vault (DPAPI LocalMachine) is the more secure store and remains supported.
# .env is the DISCOVERABLE one: on 2026-09-13 the operator went looking for "the
# hardcoded password", found nothing -- correctly, it was ciphertext in
# secrets/nas-backup-vault.dat -- and could not rotate an expired Synology
# password without reading the source. A credential nobody can find is a
# credential nobody rotates. .env is gitignored and already holds this stack's
# other secrets, so it is where an operator looks first.
#
# Precedence is .env > vault so a rotation takes effect immediately, without
# remembering to re-seal. `set-nas-credential.ps1 -FromEnv` keeps the vault in
# step for anything that still reads it.
# Get-DotEnvValue comes from nas-sync-lib.ps1 (dot-sourced above).

if (-not $NasVaultPath) {
  $NasVaultPath = Join-Path $projectRoot 'secrets\nas-backup-vault.dat'
}
$dotEnv = Join-Path $projectRoot '.env'
$nasUser = Get-DotEnvValue -Path $dotEnv -Key 'NAS_BACKUP_USER'
$nasPassPlain = Get-DotEnvValue -Path $dotEnv -Key 'NAS_BACKUP_PASSWORD'
$credSource = $null

if (-not [string]::IsNullOrEmpty($nasUser) -and -not [string]::IsNullOrEmpty($nasPassPlain)) {
  $credSource = '.env'
  Write-LogLine "loading NAS credentials from .env (NAS_BACKUP_USER / NAS_BACKUP_PASSWORD)"
}
elseif (Test-Path $NasVaultPath) {
  Write-LogLine "no NAS_BACKUP_* in .env; falling back to DPAPI vault: $NasVaultPath"
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
  $credSource = 'vault'
}
else {
  Write-LogLine "no NAS credentials: NAS_BACKUP_USER/NAS_BACKUP_PASSWORD absent from $dotEnv AND no vault at $NasVaultPath" 'ERROR'
  Write-LogLine "Fix: set NAS_BACKUP_USER / NAS_BACKUP_PASSWORD in .env, or run scripts\backup\set-nas-credential.ps1" 'INFO'
  Send-AlerterFailure -Reason "no NAS credentials in .env or vault"
  exit 1
}

if ([string]::IsNullOrEmpty($nasUser) -or [string]::IsNullOrEmpty($nasPassPlain)) {
  Write-LogLine "credentials from $credSource are incomplete (empty user or password)" 'ERROR'
  Send-AlerterFailure -Reason "credentials from $credSource had empty fields"
  exit 1
}
Write-LogLine "credentials loaded from ${credSource} (user: $nasUser)"

# `net use` with positional <password> and named /user:<user>. We pass the
# password as the second positional arg, /user: as a named arg. This is the
# documented net-use syntax for ad-hoc credentials.
# Prefer the IP as the session identity (see -NoIpResolve above). Both the
# session AND the robocopy destination must use the same spelling: connecting to
# \\<ip>\share and then writing to \\<host>\share opens a SECOND session to the
# hostname and reintroduces the very 1219 this avoids.
if (-not $NoIpResolve) {
  $resolved = $null
  try {
    $resolved = (Resolve-DnsName -Name $nasServer -Type A -ErrorAction Stop |
                 Where-Object { $_.IPAddress } | Select-Object -First 1).IPAddress
  } catch {
    try { $resolved = ([System.Net.Dns]::GetHostAddresses($nasServer) |
                       Where-Object { $_.AddressFamily -eq 'InterNetwork' } |
                       Select-Object -First 1).IPAddressToString } catch { $resolved = $null }
  }
  if ($resolved) {
    Write-LogLine "resolved $nasServer -> $resolved (session opened by IP to avoid error 1219)"
    $nasDest = $nasDest -replace [regex]::Escape("\\$nasServer\"), "\\$resolved\"
    if ($archiveDest) {
      # Re-judge the archive root in the spelling robocopy will actually get.
      $slotRootIp = $NasUncRoot -replace [regex]::Escape("\\$nasServer\"), "\\$resolved\"
      $archiveIp = $archiveDest -replace [regex]::Escape("\\$nasServer\"), "\\$resolved\"
      try { $archiveDest = Resolve-NasArchiveRoot -NasUncRoot $slotRootIp -NasArchiveRoot $archiveIp }
      catch {
        Write-LogLine "archive root rejected after the IP rewrite: $($_.Exception.Message)" 'ERROR'
        Send-AlerterFailure -Reason "archive root rejected after the IP rewrite: $($_.Exception.Message)"
        exit 1
      }
    }
    $nasServer = $resolved
    $shareRoot = "\\$nasServer\$nasShare"
    Write-LogLine "destination rewritten: $nasDest"
    if ($archiveDest) { Write-LogLine "archive dest rewritten: $archiveDest" }
  } else {
    Write-LogLine "could not resolve $nasServer to an IP; continuing with the hostname" 'WARN'
  }
}

# Drop any session THIS script previously left behind for the same share. Not a
# teardown of anyone else's mapping -- /delete on a share we are about to open.
& net.exe use $shareRoot /delete /yes 2>&1 | Out-Null

Write-LogLine "establishing SMB session: net use $shareRoot /user:$nasUser <pass-redacted> /persistent:no"
$netOut = (& net.exe use $shareRoot $nasPassPlain "/user:$nasUser" /persistent:no 2>&1 | Out-String)
$netExit = $LASTEXITCODE
# Clear plaintext password from memory immediately after net use returned
$nasPassPlain = $null
[System.GC]::Collect()
foreach ($l in ($netOut -split "`r?`n")) { if ($l.Trim()) { Write-LogLine "  net use: $($l.Trim())" } }

if ($netExit -ne 0) {
  # NAME the failure. "net use failed (exit 2)" sent the 2026-09-06 and 09-13
  # investigations nowhere; the actual cause was one line above it in the log.
  $diag = $null
  if ($netOut -match '2242' -or $netOut -match 'password of this user has expired') {
    $diag = ("NAS password for '$nasUser' has EXPIRED (system error 2242). Fix: set the new " +
             "password on the NAS, put it in .env as NAS_BACKUP_PASSWORD, then run " +
             "scripts\backup\set-nas-credential.ps1 -FromEnv. Synology expires this on a schedule, " +
             "so expect it again -- see documentation/runbooks/backup-restore-runbook.md.")
  }
  elseif ($netOut -match '1219' -or $netOut -match 'Multiple connections to a server') {
    $diag = ("CONFLICTING SMB SESSION to $nasServer (system error 1219): Windows allows one " +
             "credential set per server name and something else is already connected as a " +
             "different user (typically an operator's mapped drive). This run used " +
             $(if ($NoIpResolve) { "the HOSTNAME (-NoIpResolve); drop that switch so the session is opened by IP." }
               else { "an IP already, so the conflict is against the IP itself -- run `net use` and disconnect the clashing session." }))
  }
  elseif ($netOut -match '1326' -or $netOut -match 'user name or password is incorrect') {
    $diag = "NAS rejected the credentials for '$nasUser' (system error 1326): wrong password, or the account is disabled/locked."
  }
  elseif ($netOut -match '53' -and $netOut -match 'network path was not found') {
    $diag = "share $shareRoot not found (system error 53): NAS offline, share renamed, or SMB disabled."
  }
  if ($diag) { Write-LogLine $diag 'ERROR' }
  Write-LogLine "net use failed (exit $netExit), credentials from $credSource." 'ERROR'
  Write-LogLine "Diagnose with: Test-NetConnection $nasServer -Port 445" 'INFO'
  Send-AlerterFailure -Reason $(if ($diag) { $diag } else { "net use to $shareRoot failed (exit $netExit)" })
  exit 1
}
Write-LogLine "SMB session established"

# Run Robocopy. Use PowerShell's call operator (&) rather than Start-Process
# because Start-Process -ArgumentList does NOT quote args containing spaces,
# which trips on any repo path holding a space (Robocopy parses it as two
# separate paths and returns exit 16 "no files copied"). The call operator
# passes each element of an array as a separate, properly-quoted argument
# to the native exe.
#
# /MIR mirrors (deletes files in dest not in source),
# /R:3 /W:5 retries, /Z restartable, /MT:8 parallel copies, /LOG+ append.
# /XJ excludes junction points just in case. /NDL hides directory listings.
# The argument set is built by nas-sync-lib.ps1 (Get-NasSlotMirrorArgs) - the
# same /MIR /R:3 /W:5 /Z /MT:8 /XJ /LOG+ /NDL as before, in one tested place.
$mirrorArgs = Get-NasSlotMirrorArgs -Source $backupSrc -Destination $nasDest -LogFile $logFile -DryRun:$DryRun
if ($DryRun) { Write-LogLine "(DRY RUN -- /L added; no files will be written)" }
Write-LogLine ("robocopy: " + (($mirrorArgs | ForEach-Object { if ($_ -match ' ') { "`"$_`"" } else { $_ } }) -join ' '))
& robocopy.exe @mirrorArgs | Out-Null
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

# -- archive pass: ./backup/<subdir>/ -> archive root, verified, never replacing -----
# Runs only after the slot mirror succeeded, so a failure here never costs the
# slot. A MISMATCH or FAIL-* is alerted and makes the run exit 2 WITHOUT the
# completion marker at the bottom (check_backups.py reads that marker and
# [ERROR] lines), after the slot's integrity check has still run.
$archiveFailed = $null
if (-not $NoArchive) {
  if (-not (Test-Path -LiteralPath $archiveSrc)) {
    Write-LogLine "archive source $archiveSrc does not exist - archive pass skipped" 'WARN'
  } else {
    $skipped = @(Get-ChildItem -LiteralPath $archiveSrc -File -Force).Count
    Write-LogLine "archive pass: $archiveSrc\<subdir> -> $archiveDest$(if ($DryRun) { ' (DRY RUN - nothing written)' }); $skipped top-level file(s) skipped (git-tracked sidecar sources)"
    $results = @()
    try { $results = @(Invoke-NasArchivePass -Source $archiveSrc -Destination $archiveDest -DryRun:$DryRun) }
    catch { $results = @(@{ Rel = '(pass)'; Status = 'FAIL-COPY'; Detail = $_.Exception.Message; Trust = '' }) }
    $bad = @()
    foreach ($x in $results) {
      switch -Regex ($x.Status) {
        '^(COPIED|REPAIRED|WOULD-COPY|WOULD-REPAIR)$' { Write-LogLine "archive: $($x.Status) $($x.Rel) $($x.Detail)" }
        '^PRESENT$' { }
        '^MISMATCH$' {
          Write-LogLine "archive: MISMATCH $($x.Rel) - the NAS copy is complete but its content differs from the local file ($($x.Detail)); NOTHING was overwritten. Trust: $($x.Trust)" 'ERROR'
          $bad += $x.Rel
        }
        default {
          # FAIL-LOCAL (the local file contradicts its recorded checksum) and FAIL-COPY
          # (the copy could not be written or verified) - both mean "not safely archived".
          Write-LogLine "archive: $($x.Status) $($x.Rel) $($x.Detail)$(if ($x.Trust) { ". Trust: $($x.Trust)" })" 'ERROR'
          $bad += $x.Rel
        }
      }
    }
    $counts = ($results | Group-Object { $_.Status } | Sort-Object Name | ForEach-Object { "$($_.Name)=$($_.Count)" }) -join ' '
    Write-LogLine "archive pass summary: $(if ($counts) { $counts } else { 'no archive files' })"
    if ($bad.Count -gt 0) {
      $more = $(if ($bad.Count -gt 5) { " and $($bad.Count - 5) more (see log $logFile)" } else { '' })
      Send-AlerterFailure -Reason ("archive pass: $($bad.Count) file(s) not safely on the NAS ($archiveDest): " + (($bad | Select-Object -First 5) -join ', ') + $more)
      $archiveFailed = "$($bad.Count) archive file(s) failed"
    }
  }
}

# If this was a dry run, stop here without verification.
if ($DryRun) {
  if ($archiveFailed) {
    Write-LogLine "(DRY RUN) archive pass failed ($archiveFailed) - run NOT complete" 'ERROR'
    exit 2
  }
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

if ($archiveFailed) {
  Write-LogLine "slot mirror OK but the archive pass failed ($archiveFailed) - run NOT complete" 'ERROR'
  exit 2
}
Write-LogLine "=== NAS sync complete ==="
exit 0
