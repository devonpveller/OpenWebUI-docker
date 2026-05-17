# Personal Memory Stack — Task Tracker (LIVING DOCUMENT)

> **Last updated:** 2026-05-16
> **Plan:** [INTEGRATION-PLAN.md](INTEGRATION-PLAN.md) — rationale, architecture, risks. Update both together.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → also bump _Last updated_ and add a Decision Log row if a choice changed.

## Current state

- **Active phase:** Phases 0–6 ✅ (Phase 6 wiring handed off). **Post-Phase-5 correctness rework ✅** (2026-05-17): source→entity extraction (real `source_entities` links, bleed fixed), notebook→topic synthesis, type subfolders, then the **evolving-vault model** (D18–D22) — supersedes the regenerable-mirror/force-push model. All live-verified, fully local. **Phase 7 GATED** (Open Notebook teardown — user-executed on accepted proof; do NOT execute). See **NOTES-VAULT-WORKFLOW.md**.
- **Phase 5 / B.4 + B.6 + final (2026-05-16):** **B.4** six `wiki_*` tools (`list_pages`/`read_page`/`search`/`get_backlinks`/`get_related`/`trigger_recompile`) added to `openbrain-ext` (Extension 7, reads shared `openbrain-wiki-data` volume + graph.json; no DB) — all 6 live-verified through the `openbrain-mcpo-ext` OpenAPI bridge (the Open WebUI path). **B.6** `--notebook` scoping in `generate-wiki.mjs` (in-scope = sources.notebook + thoughts.metadata.notebook; global edges/semantic excluded under scope; `notebook:` frontmatter; `WIKI_NOTEBOOK` passthrough) — verified in/out-of-scope. **Final:** new `openbrain-wiki` service (compile on boot + every `RECOMPILE_INTERVAL_HOURS` + `POST /recompile`; local git commit per changed compile, D16) + `openbrain-wiki-viewer` Quartz 4 (D15, watch-rebuild, serves :8812) + recipe emits `index.md` home page. Boot compile: 10 pages + graph.json committed; Quartz renders pages & resolves `[[wikilinks]]`→`href`. reqs 10,11,12,13.
- **Phase 5 / B.3 wikilinks + graph.json (2026-05-16):** `generate-wiki.mjs` — added `other_slug` to edge descriptors + prompt instruction to emit `[[slug\|name]]`, plus a deterministic `linkifyEntities()` backstop (idempotent; protects existing `[[]]`/code/URLs; bounded to the page's connected entities). New `writeGraphManifest()` writes `graph.json` (nodes `{id,label,type,slug,file}` + edges `{source,target,relation,weight,confidence}`, co_occurs_with excluded, dangling-edge filtered, `--no-graph`/`--graph-limit`), emitted once per run in file mode (batch + single). **Live-verified** (batch, fully local): 6 pages cross-linked bidirectionally with filename-matching slugs; `graph.json` valid (10 nodes/8 edges, all endpoints resolve). Note: links to entities outside a `--batch-limit` slice are valid dangling links (Obsidian-normal; full batch creates all pages). reqs 6,7.
- **Phase 5 / B.2 sources read path (2026-05-16):** extended `entity-wiki/generate-wiki.mjs` (Path B, non-breaking opt-in mirroring `--semantic-expand`): new `--include-sources`/`--max-sources` → `semanticExpandSources()` calls the `match_sources` RPC, feeds external docs into synthesis as scrubbed+fenced `<source>` blocks cited `[S:id]`, adds a `## Sources` provenance section + `source_doc_count`/`source_doc_ids` frontmatter (req 14). Scrub regex extended to neutralize `</source>` breakout. **Live-verified fully local** (qwen36-27b:nothink synth + bge-m3 match) in a node container on obnet+llm-net vs the Caddy/PostgREST proxy: ai-stack page cited 1 thought `[#3]` + 5 sources with correct external attribution, `## Sources` + frontmatter provenance present in both dry-run and file mode.
- **Phase 5 / B.1b extraction worker (2026-05-16):** patched worker live as `openbrain-entity-worker` (Dockerfile added; obnet+llm-net; loopback `127.0.0.1:8810`; `SUPABASE_URL=http://openbrain-rest` via supabase-js→Caddy→PostgREST; `CHAT_API_BASE`→llama-cpp `qwen36-27b:nothink`, `EMBEDDING_API_BASE`→llama-cpp-embed bge-m3, `EMBEDDING_DIMENSION=1024`). **End-to-end verified:** inserted real thought → `trg_queue_entity_extraction` auto-enqueued → 1 local LLM call (~15s) → **10 entities + 8 edges**, all semantically correct (Devon→works_on→ai-stack, PostgreSQL→uses→pgvector, Claude→member_of→Anthropic, …), queue row `complete`. Note: `entities` has no embedding column (worker embeds thoughts only, not entities), so this run exercised `CHAT_API_BASE`; `EMBEDDING_API_BASE` is covered by the bge-m3 path used elsewhere.
- **Phase 5 / B.1b data-bridge (2026-05-16):** added `openbrain-postgrest` (PGRST anon-role=service_role, obnet-only) + `openbrain-rest` Caddy proxy (`127.0.0.1:3001`, strips `/rest/v1`→PostgREST) + `50-init-grants.sql` (service_role schema-wide). Verified: `/rest/v1/entities|sources|thoughts` HTTP 200, real research rows served. Scripts need only env (`OPEN_BRAIN_URL=http://openbrain-rest`), zero code change.
- **Phase 5 / B.1a (2026-05-16):** `40-init-graph.sql` (entity-extraction + typed-reasoning-edges, BIGINT-adapted per D14) applied + mounted + reproducible. 6 graph tables, `content_fingerprint` additive + auto-trigger, no-op roles, `upsert_thought`/`thought_edges_upsert` shims. Verified: capture path intact, new thoughts auto-enqueue to `entity_extraction_queue`, CASCADE clean. No data loss, no Deno change.
- **Phase 4 ✅ COMPLETE & LIVE-VERIFIED.**
- **Phase 4 LIVE-VERIFY (2026-05-16):** real OWUI research session (3 web-search-API questions) persisted **3 `research_synthesis` + 50 `web_article` rows** to open-brain `sources`; `volatility=fast` auto-classified; `thoughts` table untouched (0). mnemory misuse fully resolved in production. Bundle re-pasted + `openbrain_key` valve set (confirmed by persistence firing).
- **Phase 4 outcome:** `evidence_memory.py` rewritten → open-brain `/research/persist` + `/research/lookup` (native schema; EV-header/label/artifact hacks retired per D10). `last_sources` threaded through both runners. Monolith mirrored, `mnemory_*` valves removed (D13). System prompts (research + general) updated to 3-layer. **Migration = no-op** (F8).
- **Phase 3 outcome:** `sources` table + `match_sources()` (vector 1024, reproducible migration) + `ingest_url`/`ingest_urls` MCP tools (local fetch+extract, bge-m3, no cloud/smolcrawl) — smoke-passed single + batch + long-doc + semantic search; test rows cleaned.
- **Phase 2 outcome:** Full OB1 stack live (5 containers), capture→1024-dim bge-m3 embed→semantic recall verified on local models. DB clean (0 rows).
- **Phase 1 outcome:** `OB1/docker/` is a ready raw-Postgres self-host stack (no Supabase). wiki-compiler scored Y8/P2/N4 → **Path B (extend)**.
- **Standing directive:** Open Notebook + surrealdb **stay running** until proven appropriately replaced (2026-05-16). Phase 7 is gated on accepted proof.

---

## Phase 0 — Prerequisites & discovery

- [ ] Verify Docker + Compose, ~10GB free disk for Postgres/pgvector + wiki dir
- [ ] Confirm local stack replaces `OPENAI_API_KEY`: `qwen36-27b`, `qwen36-27b:nothink`, `llama-cpp-embed` reachable
- [ ] Inventory `open_notebook` + `surrealdb` compose blocks, volumes, host ports (scope future retirement; do **not** stop them)
- [ ] Trace how `evidence_memory.py` reaches mnemory today (gateway vs direct `mnemory_url` valve) — informs Phase 4
- [ ] Produce gap list; stop if anything blocks
- **Exit:** gap list produced.

## Phase 1 — Clone OB1 + VALIDATION GATE ✅ DONE

- [x] Clone `github.com/devonpveller/OB1` @ `develop` → `d:\Open WebUI\ai-stack\OB1\`
- [x] Read setup docs + discovered `OB1/docker/` raw-Postgres self-host path
- [x] Read wiki recipe stack (wiki-compiler, entity-wiki, wiki-synthesis, typed-edge-classifier, ob-graph) end to end
- [x] Score the 14-requirement table → **Y8/P2/N4**
- [x] Effort: A=infeasible · B≈2–3.5wk · C≈5–8wk
- [x] Reported; **user chose Path B + core-only**
- **Exit:** ✅ gate passed.

## Phase 2 — Stand up OB1/docker core _(reshaped by F1 — no Supabase)_

- [ ] Verify `docker/init.sql` + `init-extensions.sql`: which schemas? (entity-extraction / typed-reasoning-edges / graph present, or only core + 6 life-apps?) — gates Phase 5
- [ ] Decide integration: standalone `OB1/docker/` compose vs merge into ai-stack compose
- [ ] Create gitignored `docker/.env` (MCP_ACCESS_KEY, POSTGRES_PASSWORD, MCPO_API_KEY, DEFAULT_USER_ID); ensure ai-stack `.gitignore` covers `OB1/` vendored repo + secrets
- [ ] `docker compose up -d` full stack: `openbrain-db` + `openbrain-mcp` + `openbrain-ext` + `openbrain-mcpo` + `openbrain-mcpo-ext` (D6 revised: run all, wire selectively)
- [ ] Confirm joins `ai-stack_llm-net`; embeddings→`llama-cpp-embed` (bge-m3 1024), chat→`qwen36-27b:nothink`
- [x] Smoke: capture a thought via :8808, semantic-search it back (local models only) — **PASSED** (1024-dim bge-m3 embed, recall OK at low threshold)
- [x] Test row cleaned (DB back to 0 thoughts)
- **Exit:** ✅ thought round-trips through OB1 core on local models.

## Phase 3 — OpenBrain `sources` ingest _(spec Task 4)_

- [ ] Check `recipes/` + `extensions/` for an existing sources/ingest extension before building
- [ ] Add `sources` table (schema per spec Task 4, local embed dim)
- [ ] `openbrain_ingest_url(url, notebook?, tags?)` — fetch via smolcrawl, extract, embed, write row, return id
- [ ] `openbrain_ingest_urls(urls[], notebook?)` — parallel batch
- [ ] YouTube transcript extractor (modular)
- [ ] PDF text extractor (modular)
- [ ] Smoke: ingest a URL; agent answers a fact from it via `openbrain`
- **Exit:** URL ingested and queryable via `openbrain`.

## Phase 4 — Migrate research off mnemory _(resolves the misuse)_

- [ ] Repoint `deep_research/evidence_memory.py` persistence → OpenBrain `sources`
- [ ] Repoint cache lookup → OpenBrain (drop mnemory `EV:research` search)
- [ ] Synthesis surfaced via wiki, not mnemory
- [ ] One-time migrate existing `⟦EV:research⟧` mnemory memories → OpenBrain `sources`
- [ ] Patch modular `smolcrawl/deep_research/` **and** `deep_research_tool.py` monolith (keep in sync; do NOT touch stale `data/openwebui/deep_research_function.py`)
- [ ] Flag user: re-paste `deep_research_tool.py` into OWUI → Tools
- [ ] Verify: new research run writes OpenBrain, not mnemory; mnemory gains no new `EV:research`
- **Exit:** research decoupled from mnemory; cache served from OpenBrain.

## Phase 5 — Wiki compiler _(spec Task 5 / companion §§2–3)_

- [x] **Path B.1** ✅ Data bridge (PostgREST+Caddy) + local entity-extraction worker live & end-to-end verified (2026-05-16)
- [x] **Path B.2** ✅ sources read path — `--include-sources` via `match_sources`, `[S:id]` citations + `## Sources` provenance, fully local, live-verified (2026-05-16) — req 2 (+ req 14)
- [x] **Path B.3** ✅ `[[wikilinks]]` (prompt + deterministic backstop) + `graph.json` manifest — live-verified batch, fully local (2026-05-16) — reqs 6,7
- [x] **Path B.4** ✅ Six `wiki_*` MCP tools in `openbrain-ext` (Extension 7) — live-verified via mcpo-ext bridge (2026-05-16) — req 13
- [x] **Path B.5** Local LLM repoint ✅ (code, 2026-05-16): non-breaking `CHAT_API_BASE`/`EMBEDDING_API_BASE` overrides (cloud defaults preserved). Patched: `_shared/helpers.ts` (`embedText` local-first + `fetchLocalMetadata` provider), `_shared/config.ts` (`EMBEDDING_DIMENSION` env, dflt 1536), worker `index.ts` (`extractEntities` local branch), `typed-edge-classifier/classify-edges.mjs` (local OpenAI-compat path + zero-pricing/single-model override, ANTHROPIC key optional), `entity-wiki/generate-wiki.mjs` (`EMBEDDING_DIMENSION`-driven preflight, was hardcoded 1536). `wiki-synthesis/*` already env-driven (`LLM_BASE_URL`), no patch. `.mjs` node --check OK; Deno type-check deferred to docker build (no host deno).
- [x] **Path B.6** ✅ First-class `notebook` scoping (`--notebook`/`WIKI_NOTEBOOK`, frontmatter) — live-verified in/out-of-scope (2026-05-16) — req 10
- [x] All extraction/relation/classify passes → `qwen36-27b:nothink` (B.5; worker live-verified)
- [x] All synthesis passes → `qwen36-27b` (recipe `LLM_MODEL`; wiki-service sets it)
- [x] Output → git-backed dir; commit per changed compile (D16; `openbrain-wiki`, verified)
- [x] Scheduled (interval) + on-demand `wiki_trigger_recompile` (verified end-to-end)
- [x] Read-only web viewer added to compose — Quartz 4 (D15), serves :8812, watch-rebuild
- [x] Expose all six `wiki_*` tools (verified via mcpo-ext)
- [ ] Compile-failure alerting (companion §5.2 R: stale wiki) — DEFERRED (parking lot: status endpoint exists; alerting not wired)
- [~] Smoke: topic-synthesis answered from `wiki` w/ page refs (Phase 6); wiki rebuildable from OpenBrain alone (✅ — `down` volume + recompile rebuilds)
- **Exit:** wiki compiles, serves, and is queryable; fully regenerable.

## Phase 6 — Wire MCP + routing skill + smoke tests _(spec Tasks 6–8)_

Handoff doc: **PHASE6-WIRING-HANDOFF.md** (exact `.mcp.json` entries +
OWUI tool-server steps + conversational smokes).

- [x] Write `~/.claude/skills/memory-stack-routing/SKILL.md` verbatim (Task 7) ✅
- [~] `.mcp.json`: `openbrain`(8808) + `openbrain-ext`(8809, holds wiki_*) — **agent self-config blocked by harness; documented for user to paste + reload/approve** (D17)
- [~] OWUI tool servers: `open-brain` + `open-brain-extensions` via mcpo bridges on `ai-stack_llm-net` — **user step, documented** (no host port by design)
- [x] Smoke 2 — OB1 records: `capture_thought` → `search_thoughts` round-trip ✅ (after F12 fix)
- [x] Smoke 3 — OB1 source: `ingest_url`/sources path proven (B.2 — 52 sources ingested + semantically retrieved) ✅ infra
- [x] Smoke 4 — wiki: `wiki_search`/`wiki_read_page` return synthesis w/ `[S:id]`/`[#id]` provenance ✅ (B.4)
- [ ] Smoke 1 — mnemory UI check — **user step** (needs mnemory mgmt UI)
- [ ] Smoke 5 — conversational routing 3/3 — **user step** (needs live OWUI/Claude session)
- **Exit:** infra smokes pass ✅; conversational smokes 1 & 5 are the remaining user-run items.

## Phase 7 — Retire Open Notebook _(GATED ON PROOF — user-executed)_

> Open Notebook + surrealdb **remain UP** until every proof item below is accepted by the user.

- [ ] Proof (a): notebook-scoped view of sources + notes + wiki pages (OB1 dashboard + Obsidian graph filtered by notebook)
- [ ] Proof (b): ingest UX parity (`openbrain_ingest_url` from chat covers drop-a-URL/PDF)
- [ ] Proof (c): project-scoped Q&A (agent + routing skill, scoped by notebook name)
- [ ] User reviews and **accepts** replacement proof
- [ ] Prepare exact teardown commands for `open_notebook` + `surrealdb` services + volumes
- [ ] **User executes** irreversible deletions
- [ ] Confirm stack converged on three-layer model
- **Exit:** proof accepted AND services/volumes removed.

---

## Decision Log

| Date       | #   | Decision / change                                                                                                                                                                                                                                                                                                                                                       | Rationale                                                                                                              |
| ---------- | --- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| 2026-05-16 | D1  | Supabase self-hosted in ai-stack                                                                                                                                                                                                                                                                                                                                        | Local-first/privacy posture; keep personal records + sources on-box                                                    |
| 2026-05-16 | D2  | Fix research↔mnemory misuse by migrating _when OB1 lands_, not now                                                                                                                                                                                                                                                                                                      | Zero research-cache downtime; misuse knowingly live through Phase 4                                                    |
| 2026-05-16 | D3  | Local llama-cpp only: `qwen36-27b` (think) for synthesis, `qwen36-27b:nothink` for high-volume extraction, `llama-cpp-embed` for vectors                                                                                                                                                                                                                                | Consistency with privacy-first stack; spec's OpenAI default overridden                                                 |
| 2026-05-16 | D4  | Wiki output git-backed + read-only web viewer                                                                                                                                                                                                                                                                                                                           | Spec §5.1 most-flexible option; mobile/cross-device                                                                    |
| 2026-05-16 | —   | Open Notebook + surrealdb stay UP until proven replaced; Phase 7 gated on accepted proof                                                                                                                                                                                                                                                                                | User directive — no teardown on faith                                                                                  |
| 2026-05-16 | F1  | **Phase 1 finding:** `OB1/docker/` is a complete raw-Postgres self-host stack already wired to ai-stack (bge-m3 1024 + qwen36-27b:nothink). **D1 (self-host Supabase) is largely MOOT — no Supabase; direct Postgres.** R4 eliminated. D3 already satisfied for OB1 core.                                                                                               | The deployable fork ships the local stack; Phase 2 shrinks dramatically                                                |
| 2026-05-16 | F2  | **Phase 1 score:** wiki-compiler = Y8/P2/N4 → Path B band. Recommended **Path B (extend)**. Awaiting user path decision (hard-stop gate).                                                                                                                                                                                                                               | Mature, fresh recipe; gaps additive (sources, wikilinks, graph.json, wiki-MCP, local typed-edge port, PostgREST→PG)    |
| 2026-05-16 | D5  | Wiki path = **Path B (extend)** chosen                                                                                                                                                                                                                                                                                                                                  | Reuse mature core; gaps additive; A infeasible, C wasteful                                                             |
| 2026-05-16 | D6  | ~~core+sources only~~ → **revised: run full stack, wire selectively**                                                                                                                                                                                                                                                                                                   | Ext bloat is opt-in per client (separate ext mcpo), not global; life-app tools = "domain records → OpenBrain" per spec |
| 2026-05-16 | F3  | **Phase 2 gate finding:** docker `init.sql`=thoughts only; `init-extensions.sql`=6 life-app schemas only. **NO entities/edges/thought_entities/thought_edges/graph schema in deployable DB.** Path B Phase 5 must port `schemas/entity-extraction` + `schemas/typed-reasoning-edges` (+ob-graph) to the raw-PG `auth`-shim pattern.                                     | Quantifies a core chunk of Phase 5 up front                                                                            |
| 2026-05-16 | D7  | OB1 stays a **standalone compose** (`OB1/docker/`, `name: open-brain`, `ai-stack_llm-net` external), not merged into ai-stack compose                                                                                                                                                                                                                                   | Purpose-built standalone; matches prior deployment pattern; lower risk                                                 |
| 2026-05-16 | F4  | **Phase 2 finding:** bge-m3 cosine scores run lower than OpenAI; default `search_thoughts` threshold 0.5 hid a 68% match. Lower the default (or always pass low threshold) when wiring clients/OWUI in Phase 6.                                                                                                                                                         | Avoid silent empty-recall; tuning, not a bug                                                                           |
| 2026-05-16 | D8  | **A5 reversed:** primary sources producer = `deep_research_tool.py` (not smolcrawl). Phase 3+4 merge: `sources` populated by deep_research persistence repointed off mnemory. smolcrawl→open-brain deferred (bloat).                                                                                                                                                    | User scope clarification — sources that matter for the wiki are research-gathered                                      |
| 2026-05-16 | D9  | **D8 amended:** open-brain ALSO gets a direct user-facing `ingest_url` / `ingest_urls` MCP tool (spec Task 4 reinstated) — end user will feed URLs manually for wiki use. Fetch+extract is **local in the open-brain server (no smolcrawl, no cloud)**; embed via bge-m3 → `sources` row.                                                                               | User: open-brain needs a URL/batch entry point for wiki use later                                                      |
| 2026-05-16 | F5  | **Phase 3 schema DONE:** `sources` table + `match_sources()` (vector 1024) applied live + mounted as reproducible `30-init-sources.sql`. EV:research staleness is now real columns (mnemory header hack retired in Phase 4).                                                                                                                                            | —                                                                                                                      |
| 2026-05-16 | D10 | **Phase 4 guidance:** the `⟦EV:research⟧` header, `research_key` labels, artifact archiving, header-parsed staleness in `evidence_memory.py` are byproducts of misusing mnemory — **NOT requirements**. Phase 4 redesigns for open-brain's native structured schema and retires the workarounds; do not port them.                                                      | User: don't treat mnemory-era research structure as a spec; open-brain is purpose-built for this                       |
| 2026-05-16 | F6  | **Phase 3b DONE:** `ingest_url` + `ingest_urls` (local fetch+extract, bge-m3, →`sources`) built, rebuilt, smoke-passed (single/batch/long-doc/semantic); test rows cleaned.                                                                                                                                                                                             | —                                                                                                                      |
| 2026-05-16 | F7  | **llama-cpp-embed caps embed input at ~512 tokens** (physical batch). Mitigation = 1600-char embed cap in ingest (full body still stored). **D11: do NOT raise embed batch size** — embed runs on the tight **2080 (GPU dev 1)**; raising it is VRAM-costed. Richer embeddings, if ever needed, must be VRAM-neutral (client-side chunk + mean-pool of ≤512-tok calls). | User: 2080 VRAM tight, caution on VRAM increases                                                                       |
| 2026-05-16 | F12 | **DB-direct pooled services strand on `openbrain-db` restart.** `openbrain-mcp` (Deno pg pool) started 11:12; `openbrain-db` was recreated 12:48 by a later `docker compose up -d` (new services `depends_on: openbrain-db: service_healthy`). The stale pool → **`Broken pipe (os error 32)` on every DB op** (capture_thought, thought_stats — models were fine, isolating it to DB). PostgREST-based services (entity-worker, wiki-service) immune (HTTP reconnects per request). **Fix:** `docker compose restart openbrain-mcp` (verified: Smoke 2 capture→search green after). **Operational rule:** any time `openbrain-db` restarts, also restart DB-direct services (`openbrain-mcp`; `openbrain-ext` too if it predates the DB). | Caught running Phase-6 Smoke 2 |
| 2026-05-16 | D17 | **3-lane routing maps onto 2 OB1 servers + mnemory** (no separate wiki MCP server). `wiki_*` tools live in `openbrain-ext` beside life-app tools; lane separation is enforced by the routing skill + system prompts, not a server boundary. `.mcp.json`: `openbrain`(8808 core) + `openbrain-ext`(8809). A dedicated wiki-only door can be split later; functionally unnecessary. Flagged to user in PHASE6-WIRING-HANDOFF.md. | Pragmatic: avoids a redundant server; routing works by tool name/description |
| 2026-05-17 | D22 | **Stable-slug/alias registry (spec req 8).** Entity slug is PINNED for life in `entities.metadata.wiki_slug` (set on first generation via `ensurePinnedSlug`, reused forever even if `canonical_name` changes); `slugFor()` everywhere (filenames, graph nodes, related-entity links). Pages emit `aliases: [canonical_name]`. ⇒ `[[slug]]` link targets never drift on rename/merge; renamed display still resolves. Closes the only note→generated link-break risk. | User-authorized; linchpin for a durable notes vault |
| 2026-05-17 | D21 | **Notes → OpenBrain tethered ingest.** `notes/<nb>/*.md` ingested as ONE OpenBrain thought keyed by `metadata.note_path` (notebook = first folder, `source=user_note`). Edit note → PATCH same row (NO duplicate); delete → remove row. Closes the loop: user notes become durable source-of-truth records that feed extraction. **Verified:** create→count 1; edit→count stays 1 + content updated. | User: notes tethered, not iterated; anti-drift preserved |
| 2026-05-17 | D20 | **Incremental compile.** `generate-wiki --ids <dirty>`; wiki-service computes dirty = `entities.updated_at >= last_compile` (watermark in gitignored `.wikistate.json`). graph.json/entities.md/topic always refreshed (cheap). Full `--batch` only first run / >BATCH_LIMIT / manual. **Verified:** a note touched 5 entities → only 5 pages regenerated, not 108. | User: don't recompile the whole wiki every time |
| 2026-05-17 | D19 | **Evolving-vault git model — SUPERSEDES D16+/force.** No wipe; `git pull --rebase` before compile; normal fast-forward push; `WIKI_GIT_FORCE` default **false** (force retired as steady state, manual escape only). Commits accumulate as a real history; user note-commits and generated content-commits interleave. **Verified:** boot + note compiles pushed FF (`pushed:true`, not reconciled), `pull_ok:true`. | User: commits pushed as-is/naturally, not force-overwritten |
| 2026-05-17 | D18 | **Two-layer vault — SUPERSEDES regenerable-mirror.** `content/**` = compiler-owned (incremental, regenerable); `notes/**` = human-owned (compiler NEVER touches); vault-root `index.md` home; `content/entities.md` (was index.md). Quartz serves the whole vault so cross-layer `[[wikilinks]]` resolve. Topology: user clones the remote, authors in Obsidian, Obsidian-Git pushes; service pulls. **Verified:** notes/ byte-identical after compile; commit diff scoped to content/. | User: Obsidian note-writing workflow; read/discover + author + feed back |
| 2026-05-17 | D16+ | **Wiki private-remote push WIRED & LIVE** (D16 extended per user request). `wiki-service.mjs` pushes after each compile-commit over SSH (`WIKI_GIT_REMOTE=git@github.com:devonpveller/openbrain-wiki-data.git`); dedicated passphrase-less ed25519 **deploy key** at gitignored `secrets/openbrain-wiki-deploy_key` (mounted RO; copied to 0600 temp in-container — Win bind-mount perms). Push failure never fails the compile (local commit stays SoT). One-time `--force` (user-authorized) seeded the repo over GitHub's auto-init; steady-state pushes are normal fast-forwards (verified: remote main == local HEAD, push.pushed:true). Setup gotchas hit & resolved: Win ACL on key (`icacls /inheritance:r`), PS drops empty `-N ""` (use `cmd /c`), Docker Desktop single-file mount needs `--force-recreate` after host key swap, deploy key first added to wrong repo (OB1) — `ssh -T git@github.com` reports bound repo. | User: push wiki to private repo; OB1 repo untouched/separate |
| 2026-05-16 | D16 | **Git-backed wiki = local commits only** (user-chosen). The shared wiki volume is a local git repo; each successful compile auto-commits. No remote, **no secrets required from the user**. Remote push remains an opt-in env-gated add-on for later. | User pick: local-only, regenerable history, zero external dependency |
| 2026-05-16 | D15 | **Wiki viewer = Quartz 4** (user-chosen). Obsidian-markdown→static-site, native `[[wikilinks]]`/backlinks/search/graph. Added as its own container reading the shared wiki volume; regenerable. | User pick over Perlite/minimal/defer — best read UX, regenerable |
| 2026-05-16 | F11 | **PostgREST + Bearer = PGRST300.** PostgREST with no `PGRST_JWT_SECRET` returns HTTP 500 `PGRST300 "Server lacks JWT secret"` for **any** request carrying `Authorization: Bearer …`. supabase-js (worker) **and** the recipe `sbClient` send it unconditionally — so the earlier "B.1b data-bridge live-verified" only held for raw curl (no Bearer); real clients would have 500'd. **Fix:** Caddyfile `header_up -Authorization` + `-apikey` so every caller falls through to anon=service_role. Preserves app-layer-trust posture, zero script changes. Re-verified worker queue read = 200. | Caught wiring the live worker (peek/claim swallow errors → silent `processed:0`) |
| 2026-05-16 | F8  | **Phase 4 migration = no-op:** mnemory holds **zero `⟦EV:research⟧` memories** (labelled query empty; broad search shows only normal user/project memories). The cache structure was never populated — nothing to migrate, nothing stranded. | Verified via transient llm-net curl to mnemory:8050 |
| 2026-05-16 | F10 | **B.1b finding:** `integrations/entity-extraction-worker` + `_shared/helpers.ts` **hardcode** cloud endpoints (openrouter.ai / api.openai.com / api.anthropic.com), no base-URL override. Same for typed-edge-classifier (Anthropic). Local worker can't run verbatim — needs the B.5 local-LLM patch (point chat→`llama-cpp:8080/v1` qwen36-27b:nothink, embed→`llama-cpp-embed:8080/v1` bge-m3 1024) as a prerequisite. **Reorders Path B: do B.5 (local-LLM repoint) before the extraction worker.** | Worker LLM endpoints not configurable |
| 2026-05-16 | F9  | **Phase 5 blocking finding:** upstream graph schemas (`entity-extraction`, `typed-reasoning-edges`) assume Supabase OB1 `thoughts.id UUID` + `content_fingerprint` + `upsert_thought` + `service_role`/`authenticated` roles. The deployable docker `thoughts` is **`BIGSERIAL` id, no content_fingerprint, no roles** (minimal kubernetes-deployment variant). Verbatim port impossible — 3 structural gaps (id type, fingerprint, roles/RPC). | Surfaced porting Path B.1a |
| 2026-05-16 | D14 | **Strategy 2 (non-destructive adaptation), chosen.** Port graph schemas with `thought*_id` as **BIGINT** (match docker thoughts.id); **additively** add `thoughts.content_fingerprint` + auto-populate trigger (guardrail: adding cols OK); create no-op `service_role`/`authenticated`/`anon` roles; provide `upsert_thought` shim. NOT Strategy 1 (UUID convergence) — that needs `down -v` (data loss) + Deno server rewrite + violates "don't alter core id". Reversible/additive; diverges upstream schemas (accepted, already forked via docker variant). | Non-destructive, guardrail-compliant, no data loss, no Deno change |
| 2026-05-16 | D13 | **mnemory_* valves removed entirely** from both `models.py` + monolith Valves (3 fields: url/api_key/user_id); `evidence_*` descriptions reworded to open-brain. Justified by F8 (no migration needed) + research output = structured data, not user-personal. Valves parity 29==29 verified. ⚠️ OWUI valve panel only reflects this **after re-paste** (it shows the deployed bundle). | User: research results are structured data, mnemory valves can be omitted |
| 2026-05-16 | D12 | **Deploy gate:** `openbrain_key` Tool valve defaults `""` → persistence/cache **skipped until set**. On OWUI re-paste, set `openbrain_key` = OB1 docker `.env` `MCP_ACCESS_KEY`. `openbrain_url` default `http://openbrain-mcp:8000` works (openwebui ↔ openbrain-mcp on ai-stack_llm-net). mnemory_* valves retained but unused (no migration needed per F8). | Graceful-skip design; user action required to activate |

## Open items / parking lot

- Confirm A1 (OB1 fork/branch/path), A2 (MCP client targets), A5 (smolcrawl reused for fetch) — validate during Phase 0/1.
- Research frontend (companion §4): deferred; revisit only after ~1 week of real use.
- Embedding dimension of `llama-cpp-embed` — fill in before any pgvector index (Phase 2).
