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
# INDETERMINATE IS NOT A PASS. Every predicate may answer ok / fire / indeterminate, and
# `on_indeterminate` defaults to halt. "The check could not run, so we continued" is one
# of the failure shapes the board is here to catch; it does not get to be the board's own
# behaviour.
#
# WHAT "HALT" MEANS HERE. This tool does not kill processes. It returns a verdict, and the
# pipeline gate is the thing that obeys it: queue.ps1 REFUSES to auto-pass a gate while the
# board is raised (exit 6), leaving the item parked in its pre-gate state with the raise
# written to the ledger. An attended gate is unaffected - a human passing a gate is the
# human deciding, which is what attended means.
#
# THE BOARD MUST BE THE WHOLE BOARD. Switching it off was closed two ways - `andon.enabled:
# false` and deleting the `andon` block both report `not-evaluated` and halt. A THIRD way was
# open until 2026-08-30 and it is the one anybody would actually reach for: DELETE CONDITION
# ENTRIES from `andon.conditions`. Thinned to one of five on a genuinely detached checkout the
# gate AUTO-PASSED, exit 0, ledger `clear`, `-VerifyAudit COMPLETE`. So the ids the system
# requires are declared in CODE (config.ps1 `$script:RequiredAndonConditions`), not in the
# config that would otherwise be agreeing with itself, and a board missing any of them is
# `incomplete`: its own state, named ids, exit 6, no auto-pass.
#
# WHERE THE RAISE GOES: the audit ledger (gate-audit.ps1, an append-only JSONL beside the
# queue) and stderr. The LEDGER write is unconditional and is deliberately NOT a knob - a run
# that could switch off the record of its own halt is the failure this board exists to
# prevent - so `andon.raise` configures the stderr copy only. It held a second key,
# `andon.raise.ledger`, until 2026-08-30: nothing read it, which is exactly what this file's
# own `policy-declared-unread` condition refuses, and that condition now covers the `andon`
# block as well as `pipeline` so it can catch the next one here.
#
#   .\andon.ps1 -Evaluate                  # all enabled conditions; exit 6 if the board is raised
#   .\andon.ps1 -Evaluate -Json            # machine-readable verdict on stdout
#   .\andon.ps1 -Evaluate -Only <id>       # one condition
#   .\andon.ps1 -List                      # what is declared, and what implements it
#   .\andon.ps1 -Baseline                  # record the protected refs this run starts from
#
# Exit codes: 0 board CLEAR | 1 usage/config error | 2 harness disabled |
#             6 board not clear - raised, incomplete (a REQUIRED condition is not declared),
#               partial (some conditions switched off) or not-evaluated (andon off, or every
#               declared condition switched off). All four refuse an unattended pass; only
#               `clear` authorises one.

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
    $prefix = [string](Get-HarnessSetting "worktree.branch_prefix" "work/")
    $branches = @(Get-Param $cond "branches" @())
    if ($ctx.run_branches -and @($ctx.run_branches).Count -gt 0) { $branches = @($ctx.run_branches) }
    $repo = [string]$ctx.repo_root

    if ($branches.Count -eq 0) {
        $local = Invoke-GitCapture @("-C", $repo, "for-each-ref", "--format=%(refname:short)", ("refs/heads/" + $prefix + "*"))
        if ($LASTEXITCODE -ne 0) { return (New-Result "indeterminate" "git could not list local branches in '$repo'" @()) }
        $branches = @($local | Where-Object { $_ })
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
    # So `clear` now means something narrow and checkable: at least one condition was
    # evaluated, none halted, and none was skipped. Everything else gets its own name and is
    # refused by the gate:
    #   raised        - a condition fired, or could not be evaluated (on_indeterminate=halt)
    #   incomplete    - a condition the system REQUIRES is not declared at all
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
    $results = @()
    $raised = $false
    foreach ($c in $allConditions) {
        $id = [string]$c["id"]
        if ($OnlyId -and $id -ne $OnlyId) { continue }
        $condOff = ($c.Contains("enabled") -and -not $c["enabled"])
        if ($condOff -or -not $enabled) {
            $why = if ($condOff) { "disabled in config" } else { "andon.enabled=false" }
            $results += [ordered]@{ id = $id; status = "disabled"; action = "none"; detail = $why
                                    evidence = @(); predicate = [string]$c["predicate"]; incident = "" }
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
        if ($action -eq "halt") { $raised = $true }
        $incident = ""
        if ($c.Contains("incident")) { $incident = [string]$c["incident"] }
        $results += [ordered]@{
            id = $id; status = $r.status; action = $action; detail = $r.detail
            evidence = @($r.evidence); predicate = [string]$c["predicate"]; incident = $incident
        }
    }
    if ($OnlyId -and $results.Count -eq 0) { throw "no andon condition with id '$OnlyId'" }

    $disabledIds = @($results | Where-Object { $_.status -eq "disabled" } | ForEach-Object { $_.id })
    $evaluated = @($results | Where-Object { $_.status -ne "disabled" }).Count
    $coverage = [ordered]@{
        declared     = @($results).Count
        evaluated    = $evaluated
        disabled     = @($disabledIds).Count
        disabled_ids = @($disabledIds)
        required     = @(Get-RequiredAndonConditionIds).Count
        missing      = @($missingIds).Count
        missing_ids  = @($missingIds)
    }
    $board = "clear"
    $why = ""
    if (@($missingIds).Count -gt 0) {
        $board = "incomplete"
        $why = ("{0} of {1} REQUIRED condition(s) are not declared in the config: {2}" -f
                @($missingIds).Count, @(Get-RequiredAndonConditionIds).Count, (@($missingIds) -join ", "))
    } elseif ($raised) {
        $board = "raised"
    } elseif ($evaluated -eq 0) {
        $board = "not-evaluated"
        # The "no conditions declared at all" case cannot land here while the required set is
        # non-empty - an empty board is `incomplete` above, naming all five. The branch stays
        # so that emptying the required set in code (a deliberate act, visible in a diff)
        # still produces a true sentence rather than a confident wrong one.
        $why = if (@($results).Count -eq 0) {
                   "no andon conditions are declared - the board cannot certify anything"
               } elseif (-not $enabled) {
                   "andon.enabled=false - " + @($results).Count + " condition(s) declared, none evaluated"
               } else {
                   "every declared condition is disabled in config - none evaluated"
               }
    } elseif (@($disabledIds).Count -gt 0) {
        $board = "partial"
        $why = ("{0} of {1} condition(s) are switched off and were not evaluated: {2}" -f
                @($disabledIds).Count, @($results).Count, (@($disabledIds) -join ", "))
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
            if ($r.status -eq "fire") { $colour = "Red" }
            elseif ($r.status -eq "indeterminate") { $colour = "Yellow" }
            elseif ($r.status -eq "disabled") { $colour = "DarkGray" }
            Write-Host ("  [{0,-13}] {1,-30} {2}" -f $r.status, $r.id, $r.detail) -ForegroundColor $colour
            foreach ($e in $r.evidence) { Write-Host ("      - {0}" -f $e) -ForegroundColor DarkGray }
        }
        Write-Host ("  coverage: {0} declared, {1} evaluated, {2} switched off, {3} of {4} required MISSING" -f
                    $verdict.coverage.declared, $verdict.coverage.evaluated, $verdict.coverage.disabled,
                    $verdict.coverage.missing, $verdict.coverage.required) -ForegroundColor DarkGray
        if ([int]$verdict.coverage.missing -gt 0) {
            # Named, not counted. "4 missing" sends an operator to the config to guess; the
            # ids send them to the four lines that are gone.
            Write-Host ("  MISSING required condition(s): {0}" -f (@($verdict.coverage.missing_ids) -join ", ")) -ForegroundColor Red
        }
    }
    # ANYTHING BUT `clear` EXITS 6. `incomplete`, `partial` and `not-evaluated` are not
    # softer greens - a board that did not look, or that is not the board the system
    # requires, cannot say the line is clear, and exiting 0 there is precisely
    # the skip-counts-as-a-pass shape this file refuses everywhere else.
    if ($verdict.board -ne "clear") {
        # The raise ALWAYS goes to the gate ledger (queue.ps1 writes a decision=refused
        # record) - that is not a knob, because a run able to switch off the record of its own
        # halt is the failure this board exists to prevent. Only the stderr copy is optional.
        if ([bool](Get-HarnessSetting "andon.raise.stderr" $true)) {
            $fired = @($verdict.conditions | Where-Object { $_.action -eq "halt" } | ForEach-Object { $_.id })
            if (@($fired).Count -eq 0 -and $verdict.why) { $fired = @($verdict.why) }
            [Console]::Error.WriteLine(("ANDON " + $verdict.board.ToUpper() + ": ") + (@($fired) -join ", "))
        }
        exit 6
    }
    exit 0
}

Die "pass one of -Evaluate | -List | -Baseline"
