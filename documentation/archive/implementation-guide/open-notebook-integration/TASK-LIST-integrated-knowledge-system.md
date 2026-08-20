# Task List — Integrated Knowledge System

**Companion to:** [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](IMPLEMENTATION-PLAN-integrated-knowledge-system.md) (the source of truth — each task links to its phase there)
**Created:** 2026-06-01 · post-audit (incorporates corrections **C1–C4**, see plan §11)
**How to use:** tick boxes as you go; this mirrors the plan's Progress Ledger (§9). Update both, or treat the plan's ledger as canonical and this as the quick view.

Legend: `[ ]` todo · `[~]` in-progress · `[x]` done · `[!]` blocked · ⚠ = audit-critical

---

## Dependency order

```
Phase 0 ─► Phase 1 ─► Phase 2 ─┬─► Phase 3 (3.0 ⚠ first)
                               └─► Phase 4 ─┬─► Phase 5*
                                            ├─► Phase 6
                                            └─► Phase 7
                          all ────────────► Phase 8
* Phase 5 builds on 1–2; its DoD is validated with Phase 6.
```

Do not start a phase whose dependencies' DoD is unmet. All validation runs in the `iks-dev` sandbox only (never prod). No `git add`/`commit`/`push` (D4).

---

## Phase 0 — Isolation harness & branches  *(no deps)*

- [x] **0.1** Working branches (no commits): `ai-stack` → `feature/integrated-knowledge-system`; `OB1` → local branch; `open-notebook` → local branch
- [x] **0.2** `iks-dev/docker-compose.dev.yml` — project `iks-dev`: `iks-db`, `iks-mcp`, `iks-surreal`, `iks-notebook` (fresh volumes, non-colliding ports, no prod volume/container)
- [x] **0.3** `iks-dev/seed.sql` — ~3 threads, ~15 sources (some overlapping), a couple of sessions (synthetic only)
- [x] **0.4** `iks-dev/README.md` — up/down, ports, "never touches prod" warning
- [x] **0.5** `iks-dev/baseline-inventory.md` — snapshot via `/stack-map`

**DoD:** sandbox comes up; `iks-mcp` answers `tools/list`; `iks-db` has seed data; grep-proof zero prod refs.

---

## Phase 1 — OB1 data model  *(needs 0)*

- [x] **1.1** `OB1/docker/init-threads.sql` (runs after `init-sources.sql`): `threads`, `thread_sources` (link_type ∈ automatic/suggested/deliberate, status ∈ confirmed/pending/hidden/inactive), `sessions`, `session_sources` + touch-triggers + indexes
- [x] **1.2** `ALTER TABLE sources ADD COLUMN IF NOT EXISTS content_hash TEXT` + index
- [x] **1.3** `find_or_create_source(...)` — match on `url` OR `content_hash`; returns `(id, was_duplicate)` ⚠ *also unblocks 3.0*
- [x] **1.4** Lifecycle fns: `link_source_to_thread`, `set_thread_source_status` (upsert/flag, never delete)
- [ ] **1.5** Mirror as `OB1/schemas/research-threads/` contrib *(optional — skipped)*

**DoD:** idempotent re-run; dup insert → one row + `was_duplicate=true`; link to two threads → two rows; soft-unlink → `inactive`; no `DROP`/`TRUNCATE`/unqualified `DELETE`.

---

## Phase 2 — MCP tool extensions  *(needs 1)*

- [x] **2.1** 11 tools in `kubernetes-deployment/index.ts` (mirror existing registration + `x-brain-key`): `create_thread`, `list_threads`, `get_thread_sources`, `add_to_thread`, `remove_from_thread`, `get_suggestions`, `accept_suggestion`, `hide_suggestion`, `get_hidden_suggestions`, `restore_suggestion`, `capture_with_thread`
- [x] **2.2** Gateway stays closed — do **not** add to `openbrain-gateway/app.py` allow-list; add code comment + runbook note (guardrail 5)
- [x] **2.3** OpenAPI exposure — **no mcpo config entry needed (C3):** restart `openbrain-mcpo` (core, ≠ `openbrain-mcpo-ext`) so it re-reads `tools/list`, re-import in OWUI

**DoD:** `tools/list` shows 11; `create_thread`→row; `capture_with_thread`→source+`thread_sources(automatic,confirmed)`(+`session_sources`); `get_thread_sources` returns only confirmed; accept/hide/restore follow §4.3; `remove_from_thread`→`inactive`.

---

## Phase 3 — OWUI research → session provenance + thread linkage  *(needs 2)*

- [x] **3.0 ⚠ PREREQUISITE (C1)** — refactor `/research/persist` per-source replace from hard-`DELETE`+INSERT to `find_or_create_source` (stable `id`s, dedup-and-relink) so thread/session FKs survive re-runs
- [x] **3.1** Extend payload (`evidence_memory.py::persist_research_evidence` ~L188) + handler (`index.ts` ~L853): create `sessions` row, `session_sources`, `thread_sources(automatic,confirmed)` if `thread_id`, stamp provenance into `sources.metadata`
- [x] **3.2** `active_thread_id` valve/param in `smolcrawl/deep_research_tool.py`; no thread ⇒ unthreaded inbox (session only)
- [x] **3.3** Preserve synthesis upsert (`uq_sources_synthesis_key`) + volatility/revalidate (additive now *requires* 3.0)

**DoD:** persist with `thread_id` → 1 session + N session_sources + N thread_sources(automatic); without → session + session_sources, no thread link; synthesis still supersedes-in-place; **re-running same `research_key` keeps source `id`s + existing links intact (C1)**.

---

## Phase 4 — Open Notebook full repoint (D1 — spine)  *(needs 2)*

- [x] **4.1** Inventory ON source surface (real layout, C2): `open_notebook/domain/notebook.py` (`Source`/`Notebook`, `table_name="source"`) → `open_notebook/database/repository.py` → `api/sources_service.py` → `api/routers/sources.py` + `notebooks.py` → `open_notebook/graphs/source.py` → `database/migrations/*.surrealql`. Output `iks-dev/on-source-surface.md`
- [x] **4.2** OB1 data-access module in fork (`pg` async; document choice) — `ob1_repository.py` (asyncpg)
- [x] **4.3** Repoint upload (`find_or_create_source`+`thread_sources`), notebook-view = thread-view (1:1 thread↔notebook), interaction reads from OB1 — **upload+view+interact DONE & validated E2E**. Upload: `save_source`→`sync_extracted_source` (content+bge-m3 embedding+thread link; no empty pre-extraction rows; notebook⇄thread 1:1). Chat/source-chat/podcast context read OB1. **Interact/Q&A:** ask graph + `/search` vector now retrieve OB1 `source_chunks` (`search_all_chunks`→`group_chunks_by_source`), cite real `source:<uuid>`; SurrealDB fallback when OB1 off; keyword `text_search` stays SurrealDB by design.
- [x] **4.4** Keep SurrealDB for UI prefs / job queues / chat / cache only — only source-family routed to OB1
- [x] **4.5** One-time migration script `iks-dev/migrate-on-sources.*` (idempotent, dedup-aware, dry-run; agent never runs vs prod)
- [x] **4.6** Stage (not apply) compose swap `image: lfnovo/open_notebook:v1-latest` → built fork; capture as runbook diff

**DoD:** upload → `iks-db.sources` + `thread_sources`, nothing source-shaped in `iks-surreal`; open notebook → shows OB1 sources incl. Phase-3 OWUI row; dup URL → no new row + notice; Q&A reads OB1; UI state still in SurrealDB.

---

## Phase 5 — Cross-thread suggestion engine  *(builds on 1–2; DoD with 6)*

- [x] **5.1** `OB1/integrations/suggestion-worker/index.ts` (model on `entity-extraction-worker`); reuse existing `source_extraction_queue` or sibling `suggestion_queue` (C4); `match_sources` cosine vs other threads → `thread_sources(suggested,pending,reason)`
- [x] **5.2** Suggested-only rule — never auto-confirm cross-thread (concept §4.2)
- [x] **5.3** `SUGGESTION_THRESHOLD` env + dedup vs existing links (any status); HTTP drain route `POST /suggest` style (not `/run`, C4)
- [x] **5.4** Note three-places sync owed in Phase 8.2 (new container)

**DoD:** overlapping seeds → `suggested/pending` rows with reasons; no auto-confirmed cross-links; hidden pair not re-suggested; threshold env-tunable + logged.

---

## Phase 6 — Triage UI inside Open Notebook (D2)  *(needs 4)*

- [x] **6.1** Triage queue view: `get_suggestions` + Accept (`accept_suggestion`→confirmed) / Hide (`hide_suggestion`→hidden) — `SuggestionsDialog`, operator-approved
- [x] **6.2** Hidden/rejected pool view: `get_hidden_suggestions` + Restore (`restore_suggestion`→pending) — Hidden tab + undo toast
- [x] **6.3** Entry point: triage panel/badge in ON nav (per-thread + global) — thread-mode in `NotebookHeader`, source-mode popover on `/sources`

**DoD:** pending suggestion shows with reason; Accept → appears as linked source; Hide → leaves queue, retrievable from pool; Restore → pending. Transitions match §4.3.

---

## Phase 7 — Obsidian inbox stub  *(needs 4)*

- [x] **7.1** ON "send session to Obsidian inbox" → markdown stub into wiki `notes/` (summary + source refs + claims; deliberately incomplete)
- [x] **7.2** Respect separation: stubs in `notes/` not `content/`; not ingested as a `source`
- [x] **7.3** Sandbox targets a scratch clone — never push to real `openbrain-wiki-data` remote

**DoD:** trigger writes `.md` into scratch `notes/`; excluded from `content/`; no `sources` row created.

---

## Phase 8 — E2E, drift sync, promotion handoff  *(needs all)*

- [x] **8.1** Run §6 scenarios end-to-end in `iks-dev` → `iks-dev/e2e-results.md`
- [x] **8.2** Three-places sync for `suggestion-worker`: OB1 compose + `scripts/emergency-recovery.ps1` + `.bat` (inventory + ordered start/stop) + `.claude/skills/stack-map/references/workspace-stacks.md`; `/stack-map` shows no drift vs baseline — ⚠ **`chunk-worker` (17th container) three-places still owed at promotion** (staged in PROMOTION-RUNBOOK)
- [x] **8.3** `PROMOTION-RUNBOOK.md` (operator-executed): backup first → migration order (`init-threads.sql` → `content_hash` → `migrate-on-sources` dry-run then real) → rebuild order → rollback → per-repo commit checklist (D4)
- [x] **8.4** Verify `openbrain-db` backup covers new tables (don't assume)

**DoD:** all five §6 scenarios pass; `/stack-map` no drift; runbook complete; nothing committed.

---

## Audit corrections to keep front-of-mind (plan §11)

- **C1 (High)** — `/research/persist` hard-DELETEs source rows → **Phase 3.0 must land before 3.1**.
- **C2 (Med)** — ON services/routers under `api/`; primary surface is `domain/notebook.py`.
- **C3 (Med)** — mcpo = whole-server proxy; no per-tool config, just restart + re-import.
- **C4 (Low)** — reuse `source_extraction_queue`; worker route `POST /` / `POST /sources`.
