# test-restore-runbook-wipe.ps1 - the restore runbook's Invoke-BindRestore, when its wipe fails
# part-way, says the target is partly deleted and to run the same line again (cf-small-fixes G20,
# from the ac-identity-gate review: the raw Remove-Item error was thrown instead).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\checks\test-restore-runbook-wipe.ps1
#   ... -Runbook <path>   to test another copy of the runbook (e.g. the base commit's, to see it RED)
#
# HERMETIC. The two functions are read out of the runbook's own code block, so the test runs the
# text a reader pastes. Resolve-BindTarget is replaced by a stub returning a scratch directory, and
# `docker` is a stand-in .cmd at the front of PATH that only echoes (DOCKER_HOST is also a dead
# endpoint), so nothing here can reach a daemon. A file held open with FileShare.None makes the
# wipe fail part-way. Exit 0 = all passed, 1 = a check failed.
[CmdletBinding()]
param([string]$Runbook = '')
$ErrorActionPreference = 'Continue'
if (-not $Runbook) { $Runbook = Join-Path $PSScriptRoot '..\..\documentation\runbooks\restore-from-snapshot.md' }

$script:pass = 0; $script:fail = 0
function Check($name, $cond, $detail) {
    if ($cond) { $script:pass++; Write-Host "  PASS  $name" -ForegroundColor Green }
    else { $script:fail++; Write-Host "  FAIL  $name  $detail" -ForegroundColor Red }
}

# 1. The runbook's code block that defines both functions, exactly as a reader would paste it.
$md = [IO.File]::ReadAllText((Resolve-Path $Runbook).Path)
$blocks = [regex]::Matches($md, '(?ms)^```powershell\r?\n(.*?)^```')
$code = $null
foreach ($b in $blocks) { if ($b.Groups[1].Value -match 'function Invoke-BindRestore') { $code = $b.Groups[1].Value } }
Check 'the runbook has the code block defining Invoke-BindRestore' ($null -ne $code) $Runbook
if ($null -eq $code) { Write-Host "passed $script:pass, failed $script:fail"; exit 1 }

$root = Join-Path ([IO.Path]::GetTempPath()) ("cf-bindrestore-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$target = Join-Path $root 'target'; $backup = Join-Path $root 'backups'; $bin = Join-Path $root 'bin'
New-Item -ItemType Directory -Force -Path $target, $backup, $bin, (Join-Path $target 'sub') | Out-Null
Set-Content -Path (Join-Path $target 'a.txt') -Value 'a' -Encoding ascii
Set-Content -Path (Join-Path $target 'locked.txt') -Value 'held open' -Encoding ascii
Set-Content -Path (Join-Path $target 'sub\b.txt') -Value 'b' -Encoding ascii
$archive = 'probe-20260928.tar.gz'
[IO.File]::WriteAllBytes((Join-Path $backup $archive), [byte[]](1, 2, 3, 4))
$sha = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([byte[]](1, 2, 3, 4))).Replace('-', '').ToLowerInvariant()
Set-Content -Path (Join-Path $backup "$archive.sha256") -Value "$sha  /backups/$archive" -Encoding ascii
# the docker stand-in: records its argv, exits 0
$calls = Join-Path $root 'docker-calls.txt'
Set-Content -Path (Join-Path $bin 'docker.cmd') -Encoding ascii -Value @('@echo off', "echo %*>>`"$calls`"", 'exit /b 0')

$savedPath = $env:PATH; $savedHost = $env:DOCKER_HOST; $savedLoc = Get-Location
$env:PATH = "$bin;$env:PATH"; $env:DOCKER_HOST = 'tcp://127.0.0.1:1'
$lock = $null
try {
    Set-Location $root
    . ([ScriptBlock]::Create($code))
    # the stub: the resolve is not what is under test, and the real one asks docker
    # (the path is written INTO the stub: a variable would resolve dynamically to the CALLER's
    # $target, which Invoke-BindRestore has just set to $null)
    . ([ScriptBlock]::Create("function Resolve-BindTarget { param(`$Container, `$Destination, `$ComposeFile, `$ComposeProfile, `$Service, `$ShellVar) return '$($target.Replace("'", "''"))' }"))
    $dk = @(Get-Command docker -CommandType Application)[0].Source
    Check 'the docker the function will resolve is the stand-in' ($dk -like "$bin*") $dk

    $lock = [IO.File]::Open((Join-Path $target 'locked.txt'), 'Open', 'Read', 'None')
    $msg = $null
    try {
        Invoke-BindRestore -Container c -Destination /d -ComposeFile x.yml -Service s -BackupDir $backup -Archive $archive -Start c
    } catch { $msg = $_.Exception.Message }
    Check 'a wipe that fails part-way THROWS' ($null -ne $msg) 'no exception'
    Check '  ... and it did fail part-way (a.txt gone, locked.txt still there)' `
        ((-not (Test-Path (Join-Path $target 'a.txt'))) -and (Test-Path (Join-Path $target 'locked.txt'))) ''
    Check '  ... the message says the target is partly deleted and not restored' ($msg -match 'partly deleted and not restored') $msg
    Check '  ... the message says to run this same line again' ($msg -match 'run this same line again') $msg
    Check '  ... the message names the target' ($msg -and $msg.Contains($target)) $msg
    Check '  ... nothing was restored or started (docker never called)' (-not (Test-Path $calls)) ''

    # Following the advice works: free the file, run the SAME line again.
    $lock.Dispose(); $lock = $null
    $msg2 = $null
    try {
        Invoke-BindRestore -Container c -Destination /d -ComposeFile x.yml -Service s -BackupDir $backup -Archive $archive -Start c
    } catch { $msg2 = $_.Exception.Message }
    Check 'the same line, run again after freeing the file, completes' ($null -eq $msg2) $msg2
    Check '  ... the target was wiped' (@(Get-ChildItem -LiteralPath $target -Force).Count -eq 0) ''
    $seen = if (Test-Path $calls) { Get-Content $calls } else { @() }
    Check '  ... then restored and started through docker (run, then start)' `
        ((@($seen | Where-Object { $_ -like 'run --rm*' }).Count -eq 1) -and (@($seen | Where-Object { $_ -eq 'start c' }).Count -eq 1)) ($seen -join ' / ')
} finally {
    if ($lock) { $lock.Dispose() }
    Set-Location $savedLoc
    $env:PATH = $savedPath; $env:DOCKER_HOST = $savedHost
    Remove-Item -LiteralPath $root -Recurse -Force -ErrorAction SilentlyContinue
}
Write-Host ''
Write-Host "passed $script:pass, failed $script:fail"
if ($script:fail -gt 0) { exit 1 }
exit 0
