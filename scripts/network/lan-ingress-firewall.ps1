# lan-ingress-firewall.ps1 - the Windows Firewall guard for ai-stack ports published on the
# workstation's LAN address (cluster-transition PLAN section 5.1, Track B item B1;
# harness item edge-b1-fw, 2026-10-16).
#
# WHY. Docker Desktop installs its own inbound rule (com.docker.backend: Allow, any remote
# address, any port). The moment a compose file publishes a port on the LAN IP, that port is
# open to the whole LAN. Some of the services behind those ports have no login of their own.
# In Windows Firewall a BLOCK rule beats an ALLOW rule, so the guard is one inbound BLOCK rule
# per listed port whose remote addresses are EVERYTHING EXCEPT the allowed nodes (the Proxmox
# Traefik nodes): every other IPv4 address and the whole IPv6 space. Docker's rule is never
# edited or disabled; the block simply wins over it.
#
# SOURCE OF TRUTH. The port list is stack.manifest.toml [lan_ingress.ports] (tracked). The
# addresses - host_ip and allowed_remote - are per-host and live in the gitignored
# .stack/lan-ingress.toml beside the manifest, because this public repository keeps the
# operator's LAN addresses out of its tracked files (see Read-Source below). This script
# reads both with Python's tomllib (the driver's own parser; PS 5.1 has no TOML parser),
# then validates them strictly here. Anything it cannot read or does not fully understand
# ABORTS with no firewall change: a missing table, an unknown key, an address that is not a
# plain dotted-quad IPv4 (no CIDR, no ranges, no wildcards, no leading zeros), an empty
# address or port list, a port outside 1-65535 or not an integer, a duplicate port, a plane
# the manifest does not declare.
#
# RULES OWNED. Every rule this script creates is named  ai-stack-lan-guard-<service>-<port>.
# -Apply and -Remove only ever touch rules whose Name starts with that prefix, so Docker's
# rules and anything else on the machine are out of reach.
#
# MODES
#   -Status            read-only, no elevation: each rule OK / MISSING / DRIFT / STALE, and
#                      which listed ports are listening where (LAN IP, all addresses,
#                      loopback). Exit 0 = in sync, 3 = something to fix.
#   -Apply             elevated: create missing rules, replace drifted ones, remove stale ones
#                      (a port no longer listed). Idempotent; a second run changes nothing.
#   -Remove            elevated: delete every rule with the prefix (the rollback). Does not
#                      read the manifest, so it works even if the manifest is broken.
#   -WhatIf / -DryRun  with -Apply or -Remove: print the exact plan and change nothing. Needs
#                      no elevation. Alone, it means -Apply -WhatIf.
#   -ManifestPath      another manifest (tests use fixtures). Default: the repo's.
#   -HostFile          the per-host address file. Default: <manifest dir>\.stack\lan-ingress.toml.
#
# Exit codes: 0 ok, 1 aborted (bad source / failed step), 2 refused (not elevated),
# 3 -Status found missing, drifted or stale rules.
#
# TEST SEAM. Tests shadow the NetSecurity cmdlets with functions (see
# test-lan-ingress-firewall.ps1) and fake elevation by defining a function named
# Test-LanIngressGuardAdmin in the caller's scope. That seam can only make this script
# ATTEMPT a change; the OS still refuses firewall writes from a non-elevated process.
#
# Runbook: documentation/runbooks/lan-ingress-guard.md (apply before publish, verify, roll back).

[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$Status,
    [switch]$Apply,
    [switch]$Remove,
    [switch]$DryRun,
    [string]$ManifestPath = '',
    [string]$HostFile = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2
# Fail closed: anything unexpected stops the script. Every write happens in Invoke-Plan,
# which reports its own partial progress, so an error reaching this trap before it means
# nothing was changed.
trap {
    Write-Host ("ABORT: unexpected error: " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "Run -Status to see the firewall state." -ForegroundColor Red
    exit 1
}

$Prefix = 'ai-stack-lan-guard-'
$RuleGroup = 'ai-stack LAN ingress guard'
$V6All = '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff'
$Octet = '(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])'
# \z, never $: in .NET `$` also matches before a final newline, so "192.0.2.11`n" would pass.
$V4Rx = '^' + $Octet + '\.' + $Octet + '\.' + $Octet + '\.' + $Octet + '\z'
$SvcRx = '^[a-z0-9][a-z0-9-]{0,40}\z'
$Dry = [bool]($DryRun -or $WhatIfPreference)

function Say([string]$Text) { Write-Host $Text }
function Stop-Abort([string]$Why) {
    Write-Host ("ABORT: " + $Why) -ForegroundColor Red
    Write-Host "No firewall change was made." -ForegroundColor Red
    exit 1
}

# ------------------------------------------------------------------ elevation
function Test-Elevated {
    $seam = Get-Command -Name 'Test-LanIngressGuardAdmin' -CommandType Function -ErrorAction SilentlyContinue
    if ($seam) { return [bool](& $seam) }
    $p = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ------------------------------------------------------------------ addresses
function Test-StrictV4([object]$s) { return (($s -is [string]) -and ($s -cmatch $V4Rx)) }

function ConvertTo-BigInt([byte[]]$BigEndian) {
    $le = New-Object byte[] ($BigEndian.Length + 1)
    for ($i = 0; $i -lt $BigEndian.Length; $i++) { $le[$i] = $BigEndian[$BigEndian.Length - 1 - $i] }
    return New-Object System.Numerics.BigInteger (, $le)
}
function ConvertFrom-V4([string]$s) {
    if (-not (Test-StrictV4 $s)) { return $null }
    $o = $s.Split('.')
    return [int64]$o[0] * 16777216 + [int64]$o[1] * 65536 + [int64]$o[2] * 256 + [int64]$o[3]
}
function Format-V4([int64]$v) {
    return ('{0}.{1}.{2}.{3}' -f (($v -shr 24) -band 255), (($v -shr 16) -band 255), (($v -shr 8) -band 255), ($v -band 255))
}
# One address token as the firewall may print it -> @(family, start, end) as BigIntegers, or
# $null when it is anything this script does not understand (a keyword such as Any or
# LocalSubnet, a malformed address). $null always counts as drift.
function ConvertTo-Interval([string]$tok) {
    $tok = $tok.Trim()
    if ($tok -match ':') {
        $fam = 6; $bits = 128
        $parse = {
            param($t) $a = $null
            if ([Net.IPAddress]::TryParse($t, [ref]$a) -and $a.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetworkV6) { return (ConvertTo-BigInt $a.GetAddressBytes()) }
            return $null
        }
    }
    else {
        $fam = 4; $bits = 32
        $parse = { param($t) $v = ConvertFrom-V4 $t; if ($null -eq $v) { return $null } return [System.Numerics.BigInteger]$v }
    }
    if ($tok -match '^([^-/]+)-([^-/]+)\z') {
        $m1 = $Matches[1]; $m2 = $Matches[2]
        $a = & $parse $m1; $b = & $parse $m2
        if ($null -eq $a -or $null -eq $b -or $a -gt $b) { return $null }
        return , @($fam, $a, $b)
    }
    if ($tok -match '^([^-/]+)/([^-/]+)\z') {
        $m1 = $Matches[1]; $m = $Matches[2]
        $base = & $parse $m1
        if ($null -eq $base) { return $null }
        if ($m -match '^[0-9]{1,3}\z') { $len = [int]$m }
        elseif ($fam -eq 4 -and (Test-StrictV4 $m)) {
            $mv = ConvertFrom-V4 $m; $len = $null
            for ($l = 0; $l -le 32; $l++) {
                $mk = [int64]4294967295 - ([int64][math]::Pow(2, 32 - $l) - 1)
                if ($mk -eq $mv) { $len = $l; break }
            }
            if ($null -eq $len) { return $null }
        }
        else { return $null }
        if ($len -lt 0 -or $len -gt $bits) { return $null }
        $size = [System.Numerics.BigInteger]::Pow(2, $bits - $len)
        $start = $base - ($base % $size)
        return , @($fam, $start, ($start + $size - 1))
    }
    $one = & $parse $tok
    if ($null -eq $one) { return $null }
    return , @($fam, $one, $one)
}
# A list of address tokens -> a canonical key (merged intervals per family), or $null.
function Get-AddressKey([object[]]$tokens) {
    $iv = @()
    foreach ($t in @($tokens)) {
        if ($null -eq $t) { return $null }
        $x = ConvertTo-Interval ([string]$t)
        if ($null -eq $x) { return $null }
        $iv += , $x
    }
    if ($iv.Count -eq 0) { return $null }
    $parts = @()
    foreach ($fam in @(4, 6)) {
        $mine = @($iv | Where-Object { $_[0] -eq $fam } | Sort-Object { $_[1] })
        $merged = @()
        foreach ($x in $mine) {
            if ($merged.Count -gt 0 -and $x[1] -le ($merged[-1][1] + 1)) {
                if ($x[2] -gt $merged[-1][1]) { $merged[-1][1] = $x[2] }
            }
            else { $merged += , @($x[1], $x[2]) }
        }
        $parts += ('v' + $fam + ':' + (($merged | ForEach-Object { $_[0].ToString() + '-' + $_[1].ToString() }) -join ','))
    }
    return ($parts -join ';')
}

# The remote addresses to BLOCK: the IPv4 complement of the allowed nodes, plus all of IPv6.
function Get-BlockRanges([string[]]$Allowed) {
    $vals = @($Allowed | ForEach-Object { ConvertFrom-V4 $_ } | Sort-Object -Unique)
    $out = @()
    $start = [int64]0
    foreach ($v in $vals) {
        if ($v -gt $start) { $out += $(if ($start -eq $v - 1) { Format-V4 $start } else { (Format-V4 $start) + '-' + (Format-V4 ($v - 1)) }) }
        $start = $v + 1
    }
    $max = [int64]4294967295
    if ($start -le $max) { $out += $(if ($start -eq $max) { Format-V4 $max } else { (Format-V4 $start) + '-' + (Format-V4 $max) }) }
    return @{ V4 = $out; V6 = @($V6All); All = @($out + $V6All) }
}

# ------------------------------------------------------------------ the source of truth
# Two files, one home per fact. The PORT LIST is tracked in stack.manifest.toml
# [lan_ingress.ports]. The ADDRESSES (host_ip, allowed_remote) identify the operator's LAN,
# which this public repository keeps out of its tracked files (scripts/checks/check_identity.py
# refuses RFC 1918 literals), so they live in the per-host, gitignored
# <manifest dir>\.stack\lan-ingress.toml (template: scripts/network/lan-ingress.host.example.toml).
# The manifest MAY carry them instead; a key set in both files aborts (two sources).
function Read-Source([string]$Path, [string]$HostPath) {
    if (-not $Path) { $Path = Join-Path $PSScriptRoot '..\..\stack.manifest.toml' }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { Stop-Abort "source of truth not found: $Path" }
    $Path = (Resolve-Path -LiteralPath $Path).Path
    if (-not $HostPath) { $HostPath = Join-Path (Split-Path -Parent $Path) '.stack\lan-ingress.toml' }
    $hostPresent = Test-Path -LiteralPath $HostPath -PathType Leaf
    if ($hostPresent) { $HostPath = (Resolve-Path -LiteralPath $HostPath).Path }
    elseif (Test-Path -LiteralPath $HostPath) { Stop-Abort "$HostPath exists but is not a file" }
    $py = $null; $pyArgs = @()
    $c = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { $py = $c.Source }
    else {
        $c = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { $py = $c.Source; $pyArgs = @('-3') }
    }
    if (-not $py) { Stop-Abort "python (>= 3.11, for tomllib) is not on PATH; it is needed to read $Path" }
    # Single quotes only inside: PS 5.1 mangles embedded double quotes on a native command line.
    $code = "import sys, os, json, tomllib; m = tomllib.load(open(sys.argv[1], 'rb')); h = tomllib.load(open(sys.argv[2], 'rb')) if os.path.isfile(sys.argv[2]) else None; print(json.dumps({'lan_ingress': m.get('lan_ingress'), 'planes': sorted((m.get('planes') or {}).keys()), 'host_present': h is not None, 'host_keys': sorted((h or {}).keys()), 'host': (h or {}).get('lan_ingress')}))"
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $raw = @(& $py @pyArgs -c $code $Path $HostPath 2>&1); $rc = $LASTEXITCODE }
    finally { $ErrorActionPreference = $prev }
    $errs = @($raw | Where-Object { $_ -is [System.Management.Automation.ErrorRecord] } | ForEach-Object { $_.ToString() } | Where-Object { $_.Trim() })
    $outs = @($raw | Where-Object { $_ -isnot [System.Management.Automation.ErrorRecord] } | ForEach-Object { [string]$_ })
    if ($rc -ne 0) {
        $last = if ($errs.Count) { $errs[-1] } else { "exit $rc" }
        Stop-Abort ("could not read $Path" + $(if ($hostPresent) { " / $HostPath" } else { '' }) + " as TOML: $last")
    }
    try { $data = ($outs -join "`n") | ConvertFrom-Json }
    catch { Stop-Abort "could not parse the reader's output for $Path" }
    if ([bool]$data.host_present -ne [bool]$hostPresent) { Stop-Abort "$HostPath changed while it was being read" }
    return @{ Path = $Path; HostPath = $HostPath; HostPresent = $hostPresent; Data = $data }
}

function Get-Props($o) { if ($o -is [System.Management.Automation.PSCustomObject]) { return , @($o.PSObject.Properties) } return $null }

function Test-Source($src) {
    $li = $src.Data.lan_ingress
    $planes = @($src.Data.planes)
    $hp = $src.HostPath
    if ($null -eq $li) { Stop-Abort "$($src.Path) has no [lan_ingress] table" }
    $props = Get-Props $li
    if ($null -eq $props) { Stop-Abort '[lan_ingress] is not a table' }
    $known = @('host_ip', 'allowed_remote', 'ports')
    # Case-SENSITIVE throughout, like the host file: TOML keys are case-sensitive, so
    # `Allowed_Remote` is an unknown key, not a spelling of allowed_remote.
    foreach ($p in $props) { if ($known -cnotcontains $p.Name) { Stop-Abort "[lan_ingress] in the manifest has an unknown key '$($p.Name)' (known: $($known -join ', '))" } }
    $val = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
    $from = New-Object System.Collections.Hashtable ([StringComparer]::Ordinal)
    foreach ($p in $props) { $val[$p.Name] = $p.Value; $from[$p.Name] = 'manifest' }

    if ($src.HostPresent) {
        $top = @($src.Data.host_keys)
        foreach ($t in $top) { if ($t -cne 'lan_ingress') { Stop-Abort "$hp has an unknown table or key '$t' (only [lan_ingress] belongs there)" } }
        if ($null -eq $src.Data.host) { Stop-Abort "$hp has no [lan_ingress] table" }
        $hprops = Get-Props $src.Data.host
        if ($null -eq $hprops) { Stop-Abort "[lan_ingress] in $hp is not a table" }
        foreach ($p in $hprops) {
            if ($p.Name -ceq 'ports') { Stop-Abort "$hp sets 'ports'; the port list belongs in the tracked manifest only" }
            if (@('host_ip', 'allowed_remote') -cnotcontains $p.Name) { Stop-Abort "[lan_ingress] in $hp has an unknown key '$($p.Name)' (known: host_ip, allowed_remote)" }
            if ($val.ContainsKey($p.Name)) { Stop-Abort "'$($p.Name)' is set in both $($src.Path) and $hp; keep it in one place" }
            $val[$p.Name] = $p.Value; $from[$p.Name] = $hp
        }
    }
    foreach ($k in @('host_ip', 'allowed_remote')) {
        if (-not $val.ContainsKey($k)) {
            Stop-Abort ("'$k' is not set. Put it in $hp (copy scripts/network/lan-ingress.host.example.toml)" + $(if ($src.HostPresent) { '' } else { '; that file does not exist' }))
        }
    }
    if (-not $val.ContainsKey('ports')) { Stop-Abort "[lan_ingress] in the manifest is missing 'ports'" }

    if (-not (Test-StrictV4 $val.host_ip)) { Stop-Abort "[lan_ingress] host_ip '$($val.host_ip)' is not a plain IPv4 address" }

    $allowed = $val.allowed_remote
    if (-not ($allowed -is [System.Array])) { Stop-Abort '[lan_ingress] allowed_remote must be a list of IPv4 addresses' }
    if ($allowed.Count -eq 0) { Stop-Abort '[lan_ingress] allowed_remote is empty' }
    foreach ($a in $allowed) {
        if (-not (Test-StrictV4 $a)) { Stop-Abort "[lan_ingress] allowed_remote entry '$a' is not a plain IPv4 address (no CIDR, ranges, wildcards, keywords, IPv6 or leading zeros)" }
    }

    $ports = Get-Props $val.ports
    if ($null -eq $ports) { Stop-Abort '[lan_ingress.ports] is not a table' }
    if ($ports.Count -eq 0) { Stop-Abort '[lan_ingress.ports] is empty' }
    $seen = @{}
    $rows = @()
    foreach ($p in $ports) {
        $svc = $p.Name
        if ($svc -cnotmatch $SvcRx) { Stop-Abort "[lan_ingress.ports] service name '$svc' must match $SvcRx" }
        $e = Get-Props $p.Value
        if ($null -eq $e) { Stop-Abort "[lan_ingress.ports] $svc must be { plane = ..., port = ... }" }
        foreach ($x in $e) { if (@('plane', 'port') -cnotcontains $x.Name) { Stop-Abort "[lan_ingress.ports] $svc has an unknown key '$($x.Name)'" } }
        $plane = $p.Value.PSObject.Properties['plane']; $port = $p.Value.PSObject.Properties['port']
        if (-not $plane -or -not $port) { Stop-Abort "[lan_ingress.ports] $svc needs both plane and port" }
        if (-not ($plane.Value -is [string]) -or ($planes -cnotcontains $plane.Value)) { Stop-Abort "[lan_ingress.ports] $svc names plane '$($plane.Value)', which the manifest does not declare" }
        $pv = $port.Value
        if (-not (($pv -is [int]) -or ($pv -is [long]))) { Stop-Abort "[lan_ingress.ports] $svc port '$pv' is not an integer" }
        if ($pv -lt 1 -or $pv -gt 65535) { Stop-Abort "[lan_ingress.ports] $svc port $pv is outside 1-65535" }
        if ($seen.ContainsKey([string]$pv)) { Stop-Abort "[lan_ingress.ports] port $pv is listed twice ($($seen[[string]$pv]) and $svc)" }
        $seen[[string]$pv] = $svc
        $rows += [pscustomobject]@{ Service = $svc; Plane = $plane.Value; Port = [int]$pv }
    }
    return [pscustomobject]@{ HostIp = $val.host_ip; Allowed = @($allowed | ForEach-Object { [string]$_ }); Ports = $rows; AddrFrom = $from['allowed_remote'] }
}

function Get-DesiredRules($cfg) {
    $ranges = Get-BlockRanges $cfg.Allowed
    $key = Get-AddressKey $ranges.All
    if ($null -eq $key) { Stop-Abort 'internal: the computed block ranges did not parse back' }
    $rules = @()
    foreach ($p in ($cfg.Ports | Sort-Object Port)) {
        $rules += [pscustomobject]@{
            Name          = $Prefix + $p.Service + '-' + $p.Port
            DisplayName   = "ai-stack LAN guard: $($p.Service) tcp/$($p.Port) (only $($cfg.Allowed -join ', '))"
            Description   = "Managed by scripts/network/lan-ingress-firewall.ps1 from stack.manifest.toml [lan_ingress]. Blocks every remote address except $($cfg.Allowed -join ', '). Do not edit by hand; re-run -Apply."
            Service       = $p.Service
            Plane         = $p.Plane
            Port          = $p.Port
            RemoteAddress = $ranges.All
            RemoteKey     = $key
        }
    }
    return @{ Rules = $rules; Ranges = $ranges }
}

# ------------------------------------------------------------------ the firewall (read)
function Test-OwnName([string]$Name) { return ($Name -and $Name.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase)) }

function Get-GuardRules {
    # A property's value as one comparable string: $null when the object lacks it (StrictMode-safe),
    # arrays joined with ','. A missing property can therefore never read as the expected value.
    function Get-FieldText($o, [string]$Name) {
        if ($null -eq $o) { return $null }
        $p = $o.PSObject.Properties[$Name]
        if ($null -eq $p) { return $null }
        if ($null -eq $p.Value) { return '' }
        return ((@($p.Value) | ForEach-Object { [string]$_ }) -join ',')
    }
    # (nested, so Get-GuardRules is self-contained)
    try { $all = @(Get-NetFirewallRule -Name ($Prefix + '*') -ErrorAction Stop) }
    catch {
        if ($_.CategoryInfo.Category -eq 'ObjectNotFound') { $all = @() }
        else { Stop-Abort ("could not read the firewall rules: " + $_.Exception.Message) }
    }
    $out = @()
    foreach ($r in $all) {
        if (-not (Test-OwnName ([string]$r.Name))) { continue }
        # EVERY filter a rule has, because each one can narrow where a BLOCK applies: a block
        # rule limited to remote port 1, one service, one interface, one user or one package
        # blocks (almost) nothing while still looking present (tester finding TF1).
        try {
            $pf = Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $af = Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $ap = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $sv = Get-NetFirewallServiceFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $it = Get-NetFirewallInterfaceTypeFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $if = Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
            $sf = Get-NetFirewallSecurityFilter -AssociatedNetFirewallRule $r -ErrorAction Stop
        }
        catch { Stop-Abort ("could not read the filters of rule $($r.Name): " + $_.Exception.Message) }
        $out += [pscustomobject]@{
            Name               = [string]$r.Name
            Direction          = Get-FieldText $r 'Direction'
            Action             = Get-FieldText $r 'Action'
            Enabled            = Get-FieldText $r 'Enabled'
            Profile            = Get-FieldText $r 'Profile'
            Owner              = Get-FieldText $r 'Owner'
            Platform           = Get-FieldText $r 'Platform'
            Protocol           = Get-FieldText $pf 'Protocol'
            LocalPort          = @(@($pf.LocalPort) | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ })
            RemotePort         = Get-FieldText $pf 'RemotePort'
            DynamicTarget      = Get-FieldText $pf 'DynamicTarget'
            LocalAddress       = Get-FieldText $af 'LocalAddress'
            RemoteAddress      = @(@($af.RemoteAddress) | Where-Object { $null -ne $_ } | ForEach-Object { [string]$_ })
            Program            = Get-FieldText $ap 'Program'
            Package            = Get-FieldText $ap 'Package'
            Service            = Get-FieldText $sv 'Service'
            InterfaceType      = Get-FieldText $it 'InterfaceType'
            InterfaceAlias     = Get-FieldText $if 'InterfaceAlias'
            LocalUser          = Get-FieldText $sf 'LocalUser'
            RemoteUser         = Get-FieldText $sf 'RemoteUser'
            RemoteMachine      = Get-FieldText $sf 'RemoteMachine'
            Authentication     = Get-FieldText $sf 'Authentication'
            Encryption         = Get-FieldText $sf 'Encryption'
            OverrideBlockRules = Get-FieldText $sf 'OverrideBlockRules'
        }
    }
    return $out
}

# The drift check. EVERY field below must hold exactly this value (shapes measured read-only
# on this host's rules, 2026-10-16: an unrestricted filter reads 'Any', no package/owner/
# platform reads empty). Anything else - including a field the read could not find - is
# drift, and -Apply replaces the rule. LocalPort and RemoteAddress are compared separately.
function Get-Drift($have, $want) {
    $expect = [ordered]@{
        Direction = 'Inbound'; Action = 'Block'; Enabled = 'True'; Profile = 'Any'
        Owner = ''; Platform = ''
        Protocol = 'TCP'; RemotePort = 'Any'; DynamicTarget = 'Any'
        LocalAddress = 'Any'
        Program = 'Any'; Package = ''
        Service = 'Any'; InterfaceType = 'Any'; InterfaceAlias = 'Any'
        LocalUser = 'Any'; RemoteUser = 'Any'; RemoteMachine = 'Any'
        Authentication = 'NotRequired'; Encryption = 'NotRequired'; OverrideBlockRules = 'False'
    }
    $why = @()
    foreach ($f in $expect.Keys) {
        $p = $have.PSObject.Properties[$f]
        $v = if ($null -eq $p) { $null } else { $p.Value }
        if ($null -eq $v -or [string]$v -ne $expect[$f]) {
            $shown = if ($null -eq $v) { '<unreadable>' } elseif ([string]$v -eq '') { '<empty>' } else { [string]$v }
            $why += ("{0} {1}" -f $f.ToLowerInvariant(), $shown)
        }
    }
    if ((@($have.LocalPort) -join ',') -ne [string]$want.Port) { $why += "localport $(@($have.LocalPort) -join ',')" }
    $k = Get-AddressKey @($have.RemoteAddress)
    if ($null -eq $k -or $k -ne $want.RemoteKey) { $why += "remoteaddress $(@($have.RemoteAddress) -join ',')" }
    return $why
}

function Get-Plan($desired, $current) {
    $plan = @()
    foreach ($w in $desired) {
        $h = @($current | Where-Object { $_.Name -eq $w.Name })
        if ($h.Count -eq 0) { $plan += [pscustomobject]@{ Op = 'CREATE'; Name = $w.Name; Want = $w; Why = '' } }
        else {
            $d = @(Get-Drift $h[0] $w)
            if ($d.Count -eq 0) { $plan += [pscustomobject]@{ Op = 'KEEP'; Name = $w.Name; Want = $w; Why = '' } }
            else { $plan += [pscustomobject]@{ Op = 'REPLACE'; Name = $w.Name; Want = $w; Why = ('drift: ' + ($d -join '; ')) } }
        }
    }
    foreach ($h in $current) {
        if (-not ($desired | Where-Object { $_.Name -eq $h.Name })) {
            $plan += [pscustomobject]@{ Op = 'REMOVE'; Name = $h.Name; Want = $null; Why = ('stale: not in the source; tcp/' + (@($h.LocalPort) -join ',')) }
        }
    }
    return $plan
}

# ------------------------------------------------------------------ the firewall (write)
function Invoke-New($w) {
    if (-not (Test-OwnName $w.Name)) { Stop-Abort "internal: refusing to create '$($w.Name)' outside the $Prefix prefix" }
    New-NetFirewallRule -Name $w.Name -DisplayName $w.DisplayName -Group $RuleGroup -Description $w.Description `
        -Direction Inbound -Action Block -Protocol TCP -LocalPort ([string]$w.Port) -LocalAddress Any `
        -RemoteAddress $w.RemoteAddress -Profile Any -Enabled True -ErrorAction Stop | Out-Null
}
function Invoke-Remove([string]$Name) {
    if (-not (Test-OwnName $Name)) { Stop-Abort "internal: refusing to remove '$Name', which is outside the $Prefix prefix" }
    Remove-NetFirewallRule -Name $Name -ErrorAction Stop | Out-Null
}

function Invoke-Plan($plan) {
    $done = @()
    $steps = @()
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'CREATE' })) { $steps += , @('new', $p) }
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'REPLACE' })) { $steps += , @('remove', $p); $steps += , @('new', $p) }
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'REMOVE' })) { $steps += , @('remove', $p) }
    foreach ($s in $steps) {
        try {
            if ($s[0] -eq 'new') { Invoke-New $s[1].Want } else { Invoke-Remove $s[1].Name }
            $done += ($s[0] + ' ' + $s[1].Name)
        }
        catch {
            Write-Host ("FAILED: " + $s[0] + ' ' + $s[1].Name + ': ' + $_.Exception.Message) -ForegroundColor Red
            Write-Host ("Already done: " + $(if ($done.Count) { $done -join '; ' } else { 'nothing' })) -ForegroundColor Red
            Write-Host 'Run -Status to see the state, then -Apply again.' -ForegroundColor Red
            exit 1
        }
    }
    return $done
}

# ------------------------------------------------------------------ output
function Show-Source($src, $cfg, $want) {
    Say ("  source  : " + $src.Path + " [lan_ingress]")
    Say ("  addresses: " + $cfg.AddrFrom)
    Say ("  host    : " + $cfg.HostIp + "   allowed remote: " + ($cfg.Allowed -join ', '))
    Say ("  blocked remote (IPv4): " + ($want.Ranges.V4 -join ', '))
    Say ("  blocked remote (IPv6): " + ($want.Ranges.V6 -join ', '))
    Say  "  each rule: inbound, BLOCK, TCP, local address Any, all profiles, one rule per port"
}
function Show-Plan($plan) {
    Say 'plan:'
    if (@($plan).Count -eq 0) { Say '  (nothing to do)' }
    foreach ($p in $plan) {
        $what = if ($p.Want) { "$($p.Want.Plane)/$($p.Want.Service)  tcp/$($p.Want.Port)" } else { '' }
        Say ("  {0,-8} {1}  {2}  {3}" -f $p.Op, $p.Name, $what, $p.Why).TrimEnd()
    }
}
function Get-Mode {
    $n = [int][bool]$Status + [int][bool]$Apply + [int][bool]$Remove
    if ($n -gt 1) { Stop-Abort 'give exactly one of -Status, -Apply, -Remove' }
    if ($Apply) { return 'Apply' }
    if ($Remove) { return 'Remove' }
    if ($Status) { return 'Status' }
    if ($Dry) { return 'Apply' }
    return 'Status'
}
function Assert-Elevated([string]$Mode) {
    if (Test-Elevated) { return }
    Write-Host ("REFUSED: -" + $Mode + " changes the Windows Firewall and must run from an elevated (Run as Administrator) PowerShell.") -ForegroundColor Red
    Write-Host ("Nothing was changed. To see what it would do, run it with -WhatIf (no elevation needed).") -ForegroundColor Red
    exit 2
}

# ------------------------------------------------------------------ main
$mode = Get-Mode

if ($mode -eq 'Remove') {
    Say ('LAN ingress guard -Remove' + $(if ($Dry) { ' (DRY RUN)' } else { '' }))
    $current = @(Get-GuardRules)
    $plan = @($current | ForEach-Object { [pscustomobject]@{ Op = 'REMOVE'; Name = $_.Name; Want = $null; Why = ('tcp/' + (@($_.LocalPort) -join ',')) } })
    Show-Plan $plan
    if ($Dry) { Say 'DRY RUN: no change made.'; exit 0 }
    Assert-Elevated 'Remove'
    if ($plan.Count -eq 0) { Say 'Nothing to remove; no change made.'; exit 0 }
    $null = Invoke-Plan $plan
    $left = @(Get-GuardRules)
    if ($left.Count -ne 0) { Write-Host ("ERROR: rules still present after -Remove: " + (($left | ForEach-Object { $_.Name }) -join ', ')) -ForegroundColor Red; exit 1 }
    Say ("Removed {0} rule(s). The listed ports are now governed by the other rules on this host (Docker Desktop's allow-any)." -f $plan.Count)
    exit 0
}

$src = Read-Source $ManifestPath $HostFile
$cfg = Test-Source $src
$want = Get-DesiredRules $cfg
$current = @(Get-GuardRules)
$plan = @(Get-Plan $want.Rules $current)

if ($mode -eq 'Status') {
    Say 'LAN ingress guard -Status (read-only)'
    Show-Source $src $cfg $want
    Say 'rules:'
    $bad = 0
    $byPort = @{}
    foreach ($p in $plan) {
        switch ($p.Op) {
            'KEEP' { $tag = 'OK' }
            'CREATE' { $tag = 'MISSING'; $bad++ }
            'REPLACE' { $tag = 'DRIFT'; $bad++ }
            'REMOVE' { $tag = 'STALE'; $bad++ }
        }
        if ($p.Want) { $byPort[[string]$p.Want.Port] = $tag }
        $what = if ($p.Want) { "tcp/$($p.Want.Port)" } else { '' }
        Say ("  {0,-8} {1}  {2}  {3}" -f $tag, $p.Name, $what, $p.Why).TrimEnd()
    }
    Say "listeners (Get-NetTCPConnection -State Listen):"
    $listen = @()
    try { $listen = @(Get-NetTCPConnection -State Listen -ErrorAction Stop) }
    catch { Say ('  could not read listeners: ' + $_.Exception.Message) }
    foreach ($w in $want.Rules) {
        $hits = @($listen | Where-Object { [int]$_.LocalPort -eq $w.Port } | ForEach-Object { [string]$_.LocalAddress } | Sort-Object -Unique)
        $descr = @()
        $reachable = $false
        foreach ($a in $hits) {
            if ($a -eq $cfg.HostIp) { $descr += "$a (LAN)"; $reachable = $true }
            elseif ($a -eq '0.0.0.0' -or $a -eq '::') { $descr += "$a (all addresses)"; $reachable = $true }
            elseif ($a -like '127.*' -or $a -eq '::1') { $descr += "$a (loopback only)" }
            else { $descr += $a; $reachable = $true }
        }
        $line = "  tcp/{0,-5} {1,-24} " -f $w.Port, $w.Service
        if ($hits.Count -eq 0) { $line += 'not listening' }
        else { $line += 'listening: ' + ($descr -join ', ') }
        if ($reachable) {
            if ($byPort[[string]$w.Port] -eq 'OK') { $line += '  guarded' }
            else { $line += ('  EXPOSED: guard rule ' + $byPort[[string]$w.Port]); $bad++ }
        }
        Say $line
    }
    if ($bad -gt 0) { Say ("{0} item(s) need attention; -Apply fixes the rules." -f $bad); exit 3 }
    Say 'In sync with the source of truth.'
    exit 0
}

# Apply
Say ('LAN ingress guard -Apply' + $(if ($Dry) { ' (DRY RUN)' } else { '' }))
Show-Source $src $cfg $want
Show-Plan $plan
$todo = @($plan | Where-Object { $_.Op -ne 'KEEP' })
if ($Dry) { Say 'DRY RUN: no change made.'; exit 0 }
# Elevation is required even for a no-op, so 'refused without admin' never depends on state.
Assert-Elevated 'Apply'
if ($todo.Count -eq 0) { Say 'Already in sync; no change made.'; exit 0 }
$done = Invoke-Plan $plan
$after = @(Get-Plan $want.Rules @(Get-GuardRules) | Where-Object { $_.Op -ne 'KEEP' })
if ($after.Count -ne 0) {
    Write-Host ("ERROR: after -Apply the rules still differ from the source: " + (($after | ForEach-Object { $_.Op + ' ' + $_.Name }) -join ', ')) -ForegroundColor Red
    exit 1
}
Say ("Applied: " + ($done -join '; '))
Say 'Verified: every rule matches the source of truth.'
exit 0
