# check-project-configs.ps1 - pre-commit structural validation (Part K.8, 2026-08-21).
#
# Two cheap gates, each run ONLY when the staged changes make them relevant:
#   1. compose validation - any staged *.yml/*.yaml, OR A STAGED `OB1` GITLINK,
#      => render every project's compose file with `docker compose config -q`
#      against ITS OWN `<plane>/.env.example` (each kept complete for that plane,
#      v3 A.4 + sl-env-split D10). Catches exactly the drift class
#      the Part K restructure kept finding by hand: broken includes, dead
#      depends_on, missing env guards, bad network refs.
#      THE GITLINK IS PART OF THAT TRIGGER because of a hole sl-ob1-gitlink walked
#      into: bumping the OB1 submodule pointer REPLACES OB1/docker/docker-compose.yml
#      and its included scheduled file wholesale, yet stages no path matching
#      *.yml - `git status` shows one entry, `OB1`. So the gitlink bump that took
#      OB1's bare render from 29 services to 20 ran this check and got
#      "2 staged .ps1 file(s) parse clean", with the compose renders and the
#      inventory coverage assertion below never executed. A check that sits out
#      the one commit shape it most needs to see is the silent-narrowing class
#      this file already guards against twice (see the render-target notes below).
#   2. PowerShell parse - staged *.ps1 files are tokenized with PSParser so a
#      syntax error can never reach a commit (the ops plane is PS 5.1).
#
# EXIT: 0 = clean/skipped, 1 = blocked. Skips gracefully if docker is absent.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot

$staged = @(& git diff --cached --name-only --diff-filter=ACM) | Where-Object { $_ }
# The SAME `--diff-filter=ACM` gate 4 below had to drop: it discards a rename
# outright, so `git mv old new` plus an edit, staged alone, left $staged empty
# and the whole check exited 0 on "nothing staged" (reproduced at f3eee64,
# 2026-09-22). Gates 1-3 keep the ACM list on purpose - widening what THEY
# enforce is a separate change - but the EARLY EXIT must not be able to skip a
# commit that really did stage something. The gates 1-3 half of this is left
# open deliberately and written down in
# documentation/notes/stack-layers-sl-gate4-carries-findings.md.
$stagedAny = @(& git diff --cached --name-only) | Where-Object { $_ }
if (-not $stagedAny) { Write-Host "  [configs] nothing staged - skip"; Pop-Location; exit 0 }

$failed = 0

# --- 1. compose project validation -----------------------------------------
$ymlStaged = @($staged | Where-Object { $_ -match '\.(yml|yaml)$' })
# A staged `OB1` entry is a submodule gitlink bump: no *.yml path is staged, but
# OB1/docker/docker-compose.yml (and the scheduled file it includes) change
# wholesale underneath. Treat it as a compose change - see the header note.
$gitlinkStaged = @($staged | Where-Object { $_ -eq 'OB1' })
if ($ymlStaged.Count -gt 0 -or $gitlinkStaged.Count -gt 0) {
    $dockerOk = $true
    try { docker compose version | Out-Null } catch { $dockerOk = $false }
    if (-not $dockerOk -or $LASTEXITCODE -ne 0) {
        Write-Host "  [configs] docker compose unavailable - compose validation skipped" -ForegroundColor Yellow
    }
    else {
        # (OB1 + agent-org validate against their own gitignored env files and
        # are covered by their own workflows; the portal needs its profile.)
        # EACH PROJECT AGAINST ITS OWN EXAMPLE (sl-env-split, 2026-09-19). The
        # example is passed explicitly rather than left to compose's native
        # project-directory load, for the reason render_env_path() gives in
        # stack.py: `<plane>/.env` is the DEPLOY host's file and may not exist
        # here at all, while `<plane>/.env.example` is committed - so the render
        # is the same on a laptop, this host and a CI runner. Passing --env-file
        # also SUPPRESSES the native load, so a stray local .env cannot colour
        # the answer.
        $projects = @(
            @{ N = 'anchor';    F = 'docker-compose.yml';           A = @('--env-file', '.env.example') }
            @{ N = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', 'inference\.env.example') }
            # The frontend plane is profile-gated (stack-layers 2.5 / D8), so it
            # is rendered TWICE: once as frontend\.env.example leaves it
            # (COMPOSE_PROFILES=stock - the fresh clone) and once with the
            # operator's. One render can be valid while the other is broken -
            # `openwebui-backup` depends on both openwebui definitions, and a
            # dropped `required: false` only shows up in the render where the
            # named service is off. The inference plane gets the same treatment
            # since sl-env-split: its own example ships `local` COMMENTED OUT (the
            # cloud-only shape), so the `local` half needs its own render here or
            # four services would stop being validated - which is what the root
            # .env.example's single global COMPOSE_PROFILES used to hide.
            @{ N = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', 'frontend\.env.example') }
            @{ N = 'frontend (gpu,tailscale)'; F = 'frontend\docker-compose.yml'; A = @('--env-file', 'frontend\.env.example', '--profile', 'gpu', '--profile', 'tailscale') }
            @{ N = 'inference (local)'; F = 'inference\docker-compose.yml'; A = @('--env-file', 'inference\.env.example', '--profile', 'local') }
            @{ N = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', 'memory\.env.example') }
            @{ N = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', 'search\.env.example') }
            @{ N = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', 'coder\.env.example') }
            @{ N = 'portal';    F = 'portal\docker-compose.yml';    A = @('--env-file', 'portal\.env.example', '--profile', 'internet') }
        )
        foreach ($p in $projects) {
            # cmd /c so compose's stderr WARNINGS (e.g. an unset optional var)
            # can't become PS 5.1 NativeCommandErrors under EAP=Stop.
            $argStr = ($p.A -join ' ')
            cmd /c "docker compose -f $($p.F) $argStr config -q 2>nul" | Out-Null
            if ($LASTEXITCODE -ne 0) {
                $msg = (cmd /c "docker compose -f $($p.F) $argStr config -q 2>&1" | Select-Object -First 2) -join ' '
                Write-Host "  [configs] COMPOSE INVALID: $($p.N) - $msg" -ForegroundColor Red
                $failed++
            }
        }
        if ($failed -eq 0) { Write-Host "  [configs] all $($projects.Count) compose projects render clean" }

        # --- stack-services.json drift verifier (D-12, 2026-08-22) -----------
        # The inventory's curated fields (critical/host_health/notes) stay
        # hand-owned, but the MACHINE guarantees the (container -> project)
        # rows are complete and correct against the rendered compose configs.
        $invPath = 'scripts\lib\stack-services.json'
        if (Test-Path $invPath) {
            $inv = Get-Content $invPath -Raw | ConvertFrom-Json
            $known = @{}
            $rowProfile = @{}
            foreach ($plane in $inv.planes.PSObject.Properties) {
                foreach ($row in $plane.Value) {
                    $known[$row.container] = $row.project
                    $rowProfile[$row.container] = $row.profile   # $null when unprofiled
                }
            }
            # Each target must pass every profile its project's inventory rows carry, or
            # the coverage assertion below fails it. `local` was added here 2026-09-19:
            # without it this render emitted 4 of inference's 8 rows and the other four
            # (llama-cpp-upstream, llama-cpp-embed-upstream, llm-queue, lm-models-backup)
            # had never been verified, silently - the same defect the OB1 profiles would
            # have introduced, already present and unnoticed.
            $renderTargets = @(
                # PROFILED renders for BOTH gated planes: a default render is a
                # SUBSET, so checking it would silently stop verifying the rows
                # behind a profile. The profiled render is the superset - every
                # container name the plane can produce. (`--profile local` from
                # sl-inference-split; the frontend's pair from sl-frontend-solo.)
                @{ P = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', 'inference\.env.example', '--profile', 'local') }
                @{ P = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', 'frontend\.env.example', '--profile', 'gpu', '--profile', 'tailscale') }
                @{ P = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', 'memory\.env.example') }
                @{ P = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', 'search\.env.example') }
                @{ P = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', 'coder\.env.example') }
            )
            # OB1 renders only where its gitignored env exists (not in CI).
            # ALL FOUR PROFILES, deliberately: OB1 gained research/wiki/notebook
            # profiles on 2026-09-19 (sl-ob1-profiles), so a bare render now
            # emits 20 of its 30 container_names. This check only walks
            # compose -> json, so the 10 profiled rows would not have FAILED -
            # they would simply have stopped being verified, which is worse than
            # a red check. Passing the profiles keeps all 30 rows covered.
            if (Test-Path 'OB1\docker\.env') {
                $renderTargets += @{ P = 'open-brain'; F = 'OB1\docker\docker-compose.yml';
                                     A = @('--profile', 'research', '--profile', 'wiki',
                                           '--profile', 'notebook', '--profile', 'idea-refinery') }
            }

            # A project with inventory rows but NO render target is unverified, and until
            # now that was invisible: the `if` above silently dropped open-brain wherever
            # OB1\docker\.env is absent - which is the CI shape - so the check printed its
            # green line having verified 30 fewer rows than it appeared to. Same silent
            # narrowing as a dropped --profile, by a different route. Name it. NOT a
            # failure: OB1's env is gitignored and CI legitimately cannot render it, so a
            # red here would mean crying wolf on every CI run. An unmissable line is the
            # honest answer; the assertions below still cover every project that IS
            # rendered. (agent-org has never had a render target either - same treatment.)
            $rendered = @($renderTargets | ForEach-Object { $_.P })
            $projectsWithRows = @($known.Values | Sort-Object -Unique)
            foreach ($proj in $projectsWithRows) {
                if ($rendered -notcontains $proj) {
                    $n = @($known.Keys | Where-Object { $known[$_] -eq $proj }).Count
                    $why = if ($proj -eq 'open-brain') {
                        " (OB1\docker\.env absent - gitignored, so this is expected off the deploy host)"
                    } else { "" }
                    Write-Host ("  [configs] NOT VERIFIED: project '$proj' has $n inventory row(s) " +
                                "and no render target$why") -ForegroundColor Yellow
                }
            }
            $drift = @()
            $coverage = @()
            foreach ($rt in $renderTargets) {
                # WHAT THIS RENDER MUST COVER: EVERY inventory row for the project.
                #
                # Not "every row whose profile this target happens to pass" - that was the
                # first version of this guard and it was worthless, because it derived the
                # expectation from the same argument list it was meant to police: dropping
                # --profile wiki dropped the four wiki rows from BOTH sides and the check
                # stayed green at 26/26. The expectation has to come from something the
                # render target cannot move, and the inventory is that thing.
                #
                # So a profiled row is covered by PASSING ITS PROFILE here. If a project
                # ever has rows a single render genuinely cannot reach, the honest fix is
                # to say so in the target, not to shrink the expectation.
                #
                # Why this matters: the check only walks compose -> json, so ANY narrowing
                # of a render - a dropped --profile, a missing gitignored env file - used
                # to silently shrink what was verified while the green line still printed.
                # This item's tester proved it by instrumenting the script: open-brain went
                # 30 -> 26 rows, exit 0, no new output. inference was quietly verifying 4
                # of its 8 rows for the same reason, which is why `local` is now passed.
                $expected = @($known.Keys | Where-Object { $known[$_] -eq $rt.P })

                # Regex extraction, NOT ConvertFrom-Json: PS 5.1's parser rejects
                # the rendered config's case-duplicate env keys (HTTP_PROXY vs
                # http_proxy on open-terminal). container_name lines are enough.
                $argStr = ($rt.A -join ' ')
                $raw = (cmd /c "docker compose -f $($rt.F) $argStr config --format json 2>nul") -join "`n"
                if (-not $raw) {
                    # Was `continue`, which unverified every row of the target in silence.
                    $drift += ("RENDER PRODUCED NOTHING for $($rt.P) ($($rt.F)) - " +
                               "$($expected.Count) inventory row(s) went unverified. " +
                               "Check the compose file and its env/profile arguments.")
                    continue
                }
                $names = [regex]::Matches($raw, '"container_name":\s*"([^"]+)"') |
                    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
                foreach ($cname in $names) {
                    if (-not $known.ContainsKey($cname)) {
                        $drift += "MISSING from stack-services.json: $cname (project $($rt.P))"
                    }
                    elseif ($known[$cname] -ne $rt.P) {
                        $drift += "WRONG project for ${cname}: json says '$($known[$cname])', compose says '$($rt.P)'"
                    }
                }
                $coverage += "$($rt.P):$($names.Count)/$($expected.Count)"
                # The other direction, and the one the compose->json walk cannot see.
                $unseen = @($expected | Where-Object { $names -notcontains $_ })
                if ($unseen.Count) {
                    $profilesPassed = @()
                    for ($i = 0; $i -lt $rt.A.Count; $i++) {
                        if ($rt.A[$i] -eq '--profile') { $profilesPassed += $rt.A[$i + 1] }
                    }
                    $needed = @($unseen | ForEach-Object { $rowProfile[$_] } |
                                Where-Object { $_ } | Sort-Object -Unique)
                    $hint = if ($needed) { " (add --profile $($needed -join ' --profile '))" } else { "" }
                    $drift += ("UNVERIFIED rows for $($rt.P): the render emitted $($names.Count) " +
                               "container(s), the inventory holds $($expected.Count) - not verified: " +
                               "$($unseen -join ', ')$hint. Profiles passed: " +
                               "$(if ($profilesPassed) { $profilesPassed -join ',' } else { '(none)' }). " +
                               "Either the render lost a profile or an env file, or the row is stale.")
                }
            }
            if ($drift.Count) {
                foreach ($d in $drift) { Write-Host "  [configs] INVENTORY DRIFT: $d" -ForegroundColor Red }
                Write-Host "  [configs] fix scripts\lib\stack-services.json (curated fields are yours; container/project rows must match compose)" -ForegroundColor Yellow
                $failed += $drift.Count
            }
            else {
                # Say HOW MANY rows each render actually verified, as rendered/expected.
                # "matches the compose configs" on its own reads as coverage and is really
                # just the size of whatever the render happened to emit - the number this
                # item's tester had to add by instrumenting the script to see a silent
                # 30 -> 26 narrowing. Print it so nobody has to instrument it again.
                Write-Host ("  [configs] stack-services.json inventory matches the compose configs " +
                            "[rows verified/expected: $($coverage -join ' ')]")
            }

        # The coverage guard above and the generator check below are BOTH kept,
        # deliberately (sl-driver-parity + sl-ob1-profiles, merged 2026-09-19).
        # They do not answer the same question:
        #   coverage  - did THIS render reach every inventory row for the project?
        #               It is what caught open-brain silently verifying 26 of 30.
        #   generator - is the whole file reproducible from stack.manifest.toml
        #               plus scripts\lib\stack-services.curated.json?
        # A file can be perfectly reproducible from inputs that were themselves
        # derived from a render that narrowed, so neither subsumes the other.
        }
    }
}


# --- 1b. the service inventory, via the driver ------------------------------
#
# WAS an inline row diff here (D-12, 2026-08-22): render five projects, regex
# every container_name out of the JSON, compare (container -> project) against
# scripts\lib\stack-services.json. It has been replaced by
# `python scripts\stack\stack.py inventory --check`, which does strictly more:
#
#   * it renders WITH every declared profile, so profile-gated containers are
#     visible. The inline version rendered without them and therefore could not
#     see openbrain-idea-refinery - a container the watchdog consequently
#     refused to repair, for as long as the check had existed;
#   * it checks the reverse direction too (a row in the JSON that no render
#     produces), and the compose SERVICE key, and the published host ports and
#     the profiles against stack.manifest.toml;
#   * the whole file is GENERATED from the manifest plus
#     scripts\lib\stack-services.curated.json, so "matches" means byte-for-byte
#     reproducible, not "the two columns I happened to compare agree".
#
# Trigger: any staged compose file (as before) OR any staged inventory input -
# the manifest, the generated file, or the curated sidecar. The old placement,
# inside the *.yml gate, meant an edit to stack-services.json alone was never
# verified against anything.
$invInputs = @($staged | Where-Object {
        $_ -match '\.(yml|yaml)$' -or
        $_ -eq 'stack.manifest.toml' -or
        $_ -match '^scripts/lib/stack-services(\.curated)?\.json$'
    })
if ($invInputs.Count -gt 0) {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    $dockerOk = $true
    try { docker compose version | Out-Null } catch { $dockerOk = $false }
    if (-not $py) {
        Write-Host "  [configs] python not found - service inventory NOT verified (this is a gap, not a pass)" -ForegroundColor Yellow
    }
    elseif (-not $dockerOk -or $LASTEXITCODE -ne 0) {
        Write-Host "  [configs] docker compose unavailable - service inventory NOT verified (this is a gap, not a pass)" -ForegroundColor Yellow
    }
    else {
        # EAP is dropped to Continue around the call for the usual PS 5.1 reason:
        # a native command's stderr becomes a terminating NativeCommandError
        # under 'Stop'. The driver writes nothing to stderr by design, but a
        # docker warning underneath it can.
        $prev = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $invOut = & python 'scripts/stack/stack.py' inventory --check 2>&1
        $invCode = $LASTEXITCODE
        $ErrorActionPreference = $prev
        foreach ($line in $invOut) { Write-Host ("  [configs] " + ("$line").TrimEnd()) }
        if ($invCode -ne 0) {
            Write-Host "  [configs] INVENTORY DRIFT - regenerate with: python scripts\stack\stack.py inventory --write" -ForegroundColor Red
            Write-Host "  [configs] (curated fields live in scripts\lib\stack-services.curated.json; edit those THERE)" -ForegroundColor Yellow
            $failed++
        }
    }
}

# --- 2. staged PowerShell parse ---------------------------------------------
$ps1Staged = @($staged | Where-Object { $_ -match '\.ps1$' -and (Test-Path $_) })
foreach ($f in $ps1Staged) {
    $errs = $null
    [System.Management.Automation.PSParser]::Tokenize((Get-Content $f -Raw), [ref]$errs) | Out-Null
    if ($errs.Count -gt 0) {
        Write-Host "  [configs] PS1 PARSE ERROR: $f - $($errs[0].Message)" -ForegroundColor Red
        $failed++
    }
}
if ($ps1Staged.Count -gt 0 -and $failed -eq 0) {
    Write-Host "  [configs] $($ps1Staged.Count) staged .ps1 file(s) parse clean"
}

# --- 3. staged JSON, under the STRICTER of the two parsers -------------------
#
# WHY PYTHON AND NOT ConvertFrom-Json. PowerShell's JSON parser is lenient: it accepts a
# raw newline inside a string literal, which is invalid JSON. Python's does not. That is
# not academic here - scripts/agent-harness/harness.config.json is read by BOTH
# config.ps1 and config.py (it says so in its own header), and a multi-line comment string
# written into it parsed fine in every PowerShell path while json.loads rejected it. The
# result would have been a harness that worked from the scripts and broke in the Mattermost
# bridge, with nothing at commit time to say so.
#
# So the gate uses the stricter parser deliberately. If python is unavailable it says so
# and skips - a check that cannot run must never masquerade as one that passed.
$jsonStaged = @($staged | Where-Object { $_ -match '\.json$' -and (Test-Path $_) })
if ($jsonStaged.Count -gt 0) {
    $py = (Get-Command python -ErrorAction SilentlyContinue)
    if (-not $py) {
        Write-Host "  [configs] python not found - staged JSON NOT validated (this is a gap, not a pass)" -ForegroundColor Yellow
    } else {
        $jsonBad = 0
        foreach ($f in $jsonStaged) {
            $prev = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            $out = & python -c "import json,sys; json.load(open(sys.argv[1], encoding='utf-8'))" $f 2>&1
            $code = $LASTEXITCODE
            $ErrorActionPreference = $prev
            if ($code -ne 0) {
                Write-Host "  [configs] INVALID JSON: $f" -ForegroundColor Red
                Write-Host ("             " + (($out | Out-String).Trim() -split "`n" | Select-Object -Last 1)) -ForegroundColor Red
                $failed++; $jsonBad++
            }
        }
        if ($jsonBad -eq 0) { Write-Host "  [configs] $($jsonStaged.Count) staged .json file(s) are strict-valid" }
    }
}

# --- 4. control characters in ADDED lines ----------------------------------
# A `\b` in a non-raw Python replacement string put four BACKSPACE bytes (0x08)
# into three shipped scripts during sl-recovery-backups. One of them was an
# executable Write-Host: rendered on a terminal the backspace ERASES the
# preceding character, so an operator setting up NAS credentials was told to run
# `.\scriptackup\install-nas-backup-task.ps1`, a path that does not exist. The
# same escape-sequence class as the `\s` that item fixed in status_check.py.
#
# EVERY gate passed. This file parsed it (0x08 is whitespace to the tokenizer),
# the line-ending check only looks at CR/LF, and the anchor's hand-run encoding
# sweep tested `byte > 127` - and 0x08 is 8. That is the workspace's recurring
# "a check that passes while checking nothing" shape, and it survived purely
# because nothing had ever looked BELOW 0x20.
#
# ADDED LINES, not whole files, and deliberately so: eleven such bytes already
# sit in older documentation/evidence and documentation/notes files (measured
# 2026-09-21), and a whole-file rule would fail the next commit that touches one
# of them for an unrelated reason. This catches what a commit INTRODUCES, which
# is the failure mode.
#
# Tab, LF and CR are legal. Everything else below 0x20 - and 0x7F - is not: none
# of them survives a copy-paste, a terminal render or a code review intact.
$ctrlBad = @()
$prev = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
# -U0 so only changed lines carry a '+', and --text so a file git has not been
# TOLD is binary is still scanned rather than summarised away (the .gitattributes
# skip below is the deliberate, PRINTED exit from the scan - never git's silent
# content heuristic).
#
# NO --diff-filter, and rename detection left at git's default (-M). Both are
# corrections to this gate's first version; each reproduction at f3eee64 is
# recorded in documentation/notes/stack-layers-sl-gate4-carries-findings.md.
#   * `--diff-filter=ACM` DROPPED a rename outright. `git mv` plus an edit that
#     injected a 0x08 produced an EMPTY diff here, and because the same filter
#     feeds $staged at the top of the file the whole check printed
#     "nothing staged - skip" and exited 0. No filter at all cannot drop a
#     change class - the same silent-narrowing shape gate 1 above already
#     carries twice. Deletions arrive too, and a deletion has no added lines,
#     so they cost nothing.
#   * rename detection STAYS ON rather than --no-renames. --no-renames respells
#     a move as delete+add, which makes EVERY line of the moved file an "added"
#     line: it would report lines the commit did not author, and a pure move of
#     any of the eleven pre-existing control bytes already sitting in
#     documentation/ would go red. With -M the rename's hunk holds exactly the
#     lines the commit introduced, which is what "added line" is meant to mean.
$addedDiff = @(& git diff --cached -U0 --text)
$ErrorActionPreference = $prev

# WHICH FILES ARE BINARY: `git check-attr`, i.e. .gitattributes - NOT a NUL-byte
# content probe. Measured on this tree 2026-09-22: 1198 tracked files, 0 with
# the binary attribute set and 0 carrying a NUL byte in their first 8000 bytes,
# so neither test changes anything about what is here today and the choice is
# entirely about what a future commit stages. The attribute wins because a
# content probe would skip a file BECAUSE of a byte (0x00) that is itself inside
# this gate's own class: inject a NUL into a text file and a NUL-probing gate
# falls silent on precisely its worst input. .gitattributes is a declaration
# somebody made on purpose and it is reviewable in the diff. The cost is that a
# binary type .gitattributes does not cover yet (.pdf, .woff2, .wasm, .zip)
# still reaches the scan and fails - so the failure text below names that case
# and its one-line fix, instead of blaming a non-raw Python string as it did for
# the 70-byte PNG that started this.
$ctrlFiles = @()
foreach ($line in $addedDiff) {
    if ($line -like '+++ b/*') { $ctrlFiles += $line.Substring(6) }
}
$binarySkip = @{}
if ($ctrlFiles.Count -gt 0) {
    # --stdin, NOT a path list: `git check-attr binary -- <path>...` over a
    # whole-tree stage blows the Windows command-line limit (measured
    # 2026-09-22 on this tree: WinError 206 at 1198 paths).
    #
    # And a TEMP FILE redirected by cmd, not a PowerShell pipeline into git:
    # PS 5.1 terminates every line it writes to a native command's stdin with
    # CRLF, and `git check-attr --stdin` takes the trailing CR as part of the
    # path. Measured 2026-09-22: the pipeline form answered
    # `"docs-test.png\r": binary: unspecified` for a file .gitattributes marks
    # binary - a skip that silently never happens, which is the failure this
    # gate was rewritten to stop making.
    $attrTmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($attrTmp, (($ctrlFiles -join "`n") + "`n"))
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $attrOut = @(cmd /c "git check-attr --stdin binary < ""$attrTmp"" 2>nul")
    $ErrorActionPreference = $prev
    Remove-Item $attrTmp -Force -ErrorAction SilentlyContinue
    foreach ($a in $attrOut) {
        $m = [regex]::Match("$a", '^(.*): binary: (.*)$')
        if ($m.Success -and $m.Groups[2].Value -eq 'set') {
            $binarySkip[$m.Groups[1].Value] = $true
        }
    }
}
foreach ($b in ($binarySkip.Keys | Sort-Object)) {
    Write-Host "  [configs] binary per .gitattributes - control-character scan skipped: $b"
}

$ctrlRx = [regex]'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]'
$curFile = ''
$curLine = 0
$skipFile = $true
foreach ($line in $addedDiff) {
    if ($line -like '+++ *') {
        # '+++ /dev/null' is a deletion - no added lines follow it, so park it.
        if ($line -like '+++ b/*') { $curFile = $line.Substring(6) } else { $curFile = '' }
        $skipFile = ($curFile -eq '') -or $binarySkip.ContainsKey($curFile)
        continue
    }
    if ($line -like '@@*') {
        # -U0 hunk header '@@ -a,b +c,d @@': c is the first NEW-file line number
        # in the hunk, which is what turns a hit into a file:line.
        $h = [regex]::Match("$line", '^@@ -\S+ \+(\d+)')
        if ($h.Success) { $curLine = [int]$h.Groups[1].Value }
        continue
    }
    if ($line -notlike '+*') { continue }
    $n = $curLine
    $curLine++
    if ($skipFile) { continue }
    # The staged blob is what ships, so scan the diff's own bytes.
    $hit = $ctrlRx.Match("$line")
    if ($hit.Success) {
        $code = '0x{0:X2}' -f [int][char]$hit.Value
        $shown = "$line".Substring(1) -replace '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '<CTRL>'
        $ctrlBad += "${curFile}:${n} : $code in an added line -> $($shown.Trim())"
    }
}
if ($ctrlBad.Count -gt 0) {
    Write-Host "  [configs] CONTROL CHARACTER in staged added line(s):" -ForegroundColor Red
    $ctrlBad | ForEach-Object { Write-Host "             $_" -ForegroundColor Red }
    Write-Host "             Tab/LF/CR are fine; nothing else below 0x20 is." -ForegroundColor Red
    Write-Host "             Usual cause: a backslash escape in a NON-RAW replacement string" -ForegroundColor Red
    Write-Host "             (\b -> 0x08, \a -> 0x07, \f -> 0x0C). Use rb'' / r'' literals." -ForegroundColor Red
    Write-Host "             If the file is BINARY, this gate is the wrong place to argue with it:" -ForegroundColor Red
    Write-Host "             declare the type in .gitattributes (e.g. '*.woff2 binary') and the next" -ForegroundColor Red
    Write-Host "             run skips it BY NAME on a printed line." -ForegroundColor Red
    $failed += $ctrlBad.Count
} elseif ($stagedAny.Count -gt 0) {
    Write-Host "  [configs] no control characters in staged added lines"
}

Pop-Location
if ($failed -gt 0) { exit 1 }
exit 0
