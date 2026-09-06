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
#   merged         landed by the reviewer. TERMINAL unless the merge derived deploy
#                  surfaces (an OB1 integration image, a :local build context, an owui/ file
#                  OWUI only sees by paste) - then -List shows [UNDEPLOYED: ...] until each is
#                  closed by -Deployed with health evidence. The list is DERIVED from the
#                  merge range at -Merged, never typed by the author (deploystate, 2026-09-06).
#   deployed       every derived surface closed with evidence (terminal). Deploy itself stays
#                  human-gated (MERGE-PROTOCOL section 4): -Deployed records one, never performs one.
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
#     -Evidence takes a FILE PATH or inline text. Prefer the path: an over-long inline
#     string can blow the process command-line limit, and that failure happens BEFORE
#     PowerShell starts - exit 2, no message, nothing this script can catch (observed
#     2026-09-03, wiki-mirror-hardening: the retry then consumed the claim). Inline
#     text over 2000 chars is auto-spilled to the item's evidence file, but text long
#     enough to kill the process never reaches us - write the file yourself.
#   .\queue.ps1 -Fail  -Id mem-readme -By wt-tester-1 -Reason "case 3 fails on a cold cache"
#   .\queue.ps1 -Resubmit -Id mem-readme -By wt-mem-readme [-TestPlan <path>]  # after a -Fail
#   .\queue.ps1 -Requeue -Id mem-readme -By wt-mem-readme -Reason "..."
#     (THE DEVELOPER'S way back from 'test-passed' when the ARTIFACT itself must change.
#      Lands on 'anchor-confirmed' - the same "back with the developer" state -AmendAnchor
#      uses - so the way forward is the ordinary -Submit. The REVIEWER's -Requeue is the
#      stale-pass rule and goes to 'ready-to-test'. Both bump `attempt`, and both take an
#      optional -TestPlan.)
#   .\queue.ps1 -Approve -Id mem-readme -By profnovice               # THE HUMAN GATE
#   .\queue.ps1 -Claim -Id mem-readme -Role reviewer -By wt-reviewer-1
#   .\queue.ps1 -Merged -Id mem-readme -By wt-reviewer-1 -Sha <merge sha>
#   .\queue.ps1 -Deployed -Id mem-readme -By profnovice -Evidence <path> [-Surface image:openbrain-curator]
#     (closes the surfaces -Merged derived; -By is a person - the auto: namespace is refused)
#
# Exit codes: 0 ok | 1 usage/state error | 2 harness disabled | 3 claimed by someone else
#             | 4 refused (duties) | 5 refused (no confirmed anchor) | 6 ANDON not clear.
#               EVERY non-`clear` board word lands here, and the list is the board's, not a
#               copy that drifts: raised / warned / indeterminate / unaccounted / incomplete
#               / partial / not-evaluated, plus `unavailable` when the board could not be
#               run at all. This enumeration omitted `warned` until 2026-08-30 and read as
#               though a warn-declared fire were not an exit-6 refusal; the code has always
#               refused anything that is not the literal word `clear` (Invoke-AutoGate).
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
    [switch]$Deployed,
    [switch]$Reject,
    [switch]$Requeue,
    [switch]$Approve,
    [switch]$Resubmit,
    [switch]$Unclaim,
    [switch]$ScopeNodes,
    [switch]$Oracle,
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
    # -Deployed: close ONE derived surface (image:<svc> / paste:<path>) instead of all of them.
    [string]$Surface = "",
    # NOT -Profile: $Profile is a PowerShell automatic variable (the profile script path),
    # and a param of that name shadows it for the whole script scope: a script declaring
    # `param([string]$Profile)` sees "" inside, not the profile path. The name also reads
    # better, since what it selects is which RUNNER each role executes on.
    [string]$RunnerProfile = "",
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

# The states nothing moves out of. Named ONCE: the hand-off flag, -CloseOut and -AmendAnchor
# each used to carry their own list, and `deployed` (2026-09-06) would have been a fourth
# place to forget. `closed-outside-gates` is terminal too - the work landed, this queue did not
# adjudicate it (see -CloseOut).
$TerminalStates = @("merged", "deployed", "rejected", "closed-outside-gates")

$QueueDir = Join-Path (Get-SharedStateDir) "queue"
if (-not (Test-Path $QueueDir)) { New-Item -ItemType Directory -Force -Path $QueueDir | Out-Null }

function Now() { return [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds() }
function ItemPath([string]$i) { return (Join-Path $QueueDir "$i.json") }
function ClaimPath([string]$i, [string]$r) { return (Join-Path $QueueDir "$i.$r.claim") }
function Die([string]$m, [int]$code = 1) { Write-Host "ERROR: $m" -ForegroundColor Red; exit $code }

function Read-Item([string]$i) {
    $p = ItemPath $i
    if (-not (Test-Path $p)) { Die "no queue item '$i'" }
    return (Read-ItemFile $p)
}

function Read-ItemFile([string]$p) {
    # UTF-8, stated. PS5.1's Get-Content default is the ANSI code page, which would read the
    # UTF-8 bytes Write-Item now produces as three characters per em-dash.
    return (Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json)
}

function Write-Item($item) {
    $p = ItemPath $item.id
    $tmp = "$p.tmp"
    # UTF-8 WITHOUT a BOM (passplan attempt 2, 2026-09-06). This was `Set-Content -Encoding
    # ASCII`, and PS5.1's ConvertTo-Json does not escape non-ASCII, so every em-dash in a
    # recorded per-case `line` - which is to say every real evidence heading - was stored as
    # `?`. A record that exists to quote what the tester wrote cannot drop their characters.
    # The ASCII rule in CLAUDE.md is for scripts PS5.1 PARSES, not for data files; a BOM-less
    # UTF-8 file is what Python's json.load (the bridge, oracle_on_stall.py) and the
    # -Encoding UTF8 readers here both expect.
    [System.IO.File]::WriteAllText($tmp, ($item | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
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
    # A registry with NO ROWS is 'cannot check', exactly like a missing one - and the two
    # lines above only catch the missing and the null spellings. The empty one is what
    # ORDINARY CLEANUP LEAVES BEHIND: remove-worktree.ps1 rewrites the whole file from a
    # hashtable, so retiring the LAST worktree - or -PruneRegistry dropping the last dead
    # row - writes {"worktrees":{}}: present, non-null, and empty. `-not $rows` is FALSE for
    # that object, so the check fell through to a membership test against nothing and
    # refused EVERY developer, including agents who never owned a worktree. Correct cleanup
    # bricked -Submit for the whole queue, telling each victim to provision a worktree they
    # already had.
    #
    # The guard belongs HERE and not in remove-worktree.ps1: a hand-edited registry, an
    # external prune, or a fresh state dir seeded with an empty object all produce the same
    # file, and only this function decides what it means. Nothing is given away - a registry
    # naming nobody excludes nobody, which is the same position as no registry at all. One
    # real row and the membership test below enforces again.
    #
    # Count the PROPERTIES, not the projected names. PS5.1 member enumeration over an empty
    # collection returns $null rather than nothing, so `@($rows.PSObject.Properties.Name)`
    # on an empty object is a ONE-element array holding $null and its .Count is 1 - a guard
    # written that way looks right, reads right, and does not fire. Found by running it.
    $props = @($rows.PSObject.Properties)
    if ($props.Count -eq 0) { return $true }
    $known = @($props.Name | ForEach-Object { Normalize-Id $_ })
    return ($known -contains (Normalize-Id $who))
}

function Drop-Claim([string]$i, [string]$role) {
    $c = ClaimPath $i $role
    if (Test-Path $c) { Remove-Item $c -Force }
}

function Copy-IntoQueue([string]$Source, [string]$Destination, [string]$Flag, [string]$What) {
    # THE ONE PLACE a caller-supplied path is copied into the queue directory.
    #
    # Every such copy is `<something the caller typed>` -> `<QueueDir>\<id>....`, and if the
    # caller typed the destination itself, Copy-Item refuses to overwrite a file with itself
    # and THROWS. Under this script's ErrorActionPreference=Stop that ends the run - so the
    # verdict, the plan or the anchor is not recorded, and what the operator sees is
    # "Copy-Item : Cannot overwrite the item ... with itself", which names a cmdlet and not a
    # cause. Nobody reads that as "your pass was not recorded".
    #
    # Found twice on 2026-09-04, in two different commands (-Pass -Evidence, and -Submit
    # -TestPlan) by two different agents, which is what makes it a CLASS rather than a bug:
    # writing your evidence or your plan straight into the queue dir is a reasonable thing to
    # do, because that is visibly where it ends up. So the guard lives here, once, and every
    # copy site goes through it - a second copy of this reasoning in a third command is a
    # third chance to get it wrong.
    #
    # It refuses BEFORE copying and before any caller writes the item, so a refusal never
    # leaves half a mutation behind. Compared on resolved full paths: `.\x.md` and
    # `C:\...\x.md` are the same file, and a guard that only catches the spelling the
    # reporter happened to use is not a guard.
    $srcFull = $Source
    try { $srcFull = (Resolve-Path -LiteralPath $Source -ErrorAction Stop).Path } catch { }
    $dstFull = $Destination
    try { $dstFull = [System.IO.Path]::GetFullPath($Destination) } catch { }
    if ($srcFull -eq $dstFull) {
        Die (("{0} points at the queue's own {1} ({2}) - the very file this command COPIES " +
              "it to. A file cannot be copied onto itself, so nothing has been recorded and " +
              "nothing has changed. Keep your own copy OUTSIDE {3} and pass that path; this " +
              "command puts it here for you.") -f $Flag, $What, $dstFull, $QueueDir)
    }
    Copy-Item -LiteralPath $srcFull -Destination $dstFull -Force
}

# --- the plan's cases, and what the evidence says about each (passplan, 2026-09-06) ------
#
# WHY. Item curator2 (2026-09-04) merged on a -Pass whose evidence file carried, on two of
# its eight case headings, `PASS (scoped - read the caveat)` and `PASS (blocking path
# driven; chat half NOT run)`. The tester wrote the truth on the heading line; the tool never
# read it. -Pass opened neither the plan nor the evidence - the only thing between a scoped
# case and a green item was the tester's willingness to type -Fail instead, and the image
# those two cases existed to exercise had never been built. So the tool reads both now.
#
# A CASE is a Markdown H2 that starts with the case id - `## T3 - ...` or `## Case 3 - ...`,
# the two shapes every real plan in the queue already uses. A case PASSES only when the
# evidence carries a heading with the same id whose LAST word is the bare token PASS.
# Anything after PASS - a parenthetical, a dash, a caveat - is the tester telling us it was
# not a pass, and the tool now agrees with them. A case the tester could not execute is a
# plan inadequacy (-Fail -PlanInadequate), never a scoped pass.
#
# The plan is also hashed at -Submit (and re-hashed by the two plan-revision doors), and
# -Pass refuses when the queued file no longer hashes to what was submitted: a pass
# describes the cases that were agreed, and a plan that changed underneath is not those.
$CaseHeadingPattern = '^##\s+(T\d+|Case\s+\d+)\b'
$CaseHeadingShape = ("a case heading is a Markdown H2 that STARTS with the case id - " +
                     "'## T0 - what it checks' or '## Case 1 - what it checks' - the id first, " +
                     "then anything")

function Read-Utf8Text([string]$path) {
    # Evidence carries em-dashes and the like. PS5.1's Get-Content default is the ANSI code
    # page, which turns those into several characters and can move what "the last word" is.
    return [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
}

function Get-PlanSha256([string]$path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLower()
}

function Normalize-CaseId([string]$raw) {
    # `T5`, `t5`, `Case 5`, `case  5` are one case: the plan's spelling and the evidence's
    # must compare equal, and a refusal must not fire on capitalisation.
    $r = ($raw -replace '\s+', ' ').Trim()
    if ($r -match '^[Tt](\d+)$') { return ("T" + $matches[1]) }
    if ($r -match '^[Cc][Aa][Ss][Ee] (\d+)$') { return ("Case " + $matches[1]) }
    return $r
}

function Get-FenceWalk([string]$text) {
    # ONE walk over a Markdown document that answers two questions: which lines are OUTSIDE
    # fenced code blocks (``` or ~~~, indented up to three SPACES, any info string), and
    # whether a fence was left open. A `## T2 ... PASS` quoted inside a fence - the
    # protocol's own example pasted into evidence, say - is a quotation, not a verdict; a
    # fenced `## T9` in a plan is not a case. Found by the tester of attempt 1: without this
    # a fenced heading satisfied a plan case, and a plan whose only heading was fenced was
    # accepted at -Submit. The fence lines themselves are dropped too.
    #
    # Two edges decided by passplan's attempt-2 tester (carried to deploystate): a
    # TAB-indented ``` is NOT a fence - CommonMark reads a tab as four columns, which is an
    # indented code line - so the pattern says ' {0,3}' and not '\s{0,3}'; and an UNTERMINATED
    # fence swallows every line after it, which in a PLAN silently shrinks the enforced case
    # list. That is CommonMark-correct and stays so, but it is reported: `open_at` is the
    # 1-based line of the fence still open at the end of the text, 0 when none is.
    $out = New-Object System.Collections.ArrayList
    $fence = ""; $openAt = 0; $n = 0
    foreach ($line in ($text -split "`r?`n")) {
        $n++
        if ($line -match '^ {0,3}(`{3,}|~{3,})') {
            $marker = $matches[1].Substring(0, 1)
            if (-not $fence) { $fence = $marker; $openAt = $n; continue }
            if ($fence -eq $marker) { $fence = ""; $openAt = 0; continue }
        }
        if (-not $fence) { [void]$out.Add($line) }
    }
    return @{ lines = @($out); open_at = $openAt }
}

function Get-UnfencedLines([string]$text) {
    return @((Get-FenceWalk $text).lines)
}

function Get-UnterminatedFenceLine([string]$text) {
    return [int](Get-FenceWalk $text).open_at
}

function Get-PlanCases([string]$text) {
    # The plan's case ids, in plan order, each once. Fenced blocks count for nothing.
    $ids = New-Object System.Collections.ArrayList
    foreach ($line in (Get-UnfencedLines $text)) {
        if ($line -match $CaseHeadingPattern) {
            $id = Normalize-CaseId $matches[1]
            if (-not $ids.Contains($id)) { [void]$ids.Add($id) }
        }
    }
    return @($ids)
}

function Get-EvidenceVerdicts([string]$text) {
    # One row per case heading in the evidence: {case, verdict, line}. The verdict is PASS
    # only when the heading ENDS in the bare token PASS (case-sensitive - `Pass` and `pass`
    # are prose, not verdicts). Otherwise it is the tail of the line from the first
    # verdict-like word, so a refusal can quote what the tester actually wrote; a heading
    # that carries no verdict word says so. A case that appears on several headings (a RED
    # mutation and a GREEN run, say) yields several rows, and every one of them must pass.
    # Fenced blocks count for nothing - see Get-UnfencedLines.
    $rows = @()
    foreach ($line in (Get-UnfencedLines $text)) {
        if ($line -match $CaseHeadingPattern) {
            $id = Normalize-CaseId $matches[1]
            $trimmed = $line.TrimEnd()
            $verdict = "(no verdict on the heading line)"
            if ($trimmed -cmatch '(^|\s)PASS$') { $verdict = "PASS" }
            elseif ($trimmed -cmatch '(^|\s)(PASS|FAIL|FAILED|SKIP|SKIPPED|SCOPED|PARTIAL|BLOCKED|DEFERRED|NOT RUN|N/A)\b.*$') {
                $verdict = $matches[0].Trim()
            }
            $rows += [ordered]@{ case = $id; verdict = $verdict; line = $trimmed }
        }
    }
    return @($rows)
}

function Compare-EvidenceToPlan([string[]]$planCases, $rows) {
    # Returns @{ ok; problems; cases }. `cases` is what results[] stores: every evidence row
    # in evidence order, plus a MISSING row for each plan case the evidence never names.
    # `problems` is one line per case that stops a pass, quoting the verdict as written.
    $problems = @()
    $cases = @()
    foreach ($r in @($rows)) {
        $cases += $r
        if ($r.verdict -ne "PASS") { $problems += ("{0}: {1}" -f $r.case, $r.verdict) }
    }
    $seen = @(@($rows) | ForEach-Object { $_.case })
    foreach ($c in @($planCases)) {
        if ($seen -notcontains $c) {
            $problems += ("{0}: MISSING - no '## {0}' heading in the evidence at all" -f $c)
            $cases += [ordered]@{ case = $c; verdict = "MISSING"; line = "" }
        }
    }
    return @{ ok = (@($problems).Count -eq 0); problems = @($problems); cases = @($cases) }
}

function Assert-PlanReadable([string]$path, [string]$flag) {
    # A plan the parser cannot read must not be able to produce a pass by having nothing to
    # check - so it is refused at the door it arrives through, not discovered at -Pass by a
    # tester who has already done the work.
    $text = Read-Utf8Text $path
    $cases = Get-PlanCases $text
    $openAt = Get-UnterminatedFenceLine $text
    if (@($cases).Count -eq 0) {
        $fenceHint = ""
        if ($openAt -gt 0) { $fenceHint = (" A code fence opened at line {0} is never closed, so everything after it is inside a fence and counts for nothing." -f $openAt) }
        Die (("{0} '{1}' has no case headings a verdict can be checked against: {2}.{3} Every " +
              "case the tester must execute is one such heading, and -Pass is refused unless " +
              "the evidence carries each of them with PASS as the last word on its heading " +
              "line. Nothing has been recorded.") -f $flag, $path, $CaseHeadingShape, $fenceHint)
    }
    if ($openAt -gt 0) {
        # THE ONE FENCE SHAPE THAT REDUCES WHAT A PASS MUST PROVE. A fence that never closes
        # hides every heading after it, and the only signal used to be the case count on the
        # -Submit line. Warned, not refused: the plan may genuinely end in a fenced block. But
        # it is warned BY LINE NUMBER at every door a plan enters through, with the list the
        # tool will actually enforce, so a shorter-than-it-reads plan is never a surprise.
        Write-Host ("  WARNING: {0} '{1}' opens a code fence at line {2} that is never closed. Every line after it is" -f $flag, $path, $openAt) -ForegroundColor Yellow
        Write-Host ("           inside the fence and counts for nothing, so the enforced case list may be SHORTER than the plan reads.") -ForegroundColor Yellow
        Write-Host ("           Recognised {0} case(s): {1}. Close the fence if those are not all the cases the tester must execute." -f @($cases).Count, (@($cases) -join ", ")) -ForegroundColor Yellow
    }
    return @($cases)
}

# --- what a merge SHIPS, and whether the queue's commits exist (deploystate, 2026-09-06) ---
#
# WHY. Three things the board could not say. (1) `merged` read as finished, and for an item
# that bumped an OB1 image or changed a file OWUI only sees by paste it is not - the reviewer's
# merge message said "must be after deploy" and that sentence had nowhere to land. (2) A row
# whose recorded commit, or whose OB1 pin at that commit, exists in no clone (curatorpool:
# submitted_sha f71772b pins OB1 22f41b6, a commit nobody holds) read as live work. (3)
# [needs hand-off] was set at -Submit and never cleared, so 31 of the live board's 42 rows
# wore it on 2026-09-06 and 30 of those were TERMINAL - one true instance in thirty-one - a
# flag that
# means "the reviewer cannot merge this", and the one row where it was true was invisible.
#
# THE SURFACES ARE DERIVED FROM THE MERGE RANGE, NEVER DECLARED. `git diff --name-only
# <first parent>..<merge>` is the only input: an OB1 gitlink move whose OB1 diff touches
# integrations/<dir>/ that has a Dockerfile at the new pin -> image:<compose service that
# builds ../integrations/<dir>>; a changed owui/ file THAT owui/manifest.csv LISTS ->
# paste:<path>; a changed build context of a :local-tagged service in this repository's
# compose files -> image:<service>. A list the author typed is the list the author
# remembered; this one is what git saw.
#
# THE MANIFEST IS THE AUTHORITY ON WHAT IS PASTEABLE (2026-09-06, attempt 1's tester). The
# rule was `any owui/** path`, and owui/ also holds its own README and the manifest itself:
# the real owuidrift merge e989265 derived paste:owui/manifest.csv and paste:owui/README.md,
# two surfaces nobody can ever close honestly, because neither is pasted into anything.
# owui/manifest.csv is the file -> OWUI id map, so it already answers the question exactly;
# it is read from the MERGED tree, and an owui/ path it does not list derives nothing and
# says so in a note.

function Get-ArrayField($item, [string]$name) {
    # An array field read back from JSON: `[]` comes back as an empty array, an absent field
    # as nothing, and `@($null)` has Count 1 - so every reader goes through this.
    # CONVENTION for every array-returning helper here: the function returns a PLAIN array
    # and the CALLER wraps the call in @(). A `return ,@(x)`
    # wrapped in @() by the caller is a one-element array holding the array - which is how
    # -List printed "[UNDEPLOYED: System.Object[]]" on its first run.
    if (-not ($item.PSObject.Properties.Name -contains $name)) { return @() }
    $v = $item.$name
    if ($null -eq $v) { return @() }
    return @(@($v) | Where-Object { $null -ne $_ })
}

function Get-DeployPending($item) { return @(Get-ArrayField $item "deploy_pending") }

function Convert-RepoRelative([string]$BaseDir, [string]$Rel) {
    # Combine a compose file's directory with a build context (or a context with a Dockerfile
    # name) and return the REPOSITORY-relative, forward-slash path - "" for the root, $null
    # when it escapes the repository (memory/docker-compose.yml builds ../../mnemory, which is
    # a sibling checkout, not this tree). Pure string arithmetic against a fake root: nothing
    # here touches the working directory, because the derivation reads the MERGED tree.
    $fake = "C:\__repo_root__\"
    $full = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($fake, $BaseDir, $Rel))
    # `frontend` + `..` normalises to the root WITHOUT its trailing separator (found by the
    # curatorimg replay: both frontend services read as "outside this repository").
    if ($full.TrimEnd('\') -eq $fake.TrimEnd('\')) { return "" }
    if (-not $full.StartsWith($fake, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
    return ($full.Substring($fake.Length) -replace '\\', '/').Trim('/')
}

function Remove-YamlQuotes([string]$s) {
    $t = $s.Trim()
    if ($t.Length -ge 2 -and (($t[0] -eq '"' -and $t[-1] -eq '"') -or ($t[0] -eq "'" -and $t[-1] -eq "'"))) { $t = $t.Substring(1, $t.Length - 2) }
    return $t
}

function Get-ComposeServices([string[]]$Lines) {
    # A deliberately SMALL reader of the compose shape every file in this tree is written in:
    # `services:` at column 0, one service per two-space key, `image:` and `build:` at four,
    # `context:` and `dockerfile:` at six. It is not a YAML parser, and it does not need to be
    # - what it answers is "which service builds which directory into which :local image".
    $svcs = @()
    $inServices = $false; $cur = $null; $inBuild = $false
    foreach ($raw in @($Lines)) {
        $line = ([string]$raw -replace '\s+#.*$', '').TrimEnd()
        if ($line.Trim() -eq '' -or $line -match '^\s*#') { continue }
        if ($line -match '^\S') { $inServices = ($line -match '^services:\s*$'); $cur = $null; $inBuild = $false; continue }
        if (-not $inServices) { continue }
        if ($line -match '^  ([A-Za-z0-9_.-]+):\s*$') {
            $cur = [ordered]@{ name = $matches[1]; image = ""; context = ""; dockerfile = "" }
            $svcs += $cur; $inBuild = $false; continue
        }
        if ($null -eq $cur) { continue }
        if ($line -match '^    image:\s*(.+)$') { $cur.image = Remove-YamlQuotes $matches[1]; $inBuild = $false; continue }
        if ($line -match '^    build:\s*(\S.*)$') { $cur.context = Remove-YamlQuotes $matches[1]; $inBuild = $false; continue }
        if ($line -match '^    build:\s*$') { $inBuild = $true; continue }
        if ($line -match '^    \S') { $inBuild = $false; continue }
        if ($inBuild -and $line -match '^      context:\s*(.+)$') { $cur.context = Remove-YamlQuotes $matches[1]; continue }
        if ($inBuild -and $line -match '^      dockerfile:\s*(.+)$') { $cur.dockerfile = Remove-YamlQuotes $matches[1]; continue }
    }
    return @($svcs)
}

function Get-WorktreeRegistry {
    $reg = Join-Path (Get-SharedStateDir) "worktrees.json"
    if (-not (Test-Path $reg)) { return $null }
    try { return (Get-Content -Raw -Path $reg | ConvertFrom-Json).worktrees } catch { return $null }
}

$script:RepoAnchors = $null
function Get-RepoAnchors {
    # The main checkout and the current repository's top level, asked ONCE per run: -List
    # resolves every row, and a git process per row for a fact that does not change across
    # rows is what pushed the first version of the board past its 5 s budget.
    if ($null -eq $script:RepoAnchors) {
        $top = (Invoke-GitCapture @("rev-parse", "--show-toplevel") | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0 -or -not $top) { $top = "" } else { $top = ([string]$top).Trim() }
        $script:RepoAnchors = @{ main = (Get-MainCheckout); top = $top }
    }
    return $script:RepoAnchors
}

function Get-Ob1CloneCandidates($item, $registry) {
    # The item's own worktree clone first (each worktree carries a full OB1 checkout under
    # .git/worktrees/<wt>/modules/OB1), then the main checkout's, then the OB1 beside the
    # current working directory's repository. Only directories that ARE clones are returned.
    $anchors = Get-RepoAnchors
    $cands = @()
    $dev = ""
    if ($item.PSObject.Properties.Name -contains "developer") { $dev = Normalize-Id ([string]$item.developer) }
    if ($dev -and $registry -and ($registry.PSObject.Properties.Name -contains $dev)) {
        $row = $registry.$dev
        if ($row -and ($row.PSObject.Properties.Name -contains "path") -and $row.path) { $cands += (Join-Path ([string]$row.path) "OB1") }
    }
    if ($anchors.main) { $cands += (Join-Path $anchors.main "OB1") }
    if ($anchors.top) { $cands += (Join-Path $anchors.top "OB1") }
    $out = @()
    foreach ($c in $cands) {
        if ((Test-Path -LiteralPath (Join-Path $c ".git")) -and ($out -notcontains $c)) { $out += $c }
    }
    return @($out)
}

function Get-ExistingCommits([string]$RepoPath, [string[]]$Shas) {
    # Which of these FULL commit shas exist in the repository at $RepoPath ("" = here)?
    # Returns a hashtable keyed by lower-case oid; ONE git process for any number of shas.
    #
    # `git rev-list --no-walk=unsorted --ignore-missing <sha>...` prints the ones that exist
    # and silently drops the rest - which is what makes it the right tool: a per-object
    # answer on ARGUMENTS, no stdin. `git cat-file --batch-check` over stdin was the first
    # version, and it reported the FIRST object of every batch missing whenever the console
    # is UTF-8 (chcp 65001: the extension's terminal, the bridge): .NET Framework's Process
    # builds the child's stdin writer on Console.InputEncoding and its AutoFlush setter
    # writes that encoding's preamble, so git read "<BOM>sha^{commit}" - and the reader on
    # the other side stripped the BOM from the echo, so the transcript showed a clean sha
    # marked missing. PowerShell's own `$list | & git` pipe does the same. Found on this
    # feature's first run: the first sha of the board, the first OB1 pin of every clone and
    # the merge's own gitlink in -Merged all read UNRESOLVABLE from the extension and
    # resolved from Git Bash. Nothing here touches stdin now.
    $found = @{}
    $list = @($Shas | ForEach-Object { ([string]$_).Trim().ToLower() } | Where-Object { $_ -match '^[0-9a-f]{40}$' } | Select-Object -Unique)
    for ($start = 0; $start -lt $list.Count; $start += 200) {
        $chunk = @($list[$start..([Math]::Min($start + 199, $list.Count - 1))])
        $gitArgs = @()
        if ($RepoPath) { $gitArgs += @("-C", $RepoPath) }
        $gitArgs += @("rev-list", "--no-walk=unsorted", "--ignore-missing") + $chunk
        $out = @(Invoke-GitCapture $gitArgs)
        if ($LASTEXITCODE -ne 0) { return $null }
        foreach ($l in $out) { $t = ([string]$l).Trim().ToLower(); if ($t -match '^[0-9a-f]{40}$') { $found[$t] = $true } }
    }
    return $found
}

function Resolve-ItemShas($items) {
    # For every item: does each recorded commit (submitted_sha / tested_at_sha / merged_sha)
    # exist, and does the OB1 gitlink at that commit exist in an OB1 clone the item can be
    # asked about? Returns a hashtable id -> string[] of flags, empty when everything
    # resolves. BATCHED, one pass for the whole board: a per-item `git cat-file -e` is
    # 40 items x 3 shas x 3 processes on Windows, which is the wrong side of the 5 s the
    # board is allowed. Here it is one cat-file for the commits, one rev-parse for the
    # gitlinks, and one cat-file per OB1 clone. Read-only throughout: nothing is written.
    $out = @{}
    $wanted = @{}
    foreach ($it in @($items)) {
        $out[$it.id] = @()
        foreach ($f in @("submitted_sha", "tested_at_sha", "merged_sha")) {
            $v = ""
            if ($it.PSObject.Properties.Name -contains $f) { $v = ([string]$it.$f).Trim() }
            if (-not $v) { continue }
            if (-not $wanted.ContainsKey($v)) { $wanted[$v] = @() }
            $wanted[$v] += ,@($it.id, $f)
        }
    }
    if ($wanted.Count -eq 0) { return $out }
    $shas = @($wanted.Keys)
    $short = @{}
    foreach ($s in $shas) { $short[$s] = $(if ($s.Length -ge 7) { $s.Substring(0, 7) } else { $s }) }
    # 1. the commits, one process. A recorded sha that is not a full oid (nothing live
    #    records one, but a hand-edited item could) is asked about on its own.
    $exists = @{}
    $found = Get-ExistingCommits "" $shas
    if ($null -eq $found) {
        foreach ($id in @($out.Keys)) { $out[$id] = @("UNCHECKED: git rev-list could not run here, so no recorded commit was resolved") }
        return $out
    }
    foreach ($s in $shas) {
        if ($s -match '^[0-9a-f]{40}$') { $exists[$s] = $found.ContainsKey($s.ToLower()) }
        else {
            [void](Invoke-GitCapture @("rev-parse", "--verify", "--quiet", "$s^{commit}"))
            $exists[$s] = ($LASTEXITCODE -eq 0)
        }
    }
    # 2. the OB1 gitlink at each existing commit, one process (chunked well under the
    #    Windows command-line limit). rev-parse prints one stdout line per argument in
    #    order, echoing the ARGUMENT for one it cannot resolve (a tree with no OB1 entry),
    #    so the mapping is positional and a non-oid line means "no gitlink".
    $link = @{}
    $present = @($shas | Where-Object { $exists[$_] })
    for ($start = 0; $start -lt $present.Count; $start += 200) {
        $chunk = @($present[$start..([Math]::Min($start + 199, $present.Count - 1))])
        $rp = @(Invoke-GitCapture (@("rev-parse") + @($chunk | ForEach-Object { $_ + ":OB1" })))
        for ($i = 0; $i -lt $chunk.Count; $i++) {
            $l = if ($i -lt $rp.Count) { ([string]$rp[$i]).Trim() } else { "" }
            if ($l -match '^[0-9a-f]{40}$') { $link[$chunk[$i]] = $l }
        }
    }
    # 3. which clone each item is asked about, then one cat-file per clone
    $registry = Get-WorktreeRegistry
    $itemClone = @{}
    foreach ($it in @($items)) {
        $c = @(Get-Ob1CloneCandidates $it $registry)
        $itemClone[$it.id] = $(if ($c.Count -gt 0) { $c[0] } else { "" })
    }
    $byClone = @{}
    foreach ($s in $link.Keys) {
        foreach ($pair in $wanted[$s]) {
            $c = $itemClone[$pair[0]]
            if (-not $c) { continue }
            if (-not $byClone.ContainsKey($c)) { $byClone[$c] = @() }
            if ($byClone[$c] -notcontains $link[$s]) { $byClone[$c] += $link[$s] }
        }
    }
    $linkOk = @{}
    foreach ($c in $byClone.Keys) {
        $objs = @($byClone[$c])
        $held = Get-ExistingCommits $c $objs
        foreach ($g in $objs) { $linkOk["$c|$g"] = (($null -ne $held) -and $held.ContainsKey($g.ToLower())) }
    }
    # 4. the flags
    foreach ($s in $shas) {
        foreach ($pair in $wanted[$s]) {
            $id = $pair[0]; $field = $pair[1]
            if (-not $exists[$s]) { $out[$id] += ("UNRESOLVABLE: {0} {1}" -f $field, $short[$s]); continue }
            if (-not $link.ContainsKey($s)) { continue }
            $g = $link[$s]; $g7 = $g.Substring(0, 7)
            $c = $itemClone[$id]
            if (-not $c) { $out[$id] += ("OB1 {0} UNCHECKED: no OB1 clone to ask" -f $g7); continue }
            if (-not $linkOk["$c|$g"]) { $out[$id] += ("UNRESOLVABLE: OB1 {0}" -f $g7) }
        }
    }
    foreach ($id in @($out.Keys)) { $out[$id] = @($out[$id] | Select-Object -Unique) }
    return $out
}

function Find-Ob1CloneHolding($item, [string[]]$Needed) {
    $registry = Get-WorktreeRegistry
    foreach ($c in @(Get-Ob1CloneCandidates $item $registry)) {
        $held = Get-ExistingCommits $c $Needed
        if ($null -eq $held) { continue }
        $all = $true
        foreach ($n in @($Needed)) { if (-not $held.ContainsKey(([string]$n).ToLower())) { $all = $false } }
        if ($all) { return $c }
    }
    return ""
}

function Get-DeploySurfaces($item, [string]$Sha) {
    # See the section comment. Returns @{ surfaces; notes; skipped; line_before }. `notes` is
    # printed at -Merged; `skipped` names the services the rule cannot reach (a build context
    # outside this repository - memory/ builds mnemory from a sibling checkout) and is only
    # recorded, because a NOTE printed on every merge is a note nobody reads. Every git
    # question is asked of the MERGED tree (`git show <sha>:<path>`), never of a working directory.
    $notes = @(); $skipped = @()
    $head = (Invoke-GitCapture @("rev-list", "--parents", "-n", "1", $Sha) | Select-Object -First 1)
    $parts = @(([string]$head).Trim() -split '\s+' | Where-Object { $_ })
    if ($parts.Count -lt 2) { return @{ surfaces = @(); notes = @("'$Sha' has no parent - nothing to derive a deploy surface from"); skipped = @(); line_before = "" } }
    $lineBefore = $parts[1]
    if ($parts.Count -lt 3) { $notes += ("'{0}' is not a merge commit; surfaces derived from its single parent {1}" -f $Sha.Substring(0, 7), $lineBefore.Substring(0, 7)) }
    $changed = @(Invoke-GitCapture @("diff-tree", "-r", "--name-only", "--no-commit-id", $lineBefore, $Sha) | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
    $images = @(); $pastes = @()
    # (1) an OB1 gitlink move
    if ($changed -contains "OB1") {
        $old = (Invoke-GitCapture @("rev-parse", "--verify", "--quiet", "$lineBefore`:OB1") | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0) { $old = "" } else { $old = ([string]$old).Trim() }
        $new = (Invoke-GitCapture @("rev-parse", "--verify", "--quiet", "$Sha`:OB1") | Select-Object -First 1)
        if ($LASTEXITCODE -ne 0) { $new = "" } else { $new = ([string]$new).Trim() }
        if ($new) {
            $need = @($new); if ($old) { $need += $old }
            $clone = Find-Ob1CloneHolding $item $need
            if (-not $clone) {
                Die (("cannot derive the deploy surfaces of '{0}': its OB1 gitlink {1}{2} exists in no OB1 clone " +
                      "this tool can reach (the item's worktree, the main checkout, the current repository). A " +
                      "merge that pins an OB1 commit nobody holds is exactly the zombie -List flags as " +
                      "[UNRESOLVABLE]. Push it to OB1's remote (CLAUDE.md: never bump the gitlink to a commit " +
                      "that is not there) and fetch it into a clone, then record the merge again. Nothing has " +
                      "been recorded.") -f $Sha, $new.Substring(0, 7), $(if ($old) { " (from " + $old.Substring(0, 7) + ")" } else { "" }))
            }
            if ($old) { $ob1Changed = @(Invoke-GitCapture @("-C", $clone, "diff-tree", "-r", "--name-only", "--no-commit-id", $old, $new)) }
            else { $ob1Changed = @(Invoke-GitCapture @("-C", $clone, "ls-tree", "-r", "--name-only", $new)) }
            $ob1Changed = @($ob1Changed | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
            $dirs = @($ob1Changed | ForEach-Object { if ($_ -match '^integrations/([^/]+)/') { $matches[1] } } | Where-Object { $_ } | Select-Object -Unique)
            $svcs = @()
            $composeText = @(Invoke-GitCapture @("-C", $clone, "show", "$new`:docker/docker-compose.yml"))
            if ($LASTEXITCODE -eq 0) { $svcs = @(Get-ComposeServices $composeText) }
            else { $notes += ("OB1 {0} has no docker/docker-compose.yml - integration images are recorded by directory" -f $new.Substring(0, 7)) }
            foreach ($d in $dirs) {
                # ls-tree, NOT `cat-file -e`: cat-file writes `fatal: path ... does not exist` to
                # stderr for the ordinary absent-Dockerfile case, and Invoke-GitCapture does not
                # redirect stderr - so a perfectly successful -Merged printed a `fatal:` line above
                # its own success message (found by attempt 1's tester on the real de42243 replay).
                # An operator taught to ignore a `fatal:` is being taught the wrong lesson. ls-tree
                # exits 0 either way and prints the path only when it is there.
                $dfHit = @(Invoke-GitCapture @("-C", $clone, "ls-tree", "--name-only", $new, "integrations/$d/Dockerfile") | Where-Object { ([string]$_).Trim() })
                if ($dfHit.Count -eq 0) { $notes += ("OB1 integrations/{0} changed but has no Dockerfile at {1} - not an image" -f $d, $new.Substring(0, 7)); continue }
                $svc = @($svcs | Where-Object { $_.context -and ((Convert-RepoRelative "docker" $_.context) -eq "integrations/$d") } | Select-Object -First 1)
                if ($svc.Count -gt 0) { $images += ("image:" + $svc[0].name) }
                else { $images += ("image:integrations/" + $d); $notes += ("no compose service builds integrations/{0} at OB1 {1} - recorded by directory" -f $d, $new.Substring(0, 7)) }
            }
        }
    }
    # (2) the files OWUI only sees by paste - and owui/manifest.csv says which those are.
    #     See the section header: `any owui/** path` derived surfaces for the manifest and the
    #     README, which are not pasted into anything. The manifest of the MERGED tree is read,
    #     never the working copy, and its `file` column is resolved BY HEADER NAME (the column
    #     set moved from `bytes` to `sha256` between 08c4ae1 and e989265; the position did not,
    #     but reading it by name means the next move costs nothing). Paths carry no commas, so
    #     a plain split is enough here - this is not a general CSV reader.
    $owuiChanged = @($changed | Where-Object { $_ -match '^owui/' })
    if ($owuiChanged.Count -gt 0) {
        $manifest = @(Invoke-GitCapture @("show", "$Sha`:owui/manifest.csv"))
        if ($LASTEXITCODE -ne 0) {
            $notes += ("owui/ changed but the merged tree has no owui/manifest.csv, which is what says a file is pasted - no paste surface derived for: " + ($owuiChanged -join ", "))
        } else {
            $pasteable = @{}
            $fileCol = -1
            foreach ($row in $manifest) {
                $cells = @(([string]$row) -split ',')
                if ($fileCol -lt 0) {
                    $fileCol = [array]::IndexOf(@($cells | ForEach-Object { ([string]$_).Trim().ToLower() }), "file")
                    if ($fileCol -lt 0) { break }
                    continue
                }
                if ($fileCol -ge $cells.Count) { continue }
                $f = ([string]$cells[$fileCol]).Trim() -replace '\\', '/'
                if ($f) { $pasteable["owui/" + $f.TrimStart('/')] = $true }
            }
            if ($fileCol -lt 0) {
                $notes += ("owui/manifest.csv at {0} has no 'file' column - no paste surface derived for: {1}" -f $Sha.Substring(0, 7), ($owuiChanged -join ", "))
            } else {
                foreach ($p in $owuiChanged) {
                    if ($pasteable.ContainsKey($p)) { $pastes += ("paste:" + $p) }
                    else { $notes += ("{0} changed but owui/manifest.csv does not list it - it is not pasted into OWUI, so it is not a deploy surface" -f $p) }
                }
            }
        }
    }
    # (3) a :local-tagged service of this repository whose build context changed
    $tree = @(Invoke-GitCapture @("ls-tree", "-r", "--name-only", $Sha) | ForEach-Object { ([string]$_).Trim() })
    $composeFiles = @($tree | Where-Object { $_ -match '(^|/)(docker-)?compose[^/]*\.ya?ml$' -and $_ -notmatch '^scripts/archive/' })
    foreach ($cf in $composeFiles) {
        $text = @(Invoke-GitCapture @("show", "$Sha`:$cf"))
        if ($LASTEXITCODE -ne 0) { continue }
        $dir = if ($cf.Contains("/")) { $cf.Substring(0, $cf.LastIndexOf("/")) } else { "" }
        foreach ($s in @(Get-ComposeServices $text)) {
            if (-not $s.context) { continue }
            if ($s.image -notmatch ':local(\}|\s|$)') { continue }
            $ctx = Convert-RepoRelative $dir $s.context
            if ($null -eq $ctx) { $skipped += ("{0} service {1}: build context {2} lies outside this repository - not derivable" -f $cf, $s.name, $s.context); continue }
            $df = if ($s.dockerfile) { $s.dockerfile } else { "Dockerfile" }
            $dfPath = Convert-RepoRelative $ctx $df
            $roots = @()
            if ($ctx -eq "") {
                # A context that is the REPOSITORY ROOT is not a build context, it is the
                # repository (frontend builds openwebui and tailscale from `..`): counting every
                # merge as a rebuild of both would say nothing. For those the surface is the
                # Dockerfile itself plus what it COPY/ADDs from the context.
                if ($dfPath) { $roots += $dfPath }
                $dfText = @(Invoke-GitCapture @("show", "$Sha`:$dfPath"))
                if ($LASTEXITCODE -eq 0) {
                    foreach ($dl in $dfText) {
                        if ([string]$dl -notmatch '^\s*(COPY|ADD)\s+(.*)$') { continue }
                        $rest = $matches[2]
                        if ($rest -match '--from=') { continue }
                        $toks = @($rest -split '\s+' | Where-Object { $_ -and ($_ -notlike '--*') })
                        if ($toks.Count -lt 2) { continue }
                        foreach ($src in $toks[0..($toks.Count - 2)]) {
                            if ($src -in @(".", "./", "*")) { $roots += "" } else { $r = Convert-RepoRelative "" $src; if ($null -ne $r) { $roots += $r } }
                        }
                    }
                } else { $notes += ("{0} service {1}: Dockerfile {2} not found at {3} - only the Dockerfile path is watched" -f $cf, $s.name, $dfPath, $Sha.Substring(0, 7)) }
            } else { $roots += $ctx }
            $hit = $false
            foreach ($p in $changed) {
                foreach ($r in $roots) {
                    if (($r -eq "") -or ($p -eq $r) -or $p.StartsWith($r + "/")) { $hit = $true; break }
                }
                if ($hit) { break }
            }
            if ($hit) { $images += ("image:" + $s.name) }
        }
    }
    $surfaces = @(@($images | Select-Object -Unique | Sort-Object) + @($pastes | Select-Object -Unique | Sort-Object))
    return @{ surfaces = $surfaces; notes = @($notes); skipped = @($skipped); line_before = $lineBefore }
}

function Test-DeployEvidence([string[]]$Surfaces, [string]$Body) {
    # What -Deployed demands of its evidence, PER SURFACE: a line that names the surface, and
    # on the lines that do, a pin (an image label or revision sha, or the pasted file's hash)
    # and a health state a deploy can close on. Returns @{ ok; problems }.
    #
    # THE CHECK IS LINE-SCOPED, NOT ENTITY-SCOPED, AND STAYS SO. It asks "some line mentioning
    # this surface also carries a pin and a health state", not "the pin and the health state
    # are ABOUT this surface". So
    #     openbrain-curator rebuilt from 6fba6b3 - checked openbrain-research instead: ...healthy
    # is accepted (attempt 1's tester wrote it deliberately). Named rather than fixed: telling
    # those two apart is a judgement about English prose, not something a regex decides, and a
    # tighter pattern would refuse honest evidence far more often than it would catch this.
    # What the check does buy is that the surface's own name must appear - evidence naming only
    # the other container is refused with `no line of the evidence names it`.
    $problems = @()
    $lines = @($Body -split "`r?`n")
    foreach ($s in @($Surfaces)) {
        $kind = ($s -split ':', 2)[0]; $name = ($s -split ':', 2)[1]
        $needles = @($name)
        if ($kind -eq "paste") { $needles += ($name -split '/')[-1] }
        $hit = @($lines | Where-Object { $l = $_; @($needles | Where-Object { $l.IndexOf($_, [System.StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0 })
        if ($hit.Count -eq 0) { $problems += ("{0}: no line of the evidence names it" -f $s); continue }
        $text = $hit -join "`n"
        # A PIN IS HEX, AND A DECIMAL NUMBER IS NOT A PIN. The old test was
        # `[0-9a-f]{7,64}` requiring at least one DIGIT, so `deployed at 1788720066` closed a
        # surface: an epoch, a run number or a ticket id read as an image revision (found by
        # attempt 1's tester, who closed rp-researchretry with a timestamp). Two shapes now:
        # a LABELLED digest (`sha256:<hex>` / `sha256 <hex>` / `sha256=<hex>`, 7-64 - this is
        # how a paste's 64-char hash and an image id arrive), or a BARE token of 7-40 hex that
        # is not simply a decimal number - the leading `(?![0-9]+(?![0-9a-z]))` is what refuses
        # an all-digits run, and 40 is the length of a git object id.
        $pinOk = ($text -match '(?i)sha256\s*[:=]?\s*[0-9a-f]{7,64}(?![0-9a-z])') -or
                 ($text -match '(?i)(?<![0-9a-z])(?![0-9]+(?![0-9a-z]))[0-9a-f]{7,40}(?![0-9a-z])')
        if (-not $pinOk) {
            $problems += ("{0}: names no pin - {1} (7-40 hex characters, or sha256:<hex>; an all-digit token such as a timestamp is not a pin)" -f $s, $(if ($kind -eq "image") { "the running container's image label or revision (org.opencontainers.image.revision=<sha>, or the image id)" } else { "the sha256 of the file as pasted" }))
        }
        $m = [regex]::Match($text, '(?i)(State\.Health\.Status|Health\.Status|health(?:_status)?|State\.Status|status)\s*[=:]\s*"?([A-Za-z]+)')
        if (-not $m.Success) { $problems += ("{0}: names no health state (State.Health.Status=healthy, or State.Status=running for a container with no healthcheck)" -f $s) }
        elseif (@("healthy", "running") -notcontains $m.Groups[2].Value.ToLower()) { $problems += ("{0}: health state '{1}' is not one a deploy closes on (healthy, or running)" -f $s, $m.Groups[2].Value) }
    }
    return @{ ok = (@($problems).Count -eq 0); problems = @($problems) }
}

function Invoke-OracleOnStall([string]$i) {
    # FRONTIER-ORACLE-ON-STALL (dark-factory-unification U4; ORCHESTRATION-DESIGN sec 7).
    #
    # A failed test round is the ONLY moment the harness learns something new about whether
    # this item is converging, so it is where the stall test belongs. oracle_on_stall.py
    # owns the definition (agent-org's: no novel failure signature, no moved commit, twice)
    # and the ledger; this just runs it and shows the operator what it found.
    #
    # ADVISORY: a stall check that could not run must never block a tester from recording a
    # verdict. But it says SKIPPED and why - a check that quietly does nothing is the exact
    # failure class this plan's sec 0 A6 is about (CLAUDE.md:131 records eight found in a day).
    $mod = Join-Path $PSScriptRoot "oracle_on_stall.py"
    if (-not (Test-Path $mod)) {
        Write-Host "  stall check SKIPPED: oracle_on_stall.py is not beside queue.ps1." -ForegroundColor Yellow
        return
    }
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "  stall check SKIPPED: python not found on PATH." -ForegroundColor Yellow
        return
    }
    $repo = Get-MainCheckout
    if (-not $repo) {
        Write-Host "  stall check SKIPPED: cannot resolve the main checkout." -ForegroundColor Yellow
        return
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    # Run the FILE, not a `python -c` snippet - same reason as -ScopeNodes: the repo path
    # contains a space and a snippet's quoting does not survive Windows argument handling.
    try { & python $mod check $QueueDir $i --repo $repo } finally { $ErrorActionPreference = $prev }
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

function Invoke-AutoGate($item, [string]$gate, $decision, [string]$runBranch = "") {
    # Try to auto-pass $gate. Returns the andon verdict; the CALLER halts on a raise, so the
    # halt is visible at the state transition rather than buried in here.
    #
    # THE RUN'S OWN BRANCH HAS TO REACH THE BOARD, and until 2026-08-30 it did not reach the
    # ANCHOR gate. `-Propose` writes `branch = ""` - there is no branch yet when the anchor is
    # proposed - and `-Submit` stores the real one only AFTER the anchor gate has run. So at
    # that gate $item.branch was empty, no -RunBranch was passed, and `work-branch-on-remote`
    # fell back to its BROAD reading: is ANY local work branch on a remote. README.md promised
    # the narrow one ("a dark run is blocked by ITS OWN branch being on a remote, not by
    # anybody else's"); the gate asked the broad one. On this repository that is eleven foreign
    # work/* branches somebody else pushed, so `dark` could never auto-pass an anchor gate here
    # - a run refused for a fact about the operator's remote that the run neither caused nor is
    # allowed to clear. Reproduced before the fix: own branch clean, ONE foreign work branch on
    # a remote, anchor gate exit 6, ledger `fired=work-branch-on-remote`.
    #
    # The branch IS known at that point - it is `-Submit -Branch`, a mandatory parameter - it
    # simply had not been written to the item yet. The caller passes it, and both gates now ask
    # the same narrow question about the same branch. The BROAD question is still asked by a
    # bare `andon.ps1 -Evaluate`, which is the operator's reading, not a run's.
    #
    # A GATE THAT CANNOT NAME THE BRANCH DOES NOT AUTO-PASS. Falling back to the broad question
    # is the defect above; falling back to an empty list would be worse still, because
    # Predicate-BranchOnRemote answers `ok` for one - "no local work branches to check" - and
    # that is a pass nobody proved. Unreachable by construction (both call sites have a branch:
    # -Submit requires -Branch, and pre_review runs after -Submit stored it), and kept because
    # the two alternatives are a wrong answer and a silent one.
    $branches = @()
    if ($item.branch) { $branches += $item.branch }
    elseif ($runBranch) { $branches += $runBranch }
    if (@($branches).Count -eq 0) {
        Die (("the '{0}' gate cannot name the branch this run owns, so it cannot ask whether that " +
              "branch is on a remote. An unattended gate does not pass a question it did not ask.") -f $gate) 1
    }
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
    # EVERY LIST THE VERDICT CARRIES. `fired` is what the detectors saw and `halted` is what
    # stopped the line; they were one derived list until 2026-08-30, which hid a fire whose
    # on_fire was not `halt`. Printing only those two then hid the SIBLING case the same day:
    # an INDETERMINATE condition halts nothing and fires nothing, so a warn-declared
    # indeterminate reached the operator as a bare board word. De-duplicated because a
    # halting fire is legitimately in more than one.
    $seen = @()
    foreach ($k in @("halted", "fired", "indeterminate", "unrecognised", "unaccounted")) {
        if (Test-AndonField $andon $k) { $seen += @($andon.$k) }
    }
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
    # THE BUCKET CENSUS, which is what the refusal actually rests on: `clear` requires every
    # bucket but `evaluated_ok` to be empty, so the operator should be able to see WHICH
    # bucket was not.
    if (Test-AndonField $andon "census") {
        $parts = @()
        foreach ($k in @(Get-AndonBucketNames)) {
            $n = 0
            if ($andon.census -is [System.Collections.IDictionary]) {
                if ($andon.census.Contains($k)) { $n = [int]$andon.census[$k] }
            } elseif ($andon.census.PSObject.Properties.Name -contains $k) { $n = [int]$andon.census.$k }
            $parts += ("{0}={1}" -f $k, $n)
        }
        Write-Host ("  board census: {0}" -f ($parts -join ", ")) -ForegroundColor Red
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
    $item = Read-ItemFile $f
    if ($item.state -in $TerminalStates) {
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

if ($Oracle) {
    # THE OBSERVATION SURFACE for frontier-oracle-on-stall (U4).
    #
    # "stall -> oracle observed firing at least once" is the phase's validation, and a
    # firing nobody can point at afterwards has not been observed. This prints the ledger:
    # every stall the detector found, what it saw, which runner stalled, which oracle it
    # escalated to, and whether that escalation has been served yet.
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Write-Host "python not found - the oracle ledger needs it." -ForegroundColor Red
        exit 2
    }
    $mod = Join-Path $PSScriptRoot "oracle_on_stall.py"
    $repo = Get-MainCheckout
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ($Id) { & python $mod report --repo $repo --item $Id }
        else { & python $mod report --repo $repo }
    } finally { $ErrorActionPreference = $prev }
    exit $LASTEXITCODE
}

if ($List) {
    $items = @(Get-ChildItem -Path $QueueDir -Filter "*.json" -ErrorAction SilentlyContinue)
    if (-not $items.Count) { Write-Host "queue empty" -ForegroundColor Green; exit 0 }
    $rows = @()
    foreach ($f in ($items | Sort-Object Name)) {
        # `<id>.anchor.json` sits beside `<id>.json` in this directory. Ids are
        # [a-z0-9-], so a dot in the base name means a sidecar file, not a work item.
        if ($f.BaseName.Contains(".")) { continue }
        $it = Read-ItemFile $f.FullName
        if ($State -and $it.state -ne $State) { continue }
        $rows += $it
    }
    # DOES THE RECORD DESCRIBE COMMITS THAT EXIST? One batched pass for the whole board (see
    # Resolve-ItemShas); a row with an unresolvable commit or OB1 pin is flagged and sorts
    # FIRST, because a queue row that names nothing is the one thing on this board that is
    # not live work and reads exactly like it. Read-only: -List writes nothing, ever.
    $resolution = Resolve-ItemShas $rows
    $ordered = @($rows | Sort-Object -Property @{ Expression = { if (@($resolution[$_.id] | Where-Object { $_ -like "UNRESOLVABLE:*" }).Count -gt 0) { 0 } else { 1 } } }, @{ Expression = { $_.id } })
    Write-Host ("{0,-18} {1,-17} {2,-20} {3}" -f "ID", "STATE", "DEVELOPER", "HELD BY")
    foreach ($it in $ordered) {
        $held = ""
        foreach ($r in @("tester", "reviewer")) {
            $c = ClaimPath $it.id $r
            if (Test-Path $c) { $held = "$r=" + (Get-Content -Raw -Path $c | ConvertFrom-Json).by }
        }
        $flag = ""
        foreach ($u in @($resolution[$it.id])) { $flag += " [" + $u + "]" }
        # [needs hand-off] means "the reviewer will not be able to merge this". line_mergeable is
        # written at -Submit and never cleared, so until 2026-09-06 every merged item wore it
        # forever: 31 of the 42 rows on the live board carried it on 2026-09-06 and 30 of them
        # were terminal, so the one row where it was true was one in thirty-one.
        # A terminal item has nothing left to merge; the flag is for the rows still moving.
        if (($it.state -notin $TerminalStates) -and ($it.PSObject.Properties.Name -contains "line_mergeable") -and -not $it.line_mergeable) { $flag += " [needs hand-off]" }
        # The two states that are waiting on a PERSON are called out: an unread queue is
        # how a human gate quietly becomes a human bottleneck.
        if ($it.state -eq "anchor-draft") { $flag += " [waiting: operator to confirm the anchor]" }
        if ($it.state -eq "test-passed")  { $flag += " [waiting: operator to release for review]" }
        # MERGED IS NOT LIVE while a derived surface is open. Items merged before the derivation
        # existed carry no surfaces and read as plain `merged` - re-deriving them is out of scope.
        if ($it.state -eq "merged") {
            $pending = @(Get-DeployPending $it)
            if ($pending.Count -gt 0) { $flag += (" [UNDEPLOYED: " + ($pending -join ", ") + "]") }
        }
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
    }
    # DO THE RECORDED COMMITS EXIST? Same batched resolution -List uses, for this one item.
    $resolution = Resolve-ItemShas @($it)
    $flags = @($resolution[$it.id])
    if ($flags.Count -gt 0) {
        Write-Host "--- RESOLUTION ---" -ForegroundColor Red
        foreach ($u in $flags) { Write-Host ("  [" + $u + "]") -ForegroundColor Red }
        Write-Host "  A recorded commit, or the OB1 commit it pins, exists in no clone this tool can reach. The row is" -ForegroundColor DarkGray
        Write-Host "  not live work until that object is fetched or the record corrected." -ForegroundColor DarkGray
        Write-Host ""
    }
    # WHAT THE MERGE SHIPPED, and whether it is live. Present only on items merged since the
    # derivation existed (2026-09-06); older merged items have no surfaces to show.
    if ($it.PSObject.Properties.Name -contains "deploy_surfaces") {
        $all = @(Get-ArrayField $it "deploy_surfaces")
        $pending = @(Get-DeployPending $it)
        $done = @(Get-ArrayField $it "deployed")
        Write-Host "--- DEPLOY ---" -ForegroundColor Cyan
        if ($all.Count -eq 0) { Write-Host "  no deploy surface derived from the merge range (no image, no paste)" }
        foreach ($s in $all) {
            $row = @($done | Where-Object { $_.surface -eq $s } | Select-Object -First 1)
            if ($row.Count -gt 0) { Write-Host ("  {0,-45} CLOSED by {1} at {2}" -f $s, $row[0].by, $row[0].at) -ForegroundColor Green }
            elseif ($pending -contains $s) { Write-Host ("  {0,-45} OPEN - not live until -Deployed records it" -f $s) -ForegroundColor Yellow }
            else { Write-Host ("  {0,-45} (neither open nor closed - record inconsistent)" -f $s) -ForegroundColor Red }
        }
        if ($it.PSObject.Properties.Name -contains "deploy_derived" -and $it.deploy_derived) {
            $dd = $it.deploy_derived
            Write-Host ("  derived from git diff --name-only {0}..{1}" -f ([string]$dd.line_before).Substring(0, [Math]::Min(7, ([string]$dd.line_before).Length)), ([string]$dd.merge).Substring(0, [Math]::Min(7, ([string]$dd.merge).Length))) -ForegroundColor DarkGray
            foreach ($n in @(Get-ArrayField $dd "notes")) { Write-Host ("  NOTE: " + $n) -ForegroundColor DarkGray }
        }
        Write-Host ""
    }
    # THE VERDICTS, case by case. The operator reading this used to see one word per
    # attempt; the tester's own headings said more, and now the record carries them.
    $verdicts = @()
    if ($it.PSObject.Properties.Name -contains "results") { $verdicts = @($it.results) }
    if ($verdicts.Count -gt 0) {
        Write-Host "--- VERDICTS ---" -ForegroundColor Cyan
        $n = 0
        foreach ($r in $verdicts) {
            $n++
            $att = if ($r.PSObject.Properties.Name -contains "attempt") { "attempt " + $r.attempt } else { "verdict #" + $n }
            $shaShort = if ($r.sha -and ([string]$r.sha).Length -ge 8) { ([string]$r.sha).Substring(0, 8) } else { [string]$r.sha }
            $colour = if ($r.verdict -eq "pass") { "Green" } else { "Yellow" }
            Write-Host ("{0}: {1} by {2} at {3} (plan_adequate={4})" -f $att, ([string]$r.verdict).ToUpper(), $r.by, $shaShort, $r.plan_adequate) -ForegroundColor $colour
            $rows = @()
            if ($r.PSObject.Properties.Name -contains "cases") { $rows = @($r.cases) }
            if ($rows.Count -eq 0) {
                Write-Host "    (no per-case verdicts recorded - evidence predates the case parser, or carried no case headings)" -ForegroundColor DarkGray
                continue
            }
            foreach ($c in $rows) {
                $cc = if ($c.verdict -eq "PASS") { "Green" } elseif ($c.verdict -eq "MISSING") { "Red" } else { "Yellow" }
                Write-Host ("    {0,-8} {1}" -f $c.case, $c.verdict) -ForegroundColor $cc
                # The heading as written, for every row - the record quotes the tester.
                if ($c.line) { Write-Host ("             " + $c.line) -ForegroundColor DarkGray }
            }
        }
        Write-Host ""
    }
    Write-Host "--- RECORD ---" -ForegroundColor Cyan
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
        try { $items += (Read-ItemFile $f.FullName) } catch { }
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
    Copy-IntoQueue $Anchor $anchorDest "-Anchor" "anchor file for this item"
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
        Copy-IntoQueue $Anchor $item.anchor_file "-Anchor" "anchor file for this item"
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
    if ($item.state -in @("merged", "deployed", "rejected")) { Die "'$Id' is '$($item.state)' - open a new item" }
    if ($item.state -eq "anchor-draft") { Die "'$Id' is not confirmed yet - amend it on -ConfirmAnchor instead" }
    try { $anchorObj = Read-AnchorFile $Anchor } catch { Die $_.Exception.Message }
    Copy-IntoQueue $Anchor $item.anchor_file "-Anchor" "anchor file for this item"
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
    if (-not $Id -or -not $Developer) { Die "-Submit needs -Id, -Branch and -Developer" }
    # A NAME THAT IS NOT A NAME IS NOT A MISSING PARAMETER. `" "` is TRUTHY in PowerShell, so
    # `-not $Branch` waved it through, and the anchor gate below is reached BEFORE the
    # rev-parse further down: the board trimmed it to nothing, stepped over it, and reported
    # "checked 1 branch(es); none is on a remote" - a clear board and `decision=passed` in the
    # ledger for a branch question nobody asked. Reproduced 2026-08-30 with `-Branch ' '`.
    # The board refuses this now too (Predicate-BranchOnRemote returns indeterminate); this is
    # the same refusal at the door, so the gate is never handed an input it cannot use.
    if ([string]::IsNullOrWhiteSpace($Branch)) {
        Die ("-Branch is empty or whitespace, which is not a branch. It is the name the anchor " +
             "gate asks the andon board about, and a question asked about nothing comes back " +
             "clear. Pass the branch this work is on.")
    }
    $Branch = $Branch.Trim()
    # -Thread is how the bridge knows which Mattermost conversation to report back into.
    if (-not $TestPlan) {
        Die ("-Submit needs -TestPlan. The plan is written BEFORE the work is queued - it is " +
             "what someone else executes. List the CASES, and for each say what counts as " +
             "passing and what would count as failing. A plan that cannot fail is not a plan, " +
             "and 'I tested it myself' is not one either.")
    }
    # A DEVELOPER ID THAT MATCHES NO WORKTREE IS A GUARD SWITCHED OFF. Refused here, at the
    # door, rather than warned about at the very end (2026-09-04).
    #
    # Separation of duties is a NAME comparison (Normalize-Id): a tester or reviewer is
    # refused because their -By equals the recorded developer, and an id nobody holds equals
    # nobody. So a typo does not weaken the check a little - it removes it, silently, for
    # this item. The old behaviour printed a WARNING after the item was already queued, which
    # is the worst arrangement available: the trap sprung AND the door shut, because a second
    # -Submit came back "already exists". The correction path below is the other half of this
    # fix; a refusal with no way forward would just move the dead end.
    #
    # Test-KnownAgent stays advisory-by-construction where there is no registry at all (a
    # fresh clone, a hermetic fixture) - that is "cannot check", not "checked and passed",
    # and refusing there would break every environment before it had a chance to work.
    if (-not (Test-KnownAgent $Developer)) {
        Die (("-Developer '{0}' matches no worktree in the registry ({1}), so it is refused, " +
              "not warned about. Separation of duties is a NAME comparison - an id nobody " +
              "holds excludes nobody, and this item would then accept its own author as its " +
              "tester and its reviewer. Use the id the worktree was registered under (the " +
              "'wt-' prefix is optional), or provision it first:`n" +
              "    scripts\agent-harness\new-worktree.ps1 -Id <short-id>") -f
             $Developer, (Join-Path (Get-SharedStateDir) "worktrees.json")) 4
    }
    # The anchor gate. An item that was proposed and confirmed is ADVANCED here; creating
    # one on the fly is only allowed when the operator has turned the gate off, and then it
    # is a stated configuration choice rather than a silent bypass.
    $anchorRequired = [bool](Get-HarnessSetting "pipeline.anchor_required" $true)
    $correcting = $false
    $bumped = $false
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
            $andon = Invoke-AutoGate $existing "anchor" $gd $Branch
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
        # THE CORRECTION PATH (2026-09-04). A second -Submit used to be refused outright, so
        # anything typed wrong at submission - the developer id, the branch, the plan path -
        # was permanent, and the only way out was a NEW id: which scatters one piece of work
        # across two items, orphans the confirmed anchor, and loses the history. An item that
        # is queued but that NOBODY HAS CLAIMED has no one standing on it; re-stating what
        # was submitted is a correction to a record, not a state change, so the attempt is
        # deliberately NOT bumped and no verdict is disturbed.
        if ($existing.state -eq "ready-to-test") {
            if (Test-Path (ClaimPath $Id "tester")) {
                $holder = (Get-Content -Raw -Path (ClaimPath $Id "tester") | ConvertFrom-Json).by
                Die (("'{0}' is claimed by {1} right now. Re-submitting would move the ground " +
                      "under a tester mid-run - the plan and the branch they are executing " +
                      "against would change without their knowing. Wait for the verdict, or " +
                      "ask them to -Unclaim it.") -f $Id, $holder) 3
            }
            $correcting = $true
            # CAPTURED NOW, not later. $item and $existing are the SAME object below, so by
            # the time the history line is written Set-Field has already overwritten both
            # fields with the new values and the "what changed" line would read "same values
            # re-stated" for every correction - a record that is worse than none.
            $wasDeveloper = $existing.developer
            $wasBranch = $existing.branch
        }
        if (-not $correcting -and $existing.state -ne "anchor-confirmed") {
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
    # And it needs CASES the verdict can be checked against - see the helpers above.
    $planCaseIds = Assert-PlanReadable $TestPlan "-TestPlan"
    # And it needs a HOME. The obvious place is the developer's worktree, which is exactly
    # what gets deleted at the end - leaving the item pointing at nothing. Copy it beside the
    # item in the shared state dir, which outlives the worktree. Both agents independently
    # improvised this same location; two agents guessing alike is luck, not a protocol.
    $planDest = Join-Path $QueueDir "$Id.plan.md"
    Copy-IntoQueue $TestPlan $planDest "-TestPlan" "test plan for this item"
    # Hashed from the QUEUED copy - the file -Pass will re-hash - not the developer's.
    $planHash = Get-PlanSha256 $planDest
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
        # A VERDICT ALREADY STANDS AT THIS ATTEMPT -> the attempt moves on (deploystate,
        # 2026-09-06). -AmendAnchor sends a tested item back to 'anchor-confirmed' without
        # touching `attempt`, and the evidence filename is <id>.attempt<N>.evidence.md, so the
        # next tester's -Pass would copy straight over the file the previous verdict rests on -
        # the D1 incident through another door. Two signals, either is enough: a results[] row
        # recorded at this attempt, or that attempt's evidence file beside the item. The
        # correction path (re-stating an unclaimed submission) has neither and stays put (D4);
        # a developer's -Requeue already bumped, so nothing here double-counts it (D6).
        if (-not $correcting) {
            $att = [int]$item.attempt
            $rowsHere = @(@(Get-ArrayField $item "results") | Where-Object { ($_.PSObject.Properties.Name -contains "attempt") -and ([int]$_.attempt -eq $att) })
            $evHere = Join-Path $QueueDir ("{0}.attempt{1}.evidence.md" -f $Id, $att)
            if (($rowsHere.Count -gt 0) -or (Test-Path -LiteralPath $evHere)) {
                Set-Field $item "attempt" ($att + 1)
                Add-History $item ("attempt bumped to {0}: attempt {1} already carries a verdict, and its evidence file stays where it is" -f ($att + 1), $att) $Developer
                $bumped = $true
            }
        }
        Set-Field $item "branch" $Branch; Set-Field $item "line" $line
        Set-Field $item "developer" $Developer
        Set-Field $item "state" "ready-to-test"; Set-Field $item "test_plan" $planDest
        Set-Field $item "plan_sha256" $planHash
        if ($Thread) { Set-Field $item "thread" $Thread }
        if ($RunnerProfile) { Set-Field $item "profile" $RunnerProfile }
        Set-Field $item "line_mergeable" (-not (Test-LineCheckedOutElsewhere -Line $line))
        Set-Field $item "submitted_sha" $sha.Trim()
    } else {
        $item = [ordered]@{
            id = $Id; branch = $Branch; line = $line; developer = $Developer
            state = "ready-to-test"; anchor = $null; anchor_file = ""
            anchor_confirmed_by = ""; anchor_confirmed_at = 0; gates = (Get-EmptyGateMap)
            test_plan = $planDest; plan_sha256 = $planHash; thread = $Thread; attempt = 1
            # Which runner profile this item is being worked under. Recorded so the stall
            # detector can name the runner that stalled and resolve the oracle ABOVE it
            # (U4). Empty means "whatever the surface's default profile is at the time",
            # which is what an item queued before this field existed also means.
            profile = $RunnerProfile
            line_mergeable = (-not (Test-LineCheckedOutElsewhere -Line $line))
            submitted_sha = $sha.Trim(); tested_at_sha = ""; merged_sha = ""
            results = @(); history = @()
        }
    }
    if ($correcting) {
        # Say WHAT was corrected, not just that something was: the whole point of allowing a
        # second -Submit is that the first one recorded the wrong thing, and a history line
        # that does not name it leaves the next reader guessing which field moved.
        $changes = @()
        if ($wasDeveloper -ne $Developer) { $changes += ("developer '{0}' -> '{1}'" -f $wasDeveloper, $Developer) }
        if ($wasBranch -ne $Branch) { $changes += ("branch '{0}' -> '{1}'" -f $wasBranch, $Branch) }
        if (@($changes).Count -eq 0) { $changes += "same values re-stated" }
        Add-History $item ("re-submitted before testing: " + ($changes -join "; ")) $Developer
    }
    Add-History $item "submitted for testing" $Developer
    Write-Item $item
    Write-Host ("Queued '{0}' for TESTING (branch {1} -> {2})." -f $Id, $Branch, $line) -ForegroundColor Green
    Write-Host ("  Plan copied to {0}" -f $planDest)
    Write-Host ("  {0} case(s) the tester must execute: {1}  (sha256 {2})" -f @($planCaseIds).Count, (@($planCaseIds) -join ", "), $planHash.Substring(0, 12))
    Write-Host "  A tester who is NOT the developer must claim and execute the plan."
    if (-not $item.line_mergeable) {
        Write-Host ("  NOTE: '{0}' is checked out in the main checkout, so the reviewer will have to" -f $line) -ForegroundColor Yellow
        Write-Host "        hand the merge back to the operator. Known now rather than at landing time." -ForegroundColor Yellow
    }
    if ($correcting) {
        Write-Host "  This CORRECTED an item that was already queued and unclaimed; the attempt is unchanged." -ForegroundColor Yellow
    }
    if ($bumped) {
        Write-Host ("  Attempt {0}: the previous attempt already carries a verdict, so its evidence file is kept, not overwritten." -f $item.attempt) -ForegroundColor Yellow
    }
    # The unregistered-developer WARNING that used to sit here was replaced by a refusal at
    # the top of this handler (2026-09-04). It warned after the item was queued, which is
    # after the only moment the warning could have helped.
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
        Die ("-Pass and -Fail both need -Evidence (what you ran and what it produced). A verdict " +
             "without evidence is an opinion. Long evidence: write it to a FILE and pass the " +
             "path - it is copied beside the item. An over-long INLINE string dies before " +
             "PowerShell even starts (exit 2, no message), so the path form is the safe one.")
    }
    # Long evidence does not fit a PS5.1 argument. Accept a FILE and copy it beside the
    # item, the same way the test plan is stored.
    #
    # Deciding "is this a path or prose?" with a bare Test-Path THROWS on prose under
    # this script's ErrorActionPreference=Stop - and the throw's trigger is subtle
    # (reviewer matrix, 2026-09-03): multi-line prose with NO colon anywhere throws
    # ArgumentException; the SAME prose containing a colon does not (a colon routes
    # Test-Path into drive-qualified handling that skips char validation), and
    # -LiteralPath changes no outcome. So: only a single-line, path-sized string is
    # ever OFFERED to Test-Path, and even that sits in a try/catch - prose must
    # never be able to crash a verdict.
    $evidenceText = $Evidence
    $evDest = Join-Path $QueueDir ("{0}.attempt{1}.evidence.md" -f $Id, $item.attempt)
    $isFile = $false
    if ($Evidence.Length -le 4096 -and $Evidence -notmatch "[`r`n]") {
        try { $isFile = Test-Path -LiteralPath $Evidence -PathType Leaf -ErrorAction Stop }
        catch { $isFile = $false }
    }
    # READ THE PLAN, AND READ THE EVIDENCE (passplan, 2026-09-06 - the helpers above say
    # why). Everything in this block is a check, not a change: it runs before the evidence
    # is copied beside the item, before the branch is resolved, before results[] moves, so a
    # refusal leaves the tester holding the claim with nothing recorded and a message that
    # names the cases. The pre_review auto-gate further down runs AFTER the verdict is
    # written and is untouched - a refused verdict never reaches it.
    $evidenceBody = $Evidence
    if ($isFile) { $evidenceBody = Read-Utf8Text $Evidence }
    $caseRows = @(Get-EvidenceVerdicts $evidenceBody)
    if ($Pass) {
        $planPath = [string]$item.test_plan
        if (-not $planPath -or -not (Test-Path -LiteralPath $planPath)) {
            Die (("'{0}' has no queued test plan at '{1}', so there is nothing to check the " +
                  "evidence against and no pass can be recorded. -Submit puts the plan there; " +
                  "if it was removed, the developer re-submits with -TestPlan.") -f $Id, $planPath)
        }
        # THE HASH. Recorded at -Submit and by the two plan-revision doors; anything else
        # that changed the queued file changed the ground the tester stood on.
        $planNow = Get-PlanSha256 $planPath
        $planThen = ""
        if ($item.PSObject.Properties.Name -contains "plan_sha256") { $planThen = [string]$item.plan_sha256 }
        if ($planThen) {
            if ($planThen -ne $planNow) {
                Die (("the queued plan for '{0}' is not the plan that was submitted. Recorded at " +
                      "submit: sha256 {1}. The file at {2} now hashes to {3}. Evidence written " +
                      "against a plan that changed since submit cannot be recorded - a pass " +
                      "describes the cases that were agreed, and these are not those. A plan " +
                      "revision goes through -Resubmit -TestPlan or -Requeue -TestPlan, which " +
                      "re-record the hash. Nothing has been recorded; you still hold the claim.") -f
                     $Id, $planThen, $planPath, $planNow)
            }
        } else {
            Write-Host "  NOTE: no plan hash was recorded at submit (the item predates 2026-09-06), so plan drift cannot be checked." -ForegroundColor Yellow
        }
        $planCases = @(Get-PlanCases (Read-Utf8Text $planPath))
        if ($planCases.Count -eq 0) {
            Die (("the queued plan for '{0}' ({1}) has no case headings a pass can be checked " +
                  "against: {2}. A plan the tool cannot read cannot produce a pass by having " +
                  "nothing to check. Record what you found with -Fail -PlanInadequate so the " +
                  "developer re-submits a plan with cases. Nothing has been recorded.") -f
                 $Id, $planPath, $CaseHeadingShape)
        }
        $compared = Compare-EvidenceToPlan $planCases $caseRows
        if (-not $compared.ok) {
            Die (("-Pass on '{0}' is REFUSED. A pass means EVERY case in the plan was executed " +
                  "and passed, and the evidence does not say that.`n" +
                  "  plan cases : {1}`n" +
                  "  the evidence says:`n{2}`n" +
                  "A case reads PASS only when its heading line ends in the bare word PASS - " +
                  "'## T5 - what it checks   PASS' - with nothing after it. A parenthetical, a " +
                  "caveat or a 'scoped' after PASS is not a pass. A case you could not execute " +
                  "in your environment is a plan inadequacy: record what you did run with " +
                  "-Fail -PlanInadequate and say what the plan should have made runnable. " +
                  "Nothing has been recorded; you still hold the claim.") -f
                 $Id, ($planCases -join ", "), (($compared.problems | ForEach-Object { "    " + $_ }) -join "`n"))
        }
        $caseRows = @($compared.cases)
    }
    if ($isFile) {
        Copy-IntoQueue $Evidence $evDest "-Evidence" "evidence file for this attempt"
        $evidenceText = $evDest
    } elseif ($Evidence.Length -gt 2000) {
        # Inline-but-long: spill the FULL text to the evidence file and record the path,
        # so the record stays readable and nothing is truncated. (2026-09-03: a tester's
        # long inline evidence died at the process boundary and the retry stored a
        # placeholder - this branch keeps the survivable version of that mistake lossless.)
        # UTF-8 WITHOUT a BOM, like the item file (deploystate, 2026-09-06): Set-Content
        # -Encoding UTF8 prefixes EF BB BF, and a spilled evidence file is read back by the
        # same UTF-8 readers - and by Python - as the item beside it.
        [System.IO.File]::WriteAllText($evDest, $Evidence, (New-Object System.Text.UTF8Encoding($false)))
        $evidenceText = $evDest
        Write-Host ("  Inline evidence ({0} chars) spilled to {1}" -f $Evidence.Length, $evDest)
    }
    if ($Fail -and -not $Reason) {
        Die ("-Fail needs -Reason: name the CASE that failed and what it revealed. The " +
             "developer fixes the finding, not the verdict.")
    }
    # CHECK THE EXIT CODE. `git rev-parse <missing-ref>` prints the ARGUMENT ITSELF on
    # STDOUT and exits 128 - so without this the branch NAME was stored as the round's sha
    # and as `tested_at_sha`, silently, with a green exit. Two rounds recorded that way carry
    # an identical "sha", which the stall detector reads as "the code did not move" and
    # escalates on: a tooling failure manufacturing a frontier escalation. Reproduced
    # 2026-08-30 by deleting the branch between two -Fail calls; the round recorded
    # `sha: "probe/oracle"`. -Submit (line 483) and -Resubmit (line 822) already checked
    # this; the verdict path was the one that did not.
    $headSha = (Invoke-GitCapture @("rev-parse", $item.branch) | Select-Object -First 1)
    if ($LASTEXITCODE -ne 0 -or -not $headSha) {
        Die ("branch '$($item.branch)' not found - cannot record a verdict against a branch " +
             "that does not resolve. Recreate it, or the verdict names a commit nobody can read.")
    }
    $item.results += [ordered]@{
        at = Now; by = $By; verdict = $(if ($Pass) { "pass" } else { "fail" })
        attempt = [int]$item.attempt
        sha = $headSha.Trim(); evidence = $evidenceText; reason = $Reason
        # The plan was written by the DEVELOPER. A tester who only reports pass/fail is
        # grading someone else's exam without reading the syllabus - say whether the plan
        # actually covered the change.
        plan_adequate = [bool]$PlanAdequate
        # Per-case verdicts as READ from the evidence headings: {case, verdict, line}. On a
        # pass every row is PASS and every plan case is present (or it was refused above);
        # on a fail they are whatever the tester wrote, recorded so -Show can say WHICH
        # case failed rather than one word for the whole item.
        cases = @($caseRows)
    }
    if ($Pass) {
        Write-Host ("  {0} case(s) checked against the plan, every one PASS." -f @($caseRows).Count)
    } elseif (@($caseRows).Count -gt 0) {
        Write-Host ("  {0} per-case verdict line(s) recorded from the evidence." -f @($caseRows).Count)
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
    # The item must be WRITTEN before the stall test reads it: the detector reads results[]
    # off disk, so running it first would score this round's failure as if it had not
    # happened - and the round that trips a stall is exactly the one that would be missed.
    if ($Fail) { Invoke-OracleOnStall $Id }
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
    #
    # ASKED ABOUT tested_at_sha, NOT $item.branch (2026-09-04). This used to resolve the
    # BRANCH as a live ref - and the success message ten lines below tells the developer to
    # retire the worktree, which is what deletes that ref. Item `wiki-gate-polish` followed
    # that instruction and then could not be recorded as merged at all: the tool refused a
    # true statement because of a step the tool itself had asked for, and the only ways out
    # were to recreate a branch nobody needed or to leave the queue lying about what landed.
    #
    # tested_at_sha is written once, by -Pass, and never rewritten. It cannot be deleted out
    # from under this check, and it is the STRONGER question anyway: not "does this merge
    # contain whatever that branch points at now?" but "does it contain the commit somebody
    # actually tested?" A branch tip can move after the pass; the tested commit cannot.
    if (-not $item.tested_at_sha) {
        Die ("'$Id' records no tested_at_sha, so there is no commit to prove this merge " +
             "contains. Only a tested item reaches review - if this one did not, it must not " +
             "be recorded as merged. Nothing has been recorded.") 1
    }
    [void](Invoke-GitCapture @("merge-base", "--is-ancestor", $item.tested_at_sha, $Sha))
    if ($LASTEXITCODE -ne 0) {
        Die ("'$Sha' does not contain '$($item.tested_at_sha)' - the commit this item's tests " +
             "passed at - so that is not a merge of this item. If the merge command failed, " +
             "it failed silently: check its exit code before recording the outcome. Nothing " +
             "has been recorded.") 1
    }
    # WHAT DID THIS MERGE SHIP? Derived from the merge range - never from a list the author
    # typed - BEFORE the state changes, so a derivation that cannot complete (an OB1 pin no
    # clone holds) refuses the record rather than leaving a merged item with no surfaces.
    $derived = Get-DeploySurfaces $item $Sha
    # Items merged before 2026-08-29 carry `fits_anchor` instead. That recorded the answer to
    # a DIFFERENT question (did this match the intent?), so do not read the two as one field.
    Set-Field $item "fits_codebase" ([bool]$FitsCodebase)
    $item.state = "merged"; $item.merged_sha = $Sha
    Set-Field $item "deploy_surfaces" @($derived.surfaces)
    Set-Field $item "deploy_pending" @($derived.surfaces)
    Set-Field $item "deployed" @()
    Set-Field $item "deploy_derived" ([ordered]@{ line_before = $derived.line_before; merge = $Sha; at = (Now); notes = @($derived.notes); skipped = @($derived.skipped) })
    Add-History $item "merged as $Sha" $By
    if (@($derived.surfaces).Count -gt 0) { Add-History $item ("deploy surfaces derived: " + (@($derived.surfaces) -join ", ")) $By }
    Drop-Claim $Id "reviewer"
    Write-Item $item
    Write-Host ("'{0}' MERGED as {1} by {2}." -f $Id, $Sha, $By) -ForegroundColor Green
    $lb7 = if ($derived.line_before) { ([string]$derived.line_before).Substring(0, 7) } else { "?" }
    if (@($derived.surfaces).Count -gt 0) {
        Write-Host ("  {0} deploy surface(s) derived from git diff --name-only {1}..{2} - NOT LIVE until each is closed:" -f @($derived.surfaces).Count, $lb7, $Sha.Substring(0, 7)) -ForegroundColor Yellow
        foreach ($s in @($derived.surfaces)) { Write-Host ("    - " + $s) -ForegroundColor Yellow }
        Write-Host ("  Deploy stays human-gated. When it is done and healthy: queue.ps1 -Deployed -Id {0} -By <operator> -Evidence <path> [-Surface <one of them>]" -f $Id)
    } else {
        Write-Host ("  No deploy surface derived from {0}..{1}: no OB1 integration image, no owui/ paste, no :local build context changed." -f $lb7, $Sha.Substring(0, 7))
    }
    foreach ($n in @($derived.notes)) { Write-Host ("  NOTE: " + $n) -ForegroundColor DarkGray }
    Write-Host ("  {0} can now retire the worktree (remove-worktree.ps1 -Id ...)." -f $item.developer)
    exit 0
}

if ($Deployed) {
    # THE DEPLOY IS RECORDED, NOT PERFORMED. MERGE-PROTOCOL section 4 keeps deploying to prod
    # containers and retagging :local a human-gated step, and this verb does not move that
    # line: it takes -By (a person - the auto: namespace is refused, as at the two gates) and
    # evidence that, PER SURFACE, names the image label or pin (or the pasted file's hash) and
    # the container's health state. A merged item that shipped an image or a paste cannot read
    # as finished until someone has looked at the running thing and written down what they saw.
    if (-not $Id -or -not $By -or -not $Evidence) {
        Die ("-Deployed needs -Id, -By (who verified the deploy) and -Evidence (per surface: the image " +
             "label or revision, or the pasted file's sha256, and the container's health state - a file " +
             "path or inline text). -Surface <name> closes one surface; without it, all open ones.")
    }
    Assert-HumanPrincipal $By "-Deployed"
    $item = Read-Item $Id
    if ($item.state -eq "deployed") { Die "'$Id' is already 'deployed' - every surface it derived is closed; there is nothing left to record" }
    if ($item.state -ne "merged") {
        Die (("'{0}' is '{1}', not 'merged'. A deploy is recorded on a merged item only - it follows the " +
              "merge, and -Merged is what derives the surfaces this closes.") -f $Id, $item.state)
    }
    $all = @(Get-ArrayField $item "deploy_surfaces")
    $pending = @(Get-DeployPending $item)
    if (-not ($item.PSObject.Properties.Name -contains "deploy_pending") -or ($all.Count -eq 0)) {
        Die (("'{0}' records no deploy surface: it merged before -Merged derived them (2026-09-06), or its " +
              "merge range changed no OB1 integration image, no :local build context and no owui/ file. There " +
              "is nothing to close, so -Deployed is refused rather than recorded against nothing.") -f $Id)
    }
    if ($pending.Count -eq 0) { Die "'$Id' has no deploy surface left open - its record is inconsistent (state merged, nothing pending); -Show it" }
    $closed = @(Get-ArrayField $item "deployed")
    $targets = $pending
    if ($Surface) {
        $prior = @($closed | Where-Object { $_.surface -eq $Surface } | Select-Object -First 1)
        if ($prior.Count -gt 0) {
            Die (("surface '{0}' on '{1}' is already closed - recorded by {2} at {3}. A surface is closed once; " +
                  "if the deploy was redone, that is a new item's evidence, not a second closure of this one.") -f $Surface, $Id, $prior[0].by, $prior[0].at)
        }
        if ($pending -notcontains $Surface) {
            Die (("'{0}' is not an open deploy surface of '{1}'. Open: {2}. The names are the ones -Merged " +
                  "derived (image:<compose service> / paste:<path>), not free text.") -f $Surface, $Id, ($pending -join ", "))
        }
        $targets = @($Surface)
    }
    # A FILE PATH OR INLINE TEXT, decided the way -Pass decides it (see the note there on
    # Test-Path and prose), and read as UTF-8.
    $isFile = $false
    if ($Evidence.Length -le 4096 -and $Evidence -notmatch "[`r`n]") {
        try { $isFile = Test-Path -LiteralPath $Evidence -PathType Leaf -ErrorAction Stop } catch { $isFile = $false }
    }
    $body = $Evidence
    if ($isFile) { $body = Read-Utf8Text $Evidence }
    $check = Test-DeployEvidence $targets $body
    if (-not $check.ok) {
        Die (("-Deployed on '{0}' is REFUSED - the evidence does not say, for every surface it closes, what " +
              "is running and that it is healthy:`n{1}`n" +
              "Per surface, one line that names it and carries (a) the pin - for an image the running " +
              "container's org.opencontainers.image.revision label or image id, for a paste the sha256 of " +
              "the file as pasted - and (b) the container's health state: State.Health.Status=healthy, or " +
              "State.Status=running for a container with no healthcheck. For example:`n" +
              "    openbrain-curator: label org.opencontainers.image.revision=d89c126, State.Health.Status=healthy, RestartCount=0`n" +
              "Nothing has been recorded.") -f $Id, (($check.problems | ForEach-Object { "    " + $_ }) -join "`n"))
    }
    # Every check above is before every change below.
    $n = $closed.Count + 1
    $evDest = Join-Path $QueueDir ("{0}.deploy{1}.evidence.md" -f $Id, $n)
    $evText = $Evidence
    if ($isFile) { Copy-IntoQueue $Evidence $evDest "-Evidence" "deploy evidence file for this item"; $evText = $evDest }
    elseif ($Evidence.Length -gt 2000) {
        [System.IO.File]::WriteAllText($evDest, $Evidence, (New-Object System.Text.UTF8Encoding($false)))
        $evText = $evDest
    }
    $rows = @($closed)
    foreach ($t in $targets) { $rows += [ordered]@{ surface = $t; by = $By; at = (Now); evidence = $evText } }
    Set-Field $item "deployed" @($rows)
    $remaining = @($pending | Where-Object { $targets -notcontains $_ })
    Set-Field $item "deploy_pending" @($remaining)
    Add-History $item ("deploy recorded for " + ($targets -join ", ")) $By
    if ($remaining.Count -eq 0) {
        Set-Field $item "state" "deployed"
        Add-History $item ("deployed - every derived surface closed ({0})" -f $all.Count) $By
    }
    Write-Item $item
    Write-Host ("'{0}': deploy recorded for {1} by {2}." -f $Id, ($targets -join ", "), $By) -ForegroundColor Green
    if ($remaining.Count -eq 0) { Write-Host ("  Every derived surface is closed - '{0}' is DEPLOYED." -f $Id) -ForegroundColor Green }
    else { Write-Host ("  Still open: {0}. The item stays 'merged' and -List keeps flagging it until those are closed." -f ($remaining -join ", ")) -ForegroundColor Yellow }
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
        [void](Assert-PlanReadable $TestPlan "-TestPlan")
        Copy-IntoQueue $TestPlan $item.test_plan "-TestPlan" "test plan for this item"
        # A revised plan is the plan the next pass is checked against, so its hash replaces
        # the submit-time one - the drift check compares against what was last agreed.
        Set-Field $item "plan_sha256" (Get-PlanSha256 $item.test_plan)
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
    # TWO WAYS BACK, and they are different journeys with the same name.
    #
    # THE REVIEWER'S - the stale-pass rule. Tests passed at `tested_at_sha`; if the
    # reviewer's rebase moved the content, that verdict no longer describes what would land.
    # Back to 'ready-to-test' - NOT a rejection, because nothing is wrong with the work.
    #
    # THE DEVELOPER'S - added 2026-09-04, because there was no way back from 'test-passed'
    # for the person best placed to use one. A developer who realises at the human gate that
    # the ARTIFACT is wrong could only wait to be told: spending an operator's release and a
    # reviewer's round to be handed back work its own author already knew was wrong. It lands
    # on 'anchor-confirmed', which is where -AmendAnchor already puts an item that is "back
    # with the developer", so the way forward is the ordinary -Submit and no new state is
    # invented. Deliberately NARROW: only the developer of the item, and only from
    # 'test-passed'. Once a reviewer holds it, it is theirs to return.
    if (-not $Id -or -not $By -or -not $Reason) { Die "-Requeue needs -Id, -By and -Reason" }
    $item = Read-Item $Id
    $byDeveloper = (($item.developer) -and ((Normalize-Id $By) -eq (Normalize-Id $item.developer)))
    if ($byDeveloper) {
        if ($item.state -ne "test-passed") {
            Die (("'{0}' is '{1}'. A developer withdraws their OWN work only from " +
                  "'test-passed' - before it is released, nothing is holding it up but you " +
                  "(just keep working and -Submit or -Resubmit); after review starts it " +
                  "belongs to the reviewer, who returns it with -Requeue or -Reject.") -f $Id, $item.state)
        }
    } else {
        Assert-Claim $item "reviewer" $By
    }
    # VALIDATE BEFORE MUTATING. A bad -TestPlan path must leave the item exactly as it was,
    # claim included - the failure mode this whole item is about is a command that half-runs.
    if ($TestPlan -and -not (Test-Path $TestPlan)) {
        Die ("-TestPlan must be a path to a file that exists (got '$TestPlan'). Nothing has been changed.")
    }
    if ($TestPlan) { [void](Assert-PlanReadable $TestPlan "-TestPlan") }
    # BUMP THE ATTEMPT. The evidence filename is `<id>.attempt<N>.evidence.md`, so leaving N
    # alone means the NEXT tester's -Pass copies straight over the LAST tester's evidence.
    # That happened on item `wikinote` (2026-09-04): the verdict survived in results[], the
    # evidence it rested on did not, and nobody noticed until the file was read for the
    # review. -Resubmit has bumped it since the same argument was made about a failed round;
    # a return to test is the same lap by another name, and so is a developer's withdrawal.
    Set-Field $item "attempt" ([int]$item.attempt + 1)
    Set-Field $item "tested_at_sha" ""
    # AND CARRY THE REVISED PLAN. A stale-pass return is exactly when a case is most likely
    # to be missing - the reviewer is the one who knows what the rebase changed - and until
    # now -Requeue took no -TestPlan at all, so the revision stayed on the reviewer's disk
    # and the next tester executed the plan already known to be incomplete (same item, same
    # day). -Submit and -Resubmit both take one; this is the third door into the same room.
    if ($TestPlan) {
        $planDest = if ($item.test_plan) { $item.test_plan } else { Join-Path $QueueDir "$Id.plan.md" }
        Copy-IntoQueue $TestPlan $planDest "-TestPlan" "test plan for this item"
        Set-Field $item "test_plan" $planDest
        Set-Field $item "plan_sha256" (Get-PlanSha256 $planDest)
        Add-History $item "test plan revised for attempt $([int]$item.attempt)" $By
    }
    if ($byDeveloper) {
        Set-Field $item "state" "anchor-confirmed"
        Add-History $item "developer WITHDREW it from test-passed (now attempt $($item.attempt)): $Reason" $By
        Write-Item $item
        Write-Host ("'{0}' WITHDRAWN by its developer - {1}" -f $Id, $Reason) -ForegroundColor Yellow
        Write-Host ("  It is back with you at 'anchor-confirmed', attempt {0}. The confirmed anchor still stands." -f $item.attempt)
        Write-Host ("  Change the artifact, then: queue.ps1 -Submit -Id {0} -Branch {1} -Developer {2} -TestPlan <path>" -f $Id, $item.branch, $item.developer)
    } else {
        Set-Field $item "state" "ready-to-test"
        Add-History $item "returned to test (now attempt $($item.attempt)): $Reason" $By
        Drop-Claim $Id "reviewer"
        Write-Item $item
        Write-Host ("'{0}' returned to TESTING as attempt {1} - {2}" -f $Id, $item.attempt, $Reason) -ForegroundColor Yellow
        if (-not $TestPlan) {
            Write-Host "  Plan unchanged. If what you rebased over needs a new case, re-run with" -ForegroundColor Yellow
            Write-Host "  -TestPlan <path> - the tester reads the queued copy, not yours." -ForegroundColor Yellow
        }
    }
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

Die "pass one of -Submit | -Resubmit | -List | -Show | -Claim | -Unclaim | -Pass | -Fail | -Approve | -Merged | -Deployed | -Requeue | -Reject | -Audit | -VerifyAudit"
