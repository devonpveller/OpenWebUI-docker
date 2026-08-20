# Idea Refinery — an upstream idea-honing loop

**Status:** 📐 **design draft — COMPLETE (2026-07-25)** — all decisions settled with the operator:
OD-1..OD-6, RQ-1..RQ-3, and DT-1..DT-5 (§11, §13). **Still in DESIGN by operator request** — the next
artifact is `TASKS-idea-refinery.md` (IR.0–IR.7) when the operator gives the word; no build yet.
"Idea Refinery" is a working name; rename freely.

**One-line intent:** the user has a raw idea, describes it to a model, and walks away. In the
background the stack *researches the idea on the user's behalf* — what already exists in industry
(products and their value propositions) and what we already know about it (related Open Brain
claims, direct or adjacent) — then re-engages the user in Mattermost while the idea is fresh, as a
brainstorm with real depth behind it. Ideas that don't catch **fizzle naturally** (no nagging);
ideas that do get
**honed** — across one or many sessions — into a **credible, targetable, tangible Project Design
Brief**. That brief is the deliverable. What happens to it next — hand it to the dark factory
(agent-org) to build, take it elsewhere, or shelve it — is **the user's choice, never automatic.**

---

## 0. What this is, and the boundary it must respect

This is **not** the agent-orchestration project and must not be folded into it. The dark factory
*implements* things; the Idea Refinery gathers the evidence for *whether an idea is worth
implementing* — who already does it, how they pitch its value, and what we already know — and
sharpens the idea with that context. **The worth call stays with the human** (§3). It sits
**upstream of everything that builds** — earlier even than the PDL's Movement 1 (charter). The
relationship is a one-way, user-gated seam:

```
  raw idea (OWUI chat → Open Brain)
        │   [autonomous]
        ▼
  ┌─────────────────────────────┐
  │      IDEA REFINERY          │   research · evaluate value-to-ai-stack · re-engage · hone
  │  (this doc — a NEW system)  │
  └─────────────────────────────┘
        │   PROMOTE  ← user-chosen, explicit, never automatic
        ▼
  Project Design Brief  ──►  dark factory (agent-org /nl)  ·  PDL charter  ·  elsewhere  ·  shelf
```

**Why it lives outside agent-org (locked by the operator boundary):** the moment the refinery
reaches into the org's tables or its dispatch, the "idea nurturing" concern and the "governed
build" concern blur — exactly the line just drawn. The refinery is **read-mostly + advisory**: it
reads Open Brain, calls the research service, posts to Mattermost, and (only on a user command)
POSTs a finished brief to agent-org's existing `/nl` inlet like any other client. It never merges,
never touches a repo, never spawns a worker. That keeps its safety envelope tiny and the boundary
clean.

### 0.1 The deliberate side effect — a knowledge flywheel

Every research run persists cited **claims + sources** back into Open Brain (via the research
service's curator → `/research/persist`). Those accrete into the same substrate the **wiki
compiler** and **Open Notebook** render. So the refinery is not only an idea-nurturer — it is a
*self-directed R&D reading program for the stack*: as the user's ideas get researched, the stack's
own knowledge base, wiki, and notebooks **grow over time**, and each later idea's research gets
cheaper because prior claims are reusable (the research engine's compounding-reuse economics). This
externality is a feature, and it's a reason to prefer real cited research over throwaway summaries.

The curator already **aligns notebooks** to the research it persists (operator-confirmed
2026-07-25), so each idea's dossier surfaces as an aligned Open Notebook with no extra wiring — the
flywheel is a **reuse, not a build**.

---

## 1. Relationship to the neighbors (what this is NOT)

| System | Concern | Relationship to the Refinery |
|---|---|---|
| **Research service** (`OB1/integrations/research-service/`) | Runs a grounded, cited research job; persists claims | **Reused wholesale** — the refinery is a new *caller*, not a new engine |
| **Open Brain** (`OB1/`) | Stores thoughts + claims + sources | The idea *originates* here; the idea-lifecycle aggregate is added here (§5) |
| **claude-sessions bridge** (`scripts/claude-sessions-bridge/`) | MM thread ⟷ headless `claude -p` session | Powers the *brainstorm* half — one idea thread → one honing session (§7) |
| **Product-Discovery Loop** (PDL, design) | Autonomous *build* toward market fit | A **downstream consumer**: a promoted brief can seed a PDL charter. Different goal — PDL builds features; the refinery decides what's worth a project |
| **agent-org / dark factory** | Governed implementation | A **downstream consumer**, reached only via `/nl`, only on user PROMOTE |
| **automations / n8n** (design) | User-editable workflow glue | An *alternative host* for the driver (§11, OD-2) if we want it visual/editable |

The sharpest contrast: **the PDL is product-shaped (terminal state = "shipped"); the Refinery is
thinking-partner-shaped (terminal states = "user re-engaged and honed it" or "idea went
dormant").** There is no build in this loop at all.

---

## 2. The core model — an idea is a lifecycle, not a row

```
   capture_thought(kind=idea)
        │
        ▼
   NEW ──────────────► QUEUED ──────────► RESEARCHED ──────────► ENGAGED
   (flagged,           (in the           (cited dossier          (user replied in the
    deterministically   sweep, exactly-   posted to its           thread → honing
    logged)             once)             Mattermost thread)      brainstorm, N turns)
                                              │                        │
                             user edits idea  │                        │ user: "make this real"
                             in Open Brain    ▼                        ▼
                          DIRTY ◄──────────────┘                   PROMOTED
                          (re-research as a continuation,          (emit Project Design Brief;
                           seeded w/ prior dossier + the diff;      hand off on user command)
                           append UPDATE to the SAME thread)
                                              │
                          no engagement for T days (no nag)
                                              ▼
                                          DORMANT ──────► REVIVED
                                          (fizzled,       (user opens it, edits it, OR a NEW
                                           stored)         idea lands semantically near it and
                                                           cross-links back — surfaced, not pinged)
```

**The determinism you asked for lives in the sweep, not the judgment.** "Deterministically logs
all user ideas … ensures a research session is run on each" = an **exactly-once, durable,
restart-surviving queue**: every idea flagged as an idea is guaranteed to be logged and to have a
research run on its latest settled state (§6). This is the *same* guarantee the recent agent-org convergence
work hardened — P27 ("never close *done* on an incomplete sweep"), P25 ("the drain loop survives a
transient unverifiable delivery"), P28 (deterministic filters, not an LLM verdict). The refinery's
driver should inherit those lessons, not re-learn them. The **LLM** does only the semantic content
(the research, the synthesis, the brainstorm); the **queue** does the counting and the guarantees.
One nuance: because users work a thought across several chats, a *dirty* idea is **coalesced, not
researched per edit** — a research cycle researches its latest settled revision once (§6).

---

## 3. What "research" means here — evidence-gathering, not value-scoring

**The machine does not judge whether an idea is worth it — the human does, at engagement/promote
time (operator decision 2026-07-25).** The research's job is to bring the user the evidence to make
that call. So each idea's research is a *gathering* task with two streams, not an evaluation:

1. **Industry landscape** — existing **products/tools in industry** that address the idea's
   feature(s), and each one's **value proposition** (what problem it claims to solve, how it
   positions itself). External web research via the existing private search path (SearXNG
   `gateway:8080` → Tor page-fetch), injection-screened, cited. Concrete offerings, not vibes.
2. **Related prior knowledge** — Open Brain **claims** that describe the feature(s) **directly or
   adjacently** (`search_claims`, embedding-broadened so adjacent work surfaces, freshness-gated).
   What have past research sessions already concluded near this idea? Is there a prior dossier to
   continue?

The output is a **grounded, cited Idea Dossier** (synthesis + `[SOURCED]/[INFERRED]/[UNCERTAIN]/
[GAP]` tags + Sources block) — the research engine's grounding discipline verbatim. Crucially the
dossier **presents evidence and leaves the value call open**: it never manufactures a "you should
build this" verdict. An ungrounded assertion is an honest `[GAP]`, not an invented case.

> **Why this framing is better:** it keeps the machine in its lane (find what exists, recall what we
> know) and the human in theirs (decide what it's worth). It also drops the dependency of modelling
> the ai-stack's own state — the research doesn't score *against* the stack, so it doesn't need to
> know the stack. Credibility for the idea comes from *knowing the landscape and not reinventing
> prior conclusions* — exactly what "hone into something credible, targetable, tangible" needs.

> **Scope of the domain (v1):** ideas are AI-related tooling/feature ideas. The machinery is
> idea-domain-agnostic; the gathering frame above (industry products + related claims) is v1's
> default. General personal ideas can later flip the frame per idea/channel (§11 OD-6).

### 3.1 The dossier — gap-centered (DT-2)

What the returning human sees in the thread, in order:

1. **The idea, in the user's own words** — so they reconnect fast.
2. **Landscape** — existing products/tools: *name · what it is · its value proposition · citation.*
   Capped at ~5 with "+N more found" so it stays skimmable, not a wall (DT-2a).
3. **What we already know** — related OB claims (direct + adjacent), each cited, with freshness
   ("you previously concluded X") — the not-reinvent-the-wheel value.
4. **Gaps — the engagement spine (DT-2b).** Where the idea sits *differently* from the landscape
   ("no product found does X that you mentioned"), what the research couldn't ground (`[GAP]`), and
   the open questions. These are **descriptive, never evaluative** — "no one does X" is evidence;
   "so it's worth building" is the human's call, never the dossier's.
5. **An open invitation** — "what would you want to explore?" — the single fresh nudge, no verdict.

The **full cited synthesis lives in Open Brain / the notebook** (feeding the wiki flywheel); the
thread gets a tight **digest** so Mattermost stays readable (DT-2c).

**Gaps are the point (operator, 2026-07-25): "generating gaps is the relevant engagement."** A
research pass is *good* when it surfaces sharp, honing-worthy gaps — not when it reaches a
conclusion. Gaps are what pull the user back and what the brainstorm works through (§7.1).

---

## 4. The reuse surface — the "research + deliver" half already exists

None of this is a build; it's a new caller of live parts:

| Capability | Reused as-is | Entry point |
|---|---|---|
| Autonomous research trigger (no human msg) | `POST /research` → 202 + job poll (or `grounding.advise`) | `research-service/index.ts:588`; `agent-bridge/app/modules/grounding.py:161` |
| Search prior claims + industry web, cited | claims-reuse then web-gather harness | `research-service/harness.ts`, `kb.ts` |
| Persist cited claims/sources back to OB | curator → single writer `/research/persist` | `research-curator/`, `index.ts:1635` |
| One MM thread per idea, appends as updates | channel + root post = thread; `post(…, thread_id=root)` | `agent-org/.../adapters/mattermost.py:95`, `router.py:161` (open_effort pattern) |
| Brainstorm = thread ⟷ headless session | `thread_root → session_id` map; reply = resume | `scripts/claude-sessions-bridge/bridge.py` |
| Durable, gentle, exactly-once driver loop | `_capacity_drain_loop` + `ParkStore` shape | `orchestrator.py:570`, `capacity_park.py` |
| Don't self-DoS the GPU | llm-queue admission budgets + backpressure park | (research service is already queue-governed) |

---

## 5. New persistent state — the Idea aggregate

The raw idea is an Open Brain `thought`; the **lifecycle** is a new aggregate. Today `thoughts`
(`OB1/docker/init.sql`) is append-only with no `type`/`status`/`version` columns — "idea" is only a
soft `metadata.type` string set by the LLM extractor (`index.ts:207`). So the aggregate is
net-new — **but the model is already battle-tested one layer over and should be lifted, not
invented:** `claims.status ∈ {active,retracted,superseded}` + `contradicts` edges + freshness
windows (`init-claims.sql`), plus the `source_revisions` history table and the staged-tombstone
supersede-in-place pattern (`init-source-revisions.sql`, `init-sources.sql:59`).

Proposed tables (host = OB Postgres, next to `claims`; see OD-2 for the driver's home):

```
ideas
  id              TEXT PK              # idea-<slug>  (STABLE identity across revisions — §11 OD-3)
  thought_id      BIGINT FK thoughts   # the originating capture (latest revision)
  title           TEXT
  summary         TEXT                 # current canonical statement of the idea
  embedding       vector(1024)         # for dedup + resurfacing (§8)
  status          TEXT                 # new|queued|researched|engaged|dirty|dormant|promoted|archived
  domain          TEXT                 # v1 default; selects the research gathering frame (§3)
  thread_root     TEXT | NULL          # the Mattermost root post id = the idea's thread
  session_id      TEXT | NULL          # bound headless session (set lazily on first engagement, §7)
  last_job_id     TEXT | NULL          # the research job whose dossier is current
  dossier_source  TEXT | NULL          # the OB source row holding the current synthesis
  created_at, updated_at
  engaged_at      TIMESTAMP | NULL     # last human turn in the thread (drives dormancy, §8)
  dormant_at      TIMESTAMP | NULL

idea_revisions                         # append-only lineage — mirrors source_revisions
  idea_id         TEXT FK ideas
  revision        INT
  summary         TEXT                 # the idea text at this revision
  thought_id      BIGINT               # the capture that produced this revision
  research_job_id TEXT | NULL          # the research run for this revision (exactly-once, §6)
  created_at
  PRIMARY KEY (idea_id, revision)
```

**Dirty is a version bump, not a mutation.** Editing the idea appends an `idea_revisions` row and
sets `ideas.status='dirty'`. Prior dossiers/revisions are never destroyed — they are the
continuation context (§7). **Research is not fired per edit** — users commonly work a thought across
several chats, so revisions *coalesce*: a research cycle researches an idea's **latest settled
revision once** (exactly-once per idea per cycle, not per revision — §6). Intermediate revisions
keep their lineage but don't each spawn a run; `idea_revisions.research_job_id` is set only on
whichever revision a run actually targeted.

### 5.1 The inlet — dedicated MCP tooling (OD-1, OD-3)

The idea enters through **purpose-built MCP tools**, not a classifier guessing that a thought is an
idea (operator decision 2026-07-25: consistency is the priority, so the tool — not the LLM — owns
the flag). Two operations, both of which write the `ideas`/`idea_revisions` aggregate as the source
of truth and mark the idea as owing a research run:

- `capture_idea(title, body[, domain])` → creates an `ideas` row (+ revision 1) and returns a
  stable `idea_id`. Also writes the underlying `thought`, so ideas stay first-class OB captures.
  On capture it **runs `find_idea` first** and, on a near-duplicate (≈0.30 cosine), **asks** "update
  your existing idea *X* instead?" rather than silently forking (DT-3).
- `update_idea(idea_id, body)` → appends an `idea_revisions` row and sets `status='dirty'`. This is
  the deterministic **"same idea, modified"** signal — identity is explicit, never inferred. It
  **marks the idea as owing research; it does not research on the spot** (the coalescing debounce,
  §6). The *bidirectional* flow (§2) is: add/update → (batched or deliberate) research → alert.
- `research_idea(idea_id)` → the **deliberate** trigger: research this idea *now*, bypassing the
  nightly debounce — for "I'm done working it, go."

**What OWUI shows back — an acknowledgement, not the research (2026-07-25).** Research is async and
lands in Mattermost (§7), and a finished OWUI chat cannot be pushed to — so every tool returns a
small **ack payload** `{idea_id, title, status, when_researched: 'overnight'|'now', mattermost:
{channel, thread_url?}}`, from which the model composes a truthful OWUI reply: *what* was logged,
*when* it will be researched, and the Mattermost thread where findings land. It never returns
dossier content it does not have yet. `research_idea` **pre-creates** the thread and returns its
`thread_url` (a live "follow along" link); the overnight `capture_idea` path has no thread yet.

The tool **records** the owed work; the **driver's sweep still performs it** (§6). Keeping the tool
as the recorder and the sweep as the performer means a trigger is never lost if the driver is down —
the guarantee lives in the durable table, not a fire-and-forget call. (An optional push-notify to
the driver is a latency optimization on top.)

**Identity resolution on update (RQ-1, resolved 2026-07-25).** The idea tooling **mirrors the
existing Open Brain thoughts-capture family** — `capture_idea` ↔ `capture_thought`, `find_idea` ↔
`search_thoughts`/`list_thoughts`, plus `update_idea`. On a return visit the user's model calls
`find_idea` (title/embedding search over `ideas`) to resolve the right `idea_id` before
`update_idea`, exactly as it would `search_thoughts` before acting on a thought — same ergonomics,
same mental model, so an update lands on the existing idea instead of forking a duplicate.

---

## 6. The driver — a cron-triggered nightly batch (not an always-on loop)

The user works ideas across several chats, so the driver is **debounced by design**: rather than a
bespoke always-on loop reacting to every edit, it's a **nightly batch** — a `POST /run` handler in
the existing scheduled slice (§6.1) that **starts the throttled drain of the owed-research queue for
its window (§6.2)** — plus the deliberate `research_idea` MCP trigger for on-demand. This is
*thinner* than an always-on driver and matches how the stack already schedules overnight work. One
`/run` pass:

- **Detect + log (deterministic).** New ideas and updates enter via the MCP tooling (§5.1), which
  writes the `ideas`/`idea_revisions` rows directly — so "flagging" is an explicit tool call, not a
  classifier guess. The sweep reconciles any not-yet-processed idea/revision exactly once. This is
  the "curated list" — a table whose guarantee is *nothing flagged is ever silently skipped* (P27).
- **Research the owed work (coalesced).** For each idea `status ∈ {new, dirty}`, enqueue **one**
  research job on its **latest** revision (frame §3; seeded with the prior dossier + the diff if
  `dirty`) — rapid multi-chat edits since the last cycle collapse into a single run. Jobs go through
  the **throttled, roll-over drain (§6.2)** — bounded in-flight, backpressure-aware — so a heavy
  capture day never self-DoS's the shared GPU; the overflow rolls to the next cycle.
- **Deliver (idempotent, survives a flaky post).** On job completion: render the dossier, create
  the idea's Mattermost thread (or append an UPDATE if it exists), persist `thread_root` +
  `last_job_id`, set `status='researched'`. A failed Mattermost post must **not** lose the research
  or double-post — retry against the persisted `last_job_id` (the P25 "survive a transient
  unverifiable delivery" lesson).
- **Age.** Ideas with no human turn for `T` days → `dormant` (no message sent — see §8).

Everything the batch *counts on* — exactly-once, never-skip, survive-transient-failure, no silent
caps — is a solved shape in the agent-org drain work. The refinery's driver is that shape pointed
at ideas, on a nightly clock.

### 6.1 Scheduling — slot into the existing nightly pipeline (before the 1am wiki compile)

The batch reuses the **`openbrain-cron`** staging that already runs the overnight work — no new
scheduler:

- **Deploy as an HTTP-triggered service** (`openbrain-idea-refinery`, `POST /run`) in
  `OB1/docker/docker-compose.scheduled.yml`, on `obnet` + `llm-net` (+ `search-gw-net` for
  search-over-Tor), mirroring `openbrain-research` / `openbrain-gmail-pull`.
- **Add one stand-alone crontab line** in `OB1/docker/cron/crontab` firing **~02:00–03:00 UTC**
  (≈22:00–23:00 EDT), e.g. `0 3 * * * curl -fsS -X POST http://openbrain-idea-refinery:8080/run ||
  true`. That gives ~2h headroom (a run can take up to `RESEARCH_WAIT_MS` = 2h) so the night's new
  claims land **before** the wiki's deterministic **1am-local** compile (`openbrain-wiki`,
  `WIKI_RECOMPILE_HOUR=1`) — the flywheel (§0.1). **Do not** splice it into the 1am Gmail chain
  (that *starts* at 1am — too late).
- The wiki also runs a **~3-min change-watch**, so newly-landed claims are picked up reactively even
  off-cadence; the pre-1am slot is the clean deterministic path, not a hard dependency.
- **DST caveat:** the crontab is fixed UTC while the wiki uses `America/New_York`, so the
  pre-compile ordering only holds in summer unless the crontab hour is bumped at the DST transition
  (documented in the crontab header). The ~02:00–03:00 UTC slot keeps enough headroom that a ±1h
  drift stays harmless.

### 6.2 One throttled research queue — for both fresh ideas and the backfill

Two things independently threaten the GPU: many *revisions* of one idea (handled by coalescing, §6)
and many *ideas* owing research at once — a busy capture day, or the migration backfill dumping a
whole history. The operator's point (2026-07-25): these are the **same timing problem**, so they
share **one throttle**, not two.

**The owed set *is* the queue.** `ideas` needing research — fresh (`new`/`dirty`) and backfilled
(dormant, never-researched) — are the work. A **bounded-concurrency, submit-on-complete drain** (the
pattern the migration script already implied: submit one, wait for the event, submit the next)
processes them:

- **Bounded in-flight (K ≈ 1–2).** Never dump N jobs; submit up to K into the research service, and
  only submit the next when one completes. The research service's own FIFO drain (concurrency 1) +
  the **llm-queue admission budget under a distinct `idea-refinery` origin** are the hard GPU guard;
  the drain honors 429 backpressure (park + retry next window).
- **A drain, not a flush.** The nightly batch (§6.1) *starts the drain for its window*; whatever
  doesn't finish safely **rolls over** to the next cycle. Under heavy load exactly-once still holds —
  it just spans more cycles. The batch never races to empty the queue.
- **Priority: fresh before backfill.** A returning user's fresh idea drains ahead of the backfill
  trickle, so it isn't stuck behind 200 migrated ideas. Backfill is the low-priority tail.
- **Migration reuses this exact drain (IR.7).** The migration script only *marks* existing ideas as
  owed (dormant backfill); the same drain paces their research. There is no separate migration
  throttle to build.

This is deliberately thin — a small bounded-concurrency drain over the owed set, leaning on the
research service + llm-queue for the real GPU governance, not a bespoke scheduler.

---

## 7. Engagement — one thread per idea, honed over sessions

**Two surfaces, distinct roles (2026-07-25).** OWUI is the **capture** surface (synchronous,
ephemeral — and *unpushable* once the chat ends); Mattermost is the **honing** surface (durable,
pushable via the bridge). That asymmetry is *why* research re-engagement is in Mattermost, not OWUI.
In OWUI the model only **acknowledges** (§5.1): what was logged, *when* it will be researched
(overnight for `capture_idea`, now for `research_idea`), and the Mattermost thread findings will
land in — a deliberate `research_idea` **pre-creates** that thread and returns a live "follow along"
permalink. The dossier is never forked into OWUI.

**Placement.** Each idea gets one Mattermost thread (root post = idea, appends = research updates),
in a dedicated channel (working name `#ideas`), created once via `ensure_channel`. The dossier is
posted as the root; every `dirty` re-research appends an **UPDATE** into the *same* thread —
"here's more research and a re-evaluation, given your change" — exactly as you described.

**Brainstorm = the claude-sessions bridge, seeded lazily.** The honing conversation is a headless
`claude -p` session bound to the idea's thread (`thread_root → session_id`, the bridge's existing
map). Two choices, and I recommend **lazy-seed**:

- *Pre-warm* (spawn a session per idea at research time) pays for a session on every idea, but most
  ideas *should* fizzle — that's wasteful.
- **Lazy-seed (recommended):** the dossier sits in the thread; the session is created only when the
  user **first replies**, and its first turn is seeded with the idea's dossier + prior-revision
  history as context. You only spend a session on ideas the user actually re-engages, which is
  exactly aligned with "let ideas fizzle naturally." Requires a small bridge enhancement: when
  starting a session for an `#ideas` thread, load that idea's dossier as context (via
  `--append-system-prompt` or a seeded context turn).

**A `dirty` update mid-brainstorm** appends to the same thread and (optionally) wakes the bound
session via the bridge's `follow_thread` mechanism, so an active honing session absorbs the new
research. (Gotcha to respect: for a backend post to *wake* a following session it must be posted
under a non-`bot-claude` identity without `from_*` props — `bridge.py` `follow_matches`.)

**PROMOTE is a first-class, human-only action.** When the user is satisfied, a command in the
thread ("make this real" / "promote") **finalizes the design draft that accreted through the
gap-working brainstorm** (§7.1) into a **Project Design Brief** (§9) and hands it off where the user
directs. The refinery never promotes on its own.

### 7.1 Gap-driven honing — the plan develops along the way

The honing conversation is not open-ended chat; it is **gap-driven**, and it mirrors exactly how
*this* design doc was built — surface a gap/decision, resolve it, the plan advances a step (operator
insight, 2026-07-25).

- **Gaps are the engagement.** The dossier's gaps (§3.1 #4) are the hooks; the brainstorm works
  them one or a few at a time.
- **Each resolution accretes into an evolving design draft** — the plan is *developed along the
  way*, decision by decision, not synthesized cold at the end; by PROMOTE-time the brief is mostly
  already written (§9).
- **The document framework IS the accretion mechanism (operator, 2026-07-25).** The
  proto-Project-Design-Brief is a **fixed structured template** (§9) with named slots; honing
  *fills the slots* rather than inventing structure — the template itself answers "where does this
  decision go." Each slot admits only **accredited content**: a **grounded claim** (cited to a
  source, via the same claims layer the research systems use) or an **explicit human decision**
  (attributed to the operator). **Model-invented facts are forbidden** — the model drafts and slots,
  but every asserted fact traces to a source-claim or a human decision; anything ungrounded is
  tagged `[GAP]` / dropped into `open_questions`, exactly the research synthesizer's
  `[SOURCED]/[INFERRED]/[UNCERTAIN]/[GAP]` discipline. This is the anti-hallucination guarantee for
  small models: the plan is an *assembly of validated claims + attributed decisions*, not free
  generation.
- **Keep the harness thin (don't over-engineer for a model that will evolve).** Reliability comes
  from **structure + grounding**, not a clever capture state machine — so capture stays simple (slot
  on an explicit "lock it in" and/or a session-end sweep) and survives model turnover. We
  deliberately do *not* build elaborate capture-timing logic a better model would later make moot.
- **New gaps are generated as it explores.** Just as this conversation surfaced new DTs/RQs while
  resolving others, the session can raise fresh gaps mid-brainstorm; and a `dirty` re-research can
  inject new ones. Gap-generation is ongoing, not a one-time research artifact.
- **Progress = gaps closing.** The idea matures as its gaps resolve. This is a *presence-time*
  signal only — it is **never** used to nag (no "you have 5 open gaps" pings; the no-nag rule, §8
  and §10, is absolute). Gaps engage when the user is here; they never chase.

### 7.2 The brainstorm is a GROUNDED research consultant (redesign 2026-07-26) — supersedes the plain chat

The first implementation made the brainstorm a plain local-model chat with **no tools** — which answers
from the model's own (hallucination-prone) memory, rendering the discussion useless and bias-confirming.
**Superseded.** The brainstorm bot is instead a **grounded, fact-driven research consultant**, enforcing
in the *live conversation* the very source→claim discipline §3 / §7.1 / §9 already demand:

- **Same MCP tooling as OWUI.** The thread has the full Open Brain toolset — above all `search_claims`
  (the grounding tool: "what research has ESTABLISHED", each claim source-anchored + confidence-scored)
  and the research trigger (`research` / `research_idea`).
- **Every reply is grounded in a claim — or it is a gap.** The hard loop on each user message:
  1. **Retrieve** — `search_claims` for the query FIRST.
  2. **Draft** — claims found → draft an answer **grounded, citing them**.
  3. **Validate** (the make-it-true round) → §7.2.1.
  4. **Gap** — no claim answers it → **name the gap and trigger research** on that specific angle; do
     NOT answer from the model's own knowledge.
- **Never from model memory.** An ungrounded answer is the failure mode (hallucinated, bias-confirming).
  The bot is **unbiased + fact-driven** — a research consultant that states only what researched claims
  support and flags everything else as a gap to research. This grounding is what makes the research
  service trustworthy and the whole honing loop useful.

**The crux (hard problem): ENFORCING grounding on a local 27B.** A model answers from memory unless
forced. Prompt-only is weak → **structural enforcement**: the agent MUST `search_claims` before a final
reply, and a reply not backed by a returned claim is rejected/reworked into a gap→research (mirroring the
research engine's `[SOURCED]/[GAP]` discipline, §9). Make-or-break.

**Gap flow (latency):** research takes minutes, so on a gap the bot says "no grounding for that yet —
researching it," triggers the LOCAL research engine, and follows up in-thread when the claims land
(reusing the drain's delivery). All local.

#### 7.2.1 The validation round — "thesis, then validated until true" (mirrors the research synthesizer)

Retrieval + a grounded draft is not enough — a 27B *will* smuggle an unsupported sentence into an
otherwise-cited answer. So the draft (step 2) is a **thesis**, and a **separate validation pass** makes
it true before it is posted — exactly what the research engine already does when it produces a session
synthesis:

- **The check:** a second LLM round is handed the draft **and the claims/sources it was built from**,
  and answers, per assertion, the single question *"is this supported by the provided source?"*
  **Unsupported → removed. Supported → kept.**
- **The outcome is a grounded synthesis:** what survives validation is, by construction, only what the
  researched claims support — the accreditation §3/§9 demand, now enforced on the *live reply*.
- **This is the research engine's own discipline.** In `research-service/harness.ts` the synthesizer
  runs under ABSOLUTE RULES (every line `[SOURCED]`/`[INFERRED]`/`[UNCERTAIN]` + a `[Source N]`
  citation, or it is demoted to a `[GAP]`), then `buildCitedAndRenumber` **structurally strips** any
  claim citing a source that isn't in the pool (cited-only, GROUNDING-MODEL §6.3), over an iterative
  decompose→coverage→deepen loop (thesis → validate open needs → repeat until covered). The brainstorm
  reuses that same "thesis → validate → keep-only-what-holds" shape as its per-reply validation round.
- **A stripped-to-nothing reply is a gap, not an empty reply.** If validation removes everything (the
  claims did not actually support an answer), that collapses to step 4 — name the gap, trigger
  research — never a hollow or hallucinated fallback.

#### 7.2.2 Enforcement control flow (per user message)

Grounding is not asked for in a prompt — it is imposed by the loop. **Deterministic where it must be
(the gates are CODE), model-driven where the model should evolve** (draft / validate / render are LLM
calls) — the balance §5 asked for. One user message in a thread runs steps 0–4:

**0 · Bind + load.** The thread ↔ idea binding already exists (the drain delivered *this idea's* dossier
as the thread root). Load the idea + its known claims as working context.

**1 · RETRIEVE — Gate A (must-ground).** The harness ITSELF issues `search_claims` (idea-scoped, seeded
with the user's message) before qwen emits any prose. The agent may call it again to refine an angle,
but a reply is **never reachable with zero claims in context** — enforced by code, not instruction.
Nothing found at all → jump straight to step 4.

**2 · DRAFT (the thesis).** qwen drafts from the returned claims ONLY, under the research synthesizer's
ABSOLUTE RULES: one assertion per line, each `[SOURCED]/[INFERRED]/[UNCERTAIN]` + a claim citation, or
it is a `[GAP]`. The draft is machine-truth (one-claim-per-line), exactly like `harness.ts`.

**3 · VALIDATE — Gate B (must-validate).** A **separate** LLM call receives ONLY the draft lines + the
claim/source each one cites, and returns structured JSON `[{line, supported: true|false}]` — the single
question *"is this line supported by its cited source?"* CODE strips every `supported:false` line.
Validation can only **remove**; it can introduce no new fact. (This is the live-reply analogue of
`buildCitedAndRenumber`'s cited-only strip + the synthesizer's "thesis, validated until true.")

**4 · OUTCOME / COLLAPSE.**
- **≥1 line survives** → render survivors into readable prose (ABSOLUTE RULE: introduce no fact not in
  the validated answer, §7.2.1), post to the thread **with citations**. This is the grounded synthesis.
- **0 survive** (all `[GAP]`, or validation stripped everything) → it is a **GAP**: post "no grounding
  for that yet — researching it," trigger the LOCAL research engine on that specific angle, and follow
  up in-thread when the claims land (reuse the drain's delivery). **Never** an ungrounded fallback.

**Models.** Retrieve = no model (code calls the tool). Draft / validate / render = `qwen36-27b:nothink`
(nothink avoids the thinking-variant empty-reply bug at bounded `max_tokens`; low-temp + structured JSON
on validate). The model is a swappable knob; **the two gates are not.**

**Bounds (no loops, no GPU storms).** One research trigger per gap per message; dedupe against
`ideas_owed_research` (already researching this idea/angle → "already on it," no re-queue). The whole
turn is a single pass through 0–4.

**Open decisions (now narrowed):**
- **(b) Tool set** — the flow strictly needs `search_claims` + a research trigger + delivery. Do we
  still expose the FULL OWUI toolset (the operator's "same capacity as OWUI"), or a grounding subset,
  given the gates constrain the reply either way? Leaning: full toolset available, gates applied on top.
- **(c) Tool access — RESOLVED (operator-approved 2026-07-26): Option A, an MCP client to
  `openbrain-mcp`** (true OWUI parity, "same MCP tooling"), full toolset available with the two gates
  layered on top. Not the direct-DB shortcut — the thread must be as capable as OWUI, not a subset.
- **(a) enforcement = structural** and **(d) gap round-trip = step 4** are resolved above.

---

## 8. Fizzle, revive, resurface — the emotional design (the biggest open area)

This is the part with the least prior art and the most product judgment, so it's called out as its
own section and revisited in §11 (OD-4). The proposed defaults:

- **Fizzle = silence, not a stop.** After research posts once (the "while it's fresh" moment),
  there is **no follow-up ping**. If the user doesn't engage within `T` days (default **14**), the
  idea goes `dormant` quietly. Dormant is a resting state, not a deletion — the idea and its dossier
  are kept.
- **One post, no nagging.** The single research post *is* the re-engagement. We do **not** chase.
  (An optional single scheduled nudge is a dial in OD-4, default off.)
- **Revive triggers (all user-respecting):**
  1. the user replies in the thread (direct);
  2. the user edits the idea in Open Brain (→ `dirty` → re-research → UPDATE in the same thread);
  3. **resurfacing** — a *new* idea lands semantically near a dormant one (embedding distance
     ≈**0.40** cosine — tighter than the research engine's ≈0.55 reuse cutoff, so cross-links are
     clearly related, not noisy). The new idea's dossier **cross-links** back ("this connects to
     your earlier idea *X*, which you parked") — surfaced *in the new idea's thread*, never a cold
     ping on the old one.
- **User-driven rummaging.** "Show me my parked ideas" / "what did I have about caching?" is a
  read over the `ideas` table + embedding search — the "explore old ideas with fresh eyes"
  affordance, on the user's clock.

The whole posture: **encourage while fresh, then get out of the way.** The system's job is to make
the *first* re-engagement worth it, not to hound.

---

## 9. The hand-off seam — the Project Design Brief

The deliverable — **accreted through the gap-working brainstorm (§7.1), finalized at PROMOTE** (not
synthesized cold in one shot). Structure:

```
Project Design Brief
  idea_id / title
  problem               # the need, in the user's terms
  industry_landscape    # existing products/tools + their value propositions (CITED) — the lay of the land
  related_claims        # OB claims describing the feature(s) directly or adjacently (CITED)
  rationale_to_proceed  # the HUMAN's reason this is worth building (value is human-determined, §3)
  proposed_approach     # the honed, targetable design direction (from the brainstorm)
  scope / out_of_scope
  open_questions        # what's still uncertain — honest, not hidden
  suggested_target      # dark factory (agent-org) | PDL charter | manual | shelf — USER picks
  provenance            # links: OB thought(s), research job(s), the Mattermost thread
```

**Every slot is accredited (§7.1).** Content is either a **grounded claim** (cited to a source) or
an **attributed human decision** — never a model-invented fact. Ungrounded assertions live in
`open_questions` as honest `[GAP]`s, not smuggled into the plan as fact. The fixed structure is what
makes accretion deterministic: honing *slots* validated content, it doesn't author prose freely.

**Where it can go (user's choice each time):**
- **Dark factory** — POST the brief to agent-org `/nl` as an advisory or an effort request; the
  org's own governance (floor, gates, human merge) takes over from there. The refinery's
  involvement ends at the hand-off.
- **PDL** — the brief seeds a `discovery_loop` charter (Movement 1 input).
- **Elsewhere / shelf** — export the brief; or keep it in `ideas` as `promoted` for later.

Because the brief carries full provenance, whatever consumes it inherits a cited rationale, not a
bare one-liner.

---

## 10. Safety & non-negotiables

Small envelope by construction, but stated explicitly:

- **No autonomous building, ever.** The refinery never merges, pushes, spawns a worker, or hands
  off to the dark factory without an explicit user PROMOTE. The one irreversible-ish action
  (handing a brief to a builder) is human-initiated.
- **Gentle on the shared GPU.** Enforced by the **single throttled drain (§6.2)** shared by fresh
  ideas and the backfill: bounded in-flight, submit-on-complete, off-peak, backpressure-parked,
  overflow rolls over. An autonomous loop that floods the inference plane is a regression, not a
  feature (prior pain: LiteLLM health-probe thrash, OOM). Hard requirement from day one.
- **No nagging.** Re-engagement is one post while fresh; dormancy is silent (§8). The system must
  be easy to ignore.
- **Deterministic sweep, honest gaps.** Nothing flagged is silently skipped (P27); ungrounded
  value claims are `[GAP]`, never invented (research grounding discipline).
- **Observable.** Every research query, dossier, and update lands in the idea's thread; the
  `ideas`/`idea_revisions` tables are the audit trail.
- **Read-mostly on Open Brain.** The refinery reads thoughts/claims and writes only its own
  lifecycle tables + (via the research service) cited claims. It doesn't mutate user thoughts.
- **No invented facts in the plan (source → claim accreditation).** Like the research systems,
  everything asserted in a dossier or brief traces to a source-grounded claim or an attributed human
  decision; the small model never manufactures facts. Ungrounded → `[GAP]`. This is the primary
  anti-hallucination guarantee (§7.1), and it holds regardless of which model backs the loop.

---

## 11. Decisions

**Resolved with the operator (2026-07-25):**

- **OD-1 — Inlet trigger → dedicated MCP tooling.** An idea entry into Open Brain must fire a
  trigger; rather than lean on the LLM's soft `metadata.type=="idea"`, we add **dedicated MCP
  tooling** (`capture_idea` / `update_idea`, §5.1) that deterministically records the idea and its
  identity. Consistency is the reason — the tool owns the flag, so nothing depends on a classifier
  guessing right.
- **OD-2 — Driver home → dedicated service, reusing the research curator.** The refinery is its own
  service outside agent-org; it reuses the existing **research curator**, which already aligns
  notebooks — so dossier persistence + notebook/thread alignment are reuse, not new code (§0.1, §4).
  Concretely (2026-07-25): an HTTP-triggered `POST /run` batch service (`openbrain-idea-refinery`) in
  the `openbrain-cron` scheduled slice — a nightly cron line, **not** an always-on loop (§6.1).
- **OD-3 — Identity & dirty → explicit, tool-owned; bidirectional.** Because the MCP tool owns the
  `idea_id`, "the same idea, modified" is a deterministic `update_idea(idea_id, …)` call, not fuzzy
  matching. The flow is **bidirectional**: *add idea → process → Mattermost alert (new thread)*;
  *update idea → process → Mattermost alert (UPDATE in the same thread)*. Beyond those alerts, the
  conversation is natural in the Mattermost session (§2, §5.1, §7).
- **OD-4 — The feel → one post, no nag, silent dormancy, resurface-on-related.** Confirmed (§8).
- **OD-5 — Engagement → lazy-seed, modular + expandable.** Confirmed; the engagement surface is a
  pluggable layer (Mattermost now, other surfaces later) (§7).
- **OD-6 — Rollout → evidence-gated, then migrate.** Domain stays ai-stack-improvement for v1. The
  system is **built + tested with evidence of end-to-end correctness BEFORE deployment**; once live,
  a **throttled migration/backfill** seeds the backlog from existing OB ideas (§12.1, IR.7).

**Residual sub-questions — also resolved 2026-07-25:**

- **RQ-1 — identity resolution on update → mirror the thoughts-capture family.** The idea tooling
  copies OB's thought ergonomics (`capture_idea`/`find_idea`/`update_idea` ↔ `capture_thought`/
  `search_thoughts`/`update`); the model `find_idea`s to resolve the right `idea_id` before
  `update_idea` (§5.1).
- **RQ-2 — migration flood → the shared throttled drain (§6.2).** The one-off Python migration only
  *marks* existing ideas as owed (dormant backfill); the **same** bounded-concurrency drain that
  paces fresh ideas paces the backfill as its low-priority tail — one throttle, not two (§12.1,
  IR.7).
- **RQ-3 — the evidence bar → fake-driven E2E harness + a real staging run.** Accepted; the goal is
  risk reduction, not zero risk — post-deployment issues are expected and handled iteratively
  (§12.1).

---

## 12. Phased build (each deployable, tested, reversible)

- **IR.0 — Idea aggregate + deterministic inlet.** `ideas` + `idea_revisions` tables; the inlet
  contract (OD-1); the reconciler sweep (nothing flagged is skipped). *Tests:* capture→logged
  exactly once, edit→new revision, restart-survives, no double-log.
- **IR.1 — Research the batch.** The `POST /run` batch service (in the scheduled slice, §6.1)
  enqueues one research job per owed idea (latest revision, coalescing multi-chat edits; frame §3),
  budget-capped + backpressure-parked; persist the dossier. Deliberate `research_idea` bypasses the
  debounce. *Tests:* one run per idea per cycle (not per revision), coalescing, reuse-prior-claims,
  backpressure park, no self-DoS.
- **IR.2 — Deliver to Mattermost.** Create the idea thread, post the dossier; idempotent + survives
  a flaky post. *Tests:* thread created once, UPDATE appends to the same thread, retry doesn't
  double-post.
- **IR.3 — Dirty → continuation.** Edit detection → `dirty` → re-research seeded with prior
  dossier + diff → UPDATE in the same thread. *Tests:* re-research uses prior context, same thread,
  revision lineage intact.
- **IR.4 — Brainstorm engagement.** Lazy-seed the bound session on first reply (OD-5); the honing
  conversation. *Tests:* session seeded with dossier, reply=resume continuity.
- **IR.5 — Fizzle/revive/resurface.** Dormancy timer (no nag), revive-on-engage/edit, embedding
  resurfacing + cross-link, "show my parked ideas". *Tests:* silent dormancy, resurface threshold,
  cross-link lands in the new thread not the old.
- **IR.6 — Promote → Project Design Brief.** Synthesize the brief with provenance; user-chosen
  hand-off to agent-org `/nl` / PDL / export. *Tests:* brief structure + citations, hand-off only on
  explicit command, provenance links resolve.
- **IR.7 — Migration / backfill (post-go-live, OD-6/RQ-2).** A **standalone one-off Python script**
  that **marks** existing OB idea-typed thoughts as owed (dormant backfill) — it does **not** run
  its own throttle; the **same throttled drain (§6.2)** paces their research as the low-priority tail
  behind fresh ideas. *Tests:* backfill is idempotent, lands dormant, drains behind fresh work, and
  never fires N research jobs at once.

### 12.1 Rollout discipline — evidence before deployment (OD-6, RQ-3)

The system is **proven before it goes live** — the gate to deploy is evidence of correct end-to-end
behavior, not a hunch:

- a **fake-driven end-to-end harness** exercising the full lifecycle — capture → sweep → research →
  post → dirty → update → dormant → revive → promote — with the research + Mattermost sides faked
  (the orchestration-gym pattern);
- a **staging run** against a handful of *real* ideas in a test channel, showing real cited
  dossiers, a real dirty-update landing in the same thread, and a real brainstorm resuming with
  context;
- only then: flip it on, and run the throttled migration (IR.7).

This mirrors the workspace convention (each phase deployable, tested, reversible) and the "prove it
works in the deployed artifact" discipline — the loop earns deployment by demonstrating it, then
inherits the backlog.

---

## 13. Open design threads (still in design — not a build plan yet)

OD-1..OD-6 and RQ-1..RQ-3 are settled; **DT-1..DT-5 are now resolved too (2026-07-25)** — the design
draft is complete. Kept here for the reasoning trail:

- **DT-1 — Value framing → RESOLVED 2026-07-25 (dropped "value vs the stack").** The machine does
  not score value against the ai-stack; **the human determines worth.** Research instead *gathers*
  the industry landscape (products + value propositions) and related OB claims (direct + adjacent),
  and the dossier leaves the value call open (§3). This removes the "model the current stack"
  grounding dependency entirely.
- **DT-2 — RESOLVED 2026-07-25.** Research instruction + gap-centered dossier settled (§3.1): ~5
  products +N-more (a), a *descriptive* landscape-gap read (b), digest-in-thread + full synthesis in
  OB (c). Key principle: **gaps are the primary engagement mechanic** — "generating gaps is the
  relevant engagement" — and the plan **accretes through gap-working** (§7.1), finalized at PROMOTE.
  Raises the relevance of DT-4 (the in-thread grammar for surfacing and working gaps).
- **DT-3 — Capture-time dedup → RESOLVED 2026-07-25.** `capture_idea` runs `find_idea` first; on a
  near-duplicate (≈0.30 cosine) it **asks** "update your existing idea *X* instead?" — never
  auto-merges. RQ-1 applied at the *create* moment (§5.1).
- **DT-4 — Engagement grammar → RESOLVED 2026-07-25.** NL-first (no slash burden): labeled gaps
  (G1/G2) for easy reference, "make this real"/"promote" → finalize + pick target, "show my parked
  ideas" → `find_idea`, `dirty` surfaces as an in-thread ↻ update. **Decision capture = the document
  framework itself** (§7.1/§9): a fixed grounded template whose slots admit only accredited content
  (grounded claim or attributed human decision), no invented facts — *as deterministic as possible*
  via structure + grounding, with a **deliberately thin harness** (no clever capture-timing logic)
  so it survives model evolution.
- **DT-5 — Parameters & defaults → RESOLVED 2026-07-25.** Dormancy `T`=14d, resurface distance
  ≈0.40, capture-dedup ≈0.30, K≈1–2 in-flight (§6.2), one research job per idea per cycle, backfill
  as the low-priority tail; batch cron ~02:00–03:00 UTC before the 1am wiki compile (§6.1). Plus an
  **optional quiet-period dial** (skip research if edited within the last N hours → wait one cycle),
  **default off**. All tunable post-staging.

---

## Appendix — key files this design leans on

- Research trigger/engine: `OB1/integrations/research-service/index.ts` (`:469` drainLoop, `:588`
  enqueue, `:1635` `/research/persist`), `harness.ts`, `kb.ts`; client `agent-bridge/app/modules/
  grounding.py:161`.
- Open Brain store: `OB1/docker/init.sql` (thoughts), `init-claims.sql` (claims + status +
  freshness), `init-source-revisions.sql`, `init-sources.sql:59` (supersede-in-place); capture
  `index.ts:207/:834`.
- Mattermost: `scripts/claude-sessions-bridge/bridge.py` (thread↔session map, follows/wake),
  `approval_server.py:257` (`follow_thread`), `agent-org/agent-bridge/app/adapters/mattermost.py:95`
  (`post`/`update_post`/`ensure_channel`), `router.py:161` (open_effort pattern). Tokens in
  `agent-org/docker/.env`.
- Driver shape: `orchestrator.py:570` (`_capacity_drain_loop`), `capacity_park.py` (`ParkStore`).
- Scheduling: `OB1/docker/cron/crontab` (nightly cron, UTC/supercronic),
  `OB1/docker/docker-compose.scheduled.yml` (cron container + HTTP-chained services +
  `NEXT_TRIGGER_URL`), `OB1/docker/docker-compose.yml:502` (`openbrain-wiki` — 1am compile +
  ~3-min change-watch).
- Sibling design (downstream, different goal): `../teams-chat-agent-orchestration/
  PRODUCT-DISCOVERY-LOOP.md`.
