# Portable implementation plan — Quartz 4 integration

**Goal:** render the Open Brain knowledge base — threads, sources, claims, and
research syntheses produced by the [research service](PORTABLE-RESEARCH-SERVICE.md)
— as a browsable, navigable, editable [Quartz v4](https://quartz.jzhao.xyz/)
wiki. Research output that lands in the KB shows up as wiki pages with working
citations and provenance; an authenticated **workbench** layer lets the operator
read, edit, retract, and import sources in place.

This is a **separate effort** from the research service. It is a downstream
*consumer* of that service's schema (`sources`, `threads`, `claims`,
`claim_sources`, `source_chunks`). It works the same way it does in the reference
`ai-stack` workspace; this plan strips workspace-specific deployment details so it
travels.

> The single most important thing this plan carries forward is **publish-gate
> discipline** (§6). Quartz cold-builds over a large vault are fragile; the
> reference workspace took several outages to learn which gates prevent serving a
> half-built site. Implement those gates from day one.

---

## 0. What "works the same way" means (the invariants)

1. **The KB is the source of truth; the wiki is a projection.** A compiler reads
   Postgres and emits markdown; Quartz renders the markdown. The wiki never owns
   data the KB doesn't. (§2)
2. **Provenance is navigable.** Every synthesized page cites sources as working
   wikilinks; every source has its own leaf page; claims trace back to sources.
   (§3, §4)
3. **Never serve a half-built site.** Publish only when the build is *complete*
   (all assets present, indices intact), not merely "started." Serve the last
   good snapshot while rebuilding. (§6)
4. **Edits are authenticated and provenanced.** The workbench writes go through a
   server-injected key behind an auth proxy; note edits commit to the vault git
   repo. No client-side secrets. (§5)

---

## 1. Prerequisites

| Requirement | Why | Reference default |
|-------------|-----|-------------------|
| **The research-service data plane** | The compiler reads `sources`/`threads`/`claims`/`claim_sources` | see the [research service plan](PORTABLE-RESEARCH-SERVICE.md) §3 |
| **A read API over the DB** | compiler reads; PostgREST is convenient for reads | `openbrain-rest` (PostgREST) + a Caddy alias |
| **Chat LLM + embeddings** | the compiler synthesizes entity/thread pages and embeds for shortlist | same gateway as the research service |
| **A reverse proxy** | inject the workbench key; route public vs tailnet | Caddy (`wiki_app` snippet) |
| **(Optional) an auth provider** | gate the public surface | Authelia `forward_auth` |

If you only want a read-only wiki, you can skip the workbench (§5) and the auth
proxy and serve the viewer directly.

---

## 2. Architecture

Three services plus the shared Postgres. The viewer and compiler share a vault
volume; the workbench writes to it and to the DB.

```
 Postgres (sources/threads/claims/claim_sources/source_chunks)
        │  reads (PostgREST)
        ▼
   wiki-compiler ──emits markdown──►  /wiki  (vault volume, git repo)
   (scheduler +                          │  watch
    change-watch +                       ▼
    git + synthesis)                  wiki-viewer  ──:8080──►  Caddy ──►  browser
                                      (Quartz v4 build --serve)   │
   workbench ◄──writes notes/sources──┘  (reads /wiki :ro)        ├─ public:  forward_auth (Authelia) + key inject
   (Deno/Hono) ──writes DB (txns)──► Postgres                     └─ tailnet: no auth, same backends
```

| Service | Role | Stack |
|---------|------|-------|
| **wiki-compiler** | Polls/debounces on KB change; synthesizes entity & thread pages; emits source/claim **leaf pages**; rewrites citations to wikilinks; commits to vault git. Nightly full recompile + change-watch incremental. | Node |
| **wiki-viewer** | Quartz v4 pinned at a fixed ref, with an overlay of custom components; `npx quartz build --serve`; watches the vault volume; serves static HTML. | Node 22 + Quartz |
| **workbench** | Authenticated write API: notebook/thread CRUD, note read/write/trash (vault git), source read/update/**retract**/restore, file/URL **import** (extract→chunk→embed→link). | Deno + Hono |

---

## 3. Data flow: Open Brain → Quartz

```
sources / threads / claims (Postgres)
   │  compiler reads linked sources for an entity/thread
   ▼
buildSynthesisInput()   prepare context: entity, source tokens (S1/S2…), claim/thought ids (#id)
   ▼
synthesize()            LLM (nothink) writes the page body
   ▼
rewriteCitations()      S1 -> [[source/<uuid>|S1]] ;  #id -> [[thought/<id>|#id]]
   ▼
writePage()             emit markdown to /wiki/content/
   ├─ content/source/<uuid>.md     (leaf: full source body + backlinks + tombstone if retracted)
   ├─ content/thread|notebook/<slug>.md
   ├─ content/entity/<slug>.md
   ├─ graph.json                   (nodes + edges for the graph view)
   └─ .wikistate.json              (compile state; gitignored)
   ▼
git commit (vault)      "wiki: <reason>"   <- proof of a finished compile
   ▼
Quartz watcher rebuilds -> static HTML -> viewer serves
```

Key rewrite rule: citation tokens that the synthesis emits (e.g. `S1`, `#abc`)
are rewritten into **wikilinks** pointing at leaf pages, so Quartz's native
backlinks/popovers/graph all light up for free. The `source → claim` relationship
from the research service becomes navigable links here.

---

## 4. Rendering the research content types

### 4.1 `research_synthesis` sources

A research synthesis is a `sources` row with `content_type='research_synthesis'`,
a unique `research_key`, and the full synthesis body in `content`. The compiler:

- includes it in the linked-source read path for any entity/thread it cites,
- renders cited claims as a synthesis section on the relevant page,
- gives it a source leaf page like any other source.

### 4.2 Source leaf pages

`content/source/<uuid>.md` is a read-only provenance record:

- frontmatter `type: source`, `id`, `url`, `title`, `content_type`,
- body = full `sources.content`,
- backlinks = "which pages cite this source" (Quartz native),
- if the source is retracted **and committed** (`retracted_at IS NOT NULL AND
  retraction_committed_at IS NOT NULL`), render a tombstone instead of content.

**Leaf size cap (a real failure mode):** migrated chat/doc "sources" can be tens
of MB and blow up Quartz's markdown parse with catastrophic regex backtracking.
Cap leaf body emission (reference: **128 KB**) and link to the full content
rather than inlining it.

### 4.3 Custom components (overlay)

Quartz is pinned at a fixed ref; an overlay directory adds components that are
copied over the clone at build time and registered append-only in
`components/index.ts` + `quartz.layout.ts`:

- **NotesEditor** — in-place CodeMirror 6 editor on `notes/` pages; `[[…]]`
  autocomplete, debounced autosave (`PUT /workbench/notes/<path>` with `If-Match`
  optimistic concurrency).
- Source view/edit/retract components, import dropzone + status, grounding badge,
  notebook hub page.

**Hydration gotcha:** a generic "replace `article.innerHTML` with live source
content" hook will clobber synthesized prose on research pages. Gate any such
hydration on `content_type` (skip `research_synthesis`); the reference fix keyed
off the workbench `/sources/:id` content type.

---

## 5. Workbench (authenticated edit surface)

Optional but recommended. Deno + Hono, internal port 8000.

### 5.1 Auth model (no client secrets)

```
browser ──(Authelia forward_auth gates the subdomain)──► Caddy
Caddy injects header  X-Brain-Key: {$WORKBENCH_KEY}   (== MCP_ACCESS_KEY)
workbench requireBrainKey middleware validates it; trusts proxied requests
```

Tokens never live in static JS. On a private tailnet listener you may drop
Authelia and keep the key injection (same backends, different listener).

### 5.2 Routes (all under `/workbench`, prefix-preserving)

| Route | Method | Purpose |
|-------|--------|---------|
| `/health` | GET | unauthenticated liveness |
| `/notebooks`, `/notebooks/:id/sources` | GET/POST/PATCH/DELETE | thread CRUD + link/unlink sources |
| `/notes/<path>` | GET/PUT/DELETE | note read/write/trash → vault git commit |
| `/sources/:id` | GET/PATCH/DELETE | read / **update (never destructive replace)** / retract |
| `/sources/:id/revisions` | GET | edit history + diff |
| `/sources/:id/retract`, `/restore` | POST | stage/clear `retracted_at` |
| `/import`, `/jobs/:id` | POST/GET | upload file or URL → extract→chunk→embed→link (async) + status |
| `/grounding` | GET | extraction-queue health / grounding state |

DB access: reads via PostgREST, writes via direct postgres in a transaction
(`withTransaction`) so a multi-row import (source + `source_chunks` +
`thread_sources`) is all-or-nothing. Note writes commit to the vault git repo for
provenance; a nightly trash-empty runs before the nightly compile.

---

## 6. Build / publish gates — the discipline that keeps the wiki up

This section is the reason to read this plan. Quartz cold builds (nightly,
recreate, restart) over a large vault fail in non-obvious ways and, without
gates, publish a broken site.

### 6.1 The completeness gate (non-negotiable)

**Publish only a complete build; serve the last good snapshot meanwhile.**

- Maintain a served snapshot directory separate from the in-progress build
  (the reference viewer serves `/srv/build-N` and atomically swaps).
- Before swapping a build into the served slot, assert **completeness**, not just
  "index.html exists":
  - `is_complete()` — all expected assets present (CSS, prescripts, postscripts,
    icons) — *not* an `index.html`-only check.
  - `index_ok()` — the content/search index file is present **and** well-formed
    (e.g. ends with `}`; a torn multi-MB `contentIndex.json` yields "Unterminated
    string in JSON" at runtime).
- The serve process's "build ready" signal must require the **full asset set**,
  not just `index.html`.

Both gates exist because of real incidents: an asset-less publish (text/plain CSS,
404 prescripts) and a torn 32 MB search index copied mid-rebuild.

### 6.2 Cold-build failure modes to guard

| Failure | Cause | Fix to bake in |
|---------|-------|----------------|
| esbuild "deadlock" / looks like OOM | orphan sweep deletes a leaf mid-build → Quartz `trace()` throws on a vanished file | patch `trace`/emit to **skip ENOENT** (warn, don't throw) |
| 100% CPU hang, never finishes | a giant migrated source (10–17 MB) → regex backtracking in markdown parse | cap leaf body (§4.2, 128 KB); pinpoint with `--concurrency 1 --verbose` |
| port already in use mid-restart | nightly builder restart races the running serve on the build port | wait for the port to be free before building; builder self-heal |
| Explorer/Search entries duplicated | SPA nav appends without clearing on redundant navigation | patch components to `replaceChildren` before re-render |
| dev hot-reload eats unsaved edits | auto-reload fires while NotesEditor is open | guard reload behind an `editing` flag |

### 6.3 Caching gotchas

- Serve assets with `Cache-Control: no-cache` from the viewer; have the proxy
  forward it. Stable-named scripts otherwise get pinned by the browser/CDN and a
  fix "won't take."
- If a CDN fronts the wiki, a broken asset will be cached — purge on fix. Provide
  a **CDN-bypass path** (a tailnet listener straight to the viewer) so you can
  verify a fix without fighting the cache.

### 6.4 Change-watch + schedule

- Debounce KB changes (reference: 3 min idle) before compiling, to coalesce a
  research burst into one compile.
- Nightly full recompile at a fixed local hour; `compile-on-boot` optional.
- Timeouts: full-compile and synthesis-only wall-clock ceilings; on timeout, skip
  the cycle and keep the last good snapshot.

---

## 7. Networks, ports, proxy

| Service | Internal port | Networks | Notes |
|---------|---------------|----------|-------|
| wiki-compiler | 8000 | db-net, llm-net | reads DB, calls LLM/embeddings |
| wiki-viewer | 8080 | db-net, app-net | `app-net` lets the proxy reach it by name |
| workbench | 8000 | db-net, llm-net, app-net | needs DB + embeddings + proxy reach |

Caddy `wiki_app` snippet (shared by public + tailnet listeners):

```caddy
(wiki_app) {
    encode zstd gzip
    @import path /workbench/import /workbench/import/* /workbench/sources/*/replace-from-upload
    request_body @import { max_size 100MB }
    request_body { max_size 1MB }
    handle /workbench/* {
        reverse_proxy workbench:8000 {
            header_up X-Forwarded-Proto https
            header_up X-Brain-Key {$WORKBENCH_KEY}
        }
    }
    handle { reverse_proxy wiki-viewer:8080 { header_up X-Forwarded-Proto https } }
    handle_errors { root * /srv/site; rewrite * /wiki-building.html; file_server { status 200 } }
}
http://wiki.{$PUBLIC_DOMAIN} { forward_auth authelia:9091 {…}; import wiki_app }
:8446 { import wiki_app }   # tailnet, no auth, CDN-bypass
```

The `handle_errors → wiki-building.html` is the public face of "serving last
good snapshot while a compile is in progress."

---

## 8. Configuration highlights

```bash
# wiki-compiler
OPEN_BRAIN_URL=http://<read-api>         # PostgREST base
LLM_BASE_URL=.../v1   LLM_MODEL=<model:nothink>
EMBEDDING_BASE_URL=.../v1  EMBEDDING_MODEL=bge-m3  EMBEDDING_DIMENSION=1024
WIKI_OUT_DIR=/wiki/content   WIKI_GIT_DIR=/wiki
WIKI_RECOMPILE_HOUR=1  TZ=<IANA tz>  COMPILE_ON_BOOT=true
WIKI_WATCH_ENABLED=true  WIKI_WATCH_INTERVAL_MIN=3
WIKI_MAX_SOURCES=5  WIKI_BATCH_LIMIT=1000
WIKI_COMPILE_TIMEOUT_MIN=240  WIKI_SYNTH_TIMEOUT_MIN=120
WIKI_GIT_REMOTE=""             # blank = local commits only (recommended default)

# wiki-viewer
CHOKIDAR_USEPOLLING=true  CHOKIDAR_INTERVAL=5000   # inotify unreliable across docker volumes
NODE_OPTIONS=--max-old-space-size=20480            # large vaults hold all ASTs in memory
QUARTZ_REF=v4.5.1                                  # pin for reproducibility

# workbench
PG_URL=postgres://…@db:5432/…   REST_URL=http://<read-api>
EXTRACT_URL=http://extract:8000  EMBEDDING_URL=.../v1  EMBEDDING_MODEL=bge-m3
VAULT_PATH=/wiki
WORKBENCH_KEY=<== MCP_ACCESS_KEY>
```

Schema additions for the editing/import features are **additive** and applied in
order (revisions, retract columns, source_chunks, import_jobs, content_types).
Same rule as the research service: initdb scripts run only on a fresh volume;
live DBs get them via ordered `psql` with a backup + rollback runbook.

---

## 9. Phased build order

| Phase | Deliverable | Done-when |
|-------|-------------|-----------|
| **P0** | Stable backing IDs in page frontmatter; pinned Quartz ref + overlay scaffold. | A trivial vault builds & serves; component registration is grep-asserted (fails loud if upstream layout changes). |
| **P1** | Source + thought/claim **leaf pages** with the 128 KB cap; citation→wikilink rewrite. | Synthesis pages link to working source leaves; backlinks populate. |
| **P2** | Thread/notebook hub pages; thread slug pinning; graph.json. | Browsing a thread shows its sources/claims; graph view renders. |
| **P3** | Notes editor + `/workbench/notes` (vault git). | In-place edit autosaves and commits; no client secrets. |
| **P4** | Source view/edit/retract + revisions; tombstone filter on every read path. | A retracted+committed source shows a tombstone everywhere; edit history diffs. |
| **P5** | Import pipeline (file/URL → extract→chunk→embed→link) + durable `import_jobs`. | An uploaded PDF becomes a linked, chunked, embedded source; restart-safe. |
| **P6** | Grounding badge / queue health; auth proxy + tailnet bypass. | Public surface gated; tailnet bypass verifies fixes past the CDN. |
| **P-gates** | **Completeness/index gates from §6 — implement alongside P0, not last.** | A deliberately broken build is *not* served; last good snapshot stays up. |

---

## 10. Verification / acceptance

- **Round-trip:** run a research job (other plan) → confirm a wiki page appears
  for the new thread with the synthesis and working source-leaf wikilinks.
- **Completeness gate:** kill a build mid-emit (or remove an asset) → the viewer
  keeps serving the previous snapshot; the broken build is never published.
- **Index integrity:** truncate `contentIndex.json` in a candidate build → the
  gate rejects it; search still works off the last good build.
- **Leaf cap:** add a 10 MB source → its leaf is capped (links out), the build
  completes in reasonable time, CPU doesn't peg.
- **Retraction:** retract + commit a source → tombstone on the leaf and no
  content leakage on any citing page.
- **Workbench auth:** hit `/workbench/sources/:id` directly without the proxy →
  rejected; through the proxy (authenticated) → works. No key in page source.
- **Cache:** deploy an asset fix → verify via the tailnet bypass immediately;
  confirm the CDN path updates after purge.

---

## 11. Relationship to the research service

This wiki is a **read/edit projection** of the same KB the research service
writes. Keep the boundary clean:

- The research service **writes** grounded claims/sources; the compiler **reads**
  them and the workbench **edits** them — but the persist endpoint remains the
  sole writer of *research-originated* data.
- Don't let the wiki invent schema. If you need a new field to render something,
  add it to the KB additively and let both efforts share it.
- Build the research service first; this integration assumes its tables and the
  `source → claim` relationship already exist.
