# verify-ob1-items.ps1 - executable proof for OB1-only harness items (harness-ob1, 2026-10-18).
#
#   .\scripts\agent-harness\verify-ob1-items.ps1                    # the shipped queue.ps1 (GREEN)
#   .\scripts\agent-harness\verify-ob1-items.ps1 -AgainstBaseline   # the pre-fix harness (RED)
#   .\scripts\agent-harness\verify-ob1-items.ps1 -Script <path>     # some OTHER copy
#
# THE THREE FAILURES, each from a real item (journal/notes/harness-stall-submodule-sha-2026-10-15.md):
#   S1  intake-i1: all of round 2's change was in OB1 (a379c13 -> e739d0f), the ai-stack sha
#       stayed 79ab80e3, and -Fail logged "no progress: the failure changed but the code did not".
#   S2  the model-tier advisory measured the ai-stack branch only: "lines changed since the last
#       verdict 0" for a ~400-line OB1 change -> a SMALL confirmation retest.
#   S3  intake-i3: the item's tested sha was development's tip (no ai-stack commit), the reviewer
#       merged a gitlink-bump branch --no-ff, and -Merged refused it - the tested sha is the
#       merge's FIRST parent. (intake-i2's lone gitlink-bump commit is refused as before: R3.)
# THE REFUSALS that must stand (R1-R5) and the CONTROL (C1: a non-OB1 item's whole flow is
# byte-identical to the baseline harness, after masking epochs and fixture paths).
#
# HERMETIC, like verify-queue-defects.ps1: every case builds its own scratch repository pair
# (an ai-stack-like repo with an OB1-like nested clone at OB1/, the gitlink written with
# update-index) and its own state dir, under -Root, and points AI_STACK_WORKTREE_STATE at it.
# Nothing here reads or writes the real queue or the real repositories; the only git call
# outside a fixture is `git show <BaselineRef>:<file>` in the repository holding -Script, to
# build the baseline copy.
#
# THE BASELINE is the harness as it was before this item: the directory holding -Script,
# copied, with queue.ps1 and oracle_on_stall.py taken from -BaselineRef (af210613, the
# development tip work/harness-ob1 was cut from - a fixed commit, so the comparison still
# means something after this lands).

[CmdletBinding()]
param(
    [string]$Script = "",
    [string]$Root = "",
    [string]$BaselineRef = "af210613",
    [switch]$AgainstBaseline,
    [switch]$SkipSnapshot,
    [switch]$KeepFixtures
)

$ErrorActionPreference = "Continue"   # native git stderr must never be fatal here

if (-not $Script) { $Script = Join-Path $PSScriptRoot "queue.ps1" }
$Script = (Resolve-Path $Script).Path
$PsExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }
if (-not $Root) { $Root = Join-Path $env:TEMP ("ob1-items-" + $PID) }
if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null

$results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, $ok, [string]$detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) `
        -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git.exe @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed: " + ($out -join "`n")) }
    return @($out)
}

# --- the baseline harness -------------------------------------------------------------
function New-Baseline {
    $src = Split-Path -Parent $Script
    $dst = Join-Path $Root "baseline\scripts\agent-harness"
    New-Item -ItemType Directory -Force -Path $dst | Out-Null
    Get-ChildItem -LiteralPath $src -File | ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $dst -Force }
    $chkSrc = Join-Path $src "..\checks\check-hook-attestation.ps1"
    if (Test-Path $chkSrc) {
        $chkDst = Join-Path $Root "baseline\scripts\checks"
        New-Item -ItemType Directory -Force -Path $chkDst | Out-Null
        Copy-Item -LiteralPath $chkSrc -Destination $chkDst -Force
    }
    foreach ($f in @("queue.ps1", "oracle_on_stall.py")) {
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        try { $text = & git.exe -C $src show ("{0}:scripts/agent-harness/{1}" -f $BaselineRef, $f) 2>$null }
        finally { $ErrorActionPreference = $prev }
        if ($LASTEXITCODE -ne 0) { throw ("cannot read {0} at {1} - pass -SkipSnapshot, or a -BaselineRef this clone holds" -f $f, $BaselineRef) }
        [System.IO.File]::WriteAllText((Join-Path $dst $f), ((@($text) -join "`n") + "`n"), (New-Object System.Text.UTF8Encoding($false)))
    }
    return (Join-Path $dst "queue.ps1")
}

$Baseline = ""
if ($AgainstBaseline -or -not $SkipSnapshot) { $Baseline = New-Baseline }
if ($AgainstBaseline) { $Script = $Baseline }
Write-Host ("queue.ps1 under test : {0}" -f $Script) -ForegroundColor Cyan
Write-Host ("baseline             : {0}" -f $(if ($Baseline) { $Baseline + " (" + $BaselineRef + ")" } else { "(none)" })) -ForegroundColor Cyan
Write-Host ("fixture root         : {0}" -f $Root) -ForegroundColor Cyan
$SupportsOb1Branch = ((Get-Content -Raw -LiteralPath $Script) -match '\[string\]\$Ob1Branch')
if (-not $SupportsOb1Branch) {
    Write-Host "  NOTE: this queue.ps1 has no -Ob1Branch parameter, so the developer cannot name the OB1 branch; it is left out." -ForegroundColor Yellow
}

# --- fixtures -------------------------------------------------------------------------
$script:Tick = 1790000000
function Set-GitClock {
    # Fixed author/committer dates, advanced per commit: two fixtures built by the same steps
    # get the same shas, which is what makes the C1 snapshot comparable byte for byte.
    $script:Tick += 60
    $env:GIT_AUTHOR_DATE = "$($script:Tick) +0000"
    $env:GIT_COMMITTER_DATE = "$($script:Tick) +0000"
}
function Clear-GitClock { Remove-Item Env:GIT_AUTHOR_DATE -ErrorAction SilentlyContinue; Remove-Item Env:GIT_COMMITTER_DATE -ErrorAction SilentlyContinue }

function Add-Lines([string]$path, [int]$n, [string]$tag) {
    $lines = @(); for ($i = 1; $i -le $n; $i++) { $lines += ("export const {0}{1} = {1};" -f $tag, $i) }
    Set-Content -Path $path -Encoding ascii -Value $lines
}

function New-Ob1Fixture([string]$name) {
    # OB1/ (nested clone): main at pinA; work/ob1x at pinB (+10 lines). The ai-stack-like repo:
    # `base` pins pinA; work/qd adds WORK.md and does NOT move the gitlink.
    $script:Tick = 1790000000
    $repo = Join-Path $Root $name
    $ob1 = Join-Path $repo "OB1"
    New-Item -ItemType Directory -Force -Path (Join-Path $ob1 "lib") | Out-Null
    Push-Location $ob1
    try {
        Invoke-Git init -q -b main | Out-Null
        Invoke-Git config user.email "ob1drill@example.invalid" | Out-Null
        Invoke-Git config user.name "ob1 item drill" | Out-Null
        Add-Lines (Join-Path $ob1 "lib\a.ts") 1 "a"
        Invoke-Git add -A | Out-Null
        Set-GitClock; Invoke-Git commit -q -m "OB1 pin A" | Out-Null
        $pinA = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Invoke-Git checkout -q -b work/ob1x | Out-Null
        Add-Lines (Join-Path $ob1 "lib\b.ts") 10 "b"
        Invoke-Git add -A | Out-Null
        Set-GitClock; Invoke-Git commit -q -m "OB1 pin B: +10 lines" | Out-Null
        $pinB = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Invoke-Git checkout -q main | Out-Null
    } finally { Pop-Location }
    Push-Location $repo
    try {
        Invoke-Git init -q -b base | Out-Null
        Invoke-Git config user.email "ob1drill@example.invalid" | Out-Null
        Invoke-Git config user.name "ob1 item drill" | Out-Null
        Set-Content -Path (Join-Path $repo "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git -GitArgs @("update-index", "--add", "--cacheinfo", "160000,$pinA,OB1") | Out-Null
        Set-GitClock; Invoke-Git commit -q -m "scratch base with OB1 at pin A" | Out-Null
        $base0 = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Invoke-Git checkout -q -b work/qd | Out-Null
        Set-Content -Path (Join-Path $repo "WORK.md") -Encoding ascii -Value "the work"
        Invoke-Git add WORK.md | Out-Null
        Set-GitClock; Invoke-Git commit -q -m "the work (ai-stack side)" | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location; Clear-GitClock }
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return @{ repo = $repo; state = $state; name = $name; ob1 = $ob1; pinA = $pinA; pinB = $pinB; base0 = $base0 }
}

function Add-Ob1Commit($fix, [int]$n, [string]$tag) {
    # The developer's next OB1 step on work/ob1x; returns its sha.
    Push-Location $fix.ob1
    try {
        Invoke-Git checkout -q work/ob1x | Out-Null
        Add-Lines (Join-Path $fix.ob1 ("lib\" + $tag + ".ts")) $n $tag
        Invoke-Git add -A | Out-Null
        Set-GitClock; Invoke-Git commit -q -m ("OB1 step " + $tag) | Out-Null
        $s = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Invoke-Git checkout -q main | Out-Null
        return $s
    } finally { Pop-Location; Clear-GitClock }
}

function Set-Gitlink($fix, [string]$branch, [string]$from, [string]$pin, [string]$msg) {
    # A commit on $branch (created from $from when it does not exist) that moves the gitlink to $pin.
    Push-Location $fix.repo
    try {
        $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
        & git.exe rev-parse --verify --quiet "refs/heads/$branch" 2>$null | Out-Null
        $exists = ($LASTEXITCODE -eq 0); $ErrorActionPreference = $prev
        if ($exists) { Invoke-Git checkout -q $branch | Out-Null } else { Invoke-Git checkout -q -b $branch $from | Out-Null }
        Invoke-Git -GitArgs @("update-index", "--cacheinfo", "160000,$pin,OB1") | Out-Null
        Set-GitClock; Invoke-Git commit -q -m $msg | Out-Null
        $s = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        Invoke-Git checkout -q base | Out-Null
        return $s
    } finally { Pop-Location; Clear-GitClock }
}

function Merge-NoFf($fix, [string]$branch) {
    Push-Location $fix.repo
    try {
        Invoke-Git checkout -q base | Out-Null
        Set-GitClock; Invoke-Git merge --no-ff -q $branch -m ("merge " + $branch) | Out-Null
        return (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
    } finally { Pop-Location; Clear-GitClock }
}

function Invoke-Q($fix, [string[]]$QArgs, [string]$With = "") {
    $use = if ($With) { $With } else { $Script }
    $prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
    $env:AI_STACK_WORKTREE_STATE = $fix.state
    $env:AI_STACK_WORK_LINE = "base"
    Push-Location $fix.repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -File $use @QArgs 2>&1 }
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
    return (Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json)
}
function First-Line([string]$s) {
    $l = @($s -split "`n" | Where-Object { $_ -match 'ERROR|PASS|MERGED|round 2|lines changed' })
    if ($l.Count -eq 0) { $l = @($s -split "`n" | Where-Object { $_.Trim() }) }
    if ($l.Count -eq 0) { return "(no output)" }
    return $l[0].Trim()
}

# --- shared inputs -------------------------------------------------------------------
$anchorFile = Join-Path $Root "anchor.json"
Set-Content -Path $anchorFile -Encoding ascii -Value @(
    '{',
    '  "goal": "WORK.md and the OB1 change state what the work was.",',
    '  "artifact": "WORK.md and OB1 lib/ - produced by the drill fixture.",',
    '  "audience": "The next agent to read the files with no other context.",',
    '  "acceptance": [',
    '    "The OB1 change exists on the OB1 branch. Fail: it is absent."',
    '  ],',
    '  "out_of_scope": ["Anything outside the fixture."],',
    '  "findings_sink": "../documentation-plans-ai-stack/journal/notes/ob1-item-drill.md"',
    '}')
$plan = Join-Path $Root "plan.md"
Set-Content -Path $plan -Encoding ascii -Value @("# Test plan", "## Case 1 - the change exists. Fail: it is absent.")
$evPass = Join-Path $Root "evidence-pass.md"
Set-Content -Path $evPass -Encoding ascii -Value @("## Case 1 - the change exists   PASS", "ran case 1.")
$evFail = Join-Path $Root "evidence-fail.md"
Set-Content -Path $evFail -Encoding ascii -Value @("## Case 1 - the change exists   FAIL", "ran case 1.")

function Get-SubmitArgs([string]$id, [string]$branch, [string]$ob1Branch) {
    $a = @("-Submit", "-Id", $id, "-Branch", $branch, "-Developer", "qdev", "-TestPlan", $plan)
    if ($ob1Branch -and $SupportsOb1Branch) { $a += @("-Ob1Branch", $ob1Branch) }
    return $a
}
function Initialize-ToReview($fix, [string]$id, [string]$branch, [string]$ob1Branch) {
    Invoke-Q $fix @("-Propose", "-Id", $id, "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
    Invoke-Q $fix @("-ConfirmAnchor", "-Id", $id, "-By", "qoperator") | Out-Null
    $s = Invoke-Q $fix (Get-SubmitArgs $id $branch $ob1Branch)
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "tester", "-By", "qtester") | Out-Null
    $p = Invoke-Q $fix @("-Pass", "-Id", $id, "-By", "qtester", "-Evidence", $evPass, "-PlanAdequate")
    Invoke-Q $fix @("-Approve", "-Id", $id, "-By", "qoperator") | Out-Null
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "reviewer", "-By", "qrev") | Out-Null
    return @{ submit = $s; pass = $p }
}
function Get-Merged($fix, [string]$id, [string]$sha) {
    return (Invoke-Q $fix @("-Merged", "-Id", $id, "-By", "qrev", "-Sha", $sha, "-FitsCodebase"))
}

# ======================================================================================
Step "S1+S2  intake-i1/i2: the work moves only in OB1 - stall detector and tier advisory"
# ======================================================================================
$f1 = New-Ob1Fixture "s1"
Invoke-Q $f1 @("-Propose", "-Id", "qs1", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f1 @("-ConfirmAnchor", "-Id", "qs1", "-By", "qoperator") | Out-Null
$r = Invoke-Q $f1 (Get-SubmitArgs "qs1" "work/qd" "work/ob1x")
Check "setup: the OB1 item is submitted (exit 0)" ($r.code -eq 0) ("exit=" + $r.code + " | " + (First-Line $r.out))
$it = Get-QItem $f1 "qs1"
Check "S1: the submission records the OB1 gitlink of the submitted ai-stack commit and the named OB1 branch tip" `
    (($it.ob1_gitlink -eq $f1.pinA) -and ($it.ob1_branch -eq "work/ob1x") -and ($it.ob1_branch_sha -eq $f1.pinB)) `
    ("ob1_gitlink=" + $it.ob1_gitlink + " ob1_branch=" + $it.ob1_branch + " ob1_branch_sha=" + $it.ob1_branch_sha)
Invoke-Q $f1 @("-Claim", "-Id", "qs1", "-Role", "tester", "-By", "qtester") | Out-Null
$r = Invoke-Q $f1 @("-Fail", "-Id", "qs1", "-By", "qtester", "-Evidence", $evFail, "-PlanAdequate", "-Reason", "case 1: the guard is missing")
Check "setup: round 1 fails (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)
# The developer's round-2 step lives ENTIRELY in OB1 (+20 lines); the ai-stack branch is untouched.
$pinC = Add-Ob1Commit $f1 20 "c"
$r = Invoke-Q $f1 @("-Resubmit", "-Id", "qs1", "-By", "qdev")
$m = [regex]::Match($r.out, "lines changed since the last verdict (\S+)")
$delta = if ($m.Success) { $m.Groups[1].Value } else { "(not printed)" }
Check "S2: the tier advisory counts the OB1 diff - 20 lines changed since the last verdict, not 0" ($delta -eq "20") ("printed: " + $delta)
Invoke-Q $f1 @("-Claim", "-Id", "qs1", "-Role", "tester", "-By", "qtester") | Out-Null
$r = Invoke-Q $f1 @("-Fail", "-Id", "qs1", "-By", "qtester", "-Evidence", $evFail, "-PlanAdequate", "-Reason", "case 1: the exit code is wrong")
$r2 = @($r.out -split "`n" | Where-Object { $_ -match "round 2" }) | Select-Object -First 1
Check "S1: round 2 (OB1 moved, ai-stack did not) is PROGRESS - not 'the code did not'" `
    (($r2 -match "PROGRESS: new failure on new code") -and ($r2 -notmatch "the code did not")) ("line: " + $r2)
$it = Get-QItem $f1 "qs1"
$rows = @($it.results)
Check "S1: each verdict row records the OB1 commit it tested (pin B, then pin C)" `
    (($rows.Count -eq 2) -and ($rows[0].ob1_sha -eq $f1.pinB) -and ($rows[1].ob1_sha -eq $pinC)) `
    ("rows=" + $rows.Count + " ob1=" + (@($rows | ForEach-Object { "$($_.ob1_sha)".Substring(0, [Math]::Min(8, "$($_.ob1_sha)".Length)) }) -join ","))
# THE CONTROL: round 3 moves NOTHING - neither repository. Still noise.
$r = Invoke-Q $f1 @("-Resubmit", "-Id", "qs1", "-By", "qdev")
$m = [regex]::Match($r.out, "lines changed since the last verdict (\S+)")
$delta = if ($m.Success) { $m.Groups[1].Value } else { "(not printed)" }
Check "S2 control: nothing moved in either repository -> 0 lines changed" ($delta -eq "0") ("printed: " + $delta)
Invoke-Q $f1 @("-Claim", "-Id", "qs1", "-Role", "tester", "-By", "qtester") | Out-Null
$r = Invoke-Q $f1 @("-Fail", "-Id", "qs1", "-By", "qtester", "-Evidence", $evFail, "-PlanAdequate", "-Reason", "case 1: a third, different symptom")
$r3 = @($r.out -split "`n" | Where-Object { $_ -match "round 3" }) | Select-Object -First 1
Check "S1 control: round 3 (neither moved) is still 'no progress ... the code did not'" `
    ($r3 -match "no progress: the failure changed but the code did not") ("line: " + $r3)

# S2b - the OB1 item whose rounds bump the gitlink (the documented flow): the gitlink row is
# MEASURED as the OB1 diff, where the baseline calls any gitlink change unknown.
$f2 = New-Ob1Fixture "s2b"
[void](Set-Gitlink $f2 "work/qd" "" $f2.pinB "bump OB1 to pin B")
Invoke-Q $f2 @("-Propose", "-Id", "qs2", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q $f2 @("-ConfirmAnchor", "-Id", "qs2", "-By", "qoperator") | Out-Null
Invoke-Q $f2 (Get-SubmitArgs "qs2" "work/qd" "") | Out-Null
$it = Get-QItem $f2 "qs2"
Check "S2b: a branch that moves the gitlink is an OB1 item without naming a branch (ob1_gitlink = pin B)" `
    ($it.ob1_gitlink -eq $f2.pinB) ("ob1_gitlink=" + $it.ob1_gitlink)
Invoke-Q $f2 @("-Claim", "-Id", "qs2", "-Role", "tester", "-By", "qtester") | Out-Null
Invoke-Q $f2 @("-Fail", "-Id", "qs2", "-By", "qtester", "-Evidence", $evFail, "-PlanAdequate", "-Reason", "case 1: the guard is missing") | Out-Null
$pinC2 = Add-Ob1Commit $f2 7 "c"
[void](Set-Gitlink $f2 "work/qd" "" $pinC2 "bump OB1 to pin C")
$r = Invoke-Q $f2 @("-Resubmit", "-Id", "qs2", "-By", "qdev")
$m = [regex]::Match($r.out, "lines changed since the last verdict (\S+)")
$delta = if ($m.Success) { $m.Groups[1].Value } else { "(not printed)" }
Check "S2b: a gitlink bump B -> C on an OB1 item measures the OB1 diff (7 lines), not 'unknown'" ($delta -eq "7") ("printed: " + $delta)

# ======================================================================================
Step "S3  intake-i3: tested sha = the line's tip, reviewer merges a gitlink-bump branch --no-ff"
# ======================================================================================
$f3 = New-Ob1Fixture "s3"
# The item's ai-stack branch carries NO commit of its own: it IS the line's tip.
Push-Location $f3.repo; try { Invoke-Git branch work/qd3 base | Out-Null } finally { Pop-Location }
$i3 = Initialize-ToReview $f3 "qs3" "work/qd3" "work/ob1x"
$it = Get-QItem $f3 "qs3"
Check "setup: the item reached review with tested_at_sha = the line's tip" (($it.state -eq "reviewing") -and ($it.tested_at_sha -eq $f3.base0)) ("state=" + $it.state)
[void](Set-Gitlink $f3 "work/qs3-land" "base" $f3.pinB "land qs3: OB1 to pin B")
$mg3 = Merge-NoFf $f3 "work/qs3-land"
$r = Get-Merged $f3 "qs3" $mg3
$it = Get-QItem $f3 "qs3"
Check "S3: -Merged ACCEPTS the --no-ff merge whose gitlink is the tested OB1 commit (intake-i3)" `
    (($r.code -eq 0) -and ($it.state -eq "merged") -and ($it.merged_sha -eq $mg3)) ("exit=" + $r.code + " | " + (First-Line $r.out))

# ======================================================================================
Step "S4  the documented flow: the developer commits the gitlink bump on work/<id>"
# ======================================================================================
$f4 = New-Ob1Fixture "s4"
[void](Set-Gitlink $f4 "work/qd" "" $f4.pinB "bump OB1 to pin B (the developer, after pushing OB1)")
$i4 = Initialize-ToReview $f4 "qs4" "work/qd" "work/ob1x"
$it = Get-QItem $f4 "qs4"
Check "S4: -Pass records tested_ob1_sha = the OB1 commit the tested ai-stack commit pins" ($it.tested_ob1_sha -eq $f4.pinB) ("tested_ob1_sha=" + $it.tested_ob1_sha)
$mg4 = Merge-NoFf $f4 "work/qd"
$r = Get-Merged $f4 "qs4" $mg4
Check "S4: -Merged accepts the ordinary --no-ff merge of the bumped work branch" `
    (($r.code -eq 0) -and ((Get-QItem $f4 "qs4").state -eq "merged")) ("exit=" + $r.code + " | " + (First-Line $r.out))

# ======================================================================================
Step "R1-R5  -Merged still refuses what is not a merge of the tested code"
# ======================================================================================
function Test-Refused($fix, [string]$id, [string]$sha, [string]$label, [string]$expect) {
    $r = Get-Merged $fix $id $sha
    $it = Get-QItem $fix $id
    Check $label (($r.code -eq 1) -and ($it.state -eq "reviewing") -and ($r.out -match $expect)) ("exit=" + $r.code + " state=" + $it.state + " | " + (First-Line $r.out))
}
# R1 - wrong OB1 gitlink: the merge contains the tested ai-stack commit but pins OB1 C, not the tested B.
$fr1 = New-Ob1Fixture "r1"
[void](Set-Gitlink $fr1 "work/qd" "" $fr1.pinB "bump OB1 to pin B")
[void](Initialize-ToReview $fr1 "qr1" "work/qd" "work/ob1x")
$pinCr1 = Add-Ob1Commit $fr1 3 "c"
[void](Set-Gitlink $fr1 "work/qr1-land" "work/qd" $pinCr1 "land qr1 with OB1 at pin C (untested)")
$mgr1 = Merge-NoFf $fr1 "work/qr1-land"
Test-Refused $fr1 "qr1" $mgr1 "R1: a merge whose OB1 gitlink is NOT the tested OB1 commit is refused" "pins OB1"
# R2 - tested ai-stack sha not an ancestor: the gitlink is right, but the merged branch does not contain the tested commit.
$fr2 = New-Ob1Fixture "r2"
[void](Set-Gitlink $fr2 "work/qd" "" $fr2.pinB "bump OB1 to pin B")
[void](Initialize-ToReview $fr2 "qr2" "work/qd" "work/ob1x")
[void](Set-Gitlink $fr2 "work/qr2-other" "base" $fr2.pinB "a different branch, same pin")
$mgr2 = Merge-NoFf $fr2 "work/qr2-other"
Test-Refused $fr2 "qr2" $mgr2 "R2: a merge whose non-first parents do not contain the tested ai-stack commit is refused" "not behind any of its"
# R3 - intake-i2: a lone gitlink-bump commit straight on the line (no merge commit).
$fr3 = New-Ob1Fixture "r3"
Push-Location $fr3.repo; try { Invoke-Git branch work/qd3 base | Out-Null } finally { Pop-Location }
[void](Initialize-ToReview $fr3 "qr3" "work/qd3" "work/ob1x")
Push-Location $fr3.repo
try {
    Invoke-Git -GitArgs @("update-index", "--cacheinfo", "160000,$($fr3.pinB),OB1") | Out-Null
    Set-GitClock; Invoke-Git commit -q -m "lone gitlink bump on the line" | Out-Null
    $bump3 = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
} finally { Pop-Location; Clear-GitClock }
Test-Refused $fr3 "qr3" $bump3 "R3: a lone gitlink-bump commit (intake-i2, fast-forward shape) is refused - not a merge commit" "is not a merge commit"
# R4 - cherry-pick of the tested commit onto the line.
$fr4 = New-Ob1Fixture "r4"
[void](Set-Gitlink $fr4 "work/qd" "" $fr4.pinB "bump OB1 to pin B")
[void](Initialize-ToReview $fr4 "qr4" "work/qd" "work/ob1x")
Push-Location $fr4.repo
try {
    Set-GitClock; Invoke-Git cherry-pick work/qd | Out-Null
    $cp4 = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
} finally { Pop-Location; Clear-GitClock }
Test-Refused $fr4 "qr4" $cp4 "R4: a cherry-pick of the tested commit onto the line is refused - not a merge commit" "is not a merge commit"
# R5 - the intake-i3 allowance does NOT open when the first parent already pins the tested OB1
# (the OB1 work reached the line some other way; this merge introduces nothing of the item's).
$fr5 = New-Ob1Fixture "r5"
Push-Location $fr5.repo; try { Invoke-Git branch work/qd5 base | Out-Null } finally { Pop-Location }
[void](Initialize-ToReview $fr5 "qr5" "work/qd5" "work/ob1x")
Push-Location $fr5.repo
try {
    Invoke-Git checkout -q -b work/qr5-doc base | Out-Null
    Set-Content -Path (Join-Path $fr5.repo "DOC.md") -Encoding ascii -Value "unrelated"
    Invoke-Git add DOC.md | Out-Null
    Set-GitClock; Invoke-Git commit -q -m "an unrelated doc" | Out-Null
    Invoke-Git checkout -q base | Out-Null
    Invoke-Git -GitArgs @("update-index", "--cacheinfo", "160000,$($fr5.pinB),OB1") | Out-Null
    Set-GitClock; Invoke-Git commit -q -m "OB1 to pin B some other way" | Out-Null
} finally { Pop-Location; Clear-GitClock }
$mgr5 = Merge-NoFf $fr5 "work/qr5-doc"
Test-Refused $fr5 "qr5" $mgr5 "R5: the first parent already pins the tested OB1 -> 'already behind its FIRST parent' still refuses" "already behind its FIRST parent"

# ======================================================================================
Step "C1  a non-OB1 item: the whole flow is byte-identical to the baseline harness"
# ======================================================================================
if (-not $Baseline -or $AgainstBaseline) {
    Write-Host "  SKIPPED: no baseline to compare with (-SkipSnapshot, or this run IS the baseline)." -ForegroundColor Yellow
} else {
    function Invoke-Flow($fix, [string]$with) {
        # Two non-OB1 laps. qc1: propose, confirm, submit, fail, a fix, resubmit, pass, approve,
        # review, show (its -Merged would need a learning record - out of this comparison).
        # qc2: a first-try green on its own branch through -Merged. Then -List. Returns the
        # transcript and both item files, masked.
        $log = @()
        $steps = @(
            @("-Propose", "-Id", "qc1", "-Anchor", $anchorFile, "-Developer", "qdev"),
            @("-ConfirmAnchor", "-Id", "qc1", "-By", "qoperator"),
            @("-Submit", "-Id", "qc1", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan),
            @("-Claim", "-Id", "qc1", "-Role", "tester", "-By", "qtester"),
            @("-Fail", "-Id", "qc1", "-By", "qtester", "-Evidence", $evFail, "-PlanAdequate", "-Reason", "case 1: the guard is missing"),
            "FIX",
            @("-Resubmit", "-Id", "qc1", "-By", "qdev"),
            @("-Claim", "-Id", "qc1", "-Role", "tester", "-By", "qtester"),
            @("-Pass", "-Id", "qc1", "-By", "qtester", "-Evidence", $evPass, "-PlanAdequate"),
            @("-Approve", "-Id", "qc1", "-By", "qoperator"),
            @("-Claim", "-Id", "qc1", "-Role", "reviewer", "-By", "qrev"),
            @("-Show", "-Id", "qc1"),
            "BRANCH2",
            @("-Propose", "-Id", "qc2", "-Anchor", $anchorFile, "-Developer", "qdev"),
            @("-ConfirmAnchor", "-Id", "qc2", "-By", "qoperator"),
            @("-Submit", "-Id", "qc2", "-Branch", "work/qd2", "-Developer", "qdev", "-TestPlan", $plan),
            @("-Claim", "-Id", "qc2", "-Role", "tester", "-By", "qtester"),
            @("-Pass", "-Id", "qc2", "-By", "qtester", "-Evidence", $evPass, "-PlanAdequate"),
            @("-Approve", "-Id", "qc2", "-By", "qoperator"),
            @("-Claim", "-Id", "qc2", "-Role", "reviewer", "-By", "qrev"),
            "MERGE",
            @("-Show", "-Id", "qc2"),
            @("-List")
        )
        foreach ($s in $steps) {
            if ($s -eq "FIX") {
                Push-Location $fix.repo
                try {
                    Invoke-Git checkout -q work/qd | Out-Null
                    Set-Content -Path (Join-Path $fix.repo "WORK.md") -Encoding ascii -Value "the work, fixed"
                    Invoke-Git add WORK.md | Out-Null
                    Set-GitClock; Invoke-Git commit -q -m "fix" | Out-Null
                    Invoke-Git checkout -q base | Out-Null
                } finally { Pop-Location; Clear-GitClock }
                continue
            }
            if ($s -eq "BRANCH2") {
                Push-Location $fix.repo
                try {
                    Invoke-Git checkout -q -b work/qd2 base | Out-Null
                    Set-Content -Path (Join-Path $fix.repo "WORK2.md") -Encoding ascii -Value "the second work"
                    Invoke-Git add WORK2.md | Out-Null
                    Set-GitClock; Invoke-Git commit -q -m "the second work" | Out-Null
                    Invoke-Git checkout -q base | Out-Null
                } finally { Pop-Location; Clear-GitClock }
                continue
            }
            if ($s -eq "MERGE") {
                $mg = Merge-NoFf $fix "work/qd2"
                $r = Invoke-Q $fix @("-Merged", "-Id", "qc2", "-By", "qrev", "-Sha", $mg, "-FitsCodebase") $with
                $log += ("### -Merged exit " + $r.code); $log += $r.out
                continue
            }
            $r = Invoke-Q $fix $s $with
            $log += ("### " + $s[0] + " exit " + $r.code); $log += $r.out
        }
        $itemText = (Get-Content -Raw -Path (Join-Path $fix.state "queue\qc1.json") -Encoding UTF8) + "`n### ITEM qc2`n" +
                    (Get-Content -Raw -Path (Join-Path $fix.state "queue\qc2.json") -Encoding UTF8)
        $all = ($log -join "`n") + "`n### ITEM qc1`n" + $itemText
        $all = $all.Replace($fix.repo, "<REPO>").Replace($fix.state, "<STATE>").Replace(($fix.state -replace '\\', '\\'), "<STATE>").Replace(($fix.repo -replace '\\', '\\'), "<REPO>")
        $all = $all.Replace($Root, "<ROOT>").Replace(($Root -replace '\\', '\\'), "<ROOT>")
        $all = [regex]::Replace($all, '\b1[789]\d{8}\b', '<EPOCH>')
        $all = [regex]::Replace($all, '\(\d+m ago\)', '(<N>m ago)')
        return $all
    }
    $cA = New-Ob1Fixture "c1-baseline"
    $tA = Invoke-Flow $cA $Baseline
    $cB = New-Ob1Fixture "c1-current"
    $tB = Invoke-Flow $cB $Script
    Set-Content -Path (Join-Path $Root "c1-baseline.txt") -Encoding UTF8 -Value $tA
    Set-Content -Path (Join-Path $Root "c1-current.txt") -Encoding UTF8 -Value $tB
    Check "C1 setup: the two fixtures are the same commits (deterministic shas)" `
        ((Invoke-Git -C $cA.repo rev-parse work/qd | Select-Object -First 1) -eq (Invoke-Git -C $cB.repo rev-parse work/qd | Select-Object -First 1))
    Check "C1 setup: the baseline's second lap reached 'merged' and the first carries a fail and a pass" `
        (($tA -match '### -Merged exit 0') -and ($tA -match '"verdict":\s+"fail"') -and ($tA -match '"verdict":\s+"pass"')) ""
    $diffAt = -1
    $la = @($tA -split "`n"); $lb = @($tB -split "`n")
    for ($k = 0; $k -lt [Math]::Max($la.Count, $lb.Count); $k++) {
        $x = if ($k -lt $la.Count) { $la[$k] } else { "<EOF>" }
        $y = if ($k -lt $lb.Count) { $lb[$k] } else { "<EOF>" }
        if ($x -ne $y) { $diffAt = $k; break }
    }
    $detail = if ($diffAt -lt 0) { ("{0} lines identical" -f $la.Count) } else { ("first difference at line {0}: baseline '{1}' vs current '{2}'" -f ($diffAt + 1), $la[$diffAt], $(if ($diffAt -lt $lb.Count) { $lb[$diffAt] } else { "<EOF>" })) }
    Check "C1: a non-OB1 item's transcript and item file are byte-identical to the baseline's (epochs and paths masked)" ($tA -ceq $tB) $detail
    $noOb1 = $true
    foreach ($cid in @("qc1", "qc2")) {
        $itB = Get-QItem $cB $cid
        if (@($itB.PSObject.Properties.Name) | Where-Object { $_ -like "*ob1*" }) { $noOb1 = $false }
        if (@($itB.results | ForEach-Object { $_.PSObject.Properties.Name }) | Where-Object { $_ -like "*ob1*" }) { $noOb1 = $false }
    }
    Check "C1: the non-OB1 items carry no OB1 field at all" $noOb1 ""
}

# --- verdict --------------------------------------------------------------------------
$fail = @($results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0} check(s), {1} failed." -f $results.Count, $fail.Count) -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
foreach ($f in $fail) { Write-Host ("  FAILED: " + $f.check + " " + $f.detail) -ForegroundColor Red }
if (-not $KeepFixtures) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
else { Write-Host ("fixtures kept at " + $Root) -ForegroundColor Yellow }
if ($fail.Count) { exit 1 }
exit 0
