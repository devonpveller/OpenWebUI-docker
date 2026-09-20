# stack.ps1 - a SHIM over scripts/stack/stack.py (sl-driver-parity, 2026-09-19).
#
# This file used to BE the driver: an ordered registry of the eight compose
# projects, the health sweep, and the stats hand-off. All of that now lives in
# scripts/stack/stack.py, which reads stack.manifest.toml - one declaration of
# the planes instead of a second copy of the list inside a PowerShell array.
#
# WHY THE FILE SURVIVES AT ALL, rather than being archived:
#   * runbooks, plane READMEs and compose-file comments say
#     `.\scripts\stack\stack.ps1 up <plane>` in about forty places;
#   * an operator's muscle memory and shell history say the same;
#   * a Windows shortcut or scheduled task can be pointed at a .ps1 without a
#     python-on-PATH assumption baked into the task definition.
# Retiring it is a later stack-layers item, once every caller has moved. Until
# then this forwards and does NOTHING else - no probe, no ordering, no registry.
# There is deliberately no logic here to drift from the driver.
#
# Verified 2026-09-19: neither scheduled task that touches this repo's stack
# (`StackWatchdog` -> scripts\checks\stack-watchdog.ps1, `AI-Stack Weekly
# Maintenance` -> scripts\maintenance\weekly-maintenance.ps1) invokes this
# script at all - both call their own file directly, and neither contains the
# string "stack.ps1". The shim cannot break them.
#
#   .\scripts\stack\stack.ps1 up               # every plane, dependency order
#   .\scripts\stack\stack.ps1 up inference     # one plane
#   .\scripts\stack\stack.ps1 up --dry-run     # print the docker lines, run nothing
#   .\scripts\stack\stack.ps1 down             # every plane, reverse order
#   .\scripts\stack\stack.ps1 status           # per-project container states
#   .\scripts\stack\stack.ps1 restart memory   # one plane, in place
#   .\scripts\stack\stack.ps1 health           # functional probes across every plane
#   .\scripts\stack\stack.ps1 stats            # inference demand + queue statistics
#
# WHERE sl-ob1-profiles' FOUR-PROFILE OB1 ROW WENT. That item (merged 2026-09-19)
# changed this script's `$Projects` registry to pass
# `--profile idea-refinery --profile research --profile wiki --profile notebook`
# for OB1, so the pre-manifest driver kept starting the thirty containers running
# on this host. The registry does not exist any more, so there is nowhere in this
# file for that line to live; the SAME SET is expressed in the driver instead:
# `idea-refinery` is `default = true` in stack.manifest.toml and `requires`
# `research`, so `up` passes both. THAT IS NO LONGER THE WHOLE SET: sl-ob1-gitlink
# bumped the OB1 gitlink 5005197 -> fe3e045 on 2026-09-20, and the pinned compose
# now declares all four. Measured at fe3e045 (`config --services`): bare 20,
# idea-refinery+research 22, all four 30 - the 30 that are running. So `wiki` and
# `notebook` gate SEVEN live containers this script's `up` will not start on its
# own. The operator closes that ONCE, either with
# `stack.py init --product research --force` (writes all four to .stack/state.json)
# or with COMPOSE_PROFILES=research,wiki,notebook,idea-refinery in OB1/docker/.env
# (the driver unions the plane env's list into the flags it passes - see
# effective_profiles in stack.py). Both were measured; neither is done by this
# script.
#
# `restart` with no plane still refuses - the refusal is the DRIVER's
# (stack.py cmd_restart), reached by forwarding "all" rather than reimplemented
# here. The portal is still not managed (portal-on.ps1 / portal-off.ps1): the
# manifest marks that plane `manual`, so the driver skips it and says so.
#
# Anything the driver understands can be passed through: a plane name lands in
# $Plane, everything after it in $Rest.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("up", "down", "status", "restart", "health", "stats", "list", "doctor", "inventory")]
    [string]$Action = "status",

    [Parameter(Position = 1)]
    [string]$Plane = "all",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

# NOT 'Stop'. The driver writes everything - refusals included - to stdout and
# carries the failure in its exit code, precisely so a .ps1 can call it without
# PS 5.1 turning a native stderr write into a terminating NativeCommandError.
# Setting 'Stop' here would re-introduce the trap the driver was shaped to avoid.
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$Driver = Join-Path $PSScriptRoot 'stack.py'

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "stack.ps1 is a shim over $Driver and python is not on PATH." -ForegroundColor Red
    Write-Host "Install Python >= 3.11 (the driver is standard-library only), or run the compose" -ForegroundColor Red
    Write-Host "project directly: docker compose -f <plane>/docker-compose.yml <verb>" -ForegroundColor Red
    exit 1
}

# `all` is this script's historical default for $Plane and means "every declared
# plane" - the driver spells that --all, because a bare `stack.py up` means the
# narrower "whatever THIS machine enables" and the two must not be confused.
$Argv = @($Action)
switch ($Action) {
    { $_ -in @('up', 'down', 'status') } {
        if ($Plane -and $Plane -ne 'all') { $Argv += $Plane } else { $Argv += '--all' }
    }
    'restart' {
        # Forwarded verbatim, "all" included: refusing it is the driver's job.
        if ($Plane) { $Argv += $Plane }
    }
    default {
        # health / stats / list / doctor / inventory take no plane. A plane left
        # over from the default is dropped rather than passed as a stray token.
        if ($Plane -and $Plane -ne 'all') { $Argv += $Plane }
    }
}
if ($Rest) { $Argv += $Rest }

Set-Location $RepoRoot
& python $Driver @Argv
exit $LASTEXITCODE
