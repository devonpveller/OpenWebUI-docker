# Plan — Research Engine for Open Brain

**Status:** 📐 DESIGN → (build pending)
**Owner:** ai-stack / Open Brain (OB1)
**Branch:** `feature/integrated-knowledge-system` (no `main` merge)
**Companion specs:** [GROUNDING-MODEL.md](GROUNDING-MODEL.md) (governing rubric) ·
[TASKS-research-engine.md](TASKS-research-engine.md)
**Builds on:** the deployed **curator** inlet
(`documentation/implementation-guide/expand-OB1-research-inlet-service/`) and the
deep_research tool (`smolcrawl/deep_research_tool.py` + `smolcrawl/deep_research/`).

---

## 1. Vision

Research is a **first-class shared service of Open Brain**, not a feature of any
one app. One harness, one enforcement model, many thin inlets. It produces
**grounded claims** (per [GROUNDING-MODEL.md](GROUNDING-MODEL.md)) that accumulate
into a knowledge base whose **marginal research cost falls over time**.

Three pillars:
1. **Grounded-claim epistemics** — every claim anchored to its source(s); nothing
   ungrounded is stored, served, or reused. (The rubric.)
2. **Shared research service** — the harness (discover → stage → loop →
   synthesize → ground → curate) lives centrally, OB-side; inlets are thin.
3. **Compounding reuse** — the expensive part (gather + validate) is paid once
   per claim and amortized; similar/adjacent queries reuse grounded claims and
   only spend compute on the gaps. Research gets cheaper as the KB matures.

---

## 2. Problem (what's wrong today)

- The harness lives **inside the OWUI tool** (`deep_research_tool.py`), so it
  can't be reused by future autonomous agents or the Open Notebook container
  (which re-implements a weaker, redundant version).
- Source content comes from **OWUI's web search snippets (~150 chars)** — too
  thin to ground a real synthesis, so the chat model embellishes (and fabricates,
  e.g. a wrong phone number).
- The **query cache can re-serve ungrounded content as truth** (poisoning).
- Sources are linked **whether or not the synthesis used them**; the synthesis
  was at risk of being stored as a short claim / corrupted by remediation
  (both since fixed in the tool, but the architecture still needs to enforce it).

None of this is fixable by patching the OWUI tool alone — the harness needs to
move to a shared, enforced service.

---

## 3. Architecture

```
INLETS (thin)        OWUI deep_research tool · autonomous agents (future) · Open Notebook
   │  POST /research {query, scope, options}; stream progress; receive grounded synthesis
   ▼
RESEARCH SERVICE     THE HARNESS (OB-side, shared):
(the agent harness)    1. plan: decompose query → needed claims/sub-questions
                       2. reuse: pull grounded+fresh claims from the KB (cheap)
                       3. gap analysis: what's missing/stale/low-confidence?
                       4. stage: for gaps only — search (SearXNG) → fetch+extract
                          full content (SmolCrawl/extract) → stage in a session
                       5. relevance/validation loop until grounded
                       6. synthesize verbatim, with claim-level citations
                       7. ENFORCE grounding (GROUNDING-MODEL §6) → hand to curator
   │
   ▼
CURATOR + OB         thread resolution (deployed curator) · write claim→source
(knowledge substrate)  grounding edges · grounded-claim KB · grounded-only cache
```

### 3.1 Why OB-side
The service produces grounded claims **into** OB and needs exactly what already
lives on the OB side: the SearXNG private-search gateway, SmolCrawl, openbrain-
extract, local LLM (llama-cpp), and the OB substrate. It runs as a new service in
the OB1 compose project, **sibling to the curator**.

### 3.2 Inlets are thin
Each inlet only: submits a request, streams progress, renders the returned
grounded synthesis. No harness logic, no enforcement bypass.
- **OWUI tool** → thin client (the heavy bundle mostly disappears; the weak-fetch
  problem goes with it).
- **Autonomous agents** (future) → same API.
- **Open Notebook** → same API, retiring its redundant research code (consistent
  with the ON→Quartz-workbench direction — whatever inlet survives consumes the
  service rather than re-implementing it).

---

## 4. The harness — lift, don't rewrite

The deep_research harness is **already modularized** under
`smolcrawl/deep_research/` (anchor, sub_agent, rag_research, synthesis, journal,
research, knowledge_research, context_budget, domain_discovery, crawl_integration,
evidence_memory, models). The OWUI bundle is just those stitched together. The
migration **lifts these modules into the service** and swaps three OWUI-coupled
seams for stack-native ones:

| OWUI-coupled today | Becomes (in the service) |
|--------------------|--------------------------|
| `search_web()` (OWUI) | the **SearXNG private gateway** directly |
| `generate_chat_completion` sub-agent | **llama-cpp** directly (every OB service already does this) |
| RAG over **OWUI KB collections** | query the **grounded-claim KB in OB** (grounded + confidence-scored, not opaque chunks) |

Everything we already hardened in the tool this session carries over: full
synthesis stored verbatim, `<think>` stripped, remediation `nothink` + guarded,
evidence-less runs skipped, full-content fetch (now server-side with real
tooling), cited-only source linking.

---

## 5. Service contract (draft)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/research` | run a full research effort; stream progress (SSE/chunked); return the grounded synthesis + claim/source refs. Key-gated. |
| POST | `/research/stage` | (internal/sub-step) discover + full-fetch + stage candidate sources for a query/gap-terms into a session; return staged sources with full text. |
| GET | `/research/lookup` | existing — grounded-claim/synthesis recall by key (extended to the claim layer + freshness). |
| GET | `/health` | liveness. |

`POST /research` body (superset of today's package): `{query, scope?(thread_id),
options{depth, freshness, confidence_floor}, origin(owui|agent|notebook)}`.
Response: streamed status + final `{synthesis, claims[], cited_sources[],
thread_id, reuse_ratio}`.

Staging uses the existing `sessions`/`session_sources` tables as the **candidate
pool** before promotion. Promotion (cited + grounded only) reuses
`find_or_create_source` + `link_source_to_thread` + the curator's thread
resolution.

---

## 6. Compounding reuse (the economics)

The expensive stage (gather + fetch + validate) is **gated to the gaps**:

```
retrieve grounded claims from KB (cheap, semantic + thread-scoped)
  → coverage/gap analysis: what is missing / stale / below confidence floor?
  → gather + fetch + validate ONLY the gaps (expensive, bounded)
  → synthesize from reused + newly-grounded claims
  → persist new claims; upgrade confidence on any corroborated; flag conflicts
```

Rules (all from [GROUNDING-MODEL.md](GROUNDING-MODEL.md)):
- **Freshness gates reuse** — volatility/`revalidate_days` already exist; stale
  claims trigger a (cheap) re-validation, not a full re-gather.
- **Corroboration is cheap reinforcement** — a new confirming source upgrades
  confidence instead of re-deriving.
- **Conflict is a signal** — contradicting evidence raises a revision/retraction,
  never a silent cache preference.
- **Confidence floor for reuse** — below the bar, treat as a gap and re-research
  (so weak early claims don't ossify).
- **Thread clustering makes reuse efficient** — the curator's de-fragmentation
  co-locates reusable claims by line of inquiry; fragmented threads defeat reuse.

**Expectations:** cold start pays full price; savings compound **per domain** as
layers accumulate. **Metric:** log `claims_reused vs claims_freshly_gathered` and
gap-ratio per run; on a maturing thread the ratio should trend toward reuse —
the observable proof the engine works. Instrument from day one.

---

## 7. Relationship to the curator (already built/deployed)

The **curator** (`openbrain-curator`, deployed) already does the back half:
thread resolution (embedding shortlist + LLM), verbatim synthesis storage,
synthesis↔thread linking, thread-name stamping. The research engine:
- **extends the curator's ingestion** to write **claim→source grounding edges**
  and enforce "no ungrounded claim" (GROUNDING-MODEL §6),
- **adds the front half** (the harness: discover/stage/loop) that the curator
  currently receives from the external tool.

Open question (OD-2): is the research engine **one service** (harness + curate)
or the **harness service + the curator service** cooperating? Leaning: keep the
curator as the placement/ingestion authority; the research service calls it. (SRP,
reuse what's deployed.)

---

## 8. Existing building blocks (reused, not rebuilt)

| Need | Existing piece |
|------|----------------|
| Source discovery | SearXNG-over-Tor private gateway (search stack) |
| Per-source fetch + extraction | direct page fetch (HTTP) + HTML→text extract (the tool's stopgap path) — single pages, not whole sites |
| **Deep whole-domain ingest** (optional, distinct mode) | **SmolCrawl** (`smolcrawl-pipelines`) — for exhaustively crawling an entire documentation site / framework, NOT ordinary per-source gather. Invoked only when a gap is "ingest all of <domain>". |
| Upload/file extraction (PDF/Office/audio) | `openbrain-extract` (multipart upload → markdown; not a URL fetcher) |
| Candidate staging | `sessions` / `session_sources` |
| Dedup + linking | `find_or_create_source`, `link_source_to_thread` |
| Thread placement + storage | the deployed **curator** + `/research/persist` |
| Graph layer | entity-extraction worker (`entities`/`edges`) — extend for claims |
| Freshness | `volatility` / `revalidate_days` / `researched_on` on `sources` |
| Local LLM / embeddings | `llama-cpp` (chat) / `llama-cpp-embed` (bge-m3, 1024) |
| Harness logic | `smolcrawl/deep_research/` modules (lift) |

This is largely **orchestration + a claims layer**, not new infrastructure.

---

## 9. Phasing (each phase independently useful)

See [TASKS-research-engine.md](TASKS-research-engine.md) for detail.

- **P0 — Grounding model locked** (the spec; this doc + GROUNDING-MODEL.md).
- **P1 — Claims layer in OB** (schema: `claims`, `claim_sources` typed edges,
  confidence; ingestion that parses synthesis citations → edges; enforcement).
- **P2 — Curator grounded ingestion** (extend curator to write claims/edges,
  cited-only linking, grounded-only cache).
- **P3 — Staging** (`/research/stage`: SearXNG → SmolCrawl/extract → session).
- **P4 — Research service** (lift harness modules; swap the 3 seams; `/research`
  with streaming; reuse loop + gap-against-KB; reuse metric).
- **P5 — Repoint OWUI** as a thin client of `/research`.
- **P6 — Onboard agents / Open Notebook** to the same API.

Stopgap already shipped: in-OWUI full-content fetch + cited-only linking in the
tool — it proves the thesis (snippet → full content) while P1–P4 are built, then
is retired in P5.

---

## 10. Decisions (resolved 2026-06-07)

- **OD-1 ✅ Dedicated claims tables.** New `claims` + `claim_sources` (typed-edge)
  tables — claims have distinct semantics (grounding-typed edges + computed
  confidence + freshness) that don't belong on the entity graph.
- **OD-2 ✅ Two cooperating services.** The research-service (harness) calls the
  already-deployed **curator** for placement/ingestion (SRP; reuse what's live).
- **OD-3 ✅ Async job + poll, optional live stream.** `POST /research` returns a
  `job_id`; inlets poll for status/result; an optional SSE progress stream serves
  live inlets (OWUI). Survives long runs / disconnects; works for headless agents.
- **OD-4 ✅ Parse existing citations.** Turn the synthesis's `[SOURCED]/[INFERRED]
  [Source N]` tags into claims + typed edges (cheap, deterministic). LLM-assisted
  extraction is a later enhancement, not v1.
- **OD-5 ✅ Strict grounding + freshness ("strict + stale").**
  - *Reuse as-is* (no gather): `states`/corroborated claims **and** within the
    freshness window.
  - *Re-validate* (cheap re-confirm, not full re-gather): `inferred_from`-only
    claims, **or** anything past its freshness window.
  - *Re-research* (full gather): ungrounded / below-floor / contradicted.
  - Freshness windows unchanged: fast 7d / medium 180d / slow 1095d.
- **OD-6 ✅ Adaptive-by-coverage + hard backstop that can't hallucinate.**
  1. Free pass: exhaust reuse of grounded OB claims (zero gather cost).
  2. Deep gather: for the *remaining* gaps, gather as deep as the gap needs
     (depth scales to the gap, not a blanket cap).
  3. Backstop: a hard ceiling (wall-time + max-fetch) stops runaway.
  4. **Guard:** if the backstop hits with gaps open, the synthesis does NOT fill
     them from model knowledge — it returns the grounded claims it has, marks the
     rest as explicit `[GAP]`s, and **records** them (→ next run's gather targets).
     The "no ungrounded claim" rule makes a premature stop degrade to honest gaps,
     never fabrication.

---

## 11. Conventions

- **G1** — never commit/push on the user's behalf.
- **G2** — additive migrations only (`IF NOT EXISTS`, no alter/drop).
- **G10** — agent does not run prod migrations; schema changes authored here,
  applied by the operator (rehearse → live), with a backup first.
- **3-place change** — a new service updates compose **+** recovery scripts **+**
  the stack-map reference doc together (`/stack-map` checks drift).
- **No secrets in files** — keys via env (`MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`).
- **Governing spec** — [GROUNDING-MODEL.md](GROUNDING-MODEL.md) overrides
  convenience: no path may store/serve/reuse an ungrounded claim.
