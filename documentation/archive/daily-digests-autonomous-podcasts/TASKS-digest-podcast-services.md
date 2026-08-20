# TASKS — Daily-Digest Podcast & the four agentic services

> **Companion to** [PLAN-digest-podcast-services.md](PLAN-digest-podcast-services.md). Read the PLAN first — locked decisions D1–D5 and the service contracts are not restated here.
> **Status:** Not started. Every phase is additive + reversible. Tick boxes as built.
> **Convention reminders:** Local llama-cpp only (`qwen…:nothink` for high-volume, think model for synthesis). Adding/removing a container = **3-place change** (OB1 compose + `scripts/emergency-recovery.ps1` inventory + `/stack-map` ref doc). Schema changes are additive + **operator-applied** (backup → rehearse → live). **Never commit on the operator's behalf.**

---

## Build order at a glance

```
P0 ON profiles (prereq, silent-break risk)
P1 S3 link processor — standalone, eyeball-first        ─┐ de-risk extraction (R3)
P2 S2 grounding backfiller — worker                      │
P3 S4a briefing builder + PodcastScriptRenderer → /reports ─┘ de-risk script (R4) — no audio yet
P4 S4b ON audio + ingest note + thread closure          ── de-risk ON seam
P5 openbrain-podcast service + chain wiring + email link-back
P6 cross-cutting: retention, curator-load sizing, docs/recovery
```

The first three phases produce **inspectable artifacts with no automation and no audio**, so the two real unknowns (extraction quality, two-host script quality) are validated before anything is wired into the 01:00 chain.

---

## P0 — Prerequisite: Open Notebook profiles  *(operator + agent)*

The single thing that silently breaks the first automated run if skipped (PLAN §6).

- [ ] **P0.1** Register a **TTS Model** in ON's Model registry pointing at the chosen TTS backend (local OpenAI-compatible `host.docker.internal:8000/v1` or equivalent). Capture provider + credential.
- [ ] **P0.2** Create a **two-voice speaker profile** (D2 two-host) referencing the TTS Model — `POST /api/speaker-profiles` (or UI). Confirm `resolve_tts_config()` succeeds.
- [ ] **P0.3** Create an **episode profile** — `POST /api/episode-profiles`.
- [ ] **P0.4** Smoke: `POST /api/podcasts/generate { content:"<10-line test script>", episode_profile, speaker_profile, episode_name }` → poll job → retrieve audio over Tailnet `:8443`. **Confirms headless content-only path end-to-end with two voices.**
- [ ] **P0.5** Record the profile names somewhere the `openbrain-podcast` service will read them (env), so the binding is config not code.

**Acceptance:** a hand-written test script produces a two-host audio file retrievable via the ON audio route.

---

## P1 — S3 Digest Link Processor (standalone, eyeball-first)  *(agent)*  ✅ BUILT + DRY-RUN VALIDATED 2026-06-08

Reuses the live curator (S1) — no new service yet; run as a standalone script against one real day.
Code: [`OB1/recipes/daily-digest/link-enrich.ts`](../../OB1/recipes/daily-digest/link-enrich.ts) (runner, **dry-run by default**) + [`src/enrich/`](../../OB1/recipes/daily-digest/src/enrich/) (`types`, `email-body`, `links`, `extract`, `synthesize`, `curator`, `egress`). All `deno check --unstable-net` clean. Nothing committed (G1).

- [x] **P1.1** `byLabel` grouping on [AiNewsSection](../../OB1/recipes/daily-digest/src/sections/ai-news.ts) (additive map next to `bySender`; email unchanged). Labels come through as readable names (`brain/ai/nate b jones`); new `brain/*` auto-discovered. Label→display prettify deferred to S4.
- [x] **P1.2** Link extraction + hygiene + redirect unwrap ([links.ts](../../OB1/recipes/daily-digest/src/enrich/links.ts)): extract inline URLs, unwrap Substack/beehiiv `/redirect/` wrappers to the real destination, drop noise (unsubscribe/`/action/`/`/subscribe`/social/mailto/assets), dedup on final URL, cap per email. *Eyeball-tuned: `/action/disable_email` + `/subscribe` now dropped pre-fetch.*
- [x] **P1.3** Fetch/extract ([extract.ts](../../OB1/recipes/daily-digest/src/enrich/extract.ts)) — self-contained `extractTextFromHtml` + `<title>`, time-boxed (~60s). **Egress through Tor** (see note below), not `ingest_url`, so fetch stays decoupled from a brain write.
- [x] **P1.4** **Synthesis-then-package (D4/D5/D6).** [synthesize.ts](../../OB1/recipes/daily-digest/src/enrich/synthesize.ts) runs a single-source extraction pass (`SINGLE_SOURCE_SYNTH_SYS`, nothink) → tagged `[SOURCED] … [Source 1]` claims; package POSTed via [curator.ts](../../OB1/recipes/daily-digest/src/enrich/curator.ts). **Validated:** real articles produced 13–19 well-formed grounded claims + correct uncited `[GAP]` lines, no fabrication. *Curator POST is commit-only (operator-gated); dry-run previews the package.*
- [x] **P1.5** Stamp `gmail_id`/`gmail_thread_id`/`gmail_labels`/`email_date` on each ingested source — **commit-only** via `BrainClient.mergeSourceMetadata` (curator package has no metadata passthrough). ⚠️ Assumes a writable `sources.metadata` jsonb — **verify live schema before relying on the join.**
- [x] **P1.6** Per-link status `enriched | email-only | previously-seen` recorded on the report.
- [x] **P1.7** **DAY REPORT** (`DayReportEntry[]` + totals) written to `/reports` as JSON + markdown.
- [x] **P1.8** Respect robots (Substack `/p/` allowed, `/action/`+`/subscribe` blocked — robots does NOT cost real content); paywall/403/stub → `email-only`, no retry storm. *Dedup-across-days (`previously-seen`) relies on curator `find_or_create_source`; explicit pre-check deferred.*

**Acceptance — PASSED (dry-run, 2026-06-08):** 2 real Substack newsletters → both article links enriched (18/15 grounded claims), all noise correctly `email-only`/dropped, `no-links=0` (text/plain carried URLs). Report at `OB1/recipes/daily-digest/.eyeball/` (scratch, untracked). **R3 gate cleared.** Remaining before COMMIT: verify `sources.metadata` schema (P1.5); exercise the curator POST on one link with operator approval (writes to live brain).

> **NEW capability added in P1 — page-fetch through Tor.** The stack previously Tor-routed only *search* (SearXNG); page fetches went direct. P1 adds [egress.ts](../../OB1/recipes/daily-digest/src/enrich/egress.ts): external article/robots/redirect fetches go through the existing `tor` SOCKS5 proxy (`socks5://tor:9050`) via `Deno.createHttpClient` (needs `--unstable-net`). **Privacy-by-default + fail-closed** (Tor unreachable → `email-only`, never a direct leak; proven: direct fetch fails on internal `search-net`, Tor fetch returns `IsTor:true`). The runner container must join `ai-stack_search-net` + `open-brain_obnet` + `ai-stack_llm-net`. This is the general "private page fetch" seam the rest of the stack lacked.

---

## P2 — S2 Grounding Backfiller worker  *(agent + operator for deploy)*  ✅ BUILT + DEPLOYED + VERIFIED 2026-06-10

New worker on the live claims layer. General brain-health, not podcast-specific.
Code: [`OB1/integrations/grounding-backfiller/`](../../OB1/integrations/grounding-backfiller/) (`index.ts` + `deno.json` + `Dockerfile`). Deployed as `openbrain-grounding-backfiller` (loopback `127.0.0.1:8819`). Nothing committed (G1).

- [x] **P2.1** Worker scaffold (Deno + deno-postgres `Pool`, sibling to chunk-worker), loopback `:8819`; reads `ungrounded_claims` ([init-claims.sql](../../OB1/docker/init-claims.sql)). Routes: `GET /health`, `POST /backfill?limit=N {thread_ids?}`.
- [x] **P2.2** Drain loop: per ungrounded claim → **entity extraction (one local `:nothink` LLM call)** → Wikipedia search + REST summary (grounding-corpus backend, swappable; **via Tor**, D10) → `find_or_create_source` (dedup) → `link_claim_to_source(claim, src, 'corroborates', 0.7)`. Verified: `claim_confidence` trigger recomputed (0 → 0.765, authority 0.85) and the claim **left** `ungrounded_claims` (`claim_min_depth` 0).
- [x] **P2.3** Scope control: `thread_ids` (scoped) + global; budget caps (`BACKFILL_BATCH`, `?limit`, `BACKFILL_CONCURRENCY`). **Off-peak GLOBAL sweep wired in cron** (07:00 UTC, after the daily chain). **NOTE / deliberate deviation:** the podcast-scoped *pre-script* trigger was **skipped** — the episode renders from the research **synthesis**, not a `reusable_claims` re-query, so backfilling wouldn't change it; the global sweep covers today's threads anyway, so a scoped trigger would be a redundant no-op in the hot path.
- [x] **P2.4** No-entity / no-Wikipedia-match → stamp `metadata.backfill_skip=true` + log; **never retry-storm** (the drain query excludes `backfill_skip` claims; clear the flag to retry). Idempotent (grounded claims leave the view).
- [x] **P2.5** **3-place change**: OB1 compose (`docker-compose.yml`, search-gw-net for Tor) + `emergency-recovery.ps1` inventory + `/stack-map` ref doc.
- [x] **P2.6** Operator: deployed (built `:local` image, brought up, healthy). No schema change needed (claims layer already live).

**Acceptance — PASSED (2026-06-10):** seeded one ungrounded test claim ("Anthropic is an AI safety and research company…") → ran the worker → entity "Anthropic" → Wikipedia page linked as `corroborates` (weight 0.7, `en.wikipedia.org`) → confidence `0 → 0.765`, `claim_min_depth` `NULL → 0`, dropped out of `ungrounded_claims`. Test claim + source cleaned up (brain back to 0 ungrounded).

---

## P3 — S4a Briefing builder + PodcastScriptRenderer → /reports (no audio)  *(agent)*

The **D5** decision: briefing assembled externally from the claims pipeline; ON not involved yet.

- [ ] **P3.1** **Briefing builder** (claim-pipeline-owned): from the S3 day report, resolve distinct `thread_id`s → pull `reusable_claims` scoped to those threads + the syntheses persisted today (with `[Source N]` citations). Compute per-segment **coverage** (grounded vs email-only).
- [ ] **P3.2** `PodcastScriptRenderer` beside the Html/Markdown renderers. Input: `{calendar/weather SectionData, grounded pool, coverage}`. Output: a **two-host (D2), speaker-attributed** script, **one segment per label (D3)**, morning-show structure (§4.2): cold-open → calendar/travel → per-label feeds → action-items → sign-off.
- [ ] **P3.3** **LLM-polish path is the default** (D2): one `qwen…:nothink` pass over the grounded data → natural dialogue. Templated assembly remains the deterministic fallback.
- [ ] **P3.4** **Grounding boundary (enforce):** the renderer narrates **only** grounded claims; ungrounded/email-only segments get the **audible caveat** ("…based only on the newsletter summary; I couldn't open the linked article"); `partial` notes the mix.
- [ ] **P3.5** Write the script to `/reports` next to the markdown audit copy.

**Acceptance:** read the `/reports` script for a real day — natural two-host dialogue, correct per-label segments, **every caveat present where coverage is email-only**, no ungrounded assertion stated as fact. **Gate R4 before audio.**

---

## P4 — S4b ON audio + ingest note + thread closure  *(agent + operator)*

- [ ] **P4.1** POST the P3 script as `content` to ON `POST /api/podcasts/generate` with the P0 profiles; poll `GET /api/podcasts/jobs/{id}`; retrieve audio. **(Never the `notebook_id` path — D5.)**
- [ ] **P4.2** **Ingest the podcast as an AI note** into Open Brain: body = transcript, referencing the source set + the audio file URL.
- [ ] **P4.3** **Thread closure:** link the note → **all** `thread_id`s from the S3 report (one Postgres row each; ON↔OB1 shared store). Confirm the note becomes a first-class thread member (wiki-compiler visible).
- [ ] **P4.4** Behind the audio **interface seam** so the Quartz-4 **P7** in-stack TTS swap is a backend change only.

**Acceptance:** a real day's script → audio → an AI note linked to the day's threads → the note appears in the wiki compile.

---

## P5 — `openbrain-podcast` service + chain wiring + email link-back  *(agent + operator)*

Now automate; everything above ran by hand.

- [ ] **P5.1** New **`openbrain-podcast`** Deno service (`:8080 POST /run`, returns 202, async, best-effort) owning the S3 → S2(today) → S4 pipeline. Reads the digest output + P0 profile names from env.
- [ ] **P5.2** `chainTrigger()` in [send-digest.ts](../../OB1/recipes/daily-digest/send-digest.ts) (pattern from [pull-gmail.ts:1474](../../OB1/recipes/daily-digest/pull-gmail.ts#L1474)); set `NEXT_TRIGGER_URL: http://openbrain-podcast:8080/run` on `openbrain-digest`.
- [ ] **P5.3** Confirm best-effort isolation: the podcast step **never** delays/fails the email; a hung crawl/dead link/ON timeout degrades gracefully (status flags + section-omit).
- [ ] **P5.4** **Email link-back:** top-of-email block "🎧 episode (X min) → listen" (Tailnet `:8443` audio URL) + "🔗 N/M links ingested" status. Decide sync (link fills in) vs short follow-up email (§5 — default: decoupled, link resolves when ready).
- [ ] **P5.5** **3-place change** for `openbrain-podcast`: OB1 compose + `emergency-recovery.ps1` inventory + `/stack-map`.
- [ ] **P5.6** Operator: deploy; run one live 01:00 cycle (or a manual chain trigger) end-to-end.

**Acceptance:** a real chain run — email out on time, episode produced, note linked, email link-back present — with the email demonstrably unaffected by an induced enrichment failure.

---

## P6 — Cross-cutting  *(agent + operator)*

- [ ] **P6.1 — R1 curator-load sizing:** measure N-links/night curator Stage-2 + claim-parse + conflict cost against the off-peak GPU budget ([[llama-swap-perf-tuning]]); batch the shortlist if needed **before** enabling at full link volume.
- [ ] **P6.2 — R2 retention:** retention sweep for `./data/podcasts/episodes/` + backup coverage; decide keep-window.
- [ ] **P6.3 — Docs:** update [initial-podcast-digest-idea.md](initial-podcast-digest-idea.md) status to "planned → in build"; keep this TASKS file as the living tracker; update the `/stack-map` ref doc for both new containers.
- [ ] **P6.4 — Recovery:** verify `scripts/emergency-recovery.ps1` startup/shutdown ordering includes `openbrain-podcast` + the S2 worker (after `llama-cpp` healthy, after `openbrain-db`/`openbrain-mcp`; ON up before podcast generation).

---

## Decision log (mirror of PLAN §2 — update here as work proceeds)

| ID | Decision | Status |
|---|---|---|
| D1 | v1 = all four services (S1+S2+S3+S4) | locked 2026-06-08 |
| D2 | Two-host conversational | locked 2026-06-08 |
| D3 | One daily episode, segments per label | locked 2026-06-08 |
| D4 | Each link ingested as a full research package | locked 2026-06-08 |
| D5 | Briefing assembled externally via claims pipeline; ON = content-only renderer | locked 2026-06-08 |
| D6 | Each link enters the claim pipeline via a mandatory **LLM extraction pass** (tagged `[Source 1]` claims) before the curator — raw body yields 0 claims | superseded by D7 |
| **D7** | **Route through the proper research channel.** S3 does NOT synthesize ad-hoc; it submits each link to the shared `openbrain-research` service (grounds + delegates to curator). Supersedes D6's in-recipe synthesis. | locked 2026-06-08 (audit) |
| **D8** | **Article mode.** The article is the **seed + primary subject**; corroboration from **existing OB claims** (harness reuse pass), not topic web-search. New `mode=article` + `seed_sources`. | locked 2026-06-08 (audit) |
| **D9** | **Async wait-gate (paramount).** S3 awaits ALL research jobs to terminal `done` before any claim feeds the podcast; a job that times out → `email-only`. | locked 2026-06-08 (audit) |
| **D10** | **Tor-route ALL external fetches** (`socks5h://tor:9050` — DNS through Tor, matching SearXNG). Digest link stage AND `openbrain-research` `fetchPage`. Fixes the `socks5://` DNS leak + identifying UA. | locked 2026-06-08 (audit) |
| **D11** | **Gap handling.** Solve gaps from the article/OB where possible; else surface as POI **and** allow **bounded PRELIMINARY web research** → tentative `[UNCERTAIN] "preliminary research suggests…"` (lower confidence). Unsolved gaps stay `[GAP]`. New `gap_research=preliminary`. | locked 2026-06-08 (audit) |
| — | ~~source-package (claims-free) inlet fallback~~ | **rejected 2026-06-08** — bypasses the claims layer (D5/D7). |
| — | ~~in-recipe single-source synthesis (`synthesize.ts`/`curator.ts`)~~ | **retired 2026-06-08** by D7 → replaced by `research-client.ts` → `openbrain-research`. Files deleted. |
