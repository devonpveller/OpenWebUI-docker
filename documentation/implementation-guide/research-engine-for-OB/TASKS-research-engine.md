# Tasks — Research Engine for Open Brain

Companion to [PLAN-research-engine.md](PLAN-research-engine.md) and the governing
[GROUNDING-MODEL.md](GROUNDING-MODEL.md).
Status: ⬜ todo · 🔧 in progress · ✅ done · 🧪 test · 🚀 operator (migration/deploy).

Build order is P0→P6; each phase is independently useful. Everything additive +
operator-applied for live schema (G2/G10), backup first.

---

## P0 — Lock the spec ✅ (this commit)
- ✅ **P0.1** [GROUNDING-MODEL.md](GROUNDING-MODEL.md) — claim atoms, typed
  grounding edges, confidence function, enforcement rules.
- ✅ **P0.2** [PLAN-research-engine.md](PLAN-research-engine.md) — architecture,
  service contract, reuse economics, phasing, open decisions.
- ✅ **P0.3** OD-1..6 resolved (2026-06-07) — see [PLAN §10](PLAN-research-engine.md):
  dedicated claims tables · two cooperating services · async job+poll · parse
  citations · strict-grounding+freshness reuse · adaptive+backstop (gap-honest).

## P1 — Claims layer in OB
- ⬜ **P1.1** Schema (additive): `claims` (id, text, thread_id?, confidence,
  volatility/fresh fields, created/updated) + `claim_sources` typed edges
  (`claim_id`, `source_id` or `parent_claim_id`, `edge_type` ∈
  states|inferred_from|corroborates|contradicts, weight). `IF NOT EXISTS`.
- ⬜ **P1.2** `confidence(claim)` function (DB or service) per GROUNDING-MODEL §5
  (strongest-edge, #corroborators, depth, authority, freshness).
- ⬜ **P1.3** Synthesis→claims parser: turn `[SOURCED]/[INFERRED]` + `[Source N]`
  citations into claims + typed edges (OD-4: parse-first).
- ⬜ **P1.4** Enforcement gate: reject/record-as-gap any claim with no edge
  terminating in a primary source; flag `contradicts`.
- 🚀 **P1.5** Operator: apply schema to live DB (rehearse→live, backup first).

## P2 — Curator grounded ingestion (extend the deployed curator)
- ⬜ **P2.1** On ingest, write claim→source grounding edges (P1.3) alongside the
  existing source/thread linking.
- ⬜ **P2.2** Cited-only linking enforced server-side (sources used = linked;
  unused/invalidated stay in session, unlinked) — GROUNDING-MODEL §6.3.
- ⬜ **P2.3** Grounded-only cache: `/research/lookup` + reuse serve a claim/
  synthesis only if grounded **and** fresh **and** ≥ confidence floor (§6.2).
- ⬜ **P2.4** Conflict handling: contradicting evidence → revision/retraction
  event (reuse `source-retract` machinery), never silent cache preference.
- 🧪 **P2.5** Verify the Oak-Ridge poisoning case is now impossible (fabricated,
  unsourced claim is rejected, not stored/served).

## P3 — Staging (`/research/stage`)
- ⬜ **P3.1** New OB endpoint: `{query, gap_terms?, session_id?}` → SearXNG search
  → shortlist → fetch+extract full content (SmolCrawl/`openbrain-extract`/
  `ingest_url`), parallel-bounded + per-source timeout + dedupe-by-url.
- ⬜ **P3.2** Stage candidates into a `sessions`/`session_sources` pool; return
  staged sources **with full text** + ids.
- ⬜ **P3.3** Freshness/dedup: a URL already a fresh source in OB is reused, not
  re-fetched.

## P4 — Research service (the harness, OB-side)
- ⬜ **P4.1** New service `openbrain-research` (Deno/Node; obnet+llm-net) — 3-place
  change (compose + recovery + stack-map).
- ⬜ **P4.2** Lift `smolcrawl/deep_research/` modules into the service.
- ⬜ **P4.3** Seam swaps: `search_web`→SearXNG gateway; sub-agent LLM→llama-cpp;
  OWUI-KB RAG→OB grounded-claim KB query.
- ⬜ **P4.4** `POST /research` with streamed progress; reuse loop = retrieve
  grounded claims → gap analysis vs KB → stage gaps only (P3) → synthesize
  verbatim → enforce grounding → delegate placement to the curator.
- ⬜ **P4.5** Reuse metric: emit `claims_reused / claims_freshly_gathered` +
  gap-ratio per run; persist for trend tracking.
- ⬜ **P4.6** Cost guardrail (OD-6): adaptive-by-coverage — free reuse pass →
  deep gather of remaining gaps → hard backstop (wall-time + max-fetch). On
  backstop-with-open-gaps, emit grounded claims + explicit `[GAP]`s + record the
  gaps (next-run targets); NEVER hallucinate-fill (GROUNDING-MODEL §6.7).
- 🧪 **P4.7** Quality test vs the in-tool stopgap (grounded richness, fewer gaps).

## P5 — Repoint OWUI as a thin client
- ⬜ **P5.1** `deep_research_tool.py` → thin client of `/research` (submit, stream,
  render); retire the in-tool harness + the stopgap fetch.
- 🚀 **P5.2** Operator re-paste the slimmed bundle into OWUI.

## P6 — Onboard other inlets
- ⬜ **P6.1** Autonomous-agent client of `/research` (when agent orchestration lands).
- ⬜ **P6.2** Open Notebook (or successor inlet) → call `/research`; retire its
  redundant research code.

---

## Cross-cutting
- 🧪 **T.1** Grounding enforcement: no ungrounded claim stored/served/reused (the
  governing invariant) — assert across P1/P2/P4.
- 🧪 **T.2** Reuse compounding: replay similar queries on a maturing thread; gap-
  ratio trends down.
- 🧪 **T.3** Freshness: stale claim re-validates (cheap) not re-gathers; conflict
  raises revision.
- 📝 **T.4** Keep [PLAN](PLAN-research-engine.md)/[GROUNDING-MODEL](GROUNDING-MODEL.md)
  updated as OD-1..6 resolve.

## Sequencing notes
- P1+P2 deliver the **grounded KB + safe cache** even before the service exists —
  immediate value (stops poisoning, structures claims).
- P3+P4 deliver the **shared harness + cheap reuse**.
- P5+P6 collapse the inlets to thin clients.
- The deployed **curator** is the anchor P2 extends; the **in-tool fetch + cited-
  only linking** already shipped is the P5-retired stopgap proving the thesis.
