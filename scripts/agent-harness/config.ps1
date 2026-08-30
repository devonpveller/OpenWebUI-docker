# config.ps1 - CONFIGURATION: read the harness settings, merge the layers, answer
# questions about them.
#
# Single responsibility: turn files + environment into settings. It knows nothing about
# git, worktrees, queues or leases - the layers above ask it for values. That is what lets
# another distribution retarget this toolkit by editing JSON instead of editing scripts.
#
# LAYERS, lowest to highest:
#   1. $script:Defaults below   - the harness still runs if every file is missing
#   2. harness.config.json      - committed policy; no secrets, ever
#   3. harness.local.json       - gitignored; machine-specific or experimental
#   4. a short, EXPLICIT list of environment variables (see Get-EnvOverrides)
#
# The environment layer is deliberately NOT a generic "any setting by env var" scheme.
# A generic scheme reads well in a README and is unusable in practice: nobody can tell
# which variables exist, and a typo silently does nothing. The supported overrides are
# listed in one function, and that list is the documentation.

# Built-in defaults. These duplicate harness.config.json on purpose: the file is policy
# that an operator may edit or delete, and the toolkit must still start. If the two ever
# disagree the FILE wins - these exist to keep a missing file from being a crash.
$script:Defaults = [ordered]@{
    version         = 1
    enabled         = $true
    default_profile = "all-cloud"
    surfaces        = [ordered]@{
        extension  = [ordered]@{ enabled = $true; profile = "all-cloud"; profile_locked = $true }
        mattermost = [ordered]@{ enabled = $true; profile = "all-cloud"; profile_locked = $false }
    }
    runners         = [ordered]@{
        "claude-code" = [ordered]@{ kind = "claude-code"; status = "proven"; default_model = "opus"; reachable_from = @("host") }
    }
    profiles        = [ordered]@{
        "all-cloud" = [ordered]@{
            worker   = [ordered]@{ runner = "claude-code"; model = "opus" }
            tester   = [ordered]@{ runner = "claude-code"; model = "opus" }
            reviewer = [ordered]@{ runner = "claude-code"; model = "opus" }
        }
    }
    pipeline        = [ordered]@{
        claim_ttl_minutes = 60
        anchor_required   = $true
        human_gates       = [ordered]@{ anchor = $true; pre_review = $true }
    }
    worktree        = [ordered]@{
        root               = ".claude/worktrees"
        dir_prefix         = "wt-"
        branch_prefix      = "work/"
        work_line_env      = "AI_STACK_WORK_LINE"
        work_line_fallback = "development"
        state_dir_env      = "AI_STACK_WORKTREE_STATE"
        state_dir_name     = "agent-worktrees"
        env_files          = @(".env", ".env.test", "OB1/docker/.env")
        test_image_tag_prefix = "wt-"
    }
    leases          = [ordered]@{ names_file = "lease-names.conf"; default_ttl_minutes = 30 }
}

$script:Cache = $null

function ConvertTo-HashtableDeep($obj) {
    # PowerShell 5.1's ConvertFrom-Json has no -AsHashtable, and merging PSCustomObjects
    # means re-implementing property assignment for every nesting level. Convert once,
    # merge with ordinary hashtable semantics.
    if ($null -eq $obj) { return $null }
    if ($obj -is [System.Collections.IDictionary]) {
        $h = [ordered]@{}
        foreach ($k in $obj.Keys) { $h[$k] = ConvertTo-HashtableDeep $obj[$k] }
        return $h
    }
    if ($obj -is [System.Management.Automation.PSCustomObject]) {
        $h = [ordered]@{}
        foreach ($p in $obj.PSObject.Properties) { $h[$p.Name] = ConvertTo-HashtableDeep $p.Value }
        return $h
    }
    if ($obj -is [array]) { return @($obj | ForEach-Object { ConvertTo-HashtableDeep $_ }) }
    return $obj
}

function Merge-Settings($base, $overlay) {
    # Deep merge: a map merges key by key, anything else REPLACES. Arrays replace rather
    # than concatenate - an operator narrowing `worktree.env_files` to one file must get
    # one file, not the default three plus theirs.
    if ($null -eq $overlay) { return $base }
    if (-not ($base -is [System.Collections.IDictionary]) -or
        -not ($overlay -is [System.Collections.IDictionary])) { return $overlay }
    $out = [ordered]@{}
    foreach ($k in $base.Keys) { $out[$k] = $base[$k] }
    foreach ($k in $overlay.Keys) {
        if ($out.Contains($k)) { $out[$k] = Merge-Settings $out[$k] $overlay[$k] }
        else { $out[$k] = $overlay[$k] }
    }
    return $out
}

function Read-JsonFile([string]$path) {
    if (-not (Test-Path $path)) { return $null }
    $raw = Get-Content -Raw -Path $path
    if (-not $raw -or -not $raw.Trim()) { return $null }
    try { return (ConvertTo-HashtableDeep (ConvertFrom-Json $raw)) }
    catch { throw "harness config '$path' is not valid JSON: $($_.Exception.Message)" }
}

function Get-EnvOverrides {
    # The complete list of environment overrides. Adding one means adding a row here and
    # a line in MODULE.md - there is no hidden naming convention to discover.
    #
    #   AI_STACK_HARNESS_CONFIG   path to an alternate harness.config.json
    #   AI_STACK_HARNESS_ENABLED  0/1 - kill switch, beats both files
    #   AI_STACK_HARNESS_PROFILE  profile name applied to every surface
    $o = [ordered]@{}
    if ($env:AI_STACK_HARNESS_ENABLED) {
        $o["enabled"] = ($env:AI_STACK_HARNESS_ENABLED -notin @("0", "false", "no", "off"))
    }
    if ($env:AI_STACK_HARNESS_PROFILE) { $o["default_profile"] = $env:AI_STACK_HARNESS_PROFILE }
    return $o
}

function Get-HarnessConfig {
    param([switch]$Fresh)
    if ($script:Cache -and -not $Fresh) { return $script:Cache }
    $cfgPath = if ($env:AI_STACK_HARNESS_CONFIG) { $env:AI_STACK_HARNESS_CONFIG }
               else { Join-Path $PSScriptRoot "harness.config.json" }
    $localPath = Join-Path $PSScriptRoot "harness.local.json"
    $merged = $script:Defaults
    $merged = Merge-Settings $merged (Read-JsonFile $cfgPath)
    $merged = Merge-Settings $merged (Read-JsonFile $localPath)
    $merged = Merge-Settings $merged (Get-EnvOverrides)
    $script:Cache = $merged
    return $merged
}

function Get-HarnessSetting {
    # One accessor, dotted path. Callers read as intent - Get-HarnessSetting worktree.root -
    # instead of walking nested hashtables at every call site.
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        $Default = $null
    )
    $node = Get-HarnessConfig
    foreach ($part in $Path.Split(".")) {
        if ($null -eq $node) { return $Default }
        if (-not ($node -is [System.Collections.IDictionary]) -or -not $node.Contains($part)) { return $Default }
        $node = $node[$part]
    }
    if ($null -eq $node) { return $Default }
    return $node
}

function Get-HarnessDisabledReason {
    # Returns "" when the harness may run, or a sentence saying why it may not.
    # Callers decide what to do with that - a script exits, the bridge stops offering
    # directives. "Off" has to be a stated reason, not an obscure failure.
    param([string]$Surface = "")
    if (-not (Get-HarnessSetting "enabled" $true)) {
        return "the agent harness is disabled (enabled=false in harness.config.json, or AI_STACK_HARNESS_ENABLED=0)"
    }
    if ($Surface) {
        $s = Get-HarnessSetting "surfaces.$Surface"
        if ($s -and $s.Contains("enabled") -and -not $s["enabled"]) {
            return "the agent harness is disabled for the '$Surface' surface (surfaces.$Surface.enabled=false)"
        }
    }
    return ""
}

function Get-HarnessProfileName {
    # Which profile applies on a surface. A LOCKED surface ignores every request, including
    # the environment override - that is what locked means (extension sessions, operator
    # decision 2026-08-28).
    param([string]$Surface = "", [string]$Requested = "")
    $s = if ($Surface) { Get-HarnessSetting "surfaces.$Surface" } else { $null }
    if ($s -and $s.Contains("profile_locked") -and $s["profile_locked"]) {
        if ($s.Contains("profile")) { return $s["profile"] }
    }
    if ($Requested) { return $Requested }
    if ($s -and $s.Contains("profile")) { return $s["profile"] }
    return (Get-HarnessSetting "default_profile" "all-cloud")
}

function Test-HarnessProfileLocked {
    param([string]$Surface)
    $s = Get-HarnessSetting "surfaces.$Surface"
    return [bool]($s -and $s.Contains("profile_locked") -and $s["profile_locked"])
}

function Resolve-RoleTarget {
    # role + profile -> the runner and model that role should execute on.
    # Throws on an unknown profile or role rather than quietly falling back: a typo in a
    # 'profile:' directive must be visible, not silently served by the default.
    param(
        [Parameter(Mandatory = $true)][ValidateSet("worker", "tester", "reviewer")][string]$Role,
        [string]$Profile = "",
        [string]$Surface = ""
    )
    $name = Get-HarnessProfileName -Surface $Surface -Requested $Profile
    $profiles = Get-HarnessSetting "profiles"
    if (-not $profiles -or -not $profiles.Contains($name)) {
        $known = if ($profiles) { ($profiles.Keys | Where-Object { $_ -notlike "_*" }) -join ", " } else { "(none)" }
        throw "unknown harness profile '$name' - known profiles: $known"
    }
    $p = $profiles[$name]
    if (-not $p.Contains($Role)) { throw "profile '$name' does not assign the '$Role' role" }
    $t = $p[$Role]
    $runnerName = $t["runner"]
    $runners = Get-HarnessSetting "runners"
    if (-not $runners -or -not $runners.Contains($runnerName)) {
        throw "profile '$name' assigns '$Role' to runner '$runnerName', which is not defined under runners"
    }
    $runner = $runners[$runnerName]
    $model = if ($t.Contains("model") -and $t["model"]) { $t["model"] }
             elseif ($runner.Contains("default_model")) { $runner["default_model"] }
             else { "" }
    return [ordered]@{
        role    = $Role
        profile = $name
        runner  = $runnerName
        kind    = $(if ($runner.Contains("kind")) { $runner["kind"] } else { $runnerName })
        model   = $model
        status  = $(if ($runner.Contains("status")) { $runner["status"] } else { "unknown" })
    }
}

function Get-HarnessProfileNames {
    $profiles = Get-HarnessSetting "profiles"
    if (-not $profiles) { return @() }
    return @($profiles.Keys | Where-Object { $_ -notlike "_*" })
}

# ---------------------------------------------------------------------------
# THE RUNNER REGISTRY (dark-factory-unification U4)
#
# A runner is an execution SUBSTRATE - what runs a role, of what kind, at what address.
# That is the one object this harness and agent-org genuinely share, so agent-org's bridge
# reads the SAME `runners` block out of the same file
# (agent-org/agent-bridge/app/modules/runners.py). Their PROFILE tables did not merge and
# should not: agent-org's profile binds a role to a model LANE for the bridge's own
# inference calls; this one binds a role to a runner. See
# documentation/notes/u4bidir-findings.md for why forcing them together would have made
# both worse.
#
# These are READERS only. Dispatch - actually submitting a task to a runner - is a
# separate concern and deliberately not here (config.ps1 knows nothing about git,
# worktrees, queues or HTTP, and gains nothing by learning).

function Get-HarnessRunnerNames {
    $runners = Get-HarnessSetting "runners"
    if (-not $runners) { return @() }
    return @($runners.Keys | Where-Object { $_ -notlike "_*" })
}

function Get-HarnessRunner {
    # One runner row, normalised. Throws on an unknown name rather than returning $null:
    # a typo in a runner name must be visible where it is made, not three calls later as a
    # dispatch to an empty endpoint.
    param([Parameter(Mandatory = $true)][string]$Name)
    $runners = Get-HarnessSetting "runners"
    if (-not $runners -or -not $runners.Contains($Name)) {
        $known = (Get-HarnessRunnerNames) -join ", "
        throw "unknown runner '$Name' - known runners: $known"
    }
    $r = $runners[$Name]
    $instances = [ordered]@{}
    if ($r.Contains("instances") -and $r["instances"]) {
        foreach ($k in $r["instances"].Keys) {
            if ($k -notlike "_*") { $instances[$k] = $r["instances"][$k] }
        }
    }
    return [ordered]@{
        name           = $Name
        kind           = $(if ($r.Contains("kind")) { $r["kind"] } else { $Name })
        status         = $(if ($r.Contains("status")) { $r["status"] } else { "unknown" })
        endpoint       = $(if ($r.Contains("endpoint")) { $r["endpoint"] } else { "" })
        default_model  = $(if ($r.Contains("default_model")) { $r["default_model"] } else { "" })
        instances      = $instances
        reachable_from = @(if ($r.Contains("reachable_from")) { $r["reachable_from"] } else { @() })
        # Whether an orchestrator may ACQUIRE these addresses as work capacity. NOT a
        # synonym for addressable: the coder plane little-coder IS addressable and is NOT
        # pooled - it is the operator interactive daemon on one shared /workspace.
        pooled         = [bool]$(if ($r.Contains("pooled")) { $r["pooled"] } else { $false })
    }
}

function Get-HarnessRunnerAddresses {
    # Every address the registry declares, in declaration order - pooled or not:
    #   @{ runner; label; url; kind; reachable_from; pooled }
    # A row with `instances` contributes each of them; a row with a single `endpoint`
    # contributes that one; a row with neither (claude-code) contributes nothing, because a
    # Claude Code agent is a host process with no task endpoint to address.
    # This is what check-runner-endpoints.ps1 walks: a declaration is worth checking whether
    # or not anyone is allowed to acquire it.
    $out = @()
    foreach ($name in Get-HarnessRunnerNames) {
        $r = Get-HarnessRunner -Name $name
        $rows = @()
        if ($r.instances.Count -gt 0) {
            foreach ($label in $r.instances.Keys) { $rows += , @($label, $r.instances[$label]) }
        }
        elseif ($r.endpoint) { $rows += , @($name, $r.endpoint) }
        foreach ($row in $rows) {
            $out += [ordered]@{ runner = $name; label = $row[0]; url = $row[1]; kind = $r.kind; reachable_from = $r.reachable_from; pooled = $r.pooled }
        }
    }
    return @($out)
}

function Get-HarnessRunnerPool {
    # The addresses an orchestrator may ACQUIRE as work capacity (`pooled: true`). This is
    # the list agent-org scheduler registers, which is why it is defined HERE and not in
    # agent-org: one declaration, three readers, no second opinion about which daemons are
    # the org to use.
    return @(Get-HarnessRunnerAddresses | Where-Object { $_.pooled })
}
