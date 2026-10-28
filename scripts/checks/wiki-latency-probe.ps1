# wiki-latency-probe.ps1 - baseline/verification probe for the wiki viewer.
#
# The viewer (openbrain-wiki-viewer) renders every page live from the
# database (wiki_pages) the moment it listens (research-workbench P1a/P1b);
# there is no rebuild to wait for and no published snapshot index on the read
# path, so the old builder numbers (Quartz "Done rebuilding" wall time, the
# /srv/current static index sizes) say nothing about reader latency any more.
# What it measures now, against the published loopback port (default 8812):
#   1. page    - TTFB / bytes of the home page over -Samples GETs, with the
#                viewer's own server-timing (app;dur=<ms>)
#   2. etag    - a revalidating GET with If-None-Match: the page's ETag must
#                answer 304 (P1b page validators)
#   3. nav     - TTFB / bytes / entry total of /__nav?folder= (the explorer
#                root) and of the largest root folder (by the entries' count)
#   4. health  - /__health status (ok|degraded) and its reasons
#   5. note    - (-Note only) seconds from a note write to the viewer
#                presenting it from its database row (x-wiki-render: db)
#
# Read-only except for -Note, which writes ONE probe note into the vault and
# removes it afterwards (notes/ is author-owned; the orphan sweep does not
# touch it, so the probe cleans up after itself). The note is written straight
# into the vault (not through the workbench), so it is presented only once
# something writes its wiki_pages row; until then the viewer shows the
# pending-compile state and the reconciliation counts the file as missing
# drift (/__health degraded), and deleting it can leave an orphan row until the
# next repair pass. Run -Note only when that is acceptable.
#
# (research-workbench P1c: the viewer keeps no reader-activity clock any more -
# /__last-access and the compiler's WIKI_BACKFILL_IDLE_MIN gate are gone - so
# this probe defers nothing. Still do not run it in a tight loop.)
#
# Usage:
#   .\wiki-latency-probe.ps1              # fast checks (1-4)
#   .\wiki-latency-probe.ps1 -Note        # + note round-trip (slow: up to -TimeoutSec)
#   .\wiki-latency-probe.ps1 -Json        # machine-readable
#   .\wiki-latency-probe.ps1 -Samples 10  # more page samples

[CmdletBinding()]
param(
  [switch]$Note,
  [switch]$Json,
  [int]$Samples = 5,
  [int]$TimeoutSec = 600
)

$ErrorActionPreference = 'Stop'
$WIKI = 'openbrain-wiki'

function Invoke-Docker {
  param([string[]]$DockerArgs)
  # NEVER use 2>&1 on a native exe in PS 5.1: each stderr line becomes an
  # ErrorRecord (NativeCommandError) and trips $ErrorActionPreference='Stop'
  # even when docker exits 0.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $out = & docker @DockerArgs 2>$null | Out-String } finally { $ErrorActionPreference = $prev }
  return $out
}

# One HTTP GET with timings. HttpWebRequest (not Invoke-WebRequest) because PS
# 5.1's Invoke-WebRequest throws on 304/503 and cannot time the first byte.
#   ttfb_ms  request sent -> response headers received
#   total_ms request sent -> body fully read
function Get-Http {
  param([string]$Uri, [string]$IfNoneMatch, [int]$TimeoutMs = 30000)
  $req = [System.Net.HttpWebRequest]::Create($Uri)
  $req.Method = 'GET'
  $req.Timeout = $TimeoutMs
  $req.ReadWriteTimeout = $TimeoutMs
  $req.AllowAutoRedirect = $false
  if ($IfNoneMatch) { $req.Headers.Add('If-None-Match', $IfNoneMatch) }
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $resp = $null
  try { $resp = $req.GetResponse() }
  catch [System.Net.WebException] {
    if ($_.Exception.Response) { $resp = $_.Exception.Response } else { throw }
  }
  $ttfb = $sw.Elapsed.TotalMilliseconds
  try {
    $ms = New-Object System.IO.MemoryStream
    $stream = $resp.GetResponseStream()
    if ($stream) { $stream.CopyTo($ms); $stream.Dispose() }
    $total = $sw.Elapsed.TotalMilliseconds
    $bytes = $ms.ToArray()
    $appDur = $null
    $st = $resp.Headers['server-timing']
    if ($st -and ($st -match 'app;dur=([\d.]+)')) { $appDur = [double]$Matches[1] }
    return [pscustomobject]@{
      status   = [int]$resp.StatusCode
      ttfb_ms  = [math]::Round($ttfb, 1)
      total_ms = [math]::Round($total, 1)
      bytes    = $bytes.Length
      app_ms   = $appDur
      etag     = $resp.Headers['etag']
      render   = $resp.Headers['x-wiki-render']
      body     = [Text.Encoding]::UTF8.GetString($bytes)
    }
  } finally { $resp.Close() }
}

function Get-Stats {
  param($Values)
  $v = @($Values | Where-Object { $null -ne $_ })
  if ($v.Count -eq 0) { return $null }
  $m = $v | Measure-Object -Average -Minimum -Maximum
  return [ordered]@{ avg = [math]::Round($m.Average, 1); min = [math]::Round($m.Minimum, 1); max = [math]::Round($m.Maximum, 1) }
}

$result = [ordered]@{
  timestamp_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
$port = $env:WIKI_VIEWER_PORT; if (-not $port) { $port = '8812' }
$base = "http://127.0.0.1:$port"

# --- 1. page TTFB --------------------------------------------------------
$pageSamples = @()
try {
  for ($i = 0; $i -lt [math]::Max(1, $Samples); $i++) { $pageSamples += Get-Http -Uri "$base/" }
  $last = $pageSamples[-1]
  $result['page_status']  = $last.status
  $result['page_render']  = $last.render
  $result['page_bytes']   = $last.bytes
  $result['page_samples'] = $pageSamples.Count
  $s = Get-Stats ($pageSamples | ForEach-Object { $_.ttfb_ms }); if ($s) { $result['page_ttfb_ms'] = $s }
  $s = Get-Stats ($pageSamples | ForEach-Object { $_.app_ms });  if ($s) { $result['page_app_ms']  = $s }
} catch {
  $result['page_error'] = $_.Exception.Message
}

# --- 2. page ETag revalidation -------------------------------------------
# A presented page (x-wiki-render: db) carries etag W/"p-<16 hex>"; the same
# request with If-None-Match must answer 304. The other states (pending,
# database screen) are no-store and carry no validator.
if ($pageSamples.Count -gt 0) {
  $etag = $pageSamples[-1].etag
  $result['page_etag'] = $etag
  if ($etag) {
    try {
      $r = Get-Http -Uri "$base/" -IfNoneMatch $etag
      $result['etag_revalidate_status'] = $r.status
      $result['etag_revalidate_ms']     = $r.ttfb_ms
      $result['etag_revalidate_ok']     = ($r.status -eq 304)
    } catch { $result['etag_error'] = $_.Exception.Message }
  } else {
    $result['etag_revalidate_ok'] = $false
    $result['etag_error'] = "no etag on the page (x-wiki-render: $($pageSamples[-1].render))"
  }
}

# --- 3. nav ---------------------------------------------------------------
# /__nav answers ONE folder of the explorer (the browser reaches it as
# /workbench/nav). Root first, then the root folder with the most children.
function Measure-Nav {
  param([string]$Folder, [string]$Prefix)
  $r = Get-Http -Uri ("$base/__nav?folder=" + [uri]::EscapeDataString($Folder))
  $result["${Prefix}_status"]  = $r.status
  $result["${Prefix}_ttfb_ms"] = $r.ttfb_ms
  $result["${Prefix}_app_ms"]  = $r.app_ms
  $result["${Prefix}_bytes"]   = $r.bytes
  $doc = $null
  if ($r.status -eq 200) {
    $doc = $r.body | ConvertFrom-Json
    $result["${Prefix}_total"] = $doc.total
  }
  return $doc
}
try {
  $root = Measure-Nav -Folder '' -Prefix 'nav_root'
  if ($root) {
    $big = @($root.entries | Where-Object { $_.folder } | Sort-Object -Property count -Descending | Select-Object -First 1)
    if ($big.Count -gt 0) {
      $folder = $big[0].href.Trim('/')
      $result['nav_largest_folder'] = $folder
      $null = Measure-Nav -Folder $folder -Prefix 'nav_largest'
    }
  }
} catch {
  $result['nav_error'] = $_.Exception.Message
}

# --- 4. viewer health ----------------------------------------------------
# 200 + status ok, or 503 + status degraded with the reasons (database not up,
# reconciliation drift, broken page class, render errors).
try {
  $h = Get-Http -Uri "$base/__health"
  $result['health_http'] = $h.status
  $hb = $h.body | ConvertFrom-Json
  $result['health_status']  = $hb.status
  $result['health_reasons'] = (@($hb.reasons) -join '; ')
  if ($hb.db) { $result['health_db'] = $hb.db.state }
  if ($hb.reconcile -and ($null -ne $hb.reconcile.drift)) { $result['health_drift'] = $hb.reconcile.drift }
} catch {
  $result['health_error'] = $_.Exception.Message
}

# --- 5. note round-trip (opt-in) -----------------------------------------
if ($Note) {
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
  $slug  = "zz-latency-probe-$stamp"
  $rel   = "notes/$slug.md"
  $body  = "---`ntitle: latency probe $stamp`n---`n`n# latency probe $stamp`n`nprobe body`n"
  $writeCmd = "printf '%s' " + ("'" + $body.Replace("'", "'\''") + "'") + " > /wiki/$rel && echo WROTE"
  $null = Invoke-Docker @('exec', $WIKI, 'sh', '-c', $writeCmd)
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $served = $false
  $state = $null
  # Served = the viewer presents the page from its row (x-wiki-render: db);
  # pending = the file is known but no row is presentable yet.
  while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    try {
      $r = Get-Http -Uri "$base/notes/$slug"
      $state = $r.render
      if ($r.status -eq 200 -and $r.render -eq 'db') { $served = $true; break }
    } catch { $state = "error: $($_.Exception.Message)" }
    Start-Sleep -Seconds 5
  }
  $sw.Stop()
  $result['note_served']     = $served
  $result['note_last_state'] = $state
  $result['note_latency_s']  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
  $null = Invoke-Docker @('exec', $WIKI, 'sh', '-c', "rm -f /wiki/$rel")
}

if ($Json) {
  $result | ConvertTo-Json -Depth 4
} else {
  Write-Output '=== wiki latency probe ==='
  foreach ($k in $result.Keys) {
    $v = $result[$k]
    if ($v -is [System.Collections.IDictionary]) { $v = "avg $($v.avg) min $($v.min) max $($v.max)" }
    Write-Output ("{0,-24} {1}" -f $k, $v)
  }
  Write-Output '=========================='
  $verdict = @()
  if ($result['page_ttfb_ms']) { $verdict += "page ttfb avg $($result['page_ttfb_ms'].avg) ms" }
  if ($result.Contains('etag_revalidate_ok')) {
    if ($result['etag_revalidate_ok']) { $verdict += 'etag 304 ok' } else { $verdict += 'etag 304 NO' }
  }
  if ($result.Contains('nav_root_ttfb_ms')) { $verdict += "nav root $($result['nav_root_ttfb_ms']) ms" }
  if ($result.Contains('nav_largest_ttfb_ms')) { $verdict += "nav $($result['nav_largest_folder']) $($result['nav_largest_ttfb_ms']) ms" }
  if ($result.Contains('health_status')) { $verdict += "health $($result['health_status'])" }
  if ($result.Contains('note_latency_s')) {
    $n = "note $($result['note_latency_s'])s"
    if (-not $result['note_served']) { $n += " (TIMEOUT, last state $($result['note_last_state']))" }
    $verdict += $n
  }
  Write-Output ('VERDICT: ' + ($verdict -join ' | '))
}
