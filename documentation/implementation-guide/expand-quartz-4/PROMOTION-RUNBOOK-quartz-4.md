# Promotion runbook — Quartz 4 Expansion (live-DB migrations + rollout)

**Audience:** the operator. The coding agent **authors** this; it never runs a
destructive migration against prod (G10). Mirrors the IKS promotion pattern.

The additive migrations are mounted in
[OB1/docker/docker-compose.yml](../../../OB1/docker/docker-compose.yml) at
`/docker-entrypoint-initdb.d` — but **initdb scripts run ONLY on a fresh
`openbrain-db-data` volume**. On the existing live volume they are no-ops, so
they must be applied by hand here (G3). All are additive + idempotent (safe to
re-run).

> **⚠ PROD BASELINE FINDING (2026-06-06, verified read-only against live
> `openbrain-db`):** prod is at the **pre-IKS-Phase-1 baseline**. It has the core
> memory/source/entity schema (`sources` 777, `thoughts` 520, `entities` 25 330,
> `source_entities` 1 935, `thought_entities` 47 108) **but NOT the
> threads/notebooks substrate** — `threads`, `thread_sources`, `sessions`,
> `session_sources`, `sources.content_hash`, and `find_or_create_source` are all
> **absent**. So `init-threads-slug.sql` (step 1 below) would fail immediately
> with `relation "threads" does not exist`. **`init-threads.sql` is therefore a
> required STEP 0** (added below). Roles `service_role` + `authenticated` exist,
> so its RLS grants apply cleanly. Net effect: this promotion also lands **IKS
> Phase-1 schema**, and the **Notebooks feature is net-new to prod** (empty
> `threads` + a **38-notebook** free-text backfill, done by the compiler/workbench
> at rollout, not by SQL).

## 0. Preconditions

- **Run the offline gate first:** `.\scripts\test-quartz4-offline.ps1 -Phase unit`
  (static checks + Node/Deno unit tests + Caddy validate + a throwaway-pgvector
  migration run — touches nothing live). Optionally `-Phase e2e` builds the
  images and smoke-tests an isolated `ob-test` stack with fresh volumes. Both
  must be green before promoting.
- A current **DB backup** (`docker exec openbrain-db pg_dump -U postgres openbrain > backup.sql`).
- Bring changes in together: schema first, then rebuild the images, then the
  compiler/worker pick up the new columns. Code that references the new columns
  (e.g. the `retraction_committed_at` filters) will error if it runs before the
  migration — so **migrate before rebuilding `openbrain-wiki`/`-workbench`/`-entity-worker`.**

## 1. Apply the migrations (in this order)

Order matters: `content_types` drops the CHECK + adds an FK (fails on an
unseeded value), `source_chunks`/`match_sources` reference the retract column,
etc. Run from the repo root:

```powershell
$files = @(
  "init-threads.sql",            # STEP 0 (IKS-P1 prereq, MISSING on prod) — threads,
                                 #   thread_sources, sessions, session_sources,
                                 #   sources.content_hash, find_or_create_source +
                                 #   lifecycle helpers. WITHOUT THIS, step 1 fails.
  "init-threads-slug.sql",       # P2  — threads.slug + uq_threads_slug
  "init-source-revisions.sql",   # P4  — source_revisions
  "init-source-retract.sql",     # P4  — retract cols + match_sources redefine
  "init-content-types.sql",      # P5  — content_types + FK (seed→drop CHECK→FK)
  "init-source-chunks.sql",      # P5  — source_chunks + match_source_chunks
  "init-import-jobs.sql",        # P5  — import_jobs
  "init-source-editing.sql"      # P4.7 — sources.last_edited_by/at (working head)
)
foreach ($f in $files) {
  Write-Host "applying $f"
  Get-Content "OB1/docker/$f" -Raw | docker exec -i openbrain-db psql -U postgres -d openbrain -v ON_ERROR_STOP=1
}
```

**Verify** (should all be ≥1, and the old CHECK gone = 0):

```powershell
docker exec openbrain-db psql -U postgres -d openbrain -tA -c "
SELECT 'threads tbl (STEP 0)',  count(*) FROM information_schema.tables  WHERE table_name='threads'
UNION ALL SELECT 'thread_sources tbl (STEP 0)', count(*) FROM information_schema.tables WHERE table_name='thread_sources'
UNION ALL SELECT 'find_or_create_source (STEP 0)', count(*) FROM pg_proc WHERE proname='find_or_create_source'
UNION ALL SELECT 'threads.slug',          count(*) FROM information_schema.columns WHERE table_name='threads' AND column_name='slug'
UNION ALL SELECT 'source_revisions',         count(*) FROM information_schema.tables  WHERE table_name='source_revisions'
UNION ALL SELECT 'sources.retraction_committed_at', count(*) FROM information_schema.columns WHERE table_name='sources' AND column_name='retraction_committed_at'
UNION ALL SELECT 'content_types rows',        count(*) FROM content_types
UNION ALL SELECT 'content_type FK',           count(*) FROM pg_constraint WHERE conname='sources_content_type_fkey'
UNION ALL SELECT 'old CHECK gone (want 0)',   count(*) FROM pg_constraint WHERE conname='sources_content_type_check'
UNION ALL SELECT 'match_source_chunks',       count(*) FROM pg_proc WHERE proname='match_source_chunks'
UNION ALL SELECT 'import_jobs',               count(*) FROM information_schema.tables WHERE table_name='import_jobs';"
```

## 2. Rebuild + roll out the images

```powershell
docker compose -f OB1/docker/docker-compose.yml build `
  openbrain-wiki openbrain-wiki-viewer openbrain-workbench openbrain-extract openbrain-entity-worker
# Bring OB1 up AFTER the main stack (llama-cpp healthy). New containers:
docker compose -f OB1/docker/docker-compose.yml up -d `
  openbrain-extract openbrain-workbench openbrain-wiki openbrain-wiki-viewer openbrain-entity-worker
```

Set in the **main** stack `.env`: `WORKBENCH_KEY=<same value as OB1 MCP_ACCESS_KEY>`
(Caddy injects it). Reload the portal Caddy:
`docker exec caddy caddy reload --config /etc/caddy/Caddyfile`.

Add the Tailscale `serve` path for `/workbench/*` (mirror the existing wiki serve —
see the `tailscale-serve-restore-recipe` memory).

## 3. Smoke test (the per-phase gates)

- `GET http://127.0.0.1:8814/health` → `{ ok, db:true, rest:true }` (workbench).
- `GET http://127.0.0.1:8815/health` → extractor format list.
- Through the portal: `GET /workbench/health` (Caddy injects the key).
- Trigger a wiki recompile (`POST :8811/recompile`) → confirm `content/notebook/<slug>.md`
  hubs appear, `content/thought|source/*.md` leaves render, citations are wikilinks,
  the old `content/topic/` layer is gone.

## 4. Rollback

The migrations are additive, so the **previous images keep working against the
new schema** (extra columns/tables are ignored). To roll back code: redeploy the
prior images. The new tables/columns can be left in place (harmless) or dropped
manually if required (operator decision; not automated).

## 5. Backups (X.2)

Add the **`wiki-assets`** volume to the backup set (images now, audio later).
Purged sources drop their assets; **retracted sources keep theirs** (restorable).
