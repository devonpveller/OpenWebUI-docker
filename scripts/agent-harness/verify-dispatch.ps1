# verify-dispatch.ps1 - executable drill over dispatch.ps1 (the runner dispatch layer).
#
#   .\scripts\agent-harness\verify-dispatch.ps1            # everything, incl. the REAL daemon
#   .\scripts\agent-harness\verify-dispatch.ps1 -Offline   # skip the real daemon, and SAY SO
#
# Written for dark-factory-unification U4. PLAN.md section C.7: a phase closes only on an
# EXECUTABLE check, so the dispatch layer ships with one that fails red.
#
# THREE LAYERS, and the reason there are three:
#   A. in-process, faked transport - Invoke-LcApi is replaced by a scripted fake. Fast, and
#      it can drive states (timeout, abandoned) a real daemon will not produce on demand.
#   B. a REAL CHILD PROCESS over a REAL socket - dispatch.ps1 is executed by powershell.exe
#      against a TCP server this drill runs, so the PROCESS EXIT CODE is observed rather
#      than the return value of Get-DispatchExitCode. Layer A cannot see the `exit` line at
#      all: hardwiring `exit 0` in script mode left the old drill entirely green.
#   C. LIVE, the real little-coder daemon over its declared transport. This runs BY DEFAULT.
#      It used to be opt-in behind -Live, which meant the default run printed "31/31 checks
#      passed" with zero coverage of the only transport that ships - a green count that
#      excludes the real thing is the failure class this whole effort exists to kill.
#      -Offline skips it, and then the summary line says the transport is NOT COVERED.
#
# What layer A asserts, and why each one is here rather than assumed:
#   1  the little-coder runner resolves to a transport record, not just a name
#   2  a docker-exec runner names a container (the failure the whole U4 item started from:
#      a config that named a door - 127.0.0.1:8090 - which was never published)
#   3  a completed task with a PASSING acceptance command -> outcome pass, exit 0
#   4  the acceptance command REACHES THE WIRE (the submitted body carries it), and is
#      absent when none was given - deleting the line that sets it must go red
#   5  a completed task with a FAILING acceptance command -> ok STILL true, outcome fail,
#      exit 3. Dispatch succeeding and the work being right are different facts
#   6  no acceptance command -> unverified, exit 4 (never a silent pass)
#   7  a task that never terminates -> ok false, status timeout, exit 1
#   8  a task that ends `abandoned` -> exit 1, NOT 4 (the header claimed 4 until 2026-08-30)
#   9  an unfocused daemon is refused BEFORE submitting, with the reason
#  10  a claude-code role is refused with a reason, not silently attempted
#  11  the event stream is drained after the terminal state, not only across polls: the
#      last event is scripted to arrive ONLY in that final drain
#  12  an audit record is written and carries the events (section C.7's "twin")
#  13  the runner's declared lease must be HELD BY THE CALLER or nothing is submitted

[CmdletBinding()]
param(
    [switch]$Offline,
    [switch]$Live      # accepted and ignored: live is now the default. -Offline is the opt-out.
)

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
. (Join-Path $Here "dispatch.ps1") -NoRun

$script:results = @()
function Check($label, $ok, $detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = $ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function Step($n, $text) { Write-Host "`n=== $n. $text ===" -ForegroundColor Cyan }

# --- the fake daemon (layer A) ------------------------------------------------------------
# A scripted little-coder. `Script` maps "METHOD path-prefix" to a queue of responses; each
# call shifts one off (the last is repeated), so a poll loop can be driven through states.
# It records the BODIES as well as the calls: a dispatcher that never puts the acceptance
# command on the wire is indistinguishable from one that does, if you only count calls.
$script:Fake = $null
function New-FakeDaemon {
    param([hashtable]$Script, [string]$Focus = "https://github.com/o/r")
    return [pscustomobject]@{
        Script = $Script; Focus = $Focus
        Calls  = New-Object System.Collections.ArrayList
        Bodies = New-Object System.Collections.ArrayList
    }
}
function Invoke-LcApi {
    # Replaces dispatch.ps1's real transport for the offline drill.
    param($Runner, [string]$Method = "GET", [string]$Path, $Body = $null, [int]$TimeoutSeconds = 60)
    [void]$script:Fake.Calls.Add("$Method $Path")
    if ($null -ne $Body) {
        [void]$script:Fake.Bodies.Add([pscustomobject]@{ call = "$Method $($Path.Split('?')[0])"; body = $Body })
    }
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
function Get-FakeBody([string]$Call) {
    $m = @($script:Fake.Bodies | Where-Object { $_.call -eq $Call })
    if (-not $m.Count) { return $null }
    return $m[0].body
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

# --- the drill's own lease namespace ------------------------------------------------------
# dispatch.ps1 now REFUSES to mutate a plane whose lease the caller does not hold. The drill
# therefore has to hold one - and takes it through lease.ps1, in a temp AI_STACK_LEASE_DIR,
# so it proves the two agree on the on-disk format instead of hand-writing a file that only
# looks right. Hermetic: the real locks dir is never touched.
$DrillOwner  = "dispatch-drill"
$OtherOwner  = "some-other-agent"
$LeaseName   = "coder"
$leaseRoot   = Join-Path ([IO.Path]::GetTempPath()) ("dispatch-lease-" + [guid]::NewGuid().ToString("N"))
$emptyLease  = Join-Path ([IO.Path]::GetTempPath()) ("dispatch-nolease-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $leaseRoot, $emptyLease | Out-Null
$prevLeaseDir = $env:AI_STACK_LEASE_DIR
$leaseScript  = Join-Path $Here "lease.ps1"
function Use-DrillLease([string]$Owner) {
    $env:AI_STACK_LEASE_DIR = $leaseRoot
    & $leaseScript -Release -Name $LeaseName -Owner $DrillOwner *> $null
    & $leaseScript -Release -Name $LeaseName -Owner $OtherOwner *> $null
    & $leaseScript -Acquire -Name $LeaseName -Owner $Owner *> $null
    return ($LASTEXITCODE -eq 0)
}

$script:transportCoverage = "NOT COVERED (the drill did not reach the live section)"

try {
$acquired = Use-DrillLease $DrillOwner

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
Check "the drill holds the runner's lease via lease.ps1" ($acquired -and [bool](Get-HeldLease -Name $LeaseName)) `
    ("{0} owns '{1}'" -f $DrillOwner, $LeaseName)

# --- 3/4/11/12: a passing acceptance command ---------------------------------------------
Step 2 "a completed task whose acceptance command passed"
# The event script is deliberately shaped so the LAST event arrives only in the drain that
# happens AFTER the terminal state. Deleting that drain must drop the count to 2.
$script:Fake = New-FakeDaemon @{
    "POST /tasks"           = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events"  = @((New-EventsView "running" @("e1", "e2") 2),
                                (New-EventsView "done" @() 2),
                                (New-EventsView "done" @("e3-after-terminal") 3))
    "GET /tasks/T1"         = @((New-TaskView "running"), (New-TaskView "done" "pass" "acceptance command exit 0" 4))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "do the thing" `
        -AcceptanceCommand "pytest -q" -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot `
        -LeaseOwner $DrillOwner
Check "dispatch reports ok" ($res.ok -eq $true) ""
Check "outcome is pass" ($res.outcome -eq "pass") $res.outcome
Check "the signal is carried through" ($res.signal -like "acceptance command exit 0*") $res.signal
Check "exit code 0" ((Get-DispatchExitCode $res) -eq 0) ("{0}" -f (Get-DispatchExitCode $res))
Check "events drained across polls AND after terminal" ($res.event_count -eq 3) ("{0}" -f $res.event_count)
Check "the runner and model are reported" (($res.runner -eq "little-coder") -and $res.model) ("{0}/{1}" -f $res.runner, $res.model)

$submitted = Get-FakeBody "POST /tasks"
Check "the acceptance command reached the wire" `
    ($null -ne $submitted -and "$($submitted.acceptance_command)" -eq "pytest -q") `
    ("{0}" -f $(if ($submitted) { $submitted.acceptance_command } else { "(no body captured)" }))
Check "so did the prompt and the caller identity" `
    ([bool]$submitted -and "$($submitted.prompt)" -eq "do the thing" -and "$($submitted.user_id)" -eq "harness") ""

# --- 12: the audit record ----------------------------------------------------------------
Step 3 "the audit record is the deliverable's twin"
Check "an audit file was written" ([bool]$res.audit_path -and (Test-Path $res.audit_path)) "$($res.audit_path)"
if ($res.audit_path -and (Test-Path $res.audit_path)) {
    $rec2 = Get-Content -Raw -Path $res.audit_path | ConvertFrom-Json
    Check "it carries the prompt" ($rec2.prompt -eq "do the thing") ""
    Check "it carries the acceptance command" ($rec2.acceptance_command -eq "pytest -q") ""
    Check "it carries every event" (@($rec2.events).Count -eq 3) ("{0}" -f @($rec2.events).Count)
    Check "including the one only the post-terminal drain can see" `
        (@($rec2.events) -contains "e3-after-terminal") (@($rec2.events) -join ",")
    Check "it carries the outcome" ($rec2.result.outcome -eq "pass") ""
}

# --- 5: a failing acceptance command ------------------------------------------------------
Step 4 "a completed task whose acceptance command FAILED"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @("e1") 1))
    "GET /tasks/T1"        = @((New-TaskView "done" "fail" "acceptance command exit 1" 6))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -AcceptanceCommand "pytest -q" -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot `
        -LeaseOwner $DrillOwner
Check "dispatch still reports ok (the DISPATCH worked)" ($res.ok -eq $true) ""
Check "outcome is fail" ($res.outcome -eq "fail") $res.outcome
Check "exit code 3, not 0" ((Get-DispatchExitCode $res) -eq 3) ("{0}" -f (Get-DispatchExitCode $res))

# --- 6: no acceptance command -------------------------------------------------------------
Step 5 "no acceptance command is UNVERIFIED, never a silent pass"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @() 0))
    "GET /tasks/T1"        = @((New-TaskView "done" "unverified" "" 2))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner
Check "outcome is unverified" ($res.outcome -eq "unverified") $res.outcome
Check "exit code 4, not 0" ((Get-DispatchExitCode $res) -eq 4) ("{0}" -f (Get-DispatchExitCode $res))
$submitted = Get-FakeBody "POST /tasks"
Check "and the body carries NO acceptance_command key" `
    ([bool]$submitted -and -not ($submitted.Keys -contains "acceptance_command")) `
    (($submitted.Keys) -join ",")

# --- 7: a task that never terminates ------------------------------------------------------
Step 6 "a task that never terminates times out instead of hanging or lying"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "running" @("e1") 1))
    "GET /tasks/T1"        = @((New-TaskView "running"))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 0 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner
Check "ok is false" ($res.ok -eq $false) ""
Check "status is timeout" ($res.status -eq "timeout") $res.status
Check "exit code 1 (dispatch failure)" ((Get-DispatchExitCode $res) -eq 1) ("{0}" -f (Get-DispatchExitCode $res))
Check "the timed-out attempt still left an audit record" ([bool]$res.audit_path -and (Test-Path $res.audit_path)) ""

# --- 8: a terminal state that is not `done` -----------------------------------------------
Step 7 "an ABANDONED task is exit 1, not exit 4"
# The header said "4 = ... or was abandoned" until 2026-08-30. It never did: ok is false for
# any terminal status that is not `done`, and Get-DispatchExitCode returns 1 on not-ok before
# it ever looks at the outcome. Pinned here so the docs and the code cannot drift again.
foreach ($terminal in @("abandoned", "rejected")) {
    $script:Fake = New-FakeDaemon @{
        "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
        "GET /tasks/T1/events" = @((New-EventsView $terminal @("e1") 1))
        "GET /tasks/T1"        = @((New-TaskView $terminal "unverified" "" 1))
    }
    $res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
            -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner
    Check "'$terminal' reports ok false" ($res.ok -eq $false) $res.status
    Check "'$terminal' exits 1, not 4" ((Get-DispatchExitCode $res) -eq 1) ("{0}" -f (Get-DispatchExitCode $res))
}

# --- 9: an unfocused daemon ---------------------------------------------------------------
Step 8 "an unfocused daemon is refused BEFORE a task is submitted"
$script:Fake = New-FakeDaemon -Focus "" -Script @{
    "POST /tasks" = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
}
$threw = ""
try {
    Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner | Out-Null
} catch { $threw = $_.Exception.Message }
Check "it refused" ([bool]$threw) $threw
Check "the reason names the fix (-Repo)" ($threw -like "*-Repo*") ""
Check "nothing was submitted" (-not (@($script:Fake.Calls) -contains "POST /tasks")) (@($script:Fake.Calls) -join ", ")

# --- 10: a cloud role ---------------------------------------------------------------------
Step 9 "a claude-code role is refused with a reason, not silently attempted"
$threw = ""
try {
    Invoke-HarnessTask -Role reviewer -Profile local-work-cloud-review -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner | Out-Null
} catch { $threw = $_.Exception.Message }
Check "it refused" ([bool]$threw) ""
Check "the reason names the runner kind" ($threw -like "*claude-code*") $threw

# --- 13: the lease ------------------------------------------------------------------------
Step 10 "the runner's lease must be HELD BY THE CALLER or nothing is submitted"
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @() 0))
    "GET /tasks/T1"        = @((New-TaskView "done" "pass" "ok" 1))
}
$env:AI_STACK_LEASE_DIR = $emptyLease      # nobody holds anything
$threw = ""
try {
    Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner | Out-Null
} catch { $threw = $_.Exception.Message }
Check "a free lease refuses the dispatch" ($threw -like "*not held*") $threw
Check "and nothing was submitted" (-not (@($script:Fake.Calls) -contains "POST /tasks")) (@($script:Fake.Calls) -join ", ")

$script:Fake.Calls.Clear()
[void](Use-DrillLease $OtherOwner)          # held - by somebody else
$threw = ""
try {
    Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner | Out-Null
} catch { $threw = $_.Exception.Message }
Check "someone ELSE's lease refuses it too (naming the holder)" `
    (($threw -like "*held by*") -and ($threw -like "*$OtherOwner*")) $threw
Check "and still nothing was submitted" (-not (@($script:Fake.Calls) -contains "POST /tasks")) (@($script:Fake.Calls) -join ", ")

$threw = ""
try {
    Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot | Out-Null
} catch { $threw = $_.Exception.Message }
Check "no owner at all is refused, with the acquire command" `
    (($threw -like "*no owner*") -and ($threw -like "*lease.ps1 -Acquire*")) $threw

[void](Use-DrillLease $DrillOwner)          # back to ours for the rest of the run
$script:Fake = New-FakeDaemon @{
    "POST /tasks"          = @([pscustomobject]@{ task_id = "T1"; status = "queued" })
    "GET /tasks/T1/events" = @((New-EventsView "done" @() 0))
    "GET /tasks/T1"        = @((New-TaskView "done" "pass" "ok" 1))
}
$res = Invoke-HarnessTask -Role worker -Profile all-local -Surface mattermost -Prompt "p" `
        -TimeoutMinutes 1 -PollSeconds 0 -AuditDir $auditRoot -LeaseOwner $DrillOwner
Check "with the lease held by the caller it proceeds" ($res.ok -eq $true) $res.status

# ==========================================================================================
# LAYER B - a real child process over a real socket
# ==========================================================================================
# Everything above calls Invoke-HarnessTask IN THIS PROCESS and reads Get-DispatchExitCode's
# return value. Neither touches script mode's `exit` line. This section runs dispatch.ps1 as
# powershell.exe would, against a TCP server, and reads $proc.ExitCode - so replacing the
# final line with `exit 0` fails here and nowhere else.

function Read-HttpRequest($stream) {
    $raw = New-Object System.Collections.Generic.List[byte]
    $buf = New-Object byte[] 4096
    $headEnd = -1
    while ($headEnd -lt 0) {
        $n = $stream.Read($buf, 0, $buf.Length)
        if ($n -le 0) { return $null }
        for ($i = 0; $i -lt $n; $i++) { $raw.Add($buf[$i]) }
        $text = [Text.Encoding]::ASCII.GetString($raw.ToArray())
        $headEnd = $text.IndexOf("`r`n`r`n")
    }
    $text = [Text.Encoding]::ASCII.GetString($raw.ToArray())
    $head = $text.Substring(0, $headEnd)
    $lines = @($head -split "`r`n")
    $requestLine = $lines[0] -split " "
    $len = 0
    $expect = $false
    foreach ($h in $lines) {
        if ($h -match '^(?i)content-length:\s*(\d+)') { $len = [int]$Matches[1] }
        if ($h -match '^(?i)expect:\s*100-continue') { $expect = $true }
    }
    if ($expect) {
        $cont = [Text.Encoding]::ASCII.GetBytes("HTTP/1.1 100 Continue`r`n`r`n")
        $stream.Write($cont, 0, $cont.Length); $stream.Flush()
    }
    $have = $raw.Count - ($headEnd + 4)
    while ($have -lt $len) {
        $n = $stream.Read($buf, 0, [Math]::Min($buf.Length, $len - $have))
        if ($n -le 0) { break }
        for ($i = 0; $i -lt $n; $i++) { $raw.Add($buf[$i]) }
        $have += $n
    }
    $bodyBytes = $raw.GetRange($headEnd + 4, [Math]::Max(0, $raw.Count - $headEnd - 4)).ToArray()
    return [pscustomobject]@{
        Method = $requestLine[0]
        Path   = $requestLine[1].Split("?")[0]
        Body   = [Text.Encoding]::UTF8.GetString($bodyBytes)
    }
}

function Write-HttpJson($stream, $obj) {
    $json = ($obj | ConvertTo-Json -Depth 8 -Compress)
    $bytes = [Text.Encoding]::UTF8.GetBytes($json)
    $head = "HTTP/1.1 200 OK`r`nContent-Type: application/json`r`nContent-Length: $($bytes.Length)`r`nConnection: close`r`n`r`n"
    $hb = [Text.Encoding]::ASCII.GetBytes($head)
    $stream.Write($hb, 0, $hb.Length)
    $stream.Write($bytes, 0, $bytes.Length)
    $stream.Flush()
}

function Invoke-RealProcessDispatch {
    # Runs dispatch.ps1 as a child process against a TCP fake, and returns the process's
    # ACTUAL exit code plus what the child put on the wire.
    param([string]$Outcome, [string]$AcceptanceCommand = "")
    $listener = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port

    # A config whose little-coder runner speaks http to THIS socket. Same file shape the
    # real one has, so the http transport branch of Invoke-LcApi is the code under test.
    $cfg = Get-Content -Raw -Path (Join-Path $Here "harness.config.json") | ConvertFrom-Json
    $cfg.runners.'little-coder'.transport = "http"
    $cfg.runners.'little-coder'.base_url = "http://127.0.0.1:$port"
    $cfgPath = Join-Path ([IO.Path]::GetTempPath()) ("dispatch-cfg-" + [guid]::NewGuid().ToString("N") + ".json")
    [IO.File]::WriteAllText($cfgPath, ($cfg | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))

    $outFile = Join-Path ([IO.Path]::GetTempPath()) ("dispatch-out-" + [guid]::NewGuid().ToString("N") + ".txt")
    $argv = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $Here "dispatch.ps1"),
              "-Role", "worker", "-Profile", "all-local", "-Surface", "mattermost",
              "-Prompt", "child process item", "-TimeoutMinutes", "1", "-PollSeconds", "0",
              "-AuditDir", $auditRoot, "-LeaseOwner", $DrillOwner)
    if ($AcceptanceCommand) { $argv += @("-AcceptanceCommand", $AcceptanceCommand) }
    # Start-Process joins -ArgumentList with spaces and quotes NOTHING, so any argument
    # holding a space (this repo lives under "D:\Open WebUI") arrives split in two. Found
    # by this drill failing with: Processing -File 'D:\Open' failed.
    $argv = @($argv | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } })

    $prevCfg = $env:AI_STACK_HARNESS_CONFIG
    $env:AI_STACK_HARNESS_CONFIG = $cfgPath
    $seen = New-Object System.Collections.ArrayList
    try {
        $proc = Start-Process -FilePath "powershell.exe" -ArgumentList $argv -PassThru `
                              -NoNewWindow -RedirectStandardOutput $outFile
        # Touch .Handle before waiting. Start-Process -PassThru hands back a Process whose
        # ExitCode reads as $null after the process ends unless the handle was cached while
        # it was alive - which would have made every exit-code check here silently vacuous.
        $null = $proc.Handle
        $deadline = (Get-Date).AddSeconds(120)
        while (-not $proc.HasExited -and (Get-Date) -lt $deadline) {
            if ($listener.Pending()) {
                $client = $listener.AcceptTcpClient()
                try {
                    $stream = $client.GetStream()
                    $req = Read-HttpRequest $stream
                    if ($req) {
                        [void]$seen.Add($req)
                        if ($req.Path -eq "/health") {
                            Write-HttpJson $stream @{ status = "ok"; version = "tcp-fake"
                                                      focus = "https://github.com/o/r"; queue_depth = 0 }
                        } elseif ($req.Path -eq "/tasks" -and $req.Method -eq "POST") {
                            Write-HttpJson $stream @{ task_id = "T9"; status = "queued" }
                        } elseif ($req.Path -like "*/events") {
                            Write-HttpJson $stream @{ status = "done"; events = @("ev"); next_offset = 1; done = $true }
                        } else {
                            Write-HttpJson $stream @{ task_id = "T9"; status = "done"; outcome = $Outcome
                                                      signal = "from the tcp fake"; detail = "1 command(s)"
                                                      commands = 1; answer = "a"; repo = "https://github.com/o/r" }
                        }
                    }
                } finally { $client.Close() }
            } else {
                Start-Sleep -Milliseconds 20
            }
        }
        if (-not $proc.HasExited) { $proc.Kill() }
        $proc.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $proc.ExitCode
            Requests = @($seen)
            Output   = $(if (Test-Path $outFile) { Get-Content -Raw -Path $outFile } else { "" })
        }
    } finally {
        $listener.Stop()
        if ($null -eq $prevCfg) {
            Remove-Item Env:\AI_STACK_HARNESS_CONFIG -ErrorAction SilentlyContinue
        } else {
            $env:AI_STACK_HARNESS_CONFIG = $prevCfg
        }
        Remove-Item -Force -ErrorAction SilentlyContinue $cfgPath, $outFile
    }
}

Step 11 "a REAL child process, a REAL socket: the exit code the shell sees"
$r3 = Invoke-RealProcessDispatch -Outcome "fail" -AcceptanceCommand "pytest -q"
Check "the child process actually exits 3 on a failed acceptance command" ($r3.ExitCode -eq 3) `
    ("exit={0}" -f $r3.ExitCode)
$post = @($r3.Requests | Where-Object { $_.Method -eq "POST" -and $_.Path -eq "/tasks" })
Check "its POST body crossed a real socket carrying the acceptance command" `
    ($post.Count -ge 1 -and $post[0].Body -like '*"acceptance_command":"pytest -q"*') `
    ("{0}" -f $(if ($post.Count) { $post[0].Body } else { "(no POST seen)" }))
Check "the http transport branch was exercised end to end" (@($r3.Requests).Count -ge 3) `
    ((@($r3.Requests) | ForEach-Object { "$($_.Method) $($_.Path)" }) -join " | ")

$r0 = Invoke-RealProcessDispatch -Outcome "pass" -AcceptanceCommand "pytest -q"
Check "and exits 0 when the acceptance command passed (not hardwired to 3)" ($r0.ExitCode -eq 0) `
    ("exit={0}" -f $r0.ExitCode)

$env:AI_STACK_LEASE_DIR = $emptyLease
$rL = Invoke-RealProcessDispatch -Outcome "pass" -AcceptanceCommand "pytest -q"
Check "a child with no lease exits 1 and submits nothing" `
    ($rL.ExitCode -eq 1 -and @($rL.Requests | Where-Object { $_.Method -eq "POST" }).Count -eq 0) `
    ("exit={0} requests={1}" -f $rL.ExitCode, @($rL.Requests).Count)
[void](Use-DrillLease $DrillOwner)

# ==========================================================================================
# LAYER C - the real daemon. DEFAULT ON.
# ==========================================================================================
if ($Offline) {
    $script:transportCoverage = "NOT COVERED (-Offline was passed)"
    Write-Host "`n=== 12. LIVE probe SKIPPED (-Offline) ===" -ForegroundColor Yellow
} else {
    Step 12 "LIVE: the declared transport actually reaches the real daemon"
    Remove-Item Function:\Invoke-LcApi -ErrorAction SilentlyContinue   # back to the real one
    . (Join-Path $Here "dispatch.ps1") -NoRun
    $liveRec = (Resolve-RunnerRecord -Role worker -Profile all-local -Surface mattermost).record
    $container = Get-RunnerPath $liveRec "container" ""
    $tp = Get-RunnerPath $liveRec "transport" ""
    if ($tp -eq "docker-exec") {
        # The RUNTIME half of the door check. test_harness_config.py's _door_problems is a
        # substring match over compose TEXT: it proves a door is DECLARED. Those two genuinely
        # differ on this host - compose declares 127.0.0.1:9091->9090 for little-coder while
        # `docker port little-coder` prints nothing and
        # `docker inspect ... .NetworkSettings.Ports` gives {"9090/tcp":[]}. The declared and
        # running states disagree; the CAUSE IS NOT ESTABLISHED (the container was created
        # 2026-08-23, two days AFTER the ports line landed in 56af93a on 2026-08-21). Only a
        # runtime probe sees the disagreement at all.
        # $ErrorActionPreference is Stop for this drill, and a native exe writing to stderr
        # under `2>&1` raises NativeCommandError - which made a missing container ABORT the
        # run before the summary line. Drop to Continue for this one call so it fails as a
        # RED check with a summary, not as an abort.
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        $state = ((& docker.exe inspect -f "{{.State.Running}}" $container 2>&1) | Out-String).Trim()
        $ErrorActionPreference = $prevEap
        Check "the docker-exec runner's container EXISTS and is running (not just declared)" `
            ($state -eq "True") ("docker inspect {0} -> {1}" -f $container, $state)
    }
    $p = Test-RunnerReachable -Role worker -Profile all-local -Surface mattermost
    Check "little-coder is reachable over $($p.transport)" ($p.ok -eq $true) $p.detail
    Check "it reports a version" ([bool]$p.version) $p.version
    $script:transportCoverage = $(if ($p.ok) {
        "COVERED (real daemon over $($p.transport), version $($p.version))"
    } else {
        "ATTEMPTED AND FAILED over $($p.transport)"
    })
}

}
finally {
    $env:AI_STACK_LEASE_DIR = $leaseRoot
    & $leaseScript -Release -Name $LeaseName -Owner $DrillOwner *> $null
    & $leaseScript -Release -Name $LeaseName -Owner $OtherOwner *> $null
    if ($null -eq $prevLeaseDir) {
        Remove-Item Env:\AI_STACK_LEASE_DIR -ErrorAction SilentlyContinue
    } else {
        $env:AI_STACK_LEASE_DIR = $prevLeaseDir
    }
    Remove-Item -Recurse -Force $auditRoot, $leaseRoot, $emptyLease -ErrorAction SilentlyContinue
}

$failed = @($script:results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0}/{1} checks passed" -f (@($script:results).Count - $failed.Count), @($script:results).Count) `
    -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
# The count alone is what made the old default run misleading - it excluded the only
# real-transport coverage there is. Every run that REACHES this line states the coverage:
# COVERED, NOT COVERED, or ATTEMPTED AND FAILED. Not 'always' - a terminating error before
# here still aborts with no summary at all. That is fail-safe (the run exits non-zero and
# prints no count, so it cannot be read as green) but it is not a coverage statement.
Write-Host ("real transport: {0}" -f $script:transportCoverage) `
    -ForegroundColor $(if ($script:transportCoverage -like "COVERED*") { "Green" } else { "Yellow" })
if ($failed.Count) {
    $failed | ForEach-Object { Write-Host ("  FAILED: {0} {1}" -f $_.check, $_.detail) -ForegroundColor Red }
    exit 1
}
exit 0
