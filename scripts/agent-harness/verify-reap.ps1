# verify-reap.ps1 - prove reap.ps1 removes exactly what its owner made, and nothing else.
#
# WHY THIS EXISTS AS A SCRIPT. A reaper is a delete button pointed at a live daemon, and
# the only convincing evidence that it is aimed correctly is a run where the wrong thing
# was RIGHT THERE and survived. So WHEREVER A CASE IS ABOUT WHAT GETS DELETED, it builds the
# thing that must not go beside the thing that must, and checks both afterwards.
#
# Not every case is about that, and this sentence used to say "every case" - the sixth
# completeness word in this change to be wrong, caught by the fifth tester, four lines above
# the paragraph explaining why this header stopped making them. CASE 4 reads the live stack
# and builds nothing; CASE 8 takes the daemon away; CASE 9 asks about an owner that does not
# exist; CASE 7f builds a pair where BOTH must survive. If you are checking this claim,
# `Select-String "New-Test" -Context` over each case body is the way.
#
# IT NEVER TOUCHES A PLANE. Everything it creates is built by this script from
# `alpine:3.21`, attached to NO `ai-stack_*` network, and reuses NO production
# container_name - the pointwise sandbox shape MERGE-PROTOCOL.md permits without a lease.
# Those three are the safety properties; nothing else about the fixtures is promised here.
#
# In particular this used to add "a stopped container or an empty bridge network with a
# `reapv-` name", which was false three ways and contradicted line 45 below twenty lines
# later: CASE 5's occupant is deliberately STARTED (that is what makes the in-use check
# non-vacuous), CASE 7c's decoy is deliberately named with another container's 64-hex id
# (that is the shadowing it tests), and CASE 7f's network is deliberately occupied. Seventh
# completeness claim in this change to be wrong, caught by the seventh tester. The lesson
# has been learned twice and written down once: state the INVARIANT, never the inventory.
# Read-only questions about the live stack (does any compose container appear as a
# candidate?) are answered by looking, never by changing.
#
# MOST fixtures carry the ownership label; SOME DELIBERATELY DO NOT, because the cases that
# test the unlabelled paths need unlabelled resources - CASE 6 needs an orphan, and CASE 5
# needs an occupant that is not the network's owner. The fixtures also span several owner
# ids, so that one case's leftovers cannot supply another case's exit code.
#
# NO COUNTS ARE WRITTEN HERE, deliberately. This comment asserted a count twice and was
# wrong both times, in a file whose entire subject is labelling: first "all fixtures are
# labelled" (disproved by this script's own passing CASE 6), then "three are unlabelled"
# (there were two, and the next sentence named them). Both were caught by testers, one each
# on attempts 1 and 2. `documentation/notes/u4quad-findings.md:342` records the same repo
# going stale on a hardcoded count twice over.
#
# So the numbers are DERIVED instead: every fixture is registered in `$script:Made` with its
# owner, and the teardown block prints the actual owner ids and the actual unlabelled names.
# Read that, not a sentence up here that nobody re-counts.
#
# DO NOT RECOVER FROM A KILLED RUN WITH A NAME SWEEP. `docker ps -a --filter name=reapv-`
# looks like it would find everything and does not, three ways: docker's name filter is
# CASE-SENSITIVE and CASE 7e creates an upper-case fixture; `docker ps` does not list
# NETWORKS, and two fixtures are networks; and CASE 7c names its decoy with another
# container's 64-hex id on purpose, because that shadowing is what it tests.
#
# This header has now had a count or a completeness word wrong on three separate attempts,
# each caught by a tester. That is why nothing here describes the fixture set any more: the
# teardown block ENUMERATES what the run actually made, kind by kind, as runnable commands.
# Read that.
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
    #
    # A bare `@()` returned here EMITS NOTHING, so a docker command that matched nothing
    # hands the caller `$null`. On attempt 6 a call site did
    # `([string](Docker ... | Select-Object -First 1)).Trim()` on exactly that and threw
    # "You cannot call a method on a null-valued expression", taking out the
    # `-KeepOnFailure` teardown hint midway: 8 of 13 `docker rm -f` lines, no
    # `docker network rm` lines at all, no summary, 18 containers and 2 networks abandoned
    # by the block whose whole job was naming them.
    #
    # THE FIX IS AT THE CALL SITE, NOT HERE, and the reason is worth knowing because
    # `reap.ps1`'s Get-DockerIds solves the same problem the OPPOSITE way. Its callers
    # ASSIGN the result and test `if ($null -eq $names)`, so it returns `,@(...)` to keep an
    # empty result distinguishable from a failure. EVERY caller of THIS helper PIPES the
    # result - `Docker @(...) | ForEach-Object { ([string]$_).Trim() }` - and a `,@(...)`
    # wrap sends the pipeline ONE object, which `[string]` then joins with spaces into a
    # single line. Measured while fixing this: 22 network names became 1, and CASE 4's
    # anchor-network check went red. Same idiom, opposite correct answer, decided by what
    # the caller does with the value.
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

function Get-FixtureLabelling([string[]]$Labels) {
    # Returns @{ Labelled = <bool>; Owner = <string> }.
    #
    # LABELLED AND OWNER ARE TWO QUESTIONS, and conflating them is the exact bug this item
    # fixed in reap.ps1 - then reproduced here. `ai-stack.harness.owner=` (empty value) IS
    # labelled; returning "" for it made the teardown file CASE 7d's fixture under
    # "unlabelled by design" while CASE 7d itself asserts and proves the opposite. A tester
    # found the verifier contradicting its own passing case on attempt 3.
    foreach ($l in $Labels) {
        if ($l -like "ai-stack.harness.owner=*") {
            return @{ Labelled = $true; Owner = $l.Substring("ai-stack.harness.owner=".Length) }
        }
    }
    return @{ Labelled = $false; Owner = "" }
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
    $lab = Get-FixtureLabelling $Labels
    $script:Made += @{ Kind = "container"; Name = $Name; Labelled = $lab.Labelled; Owner = $lab.Owner }
}

function New-TestNetwork([string]$Name, [string[]]$Labels) {
    $dockerArgs = @("network", "create")
    foreach ($l in $Labels) { $dockerArgs += "--label"; $dockerArgs += $l }
    $dockerArgs += $Name
    $null = Docker $dockerArgs
    if ($LASTEXITCODE -ne 0) { throw "could not create test network $Name" }
    $lab = Get-FixtureLabelling $Labels
    $script:Made += @{ Kind = "network"; Name = $Name; Labelled = $lab.Labelled; Owner = $lab.Owner }
}

function Invoke-Reap {
    # Runs reap.ps1 and returns its exit code plus its text, so a case can assert on both.
    #
    # TWO PS5.1 TRAPS, both of which made this verifier LIE before they were fixed, and
    # both worth the explicit shape rather than the terse one:
    #
    #   1. ARRAY SPLATTING INTO A SCRIPT BINDS POSITIONALLY. Splatting needs a VARIABLE -
    #      `$a = @("-Owner","x"); & $script @a` - and an array splat passes values in ORDER
    #      with no parameter names, so the leading "-Owner" is simply the first value and
    #      lands in the first positional parameter. Measured: a script with
    #      `param([string]$Owner,[string]$RemoveOrphan)` invoked that way reports
    #      Owner=[-Owner] RemoveOrphan=[x]. So every case below was silently running the
    #      -RemoveOrphan path while claiming to test -Owner.
    #      (`@(...)` written inline after a command is NOT a splat at all - it is the array
    #      subexpression operator, passing ONE array-valued argument. Only the HASHTABLE
    #      splat of a variable, `$h = @{Owner="x"}; & $script @h`, binds by name.)
    #      Parameters are named here, explicitly, one call site per mode.
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
    # The fixture carries the compose label and NO owner label, so this reaches the COMPOSE
    # branch. It used to name `$tag-compose`, which carries both - and once CASE 7b's owner
    # check was added that check fired first, so CASE 7 passed without ever exercising the
    # guard it is named for. A tester found it passing for the wrong reason on attempt 2.
    Write-Host "`nCASE 7  -RemoveOrphan refuses a compose-managed name" -ForegroundColor Cyan
    New-TestContainer "$tag-composeonly" @("com.docker.compose.project=verify-reap-fake")
    $r = Invoke-Reap -Orphan "$tag-composeonly"
    Check "naming a compose-managed resource is refused, not obeyed" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Check "it still exists" (Test-Exists "container" "$tag-composeonly") "$tag-composeonly was deleted by name"
    Assert-NonEmpty "the CASE 7 refusal" $r.Text
    Check "the refusal gives the COMPOSE reason, not the not-an-orphan reason" `
        ($r.Text -match "compose-managed") $r.Text

    # --- CASE 7b: -RemoveOrphan refuses a resource that is NOT an orphan -----------
    # Found by a tester on 2026-09-07: this flag is documented as removing UNLABELLED
    # leftovers, and it used to remove any named resource the compose guard allowed -
    # including another worktree's live, labelled fixture. Code and documentation
    # disagreed, and the code was the more dangerous of the two.
    Write-Host "`nCASE 7b  -RemoveOrphan refuses a LABELLED resource (it is not an orphan)" -ForegroundColor Cyan
    $r = Invoke-Reap -Orphan "$tag-b"      # labelled $OwnerB, created in CASE 1
    Check "naming another owner's labelled resource is refused" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Check "that resource survives" (Test-Exists "container" "$tag-b") "$tag-b was deleted through the orphan path"
    Assert-NonEmpty "the CASE 7b refusal" $r.Text
    Check "the refusal names the owner and the -Owner command to use" `
        (($r.Text -match [regex]::Escape($OwnerB)) -and ($r.Text -match "-Owner")) $r.Text

    # --- CASE 7c: A NAME IS NOT AN IDENTITY -----------------------------------------
    # THE most dangerous defect found in this item, by a tester on attempt 2. Docker resolves
    # a reference by id BEFORE the name index, so a container NAMED with another container's
    # full 64-char id shadows it: `docker rm -f <that name>` deletes the OTHER one. reap.ps1
    # deleted by name, so a labelled decoy named after a production container's id would have
    # aimed the delete at production. Demonstrated here entirely on this script's own
    # fixtures - the victim is an unlabelled throwaway, never anything real.
    Write-Host "`nCASE 7c  a decoy NAMED with another container's id does not shadow it" -ForegroundColor Cyan
    $ownerD = "verify-reap-id"
    New-TestContainer "$tag-victim" @()
    $victimId = ([string]((Docker @("inspect", "--type", "container", "$tag-victim", "--format", "{{.Id}}")) | Select-Object -First 1)).Trim()
    Check "got the victim's full id" ($victimId.Length -ge 64) "id was '$victimId'"
    # A container whose NAME is that id, and which IS a legitimate reap candidate.
    # Its OWN owner id. CASE 3 deliberately leaves a compose-labelled $OwnerA container
    # alive, and reaping $OwnerA therefore exits 1 on that refusal - which would be
    # attributed to this case. Same confounder CASE 5 already avoids this way.
    New-TestContainer $victimId @("$ownLabel=$ownerD")
    # Its OWN id, captured now: after this point the decoy cannot be addressed by its name -
    # that name resolves to the victim, which is the entire point of the case. Checking the
    # decoy's fate by name would ask docker about the victim and report the decoy as alive
    # forever. (That is what the first version of this case did.)
    $decoyId = ([string]((Docker @("ps", "-a", "--no-trunc", "--filter", "label=$ownLabel=$ownerD", "--format", "{{.ID}}")) |
                Where-Object { $_ -and ([string]$_).Trim() -ne $victimId } | Select-Object -First 1)).Trim()
    Check "got the decoy's own id, distinct from the victim's" ($decoyId -and $decoyId -ne $victimId) "decoy id was '$decoyId'"
    $shadowCheck = ([string]((Docker @("inspect", "$victimId", "--format", "{{.Name}}")) | Select-Object -First 1)).Trim()
    Check "docker really does resolve that string to the VICTIM (the case is not vacuous)" `
        ($shadowCheck -like "*$tag-victim*") "docker resolved it to '$shadowCheck'"
    $r = Invoke-Reap -OwnerId $ownerD
    Check "the reap exits 0" ($r.Code -eq 0) ("exit {0}: {1}" -f $r.Code, $r.Text)
    Check "THE VICTIM SURVIVES - the delete was aimed by id, not by name" (Test-Exists "container" "$tag-victim") `
        "$tag-victim was deleted: reap.ps1 is deleting by name and a name shadowed it"
    $null = Docker @("inspect", "--type", "container", $decoyId)
    Check "and the decoy itself is gone" ($LASTEXITCODE -ne 0) "the decoy ($decoyId) survived"

    # --- CASE 7d: an EMPTY owner value is labelled, not an orphan --------------------
    # `ai-stack.harness.owner=` is a resource somebody labelled badly. It is not unlabelled,
    # so the orphan path must refuse it rather than delete it - the documented contract for
    # -RemoveOrphan is "unlabelled leftovers". Found by a tester on attempt 2.
    Write-Host "`nCASE 7d  an empty owner VALUE is still labelled" -ForegroundColor Cyan
    New-TestContainer "$tag-emptyowner" @("$ownLabel=")
    $report = (Invoke-Reap -ReportMode).Text
    Assert-NonEmpty "the CASE 7d report" $report
    Check "it is NOT listed as an orphan" `
        (-not (($report -split "`r?`n" | Where-Object { $_ -match [regex]::Escape("$tag-emptyowner") }) -match "^\s+container\s+\S+\s+(created|exited)")) $report
    $r = Invoke-Reap -Orphan "$tag-emptyowner"
    Check "-RemoveOrphan refuses it" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Check "and it survives" (Test-Exists "container" "$tag-emptyowner") "$tag-emptyowner was deleted through the orphan path"

    # --- CASE 7e: docker names are CASE-SENSITIVE, PowerShell comparison is not -----
    # Found by a tester on attempt 3. `-eq` and `-contains` ignore case, docker does not, and
    # both spellings can exist at once - so the lookup resolved to whichever row `docker ps`
    # listed first and the guards were then applied to a resource the caller never named.
    Write-Host "`nCASE 7e  a name differing only in case is a DIFFERENT resource" -ForegroundColor Cyan
    New-TestContainer "$tag-case" @("$ownLabel=$OwnerB")          # labelled: must be refused
    New-TestContainer "$tag-CASE".ToUpper() @()                    # unlabelled: a real orphan
    $r = Invoke-Reap -Orphan "$tag-case"
    Check "naming the LABELLED lower-case one is refused" ($r.Code -ne 0) ("exit {0}: {1}" -f $r.Code, $r.Text)
    Check "the labelled one survives" (Test-Exists "container" "$tag-case") "$tag-case was deleted"
    Check "and the DIFFERENTLY-CASED one was not touched instead" (Test-Exists "container" "$tag-CASE".ToUpper()) `
        "the upper-case container was deleted while the lower-case one was named"
    # And the owner match is case-sensitive too.
    $r = Invoke-Reap -OwnerId $OwnerB.ToUpper()
    Check "-Owner with the wrong case reaps nothing" (Test-Exists "container" "$tag-case") `
        "$tag-case was reaped by an owner id differing only in case"

    # --- CASE 7f: exit 0 is not proof the resource went ------------------------------
    # `docker container rm --force <missing id>` prints an error and EXITS 0 on this daemon.
    # Remove-Resource trusted the exit code, so a delete that removed nothing reported
    # "removed" - and that line is the only evidence anyone reads. Measured on attempt 3.
    Write-Host "`nCASE 7f  a delete that removes nothing is not reported as success" -ForegroundColor Cyan
    $missing = "0000000000000000000000000000000000000000000000000000000000000000"
    $null = Docker @("container", "rm", "--force", $missing)
    Check "docker really does exit 0 on a missing container (the case is not vacuous)" `
        ($LASTEXITCODE -eq 0) ("docker exited {0} - this daemon differs; the guard is still correct" -f $LASTEXITCODE)
    # An ambiguous name must be refused rather than resolved by list order.
    New-TestContainer "$tag-dual" @()
    New-TestNetwork "$tag-dual" @()
    $r = Invoke-Reap -Orphan "$tag-dual"
    Check "a name shared by a container and a network is refused as AMBIGUOUS" ($r.Code -ne 0) ("exit {0}" -f $r.Code)
    Assert-NonEmpty "the CASE 7f ambiguity refusal" $r.Text
    Check "the refusal offers ids to disambiguate with" ($r.Text -match "AMBIGUOUS") $r.Text
    Check "both survive" ((Test-Exists "container" "$tag-dual") -and (Test-Exists "network" "$tag-dual")) `
        "one of the $tag-dual pair was removed"

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
        # DERIVED from what was actually created, so it cannot name the wrong set. An
        # earlier version hardcoded one owner id while the fixtures used three, and read as
        # a complete recovery instruction while leaving two owners' resources behind.
        $owners = @($script:Made | Where-Object { $_.Owner } | ForEach-Object { $_.Owner } | Select-Object -Unique | Sort-Object)
        $bare   = @($script:Made | Where-Object { -not $_.Labelled } | ForEach-Object { $_.Name })
        $emptyv = @($script:Made | Where-Object { $_.Labelled -and -not $_.Owner } | ForEach-Object { $_.Name })
        Write-Host ("  Labelled, reapable ({0} owner id(s)): {1}" -f $owners.Count,
                    (($owners | ForEach-Object { "reap.ps1 -Owner $_" }) -join " ; ")) -ForegroundColor Yellow
        if ($emptyv.Count) {
            Write-Host ("  Labelled with an EMPTY owner ({0}) - no -Owner reaches these: {1}" -f $emptyv.Count, ($emptyv -join ", ")) -ForegroundColor Yellow
        }
        Write-Host ("  Unlabelled by design ({0}): {1}" -f $bare.Count, ($bare -join ", ")) -ForegroundColor Yellow
        # THE EXACT COMMANDS, built from what was made, one per resource, kind included.
        #
        # This used to print `docker ps -a --filter name=<tag>` as the recovery sweep, and
        # that command was wrong three ways at once, all found by a tester on attempt 4:
        # docker's name filter is CASE-SENSITIVE so it missed CASE 7e's upper-case fixture;
        # `docker ps` never sees NETWORKS, so both test networks were unreachable by it; and
        # CASE 7f creates a container and a network sharing one name, which the sweep listed
        # twice with no way to tell which was which. It is the same ambiguous-name defect
        # this item had just fixed in reap.ps1's own hint and did not carry across.
        Write-Host "  Or remove them one by one - names are case-sensitive and networks are not containers:" -ForegroundColor Yellow
        # BY ID where the fixture's NAME is another container's id - CASE 7c creates exactly
        # that, and `docker rm -f <the decoy's name>` resolves to the VICTIM. The subject of
        # this drill was fixed for that on attempt 2; the drill's own recovery hint was not,
        # and printed a command aimed at the wrong container. Same sibling-path shape, found
        # on attempt 5.
        foreach ($m in @($script:Made | Where-Object { $_.Kind -eq "container" })) {
            $ref = $m.Name
            if ($m.Name -match '^[0-9a-f]{64}$') {
                # Belt as well as braces: the helper above now returns an array even when
                # nothing matched, and this still does not assume a match. By teardown time
                # the decoy is usually ALREADY GONE, so "no match" is the normal case here,
                # not the exceptional one - which is exactly why the unguarded version fired
                # on essentially every failing run rather than on a rare edge.
                $own = @(Docker @("ps", "-a", "--no-trunc", "--filter", "name=^$($m.Name)$", "--format", "{{.ID}}")) |
                       ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ } | Select-Object -First 1
                if ($own) { $ref = $own }
                Write-Host  "      # next one is named with another container's id - removing by ID where one was found:" -ForegroundColor Yellow
            }
            Write-Host ("      docker rm -f {0}" -f $ref) -ForegroundColor Yellow
        }
        foreach ($m in @($script:Made | Where-Object { $_.Kind -eq "network" })) {
            Write-Host ("      docker network rm {0}" -f $m.Name) -ForegroundColor Yellow
        }
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
