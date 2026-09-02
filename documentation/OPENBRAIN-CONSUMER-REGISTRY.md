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
| `openbrain-mcp` | `:8000` on `obnet` + `llm-net` | `x-brain-key` (= `MCP_ACCESS_KEY`) | app-level plane forcing (`doorExposure`) + DB RLS | `app.all("*")` routes any path. The agents' door — **and a PRODUCER; see its row below and rule 9** |
| `openbrain-gateway` (cloud) | host `:8061`, `obnet` | Bearer; forces `share=cloud` filters | app-level + DB | The ONLY cloud door; agent-memory tools deny-listed |
| `openbrain-ops-gateway` | `127.0.0.1:8062`, `obnet` | `OPS_GATEWAY_KEY` | forces `exposure='ops'` | Same image as cloud gateway, ops profile (U5/U8) |
| Direct `deno_postgres` | in-container to `openbrain-db` | **connects as SUPERUSER `postgres`** | **only if the client `SET ROLE`s** | ~17 connections measured. H1's subject: superuser + no SET ROLE = RLS bypassed. See hardening rules |

## Producers (write into Open Brain)

| Service | Writes | Path | Plane stamping | Breaks if / broke when |
|---|---|---|---|---|
| `openbrain-gmail-pull` | `thoughts` (chunked email) | PostgREST | `exposure='ops'` since `e52e28d`; **before that: none → 42501 outage 08-30→09-01** | Any tightening of the thoughts write contract. **Its `/run` door defaults `--labels=SENT` (operator's personal mail) — `labelsPrefix:"brain/"` MUST be explicit on every ad-hoc call.** Caused the 09-01 personal-mail incident |
| `openbrain-mcp` (`capture_thought`, `capture_idea`, `update_idea`, agent-memory writeback, `promote_exposure`) | `thoughts`, `agent_memories`, `ideas` / `idea_revisions`, the `agent_memory_*` sidecars | direct `deno_postgres` to `openbrain-db` | column **and** mirror on every INSERT and on the review UPDATE, at the pinned OB1 `b604d55` (`index.ts` 884/982/1052, `agent-memory.ts` 267/292, `agent-memory-ops.ts` 165) | **It is ALSO the door two rows above, and until 2026-09-02 it was listed ONLY as a door.** That missing row is why the C.3 promotion of the non-superuser role was reverted at step 2 — there was no producer entry to check the write contract against. Its DEPLOYED image (`openbrain-mcp-server:local`, built 2026-08-30) writes NEITHER half; the pinned tree fixes it, so what is owed is a rebuild, not a code change |
| `openbrain-chunk-worker` | `source_chunks` (chunk text + embeddings) — **not `thoughts`**; corrected 2026-09-02 by reading `integrations/chunk-embedding-worker/index.ts:210`, its only INSERT | PostgREST/direct | none, and none is needed: `source_chunks` has no exposure column and no plane policy | 512-token embed limit (halve-retry); IKS fork :8817. Out of scope for the exposure contract today — in scope the day `sources`/`source_chunks` are governed (rule 6) |
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
9. **ANY DOOR THAT WRITES IS ALSO A PRODUCER, AND MUST BE LISTED AS BOTH.** The two tables
   above answer different questions — *how does traffic reach the database* and *what
   satisfies the write contract* — and a service that does both appears twice or the second
   question never gets asked about it. `openbrain-mcp` was a door row only, so when the C.3
   promotion moved the door off its `bypassrls` superuser on 2026-09-02, `capture_thought`
   failed the RLS `WITH CHECK` and the promotion was reverted at step 2. That is the same
   shape as the gmail outage: not an unknown service, but a known service whose WRITES nobody
   had written down. The same test applies to the two gateways (they proxy writes today,
   they do not compose them — if either ever builds a row, it gets a producer row) and to
   `dfu-done.ps1`, which is listed as a reader and writes probe rows.
10. **A producer's DEPLOYED ARTEFACT is what the contract binds, not its source.** Every
   corpus producer in the pinned tree stated the exposure column by 2026-09-02, and two
   DEPLOYED images still did not: `openbrain-mcp-server:local` and `openbrain-wiki:local`,
   both baked before the change. Bind-mounted producers (gmail-pull, entity-wiki, the import
   recipes) pick source changes up on their next run; baked ones do not, and the difference
   is invisible in a source read. Before tightening a write contract, check the running
   containers (`docker exec <c> grep -n 'INSERT INTO thoughts' -A3 …`), not just the tree.

## Producer sweep against the COLUMN-AUTHORITATIVE predicate (2026-09-02, item `dfux0s`)

Rule 1 in practice, for `OB1/docker/init-agent-memory-column-authority.sql` (the migration that
moves `thoughts_ops_plane` / `agent_memories_ops_plane` off `metadata->>'exposure'` and onto the
`exposure` column, and makes the column NOT NULL + CHECKed). **A producer stamping only the
mirror breaks the instant authority moves** — that is precisely how the gmail outage happened,
so every row was read rather than assumed. Verdicts are against the PINNED tree (`b604d55`) AND
against the RUNNING container, because those disagree (rule 10).

| Producer | What was read | Verdict against the new predicate |
|---|---|---|
| `openbrain-mcp` | pinned `index.ts` 884/982/1052, `agent-memory.ts` 267/292, `agent-memory-ops.ts` 165 — and `docker exec openbrain-mcp grep` on `/app/index.ts` + `/app/agent-memory.ts` | **Tree: SAFE** — column + mirror on all five INSERTs and on the review UPDATE (both `sets` entries share one `args` index, so the two cannot desync). **DEPLOYED IMAGE: BREAKS** — built 2026-08-30, `INSERT INTO thoughts (content, embedding, metadata)`, neither half. Rebuild required BEFORE the migration |
| `openbrain-wiki` (wiki-service note ingest) | pinned `docker/wiki-service/wiki-service.mjs` 469–482 vs `docker exec openbrain-wiki` on `/app/wiki-service.mjs` 463–473 | **Tree: SAFE** — POST states `exposure:"ops"` and the PATCH body carries the key. **DEPLOYED IMAGE: BREAKS** — POSTs neither half and its idempotent PATCH replaces `metadata` wholesale without the key. Rebuild required. (Currently unexercised: `notes ingested: 0 upserted` on every cycle for 72h, so it is latent, not firing) |
| `upsert_thought` (in-DB shared rpc door) | live `pg_proc.prosrc` on `openbrain-db` | **BREAKS** — its live body is `INSERT INTO public.thoughts (content, metadata)`, mirror only. Fixed by the migration itself (section 5), which is why the migration carries the door rather than only the policies. **Its UPDATE branch is also a LIVE containment hole today**: it merges a caller's `metadata.exposure` without touching the column, so under mirror authority re-upserting a personal row with `exposure:"ops"` republishes it. Reproduced on a throwaway: `col=personal mirror=ops`, `personal_row_now_visible_to_ops_plane=1` |
| `openbrain-gmail-pull` | pinned `recipes/email-history-import/pull-gmail.ts` 831–832; `docker inspect` mount `OB1/recipes/email-history-import → /app` | **SAFE** — writes `metadata:{…,exposure:"ops"}` AND `exposure:"ops"`, and it is BIND-MOUNTED from the host tree, so no rebuild is owed |
| `recipes/entity-wiki/generate-wiki.mjs` (the wiki compiler, `upsert_thought` caller + direct fallback) | pinned 1240 / 1307 / 1326–1336; `docker inspect openbrain-wiki` mount `OB1/recipes → /recipes` | **SAFE** — the dossier `metadata` object carries `exposure:"ops"` (so the idempotent PATCH cannot delete the mirror) and the direct-insert fallback states the column. Bind-mounted; no rebuild |
| `openbrain-chunk-worker` | `integrations/chunk-embedding-worker/index.ts` — its only INSERT | **NOT AFFECTED** — writes `source_chunks`, not `thoughts`. The registry row saying "thoughts" was wrong and is corrected above |
| `openbrain-entity-worker` | `integrations/entity-extraction-worker/index.ts:907` | **NOT AFFECTED as a corpus producer** — it SELECTs `thoughts` and writes only the derived graph tables, which carry no exposure column. It IS affected by the `queue_entity_extraction()` change: the gate now reads the column and the trigger fires on a column-only demotion, which can only REMOVE work from its queue |
| `openbrain-research` / `grounding-backfiller` | grep for `exposure` and for corpus writes across both integrations | **NOT AFFECTED** — no `thoughts` / `agent_memories` write; they write `claims` / `claim_sources`, still ungoverned |
| `agent-bridge`, harness/sessions, Claude clients | they reach the corpus THROUGH `openbrain-mcp` / the gateways; `openbrain-gateway/app.py` proxies `capture_thought` and composes no row | **INHERIT the openbrain-mcp verdict** — safe once its image is rebuilt |
| `openbrain-idea-refinery`, `openbrain-suggestion-worker`, `openbrain-curator`, `openbrain-ext` | grep for corpus INSERTs in the deployed `/app` of each | **NOT AFFECTED** — no `thoughts` / `agent_memories` write found in any of them |
| `openbrain-wiki` → `wiki_pages` | not read | **OUT OF SCOPE, deliberately.** `wiki_pages` is operator-parked; the migration does not touch it and it carries no exposure column |
| Import recipes (grok, instagram, blogger, chatgpt, google-activity, local-ollama) | grep for `upsert_thought` / direct inserts | **SAFE in the tree** — each states `exposure`. All are manual/ad-hoc, none runs as a deployed container, so there is no stale artefact to rebuild |

**Not cleared, and named as such:** `openbrain-postgrest` and both gateways were read only far
enough to establish that they proxy rather than compose corpus rows. If any of them gains a
write path it needs a producer row and its own line here.

## Standing open items (as of 2026-09-01)

- `PGRST_DB_ANON_ROLE=service_role` projects the whole public schema — own anchor, not fixed.
- `claims`/`claim_sources` and other non-memory tables still `USING(true)` — future governing
  must stamp producers FIRST (rule 1/2) or it recreates the gmail outage on the research lane.
- H1 (non-superuser app credentials) specified in PLAN §C.9, not implemented.
- `wiki_pages` writer broken since 08-28 (`extractLinks`) — pre-existing, needs its own fix.
- 1,129 personal-mail thoughts + 632 derived entities relabelled `exposure='personal'`
  (2026-09-01, lossless) — they have **no `user_id`**, so they are invisible to every role
  until the multi-user personal plane assigns one. Deliberate; revisit at multi-user.

---

## Superuser connections after H1 (2026-09-02) — and the two that stay

H1 moved every application client off the `postgres` superuser role onto
`ob_app_memory` (`rolsuper=false`, `rolbypassrls=false`, inheriting `service_role`).
**Census: 24 superuser connections → 5.** Nine services migrated one at a time, each
verified for reads *and* writes before the next was touched, zero errors across all nine.

| service | now connects as |
|---|---|
| `openbrain-mcp`, `-chunk-worker`, `-ext`, `-suggestion-worker`, `-curator`, `-research`, `-workbench`, `-grounding-backfiller`, `-idea-refinery` | `ob_app_memory` |

**The remainder is intentional. Each is named here with its reason, which is the point of
this section — an unexplained superuser connection is the thing H1 exists to remove:**

1. **`openbrain-postgrest`** — the authenticator. It switches role **per request**
   (`PGRST_DB_ANON_ROLE=service_role`) and is mechanically bound already. It is the pattern
   H1 points the other clients *at*, not a gap in H1.
2. **local `psql`** — migrations, `openbrain-db-backup`'s `pg_dump`, and `dfu-done.ps1`'s own
   boundary probes. Applying DDL needs a superuser, and **a probe bound by the policy it is
   testing cannot test that policy** — clause 3's door attack plants and removes personal
   fixtures precisely to prove the boundary holds against them.

**`ob_app_memory` proves the boundary, not merely the role:** connecting as it with **no
`SET ROLE` at all** gives `personal_visible=0`, `ops_visible=13012`. The 1,129 personal rows
are invisible to every application client; ops content still reads. A boundary, not a blackout.

### Rule 11 — check the code, not the row, before a contract change

This registry said `openbrain-chunk-worker` "writes `thoughts` (chunks + embeddings)". **It
does not** — it writes `public.sources` and `public.source_chunks`, neither of which carries
RLS. Trusting the row would have made chunk-worker look like the riskiest client in the set;
reading the code showed it was the safest, and it was migrated first for that reason. A
registry row is a starting point for a check, never its conclusion.
