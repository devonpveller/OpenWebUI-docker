# drill-u6-dark-gate.ps1 - U6's section 2 column, both halves, at the REAL gate.
#
# THE COLUMN (PLAN.md section 2, U6):
#
#     "Gym: an unattended run that hits each andon condition halts-and-raises; one that
#      hits none lands with a complete audit trail"
#
#   .\drill-u6-dark-gate.ps1            # run it
#   .\drill-u6-dark-gate.ps1 -Keep      # keep the scratch dirs for inspection
#
# Exit: 0 every assertion passed | 1 one or more failed
#
# ------------------------------------------------------------------------------------
# WHY THIS EXISTS BESIDE drill-dark-factory.ps1, AND WHAT IT DOES NOT REPLACE
#
# `scripts/agent-harness/drill-dark-factory.ps1` is the broader instrument - 213 assertions
# across ~20 fixtures - and it stays the reference for this phase. MEASURED 2026-09-02 on
# `work/dfufp`: **exit 1, 146 passed, 67 failed**. Every one of those 67 traces to a single
# fact about the SHIPPED configuration, which each of its fixtures inherits whole:
#
#     powershell -File scripts/agent-harness/andon.ps1 -Evaluate -Only policy-declared-unread
#     ANDON BOARD: RAISED
#       [fire] policy-declared-unread -> declared policy nothing reads: pipeline.convergence
#                                                                             exit 6
#
# `harness.config.json` declares `pipeline.convergence` (a spec block whose own `_status`
# says "NOT YET LIVE") and no executable source reads it, so the condition that exists to
# catch exactly that fires - correctly - and every *clean board* control in that drill
# inherits a board that can never be clear. MEASURED: with that one key removed from the
# shipped config, the same drill runs **0 failed**. So the drill is not broken and its red
# is not noise; it is one config decision wide, and that decision - is the key dead, or is
# its reader unwritten? - is the operator's, not a check author's.
#
# WHAT THIS FILE ADDS, and it is not a lighter re-run of that drill. It asks the column's
# question of the GATE rather than of the shipped config, by CONSTRUCTING both worlds:
#
#   HALT fixture   the shipped board plus a dead policy key OF THIS DRILL'S OWN
#                  (`pipeline.drill_dead_knob`), so the fire is caused by something this
#                  file put there. Deliberately NOT the shipped `pipeline.convergence`
#                  defect: a check that depends on a live defect goes RED the day the defect
#                  is fixed, which teaches the next reader to stop trusting it.
#   CLEAR fixture  the shipped board with the parked `pipeline.convergence` spec block
#                  removed FROM THE FIXTURE'S COPY ONLY. That is fixture construction, not
#                  concealment: you cannot ask "does a board that hits NO condition
#                  auto-pass?" without a board that hits none. The shipped config is never
#                  written, and the shipped board's real verdict is printed below as a
#                  labelled MEASUREMENT so no reader can mistake this drill's green for a
#                  claim that the shipped board is clear. It is not. It is raised, and step
#                  M says so with its exit code.
#
# The pair is the point. If the gate refused everything, CLEAR fails; if it passed
# everything, HALT fails. Neither direction can be green on its own.
#
# WHAT IS REAL AND WHAT IS NOT, stated up front because a local run described as a gym run
# is the over-claim PLAN section C.7 exists to prevent:
#   REAL - real git repositories, the real andon.ps1, the real queue.ps1, the real
#          gate-audit verifier and the real harness config loader. Every state transition is
#          produced by the shipped tools.
#   NOT  - this is not a run in `ai-orchestration-gym`. U6's mechanism is the HARNESS
#          pipeline, which has no gym scenario. The column's first word is therefore NOT
#          discharged here and this file does not claim it, exactly as
#          drill-dark-factory.ps1 does not.
#   NOT  - a statement about the shipped board. See step M.
#
# ISOLATION. Every write is to a scratch repository under $env:TEMP with
# AI_STACK_HARNESS_CONFIG and AI_STACK_WORKTREE_STATE redirected there: no real queue, no
# real ledger, no repository but its own. The one condition that could otherwise reach
# outside a fixture - `operator-checkout-off-branch` - is PINNED to the fixture repo by
# params.repo, and step B asserts from the ledger that the board the gate consulted was
# looking at a path under the scratch root. Step M is the single deliberate READ of this
# checkout: it evaluates the shipped board and prints the answer.

[CmdletBinding()]
param([switch]$Keep)

$ErrorActionPreference = "Stop"

$RepoRoot    = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$HarnessDir  = Join-Path $RepoRoot (Join-Path "scripts" "agent-harness")
$AndonPs     = Join-Path $HarnessDir "andon.ps1"
$QueuePs     = Join-Path $HarnessDir "queue.ps1"
$ShippedCfg  = Join-Path $HarnessDir "harness.config.json"
$PsExe       = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }

foreach ($needed in @($AndonPs, $QueuePs, $ShippedCfg)) {
    if (-not (Test-Path $needed)) {
        Write-Host "MISCONFIGURED: expected $needed - this drill must run from the repository it belongs to." -ForegroundColor Red
        exit 1
    }
}

$Root = Join-Path $env:TEMP ("u6dark-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Force -Path $Root | Out-Null
$CfgDir = Join-Path $Root "cfg"
New-Item -ItemType Directory -Force -Path $CfgDir | Out-Null

$script:Results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, [bool]$ok, [string]$detail = "") {
    $script:Results += [pscustomobject]@{ label = $label; pass = $ok; detail = $detail }
    if ($ok) { Write-Host ("  [PASS] " + $label + $(if ($detail) { " " + $detail } else { "" })) -ForegroundColor Green }
    else     { Write-Host ("  [FAIL] " + $label + $(if ($detail) { " " + $detail } else { "" })) -ForegroundColor Red }
}

function Invoke-GitAt([string]$repo, [string[]]$GitArgs) {
    # THE EXIT CODE IS CHECKED AT THE CALL SITE, and this drill found out why the hard way:
    # its own first version swallowed it, and step M's measurement of the SHIPPED board came
    # back naming this very file - `drill-u6-dark-gate.ps1:109 in Invoke-GitAt() runs git and
    # does not check the result within 5 line(s)`. A check that adds a finding to the board
    # it is measuring is not a neutral instrument. Throwing is right on its own merits too:
    # every git command here is setup, and setup that silently failed would be asserted over.
    Push-Location $repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $out = & git.exe @GitArgs 2>&1
        $code = $LASTEXITCODE
        if ($code -ne 0) { throw ("git " + ($GitArgs -join " ") + " exited " + $code + ": " + (@($out) -join "; ")) }
    } finally { $ErrorActionPreference = $prev; Pop-Location }
    return @($out | ForEach-Object { "$_" })
}

function New-ScratchRepo([string]$name) {
    # A real repository: the conditions ask git questions and a fake tree answers none of them.
    $p = Join-Path $Root $name
    New-Item -ItemType Directory -Force -Path $p | Out-Null
    Invoke-GitAt $p @("init", "-q", "-b", "main") | Out-Null
    Invoke-GitAt $p @("config", "user.email", "drill@example.invalid") | Out-Null
    Invoke-GitAt $p @("config", "user.name", "u6 dark gate drill") | Out-Null
    Set-Content -Path (Join-Path $p "README.md") -Encoding ascii -Value "scratch"
    # Fixture sources, so `git-error-swallowed` has something CLEAN to scan in this repo
    # rather than nothing at all - a detector handed an empty tree is not a passing detector.
    foreach ($d in @((Join-Path "scripts" "checks"), (Join-Path "scripts" "agent-harness"))) {
        $full = Join-Path $p $d
        New-Item -ItemType Directory -Force -Path $full | Out-Null
        Set-Content -Path (Join-Path $full "clean.ps1") -Encoding ascii -Value @(
            'function Get-CleanHead {',
            '    $out = & git.exe rev-parse HEAD',
            '    if ($LASTEXITCODE -ne 0) { throw "git failed" }',
            '    return $out',
            '}')
    }
    Invoke-GitAt $p @("add", "-A") | Out-Null
    Invoke-GitAt $p @("commit", "-q", "-m", "scratch base") | Out-Null
    Invoke-GitAt $p @("branch", "dev") | Out-Null
    Invoke-GitAt $p @("checkout", "-q", "-b", "work/u6drill") | Out-Null
    Set-Content -Path (Join-Path $p "DELIVERABLE.md") -Encoding ascii -Value "the work"
    Invoke-GitAt $p @("add", "DELIVERABLE.md") | Out-Null
    Invoke-GitAt $p @("commit", "-q", "-m", "the work") | Out-Null
    Invoke-GitAt $p @("checkout", "-q", "main") | Out-Null
    return $p
}

function Get-Cond($cfg, [string]$id) { return ($cfg.andon.conditions | Where-Object { $_.id -eq $id }) }

function New-Fixture([string]$name, [scriptblock]$editConfig) {
    $repo  = New-ScratchRepo ("repo-" + $name)
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null

    $o = Get-Content -Raw -Path $ShippedCfg | ConvertFrom-Json
    $o.pipeline.gate_profile = "dark"
    (Get-Cond $o "git-error-swallowed").params.globs = @("scripts/checks/*.ps1", "scripts/agent-harness/*.ps1")
    # PIN THE CHECKOUT CONDITION TO THIS FIXTURE. Left empty it resolves the operator's main
    # checkout, so the fixture's isolation would rest on every caller remembering to change
    # directory - and a detached operator checkout would turn this fixture red for reasons
    # that have nothing to do with the fixture.
    (Get-Cond $o "operator-checkout-off-branch").params.repo = $repo
    & $editConfig $o
    $cfg = Join-Path $CfgDir ($name + ".json")
    ($o | ConvertTo-Json -Depth 40) | Set-Content -Path $cfg -Encoding ASCII

    # The board needs a recorded baseline before `protected-ref-moved` can say anything but
    # "indeterminate", and an indeterminate condition is a raised board by design.
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
    $env:AI_STACK_HARNESS_CONFIG = $cfg; $env:AI_STACK_WORKTREE_STATE = $state
    try { & $PsExe -NoProfile -NonInteractive -File $AndonPs -Baseline -RepoRoot $repo | Out-Null }
    finally { $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState }

    return @{ repo = $repo; state = $state; cfg = $cfg; name = $name }
}

function Invoke-Andon($fix) {
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
    $env:AI_STACK_HARNESS_CONFIG = $fix.cfg; $env:AI_STACK_WORKTREE_STATE = $fix.state
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $out = & $PsExe -NoProfile -NonInteractive -File $AndonPs -Evaluate -Json -RepoRoot $fix.repo 2>$null
    } finally {
        $ErrorActionPreference = $prev
        $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState
    }
    $code = $LASTEXITCODE
    $text = ($out | Where-Object { $_ }) -join ""
    $v = $null
    if ($text) { try { $v = ConvertFrom-Json $text } catch { } }
    return @{ code = $code; verdict = $v }
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

function Get-Item2($fix, [string]$id) {
    $p = Join-Path (Join-Path $fix.state "queue") ($id + ".json")
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Raw -Path $p | ConvertFrom-Json)
}
function Get-LedgerPath($fix) { return (Join-Path (Join-Path $fix.state "audit") "gates.jsonl") }
function Get-Ledger($fix) {
    $p = Get-LedgerPath $fix
    if (-not (Test-Path $p)) { return @() }
    return @(Get-Content -Path $p | Where-Object { $_.Trim() } | ForEach-Object { ConvertFrom-Json $_ })
}
function Get-CondStatus($r, [string]$id) {
    if (-not $r.verdict) { return "(no verdict)" }
    $c = @($r.verdict.conditions | Where-Object { $_.id -eq $id })
    if ($c.Count -eq 0) { return "(not evaluated)" }
    return $c[0].status
}

# The anchor and test plan every queue item needs. Content is irrelevant to the andon
# question; what matters is that the gates are crossed by the real tool.
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
    "Case 1: DELIVERABLE.md exists on work/u6drill. Pass: it does. Fail: absent.",
    "Case 2: it holds one line. Pass: one. Fail: zero or contradictory lines.")
$evFile = Join-Path $Root "evidence.md"
Set-Content -Path $evFile -Encoding ascii -Value @("Case 1: file present. Case 2: one line. Both pass.")

# ====================================================================================
Step "A  HALT - an unattended run that HITS a condition halts and RAISES"
# ====================================================================================
# The fire is caused by a dead policy key THIS DRILL declares, so the assertion does not
# depend on any defect in the shipped config and does not go red when one is fixed.
$fixA = New-Fixture "halt" {
    param($c)
    # The parked `pipeline.convergence` block comes OUT of this fixture too, so the board's
    # only reason to fire is the key added on the next line. Measured 2026-09-02: left in,
    # the evidence read `pipeline.convergence, pipeline.drill_dead_knob` - the assertion
    # would have passed with the drill's own key contributing nothing, which is a green that
    # survives the removal of its subject.
    $c.pipeline.PSObject.Properties.Remove("convergence")
    $c.pipeline | Add-Member -NotePropertyName drill_dead_knob -NotePropertyValue 1 -Force
}
$r = Invoke-Andon $fixA
Check "the board RAISES on a declared key nothing reads (exit 6)" `
      (($r.code -eq 6) -and ("$($r.verdict.board)" -ne "clear")) ("exit=" + $r.code + " board=" + $r.verdict.board)
Check "and the fired condition is named" ((Get-CondStatus $r "policy-declared-unread") -eq "fire") `
      ("status=" + (Get-CondStatus $r "policy-declared-unread"))
$evA = @()
if ($r.verdict) { $evA = @(@($r.verdict.conditions | Where-Object { $_.id -eq "policy-declared-unread" })[0].evidence) }
Check "and the EVIDENCE names this drill's own key, and ONLY it - nothing inherited" `
      ((@($evA).Count -eq 1) -and ($evA -contains "pipeline.drill_dead_knob")) ($evA -join ", ")

$r = Invoke-Queue $fixA @("-Propose", "-Id", "u6a", "-Anchor", $anchorFile, "-Developer", "wt-u6drill")
Check "the item is proposed" (("$((Get-Item2 $fixA 'u6a').state)") -eq "anchor-draft") ("state=" + (Get-Item2 $fixA "u6a").state)

$r = Invoke-Queue $fixA @("-Submit", "-Id", "u6a", "-Branch", "work/u6drill", "-Developer", "wt-u6drill", "-TestPlan", $planFile)
$itA = Get-Item2 $fixA "u6a"
Check "HALT: the dark ANCHOR gate refuses to auto-pass (exit 6)" ($r.code -eq 6) ("exit=" + $r.code)
Check "HALT: the item stays PARKED at anchor-draft - a halt that advances the work is not a halt" `
      ("$($itA.state)" -eq "anchor-draft") ("state=" + $itA.state)
Check "RAISE: the halt names the condition on the console" ($r.out -like "*policy-declared-unread*") ""
$ledA = @(Get-Ledger $fixA)
$refusals = @($ledA | Where-Object { $_.decision -eq "refused" })
Check "RAISE: the refusal is IN THE LEDGER, not only on the console" (@($refusals).Count -ge 1) ("refusals=" + @($refusals).Count)
Check "RAISE: and the refusal record carries the fired condition" `
      ((@($refusals).Count -ge 1) -and ((@($refusals)[0].andon.fired -join ";") -like "*policy-declared-unread*")) `
      $(if (@($refusals).Count -ge 1) { "fired=" + (@($refusals)[0].andon.fired -join ";") } else { "no refusal record" })
Check "RAISE: no auto-pass record was written for a gate that refused" `
      ((@($ledA | Where-Object { $_.decision -eq "passed" }).Count) -eq 0) `
      ("passes=" + @($ledA | Where-Object { $_.decision -eq "passed" }).Count)

# ====================================================================================
Step "B  CLEAR - an unattended run that hits NONE lands, with a COMPLETE audit trail"
# ====================================================================================
# The fixture's copy of the config drops `pipeline.convergence` - the parked spec block the
# shipped board fires on. See the header: constructing a clear board is the only way to ask
# whether a clear board auto-passes, and step M keeps the shipped board's real verdict on
# the record so this construction cannot be mistaken for a claim about it.
$fixB = New-Fixture "clear" {
    param($c)
    $c.pipeline.PSObject.Properties.Remove("convergence")
}
$r = Invoke-Andon $fixB
Check "the constructed board is CLEAR (exit 0)" (($r.code -eq 0) -and ("$($r.verdict.board)" -eq "clear")) `
      ("exit=" + $r.code + " board=" + $r.verdict.board)
Check "and it is clear because every required condition LOOKED - 5 evaluated, 0 switched off" `
      (([int]$r.verdict.coverage.evaluated -eq 5) -and ([int]$r.verdict.coverage.declared -eq 5) -and ([int]$r.verdict.coverage.disabled -eq 0)) `
      ("evaluated=" + $r.verdict.coverage.evaluated + "/" + $r.verdict.coverage.declared + " disabled=" + $r.verdict.coverage.disabled)

$r = Invoke-Queue $fixB @("-Propose", "-Id", "u6b", "-Anchor", $anchorFile, "-Developer", "wt-u6drill")
Check "the item is proposed" (("$((Get-Item2 $fixB 'u6b').state)") -eq "anchor-draft") ("state=" + (Get-Item2 $fixB "u6b").state)

$r = Invoke-Queue $fixB @("-Submit", "-Id", "u6b", "-Branch", "work/u6drill", "-Developer", "wt-u6drill", "-TestPlan", $planFile)
$itB = Get-Item2 $fixB "u6b"
Check "the ANCHOR gate auto-passes with NO human (exit 0)" (($r.code -eq 0) -and ("$($itB.state)" -eq "ready-to-test")) `
      ("exit=" + $r.code + " state=" + $itB.state)
Check "the anchor pass is signed by the RESERVED auto principal, not a name" `
      ("$($itB.anchor_confirmed_by)" -eq "auto:dark") ("by=" + $itB.anchor_confirmed_by)

$r = Invoke-Queue $fixB @("-Claim", "-Id", "u6b", "-Role", "tester", "-By", "wt-tester")
Check "a tester who is not the developer may claim it" ($r.code -eq 0) ("exit=" + $r.code)
$r = Invoke-Queue $fixB @("-Pass", "-Id", "u6b", "-By", "wt-tester", "-Evidence", $evFile, "-PlanAdequate")
$itB = Get-Item2 $fixB "u6b"
Check "the PRE-REVIEW gate auto-passes with NO human (exit 0)" (($r.code -eq 0) -and ("$($itB.state)" -eq "ready-review")) `
      ("exit=" + $r.code + " state=" + $itB.state)
Check "the pre-review pass records kind 'auto' by the reserved principal" `
      (("$($itB.gates.pre_review.kind)" -eq "auto") -and ("$($itB.gates.pre_review.by)" -eq "auto:dark")) `
      ("kind=" + $itB.gates.pre_review.kind + " by=" + $itB.gates.pre_review.by)

$r = Invoke-Queue $fixB @("-Claim", "-Id", "u6b", "-Role", "reviewer", "-By", "wt-reviewer")
Check "a reviewer who is not the developer may claim it" ($r.code -eq 0) ("exit=" + $r.code)
Invoke-GitAt $fixB.repo @("checkout", "-q", "dev") | Out-Null
Invoke-GitAt $fixB.repo @("merge", "--no-ff", "-q", "-m", "merge work/u6drill", "work/u6drill") | Out-Null
# -join, not [0]: a one-line git result comes back from Invoke-GitAt as a bare STRING, and
# indexing a string yields a [char], which has no .Trim(). Measured on this drill's own
# first run.
$mergeSha = ((Invoke-GitAt $fixB.repo @("rev-parse", "HEAD")) -join "").Trim()
$r = Invoke-Queue $fixB @("-Merged", "-Id", "u6b", "-By", "wt-reviewer", "-Sha", $mergeSha, "-FitsCodebase")
$itB = Get-Item2 $fixB "u6b"
Check "THE RUN LANDS - state merged, with no human at either gate" ("$($itB.state)" -eq "merged") `
      ("state=" + $itB.state + " exit=" + $r.code)

$ledB = @(Get-Ledger $fixB)
$autoRecs = @($ledB | Where-Object { $_.kind -eq "auto" -and $_.decision -eq "passed" })
Check "the ledger holds exactly two gate events for this run" (@($ledB).Count -eq 2) ("count=" + @($ledB).Count)
Check "both are AUTO passes and each names its gate profile" `
      ((@($autoRecs).Count -eq 2) -and (@($autoRecs | Where-Object { $_.gate_profile -eq "dark" }).Count -eq 2)) `
      ("auto=" + @($autoRecs).Count)
Check "each auto pass records the andon verdict that AUTHORISED it" `
      ((@($autoRecs | Where-Object { $_.andon.status -eq "clear" }).Count) -eq 2) `
      (($autoRecs | ForEach-Object { $_.andon.status }) -join ",")
Check "each auto pass states its COVERAGE, so 'clear' can be re-derived rather than trusted" `
      ((@($autoRecs | Where-Object { ([int]$_.andon.evaluated -eq 5) -and ([int]$_.andon.conditions -eq 5) -and ([int]$_.andon.disabled -eq 0) }).Count) -eq 2) `
      (($autoRecs | ForEach-Object { "$($_.andon.evaluated)/$($_.andon.conditions)" }) -join ",")
$scratchOnly = @($autoRecs | Where-Object { (Resolve-Path -LiteralPath "$($_.andon.repo)" -ErrorAction SilentlyContinue).Path -like ((Resolve-Path -LiteralPath $Root).Path + "*") })
Check "HERMETIC: the board the gate consulted was looking at the SCRATCH repo, not the operator's" `
      (@($scratchOnly).Count -eq 2) (($autoRecs | ForEach-Object { $_.andon.repo }) -join " | ")
Check "no record can be mistaken for a human approval" `
      ((@($ledB | Where-Object { $_.kind -eq "human" }).Count -eq 0) -and (@($ledB | Where-Object { "$($_.principal)".StartsWith("auto:") }).Count -eq 2)) ""

$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "u6b")
Check "THE COLUMN'S SECOND HALF: the audit trail verifies COMPLETE (exit 0)" ($r.code -eq 0) `
      ("exit=" + $r.code + " :: " + ($r.out -split "`n" | Select-Object -Last 1))

# ====================================================================================
Step "C  'COMPLETE' has TEETH - a tampered trail must go red"
# ====================================================================================
# Without this, step B's last assertion is satisfiable by a verifier that returns 0 for
# everything - which is the shape this whole item exists to refuse.
$ledgerPath = Get-LedgerPath $fixB
$ledgerBak = Get-Content -Path $ledgerPath

Set-Content -Path $ledgerPath -Encoding ASCII -Value @($ledgerBak | ForEach-Object { $_ -replace '"kind":"auto"', '"kind":"human"' })
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "u6b")
Check "RED: an auto pass relabelled 'human' is caught (exit 1)" ($r.code -eq 1) ("exit=" + $r.code)

Set-Content -Path $ledgerPath -Encoding ASCII -Value @($ledgerBak | Select-Object -First 1)
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "u6b")
Check "RED: a MISSING record for a crossed gate is caught (exit 1)" ($r.code -eq 1) ("exit=" + $r.code)

Set-Content -Path $ledgerPath -Encoding ASCII -Value @($ledgerBak | ForEach-Object { $_ -replace '"status":"clear"', '"status":"raised"' })
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "u6b")
Check "RED: an auto pass taken while the board was RAISED is caught (exit 1)" ($r.code -eq 1) ("exit=" + $r.code)

Set-Content -Path $ledgerPath -Encoding ASCII -Value $ledgerBak
$r = Invoke-Queue $fixB @("-VerifyAudit", "-Id", "u6b")
Check "GREEN again once the ledger is restored - the verifier is not stuck red" ($r.code -eq 0) ("exit=" + $r.code)

# ====================================================================================
Step "M  MEASUREMENT (not an assertion): what the SHIPPED board says in this checkout"
# ====================================================================================
# Printed, never asserted. Step B constructs a clear board; this is the honest note beside
# it saying that the board this repository actually ships is a different question with a
# different answer, and what that answer is today.
# The CONFIG is the shipped one - that is the whole point of this step - but the STATE dir
# is still redirected to the scratch root, so evaluating the board cannot create the audit
# directory inside this checkout's `.git`. A measurement that writes to the tree it is
# measuring is not a measurement.
$prevCfg = $env:AI_STACK_HARNESS_CONFIG; $prevState = $env:AI_STACK_WORKTREE_STATE
$env:AI_STACK_HARNESS_CONFIG = $null
$env:AI_STACK_WORKTREE_STATE = Join-Path $Root "state-measurement"
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
try { $shippedOut = & $PsExe -NoProfile -NonInteractive -File $AndonPs -Evaluate -RepoRoot $RepoRoot 2>&1 }
finally { $ErrorActionPreference = $prev; $env:AI_STACK_HARNESS_CONFIG = $prevCfg; $env:AI_STACK_WORKTREE_STATE = $prevState }
$shippedCode = $LASTEXITCODE
Write-Host ("  shipped board: exit " + $shippedCode) -ForegroundColor Yellow
foreach ($line in @($shippedOut | ForEach-Object { "$_" })) { Write-Host ("    " + $line) -ForegroundColor DarkYellow }
Write-Host "  This is a MEASUREMENT. It is not counted in the result below, because the" -ForegroundColor Yellow
Write-Host "  column's question is about the GATE and the shipped board's contents are a" -ForegroundColor Yellow
Write-Host "  separate, live config decision - see this file's header." -ForegroundColor Yellow

# ====================================================================================
$failed = @($script:Results | Where-Object { -not $_.pass })
Write-Host ""
if (@($failed).Count -gt 0) {
    Write-Host ("U6 DARK GATE DRILL: " + @($failed).Count + " of " + @($script:Results).Count + " assertions FAILED") -ForegroundColor Red
    foreach ($f in $failed) { Write-Host ("  FAILED: " + $f.label + " " + $f.detail) -ForegroundColor Red }
} else {
    Write-Host ("U6 DARK GATE DRILL: " + @($script:Results).Count + " assertions, 0 failed.") -ForegroundColor Green
    Write-Host "  HALT and CLEAR were both driven through the REAL gate: a board that fires" -ForegroundColor Green
    Write-Host "  refuses the unattended gate and records the refusal; a board that fires" -ForegroundColor Green
    Write-Host "  nothing auto-passes both gates and the trail verifies COMPLETE. Neither" -ForegroundColor Green
    Write-Host "  direction can be green on its own." -ForegroundColor Green
}

if ($Keep) { Write-Host ("scratch KEPT: " + $Root) }
else {
    try { Remove-Item -Recurse -Force -LiteralPath $Root -ErrorAction Stop } catch { }
    if (Test-Path $Root) {
        # git's object files are read-only on Windows; a cleanup that only works on a tree
        # nobody wrote is not a cleanup.
        Get-ChildItem -Recurse -Force -LiteralPath $Root | ForEach-Object { try { $_.Attributes = "Normal" } catch { } }
        try { Remove-Item -Recurse -Force -LiteralPath $Root -ErrorAction Stop } catch { }
    }
}

if (@($failed).Count -gt 0) { exit 1 }
exit 0
