# stack-stats.ps1 - inference demand + queue statistics (K.9, 2026-08-22).
# Invoked as `stack.ps1 stats` (or directly). READ-ONLY: the llm-queue
# /observe/* namespace (the P2 live board) + the LiteLLM spend ledger in
# llm-gateway-db, keyed per caller since J.1. This is the terminal face of
# the "available statistics in the ai-stack" effort.

[CmdletBinding()]
param(
    [int]$BucketMinutes = 10,
    [int]$Hours = 1
)

$ErrorActionPreference = 'Continue'
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

function Psql([string]$Sql) {
    # SQL goes via stdin: PS 5.1 native-arg quoting mangles the double quotes
    # PostgreSQL needs around LiteLLM's CamelCase identifiers.
    $Sql | & docker exec -i llm-gateway-db psql -U litellm -d litellm -tA -F '|' 2>$null
}

# ─── 1. Queue live board ────────────────────────────────────────────────
Write-Host "== llm-queue live board" -ForegroundColor Cyan
$raw = docker exec llm-queue python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8080/observe/queue',timeout=5).read().decode())" 2>$null
if (-not $raw) {
    Write-Host "  (llm-queue unreachable)" -ForegroundColor Yellow
}
else {
    $q = $raw | ConvertFrom-Json
    foreach ($prop in $q.models.PSObject.Properties) {
        $m = $prop.Value
        Write-Host ("  {0}: running={1} waiting={2} permits_free={3} avg_T={4}s" -f `
            $prop.Name, $m.running.Count, $m.waiting.Count, $m.permits_free, $m.avg_T_s)
        foreach ($r in $m.running) {
            Write-Host ("    RUNNING  {0}  key={1}  model={2}  {3:n0}s elapsed" -f $r.id, $r.key, $r.model, $r.elapsed_s) -ForegroundColor Green
        }
        $i = 0
        foreach ($w in $m.waiting) {
            $i++
            $tag = if ($i -eq 1) { "NEXT    " } else { "waiting " }
            Write-Host ("    $tag {0}  key={1}" -f $w.id, $w.key) -ForegroundColor Yellow
            if ($i -ge 5) { Write-Host ("    ... +{0} more" -f ($m.waiting.Count - 5)); break }
        }
    }
    Write-Host ("  connections held: {0}/{1}" -f $q.held_total, $q.max_total_connections)
}

# ─── 2. Demand, last N hours in increments ──────────────────────────────
Write-Host ""
Write-Host "== demand: last $Hours h in $BucketMinutes-min buckets (requests | tokens | failures)" -ForegroundColor Cyan
$rows = Psql @"
select to_char(date_trunc('hour', "startTime") + (floor(extract(minute from "startTime")/$BucketMinutes)*$BucketMinutes) * interval '1 minute', 'HH24:MI') as bucket,
       count(*), coalesce(sum(total_tokens),0),
       count(*) filter (where status='failure')
from "LiteLLM_SpendLogs"
where "startTime" > now() - interval '$Hours hours'
group by 1 order by 1
"@
if (-not $rows) { Write-Host "  (no requests in the window)" }
foreach ($r in $rows) {
    $f = $r -split '\|'
    $bar = '#' * [Math]::Min(60, [int]([int]$f[1] / 2 + 0.5))
    Write-Host ("  {0}  {1,5} req  {2,10:n0} tok  {3,3} fail  {4}" -f $f[0], $f[1], [long]$f[2], $f[3], $bar)
}

# ─── 3. By caller, last N hours ─────────────────────────────────────────
Write-Host ""
Write-Host "== by caller: last $Hours h" -ForegroundColor Cyan
$rows = Psql @"
select coalesce(t.key_alias, s.api_key) as caller, count(*),
       coalesce(sum(s.total_tokens),0),
       count(*) filter (where s.status='failure')
from "LiteLLM_SpendLogs" s
left join "LiteLLM_VerificationToken" t on s.api_key = t.token
where s."startTime" > now() - interval '$Hours hours'
group by 1 order by 2 desc limit 12
"@
if (-not $rows) { Write-Host "  (idle)" }
foreach ($r in $rows) {
    $f = $r -split '\|'
    $color = if ([int]$f[3] -gt 0) { 'Yellow' } else { 'White' }
    Write-Host ("  {0,-22} {1,6} req  {2,12:n0} tok  {3,4} fail" -f $f[0], $f[1], [long]$f[2], $f[3]) -ForegroundColor $color
}

# ─── 4. Global totals ───────────────────────────────────────────────────
Write-Host ""
Write-Host "== global totals (ledger since 2026-06-12)" -ForegroundColor Cyan
$tot = (Psql 'select count(*), coalesce(sum(total_tokens),0), count(*) filter (where status=''failure''), min("startTime")::date from "LiteLLM_SpendLogs"') -split '\|'
Write-Host ("  {0:n0} requests | {1:n0} tokens | {2:n0} failures | since {3}" -f [long]$tot[0], [long]$tot[1], [long]$tot[2], $tot[3])
$rows = Psql @"
select coalesce(t.key_alias, s.api_key), count(*), coalesce(sum(s.total_tokens),0)
from "LiteLLM_SpendLogs" s
left join "LiteLLM_VerificationToken" t on s.api_key = t.token
group by 1 order by 3 desc limit 8
"@
foreach ($r in $rows) {
    $f = $r -split '\|'
    Write-Host ("  {0,-22} {1,8:n0} req  {2,14:n0} tok" -f $f[0], [long]$f[1], [long]$f[2])
}
Write-Host ""
Write-Host "(failures with caller 'not-needed'/'no-key' = something calling without its virtual key - see J1-VIRTUAL-KEYS-CUTOVER.md)" -ForegroundColor DarkGray
