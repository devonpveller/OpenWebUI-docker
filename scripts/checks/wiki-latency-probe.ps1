# wiki-latency-probe.ps1 - baseline/verification probe for the wiki viewer pipeline.
#
# Measures the four numbers the wiki-dynamic-index plan moves
# (documentation/implementation-guide/wiki-dynamic-index/PLAN.md):
#   1. rebuild   - Quartz builder wall time per rebuild (viewer log)
#   2. indexes   - published static index sizes (contentIndex / graphIndex)
#   3. eager     - bytes a cold page load pulls before any panel is opened
#   4. note      - seconds from note write to its built page being served
#
# Read-only except for -Note, which writes ONE probe note into the vault and
# removes it afterwards (notes/ is author-owned; the orphan sweep does not
# touch it, so the probe cleans up after itself).
#
# Usage:
#   .\wiki-latency-probe.ps1              # fast checks (1-3)
#   .\wiki-latency-probe.ps1 -Note        # + note round-trip (slow: up to -TimeoutSec)
#   .\wiki-latency-probe.ps1 -Json        # machine-readable

[CmdletBinding()]
param(
  [switch]$Note,
  [switch]$Json,
  [int]$TimeoutSec = 600
)

$ErrorActionPreference = 'Stop'
$VIEWER = 'openbrain-wiki-viewer'
$WIKI   = 'openbrain-wiki'

function Invoke-Docker {
  param([string[]]$DockerArgs)
  # NEVER use 2>&1 on a native exe in PS 5.1: each stderr line becomes an
  # ErrorRecord (NativeCommandError) and trips $ErrorActionPreference='Stop'
  # even when docker exits 0. Container stderr (quartz LaTeX warnings) is
  # noise here; the lines we parse are stdout.
  $prev = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { $out = & docker @DockerArgs 2>$null | Out-String } finally { $ErrorActionPreference = $prev }
  return $out
}

$result = [ordered]@{
  timestamp_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}

# --- 1. rebuild wall time -----------------------------------------------
# Quartz logs "Done rebuilding in 59s" / "in 1m2s" per incremental rebuild.
$log = Invoke-Docker @('logs', '--tail', '4000', $VIEWER)
$rebuildSecs = @()
foreach ($m in [regex]::Matches($log, 'Done rebuilding in (?:(\d+)m)?(?:([\d.]+)s)?')) {
  $s = 0.0
  if ($m.Groups[1].Success) { $s += [double]$m.Groups[1].Value * 60 }
  if ($m.Groups[2].Success) { $s += [double]$m.Groups[2].Value }
  if ($s -gt 0) { $rebuildSecs += $s }
}
if ($rebuildSecs.Count -gt 0) {
  $recent = $rebuildSecs | Select-Object -Last 10
  $stats = $recent | Measure-Object -Average -Maximum -Minimum
  $result['rebuild_samples']  = $recent.Count
  $result['rebuild_avg_s']    = [math]::Round($stats.Average, 1)
  $result['rebuild_min_s']    = [math]::Round($stats.Minimum, 1)
  $result['rebuild_max_s']    = [math]::Round($stats.Maximum, 1)
} else {
  $result['rebuild_samples'] = 0
}

# --- 2. published index sizes -------------------------------------------
# /srv/current is the snapshot actually being served.
# NOTE: no embedded double quotes in the sh command - PS 5.1 mangles native
# arguments containing them (the arg arrives at sh as a syntax error).
$sizeCmd = 'for f in contentIndex graphIndex navIndex; do p=/srv/current/static/$f.json; ' +
           'if [ -f $p ]; then echo $f=$(wc -c < $p); else echo $f=absent; fi; done'
$sizes = Invoke-Docker @('exec', $VIEWER, 'sh', '-c', $sizeCmd)
foreach ($line in ($sizes -split "`n")) {
  if ($line -match '^(\w+)=(\d+|absent)\s*$') {
    $result["index_$($Matches[1])_bytes"] = $Matches[2]
  }
}

# --- 3. eager cold-page-load bytes --------------------------------------
# Measured from the HOST against the published loopback port, so it reflects
# what a real reader pulls: the page HTML plus every static index the page
# fetches eagerly (renderPage injects a top-level fetch until the plan makes
# it lazy). Panels (search/graph) are NOT opened, so their lazy fetches are
# correctly excluded.
# SIDE EFFECT: this counts as viewer activity, which defers the compiler's
# backfill drain by WIKI_BACKFILL_IDLE_MIN (15 min). Fine for a probe; do not
# run it in a tight loop.
$port = $env:WIKI_VIEWER_PORT; if (-not $port) { $port = '8812' }
$base = "http://127.0.0.1:$port"
try {
  $homePage = Invoke-WebRequest -Uri "$base/" -UseBasicParsing -TimeoutSec 30
  $htmlBytes = $homePage.RawContentLength
  if (-not $htmlBytes) { $htmlBytes = [Text.Encoding]::UTF8.GetByteCount($homePage.Content) }
  $idxBytes = 0
  $seen = @{}
  foreach ($m in [regex]::Matches($homePage.Content, '/static/[A-Za-z]+Index\.json')) {
    if ($seen.ContainsKey($m.Value)) { continue }
    $seen[$m.Value] = $true
    try {
      $r = Invoke-WebRequest -Uri ($base + $m.Value) -UseBasicParsing -TimeoutSec 120
      $n = $r.RawContentLength
      if (-not $n) { $n = $r.Content.Length }
      $idxBytes += $n
      $result["eager_asset$($m.Value.Replace('/static/','_').Replace('.json',''))_bytes"] = $n
    } catch { $result['eager_asset_error'] = $_.Exception.Message }
  }
  $result['eager_html_bytes']  = $htmlBytes
  $result['eager_index_bytes'] = $idxBytes
  $result['eager_total_bytes'] = $htmlBytes + $idxBytes
} catch {
  $result['eager_error'] = $_.Exception.Message
}

# --- 4. note round-trip (opt-in) ----------------------------------------
if ($Note) {
  $stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
  $slug  = "zz-latency-probe-$stamp"
  $rel   = "notes/$slug.md"
  $body  = "---`ntitle: latency probe $stamp`n---`n`n# latency probe $stamp`n`nprobe body`n"
  $writeCmd = "printf '%s' " + ("'" + $body.Replace("'", "'\''") + "'") + " > /wiki/$rel && echo WROTE"
  $null = Invoke-Docker @('exec', $WIKI, 'sh', '-c', $writeCmd)
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  $served = $false
  # Built page is served from the published snapshot - that is the moment the
  # user actually gets the real (editable) page rather than the interim view.
  $checkCmd = "test -f /srv/current/notes/$slug.html -o -f /srv/current/$slug.html"
  while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    $prevEA = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & docker exec $VIEWER sh -c $checkCmd 2>$null | Out-Null
    $ok = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $prevEA
    if ($ok) { $served = $true; break }
    Start-Sleep -Seconds 5
  }
  $sw.Stop()
  $result['note_served']     = $served
  $result['note_latency_s']  = [math]::Round($sw.Elapsed.TotalSeconds, 1)
  $null = Invoke-Docker @('exec', $WIKI, 'sh', '-c', "rm -f /wiki/$rel")
}

if ($Json) {
  $result | ConvertTo-Json -Depth 3
} else {
  Write-Output '=== wiki latency probe ==='
  foreach ($k in $result.Keys) { Write-Output ("{0,-24} {1}" -f $k, $result[$k]) }
  Write-Output '=========================='
  $verdict = @()
  if ($result['rebuild_avg_s']) { $verdict += "rebuild avg $($result['rebuild_avg_s'])s" }
  if ($result['eager_total_bytes']) {
    $verdict += ("eager load {0:N1} MB" -f ($result['eager_total_bytes'] / 1MB))
  }
  if ($result.Contains('note_latency_s')) {
    $verdict += "note $($result['note_latency_s'])s$(if(-not $result['note_served']){' (TIMEOUT)'})"
  }
  Write-Output ('VERDICT: ' + ($verdict -join ' | '))
}
