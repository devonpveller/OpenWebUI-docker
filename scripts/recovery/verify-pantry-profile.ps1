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
$want = 'Test-OB1PantryEnabled', 'Get-OB1StartProfiles'
$fns = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -in $want }, $true)
Check 'both functions exist in emergency-recovery.ps1' ($fns.Count -eq 2)
foreach ($f in $fns) { . ([scriptblock]::Create($f.Extent.Text)) }

# The same constants the script declares, read from it rather than copied.
$src = Get-Content -Raw -Path $Target
$m = [regex]::Match($src, '(?s)\$Script:OB1Profiles = @\((.*?)\)')
$Script:OB1Profiles = @([regex]::Matches($m.Groups[1].Value, "'([^']+)'") | ForEach-Object { $_.Groups[1].Value })
$Script:OB1PantryProfile = 'pantry'
$Script:OB1Compose = 'OB1\docker\docker-compose.yml'

Check 'teardown/status list names pantry (a running one must go)' ($Script:OB1Profiles -contains 'pantry')
$upLines = @($src -split "`r?`n" | Where-Object { $_ -match 'compose -f \$Script:OB1Compose @prof up ' })
Check 'there are exactly two OB1 up sites' ($upLines.Count -eq 2) "(found $($upLines.Count))"
# each up site must be preceded by `$prof = Get-OB1StartProfiles`
$lines = $src -split "`r?`n"
$bad = @()
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match 'compose -f \$Script:OB1Compose @prof up ') {
        $prev = ($lines[[Math]::Max(0, $i - 3)..($i - 1)] -join "`n")
        if ($prev -notmatch 'Get-OB1StartProfiles') { $bad += ($i + 1) }
    }
}
Check 'both up sites take their flags from Get-OB1StartProfiles' ($bad.Count -eq 0) "(line(s) $($bad -join ','))"

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
}
finally { Pop-Location; Remove-Item -Recurse -Force $tmp }

Write-Host ''
if ($failed -eq 0) { Write-Host 'ALL PANTRY RECOVERY CASES PASSED' } else { Write-Host "$failed case(s) FAILED" }
exit $failed
