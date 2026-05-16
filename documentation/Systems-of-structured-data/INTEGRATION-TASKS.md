# Personal Memory Stack — Task Tracker (LIVING DOCUMENT)

> **Last updated:** 2026-05-16
> **Plan:** [INTEGRATION-PLAN.md](INTEGRATION-PLAN.md) — rationale, architecture, risks. Update both together.
> **Legend:** `[ ]` todo · `[~]` in progress · `[x]` done · `[!]` blocked · `[-]` descoped
> **Rule:** every status change → also bump *Last updated* and add a Decision Log row if a choice changed.

## Current state

- **Active phase:** Phase 0/1 not yet started — awaiting greenlight.
- **Hard stop ahead:** Phase 1 validation gate. No OB1/wiki implementation until the user picks Path A/B/C.
- **Standing directive:** Open Notebook + surrealdb **stay running** until proven appropriately replaced (2026-05-16). Phase 7 is gated on accepted proof.

---

## Phase 0 — Prerequisites & discovery

- [ ] Verify Docker + Compose, ~10GB free disk for Postgres/pgvector + wiki dir
- [ ] Confirm local stack replaces `OPENAI_API_KEY`: `qwen36-27b`, `qwen36-27b:nothink`, `llama-cpp-embed` reachable
- [ ] Inventory `open_notebook` + `surrealdb` compose blocks, volumes, host ports (scope future retirement; do **not** stop them)
- [ ] Trace how `evidence_memory.py` reaches mnemory today (gateway vs direct `mnemory_url` valve) — informs Phase 4
- [ ] Produce gap list; stop if anything blocks
- **Exit:** gap list produced.

## Phase 1 — Clone OB1 + VALIDATION GATE *(HARD STOP)*

- [ ] Clone `github.com/devonpveller/OB1` @ `develop` → `d:\Open WebUI\ai-stack\OB1\`
- [ ] Read `docs/01-getting-started.md`, `docs/04-ai-assisted-setup.md`
- [ ] Locate + read the existing wiki recipe in `recipes/` end to end
- [ ] Record: file count, last-commit date, model assumed, dependencies, tables/columns added
- [ ] Score the 14-requirement table (companion §1.2)
- [ ] Estimate effort for Path A / B / C
- [ ] **Report to user; STOP for path decision**
- **Exit:** scored table + recommended path delivered; user has chosen A/B/C.

## Phase 2 — Self-host Supabase + OB1 base *(spec Task 3)*

- [ ] Add Supabase container set to `docker-compose.yml`, private network, minimal host ports
- [ ] OB1 base schema applied
- [ ] OB1 edge functions deployed
- [ ] OB1 MCP server up
- [ ] Rewire OB1 embeddings → `llama-cpp-embed`; parameterize vector dim (replace spec `vector(1536)`)
- [ ] Skip Slack capture (spec default)
- [ ] Smoke: capture a thought via MCP, read back via semantic search
- **Exit:** thought round-trips through OB1.

## Phase 3 — OpenBrain `sources` ingest *(spec Task 4)*

- [ ] Check `recipes/` + `extensions/` for an existing sources/ingest extension before building
- [ ] Add `sources` table (schema per spec Task 4, local embed dim)
- [ ] `openbrain_ingest_url(url, notebook?, tags?)` — fetch via smolcrawl, extract, embed, write row, return id
- [ ] `openbrain_ingest_urls(urls[], notebook?)` — parallel batch
- [ ] YouTube transcript extractor (modular)
- [ ] PDF text extractor (modular)
- [ ] Smoke: ingest a URL; agent answers a fact from it via `openbrain`
- **Exit:** URL ingested and queryable via `openbrain`.

## Phase 4 — Migrate research off mnemory *(resolves the misuse)*

- [ ] Repoint `deep_research/evidence_memory.py` persistence → OpenBrain `sources`
- [ ] Repoint cache lookup → OpenBrain (drop mnemory `EV:research` search)
- [ ] Synthesis surfaced via wiki, not mnemory
- [ ] One-time migrate existing `⟦EV:research⟧` mnemory memories → OpenBrain `sources`
- [ ] Patch modular `smolcrawl/deep_research/` **and** `deep_research_tool.py` monolith (keep in sync; do NOT touch stale `data/openwebui/deep_research_function.py`)
- [ ] Flag user: re-paste `deep_research_tool.py` into OWUI → Tools
- [ ] Verify: new research run writes OpenBrain, not mnemory; mnemory gains no new `EV:research`
- **Exit:** research decoupled from mnemory; cache served from OpenBrain.

## Phase 5 — Wiki compiler *(spec Task 5 / companion §§2–3)*

- [ ] Implement per chosen Path (A: run as-is / B: extend / C: build per §3) — *expand into subtasks once path is chosen*
- [ ] All extraction/relation/classify passes → `qwen36-27b:nothink`
- [ ] All synthesis/topic/contradiction passes → `qwen36-27b` (think)
- [ ] Output → git-backed dir; commit per successful compile
- [ ] Scheduled (daily) + on-demand `wiki_trigger_recompile`
- [ ] Read-only web viewer service added to compose (Quartz/Perlite/Next)
- [ ] Expose `wiki_search` / `wiki_read_page` / `wiki_get_backlinks` / `wiki_get_related` / `wiki_list_pages` / `wiki_trigger_recompile`
- [ ] Compile-failure alerting (companion §5.2 R: stale wiki)
- [ ] Smoke: topic-synthesis question answered from `wiki` with compiled-page refs; wiki rebuildable from OpenBrain alone
- **Exit:** wiki compiles, serves, and is queryable; fully regenerable.

## Phase 6 — Wire MCP + routing skill + smoke tests *(spec Tasks 6–8)*

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

## Phase 7 — Retire Open Notebook *(GATED ON PROOF — user-executed)*

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

| Date | # | Decision / change | Rationale |
|------|---|-------------------|-----------|
| 2026-05-16 | D1 | Supabase self-hosted in ai-stack | Local-first/privacy posture; keep personal records + sources on-box |
| 2026-05-16 | D2 | Fix research↔mnemory misuse by migrating *when OB1 lands*, not now | Zero research-cache downtime; misuse knowingly live through Phase 4 |
| 2026-05-16 | D3 | Local llama-cpp only: `qwen36-27b` (think) for synthesis, `qwen36-27b:nothink` for high-volume extraction, `llama-cpp-embed` for vectors | Consistency with privacy-first stack; spec's OpenAI default overridden |
| 2026-05-16 | D4 | Wiki output git-backed + read-only web viewer | Spec §5.1 most-flexible option; mobile/cross-device |
| 2026-05-16 | — | Open Notebook + surrealdb stay UP until proven replaced; Phase 7 gated on accepted proof | User directive — no teardown on faith |

## Open items / parking lot

- Confirm A1 (OB1 fork/branch/path), A2 (MCP client targets), A5 (smolcrawl reused for fetch) — validate during Phase 0/1.
- Research frontend (companion §4): deferred; revisit only after ~1 week of real use.
- Embedding dimension of `llama-cpp-embed` — fill in before any pgvector index (Phase 2).
