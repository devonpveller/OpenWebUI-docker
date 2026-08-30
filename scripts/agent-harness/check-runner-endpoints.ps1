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
#   * a row that claims a vantage point and cannot be opened from it is a dead address (the
#     bug above);
#   * a row that does NOT claim `host` and DOES answer on the host is a stale declaration -
#     someone published a port and nobody updated the file, which is how the first kind of
#     error gets written in the first place.
#
# WHY IT PROBES FROM INSIDE A CONTAINER
# The first version of this file probed ONLY from the host, and was therefore VACUOUS for
# exactly the rows it was written for. None of the shipped rows claims `host`, so for each
# of them the host probe FAILING was the branch that set status "ok" - the failing probe was
# the passing branch. A verifier pointed all three declared addresses at containers that do
# not exist, on ports nothing listens on, and got three [ok] rows and exit 0. The port was
# never validated on any container row.
# A container-DNS claim is now checked the only way it can be honestly checked: from a
# container that is actually on that network (`docker exec <probe> curl <url>`), which
# exercises BOTH the name and the port.
#
# WHY "CANNOT LOOK" IS ITS OWN EXIT CODE
# The same review found the second half of the vacuity: when `docker inspect` could not
# answer, the leg was recorded "skip" and skip never incremented the failure count - a
# silent pass. A check that quietly passes when it cannot look is the failure class this
# file exists to catch, so it is now a distinct outcome (exit 2) that a drill can assert on.
#
# NOT a pre-commit hook: it needs the stack running. Run it before wiring a dispatcher to a
# runner, and after any compose change that touches a daemon's ports or networks.
#
#   powershell -NoProfile -File scripts/agent-harness/check-runner-endpoints.ps1
#   ... -Json     machine-readable result for a drill
#
# EXIT CODES
#   0  every declaration was CHECKED and matched reality
#   1  at least one declaration is FALSE
#   2  at least one declaration could NOT be checked (docker absent, container not running,
#      no probe vantage on a declared network). Not a pass, and deliberately distinct from
#      one so a caller can tell "the stack is down" from "the config is wrong".
#
# The drill that proves all three outcomes are reachable - and that a WRONG PORT fails - is
# scripts/agent-harness/verify-runner-endpoint-check.ps1.

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
    # absent, container not running). $null means "cannot tell" and is reported as UNKNOWN
    # (exit 2) - never as a pass, because a check that quietly passes when it cannot look is
    # the exact failure class this file exists to catch.
    param([string]$Name)
    try {
        $out = & docker inspect -f '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' $Name 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        return @(($out -join " ").Trim() -split '\s+' | Where-Object { $_ })
    }
    catch { return $null }
}

$script:ProbeCache = @{}

function Get-NetworkProbe {
    # A container ON the given network that can make an outbound HTTP request - i.e. a
    # vantage point from which a container-DNS claim can actually be tested. Returns
    # @{ name; client } or $null when no such vantage exists (which is UNKNOWN, not ok).
    #
    # $Exclude is the address's own container: probing a daemon from itself still resolves
    # through the docker DNS, but a SECOND container is the honest test of "another workload
    # on this network can reach it", so the target is tried last, never first.
    param([string]$Network, [string]$Exclude = "")
    $key = "$Network|$Exclude"
    if ($script:ProbeCache.Contains($key)) { return $script:ProbeCache[$key] }
    $found = $null
    try {
        $raw = & docker network inspect $Network --format '{{range .Containers}}{{.Name}} {{end}}' 2>$null
        if ($LASTEXITCODE -eq 0 -and $raw) {
            $names = @(($raw -join " ").Trim() -split '\s+' | Where-Object { $_ })
            $ordered = @($names | Where-Object { $_ -ne $Exclude }) + @($names | Where-Object { $_ -eq $Exclude })
            $wgetOnly = $null
            foreach ($n in $ordered) {
                $client = & docker exec $n sh -c 'command -v curl 2>/dev/null || command -v wget 2>/dev/null' 2>$null
                if ($LASTEXITCODE -eq 0 -and $client) {
                    $c = ($client -join " ").Trim()
                    # curl is PREFERRED, not merely accepted. BusyBox wget exits 1 for an HTTP
                    # 404 and 1 for a DNS failure alike, so a wget vantage cannot tell "the
                    # port answered with an error" from "nothing is there" - and this script's
                    # whole question is which of those two it is. ao-git-egress is on
                    # agent-org_ao-worker-net and is wget-only; picking it turned a reachable
                    # ao-worker-2 into a FAIL. So scan every candidate for curl first and fall
                    # back to a wget vantage only if the network truly has no curl anywhere.
                    if ($c -match 'curl') { $found = @{ name = $n; client = "curl" }; break }
                    if (-not $wgetOnly) { $wgetOnly = @{ name = $n; client = "wget" } }
                }
            }
            if (-not $found) { $found = $wgetOnly }
        }
    }
    catch { $found = $null }
    $script:ProbeCache[$key] = $found
    return $found
}

function Test-ReachableFromContainer {
    # Open $Url from inside $Probe. Returns @{ ok; detail }.
    # Any COMPLETED HTTP exchange counts as reachable - 404 and 500 both prove the name
    # resolved and the port answered, which is the question this script asks. Only a
    # transport failure (refused / unresolvable / timed out) is a miss.
    param([hashtable]$Probe, [string]$Url, [int]$TimeoutSec)
    if ($Probe.client -eq "curl") {
        $cmd = "curl -s -o /dev/null -m $TimeoutSec -w '%{http_code}' '$Url' ; echo rc=`$?"
    }
    else {
        $cmd = "wget -q -T $TimeoutSec -O /dev/null '$Url' ; echo rc=`$?"
    }
    $out = ""
    # Native stderr must NOT be captured here. Under $ErrorActionPreference = "Stop", a
    # native command writing to stderr through `2>&1` is raised as a terminating error - and
    # wget writes "server returned error: HTTP/1.1 404" to stderr for a perfectly reachable
    # endpoint, so capturing it turned a PASS into an exception. The rc we need is on stdout.
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = (& docker exec $($Probe.name) sh -c $cmd 2>$null) -join "" }
    catch { return @{ ok = $false; detail = "docker exec into $($Probe.name) failed: $($_.Exception.Message)" } }
    finally { $ErrorActionPreference = $prev }
    $out = ($out -replace "`r", "").Trim()
    $rc = -1
    if ($out -match 'rc=(\d+)$') { $rc = [int]$Matches[1] }
    $code = ($out -split 'rc=')[0]
    # curl: 0 = a response arrived; 6 DNS, 7 refused, 28 timed out.
    # wget: 0 = 2xx; 8 = the server answered with an error status (still reachable);
    #       4 = network failure.
    $okCodes = if ($Probe.client -eq "curl") { @(0) } else { @(0, 8) }
    if ($okCodes -contains $rc) {
        return @{ ok = $true; detail = "opened from $($Probe.name) (http $code)" }
    }
    $why = switch ($rc) {
        6 { "could not resolve the host" }
        7 { "connection refused" }
        28 { "timed out / unresolvable" }
        4 { "network failure" }
        default { "client exit $rc" }
    }
    return @{ ok = $false; detail = "NOT openable from $($Probe.name): $why" }
}

$rows = @(Get-HarnessRunnerAddresses)
$results = @()
$failed = 0
$unknown = 0

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

    # ---- the container-network legs of the claim -------------------------------------
    # This is where the real content is for every row in the shipped registry: none of them
    # claims `host`, so the host leg above can only ever fail them in the STALE direction -
    # it cannot validate the port. A network claim is checked twice: attachment (docker
    # inspect) and an actual request from a container on that network. Either being
    # unanswerable is UNKNOWN, not ok.
    $netStatus = "ok"
    $netWhy = "no container-network claim to check"
    $nets = @($r.reachable_from | Where-Object { $_ -ne "host" })
    if ($nets.Count -gt 0) {
        $u = $null
        try { $u = [Uri]$r.url } catch { $u = $null }
        if (-not $u) {
            $netStatus = "FAIL"; $netWhy = "unparseable url - no host to check"
        }
        else {
            $attached = Get-ContainerNetworks -Name $u.Host
            if ($null -eq $attached) {
                $netStatus = "UNKNOWN"
                $netWhy = "docker cannot describe container '$($u.Host)' (absent, or not running) - the claim was NOT checked"
            }
            else {
                $missing = @($nets | Where-Object { $attached -notcontains $_ })
                if ($missing.Count -gt 0) {
                    $netStatus = "FAIL"
                    $netWhy = "declares $($missing -join ', ') but the container is on $($attached -join ', ')"
                }
                else {
                    # Attachment agrees; now open the ADDRESS - name AND port - from each
                    # declared network. This is the leg the first version lacked entirely,
                    # which is why a wrong port passed.
                    $details = @()
                    foreach ($n in $nets) {
                        $p = Get-NetworkProbe -Network $n -Exclude $u.Host
                        if ($null -eq $p) {
                            if ($netStatus -ne "FAIL") { $netStatus = "UNKNOWN" }
                            $details += "$n : no container on it can run curl/wget - NOT checked"
                            continue
                        }
                        $res = Test-ReachableFromContainer -Probe $p -Url $r.url -TimeoutSec $TimeoutSec
                        if ($res.ok) { $details += "$n : $($res.detail)" }
                        else { $netStatus = "FAIL"; $details += "$n : $($res.detail)" }
                    }
                    $netWhy = $details -join " ; "
                }
            }
        }
    }

    if ($status -eq "FAIL" -or $netStatus -eq "FAIL") { $failed++ }
    elseif ($netStatus -eq "UNKNOWN") { $unknown++ }

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
    # Shape kept stable for drills: the summary sits beside the rows, so a caller does not
    # have to re-derive the verdict the script already reached.
    [ordered]@{
        rows    = $results
        failed  = $failed
        unknown = $unknown
        verdict = $(if ($failed -gt 0) { "FAIL" } elseif ($unknown -gt 0) { "UNKNOWN" } else { "OK" })
    } | ConvertTo-Json -Depth 6
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
    if ($unknown -gt 0) {
        Write-Output ""
        Write-Output "$unknown declaration(s) could NOT be checked (stack down, or no probe vantage). This is exit 2, not a pass."
    }
}

exit $(if ($failed -gt 0) { 1 } elseif ($unknown -gt 0) { 2 } else { 0 })
