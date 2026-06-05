# Implementation Plan — Integrated Knowledge System

**Companion to:** [Integrated-knowledge-system-concept.md](Integrated-knowledge-system-concept.md)
**Audience:** an autonomous Claude agent implementing inside the `ai-stack` workspace.
**Status:** Ready to execute · **Created:** 2026-06-01 · **Owner repo:** `d:\Open WebUI\ai-stack`

> This is a *living* execution spec. The agent updates the **Progress Ledger** (§9)
> after every task and records deviations in the **Decision Log** (§10). Read this
> file end-to-end before starting; re-read §1 (decisions) and §2 (guardrails) at the
> top of every work session.

---

## 0. Operator decisions (locked)

These four choices were made by the operator and override any contrary reading of the concept doc:

| # | Decision | Consequence for this plan |
|---|----------|---------------------------|
| **D1** | **Full repoint of Open Notebook now.** OB1 Postgres becomes Open Notebook's only *source* store; SurrealDB is retained **only** for UI state, job queues, chat history, caching (concept §7.2). | Phase 4 is the spine. It is the largest, riskiest workstream and gates Phases 6–7. |
| **D2** | **Triage UI lives inside Open Notebook.** | Phase 6 (triage) depends on Phase 4 landing. No standalone triage container is built. |
| **D3** | **Build + validate in isolation. Never touch prod data or prod containers.** | All validation runs in a throwaway `*-dev` sandbox against a **scratch DB** seeded with synthetic data. The agent produces a promotion runbook (Phase 8); the operator promotes. |
| **D4** | **Edit OB1/ and the sibling `open-notebook` fork, but never commit or stage.** | The agent may modify files under `OB1/`, `d:\Open WebUI\open-notebook`, and `ai-stack`. It must **not** `git add`, `git commit`, or `git push` in any repo. The operator reviews and commits per-repo. |

---

## 1. Reality map — concept doc vs. what is actually here

The concept was written against upstream OB1's vocabulary. The self-hosted stack differs. **Honor reality, not the doc's wording.**

| Concept says | Actual state in this workspace | Plan impact |
|--------------|-------------------------------|-------------|
| "Supabase" (PostgreSQL, RLS, edge functions) | **Plain `pgvector/pgvector:pg16`** as `openbrain-db`; a Deno MCP server (`OB1/integrations/kubernetes-deployment/index.ts`); PostgREST + a Caddy `/rest/v1/*` proxy. No Supabase, no RLS/JWT. | Read **"Supabase" ⇒ "OB1 Postgres"** everywhere. The "Supabase client libraries" framing in concept §7.3 does **not** apply — Open Notebook talks to OB1 over **direct `pg`** or **PostgREST**, not a Supabase SDK. |
| `sources` table needs creating | **Already exists** — `OB1/docker/init-sources.sql`. `id UUID PK`, `url`, `title`, `content`, `content_type` (CHECK enum), `tags[]`, `notebook`, `domain`, research-linkage columns, `embedding vector(1024)`, `metadata jsonb`. **No `content_hash` column.** | Phase 1 *extends* `sources` (adds `content_hash`), it does not create it. FKs in new tables reference `sources(id)` as `UUID`. |
| `thoughts`, entities, knowledge graph, wiki compiler | **All exist and run live** (`init.sql`, `init-graph.sql`, entity-extraction-worker, `openbrain-wiki`). | Concept §3.1, the entity/graph/wiki layers are **done**. Do not rebuild them. **Never alter the `thoughts` table structure** (OB1 guardrail). |
| Obsidian + LLM Wiki, `notes/` vs `content/` separation, git sync, note→OB1 tethering | **~95% built and live** (`openbrain-wiki-data` repo, `notes/` user folder, `content/` generated, deploy key at `secrets/openbrain-wiki-deploy_key`). | Concept §3.2 / §6.5 are **done**. The only missing piece is the Open Notebook "send to Obsidian inbox" stub → **Phase 7**. |
| `threads`, `thread_sources`, `sessions`, `session_sources` | **Do not exist.** | **Phase 1** — the core net-new schema. |
| OWUI research pushes sources to OB1 | **Already does** via `POST /research/persist` (custom REST on the MCP server) with `x-brain-key`. **But: no session provenance, no thread linkage.** Sessions live only in local Fileshed (`smolcrawl/deep_research/journal.py`). **⚠️ The handler hard-`DELETE`s and re-inserts the per-source rows for a `research_key` on every re-run (`index.ts` ~L890–894), so source `id`s are NOT stable across runs.** | **Phase 3** adds session records + thread linkage — but must **first** make source rows durable (dedup-and-relink, not delete-and-reinsert) so thread/session FKs survive a re-run. See audit **C1** (§11) and **Phase 3.0**. |
| 11 thread/suggestion MCP tools (concept §9) | **None exist.** Today's tools: `search`, `fetch`, `search_thoughts`, `list_thoughts`, `thought_stats`, `capture_thought`, `ingest_url(s)`. | **Phase 2.** |
| Cross-thread suggestion engine | **Does not exist.** | **Phase 5** — a new worker modeled on `entity-extraction-worker`. |

### 1.1 Load-bearing files (read these before touching them)

| Concern | Path |
|---------|------|
| Sources schema (extend) | `OB1/docker/init-sources.sql` |
| Thoughts schema (read-only reference; **do not alter**) | `OB1/docker/init.sql` |
| Graph schema (pattern reference for new tables) | `OB1/docker/init-graph.sql`, `OB1/docker/init-source-graph.sql` |
| MCP server (add tools here) | `OB1/integrations/kubernetes-deployment/index.ts` |
| Extensions MCP server (pattern reference) | `OB1/docker/extensions-server/index.ts` |
| Entity worker (pattern for suggestion worker) | `OB1/integrations/entity-extraction-worker/index.ts` |
| OB1 compose (add worker service here) | `OB1/docker/docker-compose.yml`, `OB1/docker/docker-compose.scheduled.yml` |
| Cloud gateway (allow-list; keep thread tools OFF by default) | `openbrain-gateway/app.py` |
| OWUI research persistence | `smolcrawl/deep_research/evidence_memory.py`, `smolcrawl/deep_research_tool.py` |
| ON source surface — **domain models (primary repoint target)** | `d:\Open WebUI\open-notebook\open_notebook\domain\notebook.py` (`Source`, `Notebook`, `SourceEmbedding`, `SourceInsight`; `table_name="source"`; all `repo_query(...)` calls) |
| ON source surface — DB driver + migrations | `open_notebook\database\repository.py` (`repo_query`/`repo_create`, `AsyncSurreal`), `open_notebook\database\migrations\*.surrealql` |
| ON source surface — service + routers + ingest graph | `api\sources_service.py`, `api\routers\sources.py`, `api\routers\notebooks.py`, `open_notebook\graphs\source.py` — **NB: services and routers live under `api\`, not `open_notebook\`** |
| ON compose wiring (image vs build) | `ai-stack/docker-compose.yml` (services `open_notebook`, `surrealdb`) |
| Recovery scripts (three-places rule) | `scripts/emergency-recovery.ps1`, `scripts/emergency-recovery.bat` |
| Stack-map reference (three-places rule) | `.claude/skills/stack-map/references/workspace-stacks.md` |
| Wiki notes target for Phase 7 | `openbrain-wiki-data` repo `notes/` (volume `open-brain_openbrain-wiki-data`), deploy key `secrets/openbrain-wiki-deploy_key` |

---

## 2. Guardrails (apply to every task)

1. **Prod is untouchable (D3).** Never start/stop/rebuild a live container, never mount `openbrain-db-data` or `open-notebook\surreal_data`, never run a migration against live OB1. All work happens in the **`iks-dev` sandbox** (Phase 0). The only prod-facing artifact you produce is the **promotion runbook**.
2. **No commits, no staging (D4).** You may create a working branch per repo (`git checkout -b …` does not commit). You may **not** `git add`/`commit`/`push`. Leave changes in the working tree for operator review.
3. **Protected schema.** Never `ALTER`/`DROP` columns on `thoughts`. Adding columns to `sources` is allowed. **No `DROP TABLE`, `DROP DATABASE`, `TRUNCATE`, or unqualified `DELETE`** in any SQL (OB1 guardrail). All schema is **idempotent** (`CREATE … IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`).
4. **Soft-delete only.** Every "remove" in this system is a status flag flip (recoverable), never a row deletion (concept §2 principle 4, §4.3).
5. **Privacy boundary holds.** The cloud gateway (`127.0.0.1:8061`) stays the only cloud door. **New thread/suggestion tools are NOT added to the gateway allow-list** by default — they are personal/local like the extensions (`openbrain-gateway/app.py`). Document this; revisit only on explicit operator request.
6. **Three-places rule.** Adding/removing a container = edit **all three** together: the compose file, the recovery scripts' service inventory + shutdown/startup sequences, and the stack-map reference doc. Run `/stack-map` to check drift. (The Phase 5 suggestion worker triggers this.)
7. **Map the vocabulary.** "Supabase" ⇒ OB1 Postgres. "Notebook" (Open Notebook) ⇒ OB1 **thread** (1:1).
8. **Mirror existing conventions.** New SQL mirrors `init-sources.sql` (idempotent, `*_touch_updated_at` trigger, `match_*` SQL function style). New MCP tools mirror the registration pattern already in `index.ts`. New worker mirrors `entity-extraction-worker`.

---

## 3. Architecture target (what "done" looks like)

```
                         ┌──────────────────────────── OB1 Postgres (single source of truth) ───────────────────────────┐
   OWUI (discovery) ─────┤  sources(+content_hash)   thoughts   entities/edges                                          │
     research/persist    │  threads        ─────┐                                                                       │
        + session        │  thread_sources  (M:N, link_type ∈ auto|suggested|deliberate, status ∈ confirmed|pending|hidden)
        + thread link    │  sessions / session_sources (provenance)                                                     │
                         │                  └──── suggestion worker (embeddings → cross-thread "suggested/pending")     │
   Open Notebook ────────┤  source upload → find_or_create_source → thread_sources(auto)                                │
   (workbench + triage)  │  notebook = thread view  ·  triage UI (accept/hide/restore)                                  │
                         └───────────────────────────────────────────────────────────────────────────────────────────┘
                                   │ MCP tools (create_thread … capture_with_thread)        │ "send to inbox" stub
   Claude / clients ───────────────┘                                                         ▼
                                                                              Obsidian wiki repo  notes/  (already live, git-synced)

   SurrealDB  ── retained ONLY for: ON UI state · job queues · chat history · local cache  (NO source data)
```

---

## 4. Phase plan

Each phase: **Goal → Tasks → Files → Definition of Done (DoD)**. DoD is validated **in the `iks-dev` sandbox only**.

### Dependency / sequencing graph

```
Phase 0 (sandbox) ─► Phase 1 (schema) ─► Phase 2 (MCP tools) ─┬─► Phase 3 (OWUI provenance)
                                                              └─► Phase 4 (ON repoint) ─┬─► Phase 5 (suggestion worker)*
                                                                                        ├─► Phase 6 (triage in ON)
                                                                                        └─► Phase 7 (Obsidian inbox stub)
                                                              all phases ─────────────► Phase 8 (E2E + drift + promotion runbook)
* Phase 5 needs only Phases 1–2 to build, but its DoD (suggestions visible) is validated together with Phase 6.
```

---

### Phase 0 — Isolation harness & branches

**Goal:** a disposable sandbox that mirrors the real topology without touching prod, plus clean working branches.

**Tasks**
- **0.1** Create working branches (no commits): `ai-stack` → `feature/integrated-knowledge-system`; `OB1` → a local branch; `open-notebook` → a local branch. Leave all later edits uncommitted.
- **0.2** Author `documentation/implementation-guide/open -notebook-integration-openbrain/iks-dev/docker-compose.dev.yml` — a **separate compose project** named `iks-dev`. It stands up, on a dedicated bridge network and non-colliding host ports:
  - `iks-db` — `pgvector/pgvector:pg16`, initialized **from copies of** `OB1/docker/init*.sql` **plus the new `init-threads.sql`** via `/docker-entrypoint-initdb.d`. **Fresh named volume** (`iks-db-data`) — never the prod `openbrain-db-data`.
  - `iks-mcp` — the OB1 MCP server built from source, env-pointed at `iks-db` and the **live local models** (`llama-cpp`, `llama-cpp-embed` on `ai-stack_llm-net`, read-only inference — acceptable).
  - `iks-surreal` — scratch `surrealdb/surrealdb:v2`, fresh volume.
  - `iks-notebook` — Open Notebook **built from the fork** (`build: d:\Open WebUI\open-notebook`), pointed at `iks-db` + `iks-surreal`.
  - (Phase 5) `iks-suggestion-worker`.
- **0.3** Write `iks-dev/seed.sql` — synthetic, non-personal sample data: ~3 threads, ~15 sources across them (some deliberately semantically overlapping to exercise suggestions), a couple of sessions. **No real user data.**
- **0.4** Write `iks-dev/README.md` — how to bring the sandbox up/down, ports, and the explicit warning that this never touches prod volumes.
- **0.5** Snapshot current topology: run `/stack-map`, save the container inventory to `iks-dev/baseline-inventory.md` for later drift comparison.

**Files:** new under `documentation/implementation-guide/open -notebook-integration-openbrain/iks-dev/`.

**DoD:** `docker compose -p iks-dev -f …/iks-dev/docker-compose.dev.yml up -d` brings up the sandbox; `iks-mcp` answers a tools/list; `iks-db` contains the seed data; **zero** prod containers/volumes referenced (grep the dev compose to prove it).

---

### Phase 1 — OB1 data model: threads, sessions, joins, dedup

**Goal:** the net-new schema (concept §5), idempotent and additive.

**Tasks**
- **1.1** New file `OB1/docker/init-threads.sql` (ordered to run **after** `init-sources.sql`). Mirror `init-sources.sql` conventions. Create:
  - `threads(id UUID PK default gen_random_uuid(), name TEXT NOT NULL, description TEXT, status TEXT CHECK(status IN ('active','archived')) DEFAULT 'active', created_at, updated_at)` + `*_touch_updated_at` trigger.
  - `thread_sources(thread_id UUID → threads, source_id UUID → sources, link_type TEXT CHECK(link_type IN ('automatic','suggested','deliberate')), status TEXT CHECK(status IN ('confirmed','pending','hidden','inactive')), suggestion_reason TEXT, created_at, confirmed_at)`, `PRIMARY KEY(thread_id, source_id)` (one logical link per pair; status carries lifecycle). Indexes on `(thread_id,status)`, `(source_id)`.
  - `sessions(id UUID PK, origin_tool TEXT CHECK(origin_tool IN ('owui','open_notebook','manual')), query_text TEXT, thread_id UUID NULL → threads, created_at)`.
  - `session_sources(session_id UUID → sessions, source_id UUID → sources, PRIMARY KEY(session_id, source_id))`.
- **1.2** Extend `sources` (additive, allowed): `ALTER TABLE sources ADD COLUMN IF NOT EXISTS content_hash TEXT;` + `CREATE INDEX IF NOT EXISTS idx_sources_content_hash ON sources(content_hash);`.
- **1.3** Dedup helper SQL function `find_or_create_source(...)` (concept §5.2): match on `url` OR `content_hash`; if found, return existing `id` (and let caller link to thread/session); else insert. Returns `(id UUID, was_duplicate BOOLEAN)`.
- **1.4** Lifecycle helper functions (thin; the MCP layer may call these or inline SQL): `link_source_to_thread(thread, source, link_type, reason)`, `set_thread_source_status(thread, source, status)` (used for accept/hide/restore/soft-unlink). All **upsert/flag**, never delete.
- **1.5** Mirror the schema as an OB1 public-contrib extension under `OB1/schemas/research-threads/` (`README.md` + `metadata.json` + the `.sql`) to match repo norms — **optional but preferred**; the deploy-time source of truth remains `OB1/docker/init-threads.sql`.

**DoD (scratch DB):**
- Re-running `init-threads.sql` is a no-op (idempotent).
- Insert the same `(url, content_hash)` twice via `find_or_create_source` → **one** `sources` row, `was_duplicate=true` on the second.
- Link one source to two threads → two `thread_sources` rows, both `confirmed`; the source is unaffected in either (additive, concept §2.4).
- Soft-unlink → `status='inactive'`, row still present (recoverable).
- No `DROP`/`TRUNCATE`/unqualified `DELETE` anywhere in the file.

---

### Phase 2 — MCP tool extensions (concept §9)

**Goal:** the 11 thread/suggestion tools, callable over MCP and via mcpo (OpenAPI) for OWUI/ON.

**Tasks**
- **2.1** Add to `OB1/integrations/kubernetes-deployment/index.ts`, following the existing tool-registration + `x-brain-key` auth pattern:
  `create_thread`, `list_threads`, `get_thread_sources`, `add_to_thread`, `remove_from_thread`, `get_suggestions`, `accept_suggestion`, `hide_suggestion`, `get_hidden_suggestions`, `restore_suggestion`, `capture_with_thread`.
  - `capture_with_thread` = existing capture/ingest path + `thread_id` → one transaction: write source (via `find_or_create_source`) + `thread_sources(automatic, confirmed)` + optional `session_sources`.
  - `accept/hide/restore` = `set_thread_source_status` transitions per the §4.3 state machine (`pending→confirmed`, `pending→hidden`, `hidden→pending`). Nothing is destroyed.
- **2.2** **Gateway policy (guardrail 5):** do **not** add these to `openbrain-gateway/app.py`'s allow-list. Add a code comment + a line in the promotion runbook explaining the personal/local posture and how to expose read-only thread tools later if wanted.
- **2.3** Expose the new tools as OpenAPI for OWUI/ON. **No mcpo config entry is needed (audit C3):** `OB1/docker/mcpo.config.json` points the `open-brain` bridge at the **whole server URL** (`http://openbrain-mcp:8000/`), so every tool registered on `openbrain-mcp` is auto-proxied. To surface the 11 new tools: restart `openbrain-mcpo` (the **core** bridge — distinct from `openbrain-mcpo-ext`, which fronts the 39-tool extensions server) so it re-reads `tools/list`, then re-import the OpenAPI schema in OWUI. In the sandbox this is `iks-mcpo` (or call `iks-mcp` directly over MCP). Document this in the runbook; do not invent secrets.

**DoD (sandbox `iks-mcp`):** tools/list shows all 11; `create_thread` → row; `capture_with_thread(thread)` → source + `thread_sources(automatic,confirmed)` + (if session passed) `session_sources`; `get_thread_sources` returns only `status='confirmed'`; `accept/hide/restore` move a seeded suggestion through the exact §4.3 states; `remove_from_thread` sets `inactive` (recoverable), never deletes.

---

### Phase 3 — OWUI research → session provenance + thread linkage (concept §6.1)

**Goal:** research runs create a `session`, link discovered sources to it, and (when a thread is active) auto-link them to that thread; otherwise they land in the unthreaded inbox.

**Tasks**
- **3.0 — PREREQUISITE (audit C1): make research source rows durable.** Today `/research/persist` **hard-`DELETE`s** the per-source rows for a `research_key` and re-inserts them on every run (`index.ts` ~L890–894), minting fresh source `id`s each time. A `thread_sources`/`session_sources` FK to `sources(id)` would be orphaned (or blocked/cascaded) on the next run of the same query — so this path is **not** additive as written. **Before adding any session/thread linkage, refactor the per-source replace to use `find_or_create_source` (Phase 1.3):** match on `url`/`content_hash`, keep the existing row + `id`, update its content in place, and insert only genuinely new sources. The synthesis row already upserts in place via `uq_sources_synthesis_key`; extend the same stability to its source rows. Validate: re-running a `research_key` keeps source `id`s stable and preserves existing `thread_sources`/`session_sources`.
- **3.1** Extend the persistence payload + handler: `smolcrawl/deep_research/evidence_memory.py` (build the payload — `persist_research_evidence`, ~L188) and the `/research/persist` handler in `index.ts` (consume it, ~L853). On persist: create a `sessions` row (`origin_tool='owui'`, `query_text`, `thread_id` nullable); insert `session_sources` for each gathered source; if `thread_id` present, `thread_sources(automatic, confirmed)`; stamp session/provenance into `sources.metadata` (timestamp, originating query, model).
- **3.2** Surface an **active thread** to the tool: add a valve / tool parameter (`active_thread_id`) in `smolcrawl/deep_research_tool.py`. No thread ⇒ unthreaded inbox (no `thread_sources` row), still recorded as a session.
- **3.3** Preserve today's behavior: research-synthesis upsert-in-place (`uq_sources_synthesis_key`) and `volatility/revalidate` stay intact. New work is additive — note "additive" now **requires** the 3.0 refactor (the current per-source DELETE is *not* additive and must be replaced, not merely preserved).

**DoD (sandbox):** simulated persist **with** `thread_id` → one `sessions` row + N `session_sources` + N `thread_sources(automatic)`; **without** `thread_id` → `sessions` row + `session_sources`, **no** thread link (inbox); existing synthesis caching still supersedes-in-place; **re-running the same `research_key` leaves source `id`s and any existing `thread_sources`/`session_sources` rows intact** (no orphaned links — audit C1).

---

### Phase 4 — Open Notebook full repoint (D1 — the spine)

**Goal:** Open Notebook reads/writes **source data** to OB1 Postgres; SurrealDB keeps only UI/queue/chat/cache state (concept §7). Notebooks become thread views.

**Tasks**
- **4.1** Inventory the SurrealDB source surface in the fork: every read/write touching source records. **Real layout (audit C2):** start at `open_notebook/domain/notebook.py` (the `Source`/`Notebook` models — `table_name="source"` — which issue the `repo_query` calls), then `open_notebook/database/repository.py` (the SurrealDB driver), the service layer `api/sources_service.py`, the routers `api/routers/sources.py` + `api/routers/notebooks.py`, the ingestion graph `open_notebook/graphs/source.py`, and the source-related `open_notebook/database/migrations/*.surrealql`. (Domain + database live under `open_notebook/`; services + routers under `api/`.) Produce `iks-dev/on-source-surface.md` listing each call site.
- **4.2** Add an **OB1 data-access module** in the fork (direct `pg` async client **or** PostgREST via the `/rest/v1` proxy — pick `pg` for transactional upload+link; document the choice). Implement the same data shapes the ON UI already consumes, so UI code changes stay minimal (concept §7.3).
- **4.3** Repoint source operations:
  - **Upload** (concept §6.2): write to OB1 via `find_or_create_source` + `thread_sources(automatic, confirmed)` to the active thread — in one operation. Dedup notifies "already exists, added to this thread" (concept §5.2).
  - **Notebook view = thread view** (concept §6.3): opening a notebook calls `get_thread_sources(thread_id)`; show sources from **all** ingestion points (OWUI, prior ON, manual). Establish the **notebook⇄thread 1:1 mapping** (create a thread on notebook creation; store `thread_id` on the ON notebook record in SurrealDB state).
  - **Interaction** (Q&A, podcast, contradictions): read the source pool from OB1.
- **4.4** Keep in SurrealDB (concept §7.2): UI prefs, job queues, chat histories, cache. Do **not** route these to OB1.
- **4.5** One-time **migration script** (for promotion, run by operator): export existing SurrealDB source records → insert into OB1 `sources` (+ create threads from existing notebooks + `thread_sources`). Idempotent, dedup-aware, dry-run mode. Lives in `iks-dev/migrate-on-sources.*`; **never run against prod by the agent**.
- **4.6** Compose wiring: in the **dev** compose, `iks-notebook` already `build:`s the fork. For prod, **stage** (do not apply) the `ai-stack/docker-compose.yml` change swapping `open_notebook`'s `image: lfnovo/open_notebook:v1-latest` → a locally built tag/`build:` of the fork. Capture this as a runbook diff, not a live edit.

**DoD (sandbox):**
- Upload a source in `iks-notebook` UI → row in `iks-db.sources` + `thread_sources(automatic,confirmed)`; **nothing** source-shaped written to `iks-surreal`.
- Open a notebook → shows OB1 sources including a row inserted by the Phase-3 simulated OWUI path (cross-tool visibility, concept §6.3 step 2).
- Uploading a duplicate URL → no new `sources` row; user sees the "already exists, linked here" notice.
- Q&A reads from OB1. UI state (last-opened notebook etc.) still persists in `iks-surreal`.

---

### Phase 5 — Cross-thread suggestion engine (concept §4.3, §6.4)

**Goal:** a background worker proposes (never auto-creates) cross-thread links by semantic similarity.

**Tasks**
- **5.1** New worker `OB1/integrations/suggestion-worker/index.ts`, modeled on `entity-extraction-worker`: drains a queue of new/updated sources; for each, compare its embedding against **other** threads' source clusters (per-source cosine via `match_sources`, scoped per thread, or a thread centroid); when similarity > `SUGGESTION_THRESHOLD` (env, default tunable) **and** the source is not already linked to that thread, insert `thread_sources(link_type='suggested', status='pending', suggestion_reason=…)`. **Note (audit C4): a `source_extraction_queue` already exists** (created and drained by `entity-extraction-worker`, auto-filled by a trigger on `sources`). Reuse it as the work feed, or add a sibling `suggestion_queue` mirroring it — do not invent a new triggering mechanism.
- **5.2** **Critical rule (concept §4.2):** research in one thread is **never** auto-added to another. The worker only ever writes `suggested/pending`. Confirmation is a deliberate user act (Phase 6).
- **5.3** Trigger model like the entity worker: queue-drain on an HTTP POST, debounced. (The entity worker uses `POST /` for the thought queue and `POST /sources` for the source queue — mirror that route convention, e.g. `POST /suggest`; there is no `/run` route, audit C4.) Add `SUGGESTION_THRESHOLD`, dedup against existing `thread_sources` of any status (don't re-suggest a hidden/rejected pair — it stays in the hidden pool, concept §3 principle 5 / §4.3).
- **5.4** **Three-places rule (guardrail 6):** this new container must be added to the OB1 compose **and** the recovery scripts' inventory + sequences **and** the stack-map reference doc. Do this in Phase 8's drift step, but note it here so it isn't missed.

**DoD (sandbox):** seed two threads with overlapping sources → `suggested/pending` rows appear with populated `suggestion_reason`; **no** `automatic`/`confirmed` cross-links created; a previously `hidden` pair is **not** re-suggested; threshold is env-tunable and logged.

---

### Phase 6 — Triage UI inside Open Notebook (D2; concept §4.3, §6.4)

**Goal:** accept / hide / restore suggestions from within Open Notebook.

**Tasks**
- **6.1** In the fork, add a **triage queue** view: list `get_suggestions` (pending) with `suggestion_reason`; **Accept** → `accept_suggestion` (→ confirmed, source now appears in the thread view, indistinguishable from auto/deliberate links); **Hide** → `hide_suggestion` (→ hidden, leaves the queue).
- **6.2** Add a **hidden/rejected pool** view: `get_hidden_suggestions` + **Restore** → `restore_suggestion` (→ pending). The pool is always accessible (concept §3 principle 5).
- **6.3** Entry point: a triage panel/badge in the ON nav; optionally scoped per-thread when a notebook is open, plus a global review.

**DoD (sandbox):** a `pending` suggestion appears in ON triage with its reason; **Accept** makes it show up as a linked source in the thread view; **Hide** removes it from the queue but it is retrievable from the hidden pool; **Restore** returns it to pending. State transitions match §4.3 exactly.

---

### Phase 7 — Obsidian inbox stub ("send to Obsidian"; concept §6.3 step 4, §8.1 step 3)

**Goal:** an ON action drops a drafting stub into the **already-live** wiki `notes/` folder. No new pipeline — reuse the running one.

**Tasks**
- **7.1** ON action "send session to Obsidian inbox" → write a markdown stub into the wiki repo `notes/` working tree (the live git-synced user-notes folder). The stub is a **starting point, not a finished note** (concept §5 open-Q #5): session summary + key source references (titles + source IDs / wiki page links) + extracted claims. Keep it deliberately incomplete.
- **7.2** Respect separation (concept §3.2, §6.5): stubs go in `notes/` (user folder), **never** `content/` (generated), and are **not** ingested back as a `source`. (Note: the live wiki already tethers `notes/` → OB1 *thoughts*; a stub becoming a tethered thought is the existing, intended behavior — do not create a *source* from it.)
- **7.3** Path/credential handling: target the wiki working copy the way the live pipeline does (volume `open-brain_openbrain-wiki-data`); in the sandbox, target a scratch clone — do **not** push to the real `openbrain-wiki-data` remote.

**DoD (sandbox):** triggering the action writes a stub `.md` into the scratch `notes/`; it lands in the user folder, is excluded from `content/`, and is not turned into a `sources` row.

---

### Phase 8 — End-to-end validation, drift sync, promotion handoff

**Goal:** prove the whole loop in the sandbox, fix the three-places drift, and hand the operator an exact promotion runbook.

**Tasks**
- **8.1** Run the concept §6 scenarios end-to-end in `iks-dev`: 6.1 OWUI session, 6.2 ON upload, 6.3 ON interaction (cross-tool visibility), 6.4 cross-thread suggestion → triage, 6.5 Obsidian note writing. Record results in `iks-dev/e2e-results.md`.
- **8.2** **Three-places sync (guardrail 6)** for the new `suggestion-worker` (and any other new container): update OB1 compose, `scripts/emergency-recovery.ps1` + `.bat` (service inventory + ordered shutdown/startup — OB1 comes up after `llama-cpp` healthy, down before the main stack), and `.claude/skills/stack-map/references/workspace-stacks.md`. Run `/stack-map` and confirm no drift vs `baseline-inventory.md`.
- **8.3** **Promotion runbook** `documentation/implementation-guide/open -notebook-integration-openbrain/PROMOTION-RUNBOOK.md` for the operator (the agent does not execute it):
  - **Backup first:** dump live `openbrain-db` (and the wiki repo) before any migration.
  - **Migration order:** apply `init-threads.sql` (idempotent) → `content_hash` add + optional backfill → run `migrate-on-sources` (dry-run, then real) to move existing SurrealDB sources into OB1.
  - **Rebuild order:** build the ON fork image; swap the compose `image:`→`build:`; bring up respecting OB1-after-`llama-cpp`-healthy; start `suggestion-worker`.
  - **Rollback:** since all schema is additive and removals are soft, rollback = revert compose to upstream ON image + leave new tables in place (unused). Document it.
  - **Per-repo commit checklist (D4):** the operator commits ai-stack, OB1, and open-notebook separately; the agent staged nothing.
- **8.4** Update OB1 backup coverage note: confirm the existing `openbrain-db` backup includes the new tables (it dumps the whole DB — verify, don't assume).

**DoD:** all five §6 scenarios pass in the sandbox; `/stack-map` reports no drift; the promotion runbook is complete enough that the operator can execute it without re-deriving anything; nothing was committed.

---

## 5. Risk register

| Risk | Severity | Mitigation |
|------|----------|------------|
| `/research/persist` hard-DELETE orphans thread/session FKs (audit C1) | High | **Phase 3.0 prerequisite:** replace the per-source DELETE+INSERT with `find_or_create_source` (dedup-and-relink) so FKs target stable `id`s; sandbox DoD re-runs a `research_key` and asserts links survive. |
| ON repoint breaks the UI or loses data | High | Full fork build validated in sandbox first; one-time migration has dry-run + dedup; SurrealDB source data is left in place during migration (additive), so rollback is non-destructive. |
| Upstream Open Notebook drift post-fork (concept open-Q #4) | Medium | Isolate OB1 access behind one new module (Task 4.2) so future upstream merges touch a small surface. Record the fork point. |
| Suggestion noise / threshold wrong (concept open-Q #1) | Medium | `SUGGESTION_THRESHOLD` env-tunable; validate against the deliberately-overlapping seed set; suggestions are non-destructive and hideable. |
| New worker not registered in recovery/stack-map | Medium | Phase 8.2 three-places sync + `/stack-map` drift gate. |
| Accidental prod mutation | High | Guardrail 1 + sandbox-only DoD + grep-proof that the dev compose references no prod volume/container. |
| Thread tools leak to cloud | Medium | Guardrail 5: not added to gateway allow-list; documented. |
| `thoughts` table altered | High | Guardrail 3; all new structure is separate tables + additive `sources` columns only. |

---

## 6. Open questions carried from the concept (§10)

Resolved by operator decisions: **#2 triage UX → inside Open Notebook (D2).** Remaining, to settle with real data during/after validation:

- **#1 suggestion threshold** — tune in Phase 5 against seed + (later) real data.
- **#3 wiki recompile triggers** — out of scope; the live wiki already runs scheduled + change-watch + on-demand. No change.
- **#4 upstream tracking** — capture the fork point; revisit cadence after Phase 4.
- **#5 inbox stub format** — Phase 7 ships a first cut (summary + source refs + claims); refine with the operator.
- **#6 thread archival lifecycle** — `threads.status='archived'` exists in the schema; the *reopen UX* is deferred (note in Decision Log when reached).
- **#7 git-sync scope of stubs** — stubs land in `notes/`, which already syncs via git; confirm in Phase 7 DoD.

---

## 7. What this plan deliberately does NOT do

- Does not rebuild entities / knowledge graph / wiki compiler (already live).
- Does not touch the `thoughts` table structure.
- Does not introduce Supabase (the stack is plain Postgres).
- Does not build a standalone triage UI (triage is inside ON, D2).
- Does not run a bidirectional SurrealDB↔OB1 sync daemon (concept §7.3 rejects it; D1 is a clean repoint).
- Does not deploy to prod, migrate live data, or commit/push (D3, D4).

---

## 8. Conventions for the implementing agent

- Start each session by reading §0–§2, then the Progress Ledger.
- Work phase-by-phase; do not start a phase whose dependencies' DoD is unmet.
- After each task: update the Progress Ledger; if you deviated, append to the Decision Log with the why.
- Keep new SQL idempotent and mirror `init-sources.sql`. Keep new MCP tools mirroring `index.ts`. Keep the new worker mirroring `entity-extraction-worker`.
- Reference files as clickable paths in any summaries you write back to the operator.
- If a decision genuinely blocks you and isn't covered here, stop and ask — don't guess on anything that mutates data shape or topology.

---

## 9. Progress Ledger

| Phase | Task | Status | Validated in sandbox? | Notes |
|-------|------|--------|----------------------|-------|
| 0 | 0.1 branches | ☑ done | — | `feature/integrated-knowledge-system` in all 3 repos; no commits |
| 0 | 0.2 dev compose | ☑ done | ☑ | `iks-dev/docker-compose.dev.yml`; grep-proof no prod volumes |
| 0 | 0.3 seed.sql | ☑ done | ☑ | 3 threads/15 sources/2 sessions; t1↔t2 overlap |
| 0 | 0.4 dev README | ☑ done | — | |
| 0 | 0.5 baseline inventory | ☑ done | ☑ | open-brain=15 (→16 post-promote) |
| 1 | 1.1 init-threads.sql | ☑ done | ☑ | idempotent re-run clean |
| 1 | 1.2 content_hash | ☑ done | ☑ | additive column + index |
| 1 | 1.3 find_or_create_source | ☑ done | ☑ | dedup proven (same id, was_duplicate) |
| 1 | 1.4 lifecycle funcs | ☑ done | ☑ | link/status soft-flip, never delete |
| 1 | 1.5 schemas/ mirror | ☐ todo | — | optional — skipped |
| 2 | 2.1 11 MCP tools | ☑ done | ☑ | tools/list=19; all 11 exercised |
| 2 | 2.2 gateway stays closed | ☑ done | ☑ | allow-list untouched + comment added |
| 2 | 2.3 mcpo exposure | ☑ done | ☑ | whole-server proxy; restart+reimport (C3) |
| 3 | 3.0 durable source rows (find_or_create) | ☑ done | ☑ | **C1 fixed: ids stable on re-run, links preserved** |
| 3 | 3.1 session + link on persist | ☑ done | ☑ | session + session_sources + thread_sources(auto) |
| 3 | 3.2 active_thread valve | ☑ done | ☑ | valve in monolith + package + models.py |
| 3 | 3.3 preserve synthesis cache | ☑ done | ☑ | supersede-in-place intact |
| 4 | 4.1 source-surface inventory | ☑ done | — | `iks-dev/on-source-surface.md` |
| 4 | 4.2 OB1 data-access module | ☑ done | ☑ | `ob1_repository.py` validated vs iks-db |
| 4 | 4.3 repoint upload/view/interact | ◐ partial | ☑ | **upload+view repointed & validated E2E** (06-05): `save_source`→`sync_extracted_source` writes extracted content+bge-m3 embedding+`thread_sources(auto,confirmed)` to OB1; `add_to_notebook` no longer mints empty pre-extraction rows; notebook⇄thread 1:1 persists. **Remaining: interact/Q&A still reads SurrealDB `source_embedding`** (not yet pointed at `search_sources`) |
| 4 | 4.4 keep SurrealDB state | ☑ done | ☑ | only source-family routed to OB1 |
| 4 | 4.5 migration script | ☑ done | — | `migrate-on-sources.py` dry-run default; compiles |
| 4 | 4.6 stage compose swap | ☑ done | — | runbook diff (PROMOTION-RUNBOOK §4) |
| 5 | 5.1 suggestion worker | ☑ done | ☑ | built; 9 suggestions on overlap seed |
| 5 | 5.2 suggested-only rule | ☑ done | ☑ | all pending; no auto-confirm; negative control clean |
| 5 | 5.3 threshold + dedup | ☑ done | ☑ | env-tunable+logged; hidden not re-suggested |
| 6 | 6.1 triage queue | ☑ done | ☑ | FE `SuggestionsDialog` (Suggested tab, Add/Hide, why-% explain) built + **operator-approved UX** (06-05) |
| 6 | 6.2 hidden pool | ☑ done | ☑ | FE Hidden tab + Restore+undo toast; operator-approved |
| 6 | 6.3 entry point | ☑ done | ☑ | thread-mode button in `NotebookHeader`; source-mode popover on `/sources` rows; `/api/triage/*` router live |
| 7 | 7.1 inbox stub action | ☑ done | ☑ | `obsidian_inbox.py` + `/api/triage/inbox` |
| 7 | 7.2 separation respected | ☑ done | ☑ | notes/ not content/; not a source |
| 7 | 7.3 scratch wiki target | ☑ done | ☑ | `WIKI_NOTES_DIR`; scratch dir in test |
| 8 | 8.1 E2E §6 scenarios | ☑ done | ☑ | `iks-dev/e2e-results.md` |
| 8 | 8.2 three-places drift | ☑ done | — | compose 16 + recovery 16 + stack-map aligned |
| 8 | 8.3 promotion runbook | ☑ done | — | `PROMOTION-RUNBOOK.md` |
| 8 | 8.4 backup coverage | ☑ done | ☑ | whole-db pg_dump verified covers new tables |

Legend: ☐ todo · ◐ in-progress · ☑ done · ✗ blocked.

---

## 10. Decision Log

| Date | Phase/Task | Decision / deviation | Why |
|------|-----------|----------------------|-----|
| 2026-06-01 | plan | D1–D4 locked (full repoint · triage-in-ON · isolated validation · edit-no-commit) | operator |
| 2026-06-01 | audit | Plan audited against live code. **Verified accurate:** sources schema, MCP server + 8 tools, allow-list gateway (guardrail 5 holds), compose images, recovery OB1 inventory, wiki notes/content split. **Corrected:** C1 persist hard-DELETE vs FKs (new Phase 3.0 + risk row), C2 ON paths (services/routers under `api/`; added `domain/notebook.py` as primary surface), C3 mcpo proxies whole server (no per-tool entry), C4 worker route + existing `source_extraction_queue`. Full detail in §11. | audit pass |
| 2026-06-02 | 0–3 | Phases 0–3 implemented **and validated in `iks-db`**: schema idempotent + dedup/lifecycle; 19 MCP tools (8+11) with full §4.3 suggestion lifecycle; persist refactor proven C1-stable (ids survive re-run, links preserved). Branches created in all 3 repos; nothing committed. | done |
| 2026-06-02 | 4.2 | OB1 data-access chose **pg-direct via asyncpg** (per plan) over PostgREST — upload needs one-tx find_or_create+link. Added `asyncpg` to fork `pyproject.toml`; **`uv lock` must be run before building iks-notebook** (host has no `uv`; lockfile not regenerated here). Module validated standalone against `iks-db`. | done |
| 2026-06-02 | 4.3 | ON repoint wired **feature-gated** behind `ob1_enabled()` (true only when `OB1_DB_HOST` set → sandbox): `Notebook.ob_thread_id` + `ensure_ob_thread()`, `get_sources()` OB1 branch, `add_to_notebook()` OB1 dual-write. Prod path unchanged until promotion. **Remaining for full repoint:** route `Source.get/delete/get_insights` + the source-processing graph by OB1 UUID (see `on-source-surface.md`); end-to-end UI validation needs the heavy `iks-notebook` build (operator/Phase 8) — Phase 4 is the plan's largest/riskiest workstream by design. | partial — code complete, UI validation deferred |
| 2026-06-05 | 4.3 | **Ingestion write-path repoint landed & validated E2E in `iks-dev`.** Root issue found: the only OB1 write hook (`add_to_notebook`) fired *before* content extraction, so it created **empty** OB1 rows for URL uploads (and `find_or_create_source` deliberately never backfills content on a dup) — the actual extraction graph wrote nothing to OB1. Fix: (1) new `ob1_repository.sync_extracted_source()` — find-or-create + **UPDATE content/title/embedding on the canonical row** + link to each notebook's thread, in one tx; (2) called it from `graphs/source.py::save_source` (post-extraction — first point real text exists), embedding via bge-m3; (3) tightened `add_to_notebook` to only write when `full_text` is already present (re-linking an existing source), so fresh uploads no longer mint empty rows. Smoke test: text upload → OB1 row with real content, `has_embedding=t`, `content_hash`, `metadata.on_source_id` cross-id map, `content_type='manual'` (no-URL default), `thread_sources(automatic,confirmed)` to the notebook's thread; notebook⇄thread 1:1 (`ob_thread_id`) persisted; no orphan threads. Also fixed `GET /notebooks` list endpoint to serialize `ob_thread_id` (single-get already did). **Still open in 4.3:** interact/Q&A reads (`graphs/ask.py` → SurrealDB `source_embedding`) not yet pointed at `ob1_repository.search_sources`. | done (upload+view) — interact/Q&A pending |
| 2026-06-05 | 4.3 | **Source-identity routing for delete + notebook add/remove made OB1-aware** (operator hit it while testing delete). The notebook view lists OB1 UUIDs, but `DELETE /sources/{id}` and `POST/DELETE /notebooks/{nb}/sources/{id}` still resolved against SurrealDB → would 404 on a UUID and never touch OB1. Wired OB1 branches: `DELETE /sources/{id}` → `ob1.soft_delete_source` (guardrail 4 — links→'inactive' + `metadata.deleted=true`, row+embedding preserved/recoverable; filtered from `list_sources`); `POST /notebooks/{nb}/sources/{id}` → `link_source_to_thread(deliberate,confirmed)`; `DELETE /notebooks/{nb}/sources/{id}` → `set_thread_source_status(inactive)`. Validated: DELETE returns 200, row persists, link inactive, source leaves global list. Leaves only **interact/Q&A** open in 4.3. | done (delete/add/remove); interact pending |
| 2026-06-05 | extraction (out-of-plan, ON fork) | **Source images fixed via a local extractor (operator-reported bug).** Web sources showed no images: content-core's `auto` URL engine used crawl4ai → dead `blob:http://localhost/...` URLs (the local `simple`/bs4 engine strips images entirely); PDFs were text-only. Not an OB1/viewer fault — OB1 stored exactly what content-core produced; the ON viewer renders valid image URLs fine. Evaluated Firecrawl self-host but the only prebuilt image is now a 4–5 service stack (Postgres+RabbitMQ+Redis+Playwright+harness); operator chose a **custom local extractor** (privacy-preserving, zero new infra). Built `open_notebook/utils/url_markdown.py`: fetch → readability (main content) → **externalize inline base64 `data:` images to files** → absolutize relative img/href → **pandoc** (`gfm-raw_html`) → markdown, with markdownify + content-core fallbacks. Wired as a URL fast-path in `graphs/source.py::content_process` (YouTube/files stay on content-core). Added `SOURCE_ASSETS_FOLDER` + a read-only `GET /api/source-assets/{name}` route (realpath-guarded) so images serve same-origin; added `pandoc` to `Dockerfile.single`. Validated on the transformer-circuits article: stored content **3.3 MB → 27.8 KB** (clean text), **7 images externalized** and served (`200 image/png`), zero base64 in the source-of-truth (keeps embeddings/entities/wiki clean). No new container → no three-places drift. **PDF figures still deferred** (operator), but the externalization + serving infra now exists to reuse for docling figures later. | done (web); PDF figures deferred |
| 2026-06-05 | extraction (fix) | **Follow-up fixes after operator testing.** (1) pandoc's `gfm-raw_html` keeps `<div>` wrappers (readability wraps content in a div); a leading raw-HTML block makes ReactMarkdown (no rehype-raw) treat the whole body as literal HTML, so the externalized `![](…)` images never parsed/rendered — *the* reason images didn't show. Fix: `_clean_markdown` strips standalone structural tag lines (div/section/figure/…) from pandoc/markdownify output while preserving block spacing (unwrapping in HTML mashed text). (2) `sync_extracted_source` now clears `metadata.deleted` on re-ingest so a delete→re-add resurrects the source (back in global list; `link_source_to_thread` already reactivates inactive links). Validated on the article: content `<div>`=0, 7 image refs top-level, starts with clean text, present in global list, assets serve 200. | done |
| 2026-06-05 | 6 | **Phase 6 triage UI complete & operator-approved.** `SuggestionsDialog` (thread-mode button in `NotebookHeader`; source-mode popover on `/sources` rows): Suggested/Hidden tabs, Add (names target thread), Hide, Restore+undo, collapsible why-% (explain endpoint = nearest confirmed sources w/ cosine), subject header card. Ledger 6.1–6.3 → done. | done |

---

## 11. Audit findings (2026-06-01) — plan vs. live code

Verified against the actual workspace before execution.

**Confirmed accurate (no change):**
- `sources` schema (`OB1/docker/init-sources.sql`): `VECTOR(1024)` (bge-m3), `content_type` CHECK enum, `notebook`/`domain`/research-linkage columns, **no `content_hash`**, `uq_sources_synthesis_key` partial-unique index, `match_sources()` SQL fn + `sources_touch_updated_at` trigger. Phase 1.2's additive `content_hash` is right; Phase 1.3's `find_or_create_source` matches the existing convention.
- MCP server is `openbrain-mcp`, built from `OB1/integrations/kubernetes-deployment/index.ts`; `POST /research/persist` exists with `x-brain-key` auth. Current tools (**8**): `search`, `fetch`, `search_thoughts`, `list_thoughts`, `thought_stats`, `capture_thought`, `ingest_url`, `ingest_urls`.
- Gateway `openbrain-gateway/app.py` is an **allow-list** (`ALLOWED_TOOLS`, default-deny; `tools/list` is filtered to it). **Guardrail 5 holds** — new thread tools stay private unless explicitly added. ✔
- Compose: `open_notebook` = `lfnovo/open_notebook:v1-latest`, `surrealdb` = `surrealdb/surrealdb:v2`, `SURREAL_URL=ws://surrealdb:8000/rpc`. `repository.py` is the SurrealDB driver. Migrations at `open_notebook/database/migrations/*.surrealql`.
- Recovery script `scripts/emergency-recovery.ps1` carries the OB1 service inventory (`$Script:OB1Services` — **15** services incl. scheduled `cron`/`gmail-pull`/`gmail-prune`/`digest`) + ordered start/stop → the three-places rule (guardrail 6) is real. The Phase 5 worker is ~16th.
- Deploy key `secrets/openbrain-wiki-deploy_key` + volume `openbrain-wiki-data` + `notes/` vs `content/` split (`WIKI_OUT_DIR=/wiki/content`) are all live. `extensions-server/index.ts`, `init-graph.sql`, `init-source-graph.sql` exist.

**Corrections (applied inline above):**

| ID | Sev | Finding | Fix location |
|----|-----|---------|--------------|
| **C1** | High | `/research/persist` hard-`DELETE`s per-source rows per run (`index.ts` ~L890–894) → would orphan Phase-1 `thread_sources`/`session_sources` FKs. The OWUI path is **not** additive as written. | New **Phase 3.0** (refactor to `find_or_create_source`); §1 reality map; risk register; Phase 3.3 + DoD. |
| **C2** | Med | ON file paths wrong: `sources_service.py` is at `api/`, routers at `api/routers/` (not `open_notebook/`). The real primary source surface — `open_notebook/domain/notebook.py` (`Source`/`Notebook`, `table_name="source"`) — was missing. | §1.1 load-bearing table; Phase 4.1. |
| **C3** | Med | mcpo proxies the **whole** MCP server URL (`http://openbrain-mcp:8000/`), not per-tool — no config entry is needed. Two mcpo instances exist: `openbrain-mcpo` (core) and `openbrain-mcpo-ext` (extensions). | Task 2.3. |
| **C4** | Low | A `source_extraction_queue` already exists (entity worker drains it, trigger-filled). Worker route is `POST /` / `POST /sources`, not `/run`. | Phase 5.1, 5.3. |
