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
#     an auto record that cannot state it is incomplete by definition;
#   - a gate the board REFUSED to auto-pass also writes a record. An unattended run that
#     halts must leave the halt in the trail, not a gap.
#
# COMPLETENESS is defined by Test-GateAuditComplete below and is executable, because
# "lands with a complete audit trail" is half of U6's validation column and the half that
# gets skipped. Crossed gates are derived from THE ITEM'S OWN STATE, never from the ledger
# - that is what makes a MISSING record detectable rather than invisible.

$script:GateLedgerSchema = 1

function Get-GateAuditDir {
    $dir = Join-Path (Get-SharedStateDir) ([string](Get-HarnessSetting "andon.ledger_dir_name" "audit"))
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return $dir
}

function Get-GateLedgerPath { return (Join-Path (Get-GateAuditDir) "gates.jsonl") }

function Invoke-AndonForGate {
    # Run the board as a child process. andon.ps1 owns a CLI and a param() block, so it is
    # invoked rather than dot-sourced; that also keeps the board a single artifact an
    # operator can run by hand and get the same verdict the gate got.
    #
    # A board that could not be RUN returns "unavailable", and callers treat that exactly
    # like "raised". A gate that opens because its check crashed is the skip-that-counts-
    # as-a-pass shape, and this file is not going to ship it.
    param([string[]]$RunBranches = @(), [string]$RepoRoot = "")
    $script = Join-Path $PSScriptRoot "andon.ps1"
    if (-not (Test-Path $script)) {
        return [ordered]@{ status = "unavailable"; conditions = 0; fired = @("andon.ps1 not found at $script") }
    }
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
    if (-not $text) {
        return [ordered]@{ status = "unavailable"; conditions = 0; fired = @("andon.ps1 produced no verdict (exit $code)") }
    }
    try { $v = ConvertFrom-Json $text }
    catch { return [ordered]@{ status = "unavailable"; conditions = 0; fired = @("andon verdict was not JSON (exit $code)") } }
    $fired = @($v.conditions | Where-Object { $_.action -eq "halt" } | ForEach-Object { "$($_.id): $($_.detail)" })
    $status = "clear"
    if ($v.board -ne "clear") { $status = "raised" }
    return [ordered]@{ status = $status; conditions = @($v.conditions).Count; fired = @($fired) }
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
    if (-not $Andon) { $Andon = [ordered]@{ status = "not-evaluated"; conditions = 0; fired = @() } }
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
    $crossed = @()
    $hasAnchor = ($item.PSObject.Properties.Name -contains "anchor_file" -and $item.anchor_file) -or
                 ($item.PSObject.Properties.Name -contains "anchor" -and $item.anchor)
    if ($hasAnchor -and $item.state -ne "anchor-draft") { $crossed += "anchor" }
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
    if ($r.kind -eq "auto") { $tag = "  <- NO HUMAN SAW THIS (profile '$($r.gate_profile)', andon $($r.andon.status))" }
    return ("{0}  {1,-12} {2,-10} {3,-6} {4}{5}" -f
            ([DateTimeOffset]::FromUnixTimeSeconds([int64]$r.ts).ToString("yyyy-MM-dd HH:mm:ss")),
            $r.item, $r.gate, $r.decision, $who, $tag)
}
