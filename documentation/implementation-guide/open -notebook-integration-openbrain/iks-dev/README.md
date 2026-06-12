# iks-dev — Integrated Knowledge System sandbox

A **disposable** Docker Compose project (`iks-dev`) that mirrors the real
topology — OB1 Postgres + MCP server + SurrealDB + the Open Notebook fork
— so the integration can be built and validated **without touching prod**.

> ⚠️ **Guardrail 1 (plan §2): this never touches prod.** No service here
> mounts `openbrain-db-data` or `open-notebook/surreal_data`; every volume
> is a fresh `iks-*` named volume. The init SQL is mounted **read-only**
> from `OB1/docker`. Models are reached read-only for inference only.

## Bring it up / down

```bash
# from this directory:
docker compose -p iks-dev -f docker-compose.dev.yml up -d            # everything
docker compose -p iks-dev -f docker-compose.dev.yml up -d iks-db     # just the DB (fast; no builds)

docker compose -p iks-dev -f docker-compose.dev.yml down             # stop, keep volumes
docker compose -p iks-dev -f docker-compose.dev.yml down -v          # stop + WIPE scratch data
```

`iks-mcp` and `iks-notebook` are **built from source** (the OB1 MCP server
and the Open Notebook fork) — the first build is slow (Next.js + Python +
Deno). `iks-db` is a plain image pull and starts in seconds, which is all
you need for Phase 1 schema work.

## Ports (all loopback; 18xxx to avoid prod/fork collisions)

| Service | Host | Container | Notes |
|---------|------|-----------|-------|
| iks-db | `127.0.0.1:18432` | 5432 | Postgres + pgvector (scratch) |
| iks-mcp | `127.0.0.1:18000` | 8000 | OB1 MCP server (HTTP/MCP) |
| iks-surreal | `127.0.0.1:18003` | 8000 | SurrealDB (ON operational state) |
| iks-notebook | `127.0.0.1:18502` | 8502 | Open Notebook web UI |
| iks-notebook | `127.0.0.1:15055` | 5055 | Open Notebook REST API |
| iks-suggestion-worker | `127.0.0.1:18810` | 8000 | Phase 5 (commented until built) |

Sandbox secrets are **non-secret throwaway values** (DB password
`iks_dev_only`, MCP key `iks-dev-key`) — intentionally inline so the
sandbox needs no `.env`.

## Connecting

```bash
# psql into the scratch DB
docker exec -e PGPASSWORD=iks_dev_only iks-db psql -U postgres -d openbrain

# from the host (e.g. a GUI), use 127.0.0.1:18432 / postgres / iks_dev_only

# MCP tools/list (once iks-mcp is up)
curl -s -X POST http://127.0.0.1:18000/ -H 'x-brain-key: iks-dev-key' \
  -H 'content-type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## What's loaded

On a fresh volume the DB auto-runs, in order: the real
`init.sql → init-extensions → init-sources → init-graph → init-grants →
init-source-graph` (read-only mounts), then **`70-init-threads.sql`** (the
Phase-1 net-new schema), then **`80-seed.sql`** (3 threads, 15 sources,
2 sessions — synthetic; see `seed.sql`).

Seed sources have **NULL embeddings** (SQL can't call bge-m3). Before
validating the Phase-5 suggestion worker, backfill them with
`embed-seed.ps1` (added in Phase 5), which calls the live `llama-cpp-embed`
model and `UPDATE`s `sources.embedding`.

## Files

| File | Purpose |
|------|---------|
| `docker-compose.dev.yml` | the sandbox project (Task 0.2) |
| `seed.sql` | synthetic seed data (Task 0.3) |
| `baseline-inventory.md` | pre-work container inventory for drift (Task 0.5) |
| `on-source-surface.md` | ON source call-site inventory (Phase 4.1) |
| `migrate-on-sources.*` | SurrealDB→OB1 source migration, dry-run (Phase 4.5) |
| `embed-seed.ps1` | seed embedding backfill (Phase 5) |
| `e2e-results.md` | end-to-end scenario results (Phase 8.1) |

## Validated so far

- **Phase 1 (DoD met, 2026-06-02):** all init scripts load clean;
  `init-threads.sql` re-run is idempotent (no errors); `find_or_create_source`
  dedups (same id, `was_duplicate=true`, one row); a source links to two
  threads (both `confirmed`); `set_thread_source_status` soft-unlink →
  `inactive` (row preserved); `pending→confirmed` sets `confirmed_at`.
