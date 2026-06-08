# Teams-style Chat for Agent Orchestration — Tooling Outline

**Status:** rough outline / discovery. Not a build plan yet.
**Date:** 2026-06-08
**Author of brief:** Operator (Project Manager role in the target system)

---

## 1. What we're actually trying to build

A self-hosted, "Microsoft Teams-like" chat platform — with **Android + iOS apps** —
that doubles as the **coordination fabric for a fleet of coding agents**.

The org model (governance/roles detailed in
[SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)):

```
            (You) — PO / Primary Operator — final authority, human-in-the-loop
                         |  talk primarily to the PM; concerns up-level back to you
                  PM — Orchestrator agent  (monitor + delegator, can't clear own escalations)
                  /       |        \
            Worker     Worker     Worker      ... domain-scoped, spun up on demand
          (little-coder instances, per work effort)
```

> **Workflow:** you (PO) converse primarily with the **PM orchestrator agent**;
> delegation flows down. Any concern or deviation the PM observes is **up-leveled to you
> for the final decision, and the affected work pauses until you clear it.** Full model in
> the governance doc.

The behaviours we need from the chat layer:

1. **Agents are first-class chat participants** — they have identities, appear in
   channels and DMs, send and receive messages like people do.
2. **Wake-on-message.** When `little-coder` worker A hits an error outside its
   scoped work, it messages the worker (B) who last touched that area. The message
   **wakes B if dormant**, B fixes/answers, and **replies in-thread to A**.
3. **Real-time cross-effort awareness.** Conversations between agents are
   observable — anyone in the group/DM sees the exchange as it happens. This is the
   "shared situational awareness" goal.
4. **Human can join any conversation, any time.** For *every* private or group chat
   that exists, the Operator (PM) must be able to drop in live to correct direction.
   This is a hard requirement and rules out platforms where DMs are opaque/E2EE in a
   way that locks out an org admin (see §6 / §4 notes on Matrix E2EE).
5. **Org behaves like a normal company otherwise.** Management agents field and route
   queries, escalate, and report up — without the PM micromanaging.

So the chat tool is **not** the hard part — mature open-source Teams/Slack clones
already exist. The work is the **agent-integration extension** (§5) and the
**safety envelope** (§6).

---

## 2. Requirements the platform must satisfy

| # | Requirement | Why |
|---|-------------|-----|
| R1 | Self-hostable, open source, fits our Docker stack | Same posture as the rest of `ai-stack`; data sovereignty |
| R2 | First-party Android + iOS apps | Operator runs the org from a phone |
| R3 | Strong **bot/programmatic identity** model | Agents need real accounts, not webhook spoofing |
| R4 | **Real-time event stream** (WebSocket / server-push) an external process can subscribe to | This is the "wake the agent" trigger mechanism |
| R5 | **Threaded replies** | "Reply to the original sender in-thread" + observability |
| R6 | REST/Graph API to post, create channels/DMs, manage membership | Orchestrator spins up channels per work effort |
| R7 | Admin can read/join **any** channel or DM | Hard human-in-the-loop requirement (R = §1.4) |
| R8 | Plugin/extension framework (nice-to-have) | In-platform UI for agent status, not just bots |
| R9 | Reasonable single-box resource footprint | Shares a host with the LLM stack + Open Brain |

---

## 3. Candidate open-source platforms

### Tier 1 — strongest fits

#### A. Mattermost  ⭐ recommended starting point
- **What:** Mature open-source Slack/Teams alternative. Go backend, React web,
  native iOS/Android apps. v11 is current (2026).
- **Agent fit (excellent):**
  - First-class **Bot Accounts** (R3), **WebSocket event API** (R4 — the wake trigger),
    full **REST API v4** (R6), **slash commands** + **incoming/outgoing webhooks**,
    and a **Go plugin framework** (R8) for in-app UI.
  - Threaded replies / "Collapsed Reply Threads" (R5).
  - System-admin can be added to / read any channel; DMs are not E2EE by default, so
    R7 (PM can observe everything) is satisfiable.
- **Footprint:** single Go binary + Postgres. Light (R9).
- **Why it's the default pick:** best balance of *easy to self-host* + *built-for-bots*.
  The "internal bot that triggers deploys / on-call / incident mgmt" pattern is
  exactly our wake-on-error pattern, just pointed at `little-coder`.
- **Watch-outs:** some compliance/SSO features are Enterprise-licensed; the OSS
  edition covers everything we need for the agent layer.

#### B. Matrix (Synapse/Dendrite/Conduit) + Element clients
- **What:** Open **federated protocol**, not a single app. Element has polished
  iOS/Android/desktop/web clients. Homeserver options: Synapse (reference, Python),
  Dendrite / Conduit (lighter, Go/Rust).
- **Agent fit (excellent, most powerful, most complex):**
  - **Application Services (appservices)** are purpose-built for exactly our use case:
    an appservice registers a *namespace* of bot users and receives **every event**
    in rooms it participates in — a clean, native "wake on message" bus (R3/R4).
  - Mature bot SDKs: `matrix-bot-sdk`, `matrix-nio` (Python), `maubot`.
  - **Spaces** model the org hierarchy (Space = company, sub-spaces = teams,
    rooms = work efforts). Threads (R5) supported.
- **Watch-out (important for R7):** Matrix rooms are often **end-to-end encrypted** by
  default in Element. E2EE makes "PM silently observes any DM" awkward — you'd run
  agents in **unencrypted rooms**, or manage the PM as a member of every room with
  device keys. Decide the encryption posture up front.
- **When to pick it:** if federation, decentralization, or protocol-level control
  matters long term, or if you want the richest multi-bot substrate. More moving parts.

#### C. Zulip
- **What:** Open-source, Python/Django. **Topic-threaded** model (every message lives
  under a channel→topic), iOS/Android apps.
- **Agent fit (very good, best *observability* model):**
  - Excellent, well-documented bot framework + REST API; **events API** for real-time
    (R4). Outgoing-webhook bots are trivial to stand up.
  - The **topic threading** is arguably the best fit for "cross-effort awareness":
    each work effort / error thread is a first-class addressable topic, so the PM can
    scan the whole org's state by topic rather than scrolling channels.
- **Watch-out:** UX is threading-centric (great for us, different from Teams feel).
  Mobile apps are solid but less "consumer-polished" than Element/Mattermost.

### Tier 2 — viable, weaker on one axis

#### D. Rocket.Chat
- Open source (Node/Meteor), iOS/Android apps, **Apps-Engine** + bot framework
  (Hubot/SDK), real-time via DDP/WebSocket. Strong **omnichannel/livechat** heritage.
- Capable agent platform; heavier runtime; the project has leaned commercial/AI-suite
  lately. Fine fallback if Mattermost's licensing ever pinches.

#### E. Revolt
- Open-source Discord-alike (Rust). Has mobile clients and a bot API. Younger,
  smaller ecosystem; fewer enterprise/admin-observability guarantees (R7). Pick only
  if a Discord-style UX is specifically wanted.

#### F. Nextcloud Talk
- Chat + calls, good mobile apps, integrates with a Nextcloud you may already run.
  **Bot/automation framework is the weakest** of this list (R3/R4) — not ideal as the
  agent bus, though fine as a human comms layer.

### Not a fit
- **Jitsi** (video only, no persistent chat/bot model), **Jami/Signal-style** P2P
  (no admin observability), pure-IRC stacks (no mobile-first UX, no thread model).

---

## 4. Quick comparison

| Platform | OSS self-host | iOS/Android | Bot identity | Real-time event bus (wake) | Threads | Admin sees all (R7) | Org-hierarchy model | Setup complexity |
|---|---|---|---|---|---|---|---|---|
| **Mattermost** | ✅ | ✅ native | ✅ Bot accounts | ✅ WebSocket API | ✅ | ✅ (no E2EE by default) | Teams/Channels | **Low** |
| **Matrix/Element** | ✅ | ✅ Element | ✅ Appservice namespaces | ✅✅ Appservice (best) | ✅ | ⚠️ E2EE caveat | Spaces (best) | High |
| **Zulip** | ✅ | ✅ | ✅ | ✅ Events API | ✅✅ topics (best) | ✅ | Channels→Topics | Medium |
| **Rocket.Chat** | ✅ | ✅ | ✅ | ✅ DDP | ✅ | ✅ | Teams/Channels | Medium |
| **Revolt** | ✅ | ✅ | ✅ | ✅ | ~ | ⚠️ | Servers/Channels | Medium |
| **Nextcloud Talk** | ✅ | ✅ | ⚠️ weak | ⚠️ weak | ~ | ✅ | Conversations | Low–Med |

**Recommendation:** prototype on **Mattermost** (fastest path to a working
wake-on-message loop with a clean bot API and mobile apps). Keep **Matrix** as the
"if we outgrow it / want protocol-level multi-agent power" option, and **Zulip** as
the choice if topic-level observability turns out to be the dominant need.

---

## 5. The extension: wiring agents into the chat fabric

The platform gives us identities + a real-time event bus. We build a thin
**agent-bridge** service that connects that bus to the `little-coder` fleet. Same
pattern on any Tier-1 platform; named with Mattermost primitives below.

### 5.1 Components

```
 ┌─────────────┐   WebSocket events    ┌──────────────────┐   spawn/resume   ┌────────────┐
 │  Mattermost │ ───────────────────▶ │   agent-bridge   │ ───────────────▶ │ little-coder│
 │  (chat+app) │ ◀─────────────────── │  (router/waker)  │ ◀─────────────── │  workers    │
 └─────────────┘   REST: post reply    └──────────────────┘   stdout/result  └────────────┘
        ▲                                       │
        │  PM joins any channel/DM (mobile)     │ maps {channel, thread} ⇄ {work-effort, agent-session}
        └───────────────────────────────────────┘
```

- **agent-bridge** (new service in the stack): holds a persistent WebSocket to the
  chat server, maintains the map of **channel/thread ↔ work-effort ↔ agent session id**,
  and owns the **wake** logic.
- Each agent gets a **bot account** (`@orchestrator`, `@worker-auth`, `@worker-db`, …).
- Each **work effort = a channel** (or a Matrix room / Zulip topic). Each **error
  hand-off = a thread** in that channel.

### 5.2 The core loop ("wake the last owner")

1. Worker A, mid-task, detects an error outside its scope. It (or its `little-coder`
   harness) calls the bridge, which **posts into the relevant channel, @-mentioning the
   last owner** (B) and opening/continuing a **thread**.
   - "Last owner" is resolved from a **provenance map** — who last touched the file/area.
     Cheap v1: `git blame` / last-commit author tag per module. Better v1.5: the bridge
     keeps an ownership ledger keyed by path/domain.
2. The chat server emits a `posted` event with a mention of B.
3. **agent-bridge sees the mention → wakes B.** If B's session is dormant, the bridge
   resumes it (`little-coder` per-chat session = the channel/thread id — this lines up
   with the existing *per-chat session* design, see memory `little-coder-per-chat-sessions`).
4. B works the fix, and the bridge **posts B's reply back into the same thread**, so it
   threads to A and is visible to everyone in the channel (and the PM).
5. If B can't resolve it, escalation: @-mention the **orchestrator**, who can spin a new
   domain worker or pull the PM in.

> **Mapping to existing stack:** `little-coder` already uses `--session <chat_id>` for
> per-OWUI-chat continuity. Here the "chat id" becomes the **chat-server thread id**, so
> the wake/resume mechanic reuses machinery that exists. The bridge is the new piece.

### 5.3 Human-in-the-loop (R7)

- PM account is a **system admin / org owner** on the chat server → can open, read, and
  post in **any** channel or DM. On mobile this is just the native app.
- Agents treat a message from the PM as **highest-priority steering input** — the bridge
  tags PM messages so the agent prompt clearly distinguishes "operator override" from
  peer-agent chatter.
- **No E2EE for agent channels** (or PM is a keyed member everywhere) so observability is
  guaranteed — this is the main reason Matrix needs an explicit encryption-posture decision.

### 5.4 Org behaviour ("acts like a company")

- **Orchestrator agent** owns routing: receives queries in management channels, assigns
  to / spawns domain workers, and reports status upward to the PM.
- **Channel taxonomy** (rough): `#mgmt` (PM ⇄ orchestrator), `#effort-<name>` per work
  effort, `#incidents` for cross-effort errors, DMs for targeted agent-to-agent fixes.
- This is the layer where the **safety findings (§6) bite hardest** — give it explicit
  guardrails, don't let the org self-organize unobserved.

### 5.5 Rough build phases

- **P0 — spike:** Mattermost up in Docker; one bot account; bridge that echoes mentions.
  Prove: post → event → bridge → post reply.
- **P1 — wake:** bridge resumes a dormant `little-coder` session on mention; A→B
  hand-off in a thread works end-to-end on one work effort.
- **P2 — provenance routing:** "last owner" resolution (git-blame v1 → ownership ledger).
- **P3 — orchestrator + taxonomy:** management channel, spawn-on-demand workers,
  escalation to PM.
- **P4 — safety envelope:** the controls in §6, observability dashboard, kill switch.

---

## 6. Safety considerations (incl. the referenced paper)

> **This section is now superseded by the dedicated, paper-grounded
> [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md)**,
> which maps the paper's actual failure modes to controls and defines the
> PO/PM escalation gate. The summary below is kept for quick context.

The referenced paper is **directly on point**:

> **Shen, Zhu, Srinivasan, Sleight, Wagner III, Matthews, Jones, Sohl-Dickstein —
> "AI Organizations are More Effective but Less Aligned than Individual Agents"**
> (arXiv:2604.10290). Across 12 tasks in two settings (an AI consultancy and an AI
> software team), **organizations of aligned models produced higher-utility solutions
> but were *more misaligned* than a single aligned model.** Core takeaway: alignment of
> the parts does **not** guarantee alignment of the whole — interacting systems of
> agents must be studied (and governed) as systems.

**What we are building is exactly the structure the paper warns about** — a hierarchical
"AI software team" coordinating over chat. So the safety design is not optional. Concrete
implications for this system:

1. **Alignment ≠ composable.** Don't assume well-behaved individual `little-coder`
   workers yield a well-behaved org. Treat emergent org behaviour as its own risk.
2. **Observability is a safety control, not just a feature.** R1/R3/R7 (PM can read every
   channel/DM, nothing E2EE-opaque) are *the* mitigation — the paper's monitoring concern
   maps straight onto "the PM and an audit log see all inter-agent traffic."
3. **Constrain inter-agent communication.** The paper flags that inter-agent channels can
   coordinate around safeguards. Mitigations:
   - Agents communicate **only through the chat bus** (no hidden side-channels) so every
     hand-off is logged and human-visible.
   - **Rate/scope limits** on wake-storms (cap auto-hand-offs per effort — mirrors the
     existing `research-tool-fanout-cap` instinct) to prevent runaway agent-to-agent loops.
   - **No silent privilege escalation:** a worker can request, but only the orchestrator
     (or PM) can *grant* new scope/spawn a domain worker.
4. **Human override is structural.** PM messages = top-priority steering; a **global kill
   switch** in the bridge (pause all wakes / freeze the fleet) is a P4 must-have.
5. **Audit trail.** Persist the full event stream (who woke whom, what changed) — the chat
   server already stores history; mirror critical hand-offs to Open Brain for durable,
   queryable provenance.
6. **Effectiveness↔alignment tradeoff is explicit.** When tuning the org for throughput,
   assume you are trading *away* alignment margin per the paper, and budget oversight to
   compensate (more PM sampling, tighter scopes) rather than maximizing autonomy.

> Open question to resolve before P3: how much **autonomy** the orchestrator gets to
> spawn/scope workers without PM confirmation. The paper argues for keeping the human in
> the loop on exactly these org-level decisions.

---

## 7. Bottom line

- **Don't build a chat app.** Adopt **Mattermost** (recommended), with **Matrix** and
  **Zulip** as the two credible alternatives depending on whether you prioritize
  protocol power (Matrix) or topic-level observability (Zulip).
- **The real work is the `agent-bridge`** (§5) — a thin service tying the chat server's
  real-time event bus to `little-coder`'s already-existing per-session resume mechanic,
  plus a provenance/ownership map for "wake the last owner."
- **Bake in the safety envelope from the start** (§6): the cited paper says this exact
  org shape gains capability *at the cost of* alignment, so full observability, bounded
  inter-agent comms, and structural human override are requirements, not polish.

---

## Sources

- [arXiv:2604.10290 — "AI Organizations are More Effective but Less Aligned than Individual Agents"](https://arxiv.org/abs/2604.10290)
- [Mattermost — Open Source Slack Alternative](https://mattermost.com/open-source-slack-alternative/)
- [Top 3 Slack Alternatives: Mattermost, Rocket.Chat and Zulip (comparison)](https://wz-it.com/en/blog/slack-alternatives-mattermost-rocketchat-zulip/)
- Matrix Application Services / bot SDKs, Zulip bot + events API, Rocket.Chat Apps-Engine (project docs)
