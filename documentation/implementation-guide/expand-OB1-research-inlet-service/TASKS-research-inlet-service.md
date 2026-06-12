# Tasks — OB1 Research-Package Ingestion Inlet + LLM Thread Resolver

Companion to [PLAN-research-inlet-service.md](PLAN-research-inlet-service.md).
Status keys: ⬜ todo · 🔧 in progress · ✅ done · 🧪 needs test · 🚀 deploy (operator).

---

## P0 — Schema substrate (additive)

- ⬜ **P0.1** `OB1/docker/init-thread-embedding.sql` — `ALTER TABLE threads ADD
  COLUMN IF NOT EXISTS embedding VECTOR(1024)`; commented optional HNSW cosine
  index. Idempotent. (G2)
- ⬜ **P0.2** Thread-embedding backfill — embed `name + description` for every
  existing thread; dry-run flag; idempotent (skip rows already embedded unless
  `--force`). Author as a script; operator runs it. (G10)
- 🚀 **P0.3** Operator: apply P0.1 + run P0.2 on the live volume (initdb scripts
  only run on fresh volumes — G3 gap; use the promotion-runbook pattern,
  rehearse on a restored copy first).

## P1 — Service scaffold (`openbrain-curator`)

- ⬜ **P1.1** `OB1/integrations/research-curator/deno.json` — imports: hono,
  `@db/postgres` (deno-postgres), supabase-js not needed (direct pg).
- ⬜ **P1.2** `OB1/integrations/research-curator/Dockerfile` — `denoland/deno`,
  cache, `--allow-net --allow-env`.
- ⬜ **P1.3** `index.ts` — Hono app, `Deno.serve`, `/health` (unauth, db ping),
  `requireBrainKey` middleware on `/ingest/*`, config from env, pg Pool, `embed()`
  with adaptive shrink, `chat()` JSON helper.
- ⬜ **P1.4** Stub `POST /ingest/research-package` returning the resolved shape
  (wired in P2/P3).

## P2 — Thread resolver

- ⬜ **P2.1** Stage 1: `shortlistThreads(embedding, k)` — pgvector
  `ORDER BY embedding <=> $1 LIMIT k`, active + non-null embedding; returns
  `{thread_id, name, description, distance}[]`.
- ⬜ **P2.2** Stage 2: `decideThread(pkg, shortlist)` — LLM JSON decision
  (existing|new + confidence + new name/description + reason).
- ⬜ **P2.3** Policy glue: explicit-`thread_id` bypass; `NEW_THREAD_MIN_CONFIDENCE`
  gate; empty-shortlist cold-start; conservative-merge prompt wording.
- ⬜ **P2.4** `ensureThread(...)` — create thread (name, description, embedding)
  when decision=new; return its id.

## P3 — Persist delegation + thread maintenance

- ⬜ **P3.1** `delegatePersist(pkg, thread_id)` — POST `/research/persist` with the
  resolved `thread_id` injected, keyed with `MCP_ACCESS_KEY`; return its JSON.
- ⬜ **P3.2** `refreshThread(thread_id)` — LLM-extend `description`; recompute
  `embedding = embed(name+description)`; `UPDATE threads`.
- ⬜ **P3.3** Wire the full `/ingest/research-package` flow:
  resolve → ensure → delegate → refresh; assemble response.
- ⬜ **P3.4** Graceful degradation: persist-down fallback signal to caller;
  resolver-down → Stage-1 top candidate or cold-start; never hard-fail.

## 3-place change (service registration)

- ⬜ **R.1** `OB1/docker/docker-compose.yml` — add `openbrain-curator`
  (build context, env: DB_*, EMBEDDING_*, CHAT_*, MCP_ACCESS_KEY, PERSIST_URL,
  PORT; networks obnet+llm-net; `127.0.0.1:<port>:8000`; depends_on db healthy +
  openbrain-mcp; restart unless-stopped).
- ⬜ **R.2** `scripts/emergency-recovery.ps1` + `.bat` — add to OB1 service
  inventory and the startup/shutdown sequences (after db/mcp on start; before db
  on stop).
- ⬜ **R.3** `.claude/skills/stack-map/references/workspace-stacks.md` — add the
  service row (network, port, dependency order). Run `/stack-map` to confirm no
  drift.

## P4 — Repoint deep_research (author here; deploy = operator)

- ⬜ **P4.1** `smolcrawl/deep_research_tool.py` — add a `curator_url` valve;
  `_persist_research_evidence()` POSTs the package to
  `<curator_url>/ingest/research-package` (topic → `topic_hint`); on failure or
  unset, fall back to the existing `/research/persist` (unchanged behavior).
- ⬜ **P4.2** Keep the explicit `active_thread_id` valve honored (becomes the
  bypass `thread_id`).
- 🚀 **P4.3** Operator: re-paste/redeploy the deep_research bundle in OWUI
  (`deep-research-tool-deployment` memory).

## P5 — Retroactive consolidation (author here; run = operator)

- ⬜ **P5.1** `consolidate-threads` script — for each of the 38 threads, resolve
  its synthesis against the *others*; produce a **dry-run report** of proposed
  merges (splinter → canonical, with confidence + reason). No writes.
- ⬜ **P5.2** Apply mode — re-link splinter sources onto canonical
  (`link_source_to_thread`), archive splinter (`status='archived'`); idempotent.
- ⬜ **P5.3** Wiki reconciliation — define how archived threads'
  `content/notebooks/<slug>/` hubs are retired/redirected by the compiler (do not
  orphan folders); coordinate with the Quartz-4 compiler.
- 🚀 **P5.4** Operator: rehearse on a restored copy → review dry-run → approve
  merge set → apply live → recompile wiki. (G10)

## P6 — Observability / safety

- ⬜ **P6.1** Structured decision log per ingest (decision, confidence, shortlist
  distances, chosen thread) for tuning the threshold.
- ⬜ **P6.2** `/health` reports db + persist + embed/chat reachability.
- ⬜ **P6.3** Idempotency: re-POST of the same `research_key` is safe (persist
  already supersedes; ensure no duplicate thread created on retry).

## Testing

- 🧪 **T.1** Resolver unit cases (dup→existing, novel→new, near-dup→existing).
- 🧪 **T.2** Integration on dev OB1: assert thread_sources + session + supersede +
  thread refresh.
- 🧪 **T.3** Fragmentation regression: near-identical packages converge to one
  thread.
- 🧪 **T.4** Degradation: mcp-down fallback; chat-down resolver fallback.
- 🧪 **T.5** P5 dry-run report over the live 38.

---

## Sequencing

Build **P1 → P2 → P3 → P6** on dev (no prod impact). **P0** in parallel (SQL is
authored now; operator applies before the service ships). **P4** authored now,
deployed by operator after the service is live and tested. **P5** last, gated,
operator-run with rehearsal.
