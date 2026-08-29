# anchor.ps1 - the ANCHOR: what the work is for, agreed before the work starts.
#
# WHY THIS EXISTS (operator, 2026-08-28). The first real run of this pipeline shipped two
# READMEs that passed every check and were still the wrong artifact - 46% of one was a
# defect log, because the task prompt had told the agent that finding disagreements was
# the deliverable. Nothing in the pipeline asked whether the artifact was the thing that
# was asked for. The test plan verified that each claim was TRUE. The review verified that
# each claim was SUPPORTED. A pipeline that validates correctness and never validates
# intent will reliably ship a correct answer to the wrong question.
#
# The anchor is the fix, in the operator's words: "the original prompt should be phrased
# and confirmed with the end user to ensure alignment then generating a plan before work
# begins." It is written BEFORE the work, CONFIRMED by the operator, and it outlives the
# prompt - which lives in one agent's context and is gone by the time a tester or reviewer
# needs to know what "done" meant.
#
# Single responsibility: the SHAPE of an anchor and whether a given one is usable. It owns
# no state and performs no transitions - queue.ps1 does that, and asks this file whether
# the anchor it was handed is worth gating on.
#
# THE SHAPE ITSELF IS NOT HERE (2026-08-29, dark-factory-unification U2). It lives in
# anchor.schema.json, because a field table hardcoded in PowerShell is a policy literal in
# source (PLAN A.2) and because PowerShell cannot be the only thing that knows what an
# anchor is - anchor_schema.py is the twin reader, and test_anchor_schema.py pins the two
# together. This file is now a thin reader plus the validation semantics.

$script:AnchorSchemaPath = Join-Path $PSScriptRoot "anchor.schema.json"
$script:AnchorSchema = $null

function Get-AnchorSchema {
    # Cached; the schema does not change within a run. Deliberately THROWS if the file is
    # missing or unparseable rather than falling back to a built-in copy: a silent fallback
    # is how the two readers would drift apart without anything failing.
    if ($null -ne $script:AnchorSchema) { return $script:AnchorSchema }
    if (-not (Test-Path $script:AnchorSchemaPath)) {
        throw "anchor schema not found: '$($script:AnchorSchemaPath)'. It defines what an anchor is; without it nothing can validate one."
    }
    try { $script:AnchorSchema = Get-Content -Raw -Path $script:AnchorSchemaPath | ConvertFrom-Json }
    catch { throw "anchor schema '$($script:AnchorSchemaPath)' is not valid JSON: $($_.Exception.Message)" }
    return $script:AnchorSchema
}

function Get-AnchorMode($anchor) {
    # Absent mode means B. Every anchor written before the schema existed has no mode field,
    # and all of them stay valid - this was an extension, not a migration.
    $schema = Get-AnchorSchema
    $default = $schema.default_mode
    if ($null -eq $anchor) { return $default }
    if (-not ($anchor.PSObject.Properties.Name -contains "mode")) { return $default }
    $m = "$($anchor.mode)".Trim()
    if (-not $m) { return $default }
    return $m.ToUpperInvariant()
}

function Get-AnchorModeSpec([string]$mode) {
    $schema = Get-AnchorSchema
    $names = @($schema.modes.PSObject.Properties.Name)
    if ($names -notcontains $mode) { return $null }
    return $schema.modes.$mode
}

function Get-AnchorFieldHelp([string]$mode = "") {
    $schema = Get-AnchorSchema
    if (-not $mode) { $mode = $schema.default_mode }
    $spec = Get-AnchorModeSpec $mode
    if ($null -eq $spec) {
        return ("  (unknown mode '{0}' - known modes: {1})" -f $mode, (@($schema.modes.PSObject.Properties.Name) -join ", "))
    }
    $lines = @("  mode {0} - {1}" -f $mode, $spec.desc)
    foreach ($p in $spec.fields.PSObject.Properties) {
        $req = if ($p.Value.required) { "required" } else { "optional" }
        $lines += ("  {0,-14} {1,-8} {2}" -f $p.Name, "($req)", $p.Value.why)
    }
    foreach ($p in $spec.forbidden.PSObject.Properties) {
        $lines += ("  {0,-14} {1,-8} {2}" -f $p.Name, "(REFUSED)", $p.Value)
    }
    return ($lines -join "`n")
}

function Test-Anchor($anchor) {
    # Returns a list of problems, empty when the anchor is usable. Deliberately returns
    # rather than throws: the caller decides whether a bad anchor is fatal (queue.ps1) or
    # merely reported (a linting pass, an agent checking its own draft before proposing).
    $problems = @()
    if ($null -eq $anchor) { return @("the anchor is empty") }
    $schema = Get-AnchorSchema
    $mode = Get-AnchorMode $anchor
    $spec = Get-AnchorModeSpec $mode
    if ($null -eq $spec) {
        # A typo in `mode` must be loud. Defaulting an unknown mode to B would validate a
        # generative anchor against a bounded contract and pass it for the wrong reasons.
        return @(("unknown anchor mode '{0}' - known modes: {1}" -f
                  $mode, (@($schema.modes.PSObject.Properties.Name) -join ", ")))
    }
    foreach ($p in $spec.fields.PSObject.Properties) {
        $k = $p.Name
        $f = $p.Value
        $has = ($anchor.PSObject.Properties.Name -contains $k)
        $val = if ($has) { $anchor.$k } else { $null }
        if ($f.kind -eq "list") {
            $items = @($val | Where-Object { $_ -ne $null -and "$_".Trim() })
            if ($f.required -and $items.Count -lt 1) {
                $problems += ("'{0}' must list at least one entry - {1}" -f $k, $f.why)
            }
        } else {
            if ($f.required -and (-not $val -or -not "$val".Trim())) {
                $problems += ("'{0}' is required - {1}" -f $k, $f.why)
            }
        }
    }
    # Fields this mode REFUSES. Mode A rejecting `acceptance` is the load-bearing case: it
    # is a category error, not a style preference (see the schema's note on gym-024).
    foreach ($p in $spec.forbidden.PSObject.Properties) {
        $k = $p.Name
        if ($anchor.PSObject.Properties.Name -contains $k) {
            $items = @($anchor.$k | Where-Object { $_ -ne $null -and "$_".Trim() })
            if ($items.Count -ge 1 -or ("$($anchor.$k)".Trim() -and $anchor.$k -isnot [array])) {
                $problems += ("mode {0} anchors must not carry '{1}' - {2}" -f $mode, $k, $p.Value)
            }
        }
    }
    # An acceptance criterion nobody can check is a wish. This catches the common shape -
    # a single vague line - without pretending to judge English.
    $minLen = [int]$schema.rules.min_acceptance_criterion_chars
    if ($anchor.PSObject.Properties.Name -contains "acceptance" -and
        ($spec.fields.PSObject.Properties.Name -contains "acceptance")) {
        # Blank entries are skipped: an all-blank list already reported "must list at least
        # one entry", and adding "criterion '   ' is too short" on top is a second complaint
        # about one problem. (The cross-reader test caught this differing from Python.)
        foreach ($c in @($anchor.acceptance | Where-Object { $_ -ne $null -and "$_".Trim() })) {
            if ("$c".Trim().Length -lt $minLen) {
                $problems += (("acceptance criterion '{0}' is too short to check - say what " +
                               "would count as failing it") -f $c)
            }
        }
    }
    return $problems
}

function Read-AnchorFile([string]$path) {
    # Parse + validate in one place, so no caller can hold an unvalidated anchor.
    if (-not (Test-Path $path)) {
        throw ("anchor file not found: '$path'. Start from anchor.template.json in this " +
               "directory - the fields are:`n" + (Get-AnchorFieldHelp))
    }
    $raw = Get-Content -Raw -Path $path
    try { $anchor = ConvertFrom-Json $raw }
    catch { throw "anchor file '$path' is not valid JSON: $($_.Exception.Message)" }
    $problems = Test-Anchor $anchor
    if ($problems.Count) {
        # Show the help for the mode the author ACTUALLY declared, not the default - telling
        # someone writing a mode A anchor that 'acceptance' is required would be worse than
        # saying nothing.
        throw ("the anchor in '$path' is not usable:`n  - " + ($problems -join "`n  - ") +
               "`n`nFields:`n" + (Get-AnchorFieldHelp (Get-AnchorMode $anchor)))
    }
    return $anchor
}

function Format-Anchor($anchor) {
    # The rendering a tester and a reviewer read. Every role sees the SAME text - if the
    # tester and the reviewer are working from different summaries of the goal, the anchor
    # has failed at the one job it has.
    if ($null -eq $anchor) { return "(no anchor)" }
    $mode = Get-AnchorMode $anchor
    $spec = Get-AnchorModeSpec $mode
    $out = @()
    # Mode B is the overwhelming majority and its rendering is the one every tester and
    # reviewer already reads, so it is NOT prefixed with a mode banner - only mode A is,
    # where "there is no acceptance list" is information the reader needs up front.
    if ($mode -ne (Get-AnchorSchema).default_mode) {
        $desc = if ($null -ne $spec) { $spec.desc } else { "unknown mode" }
        $out += ("MODE      : {0} - {1}" -f $mode, $desc)
    }
    if ($anchor.PSObject.Properties.Name -contains "north_star" -and $anchor.north_star) {
        $out += "NORTH STAR: " + $anchor.north_star
    }
    if ($anchor.PSObject.Properties.Name -contains "goal" -and $anchor.goal) {
        $out += "GOAL      : " + $anchor.goal
    }
    if ($anchor.PSObject.Properties.Name -contains "artifact" -and $anchor.artifact) {
        $out += "ARTIFACT  : " + $anchor.artifact
    }
    if ($anchor.PSObject.Properties.Name -contains "audience" -and $anchor.audience) {
        $out += "AUDIENCE  : " + $anchor.audience
    }
    if ($anchor.PSObject.Properties.Name -contains "acceptance" -and @($anchor.acceptance).Count) {
        $out += "ACCEPTANCE:"
        foreach ($c in @($anchor.acceptance)) { $out += "  - " + $c }
    }
    if ($anchor.PSObject.Properties.Name -contains "constraints" -and @($anchor.constraints).Count) {
        $out += "CONSTRAINTS (the path does not run here):"
        foreach ($c in @($anchor.constraints)) { $out += "  - " + $c }
    }
    if ($anchor.PSObject.Properties.Name -contains "out_of_scope" -and @($anchor.out_of_scope).Count) {
        $out += "OUT OF SCOPE:"
        foreach ($c in @($anchor.out_of_scope)) { $out += "  - " + $c }
    }
    if ($anchor.PSObject.Properties.Name -contains "findings_sink" -and $anchor.findings_sink) {
        $out += "FINDINGS  : anything true but out of scope goes to " + $anchor.findings_sink
    }
    return ($out -join "`n")
}
