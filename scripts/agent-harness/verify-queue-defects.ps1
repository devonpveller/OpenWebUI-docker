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
    $p = Join-Path $fix.state "queue\$id.json"
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Raw -Path $p | ConvertFrom-Json)
}
function Get-QFile($fix, [string]$leaf) { return (Join-Path $fix.state ("queue\" + $leaf)) }
function Get-QRaw($fix, [string]$id) { return (Get-Content -Raw -Path (Join-Path $fix.state "queue\$id.json")) }
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
$planV1 = Join-Path $Root "plan-v1.md"
Set-Content -Path $planV1 -Encoding ascii -Value @(
    "# Test plan, attempt 1",
    "Case 1: WORK.md exists. Fail: it is absent.")
$planV2 = Join-Path $Root "plan-v2.md"
Set-Content -Path $planV2 -Encoding ascii -Value @(
    "# Test plan, REVISED after the return to test",
    "Case 2 (new): the case the first plan was missing.")

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
Set-Content -Path $ev1 -Encoding ascii -Value @("# attempt 1 evidence", "ran case 1: WORK.md present.")
Initialize-ToReview $f1 "qd1" "qdev" $ev1
$att1Path = Get-QFile $f1 "qd1.attempt1.evidence.md"
Check "setup: attempt 1's evidence is beside the item" (Test-Path $att1Path) $att1Path
$att1Before = Get-Sha256 $att1Path

$r = Invoke-Q $f1 @("-Requeue", "-Id", "qd1", "-By", "qrev", "-Reason", "rebase moved the tested content")
Check "the reviewer's -Requeue still succeeds" ($r.code -eq 0) ("exit=" + $r.code)
$it = Get-QItem $f1 "qd1"
Check "D1: -Requeue BUMPED attempt to 2" ([int]$it.attempt -eq 2) ("attempt=" + $it.attempt)

$ev2 = Join-Path $Root "d1-evidence-attempt2.md"
Set-Content -Path $ev2 -Encoding ascii -Value @("# attempt 2 evidence", "re-ran case 1 on the adapted content.")
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
Set-Content -Path $ev -Encoding ascii -Value "ran case 1."
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
Set-Content -Path $ev -Encoding ascii -Value "ran case 1."
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
Set-Content -Path $dest -Encoding ascii -Value @("# evidence written straight to the queue", "case 1 green.")
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
Set-Content -Path $ev -Encoding ascii -Value "ran case 1."
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
Set-Content -Path $ev -Encoding ascii -Value "ran case 1."
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
Set-Content -Path $ev -Encoding ascii -Value "ran case 1."
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
