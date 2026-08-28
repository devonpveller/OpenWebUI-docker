# Wiki viewer perf — make a click cost what a click should cost

> Status: PLAN v1, **not built** · written 2026-08-28 after the operator
> reported "click a page/link and wait 2-4 seconds", then "almost doubled on
> mobile, to the point that most clicks are unresponsive".
> Companions: `PLAN.md` (v2, P0-P4 shipped), `PLAN-NO-REBUILD.md` (A-D
> shipped), `BUILD-LOG.md`. THE system doc for implementers:
> `OB1/docker/wiki-viewer/README.md` (system map + 7 invariants).
>
> Goal (operator): navigation feels instant, on desktop **and** on the phone.
> Non-goal: changing what the wiki shows. Every phase here is invisible when
> it works.

## 0. Measured baseline (2026-08-28, live stack, 48,178 pages)

All numbers measured, none assumed. Method in parentheses.

| # | Metric | Measured | How |
|---|---|---|---|
| 1 | Page HTML served | **5-14 ms** | `curl` vs viewer :8812, warm |
| 2 | Explorer **sort** per navigation | **2,165 ms** | shipped comparator + real index, in-container V8 |
| 3 | Explorer trie build per navigation | 92 ms | same harness |
| 4 | DOM elements rebuilt per navigation | **98,210** | templates in the served page (2/file, 9/folder) x 48,178 files + 206 folders |
| 5 | Nav index payload | 16.3 MB raw / **3.1 MB** zstd, 3.3 MB gzip | `/static/graphIndex.json` through caddy |
| 6 | Nav index JSON.parse | 157-197 ms | in-container V8 |
| 7 | Nav index **retained heap** | **74 MB** | `--expose-gc`, heapUsed delta |
| 8 | Nav cache rebuild stall | **3.4 s TTFB, even on a 304** | request after `WIKI_NAV_TTL_MS` expiry |
| 9 | PostgREST page fetch, OFFSET | 46 ms @0 -> **289 ms @25k** | `/rest/v1/wiki_pages?...&offset=` |
| 10 | Same page by keyset cursor | **47 ms, flat** | `...&slug=gt.<cursor>` |
| 11 | Graph endpoint per nav | 127-130 ms | `/workbench/graph?slug=&depth=1` |
| 12 | Compression through caddy | Safari gzip 3.32 MB / Chrome zstd 3.10 MB, ~130 ms TTFB warm | raw `node:http`, no auto-decode |

**Verdict from the numbers:** the server is not slow (1). The click is slow
because every navigation redoes global work whose size is the whole vault
(2, 3, 4). Mobile is worse because it does all of that for a sidebar it does
not display (see §1, F1), on a slower core, under memory pressure (7).

**Explicitly disproved** (my first suspicion, killed by 12): Safari's lack of
zstd support is NOT a factor — caddy's `encode zstd gzip` serves both browser
families at comparable size and latency. Do not "fix" compression.

## 1. Codebase facts this plan depends on (verified, not assumed)

| # | Fact | Where |
|---|---|---|
| F1 | The nav handler awaits `setupExplorer()` **first**, and only then checks `mobileExplorer.checkVisibility()` and adds `.collapsed` — so a phone builds the whole sidebar, then hides it | `explorer.inline.ts:281-299`; confirmed in the SERVED minified bundle (`await Jt(ae)` ... `checkVisibility()&&(...add("collapsed"))`) |
| F2 | `hide-until-loaded` hides the **sibling** `.explorer-content`, not the toggle button — so `checkVisibility()` on the button is valid BEFORE the tree is built | `/srv/current/index.css`: `.hide-until-loaded~.explorer-content{display:none}` |
| F3 | Every anchor href is built **relative to the current page**, so a memoised DOM would carry wrong hrefs after a nav | `explorer.inline.ts:86` -> `path.ts:171` `resolveRelative = pathToRoot(current) + simplifySlug(target)` |
| F4 | `baseUrl` carries no path prefix and both listeners serve the wiki at `/` | `quartz.config.ts:19` (`quartz.jzhao.xyz`), Caddyfile `(wiki_app)` |
| F5 | The comparator is serialised into `data-data-fns` and rebuilt with `new Function` — it can reference a global | served page attribute; `explorer.inline.ts:166-171` |
| F6 | `Component.Explorer()` is called with NO options, at TWO call sites | `quartz.layout.ts:41` and `:67` |
| F7 | The trie needs `slug`, `title` **and `filePath`** (`fileSegmentHint` for folders without index files) — `filePath` is NOT unused, but it IS derivable (`${slug}.md`, exactly what the server already synthesises) | `fileTrie.ts:81`; `lib/nav-index.mjs:39` |
| F8 | Index field consumers: explorer `.slug` (+ trie title/filePath); graph `.links/.tags/.title` **fallback path only** (live endpoint is primary); search `.title/.tags` (TAG search only — basic search is on the API); NotesEditor `.title/.slug/.date` | grep over the four inline scripts |
| F9 | The graph re-renders on EVERY nav, and `sidebar-right` is inside the <=800px grid, so mobile pays it too | `graph.inline.ts:722-733`; `index.css` mobile grid |
| F10 | The trash-marker `MutationObserver` watches `#quartz-body` for added nodes and is idempotent (`dataset.neTrashMarked`) — so a LATER explorer build still gets marked | `NotesEditor.inline.ts:725-734` |
| F11 | `getNavIndex()` rebuilds INLINE on TTL expiry, paginating with OFFSET, and the ETag is derived from the rebuilt payload — so even a 304 waits for the rebuild | `lib/nav-index.mjs:48-79` |
| F12 | **No headless browser exists** in the image or the stack (no puppeteer / playwright / jsdom / linkedom / chromium) | `ls` + `which` in the viewer container |
| F13 | esbuild compile gates exist for `search.inline.ts`, `NotesEditor.inline.ts`, `graph.inline.ts` — **`explorer.inline.ts` has none** | `Dockerfile:145,197,203` |
| F14 | sed into TypeScript is banned (it shipped a control byte and broke the site); multi-line edits go through anchor-asserted scripts in `patches/` | `patches/search-api.mjs` header; README dev workflow |
| F15 | A viewer deploy costs a ~25-min cold rebuild; the DB fallback keeps serving throughout | README "Known windows" |

## 2. The cost model this plan attacks

Per navigation today, every consumer pays:

```
click -> SPA nav event
  ├─ explorer: rebuild trie (92ms) + SORT ALL (2,165ms) + rebuild 98,210 DOM elements
  │            ... on mobile: then hide the result            <- 100% waste (F1)
  ├─ graph:    PixiJS re-render + /workbench/graph fetch (130ms), mobile included (F9)
  └─ (full loads only) nav index: 3.1MB wire + 157ms parse + 74MB heap,
                                  plus a 3.4s stall if the server cache expired (F11)
```

The fix is not "make the sort faster" alone — it is **stop doing whole-vault
work on a per-page event**. Phases below, in payoff order.

## 3. Phases

Rule (house): each phase independently revertible, numerically gated,
rehearsed-revert once. **The deploy unit is the BATCH** (F15) — see §5.

### V0 — In-page measurement (do first; it is how every later gate is judged)

There is no headless browser (F12), and jsdom's fake layout would produce
meaningless DOM numbers, so client gates need an in-page timer.

- Query-param gated: `?wikiperf=1` records `nav`-handler durations (explorer
  build, graph render, total) into `window.__wikiperf` and renders a small
  fixed badge, so the operator can read the number **on the phone** and
  screenshot before/after. Absent the param: zero cost, no badge, no logging.
- **Gate:** with the param, a desktop nav reports ~2.2 s explorer time
  (reproducing baseline #2); without it, the string `wikiperf` never executes.
- **Revert:** remove the patch (rebuild).

### V1 — Do not build a sidebar nobody can see ← the mobile fix

Invert F1: decide displayed-ness BEFORE building, not after.

- Compute `displayed = !mobileToggleVisible || !explorer.classList.contains("collapsed")`
  using the toggle's `checkVisibility()` (valid pre-build per F2).
- If not displayed: skip `setupExplorer` entirely, flag the explorer dirty, and
  bind a ONE-SHOT build on the toggle's first open (and on the existing
  `resize` path, for a phone rotated into desktop width).
- Build-on-open uses the CURRENT slug, so the tree opens correct and scrolled
  to the active page.
- **Gate (mobile RED->GREEN, through caddy, on the operator's phone):** with
  `?wikiperf=1`, explorer time per nav goes **~2,200 ms -> 0 ms**; opening the
  hamburger builds once and shows the right tree, right active item, right
  ancestor folders open; trash strike-throughs still appear (F10); desktop
  numbers unchanged in this phase.
- **Revert:** rebuild previous image.

### V2 — Cached collator: 2,165 ms -> 75 ms (both platforms)

- Pass an explicit `sortFn` to `Component.Explorer({...})` at both call sites
  (F6) whose body uses a memoised global collator —
  `(window.__wikiCollator ??= new Intl.Collator(undefined,{numeric:true,sensitivity:"base"})).compare(...)`
  — preserving the stock folders-before-files ordering exactly.
- Works because the comparator is serialised and re-created with `new Function`
  (F5), and a `new Function` body may reference globals.
- **Gate (no browser needed):** the served page's `data-data-fns` attribute
  contains `__wikiCollator` (grep the artifact); with `?wikiperf=1` a desktop
  nav drops from ~2.2 s to **~0.3 s**; sidebar order is identical to today's
  (capture the rendered list of one deep folder before/after).
- **Revert:** drop the layout patch (rebuild).

### V3 — Build the tree ONCE per page load, not once per click

After V2 a nav still costs ~300 ms (trie 92 ms + ~98k DOM elements). Remove it.

- Memoise the built explorer DOM; per nav do only O(depth) work: clear the old
  `.active`, set the new one via `a[data-for="<slug>"]`, and open the new
  slug's ancestor folders (keeping `currentExplorerState` and its localStorage
  persistence in sync).
- **Prerequisite (F3):** hrefs must stop encoding the current page — emit
  root-relative `"/" + simplifySlug(node.slug)`. Safe per F4.
- Rebuild only when the underlying data changes; within one page load the
  `fetchData` thenable is already a single snapshot, so a new page appears on
  the next full load exactly as it does today.
- **Gate:** with `?wikiperf=1`, the FIRST nav after load reports a build and
  every subsequent nav reports **0 ms explorer / 0 DOM rebuild**; a sample of
  50 hrefs from the built tree return 200 through caddy; clicking one stays an
  SPA transition (no full reload); folder open/collapse survives navigation
  AND reload; active highlight and auto-open verified at depth >= 3.
- **Revert:** restore per-nav rebuild + relative hrefs (rebuild).

### V4 — Graph on demand (mobile)

- Gate the local-graph render on actual visibility (container
  `checkVisibility()` / `IntersectionObserver`), so a phone stops paying
  PixiJS + a 130 ms fetch on every tap (F9). Desktop, where the panel is
  visible, is unchanged. Render on first reveal.
- **Gate:** `?wikiperf=1` shows graph time 0 ms per mobile nav; scrolling the
  graph into view renders it once, correct nodes; desktop unchanged.
- **Revert:** unconditional render (rebuild).

### V5 — Never make a reader wait on the nav cache (server, flag-revertible)

`lib/nav-index.mjs` only; no client involvement.

- **Serve-stale-while-revalidate:** return the cached copy immediately and
  refresh in the background; a reader never pays the rebuild (kills baseline
  #8's 3.4 s, including the 304 case, F11).
- **Keyset pagination** (`slug=gt.<last>`) instead of OFFSET: 47 ms flat vs up
  to 289 ms per page (#9 / #10).
- **Stable ETag** derived from `count + max(updated_at)` so it changes only
  when the data does, and browsers keep their copy through drain churn.
- **Gate:** 200 requests spanning a TTL rollover all < 200 ms TTFB; ETag
  unchanged while no page is written, changes within one TTL after a write;
  DB stopped -> last good copy still served (existing fail-soft, re-drilled);
  unit tests extend the existing `lib/nav-index.test.mjs`.
- **Revert:** `WIKI_NAV_API=0` (published file) — no rebuild — or revert the
  module.

### V6 — Payload diet (decide on data, AFTER V1-V3)

Only if the remaining first-load cost (3.1 MB wire, 157 ms parse, **74 MB
heap**) still hurts mobile once the per-click cost is gone.

- Free, zero-behaviour-change: stop shipping `filePath` and re-derive it
  client-side as `${slug}.md` (F7).
- Costed, needs an explicit decision (F8): dropping `links` / `tags` degrades
  the graph's OFFLINE fallback and TAG search; dropping `date` costs one
  NotesEditor use. Measured ceiling if all go: 16.3 -> **5.7 MB** raw,
  3.1 -> **1.0 MB** compressed.
- **Gate:** measure each field's byte share first, then decide field by field;
  whatever is dropped gets a stated, tested consequence.
- **Revert:** re-add fields to the projection (`WIKI_NAV_API=0` as the
  emergency path).

## 4. Testing discipline (per phase, no exceptions)

The honest constraint: **client-side rendering has no automated harness here**
(F12). Compensate in layers rather than pretend.

1. **Pure logic -> `node --test` at image build** (existing gate, `lib/`):
   comparator factory, root-relative href builder, the "should we build?"
   predicate, the per-nav update planner, the keyset paginator. Each is a pure
   function extracted deliberately so it CAN be tested without a DOM.
2. **Compile gate -> add esbuild for `explorer.inline.ts`** (missing today,
   F13). A patch that writes text but breaks compilation has shipped before.
3. **Patch mechanism -> anchor-asserted scripts in `patches/`** (F14), never
   sed for multi-line inserts; every anchor grep-asserted so a `QUARTZ_REF`
   bump fails the build loudly.
4. **Served-artifact assertions (no browser needed):** `data-data-fns`
   contains `__wikiCollator`; the bundle contains the memo guard; 50 sampled
   hrefs return 200 **through caddy**.
5. **Live RED->GREEN with `?wikiperf=1`**, entered through the user's door
   (caddy, not container ports), on desktop AND the operator's phone, numbers
   posted before/after. This is the gate that decides whether a phase shipped.
6. **Fail-soft drills:** DB stopped (nav falls back to the published file),
   workbench stopped (search/graph degrade, pages fine), nav index absent.
7. **Rollback rehearsal** once per batch before the next batch starts.
8. **Sidecar for iteration** (README dev workflow) — never iterate live.

## 5. Deploy plan (batching, because a deploy costs ~25 min)

"Independently revertible" and "batch the deploys" are in tension; resolve it
explicitly: phases stay separate COMMITS, batches are the DEPLOY unit.

- **Batch A = V0 + V1 + V2 + V5.** Small, low risk, and it contains the whole
  mobile fix plus the 35x desktop win. Expected after A: mobile per-nav
  explorer cost 0 ms; desktop ~2.2 s -> ~0.3 s; no 3.4 s cold stalls.
- **Batch B = V3 (+ V4).** The riskiest change (href semantics, memoised
  state) ships alone, judged against A's numbers.
- **Batch C = V6**, only if A + B leave mobile memory-bound.

Stop early if the numbers say the problem is solved — V6 in particular is a
tradeoff, not a free win.

## 6. Risks

| Risk | Containment |
|---|---|
| Root-relative hrefs break under a future path prefix | Build-time assert that `baseUrl` has no path (F4) + the live 200-sample gate |
| Memoised DOM diverges from a changing index | Rebuild on data change; within a load the snapshot is already fixed (same as today's behaviour) |
| Lazy explorer breaks trash markers | F10: the observer is idempotent and fires on later insertion — asserted in V1's gate anyway |
| Lazy explorer hides a page the user expected in the sidebar | The sidebar is ALREADY collapsed on mobile; only the build moves, not the UX |
| `QUARTZ_REF` bump silently drops a patch | Every patch anchor-asserted; new esbuild gate on `explorer.inline.ts` |
| A "fix" that only helps desktop | Every headline gate is measured on the phone too |
| Scope creep into a viewer rewrite | Quartz stays the renderer; this plan only removes per-nav global work |

## 7. Deliberately NOT here

- Virtualised / lazy per-folder nav rendering (the 12,787-child `content/tool`
  folder). The correct long-term answer if the vault keeps growing, but V1 + V3
  remove the per-click cost without it — revisit with data, not appetite.
- Retiring the published `graphIndex.json` fallback (PLAN-NO-REBUILD E3).
- Search quality (`pg_trgm`, semantic) — unrelated to this symptom.

## 8. Increment V7 — graph parity + two-stage activation (2026-08-28, after Batch A)

Two operator reports on the deployed build, both about the graph panel.

### V7.1 Fullscreen showed a different graph than the inline panel

Reported: "when small there's maybe 20 nodes but at full screen there appears
to be maybe 100-200. They should be the same number."

MEASURED on the operator's example page (`content/organization/organization-lego`),
against the live endpoint:

| Query | Nodes | Links |
|---|---|---|
| `depth=1` (what the INLINE panel asked for) | **17** | 16 |
| `depth=2` | 47 | 43 |
| `depth=3&limit=800` (what FULLSCREEN asked for) | **123** | 119 |

Cause: `patches/graph-api.mjs` mapped the component's "global" request
(`depth: -1`) to `depth 3` plus `limit=800`, while the inline panel sent its
configured depth (1) and the server default limit. Two different questions, so
two different answers — the fullscreen view was never a magnification of the
inline one.

Fix: when `depth < 0`, read the depth from the INLINE graph's own `data-cfg`
(fallback 1) and send an otherwise IDENTICAL query — same depth, no extra
limit. The two views cannot drift again, and retuning `localGraph.depth` in
`quartz.layout.ts` now moves both together.

### V7.2 A single tap navigated away before you could read the graph

Reported: single click/touch should highlight the chain; a second click
navigates.

Cause: both navigation sites (the drag-end "short press = click" branch, and
the no-drag `gfx.on("click")` branch) called `spaNavigate` immediately. On a
touch screen there is no hover, so `focusOnHover` highlighting was unreachable
— a tap could only leave the page.

Fix, in the overlay fork `graph.inline.ts`: a `selectedNodeId` plus an
`activateNode()` chokepoint that both sites now call. First activation selects
and highlights the node's chain; a second activation of the SAME node
navigates. Two supporting changes make the selection usable:

- `pointerleave` restores the SELECTED node's highlight instead of clearing it,
  so a click-selected chain survives the pointer moving away;
- the dimming condition became `focusOnHover || selectedNodeId !== null`,
  because the inline graph ships `focusOnHover: false` and click-to-highlight
  must work there too.

Deliberately NOT a timed double-click: a 300 ms window is hostile on touch, and
sticky selection also lets you study the chain before deciding. Selection moves
with each new node clicked and resets on navigation; tapping empty canvas to
deselect is not implemented.

**Gates:** esbuild compile gate on the patched `graph.inline.ts`;
anchor-asserted patch script; post-patch assertions that both navigation sites
route through `activateNode` and that `limit=800` is gone. Node-set parity is
verified against the live endpoint (the table above), not by eye.

### V7.3 Selection refinements (operator, after using V7.2)

Reported: "when a second node is clicked, the last node should be 'unclicked'";
"when the user clicks in the empty space then any highlighted nodes should be
unclicked".

- **Empty-space deselect** was genuinely missing (called out as unimplemented
  when V7.2 shipped). The Pixi stage is deliberately `interactive = false` -
  d3-zoom and d3-drag own the canvas events - so this is a plain DOM `click`
  listener on the canvas that clears the selection unless a node was activated
  within the last 400 ms. Rejected alternative: hand-rolled hit testing
  against `currentTransform`. Its failure mode is worse than the timing
  guard's: a mis-mapped coordinate would clear the selection the user just
  made, whereas a too-fast background tap merely needs a second tap.
- **Single selection** was already the behaviour: `activateNode` assigns
  `selectedNodeId` and `updateHoverInfo` recomputes `active` for every node
  from scratch. What made it LOOK like the previous node stayed selected is
  that a neighbour of the newly-selected node is part of its chain, so it
  stays lit. Fixed by making the highlight THREE tiers instead of two -
  focused node 1.0, its chain 0.65, everything else 0.2 - so which node is
  selected is unambiguous even when the two are linked. (This also slightly
  changes pure-hover highlighting on the fullscreen graph, where chain nodes
  now sit at 0.65 rather than 1.0.)
