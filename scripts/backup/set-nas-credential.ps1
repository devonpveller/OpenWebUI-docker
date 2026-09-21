# scripts/backup/set-nas-credential.ps1
#
# One-time setup: prompts for the dedicated NAS backup user's username and
# password, encrypts them with Windows DPAPI (LocalMachine scope), writes
# the result to secrets/nas-backup-vault.dat.
#
# Why DPAPI LocalMachine scope?
#   - User scope (default) encrypts using a key derived from the operator's
#     login password. That works for INTERACTIVE sessions but BREAKS for
#     scheduled tasks running under S4U logon (no password = no key).
#   - Machine scope encrypts with a machine-wide key. Any local process on
#     this Windows host can decrypt -- including the scheduled task. The
#     trade-off: another local user/process on the SAME machine could read
#     it. For a single-operator home stack, acceptable.
#   - The file never leaves this machine. It's gitignored via the existing
#     `secrets/` rule.
#
# Usage:
#   .\scriptsackup\set-nas-credential.ps1
#
# Run this whenever you rotate the dedicated backup user's NAS password.

[CmdletBinding()]
param(
  [Parameter(Mandatory = $false)]
  [string]$NasVaultPath,

  # Take the credentials from .env (NAS_BACKUP_USER / NAS_BACKUP_PASSWORD)
  # instead of prompting. Get-Credential CANNOT run from a scheduled task, a
  # remote session, or any non-interactive repair path -- which is exactly when
  # a rotation is most likely to be needed. Added 2026-09-13, after an expired
  # Synology password took the weekly off-site backup down for two runs.
  [switch]$FromEnv
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $NasVaultPath) {
  $NasVaultPath = Join-Path $projectRoot 'secrets\nas-backup-vault.dat'
}
$vaultDir = Split-Path -Parent $NasVaultPath
if (-not (Test-Path $vaultDir)) {
  New-Item -ItemType Directory -Path $vaultDir -Force | Out-Null
  Write-Host "Created directory: $vaultDir" -ForegroundColor DarkGray
}

Write-Host "==> Storing NAS SMB credentials (dedicated backup user)" -ForegroundColor Cyan
Write-Host "    These should be the SYNOLOGY backup-only user, NOT your admin account."
Write-Host "    Encrypted at: $NasVaultPath"
Write-Host ""

# Get-Credential gives a SecureString password by design. We pull plaintext
# from it just long enough to build the byte array, then null it immediately.
if ($FromEnv) {
  $dotEnv = Join-Path $projectRoot '.env'
  if (-not (Test-Path $dotEnv)) {
    Write-Host "ERROR: -FromEnv but no .env at $dotEnv" -ForegroundColor Red
    exit 1
  }
  function Get-DotEnvValue {
    param([string]$Path, [string]$Key)
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
  $nasUser = Get-DotEnvValue -Path $dotEnv -Key 'NAS_BACKUP_USER'
  $nasPassPlain = Get-DotEnvValue -Path $dotEnv -Key 'NAS_BACKUP_PASSWORD'
  if ([string]::IsNullOrEmpty($nasUser) -or [string]::IsNullOrEmpty($nasPassPlain)) {
    Write-Host "ERROR: NAS_BACKUP_USER / NAS_BACKUP_PASSWORD missing or empty in $dotEnv" -ForegroundColor Red
    exit 1
  }
  # Length only -- never the value. This script's whole job is to stop the
  # password existing in plaintext anywhere it is not already.
  Write-Host "Read from .env: user '$nasUser', password <$($nasPassPlain.Length) chars>" -ForegroundColor DarkGray
}
else {
  $cred = Get-Credential -Message "NAS dedicated backup user credentials (for SMB)"
  if (-not $cred) {
    Write-Host "Cancelled. No credential saved." -ForegroundColor Yellow
    exit 1
  }
  $nasUser = $cred.UserName
  $nasPassPlain = $cred.GetNetworkCredential().Password
}
if ([string]::IsNullOrEmpty($nasUser) -or [string]::IsNullOrEmpty($nasPassPlain)) {
  Write-Host "ERROR: empty username or password not allowed." -ForegroundColor Red
  exit 1
}

# Serialize as "user`npassword" (newline-separated). Both fields are UTF-8.
$payload = "$nasUser`n$nasPassPlain"
$payloadBytes = [System.Text.Encoding]::UTF8.GetBytes($payload)

# Clear plaintext password from memory ASAP. We still hold $payloadBytes,
# but that's encrypted-in-place via DPAPI in the next call.
$nasPassPlain = $null
$cred = $null

# DPAPI Protect, LocalMachine scope.
Add-Type -AssemblyName System.Security
$encryptedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
  $payloadBytes,
  $null,
  [System.Security.Cryptography.DataProtectionScope]::LocalMachine
)

# Zero the payload bytes to remove the plaintext password from memory.
for ($i = 0; $i -lt $payloadBytes.Length; $i++) { $payloadBytes[$i] = 0 }
$payloadBytes = $null
[System.GC]::Collect()

# Write encrypted file. Restrict NTFS permissions on the file too (defense
# in depth: even though anyone who can run code as a local user could
# decrypt via DPAPI machine-scope, we still keep the file ACL tight).
[System.IO.File]::WriteAllBytes($NasVaultPath, $encryptedBytes)
icacls $NasVaultPath /inheritance:r | Out-Null
# Owner gets full control; SYSTEM gets read (for scheduled task service);
# Administrators get full control (for emergency recovery).
icacls $NasVaultPath /grant:r "$($env:USERNAME):(F)" "SYSTEM:(R)" "Administrators:(F)" | Out-Null

Write-Host ""
Write-Host "==> Saved. Verification:" -ForegroundColor Green
$item = Get-Item $NasVaultPath
Write-Host "    Path  : $($item.FullName)"
Write-Host "    Size  : $($item.Length) bytes (encrypted)"
Write-Host "    ACLs  :"
icacls $NasVaultPath | ForEach-Object { Write-Host "      $_" }
Write-Host ""
Write-Host "==> Next: register or re-fire the scheduled task" -ForegroundColor Cyan
Write-Host "    .\scriptsackup\install-nas-backup-task.ps1 -NasUncRoot ""\\<your-nas>\<share>\..."" -RunNow"
