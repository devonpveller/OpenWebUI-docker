# PLAN — Daily-Digest Podcast & the four agentic services

> **Status:** Plan for later implementation (not started). Distilled 2026-06-08 from
> [initial-podcast-digest-idea.md](initial-podcast-digest-idea.md) (read its **Part II**
> first) against the live 2026-06-07 stack.
> **Companion:** [TASKS-digest-podcast-services.md](TASKS-digest-podcast-services.md) — the phased build.
> **Governing epistemic spec:** [research-engine-for-OB/GROUNDING-MODEL.md](../implementation-guide/research-engine-for-OB/GROUNDING-MODEL.md)
> (Rule #1: no ungrounded claim is stored/served/reused). This plan does not restate it; it consumes it.

---

## 1. Framing — four general services, podcast is the first consumer

This is **not** a podcast feature with some plumbing behind it. It is a proposal for
**four general agentic services** that any future inlet (deep research, the OWUI tool,
Open Notebook, a scheduled job, an autonomous agent) can call. The daily-digest podcast
is simply the **first use case that chains all four end-to-end**, which is why it's worth
building them as services rather than as podcast-internal steps.

| # | Service | General purpose (beyond podcast) | Build state |
|---|---|---|---|
| **S1** | **Research Thread Assigner** | De-fragmentation front door for *any* new knowledge — resolve it onto the best existing thread instead of spawning a new one. | ✅ **Live** (`openbrain-curator`) — reuse, don't build |
| **S2** | **Grounding Backfiller** | Brain-health worker — drain ungrounded claims by fetching grounding sources; raises the whole brain's reusable-claim ratio. | 🟡 Substrate live (claims layer); **worker is new** |
| **S3** | **Digest Link Processor** | Turn a stream of inbound links into grounded, thread-assigned sources + a day report. | 🟡 New stage (reuses S1 + ingest engine) |
| **S4** | **Grounded Podcast Producer** | Assemble a grounded briefing from the claims pipeline and render it to audio via a swappable backend. | 🟡 New orchestration (ON = audio backend only) |

### 1.1 Service-abstraction doctrine (the operator's core requirement)

Every service obeys the workspace's established pattern (the [[research-engine-plan]]
doctrine — "one research brain, many thin inlets"):

- **Thin inlet → shared OB1-side service.** Each service is an OB1-side service/worker
  with a clean HTTP or queue contract. Callers are thin clients.
- **Backend behind an interface.** The three swappable backends in this build:
  1. **Fetch/extract engine** — `ingest_url` today → `openbrain-extract` (Quartz-4 P5) later.
  2. **Audio backend** — Open Notebook today → in-stack TTS (Quartz-4 P7) later.
  3. **Grounding corpus** — Wikipedia today → any authoritative corpus later.
- **Best-effort, never block the email.** Inherits the digest's section-omit philosophy
  ([digest.ts:80-104](../../OB1/recipes/daily-digest/src/digest.ts#L80-L104), `Promise.allSettled`):
  every service fails independently and never delays the 01:00 email.

---

## 2. Locked decisions

Resolved with the operator 2026-06-08. These are fixed inputs to the TASKS.

| ID | Decision | Choice | Consequence |
|---|---|---|---|
| **D1** | v1 scope | **All four services** (S1+S2+S3+S4) | S2 backfiller is a v1 deliverable (new worker + 3-place change), not a fast-follow. |
| **D2** | Podcast format | **Two-host conversational** | Needs **two voices**; the LLM-polish script path is the default (templated reads poorly as dialogue); script is speaker-attributed. |
| **D3** | Episode shape | **One daily episode, segments per label** | `byLabel` grouping on `AiNewsSection` is **required**. Per-thread episodes stay the §6 endgame. |
| **D4** | Link grounding | **Research package (full grounding)** | Each link → curator as `{claim, synthesis, sources}` → claims layer. Pairs with S2; raises curator LLM load (→ R1 sizing). |
| **D5** | **Podcast content ownership** | **Briefing assembled externally via the claims pipeline; ON receives `content` only** | ON is a **pure audio renderer**. We never use ON's `notebook_id` internal-briefing path. Makes grounding enforceable + the P7 swap a one-seam change. |

### 2.1 Recommended technical resolutions (locked unless overridden)

From §7 of the idea doc, resolved as plan defaults:

- **Link hygiene** — start permissive, eyeball the first run; unwrap redirects with a cap.
- **Crawl budget** — ~5 links/email, ~60s/link timeout, global time-box; off-peak.
- **Paywall/robots** — `email-only` fallback, respect robots, **no** retry storm.
- **Dedup across days** — `find_or_create_source` dedups; re-shared links still narrated as "previously seen."
- **Backfiller grounding policy** — Wikipedia = `corroborates` edge @ authority 0.85 (tertiary source); chase Wikipedia's own cited primaries a v2.
- **Backfiller scope** — today's-digest-threads **first** (pre-script), global sweep off-peak.
- **Audio hosting** — ON's `GET /api/podcasts/episodes/{id}/audio` over Tailnet `:8443` is the v1 stable URL; no mp3-copy step.
- **Window** — same as the email (yesterday's pulled emails + today/tomorrow calendar).

---

## 3. Service contracts

### S1 — Research Thread Assigner  →  **reuse the live `openbrain-curator`**

**Do not build.** Live and deployed; already folded the brain 38→25 threads.

```
POST /ingest/research-package        (header: x-brain-key)
  { claim, synthesis?, query?, sources:[{url,title,content,summary,domain}],
    volatility?, topic_hint?, thread_id?(explicit override) }
→ { thread_id, thread_decision:"explicit"|"existing"|"new",
    thread_confidence, thread_name, shortlist:[...], persist:{...}, claims:{...} }
```

- Stage 1 pgvector shortlist over `threads.embedding` → Stage 2 LLM decision (conservative-merge bias).
- Delegates the write to `openbrain-mcp` `/research/persist`; parses `synthesis` → grounded claims+edges.
- **Claims require tagged synthesis.** The curator parses claims **only** from
  `[SOURCED]/[INFERRED] … [Source N]`-tagged `synthesis`
  ([claims.ts](../../OB1/integrations/research-curator/claims.ts)); untagged prose →
  **zero claims**. So a raw article must pass through an **LLM extraction pass first**
  (emit tagged claims citing the article). There is **no claims-free shortcut** for
  this use case: a bare source with no tagged synthesis enters as a source but
  contributes **no claims**, and is therefore invisible to S4's grounded briefing
  (D5). The earlier `source-package` "skip the synthesis" idea is **rejected** on
  exactly this ground.
- **Per D4**, S3 calls this once per ingested link, *after* the extraction pass. The label is passed as `topic_hint` (a bias, not a hard map — supersedes the idea doc's §3.4 static `label→thread` map).

### S2 — Grounding Backfiller  →  **new worker on the live claims layer**

Sibling to `openbrain-entity-worker` / `openbrain-chunk-worker`. Drains the live
`ungrounded_claims` view ([init-claims.sql](../../OB1/docker/init-claims.sql)).

```
loop (event-driven on new ungrounded claim) + bounded off-peak sweep:
  for claim in ungrounded_claims  (today's-digest-threads first, then global):
    entity   ← extract the ungrounded reference from claim.text
    page     ← Wikipedia lookup for entity            # grounding-corpus backend (swappable)
    src_id   ← ingest_url(page)                       # fetch/extract backend (swappable); find_or_create_source dedups
    link_claim_to_source(claim, src_id, 'corroborates', weight)   # claim_confidence trigger recomputes → claim leaves the view
  no-entity → leave ungrounded + log (no retry storm)
```

- Per-run budget cap (pages/claim, total Wikipedia calls). Idempotent (dedup + upsert edge).
- General brain-health: it grounds every inlet's claims, not just the podcast's.

### S3 — Digest Link Processor  →  **new enrich stage + day report**

The idea doc's §3 enrichment, re-pointed through S1. Runs on the **podcast side of the chain only**.

```
for each label email (window = email's window):
  links ← extract → unwrap redirects → hygiene-filter → cap per email   (§3.2)
  for each link:
    content   ← fetch/extract (ingest_url)                              (§3.3, swappable)
    synthesis ← LLM extraction pass → [SOURCED]…[Source 1] tagged claims, citing THIS article
                (MANDATORY — raw content yields 0 claims; tags are what the curator parses; D4/D5)
    pkg       ← { claim: title/summary, synthesis, sources:[link], topic_hint: label }
    resp      ← POST /ingest/research-package  (S1)                     (curator parses synthesis → grounded claims+edges)
    stamp gmail_id/gmail_thread_id/gmail_labels/email_date on the source (§3.4)
    record enrichment status: enriched | email-only | partial           (§3.5, audible)
→ DAY REPORT: [ {source_id, url, label, thread_id, thread_name, status} ... ]   # required input to S4
```

After S3, trigger S2 scoped to the report's `thread_id`s (ground today's claims **before** the script).

### S4 — Grounded Podcast Producer  →  **claim-pipeline briefing → ON as content-only**

**The D5 decision lives here.** ON does **zero** content gathering; we own the briefing.

```
1. BRIEFING ASSEMBLY (external, claim-pipeline-owned):
     threads  ← distinct thread_ids from the S3 day report
     grounded ← reusable_claims scoped to those threads        # grounded + fresh + conf≥0.50
              + the syntheses persisted today (carry [Source N] citations)
     coverage ← per label/segment: grounded vs email-only      # drives the audible caveat
2. SCRIPT (PodcastScriptRenderer — D2 two-host, D3 per-label segments):
     LLM-polish pass over { calendar/weather SectionData, grounded pool, coverage }
     → speaker-attributed script that narrates ONLY grounded claims,
       speaks GAP/email-only caveats, segments per brain/ label.
     Written to /reports next to the markdown audit copy (verify caveats pre-audio).
3. AUDIO (ON = pure renderer):
     POST /api/podcasts/generate { content: <script>, episode_profile, speaker_profile, episode_name }
     poll  GET /api/podcasts/jobs/{job_id}
     fetch GET /api/podcasts/episodes/{id}/audio        # Tailnet :8443 stable URL
4. INGEST + CLOSE THE LOOP:
     AI note ← { body: transcript, refs: source set + audio file }
     for thread in threads: link the note → thread     # one Postgres row (ON↔OB1 shared store)
     note becomes a first-class thread member → wiki-compiler visible (grounds the wiki too)
5. EMAIL LINK-BACK:
     short follow-up / top-of-email block: "🎧 episode (X min) → listen" + "🔗 N/M links ingested"
```

**Why D5 matters:** because the script's only source is the grounded-claims view, an
ungrounded assertion cannot be narrated as fact — it is either grounded first by S2 or
spoken as a flagged gap. Grounding stops being a hope and becomes a query boundary.

---

## 4. Chain topology

Cron fires `pull` only; steps self-chain via `NEXT_TRIGGER_URL` POST
([pull-gmail.ts:1474](../../OB1/recipes/daily-digest/pull-gmail.ts#L1474)).

```
05:00 UTC cron → pull → prune → digest (email out, unchanged, on time)
                                   └─NEXT_TRIGGER_URL→ podcast (NEW openbrain-podcast :8080 /run)
                                                          S3 enrich → S2 backfill(today) → S4 script→audio→ingest→close → email link-back
```

- **New service `openbrain-podcast`** owns S3 + S4 orchestration; returns 202 immediately, runs async, best-effort.
- Adding it = one env var on `openbrain-digest` + the new service + `chainTrigger()` in `send-digest.ts`.
- **3-place change** (the CLAUDE.md rule): OB1 compose (`docker-compose.scheduled.yml`) + `scripts/emergency-recovery.ps1` inventory + the `/stack-map` reference doc. Same for the S2 worker.

---

## 5. How the four compound (the services thesis)

```
link → S1 assign → thread + grounded claims → S2 backfill gaps → S4 narrates grounded material
     → note linked back onto the same threads → next day's research reuses those grounded claims (cheaper) → repeat
```

The reuse economics ([[research-engine-plan]] pillar 3): gather+validate is paid once per
claim, then amortized. With D4+D1 (links become claims, backfiller grounds them), the
brain's `reusable_claims` set grows every night — the metric to watch is
`claims_reused / freshly_gathered` trending up per thread.

---

## 6. Prerequisite — the silent-break risk

⚠️ **Open Notebook ships no default episode/speaker profiles and no seed migration**
([podcast_service.py:47-55](../../open-notebook/api/podcast_service.py#L47-L55)). Generation
throws if either is missing. **P0 must, once, up front:**

1. Register a **TTS Model** (provider + credential) in ON's Model registry pointing at the
   chosen TTS backend (e.g. local OpenAI-compatible `host.docker.internal:8000/v1`).
2. Create a **two-voice speaker profile** (D2) referencing it (`POST /api/speaker-profiles`).
3. Create an **episode profile** (`POST /api/episode-profiles`).

Record the profile names in the `openbrain-podcast` service env so the seam is config, not code.

---

## 7. Risks / sizing

- **R1 — Per-link LLM load (from D4/D5).** Each link incurs an **extraction/synthesis
  pass** + curator Stage-2 (thread decision) + claim parse + conflict detection — N
  links/night of LLM work, contending with TTS + the script pass on `llama-cpp`. 01:00
  off-peak helps; **size it, batch where possible, cap links/email** before full enable.
  The load is **intrinsic to grounding (D5)** — the lever is link volume, **not**
  skipping the synthesis (a claims-free link is invisible to the podcast anyway). Check
  against [[llama-swap-perf-tuning]].
- **R2 — Audio retention.** Episodes accumulate in ON's `./data/podcasts/episodes/`; needs
  a retention sweep + backup coverage.
- **R3 — Extraction quality.** The keep/drop + redirect-unwrap heuristics decide episode
  quality; de-risk by eyeballing the S3 standalone run before automating (P1).
- **R4 — Two-host script quality.** Conversational dialogue from grounded claims needs the
  LLM-polish path tuned; verify the `/reports` script copy before any audio.

---

## 8. Relationship to existing work

- **Reuses live infra:** `openbrain-curator` ([[research-curator-inlet-service]]),
  claims layer + `openbrain-research` ([[research-engine-plan]]), the IKS-cutover
  OB1-backed Open Notebook ([[iks-cutover-live]]).
- **Consistent with the roadmap:** ON-as-audio-backend is the Quartz-4 D-B/D-H interim;
  the S4 audio seam is the P7 cutover point. The grounding model is the research-engine's
  GROUNDING-MODEL.md.
- **Does not block** the eventual ON decommission (Quartz-4 §10) — S4's briefing builder is
  backend-agnostic by construction (D5).
