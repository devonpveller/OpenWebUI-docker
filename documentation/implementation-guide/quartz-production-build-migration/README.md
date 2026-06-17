# Quartz Production-Build Migration

**Status:** PLAN (not started) · created 2026-06-17
**Owner area:** `OB1/docker/wiki-viewer/` (the `openbrain-wiki-viewer` container)
**Related:** [expand-quartz-4](../expand-quartz-4/) (the workbench overlay this must preserve)

## Why this exists

The wiki viewer is built and served with Quartz's **dev mode** (`quartz build --serve`),
not a production build. That was a deliberate choice — the production build's minifier
chokes on the workbench overlay inline scripts — but it has leaked dev-mode artifacts
into the **publicly served** site:

- A hot-reload client (`new WebSocket('ws://localhost:3001')`) — currently papered over
  by a strip in `serve.mjs`.
- A blob-backed Web Worker that the browser blocks (`Not allowed to load local resource:
  blob:http://localhost/…`) — cosmetic (search falls back to the main thread) but noisy,
  and **not** strippable the same easy way (it's compiled into `postscript.js`).
- An unminified client bundle (larger than it needs to be).

These are all symptoms of one root cause: **serving a dev build remotely.** This guide
plans the migration to a clean production-quality build.

## Read order

1. **[PLAN-quartz-production-build.md](PLAN-quartz-production-build.md)** — problem, current
   architecture, root causes, options analysis, recommended phased approach, risks, and the
   **mandatory browser-testing strategy**.
2. **[TASKS-quartz-production-build.md](TASKS-quartz-production-build.md)** — phased, testable
   task breakdown with per-task done-when criteria and browser-test gates.

## The one non-negotiable

This is a **client-side change to a live wiki**. On 2026-06-16 an index-split change was
shipped after headless verification only; it passed every server-side check and still broke
search + mobile because the defect was in browser runtime behaviour. **Every phase here must
be validated in a real browser (desktop + iPhone) on a non-live test instance before it
touches the live viewer.** The PLAN's §6 defines how.
