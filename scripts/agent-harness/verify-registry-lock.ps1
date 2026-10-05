# verify-registry-lock.ps1 - race test for the shared worktree registry (worktrees.json).
#
# Builds a SCRATCH git repository under %TEMP% (its own .git, so its own registry - never this
# checkout's), then races real new-worktree.ps1 / remove-worktree.ps1 processes against it and
# counts lost rows. Run it against any toolkit copy with -ToolkitDir: the base commit's scripts
# (expected RED: rows lost) or this tree's (expected GREEN: none lost).
#
#   .\verify-registry-lock.ps1 -Runs 10                    # this tree's toolkit
#   .\verify-registry-lock.ps1 -Runs 10 -ToolkitDir <dir>  # a copy of an older toolkit
#
# Docker is never touched: remove-worktree.ps1 calls reap.ps1, so every child runs with a
# fail-closed `docker` stub first on PATH and DOCKER_HOST pointing at a dead endpoint.
# Exit 0 = no row lost in any scenario; exit 1 = a loss or a wedge.

[CmdletBinding()]
param(
    [int]$Runs = 10,
    [int]$Concurrency = 2,
    [string]$ToolkitDir = "",
    [int]$HolderTimeoutSec = 6
)
$ErrorActionPreference = "Stop"
if (-not $ToolkitDir) { $ToolkitDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ToolkitDir = (Resolve-Path $ToolkitDir).Path

$root = Join-Path ([System.IO.Path]::GetTempPath()) ("wtlock-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $root | Out-Null
$repo = Join-Path $root "repo"
$stub = Join-Path $root "stub"
New-Item -ItemType Directory -Path $repo, $stub | Out-Null
Set-Content -Path (Join-Path $stub "docker.cmd") -Value "@echo off`r`nexit /b 1`r`n" -Encoding ASCII

function Invoke-ScratchGit([string[]]$a) {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try {
        $o = & git -C $repo @a 2>&1
        if ($LASTEXITCODE -ne 0) { throw "git $($a -join ' ') failed: $o" }
        return $o
    } finally { $ErrorActionPreference = $p }
}
function Remove-ScratchWorktree([string]$Id) {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $null = & git -C $repo worktree remove --force (Join-Path $repo ".claude\worktrees\wt-$Id") 2>&1 } finally { $ErrorActionPreference = $p }
}
$null = Invoke-ScratchGit @("init", "-q", "-b", "dev")
$null = Invoke-ScratchGit @("config", "user.email", "t@example.invalid")
$null = Invoke-ScratchGit @("config", "user.name", "t")
$null = Invoke-ScratchGit @("config", "commit.gpgsign", "false")
Set-Content -Path (Join-Path $repo "a.txt") -Value "x" -Encoding ASCII
$null = Invoke-ScratchGit @("add", "a.txt")
$null = Invoke-ScratchGit @("commit", "-q", "-m", "init")

# Safety: the registry this test uses must live inside the scratch repo.
$gitDir = (Resolve-Path (Join-Path $repo ".git")).Path
$registry = Join-Path $gitDir "agent-worktrees\worktrees.json"
if (-not $registry.StartsWith($root)) { throw "refusing: registry $registry is outside the scratch root" }

$env:PATH = "$stub;$env:PATH"
$env:DOCKER_HOST = "tcp://127.0.0.1:1"
$env:AI_STACK_WORK_LINE = "dev"
Remove-Item Env:AI_STACK_WORKTREE_STATE -ErrorAction SilentlyContinue

function Start-Child([string]$Script, [string[]]$ScriptArgs, [long]$GoTicks) {
    # Each child spins until the same instant so racing processes start together.
    $cmd = "while([DateTime]::UtcNow.Ticks -lt $GoTicks){}; & '" + (Join-Path $ToolkitDir $Script) + "' " +
           ($ScriptArgs -join " ") + "; exit `$LASTEXITCODE"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-NoProfile -NonInteractive -Command `"$cmd`""
    $psi.WorkingDirectory = $repo
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $p | Add-Member -NotePropertyName Out -NotePropertyValue $p.StandardOutput.ReadToEndAsync()
    $p | Add-Member -NotePropertyName Err -NotePropertyValue $p.StandardError.ReadToEndAsync()
    return $p
}
function Get-Rows {
    if (-not (Test-Path $registry)) { return @() }
    # A registry two writers interleaved into invalid JSON counts as every row lost.
    try { $j = Get-Content -Raw -Path $registry | ConvertFrom-Json } catch { Write-Host "  registry file is corrupt (unparsable JSON)" -ForegroundColor Red; return @() }
    if (-not $j.worktrees) { return @() }
    return @($j.worktrees.PSObject.Properties.Name)
}
function Wait-All($procs) { foreach ($p in $procs) { if (-not $p.WaitForExit(180000)) { $p.Kill() } } }
function Start-Holder([string]$LockPath) {
    # Takes the lock exactly as the helper does (open, FileShare.None) and sits on it.
    $c = "`$f=New-Object System.IO.FileStream('$LockPath','OpenOrCreate','ReadWrite','None'); Start-Sleep 600"
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "powershell.exe"
    $psi.Arguments = "-NoProfile -NonInteractive -Command `"$c`""
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $h = [System.Diagnostics.Process]::Start($psi)
    for ($i = 0; $i -lt 100; $i++) {
        Start-Sleep -Milliseconds 100
        try { $t = [System.IO.File]::Open($LockPath, 'Open', 'ReadWrite', 'None'); $t.Dispose() } catch { return $h }
    }
    $h.Kill()
    throw "holder never took the lock"
}

$fail = 0
try {
    # --- 1. N concurrent provisions -------------------------------------------------
    $lost = 0
    for ($r = 1; $r -le $Runs; $r++) {
        if (Test-Path $registry) { Remove-Item $registry -Force }
        $go = [DateTime]::UtcNow.AddSeconds(3).Ticks
        $ids = @(1..$Concurrency | ForEach-Object { "r$r-$_" })
        $procs = @($ids | ForEach-Object { Start-Child "new-worktree.ps1" @("-Id", $_) $go })
        Wait-All $procs
        $rows = Get-Rows
        $missing = @($ids | Where-Object { $rows -notcontains $_ })
        if ($missing.Count) { $lost++; Write-Host ("  run {0}: LOST row(s) {1}" -f $r, ($missing -join ",")) -ForegroundColor Red }
        foreach ($i in $ids) { Remove-ScratchWorktree $i }
    }
    Write-Host ("[provision x{0} race] runs={1} runs_with_lost_row={2}" -f $Concurrency, $Runs, $lost)
    if ($lost) { $fail++ }

    # --- 2. new + remove racing -----------------------------------------------------
    $lost2 = 0
    for ($r = 1; $r -le $Runs; $r++) {
        if (Test-Path $registry) { Remove-Item $registry -Force }
        $old = "old$r"; $new = "new$r"
        $p0 = Start-Child "new-worktree.ps1" @("-Id", $old) ([DateTime]::UtcNow.Ticks)
        Wait-All @($p0)
        $go = [DateTime]::UtcNow.AddSeconds(3).Ticks
        $pn = Start-Child "new-worktree.ps1" @("-Id", $new) $go
        $pr = Start-Child "remove-worktree.ps1" @("-Id", $old) $go
        Wait-All @($pn, $pr)
        $rows = Get-Rows
        $bad = @()
        if ($rows -notcontains $new) { $bad += "missing $new" }
        if ($rows -contains $old) { $bad += "stale $old" }
        if ($bad.Count) { $lost2++; Write-Host ("  run {0}: {1}" -f $r, ($bad -join "; ")) -ForegroundColor Red }
        Remove-ScratchWorktree $new
    }
    Write-Host ("[new+remove race] runs={0} runs_with_wrong_rows={1}" -f $Runs, $lost2)
    if ($lost2) { $fail++ }

    # --- 3. killed holder, then live holder ------------------------------------------
    if (Test-Path $registry) { Remove-Item $registry -Force }
    $lockPath = "$registry.lock"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $registry) | Out-Null
    $env:AI_STACK_REGISTRY_LOCK_TIMEOUT_SEC = "$HolderTimeoutSec"

    $h = Start-Holder $lockPath
    $h.Kill(); $null = $h.WaitForExit(10000)
    $p = Start-Child "new-worktree.ps1" @("-Id", "afterkill") ([DateTime]::UtcNow.Ticks)
    Wait-All @($p)
    $present = (Get-Rows) -contains "afterkill"
    $ok3 = ($p.ExitCode -eq 0) -and $present
    Write-Host ("[killed holder] next provision exit={0} row_present={1} -> {2}" -f $p.ExitCode, $present, $(if ($ok3) { "OK" } else { "WEDGED" }))
    if (-not $ok3) { $fail++ }
    Remove-ScratchWorktree "afterkill"

    $h = Start-Holder $lockPath
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $q = Start-Child "new-worktree.ps1" @("-Id", "blocked") ([DateTime]::UtcNow.Ticks)
    Wait-All @($q)
    $h.Kill()
    $text = $q.Out.Result + $q.Err.Result
    if ($env:WTLOCK_DEBUG) { Write-Host $text }
    $named = $text -match [regex]::Escape("worktrees.json.lock")
    $bounded = $sw.Elapsed.TotalSeconds -lt ($HolderTimeoutSec + 90)
    $ok4 = ($q.ExitCode -ne 0) -and $named -and $bounded
    Write-Host ("[live holder] provision exit={0} names_lock={1} elapsed={2:N1}s (bound {3}s) -> {4}" -f $q.ExitCode, $named, $sw.Elapsed.TotalSeconds, $HolderTimeoutSec, $(if ($ok4) { "OK" } else { "FAIL" }))
    if (-not $ok4) { $fail++ }
    Remove-ScratchWorktree "blocked"
    Remove-Item Env:AI_STACK_REGISTRY_LOCK_TIMEOUT_SEC -ErrorAction SilentlyContinue
}
finally {
    $p = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    try { $null = & git -C $repo worktree prune 2>&1 } finally { $ErrorActionPreference = $p }
    Remove-Item -Recurse -Force $root -ErrorAction SilentlyContinue
}
if ($fail) { Write-Host "RESULT: RED ($fail scenario(s) failed)" -ForegroundColor Red; exit 1 }
Write-Host "RESULT: GREEN" -ForegroundColor Green
exit 0
