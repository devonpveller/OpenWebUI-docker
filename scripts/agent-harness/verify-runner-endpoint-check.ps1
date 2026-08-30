# verify-runner-endpoint-check.ps1 - the drill that proves check-runner-endpoints.ps1 can FAIL
#
# WHY THIS EXISTS
# check-runner-endpoints.ps1 shipped in a state where it could not fail for the rows it
# exists to validate. Every row in the registry is a container-DNS address, none of them
# claims `host`, and the script's only probe was a HOST probe - so a failing probe landed in
# the branch that set status "ok". A verifier pointed all three declared addresses at
# containers that do not exist, on ports nothing listens on, and got three [ok] rows and
# exit 0. The header claimed "Exit 0 = every declaration matched reality"; the sentence was
# false.
#
# A check whose failure path is never executed is not a check, and prose saying "it fails in
# both directions" is exactly the verification standard this repo has recorded as FALSIFIED
# (DFU PLAN section 0 A6). So the failure paths are executed HERE, mechanically, on every run:
# each case mutates a COPY of harness.config.json, points the check at it with
# AI_STACK_HARNESS_CONFIG, and asserts the exit code the mutation deserves.
#
# The load-bearing case is `wrong-port`. It is the one a reviewer should read first, because
# it is the acceptance test the original defect was measured against: change a declared port
# to one nothing listens on and the script MUST fail.
#
#   powershell -NoProfile -File scripts/agent-harness/verify-runner-endpoint-check.ps1
#
# Needs the stack up (little-coder + the ao-workers), like the script it drills.
# Exit 0 = every case produced the exit code it should. Exit 1 = the check is not falsifiable.

[CmdletBinding()]
param([switch]$Quiet)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$here = $PSScriptRoot
$target = Join-Path $here "check-runner-endpoints.ps1"
$baseline = Join-Path $here "harness.config.json"
$work = Join-Path ([IO.Path]::GetTempPath()) ("runner-check-drill-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $work -Force | Out-Null

$raw = Get-Content -Raw -Path $baseline

function New-Case {
    # A mutated registry on disk. Text substitution on purpose: it is the same edit an
    # operator fat-fingers into the real file, not a synthetic object the loader never sees.
    param([string]$Name, [string]$From, [string]$To)
    $p = Join-Path $work "$Name.json"
    $text = if ($From) { $raw.Replace($From, $To) } else { $raw }
    if ($From -and $text -eq $raw) { throw "drill case '$Name' did not change anything - the pattern '$From' is gone from harness.config.json, so this case is checking nothing" }
    Set-Content -Path $p -Value $text -Encoding ASCII
    return $p
}

function Invoke-Check {
    param([string]$ConfigPath)
    $prev = $env:AI_STACK_HARNESS_CONFIG
    $env:AI_STACK_HARNESS_CONFIG = $ConfigPath
    try {
        # A child powershell, not dot-sourcing: the check exits with a code, and config.ps1
        # caches its merged settings in a script-scoped variable that would survive between
        # cases in one process and make every case after the first read a stale registry.
        $out = & powershell -NoProfile -ExecutionPolicy Bypass -File $target 2>&1
        return @{ code = $LASTEXITCODE; out = ($out | Out-String) }
    }
    finally {
        if ($null -eq $prev) { Remove-Item Env:AI_STACK_HARNESS_CONFIG -ErrorAction SilentlyContinue }
        else { $env:AI_STACK_HARNESS_CONFIG = $prev }
    }
}

# name, expected exit, why, mutation
$cases = @(
    @{ name = "as-shipped"; expect = 0
       why  = "the registry as committed describes the running stack"
       from = ""; to = "" },

    @{ name = "wrong-port"; expect = 1
       why  = "THE acceptance test: a declared port nothing listens on must FAIL. This is the case the shipped check passed."
       from = "http://little-coder:8090"; to = "http://little-coder:9999" },

    @{ name = "nonexistent-container"; expect = 2
       why  = "an address naming a container that does not exist CANNOT be checked - that is exit 2 (unknown), and specifically not 0"
       from = "http://little-coder:8090"; to = "http://no-such-container:8090" },

    @{ name = "wrong-network"; expect = 1
       why  = "a network the container is not attached to is a false declaration"
       from = '"reachable_from": ["ai-stack_llm-net", "coder_lc-net"]'
       to   = '"reachable_from": ["ai-stack_llm-net", "search-net"]' },

    @{ name = "dead-host-claim"; expect = 1
       why  = "the ORIGINAL bug: reachable_from 'host' on an address the host cannot open"
       from = '"reachable_from": ["ai-stack_llm-net", "coder_lc-net"]'
       to   = '"reachable_from": ["host"]' },

    @{ name = "stale-host-claim"; expect = 1
       why  = "the other direction: an address that DOES answer on the host while claiming only container networks"
       from = '"endpoint": "http://little-coder:8090"'
       to   = "LISTENER" }
)

# The stale-declaration case needs an address that genuinely answers on the host, and this
# drill opens one itself rather than borrowing a service's published port. Two reasons: a
# borrowed port makes the drill fail when someone re-points that service, and the obvious
# candidate here does not work - coder/docker-compose.yml:121 publishes
# 127.0.0.1:9091 -> 9090 and `docker inspect` confirms the binding, yet a host TCP connect to
# 9091 is REFUSED on this machine while `docker exec little-coder curl localhost:9090/metrics`
# returns 200. See documentation/notes/u4bidir-findings.md - that is a live stack defect, not
# something a drill should encode as an assumption.
$listener = $null
$listenerPort = 0
try {
    $listener = New-Object System.Net.Sockets.TcpListener([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $listenerPort = $listener.LocalEndpoint.Port
}
catch { $listener = $null }

$fails = 0
foreach ($c in $cases) {
    $to = $c.to
    if ($to -eq "LISTENER") {
        if (-not $listener) {
            Write-Output "[SKIP] stale-host-claim         could not open a loopback listener - case not run"
            $fails++
            continue
        }
        $to = '"endpoint": "http://127.0.0.1:' + $listenerPort + '"'
    }
    $p = New-Case -Name $c.name -From $c.from -To $to
    $r = Invoke-Check -ConfigPath $p
    $ok = ($r.code -eq $c.expect)
    if (-not $ok) { $fails++ }
    $tag = if ($ok) { "PASS" } else { "FAIL" }
    Write-Output ("[{0}] {1,-22} expected exit {2}, got {3}" -f $tag, $c.name, $c.expect, $r.code)
    Write-Output ("        why: {0}" -f $c.why)
    if (-not $ok -or -not $Quiet) {
        foreach ($line in ($r.out -split "`r?`n" | Where-Object { $_ -match '\S' })) {
            Write-Output ("        | {0}" -f $line)
        }
    }
}

if ($listener) { $listener.Stop() }
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue

Write-Output ""
if ($fails -gt 0) {
    Write-Output "$fails of $($cases.Count) case(s) did not produce the expected exit code."
    Write-Output "check-runner-endpoints.ps1 is not falsifiable as claimed - fix the CHECK, not this drill."
    exit 1
}
Write-Output "$($cases.Count)/$($cases.Count) cases produced the expected exit code - the check fails when it should."
exit 0
