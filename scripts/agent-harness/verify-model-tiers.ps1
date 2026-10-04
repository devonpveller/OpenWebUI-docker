# verify-model-tiers.ps1 - executable proof for the ADVISORY model-tier recommendation
# queue.ps1 prints (item `mt-policy`, tracker H2).
#
#   .\scripts\agent-harness\verify-model-tiers.ps1                    # the shipped queue.ps1
#   .\scripts\agent-harness\verify-model-tiers.ps1 -Script <path>     # some OTHER copy
#
# -Script exists for the same reason as in verify-queue-defects.ps1: point it at the copy you
# edited to see GREEN, and at a copy from before the change (git archive of the base commit)
# to see every check below go RED. A check that cannot go red proves nothing.
#
# HERMETIC. Every case builds its own scratch git repository and its own state directory
# under $env:TEMP and points AI_STACK_WORKTREE_STATE at it, so nothing here can see the real
# queue. No docker, no network, no live plane.
#
# WHAT IS PROVEN (each a numbered case in the mt-policy test plan):
#   M1  -Propose with no tier: the item records tier "" and the developer advice is the
#       documented default (large -> opus | local-large), saying the default was used.
#   M2  -Propose with tier small: recorded; developer advice small (sonnet | local-small).
#   M3  An invalid tier ("medium", and "Large" - the check is case-sensitive) is REFUSED at
#       -Propose with a message naming the allowed values; no item is created.
#   M4  -Submit of a CODE change prints tester advice: attempt 1 = large by the
#       first-adversarial-round rule even for a small item.
#   M5  -Submit of a DOC-ONLY change prints small by the doc-only rule (doc-only yes).
#   M6  -Claim prints advice for the claimed role; a reviewer is large (review-whole-change)
#       even on a small, doc-only item. A legacy item with NO tier field gets the default.
#   M7  -Fail then -Resubmit with a small diff: attempt 2 tester = small (retest-small-diff),
#       with the delta printed.
#   M8  -Resubmit with a big diff on a large item: attempt 2 tester = large (item-tier).
#   M9  -Approve prints reviewer advice: large (review-whole-change).
#   M10 ADVISORY, NEVER BLOCKING: a broken model_tiers block (an unknown rule key) prints
#       "unavailable" and -Propose still exits 0 with the item created.
#   M11 A tier the schema allows but the CONFIG does not (model_tiers.tiers narrowed) is
#       refused at -Propose with the config's own words.
# ADDED attempt 2 (the tester's findings on attempt 1):
#   M12 model_tiers block DELETED: -Propose of the shipped anchor.template.json ("tier": "large")
#       and of a tier-small anchor exits 0, creates the item with its tier, prints "unavailable".
#       (Attempt 1 exited 1 here - a broken policy block refused a schema-valid anchor.)
#   M13 a NON-STRING tier (["small"], 1) is refused at -Propose by its JSON type; no item.
#   M14 a BINARY change after a failure is an UNKNOWN delta (not 0 lines): no retest-small-diff.
#   M15 a rename big.py -> big.md is NOT doc-only (both paths are classified).
#   M16 a pure RENAME after a failure counts its content delta (0), so the retest is small.
# ADDED attempt 3:
#   M17 a SUBMODULE (gitlink) bump after a failure is an UNKNOWN delta (numstat says 2 lines).
#   M18 an anchor tier with an IGNORABLE character ("sm<U+00AD>all", "small<U+0000>") is refused at
#       -Propose - PowerShell's culture-aware comparison used to read it as "small".

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
if (-not $Root) { $Root = Join-Path $env:TEMP ("model-tiers-" + $PID) }
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
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & git.exe @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed: " + ($out -join "`n")) }
    return @($out)
}

function New-Fixture([string]$name) {
    # A line (`base`) and two work branches: work/code changes a script AND a doc, work/doc
    # changes only a doc - the two shapes the doc-only rule has to tell apart.
    $repo = Join-Path $Root $name
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    Push-Location $repo
    try {
        Invoke-Git init -q -b base | Out-Null
        Invoke-Git config user.email "mtdrill@example.invalid" | Out-Null
        Invoke-Git config user.name "model tier drill" | Out-Null
        Invoke-Git config core.autocrlf false | Out-Null
        Set-Content -Path (Join-Path $repo "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git commit -q -m "scratch base" | Out-Null
        Invoke-Git checkout -q -b work/code | Out-Null
        Set-Content -Path (Join-Path $repo "tool.ps1") -Encoding ascii -Value 'Write-Output "new logic"'
        Set-Content -Path (Join-Path $repo "WORK.md") -Encoding ascii -Value "the work"
        Invoke-Git add tool.ps1 WORK.md | Out-Null
        Invoke-Git commit -q -m "code work" | Out-Null
        Invoke-Git checkout -q base | Out-Null
        Invoke-Git checkout -q -b work/doc | Out-Null
        New-Item -ItemType Directory -Force -Path (Join-Path $repo "docs") | Out-Null
        Set-Content -Path (Join-Path $repo "docs\GUIDE.md") -Encoding ascii -Value "a guide"
        Invoke-Git add docs/GUIDE.md | Out-Null
        Invoke-Git commit -q -m "doc work" | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location }
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return @{ repo = $repo; state = $state; name = $name; config = "" }
}

function Add-Commit($fix, [string]$branch, [int]$lines) {
    # Move a work branch forward by exactly $lines added lines (the delta the retest rule reads).
    Push-Location $fix.repo
    try {
        Invoke-Git checkout -q $branch | Out-Null
        $body = @(1..$lines | ForEach-Object { "fix line $_" })
        Add-Content -Path (Join-Path $fix.repo "tool.ps1") -Encoding ascii -Value $body
        Invoke-Git add tool.ps1 | Out-Null
        Invoke-Git commit -q -m ("fix: {0} line(s)" -f $lines) | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location }
}

function Invoke-Q($fix, [string[]]$QArgs) {
    $prevState = $env:AI_STACK_WORKTREE_STATE; $prevLine = $env:AI_STACK_WORK_LINE
    $prevCfg = $env:AI_STACK_HARNESS_CONFIG
    $env:AI_STACK_WORKTREE_STATE = $fix.state
    $env:AI_STACK_WORK_LINE = "base"
    if ($fix.config) { $env:AI_STACK_HARNESS_CONFIG = $fix.config } else { Remove-Item Env:AI_STACK_HARNESS_CONFIG -ErrorAction SilentlyContinue }
    Push-Location $fix.repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -File $Script @QArgs 2>&1 }
    finally {
        $ErrorActionPreference = $prev
        Pop-Location
        $env:AI_STACK_WORKTREE_STATE = $prevState; $env:AI_STACK_WORK_LINE = $prevLine
        if ($null -ne $prevCfg) { $env:AI_STACK_HARNESS_CONFIG = $prevCfg } else { Remove-Item Env:AI_STACK_HARNESS_CONFIG -ErrorAction SilentlyContinue }
    }
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { "$_" }) -join "`n") }
}

function Get-QItem($fix, [string]$id) {
    $p = Join-Path $fix.state "queue\$id.json"
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json)
}

function Get-Advice([string]$out) {
    # The MODEL block as printed: the headline, the two-map line, and the facts line.
    $l = @($out -split "`n" | Where-Object { $_ -match 'MODEL \(advisory\)|cloud \(Claude Code subagent\)|facts:' })
    return ($l -join " / ")
}

function First-Line([string]$s) {
    $l = @($s -split "`n" | Where-Object { $_.Trim() })
    if ($l.Count -eq 0) { return "(no output)" }
    return $l[0].Trim()
}

function New-Anchor([string]$name, [string]$tierLine) {
    $p = Join-Path $Root ("anchor-" + $name + ".json")
    $lines = @(
        '{',
        '  "goal": "tool.ps1 does the one thing the drill asks of it.",',
        '  "artifact": "tool.ps1 - a one-line script produced by the drill fixture.",',
        '  "audience": "The next agent to read the fixture with no other context.",',
        '  "acceptance": ["tool.ps1 exists on the branch. Fail: it is absent."],',
        '  "out_of_scope": ["Anything outside the fixture."],')
    if ($tierLine) { $lines += ('  "tier": "' + $tierLine + '",') }
    $lines += @('  "findings_sink": "../documentation-plans-ai-stack/journal/notes/model-tier-drill.md"', '}')
    Set-Content -Path $p -Encoding ascii -Value $lines
    return $p
}

$plan = Join-Path $Root "plan.md"
Set-Content -Path $plan -Encoding ascii -Value @("# Test plan", "## Case 1 - tool.ps1 exists. Fail: it is absent.")
$evFail = Join-Path $Root "evidence-fail.md"
Set-Content -Path $evFail -Encoding ascii -Value @("# evidence", "## Case 1 - tool.ps1 exists   FAIL", "it printed the wrong thing")
$evPass = Join-Path $Root "evidence-pass.md"
Set-Content -Path $evPass -Encoding ascii -Value @("# evidence", "## Case 1 - tool.ps1 exists   PASS")

$anchorNone = New-Anchor "none" ""
$anchorSmall = New-Anchor "small" "small"
$anchorLarge = New-Anchor "large" "large"
$anchorMedium = New-Anchor "medium" "medium"
$anchorCase = New-Anchor "case" "Large"

function Initialize-Submitted($fix, [string]$id, [string]$anchor, [string]$branch) {
    Invoke-Q $fix @("-Propose", "-Id", $id, "-Anchor", $anchor, "-Developer", "mtdev") | Out-Null
    Invoke-Q $fix @("-ConfirmAnchor", "-Id", $id, "-By", "mtoperator") | Out-Null
    return (Invoke-Q $fix @("-Submit", "-Id", $id, "-Branch", $branch, "-Developer", "mtdev", "-TestPlan", $plan))
}

# ======================================================================================
Step "M1  -Propose with no tier -> recorded empty, developer advice is the documented default"
# ======================================================================================
$f = New-Fixture "m1"
$r = Invoke-Q $f @("-Propose", "-Id", "m1", "-Anchor", $anchorNone, "-Developer", "mtdev")
$a = Get-Advice $r.out
$it = Get-QItem $f "m1"
Check "M1 -Propose exits 0" ($r.code -eq 0) ("exit=" + $r.code)
Check "M1 the item carries a tier field, empty (= default)" (($null -ne $it) -and ($it.PSObject.Properties.Name -contains "tier") -and ("$($it.tier)" -eq "")) ("tier='" + $(if ($it) { $it.tier }) + "'")
Check "M1 advice: developer, attempt 1, tier large by item-tier" ($a -match 'next role developer, attempt 1 -> tier large \[rule item-tier; item tier large\]') $a
Check "M1 advice names BOTH maps: cloud opus, local local-large" ($a -match 'cloud \(Claude Code subagent\): opus\s+\|\s+local \(agent-org model role\): local-large') $a
Check "M1 advice says the default was used" ($a -match 'none set -> model_tiers\.default_tier') $a

# ======================================================================================
Step "M2  -Propose with tier small -> recorded, developer advice small"
# ======================================================================================
$f = New-Fixture "m2"
$r = Invoke-Q $f @("-Propose", "-Id", "m2", "-Anchor", $anchorSmall, "-Developer", "mtdev")
$a = Get-Advice $r.out
$it = Get-QItem $f "m2"
Check "M2 -Propose exits 0 and records tier small" (($r.code -eq 0) -and $it -and ($it.tier -eq "small")) ("exit=" + $r.code)
Check "M2 advice: developer small -> sonnet | local-small" ($a -match 'tier small \[rule item-tier; item tier small\]' -and $a -match 'cloud \(Claude Code subagent\): sonnet\s+\|\s+local \(agent-org model role\): local-small') $a
Check "M2 -Propose renders the tier in the anchor" ($r.out -match 'TIER\s+: small') ""

# ======================================================================================
Step "M3  an invalid tier is refused at -Propose, with the allowed values named"
# ======================================================================================
$f = New-Fixture "m3"
$r = Invoke-Q $f @("-Propose", "-Id", "m3", "-Anchor", $anchorMedium, "-Developer", "mtdev")
Check "M3 tier 'medium' -> non-zero exit" ($r.code -ne 0) ("exit=" + $r.code)
Check "M3 the message names the field, the allowed values and the bad value" ($r.out -match "'tier' must be one of: large, small \(got 'medium'\)") ((@($r.out -split "`n") | Select-String "tier" | Select-Object -First 1) -as [string])
Check "M3 no item was created" ($null -eq (Get-QItem $f "m3")) ""
$r = Invoke-Q $f @("-Propose", "-Id", "m3b", "-Anchor", $anchorCase, "-Developer", "mtdev")
Check "M3 tier 'Large' (wrong case) is refused too" (($r.code -ne 0) -and ($r.out -match "got 'Large'") -and ($null -eq (Get-QItem $f "m3b"))) ("exit=" + $r.code)

# ======================================================================================
Step "M4  -Submit of a code change: attempt-1 tester is large (first-adversarial-round)"
# ======================================================================================
$f = New-Fixture "m4"
$r = Initialize-Submitted $f "m4" $anchorSmall "work/code"
$a = Get-Advice $r.out
Check "M4 -Submit exits 0" ($r.code -eq 0) ("exit=" + $r.code)
Check "M4 advice: tester attempt 1 -> large by first-adversarial-round, on a SMALL item" ($a -match 'next role tester, attempt 1 -> tier large \[rule first-adversarial-round; item tier small\]') $a
Check "M4 advice: cloud opus | local local-large; doc-only no" (($a -match 'cloud \(Claude Code subagent\): opus\s+\|\s+local \(agent-org model role\): local-large') -and ($a -match 'doc-only no')) $a

# ======================================================================================
Step "M5  -Submit of a doc-only change: tester is small (doc-only)"
# ======================================================================================
$f = New-Fixture "m5"
$r = Initialize-Submitted $f "m5" $anchorNone "work/doc"
$a = Get-Advice $r.out
Check "M5 advice: tester attempt 1 -> small by doc-only, on a default (large) item" ($a -match 'next role tester, attempt 1 -> tier small \[rule doc-only; item tier large\]') $a
Check "M5 advice: sonnet | local-small; doc-only yes" (($a -match 'cloud \(Claude Code subagent\): sonnet\s+\|\s+local \(agent-org model role\): local-small') -and ($a -match 'doc-only yes')) $a

# ======================================================================================
Step "M6  -Claim prints advice for the claimed role; legacy item without a tier field"
# ======================================================================================
$r = Invoke-Q $f @("-Claim", "-Id", "m5", "-Role", "tester", "-By", "mttester")
$a = Get-Advice $r.out
Check "M6 -Claim tester prints the same doc-only advice" (($r.code -eq 0) -and ($a -match 'next role tester, attempt 1 -> tier small \[rule doc-only')) $a
Invoke-Q $f @("-Pass", "-Id", "m5", "-By", "mttester", "-Evidence", $evPass, "-PlanAdequate") | Out-Null
Invoke-Q $f @("-Approve", "-Id", "m5", "-By", "mtoperator") | Out-Null
$r = Invoke-Q $f @("-Claim", "-Id", "m5", "-Role", "reviewer", "-By", "mtreviewer")
$a = Get-Advice $r.out
Check "M6 -Claim reviewer on a DOC-ONLY item is still large (review-whole-change)" (($r.code -eq 0) -and ($a -match 'next role reviewer, attempt 1 -> tier large \[rule review-whole-change') -and ($a -match ': opus\s+\|.*: local-large')) $a
# A LEGACY item - written before the field existed - has no `tier` property at all.
$fl = New-Fixture "m6legacy"
Invoke-Q $fl @("-Propose", "-Id", "m6", "-Anchor", $anchorSmall, "-Developer", "mtdev") | Out-Null
Invoke-Q $fl @("-ConfirmAnchor", "-Id", "m6", "-By", "mtoperator") | Out-Null
Invoke-Q $fl @("-Submit", "-Id", "m6", "-Branch", "work/code", "-Developer", "mtdev", "-TestPlan", $plan) | Out-Null
$p = Join-Path $fl.state "queue\m6.json"
$raw = Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json
$raw.PSObject.Properties.Remove("tier")
[System.IO.File]::WriteAllText($p, ($raw | ConvertTo-Json -Depth 8), (New-Object System.Text.UTF8Encoding($false)))
Check "M6 setup: the legacy item has no tier field" (-not ((Get-QItem $fl "m6").PSObject.Properties.Name -contains "tier")) ""
$r = Invoke-Q $fl @("-Claim", "-Id", "m6", "-Role", "tester", "-By", "mttester")
$a = Get-Advice $r.out
Check "M6 legacy item: claim succeeds and the default tier is used" (($r.code -eq 0) -and ($a -match 'item tier large\]') -and ($a -match 'none set -> model_tiers\.default_tier')) $a

# ======================================================================================
Step "M7  -Fail then -Resubmit with a SMALL diff: attempt-2 tester is small (retest-small-diff)"
# ======================================================================================
$f = New-Fixture "m7"
Initialize-Submitted $f "m7" $anchorLarge "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m7", "-Role", "tester", "-By", "mttester") | Out-Null
$r = Invoke-Q $f @("-Fail", "-Id", "m7", "-By", "mttester", "-Reason", "case 1", "-Evidence", $evFail, "-PlanAdequate")
Check "M7 setup: the item failed" ((Get-QItem $f "m7").state -eq "test-failed") ("exit=" + $r.code)
Add-Commit $f "work/code" 3
$r = Invoke-Q $f @("-Resubmit", "-Id", "m7", "-By", "mtdev")
$a = Get-Advice $r.out
Check "M7 -Resubmit exits 0" ($r.code -eq 0) ("exit=" + $r.code)
Check "M7 advice: tester attempt 2 -> small by retest-small-diff on a LARGE item" ($a -match 'next role tester, attempt 2 -> tier small \[rule retest-small-diff; item tier large\]') $a
Check "M7 advice prints the measured delta (3 lines) and sonnet | local-small" (($a -match 'lines changed since the last verdict 3') -and ($a -match ': sonnet\s+\|.*: local-small')) $a
$r = Invoke-Q $f @("-Claim", "-Id", "m7", "-Role", "tester", "-By", "mttester2")
Check "M7 -Claim of the attempt-2 retest agrees" ((Get-Advice $r.out) -match 'attempt 2 -> tier small \[rule retest-small-diff') (Get-Advice $r.out)

# ======================================================================================
Step "M8  -Resubmit with a BIG diff on a large item: attempt-2 tester stays large (item-tier)"
# ======================================================================================
$f = New-Fixture "m8"
Initialize-Submitted $f "m8" $anchorLarge "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m8", "-Role", "tester", "-By", "mttester") | Out-Null
Invoke-Q $f @("-Fail", "-Id", "m8", "-By", "mttester", "-Reason", "case 1", "-Evidence", $evFail, "-PlanAdequate") | Out-Null
Add-Commit $f "work/code" 120
$r = Invoke-Q $f @("-Resubmit", "-Id", "m8", "-By", "mtdev")
$a = Get-Advice $r.out
Check "M8 advice: tester attempt 2 -> large by item-tier (delta 120 > 80)" (($a -match 'next role tester, attempt 2 -> tier large \[rule item-tier; item tier large\]') -and ($a -match 'lines changed since the last verdict 120')) $a

# ======================================================================================
Step "M9  -Approve prints reviewer advice: large (review-whole-change)"
# ======================================================================================
$f = New-Fixture "m9"
Initialize-Submitted $f "m9" $anchorSmall "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m9", "-Role", "tester", "-By", "mttester") | Out-Null
Invoke-Q $f @("-Pass", "-Id", "m9", "-By", "mttester", "-Evidence", $evPass, "-PlanAdequate") | Out-Null
$r = Invoke-Q $f @("-Approve", "-Id", "m9", "-By", "mtoperator")
$a = Get-Advice $r.out
Check "M9 -Approve exits 0" ($r.code -eq 0) ("exit=" + $r.code)
Check "M9 advice: reviewer -> large by review-whole-change on a SMALL item, opus | local-large" (($a -match 'next role reviewer, attempt 1 -> tier large \[rule review-whole-change; item tier small\]') -and ($a -match ': opus\s+\|.*: local-large')) $a

# ======================================================================================
Step "M10 a broken model_tiers block is reported 'unavailable' and NEVER blocks"
# ======================================================================================
$shipped = Join-Path (Split-Path -Parent $Script) "harness.config.json"
$cfg = Get-Content -Raw -Path $shipped | ConvertFrom-Json
$hasTiers = ($cfg.PSObject.Properties.Name -contains "model_tiers")
if ($hasTiers) { $cfg.model_tiers.rules[0] | Add-Member -NotePropertyName "max_attemp" -NotePropertyValue 1 }
$broken = Join-Path $Root "broken.config.json"
[System.IO.File]::WriteAllText($broken, ($cfg | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding($false)))
$f = New-Fixture "m10"
$f.config = $broken
$r = Invoke-Q $f @("-Propose", "-Id", "m10", "-Anchor", $anchorNone, "-Developer", "mtdev")
Check "M10 setup: the copied config HAS a model_tiers block to break" $hasTiers ""
Check "M10 -Propose still exits 0 and the item exists" (($r.code -eq 0) -and ($null -ne (Get-QItem $f "m10"))) ("exit=" + $r.code)
Check "M10 the advice says unavailable and names the unknown key" ($r.out -match "MODEL \(advisory\): unavailable - model_tiers rule 'review-whole-change' has an unknown key 'max_attemp'") (Get-Advice $r.out)

# ======================================================================================
Step "M11 a tier the schema allows but the CONFIG does not is refused at -Propose"
# ======================================================================================
$cfg = Get-Content -Raw -Path $shipped | ConvertFrom-Json
if ($hasTiers) {
    # A CONSISTENT narrowing (attempt 2): rules that named `small` now name `large`, so the block
    # is usable and it is the config - not a broken block - that refuses the anchor's tier.
    $cfg.model_tiers.tiers = @("large")
    foreach ($rule in $cfg.model_tiers.rules) { if ($rule.tier -ceq "small") { $rule.tier = "large" } }
    # attempt 3: every key of a role map must be a configured tier, so `small` leaves the maps too.
    $cfg.model_tiers.cloud.roles.PSObject.Properties.Remove("small")
    $cfg.model_tiers.local.roles.PSObject.Properties.Remove("small")
}
$narrow = Join-Path $Root "narrow.config.json"
[System.IO.File]::WriteAllText($narrow, ($cfg | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding($false)))
$f = New-Fixture "m11"
$f.config = $narrow
$r = Invoke-Q $f @("-Propose", "-Id", "m11", "-Anchor", $anchorSmall, "-Developer", "mtdev")
Check "M11 refused (exit 1), naming the config's known tiers, no item" (($r.code -eq 1) -and ($r.out -match "unknown tier 'small' - known tiers: large") -and ($null -eq (Get-QItem $f "m11"))) ("exit=" + $r.code)

# ======================================================================================
Step "M12 model_tiers DELETED: a templated anchor still proposes; the advice says unavailable"
# ======================================================================================
$cfg = Get-Content -Raw -Path $shipped | ConvertFrom-Json
if ($hasTiers) { $cfg.PSObject.Properties.Remove("model_tiers") }
$nomt = Join-Path $Root "nomt.config.json"
[System.IO.File]::WriteAllText($nomt, ($cfg | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding($false)))
$f = New-Fixture "m12"
$f.config = $nomt
$template = Join-Path (Split-Path -Parent $Script) "anchor.template.json"
$r = Invoke-Q $f @("-Propose", "-Id", "m12t", "-Anchor", $template, "-Developer", "mtdev")
$it = Get-QItem $f "m12t"
Check "M12 the TEMPLATE anchor (tier large) proposes: exit 0, item created, tier recorded" (($r.code -eq 0) -and $it -and ($it.tier -eq "large")) ("exit=" + $r.code + " " + (First-Line $r.out))
Check "M12 ... and the advice says unavailable, naming the missing block" ($r.out -match "MODEL \(advisory\): unavailable - harness.config.json has no model_tiers block") (Get-Advice $r.out)
$r = Invoke-Q $f @("-Propose", "-Id", "m12s", "-Anchor", $anchorSmall, "-Developer", "mtdev")
Check "M12 a tier-small anchor proposes too (exit 0, tier small)" (($r.code -eq 0) -and ((Get-QItem $f "m12s").tier -eq "small")) ("exit=" + $r.code)
Check "M12 no contradictory 'unknown tier' refusal anywhere" (-not ($r.out -match "unknown tier")) ""

# ======================================================================================
Step "M13 a non-string tier is refused at -Propose by its JSON type"
# ======================================================================================
$f = New-Fixture "m13"
foreach ($shape in @(@{ n = "array"; v = '["small"]' }, @{ n = "number"; v = '1' })) {
    $p = Join-Path $Root ("anchor-shape-" + $shape.n + ".json")
    (Get-Content -Raw $anchorNone) -replace '"findings_sink"', ('"tier": ' + $shape.v + ', "findings_sink"') |
        Set-Content -Path $p -Encoding ascii
    $id = "m13" + $shape.n
    $r = Invoke-Q $f @("-Propose", "-Id", $id, "-Anchor", $p, "-Developer", "mtdev")
    Check ("M13 tier " + $shape.v + " refused (exit 1), named by JSON type, no item") `
        (($r.code -eq 1) -and ($r.out -match ("'tier' must be a string, one of: large, small \(got JSON type " + $shape.n + "\)")) -and ($null -eq (Get-QItem $f $id))) ("exit=" + $r.code)
}

function First-Commit($fix, [string]$branch, [scriptblock]$change, [string]$msg) {
    Push-Location $fix.repo
    try {
        Invoke-Git checkout -q $branch | Out-Null
        & $change
        Invoke-Git add -A | Out-Null
        Invoke-Git commit -q -m $msg | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location }
}

# ======================================================================================
Step "M14 a BINARY change after a failure is an UNKNOWN delta, not 0 lines"
# ======================================================================================
$f = New-Fixture "m14"
Initialize-Submitted $f "m14" $anchorLarge "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m14", "-Role", "tester", "-By", "mttester") | Out-Null
Invoke-Q $f @("-Fail", "-Id", "m14", "-By", "mttester", "-Reason", "case 1", "-Evidence", $evFail, "-PlanAdequate") | Out-Null
First-Commit $f "work/code" { $b = New-Object byte[] 4096; (New-Object System.Random 7).NextBytes($b); $b[0] = 0; [System.IO.File]::WriteAllBytes((Join-Path $f.repo "blob.bin"), $b) } "add a binary"
$r = Invoke-Q $f @("-Resubmit", "-Id", "m14", "-By", "mtdev")
$a = Get-Advice $r.out
Check "M14 binary delta is unknown -> item-tier large, not retest-small-diff" (($a -match 'attempt 2 -> tier large \[rule item-tier') -and ($a -match 'lines changed since the last verdict unknown')) $a

# ======================================================================================
Step "M15 big.py -> big.md is NOT a doc-only change"
# ======================================================================================
$f = New-Fixture "m15"
Push-Location $f.repo
try {
    Invoke-Git checkout -q base | Out-Null
    Set-Content -Path (Join-Path $f.repo "big.py") -Encoding ascii -Value @(1..30 | ForEach-Object { "x = $_" })
    Invoke-Git add big.py | Out-Null
    Invoke-Git commit -q -m "base code" | Out-Null
    Invoke-Git checkout -q -b work/rename | Out-Null
    Invoke-Git mv big.py big.md | Out-Null
    Invoke-Git commit -q -m "code moved into a doc name" | Out-Null
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
$r = Initialize-Submitted $f "m15" $anchorNone "work/rename"
$a = Get-Advice $r.out
Check "M15 doc-only no -> first-adversarial-round large" (($a -match 'doc-only no') -and ($a -match 'tier large \[rule first-adversarial-round')) $a

# ======================================================================================
Step "M16 a pure RENAME after a failure counts its content delta (0) -> retest small"
# ======================================================================================
$f = New-Fixture "m16"
Initialize-Submitted $f "m16" $anchorLarge "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m16", "-Role", "tester", "-By", "mttester") | Out-Null
Invoke-Q $f @("-Fail", "-Id", "m16", "-By", "mttester", "-Reason", "case 1", "-Evidence", $evFail, "-PlanAdequate") | Out-Null
Push-Location $f.repo
try {
    Invoke-Git checkout -q work/code | Out-Null
    Invoke-Git mv tool.ps1 renamed-tool.ps1 | Out-Null
    Invoke-Git commit -q -m "rename only" | Out-Null
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
$r = Invoke-Q $f @("-Resubmit", "-Id", "m16", "-By", "mtdev")
$a = Get-Advice $r.out
Check "M16 rename delta 0 -> retest-small-diff small" (($a -match 'attempt 2 -> tier small \[rule retest-small-diff') -and ($a -match 'lines changed since the last verdict 0')) $a

# ======================================================================================
Step "M17 a SUBMODULE (gitlink) bump after a failure is an UNKNOWN delta, not 2 lines"
# ======================================================================================
$f = New-Fixture "m17"
Push-Location $f.repo
try {
    $shaA = (Invoke-Git rev-parse base | Select-Object -First 1).Trim()
    $shaB = (Invoke-Git rev-parse work/doc | Select-Object -First 1).Trim()
    Invoke-Git checkout -q work/code | Out-Null
    Invoke-Git update-index --add --cacheinfo ("160000," + $shaA + ",vendor/sub") | Out-Null
    Invoke-Git commit -q -m "add a gitlink" | Out-Null
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
Initialize-Submitted $f "m17" $anchorLarge "work/code" | Out-Null
Invoke-Q $f @("-Claim", "-Id", "m17", "-Role", "tester", "-By", "mttester") | Out-Null
Invoke-Q $f @("-Fail", "-Id", "m17", "-By", "mttester", "-Reason", "case 1", "-Evidence", $evFail, "-PlanAdequate") | Out-Null
Push-Location $f.repo
try {
    Invoke-Git checkout -q work/code | Out-Null
    Invoke-Git update-index --cacheinfo ("160000," + $shaB + ",vendor/sub") | Out-Null
    Invoke-Git commit -q -m "bump the gitlink" | Out-Null
    $num = @(Invoke-Git diff --numstat HEAD~1 HEAD) -join " "
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
Check "M17 setup: git numstat counts the bump as 1+1 lines" ($num -match '^1\s+1\s+vendor/sub') $num
$r = Invoke-Q $f @("-Resubmit", "-Id", "m17", "-By", "mtdev")
$a = Get-Advice $r.out
Check "M17 gitlink delta is unknown -> item-tier large, not retest-small-diff" (($a -match 'attempt 2 -> tier large \[rule item-tier') -and ($a -match 'lines changed since the last verdict unknown')) $a

# ======================================================================================
Step "M18 an anchor tier carrying an IGNORABLE character is refused at -Propose"
# ======================================================================================
$f = New-Fixture "m18"
foreach ($shape in @(@{ n = "softhyphen"; v = '"sm\u00adall"' }, @{ n = "nul"; v = '"small\u0000"' }, @{ n = "padded"; v = '" small"' })) {
    $p = Join-Path $Root ("anchor-ign-" + $shape.n + ".json")
    (Get-Content -Raw $anchorNone) -replace '"findings_sink"', ('"tier": ' + $shape.v + ', "findings_sink"') |
        Set-Content -Path $p -Encoding ascii
    $id = "m18" + $shape.n
    $r = Invoke-Q $f @("-Propose", "-Id", $id, "-Anchor", $p, "-Developer", "mtdev")
    Check ("M18 tier " + $shape.v + " refused (exit 1), no item") `
        (($r.code -eq 1) -and ($r.out -match "'tier' must be one of: large, small \(got '") -and ($null -eq (Get-QItem $f $id))) ("exit=" + $r.code)
}

# --- verdict --------------------------------------------------------------------------
$fail = @($results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0} check(s), {1} failed." -f $results.Count, $fail.Count) -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
foreach ($x in $fail) { Write-Host ("  FAILED: " + $x.check + " " + $x.detail) -ForegroundColor Red }
if (-not $KeepFixtures) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
else { Write-Host ("fixtures kept at " + $Root) -ForegroundColor Yellow }
if ($fail.Count) { exit 1 }
exit 0
