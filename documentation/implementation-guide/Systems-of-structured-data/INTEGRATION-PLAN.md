# Personal Memory Stack — Integration Plan (LIVING DOCUMENT)

> **Status:** Phases 0–5 COMPLETE and live-verified — see
> [PHASE6-WIRING-HANDOFF.md](PHASE6-WIRING-HANDOFF.md) for the wiring record.
> (Header corrected 2026-08-20; it had claimed "Phase 1 not yet started"
> since 2026-05-16 while the build shipped around it.)
> **Last updated:** 2026-08-20
> **Companion tracker:** [INTEGRATION-TASKS.md](INTEGRATION-TASKS.md) — granular checklist + decision log. Update both together.
> **Source specs:** [personal-memory-stack-setup.md](personal-memory-stack-setup.md) (requirements) · [wiki-compiler-and-research-frontend.md](wiki-compiler-and-research-frontend.md) (task order + validation gate)

This document is the authoritative, evolving plan for integrating the three-layer
memory stack (mnemory + OpenBrain/OB1 + compiled wiki) into the ai-stack. It is
expected to change as the Phase 1 validation gate and later discovery resolve
unknowns. When a decision changes, edit the Decision Log in the tracker and the
affected phase here — do not let the docs drift from reality (the same anti-drift
rule the wiki architecture itself enforces).

---

## 1. Target architecture (per v2 spec)

Three layers, each owning one role; the agent routes unambiguously:

1. **mnemory** — agent-facing semantic memory. Short extracted facts _about the
   user_ only. Already deployed (`mnemory` container, gateway-fronted).
2. **OpenBrain (OB1)** — single source of truth for everything that is not a
   user-fact: structured records (contacts, calendar, thoughts, projects) **and**
   external source documents (papers, articles, transcripts, web pages) in a
   `sources` table. To be self-hosted in this stack with self-hosted Supabase.
3. **Compiled wiki** — read-only synthesis artifact, regenerated on a schedule
   from OpenBrain. Never authoritative; fix the OpenBrain row and recompile.

Source-of-truth rules and routing rules are taken verbatim from
[personal-memory-stack-setup.md](personal-memory-stack-setup.md) §"Source-of-truth
rules" and §"Routing rules" and are not restated here to avoid a second copy that
can drift.

---

## 2. Decisions locked this session (2026-05-16)

| #   | Decision                                  | Choice                                                                                                                                                                                                                        | Deviation from spec                                                                                                                                                                        |
| --- | ----------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D1  | ~~Supabase deployment~~ → **No Supabase** | **SUPERSEDED by Phase 1 finding F1.** `OB1/docker/` ships a raw **Postgres+pgvector** self-host stack (`openbrain-db`) already wired to ai-stack — no Supabase anywhere. We deploy that.                                      | Spec is Supabase-centric; the deployable fork deliberately uses direct Postgres + OpenAI-compatible local API                                                                              |
| D5  | Wiki-compiler path                        | **Path B — extend** the existing recipe (2026-05-16)                                                                                                                                                                          | Companion §1.3; Y8/P2/N4 score                                                                                                                                                             |
| D6  | OB1 extension tools                       | **Run full stack, wire selectively** (revised 2026-05-16): bring up `openbrain-db`+`openbrain-mcp`+`openbrain-ext`+both mcpo bridges; each client/OWUI model opts into core and/or ext explicitly                             | Ext tool-context cost is **opt-in per client** (separate ext mcpo bridge), not global bloat; life-app tools are legitimately "domain records → OpenBrain" per the v2 source-of-truth table |
| D2  | Research↔mnemory misuse fix               | **Migrate when OB1 lands** — keep the mnemory evidence cache live until OpenBrain `sources` ingest exists, then repoint research persistence/cache to OpenBrain (+ wiki for synthesis) and strip `⟦EV:research⟧` from mnemory | Deviation is knowingly live through Phase 4 by design (no capability gap)                                                                                                                  |
| D3  | LLM + embeddings provider                 | **Local llama-cpp.** Synthesis/extraction model = `qwen36-27b` (think) and `qwen36-27b:nothink` (no-think), routed by need through the architecture. Embeddings = `llama-cpp-embed`                                           | Spec defaults to OpenAI (`OPENAI_API_KEY`); we override everywhere                                                                                                                         |
| D4  | Wiki output hosting                       | **Git-backed directory + read-only web viewer** (Quartz/Perlite/Next), commit per successful compile                                                                                                                          | Spec §5.1 "most flexible" option                                                                                                                                                           |

### Model routing convention (D3 detail)

- **`qwen36-27b`** (think / reasoning-budget on) — page synthesis, topic/cluster
  synthesis, contradiction analysis, anything where reasoning quality matters.
- **`qwen36-27b:nothink`** (llama-swap nothink profile, `:nothink` suffix) —
  high-volume, low-precision passes: entity extraction, relation extraction,
  classification. Matches the existing deep_research `nothink_suffix=":nothink"`
  routing convention so the whole stack stays consistent.
- **`llama-cpp-embed`** — all pgvector embeddings (OB1 `sources`, wiki pages).
- Spec SQL hardcodes `vector(1536)` (OpenAI dim). **Schema must be parameterized
  to the local embed model's actual dimension** — flagged as a build-time fix.

---

## 3. Assumptions (correct in the Decision Log if wrong)

- **A1** OB1 source = `github.com/devonpveller/OB1` @ `develop`, cloned to
  `d:\Open WebUI\ai-stack\OB1\`, integrated into the ai-stack compose (not a
  separate `~/projects/OB1` stack as the spec literally says).
- **A2** MCP wiring = Claude Code via a **gateway-pattern door** (mirror
  `mnemory-gateway`; cloud clients never hold raw OB1/wiki keys) + OWUI direct on
  `llm-net`. Claude Desktop only if requested.
- **A3** `open_notebook` + `surrealdb` services **stay running** until this
  integration demonstrates _proven appropriate replacement_ of Open Notebook's
  capabilities (user directive 2026-05-16). Only then is retirement proposed, as
  a **confirmed, user-executed** destructive step (prior OB1-teardown pattern),
  never silent. Replacement proof is a Phase 7 gate, not an assumption.
- **A4** Research frontend (companion §4) is **deferred, not built** — per the
  doc's own guidance: run OB1 + wiki + Obsidian for a week before deciding.
- **A5 — REVERSED (2026-05-16, D8).** smolcrawl is **not** the sources ingest
  backend. smolcrawl's purpose is whole-domain knowledge collections for OWUI
  (deferred — 100s-of-URLs bloat risk into open-brain). The **sources producer
  is `deep_research_tool.py`**: the URLs/content it already fetches + synthesizes
  during a research run are what populate open-brain `sources`. The generic spec
  `openbrain_ingest_url` MCP tool is **deferred**, not the primary path. Phase 3
  (`sources` schema) and Phase 4 (repoint deep_research → sources) are one
  coupled piece.

---

## 4. Phased plan

Each phase has an explicit exit condition. Phase 1 is a **hard stop**.

### Phase 0 — Prerequisites & discovery

Non-destructive. Adapted spec Task 1: Docker ✓, local llama-cpp replaces
`OPENAI_API_KEY`, ~10GB disk for Postgres/pgvector + wiki dir. Inventory the
`open_notebook`/`surrealdb` compose blocks + volumes (scope retirement). Confirm
exactly how `evidence_memory.py` reaches mnemory today (gateway vs direct valve)
so the Phase 4 migration is surgical.
**Exit:** gap list produced; stop if anything blocks.

### Phase 1 — Clone OB1 + VALIDATION GATE _(hard stop — companion Section 1)_

Clone the fork @ `develop`. Read `docs/01-getting-started.md`,
`docs/04-ai-assisted-setup.md`, inspect `recipes/` for the existing wiki recipe.
Score it against the **14-requirement table** (companion §1.2). Record last-commit
date, model assumed, dependencies, tables added.
**Exit / deliverable:** scored 14-row table + recipe summary + recommended Path
**A** (use as-is, 11+ Y), **B** (extend, 7–10 Y), or **C** (build per Section 3)
with per-path effort estimate. **STOP. User picks the path. No implementation
before that.**

### Phase 2 — Stand up OB1/docker core _(reshaped by F1 — much smaller)_

**No Supabase.** Use `OB1/docker/` as-is for the core: `openbrain-db`
(pgvector pg16, `vector(1024)` bge-m3), `openbrain-mcp` (core 4 tools, :8808).
**Skip** `openbrain-ext` + the two `mcpo` bridges' ext path for now (D6 —
core+sources only). Generate `.env` secrets (gitignored), join
`ai-stack_llm-net`, confirm embeddings→`llama-cpp-embed`, chat→`qwen36-27b:nothink`.
Decide integration shape: keep `OB1/docker/` as its own compose (prior pattern)
vs. merge services into ai-stack `docker-compose.yml`.
**Open verify:** does `docker/init*.sql` include the entity-extraction /
typed-reasoning-edges / graph schemas (needed by Path B Phase 5), or only core +
the 6 life-app ext tables?
**Exit:** capture a thought via MCP (:8808), read it back via semantic search,
all on local models.

### Phase 3 — OpenBrain `sources` ingest _(spec Task 4)_

Add `sources` table (check `recipes/`/`extensions/` for an existing one first).
Build `openbrain_ingest_url` + `openbrain_ingest_urls` — fetch (reuse smolcrawl,
A5), extract, embed (local), write row. Modular YouTube-transcript + PDF
extractors.
**Exit:** ingest a URL; agent answers a factual question from that source via
`openbrain`.

### Phase 4 — Migrate research off mnemory _(resolves the D2 misuse)_

Repoint `smolcrawl/deep_research/evidence_memory.py`: persistence →
OpenBrain `sources`; cache lookup → OpenBrain; synthesis surfaced via wiki.
Remove `⟦EV:research⟧` writes/cache from mnemory; one-time migrate existing
`EV:research` memories into `sources`. Patch **both** the modular
`smolcrawl/deep_research/` source **and** the `deep_research_tool.py` monolith;
flag for OWUI re-paste (no build script — hand-mirrored; see
`deep-research-tool-deployment` memory).
**Exit:** research run persists to OpenBrain, not mnemory; mnemory holds no new
`EV:research`; cache hit served from OpenBrain.

### Phase 5 — Wiki compiler _(Path B — extend; spec Task 5 / companion §2)_

Extend the existing `wiki-compiler` recipe stack. Concrete Path B work:

1. Port wiki scripts' data access **PostgREST → docker Postgres**; replace the
   Edge-Function entity-extraction-worker trigger with a local runner.
2. Add the v2 `sources` table read path (depends on Phase 3) — reqs 2.
3. Emit `[[wikilinks]]` + export `graph.json` (reqs 6, 7).
4. Add six `wiki_*` MCP tools into the `openbrain-ext` Deno server (req 13).
5. Repoint LLM local: entity/topic via `LLM_BASE_URL`→llama-cpp + patch the
   1536→1024 embedding preflight; **port `typed-edge-classifier` off the
   hardcoded Anthropic API** to an OpenAI-compatible local call (D3).
6. First-class `notebook` scoping (req 10).
   All synthesis on `qwen36-27b`, extraction/classify on `qwen36-27b:nothink`.
   Output → git-backed dir, commit per compile; read-only web viewer (D4).
   **Exit:** topic-synthesis question answered from `wiki` referencing compiled
   pages; wiki rebuildable from OpenBrain alone.

### Phase 6 — Wire MCP + routing skill + smoke tests _(spec Tasks 6–8)_

Three gated MCP entries (mnemory / openbrain / wiki) with the verbatim
non-overlapping descriptions. Write
`~/.claude/skills/memory-stack-routing/SKILL.md` verbatim from spec Task 7. Run
all 5 spec Task 8 end-to-end smoke tests.
**Exit:** all 5 smoke tests pass; routing picks the correct lane 3/3.

### Phase 7 — Retire Open Notebook _(gated on proof — confirmed, user-executed)_

**Open Notebook stays UP until proven replaced** (user directive 2026-05-16).
Entry gate — demonstrate the new stack appropriately covers Open Notebook's three
real values (companion §4.1): (a) notebook-scoped source/note/chat view, (b)
ingest UX, (c) project-scoped Q&A. Capture the proof in the tracker. Only after
the user accepts the proof: prepare exact teardown commands for `open_notebook` +
`surrealdb` services/volumes; **user executes** the irreversible deletions.
**Exit:** replacement proof accepted **and** services + volumes removed; stack
converged on the three-layer model.

---

## 5. Risks & watch-items

- **R1** Local-model throughput on high-volume entity/relation extraction —
  companion §5.2's _cost_ concern becomes a _latency_ concern on local Qwen.
  Mitigation: `:nothink` routing for extraction; incremental compile only.
- **R2** Embedding-dim mismatch with spec SQL (`vector(1536)`) — must
  parameterize to `llama-cpp-embed`'s real dim before any pgvector index.
- **R3** D2 migrate-later window — the misuse is knowingly live through Phase 4.
  Acceptable trade for zero research-cache downtime; revisit if it slips.
- **R4** Self-hosted Supabase is the largest infra addition this stack has taken
  (~6–8 containers). Isolate like mnemory; minimal host ports.
- **R5** Wiki link entropy / entity proliferation (companion §5.2) — registry +
  weekly broken-link lint + dedup pass required if Path B/C.
- **R6** Three-copy drift in deep_research (modular vs monolith vs stale
  `data/openwebui/deep_research_function.py`) — patch the two live copies, never
  the stale one; flag for re-paste.

---

## 6. What stays out of scope (explicit)

- Research frontend / Open Notebook UI clone (A4 — deferred).
- Wiki editing of any kind (architecturally forbidden — fix OpenBrain + recompile).
- Cloud LLM/embeddings (D3 — local only).
- Slack capture (spec default skip).
- Podcast/audio generation (companion §4.5).
