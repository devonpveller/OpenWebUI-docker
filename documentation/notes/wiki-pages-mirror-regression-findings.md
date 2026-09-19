# Findings: wiki_pages mirror regression (2026-08-28 → 09-02)

Findings sink for harness item `wiki-mirror-fix` (anchor confirmed 2026-09-02).
The deliverable itself is OB1 `09f70f4` (extractLinks binding fix + warnOnce)
plus a backfill run; everything below is TRUE but OUT OF SCOPE for it.
Each claim states how it was checked and when.

## 1. A test that catches the bug existed the whole time — nothing runs it

`OB1/recipes/_shared/wiki-pages.test.mjs` fails 5/10 on the broken code
(verified 2026-09-02 by running `node --test` inside the live
`openbrain-wiki` container: 5 fail with the exact `ReferenceError`).
No CI job, pre-commit hook, image-build gate, or service startup executes
the recipe tests. The regression shipped on 2026-08-28 and would have been
one red test at commit time. Deciding *where* recipe tests should run
(OB1's repo CI vs. the wiki image build vs. a parent-repo check) is its own
piece of work — the wiki-viewer already has an esbuild gate precedent
(see memory: wiki-dynamic-index-build).

## 2. `warnOnce` is one-shot across ALL failure kinds

`wiki-pages.mjs` uses a single module-level `warned` flag, so the first
degradation of ANY kind (`upsert`, `delete`, now `queue`) suppresses every
later message of every other kind for the life of the process. For the
short-lived CLI writers that's per-compile and mostly fine; noted because
the fix routes parse failures through it — a compile that hits both a parse
bug and an upsert outage logs only the first. Checked against the code path
(`warnOnce`, `wiki-pages.mjs`) on 2026-09-02, not fixed.

## 3. The mirror can only shrink while writes are broken

`deleteWikiPages()` takes slugs directly (no parse), so orphan-sweep
deletions kept landing during the 5-day write outage — e.g. the 09-02 08:28
compile deleted 11 entity + 13 leaf rows against zero inserts. Nothing
reconciles "rows deleted but never re-added"; `countWikiPages()` exists for
a per-compile reconciliation log, but a count comparison would not have
fired here anyway since files on disk kept growing too. Verified from
`docker logs openbrain-wiki` (orphan-sweep lines) and row counts
(47,814 rows vs 59,299 vault `.md` files) on 2026-09-02.

## 4. The `notes` lane was immune — by design, and that design is why

Workbench-authored notes write `wiki_pages` through the Deno workbench's own
DB pool with its own deliberate mirror of `extractLinks`
(`docker/workbench/src/util/notes-parse.ts`, documented as a mirror because
the workbench image build cannot see `/recipes`). Verified: `note` rows
current to 2026-08-31 while every compiler-written class froze at 08-28.
The "deliberate mirror, both sides unit-tested" pattern did its job; the
single-sided re-export did not.

## 5. Unrelated log observations (not investigated)

Seen in `openbrain-wiki` logs while diagnosing, verified only as "the log
says this", 2026-09-02:

- Recurring slug collisions: entity #84291 "Nate's Substack", #97901
  "Dr. Sam Illingworth", #141815 "e-Cycle Inc.", #142561 "DALL·E" write
  `-1`-suffixed files every compile. The recipe suggests disambiguating
  canonical names.
- `planned manifest: 2 queued page(s) (+1065 unlinked, not backfill-eligible)`
  — the unlinked backlog grows (~933 → 1065 over 09-01 → 09-02).

## 6. Workbench search is keyed even on obnet

`GET http://openbrain-workbench:8000/workbench/search` from the wiki
container returns `401 {"error":"unauthorized"}` without credentials
(probed 2026-09-02). Not a bug — but a tester probing the acceptance
criterion must go through the viewer (`:8444` tailnet route) or supply the
workbench key; a bare in-network curl "proving search is broken" would be
proving auth, not search.
