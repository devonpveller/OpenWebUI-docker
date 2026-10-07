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
        "claude-code" = [ordered]@{ kind = "claude-code"; status = "proven"; default_model = "opus" }
    }
    profiles        = [ordered]@{
        "all-cloud" = [ordered]@{
            worker   = [ordered]@{ runner = "claude-code"; model = "opus" }
            tester   = [ordered]@{ runner = "claude-code"; model = "opus" }
            reviewer = [ordered]@{ runner = "claude-code"; model = "opus" }
        }
    }
    gate_profiles   = [ordered]@{
        attended = [ordered]@{ anchor = "human"; pre_review = "human" }
        dark     = [ordered]@{ anchor = "auto";  pre_review = "auto" }
    }
    pipeline        = [ordered]@{
        claim_ttl_minutes = 60
        anchor_required   = $true
        gate_profile      = "attended"
        learning_record   = [ordered]@{ enforce_at_merged = $true }
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
    reap            = [ordered]@{ owner_label = "ai-stack.harness.owner" }
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
    # `,@(...)` - the leading comma is load-bearing. Returning `@()` from a PowerShell function
    # emits NOTHING, so an EMPTY array in the config became `$null` in the hashtable: the
    # shipped `work-branch-on-remote` params are `{"branches": []}`, and every gate record
    # wrote them as `{"branches":{}}` - so `looked_at`, whose whole job is to name the params a
    # predicate was handed, did not name the list it claimed to. Found 2026-08-30 in drill step
    # L's evidence line. The comma wraps the result so an empty array survives as an array.
    if ($obj -is [array]) { return , @($obj | ForEach-Object { ConvertTo-HashtableDeep $_ }) }
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

function Get-HarnessRunner {
    # The full runner record - kind, status, and (for a runner something has to CALL) its
    # transport topology. Resolve-RoleTarget deliberately returns only the policy answer;
    # dispatch.ps1 needs the topology too, and reading it here keeps that knowledge in the
    # config file instead of hardcoded in a dispatcher. Throws on an unknown name for the
    # same reason Resolve-RoleTarget does: a typo must be visible.
    param([Parameter(Mandatory = $true)][string]$Name)
    $runners = Get-HarnessSetting "runners"
    if (-not $runners -or -not $runners.Contains($Name)) {
        $known = if ($runners) { ($runners.Keys | Where-Object { $_ -notlike "_*" }) -join ", " } else { "(none)" }
        throw "unknown runner '$Name' - known runners: $known"
    }
    return $runners[$Name]
}

function Get-HarnessRunnerNames {
    $runners = Get-HarnessSetting "runners"
    if (-not $runners) { return @() }
    return @($runners.Keys | Where-Object { $_ -notlike "_*" })
}

# The pipeline gates, in the order a work item crosses them. Declared once so the two
# readers and the audit verifier cannot disagree about how many there are.
$script:Gates = @("anchor", "pre_review")

# Reserved principal namespace for a gate NOBODY looked at. A human -By value may never
# start with this, and an auto record may never omit it - that is what makes an auto-pass
# distinguishable from a human approval when the operator reads the ledger afterwards.
# A record that says only "passed" reads as approval, and is worse than no record at all.
$script:AutoPrincipalPrefix = "auto:"

function Get-GateNames { return @($script:Gates) }
function Get-AutoPrincipalPrefix { return $script:AutoPrincipalPrefix }

# THE ANDON CONDITIONS THE SYSTEM REQUIRES, declared HERE - in code - and deliberately not
# in harness.config.json. The config says which conditions are configured and with what
# parameters; this says which ones must EXIST.
#
# WHY IT IS NOT A CONFIG KEY (2026-08-30, and this is the defect that produced it): the
# board could be switched off two ways, and both were closed. They report DIFFERENT states,
# and this comment claimed otherwise until 2026-08-30 - the same false sentence as andon.ps1
# and config.py carried, all three written by the commit that made it false. The mapping is
# stated once, in README.md's ways-off table, and cited here by route id:
#   andon-disabled      -> not-evaluated
#   andon-block-deleted -> incomplete
# Both halt. There was a THIRD, and it is the one an operator or an agent actually reaches
# for: DELETE CONDITION ENTRIES from `andon.conditions` (route `conditions-deleted`). Pruned to one of five on a genuinely detached checkout, the gate
# AUTO-PASSED - exit 0, ledger `clear`, coverage `1 declared / 1 evaluated / 0 switched off`,
# `-VerifyAudit COMPLETE`. A thinned board was neither "absent" nor "switched off": it was a
# third state that reported itself perfectly healthy with four of five detectors gone.
#
# A required-set that lived in the same file as the conditions would be no guard at all -
# whoever deletes the entry deletes the name beside it, and the file agrees with itself.
# Here, retiring a condition is a CODE edit that shows up in a diff and passes a reviewer.
# That asymmetry IS the mechanism, the same one `$script:Predicates` in andon.ps1 already
# has: the config may not declare a detector nobody wrote, and it may not silently drop one
# somebody did. Mirrored in config.py as REQUIRED_ANDON_CONDITIONS; test_gate_profiles.py
# asks both readers and the shipped config the same question.
#
# There is deliberately NO environment override. A variable that thins the board is the
# same hole with a longer name.
#
# THE VALUE beside each id is the predicate that id is SUPPOSED to run. It pins the
# COMMITTED config and nothing else, and the difference matters:
#   - `test_gate_profiles.py` compares this map against harness.config.json, so an entry
#     that keeps a required id while naming a different predicate - id squatting, which the
#     id-set check at andon.ps1 cannot see because it compares ids only - fails the suite;
#   - `andon.ps1` reads only the KEYS. It does NOT re-check the predicate at run time, so a
#     swap in an uncommitted config, or in one named by AI_STACK_HARNESS_CONFIG, still runs
#     whatever the entry says. That route is OPEN and is named as open in README.md and
#     MODULE.md rather than papered over here.
$script:RequiredAndonConditions = [ordered]@{
    "operator-checkout-off-branch" = "git-checkout-state"
    "policy-declared-unread"       = "config-key-unread"
    "git-error-swallowed"          = "git-error-unchecked"
    "work-branch-on-remote"        = "branch-on-remote"
    "protected-ref-moved"          = "protected-ref-moved"
}

function Get-RequiredAndonConditionIds { return @($script:RequiredAndonConditions.Keys) }
function Get-RequiredAndonPredicate {
    param([Parameter(Mandatory = $true)][string]$Id)
    if ($script:RequiredAndonConditions.Contains($Id)) { return [string]$script:RequiredAndonConditions[$Id] }
    return ""
}

# THE ONLY WORDS an andon condition may use for `on_fire` and `on_indeterminate`. An action
# the board does not understand cannot be honoured, and guessing at one is how a config ends
# up deciding something nobody wrote down - so an unknown literal is refused rather than
# treated as either.
#
# `warn` does NOT mean "carry on". A fired condition is never a clear board, whatever its
# action says, so no unattended gate passes over one either way (andon.ps1
# Invoke-AndonEvaluation). What `warn` buys is the WORD - `warned` rather than `raised` -
# and the ledger's separate `fired` / `halted` lists, which is severity for a human reading
# afterwards, not permission for a machine at the time.
$script:AllowedAndonActions = @("halt", "warn")
function Get-AllowedAndonActions { return @($script:AllowedAndonActions) }

# THE OUTCOME TABLE - every (status, action) pair this board knows how to think about, and
# what bucket it counts as.
#
# WHY A TABLE AND NOT A LADDER (U6, 2026-08-30, and this is the fifth way off the board):
# the verdict used to be computed BY EXCEPTION. `$raised` was set only when the action was
# `halt`, `fired` only when the status was `fire`, and EVERY OTHER OUTCOME SET NOTHING -
# after which `clear` was what you got because nothing had objected. So any outcome nobody
# had enumerated silently meant "fine". That is the vacuous-check shape this whole effort
# keeps finding, sitting in the function that decides whether a human is needed, and it
# cost two rounds: `on_fire: warn` was closed on 2026-08-30 and `on_indeterminate: warn`
# reopened the identical hole on the sibling key the same day - a condition that could not
# be evaluated printed `ANDON BOARD: CLEAR` at exit 0, the dark gate auto-passed signed
# `auto:dark`, and the unevaluated condition was absent from the ledger entirely.
#
# So `clear` is PROVEN, not defaulted. Every result is classified through this table into
# EXACTLY ONE bucket; the buckets must SUM to the number of conditions the run had in
# scope; and `clear` requires every bucket except `evaluated_ok` to be EMPTY - stated
# positively, rather than as the absence of two particular flags.
#
# A KEY THAT IS NOT HERE IS NOT A PASS. An unlisted status (a predicate that grows a new
# answer) and an unlisted action (a word added to $script:AllowedAndonActions just above)
# both fall to `unrecognised`, which is a REFUSING bucket - no branch anywhere names the new
# word, and none has to. That generalisation is what drill step K proves: it introduces an
# action word and a status word this file has never heard of, in a scratch COPY of the
# harness whose verdict logic is asserted byte-identical to the shipped one, and the board
# refuses both.
$script:AndonBuckets = [ordered]@{
    "ok|none"            = "evaluated_ok"
    "fire|halt"          = "fired"
    "fire|warn"          = "fired"
    "indeterminate|halt" = "indeterminate"
    "indeterminate|warn" = "indeterminate"
    "disabled|none"      = "disabled"
}
# The bucket every result must be in to authorise an unattended pass, and the ONLY one that
# may be non-empty on a clear board.
$script:AndonClearBucket = "evaluated_ok"
$script:AndonUnrecognisedBucket = "unrecognised"
# Bucket -> the board's headline word, in SEVERITY ORDER (most severe first). This is also
# the declared bucket set: a bucket absent from here is not a bucket, and a result
# classified into one is `unaccounted`. Adding a bucket later means adding a word for it
# here, and until that is done the board refuses rather than guesses.
$script:AndonBucketBoard = [ordered]@{
    "unrecognised"  = "unaccounted"
    "fired"         = "warned"
    "indeterminate" = "indeterminate"
    "disabled"      = "partial"
    "evaluated_ok"  = "clear"
}
function Get-AndonBucketNames { return @($script:AndonBucketBoard.Keys) }
function Get-AndonBucket([string]$status, [string]$action) {
    $key = "{0}|{1}" -f $status, $action
    if ($script:AndonBuckets.Contains($key)) { return [string]$script:AndonBuckets[$key] }
    return $script:AndonUnrecognisedBucket
}

function Test-AutoPrincipal {
    param([string]$Principal)
    return [bool]($Principal -and $Principal.StartsWith($script:AutoPrincipalPrefix))
}

function Get-LearningRecordEnforced {
    # queue.ps1 -Merged runs learning_records.py check before any state change. True: a refusal
    # stops the merge record. False (the module's off switch for this gate, MODULE.md): the
    # check's result is printed as advice and the merge is recorded as before. Python twin:
    # config.learning_record_enforced().
    return [bool](Get-HarnessSetting "pipeline.learning_record.enforce_at_merged" $true)
}

function Get-GateProfileName {
    # Which gate profile is in force. An explicit request beats the configured default.
    param([string]$Requested = "")
    if ($Requested) { return $Requested }
    return (Get-HarnessSetting "pipeline.gate_profile" "attended")
}

function Get-GateProfileNames {
    $p = Get-HarnessSetting "gate_profiles"
    if (-not $p) { return @() }
    return @($p.Keys | Where-Object { $_ -notlike "_*" })
}

function Resolve-Gate {
    # gate + gate profile -> who passes it: "human" or "auto".
    # Throws rather than defaulting, for the same reason Resolve-RoleTarget does: a typo in
    # a gate profile name must be visible. Silently serving 'attended' would be safe and
    # silently serving 'dark' would not, and a rule that depends on which way the typo fell
    # is not a rule.
    param(
        [Parameter(Mandatory = $true)][string]$Gate,
        [string]$Profile = ""
    )
    if ($script:Gates -notcontains $Gate) {
        throw "unknown gate '$Gate' - known gates: $($script:Gates -join ', ')"
    }
    $name = Get-GateProfileName -Requested $Profile
    $profiles = Get-HarnessSetting "gate_profiles"
    if (-not $profiles -or -not $profiles.Contains($name)) {
        $known = if ($profiles) { (Get-GateProfileNames) -join ", " } else { "(none)" }
        throw "unknown gate profile '$name' - known gate profiles: $known"
    }
    $p = $profiles[$name]
    if (-not $p.Contains($Gate)) { throw "gate profile '$name' does not assign the '$Gate' gate" }
    $passer = [string]$p[$Gate]
    if ($passer -ne "human" -and $passer -ne "auto") {
        throw "gate profile '$name' assigns '$Gate' to '$passer' - only 'human' or 'auto'"
    }
    return [ordered]@{ gate = $Gate; profile = $name; passer = $passer }
}

function Get-HarnessProfileNames {
    $profiles = Get-HarnessSetting "profiles"
    if (-not $profiles) { return @() }
    return @($profiles.Keys | Where-Object { $_ -notlike "_*" })
}

# --- MODEL TIERS (tracker H2, mt-policy) ----------------------------------------------
# Which MODEL a role should run on for a given work item: the item's tier (large|small) plus
# the ordered rules in harness.config.json `model_tiers`, looked up in one of TWO SEPARATE
# maps - `cloud` (Claude Code subagents) and `local` (agent-org model roles). ADVISORY only:
# queue.ps1 prints the answer and never blocks on it. This is the ONE resolver (item
# ef-one-resolver removed the Python twin); test_model_tiers.py asks it every rule, and the
# non-canonical inputs, directly.
#
# THE STRING-COMPARISON CONTRACT, STRUCTURALLY (mt-policy attempt 3). Two attempts each closed one
# instance of PowerShell's default string comparison being looser than the policy: attempt 1 used
# -contains (case-INsensitive); attempt 2 used -ceq/-ccontains, which are CULTURE-aware and ignore
# U+00AD, U+200D and U+0000, and read config KEYS through PowerShell hashtables, which are case-
# insensitive ("Cloud" found "cloud"). So, in this section, with no exceptions:
#   1. every NAME - a tier, a role, a rule key, default_tier, an item tier - must match
#      ^[a-z][a-z0-9_-]{0,31}$ (ASCII, checked with a regex anchored by \z, no trimming, no
#      normalisation) BEFORE it is compared with anything; a name that does not is refused;
#   2. every comparison is ORDINAL ([string]::Equals(..., Ordinal)) - no -eq/-ceq/-contains;
#   3. every config KEY is found by an ordinal scan of the dictionary's own keys (Find-OrdinalKey),
#      never by .Contains()/[] with the canonical name, and every key at every level of the block
#      must be exactly one of the canonical spellings (or start with "_"), else it is refused;
#   4. numeric bounds are whole numbers in 0..2147483647 - fractions and out-of-range values are
#      refused (PowerShell reads 99999999999999999999 as a Decimal, not an int).
# (The messages are pinned word for word by test_model_tiers.py.)
$script:ModelTierNamePattern = '^[a-z][a-z0-9_-]{0,31}\z'
$script:ModelTierTopKeys = @("tiers", "default_tier", "doc_only_patterns", "rules", "cloud", "local")
$script:ModelTierSubstrateKeys = @("substrate", "roles", "trivial")
$script:ModelTierRuleKeys = @("id", "why", "tier", "role", "min_attempt", "max_attempt", "doc_only", "max_delta_lines")
$script:ModelTierIntKeys = @("min_attempt", "max_attempt", "max_delta_lines")
$script:ModelTierIntMax = 2147483647
$script:ModelTierRoles = @("developer", "tester", "reviewer")
$script:ModelTierSubstrates = @("cloud", "local")
$script:ModelTierFallbackDefault = "large"
$script:ModelTierProblemsFor = $null
$script:ModelTierProblemsCache = @()

function Get-JsonKindName($v) {
    # The JSON type name of a value as ConvertFrom-Json produced it ("array", "object",
    # "number"...), used in the refusal messages.
    if ($null -eq $v) { return "null" }
    if ($v -is [string]) { return "string" }
    if ($v -is [bool]) { return "boolean" }
    if (($v -is [array]) -or ($v -is [System.Collections.IList])) { return "array" }
    if (($v -is [System.Collections.IDictionary]) -or ($v -is [System.Management.Automation.PSCustomObject])) { return "object" }
    if ($v -is [ValueType]) { return "number" }
    return "object"
}

function Test-ModelTierNameShape($v) {
    # Rule 1: a string matching the ASCII name pattern exactly. No trimming: " small", "small\x1f"
    # and "sm<U+00AD>all" are not names, whatever a culture-aware comparison would say about them.
    return (($v -is [string]) -and [regex]::IsMatch($v, $script:ModelTierNamePattern))
}

function Test-OrdinalIn([string]$s, $set) {
    # Rule 2: ordinal membership.
    foreach ($x in @($set)) { if ([string]::Equals($s, [string]$x, [System.StringComparison]::Ordinal)) { return $true } }
    return $false
}

function Find-OrdinalKey($dict, [string]$name) {
    # Rule 3: the dictionary's OWN key spelled exactly $name, or $null. PowerShell hashtables
    # (and the [ordered] ones config.ps1 builds) match keys case-insensitively, so `.Contains`
    # and `[]` with a canonical name would find "Cloud" for "cloud"; this does not.
    if (-not ($dict -is [System.Collections.IDictionary])) { return $null }
    foreach ($k in @($dict.Keys)) {
        if ([string]::Equals([string]$k, $name, [System.StringComparison]::Ordinal)) { return $k }
    }
    return $null
}

function Test-ModelTierBound($v) {
    # Rule 4: an integer (not a boolean, not a fraction) in 0..2147483647.
    if (-not (($v -is [int]) -or ($v -is [long]) -or ($v -is [int16]) -or ($v -is [byte]))) { return $false }
    return (([long]$v -ge 0) -and ([long]$v -le $script:ModelTierIntMax))
}

function Get-ModelTiersBlock {
    # The model_tiers block by an ORDINAL key lookup on the merged config ($null when absent).
    $cfg = Get-HarnessConfig
    $k = Find-OrdinalKey $cfg "model_tiers"
    if ($null -eq $k) { return $null }
    return $cfg[$k]
}

function Get-ModelTierNames {
    # The configured tiers that are well-formed names, in order.
    $mt = Get-ModelTiersBlock
    $k = Find-OrdinalKey $mt "tiers"
    if ($null -eq $k) { return @() }
    $raw = $mt[$k]
    if ((Get-JsonKindName $raw) -ne "array") { return @() }
    return @(@($raw) | Where-Object { Test-ModelTierNameShape $_ } | ForEach-Object { [string]$_ })
}

function Add-UnknownKeyProblems($dict, $allowed, [string]$where, [string]$allowedText) {
    $out = @()
    foreach ($k in @($dict.Keys)) {
        $ks = [string]$k
        if ($ks.StartsWith("_", [System.StringComparison]::Ordinal)) { continue }
        if (-not (Test-OrdinalIn $ks $allowed)) { $out += ("{0} has an unknown key '{1}' - keys: {2}" -f $where, $ks, $allowedText) }
    }
    return $out
}

function Get-ModelTiersProblems {
    # Everything wrong with the model_tiers block, in a fixed order; empty when it is usable.
    # Cached per loaded config
    # object (the config is itself cached), so a resolve does not re-validate the block.
    $cfg = Get-HarnessConfig
    if ([object]::ReferenceEquals($cfg, $script:ModelTierProblemsFor)) { return @($script:ModelTierProblemsCache) }
    $p = @(Measure-ModelTiersProblems)
    $script:ModelTierProblemsFor = $cfg
    $script:ModelTierProblemsCache = $p
    return @($p)
}

function Measure-ModelTiersProblems {
    $mt = Get-ModelTiersBlock
    if ($null -eq $mt) { return @("harness.config.json has no model_tiers block") }
    if (-not ($mt -is [System.Collections.IDictionary])) { return @("model_tiers must be an object") }
    $p = @()
    $p += @(Add-UnknownKeyProblems $mt $script:ModelTierTopKeys "model_tiers" ($script:ModelTierTopKeys -join ", "))
    $tiers = @()
    $tk = Find-OrdinalKey $mt "tiers"
    $raw = $null
    if ($null -ne $tk) { $raw = $mt[$tk] }
    if ((Get-JsonKindName $raw) -ne "array" -or @($raw).Count -eq 0) {
        $p += "model_tiers.tiers must be a non-empty list of tier names"
    } else {
        foreach ($t in @($raw)) {
            if (-not ($t -is [string])) { $p += ("model_tiers.tiers holds a {0}, not a tier name" -f (Get-JsonKindName $t)); continue }
            if (-not (Test-ModelTierNameShape $t)) { $p += ("model_tiers.tiers entry '{0}' is not a name (^[a-z][a-z0-9_-]{{0,31}}$)" -f $t); continue }
            $tiers += $t
        }
    }
    $dk = Find-OrdinalKey $mt "default_tier"
    if ($null -ne $dk) {
        $d = $mt[$dk]
        if (-not ($d -is [string])) {
            $p += ("model_tiers.default_tier must be a tier name (got a {0})" -f (Get-JsonKindName $d))
        } elseif (-not ((Test-ModelTierNameShape $d) -and (Test-OrdinalIn $d $tiers))) {
            $p += ("model_tiers.default_tier '{0}' is not one of the tiers: {1}" -f $d, ($tiers -join ", "))
        }
    }
    $pk = Find-OrdinalKey $mt "doc_only_patterns"
    if ($null -ne $pk) {
        $pats = $mt[$pk]
        $okPats = ((Get-JsonKindName $pats) -eq "array")
        if ($okPats) { foreach ($x in @($pats)) { if (-not ($x -is [string]) -or $x.Length -eq 0) { $okPats = $false } } }
        if (-not $okPats) { $p += "model_tiers.doc_only_patterns must be a list of non-empty patterns" }
    }
    $rk = Find-OrdinalKey $mt "rules"
    $rules = $null
    if ($null -ne $rk) { $rules = $mt[$rk] }
    if ((Get-JsonKindName $rules) -ne "array") {
        $p += "model_tiers.rules must be a list"
    } else {
        $n = 0
        foreach ($r in @($rules)) {
            $n++
            if (-not ($r -is [System.Collections.IDictionary])) { $p += ("model_tiers rule #{0} is a {1}, not an object" -f $n, (Get-JsonKindName $r)); continue }
            $ik = Find-OrdinalKey $r "id"
            $rid = "#$n"
            if (($null -ne $ik) -and ($r[$ik] -is [string]) -and ([string]$r[$ik]).Length -gt 0) { $rid = [string]$r[$ik] }
            $p += @(Add-UnknownKeyProblems $r $script:ModelTierRuleKeys ("model_tiers rule '{0}'" -f $rid) ($script:ModelTierRuleKeys -join ", "))
            $tvk = Find-OrdinalKey $r "tier"
            $tv = $null
            if ($null -ne $tvk) { $tv = $r[$tvk] }
            if (-not ($tv -is [string])) {
                $p += ("model_tiers rule '{0}' tier must be a tier name (got a {1})" -f $rid, (Get-JsonKindName $tv))
            } elseif (-not ((Test-ModelTierNameShape $tv) -and ((Test-OrdinalIn $tv @("item")) -or (Test-OrdinalIn $tv $tiers)))) {
                $p += ("model_tiers rule '{0}' names unknown tier '{1}' - known tiers: {2} (or item)" -f $rid, $tv, ($tiers -join ", "))
            }
            $rok = Find-OrdinalKey $r "role"
            if ($null -ne $rok) {
                $rv = $r[$rok]
                if (-not ($rv -is [string])) {
                    $p += ("model_tiers rule '{0}' role must be a role name (got a {1})" -f $rid, (Get-JsonKindName $rv))
                } elseif (-not ((Test-ModelTierNameShape $rv) -and (Test-OrdinalIn $rv $script:ModelTierRoles))) {
                    $p += ("model_tiers rule '{0}' names unknown role '{1}' - known roles: {2}" -f $rid, $rv, ($script:ModelTierRoles -join ", "))
                }
            }
            foreach ($key in $script:ModelTierIntKeys) {
                $kk = Find-OrdinalKey $r $key
                if (($null -ne $kk) -and -not (Test-ModelTierBound $r[$kk])) {
                    $p += ("model_tiers rule '{0}' key '{1}' must be a whole number from 0 to {2} (got a {3})" -f $rid, $key, $script:ModelTierIntMax, (Get-JsonKindName $r[$kk]))
                }
            }
            $dok = Find-OrdinalKey $r "doc_only"
            if (($null -ne $dok) -and -not ($r[$dok] -is [bool])) {
                $p += ("model_tiers rule '{0}' key 'doc_only' must be true or false (got a {1})" -f $rid, (Get-JsonKindName $r[$dok]))
            }
        }
    }
    foreach ($sub in $script:ModelTierSubstrates) {
        $sk = Find-OrdinalKey $mt $sub
        $sv = $null
        if ($null -ne $sk) { $sv = $mt[$sk] }
        $roles = $null
        if ($sv -is [System.Collections.IDictionary]) {
            $p += @(Add-UnknownKeyProblems $sv $script:ModelTierSubstrateKeys ("model_tiers.{0}" -f $sub) ($script:ModelTierSubstrateKeys -join ", "))
            $rsk = Find-OrdinalKey $sv "roles"
            if ($null -ne $rsk) { $roles = $sv[$rsk] }
        }
        if ($roles -is [System.Collections.IDictionary]) {
            $p += @(Add-UnknownKeyProblems $roles $tiers ("model_tiers.{0}.roles" -f $sub) ($tiers -join ", "))
        }
        foreach ($t in $tiers) {
            $map = $null
            $mk = Find-OrdinalKey $roles $t
            if ($null -ne $mk) { $map = $roles[$mk] }
            if ($map -is [System.Collections.IDictionary]) {
                $p += @(Add-UnknownKeyProblems $map $script:ModelTierRoles ("model_tiers.{0}.roles.{1}" -f $sub, $t) ($script:ModelTierRoles -join ", "))
            }
            foreach ($role in $script:ModelTierRoles) {
                $m = $null
                $ck = Find-OrdinalKey $map $role
                if ($null -ne $ck) { $m = $map[$ck] }
                if (-not ($m -is [string]) -or $m.Length -eq 0) { $p += ("model_tiers.{0}.roles.{1}.{2} is not set" -f $sub, $t, $role) }
            }
        }
    }
    return $p
}

function Get-DefaultModelTier {
    # The documented default for an item that names no tier: `model_tiers.default_tier`, and
    # "large" if even that is missing - an unclassified item never drops to a smaller model.
    $mt = Get-ModelTiersBlock
    $dk = Find-OrdinalKey $mt "default_tier"
    if ($null -ne $dk) {
        $d = $mt[$dk]
        if (Test-ModelTierNameShape $d) { return [string]$d }
    }
    return $script:ModelTierFallbackDefault
}

function Test-ModelTierName {
    # "" when the value is a well-formed name AND a configured tier (ordinal), else the
    # sentence a caller prints.
    param([AllowEmptyString()][string]$Tier)
    $known = @(Get-ModelTierNames)
    if ((Test-ModelTierNameShape $Tier) -and (Test-OrdinalIn $Tier $known)) { return "" }
    return ("unknown tier '{0}' - known tiers: {1}" -f $Tier, ($known -join ", "))
}

function Resolve-ModelTier {
    # role + item facts -> the tier that applies, the rule that decided it, and the model in
    # EACH map. Throws on a malformed block or an unknown role or tier - the caller is advisory
    # and reports the throw as "unavailable"; it is the CONFIG that must be loud, not the queue
    # that must stop.
    #   -DocOnly    $true / $false, or $null when unknown (unknown never matches a doc_only rule)
    #   -DeltaLines lines changed since the previous attempt, or $null when unknown
    param(
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Role,
        [AllowEmptyString()][string]$ItemTier = "",
        [int]$Attempt = 1,
        $DocOnly = $null,
        $DeltaLines = $null
    )
    if (-not ((Test-ModelTierNameShape $Role) -and (Test-OrdinalIn $Role $script:ModelTierRoles))) {
        throw ("unknown role '{0}' for a model tier - known roles: {1}" -f $Role, ($script:ModelTierRoles -join ", "))
    }
    $problems = @(Get-ModelTiersProblems)
    if ($problems.Count -gt 0) { throw $problems[0] }
    $mt = Get-ModelTiersBlock
    $itemT = if ($ItemTier.Length -gt 0) { $ItemTier } else { Get-DefaultModelTier }
    $bad = Test-ModelTierName $itemT
    if ($bad) { throw $bad }
    foreach ($r in @($mt[(Find-OrdinalKey $mt "rules")])) {
        $k = Find-OrdinalKey $r "role"
        if (($null -ne $k) -and -not [string]::Equals([string]$r[$k], $Role, [System.StringComparison]::Ordinal)) { continue }
        $k = Find-OrdinalKey $r "min_attempt"
        if (($null -ne $k) -and ([long]$Attempt -lt [long]$r[$k])) { continue }
        $k = Find-OrdinalKey $r "max_attempt"
        if (($null -ne $k) -and ([long]$Attempt -gt [long]$r[$k])) { continue }
        $k = Find-OrdinalKey $r "doc_only"
        if ($null -ne $k) {
            if ($null -eq $DocOnly) { continue }
            if ([bool]$DocOnly -ne [bool]$r[$k]) { continue }
        }
        $k = Find-OrdinalKey $r "max_delta_lines"
        if ($null -ne $k) {
            if ($null -eq $DeltaLines) { continue }
            if ([long]$DeltaLines -gt [long]$r[$k]) { continue }
        }
        $t = [string]$r[(Find-OrdinalKey $r "tier")]
        if ([string]::Equals($t, "item", [System.StringComparison]::Ordinal)) { $t = $itemT }
        $ik = Find-OrdinalKey $r "id"
        $wk = Find-OrdinalKey $r "why"
        $out = [ordered]@{
            role = $Role; attempt = $Attempt; item_tier = $itemT; tier = $t
            rule = $(if ($null -ne $ik) { [string]$r[$ik] } else { "" })
            why = $(if ($null -ne $wk) { [string]$r[$wk] } else { "" })
        }
        foreach ($sub in $script:ModelTierSubstrates) {
            $roles = $mt[(Find-OrdinalKey $mt $sub)][(Find-OrdinalKey $mt[(Find-OrdinalKey $mt $sub)] "roles")]
            $map = $roles[(Find-OrdinalKey $roles $t)]
            $out[$sub] = [string]$map[(Find-OrdinalKey $map $Role)]
        }
        return $out
    }
    throw ("no model_tiers rule matched role '{0}' - the rule list needs a final catch-all (tier: item)" -f $Role)
}

function Format-ModelTierAdvice {
    # The lines queue.ps1 prints. One rendering, so every door says it the same way.
    param([Parameter(Mandatory = $true)]$Advice)
    $l1 = ("MODEL (advisory): next role {0}, attempt {1} -> tier {2} [rule {3}; item tier {4}]" -f
           $Advice.role, $Advice.attempt, $Advice.tier, $Advice.rule, $Advice.item_tier)
    $l2 = ("  cloud (Claude Code subagent): {0}   |   local (agent-org model role): {1}" -f $Advice.cloud, $Advice.local)
    $l3 = ("  why: {0}" -f $Advice.why)
    return @($l1, $l2, $l3)
}
