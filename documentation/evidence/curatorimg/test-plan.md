# Test plan - curatorimg

Anchor: `queue.ps1 -Show -Id curatorimg`. Branch `work/curatorimg`, worktree
`wt-curatorimg`. Parent code commit `af0faad` (the gitlink bump + the three
health surfaces; this plan and the findings note land in a follow-up commit on
the same branch). **OB1 commit `d89c126d9b65b39f9b31d49392f62604becb3485`** on
OB1 branch `fix/research-curator-dockerfile`, cut from the line's pin `a07103b`.

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

## Environment

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg"
    git rev-parse HEAD                  # af0faad or its docs follow-up
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

## T2 - GREEN: gate 5d passes this item's pin, and the build log shows the check

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-gate5d"
    git -C OB1 fetch origin fix/research-curator-dockerfile
    powershell -NoProfile -File scripts/checks/check-ob1-integration-images.ps1 -OldPin a07103b -NewPin d89c126d9b65b39f9b31d49392f62604becb3485; echo $LASTEXITCODE

Expected: **exit 0**, and the gate's build output contains
`RUN deno check index.ts` for research-curator (the image was type-checked,
not merely assembled). Quote that line. T1 red AND T2 green is the pass; any
other combination is a FAIL of this case with the reason written down.

## T3 - the image built from the pin starts with a dead database

Build from the worktree's OB1 clone (this is the tree the gitlink pins):

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg\OB1\integrations\research-curator"
    docker build --progress=plain --no-cache -t openbrain-curator:wt-curatorimg . 2>&1 | Select-String -Pattern "RUN deno check|Check file:///app/index.ts|COPY \*\.ts|rm -f|naming to"

Expected in the log, in this order: `COPY *.ts ./`, `RUN rm -f ./*.test.ts`,
`RUN deno check index.ts` followed by `Check file:///app/index.ts`, then
`naming to docker.io/library/openbrain-curator:wt-curatorimg`. (Developer's
run: image `597255d787c1`.)

Run it against a host with no Postgres on it, on the default bridge only:

    docker run -d --name curatorimg-t3 -e DB_HOST=127.0.0.1 -e DB_PASSWORD=x -p 127.0.0.1:18816:8000 openbrain-curator:wt-curatorimg
    Start-Sleep 5
    curl.exe -s -m 5 http://127.0.0.1:18816/health
    curl.exe -s -o NUL -w "%{http_code}" -m 5 http://127.0.0.1:18816/health
    docker inspect --format "{{range `$k,`$v := .NetworkSettings.Networks}}{{`$k}} {{end}}" curatorimg-t3

Expected: body is JSON with `"ok":false,"db":false` (a `pool_rebuilds` counter
is also present and climbs - that is the ResilientPool discarding dead pools,
not a fault); HTTP status **503** - the handler's documented db-down status,
the PROCESS is up; networks print exactly `bridge`. Then wait and read the
restart counter:

    Start-Sleep 60
    docker inspect --format "RestartCount={{.RestartCount}} status={{.State.Status}} started={{.State.StartedAt}}" curatorimg-t3
    docker rm -f curatorimg-t3

Expected: `RestartCount=0 status=running`, and `started=` at least 60 s before
the time you read it (quote both timestamps). **Keep the `:wt-curatorimg` tag** -
do not `docker rmi` it. (Developer's run: started 12:17:53Z, read 12:19:25Z,
RestartCount=0.)

## T4 - compose renders the healthcheck and the service_healthy condition

    cd "D:\Open WebUI\ai-stack\.claude\worktrees\wt-curatorimg"
    docker compose -f OB1/docker/docker-compose.yml --env-file OB1/docker/.env config > "$env:TEMP\curatorimg-config.yml"; echo $LASTEXITCODE

Expected: exit 0 (`config` starts nothing). Then read the two service blocks:

    Select-String -Path "$env:TEMP\curatorimg-config.yml" -Pattern "^  openbrain-curator:" -Context 0,60 | Out-String | Select-String -Pattern "healthcheck:|Deno.exit\(r.ok\?0:1\)|interval: 30s|timeout: 5s|retries: 3|start_period: 20s"
    Select-String -Path "$env:TEMP\curatorimg-config.yml" -Pattern "^  openbrain-research:" -Context 0,15 | Out-String

Expected, curator block: `healthcheck:` with a `test:` list whose last element
is `const r=await fetch('http://localhost:8000/health');Deno.exit(r.ok?0:1)`,
`interval: 30s`, `timeout: 5s`, `retries: 3`, `start_period: 20s`.
Expected, research block: under `depends_on:` an `openbrain-curator:` entry
with `condition: service_healthy` (and `required: true`, which compose adds).

Negative control, so the grep is known to bite: the same fields must be ABSENT
at the old pin -

    git -C OB1 show a07103b:docker/docker-compose.yml | Select-String -Pattern "^  openbrain-curator:" -Context 0,45 | Out-String | Select-String "healthcheck"

must print nothing (the curator block at `a07103b` has no healthcheck), and

    git -C OB1 show a07103b:docker/docker-compose.yml | Select-String -Pattern "condition: service_started"

must show the research->curator line that this item replaced (there is exactly
one `service_started` in that file at `a07103b`; at `d89c126` there are none -
confirm with the same command against `HEAD`).

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
    docker build --progress=plain -t curator-mutant:b . 2>&1 | Select-String -Pattern "deno check|TS2307|Type checking failed|did not complete"; echo "build exit=$LASTEXITCODE"
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
row (within the 4 lines after the `container` line).

Negative control: the three `8816` lines must be ADDED by this branch, with
nothing removed -

    git diff a07103b..HEAD -- scripts/stack/stack.ps1 scripts/checks/check-openbrain-health.ps1 scripts/lib/stack-services.json | Select-String -Pattern "^[+-].*8816"

(`a07103b` here is only a convenient parent ref: use the merge-base with the
line branch, `git merge-base HEAD <line-branch>`, if that is not an ancestor).
Expected: every match starts with `+`; the `stack.ps1` probe, the
`check-openbrain-health.ps1` probe and header line, and the JSON `host_health`
all appear; no line starts with `-`.

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
inadequacy or a fail - it is never rounded up.
