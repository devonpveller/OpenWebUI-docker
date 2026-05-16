# Personal Memory Stack — Integration Plan (LIVING DOCUMENT)

> **Status:** Phase 1 not yet started — awaiting greenlight to begin Phase 0/1.
> **Last updated:** 2026-05-16
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

1. **mnemory** — agent-facing semantic memory. Short extracted facts *about the
   user* only. Already deployed (`mnemory` container, gateway-fronted).
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

| # | Decision | Choice | Deviation from spec |
|---|----------|--------|---------------------|
| D1 | Supabase deployment | **Self-hosted** in ai-stack `docker-compose.yml`, private network, mnemory-style isolation (minimal host ports) | Spec is provider-agnostic; we commit to self-host for the local-first/privacy posture |
| D2 | Research↔mnemory misuse fix | **Migrate when OB1 lands** — keep the mnemory evidence cache live until OpenBrain `sources` ingest exists, then repoint research persistence/cache to OpenBrain (+ wiki for synthesis) and strip `⟦EV:research⟧` from mnemory | Deviation is knowingly live through Phase 4 by design (no capability gap) |
| D3 | LLM + embeddings provider | **Local llama-cpp.** Synthesis/extraction model = `qwen36-27b` (think) and `qwen36-27b:nothink` (no-think), routed by need through the architecture. Embeddings = `llama-cpp-embed` | Spec defaults to OpenAI (`OPENAI_API_KEY`); we override everywhere |
| D4 | Wiki output hosting | **Git-backed directory + read-only web viewer** (Quartz/Perlite/Next), commit per successful compile | Spec §5.1 "most flexible" option |

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
  integration demonstrates *proven appropriate replacement* of Open Notebook's
  capabilities (user directive 2026-05-16). Only then is retirement proposed, as
  a **confirmed, user-executed** destructive step (prior OB1-teardown pattern),
  never silent. Replacement proof is a Phase 7 gate, not an assumption.
- **A4** Research frontend (companion §4) is **deferred, not built** — per the
  doc's own guidance: run OB1 + wiki + Obsidian for a week before deciding.
- **A5** URL/PDF/YouTube fetching reuses the existing **smolcrawl** crawl stack
  rather than adding Firecrawl/Jina as new dependencies.

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

### Phase 1 — Clone OB1 + VALIDATION GATE *(hard stop — companion Section 1)*
Clone the fork @ `develop`. Read `docs/01-getting-started.md`,
`docs/04-ai-assisted-setup.md`, inspect `recipes/` for the existing wiki recipe.
Score it against the **14-requirement table** (companion §1.2). Record last-commit
date, model assumed, dependencies, tables added.
**Exit / deliverable:** scored 14-row table + recipe summary + recommended Path
**A** (use as-is, 11+ Y), **B** (extend, 7–10 Y), or **C** (build per Section 3)
with per-path effort estimate. **STOP. User picks the path. No implementation
before that.**

### Phase 2 — Self-host Supabase + OB1 base *(spec Task 3)*
Add Supabase container set to `docker-compose.yml` on a private network. Run OB1
base schema, edge functions, MCP server. Slack capture skipped (spec default).
Rewire OB1 embeddings → `llama-cpp-embed` (parameterize vector dim, D3).
**Exit:** capture a thought via MCP, read it back via semantic search.

### Phase 3 — OpenBrain `sources` ingest *(spec Task 4)*
Add `sources` table (check `recipes/`/`extensions/` for an existing one first).
Build `openbrain_ingest_url` + `openbrain_ingest_urls` — fetch (reuse smolcrawl,
A5), extract, embed (local), write row. Modular YouTube-transcript + PDF
extractors.
**Exit:** ingest a URL; agent answers a factual question from that source via
`openbrain`.

### Phase 4 — Migrate research off mnemory *(resolves the D2 misuse)*
Repoint `smolcrawl/deep_research/evidence_memory.py`: persistence →
OpenBrain `sources`; cache lookup → OpenBrain; synthesis surfaced via wiki.
Remove `⟦EV:research⟧` writes/cache from mnemory; one-time migrate existing
`EV:research` memories into `sources`. Patch **both** the modular
`smolcrawl/deep_research/` source **and** the `deep_research_tool.py` monolith;
flag for OWUI re-paste (no build script — hand-mirrored; see
`deep-research-tool-deployment` memory).
**Exit:** research run persists to OpenBrain, not mnemory; mnemory holds no new
`EV:research`; cache hit served from OpenBrain.

### Phase 5 — Wiki compiler *(spec Task 5 / companion §§2–3)*
Implement per the Phase 1 path. All extraction/synthesis on local Qwen per D3
routing. Output → git-backed dir, commit per successful compile. Scheduled
(daily) + on-demand `wiki_trigger_recompile`. Add read-only web viewer service to
compose. Expose the six `wiki_*` MCP tools (companion §"MCP tools").
**Exit:** topic-synthesis question answered from `wiki` referencing compiled
pages; wiki rebuildable from OpenBrain alone.

### Phase 6 — Wire MCP + routing skill + smoke tests *(spec Tasks 6–8)*
Three gated MCP entries (mnemory / openbrain / wiki) with the verbatim
non-overlapping descriptions. Write
`~/.claude/skills/memory-stack-routing/SKILL.md` verbatim from spec Task 7. Run
all 5 spec Task 8 end-to-end smoke tests.
**Exit:** all 5 smoke tests pass; routing picks the correct lane 3/3.

### Phase 7 — Retire Open Notebook *(gated on proof — confirmed, user-executed)*
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
  companion §5.2's *cost* concern becomes a *latency* concern on local Qwen.
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
