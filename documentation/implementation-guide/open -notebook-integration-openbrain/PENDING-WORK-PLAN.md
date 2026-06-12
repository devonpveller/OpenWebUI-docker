# Pending Work Plan — Integrated Knowledge System (post-sandbox)

**Created:** 2026-06-06
**Companion to:** [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](IMPLEMENTATION-PLAN-integrated-knowledge-system.md) (canonical Progress Ledger + Decision Log) · [PROMOTION-RUNBOOK.md](PROMOTION-RUNBOOK.md)
**Purpose:** collect every pending / deferred / proposed implementation into one actionable plan so we can start the moment the **incoming OB1 changes** land. This is the "what's left" view; the IMPLEMENTATION-PLAN remains the source of truth for what's done.

> ⚠️ **Trigger:** the operator is finishing OB1 updates. Group A is blocked on those changes; Groups B–E are not. Reconcile Group A's schema assumptions against the actual OB1 changes before building.

> ✅ **Conformance check done (2026-06-06).** The OB1 thread-table changes are now in **live** `openbrain-db` (threads=38, thread_sources=777, sources=777, IKS-P1 schema + all functions). Verified read-only: **the ON fork already conforms** — identical function signatures, compatible superset columns (live adds `threads.slug`, `sources.last_edited_by/at`, `source_revisions` — inert to the fork), matching constraints; the fork's queries run against live with real data. **No fork code change needed.** Consequences:
> - **A2 (reconcile) → done.** Live is a compatible superset of the sandbox schema.
> - **B2/B3 mostly done in prod already** — the OB1-side schema + functions exist in live; promotion no longer needs to apply them (verify, don't re-create).
> - **⛳ NEW critical prerequisite (B-tier): `source_chunks = 0` in live.** 736 chunkable sources (738 web_article + 1 pdf; 38 research_synthesis excluded by design) are unchunked, so ON chat/ask/researcher return nothing until the **chunk-embedding-worker runs against `openbrain-db`**. This makes B1 (chunk-worker) the gating item for ON to *function* against live, not just a promotion nicety.
> - **Mapping decision:** the 38 live threads are deep_research-derived (topic+synthesis+sources), not ON user-notebooks — operator decides whether ON surfaces them (map `ob_thread_id`) or keeps its own threads.
> - **Optional resilience:** add a whole-source `match_sources`/content fallback when chunk search is empty, so ON isn't blank during the chunking window.

---

## 0. State of play (what already works in `iks-dev`)

The IKS is **functionally complete and validated in the sandbox**. OB1 Postgres is the source-of-truth for Open Notebook; SurrealDB holds only operational/UI state. Working + validated end-to-end:

- **Ingestion → OB1**: upload (file/URL/YouTube) → extracted text + bge-m3 embedding + `thread_sources(automatic,confirmed)`; notebook⇄thread 1:1; dedup via `find_or_create_source`. URL HTML via local readability+pandoc (images externalized); URL/file PDFs via content-core/docling.
- **Canonical chunking**: writer-agnostic `chunk-embedding-worker` (1200/150 + bge-m3) → `source_chunks`, hardened against binary/poison content.
- **Retrieval everywhere on OB1**: notebook chat (agentic researcher), source chat, global Ask/Search, podcast content — all read OB1 `source_chunks`; evidence-validated, cited, gap-honest.
- **Cross-thread suggestions + triage UI** (Phase 5/6), **Obsidian inbox stub** (Phase 7).
- **Editable OB1 connection pointer** + **TTS/STT model config** in Settings; podcast generation (researcher-validated briefing → audio) with live page refresh.

**Caveat:** all of the above runs from the **cp'd sandbox image** (`iks-notebook:local`) and the working-branch source. Nothing is committed; prod is untouched (D3/D4). The [PROMOTION-RUNBOOK.md](PROMOTION-RUNBOOK.md) is **stale** — it predates the chat-researcher, podcast, settings, search/ask, url-markdown, delete-unlink, and chunk-worker work and must be refreshed (see B7).

---

## Group A — Blocked on the incoming OB1 changes  ⛔ (do first, once OB1 lands)

### A1. Notes → OB1 "AI notes" (the main OB1-dependent feature)
Today ON notes live in SurrealDB; OB1 insights/notes are empty (source-chat "insights" come back empty; researcher grounding uses source passages only). The operator deferred this pending OB1's AI-notes feature ("in active development, I'll signal when ready").

**When OB1's AI-notes schema is in, wire:**
1. **Write path** — "Save as note" (from chat/research/source) and notebook notes → OB1 AI notes (per the OB1 table/RPC the changes introduce). Likely `api/routers/notes.py`, `open_notebook/domain/notebook.py` (`Note`), new `ob1_repository` note CRUD.
2. **Read path** — notebook/source views list OB1 AI notes; `context_builder._add_source_context` populates insights from OB1 AI notes (currently stubbed empty).
3. **Grounding** — include AI notes as first-class evidence in the chat researcher + Ask (alongside `source_chunks`), with the same citation/validation contract.
4. **Chunking** — decide whether AI notes are chunked/embedded into the canonical index (extend the chunk-worker scan to AI notes if they're retrievable).

**Depends on:** the exact OB1 AI-notes table name, columns, and any RPC/embedding contract. **Reconcile A1's design against the real OB1 changes before coding.**

### A2. Reconcile any OB1 schema/retrieval changes the update introduces
If the OB1 changes touch `sources`, `source_chunks`, `match_source_chunks`, thread tables, or embeddings, re-verify: `ob1_repository` queries, the chunk-worker scan, and the retrieval functions (`search_*`) still match. Quick smoke: ingest → chunk → chat/ask cite correctly.

---

## Group B — Promotion to production (Phase 8 close-out)  🚀 (operator-executed; agent prepares)

The runbook covers the original Phases 0–7; this session added a lot it doesn't reflect. Promotion now must also include:

### B1. Three-places sync for the **chunk-embedding worker** (17th container)
Add `openbrain-chunk-worker` to: (a) `OB1/docker/docker-compose.yml`, (b) `scripts/emergency-recovery.ps1` **and** `.bat` (service inventory + ordered start/stop — after `llama-cpp-embed`, before/with ON), (c) `.claude/skills/stack-map/references/workspace-stacks.md`. Then `/stack-map` shows no drift. (Suggestion-worker three-places was done; chunk-worker is owed.)

### B2. Prod OB1 schema (additive, idempotent)
Ensure prod `openbrain-db` has everything the fork reads: `init-threads.sql`, `content_hash` + `find_or_create_source`/lifecycle fns, **`init-source-chunks.sql`** (the `source_chunks` table + `match_source_chunks`), **`init-source-retract.sql`** (`retraction_committed_at`). Wire the init mounts in the prod compose. (Runbook §2 covers threads/content_hash; **add source_chunks + retract**.)

### B3. MCP tool surface → prod
Deploy the 11 thread/suggestion tools to `openbrain-mcp`; `docker restart openbrain-mcpo`; re-import OpenAPI in OWUI; confirm gateway allow-list unchanged (guardrail 5). (Runbook §5 covers this.)

### B4. Build + swap the ON fork image
`uv lock` first (asyncpg dep), build the fork, swap `image: lfnovo/open_notebook:v1-latest` → built fork; set `OB1_DB_HOST` + `EMBEDDING_API_BASE/KEY/MODEL` env. **This bakes in every sandbox cp'd fix** (url_markdown PDF guard, get_source deleted-filter, delete-unlink, title derivation, OB1 settings, search/ask gate, chat researcher, podcast). (Runbook §4.)

### B5. One-time source migration
`iks-dev/migrate-on-sources.py` dry-run → real (existing prod ON SurrealDB sources → OB1, dedup-aware). (Runbook §3.)

### B6. Backups cover new tables
Verify the `openbrain-db` backup includes `threads`, `thread_sources`, `sessions`, `session_sources`, `source_chunks`, and the `sources` additive columns. (Runbook §1 + ledger 8.4.)

### B7. **Refresh the PROMOTION-RUNBOOK** (it's stale)
Update: §6 container count 16→**17** (chunk-worker); §8 commit checklist to add this session's files (below); §9 — Phase 6 triage frontend **is built** (remove from "not built"); add the chunk-worker, chat-researcher, podcast, settings/OB1-pointer, search/ask, url-markdown, delete-unlink, title files.

**New/changed files this session (for the commit checklist), all on branch `feature/integrated-knowledge-system`:**
- `ai-stack`: `OB1/integrations/chunk-embedding-worker/*`, `OB1/docker/*` (chunk-worker service + source-chunks/retract init mounts), recovery scripts, stack-map, these docs + `iks-dev/`.
- `open-notebook`: `open_notebook/research/chat_researcher.py`, `open_notebook/graphs/{chat,source_chat,source,ask}.py`, `open_notebook/database/ob1_repository.py`, `open_notebook/domain/{notebook,ob1_settings}.py`, `open_notebook/utils/{url_markdown,context_builder}.py`, `api/routers/{chat,sources,notebooks,search,settings,podcasts}.py`, `api/podcast_service.py`, `commands/podcast_commands.py`, `api/main.py`, `next.config.ts`, `Dockerfile.single`, and the frontend (`ChatColumn/ChatPanel/source-references`, `OB1SettingsCard` + api-keys page, `use-podcasts`/`query-client`, `use-sources`/`sources.ts`/`SourcesColumn`, search page, `lib/api/ob1.ts` + `use-ob1.ts`).

### B8. Prod parity checks
After swap: ingest (HTML + PDF-URL), chat/ask cite, triage works, podcast generates, delete unlinks. Then `/stack-map` clean.

---

## Group C — Chat / researcher quality (optional; operator picks)

- **C1. Source-chat → full researcher.** Source chat still uses the simpler passage-RAG + grounded prompt; upgrade to `ChatResearcher` (mirror the notebook-chat wiring: `search_source_chunks` retrieve + per-source coverage). Unifies validation/citation quality.
- **C2. Ask/Search citations show source NAMES, not uuids.** Notebook chat threads a `sourceId→title` map; the `/search` Ask answer still renders `source:<uuid>`. Thread the same name map into `StreamingResponse`/the ask render (reuse `convertReferencesToCompactMarkdown(..., sourceNames)`).
- **C3. Relevance-gate tiers before MAP.** Add explicit relevant/trail/drop (+authority) classification before the map-reduce so low-value passages are dropped, not just truncated by the 32K budget.
- **C4. Fileshed-style journaling.** On-disk journal for resumability + very large pools (deep_research `fileshed.py`/`journal.py` pattern) — only if pools routinely exceed the bounded budget.
- **C5. Per-group verification in MAP.** Verify each group's grounded findings against its own evidence inside the map stage (catches drift earlier than the single final verify).

---

## Group D — Follow-up fixes / polish (from testing)

- **D1. SurrealDB source duplication.** In OB1 mode ON still writes a full `source` record (with `full_text`) to SurrealDB on every upload — duplicate storage of the canonical OB1 content. Decide: stop persisting `full_text` in SurrealDB (keep only the lightweight UI/processing record) or prune periodically. (Relates to the earlier "delete left a SurrealDB record" surprise — now unlink-only, but the row still lingers.)
- **D2. PDF figures/images.** PDFs are text-only; web-image externalize+serve infra (`/api/source-assets/`) exists to reuse for docling figures. Add figure extraction if visual fidelity matters.
- **D3. Title casing.** `_derive_title` Title-Cases ALL-CAPS PDF titles → "Ai"/"Llm" instead of "AI"/"LLM". Add an acronym-aware pass (keep known acronyms upper).
- **D4. content-core URL-PDF title at source.** D3 is a downstream fix; optionally have content-core/docling surface the PDF's `/Title` metadata so the derivation isn't needed.

---

## Group E — Deferred / optional

- **E1. gap → web-research auto-escalation.** Researcher currently hands back a paste-ready OWUI research prompt for material gaps (operator's choice). Auto-triggering `deep_research` remains deferred.
- **E2. Suggestion scheduling.** `POST /suggest` is on-demand; add to `openbrain-cron` (debounced) for automatic cross-thread suggestions.
- **E3. Phase 1.5 `OB1/schemas/` contrib mirror.** Optional; skipped.

---

## Open questions (resolve before/at kickoff)

1. **OB1 changes scope.** What exactly do the incoming OB1 updates include (AI-notes table/RPC? new/changed `sources`/`source_chunks` columns? embedding/retrieval changes)? This sets A1's contract and A2's reconcile list.
2. **Frontend direction (strategic).** A prior note (2026-06-02, [[quartz-workbench-retire-on]]) had ON *retired* in favor of a Quartz→OB1 workbench; this session built the **ON↔OB1 IKS** instead. Confirm ON-OB1 is the production path before doing Group B (promotion) — otherwise promotion targets the wrong frontend.
3. **Scope for this round.** Which of Group C/D to include now vs later.

---

## Recommended sequencing

1. **OB1 lands → A2 reconcile, then A1 (AI notes).** The headline OB1-dependent feature.
2. **D1–D3 quick polish** (cheap, independent).
3. **C1 + C2** (source-chat researcher + Ask citation names) — highest-value quality wins; C3–C5 as desired.
4. **B (promotion)** last — operator-executed, after everything above is validated in `iks-dev`; start by **refreshing the runbook (B7)** so it reflects reality, then schema (B1/B2) → tools (B3) → build/swap (B4) → migrate (B5) → verify (B8).

All of A–D is built/validated in `iks-dev` only; **no commits, prod untouched** until the operator runs the promotion (D4).
