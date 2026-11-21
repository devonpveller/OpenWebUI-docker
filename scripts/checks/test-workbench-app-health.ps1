# test-workbench-app-health.ps1 - health section 5c's enabled-profile switch (rwr-app-container round 3).
# openbrain-workbench-app has its OWN compose profile `workbench-app`, off until its first deploy. Section 5c of
# check-openbrain-health.ps1 reads it through Test-ObProfileEnabled: absent + profile off = "not enabled" (info);
# absent + profile on = Confirm-ObContainer (a fault). Test-ObProfileEnabled is lifted from the script by AST
# (never its main block) and run against throwaway repo roots; no docker is called.
#   powershell -NoProfile -File scripts\checks\test-workbench-app-health.ps1
# Exit code = number of failed cases. ASCII only.
param([string]$Repo = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$ErrorActionPreference = 'Stop'
$Repo = (Resolve-Path $Repo).Path
$failed = 0
function Say([string]$Name, [bool]$Ok) { if ($Ok) { Write-Host "  [OK]   $Name" } else { Write-Host "  [FAIL] $Name"; $script:failed++ } }

$src = Join-Path $Repo 'scripts\checks\check-openbrain-health.ps1'
$tokens = $null; $errs = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$tokens, [ref]$errs)
$fn = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-ObProfileEnabled' }, $true) | Select-Object -First 1
Say 'Test-ObProfileEnabled exists in the health script' ($null -ne $fn)
if ($null -eq $fn) { exit 1 }
Invoke-Expression $fn.Extent.Text

function New-Root([string[]]$StateProfiles, [string]$EnvLine) {
    $r = Join-Path ([System.IO.Path]::GetTempPath()) ("wah-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force (Join-Path $r '.stack'), (Join-Path $r 'OB1\docker') | Out-Null
    if ($null -ne $StateProfiles) {
        $state = @{ planes = @{ ob1 = @{ profiles = $StateProfiles } } } | ConvertTo-Json -Depth 5
        Set-Content -Path (Join-Path $r '.stack\state.json') -Value $state -Encoding ASCII
    }
    if ($EnvLine) { Set-Content -Path (Join-Path $r 'OB1\docker\.env') -Value $EnvLine -Encoding ASCII }
    return $r
}
$base = @('idea-refinery', 'research', 'wiki', 'notebook')
$cases = @(
    @{ N = 'default host (wiki on, app profile off) -> not enabled'; S = $base; E = 'COMPOSE_PROFILES=research,wiki,notebook'; Want = $false },
    @{ N = 'state.json carries workbench-app -> enabled';            S = ($base + 'workbench-app'); E = $null; Want = $true },
    @{ N = 'COMPOSE_PROFILES carries workbench-app -> enabled';      S = $null; E = 'COMPOSE_PROFILES=wiki, workbench-app'; Want = $true },
    @{ N = 'quoted COMPOSE_PROFILES -> enabled';                     S = $null; E = 'COMPOSE_PROFILES="wiki,workbench-app"'; Want = $true },
    @{ N = 'no state, no env -> not enabled';                        S = $null; E = $null; Want = $false },
    @{ N = 'a profile that merely contains the name -> not enabled'; S = $null; E = 'COMPOSE_PROFILES=workbench-app-x'; Want = $false }
)
foreach ($c in $cases) {
    $RepoRoot = New-Root $c.S $c.E
    Say $c.N ((Test-ObProfileEnabled 'workbench-app') -eq $c.Want)
    Remove-Item -Recurse -Force $RepoRoot
}
# unreadable state.json -> not enabled (the safe default)
$RepoRoot = New-Root $null $null
Set-Content -Path (Join-Path $RepoRoot '.stack\state.json') -Value '{not json' -Encoding ASCII
Say 'unreadable state.json -> not enabled' ((Test-ObProfileEnabled 'workbench-app') -eq $false)
Remove-Item -Recurse -Force $RepoRoot

# the 5c block: absent + off is info, absent + on goes to Confirm-ObContainer, running is checked either way
$text = Get-Content -Raw -Path $src
$s = $text.IndexOf("`$wbApp = 'openbrain-workbench-app'")
$block = $text.Substring($s, $text.IndexOf('# ---- 6.', $s) - $s)
Say "5c: absent AND profile off -> 'not enabled', not a fault" ($block -match "-eq 'absent' -and -not \(Test-ObProfileEnabled 'workbench-app'\)" -and $block -match 'not enabled')
Say '5c: every other non-running state still goes to Confirm-ObContainer' ($block -match 'else \{\s*Confirm-ObContainer \$wbApp')
Say '5c: running is checked whatever the profile says' ($block -match "if \(\`$wbAppState -eq 'running'\)")
Write-Host "$failed failed"
exit $failed
