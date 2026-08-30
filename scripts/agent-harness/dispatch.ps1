# dispatch.ps1 - the RUNNER DISPATCH layer: given a role + profile, actually run the work.
#
# Why this exists (2026-08-30, dark-factory-unification U4). config.ps1's Resolve-RoleTarget
# answers "which runner and model should this role use". Nothing consumed that answer.
# Verified before writing this: `grep -rn 8090 scripts/agent-harness/` returned nothing, and
# the only files naming `little-coder` were harness.config.json, lease-names.conf, MODULE.md
# and two tests. So PLAN.md's A11 ("the little-coder runner is wired, status: unproven") was
# understated - the RESOLUTION existed and the DISPATCH did not. This file is the dispatch.
#
# Transport decision (class-2, logged in DECISIONS.md as U4-1). harness.config.json used to
# declare endpoint "http://127.0.0.1:8090". little-coder publishes ONLY 127.0.0.1:9091->9090
# (Prometheus); its task API is reachable only from the container networks, so the declared
# door did not exist. Of the two honest fixes - publish the port, or dispatch through the
# container - this takes the second, because the daemon says of itself: "It is NOT the
# task-trigger authentication surface; that is lc-mcpo" (little-coder/src/littlecoder/
# daemon.py lines 1-7), and lc-mcpo was retired 2026-08-20. Publishing an UNAUTHENTICATED
# POST /tasks (arbitrary agent execution) plus POST /admin/shutdown on the host loopback
# would widen the blast radius for no capability this layer needs. `docker exec` reaches the
# same API across a boundary that already requires Docker access.
#   Revert path: set runners.little-coder.transport to "http", give it base_url
#   "http://127.0.0.1:8090", and publish "127.0.0.1:8090:8090" in coder/docker-compose.yml.
#   Invoke-LcApi honours transport=http already.
#
# Usage (as a script):
#   .\dispatch.ps1 -Role worker -Profile all-local -Prompt "..." -AcceptanceCommand "..."
#   .\dispatch.ps1 -Role worker -Profile all-local -Prompt "..." -Repo https://host/o/r#branch
#   .\dispatch.ps1 -Probe -Profile all-local        # transport reachability only
#
# Usage (as a library): . .\dispatch.ps1 -NoRun   then call Invoke-HarnessTask.
#
# Exit codes (a caller pipeline reads these, not the prose):
#   0  the task completed AND its acceptance command passed
#   3  the task completed and its acceptance command FAILED
#   4  the task completed with no checkable signal (unverified), or was abandoned
#   1  dispatch itself failed (no runner, unreachable transport, no focus, timeout)
#   2  the harness module is switched off
#
# 0 vs 3/4 is the whole point: "the agent answered" is not "the work is right".
# PLAN.md section C.7 - only an executable check closes anything.

[CmdletBinding()]
param(
    [ValidateSet("worker", "tester", "reviewer")][string]$Role = "worker",
    [string]$Profile = "",
    [string]$Surface = "",
    [string]$Prompt = "",
    [string]$AcceptanceCommand = "",
    [string]$Repo = "",                 # focus the runner's workspace on this repo first
    [switch]$Fresh,                     # force a clean re-clone when focusing
    [string]$Channel = "batch",
    [string]$UserId = "harness",
    [string]$SessionId = "",
    [int]$TimeoutMinutes = 30,
    [int]$PollSeconds = 10,
    [string]$AuditDir = "",
    [switch]$Probe,                     # reachability only; changes nothing
    [switch]$NoRun,                     # dot-source as a library
    [switch]$Json
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

# ---------------------------------------------------------------------------
# Runner record
# ---------------------------------------------------------------------------

function Get-RunnerPath {
    param($Runner, [string]$Key, [string]$Fallback)
    if ($Runner -and $Runner.Contains($Key) -and $Runner[$Key]) { return $Runner[$Key] }
    return $Fallback
}

function Resolve-RunnerRecord {
    # role + profile -> the resolved target (Resolve-RoleTarget) PLUS the runner's own
    # transport record. Resolve-RoleTarget deliberately returns only the policy answer;
    # dispatch needs the topology too, and asking config.ps1 for it keeps that knowledge
    # in the config file rather than in this script.
    param([string]$Role, [string]$Profile = "", [string]$Surface = "")
    $t = Resolve-RoleTarget -Role $Role -Profile $Profile -Surface $Surface
    $t["record"] = Get-HarnessRunner -Name $t.runner
    return $t
}

# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------

function Invoke-LcApi {
    # ONE call to the little-coder control daemon. Returns the parsed JSON body.
    # Throws on a transport failure or a non-2xx status, with the body in the message.
    #
    # The drill replaces this whole function with a fake - which is why every other
    # function in this file goes through it and none of them shells out directly.
    param(
        [Parameter(Mandatory = $true)]$Runner,
        [ValidateSet("GET", "POST")][string]$Method = "GET",
        [Parameter(Mandatory = $true)][string]$Path,
        $Body = $null,
        [int]$TimeoutSeconds = 60
    )
    $transport = Get-RunnerPath $Runner "transport" "docker-exec"
    $baseUrl = Get-RunnerPath $Runner "base_url" "http://localhost:8090"
    $url = $baseUrl.TrimEnd("/") + $Path

    if ($transport -eq "http") {
        # The revert path. Kept live so flipping the config is the whole change.
        $req = @{ Uri = $url; Method = $Method; TimeoutSec = $TimeoutSeconds; UseBasicParsing = $true }
        if ($null -ne $Body) {
            $req["Body"] = ($Body | ConvertTo-Json -Depth 8 -Compress)
            $req["ContentType"] = "application/json"
        }
        $resp = Invoke-WebRequest @req
        if (-not $resp.Content) { return $null }
        return ($resp.Content | ConvertFrom-Json)
    }

    if ($transport -ne "docker-exec") {
        throw "runner transport '$transport' is not supported by dispatch.ps1 (known: docker-exec, http)"
    }
    $container = Get-RunnerPath $Runner "container" ""
    if (-not $container) { throw "runner declares transport 'docker-exec' but no 'container'" }

    # curl's argv is passed straight to exec (no shell), so nothing here is shell-quoted.
    # The BODY still goes through a file: PowerShell 5.1 mangles embedded double quotes when
    # it hands a native process an argument, and a JSON body is nothing but double quotes.
    $tmpName = ""
    if ($null -ne $Body) {
        $tmpName = "/tmp/lc-dispatch-" + [guid]::NewGuid().ToString("N") + ".json"
        $json = ($Body | ConvertTo-Json -Depth 8 -Compress)
        $hostTmp = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName() + ".json")
        [IO.File]::WriteAllText($hostTmp, $json, (New-Object Text.UTF8Encoding($false)))
        try {
            & docker.exe cp $hostTmp ("{0}:{1}" -f $container, $tmpName) | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "docker cp into '$container' failed (exit $LASTEXITCODE)" }
        } finally {
            Remove-Item -Path $hostTmp -Force -ErrorAction SilentlyContinue
        }
    }
    $bodyFile = "/tmp/lc-dispatch-body-" + [guid]::NewGuid().ToString("N")
    $curl = @("exec", $container, "curl", "-sS", "--max-time", "$TimeoutSeconds",
              "-o", $bodyFile, "-w", "%{http_code}", "-X", $Method)
    if ($null -ne $Body) {
        $curl += @("-H", "Content-Type: application/json", "--data-binary", "@$tmpName")
    }
    $curl += @($url)

    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $code = (& docker.exe @curl) | Select-Object -Last 1
        $exec = $LASTEXITCODE
        $body = (& docker.exe exec $container sh -c "cat $bodyFile 2>/dev/null; rm -f $bodyFile") -join "`n"
        if ($tmpName) { & docker.exe exec $container rm -f $tmpName | Out-Null }
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($exec -ne 0) {
        throw "docker exec $container curl $Method $url failed (exit $exec): $body"
    }
    if ("$code" -notmatch '^2\d\d$') {
        throw "little-coder $Method $Path returned HTTP $code : $body"
    }
    if (-not $body) { return $null }
    return ($body | ConvertFrom-Json)
}

# ---------------------------------------------------------------------------
# little-coder operations
# ---------------------------------------------------------------------------

function Get-LcHealth {
    param([Parameter(Mandatory = $true)]$Runner)
    return (Invoke-LcApi -Runner $Runner -Method GET -Path (Get-RunnerPath $Runner "health_path" "/health"))
}

function Set-LcFocus {
    # POST /project - point the runner's workspace at a repo. The daemon WIPES the workspace
    # on a real switch, so this is a live-plane mutation: callers take the coder lease.
    param(
        [Parameter(Mandatory = $true)]$Runner,
        [Parameter(Mandatory = $true)][string]$Repo,
        [switch]$Fresh,
        [string]$Actor = "harness"
    )
    $body = [ordered]@{ repo = $Repo; actor = $Actor }
    if ($Fresh) { $body["fresh"] = $true }
    return (Invoke-LcApi -Runner $Runner -Method POST -Path (Get-RunnerPath $Runner "project_path" "/project") -Body $body -TimeoutSeconds 900)
}

function Submit-LcTask {
    param(
        [Parameter(Mandatory = $true)]$Runner,
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$AcceptanceCommand = "",
        [string]$Channel = "batch",
        [string]$UserId = "harness",
        [string]$SessionId = ""
    )
    if (-not $Prompt.Trim()) { throw "dispatch needs a non-empty prompt" }
    $body = [ordered]@{ prompt = $Prompt; channel = $Channel; user_id = $UserId }
    if ($AcceptanceCommand) { $body["acceptance_command"] = $AcceptanceCommand }
    if ($SessionId) { $body["session_id"] = $SessionId }
    return (Invoke-LcApi -Runner $Runner -Method POST -Path (Get-RunnerPath $Runner "submit_path" "/tasks") -Body $body)
}

function Get-LcTask {
    param([Parameter(Mandatory = $true)]$Runner, [Parameter(Mandatory = $true)][string]$TaskId)
    $p = (Get-RunnerPath $Runner "task_path" "/tasks/{id}").Replace("{id}", $TaskId)
    return (Invoke-LcApi -Runner $Runner -Method GET -Path $p)
}

function Get-LcTaskEvents {
    param([Parameter(Mandatory = $true)]$Runner, [Parameter(Mandatory = $true)][string]$TaskId, [int]$Offset = 0)
    $p = (Get-RunnerPath $Runner "events_path" "/tasks/{id}/events").Replace("{id}", $TaskId)
    return (Invoke-LcApi -Runner $Runner -Method GET -Path ($p + "?offset=$Offset"))
}

function Wait-LcTask {
    # Follow one task to a terminal state, draining its event stream as it goes.
    # Returns @{ status; task; events; timed_out; elapsed_seconds }.
    param(
        [Parameter(Mandatory = $true)]$Runner,
        [Parameter(Mandatory = $true)][string]$TaskId,
        [int]$TimeoutMinutes = 30,
        [int]$PollSeconds = 10,
        [scriptblock]$OnProgress = $null
    )
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $started = Get-Date
    $offset = 0
    $events = New-Object System.Collections.ArrayList
    $terminal = @("done", "abandoned", "rejected")
    $status = "queued"
    $task = $null
    while ($true) {
        $ev = Get-LcTaskEvents -Runner $Runner -TaskId $TaskId -Offset $offset
        if ($ev) {
            if ($ev.events) { foreach ($line in $ev.events) { [void]$events.Add($line) } }
            if ($null -ne $ev.next_offset) { $offset = [int]$ev.next_offset }
            $status = "$($ev.status)"
        }
        $task = Get-LcTask -Runner $Runner -TaskId $TaskId
        if ($task) { $status = "$($task.status)" }
        if ($OnProgress) { & $OnProgress $status $events.Count }
        if ($terminal -contains $status) { break }
        if ((Get-Date) -gt $deadline) {
            return [ordered]@{
                status = $status; task = $task; events = @($events.ToArray()); timed_out = $true
                elapsed_seconds = [int]((Get-Date) - $started).TotalSeconds
            }
        }
        Start-Sleep -Seconds $PollSeconds
    }
    # One last drain: the final events are written as the task closes.
    $ev = Get-LcTaskEvents -Runner $Runner -TaskId $TaskId -Offset $offset
    if ($ev -and $ev.events) { foreach ($line in $ev.events) { [void]$events.Add($line) } }
    return [ordered]@{
        status = $status; task = $task; events = @($events.ToArray()); timed_out = $false
        elapsed_seconds = [int]((Get-Date) - $started).TotalSeconds
    }
}

# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------

function ConvertTo-DispatchResult {
    # The ONE shape a pipeline reads, whichever runner produced it.
    param($Target, $Wait, [string]$Repo = "", [string]$AuditPath = "")
    $task = $Wait.task
    $outcome = if ($task -and $task.outcome) { "$($task.outcome)" } else { "" }
    $status = "$($Wait.status)"
    if ($Wait.timed_out) { $status = "timeout" }
    # `ok` is about DISPATCH, never about the work. A failed acceptance command is a
    # successful dispatch reporting a real failure - conflating them is how a pipeline
    # learns to call red green.
    $ok = (-not $Wait.timed_out) -and ($status -eq "done")
    return [ordered]@{
        ok              = $ok
        role            = $Target.role
        profile         = $Target.profile
        runner          = $Target.runner
        kind            = $Target.kind
        model           = $Target.model
        repo            = $(if ($Repo) { $Repo } elseif ($task) { "$($task.repo)" } else { "" })
        task_id         = $(if ($task) { "$($task.task_id)" } else { "" })
        status          = $status
        outcome         = $outcome
        signal          = $(if ($task) { "$($task.signal)" } else { "" })
        detail          = $(if ($task) { "$($task.detail)" } else { "" })
        commands        = $(if ($task -and $null -ne $task.commands) { [int]$task.commands } else { 0 })
        answer          = $(if ($task) { "$($task.answer)" } else { "" })
        event_count     = @($Wait.events).Count
        elapsed_seconds = $Wait.elapsed_seconds
        audit_path      = $AuditPath
    }
}

function Get-DispatchExitCode {
    param($Result)
    if (-not $Result.ok) { return 1 }
    switch ("$($Result.outcome)") {
        "pass" { return 0 }
        "fail" { return 3 }
        default { return 4 }
    }
}

function Write-DispatchAudit {
    # PLAN.md section C.7: "the audit trail is the deliverable's twin". A dispatched task
    # that left no record is indistinguishable from one that never ran.
    param($Result, $Wait, [string]$Dir, [string]$Prompt, [string]$AcceptanceCommand)
    if (-not $Dir) { return "" }
    if (-not (Test-Path $Dir)) { New-Item -ItemType Directory -Path $Dir -Force | Out-Null }
    $stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
    $id = if ($Result.task_id) { $Result.task_id } else { "no-task" }
    $file = Join-Path $Dir ("{0}-{1}.json" -f $stamp, $id)
    $record = [ordered]@{
        dispatched_utc     = (Get-Date).ToUniversalTime().ToString("o")
        result             = $Result
        prompt             = $Prompt
        acceptance_command = $AcceptanceCommand
        events             = @($Wait.events)
    }
    [IO.File]::WriteAllText($file, ($record | ConvertTo-Json -Depth 10), (New-Object Text.UTF8Encoding($false)))
    return $file
}

function Invoke-HarnessTask {
    # Resolve role+profile -> runner, submit the work, follow it, return an outcome the
    # pipeline can act on. The claude-code runner is NOT dispatchable from here and says so
    # rather than pretending: a cloud agent is started by the session or the Mattermost
    # bridge, not by a PowerShell script.
    param(
        [ValidateSet("worker", "tester", "reviewer")][string]$Role = "worker",
        [string]$Profile = "",
        [string]$Surface = "",
        [Parameter(Mandatory = $true)][string]$Prompt,
        [string]$AcceptanceCommand = "",
        [string]$Repo = "",
        [switch]$Fresh,
        [string]$Channel = "batch",
        [string]$UserId = "harness",
        [string]$SessionId = "",
        [int]$TimeoutMinutes = 30,
        [int]$PollSeconds = 10,
        [string]$AuditDir = "",
        [scriptblock]$OnProgress = $null
    )
    $target = Resolve-RunnerRecord -Role $Role -Profile $Profile -Surface $Surface
    if ($target.kind -ne "little-coder") {
        throw ("role '{0}' resolves to runner '{1}' (kind '{2}'), which dispatch.ps1 cannot start: " +
               "a '{2}' agent is started by the session or the bridge, not by this script. " +
               "Pick a profile whose {0} is a little-coder runner, or dispatch it on that surface.") `
               -f $Role, $target.runner, $target.kind
    }
    $runner = $target.record

    if ($Repo) {
        $focus = Set-LcFocus -Runner $runner -Repo $Repo -Fresh:$Fresh
        if ($OnProgress) { & $OnProgress ("focus:" + $focus.action) 0 }
    }
    $health = Get-LcHealth -Runner $runner
    if (-not $health.focus) {
        throw "little-coder has no focused project - pass -Repo <url> so dispatch focuses it first (the daemon rejects a task with HTTP 409 otherwise)"
    }
    if ("$($health.status)" -eq "draining") {
        throw "little-coder is draining (shutting down) - not accepting triggers"
    }

    $submit = Submit-LcTask -Runner $runner -Prompt $Prompt -AcceptanceCommand $AcceptanceCommand `
                            -Channel $Channel -UserId $UserId -SessionId $SessionId
    $taskId = "$($submit.task_id)"
    if (-not $taskId) { throw "little-coder accepted the trigger but returned no task_id" }
    if ($OnProgress) { & $OnProgress "submitted:$taskId" 0 }

    $wait = Wait-LcTask -Runner $runner -TaskId $taskId -TimeoutMinutes $TimeoutMinutes `
                        -PollSeconds $PollSeconds -OnProgress $OnProgress
    $result = ConvertTo-DispatchResult -Target $target -Wait $wait -Repo $Repo
    $result["audit_path"] = (Write-DispatchAudit -Result $result -Wait $wait -Dir $AuditDir `
                                                 -Prompt $Prompt -AcceptanceCommand $AcceptanceCommand)
    return $result
}

function Test-RunnerReachable {
    # Reachability ONLY - proves the transport in the config is the transport that works.
    # Returns @{ ok; runner; transport; detail }.
    param([string]$Role = "worker", [string]$Profile = "", [string]$Surface = "")
    $target = Resolve-RunnerRecord -Role $Role -Profile $Profile -Surface $Surface
    $out = [ordered]@{
        ok = $false; role = $Role; runner = $target.runner; kind = $target.kind
        transport = ""; detail = ""; focus = ""; version = ""
    }
    if ($target.kind -ne "little-coder") {
        $out["detail"] = "runner kind '$($target.kind)' has no probeable transport"
        return $out
    }
    $runner = $target.record
    $out["transport"] = (Get-RunnerPath $runner "transport" "docker-exec")
    try {
        $h = Get-LcHealth -Runner $runner
        $out["ok"] = ("$($h.status)" -eq "ok")
        $out["version"] = "$($h.version)"
        $out["focus"] = "$($h.focus)"
        $out["detail"] = "status=$($h.status) queue_depth=$($h.queue_depth)"
    } catch {
        $out["detail"] = $_.Exception.Message
    }
    return $out
}

# ---------------------------------------------------------------------------
# Script mode
# ---------------------------------------------------------------------------

if ($NoRun) { return }

$offReason = Get-HarnessDisabledReason -Surface $Surface
if ($offReason) { Write-Host "REFUSED: $offReason" -ForegroundColor Yellow; exit 2 }

if ($Probe) {
    $p = Test-RunnerReachable -Role $Role -Profile $Profile -Surface $Surface
    if ($Json) {
        $p | ConvertTo-Json -Depth 6
    } else {
        Write-Host ("{0} runner '{1}' via {2}: {3}" -f `
            $(if ($p.ok) { "REACHABLE" } else { "UNREACHABLE" }), $p.runner, $p.transport, $p.detail) `
            -ForegroundColor $(if ($p.ok) { "Green" } else { "Red" })
        if ($p.focus) { Write-Host ("  focus  : {0}" -f $p.focus) }
    }
    exit $(if ($p.ok) { 0 } else { 1 })
}

if (-not $Prompt) {
    Write-Host "ERROR: -Prompt is required (or pass -Probe)." -ForegroundColor Red
    exit 1
}
if (-not $AuditDir) {
    $AuditDir = Join-Path (Get-SharedStateDir) "dispatch"
}

$res = $null
try {
    $progress = {
        param($state, $count)
        Write-Host ("  [{0}] {1} ({2} events)" -f (Get-Date -Format "HH:mm:ss"), $state, $count)
    }
    $res = Invoke-HarnessTask -Role $Role -Profile $Profile -Surface $Surface -Prompt $Prompt `
        -AcceptanceCommand $AcceptanceCommand -Repo $Repo -Fresh:$Fresh -Channel $Channel `
        -UserId $UserId -SessionId $SessionId -TimeoutMinutes $TimeoutMinutes `
        -PollSeconds $PollSeconds -AuditDir $AuditDir -OnProgress $progress
} catch {
    Write-Host ("DISPATCH FAILED: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 1
}

if ($Json) {
    $res | ConvertTo-Json -Depth 8
} else {
    Write-Host ""
    Write-Host ("runner   : {0}/{1} ({2})" -f $res.runner, $res.model, $res.profile)
    Write-Host ("task     : {0}" -f $res.task_id)
    Write-Host ("status   : {0}" -f $res.status)
    Write-Host ("outcome  : {0} {1}" -f $res.outcome, $res.signal)
    Write-Host ("commands : {0}   events: {1}   elapsed: {2}s" -f $res.commands, $res.event_count, $res.elapsed_seconds)
    if ($res.audit_path) { Write-Host ("audit    : {0}" -f $res.audit_path) }
}
exit (Get-DispatchExitCode $res)
