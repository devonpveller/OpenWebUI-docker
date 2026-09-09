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
# SCOPE (operator, 2026-09-07): CONTAINERS and NETWORKS are reaped. IMAGES are counted in
# the report and never deleted - a deleted test image costs a rebuild nobody asked for.
# VOLUMES are neither reaped NOR reported: this script makes no `docker volume` call at all,
# and the anchor permits that ("reported at most, never deleted"). The header said "images
# and volumes are REPORTED", which overstated it - `grep -i volume reap.ps1` finds only
# comments. `docker volume prune` is a standing hazard in this stack, so the deliberate
# choice is to leave volumes entirely alone rather than to build a reporting path nobody
# asked for.
#
# WHICH KINDS ARE REAPABLE LIVES HERE IN CODE, not in harness.config.json, on purpose:
# widening it should show up in a diff and pass a reviewer, which a config key would not.
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
#      failure mode this repository keeps finding. `Get-DockerIds` returns `$null` on a
#      non-zero exit, and every caller treats `$null` as a hard stop.
#
#   3. A NAME CAN BE ANOTHER RESOURCE'S ID. Docker resolves a reference by id before it
#      consults the name index, so a container NAMED with a live container's full 64-char id
#      shadows it: `docker rm -f <that name>` deletes the live one. This script therefore
#      keys its whole inventory on the id and deletes by id. It deleted by name until a
#      tester demonstrated the shadowing against `openbrain-research` on 2026-09-07.
#
# Usage:
#   .\reap.ps1 -Owner wt-x                  # delete wt-x's containers + networks
#   .\reap.ps1 -Owner wt-x -WhatIfOnly      # ... report only
#   .\reap.ps1 -Report                      # full report: owned, orphaned, and out of scope
#   .\reap.ps1 -RemoveOrphan a,b            # delete named unlabelled leftovers, deliberately
#   .\reap.ps1 -RemoveOrphan "a,b"          # same - both forms work, see the note on the param
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
    # [string[]], not [string]. PowerShell parses a bare `-RemoveOrphan a,b` as an ARRAY,
    # which fails to bind to a [string] with ParameterArgumentTransformationError, removes
    # nothing, and leaves $LASTEXITCODE untouched - so the failure is silent. The usage line
    # above and the hint this script PRINTS both used that form, and a tester pasted the
    # printed hint on attempt 4 and it quietly did nothing. Accepting an array makes both
    # `a,b` and `"a,b"` work, which removes the trap rather than documenting it.
    [string[]]$RemoveOrphan = @(),
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
# Labels compose writes onto a CONTAINER when it starts one. A compose-built IMAGE carries
# {project, service, version} and NONE of these, which is the only thing separating a real
# compose-managed container from one merely run from such an image. Measured 2026-09-08:
# all 81 production containers on this host carry all three. Declared in CODE, like the
# reapable kinds, because widening or narrowing the compose guard must show up in a diff.
$ComposeRuntimeLabels = @(
    "com.docker.compose.config-hash",
    "com.docker.compose.container-number",
    "com.docker.compose.oneoff"
)

function Say([string]$Message, [string]$Colour = "Gray") {
    if (-not $Quiet) { Write-Host $Message -ForegroundColor $Colour }
}

function Invoke-DockerCapture {
    # Same PS5.1 trap git-io.ps1 documents for git: capturing a native command's output
    # under $ErrorActionPreference='Stop' turns ordinary stderr into a TERMINATING error,
    # which would kill this script at the docker line - before the error handling written
    # for that call can run. Flip it only around the native call and trust $LASTEXITCODE.
    #
    # AND CATCH CommandNotFoundException, because $ErrorActionPreference DOES NOT COVER IT.
    # A missing executable is a terminating error however the preference is set - measured
    # 2026-09-07: `$ErrorActionPreference='Continue'; & nosuchexe.exe` still throws. So on a
    # machine with no docker CLI on PATH this function threw straight past every exit-code
    # check in the file, past reap.ps1's own "docker unreachable" handling, and out through
    # `remove-worktree.ps1`, which then exited 1 and left the worktree, its branch and its
    # registry row in place - the exact outcome the anchor forbids, and there was no exit
    # code for the caller to ignore because the process never got that far. Found by a
    # tester on attempt 4. 127 is the shell convention for "command not found".
    param([string[]]$DockerArgs)
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& docker.exe @DockerArgs 2>&1) }
    catch [System.Management.Automation.CommandNotFoundException] {
        $global:LASTEXITCODE = 127
        return @("docker.exe is not on PATH: $($_.Exception.Message)")
    }
    finally { $ErrorActionPreference = $prevEap }
}

function Test-DockerReachable {
    # Asked in two parts because they fail differently: a MISSING CLI throws (handled
    # above), a present CLI with a dead daemon exits non-zero.
    if (-not (Get-Command docker.exe -ErrorAction SilentlyContinue)) { return $false }
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

function Get-DockerIds([string]$Kind, [string[]]$Filters) {
    # Returns the full IDs matching every filter, or $NULL if docker refused the question.
    # The null is load-bearing - see trap 2 in the header.
    #
    # IDS, NOT NAMES, AND THAT IS A SAFETY PROPERTY, not a style preference. Docker resolves
    # a reference by ID before it consults the name index, so a container whose NAME is a
    # live container's full 64-char ID shadows it. Measured 2026-09-07: a throwaway named
    # with `openbrain-research`'s id made `docker inspect <that string>` return
    # openbrain-research itself. This script used to delete by name, so a labelled decoy
    # named that way would have had `docker rm -f` aimed straight at a production container.
    # Found by a tester on attempt 2; everything below keys on the id.
    $dockerArgs = if ($Kind -eq "container") { @("ps", "-a") } else { @("network", "ls") }
    foreach ($f in $Filters) { $dockerArgs += "--filter"; $dockerArgs += $f }
    $dockerArgs += @("--no-trunc", "--format", "{{.ID}}")
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
    # id -> name/state/created. Keyed on the FULL id for the same reason everything else
    # here is: a name can be another resource's id. Separator is a pipe because no docker
    # id, name, state or timestamp can contain one, and unlike a tab it needs no quoting to
    # reach the exe.
    $dockerArgs = if ($Kind -eq "container") { @("ps", "-a", "--no-trunc", "--format", "{{.ID}}|{{.Names}}|{{.State}}|{{.CreatedAt}}") }
                  else { @("network", "ls", "--no-trunc", "--format", "{{.ID}}|{{.Name}}||{{.CreatedAt}}") }
    $out = Invoke-DockerCapture $dockerArgs
    $meta = @{}
    if ($LASTEXITCODE -ne 0) { return $meta }
    foreach ($line in $out) {
        $parts = ([string]$line).Split("|")
        if ($parts.Count -lt 4) { continue }
        $meta[$parts[0]] = [pscustomobject]@{ Name = $parts[1]; State = $parts[2]; Created = $parts[3] }
    }
    return $meta
}

function Get-OwnerValue([string]$Kind, [string]$Id) {
    return Get-OwnerValueFor $Kind $Id $OwnerLabel
}

function Get-OwnerValueFor([string]$Kind, [string]$Id, [string]$Key) {
    # The label VALUE, fetched as a whole JSON map so no quoted key ever reaches docker,
    # and addressed BY ID so it cannot be answered about a different resource.
    $dockerArgs = if ($Kind -eq "container") { @("inspect", "--type", "container", $Id, "--format", "{{json .Config.Labels}}") }
                  else { @("network", "inspect", $Id, "--format", "{{json .Labels}}") }
    $out = Invoke-DockerCapture $dockerArgs
    if ($LASTEXITCODE -ne 0) { return "" }
    $raw = ($out | Select-Object -First 1)
    if (-not $raw) { return "" }
    try { $map = [string]$raw | ConvertFrom-Json } catch { return "" }
    if (-not $map) { return "" }
    $prop = $map.PSObject.Properties[$Key]
    if (-not $prop) { return "" }
    return [string]$prop.Value
}

function Get-Inventory {
    # Every container and network, each classified into EXACTLY ONE bucket.
    #
    # `Bucket` is computed here, once, and every reader uses it. It used to be derived at
    # each display site from `OwnerId` and `Protection` independently, so a resource
    # carrying BOTH labels appeared under HARNESS-OWNED and again under PROTECTED, and the
    # printed counts summed to more than the number of resources (measured on attempt 2:
    # sum=112 over 111 resources). That blunts the whole point of printing the PROTECTED
    # count - it is meant to be a tripwire you can watch for movement, and a number that
    # double-counts cannot be one.
    #
    # Precedence is protection first, deliberately: a resource that is BOTH compose-managed
    # and harness-labelled is a protected resource that someone mislabelled, not an owned
    # one with a caveat.
    $rows = @()
    foreach ($kind in @("container", "network")) {
        $ids = Get-DockerIds $kind @()
        if ($null -eq $ids) { throw "docker could not list ${kind}s - refusing to report an empty sweep as a clean one" }
        $compose = Get-DockerIds $kind @("label=$ComposeLabel")
        if ($null -eq $compose) { throw "docker could not list compose-managed ${kind}s - refusing to reap without the protection set" }
        # THE COMPOSE RUNTIME KEYS. Compose writes these onto a CONTAINER when it starts it;
        # a compose-built IMAGE does not carry them. That difference is what separates a real
        # compose-managed container from one merely RUN FROM an image compose built - see
        # Test-ComposeInherited below for why that distinction had to be made.
        $composeRuntime = @()
        if ($kind -eq "container") {
            foreach ($key in $ComposeRuntimeLabels) {
                $hit = Get-DockerIds $kind @("label=$key")
                if ($null -eq $hit) { throw "docker could not list ${kind}s carrying $key - refusing to weaken the compose guard on an unanswered question" }
                $composeRuntime += $hit
            }
        }
        $labelled = Get-DockerIds $kind @("label=$OwnerLabel")
        if ($null -eq $labelled) { throw "docker could not list harness-labelled ${kind}s" }
        $meta = Get-DockerMeta $kind
        foreach ($id in $ids) {
            $m = $meta[$id]
            $name = $(if ($m) { $m.Name } else { $id })
            $isLabelled = ($labelled -contains $id)
            $protection = ""
            if ($compose -contains $id) {
                # A COMPOSE LABEL IS NOT ALWAYS COMPOSE MANAGEMENT. Labels are INHERITED from
                # the image, and several `:local` images in this stack were built BY compose,
                # so they carry {project, service, version} and stamp them on every container
                # run from them. `drill-mcp-door-not-superuser.ps1` runs
                # `openbrain-mcp-server:local`, so its own throwaway looked compose-managed and
                # the reaper refused to clean it up - found 2026-09-08 by the first real
                # consumer of the labelling convention, on the very drill the anchor names as
                # its demonstration case. No edit at the creation site can fix it: an empty
                # `--label com.docker.compose.project=` still matches a key-presence filter.
                #
                # THE EXCEPTION IS NARROW AND FAILS CLOSED. Three things must ALL hold before
                # a compose-labelled resource is reapable, and any doubt keeps it protected:
                #   1. it carries THIS harness's owner label - a production container never
                #      does, and that alone is the load-bearing gate;
                #   2. it carries NONE of the compose RUNTIME keys - measured 2026-09-08,
                #      all 81 production containers carry config-hash AND container-number AND
                #      oneoff, and a compose-built image carries none of the three;
                #   3. its IMAGE carries the same project value, so the label is demonstrably
                #      inherited rather than set by compose at run time.
                # Miss any one and it stays protected. If compose ever stops writing the
                # runtime keys, rule 1 still holds the line, because prod containers do not
                # carry the ownership label.
                if ($isLabelled -and ($kind -eq "container") -and ($composeRuntime -notcontains $id) -and (Test-ComposeInherited $id)) {
                    $protection = ""
                } else {
                    $protection = "compose-managed - deleting it is a deploy, not a cleanup"
                }
            }
            # -ccontains, case-SENSITIVE. Docker network names are case-sensitive, so a
            # network called `Bridge` is not the built-in `bridge`; the case-insensitive
            # form protected it anyway and gave "docker built-in network" as the reason,
            # which is a wrong answer even though it errs safe.
            elseif ($kind -eq "network" -and ($BuiltinNetworks -ccontains $name)) { $protection = "docker built-in network" }
            # LABELLED is key PRESENCE, from docker's own filter - not "the value is
            # non-empty". A resource carrying `ai-stack.harness.owner=` (empty value) is
            # labelled, and treating it as unlabelled made it an orphan that -RemoveOrphan
            # would delete, contradicting the documented contract. Its owner is unusable,
            # so it is reported as such rather than silently reaped.
            $owner = if ($isLabelled) { Get-OwnerValue $kind $id } else { "" }
            $bucket = if ($protection) { "protected" } elseif ($isLabelled) { "owned" } else { "orphan" }
            $rows += [pscustomobject]@{
                Kind       = $kind
                Id         = $id
                Name       = $name
                OwnerId    = $owner
                Labelled   = $isLabelled
                Protection = $protection
                Bucket     = $bucket
                State      = $(if ($m) { $m.State } else { "" })
                Created    = $(if ($m) { $m.Created } else { "" })
            }
        }
    }
    return $rows
}

function Test-ComposeInherited([string]$ContainerId) {
    # True when the container's compose project label is EXPLAINED BY ITS IMAGE - the image
    # carries the same key with the same value, so the container inherited it rather than
    # having it written by compose. Any doubt returns $false, which keeps the resource
    # protected: an unreadable image, an unparseable label map and a mismatched value are
    # all "no".
    $imgOut = Invoke-DockerCapture @("inspect", "--type", "container", $ContainerId, "--format", "{{.Image}}")
    if ($LASTEXITCODE -ne 0) { return $false }
    $imageId = ([string]($imgOut | Select-Object -First 1)).Trim()
    if (-not $imageId) { return $false }
    $ownVal = Get-OwnerValueFor "container" $ContainerId $ComposeLabel
    $labOut = Invoke-DockerCapture @("image", "inspect", $imageId, "--format", "{{json .Config.Labels}}")
    if ($LASTEXITCODE -ne 0) { return $false }
    $raw = ($labOut | Select-Object -First 1)
    if (-not $raw) { return $false }
    try { $map = [string]$raw | ConvertFrom-Json } catch { return $false }
    if (-not $map) { return $false }
    $prop = $map.PSObject.Properties[$ComposeLabel]
    if (-not $prop) { return $false }
    return ([string]$prop.Value -ceq $ownVal)
}

function Get-NetworkAttachedCount([string]$Id) {
    # A network with containers attached cannot be removed, and `docker network rm` failing
    # must not be reported as a successful reap. Returns -1 when the count is unknown, which
    # is treated as "in use" - refusing on unknown is the safe direction.
    $out = Invoke-DockerCapture @("network", "inspect", $Id, "--format", "{{len .Containers}}")
    if ($LASTEXITCODE -ne 0) { return -1 }
    $n = 0
    $first = ([string]($out | Select-Object -First 1)).Trim()
    if ([int]::TryParse($first, [ref]$n)) { return $n }
    return -1
}

function Test-ResourceGone($Row) {
    if ($Row.Kind -eq "container") { $null = Invoke-DockerCapture @("inspect", "--type", "container", $Row.Id) }
    else { $null = Invoke-DockerCapture @("network", "inspect", $Row.Id) }
    return ($LASTEXITCODE -ne 0)
}

function Remove-Resource($Row) {
    # Returns "" on success or the failure text. Never throws: one stuck resource must not
    # abandon the rest of the sweep.
    # BY ID. See Get-DockerIds for why deleting by name was a production-deletion path.
    if ($Row.Kind -eq "container") { $out = Invoke-DockerCapture @("rm", "-f", $Row.Id) }
    else { $out = Invoke-DockerCapture @("network", "rm", $Row.Id) }
    if ($LASTEXITCODE -ne 0) { return (($out -join " ").Trim()) }
    # EXIT 0 IS NOT PROOF IT WENT. Measured 2026-09-07 on this daemon:
    # `docker container rm --force <id that does not exist>` prints
    # "Error response from daemon: No such container: ..." to stderr AND EXITS 0. Trusting
    # the exit code alone made this function report "removed" for a resource it had not
    # touched - and the report line is the only evidence anyone reads afterwards. Found by a
    # tester on attempt 3, where one ambiguous name printed "removed" twice for one delete.
    if (-not (Test-ResourceGone $Row)) {
        return ("docker exited 0 but the {0} is still there: {1}" -f $Row.Kind, (($out -join " ").Trim()))
    }
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
        $n = Get-NetworkAttachedCount $Row.Id
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
# THE MODES ARE EXCLUSIVE, and saying so out loud closes a silent half-job. `-RemoveOrphan
# a b` (spaces, not commas) binds `a` to -RemoveOrphan and `b` POSITIONALLY to -Owner, so
# the run removed one orphan, reaped a different owner, and exited 0 - reporting success for
# a command that did something other than what was typed. Found by a tester on attempt 5.
if (($Owner -and $RemoveOrphan) -or ($Owner -and $Report) -or ($RemoveOrphan -and $Report)) {
    Write-Host "ERROR: -Owner, -Report and -RemoveOrphan are separate modes - pass exactly one." -ForegroundColor Red
    Write-Host ("       Got: {0}{1}{2}" -f $(if ($Owner) { "-Owner $Owner  " } else { "" }),
                                          $(if ($Report) { "-Report  " } else { "" }),
                                          $(if ($RemoveOrphan) { "-RemoveOrphan $($RemoveOrphan -join ',')" } else { "" })) -ForegroundColor Red
    Write-Host  "       A space-separated list binds its second name to -Owner; use commas: -RemoveOrphan a,b" -ForegroundColor Red
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
    # Each element may itself be a comma list, so `a,b` (two elements) and `"a,b"` (one
    # element containing a comma) both reduce to the same set of names.
    $wanted = @($RemoveOrphan | ForEach-Object { $_.Split(",") } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $failed = 0
    foreach ($name in $wanted) {
        # -ceq, CASE-SENSITIVE, and every match considered rather than the first.
        #
        # POWERSHELL COMPARES STRINGS CASE-INSENSITIVELY; DOCKER NAMES ARE CASE-SENSITIVE.
        # Both `atk-case` and `ATK-CASE` can exist at once (measured 2026-09-07), and `-eq`
        # matched whichever `docker ps -a` happened to list first. So this resolved to a row
        # the caller had not named, and BOTH guards below were then evaluated against the
        # wrong resource: a tester deleted an unlabelled `T12-OWNED` while naming the
        # labelled `t12-owned`, exit 0, no refusal - and in the other creation order got a
        # refusal citing an owner belonging to a different container. Found on attempt 3.
        #
        # A name can also be ambiguous ACROSS KINDS - a container and a network may share
        # one - so an ambiguous name is refused and its ids printed rather than resolved by
        # list order. An id may be given instead of a name, which is never ambiguous.
        $matched = @($resources | Where-Object { $_.Name -ceq $name -or $_.Id -ceq $name -or $_.Id.StartsWith($name) })
        if (-not $matched.Count) { Say ("    not found: {0}" -f $name) Yellow; continue }
        if ($matched.Count -gt 1) {
            Write-Host ("    AMBIGUOUS {0} - {1} resources share that name. Name one of these ids instead:" -f $name, $matched.Count) -ForegroundColor Red
            foreach ($m in $matched) { Write-Host ("        {0,-9} {1}" -f $m.Kind, $m.Id) -ForegroundColor Red }
            $failed++
            continue
        }
        $row = $matched[0]
        # AN ORPHAN IS UNLABELLED. This flag is documented, here and in README.md, as the way
        # to remove leftovers that carry no owner - and it used to remove ANY named resource
        # the compose guard allowed, including a live fixture belonging to another worktree.
        # A tester found that on 2026-09-07: the code and the documentation disagreed about
        # what the flag does, and the code was the more dangerous of the two. Refusing here
        # costs a labelled resource's owner nothing (they have -Owner) and stops one agent
        # deleting another's running test by name.
        if ($row.Labelled) {
            # Two different refusals, because they need two different next steps. A resource
            # with a real owner is reaped with -Owner; one carrying the key with an EMPTY
            # value is reachable by no -Owner at all, so telling the reader to run
            # `-Owner <empty owner - remove it by hand>` is a command that cannot be typed.
            if ($row.OwnerId) {
                Write-Host ("    REFUSED {0} - not an orphan: it belongs to '{1}'. Reap it with:  .\reap.ps1 -Owner {1}" -f $name, $row.OwnerId) -ForegroundColor Red
            } else {
                Write-Host ("    REFUSED {0} - labelled {1} with an EMPTY value, so no -Owner reaches it." -f $name, $OwnerLabel) -ForegroundColor Red
                Write-Host ("              Remove it by hand:  docker {0} rm {1}{2}" -f $(if ($row.Kind -eq "container") { "container" } else { "network" }), $(if ($row.Kind -eq "container") { "-f " } else { "" }), $row.Id) -ForegroundColor Red
            }
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
    # Selected on LABELLED, not on Bucket. A resource that is compose-managed AND carries
    # this owner's label must still be MATCHED here so that Remove-OneRow can refuse it out
    # loud - somebody having written the harness label into a plane's compose file is exactly
    # the thing worth shouting about, and selecting on Bucket -eq "owned" would have skipped
    # it silently and exited 0. The report's buckets partition for COUNTING; this is
    # selection for ACTING, and they are different questions.
    # -ccontains, CASE-SENSITIVE: `-Owner T4C` used to reap a container labelled `t4c`,
    # which contradicts "deletes exactly the resources labelled with the owner you asked
    # for". Same PowerShell-vs-docker mismatch the -RemoveOrphan lookup had.
    $mine = @($resources | Where-Object { $_.Labelled -and $_.OwnerId -and ($aliases -ccontains $_.OwnerId) })
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
$owned = @($resources | Where-Object { $_.Bucket -eq "owned" })
$orphans = @($resources | Where-Object { $_.Bucket -eq "orphan" })
$protected = @($resources | Where-Object { $_.Bucket -eq "protected" })

Write-Host ("HARNESS-OWNED - {0} (reaped when their worktree is removed)" -f $owned.Count) -ForegroundColor Cyan
if (-not $owned.Count) { Say "    (none)" }
foreach ($row in $owned) {
    $who = if ($row.OwnerId) { $row.OwnerId } else { "(empty - unreapable, remove by hand)" }
    Show-Row $row ("owner={0}  {1}" -f $who, $row.Created) Gray
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
    # A name that is not unique across the inventory is printed as an ID instead. Names can
    # collide between a container and a network, and the hint used to emit such a name TWICE
    # - and running the hint verbatim then reported "removed" twice for one delete. The hint
    # has to be a command that does the right thing when pasted, or it should not be printed.
    $hint = foreach ($row in $orphans) {
        $clash = @($resources | Where-Object { $_.Name -ceq $row.Name })
        if ($clash.Count -gt 1) { $row.Id } else { $row.Name }
    }
    # Quoted, and the parameter also accepts an array, so this line survives being pasted
    # either way. It used to print a bare comma list that refused to bind and removed
    # nothing - a hint that exists to be pasted has to work when pasted.
    Write-Host ("  Remove them deliberately:  .\reap.ps1 -RemoveOrphan `"{0}`"" -f ($hint -join ",")) -ForegroundColor Gray
}

# The protected count is printed rather than the list: it is the number this script REFUSED
# to consider, and seeing it move is how an operator notices the guard being eroded. That
# only works if the three buckets PARTITION the inventory, so the total is asserted here
# rather than assumed - they were derived independently once, and a resource carrying both
# labels was counted twice (sum 112 over 111 resources, found by a tester 2026-09-07).
Write-Host ""
Write-Host ("PROTECTED - {0} compose-managed or built-in resource(s), never candidates" -f $protected.Count) -ForegroundColor Green
$sum = $owned.Count + $orphans.Count + $protected.Count
if ($sum -ne $resources.Count) {
    Write-Host ("  BUG: {0} + {1} + {2} = {3}, but the daemon has {4} resource(s). The buckets do not partition." `
        -f $owned.Count, $orphans.Count, $protected.Count, $sum, $resources.Count) -ForegroundColor Red
}
# A compose-managed resource that ALSO carries the ownership label is not a routine sight -
# it means somebody wrote the harness label into a plane's compose file. It is protected
# either way, and it is worth naming rather than folding into a count.
$mislabelled = @($protected | Where-Object { $_.Labelled })
if ($mislabelled.Count) {
    Write-Host ("  {0} of them ALSO carry {1} - protected anyway, but somebody labelled a compose resource:" -f $mislabelled.Count, $OwnerLabel) -ForegroundColor Yellow
    # "PROTECTED" goes on the LINE, not only in the heading above it. A reader - or a test -
    # scanning for compose-managed names offered up for deletion looks line by line, and a
    # name on a bare line reads as a candidate however reassuring the section header is.
    foreach ($row in $mislabelled) { Show-Row $row ("owner={0}  PROTECTED: {1}" -f $row.OwnerId, $row.Protection) Yellow }
}

# IMAGES: counted, never touched. Reporting them is how the operator learns the disk cost
# without this script acquiring the authority to delete them. Volumes are NOT counted here -
# see the scope note in the header for why they are left alone entirely.
$imgOut = Invoke-DockerCapture @("images", "--format", "{{.Repository}}:{{.Tag}}")
$testTag = [string](Get-HarnessSetting "worktree.test_image_tag_prefix" "wt-")
$testImgs = @($imgOut | Where-Object { $_ -match [regex]::Escape(":$testTag") })
$dangling = @(Invoke-DockerCapture @("images", "-f", "dangling=true", "-q"))
Write-Host ""
Write-Host "OUT OF SCOPE (reported only - this script never deletes these)" -ForegroundColor Gray
Say ("    {0} test image tag(s) matching ':{1}*', {2} dangling image(s)" -f $testImgs.Count, $testTag, $dangling.Count)
Say "    Reclaim them by hand when you want the disk back; a deleted image costs a rebuild."
exit 0
