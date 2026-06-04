# Quartz 4 Expansion Plan — Quartz as the Open Brain Workbench

> **Status:** Plan / pre-implementation
> **Branch context:** `feature/integrated-knowledge-system`
> **Reframes:** the Open Notebook repoint phases of the in-flight Integrated
> Knowledge System (IKS) work — see [§10](#10-relationship-to-iks--retiring-open-notebook).
> **Source idea:** [initial-quartz-4-expansion-idea.md](documentation/implementation-guide/expand-quartz-4/initial-quartz-4-expansion-idea.md)

---

## 0. Decisions locked for this plan

Confirmed with the operator across two rounds.

| # | Decision | Consequence |
|---|----------|-------------|
| D-A | **Interactive layer = in-Quartz client components.** | Preact components + client scripts in the Quartz viewer, backed by a thin write-API on Open Brain (Quartz can't write Postgres from a static page). |
| D-B | **Borrow from Open Notebook, then retire it — but ON stays running for podcasts until that feature is ported.** | Harvest content-core (docs + **images** + OCR), the **podcast** flow (deferred), and source/notes/notebook UX into the Quartz+OB1 stack. Full ON decommission waits for the deferred podcast phase ([§10](#10-relationship-to-iks--retiring-open-notebook)). |
| D-C | **Features, phased:** Provenance → Threads → Notes → Source Lifecycle → Import → Source-Grounding & Deliberate Linking → (deferred) Podcasts. | Each phase has its own ship gate. |
| D-D | **Sources are added as-is, editable with preserved history, and removed via a reversible global tombstone (retained, restorable) — not destroyed by default.** | An edit snapshots the prior into `source_revisions` (history kept). "Remove" = **retract** (set `retracted_at`; the row + content survive, invisible to all wiki generation, restorable). Irreversible **purge** (`DELETE`) is a rare, explicit operator escape hatch. Per-notebook **unlink** is a separate membership flip. (P4) |
| D-E | **User notes are the freely editable, additive layer**, written into the Open Brain DB. | The tethered-notes mechanism, surfaced with an in-Quartz editor. (P3) |
| D-F | **Images are first-class** — Quartz must display them; ingestion must extract/accept them. | Comes with the content-core borrow + Quartz vault-asset handling + a widened `content_type`. (P5) |
| D-G | **Notebook = research group = ON notebook; M:N, non-exclusive.** "**Notebook**" is the user-facing noun (UI, pages, routes); the `threads`/`thread_sources` table keeps its name **internally**. One **notebook hub page** per notebook merges synthesis + sources + notes + triage; the slug is **pinned** (renameable name). | Schema already supports membership; the work is the Quartz surface + a pinned `slug` column + folding the old `topic/` synthesis into the hub. (P2, [§5](#5-notebooks--research-groups-the-core-organizing-axis)) |
| D-H | **Podcasts use the existing local TTS/STT service** at `host.docker.internal:8000/v1` (OpenAI-compatible, several voices; STT too). Settings via a **config panel mirroring ON's UI.** | No new TTS engine decision; STT also enables audio/video source ingestion. **Podcast phase is deferred** (large feature; ON covers it meanwhile). (P7) |
| D-I | **Storage direction: move off the current git-vault-on-volume toward a self-hosted git vault (later); Quartz 4 is the primary surface.** | Near-term: a separate `wiki-assets` volume for binaries; notes stay in the current vault but the roadmap target is a self-hosted git server. ([§9](#9-storage--the-vault-direction)) |
| D-J | **Wiki pages are generated from the entity graph (thoughts + sources + edges), not from sources alone — so sourceless pages are *expected* for thought-only entities, not a generator bug.** Treat grounding as a *surfaced state* fixed by user-driven **deliberate source→page linking** + **upload-and-link**, and distinguish by-design sourcelessness from extraction-queue backlog. | Closes the operator-observed "many entries without sources" gap without suppressing graph-connectivity pages. ([§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap), P6) |

Spine unchanged: Quartz already mimics Obsidian and is *already wired directly to
Open Brain*, so this is feature development + borrowed implementations, not a new
architecture.

---

## 1. Where we are starting from

### 1.1 What Quartz is here (today)

Read-only `openbrain-wiki-viewer` — Quartz `v4.5.1`, fetched at image-build,
serving static HTML it rebuilds from markdown:

- [OB1/docker/wiki-viewer/Dockerfile](OB1/docker/wiki-viewer/Dockerfile) — pins `QUARTZ_REF=v4.5.1`, `npm ci`.
- [OB1/docker/wiki-viewer/entrypoint.sh](OB1/docker/wiki-viewer/entrypoint.sh) — symlinks `/wiki` → `/quartz/content`, patches `ignorePatterns`, then `npx quartz build --serve --port 8080`.
- [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — on `obnet` + `app-net`, `127.0.0.1:8812:8080`, public via the Caddy/Tailscale portal.

Vault = a **git repo on the `openbrain-wiki-data` volume**, two ownership layers:
- **`content/`** — **compiler-owned**, regenerated every compile, never hand-edited: entity pages (+ P1 `thought/`/`source/` leaves), `entities.md`, `graph.json`, and — post-P2 — the **notebook hub pages** (`content/notebook/<slug>.md`, which absorb the old `topic/` synthesis).
- **`notes/`** — the **authored** layer the compiler is forbidden to write ([wiki-service.mjs:132-140](OB1/docker/wiki-service/wiki-service.mjs#L132-L140)). Authored by **humans *and* AI assistants** (research pipelines, Open WebUI, other chat services emit notes here for the human to build on); each note tethers to one thought via `ingestNotes()`. Note provenance (`metadata.source = user_note | ai_note`, plus which agent) distinguishes them.
- **`index.md`** — vault-root home.

Quartz renders standard markdown, so **images display once they're in the vault** — the gap is nothing puts them there yet (D-F).

### 1.2 What Open Brain already gives us (the foundation)

| Capability | Where | State |
|------------|-------|-------|
| `sources` (url, title, content, content_type, tags, notebook, embedding `VECTOR(1024)`, metadata) | [init-sources.sql](OB1/docker/init-sources.sql) | ✅ |
| `find_or_create_source()` — dedup on url/content-hash | [init-threads.sql](OB1/docker/init-threads.sql) | ✅ |
| **`threads`** (research groups) + **`thread_sources`** (M:N, link_type auto/suggested/deliberate, status confirmed/pending/hidden/inactive) + `sessions` + `session_sources` | [init-threads.sql](OB1/docker/init-threads.sql) | ✅ — **this is the notebook model already** |
| `link_source_to_thread()` (additive upsert) + `set_thread_source_status()` (soft flips, never deletes) | [init-threads.sql](OB1/docker/init-threads.sql) | ✅ — add/subtract primitives |
| `source_extraction_queue` + fingerprint-gated auto-enqueue trigger | [init-source-graph.sql](OB1/docker/init-source-graph.sql) | ✅ |
| Entity worker → `source_entities` (`ON DELETE CASCADE`) | `openbrain-entity-worker` | ✅ |
| Cross-thread link suggestions (status `pending`) | `openbrain-suggestion-worker` | ✅ — feeds triage UI |
| Embeddings `bge-m3` 1024-dim via `llama-cpp-embed`; `match_sources()` RPC | MCP server / [init-sources.sql](OB1/docker/init-sources.sql) | ✅ |
| Wiki compiler: change-watch (3-min debounce), notes ingest, **orphan sweep**, `POST /recompile` | [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ |
| **Tethered notes** — `notes/<notebook>/file.md` ⇄ one thought via `metadata.note_path`, diff-based upsert/delete + extraction enqueue | `ingestNotes()` in [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ — basis for P3 |
| **Local TTS + STT** (OpenAI-compatible, several voices) | `host.docker.internal:8000/v1` | ✅ — basis for P7 + audio ingest |

### 1.3 The gaps this plan closes

1. No browser write path (upload, note authoring, source edit/remove, thread membership, podcast request).
2. PDF/DOCX/PPTX text + **image** extraction is a stub.
3. Provenance isn't surfaced on pages.
4. **Notebooks have no Quartz surface** — no index, no hub page, no membership UI (the core organizing axis is invisible).
5. No source edit-with-history or removal path.
6. **Many wiki pages have no sources, and there's no way to fix that from the UI** — see [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap); no user path to link an existing source, or upload+link one, as a hint for the next generation.
7. No podcast capability in-stack (deferred; ON covers it for now).

### 1.4 Why wiki pages appear without sources (the grounding gap)

The operator observed many wiki entries with no sources, which seems wrong if
"wikis are generated from sources." Reading the generator settles it: **pages are
generated from the *entity graph*, not from sources directly.**

- **Candidate selection** ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) `listBatchCandidates`, ~L302–335): an entity gets a page if it has ≥ `WIKI_BATCH_MIN_LINKED` links — counting **`thought_entities` first, then `source_entities`**. So thought mentions alone qualify an entity.
- **Source attachment** (`fetchLinkedSources`, ~L435–459, gated by `--include-sources` + `WIKI_MAX_SOURCES`): sources are joined via `source_entities!inner`. If an entity has **zero `source_entities` rows**, the page renders with **no `## Sources` section** (the system prompt omits it when nothing was cited).

So a sourceless page arises three ways:

1. **Thought-only entity — *by design, not a bug*.** The entity was mentioned in a captured thought (`capture_thought`) but in no source document → `thought_entities > 0`, `source_entities = 0`. The page exists so cross-entity `[[wikilinks]]` and graph nodes resolve; it legitimately has no sources. *This reads as "ungrounded" and is what the operator is mostly seeing.*
2. **Extraction backlog / worker failure — *operational bug*.** Sources exist but `source_extraction_queue` rows are stuck `pending`/`started` or carry `last_error`, or the pre-compile drain ([wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) `drainWorkerQueues`, ~L280) gave up (worker unreachable). The page compiles *before* its sources are linked.
3. **Misconfiguration — *guard against regression*.** `--include-sources` not passed or `WIKI_MAX_SOURCES=0`. The service currently passes the flag ([wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) ~L589/L594), so this is only a watch-for-drift case.

**Implication:** "fix sourceless pages" is two jobs — (a) *distinguish* by-design (1) from backlog (2) via queue health, and (b) give the user a way to **deliberately attach a source** (existing or freshly uploaded) so thought-only pages become grounded on the next compile. Both are [Phase 6](#phase-6--source-grounding--deliberate-wiki-linking-d-j).

---

## 2. Target architecture

### 2.1 The unavoidable backends

```
        ┌──────────────────── Quartz viewer (static + hydrated) ─────────────────────┐
        │ ProvenancePanel  NotebookIndex/NotebookPage  MembershipPicker  NotesEditor  │
        │ SourceEditor(versioned)  SourceRetractor  SourceLinker  ImportDropzone      │
        └───────────────┬─────────────────────────────────────────────────────────────┘
                        │  fetch()  (same-origin /workbench/* via Caddy)
                        ▼
        ┌──────────── openbrain-workbench  (Deno+Hono, :8814) ───────────────────────┐
        │ provenance · sources CRUD+versioning+retract · threads & membership ·       │
        │ notes read/write+commit · import jobs · [podcast jobs*]                     │
        └──────┬───────────────────────────┬──────────────────────────┬──────────────┘
               ▼                            ▼                          ▼
   openbrain-extract (Py/FastAPI)   host.docker.internal:8000   OB1 Postgres
   content-core: docs+images+OCR    TTS (podcast*) + STT(audio)  sources · source_revisions(NEW)
        │                                  │                      source_chunks(NEW) · threads
        ▼                                  ▼                      thread_sources · podcasts(NEW*)
   markdown + extracted images        audio/transcripts          │ change-watch + queue trigger
        └────────────► OB1 sources + wiki-assets volume ◄─────────┘
                                       │
                          openbrain-wiki recompile → Quartz file watcher reloads
```

`*` = deferred podcast phase (P7).

> **Provenance read is not workbench-backed (Phase 1 redesign).** The `provenance` item in the workbench box and the `ProvenancePanel` `fetch()` arrow above are superseded *for read*: P1 serves provenance as **static `thought/<id>` & `source/<id>` leaf pages** emitted at compile and reached via ordinary wikilinks (native popover + SPA). The workbench's provenance role shrinks to the **optional** `ProvenancePanel` summary; its load-bearing jobs remain the **write** paths (sources CRUD/versioning, threads, notes, import) plus the P6 deliberate-link write. See [§4 Phase 1](#4-feature-phases).

- **`openbrain-workbench`** (Deno+Hono) — browser-facing read/write API, kept **off** the MCP server + cloud-gateway (limited to 8 tools) so the multipart/auth surface is isolated. **Port/convention (audit):** every OB1 service listens on internal `PORT=8000` and (optionally) publishes a distinct loopback host port; follow that — internal `:8000`, optional debug publish `127.0.0.1:8814:8000`. The `:8814` used throughout this doc denotes that **host** debug port, not a second internal port. Networks: **`obnet` + `llm-net` + `app-net`** (app-net is required for portal-Caddy reachability — see [§2.3](#23-routing-exposure-auth)). **DB access:** for atomic multi-row writes (import = source + chunks + links in one unit) the workbench should talk to `openbrain-db` **directly via deno-postgres with transactions** — mirroring `openbrain-suggestion-worker` ([docker-compose.yml:299-325](OB1/docker/docker-compose.yml#L299-L325)) — rather than firing several non-atomic PostgREST calls through `openbrain-rest`. Read paths may still use PostgREST.
- **`openbrain-extract`** (Python/FastAPI) — wraps `content-core`; `POST /extract` → `{ markdown, title, metadata, pages, images[] }`; OCR for scans/images.
- **Existing TTS/STT** at `host.docker.internal:8000/v1` — STT used at import for audio/video sources (P5); TTS for podcasts (P7, deferred). No new container until P7 (and even then likely just a thin caller, not a TTS engine).

### 2.2 Asset handling (images now; audio later)

New **`wiki-assets`** volume for binaries Quartz serves statically (decouples
binaries from the git vault per D-I):

- **Images:** extraction returns embedded images → workbench writes `assets/<source-id>/img-n.png` and rewrites source markdown to `![alt](assets/<source-id>/img-n.png)` → Quartz renders inline. Standalone image upload → a `source` (`content_type='image'`, content = OCR/caption text for embedding) with the image as its asset.
- **Audio (P7):** `assets/podcasts/<id>.mp3`, streamed by the player.
- Confirm Quartz asset config in the overlay so `assets/` is served but not paginated.

### 2.3 Routing, exposure, auth

**⚠️ Corrected against the live Caddy config (audit).** The wiki is fronted by the **portal** Caddy on its own subdomain `wiki.{$PUBLIC_DOMAIN}` ([config/caddy/Caddyfile:210-257](config/caddy/Caddyfile#L210-L257)), which `reverse_proxy openbrain-wiki-viewer:8080`. The OB1 [Caddyfile](OB1/docker/Caddyfile) is **only** the internal PostgREST `/rest/v1` proxy (`openbrain-rest`, port 80, obnet) — it does **not** front the viewer, so adding `/workbench/*` there does nothing for same-origin browser routing. The earlier "both Caddyfiles" instruction was wrong.

- **Same-origin route:** add `handle_path /workbench/* { reverse_proxy openbrain-workbench:8000 … }` to the **`wiki.{$PUBLIC_DOMAIN}` block in [config/caddy/Caddyfile](config/caddy/Caddyfile)** (above the catch-all `reverse_proxy` to the viewer) **and** the equivalent Tailscale `serve` path ([recipe](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\tailscale-serve-restore-recipe.md)). `/workbench/*` is a distinct prefix from Quartz's root-relative assets, so it coexists on the same subdomain cleanly.
- **Network reachability:** for the portal Caddy to resolve `openbrain-workbench` by name it **must join `app-net`** (the external `ai-stack_app-net`), exactly as `openbrain-wiki-viewer` does ([docker-compose.yml:417-423](OB1/docker/docker-compose.yml#L417-L423)). So the workbench's networks are **`obnet` + `llm-net` + `app-net`** — the P0.1 "obnet+llm-net" list is incomplete.
- **Upload body cap:** the wiki subdomain currently enforces `request_body { max_size 1MB }` ([config/caddy/Caddyfile:216-218](config/caddy/Caddyfile#L216-L218)). Every P5 import (PDF/DOCX/PPTX/image/audio) exceeds 1 MB, so the `/workbench/import` route needs its **own raised `request_body` cap** (e.g. a `@import` matcher with `max_size 100MB`) — and the workbench must independently enforce its own ceiling.
- **Auth — secret stays server-side (audit):** the subdomain is already gated by **Authelia `forward_auth`** ([config/caddy/Caddyfile:220-231](config/caddy/Caddyfile#L220-L231)), so the browser is an authenticated operator before any `/workbench` call. A static Quartz page **cannot safely hold a bearer** — embedding `MCP_ACCESS_KEY` in client JS leaks it. So **do not** send the shared secret from the browser. Instead, let Caddy **inject** the shared secret server-side when proxying to the workbench (`header_up X-Brain-Key {$WORKBENCH_KEY}`), mirroring how `openbrain-rest` strips/rewrites auth headers ([OB1/docker/Caddyfile:14-17](OB1/docker/Caddyfile#L14-L17)). The workbench trusts that header on `app-net` and is never host-published. (Reusing the `MCP_ACCESS_KEY` *value* is fine; the correction is *where it lives* — Caddy, not client code.)

### 2.4 The visibility loop (no new sync)

Writes land in OB1 / the vault → existing `source_extraction_queue` trigger +
wiki change-watch (3-min debounce) recompile → Quartz reloads. Removal reuses the
compiler's **orphan sweep**.

### 2.5 Quartz customization model

Layer, don't fork: `OB1/docker/wiki-viewer/quartz-overlay/` (`*.tsx`, `*.inline.ts`,
patched `quartz.layout.ts`/`quartz.config.ts`) `COPY`'d over the pinned clone after
`git clone`, before `npm ci`. Keeps `QUARTZ_REF` upgradeable.

---

## 3. Cross-cutting foundations (Phase 0)

- **P0.1** `openbrain-workbench` skeleton (Deno+Hono, internal `:8000`, health, Caddy-injected `X-Brain-Key` trust, networks `obnet`+`llm-net`+`app-net`, optional debug publish `127.0.0.1:8814:8000`) → compose **+ recovery scripts + stack-map**.
- **P0.2** Caddy `/workbench/*` proxy in the **`wiki.{$PUBLIC_DOMAIN}` block of [config/caddy/Caddyfile](config/caddy/Caddyfile)** + the Tailscale `serve` path, same-origin; raised `request_body` cap on the import sub-route; Caddy injects the shared secret (see [§2.3](#23-routing-exposure-auth)).
- **P0.3** Quartz overlay scaffold + asset config; no-op component proving overlay + client `fetch()` to `/workbench/health`; confirm `assets/` served, not paginated.
- **P0.4** `wiki-assets` volume wired to workbench + viewer.
- **P0.5** Shared TS types for source/thread/membership/provenance shapes mirrored from [init-sources.sql](OB1/docker/init-sources.sql)/[init-threads.sql](OB1/docker/init-threads.sql).

**Gate:** a custom component, served through the portal, calls the authed API and
renders; an image under `assets/` renders in a page. No `sources` writes yet.

---

## 4. Feature phases

### Phase 1 — Provenance: Source **and** Thought Visibility
**Goal:** every inline citation on an entity/topic page — both **`[S:id]` external sources** *and* **`[#id]` captured thoughts** — behaves like every other link in the wiki: **hover → native popover preview, click → SPA navigation** to a read page for that record. The mechanism is to make each citation a *real internal link* to a compiled **leaf page**, so Quartz's built-in popover + SPA — plus graph nodes, backlinks, and full-text search — all apply with **no custom interaction code**.

Both citation forms come from the generator's system prompt ([generate-wiki.mjs:556-566](OB1/recipes/entity-wiki/generate-wiki.mjs#L556-L566)): **`[#id]`** = a row in `thoughts` (the user's own captured record, linked via `thought_entities`); **`[S:id]`** = a row in `sources` (an external document, linked via `source_entities`). Treated symmetrically. Because a **thought-only page** ([§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap), the majority of pages) has **no `[S:id]` sources at all**, the `[#id]` half is the higher-value one — for those pages it *is* the entire provenance trail.

> **Page-class note (reconciles [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap)):** these leaf pages are a *distinct page class* — read-only provenance records, **not** entities. They don't enter the entity graph or candidate selection, so the "pages come from the entity graph" model is untouched; this only gives each cited record a viewable address.

- **Compiler (the load-bearing change — [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) + [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs)):**
  - **Emit bounded leaf pages** — `content/thought/<id>.md` and `content/source/<id>.md`, **only for ids actually cited** this compile (collected from the `provenance.linked_ids`/`semantic_ids`/`source_ids` sets the generator already builds, [generate-wiki.mjs:542-544](OB1/recipes/entity-wiki/generate-wiki.mjs#L542-L544)). Bounded by *citations*, not the DB — 800 cited thoughts → 800 leaves, not 50k.
  - **Batch-fetch full content** by id (`thoughts?id=in.(…)` / `sources?id=in.(…)`) for the leaf bodies — the synthesis payload only carries 300-char snippets. Leaf frontmatter: `type: thought|source`, date, `metadata.type`; sources add `url`/`title`/`content_type`/`notebook`.
  - **Rewrite inline citations into wikilinks** — post-process generated pages: `[#11173]` → `[[thought/11173|#11173]]`, `[S:<uuid>]` → `[[source/<uuid>|S:<uuid>]]`. An id with no emitted leaf (uncited / model mis-cite) is **left as plain text**, mirroring broken-`[[wikilink]]` handling. The citation now *is* a wikilink — same object, same behavior as the rest of the wiki.
    - **⚠️ Id-shape reality (audit):** `thoughts.id` is **`BIGSERIAL`** ([init.sql:9](OB1/docker/init.sql#L9)) — small integers an LLM reproduces reliably, so `[#id]` rewriting is robust. **`sources.id` is `UUID`** ([init-sources.sql:23](OB1/docker/init-sources.sql#L23)) — the example `[S:4521]` is fictitious; a real source citation is `[S:a1b2c3d4-e5f6-…]`. Two consequences the rewrite must handle: (1) the citation regex must match a **36-char UUID with hyphens**, not `\d+`; (2) an LLM transcribing a full UUID verbatim has a high error rate, so the `[S:…]`→plain-text fallback will fire often and source-leaf coverage will be lossy. **Mitigation:** in `buildSynthesisInput`/`synthesize`, present sources to the model under a **short stable per-page token** (e.g. `S1,S2,…` mapped to the real UUIDs in the structure payload) and resolve the token→UUID during the deterministic rewrite, rather than asking the model to echo UUIDs. Thought ids stay as-is.
  - **Orphan-sweep — dedicated leaf sweep, NOT the existing entity sweep (audit — data-loss bug otherwise):** the current `sweepOrphanEntityPages` ([wiki-service.mjs:457-506](OB1/docker/wiki-service/wiki-service.mjs#L457-L506)) builds its kept-set from **entity slugs only** and `listEntityFiles` ([wiki-service.mjs:424-450](OB1/docker/wiki-service/wiki-service.mjs#L424-L450)) walks **every** `content/<dir>/` except `topic/`. So if leaves land in `content/thought/` and `content/source/`, the existing sweep would delete **every leaf on the very next compile** (no leaf id is in the entity kept-set). Required: (a) add `thought/` and `source/` to the `listEntityFiles` skip-list exactly as `topic/` is skipped, and (b) add a **new `sweepOrphanLeafPages`** keyed on the set of ids actually cited this compile (the union of `provenance` id sets across all generated pages), removing only leaves whose id is no longer cited. "Reuse the existing sweep" is incorrect.
  - **Untrusted-content guard** — leaf bodies render captured / external text: keep the scrub ([generate-wiki.mjs:597-610](OB1/recipes/entity-wiki/generate-wiki.mjs#L597-L610)) and rely on Quartz's markdown→HTML sanitization; this text is untrusted at *render* time, not just as LLM input.
- **Quartz (mostly native — no custom interaction component):**
  - Hover-popover + click-navigate come **free** from stock Quartz (this v4.5.1 build runs SPA + popovers). Citations also gain graph nodes, **backlinks** ("which wiki pages cite this record"), and full-text search automatically.
  - **Leaf-page template** — a small overlay layout keyed on `type: thought|source` (metadata header + body + backlinks) so leaves read as records, not orphans.
  - **`ProvenancePanel.tsx` is now optional** — a consolidated per-page provenance index on top of the generator's existing `## Sources` section; a nice-to-have, no longer load-bearing since inline links + backlinks already deliver traceability.
- **Backend (read) — not required in P1:** the static leaf pages serve the read view, so P1 needs **no** workbench endpoint on its hot path. `GET /workbench/thoughts/:id` / `…/sources/:id` are still built for the **write/live** phases (P4 edit, P6 linking) but aren't a P1 dependency — **P1 is essentially a compiler-only feature.**
- **Gate:** open a **thought-only** page → its `[#id]` markers are real links → hover shows a native popover of the captured thought, click navigates to its `thought/<id>` leaf (showing "cited by" backlinks); a **sourced** page → `[S:id]` does the same to a `source/<id>` leaf; an uncited/unknown id stays plain text (no broken link); after a citation is removed, the next compile sweeps the now-orphan leaf. Behavior is indistinguishable from any `[[wikilink]]`.

### Phase 2 — Notebooks & Membership (research groups)
*The core organizing axis — see [§5](#5-notebooks--research-groups-the-core-organizing-axis) for the full design.*
**Goal:** surface **notebooks** (user-facing name for `threads` rows) as the organizing layer — a notebook index, one **notebook hub page** per notebook, and add/subtract membership (M:N, non-exclusive).

- **Naming:** user-facing surfaces (pages, UI labels, routes) say **"Notebook"**; the persistence layer keeps `threads`/`thread_sources` (a renaming of a live table with a suggestion worker + sessions FK pointed at it buys nothing). One documented seam: *a Notebook **is** a `threads` row.*
- **Schema (additive — pin the slug):** `ALTER TABLE public.threads ADD COLUMN IF NOT EXISTS slug TEXT;` + `CREATE UNIQUE INDEX IF NOT EXISTS uq_threads_slug ON public.threads(slug);`. The workbench generates the slug **once at create time** (shared slug module §14.1), de-collides on the `UNIQUE` violation (`-1/-2`, mirroring [`resolveOutputPath`](OB1/recipes/entity-wiki/generate-wiki.mjs#L815)), and **never recomputes it** — rename touches `name` only, and the hub page emits `aliases: [name]` so `[[Notebook Display Name]]` keeps resolving. (Same pin-for-life contract entity `wiki_slug` has.)
- **One hub page per notebook (folds in the old `topic/`):** the compiler emits `content/notebook/<slug>.md` carrying **(1)** a `## Synthesis` section — the existing notebook synthesis ([synthesize-notebooks.mjs](OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs)) writes *here* instead of a separate `topic/<slug>.md`; **(2)** `## Sources` (`thread_sources.status=confirmed`, with P1 provenance); **(3)** `## Notes` listing/linking `notes/<slug>/*`; **(4)** a `## Suggestions` triage strip; plus a scoped graph + backlinks. This is the discovery hub — opening it surfaces the user's own `notes/<slug>/` folder, and every note backlinks `[[notebook/<slug>]]` (see [§5.2](#52-how-notebooks-appear-in-quartz)).
- **Backfill (unifies the two notebook populations):** for every distinct `metadata.notebook` string on a source/thought with **no matching `threads` row**, the compiler auto-creates one (slug pinned). Guarantees every notebook — whether born from a research run's free-text tag or a user-created `notes/` folder — has exactly one discoverable hub. No hidden parallel notebooks.
- **Backend:** notebook CRUD (`GET/POST/PATCH /workbench/notebooks` → `threads`), `POST /workbench/notebooks/:id/sources` (link via `link_source_to_thread`), `DELETE` (unlink via `set_thread_source_status → hidden`), suggestion triage (`accept`/`hide`).
- **Compiler:** generate `content/notebook/<slug>.md` per active notebook from `threads` + `thread_sources(status=confirmed)` + backfill; notebook nodes into `graph.json`. Add `notebook/` to the `listEntityFiles` sweep skip-list (it has its own kept-set, like `topic/` did).
- **Quartz:** `NotebookIndex.tsx`, `NotebookPage.tsx`, `MembershipPicker.tsx` (+ `.inline.ts`); backlinks/graph leveraged (see §5).
- **Gate:** create a notebook (slug pinned), rename it (page + links survive), add a source from two notebooks (proves non-exclusive), unlink from one (still in the other), accept a worker suggestion, and confirm a hand-created `notes/<slug>/` folder shows under the hub's `## Notes` — all reflected after recompile.

### Phase 3 — Notes System (authored layer: human + AI — D-E)
**Goal:** author Obsidian-style notes **in Quartz** (live preview, `[[wikilinks]]`, tags, notebook grouping), written additively into OB1 — and accept notes **emitted by AI assistants** (research pipelines, Open WebUI, other chat services) into the same layer for the human to build on.
- **Reuse:** `ingestNotes()` already maps `notes/<notebook>/file.md` ⇄ a thought; we add the **browser editor that writes those files**. Align the `notes/<notebook>/` folder = the pinned **notebook slug** so notes group under their notebook hub ([§5](#5-notebooks--research-groups-the-core-organizing-axis)).
- **AI notes from Open WebUI research chats (the concrete case):** OWUI conversations are where much of the research that *derives sources* happens; the **synthesized response of a research chat is an AI note on its subject**. P3 lets those synthesized responses land as `ai_note`s **attached to the relevant notebook** (`notes/<notebook-slug>/…`), so a notebook accumulates both the user's hand notes and the assistant's synthesis on that subject. (A small OWUI→workbench `PUT /workbench/notes` hand-off writes the synthesis into the notebook folder; the existing notes ingest tethers it.)
- **Provenance:** stamp `metadata.source = user_note | ai_note` (+ originating agent/chat for AI notes) so the hub and search can distinguish human vs assistant authorship; both tether to thoughts identically.
- **Backend:** `PUT/GET /workbench/notes/<path>` (path validated under `notes/` — one shared no-`../`-escape validator per §14.3, write+`git commit`, optimistic concurrency via content-hash/`If-Match`); notes index. AI-emitted notes use the **same write path** (one ingestion surface, not a parallel one).
- **Quartz:** `NotesEditor.tsx` + `.inline.ts` — editor, `[[…]]` autocomplete from `entities.md` + the notebook index (so authors pick an **existing** notebook rather than fat-finger a near-duplicate slug), tags.
- **Decision (idea-doc Q1):** notes stay in the `notes/` layer tethered to thoughts — not a separate `user_notes` collection.
- **Gate:** create/edit a note (human and AI-authored) → appears in vault under its notebook, links resolve, next compile tethers + extracts and the note shows under the notebook hub's `## Notes`; two-session conflict detected.

### Phase 4 — Source Lifecycle: Edit-with-history + Retract/Restore (D-D)
**Goal:** sources are added as-is but the user can **edit them (history preserved)** and **remove them reversibly (retain + restore)** — removal must not contaminate future wiki generation, but the data is kept so the user can bring a source back later.
- **Edit, versioned:** an edit snapshots the prior content into an append-only **`source_revisions(source_id, revision, content, title, edited_at, edited_by)`**, then updates `sources.content` to the new version (current = head; history = revisions). Re-embed is automatic via the existing fingerprint-gated queue trigger; metadata-only edits must not bump the content fingerprint. The source view shows version history + diff.
- **Three distinct removal verbs (keep them visually separate so the user never confuses scope):**
  1. **Unlink from notebook** (membership, per-notebook) — `set_thread_source_status → hidden`. Source stays in its other notebooks and in generation. *Not* a deletion.
  2. **Retract** (global, **reversible — the default "remove"**) — additive `sources.retracted_at TIMESTAMPTZ` + `retracted_by TEXT`. The **row and content are retained and restorable**, but the source becomes **invisible to all wiki generation/linking** so it can't contaminate future generations. **Restore** = clear `retracted_at`; its `source_entities`/`thread_sources` rows are still intact, so it lights straight back up.
  3. **Purge** (global, **irreversible**) — `DELETE FROM sources` → `source_entities`/`thread_sources`/`session_sources`/`source_revisions`/`source_chunks` cascade → orphan sweep removes unsupported pages + assets. A rare, explicit operator escape hatch, **not** the normal remove.
- **⚠️ Tombstone filtering — every generation read-path must exclude `retracted_at IS NULL` (audit; miss one and tombs resurface):**
  - `fetchLinkedSources` ([generate-wiki.mjs:435](OB1/recipes/entity-wiki/generate-wiki.mjs#L435)) — the main source→page join
  - `listBatchCandidates` source-count ([generate-wiki.mjs:324](OB1/recipes/entity-wiki/generate-wiki.mjs#L324)) — so a tomb can't keep a page alive
  - `match_sources` + new `match_source_chunks` RPCs ([init-sources.sql:78](OB1/docker/init-sources.sql#L78)) — `AND s.retracted_at IS NULL`
  - notebook synthesis source pulls ([synthesize-notebooks.mjs](OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs))
  - P1 source-leaf emission + the leaf-sweep (a retracted source's `source/<uuid>` leaf is swept)
  - the extraction queue (don't re-extract a tomb — it must stop producing fresh `source_entities`)
  - *This list is the P4 regression-test checklist.*
- **Backend:** `PATCH /workbench/sources/:id` (versioned edit), `GET …/revisions`, `POST …/:id/retract {scope: notebook|global}` (notebook→status flip, global→`retracted_at`), `POST …/:id/restore`, `DELETE …/:id` (purge, operator-confirmed).
- **Quartz:** `SourceEditor.tsx` (inline editor + version history/diff), `SourceRetractor.tsx` (unlink-from-notebook vs retract-globally vs purge, confirm dialog showing affected pages/links; default to retract, purge gated behind explicit confirm).
- **Gate:** edit → new revision recorded, old preserved, re-embed enqueued, dependent pages refresh; **retract → the source vanishes from every generation read-path but the row survives; restore → it returns with links intact**; purge cascades + sweeps; purge always needs explicit confirmation.

### Phase 5 — Direct Source Import Pipeline (incl. images — D-F)
**Goal:** drag-and-drop / picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT, **images** (and **audio/video** via STT) → extract → chunk → embed → land as first-class sources, linked to a chosen thread, with progress + errors; images render in Quartz.
- **`openbrain-extract` sidecar (correctness-first, all formats — §12.3) — built to expand:** stable `POST /extract` → `{ markdown, title, metadata, pages, images[] }` over a **format→extractor registry** (PDF: PyMuPDF/Docling-class; DOCX/PPTX: python-docx/-pptx; images: Pillow+Tesseract OCR; audio/video: STT at `host.docker.internal:8000/v1`; content-core where it extracts most faithfully). The registry shape matters: **a future format (epub, html, eml, spreadsheets…) is a new registry entry behind the unchanged `/extract` contract, not a caller change** (the import pipeline, workbench, and Quartz never learn about new formats). **Per-format extraction-quality acceptance gate** (text fidelity, tables, headings, image refs). Add to compose **+ recovery + stack-map**.
- **Workbench `POST /workbench/import` (async, transactional):** store upload → extract → write images to `assets/<source-id>/` + rewrite refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(..., 'deliberate')` to the selected notebook → `job_id`; `GET /workbench/jobs/:id` for progress. Source + chunks + links land in **one deno-postgres transaction** (§14.5) so a mid-sequence failure can't leave a source with no chunks/links.
- **Schema (additive — §12.2):** a `content_types` reference table, **`source_chunks`**, a chunk search RPC, and durable **`import_jobs`**:
  - **`content_type` → reference table (not a CHECK), for expandability.** Replace the inline CHECK ([init-sources.sql:27-30](OB1/docker/init-sources.sql#L27-L30)) with `content_types(value TEXT PRIMARY KEY, label TEXT, category TEXT, created_at TIMESTAMPTZ DEFAULT now())` and an FK `sources.content_type → content_types(value)`. A new format becomes **one `INSERT`, no DDL**. Migration order on the **live** DB matters: create table → **seed every value already in `sources`** (the existing 7) **+ the new ones** (`docx,pptx,image,audio,txt,md`) → drop the old CHECK → add the FK (FK-add fails if any existing value is unseeded). `find_or_create_source`'s `'web_article'` default stays valid.
  - **`source_chunks(source_id UUID, idx INT, content TEXT, embedding VECTOR(1024), PRIMARY KEY(source_id, idx))`** (confirmed — long-doc retrieval *and* the source list podcasts build from), `source_id … REFERENCES sources(id) ON DELETE CASCADE`. Search RPC named **`match_source_chunks`** (don't overload `match_sources` — [init-sources.sql:78](OB1/docker/init-sources.sql#L78)).
  - **`import_jobs(id, status, source_id, target_entity_ids, target_notebook, error, created_at, updated_at)`** (§14.4) — persists job state so a workbench restart doesn't orphan in-flight imports, backs `ImportStatus.tsx` history, and (per P6) records the **target links + terminal error** of an upload-and-link / grounding attempt for the later alerts surface.
- **Quartz:** `ImportDropzone.tsx` (validation, per-file progress, errors) + `ImportStatus.tsx`; notebook selector reuses `MembershipPicker`.
- **Gate:** drop a PDF, DOCX, PNG (and an MP3) → all extract/transcribe, chunk, embed, dedupe, link to the chosen notebook, entity-extract, surface via P1 provenance and on the P2 notebook hub; images render inline; corrupt files fail clearly.

### Phase 6 — Source Grounding & Deliberate Wiki Linking (D-J)
*Closes the [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap) gap. Builds on P1 (provenance), P4 (source edit options), P5 (import).*

**Goal:** turn a **thought-only page** (a "mental model" resting solely on the
user's captured beliefs) into a **source-grounded entity** by attaching a source
*from the page being read*, regenerating it so source-backed facts carry the
claims and the prior beliefs are reframed as unverified.

> **Conceptual frame (so the mechanism is unambiguous):** a wiki page is an
> **entity** synthesized from many thoughts/edges/sources — not a single thought.
> "Grounding" attaches a source to **that entity**; regeneration re-synthesizes the
> whole page, now able to assert source-backed facts and demote the earlier
> belief. *Example:* "Project Aurora" is thought-only and says *"launches Q2"* (your
> belief). You ground it with the project doc (*"launches Q3"*); regeneration →
> the page asserts **"Launches Q3 [S:doc]"** and the *Q2* belief is shown as a
> superseded assumption. The **evolution** view records the transition.

- **Grounding state is LIVE, read at the moment of reading (hydrated, not baked):** because the user is reading the page *right now*, the badge must reflect the true current state, accurate between compiles. The in-Quartz component hydrates from the workbench and shows one of:
  - **"Mental model — thought-only"** (no sources; rests on beliefs),
  - **"⏳ Grounding pending"** (a source is linked/ingesting; page not yet regenerated),
  - **"Grounded by N source(s)"** (regenerated and citing ≥1 source),
  - **"⚠ Ingest failed"** (a grounding attempt failed — see failure handling below).
  - `GET /workbench/grounding` also surfaces `source_extraction_queue` health so a **backlog** page (§1.4 cause 2) is never mislabeled as **by-design** (cause 1).
  - Compiler policy: **badge** thought-only pages, never suppress them — they still carry graph value (D-J).
- **Ground-from-the-page (the core feature):** on the live wiki page the user sees **"Provide grounding with a new source"** and supplies **a document (upload) or a URL (ingest)**. The source is ingested (reusing the P5 import pipeline) and linked to **this page's entity** — a marked `source_entities` row (entity-level grounding; this is distinct from notebook-level `thread_sources` membership, which is P2). On success the entity is marked to regenerate; `fetchLinkedSources` includes the new source → the page **regenerates and flips to "Grounded."**
  - **Generation policy on a grounded page (decision: sources = facts, thoughts = demoted, not deleted):** once the page has ≥1 source, the regenerated page lets **sources carry the asserted facts** and reframes the thought-derived claims under a clearly-labeled *"Working hypotheses / unverified"* framing rather than dropping them. This honors "the original belief is a mental model no longer valid" **without** destroying information — important because thought-only entities (with possibly many thoughts) are the majority of pages (§1.4). *(Not the lossy "suppress thoughts entirely" variant.)*
  - **⚠️ Schema reality (audit):** `source_entities` ([init-source-graph.sql:27-35](OB1/docker/init-source-graph.sql#L27-L35)) has columns `source_id, entity_id, mention_role, confidence, evidence, created_at` and **PK `(source_id, entity_id)`** — there is **no `metadata` column**, so "plus a `metadata` flag" as written is impossible. Two additive fixes, both in scope under the additive-only guardrail:
    1. **Marker:** `mention_role='user_linked'`, `confidence=1.0`, `evidence='manual:<operator>@<iso8601>'`. Optionally `ALTER TABLE public.source_entities ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb` if a richer flag is wanted — but `mention_role` alone suffices and avoids a column.
    2. **PK-collision decision (must be stated):** because the PK is `(source_id, entity_id)`, a manual `user_linked` row and an auto `mentioned` row for the **same pair cannot coexist**. Policy: **`user_linked` wins** — the worker's re-extraction upsert must `ON CONFLICT (source_id,entity_id) DO NOTHING` (or merge) when the existing row is `user_linked`, never overwriting the manual mention_role back to `mentioned`.
- **Failure handling (ingest can fail — bad URL, unparseable doc):** on failure, **do not regenerate the entity** (nothing new to cite). Instead the live component marks the attempt on the page **client-side** (the "⚠ Ingest failed" state, hydrated — no recompile), and the failure is recorded durably in `import_jobs` (terminal `status=failed` + `error` + `target_entity_ids`). That captured data feeds a later **alerts/indicator surface** (a "grounding attempts that failed" list) — *capture now, surface later*; only a **successful** ingest triggers the entity's regeneration.
- **Evolution / timeline (P6b — derive, don't snapshot):** the entity's grounding history is **derived for free** from existing timestamps — each `source_entities.created_at` (when each grounding source attached) plus the entity's first-seen — rendered as a `## Evolution` section, complemented by the **vault's git history** (every compile is a commit, so each page already carries a full diff trail). **No new storage.** This works on **today's** vault git (the `openbrain-wiki-data` volume repo) **right now** — it does **not** depend on the self-hosted git server, which isn't built yet; when D-I later migrates the vault to a locally-owned git host ([§9](#9-storage--the-vault-direction)) the same derivation keeps working unchanged. *(Per-version page-body snapshots are deferred future scope.)*
- **Upload-and-link entry point:** the same flow is also reachable from the P5 `ImportDropzone` with a **"link to wiki page(s) / notebook"** target field, so a *new* source is ingested **and** linked to chosen entities/notebooks in one action.
- **Worker change (required):** the entity-extraction worker must **not delete `user_linked` `source_entities` rows** on re-extraction (see [§11](#11-risks--guardrails) for the concrete `index.ts:748` change + the `user_linked`-wins PK policy). Guard + test: a re-extraction cycle must leave the manual link intact.
- **Quartz:** `SourceLinker.tsx` (entity/page picker on a source view + in the editor), the live grounding-state badge + `## Evolution` in `ProvenancePanel`, "Provide grounding with a new source" on the entity page, "link targets" field in `ImportDropzone`.
- **Scope note:** entity pages first (the common case). Notebook-hub synthesis pages (e.g. autobiography) are generated differently — grounding for those is a follow-up.

**Gate:** from a thought-only page, "provide grounding" with a URL/doc → it
ingests, links to the entity, the page regenerates citing it with sources-as-facts
/ thoughts demoted, and the badge flips **Mental model → Grounded**; a **failed**
ingest shows "⚠ Ingest failed" on the page **without** recompiling it and lands a
`failed` `import_jobs` row; the manual link survives a re-extraction cycle; a
backlog page shows "⏳ pending", not a false "ungrounded"; the `## Evolution`
section shows the grounding transition.

---

### Phase 7 — Podcast Service (DEFERRED — D-B/D-H)
*Large feature; built later. **Open Notebook remains available and provides podcasts until this ships.*** Recorded here so the architecture leaves room.
- **Generation on request, per thread:** pull the thread's sources (via `source_chunks`) → script via local Qwen (`llama-cpp`) → audio via **existing TTS** at `host.docker.internal:8000/v1` (several voices) → `assets/podcasts/<id>.mp3` + transcript.
- **Schema (additive):** `podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)` — "organized by thread" for free.
- **Settings:** a **config options panel mirroring ON's UI** (speaker count 1–4, voice selection from the TTS service, style/length). Stored per-notebook or global default.
- **Transcript → note:** reuses the P3 notes write path (editable, tethered).
- **Quartz:** `PodcastPanel.tsx` — Podcasts section (global + per-notebook) with an `<audio>` player; "Generate podcast for this notebook"; "Save transcript as note".
- **Deferral note:** because ON covers podcasts meanwhile, no `openbrain-podcast` container is built in P1–P6. When P7 starts, prefer a thin caller of the existing TTS over a heavy `podcast-creator` dependency unless multi-speaker scripting needs it.

---

## 5. Notebooks / research groups — the core organizing axis

*Answers the operator's questions on how notebooks are organized and how membership is managed. "**Notebook**" is the user-facing noun; the backing table is `threads`/`thread_sources` (see [Phase 2](#phase-2--notebooks--membership-research-groups)).*

### 5.1 Model (already in the DB — D-G)

A **notebook = a research group = an ON notebook**: a named grouping of relatable
information, persisted as a `threads` row. Membership lives in `thread_sources` as
an **M:N join**, so a source is **non-exclusive** — it can sit in many notebooks at
once (linking, not ownership), exactly like ON. The rows, link primitives,
soft-status flips, and the cross-thread suggestion worker all already exist
([§1.2](#12-what-open-brain-already-gives-us-the-foundation)); the work is the
Quartz surface + a pinned `slug` column (Phase 2).

### 5.2 How notebooks appear in Quartz

One **notebook hub page** per notebook — `content/notebook/<slug>.md` +
`NotebookPage.tsx` — is the single surface that merges the previously-separate
`topic/` synthesis with membership/notes/triage:

- a `## Synthesis` section (the folded-in notebook synthesis, formerly `topic/<slug>.md`),
- its **sources** (`thread_sources.status=confirmed`) with P1 provenance,
- its **notes** — `notes/<slug>/…`, both human and AI-authored — listed/linked under `## Notes`,
- a **scoped graph** (notebook node + its sources/entities) and a **backlinks** panel — *this is where Quartz earns its keep*,
- a **suggestion triage** strip (pending cross-notebook links → accept/hide),
- later, its **podcasts** (P7).

Plus a **notebook index** (`NotebookIndex.tsx`) of all active notebooks in the nav.
Compiler generates the hub from `threads` + confirmed `thread_sources` (+ backfill);
client components hydrate live membership actions on top.

**Discovery — how the user finds that their `notes/<slug>/` folder and the
auto-generated hub are the same notebook** (the two live in different,
oppositely-owned folders by design — `content/` is compiler-owned, `notes/` is
author-owned, and the compiler may not write into `notes/`). The **shared slug**
is the identity binding them, surfaced four native ways: (1) the hub's `## Notes`
section enumerates `notes/<slug>/*`, so opening the hub shows the user's own notes;
(2) each note carries `[[notebook/<slug>]]`, so Quartz **backlinks** point home;
(3) the **graph** clusters hub + notes + sources/entities; (4) the notes editor's
notebook picker autocompletes from existing notebooks so authors select the
existing one instead of forking a near-duplicate slug. There is no "automated
notebook vs. my notebook" — one slug, one hub, aggregating everything that carries
it (backfill guarantees a hub even for legacy free-text notebook strings).

### 5.3 Add / subtract membership — options explored (operator's Q3)

| Option | How it works | Best for | Trade-off |
|--------|--------------|----------|-----------|
| **A — ON-style picker** | `MembershipPicker` on a source view / notebook hub; multi-select notebooks; calls `link_source_to_thread` (add) / `set_thread_source_status→hidden` (subtract). | **Sources** (read-mostly DB rows with no markdown body to link in). | Explicit, familiar, but a separate UI gesture. |
| **B — Obsidian-style wikilinks** | A **note** contains `[[notebook/<slug>]]`; the compiler materializes the membership and Quartz's backlinks/graph show it. Optionally a `notebook:` tag bulk-links. | **Notes** (real markdown files; native to Quartz). | Indirect for sources (you can't edit a wikilink into an immutable-ish source row), so not sufficient alone. |
| **C — Hybrid + triage (recommended)** | Picker for sources, wikilinks/tags for notes, **plus** the suggestion-worker proposing links (status `pending`) surfaced as a triage queue on the notebook hub (accept→confirmed, hide→hidden). | Everything. | Slightly more UI, but matches how each object type actually behaves and uses Quartz's graph/backlinks where they're strongest. |

**Recommendation: C.** Sources get the explicit picker; notes get Obsidian-native
`[[notebook/x]]` linking with the hub's backlinks panel as the live view of
membership; the suggestion worker fills the long tail and the user triages.
"Unlink from notebook" is always a **soft status flip** (the source stays in its
other notebooks) — categorically different from a global **retract** or **purge**
(P4).

### 5.4 Where Quartz is uniquely helpful here

Graph view (notebook + source/entity nodes), backlinks (every note referencing a
notebook), notebook-scoped full-text search, and `[[ ]]` autocomplete make
notebooks feel like Obsidian while membership stays in Postgres as the single
source of truth.

---

## 6. Design decisions — consolidated

| Question | Decision | Rationale |
|---|---|---|
| Notes vs sources storage | Notes (human + AI-authored) in `notes/<notebook-slug>/` tethered to thoughts; OWUI research-chat syntheses land as `ai_note`s on the notebook. | Isolated, reuses `ingestNotes()`, unifies notes↔notebooks. |
| Notebook = thread (naming) | User-facing noun **Notebook**; backing table stays `threads`. One **hub page** per notebook folds in the old `topic/` synthesis; slug **pinned**, name renameable; legacy free-text notebooks **backfilled** to rows. | "Notebook" reads better; renaming a live table buys nothing; one hub = discoverable. |
| Source editing/versioning (D-D) | **Editable; one canonical row (head) + append-only `source_revisions` history** — `source_id` never changes (§12.1). | Operator wants edits *with* history; stable id keeps thread links/search valid. |
| Source removal (D-D) | Three verbs: **unlink** (per-notebook status flip) · **retract** (global, reversible tombstone via `retracted_at`; retained + restorable; invisible to generation) · **purge** (irreversible `DELETE`, rare). Retract is the default. | Removed sources must not contaminate future generations but stay restorable; M:N means unlink ≠ remove. |
| Grounded-page generation policy (P6) | Sources carry asserted **facts**; thought-derived claims demoted to a labeled **"working hypotheses / unverified"** framing — not deleted. | Honors "belief is a superseded mental model" without losing the majority thought-only content (§1.4). |
| Entity evolution (P6b) | **Derived** from `source_entities.created_at` + vault git history; no new storage. Works on today's volume git; survives the D-I self-hosted-git migration. | Free timeline; doesn't block on the not-yet-built local git. |
| Re-embed | Automatic via fingerprint-gated queue trigger on content change; metadata-only edits don't bump fingerprint. | Already built; no threshold needed. |
| Import chunking + `source_chunks` (confirmed) | Semantic/sentence-boundary default, `bge-m3`-tuned; `source_chunks` table. | Long-doc retrieval **and** the source list podcasts build from. |
| Images (D-F) | content-core/Pillow extracts → `assets/` → Quartz renders inline; `content_type='image'` (a row in the new `content_types` table). | Closes the no-images gap with the ingestion borrow. |
| Audio/video sources | Transcribe via existing **STT** at `host.docker.internal:8000/v1`; `content_type='audio'`. | Reuses the local service; no new dependency. |
| `content_type` storage | **Reference table** `content_types` + FK (not an inline CHECK). | New formats = one `INSERT`, no DDL — import is built to expand. |
| Podcast TTS (D-H) | Existing local **TTS** at `host.docker.internal:8000/v1`; settings via ON-style config panel. **Deferred (P7).** | No engine decision needed; ON covers podcasts meanwhile. |
| Wiki grounding gap (D-J) | Pages come from the entity graph; thought-only pages are sourceless by design. Surface grounding state + **deliberate source→page linking** + **upload-and-link**; distinguish backlog via queue health. | Fixes the observed "no sources" gap without suppressing graph pages (§1.4, P6). |
| Notebooks (D-G) | Existing `threads`/`thread_sources` M:N + a new pinned `slug` column; Quartz adds the notebook index + hub pages + hybrid membership (§5, §12.4). | Membership schema ready; the surface + pinned slug are the work. |
| Write-API home / auth | New `openbrain-workbench`, not the MCP server; single operator bearer (§12.6). | Keeps browser/multipart/auth off the limited MCP + cloud-gateway contract. |
| Extraction engine | Python `openbrain-extract` with correctness-first per-format extractors behind a stable interface; quality acceptance gate per format (§12.3). | Faithful extraction of all listed formats over minimal footprint. |
| Quartz customization | `quartz-overlay/` COPY'd over the pinned clone. | Keeps `QUARTZ_REF` upgradeable. |
| Storage direction (D-I) | `wiki-assets` volume now; self-hosted git vault later; Quartz primary. | Decouple binaries from git; roadmap target = self-hosted git server. |

---

## 7. File / touchpoint index

- **New services:** `OB1/docker/workbench/` (Deno+Hono); `OB1/docker/extract/` (FastAPI+content-core). *(P7 later: a thin podcast caller, not necessarily its own heavy service.)*
- **Wiki generator + worker (P6):** [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) (honor `user_linked` source_entities; grounding state), entity-extraction worker (upsert without clobbering `user_linked` rows), [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) (queue-health endpoint feed).
- **Compose:** [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — add `openbrain-workbench`, `openbrain-extract`, `wiki-assets` volume, `host.docker.internal` reachability for STT/TTS.
- **Recovery (required):** `scripts/emergency-recovery.ps1` + `.bat` inventory + start/stop order.
- **Stack map (required):** [.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md).
- **Caddy:** [config/caddy/Caddyfile](config/caddy/Caddyfile), [OB1/docker/Caddyfile](OB1/docker/Caddyfile) — `/workbench/*` + `assets/`.
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` — leaf-page template (`type: thought|source` layout; native popover + SPA + backlinks do the interaction, no custom linkifier), `ProvenancePanel` (per-page provenance index + live grounding-state badge + `## Evolution`), `NotebookIndex`, `NotebookPage`, `MembershipPicker`, `SourceLinker`, `NotesEditor`, `SourceEditor`, `SourceRetractor`, `ImportDropzone`, `ImportStatus`, `[PodcastPanel]`, `.inline.ts`, layout/config.
- **Wiki compiler:** [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) + [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) — emit cited-only `thought/<id>.md` + `source/<uuid>.md` leaf pages (batch-fetch full content by id; UUID-aware), rewrite inline `[#id]`/`[S:…]` citations into wikilinks, **dedicated leaf-sweep** + `thought/`+`source/` added to the `listEntityFiles` skip-list (P1); **notebook hub generation** (`content/notebook/<slug>.md`, folding in `topic/` synthesis) + pinned-slug + backfill + graph (P2); notes write interplay (P3); **tombstone filtering** on every source read-path (P4); honor `user_linked` source_entities + grounded-page generation policy (P6).
- **Schema (additive only):** `threads.slug` pinned column + `uq_threads_slug` (P2); `source_revisions` (`init-source-revisions.sql`); `content_types` reference table + FK replacing the `content_type` CHECK, `source_chunks` + `match_source_chunks` (`init-source-chunks.sql`); `sources.retracted_at`/`retracted_by` retract columns (P4); `import_jobs` durable job table (P5/P6); `user_linked` rows in `source_entities` (P6, marked via `mention_role='user_linked'`); `podcasts` (P7).
  - **⚠️ Migration path (audit — two places, not one):** `/docker-entrypoint-initdb.d` scripts run **only on a fresh `openbrain-db-data` volume** ([docker-compose.yml:36-37](OB1/docker/docker-compose.yml#L36-L37)). So each new SQL file must be **(a)** mounted with an ordering prefix after `70-init-threads.sql` — `80-init-source-revisions.sql`, `90-init-source-chunks.sql`, `95-…` for the `content_type`/`source_entities` widening — for fresh installs, **and (b)** applied to the **live** DB via the existing psql promotion runbook (the same path `init-threads.sql` took). A file that is only added to compose silently no-ops on the running stack.
- **Search:** `match_source_chunks` (new RPC; do not overload `match_sources`).

---

## 8. Sequencing & dependencies

```
P1 Provenance (compiler-only) ──> P4 Source Lifecycle (edit+retract) ─┐
                                                                      ├─> P6 Grounding &
P0 Foundations ─┬─> P2 Notebooks                                      │   Deliberate Linking
 (workbench +   ├─> P3 Notes                                          │            │
  extract)      └─> P5 Import ─────────────────────────────────────────┘            ▼
                                                                       [P7 Podcasts — DEFERRED]
```

- **P1 is compiler-only and lands independently of P0** — it emits `thought/<id>`/`source/<id>` leaf pages + rewrites citations into wikilinks ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) / [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs)); only its *optional* leaf-template polish + `ProvenancePanel` touch the P0.3 overlay scaffold.
- **P0 unblocks the workbench-backed phases** — P2/P3/P5 parallelize after it; P4/P6 add their write paths on top.
- P4 depends on **P1's source leaf page** (the read view it edits) **+ P0** (the workbench write API). P5 depends on P2 (thread linking) + P0; uses P4 conventions.
- **P6** (grounding + deliberate linking) depends on P1 (provenance), P4 (source edit options), P5 (import entry point) — it stitches them together.
- **P7 deferred**; ON stays for podcasts until it ships → **full ON retirement happens after P7, not P5** ([§10](#10-relationship-to-iks--retiring-open-notebook)).

---

## 9. Storage — the vault direction (D-I)

Current: the wiki is a git repo on the `openbrain-wiki-data` volume; notes commit
there and the compiler pulls. Direction:

- **Now:** binaries move out of git into a `wiki-assets` volume; notes stay in the current vault (the P3 write path still `git commit`s there).
- **Later (roadmap, not this plan):** replace the volume-git with a **self-hosted git server** (e.g. a Gitea-class service) as the durable vault; the compiler pushes/pulls there; **Quartz 4 remains the primary read/render surface**. The P3 notes API already commits to a git remote-agnostic location, so the migration is a remote/clone change, not an app rewrite.
- Backups must cover `wiki-assets`; purged sources should drop their assets too (retracted sources keep theirs, since they're restorable) ([§11](#11-risks--guardrails)).

---

## 10. Relationship to IKS & retiring Open Notebook

IKS was repointing **ON's** sources onto OB1 Postgres. D-B changes the
destination: **Quartz becomes the workbench**; ON is retired **in stages**.

- **Keep (built, reused):** the unified OB1 schema — `sources`, `threads`, `thread_sources`, `sessions`, `session_sources`, `find_or_create_source()`, `set_thread_source_status()`, suggestion worker. See [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/IMPLEMENTATION-PLAN-integrated-knowledge-system.md), [Integrated-knowledge-system-concept.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/Integrated-knowledge-system-concept.md).
- **Drop (after P5):** ON's repoint phases, its source import/notes role, SurrealDB-as-ON-store **for those functions**.
- **Keep running until P7:** ON as the **podcast** tool. ON stays available so podcasts work during the deferral.
- **Full decommission (after the deferred P7, gated on verification):**
  1. Confirm P1–P7 cover ON's wanted features (provenance, threads/notebooks, notes, source edit/remove, import incl. images, source grounding, **podcasts**). ON chat sessions intentionally dropped.
  2. Migrate ON-only source data still in SurrealDB into OB1 `sources` via `find_or_create_source()` (one-shot).
  3. Remove `open_notebook` + `surrealdb` dep from [docker-compose.yml](docker-compose.yml), recovery scripts, stack-map, and the `:8443/:5055` Tailscale serves ([entrypoint.sh](entrypoint.sh)).
  4. Update memory: reverses the "repoint ON" direction in [three-layer-memory-stack-integration](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\three-layer-memory-stack-integration.md).

> ⚠️ Verify ON is SurrealDB's only consumer before removing `surrealdb`
> ([gotcha](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\surrealdb-v2-define-user-gotcha.md)).

---

## 11. Risks & guardrails

- **Three-place change convention (CLAUDE.md):** each new container updates compose **+** recovery scripts **+** stack-map together. Run `/stack-map` before/after compose edits.
- **OB1 guard rails:** additive schema only (widening a CHECK = drop+re-add, values only added); never alter/drop existing `thoughts`/`sources` columns; no secrets in files. (OB1's "MCP servers must be remote Edge Functions" rule is the public-contribution contract; this is local infra under `OB1/docker/`, like the existing `openbrain-mcp` — keep out of upstream PR scope.)
- **Removal scope (D-D):** the default "remove" is a **reversible retract** (`retracted_at`; retained + restorable, invisible to generation). Only **purge** (`DELETE`) is irreversible + cascades (incl. `source_revisions`/`source_chunks`); gate it behind explicit confirm, show affected pages/links, stamp `retracted_by`/`retracted_at`, default the UI to retract, and don't expose purge to non-operators.
- **`host.docker.internal` reach:** containers on `obnet` need host gateway access (Docker Desktop provides it); confirm it's reachable from the extract/workbench containers, and that the TTS/STT service is up before P5/P7 depend on it.
- **Manual links surviving re-extraction (P6) — concrete code change:** the worker today does a **full wipe-and-reinsert per source** — `await supabase.from("source_entities").delete().eq("source_id", item.source_id)` ([entity-extraction-worker/index.ts:748](OB1/integrations/entity-extraction-worker/index.ts#L748)) — so on the next fingerprint-change re-extraction it **deletes every `user_linked` row**. Required change: scope the delete to exclude manual links (`.delete().eq("source_id", …).neq("mention_role","user_linked")`) and make the subsequent insert an upsert that yields to `user_linked` per the §Phase-6 PK-collision policy. Guard + test this explicitly (a re-extraction cycle must leave the manual link intact).
- **Don't suppress graph pages (D-J):** badging thought-only pages as "ungrounded" is fine; *removing* them would break cross-entity `[[wikilinks]]` and graph nodes. Surface, don't delete.
- **New browser-facing write surface:** bearer auth, portal-only, validate/normalize note + asset paths (no `../` escape), cap upload size, **sandbox `openbrain-extract`** (untrusted file/image parsing = classic RCE vector; run unprivileged, no extra network).
- **Membership confusion:** keep the three verbs visually distinct — **unlink from notebook** (soft, M:N status) vs **retract** (global, reversible) vs **purge** (global, irreversible cascade) — so users don't retract/nuke a shared source when they meant to unlink it from one notebook.
- **GPU/compile churn:** large imports + recompile; STT/TTS contend with `llama-cpp` — see [llama-swap perf tuning](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md). Batch embeddings; lean on the 3-min change-watch debounce.
- **Asset volume growth:** audio + images accumulate on `wiki-assets`; backup coverage + retention/cleanup (purge drops assets; retract keeps them).
- **Static-build friction (D-A):** heavy logic in `.inline.ts`; thin build-time components; overlay must not block `QUARTZ_REF` upgrades.
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the vault repo (that *is* the notes write mechanism), staying local (no remote push; D16) until the self-hosted vault exists (D-I).

---

## 12. Resolved decisions (formerly open questions)

All resolved with the operator across four rounds. These are now binding for implementation.

1. **Source edit-history = append-only revision log.** Keep one canonical `sources` row (current = head); each edit snapshots prior `content`/`title` into `source_revisions(source_id, revision, content, title, edited_at, edited_by)`. The `source_id` never changes, so `thread_sources`/`source_entities`/search stay valid; diff = head vs revision N. *(Not the supersedes-chain variant.)* → P4.
2. **`content_type` → reference table `content_types` + FK** (replaces the inline CHECK), seeded with the existing 7 + `image,docx,pptx,audio,txt,md`. A new format is one `INSERT`, no DDL. Per-format badges/filters in provenance; an `'audio'` source is its STT transcript. → P5.
3. **Extractor = correctness-first, all formats, clean/industry-standard.** Engine chosen on **extraction quality**, not footprint. `openbrain-extract` exposes a stable `/extract` interface with **best-of-breed per-format extractors** (e.g. PyMuPDF/Docling-class for PDF fidelity, python-docx/-pptx, Pillow+Tesseract for image OCR, STT for audio/video); content-core may back any format where it extracts most correctly. **Every supported format gets an explicit extraction-quality acceptance gate** (faithful text, tables, headings, image refs) — coverage of *all* listed types is in scope for this effort. → P5.
4. **Notebook hub pages = compiled stub + live hydration.** Compiler emits a thin shell (title, description, synthesis, static graph); `NotebookPage.inline.ts` fetches live sources/notes/membership/suggestions from the workbench API, so add/remove reflects instantly with no recompile wait; degrades to the shell if the API is down. → P2/§5.
5. **Notebook = thread, one hub, pinned slug.** User-facing noun **Notebook**; backing table stays `threads` (+ a pinned `slug` column). One `content/notebook/<slug>.md` hub folds in the old `topic/` synthesis and surfaces sources + `notes/<slug>/*` + triage; legacy free-text notebooks are **backfilled** to rows. Notes live under `notes/<notebook-slug>/`; `[[notebook/x]]` lines up; align `ingestNotes()` `notebook = parts[1]` to the slug. → P2/P3/§5.
6. **Workbench auth = Authelia (browser) + Caddy-injected secret (server-side).** The wiki subdomain's existing Authelia `forward_auth` authenticates the operator; Caddy injects the shared secret (reuse the `MCP_ACCESS_KEY` value) when proxying to the workbench — **never** embedded in static client JS. `edited_by`/`retracted_by` stamped `'operator'`; per-user identities deferred. → §2.3.
7. **Source removal = three verbs.** unlink (per-notebook flip) · **retract** (global, reversible `retracted_at` tombstone — retained, restorable, invisible to all generation read-paths) as the default · purge (`DELETE`, irreversible, rare). → P4.
8. **Grounded-page generation policy.** Once a page has ≥1 source: sources carry asserted facts; thought-derived claims are demoted to a labeled "working hypotheses / unverified" framing, not deleted. → P6.
9. **Ground-from-the-page + failure handling.** "Provide grounding with a new source" lives on the entity page; user supplies a document or URL; success → regenerate; failure → client-marked "⚠ Ingest failed" with **no recompile** + a durable `failed` `import_jobs` row feeding a later alerts surface. → P6.
10. **Entity evolution = derived, no new storage.** From `source_entities.created_at` + vault git history (works on today's volume git; survives the D-I self-hosted migration, which isn't built yet). → P6b.
11. **Source citations use a short per-page token** (`S1/S2…` → UUID map), not echoed UUIDs (`sources.id` is UUID; LLMs mis-transcribe them). Thought ids (`BIGSERIAL`) stay literal. → P1.
12. **One shared slug module**, canonical = the NFKD-normalizing algorithm; entity slugs already pinned so no data migration. → §14.1.
13. **Workbench writes via deno-postgres transactions** (atomic import); reads may use PostgREST. → §14.5.

---

## 13. Suggested next step

All gating questions are resolved, so the next action is **Phase 0** — stand up
the `openbrain-workbench` skeleton (Deno+Hono, internal `:8000`, on
`obnet`+`llm-net`+`app-net`, Authelia-fronted + Caddy-injected secret), the Caddy
`/workbench/*` same-origin route in the `wiki.` block, the Quartz overlay scaffold
+ asset config, and the `wiki-assets` volume. P0 unblocks every **workbench-backed** phase (P2–P7)
and proves the in-Quartz-components + thin-API + extract-sidecar architecture
end-to-end before any schema (`source_revisions`, `source_chunks`, `content_type`
widening), extractor, or thread-surface work. **One exception:** P1 (provenance)
is now **compiler-only** — it emits `thought/<id>`/`source/<id>` leaf pages and
rewrites `[#id]`/`[S:id]` citations into wikilinks — so it depends on neither the
workbench nor the schema work and can ship **in parallel with, or before, P0** as
a low-cost early win. Podcasts (P7) wait; ON keeps serving them in the meantime.

**Independent of the workbench build**, the [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap)
grounding gap can be triaged on the live stack right now — query
`source_extraction_queue` for stuck/`pending`/`last_error` rows and sample a few
sourceless entities for `source_entities` vs `thought_entities` counts — to learn
how much of the "no sources" problem is extraction backlog (fixable now) vs
thought-only-by-design (needs P6). Worth doing before committing P6 scope.

---

## 14. Engineering-fundamentals corrections (from codebase audit)

Cross-cutting issues found auditing this plan against the live code. The
phase-specific factual fixes are inlined above (flagged **⚠️ audit**); these are
the SOLID / DRY / naming / durability items that span phases.

### 14.1 DRY — one slug module, not a fourth hand-synced copy

Slug derivation is **already duplicated**: `slugify()` in
[generate-wiki.mjs:706](OB1/recipes/entity-wiki/generate-wiki.mjs#L706) and the
hand-synced `slugifyEntity()`/`slugifyNotebook()` in
[wiki-service.mjs:401-420](OB1/docker/wiki-service/wiki-service.mjs#L401-L420)
(the comment literally says *"kept in sync by hand; the recipe owns the canonical
version"*). This plan adds **thread slugs** (P2) and **leaf-page ids** (P1), which
would create a 4th/5th divergent copy. **Fix:** extract one shared
`slug.mjs`/`slug.ts` (normalize → lowercase → `[^a-z0-9]+`→`-` → trim) imported by
the recipe, the compiler, and the workbench, and the **shared TS types** already
called for in P0.5. A drifting slug function silently breaks `[[wikilink]]`
resolution across layers — the highest-leverage cleanup here.

### 14.2 Thread slugs must be **pinned** (mirror the entity `wiki_slug` pattern)

Entity pages go to real lengths to **pin a slug for life**
(`entities.metadata.wiki_slug`, [generate-wiki.mjs:719-741](OB1/recipes/entity-wiki/generate-wiki.mjs#L719-L741))
so a rename never breaks links. Threads, by contrast, have a **UUID PK and a
mutable `name`** ([init-threads.sql:44-52](OB1/docker/init-threads.sql#L44-L52))
and **no slug column**. If P2 derived `content/notebook/<slug>.md` from the name on
every compile, **renaming a notebook would orphan its page and break every
`[[notebook/<old-slug>]]`** and the §5.3 Option-B note-linking. **Resolved (now in
P2):** an additive pinned `threads.slug TEXT UNIQUE` column, generated once at
create time, immutable on rename, display name aliased — exactly the entity
`wiki_slug` pattern. Because entity slugs are already pinned in metadata, existing
pages need no migration.

### 14.3 SRP — the workbench is becoming a god-service

`openbrain-workbench` is specced to own provenance + sources CRUD + versioning +
retract + threads + membership + notes + import jobs + (later) podcast jobs. That
is fine as **one deployable**, but the handler file must not be one flat switch.
**Fix:** structure it as Hono **sub-routers per resource** (`/sources`, `/threads`,
`/notes`, `/import`, …) over a thin **service → repository** layering, so the DB
access (14.5) and validation live behind interfaces and each route stays
single-responsibility. Encapsulation point: path/asset normalization (no `../`
escape — already a §11 guardrail) belongs in one shared validator, not per-handler.

### 14.4 Durability — import job state must outlive a restart

P5 returns a `job_id` and exposes `GET /workbench/jobs/:id`. If job state is
in-memory (as `lastStatus` is in
[wiki-service.mjs:89](OB1/docker/wiki-service/wiki-service.mjs#L89)), a workbench
restart **orphans every in-flight import** and the UI polls a 404 forever.
**Resolved:** persist `import_jobs(id, status, source_id, target_entity_ids,
target_notebook, error, created_at, updated_at)`. Beyond surviving restarts and
backing `ImportStatus.tsx`, this table is **load-bearing for P6**: a failed
ground-from-the-page attempt records its `target_entity_ids` + terminal `error`
here, which is exactly the data the later "failed grounding attempts" alerts
surface reads.

### 14.5 DB access path — pick one, transactionally

Stated above ([§2.1](#21-the-unavoidable-backends)) but repeated as a fundamental:
an import inserts a `source` **and** N `source_chunks` **and** `source_entities` /
`thread_sources` links. Across separate PostgREST calls that is **non-atomic** — a
mid-sequence failure leaves a source with no chunks/links (a silently-degraded
"grounded" page). Use **deno-postgres with a transaction** for write paths
(precedent: `openbrain-suggestion-worker` talks to `openbrain-db` directly). Keep
read paths on PostgREST if convenient.

### 14.6 Naming — say what each new symbol is

- `match_source_chunks`, not "a chunk-aware `match_sources` variant" (overloading
  the name hides the row-shape difference).
- Leaf page classes `thought/` and `source/` are a **distinct page class**, not
  entity types — keep them out of the entity orphan-sweep kept-set (14.1's sibling
  bug, fixed in P1 above) and out of `graph.json`/`entities.md` candidate logic.
- Workbench routes are **resources** (`/workbench/sources/:id/revisions`), not
  verbs — the REST surface in P2/P4/P6 already mostly follows this; keep it.
