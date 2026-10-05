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
    [int]$HolderTimeoutSec = 6,
    [int]$Kills = 15
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
        if ($missing.Count) {
            $lost++; Write-Host ("  run {0}: LOST row(s) {1}" -f $r, ($missing -join ",")) -ForegroundColor Red
            foreach ($pp in $procs) { Write-Host ("    child exit={0} err/out tail: {1}" -f $pp.ExitCode, ((($pp.Err.Result + " " + $pp.Out.Result).Trim()) -replace "\s+", " ")) -ForegroundColor DarkRed }
        }
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

    # --- 2b. writer vs a tight reader loop ----------------------------------------------
    # A reader that holds worktrees.json open (Get-Content shares no DELETE) used to make the
    # writer's delete-then-move swap fail, losing the update. One writer does 150 locked
    # updates while a reader hammers the file; every update must land. Drives the helper in
    # the toolkit under test, so a toolkit without lock.ps1 has nothing to run here.
    $lockLib = Join-Path $ToolkitDir "lock.ps1"
    if (Test-Path $lockLib) {
        $rdReg = Join-Path $root "rw\worktrees.json"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $rdReg) | Out-Null
        Set-Content -Path $rdReg -Value '{"worktrees":{}}' -Encoding ASCII
        $stopFile = Join-Path $root "rw\stop"
        $readerPs1 = Join-Path $root "rw-reader.ps1"
        $writerPs1 = Join-Path $root "rw-writer.ps1"
        Set-Content -Path $readerPs1 -Encoding ASCII -Value @(
            "while (-not (Test-Path '$stopFile')) { try { if (Test-Path '$rdReg') { `$null = Get-Content -Raw -Path '$rdReg' } } catch { } }")
        Set-Content -Path $writerPs1 -Encoding ASCII -Value @(
            "`$ErrorActionPreference = 'Stop'",
            ". '$lockLib'",
            "`$e = 0",
            "for (`$i = 0; `$i -lt 150; `$i++) { try { Update-WorktreeRegistry -Registry '$rdReg' -Mutate ({ param(`$r) `$r[('w' + `$i)] = @{ id = 'w' } }.GetNewClosure()) } catch { `$e++ } }",
            "Write-Output ('errors=' + `$e)")
        $rp = Start-Process powershell.exe -ArgumentList @("-NoProfile", "-File", "`"$readerPs1`"") -PassThru -WindowStyle Hidden
        $wpsi = New-Object System.Diagnostics.ProcessStartInfo
        $wpsi.FileName = "powershell.exe"; $wpsi.Arguments = "-NoProfile -File `"$writerPs1`""
        $wpsi.UseShellExecute = $false; $wpsi.RedirectStandardOutput = $true; $wpsi.RedirectStandardError = $true
        $wp = [System.Diagnostics.Process]::Start($wpsi)
        $wOut = $wp.StandardOutput.ReadToEndAsync(); $null = $wp.StandardError.ReadToEndAsync()
        if (-not $wp.WaitForExit(240000)) { $wp.Kill() }
        Set-Content -Path $stopFile -Value "x" -Encoding ASCII
        if (-not $rp.WaitForExit(20000)) { $rp.Kill() }
        $rowsNow = 0
        try { $rowsNow = @((Get-Content -Raw $rdReg | ConvertFrom-Json).worktrees.PSObject.Properties).Count } catch { }
        $okRw = ($wOut.Result -match "errors=0") -and ($rowsNow -eq 150)
        Write-Host ("[writer vs tight reader] 150 updates: {0} final_rows={1}/150 -> {2}" -f $wOut.Result.Trim(), $rowsNow, $(if ($okRw) { "OK" } else { "LOST UPDATES" }))
        if (-not $okRw) { $fail++ }
    } else {
        Write-Host "[writer vs tight reader] n/a - this toolkit has no lock.ps1"
    }

    # --- 2c-2g. helper-level scenarios (need the toolkit's lock.ps1) -------------------
    if (Test-Path $lockLib) {
        function Invoke-PsFile([string]$File, [string[]]$FileArgs, [int]$TimeoutMs = 120000) {
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = "powershell.exe"
            $psi.Arguments = "-NoProfile -NonInteractive -File `"$File`" " + (($FileArgs | ForEach-Object { "`"$_`"" }) -join " ")
            $psi.UseShellExecute = $false; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
            $c = [System.Diagnostics.Process]::Start($psi)
            $o = $c.StandardOutput.ReadToEndAsync(); $e = $c.StandardError.ReadToEndAsync()
            if (-not $c.WaitForExit($TimeoutMs)) { $c.Kill() }
            return [pscustomobject]@{ Out = $o.Result.Trim(); Err = $e.Result.Trim(); Exit = $c.ExitCode }
        }
        function New-RegJson([int]$Count, [string]$Tag) {
            $rows = @{}
            for ($i = 1; $i -le $Count; $i++) { $rows["$Tag$i"] = @{ id = "$Tag$i"; path = "x" } }
            return (@{ worktrees = $rows } | ConvertTo-Json -Depth 5)
        }
        function Get-RowCount([string]$File) {
            try { return @((Get-Content -Raw $File | ConvertFrom-Json).worktrees.PSObject.Properties).Count } catch { return -1 }
        }
        $updPs1 = Join-Path $root "upd.ps1"
        Set-Content -Path $updPs1 -Encoding ASCII -Value @(
            "param([string]`$Reg, [string]`$Id)",
            "`$ErrorActionPreference = 'Stop'",
            ". '$lockLib'",
            "try { Update-WorktreeRegistry -Registry `$Reg -Mutate ({ param(`$r) `$r[`$Id] = @{ id = `$Id; path = 'x' } }.GetNewClosure()); Write-Output 'ok' }",
            "catch { Write-Output ('ERR ' + `$_.Exception.Message); exit 1 }")

        # 2c. Recovery of a registry stranded by a writer killed between delete and move. The
        # killed writer leaves NO worktrees.json: finished content in .tmp, older in ~RF*.TMP.
        $rc = Join-Path $root "rec"; New-Item -ItemType Directory -Force -Path $rc | Out-Null
        $rcReg = Join-Path $rc "worktrees.json"
        Set-Content -Path "$rcReg~RF1a2b3c.TMP" -Value (New-RegJson 30 "o") -Encoding ASCII
        (Get-Item "$rcReg~RF1a2b3c.TMP").LastWriteTimeUtc = [DateTime]::UtcNow.AddMinutes(-5)
        Set-Content -Path "$rcReg.tmp" -Value (New-RegJson 40 "n") -Encoding ASCII
        $r1 = Invoke-PsFile $updPs1 @($rcReg, "added")
        $n1 = Get-RowCount $rcReg
        $okA = ($r1.Exit -eq 0) -and ($n1 -eq 41)
        # unusable leftovers: refuse, name the files, destroy nothing, write nothing
        Remove-Item $rcReg -Force -ErrorAction SilentlyContinue
        Get-ChildItem $rc -Filter "worktrees.json~RF*.TMP" | Remove-Item -Force
        Set-Content -Path "$rcReg.tmp" -Value "{ not json" -Encoding ASCII
        $r2 = Invoke-PsFile $updPs1 @($rcReg, "added")
        $okB = ($r2.Exit -ne 0) -and ($r2.Out -match "worktrees\.json\.tmp") -and (-not (Test-Path $rcReg)) -and (Test-Path "$rcReg.tmp")
        # a true first write (no registry, no leftovers): a fresh start is right
        Remove-Item "$rcReg.tmp" -Force -ErrorAction SilentlyContinue
        $r3 = Invoke-PsFile $updPs1 @($rcReg, "first")
        $okC = ($r3.Exit -eq 0) -and ((Get-RowCount $rcReg) -eq 1)
        Write-Host ("[stranded registry recovery] recovered_rows={0}/41 -> {1}; unusable leftovers refused naming file -> {2}; true first write -> {3}" -f $n1, $(if ($okA) { "OK" } else { "LOST ROWS" }), $(if ($okB) { "OK" } else { "FAIL" }), $(if ($okC) { "OK" } else { "FAIL" }))
        if (-not ($okA -and $okB -and $okC)) { $fail++ }

        # 2d. Real kills mid-update with a tight reader running (the reader widens the
        # kill-between-delete-and-move window). After each kill the next update must keep every
        # row the dead writer had committed (it logs its count after each completed update).
        $kdir = Join-Path $root "kill"; New-Item -ItemType Directory -Force -Path $kdir | Out-Null
        $kReg = Join-Path $kdir "worktrees.json"; $kProg = Join-Path $kdir "progress.txt"; $kStop = Join-Path $kdir "stop"
        $kWriter = Join-Path $root "kwriter.ps1"; $kReader = Join-Path $root "kreader.ps1"
        Set-Content -Path $kWriter -Encoding ASCII -Value @(
            "param([string]`$Reg, [string]`$Prog)", ". '$lockLib'", "`$i = 0",
            "while (`$true) { `$i++; Update-WorktreeRegistry -Registry `$Reg -Mutate ({ param(`$r) `$r[('k' + `$i)] = @{ id = 'k'; path = 'x' } }.GetNewClosure()); Set-Content -Path `$Prog -Value `$i -Encoding ASCII }")
        Set-Content -Path $kReader -Encoding ASCII -Value @(
            "param([string]`$Reg, [string]`$Stop)",
            "while (-not (Test-Path `$Stop)) { try { if (Test-Path `$Reg) { `$null = Get-Content -Raw -Path `$Reg } } catch { } }")
        $kRd = Start-Process powershell.exe -ArgumentList @("-NoProfile", "-File", "`"$kReader`"", "`"$kReg`"", "`"$kStop`"") -PassThru -WindowStyle Hidden
        $shrunk = 0; $missingAfterKill = 0
        for ($k = 1; $k -le $Kills; $k++) {
            # The scenario's own tight reader holds the registry open without FILE_SHARE_DELETE, so
            # the reset can fail for a moment: retry it rather than die (or silently keep old state).
            for ($rt = 0; $rt -lt 400; $rt++) {
                try {
                    Get-ChildItem $kdir -Filter "worktrees.json*" | Remove-Item -Force -ErrorAction Stop
                    Set-Content -Path $kReg -Value (New-RegJson 40 "s") -Encoding ASCII -ErrorAction Stop
                    break
                } catch { Start-Sleep -Milliseconds 25 }
            }
            Set-Content -Path $kProg -Value 0 -Encoding ASCII
            $psiK = New-Object System.Diagnostics.ProcessStartInfo
            $psiK.FileName = "powershell.exe"; $psiK.Arguments = "-NoProfile -NonInteractive -File `"$kWriter`" `"$kReg`" `"$kProg`""
            $psiK.UseShellExecute = $false; $psiK.CreateNoWindow = $true
            $kw = [System.Diagnostics.Process]::Start($psiK)
            Start-Sleep -Milliseconds (900 + (Get-Random -Maximum 1200))
            if (-not $kw.HasExited) { try { $kw.Kill() } catch { } }; $null = $kw.WaitForExit(10000)
            $done = 0; try { $done = [int](Get-Content -Raw $kProg).Trim() } catch { }
            if (-not (Test-Path $kReg)) { $missingAfterKill++ }
            $ru = Invoke-PsFile $updPs1 @($kReg, "after$k")
            $nAfter = Get-RowCount $kReg
            if (($ru.Exit -ne 0) -or ($nAfter -lt (40 + $done + 1))) {
                $shrunk++; Write-Host ("  kill {0}: committed>={1} rows, after next update {2} ({3})" -f $k, (40 + $done), $nAfter, $ru.Out) -ForegroundColor Red
            }
        }
        Set-Content -Path $kStop -Value "x" -Encoding ASCII
        if (-not $kRd.WaitForExit(20000)) { $kRd.Kill() }
        Write-Host ("[killed writer + reader] kills={0} registry_absent_after_kill={1} runs_that_lost_rows={2}" -f $Kills, $missingAfterKill, $shrunk)
        if ($shrunk) { $fail++ }

        # 2e. A harness reader (Read-RegistryJson, what queue.ps1 and sync-worktree-env.ps1 use)
        # rides out a short exclusive holder instead of giving up at once.
        $he = Join-Path $root "hold"; New-Item -ItemType Directory -Force -Path $he | Out-Null
        $heReg = Join-Path $he "worktrees.json"
        Set-Content -Path $heReg -Value (New-RegJson 3 "h") -Encoding ASCII
        $heHold = Join-Path $root "hold.ps1"; $heRead = Join-Path $root "hread.ps1"
        Set-Content -Path $heHold -Encoding ASCII -Value @(
            "param([string]`$Reg)", "`$f = New-Object System.IO.FileStream(`$Reg, 'Open', 'ReadWrite', 'None')", "Start-Sleep -Milliseconds 1500", "`$f.Dispose()")
        Set-Content -Path $heRead -Encoding ASCII -Value @(
            "param([string]`$Reg)", ". '$lockLib'", "`$o = Read-RegistryJson -Path `$Reg",
            "if (`$o -and `$o.worktrees) { Write-Output 'ok' } else { Write-Output 'null' }")
        $psiH = New-Object System.Diagnostics.ProcessStartInfo
        $psiH.FileName = "powershell.exe"; $psiH.Arguments = "-NoProfile -NonInteractive -File `"$heHold`" `"$heReg`""
        $psiH.UseShellExecute = $false; $psiH.CreateNoWindow = $true
        $hp = [System.Diagnostics.Process]::Start($psiH)
        for ($i = 0; $i -lt 100; $i++) {
            try { $t = [System.IO.File]::Open($heReg, 'Open', 'Read', 'ReadWrite'); $t.Dispose(); Start-Sleep -Milliseconds 20 } catch { break }
        }
        $rh = Invoke-PsFile $heRead @($heReg)
        $null = $hp.WaitForExit(10000)
        $okH = ($rh.Out -eq "ok")
        Write-Host ("[harness reader vs short exclusive holder] Read-RegistryJson -> {0} -> {1}" -f $rh.Out, $(if ($okH) { "OK" } else { "GAVE UP" }))
        if (-not $okH) { $fail++ }

        # 2f. The harness reader opens with FileShare.Delete: with the swap retry switched OFF
        # (swap timeout 0) a writer must still never fail against a tight harness-reader loop.
        # A reader without FILE_SHARE_DELETE would break the writer's delete-then-move here.
        $sd = Join-Path $root "sd"; New-Item -ItemType Directory -Force -Path $sd | Out-Null
        $sdReg = Join-Path $sd "worktrees.json"; $sdStop = Join-Path $sd "stop"
        Set-Content -Path $sdReg -Value '{"worktrees":{}}' -Encoding ASCII
        $sdReader = Join-Path $root "sdreader.ps1"; $sdWriter = Join-Path $root "sdwriter.ps1"
        Set-Content -Path $sdReader -Encoding ASCII -Value @(
            "param([string]`$Reg, [string]`$Stop)", ". '$lockLib'",
            "while (-not (Test-Path `$Stop)) { `$null = Read-RegistryJson -Path `$Reg -TimeoutSec 1 }")
        Set-Content -Path $sdWriter -Encoding ASCII -Value @(
            "param([string]`$Reg)", ". '$lockLib'", "`$e = 0",
            "for (`$i = 0; `$i -lt 150; `$i++) { try { Update-WorktreeRegistry -Registry `$Reg -Mutate ({ param(`$r) `$r[('d' + `$i)] = @{ id = 'd' } }.GetNewClosure()) } catch { `$e++ } }",
            "Write-Output ('errors=' + `$e)")
        $sdRd = Start-Process powershell.exe -ArgumentList @("-NoProfile", "-File", "`"$sdReader`"", "`"$sdReg`"", "`"$sdStop`"") -PassThru -WindowStyle Hidden
        $env:AI_STACK_REGISTRY_SWAP_TIMEOUT_SEC = "0"
        $rsd = Invoke-PsFile $sdWriter @($sdReg) 240000
        Remove-Item Env:AI_STACK_REGISTRY_SWAP_TIMEOUT_SEC -ErrorAction SilentlyContinue
        Set-Content -Path $sdStop -Value "x" -Encoding ASCII
        if (-not $sdRd.WaitForExit(20000)) { $sdRd.Kill() }
        $nsd = Get-RowCount $sdReg
        $okSd = ($rsd.Out -match "errors=0") -and ($nsd -eq 150)
        Write-Host ("[writer (no swap retry) vs harness-reader loop] {0} final_rows={1}/150 -> {2}" -f $rsd.Out, $nsd, $(if ($okSd) { "OK" } else { "READER BLOCKED THE SWAP" }))
        if (-not $okSd) { $fail++ }

        # 2g. -Mutate cannot bind to the helper's variables: a closure sees the caller's
        # $n/$dir/$fs/$deadline, and a plain scriptblock is refused.
        $cl = Join-Path $root "closure.ps1"
        Set-Content -Path $cl -Encoding ASCII -Value @(
            "param([string]`$Reg)", ". '$lockLib'", "`$n = 99; `$dir = 'CALLER'; `$fs = 'CALLER'; `$deadline = 'CALLER'",
            "Update-WorktreeRegistry -Registry `$Reg -Mutate ({ param(`$r) `$r['c'] = @{ n = `$n; dir = `$dir; fs = `$fs; dl = `$deadline } }.GetNewClosure())",
            "`$plain = 'accepted'; try { Update-WorktreeRegistry -Registry `$Reg -Mutate { param(`$r) } } catch { `$plain = 'refused' }",
            "`$row = (Get-Content -Raw `$Reg | ConvertFrom-Json).worktrees.c",
            "Write-Output ('{0}|{1}|{2}|{3}|{4}' -f `$row.n, `$row.dir, `$row.fs, `$row.dl, `$plain)")
        $rcl = Invoke-PsFile $cl @((Join-Path $root "cl\worktrees.json"))
        $okCl = ($rcl.Out -eq "99|CALLER|CALLER|CALLER|refused")
        Write-Host ("[-Mutate scope] {0} -> {1}" -f $rcl.Out, $(if ($okCl) { "OK" } else { "BOUND TO HELPER LOCALS" }))
        if (-not $okCl) { $fail++ }

        # 2h. Leftover handling, hand-built states (deterministic).
        $lf = Join-Path $root "left"; New-Item -ItemType Directory -Force -Path $lf | Out-Null
        $lfReg = Join-Path $lf "worktrees.json"
        function Reset-Left { Get-ChildItem $lf -Force | Remove-Item -Force -Recurse -ErrorAction SilentlyContinue }
        # (a) a NEWER leftover that does not parse is skipped in favour of an older good one: the
        # warning names the skipped file and its bytes are kept (a rollback is never silent).
        Reset-Left
        Set-Content -Path "$lfReg~RF9a9a9a.TMP" -Value (New-RegJson 5 "g") -Encoding ASCII
        (Get-Item "$lfReg~RF9a9a9a.TMP").LastWriteTimeUtc = [DateTime]::UtcNow.AddDays(-3)
        Set-Content -Path "$lfReg.tmp" -Value '{ "worktrees": { "x": ' -Encoding ASCII
        $ra = Invoke-PsFile $updPs1 @($lfReg, "added")
        $keptBad = @(Get-ChildItem $lf -Filter "worktrees.json.tmp.bad-*").Count
        $okA2 = ($ra.Exit -eq 0) -and ((Get-RowCount $lfReg) -eq 6) -and ($ra.Out -match "skipped") -and ($ra.Out -match "worktrees\.json\.tmp\.bad-") -and ($keptBad -eq 1)
        Write-Host ("[rollback visible] rows={0}/6 warn_names_skipped={1} skipped_bytes_kept={2} -> {3}" -f (Get-RowCount $lfReg), ($ra.Out -match "skipped"), $keptBad, $(if ($okA2) { "OK" } else { "FAIL" }))
        if (-not $okA2) { $fail++ }
        # (b) parseable JSON of the wrong shape is not a registry: refused when absent (nothing
        # written), set aside + fresh registry with NO junk rows when present.
        $shapes = @('{"worktrees":"a string"}', '{"worktrees":[1,2,3]}', '{"worktrees":42}', '{"worktrees":null}', '{"worktrees":{"a":"notobj"}}', '[1,2]')
        $badShape = 0
        foreach ($sh in $shapes) {
            Reset-Left
            Set-Content -Path "$lfReg.tmp" -Value $sh -Encoding ASCII
            $rb = Invoke-PsFile $updPs1 @($lfReg, "added")
            if (-not (($rb.Exit -ne 0) -and (-not (Test-Path $lfReg)))) { $badShape++; Write-Host ("  absent+leftover {0}: not refused" -f $sh) -ForegroundColor Red }
            Reset-Left
            Set-Content -Path $lfReg -Value $sh -Encoding ASCII
            $rb = Invoke-PsFile $updPs1 @($lfReg, "added")
            $badFiles = @(Get-ChildItem $lf -Filter "worktrees.json.bad-*").Count
            if (-not (($rb.Exit -eq 0) -and ((Get-RowCount $lfReg) -eq 1) -and ($badFiles -eq 1))) { $badShape++; Write-Host ("  present {0}: rows={1} bad_files={2}" -f $sh, (Get-RowCount $lfReg), $badFiles) -ForegroundColor Red }
        }
        Write-Host ("[wrong-shape registry] cases={0} mishandled={1}" -f ($shapes.Count * 2), $badShape)
        if ($badShape) { $fail++ }
        # (c) an unreadable present registry is set aside under a timestamped name and a second
        # incident never overwrites the first one's copy.
        Reset-Left
        Set-Content -Path $lfReg -Value "first { corrupt" -Encoding ASCII
        $null = Invoke-PsFile $updPs1 @($lfReg, "one")
        Set-Content -Path $lfReg -Value "second { corrupt" -Encoding ASCII
        $null = Invoke-PsFile $updPs1 @($lfReg, "two")
        $bads = @(Get-ChildItem $lf -Filter "worktrees.json.bad-*")
        $contents = @($bads | ForEach-Object { (Get-Content -Raw $_.FullName).Trim() })
        $okC2 = ($bads.Count -eq 2) -and ($contents -contains "first { corrupt") -and ($contents -contains "second { corrupt")
        Write-Host ("[set-aside copies] files={0} both_incidents_kept={1} -> {2}" -f $bads.Count, (($contents -contains "first { corrupt") -and ($contents -contains "second { corrupt")), $(if ($okC2) { "OK" } else { "FAIL" }))
        if (-not $okC2) { $fail++ }
        # (d) ~RF leftovers of THIS registry beside a present registry are cleaned after a good
        # swap; another file's ~RF is left alone.
        Reset-Left
        Set-Content -Path $lfReg -Value (New-RegJson 3 "p") -Encoding ASCII
        Set-Content -Path "$lfReg~RF111111.TMP" -Value (New-RegJson 9 "z") -Encoding ASCII
        Set-Content -Path "$lfReg~RF222222.TMP" -Value "garbage" -Encoding ASCII
        Set-Content -Path (Join-Path $lf "other.json~RF333333.TMP") -Value "keep me" -Encoding ASCII
        $rd = Invoke-PsFile $updPs1 @($lfReg, "added")
        $okD = ($rd.Exit -eq 0) -and ((Get-RowCount $lfReg) -eq 4) -and (-not (Test-Path "$lfReg~RF111111.TMP")) -and (-not (Test-Path "$lfReg~RF222222.TMP")) -and (Test-Path (Join-Path $lf "other.json~RF333333.TMP"))
        Write-Host ("[~RF cleanup] rows={0}/4 own_cleaned={1} other_left={2} -> {3}" -f (Get-RowCount $lfReg), ((-not (Test-Path "$lfReg~RF111111.TMP")) -and (-not (Test-Path "$lfReg~RF222222.TMP"))), (Test-Path (Join-Path $lf "other.json~RF333333.TMP")), $(if ($okD) { "OK" } else { "FAIL" }))
        if (-not $okD) { $fail++ }

        # 2i. Double fault: a writer killed mid-swap left NO registry (rows only in .tmp), then the
        # NEXT writer is killed the moment it first touches that .tmp (truncated, or moved away).
        # The rows must still be recoverable by the writer after that.
        $dfDir = Join-Path $root "dfault"; New-Item -ItemType Directory -Force -Path $dfDir | Out-Null
        $dfReg = Join-Path $dfDir "worktrees.json"
        $dfRows = 12000; $dfTries = 3; $dfLost = 0; $dfKilled = 0
        $big = @{}
        for ($i = 1; $i -le $dfRows; $i++) { $big["row$i"] = @{ id = "row$i"; path = ("C:\very\long\path\" + ("q" * 200) + $i); branch = "work/row$i" } }
        $bigJson = (@{ worktrees = $big } | ConvertTo-Json -Depth 5)
        for ($t = 1; $t -le $dfTries; $t++) {
            Get-ChildItem $dfDir -Force | Remove-Item -Force -ErrorAction SilentlyContinue
            Set-Content -Path "$dfReg.tmp" -Value $bigJson -Encoding ASCII
            $len0 = (Get-Item "$dfReg.tmp").Length
            $psiD = New-Object System.Diagnostics.ProcessStartInfo
            $psiD.FileName = "powershell.exe"; $psiD.Arguments = "-NoProfile -NonInteractive -File `"$updPs1`" `"$dfReg`" `"B$t`""
            $psiD.UseShellExecute = $false; $psiD.CreateNoWindow = $true
            $dw = [System.Diagnostics.Process]::Start($psiD)
            $fi = New-Object System.IO.FileInfo("$dfReg.tmp")
            $swd = [Diagnostics.Stopwatch]::StartNew()
            while (-not $dw.HasExited -and $swd.Elapsed.TotalSeconds -lt 120) {
                $fi.Refresh()
                if ((-not $fi.Exists) -or ($fi.Length -lt $len0)) { if (-not $dw.HasExited) { try { $dw.Kill(); $dfKilled++ } catch { } }; break }
            }
            $null = $dw.WaitForExit(10000)
            $rn = Invoke-PsFile $updPs1 @($dfReg, "C$t") 240000
            $got = Get-RowCount $dfReg
            if (($rn.Exit -ne 0) -or ($got -lt $dfRows)) { $dfLost++; Write-Host ("  try {0}: next writer exit={1} rows={2} (expected >= {3}) {4}" -f $t, $rn.Exit, $got, $dfRows, $rn.Out.Substring(0, [Math]::Min(100, $rn.Out.Length))) -ForegroundColor Red }
        }
        Write-Host ("[double fault] tries={0} killed_mid_recovery={1} tries_that_lost_rows={2}" -f $dfTries, $dfKilled, $dfLost)
        if ($dfLost) { $fail++ }
    }

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
