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
# ISOLATION, stated exactly, because the previous wording ("runs entirely in scratch
# repositories under $env:TEMP") was not true and an isolation claim nobody can check is how
# the 2026-08-30 incident happened. Every WRITE is to a scratch repository under $env:TEMP,
# with AI_STACK_WORKTREE_STATE and AI_STACK_HARNESS_CONFIG redirected: this drill mutates no
# repository but its own, and never the real queue or the real ledger. It makes exactly one
# READ of a real repository, deliberately and by name - step A3's last case scans THIS
# checkout's .ps1 files so the detector is shown naming the incident's own function in the
# code that actually shipped. Nothing is written there and no git command runs against it.
#
# The two conditions that could otherwise reach outside a fixture are PINNED to it:
# `operator-checkout-off-branch` takes params.repo, which New-DarkFixture sets to the fixture
# repo (unset it resolves the MAIN CHECKOUT via Get-MainCheckout), and step B asserts from the
# ledger that the board the gate consulted was looking at a path under $env:TEMP. An isolation
# property that is only true because of an inherited working directory is not a property.
#
# PROVE RED BEFORE GREEN. Every condition is shown FIRING on a constructed instance and NOT
# firing on a clean one. A detector that always fires is as useless as one that never does,
# so both directions are asserted for all five.
#
# WHAT THE STEPS COVER: A1-A6 each condition RED and GREEN; B a clean unattended run end to
# end with a complete trail; C the completeness check going red on tampered trails; D/D2 a
# halt and an unavailable board; E attended unchanged; F the board switched off three ways;
# G what "complete" is measured against; H a THINNED board (entries deleted); I -GateProfile
# overriding an attended config; J a fire whose `on_fire` is not `halt` - which auto-passed
# the gate at exit 0 with `fired=[]` in the ledger until 2026-08-30.
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

# The repo is still DETACHED and mid-rebase from the two cases above - a genuinely dirty
# checkout, which is exactly the state in which "the board is clear" must be impossible to
# say. Switching the board off must not launder it into a green.
$cfgA1off = New-DrillConfig "a1-off" {
    param($c)
    (Get-Cond $c "operator-checkout-off-branch").params.repo = $repoA
    $c.andon.enabled = $false
}
$r = Invoke-Andon -Config $cfgA1off -Repo $repoA
Check "andon.enabled=false is NOT-EVALUATED, never 'clear'" ($r.verdict.board -eq "not-evaluated") ("board=" + $r.verdict.board)
Check "and it exits 6, so no gate can read it as a pass" ($r.code -eq 6) ("exit=" + $r.code)
Check "the verdict states its COVERAGE: 5 declared, 0 evaluated" (([int]$r.verdict.coverage.declared -eq 5) -and ([int]$r.verdict.coverage.evaluated -eq 0)) ("declared=" + $r.verdict.coverage.declared + " evaluated=" + $r.verdict.coverage.evaluated)

# THE DOCUMENTED REVERT PATH. Deleting the andon block was described as restoring prior
# behaviour; under `dark` it removed the only thing between an unattended run and its own
# approval. An absent board is now an absent board, not a clear one.
$cfgA1none = New-DrillConfig "a1-no-board" { param($c) $c.PSObject.Properties.Remove("andon") }
$r = Invoke-Andon -Config $cfgA1none -Repo $repoA
# INCOMPLETE, not not-evaluated, since 2026-08-30: an absent block is five REQUIRED
# conditions that are not declared, and naming them is strictly more useful than saying
# nothing was evaluated. Either way it exits 6; what changed is what the operator is told.
Check "an ABSENT andon block is INCOMPLETE (exit 6), not an empty clear board" (($r.verdict.board -eq "incomplete") -and ($r.code -eq 6)) ("board=" + $r.verdict.board + " exit=" + $r.code)
Check "and it NAMES all five required conditions as missing" (([int]$r.verdict.coverage.missing -eq 5) -and (@($r.verdict.coverage.missing_ids) -contains "git-error-swallowed")) ("missing=" + $r.verdict.coverage.missing + " ids=" + (@($r.verdict.coverage.missing_ids) -join ","))

# A condition switched off individually is reported as DISABLED and NAMED, and asking the
# board about that one alone leaves it with nothing evaluated. (The mixed case - some
# evaluated, some switched off - is `partial`, and it is proven at the real gate in step F,
# because it needs a board on which the other conditions genuinely pass.)
$cfgA1part = New-DrillConfig "a1-one-off" {
    param($c)
    (Get-Cond $c "operator-checkout-off-branch").params.repo = $repoA
    (Get-Cond $c "operator-checkout-off-branch") | Add-Member -NotePropertyName enabled -NotePropertyValue $false -Force
}
$r = Invoke-Andon -Config $cfgA1part -Only "operator-checkout-off-branch" -Repo $repoA
Check "a switched-off condition is reported DISABLED, never ok" ((Get-CondStatus $r "operator-checkout-off-branch") -eq "disabled") ("status=" + (Get-CondStatus $r "operator-checkout-off-branch"))
Check "asking only about a switched-off condition evaluates NOTHING (exit 6)" (($r.verdict.board -eq "not-evaluated") -and ($r.code -eq 6)) ("board=" + $r.verdict.board + " exit=" + $r.code)
Check "and the verdict NAMES the condition that was not evaluated" ((@($r.verdict.coverage.disabled_ids) -join ",") -eq "operator-checkout-off-branch") ((@($r.verdict.coverage.disabled_ids) -join ","))

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

# THE DETECTOR MUST BE ABLE TO DETECT. Matching the dotted path as an unanchored SUBSTRING
# made every key that is a PREFIX of a live one report "read": with `pipeline.gate_profile`
# in the sources, all four keys below passed while no line mentions any of them.
$cfgA2trunc = New-DrillConfig "a2-truncated" {
    param($c)
    $c.pipeline = [pscustomobject]@{ claim_ttl = 60; anchor_require = $true; gate_profil = "attended"; a = 1 }
}
$r = Invoke-Andon -Config $cfgA2trunc -Only "policy-declared-unread" -Repo $repoA2
$ev = @(Get-CondEvidence $r "policy-declared-unread")
Check "RED: keys that are only PREFIXES of live ones are unread, not read" ((Get-CondStatus $r "policy-declared-unread") -eq "fire") ("status=" + (Get-CondStatus $r "policy-declared-unread"))
Check "RED: and all four truncated keys are named" ((($ev -contains "pipeline.claim_ttl") -and ($ev -contains "pipeline.anchor_require") -and ($ev -contains "pipeline.gate_profil") -and ($ev -contains "pipeline.a"))) ($ev -join ", ")

# THE DEAD-KNOB DETECTOR SHIPPED A DEAD KNOB IN ITS OWN BLOCK (`andon.raise.ledger`, zero
# readers). `andon` is a root now, so putting one back must fire.
$cfgA2own = New-DrillConfig "a2-own-block" {
    param($c)
    $c.andon.raise | Add-Member -NotePropertyName ledger -NotePropertyValue $true -Force
}
$r = Invoke-Andon -Config $cfgA2own -Only "policy-declared-unread" -Repo $repoA2
$ev = @(Get-CondEvidence $r "policy-declared-unread")
Check "RED: a dead knob in the ANDON block is caught by the andon board's own condition" (($ev -contains "andon.raise.ledger")) ($ev -join ", ")

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

# CODE OUTSIDE ANY FUNCTION. The scan used to walk function bodies only, so a script whose
# git calls sit at file scope was reported clean - and a live in-glob instance,
# check-project-configs.ps1:18, went unflagged for it.
Remove-Item (Join-Path $fixDir "commented.ps1") -Force
Set-Content -Path (Join-Path $fixDir "toplevel.ps1") -Encoding ascii -Value @(
    '# there is no function anywhere in this file',
    '& git.exe push origin HEAD | Out-Null',
    'Write-Host "pushed"')
$r = Invoke-Andon -Config $cfgA3 -Only "git-error-swallowed" -Repo $repoA3
$ev = @(Get-CondEvidence $r "git-error-swallowed")
Check "RED: a git call OUTSIDE any function fires" ((Get-CondStatus $r "git-error-swallowed") -eq "fire") ($ev -join "; ")
Check "RED: and it is attributed to the top level, not to some function" (($ev -join ";") -like "*(top level)*") ($ev -join "; ")

# NO BODY-WIDE AMNESTY. An unrelated guard clause at the TOP of a function used to clear
# every git call below it, because the old check asked whether the body mentioned
# $LASTEXITCODE anywhere at all.
Remove-Item (Join-Path $fixDir "toplevel.ps1") -Force
Set-Content -Path (Join-Path $fixDir "amnesty.ps1") -Encoding ascii -Value @(
    'function Push-Everything {',
    '    param([string]$Branch)',
    '    if (-not $Branch) { throw "no branch" }',
    '    $prev = $ErrorActionPreference',
    '    $ErrorActionPreference = "Continue"',
    '    & git.exe push origin $Branch | Out-Null',
    '    $ErrorActionPreference = $prev',
    '}')
$r = Invoke-Andon -Config $cfgA3 -Only "git-error-swallowed" -Repo $repoA3
$ev = @(Get-CondEvidence $r "git-error-swallowed")
Check "RED: a throw BEFORE the git call does not excuse the git call" ((Get-CondStatus $r "git-error-swallowed") -eq "fire") ($ev -join "; ")
Check "RED: and the finding names the swallowing function" (($ev -join ";") -like "*Push-Everything*") ($ev -join "; ")
Remove-Item (Join-Path $fixDir "amnesty.ps1") -Force
Set-Content -Path (Join-Path $fixDir "commented.ps1") -Encoding ascii -Value @(
    'function Get-Nothing {',
    '    # Thin policy wrapper over the git fact - mentions git, runs none.',
    '    return "no git here"',
    '}')

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
    # Per-fixture params, patched after the shared edit above.
    $o = Get-Content -Raw -Path $cfg | ConvertFrom-Json
    (Get-Cond $o "git-error-swallowed").params.globs = @("scripts/checks/*.ps1", "scripts/agent-harness/*.ps1")
    # PIN THE CHECKOUT CONDITION TO THIS FIXTURE. Left empty it calls Get-MainCheckout, which
    # answers from the CURRENT DIRECTORY - so the fixture's isolation would rest on every
    # caller remembering to Push-Location, and a detached operator checkout would turn this
    # fixture's board red for reasons that have nothing to do with the fixture.
    (Get-Cond $o "operator-checkout-off-branch").params.repo = $repo
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
# 'clear' has to be checkable, not just readable. The record carries the COVERAGE, so an
# auditor can tell five conditions that looked from five that were switched off.
Check "each auto pass states its COVERAGE: 5 of 5 evaluated, none switched off" ((@($autoRecs | Where-Object { [int]$_.andon.evaluated -eq 5 -and [int]$_.andon.conditions -eq 5 -and [int]$_.andon.disabled -eq 0 }).Count -eq 2)) (($autoRecs | ForEach-Object { "$($_.andon.evaluated)/$($_.andon.conditions)" }) -join ",")
# HERMETICITY, asserted rather than assumed: the board the GATE consulted was looking at the
# fixture, not at the operator's checkout.
$scratchOnly = @($autoRecs | Where-Object { "$($_.andon.repo)".Replace("/", [string][char]92).StartsWith($Root) })
Check "the board the gate consulted was looking at the SCRATCH repo, not the operator's" (@($scratchOnly).Count -eq 2) (($autoRecs | ForEach-Object { $_.andon.repo }) -join " | ")
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

# C3b - the word 'clear' with counters that say nobody looked. This is the shape the record
# used to be UNABLE to express: `andon.status=clear conditions=5` on a board that evaluated
# nothing. The verifier re-derives the claim from the counters instead of trusting the word.
$tampered = @($ledgerBak | ForEach-Object { $_ -replace '"evaluated":5', '"evaluated":0' })
Set-Content -Path $ledgerPath -Encoding ASCII -Value $tampered
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "RED: 'clear' with 0 of 5 conditions evaluated is not an authorised pass" ($r.code -eq 1) ("exit=" + $r.code)
Check "RED: and the finding says nothing was checked" ($r.out -like "*nothing was checked*") ""

# C3c - a record that cannot state its coverage at all. Incomplete by definition, per the
# rule at the top of gate-audit.ps1.
$tampered = @($ledgerBak | ForEach-Object { $_ -replace ',"evaluated":5,"disabled":0', '' })
Set-Content -Path $ledgerPath -Encoding ASCII -Value $tampered
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "dfd")
Check "RED: an auto record that does not state its coverage is incomplete" ($r.code -eq 1) ("exit=" + $r.code)

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
Step 'E  attended is unchanged, and the reserved namespace is reserved both ways'
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
Step "F  switching the board OFF must not open the gates - the revert is not a kill switch"
# ====================================================================================
# The findings note used to offer `andon.enabled: false`, or deleting the andon block, as the
# revert that makes "every gate behave as it did before". Under `dark` that is the opposite of
# true: it removes the only thing standing between an unattended run and its own approval. A
# revert path that does something other than what it says is worse than none, because it is
# reached for in a hurry. THE REVERT IS `pipeline.gate_profile: attended`, and step E proves
# that one. These two cases prove the other reading cannot silently pass.
foreach ($case in @(
    @{ name = "andon-off";  label = "andon.enabled=false"; status = "not-evaluated"; evaluated = 0
       edit = { param($o) $o.andon.enabled = $false } },
    @{ name = "andon-gone"; label = "the andon block deleted"; status = "incomplete"; evaluated = 0
       edit = { param($o) $o.PSObject.Properties.Remove("andon") } },
    # The MIXED case. Four conditions look and pass; one is switched off. That is not a clear
    # board either - it is the operator saying "do not look at this one", which is a decision
    # they may make, and a decision to be attended.
    @{ name = "one-off";    label = "one condition switched off"; status = "partial"; evaluated = 4
       edit = { param($o) ($o.andon.conditions | Where-Object { $_.id -eq "work-branch-on-remote" }) |
                          Add-Member -NotePropertyName enabled -NotePropertyValue $false -Force } }
)) {
    $fixF = New-DarkFixture ("dark-" + $case.name) "dark"
    # Baseline FIRST, while the config still declares protected-ref-moved: two of these cases
    # remove the block that -Baseline reads.
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
    $env:AI_STACK_HARNESS_CONFIG = $fixF.cfg; $env:AI_STACK_WORKTREE_STATE = $fixF.state
    & $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixF.repo | Out-Null
    $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState
    $o = Get-Content -Raw -Path $fixF.cfg | ConvertFrom-Json
    & $case.edit $o
    ($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixF.cfg -Encoding ASCII

    $r = Invoke-Queue $fixF @("-Propose", "-Id", "dff", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
    $r = Invoke-Queue $fixF @("-Submit", "-Id", "dff", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
    $it = Get-QueueItem $fixF "dff"
    Check ("HALT with " + $case.label + ": the anchor gate refuses (exit 6)") ($r.code -eq 6) ("exit=" + $r.code)
    Check ("HALT with " + $case.label + ": the item stays parked at anchor-draft") ($it.state -eq "anchor-draft") ("state=" + $it.state)
    $ledF = Get-Ledger $fixF
    $ref = @($ledF | Where-Object { $_.decision -eq "refused" })
    Check ("HALT with " + $case.label + ": the refusal is in the ledger, recorded as '" + $case.status + "'") ((@($ref).Count -ge 1) -and (@($ref)[0].andon.status -eq $case.status)) ("status=" + ((@($ref) | ForEach-Object { $_.andon.status }) -join ","))
    Check ("HALT with " + $case.label + ": the refusal states " + $case.evaluated + " condition(s) evaluated") (([int](@($ref)[0].andon.evaluated)) -eq [int]$case.evaluated) ("evaluated=" + (@($ref)[0].andon.evaluated))
}

# ====================================================================================
Step "G  'COMPLETE' is checked against the gates the pipeline REQUIRES, not only the ones an item happened to reach"
# ====================================================================================
# Get-CrossedGates counted the anchor gate as crossed only when the item CARRIED an anchor. An
# item that ran end to end with no anchor therefore crossed no anchor gate, held one ledger
# record, and verified COMPLETE - "complete" meaning "every gate this item happened to cross".
# With anchor_required=true (the shipped default) an item past anchor-draft has crossed it,
# anchor or not; with anchor_required=false the anchor gate genuinely is not a gate, and
# -VerifyAudit now says that in words instead of quietly counting a narrower complete.
$fixG = New-DarkFixture "no-anchor" "dark"
$o = Get-Content -Raw -Path $fixG.cfg | ConvertFrom-Json
$o.pipeline.anchor_required = $false
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixG.cfg -Encoding ASCII
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixG.cfg; $env:AI_STACK_WORKTREE_STATE = $fixG.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixG.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

$r = Invoke-Queue $fixG @("-Submit", "-Id", "dfg", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
Check "anchor_required=false: an item may be submitted with NO anchor" ($r.code -eq 0) ("exit=" + $r.code)
$r = Invoke-Queue $fixG @("-Claim", "-Id", "dfg", "-Role", "tester", "-By", "wt-tester")
$r = Invoke-Queue $fixG @("-Pass", "-Id", "dfg", "-By", "wt-tester", "-Evidence", $evFile, "-PlanAdequate")
Check "and it auto-passes pre_review under dark" ((Get-QueueItem $fixG "dfg").state -eq "ready-review") ("state=" + (Get-QueueItem $fixG "dfg").state)
$r = Invoke-Queue $fixG @("-VerifyAudit", "-Id", "dfg")
Check "COMPLETE says WHAT it covered - it does not let 'complete' read as 'all gates enforced'" ($r.out -like "*anchor_required=false*") ($r.out -split "`n" | Select-Object -Last 3) -join " / "

# The shipped default is the case that matters, and there it is a FINDING, not a footnote:
# an item past the anchor gate with no anchor record is a missing pass.
$o = Get-Content -Raw -Path $fixG.cfg | ConvertFrom-Json
$o.pipeline.anchor_required = $true
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixG.cfg -Encoding ASCII
$r = Invoke-Queue $fixG @("-VerifyAudit", "-Id", "dfg")
Check "anchor_required=true: the same anchorless item is INCOMPLETE (exit 1)" ($r.code -eq 1) ("exit=" + $r.code)
Check "and the finding names the anchor gate with no pass record" ($r.out -like "*anchor*no pass record*") ""

# ====================================================================================
Step "H  a THINNED board must not open the gates either - deleting condition ENTRIES"
# ====================================================================================
# THE THIRD WAY OFF, and the one an operator or an agent would actually reach for. Step F
# closed two: `andon.enabled: false` and deleting the whole `andon` block. Neither is what
# somebody does when a condition is in their way. They delete THAT CONDITION'S ENTRY from
# `andon.conditions` - and until 2026-08-30 that produced a board reporting itself perfectly
# healthy: pruned to one of five on a genuinely detached checkout the gate AUTO-PASSED, exit
# 0, ledger `clear` with `1 declared / 1 evaluated / 0 switched off`, `-VerifyAudit COMPLETE`.
# Every counter was TRUE, because every counter was relative to the config's own thinned list.
#
# The fix is the required SET, declared in config.ps1 where the config cannot edit it. These
# cases prove the refusal at the REAL gate, prove the record NAMES which ids are gone, and -
# because a fix that refuses everything is not a fix - re-run the negative control after them.
foreach ($case in @(
    @{ name = "thin-one"; label = "ONE condition entry deleted"
       drop = @("protected-ref-moved") },
    @{ name = "thin-four"; label = "FOUR condition entries deleted"
       drop = @("operator-checkout-off-branch", "policy-declared-unread", "git-error-swallowed", "protected-ref-moved") }
)) {
    $fixH = New-DarkFixture ("dark-" + $case.name) "dark"
    # Baseline FIRST: one of these cases deletes the condition -Baseline reads.
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
    $env:AI_STACK_HARNESS_CONFIG = $fixH.cfg; $env:AI_STACK_WORKTREE_STATE = $fixH.state
    & $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixH.repo | Out-Null
    $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

    $o = Get-Content -Raw -Path $fixH.cfg | ConvertFrom-Json
    # NOTHING ELSE IS TOUCHED: andon.enabled stays true, the block stays present, every
    # surviving condition keeps its params. Only the entries are gone.
    $o.andon.conditions = @($o.andon.conditions | Where-Object { $case.drop -notcontains $_.id })
    ($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixH.cfg -Encoding ASCII
    $kept = @($o.andon.conditions).Count

    $r = Invoke-Queue $fixH @("-Propose", "-Id", "dfh", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
    $r = Invoke-Queue $fixH @("-Submit", "-Id", "dfh", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
    $it = Get-QueueItem $fixH "dfh"
    Check ("HALT with " + $case.label + ": the anchor gate refuses (exit 6)") ($r.code -eq 6) ("exit=" + $r.code + " kept=" + $kept)
    Check ("HALT with " + $case.label + ": the item stays parked at anchor-draft") ($it.state -eq "anchor-draft") ("state=" + $it.state)
    Check ("HALT with " + $case.label + ": nothing signed the anchor gate") (-not $it.anchor_confirmed_by) ("anchor_confirmed_by='" + $it.anchor_confirmed_by + "'")
    $ledH = @(Get-Ledger $fixH | Where-Object { $_.decision -eq "refused" })
    Check ("HALT with " + $case.label + ": the refusal is recorded as 'incomplete', not 'clear'") ((@($ledH).Count -ge 1) -and (@($ledH)[0].andon.status -eq "incomplete")) ("status=" + ((@($ledH) | ForEach-Object { $_.andon.status }) -join ","))
    # NAMED, not counted. "the board is short" sends an operator to diff the config; the ids
    # send them to the lines that are gone.
    $named = @(@($ledH)[0].andon.missing_ids)
    $allNamed = $true
    foreach ($d in $case.drop) { if ($named -notcontains $d) { $allNamed = $false } }
    Check ("HALT with " + $case.label + ": the record NAMES every missing id") (($allNamed) -and (@($named).Count -eq @($case.drop).Count)) ("missing_ids=" + ($named -join ","))
    # The counters the OLD record carried are all still satisfied by this board - which is
    # precisely why they could not catch it. Asserted, so the reason survives the fix.
    Check ("HALT with " + $case.label + ": and the old counters would have said 'full coverage'") ((([int](@($ledH)[0].andon.evaluated)) -eq $kept) -and (([int](@($ledH)[0].andon.disabled)) -eq 0)) ("evaluated=" + (@($ledH)[0].andon.evaluated) + "/" + (@($ledH)[0].andon.conditions) + " disabled=" + (@($ledH)[0].andon.disabled))
}

# THE NEGATIVE CONTROL, re-run after the fix. A gate that refuses everything is not a gate.
# Step B already lands a clean board end to end; this repeats the auto-pass on a fresh fixture
# built exactly like the two thinned ones above, so the ONLY difference between refusing and
# passing is which condition entries are present.
$fixHok = New-DarkFixture "dark-thin-control" "dark"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixHok.cfg; $env:AI_STACK_WORKTREE_STATE = $fixHok.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixHok.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState
$r = Invoke-Queue $fixHok @("-Propose", "-Id", "dfhok", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
$r = Invoke-Queue $fixHok @("-Submit", "-Id", "dfhok", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixHok "dfhok"
Check "CONTROL: a FULL board still auto-passes the anchor gate (exit 0)" (($r.code -eq 0) -and ($it.state -eq "ready-to-test")) ("exit=" + $r.code + " state=" + $it.state)
Check "CONTROL: and it still says NO HUMAN CONFIRMED IT" ($r.out -like "*NO HUMAN CONFIRMED IT*") ""
Check "CONTROL: signed by the reserved auto principal" ($it.anchor_confirmed_by -eq "auto:dark") ("got '" + $it.anchor_confirmed_by + "'")
$ledHok = @(Get-Ledger $fixHok | Where-Object { $_.decision -eq "passed" })
Check "CONTROL: the pass record states 0 of 5 required MISSING" ((@($ledHok).Count -ge 1) -and ([int](@($ledHok)[0].andon.missing) -eq 0) -and ([int](@($ledHok)[0].andon.required) -eq 5)) ("missing=" + (@($ledHok)[0].andon.missing) + "/" + (@($ledHok)[0].andon.required))
$r = Invoke-Queue $fixHok @("-VerifyAudit", "-Id", "dfhok")
Check "CONTROL: the audit trail still verifies COMPLETE (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)

# AND THE COMPLETENESS CHECK RE-DERIVES IT rather than trusting `status`. A tamper that leaves
# the word `clear` in place while admitting a thinned board must still go red - otherwise the
# ledger's own word is its only oracle, which is the defect this file exists to refuse.
$ledgerHok = Join-Path $fixHok.state "audit\gates.jsonl"
$bakHok = Get-Content -Path $ledgerHok
$thinTamper = '"missing":4,"missing_ids":["operator-checkout-off-branch","policy-declared-unread","git-error-swallowed","protected-ref-moved"]'
Set-Content -Path $ledgerHok -Encoding ASCII -Value @($bakHok | ForEach-Object { $_.Replace('"missing":0,"missing_ids":[]', $thinTamper) })
$r = Invoke-Queue $fixHok @("-VerifyAudit", "-Id", "dfhok")
Check "RED: a record still labelled 'clear' but admitting 4 missing conditions is caught" ($r.code -eq 1) ("exit=" + $r.code)
Check "RED: and the finding NAMES them" (($r.out -like "*REQUIRED*") -and ($r.out -like "*git-error-swallowed*")) ""
# A schema-2 shaped record - coverage present, required set absent - cannot answer the
# question at all, and "cannot answer" is a finding, never a pass.
Set-Content -Path $ledgerHok -Encoding ASCII -Value @($bakHok | ForEach-Object { $_.Replace(',"required":5,"missing":0,"missing_ids":[]', '') })
$r = Invoke-Queue $fixHok @("-VerifyAudit", "-Id", "dfhok")
Check "RED: a record that cannot state the required set is a FINDING, not a pass" ($r.code -eq 1) ("exit=" + $r.code)
Set-Content -Path $ledgerHok -Encoding ASCII -Value $bakHok
$r = Invoke-Queue $fixHok @("-VerifyAudit", "-Id", "dfhok")
Check "GREEN again once the ledger is restored" ($r.code -eq 0) ("exit=" + $r.code)

# ====================================================================================
Step 'I  -GateProfile OVERRIDES an attended config, so attended is a default not a lock'
# ====================================================================================
# Disclosed rather than changed. `pipeline.gate_profile: attended` is the right revert and
# step E proves it works - but it is the CONFIGURED DEFAULT, and a single call may name a
# different profile. Reading "the revert is attended" as "no run can self-pass now" would be
# wrong, so the same item is driven both ways here.
$fixI = New-DarkFixture "attended-overridden" "attended"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixI.cfg; $env:AI_STACK_WORKTREE_STATE = $fixI.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixI.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState
# The board is thinned here on purpose, so the override shows up as a DIFFERENT REFUSAL
# rather than as an unattended pass: this step is about which profile the call took, and the
# drill does not need to demonstrate a dark auto-pass a third time.
$o = Get-Content -Raw -Path $fixI.cfg | ConvertFrom-Json
$o.andon.conditions = @($o.andon.conditions | Where-Object { $_.id -ne "protected-ref-moved" })
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixI.cfg -Encoding ASCII

$r = Invoke-Queue $fixI @("-Propose", "-Id", "dfi", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
$r = Invoke-Queue $fixI @("-Submit", "-Id", "dfi", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
Check "the configured profile is attended: -Submit refuses with exit 5 (no confirmed anchor)" ($r.code -eq 5) ("exit=" + $r.code)
$r = Invoke-Queue $fixI @("-GateProfile", "dark", "-Submit", "-Id", "dfi", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
Check "-GateProfile dark on the SAME item takes the dark path instead (exit 6, andon)" ($r.code -eq 6) ("exit=" + $r.code)
Check "so pipeline.gate_profile: attended is a DEFAULT, not a lock" ($r.out -like "*ANDON*") ""

# ====================================================================================
Step "J  a FIRED condition must not open a gate, and must be IN THE RECORD, whatever on_fire says"
# ====================================================================================
# THE FOURTH WAY OFF, and it never touches the board's on/off switch. Set `on_fire` to
# anything but `halt` on ONE condition and, until 2026-08-30: the condition FIRED, the board
# reported `clear`, the dark gate AUTO-PASSED at exit 0 signed `auto:dark`, and the ledger
# read `status=clear evaluated=5 missing=0 fired=[]` with `-VerifyAudit COMPLETE`. `$raised`
# was `action -eq halt` and the record's `fired` list was derived the same way, so a fire
# that did not halt was in no audit surface at all - the clause inverted, since U6 (c) exists
# precisely so an operator can tell afterwards what happened while nobody was looking.
#
# Two separate properties are asserted below, and they fail independently:
#   THE RECORD  - `fired` means the detectors SAW something; `halted` means the line stopped.
#   THE GATE    - a board with a fire on it is never `clear`, so no unattended gate passes it.
$fixJ = New-DarkFixture "dark-warn-onfire" "dark"
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $fixJ.cfg; $env:AI_STACK_WORKTREE_STATE = $fixJ.state
& $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $fixJ.repo | Out-Null
$env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState

# NOTHING ELSE IS TOUCHED: the board is enabled, all five entries are present with their
# params, every on_indeterminate stays `halt`. One word changes.
$o = Get-Content -Raw -Path $fixJ.cfg | ConvertFrom-Json
(Get-Cond $o "work-branch-on-remote").on_fire = "warn"
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixJ.cfg -Encoding ASCII

# Make that one condition TRUE, exactly as step D does.
$bareJ = Join-Path $Root "origin-j.git"
Invoke-Git init -q --bare $bareJ | Out-Null
Push-Location $fixJ.repo
try {
    Invoke-Git remote add origin $bareJ | Out-Null
    Invoke-Git push -q origin work/dfdrill | Out-Null
} finally { Pop-Location }

$rj = Invoke-Andon -Config $fixJ.cfg -Repo $fixJ.repo -StateDir $fixJ.state -RunBranch @("work/dfdrill")
$condJ = @($rj.verdict.conditions | Where-Object { $_.id -eq "work-branch-on-remote" })
Check "the condition genuinely FIRES with on_fire=warn" ((Get-CondStatus $rj "work-branch-on-remote") -eq "fire") ("status=" + (Get-CondStatus $rj "work-branch-on-remote"))
Check "and its recorded ACTION is the configured one, not a rewritten halt" ($condJ[0].action -eq "warn") ("action=" + $condJ[0].action)
Check "the BOARD is not clear: warned, exit 6" (($rj.verdict.board -eq "warned") -and ($rj.code -eq 6)) ("board=" + $rj.verdict.board + " exit=" + $rj.code)
Check "the verdict SEPARATES what fired from what halted" ((@($rj.verdict.coverage.fired_ids) -contains "work-branch-on-remote") -and (@($rj.verdict.coverage.halted_ids).Count -eq 0)) ("fired_ids=" + (@($rj.verdict.coverage.fired_ids) -join ",") + " halted_ids=" + (@($rj.verdict.coverage.halted_ids) -join ","))

$r = Invoke-Queue $fixJ @("-Propose", "-Id", "dfj", "-Anchor", $anchorFile, "-Developer", "wt-dfdrill")
$r = Invoke-Queue $fixJ @("-Submit", "-Id", "dfj", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixJ "dfj"
Check "HALT: a downgraded on_fire does NOT auto-pass the anchor gate (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)
Check "HALT: the item stays parked at anchor-draft" ($it.state -eq "anchor-draft") ("state=" + $it.state)
Check "HALT: nothing signed the anchor gate" (-not $it.anchor_confirmed_by) ("anchor_confirmed_by='" + $it.anchor_confirmed_by + "'")
$ledJ = @(Get-Ledger $fixJ | Where-Object { $_.decision -eq "refused" })
Check "THE RECORD: the refusal is in the ledger as warned, not clear" ((@($ledJ).Count -ge 1) -and (@($ledJ)[0].andon.status -eq "warned")) ("status=" + ((@($ledJ) | ForEach-Object { $_.andon.status }) -join ","))
Check "THE RECORD: the fired list NAMES the condition even though it did not halt" ((@(@($ledJ)[0].andon.fired) -join ";") -like "*work-branch-on-remote*") ("fired=" + (@(@($ledJ)[0].andon.fired) -join ";"))
Check "THE RECORD: the halted list is empty - the two are not one derived list" (@(@($ledJ)[0].andon.halted).Count -eq 0) ("halted=" + (@(@($ledJ)[0].andon.halted) -join ";"))
Check "THE CONSOLE: the halt names the fired condition too" ($r.out -like "*work-branch-on-remote*") ""

# AN ACTION THE BOARD DOES NOT IMPLEMENT is refused rather than guessed at - and refusing
# means the board produces no verdict, which every gate reads as "not clear".
$o = Get-Content -Raw -Path $fixJ.cfg | ConvertFrom-Json
(Get-Cond $o "work-branch-on-remote").on_fire = "log-it-and-carry-on"
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixJ.cfg -Encoding ASCII
$rj2 = Invoke-Andon -Config $fixJ.cfg -Repo $fixJ.repo -StateDir $fixJ.state -RunBranch @("work/dfdrill")
Check "an on_fire the board does not implement is REFUSED (exit 1, no verdict)" (($rj2.code -eq 1) -and ($null -eq $rj2.verdict)) ("exit=" + $rj2.code)
$r = Invoke-Queue $fixJ @("-Submit", "-Id", "dfj", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
Check "and the gate treats an unreadable board as UNAVAILABLE, not as clear (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)

# THE NEGATIVE CONTROL. warn is not a blanket refusal: the condition has to actually FIRE.
# Same fixture, same on_fire: warn, condition cleared - the gate passes.
$o = Get-Content -Raw -Path $fixJ.cfg | ConvertFrom-Json
(Get-Cond $o "work-branch-on-remote").on_fire = "warn"
($o | ConvertTo-Json -Depth 40) | Set-Content -Path $fixJ.cfg -Encoding ASCII
Push-Location $fixJ.repo
try { Invoke-Git push -q origin --delete work/dfdrill | Out-Null; Invoke-Git remote remove origin | Out-Null } finally { Pop-Location }
$r = Invoke-Queue $fixJ @("-Submit", "-Id", "dfj", "-Branch", "work/dfdrill", "-Developer", "wt-dfdrill", "-TestPlan", $planFile)
$it = Get-QueueItem $fixJ "dfj"
Check "CONTROL: with the condition CLEARED, the same warn-declared board auto-passes (exit 0)" (($r.code -eq 0) -and ($it.state -eq "ready-to-test")) ("exit=" + $r.code + " state=" + $it.state)
$passJ = @(Get-Ledger $fixJ | Where-Object { $_.decision -eq "passed" })
Check "CONTROL: the pass record states 0 fired and 0 halted" ((@($passJ).Count -ge 1) -and (@(@($passJ)[0].andon.fired).Count -eq 0) -and (@(@($passJ)[0].andon.halted).Count -eq 0)) ("fired=" + (@(@($passJ)[0].andon.fired).Count) + " halted=" + (@(@($passJ)[0].andon.halted).Count))
$r = Invoke-Queue $fixJ @("-VerifyAudit", "-Id", "dfj")
Check "CONTROL: the trail verifies COMPLETE (exit 0)" ($r.code -eq 0) ("exit=" + $r.code)

# AND THE VERIFIER RE-DERIVES IT. A record that keeps the word clear while admitting a fire
# must go red, or the ledger's own word is its only oracle.
$ledgerJ = Join-Path $fixJ.state "audit\gates.jsonl"
$bakJ = Get-Content -Path $ledgerJ
$fireTamper = '"fired":["work-branch-on-remote: a work branch of this run is on a remote"]'
Set-Content -Path $ledgerJ -Encoding ASCII -Value @($bakJ | ForEach-Object { $_.Replace('"fired":[]', $fireTamper) })
$r = Invoke-Queue $fixJ @("-VerifyAudit", "-Id", "dfj")
Check "RED: a record labelled clear that admits a FIRE is caught" ($r.code -eq 1) ("exit=" + $r.code)
Check "RED: and the finding NAMES the fired condition" ($r.out -like "*work-branch-on-remote*") ""
# A schema-3 shaped record - fired present, halted absent - cannot say whether a condition
# fired without halting, and "cannot answer" is a finding, never a pass.
Set-Content -Path $ledgerJ -Encoding ASCII -Value @($bakJ | ForEach-Object { $_.Replace(',"halted":[]', '') })
$r = Invoke-Queue $fixJ @("-VerifyAudit", "-Id", "dfj")
Check "RED: a record that cannot separate fired from halted is a FINDING, not a pass" ($r.code -eq 1) ("exit=" + $r.code)
Set-Content -Path $ledgerJ -Encoding ASCII -Value $bakJ
$r = Invoke-Queue $fixJ @("-VerifyAudit", "-Id", "dfj")
Check "GREEN again once the ledger is restored" ($r.code -eq 0) ("exit=" + $r.code)

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
