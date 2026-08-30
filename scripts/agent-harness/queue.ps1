# queue.ps1 - the work item pipeline: develop -> test -> review -> merge.
#
# WHY THIS REPLACED A LOCK (operator, 2026-08-28): the earlier design used a `merge`
# lease, which was a mutex for a problem that is not a race. A worktree already isolates
# files, and git already refuses two worktrees on one branch - so concurrent merges were
# never the danger. The real requirement is a PIPELINE with separated roles: work is
# tested by someone who did not write it, and merged by someone who did not write it
# (GitFlow separation of duties). That is a queue, not a lock. With the developer never
# merging, merge contention disappears as a category.
#
# THE ARTIFACT IS LOCAL, deliberately. A GitHub PR would need branches pushed (against
# this repo's push policy), the gh CLI (absent here) and network. The diff is simply
# `git diff <line>...work/<id>`; this file carries the state a PR would carry.
#
# STATES
#   anchor-draft   an anchor has been PROPOSED and is waiting on the operator. No work yet.
#   anchor-confirmed the operator agreed what this is for. Work may begin. (See anchor.ps1
#                  for why this gate exists: the first real run shipped two artifacts that
#                  passed every check and were still not what was asked for.)
#   ready-to-test  developer submitted; test plan written; nobody has claimed it
#   testing        a tester holds it
#   test-failed    a case failed; back to the developer, who fixes IN THE SAME worktree and
#                  re-submits with -Resubmit. A plan holds SEVERAL CASES; a cycle happens
#                  because a case found something real, not as ceremony on the way to
#                  review. Cycles are the tests doing their job - an involved change
#                  finding nothing on the first pass is a reason to doubt the plan, not to
#                  celebrate.
#   test-passed    tests green, and STOPPED at the human gate (see -Approve). This is not
#                  ready for review yet - the operator may want changes, or the world may
#                  have moved, and review is the last cheap moment to say so.
#   ready-review   the operator released it for review
#   reviewing      a reviewer holds it
#   merged         landed by the reviewer (terminal)
#   (a reviewer whose rebase CHANGES the tested content sends it back with -Requeue:
#    a pass earned at one base is not a pass at another - that is the stale-pass rule)
#   rejected       reviewer sent it back (terminal for this item; open a new one)
#
# EXCLUSIVITY comes from CreateNew on a per-role claim file - the same atomic primitive
# the leases used, applied to the thing that actually needs it: the work item.
#
#   .\queue.ps1 -Propose -Id mem-readme -Anchor <path> -Developer wt-mem-readme   # BEFORE any work
#   .\queue.ps1 -ConfirmAnchor -Id mem-readme -By profnovice                       # THE ANCHOR GATE
#   .\queue.ps1 -AmendAnchor -Id mem-readme -By profnovice -Anchor <path> -Reason "..."
#     (the world turned out different; sends the item BACK to the developer - see the handler)
#   .\queue.ps1 -Submit -Id mem-readme -Branch work/mem-readme -Developer wt-mem-readme -TestPlan <path>
#   .\queue.ps1 -List
#   .\queue.ps1 -Claim -Id mem-readme -Role tester -By wt-tester-1
#   .\queue.ps1 -Pass  -Id mem-readme -By wt-tester-1 -Evidence <path> -PlanAdequate
#     (-PlanAdequate or -PlanInadequate is REQUIRED on both verdicts - see the checks)
#   .\queue.ps1 -Fail  -Id mem-readme -By wt-tester-1 -Reason "case 3 fails on a cold cache"
#   .\queue.ps1 -Resubmit -Id mem-readme -By wt-mem-readme [-TestPlan <path>]  # after a -Fail
#   .\queue.ps1 -Approve -Id mem-readme -By profnovice               # THE HUMAN GATE
#   .\queue.ps1 -Claim -Id mem-readme -Role reviewer -By wt-reviewer-1
#   .\queue.ps1 -Merged -Id mem-readme -By wt-reviewer-1 -Sha <merge sha>
#
# Exit codes: 0 ok | 1 usage/state error | 2 harness disabled | 3 claimed by someone else
#             | 4 refused (duties) | 5 refused (no confirmed anchor) | 6 ANDON not clear
#               (raised / incomplete / partial / not-evaluated / unavailable)
#             | 7 audit COVERAGE incomplete (-VerifyAudit found items it could not audit)
#
# GATE PROFILES (U6, 2026-08-30). `attended` is unchanged: a human runs -ConfirmAnchor
# and -Approve. `dark` makes both gates SELF-PASS - but only while the andon board is
# clear, and every self-pass writes a ledger record under the reserved `auto:` principal
# namespace that no -By value may occupy. What a gate DOES is still decided here; who
# passes it is tuning, and lives in harness.config.json under gate_profiles.
#
#   .\queue.ps1 -Audit [-Id x]         # the gate ledger, auto-passes flagged
#   .\queue.ps1 -VerifyAudit [-Id x]   # is the trail COMPLETE? 0 complete | 1 findings |
#                                      # 7 there were items it could not audit (NOT a pass).
#                                      # This line said "exit 1 if not" until 2026-08-30;
#                                      # drill step C reaches 7, so the usage was narrower
#                                      # than the tool and read as though 7 were impossible.

[CmdletBinding()]
param(
    [switch]$Propose,
    [switch]$ConfirmAnchor,
    [switch]$AmendAnchor,
    [switch]$Submit,
    [switch]$List,
    [switch]$Show,
    [switch]$Claim,
    [switch]$Pass,
    [switch]$Fail,
    [switch]$Merged,
    [switch]$Reject,
    [switch]$Requeue,
    [switch]$Approve,
    [switch]$Resubmit,
    [switch]$Unclaim,
    [switch]$ScopeNodes,
    [switch]$CloseOut,
    [switch]$Audit,
    [switch]$VerifyAudit,
    [string]$GateProfile = "",
    [string]$Id = "",
    [string]$Branch = "",
    [string]$Developer = "",
    [string]$TestPlan = "",
    [string]$Anchor = "",
    [string]$Role = "",
    [string]$By = "",
    [string]$Evidence = "",
    [string]$Reason = "",
    [string]$Sha = "",
    [string]$Thread = "",
    [string]$State = "",
    [switch]$PlanAdequate,
    [switch]$PlanInadequate,
    # The reviewer's forced verdict. Renamed from -FitsAnchor/-MissesAnchor on 2026-08-29
    # (U2): review judges CODEBASE fit, not intent. No alias for the old spelling - a rename
    # that leaves the old name working moves nobody, and this one is a change of question.
    [switch]$FitsCodebase,
    [switch]$Misfits,
    [int]$ClaimTtlMin = 0
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")
. (Join-Path $PSScriptRoot "anchor.ps1")
. (Join-Path $PSScriptRoot "gate-audit.ps1")

# The module OFF switch. "Off" must be inert and say so, not fail obscurely three calls
# deeper - see harness.config.json / MODULE.md.
# A param default cannot call into config.ps1 - param() binds before common.ps1 is dot-
# sourced. So 0 means "unset" and the configured value fills in, while an explicit -0 from
# a caller is still honoured as "expire immediately".
if (-not $PSBoundParameters.ContainsKey("ClaimTtlMin")) {
    $ClaimTtlMin = [int](Get-HarnessSetting "pipeline.claim_ttl_minutes" 60)
}

$offReason = Get-HarnessDisabledReason
if ($offReason) { Write-Host "REFUSED: $offReason" -ForegroundColor Yellow; exit 2 }

# The role rules, declared ONCE. They were previously re-derived in four separate
# if/else branches, so adding a role (or renaming a state) meant finding all of them and
# getting every one right. Open for extension - a new role is a new row, not an edit to
# the claim/verdict logic.
$RoleRules = [ordered]@{
    tester   = @{ ready = "ready-to-test"; busy = "testing";   duty = "execute the plan" }
    reviewer = @{ ready = "ready-review";  busy = "reviewing"; duty = "review and merge" }
}

$QueueDir = Join-Path (Get-SharedStateDir) "queue"
if (-not (Test-Path $QueueDir)) { New-Item -ItemType Directory -Force -Path $QueueDir | Out-Null }

function Now() { return [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function ItemPath([string]$i) { return (Join-Path $QueueDir "$i.json") }
function ClaimPath([string]$i, [string]$r) { return (Join-Path $QueueDir "$i.$r.claim") }
function Die([string]$m, [int]$code = 1) { Write-Host "ERROR: $m" -ForegroundColor Red; exit $code }

function Read-Item([string]$i) {
    $p = ItemPath $i
    if (-not (Test-Path $p)) { Die "no queue item '$i'" }
    return (Get-Content -Raw -Path $p | ConvertFrom-Json)
}

function Write-Item($item) {
    $p = ItemPath $item.id
    $tmp = "$p.tmp"
    ($item | ConvertTo-Json -Depth 8) | Set-Content -Path $tmp -Encoding ASCII
    Move-Item -Path $tmp -Destination $p -Force
}

function Set-Field($item, [string]$name, $value) {
    # An item written before a field existed has no such property, and PowerShell throws on
    # assignment rather than creating it. Every live queue contains items older than the
    # newest field, so this is the ordinary path.
    if ($item.PSObject.Properties.Name -contains $name) { $item.$name = $value }
    else { $item | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}

function Add-History($item, [string]$what, [string]$who) {
    $item.history += [ordered]@{ at = Now; who = $who; what = $what }
}

function Assert-Claim($item, [string]$role, [string]$who) {
    $c = ClaimPath $item.id $role
    if (-not (Test-Path $c)) { Die "you do not hold the $role claim on '$($item.id)' - claim it first" 3 }
    $holder = (Get-Content -Raw -Path $c | ConvertFrom-Json).by
    if ($holder -ne $who) { Die "the $role claim on '$($item.id)' is held by $holder, not $who" 3 }
}

function Normalize-Id([string]$who) {
    # `wt-coder-readme` and `coder-readme` are the SAME agent - the worktree directory is
    # prefixed, the registry id is not. Separation of duties is a string comparison, so
    # without this a developer could test or review their own work simply by typing the
    # other form of their own name. Found by a developer agent, not by me.
    if (-not $who) { return "" }
    return ($who.ToLower() -replace '^wt-', '')
}

function Test-KnownAgent([string]$who) {
    # Advisory only: testers, reviewers and the operator legitimately have no worktree.
    $reg = Join-Path (Get-SharedStateDir) "worktrees.json"
    if (-not (Test-Path $reg)) { return $true }
    try { $rows = (Get-Content -Raw -Path $reg | ConvertFrom-Json).worktrees } catch { return $true }
    if (-not $rows) { return $true }
    $known = @($rows.PSObject.Properties.Name | ForEach-Object { Normalize-Id $_ })
    return ($known -contains (Normalize-Id $who))
}

function Drop-Claim([string]$i, [string]$role) {
    $c = ClaimPath $i $role
    if (Test-Path $c) { Remove-Item $c -Force }
}

# --- gates: who passes them, and what the record says --------------------------------
# The gate PROFILE decides who passes; this file still decides what passing DOES. See the
# header and harness.config.json -> gate_profiles.

function Get-EmptyGateMap {
    $m = [ordered]@{}
    foreach ($g in (Get-GateNames)) { $m[$g] = [ordered]@{ kind = ""; by = ""; at = 0; profile = "" } }
    return $m
}

function Set-ItemGate($item, [string]$gate, [string]$kind, [string]$by, [string]$profile) {
    if (-not ($item.PSObject.Properties.Name -contains "gates") -or -not $item.gates) {
        Set-Field $item "gates" (Get-EmptyGateMap)
    }
    Set-Field $item.gates $gate ([ordered]@{ kind = $kind; by = $by; at = (Now); profile = $profile })
}

function Assert-HumanPrincipal([string]$who, [string]$flag) {
    # The reserved namespace is reserved in BOTH directions. A human may not sign as `auto:`
    # (which would let a person hide behind the machine), and the auto path may not sign as
    # a person (which is the failure this whole clause exists to prevent: a record that
    # reads as human approval when no human was there).
    if (Test-AutoPrincipal $who) {
        Die ("'{0}' is in the reserved auto-pass namespace '{1}' and cannot be used as {2} -By. " -f $who, (Get-AutoPrincipalPrefix), $flag) 4
    }
}

function Resolve-GateOrDie([string]$gate) {
    try { return (Resolve-Gate -Gate $gate -Profile $GateProfile) }
    catch { Die $_.Exception.Message }
}

function Invoke-AutoGate($item, [string]$gate, $decision) {
    # Try to auto-pass $gate. Returns the andon verdict; the CALLER halts on a raise, so the
    # halt is visible at the state transition rather than buried in here.
    $branches = @()
    if ($item.branch) { $branches += $item.branch }
    $andon = Invoke-AndonForGate -RunBranches $branches
    if ($andon.status -ne "clear") {
        [void](Write-GateRecord -Item $item.id -Gate $gate -Decision "refused" -Kind "auto" `
                 -Principal ((Get-AutoPrincipalPrefix) + $decision.profile) -GateProfile $decision.profile `
                 -FromState $item.state -ToState $item.state -Andon $andon)
    }
    return $andon
}

function Test-AndonField($andon, [string]$name) {
    # Does this andon verdict carry $name? Handles both shapes the verdict travels in: an
    # [ordered] hashtable (fresh from Invoke-AndonForGate) and a PSCustomObject (parsed back
    # out of the ledger). Asking `.PSObject.Properties.Name` of a hashtable answers about the
    # DICTIONARY, not its contents - see Stop-OnAndon.
    if ($null -eq $andon) { return $false }
    if ($andon -is [System.Collections.IDictionary]) { return $andon.Contains($name) }
    return ($andon.PSObject.Properties.Name -contains $name)
}

function Stop-OnAndon($andon, [string]$gate, [string]$id, [string]$parkedAt) {
    Write-Host ""
    Write-Host ("ANDON {0} - the '{1}' gate will NOT auto-pass." -f ("$($andon.status)").ToUpper(), $gate) -ForegroundColor Red
    # BOTH LISTS. `fired` is what the detectors saw and `halted` is what stopped the line;
    # they were one derived list until 2026-08-30, which hid a fire whose on_fire was not
    # `halt`. De-duplicated because a halting fire is legitimately in both.
    $seen = @()
    if (Test-AndonField $andon "halted") { $seen += @($andon.halted) }
    $seen += @($andon.fired)
    foreach ($f in @($seen | Where-Object { $_ } | Select-Object -Unique)) { Write-Host ("  - {0}" -f $f) -ForegroundColor Red }
    # State the coverage on the console too. A halt whose only word is 'not-evaluated' sends
    # the operator to the config; a halt that says 0 of 5 evaluated sends them to the right line.
    #
    # THIS GUARD USED TO BE `$andon.PSObject.Properties.Name -contains "evaluated"`, WHICH IS
    # ALWAYS FALSE HERE. Invoke-AndonForGate returns an [ordered] hashtable, and an
    # OrderedDictionary's PSObject properties are the .NET ones - Count, Keys, Values,
    # IsReadOnly - never its keys. So the line never printed and a real dark halt reached the
    # operator with no coverage at all: a check that could not fire, inside the tool built to
    # refuse checks that cannot fire. Test-AndonField below asks the question in a way that
    # works for a hashtable AND for a PSCustomObject, because a record read back from the
    # ledger is the latter.
    if (Test-AndonField $andon "evaluated") {
        Write-Host ("  board coverage: {0} of {1} declared condition(s) evaluated, {2} switched off" -f
                    [int]$andon.evaluated, [int]$andon.conditions, [int]$andon.disabled) -ForegroundColor Red
    }
    if ((Test-AndonField $andon "missing") -and ([int]$andon.missing -gt 0)) {
        Write-Host ("  {0} of {1} REQUIRED condition(s) are NOT DECLARED: {2}" -f
                    [int]$andon.missing, [int]$andon.required, (@($andon.missing_ids) -join ", ")) -ForegroundColor Red
    }
    Write-Host ""
    Write-Host ("'{0}' is PARKED at '{1}'. The refusal is in the gate ledger (queue.ps1 -Audit -Id {0})." -f $id, $parkedAt) -ForegroundColor Yellow
    Write-Host ("Clear the condition, or pass the gate attended: queue.ps1 -{0} -Id {1} -By <operator>" -f $(if ($gate -eq "anchor") { "ConfirmAnchor" } else { "Approve" }), $id) -ForegroundColor Yellow
    exit 6
}

# --- list / show --------------------------------------------------------------------
if ($CloseOut) {
    # CLOSE OUT a row whose work landed OUTSIDE this queue's gates (§C.1).
    #
    # Not -Reject, and the distinction is the point. 'rejected' asserts a reviewer turned
    # the work down; these items MERGED. Recording them as rejected would put a false
    # statement into the audit trail that C.7 makes the deliverable's twin - and the whole
    # reason that trail is trusted is that nobody writes convenient things into it.
    #
    # 'closed-outside-gates' says exactly what happened: the item existed, the work landed,
    # and this queue did not adjudicate it.
    if (-not $Id) { Die "-CloseOut needs -Id" }
    if (-not $Reason) { Die "-CloseOut needs -Reason - a row closed without one is a row nobody can account for" }
    $f = Join-Path $QueueDir "$Id.json"
    if (-not (Test-Path $f)) { Die "no queue item '$Id'" }
    $item = Get-Content -Raw -Path $f | ConvertFrom-Json
    if ($item.state -in @("merged", "rejected", "closed-outside-gates")) {
        Write-Host "'$Id' is already terminal ('$($item.state)') - nothing to close." -ForegroundColor Yellow
        exit 0
    }
    $was = $item.state
    Set-Field $item "state" "closed-outside-gates"
    Set-Field $item "closed_reason" $Reason
    Add-History $item "closed-outside-gates" ("was '{0}': {1}" -f $was, $Reason)
    Write-Item $item
    Write-Host "closed '$Id' (was '$was'): $Reason" -ForegroundColor Green
    exit 0
}

if ($ScopeNodes) {
    # U2: THE QUEUE, AS THE SCOPE TREE IT ALREADY IS.
    #
    # A queue item is one bounded tier below a project, handed to a developer deliberately
    # unaware of the rest - which is agent-org's ScopeNode at depth 1, and which the harness
    # has been building since it existed under a different name. This prints the projection
    # so the shape is reachable rather than living only in a test; scope_node.py owns the
    # mapping and test_scope_node.py pins it against agent-org's real model.
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) {
        Write-Host "python not found - the ScopeNode projection needs it." -ForegroundColor Red
        exit 2
    }
    $mod = Join-Path $PSScriptRoot "scope_node.py"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Run the FILE, not a `python -c` snippet. The snippet form broke twice for reasons
    # unrelated to the projection: Windows argument handling stripped the quotes out of a
    # string literal, and the repo path (which contains a space) split across argv.
    try { & python $mod $QueueDir $Line } finally { $ErrorActionPreference = $prev }
    exit $LASTEXITCODE
}

if ($List) {
    $items = @(Get-ChildItem -Path $QueueDir -Filter "*.json" -ErrorAction SilentlyContinue)
    if (-not $items.Count) { Write-Host "queue empty" -ForegroundColor Green; exit 0 }
    Write-Host ("{0,-18} {1,-17} {2,-20} {3}" -f "ID", "STATE", "DEVELOPER", "HELD BY")
    foreach ($f in ($items | Sort-Object Name)) {
        # `<id>.anchor.json` sits beside `<id>.json` in this directory. Ids are
        # [a-z0-9-], so a dot in the base name means a sidecar file, not a work item.
        if ($f.BaseName.Contains(".")) { continue }
        $it = Get-Content -Raw -Path $f.FullName | ConvertFrom-Json
        if ($State -and $it.state -ne $State) { continue }
        $held = ""
        foreach ($r in @("tester", "reviewer")) {
            $c = ClaimPath $it.id $r
            if (Test-Path $c) { $held = "$r=" + (Get-Content -Raw -Path $c | ConvertFrom-Json).by }
        }
        $flag = if ($it.PSObject.Properties.Name -contains "line_mergeable" -and -not $it.line_mergeable) { " [needs hand-off]" } else { "" }
        # The two states that are waiting on a PERSON are called out: an unread queue is
        # how a human gate quietly becomes a human bottleneck.
        if ($it.state -eq "anchor-draft") { $flag += " [waiting: operator to confirm the anchor]" }
        if ($it.state -eq "test-passed")  { $flag += " [waiting: operator to release for review]" }
        if ($it.PSObject.Properties.Name -contains "gates" -and $it.gates) {
            $autoGates = @(Get-GateNames | Where-Object { $it.gates.$_ -and $it.gates.$_.kind -eq "auto" })
            if ($autoGates.Count -gt 0) { $flag += (" [AUTO-PASSED: " + ($autoGates -join ", ") + "]") }
        }
        Write-Host ("{0,-18} {1,-17} {2,-20} {3}{4}" -f $it.id, $it.state, $it.developer, $held, $flag)
    }
    exit 0
}

if ($Show) {
    if (-not $Id) { Die "-Show needs -Id" }
    $it = Read-Item $Id
    if ($it.anchor) {
        Write-Host "--- ANCHOR ---" -ForegroundColor Cyan
        Write-Host (Format-Anchor $it.anchor)
        $who = if (Test-AutoPrincipal $it.anchor_confirmed_by) {
                   "AUTO-PASSED by " + $it.anchor_confirmed_by + " - NO HUMAN CONFIRMED THIS"
               } elseif ($it.anchor_confirmed_by) { "confirmed by " + $it.anchor_confirmed_by }
               else { "NOT YET CONFIRMED" }
        Write-Host ("(" + $who + ")")
        Write-Host ""
        Write-Host "--- RECORD ---" -ForegroundColor Cyan
    }
    $it | ConvertTo-Json -Depth 8
    exit 0
}

# --- the gate audit trail -----------------------------------------------------------
if ($Audit) {
    $recs = @(Read-GateLedger -Item $Id)
    if ($recs.Count -eq 0) {
        Write-Host "The gate ledger is empty$(if ($Id) { " for '$Id'" })." -ForegroundColor Yellow
        Write-Host ("  ({0})" -f (Get-GateLedgerPath))
        exit 0
    }
    Write-Host ("GATE LEDGER  {0}" -f (Get-GateLedgerPath)) -ForegroundColor Cyan
    foreach ($r in $recs) {
        $colour = "Gray"
        if ($r.kind -eq "auto") { $colour = "Yellow" }
        if ($r.decision -eq "refused") { $colour = "Red" }
        Write-Host ("  " + (Format-GateRecord $r)) -ForegroundColor $colour
        $lines = @()
        if ($r.andon -and ($r.andon.PSObject.Properties.Name -contains "halted")) { $lines += @($r.andon.halted) }
        $lines += @($r.andon.fired)
        foreach ($f in @($lines | Where-Object { $_ } | Select-Object -Unique)) { Write-Host ("      andon: {0}" -f $f) -ForegroundColor DarkGray }
    }
    $auto = @($recs | Where-Object { $_.kind -eq "auto" -and $_.decision -eq "passed" })
    Write-Host ""
    Write-Host ("{0} gate event(s); {1} passed with NO HUMAN in the loop." -f $recs.Count, $auto.Count) -ForegroundColor $(if ($auto.Count -gt 0) { "Yellow" } else { "Green" })
    exit 0
}

if ($VerifyAudit) {
    # IS THE TRAIL COMPLETE? This is the half of U6's validation column that gets skipped:
    # a clean unattended run must leave a trail something can CHECK, or "dark-factory mode"
    # is just a halt mechanism with a nicer name. The rules are in gate-audit.ps1.
    $items = @()
    foreach ($f in (Get-ChildItem -Path $QueueDir -Filter "*.json" -File | Where-Object { $_.Name -notlike "*.anchor.json" })) {
        try { $items += (Get-Content -Raw -Path $f.FullName | ConvertFrom-Json) } catch { }
    }
    $only = @()
    if ($Id) { $only = @($Id) }
    $verdict = Test-GateAuditComplete -Items $items -OnlyItems $only
    Write-Host ("AUDIT COMPLETENESS  ({0} item(s) audited)" -f @($verdict.audited).Count) -ForegroundColor Cyan
    foreach ($a in $verdict.audited) { Write-Host ("  audited   : {0}" -f $a) }
    foreach ($u in $verdict.unaudited) { Write-Host ("  UNAUDITED : {0} (predates the gate ledger - not a pass)" -f $u) -ForegroundColor DarkGray }
    if (@($verdict.findings).Count -gt 0) {
        Write-Host ""
        Write-Host ("INCOMPLETE - {0} finding(s):" -f @($verdict.findings).Count) -ForegroundColor Red
        foreach ($f in $verdict.findings) { Write-Host ("  - {0}" -f $f) -ForegroundColor Red }
        exit 1
    }
    if (@($verdict.unaudited).Count -gt 0) {
        # NOT A PASS. An item this check could not audit is coverage it does not have, and
        # reporting that as green is the same skip-counts-as-a-pass shape the andon board
        # refuses. A distinct code so a caller can tell "the trail is wrong" (1) from "the
        # trail does not cover everything" (7).
        Write-Host ""
        Write-Host ("COVERAGE INCOMPLETE - {0} item(s) could not be audited. Nothing is wrong with" -f @($verdict.unaudited).Count) -ForegroundColor Yellow
        Write-Host "what WAS audited; this is not a green." -ForegroundColor Yellow
        exit 7
    }
    Write-Host ""
    Write-Host "COMPLETE - every gate these item(s) CROSSED has a record, and every record names who" -ForegroundColor Green
    Write-Host "or what passed it." -ForegroundColor Green
    # SAY WHAT 'COMPLETE' DOES NOT MEAN, in the same breath as saying it. 'Crossed' is derived
    # from item state, so this is a statement about the gates these items reached - never a
    # statement that the pipeline's gates were all enforced. An item that never reached a gate
    # is not evidence about that gate.
    Write-Host "  Scope: 'crossed' is derived from each item's own state. This says nothing about a" -ForegroundColor DarkGray
    Write-Host "  gate an item never reached." -ForegroundColor DarkGray
    if (-not [bool](Get-HarnessSetting "pipeline.anchor_required" $true)) {
        Write-Host "  pipeline.anchor_required=false: an item created without an anchor crosses NO anchor" -ForegroundColor Yellow
        Write-Host "  gate, so completeness cannot account for one. That is configuration, not coverage." -ForegroundColor Yellow
    }
    exit 0
}

# --- anchor: propose / confirm ------------------------------------------------------
# The anchor gate. It sits BEFORE the work because that is the cheapest moment to correct
# a misunderstanding - the pre-review gate catches "the world moved", this one catches
# "we were never building the same thing".
if ($Propose) {
    if (-not $Id -or -not $Anchor) { Die "-Propose needs -Id and -Anchor <path to an anchor json>" }
    if (Test-Path (ItemPath $Id)) { Die "queue item '$Id' already exists (use a new -Id, or -Show it)" }
    try { $anchorObj = Read-AnchorFile $Anchor } catch { Die $_.Exception.Message }
    # Copy it beside the item, for the same reason the test plan is copied: the developer's
    # worktree is deleted at the end, and a tester or reviewer reading a dangling path is
    # exactly the failure this whole mechanism exists to prevent.
    $anchorDest = Join-Path $QueueDir "$Id.anchor.json"
    Copy-Item -Path $Anchor -Destination $anchorDest -Force
    $item = [ordered]@{
        id = $Id; branch = ""; line = ""; developer = $Developer
        state = "anchor-draft"; anchor = $anchorObj; anchor_file = $anchorDest
        anchor_confirmed_by = ""; anchor_confirmed_at = 0; gates = (Get-EmptyGateMap)
        test_plan = ""; thread = $Thread; attempt = 1
        line_mergeable = $true
        submitted_sha = ""; tested_at_sha = ""; merged_sha = ""
        results = @(); history = @()
    }
    Add-History $item "anchor proposed" $(if ($Developer) { $Developer } else { "unknown" })
    Write-Item $item
    Write-Host ("Anchor PROPOSED for '{0}'. Nothing may be built yet." -f $Id) -ForegroundColor Cyan
    Write-Host ""
    Write-Host (Format-Anchor $anchorObj)
    Write-Host ""
    Write-Host ("  The operator confirms with: queue.ps1 -ConfirmAnchor -Id {0} -By <operator>" -f $Id)
    Write-Host "  Until then this is a proposal, not an agreement."
    exit 0
}

if ($ConfirmAnchor) {
    if (-not $Id -or -not $By) { Die "-ConfirmAnchor needs -Id and -By (who is agreeing)" }
    Assert-HumanPrincipal $By "-ConfirmAnchor"
    $item = Read-Item $Id
    if ($item.state -ne "anchor-draft") {
        Die ("'{0}' is '{1}', not 'anchor-draft' - an anchor is confirmed once, before the work" -f $Id, $item.state)
    }
    # An amended anchor REPLACES the proposal: the operator is allowed to change what the
    # work is for, and the record must show what was actually agreed, not what was asked.
    if ($Anchor) {
        try { $anchorObj = Read-AnchorFile $Anchor } catch { Die $_.Exception.Message }
        Copy-Item -Path $Anchor -Destination $item.anchor_file -Force
        $item.anchor = $anchorObj
        Add-History $item "anchor amended on confirmation" $By
    }
    $was = $item.state
    $item.state = "anchor-confirmed"
    $item.anchor_confirmed_by = $By
    $item.anchor_confirmed_at = Now
    Set-ItemGate $item "anchor" "human" $By (Get-GateProfileName -Requested $GateProfile)
    Add-History $item "anchor confirmed" $By
    Write-Item $item
    [void](Write-GateRecord -Item $Id -Gate "anchor" -Decision "passed" -Kind "human" -Principal $By `
             -GateProfile (Get-GateProfileName -Requested $GateProfile) -FromState $was -ToState $item.state)
    Write-Host ("Anchor CONFIRMED for '{0}' by {1}. Work may begin." -f $Id, $By) -ForegroundColor Green
    Write-Host ""
    Write-Host (Format-Anchor $item.anchor)
    exit 0
}

if ($AmendAnchor) {
    # THE WORLD CAN TURN OUT DIFFERENT MID-FLIGHT. An anchor is confirmed against what was
    # known then; when a scope justification turns out to be false, the honest move is to
    # correct the record, not to carry a known-wrong anchor to the reviewer and explain it
    # in prose. Found live: an anchor put a script out of scope "having grepped it - it
    # contains no bare invocation", and it contained sixteen.
    #
    # THE COST IS DELIBERATE: amending sends the item BACK to the developer. A test verdict
    # describes work against the target that existed when it ran, so moving the target
    # invalidates it - the same reasoning as the stale-pass rule. Without that, amending
    # would be the obvious way to make failing work fit, which is the one thing this gate
    # exists to prevent.
    if (-not $Id -or -not $By -or -not $Anchor -or -not $Reason) {
        Die "-AmendAnchor needs -Id, -By, -Anchor <path> and -Reason (what changed about the world)"
    }
    $item = Read-Item $Id
    if ($item.state -in @("merged", "rejected")) { Die "'$Id' is '$($item.state)' - open a new item" }
    if ($item.state -eq "anchor-draft") { Die "'$Id' is not confirmed yet - amend it on -ConfirmAnchor instead" }
    try { $anchorObj = Read-AnchorFile $Anchor } catch { Die $_.Exception.Message }
    Copy-Item -Path $Anchor -Destination $item.anchor_file -Force
    Set-Field $item "anchor" $anchorObj
    Set-Field $item "anchor_confirmed_by" $By
    Set-Field $item "anchor_confirmed_at" (Now)
    $was = $item.state
    foreach ($r in $RoleRules.Keys) { Drop-Claim $Id $r }
    Set-Field $item "state" "anchor-confirmed"
    Set-Field $item "tested_at_sha" ""
    Add-History $item "anchor AMENDED (was '$was'): $Reason" $By
    Write-Item $item
    Write-Host ("Anchor AMENDED for '{0}' by {1}." -f $Id, $By) -ForegroundColor Yellow
    Write-Host ("  {0}" -f $Reason)
    if ($was -ne "anchor-confirmed") {
        Write-Host ("  '{0}' was '{1}' and is now back with the developer: a verdict describes" -f $Id, $was) -ForegroundColor Yellow
        Write-Host "  work against the target that existed when it ran, and the target moved." -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host (Format-Anchor $item.anchor)
    exit 0
}

# --- submit -------------------------------------------------------------------------
if ($Submit) {
    if (-not $Id -or -not $Branch -or -not $Developer) { Die "-Submit needs -Id, -Branch and -Developer" }
    # -Thread is how the bridge knows which Mattermost conversation to report back into.
    if (-not $TestPlan) {
        Die ("-Submit needs -TestPlan. The plan is written BEFORE the work is queued - it is " +
             "what someone else executes. List the CASES, and for each say what counts as " +
             "passing and what would count as failing. A plan that cannot fail is not a plan, " +
             "and 'I tested it myself' is not one either.")
    }
    # The anchor gate. An item that was proposed and confirmed is ADVANCED here; creating
    # one on the fly is only allowed when the operator has turned the gate off, and then it
    # is a stated configuration choice rather than a silent bypass.
    $anchorRequired = [bool](Get-HarnessSetting "pipeline.anchor_required" $true)
    $existing = if (Test-Path (ItemPath $Id)) { Read-Item $Id } else { $null }
    if ($existing) {
        if ($existing.state -eq "anchor-draft") {
            # THE ANCHOR GATE. Under `attended` this is where an unconfirmed anchor stops.
            # Under `dark` the gate self-passes - but only while the andon board is clear,
            # and the pass is written to the ledger as an AUTO pass under the reserved
            # principal namespace, so an operator reading the trail afterwards can see at a
            # glance that no human agreed what this item was for.
            $gd = Resolve-GateOrDie "anchor"
            if ($gd.passer -ne "auto") {
                Die (("'{0}' has an anchor that nobody has confirmed. The operator agrees what this " +
                      "is for BEFORE it is built: queue.ps1 -ConfirmAnchor -Id {0} -By <operator>") -f $Id) 5
            }
            $andon = Invoke-AutoGate $existing "anchor" $gd
            if ($andon.status -ne "clear") { Stop-OnAndon $andon "anchor" $Id "anchor-draft" }
            $autoWho = (Get-AutoPrincipalPrefix) + $gd.profile
            $existing.state = "anchor-confirmed"
            $existing.anchor_confirmed_by = $autoWho
            $existing.anchor_confirmed_at = Now
            Set-ItemGate $existing "anchor" "auto" $autoWho $gd.profile
            Add-History $existing "anchor AUTO-PASSED (gate profile '$($gd.profile)') - no human saw it" $autoWho
            Write-Item $existing
            [void](Write-GateRecord -Item $Id -Gate "anchor" -Decision "passed" -Kind "auto" -Principal $autoWho `
                     -GateProfile $gd.profile -FromState "anchor-draft" -ToState "anchor-confirmed" -Andon $andon)
            Write-Host ("Anchor AUTO-PASSED for '{0}' under gate profile '{1}' - NO HUMAN CONFIRMED IT." -f $Id, $gd.profile) -ForegroundColor Yellow
            $existing = Read-Item $Id
        }
        if ($existing.state -ne "anchor-confirmed") {
            Die ("queue item '$Id' already exists in state '$($existing.state)' (use a new -Id, or -Show it)")
        }
    } elseif ($anchorRequired) {
        Die ("no anchor for '$Id'. Propose one first - the work is agreed before it is built:`n" +
             "  queue.ps1 -Propose -Id $Id -Anchor <path> -Developer <you>`n" +
             "Start from anchor.template.json. (Set pipeline.anchor_required=false in " +
             "harness.config.json to work without anchors - see anchor.ps1 for what that costs.)") 5
    }
    # The plan is a FILE, and the tool must prove it. It used to store whatever string it was
    # given, so `-TestPlan "I tested it"` sailed through - precisely what the error text
    # claims to refuse. Both developer agents in the first pipeline run raised this.
    if (-not (Test-Path $TestPlan)) {
        Die ("-TestPlan must be a path to a file that exists (got '$TestPlan'). Write the plan " +
             "down first - the tester executes it, and a sentence is not a plan.")
    }
    # And it needs a HOME. The obvious place is the developer's worktree, which is exactly
    # what gets deleted at the end - leaving the item pointing at nothing. Copy it beside the
    # item in the shared state dir, which outlives the worktree. Both agents independently
    # improvised this same location; two agents guessing alike is luck, not a protocol.
    $planDest = Join-Path $QueueDir "$Id.plan.md"
    Copy-Item -Path $TestPlan -Destination $planDest -Force
    $line = Resolve-WorkLine
    $sha = (Invoke-GitCapture @("rev-parse", $Branch) | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $sha) { Die "branch '$Branch' not found" }

    # THE HOOKS MUST HAVE RUN (U5 containment parity; PLAN 0 A7 is FALSIFIED - an agent
    # reached for --no-verify on its first commit, and --no-verify leaves no trace in a git
    # object, so "the hooks ran" was unprovable). Submission is the right chokepoint: it is
    # the moment work stops being the developer's private business and becomes something a
    # tester and reviewer will trust. Checked mechanically, not asked about, because A7's
    # whole finding is that asking does not work.
    # Resolved from $PSScriptRoot, not the working directory: queue.ps1 is invoked from
    # whichever worktree an agent happens to be in, and a cwd-relative path would silently
    # miss the script - Test-Path would be false and the check would skip itself, which is
    # the exact silent no-op this guard exists to prevent.
    $harnessRepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $attestScript = Join-Path $harnessRepoRoot "scripts\checks\check-hook-attestation.ps1"
    if (Test-Path $attestScript) {
        $attestOut = & $attestScript -Branch $Branch -Base $line -RepoRoot $harnessRepoRoot -Json
        $attestExit = $LASTEXITCODE
        if ($attestExit -eq 1) {
            $report = $null
            try { $report = $attestOut | ConvertFrom-Json } catch { }
            $offenders = if ($report) {
                (@($report.unattested) | ForEach-Object { "    $($_.sha.Substring(0,8))  $($_.subject)" }) -join "`n"
            } else { "    (could not parse the checker's report)" }
            Die (("'{0}' has commit(s) the pre-commit hooks never validated:`n{1}`n`n" +
                  "The hooks are the secret guard, the line-ending rule, the LLM-gateway " +
                  "routing rule and the compose/ps1 structural check. Each exists because it " +
                  "caught a real failure, and --no-verify skips all four while leaving no " +
                  "trace in the repo - which is why this is checked here rather than trusted.`n`n" +
                  "REMEDY - re-commit the same content so the hooks run:`n" +
                  "    git commit --amend --no-edit                      # the tip commit`n" +
                  "    git rebase --exec 'git commit --amend --no-edit' {2}`n`n" +
                  "Run scripts\checks\check-hook-attestation.ps1 -Branch {0} -Base {2} for the " +
                  "full explanation, including the two innocent causes.") -f $Branch, $offenders, $line) 4
        }
    }
    if ($existing) {
        $item = $existing
        Set-Field $item "branch" $Branch; Set-Field $item "line" $line
        Set-Field $item "developer" $Developer
        Set-Field $item "state" "ready-to-test"; Set-Field $item "test_plan" $planDest
        if ($Thread) { Set-Field $item "thread" $Thread }
        Set-Field $item "line_mergeable" (-not (Test-LineCheckedOutElsewhere -Line $line))
        Set-Field $item "submitted_sha" $sha.Trim()
    } else {
        $item = [ordered]@{
            id = $Id; branch = $Branch; line = $line; developer = $Developer
            state = "ready-to-test"; anchor = $null; anchor_file = ""
            anchor_confirmed_by = ""; anchor_confirmed_at = 0; gates = (Get-EmptyGateMap)
            test_plan = $planDest; thread = $Thread; attempt = 1
            line_mergeable = (-not (Test-LineCheckedOutElsewhere -Line $line))
            submitted_sha = $sha.Trim(); tested_at_sha = ""; merged_sha = ""
            results = @(); history = @()
        }
    }
    Add-History $item "submitted for testing" $Developer
    Write-Item $item
    Write-Host ("Queued '{0}' for TESTING (branch {1} -> {2})." -f $Id, $Branch, $line) -ForegroundColor Green
    Write-Host ("  Plan copied to {0}" -f $planDest)
    Write-Host "  A tester who is NOT the developer must claim and execute the plan."
    if (-not $item.line_mergeable) {
        Write-Host ("  NOTE: '{0}' is checked out in the main checkout, so the reviewer will have to" -f $line) -ForegroundColor Yellow
        Write-Host "        hand the merge back to the operator. Known now rather than at landing time." -ForegroundColor Yellow
    }
    if (-not (Test-KnownAgent $Developer)) {
        Write-Host ("  WARNING: '{0}' matches no worktree in the registry. Separation of duties is a" -f $Developer) -ForegroundColor Yellow
        Write-Host "           name comparison - a mistyped id silently weakens it." -ForegroundColor Yellow
    }
    exit 0
}

# --- claim --------------------------------------------------------------------------
if ($Claim) {
    if (-not $Id -or -not $Role -or -not $By) { Die "-Claim needs -Id, -Role (tester|reviewer) and -By" }
    if (-not $RoleRules.Contains($Role)) { Die ("-Role must be one of: " + ($RoleRules.Keys -join ", ")) }
    $item = Read-Item $Id

    # SEPARATION OF DUTIES, enforced rather than trusted. The developer may not test or
    # review their own work: a rule an agent has to remember is a rule that gets skipped
    # at 2am by the agent most convinced it is fine.
    if ((Normalize-Id $By) -eq (Normalize-Id $item.developer)) {
        Die ("$Role of '$Id' cannot be its developer ($By). Someone else must " +
             $RoleRules[$Role].duty + " - that separation is the point.") 4
    }
    # Order matters: check the CLAIM before the STATE. A claimed item is already in the
    # in-progress state, so checking state first told a waiting agent "wrong state" (exit 1)
    # when the true answer is "someone else has it, go find another item" (exit 3).
    $want = $RoleRules[$Role].ready
    $busy = $RoleRules[$Role].busy
    $c = ClaimPath $Id $Role
    if (-not (Test-Path $c) -and $item.state -notin @($want, $busy)) {
        Die "'$Id' is '$($item.state)', not '$want' - nothing for a $Role to claim"
    }
    if (Test-Path $c) {
        $held = Get-Content -Raw -Path $c | ConvertFrom-Json
        $age = (Now) - [int64]$held.at
        if ($age -lt ([int]$held.ttl_min * 60)) {
            Write-Host ("'{0}' is already claimed by {1} ({2}m ago) - pick up another item." -f $Id, $held.by, [int]($age / 60)) -ForegroundColor Yellow
            exit 3
        }
        Write-Host ("Taking over an EXPIRED {0} claim from {1}." -f $Role, $held.by) -ForegroundColor Yellow
        Remove-Item $c -Force
    }
    $payload = ([ordered]@{ by = $By; at = Now; ttl_min = $ClaimTtlMin } | ConvertTo-Json -Depth 3)
    try {
        $fs = [System.IO.File]::Open($c, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $b = [System.Text.Encoding]::ASCII.GetBytes($payload); $fs.Write($b, 0, $b.Length) } finally { $fs.Close() }
    } catch [System.IO.IOException] {
        Write-Host "another agent claimed it a moment ago." -ForegroundColor Yellow
        exit 3
    }
    # Tester and reviewer being the SAME agent is weaker than two independent sets of
    # eyes, but it is not the rule the operator named (developer must not merge their own
    # work). Warned, not refused - and recorded, so a pattern of it is visible later.
    if ($Role -eq "reviewer") {
        $priorTester = @($item.results | Where-Object { $_.verdict -eq "pass" } | Select-Object -Last 1).by
        if ($priorTester -eq $By) {
            Write-Host "  NOTE: you also tested this item. Allowed (you are not the developer), but a" -ForegroundColor Yellow
            Write-Host "        different reviewer is stronger - hand it over if one is available." -ForegroundColor Yellow
            Add-History $item "reviewer is also the tester" $By
        }
    }
    $item.state = $RoleRules[$Role].busy
    Add-History $item "claimed as $Role" $By
    Write-Item $item
    Write-Host ("Claimed '{0}' as {1} ({2})." -f $Id, $Role, $By) -ForegroundColor Green
    if ($item.anchor) {
        Write-Host ""
        Write-Host "  --- WHAT THIS IS FOR (the confirmed anchor) ---" -ForegroundColor Cyan
        foreach ($ln in (Format-Anchor $item.anchor) -split "`n") { Write-Host ("  " + $ln) }
        Write-Host ""
    }
    if ($Role -eq "tester") {
        # Tell the tester what to test, rather than leaving them to infer it from a
        # recorded sha. `submitted_sha` is a RECORD of an attempt, not an instruction -
        # reading it as one is how a tester ends up re-testing an already-failed commit.
        $tip = (Invoke-GitCapture @("rev-parse", "--short", $item.branch) | Select-Object -First 1)
        Write-Host ("  Test {0} at its CURRENT tip: {1} (attempt {2})" -f $item.branch, $tip, $item.attempt)
        Write-Host ("  Execute the plan: {0}" -f $item.test_plan)
        Write-Host "  Touching a plane's RUNNING services? Hold its lease first (lease.ps1)."
    } else {
        Write-Host ("  Review the diff: git diff {0}...{1}" -f $item.line, $item.branch)
        Write-Host ("  Tests passed at {0}; if your rebase moves it, send it BACK to test." -f $item.tested_at_sha)
    }
    exit 0
}

if ($Unclaim) {
    if (-not $Id -or -not $Role) { Die "-Unclaim needs -Id and -Role" }
    $item = Read-Item $Id
    Drop-Claim $Id $Role
    $item.state = $RoleRules[$Role].ready
    Add-History $item "released the $Role claim" $By
    Write-Item $item
    Write-Host ("Released the {0} claim on '{1}'." -f $Role, $Id) -ForegroundColor Green
    exit 0
}

# --- test verdicts ------------------------------------------------------------------
if ($Pass -or $Fail) {
    if (-not $Id -or -not $By) { Die "-Pass/-Fail need -Id and -By" }
    $item = Read-Item $Id
    Assert-Claim $item "tester" $By
    # The plan judgement must be STATED, not defaulted. It used to be a lone switch, so a
    # tester who simply forgot recorded `false` - identical to a considered "this plan is
    # inadequate" - and the nudge only fired AFTER the verdict was already written. A
    # tester reported that; it is the same class as an empty evidence field.
    if ($PlanAdequate -and $PlanInadequate) { Die "-PlanAdequate and -PlanInadequate are contradictory" }
    if (-not ($PlanAdequate -or $PlanInadequate)) {
        Die ("state a plan judgement: -PlanAdequate, or -PlanInadequate with what it should " +
             "have covered. The plan was written by the developer - whether it was good enough " +
             "to find what you found is part of your verdict, not an afterthought.")
    }
    if (-not $Evidence) {
        # Required on BOTH verdicts. It used to be pass-only, so two testers had to cram
        # paragraphs of findings into -Reason and the recorded evidence came out empty -
        # on the FAIL path, which is exactly when the next person needs it most.
        Die "-Pass and -Fail both need -Evidence (what you ran and what it produced). A verdict without evidence is an opinion."
    }
    # Long evidence does not fit a PS5.1 argument. Accept a FILE and copy it beside the
    # item, the same way the test plan is stored.
    $evidenceText = $Evidence
    if (Test-Path $Evidence) {
        $evDest = Join-Path $QueueDir ("{0}.attempt{1}.evidence.md" -f $Id, $item.attempt)
        Copy-Item -Path $Evidence -Destination $evDest -Force
        $evidenceText = $evDest
    }
    if ($Fail -and -not $Reason) {
        Die ("-Fail needs -Reason: name the CASE that failed and what it revealed. The " +
             "developer fixes the finding, not the verdict.")
    }
    $headSha = (Invoke-GitCapture @("rev-parse", $item.branch) | Select-Object -First 1)
    $item.results += [ordered]@{
        at = Now; by = $By; verdict = $(if ($Pass) { "pass" } else { "fail" })
        sha = $headSha.Trim(); evidence = $evidenceText; reason = $Reason
        # The plan was written by the DEVELOPER. A tester who only reports pass/fail is
        # grading someone else's exam without reading the syllabus - say whether the plan
        # actually covered the change.
        plan_adequate = [bool]$PlanAdequate
    }
    if ($PlanInadequate) {
        Write-Host "  Plan marked INADEQUATE - say in your report what it should have covered." -ForegroundColor Yellow
    }
    Drop-Claim $Id "tester"
    if ($Pass) {
        # STOP at the human gate. Tests being green says the code does what the plan said;
        # it does not say the operator still wants it, or wants it THIS way. Review is the
        # last cheap moment to change course, so the operator releases it, not the tester.
        $item.state = "test-passed"
        $item.tested_at_sha = $headSha.Trim()
        Add-History $item "tests PASSED (attempt $($item.attempt))" $By
        Write-Host ("'{0}' PASSED at {1} on attempt {2}." -f $Id, $item.tested_at_sha.Substring(0, 8), $item.attempt) -ForegroundColor Green
        $gd = Resolve-GateOrDie "pre_review"
        if ($gd.passer -eq "auto") {
            # THE PRE-REVIEW GATE, unattended. The tester's verdict is already written
            # (state test-passed) BEFORE the gate is tried, so a raise parks the item with
            # its pass intact rather than losing the test result to the halt.
            Write-Item $item
            $andon = Invoke-AutoGate $item "pre_review" $gd
            if ($andon.status -ne "clear") { Stop-OnAndon $andon "pre_review" $Id "test-passed" }
            $autoWho = (Get-AutoPrincipalPrefix) + $gd.profile
            $item.state = "ready-review"
            Set-ItemGate $item "pre_review" "auto" $autoWho $gd.profile
            Add-History $item "released for review AUTOMATICALLY (gate profile '$($gd.profile)') - no human saw it" $autoWho
            [void](Write-GateRecord -Item $Id -Gate "pre_review" -Decision "passed" -Kind "auto" -Principal $autoWho `
                     -GateProfile $gd.profile -FromState "test-passed" -ToState "ready-review" -Andon $andon)
            Write-Host ("  AUTO-RELEASED for review under gate profile '{0}' - NO HUMAN SAW IT." -f $gd.profile) -ForegroundColor Yellow
        } else {
            Write-Host "  It is NOT queued for review yet - the operator releases it (-Approve)." -ForegroundColor Yellow
        }
    } else {
        $item.state = "test-failed"
        # Stamp the sha on failure too. Without it a resubmit loses which commit the
        # finding was against, and `submitted_sha` gets overwritten by the next attempt.
        $item.tested_at_sha = $headSha.Trim()
        Add-History $item "tests FAILED (attempt $($item.attempt)): $Reason" $By
        Write-Host ("'{0}' FAILED on attempt {1} - back to {2}, who fixes in the same worktree and runs -Resubmit." -f $Id, $item.attempt, $item.developer) -ForegroundColor Yellow
    }
    Write-Item $item
    exit 0
}

# --- review outcomes ----------------------------------------------------------------
if ($Merged) {
    if (-not $Id -or -not $By -or -not $Sha) { Die "-Merged needs -Id, -By and -Sha (the merge commit)" }
    $item = Read-Item $Id
    Assert-Claim $item "reviewer" $By
    if ((Normalize-Id $By) -eq (Normalize-Id $item.developer)) { Die "the developer cannot merge their own work" 4 }
    # THE FITNESS VERDICT. Green tests say the artifact is CORRECT; they say nothing about
    # whether it BELONGS here. So the reviewer states it, and it cannot be defaulted - the
    # same reason the tester must state -PlanAdequate.
    #
    # RE-SCOPED 2026-08-29 (dark-factory-unification U2 / PLAN L1): this verdict was
    # -FitsAnchor, which asked the reviewer to re-judge INTENT. That is the wrong seat.
    # Intent is settled by the operator twice already - at the anchor gate before any work,
    # and at the pre-review release gate - and re-litigating it at merge time puts the
    # decision furthest from the person who owns it, at the moment it is most expensive to
    # act on. Review is for merge safety, clean code, and DIFFERENT EYES ON CODEBASE FIT.
    # An intent objection is still worth raising; it just routes back to the release gate
    # rather than being decided here.
    if ($item.anchor) {
        if (-not ($FitsCodebase -or $Misfits)) {
            Die ("state the fitness verdict: -FitsCodebase or -Misfits.`n`n" +
                 (Format-Anchor $item.anchor) +
                 "`n`nTests passing is not the question here. The question is whether what you " +
                 "are about to land BELONGS in this codebase: does it follow the house " +
                 "patterns, is it in the right module, does it leave the tree coherent?`n" +
                 "If your objection is that the anchor asked for the wrong THING, that is an " +
                 "intent challenge - it goes back to the operator at the release gate " +
                 "(-Approve), not into this verdict.")
        }
        if ($Misfits) {
            Die ("you judged that '$Id' MISFITS the codebase - that is a -Reject, not a merge. " +
                 "Say what misfits it in -Reason so the developer can aim at it.") 4
        }
    }
    # THE SHA MUST ACTUALLY CONTAIN THE BRANCH. Recorded 2026-08-29 after I ran a merge that
    # FAILED (a dirty index refused it), did not check the exit code, and then recorded
    # `-Merged` with `git rev-parse HEAD` - which was simply the pre-merge tip. The queue
    # said "merged" while nothing had merged. A pipeline whose terminal state can be reached
    # without the thing happening is worse than no pipeline, because everyone downstream
    # trusts it.
    $reach = Invoke-GitCapture @("merge-base", "--is-ancestor", $item.branch, $Sha)
    if ($LASTEXITCODE -ne 0) {
        Die ("'$Sha' does not contain '$($item.branch)' - that is not a merge of this item. " +
             "If the merge command failed, it failed silently: check its exit code before " +
             "recording the outcome. Nothing has been recorded.") 1
    }
    # Items merged before 2026-08-29 carry `fits_anchor` instead. That recorded the answer to
    # a DIFFERENT question (did this match the intent?), so do not read the two as one field.
    Set-Field $item "fits_codebase" ([bool]$FitsCodebase)
    $item.state = "merged"; $item.merged_sha = $Sha
    Add-History $item "merged as $Sha" $By
    Drop-Claim $Id "reviewer"
    Write-Item $item
    Write-Host ("'{0}' MERGED as {1} by {2}." -f $Id, $Sha, $By) -ForegroundColor Green
    Write-Host ("  {0} can now retire the worktree (remove-worktree.ps1 -Id ...)." -f $item.developer)
    exit 0
}

if ($Approve) {
    # THE HUMAN GATE (operator, 2026-08-28). Deterministic stage between "every case passed"
    # and "someone may merge this". Deliberately NOT automatic: while the cases were finding
    # and fixing things, the operator may have seen something concerning, or the world may
    # have moved. Once review starts, the next step is a merge - this is the last cheap
    # moment to change course.
    if (-not $Id -or -not $By) { Die "-Approve needs -Id and -By (who is releasing it)" }
    Assert-HumanPrincipal $By "-Approve"
    $item = Read-Item $Id
    if ($item.state -ne "test-passed") { Die "'$Id' is '$($item.state)' - only a test-passed item can be released for review" }
    if ((Normalize-Id $By) -eq (Normalize-Id $item.developer)) {
        Die "the developer cannot release their own work for review - that is the human gate, and self-service defeats it" 4
    }
    $was = $item.state
    $item.state = "ready-review"
    Set-ItemGate $item "pre_review" "human" $By (Get-GateProfileName -Requested $GateProfile)
    Add-History $item "released for review" $By
    Write-Item $item
    [void](Write-GateRecord -Item $Id -Gate "pre_review" -Decision "passed" -Kind "human" -Principal $By `
             -GateProfile (Get-GateProfileName -Requested $GateProfile) -FromState $was -ToState $item.state)
    Write-Host ("'{0}' released for REVIEW by {1}." -f $Id, $By) -ForegroundColor Green
    exit 0
}

if ($Resubmit) {
    # The iteration lap. A real task is several test cycles, not one - and forcing a new id
    # per cycle would scatter one piece of work across several items and lose its history.
    if (-not $Id -or -not $By) { Die "-Resubmit needs -Id and -By" }
    $item = Read-Item $Id
    if ($item.state -ne "test-failed") { Die "'$Id' is '$($item.state)' - only a test-failed item is re-submitted" }
    if ((Normalize-Id $By) -ne (Normalize-Id $item.developer)) { Die "only the developer ($($item.developer)) re-submits their own item" 4 }
    $item.attempt = [int]$item.attempt + 1
    $item.state = "ready-to-test"
    $item.tested_at_sha = ""
    # RE-READ THE BRANCH. This used to keep attempt 1's already-failed commit as
    # `submitted_sha`, so a tester who checked it out would test the very commit that
    # failed and re-report the identical finding - and the reviewer's staleness comparison
    # would be against a sha that never described the fix. Found by a developer agent on
    # its own resubmit, which is the first moment the bug is visible.
    $newSha = (Invoke-GitCapture @("rev-parse", $item.branch) | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $newSha) { Die "branch '$($item.branch)' not found - cannot re-submit" }
    $item.submitted_sha = $newSha.Trim()
    # AND RE-READ THE PLAN, when one is offered. A failed case very often means the plan
    # missed something - the protocol tells the developer to add the case, and until now
    # there was no mechanism behind that instruction: -Resubmit took no -TestPlan and left
    # the queued copy at attempt 1, so the tester re-read a plan already known to be
    # incomplete. A developer agent hit this, copied the file into place by hand, and said
    # so rather than letting it pass; the workaround is what a tool is for.
    if ($TestPlan) {
        if (-not (Test-Path $TestPlan)) {
            Die ("-TestPlan must be a path to a file that exists (got '$TestPlan')")
        }
        Copy-Item -Path $TestPlan -Destination $item.test_plan -Force
        Add-History $item "test plan revised for attempt $([int]$item.attempt)" $By
    }
    Add-History $item "re-submitted for testing (attempt $($item.attempt))" $By
    Write-Item $item
    Write-Host ("'{0}' re-submitted - attempt {1} at {2}, awaiting a tester." -f $Id, $item.attempt, $item.submitted_sha.Substring(0, 8)) -ForegroundColor Green
    if ($TestPlan) { Write-Host ("  Plan REVISED for this attempt -> {0}" -f $item.test_plan) }
    else {
        Write-Host "  Plan unchanged. If the failure showed the plan missed a case, add it and" -ForegroundColor Yellow
        Write-Host "  re-submit with -TestPlan <path> - the tester reads the queued copy, not yours." -ForegroundColor Yellow
    }
    exit 0
}

if ($Requeue) {
    # The stale-pass rule. Tests passed at `tested_at_sha`; if the reviewer's rebase moved
    # the content, that verdict no longer describes what would land. Back to test - NOT a
    # rejection, because nothing is wrong with the work.
    if (-not $Id -or -not $By -or -not $Reason) { Die "-Requeue needs -Id, -By and -Reason" }
    $item = Read-Item $Id
    Assert-Claim $item "reviewer" $By
    $item.state = "ready-to-test"
    $item.tested_at_sha = ""
    Add-History $item "returned to test: $Reason" $By
    Drop-Claim $Id "reviewer"
    Write-Item $item
    Write-Host ("'{0}' returned to TESTING - {1}" -f $Id, $Reason) -ForegroundColor Yellow
    exit 0
}

if ($Reject) {
    if (-not $Id -or -not $By -or -not $Reason) { Die "-Reject needs -Id, -By and -Reason" }
    $item = Read-Item $Id
    Assert-Claim $item "reviewer" $By
    # Recorded because the two rejections mean different things to whoever picks this up:
    # "it does not work" is a fix, "it does not belong here like this" is a re-shape.
    if ($Misfits) { Set-Field $item "fits_codebase" $false }
    $item.state = "rejected"
    Add-History $item "rejected: $Reason" $By
    Drop-Claim $Id "reviewer"
    Write-Item $item
    Write-Host ("'{0}' REJECTED - {1} keeps the worktree and opens a new item when addressed." -f $Id, $item.developer) -ForegroundColor Yellow
    exit 0
}

Die "pass one of -Submit | -Resubmit | -List | -Show | -Claim | -Unclaim | -Pass | -Fail | -Approve | -Merged | -Requeue | -Reject | -Audit | -VerifyAudit"
