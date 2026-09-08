# reap.ps1 - remove the docker resources a worker created to prove out its work.
#
# THE PROBLEM IT SOLVES. Test containers and test networks outlive the work they were
# built for. On 2026-09-07 the daemon held ten dead containers and two orphan networks,
# every one of them from a run whose own cleanup existed and never ran: the drills
# (scripts/checks/drill-personal-plane-exclusion.ps1, prove-agent-memory-rls.ps1) tear
# down in a PowerShell `finally`, and a `finally` does not run when the script is killed -
# Ctrl+C, a crashed turn, or this workspace's known trap of a background task being killed
# when a turn ends. Per-script cleanup cannot be the mechanism, because the case that
# leaks is exactly the case where the script does not reach its own last line.
#
# SO OWNERSHIP IS RECORDED AT CREATION, NOT AT CLEANUP. A worker labels what it creates:
#
#   docker run  --label ai-stack.harness.owner=<your-wt-id> ...
#   docker network create --label ai-stack.harness.owner=<your-wt-id> ...
#
# and the label survives everything - the script dying, the agent's session ending, the
# host rebooting. Cleanup then needs no cooperation from the thing being cleaned up.
# `remove-worktree.ps1` calls this when the worktree is retired, which is the moment the
# operator named: the work is merged and cleaned, so what was built to prove it is done.
#
# WHY A LABEL AND NOT A NAME PREFIX. The existing drills DO use name prefixes
# (`pp-drill-<run>-*`, `u5rls-*-<run>`), and that is precisely why the leftovers sat there:
# a reaper would have to know every drill's naming scheme, and would silently miss the next
# one. A label is one convention, declared once, that a script written next month gets for
# free.
#
# WHAT IT WILL NOT TOUCH, and why that is checked POSITIVELY. A compose-managed resource is
# never reaped: containers carrying `com.docker.compose.project`, networks carrying it (that
# is every plane network including the `ai-stack_*` anchors), and docker's built-in
# bridge/host/none. The lazy argument is that a prod container "would not have our label
# anyway" - but a plane's compose file can carry any label an agent writes into it, and one
# stray `labels:` block under a service would otherwise point this script at prod. The guard
# is the mechanism, not a belt over braces.
#
# SCOPE (operator, 2026-09-07): CONTAINERS and NETWORKS are reaped. Images and volumes are
# REPORTED and never deleted - `docker volume prune` is a standing hazard in this stack, and
# a deleted test image costs a rebuild nobody asked for. That split lives here in code
# rather than in harness.config.json on purpose: widening it should show up in a diff and
# pass a reviewer, which a config key would not.
#
# ORPHANS - resources with NO compose project and NO owner label - are reported with their
# age and never auto-deleted. Something unlabelled may be an operator's hand-run sidecar.
# Removing one takes `-RemoveOrphan <name>`, which names it out loud.
#
# TWO NATIVE-COMMAND TRAPS THIS FILE IS BUILT AROUND, both hit while writing it:
#
#   1. A GO TEMPLATE WITH A QUOTED KEY DOES NOT SURVIVE PS5.1. PowerShell strips the inner
#      quotes when it hands an argument to a native .exe, so
#      `--format '{{index .Labels "com.docker.compose.project"}}'` reaches docker as
#      `{{index .Labels com.docker.compose.project}}` and dies with `function "com" not
#      defined`. Every template here is therefore QUOTE-FREE: label questions are asked
#      with docker's own `--filter label=...` (no template at all), and where a label VALUE
#      is needed the whole map comes back via `{{json .Labels}}` and is parsed in
#      PowerShell.
#
#   2. A FAILED QUERY MUST NOT LOOK LIKE AN EMPTY ONE. The first cut of this script returned
#      `@()` when docker errored, so the broken template above produced a clean, confident,
#      completely empty report - a check that passes while checking nothing, which is the
#      failure mode this repository keeps finding. `Get-DockerNames` returns `$null` on a
#      non-zero exit, and every caller treats `$null` as a hard stop.
#
# Usage:
#   .\reap.ps1 -Owner wt-x                  # delete wt-x's containers + networks
#   .\reap.ps1 -Owner wt-x -WhatIfOnly      # ... report only
#   .\reap.ps1 -Report                      # full report: owned, orphaned, and out of scope
#   .\reap.ps1 -RemoveOrphan a,b            # delete named unlabelled leftovers, deliberately
#
# Exit codes: 0 ok | 1 usage/config/partial failure | 2 harness off | 4 docker unreachable
#   Callers that must not fail because of docker (remove-worktree.ps1) ignore this exit
#   code entirely. It is honest here so that a tester running the script directly sees a
#   daemon that was down instead of a silent "nothing to reap".

[CmdletBinding()]
param(
    [string]$Owner = "",
    [switch]$Report,
    [switch]$WhatIfOnly,
    [string]$RemoveOrphan = "",
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

$offReason = Get-HarnessDisabledReason
if ($offReason) { Write-Host "REFUSED: $offReason" -ForegroundColor Yellow; exit 2 }

$OwnerLabel = [string](Get-HarnessSetting "reap.owner_label" "ai-stack.harness.owner")
$DirPrefix = [string](Get-HarnessSetting "worktree.dir_prefix" "wt-")
$ComposeLabel = "com.docker.compose.project"
# Docker's built-ins. They carry no compose project label, so without naming them here a
# network called `bridge` would be classified as an orphan and offered up for removal.
$BuiltinNetworks = @("bridge", "host", "none")

function Say([string]$Message, [string]$Colour = "Gray") {
    if (-not $Quiet) { Write-Host $Message -ForegroundColor $Colour }
}

function Invoke-DockerCapture {
    # Same PS5.1 trap git-io.ps1 documents for git: capturing a native command's output
    # under $ErrorActionPreference='Stop' turns ordinary stderr into a TERMINATING error,
    # which would kill this script at the docker line - before the error handling written
    # for that call can run. Flip it only around the native call and trust $LASTEXITCODE.
    param([string[]]$DockerArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& docker.exe @DockerArgs 2>&1) } finally { $ErrorActionPreference = $prevEap }
}

function Test-DockerReachable {
    $null = Invoke-DockerCapture @("version", "--format", "{{.Server.Version}}")
    return ($LASTEXITCODE -eq 0)
}

function Get-OwnerAliases([string]$Id) {
    # An agent may label with the bare item id (`reap`) or the worktree dir name (`wt-reap`);
    # the README uses both spellings in different places. Matching only one would silently
    # reap nothing and report success - the failure mode this whole script exists to end.
    $trimmed = $Id.Trim()
    if (-not $trimmed) { return , @() }
    $bare = if ($trimmed.StartsWith($DirPrefix)) { $trimmed.Substring($DirPrefix.Length) } else { $trimmed }
    return , @(@($bare, "$DirPrefix$bare") | Select-Object -Unique)
}

function Get-DockerNames([string]$Kind, [string[]]$Filters) {
    # Returns the names matching every filter, or $NULL if docker refused the question.
    # The null is load-bearing - see trap 2 in the header.
    $dockerArgs = if ($Kind -eq "container") { @("ps", "-a") } else { @("network", "ls") }
    foreach ($f in $Filters) { $dockerArgs += "--filter"; $dockerArgs += $f }
    $dockerArgs += "--format"
    $dockerArgs += $(if ($Kind -eq "container") { "{{.Names}}" } else { "{{.Name}}" })
    $out = Invoke-DockerCapture $dockerArgs
    if ($LASTEXITCODE -ne 0) { return $null }
    # `,@(...)` - the leading comma is load-bearing, and this file learned it the hard way
    # twice over. Returning a bare `@()` from a PowerShell function emits NOTHING, so the
    # caller receives $null - which is exactly the sentinel this function uses for "docker
    # refused the question". A filter that legitimately matched nothing would have been
    # reported as a daemon failure. Same rule config.ps1 records for ConvertTo-HashtableDeep.
    return , @($out | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
}

function Get-DockerMeta([string]$Kind) {
    # name -> "state|created". Separator is a pipe because no docker name, state or
    # timestamp can contain one, and unlike a tab it needs no quoting to reach the exe.
    $dockerArgs = if ($Kind -eq "container") { @("ps", "-a", "--format", "{{.Names}}|{{.State}}|{{.CreatedAt}}") }
                  else { @("network", "ls", "--format", "{{.Name}}||{{.CreatedAt}}") }
    $out = Invoke-DockerCapture $dockerArgs
    $meta = @{}
    if ($LASTEXITCODE -ne 0) { return $meta }
    foreach ($line in $out) {
        $parts = ([string]$line).Split("|")
        if ($parts.Count -lt 3) { continue }
        $meta[$parts[0]] = [pscustomobject]@{ State = $parts[1]; Created = $parts[2] }
    }
    return $meta
}

function Get-OwnerValue([string]$Kind, [string]$Name) {
    # The label VALUE, fetched as a whole JSON map so no quoted key ever reaches docker.
    $dockerArgs = if ($Kind -eq "container") { @("inspect", "--type", "container", $Name, "--format", "{{json .Config.Labels}}") }
                  else { @("network", "inspect", $Name, "--format", "{{json .Labels}}") }
    $out = Invoke-DockerCapture $dockerArgs
    if ($LASTEXITCODE -ne 0) { return "" }
    $raw = ($out | Select-Object -First 1)
    if (-not $raw) { return "" }
    try { $map = [string]$raw | ConvertFrom-Json } catch { return "" }
    if (-not $map) { return "" }
    $prop = $map.PSObject.Properties[$OwnerLabel]
    if (-not $prop) { return "" }
    return [string]$prop.Value
}

function Get-Inventory {
    # Every container and network, each classified exactly once: compose-managed (never
    # touched), harness-owned (reapable by its owner), built-in, or orphan.
    $rows = @()
    foreach ($kind in @("container", "network")) {
        $names = Get-DockerNames $kind @()
        if ($null -eq $names) { throw "docker could not list ${kind}s - refusing to report an empty sweep as a clean one" }
        $compose = Get-DockerNames $kind @("label=$ComposeLabel")
        if ($null -eq $compose) { throw "docker could not list compose-managed ${kind}s - refusing to reap without the protection set" }
        $labelled = Get-DockerNames $kind @("label=$OwnerLabel")
        if ($null -eq $labelled) { throw "docker could not list harness-labelled ${kind}s" }
        $meta = Get-DockerMeta $kind
        foreach ($name in $names) {
            $protection = ""
            if ($compose -contains $name) { $protection = "compose-managed - deleting it is a deploy, not a cleanup" }
            elseif ($kind -eq "network" -and ($BuiltinNetworks -contains $name)) { $protection = "docker built-in network" }
            $owner = if ($labelled -contains $name) { Get-OwnerValue $kind $name } else { "" }
            $m = $meta[$name]
            $rows += [pscustomobject]@{
                Kind       = $kind
                Name       = $name
                OwnerId    = $owner
                Protection = $protection
                State      = $(if ($m) { $m.State } else { "" })
                Created    = $(if ($m) { $m.Created } else { "" })
            }
        }
    }
    return $rows
}

function Get-NetworkAttachedCount([string]$Name) {
    # A network with containers attached cannot be removed, and `docker network rm` failing
    # must not be reported as a successful reap. Returns -1 when the count is unknown, which
    # is treated as "in use" - refusing on unknown is the safe direction.
    $out = Invoke-DockerCapture @("network", "inspect", $Name, "--format", "{{len .Containers}}")
    if ($LASTEXITCODE -ne 0) { return -1 }
    $n = 0
    $first = ([string]($out | Select-Object -First 1)).Trim()
    if ([int]::TryParse($first, [ref]$n)) { return $n }
    return -1
}

function Remove-Resource($Row) {
    # Returns "" on success or the failure text. Never throws: one stuck resource must not
    # abandon the rest of the sweep.
    if ($Row.Kind -eq "container") { $out = Invoke-DockerCapture @("rm", "-f", $Row.Name) }
    else { $out = Invoke-DockerCapture @("network", "rm", $Row.Name) }
    if ($LASTEXITCODE -ne 0) { return (($out -join " ").Trim()) }
    return ""
}

function Show-Row($Row, [string]$Suffix, [string]$Colour) {
    Say ("    {0,-9} {1,-42} {2}" -f $Row.Kind, $Row.Name, $Suffix) $Colour
}

function Remove-OneRow($Row, [string]$Indent) {
    # The single delete path, shared by -Owner and -RemoveOrphan so the two can never
    # disagree about what is protected or what counts as success. Returns $true if the
    # resource is gone (or would be, under -WhatIfOnly).
    if ($Row.Protection) {
        # A harness label on a compose-managed resource is worth shouting about: somebody
        # wrote the ownership label into a plane's compose file.
        Write-Host ("{0}REFUSED  {1,-9} {2} - {3}" -f $Indent, $Row.Kind, $Row.Name, $Row.Protection) -ForegroundColor Red
        return $false
    }
    if ($Row.Kind -eq "network") {
        $n = Get-NetworkAttachedCount $Row.Name
        if ($n -ne 0) {
            Show-Row $Row ("IN USE - {0} container(s) attached; NOT removed" -f $(if ($n -lt 0) { "unknown" } else { $n })) Yellow
            return $false
        }
    }
    if ($WhatIfOnly) { Show-Row $Row "would remove" Cyan; return $true }
    $err = Remove-Resource $Row
    if ($err) { Show-Row $Row ("FAILED - " + $err) Red; return $false }
    Show-Row $Row "removed" Green
    return $true
}

# --- preconditions ----------------------------------------------------------------
if (-not $Owner -and -not $Report -and -not $RemoveOrphan) {
    Write-Host "ERROR: pass -Owner <id>, -Report, or -RemoveOrphan <names>" -ForegroundColor Red
    exit 1
}
if (-not (Test-DockerReachable)) {
    Say "NOTE: the docker daemon is not reachable - nothing was reaped and nothing was lost." Yellow
    Say "      Re-run reap.ps1 with the same arguments once it is up." Yellow
    exit 4
}

try { $resources = Get-Inventory }
catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
    exit 4
}

# --- mode: remove named orphans ---------------------------------------------------
if ($RemoveOrphan) {
    $wanted = @($RemoveOrphan.Split(",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $failed = 0
    foreach ($name in $wanted) {
        $row = @($resources | Where-Object { $_.Name -eq $name }) | Select-Object -First 1
        if (-not $row) { Say ("    not found: {0}" -f $name) Yellow; continue }
        # AN ORPHAN IS UNLABELLED. This flag is documented, here and in README.md, as the way
        # to remove leftovers that carry no owner - and it used to remove ANY named resource
        # the compose guard allowed, including a live fixture belonging to another worktree.
        # A tester found that on 2026-09-07: the code and the documentation disagreed about
        # what the flag does, and the code was the more dangerous of the two. Refusing here
        # costs a labelled resource's owner nothing (they have -Owner) and stops one agent
        # deleting another's running test by name.
        if ($row.OwnerId) {
            Write-Host ("    REFUSED {0} - not an orphan: it belongs to '{1}'. Reap it with -Owner {1}" -f $name, $row.OwnerId) -ForegroundColor Red
            $failed++
            continue
        }
        if (-not (Remove-OneRow $row "    ")) { $failed++ }
    }
    if ($failed) { exit 1 }
    exit 0
}

# --- mode: reap one owner ---------------------------------------------------------
if ($Owner) {
    $aliases = Get-OwnerAliases $Owner
    $mine = @($resources | Where-Object { $_.OwnerId -and ($aliases -contains $_.OwnerId) })
    if (-not $mine.Count) {
        Say ("Reap {0}: nothing labelled {1}={2}." -f $Owner, $OwnerLabel, ($aliases -join "|")) Green
        exit 0
    }
    Say ("Reap {0} - {1} resource(s) labelled {2}" -f $Owner, $mine.Count, $OwnerLabel) Cyan
    $failed = 0
    # Containers first: a network cannot be removed while one of its containers is up, and
    # the containers on a test network are normally that same owner's.
    $ordered = @($mine | Where-Object { $_.Kind -eq "container" }) + @($mine | Where-Object { $_.Kind -eq "network" })
    foreach ($row in $ordered) {
        if (-not (Remove-OneRow $row "    ")) { $failed++ }
    }
    if ($failed) {
        Say ("  {0} resource(s) could not be reaped - listed above." -f $failed) Yellow
        exit 1
    }
    exit 0
}

# --- mode: report -----------------------------------------------------------------
$owned = @($resources | Where-Object { $_.OwnerId })
$orphans = @($resources | Where-Object { -not $_.OwnerId -and -not $_.Protection })
$protected = @($resources | Where-Object { $_.Protection })

Write-Host ("HARNESS-OWNED - {0} (reaped when their worktree is removed)" -f $owned.Count) -ForegroundColor Cyan
if (-not $owned.Count) { Say "    (none)" }
foreach ($row in $owned) {
    if ($row.Protection) { Show-Row $row ("owner={0}  PROTECTED: {1}" -f $row.OwnerId, $row.Protection) Red }
    else { Show-Row $row ("owner={0}  {1}" -f $row.OwnerId, $row.Created) Gray }
}

Write-Host ""
Write-Host ("ORPHANS - {0} (unlabelled, no compose project - NEVER auto-deleted)" -f $orphans.Count) -ForegroundColor Yellow
if (-not $orphans.Count) { Say "    (none)" }
foreach ($row in $orphans) {
    # `docker ps` prints a local timestamp with a numeric offset ("2026-09-02 06:33:11 -0400
    # EDT") while `docker network ls` prints UTC ("2026-09-08 00:17:14.206 +0000 UTC").
    # Stripping the trailing zone NAME and letting DateTimeOffset read the numeric OFFSET
    # handles both; the first cut dropped the offset too and read every network's UTC stamp
    # as local time, overstating network ages by the UTC offset. Cosmetic - nothing acts on
    # this number - but a displayed figure that is wrong is still wrong.
    $age = "age unknown"
    try {
        $stamp = ($row.Created -replace "\s+[A-Za-z]{2,5}$", "").Trim()
        $created = [datetimeoffset]::Parse($stamp)
        $age = "{0:N0}d old" -f ([datetimeoffset]::Now - $created).TotalDays
    } catch { $age = "age unknown" }
    $state = if ($row.State) { $row.State } else { "-" }
    Show-Row $row ("{0,-8} {1}" -f $state, $age) Yellow
}
if ($orphans.Count) {
    Write-Host ""
    Write-Host ("  Remove them deliberately:  .\reap.ps1 -RemoveOrphan {0}" -f (($orphans | ForEach-Object { $_.Name }) -join ",")) -ForegroundColor Gray
}

# The protected count is printed rather than the list: it is the number this script REFUSED
# to consider, and seeing it move is how an operator notices the guard being eroded.
Write-Host ""
Write-Host ("PROTECTED - {0} compose-managed or built-in resource(s), never candidates" -f $protected.Count) -ForegroundColor Green

# Images and volumes: counted, never touched. Reporting them is how the operator learns the
# disk cost without this script acquiring the authority to delete either.
$imgOut = Invoke-DockerCapture @("images", "--format", "{{.Repository}}:{{.Tag}}")
$testTag = [string](Get-HarnessSetting "worktree.test_image_tag_prefix" "wt-")
$testImgs = @($imgOut | Where-Object { $_ -match [regex]::Escape(":$testTag") })
$dangling = @(Invoke-DockerCapture @("images", "-f", "dangling=true", "-q"))
Write-Host ""
Write-Host "OUT OF SCOPE (reported only - this script never deletes these)" -ForegroundColor Gray
Say ("    {0} test image tag(s) matching ':{1}*', {2} dangling image(s)" -f $testImgs.Count, $testTag, $dangling.Count)
Say "    Reclaim them by hand when you want the disk back; a deleted image costs a rebuild."
exit 0
