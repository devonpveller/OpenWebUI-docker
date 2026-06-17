# Concept Design — ai-stack User-Created Automations

> **Status:** CONCEPT / DESIGN — not built. This is an idea-stage design document
> with a feasibility verdict. Nothing here is deployed.
> **Date:** 2026-06-13
> **Inspiration:** Open WebUI's new *Automations* feature — but instead of automating
> OWUI's own primitives, this automates the **services that already exist in the
> ai-stack** (research, podcast, Open Brain, search, memory, little-coder, …).

---

## 1. The idea in one paragraph

Build a **node-based automation builder** — a web front-end, reachable on the
**tailnet** and via **cloudflared** — where a user wires together *nodes* into an
*automation*. Each node wraps an existing ai-stack capability. The flagship node
is **Research**: it takes a prompt, runs its internal harness until an output is
available, and emits a result that can be *formatted* three ways — chat (OWUI),
chat (Open Notebook), or **podcast**. Nodes chain: one node's output triggers the
next. Every automation declares a minimum of an **input** (where it starts) and an
**output** (where it goes).

The key insight that makes this feasible: **the ai-stack is already a mesh of
HTTP-triggerable services that chain via `POST /run` + `NEXT_TRIGGER_URL`.** The
daily-digest → podcast pipeline is *already* a hard-coded automation of exactly
this shape. This project generalizes that hidden pipeline into a **user-editable
graph** with a UI.

---

## 2. Why this fits the ai-stack (and isn't greenfield)

The stack already contains every architectural piece this needs:

| Need | Already exists in the stack |
|------|------------------------------|
| Long-running job that "runs until output is ready" | `openbrain-research`: `POST /research` → `job_id`, poll `GET /research/jobs/:id`, or SSE `/stream` |
| Services that **trigger the next service** in sequence | The digest chain: `openbrain-gmail-pull` → `gmail-prune` → `openbrain-podcast` → `openbrain-digest`, each `POST /run` to a `NEXT_TRIGGER_URL` |
| Output formatting (research → chat / notebook / podcast) | OWUI thin client renders `result.synthesis`; podcast pipeline turns grounded claims into a two-host script + audio via Open Notebook `/api/podcasts/generate` |
| Scheduled triggers | `openbrain-cron` reads a crontab and fires `POST /run` at services |
| A dual-exposed (tailnet + cloudflared) web UI with auth | The **Portal** (caddy + authelia + cloudflared) and **tailscale serve** already expose OWUI, Open Notebook, and the wiki on both planes |
| A precedent for a **new compose project** that attaches to existing networks | OB1 (`open-brain`) and the designed `agent-org` (teams-chat) both attach to `ai-stack_llm-net` as an external network |

So the automation builder is **mostly orchestration glue + a UI** over surfaces
that are already live. It is closer to "expose and generalize a pattern that
already runs daily" than to "build a new subsystem from scratch."

> **Related prior art in this repo:** the daily-digest podcast pipeline
> (`documentation/expanding-daily-digest-with-auto-podcast/`,
> `OB1/recipes/daily-digest/`) is the *de-facto* first automation. The
> teams-chat-agent-orchestration design
> (`documentation/implementation-guide/teams-chat-agent-orchestration/`) is the
> blueprint for adding a new governed compose project. This concept borrows from
> both.

---

## 3. Core concepts & vocabulary

### 3.1 Node
A node is a typed unit of work wrapping one ai-stack capability. A node has:

- **One or more typed input ports** (e.g. `prompt: text`, `sources: url[]`).
- **One or more typed output ports** (e.g. `synthesis: research_result`).
- **A configuration panel** (the per-node settings — model, depth, format, …).
- **An execution contract**: *sync* (returns inline) or *async* (`POST` → poll
  `job_id` / `GET /health`).

The flagship node — **Research** — has:
- Input: `prompt` (text), optional `seed_sources` (url[]), optional `thread_id`.
- Internal: runs the existing harness until terminal (`status == done`).
- Output: a `research_result` object = `{ synthesis, cited_sources, gaps,
  thread_id, metrics }` — the **raw** result, *before* formatting.

### 3.2 Output node — store vs. surface (corrected model)

The user is right that "each output is really just a formatting." But there's a
distinction the first draft blurred (corrected 2026-06-17):

- **Canonical store = Open Brain.** Open Brain is the knowledge store. The Research
  node **already writes its result there** (via the curator: thread placement +
  grounded claims). **Open Notebook is *not* a store — it's a viewer over Open
  Brain.** So you never "write to Open Notebook"; you write to Open Brain and ON
  *displays* it.
- **Outputs are therefore mostly *surfacings*** of a result that (when it's
  knowledge) already lives in Open Brain — not separate stores:

| Output | Nature | Note |
|--------|--------|------|
| **Open Brain** | canonical store | default for knowledge; **automatic** for research (curator) |
| **Open Notebook** | a *view* of Open Brain | free once it's in Open Brain — no write |
| **Podcast** | an audio *rendering* | reuses ON's podcast renderer/TTS |
| **OWUI chat** | a *surfacing/notification* into OWUI | unverified (PLAN §5.2) |
| **Teams-chat (Mattermost)** | a *surfacing* into the orchestration chat | **leading future candidate**, governance-gated (PLAN §5.5) |

Two consequences: (1) **any** node producing a compatible payload can fan out to
**any** surfacing destination — surfacing is a node category, not a property of
Research; (2) **output destinations are an open design area** — Open Brain is the
sensible default *where the result is knowledge*, but not every automation fits
that, so the surfacing palette (OWUI / podcast / teams-chat / …) is explored as the
stack grows.

**Most outputs terminate *outside* the ai-stack (operator, 2026-06-17).** n8n's
gravity is its **native integration catalog** — Slack, Discord, Etsy, Sheets,
email, hundreds more. The common case is an automation whose result reaches its
**natural external endpoint and ends there** (a Slack message, a Discord post, an
Etsy listing). Feeding a result **back into the ai-stack** — Open Brain or
Mattermost — is a **deliberate, opt-in connection the user wires**, not the default.
So the ai-stack custom nodes are best understood as the **bridge between the
private stack and the outside automation world**: *Research* as an input/source,
*Open Brain* / *Mattermost* as deliberate ai-stack output destinations, living
**alongside** n8n's external outputs rather than replacing them. Exactly how the
Open-Brain and Mattermost outputs should behave is **deferred until n8n is
hands-on** (PLAN §10b) — the native catalog + data-passing model has to be felt
first.

### 3.3 Automation (the graph)
An **automation** is a directed graph of nodes with:
- **An input/trigger** (required): manual "Run" button, a schedule (cron), a
  webhook, or an upstream event.
- **An output** (required): the canonical store (Open Brain — often written by the
  producing node itself) and/or a *surfacing* destination (Open Notebook view,
  podcast, OWUI chat, teams-chat, email, wiki). See §3.2 — store vs. surface.
- **Edges**: an edge from node A's output port to node B's input port means
  "when A completes, pass its output and trigger B." This is the UI-level
  generalization of the existing `NEXT_TRIGGER_URL` chain.

### 3.4 Run
A **run** is one execution of an automation. It has a status, a per-node progress
trace, and a persisted result. Long-running nodes (research, podcast) make a run
itself long-running — so runs are **async + pollable**, exactly like research jobs
today.

---

## 4. Architecture

### 4.1 Components (proposed new services)

```
┌──────────────────────────────────────────────────────────────────┐
│  automations-ui        (web front-end: the node/graph editor)      │
│   - React/Svelte + a graph lib (React Flow / Svelte Flow / Rete)   │
│   - talks ONLY to automations-engine over REST/WS                  │
└───────────────┬──────────────────────────────────────────────────┘
                │  REST + WebSocket (live run progress)
┌───────────────▼──────────────────────────────────────────────────┐
│  automations-engine    (backend: graph store + executor)           │
│   - Node registry (what node types exist + their schemas)          │
│   - Automation store (graphs)         → Postgres (reuse a DB)      │
│   - Run store + executor (topo-order, async job poll, retries)     │
│   - Adapters: research / podcast / OB / search / mnemory / LLM …   │
└───────────────┬──────────────────────────────────────────────────┘
                │  HTTP / MCP (existing surfaces, unchanged)
   ┌────────────┼─────────────┬──────────────┬───────────────┐
   ▼            ▼             ▼              ▼               ▼
openbrain-   openbrain-   openbrain-    search-        llm-gateway
research     podcast      mcp/curator   gateway        (LiteLLM)
(/research)  (/run)       (MCP tools)   (/search)      (/v1/...)
```

Two new containers: **`automations-ui`** (front-end) and **`automations-engine`**
(orchestrator). Everything else is an *existing* surface the engine calls. This
mirrors the teams-chat `agent-bridge` + UI split and the OB1 worker pattern.

### 4.2 The executor (the heart)

The engine's executor reuses three patterns already proven in the stack:

1. **Topological execution** — walk the graph in dependency order; a node fires
   when all its inputs are ready (the UI-level form of the digest chain).
2. **Async job + poll** — for research/podcast nodes, `POST` to start, then poll
   `job_id`/`/health` until terminal, streaming progress to the UI over WebSocket
   (the research SSE pattern, lifted to the run level).
3. **Degrade honestly, never fabricate** — inherit the grounding model's rule:
   if a node hits a gap (search down, budget exhausted), surface it as a `[GAP]`
   in the run, don't invent output.

### 4.3 Node registry — how the engine knows what each node accepts

Each adapter declares a small **node descriptor** (id, label, input ports,
output ports, config schema, exec contract, the endpoint/tool it calls). The
descriptors are the source of truth the UI reads to render the palette. Three
descriptor flavors map onto the three trigger surfaces found in the stack:

- **REST node** — method + path + body template (e.g. research, podcast, search).
- **MCP-tool node** — tool name + args (e.g. `capture_thought`, `search`,
  `ingest_url` via `openbrain-mcp`; `remember`/`retrieve` via `mnemory`).
- **LLM node** — model + prompt, hitting `llm-gateway` `/v1/chat/completions`.

> Future nicety: auto-generate REST descriptors from `/openapi.json` (mcpo
> bridges already publish these) and MCP descriptors from `tools/list`. Not
> required for v1 — start with a hand-written registry of ~6 nodes.

---

## 5. Node catalogue (grounded in what's live today)

Maturity legend: **READY** = HTTP/MCP trigger exists; **ADAPTER** = exists but
needs a thin wrapper; **BUILD** = needs new work.

| Node | Backing service | Trigger | Contract | Maturity |
|------|-----------------|---------|----------|----------|
| **Research** | `openbrain-research` | `POST /research` → poll `GET /research/jobs/:id` (+ SSE); **auto-persists to Open Brain via curator** | async | **READY** |
| **Open Brain (canonical output)** | `openbrain-curator` / `openbrain-mcp` | written *by* the research harness; ON *displays* it (no ON write) | — | **automatic** for research |
| **Surface → Podcast** | ON `/api/podcasts/generate` (bypass `openbrain-podcast`) | `POST /generate` → poll `/jobs/:id`; fetch audio | async | **ADAPTER** (decouple from digest chain) |
| **Surface → OWUI chat** | `openwebui` REST API | `POST /api/v1/chats/...` (create chat) | sync | **ADAPTER — unverified** (PLAN §5.2) |
| **Surface → Teams-chat** | Mattermost via `agent-bridge` (teams-chat project) | governance-gated post | async | **BUILD** (future, PLAN §5.5) |
| **Web search** | `search-gateway` (SearXNG/Tor) | `GET /search?q=` | sync | **READY** |
| **Open Brain capture** | `openbrain-mcp` | MCP `capture_thought` | sync | **READY** |
| **Open Brain search** | `openbrain-mcp` | MCP `search` / `search_thoughts` / `search_claims` | sync | **READY** |
| **Ingest URL(s)** | `openbrain-mcp` | MCP `ingest_url` / `ingest_urls` | sync/async | **READY** |
| **Curate research → thread** | `openbrain-curator` | `POST /ingest/research-package` | sync | **READY** |
| **Memory write/read** | `mnemory` | MCP `remember` / `retrieve` | sync | **READY** |
| **LLM completion** | `llm-gateway` (LiteLLM) | `POST /v1/chat/completions` | sync | **READY** |
| **Email / digest** | `openbrain-digest` | `POST /run` | async | **READY** (sink) |
| **Wiki recompile** | `openbrain-wiki` | `POST /recompile` | async | **READY** (sink) |
| **Extract document** | `openbrain-extract` | `POST /extract` (PDF/DOCX/OCR/STT) | sync | **READY** |
| **Coding task** | `little-coder` via `lc-mcpo` | MCP task trigger | async | **ADAPTER** (governance-sensitive) |
| **Schedule (trigger)** | `openbrain-cron` pattern | crontab → `POST /run` | trigger | **ADAPTER** |
| **Webhook (trigger)** | new engine endpoint | inbound HTTP | trigger | **BUILD** |

**Read of the table:** the *capabilities* are overwhelmingly **READY** — the
work is the engine + UI + a handful of thin format/trigger adapters, **not** new
backend services.

---

## 6. Worked example — the user's flagship automation

```
   [Trigger: Manual "Run"]   prompt = "State of small-model agents, June 2026"
            │
            ▼
   ┌──────────────────┐
   │  Research node   │  POST /research → poll until done
   │  (async, polled) │  ──curator──▶  ★ Open Brain (canonical store)
   └────────┬─────────┘                  └─ visible in Open Notebook (a view; no write)
            │  research_result { synthesis, prose, cited_sources, gaps, thread_id }
            │  (optional SURFACING fan-out — additive, not stores)
            ├───────────────────┬───────────────────┐
            ▼                   ▼                   ▼
   ┌──────────────┐    ┌───────────────────┐  ┌──────────────────────┐
   │ Surface→OWUI │    │ Surface→Podcast   │  │ Surface→Teams-chat   │
   │ (unverified) │    │ (audio episode)   │  │ (future, gated)      │
   └──────┬───────┘    └─────────┬─────────┘  └──────────┬───────────┘
          ▼                      ▼                       ▼
   OWUI chat (spike)      MP3 + transcript        Mattermost post (P-future)
```

The canonical output (**Open Brain**, shown in Open Notebook) happens **inside the
Research node** — no fan-out needed for it. The fan-out is over optional
**surfacing** destinations. This proves the user's "output is just formatting"
intuition *and* the corrected store-vs-surface model (§3.2): one research run, one
canonical home, many optional renderings.

---

## 7. Front-end exposure (tailnet + cloudflared)

The stack has a battle-tested dual-exposure recipe; the automations UI reuses it
verbatim. No new exposure mechanism is invented.

**Public (cloudflared) lane:**
- Add a Caddy subdomain block: `http://automations.{$PUBLIC_DOMAIN}` →
  `forward_auth authelia:9091` → `reverse_proxy automations-ui:3000`.
- `automations-ui` joins `app-net` (Caddy reaches it by name); `cloudflared` is
  the only ingress, gated by Authelia. This is the Portal pattern, already used
  for OWUI/ON/wiki.

**Tailnet lane:**
- Add `tailscale serve --https=<PORT>` for the UI (with a `socat` proxy in
  `entrypoint.sh` since the UI is a separate container), plus the monitoring-loop
  deferred-setup entry — exactly as open_notebook (:8443) and the wiki (:8444)
  are wired today.

**Auth note:** on the tailnet the UI is reachable to any tailnet device (no
Authelia); via cloudflared it's Authelia-gated. Because automations can trigger
real actions (send email, write to Open Brain, run a coding task), **the engine
must enforce its own per-action authorization** rather than trusting "you reached
the UI" — see §9.

---

## 8. Data & state

- **Automations (graphs)** and **runs** persist to Postgres. Cleanest option:
  a new small DB container (`automations-db`) following the `llm-gateway-db` /
  `mattermost-db` precedent, **or** a dedicated schema in an existing OB1 DB.
  A new DB keeps blast-radius small and matches the stack's "one DB per project"
  habit; recommended for v1.
- **Run artifacts** (a synthesis, an episode id) are references, not copies —
  the actual content lives where it already lives (OB1 threads, ON, the podcast
  store). The engine stores pointers + metrics.
- **Backups:** a new stateful container means a new `*-backup` cron sidecar (the
  stack's convention) — and the **three-place change rule** (compose +
  emergency-recovery + stack-map).

---

## 9. Risks, constraints & open questions

1. **Inference isolation must hold.** Any LLM-completion node must route through
   `llama-cpp` / `llm-gateway` (LiteLLM), **never** `*-upstream`. The existing
   `scripts/check-llm-gateway-routing.ps1` guard applies to engine config too.
   (See `gateway-only-llm-routing-enforced` memory.)
2. **Action authorization.** The engine is a *confused-deputy* risk: it can send
   email, write memory, ingest URLs, and (via little-coder) run code. It needs an
   allow-list of node types and a clear trust boundary — especially because the
   tailnet lane has no Authelia. Treat little-coder and email/wiki **sinks** as
   privileged; consider an operator-confirm gate (the teams-chat "stop-gate"
   governance model is directly reusable here).
3. **Prompt-injection surface.** Research/search nodes pull untrusted web content;
   chaining their output into an LLM node or a coding task widens the existing
   output-poisoning threat. Reuse `openbrain-research`'s `injection.ts` /
   `screenSources` defenses; never feed un-screened source text into a privileged
   sink. (See `research-engine-injection-defense` memory.)
4. **Podcast node coupling.** Podcast generation is today the *tail* of the digest
   chain (`gmail-pull → prune → podcast → digest`) and assembles from
   `reusable_claims`, not from an arbitrary synthesis. A standalone "Format →
   Podcast" node needs the renderer **decoupled** from the digest-specific brief
   assembly. This is the single biggest ADAPTER, not a free READY.
5. **Long-run reliability.** Runs can take many minutes (research + podcast). The
   engine needs durable run state (survive an engine restart), idempotent node
   re-trigger, timeouts, and partial-failure semantics (one Format branch fails,
   others still deliver) — the digest chain's fallback-trigger pattern is a model.
6. **Scope creep into "another n8n".** The stack already has cron + chain
   triggers. The temptation is to build a general workflow engine. **Resist:**
   v1 should ship the ~6 READY nodes + Research fan-out, not a universal node SDK.
7. **Three-place + recovery discipline.** Two new containers (plus DB + backup)
   touch compose, emergency-recovery scripts, and the stack-map. Skipping this is
   how the recovery plane silently rots.

**Open questions for the operator:**
- (a) Single new compose project (`automations`, like OB1/agent-org) or fold the
  two services into the main stack? *(Recommend: separate project, external-net
  attach — matches isolation habit.)*
- (b) Is this **single-user** (you) or will it be shared via cloudflared to other
  people? That decides how hard §9.2 auth has to be.
- (c) Should automations be able to trigger **little-coder** in v1, or is that
  deferred until the teams-chat governance layer exists?
- (d) Build the UI graph editor from scratch, or adopt an existing OSS node-editor
  (React Flow / Rete / a fork of n8n's editor)?

---

## 10. Feasibility verdict

**Feasible — and notably well-matched to this stack. Rating: HIGH feasibility,
MEDIUM build effort.**

**Why HIGH feasibility:**
- The hard part of any automation platform — *long-running jobs, async polling,
  service-to-service chaining, dual-plane exposure with auth* — **already exists
  and runs in production daily** (the digest→podcast chain, research jobs, the
  Portal, tailscale serve).
- The flagship Research node is **READY today**: a documented async
  `POST /research` + poll/SSE contract with a structured `research_result`.
- ~12 of the ~17 catalogued nodes are **READY** with zero backend work.
- There is a clean, repeated precedent (OB1, agent-org) for adding a new compose
  project that attaches to existing networks.

**Where the real work is (MEDIUM effort):**
- `automations-engine`: the graph store + topological async executor + node
  registry + ~6 adapters. This is real but bounded software — closest analog is
  the teams-chat `agent-bridge`.
- `automations-ui`: a node/graph editor. Biggest *visual* effort; strongly favor
  adopting an OSS graph library over hand-rolling.
- The **Format → Podcast** adapter (decouple the renderer from the digest brief).
- Hardening the **action-authorization / injection** boundary (§9.2–9.3) before
  any privileged sink (email, wiki, little-coder) is exposed — non-negotiable if
  the cloudflared lane is shared.

**Recommended phasing:**
- **P0 — Spike:** engine + UI with **two** node types (Research → Format→OWUI),
  manual trigger only, tailnet-only (no cloudflared), no privileged sinks. Proves
  the executor + the research async contract end-to-end.
- **P1 — Fan-out:** add Format→ON and Format→Podcast (the podcast decouple),
  prove one-research-to-three-sinks (the §6 graph).
- **P2 — Palette:** add the READY read/write nodes (search, OB capture/search,
  memory, LLM, extract) + schedule trigger.
- **P3 — Expose & govern:** cloudflared lane + Authelia + the §9 auth boundary;
  only then consider little-coder and email sinks.

**Bottom line:** this is not a moonshot. It is *productizing a pattern the stack
already embodies*. The single largest risk is **scope discipline** (don't build a
universal workflow engine) and the single largest hardening task is the
**action-authorization boundary** once the public lane is on. Start with the
Research → OWUI spike on the tailnet; it will be working quickly and will tell you
whether the full builder is worth it.

---

## 11. Pointers

- Research async contract & grounding → `documentation/implementation-guide/research-engine-for-OB/`
  (`GROUNDING-MODEL.md`, `PLAN-research-engine.md`) and `OB1/integrations/research-service/`.
- Podcast pipeline (the de-facto first automation) → `documentation/expanding-daily-digest-with-auto-podcast/`,
  `OB1/recipes/daily-digest/`.
- Dual-exposure recipe (caddy/authelia/cloudflared + tailscale serve) →
  `scripts/portal-on.ps1`, `config/caddy/Caddyfile`, `entrypoint.sh`.
- New-compose-project precedent → `documentation/implementation-guide/teams-chat-agent-orchestration/`
  (PLAN §3) and `OB1/docker/docker-compose.yml` (external-network attach).
- Stack inventory → `/stack-map` skill /
  `.claude/skills/stack-map/references/workspace-stacks.md`.
- Governance / stop-gate model to reuse for §9.2 →
  `teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md`.
