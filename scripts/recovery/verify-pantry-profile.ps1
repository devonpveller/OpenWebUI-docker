<#
.SYNOPSIS
    Executable proof that emergency-recovery.ps1 starts openbrain-pantry only when the
    `pantry` profile is ENABLED (pantry-wire, 2026-10-04). Starts, stops and touches
    NO container, reads NO live state: every case runs in a throwaway directory.

.DESCRIPTION
    The script under test is never dot-sourced (its main block would run a recovery).
    Only Test-OB1PantryEnabled and Get-OB1StartProfiles are lifted out of it with the
    PowerShell parser and defined in a child scope, as verify-recovery-gates.ps1 does.

    Cases (each in a temp dir holding its own .stack\state.json / OB1\docker\.env):
      no state, no env                -> the start set has NO pantry flag
      state = the live four profiles  -> NO pantry flag (the host today)
      state lists pantry              -> pantry flag present
      env COMPOSE_PROFILES has pantry -> pantry flag present
      env COMPOSE_PROFILES without it -> NO pantry flag
      unreadable state file           -> NO pantry flag (fails closed to "off")
    and one STATIC case: the teardown/status list $Script:OB1Profiles DOES name pantry
    (a running one must be torn down), while no `up` site uses it directly.

    STATIC DOCKER-ESCAPE REFUSAL (harness-gaps). The behavioural layer replaces `docker` with
    a function that records argv. That stub intercepts ONLY a command whose name resolves to
    the bare `docker`; every other way to reach the daemon would run for REAL during
    verification. So before anything else the five functions are walked as an AST and the
    verifier REFUSES (non-zero, one file:line per offender, behavioural layer never reached):
      `& docker.exe ...` or any path ending in docker(.exe); `& 'docker'` / `& "docker"` / `. docker`
      `& $var` / `& (expr)` (dynamic: cannot be shown not to be docker)
      Start-Process / start / saps naming docker; cmd / powershell / pwsh / bash / sh / wsl naming docker
      Invoke-Expression / iex that names docker or is not a plain literal
      [Diagnostics.Process]::Start, ProcessStartInfo, New-Object ...Process
    The only form allowed is the bare `docker ...` command the stub intercepts. Comments are
    not in the AST, so a comment mentioning docker.exe is not an offender. As defence in depth
    the verifier also points DOCKER_HOST at a dead endpoint for its own process.

    -MutantDrill: build a scratch copy of the target per escape form (plus a comment control),
    run this verifier against each, and require non-zero with the offending line named - and
    the unmutated target to stay green. Touches no container.

    Exit code = number of failed cases.
#>
[CmdletBinding()]
param([string]$Target = '', [switch]$MutantDrill)

$ErrorActionPreference = 'Stop'
if (-not $Target) { $Target = Join-Path $PSScriptRoot 'emergency-recovery.ps1' }
$failed = 0
function Check($name, $ok, $detail = '') {
    if ($ok) { Write-Host ("  [OK]   {0}" -f $name) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $name, $detail); $script:failed++ }
}

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$null, [ref]$null)
$want = 'Test-OB1PantryEnabled', 'Get-OB1StartProfiles', 'Start-OB1Stack', 'Reset-OB1Stack', 'Stop-OB1Stack'

# ---- STATIC: no docker invocation the stub cannot intercept -----------------------------------
function Get-DockerEscapes($ast, [string[]]$Fns, [string]$File) {
    $out = New-Object System.Collections.ArrayList
    $leaf = Split-Path -Leaf $File
    function Add-Esc($node, $fn, $why) { [void]$out.Add(("{0}:{1}: in {2}: {3}  [{4}]" -f $leaf, $node.Extent.StartLineNumber, $fn, $why, ($node.Extent.Text -replace '\s+', ' ').Trim())) }
    foreach ($fn in $Fns) {
        $def = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fn }, $true)
        if (-not $def) { continue }
        foreach ($c in @($def.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true))) {
            $name = $c.GetCommandName()
            $text = $c.Extent.Text
            $hasOp = ($c.InvocationOperator -ne [System.Management.Automation.Language.TokenKind]::Unknown)
            if ($null -eq $name) {
                if ($hasOp) { Add-Esc $c $fn 'dynamic invocation (& $x / & (...)) - cannot be shown to be the stubbed docker' }
                continue
            }
            $leafName = ($name -split '[\\/]')[-1]
            if ($name -match '(?i)(^|[\\/])docker(\.exe)?$') {
                if ($hasOp) { Add-Esc $c $fn "docker behind an invocation operator ($($c.InvocationOperator)) - not the bare command the stub intercepts" }
                elseif ($name -ne 'docker') { Add-Esc $c $fn "'$name' is not the bare name 'docker' - the stub does not shadow it" }
                continue
            }
            if ($hasOp) { Add-Esc $c $fn "invocation operator on '$name'"; continue }
            switch -Regex ($leafName) {
                '(?i)^(Start-Process|start|saps)$' { if ($text -match '(?i)docker') { Add-Esc $c $fn 'Start-Process naming docker' } }
                '(?i)^(cmd|powershell|pwsh|bash|sh|wsl)(\.exe)?$' { if ($text -match '(?i)docker') { Add-Esc $c $fn "$leafName shell-out naming docker" } }
                '(?i)^(Invoke-Expression|iex)$' {
                    $args2 = @($c.CommandElements | Select-Object -Skip 1)
                    $plain = ($args2.Count -ge 1) -and (@($args2 | Where-Object { $_ -isnot [System.Management.Automation.Language.StringConstantExpressionAst] }).Count -eq 0)
                    if (($text -match '(?i)docker') -or -not $plain) { Add-Esc $c $fn 'Invoke-Expression naming docker or not a plain literal' }
                }
                '(?i)^New-Object$' { if ($text -match '(?i)Diagnostics\.Process|ProcessStartInfo') { Add-Esc $c $fn 'New-Object of a Process' } }
                '(?i)^(Invoke-Command|icm|Start-Job|Start-ThreadJob|Invoke-WmiMethod|Invoke-CimMethod)$' { if ($text -match '(?i)docker') { Add-Esc $c $fn "$leafName naming docker" } }
            }
        }
        foreach ($m in @($def.FindAll({ param($n) $n -is [System.Management.Automation.Language.InvokeMemberExpressionAst] -or $n -is [System.Management.Automation.Language.TypeExpressionAst] }, $true))) {
            if ($m.Extent.Text -match '(?i)Diagnostics\.Process|ProcessStartInfo|\[Process\]') { Add-Esc $m $fn 'System.Diagnostics.Process (starts a native process the stub cannot see)' }
        }
    }
    return @($out | Select-Object -Unique)
}

if ($MutantDrill) {
    $self = $MyInvocation.MyCommand.Path
    $src = [System.IO.File]::ReadAllText($Target)
    $upLine = 'docker compose -f $Script:OB1Compose @prof up -d'
    $stopLine = 'docker compose -f $Script:OB1Compose @prof stop'
    $downLine = 'docker compose -f $Script:OB1Compose @prof down'
    $cases = @(
        @{ n = 'control: a comment naming docker.exe (stays GREEN)'; fn = 'Start-OB1Stack'; from = $upLine; to = ('# & docker.exe compose is not code' + "`n        " + $upLine); red = $false },
        @{ n = '& docker.exe compose'; fn = 'Start-OB1Stack'; from = $upLine; to = '& docker.exe compose -f $Script:OB1Compose @prof up -d'; red = $true },
        @{ n = "& 'docker' compose"; fn = 'Start-OB1Stack'; from = $upLine; to = "& 'docker' compose -f `$Script:OB1Compose @prof up -d"; red = $true },
        @{ n = '& "docker" compose'; fn = 'Reset-OB1Stack'; from = $downLine; to = '& "docker" compose -f $Script:OB1Compose @prof down'; red = $true },
        @{ n = '& $d compose (dynamic, $d = docker)'; fn = 'Stop-OB1Stack'; from = $stopLine; to = ('$d = ' + "'docker'" + '; & $d compose -f $Script:OB1Compose @prof stop'); red = $true },
        @{ n = '& C:\...\docker.exe (full path)'; fn = 'Stop-OB1Stack'; from = $stopLine; to = "& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' compose stop"; red = $true },
        @{ n = 'Start-Process docker'; fn = 'Start-OB1Stack'; from = $upLine; to = "Start-Process docker -ArgumentList 'compose up -d' -Wait"; red = $true },
        @{ n = 'Start-Process -FilePath docker.exe'; fn = 'Reset-OB1Stack'; from = $downLine; to = "Start-Process -FilePath 'docker.exe' -ArgumentList 'compose down'"; red = $true },
        @{ n = 'cmd /c docker'; fn = 'Start-OB1Stack'; from = $upLine; to = 'cmd /c docker compose up -d'; red = $true },
        @{ n = "Invoke-Expression 'docker ...'"; fn = 'Stop-OB1Stack'; from = $stopLine; to = "Invoke-Expression 'docker compose stop'"; red = $true },
        @{ n = 'iex $variable'; fn = 'Stop-OB1Stack'; from = $stopLine; to = '$cmdline = "docker compose stop"; iex $cmdline'; red = $true },
        @{ n = '[Diagnostics.Process]::Start'; fn = 'Start-OB1Stack'; from = $upLine; to = "[Diagnostics.Process]::Start('docker', 'compose up -d') | Out-Null"; red = $true },
        @{ n = '[System.Diagnostics.Process]::Start'; fn = 'Reset-OB1Stack'; from = $downLine; to = "[System.Diagnostics.Process]::Start('docker.exe', 'ps') | Out-Null"; red = $true })
    $bad = 0
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('pantry-mut-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    try {
        $srcAst = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
        foreach ($k in $cases) {
            $def = $srcAst.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $k.fn }, $true)
            $body = $def.Extent.Text
            $i = $body.IndexOf($k.from)
            if ($i -lt 0) { Write-Host ("  [FAIL] drill setup: '{0}' not found in {1}" -f $k.from, $k.fn); $bad++; continue }
            $mut = $src.Substring(0, $def.Extent.StartOffset) + $body.Substring(0, $i) + $k.to + $body.Substring($i + $k.from.Length) + $src.Substring($def.Extent.EndOffset)
            $f = Join-Path $dir 'emergency-recovery.ps1'
            [System.IO.File]::WriteAllText($f, $mut, (New-Object System.Text.UTF8Encoding($false)))
            $o = & powershell -NoProfile -ExecutionPolicy Bypass -File $self -Target $f 2>&1 | Out-String
            $code = $LASTEXITCODE
            if ($k.red) {
                $named = [regex]::IsMatch($o, 'emergency-recovery\.ps1:\d+: in ' + [regex]::Escape($k.fn))
                $ok = ($code -ne 0) -and $named -and ($o -notmatch 'Start and Reset each issue')
                Write-Host ("  [{0}] mutant {1} -> exit {2}{3}" -f $(if ($ok) { 'OK' } else { 'FAIL' }), $k.n, $code, $(if ($named) { ' (file:line named, behavioural layer not reached)' } else { ' (NO file:line)' }))
            } else {
                $ok = ($code -eq 0)
                Write-Host ("  [{0}] {1} -> exit {2}" -f $(if ($ok) { 'OK' } else { 'FAIL' }), $k.n, $code)
            }
            if (-not $ok) { $bad++ }
        }
    } finally { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue }
    $o = & powershell -NoProfile -ExecutionPolicy Bypass -File $self -Target $Target 2>&1 | Out-String
    $ok = ($LASTEXITCODE -eq 0)
    Write-Host ("  [{0}] the unmutated target stays green -> exit {1}" -f $(if ($ok) { 'OK' } else { 'FAIL' }), $LASTEXITCODE)
    if (-not $ok) { $bad++ }
    Write-Host ''
    if ($bad -eq 0) { Write-Host 'ALL MUTANT CASES BEHAVED' } else { Write-Host "$bad mutant case(s) FAILED" }
    exit $bad
}

$escapes = @(Get-DockerEscapes $ast $want $Target)
if ($escapes.Count -gt 0) {
    Write-Host 'REFUSED: docker invocation form(s) the verifier cannot intercept - running the behavioural layer would reach the REAL daemon:'
    foreach ($e in $escapes) { Write-Host ("  [FAIL] {0}" -f $e) }
    Write-Host 'Use the bare `docker ...` command (the only form the stub shadows), or extend this verifier.'
    exit $escapes.Count
}
# Defence in depth: whatever slips past the static layer finds no daemon to talk to.
$env:DOCKER_HOST = 'tcp://127.0.0.1:1'
$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $want }, $true)
Check 'no docker invocation form the stub cannot intercept (static, AST)' ($escapes.Count -eq 0)
Check 'the five functions exist in emergency-recovery.ps1' ($fns.Count -eq 5)
foreach ($f in $fns) { . ([scriptblock]::Create($f.Extent.Text)) }

# The same constants the script declares, read from it rather than copied.
$src = Get-Content -Raw -Path $Target
$m = [regex]::Match($src, '(?s)\$Script:OB1Profiles = @\((.*?)\)')
$Script:OB1Profiles = @([regex]::Matches($m.Groups[1].Value, "'([^']+)'") | ForEach-Object { $_.Groups[1].Value })
$Script:OB1PantryProfile = 'pantry'
$Script:OB1Compose = 'OB1\docker\docker-compose.yml'

Check 'teardown/status list names pantry (a running one must go)' ($Script:OB1Profiles -contains 'pantry')
# CODE, NOT TEXT (attempt 1's tester: a comment containing "Get-OB1StartProfiles" above an up
# site satisfied a text look-back while the mutant `$prof = $Script:OB1Profiles` was live).
# Walk the AST: every compose `up` invocation inside the two start functions must splat a
# variable whose LAST assignment before that command is a call to Get-OB1StartProfiles.
# Comments are not in the AST, so they cannot satisfy this.
function Get-UpSiteVerdicts($ast, [string]$fn) {
    $def = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fn }, $true)
    if (-not $def) { return @(@{ fn = $fn; ok = $false; why = 'function not found' }) }
    $cmds = $def.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
    $verdicts = @()
    foreach ($c in $cmds) {
        if ($c.GetCommandName() -ne 'docker') { continue }
        $words = @($c.CommandElements | ForEach-Object { $_.Extent.Text })
        if (($words -notcontains 'compose') -or ($words -notcontains 'up')) { continue }
        $splat = @($c.CommandElements | Where-Object { $_ -is [System.Management.Automation.Language.VariableExpressionAst] -and $_.Splatted })
        if ($splat.Count -ne 1) { $verdicts += @{ fn = $fn; ok = $false; why = 'up site does not splat exactly one variable' }; continue }
        $var = $splat[0].VariablePath.UserPath
        $assigns = $def.FindAll({ param($n)
            $n -is [System.Management.Automation.Language.AssignmentStatementAst] -and
            $n.Left -is [System.Management.Automation.Language.VariableExpressionAst] -and
            $n.Left.VariablePath.UserPath -eq $var -and
            $n.Extent.EndOffset -le $c.Extent.StartOffset }, $true)
        if ($assigns.Count -eq 0) { $verdicts += @{ fn = $fn; ok = $false; why = "no assignment of $var before the up site" }; continue }
        $last = $assigns | Sort-Object { $_.Extent.StartOffset } | Select-Object -Last 1
        $calls = @($last.Right.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] -and $n.GetCommandName() -eq 'Get-OB1StartProfiles' }, $true))
        $verdicts += @{ fn = $fn; ok = ($calls.Count -ge 1); why = "last assignment: $($last.Extent.Text)" }
    }
    if ($verdicts.Count -eq 0) { $verdicts += @{ fn = $fn; ok = $false; why = 'no compose up site found' } }
    return $verdicts
}
foreach ($fn in 'Start-OB1Stack', 'Reset-OB1Stack') {
    $vs = @(Get-UpSiteVerdicts $ast $fn)
    Check "${fn}: its one compose up takes its flags from Get-OB1StartProfiles (AST)" ((@($vs | Where-Object { -not $_.ok }).Count -eq 0) -and ($vs.Count -eq 1)) (($vs | ForEach-Object { $_.why }) -join '; ')
}

function Flags { ((Get-OB1StartProfiles) -join ' ') }
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ('pantry-rec-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
Push-Location $tmp
try {
    New-Item -ItemType Directory -Path '.stack', 'OB1\docker' | Out-Null
    Check 'no state, no env: no pantry flag' ((Flags) -notmatch 'pantry') (Flags)
    '{"planes":{"ob1":{"profiles":["idea-refinery","research","wiki","notebook"]}}}' | Set-Content '.stack\state.json'
    Check 'state = the live four profiles: no pantry flag' ((Flags) -notmatch 'pantry') (Flags)
    Check 'the four profiles are all still passed' ((Flags) -eq '--profile research --profile wiki --profile notebook --profile idea-refinery') (Flags)
    '{"planes":{"ob1":{"profiles":["idea-refinery","research","wiki","notebook","pantry"]}}}' | Set-Content '.stack\state.json'
    Check 'state lists pantry: pantry flag present' ((Flags) -match '--profile pantry$') (Flags)
    Remove-Item '.stack\state.json'
    'COMPOSE_PROFILES=research,wiki,pantry' | Set-Content 'OB1\docker\.env'
    Check 'env COMPOSE_PROFILES has pantry: flag present' ((Flags) -match '--profile pantry$') (Flags)
    'COMPOSE_PROFILES=research,wiki' | Set-Content 'OB1\docker\.env'
    Check 'env COMPOSE_PROFILES without pantry: no flag' ((Flags) -notmatch 'pantry') (Flags)
    'not json {' | Set-Content '.stack\state.json'
    Check 'unreadable state file: fails closed to off' ((Flags) -notmatch 'pantry') (Flags)

    # ---- BEHAVIOUR: run the real start/reset/stop functions with `docker` stubbed to record argv.
    # The AST check above cannot see an escape that keeps the right assignment but adds pantry some
    # other way (a literal `--profile pantry`, `+ $Script:OB1Profiles`, an if/else). This does.
    function Test-OB1Available { $true }
    function Write-Log { param($Level, $Message) }
    function Wait-ForRestartLoops { param($Seconds) return ,@() }
    function Start-Sleep { param($Seconds) }
    $global:DockerCalls = New-Object System.Collections.ArrayList
    function docker { [void]$global:DockerCalls.Add(($args -join ' ')); $global:LASTEXITCODE = 0 }
    function Run-Recovery {
        $global:DockerCalls.Clear()
        Start-OB1Stack; $startCalls = @($global:DockerCalls); $global:DockerCalls.Clear()
        Reset-OB1Stack; $resetCalls = @($global:DockerCalls); $global:DockerCalls.Clear()
        Stop-OB1Stack; $stopCalls = @($global:DockerCalls); $global:DockerCalls.Clear()
        return @{ start = $startCalls; reset = $resetCalls; stop = $stopCalls }
    }
    function Ups($calls) { @($calls | Where-Object { $_ -match '(^| )up( |$)' }) }
    function Downs($calls) { @($calls | Where-Object { $_ -match '(^| )(down|stop)( |$)' }) }
    foreach ($case in @(
        @{ name = 'disabled (no state, no env)'; state = $null; env = $null; on = $false },
        @{ name = 'disabled (live four-profile state)'; state = '{"planes":{"ob1":{"profiles":["idea-refinery","research","wiki","notebook"]}}}'; env = 'COMPOSE_PROFILES=research,wiki'; on = $false },
        @{ name = 'enabled via state'; state = '{"planes":{"ob1":{"profiles":["research","pantry"]}}}'; env = $null; on = $true },
        @{ name = 'enabled via OB1/docker/.env'; state = $null; env = 'COMPOSE_PROFILES=research,pantry'; on = $true })) {
        Remove-Item '.stack\state.json', 'OB1\docker\.env' -ErrorAction SilentlyContinue
        if ($case.state) { $case.state | Set-Content '.stack\state.json' }
        if ($case.env) { $case.env | Set-Content 'OB1\docker\.env' }
        $r = Run-Recovery
        $ups = @(Ups $r.start) + @(Ups $r.reset)
        Check "$($case.name): Start and Reset each issue exactly one up" ($ups.Count -eq 2) "($($ups.Count))"
        $pantryUps = @($ups | Where-Object { $_ -match 'pantry' })
        if ($case.on) { Check "$($case.name): every up names --profile pantry" ($ups.Count -gt 0 -and $pantryUps.Count -eq $ups.Count) ($ups -join ' | ') }
        else { Check "$($case.name): NO up names pantry anywhere" ($pantryUps.Count -eq 0) ($pantryUps -join ' | ') }
        $downs = @(Downs $r.start) + @(Downs $r.reset) + @(Downs $r.stop)
        $downsNoPantry = @($downs | Where-Object { $_ -notmatch '--profile pantry' })
        Check "$($case.name): every down/stop names --profile pantry (a running one must go)" ($downs.Count -ge 2 -and $downsNoPantry.Count -eq 0) ($downsNoPantry -join ' | ')
    }
}
finally { Pop-Location; Remove-Item -Recurse -Force $tmp }

Write-Host ''
if ($failed -eq 0) { Write-Host 'ALL PANTRY RECOVERY CASES PASSED' } else { Write-Host "$failed case(s) FAILED" }
exit $failed
