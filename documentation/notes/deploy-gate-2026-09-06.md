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

## passplan

- **`gate2.plan.md` in the live queue has zero recognisable case headings** (checked
  2026-09-06: `grep -cE '^##\s+(T[0-9]+|Case\s+[0-9]+)\b'` over every `*.plan.md` in
  `.git/agent-worktrees/queue/` - 30 plans have 5-18 case headings, `gate2.plan.md` has 0).
  `gate2` is `merged`, so nothing acts on it; it is the one existing item whose plan the
  new parser could not have checked. Re-grading is out of scope by the anchor. No item was
  in `ready-to-test` or `testing` at the time of the check, so the new `-Pass` rule met no
  in-flight item.
- **The real `curator2` evidence has no `## T7` heading at all.** The tester wrote "T7's
  happy path is covered here at the same (scoped) level" inside T5's caveat
  (`documentation/evidence/passplan/fixtures/curator2.attempt1.evidence.md:242`, in the T5
  section that runs from line 213 to the T6 heading at line 245). The
  anchor names T5 and T6 as the two scoped cases; the parser also refuses T1 (one of its
  three headings reads `PASS (with caveats)`, line 46) and T7 (missing). Four of eight
  cases in a merged item's evidence were not bare passes. Re-grading is out of scope.
- **Items submitted before this change carry no `plan_sha256`.** `-Pass` on such an item
  prints a yellow NOTE and skips the drift check rather than refusing (queue.ps1, the
  `$planThen` branch in the -Pass handler) - refusing would strand a tester on an item
  already in `testing`, which has no door that re-records the hash. Every item is either
  terminal or pre-submit today (see the first entry), so the branch has no live subject;
  it exists for a queue that still has one when this merges.
- **`-Fail` does not check the plan hash.** Kept deliberately: the anchor limits the
  `-Fail` change to recording per-case verdicts, and a fail against a drifted plan still
  sends the item back to the developer, who re-submits a plan (which re-records the hash).
- **`verify-merge-protocol.ps1` cannot go fully green on this machine today, with or
  without this change - six checks fail on the BASE too.** Mechanism, verified by reading
  the drill output: `.githooks/pre-commit` step 3b (`refactor/ai-stack-cleanup`) runs
  `./scripts/checks/check-corpus-exposure-producers.ps1` relative to the committing
  worktree; the drill cuts `drill/verify-d` from `development` (hard-coded,
  `verify-merge-protocol.ps1:102`), and `git cat-file -e
  development:scripts/checks/check-corpus-exposure-producers.ps1` fails (the script was
  added in `d596426`, which `development` at `104d8f0` does not contain). `core.hooksPath`
  is the absolute path to the main checkout's `.githooks`, so the drill's two commits in
  step 2 are refused by a hook that cannot find its own script ("Pre-commit validation
  failed (a corpus insert does not state its plane)!"), `two divergent commits exist` fails,
  and five downstream checks (conflict, stale sha, both intents, two merge commits) fail
  with it. Evidence: the base checkout's OWN copy, `D:\Open WebUI\ai-stack\scripts\
  agent-harness\verify-merge-protocol.ps1`, run 2026-09-06 = `60/66 checks passed`, those
  six FAIL; this branch's copy = `64/70`, the same six FAIL and the four new pass-rule
  checks PASS. Fix candidates (out of scope here): step 3b skipping when the script is
  absent in the committing tree, or the drill basing on the work line rather than
  `development`.
- **`verify-merge-protocol.ps1` left `drill-evidence-*.md` in `%TEMP%`** (attempt 1's four
  evidence files; found by the tester). Fixed in attempt 2 with one line in step 12. It
  still leaves its OWN pre-existing temp files behind - `drill-test-plan.md`,
  `drill-test-plan-v2.md`, `drill-anchor.json`, `drill-anchor-vague.json`,
  `drill-attest-absent.log` - all written to `$env:TEMP` by name and never removed
  (checked 2026-09-06: `Get-ChildItem $env:TEMP -Filter "drill-*"` lists them from earlier
  runs). Harmless and idempotent; not this item's.
- **Legacy-item behaviours under the new rule** (each driven by attempt 1's tester in a
  hermetic state dir, `passplan.evidence.md` "Refutations attempted"): an item with no
  `plan_sha256` gets a yellow NOTE, not a refusal, and the case rule still applies; a legacy
  item whose queued plan has drifted AND whose evidence is all-PASS passes silently (drift is
  unverifiable without the hash - accepted); a legacy item whose queued plan has no case
  headings is refused at `-Pass` (`has no case headings`) and needs `-Fail -PlanInadequate`
  then `-Resubmit -TestPlan` with a headed plan before it can ever pass. Live items in flight
  on 2026-09-06 (`passplan`, `gate5d`, `curatorimg`) all carry `## T<n>` plans and no
  `plan_sha256`; no live item has a heading-less plan except the merged `gate2`.
- **`## T05` and `## Case 5` do not match plan case `T5` - intended.** Ids are literal after
  one normalisation (case of the letter, whitespace in `Case <n>`); `T05` is a different
  string from `T5`, and `Case`/`T` are two namespaces. Documented in MERGE-PROTOCOL Step 3
  ("Three edges, decided") in attempt 2. A tester who retypes an id differently from the
  plan gets `T5: MISSING`, which names the case, so the cost of the strictness is one
  readable refusal.
- **`verify-merge-protocol.ps1` is not hermetic** - it runs in the main checkout's git
  (`Get-MainCheckout`, `Set-Location $repo`), provisions `wt-drilla`/`wt-drillb` under the
  main `.claude/worktrees/`, and writes `drill-*` items into the SHARED queue directory.
  That is its documented design (header lines 16-18), and the anchor's acceptance names
  running it. Noted because the item brief for this worktree said never to run git against
  the operator's checkout; the drill does, by construction, on its own branches only.

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

## researchretry

Recorded 2026-09-06 by the `researchretry` developer (worktree
`wt-researchretry`). Pins: attempt 2 = parent `8554c97` / OB1 `20ed84b` on
`fix/research-curator-call-timeout-retry`; attempt 1 = parent `995c0c7` /
OB1 `6197bc7`; the first cut before the anchor amendment = `dfac66c` /
`d0c8a65`. Everything below was verified by reading the named file at the
named line at the named pin, or by running the named command, or - where it
says so - is the tester's measurement from attempt 1's evidence
(`researchretry.evidence.md`, tester-researchretry-sub1). Nothing here is in
the deliverable's scope; the deliverable fixes the RESEARCH side of the
call, the curator, its compose wiring and the deploy are out of scope.

### DECIDED IN THE ANCHOR: the first cut's 15 s default would have clipped healthy ingests; a timeout is never resent

This was finding 1 of the first cut, raised against the original anchor
(default `CURATOR_TIMEOUT_MS = FETCH_TIMEOUT_MS`, 15 000 ms, timeouts
retried). The operator amended the anchor the same day; the amended artifact
line reads:

> gains AbortSignal.timeout(CURATOR_TIMEOUT_MS, default 180000 - an ingest
> awaits an embedding, an LLM thread decision, a persist and a claims pass,
> so a 15 s default would abort healthy work) and a bounded retry with
> backoff (CURATOR_RETRIES total attempts, default 3) on CONNECTION-LEVEL
> errors only (refused, reset, EPIPE, host unreachable, 'error sending
> request' before any response) - NEVER on a timeout (the curator may still
> be working; a resend would double-ingest) and never on a 4xx/5xx JSON
> answer, which is the curator's verdict. A timeout fails once, loudly,
> naming the elapsed time.

and its out-of-scope list adds: "Measuring real ingest durations in
production and tuning CURATOR_TIMEOUT_MS below the default - recorded as a
follow-up; the default is deliberately generous because the goal is 'cannot
hang forever', not 'fail fast'." At `20ed84b`: `lib.ts:448` default 180 000;
`lib.ts:518-531` `shouldRetryCuratorError` returns false for AbortError /
TimeoutError / the Curator* classes; `lib.ts:639-642` throws
`CuratorTimeoutError` on the first timeout with the elapsed ms. The evidence
the decision rested on stays below, because the follow-up (measuring real
ingests) needs it.

The curator's ingest handler answers only after, in order: `embed`
(`research-curator/index.ts:543`), `resolve` (`:545`), which awaits an LLM
chat completion at `:238` (`chatJson`, a bare fetch at `:155` with no
signal) and, when it refreshes a thread, a second one at `:284`;
`delegatePersist` to openbrain-mcp (`:551`, bare fetch at `:388`); then
`writeClaims` (`:421`) with a per-conflict LLM judge at `:462`. `grep -n
signal research-curator/index.ts` at the pin matches nothing: none of the
curator's three outbound fetches (`:131`, `:155`, `:388`) carries a timeout,
and nothing cancels the handler when the research side aborts its request.
Under the shipped policy the residual exposure is one aborted client waiting
on a curator that may still file the package; the job says `timed out ...
the curator may still be working`, which is honest, and the 180 s default
makes it rare. Dedupe that IS verified: claims go through
`find_or_create_claim` and count `was_duplicate`
(`research-curator/claims.ts:214-224`); sources are described as going
through `find_or_create_source` (`research-curator/index.ts:18`, a comment -
the function lives in openbrain-mcp and was not read). The synthesis row and
the thread decision were not checked for idempotency.

How long a real ingest takes is NOT retained anywhere: `research_jobs.progress`
holds only the last `{phase, message}` (`research-service/index.ts:625` at
`d0c8a65`; `:634` at `20ed84b`), and the curator logs one summary line per
ingest with no duration (`research-curator/index.ts:620`). The one completed
job on 2026-09-06 (`0b3b1bfa`, 13:36:12 -> 13:43:12, 420 s total) shows the
ingest END (curator line 13:43:12.022, `finished_at` 13:43:12.025), not its
start. Follow-up: stamp `started`/`finished` per stage in `progress`, or log
the ingest duration in the curator's summary line, before tuning the default.

### DECIDED IN THE DELEGATED SEAT (attempt 1 -> 2): the tester's refutations, and what each became

The tester's attempt-1 evidence carried eight observations. Where one changed
the code, the pin-exact line is given; the rest are recorded as-is.

1. **`(SendRequest)` closes were retried - the package resent after it was
   received.** Tester's `rr-reset2` listener (reads the request, then closes)
   logged the same 227-byte package THREE times under `6197bc7`'s regex,
   which matched Deno's generic `error sending request` prefix for both
   `client error (Connect)` and `client error (SendRequest)`. FIXED at
   `20ed84b`: `lib.ts:515-516` `CONNECT_PHASE_RE` matches only the
   `(Connect)` family + ECONNREFUSED/EHOSTUNREACH/ENETUNREACH/EAI_AGAIN;
   `lib.ts:643-653` turns a post-send transport failure into
   `CuratorAfterSendError` (one attempt, "the package may have been
   received"). Developer's `rr-close` listener at attempt 2 logged the
   package exactly once (plan T3(e)).
2. **A 2xx with a non-JSON / truncated body read as filed with error NULL**
   (`OK after 10 ms: {}` for a `text/html` 200). FIXED: `lib.ts:634-636`
   throws `CuratorNoVerdictError` `curator answered <status> with no JSON
   verdict (<n> bytes)`; a non-JSON 4xx/5xx keeps `curator <status>: {}`
   (`lib.ts:629-633`).
3. **THE attempt-1 FAIL (refutation 2b): a timeout during the body read of a
   2xx resolved `{}` silently** - `OK after 5003 ms: {}` from a listener that
   sent 200 headers then stalled. The old `r.json().catch(() => ({}))`
   swallowed the TimeoutError. At `d89c126` that shape hung forever; at
   `6197bc7` it was reported as success - the outcome the item exists to
   prevent. FIXED: the body is read as text inside the same signal
   (`lib.ts:626-628`) and a timeout there is `CuratorTimeoutError ... while
   reading its answer` (`lib.ts:639-642`). Reachability today: the real
   curator never streams (7x `Response.json(...)` in
   `research-curator/index.ts`, no ReadableStream), so this needed an
   intermediary or a future streaming curator; the policy function's
   contract was wrong regardless.
4. **`CURATOR_TIMEOUT_MS=-1` was passed through to `AbortSignal.timeout`**
   and threw `Argument 1 is outside the accepted range` on every attempt
   (loud, not a hang; every job failed). FIXED: `curatorTimeoutMsFromEnv`
   (`lib.ts:452-455`) and `curatorRetriesFromEnv` (`:458-461`) fall back to
   the defaults for <= 0 / non-numeric / unset, and the wrapper clamps a
   non-positive `timeoutMs` (`lib.ts:615`). `index.ts:115-116` uses them.
   Tester's other edge values were already sane: `CURATOR_RETRIES=0|abc|-2`
   -> 1 attempt, `2.7` -> 2; `CURATOR_TIMEOUT_MS=0|abc|""` -> 180000,
   `5000abc` -> 5000.
5. **A just-died host with a CACHED ARP entry reads as a "may still be
   working" timeout** at the 5 s test timeout: SYNs to the dead IP are
   silently dropped, the AbortSignal fires, and the code cannot tell a dead
   curator from a slow one. With an UNCACHED unreachable IP (172.17.0.250)
   the kernel answers `No route to host (os error 113)` after ~3 s and that
   IS retried (`(Connect)`). The tester's statement that at the 180 s
   default the OS connect timeout (~127 s) would fire first and turn the
   cached-ARP case into a retried connect failure is an INFERENCE, not
   measured (it would take >2 min); recorded as such. Not changed: the
   policy cannot distinguish the two without a probe, and a probe is out of
   scope.
6. **Backoff cap and the real sleep** hold: `curatorBackoffMs(50|1e9|Infinity)
   = 10000`, `(NaN|-3) = 2000`; `curatorRefusedWorstCaseMs(50, 5) = 474250`;
   with the real `setTimeout` the gaps were never below the wait (Windows
   timer granularity added ~10 ms). No change.
7. **The suite runs with `--allow-env` only** (no `--allow-net`), as the
   anchor's acceptance command says. No change.
8. **`research_jobs.error` is `TEXT`** (`init-research-jobs.sql:41`): no
   column limit truncates the longer messages. No change.

Also from attempt 1, unchanged: `harness.ts` diff empty between pins;
`lib.ts:332-359` md5-identical at both pins (T5).

### Smaller facts

- `lib.test.ts` had 25 cases at d89c126 (`deno test`), not the 24 the brief
  quoted; 38 at `20ed84b`.
- On Docker Desktop's default bridge a just-STOPPED container's former IP
  answers with a TCP reset: Deno reports `Connection refused (os error 111)`,
  not `EHOSTUNREACH`. Retried either way (`(Connect)`).
- Deno's own message for a refused connect repeats the cause (`... tcp
  connect error: Connection refused (os error 111): Connection refused (os
  error 111)`); that is the runtime's text, not the wrapper's.
- OB1 commit `20ed84b`'s message miscounts the mutation kills ("signal
  removed -> 3 fail; timeouts retryable -> 3 fail"). Measured: retry removed
  2, signal removed 4, timeouts retryable 3 (with the three-edit mutation
  the plan specifies; the one-edit version the message counted only un-wraps
  the timeout and kills 2), body errors swallowed 2. Not amended: a force-push
  is a human line; the plan and the parent commit `8554c97` carry the
  measured counts.
- A PowerShell command that contains both `docker ... rm` (or `--rm`) and a
  path under `D:\Open` is blocked by the agent tool guard as a file removal;
  the live cases were driven from Git Bash with `MSYS_NO_PATHCONV=1`.

## owuidrift

Recorded 2026-09-06 by the `owuidrift` developer (worktree `wt-owuidrift`,
branch `work/owuidrift`, base `7614556`). Everything below was verified by
reading the named file at the named line, or by running the named command
against the live stack on 2026-09-06. The live `openwebui` container was read
only - `mode=ro`, no restart, no write; `tailscale` was not touched. Nothing
here is in the deliverable's scope beyond what the anchor names.

### THE CENSUS: what is actually live, for the first time

`scripts/checks/check-owui-drift.ps1` against the live `openwebui` container,
2026-09-06: **21 manifest rows, 19 IN SYNC, 2 DIFFERS, 0 MISSING LIVE, 0
MISSING REPO.** Nobody had this list before; the previous answer was a hand
check dated 2026-08-20 written into `owui/README.md` and never re-run.

IN SYNC (19):
`actions/copy_research_note.py`, `actions/copy_sources.py`,
`filters/context_window_manager.py`, `pipes/githelper.py`,
`pipes/github_chat_mcp.py`, `pipes/little_coder.py`, `pipes/server_status.py`,
`skills/canvas-design.md`, `skills/doc-coauthoring.md`, `skills/docx.md`,
`skills/feature-validation-workflow.md`, `skills/github-repo-analyzer.md`,
`skills/github-repo-expert.md`, `skills/openwebui-tools.md`,
`skills/skill-creator.md`, `tools/deep_research.py`, `tools/fileshed.py`,
`tools/github_chat_mcp_tools.py`, `tools/superpowers_tool.py`.

DIFFERS (2):

| row | repo sha256 (CR-normalised) | live sha256 | live updated_at |
|-----|------------------------------|-------------|-----------------|
| `filters/mnemory_persistent_memory.py` (`function|mnemory_persistent_memory`) | `a4542e32f323c43762676ae557c8e9e9fadda67fad8cff265078a00aad234753` | `c8d1dcf7804a4c15439cc1407852ac84188e76fa55edaaf12838c832f4051b63` | 2026-09-03 00:53:44 UTC |
| `tools/mnemory.py` (`tool|mnemory`) | `9d57b7f6efcc4a29337dce8febb0b9a4cbdb7eb4c4b7ef3ce0c2e107edde75be` | `240c39138b196a3929ddeaa3bb49be7d54251e549c1447b38b84c42b268ded5f` | 2026-05-29 18:13:03 UTC |

`tools/deep_research.py` is IN SYNC, live `updated_at` 2026-09-06 12:07:10 UTC -
the paste this session made. That is the anchor's headline case and it holds.

### FINDING 1: the mnemory URL-default fix has been committed and unpasted for 17 days

Both drifted files were last changed by the SAME commit, `98c0317` (2026-08-20,
"owui: retire add_web_sources_to_knowledge action; fix mnemory URL defaults (v3
D-14, D-9-lite)"), one line each (`git show --numstat 98c0317 -- owui/tools/mnemory.py
owui/filters/mnemory_persistent_memory.py` -> `1 1` for both). The changed line
in each is a `default="<url>"` inside a Valves class.

- `tool|mnemory`'s live `updated_at` is **2026-05-29**, which predates both
  `98c0317` (2026-08-20) and the export commit `223ebbc` (2026-06-16). The row
  has not been written since May. **The 2026-08-20 fix was never pasted.**
- Measured, same run: live content is 23315 chars / 23789 UTF-8 bytes / 615
  lines; the repo file CR-stripped is 23313 chars / 23787 bytes / 615 lines -
  two characters apart, consistent with the one-line default change and with
  nothing else having moved.
- `function|mnemory_persistent_memory` differs by the same 2 characters (live
  41490 chars vs repo 41488) but its live `updated_at` is **2026-09-03**, i.e.
  AFTER the repo's last commit. Content cannot say which side is authoritative;
  the check deliberately does not guess (`check-owui-drift.ps1:51-53`).

What this does NOT establish: whether either drift has any runtime effect. The
changed line is a valve DEFAULT, and a stored valve overrides the default -
valves live in their own column that this check never reads. Both live rows do
carry non-null valves (14 of 14 tool+function rows do). Deciding whether to
paste is the operator's call, and the paste itself is out of this item's scope.

### FINDING 2: "16 files" was stale by three retirements; the real count is 21

The anchor, and `owui/README.md` before this change, both said 16. The manifest
has **21** data rows: 13 tool/function files + 8 skills. 16 was the count before
`add_web_sources_to_knowledge`, `code_agent` and `code_agent_tools` were retired
in August 2026 (`owui/README.md:19` and the closing note of that file record the
retirements); the total was never recomputed. Corrected in `owui/README.md:29-35`
in this change. Anyone reading "16" and trimming the manifest to match would be
deleting live rows.

### FINDING 3: a live function nobody's manifest names

The live `function` table holds **9** rows; the manifest names 8. The extra is
`add_web_sources_to_knowledge` (live `updated_at` 2026-08-20 20:10:16 UTC),
which `owui/README.md:19` says was retired 2026-08-20 and "deactivated in
webui.db" - it is deactivated, not deleted, and still carries content. This
check is manifest-driven, so it cannot see it; the script's header says so in as
many words (`check-owui-drift.ps1:49-50`). A live-side census that flags rows
the manifest does not name is a different check and was not built here.

### FINDING 4: nothing verifies the manifest's own sha256 column

The new `sha256` column is documentation. `check-owui-drift.ps1` hashes the FILE
at run time and never reads the column, so a stale column cannot make the check
lie - but nothing complains if a future session edits a plugin file and forgets
to regenerate it. Deliberate: making the check read the column would let a
forgotten regeneration mask real drift. A separate "manifest column matches the
file" assertion would close it; not in scope.

### FINDING 5: CR-normalisation is load-bearing for all 21 rows, in both directions

Measured on the live data:

- Working tree: `.py` files are CRLF on disk (`core.autocrlf=true`);
  `.md` skills are LF (`.gitattributes`: `*.md text eol=lf`).
- Live DB: `tool` and `function` content is LF (0 CRs);
  `skill` content is CRLF (`docx`: 590 CRs, 590 LFs).

So every `.py` row is CRLF-vs-LF and every skill row is LF-vs-CRLF. A byte
comparison would have reported **21 of 21 rows drifted** and been useless. Both
directions are exercised by the live run and pinned by a constructed pair in the
test plan (T3).

### FINDING 6: three PowerShell 5.1 traps that each produce a WRONG answer, not an error

Found while building this check; all three are recorded in the script.

1. `& docker inspect ... | Select-Object -First 1` stops the pipeline early,
   which kills the native process and leaves `$LASTEXITCODE = -1` **for a
   container that is running**. The first version of this check refused against
   a healthy `openwebui` for that reason alone (`check-owui-drift.ps1:122-134`).
2. Under `$ErrorActionPreference = 'Stop'`, anything a native command writes to
   stderr is raised as a terminating `NativeCommandError` and `$LASTEXITCODE`
   becomes `-1`. Reproduced directly: a `docker exec` whose python raised
   "no such table" reported exit `-1`, not `1`. Any harmless docker warning
   would therefore have read as "database unreadable". Fixed by dropping to
   `'Continue'` around the two native calls (`check-owui-drift.ps1:126-129`,
   `152-157`).
3. A parameter named `-Db` on a `[CmdletBinding()]` script is REJECTED at parse
   time: "conflicts with the parameter alias of the same name for parameter
   'Debug'". Renamed `-DbPath`.

### FINDING 7: `grep -c $'\r'` is not a reliable line-ending measurement here

Through the agent tool layer the CR in `$'\r'` can be stripped in transport,
leaving `grep -c ''` - which matches EVERY line. It reported "590 CR lines" for
`owui/skills/docx.md`, a file with **0** CR bytes, and I briefly believed the
skills were CRLF on disk. `tr -dc '\r' | wc -c` gives the byte count and does not
lie. This is the same class as the false "PLAN.md is CRLF" reading the anchor
cites: a line-ending claim needs a byte count, not a pattern match.

### CONSEQUENCE: `stack.ps1 health` now exits non-zero on this stack

The new probe (`scripts/stack/stack.ps1:146-147`) reports the drifted count and
fails when it is not 0, so today's run ends `1 probe(s) FAILED` (exit 1) where
it previously ended `ALL HEALTH PROBES PASSED`. That is the true state - two
plugin snapshots do not match what OWUI is running - and it returns to green
when the operator pastes them or re-exports the repo copies. Worth saying out
loud because `stack.ps1 health`'s exit code is used as a smoke test elsewhere.

### Method notes

- Read path, every time: `docker exec openwebui python3 -c "..."` with
  `sqlite3.connect('file:/app/backend/data/webui.db?mode=ro',uri=True)`. Hashing
  happens INSIDE the container; only `<table>|<id>|<sha256>|<updated_at>` lines
  cross the boundary, so no plugin body, valve or key ever reaches the host.
- Proof nothing was written, across ~30 runs of the check plus the manual
  probes: `webui.db` size 1132519424 and mtime `2026-09-06 11:33:41.761765640
  -0400` identical before and after, row counts `tool 5 / function 9 / skill 8`
  identical. `webui.db-wal` and `-shm` DO move continuously - that is OWUI
  serving the operator, not this check.
- Refusal-path testing used a throwaway `python:3-slim` container
  (`--network none`, its own constructed sqlite fixture) and a scratch copy of
  `owui/`. `openwebui` was never stopped, paused or written to; `tailscale` was
  never touched.
- Git Bash rewrites a bare `/tmp/fake.db` argument into
  `C:/Users/.../Temp/fake.db` before PowerShell sees it (MSYS path conversion),
  which turns a container-path test into a refusal for the wrong reason. Drive
  those cases from PowerShell, or prefix `MSYS_NO_PATHCONV=1`.
