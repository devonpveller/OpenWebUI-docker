# verify-reap.ps1 - prove reap.ps1 removes exactly what its owner made, and nothing else.
#
# WHY THIS EXISTS AS A SCRIPT. A reaper is a delete button pointed at a live daemon, and
# the only convincing evidence that it is aimed correctly is a run where the wrong thing
# was RIGHT THERE and survived. So every case below builds the thing that must not be
# deleted, next to the thing that must, and checks both afterwards.
#
# IT NEVER TOUCHES A PLANE. Every resource it creates is a stopped `alpine` container or an
# empty bridge network with a `reapv-` name, on no plane network, using no prod
# container_name - the pointwise sandbox shape MERGE-PROTOCOL.md permits without a lease.
# Read-only questions about the live stack (does any compose container appear as a
# candidate?) are answered by looking, never by changing.
#
# ITS OWN RESOURCES CARRY THE OWNERSHIP LABEL, which is the point of the design: if this
# script is killed halfway - the exact accident that left ten containers on the daemon and
# caused this whole item - the leftovers are labelled, and `reap.ps1 -Owner verify-reap`
# collects them. A verifier that could leak would be arguing against its own subject.
#
# Usage:
#   .\verify-reap.ps1                      (exit 0 = every case passed, 1 = something failed)
#   .\verify-reap.ps1 -Script <path>       drive a DIFFERENT copy of reap.ps1
#
# `-Script` exists for the same reason verify-queue-defects.ps1 has it: a verifier is only
# worth its green run if you have also seen it go RED. Point it at a copy with the compose
# guard deleted and CASE 3 must fail; point it at one whose Get-OwnerAliases returns only
# the bare id and CASE 2 must fail. A verifier nobody has watched fail is an assertion, not
# evidence.

[CmdletBinding()]
param(
    [switch]$KeepOnFailure,
    [string]$Script = ""
)

$ErrorActionPreference = "Stop"
$Reap = if ($Script) { (Resolve-Path $Script).Path } else { Join-Path $PSScriptRoot "reap.ps1" }
if (-not (Test-Path $Reap)) { throw "no reap.ps1 at $Reap" }
$OwnerA = "verify-reap"
$OwnerB = "verify-reap-other"
$Image = "alpine:3.21"

$script:Pass = 0
$script:Fail = 0
$script:Made = @()   # @{ Kind; Name } - torn down in the finally, label or no label

function Check([string]$What, [bool]$Ok, [string]$Detail) {
    if ($Ok) { Write-Host ("  PASS  " + $What) -ForegroundColor Green; $script:Pass++ }
    else {
        Write-Host ("  FAIL  " + $What) -ForegroundColor Red
        Write-Host ("        " + $Detail) -ForegroundColor Red
        $script:Fail++
    }
}

function Docker {
    # Same PS5.1 rule reap.ps1 and git-io.ps1 record: capturing a native command's output
    # under 'Stop' makes its stderr a terminating error.
    param([string[]]$DockerArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& docker.exe @DockerArgs 2>&1) } finally { $ErrorActionPreference = $prev }
}

function Test-Exists([string]$Kind, [string]$Name) {
    if ($Kind -eq "container") { $null = Docker @("inspect", "--type", "container", $Name) }
    else { $null = Docker @("network", "inspect", $Name) }
    return ($LASTEXITCODE -eq 0)
}

function New-TestContainer([string]$Name, [string[]]$Labels, [string]$Network, [string[]]$Command) {
    # The default command exits immediately, which is what a cheap "does it exist" fixture
    # wants. An occupant that must hold a network open needs one that BLOCKS - see CASE 5,
    # where `true` made the container exit the instant it started, docker reported the
    # network as having zero attached containers, and the case proved the opposite of what
    # it claimed to.
    if (-not $Command) { $Command = @("true") }
    $dockerArgs = @("create", "--name", $Name)
    foreach ($l in $Labels) { $dockerArgs += "--label"; $dockerArgs += $l }
    if ($Network) { $dockerArgs += "--network"; $dockerArgs += $Network }
    $dockerArgs += $Image
    $dockerArgs += $Command
    $null = Docker $dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "could not create test container $Name" }
    $script:Made += @{ Kind = "container"; Name = $Name }
}

function New-TestNetwork([string]$Name, [string[]]$Labels) {
    $dockerArgs = @("network", "create")
    foreach ($l in $Labels) { $dockerArgs += "--label"; $dockerArgs += $l }
    $dockerArgs += $Name
    $null = Docker $dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "could not create test network $Name" }
    $script:Made += @{ Kind = "network"; Name = $Name }
}

function Invoke-Reap {
    # Runs reap.ps1 and returns its exit code plus its text, so a case can assert on both.
    #
    # TWO PS5.1 TRAPS, both of which made this verifier LIE before they were fixed, and
    # both worth the explicit shape rather than the terse one:
    #
    #   1. ARRAY SPLATTING INTO A SCRIPT BINDS POSITIONALLY. `& $script @("-Owner","x")`
    #      does NOT set -Owner; it sets the first positional parameter to the literal
    #      string "-Owner" and the second to "x". Measured: a script with
    #      `param([string]$Owner,[string]$RemoveOrphan)` invoked that way reports
    #      Owner=[-Owner] RemoveOrphan=[x]. So every case below was silently running the
    #      -RemoveOrphan path while claiming to test -Owner. Parameters are named here,
    #      explicitly, one call site per mode.
    #
    #   2. `2>&1` DOES NOT CAPTURE Write-Host. It writes to the INFORMATION stream (6),
    #      not stdout, so `& $script 2>&1 | Out-String` returned an empty string and every
    #      assertion of the form "the output says X" passed by matching nothing against
    #      nothing. Measured: len=0 with `2>&1`, len=72 with `2>&1 6>&1`. That is a check
    #      that passes while checking nothing - in the verifier for a reaper, which is the
    #      worst possible place for one - so `Assert-NonEmpty` below refuses to let any
    #      case reason about text it never received.
    param(
        [string]$OwnerId = "",
        [string]$Orphan = "",
        [switch]$ReportMode
    )
    if ($OwnerId) { $out = & $Reap -Owner $OwnerId 2>&1 6>&1 | Out-String }
    elseif ($Orphan) { $out = & $Reap -RemoveOrphan $Orphan 2>&1 6>&1 | Out-String }
    elseif ($ReportMode) { $out = & $Reap -Report 2>&1 6>&1 | Out-String }
    else { throw "Invoke-Reap needs a mode" }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Text = $out }
}

function Assert-NonEmpty([string]$What, [string]$Text) {
    # A text assertion against an empty capture is not a passing test, it is no test.
    Check ("{0}: reap produced output to assert on" -f $What) ([bool]$Text.Trim()) "captured nothing"
}

$tag = "reapv-{0}" -f $PID
$ownLabel = "ai-stack.harness.owner"

Write-Host "verify-reap.ps1" -ForegroundColor Cyan
Write-Host ("  image {0}, resource prefix {1}" -f $Image, $tag)

try {
    if (-not (Docker @("image", "inspect", $Image)) -or $LASTEXITCODE -ne 0) {
        Write-Host ("SKIP: {0} is not present locally and this script does not pull." -f $Image) -ForegroundColor Yellow
        exit 1
    }

    # --- CASE 1: an owner reaps its own and only its own ---------------------------
    Write-Host "`nCASE 1  owner isolation" -ForegroundColor Cyan
    New-TestContainer "$tag-a" @("$ownLabel=$OwnerA")
    New-TestContainer "$tag-b" @("$ownLabel=$OwnerB")
    $r = Invoke-Reap -OwnerId $OwnerA
    Check "reap -Owner $OwnerA exits 0" ($r.Code -eq 0) ("exit {0}: {1}" -f $r.Code, $r.Text)
    Check "the owner's container is gone" (-not (Test-Exists "container" "$tag-a")) "$tag-a still exists"
    Check "ANOTHER owner's container survives" (Test-Exists "container" "$tag-b") "$tag-b was deleted - the reaper is not owner-scoped"

    # --- CASE 2: the wt- prefix is not a different owner ---------------------------
    Write-Host "`nCASE 2  'x' and 'wt-x' are the same owner" -ForegroundColor Cyan
    New-TestContainer "$tag-pfx" @("$ownLabel=wt-$OwnerA")
    $r = Invoke-Reap -OwnerId $OwnerA
    Check "a container labelled wt-<id> is reaped by -Owner <id>" (-not (Test-Exists "container" "$tag-pfx")) `
        "$tag-pfx survived - the alias set missed the prefixed spelling"

    # --- CASE 3: THE SAFETY CASE - a compose label wins over an owner label ---------
    # The guard that matters. If an agent ever writes the ownership label into a plane's
    # compose file, the reaper must refuse the resource rather than delete a prod service.
    Write-Host "`nCASE 3  compose-managed is refused even when it carries an owner label" -ForegroundColor Cyan
    New-TestContainer "$tag-compose" @("$ownLabel=$OwnerA", "com.docker.compose.project=verify-reap-fake")
    $r = Invoke-Reap -OwnerId $OwnerA
    Check "reap reports failure rather than deleting it" ($r.Code -ne 0) ("exit {0} - a refusal must not read as success" -f $r.Code)
    Check "the compose-labelled container SURVIVES" (Test-Exists "container" "$tag-compose") `
        "$tag-compose was deleted - the compose guard does not hold"
    Assert-NonEmpty "the CASE 3 refusal" $r.Text
    Check "the refusal names the resource" ($r.Text -match [regex]::Escape("$tag-compose")) $r.Text

    # --- CASE 4: no live compose resource is ever a candidate ----------------------
    # Read-only, against the real daemon: the report must not offer up anything a compose
    # project owns, and must not offer up an ai-stack_* anchor network.
    Write-Host "`nCASE 4  the live stack is not a candidate" -ForegroundColor Cyan
    $report = (Invoke-Reap -ReportMode).Text
    Assert-NonEmpty "the -Report output" $report
    $composeNames = @(Docker @("ps", "-a", "--filter", "label=com.docker.compose.project", "--format", "{{.Names}}") |
                      ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    $composeNets = @(Docker @("network", "ls", "--filter", "label=com.docker.compose.project", "--format", "{{.Name}}") |
                     ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    Check "there ARE compose resources to protect (the test is not vacuous)" `
        (($composeNames.Count + $composeNets.Count) -gt 0) "no compose-managed resources found - this case proved nothing"
    # A compose-managed resource may APPEAR in the report - the synthetic one from CASE 3
    # does, under HARNESS-OWNED - but only ever on a line that says PROTECTED. Any other
    # line mentioning it is the report offering it up for deletion. Checked per line rather
    # than by splitting the report into sections, because a section split is exactly the
    # kind of assumption that quietly stops matching when the output is reformatted.
    $leaked = @()
    foreach ($name in @($composeNames + $composeNets)) {
        foreach ($line in ($report -split "`r?`n")) {
            if ($line -notmatch ("(^|\s)" + [regex]::Escape($name) + "(\s|$)")) { continue }
            if ($line -match "PROTECTED") { continue }
            $leaked += ("{0}  <- {1}" -f $name, $line.Trim())
        }
    }
    Check "no compose-managed resource is offered up for deletion" (-not $leaked.Count) (($leaked -join " | "))
    $anchors = @($composeNets | Where-Object { $_ -like "ai-stack_*" })
    Check "the ai-stack_* anchor networks exist, so the guard had something to protect" ($anchors.Count -ge 1) `
        "no ai-stack_* network found - cannot claim the anchors are protected"
    # And the same names must never be reachable through the orphan path either.
    $orphanHint = @($report -split "`r?`n" | Where-Object { $_ -match "-RemoveOrphan" })
    $hintLeak = @($composeNames + $composeNets | Where-Object { $orphanHint -match [regex]::Escape($_) })
    Check "the -RemoveOrphan hint names no compose-managed resource" (-not $hintLeak.Count) ("leaked: " + ($hintLeak -join ", "))

    # --- CASE 5: a network in use is reported, not silently 'reaped' ---------------
    Write-Host "`nCASE 5  a network with a container attached is NOT removed" -ForegroundColor Cyan
    # Its OWN owner id, so the compose container CASE 3 deliberately leaves behind cannot
    # supply the non-zero exit this case is trying to attribute to the busy network.
    $ownerC = "verify-reap-net"
    New-TestNetwork "$tag-net" @("$ownLabel=$ownerC")
    # An unlabelled occupant: the network is the owner's, the container on it is not, so
    # reaping the owner cannot empty the network first. It must BLOCK - `docker network
    # inspect` counts running containers only, so an occupant that exits leaves the network
    # genuinely free and the case would pass while proving nothing.
    New-TestContainer "$tag-occupant" @() "$tag-net" @("sleep", "300")
    $null = Docker @("start", "$tag-occupant")
    $attached = Docker @("network", "inspect", "$tag-net", "--format", "{{len .Containers}}")
    Check "the occupant really is attached (the case is not vacuous)" `
        ((([string]($attached | Select-Object -First 1)).Trim()) -eq "1") ("attached count = " + ($attached -join " "))
    $r = Invoke-Reap -OwnerId $ownerC
    Check "reap does not claim success over a network it could not remove" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Check "the in-use network survives" (Test-Exists "network" "$tag-net") "$tag-net was removed while occupied"
    Assert-NonEmpty "the CASE 5 in-use report" $r.Text
    Check "the report says IN USE" ($r.Text -match "IN USE") $r.Text
    # Free it, then the same call must succeed.
    $null = Docker @("rm", "-f", "$tag-occupant")
    $r = Invoke-Reap -OwnerId $ownerC
    Check "once empty, the owner's network IS reaped" (-not (Test-Exists "network" "$tag-net")) "$tag-net survived an unoccupied reap"
    Check "and that reap now reports success" ($r.Code -eq 0) ("exit {0}: {1}" -f $r.Code, $r.Text)

    # --- CASE 6: orphans are reported, never auto-deleted --------------------------
    Write-Host "`nCASE 6  unlabelled orphans are reported and survive" -ForegroundColor Cyan
    New-TestContainer "$tag-orphan" @()
    $r = Invoke-Reap -OwnerId $OwnerA
    Check "reaping an owner does not touch an unlabelled container" (Test-Exists "container" "$tag-orphan") `
        "$tag-orphan was deleted by an owner-scoped reap"
    $report = (Invoke-Reap -ReportMode).Text
    Assert-NonEmpty "the CASE 6 report" $report
    Check "the report lists it as an orphan" ($report -match [regex]::Escape("$tag-orphan")) $report
    $r = Invoke-Reap -Orphan "$tag-orphan"
    Check "-RemoveOrphan removes the one it was told to" (-not (Test-Exists "container" "$tag-orphan")) "$tag-orphan survived -RemoveOrphan"
    Check "and leaves the other owner's container alone" (Test-Exists "container" "$tag-b") "$tag-b vanished during -RemoveOrphan"

    # --- CASE 7: -RemoveOrphan refuses a compose-managed name ----------------------
    Write-Host "`nCASE 7  -RemoveOrphan refuses a compose-managed name" -ForegroundColor Cyan
    $r = Invoke-Reap -Orphan "$tag-compose"
    Check "naming a compose-managed resource is refused, not obeyed" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Check "it still exists" (Test-Exists "container" "$tag-compose") "$tag-compose was deleted by name"

    # --- CASE 8: an unreachable daemon is loud, not silently clean -----------------
    Write-Host "`nCASE 8  an unreachable daemon exits 4 and says so" -ForegroundColor Cyan
    $prevHost = $env:DOCKER_HOST
    try {
        $env:DOCKER_HOST = "tcp://127.0.0.1:1"
        $r = Invoke-Reap -OwnerId $OwnerA
        Check "exit code is 4 (docker unreachable), not 0" ($r.Code -eq 4) ("exit {0}: {1}" -f $r.Code, $r.Text)
        Assert-NonEmpty "the CASE 8 unreachable notice" $r.Text
        Check "it says the daemon was unreachable" ($r.Text -match "not reachable") $r.Text
    } finally {
        if ($null -eq $prevHost) { Remove-Item Env:\DOCKER_HOST -ErrorAction SilentlyContinue }
        else { $env:DOCKER_HOST = $prevHost }
    }

    # --- CASE 9: a failed listing is not an empty sweep ----------------------------
    # The defect this script's subject was rewritten to fix: a docker query that ERRORS
    # must not read as "nothing to reap". Simulated by pointing PATH-resolved docker at a
    # daemon that answers `version` but nothing else is not possible here, so this asserts
    # the weaker but still meaningful property that an empty result and a failed result
    # take different exit codes - covered by CASE 8 (exit 4) versus an owner with nothing.
    Write-Host "`nCASE 9  'nothing labelled' and 'docker failed' are different answers" -ForegroundColor Cyan
    $r = Invoke-Reap -OwnerId "verify-reap-nobody-owns-this"
    Check "an owner with no resources exits 0" ($r.Code -eq 0) ("exit {0}: {1}" -f $r.Code, $r.Text)
    Assert-NonEmpty "the CASE 9 output" $r.Text
    Check "and says nothing was labelled, rather than claiming a reap" ($r.Text -match "nothing labelled") $r.Text
}
finally {
    if ($script:Fail -and $KeepOnFailure) {
        Write-Host "`n-KeepOnFailure: leaving test resources for inspection:" -ForegroundColor Yellow
        foreach ($m in $script:Made) { Write-Host ("    {0} {1}" -f $m.Kind, $m.Name) -ForegroundColor Yellow }
        Write-Host ("  Sweep them with: reap.ps1 -Owner {0}  (and docker rm -f for the unlabelled ones)" -f $OwnerA) -ForegroundColor Yellow
    } else {
        # Containers before networks - a network with an occupant will not delete.
        foreach ($m in @($script:Made | Where-Object { $_.Kind -eq "container" })) { $null = Docker @("rm", "-f", $m.Name) }
        foreach ($m in @($script:Made | Where-Object { $_.Kind -eq "network" })) { $null = Docker @("network", "rm", $m.Name) }
    }
}

Write-Host ""
Write-Host ("{0} passed, {1} failed" -f $script:Pass, $script:Fail) -ForegroundColor $(if ($script:Fail) { "Red" } else { "Green" })
if ($script:Fail) { exit 1 }
exit 0
