# gate-audit.ps1 - the gate audit ledger, and the definition of a COMPLETE audit trail.
#
# A pure library: no param() block and nothing executes on dot-source, because queue.ps1
# dot-sources it the same way it dot-sources anchor.ps1. The operator entry points are
# `queue.ps1 -Audit` and `queue.ps1 -VerifyAudit`.
#
# THE FAILURE MODE THIS IS DESIGNED AGAINST (U6, 2026-08-30): an audit record that says a
# gate "passed" without saying who or what passed it. That is WORSE than no record,
# because a reader takes it for human approval. So:
#
#   - every gate pass writes a record naming a PRINCIPAL and a KIND ("human" or "auto");
#   - an auto-pass principal must live in the reserved `auto:` namespace, which queue.ps1
#     refuses to accept from a human -By value, in either direction;
#   - an auto-pass must carry the gate profile that authorised it AND the andon verdict at
#     that moment - "the board was clear when nobody was looking" is the whole claim, and
#     an auto record that cannot state it is incomplete by definition. The verdict includes
#     its COVERAGE (declared / evaluated / switched off / required MISSING), because the first version of this
#     file recorded `andon.status=clear conditions=5` for a board with `andon.enabled=false`
#     that had evaluated NOTHING - a record indistinguishable from five conditions that
#     looked and found nothing. "Clear" now requires evaluated >= 1, none switched off, and
#     no REQUIRED condition missing - the last because a board thinned by DELETING condition
#     entries satisfied both of the others (1 evaluated of 1 declared, none off) while four
#     detectors were gone - and Test-GateAuditComplete re-checks all three from the record
#     rather than trusting the word;
#   - a gate the board REFUSED to auto-pass also writes a record. An unattended run that
#     halts must leave the halt in the trail, not a gap.
#
# COMPLETENESS is defined by Test-GateAuditComplete below and is executable, because
# "lands with a complete audit trail" is half of U6's validation column and the half that
# gets skipped. Crossed gates are derived from THE ITEM'S OWN STATE, never from the ledger
# - that is what makes a MISSING record detectable rather than invisible.

# Schema 2 (2026-08-30) added andon.repo and the andon coverage counters. A record that
# predates it cannot state whether the board actually looked, and Test-GateAuditComplete says
# so rather than assuming it did. Schema 3 (2026-08-30, same day) added `missing` /
# `missing_ids`: the counters answered "how many of the DECLARED conditions were evaluated"
# and a board thinned by deleting condition entries answered that perfectly - 1 of 1, none
# switched off - while four detectors were gone. A record has to be able to say whether the
# board was the WHOLE board. Schema 4 (2026-08-30, same day) split `fired` from `halted`:
# `fired` was DERIVED from `action -eq halt`, so a condition that fired with `on_fire`
# set to anything else was absent from the record entirely - `status=clear ... fired=[]`
# beside a detector that had just fired. `fired` now means the detectors saw something and
# `halted` means the line stopped. A schema-3 record cannot tell the two apart, and
# Test-GateAuditComplete reports that rather than assuming they were the same.
$script:GateLedgerSchema = 4

function Get-GateAuditDir {
    $dir = Join-Path (Get-SharedStateDir) ([string](Get-HarnessSetting "andon.ledger_dir_name" "audit"))
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return $dir
}

function Get-GateLedgerPath { return (Join-Path (Get-GateAuditDir) "gates.jsonl") }

function New-UnavailableAndon([string]$reason) {
    # A board that could not be RUN. Every counter is zero and the status is its own word, so
    # nothing downstream can read it as a clear board.
    return [ordered]@{ status = "unavailable"; repo = ""; conditions = 0; evaluated = 0
                       disabled = 0; disabled_ids = @(); required = @(Get-RequiredAndonConditionIds).Count
                       missing = @(Get-RequiredAndonConditionIds).Count
                       missing_ids = @(Get-RequiredAndonConditionIds); fired = @($reason)
                       halted = @($reason) }
}

function Invoke-AndonForGate {
    # Run the board as a child process. andon.ps1 owns a CLI and a param() block, so it is
    # invoked rather than dot-sourced; that also keeps the board a single artifact an
    # operator can run by hand and get the same verdict the gate got.
    #
    # A board that could not be RUN returns "unavailable", and callers treat that exactly
    # like "raised". A gate that opens because its check crashed is the skip-that-counts-
    # as-a-pass shape, and this file is not going to ship it.
    #
    # THE COVERAGE COUNTERS ARE THE POINT of this function's return value. `status` alone was
    # what the first version returned, and `clear` covered three different worlds: five
    # conditions evaluated ok, five switched off by `andon.enabled=false`, and no conditions
    # declared at all. The record has to be able to tell them apart afterwards, so all of
    # declared / evaluated / disabled travel with it - and `repo` too, so an auditor can see
    # WHICH checkout the board was looking at.
    param([string[]]$RunBranches = @(), [string]$RepoRoot = "")
    $script = Join-Path $PSScriptRoot "andon.ps1"
    if (-not (Test-Path $script)) { return (New-UnavailableAndon "andon.ps1 not found at $script") }
    $exe = Join-Path $PSHOME "powershell.exe"
    if (-not (Test-Path $exe)) { $exe = "powershell" }
    $argv = @("-NoProfile", "-NonInteractive", "-File", $script, "-Evaluate", "-Json")
    if ($RepoRoot) { $argv += @("-RepoRoot", $RepoRoot) }
    foreach ($b in $RunBranches) { if ($b) { $argv += @("-RunBranch", $b) } }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { $out = & $exe @argv 2>$null } finally { $ErrorActionPreference = $prev }
    $code = $LASTEXITCODE
    $text = ($out | Where-Object { $_ }) -join ""
    if (-not $text) { return (New-UnavailableAndon "andon.ps1 produced no verdict (exit $code)") }
    try { $v = ConvertFrom-Json $text }
    catch { return (New-UnavailableAndon "andon verdict was not JSON (exit $code)") }
    if (-not ($v.PSObject.Properties.Name -contains "coverage")) {
        # An andon.ps1 too old to report coverage cannot answer the question this record has
        # to answer. Unavailable, not clear.
        return (New-UnavailableAndon "andon verdict carries no coverage - it cannot say whether anything was evaluated")
    }
    if (-not ($v.coverage.PSObject.Properties.Name -contains "missing")) {
        # Same rule one level down. A coverage block that counts only the DECLARED conditions
        # cannot distinguish a whole board from a thinned one, and that was the exact gap:
        # 1 declared / 1 evaluated / 0 switched off read as a clean sweep.
        return (New-UnavailableAndon "andon coverage does not state MISSING required conditions - it cannot say whether the board was the whole board")
    }
    # TWO LISTS, AND NEITHER IS DERIVED FROM THE OTHER. `fired` was `action -eq halt` until
    # 2026-08-30, which made the word "fired" mean "halted": a condition whose `on_fire` was
    # not `halt` fired, and the record said `status=clear ... fired=[]` - the detector's
    # finding was in no audit surface at all. A reader of this ledger has to be able to ask
    # "what did the board SEE" separately from "what stopped the line".
    $fired  = @($v.conditions | Where-Object { $_.status -eq "fire" } | ForEach-Object { "$($_.id): $($_.detail)" })
    $halted = @($v.conditions | Where-Object { $_.action -eq "halt" } | ForEach-Object { "$($_.id): $($_.detail)" })
    $status = "$($v.board)"
    if (-not $status) { return (New-UnavailableAndon "andon verdict names no board state") }
    # A board that is not clear but named nothing still has to say WHY, or the halt reaches
    # the operator as a blank refusal. Asked of BOTH lists: an indeterminate condition halts
    # without firing, and a `warned` board fires without halting.
    if ($status -ne "clear" -and @($fired).Count -eq 0 -and @($halted).Count -eq 0) {
        $why = "$($v.why)"
        if (-not $why) { $why = "the board is '$status' and named no reason" }
        $halted = @($why)
    }
    return [ordered]@{
        status       = $status
        repo         = "$($v.repo)"
        conditions   = [int]$v.coverage.declared
        evaluated    = [int]$v.coverage.evaluated
        disabled     = [int]$v.coverage.disabled
        disabled_ids = @($v.coverage.disabled_ids)
        required     = [int]$v.coverage.required
        missing      = [int]$v.coverage.missing
        missing_ids  = @($v.coverage.missing_ids)
        fired        = @($fired)
        halted       = @($halted)
    }
}

function Write-GateLedgerLine($record) {
    $path = Get-GateLedgerPath
    $line = ($record | ConvertTo-Json -Depth 8 -Compress)
    # Append-only, and ASCII so the two readers and every hook agree about the bytes.
    Add-Content -Path $path -Value $line -Encoding ASCII
    return $path
}

function Write-GateRecord {
    param(
        [Parameter(Mandatory = $true)][string]$Item,
        [Parameter(Mandatory = $true)][string]$Gate,
        [Parameter(Mandatory = $true)][ValidateSet("passed", "refused")][string]$Decision,
        [Parameter(Mandatory = $true)][ValidateSet("human", "auto")][string]$Kind,
        [Parameter(Mandatory = $true)][string]$Principal,
        [string]$GateProfile = "",
        [string]$FromState = "",
        [string]$ToState = "",
        $Andon = $null
    )
    # NO VERDICT IS NOT A CLEAR ONE. A caller that passes nothing gets a record that says so
    # in every field, which Test-GateAuditComplete then refuses for an auto-pass.
    if (-not $Andon) {
        $Andon = [ordered]@{ status = "not-evaluated"; repo = ""; conditions = 0; evaluated = 0
                             disabled = 0; disabled_ids = @(); required = @(Get-RequiredAndonConditionIds).Count
                             missing = @(Get-RequiredAndonConditionIds).Count
                             missing_ids = @(Get-RequiredAndonConditionIds)
                             fired = @("no andon verdict was supplied")
                             halted = @("no andon verdict was supplied") }
    }
    $rec = [ordered]@{
        schema       = $script:GateLedgerSchema
        ts           = [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        item         = $Item
        gate         = $Gate
        decision     = $Decision
        kind         = $Kind
        principal    = $Principal
        gate_profile = $GateProfile
        from_state   = $FromState
        to_state     = $ToState
        andon        = $Andon
        tool         = "queue.ps1"
    }
    [void](Write-GateLedgerLine $rec)
    return $rec
}

function Read-GateLedger {
    param([string]$Item = "")
    $path = Get-GateLedgerPath
    if (-not (Test-Path $path)) { return @() }
    $out = @()
    $n = 0
    foreach ($line in (Get-Content -Path $path)) {
        $n++
        if (-not $line.Trim()) { continue }
        try { $rec = ConvertFrom-Json $line }
        catch {
            # A line that will not parse is itself a finding, surfaced rather than dropped.
            $out += [pscustomobject]@{ _unparsable = $true; _line = $n; _raw = $line }
            continue
        }
        if ($Item -and $rec.item -ne $Item) { continue }
        $out += $rec
    }
    return @($out)
}

function Get-CrossedGates($item) {
    # Which gates this item has crossed, derived from the ITEM'S OWN STATE. Deliberately not
    # from the ledger, and deliberately not from the item's `gates` field: both are the
    # things being audited, and a check that reads its subject as its own oracle checks
    # nothing.
    #
    # THE ANCHOR GATE AND `pipeline.anchor_required`. This used to count the anchor gate as
    # crossed only when the item CARRIED an anchor, which meant an item that advanced with no
    # anchor at all crossed no anchor gate and could not be missing a record for it. With
    # `anchor_required=true` - the shipped default - an item past `anchor-draft` has crossed
    # the gate whether or not an anchor survives on it, and a missing anchor is itself the
    # finding. With `anchor_required=false` the anchor gate is genuinely not a gate for an
    # anchorless item, and -VerifyAudit says so in words rather than quietly counting a
    # narrower 'complete'.
    $crossed = @()
    $anchorRequired = [bool](Get-HarnessSetting "pipeline.anchor_required" $true)
    $hasAnchor = ($item.PSObject.Properties.Name -contains "anchor_file" -and $item.anchor_file) -or
                 ($item.PSObject.Properties.Name -contains "anchor" -and $item.anchor)
    if ($item.state -ne "anchor-draft" -and ($hasAnchor -or $anchorRequired)) { $crossed += "anchor" }
    if (@("ready-review", "reviewing", "merged") -contains $item.state) { $crossed += "pre_review" }
    return @($crossed)
}

function Test-GateAuditComplete {
    # THE DEFINITION OF COMPLETE. Returns @{ findings = @(...); audited = @(...); unaudited = @(...) }.
    #
    # An item with no `gates` field predates this mechanism. It is reported as UNAUDITED and
    # never as a pass - unless it was named explicitly, in which case "I cannot audit the
    # item you asked about" is a finding, not a shrug.
    param(
        [Parameter(Mandatory = $true)]$Items,
        [string[]]$OnlyItems = @()
    )
    $findings = @()
    $audited = @()
    $unaudited = @()
    $gates = @(Get-GateNames)
    $prefix = Get-AutoPrincipalPrefix

    $ledger = @(Read-GateLedger)
    foreach ($bad in ($ledger | Where-Object { $_.PSObject.Properties.Name -contains "_unparsable" })) {
        $findings += ("ledger line {0} is not valid JSON: {1}" -f $bad._line, $bad._raw)
    }
    $records = @($ledger | Where-Object { -not ($_.PSObject.Properties.Name -contains "_unparsable") })

    $knownIds = @($Items | ForEach-Object { $_.id })
    foreach ($r in $records) {
        if ($gates -notcontains $r.gate) {
            $findings += ("ledger record for '{0}' names gate '{1}', which is not a pipeline gate ({2})" -f $r.item, $r.gate, ($gates -join ", "))
        }
        if ($knownIds -notcontains $r.item) {
            $findings += ("ledger record names item '{0}', which has no queue item" -f $r.item)
        }
    }

    foreach ($item in $Items) {
        if ($OnlyItems.Count -gt 0 -and $OnlyItems -notcontains $item.id) { continue }
        $hasGates = ($item.PSObject.Properties.Name -contains "gates") -and $item.gates
        if (-not $hasGates) {
            if ($OnlyItems -contains $item.id) {
                $findings += ("'{0}' has no 'gates' record - it predates the gate ledger and cannot be audited" -f $item.id)
            } else {
                $unaudited += $item.id
            }
            continue
        }
        $audited += $item.id
        $mine = @($records | Where-Object { $_.item -eq $item.id })

        foreach ($g in (Get-CrossedGates $item)) {
            $passes = @($mine | Where-Object { $_.gate -eq $g -and $_.decision -eq "passed" })
            if ($passes.Count -eq 0) {
                $findings += ("'{0}' crossed the '{1}' gate but the ledger has no pass record for it" -f $item.id, $g)
                continue
            }
            $rec = $passes[-1]

            if (-not $rec.principal) {
                $findings += ("'{0}' gate '{1}': the pass record names no principal - a record that says only 'passed' reads as human approval" -f $item.id, $g)
            }
            if (@("human", "auto") -notcontains $rec.kind) {
                $findings += ("'{0}' gate '{1}': kind is '{2}', not 'human' or 'auto'" -f $item.id, $g, $rec.kind)
            }
            if ($rec.kind -eq "auto") {
                if (-not ("$($rec.principal)").StartsWith($prefix)) {
                    $findings += ("'{0}' gate '{1}': an auto-pass principal must live in the reserved '{2}' namespace, got '{3}'" -f $item.id, $g, $prefix, $rec.principal)
                }
                if (-not $rec.gate_profile) {
                    $findings += ("'{0}' gate '{1}': an auto-pass must name the gate profile that authorised it" -f $item.id, $g)
                }
                if ($rec.andon.status -ne "clear") {
                    $findings += ("'{0}' gate '{1}': auto-passed with andon status '{2}' - only 'clear' authorises an unattended pass" -f $item.id, $g, $rec.andon.status)
                }
                # AND THE WORD 'clear' IS RE-CHECKED AGAINST ITS OWN COUNTERS. A record may
                # say clear because the board looked and found nothing, or because nobody
                # looked; those were once the same bytes. A record that cannot state its
                # coverage is incomplete by the rule at the top of this file, and one whose
                # counters say nothing was evaluated - or that a condition was switched off -
                # is not an authorised unattended pass however it is labelled.
                $cov = $rec.andon
                $hasCov = ($cov -and ($cov.PSObject.Properties.Name -contains "evaluated") -and
                                     ($cov.PSObject.Properties.Name -contains "disabled"))
                if (-not $hasCov) {
                    $findings += ("'{0}' gate '{1}': the auto-pass record does not state the andon COVERAGE - it cannot distinguish a board that was clear from a board nobody looked at" -f $item.id, $g)
                } else {
                    if ([int]$cov.evaluated -lt 1) {
                        $findings += ("'{0}' gate '{1}': auto-passed on a board that evaluated {2} of {3} condition(s) - nothing was checked" -f $item.id, $g, [int]$cov.evaluated, [int]$cov.conditions)
                    }
                    if ([int]$cov.disabled -gt 0) {
                        $findings += ("'{0}' gate '{1}': auto-passed with {2} andon condition(s) switched off ({3}) - a board with a condition turned off is not a clear board" -f $item.id, $g, [int]$cov.disabled, (@($cov.disabled_ids) -join ", "))
                    }
                    # AND WHETHER THE BOARD WAS THE WHOLE BOARD. Every counter above is
                    # relative to what the config DECLARED, so a board thinned by deleting
                    # condition entries satisfied all of them: 1 evaluated of 1 declared,
                    # none switched off, status `clear`. The required set is CODE, not
                    # config, which is what makes this question answerable from the record.
                    $hasReq = ($cov.PSObject.Properties.Name -contains "missing")
                    if (-not $hasReq) {
                        $findings += ("'{0}' gate '{1}': the auto-pass record does not state whether the board declared every REQUIRED condition - a board thinned to one condition reports full coverage of itself" -f $item.id, $g)
                    } elseif ([int]$cov.missing -gt 0) {
                        $findings += ("'{0}' gate '{1}': auto-passed on a board missing {2} of {3} REQUIRED condition(s) ({4}) - those detectors were not switched off, they were not there" -f $item.id, $g, [int]$cov.missing, [int]$cov.required, (@($cov.missing_ids) -join ", "))
                    }
                    # AND WHETHER ANY DETECTOR FIRED AT ALL. Every check above is about
                    # whether the board LOOKED; this one is about what it SAW. Before schema
                    # 4 the record could not answer it: `fired` was derived from
                    # `action -eq halt`, so a condition that fired with `on_fire` set to
                    # anything else left `fired=[]` beside `status=clear`. A record that
                    # cannot separate the two says so, and one that admits a fire is a
                    # finding whatever its status word claims.
                    $hasHalted = ($cov.PSObject.Properties.Name -contains "halted")
                    if (-not $hasHalted) {
                        $findings += ("'{0}' gate '{1}': the auto-pass record predates the fired/halted split - its 'fired' list means 'halted', so it cannot say whether a condition fired without halting" -f $item.id, $g)
                    } elseif (@($cov.fired).Count -gt 0) {
                        $findings += ("'{0}' gate '{1}': auto-passed while {2} condition(s) FIRED ({3}) - a fired condition is not a clear board, whatever its on_fire says" -f $item.id, $g, @($cov.fired).Count, (@($cov.fired) -join "; "))
                    }
                }
            }
            if ($rec.kind -eq "human" -and ("$($rec.principal)").StartsWith($prefix)) {
                $findings += ("'{0}' gate '{1}': a human pass claims the reserved '{2}' principal namespace" -f $item.id, $g, $prefix)
            }

            # The item and the ledger are two independent writes of the same fact. If they
            # disagree, one of them was edited after the event.
            $onItem = $item.gates.$g
            if (-not $onItem -or -not $onItem.kind) {
                $findings += ("'{0}' gate '{1}': the item carries no gate record, but the ledger does" -f $item.id, $g)
            } elseif ($onItem.kind -ne $rec.kind) {
                $findings += ("'{0}' gate '{1}': the item says '{2}' and the ledger says '{3}'" -f $item.id, $g, $onItem.kind, $rec.kind)
            } elseif ("$($onItem.by)" -ne "$($rec.principal)") {
                $findings += ("'{0}' gate '{1}': the item says '{2}' passed it and the ledger says '{3}'" -f $item.id, $g, $onItem.by, $rec.principal)
            }
        }
    }
    return [ordered]@{ findings = @($findings); audited = @($audited); unaudited = @($unaudited) }
}

function Format-GateRecord($r) {
    $who = "$($r.principal)"
    $tag = ""
    if ($r.kind -eq "auto") {
        # The coverage is printed, not just the word. "andon clear" reads as five conditions
        # looking; "andon clear (0/5 evaluated)" reads as what it is.
        $cov = ""
        if ($r.andon -and ($r.andon.PSObject.Properties.Name -contains "evaluated")) {
            $cov = " {0}/{1} evaluated" -f [int]$r.andon.evaluated, [int]$r.andon.conditions
            if ([int]$r.andon.disabled -gt 0) { $cov += (", {0} switched off" -f [int]$r.andon.disabled) }
            # A FIRE IS PRINTED EVEN WHEN THE STATUS WORD IS SOFT. `andon clear` beside a
            # detector that fired is the shape this line exists to make unreadable.
            if ($r.andon.PSObject.Properties.Name -contains "halted") {
                if (@($r.andon.fired).Count -gt 0) { $cov += (", {0} FIRED" -f @($r.andon.fired).Count) }
            } else {
                $cov += ", fired/halted NOT SEPARATED"
            }
            if ($r.andon.PSObject.Properties.Name -contains "missing") {
                if ([int]$r.andon.missing -gt 0) { $cov += (", {0} REQUIRED MISSING: {1}" -f [int]$r.andon.missing, (@($r.andon.missing_ids) -join " ")) }
            } else {
                $cov += ", required set NOT STATED"
            }
        } else {
            $cov = " coverage NOT STATED"
        }
        $tag = "  <- NO HUMAN SAW THIS (profile '$($r.gate_profile)', andon $($r.andon.status);$cov)"
    }
    return ("{0}  {1,-12} {2,-10} {3,-6} {4}{5}" -f
            ([DateTimeOffset]::FromUnixTimeSeconds([int64]$r.ts).ToString("yyyy-MM-dd HH:mm:ss")),
            $r.item, $r.gate, $r.decision, $who, $tag)
}
