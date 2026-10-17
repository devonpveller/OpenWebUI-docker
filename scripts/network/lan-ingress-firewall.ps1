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
# -Status, -Apply and -Remove only ever touch rules whose Name OR DisplayName starts with
# that prefix: the DisplayName is how a rule made by hand (which Windows gives a GUID Name)
# is recognised. Docker's rules and anything else on the machine are out of reach.
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
    # Every property Windows reports on the rule object and on each of its seven filter
    # objects goes into .Props as (Kind, Name, Text), unfiltered. Get-Drift judges them all.
    # The flat fields beside .Props are for display and for older readers only. No drift
    # decision is made from them.
    function Get-PropRecords([string]$Kind, $o) {
        $recs = @()
        if ($null -eq $o) { return , $recs }
        foreach ($p in $o.PSObject.Properties) {
            $text = $null
            try {
                $v = $p.Value
                $text = if ($null -eq $v) { '' } else { (@($v) | ForEach-Object { [string]$_ }) -join ',' }
            }
            catch { $text = '<unreadable: getter threw>' }
            $recs += [pscustomobject]@{ Kind = $Kind; Name = [string]$p.Name; Text = $text }
        }
        # The RAW CIM values too, as 'raw:<Name>'. PowerShell COMPUTES many of the properties
        # above, several through an enum cast, and a raw value the enum does not name reads
        # back empty (tester finding TA3-1: raw DynamicTransport 32/64/128 printed
        # DynamicTarget ''). Get-Drift judges the raw value wherever such a cast sits in between.
        $cp = $o.PSObject.Properties['CimInstanceProperties']
        if ($null -ne $cp -and $null -ne $cp.Value -and -not ($cp.Value -is [string])) {
            foreach ($c in @($cp.Value)) {
                if ($null -eq $c -or $null -eq $c.PSObject.Properties['Name']) { continue }
                $text = $null
                try {
                    $v = $c.Value
                    $text = if ($null -eq $v) { '' } else { (@($v) | ForEach-Object { [string]$_ }) -join ',' }
                }
                catch { $text = '<unreadable: getter threw>' }
                $recs += [pscustomobject]@{ Kind = $Kind; Name = ('raw:' + [string]$c.Name); Text = $text }
            }
        }
        return , $recs
    }
    function Get-FieldText([object[]]$Recs, [string]$Kind, [string]$Name) {
        foreach ($x in $Recs) { if ($x.Kind -ceq $Kind -and $x.Name -ceq $Name) { return $x.Text } }
        return $null
    }
    # OWN RULES: Name OR DisplayName starts with the prefix. The script names its rules
    # ai-stack-lan-guard-<service>-<port>. A rule made by hand in the Windows Firewall UI
    # (or by a script that sets no -Name) gets a GUID Name and carries the
    # prefix only in its DisplayName; the operator's first live rule is one of these. Such
    # a rule is recognised by its DisplayName, judged like any other, and removed by its
    # real Name. A rule with the prefix in NEITHER field is never touched.
    # ONE unfiltered read of every rule, then the prefix test here. A server-side -Name or
    # -DisplayName wildcard would need two queries, and each could fail differently.
    $all = @()
    try { $all = @(Get-NetFirewallRule -ErrorAction Stop) }
    catch {
        if ($_.CategoryInfo.Category -ne 'ObjectNotFound') { Stop-Abort ("could not read the firewall rules: " + $_.Exception.Message) }
    }
    $out = @()
    $done = @{}
    foreach ($r in $all) {
        $rid = [string]$r.Name
        if ($done.ContainsKey($rid)) { continue }
        $done[$rid] = $true
        $dn = ''
        $dp = $r.PSObject.Properties['DisplayName']
        if ($null -ne $dp -and $null -ne $dp.Value) { $dn = [string]$dp.Value }
        if (Test-OwnName $rid) { $key = $rid }
        elseif (Test-OwnName $dn) { $key = $dn }
        else { continue }
        $recs = @()
        try {
            $recs += Get-PropRecords 'rule' $r
            $recs += Get-PropRecords 'port' (Get-NetFirewallPortFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'address' (Get-NetFirewallAddressFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'application' (Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'service' (Get-NetFirewallServiceFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'interfacetype' (Get-NetFirewallInterfaceTypeFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'interface' (Get-NetFirewallInterfaceFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
            $recs += Get-PropRecords 'security' (Get-NetFirewallSecurityFilter -AssociatedNetFirewallRule $r -ErrorAction Stop)
        }
        catch { Stop-Abort ("could not read the filters of rule $($r.Name): " + $_.Exception.Message) }
        $lp = Get-FieldText $recs 'port' 'LocalPort'
        $ra = Get-FieldText $recs 'address' 'RemoteAddress'
        $out += [pscustomobject]@{
            Key                = $key
            Name               = $rid
            DisplayName        = $dn
            Direction          = Get-FieldText $recs 'rule' 'Direction'
            Action             = Get-FieldText $recs 'rule' 'Action'
            Enabled            = Get-FieldText $recs 'rule' 'Enabled'
            Profile            = Get-FieldText $recs 'rule' 'Profile'
            Owner              = Get-FieldText $recs 'rule' 'Owner'
            Platform           = Get-FieldText $recs 'rule' 'Platform'
            PolicyAppId        = Get-FieldText $recs 'rule' 'PolicyAppId'
            PackageFamilyName  = Get-FieldText $recs 'rule' 'PackageFamilyName'
            Protocol           = Get-FieldText $recs 'port' 'Protocol'
            LocalPort          = @($(if ($null -ne $lp -and $lp -ne '') { $lp.Split(',') }))
            RemotePort         = Get-FieldText $recs 'port' 'RemotePort'
            DynamicTarget      = Get-FieldText $recs 'port' 'DynamicTarget'
            LocalAddress       = Get-FieldText $recs 'address' 'LocalAddress'
            RemoteAddress      = @($(if ($null -ne $ra -and $ra -ne '') { $ra.Split(',') }))
            Program            = Get-FieldText $recs 'application' 'Program'
            Package            = Get-FieldText $recs 'application' 'Package'
            Service            = Get-FieldText $recs 'service' 'Service'
            InterfaceType      = Get-FieldText $recs 'interfacetype' 'InterfaceType'
            InterfaceAlias     = Get-FieldText $recs 'interface' 'InterfaceAlias'
            LocalUser          = Get-FieldText $recs 'security' 'LocalUser'
            RemoteUser         = Get-FieldText $recs 'security' 'RemoteUser'
            RemoteMachine      = Get-FieldText $recs 'security' 'RemoteMachine'
            Authentication     = Get-FieldText $recs 'security' 'Authentication'
            Encryption         = Get-FieldText $recs 'security' 'Encryption'
            OverrideBlockRules = Get-FieldText $recs 'security' 'OverrideBlockRules'
            Props              = $recs
        }
    }
    return $out
}

# THE DRIFT CHECK IS STRUCTURAL (tester findings TF1, TA2-1). It does not keep a list of
# "fields worth checking". Every property Windows reports, on the rule and on each of its
# seven filters, must be one of three things:
#   1. in $expect, at one of its unrestricted values;
#   2. in $metadata: identity, label, status or CIM housekeeping, which cannot narrow where
#      a rule applies (each entry gives its reason);
#   3. in $derived: another form of a property that IS compared in $expect.
# A property in none of the three is DRIFT. So is a property on the wrong object, or one
# at any other value. A property Windows adds in a future build is therefore drift until
# someone decides what it means, never silently "in sync". The $required properties must
# also be PRESENT: a missing one is drift, not "as expected".
#
# Values were measured read-only on this host 2026-10-16 (Windows 10.0.26200): every
# property of Docker's rule and its filters, and the distinct values of every property
# across all 933 rules (rule level) and a 150-rule sample (filters). The constants below
# took exactly one value on every rule read.
function Get-Drift($have, $want) {
    $metadata = @{
        'Name'                    = 'the rule id; matching is by name already'
        'ID'                      = 'alias of Name'
        'DisplayName'             = 'a label'
        'Description'             = 'a label'
        'Caption'                 = 'a CIM label'
        'ElementName'             = 'a CIM label (mirrors DisplayName)'
        'InstanceID'              = 'the CIM key (the rule id)'
        'CommonName'              = 'a CIM label'
        'Group'                   = 'a grouping label for the UI; does not change matching'
        'DisplayGroup'            = 'a grouping label for the UI'
        'RuleGroup'               = 'a grouping label for the UI'
        'PolicyKeywords'          = 'descriptive CIM metadata'
        'PolicyRoles'             = 'descriptive CIM metadata'
        'CreationClassName'       = 'the CIM key (class + rule id)'
        'SystemCreationClassName' = 'a CIM key part, empty for firewall rules'
        'SystemName'              = 'a CIM key part, empty for firewall rules'
        'PolicyRuleName'          = 'a CIM key part, empty for firewall rules'
        'Status'                  = 'the store parse message (rule.PrimaryStatus is compared)'
        'StatusCode'              = 'the numeric form of Status (rule.PrimaryStatus is compared)'
        'EnforcementStatus'       = 'reported by the store; NotApplicable on every rule read'
        'PolicyStoreSource'       = 'which store holds the rule; does not change matching'
        'PolicyStoreSourceType'   = 'which store type holds the rule; does not change matching'
        'PrimaryStatus'           = 'filter objects: always empty (rule.PrimaryStatus is compared)'
        'StatusDescriptions'      = 'CIM health metadata'
        'OperationalStatus'       = 'CIM health metadata'
        'OperatingStatus'         = 'CIM health metadata'
        'DetailedStatus'          = 'CIM health metadata'
        'HealthState'             = 'CIM health metadata'
        'CommunicationStatus'     = 'CIM health metadata'
        'InstallDate'             = 'CIM metadata'
        'PSComputerName'          = 'PowerShell remoting housekeeping'
        'CimClass'                = 'PowerShell CIM housekeeping (the class)'
        'CimInstanceProperties'   = 'PowerShell CIM housekeeping (the raw bag; every member is also a property judged here)'
        'CimSystemProperties'     = 'PowerShell CIM housekeeping'
    }
    $derived = @{
        'rule.Platforms'             = 'raw form of rule.Platform'
        'rule.LSM'                   = 'alias of rule.LooseSourceMapping'
        'address.LocalIP'            = 'alias of address.LocalAddress'
        'address.RemoteIP'           = 'alias of address.RemoteAddress'
        'application.AppPath'        = 'raw form of application.Program'
        'service.ServiceName'        = 'raw form of service.Service'
        'security.LocalUsers'        = 'raw form of security.LocalUser'
        'security.RemoteUsers'       = 'raw form of security.RemoteUser'
        'security.RemoteMachines'    = 'raw form of security.RemoteMachine'
    }
    $expect = @{
        'rule.Direction'                     = @('Inbound')
        'rule.Action'                        = @('Block')
        'rule.Enabled'                       = @('True')
        'rule.Profile'                       = @('Any')
        'rule.Platform'                      = @('')
        'rule.Owner'                         = @('')
        'rule.PackageFamilyName'             = @('')
        'rule.PolicyAppId'                   = @('')
        'rule.RemoteDynamicKeywordAddresses' = @('')
        'rule.EdgeTraversalPolicy'           = @('Block')
        'rule.LooseSourceMapping'            = @('False')
        'rule.LocalOnlyMapping'              = @('False')
        'rule.PrimaryStatus'                 = @('OK')
        'rule.Mandatory'                     = @('')
        'rule.Priority'                      = @('')
        'rule.RuleUsage'                     = @('')
        'rule.PolicyDecisionStrategy'        = @('2')
        'rule.ConditionListType'             = @('3')
        'rule.ExecutionStrategy'             = @('2')
        'rule.SequencedActions'              = @('3')
        'port.Protocol'                      = @('TCP')
        'port.LocalPort'                     = '<the listed port>'
        'port.RemotePort'                    = @('Any')
        'port.IcmpType'                      = @('Any')
        'port.DynamicTarget'                 = @('Any')
        'port.DynamicTransport'              = @('Any')
        'rule.Profiles'                      = @('0')
        'port.IsNegated'                     = @('')
        'address.LocalAddress'               = @('Any')
        'address.RemoteAddress'              = '<the complement of the nodes>'
        'address.IsNegated'                  = @('')
        'application.Program'                = @('Any')
        'application.Package'                = @('')
        'application.IsNegated'              = @('')
        'service.Service'                    = @('Any')
        'service.IsNegated'                  = @('')
        'interfacetype.InterfaceType'        = @('Any')
        'interfacetype.IsNegated'            = @('')
        'interface.InterfaceAlias'           = @('Any')
        'interface.IsNegated'                = @('')
        'security.LocalUser'                 = @('Any')
        'security.RemoteUser'                = @('Any')
        'security.RemoteMachine'             = @('Any')
        'security.Authentication'            = @('NotRequired')
        'security.Encryption'                = @('NotRequired')
        'security.OverrideBlockRules'        = @('False')
        'security.IsNegated'                 = @('')
    }
    # RAW values behind every property PowerShell computes through an ENUM cast
    # (NetSecurity.types.ps1xml: Enabled, Profile <- Profiles, Direction, Action,
    # EdgeTraversalPolicy, PrimaryStatus, DynamicTarget <- DynamicTransport, InterfaceType,
    # Authentication, Encryption). A raw value outside the enum makes the computed property
    # read empty, so for these the RAW value is the one compared. The values are those of a
    # real guard block rule, read live 2026-10-16. PolicyStoreSourceType is the one other
    # enum cast, and it is metadata. The Formatter-mapped properties (Program, Service,
    # LocalUser, ...) print the raw value verbatim, or 'Any' when it is empty, so they are
    # judged at the computed level.
    $rawExpect = @{
        'rule.raw:Enabled'             = @('1')
        'rule.raw:Profiles'            = @('0')
        'rule.raw:Direction'           = @('1')
        'rule.raw:Action'              = @('4')
        'rule.raw:EdgeTraversalPolicy' = @('0')
        'rule.raw:PrimaryStatus'       = @('1')
        'port.raw:DynamicTransport'    = @('0')
        'interfacetype.raw:InterfaceType' = @('0')
        'security.raw:Authentication'  = @('0')
        'security.raw:Encryption'      = @('0')
    }
    $required = @(
        'rule.Direction', 'rule.Action', 'rule.Enabled', 'rule.Profile', 'rule.Platform', 'rule.Owner',
        'rule.PackageFamilyName', 'rule.PolicyAppId', 'rule.RemoteDynamicKeywordAddresses',
        'port.Protocol', 'port.LocalPort', 'port.RemotePort', 'port.DynamicTarget',
        'address.LocalAddress', 'address.RemoteAddress', 'application.Program', 'application.Package',
        'service.Service', 'interfacetype.InterfaceType', 'interface.InterfaceAlias',
        'security.LocalUser', 'security.RemoteUser', 'security.RemoteMachine',
        'security.Authentication', 'security.Encryption', 'security.OverrideBlockRules')
    $why = @()
    $seen = @{}
    $props = @()
    $pp = $have.PSObject.Properties['Props']
    if ($null -ne $pp -and $null -ne $pp.Value) { $props = @($pp.Value) }
    $psNames = @{}
    $rawKinds = @{}
    foreach ($x in $props) {
        if ($x.Name.StartsWith('raw:')) { $rawKinds[$x.Kind] = $true } else { $psNames[$x.Kind + '.' + $x.Name] = $true }
    }
    foreach ($x in $props) {
        $key = $x.Kind + '.' + $x.Name
        if ($x.Name.StartsWith('raw:')) {
            $rn = $x.Name.Substring(4)
            $shownRaw = if ($x.Text -eq '') { '<empty>' } else { $x.Text }
            if ($seen.ContainsKey($key)) { $why += ("raw {0} reported twice ({1})" -f $rn.ToLowerInvariant(), $key); continue }
            $seen[$key] = $true
            if ($rawExpect.ContainsKey($key)) {
                if ($rawExpect[$key] -notcontains $x.Text) { $why += ("{0} raw {1}" -f $rn.ToLowerInvariant(), $shownRaw) }
            }
            elseif (-not $psNames.ContainsKey($x.Kind + '.' + $rn)) {
                # every raw value must also be visible, and judged, at the computed level
                $why += ("unknown property {0} = {1} ({2}, raw only)" -f $rn.ToLowerInvariant(), $shownRaw, $key)
            }
            continue
        }
        $label = $x.Name.ToLowerInvariant()
        $shown = if ($x.Text -eq '') { '<empty>' } else { $x.Text }
        if ($seen.ContainsKey($key)) { $why += ("{0} reported twice ({1})" -f $label, $key); continue }
        $seen[$key] = $true
        if ($expect.ContainsKey($key)) {
            if ($key -eq 'port.LocalPort') {
                if ($x.Text -ne [string]$want.Port) { $why += "localport $shown" }
            }
            elseif ($key -eq 'address.RemoteAddress') {
                $k = if ($x.Text -eq '') { $null } else { Get-AddressKey @($x.Text.Split(',')) }
                if ($null -eq $k -or $k -ne $want.RemoteKey) { $why += "remoteaddress $shown" }
            }
            elseif ($expect[$key] -notcontains $x.Text) { $why += ("{0} {1}" -f $label, $shown) }
            continue
        }
        if ($derived.ContainsKey($key)) { continue }
        if ($metadata.ContainsKey($x.Name)) { continue }
        $why += ("unknown property {0} = {1} ({2})" -f $label, $shown, $key)
    }
    foreach ($k in $required) {
        if (-not $seen.ContainsKey($k)) { $why += ("{0} <unreadable> ({1} missing)" -f $k.Split('.')[1].ToLowerInvariant(), $k) }
    }
    # Raw values: required on every object that IS a CIM instance (it carries CimClass);
    # such an object without readable raw values is drift too.
    foreach ($kind in @('rule', 'port', 'address', 'application', 'service', 'interfacetype', 'interface', 'security')) {
        if (-not $seen.ContainsKey($kind + '.CimClass')) { continue }
        if (-not $rawKinds.ContainsKey($kind)) { $why += ("raw values unreadable ({0}.CimInstanceProperties)" -f $kind); continue }
        foreach ($k in @($rawExpect.Keys | Where-Object { $_.StartsWith($kind + '.raw:') })) {
            if (-not $seen.ContainsKey($k)) { $why += ("{0} raw <unreadable> ({1} missing)" -f $k.Split(':')[1].ToLowerInvariant(), $k) }
        }
    }
    return $why
}

# A plan entry's Name is the rule's KEY (its prefixed Name, or its prefixed DisplayName for
# a hand-made rule). Rule is the rule as read, so a removal uses its real Name.
function Get-IdNote($h) { if ($null -ne $h -and $h.Name -ne $h.Key) { return " (rule id $($h.Name))" } return '' }
function Get-Plan($desired, $current) {
    $plan = @()
    foreach ($w in $desired) {
        $h = @($current | Where-Object { $_.Key -eq $w.Name })
        if ($h.Count -eq 0) { $plan += [pscustomobject]@{ Op = 'CREATE'; Name = $w.Name; Want = $w; Rule = $null; Why = '' } }
        else {
            # Several rules for one port (say, a hand-made one beside a managed one): KEEP one
            # that is in sync if there is one; otherwise REPLACE the one that carries the
            # managed Name (so its create step cannot collide with it), else the first. Every
            # other rule for the port is removed. Any mix converges in one -Apply (TA3-F1).
            $drifts = @{}
            foreach ($x in $h) { $drifts[$x.Name] = @(Get-Drift $x $w) }
            $keep = @($h | Where-Object { $drifts[$_.Name].Count -eq 0 } | Select-Object -First 1)
            if ($keep.Count -eq 1) {
                $pick = $keep[0]
                $plan += [pscustomobject]@{ Op = 'KEEP'; Name = $w.Name; Want = $w; Rule = $pick; Why = (Get-IdNote $pick).Trim() }
            }
            else {
                $named = @($h | Where-Object { $_.Name -eq $w.Name } | Select-Object -First 1)
                $pick = if ($named.Count -eq 1) { $named[0] } else { $h[0] }
                $plan += [pscustomobject]@{ Op = 'REPLACE'; Name = $w.Name; Want = $w; Rule = $pick; Why = ('drift: ' + ($drifts[$pick.Name] -join '; ') + (Get-IdNote $pick)) }
            }
            foreach ($x in @($h | Where-Object { $_.Name -ne $pick.Name })) {
                $plan += [pscustomobject]@{ Op = 'REMOVE'; Name = $x.Key; Want = $null; Rule = $x; Why = ('duplicate of the rule kept for tcp/' + $w.Port + (Get-IdNote $x)) }
            }
        }
    }
    foreach ($h in $current) {
        if (-not ($desired | Where-Object { $_.Name -eq $h.Key })) {
            $plan += [pscustomobject]@{ Op = 'REMOVE'; Name = $h.Key; Want = $null; Rule = $h; Why = ('stale: not in the source; tcp/' + (@($h.LocalPort) -join ',') + (Get-IdNote $h)) }
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
function Invoke-Remove($Rule) {
    if ($null -eq $Rule -or -not ((Test-OwnName $Rule.Name) -or (Test-OwnName $Rule.DisplayName))) {
        Stop-Abort "internal: refusing to remove a rule whose Name and DisplayName are both outside the $Prefix prefix"
    }
    Remove-NetFirewallRule -Name $Rule.Name -ErrorAction Stop | Out-Null
}

function Invoke-Plan($plan) {
    $done = @()
    $steps = @()
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'CREATE' })) { $steps += , @('new', $p) }
    # A replaced rule under the SAME name must go first (names are unique). A hand-made rule
    # (GUID name) is replaced create-first, so the port is never unguarded.
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'REPLACE' })) {
        if ($p.Rule.Name -eq $p.Want.Name) { $steps += , @('remove', $p); $steps += , @('new', $p) }
        else { $steps += , @('new', $p); $steps += , @('remove', $p) }
    }
    foreach ($p in ($plan | Where-Object { $_.Op -eq 'REMOVE' })) { $steps += , @('remove', $p) }
    foreach ($s in $steps) {
        try {
            if ($s[0] -eq 'new') { Invoke-New $s[1].Want } else { Invoke-Remove $s[1].Rule }
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
    $plan = @($current | ForEach-Object { [pscustomobject]@{ Op = 'REMOVE'; Name = $_.Key; Want = $null; Rule = $_; Why = ('tcp/' + (@($_.LocalPort) -join ',') + (Get-IdNote $_)) } })
    Show-Plan $plan
    if ($Dry) { Say 'DRY RUN: no change made.'; exit 0 }
    Assert-Elevated 'Remove'
    if ($plan.Count -eq 0) { Say 'Nothing to remove; no change made.'; exit 0 }
    $null = Invoke-Plan $plan
    $left = @(Get-GuardRules)
    if ($left.Count -ne 0) { Write-Host ("ERROR: rules still present after -Remove: " + (($left | ForEach-Object { $_.Key }) -join ', ')) -ForegroundColor Red; exit 1 }
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
