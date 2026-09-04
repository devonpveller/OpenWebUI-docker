# This morning's research -> wiki: status check 2026-08-30 (+ three findings)

Checked 2026-08-30 ~20:15Z (16:15 EDT), in response to "have all the new
research from this morning completed?"

## Verdict: complete, end to end.

| stage | state |
|---|---|
| `research_jobs` | 2 jobs (origin `notebook`), queued 05:00:43Z, both `done` by 05:08:30Z. Queue now **0 queued / 0 running**. |
| output | 11 thoughts + 2 sources created 05:00-05:16Z |
| `entity_extraction_queue` | **0 pending / 0 processing** (12,854 complete; 139 failed, newest failure 2026-06-13) |
| `source_extraction_queue` | **0 pending / 0 processing** (6,381 complete; 2,476 failed, newest failure 2026-08-15) |
| wiki drain + compile | 05:11Z `on-demand` (18 regenerated, 14 thought + 2 source leaf pages) and 05:34Z `change` (48 regenerated, 5 source leaf pages). Both notebook hubs resynthesized. |
| since | 5 further change-driven compiles through 14:52Z, all `0 failed`. Idle since — change-watch only logs when it sees new items. |

Verified RETRIEVABLE, not merely present — fetched through the viewer:

- `/content/notebooks/ai-newsletters-and-weekly-updates/…` -> 58,292 B, "Key
  Findings" present, live internal link to today's source `cce178a2`.
- `/content/source/cce178a2-…` -> 52,498 B.
- Hub frontmatter `generated_at: 2026-08-30T05:33:45Z`, `source_count: 20`,
  today's source cited as S20.

(URL note for future checks: pages serve under **`/content/...`**. The bare
`/notebooks/<slug>` form returns the `data-not-available="1"` miss page — that
is the viewer working as designed, not a missing page.)

Restart context: the OB1 fleet restarted 07:23Z and `openbrain-db` again ~13:14Z.
`openbrain-research` logged `orphan recovery failed` / `claimNext failed` at
07:23:55 (DB not yet accepting connections) and nothing since. No work was lost —
both jobs had finished at 05:08Z and the queue is empty — but the boot-race
handling is worth a look: research has logged nothing at all for 13h, so a dead
poller and an empty queue are indistinguishable from outside.

The `3 queued page(s)` counter is unchanged and still cosmetic — see
[wiki-drain-2026-08-29-status-and-findings.md](wiki-drain-2026-08-29-status-and-findings.md).

## Finding 1 — a login wall was admitted as a source, with a page and an entity

One of this morning's two sources:

    id      d9c6452c-f17e-402a-aaaf-a12941b3bb12
    title   "Microsoft OneDrive"
    url     https://onedrive.live.com/login/en-us/
    content 18 bytes ("Microsoft OneDrive")

It got `content/source/d9c6452c-….md` (295 B, body = the title) and minted
entity 31179 `Microsoft OneDrive` (type `tool`).

Vault-wide: **658 non-retracted sources with < 400 bytes of content** — login
pages, empty YouTube watch pages, `signup.live.com`, `myaccount.microsoft.com`.
This is the shelved admission gap (`ok` = HTTP 200 + non-empty), now with a
number on it.

## Finding 2 — a `#` in a wikilink alias leaks raw `[[...]]` into the rendered page

In the served HTML of the hub, 6 entries of the `## Sources` list render as
literal `[[content/source/<uuid>|Daily #084 — …]]` instead of links.

Exactly reproduced by the alias content: the Sources list has **14 entries, 6 of
which contain `#` in the alias — and those 6 are precisely the 6 that leak**. All
are the digest-derived `Daily #NNN — …` titles, including today's source.

Separately, citation groups render with a stray leading `[` (`[<a …>S1</a>`),
from the `[[[target|S1]], [[target|S4]]]` bracket-wrapped-list pattern the
synthesizer emits.

Both are cosmetic — the pages are complete and the links elsewhere work — but
they land on the most-read page of every research run.

## Finding 3 — agent-memory writeback is landing in the production knowledge graph

Thought 13358 (`metadata.source = "agent-memory"`, created 14:46:16Z) was
extracted at the 14:50Z compile and minted entity 139976 **`wt-tester-3`, type
`person`** — a worktree harness identity, now an entity with a wiki page
(`content/person/person-wt-tester-3.md`, visible in the viewer's build warnings).

Today is the first day this source appears: **4 agent-memory thoughts -> 11
entities** (6 `tool`, 2 `project`, 2 `topic`, 1 `person`).

Small so far, and the thoughts themselves are real engineering notes. The problem
is that harness scaffolding (worktree ids, tester identities) is being read by the
entity extractor as people and tools in the same graph as researched knowledge.
Worth deciding deliberately — scope the agent-memory plane out of entity
extraction, or teach the extractor to skip harness identifiers — before the
volume grows.

---

# Follow-up investigation (same day, operator questions)

## (1) Thin sources: a tiered removal plan, sized

`sources` already has a two-phase retraction lever (`init-source-retract.sql`):
`retracted_at` stages, the compile tick sets `retraction_committed_at`, restore
clears `retracted_at`. Consumers filter on `retraction_committed_at IS NULL`.
**It has never been used: 0 staged, 0 committed of 8,857 sources.** That is the
right instrument here — not DELETE.

Of the 658 sub-400-byte non-retracted sources, 20 are `research_synthesis` and 2
`manual`; excluding those leaves **636 thin `web_article` rows**, which split:

| tier | n | what | call |
|---|---|---|---|
| 1 | **48** | auth / account / nav walls — `app.notion.com/login`, `secure.chase.com/web/auth/logonbox`, `arxiv.org/login`, `mail.qq.com/cgi-bin/loginpage`, `onedrive.live.com/login` (this morning's) | **retract** |
| 2 | **18** | dead redirect stubs — `Redirecting to: /frozen-issues/…` | **retract** |
| 3 | **570** | thin captures of REAL documents — 126 are PDFs/YouTube where extraction failed (`web.stanford.edu/~wfsharpe/RISMAT/RIAbook.pdf`, smartasset articles, tutorial videos) | **KEEP** — these are extraction failures, not junk; deleting them destroys the URL and the research intent. They want a re-fetch queue. |

Blast radius of tiers 1+2 (66 sources): **30 entities** lose all evidence and get
swept by the orphan sweep. (For the whole 636 it would be 488 — another reason not
to sweep tier 3.)

## (2) The `#` wikilink break: not a regression, an incomplete fix in two places

**Proven from Quartz's own regex**, read out of the running viewer
(`/quartz/quartz/plugins/transformers/ofm.ts`):

    !?\[\[([^\[\]\|\#\]+)?(#+[^\[\]\|\#\]+)?(\?\|[^\[\]\#]*)?\]\]

The alias group's class `[^\[\]\#]*` **excludes `#`**. A `#` anywhere in the alias
makes the whole wikilink fail to match, so it survives as literal text. Em dashes
are irrelevant — they co-varied in the sample and are not the cause.

The prior fix HELD. `linkSafeLabel` (c170307, 2026-08-23) still strips exactly
what it was written to strip:

    .replace(/[\[\]|]/g, " ")

It was incomplete from the day it landed, in two independent ways:

1. **Character class too narrow.** Its comment reads "a title containing `| [ ]`
   would break the wikilink" — drawn from Quartz's *target* class. The *alias*
   class also excludes `#`.
2. **Applied at one emitter, not the other.** `linkSafeLabel` is called only in
   `recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs` (4 sites).
   `recipes/entity-wiki/generate-wiki.mjs:887` builds
   `Grounded by [[content/source/${s.id}|${title || s.id}]]` from the **raw
   title**, no sanitiser at all — and entity pages are where most of the damage is.

Nothing new appeared upstream: `Daily #NNN — …` titles date to June 2026 (295 that
month) and the digest mints one every day, so this has been producing breakage
continuously and quietly.

**Vault-wide scale** (Quartz's regex applied to every `[[…]]` in 58,754 files;
the scan self-tests the regex first, because an earlier run of this measurement
lost a backslash in transit and reported a false 633k):

- **A. Renders as literal `[[…]]` text: 2,264 links in 1,291 files.**
  Causes: `#` in alias **2,083**, `[`/`]` in alias 149 (e.g. the entity
  `Collider[]`), other 32.
  By directory: tool 808, project 478, person 365, notebooks 248, organization 154.
- **B. Link works but a stray `[` leaks: 7,113 links in 1,174 files** — the
  synthesizer's `[[[target|S1]], [[target|S4]]]` bracket-wrapped citation groups.
  Separate defect, cosmetic only.

Fix shape: extend `linkSafeLabel`'s class to `#` and route
`generate-wiki.mjs:887` through it. Existing pages only heal on regeneration.

## (3) agent-memory retrieval: plane-bound, not role-bound — and blind to intent

Read from `OB1/integrations/kubernetes-deployment/agent-memory{,-policy}.ts` and
the live table.

**How recall actually works.** `performRecall` embeds the query and orders by
`1 - (t.embedding <=> $1::vector)` over the *linked thought's* embedding, blended
with recency. The embedding is computed on **`row.content` only** (`agent-memory.ts:252`)
— so `content` is the entire retrieval surface; `summary` is not searchable.

**What the gate discriminates on** (`isRowRecallableBy` / `buildRecallScopeFilter`):
`workspace_id`, optional `project_id`, `visibility`, `exposure`, `lifecycle_status`,
`review_status`. **Nothing else.**

**So: retrieval is NOT role-bound.** `created_by`, `runtime_name`, `task_id`,
`flow_id` are recorded as provenance and are never consulted by recall. Any caller
on the same workspace/project plane gets the same results. `memory_type` is
returned but is not even filterable — `RecallScope` has no field for it.

**Current outcome, measured against the live 4 rows** (gate mirrored in SQL):

    default gate (confirmed, evidence_only) -> 0 returned
    with include_unconfirmed (+pending)     -> 4 returned
    all 4 have a linked embedded thought    -> findable once admitted

All four are `review_status: pending` — by design (§1 locks the write default to
`pending`, deliberately outside the default gate), but the practical state today is
a plane that holds four memories and returns none of them by default.

**The axes that exist vs the ones the operator wants:**

| axis | state |
|---|---|
| project | **exists and populated** (`project_id = ai-stack`; one `boundary-probe` test row) |
| channel | exists (`channel_kind`/`channel_id`/`channel_thread_id`), unused so far |
| topic | **absent** — no topic or tag column; the only topical reach is the embedding |
| intent | **absent as a field.** `memory_type` is the nearest thing and is a *form* taxonomy, not an intent one: `decision, output, lesson, constraint, open_question, failure, artifact_reference, work_log, check` |

This matches the operator's read. A "challenge overcome" lands today as
`memory_type: check` or `lesson` with free-text content, and an intent-shaped query
("how have we handled a check that passes while checking nothing") can only reach it
through embedding similarity on `content`.

The cheap lever, given the architecture already in place: **the abstract problem
statement has to be inside `content`**, because `content` is what gets embedded.
A writeback that records the solution without stating the problem in general terms
is unreachable by intent-shaped recall, no matter how good the solution is. That is
a prompt/contract change at the writeback door, not a schema migration — and it can
be checked mechanically the same way the plane-agreement invariant is.

A `topic`/`intent` column (or metadata key) would additionally allow cross-project
recall, which `project_id` scoping currently prevents by design.

## Correction to the earlier entry

The `person-wt-tester-3` leak is a **content/extraction** issue, not a retrieval
one: recall never exposed it by role. What happened is that the entity extractor
read the worktree id out of the memory's `content` and minted it as a `person`.

---

# Actions taken 2026-08-30 (operator-approved)

## Tier 1+2 retraction — DONE, and the tier boundary moved first

Reviewing the 66 candidates line by line before acting caught **21 false
positives** the URL pattern had swept in — real documents, not auth walls:

- 4 × `docs.pytorch.org` + 1 × `docs.vllm.ai` + 1 × `grantcurell.github.io`
  ("Redirecting…" JS shims on real documentation)
- 12 × `news.smol.ai/issues/…` ("Redirecting to: /frozen-issues/…" — the issue
  moved, the content still exists)
- 1 × `itu.int/rec/dologin_pub…X.1285` (matched on "dologin"; it is an ITU-T
  Recommendation)
- 1 × `servicenow.com/docs/…/authentication` (matched on `/auth`; a docs page)
- 1 × `myaccount.google.com/intro/find-your-phone` (a help page)

Those moved to the re-extraction set. **45 retracted**, all verified as pages
whose entire content is a login/account gate: `app.notion.com/login`,
`secure.chase.com/web/auth/logonbox`, `arxiv.org/login`, `mail.qq.com/…/loginpage`,
`dashboard.stripe.com`, `signup.live.com`, `onedrive.live.com/login` (this
morning's), etc. None was attached to a notebook.

**A gap found while executing it: nothing commits a staged retraction.**
`init-source-retract.sql` documents "the compile tick sets
`retraction_committed_at`", and every read path filters on it — but the only
writers are `retractStaged` and `restore` in
`docker/workbench/src/repositories/sources.ts`, and both set it to `NULL`.
`commitPending()` commits *content revisions*, not retractions. A staged-only
retraction therefore stays fully visible to the wiki, semantic search and
notebooks, forever. The retraction here set `retracted_at`, `retracted_by` and
`retraction_committed_at` in one transaction to reach the documented end state.

Verified end to end: the wiki's own `source_entities … retraction_committed_at
=is.null` query returns **0** rows for the retracted set, and the compile
triggered afterwards logged `orphan-sweep: deleted 18 stale entity page(s)` +
`11 stale leaf page(s)` — exactly the 18 entities predicted to lose all evidence
(`Okta`, `Stripe Dashboard`, `Fidelity NetBenefits`, `www.every.education`, …).

Reversal, if ever wanted, is one statement — the rows and their content are
retained:

    UPDATE sources SET retracted_at=NULL, retracted_by=NULL, retraction_committed_at=NULL
     WHERE retracted_by='operator:thin-source-cleanup-2026-08-30';

## Tier 3 "make the sources healthy" — measured, and it is not a re-fetch job

591 thin sources remain (570 tier 3 + the 21 rescued). All have a URL. All come
from the research service (`research-stage` 384, `deep-research-source` 207) —
**not** from `ingest_url`, so the fix belongs in `fetchPage`
(`integrations/research-service/index.ts:295`), not in the MCP ingest path.

By root cause: 51 PDF URLs, 75 video pages, 18 JS/redirect shims, 14 docs sites,
433 other thin HTML.

`fetchPage`'s admission bar is `if (!content) return error` — non-empty is the
only test, which is exactly why a login wall's title text was stored as a source.

**The measurement that matters.** 15 of the 433 "other thin HTML" were re-fetched
live through the same Mullvad proxy the service uses, comparing stored length to
freshly extractable text:

| outcome | n | examples |
|---|---|---|
| healed by a plain re-fetch | **2** | `knoxvilletn.gov/…/crash_data` 147 → **42,904** chars; `oakridgetn.gov/Calendar.aspx` 164 → 4,700 |
| bot-walled (401/403) | 3 | medium.com, tripadvisor.com, wordplays.com |
| network error | 1 | greensurfaceresource.com |
| no better — JS-rendered SPA | **9** | reddit.com → 6 chars, apps.microsoft.com → 95, notion.so → 128, computer.org → 28, lowes.com → 292 |

**So a re-fetch pass heals roughly 1 in 8.** The dominant blocker is client-side
rendering, not extraction. Anyone planning this as "re-run the fetcher" will
recover ~13% and conclude the work failed.

Staged plan, cheapest first:

1. **Re-fetch pass with a real content floor.** Safe by construction: research
   staging goes through `find_or_create_source` (dedup on URL) and then UPDATEs
   content/embedding, so a re-fetch updates the existing row rather than
   duplicating it. Heals the ~13%, and records a diagnostic reason on the rest so
   the next stage has a work list instead of a guess.
2. **PDF text extraction** — 51 URLs, bounded and well understood. Note
   `fetchPage` currently rejects any non-HTML content type outright, and
   `ingest_url` stores a literal `[PDF source not text-extracted: …]` stub.
3. **Headless render** for the JS SPA bulk. This is the expensive one and it is
   where most of the 433 live. Worth deciding whether the vault wants Reddit and
   vendor product pages at all before paying for it.
4. **Transcripts** for the 75 video URLs — a different capability again; the page
   genuinely has no article text.
5. **An admission floor at fetch time** so this stops accumulating: a content
   length/quality bar, plus a rejection reason recorded rather than a source
   stored. This is the one that prevents the next 658.


# Fix landed 2026-09-04 — anchor `wikilink` (dev-wikilink-sub2)

OB1 `fix/wikilink-hash-alias` **590d804** (pushed, `ls-remote` verified);
parent gitlink bump **67d1ebf** on `work/wikilink2`. RED proven before GREEN;
full OB1 recipe suite 8 files / 48 tests / 48 pass.

## Correction to section (2): `generate-wiki.mjs` did NOT call "no sanitiser at all"

Section (2) above, and the anchor drafted from it, both say the `Grounded by`
emitter "builds ... from the **raw title**, no sanitiser at all". **That is not
what the code did.** `buildEvolutionSection` (generate-wiki.mjs:879-887 at pin
5224928) applied an **inline copy of `linkSafeLabel`'s body**:

    .replace(/[\[\]|]/g, " ")

Same character class, same missing `#`, wrapped in `clip(..., 120)`.

The distinction matters and is not pedantry: if the emitter had truly called
nothing, fixing the shared function would have left it broken in a *different*
way (brackets and pipes too, not just `#`). Because it was a **copy**, fixing
`linkSafeLabel` alone would have left this emitter — the one responsible for
most of the damage — completely unchanged. Either mis-reading leads to a fix
that does not land. The change therefore does both: extends the class **and**
replaces the copy with a call.

After this change **no inline copy of the sanitiser remains under `recipes/`**
(`grep -rn 'replace(/\[' recipes/ --include='*.mjs'`): citations.mjs:46 is the
single source of truth.

`synthesize-notebooks.mjs` already routed through `linkSafeLabel`, so it was
fixed for free by the shared change.

> **Corrected 2026-09-04 (test).** An earlier revision of this paragraph said
> "5 sites, not the 4 recorded above". The original 4 was RIGHT: the call sites
> are lines 337, 396, 412 and 431; line 408 is a COMMENT mentioning the name,
> and a bare `grep -n linkSafeLabel` counts it. That wrong figure also reached
> OB1 commit `590d804`'s message, which cannot be edited now that it is pushed —
> this note is the correction of record. Counting call sites by grepping an
> identifier counts its comments and its import too; check what each hit IS.

## Correction to section (2): the regex quoted above is itself mis-transcribed

Section (2) warns that "an earlier run of this measurement lost a backslash in
transit" — and then loses more itself. (This correction first said "two more";
test counted **three** — the alias group's `\\?` lost one as well. The lesson
survives the miscount, and the miscount is the lesson: this is the third time
in one file that a regex changed shape in transit.) The regex printed there is:

    ([^\[\]\|\#\]+)?   <- as printed above (WRONG)

Read out of the built image just now
(`docker run --rm --entrypoint sh openbrain-wiki-viewer:local -c "sed -n '120p'
/quartz/quartz/plugins/transformers/ofm.ts"`), quartz v4.5.1:

    /!?\[\[([^\[\]\|\#\\]+)?(#+[^\[\]\|\#\\]+)?(\\?\|[^\[\]\#]*)?\]\]/g

The target classes end `\#\\]` (escaped-hash **then escaped
backslash**), not `\#\]`. The *conclusion* drawn in section (2)
is unaffected — the alias group is `(\\?\|[^\[\]\#]*)?` and does exclude `#` —
but nobody should re-derive anything from the string as printed there. The
committed code comment cites the verified form.

## RISK for anyone testing the served-page criterion: TWO renderers, only ONE is Quartz

The anchor's third acceptance item asks for the fix proven on a page fetched
from the viewer under `/content/`. **Fetching a page and finding no `[[` does
not by itself prove anything**, because the viewer has a second, independent
render path with its **own** wikilink regex —
`OB1/docker/wiki-viewer/lib/render-page.mjs:37` (`rewriteWikilinks`):

    /\[\[([^\]|#]+)(#[^\]|]*)?(?:\|([^\]]*))?\]\]/g

Its alias group is `[^\]]*` — which **permits `#`**. So a
DB-rendered page can render `Daily #NNN` correctly **even against completely
unfixed code**. A tester who does not pin the render path can pass this
acceptance on a build that contains none of this change.

Any served-page evidence must therefore state which renderer produced it.

> **CORRECTED 2026-09-04 (test) — the original advice here was INVERTED and
> would have manufactured a false green.** It said the header is
> `db|fresh|static` and that a `fresh` render is the Quartz one. Both halves are
> wrong, verified in `OB1/docker/wiki-viewer/serve.mjs`: the **db** path
> (lines 372/376) and the **fresh** path (413/418) BOTH call the same
> `renderMarkdown` imported at line 17 from `render-page.mjs` — the permissive
> regex above. The source comment on the fresh path even says it renders "with
> the SAME renderer as the DB path". So `fresh` proves nothing either. The only
> values the server ever emits are `db`, `fresh` and `not-available`; there is
> no `static`.
>
> **The ONLY Quartz-rendered path is the STATIC BUILD, and it emits NO
> `x-wiki-render` header at all.** So the correct instruction is the opposite of
> what was written: the served-page criterion is satisfied only by a response
> that carries **no** `x-wiki-render` header (after a real Quartz compile), and a
> response carrying `db` OR `fresh` must be DISCARDED as evidence.

This is a genuine divergence between the two renderers and is **out of scope
here** (the anchor is the emitters, not the viewer): the DB path is more
permissive than Quartz, so the two can disagree about whether a given page is
broken. Worth its own look — a shared contract, or at least a test asserting
the two accept the same alias set.

## Scope notes

- The 149 `[`/`]`-in-alias breaks (`Collider[]`): explicitly out of scope, and
  **partly healed anyway** as a side effect — the `Grounded by` emitter now
  strips brackets via the shared function rather than its copy. Not claimed as
  fixed; other emitters were not swept.
- The 7,113 stray-leading-bracket occurrences: untouched, as scoped.
- No backfill. Existing pages heal only on regeneration, as the anchor states.

## Post-merge addenda — review of `wikilink` (reviewer-wikilink-sub1, 2026-09-04)

Two findings from the review of merge `80f4bec`. Neither changes the verdict on
the fix (it landed FITS); both are true, both were out of scope for that anchor,
and one of them was reported at test time but never actually reached this file.

### 1. Stripping `#` is LOSSY, and `#` is not rare in legitimate titles

`linkSafeLabel` now replaces `#` with a space. That is correct — but it is worth
recording what it costs, because the anchor was written around the `Daily #NNN`
digest class and that turns out to be the MINORITY of what is affected.

Measured against the live vault (`docker exec openbrain-wiki-viewer`, `/wiki`,
2026-09-04), counting only the `Grounded by` emitter this change touches:

    grep -rhoE "Grounded by \[\[content/source/[^]|]*\|[^]]*\]\]" --include="*.md" . \
      | sed "s/.*|//;s/\]\]$//" | grep -c "#"

- **1,111** `Grounded by` links carry a `#` in the alias, across **987 files**.
- Of those, only **354** are `Daily #NNN`. The other **757** are ordinary source
  titles: GitHub issue and discussion numbers (`Issue #4194`,
  `Discussion #1504`), `C# game engine` and `Platform-specific C# code`,
  `2025 MIPS Measure #238`, `The World's #1 Employee Management App`, even a CSS
  hex colour that leaked into a title (`background:#F00`).

On regeneration every one of those labels loses a character. Most degrade
harmlessly (`Issue #4194` -> `Issue 4194`). The worst case is real, though:
**`C#` -> `C`**, which names a different language.

**This is still a strict improvement and the change was right to land.** Today
those 1,111 links do not render at all — they survive as literal `[[...]]`
markup, which is worse than a label missing one character. And there is no
better option *within this fix shape*: Quartz's wikilink alias character class
excludes `#` outright, and its backslash escape covers the pipe only
(`quartz/plugins/transformers/ofm.ts:120`, quartz v4.5.1), so a `#` cannot be
escaped into an alias — it can only be removed.

What a future decision could change, if label fidelity is judged to matter:

- substitute a lookalike outside the excluded class (`C♯`) instead of deleting;
- or emit an inline markdown link `[C#](content/source/<uuid>)` for labels that
  would otherwise be degraded, since markdown link text has no such restriction.

Both are emitter-shape changes with their own blast radius. Recorded here as an
operator decision, not opened as work.

### 2. Two alias emitters still call no sanitiser — and this was NOT filed before

The `wikilink` attempt-1 tester found these and wrote that they were "recorded
here and in the findings sink." They reached the queue evidence file only; this
file named neither. Filing them now so they stop being rediscovered:

- `OB1/recipes/entity-wiki/generate-wiki.mjs:829` — the entity **auto-linker**
  (`[[${slug}|${name}]]`), which interpolates a raw entity name.
- `OB1/recipes/entity-wiki/generate-wiki.mjs:1090` — the entity **index**
  (`[[${n.slug}|${n.label}]]`), which interpolates a raw entity label.

Either breaks exactly the way the `Grounded by` emitter did if an entity name
ever contains a `#` or a bracket — and `C#` is precisely the kind of entity a
knowledge wiki mints. This is the residue of the anchor's out-of-scope clause
("finding every such emitter is a wider sweep"), so it is a candidate anchor of
its own, not a defect in what merged.

The lesson worth keeping separately: **a finding is filed when it is in the
sink, not when it is in the evidence file.** Queue evidence is per-attempt and
nobody reads it again; this file is the thing that survives.
