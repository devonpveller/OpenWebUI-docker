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

## deploystate

- **The six pre-existing `verify-merge-protocol.ps1` reds have a second half the passplan
  entry above does not name: `core.hooksPath` is an ABSOLUTE path into the operator's
  checkout.** `git config --get core.hooksPath` returns `D:\Open WebUI\ai-stack\.githooks`
  (checked 2026-09-06 from `wt-deploystate`), so EVERY worktree runs the hook file that the
  OPERATOR's branch has loaded, whatever branch the worktree itself holds - while that hook
  invokes `./scripts/checks/*.ps1` relative to the COMMITTING worktree. `development`'s own
  `.githooks/pre-commit` names only three checkers and never mentions
  `check-corpus-exposure-producers.ps1` (`git show development:.githooks/pre-commit`); the
  line that fails, `.githooks/pre-commit:67`, comes from `refactor/ai-stack-cleanup`. The
  consequence is general, not a drill artefact: **any worktree on a branch that lacks a
  checker the operator's branch added cannot commit at all**, and the error names a file
  that does exist - in the other checkout. Out of scope here; the fix is a decision (make
  the hook tolerate a missing checker, or resolve the checker path from the hook's own
  directory), not a patch this item may take.

  *Corrected 2026-09-06 after attempt 1's tester ran the command this entry cites.* The
  sentence above said `development`'s hook "names only three checkers"; it names **four**.
  `git show development:.githooks/pre-commit | grep -n powershell.exe`, re-run before this
  correction was written:

      18: ... -File './scripts/checks/check-staged-secrets.ps1'
      25: ... -File './scripts/checks/validate-lineendings.ps1'
      34: ... -File './scripts/checks/check-llm-gateway-routing.ps1'
      43: ... -File './scripts/checks/check-project-configs.ps1'

  Four uncommented invocations (`grep -cE '^[[:space:]]*powershell\.exe'` -> `4`; the same
  count on the work line is `9`). The material half - that it never mentions
  `check-corpus-exposure-producers.ps1` - is unchanged and is what the entry rests on. The
  wrong number was written from memory of an earlier grep whose output I had narrowed with a
  second pattern; the command was cited but not re-run against the sentence. That is the
  same defect class this file exists to catch, in this file, by its author.

- **Four merged items on the live board shipped an image or a paste and the board records
  no surface for any of them.** Re-deriving history is out of scope by this item's anchor,
  so this is the list, produced by replaying each merge through the new derivation in a
  hermetic state dir (2026-09-06):

  | item | merge | what its merge range ships |
  |---|---|---|
  | `curatorimg` | `e72c678` | `image:openbrain-curator` |
  | `curator2` | `08c4ae1` | `image:openbrain-curator`, `image:openbrain-research`, `paste:owui/tools/deep_research.py` |
  | `opsdoor` | `b06057f` | `image:openbrain-curator`, `image:openbrain-research` |
  | `researchretry` | `7614556` | `image:openbrain-research` |

  `gate5d` (`5b79731`) ships nothing, which is why the anchor names it as the negative. The
  last two landed AFTER this item's anchor was written and are named here so the list is
  complete rather than as-of-Tuesday. Whether those deploys happened is recorded somewhere
  else or nowhere; this queue does not know, and after this change it would.

- **`[needs hand-off]` was one true instance in thirty-two, counted three times on the same
  day as the board moved.** All three counts are over the 42 item files directly, not over
  the tool's output:

  | when | `line_mergeable=false` | terminal | non-terminal |
  |---|---|---|---|
  | mid-afternoon (developer) | 30 | 30 | 0 |
  | ~an hour later (developer) | 31 | 30 | 1 - `owuidrift`, `testing` |
  | during attempt 1's test (tester) | 32 | 31 | 1 - `deploystate`, `testing` |

  The arithmetic reconciles exactly: this item's own `-Submit` added the 32nd row, and
  `owuidrift` moved from `testing` to `merged` mid-run, carrying one row from the
  non-terminal column to the terminal one. **The tester's 32/31 is the figure of record**,
  and `queue.ps1`, `verify-queue-defects.ps1` and the harness README now all cite it and
  point back at this table rather than each carrying a reading of their own (they held the
  developer's 31/30 until attempt 3; each was true when taken, and three sources quoting
  three moments of a moving count is how a reader concludes one of them is wrong);
  the substance is unchanged in all three readings - one true instance hidden among thirty
  or thirty-one stale ones, which is the whole reason the flag carried no signal. The figure
  `25` that an earlier draft comment and the phase PLAN
  (`documentation/implementation-guide/deploy-gate-and-curator-recovery/PLAN.md:56`)
  carried was not measured at all; `queue.ps1`, `verify-queue-defects.ps1` and the harness
  README now carry counted numbers. A count over a live board is a measurement with a
  timestamp, not a constant - so each of these says when it was taken.

- **`verify-merge-protocol.ps1` had no way to assert on what `queue.ps1` PRINTS, and the
  first check that tried was silently blind.** Every other `& $queue ...` in that drill ends
  in `| Out-Null` and asserts against the item file on disk; the `-List` check added by this
  item captured with `(& $queue -List 2>&1 | Out-String)` and got the empty string, because
  `queue.ps1` prints through `Write-Host`, which in PS5.1 does not flow into a pipeline. It
  read FAIL against a perfectly clean row. Fixed inside this item (a `Get-QueueBoard` helper
  that runs a child `powershell.exe`), and recorded because the failure mode is the
  workspace's recurring one - a check that runs and checks nothing - and the next assertion
  on printed text in that drill will meet it again unless it uses the helper.

- **`remove-worktree.ps1` counts a parent work branch's commits as this worktree's unmerged
  work.** `remove-worktree.ps1:115` asks `git log --oneline $MergedInto..$branch`, where
  `$MergedInto` defaults to the resolved WORK LINE (`:32`, `Resolve-WorkLine`) rather than
  to the branch the worktree was actually cut from. For a worktree cut from another work
  branch, everything inherited from that branch is reachable from `$branch` and not from
  the work line, so it is counted, printed as `commits not in <line>` and used to REFUSE
  the removal (`:128`). The registry records the real base, and two rows are of this shape
  today - `h2v2` and `h2v2b`, both `base: work/u8h2`
  (`.git/agent-worktrees/worktrees.json`, read 2026-09-06). **Not reproduced** - this is a
  reading of that code path, named in this item's anchor as out of scope and left for a
  separate item, which should start by running `remove-worktree.ps1 -WhatIfOnly -Id h2v2`
  and comparing the count against `work/u8h2..work/h2v2`.

- **This worktree's copy of THIS file is behind the line.** `work/deploystate` is cut from
  `49bb2db`, and `opsdoor` (`b06057f`) and `researchretry` (`7614556`) both appended
  sections to this file afterwards (`## opsdoor` and `## researchretry`, both present at
  `7614556`). The `## deploystate` section above is appended to the base version, so the reviewer's rebase will
  meet a tail conflict here; every section involved is append-only, so the resolution is to
  keep all of them in landing order.

### From attempt 1's test (tester-deploystate-sub1, 2026-09-06)

- **The `-Deployed` evidence check is LINE-SCOPED, not entity-scoped - deliberately, and it
  stays that way.** The tester wrote `openbrain-curator rebuilt from 6fba6b3 - checked
  openbrain-research instead: State.Health.Status=healthy` with
  `-Surface image:openbrain-curator` and it was ACCEPTED (exit 0). The check asks that some
  line mentioning the surface also carries a pin and a health state; it cannot ask that all
  three are *about* the same container, because that is a judgement about English prose and
  not something a regular expression settles. Tightening it would refuse far more honest
  evidence than it would catch dishonest evidence. Recorded rather than fixed, and now said
  out loud in `Test-DeployEvidence`'s own header, in MERGE-PROTOCOL step 6 and in the harness
  README, so nobody reads the check as stronger than it is. What it does guarantee is that
  the surface's own name must appear: evidence naming only the other container is refused
  with `no line of the evidence names it` (the tester confirmed that half too).

- **A `.ps1` written back in text mode on Windows silently doubles its carriage returns, and
  `git status` will not tell you.** `.gitattributes` has `*.ps1 text eol=crlf`, so the blob
  is stored LF and the checkout carries CRLF. A patch script that reads the CRLF working
  copy, inserts text containing CRLF and writes it back through a text-mode handle produces
  `CR CR LF`; git's clean filter strips exactly one CR and stores a literal CR in the blob,
  and the damage round-trips, so `git status` reads clean. Commit `ff34eda` of this item put
  **376 literal CR bytes** into `queue.ps1`'s blob that way: piping
  `git cat-file blob ff34eda:scripts/agent-harness/queue.ps1` through `tr -cd '\r' | wc -c`
  gives 376, and the same command on `49bb2db:...` gives 0 (both re-run 2026-09-06).
  PowerShell parses it, every drill stayed green, and nothing noticed. Fixed inside this item
  by rebuilding the file from the blob with every CR removed and the lines rejoined with one
  CRLF; the blob is pure LF again from `af6f483` onward.

  **How to check that the repair carried nothing else, from the tip.** An earlier version of
  this entry said the rebuilt file was "proven byte-equal to the previous blob with CRs
  stripped". That equality held at the repair STEP - a working state - and it is not what
  landed: the commit carrying the repair also carries attempt 2's changes, so **no revision
  in this branch satisfies it**. `7b5beb9`'s blob with CRs stripped hashes to `1d43a129`; the
  landed blob `af6f483:scripts/agent-harness/queue.ps1` is `0735e9fc`. The substance was
  true; the wording was stronger than any surviving artifact, which is exactly what this file
  is held to. The check that DOES survive, and the one to run - pure git, no temp files
  and no PowerShell traps (`diff` aliases `Compare-Object`, and `>` re-encodes):

      git diff --ignore-cr-at-eol 7b5beb9 af6f483 -- scripts/agent-harness/queue.ps1

  A CR anywhere but end-of-line would still show as a change, so this doubles as the check
  that the strip was lossless; all 376 CRs at `7b5beb9` were in fact CRLF pairs (bare CR 0,
  `CR CR LF` 0). The residual is **86 changed lines (9 removed, 77 added) in FOUR hunks**,
  each nameable from attempt 2's stated scope:

  | hunk | what it is |
  |---|---|
  | `@@ -493,9 +493,18 @@` | the derivation section header, rewritten for the manifest rule |
  | `@@ -773,16 +782,56 @@` | the `ls-tree` probe replacing `cat-file -e`, and the manifest-driven paste block replacing the `owui/`-path one |
  | `@@ -836,6 +885,16 @@` | a COMMENT block documenting the line-scoped evidence limit - no code |
  | `@@ -845,9 +904,18 @@` | the pin regex (hex, not merely `[0-9a-f]` with a digit) and the refusal message that states the rule |

  Nothing else rode in on the 376 whitespace-only lines. (Verified independently by attempt
  2's tester, who reported the same four hunks; the hunk headers move if the file does, so
  re-run the diff rather than trusting the offsets.)

  *That figure was 88 for one revision of this entry, and 88 is what attempt 2's tester
  reported too.* Both of us counted `diff -u ... | grep -c '^[-+]'`, which also matches the
  `--- old` and `+++ new` header lines: 88 minus 2. It is 86, and
  `git diff --ignore-cr-at-eol ... | grep -cE '^[-+][^-+]'` agrees. Nothing rests on the
  number - the claim is "four hunks, all nameable" - but two people produced the same wrong
  figure from the same lazy pattern, which is worth more than the two lines it cost.

  A related trap, found while writing the plan case for this one: **a PowerShell pipeline
  re-encodes a native command's bytes.** `git cat-file blob <rev>:<path> | tr -d '\r' |
  git hash-object --stdin` returns `1d43a129` in Git Bash and `5cae3219` in PowerShell 5.1,
  because PS decodes the first process's stdout into text lines and re-emits them with CRLF
  before `tr` ever runs. Any byte-level check of this kind belongs in bash, and any command
  quoted for one must say which shell it was measured in.

  Two standing lessons: write files as BINARY with the line ending chosen explicitly, and
  `git status` clean is not evidence that a file's bytes are what you meant
  (`git cat-file blob :<path>` is). A third, from the wording above: an equality asserted
  against a state that will not survive the commit is not a claim a reader can check - say
  what they can run at the tip. The first attempt at the repair was itself wrong - two
  chained `.replace()`s turned `CR CR LF` into a blank line, 376 of them - which is why the
  fix was asserted against the previous blob rather than eyeballed.

- **`queue.ps1 -Merged` printed git's `fatal:` above its own success message.** The probe for
  "does this OB1 integration directory have a Dockerfile?" was `git cat-file -e`, which
  writes `fatal: path 'integrations/<dir>/Dockerfile' does not exist in '<pin>'` to stderr
  for the ordinary absent case, and `Invoke-GitCapture` does not redirect stderr. Found by
  the tester replaying the real `de42243`. Fixed here (`ls-tree --name-only`, which exits 0
  either way and prints nothing when the path is absent) rather than recorded, because an
  operator taught to ignore a `fatal:` line is being taught the wrong lesson.

- **Two rules changed on the operator's call after attempt 1, both because the tester found
  a real false positive.** (a) A paste surface is now derived only for an `owui/` file that
  `owui/manifest.csv` LISTS. The rule was `any owui/** path`, and the real `owuidrift` merge
  `e989265` derived `paste:owui/manifest.csv` and `paste:owui/README.md` - two surfaces that
  could only ever be closed by inventing a container health state for a markdown file. The
  manifest is the file-to-OWUI-id map, so it already answers the question exactly; it is read
  from the merged tree, its `file` column is resolved by header name (the real manifest's
  last column moved from `bytes` to `sha256` between `08c4ae1` and `e989265`), and an
  unlisted `owui/` path is reported as a NOTE rather than dropped in silence. (b) A pin in
  `-Deployed` evidence must be HEX: the tester closed a real surface with
  `openbrain-research deployed at 1788720066 State.Status=running`, because a decimal epoch
  satisfies `[0-9a-f]{7,64}`. It is now 7-40 hex that is not simply a decimal number, or a
  labelled `sha256:<hex>`; a hex id that merely starts with a digit is still a pin. Both are
  drilled (D12's fixture carries a manifest and an unlisted `owui/README.md`; D13 refuses an
  epoch and a run number and proves the digit-leading hex id is still accepted).
