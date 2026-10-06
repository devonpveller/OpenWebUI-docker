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
    'maxgap-not-carried'     = @('            MaxGap = (ConvertTo-GapMinutes $prev[$k].MaxGap)', '            MaxGap = 0', 'P27')
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
    'maxgap-nan-kept'        = @('[double]::IsNaN($v) -or ', '', 'P33')
    'maxgap-inf-kept'        = @('[double]::IsInfinity($v) -or ', '', 'P33')
    'maxgap-carry-raw'       = @('            MaxGap = (ConvertTo-GapMinutes $prev[$k].MaxGap)', '            MaxGap = $prev[$k].MaxGap', 'P33')
    # --- ef-watchdog: the credential-shape scrub --------------------------------
    'scrub-url'              = @('$t = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.\-]{0,31}://)[^\s/]{1,256}@''', '$null = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.\-]{0,31}://)[^\s/]{1,256}@''', 'P34')
    'scrub-bearer'           = @('$t = [regex]::Replace($t, ''(?i)\b(bearer|basic)\s+([A-Za-z0-9', '$null = [regex]::Replace($t, ''(?i)\b(bearer|basic)\s+([A-Za-z0-9', 'P34')
    'scrub-vendor'           = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?:sk-', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?:sk-', 'P34')
    'scrub-telegram'         = @('$t = [regex]::Replace($t, ''(?<![0-9])[0-9]{8,10}:', '$null = [regex]::Replace($t, ''(?<![0-9])[0-9]{8,10}:', 'P34')
    'scrub-pem'              = @('$t = [regex]::Replace($t, ''-----BEGIN [A-Z0-9 ]', '$null = [regex]::Replace($t, ''-----BEGIN [A-Z0-9 ]', 'P34')
    'scrub-kv-strong'        = @('$t = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:password', '$null = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:password', 'P34')
    'scrub-kv-weak'          = @('$t = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:key|auth))', '$null = [regex]::Replace($t, ''(?i)(?<![A-Za-z0-9])([A-Za-z0-9_.\-]{0,40}?(?:key|auth))', 'P34')
    'scrub-opaque'           = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_\-])(?=[A-Za-z0-9_\-]*[0-9])', 'P34')
    'scrub-fail-open'        = @('        return ''(text withheld: the credential scrub failed)''', '        return $Text', 'P34')
    'scrub-fault-line-off'   = @('$collapsed = Hide-CredentialShapes ($pick.Trim() -replace ''\s+'', '' '')', '$collapsed = ($pick.Trim() -replace ''\s+'', '' '')', 'P35')
    'scrub-after-cut'        = @("        `$collapsed = Hide-CredentialShapes (`$pick.Trim() -replace '\s+', ' ')`n        return `$collapsed.Substring(0, [Math]::Min(280, `$collapsed.Length))", "        `$collapsed = `$pick.Trim() -replace '\s+', ' '`n        return (Hide-CredentialShapes `$collapsed.Substring(0, [Math]::Min(280, `$collapsed.Length)))", 'P35')
    'scrub-send-loop-off'    = @("    `$Message = Hide-CredentialShapes `$Message`n", '', 'P35')
    'scrub-url-slash'        = @('$t = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.\-]{0,31}://)[^\s/@:]{0,128}:', '$null = [regex]::Replace($t, ''(?i)\b([a-z][a-z0-9+.\-]{0,31}://)[^\s/@:]{0,128}:', 'P34')
    'scrub-url-no-at'        = @('://)[^\s/]{1,256}@'', (''${1}'' + $m + ''@''))', '://)[^\s/@]{1,256}@'', (''${1}'' + $m + ''@''))', 'P34')
    'scrub-url-schemeless'   = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_.:/@\[\-])[A-Za-z0-9._\-]{1,64}:', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_.:/@\[\-])[A-Za-z0-9._\-]{1,64}:', 'P34')
    'scrub-auth-header'      = @('$t = [regex]::Replace($t, ''(?i)(\b(?:proxy-)?authorization\b', '$null = [regex]::Replace($t, ''(?i)(\b(?:proxy-)?authorization\b', 'P34')
    'scrub-cli-flag'         = @('$t = [regex]::Replace($t, ''(?i)((?<!\S)--[a-z0-9\-]{0,30}', '$null = [regex]::Replace($t, ''(?i)((?<!\S)--[a-z0-9\-]{0,30}', 'P34')
    'scrub-mysql-p'          = @('$t = [regex]::Replace($t, ''(\bmysql(?:dump|admin)?', '$null = [regex]::Replace($t, ''(\bmysql(?:dump|admin)?', 'P34')
    'scrub-docker-login'     = @('$t = [regex]::Replace($t, ''(?i)(\b(?:docker|podman|helm)', '$null = [regex]::Replace($t, ''(?i)(\b(?:docker|podman|helm)', 'P34')
    'scrub-curl-u'           = @('$t = [regex]::Replace($t, ''((?<!\S)(?:--user=|--user', '$null = [regex]::Replace($t, ''((?<!\S)(?:--user=|--user', 'P34')
    'scrub-slack'            = @('$t = [regex]::Replace($t, ''(?i)(hooks\.slack\.com', '$null = [regex]::Replace($t, ''(?i)(hooks\.slack\.com', 'P34')
    'scrub-discord'          = @('$t = [regex]::Replace($t, ''(?i)(discord', '$null = [regex]::Replace($t, ''(?i)(discord', 'P34')
    'scrub-pem-rsa-only'     = @('''-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----.*''', ('''-----BEGIN RSA PRIVATE ' + 'KEY-----.*'''), 'P34')
    'scrub-kv-continue'      = @('|[;,](?!\s*[A-Za-z_][A-Za-z0-9_.\- ]{0,30}\s*[=:])', '', 'P34')
    'scrub-pwd-key'          = @('passphrase|pwd|pass|secret', 'passphrase|pass|secret', 'P34')
    'scrub-pass-key'         = @('passphrase|pwd|pass|secret', 'passphrase|pwd|secret', 'P34')
    'scrub-pass-word-bound'  = @('if (($key -imatch ''pass$'') -and ($key -inotmatch', 'if ($false -and ($key -inotmatch', 'P34')
    'scrub-bool-skip'        = @('if ($bare -imatch ''^(?:true|false|null|none|nil)$'') { return $x.Value }', '$null = 0', 'P34')
    'scrub-oracle'           = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9_.:/@\[\-])[A-Za-z][A-Za-z0-9_$#]{0,29}/', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9_.:/@\[\-])[A-Za-z][A-Za-z0-9_$#]{0,29}/', 'P34')
    'scrub-curl-user-eq'     = @('(?:--user=|--user[ \t]+|-u[ \t]*)', '(?:--user[ \t]+|-u[ \t]+)', 'P34')
    'scrub-curl-uidgid'      = @('if (($x.Groups[2].Value -match ''^[0-9]+$'') -and ($x.Groups[3].Value -match ''^[0-9]{1,6}$'')) { return $x.Value }', '$null = 0', 'P34')
    'scrub-curl-7'           = @('-match ''^[0-9]{1,6}$'')) { return $x.Value }', '-match ''^[0-9]{1,7}$'')) { return $x.Value }', 'P34')
    'scrub-curl-5'           = @('-match ''^[0-9]{1,6}$'')) { return $x.Value }', '-match ''^[0-9]{1,5}$'')) { return $x.Value }', 'P34')
    'scrub-pass-title-colon' = @('($key -ceq ''Pass'')', '$false', 'P34')
    'scrub-pass-sep-us-only' = @('($key -imatch ''[_.\-]pass$'')', '($key -imatch ''_pass$'')', 'P34')
    'scrub-pass-sep-eq-only' = @('($key -imatch ''[_.\-]pass$'')', '(($key -imatch ''[_.\-]pass$'') -and ($sep -match ''=''))', 'P34')
    'scrub-path-exempt-back' = @("            if (`$sep -notmatch '=') {`n                # After ':' a value stays readable", "            if (`$bare -match '^(?:/|~|[A-Za-z]:\\|[^\s/]+\\.[A-Za-z0-9]{1,6}`$)') { return `$x.Value }`n            if (`$sep -notmatch '=') {`n                # After ':' a value stays readable", 'P34')
    'scrub-colon-always'     = @('if (($val -eq $bare) -and ($word -match ''^[A-Za-z]{1,15}$'')) { return $x.Value }', '$null = 0', 'P34')
    'scrub-colon-word-digits' = @('$word -match ''^[A-Za-z]{1,15}$''', '$word -match ''^[A-Za-z0-9]{1,15}$''', 'P34')
    'scrub-colon-word-punct' = @('$word -match ''^[A-Za-z]{1,15}$''', '$word -match ''^[^\s]{1,15}$''', 'P34')
    'scrub-colon-trim-off'   = @('$word = $bare.TrimEnd('','', ''.'', '';'', '':'', '')'')', '$word = $bare', 'P34')
    'scrub-colon-quoted-kept' = @('if (($val -eq $bare) -and ($word -match', 'if (($true) -and ($word -match', 'P34')
    'scrub-key-secret-key'   = @('(?:secret|signing|encryption|master|license)[_\-]?key|', '', 'P34')
    'scrub-key-signing'      = @('(?:secret|signing|encryption|master|license)', '(?:secret|encryption|master|license)', 'P34')
    'scrub-key-encryption'   = @('(?:secret|signing|encryption|master|license)', '(?:secret|signing|master|license)', 'P34')
    'scrub-key-master'       = @('(?:secret|signing|encryption|master|license)', '(?:secret|signing|encryption|license)', 'P34')
    'scrub-key-license'      = @('(?:secret|signing|encryption|master|license)', '(?:secret|signing|encryption|master)', 'P34')
    'scrub-key-secret-word'  = @('(?:secret|signing|encryption|master|license)', '(?:signing|encryption|master|license)', 'P34')
    'scrub-hex40-off'        = @('[A-Za-z0-9_\-]{40,512}(?![A-Za-z0-9_\-])''', '[A-Za-z0-9_\-]{41,512}(?![A-Za-z0-9_\-])''', 'P34')
    'scrub-curl-gid-min2'    = @('-match ''^[0-9]{1,6}$'')) { return $x.Value }', '-match ''^[0-9]{2,6}$'')) { return $x.Value }', 'P34')
    'scrub-curl-uid-max5'    = @('($x.Groups[2].Value -match ''^[0-9]+$'') -and', '($x.Groups[2].Value -match ''^[0-9]{1,5}$'') -and', 'P34')
    'scrub-curl-uid-long'    = @('-match ''^[0-9]{1,6}$'')) { return $x.Value }', '-match ''^[0-9]+$'')) { return $x.Value }', 'P34')
    'scrub-pass-upper-colon' = @('(($key -ceq ''PASS'') -and ($sep -match ''=''))', '($key -ceq ''PASS'')', 'P34')
    'scrub-pass-upper-eq'    = @('(($key -ceq ''PASS'') -and ($sep -match ''=''))', '$false', 'P34')
    'scrub-pass-camel'       = @(' -or ($key -cmatch ''[a-z0-9]Pass$'')', '', 'P34')
    'scrub-oracle-quote'     = @('[A-Za-z][A-Za-z0-9_\-]{0,30}(?:[\s)?,;"'''']|$))', '[A-Za-z][A-Za-z0-9_\-]{0,30}(?:[\s)?,;]|$))', 'P34')
    'scrub-docker-auth'      = @('$t = [regex]::Replace($t, ''(?i)("auth"', '$null = [regex]::Replace($t, ''(?i)("auth"', 'P34')
    'scrub-sas-sig'          = @('$t = [regex]::Replace($t, ''([?&]sig=)', '$null = [regex]::Replace($t, ''([?&]sig=)', 'P34')
    'scrub-redis-a'          = @('$t = [regex]::Replace($t, ''(\bredis-cli\b', '$null = [regex]::Replace($t, ''(\bredis-cli\b', 'P34')
    'scrub-sshpass'          = @('$t = [regex]::Replace($t, ''(?i)(\bsshpass', '$null = [regex]::Replace($t, ''(?i)(\bsshpass', 'P34')
    'scrub-xml-tag'          = @('$t = [regex]::Replace($t, ''(?i)(<(?:password', '$null = [regex]::Replace($t, ''(?i)(<(?:password', 'P34')
    'scrub-cookie'           = @('$t = [regex]::Replace($t, ''(?i)(\b(?:set-)?cookie', '$null = [regex]::Replace($t, ''(?i)(\b(?:set-)?cookie', 'P34')
    'scrub-padded-b64'       = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9+/=_\-])[A-Za-z0-9+/]{32,512}={1,2}', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9+/=_\-])[A-Za-z0-9+/]{32,512}={1,2}', 'P34')
    'scrub-blob'             = @('$t = [regex]::Replace($t, ''(?<![A-Za-z0-9+/=_\-])[A-Za-z0-9+/]{40,512}(?!', '$null = [regex]::Replace($t, ''(?<![A-Za-z0-9+/=_\-])[A-Za-z0-9+/]{40,512}(?!', 'P34')
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
