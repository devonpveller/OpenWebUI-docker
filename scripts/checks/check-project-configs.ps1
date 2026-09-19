# check-project-configs.ps1 - pre-commit structural validation (Part K.8, 2026-08-21).
#
# Two cheap gates, each run ONLY when the staged changes make them relevant:
#   1. compose validation - any staged *.yml/*.yaml => render every project's
#      compose file with `docker compose config -q` against .env.example
#      (kept complete on purpose, v3 A.4). Catches exactly the drift class
#      the Part K restructure kept finding by hand: broken includes, dead
#      depends_on, missing env guards, bad network refs.
#   2. PowerShell parse - staged *.ps1 files are tokenized with PSParser so a
#      syntax error can never reach a commit (the ops plane is PS 5.1).
#
# EXIT: 0 = clean/skipped, 1 = blocked. Skips gracefully if docker is absent.

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Push-Location $repoRoot

$staged = @(& git diff --cached --name-only --diff-filter=ACM) | Where-Object { $_ }
if (-not $staged) { Write-Host "  [configs] nothing staged - skip"; Pop-Location; exit 0 }

$failed = 0

# --- 1. compose project validation -----------------------------------------
$ymlStaged = @($staged | Where-Object { $_ -match '\.(yml|yaml)$' })
if ($ymlStaged.Count -gt 0) {
    $dockerOk = $true
    try { docker compose version | Out-Null } catch { $dockerOk = $false }
    if (-not $dockerOk -or $LASTEXITCODE -ne 0) {
        Write-Host "  [configs] docker compose unavailable - compose validation skipped" -ForegroundColor Yellow
    }
    else {
        # (OB1 + agent-org validate against their own gitignored env files and
        # are covered by their own workflows; the portal needs its profile.)
        $projects = @(
            @{ N = 'anchor';    F = 'docker-compose.yml';           A = @('--env-file', '.env.example') }
            @{ N = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', '.env.example') }
            # The frontend plane is profile-gated (stack-layers 2.5 / D8), so it
            # is rendered TWICE: once as .env.example leaves it (COMPOSE_PROFILES
            # =stock - the fresh-clone deployment) and once with the operator's
            # profiles. One render can be valid while the other is broken -
            # `openwebui-backup` depends on both openwebui definitions, and a
            # dropped `required: false` only shows up in the render where the
            # named service is off.
            @{ N = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', '.env.example') }
            @{ N = 'frontend (gpu,tailscale)'; F = 'frontend\docker-compose.yml'; A = @('--env-file', '.env.example', '--profile', 'gpu', '--profile', 'tailscale') }
            @{ N = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', '.env.example') }
            @{ N = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', '.env.example') }
            @{ N = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', '.env.example') }
            @{ N = 'portal';    F = 'portal\docker-compose.yml';    A = @('--env-file', '.env.example', '--profile', 'internet') }
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
                @{ P = 'inference'; F = 'inference\docker-compose.yml'; A = @('--env-file', '.env.example', '--profile', 'local') }
                @{ P = 'frontend';  F = 'frontend\docker-compose.yml';  A = @('--env-file', '.env.example', '--profile', 'gpu', '--profile', 'tailscale') }
                @{ P = 'memory';    F = 'memory\docker-compose.yml';    A = @('--env-file', '.env.example') }
                @{ P = 'search';    F = 'search\docker-compose.yml';    A = @('--env-file', '.env.example') }
                @{ P = 'coder';     F = 'coder\docker-compose.yml';     A = @('--env-file', '.env.example') }
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

Pop-Location
if ($failed -gt 0) { exit 1 }
exit 0
