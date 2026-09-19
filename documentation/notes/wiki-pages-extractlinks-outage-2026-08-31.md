> **SUPERSEDED - FIXED IN PRODUCTION 2026-09-02.** The defect this note records was
> re-fixed on the then-current pin and landed: OB1 `09f70f4` (`fix/wiki-pages-extractlinks`,
> on the remote) via ai-stack merge `443c5d8`; mirror backfilled (notes/ excluded), live
> container 10/10. Aftermath + lessons: `wiki-pages-mirror-regression-findings.md`.
> The body below is the original record, unchanged.

# wiki_pages silent write outage, 2026-08-28 -> 2026-08-31

Status: **fix committed in OB1, not pushed, not deployed.** Backfill **not run** —
it is a deploy decision and this note is the input to it.

OB1 commit: `9b47135` on `fix/wiki-pages-extractlinks-binding` (parent `4fdc21c`,
which is the SHA the ai-stack gitlink currently pins). The parent gitlink is
**not** bumped: `9b47135` is not on any remote yet.

> **PARKED, 2026-08-31 (operator scope ruling).** This is NOT a DFU item - `dfu-done.ps1`
> will never gate on it - so no ai-stack branch or worktree is held open for it while the
> plan is finishing. The ai-stack branch `work/wikilinks` and its worktree were REMOVED and
> this note is the surviving record. The OB1 fix commit lived only inside that worktree's
> OB1 checkout, so before removal it was bundled to
> `D:\Open WebUI\_notes\parked-work\OB1-wiki-pages-extractlinks-fix.bundle`
> (verified "records a complete history"). Recover with `git bundle unbundle`. This note is
> backlog for after the DFU plan closes; the outage is real and unfixed in production.

---

## 1. What broke

OB1 `dfc6228` (2026-08-28 08:37:25 -0400 = **12:37 UTC**) split `extractLinks`
out of `recipes/_shared/wiki-pages.mjs` into `links.mjs` and left behind:

```js
export { extractLinks } from "./links.mjs";   // line 75
```

That form re-exports the name for **importers** and creates **no binding in the
module's own scope**. `parseWikiPage` calls `extractLinks(body)` twelve lines
later, so every call threw `ReferenceError: extractLinks is not defined` — 100%
of the time, for every page.

`queueWikiPage` wrapped its body in a bare `catch {}`. The ReferenceError went
in and nothing came out: the compilers kept reporting `compile ok ... 26
regenerated, 0 failed` while queueing zero rows, and `flushWikiPages` only logs
when `sent` is non-zero, so even the flush was silent.

Two defects, and each one hid the other. The binding bug alone would have been
found in an hour.

### Evidence (all first-hand)

| Check | Result |
|---|---|
| `docker exec openbrain-wiki node -e "...parseWikiPage(...)"` | `ReferenceError: extractLinks is not defined` |
| Same probe, 3,000 real vault pages, deployed image, `--network none` | throws on the first page |
| Same probe against the **fixed** module | 3,000 queued, 18,412 link targets, no throw |
| `backfill-wiki-pages.mjs --dry-run` on the deployed module | `[backfill] FAILED: extractLinks is not defined` |
| Same, fixed module | `walked=59213 upserted=0 failed=0` in 47s |
| `node --test recipes/_shared/wiki-pages.test.mjs`, deployed module | **5 of 10 RED**, since the day it shipped |
| `openbrain-wiki` logs, last 120h | not one `[wiki-pages] synced` line, and no `DRIFT` line |

`wiki_pages` writes by day (`updated_at`, verified by direct count):

```
08-26      16
08-27  39,602
08-28   8,285   <- all but one before 12:37 UTC; last bulk bucket is 12:00-15:00 UTC
08-29       0
08-30       0
08-31       2
```

**The three rows written after 12:37 UTC came from a different path.** All three
(`notes/ai-stack-development/testing-14` at 08-28 16:01 UTC, and both 08-31 rows)
are `page_class=note`, written by the **workbench** — `OB1/docker/workbench/src/repositories/notes.ts:69`,
which is Deno and parses with its own deliberate mirror `src/util/notes-parse.ts`.
It never imports `wiki-pages.mjs`, so it was never affected. Nothing wrote a
compiler-generated page for three days.

Deletes were **not** affected: `wiki-service.mjs` imports `deleteWikiPages`
directly and that path has no dependency on `parseWikiPage`. For three days the
table could only shrink.

---

## 2. The fix

`OB1 9b47135`, two files, both bind-mounted (see §5).

**Binding.** Import the name, then re-export it as a separate statement, with a
comment saying why it must not be collapsed back.

**The swallow — the defect that actually cost three days.** The bare `catch {}`
in `queueWikiPage` is removed.

The reasoning, because this is the part worth disagreeing with: that catch was
protecting nothing. `vaultRel` and `parseWikiPage` are pure string work — no
network, no disk, no untrusted input. The only exception they can raise is a bug
in the module, and a bug in the module fails identically for every page. The
best-effort contract is real, but it belongs to the I/O helpers
(`upsertWikiPages` / `deleteWikiPages`), where a failure is transient and costs
one stale index entry until the next write. A broken parser is not transient.
The module header now states that boundary explicitly.

**Why this is loud rather than logged.** Every call site already sits inside a
per-item catch that names the failure *and moves a counter*:

- `generate-wiki.mjs:1549` -> `[wiki] FAILED #<id>: <message>` per entity, the
  summary flips from `26 regenerated, 0 failed` to `0 regenerated, 26 failed`,
  and the ids are written to `.failed-entity-ids.json`, which `wiki-service`
  unions into the next compile's dirty set.
- `generate-wiki.mjs:1562` -> `[wiki] leaf-page emission failed: <message>`.
- `synthesize-notebooks.mjs:434` -> `[notebook-synth] FAILED notebook "<name>"`.

`wiki-service` already echoes that summary in the `compile ok (...)` line the
operator reads. So the signal is a **state change in the compile result and the
retry ledger**, not a new log channel — and no run is lost, which a hard process
crash would have cost. (Read-verified at those line numbers; the executed proof
covers the module layer.)

**Tests.** No existing assertion was changed. Two named regression tests were
added — one pinning the binding, one asserting `queueWikiPage` propagates a
programmer error, which fails if anyone re-adds the catch. Suite went **7 of 12
RED -> 12 of 12 GREEN**; the other six recipe test files were green before and
after.

---

## 3. The data gap

Exact reconciliation of disk mtime against row `updated_at`, read-only, using
`backfill-wiki-pages.mjs`'s own walk rules:

```
files on disk (backfill-eligible) = 59,213     rows = 47,905
MISSING (no row at all)           = 11,400   {entity 9,422, thought 1,487, source 457, note 34}
STALE   (file newer than its row) =    804   {entity 727, note 55, source 21, root 1}
FRESH   (row current)             = 47,009
ORPHAN rows (no file on disk)     =     92
```

So **12,204 rows need repair**, not 11,308: 11,400 pages have no row at all,
plus 804 whose row is stale, plus 92 orphan rows pointing at deleted pages.

The vault on disk is intact and is the source of truth. **No content was lost.**
What is lost is the derived index: search, nav and the graph have been serving a
table missing ~19% of the vault, weighted toward the newest pages — which are
exactly the ones a user is most likely to look for.

### What a backfill would cost

- **Walk:** 47s measured over all 59,213 pages (fixed module, network disabled).
- **Payload:** 166 MiB of markdown, 297 POSTs at ~0.56 MiB each.
- **Postgres:** `wiki_pages` is 334 MB (72 MB heap, 64 MB indexes, rest TOAST).
  `search_tsv` is `GENERATED ALWAYS`, so every upserted row re-runs two
  `to_tsvector` calls over its body, and two GIN indexes (`search_tsv`,
  `links`) plus two btrees are updated per row. A full run rewrites all 59,213
  rows — including 47,009 that are already correct.
- **Disk:** not a constraint. 803 GB free on the Postgres volume. The cost is
  time, GIN churn (wiki search will be slow during the run), and a VACUUM after.

### What a backfill would risk — the non-obvious one

**A full backfill would degrade the 210 workbench-written note rows.**
`parseWikiPage` returns link targets **as written**; the workbench resolves them
to full slugs before storing (`notes.ts:54-66`). Proven on a real row:

```
notes/research-demo/intermediary.md  on disk : [[tool-postgresql|PostgreSQL]]
stored row (workbench, resolved)             : content/tool/tool-postgresql
what a backfill would write (raw)            : tool-postgresql
```

`tool-postgresql` matches no slug, so the graph endpoint drops the link — which
is precisely the regression `notes.ts` was written on 2026-08-28 to fix (its
comment cites the operator's "3 nodes local, 2 fullscreen" report). Any backfill
must exclude `page_class=note`, or be followed by a workbench re-sync of notes.

Second: **do not pass `--prune` casually.** `backfill-wiki-pages.mjs:38` swallows
a `readdir` failure and returns, so an unreadable subdirectory silently yields
zero pages for that whole subtree. The only guard is `seen < 100`, which catches
a near-total walk failure but not one missing directory — and `--prune` deletes
every row whose slug the walk did not see.

Third: a full backfill **rewrites `updated_at` on all 47,905 existing rows**,
destroying the write-history signal this whole diagnosis was built on. Capture
the histogram (or a table backup) first.

### Recommendation

1. **Deploy the fix first and let one compile run.** It is the cheap half, it
   stops the bleeding, and every page written from then on is correct.
2. **Then run a SCOPED repair, not a full backfill** — the 12,115 missing/stale
   rows excluding `page_class=note` (~61 POSTs, ~35 MiB, roughly a fifth of the
   full run and none of the note-clobbering risk). `backfill-wiki-pages.mjs` has
   no such filter today; adding a `--missing-only` / `--since` mode is a small,
   testable change and is the recommended path.
3. **Only if a scoped mode is not wanted:** run the full backfill *without*
   `--prune`, after snapshotting `wiki_pages`, and re-sync notes through the
   workbench afterwards. Accept the 47,009 redundant rewrites and VACUUM after.
4. **Handle the 92 orphan rows separately** — they are the sweeps' job, and
   `--prune` is the wrong instrument given the `readdir` swallow above.

None of this was run. The live database was not written to; every measurement
above came from read-only queries or throwaway containers.

---

## 4. Why nobody noticed — two checks that were present and did not fire

**a) The test suite was never wired to anything.**
`recipes/_shared/wiki-pages.test.mjs` caught this the day it shipped and went
5-of-10 RED. There are **7 recipe test files and no runner for any of them**.
The two Dockerfiles with build gates (`wiki-service/Dockerfile:14`,
`wiki-viewer/Dockerfile:278`) run their own `lib/*.test.mjs`;
`workbench/Dockerfile:26` explicitly documents that it *cannot* run tests
importing `@shared/*` because `/recipes` is a runtime mount. This is structural:
**a bind-mounted module has no build-time gate, so its gate has to be at load or
commit time.** Wiring these into a pre-commit check is the durable fix —
flagged, not done, because `scripts/checks/` is another agent's territory this
session.

**b) The DRIFT check was 4.5 points under its own threshold.**
`wiki-service.mjs:1036` alarms when entity rows differ from entity files on disk
by more than 25%. Actual: 36,132 rows vs 45,462 files = **20.52%**. It has been
silently just-passing the entire outage, and would have kept quiet for roughly
another 2,600 lost pages. Worth revisiting the threshold — but note
`wiki-service.mjs` is **baked** into the image, so that is a rebuild.

---

## 5. Deploy shape

| File | How it reaches the container | Deploy |
|---|---|---|
| `OB1/recipes/_shared/wiki-pages.mjs` (this fix) | **bind mount**, `../recipes:/recipes:ro` into `openbrain-wiki` (compose:711) and `openbrain-workbench` (compose:829) | **No image rebuild.** Submodule move on the main checkout + gitlink bump. The recipes are short-lived CLI processes spawned per compile, so the next compile picks it up with **no container restart**. |
| `OB1/docker/wiki-service/wiki-service.mjs` | `COPY` into `openbrain-wiki:local` (`wiki-service/Dockerfile:10`) | Image rebuild. **Not touched by this change.** |

This is also how the regression shipped: because `/recipes` is a bind mount, the
bug went live the moment the host working tree moved to `dfc6228` — no rebuild,
no restart, no gate anywhere in the path.

`9b47135` is **not pushed**. Push it to the OB1 remote before bumping the parent
gitlink, or a fresh `--recurse-submodules` clone breaks.

---

## 6. Loose ends found in passing (not fixed, not mine)

- `content/notebooks/gemini-model-evolution` has stored link targets beginning
  with `[` (e.g. `"[content/source/0640240d-..."`). Triple brackets in a
  generated page; `extractLinks`' `\[\[([^\]]+)\]\]` captures the extra one.
  Pre-existing, cosmetic, affects graph edges for those targets.
- 92 orphan rows (row present, file gone) — see §3.
- `backfill-wiki-pages.mjs:38` `readdir` swallow — see §3.
