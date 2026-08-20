# End-to-end validation results (Task 8.1)

Run in the `iks-dev` sandbox against a live `pgvector/pgvector:pg16`
(`iks-db`), the built OB1 MCP server (`iks-mcp`), and the built suggestion
worker (`iks-suggestion-worker`). Date: 2026-06-02. Seed baseline restored to
**3 threads / 15 sources / 15 links** after each test block.

| Concept §6 scenario | Validated via | Result |
|---------------------|---------------|--------|
| **6.1 OWUI research session** | `POST /research/persist` on `iks-mcp` | ✅ persist **with** `thread_id` → 1 `session` + N `session_sources` + N `thread_sources(automatic,confirmed)`; **without** `thread_id` → session only (inbox), no thread link. Synthesis upserts in place (1 row/key). |
| **C1 (durable sources)** | re-run same `research_key` | ✅ source `id`s **stable** across re-run; existing `thread_sources`/`session_sources` preserved; content updated in place. The old hard-DELETE is gone. |
| **6.2 ON source upload** | `ob1_repository.capture_source_into_thread` vs `iks-db` | ✅ upload → `sources` row + `thread_sources(automatic,confirmed)`; re-upload same url → `was_duplicate=true`, **same id** (dedup), still linked. |
| **6.3 ON interaction / cross-tool visibility** | `ob1_repository.get_thread_sources` | ✅ a thread's confirmed sources return from **all** ingestion points (the seed's OWUI/manual sources read back through the ON module); confirmed-only filter holds. |
| **6.4 cross-thread suggestion → triage** | `iks-suggestion-worker` `POST /suggest` + MCP/`ob1_repository` triage | ✅ overlapping LLM↔vector threads → 9 `suggested/pending` rows w/ reasons (embedding-topic sources score highest, 0.67); **network thread got 0** (negative control). All §4.3 transitions: pending→hidden→pool→restore→pending→accept→**confirmed appears in thread view**→remove→inactive. Hidden pair **not re-suggested**. Threshold env-tunable (0.50→9, 0.66→2) + logged. |
| **6.5 Obsidian note writing** | `obsidian_inbox.write_inbox_stub` | ✅ stub `.md` written into `notes/` (user folder), **not** `content/`; has draft marker + `type: inbox-stub` frontmatter; `content/` untouched; **no `sources` row created**. |

## MCP surface

`tools/list` on `iks-mcp` returns **19** tools (8 original + 11 new). All 11
exercised: `create_thread`, `list_threads`, `get_thread_sources`,
`add_to_thread`, `remove_from_thread`, `get_suggestions`, `accept_suggestion`,
`hide_suggestion`, `get_hidden_suggestions`, `restore_suggestion`,
`capture_with_thread` (dedup + auto/confirmed link + optional session).

## Schema (Phase 1)

`init-threads.sql` loads clean alongside the real init scripts; re-run is
idempotent; `find_or_create_source` dedups; lifecycle helpers flip status
(soft) and never delete. No `DROP/TRUNCATE`/unqualified `DELETE` in the file.

## Three-places drift (Task 8.2)

`openbrain-suggestion-worker` present in all three: OB1 compose (16 services),
`emergency-recovery.ps1` inventory (16), stack-map reference. `/stack-map`
baseline for prod comparison is `baseline-inventory.md` (15 → 16 post-promote).

## Not validated here (needs the heavy `iks-notebook` build — operator/Phase 8)

- The Open Notebook **UI** end-to-end (upload click → OB1, notebook view
  render, triage panel). The data layer (`ob1_repository`) and API
  (`/api/triage/*`) are validated standalone against `iks-db`; the Next.js
  frontend + single-source-click identity routing are the remaining UI work
  (see `on-source-surface.md` "minimal repoint surface" and the runbook §9).
- The fork image build itself (requires `uv lock` for the new `asyncpg` dep).

## Backup coverage (Task 8.4)

The live `openbrain-db` backup is a **whole-database** `pg_dump`
(`backup/Dockerfile.postgres` / `openbrain-db-backup`), so the new tables
(`threads`, `thread_sources`, `sessions`, `session_sources`) and the
`sources.content_hash` column are captured automatically — **verified by
inspection**: the dump is not table-scoped. No backup change needed. The wiki
`notes/` inbox stubs are covered by the existing wiki-repo git sync +
`openbrain-wiki-backup`.
