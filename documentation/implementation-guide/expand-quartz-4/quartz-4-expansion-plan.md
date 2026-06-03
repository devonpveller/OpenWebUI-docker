# Quartz 4 Expansion Plan — Quartz as the Open Brain Workbench

> **Status:** Plan / pre-implementation
> **Branch context:** `feature/integrated-knowledge-system`
> **Supersedes/reframes:** the Open Notebook repoint phases of the in-flight
> Integrated Knowledge System (IKS) work — see [§9](#9-relationship-to-iks--retiring-open-notebook).
> **Source idea:** [initial-quartz-4-expansion-idea.md](documentation/implementation-guide/expand-quartz-4/initial-quartz-4-expansion-idea.md)

---

## 0. Decisions locked for this plan

Confirmed by the operator before drafting; these drive everything below.

| # | Decision | Consequence |
|---|----------|-------------|
| D-A | **Interactive layer = in-Quartz client components.** | Add Preact components + client scripts to the Quartz viewer; back them with a thin write-API on Open Brain (Quartz can't write Postgres from a static page). |
| D-B | **Borrow from Open Notebook, then retire it.** ON is ~80% redundant. | Harvest ON's reusable pieces — content-core extraction (incl. **images**), **podcast generation** (`podcast-creator`), source/notes UX — into the Quartz+OB1 stack. Decommission `open_notebook` + its SurrealDB usage at the end. |
| D-C | **Features, phased**, idea-doc order then extensions: Provenance → Notes → Source Retraction → Import → Podcasts. | Each phase has its own ship gate. |
| D-D | **Sources are immutable source-of-truth (read-only).** | No source-content editing. The only corrective action is **retraction/removal** of a wrong or accidentally-ingested source (P3). Re-ingest to "fix." |
| D-E | **User notes are the editable, additive layer.** | Notes are created/edited by the user and written additively into the Open Brain DB (via the existing tethered-notes mechanism). This is the one read-write content layer. |
| D-F | **Images are first-class.** Quartz must display images; document ingestion must accept/extract them. | Comes with the content-core borrow + Quartz asset handling + a widened `content_type`. |
| D-G | **Podcasts are an extended service.** | Generated **on request** (as ON does now); shown in their own section with **playback**; organized **by thread**; transcripts can be saved as **user notes**. |

The spine remains: Quartz already mimics Obsidian and is *already wired directly
to Open Brain*, so the cost of making it a workbench is feature development +
borrowed implementations, not a new architecture.

---

## 1. Where we are starting from

### 1.1 What Quartz is here (today)

Quartz 4 is the **read-only** `openbrain-wiki-viewer` container — Quartz `v4.5.1`,
fetched at image-build time, serving static HTML it rebuilds from markdown:

- [OB1/docker/wiki-viewer/Dockerfile](OB1/docker/wiki-viewer/Dockerfile) — pins `QUARTZ_REF=v4.5.1`, `npm ci`.
- [OB1/docker/wiki-viewer/entrypoint.sh](OB1/docker/wiki-viewer/entrypoint.sh) — symlinks the whole `/wiki` vault into `/quartz/content`, patches `ignorePatterns`, then `npx quartz build --serve --port 8080`.
- [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — `openbrain-wiki-viewer` on `obnet` + `app-net`, published `127.0.0.1:8812:8080`, reached publicly via the Caddy/Tailscale portal.

The vault is a **git repo on the `openbrain-wiki-data` volume**:

```
/wiki/
  index.md            ← compiler-owned home
  content/            ← AUTO-GENERATED (entity + topic pages, entities.md, graph.json)
    person/ org/ tool/ topic/ ...
  notes/              ← HUMAN-OWNED, compiler never edits (already exists!)
```

> Quartz renders standard markdown, so **images already display** once the image
> files live in the vault and are referenced (`![alt](path)`); the gap is that
> nothing currently *puts* images in the vault. D-F closes that in [Phase 4](#phase-4--direct-source-import-pipeline-incl-images).

### 1.2 What Open Brain already gives us (the foundation)

Most of the ingestion machinery the idea doc asks for **already exists** — this
plan mostly wires UI to it, not builds it from scratch.

| Capability | Where it lives | State |
|------------|----------------|-------|
| `sources` table (url, title, content, content_type, tags, notebook, embedding `VECTOR(1024)`, metadata jsonb, research linkage) | [OB1/docker/init-sources.sql](OB1/docker/init-sources.sql) | ✅ live |
| `find_or_create_source()` — dedup on url/content-hash, returns `was_duplicate` | [OB1/docker/init-threads.sql](OB1/docker/init-threads.sql) | ✅ live |
| `threads`, `thread_sources` (link_type auto/suggested/deliberate, **status confirmed/pending/hidden/inactive**), `sessions`, `session_sources` | [OB1/docker/init-threads.sql](OB1/docker/init-threads.sql) | ✅ live |
| `set_thread_source_status()` — soft flag flips (never deletes) | [OB1/docker/init-threads.sql](OB1/docker/init-threads.sql) | ✅ live — basis for retraction (P3) |
| `source_extraction_queue` + auto-enqueue trigger on insert/update (fingerprint-gated) | [OB1/docker/init-source-graph.sql](OB1/docker/init-source-graph.sql) | ✅ live |
| Entity extraction worker → `source_entities` (`ON DELETE CASCADE`) | `openbrain-entity-worker` ([compose](OB1/docker/docker-compose.yml)) | ✅ live |
| Embeddings — `bge-m3`, 1024-dim, via `llama-cpp-embed` | MCP server `getEmbedding()` | ✅ live |
| `match_sources()` vector search RPC | [OB1/docker/init-sources.sql](OB1/docker/init-sources.sql) | ✅ live |
| MCP/HTTP server (Hono on Deno) — `ingest_url`, `search`, `capture_thought`… | [OB1/integrations/kubernetes-deployment/index.ts](OB1/integrations/kubernetes-deployment/index.ts) (`Deno.serve(... app.fetch)` at tail) | ✅ live |
| Wiki compiler: change-watch (3-min debounce), notes ingest, **orphan sweep** (deletes pages whose source entities were removed), on-demand `POST /recompile` | [OB1/docker/wiki-service/wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ live |
| **Tethered notes** — `notes/<notebook>/file.md` ⇄ one OpenBrain thought, by `metadata.note_path`; diff-based upsert/delete + extraction enqueue | `ingestNotes()` in [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ live — basis for the editable notes layer (P2) |

### 1.3 The gaps this plan closes

1. **No write path from the browser** — no upload, note-authoring, retraction, or podcast-request endpoint.
2. **PDF/DOCX/PPTX text + image extraction is a stub** — `ingest_url` returns `[PDF source not text-extracted: …]`; no multipart upload; no image handling.
3. **Provenance isn't surfaced** — pages are synthesized from sources, but readers can't see *which* sources or click through.
4. **No way to retract a bad/accidental source** — sources are write-only today; the user needs a removal path (D-D).
5. **No podcast capability** — ON has it; the stack doesn't (D-G).

### 1.4 What we borrow from Open Notebook (D-B)

ON's reusable IP, to be ported — **not kept running**:

- **`content-core`** (PyPI) — ON's parser matrix for PDF/DOCX/PPTX/MD/TXT/URL/**image**/audio→markdown. Single most valuable borrow; replaces the PDF stub and brings image extraction (D-F). → [Phase 4](#phase-4--direct-source-import-pipeline-incl-images).
- **`podcast-creator`** (PyPI, ON dep ≥0.12.0) + its LangGraph script→TTS flow — multi-speaker (1–4), voice profiles, generated script + audio. → [Phase 5](#phase-5--podcast-service-extended). **Caveat:** ON drives TTS through cloud providers (esperanto: OpenAI/ElevenLabs/Google). This stack is local-runtime-only, so we must back it with a **local TTS engine** — a real open decision ([§10](#10-open-questions)).
- **Source-card + notebook-view UX** and ON's **podcast panel/player UX** — mirrored as Quartz components reading from OB1 instead of SurrealDB.
- **Token-based chunking** patterns — re-implemented against `bge-m3` 1024-dim + pgvector.

Dropped with ON: SurrealDB-as-store, ON's chat sessions, ON's job queue, ON's
own UI/auth. (Podcast generation is **kept**, contrary to the prior draft.)

---

## 2. Target architecture

### 2.1 The unavoidable backends

D-A ("in-Quartz components") needs somewhere to write, because a static site
can't mutate Postgres or run Python parsers/TTS. Three new services:

```
        ┌───────────────────────── Quartz viewer (static + hydrated) ─────────────────────────┐
        │ ProvenancePanel  NotesEditor  SourceRetractor  ImportDropzone  PodcastPanel(player)   │
        └───────────────┬──────────────────────────────────────────────────────────────────────┘
                        │  fetch()  (same-origin /workbench/* via Caddy)
                        ▼
        ┌───────────────────────── openbrain-workbench  (Deno+Hono, :8814) ───────────────────────┐
        │  POST  /workbench/import         (multipart → extract → chunk → embed → find_or_create)   │
        │  GET   /workbench/sources/:id    (raw source view)                                        │
        │  DELETE/POST /workbench/sources/:id/retract  (soft-hide | hard-delete; D-D)               │
        │  GET   /workbench/provenance?entity=…        (sources behind a wiki page)                 │
        │  GET/PUT /workbench/notes/<path>             (read/write notes/ markdown + commit; D-E)   │
        │  POST  /workbench/podcasts        (request gen for a thread)   GET .../podcasts/:id        │
        │  POST  /workbench/podcasts/:id/transcript-to-note  (save transcript as a user note)       │
        │  GET   /workbench/jobs/:id        (import/podcast progress)                               │
        └───────┬─────────────────────────┬───────────────────────────┬───────────────────────────┘
                ▼                          ▼                           ▼
   openbrain-extract (Py/FastAPI)   openbrain-podcast (Py/FastAPI)   OB1 Postgres
   content-core: docs+images+OCR    podcast-creator + LOCAL TTS      sources / threads / thread_sources
        │                                  │  audio files                podcasts (NEW) / source_chunks (NEW)
        ▼                                  ▼                              │
   markdown + extracted images        wiki-assets volume (audio + imgs)  │ change-watch + queue trigger
        └──────────────► OB1 sources + vault assets ◄────────────────────┘
                                       │
                              openbrain-wiki recompile → Quartz file watcher reloads
```

- **`openbrain-workbench`** (Deno+Hono, `:8814`) — the browser-facing write/read API. Kept **off** the MCP server + cloud-gateway path (which is deliberately limited to 8 tools), so the multipart/cookie/auth surface stays isolated.
- **`openbrain-extract`** (Python/FastAPI) — wraps `content-core`; `POST /extract` (multipart) → `{ markdown, title, metadata, pages, images[] }`. OCR (Tesseract/Pillow) for scanned PDFs/images. Fallback: in-repo [heavy-file-ingestion](OB1/skills/heavy-file-ingestion/) `convert_heavy_file.py` / `PyMuPDF`+`python-docx`+`python-pptx`.
- **`openbrain-podcast`** (Python/FastAPI) — wraps `podcast-creator`; script via local Qwen (`llama-cpp`), audio via a **local TTS** backend; writes audio to a shared `wiki-assets` volume and a row to `podcasts`.

> All three new containers must be added to compose **+** recovery scripts **+**
> stack-map together (workspace convention — [§8](#8-risks--guardrails)).

### 2.2 Asset handling (audio + images)

A new **`wiki-assets`** volume (or a subtree of the wiki vault, e.g.
`/wiki/content/assets/`) holds binary files Quartz serves statically:

- **Images:** extraction returns embedded images; the workbench writes them under `content/assets/<source-id>/…` and rewrites the source markdown to `![alt](assets/<source-id>/img-n.png)` so Quartz renders them inline. Standalone image uploads become a `source` (`content_type='image'`, `content` = OCR text + caption for embedding) with the image as its asset.
- **Audio:** podcast renders write `assets/podcasts/<podcast-id>.mp3`; the `PodcastPanel` `<audio>` element streams it same-origin.
- Quartz already copies static assets in `content/`; confirm `ignorePatterns`/asset config in the overlay so `assets/` is served but not treated as pages.

### 2.3 Routing, exposure, auth

- Caddy gains `/workbench/*` → `openbrain-workbench:8814`, **same host/origin** as the wiki (no CORS). Touch [config/caddy/Caddyfile](config/caddy/Caddyfile) and OB1 [Caddyfile](OB1/docker/Caddyfile).
- Reachable only via the Tailscale portal (the wiki's only public path). Add a bearer/shared-secret check (reuse `MCP_ACCESS_KEY` pattern) so write/retract/delete aren't open to non-operators. New `/workbench` portal route may need a Tailscale serve entry — see [Tailscale serve restore recipe](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\tailscale-serve-restore-recipe.md).

### 2.4 How writes become visible (the loop)

Every write lands in OB1 Postgres / the vault → the existing
`source_extraction_queue` trigger and the wiki **change-watch** (3-min debounce)
recompile → Quartz's file watcher reloads. **We add no new sync mechanism.**
Retraction reuses the compiler's existing **orphan sweep** (pages drop when their
source entities are removed).

### 2.5 Quartz customization model

Quartz is fetched fresh at image build (pinned `v4.5.1`), so changes are
**layered, not forked**: add `OB1/docker/wiki-viewer/quartz-overlay/` (components
`*.tsx`, client `*.inline.ts`, patched `quartz.layout.ts`/`quartz.config.ts`).
The Dockerfile `COPY`s the overlay over the cloned Quartz tree after `git clone`,
before `npm ci`. Keeps `QUARTZ_REF` upgradeable (bump → re-apply overlay → diff).

---

## 3. Cross-cutting foundations (Phase 0)

- **P0.1 — `openbrain-workbench` skeleton.** Deno+Hono, health check, bearer auth, `obnet`+`llm-net`, `127.0.0.1:8814`. Add to [compose](OB1/docker/docker-compose.yml) **+ recovery scripts + stack-map**.
- **P0.2 — Caddy `/workbench/*` proxy** (both Caddyfiles), same-origin with the wiki.
- **P0.3 — Quartz overlay scaffold + asset config.** `quartz-overlay/` + Dockerfile `COPY`; a no-op component proving overlay layering + client `fetch()` to `/workbench/health` through the portal; confirm `content/assets/` is served (images/audio) but not paginated.
- **P0.4 — `wiki-assets` volume** wired to workbench + viewer (+ podcast/extract later).
- **P0.5 — Shared TS types** for source/thread/provenance/podcast shapes, mirrored against [init-sources.sql](OB1/docker/init-sources.sql)/[init-threads.sql](OB1/docker/init-threads.sql).

**P0 gate:** a custom Quartz component, served through the portal, calls the
authed workbench API and renders the response; an image dropped under
`content/assets/` renders in a page. Nothing touches `sources` yet.

---

## 4. Feature phases

### Phase 1 — Source Visibility & Provenance

**Goal:** On any entity/topic page, show the underlying sources (type, capture
date, title/URL, notebook/tags) with click-through to a raw source view.

- **Backend (read-only):** `GET /workbench/provenance?entity=<id>` (join `source_entities`→`sources`), cached per entity keyed on `last_compile_iso`; `GET /workbench/sources/:id`.
- **Wiki compiler:** emit a provenance sidecar at compile time (`content/<type>/<slug>.sources.json` or front-matter `sources:`) so panels render with no hot-path DB hit; the compiler already knows linked sources ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs), `WIKI_MAX_SOURCES`).
- **Quartz:** `ProvenancePanel.tsx` (collapsible "Sources" on entity pages), source-view route, "wiki ↔ source" toggle.

**Gate:** open any person/org/tool page → see real sources w/ metadata → click
through to raw source. No DB query on hot page loads.

---

### Phase 2 — User Notes System (the editable, additive layer — D-E)

**Goal:** Author Obsidian-style markdown notes **in Quartz** — live preview,
`[[wikilinks]]`, tags, notebook/folder grouping — stored in the human-owned
`notes/` layer and written **additively into the Open Brain DB**.

- **Reuse, don't reinvent:** `ingestNotes()` already maps `notes/<notebook>/file.md` ⇄ one thought via `metadata.note_path`, diff-based upsert/delete + extraction enqueue. We add a **browser editor that writes those files**.
- **Backend:** `PUT /workbench/notes/<path>` (validate path under `notes/`, write, `git add/commit`; optimistic concurrency via content-hash / `If-Match` for multi-session safety); `GET` + notes index.
- **Quartz:** `NotesEditor.tsx` + `.inline.ts` — editor w/ live preview, `[[…]]` autocomplete from `entities.md`/notes index, tag input; notebook = first folder under `notes/` (matches `ingestNotes()` `parts[1]`).
- **Decision (idea-doc Q1):** notes stay in the `notes/` git layer tethered to thoughts — **not** a separate `user_notes` collection. Already isolated; reuses the tether; no second storage model. This is the single read-write content layer (D-E).

**Gate:** create/edit a note in the browser → appears in vault, `[[links]]`
resolve, next compile tethers it to a thought + extracts entities; two-session
edit conflict is detected, not lost.

---

### Phase 3 — Source Retraction & Correction (D-D)

*Reframed from "source editing": sources are immutable source-of-truth, so the
corrective action is **removal**, not content edits.*

**Goal:** Let the user remove a source that was wrong or ingested by accident,
with a safe two-tier model and clear consequences.

- **Two tiers:**
  - **Soft retract (default, reversible):** flip `thread_sources.status → hidden`/`inactive` via the existing `set_thread_source_status()`. Source stays in DB, drops out of that thread's provenance/search surface; restorable. No re-embed, no data loss.
  - **Hard delete (operator-confirmed, irreversible):** `DELETE FROM sources WHERE id=…`; `source_entities`/`thread_sources`/`session_sources` cascade; next compile's **orphan sweep** removes now-unsupported wiki pages. Used for accidental/garbage ingests.
- **No content editing:** source `content` is never mutated (D-D). To "correct" a source, retract + re-ingest a corrected copy. (Drops the prior draft's `original_content`/`edited_text` dual-field schema — not needed.) Tag/notebook *organization* is handled by `thread_sources` links, not by editing source rows.
- **Backend:** `POST /workbench/sources/:id/retract {scope: thread|global, mode: hide|delete}`; restore endpoint for soft-hidden. Guard hard-delete behind explicit confirmation + audit (`metadata.retracted_by`, `retracted_at`).
- **Quartz:** `SourceRetractor.tsx` on the source view — "Hide from thread" vs "Delete permanently" with a confirm dialog showing what pages/links will be affected.

**Gate:** soft-hide removes a source from a thread's provenance and restores
cleanly; hard-delete purges it, cascades links, and the dependent wiki page is
swept on next compile. A delete always requires explicit confirmation.

---

### Phase 4 — Direct Source Import Pipeline (incl. images — D-F)

**Goal:** Drag-and-drop / picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT,
**images** → extract (text + embedded images, OCR where needed) → chunk → embed →
land as first-class read-only `sources`, with progress + error handling, and
images rendered in Quartz.

- **`openbrain-extract` sidecar (the content-core borrow):** `POST /extract` (multipart) → `{ markdown, title, metadata{author,date,page_refs}, pages, images[] }`. OCR via Tesseract/Pillow for scanned PDFs and standalone images. Add to compose **+ recovery + stack-map**.
- **Workbench `POST /workbench/import` (async):** store upload → `openbrain-extract` → write extracted images to `content/assets/<source-id>/` and rewrite markdown image refs → chunk (semantic/sentence-boundary default, fixed+overlap fallback, tuned for `bge-m3`) → embed → `find_or_create_source()` (dedup) → `link_source_to_thread(..., 'deliberate')` → return `job_id`. `GET /workbench/jobs/:id` for progress (async queue avoids blocking on large files).
- **Schema (additive):**
  - **Widen `content_type` CHECK** to add `'image'` (and `'docx'`/`'pptx'` or keep file docs as `'manual'`) — drop+re-add constraint, **values only widened**, never removed.
  - **New `source_chunks(source_id, idx, content, embedding VECTOR(1024))`** for long-doc retrieval granularity + a chunk-aware `match_sources` variant. Largest new schema item — gate explicitly (see [§10 Q2](#10-open-questions)).
- **Images in Quartz:** once assets land in the vault and markdown references them, Quartz renders them inline (no Quartz code change beyond the P0 asset config). Source-view shows the image + OCR text.
- **Quartz:** `ImportDropzone.tsx` (drag/drop + picker, format validation, per-file progress, corrupt/unsupported errors) + `ImportStatus.tsx` (job dashboard).

**Gate:** drop a PDF, a DOCX, and a PNG → all extract, chunk, embed, dedupe,
link to a thread, get entity-extracted, and surface via Phase-1 provenance; the
PNG and any images embedded in the PDF render inline in Quartz; corrupt files
fail with a clear message.

---

### Phase 5 — Podcast Service (extended — D-G)

**Goal:** Generate a podcast **on request** from a thread's sources, show it in a
dedicated section with **playback**, organize **by thread**, and let the user
save the transcript as a note.

- **`openbrain-podcast` sidecar (the podcast-creator borrow):** `POST /generate {thread_id, speakers:1–4, voice_profiles, style}` → pulls the thread's sources from OB1 → script via local Qwen (`llama-cpp`) → audio via **local TTS** → writes `assets/podcasts/<id>.mp3` + transcript → upserts `podcasts` row. Async w/ `job_id`. Add to compose **+ recovery + stack-map**.
  - ⚠️ **Local TTS is an open decision ([§10 Q1](#10-open-questions)).** `podcast-creator`/esperanto default to cloud TTS (OpenAI/ElevenLabs/Google), which violates the local-runtime rule. Candidates: **Kokoro-82M**, **Piper**, **XTTS-v2** (voice cloning, heavier). May require adding a local TTS provider to / forking `podcast-creator`'s synthesis step.
- **Schema (additive):** **`podcasts(id, thread_id FK, title, status, audio_path, transcript, speaker_config jsonb, created_at)`** — `thread_id` gives the "organized by thread" requirement directly.
- **Transcript → note:** `POST /workbench/podcasts/:id/transcript-to-note` reuses the Phase-2 notes write path (transcript becomes an editable, tethered user note). The `'podcast_transcript'` `content_type` already exists if we ever also want it as a source.
- **Quartz:** `PodcastPanel.tsx` — a "Podcasts" section (global + per-thread view) listing podcasts with an `<audio>` player streaming from `assets/podcasts/…`; "Generate podcast for this thread" action; "Save transcript as note" button.

**Gate:** request a podcast for a thread → script + audio generate locally, the
episode appears in the Podcasts section under its thread, plays back in-browser,
and its transcript can be saved as an editable note.

---

## 5. Design decisions — answering the idea doc + new asks

| Question | Decision | Rationale |
|---|---|---|
| **Q1 Notes vs sources storage** | Notes in `notes/` git layer tethered to thoughts; no `user_notes` collection. | Isolated already; reuses `ingestNotes()`; no new storage model (D-E). |
| **Q2 Source "versioning"** | **None — sources are immutable (D-D).** Correction = retract + re-ingest. Soft-hide via `thread_sources.status`; hard-delete cascades + orphan-sweeps. | Read-only source-of-truth; the prior dual-field edit model is dropped. |
| **Q3 Re-embed threshold** | N/A for sources (no edits). Imports embed once; re-ingest creates a fresh source. | No edit path to re-embed. |
| **Q4 Import chunking** | Semantic/sentence-boundary default, fixed+overlap fallback, `bge-m3`-tuned; new `source_chunks` table. | Better long-doc retrieval; only sizeable new schema, gated in P4. |
| **(New) Images** | content-core extracts them; workbench writes to `content/assets/`; Quartz renders inline; `content_type` widened to `'image'`. | Closes D-F with the ingestion borrow + a vault asset convention. |
| **(New) Podcast TTS** | Local TTS engine required (Kokoro/Piper/XTTS) behind `podcast-creator`; script via local Qwen. **Engine choice open.** | Local-runtime rule forbids cloud TTS; ON's default providers are cloud. |
| **(New) Podcast storage/org** | `podcasts` table w/ `thread_id`; audio on `wiki-assets`; transcript→note via P2. | Direct "organized by thread" + "transcript as note" mapping. |
| **(New) Write-API home** | New `openbrain-workbench`, not the MCP server. | Keeps browser/multipart/auth surface off the limited MCP + cloud-gateway contract. |
| **(New) Extraction/podcast engines** | Python sidecars (`openbrain-extract`, `openbrain-podcast`) wrapping content-core / podcast-creator. | Reuse ON's best IP without Python in the Deno process; lets us retire ON. |
| **(New) Quartz customization** | `quartz-overlay/` COPY'd over the pinned clone; not in-place fork. | Keeps `QUARTZ_REF` upgradeable. |

---

## 6. File / touchpoint index

- **New services:** `OB1/docker/workbench/` (Deno+Hono), `OB1/docker/extract/` (FastAPI+content-core), `OB1/docker/podcast/` (FastAPI+podcast-creator+local TTS).
- **Compose:** [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — add `openbrain-workbench`, `openbrain-extract`, `openbrain-podcast`, `wiki-assets` volume.
- **Recovery (convention — required):** `scripts/emergency-recovery.ps1` + `.bat` inventory + start/stop order.
- **Stack map (convention — required):** [.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md).
- **Caddy:** [config/caddy/Caddyfile](config/caddy/Caddyfile), [OB1/docker/Caddyfile](OB1/docker/Caddyfile) — `/workbench/*` + `assets/` serving.
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` (+ Dockerfile COPY) — `ProvenancePanel`, `NotesEditor`, `SourceRetractor`, `ImportDropzone`, `ImportStatus`, `PodcastPanel`, `.inline.ts`, layout/config.
- **Wiki compiler:** [OB1/docker/wiki-service/wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) — provenance sidecar (P1); notes write interplay (P2); confirm orphan sweep covers hard-deletes (P3).
- **Schema (additive only):** widen `content_type` CHECK + `source_chunks` (`init-source-chunks.sql`), `podcasts` (`init-podcasts.sql`), retraction audit columns in `metadata`.
- **Search:** chunk-aware `match_sources` variant.

---

## 7. Sequencing & dependencies

```
P0 Foundations ─┬─> P1 Provenance ──┬─> P3 Retraction
                │                    │
                ├─> P2 Notes ────────┼──────────────┐
                │                    │              ▼
                └────────────────────┴─> P4 Import ─┴─> P5 Podcasts
```

- P0 unblocks all. P1 (read) and P2 (notes-write) parallelize after P0.
- P3 (retraction) depends on P1's source view.
- P4 (import + images) depends on P0/P1; adds the extract sidecar + assets + `source_chunks`. **No longer depends on P3** (no source-editing/versioning).
- P5 (podcasts) depends on P4 (sources to summarize), P2 (transcript→note), and threads; adds the podcast sidecar + local TTS + `podcasts`.
- **ON decommission only after P4 *and* P5 ship + verified** ([§9](#9-relationship-to-iks--retiring-open-notebook)).

---

## 8. Risks & guardrails

- **Three-place change convention (CLAUDE.md):** each new container (`openbrain-workbench`, `openbrain-extract`, `openbrain-podcast`) updates compose **+** recovery scripts **+** stack-map together. Run `/stack-map` before/after compose edits.
- **OB1 guard rails:** never alter/drop existing `thoughts`/`sources` columns — all schema work additive (widening a CHECK = drop+re-add with values only added). No secrets in files; reuse env-var/`MCP_ACCESS_KEY`. (OB1's "MCP servers must be remote Edge Functions" rule is the *public-contribution* contract; this is local deployment infra under `OB1/docker/`, like the already-local `openbrain-mcp` — keep out of upstream PR scope.)
- **Local-runtime rule for podcasts:** no cloud TTS. Pick a local engine ([§10 Q1](#10-open-questions)); GPU contention with `llama-cpp` matters — see [llama-swap perf tuning](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md). Prefer a CPU-capable TTS (Piper/Kokoro) so podcast renders don't starve inference.
- **Destructive action (D-D hard delete):** irreversible + cascades. Require explicit confirm, show affected pages/links, audit `retracted_by`/`retracted_at`, default the UI to soft-hide. Don't expose hard-delete to non-operators.
- **New browser-facing write surface:** auth (bearer), portal-only reach, validate/normalize note + asset paths (no `../` escape from `notes/`/`assets/`), cap upload size, **sandbox the extractor** (untrusted file + image parsing is a classic RCE vector — run `openbrain-extract` unprivileged, no extra network).
- **Static-build friction (D-A):** heavy logic in `.inline.ts` calling the API; thin build-time components; overlay must not block `QUARTZ_REF` upgrades.
- **GPU/compile churn:** large imports + podcast gen + recompile. Change-watch debounce (3 min) coalesces; batch embeddings, don't fan out unbounded.
- **Asset volume growth:** audio + extracted images accumulate on `wiki-assets`; ensure backups cover it and add a retention/cleanup story (hard-deleted sources should drop their assets too).
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the `/wiki` vault repo (that *is* the notes write mechanism), staying local (no remote push; D16).

---

## 9. Relationship to IKS & retiring Open Notebook

IKS was repointing **Open Notebook's** sources onto OB1 Postgres so ON/OWUI/wiki
share one store. D-B changes the destination: **Quartz becomes the workbench and
ON is retired.**

- **Keep (built, reused here):** the unified OB1 schema — `sources`, `threads`, `thread_sources`, `sessions`, `session_sources`, `find_or_create_source()`, `set_thread_source_status()`. See [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/IMPLEMENTATION-PLAN-integrated-knowledge-system.md), [Integrated-knowledge-system-concept.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/Integrated-knowledge-system-concept.md).
- **Drop:** the ON-repoint phases, ON triage UI, SurrealDB-as-ON-store. (Suggestion-worker triage can later resurface as a Quartz component, not in ON.)
- **Decommission (post-P5, gated on verification):**
  1. Confirm P1–P5 cover ON's wanted features: import (P4, incl. images), provenance (P1), notes (P2), retraction (P3), **podcasts (P5)**. ON's chat sessions are intentionally dropped.
  2. Migrate ON-only source data still in SurrealDB into OB1 `sources` via `find_or_create_source()` (one-shot script).
  3. Remove `open_notebook` + its `surrealdb` dependency from [docker-compose.yml](docker-compose.yml), recovery scripts, stack-map, and the Tailscale serves for `:8443/:5055` ([entrypoint.sh](entrypoint.sh) / serve recipe).
  4. Update memory: this reverses the "repoint ON" direction in [three-layer-memory-stack-integration](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\three-layer-memory-stack-integration.md).

> ⚠️ Verify `open_notebook` is SurrealDB's only consumer before removing the
> `surrealdb` service. (See [SurrealDB v2 user gotcha](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\surrealdb-v2-define-user-gotcha.md) if re-provisioning.)

---

## 10. Open questions

1. **Local TTS engine for podcasts (blocking P5):** Kokoro-82M (quality, GPU/CPU), Piper (fast CPU, lighter voices), or XTTS-v2 (voice cloning, heavy/GPU)? Drives quality, latency, and GPU contention with inference. Does `podcast-creator` accept a custom local provider, or must we patch its synthesis step?
2. **`source_chunks` adoption (P4):** add the chunk table for long-doc retrieval (recommended), or keep one-row-per-source + representative-window embedding (no schema change, weaker retrieval)?
3. **`content-core` weight:** accept its dependency footprint in `openbrain-extract`, or start with lighter `PyMuPDF`/`python-docx`/`python-pptx` (+ `convert_heavy_file.py`) and add content-core only for formats those miss?
4. **Assets location:** keep audio/images inside the wiki git vault (`content/assets/`, versioned, bloats the repo) or a separate `wiki-assets` volume served by Caddy (cleaner, but a second thing to back up)? Affects backup + git size.
5. **Workbench auth model:** single operator bearer now, or per-user later (affects note/retraction attribution)?
6. **Podcast voice profiles:** fixed house voices, or user-configurable per request/thread? (Depends on the TTS engine's capabilities.)
7. **SurrealDB sole-consumer check** before §9 removal.

---

## 11. Suggested next step

Resolve **Q1 (local TTS)** and **Q2 (`source_chunks`)** — the two that gate the
new scope — then start **Phase 0** (workbench skeleton + Caddy route + Quartz
overlay + asset config + assets volume), since it unblocks all phases and proves
the in-Quartz-components + thin-API + sidecar architecture end-to-end before any
schema, extractor, or TTS work.
