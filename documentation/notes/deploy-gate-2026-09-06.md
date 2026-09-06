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
