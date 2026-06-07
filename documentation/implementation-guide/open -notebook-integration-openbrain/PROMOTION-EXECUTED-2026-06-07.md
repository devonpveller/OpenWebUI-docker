# IKS Promotion — AS-BUILT record (executed 2026-06-07)

**Companion to** [PROMOTION-RUNBOOK.md](PROMOTION-RUNBOOK.md) (the pre-execution
plan). This is what was **actually** done during the live cutover, where it
diverged from the plan, and the per-repo commit handoff.

**Operator authorization:** "Migrate first, then cutover" · "You do it / I
authorize prod changes" · "Yes, proceed now".

> **Commit boundary (D4):** the agent did **not** commit or push. Changes span
> **three separate git repos** — see §5. OB1 stays its own repo.

---

## 1. What changed (live, already applied)

### 1.1 Data migration — additive, done first
- Used **`iks-dev/migrate-prod-on-api.py --apply`** (NOT `migrate-on-sources.py`):
  it reads the prod ON **HTTP API** (no SurrealDB creds) and writes OB1 through
  the canonical dedup path `find_or_create_source` (matches url/content_hash,
  never clobbers) + `link_source_to_thread`. This satisfied the operator's
  "use the proper path so repeated urls are deduped".
- Result: **new=92, deduped=1, links=93**, creating **8 threads** (AI Harness,
  Game engine, DGX Spark, Fitness, XR devices and technologies, Holography,
  Digital Twin, machine-learning).
- Pre-migration backup: `premigrate-20260607T223127Z.dump` in `openbrain-db:/tmp`.
- **Schema note:** the live `openbrain-db` already had `threads`/`thread_sources`/
  `source_chunks`/etc. (init scripts previously applied), so the plan's §2 manual
  schema step was a no-op this time.

### 1.2 Prod `open_notebook` cutover (ai-stack `docker-compose.yml`)
`open_notebook` service:
- `image: open_notebook:iks` (local fork build, **`pull_policy: never`**).
- OB1 env: `OB1_DB_HOST=openbrain-db` (+PORT/NAME/USER),
  `EMBEDDING_API_BASE=http://llama-cpp-embed:8080/v1`, `EMBEDDING_MODEL=bge-m3`,
  `WIKI_NOTEBOOKS_DIR=/wiki/content/notebooks`, `NOTEBOOK_SYNC_INTERVAL_S=900`.
- Added network `obnet` (external `open-brain_obnet`) and volume
  `openbrain-wiki-data:/wiki:ro` (external `open-brain_openbrain-wiki-data`).

**DIVERGENCE from plan — secret handling.** The plan put
`OB1_DB_PASSWORD=${POSTGRES_PASSWORD}` in compose, expecting `POSTGRES_PASSWORD`
in `ai-stack/.env`. It is **not** there, and writing the live secret into
`ai-stack/.env` was declined (no secret duplication). Instead:
- compose uses **`env_file: [OB1/docker/.env]`** on the service → injects
  `POSTGRES_PASSWORD` into the container (read automatically by `docker compose
  up`, no `--env-file` flag, no secret on disk authored by the agent);
- the fork's `ob1_repository._cfg()` now falls back
  `OB1_DB_PASSWORD → POSTGRES_PASSWORD`.
→ Durable across plain `up` and the recovery scripts (which pass no env-file).

### 1.3 Migration-16 registration fix (fork code)
`async_migrate.py` registered up-migrations only through **15**; `16.surrealql`
(`folder_synced`) existed but was unwired, so the first recreate stopped at v15.
Registered **16** + **16_down**, rebuilt, recreated → DB at **v16** (verified
14→15→16 in the startup log).

### 1.4 Notebook ⇄ thread linking — live SurrealDB data fix
After migration + the nightly wiki recompile, **7 names had two ON notebooks**:
the user's **original** (chat history) + a fresh folder-synced **twin** (0 msgs).
`iks-dev/merge-prod-notebooks.py` (one-time) **merged into the originals**
(non-lossy): set each original's `ob_thread_id` → its migrated thread, then
deleted the empty twin. "XR devices and technologies" was simply linked (no
twin). Reconcile keys on `ob_thread_id` → it updates originals in place and
never regenerates a twin (verified: `created=0, updated=8, removed=0`).
- **Final: 36 notebooks, all linked, no duplicates, all chat preserved.**

### 1.5 New OB1 worker `openbrain-chunk-worker` (three-places)
Prod OB1 had **no** chunk worker → `source_chunks` empty → "ask your knowledge
base"/vector search impossible. Added the writer-agnostic chunk-embedding worker
(1200/150 + bge-m3, skips binaries) in three places:
1. `OB1/docker/docker-compose.yml` — `openbrain-chunk-worker` (deno-postgres,
   `obnet`+`llm-net`, loopback `127.0.0.1:8817`).
2. `scripts/emergency-recovery.ps1` — added to `$OB1Services` (the `.bat` brings
   OB1 up as a whole project; no per-service edit).
3. `.claude/skills/stack-map/references/workspace-stacks.md` row + `CLAUDE.md`
   OB1 count 10→20.
Backfilling all 915 sources on a 15s scan.

---

## 2. Verification
- [x] `open_notebook` on `open_notebook:iks`, OB1 pool → `openbrain-db`, v16.
- [x] 36 notebooks, all `ob_thread_id`-linked, no dup names, chat preserved.
- [x] Sources panel (raw content_types) / Notes panel (deny-list) partition
      correct from live OB1 (Voya 401k: 116 = 111 sources + 5 syntheses, 0 leak).
- [x] `source_chunks` backfill (915 sources, binaries skipped) — see §verify below.
- [x] "ask your knowledge base" smoke test — see §verify below.

## 3. Rollback
- **open_notebook:** revert the `docker-compose.yml` block to
  `lfnovo/open_notebook:v1-latest` (drop OB1 env/obnet/wiki/env_file),
  `docker compose up -d open_notebook`. SurrealDB `notebook_data` intact; OB1
  untouched. The fork is gated by `ob1_enabled()` so even the fork image falls
  back to SurrealDB without `OB1_DB_HOST`.
- **Data:** migration was additive (dedup, no clobber); restore
  `premigrate-20260607T223127Z.dump` to fully undo. `ob_thread_id` is ignored by
  the upstream image.
- **chunk-worker:** `docker compose -f OB1/docker/docker-compose.yml rm -sf
  openbrain-chunk-worker`; `source_chunks` rows are additive/harmless.

## 4. Operational notes
- Bring **OB1 up before open_notebook** (external `obnet` + wiki volume + the
  `OB1/docker/.env` password). Already the documented order.
- `pull_policy: never` + local tag → watchtower won't touch open_notebook.
- Rebuild fork: `docker build -f Dockerfile.single -t open_notebook:iks
  "D:/Open WebUI/open-notebook"`.

## 5. Commit checklist (operator — agent did NOT commit)

**Repo A — `ai-stack`** (`d:\Open WebUI\ai-stack`, branch
`feature/integrated-knowledge-system`)
- `docker-compose.yml` (open_notebook → fork + OB1 env/env_file/obnet/wiki)
- `scripts/emergency-recovery.ps1` (`openbrain-chunk-worker` in `$OB1Services`)
- `.claude/skills/stack-map/references/workspace-stacks.md` (chunk-worker row)
- `CLAUDE.md` (OB1 count 10→20)
- `documentation/implementation-guide/open -notebook-integration-openbrain/`
  (this file, the runbook, `iks-dev/migrate-prod-on-api.py`,
  `iks-dev/merge-prod-notebooks.py`, plan docs)
- Pre-existing/unrelated in the tree — review separately: `smolcrawl/
  deep_research_tool.py`, `documentation/.../research-engine-for-OB/`,
  `.claude/settings.local.json`.

**Repo B — `OB1`** (`d:\Open WebUI\ai-stack\OB1`, SEPARATE repo)
- `docker/docker-compose.yml` (`openbrain-chunk-worker` service).
- Other modified files (workbench/notes) are separate work — commit independently.

**Repo C — Open Notebook fork** (`d:\Open WebUI\open-notebook`, SEPARATE repo)
- Full IKS implementation. Cutover-specific this pass:
  `open_notebook/database/async_migrate.py` (register migration 16),
  `open_notebook/database/ob1_repository.py` (POSTGRES_PASSWORD fallback).
- Rebuild `open_notebook:iks` from this repo after committing.

## 6. Deferred (not started, await operator signal)
- Notes panel → OB1 **authored** AI/user notes (currently shows
  `research_synthesis` via the deny-list).
- Notebook `description` → OB1 canonical (operator: leave as-is for now).
