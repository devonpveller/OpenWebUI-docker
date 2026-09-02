# Open Brain consumer registry — who connects, how, and what breaks if the contract moves

> **Why this exists (2026-09-01):** U5's RLS boundary landed on `thoughts` with a fail-closed
> `WITH CHECK`, and `openbrain-gmail-pull` — a producer nobody had written down — died with
> 42501 on every INSERT while logging success. Newsletter ingestion was silently dead for two
> days; the daily digest and podcast did not deliver. The schema change was correct; the outage
> happened because **no registry existed to check producers against**. This file is that
> registry. **Any change to Open Brain's schema, RLS policies, doors, or auth MUST be checked
> against every row here first**, and any new consumer MUST add its row in the same change.

Verified empirically 2026-08-31 → 2026-09-01 (live probes, DB grants, container logs, source
reads) unless marked otherwise. Confirm a row before relying on it — services move.

## The doors (how anything reaches Open Brain)

| Door | Address | Auth | RLS-bound? | Notes |
|---|---|---|---|---|
| `openbrain-postgrest` | `:3000` on `obnet` | `PGRST_DB_ANON_ROLE=service_role` | **YES** — per-request role switch | No host binding; container-net only. Projects the whole `public` schema (open item: anon-role scope) |
| `openbrain-rest` | `http://openbrain-rest` | proxy in front of PostgREST | via PostgREST | The path most recipes use |
| `openbrain-mcp` | `:8000` on `obnet` + `llm-net` | `x-brain-key` (= `MCP_ACCESS_KEY`) | app-level plane forcing (`doorExposure`) + DB RLS | `app.all("*")` routes any path. The agents' door |
| `openbrain-gateway` (cloud) | host `:8061`, `obnet` | Bearer; forces `share=cloud` filters | app-level + DB | The ONLY cloud door; agent-memory tools deny-listed |
| `openbrain-ops-gateway` | `127.0.0.1:8062`, `obnet` | `OPS_GATEWAY_KEY` | forces `exposure='ops'` | Same image as cloud gateway, ops profile (U5/U8) |
| Direct `deno_postgres` | in-container to `openbrain-db` | **connects as SUPERUSER `postgres`** | **only if the client `SET ROLE`s** | ~17 connections measured. H1's subject: superuser + no SET ROLE = RLS bypassed. See hardening rules |

## Producers (write into Open Brain)

| Service | Writes | Path | Plane stamping | Breaks if / broke when |
|---|---|---|---|---|
| `openbrain-gmail-pull` | `thoughts` (chunked email) | PostgREST | `exposure='ops'` since `e52e28d`; **before that: none → 42501 outage 08-30→09-01** | Any tightening of the thoughts write contract. **Its `/run` door defaults `--labels=SENT` (operator's personal mail) — `labelsPrefix:"brain/"` MUST be explicit on every ad-hoc call.** Caused the 09-01 personal-mail incident |
| `openbrain-chunk-worker` | `thoughts` (chunks + embeddings) | PostgREST/direct | verify before schema changes | 512-token embed limit (halve-retry); IKS fork :8817 |
| `openbrain-entity-worker` | `entities`, `thought_entities`, `thought_edges`; consumes `entity_extraction_queue` | direct | inherits plane via parent-thought join (no own exposure column) | Was stopped as incident containment 09-01; queue backs up when down |
| `agent_memory_hash_text` trigger (in-DB) | `entity_extraction_queue.source_fingerprint` = **sha256 of content** | SECURITY DEFINER | queue rows readable → hash of personal content leaked until u5graph governed the table | Any new derived table repeating this pattern |
| `openbrain-research` / `grounding-backfiller` | `claims`, `claim_sources` | direct/PostgREST | ungoverned tables (RLS `USING(true)`) as of 09-01 | Will break like gmail if claims tables get fail-closed policies without stamping first |
| `agent-bridge` (audit mirror) | `agent_memories` via openbrain-mcp `capture_thought` lane | MCP, `AO_OPENBRAIN_KEY` | ops | `AO_OPENBRAIN_MIRROR_ENABLED`; key must be `MCP_ACCESS_KEY` value, not the gw- key (past bug) |
| Harness / sessions (clause-8 seam) | `agent_memories`, recall traces | ops gateway :8062 | ops, enforced by door | |
| Claude clients (`capture_thought`) | `thoughts` | openbrain-mcp / cloud gateway | cloud gateway forces `share=cloud` | |
| `openbrain-wiki` (wiki-service) | `wiki_pages` | direct | n/a | **BROKEN since 08-28: `ReferenceError: extractLinks` — pre-existing, unfixed** |
| `openbrain-idea-refinery` | `idea_revisions` | PostgREST | dormant — last successful write **2026-08-08** | Not a DFU casualty; was dead before |

## Readers (consume from Open Brain)

| Service | Reads | Path | Plane | Notes / failure mode |
|---|---|---|---|---|
| `openbrain-digest` (`send-digest.ts`) | recent `thoughts` window | `openbrain-rest` | ops (RLS-bound) | LLM: `qwen36-27b:nothink` via `llama-cpp:8080/v1`, **hard 30s client timeout, ~200 max_tokens per call**. The 09-01 personal-mail flood (950KB into its window) blew the 30s budget → no digest. Corpus size is its de-facto contract |
| `openbrain-podcast` (`link-enrich.ts`) | emails from `thoughts` via `AiNewsSection` (BrainClient → openbrain-rest) | openbrain-rest | ops | Filters `gmailLabels` startsWith `brain/`. **Zero brain/ mail in window = silent no-episode (exit 0)**. Chains: gmail-pull → podcast → digest |
| wiki compiler (`generate-wiki.mjs`) | `thoughts`, `thought_entities(content)` **raw selects** when invoked without `--semantic-expand` (the wiki-service path) | PostgREST | was ungoverned → U5 home #4; bound since thoughts RLS | Publishes into 48k `wiki_pages` |
| `openbrain-suggestion-worker`, `openbrain-curator` | thoughts/corpus | PostgREST/direct | ops | not deeply verified — confirm before contract changes |
| OWUI tools (`deep_research.py`, plugins) | via `openbrain-mcpo` / `-ext` → openbrain-mcp | MCP | ops | mcpo-ext has a CPU-spin failure mode |
| `open_notebook` | PostgREST (same net) | HTTP | RLS-bound | podcast audio renderer; SurrealDB is its own store |
| agent-org Tier-2 advisor | `openbrain-research` | HTTP | — | research lane on llm-queue |
| Harness recall (clause 8 seam) | `agent_memories` + recall traces | ops gateway | ops | |
| `dfu-done.ps1` / drills | many tables (probes) | psql direct as postgres | superuser — **bypasses RLS by design**; probe rows must clean up | writes `DFU-DONE-*` fixture traces |

## Hardening rules for further development (each one paid for by an incident here)

1. **Check this registry before any schema/policy/auth change** — the gmail outage was a
   correct change landing on an unrecorded producer. A change PR that touches Open Brain's
   contract cites the rows it checked.
2. **Every producer stamps its plane explicitly.** The DB enforces (`exposure` NOT NULL +
   CHECK, RLS `WITH CHECK`); the commit-time producer gate
   (`check-corpus-exposure-producers.ps1`) catches only the shapes it can see — the DB is the
   authority, the gate is advisory.
3. **No permissive defaults on doors.** `gmail-pull --labels` defaulting to `SENT` put the
   operator's private correspondence on the ops plane. A door's default must be its narrowest
   safe behaviour or a refusal; "the scheduled caller passes the safe flag" is not a guard.
4. **A producer must fail loudly.** gmail-pull caught its own 42501 and logged success for two
   days. Zero-row writes on a non-empty input are an error, never a success line (fixed
   `7d27e99`; apply the same standard to every producer above).
5. **New connections use a bound role, not superuser.** RLS binds `current_user`; a
   superuser connection sees everything unless it `SET ROLE`s (and `SET LOCAL` inside a
   transaction on pooled connections — a plain `SET` leaks across requests). H1's target state:
   dedicated non-superuser app credentials. Until then, every direct client must SET ROLE and
   is normatively — not mechanically — bound.
6. **Derived tables inherit the leak surface.** `entity_extraction_queue` exposed
   sha256(content) of personal thoughts; a content HASH is disclosure (existence + confirm-by-
   hashing). Any new derived/queue/log table carrying content or its digest gets the plane
   predicate from day one.
7. **Readers with client-side timeouts have an implicit corpus-size contract.** The digest's
   30s/200-token budget worked until the corpus ballooned 40×. When a producer can grow a
   reader's input unboundedly, one of them must bound it.
8. **Backfills are the same class-4 surface as deletes.** The personal-mail incident was a
   *backfill with the wrong targeting*, not a delete. Ad-hoc invocations of any producer's
   door use the scheduled caller's exact parameters as the baseline, changed deliberately.

## Standing open items (as of 2026-09-01)

- `PGRST_DB_ANON_ROLE=service_role` projects the whole public schema — own anchor, not fixed.
- `claims`/`claim_sources` and other non-memory tables still `USING(true)` — future governing
  must stamp producers FIRST (rule 1/2) or it recreates the gmail outage on the research lane.
- H1 (non-superuser app credentials) specified in PLAN §C.9, not implemented.
- `wiki_pages` writer broken since 08-28 (`extractLinks`) — pre-existing, needs its own fix.
- 1,129 personal-mail thoughts + 632 derived entities relabelled `exposure='personal'`
  (2026-09-01, lossless) — they have **no `user_id`**, so they are invisible to every role
  until the multi-user personal plane assigns one. Deliberate; revisit at multi-user.
