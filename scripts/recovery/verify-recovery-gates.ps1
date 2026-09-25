<#
.SYNOPSIS
    Executable proof for emergency-recovery.ps1's health gates (ac-recovery-gates,
    2026-09-25). Runs NO recovery mode and starts, stops or restarts NO stack
    container.

.DESCRIPTION
    The script under test is never dot-sourced (its main block would run a
    recovery). Only the named functions are lifted out of it with the PowerShell
    parser and defined in a child scope.

    DEFAULT (stub) MODE - no Docker needed. A PowerShell function named `docker`
    shadows docker.exe and models the host AFTER Part K: every plane is its own
    compose project, the root anchor project (a compose verb with no -f, run from
    the repo root) has ZERO services, and a container is found by `docker inspect`
    whatever project owns it. `docker compose ps <svc>` against a project that does
    not declare <svc> fails with `no such service: <svc>`, which is what the real
    CLI prints (measured read-only on this host 2026-09-25, from the repo root:
    `docker compose ps openwebui --format json` -> `no such service: openwebui`,
    exit 1). Start-Sleep is stubbed so timeouts cost nothing.

      -BaseRef <git ref>   also run the BASE cases against that revision of the
                           script (git show <ref>:scripts/recovery/emergency-recovery.ps1).
                           They assert the base is RED: its gate returns $false
                           for a HEALTHY container. A base that passes them is a
                           FAIL of this drill, not a success.

    -Live - read-only against THIS host's daemon, no stub for docker. Runs the head
    gate with -TimeoutSeconds 0 (one `docker inspect`) against openwebui, tailscale,
    llm-gateway and llama-cpp-upstream, and with -BaseRef the base gate (from the
    repo root, as the recovery script pins it) against openwebui. `docker compose ps`
    and `docker inspect` are the only docker verbs issued.

      -Negative            (with -Live) also `docker run` ONE throwaway container,
                           <NegativePrefix><random>, from -NegativeImage with
                           --network none and the harness owner label, command
                           `true`, so it exits at once; gate it (expect $false with
                           a named reason) and remove it in `finally`.

    Exit code = number of failed checks.

.EXAMPLE
    powershell -NoProfile -File scripts\recovery\verify-recovery-gates.ps1 -BaseRef work/adoption-closeout
.EXAMPLE
    powershell -NoProfile -File scripts\recovery\verify-recovery-gates.ps1 -Live -Negative -BaseRef work/adoption-closeout
#>
[CmdletBinding()]
param(
    [string]$Script = "",
    [string]$BaseRef = "",
    [switch]$Live,
    [switch]$Negative,
    [string]$NegativeImage = "alpine:3",
    [string]$NegativePrefix = "ac-rg-t-",
    [string]$Owner = "wt-ac-recovery-gates"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $Script) { $Script = Join-Path $RepoRoot "scripts\recovery\emergency-recovery.ps1" }
$Script:Failures = 0
$Script:Checks = 0

function Pass([string]$m) { $Script:Checks++; Write-Host "  PASS  $m" -ForegroundColor Green }
function Fail([string]$m) { $Script:Checks++; $Script:Failures++; Write-Host "  FAIL  $m" -ForegroundColor Red }
function Check([bool]$ok, [string]$m) { if ($ok) { Pass $m } else { Fail $m } }

# ---------------------------------------------------------------- loading
function Get-FunctionText {
    # The text of the named function definitions in $Path, by the parser - the
    # file is never executed. A missing name is simply absent from the result.
    param([string]$Path, [string[]]$Names)
    $tok = $null; $err = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tok, [ref]$err)
    if ($err -and $err.Count -gt 0) { throw "parse errors in ${Path}: $($err[0].Message)" }
    $defs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true)
    $text = @()
    foreach ($d in $defs) { if ($Names -contains $d.Name) { $text += $d.Extent.Text } }
    return ($text -join "`n`n")
}

$FnNames = @("Write-Log", "Get-ContainerHealth", "Wait-ForHealthy", "Test-NetworkConnectivity", "Test-PortalRunning")

function Invoke-Under {
    # Run $Body in a CHILD scope that holds the functions lifted from $Path.
    # Returns @{ Result = <non-log output>; Log = <Write-Host lines> }.
    param([string]$Path, [scriptblock]$Body)
    $defs = Get-FunctionText $Path $FnNames
    $raw = & {
        . ([scriptblock]::Create($defs))
        . $Body
    } 6>&1
    $log = @(); $res = @()
    foreach ($o in @($raw)) {
        if ($o -is [System.Management.Automation.InformationRecord]) { $log += "$o" } else { $res += $o }
    }
    return @{ Result = $res; Log = $log }
}

# ---------------------------------------------------------------- the stub host
# Compose projects after Part K: file -> project name. No -f = the root anchor.
$Script:ProjectOfFile = @{
    "frontend\docker-compose.yml"  = "frontend"
    "inference\docker-compose.yml" = "inference"
    "memory\docker-compose.yml"    = "memory"
    "search\docker-compose.yml"    = "search"
    "coder\docker-compose.yml"     = "coder"
    "portal\docker-compose.yml"    = "portal"
}

function Reset-FakeHost {
    # Health is a SEQUENCE: the Nth inspect of a container sees element N (the
    # last one repeats), so a starting -> healthy transition can be modelled.
    $Script:Calls = New-Object System.Collections.ArrayList
    $Script:Seen = @{}
    $Script:Fake = @(
        @{ Name = "openwebui";          Project = "frontend";  Service = "openwebui";          State = "running"; Health = @("healthy") },
        @{ Name = "tailscale";          Project = "frontend";  Service = "tailscale";          State = "running"; Health = @("healthy") },
        @{ Name = "llm-gateway";        Project = "inference"; Service = "llm-gateway";        State = "running"; Health = @("healthy") },
        @{ Name = "llama-cpp-upstream"; Project = "inference"; Service = "llama-cpp-upstream"; State = "running"; Health = @("healthy") },
        @{ Name = "mnemory";            Project = "memory";    Service = "mnemory";            State = "running"; Health = @("healthy") },
        @{ Name = "search-gateway";     Project = "search";    Service = "gateway";            State = "running"; Health = @("healthy") },
        @{ Name = "little-coder";       Project = "coder";     Service = "little-coder";       State = "running"; Health = @("healthy") },
        @{ Name = "caddy";              Project = "portal";    Service = "caddy";              State = "running"; Health = @("healthy") },
        @{ Name = "ac-rg-nohc";         Project = "x";         Service = "nohc";               State = "running"; Health = @("") },
        @{ Name = "ac-rg-exited";       Project = "x";         Service = "exited";             State = "exited";  Health = @("") },
        @{ Name = "ac-rg-unhealthy";    Project = "x";         Service = "unhealthy";          State = "running"; Health = @("unhealthy") },
        @{ Name = "ac-rg-starting";     Project = "x";         Service = "starting";           State = "running"; Health = @("starting", "healthy") }
    )
}

function Get-FakeHealth($c) {
    $n = 0; if ($Script:Seen.ContainsKey($c.Name)) { $n = $Script:Seen[$c.Name] }
    $Script:Seen[$c.Name] = $n + 1
    $i = [Math]::Min($n, $c.Health.Count - 1)
    return $c.Health[$i]
}

function Install-Stub {
    # Defined at SCRIPT scope so the lifted functions (in a child scope) resolve
    # `docker` and `Start-Sleep` to these rather than to docker.exe / the cmdlet.
    function Script:docker {
        $a = @($args | ForEach-Object { "$_" })
        [void]$Script:Calls.Add(($a -join ' '))
        $global:LASTEXITCODE = 0
        if ($a[0] -eq "compose") {
            $file = ""; $i = 1
            while ($i -lt $a.Count -and $a[$i].StartsWith("-")) {
                if ($a[$i] -eq "-f") { $file = $a[$i + 1].Replace("/", "\").ToLower(); $i += 2 } else { $i++ }
            }
            $proj = if ($file) { $Script:ProjectOfFile[$file] } else { "ai-stack" }
            $verb = $a[$i]
            $rest = @(); if ($a.Count -gt $i + 1) { $rest = $a[($i + 1)..($a.Count - 1)] }
            $svcs = @(); $j = 0
            while ($j -lt $rest.Count) {
                if ($rest[$j] -eq "--format") { $j += 2; continue }
                if ($rest[$j].StartsWith("-")) { $j++; continue }
                $svcs += $rest[$j]; $j++
            }
            foreach ($s in $svcs) {
                if ($verb -eq "exec" -and $s -ne $svcs[0]) { break }
                $hit = @($Script:Fake | Where-Object { $_.Project -eq $proj -and $_.Service -eq $s })
                if ($hit.Count -eq 0) { $global:LASTEXITCODE = 1; Write-Error "no such service: $s"; return }
            }
            if ($verb -eq "ps" -and $svcs.Count -gt 0) {
                $c = @($Script:Fake | Where-Object { $_.Project -eq $proj -and $_.Service -eq $svcs[0] })[0]
                return ('{"Name":"' + $c.Name + '","Service":"' + $c.Service + '","State":"' + $c.State + '","Health":"' + (Get-FakeHealth $c) + '"}')
            }
            return
        }
        if ($a[0] -eq "inspect") {
            $name = $a[$a.Count - 1]
            $c = @($Script:Fake | Where-Object { $_.Name -eq $name })
            if ($c.Count -eq 0) { $global:LASTEXITCODE = 1; Write-Error "Error: No such object: $name"; return }
            return ($c[0].State + "|" + (Get-FakeHealth $c[0]))
        }
        if ($a[0] -eq "exec") {
            $c = @($Script:Fake | Where-Object { $_.Name -eq $a[1] })
            if ($c.Count -eq 0) { $global:LASTEXITCODE = 1; Write-Error "Error response from daemon: No such container: $($a[1])"; return }
            if ($c[0].State -ne "running") { $global:LASTEXITCODE = 1; return }
            return "1 packets transmitted, 1 packets received"
        }
        return
    }
    function Script:Start-Sleep { param([int]$Seconds) }
}

function Remove-Stub {
    Remove-Item function:\docker -ErrorAction SilentlyContinue
    Remove-Item function:\Start-Sleep -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------- static checks
function Get-BareComposeCalls {
    # Every `docker compose ...` command in $Path WITHOUT -f / --file, by AST.
    param([string]$Path)
    $tok = $null; $err = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tok, [ref]$err)
    $cmds = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.CommandAst] }, $true)
    $bare = @()
    foreach ($c in $cmds) {
        $els = $c.CommandElements
        if ($els.Count -lt 2) { continue }
        if ($els[0].Extent.Text -ne "docker" -or $els[1].Extent.Text -ne "compose") { continue }
        $words = @($els | ForEach-Object { $_.Extent.Text })
        if ($words -contains "-f" -or $words -contains "--file") { continue }
        $bare += ("{0}: {1}" -f $c.Extent.StartLineNumber, (($words[2..($words.Count - 1)]) -join ' '))
    }
    return , $bare
}

function Test-StaticShape {
    param([string]$Path)
    Write-Host "[static] $Path"
    $tok = $null; $err = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$tok, [ref]$err)
    Check ($err.Count -eq 0) "parses under this PowerShell ($($PSVersionTable.PSVersion)) with zero errors (got $($err.Count))"
    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $nonAscii = @($bytes | Where-Object { $_ -gt 127 }).Count
    Check (-not $bom) "no UTF-8 BOM"
    Check ($nonAscii -eq 0) "ASCII only ($nonAscii bytes above 127)"
    # The ONLY bare compose verbs allowed: the root anchor's own network
    # create/drop, and the version probe. Anything else addresses a project with
    # no services.
    $allowed = @("up -d", "down", "version")
    $bare = Get-BareComposeCalls $Path
    $bad = @($bare | Where-Object { $allowed -notcontains (($_ -split ': ', 2)[1] -replace '\s*\|.*$', '' -replace '\s+Out-Null$', '').Trim() })
    foreach ($b in $bare) { Write-Host "         bare: $b" }
    Check ($bad.Count -eq 0) ("no bare ``docker compose`` outside {0} ({1} bare call(s) found, {2} not allowed{3})" -f ($allowed -join ' / '), $bare.Count, $bad.Count, $(if ($bad.Count) { ': ' + ($bad -join '; ') } else { '' }))
}

# ---------------------------------------------------------------- stub cases
function Test-HeadStub {
    param([string]$Path)
    Write-Host "[head, stub docker] $Path"
    Install-Stub
    try {
        foreach ($n in @("openwebui", "tailscale", "llm-gateway", "llama-cpp-upstream", "mnemory", "search-gateway", "little-coder")) {
            Reset-FakeHost
            $r = Invoke-Under $Path ([scriptblock]::Create("Wait-ForHealthy '$n' 10"))
            $composeCalls = @($Script:Calls | Where-Object { $_ -like "compose*" })
            Check (($r.Result[-1] -eq $true) -and $composeCalls.Count -eq 0) "Wait-ForHealthy $n -> True, by docker inspect (calls: $($Script:Calls -join ' ; '))"
        }

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-nohc" 10 }
        Check (($r.Result[-1] -eq $true) -and (($r.Log -join "`n") -match 'no health check')) "running container with no healthcheck -> True ('no health check' logged)"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-exited" 10 }
        $err = @($r.Log | Where-Object { $_ -match '\[ERROR\]' }) -join ' '
        Check (($r.Result[-1] -eq $false) -and ($err -match 'last seen: state=exited')) "exited container -> False, reason named: $err"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-missing" 10 }
        $err = @($r.Log | Where-Object { $_ -match '\[ERROR\]' }) -join ' '
        Check (($r.Result[-1] -eq $false) -and ($err -match "no such container 'ac-rg-missing'")) "absent container -> False, reason named: $err"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-unhealthy" 10 }
        $err = @($r.Log | Where-Object { $_ -match '\[ERROR\]' }) -join ' '
        Check (($r.Result[-1] -eq $false) -and ($err -match 'health=unhealthy')) "unhealthy container -> False, reason named: $err"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-starting" 10 }
        $n = @($Script:Calls | Where-Object { $_ -like "inspect*ac-rg-starting" }).Count
        Check (($r.Result[-1] -eq $true) -and $n -eq 2) "starting -> healthy passes on the 2nd poll ($n inspect calls)"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "ac-rg-exited" 0 }
        $n = @($Script:Calls | Where-Object { $_ -like "inspect*" }).Count
        Check (($r.Result[-1] -eq $false) -and $n -eq 1) "-TimeoutSeconds 0 is exactly one read ($n inspect calls)"

        Reset-FakeHost
        $r = Invoke-Under $Path { Test-NetworkConnectivity "tailscale" }
        Check (($r.Result[-1] -eq $true) -and (@($Script:Calls)[0] -like "exec tailscale ping*")) "Test-NetworkConnectivity tailscale -> True via docker exec (calls: $($Script:Calls -join ' ; '))"

        Reset-FakeHost
        $r = Invoke-Under $Path { Test-NetworkConnectivity "ac-rg-missing" }
        Check ($r.Result[-1] -eq $false) "Test-NetworkConnectivity on an absent container -> False"

        Reset-FakeHost
        $r = Invoke-Under $Path { Test-PortalRunning }
        Check ($r.Result[-1] -eq $true) "Test-PortalRunning sees a running caddy (own project) -> True"
        $Script:Fake = @($Script:Fake | Where-Object { $_.Name -ne "caddy" })
        $r = Invoke-Under $Path { Test-PortalRunning }
        Check ($r.Result[-1] -eq $false) "Test-PortalRunning with no caddy -> False"
    }
    finally { Remove-Stub }
}

function Test-BaseStub {
    # RED expected: the base gate cannot see a healthy container.
    param([string]$Path)
    Write-Host "[BASE, stub docker - expect RED] $Path"
    Install-Stub
    try {
        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "openwebui" 10 }
        $bareps = @($Script:Calls | Where-Object { $_ -like "compose ps openwebui*" })
        $log = $r.Log -join "`n"
        Check (($r.Result[-1] -eq $false) -and $bareps.Count -ge 1 -and ($log -match 'no such service: openwebui')) "BASE RED: Wait-ForHealthy openwebui -> False for a HEALTHY container; it asked the root anchor ($($bareps.Count)x 'compose ps openwebui' with no -f) and logged 'no such service'"

        Reset-FakeHost
        $r = Invoke-Under $Path { Wait-ForHealthy "search-gateway" 10 }
        Check ($r.Result[-1] -eq $false) "BASE RED: Wait-ForHealthy search-gateway -> False (its compose service key is 'gateway', so a -f alone would not fix this gate either - measured live, not tested here)"

        Reset-FakeHost
        $r = Invoke-Under $Path { Test-NetworkConnectivity "tailscale" }
        Check (($r.Result[-1] -eq $false) -and (@($Script:Calls)[0] -like "compose exec tailscale*")) "BASE RED: Test-NetworkConnectivity tailscale -> False (bare 'compose exec')"
    }
    finally { Remove-Stub }
    $bare = Get-BareComposeCalls $Path
    Write-Host ("         base has {0} bare ``docker compose`` call(s):" -f $bare.Count)
    foreach ($b in $bare) { Write-Host "         bare: $b" }
}

# ---------------------------------------------------------------- live (read-only)
function Test-Live {
    param([string]$Path, [string]$BasePath)
    Write-Host "[LIVE, read-only] $Path"
    # Start-Sleep only is stubbed; docker is the real one.
    function Script:Start-Sleep { param([int]$Seconds) }
    Push-Location $RepoRoot
    try {
        foreach ($n in @("openwebui", "tailscale", "llm-gateway", "llama-cpp-upstream")) {
            $r = Invoke-Under $Path ([scriptblock]::Create("Wait-ForHealthy '$n' 0"))
            $line = @($r.Log | Where-Object { $_ -match "$n (is|status)" }) -join ' '
            Check ($r.Result[-1] -eq $true) "LIVE head gate ${n}: $line"
        }
        if ($BasePath) {
            $r = Invoke-Under $BasePath { Wait-ForHealthy "openwebui" 5 }
            $log = ($r.Log | Where-Object { $_ -match 'openwebui' }) -join ' | '
            Check (($r.Result[-1] -eq $false) -and ($log -match 'no such service')) "LIVE BASE RED from the repo root: Wait-ForHealthy openwebui -> $($r.Result[-1]); $log"
        }
        if ($Negative) {
            $name = $NegativePrefix + ([guid]::NewGuid().ToString("N").Substring(0, 8))
            try {
                $ErrorActionPreference = "Continue"
                $null = docker run --name $name --network none --label "ai-stack.harness.owner=$Owner" --entrypoint true $NegativeImage 2>&1
                $ErrorActionPreference = "Stop"
                $r = Invoke-Under $Path ([scriptblock]::Create("Wait-ForHealthy '$name' 0"))
                $err = @($r.Log | Where-Object { $_ -match '\[ERROR\]' }) -join ' '
                Check (($r.Result[-1] -eq $false) -and ($err -match 'last seen: state=exited')) "LIVE stopped throwaway $name -> False, reason named: $err"
            }
            finally {
                $ErrorActionPreference = "Continue"
                $null = docker rm -f $name 2>&1
                $left = @(docker ps -a --filter "name=$name" --format "{{.Names}}")
                $ErrorActionPreference = "Stop"
                Check ($left.Count -eq 0) "throwaway $name removed"
            }
        }
    }
    finally {
        Pop-Location
        Remove-Item function:\Start-Sleep -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------- main
$basePath = ""
if ($BaseRef) {
    $basePath = Join-Path ([System.IO.Path]::GetTempPath()) ("er-base-{0}.ps1" -f ([guid]::NewGuid().ToString("N").Substring(0, 8)))
    # Through cmd so the bytes land unchanged: piping native output through
    # PowerShell re-decodes it, and the base file starts with a UTF-8 BOM.
    cmd /c "git -C `"$RepoRoot`" show `"${BaseRef}:scripts/recovery/emergency-recovery.ps1`" > `"$basePath`" 2>nul"
    if ($LASTEXITCODE -ne 0) { throw "git show ${BaseRef}:scripts/recovery/emergency-recovery.ps1 failed (exit $LASTEXITCODE)" }
}
try {
    if ($Live) {
        Test-Live $Script $basePath
    }
    else {
        Test-StaticShape $Script
        Test-HeadStub $Script
        if ($basePath) { Test-BaseStub $basePath }
    }
}
finally {
    if ($basePath -and (Test-Path $basePath)) { Remove-Item $basePath -Force }
}

Write-Host ""
$color = "Green"; if ($Script:Failures) { $color = "Red" }
Write-Host ("{0} check(s), {1} failed" -f $Script:Checks, $Script:Failures) -ForegroundColor $color
exit $Script:Failures
