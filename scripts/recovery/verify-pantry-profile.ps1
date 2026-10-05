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

    STATIC ALLOWLIST (harness-gaps; redesigned after attempt 1). The behavioural layer replaces
    `docker` with a function that records argv. That stub intercepts ONLY a command that resolves to the
    bare `docker`; anything else that executes (docker.exe, docker-compose, docker.cmd, Start-Job with a
    script block, InvokeScript, an alias that beats the stub, ...) would run for REAL. A denylist of
    forms cannot win, so before anything else the five functions are walked as an AST and ONLY these may
    execute: the bare `docker` command; the helpers the verifier stubs or lifts by name; a short list of
    pure cmdlets the shipped code uses (see $Script:AllowedCommands); and the `.Trim()` member. Any other
    command, any other member invocation, script-block literal, nested function, type expression or cast
    is REFUSED (non-zero, one `file:line` per offender, behavioural layer never reached). Comments are not
    in the AST, so a comment mentioning docker.exe is not an offender.
    BELT AND BRACES: after the static gate the verifier re-runs itself in a child PowerShell whose PATH
    holds only tripwire docker / docker-compose stubs (.cmd, .bat, .exe) that write a marker, whose
    DOCKER_HOST is a dead endpoint and whose DOCKER_CONFIG is an empty directory (no contexts); the run
    fails if any marker appears.

    -MutantDrill: build a scratch copy of the target per escape form (plus a comment control), run this
    verifier against each under the same tripwire environment, and require non-zero with the offending
    line named, the unmutated target green, and no tripwire fired. Touches no container.

    Exit code = number of failed cases.
#>
[CmdletBinding()]
param([string]$Target = '', [switch]$MutantDrill, [switch]$InChild)

$ErrorActionPreference = 'Stop'
if (-not $Target) { $Target = Join-Path $PSScriptRoot 'emergency-recovery.ps1' }
$failed = 0
function Check($name, $ok, $detail = '') {
    if ($ok) { Write-Host ("  [OK]   {0}" -f $name) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $name, $detail); $script:failed++ }
}

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$null, [ref]$null)
$want = 'Test-OB1PantryEnabled', 'Get-OB1StartProfiles', 'Start-OB1Stack', 'Reset-OB1Stack', 'Stop-OB1Stack'

# ---- STATIC: an ALLOWLIST of what the five functions may execute ------------------------------
# A denylist of "bad docker forms" cannot win (attempt 1's tester found docker-compose, Start-Job with
# a script block, InvokeScript, Set-Alias, docker.cmd). So the question is inverted: every CommandAst
# and every member invocation in the lifted functions must be on a short list derived from the
# shipped code; anything else is refused with file:line.
#   (a) the bare `docker` command, no invocation operator: the one name the behavioural stub shadows;
#   (b) helpers the verifier stubs or lifts BY NAME: Test-OB1Available, Wait-ForRestartLoops, Write-Log,
#       Start-Sleep (stubbed), Test-OB1PantryEnabled, Get-OB1StartProfiles (lifted, run for real);
#   (c) the pure/read-only cmdlets the shipped code uses: ConvertFrom-Json, Get-Content, Join-Path,
#       Split-Path, Test-Path (no Set-/New-Alias, Start-*, Invoke-*, & or . are on it);
#   members: only `.Trim(...)` (instance), which Test-OB1PantryEnabled uses on a regex match.
# Also refused outright: script-block literals (they can be run by .Invoke/Start-Job), nested function
# definitions, type expressions/casts ([ScriptBlock]::Create, [Diagnostics.Process]::Start), `using:`.
# Derived by walking the shipped file 2026-10-05 (inventory of CommandAst names + operators and member calls).
# If recovery legitimately starts using another cmdlet, add it HERE, on purpose, and say why.
$Script:AllowedCommands = @('docker', 'Test-OB1Available', 'Wait-ForRestartLoops', 'Write-Log', 'Start-Sleep',
    'Test-OB1PantryEnabled', 'Get-OB1StartProfiles', 'ConvertFrom-Json', 'Get-Content', 'Join-Path', 'Split-Path', 'Test-Path')
$Script:AllowedMembers = @('Trim')
function Get-AllowlistViolations($ast, [string[]]$Fns, [string]$File) {
    $out = New-Object System.Collections.ArrayList
    $leaf = Split-Path -Leaf $File
    $L = 'System.Management.Automation.Language'
    function Add-Esc($node, $fn, $why) { [void]$out.Add(("{0}:{1}: in {2}: {3}  [{4}]" -f $leaf, $node.Extent.StartLineNumber, $fn, $why, ($node.Extent.Text -replace '\s+', ' ').Trim())) }
    foreach ($fn in $Fns) {
        $def = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $fn }, $true)
        if (-not $def) { continue }
        foreach ($n in @($def.FindAll({ param($n) $true }, $true))) {
            if ($n -is [System.Management.Automation.Language.CommandAst]) {
                $name = $n.GetCommandName()
                $hasOp = ($n.InvocationOperator -ne [System.Management.Automation.Language.TokenKind]::Unknown)
                if ($null -eq $name) { Add-Esc $n $fn 'dynamic command (& $x, & (...), a computed name) - cannot be shown to be the stubbed docker'; continue }
                if ($hasOp) { Add-Esc $n $fn "invocation operator ($($n.InvocationOperator)) on '$name' - only plain commands are allowed"; continue }
                if ($Script:AllowedCommands -notcontains $name) { Add-Esc $n $fn "command '$name' is not on the allowlist (only bare docker, the stubbed/lifted helpers and a few pure cmdlets run here)"; continue }
                if ($name -ceq 'docker' -or $name -eq 'docker') { if ($name -cne 'docker') { Add-Esc $n $fn "'$name' is not the exact bare name 'docker'" } }
            }
            elseif ($n -is [System.Management.Automation.Language.InvokeMemberExpressionAst]) {
                $mn = $null; if ($n.Member -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $mn = $n.Member.Value }
                if (($n.Static) -or ($null -eq $mn) -or ($Script:AllowedMembers -notcontains $mn)) { Add-Esc $n $fn "member invocation '$($n.Member.Extent.Text)' is not on the allowlist (only .Trim() is)" }
            }
            elseif ($n -is [System.Management.Automation.Language.ScriptBlockExpressionAst]) { Add-Esc $n $fn 'script-block literal (can be run by .Invoke() / Start-Job / InvokeScript)' }
            elseif (($n -is [System.Management.Automation.Language.FunctionDefinitionAst]) -and -not [object]::ReferenceEquals($n, $def)) { Add-Esc $n $fn 'nested function definition (could shadow a command)' }
            elseif (($n -is [System.Management.Automation.Language.TypeExpressionAst]) -or ($n -is [System.Management.Automation.Language.ConvertExpressionAst]) -or
                    ($n -is [System.Management.Automation.Language.AttributedExpressionAst]) -or ($n -is [System.Management.Automation.Language.UsingExpressionAst])) {
                Add-Esc $n $fn 'type expression / cast / using: (reaches .NET statics such as Process::Start or ScriptBlock::Create)'
            }
        }
    }
    return @($out | Select-Object -Unique)
}

# Tripwires: a directory holding ONLY docker / docker-compose stubs under every launchable name (.cmd, .bat, .exe) that
# append to tripped.txt when run, plus an EMPTY docker config dir (no contexts). A child PowerShell run with PATH = that
# directory, DOCKER_HOST dead and DOCKER_CONFIG = the empty dir can reach no real docker client or daemon; if anything
# executes a docker-named program, the marker appears and the run fails.
function New-Tripwire {
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('pantry-trip-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir, (Join-Path $dir 'cfg') | Out-Null
    $marker = Join-Path $dir 'tripped.txt'
    foreach ($n in 'docker', 'docker-compose') {
        foreach ($e in 'cmd', 'bat') { [System.IO.File]::WriteAllText((Join-Path $dir "$n.$e"), "@echo off`r`necho $n.$e>>`"%~dp0tripped.txt`"`r`n", [System.Text.Encoding]::ASCII) }
    }
    $note = ''
    try {
        $src = 'public static class T { public static void Main(string[] a) { System.IO.File.AppendAllText(System.IO.Path.Combine(System.AppDomain.CurrentDomain.BaseDirectory, "tripped.txt"), System.Diagnostics.Process.GetCurrentProcess().ProcessName + ".exe\r\n"); } }'
        $exe = Join-Path $dir 'docker.exe'
        Add-Type -TypeDefinition $src -OutputAssembly $exe -OutputType ConsoleApplication -ErrorAction Stop
        Copy-Item $exe (Join-Path $dir 'docker-compose.exe')
    } catch { $note = "no .exe tripwire (compile failed: $($_.Exception.Message)); .cmd/.bat only" }
    return @{ dir = $dir; marker = $marker; cfg = (Join-Path $dir 'cfg'); note = $note }
}
function Enter-Tripwire($t) {
    $prev = @{ PATH = $env:PATH; DOCKER_HOST = $env:DOCKER_HOST; DOCKER_CONFIG = $env:DOCKER_CONFIG; DOCKER_CONTEXT = $env:DOCKER_CONTEXT }
    $env:PATH = $t.dir; $env:DOCKER_HOST = 'tcp://127.0.0.1:1'; $env:DOCKER_CONFIG = $t.cfg; $env:DOCKER_CONTEXT = $null
    return $prev
}
function Exit-Tripwire($prev) { $env:PATH = $prev.PATH; $env:DOCKER_HOST = $prev.DOCKER_HOST; $env:DOCKER_CONFIG = $prev.DOCKER_CONFIG; $env:DOCKER_CONTEXT = $prev.DOCKER_CONTEXT }
$psExe = Join-Path $PSHOME 'powershell.exe'

if ($MutantDrill) {
    $self = $MyInvocation.MyCommand.Path
    $src = [System.IO.File]::ReadAllText($Target)
    $upLine = 'docker compose -f $Script:OB1Compose @prof up -d'
    $stopLine = 'docker compose -f $Script:OB1Compose @prof stop'
    $downLine = 'docker compose -f $Script:OB1Compose @prof down'
    function Before([string]$extra, [string]$line) { return ($extra + "`n        " + $line) }
    # `ps` / `version` only: a mutant that ever ran would at worst read.
    $cases = @(
        @{ n = 'control: a comment naming docker.exe (stays GREEN)'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before '# & docker.exe ps is not code' $upLine); red = $false },
        @{ n = '& docker.exe compose'; fn = 'Start-OB1Stack'; from = $upLine; to = '& docker.exe compose -f $Script:OB1Compose @prof up -d'; red = $true },
        @{ n = "& 'docker' compose"; fn = 'Start-OB1Stack'; from = $upLine; to = "& 'docker' compose -f `$Script:OB1Compose @prof up -d"; red = $true },
        @{ n = '& "docker" compose'; fn = 'Reset-OB1Stack'; from = $downLine; to = '& "docker" compose -f $Script:OB1Compose @prof down'; red = $true },
        @{ n = '& $d compose (dynamic, $d = docker)'; fn = 'Stop-OB1Stack'; from = $stopLine; to = ('$d = ' + "'docker'" + '; & $d compose -f $Script:OB1Compose @prof stop'); red = $true },
        @{ n = '& C:\...\docker.exe (full path)'; fn = 'Stop-OB1Stack'; from = $stopLine; to = "& 'C:\Program Files\Docker\Docker\resources\bin\docker.exe' compose stop"; red = $true },
        @{ n = 'Start-Process docker'; fn = 'Start-OB1Stack'; from = $upLine; to = "Start-Process docker -ArgumentList 'version' -Wait"; red = $true },
        @{ n = 'Start-Process -FilePath docker.exe'; fn = 'Reset-OB1Stack'; from = $downLine; to = "Start-Process -FilePath 'docker.exe' -ArgumentList 'version'"; red = $true },
        @{ n = 'cmd /c docker'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before 'cmd /c docker version' $upLine); red = $true },
        @{ n = "Invoke-Expression 'docker ...'"; fn = 'Stop-OB1Stack'; from = $stopLine; to = (Before "Invoke-Expression 'docker version'" $stopLine); red = $true },
        @{ n = 'iex $variable'; fn = 'Stop-OB1Stack'; from = $stopLine; to = (Before '$cmdline = "docker version"; iex $cmdline' $stopLine); red = $true },
        @{ n = '[Diagnostics.Process]::Start'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before "[Diagnostics.Process]::Start('docker', 'version') | Out-Null" $upLine); red = $true },
        @{ n = '[System.Diagnostics.Process]::Start'; fn = 'Reset-OB1Stack'; from = $downLine; to = (Before "[System.Diagnostics.Process]::Start('docker.exe', 'version') | Out-Null" $downLine); red = $true },
        # attempt 1's tester (2026-10-05): forms the denylist did not list
        @{ n = 'docker-compose ps (real docker-compose.exe on PATH)'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before 'docker-compose -f $Script:OB1Compose ps' $upLine); red = $true },
        @{ n = '$sb={docker version}; Start-Job -ScriptBlock $sb'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before '$sb = { docker version }; Start-Job -ScriptBlock $sb | Wait-Job | Receive-Job' $upLine); red = $true },
        @{ n = '$ExecutionContext.InvokeCommand.InvokeScript(docker.exe)'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before "`$ExecutionContext.InvokeCommand.InvokeScript('docker.exe version')" $upLine); red = $true },
        @{ n = 'Set-Alias docker docker-compose'; fn = 'Start-OB1Stack'; from = $upLine; to = (Before 'Set-Alias docker docker-compose' $upLine); red = $true },
        @{ n = 'docker.cmd'; fn = 'Stop-OB1Stack'; from = $stopLine; to = (Before 'docker.cmd version' $stopLine); red = $true },
        # the coordinator's additions
        @{ n = 'New-Alias docker docker-compose'; fn = 'Reset-OB1Stack'; from = $downLine; to = (Before 'New-Alias docker docker-compose' $downLine); red = $true },
        @{ n = "[ScriptBlock]::Create('docker version').Invoke()"; fn = 'Reset-OB1Stack'; from = $downLine; to = (Before "[ScriptBlock]::Create('docker version').Invoke() | Out-Null" $downLine); red = $true },
        @{ n = 'Start-Job { docker version }'; fn = 'Stop-OB1Stack'; from = $stopLine; to = (Before 'Start-Job { docker version } | Wait-Job | Receive-Job' $stopLine); red = $true })
    $bad = 0
    $trip = New-Tripwire
    if ($trip.note) { Write-Host ("  [note] " + $trip.note) }
    $dir = Join-Path ([System.IO.Path]::GetTempPath()) ('pantry-mut-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir | Out-Null
    $prev = Enter-Tripwire $trip     # every child below runs with only the tripwires on PATH
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
            $o = & $psExe -NoProfile -ExecutionPolicy Bypass -File $self -Target $f | Out-String
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
        $o = & $psExe -NoProfile -ExecutionPolicy Bypass -File $self -Target $Target | Out-String
        $ok = ($LASTEXITCODE -eq 0)
        Write-Host ("  [{0}] the unmutated target stays green -> exit {1}" -f $(if ($ok) { 'OK' } else { 'FAIL' }), $LASTEXITCODE)
        if (-not $ok) { $bad++ }
    } finally {
        Exit-Tripwire $prev
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
    }
    $fired = Test-Path $trip.marker
    Write-Host ("  [{0}] no tripwire fired across every mutant and the shipped run (docker / docker-compose .cmd/.bat/.exe on PATH, DOCKER_HOST dead, empty DOCKER_CONFIG)" -f $(if (-not $fired) { 'OK' } else { 'FAIL' }))
    if ($fired) { $bad++; Get-Content $trip.marker | ForEach-Object { Write-Host "         fired: $_" } }
    Remove-Item -Recurse -Force $trip.dir -ErrorAction SilentlyContinue
    Write-Host ''
    if ($bad -eq 0) { Write-Host 'ALL MUTANT CASES BEHAVED' } else { Write-Host "$bad mutant case(s) FAILED" }
    exit $bad
}

$escapes = @(Get-AllowlistViolations $ast $want $Target)
if ($escapes.Count -gt 0) {
    Write-Host 'REFUSED: the functions under test execute something off the allowlist - the docker stub cannot intercept it, so running the behavioural layer could reach the REAL daemon:'
    foreach ($e in $escapes) { Write-Host ("  [FAIL] {0}" -f $e) }
    Write-Host 'Allowed: the bare `docker ...` command, the stubbed/lifted helpers and a few pure cmdlets (see $Script:AllowedCommands). Extend the list deliberately, or remove the construct.'
    exit $escapes.Count
}
if (-not $InChild) {
    # Belt and braces: re-run this verifier in a child whose PATH holds only tripwires, whose DOCKER_HOST is dead and
    # whose DOCKER_CONFIG has no contexts. The static layer above is the gate; this proves nothing slipped through it.
    $trip = New-Tripwire
    if ($trip.note) { Write-Host ("  [note] " + $trip.note) }
    $prev = Enter-Tripwire $trip
    try { & $psExe -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Target $Target -InChild; $childCode = $LASTEXITCODE }
    finally { Exit-Tripwire $prev }
    $fired = Test-Path $trip.marker
    if ($fired) { Write-Host '  [FAIL] a docker-named program was EXECUTED during the behavioural layer:'; Get-Content $trip.marker | ForEach-Object { Write-Host "         $_" } }
    else { Write-Host '  [OK]   tripwire: no docker / docker-compose program was executed (child ran with PATH = tripwires only, DOCKER_HOST dead, empty DOCKER_CONFIG)' }
    Remove-Item -Recurse -Force $trip.dir -ErrorAction SilentlyContinue
    exit ($childCode + $(if ($fired) { 1 } else { 0 }))
}
# Defence in depth (also set by the parent): whatever slips past finds no daemon to talk to.
$env:DOCKER_HOST = 'tcp://127.0.0.1:1'
$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $want }, $true)
Check 'only allowlisted commands and members execute in the five functions (static, AST)' ($escapes.Count -eq 0)
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
