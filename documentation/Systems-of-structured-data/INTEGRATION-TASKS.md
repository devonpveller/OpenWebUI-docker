# Personal Memory Stack — Task Tracker (LIVING DOCUMENT)

> **Last updated:** 2026-05-16
> **Plan:** [INTEGRATION-PLAN.md](INTEGRATION-PLAN.md) — rationale, architecture, risks. Update both together.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → also bump _Last updated_ and add a Decision Log row if a choice changed.

## Current state

- **Active phase:** Phase 4 ✅ **COMPLETE & LIVE-VERIFIED**. **Entering Phase 5** (Path B wiki compiler).
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

- [ ] **Path B.1** Port wiki scripts PostgREST→docker Postgres; local entity-extraction runner (replace Edge-Function trigger)
- [ ] **Path B.2** sources read path (dep: Phase 3) — req 2
- [ ] **Path B.3** Emit `[[wikilinks]]` + export `graph.json` — reqs 6,7
- [ ] **Path B.4** Six `wiki_*` MCP tools into `openbrain-ext` Deno server — req 13
- [ ] **Path B.5** Local LLM repoint: entity/topic via `LLM_BASE_URL`; patch 1536→1024 preflight; port `typed-edge-classifier` off hardcoded Anthropic
- [ ] **Path B.6** First-class `notebook` scoping — req 10
- [ ] All extraction/relation/classify passes → `qwen36-27b:nothink`
- [ ] All synthesis/topic/contradiction passes → `qwen36-27b` (think)
- [ ] Output → git-backed dir; commit per successful compile
- [ ] Scheduled (daily) + on-demand `wiki_trigger_recompile`
- [ ] Read-only web viewer service added to compose (Quartz/Perlite/Next)
- [ ] Expose `wiki_search` / `wiki_read_page` / `wiki_get_backlinks` / `wiki_get_related` / `wiki_list_pages` / `wiki_trigger_recompile`
- [ ] Compile-failure alerting (companion §5.2 R: stale wiki)
- [ ] Smoke: topic-synthesis question answered from `wiki` with compiled-page refs; wiki rebuildable from OpenBrain alone
- **Exit:** wiki compiles, serves, and is queryable; fully regenerable.

## Phase 6 — Wire MCP + routing skill + smoke tests _(spec Tasks 6–8)_

- [ ] Gateway-pattern door(s) for OB1 + wiki (cloud clients never hold raw keys)
- [ ] `.mcp.json`: add `openbrain` + `wiki` entries (verbatim non-overlapping descriptions); reload + approve
- [ ] OWUI direct wiring on `llm-net` where applicable
- [ ] Write `~/.claude/skills/memory-stack-routing/SKILL.md` verbatim (spec Task 7)
- [ ] Smoke 1 — mnemory: "Remember I prefer X" → shows in mnemory UI
- [ ] Smoke 2 — OB1 records: capture thought → retrieved via `openbrain`
- [ ] Smoke 3 — OB1 source: ingest URL → fact answered via `openbrain`
- [ ] Smoke 4 — wiki: synthesis question → answered from `wiki` w/ page refs
- [ ] Smoke 5 — routing: 3 back-to-back questions → correct lane 3/3
- **Exit:** all 5 smoke tests pass.

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
| 2026-05-16 | F8  | **Phase 4 migration = no-op:** mnemory holds **zero `⟦EV:research⟧` memories** (labelled query empty; broad search shows only normal user/project memories). The cache structure was never populated — nothing to migrate, nothing stranded. | Verified via transient llm-net curl to mnemory:8050 |
| 2026-05-16 | D13 | **mnemory_* valves removed entirely** from both `models.py` + monolith Valves (3 fields: url/api_key/user_id); `evidence_*` descriptions reworded to open-brain. Justified by F8 (no migration needed) + research output = structured data, not user-personal. Valves parity 29==29 verified. ⚠️ OWUI valve panel only reflects this **after re-paste** (it shows the deployed bundle). | User: research results are structured data, mnemory valves can be omitted |
| 2026-05-16 | D12 | **Deploy gate:** `openbrain_key` Tool valve defaults `""` → persistence/cache **skipped until set**. On OWUI re-paste, set `openbrain_key` = OB1 docker `.env` `MCP_ACCESS_KEY`. `openbrain_url` default `http://openbrain-mcp:8000` works (openwebui ↔ openbrain-mcp on ai-stack_llm-net). mnemory_* valves retained but unused (no migration needed per F8). | Graceful-skip design; user action required to activate |

## Open items / parking lot

- Confirm A1 (OB1 fork/branch/path), A2 (MCP client targets), A5 (smolcrawl reused for fetch) — validate during Phase 0/1.
- Research frontend (companion §4): deferred; revisit only after ~1 week of real use.
- Embedding dimension of `llama-cpp-embed` — fill in before any pgvector index (Phase 2).
