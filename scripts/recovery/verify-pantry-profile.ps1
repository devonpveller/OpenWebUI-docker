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

    Exit code = number of failed cases.
#>
[CmdletBinding()]
param([string]$Target = '')

$ErrorActionPreference = 'Stop'
if (-not $Target) { $Target = Join-Path $PSScriptRoot 'emergency-recovery.ps1' }
$failed = 0
function Check($name, $ok, $detail = '') {
    if ($ok) { Write-Host ("  [OK]   {0}" -f $name) }
    else { Write-Host ("  [FAIL] {0} {1}" -f $name, $detail); $script:failed++ }
}

$ast = [System.Management.Automation.Language.Parser]::ParseFile($Target, [ref]$null, [ref]$null)
$want = 'Test-OB1PantryEnabled', 'Get-OB1StartProfiles', 'Start-OB1Stack', 'Reset-OB1Stack', 'Stop-OB1Stack'
$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $want }, $true)
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
