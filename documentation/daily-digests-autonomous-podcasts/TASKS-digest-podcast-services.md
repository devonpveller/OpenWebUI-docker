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

## P1 — S3 Digest Link Processor (standalone, eyeball-first)  *(agent)*

Reuses the live curator (S1) — no new service yet; run as a standalone script against one real day.

- [ ] **P1.1** `byLabel` grouping on [AiNewsSection](../../OB1/recipes/daily-digest/src/sections/ai-news.ts) (additive map next to `bySender`; email unchanged). Map `brain/slow-ai → "Slow AI"`, etc.; auto-discover new `brain/*` labels.
- [ ] **P1.2** Link extraction + hygiene per label email: extract candidate URLs, **unwrap redirect/tracker wrappers** to the real destination, drop noise (unsubscribe/view-in-browser/social/mailto/assets/ads), dedup, **cap per email** (~5).
- [ ] **P1.3** Fetch/extract each surviving link via `ingest_url` (behind the swappable fetch interface). Time-box (~60s/link), bounded total.
- [ ] **P1.4** **Synthesis-then-package (D4/D5 — mandatory; this is the step that puts the link into the claim pipeline).** Raw article body yields **0 claims** — the curator parses claims only from `[SOURCED]/[Source N]`-tagged text ([claims.ts](../../OB1/integrations/research-curator/claims.ts); uncited claims dropped at [claims.ts:138](../../OB1/integrations/research-curator/claims.ts#L138)/[207](../../OB1/integrations/research-curator/claims.ts#L207)). Per link: **(a)** run an LLM extraction pass over the extracted content → tagged claims `[SOURCED]/[INFERRED] … [Source 1]` citing **this article** (reuse the research-service SYNTH prompt — one-claim-per-line, tag-first); **(b)** assemble `{claim:title/summary, synthesis:<tagged>, sources:[link], topic_hint:label}` and `POST /ingest/research-package` (`x-brain-key`); **(c)** confirm the response `claims` stats show grounded claims written (**not** all `ungroundedSkipped`). Capture `thread_id`/`thread_decision`/`thread_name`. *No claims-free shortcut — a bare source is invisible to S4 (D5).*
- [ ] **P1.5** Stamp `gmail_id`/`gmail_thread_id`/`gmail_labels`/`email_date` on each ingested source (§3.4 keys — the same `AiNewsSection` groups on).
- [ ] **P1.6** Record per-link enrichment status `enriched | email-only | partial` (§3.5).
- [ ] **P1.7** Emit the **DAY REPORT**: `[{source_id, url, label, thread_id, thread_name, status}]` to `/reports`.
- [ ] **P1.8** Respect robots; paywall/403/stub → `email-only`, **no retry storm**. Re-shared link (dedup hit) still listed, flagged "previously seen."

**Acceptance:** run against one real day; eyeball the report — sensible thread decisions (existing vs new), clean extracted content, **non-empty grounded `claims` stats per link**, correct status flags. **Gate R3 before automating.** If per-link synthesis+curator proves too heavy (R1), the lever is **fewer links / batch / cheaper model** — **not** skipping the synthesis: a claims-free link is invisible to S4's grounded briefing (D5), so dropping grounding saves nothing and breaks the episode.

---

## P2 — S2 Grounding Backfiller worker  *(agent + operator for deploy)*

New worker on the live claims layer. General brain-health, not podcast-specific.

- [ ] **P2.1** Worker scaffold (Deno service/worker, sibling to entity/chunk workers), loopback port; reads `ungrounded_claims` ([init-claims.sql](../../OB1/docker/init-claims.sql)).
- [ ] **P2.2** Drain loop: for each ungrounded claim → extract the entity/assertion → Wikipedia lookup (grounding-corpus backend, swappable) → `ingest_url` the page (dedup) → `link_claim_to_source(claim, src, 'corroborates', weight)`. Confirm the `claim_confidence` trigger recomputes and the claim **leaves** `ungrounded_claims`.
- [ ] **P2.3** Scope control: **today's-digest-threads first** (invoked by S3's report `thread_id`s, pre-script), then a **bounded off-peak global sweep**. Per-run budget caps (pages/claim, total calls).
- [ ] **P2.4** No-entity / no-Wikipedia-match → leave ungrounded + log; never retry-storm. Idempotent (edge upsert).
- [ ] **P2.5** **3-place change** for the new worker: OB1 compose + `emergency-recovery.ps1` inventory + `/stack-map`.
- [ ] **P2.6** Operator: deploy (build image, bring up). No schema change needed (claims layer already live).

**Acceptance:** seed/identify one ungrounded claim, run the worker, confirm it gains a Wikipedia `corroborates` edge and drops out of `ungrounded_claims` with recomputed confidence.

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
| D6 | Each link enters the claim pipeline via a mandatory **LLM extraction pass** (tagged `[Source 1]` claims) before the curator — raw body yields 0 claims | locked 2026-06-08 |
| — | ~~source-package (claims-free) inlet fallback~~ | **rejected 2026-06-08** — bypasses the claims layer; a claims-free link is invisible to S4's grounded briefing (D5). Load lever = volume/batch/model, not skip-grounding. |
