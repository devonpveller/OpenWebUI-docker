# TASKS — Quartz Production-Build Migration

Phased, testable execution of [PLAN-quartz-production-build.md](PLAN-quartz-production-build.md).
Each task has a **done-when**. Phases 1 and 2 each end with the **browser-test gate** (PLAN §6)
before any live promotion. Touch points are all in `OB1/docker/wiki-viewer/`.

Legend: ☐ todo · ▶ in progress · ✓ done

---

## Phase 0 — Investigation (no live change)

- ☐ **0.1 Reproduce the production-build minify failure.** In a throwaway build of the image (or
  `docker exec` into the running viewer), run a one-shot `npx quartz build` (no `--serve`) and
  capture the esbuild error + the offending file/script.
  **Done-when:** the exact overlay inline script and esbuild message are recorded.
- ☐ **0.2 Pinpoint the `blob:http://localhost` worker.** Identify the code path in `postscript.js`
  (FlexSearch worker factory vs preview-render vs other) and what makes the blob origin
  `localhost`. Use devtools "pause on caught/uncaught" or a search of the un-minified dev bundle.
  **Done-when:** the worker's source + trigger are known, and whether a production build (clean
  base URL, no dev origin) removes it is determined.
- ☐ **0.3 Decide minify strategy (B vs C).** From 0.1: can the offending script be fixed so
  esbuild minifies it, or must minify be disabled for it (per-script) or globally?
  **Done-when:** a one-paragraph decision recorded in this folder.

## Phase 1 — Clean dev output (Option A: suppress hot-reload injection)

- ☐ **1.1 Patch out the hot-reload client injection.** Add a grep-asserted Dockerfile `sed`
  (matching the existing pattern) against Quartz's serve handler (`quartz/cli/handlers.js`) so
  `build --serve` watches + rebuilds but injects **no** `ws://localhost` client.
  **Done-when:** a fresh build's emitted HTML contains no `new WebSocket('ws://localhost')`
  (checked in `/quartz/public`, before `serve.mjs` even runs).
- ☐ **1.2 Re-verify the blob worker.** If 0.2 found it was hot-reload-related, confirm it's gone;
  if not, leave it for Phase 2 and note it.
  **Done-when:** known-state recorded.
- ☐ **1.3 Keep `serve.mjs` strip as a no-op safety net.** Leave the regex (harmless) but add a
  comment that injection is now suppressed at build time.
  **Done-when:** comment present; strip still passes its existing behaviour.
- ☐ **1.4 BROWSER-TEST GATE (PLAN §6).** Build `:prodtest`, run a second container on `:8090`
  over a read-only vault copy, expose via a temporary tailnet serve route, run the full
  checklist on **desktop + iPhone**.
  **Done-when:** console clean of `ws://`; search/graph/explorer/nav/overlays/mobile all pass.
- ☐ **1.5 Promote Phase 1.** Retag `:local`, recreate live viewer, purge Cloudflare, confirm.
  **Done-when:** live wiki passes the checklist; `MEMORY` + this file updated.

## Phase 2 — Production build (Option B; Option C fallback)

- ☐ **2.1 Custom watcher to replace `--serve`.** Add a debounced, **single-flight** file watcher
  (inotify/poll) on `/wiki` that runs one-shot `quartz build` into `/quartz/public` on change.
  Must not overlap builds (mirror the EADDRINUSE lesson) and must feed the existing snapshot loop
  unchanged (preserve P1/P2/P3).
  **Done-when:** editing a vault file triggers exactly one rebuild; the snapshot loop publishes
  it via the existing gates.
- ☐ **2.2 Switch to one-shot production `quartz build`.** Replace the `--serve` invocation in
  `entrypoint.sh` (initial build + the watcher-driven rebuilds + the nightly clean rebuild) with
  production `quartz build`. Preserve `wait_port_3001_free` only if still relevant (no WS server
  in one-shot mode → likely removable; verify).
  **Done-when:** initial cold build + an on-change rebuild + the nightly rebuild all produce a
  complete, gated snapshot.
- ☐ **2.3 Land the minify decision from 0.3.** Either fix the overlay script so esbuild minifies
  it, or apply a scoped `minify:false` (grep-asserted `sed` on `componentResources.ts` /
  `cli/handlers.js`). Keep it as narrow as possible.
  **Done-when:** production build completes; `prescript.js`/`postscript.js` are minified (or the
  documented exception applies) and the overlays still load.
- ☐ **2.4 Confirm artifacts gone.** No `ws://localhost`, no `blob:http://localhost` worker error
  in `/quartz/public` output or at runtime.
  **Done-when:** both absent.
- ☐ **2.5 BROWSER-TEST GATE (PLAN §6).** Full desktop + iPhone checklist on the `:8090` test
  instance. Pay special attention to the overlays (most likely to break under minify) and to
  mobile interactivity (the 2026-06-16 failure mode).
  **Done-when:** all pass; console clean on both platforms.
- ☐ **2.6 Promote Phase 2.** Retag `:local`, recreate, purge Cloudflare, confirm live.
  **Done-when:** live wiki passes; docs + memory updated; `serve.mjs` strip noted as fully
  redundant.

## Phase 3 — Cleanup & follow-on

- ☐ **3.1 Update `OB1/docker/wiki-viewer/quartz-overlay/README.md`** with the production-build
  facts (minify decision, watcher, no dev artifacts).
- ☐ **3.2 Update the `wiki-viewer-completeness-gate` memory** to record the production-build
  migration and that dev-artifact stripping is no longer needed.
- ☐ **3.3 Re-open the index-size optimization** (lean `contentIndex` + lazy capped
  `searchIndex`) on the clean production baseline — **with the same browser-test gate**. Cross-ref
  the reverted attempt so its known suspects (showSearch/onType double-build; `fetchSearchData`
  timing) are checked first.

---

## Known UI bugs to fix on the browser-tested baseline

These are confirmed client-side defects that need real-browser iteration to fix safely. Do them
on the `:8090` test instance (PLAN §6), not blind on live.

- ☐ **UI-1 Left-nav (Explorer) mouse-wheel scroll doesn't work** (reported 2026-06-17). The
  scrollbar exists and **dragging it works**, but wheel-over-the-list doesn't scroll — the user
  must grab the scrollbar. **Diagnosis:** not JS — the only `wheel` listener in `postscript.js` is
  `{passive:true, capture:true}` (a passive listener can't block scrolling). It's CSS:
  `.explorer-content { overflow-y: auto }` gets the scrollbar, but the wheel isn't landing on it
  as the scroll target — a flexbox `overflow`/`min-height:0` interaction in the left
  `.sidebar`/`.explorer` column, amplified by the ~12.8k-entry tree (no `max-height` on
  `.explorer-content`; `.explorer` is `flex: 0 1 auto`). **Likely fixes to try in-browser:**
  add `min-height: 0` to the flex chain so `.explorer-content` can shrink-and-scroll, and/or a
  `max-height` on `.explorer-content`; verify the wheel scrolls the list and `overscroll-behavior:
  contain` still prevents page-scroll chaining. Also rule out a transparent overlay
  (notes-editor-root / upload-modal-root) sitting over the list and eating wheel events.
  **Done-when:** wheel scrolls the Explorer list on desktop; no regression to mobile slide-in.

---

### Guardrails (apply to every task)
- All Quartz core edits are **grep-asserted** Dockerfile `sed`s (fail the build loudly on a
  `QUARTZ_REF` bump) — not overlay forks — matching the existing Dockerfile convention.
- **No promotion to the live `:8080` viewer without a passing desktop + iPhone browser test on
  the `:8090` test instance.** Headless/static checks are necessary but **not sufficient**.
- Preserve the completeness + index-integrity gates and the builder self-heal at all times.
