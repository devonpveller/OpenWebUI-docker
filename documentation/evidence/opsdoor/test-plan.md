# Test plan - opsdoor

Anchor: `powershell -NoProfile -File scripts/agent-harness/queue.ps1 -Show -Id opsdoor`.
Branch `work/opsdoor`, developer worktree `wt-opsdoor`, base `e72c678`.
**OB1 commit `6fba6b38a789806ade45dd8e4a1b2c76ba118c9f`** on OB1 branch
`fix/ob1-image-revision-label`, cut from the line's pin `d89c126`; the parent
gitlink bump is commit `b9bea9d` on this branch.

**You are testing four claims:**

1. `scripts/stack/ob1-deploy.ps1` is a door that refuses a tree that is not the
   pin, labels the image with the pin, recreates the named dependent, waits for
   healthy and refuses success over a restart loop (T1, T2, T3);
2. `emergency-recovery.ps1` no longer says SUCCESS / "N/M running" over a
   looping OB1 container (T4);
3. `check-openbrain-health.ps1` names a failed research run (T5);
4. the runbooks name the door, and the two OB1 Dockerfiles carry the label and
   build without it (T6, T7).

**Nothing here deploys.** Production `openbrain-*` containers are read
(`docker inspect`, one read-only `SELECT`) and never started, restarted,
recreated or `-Repair`ed. Every container you create is prefixed
`wt-<id>`, every image is tagged `:wt-<id>` or a throwaway tag, nothing joins
an `ai-stack_*` network, `:local` is never tagged. The production recovery
script is **never run** - T4 proves the new function in isolation and reads the
diff. `ob1-deploy.ps1` is run against production ONLY with `-WhatIfOnly` (T1,
T2), which runs nothing but `git ls-tree/rev-parse/status` and
`docker compose config`.

**A case you cannot execute is a plan inadequacy, not a scoped pass.** Use
`queue.ps1 -PlanInadequate` naming the case; never `PASS (scoped)`. **A case
that prints a secret is itself a FAIL** (acceptance line 8): do not paste
`OB1/docker/.env`, do not paste a rendered compose config, do not paste the
scratch `.env` (its value is not a secret, the habit is). If your evidence
contains a `DB_PASSWORD`, `MCP_ACCESS_KEY` or any `*_KEY` value, the case is a
FAIL and the evidence is scrubbed before submission. T8 checks this.

**Every case's result heading carries a bare `PASS` or `FAIL`** - `## T3a PASS`,
not `## T3a PASS (mostly)`.

## Environment

    scripts\agent-harness\new-worktree.ps1 -Id test-opsdoor -Base work/opsdoor
    cd <WT>                              # the path it prints; everything below runs from there
    git rev-parse HEAD                   # b9bea9d or later on work/opsdoor
    git ls-tree HEAD OB1                 # 160000 commit 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f
    git -C OB1 rev-parse HEAD            # must be the same; if not:
    git -C OB1 fetch origin fix/ob1-image-revision-label
    git -C OB1 checkout -q --detach 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f

`<WT>` = your worktree's absolute path. `<S>` = a directory in your scratchpad,
e.g. `...\scratchpad\wt-test-opsdoor`. No plane lease: nothing here touches a
running plane. Docker, `git`, `node`, `deno` on PATH (the commit hooks need the
last two; the door needs only docker + git).

Two tool traps seen while writing this plan, so you do not lose time to them:

- The PowerShell tool's guard blocks `Remove-Item -Recurse -Force "<path with a
  space>"` AND misreads `docker rm -f` as a protected-path removal. Use
  `docker stop` on `--rm` containers, `docker compose ... down -v` for the
  project, and Git Bash `rm -rf` for scratch directories.
- `docker inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'`
  (the anchor's literal command) works from **Git Bash** and FAILS from
  PowerShell 5.1 (`template parsing error: function "org" not defined`) - PS
  re-quotes native args containing spaces without escaping the inner quotes,
  and the `\"` form breaks too. The PowerShell equivalent is
  `(docker inspect <name> | ConvertFrom-Json).Config.Labels.'org.opencontainers.image.revision'`.
  Both forms are accepted wherever a label is read below.

---

## T0 - reachability and tree equality. Do this FIRST.

    git -C OB1 ls-remote origin fix/ob1-image-revision-label
    git -C OB1 rev-parse HEAD
    git ls-tree HEAD OB1
    git -C OB1 diff --stat d89c126d9b65b39f9b31d49392f62604becb3485 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f
    git merge-tree --write-tree refactor/ai-stack-cleanup work/opsdoor
    git ls-tree <the tree it printed> OB1

Expected: the first three all show `6fba6b38a789806ade45dd8e4a1b2c76ba118c9f`.
If `ls-remote` does not, **FAIL the item immediately** (unreachable gitlink; a
fresh `--recurse-submodules` clone breaks). The diff-stat lists EXACTLY
`integrations/research-curator/Dockerfile | 8 ++++++++` and
`integrations/research-service/Dockerfile | 8 ++++++++`, `2 files changed, 16
insertions(+)` - this item's OB1 change is those two files and nothing else
(a sibling item edits research-service/index.ts on ITS OWN OB1 branch; if
that content appears here, FAIL). `merge-tree` prints one tree id with no
conflict block, and `ls-tree <tree> OB1` pins `6fba6b3...` (the line's pin was
`d89c126` when this plan was written: `git ls-tree refactor/ai-stack-cleanup OB1`).
If the line's pin has MOVED since (a sibling merged), `merge-tree` reports a
conflict on `OB1` - record it verbatim; that is the reviewer's integration
merge to resolve, not a FAIL of this case, but it must be in your report.

## T1 - `-WhatIfOnly` prints the plan and touches nothing (acceptance 1)

    $before = (docker ps -a -q | Sort-Object) -join ','
    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -WhatIfOnly; echo $LASTEXITCODE
    $after = (docker ps -a -q | Sort-Object) -join ','
    $before -eq $after

Expected: exit `0`; the output has one `OB1 pin 6fba6b3... == disk HEAD (ok)`
line and a plan block with these lines (paths will be yours):

    pin        : 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f
    service    : openbrain-curator  (image openbrain-curator:local, context <WT>\OB1\integrations\research-curator)
    build      : docker compose -f OB1\docker\docker-compose.yml build --build-arg OB1_SHA=6fba6b38a789806ade45dd8e4a1b2c76ba118c9f openbrain-curator
    up         : docker compose -f OB1\docker\docker-compose.yml up -d openbrain-curator
    poll       : docker inspect <openbrain-curator> until State.Health.Status == healthy, bound 155 s (start_period 20 s + retries 3 x (interval 30 s + timeout 5 s) + interval), then no restart for 60 s
    recreate   : docker compose -f OB1\docker\docker-compose.yml up -d --no-deps --force-recreate openbrain-research   -> watch: running, no restart for 60 s (no healthcheck)
    label      : docker inspect -> Config.Labels[org.opencontainers.image.revision] must equal the pin

then `-WhatIfOnly: nothing was built, started or recreated ...`. `$before -eq
$after` is `True`. Check the bound arithmetic against the curator's compose
healthcheck yourself (`OB1/docker/docker-compose.yml`, service
`openbrain-curator`, `healthcheck:` block): 20 + 3 x (30 + 5) + 30 = 155.

Negative half: a pinned-image service is refused as not this door's job:

    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-db -WhatIfOnly; echo $LASTEXITCODE

Expected: exit `2`, one line `FAIL: service 'openbrain-db' has no 'build:' - it
is a pinned image (pgvector/pgvector:pg16) ...`.

## T2 - the refusal when OB1 on disk is not the gitlink (acceptance 2)

    git -C OB1 checkout -q d89c126d9b65b39f9b31d49392f62604becb3485
    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -WhatIfOnly; echo $LASTEXITCODE
    git -C OB1 checkout -q --detach 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f
    git -C OB1 rev-parse HEAD

Expected: exit `3` and ONE sentence that names BOTH SHAs in full:

    [ob1-deploy] FAIL: REFUSED: OB1 on disk is at d89c126d9b65b39f9b31d49392f62604becb3485 but the parent's gitlink (git ls-tree HEAD OB1) pins 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f. An image built now would be labelled 6fba6b3 yet built from d89c126. Check OB1 out at the pin (...) or bump the gitlink first - gates 5b/5c/5d apply the same rule.

No plan block is printed (the refusal is before the compose render). The last
line restores `6fba6b3...`. **Do not skip the restore** - every later case
depends on it, and the door will refuse them until you do.

## T3 - the isolated compose project (acceptance 3)

### Setup (once)

Create `<S>` and write these three files into it.

`<S>\.env` - set `WT` to your worktree path, forward slashes are fine:

    POSTGRES_PASSWORD=wt-test-opsdoor-scratch-not-a-secret
    WT=D:/Open WebUI/ai-stack/.claude/worktrees/wt-test-opsdoor

`<S>\init-research-jobs-scratch.sql` - `OB1/docker/init-research-jobs.sql`
minus the threads/sessions foreign keys, the view, RLS and the grants (the
scratch DB has none of those objects); same columns, same touch trigger:

    CREATE TABLE IF NOT EXISTS public.research_jobs (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        status      TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','done','error','cancelled')),
        origin      TEXT NOT NULL DEFAULT 'owui'
            CHECK (origin IN ('owui','agent','notebook','manual')),
        query       TEXT NOT NULL,
        thread_id   UUID,
        session_id  UUID,
        options     JSONB NOT NULL DEFAULT '{}'::jsonb,
        progress    JSONB NOT NULL DEFAULT '{}'::jsonb,
        result      JSONB,
        metrics     JSONB,
        error       TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        started_at  TIMESTAMPTZ,
        finished_at TIMESTAMPTZ
    );
    CREATE OR REPLACE FUNCTION public.research_jobs_touch_updated_at()
    RETURNS TRIGGER AS $$
    BEGIN
        NEW.updated_at = now();
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS trg_research_jobs_touch ON public.research_jobs;
    CREATE TRIGGER trg_research_jobs_touch
        BEFORE UPDATE ON public.research_jobs
        FOR EACH ROW EXECUTE FUNCTION public.research_jobs_touch_updated_at();

`<S>\docker-compose.yml` - derived from `OB1/docker/docker-compose.yml`
keeping only the three services; project name `wt-test-opsdoor`, container
names prefixed, images `:wt-test-opsdoor`, its own default network (no
`networks:` block at all, so compose creates `wt-test-opsdoor_default`),
ports on `127.0.0.1:28816/28818`. The curator's `healthcheck:` and both
services' `restart: unless-stopped` are copied verbatim from production; the
build contexts point at YOUR worktree's OB1 (the pinned tree). LLM/persist/
search endpoints point at `127.0.0.1:9` (nothing is called at boot; the
curator's `/health` needs only the DB). `DB_USER` is `postgres` because the
scratch DB has no `ob_app_memory` role. `FETCH_PROXY_URL: ""` is the research
service's explicit DIRECT choice (its startup probe otherwise refuses to start
without `--unstable-net` - see `research-service/index.ts` ~:148).

    name: wt-test-opsdoor

    services:
      openbrain-db:
        image: pgvector/pgvector:pg16
        container_name: wt-test-opsdoor-openbrain-db
        environment:
          POSTGRES_DB: openbrain
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
        volumes:
          - ./init-research-jobs-scratch.sql:/docker-entrypoint-initdb.d/010-research-jobs.sql:ro
        healthcheck:
          test: ["CMD-SHELL", "pg_isready -U postgres -d openbrain"]
          interval: 10s
          timeout: 5s
          retries: 10
          start_period: 20s
        restart: unless-stopped

      openbrain-curator:
        build:
          context: ${WT}/OB1/integrations/research-curator
          dockerfile: Dockerfile
        image: openbrain-curator:wt-test-opsdoor
        container_name: wt-test-opsdoor-openbrain-curator
        depends_on:
          openbrain-db:
            condition: service_healthy
        environment:
          DB_HOST: openbrain-db
          DB_PORT: "5432"
          DB_NAME: openbrain
          DB_USER: postgres
          DB_PASSWORD: ${POSTGRES_PASSWORD}
          MCP_ACCESS_KEY: scratch-not-a-secret
          PERSIST_URL: http://127.0.0.1:9
          EMBEDDING_API_BASE: http://127.0.0.1:9/v1
          CHAT_API_BASE: http://127.0.0.1:9/v1
          PORT: "8000"
        ports:
          - "127.0.0.1:28816:8000"
        healthcheck:
          test: ["CMD", "deno", "eval", "const r=await fetch('http://localhost:8000/health');Deno.exit(r.ok?0:1)"]
          interval: 30s
          timeout: 5s
          retries: 3
          start_period: 20s
        restart: unless-stopped

      openbrain-research:
        build:
          context: ${WT}/OB1/integrations/research-service
          dockerfile: Dockerfile
        image: openbrain-research:wt-test-opsdoor
        container_name: wt-test-opsdoor-openbrain-research
        depends_on:
          openbrain-db:
            condition: service_healthy
          openbrain-curator:
            condition: service_healthy
        environment:
          DB_HOST: openbrain-db
          DB_PORT: "5432"
          DB_NAME: openbrain
          DB_USER: postgres
          DB_PASSWORD: ${POSTGRES_PASSWORD}
          MCP_ACCESS_KEY: scratch-not-a-secret
          OWUI_API_KEY: ""
          CURATOR_URL: http://openbrain-curator:8000
          SEARCH_API_BASE: http://127.0.0.1:9
          EMBEDDING_API_BASE: http://127.0.0.1:9/v1
          CHAT_API_BASE: http://127.0.0.1:9/v1
          FETCH_PROXY_URL: ""
          PORT: "8000"
        ports:
          - "127.0.0.1:28818:8000"
        restart: unless-stopped

Bring it up ONCE with plain compose so a baseline exists (this build passes no
`OB1_SHA`, so the baseline label is EMPTY - which is what the door then fixes):

    docker compose -f "<S>\docker-compose.yml" --env-file "<S>\.env" up -d --build
    docker ps --filter "name=wt-test-opsdoor-" --format "{{.Names}}|{{.Status}}|{{.Networks}}"
    (docker inspect wt-test-opsdoor-openbrain-curator | ConvertFrom-Json).Config.Labels.'org.opencontainers.image.revision'

Expected: three containers, db and curator `(healthy)` within ~30 s, research
`Up`; every `Networks` column reads exactly `wt-test-opsdoor_default`; the
label prints an empty line. If research is not `Up` within a minute, read
`docker logs wt-test-opsdoor-openbrain-research` before going on - the scratch
table above is what its boot-time orphan recovery needs.

### T3a - the door deploys, labels, recreates the dependent, waits, exits 0

    $t = Measure-Command { powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -ComposeFile "<S>\docker-compose.yml" -EnvFile "<S>\.env" | Tee-Object -Variable out }
    $out | Select-String 'ob1-deploy\]|pin  |service  |label  |recreated  |result  |restarts=' | ForEach-Object { $_.Line }
    echo $LASTEXITCODE; $t.TotalSeconds
    (docker inspect wt-test-opsdoor-openbrain-curator | ConvertFrom-Json).Config.Labels.'org.opencontainers.image.revision'

Expected, in this order: `OB1 pin 6fba6b3... == disk HEAD (ok)` with NO
`WARN: build context ... OUTSIDE` line (the context IS the pinned tree - a
forward-slash `WT` must not trip it); the plan block; `build:`; `up:`;
`watch: openbrain-curator (loop window 60 s)` followed by
`openbrain-curator : status=running health=starting ...` then
`health=healthy` lines up to `(60 s)`; `recreate: ... up -d --no-deps
--force-recreate openbrain-research`; `watch: openbrain-research` with
`health=none restarts=0` lines to ~60 s; then the summary:

    pin        : 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f
    service    : container=wt-test-opsdoor-openbrain-curator image=openbrain-curator:wt-test-opsdoor status=running health=healthy restarts=0  recreated (<old StartedAt> -> <new StartedAt>)
    label      : 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f vs pin 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f -> MATCH
    recreated  : openbrain-research  StartedAt moved (<old> -> <new>)  status=running health=none restarts=0
    result     : OK - openbrain-curator deployed at 6fba6b3, watched 60 s clean

exit `0`; ~130-140 s; the last command prints the pin (the developer's run:
exit 0, 135 s, both StartedAt values moved). Anything short of `MATCH`, or
`StartedAt UNCHANGED` for research, or a `restarts=` above 0, is a FAIL.

### T3b - a broken Dockerfile in a scratch copy fails AT BUILD, exit 4

Copy the curator into `<S>` and break it (Git Bash for the copy; the PS guard
trips on `Remove-Item -Recurse` if you need to redo it):

    cp -r "<WT>/OB1/integrations/research-curator" "<S>/broken-curator"
    printf '\nRUN echo deliberately-broken-for-T3b && exit 7\n' >> "<S>/broken-curator/Dockerfile"
    sed 's#${WT}/OB1/integrations/research-curator#./broken-curator#' "<S>/docker-compose.yml" > "<S>/docker-compose.broken.yml"
    grep -n broken-curator "<S>/docker-compose.broken.yml"      # one hit: the curator's context

Then (PowerShell):

    powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -ComposeFile "<S>\docker-compose.broken.yml" -EnvFile "<S>\.env" | Select-String 'WARN|FAIL|deliberately|result' | ForEach-Object { $_.Line }; echo $LASTEXITCODE
    docker ps --filter "name=wt-test-opsdoor-" --format "{{.Names}}|{{.Status}}"

Expected: ONE `WARN: build context '<S>\broken-curator' is OUTSIDE this
checkout's OB1 ...` line (correct - it is), the build log's
`deliberately-broken-for-T3b` and `exit code: 7`, then
`FAIL: build of openbrain-curator failed (exit 1) - nothing was started; the
running container is untouched.`; exit `4`; no `up:`, `watch:` or `recreate:`
line; `docker ps` shows the SAME three containers still `Up` / `(healthy)` from
T3a (the T3a curator's `Status` age keeps counting - it was not recreated).

### T3c - a curator that starts and dies is named within ~60 s, exit 6

Build the loop variant: dead `DB_HOST` plus a command that exits (Git Bash):

    python - <<'EOF'
    S = "<S with forward slashes>"
    t = open(S + "/docker-compose.yml").read()
    t = t.replace("DB_HOST: openbrain-db", "DB_HOST: 127.0.0.1", 1)
    t = t.replace("container_name: wt-test-opsdoor-openbrain-curator",
                  "container_name: wt-test-opsdoor-openbrain-curator\n    command: [\"sh\", \"-c\", \"echo 'synthetic crash: DB_HOST unreachable'; exit 1\"]", 1)
    open(S + "/docker-compose.loop.yml", "w").write(t)
    EOF
    grep -n 'command:\|DB_HOST: 127' "<S>/docker-compose.loop.yml"     # 2 hits, both in the curator block

Then (PowerShell):

    $t = Measure-Command { powershell -NoProfile -File scripts/stack/ob1-deploy.ps1 -Service openbrain-curator -Recreate openbrain-research -ComposeFile "<S>\docker-compose.loop.yml" -EnvFile "<S>\.env" | Tee-Object -Variable out }
    $out | Select-String 'FAIL|RESTART|result|synthetic|recreated|dependents|watch:' | ForEach-Object { $_.Line }
    echo $LASTEXITCODE; $t.TotalSeconds
    docker ps -a --filter "name=wt-test-opsdoor-" --format "{{.Names}}|{{.Status}}"

Expected: `watch: openbrain-curator (loop window 60 s)` then, within seconds,
`FAIL: RESTART LOOP: wt-test-opsdoor-openbrain-curator status=restarting
restarts=<n> after <k> s` followed by the container's last log lines
(`synthetic crash: DB_HOST unreachable`, up to 20 of them), then
`WARN: dependents NOT recreated because openbrain-curator failed its watch:
openbrain-research`, and a summary whose `recreated` line says
`openbrain-research  StartedAt UNCHANGED (...)` and whose `result` line is
`FAILED - wt-test-opsdoor-openbrain-curator`; exit `6`; total well under 60 s
(the developer's run: 11 s). `docker ps -a` shows the curator cycling
(`Restarting (1) ...` or `Up Less than a second (health: starting)`), research
still `Up` from T3a. Note what compose itself printed just before: `Container
wt-test-opsdoor-openbrain-curator Started` - compose reports creation, not
survival; the watch is the only thing that saw the loop.

### Teardown

    docker compose -f "<S>\docker-compose.loop.yml" --env-file "<S>\.env" down -v
    docker rmi openbrain-curator:wt-test-opsdoor openbrain-research:wt-test-opsdoor
    (docker ps -a --filter "name=wt-test-opsdoor-" -q).Count; (docker network ls --filter "name=wt-test-opsdoor" -q).Count

Expected: three `Removed` container lines + the network; two `Untagged`
lines; `0` and `0`.

## T4 - emergency-recovery.ps1 names a looping container (acceptance 4)

**Do not run `scripts/recovery/emergency-recovery.ps1`.** It manages the live
planes. The function is extracted from the file's AST and called with a
synthetic `docker ps` and a stub log command.

### T4a - the function, in isolation

    $src = Get-Content -Raw scripts\recovery\emergency-recovery.ps1
    $ast = [System.Management.Automation.Language.Parser]::ParseInput($src, [ref]$null, [ref]$null)
    $fn = $ast.FindAll({ $args[0] -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $args[0].Name -eq 'Wait-ForRestartLoops' }, $true) | Select-Object -First 1
    function Write-Log { param($Level, $Message) Write-Host "[$Level] $Message" }
    Invoke-Expression $fn.Extent.Text
    $ps   = { "openbrain-db|Up 6 hours (healthy)"; "openbrain-curator|Restarting (1) 3 seconds ago"; "openbrain-research|Up 20 minutes" }
    $logs = { param($Name) "error: Uncaught (in promise) TypeError: Module not found `"file:///app/pool.ts`"."; "    at async file:///app/index.ts:39:1" }
    $r = Wait-ForRestartLoops -Seconds 0 -PollSeconds 0 -PsCommand $ps -LogsCommand $logs
    "returned: count={0} names={1}" -f @($r).Count, (@($r) -join ',')
    $r2 = Wait-ForRestartLoops -Seconds 0 -PollSeconds 0 -PsCommand { "openbrain-db|Up 6 hours (healthy)" } -LogsCommand $logs
    "clean: count={0}" -f @($r2).Count

Expected, verbatim:

    [WARN] RESTART LOOP: openbrain-curator (Restarting (1) 3 seconds ago) - last 5 log lines:
    [WARN]     openbrain-curator | error: Uncaught (in promise) TypeError: Module not found "file:///app/pool.ts".
    [WARN]     openbrain-curator |     at async file:///app/index.ts:39:1
    returned: count=1 names=openbrain-curator
    clean: count=0

The WARN names the container, carries the log tail, and the clean input
returns an empty array (not `$null`, not a one-element unrolled string:
`@($r2).Count` is `0`). Add one refutation of your own: a line whose status is
`Up 2 seconds` for a container that looped a moment ago must NOT be flagged
by a single sample - that is why the real call polls for 60 s; prove the
polling by `-Seconds 2 -PollSeconds 1` with a `-PsCommand` that returns
`Restarting` only on its second call (a script-scope counter) and check the
name is still returned.

### T4b - the real `docker ps` shape matches what the parser expects (read-only)

    docker ps -a --filter "label=com.docker.compose.project=open-brain" --format "{{.Names}}|{{.Status}}" | Select-Object -First 3

Expected: lines shaped `openbrain-<x>|Up <age> (healthy)` - one `|`, name
first, the status text the function matches `^Restarting` against. (This is
the function's default `-PsCommand`, run by hand; it reads.)

### T4c - the call sites and the summary line (read the diff)

    git diff e72c678..HEAD -- scripts/recovery/emergency-recovery.ps1

Expected: `Start-OB1Stack` and `Reset-OB1Stack` each call
`Wait-ForRestartLoops -Seconds 60` immediately after their `up -d` and log
SUCCESS only when `$looping.Count -eq 0`, else a WARN that lists the names;
the status block (`OB1    - N/M openbrain containers running`) gains a branch
that logs `...running; RESTART LOOP: <names>` from `State -eq "restarting"`
in the `docker compose ps --format json` it already parses; a new
`$Script:OB1Project = "open-brain"`. Nothing else in the file changes
(`git diff --stat e72c678..HEAD -- scripts/recovery/emergency-recovery.ps1` names that one file), in
particular NO `build` is added anywhere (rebuild is a deploy, out of scope):
`git diff e72c678..HEAD -- scripts/recovery/emergency-recovery.ps1 | grep '^+.*build'` is empty.
Parse gate: the pre-commit's PSParser tokenize -

    $e = $null; [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw scripts\recovery\emergency-recovery.ps1), [ref]$e) | Out-Null; @($e).Count

prints `0`.

## T5 - check-openbrain-health.ps1 names a failed research run (acceptance 5)

A scratch Postgres on the default bridge, no port, `--rm` so `docker stop`
removes it. **Never `-Repair`, never a write against `openbrain-db`.**

    docker run -d --rm --name wt-test-opsdoor-pg -e POSTGRES_PASSWORD=scratch-only -e POSTGRES_DB=openbrain pgvector/pgvector:pg16
    foreach ($i in 1..30) { Start-Sleep 1; docker exec wt-test-opsdoor-pg pg_isready -U postgres -d openbrain 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { break } }
    docker exec wt-test-opsdoor-pg psql -U postgres -d openbrain -q -c "CREATE TABLE IF NOT EXISTS public.research_jobs (id uuid primary key default gen_random_uuid(), status text not null default 'queued', query text not null, error text, created_at timestamptz not null default now(), updated_at timestamptz not null default now())"

### T5a - no rows -> OK line

    powershell -NoProfile -File scripts\checks\check-openbrain-health.ps1 -DbContainer wt-test-opsdoor-pg | Select-String 'research_jobs'

Expected: `  [OK]    research_jobs              no status='error' rows in 24 h (wt-test-opsdoor-pg)`.

### T5b - one error row -> WARN naming count + newest id + error, exit 1

    docker exec wt-test-opsdoor-pg psql -U postgres -d openbrain -tA -c "INSERT INTO public.research_jobs (status, query, error) VALUES ('error', 'opsdoor test', E'synthetic failure\nline two | with pipe and a very long tail that must be cut at eighty characters exactly here and beyond') RETURNING id"
    powershell -NoProfile -File scripts\checks\check-openbrain-health.ps1 -DbContainer wt-test-opsdoor-pg -Quiet; echo $LASTEXITCODE

Expected: the INSERT prints one uuid; the script prints
`  [WARN]  research_jobs              1 error(s) in 24 h; newest <that uuid>: synthetic failure line two with pipe and a very long tail that must be cut at ei`
- the newline and the `|` in the stored error are flattened to spaces (the
line stays one line; the `|` could not be a field separator) and the text is
cut at 80 characters; exit `1` (the fault counted). Insert a SECOND error row
and re-run: `2 error(s)` and the newest id is the second uuid. Insert a row
with `status='done'` and an `error`: the count does not change (status,
not error text, is the criterion).

### T5c - the table missing -> a named query failure, still a fault

    docker exec wt-test-opsdoor-pg psql -U postgres -d openbrain -q -c "ALTER TABLE public.research_jobs RENAME TO research_jobs_gone"
    powershell -NoProfile -File scripts\checks\check-openbrain-health.ps1 -DbContainer wt-test-opsdoor-pg | Select-String 'research_jobs'
    docker stop wt-test-opsdoor-pg

Expected: `  [WARN]  research_jobs              query failed on wt-test-opsdoor-pg: ERROR:  relation "public.research_jobs" does not exist` (no `docker.exe :` prefix).

### T5d - production, read-only, and no password in the command

    powershell -NoProfile -File scripts\checks\check-openbrain-health.ps1 | Select-String 'research_jobs'
    git show HEAD:scripts/checks/check-openbrain-health.ps1 | Select-String 'psql'

Expected: exactly one `research_jobs` line, `[OK]` or `[WARN]` depending on
what production has had in the last 24 h - report which, it is a fact about
the stack, not about this case; the only `psql` line in the script is
`docker exec $DbContainer psql -U postgres -d $DbName -tA -v ON_ERROR_STOP=1 -c $rjSql`
- no `-W`, no `PGPASSWORD`, no credential read anywhere. (`docker exec ...
psql -U postgres` authenticates over the container's unix socket; the same
pattern `scripts/checks/smoke-agent-memory-live.ps1:45` uses.) No `-Repair`.
Count the production rows yourself, read-only, to check the line's arithmetic:

    docker exec openbrain-db psql -U postgres -d openbrain -tA -c "SELECT count(*) FROM public.research_jobs WHERE status='error' AND updated_at >= now() - interval '24 hours'"

## T6 - the runbooks name the door (acceptance 6)

    grep -n 'ob1-deploy' documentation/runbooks/UPDATE-MANAGEMENT.md documentation/runbooks/SERVICE-LIFECYCLE.md
    git diff e72c678..HEAD -- documentation/runbooks/

Expected: hits in BOTH files - UPDATE-MANAGEMENT.md inside "## Everything
else" (a paragraph beginning "**A service with `build:` is not a pinned image
- it deploys through the door, never `up -d` alone.**" with the two example
commands), SERVICE-LIFECYCLE.md as table row `| 11 | **Deploy door
(`build:` services)** |` plus one line in "The one-command checks". Then the
MERGE-PROTOCOL check: **for every command the runbook tells a reader to run,
check its described effect against the script.** The paragraph claims the door
(a) refuses when disk != gitlink - T2; (b) builds with `--build-arg
OB1_SHA=<pin>` - T3a's `build:` line; (c) force-recreates the dependents you
name - T3a's `recreate:` line; (d) waits for healthy and exits non-zero naming
a looping container within 60 s - T3c; (e) the health script "also names
research_jobs rows that ended in error in the last 24 h" - T5b. Any claim you
cannot tie to a case above is a FAIL of this case, with the sentence quoted.

## T7 - the two Dockerfiles carry the label and build without it (acceptance 7)

    git -C OB1 show 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f:integrations/research-curator/Dockerfile | Select-String 'ARG OB1_SHA|LABEL org.opencontainers'
    git -C OB1 show 6fba6b38a789806ade45dd8e4a1b2c76ba118c9f:integrations/research-service/Dockerfile | Select-String 'ARG OB1_SHA|LABEL org.opencontainers'
    docker build -q -t ob1-t7/curator:noarg OB1\integrations\research-curator; echo $LASTEXITCODE
    docker build -q --build-arg OB1_SHA=6fba6b38a789806ade45dd8e4a1b2c76ba118c9f -t ob1-t7/curator:witharg OB1\integrations\research-curator; echo $LASTEXITCODE
    "noarg='" + (docker inspect ob1-t7/curator:noarg | ConvertFrom-Json).Config.Labels.'org.opencontainers.image.revision' + "'"
    "witharg='" + (docker inspect ob1-t7/curator:witharg | ConvertFrom-Json).Config.Labels.'org.opencontainers.image.revision' + "'"
    docker rmi ob1-t7/curator:noarg ob1-t7/curator:witharg

Expected per file: `ARG OB1_SHA=` and
`LABEL org.opencontainers.image.revision="$OB1_SHA"` (both after `FROM`,
before `WORKDIR`); both builds exit `0`; `noarg=''` (empty, NOT a failure) and
`witharg='6fba6b38a789806ade45dd8e4a1b2c76ba118c9f'`. Repeat the four build/
inspect lines for `research-service`. Push + gitlink + tree equality are T0.

## T8 - no secret in the evidence; every heading carries a bare PASS/FAIL (acceptance 8)

    Select-String -Path <your evidence file> -Pattern 'POSTGRES_PASSWORD=|DB_PASSWORD=|MCP_ACCESS_KEY=|_KEY=|sk-[A-Za-z0-9]{8}|PGPASSWORD'

Expected: no hit except the literal scratch values this plan itself prints
(`scratch-only`, `scratch-not-a-secret`, `wt-test-opsdoor-scratch-not-a-secret`)
- if ANY other value appears, scrub it and FAIL T8 with the case that leaked
it. Then every `## T<n>` result heading reads `PASS` or `FAIL` and nothing
else after it.

---

## Developer's self-verification (2026-09-06, wt-opsdoor)

Not the tester's evidence - recorded so the tester knows what a pass looked
like. Every command above was run here first; the outputs quoted in the
"Expected" blocks are from these runs (the isolated project was named
`wt-opsdoor`, containers `wt-opsdoor-openbrain-*`, in this worktree's
scratchpad). Results: T1 exit 0 / `$before -eq $after` True / exit 2 for
openbrain-db; T2 exit 3 both directions (disk 6fba6b3 vs pin d89c126 before
the bump, disk d89c126 vs pin 6fba6b3 after); T3a exit 0 in 135 s, label
MATCH, research `StartedAt moved`; T3b exit 4 at `exit code: 7`, three
containers untouched; T3c exit 6 in 11 s naming `wt-opsdoor-openbrain-curator`
with five `synthetic crash` log lines; T4a the five verbatim lines; T5a/b/c
the three lines quoted, exit 1 with the row present; T7 both images, empty
label without the arg, the pin with it. The one defect these runs found and
fixed before this plan: a forward-slash `WT` in the scratch `.env` made the
door call the pinned tree "OUTSIDE this checkout's OB1" (slash-normalisation
added; T3a's "no WARN" expectation is the regression check).
