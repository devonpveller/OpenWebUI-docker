# Daily Digest → Autonomous Podcast — loose idea

> **Status:** Raw idea / brain-dump for later distillation into a proper plan.
> **Date:** 2026-06-03
> **Revised:** 2026-06-08 — see **[Part II](#part-ii)** at the bottom. The
> 2026-06-07 stack cutover changed the foundation under this doc: Open Notebook is
> now **OB1-Postgres-backed** (not SurrealDB-only), and three OB1-side services that
> this idea needed are **already live** — the `openbrain-curator` (the "Research
> Thread Assigner"), the grounded-**claims** layer (the substrate for a "Wikipedia
> Backfiller"), and the shared `openbrain-research` harness. Part II re-frames the
> whole thing as **four general agentic services** (this podcast being their first
> chained consumer), maps each to what already exists vs. what is genuinely new, and
> supersedes the stale parts of §1.3, §1.4, and §3.4 below. Read §1–§8 as the
> original thinking; read Part II for the current-state reconciliation.
>
> **Distilled into a plan (2026-06-08):** the feasibility pass + locked decisions now
> live in [PLAN-digest-podcast-services.md](PLAN-digest-podcast-services.md) and the
> phased [TASKS-digest-podcast-services.md](TASKS-digest-podcast-services.md). Start
> there for implementation; this doc is the idea-of-record.
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

Open Notebook is a **separate app** (`open_notebook`) with a REST API at `:5055/api`
(UI `:8502`, Tailnet `:8443`). It does source ingestion, multi-source Q&A, and
**podcast generation** (episode profiles + speaker profiles + `podcast_config`,
audio via a TTS backend). The Quartz-4 plan keeps ON alive as the podcast tool
**specifically until the deferred P7 in-stack podcast service exists**, so leaning
on ON now is consistent with the locked roadmap (D-B / D-H), not a detour.

> ⚠️ **Stale as of 2026-06-07 (IKS cutover LIVE).** ON is **no longer SurrealDB-only**.
> The prod container now runs the fork image `open_notebook:iks` with **OB1 Postgres
> (`openbrain-db`) as the canonical store for sources + threads**; SurrealDB survives
> only as ON's local UI/queue/chat/cache store. This is a *big* simplification for
> this idea: an ON source and an OB1 source are now **the same row** — no cross-store
> sync, and a podcast ON generates can be linked straight onto OB1 threads. ON's
> podcast API is now **OB1-thread-aware** (it can build an evidence-validated briefing
> from a thread). See [[iks-cutover-live]] and **Part II §A**.

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

> ➕ **New since 2026-06-07 — three live OB1-side services this idea can stand on
> (full detail in Part II):**
> - **`openbrain-curator`** (`POST /ingest/research-package`) — LLM-resolves new
>   knowledge onto the **best existing thread** (pgvector shortlist → LLM decision,
>   conservative-merge bias) instead of spawning a fresh thread per ingest. This **is**
>   the "Research Thread Assigner" the new workflow proposes — already built + deployed.
> - **Grounded-claims layer** (`init-claims.sql`: `claims` + typed `claim_sources`
>   edges + `ungrounded_claims`/`reusable_claims` views + `claim_confidence`). Every
>   stored claim is anchored to a source; the `ungrounded_claims` view is exactly the
>   worklist a **"Wikipedia Backfiller"** would drain.
> - **`openbrain-research`** (`:8818`, `POST /research`) — the shared "one research
>   brain, many thin inlets" harness. The service-abstraction the new workflow asks for
>   already has a home here. See [[research-engine-plan]], [[research-curator-inlet-service]].

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

---
---

<a id="part-ii"></a>

# Part II — Revised against the live stack + the general-services framing (2026-06-08)

> This part folds in the operator's expanded workflow ("Daily Digest Podcast
> Generation & Research Thread Management") **and** the 2026-06-07 stack changes.
> The framing shift the operator asked for: the four components below are **not
> podcast-specific**. They are **general agentic services** — the podcast is simply
> the *first chained use case* that exercises all of them end-to-end. Build them so
> any future agent (deep research, the OWUI tool, Open Notebook, a scheduled job)
> can call the same front doors.

## A. What changed under this doc since it was written

Three 2026-06-07 changes rewrite the foundation. **Most of the new workflow is
already built** — the work is *wiring*, not *inventing*.

| New-workflow component | Status on the live stack | Where it lives |
|---|---|---|
| **C1 — Research Thread Assigner** | ✅ **Built + deployed** as `openbrain-curator` | `OB1/integrations/research-curator/` — `POST /ingest/research-package` |
| **C2 — Wikipedia Backfiller** | 🟡 **Substrate live, worker not built** — the grounded-claims layer + `ungrounded_claims` view exist; the draining worker does not | `OB1/docker/init-claims.sql`; new worker TBD |
| **C3 — Daily Digest Link Processor** | 🟡 **§3 of this doc, partly re-pointed** — link enrichment is sketched here; it now routes through C1 instead of a static label→thread map | `OB1/recipes/daily-digest/` + new enrich stage |
| **C4 — Podcast Generator & Ingestor** | 🟡 **API surface live, orchestration not built** — ON's podcast API is OB1-thread-aware; the digest→podcast→ingest→close loop is not wired | ON `POST /api/podcasts/generate`; new chain step |

**The single most important correction to §1–§8:** because Open Notebook is now
**OB1-Postgres-backed** (canonical sources + threads in `openbrain-db`; SurrealDB
demoted to ON's local UI/queue/cache), the "close the loop" steps stop being a
cross-store sync problem. An ON-ingested source, an OB1 thread, a curator decision,
a grounded claim, and the final podcast note **all live in one Postgres**. "Link the
note to the thread" is one row, not an integration.

## B. The Service-Abstraction requirement (operator's "Core Architectural Requirement")

The operator's requirement — *"each component must support abstraction, allowing
different services to be swapped in when adding information to the brain"* — already
has an established pattern in this workspace, and it should govern all four
components:

- **Thin inlet → shared OB1-side service.** This is the [[research-engine-plan]]
  doctrine: "one research brain, many thin inlets." A component is an **OB1-side
  service with a clean HTTP/queue contract**; callers (OWUI tool, the digest, ON, a
  future agent) are thin clients. The curator and `openbrain-research` already follow
  this shape — new components must too.
- **Backend behind an interface.** §6 already commits to this for audio (ON podcast
  API today → P7 in-stack TTS later, one seam). Generalize it: the *fetch/extract*
  engine (`ingest_url` / smolcrawl / `openbrain-extract`), the *thread-resolution*
  engine (curator), and the *grounding-source* engine (Wikipedia → later any
  authoritative corpus) each sit behind a swappable interface.
- **Best-effort, section-omit, never block the email.** The failure philosophy of
  §3.6 / §5 is itself an abstraction contract: every component is independently
  failing and never delays the one thing that must arrive on time.

## C. Component 1 — Research Thread Assigner  ⟶  **use the live `openbrain-curator`**

**Do not build this.** It exists and is deployed. The operator's description — *"an
LLM decision step for every deep-research job; the LLM evaluates the new research and
suggests the highest-probability existing thread it belongs to, or determines a new
thread should be created"* — is a verbatim description of the curator's two-stage
resolver:

- **Stage 1:** pgvector shortlist over `threads.embedding` (top-K candidate threads).
- **Stage 2:** LLM decision with a **conservative-merge bias** → attach to an existing
  thread or create a new one; an explicit `thread_id` bypasses the resolver.
- It then **delegates the write** to `openbrain-mcp`'s `/research/persist` with the
  resolved thread injected, and refreshes the thread's description + embedding.
- It already folded the live brain from **38 → 25 threads** (de-fragmentation proven),
  and `deep_research` routes through it **by default**. See [[research-curator-inlet-service]].

**Contract:** `POST /ingest/research-package` (header `x-brain-key`):
```
{ claim, synthesis?, query?, sources:[{url,title,content,summary,domain}],
  volatility?, thread_id?(explicit override), topic_hint? }
→ { thread_id, thread_decision:"explicit"|"existing"|"new",
    thread_confidence, thread_name, shortlist:[...], persist:{...}, claims:{...} }
```

**The one real gap — and a correction (2026-06-08).** The curator parses grounded
claims **only** from tag-cited synthesis (`[SOURCED] … [Source N]`, see
[claims.ts](../../OB1/integrations/research-curator/claims.ts)); **untagged prose
yields zero claims** (an uncited claim is dropped at the gate). A digest link is a
**raw crawled article** — so dumping its extracted body in as `synthesis` would
persist the article as a source but produce **no claims**, silently bypassing the
grounding layer. The correct path (D4/D5) is to run each link through an **LLM
extraction pass** that emits `[SOURCED] … [Source 1]` claims **citing the article**,
then POST *that* as the research package — each link is a **one-source mini-research
run**. A "bare source, skip the synthesis" shortcut (the earlier `source-package`
idea) is **rejected**: a source with no claims is invisible to the podcast's grounded
briefing (which reads `reusable_claims`), so skipping grounding doesn't save work — it
makes the link pointless. The "too heavy" lever is **fewer links / batch / cheaper
model**, never skipping claims.

This **supersedes §3.4's static `brain/slow-ai → "Slow AI" thread` map.** Static
label→thread mapping is the v0 fallback; the curator is the real assigner. (A label
can still seed a `topic_hint` to bias the resolver.)

## D. Component 2 — Wikipedia Backfiller  ⟶  **new worker on the live claims layer**

This is the **most genuinely new** component, and the most general — it grounds the
**whole brain**, not just podcast material. It is the *active remediation arm* of the
grounding model's Rule #1 ("no ungrounded claim is stored/served/reused").

**It already has its worklist.** `init-claims.sql` ships the `ungrounded_claims`
view = active claims whose grounding chain never terminates in a primary source
(`claim_min_depth(id) IS NULL`). In a healthy KB it should be empty; the backfiller's
job is to keep it that way.

**Proposed worker** (sibling to `openbrain-entity-worker` / `openbrain-chunk-worker`):
```
loop / on-event:
  for claim in ungrounded_claims (optionally scoped to today's digest threads first):
    entity/assertion ← extract the ungrounded reference from claim.text
    page             ← Wikipedia lookup for that entity
    source_id        ← ingest_url(page)                 # find_or_create_source dedups
    link_claim_to_source(claim, source_id, 'corroborates', weight)
    # claim_confidence trigger recomputes automatically → claim leaves the view
```

**Design decisions to settle (call these out in the plan):**
- **Edge type + authority.** The confidence model scores `.gov/.edu/.mil` at authority
  1.0, everything else 0.85 — Wikipedia lands at 0.85. Wikipedia is a **tertiary**
  source, so prefer `edge_type='corroborates'` at a modest weight (supplementary
  grounding) over `'states'` (primary). Optionally chase Wikipedia's own cited
  references for stronger primaries (a v2).
- **Trigger model.** Event-driven (fire when a new ungrounded claim appears) **plus**
  a bounded scheduled sweep. For the podcast loop, run it as a **post-enrichment step
  scoped to the day's new claims** so the episode narrates grounded material; a global
  sweep can run off-peak.
- **Budget / loop-safety.** Cap pages/claim and total Wikipedia calls per run; a claim
  with no plausible Wikipedia entity is **left ungrounded and logged**, not retried in
  a storm (mirrors §3.6).
- **Abstraction.** "Wikipedia" is the first **grounding-source backend** behind the
  interface; later swap/add authoritative corpora (docs, .gov, the brain's own
  reusable claims) without touching the worker's drain loop.

## E. Component 3 — Daily Digest Link Processor (Step 1)  ⟶  **§3 enrichment, re-pointed through C1**

This is the §3 link-enrichment stage, kept almost entirely — extraction, redirect
unwrapping, hygiene/keep-drop (§3.2), the chosen fetch engine (§3.3), and the
**audible completeness status** `enriched`/`email-only`/`partial` (§3.5) are all
still correct. **What changes:** the per-link "association" step (§3.4) now calls the
**curator** instead of writing a static label→thread link.

**Revised Step-1 workflow** (operator's outline, reconciled):
1. The 01:00 chain reaches the `digest` step; email goes out **unchanged** (§5).
2. On the **podcast side of the chain only**, extract + unwrap + filter candidate
   links from each label email (§3.2), capped per email.
3. For each surviving link: fetch+extract → **POST it to the curator as a research
   package** (C1, recommended option) → curator resolves the thread, persists the
   source linked to that thread, and (via the claims path) records grounded claims.
4. Stamp `gmail_id`/`gmail_thread_id`/`gmail_labels`/`email_date` on each source
   (§3.4) so the podcast can join sources back onto the originating email.
5. Record the per-link enrichment **status** for the audible caveat (§3.5).
6. Iterate until all candidates are evaluated.
7. **Output — the day's source report:** every source gained today + its
   **resolved `thread_id`/`thread_name`** + enrichment status. This report is the
   **required input to Step 2** (C4).

(Optional, off the same report: kick C2 to backfill the new threads' ungrounded
claims before the script is written.)

## F. Component 4 — Podcast Generator & Ingestor (Step 2)  ⟶  **§4, leveraging the OB1-thread-aware ON API**

§4's pieces (`PodcastScriptRenderer`, the morning-show structure, audio-via-ON, the
email link-back) stand. The cutover **upgrades the back half**: closing the loop is
now native because everything is one store.

**ON podcast API surface (verified, OB1-aware):**
```
POST /api/podcasts/generate
  { episode_profile, speaker_profile, episode_name,
    content?         # direct script/text  (the PodcastScriptRenderer output)
    notebook_id?     # OR an OB1-thread-linked notebook → ON builds an
                     #   evidence-VALIDATED briefing from that thread (grounded)
    briefing_suffix? }
  → { job_id, status, ... }
GET /api/podcasts/jobs/{job_id}        # poll
GET /api/podcasts/episodes/{id}/audio  # retrieve audio
```
> ⚠️ **Verify the operator's "June-6 podcast mods."** Exploration found the
> OB1-thread-aware podcast path (the relevant capability) but **no podcast code
> commits dated 2026-06-06** — last podcast-specific commits were ~Feb 2026. Confirm
> what actually changed on the 6th before relying on a specific new feature.

**Revised Step-2 workflow** (operator's outline, reconciled):
1. Take Step-1's source report. Either (a) render a script from the enriched
   `SectionData` and POST as `content`, or (b) pass a `notebook_id` for the relevant
   OB1 thread and let ON build the grounded briefing — **(b) becomes viable now** that
   ON reads OB1 threads. (For a multi-thread day, (a) is simpler; revisit per-thread
   episodes later — §6 thread-scoped podcasts.)
2. Poll the job; retrieve the audio.
3. **Ingest the produced podcast as an AI note** into Open Brain: body = the
   transcript, referencing **both** the source set **and** the audio file.
4. **Thread closure:** link that note to **all threads** the Step-1 sources resolved
   to (from the report). Because ON↔OB1 share Postgres, this is `link_source_to_thread`
   rows; the note becomes a first-class thread member, **visible to the wiki compiler**
   — so the episode also grounds the wiki, closing the research loop the operator
   described.

## G. How the four compound (why this is a services proposal, not a feature)

The operator's key point: this is the **first chained consumer** of a reusable set.
The compounding (the [[research-engine-plan]] third pillar):

```
 link → [C1 assigner] → thread + grounded claims → [C2 backfiller] → fewer gaps
        → [C3 report] → [C4 podcast note] → linked back onto the same threads
        → next day's research reuses those grounded claims (cheaper) → repeat
```

- **C1 (assigner)** is the general **front door for any new knowledge** —
  de-fragmentation for research, links, agent output, ON imports alike.
- **C2 (backfiller)** is general **brain-health** — every ungrounded claim it drains
  raises the brain's reusable-claim ratio, making *all* future research cheaper, not
  just this podcast.
- **C3/C4** are the **podcast-shaped composition** of C1+C2 over the daily digest —
  the proof that the services chain.

## H. Revised smallest-viable first cut (supersedes §8)

1. **Wire C3 → C1 (no new services):** in a standalone script, extract+unwrap+filter
   one day's label-email links (§3.2), POST each as a research package to the **live
   curator**, and eyeball the thread decisions (`existing` vs `new`) + the source
   report. *De-risks the assigner-as-link-router assumption — the alt `source-package`
   inlet only gets built if this proves too heavy.*
2. **C2 spike:** point a throwaway script at the live `ungrounded_claims` view, ground
   **one** claim via Wikipedia + `link_claim_to_source`, and confirm the confidence
   trigger drops it from the view. *De-risks the backfiller before it's a worker.*
3. **C4 by hand:** feed one day's report to ON `POST /api/podcasts/generate`, ingest
   the result as an AI note, and link it to the report's threads manually. *De-risks
   the ON API surface + the loop-closure write.*
4. Only then wire the automated `digest → enrich(C3) → backfill(C2) → podcast(C4)`
   chain step and the email link-back (§4.4 / §5).

Every step is additive, reversible, and leans on already-deployed services — the new
build surface is **one worker (C2) + one chain step (C3/C4 orchestration)**, not four
components from scratch.

## I. Open questions the new framing adds (extend §7)

16. **Link-as-research-package vs. source-package inlet** (C1 §C) — does each digest
    link become a one-shot synthesis (full claims participation) or a bare source
    (lighter, weaker compounding)? Recommended: research-package.
17. **Backfiller edge-type/authority + corpus policy** (C2 §D) — `corroborates` @ 0.85
    for Wikipedia; chase its cited primaries later? Which corpora behind the interface?
18. **Backfiller trigger + scope** (C2 §D) — event-driven vs. swept; today's-threads-first
    vs. whole-brain; per-run budget.
19. **C4 input mode** — render a `content` script (multi-thread day) vs. `notebook_id`
    grounded briefing (single thread) — and when to split into **per-thread episodes**
    (the §6 thread-scoped-podcast endgame).
20. **Verify the 2026-06-06 ON podcast changes** (§F warning) before depending on them.
21. **Curator load** — routing every digest link through the curator's LLM Stage-2 adds
    N decisions/night; confirm against the off-peak GPU budget ([[llama-swap-perf-tuning]])
    and consider batching the shortlist.
