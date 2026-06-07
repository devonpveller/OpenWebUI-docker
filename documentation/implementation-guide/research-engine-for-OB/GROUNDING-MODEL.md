# Grounding Model — the governing epistemic spec

**Status:** 📐 DESIGN (governing spec)
**Owner:** ai-stack / Open Brain
**This is the rubric.** Every part of the research engine (the service, the
staging layer, the curator ingestion, the cache, the reuse loop) MUST satisfy
these rules. If a feature would let an ungrounded claim be stored, served, or
reused, it is wrong by definition.

---

## 1. Why this exists

Open Brain is a **self-expanding knowledge base**: research syntheses become
sources that future research builds on. Without discipline, that is a drift
amplifier — model output becomes the next query's "evidence," and confident
nonsense compounds (the telephone game / hallucination recursion). We already
saw the failure mode live: a chat-model embellishment invented a phone number,
and the query cache would have re-served it as fact.

The defense is a single principle:

> **Every claim is anchored to the source(s) that make it. A claim with no
> terminating path to a primary source is not knowledge — it is a guess, and is
> labelled, gated, and never silently trusted or reused.**

This is also the precondition for the system's economics (see
[PLAN-research-engine.md](PLAN-research-engine.md) §"compounding reuse"): you can
only safely **reuse** what is grounded, confidence-scored, and fresh. Grounding
and reuse are the same coin — discipline now is what makes cheap, trustworthy
research later.

---

## 2. The atoms

| Atom | What it is | Trust |
|------|-----------|-------|
| **Source** | external/primary material — a fetched + extracted web page, PDF, paper, transcript. The ground truth. | terminal |
| **Claim** | a single assertion ("Oak Ridge recycling uses a brown cart"). Must carry ≥1 grounding edge. | derived |
| **Synthesis** | an ordered set of claims answering a query. Stored verbatim; decomposes into claims. | derived |
| **Thread** | a durable line of inquiry that accumulates claims + sources over time (the curator resolves these). | container |

The KB's first-class atom is the **grounded claim**, not the synthesis blob. A
synthesis is the human-readable rendering; the claims + their grounding edges are
the machine-truth.

---

## 3. Grounding edges (claim → source)

Every claim links to sources by a **typed** edge. The type encodes *how* the
source grounds the claim — this is the basis-vs-support distinction:

| Edge | Meaning | Example notation |
|------|---------|------------------|
| `states` | the source **directly asserts** the claim (independent, primary support) | `claim ← source` |
| `inferred_from` | the claim was **derived/synthesized from** the source (basis, not a direct assertion) | `source(basis) → claim` |
| `corroborates` | a source that **independently confirms** a claim already made (reinforcement) | `claim ← source (support)` |
| `contradicts` | a source that **conflicts** with the claim (must surface, never hidden) | `claim ⊣ source` |

The strongest claim is **both derived and directly stated/corroborated**:

```
Source ×2 (basis) ── inferred_from ──▶ claim ◀── states/corroborates ── source
```

A claim grounded **only** by `inferred_from` is an *educated claim* (weaker). A
claim grounded by `states` from a primary source is a *fact* (enforceable). A
claim with neither is **not admitted** (it's a `[GAP]`).

### Claim-on-claim (transitive grounding)
A claim may be grounded on another **claim** (e.g. a synthesis reused as input).
This is allowed ONLY if the parent claim's grounding chain **terminates in a
primary source**. The edge records the parent claim; confidence inherits a depth
penalty (§5). A chain that does not terminate in a source is rejected.

---

## 4. Mapping to what already exists

The deep_research synthesis already tags claims in prose — this is the embryonic
form we promote to structured edges:

| Today (synthesis text) | Becomes (structured) |
|------------------------|----------------------|
| `[SOURCED] [Source 1]` | claim + `states` edge → source 1 |
| `[INFERRED] [Source 1, 2]` | claim + `inferred_from` edges → sources 1,2 |
| `[UNCERTAIN]` | claim with low confidence; admitted only above the floor (§6) |
| `[GAP]` | **not a claim** — a recorded gap, triggers gathering |

So the ingestion step **parses the synthesis's own citations** into claim→source
edges. The harness already produces the raw material; we structure + enforce it.

Substrate already present to build on: `sources`, `threads`, `thread_sources`,
`sessions`/`session_sources`, `find_or_create_source`, `link_source_to_thread`,
the graph layer (`entities`/`edges` from the entity-extraction worker), and the
volatility columns on `sources` (`volatility`, `revalidate_days`, `researched_on`).

---

## 5. Confidence is a computed property (not a vibe)

Confidence of a claim is a function of its grounding, recomputed whenever its
edges change:

```
confidence = f(
   strongest_edge_type,        # states/corroborates > inferred_from
   n_corroborating_sources,    # independent confirmations raise it
   depth_from_ground,          # claim-on-claim distance lowers it
   source_authority,           # primary/official > blog > content-farm
   freshness                   # within revalidate window vs stale
)
```

Properties this must have:
- A claim **degrades gracefully** into an "educated guess" the further it is from
  a source (depth penalty), and **upgrades** when a later source corroborates it
  ("until proven deeper").
- A **stale** claim (past `researched_on + revalidate_days`) drops below the
  reuse floor until re-validated.
- A `contradicts` edge **caps** confidence and flags the claim for review.

---

## 6. The enforcement rules (non-negotiable)

These are the invariants every component must uphold:

1. **No ungrounded claim is admitted.** Ingestion rejects (or records as a gap)
   any claim lacking a grounding edge that terminates in a primary source.
2. **Only grounded claims are cacheable/reusable.** The query cache and the
   claim-reuse layer serve a claim only if it's grounded **and** fresh **and**
   above the confidence floor. (Fixes the poisoning we observed.)
3. **Sources stored = sources used.** Only sources the synthesis actually cited
   are persisted/linked to the thread; gathered-but-unused and invalidated
   sources are not linked (they may stay in the staging session as provenance).
4. **Synthesis stored verbatim.** The synthesis is stored exactly as the tool
   produced it — never re-synthesized, never truncated. (The chat model's
   embellishment is NOT the artifact; the tool's grounded synthesis is.)
5. **Conflict surfaces.** New evidence that contradicts a stored claim raises a
   revision/retraction event; the system never silently prefers the cached claim.
6. **Provenance is total.** Every stored claim can be walked back to its
   primary source(s); every synthesis records which claims (and thus sources)
   compose it.
7. **Budget exhaustion degrades to gaps, never fabrication.** When a gather
   budget / backstop is hit with gaps still open, the synthesis returns only the
   grounded claims it has and marks the remainder as explicit `[GAP]`s (recorded
   for a future run). A run is allowed to be *incomplete*; it is never allowed to
   be *falsely complete*. This is the safety property that makes hard cost caps
   acceptable — a premature stop can only ever produce honest "we don't know
   yet," because rule #1 forbids the alternative.

---

## 7. Worked example (the Oak Ridge case)

- Source S1 (oakridgetn.gov) **states** "curbside recycling uses a brown cart"
  → claim C1 `states`←S1. **Fact** (high confidence, official source).
- Sources S1+S13 → claim C2 "glass goes to the Convenience Center, not curbside"
  `inferred_from`←{S1,S13}. **Educated claim** (medium; no single source states
  it outright).
- Chat-model output "call 1-800-438-8657" → **rejected**: no source asserts it
  (the sourced number is 865-482-3656). It never becomes a claim. ← this is the
  rule that would have stopped the poisoning.
- Later research finds the official PDF stating the accepted-items list →
  new claims `states`←PDF, and C2's confidence **upgrades** via corroboration.

---

## 8. What this enables downstream

- **Trustworthy reuse** → the compounding economics (cheap research over time).
- **Auditable answers** → any OB response can show its grounding chain.
- **Safe self-expansion** → the KB grows layers of grounded claims without drift.

This spec is the contract. Build the [research service](PLAN-research-engine.md)
and the curator ingestion to enforce it; do not add a path that bypasses it.
