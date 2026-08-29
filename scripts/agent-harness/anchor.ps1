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

$script:AnchorFields = [ordered]@{
    goal          = @{ required = $true;  kind = "string"; why = "one sentence, the outcome in the operator's words - what stops goal drift across test cycles" }
    artifact      = @{ required = $true;  kind = "string"; why = "the path and kind of the thing that will exist - 'a README' vs 'a report' is a decision, not a style" }
    audience      = @{ required = $true;  kind = "string"; why = "who reads or runs it - the field that would have prevented the coder README" }
    acceptance    = @{ required = $true;  kind = "list";   why = "objectively checkable criteria - this is what the tester tests against" }
    out_of_scope  = @{ required = $true;  kind = "list";   why = "explicit non-goals - where 'and also fix what you find' gets refused" }
    findings_sink = @{ required = $false; kind = "string"; why = "where incidental discoveries go, so real findings are neither lost nor pasted into the deliverable. Its contents are held to the SAME standard as the artifact - a claim about what a script does is verified against that code path, not against its comments" }
}

function Get-AnchorFieldHelp {
    $lines = @()
    foreach ($k in $script:AnchorFields.Keys) {
        $f = $script:AnchorFields[$k]
        $req = if ($f.required) { "required" } else { "optional" }
        $lines += ("  {0,-14} {1,-8} {2}" -f $k, "($req)", $f.why)
    }
    return ($lines -join "`n")
}

function Test-Anchor($anchor) {
    # Returns a list of problems, empty when the anchor is usable. Deliberately returns
    # rather than throws: the caller decides whether a bad anchor is fatal (queue.ps1) or
    # merely reported (a linting pass, an agent checking its own draft before proposing).
    $problems = @()
    if ($null -eq $anchor) { return @("the anchor is empty") }
    foreach ($k in $script:AnchorFields.Keys) {
        $spec = $script:AnchorFields[$k]
        $has = ($anchor.PSObject.Properties.Name -contains $k)
        $val = if ($has) { $anchor.$k } else { $null }
        if ($spec.kind -eq "list") {
            $items = @($val | Where-Object { $_ -ne $null -and "$_".Trim() })
            if ($spec.required -and $items.Count -lt 1) {
                $problems += ("'{0}' must list at least one entry - {1}" -f $k, $spec.why)
            }
        } else {
            if ($spec.required -and (-not $val -or -not "$val".Trim())) {
                $problems += ("'{0}' is required - {1}" -f $k, $spec.why)
            }
        }
    }
    # An acceptance criterion nobody can check is a wish. This catches the common shape -
    # a single vague line - without pretending to judge English.
    if ($anchor.PSObject.Properties.Name -contains "acceptance") {
        foreach ($c in @($anchor.acceptance)) {
            if ("$c".Trim().Length -lt 12) {
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
        throw ("the anchor in '$path' is not usable:`n  - " + ($problems -join "`n  - ") +
               "`n`nFields:`n" + (Get-AnchorFieldHelp))
    }
    return $anchor
}

function Format-Anchor($anchor) {
    # The rendering a tester and a reviewer read. Every role sees the SAME text - if the
    # tester and the reviewer are working from different summaries of the goal, the anchor
    # has failed at the one job it has.
    if ($null -eq $anchor) { return "(no anchor)" }
    $out = @()
    $out += "GOAL      : " + $anchor.goal
    $out += "ARTIFACT  : " + $anchor.artifact
    $out += "AUDIENCE  : " + $anchor.audience
    $out += "ACCEPTANCE:"
    foreach ($c in @($anchor.acceptance)) { $out += "  - " + $c }
    $out += "OUT OF SCOPE:"
    foreach ($c in @($anchor.out_of_scope)) { $out += "  - " + $c }
    if ($anchor.PSObject.Properties.Name -contains "findings_sink" -and $anchor.findings_sink) {
        $out += "FINDINGS  : anything true but out of scope goes to " + $anchor.findings_sink
    }
    return ($out -join "`n")
}
