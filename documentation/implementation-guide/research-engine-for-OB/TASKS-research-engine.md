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

## P1 — Claims layer in OB  ✅ authored + tested (2026-06-07)
- ✅ **P1.1** Schema (additive): `OB1/docker/init-claims.sql` — `claims`
  (id, text, thread_id?, synthesis_id?, epistemic_tag, status, confidence,
  contradicted, volatility/revalidate_days/researched_on, content_hash,
  embedding) + `claim_sources` typed edges (`claim_id`, `source_id` **xor**
  `parent_claim_id`, `edge_type` ∈ states|inferred_from|corroborates|
  contradicts, weight). All `IF NOT EXISTS`; RLS + grants + dedup helper
  `find_or_create_claim` + `link_claim_to_source`/`link_claim_to_claim` +
  `retract_claim`. Verified: full ordered init chain applies clean on a fresh
  pgvector:pg16 volume.
- ✅ **P1.2** `claim_confidence(claim)` + `claim_min_depth` + auto-persist via
  `recompute_claim_confidence` trigger, per GROUNDING-MODEL §5 (strongest-edge
  0.90/0.60, +0.03/corroborator cap 0.10, depth ×0.85ⁿ, authority .gov/.edu/
  .mil ×1.0 else ×0.85, stale ×0.5, contradicts cap 0.30). Functional test
  confirmed the full ordering (fact 0.90 > corroborated 0.79 > inferred 0.51 >
  stale 0.45 > depth-1 0.43 > contradicted 0.30 > ungrounded 0.00).
- ✅ **P1.3** Synthesis→claims parser: `OB1/integrations/research-curator/
  claims.ts` (`parseSynthesisClaims` pure + `writeClaims` via SQL helpers) —
  `[SOURCED]`→states(+corroborates), `[INFERRED]`→inferred_from,
  `[UNCERTAIN]`→½-weight inferred_from, `[GAP]`→recorded gap (OD-4 parse-first).
  9 deno unit tests green.
- ✅ **P1.4** Enforcement gate: parser drops any claim whose citations resolve
  to zero real sources (never stored ungrounded); DB `ungrounded_claims` view =
  audit backstop, `reusable_claims` view = §6.2 cache read-path
  (grounded ∧ fresh ∧ ≥0.50). Verified the 1-800 fabrication → confidence 0,
  in `ungrounded_claims`, excluded from `reusable_claims`.
- 🚀 **P1.5** Operator: apply `init-claims.sql` to live DB (rehearse→live,
  backup first). Mounted in compose as `94-init-claims.sql` (fresh-volume); live
  DB needs the psql apply per the promotion runbook (G3/G10).
- 📝 **P1.6 (seam for P2)** `/research/persist` currently returns only
  `synthesis_id` + counts. P2 must also return the **ordered persisted source
  ids** (sourceIds[N-1] = `[Source N]`) so `writeClaims` can resolve citations
  into edges. Captured here so P2.1 wires it.

## P2 — Curator grounded ingestion (extend the deployed curator) ✅ authored + tested (2026-06-07)
- ✅ **P2.1** On ingest the curator (`index.ts` → `writeGroundedClaims`) parses
  `pkg.synthesis` and writes claim→source edges via `claims.ts`/`init-claims.sql`
  helpers, in its own best-effort transaction, alongside the existing
  source/thread linking. Index seam: `/research/persist` now returns
  index-aligned `source_ids` (`source_ids[N-1] == [Source N]`) — P1.6 done.
  Both services type-check clean.
- ✅ **P2.2** Cited-only is honored: the tool already sends ONLY cited sources
  (`last_sources = cited_sources`, [deep_research_tool.py:1849]); the grounding
  edges attach ONLY to cited sources (by construction, parse-from-citations); so
  grounded knowledge is cited-only regardless of caller. (Thread `link_type`
  still records what the run touched — provenance — but the *grounded* layer is
  strictly cited.)
- ✅ **P2.3** Grounded-only cache (server side): `/research/lookup` now returns
  `grounded` / `grounded_claims` / `total_claims` (from `reusable_claims`:
  grounded ∧ fresh ∧ ≥0.50), guarded to degrade to `null` pre-migration. The
  reuse path consuming this flag (refuse to re-serve a synthesis with no
  grounded claims) is wired tool-side in **P5**.
- 🟡 **P2.4** Conflict handling: the MECHANISM is in place — `contradicts` edges
  cap confidence at 0.30 + set `contradicted`, and `retract_claim()` records a
  retraction (never silent cache preference). AUTOMATIC contradiction detection
  (new evidence vs a stored claim) is deferred to the **P4** reuse loop, where
  staged evidence is compared against existing grounded claims.
- ✅ **P2.5** Verified: `claims.integration.ts` runs the real `writeClaims` path
  against the real schema — the fabricated unsourced `1-800` line is dropped by
  the parser gate, a `[Source 9]` phantom-citation line is dropped by the writer
  gate, neither is stored; the sourced `865` number is a reusable grounded
  claim; `ungrounded_claims` stays empty; re-run is idempotent. The poisoning
  case is now structurally impossible.

## P3 — Staging (`/research/stage`)
- ⬜ **P3.1** New OB endpoint: `{query, gap_terms?, session_id?}` → SearXNG search
  → shortlist → **per-source fetch+extract** (direct page fetch + HTML→text;
  NOT SmolCrawl — SmolCrawl is the separate whole-domain ingest mode below),
  parallel-bounded + per-source timeout + dedupe-by-url.
  - *Optional deep-domain mode (later):* when a gap is "ingest all of
    <docs-site>", a gather step may invoke **SmolCrawl** for exhaustive crawl —
    a distinct, heavier path, not the default per-source fetch.
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
