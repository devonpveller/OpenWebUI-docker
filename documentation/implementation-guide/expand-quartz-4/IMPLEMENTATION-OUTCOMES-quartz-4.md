# Implementation Outcomes — Quartz 4 Expansion (live deployment)

**Session date:** 2026-06-06
**Scope:** final feature refinements + **first live deployment of the entire
Quartz-4 expansion to the production `ai-stack`**, staying on
`feature/integrated-knowledge-system` (NO merge to `main` — here "merge" means
*deploy the validated work to the live stack*, per the operator's clarified
intent).

This document is the authoritative record of what changed today, the verified
state of production after it, and everything still outstanding. Read it
alongside [quartz-4-expansion-plan.md](quartz-4-expansion-plan.md),
[TASKS-quartz-4-expansion.md](TASKS-quartz-4-expansion.md),
[PROMOTION-RUNBOOK-quartz-4.md](PROMOTION-RUNBOOK-quartz-4.md), and
[MERGE-PREP-quartz-4.md](MERGE-PREP-quartz-4.md).

---

## 0. TL;DR

The Quartz-4 workbench is **live in production** end-to-end: schema migrated
(data intact), all OB1 containers rolled onto the new images, the 38 notebooks
backfilled + hub pages synthesized, and `/workbench` reachable **both** via the
external portal (Authelia) **and** the tailnet (Tailscale device-auth) through a
shared, security-verified Caddy routing layer. A genuine pre-existing worker bug
(poison extraction queue) was found and fixed. A full wiki recompile is running
to regenerate every page with the new compiler features and clear retired-layer
links.

**Not done (safe to continue):** the running full recompile must finish;
`config/caddy/Caddyfile` + `docker-compose.yml` edits are **uncommitted** (git
boundary — operator commits); `ob-preview` teardown; `source_chunks` backfill
(IKS-side); the OD-1 purge-semantics decision (gates the P8 history MCP); the
X.2 `wiki-assets` backup job.

---

## 1. Session phase A — final feature refinements (pre-deploy)

These closed out the in-preview UX work before promotion.

### 1.1 Unified RevisionHistory — deliberate-commit model fixed
- **Done no longer auto-commits.** Editing a note now leaves a **working draft**;
  commits are deliberate ("Commit now") or caught at the next compile. This is
  what surfaces the **"Uncommitted changes"** entry + the **"Discard edit"**
  button (previously the auto-commit-on-Done meant there was never an uncommitted
  state to discard). — `scripts/NotesEditor.inline.ts` (exitEdit saves without
  `commit=1`), `RevisionHistory.tsx` (Discard label, note re-render after revert).
- **Revert now takes visible effect** on notes (it always worked at the backend;
  the issue was Done-auto-commit + an immediate stale reload).

### 1.2 Read-only revision history on generated wiki pages
- Entity (`type: wiki`), notebook hub (`type: notebook`), and thought-leaf
  (`type: thought`) pages now show a **read-only** RevisionHistory card — the
  git log of each compile that changed the page, with the same line diffs but
  **no commit/revert/discard** (the compiler owns these; a revert would be
  overwritten next compile).
- Backend: `note-history.ts` gained `readPath()` (serves git history for ANY
  vault `.md`, not just `notes/`); the write ops (commit/revert/discard) stay
  notes-only. Frontend: `RevisionHistory.tsx` gained a third `kind="wiki"`
  (read-only adapter), gated on `fm.type`, ref = `${slug}.md` (slugs already
  include the `content/` prefix).

### 1.3 Planning artifacts
- **MERGE-PREP-quartz-4.md** authored (two-repo scope, migrations, rollout,
  teardown).
- **OD-1/OD-2 open decision** logged in the plan §12.5 + memory
  ([purge-erasure-open-decision](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/purge-erasure-open-decision.md)):
  purge = *suppression vs crypto-shred true-erasure*; it **gates** the read-only
  wiki-history MCP (P8 — extend `thought search`, read-only, no write tools).

---

## 2. Session phase B — live deployment to production

### 2.1 Prod baseline finding (important)
Read-only introspection of live `openbrain-db` revealed prod was at the
**pre-IKS-Phase-1 baseline**: core schema present (`sources` 777, `thoughts`
11,574, `entities` 25,329, …) **but the entire threads/notebooks substrate was
absent** (`threads`, `thread_sources`, `sessions`, `session_sources`,
`sources.content_hash`, `find_or_create_source`). `init-threads.sql` had never
run on the live volume (initdb scripts only run on a fresh volume — classic G3
gap). **Consequence:** the documented "7 migrations" were insufficient —
`init-threads-slug.sql` would fail (`relation "threads" does not exist`). The
**PROMOTION-RUNBOOK was corrected** to add `init-threads.sql` as **STEP 0** (8
files total). See memory
[prod-openbrain-pre-iks-baseline](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/prod-openbrain-pre-iks-baseline.md).

### 2.2 Migration — done right (backup → rehearsal → live)
1. **Backup** via the production mechanism (`openbrain-db-backup`'s `backup.sh`),
   not ad-hoc: `backups/openbrain-db/openbrain-20260606T152615Z.dump`
   (68 MB, **sha256 OK**, 338 objects).
2. **Rehearsal**: restored that backup into a throwaway `pgvector:pg16` container
   and ran the full 8-migration sequence against a **faithful copy of real
   data** — all clean, **idempotent** (re-ran with no dup seed rows), data
   intact. This proved the live apply before touching prod.
3. **Live apply** (operator-authorized; G10 normally reserves this for the
   operator): all 8 migrations applied cleanly, all 12 objects verified present,
   old `content_type` CHECK gone, **`sources` 777 / `thoughts` 11,574 unchanged**.

Migration order (now in the runbook): `init-threads` → `init-threads-slug` →
`init-source-revisions` → `init-source-retract` → `init-content-types` →
`init-source-chunks` → `init-import-jobs` → `init-source-editing`.

### 2.3 Container rollout
Rebuilt + rolled 5 OB1 images from the branch:
`openbrain-wiki`, `openbrain-wiki-viewer`, `openbrain-workbench` (NEW),
`openbrain-extract` (NEW), `openbrain-entity-worker`. Workbench `/health` →
`{ok, db:true, rest:true}`; extract exposes 22 formats; both healthy.

### 2.4 Portal wiring
`WORKBENCH_KEY` added to the **main** repo `.env` (operator did this; == OB1
`MCP_ACCESS_KEY`) so the portal Caddy injects `X-Brain-Key` for `/workbench/*`.
Portal Caddy recreated to pick it up.

### 2.5 Poison-queue bug — found + fixed (root cause)
The first wiki compile appeared to "hang." Root cause: the pre-compile worker
**source drain looped for 30 minutes** (`WORKER_DRAIN_MAX_MIN` deadline) on **3
empty-content sources**. The bug: `entity-extraction-worker/index.ts` passed a
**hardcoded `attempt_count=0`** in both early bail-out paths (line 724 sources,
line 899 thoughts), so `markSourceError`/`markError` always recomputed
`0+1=1 < MAX_ATTEMPTS(5)` → status reset to `pending` forever → never reached
`failed` → re-picked on every drain. **Fixed** both paths to read the real
`attempt_count` before the content check (+ accurate "Source has empty content"
message instead of the misleading "Source not found"); rebuilt the worker; the 3
sources are now terminal `failed`, drain returns instantly.

### 2.6 Notebook backfill
The full compile (after the drain fix) backfilled **38 `threads`** (slug'd) +
**777 `thread_sources`** links + **38 hub pages** (`content/notebooks/<slug>/`,
LLM-synthesized). The Notebooks feature is net-new to prod (it rode in with the
IKS-P1 schema).

### 2.7 Tailnet `/workbench` exposure — shared Caddy routing
The tailnet wiki (`:8444`) socat'd straight to the viewer (raw TCP) → `/workbench`
404'd (no Caddy → no key injection). Fixed with the operator's architecture:
**Authelia > Caddy > app** *and* **Tailnet > Caddy > app**, sharing one backend.
- **`config/caddy/Caddyfile`**: extracted backend routing into a shared snippet
  **`(wiki_app)`** (body-cap + `/workbench/*` key-inject + viewer + rebuilding
  page). Public vhost = `forward_auth` (Authelia) + `import wiki_app`; new
  **`:8446`** listener = `import wiki_app`, **no Authelia** (Tailscale
  device-auth is the boundary). Applied via graceful `reload`.
- **`docker-compose.yml`** tailscale service: `QUARTZ_HOST=caddy`,
  `QUARTZ_PORT=8446` → entrypoint builds socat `:8239 → caddy:8446`; tailscale
  recreated → **persistent** (survives restarts; monitor loop maintains it).
- **Security verified (no shortcut):** external `/` AND `/workbench/*` still
  302→Authelia for unauth; `:8446` is not host-published, no Cloudflare ingress →
  tailnet + internal only. Trade-off: tailnet edits have no `Remote-User` →
  authorship falls back to `"operator"` (portal keeps the Authelia identity).
- **Health/recovery: no script changes needed** — `check-tailscale-health.ps1`
  validates `:8444 → :8239` (unchanged); `emergency-recovery.ps1` doesn't restart
  caddy; the new caddy dependency is *soft* + self-heals via the entrypoint
  deferred-setup loop. See memory
  [tailnet-wiki-workbench-caddy-proxy](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/tailnet-wiki-workbench-caddy-proxy.md).

### 2.8 Full recompile (running)
Watermark removed → `POST /recompile` → **full rebuild in progress** to
regenerate every entity page with the new compiler features (Evolution, notebook
graph nodes, leaf pages, citation rewrites) and clear the retired `/topic/*`
layer links.

---

## 3. Files changed this session (all UNCOMMITTED — operator's git boundary)

**OB1 repo** (auto-commit hook may have staged some):
- `docker/wiki-viewer/quartz-overlay/quartz/components/RevisionHistory.tsx`
- `docker/wiki-viewer/quartz-overlay/quartz/components/scripts/NotesEditor.inline.ts`
- `docker/workbench/src/routes/note-history.ts`
- `integrations/entity-extraction-worker/index.ts`  ← poison-queue fix

**ai-stack repo:**
- `config/caddy/Caddyfile`  ← `(wiki_app)` snippet + `:8446` tailnet listener
- `docker-compose.yml`  ← tailscale `QUARTZ_HOST=caddy` / `QUARTZ_PORT=8446`
- `.env`  ← `WORKBENCH_KEY` (operator added; gitignored)
- docs: this file, `PROMOTION-RUNBOOK` (STEP 0), `MERGE-PREP`, plan §12.5

---

## 4. Verified production state (end of session)

| Area | State |
|---|---|
| `openbrain-db` schema | 8 migrations applied; data intact (777 / 11,574) |
| OB1 containers | wiki, viewer, workbench, extract, entity-worker — rolled, healthy |
| Notebooks | 38 threads + 777 links + 38 hub pages |
| Extraction queue | 774 complete, 3 failed (terminal), 0 pending |
| Portal `/workbench` (devinveller.ai) | live, Authelia-gated |
| Tailnet `/workbench` (`:8444`) | live, device-auth + key-injected, persistent |
| Full recompile | **running** |

---

## 5. Outstanding items & remaining tasks (safe to continue)

1. **Full recompile completion** — running now. When done: stale `/topic/*` links
   gone, all pages carry the new compiler features. Verify a sample entity page +
   that `/topic/*` 404s are resolved.
2. **Commit the changes** (operator) — `config/caddy/Caddyfile`,
   `docker-compose.yml`, the OB1 worker/component edits, and the docs are
   uncommitted by design (G1). Nothing is pushed.
3. **Tear down `ob-preview`** — `docker compose -f
   OB1/docker/docker-compose.preview.yml down -v`; keep the unit tests.
4. **`source_chunks` backfill (IKS-side)** — `source_chunks=0`; the 736 chunkable
   sources are unchunked, so ON retrieval (chat/ask) is empty until the
   chunk-embedding worker runs against prod. See
   [iks-pending-work-plan](../../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/iks-pending-work-plan.md).
5. **OD-1 purge semantics** (plan §12.5) — decide suppression vs crypto-shred;
   **gates the P8 read-only wiki-history MCP**.
6. **X.2 backups** — add the `wiki-assets` volume to the backup set (binary import
   assets; purged sources drop assets, retracted keep them).
7. **Tailnet authorship** — edits via the tailnet attribute to `"operator"` (no
   Authelia identity). Acceptable for a single operator; revisit if multi-user.
8. **Podcasts (P7)** — still deferred (ON keeps serving them).

---

## 6. Known gotchas captured (for the next session)

- `busybox wget` (tailscale/caddy containers) lacks `--header` / `--max-redirect`;
  `localhost` → IPv6 but socat is IPv4 — test via `127.0.0.1`.
- PowerShell mangles embedded quotes in `docker exec sh -c '…'`; prefer Bash or a
  copied script file for anything with inner quotes / `$()`.
- Forcing a FULL wiki compile = remove `/wiki/.wikistate.json` then
  `POST /recompile` (key via `x-brain-key` header from `$MCP_ACCESS_KEY`).
- Migrations are mounted in compose but **only run on a fresh volume** — live DBs
  need the runbook's manual psql apply (G3).
