# register-issue-cadence.ps1 - the git-issue intake door's cadence (U2).
#
# NOT RUN AUTOMATICALLY, and that is deliberate. Registering it starts an unattended job
# that spends model calls every day: the sweep runs `claude -p` per unplanned or stale
# issue. §C.2 class 4 lists "spending real money or calling external services beyond the
# session itself" as a hard stop, so the mechanism ships and the operator starts it.
#
#   .\scripts\issue-ops\register-issue-cadence.ps1              # register both
#   .\scripts\issue-ops\register-issue-cadence.ps1 -WhatIfOnly  # show, change nothing
#   .\scripts\issue-ops\register-issue-cadence.ps1 -Unregister  # remove both
#
# WHY SCHEDULED TASKS AND NOT SUPERCRONIC. §C.3 decision 4 names supercronic (OB1's crontab)
# as the cadence owner, and for anything that runs IN the stack it is the right answer -
# single source of cron truth. This door cannot use it: `issue_ops.py` shells to a headless
# `claude` binary, reads the GitHub App private key from
# `agent-org/agent-bridge/secrets/github-app-key.pem`, and runs `git` against the repo root.
# None of those exist inside an OB1 container, and every entry in that crontab is an HTTP
# call to a container on obnet. Containerising the planner is a much larger piece of work
# than the cadence it would carry.
#
# So the owner here is the HOST Scheduled Task family - the same one the watchdog and the
# sysadmin tasks use, following register-sysadmin-tasks.ps1's shape. Logged as a class-2
# deviation in DECISIONS.md with this evidence.

[CmdletBinding()]
param(
    [switch]$WhatIfOnly,
    [switch]$Unregister
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$repo = (Resolve-Path (Join-Path $here "..\..")).Path
$ops  = Join-Path $here "issue_ops.py"

$SweepTask     = "ai-stack issue-ops daily sweep"
$SynthesisTask = "ai-stack issue-ops weekly synthesis"

if (-not (Test-Path $ops)) { throw "issue_ops.py not found at $ops" }

if ($Unregister) {
    foreach ($t in @($SweepTask, $SynthesisTask)) {
        if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
            if ($WhatIfOnly) { Write-Host "would unregister '$t'" }
            else { Unregister-ScheduledTask -TaskName $t -Confirm:$false; Write-Host "unregistered '$t'" }
        } else { Write-Host "'$t' is not registered" }
    }
    exit 0
}

$py = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $py) { throw "python not found - the cadence runs issue_ops.py" }
$user = "$env:USERDOMAIN\$env:USERNAME"

# The sweep PLANS and nothing else - no approval, no execution. Selection happens at the
# weekly verdict thread, which is this door's operator-confirm gate (§C.3 decision 5).
#
# --limit 8 caps a day's model spend. Truncation is REPORTED by the sweep, so a capped run
# is visible rather than looking like a quiet day.
$aSweep = New-ScheduledTaskAction -Execute $py.Source `
    -Argument "`"$ops`" sweep --limit 8" -WorkingDirectory $repo
$tSweep = New-ScheduledTaskTrigger -Daily -At 6am
$pLimit = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
# StartWhenAvailable: a machine asleep at 06:00 must still sweep when it wakes, or a laptop
# day silently produces no plans and nothing says so.
$sSweep = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew

# The weekly synthesis collects the week's plans and runs the cross-plan pass, then posts the
# verdict thread. Monday 09:00, AFTER a sweep has run - a synthesis over plans that do not
# exist yet is the empty-store failure Phase 4 of the memory plane is gated on.
$aSynth = New-ScheduledTaskAction -Execute $py.Source `
    -Argument "`"$ops`" synthesis" -WorkingDirectory $repo
$tSynth = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 9am
$sSynth = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew

if ($WhatIfOnly) {
    Write-Host "would register '$SweepTask'      : daily 06:00  -> issue_ops.py sweep --limit 8"
    Write-Host "would register '$SynthesisTask'  : Monday 09:00 -> issue_ops.py synthesis"
    Write-Host ""
    Write-Host "NOTHING was changed. Both spend model calls when they run; start them deliberately."
    exit 0
}

Register-ScheduledTask -TaskName $SweepTask -Action $aSweep -Trigger $tSweep `
    -Principal $pLimit -Settings $sSweep -Force `
    -Description "ai-stack U2 issue door: daily sweep -> plans for unplanned/stale issues (anchor-drafts only)" | Out-Null
Write-Host "Registered '$SweepTask' (daily 06:00)."

Register-ScheduledTask -TaskName $SynthesisTask -Action $aSynth -Trigger $tSynth `
    -Principal $pLimit -Settings $sSynth -Force `
    -Description "ai-stack U2 issue door: weekly plan-vs-plan synthesis -> Mattermost verdict thread" | Out-Null
Write-Host "Registered '$SynthesisTask' (Monday 09:00)."
Write-Host ""
Write-Host "Both produce ANCHOR-DRAFTS. Nothing is approved or executed without the operator's"
Write-Host "verdict in the weekly Mattermost thread."
