# Promotion Runbook — Integrated Knowledge System

**For the operator.** The implementing agent never runs this (guardrail D3).
Everything below was built and validated in the `iks-dev` sandbox; this
moves it to prod. All schema is **additive** and all removals are **soft**, so
rollback is non-destructive. Work was left **uncommitted** in each repo
(D4) — you review and commit per-repo.

> Order matters: **back up → schema → migrate → rebuild → verify → commit.**

---

## 0. Pre-flight

- [ ] Read the plan §0–§2 and `iks-dev/e2e-results.md`.
- [ ] Confirm the sandbox is down so it doesn't hold ports/volumes:
      `docker compose -p iks-dev -f iks-dev/docker-compose.dev.yml down -v`
- [ ] Confirm `llama-cpp` + `llama-cpp-embed` are healthy (OB1 + ON embed depend on them).

## 1. Back up first (non-negotiable)

- [ ] Dump the live Open Brain DB (includes the new tables — it dumps the whole DB):
      ```powershell
      docker exec openbrain-db pg_dump -U postgres -d openbrain > backups\openbrain-pre-iks.sql
      ```
- [ ] Snapshot the wiki repo working copy (the `open-brain_openbrain-wiki-data` volume) — the Phase-7 inbox writes here:
      ```powershell
      docker run --rm -v open-brain_openbrain-wiki-data:/w -v ${PWD}\backups:/b alpine tar czf /b/wiki-pre-iks.tgz -C /w .
      ```
- [ ] (Optional) Export current SurrealDB ON sources for safety — the migration is additive and leaves them in place, but a dump is cheap.

## 2. Apply schema (idempotent, additive)

The init scripts only auto-run on a **fresh** volume; the live `openbrain-db`
already has data, so apply by hand. `init-threads.sql` is idempotent
(re-runnable) and includes the `content_hash` column add.

- [ ] Copy the new schema into the container and run it:
      ```powershell
      docker cp OB1\docker\init-threads.sql openbrain-db:/tmp/init-threads.sql
      docker exec -e PGPASSWORD=$env:POSTGRES_PASSWORD openbrain-db `
        psql -U postgres -d openbrain -v ON_ERROR_STOP=1 -f /tmp/init-threads.sql
      ```
- [ ] Sanity-check: `\dt` shows `threads, thread_sources, sessions, session_sources`; `\d sources` shows `content_hash`.
- [ ] (Optional backfill) populate `content_hash` for existing sources:
      `UPDATE sources SET content_hash = md5(content) WHERE content_hash IS NULL AND content <> '';`

The prod `OB1/docker/docker-compose.yml` already mounts `init-threads.sql` as
`70-init-threads.sql` (staged), so a future fresh-volume rebuild loads it too.

## 3. Migrate existing ON sources → OB1 (dedup-aware, dry-run first)

Moves SurrealDB `source` rows into OB1 and creates a thread per notebook.
**Additive** — SurrealDB source data is left untouched (rollback-safe).

- [ ] Install deps where you run it: `pip install asyncpg surrealdb`
- [ ] Set env (SURREAL_* point at the live `surrealdb`; OB1_DB_* at `openbrain-db`).
- [ ] **Dry-run** (writes nothing):
      ```powershell
      python iks-dev\migrate-on-sources.py
      ```
      Review the planned threads + source counts.
- [ ] **Apply** (after the dry-run looks right):
      ```powershell
      python iks-dev\migrate-on-sources.py --apply
      ```
      Re-runnable: dedups on url/content_hash, folds onto existing rows.

## 4. Build + swap Open Notebook to the fork

The fork stores SOURCE data in OB1. The new `asyncpg` dependency means the
lockfile must be regenerated before building.

- [ ] Regenerate the lockfile (the host had no `uv`; do it now):
      ```powershell
      cd D:\Open WebUI\open-notebook ; uv lock
      ```
- [ ] Build the fork image:
      ```powershell
      docker build -f Dockerfile.single -t open-notebook-fork:iks D:\Open WebUI\open-notebook
      ```
- [ ] **Stage the compose swap** in `ai-stack/docker-compose.yml`, service
      `open_notebook` (this is the Task 4.6 diff — apply it now):
      ```diff
      -    image: lfnovo/open_notebook:v1-latest
      +    image: open-notebook-fork:iks          # built above (or use build: D:\Open WebUI\open-notebook)
           environment:
             ...
      +      # SOURCE-of-truth -> OB1 Postgres (enables the repoint; ob1_enabled()).
      +      - OB1_DB_HOST=openbrain-db
      +      - OB1_DB_PORT=5432
      +      - OB1_DB_NAME=openbrain
      +      - OB1_DB_USER=postgres
      +      - OB1_DB_PASSWORD=${POSTGRES_PASSWORD}     # from OB1 .env
      +      - EMBEDDING_API_BASE=http://llama-cpp-embed:8080/v1
      +      - EMBEDDING_API_KEY=not-needed
      +      - EMBEDDING_MODEL=bge-m3
      ```
      Note: `open_notebook` must be able to reach `openbrain-db` (obnet) and
      `llama-cpp-embed` (llm-net). Add the networks if not already shared, or
      route via the existing cross-project networking. Until `OB1_DB_HOST` is
      set, the fork stays on the **legacy SurrealDB path** (safe default).
- [ ] Recreate just that service: `docker compose up -d open_notebook` (OB1 must be up + `llama-cpp` healthy first).

## 5. Bring up the suggestion worker + refresh tool surfaces

- [ ] The new `openbrain-suggestion-worker` is already in the OB1 compose;
      bring the OB1 project up to start it:
      `docker compose -f OB1\docker\docker-compose.yml up -d`
- [ ] Trigger a first pass: `curl -X POST http://127.0.0.1:8813/suggest`
- [ ] **mcpo (C3):** the 11 new MCP tools auto-surface — just restart the core
      bridge so it re-reads `tools/list`, then re-import the OpenAPI schema in
      OWUI: `docker restart openbrain-mcpo`. (No `mcpo.config.json` edit; do
      **not** touch `openbrain-mcpo-ext`.)
- [ ] **Gateway (guardrail 5):** confirm `openbrain-gateway/app.py`
      `ALLOWED_TOOLS` is unchanged — the thread/suggestion tools stay **off**
      the cloud allow-list (a code comment documents this). Cloud clients
      cannot see them; that is intended.
- [ ] **OWUI research valve:** to auto-thread research, set the deep-research
      tool's `active_thread_id` valve to a thread UUID (from `create_thread` /
      `list_threads`); empty = unthreaded inbox.

## 6. Verify in prod

- [ ] `docker compose -f OB1\docker\docker-compose.yml ps` → **16** `openbrain-*` containers (was 15).
- [ ] Run `/stack-map`; diff against `iks-dev/baseline-inventory.md` — only `openbrain-suggestion-worker` is new.
- [ ] OWUI research run with a thread set → `sessions` + `thread_sources(automatic)` rows appear.
- [ ] Open a notebook in ON → sources show (cross-tool); upload a source → appears in OB1 `sources` + `thread_sources`.
- [ ] Triage queue shows suggestions; accept/hide/restore behave; "send to Obsidian inbox" writes a stub into the wiki `notes/`.

## 7. Rollback (non-destructive)

All schema is additive; all removals are soft. To roll back:

- [ ] Revert `open_notebook` compose to `image: lfnovo/open_notebook:v1-latest`
      and remove the `OB1_DB_*` env → the fork code path is gated by
      `ob1_enabled()`, so without `OB1_DB_HOST` ON falls back to SurrealDB.
      (If you reverted to the upstream image, it never had the OB1 code at all.)
- [ ] Optionally `docker stop openbrain-suggestion-worker` (leaves data intact).
- [ ] **Leave the new tables in place** (unused) — they harm nothing. The
      migration left SurrealDB sources untouched, so ON is exactly as before.

## 8. Per-repo commit checklist (D4)

The agent staged nothing. Review and commit separately:

- [ ] **ai-stack** (branch `feature/integrated-knowledge-system`): `OB1/docker/init-threads.sql`, `OB1/docker/docker-compose.yml` (init mount + suggestion-worker), `OB1/integrations/kubernetes-deployment/index.ts` (11 tools + persist refactor), `OB1/integrations/suggestion-worker/*`, `openbrain-gateway/app.py` (comment), `smolcrawl/deep_research_tool.py` + `smolcrawl/deep_research/{evidence_memory,models}.py`, `scripts/emergency-recovery.ps1`, `.claude/skills/stack-map/references/workspace-stacks.md`, the `documentation/implementation-guide/.../` docs + `iks-dev/`.
- [ ] **OB1** (branch `feature/integrated-knowledge-system`): if you maintain OB1 as its own repo, the `init-threads.sql` / `index.ts` / `suggestion-worker` / compose changes live there — commit them in that repo, not ai-stack. (They are nested; pick one home and keep it consistent.)
- [ ] **open-notebook** (branch `feature/integrated-knowledge-system`): `pyproject.toml` + `uv.lock`, `open_notebook/database/ob1_repository.py`, `open_notebook/domain/notebook.py`, `open_notebook/utils/obsidian_inbox.py`, `api/routers/triage.py`, `api/main.py`. **Frontend triage components are not built yet** (see §9).

## 9. Known follow-ons (not blocking promotion)

- **ON full source-identity routing (Phase 4.3 remainder):** `Source.get()/delete()/get_insights()` and the source-processing graph still resolve by SurrealDB id. The repoint wires upload + notebook-view; completing single-source-click detail/delete against OB1 UUIDs is the remaining surface (see `iks-dev/on-source-surface.md`). Validate by building `iks-notebook` and exercising the UI.
- **Triage frontend (Phase 6):** the backend API (`/api/triage/*`) is live and validated; the Next.js triage panel/badge component is the remaining UI piece.
- **Suggestion scheduling:** `POST /suggest` is on-demand. Add it to `openbrain-cron`'s HTTP-trigger chain (debounced) when you want it automatic.
- **Embeddings/insights for ON sources:** the repoint stores source rows + bge-m3 embeddings; `source_embedding`/`source_insight` chunking is a follow-on (regenerable, not load-bearing for source-of-truth).
- **Suggestion threshold (open-Q #1):** prod default `0.50`; tune `SUGGESTION_THRESHOLD` against real data.
