# Plan — OB1 Research-Package Ingestion Inlet + LLM Thread Resolver

**Status:** 📝 DESIGN → BUILD (started 2026-06-07)
**Owner:** ai-stack / Open Brain (OB1)
**Branch:** `feature/integrated-knowledge-system` (no `main` merge)
**Relates to:** Quartz-4 expansion (`documentation/implementation-guide/expand-quartz-4/`),
Integrated Knowledge System (IKS).

---

## 1. Problem

Deep research fragments Open Brain into one notebook/thread per run.

**The data:** 38 `research_synthesis` sources ↔ 38 threads — a 1:1 fan-out. Two
queries about the same subject ("401k safe withdrawal" vs "401k withdrawal rate")
each become their own thread, each its own `content/notebooks/<slug>/` hub in the
Quartz-4 wiki. The linked-database concept dissolves into a pile of singletons.

**The mechanism (grounded in code):**

1. `smolcrawl/deep_research_tool.py:2602-2613` — after the research finishes, an
   LLM classifier (`_EV_CLASSIFY_SYS`) extracts a *fresh* 2–4 word `topic` slug
   from the answer. That slug becomes the `notebook` field in the persist payload.
   Nothing compares it against existing threads, so near-identical runs mint
   distinct strings.
2. `POST /research/persist` (`OB1/integrations/kubernetes-deployment/index.ts:1337`)
   — writes the synthesis + sources, creates a session, and links sources to a
   thread **only if `thread_id` is supplied**. deep_research rarely has an active
   thread, so the work lands in the unthreaded inbox / a per-notebook thread the
   wiki compiler later synthesizes from the distinct `notebook` strings.

The fix belongs at **assignment time**, owned by OB1 — not in the compiler (which
faithfully materializes whatever strings exist) and not bolted onto the research
model (which has just exhausted its context on the actual research).

---

## 2. Goals & non-goals

**Goals**
- A new OB1 service — the **research-package ingestion inlet** (`openbrain-curator`)
  — that is the single front door for deep-research output.
- For each incoming package, **resolve the best existing thread** (or deliberately
  create a new one) via a two-stage resolver: embedding shortlist → LLM decision.
- Bias toward **consolidation** (de-fragment) without over-merging distinct lines
  of inquiry.
- Maintain each thread's `description` + a new `threads.embedding` so matching
  *improves over time* (virtuous cycle: richer descriptions → better placement).
- Reuse the existing, battle-tested write path (`find_or_create_source`,
  `link_source_to_thread`, `/research/persist`) — add intelligence, don't
  reimplement persistence.
- Zero added UX burden on the end user; no manual thread-picking.

**Non-goals (this plan)**
- No change to how research is *generated*. The research model is untouched.
- No new browser UI. The inlet is a machine-to-machine HTTP service.
- No destructive thread operations. Consolidation archives, never deletes.
- No cloud exposure. The inlet is internal (`obnet`/`llm-net`), keyed.

---

## 3. Architecture

### 3.1 The seam

```
  deep_research (OWUI tool)                      NEW: openbrain-curator
  ─────────────────────────                      ──────────────────────
  research finishes                              POST /ingest/research-package
  builds a "package"  ───────────────────────▶   1. embed synthesis (or reuse)
  (synthesis + sources + topic_hint)             2. shortlist top-K threads (pgvector)
                                                 3. LLM decision: existing | new
                                                 4. ensure thread (create if new)
                                                 5. delegate write ──────────┐
                                                 6. refresh thread embed+desc │
                                                                              ▼
                                          EXISTING: POST /research/persist (openbrain-mcp)
                                          find_or_create_source · session · supersede · link
```

The curator owns the **housekeeping decision**; `/research/persist` owns the
**write**. This is SRP and keeps the dedup/supersede logic in exactly one place.

### 3.2 Why a separate service (vs. a route in openbrain-mcp)

The user's intent is explicit: *"a new service inlet to help manage housekeeping
for OB1 and offload the requirements from the external services."* A dedicated
container also gives the inlet its own resource envelope, restart policy, and a
clean place for future ingesters (capture, imports, other agents) to converge —
the inlet becomes the general "hand OB1 a package, let OB1 place it" boundary,
the thread-level analog of `find_or_create_source`. The cost is the standard
3-place change (compose + recovery + stack-map); accepted.

**Considered & deferred:** a route inside openbrain-mcp (lower footprint, but
couples curation to the MCP lifecycle and muddies SRP). Rejected for v1.

### 3.3 Delegate vs. own-persistence

**Decision: delegate (v1).** The curator resolves/creates the thread, injects the
resolved `thread_id` into the package, and calls the existing `/research/persist`
(internal, keyed with `MCP_ACCESS_KEY`). It then updates the thread's embedding +
description directly via pg (it already holds a pool for the shortlist query).

- **Pro:** reuses the correct, tested write (research_key supersede, sessions,
  C1 dedup-and-relink). No drift.
- **Con:** one internal hop + curator depends on openbrain-mcp being up.
  Mitigation: the existing direct path stays valid as a fallback (§3.6).

Own-persistence (curator reimplements the write) is recorded as the v2 option if
the hop ever proves fragile.

### 3.4 The resolver (two-stage)

A pure LLM-over-all-threads doesn't scale past a few dozen and wastes context.
Two stages:

**Stage 1 — embedding shortlist (cheap, scales).** Embed the synthesis claim
(bge-m3, 1024). `ORDER BY threads.embedding <=> $claim LIMIT K` (K≈5) over
`status='active'` threads with a non-null embedding. Returns candidate threads
with `name`, `description`, and cosine distance.

**Stage 2 — LLM decision (judgment, bounded).** Give the LLM *only* the K
candidates (name + description + distance) plus the new research (claim +
topic_hint + a few source titles). It returns structured JSON:

```jsonc
{
  "decision": "existing" | "new",
  "thread_id": "<uuid>",            // when existing
  "confidence": 0.0-1.0,
  "new_thread_name": "...",         // when new (broad, not query-specific)
  "new_thread_description": "...",  // when new (1-3 sentence scope statement)
  "reason": "..."                   // audit
}
```

**Resolution policy**
- **Explicit-thread bypass.** If the package carries a non-empty `thread_id`
  (operator/agent deliberately set it), skip the resolver entirely and honor it.
- **Conservative-merge bias.** The LLM is instructed to prefer an existing thread
  and must justify a *new* one as a "distinct line of inquiry." A
  `NEW_THREAD_MIN_CONFIDENCE` threshold gates auto-create; below it, attach to the
  top candidate.
- **Cold-start / empty shortlist.** No candidate threads (or none with an
  embedding) → create a new thread named from `new_thread_name` (falling back to
  the topic_hint).
- **Multi-thread.** `thread_sources` is already M:N. v1 ships a single **primary**
  thread for coherence; the schema + resolver output leave room to add `secondary`
  links later without migration.

### 3.5 Thread identity is the magnet

Matching quality depends on each thread having a real scope summary. So after a
successful ingest the curator **maintains the thread**:

- `threads.description` — extended/refined by the LLM to reflect the newly
  absorbed research (also surfaces in the wiki hub).
- `threads.embedding` — recomputed as `embed(name + "\n" + description)`.
  (Centroid-of-sources is the recorded v2 alternative; name+description is
  deterministic, cheap, and stays consistent with what the LLM matches against.)

This is a virtuous cycle: better descriptions → better embeddings → better future
placement.

### 3.6 Graceful degradation (safety)

- Curator unreachable / errors → deep_research falls back to the existing
  `POST /research/persist` (unthreaded inbox), exactly as today. Research output
  is **never** blocked by housekeeping. Best-effort, like the current persist.
- LLM resolver errors / times out → attach to the Stage-1 top candidate if one
  exists above a floor distance, else create a new thread from the topic_hint.
  Resolution never hard-fails the ingest.

---

## 4. Schema changes (additive only — G2-safe)

New migration `OB1/docker/init-thread-embedding.sql`:

```sql
ALTER TABLE public.threads ADD COLUMN IF NOT EXISTS embedding VECTOR(1024);
-- optional ANN index once row counts grow (cosine):
-- CREATE INDEX IF NOT EXISTS threads_embedding_idx
--   ON public.threads USING hnsw (embedding vector_cosine_ops);
```

`threads.description` already exists (init-threads.sql) — no change.
No existing column is altered or dropped. Follows the additive-migration
convention used across IKS (`IF NOT EXISTS`, idempotent, runs on the live volume
via the operator promotion runbook, since initdb scripts only fire on fresh
volumes — the G3 gap).

**Backfill:** a one-time pass embeds every existing thread
(`embed(name + description)`) so the shortlist works on day one. Operator-run, dry-runnable.

---

## 5. Service contract

**`openbrain-curator`** — Deno + Hono, modeled on the workbench/worker pattern.

- **Networks:** `obnet`, `llm-net` (no `app-net` — not browser-facing).
- **Port:** internal `8000`; host-published `127.0.0.1:<port>` for diagnostics only.
- **Auth:** `X-Brain-Key` / `x-brain-key` == `MCP_ACCESS_KEY` (same middleware
  shape as workbench `requireBrainKey`). Health is unauthenticated.
- **DB:** `deno-postgres` Pool (`DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD`).
- **Embeddings:** `EMBEDDING_API_BASE` (`http://llama-cpp-embed:8080/v1`), bge-m3,
  1024 — same `embed()` with adaptive shrink-on-overflow as the other services.
- **Chat:** `CHAT_API_BASE` (`http://llama-cpp:8080/v1`), `CHAT_MODEL`
  (`qwen36-27b:nothink`), `response_format: json_object`, low temperature.
- **Delegate target:** `PERSIST_URL` (`http://openbrain-mcp:8000`), keyed.

### Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | none | liveness (db + reachability) |
| POST | `/ingest/research-package` | X-Brain-Key | resolve thread → persist → maintain thread |

**`POST /ingest/research-package` body** (superset of the persist payload, so it is
a near drop-in for deep_research — only the URL changes):

```jsonc
{
  "research_key": "…",          // passthrough to persist (supersede key)
  "query": "…",
  "claim": "…",                  // the synthesis (required)
  "kind": "deep_research",
  "volatility": "medium",
  "revalidate_days": 30,
  "topic_hint": "…",            // was `notebook`; now only a hint to the resolver
  "thread_id": "",              // optional explicit override → bypass resolver
  "model": "…",
  "sources": [ { "url": "…", "title": "…", "content": "…", "domain": "…" } ]
}
```

**Response:**

```jsonc
{
  "thread_id": "<resolved uuid>",
  "thread_decision": "existing" | "new",
  "thread_confidence": 0.0-1.0,
  "thread_name": "…",
  "persist": { /* passthrough from /research/persist */ },
  "shortlist": [ { "thread_id": "…", "name": "…", "distance": 0.x } ]
}
```

---

## 6. Phases

| Phase | Title | Output | Risk | Run by |
|-------|-------|--------|------|--------|
| **P0** | Schema substrate | `init-thread-embedding.sql` + thread-embedding backfill | low (additive) | operator (rehearse→live) |
| **P1** | Service scaffold | `openbrain-curator` (Deno+Hono, auth, db, embed, /health) | low | dev build |
| **P2** | Thread resolver | two-stage shortlist + LLM decision + policy | med (LLM quality) | dev build |
| **P3** | Persist + maintenance | delegate to `/research/persist`; refresh embed+desc | med | dev build |
| **P4** | Repoint deep_research | `deep_research_tool.py` → curator inlet; topic→hint; fallback | med (deployed bundle) | author here; deploy = operator |
| **P5** | Retroactive consolidation | one-time resolver pass over the 38 threads; merge splinters | **high** | operator (rehearse→live) |
| **P6** | Observability/safety | decision logging, metrics, idempotency, graceful degradation | low | dev build |

P0–P3 + P6 are the buildable core. P4 touches the OWUI-deployed deep_research
bundle (re-paste to deploy — see `deep-research-tool-deployment` memory). P5 is an
operator-run data migration with its own rehearsal (mirror the Quartz-4
backup → rehearse-on-restored-copy → live discipline).

---

## 7. Retroactive consolidation (P5) — outline

The same resolver, run as a one-time pass, de-fragments the existing 38 threads:

1. For each existing thread, treat its synthesis as a "package" and resolve it
   against the *other* threads (excluding itself).
2. Where the resolver maps a splinter onto a canonical thread above threshold:
   re-link its sources (`link_source_to_thread`, additive) onto the canonical
   thread, then **archive** the splinter (`status='archived'`, never delete).
3. Reconcile the Quartz-4 wiki: archived threads' `content/notebooks/<slug>/`
   hubs must be retired/redirected by the wiki compiler — the pinned-slug
   behavior needs a deliberate path (recorded as a P5 sub-task; do not let the
   compiler silently orphan folders).
4. **Dry-run first**, idempotent, reversible (link history is additive; archive is
   reversible). Rehearse on a restored DB copy before live, per the established
   migration discipline.

P5 is intentionally last and gated — it is the only high-risk, irreversible-feeling
step, and the live system must be healthy on the new resolver before consolidating
history.

---

## 8. Open decisions

- **OD-A — Thread embedding basis.** `embed(name+description)` (v1, chosen) vs.
  centroid-of-source-embeddings (v2 candidate, better content fidelity, needs an
  aggregate/maintenance strategy).
- **OD-B — Multi-thread linking.** Single primary (v1) vs. primary + suggested
  secondaries surfaced for confirmation. Schema already supports M:N; defer the
  UX/policy.
- **OD-C — Consolidation aggressiveness (P5).** How high to set the merge
  threshold for the one-time pass; whether to require operator confirmation per
  proposed merge (recommended: dry-run report → operator approves the merge set).
- **OD-D — Synthesis re-embedding.** Reuse the embedding `/research/persist`
  already computes vs. compute once in the curator and pass it through (avoids a
  double embed). Lean toward computing in the curator and having persist accept a
  precomputed vector (small persist tweak) — recorded, not required for v1.

---

## 9. Testing

- **Unit (resolver):** synthetic candidate sets — exact-dup → existing; clearly
  novel → new; ambiguous near-dup → existing (conservative bias) above threshold.
- **Integration (dev OB1):** POST sample packages; assert `thread_sources` rows,
  session provenance, synthesis supersede, and thread embed/description refresh.
- **Fragmentation regression:** replay several near-identical research packages;
  assert they converge onto a single thread (the core success metric).
- **Degradation:** kill openbrain-mcp → curator falls back; kill chat LLM →
  resolver falls back to Stage-1 top candidate. Ingest never hard-fails.
- **P5 dry-run:** report proposed merges over the live 38 without writing.

---

## 10. Conventions honored

- **G1** — never commit/push on the user's behalf.
- **G2** — additive migrations only (`IF NOT EXISTS`, no alter/drop).
- **G10** — the agent does not run prod migrations/consolidations; P0 & P5 are
  authored here and **run by the operator** (rehearse → live).
- **3-place change** — adding `openbrain-curator` updates compose **+** recovery
  scripts (`emergency-recovery.ps1`/`.bat` inventory + sequences) **+** the
  stack-map reference doc, together (the `/stack-map` skill checks this drift).
- **No secrets in files** — keys via env (`MCP_ACCESS_KEY`, `POSTGRES_PASSWORD`).
