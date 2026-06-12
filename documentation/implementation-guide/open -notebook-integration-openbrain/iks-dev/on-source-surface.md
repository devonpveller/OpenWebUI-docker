# Open Notebook source surface inventory (Task 4.1)

Every read/write of **source** data in the fork (`d:\Open WebUI\open-notebook`),
so the storage can be repointed to OB1 Postgres with minimal UI change.
Operational state (notes, chat, jobs, transformations, model config) **stays
on SurrealDB**.

## Architecture (real layout, audit C2)

- **Domain models** (`open_notebook/domain/`): `Source`, `SourceEmbedding`,
  `SourceInsight`, `Notebook` in `notebook.py`; `ObjectModel` base in `base.py`.
- **DB driver** (`open_notebook/database/repository.py`): `repo_query`,
  `repo_create`, `repo_update`, `repo_upsert`, `repo_insert`, `repo_delete`,
  `repo_relate` — all SurrealDB.
- **Service + routers** (`api/`): `sources_service.py`, `routers/sources.py`,
  `routers/notebooks.py`, `notebook_service.py`.
- **Ingestion graph** (`open_notebook/graphs/source.py`) + async
  `commands/source_commands.py`, `commands/embedding_commands.py`.

## SurrealDB relationship model

```surrealql
DEFINE TABLE reference TYPE RELATION FROM source TO notebook;   -- notebook<->source
DEFINE TABLE artifact  TYPE RELATION FROM note   TO notebook;   -- notebook<->note
```
A source belongs to a notebook via the `reference` edge. **In the repoint
this edge is replaced by OB1 `thread_sources`** (notebook ⇄ thread 1:1).

## Source READ/WRITE call sites

| File:line | R/W | Table/relation | What |
|-----------|-----|----------------|------|
| `domain/notebook.py:29` `Notebook.get_sources()` | R | `reference`→`source` | **List a notebook's sources** → repoint to OB1 `get_thread_sources(thread_id)` |
| `domain/notebook.py:318/332` `Source.get_status/progress` | R | `command` | job status (stays Surreal) |
| `domain/notebook.py:376` `get_embedded_chunks()` | R | `source_embedding` | chunk count |
| `domain/notebook.py:392` `get_insights()` | R | `source_insight` | source insights |
| `domain/notebook.py:406` `Source.add_to_notebook()` | W | `reference` | **link source→notebook** → repoint to OB1 `link_source_to_thread` |
| `domain/notebook.py:411` `vectorize()` | job | — | submits `embed_source` command |
| `domain/notebook.py:516` `Source.delete()` | W | `source`,`source_embedding`,`source_insight` | cascade delete → OB1 soft-status |
| `domain/base.py:save/get/get_all/delete` | R/W | `source` (+others) | generic CRUD; route source-family to OB1 |
| `api/routers/sources.py:162-224` `GET /sources` | R | `source`,`reference` | list (all / by notebook) → OB1 |
| `api/routers/sources.py:289-551` `POST /sources` | W | `source`,`reference`,`command` | **upload** → OB1 find_or_create + thread link |
| `api/routers/sources.py:631-684` `GET /sources/{id}` | R | `source`,`reference` | detail + notebooks |
| `api/routers/sources.py:779` `PUT /sources/{id}` | W | `source` | update title/topics → OB1 |
| `api/routers/sources.py:946` `DELETE /sources/{id}` | W | `source`+children | delete → OB1 soft-status |
| `api/routers/notebooks.py:53/150` list/get notebook | R | `reference` count | source_count per notebook → OB1 count |
| `api/routers/notebooks.py:245` add_source | W | `reference` | → OB1 `add_to_thread` |
| `commands/source_commands.py` `process_source` | W | `source`,`command` | pipeline persists Source |
| `graphs/source.py:97-127` `save_source()` | W | `source` | final Source write after extraction |
| `commands/embedding_commands.py:404` `embed_source` | W | `source_embedding` | bulk chunk insert → OB1 |
| `commands/embedding_commands.py:484` `create_insight` | W | `source_insight` | insight create → OB1 |

## Stays on SurrealDB (operational state — Task 4.4)

`note`, `artifact` (note↔notebook), `chat_session`, `refers_to`, `command`
(job queue), `transformation`, `model`, `credential`, `podcast_config`, UI
prefs. Notebook records themselves stay in SurrealDB **plus a new
`ob_thread_id` field** holding the 1:1 OB1 thread id.

## Minimal repoint surface (what we actually swap)

The cleanest chokepoint is a small **OB1 data-access module**
(`open_notebook/database/ob1_repository.py`, Task 4.2) plus rewiring **four**
domain/service touchpoints (Task 4.3):

1. **Notebook ⇄ thread (1:1):** on notebook create, create an OB1 thread and
   store its id on the notebook (`ob_thread_id`). Helper:
   `ob1_ensure_thread_for_notebook(notebook)`.
2. **`Notebook.get_sources()`** → `ob1_get_thread_sources(thread_id)`
   (confirmed links) — this is what gives cross-tool visibility (OWUI/manual
   sources appear here).
3. **Source upload (`POST /sources` / `save_source`)** →
   `ob1_find_or_create_source(...)` + `ob1_link_source_to_thread(thread_id,
   id, 'automatic','confirmed')` in one call; dedup notice on duplicate.
4. **`Source.add_to_notebook()` / notebooks add_source** →
   `ob1_link_source_to_thread(... 'deliberate' ...)`.

Embeddings/insights (`source_embedding`/`source_insight`) can either move to
OB1 too or stay Surreal short-term; the **DoD only requires source rows +
thread links + cross-tool visibility + dedup**, so Phase 4 focuses on the
source row + linkage, and notes embeddings/insights as a follow-on (they are
regenerable and not load-bearing for the integration's source-of-truth goal).
