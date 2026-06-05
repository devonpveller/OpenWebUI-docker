# Task List — Quartz 4 Expansion (Open Brain Workbench)

**Companion to:** [quartz-4-expansion-plan.md](quartz-4-expansion-plan.md) — the **source of truth**. Every task below cites the plan section / phase it implements. When the plan and this file disagree, the plan wins; fix this file.
**Source idea:** [initial-quartz-4-expansion-idea.md](initial-quartz-4-expansion-idea.md)
**Created:** 2026-06-05 · **Branch:** `feature/integrated-knowledge-system`
**Audience:** an autonomous Claude coding session. Read [§0 Working agreements](#0-working-agreements-read-before-touching-anything) **first** — those are non-negotiable and recur in almost every phase.

**How to use:** work top-to-bottom within the [dependency order](#dependency-order). Tick a box only when its line is *done and locally verified*. A phase is shippable only when its **Gate** (the plan's per-phase gate, restated here) passes. Do **not** start a phase whose dependencies' Gate is unmet.

**Legend:** `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked · ⚠ = audit-critical (a miss here causes data loss, a security hole, or silent breakage) · 🔁 = three-place change owed (compose + recovery + stack-map).

---

## 0. Working agreements (read before touching anything)

These hold for **every** task. They are the parts an autonomous run is most likely to get wrong.

- **G1 — Never `git add`/`commit`/`push` on the operator's behalf** ([git-handling-boundaries](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/git-handling-boundaries.md), plan §11). The **one** exception: the workbench's *own programmatic commits inside the vault repo* (that **is** the notes write mechanism, P3) — local only, **no remote push** until the self-hosted vault exists (D-I). You, the coder, still never commit the repo you are editing.
- **G2 — Additive schema only** (plan §11). Never `ALTER`/`DROP` an existing `thoughts`/`sources` column. Widening a CHECK = drop-and-re-add with values only **added**. New columns are `ADD COLUMN IF NOT EXISTS … DEFAULT …`.
- **G3 — Every new SQL file ships in *two* places** (plan §7 ⚠ migration). `/docker-entrypoint-initdb.d` runs **only on a fresh `openbrain-db-data` volume** ([docker-compose.yml:36-37](../../../OB1/docker/docker-compose.yml#L36-L37)). So each new `.sql` must be **(a)** mounted with an ordering prefix *after* `70-init-threads.sql` (`80-…`, `90-…`, `95-…`) for fresh installs **and (b)** applied to the **live** DB via the psql promotion runbook. A file only added to compose **silently no-ops on the running stack.**
- **G4 — Three-place change convention** (CLAUDE.md, plan §11). Adding/removing a container (`openbrain-workbench`, `openbrain-extract`) means editing **together**: the compose file, the recovery scripts' inventory **and** ordered start/stop sequences ([scripts/emergency-recovery.ps1](../../../scripts/emergency-recovery.ps1) + `.bat`), and the stack-map ref ([workspace-stacks.md](../../../.claude/skills/stack-map/references/workspace-stacks.md)). Run the `/stack-map` skill before and after compose edits to catch drift. Marked 🔁 below.
- **G5 — One shared slug module** (plan §12.12, §14.1). Do **not** add a 4th/5th copy of `slugify`. Extract `slug.mjs`/`slug.ts` (canonical = the NFKD-normalize → lowercase → `[^a-z0-9]+`→`-` → trim algorithm) **in P0.6** and import it from the recipe, the compiler, and the workbench. A drifting slug fn silently breaks `[[wikilink]]` resolution across layers.
- **G6 — Pinned slugs** (plan §14.2). Notebook (`threads.slug`) and entity slugs are generated **once at create time** and **never recomputed**. Rename touches the display `name` only; pages emit `aliases: [name]` so old links resolve. De-collide on the `UNIQUE` violation (`-1`/`-2`, mirroring [`resolveOutputPath`](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L815)).
- **G7 — Secret stays server-side** (plan §2.3, §12.6). A static Quartz page **cannot hold a bearer.** Never embed `MCP_ACCESS_KEY`/`WORKBENCH_KEY` in client JS. Caddy **injects** `header_up X-Brain-Key {$WORKBENCH_KEY}`; the workbench trusts that header on `app-net` and is **never host-published** (debug publish to `127.0.0.1` only).
- **G8 — Workbench writes are transactional** (plan §14.5). Multi-row writes (import = source + chunks + links) go through **deno-postgres with a transaction** (precedent: `openbrain-suggestion-worker`, [docker-compose.yml:299-325](../../../OB1/docker/docker-compose.yml#L299-L325)). Reads may use PostgREST. Never fire several non-atomic PostgREST writes for one logical unit.
- **G9 — Workbench is sub-routered, not a god-switch** (plan §14.3). Hono sub-routers per resource (`/sources`, `/threads`, `/notes`, `/import`, `/notebooks`, `/grounding`) over a thin **service → repository** layering. Path/asset normalization (no `../` escape) lives in **one shared validator** (§14.3, §11), not per-handler.
- **G10 — Validation is a sandbox/dev concern.** Do not test against prod volumes. Stand validation on a throwaway DB volume; never run a destructive migration against the live `openbrain-db-data` without the operator running the promotion runbook.

---

## Dependency order (plan §8)

```
P1 Provenance (compiler-only) ──> P4 Source Lifecycle (edit+retract) ─┐
                                                                      ├─> P6 Grounding &
P0 Foundations ─┬─> P2 Notebooks                                      │   Deliberate Linking
 (workbench +   ├─> P3 Notes                                          │            │
  extract)      └─> P5 Import ─────────────────────────────────────────┘            ▼
                                                                       [P7 Podcasts — DEFERRED]
```

- **P1 is compiler-only and lands independently of P0** — ship it first/in parallel as a low-cost early win.
- **P0 unblocks every workbench-backed phase.** P2 / P3 / P5 parallelize after it. P4 / P6 add write paths on top.
- **P4** needs P1's source leaf page (read view) + P0. **P5** needs P2 (thread linking) + P0; uses P4 conventions. **P6** stitches P1 + P4 + P5.
- **P7 is DEFERRED** — do not build it. ON keeps serving podcasts until it ships.

---

## Phase 1 — Provenance: Source **and** Thought Visibility  *(compiler-only; no P0 dep)*
*Plan [Phase 1](quartz-4-expansion-plan.md#phase-1--provenance-source-and-thought-visibility). Goal: every inline `[#id]` (thought) and `[S:id]` (source) citation becomes a real internal wikilink to a compiled leaf page, so native Quartz popover + SPA + backlinks + search apply with **no custom interaction code**.*

Touchpoints: [generate-wiki.mjs](../../../OB1/recipes/entity-wiki/generate-wiki.mjs), [wiki-service.mjs](../../../OB1/docker/wiki-service/wiki-service.mjs), `OB1/docker/wiki-viewer/quartz-overlay/`.

- [ ] **1.1 — Emit bounded leaf pages.** Write `content/thought/<id>.md` and `content/source/<uuid>.md` **only for ids actually cited this compile** — collect the union of `provenance.linked_ids` / `semantic_ids` / `source_ids` across all generated pages ([generate-wiki.mjs:542-544](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L542-L544)). Bounded by *citations*, not the DB (800 cited → 800 leaves, not 50k).
- [ ] **1.2 — Batch-fetch full content by id** for leaf bodies (`thoughts?id=in.(…)` / `sources?id=in.(…)`) — the synthesis payload only carries 300-char snippets. Leaf frontmatter: `type: thought|source`, date, `metadata.type`; sources add `url`/`title`/`content_type`/`notebook`.
- [ ] **1.3 ⚠ — Rewrite citations into wikilinks, UUID-aware.** Post-process generated pages: `[#11173]` → `[[thought/11173|#11173]]`; source citation → `[[source/<uuid>|S:<token>]]`. **`thoughts.id` is `BIGSERIAL`** ([init.sql:9](../../../OB1/docker/init.sql#L9)) — small ints, rewrite literally. **`sources.id` is `UUID`** ([init-sources.sql:23](../../../OB1/docker/init-sources.sql#L23)) — the regex must match a 36-char hyphenated UUID, **not** `\d+`. An id with no emitted leaf (uncited / mis-cited) stays **plain text** (mirror broken-wikilink handling) — no broken links.
- [ ] **1.4 ⚠ — Short per-page source token** (plan §12.11). Do **not** ask the LLM to echo UUIDs (high transcription error). In `buildSynthesisInput`/`synthesize` present sources under a stable per-page token (`S1,S2,…`) mapped to real UUIDs in the structure payload; resolve token→UUID **deterministically** during the 1.3 rewrite. Thought ids stay literal.
- [ ] **1.5 ⚠ — Dedicated leaf sweep (NOT the entity sweep — data-loss bug otherwise).** The existing `sweepOrphanEntityPages` ([wiki-service.mjs:457](../../../OB1/docker/wiki-service/wiki-service.mjs#L457)) builds its kept-set from **entity slugs only**, and `listEntityFiles` ([wiki-service.mjs:424](../../../OB1/docker/wiki-service/wiki-service.mjs#L424)) walks every `content/<dir>/` except `topic/`. Left alone it deletes **every leaf on the next compile.** Required: **(a)** add `thought/` and `source/` to the `listEntityFiles` skip-list (exactly as `topic/` is skipped), and **(b)** add a **new `sweepOrphanLeafPages`** keyed on the set of ids cited this compile, removing only leaves no longer cited.
- [ ] **1.6 — Untrusted-content guard.** Leaf bodies render captured/external text: keep the scrub ([generate-wiki.mjs:597-610](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L597-L610)) and rely on Quartz markdown→HTML sanitization. This text is untrusted at *render* time, not just as LLM input.
- [ ] **1.7 — Leaf-page template (Quartz overlay).** Small overlay layout keyed on `type: thought|source` (metadata header + body + backlinks) so leaves read as records, not orphans. Hover-popover + click-navigate + graph nodes + backlinks + search come **free** from stock v4.5.1 — no custom linkifier.
- [ ] **1.8 — `ProvenancePanel.tsx` (OPTIONAL).** A consolidated per-page provenance index over the generator's `## Sources` section. Nice-to-have; not load-bearing (inline links + backlinks already deliver traceability). Skip if time-boxed; it is built out further in P6.
- [ ] **1.9 — Keep leaves out of the entity graph** (plan §14.6). Leaf pages are a *distinct page class* — they must **not** enter `graph.json`/`entities.md` candidate selection. The "pages come from the entity graph" model is untouched.

**Gate (plan):** open a **thought-only** page → `[#id]` markers are real links → hover = native popover of the captured thought, click → `thought/<id>` leaf (with "cited by" backlinks). A **sourced** page → `[S:id]` does the same to a `source/<uuid>` leaf. An uncited/unknown id stays plain text. After a citation is removed, the next compile sweeps the now-orphan leaf. Behavior indistinguishable from any `[[wikilink]]`.

---

## Phase 0 — Cross-cutting foundations  *(unblocks P2/P3/P4/P5/P6)*
*Plan [§3](quartz-4-expansion-plan.md#3-cross-cutting-foundations-phase-0). Stand up the workbench + extract architecture and prove it end-to-end before any schema/extractor/surface work.*

- [ ] **0.1 🔁 ⚠ — `openbrain-workbench` skeleton.** Deno+Hono, internal `PORT=8000`, `/health`, trusts Caddy-injected `X-Brain-Key`. Networks **`obnet` + `llm-net` + `app-net`** (app-net is **required** for portal-Caddy name resolution — see §2.3; the earlier "obnet+llm-net only" list is incomplete). Optional debug publish `127.0.0.1:8814:8000` (the `:8814` in the plan is the **host** debug port, not a second internal port). Structure per **G9** (sub-routers + service→repository). DB access per **G8** (deno-postgres). → compose **+ recovery scripts + stack-map** (G4).
- [ ] **0.2 ⚠ — Caddy `/workbench/*` same-origin route.** Add `handle_path /workbench/* { reverse_proxy openbrain-workbench:8000 … }` to the **`wiki.{$PUBLIC_DOMAIN}` block** of [config/caddy/Caddyfile](../../../config/caddy/Caddyfile#L210-L257), **above** the catch-all `reverse_proxy` to the viewer, plus the equivalent Tailscale `serve` path ([recipe](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/tailscale-serve-restore-recipe.md)). **Do NOT** touch [OB1/docker/Caddyfile](../../../OB1/docker/Caddyfile) — it only fronts internal PostgREST, not the viewer. Caddy injects the secret: `header_up X-Brain-Key {$WORKBENCH_KEY}` (G7).
- [ ] **0.3 ⚠ — Raised upload cap on the import sub-route.** The wiki subdomain enforces `request_body { max_size 1MB }` ([Caddyfile:216-218](../../../config/caddy/Caddyfile#L216-L218)); every P5 import exceeds it. Give `/workbench/import` its own `@import` matcher with `max_size 100MB`; the workbench independently enforces its own ceiling too.
- [ ] **0.4 — Quartz overlay scaffold + asset config.** No-op component proving the overlay loads and client `fetch('/workbench/health')` works through the portal. Confirm `assets/` is **served but not paginated** (plan §2.2). Overlay model: `OB1/docker/wiki-viewer/quartz-overlay/` `COPY`'d over the pinned clone after `git clone`, before `npm ci` — keeps `QUARTZ_REF=v4.5.1` upgradeable (plan §2.5).
- [ ] **0.5 — `wiki-assets` volume** wired to **both** workbench (writes) and viewer (serves). Decouples binaries from the git vault (D-I, plan §9). Confirm backup coverage is owed (P5/§9).
- [ ] **0.6 ⚠ (G5) — Shared slug module.** Extract `slug.mjs`/`slug.ts` and import it from the recipe ([generate-wiki.mjs:706](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L706)), the compiler (`slugifyEntity`/`slugifyNotebook`, [wiki-service.mjs:401-420](../../../OB1/docker/wiki-service/wiki-service.mjs#L401-L420)), and the workbench. Remove the hand-synced copies. **Do this in P0** so P1 leaf ids and P2 thread slugs consume one canonical fn from day one.
- [ ] **0.7 — Shared TS types** for source/thread/membership/provenance shapes, mirrored from [init-sources.sql](../../../OB1/docker/init-sources.sql) / [init-threads.sql](../../../OB1/docker/init-threads.sql). Consumed by the workbench and the inline components (plan §P0.5).

**Gate (plan):** a custom component, served through the portal, calls the authed API and renders; an image under `assets/` renders in a page. **No `sources` writes yet.**

---

## Phase 2 — Notebooks & Membership (research groups)  *(needs P0)*
*Plan [Phase 2](quartz-4-expansion-plan.md#phase-2--notebooks--membership-research-groups) + [§5](quartz-4-expansion-plan.md#5-notebooks--research-groups-the-core-organizing-axis). The core organizing axis. "**Notebook**" is the user-facing noun; the table stays `threads`/`thread_sources`. M:N, non-exclusive.*

- [ ] **2.1 ⚠ (G2/G3/G6) — Pin the slug.** `ALTER TABLE public.threads ADD COLUMN IF NOT EXISTS slug TEXT;` + `CREATE UNIQUE INDEX IF NOT EXISTS uq_threads_slug ON public.threads(slug);`. Workbench generates the slug **once at create** via the shared module (0.6), de-collides on `UNIQUE` violation, **never recomputes** (rename touches `name` only). New SQL ships in **both** migration places (G3).
- [ ] **2.2 ⚠ — Notebook hub page folds in `topic/`.** Compiler emits `content/notebook/<slug>.md` with **(1)** `## Synthesis` — point [synthesize-notebooks.mjs](../../../OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs) to write **here** instead of `topic/<slug>.md`; **(2)** `## Sources` (`thread_sources.status=confirmed`, with P1 provenance); **(3)** `## Notes` listing/linking `notes/<slug>/*`; **(4)** `## Suggestions` triage strip; plus a scoped graph + backlinks. Hub emits `aliases: [name]` (G6) so `[[Notebook Display Name]]` resolves.
- [ ] **2.3 ⚠ — Retire the `topic/` sweep path.** Because synthesis now lands in `content/notebook/`, update/replace `sweepOrphanTopicPages` ([wiki-service.mjs:511](../../../OB1/docker/wiki-service/wiki-service.mjs#L511)) and add `notebook/` to the `listEntityFiles` skip-list with its **own** kept-set (exactly as `topic/` had). Don't let the entity sweep eat notebook hubs (same class of bug as 1.5).
- [ ] **2.4 — Backfill notebooks.** For every distinct `metadata.notebook` string on a source/thought with **no matching `threads` row**, auto-create one (slug pinned). Guarantees every notebook — research-run free-text tag **or** user `notes/` folder — has exactly one discoverable hub. No hidden parallel notebooks.
- [ ] **2.5 — Backend: notebook routes.** `GET/POST/PATCH /workbench/notebooks` (→ `threads`); `POST /workbench/notebooks/:id/sources` (link via `link_source_to_thread`); `DELETE` (unlink via `set_thread_source_status → hidden`); suggestion triage (`accept` → confirmed / `hide` → hidden). Hono sub-router (G9).
- [ ] **2.6 — Compiler: notebook graph nodes** into `graph.json`; generate one hub per active notebook from `threads` + `thread_sources(status=confirmed)` + backfill.
- [ ] **2.7 — Quartz: `NotebookIndex.tsx`, `NotebookPage.tsx`, `MembershipPicker.tsx`** (+ `.inline.ts`). Hub = compiled stub + live hydration (plan §12.4): compiler emits a thin shell (title, description, synthesis, static graph); `NotebookPage.inline.ts` fetches live sources/notes/membership/suggestions so add/remove reflects instantly, degrading to the shell if the API is down. Leverage native backlinks/graph (§5.2, §5.4).

**Gate (plan):** create a notebook (slug pinned), rename it (page + links survive), add a source from two notebooks (proves non-exclusive), unlink from one (still in the other), accept a worker suggestion, and confirm a hand-created `notes/<slug>/` folder shows under the hub's `## Notes` — all reflected after recompile.

---

## Phase 3 — Notes System (authored layer: human + AI)  *(needs P0; aligns with P2 slug)*
*Plan [Phase 3](quartz-4-expansion-plan.md#phase-3--notes-system-authored-layer-human--ai--d-e). Author Obsidian-style notes in Quartz, written additively into OB1; also accept notes emitted by AI assistants into the same layer.*

- [ ] **3.1 — Align notes folder to the pinned notebook slug.** Reuse `ingestNotes()` (it already maps `notes/<notebook>/file.md` ⇄ a thought). Align `notes/<notebook>/` = the pinned **notebook slug** (P2) so notes group under their hub; update `ingestNotes()` `notebook = parts[1]` to the slug (plan §12.5).
- [ ] **3.2 — Backend: notes write path.** `PUT/GET /workbench/notes/<path>` — path validated under `notes/` via the **one shared no-`../`-escape validator** (G9, plan §14.3); write + `git commit` **in the vault repo** (the sanctioned exception to G1); optimistic concurrency via content-hash / `If-Match`. Build a notes index.
- [ ] **3.3 — AI notes use the SAME write path** (one ingestion surface, not a parallel one). A small OWUI→workbench `PUT /workbench/notes` hand-off writes a research-chat synthesis into `notes/<notebook-slug>/…`; existing notes ingest tethers it.
- [ ] **3.4 — Provenance stamp.** `metadata.source = user_note | ai_note` (+ originating agent/chat for AI notes) so hub + search distinguish authorship; both tether to thoughts identically.
- [ ] **3.5 — Quartz: `NotesEditor.tsx` + `.inline.ts`.** Editor with live preview, `[[…]]` autocomplete from `entities.md` + the notebook index (so authors pick an **existing** notebook, not a fat-fingered near-duplicate slug), tags.
- [ ] **3.6 — Decision lock:** notes stay in the `notes/` layer tethered to thoughts — **not** a separate `user_notes` collection (idea-doc Q1, plan §12.5).

**Gate (plan):** create/edit a note (human- and AI-authored) → appears in vault under its notebook, links resolve, next compile tethers + extracts and the note shows under the hub's `## Notes`; a two-session edit conflict is detected.

---

## Phase 4 — Source Lifecycle: Edit-with-history + Retract/Restore  *(needs P1 + P0)*
*Plan [Phase 4](quartz-4-expansion-plan.md#phase-4--source-lifecycle-edit-with-history--retractrestore-d-d). Sources are added as-is, editable with preserved history, removed reversibly. D-D.*

- [ ] **4.1 ⚠ (G2/G3) — `source_revisions` table.** New `init-source-revisions.sql`: `source_revisions(source_id, revision, content, title, edited_at, edited_by)`, append-only. Mount as `80-init-source-revisions.sql` (fresh) **and** apply live (G3).
- [ ] **4.2 — Versioned edit.** An edit snapshots prior `content`/`title` into `source_revisions`, then updates `sources.content` (current = head). `source_id` **never changes** (keeps `thread_sources`/`source_entities`/search valid). Re-embed is automatic via the existing fingerprint-gated queue trigger; **metadata-only edits must not bump the content fingerprint.**
- [ ] **4.3 ⚠ (G2/G3) — Retract columns.** `ALTER TABLE public.sources ADD COLUMN IF NOT EXISTS retracted_at TIMESTAMPTZ` + `retracted_by TEXT`. Both migration places (G3).
- [ ] **4.4 — Three distinct removal verbs (keep visually separate, plan §11):**
  - **Unlink from notebook** (per-notebook membership) — `set_thread_source_status → hidden`. Source stays in its other notebooks + in generation. *Not* a deletion.
  - **Retract** (global, reversible — **the default "remove"**) — set `retracted_at`/`retracted_by`. Row + content **retained and restorable**, but invisible to all generation. **Restore** = clear `retracted_at`; `source_entities`/`thread_sources` rows survive so it lights straight back up.
  - **Purge** (global, irreversible) — `DELETE FROM sources` → cascades `source_entities`/`thread_sources`/`session_sources`/`source_revisions`/`source_chunks` → orphan sweep removes unsupported pages + assets. **Rare operator escape hatch, gated behind explicit confirm.**
- [ ] **4.5 ⚠⚠ — Tombstone filtering on EVERY generation read-path (the P4 regression checklist — miss one and tombs resurface):**
  - [ ] `fetchLinkedSources` ([generate-wiki.mjs:435](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L435)) — the main source→page join
  - [ ] `listBatchCandidates` source-count ([generate-wiki.mjs:302](../../../OB1/recipes/entity-wiki/generate-wiki.mjs#L302), the `source_entities` count ~L324) — so a tomb can't keep a page alive
  - [ ] `match_sources` + new `match_source_chunks` RPCs ([init-sources.sql:78](../../../OB1/docker/init-sources.sql#L78)) — `AND s.retracted_at IS NULL`
  - [ ] notebook synthesis source pulls ([synthesize-notebooks.mjs](../../../OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs))
  - [ ] P1 source-leaf emission + leaf-sweep (a retracted source's `source/<uuid>` leaf is swept)
  - [ ] the extraction queue (don't re-extract a tomb — it must stop producing fresh `source_entities`)
- [ ] **4.6 — Backend.** `PATCH /workbench/sources/:id` (versioned edit), `GET …/revisions`, `POST …/:id/retract {scope: notebook|global}` (notebook→status flip, global→`retracted_at`), `POST …/:id/restore`, `DELETE …/:id` (purge, operator-confirmed). Sub-router (G9).
- [ ] **4.7 — Quartz: `SourceEditor.tsx`** (inline editor + version history/diff) and **`SourceRetractor.tsx`** (unlink-vs-retract-vs-purge, confirm dialog showing affected pages/links; **default to retract**, purge gated behind explicit confirm; don't expose purge to non-operators).

**Gate (plan):** edit → new revision recorded, old preserved, re-embed enqueued, dependent pages refresh; **retract → source vanishes from every generation read-path but the row survives; restore → it returns with links intact**; purge cascades + sweeps; purge always needs explicit confirmation.

---

## Phase 5 — Direct Source Import Pipeline (incl. images & audio)  *(needs P2 + P0; uses P4 conventions)*
*Plan [Phase 5](quartz-4-expansion-plan.md#phase-5--direct-source-import-pipeline-incl-images--d-f). Drag-and-drop / picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT, images, and audio/video (STT) → extract → chunk → embed → first-class sources, linked to a chosen notebook, with progress + errors; images render in Quartz. D-F.*

- [ ] **5.1 🔁 ⚠ — `openbrain-extract` sidecar.** Python/FastAPI wrapping `content-core`. Stable `POST /extract` → `{ markdown, title, metadata, pages, images[] }` over a **format→extractor registry** (PDF: PyMuPDF/Docling-class; DOCX/PPTX: python-docx/-pptx; images: Pillow+Tesseract OCR; audio/video: STT at `host.docker.internal:8000/v1`; content-core where most faithful). **A future format (epub/html/eml/spreadsheet) is a new registry entry behind the unchanged `/extract` contract** — callers never learn about new formats. **Per-format extraction-quality acceptance gate** (text fidelity, tables, headings, image refs). **⚠ Sandbox it** (plan §11): untrusted file/image parsing = classic RCE vector — run unprivileged, no extra network. → compose **+ recovery + stack-map** (G4).
- [ ] **5.2 ⚠ (G2/G3) — Schema: `content_types` reference table + FK** (replaces the inline CHECK, [init-sources.sql:27-30](../../../OB1/docker/init-sources.sql#L27-L30)). `content_types(value TEXT PRIMARY KEY, label, category, created_at)` + FK `sources.content_type → content_types(value)`. **Live-DB migration order matters:** create table → **seed the existing 7 values already in `sources` + the new ones** (`docx,pptx,image,audio,txt,md`) → drop the old CHECK → add the FK (FK-add fails if any existing value is unseeded). `find_or_create_source`'s `'web_article'` default stays valid. A new format is then **one `INSERT`, no DDL.**
- [ ] **5.3 ⚠ (G2/G3) — Schema: `source_chunks` + `match_source_chunks`.** New `init-source-chunks.sql`: `source_chunks(source_id UUID, idx INT, content TEXT, embedding VECTOR(1024), PRIMARY KEY(source_id, idx))`, `source_id … REFERENCES sources(id) ON DELETE CASCADE`. Search RPC **named `match_source_chunks`** — do **not** overload `match_sources` ([init-sources.sql:78](../../../OB1/docker/init-sources.sql#L78), plan §14.6). Mount `90-init-source-chunks.sql` (fresh) + live (G3). (Backs long-doc retrieval *and* the P7 podcast source list.)
- [ ] **5.4 ⚠ (G2/G3) — Schema: `import_jobs`.** `import_jobs(id, status, source_id, target_entity_ids, target_notebook, error, created_at, updated_at)` (plan §14.4). Durable so a workbench restart doesn't orphan in-flight imports; backs `ImportStatus.tsx`; **load-bearing for P6** (records target links + terminal error of a failed grounding attempt). Both migration places (G3).
- [ ] **5.5 ⚠ (G8) — Workbench `POST /workbench/import` (async, transactional).** Store upload → extract → write images to `assets/<source-id>/` + rewrite refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(…, 'deliberate')` to the selected notebook → return `job_id`. `GET /workbench/jobs/:id` for progress. **Source + chunks + links land in ONE deno-postgres transaction** (G8) so a mid-sequence failure can't leave a source with no chunks/links.
- [ ] **5.6 — Image handling** (plan §2.2): extraction returns embedded images → workbench writes `assets/<source-id>/img-n.png` and rewrites source markdown to `![alt](assets/<source-id>/img-n.png)` → Quartz renders inline. A standalone image upload → a `source` (`content_type='image'`, content = OCR/caption text for embedding) with the image as its asset.
- [ ] **5.7 — Quartz: `ImportDropzone.tsx`** (validation, per-file progress, errors) + **`ImportStatus.tsx`** (history from `import_jobs`); notebook selector reuses `MembershipPicker`.

**Gate (plan):** drop a PDF, DOCX, PNG (and an MP3) → all extract/transcribe, chunk, embed, dedupe, link to the chosen notebook, entity-extract, surface via P1 provenance + on the P2 hub; images render inline; corrupt files fail clearly.

---

## Phase 6 — Source Grounding & Deliberate Wiki Linking  *(needs P1 + P4 + P5)*
*Plan [Phase 6](quartz-4-expansion-plan.md#phase-6--source-grounding--deliberate-wiki-linking-d-j). Turn a thought-only page into a source-grounded entity by attaching a source from the page being read, then regenerating. Closes the [§1.4](quartz-4-expansion-plan.md#14-why-wiki-pages-appear-without-sources-the-grounding-gap) gap. D-J.*

- [ ] **6.0 — (Optional pre-work, do on live stack now) triage the grounding gap** (plan §13): query `source_extraction_queue` for stuck/`pending`/`last_error` rows and sample sourceless entities for `source_entities` vs `thought_entities` counts — to size how much is extraction backlog (cause 2, fixable now) vs thought-only-by-design (cause 1, needs P6). Worth doing before committing P6 scope.
- [ ] **6.1 — Live grounding-state badge (hydrated, not baked).** In-Quartz component hydrates from the workbench and shows one of: **"Mental model — thought-only"** · **"⏳ Grounding pending"** · **"Grounded by N source(s)"** · **"⚠ Ingest failed"**. `GET /workbench/grounding` also surfaces `source_extraction_queue` health so a **backlog** page is never mislabeled **by-design**. Compiler policy: **badge** thought-only pages, **never suppress** them (D-J, plan §11 "don't suppress graph pages").
- [ ] **6.2 — Ground-from-the-page (the core feature).** On the live page, **"Provide grounding with a new source"** accepts a document (upload) or URL (ingest), reusing the P5 import pipeline, linked to **this page's entity** as a `source_entities` row (entity-level grounding — distinct from P2 notebook-level `thread_sources`). On success the entity is marked to regenerate; `fetchLinkedSources` includes it → page regenerates and flips to "Grounded."
- [ ] **6.3 ⚠ — `source_entities` marker (no `metadata` column exists).** `source_entities` ([init-source-graph.sql:27-35](../../../OB1/docker/init-source-graph.sql#L27-L35)) is `source_id, entity_id, mention_role, confidence, evidence, created_at`, **PK `(source_id, entity_id)`** — no `metadata`. Mark a manual link with `mention_role='user_linked'`, `confidence=1.0`, `evidence='manual:<operator>@<iso8601>'`. (A `metadata JSONB` column is optional/unnecessary — `mention_role` suffices.) **PK-collision policy (must hold): `user_linked` wins** — a manual row and an auto `mentioned` row for the same pair can't coexist.
- [ ] **6.4 ⚠ — Worker must not clobber `user_linked` rows (concrete change).** The worker does a full wipe-and-reinsert per source: `await supabase.from("source_entities").delete().eq("source_id", item.source_id)` ([entity-extraction-worker/index.ts:748](../../../OB1/integrations/entity-extraction-worker/index.ts#L748)). Change to scope out manual links: `.delete().eq("source_id", …).neq("mention_role","user_linked")`, and make the subsequent insert an upsert that yields to `user_linked` (6.3 policy). **Guard + test: a re-extraction cycle must leave the manual link intact.**
- [ ] **6.5 — Grounded-page generation policy** (sources = facts, thoughts = demoted, not deleted). Once a page has ≥1 source, regeneration lets **sources carry asserted facts** and reframes thought-derived claims under a labeled **"Working hypotheses / unverified"** framing — **not** dropped (thought-only entities are the majority of pages, §1.4).
- [ ] **6.6 — Failure handling.** On ingest failure (bad URL, unparseable doc): **do NOT regenerate** the entity. The live component marks "⚠ Ingest failed" **client-side** (hydrated, no recompile); the failure is recorded durably in `import_jobs` (terminal `status=failed` + `error` + `target_entity_ids`) — feeding a later "failed grounding attempts" alerts surface (*capture now, surface later*). Only a **successful** ingest triggers regeneration.
- [ ] **6.7 — Evolution / timeline (P6b — derive, no new storage).** Render a `## Evolution` section **derived** from `source_entities.created_at` (when each grounding source attached) + the entity's first-seen, complemented by the **vault git history** (every compile is a commit). Works on **today's** `openbrain-wiki-data` volume git right now; survives the future D-I self-hosted-git migration unchanged.
- [ ] **6.8 — Upload-and-link entry point.** The same flow is reachable from the P5 `ImportDropzone` via a **"link to wiki page(s) / notebook"** target field — a new source is ingested **and** linked to chosen entities/notebooks in one action.
- [ ] **6.9 — Quartz: `SourceLinker.tsx`** (entity/page picker on a source view + in the editor), the live grounding-state badge + `## Evolution` in `ProvenancePanel`, "Provide grounding with a new source" on the entity page, "link targets" field in `ImportDropzone`.
- [ ] **6.10 — Scope note:** entity pages first (the common case). Notebook-hub synthesis pages (e.g. autobiography) are generated differently — grounding for those is a follow-up, **not** in P6.

**Gate (plan):** from a thought-only page, "provide grounding" with a URL/doc → it ingests, links to the entity, the page regenerates citing it (sources-as-facts / thoughts demoted), badge flips **Mental model → Grounded**; a **failed** ingest shows "⚠ Ingest failed" **without** recompiling + lands a `failed` `import_jobs` row; the manual link survives a re-extraction cycle; a backlog page shows "⏳ pending", not a false "ungrounded"; `## Evolution` shows the transition.

---

## Phase 7 — Podcast Service  *(DEFERRED — DO NOT BUILD)*
*Plan [Phase 7](quartz-4-expansion-plan.md#phase-7--podcast-service-deferred--d-bd-h). Open Notebook keeps serving podcasts until this ships. Recorded so the architecture leaves room; **no `openbrain-podcast` container is built in P1–P6.***

- [ ] **7.x — DEFERRED.** When started: per-thread generation (pull `source_chunks` → script via local Qwen `llama-cpp` → audio via existing TTS at `host.docker.internal:8000/v1` → `assets/podcasts/<id>.mp3` + transcript); additive `podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)`; ON-style config panel (speakers 1–4, voice, style/length); transcript → note via the P3 write path; `PodcastPanel.tsx`. Prefer a **thin TTS caller** over a heavy `podcast-creator` dependency. **Full ON decommission happens after P7** (plan §10), not after P5.

---

## Cross-cutting / closeout (do as the relevant phase lands, not at the end)

- [ ] **X.1 🔁 — `/stack-map` shows no drift** after the P0.1 workbench and P5.1 extract containers land (compose + recovery inventory + ordered start/stop + stack-map ref all in sync). Run the skill before and after each compose edit (G4).
- [ ] **X.2 — Backups cover `wiki-assets`** (plan §9, §11): the new binary volume must be in the backup set. Purged sources drop their assets; **retracted sources keep theirs** (restorable).
- [ ] **X.3 — Promotion runbook** (operator-executed, mirrors the IKS pattern): backup → live-DB migration order (`80-init-source-revisions` → `90-init-source-chunks` → `95-content_type`/`source_entities` widening → `threads.slug` → `sources.retracted_*` → `import_jobs`) → rebuild order → rollback. **The agent never runs a destructive migration against prod** (G10) — it authors the runbook; the operator runs it.
- [ ] **X.4 — `host.docker.internal` reachability** confirmed from the extract/workbench containers before P5/P7 depend on TTS/STT (plan §11); containers on `obnet` need host-gateway access (Docker Desktop provides it).
- [ ] **X.5 — GPU/compile churn** (plan §11): batch embeddings; lean on the 3-min change-watch debounce; STT/TTS contend with `llama-cpp` ([llama-swap perf tuning](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/llama-swap-perf-tuning.md)).

---

## Audit-critical items to keep front-of-mind (plan §11, §14, inline ⚠)

1. **Leaf sweep ≠ entity sweep (1.5).** Reusing `sweepOrphanEntityPages` deletes **every** `thought/`+`source/` leaf on the next compile. Needs the skip-list addition **and** a new `sweepOrphanLeafPages`. Same bug-class for the notebook hub (2.3).
2. **Source citations are UUIDs, not ints (1.3/1.4).** Regex must match 36-char hyphenated UUIDs; never make the LLM echo a UUID — use the `S1/S2…`→UUID per-page token map. Thought ids (`BIGSERIAL`) stay literal.
3. **Tombstone filtering is a *checklist*, not one edit (4.5).** Six read-paths must add `retracted_at IS NULL`. Miss one → retracted sources resurface in generation.
4. **`source_entities` has no `metadata` column (6.3).** Use `mention_role='user_linked'`; PK is `(source_id,entity_id)`; `user_linked` wins on collision.
5. **The worker wipes `source_entities` per source (6.4).** Without the `.neq("mention_role","user_linked")` scope, every manual grounding link is deleted on the next re-extraction.
6. **New SQL must hit *two* places (G3).** initdb mount (fresh) **and** the live-DB promotion runbook. Compose-only = silent no-op on the running stack.
7. **`content_type` migration order on the live DB (5.2).** create → seed existing+new values → drop CHECK → add FK. FK-add fails on any unseeded value.
8. **Secret never reaches the browser (G7).** Caddy injects `X-Brain-Key`; workbench is never host-published; client JS holds no bearer.
9. **Caddy edit is the *portal* `wiki.` block, not OB1/docker/Caddyfile (0.2).** The OB1 Caddyfile only fronts internal PostgREST.
10. **Don't suppress graph pages (6.1/6.5).** Badge thought-only pages; never delete them — that breaks cross-entity `[[wikilinks]]` and graph nodes.
