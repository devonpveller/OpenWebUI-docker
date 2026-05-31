# scripts/access-query.ps1
#
# Interactive review of recent portal access. Tails the Caddy and
# Authelia logs from inside their containers and pivots them into a
# structured table the operator can scan in 10 seconds.
#
# WHAT THIS IS GOOD FOR:
#   - "Anyone hit my portal in the last hour from outside my home IP?"
#   - "Show me every login failure in the last day"
#   - "What URLs has openwebui.devinveller.ai received this week?"
#   - "Has anyone reached /api/notebook/* recently?"
#   - "List the unique source IPs that have hit the portal"
#
# WHAT IT IS NOT:
#   - Real-time alerting (use the watcher / portal-alerter for that)
#   - Long-history forensics (logs roll at 100MB / 7 days by default)
#
# USAGE
#   .\scripts\access-query.ps1                                      # last 24h, everything
#   .\scripts\access-query.ps1 -Hours 1                             # last hour
#   .\scripts\access-query.ps1 -Subdomain openwebui                 # filter by host substring
#   .\scripts\access-query.ps1 -Status 401                          # only 401s
#   .\scripts\access-query.ps1 -Status 401 -Status 403              # 401s + 403s
#   .\scripts\access-query.ps1 -IP 2600:1700                        # IP substring match
#   .\scripts\access-query.ps1 -OnlyAuth                            # just Authelia events
#   .\scripts\access-query.ps1 -UniqueIPs                           # summary: distinct IPs hit
#   .\scripts\access-query.ps1 -Hours 168 -OnlyAuth -Subdomain auth  # 1 week of Authelia activity

[CmdletBinding()]
param(
  [int]$Hours = 24,
  [string]$Subdomain,
  [int[]]$Status,
  [string]$IP,
  [switch]$OnlyAuth,
  [switch]$OnlyCaddy,
  [switch]$UniqueIPs,
  [int]$Limit = 100
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location $projectRoot

$cutoffUnix = [DateTimeOffset]::UtcNow.AddHours(-$Hours).ToUnixTimeSeconds()
$cutoffIso  = [DateTimeOffset]::UtcNow.AddHours(-$Hours).ToString('yyyy-MM-ddTHH:mm:ssZ')

Write-Host "==> access-query  (last $Hours h, since $cutoffIso)" -ForegroundColor Cyan
if ($Subdomain) { Write-Host "    Subdomain filter : $Subdomain" }
if ($Status)    { Write-Host "    Status filter    : $($Status -join ', ')" }
if ($IP)        { Write-Host "    IP filter        : $IP" }
Write-Host ""

# --------------------- Helpers --------------------------------------------

function Parse-CaddyLines {
  param([string[]]$Lines)
  $rows = New-Object System.Collections.Generic.List[object]
  foreach ($line in $Lines) {
    if (-not $line) { continue }
    try {
      $j = $line | ConvertFrom-Json -ErrorAction Stop
    } catch { continue }
    if (-not $j.ts -or $j.ts -lt $cutoffUnix) { continue }
    $req = $j.request
    if (-not $req) { continue }
    # Real client IP: prefer Cf-Connecting-Ip header, then j.request.client_ip
    $cf = $req.headers.'Cf-Connecting-Ip'
    if ($cf -is [array]) { $cf = $cf[0] }
    $clientIp = if ($cf) { $cf } else { $req.client_ip }
    $host_   = $req.host
    $statusN = [int]($j.status)
    if ($Status -and ($Status -notcontains $statusN)) { continue }
    if ($Subdomain -and $host_ -notlike "*$Subdomain*") { continue }
    if ($IP -and $clientIp -notlike "*$IP*") { continue }
    $rows.Add([pscustomobject]@{
      Time   = [DateTimeOffset]::FromUnixTimeSeconds([int]$j.ts).ToString('HH:mm:ss')
      Date   = [DateTimeOffset]::FromUnixTimeSeconds([int]$j.ts).ToString('yyyy-MM-dd')
      Source = 'caddy'
      IP     = $clientIp
      Status = $statusN
      Method = $req.method
      Host   = $host_
      URI    = $req.uri
      User   = $j.user_id
    })
  }
  return $rows
}

function Parse-AutheliaLines {
  param([string[]]$Lines)
  $rows = New-Object System.Collections.Generic.List[object]
  foreach ($line in $Lines) {
    if (-not $line) { continue }
    try { $j = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
    if (-not $j.time) { continue }
    $eventTime = [DateTimeOffset]::Parse($j.time)
    if ($eventTime.ToUnixTimeSeconds() -lt $cutoffUnix) { continue }
    $msg = $j.msg
    # Filter
    if ($Subdomain -and ($msg -notlike "*$Subdomain*" -and ([string]$j.remote_ip) -notlike "*$Subdomain*")) { continue }
    if ($IP -and ([string]$j.remote_ip) -notlike "*$IP*") { continue }
    # Classify
    $sev = if     ($msg -like '*unsuccessful*') { 'AUTH-FAIL' }
           elseif ($msg -like '*successful*')   { 'AUTH-OK' }
           elseif ($msg -like '*banned*')       { 'BANNED' }
           elseif ($msg -like '*webauthn*')     { 'WEBAUTHN' }
           elseif ($msg -like '*totp*')         { 'TOTP' }
           elseif ($msg -like '*forbidden*')    { 'FORBIDDEN' }
           elseif ($msg -like '*not authorized*') { 'UNAUTH' }
           else                                  { 'INFO' }
    $rows.Add([pscustomobject]@{
      Time   = $eventTime.ToString('HH:mm:ss')
      Date   = $eventTime.ToString('yyyy-MM-dd')
      Source = 'authelia'
      IP     = $j.remote_ip
      Status = $sev
      Method = ''
      Host   = ''
      URI    = ''
      User   = $j.username
      Msg    = $msg
    })
  }
  return $rows
}

# --------------------- Pull logs ------------------------------------------

if (-not $OnlyCaddy) {
  Write-Host "Reading authelia.log ..." -ForegroundColor DarkGray
  $autheliaLines = & docker exec authelia-watcher sh -c "tail -n 5000 /logs/authelia/authelia.log 2>/dev/null" 2>$null
  $autheliaRows  = Parse-AutheliaLines -Lines $autheliaLines
} else { $autheliaRows = @() }

if (-not $OnlyAuth) {
  Write-Host "Reading caddy-access.log ..." -ForegroundColor DarkGray
  $caddyLines = & docker exec authelia-watcher sh -c "tail -n 5000 /logs/caddy/caddy-access.log 2>/dev/null" 2>$null
  $caddyRows  = Parse-CaddyLines -Lines $caddyLines
} else { $caddyRows = @() }

$all = @($autheliaRows) + @($caddyRows) | Sort-Object Date, Time -Descending

Write-Host ""
Write-Host "==> $($all.Count) matching event(s)" -ForegroundColor Cyan
Write-Host ""

if ($UniqueIPs) {
  $all | Group-Object IP | Sort-Object Count -Descending | ForEach-Object {
    "{0,-44}  {1,5}  events" -f $_.Name, $_.Count
  }
  Pop-Location
  return
}

$all | Select-Object -First $Limit |
  Format-Table -AutoSize @{N='Time';E={$_.Time}}, Source, IP, Status, Method, Host, @{N='URI';E={
    if ($_.URI) { $_.URI.Substring(0, [Math]::Min(60, $_.URI.Length)) } else { $_.Msg.Substring(0, [Math]::Min(80, $_.Msg.Length)) }
  }}, User

if ($all.Count -gt $Limit) {
  Write-Host "(showing first $Limit of $($all.Count); raise -Limit to see more)" -ForegroundColor DarkGray
}

Pop-Location
