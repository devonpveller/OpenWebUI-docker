# Daily Digest → Autonomous Podcast — loose idea

> **Status:** Raw idea / brain-dump for later distillation into a proper plan.
> **Date:** 2026-06-03
> **Author intent (operator):** keep sending the daily digest email exactly as it
> is today, **and additionally** generate a short morning podcast built from the
> same curated digest material — calendar (today + tomorrow + long-horizon
> travel) and the label-sectioned email summaries — using **Open Notebook**'s
> podcast generation. **Precondition added 2026-06-03:** the email summaries alone
> are too thin; the special-label newsletters carry **links**, and those links
> must be **autonomously followed and ingested into Open Brain** (associated with
> the originating email) so the podcast has real material. When a link can't be
> traversed, fall back to the email context — but **say so in the audio** so the
> listener knows a statement is incomplete.
> **Related:**
> [quartz-4-expansion-plan.md §Phase 7](../implementation-guide/expand-quartz-4/quartz-4-expansion-plan.md)
> (deferred in-stack podcasts; ON stays the podcast tool until it ships).

---

## 1. What exists today (so we build on it, not around it)

The digest is the `openbrain-digest` Deno service on the Open Brain stack. It is a
long-running container with a `POST /run` trigger, fired **last** in the daily
event-chain (`pull → prune → digest`, see [OB1/docker/cron/crontab](../../OB1/docker/cron/crontab)),
so by the time it runs the brain already holds yesterday's freshly-pulled email
thoughts. Schedule today: 05:00 UTC (= 01:00 EDT).

Architecture worth preserving — it's already cleanly decoupled
([send-digest.ts](../../OB1/recipes/daily-digest/send-digest.ts) is a pure composition root):

```
Sections (produce structured data)        Renderers (draw it)
  WeatherSection   ─┐                        HtmlRenderer   → email body
  CalendarSection  ─┼─►  SectionData[]  ─┬─► MarkdownRenderer → /reports audit copy
  AiNewsSection    ─┘                    └─► [NEW] PodcastScriptRenderer → script
```

- A section that throws is **omitted, never fatal** — the digest still goes out.
  The podcast (and the link enrichment in front of it) must inherit this:
  best-effort, never block or delay the email.
- `SectionData[]` is the clean intermediate. **A podcast is just a third renderer
  over the same `SectionData`** — this is the natural seam, no new data plumbing.

### 1.1 The calendar content the operator named — already bucketed

[CalendarSection](../../OB1/recipes/daily-digest/src/sections/calendar.ts) already
emits exactly the three buckets the operator wants narrated:

- `today` — every event today
- `tomorrow` — every event tomorrow
- `needsPrep` — curated lookahead over the next ~30 days; **long-horizon travel is
  precisely the `travel`-keyword prep items** (`flight, airport, hotel, trip,
  train, road trip, …` in `DEFAULT_PREP_KEYWORDS.travel`), alongside interviews,
  milestones, family-name hits, and `[prep]` tags.
- Each event can also carry a `considerationsSummary` — the LLM-synthesized
  "Related from your brain" paragraph
  ([synthesizer.ts](../../OB1/recipes/daily-digest/src/considerations/synthesizer.ts)).
  **This prose is gold for a podcast** — it's already narrative, already grounded.

### 1.2 The email content — labels are captured but not yet the grouping axis

[AiNewsSection](../../OB1/recipes/daily-digest/src/sections/ai-news.ts) groups
yesterday's ingested emails by `gmail_id` (collapsing a newsletter's many chunks
into one entry) and exposes per-email `topics`, `people`, `action_items`, and
**`gmailLabels`**.

⚠️ **Nuance to flag for the plan:** the email currently sections **by sender**
(`bySender`), *not* by label. The operator's mental model is "sectioned by their
labels" (`slow ai`, `nate b jones`, `ai break`, + future labels). Those labels
already live in `meta.gmail_labels`, and the pull is label-driven (`labelsPrefix:
"brain/"`), so the labels are `brain/slow-ai`, `brain/nate-b-jones`,
`brain/ai-break`, etc. **Grouping the podcast by label is a small additive
enhancement** (a `byLabel` map next to `bySender`), and arguably the email could
gain the same grouping later. New labels the operator adds under `brain/` are
auto-discovered — nothing hard-codes the label set.

### 1.3 The podcast engine — Open Notebook, already running

Open Notebook is a **separate app** (`open_notebook`, SurrealDB-backed) with a
REST API at `:5055/api` (UI `:8502`, Tailnet `:8443`). It does source ingestion,
multi-source Q&A, and **podcast generation** (episode profiles + speaker profiles
+ `podcast_config`, audio via a TTS backend). The Quartz-4 plan keeps ON alive as
the podcast tool **specifically until the deferred P7 in-stack podcast service
exists**, so leaning on ON now is consistent with the locked roadmap (D-B / D-H),
not a detour.

There is also a **local OpenAI-compatible TTS/STT service at
`host.docker.internal:8000/v1`** that Quartz-4 P7 intends to call directly later.
That gives us a clean future swap (see §6).

### 1.4 The ingestion primitives — already in the stack

Following + ingesting newsletter links does **not** need a new engine:

- **`find_or_create_source()`** (OB1, dedup on url/content-hash) — the canonical
  "make a URL a first-class source" primitive, referenced throughout Quartz-4.
- **`ingest_url` / `ingest_urls`** Open Brain tools; **smolcrawl** (main-stack
  crawler); and the Quartz-4 P5 **`openbrain-extract`** (content-core) sidecar once
  it lands — any of these can fetch + extract a URL to markdown.
- New sources auto-enqueue into `source_extraction_queue` → entity worker → wiki
  compile, so ingested link content **also grounds the wiki**, not just the
  podcast (compounds with Quartz-4 P6 grounding).

---

## 2. The loose idea, in one paragraph

After the digest builds its `SectionData[]` and sends the email unchanged, a
decoupled stage **enriches** the special-label emails by following their links and
ingesting the detail into Open Brain (associated back to each email), then turns
the enriched, structured data into a short spoken-word "morning show": a
weather/date cold-open, an "On your calendar" segment (today, tomorrow, and
travel-on-the-horizon, weaving in the brain considerations), then a "From your
feeds" run of one segment per label (Slow AI, Nate B Jones, AI Break, …) narrated
from the **full ingested articles where available** — and from the email blurb
**with a spoken "this is incomplete" caveat** where a link couldn't be reached. A
`PodcastScriptRenderer` produces the script; Open Notebook turns it into audio; the
email (or a short follow-up) links to the episode. The whole stage is best-effort
and never delays the email.

---

## 3. Link-following enrichment — the precondition (operator add, 2026-06-03)

The email summaries alone are too thin to narrate a real episode. The
special-label newsletters (`nate b jones`, `slow ai`, future labels) are mostly
**pointers** — they carry links to the actual articles / threads / videos. To make
the podcast comprehensive, the pipeline must **follow those links and ingest the
detail into Open Brain before the script is written**, tied back to the email it
came from. When a link can't be traversed, fall back to the email context — but
**mark that segment as incomplete so the listener hears it.**

### 3.1 Where this runs in the flow

New stage, between "digest builds `SectionData`" and the script renderer — and on
the **podcast side of the chain only**, so the email never waits on crawling:

```
pull → prune → digest (sends email exactly as today)
                  └→ enrich (follow + ingest label-email links)
                         └→ podcast (script → audio → link back)
```

### 3.2 Link extraction + hygiene (where the quality lives)

Newsletter HTML is mostly noise. Extract candidate URLs from each label-email's
stored content, then **filter aggressively**:

- **Keep:** article / post / video links to real content domains.
- **Drop:** tracking/click wrappers (unwrap to the real destination first),
  unsubscribe, "view in browser", "manage preferences", social-share, `mailto:`,
  image/asset URLs, ad/sponsor links.
- Many newsletter links are **redirect trackers** (e.g. `link.mail.beehiiv.com/…`,
  `substack.com/redirect/…`) — resolve the final URL before ingest + dedup.
- Dedup and **cap per email** (e.g. top N by position/heuristic) to bound crawl
  time and cost.

### 3.3 Fetch + ingest engine (reuse — see §1.4)

Pick one of the existing engines behind a thin interface (`ingest_url`/smolcrawl/
`openbrain-extract`); land each link as a source via `find_or_create_source()`
(dedup). Don't hand-roll HTML→markdown.

### 3.4 Association back to the email (the operator's "associated with")

Each ingested source records its origin so link content stays tied to the
newsletter and the day it arrived:

- `metadata.gmail_id` (+ `gmail_thread_id`, `gmail_labels`, `email_date`) of the
  source email — the **same keys `AiNewsSection` already groups on**, so the
  podcast can join ingested sources straight onto the email that referenced them.
- Optionally link the source into a **thread per label** (`brain/slow-ai` → a "Slow
  AI" thread) via `link_source_to_thread(..., 'deliberate')` — lining this up with
  Quartz-4 threads and future thread-scoped podcasts (§6).
- Net effect: the podcast's "Slow AI segment" narrates from the **full ingested
  article text**, not just the newsletter's one-line blurb.

### 3.5 Completeness / fallback marking (must be audible)

Per the operator: silence about incompleteness is unacceptable. Track, per link
and per segment, an enrichment status that rides on the `SectionData` so the
`PodcastScriptRenderer` can **speak** it:

- `enriched` — link fetched + ingested → narrate from full content.
- `email-only` — link failed / paywalled / robots-blocked / skipped → narrate from
  the email blurb **and say so**: e.g. *"…this is based only on the newsletter
  summary; I couldn't open the linked article."*
- `partial` — some links in a segment enriched, others didn't → note the mix.

The same status should surface in the **email** (a small "🔗 3/5 links ingested"
note) and the `/reports` script copy, for transparency outside the audio.

### 3.6 Failure philosophy (unchanged)

Enrichment is best-effort and **time-boxed**. A hung crawl, dead link, paywall, or
robots block → fall back to `email-only` for that item, mark it, move on. Never
block, never fail the podcast over a link, never touch the email's delivery.

---

## 4. Sketch of the pieces

### 4.1 `PodcastScriptRenderer` (new, sits beside Html/Markdown renderers)

Same `SectionData[]` input (now carrying enrichment status + joined source text).
Output: a podcast-ready **narrative script** (spoken prose). Two ways to produce it:

- **Templated** — deterministic prose assembly from the structured fields (fast,
  free, predictable). Good v0.
- **LLM-polished** — one `LlmClient` pass (`qwen…:nothink`, already wired) over the
  structured data + ingested article text → a natural, conversational script.
  Better listen; costs a GPU call that contends with TTS — but we run at 01:00
  local, off-peak.

Recommended: structured assembly first, optional LLM polish behind a flag
(mirrors `CONSIDERATIONS_SYNTH`).

### 4.2 Proposed episode structure (morning-show format)

1. **Cold open** — date + one-line weather (from `WeatherPayload.brief`).
2. **On your calendar**
   - Today's events (time, title, who, considerations).
   - Tomorrow's events.
   - **Travel on the horizon** — the `needsPrep` travel items (the long-horizon
     flights/trips the operator called out), spoken as "looking further out…".
3. **From your feeds** — **one segment per label**, in a stable order, narrated
   from ingested article content where `enriched`, with the spoken caveat where
   `email-only`:
   - *Slow AI:* N items today — the gist (full where enriched), topics, actions.
   - *Nate B Jones:* …
   - *AI Break:* …
   - *(any future `brain/*` label, auto-included)*
4. **Action items round-up** — the `totalActionItems` across the day.
5. **Sign-off.**

### 4.3 Audio generation via Open Notebook

- POST the script (and/or the enriched source pool) to ON's podcast API with a
  preset **episode profile**; poll the job; retrieve the produced audio.
- **Single vs multi-speaker** is an episode-profile choice (see §7). A
  single-narrator brief is simpler/faster; ON supports two-host formats.

### 4.4 Linking it back into the email

The email goes out **unchanged in content**, plus a small block at the top:
"🎧 Today's episode (X min) → [listen]" and the "🔗 N/M links ingested" note. The
link needs a **stable, reachable URL** — open question (§7): ON's own audio URL
over the Tailnet, vs. copying the mp3 to a portal-served location, vs. the future
`assets/podcasts/` path that P7 will use.

---

## 5. Where this slots into the chain (timing)

Both enrichment (crawl) and podcast generation (script LLM + TTS) are **minutes**,
not seconds. Inline-blocking the digest email behind either is wrong. Shape:

- **Decoupled chain step (recommended):** `digest` sends the email as today, then
  triggers a new `podcast` step (`digest → podcast`, same `NEXT_TRIGGER_URL`
  pattern the chain already uses). That step does enrich → script → audio, then
  either (a) sends a short **follow-up** "your episode is ready" email, or (b) the
  morning email links to a stable URL that fills in once ready.
- **Generate-first** (run everything before the email so it embeds a ready link)
  is rejected — it delays the one thing that must always arrive on time.

This preserves "email always on time" and makes enrichment+podcast an additive,
independently-failing step — the section-omit philosophy at chain scale.

---

## 6. Relationship to the Quartz-4 deferred podcast plan (don't paint into a corner)

This feature is effectively the **first concrete consumer** of "generate a podcast
from a curated cluster of brain material." The Quartz-4 plan's **P7** will build an
in-stack podcast service (script via local Qwen → audio via the local TTS at
`host.docker.internal:8000/v1` → `assets/podcasts/<id>.mp3`, `podcasts` table
keyed by thread). Design this so that when P7 lands:

- The **`PodcastScriptRenderer` stays** — script generation is backend-agnostic.
- Only the **audio backend swaps**: ON's podcast API → the P7 workbench podcast
  endpoint / direct TTS call. One seam, behind an interface.
- The **enrichment + per-label threads** are a natural precursor to **thread-scoped
  podcasts**: a label ≈ a recurring research group / thread, and §3.4 already lands
  ingested link-sources into those threads. Today's "Slow AI segment" becomes
  tomorrow's "podcast for the Slow AI thread."

So: **build the interim on ON, but put the audio call and the fetch/extract call
behind thin interfaces** so the P7 cutover is a backend swap, not a rewrite — and
so the eventual full ON decommission (Quartz-4 §10) isn't blocked by this feature.

---

## 7. Open questions to resolve during distillation

**Enrichment / link-following**

1. **What counts as a "content" link** vs. noise — the keep/drop heuristics in
   §3.2, per newsletter platform (beehiiv, Substack, Mailchimp, ConvertKit…).
2. **Redirect unwrapping** — resolve tracker URLs to their destination safely
   (without firing tracking pixels we don't want, and without infinite redirects).
3. **Crawl budget + timeout** — max links/email, max total time, per-link timeout;
   keep the whole enrich stage well-bounded.
4. **Paywall / robots / login policy** — when a fetch returns a stub or 403, that's
   an `email-only` fallback, not a retry storm. Confirm we respect robots.
5. **Dedup across days** — `find_or_create_source()` dedups by url/hash, so a link
   re-shared tomorrow shouldn't re-ingest; but should it still appear in tomorrow's
   episode? (Probably yes, narrated as "previously seen.")
6. **Which engine** — `ingest_url`/`ingest_urls` vs. smolcrawl vs. waiting for
   `openbrain-extract` (P5). Extraction quality on article pages is the deciding
   factor.

**Podcast / audio**

7. **Audio hosting / stable URL** for the email link — ON Tailnet URL vs.
   portal-served `/reports` vs. a served assets path. What's durable + auth-gated?
8. **ON v1 podcast API surface** — verify it supports **headless** podcast creation
   from arbitrary text/script (not only from a notebook with attached sources), and
   what episode/speaker-profile setup is required up front (profile creation is
   manual in the ON UI today). *Main unknown to verify.*
9. **Single- vs multi-speaker**, and **voice selection** — episode-profile choice;
   what TTS voices the ON backend exposes.
10. **Length budget** — a 20-email day shouldn't make a 25-minute episode. Per-label
    item caps, "and N more" tails, a global target length.
11. **GPU contention** — TTS + the optional script-LLM pass + crawl-time embeddings
    all contend with `llama-cpp`; the 01:00-local off-peak schedule helps, but
    confirm against [llama-swap perf tuning](../../../../Users/yamao/.claude/projects/d--Open-WebUI-ai-stack/memory/llama-swap-perf-tuning.md).
12. **Audio retention / cleanup** — episodes accumulate; retention + backup coverage.

**Cross-cutting**

13. **Sync vs async** — confirm the decoupled `digest → podcast` follow-up shape
    (§5) vs. embedding a link that fills in later.
14. **Label-as-segment grouping** — add `byLabel` to `AiNewsSection`; map
    `brain/slow-ai` → "Slow AI"; decide ordering. Should the *email* gain label
    grouping too?
15. **Does the podcast cover the same window** as the email (yesterday's pulled
    emails + today/tomorrow calendar)? Default: same.

---

## 8. Smallest viable first cut (if we want to prove it fast)

1. Add `byLabel` grouping to `AiNewsSection` (additive; email unchanged).
2. **Link enrichment v0:** extract + filter + redirect-unwrap URLs from label
   emails, ingest via one chosen engine with `find_or_create_source()`, stamp
   `gmail_id`/`gmail_labels` on each source, and record the per-item enrichment
   status (`enriched` / `email-only` / `partial`). Run it standalone first and eyeball
   what got ingested and what fell back.
3. Add `PodcastScriptRenderer` — structured prose assembly over the enriched
   `SectionData`, **speaking the completeness caveat**, written to `/reports` next to
   the markdown audit copy so we can read the script (and verify the caveats) before
   any audio exists.
4. Manually feed that script to ON once, by hand, to validate the episode profile,
   audio quality, and the link surface.
5. Only then wire the automated `digest → podcast` chain step and the email link.

That sequence de-risks the two unknowns (link-extraction quality §3.2, ON podcast
API §7.8) before automating anything, and every step is additive and reversible.
