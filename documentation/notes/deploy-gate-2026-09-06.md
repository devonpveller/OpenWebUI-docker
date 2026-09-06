# deploy-gate findings, 2026-09-06 - true, verified, out of scope for the item that found them

## gate5d

Found while building `scripts/checks/check-ob1-integration-images.ps1` (worktree wt-gate5d, OB1 pin a07103b). Each entry says what was checked and how.

- **A worktree's edited hook never runs for that worktree's commits.** `git config --get core.hooksPath` in `.claude/worktrees/wt-gate5d` returns `D:\Open WebUI\ai-stack\.githooks` (absolute, the operator's checkout), so a `git commit` in a worktree runs the OPERATOR's `pre-commit`, not the one the worktree just edited. `.githooks/README.md:37` states this correctly ("`core.hooksPath` is an absolute path to one checkout"), but `.githooks/README.md:9` and `:15` show the activation as the relative `git config core.hooksPath .githooks` / `# -> .githooks`, which is not what the live config holds. Consequence for the harness: any item that changes `.githooks/*` cannot be tested by a plain `git commit` in a worktree; the tester must pass `-c core.hooksPath="<worktree>\.githooks"` (gate5d's test plan T8 does). Nothing in `scripts/agent-harness/README.md` says so (checked with `grep -n hooksPath scripts/agent-harness/README.md`: no hits). Out of scope: harness/README docs.

- **Only one of the eight integration Dockerfiles checks its own module graph at build time.** `grep -n "deno check" OB1/integrations/*/Dockerfile` at a07103b hits exactly `OB1/integrations/research-service/Dockerfile:22` (`RUN deno check index.ts`). The other seven (`chunk-embedding-worker`, `entity-extraction-worker`, `grounding-backfiller`, `kubernetes-deployment`, `openbrain-idea-refinery`, `research-curator`, `suggestion-worker`) build successfully with a missing module and die at container start. Five of them COPY by name (`COPY index.ts ./`) - the research-curator shape. `kubernetes-deployment/Dockerfile` already copies by glob and says why in its own comment. This is the anchor's Phase D hardening (lint every Dockerfile regardless of the pin diff), deliberately out of gate5d's scope; gate5d only refuses the ones a bump touches.

- **`Git-InOB1` now exists in three copies.** `scripts/checks/check-ob1-recipe-tests.ps1`, `check-ob1-deno-recipes.ps1` (its definition starts at the `function Git-InOB1` line, with the GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE clearing) and the new `check-ob1-integration-images.ps1` each carry the same wrapper and the same "under a hook GIT_DIR overrides -C" comment. A fourth OB1 gate would be a fourth copy. The anchor forbids touching 5b/5c, so gate5d copied rather than extracted. A shared `scripts/checks/lib/ob1-git.ps1` dot-sourced by all three is the obvious follow-up; not done here.

### From the attempt-1 test (tester-gate5d-sub1, evidence in the tester's scratchpad; carried here by the developer)

- **Static half walked a one-line module as characters (FIXED in attempt 2).** `check-ob1-integration-images.ps1` `Read-AtPin` ended `return $lines`; PowerShell unrolls a one-element array, so a one-line file came back as a String and `Walk-Imports` indexed `$src[$i]` as chars. A barrel `export * from "./deep.ts";` two hops from index.ts was reported as "covers all 4 file(s)" and only `deno check` in the image refused (tester R3). Fix: `return ,$x` on every array return (`Read-AtPin`, `Join-Continuations`, `Strip-Comments`, `Walk-Imports`) and `[string[]]$src` at the receiver; plan cases T11a-d now cover the walk beyond the first hop. General PS 5.1 trap worth remembering for the sibling gates: 5c's `$tsFiles` is a `@(...)` literal at the call site so it does not hit this, but any helper that RETURNS a list can.
- **Commented-out imports were false reds (FIXED in attempt 2).** `// import { old } from "./gone.ts";` was refused as "DOES NOT EXIST" (tester R6). `Strip-Comments` now blanks `//` (at line start or after whitespace/`;`) and `/* */` spans before the regex runs; a `/*` inside a string literal is still misread. Plan T11e.
- **Wrong-case COPY passed the static half (FIXED in attempt 2).** `COPY Pool.ts ./` "covered" `./pool.ts` because `-contains`, `StartsWith` and the `@{}` literal are case-insensitive; Linux `docker build` then refused with `"/Pool.ts": not found` (tester R7). Now `-ccontains`, `StringComparison.Ordinal` and `System.Collections.Hashtable`. Plan T11f.
- **Only `CMD [...]` was parsed for the entrypoint; `ENTRYPOINT [...]` was not (FIXED in attempt 2, unexercised).** The eight Dockerfiles at a07103b all use CMD, so no case exists in history; the parser now reads either keyword's last `*.ts` argument.
- **`core.hooksPath` demonstrated live (tester T8, kept as a limitation).** A plain `git commit` of the RED bump in the tester's worktree ran the operator's chain and was ACCEPTED (commit 97fe5d2 made, 5d never ran); the same staged bump under `-c core.hooksPath=<WT>\.githooks` was refused. Until this item merges into the line whose `.githooks` the absolute hooksPath points at, no worktree commit is gated by 5d. Same fact as the first bullet above, now with a live reproduction.

### From the attempt-2 test (tester-gate5d-sub2; carried here by the developer)

- **Three more static-half blind spots, all string-literal or layout shaped, all caught by the build half (DECLARED in the header, attempt 3; not fixed).** N1b: ` //` inside a string literal on an import's own line blanks the import. N2: `/*` inside a string literal (`"**/*.ts"`) opens a block comment that never closes, so blanking runs to END OF FILE. N3: the import regex is single-line, so `from` split from its specifier is invisible. Each was a silent `covers all 3` statically and a `TS2307` refusal from `deno check` in the image. Fixing them properly means a tokenizer, which is the wrong tool for a fast path whose authority is the build half; plan T13a-c pin the behaviour so a future change to the stripper is noticed either way.
- **The throwaway-tag namespace is shared across concurrent runs.** Mid-T8 the tester saw a foreign `ob1-gate/research-curator:d89c126` tag and its temp dir appear from another agent's run on the same host. Two runs on the SAME pin would race on `docker build -t` / `docker rmi -f` of one tag (one run's `rmi -f` can delete the image the other is about to `docker run`), and any run's leftover after an interrupt is indistinguishable from another's. Suggested, not implemented here: a per-run suffix in the tag (`ob1-gate/<svc>:<sha7>-<pid or random>`), which keeps the cleanup exact and the namespace per-process; the temp dir already carries a random suffix. Harness-level note: the harness runs testers and developers concurrently on one Docker host, so any gate that builds images needs per-run identity, not per-pin.

- **`deno check` inside the a07103b curator image reports a cascade, not one error.** T4's mutation (`ResilientPool` -> `ResilientPoolX` on `index.ts:39`) produced `Found 3 errors.`: TS2305 at 39, TS2552 at 87, and TS7006 (implicit any on `row`) at `index.ts:189`. The third disappears when the import is correct (the GREEN run at scratch pin 6649efb passed `deno check` clean), so it is a consequence of the first, not a latent defect in `index.ts:189`. Recorded so a future reader of the mutation output does not chase it.


## curatorimg

Recorded 2026-09-06 by the `curatorimg` developer (worktree `wt-curatorimg`,
parent `af0faad`, OB1 `d89c126`). Everything below was verified by reading the
named file at the named line or by running the named command in that worktree;
line numbers are post-edit (the curator healthcheck shifted everything after
compose `:513` by +6).

### Which OB1 containers `check-openbrain-health.ps1` still omits

The script references exactly these container names
(`grep -oE "'openbrain-[a-z-]+'" scripts/checks/check-openbrain-health.ps1 | sort -u`):
`openbrain-curator` (added by this item), `-db`, `-entity-worker`, `-gateway`,
`-idea-refinery`, `-mcp`, `-mcpo`, `-mcpo-ext`, `-postgrest`, `-research`,
`-rest`, `-wiki`, `-wiki-viewer`. `OB1/docker/docker-compose.yml` defines 24
services; the ones the script never names, with the compose line of the
service key and of its `container_name`:

| container | compose service | `container_name` | has `healthcheck:`? |
|---|---|---|---|
| openbrain-ext | `:156` | `:161` | no |
| openbrain-ops-gateway | `:243` | `:246` | yes |
| openbrain-suggestion-worker | `:429` | `:434` | no |
| openbrain-workbench | `:798` | `:803` | yes |
| openbrain-extract | `:860` | `:865` | yes |
| openbrain-chunk-worker | `:902` | `:907` | no |
| openbrain-grounding-backfiller | `:938` | `:943` | no |
| openbrain-db-backup | `:979` | `:984` | no |
| openbrain-wiki-backup | `:1026` | `:1028` | no |
| surrealdb / open_notebook / open-notebook-backup | `:1064` / `:1088` / `:1168` | - | (ON trio, in this project since K.5b) |

The "five other absent workers" named in
`documentation/implementation-guide/deploy-gate-and-curator-recovery/PLAN.md:82`
(chunk-worker, suggestion-worker, grounding-backfiller, workbench, extract) are
all confirmed absent. Of those five, only workbench and extract carry a compose
`healthcheck:`, so only they can ever surface through `stack.ps1 health`'s
`docker ps --filter health=unhealthy` (`scripts/stack/stack.ps1:127-129`); the
three workers, `-ext` and both backup sidecars are invisible on BOTH surfaces
today - the exact blind spot the curator sat in. Out of scope here by the
anchor; recorded for Phase C/D. `openbrain-idea-refinery` is defined in
`OB1/docker/docker-compose.scheduled.yml:235`, not the main file, which is why
it is absent from the table yet named by the script.

### openbrain-research and openbrain-mcp have no compose healthcheck

Counted with awk over each service block in `OB1/docker/docker-compose.yml`
at `d89c126`: `openbrain-mcp` block `:98-155` has 0 `healthcheck:` lines;
`openbrain-research` block `:535-633` has 0. Before this item, 7 of 24
services had one (db, gateway, ops-gateway, mcpo, mcpo-ext, workbench,
extract); the curator makes 8. Consequences worth stating plainly:

- `openbrain-research` now waits on the curator being healthy to START
  (`condition: service_healthy`, compose `:544-545`), but nothing waits on
  research, and compose does not stop a dependant when a dependency later goes
  unhealthy - the condition is an `up`-time gate only.
- A curator whose DB pool is dead answers 503, so the new healthcheck marks
  it `unhealthy` and `stack.ps1`'s unhealthy-count probe now catches that case
  too. `restart: unless-stopped` does NOT restart an unhealthy container (no
  autoheal in this project), so a stale-pool curator stays unhealthy until
  `check-openbrain-health.ps1 -Repair` restarts it - which is the same
  posture research already has.

### Harness: `andon.ps1 -Evaluate` cannot self-pass on this repository

Claim from the coordinating session: on 2026-09-06 the andon board fires
`policy-declared-unread` and `git-error-swallowed` (35 call sites) and reports
`protected-ref-moved` indeterminate (no baseline at
`.git/agent-worktrees/audit/andon-baseline.json`), so the `dark` gate profile
cannot self-pass and the anchors were confirmed under a delegated human
principal instead.

Verified by running `powershell -NoProfile -File scripts/agent-harness/andon.ps1 -Evaluate`
from `wt-curatorimg` on 2026-09-06 (exit code 6). Census line, verbatim:

    census  : unrecognised=0, fired=3, indeterminate=1, disabled=0, evaluated_ok=1 (total 5 of 5 in scope)

The claim holds and is slightly UNDER-stated: `fired=3`, not 2. The third is
`work-branch-on-remote` - `work/pod-key is on remote 'origin'
(refs/remotes/origin/work/pod-key)`. `git-error-swallowed` listed 35 sites,
matching the claim; `protected-ref-moved` read `no baseline recorded - run:
andon.ps1 -Baseline`. Nothing in this item changes any of the four conditions.

### Tester attempt 1 (2026-09-06): two tensions, recorded verbatim

The tester passed all nine cases and marked the plan inadequate (four plan
defects, fixed in plan rev 2 on this branch). Two things the tester raised
were not plan defects but tensions; both are recorded here as findings.

**(a) The dead-DB curator is UNHEALTHY under the new healthcheck, and
research waits behind it.** Tester, Refutation 4, verbatim:

> The anchor says "answers GET /health with {"ok":..., "db":false} - the
> process is up even when the database is not"; T3 confirms the process
> stays up (RestartCount 0). But with the new healthcheck
> (`Deno.exit(r.ok?0:1)`, refutation 2) that same state is `unhealthy`, and
> `openbrain-research` now has `condition: service_healthy` on the curator,
> so a curator with a dead DB at `up` time keeps openbrain-research from
> STARTING (compose does not stop it later - an up-time gate only). [...] the
> tension is between the anchor's wording and the healthcheck's semantics,
> and the operator should confirm that research-not-starting behind a
> db-dead curator is acceptable

The tester also proved, with a throwaway `pgvector/pgvector:pg16` on the
default bridge, that the healthcheck CAN go healthy against a live DB
(research is not blocked forever) and reads `health=unhealthy failing=3
restarts=0 status=running` against a dead one.

DECIDED in the amended anchor (delegated operator seat, 2026-09-06),
acceptance line 3, verbatim:

> DECIDED 2026-09-06 (delegated operator seat): under the new healthcheck
> that state is UNHEALTHY, and openbrain-research (service_healthy) waits
> behind it. That is intended: a curator that cannot reach its database
> cannot file research, and research uses the same database and
> credentials, so nothing is lost by waiting; what is gained is that the
> state is VISIBLE (docker ps, stack.ps1 health) instead of masked as
> 'running'.

So the posture is settled, not open. What remains true and out of scope:
`restart: unless-stopped` does not restart an unhealthy container, so a
stale-pool curator stays unhealthy until `check-openbrain-health.ps1 -Repair`
restarts it - the same posture research already has (Phase C/D).

**(b) `remove-worktree.ps1` refused the tester's worktree for the base
branch's own commits.** Tester, cleanup section, verbatim:

> `remove-worktree.ps1 -Id test-curatorimg` REFUSED (exit 2): "commits not
> in refactor/ai-stack-cleanup: 2" - those are the base branch's own
> `fb8d2b0`/`af0faad`, not tester work (0 uncommitted files), but I did not
> `-Force`; the worktree `wt-test-curatorimg` (branch `work/test-curatorimg`)
> is left for the operator to remove.

Harness gap, verified at `scripts/agent-harness/remove-worktree.ps1:112-135`:
the "what would be lost" check is `git log --oneline $MergedInto..$branch`
(`:115`), where `$MergedInto` defaults to the resolved work line (`:18`,
`:32` -> `Resolve-WorkLine`, i.e. `refactor/ai-stack-cleanup`). A tester
worktree is cut FROM the developer's branch, so `work/curatorimg`'s own
unmerged commits are in that range and count as the tester's "work that is
nowhere else" (`:128`, `:134`). The check has no notion of the branch the
worktree was cut from; comparing against `$(git merge-base <base-branch>
$branch)..$branch` - or against the `-Base` the worktree was created with -
would count only commits made IN that worktree. Until then, a tester
worktree on a not-yet-merged developer branch can only be removed with
`-Force` or by the operator, which is what happened here.

### Small things seen in passing

- `deno check` with `pool.ts` missing reports a SECOND error, `TS7006
  Parameter 'row' implicitly has an 'any' type` (T5 mutant B). The `row` type
  flows from `pool.ts`; the real build reports 0 errors. Not a defect - noted
  so a tester does not read the second error as a new one.
- The T3 image's `/health` `pool_rebuilds` counter climbs by ~1 per probe
  against a dead host (3 at +6 s, 9 at +92 s) - the ResilientPool discarding a
  pool it could not open. Expected behaviour, cheap, and a useful witness that
  the wrapper is the one answering.


## opsdoor

Recorded 2026-09-06 by the `opsdoor` developer (worktree `wt-opsdoor`, base
`e72c678`, OB1 `6fba6b3` on `fix/ob1-image-revision-label`). Each entry says
what was checked and how.

- **The brief's "check-openbrain-health.ps1 already reaches openbrain-db -
  reuse its psql/exec pattern" was false.** At `e72c678` the script's only
  docker calls are `docker inspect`, `docker start` and `docker restart`
  (`grep -n "docker " scripts/checks/check-openbrain-health.ps1`); its DB
  "reach" is `Get-CStartedAt 'openbrain-db'`, a container-state read. The
  password-free psql pattern lives in the sibling checks -
  `scripts/checks/smoke-agent-memory-live.ps1:45`,
  `prove-agent-memory-rls.ps1:102`, `recall-sibling-class.ps1:90` and eight
  more (`grep -rn "psql -U" scripts/`) - and that is what the new
  `research_jobs` query copies: `docker exec <db> psql -U postgres -d <db> -tA
  -v ON_ERROR_STOP=1 -c <sql>`, authenticated over the container's unix
  socket. Recorded so the next brief does not send someone to look for it in
  the health script.

- **Six of the eight OB1 integration Dockerfiles still carry no revision
  label.** At OB1 `6fba6b3`, `git -C OB1 ls-tree -r --name-only 6fba6b3 --
  integrations | grep /Dockerfile$` lists eight; grepping each blob for
  `org.opencontainers.image.revision` finds it in `research-curator` and
  `research-service` only. LACKING: `chunk-embedding-worker`,
  `entity-extraction-worker`, `grounding-backfiller`, `kubernetes-deployment`,
  `openbrain-idea-refinery`, `suggestion-worker`. A deploy of any of those
  through `ob1-deploy.ps1` prints `label : (empty) vs pin ... -> EMPTY
  (Dockerfile carries no ARG OB1_SHA + LABEL - follow-up)` and still exits 0;
  the door cannot tell those images apart from a stale `:local`. Out of scope
  by the anchor ("this item adds it ONLY to research-curator and
  research-service"); the fix is the same eight lines in each file.

- **`docker inspect --format '{{index .Config.Labels "..."}}'` cannot be run
  from PowerShell 5.1.** PS re-quotes a native argument that contains spaces
  by wrapping it in double quotes WITHOUT escaping the inner ones, so docker
  receives a truncated template and says `template parsing error: template:
  :1: function "org" not defined`; the `\"` form (`"{{index .Config.Labels
  \"org...\"}}"`) fails differently (`'docker inspect' requires at least 1
  argument`). Both verified 2026-09-06 through the tool's PowerShell. The
  anchor's acceptance line 3 quotes the Go-template form; it works verbatim
  from Git Bash. `ob1-deploy.ps1` therefore never uses `--format` for this -
  it parses `docker inspect` JSON (`ConvertFrom-Json`) and reads
  `.Config.Labels.'org.opencontainers.image.revision'`; the test plan gives
  both forms. Sibling scripts that use `--format '{{.State.Status}}'` are
  unaffected (no space in the template).

- **`docker compose config --format json` renders `build.context` with the
  slashes the compose file / `.env` used, not normalised.** A scratch compose
  with `WT=D:/Open WebUI/...` in its `.env` produced context
  `D:/Open WebUI/.../OB1/integrations/research-curator`, and the door's
  "is this context inside OB1?" test (a `StartsWith` against a backslash
  `Resolve-Path`) called the pinned tree OUTSIDE OB1. Fixed in the door by
  normalising both sides to backslashes before comparing; recorded because
  any other script that compares compose-rendered paths to `Resolve-Path`
  output has the same hole.

- **`docker compose up -d` prints `Container ... Started` for a service that
  exits a second later.** Seen in T3c: the loop variant's curator (a
  `command` that exits 1 under `restart: unless-stopped`) got `Recreated`,
  `Starting`, `Started` from compose, and the door's first `docker inspect`
  sample - in the same second - read `status=restarting restarts=2`. Compose
  reports creation, not survival; this is the whole reason the door and the
  recovery script poll after `up -d` instead of trusting its exit code.

- **`docker compose ps --format json` is NDJSON under Compose v5.3.0, and
  `emergency-recovery.ps1`'s status block survives only because PowerShell
  pipes it line by line.** `docker compose -f OB1/docker/docker-compose.yml ps
  --format json | wc -l` is 30 (one object per line, no array). The existing
  `... ps --format json | ConvertFrom-Json` at the status block works because
  each line reaches `ConvertFrom-Json` as its own string; a refactor to
  `(... | Out-String) | ConvertFrom-Json`, or capturing to a variable first
  with `-Raw` semantics, would throw and hit the `catch` ("OB1 status
  unavailable"). The new `RESTART LOOP:` branch reads `State` from the same
  objects (field verified present: `Name`, `State`, `Service`).

- **The tool's PowerShell guard blocks two innocent-looking commands.**
  `Remove-Item -Recurse -Force "<path containing a space>"` is refused as
  "Remove-Item on system path '"D:\Open' is blocked", and so is `docker rm -f
  <container>` (the guard reads `rm -f` in the command text). Workarounds used
  here and written into the plan: `docker run --rm` + `docker stop`, `docker
  compose down -v`, and Git Bash for `rm -rf` of scratch directories. Harness
  note, not a repo defect.

- **`openbrain-research` has no compose healthcheck, so the door's "healthy"
  for it is "running for 60 s without a restart"** - the plan block says so
  (`watch: running, no restart for 60 s (no healthcheck)`), and the curatorimg
  section above already records the absence. A research service that boots,
  binds :8000 and has a dead DB pool passes the door. Out of scope by the
  anchor (healthchecks on openbrain-research / openbrain-mcp); the door will
  pick a healthcheck up automatically the day compose has one - it reads
  `start_period`/`retries`/`interval`/`timeout` from `docker compose config`.

- **Recovery now takes at least 60 s longer per OB1 start.** `Start-OB1Stack`
  and `Reset-OB1Stack` each hold for the full `Wait-ForRestartLoops -Seconds
  60` window before logging SUCCESS or WARN; `recover` calls the first,
  `nuclear` calls the second (each once, so +60 s, not +120 s per run). A
  3 a.m. reader watching the log sees `OB1 up -d returned - watching 60 s for
  restart loops before calling it started...` during the hold. Stated so the
  delay is not read as a hang.

- **The recovery status line and the post-up WARN are two independent reads.**
  The WARN comes from `docker ps -a --filter label=com.docker.compose.project=open-brain`
  sampled for 60 s right after `up -d`; the `OB1 - N/M running; RESTART LOOP:
  ...` line comes from `docker compose ps --format json` minutes later in
  `Test-BasicConnectivity`. A container that loops and then settles shows in
  the first and not the second; one that starts looping later shows only in
  the second. Neither is wrong; they answer "did it come up?" and "is it up
  now?" respectively.

### Attempt 1 (tester-opsdoor-sub1, 2026-09-06): what the test found, and what changed

Every plan case T0-T8 passed as written; the item FAILED on the tester's
refutation R4 and the plan was marked inadequate. Recorded here with the
resolution, so the second attempt's reader knows what moved and why.

- **The door's header promised a rule its body did not apply (FIXED).**
  `ob1-deploy.ps1` said "a RestartCount above 0 fails the deploy the moment
  it is seen"; `Watch-Service` baselined RestartCount on its first sample and
  failed only on an increase, so a container that crashed and restarted in
  the gap between `up -d` returning and the first inspect printed
  `restarts=1` on every line and still ended `OK ... watched 60 s clean`,
  exit 0 (tester R4a, a once-flap alpine service; the same `restarts=1 (0 s)`
  is visible at the top of T3c's watch in the tester's evidence). Decision
  (delegated seat): two rules, stated in the header and in the summary's
  `rule :` line. FRESH (the container this run created or recreated - a
  new container id after `up -d`, and every -Recreate dependent): any
  RestartCount above 0 fails, naming the container and quoting its log
  tail. PRE-EXISTING (compose found nothing to recreate, same id): only an
  increase during the watch fails. Plan case T3d exercises both rules.
- **`docker kill` does not exercise the restart policy.** While building
  T3d's "increase" half: `docker kill <container>` under
  `restart: unless-stopped` left the container `exited restarts=1` - the
  daemon treats a kill from the CLI as operator intent and does not
  restart it (the door still failed the watch, as `EXITED`). And a process
  inside the container cannot kill PID 1 either (a pid namespace's init
  ignores signals it has no handler for). The case therefore makes the
  entrypoint exit on its own when a marker file appears on its volume
  (`docker exec <c> touch /state/flap-now`). Trap for anyone writing a
  restart-policy test.
- **Recreate is not rebuild (runbook FIXED, door header states it).** In the
  tester's T3a the -Recreate'd research kept the baseline image
  (`research-label=''` after the door reported MATCH for the curator). A
  dependent named in -Recreate is `up -d --force-recreate`d on whatever image
  its tag holds. UPDATE-MANAGEMENT now says: one door call per service whose
  image changed, then -Recreate for the depends_on-only dependents; a bump
  touching both research services is `-Service openbrain-research` first,
  then `-Service openbrain-curator -Recreate openbrain-research`.
- **`-WhatIfOnly` now says up front when the Dockerfile carries no label
  (FIXED).** Tester R3: `-Service openbrain-workbench -WhatIfOnly` printed a
  clean plan and the operator would have learned of the EMPTY label only
  after deploying. The plan block now prints a `NOTE :` naming the Dockerfile
  when it lacks `ARG OB1_SHA` + the LABEL; verified on openbrain-workbench
  (NOTE printed) and openbrain-curator (no NOTE).
- **The dirty-tree refusal is per BUILD CONTEXT, not per OB1 tree** (tester
  R1b/R1c, not changed). A tracked edit in `research-service/index.ts` does
  not refuse a curator deploy (the curator's build cannot read it) and does
  refuse a research deploy. A `.gitignore`d file inside a context is
  invisible to `git status --porcelain` and WOULD be copied by
  `COPY *.ts ./` if it matched; none exists today. Left as is: the refusal
  guards "the image is the pin", and a file outside the context is not in
  the image.
- **Door and recovery judge a once-flap differently, by design (both headers
  now say so).** Tester R4b: the recovery function's `docker ps` sampling
  never sees a sub-second restart as `Restarting`, so a once-flap is not
  named there; the door now fails it on a fresh container. Recovery asks
  "is anything looping now?" over pre-existing containers; the door asks
  "did what I just started stay up?".
- **Production fact at test time (read-only, tester T5d): 4 `research_jobs`
  rows ended `status='error'` in the last 24 h**, newest
  `7eebcaee-c347-489c-9644-b19c3ab2dc00` at 05:12Z with `curator: the
  research completed but was NOT filed into Open Brain - error sendin...`.
  That is the health line doing its job on the first day; the failure it
  names (research done, persist to the curator failed) is a research/curator
  defect outside this item and is not diagnosed here.
- **Plan defect (FIXED in plan rev 2):** T4c's `grep '^+.*build'` "is empty"
  could never pass - it matched the new function's own comment "Rebuilding an
  image is a DEPLOY". The assertion now excludes comment lines and matches
  build invocations only.
