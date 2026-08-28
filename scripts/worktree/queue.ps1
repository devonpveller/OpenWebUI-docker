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
#   .\queue.ps1 -Submit -Id mem-readme -Branch work/mem-readme -Developer wt-mem-readme -TestPlan <path>
#   .\queue.ps1 -List
#   .\queue.ps1 -Claim -Id mem-readme -Role tester -By wt-tester-1
#   .\queue.ps1 -Pass  -Id mem-readme -By wt-tester-1 -Evidence <path> -PlanAdequate
#   .\queue.ps1 -Fail  -Id mem-readme -By wt-tester-1 -Reason "case 3 fails on a cold cache"
#   .\queue.ps1 -Resubmit -Id mem-readme -By wt-mem-readme          # after a -Fail: next lap
#   .\queue.ps1 -Approve -Id mem-readme -By profnovice               # THE HUMAN GATE
#   .\queue.ps1 -Claim -Id mem-readme -Role reviewer -By wt-reviewer-1
#   .\queue.ps1 -Merged -Id mem-readme -By wt-reviewer-1 -Sha <merge sha>
#
# Exit codes: 0 ok | 1 usage/state error | 3 claimed by someone else | 4 refused (duties)

[CmdletBinding()]
param(
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
    [string]$Id = "",
    [string]$Branch = "",
    [string]$Developer = "",
    [string]$TestPlan = "",
    [string]$Role = "",
    [string]$By = "",
    [string]$Evidence = "",
    [string]$Reason = "",
    [string]$Sha = "",
    [string]$Thread = "",
    [string]$State = "",
    [switch]$PlanAdequate,
    [int]$ClaimTtlMin = 60
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

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

# --- list / show --------------------------------------------------------------------
if ($List) {
    $items = @(Get-ChildItem -Path $QueueDir -Filter "*.json" -ErrorAction SilentlyContinue)
    if (-not $items.Count) { Write-Host "queue empty" -ForegroundColor Green; exit 0 }
    Write-Host ("{0,-18} {1,-14} {2,-20} {3}" -f "ID", "STATE", "DEVELOPER", "HELD BY")
    foreach ($f in ($items | Sort-Object Name)) {
        $it = Get-Content -Raw -Path $f.FullName | ConvertFrom-Json
        if ($State -and $it.state -ne $State) { continue }
        $held = ""
        foreach ($r in @("tester", "reviewer")) {
            $c = ClaimPath $it.id $r
            if (Test-Path $c) { $held = "$r=" + (Get-Content -Raw -Path $c | ConvertFrom-Json).by }
        }
        $flag = if ($it.PSObject.Properties.Name -contains "line_mergeable" -and -not $it.line_mergeable) { " [needs hand-off]" } else { "" }
        Write-Host ("{0,-18} {1,-14} {2,-20} {3}{4}" -f $it.id, $it.state, $it.developer, $held, $flag)
    }
    exit 0
}

if ($Show) {
    if (-not $Id) { Die "-Show needs -Id" }
    (Read-Item $Id) | ConvertTo-Json -Depth 8
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
    if (Test-Path (ItemPath $Id)) { Die "queue item '$Id' already exists (use a new -Id, or -Show it)" }
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
    $item = [ordered]@{
        id = $Id; branch = $Branch; line = $line; developer = $Developer
        state = "ready-to-test"; test_plan = $planDest; thread = $Thread; attempt = 1
        line_mergeable = (-not (Test-LineCheckedOutElsewhere -Line $line))
        submitted_sha = $sha.Trim(); tested_at_sha = ""; merged_sha = ""
        results = @(); history = @()
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
    if ($Role -eq "tester") {
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
    if ($Pass -and -not $Evidence) { Die "-Pass needs -Evidence (what you ran and what it produced)" }
    if ($Fail -and -not $Reason) {
        Die ("-Fail needs -Reason: name the CASE that failed and what it revealed. The " +
             "developer fixes the finding, not the verdict.")
    }
    $headSha = (Invoke-GitCapture @("rev-parse", $item.branch) | Select-Object -First 1)
    $item.results += [ordered]@{
        at = Now; by = $By; verdict = $(if ($Pass) { "pass" } else { "fail" })
        sha = $headSha.Trim(); evidence = $Evidence; reason = $Reason
        # The plan was written by the DEVELOPER. A tester who only reports pass/fail is
        # grading someone else's exam without reading the syllabus - say whether the plan
        # actually covered the change.
        plan_adequate = [bool]$PlanAdequate
    }
    if ($Pass -and -not $PlanAdequate) {
        Write-Host "  NOTE: you did not mark the plan adequate - the reviewer will see that and may send it back." -ForegroundColor Yellow
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
        Write-Host "  It is NOT queued for review yet - the operator releases it (-Approve)." -ForegroundColor Yellow
    } else {
        $item.state = "test-failed"
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
    $item = Read-Item $Id
    if ($item.state -ne "test-passed") { Die "'$Id' is '$($item.state)' - only a test-passed item can be released for review" }
    if ((Normalize-Id $By) -eq (Normalize-Id $item.developer)) {
        Die "the developer cannot release their own work for review - that is the human gate, and self-service defeats it" 4
    }
    $item.state = "ready-review"
    Add-History $item "released for review" $By
    Write-Item $item
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
    Add-History $item "re-submitted for testing (attempt $($item.attempt))" $By
    Write-Item $item
    Write-Host ("'{0}' re-submitted - attempt {1}, awaiting a tester." -f $Id, $item.attempt) -ForegroundColor Green
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
    $item.state = "rejected"
    Add-History $item "rejected: $Reason" $By
    Drop-Claim $Id "reviewer"
    Write-Item $item
    Write-Host ("'{0}' REJECTED - {1} keeps the worktree and opens a new item when addressed." -f $Id, $item.developer) -ForegroundColor Yellow
    exit 0
}

Die "pass one of -Submit | -Resubmit | -List | -Show | -Claim | -Unclaim | -Pass | -Fail | -Approve | -Merged | -Requeue | -Reject"
