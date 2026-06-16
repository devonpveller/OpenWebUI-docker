# Open WebUI 0.8.10 → 0.9.6 upgrade + knowledge-collection migration

Two coupled tracks, **run in this order**:

1. **[KNOWLEDGE-MIGRATION-PLAN.md](KNOWLEDGE-MIGRATION-PLAN.md)** — copy the existing
   OWUI knowledge collections into Open Brain (OB1) as `threads` + `sources`,
   **before** touching the OWUI version. Read-only against OWUI; additive into OB1.
   > The `migration/` scripts have been **generalized and relocated** to the
   > reusable tool [`tools/owui-knowledge-to-openbrain/`](../../../tools/owui-knowledge-to-openbrain/)
   > (entry = any OWUI `webui.db`, endpoint = Open Brain). This plan remains as the
   > historical record of the 2026-06 run; the staging artifacts were removed.
2. **[UPGRADE-PLAN.md](UPGRADE-PLAN.md)** — the 0.9.6 prep/breaking-change runbook.

## Why migrate first

- We read the collections against the **known-good 0.8.10 schema**, before 0.9.6's
  new knowledge access-control enforcement can complicate retrieval/enumeration.
- It gives knowledge a verified second home (OB1) **before** we touch OWUI at all.
- The migration never writes to OWUI, so it is safe to run anytime and re-run.

## Decided end state (2026-06-11)

- **OWUI knowledge collections are being abandoned, not rewired.** After the OB1
  copy is verified, we simply **stop using** OWUI knowledge/RAG. No deletion of the
  OWUI collections is required, and no OWUI→OB1 retrieval rewire is in scope here.
- Migration backbone is **Route B — copy already-extracted text** from `webui.db`,
  but as a **cautionary, filtered promotion**: empty collections, empty/low-context
  files, and duplicates are filtered out *before* anything is written to OB1.

## Scope guard

This folder is planning only. Nothing here changes the running stack until the
operator executes a step. Both plans call out exactly which step is the first
irreversible action (OWUI: the image rebuild + first 0.9.6 boot; Migration: the
promote/ingest step — and even that is dedup-safe and re-runnable).
