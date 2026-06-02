# Quartz 4 Expansion Plan — Quartz as the Open Brain Workbench

> **Status:** Plan / pre-implementation
> **Branch context:** `feature/integrated-knowledge-system`
> **Supersedes/reframes:** the Open Notebook repoint phases of the in-flight
> Integrated Knowledge System (IKS) work — see [§9](#9-relationship-to-iks--retiring-open-notebook).
> **Source idea:** [initial-quartz-4-expansion-idea.md](documentation/implementation-guide/expand-quartz-4/initial-quartz-4-expansion-idea.md)

---

## 0. Decisions locked for this plan

These were confirmed by the operator before drafting and drive everything below.

| # | Decision | Consequence |
|---|----------|-------------|
| D-A | **Interactive layer = in-Quartz client components.** | Add Preact components + client-side scripts to the Quartz viewer; back them with a thin write-API on Open Brain (the one unavoidable backend — Quartz can't write Postgres from a static page). |
| D-B | **Borrow from Open Notebook, then retire it.** ON is ~80% redundant to existing infra. | Harvest ON's reusable pieces (content-core extraction pipeline, source/notes UX patterns) into the Quartz+OB1 stack. Decommission `open_notebook` + its SurrealDB usage at the end. |
| D-C | **All four features, phased**, in the idea doc's order: Provenance → Notes → Editing → Import. | Four phases, each with its own ship gate. Import lands last because it reuses the chunking/embedding/edit plumbing built in P1–P3. |

The "outgrown Open Notebook" framing is the spine of this plan: Quartz already
mimics Obsidian and is *already wired directly to Open Brain*. The cost of
making it a workbench is feature development + borrowed implementations, not a
new architecture.

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

### 1.2 What Open Brain already gives us (the foundation)

Most of the ingestion machinery the idea doc asks for **already exists** — this
plan mostly wires UI to it, not builds it from scratch.

| Capability | Where it lives | State |
|------------|----------------|-------|
| `sources` table (url, title, content, content_type, tags, notebook, embedding `VECTOR(1024)`, metadata jsonb, research linkage) | [OB1/docker/init-sources.sql](OB1/docker/init-sources.sql) | ✅ live |
| `find_or_create_source()` — dedup on url/content-hash, returns `was_duplicate` | [OB1/docker/init-threads.sql](OB1/docker/init-threads.sql) | ✅ live |
| `threads`, `thread_sources` (link_type auto/suggested/deliberate, status), `sessions`, `session_sources` | [OB1/docker/init-threads.sql](OB1/docker/init-threads.sql) | ✅ live |
| `source_extraction_queue` + auto-enqueue trigger on insert/update (fingerprint-gated) | [OB1/docker/init-source-graph.sql](OB1/docker/init-source-graph.sql) | ✅ live |
| Entity extraction worker → `source_entities` | `openbrain-entity-worker` ([compose](OB1/docker/docker-compose.yml)) | ✅ live |
| Embeddings — `bge-m3`, 1024-dim, via `llama-cpp-embed` | MCP server `getEmbedding()` | ✅ live |
| `match_sources()` vector search RPC | [OB1/docker/init-sources.sql](OB1/docker/init-sources.sql) | ✅ live |
| MCP/HTTP server (Hono on Deno) — `ingest_url`, `ingest_urls`, `search`, `capture_thought`… | [OB1/integrations/kubernetes-deployment/index.ts](OB1/integrations/kubernetes-deployment/index.ts) (`Deno.serve(... app.fetch)` at the tail) | ✅ live |
| Wiki compiler with change-watch (3-min debounce), notes ingest, orphan sweep, on-demand `POST /recompile` | [OB1/docker/wiki-service/wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ live |
| **Tethered notes** — `notes/<notebook>/file.md` ⇄ one OpenBrain thought, by `metadata.note_path`; diff-based upsert/delete on compile | `ingestNotes()` in [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) | ✅ live |

### 1.3 The gaps the idea doc actually targets

1. **No write path from the browser.** Quartz pages are static; there's no upload, edit, or note-authoring endpoint.
2. **PDF/DOCX/PPTX text extraction is a stub.** `ingest_url` returns `[PDF source not text-extracted: …]`; no multipart file upload exists at all.
3. **Provenance isn't surfaced.** Entity pages are synthesized from sources, but the reader can't see *which* sources, or click through to them.
4. **Source editing has no UI and no versioning.** `original` vs `edited` snapshots don't exist; re-embed is implicit via the fingerprint trigger.

### 1.4 What we borrow from Open Notebook (D-B)

ON's reusable IP, to be ported — **not kept running**:

- **`content-core`** (PyPI) — ON's parser matrix for PDF/DOCX/PPTX/MD/URL/audio→markdown. This is the single most valuable borrow; it replaces the PDF stub. (See [§6 / Phase 4](#phase-4--direct-source-import-pipeline).)
- **Source-card + notebook-view UX** — how ON lists sources with type/date/insight chips and lets you open a source. We mirror this as Quartz components, reading from OB1 instead of SurrealDB.
- **Token-based chunking + per-chunk embedding** patterns (ON uses LangChain `tiktoken`); we re-implement against `bge-m3` 1024-dim and pgvector, which OB1 already standardises on.

Everything else ON offers (podcast generation, multi-speaker TTS, chat sessions,
SurrealDB job queue) is **out of scope** and dropped with ON.

---

## 2. Target architecture

### 2.1 The one unavoidable backend

D-A ("in-Quartz components") still needs a place to write to, because a static
site cannot mutate Postgres. The smallest honest addition is a **thin write-API**
that the Quartz client scripts call:

```
        ┌──────────────────────── Quartz viewer (static + hydrated) ───────────────────────┐
        │  ProvenancePanel   NotesEditor   SourceEditor   ImportDropzone   (Preact + .inline.ts)│
        └───────────────┬───────────────────────────────────────────────────────────────────┘
                        │  fetch()  (same-origin /workbench/* via Caddy)
                        ▼
        ┌──────────────────────── openbrain-workbench API ─────────────────────────┐
        │  POST /workbench/import   (multipart → content-core → find_or_create_source)│
        │  GET  /workbench/sources/:id     PATCH /workbench/sources/:id  (edit+version)│
        │  GET  /workbench/provenance?entity=…   (sources behind a wiki page)         │
        │  PUT  /workbench/notes/<path>    (write notes/ markdown + commit)           │
        │  GET  /workbench/jobs/:id        (import/embed progress)                    │
        └───────────────┬───────────────────────────────────────────────────────────┘
                        │
        ┌───────────────┼───────────────────────────────────────────────┐
        ▼               ▼                                               ▼
  OB1 Postgres     llama-cpp-embed (bge-m3)                     openbrain-wiki (recompile)
  sources/threads  embeddings                                   change-watch picks it up
  source_extraction_queue → entity-worker → source_entities → wiki regenerates pages
```

**Build vs. extend decision for the API:** two viable homes —

- **(Recommended) New `openbrain-workbench` service** (Deno+Hono, mirrors the existing MCP server style) on `obnet`/`llm-net`, published `127.0.0.1:8814`. Keeps the write-surface that browsers touch isolated from the MCP/cloud-gateway path (which is deliberately limited to 8 tools). Cleaner auth story, independent restart, no risk to the MCP contract.
- **(Alternative) Extend the existing Hono app** in [index.ts](OB1/integrations/kubernetes-deployment/index.ts) with `/workbench/*` routes. Less new infra, but mixes a browser-facing, multipart, session-cookie surface into the machine-to-machine MCP server. **Not recommended.**

> The `content-core` extractor is Python; the Deno API shells out to / HTTP-calls
> a small **`openbrain-extract`** Python sidecar (FastAPI wrapping `content-core`).
> This is the cleanest way to reuse ON's library without putting Python parsers in
> the Deno process. Decided in [Phase 4](#phase-4--direct-source-import-pipeline).

### 2.2 Routing & exposure

- Caddy gains a `/workbench/*` reverse-proxy to `openbrain-workbench:8814`, same host/origin as the wiki so client `fetch()` is same-origin (no CORS). Touch [config/caddy/Caddyfile](config/caddy/Caddyfile) and the OB1 [Caddyfile](OB1/docker/Caddyfile).
- **Auth:** the workbench is reachable only via the Tailscale portal (already the wiki's only public path). Add a shared-secret/bearer check (reuse `MCP_ACCESS_KEY` pattern) so the write endpoints aren't open on the tailnet to non-operators. Confirm in [§8](#8-risks--guardrails).

### 2.3 How writes become visible (the loop)

Every write lands in OB1 Postgres → the existing `source_extraction_queue`
trigger and the wiki **change-watch** (3-min debounce) already pick it up and
recompile → Quartz's file watcher reloads. **We add no new sync mechanism** — we
feed the loop that already runs. The only new wiki-compiler work is *displaying*
provenance and round-tripping notes edits (Phases 1–2).

### 2.4 Quartz customization model

Quartz is fetched fresh at image build (pinned `v4.5.1`), so our changes must be
**layered on, not forked in place**:

- Add a `quartz-overlay/` dir in [OB1/docker/wiki-viewer/](OB1/docker/wiki-viewer/) with our components (`quartz/components/*.tsx`), client scripts (`*.inline.ts`), and a patched `quartz.layout.ts` / `quartz.config.ts`.
- The Dockerfile `COPY`s the overlay over the cloned Quartz tree after `git clone`, before `npm ci` (so any new deps install). `entrypoint.sh` keeps patching `ignorePatterns`.
- This keeps the Quartz upgrade path sane: bump `QUARTZ_REF`, re-apply overlay, diff.

---

## 3. Cross-cutting foundations (Phase 0)

Do these once, before feature phases. They are the "infrastructure" the idea doc
deliberately stripped out but which D-A forces us to own.

- **P0.1 — `openbrain-workbench` service skeleton.** Deno+Hono, health check, bearer auth, `obnet`+`llm-net`, published `127.0.0.1:8814`. Add to [compose](OB1/docker/docker-compose.yml). **Per the workspace convention, also update the recovery scripts' service inventory + startup/shutdown order and the stack-map reference** — see [§8](#8-risks--guardrails).
- **P0.2 — Caddy `/workbench/*` proxy** (both Caddyfiles), same-origin with the wiki.
- **P0.3 — Quartz overlay scaffold.** `quartz-overlay/` + Dockerfile `COPY`; a no-op custom component that renders to prove the layering + client `fetch()` to `/workbench/health` works end-to-end through the portal.
- **P0.4 — Shared TypeScript types** for the source/thread/provenance shapes, generated from or hand-mirrored against the SQL in [init-sources.sql](OB1/docker/init-sources.sql) / [init-threads.sql](OB1/docker/init-threads.sql).

**P0 ship gate:** a custom Quartz component, served through the portal, can call
the authed workbench API and render the response. Nothing touches `sources` yet.

---

## 4. Feature phases

Each phase: **Goal → Components/touchpoints → Ship gate.** Order per D-C.

### Phase 1 — Source Visibility & Provenance

*Lowest risk, establishes the source→wiki link pattern reused everywhere later.*

**Goal:** On any entity/topic wiki page, show the underlying sources (type,
capture date, title/URL, notebook/tags) with click-through to a source view.

**Backend (read-only):**
- `GET /workbench/provenance?entity=<id>` → joins `source_entities` → `sources` for that page's entity (and topic pages: the thoughts/sources the synthesis used). Cache per entity keyed on `last_compile_iso` to avoid per-page-load vector/DB latency (the idea doc's caching note).
- `GET /workbench/sources/:id` → single source raw view.

**Wiki compiler:** emit a machine-readable provenance sidecar during compile
(e.g. `content/<type>/<slug>.sources.json`, or front-matter `sources:` keys) so
the panel can render instantly without a live DB hit. The compiler already knows
the linked sources when it builds a page ([generate-wiki.mjs](OB1/recipes/entity-wiki/generate-wiki.mjs), `WIKI_MAX_SOURCES`).

**Quartz components:**
- `ProvenancePanel.tsx` — collapsible "Sources" section on entity pages, fed by front-matter/sidecar (build-time) with a client `.inline.ts` fallback to the live API for freshness.
- Source-view route/component — raw source content + metadata; the read-side of the Phase-3 editor.
- "Wiki view ↔ Source view" toggle.

**Ship gate:** open any person/org/tool page → see its real sources with metadata
→ click through to the raw source. No DB query on hot page loads (served from
compiled sidecar/cache).

---

### Phase 2 — User Notes System

*Self-contained; validates the write loop end-to-end with the lowest blast radius
because the `notes/` layer and its tether already exist.*

**Goal:** Author Obsidian-style markdown notes **in Quartz**, with live preview,
`[[wikilinks]]` to entities/topics/other notes, tags, and folder/notebook
grouping — stored in the human-owned `notes/` layer, tethered into OpenBrain.

**Reuse, don't reinvent:** notes already round-trip —
`ingestNotes()` in [wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs)
maps `notes/<notebook>/file.md` ⇄ one thought via `metadata.note_path`, with
diff-based upsert/delete and extraction enqueue. We are adding a **browser editor
that writes those files**, not a new storage model.

**Backend:**
- `PUT /workbench/notes/<path>` — validate path under `notes/`, write file, `git add/commit` (the compiler's `git pull --rebase` before compile keeps it FF — mind the multi-session conflict note in the idea doc; use optimistic concurrency via content hash / `If-Match`).
- `GET /workbench/notes/<path>` and a notes index endpoint.

**Quartz components:**
- `NotesEditor.tsx` + `.inline.ts` — markdown editor with live preview, `[[…]]` autocomplete sourced from `entities.md` / notes index (link resolver), tag input.
- "New note / Edit note" affordance; notebook = first folder under `notes/` (matches `ingestNotes()`'s `parts[1]` convention).

**Design answer (idea doc Q1 — notes vs sources storage):** notes stay in the
**`notes/` git layer tethered to thoughts** (their current home), *not* a separate
`user_notes` source collection. This is already isolated from `content/` and from
`sources`, gives query isolation for free, and avoids a second storage model. The
idea doc's "separate collection" recommendation is satisfied by the existing
thought-tether, so we keep it.

**Ship gate:** create + edit a note in the browser → it appears in the vault,
`[[wikilinks]]` resolve in Quartz, and the next compile tethers it to a thought
and runs entity extraction. Edit conflict from two sessions is detected, not
silently lost.

---

### Phase 3 — Source Editing Interface

*Builds on Phase 1's source view; produces the versioning + re-embed logic Phase 4
reuses.*

**Goal:** Correct/annotate captured sources without losing the original; edit
metadata (tags, notebook, collection, confidence/notes); re-embed on meaningful
change.

**Schema (additive — never alter existing `sources` columns, per OB1 guard rails):**
- Add `original_content TEXT` (nullable; populated on first edit), `edited_at TIMESTAMPTZ`, `edited_by TEXT`. Keep `content` as the live/edited text so `match_sources()` and extraction need no change.
- **Versioning (idea doc Q2):** start with **dual-field** `original_content` / `content`. Add a lightweight `source_revisions` append-only log only if revision history is later demanded. Decided: dual-field now.

**Backend:**
- `PATCH /workbench/sources/:id` — update `content` and/or metadata; on first edit snapshot `original_content`; recompute `content_hash`.
- **Re-embed trigger (idea doc Q3):** the `source_extraction_queue` trigger already re-enqueues on `content` change *only when the fingerprint differs* ([init-source-graph.sql](OB1/docker/init-source-graph.sql)) — so re-embedding is automatic and delta-gated for free. Add an explicit **"Re-embed now"** button for operator override. Threshold-based (20% delta) auto-reembed is **not** needed given fingerprint gating; metadata-only edits must **not** bump the content fingerprint (so a tag change doesn't trigger re-embed). Verify the trigger keys on content only.

**Quartz components:**
- `SourceEditor.tsx` + `.inline.ts` — inline editor over the Phase-1 source view; metadata form; "view original ↔ edited" diff; "Re-embed" action; audit line (`edited_at`/`edited_by`).

**Ship gate:** edit a source's text → `original_content` preserved, `content`
updated, re-embed enqueued automatically, entity links + dependent wiki pages
refresh on next compile. Editing only tags does **not** trigger re-embed.

---

### Phase 4 — Direct Source Import Pipeline

*Heaviest; reuses chunking/embedding/edit/provenance from P1–P3. This is the
headline ask and where Open Notebook's `content-core` is harvested.*

**Goal:** Drag-and-drop / file-picker upload of PDF, DOC/DOCX, PPT/PPTX, MD, TXT
(images via OCR as a fast-follow) → parse → chunk → embed → land as first-class
`sources` in Open Brain, with progress + error handling.

**`openbrain-extract` Python sidecar (the ON borrow):**
- FastAPI service wrapping **`content-core`** (PDF/DOCX/PPTX/MD/URL→markdown). Falls back to / can be swapped for `PyMuPDF` + `python-docx` + `python-pptx` if we want to drop the heaviest deps. The in-repo [heavy-file-ingestion](OB1/skills/heavy-file-ingestion/) `convert_heavy_file.py` is the fallback reference implementation if `content-core` proves too heavy.
- `POST /extract` (multipart) → `{ markdown, title, metadata: {author, date, page_refs…}, pages }`.
- New service in [compose](OB1/docker/docker-compose.yml) on `obnet`; **also update recovery scripts + stack-map** (convention).

**Workbench API:**
- `POST /workbench/import` (multipart, async) → store upload → call `openbrain-extract` → chunk (token-based, overlap tuned for `bge-m3`; **semantic/sentence-boundary chunking** per idea doc Q4 as the default, fixed-size as fallback) → embed each chunk via `llama-cpp-embed` → `find_or_create_source()` (dedup) → link to a thread via `link_source_to_thread(..., 'deliberate')` → return a `job_id`.
- `GET /workbench/jobs/:id` → progress/status for the dashboard (queue avoids blocking UI on large files).
- **Chunking model note:** OB1 currently stores one row per source with a single `embedding`. Multi-chunk import needs a decision: (a) one `sources` row + a new `source_chunks(source_id, idx, content, embedding)` table for retrieval granularity, or (b) keep one row, embed a representative window (today's behavior). **Recommend (a)** — add `source_chunks` (additive) so large docs retrieve well; `match_sources()` gains a chunk-aware variant. This is the largest new schema item; gate it explicitly.

**Quartz components:**
- `ImportDropzone.tsx` + `.inline.ts` — drag/drop + picker, format validation, per-file progress, error surfacing (corrupt/unsupported).
- `ImportStatus.tsx` — job dashboard polling `/workbench/jobs/:id`.

**Ship gate:** drop a real PDF and a DOCX → both extract to text, chunk, embed,
appear as `sources` (deduped), link to the chosen notebook/thread, get entity-
extracted, and surface on the relevant wiki pages via Phase-1 provenance — with
visible progress and a clear error on a corrupt file.

---

## 5. Design decisions — answering the idea doc's "lock before coding"

| Idea-doc question | Decision for this stack | Rationale |
|---|---|---|
| **Q1 Notes vs sources storage** | Notes stay in the `notes/` git layer, tethered to thoughts (existing). No `user_notes` source collection. | Already isolated from `content/` and `sources`; reuses `ingestNotes()`; zero new storage model. |
| **Q2 Source editing versioning** | Dual-field `original_content` / `content` now; append-only `source_revisions` only if demanded. | Matches idea doc's own recommendation; additive to `sources` (respects OB1 guard rail against altering columns). |
| **Q3 Re-embed threshold** | No % threshold. Rely on the existing fingerprint-gated `source_extraction_queue` trigger (auto delta-reembed) + an explicit "Re-embed now" button. Metadata-only edits must not bump the content fingerprint. | The trigger already does delta-gated re-embed; a threshold would duplicate it. |
| **Q4 Import chunking** | Semantic/sentence-boundary chunking as default, fixed-size+overlap fallback, tuned for `bge-m3` (1024-dim). New `source_chunks` table for retrieval granularity. | Better retrieval; the only sizeable new schema, gated in P4. |
| **(New) Where does the write-API live** | New `openbrain-workbench` Deno+Hono service, not the MCP server. | Keeps browser-facing/multipart/auth surface off the limited MCP + cloud-gateway contract. |
| **(New) Extraction engine** | Borrow `content-core` in a Python `openbrain-extract` sidecar; `convert_heavy_file.py` / PyMuPDF as fallback. | Reuses ON's best IP without Python parsers in Deno; lets us retire ON. |
| **(New) Quartz customization** | `quartz-overlay/` COPY'd over the pinned Quartz clone; not an in-place fork. | Keeps `QUARTZ_REF` upgradeable. |

---

## 6. File / touchpoint index (where work lands)

- **New service:** `OB1/docker/workbench/` (Deno+Hono API) + `OB1/docker/extract/` (FastAPI + content-core).
- **Compose:** [OB1/docker/docker-compose.yml](OB1/docker/docker-compose.yml) — add `openbrain-workbench`, `openbrain-extract`.
- **Recovery (convention — required):** `scripts/emergency-recovery.ps1` + `.bat` service inventory + start/stop order.
- **Stack map (convention — required):** [.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md).
- **Caddy:** [config/caddy/Caddyfile](config/caddy/Caddyfile), [OB1/docker/Caddyfile](OB1/docker/Caddyfile) — `/workbench/*`.
- **Quartz overlay:** `OB1/docker/wiki-viewer/quartz-overlay/` (+ Dockerfile COPY) — components, `.inline.ts`, `quartz.layout.ts`.
- **Wiki compiler:** [OB1/docker/wiki-service/wiki-service.mjs](OB1/docker/wiki-service/wiki-service.mjs) — provenance sidecar emit (P1); notes write path interplay (P2).
- **Schema (additive only):** `OB1/docker/init-sources.sql` patch or new `init-source-edits.sql` (`original_content`, `edited_at/by`), new `init-source-chunks.sql` (`source_chunks`).
- **Embeddings/search:** chunk-aware `match_sources` variant.

---

## 7. Sequencing & dependencies

```
P0 Foundations ─┬─> P1 Provenance ──┬─> P3 Editing ──┐
                │                    │                ├─> P4 Import
                └─> P2 Notes ────────┘ (P3 versioning + re-embed reused by P4)
```

- P0 unblocks all. P1 and P2 can proceed in parallel after P0 (read vs notes-write).
- P3 depends on P1's source view. P4 depends on P3 (versioning/re-embed) and P1 (provenance display) and adds the extract sidecar + `source_chunks`.
- **ON decommission happens only after P4 ships and is verified** ([§9](#9-relationship-to-iks--retiring-open-notebook)).

---

## 8. Risks & guardrails

- **Three-place change convention (CLAUDE.md):** every new container (`openbrain-workbench`, `openbrain-extract`) must update compose **+** recovery scripts **+** stack-map together. The `/stack-map` skill checks this drift — run it before/after compose edits.
- **OB1 guard rails:** never alter/drop existing `thoughts`/`sources` columns — all schema work is additive. No secrets in files; reuse env-var/`MCP_ACCESS_KEY` patterns. (The OB1 "MCP servers must be remote Edge Functions" rule is the *public contribution* contract; this is local deployment infra under `OB1/docker/`, consistent with the already-local `openbrain-mcp` — keep it out of any upstream PR scope.)
- **Browser-facing write surface = new attack surface.** Auth the workbench (bearer/shared-secret), keep it reachable only via the Tailscale portal, validate/normalize note paths (no `../` escape out of `notes/`), cap upload size, sandbox the extractor (untrusted file parsing).
- **Static-build friction (D-A):** interactive components fight Quartz's model. Keep heavy logic in `.inline.ts` calling the API; keep build-time components thin. Don't let the overlay block `QUARTZ_REF` upgrades.
- **GPU/compile churn:** large imports → many embeddings + extraction + recompile. The change-watch debounce (3 min) already coalesces; respect [llama-swap perf tuning](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\llama-swap-perf-tuning.md) lane budgets — batch imports, don't fan out unbounded embedding calls.
- **Multi-session note/source edits:** optimistic concurrency (content-hash / `If-Match`); the compiler's `pull --rebase` keeps git FF but the editor must detect a stale base.
- **Never commit/push on the operator's behalf** ([git-handling-boundaries](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\git-handling-boundaries.md)) — except the workbench's own programmatic commits **inside** the `/wiki` vault repo (that *is* the notes write mechanism), which stay local (no remote push; D16).
- **Tailscale serve drift:** new `/workbench` portal route may need a serve entry — see [Tailscale serve restore recipe](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\tailscale-serve-restore-recipe.md).

---

## 9. Relationship to IKS & retiring Open Notebook

The in-flight Integrated Knowledge System work (current branch) was repointing
**Open Notebook's** sources onto OB1 Postgres so ON, OWUI, and the wiki share one
`sources`/`threads` store. D-B changes the destination: instead of repointing ON,
**Quartz becomes the workbench and ON is retired.** What carries over and what changes:

- **Keep (already built, reused here):** the unified OB1 schema — `sources`, `threads`, `thread_sources`, `sessions`, `session_sources`, `find_or_create_source()`. This plan builds *directly* on it. See [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/IMPLEMENTATION-PLAN-integrated-knowledge-system.md) and [Integrated-knowledge-system-concept.md](documentation/implementation-guide/open%20-notebook-integration-openbrain/Integrated-knowledge-system-concept.md).
- **Drop:** the ON-repoint phases (ON reading/writing OB1), the ON triage UI, the SurrealDB-as-ON-store layer. The triage/suggestion concept (suggestion-worker → `thread_sources` pending) can later resurface **as a Quartz component**, not in ON.
- **Decommission steps (post-P4, gated on verification):**
  1. Confirm P1–P4 cover ON's still-wanted features (import, source view/edit, notes). Podcast/TTS/chat explicitly dropped.
  2. Migrate any ON-only source data still in SurrealDB into OB1 `sources` via `find_or_create_source()` (one-shot script).
  3. Remove `open_notebook` + its `surrealdb` dependency from [docker-compose.yml](docker-compose.yml), the recovery scripts, the stack-map, and the Tailscale serves for `:8443/:5055` ([entrypoint.sh](entrypoint.sh) / serve recipe).
  4. Update memory: this plan **reverses** the "repoint ON" direction recorded in [three-layer-memory-stack-integration](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\three-layer-memory-stack-integration.md).

> ⚠️ SurrealDB is also used by other things — verify `open_notebook` is its only
> consumer before removing the `surrealdb` service. (See the [SurrealDB v2 user gotcha](C:\Users\yamao\.claude\projects\d--Open-WebUI-ai-stack\memory\surrealdb-v2-define-user-gotcha.md) if re-provisioning is needed.)

---

## 10. Open questions to resolve before P0

1. **Workbench auth model** — shared bearer (operator-only) now, or per-user later if the tailnet has multiple humans? (Affects edit attribution `edited_by`.)
2. **`source_chunks` adoption (P4)** — add the chunk table for retrieval granularity (recommended), or keep one-row-per-source and embed a representative window (no schema change, weaker retrieval on long docs)?
3. **`content-core` weight** — accept its dependency footprint in the `openbrain-extract` image, or start with the lighter `PyMuPDF`/`python-docx`/`python-pptx` set (reusing `convert_heavy_file.py`) and add `content-core` only for formats those miss?
4. **OCR scope** — images/scanned PDFs in P4, or fast-follow? (Adds Tesseract/Pillow to the extractor.)
5. **SurrealDB sole-consumer check** — does anything besides `open_notebook` use it before §9 removal?

---

## 11. Suggested next step

Lock the five open questions in [§10](#10-open-questions-to-resolve-before-p0),
then start **Phase 0** (workbench skeleton + Caddy route + Quartz overlay proof),
since it unblocks all four feature phases and validates the in-Quartz-components
+ thin-API architecture end-to-end before any schema or extractor work.
