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
- **0.2** Author `documentation/implementation-guide/open-notebook-integration-openbrain/iks-dev/docker-compose.dev.yml` — a **separate compose project** named `iks-dev`. It stands up, on a dedicated bridge network and non-colliding host ports:
  - `iks-db` — `pgvector/pgvector:pg16`, initialized **from copies of** `OB1/docker/init*.sql` **plus the new `init-threads.sql`** via `/docker-entrypoint-initdb.d`. **Fresh named volume** (`iks-db-data`) — never the prod `openbrain-db-data`.
  - `iks-mcp` — the OB1 MCP server built from source, env-pointed at `iks-db` and the **live local models** (`llama-cpp`, `llama-cpp-embed` on `ai-stack_llm-net`, read-only inference — acceptable).
  - `iks-surreal` — scratch `surrealdb/surrealdb:v2`, fresh volume.
  - `iks-notebook` — Open Notebook **built from the fork** (`build: d:\Open WebUI\open-notebook`), pointed at `iks-db` + `iks-surreal`.
  - (Phase 5) `iks-suggestion-worker`.
- **0.3** Write `iks-dev/seed.sql` — synthetic, non-personal sample data: ~3 threads, ~15 sources across them (some deliberately semantically overlapping to exercise suggestions), a couple of sessions. **No real user data.**
- **0.4** Write `iks-dev/README.md` — how to bring the sandbox up/down, ports, and the explicit warning that this never touches prod volumes.
- **0.5** Snapshot current topology: run `/stack-map`, save the container inventory to `iks-dev/baseline-inventory.md` for later drift comparison.

**Files:** new under `documentation/implementation-guide/open-notebook-integration-openbrain/iks-dev/`.

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
- **8.3** **Promotion runbook** `documentation/implementation-guide/open-notebook-integration-openbrain/PROMOTION-RUNBOOK.md` for the operator (the agent does not execute it):
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
| 4 | 4.3 repoint upload/view/interact | ☑ done | ☑ | **upload+view+interact repointed & validated E2E**: `save_source`→`sync_extracted_source` writes extracted content+bge-m3 embedding+`thread_sources(auto,confirmed)` to OB1; `add_to_notebook` no longer mints empty pre-extraction rows; notebook⇄thread 1:1 persists; chat/source-chat/podcast context read OB1. **Interact/Q&A now reads OB1 (06-05):** the global ask graph + `/search` vector both retrieve canonical `source_chunks` via `ob1.search_all_chunks`→`group_chunks_by_source` (feature-gated; SurrealDB fallback when OB1 off). Validated: `/search` vector → OB1 source rows w/ titles+scores+snippets; `/search/ask/simple` → grounded answer citing real OB1 `source:<uuid>`. Keyword `text_search` intentionally stays SurrealDB (no OB1 FTS; semantic is the grounded path). |
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
| 2026-06-05 | extraction (fix) | **The actual "no images" root cause: corrupt externalized PNGs.** Images served 200 and markdown/rendering were all verified correct, but the files were **invalid PNGs** (valid header, broken body) → browser showed blank. Cause: `readability` (`Document.summary()`) re-serialises HTML through lxml, which **mangles very long base64 `data:` attribute values**; decoding the mangled base64 yielded corrupt bytes. (Earlier console errors — `content.js`/webextension `polyfill.js` "block checksum mismatch" — were an unrelated browser-extension red herring.) Fix: `_externalize_images_in_html` pulls inline base64 images out of the **raw HTML before readability touches it**, so readability only ever sees short `/api/source-assets/<hash>` URLs. Re-ingest → 9/9 valid PNGs, all serve 200, content references all 9. Diagnosed by content-hash + PIL `verify()` on the stored files (they matched their hashes but failed to decode) and a fresh decode that was valid only when readability was skipped. | done |
| 2026-06-05 | extraction (fix) | **Final image bug: served URLs absolutized to the wrong domain.** After moving externalization before readability, `_process_html`'s page-relative→absolute step ran `urljoin(source_url, "/api/source-assets/x.png")` → `https://transformer-circuits.pub/api/source-assets/x.png`, so the browser fetched the *source's* domain → 404 → blank. Curl checks masked it (the grep matched only the `source-assets/<hash>.png` suffix). Surfaced by comparing content via the **frontend proxy (:18502)** vs backend. Fix: exclude `/api/source-assets/` from absolutization (keep root-relative → resolves against the ON origin). Verified: 9 root-relative refs, 0 wrong-domain, 9/9 serve 200, files valid PNGs. **Full image chain now correct end-to-end** (blob→local extractor, raw-div→clean_markdown, corrupt-png→externalize-before-readability, wrong-domain→skip absolutize). | done |
| 2026-06-05 | 4.3 (interact) | **Chat + podcast context repointed to OB1 (RAG/direct, operator-chosen over agentic-MCP).** Operator: "podcasts, chat, etc. should use the OB1 backend, not SurrealDB." Done: (1) **source-scoped chat** — `context_builder._add_source_context` now reads `ob1.get_source(uuid)` for OB1 sources (the SurrealDB `Source.get` never found the UUID, so chat-with-source was broken for OB1 sources); insights empty (OB1 AI notes WIP). (2) **notebook chat** already OB1 (thread-scoped via `get_thread_sources`). (3) **podcasts** — new `Notebook.get_context()` assembles the OB1 thread's source content; `podcast_service` already calls it (was a `str(notebook)` stub). Also: **`source_count`** now reads OB1 `count_thread_sources` for OB1 notebooks (was SurrealDB `reference` edges) + OB1-aware delete/add/remove (prior entry); cleaned 14→4 duplicate SurrealDB source records. Validated: source-chat context = 27,773-char OB1 content; `get_context` = 121,372 chars over 3 OB1 sources. **Deferred:** semantic-ask `vector_search`→`search_sources` (separate "ask" feature, not chat); notes→OB1 AI notes (operator: in active dev, will signal). NB: live LLM chat/podcast audio still need ON model-registry config (orthogonal to the OB1 context repoint). | done (context); model-config separate |
| 2026-06-05 | 4.3 (chat enablement) | **Configured a sandbox chat model + fixed 3 OB1 source-chat blockers; source chat now works E2E on OB1 content.** Operator asked to configure a model to test. Created an `openai_compatible` credential → local `llama-cpp` (`http://llama-cpp:8080/v1`), a `qwen36-27b:nothink` language model (ON model-test passed), set it as default chat/transformation/tools/large-context model. Enabling source chat surfaced 3 OB1-identity bugs (all fixed): (1) every source-chat endpoint verified existence via `Source.get` → 404 on OB1 UUIDs → added `_source_exists` (OB1-aware) across 6 sites; (2) `session.relate("refers_to", ...)` built an inline `RELATE` with a hyphenated UUID → SurrealDB parse error → backtick-escape the record id for OB1 sources (the `refers_to` SELECTs use RecordID params, already safe); (3) **asyncpg "another operation is in progress"** — ON chat graphs run context-building in short-lived event loops (threads) but asyncpg pools are loop-bound → reworked `ob1_repository` to keep **one pool per event loop** (keyed by `id(loop)`, prune closed loops) + `close_current_pool()` called by the source-chat graph to avoid leaking ephemeral-loop connections. Verified E2E: source chat answered from the OB1 article (named "mechanistic interpretability" / "superposition"). NB: podcast *audio* still needs a TTS model (not in the local stack); chat/notebook-chat context is OB1. | done |
| 2026-06-05 | 4.3 (chat fixes) | **Two more OB1 chat fixes from operator testing.** (1) **Citation links** (`source-references.tsx`): the reference id regex `[a-zA-Z0-9_]+` truncated UUIDs at the first hyphen, so `source:781dac02-d43c-…` linked to a non-existent `781dac02` (and the tail leaked as text). Added `-` to the class; verified it captures the full UUID and ignores the `OB-AUTONOMOUS:` colon. (2) **Notebook chat returned empty context** `{'sources':[],'notes':[]}` despite 3 OB1 sources: `/chat/context` resolved sources via `Source.get`/`get_context` (SurrealDB) → skipped UUID sources (the `context_config` path is the one the UI uses; the no-config default path is unreachable — field is required). Added `_ob1_source_context()` (content-as-grounding, since OB1 insights are WIP) to both paths. Verified E2E: `/chat/context` → 3 sources / 123k chars; `/chat/execute` "give an overview" listed the sources. | done |
| 2026-06-05 | chat-RAG (new, OB1-canonical) | **Retrieval-grounded, gap-honest chat (foundation; operator: insights-as-summaries destroy evidence + invite hallucination).** Replaced full-content/summary context with **passage-level RAG over OB1 `source_chunks`**. Key constraint (operator): chunking must be **OB1-canonical, shared by all frontends**, not ON-specific — so built an OB1-side **chunk-embedding worker** (`OB1/integrations/chunk-embedding-worker/`, Deno, suggestion-worker template) that reuses the workbench's EXACT chunker (`chunk.ts`: 1200char/150 overlap) + bge-m3, and is **writer-agnostic** (scans `sources` for content changed since last chunked via `md5(content)` vs `metadata.chunked_hash`) so ON/`capture_with_thread`/`/research/persist`/workbench all get identical chunks. Schema: applied `init-source-retract.sql` + `init-source-chunks.sql` to iks-db (+ compose mounts 82/86); only the workbench wrote chunks before. Retrieval: `ob1_repository.search_source_chunks` / `search_thread_chunks` (scoped, top-K verbatim w/ source_id+title). Wired query-time retrieval into source-chat graph + `/chat/execute` (thread-scoped via session→notebook→`ob_thread_id`), replacing the full-content dump. Grounded gap-honest prompts (`source_chat/system.jinja`, `chat/system.jinja`): answer only from passages, cite `[source:<id>]`, **declare gaps instead of fabricating**. Validated E2E: 29 sources→199 chunks; source-chat cited verbatim + on out-of-source Qs said "the retrieved passages don't address X"; notebook-chat grounded across thread + declared a missing-topic gap. **Solves all 3 operator concerns: bounded context, intact evidence, no hallucination.** Sandbox: `iks-chunk-worker` (15s scan). **TODO for promotion (Phase 8 three-places):** add `openbrain-chunk-worker` to OB1 prod compose + recovery scripts + stack-map; ensure `source_chunks`/retract present in prod. **Deferred (operator):** gap→research trigger. | done (foundation); prod three-places staged in runbook |
| 2026-06-05 | chat-researcher (new) | **Agentic, evidence-verified chat researcher (operator: chat must be a researcher — expand semantics, validate claims vs source, gaps as outcome; pointed to `smolcrawl/deep_research`).** Two parts. (1) **Bug:** `/chat/execute` ran `chat_graph.invoke` synchronously in the async handler → blocked the event loop ~18s → uvicorn socket stall → Next rewrite-proxy "socket hang up" → **500** on the summary query. Fix: `await asyncio.to_thread(...)`; verified 200 via proxy. (2) **Researcher:** new `open_notebook/research/chat_researcher.py` adapting deep_research's evidence-source-agnostic pipeline (anchor → iterative retrieval w/ term-expansion + continue/stop → synthesis → **claim verification vs evidence** → remediation of unsupported claims → gaps + credibility footer), but over **OB1 `source_chunks`** (`search_thread_chunks`/`search_source_chunks`) and ON's model layer (`provision_langchain_model`, `[source:<id>]` citations). Wired into the notebook chat graph (`ob_thread_id` routed from session→notebook). Validated: aggregate "summarize the notebook" → structured, source-grounded, cited (was a 500/ramble); mixed grounded+gap query → cited training-cost evidence the naive top-K missed, AND a precise Gaps section (carbon footprint absent; "millions" but no specific figures; flagged AI- vs quantum-superposition) + footer "18 passages/2 sources, grounding high, 1 weak claim flagged". Solves the classic RAG aggregate-query failure + the "summaries obliterate evidence/invite hallucination" concern. Latency ~50-76s (several LLM calls; operator accepted for full-agentic). **Source chat** still uses the simpler passage-RAG+grounded prompt (can upgrade to the researcher next, same pattern). **Deferred:** gap→deep_research(web) escalation. | done (notebook chat); source-chat upgrade optional |
| 2026-06-05 | chat-researcher (fixes) | **Proxy timeout + OWUI-escalation prompt.** (1) Researcher takes ~50–108s (several LLM calls); the Next.js `/api/*` rewrite proxy has a **hard 30s timeout** → 500 at exactly 30.02s. Fix: `experimental.proxyTimeout = 300_000` in `next.config.ts` (axios client already allows 600s). Verified 200 at 108s through the proxy. (2) Escalation (operator: don't auto-trigger; deep_research lives in OWUI — have the model hand back a research prompt the user runs in OWUI when ready): synthesis prompt now emits a **"### Research this further"** section with a ready-to-paste research query for the most important gap (only when material gaps exist). **Open latency note:** ~50–108s/turn on the sandbox qwen — works but slow; tunable via rounds/model/streaming (operator to decide). | done |
| 2026-06-05 | chat-researcher (streaming) | **Streamed researcher progress (operator chose streaming over trimming depth).** New SSE endpoint `POST /chat/execute/stream`: runs the researcher with an `on_progress` callback, emits live `status` events (planning → gathering r1/r2 → synthesizing N passages → verifying claims → removing unsupported) then the final `ai_message`, persists the turn via `chat_graph.update_state` so follow-ups keep context. Frontend: `chatApi.sendMessageStream` (fetch+ReadableStream, mirrors source-chat) + `useNotebookChat` consumes the stream into a placeholder assistant bubble (status → final answer). Keeps full agentic depth; first status at ~2.8s + continuous keepalives means the 30s proxy limit can't trip. Validated via proxy: live stages then a 4172-char verified answer at 114s. Non-OB1 notebooks fall back to the graph in the same stream. | done |
| 2026-06-05 | chat-researcher (RAG-quality) | **Fixed the classic RAG false-gap problem + grounding leaks (operator demonstrated: feeding the model's own "gaps" back retrieved the very content it claimed missing).** (1) **Gap-verification loop**: after synthesis, parse the `### Gaps` bullets and RE-RETRIEVE for each; if new evidence turns up, re-synthesize — only gaps that survive a targeted search are reported (a gap was a RETRIEVAL miss, not a source absence). (2) **Coverage mode**: anchor flags `aggregate`; for summary/overview queries, seed evidence with a per-source SPREAD (`ob1.source_chunk_spread`, evenly sampled across each doc, 12/source, cap 42) + round-robin source-diverse selection, so "summarize everything" actually covers each source instead of top-K-similar-to-"summary". (3) **Prompts**: synthesis now frames gaps as "not in the retrieved excerpts" (NOT "the source lacks"), forbids meta/process commentary + padding with marker-only sources, demands exact per-claim citation + accurate source counts; verification adds `wrong_citation`, `off_topic`, `source_lacks_overreach` checks (relevance + correct attribution). Validated: summary breadth improved (detokenize/universality/softmax now surfaced), gaps now honestly retrieval-aware. **Streaming fix**: switched stream to `text/plain`+`no-transform`/`X-Accel-Buffering` (text/event-stream was gzip-buffered by the proxy for browsers) and the frontend now skips the 123K `buildContext` for OB1 (opens stream immediately). **Open:** aggregate latency ~120s (coverage + gap-recheck + 2 syntheses); a bounded summary still can't include every passage of a long source. | done |
| 2026-06-05 | chat-researcher (staged) | **Budget-aware map-reduce synthesis (operator: large source pools overflow the window → context drift/hallucination; pointed to deep_research `fileshed.py`/`journal.py`/`context_budget.py` staged+grouped+anchored pattern; evidence+validation is a hard requirement for trust).** When the gated evidence exceeds `_SYNTH_BUDGET_CHARS` (24K), synthesis switches from single-pass to **map-reduce**: group passages by SOURCE → batch each to `_MAP_BUDGET_CHARS` (12K) → **MAP** extracts grounded, cited findings from ONE source's excerpts per call (anchored, bounded — no cross-source drift) → **REDUCE** combines only the small grounded findings (never the full pool) into the final answer. Verification evidence is also budget-capped. Mirrors deep_research's grouped/anchored staging (per-group grounding = the anchoring mechanism). Also: streaming-bubble polish (hide the redundant spinner once the status/answer bubble shows). **Next iteration (proposed, not yet built):** relevance-gate tiers (relevant/trail/drop + authority) before map; on-disk journaling (fileshed-style) for resumability + very large pools; per-group verification in the MAP stage. | done (core map-reduce); gate+journal next |
| 2026-06-05 | chat-researcher (latency tuning) | **Latency: 213s → 77s while improving quality.** (1) The 24K staged threshold was far too low for a 256K-token model → it forced map-reduce (≈10 LLM calls) on notebooks single-pass handles. Raised `_SYNTH_BUDGET_CHARS`→120K (env `CHAT_SYNTH_BUDGET_CHARS`); staged is now only the safety net for genuinely overflowing pools. (2) Parallelizing the MAP calls did NOT help — single GPU, compute-bound (concurrent calls share compute). (3) Gate gap-verification re-synth to FOCUSED queries only — aggregate already reads every source via coverage, so its first-pass gaps are real; skip the wasted second synthesis. (4) Single-pass synthesis prompt now also discards ingestion/capture metadata (the OB-AUTONOMOUS marker leak). Result on the 3-source notebook: **77s, both real sources covered, metrics (nats/35→60%/detokenize) present, capture-marker dropped, verification ran.** Floor is ~the model: anchor+synth+verify (+remediate) ≈ 4 GPU calls; faster model would trim further. | done |
| 2026-06-05 | chat-researcher (portable budget + citation names) | **(1) Relevance-first, 32K-bounded grouping (operator: a huge budget recreates the one-big-pool drift, and smaller models / other deployments drift on long context; limit by relevance THEN size; 32K is enough).** Set `_SYNTH_BUDGET_CHARS`/`_MAP_BUDGET_CHARS` = 32K (env `CHAT_SYNTH_BUDGET_CHARS`); staged synthesis now ranks ALL passages by relevance then forms ≤32K groups in relevance order (each excerpt keeps its `[source:id]` so findings cite correctly), MAP per group → REDUCE. Portable + drift-resistant regardless of model window; per-deployment tunable. Validated (50K notebook → staged): 155s, 18 citations, both sources, metrics present, no meta-leak. (2) **Citations show source NAME not uuid** — `convertReferencesToCompactMarkdown` takes a `sourceNames` map; threaded ChatColumn (builds id→title from the notebook sources) → ChatPanel → AIMessageContent, so the reference list reads "Softmax Linear Units.pdf" not "source:8167…" (user can see/where claims come from + log to memory). Operator: results "feel better". | done |
| 2026-06-05 | 6 | **Phase 6 triage UI complete & operator-approved.** `SuggestionsDialog` (thread-mode button in `NotebookHeader`; source-mode popover on `/sources` rows): Suggested/Hidden tabs, Add (names target thread), Hide, Restore+undo, collapsible why-% (explain endpoint = nearest confirmed sources w/ cosine), subject header card. Ledger 6.1–6.3 → done. | done |
| 2026-06-05 | 4.3 (interact/Q&A) | **Closed the last SurrealDB read in the Phase 4 spine: the global "ask"/vector-search Q&A now reads OB1.** The knowledge-base-wide ask graph (`graphs/ask.py::provide_answer`) and the `/search` vector branch both still hit SurrealDB `fn::vector_search` (the `source_embedding` index). Repointed both, feature-gated behind `ob1_enabled()` (prod path unchanged until promotion): new `ob1_repository.search_all_chunks(query)` retrieves the most query-relevant **canonical `source_chunks`** verbatim passages across the WHOLE source pool (same chunk index chat uses; excludes retracted + soft-deleted), and `group_chunks_by_source()` collapses passage hits into one result row per source shaped for BOTH consumers — `id`/`parent_id`=`source:<uuid>` (keeps `[source:<id>]` citation rendering + the source modal working), `title`, `final_score`, `matches` (snippets) for the search UI; joined passages as grounding for the ask answer prompt. Also relaxed the `/search` + `/search/ask(/simple)` embedding-model guards: OB1 mode embeds via OB1's own bge-m3 endpoint, so an ON `default_embedding_model` (null in the sandbox) is no longer required. Validated in `iks-dev`: `/search` vector "superposition in neural networks" → 2 OB1 sources (Softmax Linear Units.pdf 0.69, Interpretability Dreams 0.62) with titles+scores+snippets; `/search/ask/simple` → accurate, fully `[source:<real-uuid>]`-cited answer grounded in the actual paper (polysemanticity / overcomplete basis / privileged basis). Keyword `text_search` deliberately stays SurrealDB (no OB1 full-text index; semantic retrieval is the grounded Q&A path). **Phase 4 DoD "Q&A reads OB1" met → Phase 4 spine complete.** NB: search-page ask citations still render the raw `source:<uuid>` (the source-NAME threading was done for notebook chat only) — a small FE follow-up if desired, not required by the DoD. | done |
| 2026-06-06 | podcast (researcher carryover) | **Podcast generation now sources its content from the evidence-VALIDATED researcher over OB1 — not a raw full-text dump (operator: "the research chat integrations should carry over to the podcast").** Content already came from OB1 (`Notebook.get_context()`→`get_thread_sources`) but as a full-text join of every source — the context-overflow / no-validation problem rejected for chat. Added `ChatResearcher.research_briefing()`: reuses the same machinery (factored shared `_gather` + `_map_findings`; force-aggregate per-source coverage spread → bounded ≤32K relevance-ordered MAP grounding → claim-vs-evidence VERIFICATION → remediation drops unsupported facts) but a NARRATIVE `_BRIEFING_REDUCE_PROMPT` emits clean spoken-ready prose, then `_strip_citations_and_meta` removes any `[source:id]`/`[UNVERIFIED]`/Gaps/footer. **Per operator clarification:** the *facts that inform* the narrative get the full validation rigor, but the delivered context states NO validation/citation/gaps framing (that would bias the episode toward fact-gathering and away from its narrative/entertainment focus). Wiring: `PodcastGenerationInput` gained `notebook_id`; `podcast_service.submit_generation_job` DEFERS content for OB1 notebooks (passes `notebook_id`, no get_context dump — the minutes-long synthesis belongs in the background worker, not the submit handler) and `podcast_commands.generate_podcast_command` builds the validated briefing via `_build_podcast_content` (outline_llm model), falling back to raw `get_context` then passed content. Validated in `iks-dev`: briefing for the SoLU/interpretability notebook = 4035 chars, **0 citation markers, no Gaps/Research/footer sections**, clean topical prose (the only "evidence" word is the paper's own scientific content). NB: podcast *audio* still needs a TTS/voice model in the profile (not in the local stack) — orthogonal to this content repoint. | done (content); audio/TTS separate |
| 2026-06-07 | notebook sync B2 (create=thread) + B5 (syntheses-as-notes) | **B2 create = thread (operator: "creating a notebook in ON is creating a thread in OB").** `POST /notebooks` now eagerly `ensure_ob_thread()` → an OB1 thread is created on notebook creation (validated: ob_thread_id set, iks-db threads +1, thread named after the notebook). ON does NOT write the wiki folder; the compiler renders the thread into `notebooks/` and the B1 read-sync reconciles `folder_synced`. **B5 notes panel = the notebook's deep-research syntheses (operator: "syntheses as notes; raw sources stay in Sources").** Content is resolved from the DB, never the folder (operator guide): thread_id (= `ob_thread_id`) → `thread_sources` → `sources` where `content_type='research_synthesis'` (`sources.content` is the markdown). New `ob1.get_thread_notes()` + `GET /notebooks/{id}/notes`; the Sources panel now **excludes** `research_synthesis`; frontend `useNotebookSyntheses` merges them into the Notes panel as **read-only** cards (Research badge, click→source modal, no edit/delete). Chat/researcher/podcast retrieval unchanged (still see all sources/chunks). Validated (seed): synthesis → notes endpoint only; raw source → sources panel only. **Source↔thread semantics reaffirmed (operator):** source added in OpenBrain → ON shows it; ON removes a source → `thread_sources` set `inactive` (disconnected, **never deleted in OB1**) — already built. Build cp'd; image rebuild + compose wiki-mount + migration 16 owed at promotion. | done (B2,B5); AI/user notes later |
| 2026-06-06 | notebook ⇄ wiki-folder sync (B1/B4) | **Wiki `content/notebooks/` folder → ON notebooks, folder is authority (operator).** Each `notebooks/<slug>/<slug>.md` frontmatter carries `thread_id` (one wiki notebook = one OB1 thread). New `api/notebook_sync.py::reconcile()` scans the folder (RO mount), upserts an ON `Notebook` per entry keyed by `ob_thread_id` (name from the OB1 thread; `folder_synced=true`), and **deletes folder-synced ON notebooks whose entry is gone** — SurrealDB mirror record only, **no OB1 calls**, so the thread + `thread_sources` persist (operator: "source connections still remain"). Triggers: `POST /notebooks/sync` + scheduled (`NOTEBOOK_SYNC_INTERVAL_S`). Needed **migration 16** (`folder_synced` DEFINE FIELD — notebook table is SCHEMAFULL, like `ob_thread_id`/migr.15). Sandbox compose mounts `open-brain_openbrain-wiki-data:/wiki:ro`. Validated in `iks-dev`: 38 folders → 38 notebooks; idempotent; exclude-one → removed with zero OB1 unlink → restore re-creates. **Decisions:** SurrealDB stays as ON's local store (D1) — the notebook record is a thin **regenerable mirror** of the OB1 thread (resolves the operator's "no second source of truth" concern); **source↔thread link/unlink are OB1 ops** (`thread_sources` link / set-inactive; row never deleted — already built); **notebook removal ≠ source removal**. **NB:** sandbox ON→`iks-db` (6 threads) so name/source-from-thread resolves fully only in prod (ON→`openbrain-db`, 38 threads). Build cp'd; image rebuild + mount + migration 16 owed at promotion. **Gated next:** B2 create (ON→folder→thread) and B5 notes-panel — both await the OB1/compiler work + open Qs (see NOTEBOOK-FOLDER-SYNC-PLAN §4). | done (B1/B4); B2/B5 gated |
| 2026-06-06 | conform ON to LIVE OB1 (post-OB1-changes) | **Operator changed the live OB1 thread tables and asked to conform the ON fork; verified it ALREADY conforms — no code change needed.** Read-only inspection of live `openbrain-db` vs the fork's assumptions: function signatures are **identical** (`find_or_create_source`/`link_source_to_thread`/`set_thread_source_status`/`match_sources`/`match_source_chunks`), columns are a **compatible superset** (live adds `threads.slug`, `sources.last_edited_by/at`, new `source_revisions`/`source_entities`/`content_types` — all inert to the fork), and the live CHECK constraints (`threads.status` active/archived; `thread_sources.link_type` automatic/suggested/deliberate; `thread_sources.status` confirmed/pending/hidden/inactive) are exactly what the fork uses. The fork's own `get_thread_sources` + chunk-JOIN queries run verbatim against live and return real rows. The sandbox `iks-db` already mirrors live's fork-relevant schema (same functions + the `trg_queue_source_extraction` trigger + queue), so all session E2E already validated against the live shape. **Promotion implication:** the OB1-side IKS schema + functions are ALREADY in prod (threads/thread_sources/sessions/source_chunks tables + all functions) — runbook B2/B3 (schema/functions) is largely done. **Two operational prerequisites surfaced (NOT code):** (1) **`source_chunks = 0` in live** — 736 chunkable sources (738 web_article + 1 pdf; 38 research_synthesis excluded by design) are unchunked, so ON chat/ask/researcher (which read `source_chunks`) return nothing until the chunk-embedding-worker runs against `openbrain-db`. (2) The 38 live threads are **deep_research-derived** (topic + synthesis + gathered sources), not ON user-notebooks — operator decides whether ON surfaces them as notebooks (map `ob_thread_id`) or keeps its own. **Optional resilience (not done):** fall back to whole-source `match_sources`/content when chunk search is empty, so ON isn't blank during the chunking window. No escalation needed — conforms. | done (conforms); chunking is the functional prerequisite |
| 2026-06-06 | Ask/Search page embedding gate | **Fixed the last stale embedding-model block (operator: "Ask your knowledge base" alerted "no embedding model selected").** The `/search` page gated vector search + Ask on `!!modelDefaults.default_embedding_model` (null in OB1 mode) — same stale gate as Settings. Now `hasEmbeddingModel = default_embedding_model || ob1Config.enabled` (via `useOB1Config`), so OB1's bge-m3 satisfies it. Backend already repointed (interact/Q&A entry). Validated post-rebuild: page 200, OB1 enabled while ON embedding null, and `/search/ask/simple` through the proxy → 3810-char answer cited to the real OB1 source. (Feature intent, for the record: `/search` is global cross-notebook — Search = semantic/keyword lookup over ALL sources; Ask = agentic cited Q&A over the whole knowledge base, distinct from notebook-scoped chat.) | done |
| 2026-06-06 | delete = thread-unlink + URL-PDF title | **Two operator-requested polish items.** (1) **Source "delete" now UNLINKS from the notebook's thread, never globally deletes** (operator: "delete should only refer to this notebook's thread — matches the live OB1 server"). The live OB1 `remove_from_thread` MCP tool only flips `thread_sources.status='inactive'` ("never deletes the source"). Changed `DELETE /sources/{id}` to take an optional `notebook_id`: OB1 + notebook_id → `set_thread_source_status(thread, src, 'inactive')` (leaves just that notebook; canonical row + embedding preserved, re-addable); no notebook_id → new `ob1.unlink_source_all_threads()` (all links inactive, NOT `metadata.deleted`). Dropped the old `soft_delete_source` (global `metadata.deleted`) from this path. Frontend threads the notebook id through (`sources.ts` delete → `useDeleteSource({id,notebookId})` → `SourcesColumn`). This also resolves the earlier "delete didn't work / source re-served" confusion — delete is now a clean thread unlink. (2) **URL-PDF title fix** (operator: temp filename "tmp69g3pdll.10290" instead of the real title). content-core titles a URL-PDF after its temp download file; added `_looks_like_temp_title()` + `_derive_title()` in `graphs/source.py::save_source` — derives the title from the extracted text (first substantial non-boilerplate line + ALL-CAPS continuation join, Title-Cased) then the cleaned URL filename. Validated: the arxiv PDF → "AI Organizations Are More Effective But Less Aligned Than Individual Agents" (was the temp name); also backfilled the operator's existing source. Image rebuilt to bake in all cp'd fixes (url_markdown PDF guard, get_source deleted-filter, delete-unlink, title). | done |
| 2026-06-06 | incident: PDF-URL extraction + chunk-worker poison loop | **Operator hit a "stuck" Open Notebook; suspected the OB1 settings change. Root cause was unrelated to OB1 (OB1 ingestion was fine — `source_count` rose 29→30): a PDF added by URL (`arxiv.org/pdf/2604.10290`) stored 7.2 MB of RAW PDF BYTES as its text.** My earlier URL fast-path (`url_markdown._fetch_html`) did `r.text` with no content-type check, so it decoded the PDF binary as "text" and — being non-empty — never fell back to content-core/docling (file-uploaded PDFs were unaffected; only PDF-as-URL). Two compounding failures: (1) the giant binary `full_text` choked the browser on open ("stuck"); (2) the **chunk worker was poison-pilled** — it re-scanned the un-embeddable binary every cycle, the embed endpoint 500'd on invalid UTF-8 surrogates, and it looped forever hammering bge-m3 (starving real work). Fixes: **(a)** `_fetch_html` now rejects non-HTML content-types (and `%PDF` magic bytes) → `extract_url_markdown` returns None → content-core/docling handles it (validated: same URL → **105 KB clean text** "AI ORGANIZATIONS ARE MORE EFFECTIVE…" vs 7.2 MB bytes). **(b)** Hardened `chunk-embedding-worker`: scan now skips `metadata.deleted` sources, skips binary/non-text content up front (`looksBinary`), and stamps `chunked_hash`+`chunk_error` on content-error embed failures so a bad source can't loop (transient failures still retry). **(c)** Stopped the live loop (retracted the poison OB1 row), deleted the stuck SurrealDB source → notebook back to 0/clean, OB1 to 29 active + 1 retracted. Minor follow-up: content-core titles a URL-PDF with its temp filename (cosmetic). | done |
| 2026-06-06 | podcast UX (page refresh) | **Podcast page now reflects new requests + completions without a manual refresh (operator polish request).** Two compounding causes: (1) the episode row was `save()`d only AFTER the (now multi-minute) researcher briefing, so the post-submit refetch found nothing; (2) with no visible "active" episode, the list's 15s poll never started, so completion was never picked up. Fixes: **backend** — `generate_podcast_command` now saves the episode row EARLY (right after the briefing text, before the content build), then fills in content/audio (double-save), so it appears as `running` within seconds. **Frontend** — `useGeneratePodcast` opens an 8-min grace flag (`podcastGenerationPending` in the query cache); `usePodcastEpisodes.refetchInterval` polls every 5s during that window (bridges the async row-creation gap) and every 8s while any episode is active (brisker than the old 15s), clearing the grace flag once a row is active so polling stops cleanly when done. Validated in `iks-dev`: submit → row visible at **t=3s** as `running` (was 1–2 min); pipeline still completes to audio. | done |
| 2026-06-06 | settings (OB1 masked-password bug + TTS/STT models) | **Two operator-reported fixes.** (1) **OB1 connect failed with the displayed URL** (`postgresql://postgres:********@iks-db…`): the GET embedded the *masked* password in the URL, so Test/Save with an unchanged password sent `********` back and `parse_dsn` took it literally → `InvalidPasswordError`. Fix: `effective_url()` now omits the password entirely (URL = `postgresql://user@host:port/db`; password lives only in its own field), and `parse_dsn()` ignores a `********`/empty password component. Blank password → keeps the existing/env password. Validated: GET url has no password, `overridden:[]`, blank-password test connects (29 sources) after a clean recreate. (Also reset the sandbox's persisted OB1Settings record to fall back to env.) (2) **TTS/STT "not connecting":** operator's combined STT+TTS server (`host.docker.internal:8000/v1`, openedai-speech-style: `/v1/models` → `whisper-medium` + piper voices) had **credentials but no models** in the sandbox ON (separate SurrealDB from live) — that's why the provider rows were empty vs live. Registered the models via API to match live: STT `whisper-medium` (→ stt cred) + TTS `en_US-amy/lessac/ryan-medium`, `en_GB-jenny_dioco/alan-medium` (→ tts cred); set TTS/STT defaults. Both `POST /models/{id}/test` pass ("Speech generation successful" / transcription ran). **NB for podcast AUDIO:** the episode profiles (`outline_llm`/`transcript_llm`) and speaker profiles (per-speaker `voice_model`) are still **unset** (None) → podcast generation will error until a profile is wired (outline/transcript → a language model; voices → the new TTS models). Content path is done; profile model-assignment is the remaining step. | done (OB1 + TTS/STT models); podcast profiles pending |
| 2026-06-06 | settings (OB1 re-scope to connection-only) | **Trimmed the OB1 settings to exactly what the connection requires (operator: "OB1 is a database, not an endpoint with embedding settings; validate what's required and let that drive the options").** Empirically probed `asyncpg` against `iks-db`: a `postgresql://user:password@host:port/database` **URL works**, **password is required** (no/wrong password → `InvalidPasswordError`), and there is **no separate API key** (Postgres auth = user+password). So the OB1 card is now just **Database URL + Password** (the password is the credential; embedding fields removed entirely — OB1 owns none). Backend: added `ob1_repository.parse_dsn()` + `effective_url()`; `OB1Settings` dropped the embedding fields; `GET /settings/ob1` returns `{enabled, url (masked), password_set, overridden}`; PUT parses the URL (+ explicit password), persists ONLY provided fields (env password never baked into SurrealDB), applies + reconnects + tests. Validated: GET masked URL; URL test ok (29 sources); bad scheme → graceful error; PUT url+password → pinned connection + reconnect ok. FE `OB1SettingsCard` simplified to URL+password+Test/Save. **TTS/STT finding:** operator's STT/TTS server (`http://host.docker.internal:8000/v1`, a separate container — NOT OB1) is **reachable from the sandbox** (`curl` → HTTP 200; Docker Desktop resolves `host.docker.internal` with no `extra_hosts`), so network is NOT the blocker for podcast audio — TTS/STT stay ordinary ON model/credential config. | done |
| 2026-06-06 | settings (OB1 pointer + embedding decouple) | **Settings page repaired for the OB1 world (operator: embedding/TTS/STT alerts are stale since ON points at OB1; make the OB1 pointer + TTS/STT user-settable).** Two parts. (1) **Editable OB1 pointer (operator chose editable over read-only):** `ob1_repository` gained a runtime override layer (`set_overrides`/`apply_settings`/`effective_config`/`test_connection`; `_cfg`/`ob1_enabled`/`embed` read override→env) so the knowledge DB + bge-m3 endpoint can be repointed from the UI without redeploying. Persisted via new `OB1Settings` SurrealDB singleton (`domain/ob1_settings.py`), loaded into the override layer at app startup (`main.py` lifespan) so it survives restarts. New endpoints `GET/PUT /settings/ob1` + `POST /settings/ob1/test`; save applies overrides + closes pools (reconnect) + returns a fresh connection test. New FE `OB1SettingsCard` (host/port/db/user/password/embedding base+model+key, Test connection, Save & reconnect, live status). Validated: GET shows effective config (password masked); test ok → 29 sources, schema present; bad host → graceful `ok:false`; PUT override host → saved/overridden/reconnect ok; clear (`""`) → back to env. (2) **Embedding decouple:** the FE hard-coded `default_embedding_model` as `required:true` → the stale "missing required: Embedding" alert (backend already treated it optional). Now `required:!ob1Enabled`; when OB1 is active the selector is replaced by a read-only "Handled by OB1 (bge-m3)" tile, killing the alert. TTS/STT remain optional user-settable selectors (already labeled). | done |

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
