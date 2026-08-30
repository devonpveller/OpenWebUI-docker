# check-runner-endpoints.ps1 - does the runner registry tell the truth about reachability?
#
# WHY THIS EXISTS
# harness.config.json declared the little-coder runner at http://127.0.0.1:8090 from the day
# the runner block was written. The coder plane publishes 127.0.0.1:9091 (Prometheus metrics)
# and nothing else, so that address was refused by the host every single time it was tried -
# which was never, because nothing dispatched. A runner nobody calls is a runner nobody
# corrects, and the config was the only thing anyone would have read before wiring one up.
#
# So `reachable_from` is a CLAIM, and this script is the thing that can falsify it. It fails
# in BOTH directions on purpose:
#   * a row that claims `host` and does not answer on the host is a dead address (the bug
#     above);
#   * a row that does NOT claim `host` and DOES answer on the host is a stale declaration -
#     someone published a port and nobody updated the file, which is how the first kind of
#     error gets written in the first place.
#
# NOT a pre-commit hook: it needs the stack running. Run it before wiring a dispatcher to a
# runner, and after any compose change that touches a daemon's ports or networks.
#
#   powershell -NoProfile -File scripts/agent-harness/check-runner-endpoints.ps1
#   ... -Json     machine-readable result for a drill
#
# Exit 0 = every declaration matched reality. Exit 1 = at least one did not.

[CmdletBinding()]
param(
    [switch]$Json,
    [int]$TimeoutSec = 4
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "config.ps1")

function Test-HostReachable {
    # TCP only. A /health GET would conflate "the daemon is unhealthy" with "the address does
    # not exist", and this script is asking the second question. Anything that completes a
    # TCP handshake IS reachable from here, healthy or not.
    param([string]$Url, [int]$TimeoutSec)
    try { $u = [Uri]$Url } catch { return @{ ok = $false; detail = "unparseable url" } }
    $port = if ($u.Port -gt 0) { $u.Port } else { 80 }
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync($u.Host, $port)
        # Wait() RETHROWS a faulted task as an AggregateException whose own message is the
        # useless "One or more errors occurred." - so wait on the handle instead and read the
        # fault ourselves. An operator reading this output needs "connection refused", not a
        # wrapper class name.
        if (-not $task.AsyncWaitHandle.WaitOne($TimeoutSec * 1000)) {
            return @{ ok = $false; detail = "timed out after ${TimeoutSec}s" }
        }
        if ($task.IsFaulted) {
            return @{ ok = $false; detail = $task.Exception.GetBaseException().Message }
        }
        return @{ ok = $true; detail = "tcp connect ok" }
    }
    catch { return @{ ok = $false; detail = $_.Exception.GetBaseException().Message } }
    finally { $client.Dispose() }
}

function Get-ContainerNetworks {
    # The networks a container is attached to, or $null when docker cannot answer (docker
    # absent, container not running). $null means "cannot tell" and is reported as SKIP -
    # never as a pass, because a check that quietly passes when it cannot look is the exact
    # failure class this file exists to catch.
    param([string]$Name)
    try {
        $out = & docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' $Name 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return @(($out -join " ").Trim() -split '\s+' | Where-Object { $_ })
    }
    catch { return $null }
}

$rows = @(Get-HarnessRunnerAddresses)
$results = @()
$failed = 0

foreach ($r in $rows) {
    $claimsHost = @($r.reachable_from) -contains "host"
    $probe = Test-HostReachable -Url $r.url -TimeoutSec $TimeoutSec
    $status = "ok"
    $why = ""

    if ($claimsHost -and -not $probe.ok) {
        $status = "FAIL"
        $why = "declared reachable_from 'host' but the host cannot open it ($($probe.detail))"
    }
    elseif (-not $claimsHost -and $probe.ok) {
        $status = "FAIL"
        $why = "answers on the host but does not declare reachable_from 'host' - the declaration is stale"
    }
    elseif ($claimsHost) { $why = "reachable from the host, as declared" }
    else { $why = "not reachable from the host, as declared" }

    # The container-network legs of the claim. Only checkable with docker; SKIP otherwise.
    $netStatus = "skip"
    $netWhy = "docker unavailable or container not running - cannot verify"
    $nets = @($r.reachable_from | Where-Object { $_ -ne "host" })
    if ($nets.Count -gt 0) {
        $u = $null
        try { $u = [Uri]$r.url } catch { $u = $null }
        $attached = if ($u) { Get-ContainerNetworks -Name $u.Host } else { $null }
        if ($null -ne $attached) {
            $missing = @($nets | Where-Object { $attached -notcontains $_ })
            if ($missing.Count -gt 0) {
                $netStatus = "FAIL"
                $netWhy = "declares $($missing -join ', ') but the container is on $($attached -join ', ')"
            }
            else {
                $netStatus = "ok"
                $netWhy = "attached to $($nets -join ', '), as declared"
            }
        }
    }
    else { $netStatus = "ok"; $netWhy = "no container-network claim to check" }

    if ($status -eq "FAIL" -or $netStatus -eq "FAIL") { $failed++ }

    $results += [ordered]@{
        runner         = $r.runner
        label          = $r.label
        url            = $r.url
        pooled         = $r.pooled
        reachable_from = @($r.reachable_from)
        host_check     = $status
        host_detail    = $why
        net_check      = $netStatus
        net_detail     = $netWhy
    }
}

if ($Json) {
    $results | ConvertTo-Json -Depth 6
}
else {
    Write-Output "runner registry reachability - $($results.Count) declared address(es)"
    foreach ($x in $results) {
        Write-Output ("  [{0}] {1}/{2}  {3}" -f $x.host_check, $x.runner, $x.label, $x.url)
        Write-Output ("       host: {0}" -f $x.host_detail)
        Write-Output ("       nets[{0}]: {1}" -f $x.net_check, $x.net_detail)
    }
    if ($failed -gt 0) {
        Write-Output ""
        Write-Output "$failed declaration(s) do not match reality - fix harness.config.json or the compose file, not this script."
    }
}

exit $(if ($failed -gt 0) { 1 } else { 0 })
