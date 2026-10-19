# verify-queue-deploy-surfaces.ps1 - executable proof for queue item `queue-deploy-surfaces-docker`.
#
#   .\scripts\agent-harness\verify-queue-deploy-surfaces.ps1                 # the shipped queue.ps1
#   .\scripts\agent-harness\verify-queue-deploy-surfaces.ps1 -Script <path>  # some OTHER copy (RED run)
#
# THE GAP: -Merged derived OB1 image surfaces only from integrations/<dir>/ (with a Dockerfile).
# Compose services built from OB1 docker/<service>/ (openbrain-workbench, openbrain-wiki-viewer,
# openbrain-wiki) were never derived, so an item whose only image change rode the gitlink under
# docker/ landed as "no deploy surface derived" and -Deployed was refused.
#
# HERMETIC. Every case builds its own scratch ai-stack-like repository with a nested OB1 clone
# (two commits, the gitlink written with update-index) and its own state directory under
# $env:TEMP, and points AI_STACK_WORKTREE_STATE at it. It never reads or writes the live queue,
# the main checkout or any real OB1 remote.

[CmdletBinding()]
param(
    [string]$Script = "",
    [string]$Root = "",
    [switch]$KeepFixtures
)
$ErrorActionPreference = "Continue"
if (-not $Script) { $Script = Join-Path $PSScriptRoot "queue.ps1" }
$Script = (Resolve-Path $Script).Path
$PsExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }
if (-not $Root) { $Root = Join-Path $env:TEMP ("queue-deploy-surfaces-" + $PID) }
if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Write-Host ("queue.ps1 under test : {0}" -f $Script) -ForegroundColor Cyan
Write-Host ("fixture root         : {0}" -f $Root) -ForegroundColor Cyan

$results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, $ok, [string]$detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & git.exe @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed: " + ($out -join "`n")) }
    return @($out)
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
    return (Get-Content -Raw -Path $p -Encoding UTF8 | ConvertFrom-Json)
}
function First-Line([string]$s) {
    $l = @($s -split "`n" | Where-Object { $_.Trim() })
    if ($l.Count -eq 0) { return "(no output)" }
    return $l[0].Trim()
}

# --- shared inputs ---------------------------------------------------------------------
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
    '  "findings_sink": "../documentation-plans-ai-stack/journal/notes/queue-defect-drill.md"',
    '}')
$planV1 = Join-Path $Root "plan-v1.md"
Set-Content -Path $planV1 -Encoding ascii -Value @("# Test plan", "## Case 1 - WORK.md exists. Fail: it is absent.")
$evPass = Join-Path $Root "evidence.md"
Set-Content -Path $evPass -Encoding ascii -Value @("## Case 1 - WORK.md exists   PASS", "ran case 1.")

# The OB1 compose file at the new pin, in the shape the real docker/docker-compose.yml uses:
# services built from ./<dir> under docker/, one built from ../integrations/mcp, and one with no build.
$composeLines = @(
    "services:",
    "  openbrain-db:",
    "    image: pgvector/pgvector:pg16",
    "  openbrain-workbench:",
    "    build:",
    "      context: ./workbench",
    "      dockerfile: Dockerfile",
    "    image: openbrain-workbench:local",
    "  openbrain-wiki-viewer:",
    "    build:",
    "      context: ./wiki-viewer",
    "    image: openbrain-wiki-viewer:local",
    "  openbrain-wiki:",
    "    build:",
    "      context: ./wiki-service",
    "    image: openbrain-wiki:local",
    "  openbrain-mcp:",
    "    build:",
    "      context: ../integrations/mcp",
    "      dockerfile: Dockerfile",
    "    image: openbrain-mcp:local")
$ob1Files = @("docker/workbench/x", "docker/wiki-viewer/y", "docker/wiki-service/z", "docker/init-export.sql", "integrations/mcp/index.ts", "integrations/mcp/Dockerfile")

function New-Fixture([string]$name, [string[]]$Touch) {
    # OB1 pin A holds every file in $ob1Files; pin B rewrites only the $Touch ones.
    $repo = Join-Path $Root $name
    $ob1 = Join-Path $repo "OB1"
    New-Item -ItemType Directory -Force -Path $ob1 | Out-Null
    Push-Location $ob1
    try {
        Invoke-Git init -q -b main | Out-Null
        Invoke-Git config user.email "qdrill@example.invalid" | Out-Null
        Invoke-Git config user.name "deploy surfaces drill" | Out-Null
        foreach ($f in $ob1Files) {
            $full = Join-Path $ob1 $f
            New-Item -ItemType Directory -Force -Path (Split-Path $full) | Out-Null
            Set-Content -Path $full -Encoding ascii -Value "v1"
        }
        Set-Content -Path (Join-Path $ob1 "docker/docker-compose.yml") -Encoding ascii -Value $composeLines
        Invoke-Git -GitArgs @("add", "-A") | Out-Null
        Invoke-Git commit -q -m "OB1 pin A" | Out-Null
        $pinA = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
        foreach ($f in $Touch) { Set-Content -Path (Join-Path $ob1 $f) -Encoding ascii -Value "v2" }
        Invoke-Git -GitArgs @("add", "-A") | Out-Null
        Invoke-Git commit -q -m "OB1 pin B" | Out-Null
        $pinB = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
    } finally { Pop-Location }
    Push-Location $repo
    try {
        Invoke-Git init -q -b base | Out-Null
        Invoke-Git config user.email "qdrill@example.invalid" | Out-Null
        Invoke-Git config user.name "deploy surfaces drill" | Out-Null
        Set-Content -Path (Join-Path $repo "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git -GitArgs @("update-index", "--add", "--cacheinfo", "160000,$pinA,OB1") | Out-Null
        Invoke-Git commit -q -m "base with OB1 at pin A" | Out-Null
        Invoke-Git checkout -q -b work/qd | Out-Null
        Set-Content -Path (Join-Path $repo "WORK.md") -Encoding ascii -Value "the work"
        Invoke-Git add WORK.md | Out-Null
        Invoke-Git -GitArgs @("update-index", "--cacheinfo", "160000,$pinB,OB1") | Out-Null
        Invoke-Git commit -q -m "the work: OB1 to pin B" | Out-Null
        Invoke-Git checkout -q base | Out-Null
    } finally { Pop-Location }
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return @{ repo = $repo; state = $state; name = $name; pinA = $pinA; pinB = $pinB }
}
function Merge-Item($fix, [string]$id) {
    # Drive an item to `reviewing`, merge --no-ff, record -Merged. Returns the -Merged result and item.
    Invoke-Q $fix @("-Propose", "-Id", $id, "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
    Invoke-Q $fix @("-ConfirmAnchor", "-Id", $id, "-By", "qoperator") | Out-Null
    Invoke-Q $fix @("-Submit", "-Id", $id, "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $planV1) | Out-Null
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "tester", "-By", "qtester") | Out-Null
    Invoke-Q $fix @("-Pass", "-Id", $id, "-By", "qtester", "-Evidence", $evPass, "-PlanAdequate") | Out-Null
    Invoke-Q $fix @("-Approve", "-Id", $id, "-By", "qoperator") | Out-Null
    Invoke-Q $fix @("-Claim", "-Id", $id, "-Role", "reviewer", "-By", "qrev") | Out-Null
    Push-Location $fix.repo
    try {
        Invoke-Git merge --no-ff -q work/qd -m ("merge " + $id + " (evidence: drill)") | Out-Null
        $m = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
    } finally { Pop-Location }
    $r = Invoke-Q $fix @("-Merged", "-Id", $id, "-By", "qrev", "-Sha", $m, "-FitsCodebase")
    return @{ r = $r; it = (Get-QItem $fix $id) }
}
function Get-Sorted($x) { return ((@($x.it.deploy_pending) | Sort-Object) -join ",") }
function Show-Pending($x) { return ("exit=" + $x.r.code + " pending=[" + (@($x.it.deploy_pending) -join ",") + "] | " + (First-Line $x.r.out)) }

# ======================================================================================
Step "C1  a gitlink range changing only docker/workbench/: -Merged records image:openbrain-workbench, -Deployed accepts it"
# ======================================================================================
$f1 = New-Fixture "c1" @("docker/workbench/x")
$x = Merge-Item $f1 "qc1"
Check "C1: -Merged records deploy_surfaces = ['image:openbrain-workbench'] (no 'no deploy surface derived')" `
    (($x.r.code -eq 0) -and ((@($x.it.deploy_surfaces) -join ",") -eq "image:openbrain-workbench") -and ((@($x.it.deploy_pending) -join ",") -eq "image:openbrain-workbench") -and -not ($x.r.out -match "No deploy surface derived")) (Show-Pending $x)
Check "C1: the notes say which rule produced the surface" `
    ((@($x.it.deploy_derived.notes) -join "`n") -match "image:openbrain-workbench derived by the docker build-context rule") ((@($x.it.deploy_derived.notes) -join " | "))
$evLine = "openbrain-workbench: label org.opencontainers.image.revision=" + $f1.pinB.Substring(0, 12) + ", State.Health.Status=healthy, RestartCount=0"
$evFile = Join-Path $Root "c1-deploy-evidence.md"
Set-Content -Path $evFile -Encoding ascii -Value $evLine
$r = Invoke-Q $f1 @("-Deployed", "-Id", "qc1", "-By", "qoperator", "-Surface", "image:openbrain-workbench", "-Evidence", $evFile)
$it = Get-QItem $f1 "qc1"
Check "C1: -Deployed -Surface image:openbrain-workbench with a one-line evidence file is ACCEPTED, item deployed, nothing pending" `
    (($r.code -eq 0) -and ($it.state -eq "deployed") -and (@($it.deploy_pending).Count -eq 0) -and (@($it.deployed).Count -eq 1)) ("exit=" + $r.code + " state=" + $it.state + " | " + (First-Line $r.out))
$r = Invoke-Q $f1 @("-List")
Check "C1: -List shows no [UNDEPLOYED] once closed" (-not ($r.out -match "UNDEPLOYED")) (First-Line $r.out)

# ======================================================================================
Step "C2  workbench + wiki-viewer -> both, once; wiki-service maps to openbrain-wiki"
# ======================================================================================
$f2 = New-Fixture "c2" @("docker/workbench/x", "docker/wiki-viewer/y")
$x = Merge-Item $f2 "qc2"
Check "C2: a range changing docker/workbench/ and docker/wiki-viewer/ records BOTH images, each once, nothing else" `
    ((Get-Sorted $x) -eq "image:openbrain-wiki-viewer,image:openbrain-workbench") (Show-Pending $x)
$f2b = New-Fixture "c2b" @("docker/workbench/x", "docker/wiki-service/z")
$x = Merge-Item $f2b "qc2b"
Check "C2: docker/wiki-service/ maps to image:openbrain-wiki (the service name, not the directory)" `
    ((Get-Sorted $x) -eq "image:openbrain-wiki,image:openbrain-workbench") (Show-Pending $x)
$f2c = New-Fixture "c2c" @("docker/workbench/x", "docker/init-export.sql")
$x = Merge-Item $f2c "qc2c"
Check "C2: a changed path under the context plus docker/init-export.sql (not under any context) records the image once" `
    ((@($x.it.deploy_pending) -join ",") -eq "image:openbrain-workbench") (Show-Pending $x)

# ======================================================================================
Step "C3  only integrations/mcp -> image:openbrain-mcp only; only docker/init-export.sql -> nothing"
# ======================================================================================
$f3 = New-Fixture "c3" @("integrations/mcp/index.ts")
$x = Merge-Item $f3 "qc3"
Check "C3: a range changing only integrations/mcp/ still records image:openbrain-mcp and NOTHING under docker/" `
    ((@($x.it.deploy_pending) -join ",") -eq "image:openbrain-mcp") (Show-Pending $x)
Check "C3: ... and the integrations rule is the one named in the notes" `
    ((@($x.it.deploy_derived.notes) -join "`n") -match "image:openbrain-mcp derived by the integrations rule") ((@($x.it.deploy_derived.notes) -join " | "))
$f3b = New-Fixture "c3b" @("docker/init-export.sql")
$x = Merge-Item $f3b "qc3b"
Check "C3: a range changing only docker/init-export.sql (no build context) records NO image" `
    (($x.r.code -eq 0) -and (@($x.it.deploy_pending).Count -eq 0) -and (@($x.it.deploy_surfaces).Count -eq 0)) (Show-Pending $x)
$r = Invoke-Q $f3b @("-Deployed", "-Id", "qc3b", "-By", "qoperator", "-Evidence", $evFile)
Check "C3: ... and -Deployed on it is refused (nothing to close), as before" (($r.code -ne 0) -and ($r.out -match "records no deploy surface")) (First-Line $r.out)
$f3c = New-Fixture "c3c" @("integrations/mcp/index.ts", "docker/workbench/x")
$x = Merge-Item $f3c "qc3c"
Check "C3: integrations/mcp and docker/workbench together record exactly the two images, no duplicates" `
    ((Get-Sorted $x) -eq "image:openbrain-mcp,image:openbrain-workbench") (Show-Pending $x)

# --- verdict -----------------------------------------------------------------------------
$fail = @($results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0} check(s), {1} failed." -f $results.Count, $fail.Count) -ForegroundColor $(if ($fail.Count) { "Red" } else { "Green" })
foreach ($f in $fail) { Write-Host ("  FAILED: " + $f.check + " " + $f.detail) -ForegroundColor Red }
if (-not $KeepFixtures) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
else { Write-Host ("fixtures kept at " + $Root) -ForegroundColor Yellow }
if ($fail.Count) { exit 1 }
exit 0
