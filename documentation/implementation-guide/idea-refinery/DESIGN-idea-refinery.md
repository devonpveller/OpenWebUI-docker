# Idea Refinery — an upstream idea-honing loop

**Status:** 📐 **design draft (2026-07-24)** — first pass, written to be worked on collaboratively.
Several load-bearing decisions are deliberately left **OPEN** (§11) rather than guessed. "Idea
Refinery" is a working name; rename freely.

**One-line intent:** the user has a raw idea, describes it to a model, and walks away. In the
background the stack *researches the idea on the user's behalf* — what value it could add to the
ai-stack, what we already know about it (prior Open Brain claims), and how the industry solves it
today — then re-engages the user in Mattermost while the idea is fresh, as a brainstorm with real
depth behind it. Ideas that don't catch **fizzle naturally** (no nagging); ideas that do get
**honed** — across one or many sessions — into a **credible, targetable, tangible Project Design
Brief**. That brief is the deliverable. What happens to it next — hand it to the dark factory
(agent-org) to build, take it elsewhere, or shelve it — is **the user's choice, never automatic.**

---

## 0. What this is, and the boundary it must respect

This is **not** the agent-orchestration project and must not be folded into it. The dark factory
*implements* things; the Idea Refinery decides *whether an idea is even worth implementing, and
sharpens it until it is.* It sits **upstream of everything that builds** — earlier even than the
PDL's Movement 1 (charter). The relationship is a one-way, user-gated seam:

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
research session run per version. This is the *same* guarantee the recent agent-org convergence
work hardened — P27 ("never close *done* on an incomplete sweep"), P25 ("the drain loop survives a
transient unverifiable delivery"), P28 (deterministic filters, not an LLM verdict). The refinery's
driver should inherit those lessons, not re-learn them. The **LLM** does only the semantic content
(the research, the synthesis, the brainstorm); the **queue** does the counting and the guarantees.

---

## 3. What "research" means here — the evaluative frame

The stated use is **AI-related tooling and improvements to the ai-stack**, so each research effort
has a fixed evaluative frame (not a generic "tell me about X"). Every idea's research answers three
questions:

1. **Value to the ai-stack** — what capability/quality/efficiency gain would this actually add,
   and to which plane (inference / memory / search / coder / OB / portal …)? What does it cost or
   risk? *This is the "potential value gains" judgment the user named.*
2. **What we already know** — query Open Brain **claims** from prior research sessions first
   (cheap reuse; `search_claims`, freshness-gated). Has this been explored before? Is there a prior
   dossier to continue?
3. **How the industry does it** — external/industry web research for the gaps only, via the
   existing private search path (SearXNG `gateway:8080` → Tor page-fetch), injection-screened,
   cited. Concrete implementation strategies, not vibes.

The output is a **grounded, cited Idea Dossier** (synthesis + `[SOURCED]/[INFERRED]/[UNCERTAIN]/
[GAP]` tags + Sources block) — reusing the research engine's grounding discipline verbatim. An
idea whose value can't be grounded gets an honest `[GAP]`, not an invented case for building it.

> **Scope of the domain (v1):** ideas are about improving *this stack*. The machinery is
> idea-domain-agnostic, but the evaluative frame above (value-to-ai-stack) is v1's north star. If
> we later want general personal ideas, the frame becomes a per-idea/per-channel setting (§11).

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
  domain          TEXT                 # v1: 'ai-stack'  (the evaluative frame, §3)
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
sets `ideas.status='dirty'`; the driver then owes *exactly one* research run for that new revision
(the exactly-once guarantee is keyed on `(idea_id, revision)`). Prior dossiers are never destroyed —
they are the continuation context (§7).

---

## 6. The driver — a deterministic sweep

A single durable loop (mirror `_capacity_drain_loop` / the research `drainLoop`), rehydrated on
boot, one pass:

- **Detect + log (deterministic).** Every idea-flagged thought is reconciled into an `ideas` row
  exactly once (keyed on `thought_id`), and every edit is reconciled into an `idea_revisions` row.
  This is the "curated list" — it is a table, and the guarantee is *nothing flagged is ever
  silently skipped* (the P27 discipline).
- **Research the owed work.** For each idea with `status ∈ {new, dirty}` that has no research run
  for its current revision, enqueue **one** research job (frame from §3, seeded with prior dossier
  if `dirty`). One-at-a-time, off-peak, budget-bounded — the loop **must never self-DoS** the
  shared GPU (reuse the research service's queue governance + a park-on-backpressure guard).
- **Deliver (idempotent, survives a flaky post).** On job completion: render the dossier, create
  the idea's Mattermost thread (or append an UPDATE if it exists), persist `thread_root` +
  `last_job_id`, set `status='researched'`. A failed Mattermost post must **not** lose the research
  or double-post — retry against the persisted `last_job_id` (the P25 "survive a transient
  unverifiable delivery" lesson).
- **Age.** Ideas with no human turn for `T` days → `dormant` (no message sent — see §8).

Everything the loop *counts on* — exactly-once, never-skip, survive-transient-failure, no silent
caps — is a solved shape in the agent-org drain work. The refinery's driver is that shape pointed
at ideas.

---

## 7. Engagement — one thread per idea, honed over sessions

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
thread ("make this real" / "promote") tells the refinery to synthesize the accumulated dossier +
brainstorm into a **Project Design Brief** (§9) and hand it off where the user directs. The refinery
never promotes on its own.

---

## 8. Fizzle, revive, resurface — the emotional design (the biggest open area)

This is the part with the least prior art and the most product judgment, so it's called out as its
own section and revisited in §11 (OD-4). The proposed defaults:

- **Fizzle = silence, not a stop.** After research posts once (the "while it's fresh" moment),
  there is **no follow-up ping**. If the user doesn't engage within `T` days, the idea goes
  `dormant` quietly. Dormant is a resting state, not a deletion — the idea and its dossier are kept.
- **One post, no nagging.** The single research post *is* the re-engagement. We do **not** chase.
  (An optional single scheduled nudge is a dial in OD-4, default off.)
- **Revive triggers (all user-respecting):**
  1. the user replies in the thread (direct);
  2. the user edits the idea in Open Brain (→ `dirty` → re-research → UPDATE in the same thread);
  3. **resurfacing** — a *new* idea lands semantically near a dormant one (embedding distance under
     a threshold, reusing the research engine's `REUSE_MAX_DISTANCE ≈ 0.55` intuition). The new
     idea's dossier **cross-links** back ("this connects to your earlier idea *X*, which you
     parked") — surfaced *in the new idea's thread*, never a cold ping on the old one.
- **User-driven rummaging.** "Show me my parked ideas" / "what did I have about caching?" is a
  read over the `ideas` table + embedding search — the "explore old ideas with fresh eyes"
  affordance, on the user's clock.

The whole posture: **encourage while fresh, then get out of the way.** The system's job is to make
the *first* re-engagement worth it, not to hound.

---

## 9. The hand-off seam — the Project Design Brief

The deliverable. On PROMOTE, synthesize into a structured, targetable artifact:

```
Project Design Brief
  idea_id / title
  problem            # the need, in the user's terms
  value_to_stack     # the grounded value case (CITED from the dossier) — why it's worth building
  prior_art          # what OB already knew + what the industry does (cited)
  proposed_approach   # the honed, targetable design direction (from the brainstorm)
  scope / out_of_scope
  open_questions      # what's still uncertain — honest, not hidden
  suggested_target    # dark factory (agent-org) | PDL charter | manual | shelf — USER picks
  provenance          # links: OB thought(s), research job(s), the Mattermost thread
```

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
- **Gentle on the shared GPU.** One research job at a time, budget-capped, off-peak, backpressure-
  parked. An autonomous loop that floods the inference plane is a regression, not a feature (prior
  pain: LiteLLM health-probe thrash, OOM). This is a hard requirement from day one.
- **No nagging.** Re-engagement is one post while fresh; dormancy is silent (§8). The system must
  be easy to ignore.
- **Deterministic sweep, honest gaps.** Nothing flagged is silently skipped (P27); ungrounded
  value claims are `[GAP]`, never invented (research grounding discipline).
- **Observable.** Every research query, dossier, and update lands in the idea's thread; the
  `ideas`/`idea_revisions` tables are the audit trail.
- **Read-mostly on Open Brain.** The refinery reads thoughts/claims and writes only its own
  lifecycle tables + (via the research service) cited claims. It doesn't mutate user thoughts.

---

## 11. Open decisions (the gaps — let's work these together)

Marked OPEN because they're genuinely yours to shape; each has a lean + the tension.

- **OD-1 — The inlet contract (how an idea is *deterministically* flagged).** Relying on the LLM's
  soft `metadata.type=="idea"` is *not* deterministic — it'll miss some and false-positive others.
  *Lean:* the storing model in OWUI tags at capture time (`capture_thought` with
  `metadata_extra:{kind:"idea", idea_id}`), **plus** a reconciler sweep as a backstop. *Tension:*
  this asks the OWUI-side capture flow to be disciplined — do we control it enough, or do we need a
  "promote this thought to an idea" affordance the user triggers?

- **OD-2 — Where the driver lives.** *Lean:* a **dedicated `openbrain-idea-refinery` service**
  (sibling to `openbrain-research`/`-curator` under `OB1/integrations/`), state in OB Postgres,
  reusing the research service + curator + MM bot token — this keeps the agent-org boundary clean
  (your explicit ask). *Alternatives:* fold into the **automations/n8n** project (more
  user-editable, but n8n is design-not-built), or a thin service that just orchestrates. *Tension:*
  a new service is more infra to run vs. maximal boundary hygiene.

- **OD-3 — Idea identity & the `dirty` diff.** *Lean:* an explicit stable `idea_id` with
  append-only `idea_revisions` lineage (the model references the prior idea when the user updates
  it). *Tension:* if the OWUI capture flow *can't* reliably carry an `idea_id` forward, we fall back
  to fuzzy embedding-clustering to decide "same idea, modified" — which is exactly the fragile path
  we want to avoid. This decision is coupled to OD-1.

- **OD-4 — Fizzle/revive aggressiveness (the feel).** *Lean:* one research post, no nag, silent
  dormancy, resurface-on-related-idea (§8). *Tension:* how hard should "encourage while fresh"
  push? Options on a dial: (a) pure single post [default], (b) one optional scheduled nudge before
  dormancy, (c) a periodic "here are 3 parked ideas worth another look" digest the user opts into.
  This defines the whole personality of the system — I'd like your call.

- **OD-5 — Engagement seeding.** *Lean:* lazy-seed the brainstorm session on first reply (§7).
  *Tension:* lazy-seed needs the small claude-sessions-bridge enhancement to inject dossier context;
  pre-warm avoids the bridge change but spends a session per idea. Also: `#ideas` as its own channel
  vs. reusing `#claude-sessions`.

- **OD-6 — Domain scope.** *Lean:* v1 is ai-stack-improvement ideas with the value-to-stack frame
  (§3). *Tension:* do you want general personal ideas in scope now (frame becomes per-idea config),
  or keep v1 tight to the stack?

---

## 12. Phased build (each deployable, tested, reversible)

- **IR.0 — Idea aggregate + deterministic inlet.** `ideas` + `idea_revisions` tables; the inlet
  contract (OD-1); the reconciler sweep (nothing flagged is skipped). *Tests:* capture→logged
  exactly once, edit→new revision, restart-survives, no double-log.
- **IR.1 — Research the queue.** Driver enqueues one research job per owed revision (frame §3),
  budget-capped + backpressure-parked; persist the dossier. *Tests:* exactly-once per revision,
  reuse-prior-claims path, backpressure park, no self-DoS.
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
- Sibling design (downstream, different goal): `../teams-chat-agent-orchestration/
  PRODUCT-DISCOVERY-LOOP.md`.
