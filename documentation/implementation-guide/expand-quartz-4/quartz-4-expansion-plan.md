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
| D-D | **Sources are added as-is, are editable with a preserved edit history, and are removable with cascade.** | An edit creates a **new version / supersedes** the prior (history kept), it never silently mutates the record of truth. Removal cascades links + orphan-sweeps pages. (P4) |
| D-E | **User notes are the freely editable, additive layer**, written into the Open Brain DB. | The tethered-notes mechanism, surfaced with an in-Quartz editor. (P3) |
| D-F | **Images are first-class** — Quartz must display them; ingestion must extract/accept them. | Comes with the content-core borrow + Quartz vault-asset handling + a widened `content_type`. (P5) |
| D-G | **Threads = research groups = ON notebooks; M:N, non-exclusive.** Surfaced in Quartz with a thread index, per-thread pages, and membership management. | Schema already supports this; the work is the Quartz surface + UX. (P2, [§5](#5-threads--research-groups-the-core-organizing-axis)) |
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

Vault = a **git repo on the `openbrain-wiki-data` volume**: compiler-owned
`content/` (entity/topic pages, `entities.md`, `graph.json`), human-owned
`notes/`, and `index.md`. Quartz renders standard markdown, so **images display
once they're in the vault** — the gap is nothing puts them there yet (D-F).

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
4. **Threads have no Quartz surface** — no index, no per-thread view, no membership UI (the core organizing axis is invisible).
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
        │ ProvenancePanel  ThreadIndex/ThreadPage  MembershipPicker  NotesEditor      │
        │ SourceEditor(versioned)  SourceRetractor  ImportDropzone  [PodcastPanel*]   │
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

### Phase 2 — Threads & Membership (research groups)
*The core organizing axis — see [§5](#5-threads--research-groups-the-core-organizing-axis) for the full design.*
**Goal:** surface threads as the notebook layer — a thread index, per-thread pages (sources + provenance + notes + scoped graph + suggestion triage), and add/subtract membership (M:N, non-exclusive).
- **Backend:** thread CRUD (`GET/POST/PATCH /workbench/threads`), `POST /workbench/threads/:id/sources` (link via `link_source_to_thread`), `DELETE` (subtract via `set_thread_source_status → hidden`), suggestion triage (`accept`/`hide`).
- **Compiler:** generate `content/thread/<slug>.md` per active thread from `threads` + `thread_sources(status=confirmed)`; thread nodes into `graph.json`.
- **Quartz:** `ThreadIndex.tsx`, `ThreadPage.tsx`, `MembershipPicker.tsx` (+ `.inline.ts`); backlinks/graph leveraged (see §5).
- **Gate:** create a thread, add a source from two different threads (proves non-exclusive), subtract it from one (still in the other), accept a worker suggestion — all reflected on the thread page after recompile.

### Phase 3 — User Notes System (editable, additive — D-E)
**Goal:** author Obsidian-style notes **in Quartz** (live preview, `[[wikilinks]]`, tags, notebook=thread grouping), written additively into OB1.
- **Reuse:** `ingestNotes()` already maps `notes/<notebook>/file.md` ⇄ a thought; we add the **browser editor that writes those files**. Align `notebook` folder = thread slug so notes group under threads ([§5](#5-threads--research-groups-the-core-organizing-axis)).
- **Backend:** `PUT/GET /workbench/notes/<path>` (path validated under `notes/`, write+`git commit`, optimistic concurrency via content-hash/`If-Match`); notes index.
- **Quartz:** `NotesEditor.tsx` + `.inline.ts` — editor, `[[…]]` autocomplete from `entities.md` + notes/thread index, tags.
- **Decision (idea-doc Q1):** notes stay in the `notes/` layer tethered to thoughts — not a separate `user_notes` collection.
- **Gate:** create/edit a note → appears in vault, links resolve, next compile tethers + extracts; two-session conflict detected.

### Phase 4 — Source Lifecycle: Edit-with-history + Retraction (D-D)
**Goal:** sources are added as-is but the user can **edit them (history preserved)** and **remove them (cascade)**.
- **Edit, versioned (the "replacement / updated source" model):** an edit snapshots the prior content into an append-only **`source_revisions(source_id, revision, content, title, edited_at, edited_by)`**, then updates `sources.content` to the new version (current = head; history = revisions). Re-embed is automatic via the existing fingerprint-gated queue trigger; metadata-only edits must not bump the content fingerprint. The source view shows version history + diff. *(This supersedes the earlier "read-only, no edits" stance.)*
- **Remove, two-tier:**
  - **Soft (reversible):** subtract from a thread = `set_thread_source_status → hidden/inactive` (source survives in other threads — distinct from deletion; see §5).
  - **Hard (operator-confirmed, irreversible):** `DELETE FROM sources` → `source_entities`/`thread_sources`/`session_sources`/`source_revisions` cascade → next compile's orphan sweep removes unsupported pages.
- **Backend:** `PATCH /workbench/sources/:id` (versioned edit), `GET …/revisions`, `POST …/:id/retract {mode: hide|delete, scope: thread|global}`, restore.
- **Quartz:** `SourceEditor.tsx` (inline editor + version history/diff), `SourceRetractor.tsx` (hide-from-thread vs delete-permanently, confirm dialog showing affected pages/links).
- **Gate:** edit a source → new revision recorded, old preserved, re-embed enqueued, dependent pages refresh; hard-delete cascades + sweeps; delete always needs explicit confirmation.

### Phase 5 — Direct Source Import Pipeline (incl. images — D-F)
**Goal:** drag-and-drop / picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT, **images** (and **audio/video** via STT) → extract → chunk → embed → land as first-class sources, linked to a chosen thread, with progress + errors; images render in Quartz.
- **`openbrain-extract` sidecar (correctness-first, all formats — §12.3):** stable `POST /extract` → `{ markdown, title, metadata, pages, images[] }` with best-of-breed per-format extractors (PDF: PyMuPDF/Docling-class; DOCX/PPTX: python-docx/-pptx; images: Pillow+Tesseract OCR; audio/video: STT at `host.docker.internal:8000/v1`); content-core where it extracts most faithfully. **Per-format extraction-quality acceptance gate** (text fidelity, tables, headings, image refs). Add to compose **+ recovery + stack-map**.
- **Workbench `POST /workbench/import` (async):** store upload → extract → write images to `assets/<source-id>/` + rewrite refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(..., 'deliberate')` to the selected thread → `job_id`; `GET /workbench/jobs/:id` for progress.
- **Schema (additive — §12.2):** widen the `content_type` CHECK and **`source_chunks`** + a chunk search RPC. Two corrections from the audit:
  - **Reconcile the CHECK with the import format list.** The live CHECK ([init-sources.sql:27-30](OB1/docker/init-sources.sql#L27-L30)) is `web_article,pdf,youtube_transcript,podcast_transcript,paper,manual,research_synthesis`. The import set (PDF, DOC/DOCX, PPT/PPTX, MD, TXT, images, audio/video) introduces types with **no enum value** — `docx,pptx,image,audio` plus `txt,md` (and legacy `doc,ppt`). Either add **all** of them to the CHECK or pin an explicit mapping (e.g. `txt`/`md`→`manual`, `doc`→`docx`, `ppt`→`pptx`). `pdf` already exists. A TXT/MD upload that maps to no allowed value will fail the CHECK at insert — pick the mapping now.
  - **`source_chunks(source_id UUID, idx INT, content TEXT, embedding VECTOR(1024), PRIMARY KEY(source_id, idx))`** (confirmed — long-doc retrieval *and* the source list podcasts build from), `source_id … REFERENCES sources(id) ON DELETE CASCADE`. Name the search RPC explicitly **`match_source_chunks`** (don't overload `match_sources`, which returns source-level rows — see [init-sources.sql:78](OB1/docker/init-sources.sql#L78)).
- **Quartz:** `ImportDropzone.tsx` (validation, per-file progress, errors) + `ImportStatus.tsx`; thread selector reuses `MembershipPicker`.
- **Gate:** drop a PDF, DOCX, PNG (and an MP3) → all extract/transcribe, chunk, embed, dedupe, link to the chosen thread, entity-extract, surface via P1 provenance and on the P2 thread page; images render inline; corrupt files fail clearly.

### Phase 6 — Source Grounding & Deliberate Wiki Linking (D-J)
*Closes the [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap) gap. Builds on P1 (provenance), P4 (source edit options), P5 (import).*

**Goal:** make a page's grounding state visible and explainable, and let the user
**deliberately link a source — existing or freshly uploaded — to a wiki page** as
an authoritative hint the next generation honors.

- **Grounding visibility:**
  - `ProvenancePanel` (P1) shows a grounding badge: **"N sources"** / **"thought-only — no sources yet"** / **"⏳ sources pending extraction"**.
  - `GET /workbench/grounding` surfaces `source_extraction_queue` health (pending/started/`last_error` counts) so a **backlog** page (cause 2) is never mislabeled as **by-design** (cause 1).
  - Compiler policy knob: **badge** thought-only pages (recommended) rather than suppress them — they still carry graph value.
- **Deliberate source→page link (the core feature):** from the provenance panel and the P4 source editor, **"Link this source to ‹wiki page›"** writes a marked `source_entities` row. Next compile: the entity becomes/stays a candidate and `fetchLinkedSources` includes it → the page **regenerates citing that source** and flips to "grounded". (Mirrors `thread_sources` `link_type='deliberate'`, but on the source↔entity edge that drives wiki pages.)
  - **⚠️ Schema reality (audit):** `source_entities` ([init-source-graph.sql:27-35](OB1/docker/init-source-graph.sql#L27-L35)) has columns `source_id, entity_id, mention_role, confidence, evidence, created_at` and **PK `(source_id, entity_id)`** — there is **no `metadata` column**, so "plus a `metadata` flag" as written is impossible. Two additive fixes, both in scope under the additive-only guardrail:
    1. **Marker:** `mention_role='user_linked'`, `confidence=1.0`, `evidence='manual:<operator>@<iso8601>'`. Optionally `ALTER TABLE public.source_entities ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb` if a richer flag is wanted — but `mention_role` alone suffices and avoids a column.
    2. **PK-collision decision (must be stated):** because the PK is `(source_id, entity_id)`, a manual `user_linked` row and an auto `mentioned` row for the **same pair cannot coexist**. Policy: **`user_linked` wins** — the worker's re-extraction upsert must `ON CONFLICT (source_id,entity_id) DO NOTHING` (or merge) when the existing row is `user_linked`, never overwriting the manual mention_role back to `mentioned`.
- **Upload-and-link entry point:** the P5 `ImportDropzone` gains a **"link to wiki page(s) / thread"** target field, so a *new* source is ingested **and** linked to chosen entities/threads in one action → it feeds the next generation for those pages. This is exactly the "upload a new source while simultaneously linking it for wiki review/generation" path.
- **Worker change (required):** the entity-extraction worker must **upsert without deleting `user_linked` rows** (preserve manual links across re-extraction of a source). Note in the worker + a guard test.
- **Quartz:** `SourceLinker.tsx` (entity/page picker on a source view + in the editor), grounding badges in `ProvenancePanel`, "link targets" field in `ImportDropzone`.
- **Scope note:** entity pages first (the common case). Topic-synthesis pages (e.g. autobiography) are generated differently — deliberate linking for those is a follow-up.

**Gate:** link a source to a previously sourceless ("thought-only") page → next
compile regenerates it with the source cited and a "grounded" badge; the manual
link survives a re-extraction cycle; a backlog page shows "⏳ pending", not a
false "ungrounded"; upload-and-link lands a new source already attached to its
target page.

---

### Phase 7 — Podcast Service (DEFERRED — D-B/D-H)
*Large feature; built later. **Open Notebook remains available and provides podcasts until this ships.*** Recorded here so the architecture leaves room.
- **Generation on request, per thread:** pull the thread's sources (via `source_chunks`) → script via local Qwen (`llama-cpp`) → audio via **existing TTS** at `host.docker.internal:8000/v1` (several voices) → `assets/podcasts/<id>.mp3` + transcript.
- **Schema (additive):** `podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)` — "organized by thread" for free.
- **Settings:** a **config options panel mirroring ON's UI** (speaker count 1–4, voice selection from the TTS service, style/length). Stored per-thread or global default.
- **Transcript → note:** reuses the P3 notes write path (editable, tethered).
- **Quartz:** `PodcastPanel.tsx` — Podcasts section (global + per-thread) with an `<audio>` player; "Generate podcast for this thread"; "Save transcript as note".
- **Deferral note:** because ON covers podcasts meanwhile, no `openbrain-podcast` container is built in P1–P6. When P7 starts, prefer a thin caller of the existing TTS over a heavy `podcast-creator` dependency unless multi-speaker scripting needs it.

---

## 5. Threads / research groups — the core organizing axis

*Answers the operator's questions on how threads are organized and how membership is managed.*

### 5.1 Model (already in the DB — D-G)

A **thread = a research group = an ON notebook**: a named grouping of relatable
information. Membership lives in `thread_sources` as an **M:N join**, so a source
is **non-exclusive** — it can sit in many threads at once (linking, not
ownership), exactly like ON. Threads, the link primitives, soft-status flips, and
the cross-thread suggestion worker all already exist ([§1.2](#12-what-open-brain-already-gives-us-the-foundation)); the work is the Quartz surface.

### 5.2 How threads appear in Quartz

- **Thread index** (`content/thread/` + `ThreadIndex.tsx`): all active research groups, with size/recency, in the nav.
- **Per-thread page** (`content/thread/<slug>.md` + `ThreadPage.tsx`) = the **notebook view**:
  - its **sources** (`thread_sources.status=confirmed`) with P1 provenance,
  - its **notes** (`notes/<thread-slug>/…`, grouped by the unified notebook=thread slug),
  - a **scoped graph** (thread node + its sources/entities, from an extended `graph.json`) and a **backlinks** panel — *this is where Quartz earns its keep*,
  - a **suggestion triage** strip (pending cross-thread links → accept/hide),
  - later, its **podcasts** (P7).
- Compiler generates the page from `threads` + confirmed `thread_sources`; client components hydrate live membership actions on top.

### 5.3 Add / subtract membership — options explored (operator's Q3)

| Option | How it works | Best for | Trade-off |
|--------|--------------|----------|-----------|
| **A — ON-style picker** | `MembershipPicker` on a source view / thread page; multi-select threads; calls `link_source_to_thread` (add) / `set_thread_source_status→hidden` (subtract). | **Sources** (read-mostly DB rows with no markdown body to link in). | Explicit, familiar, but a separate UI gesture. |
| **B — Obsidian-style wikilinks** | A **note** contains `[[thread/<name>]]`; the compiler materializes the membership and Quartz's backlinks/graph show it. Optionally a `thread:` tag bulk-links. | **Notes** (real markdown files; native to Quartz). | Indirect for sources (you can't edit a wikilink into an immutable-ish source row), so not sufficient alone. |
| **C — Hybrid + triage (recommended)** | Picker for sources, wikilinks/tags for notes, **plus** the suggestion-worker proposing links (status `pending`) surfaced as a triage queue on the thread page (accept→confirmed, hide→hidden). | Everything. | Slightly more UI, but matches how each object type actually behaves and uses Quartz's graph/backlinks where they're strongest. |

**Recommendation: C.** Sources get the explicit picker; notes get Obsidian-native
`[[thread/x]]` linking with the thread page's backlinks panel as the live view of
membership; the suggestion worker fills the long tail and the user triages.
"Subtract from thread" is always a **soft status flip** (the source stays in its
other threads) — categorically different from the **hard source deletion** in P4.

### 5.4 Where Quartz is uniquely helpful here

Graph view (thread + source/entity nodes), backlinks (every note referencing a
thread), thread-scoped full-text search, and `[[ ]]` autocomplete make threads
feel like Obsidian notebooks while membership stays in Postgres as the single
source of truth.

---

## 6. Design decisions — consolidated

| Question | Decision | Rationale |
|---|---|---|
| Notes vs sources storage | Notes in `notes/` tethered to thoughts; notebook folder = thread slug. | Isolated, reuses `ingestNotes()`, unifies notes↔threads. |
| Source editing/versioning (D-D) | **Editable; one canonical row (head) + append-only `source_revisions` history** — `source_id` never changes (§12.1). | Operator wants edits *with* history; stable id keeps thread links/search valid. |
| Source removal (D-D) | Soft = thread status flip; hard = cascade delete + orphan sweep, operator-confirmed. | Non-exclusive M:N means "remove from thread" ≠ "delete". |
| Re-embed | Automatic via fingerprint-gated queue trigger on content change; metadata-only edits don't bump fingerprint. | Already built; no threshold needed. |
| Import chunking + `source_chunks` (confirmed) | Semantic/sentence-boundary default, `bge-m3`-tuned; `source_chunks` table. | Long-doc retrieval **and** the source list podcasts build from. |
| Images (D-F) | content-core/Pillow extracts → `assets/` → Quartz renders inline; `content_type` gains `'image'`. | Closes the no-images gap with the ingestion borrow. |
| Audio/video sources | Transcribe via existing **STT** at `host.docker.internal:8000/v1`; `content_type='audio'`. | Reuses the local service; no new dependency. |
| Podcast TTS (D-H) | Existing local **TTS** at `host.docker.internal:8000/v1`; settings via ON-style config panel. **Deferred (P7).** | No engine decision needed; ON covers podcasts meanwhile. |
| Wiki grounding gap (D-J) | Pages come from the entity graph; thought-only pages are sourceless by design. Surface grounding state + **deliberate source→page linking** + **upload-and-link**; distinguish backlog via queue health. | Fixes the observed "no sources" gap without suppressing graph pages (§1.4, P6). |
| Threads (D-G) | Existing `threads`/`thread_sources` M:N; Quartz adds index + stub+live thread pages + hybrid membership (§5, §12.4). | Schema ready; only the surface is missing. |
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
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` — leaf-page template (`type: thought|source` layout; native popover + SPA + backlinks do the interaction, no custom linkifier), optional `ProvenancePanel` (consolidated per-page provenance index), `ThreadIndex`, `ThreadPage`, `MembershipPicker`, `NotesEditor`, `SourceEditor`, `SourceRetractor`, `ImportDropzone`, `ImportStatus`, `[PodcastPanel]`, `.inline.ts`, layout/config.
- **Wiki compiler:** [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) + [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) — emit cited-only `thought/<id>.md` + `source/<id>.md` leaf pages (batch-fetch full content by id), rewrite inline `[#id]`/`[S:id]` citations into `[[thought/…|#id]]`/`[[source/…|S:id]]` wikilinks, orphan-sweep uncited leaves (P1); thread-page generation + graph (P2); notes write interplay (P3); confirm orphan sweep covers hard-deletes + uncited leaves (P4).
- **Schema (additive only):** `source_revisions` (`init-source-revisions.sql`); widen `content_type` CHECK + `source_chunks` + `match_source_chunks` (`init-source-chunks.sql`); `user_linked` rows in `source_entities` (P6, marked via `mention_role='user_linked'`, optional additive `metadata` column); `podcasts` (P7); retraction audit (additive `retracted_by`/`retracted_at` columns or a `source_revisions` tombstone — `sources` has no `metadata`-for-audit convention to lean on).
  - **⚠️ Migration path (audit — two places, not one):** `/docker-entrypoint-initdb.d` scripts run **only on a fresh `openbrain-db-data` volume** ([docker-compose.yml:36-37](OB1/docker/docker-compose.yml#L36-L37)). So each new SQL file must be **(a)** mounted with an ordering prefix after `70-init-threads.sql` — `80-init-source-revisions.sql`, `90-init-source-chunks.sql`, `95-…` for the `content_type`/`source_entities` widening — for fresh installs, **and (b)** applied to the **live** DB via the existing psql promotion runbook (the same path `init-threads.sql` took). A file that is only added to compose silently no-ops on the running stack.
- **Search:** `match_source_chunks` (new RPC; do not overload `match_sources`).

---

## 8. Sequencing & dependencies

```
P1 Provenance (compiler-only) ──> P4 Source Lifecycle (edit+retract) ─┐
                                                                      ├─> P6 Grounding &
P0 Foundations ─┬─> P2 Threads                                        │   Deliberate Linking
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
- Backups must cover `wiki-assets`; hard-deleted sources should drop their assets too ([§11](#11-risks--guardrails)).

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
- **Destructive hard-delete (D-D):** irreversible + cascades (incl. `source_revisions`); require explicit confirm, show affected pages/links, audit `retracted_by`/`retracted_at`, default UI to soft. Don't expose to non-operators.
- **`host.docker.internal` reach:** containers on `obnet` need host gateway access (Docker Desktop provides it); confirm it's reachable from the extract/workbench containers, and that the TTS/STT service is up before P5/P7 depend on it.
- **Manual links surviving re-extraction (P6) — concrete code change:** the worker today does a **full wipe-and-reinsert per source** — `await supabase.from("source_entities").delete().eq("source_id", item.source_id)` ([entity-extraction-worker/index.ts:748](OB1/integrations/entity-extraction-worker/index.ts#L748)) — so on the next fingerprint-change re-extraction it **deletes every `user_linked` row**. Required change: scope the delete to exclude manual links (`.delete().eq("source_id", …).neq("mention_role","user_linked")`) and make the subsequent insert an upsert that yields to `user_linked` per the §Phase-6 PK-collision policy. Guard + test this explicitly (a re-extraction cycle must leave the manual link intact).
- **Don't suppress graph pages (D-J):** badging thought-only pages as "ungrounded" is fine; *removing* them would break cross-entity `[[wikilinks]]` and graph nodes. Surface, don't delete.
- **New browser-facing write surface:** bearer auth, portal-only, validate/normalize note + asset paths (no `../` escape), cap upload size, **sandbox `openbrain-extract`** (untrusted file/image parsing = classic RCE vector; run unprivileged, no extra network).
- **Membership confusion:** keep "remove from thread" (soft, M:N status) visually distinct from "delete source" (global, cascading) so users don't nuke a shared source when they meant to unlink it from one thread.
- **GPU/compile churn:** large imports + recompile; STT/TTS contend with `llama-cpp` — see [llama-swap perf tuning](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md). Batch embeddings; lean on the 3-min change-watch debounce.
- **Asset volume growth:** audio + images accumulate on `wiki-assets`; backup coverage + retention/cleanup (hard-deletes drop assets).
- **Static-build friction (D-A):** heavy logic in `.inline.ts`; thin build-time components; overlay must not block `QUARTZ_REF` upgrades.
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the vault repo (that *is* the notes write mechanism), staying local (no remote push; D16) until the self-hosted vault exists (D-I).

---

## 12. Resolved decisions (formerly open questions)

All six resolved with the operator. These are now binding for implementation.

1. **Source edit-history = append-only revision log.** Keep one canonical `sources` row (current = head); each edit snapshots prior `content`/`title` into `source_revisions(source_id, revision, content, title, edited_at, edited_by)`. The `source_id` never changes, so `thread_sources`/`source_entities`/search stay valid; diff = head vs revision N. *(Not the supersedes-chain variant.)* → P4.
2. **`content_type` widened to specific types:** add `'image'`, `'docx'`, `'pptx'`, `'audio'` (+ keep existing). Per-format badges/filters in provenance; an `'audio'` source is its STT transcript. → P5.
3. **Extractor = correctness-first, all formats, clean/industry-standard.** Engine chosen on **extraction quality**, not footprint. `openbrain-extract` exposes a stable `/extract` interface with **best-of-breed per-format extractors** (e.g. PyMuPDF/Docling-class for PDF fidelity, python-docx/-pptx, Pillow+Tesseract for image OCR, STT for audio/video); content-core may back any format where it extracts most correctly. **Every supported format gets an explicit extraction-quality acceptance gate** (faithful text, tables, headings, image refs) — coverage of *all* listed types is in scope for this effort. → P5.
4. **Thread pages = compiled stub + live hydration.** Compiler emits a thin shell (title, description, static graph); `ThreadPage.inline.ts` fetches live sources/notes/membership/suggestions from the workbench API, so add/remove reflects instantly with no recompile wait; degrades to the shell if the API is down. → P2/§5.
5. **Unify notebook = thread slug now.** Notes live under `notes/<thread-slug>/`; a note's folder *is* its thread, so the thread page auto-shows `notes/<slug>/*` and `[[thread/x]]` lines up. Align the `ingestNotes()` `notebook = parts[1]` mapping to the thread slug. → P3/§5.
6. **Workbench auth = single operator bearer now.** One shared secret (reuse the `MCP_ACCESS_KEY` pattern); `edited_by`/`retracted_by` stamped `'operator'`. Per-user identities deferred until the tailnet has more humans. → §2.3.

---

## 13. Suggested next step

All gating questions are resolved, so the next action is **Phase 0** — stand up
the `openbrain-workbench` skeleton (Deno+Hono, `:8814`, bearer auth), the Caddy
`/workbench/*` same-origin route, the Quartz overlay scaffold + asset config, and
the `wiki-assets` volume. P0 unblocks every **workbench-backed** phase (P2–P7)
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
and **no slug column**. If P2 derives `content/thread/<slug>.md` from the name on
every compile, **renaming a thread orphans its page and breaks every
`[[thread/<old-slug>]]`** and the §5.3 Option-B note-linking. **Fix:** pin a thread
slug on first generation (additive `threads.metadata jsonb` or a `slug` column),
reuse forever, alias the display name — exactly the entity pattern. Add this to
the P2 schema/compiler work; it's currently unaddressed.

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
restart **orphans every in-flight import** and the UI polls a 404 forever. **Fix:**
persist jobs in a small additive `import_jobs(id, status, source_id, error,
created_at, updated_at)` table (it also gives the `ImportStatus.tsx` history view
something durable to read) — or explicitly document jobs as ephemeral and have the
client treat a missing job as "ask the source list whether it landed."

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
