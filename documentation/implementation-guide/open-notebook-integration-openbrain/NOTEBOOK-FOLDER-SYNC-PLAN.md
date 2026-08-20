# Notebook ⇄ Wiki-Folder Sync + Notes Panel — Design & Plan

**Created:** 2026-06-06
**Folder is authority:** `wiki/content/notebooks/` (live in the `openbrain-wiki` container / `open-brain_openbrain-wiki-data` volume).
**Companion to:** [IMPLEMENTATION-PLAN-integrated-knowledge-system.md](IMPLEMENTATION-PLAN-integrated-knowledge-system.md) · [PENDING-WORK-PLAN.md](PENDING-WORK-PLAN.md)

> Realizes the plan's "notebook = thread view / cross-tool visibility" intent (§4.3, concept §6.3), with the **wiki `notebooks/` folder as the canonical notebook registry**. Operator decisions (2026-06-06): folder is authority; ON reads it; create writes back to the folder; folder-removal removes the ON notebook (thread/links persist); notes panel lists the notebook's wiki notes. Cadence: scheduled + on-demand.

---

## 0. Data model (verified against live)

Each wiki notebook = `wiki/content/notebooks/<slug>/<slug>.md` with frontmatter:
```yaml
type: notebook
thread_id: "021539ee-07e2-4514-95f2-effbfc312be3"   # the OB1 thread (canonical)
slug: "self-hosted-git-servers"
source_count: 25
source_doc_ids: ["b69ecd75-…", …]                   # OB1 source UUIDs
```
**One wiki notebook = one OB1 thread.** The thread holds the sources (`thread_sources`); the folder is the **registry of which threads are notebooks**. Live: 38 notebook folders ↔ 38 threads. An ON notebook maps to a thread via `Notebook.ob_thread_id` (already implemented).

---

## 1. Behaviors (operator spec)

| # | Behavior | Direction | Detail |
|---|----------|-----------|--------|
| **B1 Read** | folder → ON | the folder is the set; ON shows one notebook per folder entry, mapped to its `thread_id`; the source list comes from `get_thread_sources(thread_id)` (as today). | Scheduled + on-demand reconcile. |
| **B2 Create** | ON → folder → thread → ON | creating a notebook in ON writes it into `notebooks/` (becomes a thread in OpenBrain), then ON reads the resulting folder back. | ⚠️ mechanism dependent on OB1/compiler (see §4 Q1). |
| **B3 Add source** | ON → thread | adding a source to a notebook appends the source→thread link (existing `link_source_to_thread`/upload path). | Already works. |
| **B4 Remove** | folder → ON | if a notebook leaves the folder (operator's future deep-research migration), ON **deletes the ON notebook record**; the **OB1 thread + thread_sources persist** (recoverable, re-syncable). | Only folder-synced ON notebooks are eligible for removal. |
| **B5 Notes panel** | folder → ON UI | ON's middle "notes" panel lists the **notes that live in `wiki/notebooks/<notebook>`** and displays them. | ⚠️ depends on what "notes" exist in a notebook folder (see §4 Q2). |

---

## 2. What's buildable NOW (unblocked) — the read-sync foundation (B1 + B4)

ON-side only; no OB1/compiler change. **This is the first deliverable.**

- **Folder access:** mount the wiki notebooks dir read-only into the ON container. Sandbox: add to `iks-dev` compose a RO mount of `open-brain_openbrain-wiki-data` (or its `content/notebooks` subtree). Prod: same mount on the ON service (a runbook line).
- **Reconcile (`notebook_folder_sync`):**
  1. Scan `content/notebooks/*/*.md`; parse frontmatter `thread_id` (+ `slug`).
  2. For each, fetch the OB1 thread (`ob1.get_thread`) for `name`/`description`; **upsert** an ON `Notebook` keyed by `ob_thread_id == thread_id` (create if absent: `name = thread.name`, `ob_thread_id = thread_id`; mark `metadata.folder_synced = true`). Idempotent.
  3. **Removal (B4):** any ON notebook with `metadata.folder_synced = true` whose `ob_thread_id` is **not** in the current folder set → delete the ON `Notebook` record only (leave the OB1 thread + `thread_sources` intact). ON-native (non-folder-synced) notebooks are never touched.
- **Triggers:** `POST /api/notebooks/sync` (manual "Sync now") + a scheduled reconcile (interval/after-compile). Mirror the chunk-worker cadence pattern.
- **Sources show automatically:** once an ON notebook has `ob_thread_id`, the existing OB1 thread-view (`get_thread_sources`) renders its sources. (NB: chat/ask still need `source_chunks` populated — the prod chunk-worker item; display works without chunks.)
- **Helpers to add:** `ob1_repository.list_threads()` is not strictly needed (folder drives the set); add a SurrealDB "find notebook by ob_thread_id" query for idempotent upsert + removal marking.

**DoD:** run reconcile → the 38 wiki notebooks appear in ON, each showing its thread's sources; re-run is idempotent (no dupes); removing a folder + reconcile → that ON notebook disappears, its thread + `thread_sources` remain.

---

## 3. Dependent / later (gated on the OB1 + compiler work in progress)

- **B2 Create (ON → folder → thread):** ON "create notebook" must result in a folder entry that becomes a thread. Two mechanisms (see §4 Q1) — both touch the OB1/compiler side, so build after that lands. Interim: ON-create can keep creating a thread directly (`ensure_ob_thread`) and rely on the compiler to render its folder; the read-sync then picks it up.
- **B5 Notes panel:** display the notebook's wiki notes in ON's middle panel. Depends on what a notebook folder contains as "notes" (the synthesis `<slug>.md`? separate note files? OB1 AI-notes rendered to the folder?). Ties to PENDING-WORK-PLAN **Group A (notes → OB1 AI notes)**. Design after the notes substrate is settled.

---

## 4. Open questions to confirm

1. ~~**B2 create mechanism**~~ — **RESOLVED: create notebook = create thread.** ON does NOT write the folder; it creates the OB1 thread (`ensure_ob_thread`, now eager on create). The compiler renders that thread into `notebooks/` on its next run; the read-sync then marks the ON notebook `folder_synced=true`. ON-created notebooks (`folder_synced=false`) show immediately and are never auto-removed.
2. **B5 notes source — content resolution RESOLVED, panel scope to confirm.** A notebook's content lives in the **DB**, not the folder: resolve `thread_id` (= ON `ob_thread_id`) → `thread_sources` → `sources` (`sources.content`; filter `content_type='research_synthesis'` for the deep-research markdowns). The folder holds only the hub + authored AI/user notes; cited sources render at `content/source/<uuid>.md`. **Still to confirm:** exactly what the middle "notes" panel shows — the `research_synthesis` syntheses (and whether to split them out of the sources panel), authored AI/user notes (OB1 AI-notes, in progress), or both.
3. ~~**Mount vs push for reads**~~ — **RESOLVED: mount** (ON reads the folder via a read-only wiki-volume mount).

---

## Decisions (operator, 2026-06-06)

- **SurrealDB role = D1 (kept as ON's local store).** OB1 is canonical for sources/threads; the SurrealDB `Notebook` record is a **thin, regenerable mirror** of its OB1 thread (rebuilt by this sync — not a second source of truth). Chat/notes/settings/UI stay in SurrealDB.
- **Source ↔ thread is an OB1 operation** (the SurrealDB `reference` edges are vestigial in OB1 mode):
  - source **added** to a thread in OpenBrain → ON shows it (it lists `get_thread_sources` = the thread's `confirmed` links) on refresh.
  - ON **removes a source** → `set_thread_source_status(thread, source, 'inactive')` — the link is disconnected but the **source row is never deleted in OB1** (already implemented: `DELETE /sources/{id}?notebook_id`, `DELETE /notebooks/{nb}/sources/{id}`).
- **Notebook removal (B4) ≠ source removal.** Dropping a folder entry deletes the SurrealDB notebook **mirror only**; it makes **no OB1 calls**, so the thread + all its `thread_sources` stay confirmed (verified: removal log shows zero OB1 unlinks).
- **Cadence:** scheduled (`NOTEBOOK_SYNC_INTERVAL_S`, 900s sandbox) + on-demand (`POST /notebooks/sync`).

---

## 5. Status

> **PROMOTED TO PROD 2026-06-07.** Live `open_notebook` runs the fork on
> `openbrain-db`. Reconcile validated against the real registry:
> `created=0 updated=8 removed=0 folder_count=36` → **36 ON notebooks, all
> `ob_thread_id`-linked, no duplicates.** Migration + nightly wiki recompile had
> produced duplicate folder-synced "twins" for 7 names; merged into the user's
> originals (chat preserved) — reconcile keys on `ob_thread_id`, so twins do not
> regenerate. Sources/notes partition confirmed live (Voya 401k: 111 sources + 5
> syntheses). See [PROMOTION-EXECUTED-2026-06-07.md](PROMOTION-EXECUTED-2026-06-07.md).

- [x] **B1/B4 read-sync** — built + validated in `iks-dev`. 38 wiki notebooks → 38 ON notebooks (`folder_synced=true`, persisted via **migration 16** — notebook table is SCHEMAFULL); idempotent (re-run created/updated/removed = 0); exclude-one → that ON notebook removed with **no OB1 unlink** (thread/sources preserved) → restore re-creates. Mount `openbrain-wiki-data:/wiki:ro` + `WIKI_NOTEBOOKS_DIR`. **NB:** sandbox ON points at `iks-db` (6 threads), so source/name-from-thread resolution fully exercises only in prod (ON → `openbrain-db`, the 38 threads); creation/idempotency/removal validated here. Build is cp'd — image rebuild + compose mount + migration 16 owed at promotion.
- [x] **B3 add / source-remove** — already implemented (OB1 `thread_sources` link / set-inactive; source row never deleted).
- [x] **B2 create = thread** — built + validated: `POST /notebooks` now eagerly `ensure_ob_thread()` → OB1 thread created (verified: ob_thread_id set, iks-db threads +1, thread named after the notebook). The compiler renders the folder later; read-sync reconciles `folder_synced`.
- [x] **B5 notes panel = non-raw content (deny-list, 2026-06-07).** Operator switched from the `=research_synthesis` allow-list to a **deny-list** for forward-compatibility: `ob1.RAW_SOURCE_CONTENT_TYPES` (the 12 web/document/image/audio/manual/transcript types from the live `content_types` table) defines the **Sources** panel; **Notes = everything NOT in that set** (`content_type <> ALL(raw)`), so `research_synthesis` + any future note-like type (ai_note, new synthesis kinds) auto-appear as notes with no code change. The two panels are complementary (clean partition). Validated against live: github-repo-privacy → 22 sources + 1 note. (Was, originally:)
- [x] **B5 notes panel = syntheses** (operator: "syntheses as notes; raw sources stay in Sources"). Built: `ob1.get_thread_notes()` (thread→`thread_sources`→`sources` WHERE `content_type='research_synthesis'`) + `GET /notebooks/{id}/notes`; the Sources panel now **excludes** `research_synthesis`. Frontend: `useNotebookSyntheses` → merged into the Notes panel as **read-only** cards (FlaskConical/"Research" badge; click opens the source modal; no edit/delete). Chat/researcher/podcast retrieval unchanged (still see everything). Validated (seed): a `research_synthesis` source appears in `/notebooks/{id}/notes` and NOT in `/sources`; a raw source appears in `/sources` and NOT in notes. **NB:** sandbox `iks-db` has no real `research_synthesis` rows (one seeded for the test); full visual validation is in prod. Authored AI/user notes can be added to the same panel later (Group A / OB1 AI-notes).
