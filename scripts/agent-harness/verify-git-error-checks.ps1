# verify-git-error-checks.ps1 - executable proof for harness item `andon-clear`: a git call
# that FAILS is reported, in each script family the andon `git-error-swallowed` condition covers.
#
#   .\scripts\agent-harness\verify-git-error-checks.ps1                      # this checkout's scripts
#   .\scripts\agent-harness\verify-git-error-checks.ps1 -ScriptsRoot <dir>   # RED run: a tree holding
#                                       scripts\agent-harness and scripts\checks from BEFORE the change
#
# HOW A GIT FAILURE IS FORCED. A tiny git.exe shim (compiled below with Add-Type, placed first on
# PATH for the child process only) forwards every call to the real git EXCEPT one whose arguments
# contain $env:GIT_SHIM_FAIL: that call prints a fatal line and exits 128, which is what a real
# git does when a repository is damaged or a ref is unreadable. Nothing in the scripts under test is
# stubbed; they run for real, in scratch repositories under $env:TEMP, never against the live
# queue, the live registry or this checkout's worktrees.
#
# Each case runs the script twice: a CONTROL with no failure forced (it must succeed, so a
# non-zero exit in the forced run is attributable to the forced failure and not to the fixture),
# then the FORCED run, which must exit non-zero AND name the failed git command in its output.

[CmdletBinding()]
param(
    [string]$ScriptsRoot = "",
    [string]$Root = "",
    [switch]$KeepFixtures
)
$ErrorActionPreference = "Continue"
if (-not $ScriptsRoot) { $ScriptsRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$H = Join-Path $ScriptsRoot "scripts\agent-harness"
$C = Join-Path $ScriptsRoot "scripts\checks"
$PsExe = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path $PsExe)) { $PsExe = "powershell" }
if (-not $Root) { $Root = Join-Path $env:TEMP ("git-error-checks-" + $PID) }
if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
New-Item -ItemType Directory -Force -Path $Root | Out-Null
Write-Host ("scripts under test : {0}" -f $ScriptsRoot) -ForegroundColor Cyan
Write-Host ("fixture root       : {0}" -f $Root) -ForegroundColor Cyan

$results = @()
function Step([string]$text) { Write-Host "`n=== $text ===" -ForegroundColor Cyan }
function Check([string]$label, $ok, [string]$detail = "") {
    $script:results += [pscustomobject]@{ check = $label; pass = [bool]$ok; detail = $detail }
    Write-Host ("  [{0}] {1} {2}" -f $(if ($ok) { "PASS" } else { "FAIL" }), $label, $detail) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
}
function First-Line([string]$s) {
    $l = @($s -split "`n" | Where-Object { $_.Trim() })
    if ($l.Count -eq 0) { return "(no output)" }
    return $l[0].Trim()
}

# --- the shim -----------------------------------------------------------------------------
$realGit = (Get-Command git.exe -ErrorAction Stop).Source
if (-not $realGit) { throw "git.exe not found on PATH - this drill needs a real git to forward to" }
$shimDir = Join-Path $Root "shim"
New-Item -ItemType Directory -Force -Path $shimDir | Out-Null
$shimSrc = @'
using System;
using System.Diagnostics;
using System.Text;
public static class GitShim {
    static string Q(string s) {
        if (s.Length > 0 && s.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0) return s;
        StringBuilder sb = new StringBuilder("\"");
        int bs = 0;
        foreach (char c in s) {
            if (c == '\\') { bs++; }
            else if (c == '"') { sb.Append('\\', bs * 2 + 1); sb.Append('"'); bs = 0; }
            else { sb.Append('\\', bs); bs = 0; sb.Append(c); }
        }
        sb.Append('\\', bs * 2); sb.Append('"');
        return sb.ToString();
    }
    public static int Main(string[] a) {
        string real = Environment.GetEnvironmentVariable("GIT_SHIM_REAL");
        string fail = Environment.GetEnvironmentVariable("GIT_SHIM_FAIL");
        string joined = string.Join(" ", a);
        if (!string.IsNullOrEmpty(fail) && joined.Contains(fail)) {
            Console.Error.WriteLine("fatal: forced failure of git " + joined);
            return 128;
        }
        ProcessStartInfo psi = new ProcessStartInfo(real);
        psi.UseShellExecute = false;
        psi.Arguments = string.Join(" ", Array.ConvertAll(a, new Converter<string, string>(Q)));
        Process p = Process.Start(psi);
        p.WaitForExit();
        return p.ExitCode;
    }
}
'@
$shimSrcFile = Join-Path $shimDir "shim.cs"
Set-Content -Path $shimSrcFile -Value $shimSrc -Encoding ascii
Add-Type -TypeDefinition $shimSrc -OutputAssembly (Join-Path $shimDir "git.exe") -OutputType ConsoleApplication
if (-not (Test-Path (Join-Path $shimDir "git.exe"))) { Write-Host "could not build the git shim" -ForegroundColor Red; exit 2 }

function Invoke-Git {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$GitArgs)
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $realGit @GitArgs 2>&1 } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw ("git " + ($GitArgs -join " ") + " failed: " + ($out -join "`n")) }
    return @($out)
}

# Run a script in a child powershell with the shim first on PATH. $Fail = "" forces nothing.
function Invoke-Under([string]$Repo, [string]$State, [string]$ScriptPath, [string[]]$ScriptArgs, [string]$Fail) {
    $saved = @{ PATH = $env:PATH; REAL = $env:GIT_SHIM_REAL; FAIL = $env:GIT_SHIM_FAIL
                STATE = $env:AI_STACK_WORKTREE_STATE; LINE = $env:AI_STACK_WORK_LINE }
    $env:PATH = $shimDir + ";" + $env:PATH
    $env:GIT_SHIM_REAL = $realGit
    $env:GIT_SHIM_FAIL = $Fail
    $env:AI_STACK_WORKTREE_STATE = $State
    $env:AI_STACK_WORK_LINE = "base"
    Push-Location $Repo
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $out = & $PsExe -NoProfile -NonInteractive -File $ScriptPath @ScriptArgs 2>&1 }
    finally {
        $ErrorActionPreference = $prev
        Pop-Location
        $env:PATH = $saved.PATH; $env:GIT_SHIM_REAL = $saved.REAL; $env:GIT_SHIM_FAIL = $saved.FAIL
        $env:AI_STACK_WORKTREE_STATE = $saved.STATE; $env:AI_STACK_WORK_LINE = $saved.LINE
    }
    return @{ code = $LASTEXITCODE; out = (($out | ForEach-Object { "$_" }) -join "`n") }
}

function New-ScratchRepo([string]$name) {
    $repo = Join-Path $Root $name
    New-Item -ItemType Directory -Force -Path $repo | Out-Null
    Push-Location $repo
    try {
        Invoke-Git init -q -b base | Out-Null
        Invoke-Git config user.email "gdrill@example.invalid" | Out-Null
        Invoke-Git config user.name "git error drill" | Out-Null
        Invoke-Git config commit.gpgsign false | Out-Null
        Set-Content -Path (Join-Path $repo "README.md") -Encoding ascii -Value "scratch"
        Invoke-Git add README.md | Out-Null
        Invoke-Git commit -q -m "base" | Out-Null
    } finally { Pop-Location }
    $state = Join-Path $Root ("state-" + $name)
    New-Item -ItemType Directory -Force -Path $state | Out-Null
    return @{ repo = $repo; state = $state }
}

# ======================================================================================
Step "F1  queue.ps1 -Merged: a failing git ls-tree is REPORTED, not read as 'no deploy surface'"
# ======================================================================================
$q = Join-Path $H "queue.ps1"
$fq = New-ScratchRepo "queue"
Push-Location $fq.repo
try {
    Invoke-Git checkout -q -b work/qd | Out-Null
    Set-Content -Path (Join-Path $fq.repo "WORK.md") -Encoding ascii -Value "the work"
    Invoke-Git add WORK.md | Out-Null
    Invoke-Git commit -q -m "the work" | Out-Null
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
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
$plan = Join-Path $Root "plan.md"
Set-Content -Path $plan -Encoding ascii -Value @("# Test plan", "## Case 1 - WORK.md exists. Fail: it is absent.")
$ev = Join-Path $Root "evidence.md"
Set-Content -Path $ev -Encoding ascii -Value @("## Case 1 - WORK.md exists   PASS", "ran case 1.")
function Invoke-Q([string[]]$QArgs, [string]$Fail = "") { return (Invoke-Under $fq.repo $fq.state $q $QArgs $Fail) }
Invoke-Q @("-Propose", "-Id", "qg1", "-Anchor", $anchorFile, "-Developer", "qdev") | Out-Null
Invoke-Q @("-ConfirmAnchor", "-Id", "qg1", "-By", "qoperator") | Out-Null
Invoke-Q @("-Submit", "-Id", "qg1", "-Branch", "work/qd", "-Developer", "qdev", "-TestPlan", $plan) | Out-Null
Invoke-Q @("-Claim", "-Id", "qg1", "-Role", "tester", "-By", "qtester") | Out-Null
Invoke-Q @("-Pass", "-Id", "qg1", "-By", "qtester", "-Evidence", $ev, "-PlanAdequate") | Out-Null
Invoke-Q @("-Approve", "-Id", "qg1", "-By", "qoperator") | Out-Null
Invoke-Q @("-Claim", "-Id", "qg1", "-Role", "reviewer", "-By", "qrev") | Out-Null
Push-Location $fq.repo
try {
    Invoke-Git merge --no-ff -q work/qd -m "merge qg1 (evidence: drill)" | Out-Null
    $mergeSha = (Invoke-Git rev-parse HEAD | Select-Object -First 1).Trim()
} finally { Pop-Location }
$mArgs = @("-Merged", "-Id", "qg1", "-By", "qrev", "-Sha", $mergeSha, "-FitsCodebase")
$rf = Invoke-Q $mArgs "ls-tree -r --name-only"
$itPath = Join-Path $fq.state "queue\qg1.json"
$itState = if (Test-Path $itPath) { (Get-Content -Raw -Path $itPath | ConvertFrom-Json).state } else { "(no item)" }
Check "F1 FORCED: -Merged exits non-zero when git ls-tree fails" ($rf.code -ne 0) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
Check "F1 FORCED: the output names the failed command (git ls-tree -r --name-only)" ($rf.out -match 'git ls-tree -r --name-only') (First-Line $rf.out)
Check "F1 FORCED: nothing was recorded (item still reviewing, not merged)" ($itState -eq "reviewing") ("state=" + $itState)
$rc = Invoke-Q $mArgs ""
$itState2 = (Get-Content -Raw -Path $itPath | ConvertFrom-Json).state
Check "F1 CONTROL: the same -Merged with no failure forced succeeds and records the merge" (($rc.code -eq 0) -and ($itState2 -eq "merged")) ("exit=" + $rc.code + " state=" + $itState2 + " | " + (First-Line $rc.out))

# ======================================================================================
Step "F2  remove-worktree.ps1: a failing git status is REPORTED, not read as 'nothing uncommitted'"
# ======================================================================================
$rw = Join-Path $H "remove-worktree.ps1"
$fr = New-ScratchRepo "rmwt"
$wtDir = Join-Path $Root "rmwt-wt1"
Push-Location $fr.repo
try { Invoke-Git worktree add -q -b work/rw1 $wtDir | Out-Null } finally { Pop-Location }
$rf = Invoke-Under $fr.repo $fr.state $rw @("-Id", "rw1", "-WhatIf") "status --porcelain"
$rfName = (First-Line $rf.out)
Check "F2 FORCED: -WhatIf exits non-zero when git status fails (it used to print 'safe to remove')" ($rf.code -ne 0) ("exit=" + $rf.code + " | " + $rfName)
Check "F2 FORCED: the output names the failed command (git ... status --porcelain)" ($rf.out -match 'status --porcelain failed') $rfName
Check "F2 FORCED: the output does not claim the worktree is safe to remove" ($rf.out -notmatch 'safe to remove') $rfName
$rc = Invoke-Under $fr.repo $fr.state $rw @("-Id", "rw1", "-WhatIf") ""
Check "F2 CONTROL: with no failure forced the same -WhatIf exits 0 and says safe to remove" (($rc.code -eq 0) -and ($rc.out -match 'safe to remove')) ("exit=" + $rc.code + " | " + (First-Line $rc.out))
# and the worktree list failure on the post-remove path (deregistering a worktree git may still track)
$rf2 = Invoke-Under $fr.repo $fr.state $rw @("-Id", "rw1") "worktree list --porcelain"
Check "F2 FORCED: a failing git worktree list is reported (exit non-zero, command named), not read as 'git no longer tracks it'" (($rf2.code -ne 0) -and ($rf2.out -match 'worktree list --porcelain failed \(exit')) ("exit=" + $rf2.code + " | " + (First-Line $rf2.out))
Remove-Item -Recurse -Force $wtDir -ErrorAction SilentlyContinue

# ======================================================================================
Step "F3  new-worktree.ps1: a failing git ls-files is REPORTED, not read as 'no CRLF found'"
# ======================================================================================
$nw = Join-Path $H "new-worktree.ps1"
$fn = New-ScratchRepo "newwt"
$rf = Invoke-Under $fn.repo $fn.state $nw @("-Id", "nw1") "ls-files *.sh"
Check "F3 FORCED: new-worktree exits non-zero when git ls-files fails" ($rf.code -ne 0) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
Check "F3 FORCED: the output names the failed command (git ... ls-files)" ($rf.out -match 'ls-files') (First-Line $rf.out)
Invoke-Git -GitArgs @("-C", $fn.repo, "worktree", "remove", "--force", (Join-Path $fn.repo ".claude\worktrees\wt-nw1")) 2>&1 | Out-Null
Invoke-Git -GitArgs @("-C", $fn.repo, "branch", "-D", "work/nw1") 2>&1 | Out-Null
Remove-Item -Recurse -Force $fn.state -ErrorAction SilentlyContinue; New-Item -ItemType Directory -Force -Path $fn.state | Out-Null
$rc = Invoke-Under $fn.repo $fn.state $nw @("-Id", "nw1") ""
Check "F3 CONTROL: with no failure forced new-worktree succeeds" ($rc.code -eq 0) ("exit=" + $rc.code + " | " + (First-Line $rc.out))

# ======================================================================================
Step "F4  scripts/checks: a failing git diff --cached is REPORTED, not read as 'nothing staged'"
# ======================================================================================
$fc = New-ScratchRepo "checks"
Push-Location $fc.repo
try {
    Set-Content -Path (Join-Path $fc.repo "docker-compose.yml") -Encoding ascii -Value @("services:", "  web:", "    image: nginx:1")
    Invoke-Git add docker-compose.yml | Out-Null
} finally { Pop-Location }
$envScope = Join-Path $C "check-env-file-scope.ps1"
$rf = Invoke-Under $fc.repo $fc.state $envScope @("-Root", $fc.repo) "diff --cached --name-only"
Check "F4a FORCED: check-env-file-scope exits non-zero when git diff --cached fails (it used to say 'no compose files staged - skipped', exit 0)" ($rf.code -ne 0) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
Check "F4a FORCED: the check itself reports the failure, naming the command (not an incidental PowerShell stderr error)" ($rf.out -match 'git diff --cached --name-only --diff-filter=ACMR failed \(exit') (First-Line $rf.out)
$rc = Invoke-Under $fc.repo $fc.state $envScope @("-Root", $fc.repo) ""
Check "F4a CONTROL: with no failure forced the same check does not report a git failure" ($rc.out -notmatch 'failed \(exit') ("exit=" + $rc.code + " | " + (First-Line $rc.out))

$docPlace = Join-Path $C "check-doc-placement.ps1"
$rf = Invoke-Under $fc.repo $fc.state $docPlace @("-All", "-Root", $fc.repo) "ls-files -z --others"
Check "F4b FORCED: check-doc-placement -All exits non-zero when git ls-files --others fails" ($rf.code -ne 0) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
Check "F4b FORCED: the output names the failed command" ($rf.out -match 'git ls-files -z --others') (First-Line $rf.out)
$rc = Invoke-Under $fc.repo $fc.state $docPlace @("-All", "-Root", $fc.repo) ""
Check "F4b CONTROL: with no failure forced the audit does not report a git failure" ($rc.out -notmatch 'failed \(exit') ("exit=" + $rc.code + " | " + (First-Line $rc.out))

# ======================================================================================
Step "F5  check-hook-attestation.ps1: a git FAILURE is not 'this branch does not attest' / 'no merge gate'"
# ======================================================================================
# The guard decides whether a branch's commits must carry attestations. Reading the branch's
# pre-commit hook (and the fork point's pre-merge-commit hook) used to treat ANY empty result as
# "file absent", so a failing git, or an unknown branch, switched the guard off and exited 0.
$fh = New-ScratchRepo "attest"
Push-Location $fh.repo
try {
    New-Item -ItemType Directory -Force -Path (Join-Path $fh.repo ".githooks") | Out-Null
    Set-Content -Path (Join-Path $fh.repo ".githooks/pre-merge-commit") -Encoding ascii -Value @("#!/bin/sh", "echo gate")
    Invoke-Git add -A | Out-Null
    Invoke-Git commit -q -m "base carries a merge hook" | Out-Null
    Invoke-Git checkout -q -b work/x | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $fh.repo ".githooks") | Out-Null
    Set-Content -Path (Join-Path $fh.repo ".githooks\pre-commit") -Encoding ascii -Value @("#!/bin/sh", "echo x >> hook-attest.log")
    Set-Content -Path (Join-Path $fh.repo "WORK.md") -Encoding ascii -Value "unattested work"
    Invoke-Git add -A | Out-Null
    Invoke-Git commit -q -m "unattested commit" | Out-Null
    Invoke-Git checkout -q base | Out-Null
} finally { Pop-Location }
$ledger = Join-Path $Root "attest-ledger.log"
Set-Content -Path $ledger -Encoding ascii -Value @("0000000000000000000000000000000000000000 0000000000000000000000000000000000000000 1 hook")
$ha = Join-Path $C "check-hook-attestation.ps1"
function Invoke-Attest([string]$Branch, [string]$Fail = "") {
    $prevL = $env:AI_STACK_ATTEST_LEDGER
    $env:AI_STACK_ATTEST_LEDGER = $ledger
    try { return (Invoke-Under $fh.repo $fh.state $ha @("-Branch", $Branch, "-Base", "base", "-RepoRoot", $fh.repo, "-AllowLedgerOverride") $Fail) }
    finally { $env:AI_STACK_ATTEST_LEDGER = $prevL }
}
$rc = Invoke-Attest "work/x"
Check "F5 CONTROL: the unattested commit is REFUSED (exit 1) - the guard is active" (($rc.code -eq 1) -and ($rc.out -match 'UNATTESTED|not attested|unattested')) ("exit=" + $rc.code + " | " + (First-Line $rc.out))
$rf = Invoke-Attest "work/x" "show work/x:.githooks/pre-commit"
Check "F5 FORCED show of the branch hook: exit 2 and the command is named (it used to print INACTIVE, exit 0)" (($rf.code -eq 2) -and ($rf.out -match 'show work/x:\.githooks/pre-commit failed \(exit')) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
$rf = Invoke-Attest "work/x" "ls-tree --name-only work/x"
Check "F5 FORCED ls-tree (hook existence probe): exit 2, command named" (($rf.code -eq 2) -and ($rf.out -match 'ls-tree --name-only work/x .* failed \(exit')) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
$ru = Invoke-Attest "work/no-such-branch"
Check "F5 UNKNOWN BRANCH: exit 2 naming the git rev-parse that could not resolve it (it used to print INACTIVE, exit 0)" (($ru.code -eq 2) -and ($ru.out -match 'rev-parse --verify --quiet work/no-such-branch.*failed \(exit')) ("exit=" + $ru.code + " | " + (First-Line $ru.out))
$rf = Invoke-Attest "work/x" "merge-base base work/x"
Check "F5 FORCED merge-base: exit 2, command named (it used to silently drop the merge gate and exit 1 on the wrong list)" (($rf.code -eq 2) -and ($rf.out -match 'git merge-base base work/x failed \(exit')) ("exit=" + $rf.code + " | " + (First-Line $rf.out))
# the merge hook read: base carries a pre-merge-commit hook (committed in the fixture), so the
# fork point of work/x has one and the merge gate is active; its read is forced to fail.
$rfShow = Invoke-Attest "work/x" ":.githooks/pre-merge-commit"
Check "F5 FORCED show of the pre-merge-commit hook at the fork point: exit 2, command named (it used to read as 'no merge gate')" (($rfShow.code -eq 2) -and ($rfShow.out -match 'pre-merge-commit failed \(exit')) ("exit=" + $rfShow.code + " | " + (First-Line $rfShow.out))

# ======================================================================================
$failed = @($results | Where-Object { -not $_.pass })
Write-Host ""
Write-Host ("{0} check(s), {1} failed" -f $results.Count, $failed.Count) -ForegroundColor $(if ($failed.Count) { "Red" } else { "Green" })
if (-not $KeepFixtures) { Remove-Item -Recurse -Force $Root -ErrorAction SilentlyContinue }
if ($failed.Count) { exit 1 }
exit 0
