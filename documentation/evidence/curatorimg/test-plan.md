# Test plan - curatorimg

Anchor: `queue.ps1 -Show -Id curatorimg` (amended 2026-09-06: acceptance line 3
now DECIDES the unhealthy-under-dead-DB posture, line 9 forbids printing a
secret, and PLAN.md joins this branch). Branch `work/curatorimg`, worktree
`wt-curatorimg`. Parent code commit `af0faad` (the gitlink bump + the three
health surfaces; docs follow on the same branch). **OB1 commit
`d89c126d9b65b39f9b31d49392f62604becb3485`** on OB1 branch
`fix/research-curator-dockerfile`, cut from the line's pin `a07103b`.

**You are testing three claims, and one of them is about a check, not code:**

1. an image built from the new OB1 pin cannot be missing a module `index.ts`
   imports - the BUILD fails instead of the container crash-looping (T3, T5);
2. a curator that is looping, or whose database is gone, is now visible on the
   three health surfaces the operator already reads (T4, T6);
3. gate 5d (item `gate5d`, landing concurrently) refuses the pin that shipped
   the loop and passes the pin that fixes it (T1, T2). **If it is green on both
   pins, the gate is wrong, not this fix - FAIL T2 and say so.**

Nothing here deploys. `openbrain-curator` in production is crash-looping on
`Module not found file:///app/pool.ts` and stays that way until the operator's
gated deploy after merge. Do not touch it: no `docker restart`, no `-Repair`,
no `docker compose up`. The `[FAIL]`/`[DOWN]` lines in T6 are the correct
reading of today's stack and are what this item exists to produce.

**A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
`queue.ps1 -PlanInadequate` naming the case; never write `PASS (scoped)`,
`SKIPPED` or a pass on partial evidence. T8 is the check on that.

**A case that prints a secret is itself a FAIL.** Anchor acceptance line 9:
no plan case prints a secret; a case that renders compose config must redact
or filter BEFORE output. Every command below that touches rendered config uses
`--no-interpolate` and matches single lines, never a block. If your evidence
contains a `DB_PASSWORD`, `MCP_ACCESS_KEY` or any `*_KEY` value, the case is
a FAIL regardless of what else it showed, and the evidence must be scrubbed
before it is submitted.

Attempt 1 (2026-09-06) passed all nine cases and was marked plan-inadequate
on T2, T3, T4 and T6; this revision fixes those four. What changed is marked
"(rev 2)" in each case.

## Environment

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg"
    git rev-parse HEAD                  # af0faad or a later commit on work/curatorimg
    git -C OB1 rev-parse HEAD           # d89c126d9b65b39f9b31d49392f62604becb3485

No plane lease: T3 and T5 build isolated images (tag `:wt-curatorimg` or a
scratch tag, default `bridge` network, loopback port 18816), T4 is `compose
config` (starts nothing), T6 is read-only against the live stack. Never tag
`:local`, never `--network ai-stack_*`, never `-Repair`. `node` and `deno` must
be on PATH (the pre-commit hooks need them; T5 needs docker only).

---

## T0 - the rule that destroyed the predecessor. Do this FIRST.

The previous attempt at this item was lost because its OB1 commit was never
pushed. Do not assume; check:

    cd OB1 && git ls-remote origin fix/research-curator-dockerfile
    git -C OB1 rev-parse HEAD
    git ls-tree HEAD OB1        # in the parent, at commit af0faad or later

All three must show `d89c126d9b65b39f9b31d49392f62604becb3485`. If ls-remote
does not, **FAIL the item immediately** - the gitlink is unreachable and a fresh
`--recurse-submodules` clone breaks.

## T1 - RED: gate 5d refuses the incident pin

The script lands with item `gate5d`. Until that merges, run it from that
item's worktree; it operates on THAT worktree's OB1 clone, which must hold both
pins (they are on `origin`'s default line, so a plain fetch suffices):

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-gate5d"
    git -C OB1 fetch origin
    powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin 48c0363 -NewPin a07103b; echo $LASTEXITCODE

Expected: **non-zero** exit, and the output names `research-curator` and
`pool.ts`. Record the exit code and the line that names them. If the script
does not exist yet in that worktree, this case cannot be executed - that is
`-PlanInadequate` for T1 and T2, not a pass.

## T2 - GREEN: gate 5d passes this item's pin

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-gate5d"
    git -C OB1 fetch origin fix/research-curator-dockerfile
    powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin a07103b -NewPin d89c126d9b65b39f9b31d49392f62604becb3485; echo $LASTEXITCODE

Expected: **exit 0**, and (rev 2) the gate's one-line build summary - it
prints a summary on success, not the build log - reads

    [check-ob1-integration-images] build: ob1-gate/research-curator:d89c126 built and 'deno check index.ts' resolved inside it (Ns); tag removed.

followed by `OK - 1 integration image(s) build and resolve at OB1 d89c126
(research-curator)`. Quote the `build:` line. The build-log proof that the
check step actually executed is T3's job. T1 red AND T2 green is the pass;
any other combination is a FAIL of this case with the reason written down.

## T3 - the image built from the pin starts with a dead database

Build from the worktree's OB1 clone (this is the tree the gitlink pins):

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg\OB1\integrations\research-curator"
    docker build --progress=plain --no-cache -t openbrain-curator:wt-curatorimg . 2>&1 | Select-String -Pattern "RUN deno check|Check\S*\s+\S*file:///app/index.ts|COPY \*\.ts|rm -f|naming to" | ForEach-Object { $_.Line }

(rev 2) `--progress=plain` is what puts the step lines in the output. Deno
colours its own `Check` line even under BuildKit's plain progress, and the
escape sequence sits BETWEEN the word and the path (`ESC[32mCheck ESC[0m
file:///app/index.ts`), so attempt 1's literal `Check file:///app/index.ts`
matched nothing. Setting `NO_COLOR` on the host does NOT fix it - the
variable never reaches the build container (verified: 0 matches with
`$env:NO_COLOR = "1"`), and this item may not edit the Dockerfile to add an
`ENV`. The pattern above tolerates one escape sequence between `Check` and
`file:///`; the line it prints will show the raw escapes. The point is that
the step RAN - this is the case that proves the gate's summary in T2 is not
free.

Expected in the output, in this order: `[5/7] COPY *.ts ./`,
`[6/7] RUN rm -f ./*.test.ts`, `[7/7] RUN deno check index.ts`, a line
containing `Check` and `file:///app/index.ts` (with escapes between), then
`naming to docker.io/library/openbrain-curator:wt-curatorimg done`.
(Developer's rev-2 run printed exactly those five lines; step
`#11 [7/7] RUN deno check index.ts`, `#11 0.372 ...Check... file:///app/index.ts`.)

Run it against a host with no Postgres on it, on the default bridge only:

    docker run -d --name curatorimg-t3 -e DB_HOST=127.0.0.1 -e DB_PASSWORD=x -p 127.0.0.1:18816:8000 openbrain-curator:wt-curatorimg
    Start-Sleep 5
    curl.exe -s -m 5 http://127.0.0.1:18816/health
    curl.exe -s -o NUL -w "%{http_code}" -m 5 http://127.0.0.1:18816/health
    docker inspect --format "{{range `$k,`$v := .NetworkSettings.Networks}}{{`$k}} {{end}}" curatorimg-t3

Expected: body is JSON with `"ok":false,"db":false` (a `pool_rebuilds` counter
is also present and climbs - that is the ResilientPool discarding dead pools,
not a fault); HTTP status **503** - the handler's documented db-down status,
the PROCESS is up; networks print exactly `bridge`. Under the new compose
healthcheck that same state is UNHEALTHY and research waits behind it - the
amended anchor DECIDES that is intended; it is not a finding against T3. Then
wait and read the restart counter:

    Start-Sleep 60
    docker inspect --format "RestartCount={{.RestartCount}} status={{.State.Status}} started={{.State.StartedAt}}" curatorimg-t3
    docker rm -f curatorimg-t3

Expected: `RestartCount=0 status=running`, and `started=` at least 60 s before
the time you read it (quote both timestamps). **Keep the `:wt-curatorimg` tag** -
do not `docker rmi` it. (Developer's run: started 12:17:53Z, read 12:19:25Z,
RestartCount=0.)

## T4 - compose renders the healthcheck and the service_healthy condition

(rev 2: attempt 1's version of this case dumped the rendered `environment:`
block - `DB_PASSWORD`, `MCP_ACCESS_KEY`, the LiteLLM key - into the tester's
terminal, because `Out-String | Select-String` matches one multi-line string
as a whole. **A case that prints a secret is itself a FAIL.** This version
renders with `--no-interpolate`, so `${VAR}` references stay as written, and
then matches single lines only. Do not `Out-String` the render, do not use
`-Context`, do not open the file in an editor and paste from it.)

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg"
    docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env config --no-interpolate > "$env:TEMP\curatorimg-config.yml"; echo $LASTEXITCODE
    Select-String -Path "$env:TEMP\curatorimg-config.yml" -Pattern "PASSWORD|_KEY" | Where-Object { $_.Line -notmatch "\$\{" } | ForEach-Object { $k, $v = $_.Line -split ":\s*", 2; "{0}: {1}: <len={2}>" -f $_.LineNumber, $k.Trim(), $v.Length }

Expected: exit 0 (`config` starts nothing), and the second command - which
prints key names and value LENGTHS, never values - lists exactly THREE lines,
in this order: `OPEN_BRAIN_SERVICE_KEY: <len=11>`, `WIKI_GIT_SSH_KEY:
<len=19>`, `target: <len=19>`. Those are literals in the TRACKED compose
source, not `.env` values: `OB1/docker/docker-compose.yml:645` sets
`OPEN_BRAIN_SERVICE_KEY` to a placeholder string, `:714` sets
`WIKI_GIT_SSH_KEY` to a container path under `/secrets/`, and the third is
the secrets-mount `target:` path. Every other `PASSWORD`/`_KEY` line in the
render is still a `${...}` reference. (Attempt 1's developer dry run of this
case asserted the count would be `0`; it is 3, for the reason above.) If the
list contains ANY other key, STOP: do not run the rest of this case, delete
the temp file, and record T4 as FAIL - the render carries a resolved secret.

Now the curator block. The render sorts services alphabetically and
`openbrain-db` (which has its own healthcheck) follows the curator, so the
window is bounded at the NEXT service key, not at a fixed line count - a
fixed window printed openbrain-db's `healthcheck:` lines as if they were the
curator's in the developer's first dry run. Match only the lines you need
inside that block, printing nothing else:

    $cfg = Get-Content "$env:TEMP\curatorimg-config.yml"
    $svcIdx = @(0..($cfg.Count-1) | Where-Object { $cfg[$_] -match "^  [a-z_-]+:$" })
    $i = [array]::IndexOf($cfg, "  openbrain-curator:")
    $end = ($svcIdx | Where-Object { $_ -gt $i } | Select-Object -First 1) - 1
    "curator block lines $($i+1)-$($end+1); next service: $($cfg[$end+1])"
    $cfg[$i..$end] | Select-String -Pattern "^    healthcheck:|Deno\.exit\(r\.ok\?0:1\)|^      interval: 30s|^      timeout: 5s|^      retries: 3|^      start_period: 20s" | ForEach-Object { $_.Line }

Expected: the block line, naming `  openbrain-db:` as the next service, then
exactly SIX lines - `    healthcheck:`, `interval: 30s`, `retries: 3`,
`start_period: 20s`, the test element ending `Deno.exit(r.ok?0:1)`,
`timeout: 5s` (the render sorts keys, so this is their order). A seventh line
means the window ran past the block. Nothing from `environment:` appears
because nothing matching was asked for. (Developer's rev-2 run: block lines
188-239, six lines.)

The research block, same way:

    $j = [array]::IndexOf($cfg, "  openbrain-research:")
    $endj = ($svcIdx | Where-Object { $_ -gt $j } | Select-Object -First 1) - 1
    $cfg[$j..$endj] | Select-String -Pattern "^    depends_on:|^      openbrain-curator:|^      openbrain-db:|^        condition: service_|^        required: true" | ForEach-Object { $_.Line }
    ($cfg[$j..$endj] | Select-String -Pattern "service_started").Count

Expected: `    depends_on:`, `      openbrain-curator:`,
`        condition: service_healthy`, `        required: true`, then
`      openbrain-db:` with its own `service_healthy` / `required: true`
pair - seven lines - and the count prints `0`: no `service_started` anywhere
in the research block. Delete the temp file afterwards
(`Remove-Item "$env:TEMP\curatorimg-config.yml"`).

Negative control (rev 2: attempt 1 claimed "exactly one `service_started` at
a07103b, none at d89c126"; the real counts are **3 and 2**, because the
curator->mcp and workbench->wiki dependencies also use `service_started` and
are untouched). The check is the diff of the ONE line that changed:

    git -C OB1 show a07103b:docker/docker-compose.yml | Select-String "condition: service_started" | ForEach-Object { $_.LineNumber }
    git -C OB1 show d89c126:docker/docker-compose.yml | Select-String "condition: service_started" | ForEach-Object { $_.LineNumber }
    git -C OB1 diff a07103b d89c126 -- docker/docker-compose.yml | Select-String -Pattern "^[+-] +condition: " | ForEach-Object { $_.Line }

Expected: first command prints `480`, `539`, `802` (three); second prints
`480`, `808` (two - line 539 gone, 802 shifted +6 by the six healthcheck
lines); third prints exactly two lines,
`-        condition: service_started` and `+        condition: service_healthy`,
and nothing else - that is research->curator at old line 539, the only
condition this item touched.

## T5 - mutation: the build-time check bites

In a SCRATCH COPY (never the worktree):

    $S = "$env:TEMP\curatorimg-mut"; Remove-Item -Recurse -Force $S -ErrorAction SilentlyContinue
    Copy-Item -Recurse "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg\OB1\integrations\research-curator" $S
    cd $S
    Remove-Item pool.ts
    (Get-Content Dockerfile) | Where-Object { $_ -notmatch "^RUN deno check index.ts$" } | Set-Content -Encoding ascii Dockerfile
    Select-String -Path Dockerfile -Pattern "deno check"      # must print NOTHING
    docker build -q -t curator-mutant:a . ; echo "build exit=$LASTEXITCODE"
    docker run -d --name curator-mutant-a -e DB_HOST=127.0.0.1 -e DB_PASSWORD=x curator-mutant:a
    docker wait curator-mutant-a
    docker logs curator-mutant-a 2>&1 | Select-Object -Last 3
    docker rm -f curator-mutant-a

Expected A (no check, no pool.ts): **build exit 0**, `docker wait` prints a
NON-zero exit code (developer's run: 1), and the log ends with
`error: Module not found "file:///app/pool.ts".` - the exact production
symptom, reproduced from an image the old Dockerfile would have shipped.

    (Get-Content Dockerfile) -replace "^USER deno$", "RUN deno check index.ts`nUSER deno" | Set-Content -Encoding ascii Dockerfile
    Select-String -Path Dockerfile -Pattern "deno check|^USER deno"   # check line directly above USER deno
    Test-Path pool.ts                                                  # False - still missing
    docker build --progress=plain -t curator-mutant:b . 2>&1 | Select-String -Pattern "deno check|TS2307|Type checking failed|did not complete" | ForEach-Object { $_.Line }; echo "build exit=$LASTEXITCODE"
    docker rmi -f curator-mutant:a curator-mutant:b

Expected B (check restored, pool.ts still missing): **build exit 1**, output
contains `RUN deno check index.ts`, `TS2307 [ERROR]: Cannot find module
'file:///app/pool.ts'` and `did not complete successfully: exit code: 1`. (A
second error, `TS7006 ... 'row' implicitly has an 'any' type`, also appears -
that type flows from the missing pool.ts; it is not present in the real build,
which reports 0 errors.) Both mutant images removed afterwards.

## T6 - the three health surfaces name the curator (read-only, live stack)

Read-only: NO `-Repair`, NO restart. Both scripts run from the worktree so the
edited files are the ones executing.

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg"
    powershell -NoProfile -File scripts/stack/stack.ps1 health 2>&1 | Select-String "research-curator"

Expected: exactly one line, `[FAIL] OB1: research-curator http://127.0.0.1:8816/health`
while production loops (or `[OK]` if the operator has deployed by the time you
run this - say which, and confirm with `docker inspect --format "{{.State.Status}} {{.RestartCount}}" openbrain-curator`).

    powershell -NoProfile -File scripts/checks/check-openbrain-health.ps1 2>&1 | Select-String "openbrain-curator"

Expected: a line naming `openbrain-curator` - today `[DOWN] openbrain-curator
state=restarting (run with -Repair to start)`; after a deploy, `[OK]
openbrain-curator /health db ok (:8816)`. The script's exit code is 1 today
(1 fault) - that is the curator, not a script error.

    Select-String -Path scripts/lib/stack-services.json -Pattern '"container": "openbrain-curator"' -Context 0,4

Expected: the `host_health": "http://127.0.0.1:8816/health"` line inside that
row (within the 4 lines after the `container` line). This file holds no
secrets; the `-Context` here reads four lines of a tracked JSON file.

Negative control: the three `8816` lines must be ADDED by this branch, with
nothing removed. (rev 2: attempt 1 wrote `a07103b..HEAD` here - `a07103b` is
an OB1 SHA, not a parent-repo revision, and git answered `fatal: bad
revision`. The parent base is resolved at run time from the line branch.)

    $base = git merge-base refactor/ai-stack-cleanup HEAD
    echo $base
    git diff "$base..HEAD" -- scripts/stack/stack.ps1 scripts/checks/check-openbrain-health.ps1 scripts/lib/stack-services.json | Select-String -Pattern "^[+-].*8816" | ForEach-Object { $_.Line }

Expected: `$base` prints a parent commit (developer's run: `97c2556`, the
commit `work/curatorimg` was cut from; if the line has moved, whatever
`merge-base` returns), and every match starts with `+`: the `stack.ps1`
probe line, the `check-openbrain-health.ps1` probe line(s) and its header
line, and the JSON `host_health` line; no line starts with `-`.

## T7 - the gitlink is an integration with the line's pin, not just an ancestor

Ancestry is not content (memory: ob1-gitlink-integration-traps). Whatever pin
the reviewer lands, its TREE must be exactly what merging this change onto the
line's pin produces:

    $linePin = (git ls-tree <line-branch> OB1).Split()[2]      # the line's OB1 gitlink at review time
    $pin     = (git ls-tree HEAD OB1).Split()[2]                # the gitlink this item lands
    git -C OB1 fetch origin
    git -C OB1 merge-tree --write-tree $linePin d89c126d9b65b39f9b31d49392f62604becb3485
    git -C OB1 rev-parse "$pin^{tree}"

Expected: the two hashes are IDENTICAL. Today `$linePin` is `a07103b` and
`$pin` is `d89c126`, so the merge-tree is trivially `d89c126^{tree}`
(developer's run, line `refactor/ai-stack-cleanup`: both printed
`027960bd23654df6797e87ced53ee7a25af99e26`). If the line's pin has moved by
review, the reviewer's integration commit becomes `$pin`, this case is re-run
against it, and an earlier pass is stale. A `merge-tree` that prints conflict
markers is a FAIL, not something to resolve in the tester's seat.

## T8 - every case above carries a bare PASS on its heading line

The evidence file's headings must read `## T0 ... PASS` through `## T7 ... PASS`
(and this one). Confirm mechanically:

    (Select-String -Path <evidence.md> -Pattern "^## T\d+.*\bPASS\b").Count

Expected: `9`. A case with FAIL, SKIPPED, scoped, or no verdict is a plan
inadequacy or a fail - it is never rounded up. And the secret rule applies to
the evidence as a whole: `Select-String -Path <evidence.md> -Pattern "PASSWORD=|_KEY="`
must match only the deliberate `DB_PASSWORD=x` from T3/T5 and nothing else.
