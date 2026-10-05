param([string]$Tip = '', [string]$Harness = '', [Parameter(Mandatory)][string]$Scratch, [string[]]$Only = @())
# cf-watchdog / ef-watchdog: one-line mutations of the watchdog under test, each
# removing one rule. Each must turn the named pure-part case red. Pure part only:
# DOCKER_HOST is forced to a dead endpoint by the harness; no daemon is touched.
# Mutants whose case is not P29 run with -SkipSim (the harness then skips only
# the ~4-minute 42-loop simulation); P29 mutants run the full pure part.
# Usage (repo root):
#   powershell -NoProfile -File scripts\checks\test-watchdog-loops-mutants.ps1 -Scratch <empty dir> [-Only name,name]
# (-Tip / -Harness default to the sibling stack-watchdog.ps1 / test-watchdog-loops.ps1.)
# Moved here from the plan store's cf-watchdog-mutants.ps1 by item ef-watchdog,
# which added the P22/P30-P35 mutants at the end of the table.
$Only = @($Only | ForEach-Object { $_ -split ',' } | Where-Object { $_ })   # -File passes a comma list as ONE string
if (-not $Tip) { $Tip = Join-Path $PSScriptRoot 'stack-watchdog.ps1' }
if (-not $Harness) { $Harness = Join-Path $PSScriptRoot 'test-watchdog-loops.ps1' }
if (-not (Test-Path $Scratch)) { New-Item -ItemType Directory -Path $Scratch -Force | Out-Null }
$src =[IO.File]::ReadAllText($Tip).Replace("`r`n", "`n")   # a blob from git cat-file is LF; the checkout is CRLF
$mutants = [ordered]@{
    # --- detection -----------------------------------------------------------
    'netns-order'            = @('if ($ownerStart -gt $joinerStart)', 'if ($ownerStart -lt $joinerStart)', 'P3')
    'threshold'              = @('$RestartLoopThreshold = 3', '$RestartLoopThreshold = 300', 'P1')
    'cooldown'               = @('if (((Get-Date) - (Get-Item $sentinel).LastWriteTime).TotalHours -lt $LoopAlertCooldownHours) {', 'if ($false) {', 'P4')
    'joiner-running'         = @("if (`$f.Status -ne 'running') { continue }", "if (`$false) { continue }", 'P8')
    'owner-status'           = @("if (`$owner.Status -notin @('running', 'paused')) {", "if (`$false) {", 'P9')
    'window-rule'            = @('} elseif ($inWindow -ge $SlowLoopThreshold) {', '} elseif ($false) {', 'P10')
    'window-prune'           = @('$he -ge ($nowEpoch - [int64]($SlowLoopWindowHours * 3600))', '$true', 'P10')
    'future-prune'           = @(' -and $he -le ($nowEpoch + 300)', '', 'P13')
    'future-month'           = @('$he -le ($nowEpoch + 300)', '$he -le ($nowEpoch + 2592000)', 'P13')
    'count-nonpos-kept'      = @('[int]::TryParse($hp[1], [ref]$hn) -and $hn -gt 0 -and', '[int]::TryParse($hp[1], [ref]$hn) -and', 'P13')
    'carry-hist'             = @('            Hist   = @($prev[$k].Hist)', '            Hist   = @()', 'P14')
    # --- settle and all-clear --------------------------------------------------
    'settled'                = @("(`$f.Status -ne 'running' -or (`$started -and", "(`$true -or (`$started -and", 'P7')
    'settle-10min'           = @('$LoopSettledMinutes = 60', '$LoopSettledMinutes = 10', 'P7')
    'settle-no-delta'        = @('$settled = ($delta -le 0) -and ($f.Status', '$settled = ($f.Status', 'P21')
    'settle-no-restarting'   = @("-and (`$f.Status -ne 'restarting') -and`n", "-and`n", 'P7')
    'settle-wipes-history'   = @('        $started = ConvertTo-UtcInstant $f.StartedAt', '        $started = ConvertTo-UtcInstant $f.StartedAt; if (($delta -le 0) -and $started -and ($nowUtc - $started).TotalMinutes -ge $LoopSettledMinutes) { $hist = @() }', 'P18')
    'settled-after-window'   = @('        } elseif ($settled) {', '        } elseif ($inWindow -ge $SlowLoopThreshold) { $looping += [pscustomobject]@{ Fact = $f; Accum = $accum; Streak = $streak; Window = $inWindow } } elseif ($settled) {', 'P12')
    'adaptive-settle'        = @('[Math]::Min($SlowLoopWindowHours * 60.0, $LoopSettleGapFactor * $maxGapMin))', '0.0)', 'P29')
    'gap-factor-1'           = @('$LoopSettleGapFactor = 3', '$LoopSettleGapFactor = 1', 'P29')
    'settle-real-clock'      = @('($nowUtc - $started).TotalMinutes -ge $settleMin', '((Get-Date).ToUniversalTime() - $started).TotalMinutes -ge $settleMin', 'P29')
    'maxgap-not-carried'     = @('            MaxGap = $prev[$k].MaxGap', '            MaxGap = 0', 'P27')
    'maxgap-not-reset'       = @('                $maxGapMin = 0.0   # a relapse starts a new loop with its own gaps', '                $null = 0', 'P27')
    'lastobs-not-reset'      = @("                `$lastObs = 0`n", "", 'P27')
    'clearedat-window'       = @('if ([int64]$hp2[0] -gt $cleared) {', 'if ($true) {', 'P12')
    'cleared-ge'             = @('if ([int64]$hp2[0] -gt $cleared) {', 'if ([int64]$hp2[0] -ge $cleared) {', 'P26')
    'clearedat-not-set'      = @("                `$cleared = `$nowEpoch`n", "                `$null = 0`n", 'P12')
    'carry-cleared-dropped'  = @('            ClearedAt = $prev[$k].ClearedAt', '            ClearedAt = 0', 'P25')
    'future-cleared-kept'    = @('elseif ($cleared -gt ($nowEpoch + 300)) { $cleared = $nowEpoch }', 'elseif ($false) { }', 'P26')
    'clear-keeps-cooldown'   = @('    foreach ($p in @($sentinel, (Join-Path $PROJECT_DIR "logs\.tg-alert-$Key"))) {', '    foreach ($p in @()) {', 'P16')
    'resolve-keeps-tgalert'  = @('foreach ($p in @($sentinel, (Join-Path $PROJECT_DIR "logs\.tg-alert-$Key"))) {', 'foreach ($p in @($sentinel)) {', 'P16')
    'resolve-keeps-sentinel' = @('foreach ($p in @($sentinel, (Join-Path $PROJECT_DIR "logs\.tg-alert-$Key"))) {', 'foreach ($p in @((Join-Path $PROJECT_DIR "logs\.tg-alert-$Key"))) {', 'P16')
    'resolve-unconditional'  = @('    if (-not (Test-Path $sentinel)) { return $false }', '    $null = 0', 'P18')
    'resolve-returns-false'  = @("    Write-LogEntry `"LOOP [`$Key] cleared: `$Message`" `"SUCCESS`"`n    return `$true", "    Write-LogEntry `"LOOP [`$Key] cleared: `$Message`" `"SUCCESS`"`n    return `$false", 'P12')
    'unreadable-loop-resolve' = @("Resolve-Catastrophe -Key 'docker-unreadable' -Message", "`$null = Resolve-LoopAlert -Key 'docker-unreadable' -Message", 'P22')
    'netns-settle-dropped'   = @('if ((($nowNs - $joinerStart).TotalMinutes -ge $LoopSettledMinutes) -and (($nowNs - $ownerStart).TotalMinutes -ge $LoopSettledMinutes)) {', 'if ($true) {', 'P23')
    'netns-old-resolve'      = @('Resolve-LoopAlert -Key ("netns-" + $f.Name)', 'Resolve-Catastrophe -Key ("netns-" + $f.Name)', 'P24')
    # --- bounded calls and the job object -------------------------------------
    'job-assign'             = @('[AiStackWatchdogJob]::Assign($job, $proc.Handle)', '$false', 'P11')
    'job-kill-on-close'      = @('e.BasicLimit.LimitFlags = 0x2000;', 'e.BasicLimit.LimitFlags = 0;', 'P11')
    'job-close'              = @('if ($job -ne [IntPtr]::Zero) { try { [AiStackWatchdogJob]::Close($job) } catch { } }', '$null = 0', 'P11')
    'no-job-at-all'          = @('$WatchdogUseJobObject = $true', '$WatchdogUseJobObject = $false', 'P11')
    'assign-fail-noclose'    = @('                [AiStackWatchdogJob]::Close($job); $job = [IntPtr]::Zero', '                $job = [IntPtr]::Zero', 'P19')
    'fail-seam-ignored'      = @('$assigned = (-not $WatchdogFailJobAssign) -and [AiStackWatchdogJob]::Assign($job, $proc.Handle)', '$assigned = [AiStackWatchdogJob]::Assign($job, $proc.Handle)', 'P19')
    'job-after-start'        = @("        `$job = New-WatchdogJob`n        `$sw = [System.Diagnostics.Stopwatch]::StartNew()`n        [void]`$proc.Start()`n", "        `$sw = [System.Diagnostics.Stopwatch]::StartNew()`n        [void]`$proc.Start()`n        `$job = New-WatchdogJob`n", 'P20')
    'no-failure-cache'       = @("        `$script:WatchdogJobUnavailable = `$true`n        Write-LogEntry `"job object unavailable", "        `$null = 0`n        Write-LogEntry `"job object unavailable", 'P28')
    'taskkill-fallback'      = @('if ($job -eq [IntPtr]::Zero) { & taskkill.exe /PID $proc.Id /T /F 2>$null | Out-Null }', '$null = 0', 'P15')
    # --- ef-watchdog: the branches the attempt-5 tester listed as untested ------
    'createjob-no-flag'      = @("            `$script:WatchdogJobUnavailable = `$true`n            Write-LogEntry `"CreateJobObject failed", "            `$null = 0`n            Write-LogEntry `"CreateJobObject failed", 'P30')
    'createjob-no-warn'      = @('            Write-LogEntry "CreateJobObject failed - bounded calls fall back to taskkill /T for the rest of this run" "WARN"', '            $null = 0', 'P30')
    'future-cleared-zero'    = @('elseif ($cleared -gt ($nowEpoch + 300)) { $cleared = $nowEpoch }', 'elseif ($cleared -gt ($nowEpoch + 300)) { $cleared = 0 }', 'P31')
    'unreadable-no-resolve'  = @('            Resolve-Catastrophe -Key ''docker-unreadable'' -Message "docker can describe every container again."', '            $null = 0', 'P22')
    'lastobs-not-carried'    = @('            LastObs = $prev[$k].LastObs', '            LastObs = 0', 'P32')
    'maxgap-nan-kept'        = @('[double]::IsNaN($maxGapMin) -or ', '', 'P33')
    'maxgap-inf-kept'        = @('[double]::IsInfinity($maxGapMin) -or ', '', 'P33')
    # --- ef-watchdog: the credential-shape scrub --------------------------------
    'scrub-url'              = @('$t = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.', '$null = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.', 'P34')
    'scrub-bearer'           = @('$t = [regex]::Replace($t, ''(?i)\b(bearer|basic)', '$null = [regex]::Replace($t, ''(?i)\b(bearer|basic)', 'P34')
    'scrub-vendor'           = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?:sk-', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?:sk-', 'P34')
    'scrub-telegram'         = @('$t = [regex]::Replace($t, ''(?<![0-9])[0-9]{8,10}:', '$null = [regex]::Replace($t, ''(?<![0-9])[0-9]{8,10}:', 'P34')
    'scrub-pem'              = @('$t = [regex]::Replace($t, ''-----BEGIN [A-Z0-9 ]', '$null = [regex]::Replace($t, ''-----BEGIN [A-Z0-9 ]', 'P34')
    'scrub-kv-strong'        = @('$t = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:password', '$null = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:password', 'P34')
    'scrub-kv-weak'          = @('$t = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:key|auth))', '$null = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:key|auth))', 'P34')
    'scrub-opaque'           = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])', 'P34')
    'scrub-colon-always'     = @('if ($sep -notmatch ''='' -and $bare.Length -lt 16 -and $bare -notmatch ''\d'' -and $val -eq $bare) { return $x.Value }', '$null = 0', 'P34')
    'scrub-fail-open'        = @('        return ''(text withheld: the credential scrub failed)''', '        return $Text', 'P34')
    'scrub-fault-line-off'   = @('$collapsed = Hide-CredentialShapes ($pick.Trim() -replace ''\s+'', '' '')', '$collapsed = ($pick.Trim() -replace ''\s+'', '' '')', 'P35')
    'scrub-after-cut'        = @("        `$collapsed = Hide-CredentialShapes (`$pick.Trim() -replace '\s+', ' ')`n        return `$collapsed.Substring(0, [Math]::Min(280, `$collapsed.Length))", "        `$collapsed = `$pick.Trim() -replace '\s+', ' '`n        return (Hide-CredentialShapes `$collapsed.Substring(0, [Math]::Min(280, `$collapsed.Length)))", 'P35')
    'scrub-send-loop-off'    = @('-Message (Hide-CredentialShapes $Message)', '-Message $Message', 'P35')
}
$bad = 0
foreach ($k in $mutants.Keys) {
    if ($Only.Count -gt 0 -and $Only -notcontains $k) { continue }
    $from, $to, $case = $mutants[$k]
    $from = $from.Replace("`r`n", "`n"); $to = $to.Replace("`r`n", "`n")
    $n = ([regex]::Matches($src, [regex]::Escape($from))).Count
    if ($n -ne 1) { "{0,-24} SETUP ERROR: the anchor text occurs {1} times" -f $k, $n; $bad++; continue }
    $p = Join-Path $Scratch "mut-$k.ps1"
    [IO.File]::WriteAllText($p, $src.Replace($from, $to), (New-Object Text.ASCIIEncoding))
    $hargs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Harness, '-Part', 'pure', '-Script', $p)
    if ($case -ne 'P29') { $hargs += '-SkipSim' }
    $res = (& powershell @hargs | Select-String '^RESULT').Line
    $red = $res -match "\b$case FAIL\b"
    if (-not $red) { $bad++ }
    "{0,-24} expects {1,-4} red: {2}   {3}" -f $k, $case, $(if ($red) { 'YES' } else { 'NO ' }), $res
}
"mutants that did NOT turn their case red: $bad"
exit $bad
