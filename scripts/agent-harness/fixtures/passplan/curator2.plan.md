# Test plan - curator2

Anchor: `queue.ps1 -Show -Id curator2` (read `-Show -Id curatorpool` too: it is
the richer original specification). Branch `work/curator2`, worktree
`wt-curator2`. Parent commit `d908d89`. **OB1 commit
`3ab8a3acd35c090892d25f0efe8207f7a8eb152a`** on OB1 branch
`fix/research-curator-resilient-pool`.

**The failure this covers is not "the database is down"** - that was always
visible. It is "the pool hands back a connection that looks fine and dies on
first use", which is what a database restart or a NAT/conntrack drop leaves
behind, and which the old plain `Pool` could neither detect nor recover from.
244 research runs lost their entire output to it between 2026-06-19 and
2026-08-31.

Read `documentation/notes/research-curator-broken-pipe-2026-08-31.md` first -
the incident, the replay record, and (at the end) the findings from this
re-implementation.

---

## T0 - the rule that destroyed the predecessor. Do this FIRST.

The previous attempt at this item was lost because its OB1 commit was never
pushed. Do not assume; check:

    cd OB1 && git ls-remote origin fix/research-curator-resilient-pool
    git -C OB1 rev-parse HEAD
    git ls-tree HEAD OB1        # in the parent, at commit d908d89

All three must show `3ab8a3acd35c090892d25f0efe8207f7a8eb152a`. If ls-remote
does not, **FAIL the item immediately** - the gitlink is unreachable and a fresh
`--recurse-submodules` clone breaks.

## T1 - unit: the recovery path (automated)

    cd OB1 && deno test integrations/research-curator/

Expect **16 passed, 0 failed, 1 ignored**. The ignored one needs net:

    cd OB1 && deno test -A integrations/research-curator/     # 17 passed, 0 failed

Then the honesty gate's pure half:

    cd OB1/integrations/research-service && deno test lib.test.ts   # 24 passed

**Do not accept a green run as evidence on its own.** Mutate the fix and confirm
the tests bite:

1. In `OB1/integrations/research-curator/pool.ts`, comment out the probe line in
   `connect()` (`await client.queryArray("SELECT 1");`) and return the client
   directly. `deno test -A integrations/research-curator/` must FAIL - recorded
   on 2026-09-04 as **6 of 7 pool tests failing**. Restore with
   `git checkout -- integrations/research-curator/pool.ts`.
2. In `pool.ts`, replace the `CONN_ERROR` regex with openbrain-mcp's narrower
   original (quoted verbatim in the findings note, finding 1). The test
   "a REAL severed socket on first acquire is classified AND recovered from"
   must FAIL with `classifier missed a REAL severed connection: ConnectionAborted
   ... (os error 10053)`. That test is the one that is not a mock; if it passes
   with the narrow regex, something is wrong with the harness, not the fix.

If fewer tests fail than stated, the tests are not covering the defect and this
item should FAIL.

## T2 - live: a real severed connection, real Postgres (manual, isolated)

The header of `OB1/integrations/research-curator/proof/severed-connection.ts`
has the commands. Summary:

    docker run -d --rm --name curator-drill-pg -e POSTGRES_PASSWORD=drillpw \
      -e POSTGRES_DB=openbrain -p 55432:5432 postgres:16
    cd OB1/integrations/research-curator
    deno run -A proof/severed-connection.ts --plain     # the OLD code
    deno run -A proof/severed-connection.ts             # the fix
    docker rm -f curator-drill-pg

Recorded 2026-09-04, verbatim:

    drill: PLAIN new Pool() (pre-2026-08-31 curator) ...
      before: SELECT 1 -> 1
      severed: pg_terminate_backend killed 1 backend(s), incl. the pooled one
      after: ConnectionError: The session was terminated unexpectedly
    RESULT: plain pool LOST the query - this is the defect

    drill: ResilientPool (pool.ts) ...
      before: SELECT 1 -> 1
      severed: pg_terminate_backend killed 1 backend(s), incl. the pooled one
      after: SELECT 1 -> 1
      pool rebuilds: 1
    RESULT: RECOVERED

**Both halves are required.** A passing new-code run alone does not show the
change was necessary. If the plain pool ALSO survives, FAIL the item and say so -
it would mean the diagnosis is wrong.

Constraints: never point this at `openbrain-db`; never attach test containers to
the `ai-stack_*` anchor networks; never tag an image `:local`; tear down after.

## T3 - live: the SERVICE recovers, not just the pool (manual, isolated)

Same throwaway Postgres. Run the real curator process against it and drive a
real HTTP route across a real database restart:

    docker run -d --rm --name curator-drill-pg -e POSTGRES_PASSWORD=drillpw \
      -e POSTGRES_DB=openbrain -p 55432:5432 postgres:16
    cd OB1/integrations/research-curator
    DB_HOST=127.0.0.1 DB_PORT=55432 DB_NAME=openbrain DB_USER=postgres \
      DB_PASSWORD=drillpw MCP_ACCESS_KEY=drillkey PORT=8899 deno run -A index.ts &
    curl -s localhost:8899/health
    docker restart curator-drill-pg && sleep 3
    curl -s localhost:8899/health

Recorded 2026-09-04:

    {"ok":true,"db":true,"pool_rebuilds":0}
    {"ok":true,"db":true,"pool_rebuilds":1}      # next request after the restart

And the same drill on the OLD code (`git stash` the curator `index.ts`, run on
PORT=8898):

    {"ok":true,"db":true}
    {"ok":false,"db":false}    # and STILL false at +5s +10s +15s +20s +25s

The old service never self-heals - it stays 503 until the container is
restarted, which is exactly the incident. `pool_rebuilds` is the witness that
the recovery happened rather than the failure simply not occurring.

## T4 - the loud error (read the output, do not grep for it)

Point a throwaway curator's `DB_HOST` at a dead host and POST
`/ingest/research-package` with a package carrying several `sources` and a
tagged `synthesis`. Read the log line and the 500 body. Both must name:

- `stage=` (`embed` / `resolve` / `persist` / `claims`)
- `research_key=`
- how many sources AND how many grounded claims went unwritten
- the query text

The old line was `ingest failed: Broken pipe (os error 32)` and named none of
it. A line that is merely longer is not a pass - ask whether an operator reading
it at 3am would know what was destroyed. The counts must be *right*: the claim
count comes from `parseSynthesisClaims`, so a synthesis with N grounded
`[SOURCED]`/`[INFERRED]` claims must report N, not 0.

Also check the partial case: when persist SUCCEEDS and only the claim write
fails, the ingest must still return **200** with `claims_error` in the body, and
the log must say `stage=claims` with the sources counted as written.

## T5 - a run that promoted nothing must not read as success (needs the stack)

This is the criterion whose live half I could not stage - the full path needs
`openbrain-research` + `openbrain-curator` + the embedding and persist services.
Take a plane lease (`lease.ps1 -Acquire -Name open-brain -Owner <you>`) and run
it against images built from this branch (tag `:wt-curator2`, never `:local`).

With a curator that returns 500 (point its `DB_HOST` at a dead host), submit a
research job and then read the row:

    SELECT status, error, progress FROM research_jobs WHERE id = '<job>';

Expect:
- `status = 'error'`   (was `'done'`)
- `error` NOT NULL, beginning `curator: the research completed but was NOT filed
  into Open Brain - ...`   (was NULL)
- `progress` = `{"phase":"error","message":"backstop=<...> curator=FAILED"}`
  (was `{"phase":"done","message":"backstop=complete"}`, where "complete" was
  only the initial value of the backstop variable)
- `result` STILL populated - `synthesis`, `prose`, `cited_sources`, `rendered`.
  This is deliberate: the report is real work and `result` is what a replay
  needs.

Name the column you read and what it said.

## T6 - the interactive path must not regress

Blocking path (verified 2026-09-04 without OWUI, by driving the tool's own
polling loop against a fake engine returning `status='error'` with a populated
`result`). The drill is small enough to rebuild: stub `aiohttp` with a session
whose `post` returns `{"job_id": ..., "callback_armed": false}` and whose `get`
returns the error job, then call `Tools().deep_research(...)`.

Result recorded: the tool returned the "Not saved to Open Brain" banner, the
reason, the job id, AND the full report. With the `deep_research.py` hunk
stashed it returned `Research failed: ...` and the report was gone.

The real check needs OWUI. `owui/tools/deep_research.py` is deploy-by-paste
(`owui/manifest.csv` maps it to OWUI id 15452), so the repo file is NOT live
until pasted. In a chat, force a filing failure and confirm:

- blocking path: the tool returns the report behind the banner, not
  `"Research failed: ..."`.
- async path: `notifyChat` writes the same banner above the report.

**A pass requires seeing the report survive.** If either path drops it, FAIL -
turning a silent loss into a loud loss that also destroys the user's answer is
worse than the defect.

## T7 - no regression on the happy path

A normal successful run must still be `status='done'`, `error IS NULL`, and
`progress.message` = `backstop=<...> curator=filed`, with no banner anywhere.
A dry run or a run with no cited sources reads `curator=skipped(nothing to
promote)` and is also `done` / `error IS NULL`.

---

## Things the reviewer should scrutinise, not take on trust

1. **The 5b gate does not cover this code.** `check-ob1-recipe-tests.ps1`
   reported `8 test file(s), 48 tests, 48 pass` - those are OB1 **recipes**.
   Everything in this change is under `OB1/integrations/`, which that gate does
   not run. T1 is the real gate.
2. **`deno test integrations/research-service/` is broken from the OB1 root and
   was before this branch.** Verified by stashing every change and re-running:
   `orchestrator.test.ts` needs the subdirectory import map, `--allow-env`, and
   a reachable `openbrain-db`. Run that suite from inside its own directory: 62
   passed, 1 failed, the failure being that pre-existing integration test.
   Do not fail this item for it - but do check the claim rather than believing
   it.
3. **Scope widening beyond the anchor's artifact line.** The anchor named
   `pool.ts`, `pool.test.ts`, the two `index.ts` files and the carried-forward
   `deep_research.py`. The change also touches:
   - `OB1/integrations/research-service/lib.ts` + `lib.test.ts` - the
     curator-outcome decision was extracted there as a pure, tested function
     because `executeJob` cannot be unit-tested. Same service, same intent, and
     it is what makes acceptance criterion 2 testable rather than merely
     inspected.
   - `OB1/integrations/research-curator/proof/` - the live drill scripts
     criterion 4 demands (named in the predecessor anchor's artifact line).
   - `documentation/notes/research-curator-broken-pipe-2026-08-31.md` - the
     findings sink the anchor names.
   If you think that widening needs the operator, it wants `-AmendAnchor`, not a
   quiet pass.
4. **Three other consumers change behaviour** (findings note, finding 2):
   agent-org grounding/advisory, Idea Refinery and the daily digest all treat
   `status='error'` as total failure and will now drop a report they previously
   accepted without knowing it was unfiled. Only the two readers the anchor
   named were fixed. Decide whether that is acceptable here or wants its own
   item; it is recorded either way.
5. **The claims-write failure now rethrows** where it used to be swallowed. The
   ingest still returns 200 (the sources landed), but the error reaches the
   response body as `claims_error` and the job row as a non-NULL `error` with
   `status='done'`. Check that nothing downstream treats a non-NULL error on a
   done row as a hard failure.

## Out of scope - do not fail the item for these

- Replaying the historical backlog.
- `fetchPage`'s admission gate (a login wall passes because non-empty is the
  only bar) - separate defect, recorded in the findings sink.
- Refactoring `ResilientPool` into a module shared with
  `integrations/kubernetes-deployment`. It is deliberately copied - including
  the fact that the sibling's classifier still has the gap finding 1 describes.
- Alerting or monitoring.

## Deploy note (NOT part of the test)

Nothing here is live on merge. `openbrain-curator` and `openbrain-research` are
Deno images that must be rebuilt and recreated, and the OWUI tool is
deploy-by-paste. The running curator today is the OLD code.
