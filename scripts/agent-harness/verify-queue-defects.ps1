# verify-queue-defects.ps1 - executable proof for the six queue.ps1 defects found BY USE
# on 2026-09-04 (queue item `harnessq`).
#
#   .\scripts\agent-harness\verify-queue-defects.ps1                    # the shipped queue.ps1
#   .\scripts\agent-harness\verify-queue-defects.ps1 -Script <path>     # some OTHER copy
#
# WHY -Script EXISTS, and why it is not optional in spirit. This file tests the tool it is
# also driven by: `queue.ps1` invoked from an agent's worktree still runs whichever copy the
# caller names, and a test that silently drove the main checkout's copy would prove nothing
# about the change under review. Point it at the copy you edited, and point it at the copy
# you did NOT edit to see every check below go RED. Each check here failed against the
# pre-fix queue.ps1; that is the whole point of the file (a fix without a red-first test is
# the failure class this line spent a week on).
#
# HERMETIC. Every case builds its own scratch git repository and its own state directory
# under $env:TEMP, and points AI_STACK_WORKTREE_STATE at it. Nothing here can see, read or
# write the real queue - which matters more than usual, because the real queue is where the
# evidence these defects destroyed used to live.
#
# THE SIX DEFECTS, each with the incident behind it:
#   D1  -Requeue did not bump `attempt`, and the evidence filename derives from it, so the
#       next tester's -Pass OVERWROTE the previous tester's evidence file. Item `wikinote`.
#   D2  -Requeue took no -TestPlan, so a plan revised after a stale-pass return never
#       reached the queue and the next tester executed the old one. Same item, same day.
#   D3  -Merged resolved `$item.branch` as a REF to prove the merge sha contained the work -
#       and its own success message tells the developer to retire the worktree, which
#       deletes that ref. Item `wiki-gate-polish` could not be recorded as merged after
#       doing exactly what the tool told it to do.
#   D4  -Submit only WARNED when -Developer matched no worktree in the registry. Separation
#       of duties is a name comparison, so a typo silently switched it off - and there was
#       no way back, because a second -Submit was refused with 'already exists'.
#   D5  An -Evidence path that IS the queue's own destination file made Copy-Item fail onto
#       itself; the verdict was never recorded and the message named a cmdlet, not a cause.
#   D6  There was no developer path back from `test-passed`. Only a reviewer could -Requeue,
#       so a developer who found their own artifact wrong had to wait to be sent back.
#   D7  Test-KnownAgent exempted a MISSING or null worktree registry from the D4 check -
#       'cannot check' is not 'checked and failed' - but not an EMPTY-but-present one.
#       remove-worktree.ps1 writes exactly that when the LAST worktree is retired, so
#       ordinary correct cleanup refused EVERY developer's -Submit with exit 4.
#
# ADDED 2026-09-06 (item `passplan`), from a defect found by READING a merged item:
#   D8  -Pass never opened the plan or the evidence. Item `curator2` merged on evidence
#       whose T5 and T6 headings read `PASS (scoped ...)` and `PASS (... chat half NOT
#       run)` - the tester wrote the truth on the line and the tool recorded one word for
#       the whole item. The REAL curator2 plan and evidence are replayed here, unedited,
#       from documentation/evidence/passplan/fixtures/, and must be refused naming T5 and
#       T6. Then the synthetic matrix: absent, FAIL, SKIPPED, 'PASS (scoped', 'PASS
#       (partial', anything after PASS - each refused by name; all-bare-PASS accepted; the
#       per-case rows land in results[] and -Show prints them; -Fail records them without
#       refusing; a plan with no case headings is refused at every door it can enter by.
#   D9  Nothing tied a verdict to the plan it was written against. -Submit now records
#       plan_sha256; -Pass re-hashes the queued file and refuses on drift naming both
#       hashes; -Resubmit -TestPlan and -Requeue -TestPlan re-record it.
# FOUND BY THE TESTER OF passplan ATTEMPT 1 (2026-09-06):
#   D10 Write-Item wrote the item with `Set-Content -Encoding ASCII`, and PS5.1's
#       ConvertTo-Json does not escape non-ASCII, so the em-dash in every real evidence
#       heading was stored as `?` in the per-case `line` - the record no longer quoted the
#       tester. Items are UTF-8 (no BOM) now, and every reader says so.
#   D11 A `## T2 ... PASS` inside a fenced code block in the evidence satisfied plan case T2
#       (the protocol's own example, pasted); a fenced `## T9` in a plan became a phantom
#       case; a plan whose only heading was fenced was accepted at -Submit. Fences (``` and
#       ~~~) count for nothing on either side now.

[CmdletBinding()]
param(
    [string]$Script = "",
    [string]$Root = "",
    [switch]$KeepFixtures
)

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here

if (-not $Script) { $Script = Join-Path $PSScriptRoot "queue.ps1" }
$Script = (Resolve-Path $Script).Path
$PsExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }
if (-not $Root) { $Root = Join-Path $env:TEMP ("queue-defects-" + $PID) }
if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null

Write-Host ("queue.ps1 under test : {0}" -f $Script) -ForegroundColor Cyan
Write-Host ("fixture root         : {0}" -f $Root) -ForegroundColor Cyan

$results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, $ok, [string]$detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

function Invoke-Git {
    # Deliberately not a helper that swallows git errors: this drill's whole subject is a
    # tool that trusted an unchecked ref resolution, so its own git adapter reports failures.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git.exe @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed: " + ($out -join "`n")) }
    return @($out)
}

function New-Fixture([string]$name) {
    # A scratch repository with a line (`base`) and a work branch, plus its own state dir.
    $repo = Join-Path $Root $name
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    Push-Location $repo
    try {
        Invoke-Git init -q -b base | Out-Null
        Invoke-Git config user.email "qdrill@example.invalid" | Out-Null
        Invoke-Git config user.name "queue defect drill" | Out-Null
        Set-Content -Path (Join-Path $repo "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git commit -q -m "scratch base" | Out-Null
        Invoke-Git checkout -q -b work/qd | Out-Null
        Set-Content -Path (Join-Path $repo "WORK.md") -Encoding ascii -Value "the work"
        Invoke-Git add WORK.md | Out-Null
        Invoke-Git commit -q -m "the work" | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location }
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return @{ repo = $repo; state = $state; name = $name }
}

function Add-Registry($fix, [string[]]$Ids) {
    # Test-KnownAgent answers TRUE when there is no registry (testers and reviewers have no
    # worktree), so a fixture that wants the unknown-developer refusal exercised has to
    # supply one. Without this the D4 case would pass vacuously - the exact shape of check
    # this whole item is about.
    $rows = [ordered]@{}
    foreach ($i in $Ids) { $rows[$i] = [ordered]@{ id = $i; branch = "work/$i" } }
    (@{ worktrees = $rows } | ConvertTo-Json -Depth 6) |
        Set-Content -Path (Join-Path $fix.state "worktrees.json") -Encoding ASCII
}

function Invoke-Q($fix, [string[]]$QArgs) {
    $prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
    $env:AI_STACK_WORKTREE_STATE = $fix.state
    $env:AI_STACK_WORK_LINE = "base"
    Push-Location $fix.repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -File $Script @QArgs 2>&1 }
    finally {
        $ErrorActionPreference = $prev
        Pop-Location
        $env:AI_STACK_WORKTREE_STATE = $prevState; $env:AI_STACK_WORK_LINE = $prevLine
    }
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { "$_" }) -join "`n") }
}

function Get-QItem($fix, [string]$id) {
    # Items are UTF-8 (no BOM) since D10; PS5.1's default is the ANSI code page.
    $p = Join-Path $fix.state "queue\$id.json"
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json)
}
function Get-QFile($fix, [string]$leaf) { return (Join-Path $fix.state ("queue\" + $leaf)) }
function Get-QRaw($fix, [string]$id) { return (Get-Content -Raw -Path (Join-Path $fix.state "queue\$id.json") -Encoding UTF8) }
function Invoke-QUtf8($fix, [string[]]$QArgs) {
    # Like Invoke-Q, but the child's console output and the parent's decoding of it are both
    # UTF-8, so a non-ASCII character the tool PRINTS (-Show) can be asserted on. Without this
    # the OEM code page on both ends turns an em-dash into `?` in transit and a check on the
    # printed line would be a check on the console, not on the tool.
    $prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
    $env:AI_STACK_WORKTREE_STATE = $fix.state
    $env:AI_STACK_WORK_LINE = "base"
    $prevOut = [Console]::OutputEncoding
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    # Switches (-Show, -Id) stay bare - quoted, '-Show' would reach the script as a string
    # argument and bind to nothing. Values are single-quoted.
    $quoted = @($QArgs | ForEach-Object { if ($_ -match '^-[A-Za-z]') { $_ } else { "'" + ($_ -replace "'", "''") + "'" } }) -join " "
    $cmd = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; & '" + $Script + "' " + $quoted
    Push-Location $fix.repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -Command $cmd 2>&1 }
    finally {
        $ErrorActionPreference = $prev
        Pop-Location
        [Console]::OutputEncoding = $prevOut
        $env:AI_STACK_WORKTREE_STATE = $prevState; $env:AI_STACK_WORK_LINE = $prevLine
    }
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { "$_" }) -join "`n") }
}
function Get-Sha256([string]$path) {
    if (-not (Test-Path $path)) { return "(absent)" }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
}
function First-Line([string]$s) {
    $l = @($s -split "`n" | Where-Object { $_.Trim() })
    if ($l.Count -eq 0) { return "(no output)" }
    return $l[0].Trim()
}

# --- shared inputs -------------------------------------------------------------------
$anchorFile = Join-Path $Root "anchor.json"
Set-Content -Path $anchorFile -Encoding ascii -Value @(
    '{',
    '  "goal": "WORK.md states what the work was, unambiguously.",',
    '  "artifact": "WORK.md - a one-line note produced by the drill fixture.",',
    '  "audience": "The next agent to read the file with no other context.",',
    '  "acceptance": [',
    '    "WORK.md exists on the branch. Fail: it is absent.",',
    '    "It contains exactly one line. Fail: it is empty or contradictory."',
    '  ],',
    '  "out_of_scope": ["Anything outside WORK.md."],',
    '  "findings_sink": "documentation/notes/queue-defect-drill.md"',
    '}')
# Plans carry CASE HEADINGS (`## Case <n>` / `## T<n>`) since D8: -Submit refuses a plan
# the case parser cannot read, and -Pass checks the evidence against these headings. The
# evidence fixtures below carry the matching `## Case 1 ... PASS` line for the same reason -
# under the D8 rule, evidence that names no case cannot pass. That every fixture in this
# file had to change is the contract working.
$planV1 = Join-Path $Root "plan-v1.md"
Set-Content -Path $planV1 -Encoding ascii -Value @(
    "# Test plan, attempt 1",
    "## Case 1 - WORK.md exists. Fail: it is absent.")
$planV2 = Join-Path $Root "plan-v2.md"
Set-Content -Path $planV2 -Encoding ascii -Value @(
    "# Test plan, REVISED after the return to test",
    "## Case 2 - (new) the case the first plan was missing.")
$case1Pass = "## Case 1 - WORK.md exists   PASS"

function Initialize-ToReview($fix, [string]$id, [string]$dev, [string]$evidence) {
    # Drive an item from nothing to `reviewing`, the state every review-side case starts in.
    Invoke-Q $fix @("-Propose", "-Id", $id, "-Anchor", $anchorFile, "-Developer", $dev) | Out-Null
    Invoke-Q $fix @("-ConfirmAnchor", "-Id", $id, "-By", "qoperator") | Out-Null
    Invoke-Q $fix @("-Submit", "-Id", $id, "-Branch", "work/qd", "-Developer", $dev, "-TestPlan", $planV1) | Out-Null
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "tester", "-By", "qtester") | Out-Null
    Invoke-Q $fix @("-Pass", "-Id", $id, "-By", "qtester", "-Evidence", $evidence, "-PlanAdequate") | Out-Null
    Invoke-Q $fix @("-Approve", "-Id", $id, "-By", "qoperator") | Out-Null
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "reviewer", "-By", "qrev") | Out-Null
}

# ======================================================================================
Step "D1  -Requeue bumps the attempt, so the next pass cannot overwrite the last evidence"
# ======================================================================================
# THE INCIDENT (wikinote, 2026-09-04): the evidence filename is <id>.attempt<N>.evidence.md.
# A reviewer returned the item to test, `attempt` stayed 1, and the second tester's -Pass
# wrote over the first tester's file. The verdict survived in results[]; the evidence it
# rested on did not.
$f1 = New-Fixture "d1"
$ev1 = Join-Path $Root "d1-evidence-attempt1.md"
Set-Content -Path $ev1 -Encoding ascii -Value @("# attempt 1 evidence", $case1Pass, "ran case 1: WORK.md present.")
Initialize-ToReview $f1 "qd1" "qdev" $ev1
$att1Path = Get-QFile $f1 "qd1.attempt1.evidence.md"
Check "setup: attempt 1's evidence is beside the item" (Test-Path $att1Path) $att1Path
$att1Before = Get-Sha256 $att1Path

$r = Invoke-Q $f1 @("-Requeue", "-Id", "qd1", "-By", "qrev", "-Reason", "rebase moved the tested content")
Check "the reviewer's -Requeue still succeeds" ($r.code -eq 0) ("exit=" + $r.code)
$it = Get-QItem $f1 "qd1"
Check "D1: -Requeue BUMPED attempt to 2" ([int]$it.attempt -eq 2) ("attempt=" + $it.attempt)

$ev2 = Join-Path $Root "d1-evidence-attempt2.md"
Set-Content -Path $ev2 -Encoding ascii -Value @("# attempt 2 evidence", $case1Pass, "re-ran case 1 on the adapted content.")
Invoke-Q $f1 @("-Claim", "-Id", "qd1", "-Role", "tester", "-By", "qtester2") | Out-Null
$r = Invoke-Q $f1 @("-Pass", "-Id", "qd1", "-By", "qtester2", "-Evidence", $ev2, "-PlanAdequate")
Check "the second tester's pass is recorded" ($r.code -eq 0) ("exit=" + $r.code)
$att1After = Get-Sha256 $att1Path
Check "D1: attempt 1's evidence file is BYTE-IDENTICAL after the second pass" `
    ($att1After -eq $att1Before) ("before=" + $att1Before + " after=" + $att1After)
$att2Path = Get-QFile $f1 "qd1.attempt2.evidence.md"
Check "D1: the second tester's evidence landed in its OWN file" `
    ((Test-Path $att2Path) -and ((Get-Content -Raw $att2Path) -match "attempt 2 evidence")) $att2Path

# ======================================================================================
Step "D2  -Requeue carries a REVISED test plan into the queue"
# ======================================================================================
# A stale-pass return is exactly when the plan is most likely to need a case added - the
# reviewer knows what the rebase changed. -Resubmit has taken -TestPlan since the same
# argument was made about a failed round; -Requeue did not, so the revision stayed in the
# reviewer's hands and the next tester read the old plan.
$f2 = New-Fixture "d2"
$ev = Join-Path $Root "d2-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f2 "qd2" "qdev" $ev
$planDest = Get-QFile $f2 "qd2.plan.md"
Check "setup: the queued plan is attempt 1's" ((Get-Content -Raw $planDest) -match "attempt 1")
# THE BAD PLAN GOES FIRST, while the reviewer still holds the claim. Run after a successful
# -Requeue it would be refused with exit 3 (the claim is dropped on the way out) and would
# have proved nothing about -TestPlan at all - a check that passes while checking nothing,
# which is the failure class this whole item exists to answer. Caught red-handed here.
$r = Invoke-Q $f2 @("-Requeue", "-Id", "qd2", "-By", "qrev", "-Reason", "x", "-TestPlan", (Join-Path $Root "no-such-plan.md"))
Check "D2: a -TestPlan that is not a file is refused (exit 1, not the claim's exit 3)" ($r.code -eq 1) ("exit=" + $r.code)
Check "D2: the refused -Requeue changed nothing - still 'reviewing'" ((Get-QItem $f2 "qd2").state -eq "reviewing") `
    ("state=" + (Get-QItem $f2 "qd2").state)
$r = Invoke-Q $f2 @("-Requeue", "-Id", "qd2", "-By", "qrev", "-Reason", "rebase changed the file", "-TestPlan", $planV2)
Check "D2: -Requeue -TestPlan is accepted (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)
Check "D2: the queued plan is the REVISED one" `
    ((Get-Content -Raw $planDest) -match "REVISED after the return to test") `
    ("first line: " + (First-Line (Get-Content -Raw $planDest)))
Check "D2: the revision is in the item's history" ((Get-QRaw $f2 "qd2") -match "test plan revised")

# ======================================================================================
Step "D3  -Merged proves containment against the IMMUTABLE tested_at_sha, not a live ref"
# ======================================================================================
# THE INCIDENT (wiki-gate-polish): -Merged's own success message says "you can now retire
# the worktree". remove-worktree.ps1 deletes the branch. Recording the merge afterwards
# then failed, because the containment check resolved $item.branch - a ref that the tool's
# own advice had just removed. tested_at_sha is stored at -Pass and never rewritten, which
# is both immutable and a STRONGER question: does the merge contain what was TESTED?
$f3 = New-Fixture "d3"
$ev = Join-Path $Root "d3-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f3 "qd3" "qdev" $ev
$it = Get-QItem $f3 "qd3"
$testedAt = $it.tested_at_sha
Check "setup: tested_at_sha was recorded at the pass" ($testedAt -match "^[0-9a-f]{40}$") $testedAt
Push-Location $f3.repo
try {
    $preMerge = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
    Invoke-Git merge --no-ff -q work/qd -m "merge the work (evidence: drill)" | Out-Null
    $mergeSha = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
    # THE STEP THE TOOL ITSELF TOLD THE DEVELOPER TO TAKE.
    # -GitArgs is spelled out: `-D` is an unambiguous prefix of the common parameter
    # -Debug, so PowerShell binds it there and git receives `branch work/qd` - which
    # CREATES a branch instead of deleting one. It did exactly that on the first run.
    Invoke-Git -GitArgs @("branch", "-D", "work/qd") | Out-Null
    & git.exe rev-parse --verify --quiet "refs/heads/work/qd" | Out-Null
    $refGone = ($LASTEXITCODE -ne 0)
} finally { Pop-Location }
Check "setup: the branch ref really is gone" $refGone

$r = Invoke-Q $f3 @("-Merged", "-Id", "qd3", "-By", "qrev", "-Sha", $preMerge, "-FitsCodebase")
Check "D3: a sha that does NOT contain the tested commit is still REFUSED" `
    (($r.code -ne 0) -and ((Get-QItem $f3 "qd3").state -ne "merged")) `
    ("exit=" + $r.code + " state=" + (Get-QItem $f3 "qd3").state)
$r = Invoke-Q $f3 @("-Merged", "-Id", "qd3", "-By", "qrev", "-Sha", "0000000000000000000000000000000000000000", "-FitsCodebase")
Check "D3: a nonexistent sha is refused, not recorded" `
    (($r.code -ne 0) -and ((Get-QItem $f3 "qd3").state -ne "merged")) ("exit=" + $r.code)
$r = Invoke-Q $f3 @("-Merged", "-Id", "qd3", "-By", "qrev", "-Sha", $mergeSha, "-FitsCodebase")
Check "D3: the real merge IS recorded even though the branch ref was deleted" `
    (($r.code -eq 0) -and ((Get-QItem $f3 "qd3").state -eq "merged")) `
    ("exit=" + $r.code + " state=" + (Get-QItem $f3 "qd3").state + " | " + (First-Line $r.out))

# ======================================================================================
Step "D4  -Submit REFUSES an unregistered developer, and a mistyped one can be corrected"
# ======================================================================================
# Separation of duties is a string comparison (Normalize-Id). A -Developer that matches no
# worktree cannot be compared against anything, so the guard silently stops guarding - and
# the old behaviour printed a WARNING after the item had already been queued, which is the
# worst of both: the trap is sprung and the door is shut, because a second -Submit was
# refused with 'already exists'.
$f4 = New-Fixture "d4"
Add-Registry $f4 @("qdev", "qdev2")
Invoke-Q $f4 @("-Propose", "-Id", "qd4", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f4 @("-ConfirmAnchor", "-Id", "qd4", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f4 @("-Submit", "-Id", "qd4", "-Branch", "work/qd", "-Developer", "qdevv", "-TestPlan", $planV1)
Check "D4: an unregistered -Developer is REFUSED (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
# NOT just "the word registry appears". The pre-fix tool ALSO said that - in a warning, after
# it had queued the item - so a bare -match on "registry" passed against the very behaviour
# this check exists to reject. It has to name the refusal.
Check "D4: the message says REFUSED, not warned (the pre-fix warning also said 'registry')" `
    (($r.out -match "matches no worktree in the registry") -and ($r.out -match "refused, not warned about")) (First-Line $r.out)
Check "D4: nothing was queued - the item is untouched" `
    ((Get-QItem $f4 "qd4").state -eq "anchor-confirmed") ("state=" + (Get-QItem $f4 "qd4").state)
$r = Invoke-Q $f4 @("-Submit", "-Id", "qd4", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "D4: the corrected id submits normally" `
    (($r.code -eq 0) -and ((Get-QItem $f4 "qd4").state -eq "ready-to-test")) ("exit=" + $r.code)

# THE CORRECTION PATH: a submitted-but-unclaimed item can be re-submitted to fix what was
# recorded. Before this, an id typed wrong (or a branch typed wrong) was permanent.
$f4b = New-Fixture "d4b"
Add-Registry $f4b @("qdev", "qdev2")
Invoke-Q $f4b @("-Propose", "-Id", "qd4b", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f4b @("-ConfirmAnchor", "-Id", "qd4b", "-By", "qoperator") | Out-Null
Invoke-Q $f4b @("-Submit", "-Id", "qd4b", "-Branch", "work/qd", "-Developer", "qdev2", "-TestPlan", $planV1) | Out-Null
Check "setup: queued under the WRONG (but registered) developer" ((Get-QItem $f4b "qd4b").developer -eq "qdev2")
$r = Invoke-Q $f4b @("-Submit", "-Id", "qd4b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV2)
Check "D4: re-submitting an UNCLAIMED ready-to-test item is allowed" ($r.code -eq 0) `
    ("exit=" + $r.code + " | " + (First-Line $r.out))
$it = Get-QItem $f4b "qd4b"
Check "D4: the corrected developer is what the item now records" ($it.developer -eq "qdev") ("developer=" + $it.developer)
Check "D4: the correction did not silently bump the attempt" ([int]$it.attempt -eq 1) ("attempt=" + $it.attempt)
Check "D4: the correction is in the history" ((Get-QRaw $f4b "qd4b") -match "re-submitted before testing")
# But NOT once a tester holds it - that would move the ground under someone mid-run.
Invoke-Q $f4b @("-Claim", "-Id", "qd4b", "-Role", "tester", "-By", "qtester") | Out-Null
$r = Invoke-Q $f4b @("-Submit", "-Id", "qd4b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "D4: a CLAIMED item cannot be re-submitted underneath its tester" ($r.code -ne 0) ("exit=" + $r.code)

# ======================================================================================
Step "D5  -Evidence pointing at the queue's OWN destination file is refused by name"
# ======================================================================================
# Copy-Item onto itself throws, the throw happened before the verdict was written, and the
# message named a cmdlet. The tester sees an error about a file copy and has no reason to
# connect it to "the verdict was not recorded" - so the state, the claim and the results
# array must all be provably untouched, and the message must say what is actually wrong.
$f5 = New-Fixture "d5"
Invoke-Q $f5 @("-Propose", "-Id", "qd5", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f5 @("-ConfirmAnchor", "-Id", "qd5", "-By", "qoperator") | Out-Null
Invoke-Q $f5 @("-Submit", "-Id", "qd5", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
Invoke-Q $f5 @("-Claim", "-Id", "qd5", "-Role", "tester", "-By", "qtester") | Out-Null
$dest = Get-QFile $f5 "qd5.attempt1.evidence.md"
Set-Content -Path $dest -Encoding ascii -Value @("# evidence written straight to the queue", $case1Pass, "case 1 green.")
$destBefore = Get-Sha256 $dest
$r = Invoke-Q $f5 @("-Pass", "-Id", "qd5", "-By", "qtester", "-Evidence", $dest, "-PlanAdequate")
Check "D5: the verdict is refused (non-zero exit)" ($r.code -ne 0) ("exit=" + $r.code)
Check "D5: the message NAMES the problem, not a cmdlet" ($r.out -match "the queue's own evidence file") (First-Line $r.out)
$it = Get-QItem $f5 "qd5"
Check "D5: NOT half-succeeded - no verdict was recorded" (@($it.results).Count -eq 0) ("results=" + @($it.results).Count)
Check "D5: NOT half-succeeded - the item is still 'testing'" ($it.state -eq "testing") ("state=" + $it.state)
Check "D5: the tester still holds the claim and can retry" (Test-Path (Get-QFile $f5 "qd5.tester.claim"))
Check "D5: the file the tester wrote is untouched" ((Get-Sha256 $dest) -eq $destBefore)
# And the ordinary retry - the same content from anywhere else - still works.
$elsewhere = Join-Path $Root "d5-evidence.md"
Copy-Item -LiteralPath $dest -Destination $elsewhere -Force
$r = Invoke-Q $f5 @("-Pass", "-Id", "qd5", "-By", "qtester", "-Evidence", $elsewhere, "-PlanAdequate")
Check "D5: the same evidence from outside the queue dir is accepted" `
    (($r.code -eq 0) -and ((Get-QItem $f5 "qd5").state -eq "test-passed")) ("exit=" + $r.code)

# ======================================================================================
Step "D5b  the SAME guard on every other caller-supplied path copied into the queue dir"
# ======================================================================================
# Reported by a second agent on the same day, in a different command: -Submit -TestPlan
# pointed at a file already inside the queue dir died with "Cannot overwrite the item ...
# with itself". Same root cause as D5, which makes it a CLASS, not two bugs - writing your
# plan or your evidence straight into the queue dir is a reasonable thing to do, because
# that is visibly where it ends up. So every copy site goes through one guard, and every
# copy site is exercised here. The dangerous half is a HALF-APPLIED mutation, so each case
# proves the item's state afterwards, not just the exit code.
$f5b = New-Fixture "d5b"
$qdir = Join-Path $f5b.state "queue"
New-Item -ItemType Directory -Force -Path $qdir | Out-Null

# -Propose -Anchor, where the destination is <id>.anchor.json.
$anchorDest = Join-Path $qdir "qd5b.anchor.json"
Copy-Item -LiteralPath $anchorFile -Destination $anchorDest -Force
$r = Invoke-Q $f5b @("-Propose", "-Id", "qd5b", "-Anchor", $anchorDest, "-Developer", "qdev")
Check "D5b: -Propose -Anchor AT the destination is refused by name" `
    (($r.code -ne 0) -and ($r.out -match "the queue's own anchor file")) ("exit=" + $r.code + " | " + (First-Line $r.out))
Check "D5b: no item was created by the refused -Propose" ($null -eq (Get-QItem $f5b "qd5b"))

Invoke-Q $f5b @("-Propose", "-Id", "qd5b", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f5b @("-ConfirmAnchor", "-Id", "qd5b", "-By", "qoperator") | Out-Null
# -Submit -TestPlan, where the destination is <id>.plan.md. THE REPORTED CASE.
$planDest5b = Join-Path $qdir "qd5b.plan.md"
Copy-Item -LiteralPath $planV1 -Destination $planDest5b -Force
$r = Invoke-Q $f5b @("-Submit", "-Id", "qd5b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planDest5b)
Check "D5b: -Submit -TestPlan AT the destination is refused by name" `
    (($r.code -ne 0) -and ($r.out -match "the queue's own test plan")) ("exit=" + $r.code + " | " + (First-Line $r.out))
Check "D5b: the refused -Submit left the item at 'anchor-confirmed'" `
    ((Get-QItem $f5b "qd5b").state -eq "anchor-confirmed") ("state=" + (Get-QItem $f5b "qd5b").state)
Invoke-Q $f5b @("-Submit", "-Id", "qd5b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
Check "D5b: the same plan from outside the queue dir submits normally" ((Get-QItem $f5b "qd5b").state -eq "ready-to-test")

# -Resubmit -TestPlan, whose destination is the item's recorded test_plan.
Invoke-Q $f5b @("-Claim", "-Id", "qd5b", "-Role", "tester", "-By", "qtester") | Out-Null
$ev = Join-Path $Root "d5b-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value "case 1 failed."
Invoke-Q $f5b @("-Fail", "-Id", "qd5b", "-By", "qtester", "-Reason", "case 1", "-Evidence", $ev, "-PlanInadequate") | Out-Null
Check "setup: the item is test-failed at attempt 1" `
    (((Get-QItem $f5b "qd5b").state -eq "test-failed") -and ([int](Get-QItem $f5b "qd5b").attempt -eq 1))
$r = Invoke-Q $f5b @("-Resubmit", "-Id", "qd5b", "-By", "qdev", "-TestPlan", $planDest5b)
Check "D5b: -Resubmit -TestPlan AT the destination is refused by name" `
    (($r.code -ne 0) -and ($r.out -match "the queue's own test plan")) ("exit=" + $r.code + " | " + (First-Line $r.out))
$it = Get-QItem $f5b "qd5b"
Check "D5b: the refused -Resubmit did NOT half-apply (state and attempt unchanged)" `
    (($it.state -eq "test-failed") -and ([int]$it.attempt -eq 1)) ("state=" + $it.state + " attempt=" + $it.attempt)

# -Requeue -TestPlan, the door added by D2, through the same guard.
$f5c = New-Fixture "d5c"
$ev = Join-Path $Root "d5c-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f5c "qd5c" "qdev" $ev
$planDest5c = Get-QFile $f5c "qd5c.plan.md"
$r = Invoke-Q $f5c @("-Requeue", "-Id", "qd5c", "-By", "qrev", "-Reason", "rebase moved it", "-TestPlan", $planDest5c)
Check "D5b: -Requeue -TestPlan AT the destination is refused by name" `
    (($r.code -ne 0) -and ($r.out -match "the queue's own test plan")) ("exit=" + $r.code + " | " + (First-Line $r.out))
$it = Get-QItem $f5c "qd5c"
Check "D5b: the refused -Requeue did NOT half-apply (still 'reviewing', attempt 1)" `
    (($it.state -eq "reviewing") -and ([int]$it.attempt -eq 1)) ("state=" + $it.state + " attempt=" + $it.attempt)
Check "D5b: the reviewer still holds the claim after the refusal" (Test-Path (Get-QFile $f5c "qd5c.reviewer.claim"))

# ======================================================================================
Step "D6  the DEVELOPER can send their own test-passed item back when the artifact must change"
# ======================================================================================
# A developer who realises at the human gate that the artifact is wrong had no move: only a
# reviewer could -Requeue, and waiting to be sent back spends a reviewer's round on work its
# author already knows is wrong. This lands on `anchor-confirmed` - the state -AmendAnchor
# already uses for "back with the developer" - so the way forward is the ordinary -Submit,
# and no new state is invented.
$f6 = New-Fixture "d6"
$ev = Join-Path $Root "d6-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Invoke-Q $f6 @("-Propose", "-Id", "qd6", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f6 @("-ConfirmAnchor", "-Id", "qd6", "-By", "qoperator") | Out-Null
Invoke-Q $f6 @("-Submit", "-Id", "qd6", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
Invoke-Q $f6 @("-Claim", "-Id", "qd6", "-Role", "tester", "-By", "qtester") | Out-Null
Invoke-Q $f6 @("-Pass", "-Id", "qd6", "-By", "qtester", "-Evidence", $ev, "-PlanAdequate") | Out-Null
Check "setup: the item is parked at the human gate" ((Get-QItem $f6 "qd6").state -eq "test-passed")
$r = Invoke-Q $f6 @("-Requeue", "-Id", "qd6", "-By", "qdev", "-Reason", "the artifact is wrong - I am changing it")
Check "D6: the developer's own -Requeue is accepted (exit 0)" ($r.code -eq 0) ("exit=" + $r.code + " | " + (First-Line $r.out))
$it = Get-QItem $f6 "qd6"
Check "D6: the item is back with the developer ('anchor-confirmed')" ($it.state -eq "anchor-confirmed") ("state=" + $it.state)
Check "D6: the stale pass is cleared (tested_at_sha empty)" ([string]$it.tested_at_sha -eq "") ("tested_at_sha=" + $it.tested_at_sha)
Check "D6: attempt bumped, so the next pass cannot overwrite attempt 1's evidence" ([int]$it.attempt -eq 2) ("attempt=" + $it.attempt)
Check "D6: attempt 1's evidence file survives the withdrawal" (Test-Path (Get-QFile $f6 "qd6.attempt1.evidence.md"))
$r = Invoke-Q $f6 @("-Submit", "-Id", "qd6", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV2)
Check "D6: the ordinary -Submit takes it forward again" `
    (($r.code -eq 0) -and ((Get-QItem $f6 "qd6").state -eq "ready-to-test")) ("exit=" + $r.code)
Check "D6: the attempt survived the re-submit" ([int](Get-QItem $f6 "qd6").attempt -eq 2) ("attempt=" + (Get-QItem $f6 "qd6").attempt)
# The developer path is NARROW: only from test-passed, and only for the developer.
$r = Invoke-Q $f6 @("-Requeue", "-Id", "qd6", "-By", "qdev", "-Reason", "changed my mind again")
Check "D6: the developer may NOT -Requeue from any other state" ($r.code -ne 0) `
    ("exit=" + $r.code + " state=" + (Get-QItem $f6 "qd6").state)
$r = Invoke-Q $f6 @("-Requeue", "-Id", "qd6", "-By", "qstranger", "-Reason", "not my item")
Check "D6: a third party with no reviewer claim is still refused" ($r.code -ne 0) ("exit=" + $r.code)

# ======================================================================================
Step "D7  an EMPTY registry is 'cannot check', not 'checked and failed'"
# ======================================================================================
# THE INCIDENT: D4 turned the unregistered-developer WARNING into a refusal, and correctly
# exempted the case where there is no registry to check against. But it read the registry as
# `if (-not $rows) { return $true }`, and an empty-but-present {"worktrees":{}} is not
# falsey - it is an object with no properties. So the check fell through to a membership
# test against an empty list and refused EVERYBODY.
#
# That shape is not exotic: it is what remove-worktree.ps1 writes every time the LAST
# worktree is retired, because it rewrites the whole file from a hashtable. Retiring your
# own worktree - the documented end of the merge protocol - stopped the queue accepting work
# from anyone. THE PREMISE IS CHECKED HERE rather than cited, because a fix aimed at a shape
# the tool does not actually produce would be a fix for nothing.

$f7 = New-Fixture "d7"
# --- the premise: remove-worktree.ps1 really does write the empty-but-present shape -------
$wtDir = Join-Path $Root "d7-wt"
Push-Location $f7.repo
try { Invoke-Git worktree add -q -b work/d7only $wtDir | Out-Null } finally { Pop-Location }
$reg7 = Join-Path $f7.state "worktrees.json"
(@{ worktrees = @{ d7only = @{ id = "d7only"; path = $wtDir; branch = "work/d7only" } } } |
    ConvertTo-Json -Depth 6) | Set-Content -Path $reg7 -Encoding ASCII
$prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
$env:AI_STACK_WORKTREE_STATE = $f7.state; $env:AI_STACK_WORK_LINE = "base"
Push-Location $f7.repo
try { & $PsExe -NoProfile -NonInteractive -File (Join-Path $PSScriptRoot "remove-worktree.ps1") -Id d7only 2>&1 | Out-Null }
finally {
    Pop-Location
    $env:AI_STACK_WORKTREE_STATE = $prevState; $env:AI_STACK_WORK_LINE = $prevLine
}
$after7 = $null
if (Test-Path $reg7) { $after7 = Get-Content -Raw -Path $reg7 | ConvertFrom-Json }
Check "D7 premise: retiring the LAST worktree leaves the registry FILE in place" (Test-Path $reg7) $reg7
Check "D7 premise: it still has a 'worktrees' key and it is NOT null" `
    (($null -ne $after7) -and ($after7.PSObject.Properties.Name -contains "worktrees") -and ($null -ne $after7.worktrees)) `
    ("parsed=" + ($null -ne $after7))
Check "D7 premise: and it holds ZERO rows - the exact shape the guard has to survive" `
    (($null -ne $after7) -and ($null -ne $after7.worktrees) -and (@($after7.worktrees.PSObject.Properties).Count -eq 0)) `
    ("rows=" + $(if (($null -eq $after7) -or ($null -eq $after7.worktrees)) { "n/a" } else { @($after7.worktrees.PSObject.Properties).Count }))

# --- the fix: that shape must not refuse anyone -------------------------------------------
# THE RED-FIRST CHECK. Against the pre-fix queue.ps1 this is exit 4 and the item never
# leaves `anchor-confirmed`.
$f7b = New-Fixture "d7b"
Set-Content -Path (Join-Path $f7b.state "worktrees.json") -Encoding ASCII -Value '{"worktrees":{}}'
Invoke-Q $f7b @("-Propose", "-Id", "qd7", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f7b @("-ConfirmAnchor", "-Id", "qd7", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f7b @("-Submit", "-Id", "qd7", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "D7: -Submit against an EMPTY registry is ACCEPTED (exit 0), not refused" ($r.code -eq 0) `
    ("exit=" + $r.code + " | " + (First-Line $r.out))
Check "D7: and the item really was queued" ((Get-QItem $f7b "qd7").state -eq "ready-to-test") `
    ("state=" + (Get-QItem $f7b "qd7").state)
# A developer who never owned a worktree is the same case - nothing about the NAME matters
# when the registry names nobody.
Invoke-Q $f7b @("-Propose", "-Id", "qd7b", "-Anchor", $anchorFile, "-Developer", "nobody-ever") | Out-Null
Invoke-Q $f7b @("-ConfirmAnchor", "-Id", "qd7b", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f7b @("-Submit", "-Id", "qd7b", "-Branch", "work/qd", "-Developer", "nobody-ever", "-TestPlan", $planV1)
Check "D7: an arbitrary developer id is equally accepted when the registry names nobody" `
    ($r.code -eq 0) ("exit=" + $r.code + " | " + (First-Line $r.out))

# --- the truth table, each row proven by running it ---------------------------------------
# null and absent-key were already exempt; they are here so a future edit to this function
# cannot quietly lose one while fixing another.
$f7c = New-Fixture "d7c"
Set-Content -Path (Join-Path $f7c.state "worktrees.json") -Encoding ASCII -Value '{"worktrees":null}'
Invoke-Q $f7c @("-Propose", "-Id", "qd7c", "-Anchor", $anchorFile, "-Developer", "anydev") | Out-Null
Invoke-Q $f7c @("-ConfirmAnchor", "-Id", "qd7c", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f7c @("-Submit", "-Id", "qd7c", "-Branch", "work/qd", "-Developer", "anydev", "-TestPlan", $planV1)
Check "D7 table: a NULL worktrees value stays exempt (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)

$f7d = New-Fixture "d7d"
Set-Content -Path (Join-Path $f7d.state "worktrees.json") -Encoding ASCII -Value '{}'
Invoke-Q $f7d @("-Propose", "-Id", "qd7d", "-Anchor", $anchorFile, "-Developer", "anydev") | Out-Null
Invoke-Q $f7d @("-ConfirmAnchor", "-Id", "qd7d", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f7d @("-Submit", "-Id", "qd7d", "-Branch", "work/qd", "-Developer", "anydev", "-TestPlan", $planV1)
Check "D7 table: an ABSENT worktrees key stays exempt (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)

# THE ROW THAT MUST NOT MOVE. If this ever goes green-by-accepting, the availability fix has
# been traded for an authorization hole: one real row means the check CAN be made, so it must.
$f7e = New-Fixture "d7e"
Add-Registry $f7e @("qdev")
Invoke-Q $f7e @("-Propose", "-Id", "qd7e", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f7e @("-ConfirmAnchor", "-Id", "qd7e", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f7e @("-Submit", "-Id", "qd7e", "-Branch", "work/qd", "-Developer", "stranger", "-TestPlan", $planV1)
Check "D7 table: ONE row is enough to ENFORCE - an unregistered developer is still refused (exit 4)" `
    ($r.code -eq 4) ("exit=" + $r.code)
Check "D7 table: the refused submit queued nothing" `
    ((Get-QItem $f7e "qd7e").state -eq "anchor-confirmed") ("state=" + (Get-QItem $f7e "qd7e").state)
$r = Invoke-Q $f7e @("-Submit", "-Id", "qd7e", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "D7 table: and the REGISTERED developer still gets through (exit 0)" `
    (($r.code -eq 0) -and ((Get-QItem $f7e "qd7e").state -eq "ready-to-test")) ("exit=" + $r.code)

# ======================================================================================
Step "D8  a scoped, skipped, failed or MISSING case cannot become a pass"
# ======================================================================================
# THE INCIDENT (curator2, 2026-09-04): eight cases in the plan; the tester's evidence file
# put the verdict on each case heading, and two of them read `PASS (scoped - read the
# caveat)` and `PASS (blocking path driven; chat half NOT run)`. -Pass never opened the
# plan or the evidence, so the item recorded one word - pass - and merged; the image those
# two cases existed to exercise had never been built. The tester told the truth on the
# heading line. The tool did not read it.
#
# THE REPLAY IS THE REAL FILES. documentation/evidence/passplan/fixtures/ holds the
# curator2 plan and evidence byte-for-byte as they sit in the live queue. Their headings are
# the fixture: the checks below prove the two lines are still there BEFORE driving them
# through -Pass, so an edit that made the case easier makes this drill red instead.
$fixDir = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path "documentation\evidence\passplan\fixtures"
$c2Plan = Join-Path $fixDir "curator2.plan.md"
$c2Ev = Join-Path $fixDir "curator2.attempt1.evidence.md"
Check "D8 fixture: the real curator2 plan and evidence are committed beside this drill" `
    ((Test-Path $c2Plan) -and (Test-Path $c2Ev)) $fixDir
$c2Text = ""
if (Test-Path $c2Ev) { $c2Text = [System.IO.File]::ReadAllText($c2Ev, [System.Text.Encoding]::UTF8) }
Check "D8 fixture: T5's heading still reads 'PASS (scoped'" ($c2Text -match "(?m)^## T5 .*PASS \(scoped")
Check "D8 fixture: T6's heading still reads '... chat half NOT run)'" ($c2Text -match "(?m)^## T6 .*chat half NOT run\)\s*$")
$f8 = New-Fixture "d8"
Invoke-Q $f8 @("-Propose", "-Id", "qd8", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f8 @("-ConfirmAnchor", "-Id", "qd8", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f8 @("-Submit", "-Id", "qd8", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $c2Plan)
Check "D8: the real curator2 plan submits, and -Submit counts its 8 cases (T0..T7)" `
    (($r.code -eq 0) -and ($r.out -match "8 case\(s\) the tester must execute: T0, T1, T2, T3, T4, T5, T6, T7")) ("exit=" + $r.code)
Invoke-Q $f8 @("-Claim", "-Id", "qd8", "-Role", "tester", "-By", "qtester") | Out-Null
$r = Invoke-Q $f8 @("-Pass", "-Id", "qd8", "-By", "qtester", "-Evidence", $c2Ev, "-PlanAdequate")
Check "D8 REPLAY: the real curator2 evidence is REFUSED (non-zero exit)" ($r.code -ne 0) ("exit=" + $r.code)
Check "D8 REPLAY: the refusal names T5 and quotes its line's verdict" ($r.out -match "T5: PASS \(scoped")
Check "D8 REPLAY: the refusal names T6 and quotes its line's verdict" ($r.out -match "T6: PASS \(blocking path driven; chat half NOT run\)")
Check "D8 REPLAY: T1's 'PASS (with caveats)' mutation line is refused too - a parenthetical after PASS is not a pass" `
    ($r.out -match "T1: PASS \(with caveats\)")
# T7 has NO heading in the real evidence: the tester wrote "T7's happy path is covered here
# at the same (scoped) level" inside T5's caveat. A case covered inside another case's
# caveat was never executed as a case, and the parser says so.
Check "D8 REPLAY: T7 is refused as MISSING (it was folded into T5's caveat, never run as a case)" ($r.out -match "T7: MISSING")
Check "D8 REPLAY: the cases that DID pass (T0, T2, T3, T4) are not listed as problems" `
    (-not ($r.out -match "(?m)^\s+T(0|2|3|4):"))
$it = Get-QItem $f8 "qd8"
Check "D8 REPLAY: NOTHING recorded - results empty, still 'testing', claim held, no evidence file copied" `
    ((@($it.results).Count -eq 0) -and ($it.state -eq "testing") -and (Test-Path (Get-QFile $f8 "qd8.tester.claim")) `
     -and -not (Test-Path (Get-QFile $f8 "qd8.attempt1.evidence.md"))) `
    ("results=" + @($it.results).Count + " state=" + $it.state)

# --- the synthetic matrix, on a three-case plan ------------------------------------------
# One item, one claim: a refused -Pass leaves the claim held, so every refusal is driven
# against the same item and the accepted evidence goes last. Each refusal proves the exit,
# the NAME in the message and the untouched state - a refusal that half-applied would be
# worse than the old behaviour.
$plan3 = Join-Path $Root "plan-3cases.md"
Set-Content -Path $plan3 -Encoding ascii -Value @(
    "# three cases", "", "## T0 - the first", "run x; expect y", "", "## T1 - the second", "", "## T2 - the third",
    "", "## Out of scope", "not a case: no id after the hashes")
function Write-Ev([string]$leaf, [string[]]$lines) {
    # UTF-8 WITHOUT a BOM - what a tester's editor writes, and what carries the em-dash in T1.
    $p = Join-Path $Root $leaf
    [System.IO.File]::WriteAllText($p, (($lines -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    return $p
}
$f8b = New-Fixture "d8b"
Invoke-Q $f8b @("-Propose", "-Id", "qd8b", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f8b @("-ConfirmAnchor", "-Id", "qd8b", "-By", "qoperator") | Out-Null
Invoke-Q $f8b @("-Submit", "-Id", "qd8b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
Invoke-Q $f8b @("-Claim", "-Id", "qd8b", "-Role", "tester", "-By", "qtester") | Out-Null
$emDash = [string][char]0x2014
$matrix = @(
    @{ name = "one case ABSENT (T2 never named)";      lines = @("## T0 - the first  PASS", "## T1 $emDash the second  PASS");                              expect = "T2: MISSING" },
    @{ name = "one heading reads FAIL";                 lines = @("## T0 - the first  PASS", "## T1 - the second  FAIL", "## T2 - the third  PASS");        expect = "T1: FAIL" },
    @{ name = "one heading reads SKIPPED";              lines = @("## T0 - the first  PASS", "## T1 - the second  SKIPPED", "## T2 - the third  PASS");     expect = "T1: SKIPPED" },
    @{ name = "'PASS (scoped' after the verdict";       lines = @("## T0 - the first  PASS", "## T1 - the second  PASS", "## T2 - the third  PASS (scoped - could not stage it)"); expect = "T2: PASS \(scoped - could not stage it\)" },
    @{ name = "'PASS (partial' after the verdict";      lines = @("## T0 - the first  PASS", "## T1 - the second  PASS", "## T2 - the third  PASS (partial)"); expect = "T2: PASS \(partial\)" },
    @{ name = "anything after PASS (a dash and words)"; lines = @("## T0 - the first  PASS - but see below", "## T1 - the second  PASS", "## T2 - the third  PASS"); expect = "T0: PASS - but see below" },
    @{ name = "lower-case 'pass' is prose, not a verdict"; lines = @("## T0 - the first  PASS", "## T1 - the second  pass", "## T2 - the third  PASS"); expect = "T1: \(no verdict on the heading line\)" },
    @{ name = "a heading with no verdict at all";       lines = @("## T0 - the first  PASS", "## T1 - the second", "## T2 - the third  PASS");             expect = "T1: \(no verdict on the heading line\)" }
)
$i = 0
foreach ($m in $matrix) {
    $i++
    $evp = Write-Ev ("d8b-ev-" + $i + ".md") $m.lines
    $r = Invoke-Q $f8b @("-Pass", "-Id", "qd8b", "-By", "qtester", "-Evidence", $evp, "-PlanAdequate")
    $it = Get-QItem $f8b "qd8b"
    Check ("D8: " + $m.name + " -> refused, named") (($r.code -ne 0) -and ($r.out -match $m.expect)) ("exit=" + $r.code + " | " + (First-Line $r.out))
    Check ("D8: " + $m.name + " -> nothing recorded") ((@($it.results).Count -eq 0) -and ($it.state -eq "testing")) ("state=" + $it.state)
}
# The refusal lists EVERY problem, not the first one found.
$evp = Write-Ev "d8b-ev-multi.md" @("## T0 - the first  FAIL", "## T1 - the second  PASS (scoped)")
$r = Invoke-Q $f8b @("-Pass", "-Id", "qd8b", "-By", "qtester", "-Evidence", $evp, "-PlanAdequate")
Check "D8: a refusal lists ALL the offending cases (FAIL + scoped + MISSING in one message)" `
    (($r.code -ne 0) -and ($r.out -match "T0: FAIL") -and ($r.out -match "T1: PASS \(scoped\)") -and ($r.out -match "T2: MISSING")) (First-Line $r.out)
# Inline evidence is parsed the same way as a file.
$r = Invoke-Q $f8b @("-Pass", "-Id", "qd8b", "-By", "qtester", "-Evidence", "## T0 - x  PASS`n## T1 - y  PASS`n## T2 - z  SKIPPED", "-PlanAdequate")
Check "D8: INLINE evidence is held to the same rule" (($r.code -ne 0) -and ($r.out -match "T2: SKIPPED")) ("exit=" + $r.code)
# And the one shape that IS a pass: every plan case, bare PASS last on the line. T1 uses an
# em-dash and T2 a lower-case id, because real evidence does and the parser must not care.
$evOk = Write-Ev "d8b-ev-ok.md" @("# evidence", "## T0 - the first  PASS", "", "    ran x, got y", "", "## T1 $emDash the second   PASS  ", "## t2 - the third  PASS", "", "## T1 $emDash RED mutation of the second  PASS")
$r = Invoke-Q $f8b @("-Pass", "-Id", "qd8b", "-By", "qtester", "-Evidence", $evOk, "-PlanAdequate")
$it = Get-QItem $f8b "qd8b"
Check "D8: evidence with bare PASS on every plan case is ACCEPTED (exit 0, test-passed)" `
    (($r.code -eq 0) -and ($it.state -eq "test-passed")) ("exit=" + $r.code + " | " + (First-Line $r.out))
$rows = @()
if (@($it.results).Count -gt 0 -and ($it.results[0].PSObject.Properties.Name -contains "cases")) { $rows = @($it.results[0].cases) }
Check "D8: per-case verdicts landed in results[0].cases as {case, verdict, line} - one row per evidence heading (4)" `
    (($rows.Count -eq 4) -and (@($rows | Where-Object { $_.verdict -eq "PASS" }).Count -eq 4) `
     -and (@($rows | ForEach-Object { $_.case }) -join ",") -eq "T0,T1,T2,T1" `
     -and ($rows[1].line -match "^## T1 . the second\s+PASS$")) `
    ("rows=" + $rows.Count + " cases=" + ((@($rows | ForEach-Object { $_.case })) -join ","))
Check "D8: the verdict record says which attempt it was (attempt=1)" ([int]$it.results[0].attempt -eq 1)
$r = Invoke-Q $f8b @("-Show", "-Id", "qd8b")
Check "D8: -Show prints the per-case verdicts under the attempt" `
    (($r.out -match "--- VERDICTS ---") -and ($r.out -match "(?m)^attempt 1: PASS by qtester") -and ($r.out -match "(?m)^\s+T2\s+PASS\s*$")) (First-Line $r.out)

# -Fail RECORDS the per-case rows and refuses nothing - the developer needs to know which
# case failed, and a fail is never held to the all-PASS rule.
$f8c = New-Fixture "d8c"
Invoke-Q $f8c @("-Propose", "-Id", "qd8c", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f8c @("-ConfirmAnchor", "-Id", "qd8c", "-By", "qoperator") | Out-Null
Invoke-Q $f8c @("-Submit", "-Id", "qd8c", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
Invoke-Q $f8c @("-Claim", "-Id", "qd8c", "-Role", "tester", "-By", "qtester") | Out-Null
$evFail = Write-Ev "d8c-ev-fail.md" @("## T0 - the first  PASS", "## T1 - the second  FAIL", "(T2 not reached)")
$r = Invoke-Q $f8c @("-Fail", "-Id", "qd8c", "-By", "qtester", "-Reason", "T1 fails", "-Evidence", $evFail, "-PlanInadequate")
$it = Get-QItem $f8c "qd8c"
$rows = @(); if (@($it.results).Count -gt 0) { $rows = @($it.results[0].cases) }
Check "D8: -Fail is recorded (exit 0, test-failed) with the per-case rows it found (T0 PASS, T1 FAIL)" `
    (($r.code -eq 0) -and ($it.state -eq "test-failed") -and ($rows.Count -eq 2) -and ($rows[1].case -eq "T1") -and ($rows[1].verdict -eq "FAIL")) `
    ("exit=" + $r.code + " state=" + $it.state + " rows=" + $rows.Count)
$r = Invoke-Q $f8c @("-Show", "-Id", "qd8c")
Check "D8: -Show names the FAILED case, not just the verdict" ($r.out -match "(?m)^\s+T1\s+FAIL\s*$")

# A plan the parser cannot read is refused at EVERY door a plan enters by.
$planNoCases = Join-Path $Root "plan-no-cases.md"
Set-Content -Path $planNoCases -Encoding ascii -Value @("# Test plan", "Case 1: WORK.md exists. Fail: it is absent.", "T2: something else.")
$f8d = New-Fixture "d8d"
Invoke-Q $f8d @("-Propose", "-Id", "qd8d", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f8d @("-ConfirmAnchor", "-Id", "qd8d", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f8d @("-Submit", "-Id", "qd8d", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planNoCases)
Check "D8: -Submit REFUSES a plan with zero case headings (non-zero)" ($r.code -ne 0) ("exit=" + $r.code)
Check "D8: ... and the message says what a heading looks like ('## T0' and '## Case 1')" `
    (($r.out -match "no case headings") -and ($r.out -match "## T0 - what it checks") -and ($r.out -match "## Case 1 - what it checks")) (First-Line $r.out)
Check "D8: ... and nothing was queued (still anchor-confirmed, no plan copied)" `
    (((Get-QItem $f8d "qd8d").state -eq "anchor-confirmed") -and -not (Test-Path (Get-QFile $f8d "qd8d.plan.md")))
$r = Invoke-Q $f8c @("-Resubmit", "-Id", "qd8c", "-By", "qdev", "-TestPlan", $planNoCases)
$it = Get-QItem $f8c "qd8c"
Check "D8: -Resubmit -TestPlan with zero case headings is refused, nothing half-applied (test-failed, attempt 1)" `
    (($r.code -ne 0) -and ($r.out -match "no case headings") -and ($it.state -eq "test-failed") -and ([int]$it.attempt -eq 1)) ("exit=" + $r.code)
$f8e = New-Fixture "d8e"
$ev = Join-Path $Root "d8e-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f8e "qd8e" "qdev" $ev
$r = Invoke-Q $f8e @("-Requeue", "-Id", "qd8e", "-By", "qrev", "-Reason", "x", "-TestPlan", $planNoCases)
$it = Get-QItem $f8e "qd8e"
Check "D8: -Requeue -TestPlan with zero case headings is refused, nothing half-applied (reviewing, attempt 1)" `
    (($r.code -ne 0) -and ($r.out -match "no case headings") -and ($it.state -eq "reviewing") -and ([int]$it.attempt -eq 1)) ("exit=" + $r.code)

# ======================================================================================
Step "D9  evidence for a plan that CHANGED since submit cannot be recorded"
# ======================================================================================
# A pass describes the cases that were agreed at submit. Nothing tied the verdict to that
# file: the queued plan could be rewritten by hand (or by a stray copy) between submit and
# pass, and -Pass would happily check the evidence against the new one. -Submit records the
# hash of the QUEUED copy; -Pass re-hashes the same file and refuses on drift naming both;
# the two legitimate revision doors (-Resubmit -TestPlan, -Requeue -TestPlan) re-record it.
$f9 = New-Fixture "d9"
Invoke-Q $f9 @("-Propose", "-Id", "qd9", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f9 @("-ConfirmAnchor", "-Id", "qd9", "-By", "qoperator") | Out-Null
Invoke-Q $f9 @("-Submit", "-Id", "qd9", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
$it = Get-QItem $f9 "qd9"
$queued9 = Get-QFile $f9 "qd9.plan.md"
$recorded9 = [string]$it.plan_sha256
Check "D9: -Submit recorded plan_sha256, and it is the sha256 of the QUEUED plan file" `
    (($recorded9 -match "^[0-9a-f]{64}$") -and ($recorded9 -eq (Get-Sha256 $queued9).ToLower())) ("plan_sha256=" + $recorded9)
# THE DRIFT: the queued file is rewritten underneath the item - here, a case is dropped,
# which is the direction that turns a refusal into a pass.
Set-Content -Path $queued9 -Encoding ascii -Value @("# three cases, one quietly removed", "## T0 - the first", "## T1 - the second")
$drifted9 = (Get-Sha256 $queued9).ToLower()
Check "setup: the queued plan now hashes differently" ($drifted9 -ne $recorded9)
Invoke-Q $f9 @("-Claim", "-Id", "qd9", "-Role", "tester", "-By", "qtester") | Out-Null
$evTwo = Write-Ev "d9-ev-two.md" @("## T0 - the first  PASS", "## T1 - the second  PASS")
$r = Invoke-Q $f9 @("-Pass", "-Id", "qd9", "-By", "qtester", "-Evidence", $evTwo, "-PlanAdequate")
$it = Get-QItem $f9 "qd9"
Check "D9: -Pass against the drifted plan is REFUSED (non-zero) even though the evidence matches the file on disk" `
    ($r.code -ne 0) ("exit=" + $r.code)
Check "D9: the refusal names BOTH hashes - the one recorded at submit and the one on disk now" `
    (($r.out -match $recorded9) -and ($r.out -match $drifted9)) (First-Line $r.out)
Check "D9: nothing recorded - results empty, still 'testing', claim held" `
    ((@($it.results).Count -eq 0) -and ($it.state -eq "testing") -and (Test-Path (Get-QFile $f9 "qd9.tester.claim"))) ("state=" + $it.state)
# THE LEGITIMATE DOOR: a failed round, then -Resubmit -TestPlan. The hash follows the plan.
Invoke-Q $f9 @("-Fail", "-Id", "qd9", "-By", "qtester", "-Reason", "plan drifted", "-Evidence", "the queued plan is not the submitted one", "-PlanInadequate") | Out-Null
$plan3b = Join-Path $Root "plan-3cases-b.md"
Set-Content -Path $plan3b -Encoding ascii -Value @("# three cases, revised", "## T0 - the first", "## T1 - the second", "## T2 - the third, reworded")
$r = Invoke-Q $f9 @("-Resubmit", "-Id", "qd9", "-By", "qdev", "-TestPlan", $plan3b)
$it = Get-QItem $f9 "qd9"
Check "D9: -Resubmit -TestPlan re-records plan_sha256 as the hash of the newly queued plan" `
    (($r.code -eq 0) -and ([string]$it.plan_sha256 -eq (Get-Sha256 $queued9).ToLower()) -and ([string]$it.plan_sha256 -ne $recorded9)) `
    ("plan_sha256=" + $it.plan_sha256)
Invoke-Q $f9 @("-Claim", "-Id", "qd9", "-Role", "tester", "-By", "qtester") | Out-Null
$evThree = Write-Ev "d9-ev-three.md" @("## T0 - the first  PASS", "## T1 - the second  PASS", "## T2 - the third  PASS")
$r = Invoke-Q $f9 @("-Pass", "-Id", "qd9", "-By", "qtester", "-Evidence", $evThree, "-PlanAdequate")
Check "D9: ... and a pass against the re-recorded plan is accepted (exit 0, test-passed)" `
    (($r.code -eq 0) -and ((Get-QItem $f9 "qd9").state -eq "test-passed")) ("exit=" + $r.code + " | " + (First-Line $r.out))
# The other door: the reviewer's -Requeue -TestPlan.
$f9b = New-Fixture "d9b"
$ev = Join-Path $Root "d9b-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f9b "qd9b" "qdev" $ev
$before9b = [string](Get-QItem $f9b "qd9b").plan_sha256
$r = Invoke-Q $f9b @("-Requeue", "-Id", "qd9b", "-By", "qrev", "-Reason", "rebase changed the file", "-TestPlan", $plan3)
$it = Get-QItem $f9b "qd9b"
Check "D9: -Requeue -TestPlan re-records plan_sha256 as the hash of the newly queued plan" `
    (($r.code -eq 0) -and ([string]$it.plan_sha256 -eq (Get-Sha256 (Get-QFile $f9b "qd9b.plan.md")).ToLower()) -and ([string]$it.plan_sha256 -ne $before9b)) `
    ("before=" + $before9b + " after=" + $it.plan_sha256)
# And a -Requeue WITHOUT -TestPlan leaves the hash exactly where it was.
$f9c = New-Fixture "d9c"
$ev = Join-Path $Root "d9c-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f9c "qd9c" "qdev" $ev
$before9c = [string](Get-QItem $f9c "qd9c").plan_sha256
Invoke-Q $f9c @("-Requeue", "-Id", "qd9c", "-By", "qrev", "-Reason", "rebase moved it, plan unchanged") | Out-Null
Check "D9: -Requeue without -TestPlan leaves plan_sha256 untouched" ([string](Get-QItem $f9c "qd9c").plan_sha256 -eq $before9c)

# ======================================================================================
Step "D10  an em-dash in an evidence heading round-trips through -Pass, the record and -Show"
# ======================================================================================
# THE INCIDENT (passplan attempt 1): the stored per-case `line` read `## T1 ? b   PASS`.
# Write-Item wrote ASCII; the record that exists to quote the tester dropped their character.
# Byte-level on disk (E2 80 94, no BOM), then through the UTF-8 reader, then through -Show.
$f10 = New-Fixture "d10"
Invoke-Q $f10 @("-Propose", "-Id", "qd10", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f10 @("-ConfirmAnchor", "-Id", "qd10", "-By", "qoperator") | Out-Null
Invoke-Q $f10 @("-Submit", "-Id", "qd10", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
Invoke-Q $f10 @("-Claim", "-Id", "qd10", "-Role", "tester", "-By", "qtester") | Out-Null
$dashLine = "## T1 " + $emDash + " the second  PASS"
$evDash = Write-Ev "d10-ev.md" @("## T0 - the first  PASS", $dashLine, "## T2 - the third  PASS")
$r = Invoke-Q $f10 @("-Pass", "-Id", "qd10", "-By", "qtester", "-Evidence", $evDash, "-PlanAdequate")
Check "D10: the pass with an em-dash heading is accepted" ($r.code -eq 0) ("exit=" + $r.code)
$itemBytes = [System.IO.File]::ReadAllBytes((Get-QFile $f10 "qd10.json"))
$hex = ($itemBytes | ForEach-Object { $_.ToString("X2") }) -join " "
Check "D10: the item file carries the em-dash as UTF-8 bytes E2 80 94 (not '?' = 3F)" `
    ($hex -match "E2 80 94") ("first bytes: " + $hex.Substring(0, [Math]::Min(24, $hex.Length)))
Check "D10: ... and has no BOM" (-not ($itemBytes[0] -eq 0xEF -and $itemBytes[1] -eq 0xBB)) ("byte0=" + $itemBytes[0].ToString("X2"))
$it = Get-QItem $f10 "qd10"
Check "D10: read back through the UTF-8 reader, results[0].cases[1].line is the heading verbatim" `
    ([string]$it.results[0].cases[1].line -eq $dashLine) ("line=" + $it.results[0].cases[1].line)
$r = Invoke-QUtf8 $f10 @("-Show", "-Id", "qd10")
Check "D10: -Show prints the heading with the em-dash intact" ($r.out.Contains($dashLine)) `
    ("exit=" + $r.code + " has verdicts block=" + $r.out.Contains("--- VERDICTS ---") + " | " + (First-Line $r.out))
$r = Invoke-Q $f10 @("-List")
Check "D10: -List still reads the UTF-8 item (exit 0, item listed)" (($r.code -eq 0) -and ($r.out -match "qd10\s+test-passed")) ("exit=" + $r.code)
# The FAIL path stores the tester's words too: a curator2-style row survives verbatim.
$f10b = New-Fixture "d10b"
Invoke-Q $f10b @("-Propose", "-Id", "qd10b", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f10b @("-ConfirmAnchor", "-Id", "qd10b", "-By", "qoperator") | Out-Null
Invoke-Q $f10b @("-Submit", "-Id", "qd10b", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
Invoke-Q $f10b @("-Claim", "-Id", "qd10b", "-Role", "tester", "-By", "qtester") | Out-Null
$scopedLine = "## T2 " + $emDash + " the third  PASS (scoped " + $emDash + " read the caveat)"
$evScoped = Write-Ev "d10b-ev.md" @("## T0 - the first  PASS", "## T1 - the second  FAIL", $scopedLine)
Invoke-Q $f10b @("-Fail", "-Id", "qd10b", "-By", "qtester", "-Reason", "T1", "-Evidence", $evScoped, "-PlanInadequate") | Out-Null
$it = Get-QItem $f10b "qd10b"
Check "D10: a -Fail row's verdict keeps its em-dashes: 'PASS (scoped - read the caveat)' as written" `
    ([string]$it.results[0].cases[2].verdict -eq ("PASS (scoped " + $emDash + " read the caveat)")) ("verdict=" + $it.results[0].cases[2].verdict)

# ======================================================================================
Step "D11  a heading inside a fenced code block counts for nothing - plan and evidence, every door"
# ======================================================================================
# THE INCIDENT (passplan attempt 1): the protocol's fenced example, pasted into evidence,
# satisfied a plan case; a fenced `## T9` in a plan became a case -Pass then demanded; a plan
# whose only heading was fenced was accepted at -Submit.
$planFencedOnly = Join-Path $Root "plan-fenced-only.md"
Set-Content -Path $planFencedOnly -Encoding ascii -Value @("# plan", "An example of a heading:", '```', "## T0 - looks like a case", '```')
$planPhantom = Join-Path $Root "plan-phantom.md"
Set-Content -Path $planPhantom -Encoding ascii -Value @(
    "# plan", "## T0 - the first", "## T1 - the second", "", "Headings look like this:", '   ```markdown', "## T9 - phantom", '   ```',
    "", "~~~", "## T8 - phantom too", "~~~", "", "Case 7: not a heading either")
$f11 = New-Fixture "d11"
Invoke-Q $f11 @("-Propose", "-Id", "qd11", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f11 @("-ConfirmAnchor", "-Id", "qd11", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f11 @("-Submit", "-Id", "qd11", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planFencedOnly)
Check "D11: -Submit REFUSES a plan whose only heading is inside a fence" `
    (($r.code -ne 0) -and ($r.out -match "no case headings") -and ((Get-QItem $f11 "qd11").state -eq "anchor-confirmed")) ("exit=" + $r.code)
$r = Invoke-Q $f11 @("-Submit", "-Id", "qd11", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planPhantom)
Check "D11: -Submit counts the two REAL cases and neither fenced phantom (T0, T1 - not T8, T9)" `
    (($r.code -eq 0) -and ($r.out -match "2 case\(s\) the tester must execute: T0, T1\s")) (First-Line $r.out)
Invoke-Q $f11 @("-Claim", "-Id", "qd11", "-Role", "tester", "-By", "qtester") | Out-Null
$evFencedBacktick = Write-Ev "d11-ev-bt.md" @("## T0 - the first  PASS", "the shape that counts:", '```markdown', "## T1 - the second  PASS", '```')
$r = Invoke-Q $f11 @("-Pass", "-Id", "qd11", "-By", "qtester", "-Evidence", $evFencedBacktick, "-PlanAdequate")
Check "D11: a backtick-fenced '## T1 ... PASS' in the evidence does NOT satisfy T1 (refused: T1 MISSING)" `
    (($r.code -ne 0) -and ($r.out -match "T1: MISSING")) ("exit=" + $r.code)
$evFencedTilde = Write-Ev "d11-ev-tilde.md" @("## T0 - the first  PASS", "~~~", "## T1 - the second  PASS", "~~~")
$r = Invoke-Q $f11 @("-Pass", "-Id", "qd11", "-By", "qtester", "-Evidence", $evFencedTilde, "-PlanAdequate")
Check "D11: a ~~~ fenced '## T1 ... PASS' does not satisfy it either" (($r.code -ne 0) -and ($r.out -match "T1: MISSING")) ("exit=" + $r.code)
$evFencedFail = Write-Ev "d11-ev-fail.md" @("## T0 - the first  PASS", "## T1 - the second  PASS", '```', "## T1 - quoted from a draft  FAIL", '```')
$r = Invoke-Q $f11 @("-Pass", "-Id", "qd11", "-By", "qtester", "-Evidence", $evFencedFail, "-PlanAdequate")
$it = Get-QItem $f11 "qd11"
Check "D11: a fenced FAIL is a quotation, not a verdict - real T0/T1 PASS headings are accepted, 2 rows recorded" `
    (($r.code -eq 0) -and ($it.state -eq "test-passed") -and (@($it.results[0].cases).Count -eq 2)) ("exit=" + $r.code + " rows=" + @($it.results[0].cases).Count)
# The other two doors a plan enters by.
$f11b = New-Fixture "d11b"
$ev = Join-Path $Root "d11b-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Initialize-ToReview $f11b "qd11b" "qdev" $ev
$r = Invoke-Q $f11b @("-Requeue", "-Id", "qd11b", "-By", "qrev", "-Reason", "x", "-TestPlan", $planFencedOnly)
Check "D11: -Requeue -TestPlan with a fenced-only plan is refused, nothing half-applied" `
    (($r.code -ne 0) -and ($r.out -match "no case headings") -and ((Get-QItem $f11b "qd11b").state -eq "reviewing")) ("exit=" + $r.code)
$f11c = New-Fixture "d11c"
Invoke-Q $f11c @("-Propose", "-Id", "qd11c", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f11c @("-ConfirmAnchor", "-Id", "qd11c", "-By", "qoperator") | Out-Null
Invoke-Q $f11c @("-Submit", "-Id", "qd11c", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan3) | Out-Null
Invoke-Q $f11c @("-Claim", "-Id", "qd11c", "-Role", "tester", "-By", "qtester") | Out-Null
Invoke-Q $f11c @("-Fail", "-Id", "qd11c", "-By", "qtester", "-Reason", "T0", "-Evidence", "## T0 - the first  FAIL", "-PlanInadequate") | Out-Null
$r = Invoke-Q $f11c @("-Resubmit", "-Id", "qd11c", "-By", "qdev", "-TestPlan", $planFencedOnly)
$it = Get-QItem $f11c "qd11c"
Check "D11: -Resubmit -TestPlan with a fenced-only plan is refused, nothing half-applied (test-failed, attempt 1)" `
    (($r.code -ne 0) -and ($r.out -match "no case headings") -and ($it.state -eq "test-failed") -and ([int]$it.attempt -eq 1)) ("exit=" + $r.code)

# ======================================================================================
Step "R  the behaviours this change must NOT have altered"
# ======================================================================================
# The regression column. Each of these was true before the six fixes and has to stay true:
# every one of them is a rule some other agent is relying on right now.
$fr = New-Fixture "reg"
Add-Registry $fr @("qdev")
$r = Invoke-Q $fr @("-Submit", "-Id", "qr1", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "R: -Submit with NO anchor at all is refused (exit 5)" ($r.code -eq 5) ("exit=" + $r.code)
Invoke-Q $fr @("-Propose", "-Id", "qr2", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
$r = Invoke-Q $fr @("-Submit", "-Id", "qr2", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1)
Check "R: -Submit before the operator confirms is refused (exit 5)" ($r.code -eq 5) ("exit=" + $r.code)
Invoke-Q $fr @("-ConfirmAnchor", "-Id", "qr2", "-By", "qoperator") | Out-Null
Invoke-Q $fr @("-Submit", "-Id", "qr2", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
$r = Invoke-Q $fr @("-Claim", "-Id", "qr2", "-Role", "tester", "-By", "qdev")
Check "R: the developer cannot TEST their own work (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
$r = Invoke-Q $fr @("-Claim", "-Id", "qr2", "-Role", "reviewer", "-By", "wt-qdev")
Check "R: the prefixed form of the developer's own id is also refused (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
Invoke-Q $fr @("-Claim", "-Id", "qr2", "-Role", "tester", "-By", "qtester") | Out-Null
$ev = Join-Path $Root "reg-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @($case1Pass, "ran case 1.")
Invoke-Q $fr @("-Pass", "-Id", "qr2", "-By", "qtester", "-Evidence", $ev, "-PlanAdequate") | Out-Null
$r = Invoke-Q $fr @("-Approve", "-Id", "qr2", "-By", "qdev")
Check "R: the developer cannot release their OWN work for review (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
Invoke-Q $fr @("-Approve", "-Id", "qr2", "-By", "qoperator") | Out-Null
Invoke-Q $fr @("-Claim", "-Id", "qr2", "-Role", "reviewer", "-By", "qrev") | Out-Null
Push-Location $fr.repo
try {
    Invoke-Git merge --no-ff -q work/qd -m "merge the work (evidence: drill)" | Out-Null
    $regMerge = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
} finally { Pop-Location }
$r = Invoke-Q $fr @("-Merged", "-Id", "qr2", "-By", "qrev", "-Sha", $regMerge)
Check "R: a merge with NO fitness verdict is refused" `
    (($r.code -ne 0) -and ((Get-QItem $fr "qr2").state -ne "merged")) ("exit=" + $r.code)
$r = Invoke-Q $fr @("-Merged", "-Id", "qr2", "-By", "qrev", "-Sha", $regMerge, "-Misfits")
Check "R: -Misfits cannot be merged (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
$r = Invoke-Q $fr @("-Merged", "-Id", "qr2", "-By", "qrev", "-Sha", $regMerge, "-FitsCodebase")
Check "R: an ordinary merge with a live branch ref still records" `
    (($r.code -eq 0) -and ((Get-QItem $fr "qr2").state -eq "merged")) `
    ("exit=" + $r.code + " state=" + (Get-QItem $fr "qr2").state)

$fr2 = New-Fixture "reg2"
Invoke-Q $fr2 @("-Propose", "-Id", "qr3", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $fr2 @("-ConfirmAnchor", "-Id", "qr3", "-By", "qoperator") | Out-Null
Invoke-Q $fr2 @("-Submit", "-Id", "qr3", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
Invoke-Q $fr2 @("-Claim", "-Id", "qr3", "-Role", "tester", "-By", "qtester") | Out-Null
$ev = Join-Path $Root "reg2-evidence.md"
Set-Content -Path $ev -Encoding ascii -Value "case 1 failed: WORK.md says nothing."
Invoke-Q $fr2 @("-Fail", "-Id", "qr3", "-By", "qtester", "-Reason", "case 1", "-Evidence", $ev, "-PlanInadequate") | Out-Null
Check "R: a failing case sends it back to the developer" ((Get-QItem $fr2 "qr3").state -eq "test-failed")
$r = Invoke-Q $fr2 @("-Resubmit", "-Id", "qr3", "-By", "qtester")
Check "R: only the DEVELOPER may -Resubmit (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)
Invoke-Q $fr2 @("-Resubmit", "-Id", "qr3", "-By", "qdev", "-TestPlan", $planV2) | Out-Null
$it = Get-QItem $fr2 "qr3"
Check "R: -Resubmit still bumps the attempt and replaces the plan" `
    (([int]$it.attempt -eq 2) -and ((Get-Content -Raw (Get-QFile $fr2 "qr3.plan.md")) -match "REVISED")) ("attempt=" + $it.attempt)

# --- verdict --------------------------------------------------------------------------
$fail = @($results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0} check(s), {1} failed." -f $results.Count, $fail.Count) -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
foreach ($f in $fail) { Write-Host ("  FAILED: " + $f.check + " " + $f.detail) -ForegroundColor Red }
if (-not $KeepFixtures) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
else { Write-Host ("fixtures kept at " + $Root) -ForegroundColor Yellow }
if ($fail.Count) { exit 1 }
exit 0
