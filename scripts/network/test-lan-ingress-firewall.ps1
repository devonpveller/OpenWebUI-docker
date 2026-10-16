# test-lan-ingress-firewall.ps1 - tests scripts/network/lan-ingress-firewall.ps1 with the
# Windows Firewall FAKED (harness item edge-b1-fw, 2026-10-16).
#
# HOW NOTHING REAL IS TOUCHED
#   - Every NetSecurity cmdlet the guard calls (Get-NetFirewallRule, the three
#     Get-NetFirewall*Filter cmdlets, New-NetFirewallRule, Remove-NetFirewallRule) and
#     Get-NetTCPConnection is SHADOWED by a function defined here. A script invoked with &
#     resolves commands through its caller's scope, and a function wins over a cmdlet, so the
#     guard talks to an in-memory rule store ($global:LGT.Rules) instead of the firewall.
#   - Cmdlets the guard must NEVER call (Set-/Enable-/Disable-NetFirewallRule, netsh) are
#     shadowed too; calling one is recorded as a violation and fails the case.
#   - Elevation is faked through the guard's documented seam: a function named
#     Test-LanIngressGuardAdmin in the caller's scope. It can only make the guard ATTEMPT a
#     change - the OS still enforces elevation on the real cmdlets.
#   - The test REFUSES to run elevated, so even a missing shadow could not change the live
#     firewall (New-/Remove-NetFirewallRule fail with access denied when not elevated).
#   - The fake Get-NetFirewallRule IGNORES its -Name filter and returns every rule in the store
#     (Docker's included), so the guard's own prefix filtering is what is under test.
#
# Usage:  powershell -NoProfile -File scripts\network\test-lan-ingress-firewall.ps1 [-Script <path>]
# Exit code = number of failed cases.

[CmdletBinding()]
param([string]$Script = '')

$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Script) { $Script = Join-Path $here 'lan-ingress-firewall.ps1' }
$repoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
$realManifest = Join-Path $repoRoot 'stack.manifest.toml'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'REFUSED: this test must not run elevated (it would put the live firewall one missing shadow away).' -ForegroundColor Red
    exit 99
}

$PREFIX = 'ai-stack-lan-guard-'
$failures = 0
$results = @()
$tmp = Join-Path ([IO.Path]::GetTempPath()) ('lgt-' + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null

# ---------------------------------------------------------------- the fake firewall
$global:LGT = @{ Rules = [ordered]@{}; Calls = @(); Violations = @(); Admin = $false; Listen = @() }

# THE FAKE REPORTS THE REAL PROPERTY SET. Every object it returns carries every property the
# real cmdlet returned for Docker's rule on this host. The full read-only dump is in
# journal/evidence/edge-b1-fw/dev-attempt3/live-props-dump.txt.
#
# A stored rule is a hashtable:
#   - flat keys hold the meaningful fields (Action, RemotePort, Owner, ...); Get-FakeKindOf
#     maps each one to its object. Removing a flat key removes that property from the object,
#     the way an unreadable field would look.
#   - a key '<kind>:<Prop>' sets (or ADDS, if the real cmdlet has no such property) <Prop>
#     on that object. A flat key that maps to no object is added to the rule object. That is
#     how a test models a property Windows might add in a future build.
# Default values are those of a freshly created, unrestricted guard rule.
function Get-FakeDefaults {
    return @{ RemotePort = 'Any'; DynamicTarget = 'Any'; IcmpType = 'Any'; Program = 'Any'; Package = $null; Service = 'Any'; InterfaceType = 'Any';
        InterfaceAlias = 'Any'; LocalUser = 'Any'; RemoteUser = 'Any'; RemoteMachine = 'Any'; Authentication = 'NotRequired';
        Encryption = 'NotRequired'; OverrideBlockRules = $false; Owner = ''; Platform = @(); PolicyAppId = $null; PackageFamilyName = $null;
        RemoteDynamicKeywordAddresses = @(); EdgeTraversalPolicy = 'Block'; LooseSourceMapping = $false; LocalOnlyMapping = $false }
}
function Get-FakeKindOf([string]$Key) {
    $map = @{
        rule        = @('Name', 'DisplayName', 'Group', 'Direction', 'Action', 'Enabled', 'Profile', 'Owner', 'Platform', 'PolicyAppId', 'PackageFamilyName',
            'RemoteDynamicKeywordAddresses', 'EdgeTraversalPolicy', 'LooseSourceMapping', 'LocalOnlyMapping')
        port        = @('Protocol', 'LocalPort', 'RemotePort', 'IcmpType', 'DynamicTarget')
        address     = @('LocalAddress', 'RemoteAddress')
        application = @('Program', 'Package')
        service     = @('Service')
        interfacetype = @('InterfaceType')
        interface   = @('InterfaceAlias')
        security    = @('LocalUser', 'RemoteUser', 'RemoteMachine', 'Authentication', 'Encryption', 'OverrideBlockRules')
    }
    foreach ($k in $map.Keys) { if ($map[$k] -ccontains $Key) { return $k } }
    return $null
}
function New-FakeObject($r, [string]$Kind) {
    $o = [ordered]@{}
    $name = [string]$r['Name']
    # semantic: from the stored rule's flat key, or absent when the key was removed
    $sem = { param($p) if ($r.ContainsKey($p)) { $o[$p] = $r[$p] } }
    # fixed: real housekeeping value unless the test overrides it with '<kind>:<Prop>'
    $fix = { param($p, $v) if ($r.ContainsKey($Kind + ':' + $p)) { $o[$p] = $r[$Kind + ':' + $p] } else { $o[$p] = $v } }
    $common = {
        & $fix 'Caption' $null; & $fix 'Description' $null; & $fix 'ElementName' ''; & $fix 'InstanceID' $name
        & $fix 'CommunicationStatus' $null; & $fix 'DetailedStatus' $null; & $fix 'HealthState' $null; & $fix 'InstallDate' $null
        & $fix 'Name' ''; & $fix 'OperatingStatus' $null; & $fix 'OperationalStatus' $null; & $fix 'PrimaryStatus' $null; & $fix 'Status' $null
        & $fix 'StatusDescriptions' $null; & $fix 'CreationClassName' ('MSFT|FW|FirewallRule|' + $name); & $fix 'IsNegated' $null
        & $fix 'SystemCreationClassName' ''; & $fix 'SystemName' ''
    }
    $cim = { param($cls) & $fix 'PSComputerName' $null; & $fix 'CimClass' ('root/standardcimv2:' + $cls); & $fix 'CimInstanceProperties' 'fake'; & $fix 'CimSystemProperties' 'fake' }
    switch ($Kind) {
        'rule' {
            & $sem 'Name'; & $fix 'ID' $name; & $sem 'DisplayName'; & $sem 'Group'; & $sem 'Enabled'; & $sem 'Profile'; & $sem 'Platform'
            & $sem 'Direction'; & $sem 'Action'; & $sem 'EdgeTraversalPolicy'; & $fix 'LSM' $r['LooseSourceMapping']
            & $fix 'PrimaryStatus' 'OK'; & $fix 'Status' 'The rule was parsed successfully from the store. (65536)'; & $fix 'EnforcementStatus' 'NotApplicable'
            & $fix 'PolicyStoreSourceType' 'Local'; & $fix 'Caption' $null; & $fix 'Description' $r['DisplayName']; & $fix 'ElementName' $r['DisplayName']
            & $fix 'InstanceID' $name; & $fix 'CommonName' $null; & $fix 'PolicyKeywords' $null; & $fix 'PolicyDecisionStrategy' ([uint16]2)
            & $fix 'PolicyRoles' $null; & $fix 'ConditionListType' ([uint16]3); & $fix 'CreationClassName' ('MSFT|FW|FirewallRule|' + $name)
            & $fix 'ExecutionStrategy' ([uint16]2); & $fix 'Mandatory' $null; & $fix 'PolicyRuleName' ''; & $fix 'Priority' $null; & $fix 'RuleUsage' $null
            & $fix 'SequencedActions' ([uint16]3); & $fix 'SystemCreationClassName' ''; & $fix 'SystemName' ''; & $fix 'DisplayGroup' $null
            & $sem 'LocalOnlyMapping'; & $sem 'LooseSourceMapping'; & $sem 'Owner'; & $sem 'PackageFamilyName'; & $fix 'Platforms' $r['Platform']
            & $sem 'PolicyAppId'; & $fix 'PolicyStoreSource' 'PersistentStore'; & $fix 'Profiles' ([uint16]0); & $sem 'RemoteDynamicKeywordAddresses'
            & $fix 'RuleGroup' $null; & $fix 'StatusCode' ([uint32]65536)
            & $cim 'MSFT_NetFirewallRule'
        }
        'port' {
            & $sem 'Protocol'; & $sem 'LocalPort'; & $sem 'RemotePort'; & $sem 'IcmpType'; & $sem 'DynamicTarget'; & $fix 'DynamicTransport' $r['DynamicTarget']
            & $common; & $cim 'MSFT_NetProtocolPortFilter'
        }
        'address' {
            & $sem 'LocalAddress'; & $fix 'LocalIP' $r['LocalAddress']; & $sem 'RemoteAddress'; & $fix 'RemoteIP' $r['RemoteAddress']
            & $common; & $cim 'MSFT_NetAddressFilter'
        }
        'application' {
            & $sem 'Program'; & $common; & $fix 'AppPath' $(if ($r['Program'] -eq 'Any') { $null } else { $r['Program'] }); & $sem 'Package'
            & $cim 'MSFT_NetApplicationFilter'
        }
        'service' { & $sem 'Service'; & $common; & $fix 'ServiceName' $(if ($r['Service'] -eq 'Any') { $null } else { $r['Service'] }); & $cim 'MSFT_NetServiceFilter' }
        'interfacetype' { & $sem 'InterfaceType'; & $common; & $cim 'MSFT_NetInterfaceTypeFilter' }
        'interface' { & $sem 'InterfaceAlias'; & $common; & $cim 'MSFT_NetInterfaceFilter' }
        'security' {
            & $sem 'Authentication'; & $sem 'Encryption'; & $sem 'LocalUser'; & $sem 'RemoteUser'; & $sem 'RemoteMachine'; & $common
            & $fix 'LocalUsers' $null; & $sem 'OverrideBlockRules'; & $fix 'RemoteMachines' $null; & $fix 'RemoteUsers' $null
            & $cim 'MSFT_NetNetworkLayerSecurityFilter'
        }
    }
    # additions: '<kind>:<Prop>' keys the real cmdlet has no property for; for the rule
    # object, also flat keys that belong to no object.
    foreach ($k in @($r.Keys)) {
        if ($k -like ($Kind + ':*')) { $p = $k.Substring($Kind.Length + 1); if (-not $o.Contains($p)) { $o[$p] = $r[$k] } }
        elseif ($Kind -eq 'rule' -and $k -notlike '*:*' -and $null -eq (Get-FakeKindOf $k) -and -not $o.Contains($k)) { $o[$k] = $r[$k] }
    }
    return [pscustomobject]$o
}
function New-FakeRule([string]$Name, [string]$Port, [string[]]$Remote, [hashtable]$Over) {
    $r = Get-FakeDefaults
    foreach ($kv in @{ Name = $Name; DisplayName = $Name; Group = ''; Direction = 'Inbound'; Action = 'Block'; Enabled = 'True';
        Profile = 'Any'; Protocol = 'TCP'; LocalPort = @($Port); LocalAddress = @('Any'); RemoteAddress = @($Remote) }.GetEnumerator()) { $r[$kv.Key] = $kv.Value }
    if ($Over) { foreach ($k in $Over.Keys) { $r[$k] = $Over[$k] } }
    return $r
}
function Reset-Fake {
    $global:LGT.Rules = [ordered]@{}; $global:LGT.Calls = @(); $global:LGT.Violations = @(); $global:LGT.Admin = $false; $global:LGT.Listen = @()
    # Docker Desktop's own allow rules and an unrelated rule: never to be touched.
    $global:LGT.Rules['TCP Query User{B6095153}C:\program files\docker\docker\resources\com.docker.backend.exe'] =
        (New-FakeRule 'TCP Query User{B6095153}C:\program files\docker\docker\resources\com.docker.backend.exe' 'Any' @('Any') @{ DisplayName = 'com.docker.backend'; Action = 'Allow'; Protocol = 'TCP'; Program = 'C:\program files\docker\docker\resources\com.docker.backend.exe'; Profile = 'Public'; EdgeTraversalPolicy = 'DeferToUser' })
    $global:LGT.Rules['ai-stack-lan-guardian'] = (New-FakeRule 'ai-stack-lan-guardian' '3000' @('Any') @{ Action = 'Allow' })
    $global:LGT.Rules['other-ai-stack-lan-guard-x'] = (New-FakeRule 'other-ai-stack-lan-guard-x' '3000' @('Any') @{ Action = 'Allow' })
}
# The WHOLE store, every field of every rule (Docker's included), so "no change" means no change.
function Get-Snapshot {
    ($global:LGT.Rules.Keys | Sort-Object | ForEach-Object {
        $r = $global:LGT.Rules[$_]
        $_ + '|' + (($r.Keys | Sort-Object | ForEach-Object { $_ + '=' + ((@($r[$_]) | ForEach-Object { [string]$_ }) -join ',') }) -join ';')
    }) -join "`n"
}
function Get-Mutations { @($global:LGT.Calls | Where-Object { $_.Op -ne 'Get' }) }

function Get-NetFirewallRule {
    [CmdletBinding()] param([string[]]$Name, [string[]]$DisplayName, [string[]]$Group)
    $global:LGT.Calls += @{ Op = 'Get' }
    foreach ($k in @($global:LGT.Rules.Keys)) { New-FakeObject $global:LGT.Rules[$k] 'rule' }
}
# A filter object carrying only the named fields the stored rule HAS (kept for the tester's
# scripts, which build reduced objects with it).
function New-FakeFilter($r, [string[]]$Fields) {
    $o = [ordered]@{}
    foreach ($f in $Fields) { if ($r.ContainsKey($f)) { $o[$f] = $r[$f] } }
    return [pscustomobject]$o
}
function Get-FakeRuleFor($AssociatedNetFirewallRule) {
    $n = $AssociatedNetFirewallRule.Name
    if (-not $global:LGT.Rules.Contains($n)) { throw "fake: no rule $n" }
    return $global:LGT.Rules[$n]
}
function Get-NetFirewallPortFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'port' }
}
function Get-NetFirewallAddressFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'address' }
}
function Get-NetFirewallApplicationFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'application' }
}
function Get-NetFirewallServiceFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'service' }
}
function Get-NetFirewallInterfaceTypeFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'interfacetype' }
}
function Get-NetFirewallInterfaceFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'interface' }
}
function Get-NetFirewallSecurityFilter {
    [CmdletBinding()] param([Parameter(ValueFromPipeline = $true)]$AssociatedNetFirewallRule)
    process { New-FakeObject (Get-FakeRuleFor $AssociatedNetFirewallRule) 'security' }
}
function New-NetFirewallRule {
    [CmdletBinding()] param([string]$Name, [string]$DisplayName, [string]$Group, [string]$Description, [string]$Direction,
        [string]$Action, [string]$Protocol, [string[]]$LocalPort, [string[]]$LocalAddress, [string[]]$RemoteAddress,
        [string]$Profile, [string]$Enabled)
    $global:LGT.Calls += @{ Op = 'New'; Name = $Name; Direction = $Direction; Action = $Action; Protocol = $Protocol; LocalPort = $LocalPort;
        LocalAddress = $LocalAddress; RemoteAddress = $RemoteAddress; Profile = $Profile; Enabled = $Enabled }
    if ($global:LGT.Rules.Contains($Name)) { throw "fake: a rule named $Name already exists" }
    $new = Get-FakeDefaults
    foreach ($kv in @{ Name = $Name; DisplayName = $DisplayName; Group = $Group; Direction = $Direction; Action = $Action; Enabled = $Enabled;
        Profile = $Profile; Protocol = $Protocol; LocalPort = @($LocalPort); LocalAddress = @($LocalAddress); RemoteAddress = @($RemoteAddress) }.GetEnumerator()) { $new[$kv.Key] = $kv.Value }
    $global:LGT.Rules[$Name] = $new
}
function Remove-NetFirewallRule {
    [CmdletBinding()] param([string[]]$Name, [Parameter(ValueFromPipeline = $true)]$InputObject)
    process {
        $names = if ($Name) { $Name } else { @($InputObject.Name) }
        foreach ($n in $names) {
            $global:LGT.Calls += @{ Op = 'Remove'; Name = $n }
            if (-not $global:LGT.Rules.Contains($n)) { throw "fake: no rule $n" }
            $global:LGT.Rules.Remove($n)
        }
    }
}
function Set-NetFirewallRule { $global:LGT.Violations += 'Set-NetFirewallRule'; $global:LGT.Calls += @{ Op = 'Set' } }
function Enable-NetFirewallRule { $global:LGT.Violations += 'Enable-NetFirewallRule'; $global:LGT.Calls += @{ Op = 'Enable' } }
function Disable-NetFirewallRule { $global:LGT.Violations += 'Disable-NetFirewallRule'; $global:LGT.Calls += @{ Op = 'Disable' } }
function netsh { $global:LGT.Violations += 'netsh'; $global:LGT.Calls += @{ Op = 'netsh' } }
function Get-NetTCPConnection {
    [CmdletBinding()] param([string]$State, [uint16[]]$LocalPort)
    $global:LGT.Listen | ForEach-Object { [pscustomobject]@{ LocalAddress = $_.Address; LocalPort = [uint16]$_.Port; State = 'Listen'; OwningProcess = 4 } }
}
function Test-LanIngressGuardAdmin { return [bool]$global:LGT.Admin }

# ---------------------------------------------------------------- helpers
function Invoke-Guard([string[]]$GuardArgs) {
    # Array splatting does not bind '-Name' strings to a script's parameters; build a hashtable.
    $h = @{}
    for ($i = 0; $i -lt $GuardArgs.Count; $i++) {
        $n = $GuardArgs[$i].TrimStart('-')
        if ($i + 1 -lt $GuardArgs.Count -and -not $GuardArgs[$i + 1].StartsWith('-')) { $h[$n] = $GuardArgs[$i + 1]; $i++ }
        else { $h[$n] = $true }
    }
    $global:LASTEXITCODE = 0
    try { $out = & $Script @h *>&1 | Out-String; $code = $LASTEXITCODE }
    catch { $out = 'THREW: ' + $_.Exception.Message; $code = 255 }
    return @{ Out = $out; Code = $code }
}

$PlanesToml = @'
[planes.frontend]
compose = "frontend/docker-compose.yml"
[planes.agent-org]
compose = "agent-org/docker/docker-compose.yml"
[planes.inference]
compose = "inference/docker-compose.yml"
[planes.ob1]
compose = "OB1/docker/docker-compose.yml"
'@
# The real layout: the PORTS in the manifest, the ADDRESSES in <manifest dir>\.stack\lan-ingress.toml.
# Addresses are RFC 5737 documentation addresses (192.0.2.0/24); the nodes are .11-.13.
$GoodIngress = @'
[lan_ingress.ports]
openwebui  = { plane = "frontend",  port = 3000 }
mattermost = { plane = "agent-org", port = 8065 }
'@
$GoodHost = @'
[lan_ingress]
host_ip        = "192.0.2.69"
allowed_remote = ["192.0.2.11", "192.0.2.12", "192.0.2.13"]
'@
$utf8 = New-Object Text.UTF8Encoding($false)
# A fixture is a directory holding stack.manifest.toml and, when $HostToml is not $null,
# .stack\lan-ingress.toml - which is where the guard looks by default. Returns the manifest path.
function New-Fixture([string]$Name, [string]$IngressToml, $HostToml = $GoodHost, [switch]$RawManifest) {
    $d = Join-Path $tmp $Name
    New-Item -ItemType Directory -Path (Join-Path $d '.stack') -Force | Out-Null
    $p = Join-Path $d 'stack.manifest.toml'
    $body = if ($RawManifest) { $IngressToml } else { $PlanesToml + "`n" + $IngressToml + "`n" }
    [IO.File]::WriteAllText($p, $body, $utf8)
    if ($null -ne $HostToml) { [IO.File]::WriteAllText((Join-Path $d '.stack\lan-ingress.toml'), ($HostToml + "`n"), $utf8) }
    return $p
}

# IPv4 check written independently of the guard's own parser.
function ConvertTo-V4Int([string]$ip) {
    $o = $ip.Split('.'); return ([int64]$o[0] * 16777216) + ([int64]$o[1] * 65536) + ([int64]$o[2] * 256) + [int64]$o[3]
}
function Test-V4Covered([string]$ip, [string[]]$ranges) {
    $v = ConvertTo-V4Int $ip
    foreach ($r in $ranges) {
        if ($r -match ':') { continue }
        if ($r -match '^([\d.]+)-([\d.]+)$') { if ($v -ge (ConvertTo-V4Int $Matches[1]) -and $v -le (ConvertTo-V4Int $Matches[2])) { return $true } }
        elseif ($r -match '^[\d.]+$') { if ($v -eq (ConvertTo-V4Int $r)) { return $true } }
        else { throw "test cannot read range '$r'" }
    }
    return $false
}
function Test-V6Full([string[]]$ranges) {
    foreach ($r in $ranges) {
        if ($r -notmatch ':') { continue }
        if ($r -match '^(.+)-(.+)$') {
            $a = [Net.IPAddress]::Parse($Matches[1]).GetAddressBytes(); $b = [Net.IPAddress]::Parse($Matches[2]).GetAddressBytes()
            if ((@($a | Where-Object { $_ -ne 0 }).Count -eq 0) -and (@($b | Where-Object { $_ -ne 255 }).Count -eq 0)) { return $true }
        }
        if ($r -eq '::/0') { return $true }
    }
    return $false
}

function Write-Case([string]$Id, [string]$Title, [bool]$Pass, [string]$Detail) {
    if ($global:LGT.Violations.Count -gt 0) { $Pass = $false; $Detail += "`nFORBIDDEN CALLS: " + ($global:LGT.Violations -join ', ') }
    $v = if ($Pass) { 'PASS' } else { 'FAIL' }
    if (-not $Pass) { $script:failures++ }
    $script:results += "$Id $v"
    Write-Host ("## {0} - {1}    {2}" -f $Id, $Title, $v)
    if ($Detail -and (-not $Pass -or $env:LGT_VERBOSE)) { foreach ($l in ($Detail -split "`n")) { Write-Host "    $l" } }
}

$good = New-Fixture 'good' $GoodIngress

# ---------------------------------------------------------------- T1 complement + boundaries
Reset-Fake; $global:LGT.Admin = $true
$r = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$news = @($global:LGT.Calls | Where-Object { $_.Op -eq 'New' })
$ok = ($r.Code -eq 0) -and ($news.Count -eq 2)
$det = "exit=$($r.Code) news=$($news.Count)`n$($r.Out)"
foreach ($n in $news) {
    $rem = @($n.RemoteAddress)
    foreach ($ip in @('192.0.2.10', '192.0.2.14', '0.0.0.0', '255.255.255.255', '192.0.2.1', '192.0.2.69', '10.0.0.1', '127.0.0.1')) {
        if (-not (Test-V4Covered $ip $rem)) { $ok = $false; $det += "`n$($n.Name): $ip NOT blocked" }
    }
    foreach ($ip in @('192.0.2.11', '192.0.2.12', '192.0.2.13')) {
        if (Test-V4Covered $ip $rem) { $ok = $false; $det += "`n$($n.Name): allowed node $ip IS blocked" }
    }
}
Write-Case 'T1' 'complement of .11-.13 blocks .10/.14 and the rest of IPv4, not the nodes' $ok $det

# ---------------------------------------------------------------- T2 IPv6
$ok = ($news.Count -eq 2)
foreach ($n in $news) { if (-not (Test-V6Full @($n.RemoteAddress))) { $ok = $false } }
Write-Case 'T2' 'every rule also blocks the whole IPv6 space' $ok ("remote: " + (($news | ForEach-Object { $_.RemoteAddress -join ',' }) -join ' | '))

# ---------------------------------------------------------------- T3 rule shape
$ok = ($news.Count -eq 2)
$det = ''
foreach ($n in $news) {
    $shape = ($n.Name.StartsWith($PREFIX)) -and ($n.Direction -eq 'Inbound') -and ($n.Action -eq 'Block') -and ($n.Protocol -eq 'TCP') -and
        (@($n.LocalAddress) -join ',') -eq 'Any' -and ($n.Profile -eq 'Any') -and ($n.Enabled -eq 'True')
    if (-not $shape) { $ok = $false; $det += "`nbad shape: $($n.Name) dir=$($n.Direction) act=$($n.Action) proto=$($n.Protocol) la=$($n.LocalAddress -join ',') prof=$($n.Profile) en=$($n.Enabled)" }
}
$ports = @($news | ForEach-Object { @($_.LocalPort) -join ',' } | Sort-Object)
if (($ports -join ' ') -ne '3000 8065') { $ok = $false; $det += "`nports: $($ports -join ' ')" }
Write-Case 'T3' 'one inbound TCP block rule per port, prefixed, any local address, all profiles' $ok $det

# ---------------------------------------------------------------- T4 idempotent re-apply
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$m = Get-Mutations
Write-Case 'T4' 'a second -Apply with the same source makes no change' (($r.Code -eq 0) -and ($m.Count -eq 0)) ("exit=$($r.Code) mutations=$($m.Count)`n$($r.Out)")

# ---------------------------------------------------------------- T5 port added + port removed
$changed = New-Fixture 'changed' @'
[lan_ingress.ports]
openwebui             = { plane = "frontend", port = 3000 }
openbrain-wiki-viewer = { plane = "ob1",      port = 8812 }
'@
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Apply', '-ManifestPath', $changed)
$m = Get-Mutations
$guard = @($global:LGT.Rules.Keys | Where-Object { $_.StartsWith($PREFIX) })
$guardPorts = @($guard | ForEach-Object { @($global:LGT.Rules[$_].LocalPort) -join ',' } | Sort-Object)
$removed = @($m | Where-Object { $_.Op -eq 'Remove' } | ForEach-Object { $_.Name })
$created = @($m | Where-Object { $_.Op -eq 'New' } | ForEach-Object { @($_.LocalPort) -join ',' })
$ok = ($r.Code -eq 0) -and (($guardPorts -join ' ') -eq '3000 8812') -and ($removed.Count -eq 1) -and ($removed[0] -match '8065') -and (($created -join ' ') -eq '8812')
Write-Case 'T5' 'port 8065 dropped + 8812 added: stale rule removed, new one created, 3000 untouched' $ok ("exit=$($r.Code) guard ports=$($guardPorts -join ' ') removed=$($removed -join ',') created=$($created -join ',')`n$($r.Out)")

# ---------------------------------------------------------------- T6 drift (widened remote) is repaired
Reset-Fake; $global:LGT.Admin = $true
$null = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$victim = @($global:LGT.Rules.Keys | Where-Object { $_.StartsWith($PREFIX) -and (@($global:LGT.Rules[$_].LocalPort) -join '') -eq '3000' })[0]
$statusSawDrift = $false; $fixed = $false; $s = @{ Code = ''; Out = 'no guard rule for 3000 to drift' }; $r = @{ Code = ''; Out = '' }
if ($victim) {
    $global:LGT.Rules[$victim].RemoteAddress = @('0.0.0.0-192.0.2.9', '192.0.2.14-255.255.255.255', '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
    $global:LGT.Calls = @()
    $s = Invoke-Guard @('-Status', '-ManifestPath', $good)
    $statusSawDrift = ($s.Out -match 'DRIFT') -and ($s.Code -ne 0) -and ((Get-Mutations).Count -eq 0)
    $r = Invoke-Guard @('-Apply', '-ManifestPath', $good)
    $fixed = $global:LGT.Rules.Contains($victim) -and -not (Test-V4Covered '192.0.2.11' @($global:LGT.Rules[$victim].RemoteAddress)) -and (Test-V4Covered '192.0.2.10' @($global:LGT.Rules[$victim].RemoteAddress))
}
Write-Case 'T6' 'a drifted rule (.10 let through) is reported by -Status and repaired by -Apply' ($statusSawDrift -and $fixed -and $r.Code -eq 0) ("status exit=$($s.Code)`n$($s.Out)`napply exit=$($r.Code)`n$($r.Out)")

# ---------------------------------------------------------------- T7 fail closed: bad sources abort with no change
$bad = [ordered]@{
    'ip-octet-256'   = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11", "192.0.2.256"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'cidr'           = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.0/24"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'dash-range'     = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11-13"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'wildcard'       = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["*"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'keyword-any'    = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["Any"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'short-ip'       = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'leading-zero'   = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.011"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'ipv6-allowed'   = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["2001:db8::1"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'empty-allowed'  = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = []' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'bad-host'       = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.690"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'empty-ports'    = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]'
    'no-ports'       = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]'
    'port-zero'      = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 0 }'
    'port-70000'     = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 70000 }'
    'port-string'    = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = "3000" }'
    'port-bool'      = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = true }'
    'dup-port'       = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }' + "`n" + 'other = { plane = "frontend", port = 3000 }'
    'unknown-plane'  = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontendd", port = 3000 }'
    'bad-svc-name'   = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + '"open webui*" = { plane = "frontend", port = 3000 }'
    'unknown-key'    = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + 'allow_all = true' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'no-table'       = ''
    'bad-toml'       = '[lan_ingress' + "`n" + 'host_ip = '
    'prefix-garbage' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["1192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'lead-space' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = [" 192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'trail-nl' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11\n"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'host-trail-nl' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69\n"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'mapped-v6' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["::ffff:192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'svc-trail-nl' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + '"openwebui\n" = { plane = "frontend", port = 3000 }'
    'miscased-port' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", Port = 3000 }'
    'miscased-extra' = '[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + 'allowed_remote = ["192.0.2.11"]' + "`n" + 'Allowed_Remote = ["0.0.0.0"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
    'miscased-keys' = '[lan_ingress]' + "`n" + 'Host_Ip = "192.0.2.69"' + "`n" + 'Allowed_Remote = ["192.0.2.11"]' + "`n" + '[lan_ingress.ports]' + "`n" + 'openwebui = { plane = "frontend", port = 3000 }'
}
# Each case runs in BOTH layouts: 'm' keeps the addresses in the manifest (no host file),
# 'h' moves the host_ip / allowed_remote lines into .stack\lan-ingress.toml (the real layout).
function Split-Case([string]$toml) {
    $hostLines = @(); $rest = @()
    foreach ($l in ($toml -split "`n")) { if ($l -match '^(host_ip|allowed_remote)\s*=') { $hostLines += $l } else { $rest += $l } }
    if ($hostLines.Count -eq 0) { return $null }
    return @{ Manifest = ($rest -join "`n"); Host = ("[lan_ingress]`n" + ($hostLines -join "`n")) }
}
# Where the REASON matters (two code paths could both abort), $Reason pins it.
$T7Reason = @{ 'miscased-keys' = "unknown key 'Host_Ip'"; 'miscased-port' = "unknown key 'Port'"; 'svc-trail-nl' = 'service name' }
function Test-AbortNoChange([string]$Id, [string]$Title, [string]$ManifestPath, [string]$Reason = '') {
    Reset-Fake; $global:LGT.Admin = $true
    $null = Invoke-Guard @('-Apply', '-ManifestPath', $good)   # a guard is in place from an earlier good apply
    $before = Get-Snapshot
    $global:LGT.Calls = @()
    $r = Invoke-Guard @('-Apply', '-ManifestPath', $ManifestPath)
    $m = Get-Mutations
    $ok = ($r.Code -eq 1) -and ($m.Count -eq 0) -and ((Get-Snapshot) -eq $before) -and ($r.Out -match 'ABORT:') -and ($r.Out -notmatch 'unexpected error') -and
        ((-not $Reason) -or ($r.Out -match [regex]::Escape($Reason)))
    Write-Case $Id $Title $ok ("exit=$($r.Code) mutations=$($m.Count)`n$($r.Out)")
}
foreach ($k in $bad.Keys) {
    Test-AbortNoChange "T7m.$k" "an invalid source ($k; addresses in the manifest) aborts with no change" (New-Fixture ('bad-m-' + $k) $bad[$k] $null) $T7Reason[$k]
    $split = Split-Case $bad[$k]
    if ($split) {
        Test-AbortNoChange "T7h.$k" "an invalid source ($k; addresses in the host file) aborts with no change" (New-Fixture ('bad-h-' + $k) $split.Manifest $split.Host)
    }
}
$hostBad = [ordered]@{
    'host-file-missing' = @($GoodIngress, $null)
    'set-in-both'       = @(('[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"' + "`n" + $GoodIngress), $GoodHost)
    'ports-in-host'     = @($GoodIngress, ($GoodHost + "`n" + '[lan_ingress.ports]' + "`n" + 'extra = { plane = "frontend", port = 9000 }'))
    'extra-table'       = @($GoodIngress, ($GoodHost + "`n" + '[other]' + "`n" + 'x = 1'))
    'host-unknown-key'  = @($GoodIngress, ($GoodHost + "`n" + 'allow_all = true'))
    'host-not-table'    = @($GoodIngress, 'lan_ingress = "192.0.2.11"')
    'host-empty'        = @($GoodIngress, '')
    'host-bad-toml'     = @($GoodIngress, '[lan_ingress' + "`n" + 'host_ip = ')
    'host-only-ip'      = @($GoodIngress, ('[lan_ingress]' + "`n" + 'host_ip = "192.0.2.69"'))
}
foreach ($k in $hostBad.Keys) {
    Test-AbortNoChange "T7x.$k" "a bad host address file ($k) aborts with no change" (New-Fixture ('bad-x-' + $k) $hostBad[$k][0] $hostBad[$k][1])
}
Test-AbortNoChange 'T7.missing-file' 'an unreadable manifest aborts with no change' (Join-Path $tmp 'does-not-exist.toml')

# ---------------------------------------------------------------- T8 prefix isolation on -Remove
Reset-Fake; $global:LGT.Admin = $true
$null = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Remove')
$left = @($global:LGT.Rules.Keys | Sort-Object)
$removed = @((Get-Mutations) | Where-Object { $_.Op -eq 'Remove' } | ForEach-Object { $_.Name })
$foreignOk = ($left -contains 'ai-stack-lan-guardian') -and ($left -contains 'other-ai-stack-lan-guard-x') -and (@($left | Where-Object { $_ -match 'com\.docker\.backend' }).Count -eq 1)
$ok = ($r.Code -eq 0) -and $foreignOk -and (@($left | Where-Object { $_.StartsWith($PREFIX) }).Count -eq 0) -and (@($removed | Where-Object { -not $_.StartsWith($PREFIX) }).Count -eq 0)
Write-Case 'T8' '-Remove deletes only its own prefixed rules (Docker, look-alikes untouched)' $ok ("exit=$($r.Code) left=$($left -join ' | ') removed=$($removed -join ',')`n$($r.Out)")
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Remove')
Write-Case 'T8b' 'a second -Remove is a no-op' (($r.Code -eq 0) -and ((Get-Mutations).Count -eq 0)) ("exit=$($r.Code)`n$($r.Out)")

# ---------------------------------------------------------------- T9 refusal without admin
foreach ($mode in @('-Apply', '-Remove')) {
    Reset-Fake; $global:LGT.Admin = $true
    $null = Invoke-Guard @('-Apply', '-ManifestPath', $good)
    $before = Get-Snapshot
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $r = Invoke-Guard @($mode, '-ManifestPath', $changed)
    $ok = ($r.Code -eq 2) -and ((Get-Mutations).Count -eq 0) -and ((Get-Snapshot) -eq $before) -and ($r.Out -match '(?i)administrator')
    Write-Case "T9$mode" "$mode refuses without admin, clearly, with no change" $ok ("exit=$($r.Code)`n$($r.Out)")
}
# Refusal must not depend on state: an in-sync -Apply and a -Remove with nothing to remove
# refuse too (exit 2) rather than reporting success to a non-elevated caller.
Reset-Fake; $global:LGT.Admin = $true
$null = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$global:LGT.Admin = $false; $global:LGT.Calls = @()
$r = Invoke-Guard @('-Apply', '-ManifestPath', $good)
Write-Case 'T9-Apply-insync' '-Apply refuses without admin even when already in sync' (($r.Code -eq 2) -and ((Get-Mutations).Count -eq 0)) ("exit=$($r.Code)`n$($r.Out)")
Reset-Fake; $global:LGT.Admin = $false
$r = Invoke-Guard @('-Remove')
Write-Case 'T9-Remove-empty' '-Remove refuses without admin even with nothing to remove' (($r.Code -eq 2) -and ((Get-Mutations).Count -eq 0)) ("exit=$($r.Code)`n$($r.Out)")

# ---------------------------------------------------------------- T10 dry run (-WhatIf and -DryRun)
foreach ($flag in @('-WhatIf', '-DryRun')) {
    Reset-Fake; $global:LGT.Admin = $false
    $r = Invoke-Guard @('-Apply', $flag, '-ManifestPath', $good)
    $creates = @(([regex]::Matches($r.Out, '(?m)^\s*CREATE\s+(\S+).*tcp/(\d+)')) | ForEach-Object { $_.Groups[2].Value } | Sort-Object)
    $ok = ($r.Code -eq 0) -and ((Get-Mutations).Count -eq 0) -and (($creates -join ' ') -eq '3000 8065') -and ($r.Out -match '0\.0\.0\.0-192\.0\.2\.10') -and ($r.Out -match '192\.0\.2\.14-255\.255\.255\.255') -and ($r.Out -match '(?i)no change')
    Write-Case "T10$flag" "-Apply $flag prints the exact plan without admin and changes nothing" $ok ("exit=$($r.Code) creates=$($creates -join ' ')`n$($r.Out)")
}
Reset-Fake; $global:LGT.Admin = $false
$null = (& { $global:LGT.Admin = $true; Invoke-Guard @('-Apply', '-ManifestPath', $good) }); $global:LGT.Admin = $false
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Remove', '-WhatIf')
$ok = ($r.Code -eq 0) -and ((Get-Mutations).Count -eq 0) -and (([regex]::Matches($r.Out, '(?m)^\s*REMOVE\s+ai-stack-lan-guard-')).Count -eq 2)
Write-Case 'T10-remove' '-Remove -WhatIf lists exactly its two rules and changes nothing' $ok ("exit=$($r.Code)`n$($r.Out)")

# ---------------------------------------------------------------- T11 -Status: read-only, no admin, listeners
Reset-Fake; $global:LGT.Admin = $true
$null = Invoke-Guard @('-Apply', '-ManifestPath', $good)
$global:LGT.Rules.Add($PREFIX + 'gone-9999', (New-FakeRule ($PREFIX + 'gone-9999') '9999' @('0.0.0.0-192.0.2.10')))
$global:LGT.Admin = $false
$global:LGT.Listen = @(@{ Address = '192.0.2.69'; Port = 3000 }, @{ Address = '127.0.0.1'; Port = 8065 }, @{ Address = '0.0.0.0'; Port = 8812 })
$changed3 = New-Fixture 'changed3' ($GoodIngress + "`n" + 'openbrain-wiki-viewer = { plane = "ob1", port = 8812 }')
$global:LGT.Calls = @()
$before = Get-Snapshot
$r = Invoke-Guard @('-Status', '-ManifestPath', $changed3)
$o = $r.Out
$ok = ((Get-Mutations).Count -eq 0) -and ((Get-Snapshot) -eq $before) -and ($r.Code -ne 0) -and
    ($o -match '(?m)OK\s+\S*3000') -and ($o -match '(?m)MISSING\s+\S*8812') -and ($o -match '(?m)STALE\s+\S*9999') -and
    ($o -match '(?m)3000.*192\.0\.2\.69') -and ($o -match '(?m)8065.*loopback') -and ($o -match '(?m)8812.*EXPOSED')
Write-Case 'T11' '-Status (no admin) reports OK/MISSING/STALE and LAN listeners, read-only, non-zero on drift' $ok ("exit=$($r.Code)`n$o")
Reset-Fake
$null = (& { $global:LGT.Admin = $true; Invoke-Guard @('-Apply', '-ManifestPath', $good) }); $global:LGT.Admin = $false
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Status', '-ManifestPath', $good)
Write-Case 'T11b' '-Status exits 0 when every rule matches the source' (($r.Code -eq 0) -and ((Get-Mutations).Count -eq 0)) ("exit=$($r.Code)`n$($r.Out)")

# ---------------------------------------------------------------- T12 the real source of truth
Reset-Fake; $global:LGT.Admin = $false
# The repo's real manifest (ports), copied beside a documentation-address host file: the real
# host file is per-host and gitignored, so it is not in a checkout.
$realCopy = New-Fixture 'real' ([IO.File]::ReadAllText($realManifest)) $GoodHost -RawManifest
$r = Invoke-Guard @('-Apply', '-WhatIf', '-ManifestPath', $realCopy)
$creates = @(([regex]::Matches($r.Out, '(?m)^\s*CREATE\s+(\S+).*tcp/(\d+)')) | ForEach-Object { $_.Groups[2].Value } | Sort-Object { [int]$_ })
$ok = ($r.Code -eq 0) -and ((Get-Mutations).Count -eq 0) -and (($creates -join ' ') -eq '3000 8065 8445 8812 8814') -and
    ($r.Out -match '0\.0\.0\.0-192\.0\.2\.10') -and ($r.Out -match '192\.0\.2\.14-255\.255\.255\.255') -and ($r.Out -match '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
Write-Case 'T12' 'the repo manifest [lan_ingress.ports]: -WhatIf lists exactly 3000 8065 8445 8812 8814 + the expected ranges' $ok ("exit=$($r.Code) creates=$($creates -join ' ')`n$($r.Out)")

# ---------------------------------------------------------------- T13 non-contiguous nodes
# This one keeps the addresses IN THE MANIFEST and has no host file: the other supported layout.
$gap = New-Fixture 'gap' @'
[lan_ingress]
host_ip        = "192.0.2.69"
allowed_remote = ["192.0.2.13", "192.0.2.11", "192.0.2.11"]

[lan_ingress.ports]
openwebui = { plane = "frontend", port = 3000 }
'@ $null
Reset-Fake; $global:LGT.Admin = $true
$r = Invoke-Guard @('-Apply', '-ManifestPath', $gap)
$n = @($global:LGT.Calls | Where-Object { $_.Op -eq 'New' })
$ok = ($r.Code -eq 0) -and ($n.Count -eq 1) -and (Test-V4Covered '192.0.2.12' @($n[0].RemoteAddress)) -and -not (Test-V4Covered '192.0.2.11' @($n[0].RemoteAddress)) -and -not (Test-V4Covered '192.0.2.13' @($n[0].RemoteAddress))
Write-Case 'T13' 'nodes .11 and .13 only (unsorted, duplicated; addresses in the manifest, no host file): .12 is blocked' $ok ("exit=$($r.Code) remote=$(if ($n) { $n[0].RemoteAddress -join ',' })`n$($r.Out)")

# ---------------------------------------------------------------- T14/T15 how the firewall prints addresses
# Windows may print a rule's remote addresses in another form than they were given (CIDR,
# dotted masks, split ranges). An EQUIVALENT set must read as in sync; a set that differs by
# one address, or holds a keyword the guard cannot evaluate, must read as drift.
$single = New-Fixture 'single' @'
[lan_ingress.ports]
openwebui = { plane = "frontend", port = 3000 }
'@
$equiv = @('0.0.0.0/1', '128.0.0.0-192.0.2.10', '192.0.2.14-192.0.2.255', '192.0.3.0/255.255.255.0', '192.0.4.0-255.255.255.255', '::/0')
Reset-Fake
$global:LGT.Rules[$PREFIX + 'openwebui-3000'] = (New-FakeRule ($PREFIX + 'openwebui-3000') '3000' $equiv)
$global:LGT.Calls = @()
$r = Invoke-Guard @('-Status', '-ManifestPath', $single)
Write-Case 'T14' 'an equivalent remote set in CIDR / mask / split-range form reads as OK' (($r.Code -eq 0) -and ($r.Out -match '(?m)^\s*OK\s')) ("exit=$($r.Code)`n$($r.Out)")
$variants = [ordered]@{
    'one-node-blocked'  = @('0.0.0.0-192.0.2.11', '192.0.2.14-255.255.255.255', '::/0')
    'dot10-let-through' = @('0.0.0.0-192.0.2.9', '192.0.2.14-255.255.255.255', '::/0')
    'no-ipv6'           = @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255')
    'half-ipv6'         = @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255', '::/1')
    'keyword'           = @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255', '::/0', 'LocalSubnet')
    'any'               = @('Any')
    'bad-mask'          = @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255', '10.0.0.0/255.0.255.0', '::/0')
}
foreach ($k in $variants.Keys) {
    Reset-Fake
    $global:LGT.Rules[$PREFIX + 'openwebui-3000'] = (New-FakeRule ($PREFIX + 'openwebui-3000') '3000' $variants[$k])
    $global:LGT.Calls = @()
    $r = Invoke-Guard @('-Status', '-ManifestPath', $single)
    Write-Case "T15.$k" "a remote set that is not exactly the complement ($k) reads as DRIFT" (($r.Code -eq 3) -and ($r.Out -match '(?m)^\s*DRIFT\s')) ("exit=$($r.Code)`n$($r.Out)")
}
foreach ($k in @('Action=Allow', 'Enabled=False', 'Profile=Private', 'Protocol=UDP', 'Direction=Outbound', 'LocalAddress=192.0.2.69', 'Program=C:\x.exe')) {
    $kv = $k.Split('=', 2)
    Reset-Fake
    $over = @{}; $over[$kv[0]] = $(if ($kv[0] -eq 'LocalAddress') { @($kv[1]) } else { $kv[1] })
    $global:LGT.Rules[$PREFIX + 'openwebui-3000'] = (New-FakeRule ($PREFIX + 'openwebui-3000') '3000' @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255', '::/0') $over)
    $global:LGT.Calls = @()
    $r = Invoke-Guard @('-Status', '-ManifestPath', $single)
    Write-Case "T15.$k" "a rule with $k reads as DRIFT" (($r.Code -eq 3) -and ($r.Out -match '(?m)^\s*DRIFT\s')) ("exit=$($r.Code)`n$($r.Out)")
}

# ---------------------------------------------------------------- T16 every rule field the drift check compares (TF1)
# A rule that is present but narrowed - to one remote port, one service, one interface, one
# user, one package, a platform, an owner - blocks (almost) nothing. Each such field must read
# as DRIFT in -Status and be repaired by -Apply, after which -Status is clean again. A field
# the read cannot find at all must read as DRIFT too, never as the expected value.
$narrow = [ordered]@{
    'RemotePort'         = '1'
    'DynamicTarget'      = 'ProximityApps'
    'Program'            = 'C:\x.exe'
    'Package'            = 'S-1-15-2-1'
    'Service'            = 'wuauserv'
    'InterfaceType'      = 'Wireless'
    'InterfaceAlias'     = 'Wi-Fi'
    'LocalUser'          = 'D:(A;;CC;;;S-1-5-18)'
    'RemoteUser'         = 'D:(A;;CC;;;S-1-5-21-1)'
    'RemoteMachine'      = 'D:(A;;CC;;;S-1-5-21-2)'
    'Authentication'     = 'Required'
    'Encryption'         = 'Required'
    'OverrideBlockRules' = $true
    'Owner'              = 'S-1-5-21-1-1-1-1001'
    'Platform'           = @('10.0')
    'Direction'          = 'Outbound'
    'Action'             = 'Allow'
    'Enabled'            = 'False'
    'Profile'            = 'Private'
    'Protocol'           = 'UDP'
    'LocalAddress'       = @('192.0.2.69')
    'RemotePort-missing' = $null
    'Service-missing'    = $null
    # attempt 3 (TA2-1): rule-level fields that narrow where a rule applies
    'PolicyAppId'                   = 'tester-tag'
    'PackageFamilyName'             = 'Contoso.App_8wekyb3d8bbwe'
    'RemoteDynamicKeywordAddresses' = @('{01234567-89ab-cdef-0123-456789abcdef}')
    'EdgeTraversalPolicy'           = 'Allow'
    'LooseSourceMapping'            = $true
    'LocalOnlyMapping'              = $true
    'IcmpType'                      = @('8')
    'PolicyAppId-missing'                   = $null
    'PackageFamilyName-missing'             = $null
    'RemoteDynamicKeywordAddresses-missing' = $null
    # attempt 3 (TA2-2): gaps three mutants slipped through
    'Owner-missing'          = $null
    'Platform-missing'       = $null
    'Package-missing'        = $null
    'LocalPort~3001'         = @('3001')
    'LocalPort~two'          = @('3000', '1')
    'RemotePort~any+1'       = @('Any', '1')
    'InterfaceAlias~any+eth' = @('Any', 'Ethernet')
    # a real shape seen on this host (TA2-3): two profiles is not Any
    'Profile~private-public' = 'Private, Public'
}
foreach ($k in $narrow.Keys) {
    $field = $k -replace '(-missing|~.*)$', ''
    Reset-Fake; $global:LGT.Admin = $true
    $null = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $n = $PREFIX + 'openwebui-3000'
    # RED-safe: with no guard (or a broken one) there is no rule to narrow; fail the case, do not crash.
    if (-not $global:LGT.Rules.Contains($n)) { Write-Case "T16.$k" "$k (setup)" $false 'the initial -Apply created no guard rule to work on'; continue }
    $expected = ((@($global:LGT.Rules[$n][$field]) | ForEach-Object { [string]$_ }) -join ',')
    if ($k -like '*-missing') { $global:LGT.Rules[$n].Remove($field) } else { $global:LGT.Rules[$n][$field] = $narrow[$k] }
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $s = Invoke-Guard @('-Status', '-ManifestPath', $single)
    $sawDrift = ($s.Code -eq 3) -and ($s.Out -match '(?m)^\s*DRIFT\s') -and ($s.Out -match ('(?i)drift:.*' + [regex]::Escape($field))) -and ((Get-Mutations).Count -eq 0)
    $global:LGT.Admin = $true; $global:LGT.Calls = @()
    $a = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $after = if ($global:LGT.Rules.Contains($n) -and $global:LGT.Rules[$n].ContainsKey($field)) { ((@($global:LGT.Rules[$n][$field]) | ForEach-Object { [string]$_ }) -join ',') } else { '<gone>' }
    $repaired = ($a.Code -eq 0) -and ($a.Out -match '(?m)^\s*REPLACE\s') -and ($after -eq $expected)
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $s2 = Invoke-Guard @('-Status', '-ManifestPath', $single)
    Write-Case "T16.$k" "a guard rule with $field narrowed/unreadable reads DRIFT, -Apply repairs it, -Status is clean after" ($sawDrift -and $repaired -and $s2.Code -eq 0) ("expected=$expected after=$after`nstatus exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)`nstatus-after exit=$($s2.Code)`n$($s2.Out)")
}

# ---------------------------------------------------------------- T17 the ends of the IPv4 space (TF4)
$ends = [ordered]@{
    'last-node'  = @('["255.255.255.254"]', @('255.255.255.255', '255.255.255.253', '0.0.0.0'), @('255.255.255.254'))
    'first-node' = @('["0.0.0.1"]', @('0.0.0.0', '0.0.0.2', '255.255.255.255'), @('0.0.0.1'))
    'both-ends'  = @('["0.0.0.0", "255.255.255.255"]', @('0.0.0.1', '255.255.255.254', '192.0.2.10'), @('0.0.0.0', '255.255.255.255'))
}
foreach ($k in $ends.Keys) {
    $f = New-Fixture ('ends-' + $k) $GoodIngress ("[lan_ingress]`nhost_ip = `"192.0.2.69`"`nallowed_remote = " + $ends[$k][0])
    Reset-Fake; $global:LGT.Admin = $true
    $r = Invoke-Guard @('-Apply', '-ManifestPath', $f)
    $n = @($global:LGT.Calls | Where-Object { $_.Op -eq 'New' })
    $ok = ($r.Code -eq 0) -and ($n.Count -eq 2)
    foreach ($x in $n) {
        foreach ($ip in $ends[$k][1]) { if (-not (Test-V4Covered $ip @($x.RemoteAddress))) { $ok = $false } }
        foreach ($ip in $ends[$k][2]) { if (Test-V4Covered $ip @($x.RemoteAddress)) { $ok = $false } }
    }
    Write-Case "T17.$k" "nodes $($ends[$k][0]): the neighbours at the ends of IPv4 are blocked, the nodes are not" $ok ("exit=$($r.Code) remote=$(if ($n) { $n[0].RemoteAddress -join ',' })`n$($r.Out)")
}

# ---------------------------------------------------------------- T18 the drift check is structural (attempt 3)
# A property the guard has never heard of, on any object, is DRIFT - not OK - and -Apply
# replaces the rule (the replacement does not carry it). A changed value of a property the
# guard compares (IsNegated, the CIM constants, PrimaryStatus) is DRIFT too.
$unknown = [ordered]@{
    'FutureNarrowingField'               = 'x'
    'port:FutureNarrowingField'          = 'x'
    'address:FutureNarrowingField'       = 'x'
    'application:FutureNarrowingField'   = 'x'
    'service:FutureNarrowingField'       = 'x'
    'interfacetype:FutureNarrowingField' = 'x'
    'interface:FutureNarrowingField'     = 'x'
    'security:FutureNarrowingField'      = 'x'
    'port:IsNegated'                     = $true
    'address:IsNegated'                  = $true
    'security:IsNegated'                 = $true
    'rule:Priority'                      = 5
    'rule:Mandatory'                     = $true
    'rule:PrimaryStatus'                 = 'Error'
    'rule:PolicyDecisionStrategy'        = [uint16]1
    'rule:SequencedActions'              = [uint16]1
}
foreach ($k in $unknown.Keys) {
    $prop = ($k -split ':')[-1]
    Reset-Fake; $global:LGT.Admin = $true
    $null = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $n = $PREFIX + 'openwebui-3000'
    # RED-safe: with no guard (or a broken one) there is no rule to narrow; fail the case, do not crash.
    if (-not $global:LGT.Rules.Contains($n)) { Write-Case "T18.$k" "$k (setup)" $false 'the initial -Apply created no guard rule to work on'; continue }
    $global:LGT.Rules[$n][$k] = $unknown[$k]
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $s = Invoke-Guard @('-Status', '-ManifestPath', $single)
    $sawDrift = ($s.Code -eq 3) -and ($s.Out -match '(?m)^\s*DRIFT\s') -and ($s.Out -match ('(?i)drift:.*' + [regex]::Escape($prop))) -and ((Get-Mutations).Count -eq 0)
    $global:LGT.Admin = $true; $global:LGT.Calls = @()
    $a = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $repaired = ($a.Code -eq 0) -and ($a.Out -match '(?m)^\s*REPLACE\s') -and $global:LGT.Rules.Contains($n) -and -not $global:LGT.Rules[$n].ContainsKey($k)
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $s2 = Invoke-Guard @('-Status', '-ManifestPath', $single)
    Write-Case "T18.$k" "a property $k = $($unknown[$k]) reads DRIFT (not OK), -Apply replaces the rule, -Status is clean after" ($sawDrift -and $repaired -and $s2.Code -eq 0) ("status exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)`nstatus-after exit=$($s2.Code)`n$($s2.Out)")
}

# ---------------------------------------------------------------- T19 no false drift (attempt 3)
# A freshly created guard rule, in the full real property set, reads in sync. So does one
# whose identity/label/status/housekeeping properties (the allow-list) or derived duplicates
# differ, and one with a real shape this host showed for an unrestricted value
# (DynamicTarget empty).
$benign = [ordered]@{
    'fresh'                        = $null
    'DynamicTarget~empty'          = @('DynamicTarget', '')
    'rule:Status'                  = @('rule:Status', 'something else')
    'rule:StatusCode'              = @('rule:StatusCode', [uint32]1)
    'rule:EnforcementStatus'       = @('rule:EnforcementStatus', 'Enforced')
    'rule:Description'             = @('rule:Description', 'edited by hand')
    'rule:DisplayGroup'            = @('rule:DisplayGroup', 'a group')
    'rule:PolicyStoreSourceType'   = @('rule:PolicyStoreSourceType', 'GroupPolicy')
    'port:Caption'                 = @('port:Caption', 'x')
    'security:HealthState'         = @('security:HealthState', [uint16]5)
    'application:AppPath'          = @('application:AppPath', 'C:\derived.exe')
}
foreach ($k in $benign.Keys) {
    Reset-Fake; $global:LGT.Admin = $true
    $null = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $n = $PREFIX + 'openwebui-3000'
    # RED-safe: with no guard (or a broken one) there is no rule to narrow; fail the case, do not crash.
    if (-not $global:LGT.Rules.Contains($n)) { Write-Case "T19.$k" "$k (setup)" $false 'the initial -Apply created no guard rule to work on'; continue }
    if ($benign[$k]) { $global:LGT.Rules[$n][$benign[$k][0]] = $benign[$k][1] }
    $global:LGT.Admin = $false; $global:LGT.Calls = @()
    $s = Invoke-Guard @('-Status', '-ManifestPath', $single)
    $global:LGT.Admin = $true; $global:LGT.Calls = @()
    $a = Invoke-Guard @('-Apply', '-ManifestPath', $single)
    $ok = ($s.Code -eq 0) -and ($s.Out -match '(?m)^\s*OK\s') -and ($a.Code -eq 0) -and ((Get-Mutations).Count -eq 0)
    Write-Case "T19.$k" "no false drift: $k reads OK and -Apply changes nothing" $ok ("status exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)")
}

# ---------------------------------------------------------------- T20 the operator's live rule shape (attempt 3)
# The first guard rule on the host was made by hand: Name is a GUID, DisplayName is
# 'ai-stack-lan-guard-openwebui-3000', and it allows ONE node (Traefik moved to a single
# edge host). Its real RemoteAddress, read live 2026-10-16, was
# '0.0.0.0-<lan>.19, <lan>.21-255.255.255.255, ::-ffff:...:ffff'. Here 192.0.2.20 stands in for the edge host.
$node20 = New-Fixture 'node20' "[lan_ingress.ports]`nopenwebui = { plane = `"frontend`", port = 3000 }" "[lan_ingress]`nhost_ip = `"192.0.2.69`"`nallowed_remote = [`"192.0.2.20`"]"
$handId = '{cb6370bd-a29e-4a3a-8200-0c11ebb17e42}'
$liveShape = @('0.0.0.0-192.0.2.19', '192.0.2.21-255.255.255.255', '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
function Add-HandRule([string]$Display, [string]$Port, [string[]]$Remote, [string]$Id = $handId) {
    $global:LGT.Rules[$Id] = (New-FakeRule $Id $Port $Remote @{ DisplayName = $Display; Group = $null })
}
# T20.whatif: one node computes exactly the live rule's ranges
Reset-Fake; $global:LGT.Admin = $false
$r = Invoke-Guard @('-Apply', '-WhatIf', '-ManifestPath', $node20)
$ok = ($r.Code -eq 0) -and ($r.Out -match [regex]::Escape('blocked remote (IPv4): 0.0.0.0-192.0.2.19, 192.0.2.21-255.255.255.255')) -and
    ($r.Out -match [regex]::Escape('blocked remote (IPv6): ::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')) -and ((Get-Mutations).Count -eq 0)
Write-Case 'T20.whatif' 'one allowed node (.20): -WhatIf computes exactly the live rule''s ranges' $ok ("exit=$($r.Code)`n$($r.Out)")
# T20.hand-in-sync: the hand-made rule (GUID name, prefixed DisplayName) reads OK; -Apply keeps it
Reset-Fake; Add-HandRule 'ai-stack-lan-guard-openwebui-3000' '3000' $liveShape
$before = Get-Snapshot
$s = Invoke-Guard @('-Status', '-ManifestPath', $node20)
$global:LGT.Admin = $true; $global:LGT.Calls = @()
$a = Invoke-Guard @('-Apply', '-ManifestPath', $node20)
$ok = ($s.Code -eq 0) -and ($s.Out -match '(?m)^\s*OK\s+ai-stack-lan-guard-openwebui-3000') -and ($s.Out -match [regex]::Escape($handId)) -and
    ($a.Code -eq 0) -and ((Get-Mutations).Count -eq 0) -and ((Get-Snapshot) -eq $before)
Write-Case 'T20.hand-in-sync' 'the hand-made live rule (GUID name) with the .20 shape reads OK and -Apply changes nothing' $ok ("status exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)")
# T20.hand-drift: the same hand rule still allowing the OLD three nodes reads DRIFT; -Apply
# creates the managed rule FIRST, then removes the hand rule by its GUID (no unguarded moment)
Reset-Fake; Add-HandRule 'ai-stack-lan-guard-openwebui-3000' '3000' @('0.0.0.0-192.0.2.10', '192.0.2.14-255.255.255.255', '::-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')
$s = Invoke-Guard @('-Status', '-ManifestPath', $node20)
$global:LGT.Admin = $true; $global:LGT.Calls = @()
$a = Invoke-Guard @('-Apply', '-ManifestPath', $node20)
$ops = @($global:LGT.Calls | Where-Object { $_.Op -ne 'Get' } | ForEach-Object { $_.Op + ':' + $_.Name })
$ok = ($s.Code -eq 3) -and ($s.Out -match '(?m)^\s*DRIFT\s+ai-stack-lan-guard-openwebui-3000') -and ($a.Code -eq 0) -and
    (($ops -join ' ') -eq ('New:' + $PREFIX + 'openwebui-3000 Remove:' + $handId)) -and -not $global:LGT.Rules.Contains($handId)
Write-Case 'T20.hand-drift' 'a drifted hand-made rule: -Apply creates the managed rule first, then removes the hand rule by GUID' $ok ("ops=$($ops -join ' ')`nstatus exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)")
# T20.duplicate: a managed rule AND a hand-made rule for the same port: keep one, remove the other
Reset-Fake; $global:LGT.Admin = $true
$null = Invoke-Guard @('-Apply', '-ManifestPath', $node20)
Add-HandRule 'ai-stack-lan-guard-openwebui-3000' '3000' $liveShape
$global:LGT.Admin = $false
$s = Invoke-Guard @('-Status', '-ManifestPath', $node20)
$global:LGT.Admin = $true; $global:LGT.Calls = @()
$a = Invoke-Guard @('-Apply', '-ManifestPath', $node20)
$left = @($global:LGT.Rules.Keys | Where-Object { $_ -eq $handId -or $_ -eq ($PREFIX + 'openwebui-3000') })
$ok = ($s.Code -eq 3) -and ($s.Out -match '(?m)^\s*STALE\s.*duplicate') -and ($a.Code -eq 0) -and ($left.Count -eq 1)
Write-Case 'T20.duplicate' 'a managed and a hand-made rule for one port: -Status flags the duplicate, -Apply leaves exactly one' $ok ("left=$($left -join ',')`nstatus exit=$($s.Code)`n$($s.Out)`napply exit=$($a.Code)`n$($a.Out)")
# T20.hand-stale + T20.remove: a hand-made rule for a port no longer listed is STALE and removed
# by GUID; -Remove removes hand-made rules too; a GUID rule whose DisplayName merely LOOKS
# like the prefix ('ai-stack-lan-guardian') is never touched.
Reset-Fake; Add-HandRule 'ai-stack-lan-guard-zzz-9999' '9999' $liveShape
Add-HandRule 'ai-stack-lan-guardian' '3000' @('Any') '{00000000-0000-0000-0000-000000000001}'
$global:LGT.Admin = $true
$a = Invoke-Guard @('-Apply', '-ManifestPath', $node20)
$ok = ($a.Code -eq 0) -and ($a.Out -match '(?m)^\s*REMOVE\s+ai-stack-lan-guard-zzz-9999') -and -not $global:LGT.Rules.Contains($handId) -and
    $global:LGT.Rules.Contains('{00000000-0000-0000-0000-000000000001}')
Write-Case 'T20.hand-stale' 'a hand-made rule for an unlisted port is STALE and removed by GUID; a look-alike DisplayName is untouched' $ok ("apply exit=$($a.Code)`n$($a.Out)")
Reset-Fake; Add-HandRule 'ai-stack-lan-guard-openwebui-3000' '3000' $liveShape
Add-HandRule 'ai-stack-lan-guardian' '3000' @('Any') '{00000000-0000-0000-0000-000000000001}'
$global:LGT.Admin = $true; $global:LGT.Calls = @()
$a = Invoke-Guard @('-Remove')
$removed = @($global:LGT.Calls | Where-Object { $_.Op -eq 'Remove' } | ForEach-Object { $_.Name })
$ok = ($a.Code -eq 0) -and (($removed -join ',') -eq $handId) -and $global:LGT.Rules.Contains('{00000000-0000-0000-0000-000000000001}') -and
    (@($global:LGT.Rules.Keys | Where-Object { $_ -match 'docker' }).Count -eq 1)
Write-Case 'T20.remove' '-Remove removes the hand-made rule by its GUID and nothing else' $ok ("removed=$($removed -join ',')`n$($a.Out)")

Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
Write-Host ''
Write-Host ("{0} case(s), {1} failed" -f $results.Count, $failures)
exit $failures
