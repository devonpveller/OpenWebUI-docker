> **SUPERSEDED - FIXED IN PRODUCTION 2026-09-02.** The defect this note records was
> re-fixed on the then-current pin and landed: OB1 `09f70f4` (`fix/wiki-pages-extractlinks`,
> on the remote) via ai-stack merge `443c5d8`; mirror backfilled (notes/ excluded), live
> container 10/10. Aftermath + lessons: `wiki-pages-mirror-regression-findings.md`.
> The body below is the original record, unchanged.

# `wiki_pages` has not been written by any compiler since 2026-08-28

**Found**: 2026-08-31, by u8h3, while trying to close a *drill* vacuity (`VACUOUS-WIKIPAGES`).
**Owner**: the wiki work line, not the agent-memory-plane / DFU C.9 line. **Not fixed here** —
recorded per CLAUDE.md's "findings go to `documentation/notes/`, not into the deliverable".
**Nothing was written to the live database while establishing any of this.**

## The defect, in one line

`OB1/recipes/_shared/wiki-pages.mjs` **re-exports** `extractLinks` without **importing** it, so
the name is not bound in the module's own scope and `parseWikiPage()` throws
`ReferenceError: extractLinks is not defined` on every call.

```js
// wiki-pages.mjs:75  — a re-export binds the name for IMPORTERS, not for this module
export { extractLinks } from "./links.mjs";
...
// wiki-pages.mjs:88  — inside parseWikiPage(), this identifier is undefined
    links: extractLinks(body),
```

## Why nobody saw it

Two layers of silence, one on top of the other.

1. **`queueWikiPage()` swallows it.** Its body is wrapped in `try { … } catch { /* never let
   bookkeeping break a compile */ }`. That comment is honest about its intent and exactly wrong
   about its effect: it converts a total failure of the feature into a no-op that logs nothing.
   The compile succeeds, the pages are written to disk, `flushWikiPages()` finds an empty queue
   and sends nothing, and `[wiki-pages] sync degraded` — the module's own warning — never fires,
   because the failure happens *before* any I/O.
2. **The re-export makes the module's public surface look correct.** `import { extractLinks }
   from "./wiki-pages.mjs"` works fine. So the test file's import resolves, the test that
   exercises `extractLinks` directly passes, and only the *internal* call is broken.

## The measurement

On the gitlink under test (`debbbaa`, which contains `dfc6228`):

```
$ node --test OB1/recipes/_shared/wiki-pages.test.mjs
ok 1 - classifySlug maps every page class the compiler emits
ok 2 - classifySlug normalises separators and leading slashes
ok 3 - extractLinks takes wikilink targets, dedupes, strips alias + anchor
not ok 4 - parseWikiPage reads frontmatter, strips it from the body, parses tags
      error: 'extractLinks is not defined'
not ok 5 - parseWikiPage tolerates missing/!malformed frontmatter without throwing
      error: 'extractLinks is not defined'
not ok 6 - parseWikiPage keeps CRLF files parseable (Windows bind mounts)
      error: 'extractLinks is not defined'
not ok 7 - frontmatter entity_type overrides the directory-derived one
      error: 'extractLinks is not defined'
ok 8 - vaultRel strips the vault root and normalises separators
not ok 9 - queueWikiPage dedupes by slug (last write wins) and never throws
ok 10 - queueWikiPage ignores pages outside the vault root
# pass 5  # fail 5
```

**The test suite already catches this. It was simply not being run.** Test 3 passes because it
imports `extractLinks` itself; test 9 fails because the queue stays empty while the function
keeps its promise never to throw. Test 10 passes *vacuously* — "ignores pages outside the vault
root" is satisfied by a function that ignores everything.

And directly, with no test harness:

```
$ WIKI_GIT_DIR=/out node -e '... vaultRel + queueWikiPage ...'
vaultRel(/out/content/concept/x.md) = content/concept/x.md     <- path handling is FINE
queued = 0                                                     <- and nothing is queued
```

## Blast radius

`parseWikiPage` is the only way a row reaches the table, and every writer goes through it:

| caller | effect |
|---|---|
| `recipes/entity-wiki/generate-wiki.mjs:1154, :1193` (`openbrain-wiki`, scheduled) | queues nothing, **silently** — inside `queueWikiPage`'s catch |
| `recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs:424` | same |
| `recipes/_shared/source-leaf.mjs:176` | same |
| `recipes/_shared/backfill-wiki-pages.mjs:80` | calls `parseWikiPage` **directly, uncaught** — so the repair path throws **loudly** |

So since `dfc6228` (2026-08-28 08:37 -0400): **`wiki_pages` receives no new or updated rows from
any compile, and cannot be rebuilt by the backfill either.** The table is not empty — rows
written before that commit persist — so the symptom is a **stale** viewer index (search / nav /
graph read this table, per the wiki-dynamic-index P1 work), not an obviously broken one. That is
the worst shape: a page renamed, retitled, relinked or newly created after 08-28 is missing or
wrong in search/nav/graph while every page still loads.

**Not verified here** (would need the live database, which this session was scoped away from):
how stale the live `wiki_pages` actually is. The right check is a read-only count of
`wiki_pages` rows with `updated_at > '2026-08-28'` against the number of vault pages changed
since. Do that under an `open-brain` lease before deciding urgency.

## The fix

One line — add the import beside the re-export:

```js
import { extractLinks } from "./links.mjs";
export { extractLinks };
```

Then `node --test recipes/_shared/wiki-pages.test.mjs` must go 10/10, and a compile into a real
vault root must produce a non-zero `queuedWikiPageCount()`. **Prove it red first**: the test
above is already the red, which is the cheapest possible starting position.

Worth doing at the same time, because the swallow is what made a total feature failure invisible:
`queueWikiPage`'s `catch {}` should keep the compile alive **and** warn once, the way
`upsertWikiPages` already does with `warnOnce`. "Never break a compile" and "never say anything"
are different requirements, and only the first one was wanted.

## Why this note exists rather than a fix

u8h3 was closing a vacuity in `drill-personal-plane-exclusion.ps1`: ATTACK 14's `wiki_pages`
assertion quantified over an empty set. Half the cause was the drill's own (it compiled into
`/out`, outside `WIKI_GIT_DIR`, so `vaultRel` correctly dropped every path — fixed by setting
`WIKI_GIT_DIR=/out`). The other half is this bug, which is a live wiki-index defect in another
work line's code. Folding an OB1 wiki fix into an agent-memory-plane branch would put a change
nobody asked for into a merge nobody would review for it.

The drill now **names this bug at the point of failure**, with the one command that decides
whether it is still biting, so the next person does not spend a drill run rediscovering it. The
`VACUOUS-WIKIPAGES` pin stays in the gap ledger with its cost stated: *a green run does not cover
the `wiki_pages` surface at all.* When this is fixed, that gap closes by itself and the drill
reports it CLOSED rather than failing the build.
