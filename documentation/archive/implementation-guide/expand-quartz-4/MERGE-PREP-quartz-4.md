# Merge Prep — Quartz 4 Expansion + Source/Note Editing

**Status:** functional build COMPLETE and preview-validated on the throwaway
`ob-preview` stack (`http://127.0.0.1:8099`). This file is the **operator
runbook to merge** — the agent does **not** run git, create PRs, or tear down
the preview without an explicit "merge" instruction (G1 / [git-handling-boundaries](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/git-handling-boundaries.md)).

Branch (both repos): `feature/integrated-knowledge-system`.

---

## 0. Validation status (offline, green)

| Check | Result |
|---|---|
| Unit tests — `slug` + `generate-wiki` (citation rewrite + Evolution) | **13/13 pass** |
| Workbench `deno check src/main.ts` | clean |
| Workbench deno util tests (`paths`, `chunk`) | pass |
| `node --check` — generate-wiki / synthesize-notebooks / wiki-service / slug | pass |
| Inline editor bundle (esbuild, with CodeMirror) | builds (~530 KB) |
| Prod Caddyfile `caddy validate` | fails only on the pre-existing `email {$…}` env placeholder (line 2) — **not** the new `@import` re-upload matcher; the preview Caddy (used) works |

Preview also caught + fixed ~30 runtime bugs across the iteration that static
checks can't (live-preview, autosave/hot-reload, popover lifecycle, diff UX, …).

---

## 1. The two PRs (prep, do not execute)

This workspace is **two separate git repos**; merging is **two PRs**.

### PR A — OB1 (`OB1/`, its own remote)
Most of this is already committed on the feature branch by the OB1 auto-commit
hook. Scope:
- **Schema:** 7 additive migrations under `OB1/docker/` (`init-threads-slug`,
  `init-source-revisions`, `init-source-retract`, `init-content-types`,
  `init-source-chunks`, `init-import-jobs`, **`init-source-editing`** ← new).
- **Backend (`OB1/docker/workbench/`):** the whole Deno+Hono workbench —
  routes `notebooks · notes · sources · import · jobs · grounding · export ·
  note-refs · note-history · note-commit · source-commit`, repository +
  transaction layering, `pandoc`+`weasyprint` in the image. `note-history` GET
  also serves **read-only** git history for any vault `.md` (generated entity /
  hub / thought pages), powering the read-only RevisionHistory card on them.
- **Sidecar:** `OB1/docker/extract/` (FastAPI extractor).
- **Compilers:** `generate-wiki.mjs` (provenance, leaves, `## Evolution`,
  notebook graph nodes), `synthesize-notebooks.mjs`, `wiki-service.mjs`
  (compile-tick source + note commit calls).
- **Viewer overlay (`OB1/docker/wiki-viewer/quartz-overlay/`):** the component
  suite — `NotesEditor`(+`scripts/NotesEditor.inline.ts` = the shared CodeMirror
  editor), `RevisionHistory`, `SourceEditor`, `SourceLinker`, `SourceRetractor`,
  `GroundingPanel`, `NotebookPage`, `NotebookIndex`, `ImportStatus`, `PageTools`,
  `NoteReferences`, `UploadModal` — and the Dockerfile overlay wiring.
- **Compose (`OB1/docker/docker-compose.yml`):** `openbrain-workbench` +
  `openbrain-extract` services, `wiki-assets` volume, the 7 migration mounts,
  `openbrain-wiki` env (`WORKBENCH_URL`/`WORKBENCH_KEY`).
- **Do NOT include in the PR:** `OB1/docker/docker-compose.preview.yml`,
  `OB1/docker/preview/` (throwaway scaffolding — see §4).

### PR B — ai-stack (this repo → `main`)
Uncommitted, smaller:
- `config/caddy/Caddyfile` — the `wiki.` block `/workbench/*` route + the
  `@import` body-cap matcher (now incl. `/workbench/sources/*/replace-from-upload`).
- `docker-compose.yml` — the portal Caddy `WORKBENCH_KEY` env (if not already in).
- `scripts/emergency-recovery.ps1` / `.bat` — `openbrain-workbench` +
  `openbrain-extract` in the service inventory + start/stop order (3-place, G4).
- `.claude/skills/stack-map/references/workspace-stacks.md` — the two new rows.
- Docs: this file + `TASKS-` + `PROMOTION-RUNBOOK-` + plan.
- `scripts/test-quartz4-offline.ps1` (keep — the offline harness).

---

## 2. Migrations on the live DB (G3/G10 — operator runs, agent never does)

Run the [PROMOTION-RUNBOOK](PROMOTION-RUNBOOK-quartz-4.md): backup → `psql`
apply the 7 files in order (it now includes `init-source-editing.sql`) → verify
→ rebuild/roll. **Compose-mount alone is a no-op on the running DB** — the
runbook's psql step is mandatory.

---

## 3. Rebuild + roll order

1. Apply migrations (above).
2. Rebuild images: `openbrain-workbench` (pandoc/weasyprint), `openbrain-extract`,
   `openbrain-wiki-viewer` (CodeMirror + overlay), `openbrain-wiki`.
3. Bring OB1 up **after** `llama-cpp` is healthy; reload the portal Caddy.
4. Smoke: edit a note (draft → Commit now → revision + diff), ground an entity,
   retract/restore a source, import a doc, export a page, re-upload a source,
   open a generated entity/hub page → **read-only** compile history (no
   commit/revert/discard — each entry is a compile that changed the page).

---

## 4. Teardown / keep (on "merge")

- **Tear down:** `docker compose -f OB1/docker/docker-compose.preview.yml down -v`
  (wipes the throwaway `ob-preview` stack + its volume). Remove
  `OB1/docker/docker-compose.preview.yml` + `OB1/docker/preview/` from the PR.
- **Keep:** all `*_test.ts` / `*.test.mjs` unit tests +
  `scripts/test-quartz4-offline.ps1` (the real offline harness).

---

## 5. X.2 — backups (operator)

- **`source_revisions`** (source edit history) lives in `openbrain-db` → covered
  by the existing DB backup.
- **Note revisions** are **git** in the vault (`openbrain-wiki-data` volume) →
  covered by the vault repo / volume backup.
- **`wiki-assets`** (binary import assets) is a **separate volume** → add a
  mirror of the existing volume-backup job (owed). Purged sources drop their
  assets; retracted keep them (restorable).
