# Research output silently discarded since the DB restart — 2026-08-31

Operator report: "several research sessions this morning but I haven't seen the
ob-wiki running." Investigated 2026-08-31 ~15:10Z.

## The wiki was never the problem

`openbrain-wiki` has been up 32h and compiling all morning:

    04:45Z  1 regenerated
    05:17Z  47 regenerated, 11 thought + 3 source leaf pages, swept 11+1+31
    07:47Z  53 regenerated, 9 thought + 4 source leaf pages
    08:00Z  daily, 0 dirty
    12:06Z  78 regenerated, 28 thought + 2 source leaf pages
    14:24Z onward  0 dirty, 0 regenerated

It went quiet after 12:06 because it had nothing to do — not because it stopped.

## Root cause: the curator's DB pool never recovered from the DB restart

`openbrain-db` restarted **2026-08-30 13:43:17Z**. `openbrain-curator` had been
running since **07:23:56Z that day** — it predates the restart and holds the dead
sockets from before it.

Every research job since has died at the promotion step:

| job | created | curator result |
|---|---|---|
| 6cd54d14 | 08-30 05:00 | OK — 10 claims, 12 edges, thread resolved |
| a04d2d41 | 08-30 05:00 | OK — 14 claims, 15 edges, thread resolved |
| 2d1aef7a | 08-31 05:00 | `Broken pipe (os error 32)` |
| 16a66c5b | 08-31 14:03 | `Broken pipe (os error 32)` |
| da69156a | 08-31 14:03 | `Broken pipe (os error 32)` |
| d0cd7aa8 | 08-31 14:53 | `Broken pipe (os error 32)` |

Matching `openbrain-curator` log lines at 05:04:00, 14:07:13, 14:08:21, 14:56:56 —
`ingest failed: Broken pipe (os error 32)` — and nothing else in that log all day.

**Why the curator and not its sibling.** `openbrain-mcp` wraps its pool in a
`ResilientPool` (`integrations/kubernetes-deployment/index.ts:88`) that probes
each acquired connection with `SELECT 1`, classifies broken pipe / connection
reset / EOF as a connection error, rebuilds the pool and retries three times.
`openbrain-curator` uses a **plain `new Pool(...)`**
(`integrations/research-curator/index.ts:71`) with none of that — and the curator
also talks to Postgres directly (embedding, thread shortlist, thread resolution)
before delegating the write. So the resilient service was fine and the
non-resilient one in front of it was not.

Ruled out, with evidence:

- **Not Postgres closing connections.** No termination messages in the DB log at
  any failure time (only checkpoints), and `idle_session_timeout`,
  `idle_in_transaction_session_timeout`, `statement_timeout`,
  `tcp_keepalives_idle` are all `0`.
- **Not openbrain-mcp.** `RestartCount=0`, `OOMKilled=false`, started 08-30
  16:47Z (after the DB restart, so its pool was fresh). A direct
  `POST /research/persist` probe from the curator container returned **200** and
  wrote a row — proving the downstream write path was healthy the whole time.
  (Probe rows deleted afterwards.)

## The failure is invisible by design

A job whose entire output was discarded is still recorded as:

    status = 'done'      error = NULL      progress = {"phase":"done","message":"backstop=complete"}

`backstop=complete` is the *initial* value of the backstop variable
(`harness.ts:289`), so it means "no backstop tripped", not "the run produced
anything". The actual failure is buried in `result->'curator'->>'error'`, which
nothing surfaces. Job 16a66c5b fetched 40 pages, kept 16 sources and wrote 3,203
characters of prose — and none of it was promoted.

That is the whole reason this ran for ~10 hours unnoticed: the only visible
symptom is a wiki with nothing to compile.

## Fixed

`docker restart openbrain-curator` at 15:12:35Z. Verified: `GET /health` returns
`{"ok":true,"db":true}`.

## Not yet done

1. **The four lost runs are recoverable.** `research_jobs.result` retains the
   full synthesis, prose and cited_sources for all four. Replaying them means
   POSTing each stored package to the curator's `/ingest/research-package`.
   Nothing has been replayed.
2. **The curator should get the same `ResilientPool` its sibling has.** This is
   the actual fix; the restart only clears today's instance. The class of failure
   recurs on every DB restart.
3. **A run that promoted nothing must not report `done`.** Either fail the job on
   a curator error, or surface `result.curator.error` where the operator sees it.
   A silent total loss is worse than a red job.
4. Unrelated but seen while reading the DB log: three
   `FATAL: role "openbrain" does not exist` connection attempts (07:54:08,
   07:54:09, 12:10:59). Something is configured with a role that was never
   created. Not connected to this incident.

---

# CORRECTION and actions, same day

## The scope above is wrong: this is not new, and it is not 4 runs

The mechanism (the curator's non-resilient pool) is right. The trigger and the
scale are not. Grouping every job by its curator error:

    curator 500 ... "Broken pipe (os error 32)"   244 jobs   2026-06-19 -> 2026-08-31
    curator 502 ... "persist_failed: persist 500"  18 jobs   2026-06-08 -> 2026-06-12
    curator 500 ... "error sending..."              3 jobs   2026-06-14

**244 research runs have lost their output to this same failure over two and a
half months.** Yesterday's DB restart is not the cause; it is one instance of a
recurring trigger.

Weekly, over completed jobs, it is bursty rather than steady — which is the
signature of a pooled connection that goes dead and stays dead until the
container is restarted:

| week | jobs | broken pipe | persisted |
|---|---|---|---|
| 06-08 | 260 | 0 | 207 |
| 06-15 | 153 | 36 | 117 |
| 06-22 | 103 | 0 | 103 |
| 06-29 | 87 | **70** | 17 |
| 07-06 | 30 | 18 | 12 |
| 07-13 | 34 | **34** | **0** |
| 07-20 | 30 | 16 | 14 |
| 07-27 | 28 | 2 | 26 |
| 08-03 | 122 | 0 | 65 |
| 08-10 | 54 | 30 | 24 |
| 08-17 | 78 | 34 | 43 |
| 08-24 | 41 | 0 | 41 |
| 08-31 | 4 | **4** | **0** |

The week of 07-13 lost every single run. The week of 08-24 lost none. Nothing in
the pipeline noticed either way.

## Replay — DONE for the 2026-08-31 burst

All four of today's runs had their cited source still present in `sources` with
full content (staging succeeded; only promotion failed), so the package
`harness.ts` sends was rebuilt from `research_jobs.result` + the stored source
content and POSTed to the curator's `/ingest/research-package`:

    OK  2d1aef7a  thread=Human-AI Deskilling Risks  synthesis=b9bccbdc  sources_written=1
    OK  16a66c5b  thread=Human-AI Deskilling Risks  synthesis=004c6617  sources_written=1
    OK  da69156a  thread=Human-AI Deskilling Risks  synthesis=5879f7b6  sources_written=1
    OK  d0cd7aa8  thread=Human-AI Deskilling Risks  synthesis=57e712c7  sources_written=1

Verified: 4 `research_synthesis` sources and **29 claims** written. Each job row's
`result.curator` was overwritten with the real persist payload and stamped
`curator_replayed_at`, so the record now matches reality and a re-run is a no-op.

The remaining ~240 are NOT replayed. Many are old and superseded, and
`result.cited_sources` stores only `{url, title}` — the replay depends on the
source content still being in `sources`, which is likely for recent runs and
decreasingly so going back. That is an operator decision, not a default.

## The fix is anchored, not yet built

`queue.ps1 -Propose -Id curatorpool` (worktree `wt-curatorpool`, branch
`work/curatorpool`), covering both the pool and the silence. Awaiting
`-ConfirmAnchor`. Acceptance deliberately requires proving recovery against a
REAL severed connection: a happy-path test would have passed every day for the
last two and a half months while 244 runs were lost.

## Legitimacy audit of the 240 remaining, before replaying any of them

| check | result |
|---|---|
| origin | `notebook` 160, `owui` 80 — the digest pipeline and user-initiated Deep Research. **No test/agent/manual origins.** |
| test-looking queries | 0. (One regex hit, "…care so much about your carbon footprint", is a false positive on `foo`.) |
| real synthesis (>200 chars) | 231 of 240 |
| cited sources | 208 of 240 cite at least one source; 32 cite none (the curator is only called when a run cited something or reused claims — those 32 had reuse only, so there is nothing to promote) |
| source content still recoverable | **473 of 474 cited URLs are still in `sources` with >400 chars of content.** Staging always succeeded; only promotion failed. |

Sampled queries are unambiguously real work: *"Verbalizable Representations Form
a Global Workspace in Language Models"*, *"SycEval: Evaluating LLM Sycophancy"*,
*"Godot engine headless mode testing GDUnit CI"*, *"MSI Restore on AC Power Loss
BIOS"*, *"Brad Lightcap, OpenAI's longtime COO, is leaving"*.

### Replay set: 192

Excluded, with reasons:

- **32** cited nothing — no synthesis to promote.
- **16** had a LATER run of the same query that persisted successfully.
  Supersede-in-place is keyed on `research_key = rs-sha1(query + thread_id)`, so
  replaying these would overwrite newer content with older. Skipped deliberately.
- 0 excluded for a thin synthesis (that set is a subset of the 0-cited ones).

The 16 whose same query persisted *earlier* ARE replayed: there the lost run is
the newer one, so superseding is the correct direction.

Within the replay set, 21 queries appear more than once. The script runs
**oldest → newest** so that for any duplicated `research_key` the newest run is
the last write and therefore the surviving row.

Idempotent: each replayed job is stamped `result.curator_replayed_at` and the
eligibility query excludes anything already stamped, so a re-run is a no-op.

## Replay result — 192/192, verified against the database not the script

    DONE: 192 replayed, 0 skipped (no content), 0 failed

Verified independently of the script's own tally:

| check | value |
|---|---|
| jobs stamped `curator_replayed_at` | **196** (192 + the 4 done by hand earlier) |
| `research_synthesis` sources written | 169 |
| claims written | **1,721** |
| distinct threads touched | 69 |
| replays that returned an error payload | 0 |
| jobs still carrying an unreplayed broken pipe | 48 — exactly the deliberate exclusions (32 zero-cited + 16 superseded by a later good run) |

169 synthesis rows from 196 replays is correct, not a shortfall: supersede-in-place
is keyed on `research_key`, so the 21 duplicated queries collapse to one row each
and the 16 that superseded an *earlier* successful row updated it instead of
inserting.

**The oldest-first ordering was verified, not assumed.** Key
`rs-cc61ede7cc85c4e7` had two lost runs — `2fa52a99` (14:58, synthesis 1,368
chars) and `decd8f02` (15:08, 1,795 chars). The surviving synthesis row
`ff22b172` is 1,795 chars with md5 `f92db69b`, matching the NEWER run. One row,
newest content.

Downstream at the time of writing: source-extraction queue 48 complete / 49
processing / 86 pending, and the wiki watched the burst arrive (23 → 80 → 134 →
160 → 192 → 200 new) before settling into a compile at 15:56Z. Notebook hubs went
from 188 to 191 — the recovered threads created three new notebooks. The tail will
take a while to drain; that is the recovered material flowing through, not a fault.

## The hardened fix — BUILT (worktree `wt-curatorpool`), not yet live

Branch `work/curatorpool`. All checks green in the worktree; **nothing is
deployed** — the running curator is still the old code with a pool that happens
to be fresh from this morning's restart.

What changed:

| file | change |
|---|---|
| `OB1/integrations/research-curator/pool.ts` (new) | `ResilientPool` — probes every acquired connection with `SELECT 1`, classifies broken-pipe/reset/EOF via `isConnError`, rebuilds the pool single-flight and retries 3× with backoff. Lazy, not eager. Own module so it is testable without starting the HTTP server. |
| `.../research-curator/index.ts` | uses it (all 7 `pool.connect()` sites fixed by the swap); ingest failures now log stage + `research_key` + unwritten source/synthesis counts + the query; `/health` reports `pool_rebuilds`. |
| `.../research-service/index.ts` | a run whose curator step failed is `status='error'` with a non-NULL `error` and `progress.phase='not_persisted'` — no longer `done`/NULL/`backstop=complete`. `result` is still written (it is what a replay needs). |
| `owui/tools/deep_research.py` | **not named in the anchor.** Without it, `status='error'` would make the tool return "Research failed" and DISCARD the report — a regression on the interactive path. It now renders the findings behind a "Not saved to Open Brain" banner. Same banner on the async `notifyChat` path. |

Evidence:

- `deno test` in research-curator: **18 passed, 0 failed** (8 new).
- **Mutation-checked**: commenting out the `SELECT 1` probe fails **6 of 8** pool
  tests. A green suite that does not bite is how the original defect survived.
- **Live severed-connection proof**, isolated Postgres, container restarted
  underneath a live connection:

  | | before sever | after sever |
  |---|---|---|
  | old plain `Pool` | `before: OK` | `OLD CODE FAILED as expected: The session was terminated unexpectedly` |
  | `ResilientPool` | `before: OK (rebuilds=0)` | `after : OK (rebuilds=1)` |

  Both halves kept in `proof/` with a README; the counter-proof is the half that
  shows the change was necessary rather than merely harmless.
- Incidental: the plain eager pool also cannot survive being constructed while
  Postgres is still starting up — which is the same weakness behind the
  `claimNext failed: the database system is starting up` lines after every stack
  restart.

Blocked on the operator: `queue.ps1 -ConfirmAnchor -Id curatorpool -By <operator>`
before `-Submit` will accept it for test and review. The anchor's `artifact` line
predates the OWUI tool change and wants `-AmendAnchor`, which is the operator's
call, not mine.

## Submitted for test — 2026-08-31

Operator approved. Recorded and moved through the gate:

- **Anchor CONFIRMED** (`-ConfirmAnchor -Id curatorpool -By profnovice`), amended
  on confirmation to name `owui/tools/deep_research.py` in the artifact and to add
  a sixth acceptance criterion: *the interactive path must not regress — a run that
  produced a report but failed to file it still SHOWS the report behind a "Not
  saved" banner.* Turning a silent loss into a loud loss that also destroys the
  user's answer would be worse than the defect.
- **Committed** on `work/curatorpool`: OB1 `22f41b6` (pool + honesty), parent
  `f71772b` (OWUI tool + gitlink bump). Pre-commit hooks passed.
- **Submitted**: `queue.ps1 -Submit -Id curatorpool -Branch work/curatorpool`.
  State is now `testing`, awaiting a tester who is NOT the developer.

Two things the queue flagged that are not mine to do:

1. **OB1 `22f41b6` is not pushed to the OB1 remote.** MERGE-PROTOCOL line 333:
   the gitlink must be reachable there before the parent merge lands, or a fresh
   `--recurse-submodules` clone breaks. Pushing is outward-facing and was not
   asked for.
2. **`refactor/ai-stack-cleanup` is checked out in the main checkout**, so git
   will refuse a second worktree on it — the reviewer hands the merge back to the
   operator rather than performing it.

Still not live either way: both services are Deno images that must be rebuilt and
recreated, and the OWUI tool is deploy-by-paste (`owui/manifest.csv` maps file →
OWUI id). Until then the running curator is the old code, currently healthy only
because it was restarted this morning.
