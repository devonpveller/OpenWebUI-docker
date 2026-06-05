# Quartz 4 Expansion Plan — Quartz as the Open Brain Workbench

> **Status:** Plan / pre-implementation
> **Branch context:** `feature/integrated-knowledge-system`
> **Reframes:** the Open Notebook repoint phases of the in-flight Integrated
> Knowledge System (IKS) work — see [§10](#10-relationship-to-iks--retiring-open-notebook).
> **Source idea:** [initial-quartz-4-expansion-idea.md](documentation/implementation-guide/expand-quartz-4/initial-quartz-4-expansion-idea.md)

---

## 0. Decisions locked for this plan

Confirmed with the operator across multiple rounds.

| #   | Decision                                                                                                                                                                                                                                                                                                                                                                                  | Consequence                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| D-A | **Interactive layer = in-Quartz client components.**                                                                                                                                                                                                                                                                                                                                      | Preact components + client scripts in the Quartz viewer, backed by a thin write-API on Open Brain (Quartz can't write Postgres from a static page).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| D-B | **Borrow from Open Notebook, then retire it — but ON stays running for podcasts until that feature is ported.**                                                                                                                                                                                                                                                                           | Harvest content-core (docs + **images** + OCR), the **podcast** flow (deferred), and source/notes/notebook UX into the Quartz+OB1 stack. Full ON decommission waits for the deferred podcast phase ([§10](#10-relationship-to-iks--retiring-open-notebook)).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| D-C | **Features, phased:** Provenance → Threads → Notes → Source Lifecycle → Import → Source-Grounding & Deliberate Linking → (deferred) Podcasts.                                                                                                                                                                                                                                             | Each phase has its own ship gate.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| D-D | **Sources are added as-is, _updated in place_ with preserved history (never replaced), and removed via a reversible global tombstone (retained, restorable) — not destroyed by default.**                                                                                                                                                                                                 | An edit snapshots the prior into `source_revisions` (history kept) and **updates the same `source_id`** — there is **no "replace source"** gesture ("a better source exists" = _add a new source_); URL sources can queue an ai-stack **re-fetch** that lands as a new revision. "Remove" = **retract** (set `retracted_at`; the row + content survive, invisible to all wiki generation, restorable). Irreversible **purge** (`DELETE`) is a rare, explicit operator escape hatch. Per-notebook **unlink** is a separate membership flip. Retract and grounding are **staged mutations** (reversible window, autonomous commit, effects shown via a gravity counter + a `Changes` log in the user-note layer — see [§4 Staged mutations](#staged-mutations-p4-retract--p6-grounding--shared-lifecycle)). (P4) |
| D-E | **User notes are the freely editable, additive layer**, written into the Open Brain DB.                                                                                                                                                                                                                                                                                                   | The tethered-notes mechanism, surfaced with an in-Quartz editor. (P3)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| D-F | **Images are first-class** — Quartz must display them; ingestion must extract/accept them.                                                                                                                                                                                                                                                                                                | Comes with the content-core borrow + Quartz vault-asset handling + a widened `content_type`. (P5)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| D-G | **Notebook = research group = ON notebook; M:N, non-exclusive.** "**Notebook**" is the user-facing noun (UI, pages, routes); the `threads`/`thread_sources` table keeps its name **internally**. One **notebook hub page** per notebook merges synthesis + sources + notes + triage; the slug is **pinned** (renameable name).                                                            | Schema already supports membership; the work is the Quartz surface + a pinned `slug` column + folding the old `topic/` synthesis into the hub. (P2, [§5](#5-notebooks--research-groups-the-core-organizing-axis))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| D-H | **Podcasts use the existing local TTS/STT service** at `host.docker.internal:8000/v1` (OpenAI-compatible, several voices; STT too). Settings via a **config panel mirroring ON's UI.**                                                                                                                                                                                                    | No new TTS engine decision; STT also enables audio/video source ingestion. **Podcast phase is deferred** (large feature; ON covers it meanwhile). (P7)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| D-I | **Storage direction: move off the current git-vault-on-volume toward a self-hosted git vault (later); Quartz 4 is the primary surface.**                                                                                                                                                                                                                                                  | Near-term: a separate `wiki-assets` volume for binaries; notes stay in the current vault but the roadmap target is a self-hosted git server. ([§9](#9-storage--the-vault-direction))                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| D-J | **Wiki pages are generated from the entity graph (thoughts + sources + edges), not from sources alone — so sourceless pages are _expected_ for thought-only entities, not a generator bug.** Treat grounding as a _surfaced state_ fixed by user-driven **deliberate source→page linking** + **upload-and-link**, and distinguish by-design sourcelessness from extraction-queue backlog. | Closes the operator-observed "many entries without sources" gap without suppressing graph-connectivity pages. ([§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap), P6)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

Spine unchanged: Quartz already mimics Obsidian and is _already wired directly to
Open Brain_, so this is feature development + borrowed implementations, not a new
architecture.

> **Core purpose — the diagnostic loop.** The reason AI notes exist alongside human
> notes and grounding: a **thought** is the user's _unverified mental model_ (a
> belief; technically its origin is a source, but it is _not grounded_); an **AI
> note** (OWUI: problem statement → LLM-driven source gathering/discovery →
> synthesis over those gathered sources) is an _independent research effort_;
> **grounding** is the user deliberately attaching real evidence (doc/URL) to a
> claim to increase its legitimacy. Together they let the user **cross-reference
> their prior mental model against an independent LLM research effort to diagnose
> their own thinking** — legitimizing beliefs by deliberate user action rather than
> trusting either a self-proclaimed thought or an LLM claim. Source _discovery_
> happens **upstream at the external tooling inlet** (OWUI research/deep-research
> gathering sources); the wiki + autonomous linking are the _byproduct_ the user
> explores, surfacing connections not previously considered. Every feature below
> serves this loop.

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
- **`notes/`** — the **authored** layer the compiler is forbidden to write ([wiki-service.mjs:132-140](OB1/docker/wiki-service/wiki-service.mjs#L132-L140)). Authored by **humans _and_ AI assistants** (research pipelines, Open WebUI, other chat services emit notes here for the human to build on); each note tethers to one thought via `ingestNotes()`. Note provenance (`metadata.source = user_note | ai_note`, plus which agent) distinguishes them. The author-owned `notes/` layer also hosts the **`Changes` log** (staged-mutation effect logging — see [§4 Staged mutations](#staged-mutations-p4-retract--p6-grounding--shared-lifecycle)); it is **never** part of the generation pool.
- **`index.md`** — vault-root home.

Quartz renders standard markdown, so **images display once they're in the vault** — the gap is nothing puts them there yet (D-F).

### 1.2 What Open Brain already gives us (the foundation)

| Capability                                                                                                                                                                   | Where                                                                           | State                                       |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------------------------- |
| `sources` (url, title, content, content_type, tags, notebook, embedding `VECTOR(1024)`, metadata)                                                                            | [init-sources.sql](OB1/docker/init-sources.sql)                                 | ✅                                          |
| `find_or_create_source()` — dedup on url/content-hash                                                                                                                        | [init-threads.sql](OB1/docker/init-threads.sql)                                 | ✅                                          |
| **`threads`** (research groups) + **`thread_sources`** (M:N, link_type auto/suggested/deliberate, status confirmed/pending/hidden/inactive) + `sessions` + `session_sources` | [init-threads.sql](OB1/docker/init-threads.sql)                                 | ✅ — **this is the notebook model already** |
| `link_source_to_thread()` (additive upsert) + `set_thread_source_status()` (soft flips, never deletes)                                                                       | [init-threads.sql](OB1/docker/init-threads.sql)                                 | ✅ — add/subtract primitives                |
| `source_extraction_queue` + fingerprint-gated auto-enqueue trigger                                                                                                           | [init-source-graph.sql](OB1/docker/init-source-graph.sql)                       | ✅                                          |
| Entity worker → `source_entities` (`ON DELETE CASCADE`)                                                                                                                      | `openbrain-entity-worker`                                                       | ✅                                          |
| Cross-thread link suggestions (status `pending`)                                                                                                                             | `openbrain-suggestion-worker`                                                   | ✅ — feeds triage UI                        |
| Embeddings `bge-m3` 1024-dim via `llama-cpp-embed`; `match_sources()` RPC                                                                                                    | MCP server / [init-sources.sql](OB1/docker/init-sources.sql)                    | ✅                                          |
| Wiki compiler: change-watch (3-min debounce), notes ingest, **orphan sweep**, `POST /recompile`                                                                              | [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs)                    | ✅                                          |
| **Tethered notes** — `notes/<notebook>/file.md` ⇄ one thought via `metadata.note_path`, diff-based upsert/delete + extraction enqueue                                        | `ingestNotes()` in [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ — basis for P3                           |
| **Local TTS + STT** (OpenAI-compatible, several voices)                                                                                                                      | `host.docker.internal:8000/v1`                                                  | ✅ — basis for P7 + audio ingest            |

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
generated from the _entity graph_, not from sources directly.**

- **Candidate selection** ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) `listBatchCandidates`, ~L302–335): an entity gets a page if it has ≥ `WIKI_BATCH_MIN_LINKED` links — counting **`thought_entities` first, then `source_entities`**. So thought mentions alone qualify an entity.
- **Source attachment** (`fetchLinkedSources`, ~L435–459, gated by `--include-sources` + `WIKI_MAX_SOURCES`): sources are joined via `source_entities!inner`. If an entity has **zero `source_entities` rows**, the page renders with **no `## Sources` section** (the system prompt omits it when nothing was cited).

So a sourceless page arises three ways:

1. **Thought-only entity — _by design, not a bug_.** The entity was mentioned in a captured thought (`capture_thought`) but in no source document → `thought_entities > 0`, `source_entities = 0`. The page exists so cross-entity `[[wikilinks]]` and graph nodes resolve; it legitimately has no sources. _This reads as "ungrounded" and is what the operator is mostly seeing._ This is an **unverified mental model** awaiting deliberate grounding (P6), not a deficiency.
2. **Extraction backlog / worker failure — _operational bug_.** Sources exist but `source_extraction_queue` rows are stuck `pending`/`started` or carry `last_error`, or the pre-compile drain ([wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) `drainWorkerQueues`, ~L280) gave up (worker unreachable). The page compiles _before_ its sources are linked.
3. **Misconfiguration — _guard against regression_.** `--include-sources` not passed or `WIKI_MAX_SOURCES=0`. The service currently passes the flag ([wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) ~L589/L594), so this is only a watch-for-drift case.

**Implication:** "fix sourceless pages" is two jobs — (a) _distinguish_ by-design (1) from backlog (2) via queue health, and (b) give the user a way to **deliberately attach a source** (existing or freshly uploaded) so thought-only pages become grounded on the next compile. Both are [Phase 6](#phase-6--source-grounding--deliberate-wiki-linking-d-j).

---

## 2. Target architecture

### 2.1 The unavoidable backends

```
        ┌──────────────────── Quartz viewer (static + hydrated) ─────────────────────┐
        │ ProvenancePanel  NotebookIndex/NotebookPage  MembershipPicker  NotesEditor  │
        │ SourceEditor(versioned)  SourceRetractor  SourceLinker  ImportDropzone      │
        └───────────────┬─────────────────────────────────────────────────────────────┘
                        │  fetch()  (same-origin /workbench/* via Caddy `handle`)
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

> **Provenance read is not workbench-backed (Phase 1 redesign).** The `provenance` item in the workbench box and the `ProvenancePanel` `fetch()` arrow above are superseded _for read_: P1 serves provenance as **static `thought/<id>` & `source/<id>` leaf pages** emitted at compile and reached via ordinary wikilinks (native popover + SPA). The workbench's provenance role shrinks to the **optional** `ProvenancePanel` summary; its load-bearing jobs remain the **write** paths (sources CRUD/versioning, threads, notes, import) plus the P6 deliberate-link write. See [§4 Phase 1](#4-feature-phases).

- **`openbrain-workbench`** (Deno+Hono) — browser-facing read/write API, kept **off** the MCP server + cloud-gateway (limited to 8 tools) so the multipart/auth surface is isolated. **Port/convention (audit):** every OB1 service listens on internal `PORT=8000` and (optionally) publishes a distinct loopback host port; follow that — internal `:8000`, optional debug publish `127.0.0.1:8814:8000`. The `:8814` used throughout this doc denotes that **host** debug port, not a second internal port. Networks: **`obnet` + `llm-net` + `app-net`** (app-net is required for portal-Caddy reachability — see [§2.3](#23-routing-exposure-auth)). **Routes are mounted prefix-inclusive** (`/workbench/sources`, `/workbench/notes`, `/workbench/import`, …) because Caddy proxies with `handle` (prefix-preserving), **not** `handle_path` (prefix-stripping) — see [§2.3](#23-routing-exposure-auth). **DB access:** for atomic multi-row writes (import = source + chunks + links in one unit) the workbench should talk to `openbrain-db` **directly via deno-postgres with transactions** — mirroring `openbrain-suggestion-worker` ([docker-compose.yml:299-325](OB1/docker/docker-compose.yml#L299-L325)) — rather than firing several non-atomic PostgREST calls through `openbrain-rest`. Read paths may still use PostgREST.
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

- **Same-origin route — use `handle`, not `handle_path` (audit):** add `handle /workbench/* { reverse_proxy openbrain-workbench:8000 … }` to the **`wiki.{$PUBLIC_DOMAIN}` block in [config/caddy/Caddyfile](config/caddy/Caddyfile)** (above the catch-all `reverse_proxy` to the viewer) **and** the equivalent Tailscale `serve` path ([recipe](C:\Users\yamao.claude\projects\d--Open-WebUI-ai-stack\memory\tailscale-serve-restore-recipe.md)). **`handle` preserves the `/workbench` prefix**, so the workbench receives `/workbench/import`, `/workbench/sources/:id`, etc. — matching the prefix-inclusive route names used throughout this doc. (`handle_path` would _strip_ the prefix and the workbench's `/workbench/*` routes would 404; if you ever prefer stripping, you must also drop `/workbench` from every Hono sub-router mount — don't mix the two.) `/workbench/*` is a distinct prefix from Quartz's root-relative assets, so it coexists on the same subdomain cleanly.
- **Network reachability:** for the portal Caddy to resolve `openbrain-workbench` by name it **must join `app-net`** (the external `ai-stack_app-net`), exactly as `openbrain-wiki-viewer` does ([docker-compose.yml:417-423](OB1/docker/docker-compose.yml#L417-L423)). So the workbench's networks are **`obnet` + `llm-net` + `app-net`** — the P0.1 "obnet+llm-net" list is incomplete.
- **Upload body cap:** the wiki subdomain currently enforces `request_body { max_size 1MB }` ([config/caddy/Caddyfile:216-218](config/caddy/Caddyfile#L216-L218)). Every P5 import (PDF/DOCX/PPTX/image/audio) exceeds 1 MB, so the `/workbench/import` route needs its **own raised `request_body` cap** (e.g. a `@import` matcher with `max_size 100MB`) — and the workbench must independently enforce its own ceiling. **All upload-bearing flows (P5 import _and_ P6 ground-from-the-page) POST to `/workbench/import`** so they share this raised cap; there is no separate grounding upload route.
- **Auth — secret stays server-side (audit):** the subdomain is already gated by **Authelia `forward_auth`** ([config/caddy/Caddyfile:220-231](config/caddy/Caddyfile#L220-L231)), so the browser is an authenticated operator before any `/workbench` call. A static Quartz page **cannot safely hold a bearer** — embedding `MCP_ACCESS_KEY` in client JS leaks it. So **do not** send the shared secret from the browser. Instead, let Caddy **inject** the shared secret server-side when proxying to the workbench (`header_up X-Brain-Key {$WORKBENCH_KEY}`), mirroring how `openbrain-rest` strips/rewrites auth headers ([OB1/docker/Caddyfile:14-17](OB1/docker/Caddyfile#L14-L17)). The workbench trusts that header on `app-net` and is never host-published. (Reusing the `MCP_ACCESS_KEY` _value_ is fine; the correction is _where it lives_ — Caddy, not client code.)

### 2.4 The visibility loop (no new sync)

Writes land in OB1 / the vault → existing `source_extraction_queue` trigger +
wiki change-watch (3-min debounce) recompile → Quartz reloads. Removal reuses the
compiler's **orphan sweep**. Staged mutations (retract, grounding) decouple the
_live marker_ (hydrated, instant) from the _rendered effect_ (compile-tick commit)
— see [§4 Staged mutations](#staged-mutations-p4-retract--p6-grounding--shared-lifecycle).

### 2.5 Quartz customization model

Layer, don't fork: `OB1/docker/wiki-viewer/quartz-overlay/` (`*.tsx`, `*.inline.ts`,
patched `quartz.layout.ts`/`quartz.config.ts`) `COPY`'d over the pinned clone after
`git clone`, before `npm ci`. Keeps `QUARTZ_REF` upgradeable.

---

## 3. Cross-cutting foundations (Phase 0)

- **P0.1** `openbrain-workbench` skeleton (Deno+Hono, internal `:8000`, health, Caddy-injected `X-Brain-Key` trust, networks `obnet`+`llm-net`+`app-net`, optional debug publish `127.0.0.1:8814:8000`) → compose **+ recovery scripts + stack-map**.
- **P0.2** Caddy `/workbench/*` proxy via **`handle` (prefix-preserving, not `handle_path`)** in the **`wiki.{$PUBLIC_DOMAIN}` block of [config/caddy/Caddyfile](config/caddy/Caddyfile)** + the Tailscale `serve` path, same-origin; raised `request_body` cap on the import sub-route; Caddy injects the shared secret (see [§2.3](#23-routing-exposure-auth)).
- **P0.3** Quartz overlay scaffold + asset config; no-op component proving overlay + client `fetch()` to `/workbench/health`; confirm `assets/` served, not paginated.
- **P0.4** `wiki-assets` volume wired to workbench + viewer.
- **P0.5** Shared TS types for source/thread/membership/provenance shapes mirrored from [init-sources.sql](OB1/docker/init-sources.sql)/[init-threads.sql](OB1/docker/init-threads.sql).
- **P0.6** **Frontmatter id contract (required by every hydrated component).** The compiler bakes a **stable backing id** into the frontmatter of every page class that hydrates: **entity pages** get `entity_id` + `wiki_slug`; **notebook hubs** get `thread_id` + `slug`; **leaf pages** get `type` + `id`. Hydrated client components — the P6 grounding badge, `SourceLinker`, `NotebookPage.inline.ts` — key off these frontmatter fields, **never** off URL-parsing or guessing. Several later phases (P2/P6) silently depend on this, so it lands in P0.

**Gate:** a custom component, served through the portal, calls the authed API and
renders; an image under `assets/` renders in a page; a hydrated component reads its
backing id from frontmatter. No `sources` writes yet.

---

## 4. Feature phases

### Staged mutations (P4 retract & P6 grounding) — shared lifecycle

Both **retract** (P4) and **ground-from-the-page** (P6) are _staged mutations_: a
state change must **never hit the rendered view piecemeal**. The user gets a
reversal window before anything re-renders, a live "in progress / pending" marker
propagates across **all** references instantly (hydrated, no compile), and the
rendered effects land **all at once** when the change commits. This is written once
here and referenced from P4 and P6.

**Commit trigger (decided): autonomous — no explicit confirm gate.** Requesting the
change and _not cancelling it during the staging window_ **is** the confirmation;
there is no "Apply" button. The safety mechanism is **visibility of effects**, not a
click:

- The mutation surfaces a **gravity counter** at request time — _"linked to N
  notebooks, cited on M wiki pages"_ — so the scope is legible at the moment of
  acting.
- It writes a **`Changes` log** into the author-owned `notes/` layer (outside
  wiki-generation scope): a programmatically generated page that illustrates the
  effect of each staged change and **logs the user's action** (what changed, when,
  which links are affected). Because it lives in the existing user-note scope it is
  durable, human-readable, and never enters the generation pool.
- The user can therefore _understand the effect after the fact_ — open the affected
  links or the `Changes` log — and **reverse the stage** before the compile commits
  it. This covers the case where the user only grasps the impact after seeing the
  links.

**Lifecycle — retract:**

1. User retracts → `retracted_at` set; `retraction_committed_at` NULL (= **staged**, reversible).
2. **Instantly (hydrated, no compile):** every reference to the source — citing entity pages, the `source/<uuid>` leaf, notebook-hub source lists — shows a live **"redacted source — `in progress`"** marker. The reader sees _what the redaction affects_ before the wiki catches up.
3. **Reversal window:** clearing `retracted_at` before commit makes the markers vanish on next hydrate; **nothing regenerated**, no orphaned leaf, no wasted compile. This is the misclick / reconsideration safety.
4. **Commit (autonomous, at the compile tick that drains the change):** the source enters the generation-exclusion read-paths ([§11](#11-risks--guardrails) checklist), the leaf is swept, and citing pages regenerate with the citation gone — all at once.

**Lifecycle — grounding:**

1. User grounds (upload/URL) → source ingests via the P5 `/workbench/import` route → `source_entities` link lands → badge hydrates **"⏳ Grounding pending."**
2. **Staged, held:** the entity is _marked_ to regenerate, but the regenerated body + "Grounded by N" badge are **held** until the compile completes (they are not trickled in).
3. **Reversal window:** removing the staged link before compile reverts cleanly — no half-regenerated page.
4. **Commit (autonomous, at compile completion):** the badge flips to **"Grounded by N"** at the _same moment_ the regenerated body goes live — never before. "Grounded" is tied to **compile completion**, not to the link landing.

**Durable staging state (must survive a workbench restart):**

- Retract: `retracted_at` + **`retraction_committed_at TIMESTAMPTZ`** (NULL = staged/reversible; set = committed). Generation read-paths exclude only `retraction_committed_at IS NOT NULL`; the live "in progress" marker keys on `retracted_at IS NOT NULL AND retraction_committed_at IS NULL`.
- Grounding: `import_jobs` ([§14.4](#144--durability--import-job-state-must-outlive-a-restart)) gains a **`staged`/`committed`** distinction; the badge reads pending-vs-grounded from the job state + the compile watermark; a pull cancels the job and removes the staged `source_entities` row.

### Tombstone visibility contract (D-D)

A tombstoned (retracted) source/thought is, throughout its life:

- **excluded from the future generation pool** — every generation read-path filters it ([§11](#11-risks--guardrails) checklist);
- **retained always** — row + content survive, restorable;
- **visible but clearly marked** wherever it is _still referenced_ (a citing page, a notebook, a note) — context stays living and transparent so the user keeps control over the use of the data;
- **UX-hidden once it has no remaining links** — unreachable from any surface, though the row persists.

This tells the leaf-page template what to render: a tombstoned-but-cited
`source/<uuid>` leaf shows the marker; a tombstoned-and-orphaned leaf is simply
linked from nowhere and thus unreachable. (Retract is _eventually consistent_ on the
read surface **by design** — the live marker and the baked citation are deliberately
decoupled during the staging window; the "in progress" wording makes the transient
state legible rather than a bug.)

### Phase 1 — Provenance: Source **and** Thought Visibility

**Goal:** every inline citation on an entity/topic page — both **`[S:id]` external sources** _and_ **`[#id]` captured thoughts** — behaves like every other link in the wiki: **hover → native popover preview, click → SPA navigation** to a read page for that record. The mechanism is to make each citation a _real internal link_ to a compiled **leaf page**, so Quartz's built-in popover + SPA — plus graph nodes, backlinks, and full-text search — all apply with **no custom interaction code**.

Both citation forms come from the generator's system prompt ([generate-wiki.mjs:556-566](OB1/recipes/entity-wiki/generate-wiki.mjs#L556-L566)): **`[#id]`** = a row in `thoughts` (the user's own captured record, linked via `thought_entities`); **`[S:id]`** = a row in `sources` (an external document, linked via `source_entities`). Treated symmetrically. Because a **thought-only page** ([§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap), the majority of pages) has **no `[S:id]` sources at all**, the `[#id]` half is the higher-value one — for those pages it _is_ the entire provenance trail.

> **Page-class note (reconciles [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap)):** these leaf pages are a _distinct page class_ — read-only provenance records, **not** entities. They don't enter the entity graph or candidate selection, so the "pages come from the entity graph" model is untouched; this only gives each cited record a viewable address.

- **Compiler (the load-bearing change — [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) + [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs)):**
  - **Emit bounded leaf pages** — `content/thought/<id>.md` and `content/source/<id>.md`, **only for ids actually cited** this compile (collected from the `provenance.linked_ids`/`semantic_ids`/`source_ids` sets the generator already builds, [generate-wiki.mjs:542-544](OB1/recipes/entity-wiki/generate-wiki.mjs#L542-L544)). Bounded by _citations_, not the DB — 800 cited thoughts → 800 leaves, not 50k.
  - **Batch-fetch full content** by id (`thoughts?id=in.(…)` / `sources?id=in.(…)`) for the leaf bodies — the synthesis payload only carries 300-char snippets. Leaf frontmatter: `type: thought|source`, `id`, date, `metadata.type`; sources add `url`/`title`/`content_type`/`notebook`. (The `type` + `id` frontmatter satisfies the P0.6 id contract.)
  - **Rewrite inline citations into wikilinks** — post-process generated pages: `[#11173]` → `[[thought/11173|#11173]]`, `[S1]` → `[[source/<uuid>|S1]]` (the **per-page token** `S1` is resolved to its real UUID via the token→UUID map — see the id-shape note below). A token with no emitted leaf (genuine mis-cite) is **left as plain text**, mirroring broken-`[[wikilink]]` handling. The citation now _is_ a wikilink — same object, same behavior as the rest of the wiki.
    - **⚠️ Id-shape reality (audit) — token mapping is the citation path:** `thoughts.id` is **`BIGSERIAL`** ([init.sql:9](OB1/docker/init.sql#L9)) — small integers an LLM reproduces reliably, so `[#id]` stays **literal**. **`sources.id` is `UUID`** ([init-sources.sql:23](OB1/docker/init-sources.sql#L23)) — and an LLM transcribing a 36-char UUID verbatim has a high error rate, so **the model never sees or emits UUIDs.** Instead, `buildSynthesisInput`/`synthesize` present sources under a **short stable per-page token** (`S1, S2, …`) mapped to the real UUIDs in the structure payload; the model emits only `[S1]`/`[#11173]`. **The citation regex matches `S\d+` and `#\d+` only — there is no UUID matching anywhere in the rewrite.** The deterministic rewrite resolves the token→UUID via the per-page map (`S1 → [[source/<uuid>|S1]]`) and `#11173 → [[thought/11173|#11173]]`. An emitted token with **no resolvable leaf** (a genuine mis-cite, now rare) falls back to plain text. _This is the single citation path; there is no UUID-regex variant — the token map exists precisely so source-leaf coverage is **not** lossy._
  - **Orphan-sweep — dedicated leaf sweep, NOT the existing entity sweep (audit — data-loss bug otherwise):** the current `sweepOrphanEntityPages` ([wiki-service.mjs:457-506](OB1/docker/wiki-service/wiki-service.mjs#L457-L506)) builds its kept-set from **entity slugs only** and `listEntityFiles` ([wiki-service.mjs:424-450](OB1/docker/wiki-service/wiki-service.mjs#L424-L450)) walks **every** `content/<dir>/` except `topic/`. So if leaves land in `content/thought/` and `content/source/`, the existing sweep would delete **every leaf on the very next compile** (no leaf id is in the entity kept-set). Required: (a) add `thought/` and `source/` to the `listEntityFiles` skip-list exactly as `topic/` is skipped, and (b) add a **new `sweepOrphanLeafPages`** keyed on the set of ids actually cited this compile (the union of `provenance` id sets across all generated pages), removing only leaves whose id is no longer cited. "Reuse the existing sweep" is incorrect.
  - **Untrusted-content guard** — leaf bodies render captured / external text: keep the scrub ([generate-wiki.mjs:597-610](OB1/recipes/entity-wiki/generate-wiki.mjs#L597-L610)) and rely on Quartz's markdown→HTML sanitization; this text is untrusted at _render_ time, not just as LLM input.
- **Quartz (mostly native — no custom interaction component):**
  - Hover-popover + click-navigate come **free** from stock Quartz (this v4.5.1 build runs SPA + popovers). Citations also gain graph nodes, **backlinks** ("which wiki pages cite this record"), and full-text search automatically.
  - **Leaf-page template** — a small overlay layout keyed on `type: thought|source` (metadata header + body + backlinks) so leaves read as records, not orphans. Renders the **tombstone marker** when the backing source is retracted-and-still-cited (per the tombstone visibility contract).
  - **`ProvenancePanel.tsx` is now optional** — a consolidated per-page provenance index on top of the generator's existing `## Sources` section; a nice-to-have, no longer load-bearing since inline links + backlinks already deliver traceability.
- **Backend (read) — not required in P1:** the static leaf pages serve the read view, so P1 needs **no** workbench endpoint on its hot path. `GET /workbench/thoughts/:id` / `…/sources/:id` are still built for the **write/live** phases (P4 edit, P6 linking) but aren't a P1 dependency — **P1 is essentially a compiler-only feature.**
- **Gate:** open a **thought-only** page → its `[#id]` markers are real links → hover shows a native popover of the captured thought, click navigates to its `thought/<id>` leaf (showing "cited by" backlinks); a **sourced** page → `[S1]` does the same to a `source/<uuid>` leaf; an uncited/unknown token stays plain text (no broken link); after a citation is removed, the next compile sweeps the now-orphan leaf. Behavior is indistinguishable from any `[[wikilink]]`.

### Phase 2 — Notebooks & Membership (research groups)

_The core organizing axis — see [§5](#5-notebooks--research-groups-the-core-organizing-axis) for the full design._
**Goal:** surface **notebooks** (user-facing name for `threads` rows) as the organizing layer — a notebook index, one **notebook hub page** per notebook, and add/subtract membership (M:N, non-exclusive).

- **Naming:** user-facing surfaces (pages, UI labels, routes) say **"Notebook"**; the persistence layer keeps `threads`/`thread_sources` (a renaming of a live table with a suggestion worker + sessions FK pointed at it buys nothing). One documented seam: _a Notebook **is** a `threads` row._
- **Schema (additive — pin the slug):** `ALTER TABLE public.threads ADD COLUMN IF NOT EXISTS slug TEXT;` + `CREATE UNIQUE INDEX IF NOT EXISTS uq_threads_slug ON public.threads(slug);`. The workbench generates the slug **once at create time** (shared slug module §14.1), de-collides on the `UNIQUE` violation (`-1/-2`, mirroring [`resolveOutputPath`](OB1/recipes/entity-wiki/generate-wiki.mjs#L815)), and **never recomputes it** — rename touches `name` only, and the hub page emits `aliases: [name]` so `[[Notebook Display Name]]` keeps resolving. (Same pin-for-life contract entity `wiki_slug` has.)
- **One hub page per notebook (folds in the old `topic/`):** the compiler emits `content/notebook/<slug>.md` carrying frontmatter `thread_id` + `slug` (P0.6 contract) and **(1)** a `## Synthesis` section — the existing notebook synthesis ([synthesize-notebooks.mjs](OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs)) writes _here_ instead of a separate `topic/<slug>.md`; **(2)** `## Sources` (`thread_sources.status=confirmed`, with P1 provenance); **(3)** `## Notes` listing/linking `notes/<slug>/*`; **(4)** a `## Suggestions` triage strip; plus a scoped graph + backlinks. This is the discovery hub — opening it surfaces the user's own `notes/<slug>/` folder, and every note backlinks `[[notebook/<slug>]]` (see [§5.2](#52-how-notebooks-appear-in-quartz)).
  - **Bake-vs-hydrate (resolved — reconciles §5.2 and §12.4):** the compiler bakes a **shell + synthesis + static scoped graph** (`## Synthesis`, title/description, the static graph node set). The **live sections** — `## Sources`, `## Notes`, `## Suggestions`, and the membership actions — are **hydrated by `NotebookPage.inline.ts`** from the workbench so add/remove reflects instantly with no recompile wait, and **degrade to a baked fallback list** if the API is down. **Precedence:** the hydrated view is authoritative when reachable; the baked fallback is a last-compile snapshot, clearly labeled stale. There is exactly one source of truth at read time (the live fetch); the bake exists only for offline-degrade. P1 provenance on hydrated sources is delivered by the client component linking to the same `source/<uuid>` leaves.
- **Backfill (unifies the two notebook populations):** for every distinct `metadata.notebook` string on a source/thought with **no matching `threads` row**, the compiler auto-creates one (slug pinned). Guarantees every notebook — whether born from a research run's free-text tag or a user-created `notes/` folder — has exactly one discoverable hub. No hidden parallel notebooks.
- **Backend:** notebook CRUD (`GET/POST/PATCH /workbench/notebooks` → `threads`), `POST /workbench/notebooks/:id/sources` (link via `link_source_to_thread`), `DELETE` (unlink via `set_thread_source_status → hidden`), suggestion triage (`accept`/`hide`). **Triaged `hidden`/`inactive` links suppress re-suggestion** — the suggestion worker must not re-propose a pair the user has triaged away (see [§11](#11-risks--guardrails)).
- **Compiler:** generate `content/notebook/<slug>.md` per active notebook from `threads` + `thread_sources(status=confirmed)` + backfill; notebook nodes into `graph.json`. Add `notebook/` to the `listEntityFiles` sweep skip-list (it has its own kept-set, like `topic/` did).
- **Quartz:** `NotebookIndex.tsx`, `NotebookPage.tsx`, `MembershipPicker.tsx` (+ `.inline.ts`); backlinks/graph leveraged (see §5).
- **Gate:** create a notebook (slug pinned), rename it (page + links survive), add a source from two notebooks (proves non-exclusive), unlink from one (still in the other), accept a worker suggestion, hide one and confirm it isn't re-proposed next run, and confirm a hand-created `notes/<slug>/` folder shows under the hub's `## Notes` — all reflected after recompile (or instantly for hydrated membership).

### Phase 3 — Notes System (authored layer: human + AI — D-E)

**Goal:** author Obsidian-style notes **in Quartz** (live preview, `[[wikilinks]]`, tags, notebook grouping), written additively into OB1 — and accept notes **emitted by AI assistants** (research pipelines, Open WebUI, other chat services) into the same layer for the human to build on. **Note-driven linking is the primary deliberate way a user connects sources into a thread** (see [§5.3](#53-add--subtract-membership--options-explored-operators-q3)).

- **Reuse:** `ingestNotes()` already maps `notes/<notebook>/file.md` ⇄ a thought; we add the **browser editor that writes those files**. Align the `notes/<notebook>/` folder = the pinned **notebook slug** so notes group under their notebook hub ([§5](#5-notebooks--research-groups-the-core-organizing-axis)).
- **Natural linking (the deliberate path):** a **human note** containing `[[notebook/<slug>]]` and `[[source/<slug>]]` _is_ how the user deliberately links a source into a thread — the user literally writes the connection; the compiler materializes the membership and Quartz's backlinks/graph show it. Because a source can belong to many threads, the user's own notes are what generate the deliberate thread↔source connection.
- **AI notes from Open WebUI research chats (the concrete case + the diagnostic loop):** OWUI conversations are where much of the research that _derives sources_ happens — the user's problem statement, LLM-driven source gathering/discovery, then a synthesized response over those sources. That **synthesized response is an AI note on its subject.** P3 lands those syntheses as `ai_note`s **attached to their AI notebook (thread)**, carrying the gathered sources, so the AI notebook accumulates {problem statement → gathered sources → synthesis}. **The user's own notebook then links _across_ to those AI notes and their sources via query** (it doesn't own them), enabling the [§0 diagnostic loop](#0-decisions-locked-for-this-plan): the user cross-references a prior mental-model thought against the independent LLM research effort. (A small OWUI→workbench `PUT /workbench/notes` hand-off writes the synthesis into the AI-notebook folder; the existing notes ingest tethers it.)
- **Provenance:** stamp `metadata.source = user_note | ai_note` (+ originating agent/chat for AI notes) so the hub and search can distinguish human vs assistant authorship; both tether to thoughts identically. **Notes are not part of the generation pool** — they are the authored layer the user reads/builds from, never demoted facts in a wiki page.
- **Backend:** `PUT/GET /workbench/notes/<path>` (path validated under `notes/` — one shared no-`../`-escape validator per §14.3, write+`git commit`, optimistic concurrency via content-hash/`If-Match`); notes index. AI-emitted notes use the **same write path** (one ingestion surface, not a parallel one).
- **Quartz:** `NotesEditor.tsx` + `.inline.ts` — editor, `[[…]]` autocomplete from `entities.md` + the notebook index (so authors pick an **existing** notebook rather than fat-finger a near-duplicate slug), tags.
- **Decision (idea-doc Q1):** notes stay in the `notes/` layer tethered to thoughts — not a separate `user_notes` collection.
- **Gate:** create/edit a note (human and AI-authored) → appears in vault under its notebook, links resolve, next compile tethers + extracts and the note shows under the notebook hub's `## Notes`; a human note with `[[source/x]]` materializes the thread↔source link; an OWUI synthesis lands as an `ai_note` on its AI notebook and the user notebook can query across to it; two-session conflict detected.

### Phase 4 — Source Lifecycle: Update-with-history + Retract/Restore (D-D)

**Goal:** sources are added as-is but the user can **update them (history preserved)** and **remove them reversibly (retain + restore)** — removal must not contaminate future wiki generation, but the data is kept so the user can bring a source back later. Updating is **never** replacing.

- **Update = in-place, never replace (mental-model fix):** editing **updates this same source** (`source_id` never changes); each edit snapshots prior `content`/`title` into an append-only **`source_revisions(source_id, revision, content, title, edited_at, edited_by)`**, then updates `sources.content` (current = head; history = revisions). The UI offers **no "replace source" affordance** — _"a better source exists"_ is **not** a reason to overwrite _this_ source; it means **add a new source** (a separate, prominent action). The edit action is labeled **"Update this source / record a revision."** Re-embed is automatic via the existing fingerprint-gated queue trigger; metadata-only edits must not bump the content fingerprint. The source view shows version history + diff.
  - **URL sources — queue a re-fetch:** offer **"Re-fetch from source"** → enqueues a job back to the ai-stack to re-pull the live URL (if still available); the result lands as a **new revision** on the same row (history preserved), never a head overwrite and never a duplicate source.
  - **Document sources — manual update:** no upstream to re-fetch, so updating is a manual re-upload landing as a new revision against the same `source_id` (freshness is the user's responsibility to keep consistent).
  - **Re-import collision policy (resolves the dedup question):** a research pipeline re-importing an existing URL hits `find_or_create_source` dedup → the re-fetch **appends a new `source_revisions` entry on the same row, linearly** (the re-fetch becomes the new head; the user's prior edit is preserved as revision N). It **never** overwrites the head silently and **never** forks a duplicate. One canonical head invariant (§12.1) holds.
- **Three distinct removal verbs (keep them visually separate so the user never confuses scope):**
  1. **Unlink from notebook** (membership, per-notebook) — `set_thread_source_status → hidden`. Source stays in its other notebooks and in generation. _Not_ a deletion.
  2. **Retract** (global, **reversible — the default "remove"**, and a **staged mutation** per [§4 Staged mutations](#staged-mutations-p4-retract--p6-grounding--shared-lifecycle)) — additive `sources.retracted_at TIMESTAMPTZ` + `retracted_by TEXT` (+ `retraction_committed_at`). The **row and content are retained and restorable**, but the source becomes **invisible to all wiki generation/linking** (on commit) so it can't contaminate future generations. While staged, all references show a live **"redacted source — `in progress`"** marker; the user can reverse before the compile commits. **Restore** = clear `retracted_at`; its `source_entities`/`thread_sources` rows are still intact, so it lights straight back up.
  3. **Purge** (global, **irreversible**) — `DELETE FROM sources` → `source_entities`/`thread_sources`/`session_sources`/`source_revisions`/`source_chunks` cascade → orphan sweep removes unsupported pages + assets. A rare, explicit operator escape hatch, **not** the normal remove. **Purge always requires explicit confirm** (it is not a staged mutation — it's irreversible).
- **⚠️ Tombstone filtering — every generation read-path must exclude committed retracts (audit; miss one and tombs resurface):** each filters `retraction_committed_at IS NOT NULL` (equivalently `retracted_at IS NULL OR retraction_committed_at IS NULL` to keep staged rows visible to the _live marker_ but out of generation):
  - `fetchLinkedSources` ([generate-wiki.mjs:435](OB1/recipes/entity-wiki/generate-wiki.mjs#L435)) — the main source→page join
  - `listBatchCandidates` source-count ([generate-wiki.mjs:324](OB1/recipes/entity-wiki/generate-wiki.mjs#L324)) — so a tomb can't keep a page alive
  - `match_sources` + new `match_source_chunks` RPCs ([init-sources.sql:78](OB1/docker/init-sources.sql#L78))
  - notebook synthesis source pulls ([synthesize-notebooks.mjs](OB1/recipes/wiki-synthesis/scripts/synthesize-notebooks.mjs))
  - P1 source-leaf emission + the leaf-sweep (a committed-retracted source's `source/<uuid>` leaf is swept once orphaned; a still-cited one shows the tombstone marker)
  - the extraction queue (don't re-extract a tomb — it must stop producing fresh `source_entities`)
  - _This list is the P4 regression-test checklist._
- **Backend:** `PATCH /workbench/sources/:id` (versioned update), `POST …/:id/refetch` (URL re-fetch → new revision), `GET …/revisions`, `POST …/:id/retract {scope: notebook|global}` (notebook→status flip, global→staged `retracted_at`), `POST …/:id/restore` (clear retract, also reverses a staged retract), `DELETE …/:id` (purge, operator-confirmed).
- **Quartz:** `SourceEditor.tsx` (inline **update** editor labeled "Update this source", version history/diff, "Re-fetch from source" for URLs, **no replace affordance**, prominent separate "Add new source"), `SourceRetractor.tsx` (unlink-from-notebook vs retract-globally vs purge; **gravity counter** in the confirm dialog — _"linked to N notebooks, cited on M pages"_ — default to retract, purge gated behind explicit confirm; present all three scopes regardless of entry point so retract isn't the only verb visible on a source view).
- **Gate:** update → new revision recorded, old preserved, re-embed enqueued, dependent pages refresh; URL re-fetch → new revision, head replaced, history intact; **retract → staged marker propagates live across all references; reversing before commit regenerates nothing; on commit the source vanishes from every generation read-path but the row survives; restore → it returns with links intact**; purge cascades + sweeps; purge always needs explicit confirmation; the gravity counter shows correct N/M before retract.

### Phase 5 — Direct Source Import Pipeline (incl. images — D-F)

**Goal:** drag-and-drop / picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT, **images** (and **audio/video** via STT) → extract → chunk → embed → land as first-class sources, linked to a chosen thread, with progress + errors; images render in Quartz.

- **`openbrain-extract` sidecar (correctness-first, all formats — §12.3) — built to expand:** stable `POST /extract` → `{ markdown, title, metadata, pages, images[] }` over a **format→extractor registry** (PDF: PyMuPDF/Docling-class; DOCX/PPTX: python-docx/-pptx; images: Pillow+Tesseract OCR; audio/video: STT at `host.docker.internal:8000/v1`; content-core where it extracts most faithfully). The registry shape matters: **a future format (epub, html, eml, spreadsheets…) is a new registry entry behind the unchanged `/extract` contract, not a caller change** (the import pipeline, workbench, and Quartz never learn about new formats). **Per-format extraction-quality acceptance gate** (text fidelity, tables, headings, image refs). Add to compose **+ recovery + stack-map**.
- **Workbench `POST /workbench/import` (async, transactional) — the single upload route (shared by P6 grounding):** store upload → extract → write images to `assets/<source-id>/` + rewrite refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(..., 'deliberate')` to the selected notebook **and/or** `link source_entities` to `target_entity_ids` when present (the P6 grounding parameterization) → `job_id`; `GET /workbench/jobs/:id` for progress. Source + chunks + links land in **one deno-postgres transaction** (§14.5) so a mid-sequence failure can't leave a source with no chunks/links.
- **Schema (additive — §12.2):** a `content_types` reference table, **`source_chunks`**, a chunk search RPC, and durable **`import_jobs`**:
  - **`content_type` → reference table (not a CHECK), for expandability.** Replace the inline CHECK ([init-sources.sql:27-30](OB1/docker/init-sources.sql#L27-L30)) with `content_types(value TEXT PRIMARY KEY, label TEXT, category TEXT, created_at TIMESTAMPTZ DEFAULT now())` and an FK `sources.content_type → content_types(value)`. A new format becomes **one `INSERT`, no DDL**. Migration order on the **live** DB matters: create table → **seed every value already in `sources`** (the existing 7) **+ the new ones** (`docx,pptx,image,audio,txt,md`) → drop the old CHECK → add the FK (FK-add fails if any existing value is unseeded). `find_or_create_source`'s `'web_article'` default stays valid.
  - **`source_chunks(source_id UUID, idx INT, content TEXT, embedding VECTOR(1024), PRIMARY KEY(source_id, idx))`** (confirmed — long-doc retrieval _and_ the source list podcasts build from), `source_id … REFERENCES sources(id) ON DELETE CASCADE`. Search RPC named **`match_source_chunks`** (don't overload `match_sources` — [init-sources.sql:78](OB1/docker/init-sources.sql#L78)).
  - **`import_jobs(id, status, source_id, target_entity_ids, target_notebook, error, staged, committed, created_at, updated_at)`** (§14.4) — persists job state so a workbench restart doesn't orphan in-flight imports, backs `ImportStatus.tsx` history, carries the **`staged`/`committed`** distinction the P6 grounding badge reads, and records the **target links + terminal error** of an upload-and-link / grounding attempt for the later alerts surface.
- **Quartz:** `ImportDropzone.tsx` (validation, per-file progress, errors, **"link to wiki page(s) / notebook" target field** feeding `target_entity_ids`/`target_notebook`) + `ImportStatus.tsx`; notebook selector reuses `MembershipPicker`.
- **Gate:** drop a PDF, DOCX, PNG (and an MP3) → all extract/transcribe, chunk, embed, dedupe, link to the chosen notebook, entity-extract, surface via P1 provenance and on the P2 notebook hub; images render inline; corrupt files fail clearly.

### Phase 6 — Source Grounding & Deliberate Wiki Linking (D-J)

_Closes the [§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap) gap. Builds on P1 (provenance), P4 (source update options), P5 (import)._

**Goal:** turn a **thought-only page** (an _unverified mental model_ resting solely on
the user's captured belief) into a **source-grounded entity** by attaching a source
_from the page being read_, regenerating it so source-backed facts carry the claims.
This is a **deliberate user action to increase the legitimacy of a claim** — adding
evidence to ground a belief in truth, rather than going along with a self-proclaimed
thought or an LLM claim.

> **Conceptual frame (so the mechanism is unambiguous):** a wiki page is an
> **entity** synthesized from many thoughts/edges/sources — not a single thought.
> A thought is the user's _belief_; technically the thought has an origin, but it is
> **not grounded** until the user attaches a real document/URL to its claim.
> "Grounding" attaches a source to **that entity**; regeneration re-synthesizes the
> whole page, now able to assert source-backed facts and reframe the earlier belief
> as unverified. _Example:_ "Project Aurora" is thought-only and says _"launches
> Q2"_ (your belief). You ground it with the project doc (_"launches Q3"_);
> regeneration → the page asserts **"Launches Q3 [S1]"** and the _Q2_ belief is shown
> as a superseded assumption. The **evolution** view records the transition.

- **Grounding state is LIVE, read at the moment of reading (hydrated via the P0.6 frontmatter `entity_id`):** because the user is reading the page _right now_, the badge must reflect the true current state, accurate between compiles. The in-Quartz component hydrates from the workbench and shows one of:
  - **"Mental model — ungrounded belief"** (no sources; rests on your thought) — paired with the CTA **"Ground this claim with a source."** This is an _invitation to deliberately legitimize the belief_, **not** an "incomplete page" notice; thought-only pages are by-design and carry graph value ([§1.4](#14-why-wiki-pages-appear-without-sources-the-grounding-gap)). Grounding is **optional and user-driven**.
  - **"⏳ Grounding pending"** (a source is linked/ingesting; page not yet regenerated — the staged state),
  - **"Grounded by N source(s)"** (regenerated and citing ≥1 source — set only at compile completion, not at link-landing),
  - **"⚠ Ingest failed"** (a grounding attempt failed — see failure handling below).
  - `GET /workbench/grounding` also surfaces `source_extraction_queue` health so a **backlog** page (§1.4 cause 2) is never mislabeled as **ungrounded by-design** (cause 1).
  - Compiler policy: **badge** thought-only pages, never suppress them — they still carry graph value (D-J).
- **Ground-from-the-page (the core feature):** on the live wiki page the user sees **"Ground this claim with a source"** and supplies **a document (upload) or a URL (ingest)**. **The upload POSTs to the P5 `/workbench/import` route** (the only route with the raised `request_body` cap — §2.3) **with `target_entity_ids` set** — it is a _parameterization_ of import (import + entity-link target), **not** a separate grounding route (a grounding-specific route would hit the default 1 MB cap and fail at the proxy). The source is ingested and linked to **this page's entity** — a marked `source_entities` row (entity-level grounding; distinct from notebook-level `thread_sources` membership, which is P2). On success the entity is marked to regenerate; `fetchLinkedSources` includes the new source → the page **regenerates and flips to "Grounded"** (at compile completion — see [§4 Staged mutations](#staged-mutations-p4-retract--p6-grounding--shared-lifecycle)).
  - **Notebook visibility of grounding sources:** a ground-from-page source gets a `source_entities` row; to keep it visible on the **core organizing axis**, the `ImportDropzone` target field defaults a notebook (the page's primary notebook if resolvable) so the source also lands in `thread_sources` and appears on a hub — avoiding sources that exist only as an entity link outside any notebook.
  - **Generation policy on a grounded page (decision: sources = facts, thought-claims = reframed, not deleted):** once the page has ≥1 source, the regenerated page lets **sources carry the asserted facts** and reframes the _thought-derived_ claims under a clearly-labeled _"Working hypotheses / unverified"_ framing rather than dropping them. _(This operates only on thought-derived synthesis claims — **notes are not in the generation pool and are never demoted**.)_ This honors "the original belief is a mental model awaiting verification" **without** destroying information — important because thought-only entities are the majority of pages (§1.4). _(Not the lossy "suppress thoughts entirely" variant.)_
  - **⚠️ Schema reality (audit):** `source_entities` ([init-source-graph.sql:27-35](OB1/docker/init-source-graph.sql#L27-L35)) has columns `source_id, entity_id, mention_role, confidence, evidence, created_at` and **PK `(source_id, entity_id)`** — there is **no `metadata` column**, so "plus a `metadata` flag" as written is impossible. Two additive fixes, both in scope under the additive-only guardrail:
    1. **Marker:** `mention_role='user_linked'`, `confidence=1.0`, `evidence='manual:<operator>@<iso8601>'`. Optionally `ALTER TABLE public.source_entities ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb` if a richer flag is wanted — but `mention_role` alone suffices and avoids a column.
    2. **PK-collision decision (must be stated):** because the PK is `(source_id, entity_id)`, a manual `user_linked` row and an auto `mentioned` row for the **same pair cannot coexist**. Policy: **`user_linked` wins** — the worker's re-extraction upsert must `ON CONFLICT (source_id,entity_id) DO NOTHING` (or merge) when the existing row is `user_linked`, never overwriting the manual mention_role back to `mentioned`.
- **Failure handling (ingest can fail — bad URL, unparseable doc):** on failure, **do not regenerate the entity** (nothing new to cite). Instead the live component marks the attempt on the page **client-side** (the "⚠ Ingest failed" state, hydrated — no recompile), and the failure is recorded durably in `import_jobs` (terminal `status=failed` + `error` + `target_entity_ids`). That captured data feeds a later **alerts/indicator surface** (a "grounding attempts that failed" list) — _capture now, surface later_; only a **successful** ingest triggers the entity's regeneration.
- **Evolution / timeline (P6b — derive, don't snapshot):** the entity's grounding history is **derived for free** from existing timestamps — each `source_entities.created_at` (when each grounding source attached) plus the entity's first-seen — rendered as a `## Evolution` section, complemented by the **vault's git history** (every compile is a commit, so each page already carries a full diff trail). **No new storage.** This works on **today's** vault git (the `openbrain-wiki-data` volume repo) **right now** — it does **not** depend on the self-hosted git server, which isn't built yet; when D-I later migrates the vault to a locally-owned git host ([§9](#9-storage--the-vault-direction)) the same derivation keeps working unchanged. _(Per-version page-body snapshots are deferred future scope.)_
- **Upload-and-link entry point:** the same flow is also reachable from the P5 `ImportDropzone` with a **"link to wiki page(s) / notebook"** target field, so a _new_ source is ingested **and** linked to chosen entities/notebooks in one action.
- **Worker change (required):** the entity-extraction worker must **not delete `user_linked` `source_entities` rows** on re-extraction (see [§11](#11-risks--guardrails) for the concrete `index.ts:748` change + the `user_linked`-wins PK policy). Guard + test: a re-extraction cycle must leave the manual link intact.
- **Quartz:** `SourceLinker.tsx` (entity/page picker on a source view + in the editor), the live grounding-state badge + `## Evolution` in `ProvenancePanel`, "Ground this claim with a source" on the entity page, "link targets" field in `ImportDropzone`.
- **Scope note:** entity pages first (the common case). Notebook-hub synthesis pages (e.g. autobiography) are generated differently — grounding for those is a follow-up.

**Gate:** from a thought-only page, "Ground this claim" with a URL/doc → it
ingests (via `/workbench/import` + `target_entity_ids`), links to the entity, the
badge shows "⏳ Grounding pending", and at the next compile the page regenerates
citing it with sources-as-facts / thought-claims reframed and the badge flips
**Mental model → Grounded** _together with the new body_; pulling the staged link
before compile reverts cleanly; a **failed** ingest shows "⚠ Ingest failed" on the
page **without** recompiling it and lands a `failed` `import_jobs` row; the manual
link survives a re-extraction cycle; a backlog page shows "⏳ pending", not a false
"ungrounded"; the `## Evolution` section shows the grounding transition.

---

### Phase 7 — Podcast Service (DEFERRED — D-B/D-H)

\*Large feature; built later. **Open Notebook remains available and provides podcasts until this ships.\*** Recorded here so the architecture leaves room.

- **Generation on request, per thread:** pull the thread's sources (via `source_chunks`) → script via local Qwen (`llama-cpp`) → audio via **existing TTS** at `host.docker.internal:8000/v1` (several voices) → `assets/podcasts/<id>.mp3` + transcript.
- **Schema (additive):** `podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)` — "organized by thread" for free.
- **Settings:** a **config options panel mirroring ON's UI** (speaker count 1–4, voice selection from the TTS service, style/length). Stored per-notebook or global default.
- **Transcript → note:** reuses the P3 notes write path (editable, tethered).
- **Quartz:** `PodcastPanel.tsx` — Podcasts section (global + per-notebook) with an `<audio>` player; "Generate podcast for this notebook"; "Save transcript as note".
- **Deferral note:** because ON covers podcasts meanwhile, no `openbrain-podcast` container is built in P1–P6. When P7 starts, prefer a thin caller of the existing TTS over a heavy `podcast-creator` dependency unless multi-speaker scripting needs it.

---

## 5. Notebooks / research groups — the core organizing axis

_Answers the operator's questions on how notebooks are organized and how membership is managed. "**Notebook**" is the user-facing noun; the backing table is `threads`/`thread_sources` (see [Phase 2](#phase-2--notebooks--membership-research-groups))._

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
`topic/` synthesis with membership/notes/triage. Per the **bake-vs-hydrate
resolution** in [Phase 2](#phase-2--notebooks--membership-research-groups), the
compiler bakes the shell + `## Synthesis` + static graph; the live sections below
are hydrated by `NotebookPage.inline.ts` (degrading to a baked fallback if the API
is down):

- a `## Synthesis` section (the folded-in notebook synthesis, formerly `topic/<slug>.md`) — **baked**,
- its **sources** (`thread_sources.status=confirmed`) with P1 provenance — **hydrated**,
- its **notes** — `notes/<slug>/…`, both human and AI-authored — listed/linked under `## Notes` — **hydrated**,
- a **scoped graph** (notebook node + its sources/entities, baked) and a **backlinks** panel — _this is where Quartz earns its keep_,
- a **suggestion triage** strip (pending cross-notebook links → accept/hide) — **hydrated**,
- later, its **podcasts** (P7).

Plus a **notebook index** (`NotebookIndex.tsx`) of all active notebooks in the nav.

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

| Option                                                           | How it works                                                                                                                                                                                                                                                                | Best for                                                                                                                      | Trade-off                                                                                                                      |
| ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **A — ON-style picker**                                          | `MembershipPicker` on a source view / notebook hub; multi-select notebooks; calls `link_source_to_thread` (add) / `set_thread_source_status→hidden` (subtract).                                                                                                             | **Sources** (read-mostly DB rows with no markdown body to link in).                                                           | Explicit, familiar, but a separate UI gesture.                                                                                 |
| **B — Obsidian-style wikilinks (the _primary deliberate path_)** | A **note** contains `[[notebook/<slug>]]` and `[[source/<slug>]]`; the compiler materializes the membership and Quartz's backlinks/graph show it. The user literally writes the connection. Optionally a `notebook:` tag bulk-links.                                        | **The user's deliberate linking** — notes are real markdown files, native to Quartz, and a source can belong to many threads. | Indirect for read-mostly DB rows, so the picker still covers pure sources.                                                     |
| **C — Hybrid + triage (recommended)**                            | Note-driven wikilinks for the user's deliberate links; picker for read-mostly sources; **plus** the suggestion-worker proposing links (status `pending`) surfaced as a triage queue on the notebook hub (accept→confirmed, hide→hidden, **hide suppresses re-suggestion**). | Everything.                                                                                                                   | Slightly more UI, but matches how each object type actually behaves and uses Quartz's graph/backlinks where they're strongest. |

**Recommendation: C, with note-driven linking (Option B) as the _primary
deliberate path_.** The natural, Obsidian-native gesture is the user **writing the
connection in their own note** — a human note containing `[[notebook/<slug>]]` /
`[[source/<slug>]]` _is_ how a source is deliberately linked into a thread; the
compiler materializes it and backlinks/graph show it. Sources still get the
explicit **picker** for the read-mostly case with no markdown body; the
**suggestion worker** fills the long tail and the user triages (and a triaged
**hide is sticky** — not re-proposed). **AI notes join their AI notebook on
arrival:** an OWUI synthesis lands as an `ai_note` attached to its thread (the "AI
notebook"), carrying the gathered sources. **The user's own notebook links _across_
to AI notes and their sources via query** rather than owning them — this is the
[§0 diagnostic loop](#0-decisions-locked-for-this-plan) (user's mental-model thought
↔ user notebook ↔ query ↔ AI notebook's independent research). "Unlink from
notebook" is always a **soft status flip** (the source stays in its other
notebooks) — categorically different from a global **retract** or **purge** (P4).

### 5.4 Where Quartz is uniquely helpful here

Graph view (notebook + source/entity nodes), backlinks (every note referencing a
notebook), notebook-scoped full-text search, and `[[ ]]` autocomplete make
notebooks feel like Obsidian while membership stays in Postgres as the single
source of truth. **Note:** the _instant_ Obsidian-feel applies to the user's own
note authoring + `[[link]]` autocomplete (client-side); the dynamically-generated
**wiki summaries** and **knowledge-graph backlinks** are net-new capability Obsidian
doesn't offer at all, and their only delay is the compile — not a regression
against Obsidian.

---

## 6. Design decisions — consolidated

| Question                                      | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | Rationale                                                                                                                                                                        |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Notes vs sources storage                      | Notes (human + AI-authored) in `notes/<notebook-slug>/` tethered to thoughts. **Deliberate source↔thread linking is primarily note-driven** (`[[notebook/x]]`/`[[source/x]]` in the user's own notes); picker for sources; suggestion worker for the long tail (hide is sticky). **AI notes join their AI notebook** (OWUI synthesis → `ai_note` on its thread, with gathered sources); **the user notebook links across to AI notes/sources via query.** Notes are **not** in the generation pool. | Note-driven linking is the natural Obsidian gesture; AI-notebook accumulation + cross-query enables the diagnostic loop (§0).                                                    |
| Notebook = thread (naming)                    | User-facing noun **Notebook**; backing table stays `threads`. One **hub page** per notebook folds in the old `topic/` synthesis; slug **pinned**, name renameable; legacy free-text notebooks **backfilled** to rows. Hub = baked shell+synthesis + hydrated live sections.                                                                                                                                                                                                                         | "Notebook" reads better; renaming a live table buys nothing; one hub = discoverable; hydration = instant membership.                                                             |
| Source editing/versioning (D-D)               | **Update-in-place, never replace**; one canonical row (head) + append-only `source_revisions` history; `source_id` never changes (§12.1). No "replace source" UI ("better source" = **add new**). URL sources offer **queued ai-stack re-fetch → new revision**; re-import of an existing URL appends a revision (linear), not a head overwrite or duplicate.                                                                                                                                       | Operator wants edits _with_ history and to avoid the wrong "replace this source" mental model; stable id keeps thread links/search valid; re-fetch automates same-URL freshness. |
| Source removal (D-D)                          | Three verbs: **unlink** (per-notebook status flip) · **retract** (global, reversible **staged** tombstone via `retracted_at`/`retraction_committed_at`; retained + restorable; invisible to generation on commit) · **purge** (irreversible `DELETE`, rare, explicit confirm). Retract is the default; gravity counter shown.                                                                                                                                                                       | Removed sources must not contaminate future generations but stay restorable; staging gives a reversal window; M:N means unlink ≠ remove.                                         |
| Staged mutations (retract + grounding)        | Autonomous commit (request + not-cancel = confirm), live "in progress/pending" marker propagated instantly, effects held then pushed at compile-tick; reversal window; effects shown via gravity counter + a `Changes` log in the user-note layer. (§4)                                                                                                                                                                                                                                             | A misclick/reconsideration must be reversible before render; visibility of effects replaces a confirm gate.                                                                      |
| Tombstone visibility                          | Excluded from generation throughout; retained always; visible-and-marked while referenced; UX-hidden when unreferenced. (§4)                                                                                                                                                                                                                                                                                                                                                                        | Living, transparent context under user control without contaminating generation.                                                                                                 |
| Grounded-page generation policy (P6)          | Sources carry asserted **facts**; _thought-derived_ claims reframed to a labeled **"working hypotheses / unverified"** framing — not deleted. Notes are never demoted (not in the pool).                                                                                                                                                                                                                                                                                                            | Honors "belief is a superseded mental model" without losing the majority thought-only content (§1.4).                                                                            |
| Grounding badge framing (P6)                  | Thought-only badge = **"Mental model — ungrounded belief"** + CTA "Ground this claim with a source" — an **invitation to legitimize deliberately**, not an incomplete-page notice; grounding optional.                                                                                                                                                                                                                                                                                              | Matches the §1.4 by-design stance and the §0 diagnostic-loop purpose.                                                                                                            |
| Entity evolution (P6b)                        | **Derived** from `source_entities.created_at` + vault git history; no new storage. Works on today's volume git; survives the D-I self-hosted-git migration.                                                                                                                                                                                                                                                                                                                                         | Free timeline; doesn't block on the not-yet-built local git.                                                                                                                     |
| Re-embed                                      | Automatic via fingerprint-gated queue trigger on content change; metadata-only edits don't bump fingerprint.                                                                                                                                                                                                                                                                                                                                                                                        | Already built; no threshold needed.                                                                                                                                              |
| Import chunking + `source_chunks` (confirmed) | Semantic/sentence-boundary default, `bge-m3`-tuned; `source_chunks` table.                                                                                                                                                                                                                                                                                                                                                                                                                          | Long-doc retrieval **and** the source list podcasts build from.                                                                                                                  |
| Images (D-F)                                  | content-core/Pillow extracts → `assets/` → Quartz renders inline; `content_type='image'` (a row in the new `content_types` table).                                                                                                                                                                                                                                                                                                                                                                  | Closes the no-images gap with the ingestion borrow.                                                                                                                              |
| Audio/video sources                           | Transcribe via existing **STT** at `host.docker.internal:8000/v1`; `content_type='audio'`.                                                                                                                                                                                                                                                                                                                                                                                                          | Reuses the local service; no new dependency.                                                                                                                                     |
| `content_type` storage                        | **Reference table** `content_types` + FK (not an inline CHECK).                                                                                                                                                                                                                                                                                                                                                                                                                                     | New formats = one `INSERT`, no DDL — import is built to expand.                                                                                                                  |
| Podcast TTS (D-H)                             | Existing local **TTS** at `host.docker.internal:8000/v1`; settings via ON-style config panel. **Deferred (P7).**                                                                                                                                                                                                                                                                                                                                                                                    | No engine decision needed; ON covers podcasts meanwhile.                                                                                                                         |
| Wiki grounding gap (D-J)                      | Pages come from the entity graph; thought-only pages are sourceless by design. Surface grounding state + **deliberate source→page linking** + **upload-and-link**; distinguish backlog via queue health.                                                                                                                                                                                                                                                                                            | Fixes the observed "no sources" gap without suppressing graph pages (§1.4, P6).                                                                                                  |
| Notebooks (D-G)                               | Existing `threads`/`thread_sources` M:N + a new pinned `slug` column; Quartz adds the notebook index + hub pages + hybrid membership (§5, §12.4).                                                                                                                                                                                                                                                                                                                                                   | Membership schema ready; the surface + pinned slug are the work.                                                                                                                 |
| Write-API home / auth                         | New `openbrain-workbench`, not the MCP server; Authelia (browser) + Caddy-injected secret (server-side); routes prefix-inclusive via `handle` (§12.6, §2.3).                                                                                                                                                                                                                                                                                                                                        | Keeps browser/multipart/auth off the limited MCP + cloud-gateway contract; secret never in client JS.                                                                            |
| Extraction engine                             | Python `openbrain-extract` with correctness-first per-format extractors behind a stable interface; quality acceptance gate per format (§12.3).                                                                                                                                                                                                                                                                                                                                                      | Faithful extraction of all listed formats over minimal footprint.                                                                                                                |
| Quartz customization                          | `quartz-overlay/` COPY'd over the pinned clone.                                                                                                                                                                                                                                                                                                                                                                                                                                                     | Keeps `QUARTZ_REF` upgradeable.                                                                                                                                                  |
| Storage direction (D-I)                       | `wiki-assets` volume now; self-hosted git vault later; Quartz primary.                                                                                                                                                                                                                                                                                                                                                                                                                              | Decouple binaries from git; roadmap target = self-hosted git server.                                                                                                             |

---

## 7. File / touchpoint index

- **New services:** `OB1/docker/workbench/` (Deno+Hono); `OB1/docker/extract/` (FastAPI+content-core). _(P7 later: a thin podcast caller, not necessarily its own heavy service.)_
- **Wiki generator + worker (P6):** [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) (honor `user_linked` source_entities; grounding state; grounded-page generation policy), entity-extraction worker (upsert without clobbering `user_linked` rows), [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) (queue-health endpoint feed).
- **Compose:** [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — add `openbrain-workbench`, `openbrain-extract`, `wiki-assets` volume, `host.docker.internal` reachability for STT/TTS.
- **Recovery (required):** `scripts/emergency-recovery.ps1` + `.bat` inventory + start/stop order.
- **Stack map (required):** [.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md).
- **Caddy:** [config/caddy/Caddyfile](config/caddy/Caddyfile) — `/workbench/*` via **`handle`** (prefix-preserving) + raised `request_body` on `/workbench/import` + `header_up X-Brain-Key`; [OB1/docker/Caddyfile](OB1/docker/Caddyfile) unchanged for the viewer (internal PostgREST proxy only) + `assets/`.
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` — leaf-page template (`type: thought|source` layout + tombstone marker; native popover + SPA + backlinks do the interaction, no custom linkifier), `ProvenancePanel` (per-page provenance index + live grounding-state badge + `## Evolution`), `NotebookIndex`, `NotebookPage` (+ `.inline.ts` hydration), `MembershipPicker`, `SourceLinker`, `NotesEditor`, `SourceEditor` (update-not-replace + re-fetch), `SourceRetractor` (three verbs + gravity counter + staged), `ImportDropzone` (+ link-target field), `ImportStatus`, `[PodcastPanel]`, `.inline.ts`, layout/config.
- **`Changes` log:** programmatically generated page(s) under the author-owned `notes/` layer logging staged-mutation actions + effects (outside generation scope — §4).
- **Wiki compiler:** [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) + [generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) — **emit stable backing id (`entity_id`/`thread_id`/leaf `id`) + `wiki_slug`/`slug` in frontmatter for all hydrated page classes (P0.6)**; emit cited-only `thought/<id>.md` + `source/<uuid>.md` leaf pages (batch-fetch full content by id; **S-token→UUID mapped**, no UUID echo), rewrite inline `[#id]`/`[S1]` citations into wikilinks, **dedicated leaf-sweep** + `thought/`+`source/` added to the `listEntityFiles` skip-list (P1); **notebook hub generation** (`content/notebook/<slug>.md`, baked shell+synthesis, folding in `topic/`) + pinned-slug + backfill + graph (P2); notes write interplay (P3); **tombstone filtering** on every source read-path (P4); honor `user_linked` source_entities + grounded-page generation policy (P6).
- **Schema (additive only):** `threads.slug` pinned column + `uq_threads_slug` (P2); `source_revisions` (`init-source-revisions.sql`); `content_types` reference table + FK replacing the `content_type` CHECK, `source_chunks` + `match_source_chunks` (`init-source-chunks.sql`); `sources.retracted_at`/`retracted_by`/`retraction_committed_at` retract columns (P4); `import_jobs` durable job table with `staged`/`committed` (P5/P6); `user_linked` rows in `source_entities` (P6, marked via `mention_role='user_linked'`); `podcasts` (P7).
  - **⚠️ Migration path (audit — two places, not one):** `/docker-entrypoint-initdb.d` scripts run **only on a fresh `openbrain-db-data` volume** ([docker-compose.yml:36-37](OB1/docker/docker-compose.yml#L36-L37)). So each new SQL file must be **(a)** mounted with an ordering prefix after `70-init-threads.sql` — `80-init-source-revisions.sql`, `90-init-source-chunks.sql`, `95-…` for the `content_type`/`source_entities`/retract widening — for fresh installs, **and (b)** applied to the **live** DB via the existing psql promotion runbook (the same path `init-threads.sql` took). A file that is only added to compose silently no-ops on the running stack.
- **Search:** `match_source_chunks` (new RPC; do not overload `match_sources`).

---

## 8. Sequencing & dependencies

```
P1 Provenance (compiler-only) ──> P4 Source Lifecycle (update+retract) ┐
                                                                       ├─> P6 Grounding &
P0 Foundations ─┬─> P2 Notebooks                                       │   Deliberate Linking
 (workbench +   ├─> P3 Notes                                           │            │
  extract +     └─> P5 Import ──────────────────────────────────────────┘            ▼
  P0.6 id)                                                              [P7 Podcasts — DEFERRED]
```

- **P1 is compiler-only and lands independently of P0** — it emits `thought/<id>`/`source/<id>` leaf pages + rewrites citations into wikilinks (S-token mapped) ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs) / [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs)); only its _optional_ leaf-template polish + `ProvenancePanel` touch the P0.3 overlay scaffold. (The P0.6 frontmatter-id contract is shared with the hydrated phases.)
- **P0 unblocks the workbench-backed phases** — P2/P3/P5 parallelize after it; P4/P6 add their write paths on top.
- P4 depends on **P1's source leaf page** (the read view it updates) **+ P0** (the workbench write API). P5 depends on P2 (thread linking) + P0; uses P4 conventions.
- **P6** (grounding + deliberate linking) depends on P1 (provenance), P4 (source update options), P5 (import entry point) — it stitches them together via the shared `/workbench/import` route + `target_entity_ids`.
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
destination: **Quartz becomes the workbench**; ON is retired **in stages**. The
end-state serves the [§0 diagnostic loop](#0-decisions-locked-for-this-plan):
external tooling (OWUI) discovers/gathers sources and emits AI notes; the user
grounds their own mental models against that research in Quartz.

- **Keep (built, reused):** the unified OB1 schema — `sources`, `threads`, `thread_sources`, `sessions`, `session_sources`, `find_or_create_source()`, `set_thread_source_status()`, suggestion worker. See [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/IMPLEMENTATION-PLAN-integrated-knowledge-system.md), [Integrated-knowledge-system-concept.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/Integrated-knowledge-system-concept.md).
- **Drop (after P5):** ON's repoint phases, its source import/notes role, SurrealDB-as-ON-store **for those functions**.
- **Keep running until P7:** ON as the **podcast** tool. ON stays available so podcasts work during the deferral.
- **Full decommission (after the deferred P7, gated on verification):**
  1. Confirm P1–P7 cover ON's wanted features (provenance, threads/notebooks, notes, source update/remove, import incl. images, source grounding, **podcasts**). ON chat sessions intentionally dropped.
  2. Migrate ON-only source data still in SurrealDB into OB1 `sources` via `find_or_create_source()` (one-shot).
  3. Remove `open_notebook` + `surrealdb` dep from [docker-compose.yml](docker-compose.yml), recovery scripts, stack-map, and the `:8443/:5055` Tailscale serves ([entrypoint.sh](entrypoint.sh)).
  4. Update memory: reverses the "repoint ON" direction in [three-layer-memory-stack-integration](C:\Users\yamao.claude\projects\d--Open-WebUI-ai-stack\memory\three-layer-memory-stack-integration.md).

> ⚠️ Verify ON is SurrealDB's only consumer before removing `surrealdb`
> ([gotcha](C:\Users\yamao.claude\projects\d--Open-WebUI-ai-stack\memory\surrealdb-v2-define-user-gotcha.md)).

---

## 11. Risks & guardrails

- **Three-place change convention (CLAUDE.md):** each new container updates compose **+** recovery scripts **+** stack-map together. Run `/stack-map` before/after compose edits.
- **OB1 guard rails:** additive schema only (widening a CHECK = drop+re-add, values only added); never alter/drop existing `thoughts`/`sources` columns; no secrets in files. (OB1's "MCP servers must be remote Edge Functions" rule is the public-contribution contract; this is local infra under `OB1/docker/`, like the existing `openbrain-mcp` — keep out of upstream PR scope.)
- **Staged mutations (§4):** retract + grounding commit autonomously at compile-tick; the live "in progress/pending" marker must propagate to **every** reference instantly (hydrated) and the rendered effects must land all-at-once on commit — never trickled. A reversal before commit must regenerate nothing and leave no orphan. Durable staging state (`retraction_committed_at`; `import_jobs.staged/committed`) must survive a workbench restart. Effects are surfaced (gravity counter + `Changes` log), not gated behind a confirm.
- **Tombstone visibility contract (§4):** excluded from generation throughout; retained always; visible-and-marked while referenced; UX-hidden when unreferenced.
- **Removal scope (D-D):** the default "remove" is a **reversible staged retract** (`retracted_at`; retained + restorable, invisible to generation on commit). Only **purge** (`DELETE`) is irreversible + cascades (incl. `source_revisions`/`source_chunks`); gate it behind explicit confirm, show affected pages/links, stamp `retracted_by`/`retracted_at`, default the UI to retract, and don't expose purge to non-operators.
- **Update-never-replace (D-D):** the source UI must offer **no "replace this source" affordance** — only "update this source / record a revision" (+ URL re-fetch) and a separate "add new source". Exposing a replace gesture teaches the wrong mental model.
- **`host.docker.internal` reach:** containers on `obnet` need host gateway access (Docker Desktop provides it); confirm it's reachable from the extract/workbench containers, and that the TTS/STT service is up before P5/P7 depend on it.
- **Manual links surviving re-extraction (P6) — concrete code change:** the worker today does a **full wipe-and-reinsert per source** — `await supabase.from("source_entities").delete().eq("source_id", item.source_id)` ([entity-extraction-worker/index.ts:748](OB1/integrations/entity-extraction-worker/index.ts#L748)) — so on the next fingerprint-change re-extraction it **deletes every `user_linked` row**. Required change: scope the delete to exclude manual links (`.delete().eq("source_id", …).neq("mention_role","user_linked")`) and make the subsequent insert an upsert that yields to `user_linked` per the §Phase-6 PK-collision policy. Guard + test this explicitly (a re-extraction cycle must leave the manual link intact).
- **Triaged links stay triaged:** a user-`hidden`/`inactive` `thread_sources` link must **suppress re-suggestion** — the suggestion worker must not re-propose a pair the user deliberately rejected (symmetric to `user_linked` suppressing worker overwrite). Otherwise the autonomous layer nags over the user's curation. Guard + test: hide a suggested link, run the worker, confirm it isn't re-proposed.
- **Don't suppress graph pages (D-J):** badging thought-only pages as "ungrounded belief" is fine; _removing_ them would break cross-entity `[[wikilinks]]` and graph nodes. Surface, don't delete.
- **New browser-facing write surface:** Authelia + Caddy-injected secret, portal-only, validate/normalize note + asset paths (no `../` escape — one shared validator §14.3), cap upload size, **sandbox `openbrain-extract`** (untrusted file/image parsing = classic RCE vector; run unprivileged, no extra network).
- **Membership confusion + gravity:** keep the three verbs visually distinct — **unlink from notebook** (soft, M:N status) vs **retract** (global, reversible, staged) vs **purge** (global, irreversible cascade). Wherever a removal is offered (source view **or** notebook hub), present **all three scopes with current memberships shown** and a **gravity counter** — _"in N notebooks, cited on M pages"_ — so the user can't globally retract a source they meant to unlink from one notebook. Purge always requires explicit confirm.
- **GPU/compile churn:** large imports + recompile; STT/TTS contend with `llama-cpp` — see [llama-swap perf tuning](C:\Users\yamao.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md). Batch embeddings; lean on the 3-min change-watch debounce.
- **Asset volume growth:** audio + images accumulate on `wiki-assets`; backup coverage + retention/cleanup (purge drops assets; retract keeps them).
- **Static-build friction (D-A):** heavy logic in `.inline.ts`; thin build-time components; overlay must not block `QUARTZ_REF` upgrades.
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the vault repo (that _is_ the notes + `Changes`-log write mechanism), staying local (no remote push; D16) until the self-hosted vault exists (D-I).

---

## 12. Resolved decisions (formerly open questions)

All resolved with the operator across multiple rounds. These are now binding for implementation.

1. **Source edit-history = append-only revision log; update-in-place, never replace.** Keep one canonical `sources` row (current = head); each edit snapshots prior `content`/`title` into `source_revisions(source_id, revision, content, title, edited_at, edited_by)`. The `source_id` never changes, so `thread_sources`/`source_entities`/search stay valid; diff = head vs revision N. **Editing updates _this_ source; "replace with a better source" is not a gesture — that's adding a new source.** URL sources can queue an ai-stack **re-fetch** that lands as a new revision; re-import of an existing URL appends a revision (linear), never overwriting the head or forking a duplicate. _(Not the supersedes-chain variant.)_ → P4.
2. **`content_type` → reference table `content_types` + FK** (replaces the inline CHECK), seeded with the existing 7 + `image,docx,pptx,audio,txt,md`. A new format is one `INSERT`, no DDL. Per-format badges/filters in provenance; an `'audio'` source is its STT transcript. → P5.
3. **Extractor = correctness-first, all formats, clean/industry-standard.** Engine chosen on **extraction quality**, not footprint. `openbrain-extract` exposes a stable `/extract` interface with **best-of-breed per-format extractors** (e.g. PyMuPDF/Docling-class for PDF fidelity, python-docx/-pptx, Pillow+Tesseract for image OCR, STT for audio/video); content-core may back any format where it extracts most correctly. **Every supported format gets an explicit extraction-quality acceptance gate** (faithful text, tables, headings, image refs); coverage of _all_ listed types is in scope. → P5.
4. **Notebook hub pages = compiled shell + live hydration.** Compiler emits a thin shell (title, description, synthesis, static graph); `NotebookPage.inline.ts` fetches live sources/notes/membership/suggestions from the workbench API, so add/remove reflects instantly with no recompile wait; degrades to the baked shell if the API is down (hydrated view authoritative when reachable). → P2/§5.
5. **Notebook = thread, one hub, pinned slug.** User-facing noun **Notebook**; backing table stays `threads` (+ a pinned `slug` column). One `content/notebook/<slug>.md` hub folds in the old `topic/` synthesis and surfaces sources + `notes/<slug>/*` + triage; legacy free-text notebooks are **backfilled** to rows. Notes live under `notes/<notebook-slug>/`; `[[notebook/x]]` lines up; align `ingestNotes()` `notebook = parts[1]` to the slug. → P2/P3/§5.
6. **Workbench auth = Authelia (browser) + Caddy-injected secret (server-side); routes prefix-inclusive via `handle`.** The wiki subdomain's existing Authelia `forward_auth` authenticates the operator; Caddy injects the shared secret (reuse the `MCP_ACCESS_KEY` value) when proxying to the workbench — **never** embedded in static client JS — and uses **`handle` (prefix-preserving)** so routes are named `/workbench/...`. `edited_by`/`retracted_by` stamped `'operator'`; per-user identities deferred. → §2.3.
7. **Source removal = three verbs.** unlink (per-notebook flip) · **retract** (global, reversible **staged** `retracted_at` tombstone — retained, restorable, invisible to all generation read-paths on commit) as the default · purge (`DELETE`, irreversible, rare). → P4/§4.
8. **Grounded-page generation policy.** Once a page has ≥1 source: sources carry asserted facts; _thought-derived_ claims are reframed to a labeled "working hypotheses / unverified" framing, not deleted. Notes are never demoted (not in the generation pool). → P6.
9. **Ground-from-the-page + failure handling.** "Ground this claim with a source" lives on the entity page; user supplies a document or URL **via the `/workbench/import` route with `target_entity_ids`**; success → regenerate (badge flips to Grounded at compile completion); failure → client-marked "⚠ Ingest failed" with **no recompile** + a durable `failed` `import_jobs` row feeding a later alerts surface. Badge framing is an invitation to legitimize, not an incomplete-page notice. → P6.
10. **Entity evolution = derived, no new storage.** From `source_entities.created_at` + vault git history (works on today's volume git; survives the D-I self-hosted migration, which isn't built yet). → P6b.
11. **Source citations use a short per-page token** (`S1/S2…` → UUID map), not echoed UUIDs (`sources.id` is UUID; LLMs mis-transcribe them). The model never emits UUIDs; the regex matches `S\d+`/`#\d+` only; the token map is resolved deterministically at rewrite. Thought ids (`BIGSERIAL`) stay literal. → P1.
12. **One shared slug module**, canonical = the NFKD-normalizing algorithm; entity slugs already pinned so no data migration. → §14.1.
13. **Workbench writes via deno-postgres transactions** (atomic import); reads may use PostgREST. → §14.5.
14. **Staged mutations + autonomous commit.** Retract and grounding are staged; commit is autonomous (request + not-cancel = confirm), not a confirm-button; safety = a gravity counter at request time + a `Changes` log in the author-owned `notes/` layer that illustrates effects and logs the user's action, with a reversal window before the compile commits. → §4.

---

## 13. Suggested next step

All gating questions are resolved, so the next action is **Phase 0** — stand up
the `openbrain-workbench` skeleton (Deno+Hono, internal `:8000`, on
`obnet`+`llm-net`+`app-net`, Authelia-fronted + Caddy-injected secret, routes via
`handle`), the Caddy `/workbench/*` same-origin route in the `wiki.` block, the
Quartz overlay scaffold + asset config, the `wiki-assets` volume, and the **P0.6
frontmatter-id contract** the hydrated phases depend on. P0 unblocks every
**workbench-backed** phase (P2–P7) and proves the in-Quartz-components + thin-API +
extract-sidecar architecture end-to-end before any schema (`source_revisions`,
`source_chunks`, `content_type`/retract widening), extractor, or thread-surface
work. **One exception:** P1 (provenance) is now **compiler-only** — it emits
`thought/<id>`/`source/<id>` leaf pages and rewrites `[#id]`/`[S1]` citations
(S-token mapped) into wikilinks — so it depends on neither the workbench nor the
schema work and can ship **in parallel with, or before, P0** as a low-cost early
win. The **staged-mutation contract (§4) is settled** — autonomous commit, reversal
window, effects shown via the gravity counter + the `Changes` log — so P4/P6 can be
built without re-litigating the commit trigger. Podcasts (P7) wait; ON keeps serving
them in the meantime.

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
(the comment literally says _"kept in sync by hand; the recipe owns the canonical
version"_). This plan adds **thread slugs** (P2) and **leaf-page ids** (P1), which
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
**Fix:** structure it as Hono **sub-routers per resource** (`/workbench/sources`,
`/workbench/threads`, `/workbench/notes`, `/workbench/import`, …) over a thin
**service → repository** layering, so the DB access (14.5) and validation live
behind interfaces and each route stays single-responsibility. (Routes are
prefix-inclusive to match the `handle` proxy — §2.3.) Encapsulation point:
path/asset normalization (no `../` escape — already a §11 guardrail) belongs in one
shared validator, not per-handler.

### 14.4 Durability — import job state must outlive a restart

P5 returns a `job_id` and exposes `GET /workbench/jobs/:id`. If job state is
in-memory (as `lastStatus` is in
[wiki-service.mjs:89](OB1/docker/wiki-service/wiki-service.mjs#L89)), a workbench
restart **orphans every in-flight import** and the UI polls a 404 forever.
**Resolved:** persist `import_jobs(id, status, source_id, target_entity_ids,
target_notebook, error, staged, committed, created_at, updated_at)`. Beyond
surviving restarts and backing `ImportStatus.tsx`, this table is **load-bearing for
P6**: the `staged`/`committed` columns drive the grounding badge's pending-vs-grounded
read, and a failed ground-from-the-page attempt records its `target_entity_ids` +
terminal `error` here — exactly the data the later "failed grounding attempts"
alerts surface reads.

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
- Source editing is **update**, not **replace** — the UI verb and the route
  (`PATCH /workbench/sources/:id`, `POST …/:id/refetch`) both say "update this
  source"; there is no "replace" verb anywhere.
