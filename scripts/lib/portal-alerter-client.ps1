# scripts/lib/portal-alerter-client.ps1
#
# Shared PowerShell client for the portal-alerter HTTP service.
# Source via dot-load from any host-side script that wants to dispatch
# alerts or trigger digests:
#
#   . (Join-Path $PSScriptRoot 'lib\portal-alerter-client.ps1')
#   Send-PortalAlert -Severity 'high' -Event 'nas-backup.failure' `
#                    -LogLine 'robocopy exit 16 to \\nas\share'
#
# Why this exists (post-2026-05-29 audit, Part 2 of the snapshot plan):
#   The old inline `Send-AlerterFailure` in backup-to-nas.ps1 had two
#   compounding bugs that silently swallowed failures:
#
#   1. UNC paths with `\\` interpolated into a JSON string via
#      ConvertTo-Json produced double-escaped `\\\\` sequences that the
#      alerter's `req.json()` rejected with HTTP 400. PowerShell didn't
#      surface this as an error.
#   2. `docker exec ... wget 2>&1 | Out-Null` discarded stderr AND
#      $LASTEXITCODE. The wrapping try/catch only caught PowerShell
#      exceptions, not native-command failures. So the script unconditionally
#      logged "alert dispatched" regardless of actual HTTP outcome.
#
# This client fixes both:
#   - Builds the JSON via `ConvertTo-Json -Depth 5` from a hashtable
#     (correct escaping by the cmdlet, not by hand).
#   - Writes JSON to a temp file, `docker cp`s it into the container,
#     then runs wget with --post-file= (avoiding shell-quoting of the body).
#   - Captures the HTTP response body, parses it, and only reports success
#     when the alerter actually returned `{"ok": true, ...}`.

if (-not (Get-Variable -Name PortalAlerterContainer -Scope Script -ErrorAction SilentlyContinue)) {
  # Default container name. Override globally before sourcing if needed.
  $script:PortalAlerterContainer = 'portal-alerter'
}

function Invoke-PortalAlerter {
  <#
    .SYNOPSIS
      Internal: POST a JSON body to one of portal-alerter's endpoints.
    .DESCRIPTION
      Used by Send-PortalAlert and Send-PortalDigest. Not intended for
      direct call by host scripts.
    .OUTPUTS
      PSCustomObject with: Ok (bool), HttpExit (int), ResponseRaw (string),
      Reason (string when !Ok).
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [string]$Path,                # e.g. '/alert' or '/run'

    [Parameter(Mandatory = $true)]
    [hashtable]$Body,             # serialized via ConvertTo-Json

    [string]$Container = $script:PortalAlerterContainer
  )

  # Pre-flight: container must be running, else there's nothing to POST to.
  $running = docker inspect $Container --format '{{.State.Running}}' 2>$null
  if ($running -ne 'true') {
    return [PSCustomObject]@{
      Ok          = $false
      HttpExit    = -1
      ResponseRaw = ''
      Reason      = "$Container is not running (state: $running)"
    }
  }

  # Build JSON via cmdlet so backslashes etc. are escaped correctly.
  $json = $Body | ConvertTo-Json -Depth 5 -Compress

  # docker cp INTO a read_only:true container fails ("container rootfs is
  # marked read-only") even when targeting a tmpfs mount, because docker cp
  # doesn't traverse the overlay properly. Workaround: stream the JSON in
  # via `docker exec -i` stdin -> `cat > /tmp/file` (which writes into the
  # tmpfs from inside the container, bypassing the rootfs RO restriction).
  # Then wget reads the file and POSTs. All cleanup happens in the same
  # shell so a failure leaves nothing behind.
  $tmpName = "portal-alert-{0:yyyyMMddHHmmssfff}-{1}.json" -f (Get-Date), ([guid]::NewGuid().ToString().Substring(0, 8))
  $containerTmp = "/tmp/$tmpName"

  # Heredoc-style: stdin -> cat into tmp; wget reads tmp; rm tmp. The
  # parent shell's exit code propagates from wget (the last "real" command),
  # so $LASTEXITCODE reflects HTTP success/failure.
  $remoteShell = "cat > $containerTmp && wget -q -O- --header='Content-Type: application/json' --post-file=$containerTmp --timeout=10 http://127.0.0.1:8080$Path; rc=`$?; rm -f $containerTmp; exit `$rc"

  $response = $json | docker exec -i $Container sh -c "$remoteShell" 2>&1
  $exit = $LASTEXITCODE

  $responseStr = [string]::Join("`n", @($response))

  if ($exit -ne 0) {
    return [PSCustomObject]@{
      Ok = $false; HttpExit = $exit; ResponseRaw = $responseStr
      Reason = "wget POST to $Path returned exit $exit; body: $responseStr"
    }
  }

  # Parse JSON and verify ok==true (the alerter contract).
  $parsed = $null
  try { $parsed = $responseStr | ConvertFrom-Json -ErrorAction Stop } catch {
    return [PSCustomObject]@{
      Ok = $false; HttpExit = 0; ResponseRaw = $responseStr
      Reason = "alerter returned exit 0 but body was not JSON: $responseStr"
    }
  }
  if (-not $parsed.ok) {
    # PowerShell 5.1 has no ?? operator; use an inline sub-expression.
    $errMsg = if ($parsed.error) { $parsed.error } else { $responseStr }
    return [PSCustomObject]@{
      Ok = $false; HttpExit = 0; ResponseRaw = $responseStr
      Reason = "alerter rejected request: $errMsg"
    }
  }

  return [PSCustomObject]@{
    Ok = $true; HttpExit = 0; ResponseRaw = $responseStr; Reason = ''
  }
}

function Send-PortalAlert {
  <#
    .SYNOPSIS
      Dispatch an instant alert to the portal-alerter -> Gmail.
    .DESCRIPTION
      Posts to portal-alerter's /alert endpoint. The alerter rate-limits
      to ALERT_RATE_LIMIT_PER_MIN (default 20) per rolling minute; excess
      events are coalesced into a single summary email at minute close.
    .PARAMETER Severity
      Required. One of: critical, high, medium, low.
    .PARAMETER Event
      Required. Short event identifier, e.g. 'nas-backup.failure'.
    .PARAMETER SourceIp
      Optional. Source IP if relevant to the event.
    .PARAMETER Username
      Optional. Username if relevant (Authelia-context alerts).
    .PARAMETER LogLine
      Optional. The trimmed log line that triggered this alert.
    .OUTPUTS
      PSCustomObject with .Ok (bool) and .Reason (string on failure).
  #>
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('critical', 'high', 'medium', 'low')]
    [string]$Severity,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Event,

    [string]$SourceIp = '',
    [string]$Username = '',
    [string]$LogLine = ''
  )

  # PS 5.1 has no Get-Date -AsUTC; use [DateTime]::UtcNow directly.
  $body = @{
    severity      = $Severity
    event         = $Event
    timestamp_utc = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ')
  }
  if ($SourceIp) { $body.source_ip = $SourceIp }
  if ($Username) { $body.username = $Username }
  if ($LogLine)  { $body.log_line = $LogLine }

  Invoke-PortalAlerter -Path '/alert' -Body $body
}

function Send-PortalDigest {
  <#
    .SYNOPSIS
      Trigger an ad-hoc portal traffic+threats digest email.
    .DESCRIPTION
      Posts to portal-alerter's /run endpoint. portal-cron fires this on
      the configured schedule; this helper is for one-off operator
      invocations or for backup scripts that want to ship a periodic
      digest of their own.
    .PARAMETER WindowHours
      Optional. Override the configured DIGEST_WINDOW_HOURS for this run.
    .OUTPUTS
      PSCustomObject with .Ok (bool) and .Reason (string on failure).
  #>
  [CmdletBinding()]
  param(
    [int]$WindowHours = 0
  )

  $body = @{}
  if ($WindowHours -gt 0) { $body.window_hours = $WindowHours }

  Invoke-PortalAlerter -Path '/run' -Body $body
}
