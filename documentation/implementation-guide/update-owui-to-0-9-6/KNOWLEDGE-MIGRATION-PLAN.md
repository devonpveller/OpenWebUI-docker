# OWUI knowledge collections → Open Brain — migration plan

Status: **planning**. Runs **before** the 0.9.6 upgrade. **Read-only** against OWUI;
**additive** into OB1; **dedup-safe and re-runnable**.

End state (decided): copy collections into OB1, then **stop using** OWUI knowledge
collections. No OWUI deletion, no OWUI→OB1 retrieval rewire in this plan.

Ingestion is a **cautionary, staged promotion** — nothing is written to OB1 until an
operator has reviewed the filtered manifest.

## Source schema (grounded in the live `webui.db`)

- **`knowledge`** = the collections (snapshot: ~34). Columns: `id`, `name`,
  `description`, `data` (JSON, holds `{"file_ids": [...]}`), `user_id`, timestamps.
- **`file`** = uploaded docs (snapshot: ~2120). Columns: `id`, `filename`,
  `data` (JSON `{"content": "<extracted text>"}`), `meta` (JSON
  `{name, content_type, size, data, collection_name}`), `user_id`, timestamps.
- **Membership** is recoverable two ways: `knowledge.data.file_ids` (authoritative)
  and `file.meta.collection_name`. Use `file_ids` as primary, reconcile with
  `collection_name` to catch orphans.
- The **extracted text is already in `file.data.content`** — Route B needs no
  re-extraction and does not touch OWUI's `vector_db/` at all.

> The numbers above are from a stale snapshot. **Step 1 re-counts against a fresh
> read-only copy of the live DB** — do not hardcode counts.

## Target model in OB1

| OWUI | → | OB1 |
|---|---|---|
| `knowledge` collection | → | `thread` (`create_thread name/description`) |
| `file` | → | `source` (`content_type=manual`, `url=null`) |
| membership | → | `thread_sources` link |
| `file.data.content` | → | `source.content` (chunked+embedded by `openbrain-chunk-worker`) |
| filename / collection | → | `source.metadata` provenance |

Embeddings: OB1 re-embeds with **bge-m3 @ 1024-dim** — the same model OWUI already
uses — so no model mismatch.

Ingest call: **`capture_with_thread`** (one transaction: `find_or_create_source` +
link to thread). It dedups on content-hash, so the same content appearing in two
collections becomes **one source linked to two threads**, and re-runs are no-ops.

## Filter gates (the "cautionary" part)

Applied in Step 2, all configurable; defaults in brackets. Every rejected item is
written to `rejected.csv` with a reason — nothing is dropped silently.

| Gate | Rule | Default | Disposition |
|---|---|---|---|
| **Empty file** | `content` null / whitespace-only | — | reject `empty` |
| **Low-context file** | `len(content) < MIN_CONTENT_CHARS` | 200 chars | reject `low_context` |
| **Empty collection** | 0 surviving files after file-level filtering | — | skip `empty_collection` |
| **Low-volume collection** | surviving files `< MIN_FILES_PER_COLLECTION` | 2 | **flag for manual review** (not auto-dropped) |
| **Duplicate content** | identical `md5(content)` seen already | — | report only; OB1 dedups to one source. Skip re-ingest into the **same** thread. |

`MIN_CONTENT_CHARS` is a floor for "real" RAG material — tune after eyeballing the
length histogram from Step 1. Consider also a token-volume view (chars/4) if you want
to reason in tokens.

## Provenance written on every source

```json
{ "source": "owui-migration",
  "owui_file_id": "<file.id>",
  "owui_collection_id": "<knowledge.id>",
  "owui_collection_name": "<knowledge.name>",
  "owui_content_type": "<file.meta.content_type>",
  "migrated_on": "<operator-supplied date>" }
```
This makes every migrated source traceable back to OWUI and lets a later pass find/
fix/re-dedup the batch. (`content_type` in OB1 is set to `manual`; the original OWUI
type is preserved in metadata. Optionally map `pdf`/`paper` from `meta.content_type`.)

## Procedure

### Step 0 — prerequisites
- A **fresh read-only copy** of the live `webui.db`:
  ```powershell
  docker cp openwebui:/app/backend/data/webui.db .\migration\webui.live.db
  # (or copy from the named volume if the container is down)
  ```
- `MCP_ACCESS_KEY` (the OB1 `x-brain-key`) available to the operator (it's the
  `MCP_ACCESS_KEY` env in `OB1/docker/docker-compose.yml`).
- OB1 stack **up and healthy** (`openbrain-mcp`, `openbrain-chunk-worker`).

### Step 1 — Extract + Audit (read-only)
Run `audit.py` (skeleton below) against the copy. Produces:
- `manifest.csv` — one row per file: `collection_id, collection_name, file_id,
  filename, content_chars, content_md5, owui_content_type`.
- `collections.csv` — per collection: file count, surviving count, total chars.
- `summary.txt` — totals, the **content-length histogram**, duplicate count,
  empty-collection list, low-volume-collection list.

**Operator reviews `summary.txt`** and tunes `MIN_CONTENT_CHARS` / `MIN_FILES_PER_COLLECTION`.

### Step 2 — Filter / Curate
Run `filter.py` to split `manifest.csv` into:
- `promote.csv` — survivors (what will be ingested).
- `rejected.csv` — with reason per row.
- `review.csv` — low-volume collections flagged for a manual keep/drop call.

**Operator edits `promote.csv`** (remove anything unwanted) — this file is the
contract for Step 3. Nothing outside `promote.csv` gets written.

### Step 3 — Promote (dry-run, then apply)
Run `promote.py` on the `ai-stack_llm-net` network so it can reach
`http://openbrain-mcp:8000`:
```powershell
docker run --rm --network ai-stack_llm-net `
  -e MCP_ACCESS_KEY=$env:MCP_ACCESS_KEY `
  -v ${PWD}\migration:/work -w /work python:3.12-slim `
  sh -c "pip install -q requests && python promote.py --input promote.csv --dry-run"
# review the planned thread/source actions, then re-run without --dry-run
```
`promote.py`:
1. Groups `promote.csv` by collection.
2. `list_threads` → reuse a thread if one already carries the
   `owui_collection_id` marker (idempotent); else `create_thread(name, description)`.
3. For each file: `capture_with_thread(thread_id, content, title=filename,
   content_type="manual", metadata_extra={...provenance...})`.
4. Logs `created / linked / deduped / failed` per row to `promote.log`.

### Step 4 — Verify
- For each thread: `get_thread_sources(thread_id)` count == survivors for that
  collection in `promote.csv` (allowing for cross-collection dedup).
- Let `openbrain-chunk-worker` drain (or `POST http://openbrain-chunk-worker:8817/chunks`),
  then spot-check `search` / `match_source_chunks` returns migrated content.
- Record results in `VERIFY-RESULTS.md`. Only after this do we proceed to the upgrade.

## Script skeletons

> Skeletons, not finished tooling — kept deliberately small. `audit.py`/`filter.py`
> are pure-Python stdlib; `promote.py` needs `requests` and the OB1 network.

**`audit.py`** (read-only)
```python
import sqlite3, json, hashlib, csv, collections
db = sqlite3.connect("webui.live.db"); db.row_factory = sqlite3.Row
files = {}
for r in db.execute("SELECT id, filename, data, meta FROM file"):
    content = (json.loads(r["data"] or "{}") or {}).get("content") or ""
    meta = json.loads(r["meta"] or "{}") or {}
    files[r["id"]] = {
        "filename": r["filename"], "chars": len(content.strip()),
        "md5": hashlib.md5(content.encode("utf-8", "ignore")).hexdigest() if content.strip() else "",
        "ctype": meta.get("content_type", ""), "content": content,
    }
rows, percoll = [], collections.defaultdict(list)
for k in db.execute("SELECT id, name, description, data FROM knowledge"):
    fids = (json.loads(k["data"] or "{}") or {}).get("file_ids", []) or []
    for fid in fids:
        f = files.get(fid)
        if not f:  # orphan id in collection
            continue
        rows.append({"collection_id": k["id"], "collection_name": k["name"],
                     "file_id": fid, "filename": f["filename"],
                     "content_chars": f["chars"], "content_md5": f["md5"],
                     "owui_content_type": f["ctype"]})
        percoll = percoll  # see summary below
        perColl = None
# write manifest.csv from `rows`; build histogram + dup counts for summary.txt
with open("manifest.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print(f"{len(rows)} file-links across "
      f"{len({r['collection_id'] for r in rows})} collections")
```

**`filter.py`** (pure transform over `manifest.csv` → `promote.csv` / `rejected.csv` / `review.csv`)
```python
import csv, collections
MIN_CHARS, MIN_FILES = 200, 2
rows = list(csv.DictReader(open("manifest.csv", encoding="utf-8")))
seen, promote, rejected = set(), [], []
by_coll = collections.defaultdict(list)
for r in rows:
    n = int(r["content_chars"])
    if n == 0:                         rejected.append({**r, "reason": "empty"}); continue
    if n < MIN_CHARS:                  rejected.append({**r, "reason": "low_context"}); continue
    dup = r["content_md5"] in seen;    seen.add(r["content_md5"])
    r2 = {**r, "duplicate": dup}; by_coll[r["collection_id"]].append(r2)
review = []
for cid, fs in by_coll.items():
    if len(fs) < MIN_FILES:
        for f in fs: review.append({**f, "reason": "low_volume_collection"})
    promote.extend(fs)              # promote anyway; review.csv is advisory
# write promote.csv / rejected.csv / review.csv
```

**`promote.py`** (calls OB1 MCP `capture_with_thread`)
```python
import csv, os, json, requests
URL = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8000")
KEY = os.environ["MCP_ACCESS_KEY"]
H = {"x-brain-key": KEY, "content-type": "application/json"}
def call(name, args):
    r = requests.post(URL, headers=H, json={"jsonrpc":"2.0","id":1,
        "method":"tools/call","params":{"name":name,"arguments":args}}, timeout=120)
    r.raise_for_status(); return r.json()
# 1) map collection -> thread_id (reuse via list_threads marker, else create_thread)
# 2) for each promote.csv row -> capture_with_thread(thread_id, content=<from webui.db>,
#       title=filename, content_type="manual", metadata_extra={provenance...})
# NB: promote.csv carries ids+filenames; re-open webui.live.db (read-only) to pull content.
```
> `promote.py` re-reads `content` from the read-only `webui.live.db` by `file_id`
> (kept out of the CSVs to avoid giant manifests).

## Sequencing into the upgrade

1. Steps 0–4 here, **fully verified in OB1**.
2. Then run `UPGRADE-PLAN.md`. After upgrade, simply don't use OWUI knowledge —
   §5 of the upgrade plan (KB access enforcement) becomes a non-issue.

## Safety properties

- **No writes to OWUI** at any step.
- **Operator gate** before any OB1 write (`promote.csv` is the contract; `--dry-run` first).
- **Idempotent / dedup-safe:** thread reuse by marker; `find_or_create_source`
  collapses duplicate content; re-runs are no-ops.
- **Traceable:** every source carries `owui_file_id` + collection provenance.
