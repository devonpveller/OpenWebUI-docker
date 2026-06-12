# OWUI knowledge collections → Open Brain — migration plan

Status: **planning**. Runs **before** the 0.9.6 upgrade. **Read-only** against OWUI;
**additive** into OB1; **dedup-safe and re-runnable**.

End state (decided): copy collections into OB1, then **stop using** OWUI knowledge
collections. No OWUI deletion, no OWUI→OB1 retrieval rewire in this plan.

Ingestion is a **cautionary, staged promotion** — nothing is written to OB1 until an
operator has reviewed the filtered manifest.

## Source schema (verified against the live `webui.db`, 2026-06-12)

- **`knowledge`** = the collections (live: **40**). Columns: `id`, `name`,
  `description`, `data`, `user_id`, timestamps.
- **`file`** = uploaded docs (live: **7645** total). Columns: `id`, `filename`,
  `data` (JSON `{"content": "<extracted text>"}`), `meta` (JSON
  `{name, content_type, size, data, collection_name}`), `user_id`, timestamps.
- **Membership — IMPORTANT, differs from the stale snapshot:** in the live DB
  `knowledge.data` is **empty (`{}`)**; there is **no `file_ids` list**. A file
  belongs to a collection iff **`file.meta.collection_name == knowledge.id`**.
  (The audit script was corrected to use this linkage.)
- **Noise filter:** there are ~1100 distinct `collection_name` values but only 40
  knowledge collections — the other ~1060 are **per-chat file uploads** (transient
  collections), correctly excluded by requiring `collection_name ∈ knowledge.id`.
  After linkage: **6327 files across the 40 collections**; 1066 chat-upload files
  excluded.
- The **extracted text is already in `file.data.content`** — Route B needs no
  re-extraction and does not touch OWUI's `vector_db/` at all.

### Origin classification (the curation axis)

Collections fall into three origins by `knowledge.description`:

| origin | rule | live counts | meaning |
|---|---|---|---|
| `smolcrawl` | desc starts `Auto-synced by SmolCrawl` | 7 colls / 4731 files / 106M chars | reproducible web-scrape mirrors (GitHub/NVIDIA/Tauri docs) |
| `appsync` | desc contains `Auto-synced` | 1 coll / 1131 files | GAPS Task Manager auto-sync |
| `authored` | everything else | 25 colls / 93 files / 168M chars | human-curated knowledge |

**Decision (2026-06-12): scope = `authored` only.** The SmolCrawl mirrors are
reproducible and would bloat OB1; GAPS-app is an app sync best handled at its
source. `filter.py --origins=authored` enforces this.

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
`promote.py` is a **stdlib-only MCP client** (no `pip`): it does the Streamable-HTTP
handshake (`initialize` → `notifications/initialized` → `tools/call`), sends
`accept: application/json, text/event-stream`, and parses the `result.content[0].text`
envelope (and SSE). Run it on `ai-stack_llm-net` so `http://openbrain-mcp:8000` resolves.
The MCP key is the `MCP_ACCESS_KEY` baked into the `openbrain-mcp` container.

```powershell
$dir = "<...>\migration"
$key = (docker exec openbrain-mcp printenv MCP_ACCESS_KEY).Trim()
# dry-run is OFFLINE (no network); review the plan first:
docker run --rm -v "${dir}:/work" -w /work python:3.12-slim python promote.py --dry-run
# then apply (real writes to OB1):
docker run --rm --network ai-stack_llm-net -e MCP_ACCESS_KEY="$key" `
  -v "${dir}:/work" -w /work python:3.12-slim python promote.py
```
Behaviour: groups `promote.csv` by collection; **reuse-or-create** a thread keyed by
an `[owui_collection_id:<id>]` marker in the thread description (idempotent); then
`capture_with_thread(thread_id, content=<read from webui.live.db by file_id>,
title=filename, content_type="manual", metadata_extra={provenance})` per file.
Every row is logged to `promote.log`; **re-runs skip file_ids already logged ok**, so
it is fully resumable.

### Step 4 — Verify
- `verify.py` lists the marker threads + `source_count` and totals them.
- DB-level proof (the real check): `source_chunks` exist and are embedded for the
  migrated sources — confirms full-document embedding regardless of the coarse
  source vector. (Don't rely on the `search` MCP tool for this — see the known
  BigInt bug in the run log below.)

## Scripts (live, in `migration/`)

The skeletons were replaced by working scripts during the run. All stdlib-only;
container runs go through **PowerShell** (the Bash tool mangles Windows `-v` mounts):

| script | role |
|---|---|
| `audit.py` | read-only; manifest.csv / collections.csv / orphans.csv / summary.txt; linkage = `meta.collection_name==knowledge.id`; tags `origin` |
| `filter.py` | gates → promote.csv / rejected.csv / review.csv; `--origins=authored[,appsync,smolcrawl]` `[MIN_CHARS] [MIN_FILES]` |
| `promote.py` | stdlib MCP client; `--dry-run` (offline) / `--check` / apply; resumable via promote.log |
| `verify.py` | marker-thread source counts + (buggy) search spot-check |

`webui.live.db`, all `*.csv`, `*.log`, `summary.txt` are gitignored (size / churn).

## ACTUAL RUN — 2026-06-12 (scope A, authored)

**Result: complete & verified — 25 threads, 93 sources, 19,624 chunks (all embedded).**

Pipeline numbers (MIN_CONTENT_CHARS=200):
- audit: 6327 file-links / 40 collections (1066 chat-upload files excluded).
- filter `--origins=authored`: **93 promoted / 25 collections**; rejected 6234
  (4766 smolcrawl, 1468 appsync, plus empty/low_context/dup within authored).
- 7 empty collections + 13 single-file collections handled (empties dropped, singles
  flagged in review.csv but kept).
- promote: 25 threads created, 93 sources, 0 dedup, **0 failed** (after the fix below).

### Deviation: embedding-size failure + fix (44 of 93 failed on first apply)

`llama-cpp-embed` has a **512-token physical batch** and hard-rejects any single
input above it (`input (575 tokens) is too large ... batch size: 512`). OB1's
`getEmbedding()` ([OB1/integrations/kubernetes-deployment/index.ts](../../../OB1/integrations/kubernetes-deployment/index.ts))
sent the **whole document** as one input, so 44 larger sources failed on the first run.

**Fix (durable, zero-GPU):** truncate the embedding input to a bounded prefix
(`MAX_EMBED_CHARS=1500`, halve-and-retry on "too large"). This loses **no retrievable
information** — full text is stored in `source.content` and the chunk-worker embeds
every 1200-char chunk (19,624 of them here), so deep retrieval covers the whole
document; only the coarse source-level vector is prefix-bounded. Raising the embed
batch was rejected — the embed GPU (2080) is maxed until a hardware upgrade.

Applied as: repo edit (durable) → `docker cp index.ts openbrain-mcp:/app/index.ts`
→ `docker restart openbrain-mcp` (interpreted `deno run`, so no image rebuild needed
to go live). Re-ran `promote.py` → the 44 completed, 49 skipped via resume.

> **Durability caveat:** the running container has the fix via `docker cp`, and the
> repo source has it, but the **`openbrain-mcp-server:local` image does not** until
> rebuilt (`docker compose -f OB1/docker/docker-compose.yml up -d --build openbrain-mcp`).
> Until that rebuild, a `--force-recreate` / recovery-script restart reverts the fix.

> **Found (not fixed):** the `search` MCP tool errors `Do not know how to serialize a
> BigInt` — a pre-existing serialization bug in the thoughts-search path, unrelated to
> this migration. Logged for a later OB1 fix.

## Sequencing into the upgrade

1. Migration **done & verified** (above).
2. Then run `UPGRADE-PLAN.md`. After upgrade, simply don't use OWUI knowledge —
   §5 of the upgrade plan (KB access enforcement) becomes a non-issue.

## Safety properties

- **No writes to OWUI** at any step.
- **Operator gate** before any OB1 write (`promote.csv` is the contract; `--dry-run` first).
- **Idempotent / dedup-safe:** thread reuse by marker; `find_or_create_source`
  collapses duplicate content; re-runs are no-ops.
- **Traceable:** every source carries `owui_file_id` + collection provenance.
