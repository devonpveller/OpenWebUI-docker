# stack.ps1 - one driver for the multi-project ai-stack workspace (Part K.6).
#
# Since the 2026-08-21 Part K restructure the workspace is a set of
# self-contained compose projects around a root NETWORK ANCHOR (the root
# docker-compose.yml owns only the shared ai-stack_* networks). This script
# is the one place that knows the dependency order and always passes the
# single root .env (every plane file fails loud without it).
#
#   .\scripts\stack\stack.ps1 up               # everything, dependency order
#   .\scripts\stack\stack.ps1 up inference     # one plane
#   .\scripts\stack\stack.ps1 down             # everything, reverse order
#   .\scripts\stack\stack.ps1 down coder       # one plane
#   .\scripts\stack\stack.ps1 status           # per-project container states
#   .\scripts\stack\stack.ps1 restart memory   # one plane, in place
#
# NOT managed here (deliberate): the internet portal (portal-on.ps1 /
# portal-off.ps1 - exposing the stack stays a human action) and the
# agent-org workers/cloud profiles (operator-driven). Crash recovery is
# scripts/recovery/emergency-recovery.ps1, which layers health gates and
# GPU repair on top of the same order.

#   .\scripts\stack\stack.ps1 health          # functional probes across every plane
#
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "restart", "health", "stats")]
    [string]$Action = "status",

    [Parameter(Position = 1)]
    [string]$Plane = "all"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))

# Ordered registry - up runs top to bottom, down runs bottom to top.
# Anchor first: creates the shared networks every project attaches to.
# Inference before the callers; OB1 after the planes it consumes
# (llm-net aliases, app-net, the search vpn proxy); agent-org last.
$Projects = @(
    @{ Name = "anchor";    Compose = "docker-compose.yml";                 Note = "shared ai-stack_* networks only (0 services)" }
    @{ Name = "inference"; Compose = "inference\docker-compose.yml";       Note = "llama.cpp upstreams -> llm-queue -> LiteLLM gateway" }
    @{ Name = "frontend";  Compose = "frontend\docker-compose.yml";        Note = "openwebui + tailscale netns pair" }
    @{ Name = "memory";    Compose = "memory\docker-compose.yml";          Note = "mnemory + cloud gateway" }
    @{ Name = "search";    Compose = "search\docker-compose.yml";          Note = "Mullvad vpn + searxng + gateway" }
    @{ Name = "coder";     Compose = "coder\docker-compose.yml";           Note = "open-terminal + little-coder + lc-egress" }
    # OB1 gained research / wiki / notebook profiles on 2026-09-19
    # (sl-ob1-profiles). This script passes ALL FOUR deliberately: it is the
    # pre-manifest driver and its job is to start what is running on this host
    # today - 30 containers. Dropping the three here would silently stop the
    # wiki, the research engine and Open Notebook from coming up, which is a
    # deployment change, and this item deploys nothing. stack.py expresses the
    # same set through the `research` product instead (only idea-refinery is
    # `default` in stack.manifest.toml, so --headless can drop the surfaces);
    # sl-driver-parity reconciles the two drivers.
    @{ Name = "ob1";       Compose = "OB1\docker\docker-compose.yml";      Note = "Open Brain + Open Notebook trio"; Profiles = @("idea-refinery", "research", "wiki", "notebook"); OwnEnv = $true }
    @{ Name = "agent-org"; Compose = "agent-org\docker\docker-compose.yml"; Note = "Mattermost + agent-bridge (default plane)"; OwnEnv = $true }
)

function Invoke-Project {
    param([hashtable]$P, [string[]]$ComposeArgs)
    $cmd = @("compose", "-f", $P.Compose)
    # Plane projects interpolate from the single root .env; OB1 and agent-org
    # carry their own env files next to their compose files.
    if (-not $P.OwnEnv) { $cmd += @("--env-file", ".env") }
    if ($P.Profiles) { foreach ($pr in $P.Profiles) { $cmd += @("--profile", $pr) } }
    & docker @cmd @ComposeArgs
}

function Resolve-Planes {
    param([string]$Name)
    if ($Name -eq "all") { return $Projects }
    $hit = $Projects | Where-Object { $_.Name -eq $Name.ToLower() }
    if (-not $hit) {
        Write-Host "Unknown plane '$Name'. Valid: $(($Projects | ForEach-Object { $_.Name }) -join ', '), all" -ForegroundColor Red
        exit 1
    }
    return @($hit)
}

$targets = Resolve-Planes $Plane

switch ($Action) {
    "up" {
        foreach ($p in $targets) {
            Write-Host "== up: $($p.Name) - $($p.Note)" -ForegroundColor Cyan
            Invoke-Project $p @("up", "-d")
        }
    }
    "down" {
        # Reverse dependency order; the anchor's networks go last (and only
        # drop if no external endpoints remain).
        [array]::Reverse($targets)
        foreach ($p in $targets) {
            Write-Host "== down: $($p.Name)" -ForegroundColor Yellow
            Invoke-Project $p @("down")
        }
    }
    "restart" {
        if ($Plane -eq "all") {
            Write-Host "Refusing 'restart all' - use down + up, or emergency-recovery.ps1 for an ordered restart with health gates." -ForegroundColor Red
            exit 1
        }
        foreach ($p in $targets) {
            Write-Host "== restart: $($p.Name)" -ForegroundColor Cyan
            Invoke-Project $p @("restart")
        }
    }
    "status" {
        foreach ($p in $targets) {
            Write-Host "== $($p.Name) ($($p.Note))" -ForegroundColor Cyan
            Invoke-Project $p @("ps", "--format", "table {{.Service}}\t{{.Status}}")
            Write-Host ""
        }
    }
    "stats" {
        # Inference demand + queue statistics (read-only; see stack-stats.ps1).
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'stack-stats.ps1')
        exit $LASTEXITCODE
    }
    "health" {
        # One-command smoke test: the same functional gates the Part K
        # cutovers used. Each probe is independent; failures don't stop the
        # sweep. Exit code = number of failed probes.
        $failed = 0
        function Probe {
            param([string]$Name, [scriptblock]$Check)
            try {
                $ok = & $Check
                if ($ok) { Write-Host ("  [OK]   " + $Name) -ForegroundColor Green; return }
            } catch {}
            Write-Host ("  [FAIL] " + $Name) -ForegroundColor Red
            $script:failed++
        }
        Write-Host "== container health (all projects)" -ForegroundColor Cyan
        $unhealthy = @(docker ps --filter health=unhealthy --format "{{.Names}}")
        Probe "0 unhealthy containers (found: $($unhealthy -join ', '))" { $unhealthy.Count -eq 0 }

        Write-Host "== functional gates" -ForegroundColor Cyan
        Probe "anchor: ai-stack_llm-net exists" {
            (docker network inspect ai-stack_llm-net --format "{{.Name}}" 2>$null) -eq "ai-stack_llm-net" }
        Probe "inference: llm-gateway liveliness" {
            docker exec llm-gateway python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health/liveliness', timeout=8).status==200 else 1)" 2>$null
            $LASTEXITCODE -eq 0 }
        Probe "frontend: OWUI http://127.0.0.1:3000/health" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:3000/health).StatusCode -eq 200 }
        Probe "frontend: 8 tailnet serve routes" {
            $r = docker exec tailscale sh -c "tailscale --socket=/tmp/tailscaled.sock serve status 2>/dev/null | grep -c 'proxy http'" 2>$null
            [int]$r -ge 8 }
        # owui/ plugins deploy BY PASTE: nothing links the repo file to the live
        # webui.db row, so a committed fix can sit unpasted for weeks (the
        # deep_research banner, 2026-09-04..06). Count only - the names are in
        # scripts\checks\check-owui-drift.ps1's own output. REFUSED reads as FAIL.
        #
        # The 'Continue' dance is not optional. The check REFUSES (exit 2, sentence
        # on stderr) rather than reporting a clean bill, and PowerShell 5.1 turns a
        # native command's stderr into a TERMINATING NativeCommandError under this
        # script's 'Stop' preference - `2>$null` does not prevent it. The first
        # version of this probe assigned outside any Probe scriptblock and so DIED
        # here whenever openwebui was down: 5 of 14 probe lines, no summary, and the
        # eight later probes (memory, search, coder, OB1 x4, agent-org) never ran.
        # A stopped openwebui must cost one FAILED line, not the rest of the sweep.
        $owuiEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $owuiOut = @()
        try {
            $owuiOut = @(& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot '..\checks\check-owui-drift.ps1') -CountOnly 2>&1)
        } catch { }
        $ErrorActionPreference = $owuiEap
        $owuiVals = @($owuiOut | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] })
        $owuiErrs = @($owuiOut | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] })
        $owuiDrift = 'REFUSED'
        if ($owuiVals.Count -gt 0) { $owuiDrift = "$($owuiVals[$owuiVals.Count - 1])".Trim() }
        if ($owuiDrift -notmatch '^\d+$') {
            $owuiWhy = 'the check produced no answer'
            if ($owuiErrs.Count -gt 0) { $owuiWhy = ("$($owuiErrs[0])" -replace '^REFUSED:\s*', '').Trim() }
            $owuiDrift = "REFUSED - $owuiWhy"
        }
        Probe "frontend: owui/ manifest rows drifted from live webui.db: $owuiDrift" { $owuiDrift -eq '0' }
        Probe "memory: cloud door http://127.0.0.1:8060/health" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8060/health).StatusCode -eq 200 }
        Probe "search: gateway http://127.0.0.1:8085/healthz" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8085/healthz).StatusCode -eq 200 }
        # /healthz said 200 through the whole 2026-09-11 outage: bing answered
        # every query with ten results for its first word, HTTP 200, no error.
        # /health reports which engines actually put results in recent payloads.
        # 'unknown' (nothing searched since the gateway started) is NOT a failure
        # here - only a measured DEGRADED is, and this probe prints the number.
        $searchEngines = 'REFUSED'
        try {
            $sh = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8085/health).Content | ConvertFrom-Json
            $n = 0
            foreach ($p in $sh.providers.PSObject.Properties) {
                if ($p.Value.engines_answering_now -gt $n) { $n = [int]$p.Value.engines_answering_now }
            }
            $searchEngines = "$($sh.search) - $n engine(s) answering"
        } catch { }
        Probe "search: $searchEngines" {
            $searchEngines -ne 'REFUSED' -and $searchEngines -notmatch '^DEGRADED' }
        Probe "coder: little-coder daemon :8090/health" {
            docker exec little-coder curl -fsS --max-time 8 http://localhost:8090/health 2>$null | Out-Null
            $LASTEXITCODE -eq 0 }
        Probe "OB1: open_notebook API :5055/api/config" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:5055/api/config).StatusCode -eq 200 }
        Probe "OB1: ops door :8062/health" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8062/health).StatusCode -eq 200 }
        # The curator answers 503 with {"ok":false,"db":false} when its DB is gone and
        # nothing at all while crash-looping (2026-09-05: "Module not found pool.ts",
        # noticed 14 h late from a disk check). Either reads as FAIL here.
        Probe "OB1: research-curator http://127.0.0.1:8816/health" {
            (Invoke-WebRequest -UseBasicParsing -TimeoutSec 8 http://127.0.0.1:8816/health).StatusCode -eq 200 }
        Probe "OB1: openbrain-db accepting connections" {
            docker exec openbrain-db pg_isready -U postgres -d openbrain -t 5 2>$null | Out-Null
            $LASTEXITCODE -eq 0 }
        Probe "agent-org: mattermost ping" {
            docker exec agent-bridge python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://mattermost:8065/api/v4/system/ping', timeout=8).status==200 else 1)" 2>$null
            $LASTEXITCODE -eq 0 }

        Write-Host ""
        if ($failed -eq 0) { Write-Host "ALL HEALTH PROBES PASSED" -ForegroundColor Green }
        else { Write-Host "$failed probe(s) FAILED" -ForegroundColor Red }
        exit $failed
    }
}
