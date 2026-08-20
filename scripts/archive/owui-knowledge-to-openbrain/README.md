# OWUI knowledge → Open Brain migration tool

A small, re-runnable, **stdlib-only** pipeline that copies **Open WebUI knowledge
collections** into **Open Brain (OB1)** as `threads` + `sources`.

- **Entry point:** a read-only copy of *any* OWUI version's `webui.db`.
- **Endpoint:** always Open Brain, via the `openbrain-mcp` Streamable-HTTP server.
- **Read-only on OWUI, additive + idempotent into OB1** — safe to run and re-run.

> History: this tool was first built for the 0.8.10 → 0.9.6 upgrade (read the
> collections against the known-good schema *before* touching the OWUI version).
> It has since been generalized — nothing here is specific to that upgrade. The
> upgrade write-up lives in
> [`documentation/implementation-guide/update-owui-to-0-9-6/`](../../documentation/implementation-guide/update-owui-to-0-9-6/).

## The four steps

| Step | Script | Reads | Writes | Network |
|------|--------|-------|--------|---------|
| 1. Audit   | `audit.py`   | OWUI `webui.db` (copy) | `manifest.csv`, `collections.csv`, `orphans.csv`, `summary.txt` | none |
| 2. Filter  | `filter.py`  | `manifest.csv` | `promote.csv` (the **contract**), `rejected.csv`, `review.csv` | none |
| 3. Promote | `promote.py` | `promote.csv` + `webui.db` | `promote.log` | OB1 (MCP) |
| 4. Verify  | `verify.py`  | OB1 | stdout | OB1 (MCP) |

`promote.csv` is the human-editable contract between filtering and writing —
delete rows to drop more before promoting. `promote.log` makes step 3 resumable
(a re-run skips `file_id`s already logged `ok`; OB1 content-hash dedup is the
backstop).

## Run it

All scripts default to a `webui.db` in the working directory and write their CSVs
there too, so run them from a scratch dir. **Take a copy of the live DB first** —
never point these at a `webui.db` a running container holds open.

```powershell
# 0. Get a read-only copy of the OWUI db out of the running container
docker cp openwebui:/app/backend/data/webui.db ./webui.db

# 1. Audit (offline) — inspect summary.txt before going further
python audit.py ./webui.db

# 2. Filter (offline). Defaults: MIN_CONTENT_CHARS=200, MIN_FILES=2.
#    Positional overrides + an --origins filter are supported.
python filter.py 200 2
#    -> review promote.csv; hand-delete any rows you don't want

# 3. Promote into OB1. Needs the openbrain-mcp network + a brain key.
#    Dry-run first (offline plan), then apply on ai-stack_llm-net.
python promote.py --dry-run
docker run --rm --network ai-stack_llm-net -e MCP_ACCESS_KEY=$key `
  -v "${PWD}:/work" -w /work python:3.12-slim python promote.py

# 4. Verify what landed
docker run --rm --network ai-stack_llm-net -e MCP_ACCESS_KEY=$key `
  -v "${PWD}:/work" -w /work python:3.12-slim python verify.py
```

### Knobs (env / args)

- `WEBUI_DB` — path to the OWUI db copy (or `audit.py`'s argv[1]).
- `filter.py [MIN_CONTENT_CHARS] [MIN_FILES] [--origins=authored,appsync,smolcrawl]`
  — gating thresholds + which source origins to keep.
- `OPENBRAIN_MCP_URL` (default `http://openbrain-mcp:8000`), `MCP_ACCESS_KEY`.
- `MIGRATED_ON` — stamped into each source's metadata (defaults to today).

## Notes for future coders

- **Idempotency is by marker, not by run.** Each created OB1 thread carries an
  `[owui_collection_id:<id>]` marker in its description; re-runs reuse the thread
  instead of duplicating it. Don't strip that marker.
- **Linkage model (0.8.10 / 0.9.6 schema):** `knowledge.data` is empty on the
  live DB; collection membership is `file.meta.collection_name == knowledge.id`.
  The ~1000 other `collection_name` values are per-chat file uploads, **not**
  curated knowledge — `audit.py` deliberately ignores them. If a future OWUI
  version moves membership back into `knowledge.data`, that join in `audit.py`
  (and `content_for` in `promote.py`) is the thing to update.
- **Cross-collection duplicates are kept on purpose.** OB1's
  `find_or_create_source` collapses identical content to one source and just adds
  the second thread link, so each `promote.csv` row maps to exactly one
  `capture_with_thread` call. Only *within*-collection dups are filtered.
- **Pure stdlib, no pip** — runs in a bare `python:3.12-slim` on the
  `ai-stack_llm-net` network. Keep it that way so it has no build step.
- The MCP client speaks the `2025-06-18` protocol and parses both JSON and
  `text/event-stream` envelopes — see `_rpc` in `promote.py`/`verify.py`.
