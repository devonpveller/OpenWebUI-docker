#requires -Version 5
<#
.SYNOPSIS
  The one door for deploying an OB1 integration that ships as a `build:`
  service: build from the checked-out pin, label the image with that pin,
  bring the service up, force-recreate its named dependents, wait for healthy,
  and refuse to report success over a restart loop.

.DESCRIPTION
  Why this exists (2026-09-06): openbrain-curator crash-looped in production
  for 14 h after a gitlink bump; the image had been rebuilt from a tree that
  was missing a module, nothing recorded which OB1 commit the running image
  came from, and the follow-up deploy of the fix showed that
  `docker compose up -d openbrain-curator openbrain-research` waits on the
  curator's healthcheck but does NOT recreate research when only its
  depends_on changed. Today there is no written deploy procedure for a
  gitlink bump; this script is it.

  What it does, in order:
    1. Reads the OB1 pin from `git ls-tree HEAD OB1` (the parent's gitlink)
       and REFUSES if `git -C OB1 rev-parse HEAD` differs - the same rule
       gates 5b/5c/5d apply at the commit. An image built from a tree that is
       not the pin, but labelled with the pin, is a lie with a checksum.
    2. Renders the compose file (`docker compose config --format json`) to
       find the service, its build context, its healthcheck and the
       dependents named in -Recreate. Refuses a service that has no `build:`
       (a pinned image deploys with `up -d` per UPDATE-MANAGEMENT.md).
    3. Prints the plan: pin, build/up/recreate commands, the poll bound.
       -WhatIfOnly stops here having run only read-only commands
       (git ls-tree / rev-parse / status, docker compose config / ps).
    4. `docker compose build --build-arg OB1_SHA=<pin> <svc>` - the two Deno
       Dockerfiles carry `ARG OB1_SHA` + `LABEL org.opencontainers.image.revision`.
    5. `docker compose up -d <svc>`, then watches the container: healthy
       (or, with no healthcheck, running) for the whole loop window, no
       restart. A container seen `restarting`, `exited`, or with a
       RestartCount above 0 fails the deploy the moment it is seen.
    6. For each -Recreate dependent: `up -d --no-deps --force-recreate <dep>`
       (--no-deps because the service is already proven healthy above; a
       plain --force-recreate would recreate the dependencies too), then the
       same watch. StartedAt before/after is printed as the proof it moved.
    7. Prints the image label against the pin and a summary; exit non-zero
       names every container that failed the watch.

  WHAT A GREEN DOES NOT PROVE:
    * That the service WORKS. Healthy means its own healthcheck passed
      (curator: GET /health with db:true). openbrain-research has no
      healthcheck (recorded follow-up), so for it "running for 60 s without
      a restart" is all this script can say.
    * That the pin is CORRECT. The refusal in step 1 checks disk == gitlink;
      it does not check that the gitlink is on the OB1 remote (the T0 rule in
      every test plan does) or that the pinned code is any good (gate 5d and
      the tests do).
    * That env wiring is right. A missing variable in OB1/docker/.env fails
      at runtime exactly as before; the watch will SEE the loop, it will not
      explain it.
    * Anything about services this script did not touch. A dependent not
      named in -Recreate keeps running the old image/config.

  EXIT CODES: 0 deployed and watched clean; 2 usage/compose problem;
  3 pin refusal; 4 build failed; 5 `up` failed; 6 a container failed the
  watch (looping / exited / never healthy) or the label does not match.

.PARAMETER Service
  The compose service to build + deploy (e.g. openbrain-curator).
.PARAMETER Recreate
  Dependents to `up -d --no-deps --force-recreate` after the service is
  healthy (e.g. openbrain-research). Comma-separated or repeated.
.PARAMETER WhatIfOnly
  Print the plan and exit 0. Nothing is built, started or recreated.
.PARAMETER ComposeFile
  Defaults to OB1\docker\docker-compose.yml (project open-brain). A tester
  points it at an isolated copy (project + container names prefixed, images
  tagged :wt-<id>); the pin rule still reads THIS repo's OB1.
.PARAMETER EnvFile
  Optional --env-file for the compose project (the production file picks up
  OB1/docker/.env on its own; a scratch copy elsewhere needs one).
.PARAMETER LoopWindowSeconds
  How long a container must stay up without a restart before it is
  believed (default 60).

.EXAMPLE
  powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -WhatIfOnly
.EXAMPLE
  powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Service,
    [string[]]$Recreate = @(),
    [switch]$WhatIfOnly,
    [string]$ComposeFile = 'OB1\docker\docker-compose.yml',
    [string]$EnvFile = '',
    [int]$LoopWindowSeconds = 60,
    [int]$PollSeconds = 3
)

# Natives are checked through $LASTEXITCODE; under 'Stop' a compose WARN on
# stderr would become a terminating NativeCommandError in PS 5.1.
$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $repoRoot

# A hook-launched shell exports GIT_DIR/GIT_INDEX_FILE for the PARENT repo and
# they override `git -C OB1` (see .githooks/commit-msg). This script is not a
# hook, but the operator may run it from one; clear them for our own process.
foreach ($v in 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE') { Remove-Item "Env:$v" -ErrorAction SilentlyContinue }

function Write-Step { param([string]$Msg) Write-Host "[ob1-deploy] $Msg" -ForegroundColor Cyan }
function Write-Warn { param([string]$Msg) Write-Host "[ob1-deploy] WARN: $Msg" -ForegroundColor Yellow }
function Write-Fail { param([string]$Msg) Write-Host "[ob1-deploy] FAIL: $Msg" -ForegroundColor Red }
function Exit-With { param([int]$Code, [string]$Msg) Write-Fail $Msg; exit $Code }

# -Recreate a,b  and  -Recreate "a, b"  both arrive as one list.
$dependents = @($Recreate | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
if ($dependents -contains $Service) { Exit-With 2 "-Recreate names the service itself ($Service); name only its dependents." }

# ---- 1. The pin rule -------------------------------------------------------
$lsTree = (& git ls-tree HEAD OB1 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $lsTree -notmatch '^160000 commit ([0-9a-f]{40})') {
    Exit-With 3 "cannot read the OB1 gitlink from 'git ls-tree HEAD OB1' in $repoRoot (got: '$lsTree')."
}
$pin = $Matches[1]
if (-not (Test-Path (Join-Path $repoRoot 'OB1\.git'))) {
    Exit-With 3 "OB1 is not initialised in this checkout (no OB1\.git) - run 'git submodule update --init' and check it out at $pin."
}
$disk = (& git -C OB1 rev-parse HEAD 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $disk -notmatch '^[0-9a-f]{40}$') {
    Exit-With 3 "cannot read OB1's HEAD on disk ('git -C OB1 rev-parse HEAD' said: '$disk')."
}
if ($disk -ne $pin) {
    Exit-With 3 ("REFUSED: OB1 on disk is at $disk but the parent's gitlink (git ls-tree HEAD OB1) pins $pin. " +
        "An image built now would be labelled $($pin.Substring(0,7)) yet built from $($disk.Substring(0,7)). " +
        "Check OB1 out at the pin ('git -C OB1 checkout $pin') or bump the gitlink first - gates 5b/5c/5d apply the same rule.")
}
Write-Step "OB1 pin $pin == disk HEAD (ok)"

# ---- 2. Render the compose project -----------------------------------------
if (-not (Test-Path $ComposeFile)) { Exit-With 2 "compose file not found: $ComposeFile" }
$composeArgs = @('-f', $ComposeFile)
if ($EnvFile) {
    if (-not (Test-Path $EnvFile)) { Exit-With 2 "env file not found: $EnvFile" }
    $composeArgs += @('--env-file', $EnvFile)
}
$cfgText = (& docker compose @composeArgs config --format json 2>$null | Out-String)
if ($LASTEXITCODE -ne 0 -or -not $cfgText.Trim()) {
    Exit-With 2 "'docker compose $($composeArgs -join ' ') config' failed - is Docker running and the compose file renderable?"
}
try { $cfg = $cfgText | ConvertFrom-Json } catch { Exit-With 2 "could not parse 'docker compose config --format json': $_" }
$project = $cfg.name
$svcCfg = $cfg.services.$Service
if (-not $svcCfg) {
    $known = @($cfg.services.PSObject.Properties.Name) -join ', '
    Exit-With 2 "service '$Service' is not in $ComposeFile (services: $known)."
}
if (-not $svcCfg.build) {
    Exit-With 2 ("service '$Service' has no 'build:' - it is a pinned image (" + $svcCfg.image +
        "). Bump the pin and 'docker compose up -d $Service' per documentation/runbooks/UPDATE-MANAGEMENT.md; this door is for images built from OB1.")
}
foreach ($d in $dependents) {
    if (-not $cfg.services.$d) { Exit-With 2 "-Recreate names '$d', which is not a service in $ComposeFile." }
}
# Dependents compose itself knows about, so the operator sees what -Recreate is leaving alone.
$declared = @()
foreach ($p in $cfg.services.PSObject.Properties) {
    if ($p.Value.depends_on -and ($p.Value.depends_on.PSObject.Properties.Name -contains $Service)) { $declared += $p.Name }
}
$notRecreated = @($declared | Where-Object { $dependents -notcontains $_ })

# Is the build context the pinned tree? A scratch copy elsewhere is allowed
# (a tester's broken-Dockerfile case) but must not pass as the pin.
$context = [string]$svcCfg.build.context
# compose renders the context with whichever slashes the file/.env used;
# compare on one form or a forward-slash path is "outside OB1" by accident.
$contextN = $context.Replace('/', '\').TrimEnd('\')
$ob1Root = (Resolve-Path (Join-Path $repoRoot 'OB1')).Path.TrimEnd('\')
$contextInOb1 = $contextN.StartsWith($ob1Root + '\', [System.StringComparison]::OrdinalIgnoreCase)
if ($contextInOb1) {
    $rel = $contextN.Substring($ob1Root.Length).TrimStart('\').Replace('\', '/')
    $dirty = (& git -C OB1 status --porcelain -- $rel 2>&1 | Out-String).Trim()
    if ($dirty) {
        Exit-With 3 ("REFUSED: the build context OB1/$rel has uncommitted changes, so the image would not be the pin it is labelled with:`n" + $dirty)
    }
}
else {
    Write-Warn "build context '$context' is OUTSIDE this checkout's OB1 - the label will claim $($pin.Substring(0,7)) but the image is built from that directory (fine for a scratch test, never for production)."
}

# ---- healthcheck bound -----------------------------------------------------
function ConvertTo-Seconds {
    # compose config renders durations as Go strings ("1m30s", "500ms") or,
    # in some versions, nanoseconds as a number.
    param($Value)
    if ($null -eq $Value) { return 0.0 }
    if ($Value -is [int] -or $Value -is [long] -or $Value -is [double]) { return [double]$Value / 1e9 }
    $s = [string]$Value; $total = 0.0
    foreach ($m in [regex]::Matches($s, '(\d+(?:\.\d+)?)(h|ms|us|ns|m|s)')) {
        $n = [double]$m.Groups[1].Value
        switch ($m.Groups[2].Value) {
            'h'  { $total += $n * 3600 }
            'm'  { $total += $n * 60 }
            's'  { $total += $n }
            'ms' { $total += $n / 1e3 }
            'us' { $total += $n / 1e6 }
            'ns' { $total += $n / 1e9 }
        }
    }
    return $total
}
function Get-HealthBound {
    # start_period + retries * (interval + timeout) + one interval, in seconds;
    # $null when the service has no (enabled) healthcheck.
    param($SvcCfg)
    $hc = $SvcCfg.healthcheck
    if (-not $hc -or $hc.disable -eq $true -or ($hc.test -and ($hc.test[0] -eq 'NONE'))) { return $null }
    $interval = ConvertTo-Seconds $hc.interval; if ($interval -le 0) { $interval = 30 }
    $timeout  = ConvertTo-Seconds $hc.timeout;  if ($timeout  -le 0) { $timeout  = 30 }
    $start    = ConvertTo-Seconds $hc.start_period
    $retries  = 3; if ($hc.retries) { $retries = [int]$hc.retries }
    return @{ Seconds = [int][math]::Ceiling($start + $retries * ($interval + $timeout) + $interval)
              Text = "start_period $start s + retries $retries x (interval $interval s + timeout $timeout s) + interval" }
}

$svcBound = Get-HealthBound $svcCfg
$depBounds = @{}
foreach ($d in $dependents) { $depBounds[$d] = Get-HealthBound $cfg.services.$d }

# ---- 3. The plan -----------------------------------------------------------
$composeCmd = "docker compose $($composeArgs -join ' ')"
Write-Host ""
Write-Host "==> ob1-deploy plan (project $project)" -ForegroundColor Cyan
Write-Host ("    pin        : {0}" -f $pin)
Write-Host ("    service    : {0}  (image {1}, context {2})" -f $Service, $svcCfg.image, $context)
Write-Host ("    build      : {0} build --build-arg OB1_SHA={1} {2}" -f $composeCmd, $pin, $Service)
Write-Host ("    up         : {0} up -d {1}" -f $composeCmd, $Service)
if ($svcBound) {
    Write-Host ("    poll       : docker inspect <{0}> until State.Health.Status == healthy, bound {1} s ({2}), then no restart for {3} s" -f $Service, $svcBound.Seconds, $svcBound.Text, $LoopWindowSeconds)
} else {
    Write-Host ("    poll       : {0} has NO healthcheck - docker inspect until State.Status == running with no restart for {1} s" -f $Service, $LoopWindowSeconds)
}
if ($dependents.Count -gt 0) {
    foreach ($d in $dependents) {
        $b = $depBounds[$d]
        $how = if ($b) { "healthy within $($b.Seconds) s" } else { "running, no restart for $LoopWindowSeconds s (no healthcheck)" }
        Write-Host ("    recreate   : {0} up -d --no-deps --force-recreate {1}   -> watch: {2}" -f $composeCmd, $d, $how)
    }
} else {
    Write-Host "    recreate   : (none named)"
}
if ($notRecreated.Count -gt 0) {
    Write-Host ("    NOT recreated (depends_on {0} but not in -Recreate): {1}" -f $Service, ($notRecreated -join ', ')) -ForegroundColor Yellow
}
Write-Host ("    label      : docker inspect -> Config.Labels[org.opencontainers.image.revision] must equal the pin")
Write-Host ""
if ($WhatIfOnly) {
    Write-Step "-WhatIfOnly: nothing was built, started or recreated (only git ls-tree/rev-parse/status and docker compose config ran)."
    exit 0
}

# ---- helpers for the live phases ------------------------------------------
function Get-ServiceContainer {
    # Container id for a compose service in THIS project (empty before first up).
    param([string]$Svc)
    $id = (& docker compose @composeArgs ps -a -q $Svc 2>$null | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { return '' }
    return ($id -split "`r?`n" | Where-Object { $_ } | Select-Object -First 1)
}
function Get-ContainerFacts {
    # One `docker inspect` parsed as JSON - no Go template, so no PS 5.1 quoting
    # trap on templates that contain spaces or double quotes.
    param([string]$Id)
    if (-not $Id) { return $null }
    $j = (& docker inspect $Id 2>$null | Out-String)
    if ($LASTEXITCODE -ne 0 -or -not $j.Trim()) { return $null }
    try { $o = ($j | ConvertFrom-Json)[0] } catch { return $null }
    $health = 'none'; if ($o.State.Health) { $health = [string]$o.State.Health.Status }
    $label = ''
    if ($o.Config.Labels) { $lp = $o.Config.Labels.PSObject.Properties['org.opencontainers.image.revision']; if ($lp) { $label = [string]$lp.Value } }
    return @{ Name = ([string]$o.Name).TrimStart('/'); Status = [string]$o.State.Status; Health = $health
              RestartCount = [int]$o.RestartCount; StartedAt = [string]$o.State.StartedAt; Label = $label; Image = [string]$o.Config.Image }
}
function Watch-Service {
    # Watches one service's container until it is believed or has failed.
    # Believed = healthy (or running when there is no healthcheck) AND the whole
    # loop window elapsed with RestartCount still 0 and no restarting/exited
    # sighting. Fails immediately on the first bad sighting - a loop is
    # named within seconds, never after the bound.
    param([string]$Svc, $Bound)
    $deadline = $LoopWindowSeconds
    if ($Bound -and $Bound.Seconds -gt $deadline) { $deadline = $Bound.Seconds }
    $t0 = Get-Date; $lastLine = ''; $good = $false; $facts = $null; $reason = ''
    # RestartCount baseline: a fresh container starts at 0, but when compose
    # found nothing to recreate the old container (and its old count) survives,
    # so a loop is an INCREASE, not a non-zero.
    $baseRestarts = $null
    while ($true) {
        $elapsed = [int]((Get-Date) - $t0).TotalSeconds
        $id = Get-ServiceContainer $Svc
        $facts = Get-ContainerFacts $id
        if ($facts -and $null -eq $baseRestarts) { $baseRestarts = $facts.RestartCount }
        if (-not $facts) {
            $reason = "no container for service $Svc after $elapsed s"
        }
        elseif ($facts.Status -eq 'restarting' -or $facts.RestartCount -gt $baseRestarts) {
            $reason = "RESTART LOOP: $($facts.Name) status=$($facts.Status) restarts=$($facts.RestartCount) after $elapsed s"
            break
        }
        elseif ($facts.Status -in @('exited', 'dead', 'removing')) {
            $reason = "EXITED: $($facts.Name) status=$($facts.Status) after $elapsed s"
            break
        }
        elseif ($Bound -and $facts.Health -eq 'unhealthy') {
            $reason = "UNHEALTHY: $($facts.Name) healthcheck failed its retries after $elapsed s"
            break
        }
        else {
            $ready = if ($Bound) { $facts.Health -eq 'healthy' } else { $facts.Status -eq 'running' }
            $line = "  $Svc : status=$($facts.Status) health=$($facts.Health) restarts=$($facts.RestartCount) ($elapsed s)"
            if ($line -ne $lastLine) { Write-Host $line; $lastLine = $line }
            if ($ready -and $elapsed -ge $LoopWindowSeconds) { $good = $true; $reason = "ok after $elapsed s"; break }
            $reason = if ($Bound) { "NOT HEALTHY: $($facts.Name) health=$($facts.Health) status=$($facts.Status) after $elapsed s (bound $deadline s)" }
                      else { "NOT RUNNING: $($facts.Name) status=$($facts.Status) after $elapsed s" }
        }
        if ($elapsed -ge $deadline) { break }
        Start-Sleep -Seconds $PollSeconds
    }
    return @{ Service = $Svc; Good = $good; Reason = $reason; Facts = $facts }
}

# ---- 4. build ---------------------------------------------------------------
Write-Step "build: $composeCmd build --build-arg OB1_SHA=$pin $Service"
& docker compose @composeArgs build --build-arg "OB1_SHA=$pin" $Service
if ($LASTEXITCODE -ne 0) { Exit-With 4 "build of $Service failed (exit $LASTEXITCODE) - nothing was started; the running container is untouched." }

# ---- 5. up + watch the service ---------------------------------------------
$depBefore = @{}
foreach ($d in $dependents) { $f = Get-ContainerFacts (Get-ServiceContainer $d); $depBefore[$d] = if ($f) { $f.StartedAt } else { '(absent)' } }
$svcBeforeFacts = Get-ContainerFacts (Get-ServiceContainer $Service)
$svcBefore = if ($svcBeforeFacts) { $svcBeforeFacts.StartedAt } else { '(absent)' }

Write-Step "up: $composeCmd up -d $Service"
& docker compose @composeArgs up -d $Service
if ($LASTEXITCODE -ne 0) { Exit-With 5 "'up -d $Service' failed (exit $LASTEXITCODE)." }

Write-Step "watch: $Service (loop window $LoopWindowSeconds s)"
$results = @()
$svcResult = Watch-Service $Service $svcBound
$results += $svcResult

$failed = @()
if (-not $svcResult.Good) {
    $failed += $svcResult
    Write-Fail $svcResult.Reason
    if ($svcResult.Facts) {
        Write-Host "  --- docker logs --tail 20 $($svcResult.Facts.Name) ---" -ForegroundColor Yellow
        & docker logs --tail 20 $svcResult.Facts.Name 2>&1 | ForEach-Object { "  $_" }
    }
    Write-Warn "dependents NOT recreated because $Service failed its watch: $($dependents -join ', ')"
}
else {
    # ---- 6. recreate dependents ------------------------------------------
    foreach ($d in $dependents) {
        Write-Step "recreate: $composeCmd up -d --no-deps --force-recreate $d"
        & docker compose @composeArgs up -d --no-deps --force-recreate $d
        if ($LASTEXITCODE -ne 0) {
            $results += @{ Service = $d; Good = $false; Reason = "'up -d --no-deps --force-recreate $d' failed (exit $LASTEXITCODE)"; Facts = $null }
            continue
        }
        Write-Step "watch: $d"
        $r = Watch-Service $d $depBounds[$d]
        $results += $r
    }
    $failed = @($results | Where-Object { -not $_.Good })
    foreach ($r in $failed) {
        Write-Fail $r.Reason
        if ($r.Facts) {
            Write-Host "  --- docker logs --tail 20 $($r.Facts.Name) ---" -ForegroundColor Yellow
            & docker logs --tail 20 $r.Facts.Name 2>&1 | ForEach-Object { "  $_" }
        }
    }
}

# ---- 7. label vs pin + summary ---------------------------------------------
$svcFacts = Get-ContainerFacts (Get-ServiceContainer $Service)
$label = if ($svcFacts) { $svcFacts.Label } else { '' }
$labelVerdict = if ($label -eq $pin) { 'MATCH' } elseif (-not $label) { 'EMPTY (Dockerfile carries no ARG OB1_SHA + LABEL - follow-up)' } else { 'MISMATCH' }
$labelBad = ($labelVerdict -eq 'MISMATCH')

Write-Host ""
Write-Host "==> ob1-deploy summary (project $project)" -ForegroundColor Cyan
Write-Host ("    pin        : {0}" -f $pin)
if ($svcFacts) {
    $svcMoved = if ($svcBefore -ne $svcFacts.StartedAt) { 'recreated' } else { 'NOT recreated (compose found nothing changed)' }
    # ($svcBefore is captured before `up -d` so this line can say whether the container moved.)
    Write-Host ("    {0,-10} : container={1} image={2} status={3} health={4} restarts={5}  {6} ({7} -> {8})" -f 'service', $svcFacts.Name, $svcFacts.Image, $svcFacts.Status, $svcFacts.Health, $svcFacts.RestartCount, $svcMoved, $svcBefore, $svcFacts.StartedAt)
} else {
    Write-Host ("    service    : {0} - no container" -f $Service)
}
Write-Host ("    label      : {0} vs pin {1} -> {2}" -f $(if ($label) { $label } else { '(empty)' }), $pin, $labelVerdict) -ForegroundColor $(if ($labelBad) { 'Red' } elseif ($label) { 'Green' } else { 'Yellow' })
foreach ($d in $dependents) {
    $f = Get-ContainerFacts (Get-ServiceContainer $d)
    $after = if ($f) { $f.StartedAt } else { '(absent)' }
    $moved = if ($depBefore[$d] -ne $after) { 'StartedAt moved' } else { 'StartedAt UNCHANGED' }
    $st = if ($f) { "status=$($f.Status) health=$($f.Health) restarts=$($f.RestartCount)" } else { 'no container' }
    Write-Host ("    recreated  : {0}  {1} ({2} -> {3})  {4}" -f $d, $moved, $depBefore[$d], $after, $st)
}
if ($failed.Count -eq 0 -and -not $labelBad) {
    Write-Host ("    result     : OK - {0} deployed at {1}, watched {2} s clean" -f $Service, $pin.Substring(0,7), $LoopWindowSeconds) -ForegroundColor Green
    exit 0
}
$names = @($failed | ForEach-Object { if ($_.Facts) { $_.Facts.Name } else { $_.Service } })
if ($labelBad) { $names += "$Service(label)" }
Write-Host ("    result     : FAILED - {0}" -f ($names -join ', ')) -ForegroundColor Red
foreach ($r in $failed) { Write-Host ("                 {0}" -f $r.Reason) -ForegroundColor Red }
if ($labelBad) { Write-Host ("                 label {0} != pin {1}: the running image is not the one this deploy built" -f $label, $pin) -ForegroundColor Red }
exit 6
