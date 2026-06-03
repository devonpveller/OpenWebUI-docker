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
| D-C | **Features, phased:** Provenance → Threads → Notes → Source Lifecycle → Import → (deferred) Podcasts. | Each phase has its own ship gate. |
| D-D | **Sources are added as-is, are editable with a preserved edit history, and are removable with cascade.** | An edit creates a **new version / supersedes** the prior (history kept), it never silently mutates the record of truth. Removal cascades links + orphan-sweeps pages. (P4) |
| D-E | **User notes are the freely editable, additive layer**, written into the Open Brain DB. | The tethered-notes mechanism, surfaced with an in-Quartz editor. (P3) |
| D-F | **Images are first-class** — Quartz must display them; ingestion must extract/accept them. | Comes with the content-core borrow + Quartz vault-asset handling + a widened `content_type`. (P5) |
| D-G | **Threads = research groups = ON notebooks; M:N, non-exclusive.** Surfaced in Quartz with a thread index, per-thread pages, and membership management. | Schema already supports this; the work is the Quartz surface + UX. (P2, [§5](#5-threads--research-groups-the-core-organizing-axis)) |
| D-H | **Podcasts use the existing local TTS/STT service** at `host.docker.internal:8000/v1` (OpenAI-compatible, several voices; STT too). Settings via a **config panel mirroring ON's UI.** | No new TTS engine decision; STT also enables audio/video source ingestion. **Podcast phase is deferred** (large feature; ON covers it meanwhile). (P6) |
| D-I | **Storage direction: move off the current git-vault-on-volume toward a self-hosted git vault (later); Quartz 4 is the primary surface.** | Near-term: a separate `wiki-assets` volume for binaries; notes stay in the current vault but the roadmap target is a self-hosted git server. ([§9](#9-storage--the-vault-direction)) |

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
| **Local TTS + STT** (OpenAI-compatible, several voices) | `host.docker.internal:8000/v1` | ✅ — basis for P6 + audio ingest |

### 1.3 The gaps this plan closes

1. No browser write path (upload, note authoring, source edit/remove, thread membership, podcast request).
2. PDF/DOCX/PPTX text + **image** extraction is a stub.
3. Provenance isn't surfaced on pages.
4. **Threads have no Quartz surface** — no index, no per-thread view, no membership UI (the core organizing axis is invisible).
5. No source edit-with-history or removal path.
6. No podcast capability in-stack (deferred; ON covers it for now).

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

`*` = deferred podcast phase (P6).

- **`openbrain-workbench`** (Deno+Hono, `:8814`) — browser-facing read/write API, kept **off** the MCP server + cloud-gateway (limited to 8 tools) so the multipart/auth surface is isolated.
- **`openbrain-extract`** (Python/FastAPI) — wraps `content-core`; `POST /extract` → `{ markdown, title, metadata, pages, images[] }`; OCR for scans/images.
- **Existing TTS/STT** at `host.docker.internal:8000/v1` — STT used at import for audio/video sources (P5); TTS for podcasts (P6, deferred). No new container until P6 (and even then likely just a thin caller, not a TTS engine).

### 2.2 Asset handling (images now; audio later)

New **`wiki-assets`** volume for binaries Quartz serves statically (decouples
binaries from the git vault per D-I):

- **Images:** extraction returns embedded images → workbench writes `assets/<source-id>/img-n.png` and rewrites source markdown to `![alt](assets/<source-id>/img-n.png)` → Quartz renders inline. Standalone image upload → a `source` (`content_type='image'`, content = OCR/caption text for embedding) with the image as its asset.
- **Audio (P6):** `assets/podcasts/<id>.mp3`, streamed by the player.
- Confirm Quartz asset config in the overlay so `assets/` is served but not paginated.

### 2.3 Routing, exposure, auth

- Caddy `/workbench/*` → `openbrain-workbench:8814`, **same origin** as the wiki (no CORS): [config/caddy/Caddyfile](config/caddy/Caddyfile) + OB1 [Caddyfile](OB1/docker/Caddyfile).
- Portal-only reach + bearer/shared-secret (reuse `MCP_ACCESS_KEY` pattern). New `/workbench` route may need a Tailscale serve entry ([recipe](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\tailscale-serve-restore-recipe.md)).

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

- **P0.1** `openbrain-workbench` skeleton (Deno+Hono, health, bearer auth, `obnet`+`llm-net`, `:8814`) → compose **+ recovery scripts + stack-map**.
- **P0.2** Caddy `/workbench/*` proxy (both Caddyfiles), same-origin.
- **P0.3** Quartz overlay scaffold + asset config; no-op component proving overlay + client `fetch()` to `/workbench/health`; confirm `assets/` served, not paginated.
- **P0.4** `wiki-assets` volume wired to workbench + viewer.
- **P0.5** Shared TS types for source/thread/membership/provenance shapes mirrored from [init-sources.sql](OB1/docker/init-sources.sql)/[init-threads.sql](OB1/docker/init-threads.sql).

**Gate:** a custom component, served through the portal, calls the authed API and
renders; an image under `assets/` renders in a page. No `sources` writes yet.

---

## 4. Feature phases

### Phase 1 — Source Visibility & Provenance
**Goal:** every entity/topic page shows its underlying sources (type, date, title/URL, notebook/tags) with click-through to a raw source view.
- **Backend (read):** `GET /workbench/provenance?entity=<id>` (join `source_entities`→`sources`), cached per entity on `last_compile_iso`; `GET /workbench/sources/:id`.
- **Compiler:** emit a provenance sidecar at compile (`<slug>.sources.json` or front-matter) so panels render with no hot-path DB hit ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs), `WIKI_MAX_SOURCES`).
- **Quartz:** `ProvenancePanel.tsx`, source-view route, wiki↔source toggle.
- **Gate:** open any page → real sources w/ metadata → click through. No DB query on hot loads.

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
- **`openbrain-extract` sidecar (content-core borrow):** `POST /extract` → markdown + metadata + images; OCR via Tesseract/Pillow. Audio/video → transcript via STT at `host.docker.internal:8000/v1`. Add to compose **+ recovery + stack-map**.
- **Workbench `POST /workbench/import` (async):** store upload → extract → write images to `assets/<source-id>/` + rewrite refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(..., 'deliberate')` to the selected thread → `job_id`; `GET /workbench/jobs/:id` for progress.
- **Schema (additive):** widen `content_type` CHECK to add `'image'` (+ `'docx'`/`'pptx'` or keep as `'manual'`); **`source_chunks(source_id, idx, content, embedding VECTOR(1024))`** (confirmed needed — long-doc retrieval *and* the source list podcasts build from) + a chunk-aware `match_sources` variant.
- **Quartz:** `ImportDropzone.tsx` (validation, per-file progress, errors) + `ImportStatus.tsx`; thread selector reuses `MembershipPicker`.
- **Gate:** drop a PDF, DOCX, PNG (and an MP3) → all extract/transcribe, chunk, embed, dedupe, link to the chosen thread, entity-extract, surface via P1 provenance and on the P2 thread page; images render inline; corrupt files fail clearly.

### Phase 6 — Podcast Service (DEFERRED — D-B/D-H)
*Large feature; built later. **Open Notebook remains available and provides podcasts until this ships.*** Recorded here so the architecture leaves room.
- **Generation on request, per thread:** pull the thread's sources (via `source_chunks`) → script via local Qwen (`llama-cpp`) → audio via **existing TTS** at `host.docker.internal:8000/v1` (several voices) → `assets/podcasts/<id>.mp3` + transcript.
- **Schema (additive):** `podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)` — "organized by thread" for free.
- **Settings:** a **config options panel mirroring ON's UI** (speaker count 1–4, voice selection from the TTS service, style/length). Stored per-thread or global default.
- **Transcript → note:** reuses the P3 notes write path (editable, tethered).
- **Quartz:** `PodcastPanel.tsx` — Podcasts section (global + per-thread) with an `<audio>` player; "Generate podcast for this thread"; "Save transcript as note".
- **Deferral note:** because ON covers podcasts meanwhile, no `openbrain-podcast` container is built in P1–P5. When P6 starts, prefer a thin caller of the existing TTS over a heavy `podcast-creator` dependency unless multi-speaker scripting needs it.

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
  - later, its **podcasts** (P6).
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
| Source editing/versioning (D-D) | **Editable with append-only `source_revisions` history** (edit = new version supersedes; old preserved). | Operator wants edits *with* history, not immutability. |
| Source removal (D-D) | Soft = thread status flip; hard = cascade delete + orphan sweep, operator-confirmed. | Non-exclusive M:N means "remove from thread" ≠ "delete". |
| Re-embed | Automatic via fingerprint-gated queue trigger on content change; metadata-only edits don't bump fingerprint. | Already built; no threshold needed. |
| Import chunking + `source_chunks` (confirmed) | Semantic/sentence-boundary default, `bge-m3`-tuned; `source_chunks` table. | Long-doc retrieval **and** the source list podcasts build from. |
| Images (D-F) | content-core extracts → `assets/` → Quartz renders inline; `content_type` widened to `'image'`. | Closes the no-images gap with the ingestion borrow. |
| Audio/video sources | Transcribe via existing **STT** at `host.docker.internal:8000/v1`. | Reuses the local service; no new dependency. |
| Podcast TTS (D-H) | Existing local **TTS** at `host.docker.internal:8000/v1`; settings via ON-style config panel. **Deferred (P6).** | No engine decision needed; ON covers podcasts meanwhile. |
| Threads (D-G) | Existing `threads`/`thread_sources` M:N; Quartz adds index + thread pages + hybrid membership (§5). | Schema ready; only the surface is missing. |
| Write-API home | New `openbrain-workbench`, not the MCP server. | Keeps browser/multipart/auth off the limited MCP + cloud-gateway contract. |
| Extraction engine | Python `openbrain-extract` wrapping content-core (fallback PyMuPDF/python-docx/python-pptx + `convert_heavy_file.py`). | Reuse ON's best IP without Python in Deno. |
| Quartz customization | `quartz-overlay/` COPY'd over the pinned clone. | Keeps `QUARTZ_REF` upgradeable. |
| Storage direction (D-I) | `wiki-assets` volume now; self-hosted git vault later; Quartz primary. | Decouple binaries from git; roadmap target = self-hosted git server. |

---

## 7. File / touchpoint index

- **New services:** `OB1/docker/workbench/` (Deno+Hono); `OB1/docker/extract/` (FastAPI+content-core). *(P6 later: a thin podcast caller, not necessarily its own heavy service.)*
- **Compose:** [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — add `openbrain-workbench`, `openbrain-extract`, `wiki-assets` volume, `host.docker.internal` reachability for STT/TTS.
- **Recovery (required):** `scripts/emergency-recovery.ps1` + `.bat` inventory + start/stop order.
- **Stack map (required):** [.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md).
- **Caddy:** [config/caddy/Caddyfile](config/caddy/Caddyfile), [OB1/docker/Caddyfile](OB1/docker/Caddyfile) — `/workbench/*` + `assets/`.
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` — `ProvenancePanel`, `ThreadIndex`, `ThreadPage`, `MembershipPicker`, `NotesEditor`, `SourceEditor`, `SourceRetractor`, `ImportDropzone`, `ImportStatus`, `[PodcastPanel]`, `.inline.ts`, layout/config.
- **Wiki compiler:** [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) — provenance sidecar (P1); thread-page generation + graph (P2); notes write interplay (P3); confirm orphan sweep covers hard-deletes (P4).
- **Schema (additive only):** `source_revisions` (`init-source-revisions.sql`); widen `content_type` CHECK + `source_chunks` (`init-source-chunks.sql`); `podcasts` (P6); retraction audit in `metadata`.
- **Search:** chunk-aware `match_sources` variant.

---

## 8. Sequencing & dependencies

```
P0 Foundations ─┬─> P1 Provenance ─┬─> P4 Source Lifecycle (edit+retract)
                ├─> P2 Threads ─────┼──────────────┐
                ├─> P3 Notes ───────┘              ▼
                └──────────────────────> P5 Import ──> [P6 Podcasts — DEFERRED]
```

- P0 unblocks all; P1/P2/P3 parallelize after it.
- P4 depends on P1's source view. P5 depends on P2 (thread linking) + P0; uses P4 conventions.
- **P6 deferred**; ON stays for podcasts until it ships → **full ON retirement happens after P6, not P5** ([§10](#10-relationship-to-iks--retiring-open-notebook)).

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
- **Keep running until P6:** ON as the **podcast** tool. ON stays available so podcasts work during the deferral.
- **Full decommission (after the deferred P6, gated on verification):**
  1. Confirm P1–P6 cover ON's wanted features (provenance, threads/notebooks, notes, source edit/remove, import incl. images, **podcasts**). ON chat sessions intentionally dropped.
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
- **`host.docker.internal` reach:** containers on `obnet` need host gateway access (Docker Desktop provides it); confirm it's reachable from the extract/workbench containers, and that the TTS/STT service is up before P5/P6 depend on it.
- **New browser-facing write surface:** bearer auth, portal-only, validate/normalize note + asset paths (no `../` escape), cap upload size, **sandbox `openbrain-extract`** (untrusted file/image parsing = classic RCE vector; run unprivileged, no extra network).
- **Membership confusion:** keep "remove from thread" (soft, M:N status) visually distinct from "delete source" (global, cascading) so users don't nuke a shared source when they meant to unlink it from one thread.
- **GPU/compile churn:** large imports + recompile; STT/TTS contend with `llama-cpp` — see [llama-swap perf tuning](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md). Batch embeddings; lean on the 3-min change-watch debounce.
- **Asset volume growth:** audio + images accumulate on `wiki-assets`; backup coverage + retention/cleanup (hard-deletes drop assets).
- **Static-build friction (D-A):** heavy logic in `.inline.ts`; thin build-time components; overlay must not block `QUARTZ_REF` upgrades.
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the vault repo (that *is* the notes write mechanism), staying local (no remote push; D16) until the self-hosted vault exists (D-I).

---

## 12. Open questions

1. **`source_revisions` shape:** append-only revision log (recommended) vs `supersedes`/`superseded_by` self-reference chaining new source rows? Both preserve history; the log is simpler, the chain keeps each version independently linkable to threads.
2. **`content_type` widening:** add `'image'` only, or also `'docx'`/`'pptx'`/`'audio'` for clearer provenance vs. keeping file docs as `'manual'`?
3. **content-core weight:** accept its footprint in `openbrain-extract`, or start lighter (PyMuPDF/python-docx/python-pptx + `convert_heavy_file.py`) and add content-core only for formats those miss?
4. **Thread page generation:** fully compiler-generated markdown (works offline, but membership edits wait for recompile) vs. a thin compiled stub hydrated live from the API (instant, but needs the API at view time)? Likely stub-for-shell + live-for-membership.
5. **Notebook↔thread naming:** unify the `notes/` `notebook` folder name with the thread slug now (cleaner), or keep them separate and map?
6. **Workbench auth:** single operator bearer now, or per-user later (affects edit/retraction/note attribution)?

---

## 13. Suggested next step

Resolve **Q1 (`source_revisions` shape)** and **Q4 (thread page generation)** —
the two that gate P2/P4 — then start **Phase 0** (workbench skeleton + Caddy route
+ overlay + asset config + assets volume). It unblocks every phase and proves the
in-Quartz-components + thin-API + extract-sidecar architecture end-to-end before
any schema, extractor, or thread-surface work. Podcasts (P6) wait; ON keeps
serving them in the meantime.
