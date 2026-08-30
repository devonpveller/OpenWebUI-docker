# verify-dispatch.ps1 - executable drill over dispatch.ps1 (the runner dispatch layer).
#
#   .\scripts\agent-harness\verify-dispatch.ps1          # offline, ~5s, no containers touched
#   .\scripts\agent-harness\verify-dispatch.ps1 -Live    # + probe the real little-coder daemon
#
# Written for dark-factory-unification U4. PLAN.md section C.7: a phase closes only on an
# EXECUTABLE check, so the dispatch layer ships with one that fails red. The offline part
# replaces Invoke-LcApi with a scripted fake - dispatch.ps1 funnels EVERY daemon call through
# that one function precisely so this is possible without a container.
#
# What it asserts, and why each one is here rather than assumed:
#   1  the little-coder runner resolves to a transport record, not just a name
#   2  a docker-exec runner names a container (the failure the whole U4 item started from:
#      a config that named a door - 127.0.0.1:8090 - which was never published)
#   3  a completed task with a PASSING acceptance command -> outcome pass, exit 0
#   4  a completed task with a FAILING acceptance command -> ok STILL true, outcome fail,
#      exit 3. Dispatch succeeding and the work being right are different facts; a layer
#      that conflates them teaches the pipeline to call red green
#   5  no acceptance command -> unverified, exit 4 (never a silent pass)
#   6  a task that never terminates -> ok false, status timeout, exit 1
#   7  an unfocused daemon is refused BEFORE submitting, with the reason
#   8  a claude-code role is refused with a reason, not silently attempted
#   9  the event stream is drained across polls, including the final drain after terminal
#  10  an audit record is written and carries the events (section C.7's "twin")

[CmdletBinding()]
param([switch]$Live)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "dispatch.ps1") -NoRun

$script:results = @()
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = $ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function Step($n, $text) { Write-Host "`n=== $n. $text ===" -ForegroundColor Cyan }

# --- the fake daemon --------------------------------------------------------------------
# A scripted little-coder. `Script` maps "METHOD path-prefix" to a queue of responses; each
# call shifts one off (the last is repeated), so a poll loop can be driven through states.
$script:Fake = $null
function New-FakeDaemon {
    param([hashtable]$Script, [string]$Focus = "https://github.com/o/r")
    return [pscustomobject]@{ Script = $Script; Focus = $Focus; Calls = New-Object System.Collections.ArrayList }
}
function Invoke-LcApi {
    # Replaces dispatch.ps1's real transport for the offline drill.
    param($Runner, [string]$Method = "GET", [string]$Path, $Body = $null, [int]$TimeoutSeconds = 60)
    [void]$script:Fake.Calls.Add("$Method $Path")
    $bare = $Path.Split("?")[0]
    if ($Method -eq "GET" -and $bare -eq "/health") {
        return [pscustomobject]@{ status = "ok"; version = "fake"; focus = $script:Fake.Focus; queue_depth = 0 }
    }
    foreach ($key in $script:Fake.Script.Keys) {
        $parts = $key.Split(" ", 2)
        if ($parts[0] -ne $Method) { continue }
        if ($bare -like $parts[1]) {
            $q = $script:Fake.Script[$key]
            if ($q.Count -gt 1) { $r = $q[0]; $script:Fake.Script[$key] = $q[1..($q.Count - 1)]; return $r }
            return $q[0]
        }
    }
    throw "fake daemon has no scripted response for $Method $bare"
}
function New-TaskView {
    param([string]$Status, [string]$Outcome = "", [string]$Signal = "", [int]$Commands = 0)
    return [pscustomobject]@{
        task_id = "T1"; status = $Status; outcome = $Outcome; signal = $Signal
        detail = "$Commands command(s)"; commands = $Commands; answer = "fake answer"
        repo = "https://github.com/o/r"
    }
}
function New-EventsView {
    param([string]$Status, [string[]]$Events, [int]$NextOffset)
    return [pscustomobject]@{ status = $Status; events = $Events; next_offset = $NextOffset
                              done = @("done", "abandoned", "rejected") -contains $Status }
}

$auditRoot = Join-Path ([IO.Path]::GetTempPath()) ("dispatch-drill-" + [guid]::NewGuid().ToString("N"))

# --- 1/2: the config declares a transport, not just a name -------------------------------
Step 1 "the runner record carries a transport a dispatcher can use"
$target = Resolve-RunnerRecord -Role worker -Profile all-local -Surface mattermost
Check "all-local worker resolves to the little-coder runner" ($target.runner -eq "little-coder") $target.runner
$rec = $target.record
$transport = Get-RunnerPath $rec "transport" ""
Check "the runner declares a transport" ([bool]$transport) $transport
Check "the transport is one dispatch.ps1 implements" (@("docker-exec", "http") -contains $transport) $transport
if ($transport -eq "docker-exec") {
    Check "a docker-exec runner names its container" ([bool](Get-RunnerPath $rec "container" "")) (Get-RunnerPath $rec "container" "(none)")
    $bu = Get-RunnerPath $rec "base_url" ""
    Check "its base_url is container-local" ($bu -match "^https?://(localhost|127\.0\.0\.1)") $bu
}
Check "the runner declares the paths dispatch calls" `
    ((Get-RunnerPath $rec "submit_path" "") -and (Get-RunnerPath $rec "task_path" "") -and (Get-RunnerPath $rec "events_path" "")) `
    ("{0} {1} {2}" -f (Get-RunnerPath $rec "submit_path" "-"), (Get-RunnerPath $rec "task_path" "-"), (Get-RunnerPath $rec "events_path" "-"))

# --- 3: a passing acceptance command --------------------------------------------------
Step 2 "a completed task whose acceptance command passed"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"           = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events"  = @((New-EventsView "running" @("e1", "e2") 2), (New-EventsView "done" @("e3") 3), (New-EventsView "done" @() 3))
    "GET /tasks/T1"         = @((New-TaskView "running"), (New-TaskView "done" "pass" "acceptance command exit 0" 4))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "do the thing" `
        -AcceptanceCommand "pytest -q" -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot
Check "dispatch reports ok" ($res.ok -eq $true) ""
Check "outcome is pass" ($res.outcome -eq "pass") $res.outcome
Check "the signal is carried through" ($res.signal -like "acceptance command exit 0*") $res.signal
Check "exit code 0" ((Get-DispatchExitCode $res) -eq 0) ("{0}" -f (Get-DispatchExitCode $res))
Check "events drained across polls AND after terminal" ($res.event_count -eq 3) ("{0}" -f $res.event_count)
Check "the runner and model are reported" (($res.runner -eq "little-coder") -and $res.model) ("{0}/{1}" -f $res.runner, $res.model)

# --- 10: the audit record --------------------------------------------------------------
Step 3 "the audit record is the deliverable's twin"
Check "an audit file was written" ([bool]$res.audit_path -and (Test-Path $res.audit_path)) "$($res.audit_path)"
if ($res.audit_path -and (Test-Path $res.audit_path)) {
    $rec2 = Get-Content -Raw -Path $res.audit_path | ConvertFrom-Json
    Check "it carries the prompt" ($rec2.prompt -eq "do the thing") ""
    Check "it carries the acceptance command" ($rec2.acceptance_command -eq "pytest -q") ""
    Check "it carries every event" (@($rec2.events).Count -eq 3) ("{0}" -f @($rec2.events).Count)
    Check "it carries the outcome" ($rec2.result.outcome -eq "pass") ""
}

# --- 4: a failing acceptance command ----------------------------------------------------
Step 4 "a completed task whose acceptance command FAILED"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @("e1") 1))
    "GET /tasks/T1"        = @((New-TaskView "done" "fail" "acceptance command exit 1" 6))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -AcceptanceCommand "pytest -q" -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot
Check "dispatch still reports ok (the DISPATCH worked)" ($res.ok -eq $true) ""
Check "outcome is fail" ($res.outcome -eq "fail") $res.outcome
Check "exit code 3, not 0" ((Get-DispatchExitCode $res) -eq 3) ("{0}" -f (Get-DispatchExitCode $res))

# --- 5: no acceptance command -----------------------------------------------------------
Step 5 "no acceptance command is UNVERIFIED, never a silent pass"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @() 0))
    "GET /tasks/T1"        = @((New-TaskView "done" "unverified" "" 2))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot
Check "outcome is unverified" ($res.outcome -eq "unverified") $res.outcome
Check "exit code 4, not 0" ((Get-DispatchExitCode $res) -eq 4) ("{0}" -f (Get-DispatchExitCode $res))

# --- 6: a task that never terminates ----------------------------------------------------
Step 6 "a task that never terminates times out instead of hanging or lying"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "running" @("e1") 1))
    "GET /tasks/T1"        = @((New-TaskView "running"))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 0 -PollSeconds 0 -AuditDir $auditRoot
Check "ok is false" ($res.ok -eq $false) ""
Check "status is timeout" ($res.status -eq "timeout") $res.status
Check "exit code 1 (dispatch failure)" ((Get-DispatchExitCode $res) -eq 1) ("{0}" -f (Get-DispatchExitCode $res))
Check "the timed-out attempt still left an audit record" ([bool]$res.audit_path -and (Test-Path $res.audit_path)) ""

# --- 7: an unfocused daemon -------------------------------------------------------------
Step 7 "an unfocused daemon is refused BEFORE a task is submitted"
$script:Fake = New-FakeDaemon -Focus "" -Script @{
    "POST /tasks" = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
}
$threw = ""
try {
    Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot | Out-Null
} catch { $threw = $_.Exception.Message }
Check "it refused" ([bool]$threw) $threw
Check "the reason names the fix (-Repo)" ($threw -like "*-Repo*") ""
Check "nothing was submitted" (-not (@($script:Fake.Calls) -contains "POST /tasks")) (@($script:Fake.Calls) -join ", ")

# --- 8: a cloud role -------------------------------------------------------------------
Step 8 "a claude-code role is refused with a reason, not silently attempted"
$threw = ""
try {
    Invoke-HarnessTask -Role reviewer -Profile local-work-cloud-review -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot | Out-Null
} catch { $threw = $_.Exception.Message }
Check "it refused" ([bool]$threw) ""
Check "the reason names the runner kind" ($threw -like "*claude-code*") $threw

# --- live probe ------------------------------------------------------------------------
if ($Live) {
    Step 9 "LIVE: the declared transport actually reaches the daemon"
    Remove-Item Function:\Invoke-LcApi -ErrorAction SilentlyContinue   # back to the real one
    . (Join-Path $PSScriptRoot "dispatch.ps1") -NoRun
    $p = Test-RunnerReachable -Role worker -Profile all-local -Surface mattermost
    Check "little-coder is reachable over $($p.transport)" ($p.ok -eq $true) $p.detail
    Check "it reports a version" ([bool]$p.version) $p.version
}

Remove-Item -Recurse -Force $auditRoot -ErrorAction SilentlyContinue

$failed = @($script:results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0}/{1} checks passed" -f (@($script:results).Count - $failed.Count), @($script:results).Count) `
    -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
if ($failed.Count) {
    $failed | ForEach-Object { Write-Host ("  FAILED: {0} {1}" -f $_.check, $_.detail) -ForegroundColor Red }
    exit 1
}
exit 0
