# curator2 — tester evidence (tester-curator2-sub1, 2026-09-04)

Tested work/curator2 @ d908d89, OB1 @ 3ab8a3a. Worktree wt-curator2 left CLEAN
(verified `git status --short` empty in parent AND OB1; HEADs unchanged).
All mutation work was done on COPIES in this scratch dir; the worktree was
never mutated.

## T0 — gitlink reachability (the rule that destroyed the predecessor)  PASS

    $ git -C OB1 ls-remote origin fix/research-curator-resilient-pool
    3ab8a3acd35c090892d25f0efe8207f7a8eb152a	refs/heads/fix/research-curator-resilient-pool
    $ git -C OB1 rev-parse HEAD          -> 3ab8a3ac...
    $ git ls-tree HEAD OB1               -> 160000 commit 3ab8a3ac...	OB1

All three agree. Checked by me, not assumed.

## T1 — unit suite, GREEN                                              PASS

    $ deno test integrations/research-curator/
    ok | 16 passed | 0 failed | 1 ignored (251ms)

    $ deno test -A integrations/research-curator/
    ok | 17 passed | 0 failed (378ms)

    $ cd integrations/research-service && deno test --allow-env lib.test.ts
    ok | 24 passed | 0 failed (48ms)

Matches the developer's report exactly.

## T1 — RED mutation 1: probe removed from connect()                   PASS

Ran on a scratch COPY of pool.ts/pool.test.ts. Replaced the probe block with
`client = await this.#pool.connect(); return client;`

    FAILED | 1 passed | 6 failed (30ms)
    FAILURES:
      connection-level failure on first acquire is retried against a REBUILT pool
      every acquire is probed - a stale socket fails at connect(), not mid-ingest
      a SQL error is surfaced immediately - no rebuild, no retry
      a database that stays down exhausts the attempts and throws the connection error
      concurrent failures share ONE rebuild (single-flight)
      a REAL severed socket on first acquire is classified AND recovered from

Reproduces the reported 1-passed/6-failed exactly. The tests bite.

## T1 — RED mutation 2: narrow openbrain-mcp regex                     PASS (with caveats)

Replaced CONN_ERROR with the verbatim narrow regex from
integrations/kubernetes-deployment/index.ts:100.

    FAILED | 6 passed | 1 failed (73ms)
    a REAL severed socket on first acquire is classified AND recovered from ... FAILED
    error: ConnectionAborted: An established connection was aborted by the software
           in your host machine. (os error 10053)
        at async ResilientPool.connect (pool.ts:105:9)

CAVEAT 1 (plan inaccuracy, not a code defect): the plan says this fails with the
message `classifier missed a REAL severed connection: ...`. It does not — that
string exists nowhere. The actual failure is the RAW error rethrown uncaught out
of ResilientPool.connect, which proves the same thing (unclassified => no
rebuild) but the plan's quoted text is wrong.

CAVEAT 2 (durability): the real-socket test is `ignore`d without --allow-net, so
the DEFAULT command `deno test integrations/research-curator/` passes 6/6 with
the defective narrow regex. Only the -A run catches a classifier regression.

## Ruling on the openbrain-mcp "live defect" claim: HALF TRUE, MIS-FRAMED

The developer claims openbrain-mcp's isConnError is live-defective because it
never matches Deno's CamelCase class names, so a real severed socket raising
`ConnectionAborted (os error 10053)` goes unclassified.

TRUE — the narrow regex does not match that string; reproduced above.

FALSE — as a production defect. os error 10053 is WSAECONNABORTED, i.e. WINDOWS.
openbrain-mcp runs on LINUX:

    $ docker inspect openbrain-mcp --format '{{.Platform}}'  -> linux
    $ docker exec openbrain-mcp uname -s                     -> Linux

On Linux (denoland/deno:2.8.1 container, same severed-socket scenario):

    NAME    : BrokenPipe
    MESSAGE : Broken pipe (os error 32)
    NARROW(openbrain-mcp) MATCHES: true

And:

    ConnectionRefused: Connection refused (os error 111)
    NARROW(openbrain-mcp) MATCHES: true

And the realistic deno-postgres path (pg_terminate_backend) surfaces

    ConnectionError: The session was terminated unexpectedly

which the narrow regex matches three different ways.

Also imprecise: the narrow regex DOES contain one CamelCase class name
(`connectionerror`), so "never matches CamelCase class names" is overstated.

CONCLUSION: openbrain-mcp's ResilientPool is NOT live-defective in its
production runtime. The gap is a real but LATENT PORTABILITY gap that only bites
where the OS strerror text is Windows-flavoured — which is exactly where this
developer's tests run. Hardening the new classifier was right; the report's
framing overstates the blast radius. This does NOT undermine pool.ts: the wide
regex is a strict SUPERSET of the narrow one, and the live drill proves recovery
independently of classifier width.

Over-match check (the risk a wider regex introduces): 12 real Postgres
SQL/quota errors (syntax error, relation does not exist, duplicate key, type
"vector" does not exist, column does not exist, remaining connection slots are
reserved, too many connections for role, permission denied, value too long,
deadlock detected, statement timeout, not-null violation) run through the
SHIPPED isConnError:

    no over-matches across 12 real SQL/quota errors

## T2 — LIVE severed-connection drill — RUN BY ME, BOTH HALVES         PASS

Ad-hoc lease `curator2-drill-pg` held and released. Throwaway
`curator-drill-pg-t1` (postgres:16, host :55432, default bridge — never an
ai-stack_* network, never a :local tag). openbrain-db never touched.

    $ deno run -A proof/severed-connection.ts --plain --port 55432
    drill: PLAIN new Pool() (pre-2026-08-31 curator) against postgres@127.0.0.1:55432/openbrain
      before: SELECT 1 -> 1
      severed: pg_terminate_backend killed 1 backend(s), incl. the pooled one
      after: ConnectionError: The session was terminated unexpectedly
    RESULT: plain pool LOST the query - this is the defect

    $ deno run -A proof/severed-connection.ts --port 55432
    drill: ResilientPool (pool.ts) against postgres@127.0.0.1:55432/openbrain
      before: SELECT 1 -> 1
      severed: pg_terminate_backend killed 1 backend(s), incl. the pooled one
      after: SELECT 1 -> 1
      pool rebuilds: 1
    RESULT: RECOVERED

Both halves reproduce the developer's record verbatim. The change is NECESSARY
(plain pool loses the query) and SUFFICIENT (ResilientPool recovers, rebuilds=1).

## T3 — LIVE service-level recovery across a real DB restart           PASS

New code (branch index.ts, PORT=8899):

    --- health BEFORE ---   {"ok":true,"db":true,"pool_rebuilds":0}
    --- docker restart curator-drill-pg-t1 ---
    --- health AFTER ---    {"ok":true,"db":true,"pool_rebuilds":1}
    --- again ---           {"ok":true,"db":true,"pool_rebuilds":1}

Old code (git archive 48c0363, PORT=8898):

    --- OLD health BEFORE --- {"ok":true,"db":true}
    --- docker restart ---
    t+0s:  {"ok":false,"db":false}
    t+5s:  {"ok":false,"db":false}
    t+10s: {"ok":false,"db":false}
    t+15s: {"ok":false,"db":false}
    t+20s: {"ok":false,"db":false}
    t+25s: {"ok":false,"db":false}

The old service never self-heals. pool_rebuilds=1 is the witness that recovery
happened rather than the failure not occurring.

EXTRA FINDING (in the fix's favour, not claimed by the developer): the OLD code's
`new Pool(cfg, 8)` is EAGER, so with the DB down at boot the process dies:

    error: Uncaught (in promise) ConnectionRefused: ... (os error 10061)
      at async Pool.#initialize (pool.ts:198:7)

The new lazy factory removes that startup crash as well.

## T4 — the loud error line                                            PASS

Curator on a dead DB (127.0.0.1:59999), fake embedding endpoint, package with
3 sources and a synthesis carrying 3 GROUNDED claims + 1 uncited [SOURCED]
(must be dropped) + 1 [GAP] (not a claim).

    HTTP_STATUS=500
    {"error":"ingest_failed","stage":"resolve",
     "research_key":"tester-curator2-T4-deadDB",
     "query":"what happens to the research package when the curator database is dead?",
     "sources_unwritten":3,"claims_unwritten":3,
     "detail":"ConnectionRefused: No connection could be made because the target
               machine actively refused it. (os error 10061)"}

    log: ingest FAILED stage=resolve research_key=tester-curator2-T4-deadDB
         LOST 3 source(s) + 3 grounded claim(s) UNWRITTEN
         query="what happens to the research package when the curator database is dead?"
         cause: ConnectionRefused: ... (os error 10061)

Names stage, research_key, sources, claims, query, cause. The claim count is
RIGHT (3, not 5 and not 0): the uncited [SOURCED] and the [GAP] are correctly
excluded. An operator reading this at 3am knows what was destroyed. Compare the
old line: `ingest failed: Broken pipe (os error 32)`.

## T4 — partial case (persist OK, only claims fail)                    PASS

Seeded minimal threads/sources tables, stubbed PERSIST_URL to report
sources_written=3, left the claims tables absent.

    HTTP_STATUS=200
    {..., "persist":{"sources_written":3,...}, "claims":null,
     "claims_error":"PostgresError: type \"vector\" does not exist"}

    log: ingest FAILED stage=claims research_key=tester-curator2-T4-partial
         LOST 0 source(s) + 3 grounded claim(s) UNWRITTEN ...
         cause: PostgresError: type "vector" does not exist

200 + claims_error in the body, stage=claims, sources correctly counted as
WRITTEN (LOST 0). Exactly as specified.

## T5 — criterion 2, the job row. COLUMNS NAMED.                       PASS (scoped — read the caveat)

Could NOT run the full stack path (needs openbrain-research + openbrain-curator
+ embedding + persist, and a plane lease on live services). I did NOT substitute
a grep. Instead I drove the SHIPPED classifier (`classifyCuratorOutcome`
imported from lib.ts) plus the SHIPPED UPDATE statement (copied verbatim from
research-service/index.ts:661-663, same $1..$6 binding) against a REAL
research_jobs table in the throwaway Postgres.

Columns I read: **status, error, progress, result**.

    id        | status | error                                                    | progress
    ----------+--------+----------------------------------------------------------+---------
    J-fail    | error  | curator: the research completed but was NOT filed into   | {"phase":"error","message":"backstop=complete curator=FAILED"}
              |        | Open Brain - Broken pipe (os error 32)                   |
    J-partial | done   | curator PARTIAL: sources filed, grounded claims NOT      | {"phase":"done","message":"backstop=complete curator=partial"}
              |        | written - PostgresError: type "vector" does not exist    |
    J-ok      | done   | (NULL)                                                   | {"phase":"done","message":"backstop=complete curator=filed"}
    J-skip    | done   | (NULL)                                                   | {"phase":"done","message":"backstop=complete curator=skipped"}

`result` STILL populated on ALL FOUR rows: `result->>'rendered'` = "REPORT BODY",
synthesis and cited_sources both non-null — including on the FAILED row. That is
the deliberate behaviour the anchor requires (the report is real work; result is
what a replay needs).

CAVEAT, stated plainly: this proves the classifier + the SQL + the binding
produce the specified row. It does NOT prove executeJob reaches this code on a
real run — that is the half the plan admits could not be staged, and I could not
stage it either without taking the open-brain lease and rebuilding two Deno
images. T7's happy path is covered here at the same (scoped) level: J-ok and
J-skip are done / error NULL / curator=filed|skipped.

## T6 — the interactive path must not regress          PASS (blocking path driven; chat half NOT run)

Rebuilt the drill: stubbed aiohttp (post -> {job_id, callback_armed:false};
get -> status='error' with a fully populated result), imported the repo file,
called Tools().deep_research(...).

NEW CODE (branch owui/tools/deep_research.py):

    > **Not saved to Open Brain.** This research completed and the findings below
      are real, but filing them failed, so they are NOT searchable and will not
      appear in the wiki.
    >
    > Reason: curator: the research completed but was NOT filed into Open Brain - ...
    >
    > The full result is retained on job `job-T6-drill` and can be replayed.

    THE FULL REPORT BODY - alpha, beta, gamma.
    ---
    **Sources**
    1. [A](https://example.invalid/a)

    BANNER PRESENT : True
    REPORT PRESENT : True
    BARE-FAIL ONLY : False

BASE CODE (git show <merge-base>:owui/tools/deep_research.py), same drill:

    Research failed: curator: the research completed but was NOT filed into Open Brain - ...
    BANNER PRESENT : False
    REPORT PRESENT : False
    BARE-FAIL ONLY : True

RED -> GREEN reproduced by me. The report SURVIVES with the fix and is DESTROYED
without it.

Async path (notifyChat): verified by reading index.ts — notSavedBanner() is
prepended when outcome.status==='error'. I checked the two banners are the same
words: both contain "Not saved to Open Brain.", "> Reason:", "The full result is
retained on job", "and can be replayed."

NOT RUN: the chat half (real OWUI). owui/tools/deep_research.py is
deploy-by-paste (owui/manifest.csv -> OWUI id 15452), so the repo file is not
live. I did not run it in a chat, and I am not passing on the developer's word
for that half either — I record it as UNVERIFIED-IN-CHAT. The mechanism is
proven at the code level in both directions.

## Plan claim #2 — the pre-existing research-service failure           VERIFIED, not believed

    branch, from inside integrations/research-service:  62 passed | 1 failed
    base 48c0363, same command, same dir:               57 passed | 1 failed

Same single failure (./orchestrator.test.ts uncaught error) on BOTH. Pre-existing.
The branch adds 5 passing tests and introduces no failure.

## Plan claim #1 — the 5b gate does not cover this change              VERIFIED

scripts/checks/check-ob1-recipe-tests.ps1:138

    $testFiles = @(Get-ChildItem -Path (Join-Path $Root 'OB1\recipes') -Recurse -Filter '*.test.mjs' ...)

It globs OB1\recipes only. Every file in this change is under OB1/integrations/.
Its green says nothing here. T1 + T2 + T3 are the real gate. Confirmed by
reading the script, not taken on trust.

## Scope widening (plan point 3) — REASONABLE, no -AmendAnchor needed

- research-service/lib.ts + lib.test.ts (classifyCuratorOutcome, 53 + 39 lines):
  lib.ts is ALREADY the service's "pure helpers ... the testable core" module —
  nothing new was invented, the decision was put where that kind of decision
  already lives. executeJob is DB- and harness-bound and cannot be unit-tested,
  and acceptance criterion 2 demands a checkable rule. This extraction is what
  makes criterion 2 testable rather than merely inspected, and it is inside the
  anchor's named artifact ("research-service/index.ts so a job whose curator
  step failed cannot report status='done' with error NULL") — it is that change,
  factored. 5 new tests, all of which I ran.
- research-curator/proof/: acceptance criterion 4 demands live verification
  against a real severed connection. A drill script IS the artifact of that
  criterion, and it is named in the predecessor anchor's artifact line.
- documentation/notes/research-curator-broken-pipe-2026-08-31.md: the anchor's
  own findings_sink.

Verdict: proportionate, in-intent, and it makes the anchor checkable rather than
enlarging it. Reviewer may disagree; I do not think it needs the operator.

## Finding 2 — three other consumers (plan point 4) — NOT A NET REGRESSION

Verified all three by reading the code, not by trusting the report:

    OB1/integrations/openbrain-idea-refinery/index.ts:307  jr.status === "error" -> {ok:false}
    OB1/integrations/openbrain-idea-refinery/index.ts:418  status error/cancelled -> markFailed(idea)
    agent-org/agent-bridge/app/modules/grounding.py:151    -> GroundingResult(grounded=False)
    agent-org/agent-bridge/app/modules/grounding.py:226    -> AdvisoryAnswer(grounded=False, reason="failed")
    OB1/recipes/daily-digest/src/enrich/research-client.ts:160
                                                           -> {ok:false, error:"status=error: ..."}

(The digest file is at OB1/recipes/daily-digest/src/enrich/research-client.ts,
not the bare path the report gives; same line number, same code.)

The claim is TRUE: all three branch on status and will now DROP a report they
previously accepted. Frequency is not hypothetical — 244 runs in 2.5 months
would each have hit it.

Why it is nonetheless not a FAIL:

1. All three degrade GRACEFULLY. None crash, none destroy a user-facing answer.
   grounding.py -> grounded=False and the advisory "proceeds without it" /
   escalates honestly. digest -> that one enrichment item is dropped.
2. Idea Refinery's markFailed is a RETRY, not a destruction: it sets
   ideas.status='new', clears research_now, and increments
   metadata.research_attempts — and selectPending (index.ts:141) gates on
   `research_attempts < $2`, so the retry loop is BOUNDED. No churn loop.
3. The PARTIAL case (claims_error) is status='done', so it does not trip any of
   them. Only a TOTAL filing failure does.
4. The anchor's prohibition — "turning a silent loss into a loud loss that also
   destroys the user's answer" — is scoped to the interactive path (OWUI tool +
   notifyChat). Both were fixed and I reproduced the RED->GREEN myself (T6).
   These three are machine consumers, and what they previously did was silently
   consume research that was never filed into the knowledge base they assume was
   updated. Dropping it is defensible, arguably more correct.
5. It was flagged honestly by the developer in both the plan and the sink rather
   than being buried.

Recommend a FOLLOW-UP ITEM to teach those three the difference between "the
research died" and "the research is real but unfiled" (the job row already
carries enough to distinguish them: result->'rendered' is populated on the
failure row). Recorded here so it is a decision, not a discovery.

## Plan point 5 — non-NULL error on a done row

Checked all four consumers plus the OWUI tool: every one branches on `status`,
none on `error`. A partial (done + non-NULL error) is accepted normally
everywhere. No downstream treats it as a hard failure. No banner is shown for a
partial either — correct, since the banner says "NOT searchable" and on a
partial the sources DID land.

## Left state

- curator-drill-pg-t1 removed; no curator-drill containers remain.
- All scratch deno listeners (:8893-8899) killed.
- Ad-hoc lease curator2-drill-pg released.
- openbrain-curator / openbrain-research / openbrain-db / openbrain-mcp: running,
  untouched (uptimes unchanged: 47h / 47h / 5d / 2d).
- The pp-drill-* containers from another session were left alone.
- Worktree wt-curator2 CLEAN, parent HEAD d908d89, OB1 HEAD 3ab8a3a — unchanged.
- No :local tag built, no ai-stack_* network attached, no probe branch created.
