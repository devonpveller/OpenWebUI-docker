# andon.ps1 - THE ANDON BOARD. Stop the line when a named condition is true.
#
# WHY THIS EXISTS, and why the conditions are the ones they are (U6, 2026-08-30):
# an unattended run has nobody watching it. The 2026-08-30 run of this very plan produced
# a written record of what actually went wrong in it (DECISIONS.md, documentation/notes/),
# and every condition shipped in harness.config.json comes from that record with its
# incident named. No taxonomy was invented. Per PLAN.md section 0 A6, a condition whose
# "detection" is prose is FALSIFIED, not implemented - so a condition exists here ONLY if
# a predicate below can decide it by reading a file or running a command.
#
# THE SPLIT: harness.config.json declares WHICH conditions are on, with what parameters
# and what happens when they fire. This file holds the PREDICATES. A condition naming a
# predicate that does not exist is REFUSED (see Invoke-Predicate) - so the config cannot
# declare a detector nobody implemented, which is the exact shape of the dead
# `human_gates` declaration this work replaced.
#
# INDETERMINATE IS NOT A PASS - and this sentence was FALSE at run time until 2026-08-30,
# which is worth saying in the file that said it. Every predicate may answer ok / fire /
# indeterminate, and `on_indeterminate` defaults to halt. But the verdict was computed by
# exception, so `on_indeterminate: warn` on ONE condition made a condition that COULD NOT BE
# EVALUATED print `ANDON BOARD: CLEAR` at exit 0, auto-pass the dark gate signed `auto:dark`,
# and leave the ledger reading `status=clear fired=[] halted=[]` with the unevaluated
# condition absent from the record entirely. It is not the default that makes indeterminate
# a non-pass; it is the CENSUS (config.ps1 $script:AndonBuckets), under which `clear` requires
# the indeterminate bucket to be EMPTY whatever any per-condition action says.
#
# WHAT "HALT" MEANS HERE. This tool does not kill processes. It returns a verdict, and the
# pipeline gate is the thing that obeys it: queue.ps1 REFUSES to auto-pass a gate while the
# board is anything but clear (exit 6), leaving the item parked in its pre-gate state with the raise
# written to the ledger. An attended gate is unaffected - a human passing a gate is the
# human deciding, which is what attended means.
#
# THE BOARD MUST BE THE WHOLE BOARD. Switching it off was closed two ways, and they do NOT
# report the same state - this comment said they both reported `not-evaluated` until
# 2026-08-30, and the same false sentence sat in config.ps1 and config.py, put there by the
# commit that made it false. The mapping is stated ONCE, in README.md's ways-off table, and
# cited here by route id so a test can check it:
#   andon-disabled       -> not-evaluated   (`andon.enabled: false`; nothing was evaluated)
#   andon-block-deleted  -> incomplete      (no `andon` block at all, so all five REQUIRED
#                                            conditions are missing - which is a different
#                                            fact from "nothing was evaluated", and the
#                                            reason the two words differ)
# Both halt. A THIRD way was open until 2026-08-30 and it is the one anybody would actually
# reach for: DELETE CONDITION ENTRIES from `andon.conditions` (route `conditions-deleted`). Thinned to one of five on a genuinely detached checkout the
# gate AUTO-PASSED, exit 0, ledger `clear`, `-VerifyAudit COMPLETE`. So the ids the system
# requires are declared in CODE (config.ps1 `$script:RequiredAndonConditions`), not in the
# config that would otherwise be agreeing with itself, and a board missing any of them is
# `incomplete`: its own state, named ids, exit 6, no auto-pass.
#
# THE FIFTH WAY OFF, and the general one - closed 2026-08-30 by making `clear` PROVEN. The
# fourth (`on_fire`, below) was fixed by adding a `fired` list and a `warned` board, and the
# IDENTICAL hole stayed open on the sibling key: `on_indeterminate: warn` on
# `protected-ref-moved` with no baseline - the state this file's own README calls
# "deliberately not a pass" - printed CLEAR at exit 0, auto-passed the dark gate, verified
# COMPLETE, and put nothing about the unevaluated condition in the ledger. Three rounds
# running, a fix left its sibling. The root cause was never the key: `$raised` was set only
# for `action -eq halt` and `fired` only for `status -eq fire`, EVERY OTHER OUTCOME SET
# NOTHING, and `clear` was what you got when nothing objected. So the verdict is no longer
# computed by exception. Every result lands in exactly one counted bucket, the buckets must
# sum to the conditions in scope, and `clear` requires every bucket but `evaluated_ok` to be
# empty - so an outcome nobody enumerated (a new status, a new action word) lands in
# `unrecognised` and REFUSES, with no branch naming it. Drill step K proves that by
# introducing outcome words this file has never heard of.
#
# A FOURTH WAY OFF WAS OPEN UNTIL 2026-08-30, and it did not need the board switched off at
# all: set `on_fire` to anything but `halt` on ONE condition. `$raised` was `action -eq halt`
# and the ledger's `fired` list was derived the same way, so the condition FIRED, the board
# reported `clear`, the dark gate auto-passed at exit 0 signed `auto:dark`, and the record
# read `status=clear evaluated=5 missing=0 fired=[]` and verified COMPLETE. The fire was in
# the console listing of `-Evaluate` and NOWHERE in the ledger - which is the surface an
# operator audits afterwards, and the whole point of the clause. So: `fired` now means the
# detectors SAW something and `halted` means the line stopped, they are separate lists in
# every verdict and every record, and a board with a fire on it is never `clear`. The policy
# that answers is deliberate and is argued in config.ps1 beside $script:AllowedAndonActions:
# `warn` buys the WORD and the record, never the pass.
#
# WHAT IS STILL CONFIG-CONTROLLED, said plainly because the sentence this replaced said "no
# route through the config opens the gates" and that was false: the SET of conditions is
# pinned in code, and so is the vocabulary of `on_fire`. What each condition DOES is not. Its
# `predicate` and its `params` come from the config, so an entry that keeps a required id
# while naming a different predicate, or one whose `params.repo` points at a clean decoy
# checkout, still satisfies every check the board makes at run time. test_gate_profiles.py
# pins the id -> predicate map of the COMMITTED config; nothing pins an uncommitted one, or
# one named by AI_STACK_HARNESS_CONFIG.
#
# WHERE THE RAISE GOES: the audit ledger (gate-audit.ps1, an append-only JSONL beside the
# queue) and stderr. The LEDGER write is unconditional and is deliberately NOT a knob - a run
# that could switch off the record of its own halt is the failure this board exists to
# prevent - so `andon.raise` configures the stderr copy only. It held a second key,
# `andon.raise.ledger`, until 2026-08-30: nothing read it, which is exactly what this file's
# own `policy-declared-unread` condition refuses, and that condition now covers the `andon`
# block as well as `pipeline` so it can catch the next one here.
#
#   .\andon.ps1 -Evaluate                  # all enabled conditions; exit 6 unless the board is clear
#   .\andon.ps1 -Evaluate -Json            # machine-readable verdict on stdout
#   .\andon.ps1 -Evaluate -Only <id>       # one condition
#   .\andon.ps1 -List                      # what is declared, and what implements it
#   .\andon.ps1 -Baseline                  # record the protected refs this run starts from
#
# Exit codes: 0 board CLEAR | 1 usage/config error | 2 harness disabled |
#             6 board not clear. The words, and each is a bucket the census found non-empty:
#               raised        a condition HALTED the line (its action was `halt`)
#               warned        a condition FIRED whose `on_fire` is not `halt`
#               indeterminate a condition COULD NOT BE EVALUATED and its `on_indeterminate`
#                             is not `halt` - the sibling of `warned`, and a pass until
#                             2026-08-30
#               unaccounted   an outcome the board does not enumerate (an unknown status or
#                             action word), or a census that does not balance
#               incomplete    a REQUIRED condition is not declared at all
#               partial       some conditions were evaluated and others are switched off
#               not-evaluated nothing was evaluated (andon off, or every condition off)
#             All seven refuse an unattended pass; only `clear` authorises one, and `clear`
#             is decided positively by the census, not by the absence of a flag.

[CmdletBinding()]
param(
    [switch]$Evaluate,
    [switch]$List,
    [switch]$Baseline,
    [switch]$Json,
    [string]$Only = "",
    [string]$RepoRoot = "",
    [string[]]$RunBranch = @()
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "common.ps1")

function Die([string]$msg, [int]$code = 1) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit $code
}

$offReason = Get-HarnessDisabledReason
if ($offReason) { Write-Host "REFUSED: $offReason" -ForegroundColor Yellow; exit 2 }

# --- where we are ------------------------------------------------------------------
# REFUSE an empty repo path rather than defaulting to the current directory.
#
# THE BEHAVIOUR IS SHELL-SPECIFIC, and the first version of this comment was not. It said
# `git -C ""` "silently runs wherever you happen to be and exits 0". That was verified IN
# BASH. In POWERSHELL - this file's own language and the only way this code path is ever
# reached - `git -C '' rev-parse --show-toplevel` exits 128 with "fatal: cannot change to
# 'rev-parse': No such file or directory", both directly and splatted (re-run 2026-08-30).
# The empty argument is dropped from the argv, so `rev-parse` lands in -C's slot: LOUD here,
# silent there. The refusal below stands on its own merits - a repo path that resolved to
# "wherever the process happens to be" is unusable to a board that has to say WHICH checkout
# it looked at, and $ctx.repo_root is written into every gate record for exactly that reason.
# The drill incident this came from is real; the sentence that generalised it was not.
function Resolve-RepoRoot([string]$explicit) {
    if ($explicit) {
        if (-not (Test-Path $explicit)) { throw "repo path '$explicit' does not exist" }
        return (Resolve-Path $explicit).Path
    }
    $top = Invoke-GitCapture @("rev-parse", "--show-toplevel") | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or -not $top) { throw "not inside a git repository, and no -RepoRoot given" }
    return $top.Trim()
}

function New-Result([string]$status, [string]$detail, $evidence) {
    if ($null -eq $evidence) { $evidence = @() }
    return [ordered]@{ status = $status; detail = $detail; evidence = @($evidence) }
}

function Get-Param($cond, [string]$name, $default) {
    if (-not $cond.Contains("params")) { return $default }
    $p = $cond["params"]
    if (-not ($p -is [System.Collections.IDictionary]) -or -not $p.Contains($name)) { return $default }
    $v = $p[$name]
    if ($null -eq $v) { return $default }
    return $v
}

function Format-Sha([string]$s) {
    if (-not $s) { return "(absent)" }
    if ($s.Length -le 9) { return $s }
    return $s.Substring(0, 9)
}

# ===================================================================================
# PREDICATES. One function per predicate name; the name -> function map below is the
# ONLY place a config id becomes code.
# ===================================================================================

function Predicate-GitCheckoutState($cond, $ctx) {
    # INCIDENT: the main checkout was found detached, mid-rebase, rebasing the live work
    # line, its process dead 8 minutes (drill-rebased-the-work-line-incident.md). Cheap to
    # detect and catastrophic to miss - which is the whole argument for an andon board.
    $repo = [string](Get-Param $cond "repo" "")
    if (-not $repo) { $repo = Get-MainCheckout }
    if (-not $repo) { return (New-Result "indeterminate" "cannot locate the main checkout" @()) }
    if (-not (Test-Path $repo)) { return (New-Result "indeterminate" "main checkout '$repo' does not exist" @()) }

    $gitDirOut = Invoke-GitCapture @("-C", $repo, "rev-parse", "--path-format=absolute", "--git-dir")
    if ($LASTEXITCODE -ne 0 -or -not $gitDirOut) {
        return (New-Result "indeterminate" "git could not describe '$repo'" @())
    }
    $gitDir = ($gitDirOut | Select-Object -First 1).Trim()

    $found = @()
    $head = Invoke-GitCapture @("-C", $repo, "rev-parse", "--abbrev-ref", "HEAD") | Select-Object -First 1
    if ($LASTEXITCODE -ne 0 -or -not $head) {
        return (New-Result "indeterminate" "git could not read HEAD in '$repo'" @())
    }
    if ($head.Trim() -eq "HEAD") { $found += "HEAD is DETACHED in $repo" }

    # An interrupted sequencer leaves its state on disk. Any of these means the checkout is
    # mid-operation, which is what "not on its branch" means in practice.
    $markers = [ordered]@{
        "rebase-merge"     = "a rebase is in progress"
        "rebase-apply"     = "a rebase/am is in progress"
        "MERGE_HEAD"       = "a merge is in progress"
        "CHERRY_PICK_HEAD" = "a cherry-pick is in progress"
        "REVERT_HEAD"      = "a revert is in progress"
        "BISECT_LOG"       = "a bisect is in progress"
    }
    $staleSeconds = [int](Get-Param $cond "stale_seconds" 0)
    foreach ($m in $markers.Keys) {
        $path = Join-Path $gitDir $m
        if (-not (Test-Path $path)) { continue }
        $age = [int]((Get-Date) - (Get-Item $path).LastWriteTime).TotalSeconds
        if ($staleSeconds -gt 0 -and $age -lt $staleSeconds) { continue }
        $found += ("{0} in {1} ({2}, idle {3}s)" -f $markers[$m], $repo, $m, $age)
    }

    if ($found.Count -gt 0) {
        return (New-Result "fire" ("the main checkout is not cleanly on a branch: " + ($found -join "; ")) $found)
    }
    return (New-Result "ok" ("$repo is on '" + $head.Trim() + "' with no operation in progress") @())
}

function Remove-CommentsOnly([string]$text, [bool]$IsPython) {
    # Strip COMMENTS but KEEP string literals - the opposite of Remove-PsNoise below, and
    # deliberately so. A config key is read THROUGH a string (`Get-HarnessSetting
    # "pipeline.gate_profile"`), so blanking strings would erase every reader; while a key
    # named in a COMMENT is not a reader at all. The first version of the predicate below
    # scanned raw text and could not reproduce the human-gates incident, because this very
    # file discusses that key path in its own header.
    $tq = [char]34 + [char]34 + [char]34
    $sq = [char]39 + [char]39 + [char]39
    if ($IsPython) {
        $text = [regex]::Replace($text, '(?s)' + [regex]::Escape($tq) + '.*?' + [regex]::Escape($tq), "")
        $text = [regex]::Replace($text, '(?s)' + [regex]::Escape($sq) + '.*?' + [regex]::Escape($sq), "")
    } else {
        $text = [regex]::Replace($text, '(?s)<#.*?#>', "")
    }
    $out = New-Object System.Text.StringBuilder
    foreach ($line in ($text -split "`n")) {
        $inS = $false; $inD = $false
        foreach ($ch in $line.ToCharArray()) {
            if (-not $inS -and -not $inD) {
                if ($ch -eq '#') { break }
                if ($ch -eq '"') { $inD = $true }
                elseif ($ch -eq "'") { $inS = $true }
            } else {
                if ($inD -and $ch -eq '"') { $inD = $false }
                elseif ($inS -and $ch -eq "'") { $inS = $false }
            }
            [void]$out.Append($ch)
        }
        [void]$out.Append("`n")
    }
    return $out.ToString()
}

function Test-ConfigPathRead([string]$blob, [string]$path) {
    # ANCHORED, and that is the whole point. A plain substring test says "read" for any key
    # that happens to be a PREFIX of a live one: with `pipeline.gate_profile` in the sources,
    # `pipeline.gate_profil`, `pipeline.claim_ttl` and even `pipeline.a` all reported read
    # (reproduced 2026-08-30 - the detector printed "ok - all 4 policy keys under pipeline
    # are read" for four keys no line mentions). A dotted path must be followed and preceded
    # by something that is not part of a longer path, or the match is an accident.
    $rx = '(?<![\w.])' + [regex]::Escape($path) + '(?![\w.])'
    return [regex]::IsMatch($blob, $rx)
}

function Get-ConfigLeafPaths($node, [string]$prefix, $sink) {
    # Every LEAF path under a node. Derived FROM THE FILE, never from a hand-written list: a
    # completeness test whose enumeration is hand-written is a list with a spell-checker
    # (DECISIONS.md 2026-08-30, 'ENUMERATE-AND-PATCH LOSES').
    if (-not ($node -is [System.Collections.IDictionary])) { return }
    foreach ($k in $node.Keys) {
        if ($k -like "_*") { continue }
        $path = if ($prefix) { "$prefix.$k" } else { "$k" }
        if ($node[$k] -is [System.Collections.IDictionary]) { Get-ConfigLeafPaths $node[$k] $path $sink }
        else { [void]$sink.Add($path) }
    }
}

function Find-UnreadConfigPaths($node, [string]$prefix, [string]$blob, $sink, $counted) {
    # Walk the tree reporting the SHALLOWEST unread node. A container counts as read when its
    # own path is matched OR any leaf beneath it is: `andon.raise` is never written literally,
    # but `andon.raise.stderr` is, and the block is alive. `pipeline.human_gates` - the real
    # incident - had neither, so it is reported as the block it was, not as two leaves.
    if (-not ($node -is [System.Collections.IDictionary])) { return }
    foreach ($k in $node.Keys) {
        if ($k -like "_*") { continue }
        $path = if ($prefix) { "$prefix.$k" } else { "$k" }
        if (-not ($node[$k] -is [System.Collections.IDictionary])) {
            [void]$counted.Add($path)
            if (-not (Test-ConfigPathRead $blob $path)) { [void]$sink.Add($path) }
            continue
        }
        $leaves = New-Object System.Collections.ArrayList
        Get-ConfigLeafPaths $node[$k] $path $leaves
        $anyRead = Test-ConfigPathRead $blob $path
        if (-not $anyRead) {
            foreach ($lp in $leaves) { if (Test-ConfigPathRead $blob $lp) { $anyRead = $true; break } }
        }
        if ($anyRead) { Find-UnreadConfigPaths $node[$k] $path $blob $sink $counted }
        else { [void]$counted.Add($path); [void]$sink.Add($path) }
    }
}

function Predicate-ConfigKeyUnread($cond, $ctx) {
    # INCIDENT: `pipeline.human_gates` and `runners.*.status` were both declared policy that
    # NO executable line read (verified 2026-08-30), and `Resolve-RoleTarget` had zero
    # executable callers. A knob that governs nothing still reads as governance, and a
    # reader who finds it assumes the behaviour exists.
    #
    # SCOPE, stated so nobody reads more into a green than it means: this walks the KEY
    # IDENTIFIERS under the configured roots and asks whether each appears in any harness
    # source outside the two defaults mirrors. It is an identifier scan, not a data-flow
    # analysis: it proves a key is MENTIONED, not that it is honoured. Roots default to
    # scalar policy knobs only - named data collections (profiles, gate_profiles,
    # andon.conditions) are consumed generically by a loop, so an identifier scan would say
    # nothing true about them.
    $roots = @(Get-Param $cond "roots" @("pipeline"))
    if ($roots.Count -eq 0) { return (New-Result "indeterminate" "no roots configured" @()) }
    # Exclusions are GLOB patterns, and they are about what counts as a READER. A test or
    # drill that names a key is exercising it, not consuming it - a knob whose only mention
    # is in its own test still governs nothing. Found the hard way: the first run of the
    # drill could not reproduce the incident, because the drill file itself says the word.
    # config.ps1 and config.py are NOT excluded: they hold the accessors, and the
    # dotted-path match below already ignores their defaults blocks, which declare keys as
    # bare identifiers rather than as paths.
    $exclude = @(Get-Param $cond "exclude_sources" @("test_*", "drill-*"))

    $cfgPath = if ($env:AI_STACK_HARNESS_CONFIG) { $env:AI_STACK_HARNESS_CONFIG }
               else { Join-Path $PSScriptRoot "harness.config.json" }
    if (-not (Test-Path $cfgPath)) { return (New-Result "indeterminate" "config file '$cfgPath' not found" @()) }
    $raw = Get-Content -Raw -Path $cfgPath
    try { $cfg = ConvertTo-HashtableDeep (ConvertFrom-Json $raw) }
    catch { return (New-Result "indeterminate" "config '$cfgPath' is not valid JSON" @()) }

    foreach ($r in $roots) {
        if (-not $cfg.Contains($r)) {
            return (New-Result "indeterminate" "configured root '$r' is not present in $cfgPath" @())
        }
    }

    $sources = @(Get-ChildItem -Path $PSScriptRoot -File | Where-Object {
        if ($_.Extension -ne ".ps1" -and $_.Extension -ne ".py") { return $false }
        foreach ($pat in $exclude) { if ($_.Name -like $pat) { return $false } }
        return $true
    })
    if ($sources.Count -eq 0) { return (New-Result "indeterminate" "no harness sources to scan" @()) }
    $blob = ($sources | ForEach-Object {
        Remove-CommentsOnly (Get-Content -Raw -Path $_.FullName) ($_.Extension -eq ".py")
    }) -join "`n"

    # Match the DOTTED PATH, not the bare key, and match it ANCHORED. `Get-HarnessSetting
    # "pipeline.gate_profile"` and `get("pipeline.gate_profile")` both contain it; a defaults
    # block, which declares the key as a bare identifier, does not - which is what lets the
    # accessors in config.ps1/config.py count as readers while the mirrors that merely declare
    # a key do not, without excluding those two files and losing their accessors with them.
    $orphans = New-Object System.Collections.ArrayList
    $counted = New-Object System.Collections.ArrayList
    foreach ($r in $roots) { Find-UnreadConfigPaths $cfg[$r] $r $blob $orphans $counted }
    if ($counted.Count -eq 0) { return (New-Result "indeterminate" "no keys found under roots: $($roots -join ', ')" @()) }
    if ($orphans.Count -gt 0) {
        return (New-Result "fire" ("declared policy nothing reads: " + (@($orphans) -join ", ")) @($orphans))
    }
    return (New-Result "ok" ("all " + $counted.Count + " policy keys under " + ($roots -join ", ") + " are read by harness sources") @())
}

function Remove-PsNoise($lines) {
    # Blank out comments and string LITERALS before scanning, preserving the line count so
    # reported line numbers stay true.
    #
    # WHY THIS IS NOT OPTIONAL: the first version of the predicate below scanned raw text.
    # It reported four functions - and all four were the word "git" inside a COMMENT
    # ("Thin policy wrapper over the git fact"), while the function the incident was
    # actually about, Invoke-DrillGit, was MISSED because it calls `git.exe` and the
    # pattern demanded `git` followed by whitespace. A detector that fires on nothing and
    # misses the thing it was built for is the very defect it is supposed to find, so this
    # is recorded rather than quietly corrected.
    #
    # LIMITATION, stated: here-strings (@" ... "@) are not tracked. A `git` inside one
    # would still be seen. This said "no scanned file uses one" until 2026-08-30 and that was
    # simply false - scripts/checks/test-quartz4-offline.ps1 is in the default glob and holds
    # EIGHT (lines 145, 185, 251, 267, 287, 327, 399, 415, all SQL passed to psql). None of
    # them contains the word git, which is why the limitation has produced no false positive
    # yet; that is luck about content, not a property of the scan. The failure direction is
    # still the safe one - a false POSITIVE, which is loud - but "no file uses one" was a
    # claim about the corpus that nobody had asked the corpus.
    $text = ($lines -join "`n")
    # Block comments first, replaced by the same number of newlines they occupied.
    $text = [regex]::Replace($text, '(?s)<#.*?#>', {
        param($m) ("`n" * ([regex]::Matches($m.Value, "`n").Count))
    })
    $out = @()
    foreach ($line in ($text -split "`n")) {
        $sb = New-Object System.Text.StringBuilder
        $inS = $false; $inD = $false
        foreach ($ch in $line.ToCharArray()) {
            if (-not $inS -and -not $inD) {
                if ($ch -eq '#') { break }
                if ($ch -eq '"') { $inD = $true; [void]$sb.Append(' '); continue }
                if ($ch -eq "'") { $inS = $true; [void]$sb.Append(' '); continue }
                [void]$sb.Append($ch)
            } else {
                if ($inD -and $ch -eq '"') { $inD = $false }
                elseif ($inS -and $ch -eq "'") { $inS = $false }
                [void]$sb.Append(' ')
            }
        }
        $out += $sb.ToString()
    }
    return $out
}

function Get-PsRegions($lines) {
    # Split a noise-stripped script into REGIONS: one per top-level function, plus a
    # "(top level)" region for everything outside them.
    #
    # The top-level region is not a nicety. Without it the scan sees only `function` bodies,
    # so a script whose git calls sit at file scope - `& git.exe push origin HEAD | Out-Null`
    # with no function anywhere - reported "ok: every git-calling function can report a
    # failure" (reproduced 2026-08-30), and a live in-glob instance,
    # scripts/checks/check-project-configs.ps1:18, went unflagged.
    #
    # DISCLOSED LIMITS: a function declared at column 0 opens a region; an INDENTED (nested)
    # function stays inside its enclosing region and is attributed to it - which is not
    # hypothetical: scripts/checks/smoke-agent-memory.ps1:131 declares `Invoke-Door` indented,
    # and it is the one such declaration in the two default globs (checked 2026-08-30). Its
    # call sites are judged against the enclosing region and reported with its label. Here-strings
    # (@" ... "@) are not tracked by the noise stripper, so a `git` inside one is still seen -
    # a false positive, which is loud, not a false negative.
    $regions = @()
    $topLines = @()
    $topIdx = @()
    $i = 0
    while ($i -lt $lines.Count) {
        if ($lines[$i] -match '^function\s+([A-Za-z0-9_\-]+)') {
            $name = $Matches[1]
            $depth = 0; $opened = $false; $j = $i
            while ($j -lt $lines.Count) {
                foreach ($ch in $lines[$j].ToCharArray()) {
                    if ($ch -eq '{') { $depth++; $opened = $true }
                    elseif ($ch -eq '}') { $depth-- }
                }
                if ($opened -and $depth -le 0) { break }
                $j++
            }
            if ($j -ge $lines.Count) { $j = $lines.Count - 1 }
            $regions += @{ label = ($name + "()"); start = $i
                           lines = @($lines[$i..$j]); idx = @($i..$j) }
            $i = $j + 1
            continue
        }
        $topLines += $lines[$i]
        $topIdx += $i
        $i++
    }
    if ($topLines.Count -gt 0) {
        $regions += @{ label = "(top level)"; start = $topIdx[0]; lines = @($topLines); idx = @($topIdx) }
    }
    return @($regions)
}

function Predicate-GitErrorUnchecked($cond, $ctx) {
    # INCIDENT: `Invoke-DrillGit` in verify-merge-protocol.ps1 sets $ErrorActionPreference to
    # Continue for its whole body and pipes git to Out-Null - no exit-code check, no stderr.
    # A check built on a function that cannot see a git failure cannot fail. Ten guards that
    # could not fail were found across this effort; this is the mechanically decidable shape
    # of that class, not the whole class (see the findings note for what it does not cover).
    #
    # THE UNIT IS THE CALL SITE, not the function body. The first version asked whether a
    # function body mentioned $LASTEXITCODE (or throw / Die / Write-Error / exit N) ANYWHERE,
    # which handed a body-wide amnesty to every git call in it: a guard clause `if (-not
    # $Branch) { throw }` at the TOP of a function cleared a swallowed `git push` five lines
    # below it (reproduced 2026-08-30). A check that is not near its call is not its check, so
    # each call site is asked about separately, within `check_window_lines` lines after it.
    #
    # WHAT THE WINDOW COSTS, measured rather than assumed: on this repository the call-site
    # rule reports 18 sites across the two default globs at a window of 5 or 8, versus 2
    # functions before. A sample of them was read line by line and each was a genuine
    # unchecked call - `git ls-files` in validate-lineendings.ps1 whose failure prints
    # "SUCCESS: No tracked shell scripts", `git diff --cached` in check-staged-secrets.ps1
    # whose failure means "nothing staged - skip". One, check-hook-attestation.ps1's
    # Invoke-AttestGit, is an adapter of the same shape as git-io.ps1; `exclude_files` is the
    # knob for that, and it is deliberately NOT applied here because that file, unlike
    # git-io.ps1, does not state the callers-check contract.
    $globs = @(Get-Param $cond "globs" @("scripts/checks/*.ps1", "scripts/agent-harness/*.ps1"))
    $excludeFiles = @(Get-Param $cond "exclude_files" @())
    $window = [int](Get-Param $cond "check_window_lines" 5)
    if ($window -lt 1) { return (New-Result "indeterminate" "check_window_lines must be >= 1, got $window" @()) }
    $root = [string]$ctx.repo_root
    $files = @()
    foreach ($g in $globs) {
        $files += @(Get-ChildItem -Path (Join-Path $root $g) -File -ErrorAction SilentlyContinue)
    }
    $files = @($files | Where-Object { $excludeFiles -notcontains $_.Name } | Sort-Object FullName -Unique)
    if ($files.Count -eq 0) { return (New-Result "indeterminate" "no files matched: $($globs -join ', ')" @()) }

    # `git`, `git.exe` (the drill calls the .exe deliberately - see its own comment about a
    # helper named Git shadowing the binary), or the harness git adapter.
    $callsGit = '(?m)(^|[^\w\-.])git(\.exe)?(\s|$)'
    $canFail = '(\$LASTEXITCODE)|((^|\W)throw(\W|$))|((^|\W)Die(\W|$))|(Write-Error)|((^|\W)exit\s+[1-9])'

    $offenders = @()
    $sites = 0
    foreach ($f in $files) {
        $raw = @(Get-Content -Path $f.FullName)
        if ($raw.Count -eq 0) { continue }
        $lines = @(Remove-PsNoise $raw)
        # git reports the toplevel with forward slashes and Get-ChildItem with backslashes,
        # so normalise BOTH before comparing - otherwise the prefix never matches and every
        # path is reported absolute.
        $rel = ($f.FullName -replace '\\', '/')
        $rootFwd = ($root -replace '\\', '/')
        if ($rel.StartsWith($rootFwd)) { $rel = $rel.Substring($rootFwd.Length).TrimStart('/') }

        foreach ($region in (Get-PsRegions $lines)) {
            $rl = @($region.lines)
            for ($k = 0; $k -lt $rl.Count; $k++) {
                $line = $rl[$k]
                if (-not (($line -match $callsGit) -or ($line -match 'Invoke-GitCapture'))) { continue }
                $sites++
                $to = [Math]::Min($rl.Count - 1, $k + $window)
                $after = ($rl[$k..$to]) -join "`n"
                if ($after -match $canFail) { continue }
                $lineNo = ([int]@($region.idx)[$k]) + 1
                $offenders += ("{0}:{1} in {2} runs git and does not check the result within {3} line(s)" -f `
                               $rel, $lineNo, $region.label, $window)
            }
        }
    }
    if ($offenders.Count -gt 0) {
        return (New-Result "fire" ("git errors are swallowed at " + $offenders.Count + " call site(s)") $offenders)
    }
    return (New-Result "ok" ("scanned " + $files.Count + " file(s) and " + $sites + " git call site(s); each checks its result") @())
}

function Predicate-BranchOnRemote($cond, $ctx) {
    # INCIDENT: eleven work/* branches reached origin under an authorisation the
    # orchestrator invented and the operator never gave (DECISIONS.md 2026-08-30 #3).
    # CLAUDE.md: never push on the operator's behalf unless explicitly asked.
    #
    # SCOPE: -RunBranch (or params.branches) narrows this to the branches THIS RUN owns,
    # which is what an unattended run should assert about itself. With neither, it asks the
    # broader question - is any local work branch on a remote - and that broader question
    # is RED in this repository today, because eleven of them still are.
    #
    # BOTH GATES NOW ASK THE NARROW ONE. Until 2026-08-30 the ANCHOR gate did not: the item
    # carries no branch until -Submit stores it, and -Submit stores it after the gate, so the
    # gate passed no -RunBranch and got the broad reading - a dark run refused for eleven
    # branches it neither pushed nor may delete. queue.ps1 Invoke-AutoGate carries the branch
    # in now. The broad reading is still what a bare `andon.ps1 -Evaluate` asks, deliberately.
    $prefix = [string](Get-HarnessSetting "worktree.branch_prefix" "work/")
    $branches = @(Get-Param $cond "branches" @())
    $named = ($branches.Count -gt 0)
    if ($ctx.run_branches -and @($ctx.run_branches).Count -gt 0) { $branches = @($ctx.run_branches); $named = $true }
    $repo = [string]$ctx.repo_root

    if (-not $named) {
        $local = Invoke-GitCapture @("-C", $repo, "for-each-ref", "--format=%(refname:short)", ("refs/heads/" + $prefix + "*"))
        if ($LASTEXITCODE -ne 0) { return (New-Result "indeterminate" "git could not list local branches in '$repo'" @()) }
        $branches = @($local | Where-Object { $_ })
        if ($branches.Count -eq 0) { return (New-Result "ok" "no local work branches to check" @()) }
    } else {
        # A NAMED BRANCH THAT DOES NOT EXIST IS NOT A CLEAN BRANCH - it is a question that was
        # never asked. The narrow reading is only as good as the name it is handed, and the
        # anchor gate hands it `-Submit -Branch` BEFORE git has been asked whether that branch
        # resolves (queue.ps1 rev-parses it further down). Without this, `-Branch work/typo`
        # would have produced "checked 1 branch(es); none is on a remote" - a clean board for a
        # branch nobody has, which is exactly the skip-counts-as-a-pass shape this board
        # refuses everywhere else. Named-but-missing is INDETERMINATE, which halts by default.
        $missing = @()
        foreach ($b in $branches) {
            $name = ([string]$b).Trim()
            if (-not $name) { continue }
            Invoke-GitCapture @("-C", $repo, "show-ref", "--verify", "--quiet", ("refs/heads/" + $name)) | Out-Null
            if ($LASTEXITCODE -ne 0) { $missing += $name }
        }
        if ($missing.Count -gt 0) {
            return (New-Result "indeterminate" ("named branch(es) not present in '" + $repo + "': " + ($missing -join ", ")) $missing)
        }
    }
    if ($branches.Count -eq 0) { return (New-Result "ok" "no local work branches to check" @()) }

    $remoteRefs = Invoke-GitCapture @("-C", $repo, "for-each-ref", "--format=%(refname)", "refs/remotes/")
    if ($LASTEXITCODE -ne 0) { return (New-Result "indeterminate" "git could not list remote-tracking refs in '$repo'" @()) }
    $remoteNames = @()
    foreach ($r in $remoteRefs) {
        if (-not $r) { continue }
        $parts = ($r.Trim()) -split "/", 4
        if ($parts.Count -lt 4) { continue }
        $remoteNames += , @($parts[2], $parts[3], $r.Trim())
    }

    $hits = @()
    foreach ($b in $branches) {
        $name = ([string]$b).Trim()
        if (-not $name) { continue }
        foreach ($rn in $remoteNames) {
            if ($rn[1] -eq $name) { $hits += ("{0} is on remote '{1}' ({2})" -f $name, $rn[0], $rn[2]) }
        }
    }
    if ($hits.Count -gt 0) {
        return (New-Result "fire" ("work branches exist on a remote: " + $hits.Count) $hits)
    }
    return (New-Result "ok" ("checked " + $branches.Count + " branch(es); none is on a remote") @())
}

function Predicate-ProtectedRefMoved($cond, $ctx) {
    # PLAN.md section C.2 class 4: merging or promoting anything to `main` is the operator's.
    # A run therefore records where the protected refs stood when it began, and this asks
    # whether any of them moved under it. NO BASELINE = INDETERMINATE, never a pass: without
    # one there is nothing to compare, and calling that clear is the skip-counts-as-a-pass
    # failure this board exists to refuse.
    $refs = @(Get-Param $cond "refs" @("main"))
    if ($refs.Count -eq 0) { return (New-Result "indeterminate" "no protected refs configured" @()) }
    $path = Get-AndonBaselinePath
    if (-not (Test-Path $path)) {
        return (New-Result "indeterminate" ("no baseline recorded - run: andon.ps1 -Baseline (expected at " + $path + ")") @())
    }
    $base = ConvertTo-HashtableDeep (ConvertFrom-Json (Get-Content -Raw -Path $path))
    if (-not $base -or -not $base.Contains("refs")) { return (New-Result "indeterminate" "baseline file has no 'refs'" @()) }
    $baseRefs = $base["refs"]

    $moved = @()
    foreach ($r in $refs) {
        if (-not $baseRefs.Contains($r)) {
            return (New-Result "indeterminate" ("ref '$r' is not in the baseline - re-record it") @())
        }
        $now = Invoke-GitCapture @("-C", [string]$ctx.repo_root, "rev-parse", "--verify", "--quiet", $r) | Select-Object -First 1
        $nowSha = ""
        if ($LASTEXITCODE -eq 0 -and $now) { $nowSha = $now.Trim() }
        $wasSha = [string]$baseRefs[$r]
        if ($nowSha -ne $wasSha) {
            $moved += ("{0}: {1} -> {2}" -f $r, (Format-Sha $wasSha), (Format-Sha $nowSha))
        }
    }
    if ($moved.Count -gt 0) {
        return (New-Result "fire" ("protected ref moved under this run: " + ($moved -join "; ")) $moved)
    }
    return (New-Result "ok" ("all " + $refs.Count + " protected ref(s) unchanged since the baseline") @())
}

$script:Predicates = [ordered]@{
    "git-checkout-state"  = ${function:Predicate-GitCheckoutState}
    "config-key-unread"   = ${function:Predicate-ConfigKeyUnread}
    "git-error-unchecked" = ${function:Predicate-GitErrorUnchecked}
    "branch-on-remote"    = ${function:Predicate-BranchOnRemote}
    "protected-ref-moved" = ${function:Predicate-ProtectedRefMoved}
}

function Get-AndonDir {
    $dir = Join-Path (Get-SharedStateDir) ([string](Get-HarnessSetting "andon.ledger_dir_name" "audit"))
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }
    return $dir
}
function Get-AndonBaselinePath { return (Join-Path (Get-AndonDir) "andon-baseline.json") }

function Get-AndonConditions {
    $conds = Get-HarnessSetting "andon.conditions"
    if (-not $conds) { return @() }
    return @($conds)
}

function Invoke-Predicate($cond, $ctx) {
    $name = [string]$cond["predicate"]
    if (-not $name) { throw "andon condition '$($cond['id'])' declares no predicate" }
    if (-not $script:Predicates.Contains($name)) {
        # REFUSE, loudly. A config that can name a detector nobody wrote is a config that
        # can declare safety it does not have - the exact failure the board is built around.
        throw ("andon condition '{0}' names predicate '{1}', which is not implemented in andon.ps1. Known predicates: {2}" -f `
               $cond["id"], $name, ($script:Predicates.Keys -join ", "))
    }
    $fn = $script:Predicates[$name]
    return (& $fn $cond $ctx)
}

function Get-CondParams($cond) {
    # The condition's declared params, as a plain map, for the record. Empty rather than
    # $null when there are none: "this condition declared no params" and "nobody recorded
    # what it declared" are different facts, and the second one is the audit hole.
    #
    # `_`-PREFIXED KEYS ARE DROPPED. The shipped config carries several `_..._note` keys -
    # paragraphs of prose explaining a param to the next reader - and they are documentation,
    # not behaviour. Carrying them would put kilobytes of unchanging prose on every ledger
    # line and bury the one field an auditor is looking for. Nothing reads them at run time;
    # if that ever changes, this filter is the thing to revisit.
    if (-not $cond.Contains("params") -or -not $cond["params"]) { return [ordered]@{} }
    $out = [ordered]@{}
    foreach ($k in @($cond["params"].Keys)) {
        if ([string]$k -like "_*") { continue }
        $out[[string]$k] = $cond["params"][$k]
    }
    return $out
}

function Invoke-AndonEvaluation {
    # THE BOARD'S OWN HONESTY CLAUSE. A verdict must distinguish EVALUATED-OK from
    # NOT-EVALUATED, because they are not the same fact and only one of them authorises an
    # unattended pass.
    #
    # THE DEFECT THIS REPLACES, reproduced 2026-08-30 on a genuinely DETACHED checkout: with
    # `andon.enabled: false` the verdict read `board=clear, conditions=5` and exited 0 -
    # indistinguishable from five conditions that had actually looked and found nothing.
    # Deleting the `andon` block gave `board=clear, conditions=0` and exit 0, so the
    # documented revert path was a SILENT KILL SWITCH: under `dark` it did not restore prior
    # behaviour, it removed the only thing between an unattended run and self-approval.
    #
    # So `clear` now means something narrow and checkable, and it is decided by the CENSUS
    # below: every condition lands in exactly one bucket, the buckets sum to the conditions in
    # scope, every bucket but `evaluated_ok` is empty, and at least one condition is in it.
    # Everything else gets its own name and is refused by the gate. THIS LIST IS THE WHOLE
    # ALPHABET, in severity order - it omitted `indeterminate` and `unaccounted`, the two words
    # the census added, until 2026-08-30, which is the same defect one file over that
    # queue.ps1's exit-code list had (`warned` missing). A partial list of the words that
    # REFUSE reads as though the missing ones do not, so
    # test_gate_profiles.py::test_every_enumeration_of_board_words_in_the_repo_is_complete now
    # finds every such list in the repository and compares it against the words the code can
    # actually assign:
    #   unaccounted   - an outcome the board does not enumerate (an unknown status or an
    #                   unknown action word), or a census that does not balance. Outranks
    #                   everything: a board that cannot say where its own results went cannot
    #                   report any board's verdict
    #   incomplete    - a condition the system REQUIRES is not declared at all
    #   raised        - a condition halted the line: it fired with on_fire=halt, or it could
    #                   not be evaluated (on_indeterminate=halt)
    #   warned        - a condition FIRED and its on_fire is not `halt`. Still not a clear
    #                   board - see the on_fire paragraph in this file's header
    #   indeterminate - a condition COULD NOT BE EVALUATED and its on_indeterminate is not
    #                   `halt`. The sibling of `warned`, and a silent pass until 2026-08-30
    #   partial       - some conditions were evaluated ok, others are switched off in config
    #   not-evaluated - nothing was evaluated at all (andon off, or every condition switched off)
    # A disabled condition is not an ok one. It is the operator saying "do not look", which is
    # a decision they are entitled to make - attended. It is not a clear board.
    #
    # THE THIRD WAY OFF, closed 2026-08-30 and the reason `incomplete` exists. Deleting
    # condition ENTRIES was neither of the two cases above: thinned to one of five on a
    # detached checkout the verdict read `clear, 1 declared, 1 evaluated, 0 switched off`,
    # exit 0, and the dark gate auto-passed. Nothing in the record was false - and that is
    # the point, because the board was answering a question about a board that is not the
    # one the system requires. `declared` counts what the CONFIG holds; it can only be
    # trusted against a list the config cannot edit, so the required ids live in config.ps1
    # and MISSING ones are named here. `incomplete` outranks every other non-clear word: a
    # verdict from a board that is not the board cannot be reported as that board's verdict,
    # and the conditions that survive are still listed and still raise on stderr, so no fired
    # condition is hidden by the rename.
    param([string]$OnlyId = "", [string]$Repo = "", [string[]]$RunBranches = @())
    $ctx = @{ repo_root = (Resolve-RepoRoot $Repo); run_branches = @($RunBranches) }
    $enabled = [bool](Get-HarnessSetting "andon.enabled" $true)
    $allConditions = @(Get-AndonConditions)
    # Asked of what the CONFIG DECLARES, never of what this invocation evaluated: `-Only`
    # narrows a run to one condition on purpose, and a run narrowed by the operator is not a
    # board with conditions missing. Otherwise `andon.ps1 -Evaluate -Only <id>` - the
    # documented single-condition form - would report the other four as deleted. `declared`
    # and `evaluated` below stay RUN-scoped (what this invocation had in scope); `required`,
    # `missing` and `missing_ids` are always config-wide.
    $declaredIds = @($allConditions | ForEach-Object { [string]$_["id"] } | Where-Object { $_ })
    $missingIds = @((Get-RequiredAndonConditionIds) | Where-Object { $declaredIds -notcontains $_ })
    # AN ACTION THE BOARD CANNOT READ IS NOT AN ACTION. Checked for every DECLARED condition
    # before anything is evaluated, including switched-off ones: a config carrying a word
    # this file does not implement has already decided something nobody wrote down, and
    # which way it would have fallen is not something to discover at the moment it fires.
    # Throwing exits 1 with no JSON, which Invoke-AndonForGate reads as `unavailable` and
    # every gate treats as "not clear".
    $allowed = @(Get-AllowedAndonActions)
    foreach ($c in $allConditions) {
        foreach ($key in @("on_fire", "on_indeterminate")) {
            if (-not $c.Contains($key)) { continue }
            $v = [string]$c[$key]
            if ($allowed -notcontains $v) {
                throw ("andon condition '{0}' declares {1}='{2}', which is not an action this board implements. Allowed: {3}" -f `
                       $c["id"], $key, $v, ($allowed -join ", "))
            }
        }
    }
    $results = @()
    $firedIds = @()
    foreach ($c in $allConditions) {
        $id = [string]$c["id"]
        if ($OnlyId -and $id -ne $OnlyId) { continue }
        $condOff = ($c.Contains("enabled") -and -not $c["enabled"])
        if ($condOff -or -not $enabled) {
            $why = if ($condOff) { "disabled in config" } else { "andon.enabled=false" }
            $results += [ordered]@{ id = $id; status = "disabled"; action = "none"; detail = $why
                                    evidence = @(); predicate = [string]$c["predicate"]; incident = ""
                                    params = (Get-CondParams $c) }
            continue
        }
        $r = Invoke-Predicate $c $ctx
        $action = "none"
        if ($r.status -eq "fire") {
            $action = "halt"
            if ($c.Contains("on_fire")) { $action = [string]$c["on_fire"] }
        } elseif ($r.status -eq "indeterminate") {
            $action = "halt"
            if ($c.Contains("on_indeterminate")) { $action = [string]$c["on_indeterminate"] }
        }
        # A FIRE IS RECORDED AS A FIRE, whatever its action. Tracked separately from what
        # halted because they answered the same question until 2026-08-30 and they are not
        # the same question: `fired` is what the detectors SAW, `halted` is what the config
        # did about it. Deriving one from the other made a fire with on_fire other than
        # `halt` disappear - board `clear`, ledger `fired=[]`, dark gate auto-passed at
        # exit 0. Neither list decides the verdict any more; the census below does.
        if ($r.status -eq "fire") { $firedIds += $id }
        $incident = ""
        if ($c.Contains("incident")) { $incident = [string]$c["incident"] }
        $results += [ordered]@{
            id = $id; status = $r.status; action = $action; detail = $r.detail
            evidence = @($r.evidence); predicate = [string]$c["predicate"]; incident = $incident
            # WHERE IT LOOKED, carried in the verdict so the redirect is visible afterwards.
            # `params.repo` on a condition points its predicate at whatever checkout it
            # names, and the verdict's own `repo` is the BOARD's checkout, not the
            # condition's - so a decoy `params.repo` left the board reporting a path the
            # detector never looked at. README.md claimed the redirect was "visible
            # afterwards" on the strength of that field; it was not, until this line.
            params = (Get-CondParams $c)
        }
    }
    if ($OnlyId -and $results.Count -eq 0) { throw "no andon condition with id '$OnlyId'" }

    # THE CENSUS - the thing that makes `clear` proven rather than defaulted. Every result is
    # classified through config.ps1's $script:AndonBuckets into EXACTLY ONE bucket and stamped
    # with it, so the verdict, the console and the ledger all name the same fact. The table
    # lives in config.ps1 beside the required-condition set for the same reason that one does:
    # gate-audit.ps1 has to read the same declaration to re-derive this verdict from a record.
    $census = [ordered]@{}
    $censusIds = [ordered]@{}
    foreach ($b in (Get-AndonBucketNames)) { $census[$b] = 0; $censusIds[$b] = @() }
    # Results whose bucket is not a DECLARED bucket at all. Unreachable while every unknown
    # pair falls to `unrecognised`, and kept because the census's whole claim is that nothing
    # escapes it: a future classifier that returns a name nobody declared must be caught by
    # the count, not by somebody noticing.
    $unaccounted = @()
    foreach ($r in $results) {
        $b = Get-AndonBucket ([string]$r["status"]) ([string]$r["action"])
        $r["bucket"] = $b
        if (-not $census.Contains($b)) {
            $unaccounted += ("{0}: status '{1}' + action '{2}' classified as '{3}', which is not a declared census bucket" -f `
                             $r["id"], $r["status"], $r["action"], $b)
            continue
        }
        $census[$b] = [int]$census[$b] + 1
        $censusIds[$b] = @($censusIds[$b]) + @(("{0}: {1}" -f $r["id"], $r["detail"]))
    }
    $censusTotal = 0
    foreach ($k in $census.Keys) { $censusTotal += [int]$census[$k] }
    $censusBalances = ($censusTotal -eq @($results).Count)

    $disabledIds = @($results | Where-Object { $_.status -eq "disabled" } | ForEach-Object { $_.id })
    $evaluated = @($results | Where-Object { $_.status -ne "disabled" }).Count
    $haltedIds = @($results | Where-Object { $_.action -eq "halt" } | ForEach-Object { $_.id })
    $unrecognisedIds = @($results | Where-Object { $_.bucket -eq $script:AndonUnrecognisedBucket } |
                         ForEach-Object { "{0} (status '{1}', action '{2}')" -f $_.id, $_.status, $_.action })
    $coverage = [ordered]@{
        declared     = @($results).Count
        evaluated    = $evaluated
        disabled     = @($disabledIds).Count
        disabled_ids = @($disabledIds)
        required     = @(Get-RequiredAndonConditionIds).Count
        missing      = @($missingIds).Count
        missing_ids  = @($missingIds)
        # BOTH LISTS TRAVEL. `fired` is every condition whose predicate said fire, halting or
        # not; `halted` is every condition whose action was halt, which includes the
        # indeterminate ones. Neither is derivable from the other, and the record has to be
        # able to answer "what did the detectors see?" as well as "what stopped the line?".
        fired        = @($firedIds).Count
        fired_ids    = @($firedIds)
        halted       = @($haltedIds).Count
        halted_ids   = @($haltedIds)
        # THE CENSUS TRAVELS TOO, so gate-audit.ps1 can RE-DERIVE the verdict from the
        # buckets instead of trusting the word `clear`. A record that carries only a status
        # is a record whose only oracle is itself.
        census       = $census
        census_total = $censusTotal
        census_ids   = $censusIds
        unaccounted  = @($unaccounted)
        unrecognised_ids = @($unrecognisedIds)
    }

    # `CLEAR` STATED POSITIVELY, which is the whole correction. Not "nothing set the halt
    # flag" - that is how an outcome nobody enumerated became a pass, twice. Every one of
    # these has to hold:
    #   the census balances                 - no condition landed outside a counted bucket
    #   nothing landed outside the buckets  - the census's own escape hatch is empty
    #   no REQUIRED condition is missing    - the board is the whole board
    #   every bucket but evaluated_ok is 0  - including buckets added after this was written
    #   at least one condition evaluated ok - a board that looked at nothing certifies nothing
    $nonClearBuckets = @()
    foreach ($k in $census.Keys) {
        if ($k -eq $script:AndonClearBucket) { continue }
        if ([int]$census[$k] -gt 0) { $nonClearBuckets += $k }
    }
    $isClear = ($censusBalances -and (@($unaccounted).Count -eq 0) -and (@($missingIds).Count -eq 0) -and
                (@($nonClearBuckets).Count -eq 0) -and ([int]$census[$script:AndonClearBucket] -ge 1))

    $board = ""
    $why = ""
    if ($isClear) {
        $board = "clear"
    } elseif ((-not $censusBalances) -or (@($unaccounted).Count -gt 0)) {
        # THE ACCOUNTING ITSELF IS BROKEN, so no verdict about the line can be trusted. This
        # outranks everything, including `incomplete`: a board that cannot say where its own
        # results went cannot report any board's verdict.
        $board = "unaccounted"
        $reasons = @($unaccounted)
        if (-not $censusBalances) {
            $reasons += ("the census counted {0} outcome(s) for {1} condition(s) in scope - something landed in no bucket" -f `
                         $censusTotal, @($results).Count)
        }
        $why = ($reasons -join "; ")
    } elseif (@($missingIds).Count -gt 0) {
        $board = "incomplete"
        $why = ("{0} of {1} REQUIRED condition(s) are not declared in the config: {2}" -f
                @($missingIds).Count, @(Get-RequiredAndonConditionIds).Count, (@($missingIds) -join ", "))
    } elseif (@($haltedIds).Count -gt 0) {
        $board = "raised"
        $why = ("{0} condition(s) HALTED the line: {1}" -f @($haltedIds).Count, (@($haltedIds) -join ", "))
    } else {
        # EVERY REMAINING REASON IS A NON-EMPTY BUCKET, and its word comes from the bucket
        # map in severity order - so a bucket added to that map later gets a word, refuses,
        # and needs no branch here. The one special case is spelled out rather than implied:
        # a board whose ONLY non-empty bucket is `disabled` evaluated nothing, and
        # "everything is switched off" is `not-evaluated`, not `partial` (partial means some
        # conditions did look and others were switched off).
        foreach ($k in $script:AndonBucketBoard.Keys) {
            if ($k -eq $script:AndonClearBucket) { continue }
            if ([int]$census[$k] -lt 1) { continue }
            if ($k -eq "disabled" -and [int]$census[$script:AndonClearBucket] -lt 1) { break }
            $board = [string]$script:AndonBucketBoard[$k]
            $why = ("{0} condition(s) landed in the '{1}' bucket - that is not a clear board, and no unattended gate passes it: {2}" -f `
                    [int]$census[$k], $k, (@($censusIds[$k]) -join "; "))
            break
        }
        if (-not $board) {
            $board = "not-evaluated"
            # The "no conditions declared at all" case cannot land here while the required
            # set is non-empty - an empty board is `incomplete` above, naming all five. The
            # branch stays so that emptying the required set in code (a deliberate act,
            # visible in a diff) still produces a true sentence rather than a confident
            # wrong one.
            $why = if (@($results).Count -eq 0) {
                       "no andon conditions are declared - the board cannot certify anything"
                   } elseif (-not $enabled) {
                       "andon.enabled=false - " + @($results).Count + " condition(s) declared, none evaluated"
                   } else {
                       "every declared condition is disabled in config - none evaluated"
                   }
        }
    }
    return [ordered]@{
        evaluated_at = [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        repo         = $ctx.repo_root
        board        = $board
        why          = $why
        coverage     = $coverage
        conditions   = @($results)
    }
}

# --- CLI ---------------------------------------------------------------------------
if ($List) {
    Write-Host "ANDON CONDITIONS (harness.config.json -> andon.conditions)" -ForegroundColor Cyan
    Write-Host ("andon.enabled = {0}" -f (Get-HarnessSetting "andon.enabled" $true))
    foreach ($c in (Get-AndonConditions)) {
        $impl = "MISSING"
        if ($script:Predicates.Contains([string]$c["predicate"])) { $impl = "implemented" }
        Write-Host ""
        Write-Host ("  {0}" -f $c["id"]) -ForegroundColor Yellow
        Write-Host ("    detects   : {0}" -f $c["detects"])
        Write-Host ("    predicate : {0} ({1})" -f $c["predicate"], $impl)
        Write-Host ("    on_fire   : {0} | on_indeterminate: {1}" -f $c["on_fire"], $c["on_indeterminate"])
        if ($c.Contains("incident")) { Write-Host ("    incident  : {0}" -f $c["incident"]) }
    }
    exit 0
}

if ($Baseline) {
    $repo = Resolve-RepoRoot $RepoRoot
    $refs = [ordered]@{}
    foreach ($c in (Get-AndonConditions)) {
        if ([string]$c["predicate"] -ne "protected-ref-moved") { continue }
        foreach ($r in @(Get-Param $c "refs" @("main"))) {
            $sha = Invoke-GitCapture @("-C", $repo, "rev-parse", "--verify", "--quiet", $r) | Select-Object -First 1
            $val = ""
            if ($LASTEXITCODE -eq 0 -and $sha) { $val = $sha.Trim() }
            $refs["$r"] = $val
        }
    }
    if ($refs.Count -eq 0) { Die "no protected-ref-moved condition is declared - nothing to baseline" }
    $out = [ordered]@{
        recorded_at = [int64][System.DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        repo        = $repo
        refs        = $refs
    }
    $path = Get-AndonBaselinePath
    $out | ConvertTo-Json -Depth 6 | Set-Content -Path $path -Encoding ASCII
    Write-Host ("Andon baseline recorded at {0}" -f $path) -ForegroundColor Green
    foreach ($k in $refs.Keys) { Write-Host ("  {0} = {1}" -f $k, (Format-Sha $refs[$k])) }
    exit 0
}

if ($Evaluate) {
    try { $verdict = Invoke-AndonEvaluation -OnlyId $Only -Repo $RepoRoot -RunBranches $RunBranch }
    catch { Die $_.Exception.Message }

    if ($Json) {
        $verdict | ConvertTo-Json -Depth 8 -Compress
    } else {
        $boardColour = if ($verdict.board -eq "clear") { "Green" } else { "Red" }
        Write-Host ("ANDON BOARD: {0}" -f $verdict.board.ToUpper()) -ForegroundColor $boardColour
        if ($verdict.why) { Write-Host ("  " + $verdict.why) -ForegroundColor Yellow }
        foreach ($r in $verdict.conditions) {
            $colour = "Green"
            if ($r.bucket -ne $script:AndonClearBucket) { $colour = "Yellow" }
            if ($r.status -eq "fire" -or $r.bucket -eq $script:AndonUnrecognisedBucket) { $colour = "Red" }
            if ($r.status -eq "disabled") { $colour = "DarkGray" }
            # THE BUCKET IS PRINTED BESIDE THE STATUS. `[indeterminate] protected-ref-moved`
            # under a green CLEAR headline is exactly what this board printed on 2026-08-30,
            # and a reader had no way to tell from the line whether it counted. Now it says
            # which bucket it landed in, and the headline is derived from those buckets.
            Write-Host ("  [{0,-13}] {1,-30} {2,-14} {3}" -f $r.status, $r.id, ("-> " + $r.bucket), $r.detail) -ForegroundColor $colour
            foreach ($e in $r.evidence) { Write-Host ("      - {0}" -f $e) -ForegroundColor DarkGray }
        }
        Write-Host ("  coverage: {0} declared, {1} evaluated, {2} switched off, {3} of {4} required MISSING" -f
                    $verdict.coverage.declared, $verdict.coverage.evaluated, $verdict.coverage.disabled,
                    $verdict.coverage.missing, $verdict.coverage.required) -ForegroundColor DarkGray
        # THE CENSUS, PRINTED. `clear` is the state where every bucket but evaluated_ok is
        # zero, so the operator should be able to read that claim off the console rather
        # than take the headline word for it.
        $censusParts = @()
        foreach ($k in $verdict.coverage.census.Keys) { $censusParts += ("{0}={1}" -f $k, [int]$verdict.coverage.census[$k]) }
        Write-Host ("  census  : {0} (total {1} of {2} in scope)" -f ($censusParts -join ", "),
                    $verdict.coverage.census_total, $verdict.coverage.declared) -ForegroundColor DarkGray
        if (@($verdict.coverage.unrecognised_ids).Count -gt 0) {
            Write-Host ("  UNRECOGNISED outcome(s): {0}" -f (@($verdict.coverage.unrecognised_ids) -join ", ")) -ForegroundColor Red
        }
        if (@($verdict.coverage.unaccounted).Count -gt 0) {
            Write-Host ("  UNACCOUNTED: {0}" -f (@($verdict.coverage.unaccounted) -join "; ")) -ForegroundColor Red
        }
        if ([int]$verdict.coverage.missing -gt 0) {
            # Named, not counted. "4 missing" sends an operator to the config to guess; the
            # ids send them to the four lines that are gone.
            Write-Host ("  MISSING required condition(s): {0}" -f (@($verdict.coverage.missing_ids) -join ", ")) -ForegroundColor Red
        }
    }
    # ANYTHING BUT `clear` EXITS 6, and there is no softer green among the other seven. A
    # board that did not look, or that is not the board the system requires, cannot say the
    # line is clear, and exiting 0 there is precisely the skip-counts-as-a-pass shape this
    # file refuses everywhere else. The seven are listed once, in the exit-code block at the
    # top of this file; naming a few of them here is how the same list came to disagree with
    # itself in four places (see Invoke-AndonEvaluation's own list, which was one of them).
    if ($verdict.board -ne "clear") {
        # The raise ALWAYS goes to the gate ledger (queue.ps1 writes a decision=refused
        # record) - that is not a knob, because a run able to switch off the record of its own
        # halt is the failure this board exists to prevent. Only the stderr copy is optional.
        if ([bool](Get-HarnessSetting "andon.raise.stderr" $true)) {
            # HALTED AND FIRED BOTH, de-duplicated. Taking only `action -eq halt` meant a
            # `warned` board reached stderr with nothing but its board word.
            # EVERY NON-CLEAR BUCKET, not only fired/halted. Taking those two meant an
            # `indeterminate` or `unrecognised` board reached stderr with nothing but its
            # board word - the same omission, one bucket over.
            $fired = @(@($verdict.coverage.halted_ids) + @($verdict.coverage.fired_ids))
            foreach ($k in $verdict.coverage.census.Keys) {
                if ($k -eq $script:AndonClearBucket) { continue }
                if ([int]$verdict.coverage.census[$k] -lt 1) { continue }
                $fired += @($verdict.coverage.census_ids[$k])
            }
            $fired = @($fired | Where-Object { $_ } | Select-Object -Unique)
            if (@($fired).Count -eq 0 -and $verdict.why) { $fired = @($verdict.why) }
            [Console]::Error.WriteLine(("ANDON " + $verdict.board.ToUpper() + ": ") + (@($fired) -join ", "))
        }
        exit 6
    }
    exit 0
}

Die "pass one of -Evaluate | -List | -Baseline"
