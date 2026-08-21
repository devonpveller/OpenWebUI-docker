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

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "restart")]
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
    @{ Name = "ob1";       Compose = "OB1\docker\docker-compose.yml";      Note = "Open Brain + Open Notebook trio"; Profiles = @("idea-refinery"); OwnEnv = $true }
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
}
