# drill-dark-factory.ps1 - U6's validation column, executable.
#
# THE COLUMN (PLAN.md section 2, U6): "an unattended run that hits each andon condition
# halts-and-raises; one that hits none lands with a complete audit trail." Both halves are
# here, and the second half is the one that gets skipped - so "complete" is DEFINED
# (gate-audit.ps1 Test-GateAuditComplete) and checked, not asserted.
#
# WHAT IS REAL AND WHAT IS NOT, stated up front because a local run described as a gym run
# is the exact over-claim PLAN section C.7 exists to prevent:
#   REAL - real git repositories, the real andon.ps1, the real queue.ps1, the real
#          gate-audit.ps1 and the real harness config loader. Every state transition below
#          is produced by the shipped tools, not simulated.
#   NOT  - this is not a run in `ai-orchestration-gym`. The gym drives the agent-org bridge
#          against GitHub with a real App installation; U6's mechanism is the HARNESS
#          pipeline, which has no gym scenario, and a gym run would additionally require
#          remote mutations this session is not granted. The "unattended run" here is the
#          pipeline driven end to end with no human at either gate - which is precisely
#          what `dark` means - not a multi-agent arena scenario.
#   NOT  - the remote in the push test is a BARE REPOSITORY ON DISK. Nothing leaves the
#          machine. It is a real git remote for the purposes of the detector, which asks
#          about refs/remotes/*, and that is the property under test.
#
# ISOLATION. Everything runs in scratch repositories under $env:TEMP with
# AI_STACK_WORKTREE_STATE and AI_STACK_HARNESS_CONFIG redirected. This drill NEVER touches
# the operator's checkout, the real queue, or the real ledger - which is not a stylistic
# preference: the 2026-08-30 incident is a drill that rebased the live work line.
#
# PROVE RED BEFORE GREEN. Every condition is shown FIRING on a constructed instance and NOT
# firing on a clean one. A detector that always fires is as useless as one that never does,
# so both directions are asserted for all five.
#
#   .\drill-dark-factory.ps1            # run it
#   .\drill-dark-factory.ps1 -Keep      # keep the scratch dirs for inspection
#
# Exit: 0 every check passed | 1 one or more failed

[CmdletBinding()]
param([switch]$Keep)

$ErrorActionPreference = "Stop"
$HarnessDir = $PSScriptRoot
$AndonPs = Join-Path $HarnessDir "andon.ps1"
$QueuePs = Join-Path $HarnessDir "queue.ps1"
$ShippedCfg = Join-Path $HarnessDir "harness.config.json"
$PsExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }

$script:Results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, [bool]$ok, [string]$detail = "") {
    $script:Results += [pscustomobject]@{ label = $label; pass = $ok; detail = $detail }
    $tag = "FAIL"; $colour = "Red"
    if ($ok) { $tag = "PASS"; $colour = "Green" }
    Write-Host ("  [{0}] {1} {2}" -f $tag, $label, $detail) -ForegroundColor $colour
}

function Invoke-GitAt([string]$repo, [string[]]$GitArgs) {
    # `,@(...)` so a single output line comes back as a one-element ARRAY. PowerShell unrolls
    # a returned array, and `(...)[0]` on the resulting string yields a [char] - which is how
    # the first run of this drill died.
    Push-Location $repo
    try { return , @(Invoke-Git @GitArgs) } finally { Pop-Location }
}

function Invoke-Git {
    # Deliberately NOT the drill helper this repo already had. `Invoke-DrillGit` swallowed
    # every git error - no exit-code check, no stderr - and is one of the two proven
    # contributing defects in the incident that produced this drill's first condition. A
    # drill whose git helper cannot fail cannot prove anything.
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git.exe @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed (" + $LASTEXITCODE + "): " + ($out -join "; ")) }
    return @($out)
}

$Root = Join-Path $env:TEMP ("dfdrill-" + $PID)
if (Test-Path $Root) { Remove-Item $Root -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$CfgDir = Join-Path $Root "cfg"; New-Item -ItemType Directory -Force -Path $CfgDir | Out-Null

function New-ScratchRepo([string]$name) {
    $p = Join-Path $Root $name
    New-Item -ItemType Directory -Force -Path $p | Out-Null
    Push-Location $p
    try {
        Invoke-Git init -q -b main | Out-Null
        Invoke-Git config user.email "drill@example.invalid" | Out-Null
        Invoke-Git config user.name "dark factory drill" | Out-Null
        Set-Content -Path (Join-Path $p "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git commit -q -m "scratch base" | Out-Null
        Invoke-Git branch dev | Out-Null
    } finally { Pop-Location }
    return $p
}

function New-DrillConfig([string]$name, [scriptblock]$edit) {
    $o = Get-Content -Raw -Path $ShippedCfg | ConvertFrom-Json
    & $edit $o
    $p = Join-Path $CfgDir "$name.json"
    ($o | ConvertTo-Json -Depth 40) | Set-Content -Path $p -Encoding ASCII
    return $p
}
function Get-Cond($cfg, [string]$id) { return ($cfg.andon.conditions | Where-Object { $_.id -eq $id }) }

function Invoke-Andon {
    param([string]$Config, [string]$Only = "", [string]$Repo = "", [string]$StateDir = "", [string[]]$RunBranch = @())
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
    $env:AI_STACK_HARNESS_CONFIG = $Config
    if ($StateDir) { $env:AI_STACK_WORKTREE_STATE = $StateDir }
    $argv = @("-NoProfile", "-NonInteractive", "-File", $AndonPs, "-Evaluate", "-Json")
    if ($Only) { $argv += @("-Only", $Only) }
    if ($Repo) { $argv += @("-RepoRoot", $Repo) }
    foreach ($b in $RunBranch) { $argv += @("-RunBranch", $b) }
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe @argv 2>$null } finally { $ErrorActionPreference = $prev }
    $code = $LASTEXITCODE
    $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState
    $text = ($out | Where-Object { $_ }) -join ""
    $v = $null
    if ($text) { try { $v = ConvertFrom-Json $text } catch { } }
    return @{ code = $code; verdict = $v }
}

function Get-CondStatus($r, [string]$id) {
    if (-not $r.verdict) { return "(no verdict)" }
    $c = @($r.verdict.conditions | Where-Object { $_.id -eq $id })
    if ($c.Count -eq 0) { return "(not evaluated)" }
    return $c[0].status
}
function Get-CondEvidence($r, [string]$id) {
    if (-not $r.verdict) { return @() }
    $c = @($r.verdict.conditions | Where-Object { $_.id -eq $id })
    if ($c.Count -eq 0) { return @() }
    return @($c[0].evidence)
}

# ====================================================================================
Step "A1  operator-checkout-off-branch: RED on a detached/mid-rebase checkout, GREEN on a clean one"
# ====================================================================================
$repoA = New-ScratchRepo "repo-a1"
$cfgA1 = New-DrillConfig "a1" { param($c) (Get-Cond $c "operator-checkout-off-branch").params.repo = $repoA }

$r = Invoke-Andon -Config $cfgA1 -Only "operator-checkout-off-branch" -Repo $repoA
Check "GREEN: a checkout on a branch does not fire" ((Get-CondStatus $r "operator-checkout-off-branch") -eq "ok") ("status=" + (Get-CondStatus $r "operator-checkout-off-branch"))

Push-Location $repoA; try { Invoke-Git checkout -q --detach HEAD | Out-Null } finally { Pop-Location }
$r = Invoke-Andon -Config $cfgA1 -Only "operator-checkout-off-branch" -Repo $repoA
Check "RED: a DETACHED checkout fires" ((Get-CondStatus $r "operator-checkout-off-branch") -eq "fire") ((Get-CondEvidence $r "operator-checkout-off-branch") -join "; ")
Check "RED: a fired condition raises the board (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)

Push-Location $repoA; try { Invoke-Git checkout -q main | Out-Null } finally { Pop-Location }
# An interrupted rebase leaves its state directory behind - which is exactly how the real
# incident was found, eight minutes after the process died.
$gitDirA = (Invoke-GitAt $repoA @("rev-parse", "--path-format=absolute", "--git-dir"))[0].Trim()
New-Item -ItemType Directory -Force -Path (Join-Path $gitDirA "rebase-merge") | Out-Null
Set-Content -Path (Join-Path $gitDirA "rebase-merge\onto") -Encoding ascii -Value "deadbeef"
$r = Invoke-Andon -Config $cfgA1 -Only "operator-checkout-off-branch" -Repo $repoA
Check "RED: an interrupted REBASE fires even though HEAD is on a branch" ((Get-CondStatus $r "operator-checkout-off-branch") -eq "fire") ((Get-CondEvidence $r "operator-checkout-off-branch") -join "; ")
Remove-Item (Join-Path $gitDirA "rebase-merge") -Recurse -Force

# ====================================================================================
Step "A2  policy-declared-unread: RED on the PRE-U6 config, GREEN on the shipped one"
# ====================================================================================
$repoA2 = New-ScratchRepo "repo-a2"
# The shipped config, as it stands after this work.
$cfgA2ok = New-DrillConfig "a2-ok" { param($c) }
$r = Invoke-Andon -Config $cfgA2ok -Only "policy-declared-unread" -Repo $repoA2
Check "GREEN: the shipped pipeline block is fully read" ((Get-CondStatus $r "policy-declared-unread") -eq "ok") ("status=" + (Get-CondStatus $r "policy-declared-unread"))

# The REAL incident, reconstructed: the pipeline block exactly as it was before this work,
# with `human_gates` that no executable line in any language read.
$cfgA2pre = New-DrillConfig "a2-pre-u6" {
    param($c)
    $c.pipeline = [pscustomobject]@{
        claim_ttl_minutes = 60
        anchor_required   = $true
        human_gates       = [pscustomobject]@{ anchor = $true; pre_review = $true }
    }
}
$r = Invoke-Andon -Config $cfgA2pre -Only "policy-declared-unread" -Repo $repoA2
$ev = @(Get-CondEvidence $r "policy-declared-unread")
Check "RED: the pre-U6 `human_gates` declaration fires" ((Get-CondStatus $r "policy-declared-unread") -eq "fire") ($ev -join ", ")
Check "RED: it names pipeline.human_gates specifically" ($ev -contains "pipeline.human_gates") ($ev -join ", ")

# ====================================================================================
Step "A3  git-error-swallowed: RED on a swallowing function, GREEN on a checking one"
# ====================================================================================
$repoA3 = New-ScratchRepo "repo-a3"
$fixDir = Join-Path $repoA3 "fixtures"; New-Item -ItemType Directory -Force -Path $fixDir | Out-Null
Set-Content -Path (Join-Path $fixDir "clean.ps1") -Encoding ascii -Value @(
    'function Get-CleanBranch {',
    '    $out = & git.exe rev-parse --abbrev-ref HEAD',
    '    if ($LASTEXITCODE -ne 0) { throw "git failed" }',
    '    return $out',
    '}')
$cfgA3 = New-DrillConfig "a3" { param($c) (Get-Cond $c "git-error-swallowed").params.globs = @("fixtures/*.ps1") }
$r = Invoke-Andon -Config $cfgA3 -Only "git-error-swallowed" -Repo $repoA3
Check "GREEN: a function that checks `$LASTEXITCODE does not fire" ((Get-CondStatus $r "git-error-swallowed") -eq "ok") ("status=" + (Get-CondStatus $r "git-error-swallowed"))

# The incident's own shape, verbatim in structure: preference flipped, output discarded,
# exit code never consulted.
Set-Content -Path (Join-Path $fixDir "swallow.ps1") -Encoding ascii -Value @(
    'function Invoke-SwallowingGit {',
    '    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"',
    '    try { & git.exe @args | Out-Null } finally { $ErrorActionPreference = $prev }',
    '}')
$r = Invoke-Andon -Config $cfgA3 -Only "git-error-swallowed" -Repo $repoA3
$ev = @(Get-CondEvidence $r "git-error-swallowed")
Check "RED: a function that discards git's exit code fires" ((Get-CondStatus $r "git-error-swallowed") -eq "fire") ($ev -join "; ")
Check "RED: it names the offending function, not just the file" (($ev -join ";") -like "*Invoke-SwallowingGit*") ($ev -join "; ")

# A comment mentioning git must NOT fire it. The first version of this predicate reported
# four functions and every one was the word "git" inside a comment.
Remove-Item (Join-Path $fixDir "swallow.ps1") -Force
Set-Content -Path (Join-Path $fixDir "commented.ps1") -Encoding ascii -Value @(
    'function Get-Nothing {',
    '    # Thin policy wrapper over the git fact - mentions git, runs none.',
    '    return "no git here"',
    '}')
$r = Invoke-Andon -Config $cfgA3 -Only "git-error-swallowed" -Repo $repoA3
Check "GREEN: the word 'git' in a COMMENT or STRING does not fire it" ((Get-CondStatus $r "git-error-swallowed") -eq "ok") ((Get-CondEvidence $r "git-error-swallowed") -join "; ")

# And against the REAL repository it must name the function from the real incident.
$repoReal = (Resolve-Path (Join-Path $HarnessDir "..\..")).Path
$cfgA3real = New-DrillConfig "a3-real" { param($c) }
$r = Invoke-Andon -Config $cfgA3real -Only "git-error-swallowed" -Repo $repoReal
$ev = @(Get-CondEvidence $r "git-error-swallowed")
Check "RED on the REAL repo: it names Invoke-DrillGit, the incident's own function" (($ev -join ";") -like "*Invoke-DrillGit*") ($ev -join "; ")

# ====================================================================================
Step "A4  work-branch-on-remote: RED once a work branch is pushed, GREEN before"
# ====================================================================================
$repoA4 = New-ScratchRepo "repo-a4"
Push-Location $repoA4
try {
    Invoke-Git checkout -q -b work/dfdrill | Out-Null
    Set-Content -Path (Join-Path $repoA4 "w.txt") -Encoding ascii -Value "work"
    Invoke-Git add w.txt | Out-Null
    Invoke-Git commit -q -m "work" | Out-Null
    Invoke-Git checkout -q main | Out-Null
} finally { Pop-Location }
$cfgA4 = New-DrillConfig "a4" { param($c) }
$r = Invoke-Andon -Config $cfgA4 -Only "work-branch-on-remote" -Repo $repoA4 -RunBranch @("work/dfdrill")
Check "GREEN: an unpushed work branch does not fire" ((Get-CondStatus $r "work-branch-on-remote") -eq "ok") ("status=" + (Get-CondStatus $r "work-branch-on-remote"))

# A BARE REPOSITORY ON DISK. Nothing leaves the machine; the detector's question is about
# refs/remotes/*, and this produces a real one.
$bare = Join-Path $Root "origin-a4.git"
Invoke-Git init -q --bare $bare | Out-Null
Push-Location $repoA4
try {
    Invoke-Git remote add origin $bare | Out-Null
    Invoke-Git push -q origin work/dfdrill | Out-Null
} finally { Pop-Location }
$r = Invoke-Andon -Config $cfgA4 -Only "work-branch-on-remote" -Repo $repoA4 -RunBranch @("work/dfdrill")
Check "RED: the same branch fires once it exists on a remote" ((Get-CondStatus $r "work-branch-on-remote") -eq "fire") ((Get-CondEvidence $r "work-branch-on-remote") -join "; ")

# ====================================================================================
Step "A5  protected-ref-moved: INDETERMINATE with no baseline, GREEN unchanged, RED when main moves"
# ====================================================================================
$repoA5 = New-ScratchRepo "repo-a5"
$stateA5 = Join-Path $Root "state-a5"
$cfgA5 = New-DrillConfig "a5" { param($c) }
$r = Invoke-Andon -Config $cfgA5 -Only "protected-ref-moved" -Repo $repoA5 -StateDir $stateA5
Check "NOT A PASS: with no baseline the condition is INDETERMINATE" ((Get-CondStatus $r "protected-ref-moved") -eq "indeterminate") ("status=" + (Get-CondStatus $r "protected-ref-moved"))
Check "NOT A PASS: an indeterminate condition still raises the board (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)

$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $cfgA5; $env:AI_STACK_WORKTREE_STATE = $stateA5
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $repoA5 | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Andon -Config $cfgA5 -Only "protected-ref-moved" -Repo $repoA5 -StateDir $stateA5
Check "GREEN: an unmoved protected ref does not fire" ((Get-CondStatus $r "protected-ref-moved") -eq "ok") ("status=" + (Get-CondStatus $r "protected-ref-moved"))

Push-Location $repoA5
try { Invoke-Git commit -q --allow-empty -m "something lands on main" | Out-Null } finally { Pop-Location }
$r = Invoke-Andon -Config $cfgA5 -Only "protected-ref-moved" -Repo $repoA5 -StateDir $stateA5
Check "RED: main moving under the run fires" ((Get-CondStatus $r "protected-ref-moved") -eq "fire") ((Get-CondEvidence $r "protected-ref-moved") -join "; ")

# ====================================================================================
Step "A6  a condition naming a predicate nobody implemented is REFUSED, not skipped"
# ====================================================================================
$repoA6 = New-ScratchRepo "repo-a6"
$cfgA6 = New-DrillConfig "a6" { param($c) (Get-Cond $c "policy-declared-unread").predicate = "detect-vibes" }
$r = Invoke-Andon -Config $cfgA6 -Only "policy-declared-unread" -Repo $repoA6
Check "a phantom predicate is an ERROR (exit 1), never a silent pass" ($r.code -eq 1) ("exit=" + $r.code)

# ====================================================================================
# THE UNATTENDED RUN. Everything below drives the REAL queue.ps1 with no human at either
# gate. Isolated repo, isolated state dir, isolated config.
# ====================================================================================
function New-DarkFixture([string]$name, [string]$gateProfile) {
    $repo = New-ScratchRepo $name
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    # Fixture sources so `git-error-swallowed` has something clean to scan inside this repo.
    foreach ($d in @("scripts/checks", "scripts/agent-harness")) {
        $full = Join-Path $repo $d
        New-Item -ItemType Directory -Force -Path $full | Out-Null
        Set-Content -Path (Join-Path $full "clean.ps1") -Encoding ascii -Value @(
            'function Get-CleanHead {',
            '    $out = & git.exe rev-parse HEAD',
            '    if ($LASTEXITCODE -ne 0) { throw "git failed" }',
            '    return $out',
            '}')
    }
    Push-Location $repo
    try {
        Invoke-Git add -A | Out-Null
        Invoke-Git commit -q -m "fixture sources" | Out-Null
        Invoke-Git checkout -q -b work/dfdrill | Out-Null
        Set-Content -Path (Join-Path $repo "DELIVERABLE.md") -Encoding ascii -Value "the work"
        Invoke-Git add DELIVERABLE.md | Out-Null
        Invoke-Git commit -q -m "the work" | Out-Null
        Invoke-Git checkout -q main | Out-Null
    } finally { Pop-Location }
    $cfg = New-DrillConfig $name {
        param($c)
        $c.pipeline.gate_profile = $gateProfile
    }
    # The globs are per-fixture, so they are patched after the shared edit above.
    $o = Get-Content -Raw -Path $cfg | ConvertFrom-Json
    (Get-Cond $o "git-error-swallowed").params.globs = @("scripts/checks/*.ps1", "scripts/agent-harness/*.ps1")
    ($o | ConvertTo-Json -Depth 40) | Set-Content -Path $cfg -Encoding ASCII
    return @{ repo = $repo; state = $state; cfg = $cfg }
}

function Invoke-Queue($fix, [string[]]$QArgs) {
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
    $env:AI_STACK_HARNESS_CONFIG = $fix.cfg
    $env:AI_STACK_WORKTREE_STATE = $fix.state
    $env:AI_STACK_WORK_LINE = "dev"
    Push-Location $fix.repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -File $QueuePs @QArgs 2>&1 }
    finally {
        $ErrorActionPreference = $prev
        Pop-Location
        $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState; $env:AI_STACK_WORK_LINE = $prevLine
    }
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { "$_" }) -join "`n") }
}

function Get-QueueItem($fix, [string]$id) {
    $p = Join-Path $fix.state "queue\$id.json"
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Raw -Path $p | ConvertFrom-Json)
}
function Get-Ledger($fix) {
    $p = Join-Path $fix.state "audit\gates.jsonl"
    if (-not (Test-Path $p)) { return @() }
    return @(Get-Content -Path $p | Where-Object { $_.Trim() } | ForEach-Object { ConvertFrom-Json $_ })
}

$anchorFile = Join-Path $Root "anchor.json"
Set-Content -Path $anchorFile -Encoding ascii -Value @(
    '{',
    '  "goal": "DELIVERABLE.md exists and states what the work was, unambiguously.",',
    '  "artifact": "DELIVERABLE.md - a one-line note produced by the drill fixture.",',
    '  "audience": "The next agent to read the file with no other context.",',
    '  "acceptance": [',
    '    "DELIVERABLE.md exists on the branch. Fail: it is absent.",',
    '    "It contains exactly one line. Fail: it is empty or contradictory."',
    '  ],',
    '  "out_of_scope": ["Anything outside DELIVERABLE.md."],',
    '  "findings_sink": "documentation/notes/u6dark-findings.md"',
    '}')
$planFile = Join-Path $Root "plan.md"
Set-Content -Path $planFile -Encoding ascii -Value @(
    "# Drill test plan",
    "Case 1: DELIVERABLE.md exists on work/dfdrill. Pass: it does. Fail: absent.",
    "Case 2: it holds one line. Pass: one. Fail: zero or contradictory lines.")

# ====================================================================================
Step "B  a CLEAN unattended run lands, with a COMPLETE audit trail"
# ====================================================================================
$fixB = New-DarkFixture "dark-clean" "dark"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixB.cfg; $env:AI_STACK_WORKTREE_STATE = $fixB.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixB.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Queue $fixB @("-Propose", "-Id", "dfd", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
Check "propose: the item exists in anchor-draft" ((Get-QueueItem $fixB "dfd").state -eq "anchor-draft") $r.out.Split("`n")[0]

$r = Invoke-Queue $fixB @("-Submit", "-Id", "dfd", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixB "dfd"
Check "ANCHOR GATE auto-passed with NO human (exit 0)" ($r.code -eq 0 -and $it.state -eq "ready-to-test") ("exit=" + $r.code + " state=" + $it.state)
Check "the auto-pass says so out loud" ($r.out -like "*NO HUMAN CONFIRMED IT*") ""
Check "the item's anchor_confirmed_by is the RESERVED auto principal, not a name" ($it.anchor_confirmed_by -eq "auto:dark") ("got '" + $it.anchor_confirmed_by + "'")
Check "the item records the gate kind as 'auto'" ($it.gates.anchor.kind -eq "auto") ("got '" + $it.gates.anchor.kind + "'")

$r = Invoke-Queue $fixB @("-Claim", "-Id", "dfd", "-Role", "tester", "-By", "wt-tester")
Check "a tester who is not the developer may claim it" ($r.code -eq 0) ("exit=" + $r.code)
$evFile = Join-Path $Root "evidence.md"
Set-Content -Path $evFile -Encoding ascii -Value @("Case 1: file present. Case 2: one line. Both pass.")
$r = Invoke-Queue $fixB @("-Pass", "-Id", "dfd", "-By", "wt-tester", "-Evidence", $evFile, "-PlanAdequate")
$it = Get-QueueItem $fixB "dfd"
Check "PRE-REVIEW GATE auto-passed with NO human" ($r.code -eq 0 -and $it.state -eq "ready-review") ("exit=" + $r.code + " state=" + $it.state)
Check "the pre-review auto-pass says so out loud" ($r.out -like "*NO HUMAN SAW IT*") ""
Check "the item records pre_review as 'auto' by the reserved principal" (($it.gates.pre_review.kind -eq "auto") -and ($it.gates.pre_review.by -eq "auto:dark")) ("kind=" + $it.gates.pre_review.kind + " by=" + $it.gates.pre_review.by)

$r = Invoke-Queue $fixB @("-Claim", "-Id", "dfd", "-Role", "reviewer", "-By", "wt-reviewer")
Check "a reviewer who is not the developer may claim it" ($r.code -eq 0) ("exit=" + $r.code)
# A REAL merge, not a recorded claim of one. queue.ps1 refuses a -Sha that does not contain
# the branch, because a pipeline whose terminal state can be reached without the thing
# happening is worse than no pipeline.
Invoke-GitAt $fixB.repo @("checkout", "-q", "dev") | Out-Null
Invoke-GitAt $fixB.repo @("merge", "--no-ff", "-q", "-m", "merge work/dfdrill", "work/dfdrill") | Out-Null
$mergeSha = (Invoke-GitAt $fixB.repo @("rev-parse", "HEAD"))[0].Trim()
$r = Invoke-Queue $fixB @("-Merged", "-Id", "dfd", "-By", "wt-reviewer", "-Sha", $mergeSha, "-FitsCodebase")
$it = Get-QueueItem $fixB "dfd"
Check "the unattended run LANDS (state merged)" ($it.state -eq "merged") ("state=" + $it.state + " exit=" + $r.code)

$led = Get-Ledger $fixB
Check "the ledger holds exactly two gate events for this run" (@($led).Count -eq 2) ("count=" + @($led).Count)
$autoRecs = @($led | Where-Object { $_.kind -eq "auto" -and $_.decision -eq "passed" })
Check "both are AUTO passes, and each names its gate profile" ((@($autoRecs).Count -eq 2) -and (@($autoRecs | Where-Object { $_.gate_profile -eq "dark" }).Count -eq 2)) ""
Check "each auto pass records the andon verdict that authorised it" ((@($autoRecs | Where-Object { $_.andon.status -eq "clear" }).Count -eq 2)) (($autoRecs | ForEach-Object { $_.andon.status }) -join ",")
Check "no record can be mistaken for a human approval" ((@($led | Where-Object { $_.kind -eq "human" }).Count -eq 0) -and (@($led | Where-Object { "$($_.principal)".StartsWith("auto:") }).Count -eq 2)) ""

$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "THE COLUMN'S SECOND HALF: the audit trail verifies COMPLETE (exit 0)" ($r.code -eq 0) ("exit=" + $r.code + " :: " + ($r.out -split "`n" | Select-Object -Last 1))

$r = Invoke-Queue $fixB @("-Audit", "-Id", "dfd")
Check "-Audit flags, in words, that no human was in the loop" ($r.out -like "*NO HUMAN in the loop*") ""

# ====================================================================================
Step "C  the completeness check has TEETH: tampered trails must go red"
# ====================================================================================
$ledgerPath = Join-Path $fixB.state "audit\gates.jsonl"
$ledgerBak = Get-Content -Path $ledgerPath

# C1 - relabel an auto pass as a human one. This is the exact failure the clause is
# designed against: a record that reads as human approval when no human was there.
$tampered = @($ledgerBak | ForEach-Object { $_ -replace '"kind":"auto"', '"kind":"human"' })
Set-Content -Path $ledgerPath -Encoding ASCII -Value $tampered
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "RED: an auto pass relabelled 'human' is caught" ($r.code -eq 1) ("exit=" + $r.code)
Check "RED: and the finding names the reserved-namespace violation" ($r.out -like "*reserved*" -or $r.out -like "*item says*") ""

# C2 - delete a record entirely. A crossed gate with no record must not verify.
Set-Content -Path $ledgerPath -Encoding ASCII -Value @($ledgerBak | Select-Object -First 1)
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "RED: a MISSING record for a crossed gate is caught" ($r.code -eq 1) ("exit=" + $r.code)
Check "RED: and it says which gate has no record" ($r.out -like "*no pass record*") ""

# C3 - a record claiming the andon board was raised must not count as an authorised pass.
$tampered = @($ledgerBak | ForEach-Object { $_ -replace '"status":"clear"', '"status":"raised"' })
Set-Content -Path $ledgerPath -Encoding ASCII -Value $tampered
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "RED: an auto pass taken while the board was raised is caught" ($r.code -eq 1) ("exit=" + $r.code)

Set-Content -Path $ledgerPath -Encoding ASCII -Value $ledgerBak
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "GREEN again once the ledger is restored (the check is not stuck red)" ($r.code -eq 0) ("exit=" + $r.code)

# C4 - an item the check CANNOT audit must not read as green. This is the same
# skip-counts-as-a-pass shape the andon board refuses, applied to the verifier itself.
$legacy = (Get-Content -Raw -Path (Join-Path $fixB.state "queue\dfd.json") | ConvertFrom-Json)
$legacy.id = "legacy"
$legacy.PSObject.Properties.Remove("gates")
($legacy | ConvertTo-Json -Depth 12) | Set-Content -Path (Join-Path $fixB.state "queue\legacy.json") -Encoding ASCII
$r = Invoke-Queue $fixB @("-VerifyAudit")
Check "NOT A PASS: an item with no gate record is COVERAGE INCOMPLETE (exit 7), not green" ($r.code -eq 7) ("exit=" + $r.code)
Check "and it is named, not silently dropped" ($r.out -like "*UNAUDITED : legacy*") ""
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "legacy")
Check "asked about it DIRECTLY, 'cannot audit' is a FINDING (exit 1)" ($r.code -eq 1) ("exit=" + $r.code)
Remove-Item (Join-Path $fixB.state "queue\legacy.json") -Force

# ====================================================================================
Step "D  an unattended run that HITS an andon condition HALTS AND RAISES"
# ====================================================================================
$fixD = New-DarkFixture "dark-raised" "dark"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixD.cfg; $env:AI_STACK_WORKTREE_STATE = $fixD.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixD.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Queue $fixD @("-Propose", "-Id", "dfd2", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
Check "the item is proposed" ((Get-QueueItem $fixD "dfd2").state -eq "anchor-draft") ""

# Make the condition TRUE: push the run's own branch to a bare repo on disk.
$bareD = Join-Path $Root "origin-d.git"
Invoke-Git init -q --bare $bareD | Out-Null
Push-Location $fixD.repo
try {
    Invoke-Git remote add origin $bareD | Out-Null
    Invoke-Git push -q origin work/dfdrill | Out-Null
} finally { Pop-Location }

$r = Invoke-Queue $fixD @("-Submit", "-Id", "dfd2", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixD "dfd2"
Check "HALT: the anchor gate refuses to auto-pass (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)
Check "HALT: the item stays PARKED at anchor-draft" ($it.state -eq "anchor-draft") ("state=" + $it.state)
Check "RAISE: the halt names the condition that fired" ($r.out -like "*work-branch-on-remote*") ""
$ledD = Get-Ledger $fixD
$refusals = @($ledD | Where-Object { $_.decision -eq "refused" })
Check "RAISE: the refusal is IN THE LEDGER, not just on the console" (@($refusals).Count -ge 1) ("refusals=" + @($refusals).Count)
Check "RAISE: the refusal record carries the fired condition" ((@($refusals)[0].andon.fired -join ";") -like "*work-branch-on-remote*") ""

# And with the condition cleared, the same command proceeds. A detector that cannot be
# cleared is a wall, not an andon cord.
Push-Location $fixD.repo
try { Invoke-Git push -q origin --delete work/dfdrill | Out-Null; Invoke-Git remote remove origin | Out-Null } finally { Pop-Location }
$r = Invoke-Queue $fixD @("-Submit", "-Id", "dfd2", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixD "dfd2"
Check "CLEARED: the same submit now auto-passes the gate" ($r.code -eq 0 -and $it.state -eq "ready-to-test") ("exit=" + $r.code + " state=" + $it.state)

# ====================================================================================
Step "D2  a board that cannot be EVALUATED refuses the gate - it does not open it"
# ====================================================================================
# The gate's own skip-counts-as-a-pass case. If andon.ps1 cannot produce a verdict, the
# only safe reading is "not clear". Broken here by naming a predicate nobody implemented,
# which makes the board exit 1 with no JSON - the same shape as a crash or a missing file.
$fixD2 = New-DarkFixture "dark-broken-board" "dark"
$o = Get-Content -Raw -Path $fixD2.cfg | ConvertFrom-Json
(Get-Cond $o "policy-declared-unread").predicate = "detect-vibes"
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixD2.cfg -Encoding ASCII
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixD2.cfg; $env:AI_STACK_WORKTREE_STATE = $fixD2.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixD2.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Queue $fixD2 @("-Propose", "-Id", "dfd3", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
$r = Invoke-Queue $fixD2 @("-Submit", "-Id", "dfd3", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixD2 "dfd3"
Check "an UNAVAILABLE board refuses the gate (exit 6), never opens it" ($r.code -eq 6) ("exit=" + $r.code)
Check "and the item is still parked at anchor-draft" ($it.state -eq "anchor-draft") ("state=" + $it.state)
$ledD2 = Get-Ledger $fixD2
Check "the refusal records that the board was UNAVAILABLE, not that it was clear" ((@($ledD2 | Where-Object { $_.andon.status -eq "unavailable" }).Count -ge 1)) (($ledD2 | ForEach-Object { $_.andon.status }) -join ",")

# ====================================================================================
Step "E  `attended` is unchanged, and the reserved namespace is reserved both ways"
# ====================================================================================
$fixE = New-DarkFixture "attended" "attended"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixE.cfg; $env:AI_STACK_WORKTREE_STATE = $fixE.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixE.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Queue $fixE @("-Propose", "-Id", "dfe", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
$r = Invoke-Queue $fixE @("-Submit", "-Id", "dfe", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
Check "attended: an unconfirmed anchor still refuses -Submit with exit 5" ($r.code -eq 5) ("exit=" + $r.code)

$r = Invoke-Queue $fixE @("-ConfirmAnchor", "-Id", "dfe", "-By", "auto:dark")
Check "a human -By may NOT claim the reserved auto namespace (exit 4)" ($r.code -eq 4) ("exit=" + $r.code)

$r = Invoke-Queue $fixE @("-ConfirmAnchor", "-Id", "dfe", "-By", "profnovice")
$it = Get-QueueItem $fixE "dfe"
Check "attended: a human confirmation still works" ($r.code -eq 0 -and $it.state -eq "anchor-confirmed") ("exit=" + $r.code + " state=" + $it.state)
Check "attended: the record says 'human' and names the person" (($it.gates.anchor.kind -eq "human") -and ($it.gates.anchor.by -eq "profnovice")) ("kind=" + $it.gates.anchor.kind + " by=" + $it.gates.anchor.by)
$ledE = Get-Ledger $fixE
Check "attended: the human pass is in the ledger too" ((@($ledE | Where-Object { $_.kind -eq "human" }).Count -eq 1)) ("human records=" + @($ledE | Where-Object { $_.kind -eq "human" }).Count)

# ====================================================================================
Write-Host ""
$failed = @($script:Results | Where-Object { -not $_.pass })
Write-Host ("{0} checks, {1} failed" -f @($script:Results).Count, @($failed).Count) -ForegroundColor $(if (@($failed).Count -gt 0) { "Red" } else { "Green" })
foreach ($f in $failed) { Write-Host ("  FAILED: {0} {1}" -f $f.label, $f.detail) -ForegroundColor Red }

if ($Keep) {
    Write-Host ("scratch kept at {0}" -f $Root) -ForegroundColor Yellow
} else {
    Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
}
if (@($failed).Count -gt 0) { exit 1 }
exit 0
