# PLAN — Quartz Production-Build Migration

Move `openbrain-wiki-viewer` from Quartz's **dev `build --serve`** output to a **clean
production-quality build**, eliminating the dev-mode artifacts that currently leak into the
publicly served site, while preserving the two-stage availability architecture, the
auto-rebuild-on-change behaviour, and the workbench overlays.

---

## 1. Problem statement

The served wiki ships dev-mode client code:

| Artifact | Where | Impact | Current mitigation |
|----------|-------|--------|--------------------|
| `new WebSocket('ws://localhost:3001')` hot-reload client | inline `<script>` in every page | would `location.reload()` on a phantom signal; connects to nothing remotely | `serve.mjs` strips the `<script>` at serve time (regex) |
| blob-backed Web Worker | compiled into `postscript.js` | `Not allowed to load local resource: blob:http://localhost/…` console errors on search; **cosmetic** (main-thread fallback works) | none — can't strip compiled code cleanly |
| Unminified client bundle | `prescript.js` / `postscript.js` | larger transfer than necessary | none |

All three are symptoms of **serving a dev build remotely**. We want a production build whose
output is clean and minified, so `serve.mjs` no longer has to paper over dev artifacts and the
console is clean on every browser.

## 2. Current architecture (what must be preserved)

`OB1/docker/wiki-viewer/entrypoint.sh` runs a **two-stage** viewer:

1. **Builder/watcher** — `npx quartz build --serve --port 8081`. Used NOT for its dev HTTP
   server but for its **file watcher**: it re-emits `/quartz/public` whenever `/wiki` changes.
2. **Static server** — `node /serve.mjs` on `:8080` serves `/srv/current` (an atomically
   swapped symlink to a snapshot). It strips the hot-reload script and sets cache headers.
3. **Snapshot loop** — copies `/quartz/public` → `/srv/build-N` and swaps `/srv/current` only
   when the build is **complete + index-intact + settled + viewer-idle**
   (`is_complete` / `index_ok` gates; see the [wiki-viewer hardening](../../..) history).

Invariants to preserve through the migration:
- **P1** Auto-rebuild on `/wiki` change (the watcher).
- **P2** The completeness + index-integrity publish gates (never serve a torn build).
- **P3** Reader never sees a "rebuilding" splash mid-session (idle-gated atomic swap).
- **P4** The workbench overlays (NotesEditor, UploadModal, GroundingPanel, Source* , Notebook*,
  RevisionHistory, etc.) keep working — they are layered via Dockerfile `sed` patches +
  `quartz-overlay/` COPY and include **inline `<script>`** blocks.
- **P5** The two prior fixes stay intact: completeness gate, builder self-heal,
  `wait_port_3001_free`, index-integrity gate.

## 3. Root causes & relevant code

- **Why dev mode was chosen:** the entrypoint comment states the production build "minifies the
  bundled component JS and the workbench overlay inline scripts trip esbuild's minifier." So a
  one-shot `quartz build` aborts on an overlay script during minification.
- **Where minify lives (Quartz v4.5.1):**
  - `quartz/plugins/emitters/componentResources.ts:71-73` and `:291` — `minify: true` for the
    bundled client scripts (`prescript.js` / `postscript.js`).
  - `quartz/cli/handlers.js:246-247` (`minifyWhitespace` / `minifySyntax`) and `:287`
    (`minify: true`) — the build vs serve divergence; the inline page scripts are transformed
    here.
- **Where the hot-reload client is injected:** Quartz's `serve` handler in
  `quartz/cli/handlers.js` (the `ws://localhost:3001` client). A one-shot `quartz build`
  (no `--serve`) does **not** inject it.
- **The blob worker:** created in `postscript.js` (`new Worker(URL.createObjectURL(new Blob([...])))`).
  Likely a FlexSearch worker-factory path or a preview-render path; **must be pinpointed**
  (TASKS Phase 0). A production build with correct base URLs / without the dev origin should
  resolve the `localhost`-origin blob, but this is an explicit investigation item, not an
  assumption.

## 4. Goals

- **G1** No dev-mode artifacts in served output: no `ws://localhost` client, no
  `blob:http://localhost` worker error, on any browser.
- **G2** Minified, production-quality client bundle (or a deliberate, documented decision to
  ship unminified if minify can't be made safe).
- **G3** Preserve P1–P5 (architecture, gates, overlays, watcher, prior hardening).
- **G4** `serve.mjs` no longer needs the hot-reload strip (it becomes a no-op safety net).
- **G5** A repeatable **browser test** (desktop + iPhone) that gates every promotion to live.

## 5. Options

### Option A — Suppress the dev hot-reload injection, keep `--serve` (low risk, partial)
Patch Quartz's serve handler so `build --serve` still **watches + rebuilds** but does **not**
inject the hot-reload client. Keeps dev mode (no minify), so the overlays are safe (P4) and the
watcher is preserved (P1). 
- **Solves:** G1 (ws gone; blob worker likely gone if it's hot-reload-related — verify),
  most of the user-visible noise.
- **Does not solve:** G2 (still unminified).
- **Risk:** LOW — a targeted patch to one Quartz file; no minify exposure.
- **Verdict:** strong **Phase 1** — immediate, low-risk cleanup.

### Option B — True production build with a custom watcher (full, higher effort)
Replace `quartz build --serve` with one-shot `quartz build` (production: minified, no
hot-reload) driven by a **custom file-watcher** that re-runs the build on `/wiki` change. Plus
**fix the minify breakage** on the overlay scripts (pinpoint the offending script and either fix
its syntax so esbuild can minify it, or exclude just that script from minification).
- **Solves:** G1 + G2 fully.
- **Cost:** a watcher to replace `--serve` (debounced, single-flight, feeds the existing
  snapshot loop), plus the minify investigation/fix.
- **Risk:** MEDIUM — new watcher + minifier changes; must not regress P1/P2/P3.
- **Verdict:** **Phase 2** — the real "production build," done deliberately after Phase 1.

### Option C — Production build, minify globally OFF
Use one-shot `quartz build` but patch `componentResources.ts` / `handlers.js` to `minify: false`,
sidestepping the breakage entirely.
- **Solves:** G1; **not** G2 (unminified, same as dev for size).
- **Risk:** LOW-MEDIUM. Simpler than B but gives up minification — so it offers little over
  Option A while still requiring the custom watcher.
- **Verdict:** fallback only if B's minify fix proves intractable.

## 6. Recommended approach — phased, browser-gated

**Phase 0 — Investigate (no live change).** Pinpoint (a) exactly which overlay inline script
trips the production minifier and the esbuild error, and (b) the precise origin of the
`blob:http://localhost` worker. Output: a one-page findings note that decides B-vs-C for minify.

**Phase 1 — Clean dev output (Option A).** Patch out the hot-reload injection; keep the watcher.
Browser-test, then promote. This removes the user-visible console noise quickly and safely.

**Phase 2 — Production build (Option B, or C as fallback).** Introduce a custom debounced
single-flight watcher feeding the existing snapshot loop; switch to one-shot `quartz build`;
land the minify fix from Phase 0. Browser-test thoroughly, then promote.

Each phase is independently shippable and independently revertable.

### §6 Browser-testing strategy (MANDATORY — the 2026-06-16 lesson)
No phase promotes to the live viewer until it passes a real-browser check on a **non-live test
instance**:
1. Build the candidate image as a distinct tag and run a **second container** on a spare port
   (e.g. `:8090`), pointed at a **copy/read-only mount** of the vault — never the live `:8080`.
2. Expose it for testing via the tailnet (a temporary `tailscale serve` route) so it can be
   opened on **both desktop and the iPhone** (tailnet bypasses Cloudflare).
3. Checklist (desktop Chrome/Safari + iOS Safari): **console is clean** (no `ws://`, no
   `blob:`); **search** opens, accepts input, returns results with previews; **graph** renders;
   **explorer** expands/collapses; **nav** (SPA link clicks) works; **dark-mode toggle** works;
   **workbench overlays** (Write-a-note, Upload modal, grounding) open and function; **mobile**
   buttons + search input are responsive.
4. Only on a clean pass: retag to `:local` and recreate the live viewer; then purge Cloudflare.

## 7. Risks & rollback

- **R1 Minify fix regresses an overlay.** Mitigate via Phase-0 pinpoint + Phase-2 browser test;
  rollback = revert the minify patch / fall back to Option C.
- **R2 Custom watcher misses changes or double-builds.** Debounce + single-flight; the snapshot
  loop's settle gate already tolerates mid-build states. Rollback = restore `--serve`.
- **R3 Production base-URL differences** surface new path bugs. Caught by the browser test.
- **Rollback (any phase):** the changes are Dockerfile `sed`s / `entrypoint.sh` / `serve.mjs` —
  revert the diff, rebuild, recreate. The completeness/integrity gates keep the last good
  snapshot serving throughout a bad rebuild.

## 8. Out of scope (tracked elsewhere)
- The **search-index size optimization** (lean `contentIndex` + lazy capped `searchIndex`). It
  was reverted 2026-06-16; it is independent of the build mode and must be re-done with the same
  browser-test discipline. Note it as a follow-up that *benefits* from this migration (clean
  baseline) but is not part of it.
