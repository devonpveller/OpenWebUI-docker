# Repo → OB → Sources wiring — onboarded repos as primary sources

**Status:** 📐 DESIGN (not built) — 2026-07-05
**Extends:** [PLAN-research-engine.md](PLAN-research-engine.md) (harness/staging/reuse),
[GROUNDING-MODEL.md](GROUNDING-MODEL.md) (the rubric — unchanged by this design)
**Consumer:** agent-org (the advisory lane) + every other research inlet

---

## 1. Why (the live gap this closes)

Two live findings from the murder-structure question (2026-07-04/05):

1. **The advisory lane returned `[GAP]`** — correctly: its gather stage searches the *web*, and
   search engines don't reliably surface a repo's README/docs/manifests. The claim-check discipline
   held; the **source corpus** just couldn't see the repo.
2. **The worker-investigation lane answered well** (it read the actual repo — primary source) but
   produced **no claims, no citations, no accretion**: the answer lives only in a chat thread. It
   doesn't enter the wiki (the after-action record) and can't be reused or claim-checked later.

The purpose of source→claim (operator-stated): **collect retrievable knowledge over time** (wiki +
sources) and **remove user-framing bias** by objective claim-vs-source comparison without the goal
in context. Repo questions currently get neither. This wiring gives repo questions the full
treatment: the repo's own files become **primary sources** in OB, so the advisory lane answers them
with `states`-edged claims — and the knowledge accretes.

---

## 2. Principle mapping (nothing new in the rubric)

| GROUNDING-MODEL concept | Repo wiring |
|---|---|
| **Source** (terminal trust, primary) | a repo FILE at a pinned commit — `README.md@<sha>`, `docs/*.md@<sha>`, `.gitmodules@<sha>`, `*.sln`/`*.csproj@<sha>`. Provenance = canonical URL `https://github.com/<o>/<r>/blob/<sha>/<path>`. |
| **Claim → `states` edge** | "a murder game vendors the engine as a submodule at `murder/`" ← *states* ← `hellomurder/.gitmodules@abc123`. Structural manifests are unusually strong sources: they don't editorialize. |
| **Freshness / `revalidate`** | keyed to the repo's HEAD sha, not wall-clock: a source is fresh while its repo's default branch still contains that blob; a moved HEAD triggers re-sync (see §5 triggers). |
| **`[GAP]` honesty** | unchanged — if the repo docs don't answer it, the gap stays a gap. |
| **Injection defense** | repo docs are third-party text (upstream READMEs!) → they pass the SAME `detectInjection`/`screenSources` quarantine as web sources. No exemption. |

No change to enforcement (§6 of the rubric). Repo files simply join the corpus as first-class
primary sources.

---

## 3. Architecture — engine-pull, thin trigger (per PLAN §3.1/§3.2)

The harness stays OB-side; the bridge stays a thin inlet. The engine gains a **repo source class**
in its staging layer; agent-bridge only *tells it when* to sync.

```
agent-bridge (thin trigger)                    openbrain-research (owns the work)
────────────────────────────                   ──────────────────────────────────
/project add · D4 merge · manual  ──POST──▶    /sources/repo-sync {repo_url, ref?}
                                               1. resolve HEAD sha (public: raw fetch;
                                                  private: read-scoped token, held engine-side)
                                               2. enumerate candidate files (§4 selection)
                                               3. fetch raw@sha → extract → detectInjection →
                                                  stage (sessions/session_sources — the existing
                                                  candidate pool)
                                               4. promote via find_or_create_source (url pinned
                                                  to sha; skip unchanged blobs — idempotent)
                                               5. link_source_to_thread → per-repo thread
                                                  ("repo: <slug>") via the curator
```

- **Why engine-pull, not bridge-push:** PLAN §3.2 — inlets submit requests only; fetch/extract/
  screening/promotion are harness capabilities that already exist engine-side (SmolCrawl/extract,
  screenSources, staging tables). A bridge-push path would duplicate extraction + bypass screening.
- **Auth:** public repos need none (raw.githubusercontent). Private repos: a **read-scoped token
  env on the engine** (`RESEARCH_REPO_TOKEN_<OWNER>`, mirroring the bridge's per-owner convention).
  The GitHub App's tokens stay in the bridge — the App key is never shared across services.

---

## 4. Source selection (bounded, deterministic)

Per repo, per sync — a fixed, auditable selection, not a crawl:

- `README*` (root + one level deep), `docs/**/*.md`, root `*.md` (LICENSE excluded)
- structural manifests: `.gitmodules`, `*.sln`, `Directory.Build.props`, root-level `*.csproj`
  globs (per-repo override possible later, like `check_cmd`)
- caps: ≤ 40 files/repo, ≤ 128 KB/file (the wiki leaf-cap lesson), text-only (no binaries)
- anything skipped is **logged in the sync result** (no silent truncation)
- **upstreams too**: an onboarded fork syncs its registered `upstream_url` docs as well — that's
  where the real documentation usually lives (murder's docs live in isadorasophia/murder, not the
  fork). Same caps; same screening (upstream text is exactly the third-party injection surface).

---

## 5. Sync triggers (the bridge knows the moments)

| Trigger | Why | Call |
|---|---|---|
| `/project add` (+ NL onboarding) | new repo → its docs should be knowledge immediately | fire-and-forget `repo-sync` |
| **D4 merge to main** | main moved → docs/manifests may have changed; D6 already announces this moment | `repo-sync {ref: new main sha}` |
| manual: NL "sync <project> docs" / `/project sync <name>` | operator control | same |
| (deliberately NOT on every branch push) | branches are work-in-progress, not knowledge; only merged main is after-action truth | — |

Idempotent by blob sha — an unchanged file re-syncs to a no-op.

---

## 6. Query-side: how repo questions reach the repo sources

1. **Reuse stage first** (PLAN §6): repo-question claims land in the KB → the next similar question
   reuses them cheaply. This is the accretion the operator wants (after-action review → wiki via
   the curator's per-repo thread).
2. **Gap staging includes repo sources**: the engine's stage step already searches its own staged/
   promoted sources; repo files are simply *in* the pool now. A repo question like the murder one
   finds `.gitmodules` + README as candidate sources without any web fetch.
3. **De-bias (operator-specified, optional flag `neutralize: true`)**: the advisory inlet may pass
   the operator's question through the bridge's `_NEUTRALIZE_SYS` rewrite before `POST /research` —
   the claim-check is already objective, but a neutral query also de-biases the *gather/plan*
   stage's search terms. Shown to the operator either way (transparency).

---

## 7. Phasing (each step independently useful)

| Phase | What | Where |
|---|---|---|
| **RS.1** | `/sources/repo-sync` endpoint: enumerate → fetch@sha → screen → stage → promote → link to per-repo thread. Manual trigger only. | openbrain-research (code change in the existing service; **no new container** — R-rule: no 3-place change) |
| **RS.2** | bridge triggers: on `/project add` + on D4 merge + NL "sync <project> docs". Fire-and-forget + result posted to the project channel (transparency). | agent-bridge |
| **RS.3** | advisory preference: a question naming an onboarded project gets `scope: repo:<slug>` (prioritize that repo's sources in reuse/stage) + optional neutralized query. | agent-bridge + engine |
| **RS.4** | accretion check: repeated repo questions must show `reuse_ratio > 0` (the economics of PLAN §6 visibly working); surfaced in the advisory answer footer ("reused N grounded claims"). | engine |

**Done-when (RS.1+RS.2):** the murder-structure question, asked through the advisory lane, returns
a **cited synthesis** whose claims carry `states` edges to `README.md@sha` / `.gitmodules@sha` —
no `[GAP]` — and the claims are visible on the "repo: murder" thread (wiki-ready).

---

## 8. Explicitly out of scope

- Ingesting **source code** wholesale (only docs + structural manifests; code Q&A stays the
  worker-investigation lane — code is better read in-workspace than claim-atomized).
- Real-time sync on every push (only merged main).
- Sharing the GitHub App key with the engine (read tokens only, engine-side env).
