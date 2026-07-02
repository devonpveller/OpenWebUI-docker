# Plan — Teams-style Chat for Agent Orchestration (governed multi-agent org)

**Status:** 🛠️ **BUILT (v1 2026-07-01; comms model + P4.0 ground/dry-run 2026-07-02).** This plan
is implemented as the **`agent-org`** compose project (`agent-bridge` + all §3.1.1 modules, the P2
gate, the deterministic [comms router](COMMS-MODEL-deterministic-routing.md), and the P4.0
risk-gated dry-run gate + grounding client). **81 passing tests.** Per-task build status is the
single source of truth in [`agent-org/IMPLEMENTATION-NOTES.md`](../../../agent-org/IMPLEMENTATION-NOTES.md);
what remains is **operator-gated** (Mattermost bot [done], OB-mirror env wiring, mobile, tailnet
exposure) and the **conditional cloud lane Pc** (skipped — P0.5 resolved LOCAL_JUDGE_OK). Baselined
against the live workspace **2026-06-13** (LiteLLM `llm-gateway` is LIVE — see §0). §§1–8 below are
the design of record (kept as authored; where the build refined a shape, IMPLEMENTATION-NOTES + the
COMMS-MODEL doc note it).
**Owner:** ai-stack
**Branch:** TBD new feature branch; **no `main` merge** without explicit ask (G1).
**Companion docs:**
- [OUTLINE-teams-chat-agent-orchestration.md](OUTLINE-teams-chat-agent-orchestration.md) — platform/tooling selection
- [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) — **the governing spec; this plan implements it**
- [UX-FLOW.md](UX-FLOW.md) — **the user journey** (intake → readiness-gate → plan → ground/dry-run → execute → escalate) + the intent thread, idle-wait DAG, and CONCERN schema
- [Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md) — source paper (arXiv:2604.10290)

> **Read order:** the governance doc is the spec. This plan is *how we build it*. Where they
> disagree, the governance doc wins and this plan gets corrected.

---

## 0. Workspace status & plan baseline (current as of 2026-06-13)

This plan is **design-only — none of it is built.** This section pins what **already exists**
in the workspace (consume it, don't rebuild it) vs. what **this plan will build** (the planned
work), so the boundary is unambiguous. Baselined against the live `docker-compose.yml` +
`config/litellm.config.yaml` + `config/llama-swap.config.yaml` + the `litellm-proxy-status` memory.

### 0.1 Current workspace status — what ALREADY EXISTS (consume, do not build)

- ✅ **LiteLLM `llm-gateway` is LIVE** (since 2026-06-12) — a **deliberately air-gapped LOCAL
  analytics front door**: callers hit `http://llama-cpp:8080` (a network alias on the gateway) →
  `llm-gateway` (LiteLLM) → `llama-cpp-upstream` (llama-swap) → llama.cpp. **Permissive (no
  `master_key`)**, **no internet egress by design**, `background_health_checks: false`,
  `qwen36-35b-a3b` removed (swap-thrash). Spend ledger in `llm-gateway-db` (+ `llm-gateway-backup`).
- ✅ **Local inference**: `qwen36-27b` on llama-swap (now `llama-cpp-upstream`), `N_PARALLEL=2`,
  256k ctx, KV `q4_0`, single GPU.
- ✅ **little-coder substrate**: `little-coder` + `open-terminal` + `lc-mcpo` + `lc-egress`;
  `--session`; Agent Skills format; the `git-proxy` + sanitization + two-plane **floor**; the
  `meta` learning loop; journals audit (TOOLING §2). **Single-task FIFO today** (the gap, §3.6).
- ✅ **Networking**: `ai-stack_llm-net` is `internal: true` (no internet); the external-network
  pattern (OB1 `name: open-brain` attaches to `ai-stack_llm-net`) is the model agent-org copies.
- ✅ **`tailscale`** (tailnet exposure), **Open Brain** (audit mirror + suggestion-worker).
- ❌ **NOT present (this plan adds):** Mattermost, the `agent-bridge`, the worker pool, role
  profiles, and the **cloud** LiteLLM / OpenRouter path.

### 0.2 Planned work — what THIS PLAN builds (the deltas over 0.1)

| Planned component | Status | Phase |
|-------------------|--------|-------|
| **`agent-bridge`** (Python/FastAPI) — orchestration + governance gate | NEW | P0–P6 |
| **Mattermost + `mattermost-db`** (chat + mobile) | NEW | P0 |
| **Worker pool** — N × `(little-coder + open-terminal)` + concurrency scheduler | NEW (extends today's single-worker little-coder) | P5 |
| **Role/model profiles** registry (§5.4) | NEW | P5/P7 |
| **Governance enforcement** — charters via profiles/skills, hooks, stop-gates, review, learning-loop *extension* | NEW / extend existing | P3–P6 |
| **Cloud lane** — separate `llm-gateway-cloud` + `ao-egress` + OpenRouter + budgets (the *planned OpenRouter extension of LiteLLM*) | **CONDITIONAL — built only if the P0.5 capability-floor test shows local judgment is too weak** | **Pc** (fires right after P0 if mandated; §4) |

**Local inference needs zero new model-layer work** — agent-org's bridge + workers just call
`http://llama-cpp:8080` (the existing gateway) and get analytics for free. The *only* model-layer
build is the **conditional cloud lane** (§3.4, OD-6).

### 0.3 Audit corrections already folded into this plan (history)

- **Egress reality:** `llm-net` has no internet, and the live `llm-gateway` is air-gapped **on
  purpose** — so OpenRouter is **not** routed through it. The cloud lane is a *separate* gateway
  with its own `ao-egress` (§3.4/§3.7; was a wrong assumption in an earlier draft).
- **No local 35B judge:** removed from the gateway (swap-thrash); local judge = same `qwen36-27b`,
  else cloud (§3.4).
- **Stack-map:** ✅ reconciled 2026-06-13 — the portal/backup planes (06-11) **and** the
  `llm-gateway` flip (`llama-cpp` → `llama-cpp-upstream` + the gateway aliases, + `llm-gateway-db`/
  `-backup`/`-db-data`). Recovery scripts + CLAUDE.md were updated by the LiteLLM work itself. So
  the agent-org 3-place change now adds onto an accurate baseline.
- **Mattermost mobile push** is a privacy surface (OD-9); **worker-pool container math** reinforces
  small N (§3.6).

---

## 1. Problem / goal

Stand up a self-hosted, mobile-accessible chat platform that doubles as the **coordination
fabric for a fleet of coding agents**, organized as a company:

- **Human Operator (you)** ⇄ **PO (Project Overseer agent)** ⇄ **PM (Project Manager agent)** ⇄ **domain workers** (`little-coder`). (Role taxonomy: governance §1.)
- Agents are first-class chat participants; a worker that hits an error outside its scope
  **messages the last owner**, which **wakes** that worker to fix it and **reply in-thread**.
- Every agent exchange is **observable** and the **Human Operator can join any channel/DM** to correct
  direction in real time.
- The whole thing is governed so it doesn't fall into the paper's failure mode (more capable
  but **less aligned**): mandatory escalation, **pause-until-cleared**, goal-grounded workers,
  differently-goaled review, doc-enforced stop-gates, and a propose-not-dispose learning loop.

**The chat platform is the easy part** (mature OSS exists). The build is: the **agent-bridge**
(event bus ↔ little-coder), and the **governance enforcement** baked into it.

---

## 2. Goals & non-goals

**Goals**
- Adopt **Mattermost** (OSS, self-host, native iOS/Android, Bot API + WebSocket event bus +
  plugins) as the chat layer — see OUTLINE §3 for why over Matrix/Zulip.
- Build **`agent-bridge`**: persistent WebSocket consumer + REST poster that maps
  `channel/thread ↔ work-effort ↔ little-coder session`, owns **wake**, and **enforces the
  governance gate** (escalation, freeze, operator decision).
- Reuse `little-coder`'s existing `--session <id>` per-chat resume — the session id becomes
  the chat **thread id** (memory: `little-coder-per-chat-sessions`).
- Implement governance as **skills + hooks** (reuse `.claude/skills/`), not a parallel system.
- Full **observability + audit** (mirror to Open Brain), Human-Operator **kill switch**, fail-safe pause.
- **Local-first model posture.** Everything runs on local `llama-cpp` by default. A larger
  model is used **only where a step is *mandatorily* beyond local capability** (judgment roles —
  see §3.4), and only via **OpenRouter** (privacy-respecting external routing), **never** a
  mainstream consumer frontier service. *(Grounded in the small-vs-frontier analysis.)*

**Non-goals (this plan)**
- Not building a chat app — adopting one.
- No public/cloud exposure of the chat server in v1 (tailnet/LAN only; reuse tailscale).
- No E2EE on agent channels (observability is a safety requirement, governance §5/§7).
- Not throughput-optimizing before the governance shape works (governance §9 build order).
- No autonomous rule self-modification — the learning loop **proposes**, the Human Operator disposes.
- **No mainstream data-collecting frontier LLM service.** Any cloud touch is OpenRouter only,
  routed to **no-log / zero-data-retention** providers (prefer open-weight large models), and
  only for steps that are mandatorily beyond local capability. Default is always local.

---

## 3. Architecture

### 3.1 The seam

```
  ┌──────────────┐  WebSocket events   ┌────────────────────┐  spawn/resume   ┌─────────────┐
  │  Mattermost  │ ──────────────────▶ │   agent-bridge     │ ──────────────▶ │ little-coder │
  │ (chat + app) │ ◀────────────────── │  router · waker ·  │ ◀────────────── │  workers    │
  └──────────────┘   REST: post/thread │  GOVERNANCE GATE   │  result/explain └─────────────┘
        ▲  Human Op joins any channel (mobile)└──────┬──────────┘
        │                                         │ audit + learning
        │                                         ▼
        └────────────────────────────  Open Brain (audit trail, patterns, suggestion pool)
```

- **agent-bridge** is the new heart: it holds the channel↔effort↔session map, the **scope
  ledger**, the **rule/goal version store**, and the **escalation-gate state machine**
  (freeze / CONCERN / operator decision / resume). Governance §3, §5.
- **Mattermost** supplies identities (bot accounts per agent), the real-time event bus (wake
  trigger), threads (hand-offs), channels (work efforts), and the mobile apps.
- **little-coder** workers are unchanged in spirit — woken via session resume; they receive
  goals + skills (charters) as injected context; they post via the bridge.

#### 3.1.1 agent-bridge internal modules (SRP — keep the Thinnest Viable Platform thin)

The bridge is the **TVP / Platform** (TT analysis §4) **and** the safety-critical component, so it
must **not** be a god-service. v1 decomposes into modules with explicit interfaces, so the
**governance-gate FSM can be unit-tested and reasoned about in isolation** from WebSocket plumbing:

| Module | Single responsibility | Talks to |
|--------|----------------------|----------|
| **event-gateway** | Mattermost WS consume + REST post; **idempotent, at-least-once** event handling (below) | Mattermost ↔ router |
| **governance-gate** | the gate FSM (machine A, governance §3.0): freeze/CONCERN/clear; fail-safe persistence | scope-ledger, audit-sink |
| **scheduler** | worker-pool registry + `MAX_CONCURRENT_WORKERS` semaphore + idle-wait FSM (machine B, §3.6) | model-router, little-coder |
| **scope-ledger** | who-may-touch-what; grant/revoke per hard-rule #2; retirement (§4.1) | governance-gate |
| **router/waker** | channel↔effort↔session map; resolve target; wake/resume sessions | scheduler, event-gateway |
| **model-router** | local-vs-cloud lane selection (§3.4) + profile→model binding (§5.4) + GBNF/Instructor validation | gateways |
| **audit-sink** | append-only event log + Open Brain mirror (§5, §6) | everything (write-only) |

> **Why this matters most for the gate:** the gate is the one module whose correctness is
> load-bearing for safety. Isolating it (no direct WS/REST coupling) is what lets P2's safety
> tests target it deterministically — and keeps the "make the safe path the default path" platform
> minimal rather than letting features accrete into the brake.

**Event-delivery semantics (the wake bus must be reliable — industry-standard pattern).** The whole
system hinges on "wake on @mention," so event-gateway is built for **at-least-once delivery with
idempotency**, not best-effort:
- **Idempotency keys:** every Mattermost event carries a post id; the bridge dedupes on
  `(event_id)` so a redelivered event never double-wakes/double-spawns.
- **Reconnect catch-up:** Mattermost's WS does **not** replay missed events. On reconnect (or after
  a bridge restart) the event-gateway **polls the REST API for posts since the last-processed
  timestamp** per active channel, replays them through the idempotent path, *then* resumes the WS.
- **Effect:** a missed wake (bridge was down) is recovered on reconnect; a duplicate wake is a
  no-op. A wake that still can't be delivered (target unreachable past a bound) is itself a §3
  trigger, not a silent stall.

### 3.2 Deployment shape — new compose project

Mirror the **Open Brain** pattern: a **separate compose project** (`agent-org`) rather than
bolting onto the main `ai-stack` compose, so it has its own lifecycle and resource envelope.
It attaches to the main stack's `ai-stack_llm-net` (external) for local-model access, exactly
as OB1 does (CLAUDE.md "Stacks at a glance").

- New containers (project `name: agent-org`): `mattermost`, `mattermost-db` (Postgres),
  `agent-bridge`, a **bounded pool of worker instances** — N × `(little-coder + open-terminal)`
  pairs (§3.6) with a **shared** `lc-egress`-style git-allowlist proxy — and, **only if P0 mandates
  a cloud judge**, `llm-gateway-cloud` (+ its spend DB) and **`ao-egress`** (OpenRouter allowlist
  proxy, mirrors `lc-egress`, §3.7).
- **Local LiteLLM is already live (not built here):** the **existing `llm-gateway`** (air-gapped
  analytics front door, `documentation/LiteLLM-Proxy/`, memory `litellm-proxy-status`). agent-org
  reaches it transparently via the `llama-cpp` alias and **adds nothing to it**. The **cloud**
  LiteLLM (above) is a *separate sibling* agent-org stands up for OpenRouter (§3.4, OD-6).
- Attach to external **`ai-stack_llm-net`** (reach the `llama-cpp` alias → `llm-gateway`); reuse
  existing `little-coder`/`open-terminal` images + `tailscale`. **No new inference containers** —
  workers + local judge share the existing single-GPU `llama-cpp-upstream`/llama-swap backend
  behind the gateway.
- **3-place change** applies for every container: compose **+** `emergency-recovery.ps1`/`.bat`
  inventory & sequences **+** stack-map reference (run `/stack-map`). G-convention.
- Bring up after `llama-cpp` healthy; tear down before main stack (same rule as OB1).

> **Open decision OD-1:** separate `agent-org` project (recommended) vs. add to main ai-stack
> compose. Recommend separate (isolation, matches OB1). Governance §7 unaffected.

### 3.3 Governance enforcement map (where each safety control lives)

This plan exists to make the governance doc real. The mapping:

| Governance control | Where it's enforced |
|--------------------|---------------------|
| Escalation gate / pause-until-cleared (§3) | `agent-bridge` state machine; freeze = stop dispatch/wake for an effort |
| CONCERN + operator decision (§3) | structured Mattermost post type + bridge parser; the Human Operator replies from mobile (PO resolves steering; hard-gate → Human Operator) |
| Bus-only comms (§5) | workers have **no** channel but Mattermost; bridge is the only transport |
| Rules-as-skills, floor/steering split (§4.2) | charters = little-coder **Agent Skills** + founding knowledge; floor **already enforced** by existing `git-proxy` + `lc-egress` + sanitization filter + two-plane split (extend, don't reinvent — TOOLING §2) |
| Worker harness + wake | **reuse** little-coder/`pi` + `--session <thread_id>` (TOOLING §2) |
| Learning loop (§6) | **extend** little-coder's existing `meta` + cohort/tier + efficacy-reversion + human gate (already propose-not-dispose) |
| Worker concurrency | **bounded pool** of `(little-coder + open-terminal)` pairs, scheduled by the bridge against shared-inference capacity (§3.6) |
| Goal grounding, constraints-inline (§4.3) | PM composes worker goals; bridge injects on spawn/wake; versioned |
| Plan-stop-gates + explain-intent (§4.5) | checkpoints in the worker's plan doc; bridge blocks past a checkpoint until review cleared |
| Peer review by differently-goaled agents (§4.4) | bridge spawns reviewer(s) with an ethics/whole-picture goal → report to PM, not self-approve |
| Scope ledger / role-expansion authority (§4.1) | bridge ledger; new role *type* = Human-Operator-gated (PO proposes), new *instance* = PM |
| Audit trail + learning loop (§5, §6) | bridge logs every event; mirror to Open Brain; suggestion pool; **propose-not-dispose** |
| Kill switch / model-by-role (§3, §7) | bridge global freeze; **local lane** via existing air-gapped `llm-gateway`; **cloud lane** (judge, if mandated) via separate `llm-gateway-cloud` → `ao-egress` (§3.4) |
| Roles = model profiles (C4) | a profile binds {charter/system-prompt, temp, tool-scope, caller-key} → a gateway model name; adding a role = adding a profile, not a gateway change (§5.4) |
| Deterministic coordination (small-model analysis) | the **bridge** does routing/wake/handoff/gating; agents barely negotiate — weak models can't coordinate reliably (§3.5) |

### 3.4 Model layer — local via the air-gapped gateway, cloud via a *separate* LiteLLM (reconciled with as-built LiteLLM, 2026-06-13; governance F7 / §2.1)

**The integrated `llm-gateway` is a deliberately air-gapped LOCAL analytics front door — not a
local+cloud router** (operator confirmed: cloud was intentionally out of scope for the current
ai-stack). So agent-org splits the model layer in two and **preserves that air-gap** (operator
decision **C1 = option B**: separate cloud and local access):

| Lane | Model | Reached via | Cost control | Egress |
|------|-------|-------------|--------------|--------|
| **Local** (workers; local judge/reviewer) | `qwen36-27b` | the **existing `llm-gateway`** — just call `http://llama-cpp:8080` (transparent alias → gateway → `llama-cpp-upstream`); analytics for free | none — local is free (no `master_key`, no budgets) | **none (air-gapped)** |
| **Cloud** (judge/reviewer *only where P0 mandates*) | OpenRouter (no-log/ZDR, open-weight) | a **NEW, separate `llm-gateway-cloud`** (agent-org's own LiteLLM) | **`master_key` + virtual keys + per-role budgets** = the cost-tiered supervision cap (governance §3) | **only** via `ao-egress` → `openrouter.ai` |

- **Local stays exactly as-built — do not touch its air-gap.** Workers (and a local 27B judge)
  reach inference through the `llama-cpp` alias on `llm-gateway`; that gateway has **no internet
  route by design** (supply-chain posture) and **no `master_key`** (permissive — budgets are
  pointless when local inference is free). agent-org **adds nothing to it** and registers no new
  models on it.
- **Cloud is a standing, separate LiteLLM (`llm-gateway-cloud`, + its own spend DB)** that
  agent-org stands up **when a cloud judge is actually needed** (gated on the P0 capability-floor
  test). It is the **only** place OpenRouter is configured, the **only** agent-org component with
  an egress (`ao-egress`), and where **`master_key` + virtual keys + budgets** live — because
  cloud is metered and *does* need budget allocation (C2; local never did). This is the planned
  **"OpenRouter extension of LiteLLM"** the operator intends, built as a **sibling** so that
  "never route around LiteLLM" still holds for cloud **and** the local gateway stays air-gapped.
- **Analytics are joined later, tagged by lane.** Two spend ledgers (`llm-gateway-db` local +
  `llm-gateway-cloud-db`); a later step unions them with a `lane: local|cloud` tag. No live merge.

**Roles are profiles, not gateway models (C4 — operator's framing).** The gateways only know
*underlying model names* (`qwen36-27b` local; OpenRouter model ids on the cloud gateway). The
**PM/monitor, planner, `reviewer-<lens>`, `worker-<domain>` distinctions are agent-org "model
profiles"** (§5.4): a profile binds {system prompt = charter, temperature, tool access = scope,
caller-key, **lane**} to a gateway model name. **Adding a role = adding a profile** — never a
gateway change. Only a genuinely new *underlying model* touches a gateway config.

**Judgment roles are the cloud-lane candidates — including plan generation (operator).** The
**PO (Project Overseer agent), the PM, the planner, and the reviewer** are judgment-heavy; in
particular **plan generation is a cloud-lane task**, because a weak planner caps the *productivity
ceiling* of everything the org builds downstream — so it earns the cloud spend even though we'd
prefer all-local. **Workers stay local.** ⚠️ **The exact reach is a per-profile `lane` setting,
tuned empirically** (start judgment-heavy-roles-cloud / workers-local, then stretch the local
boundary as practice shows what `qwen36-27b` can hold). Idle-wait (§3.6) keeps idle cloud agents
from burning OpenRouter tokens; the cloud budget caps the rest. (The **Human Operator (you)** is the
tier *above* the PO — no model; final authority on the §3 hard-gate triggers. See governance §1 / UX-FLOW §1.)

> **Swap-thrash constraint still holds (audit §0 / as-built).** The local gateway exposes only
> `qwen36-27b` — the operator **removed `qwen36-35b-a3b`** to stop 27B⇄35B swap thrash and unmask
> spurious 35B loads. So the local judge is the **same `qwen36-27b`** as workers (zero swap); if
> that's too weak (P0), judgment goes to **OpenRouter via `llm-gateway-cloud` (off-GPU)** — never
> local 35B. The decision stays binary: **all-local same-model, or cloud for judgment.**

**Why a cloud judge may be *mandatory* (not merely nice):** the small-vs-frontier analysis + the
paper's weak-model data (GPT-5-MINI lost the org capability benefit to coordination failure;
GPT-4.1 held a low ethics floor) show **judgment roles** are exactly where small local models fail
worst and where capability buys *safety*. So *if* P0 shows 27B judgment is insufficient, those
roles (and only those) move to the cloud lane. **Workers stay local regardless.**

**Privacy boundary (before any cloud call — OD-6):** only **governance-level summaries** (claim/
goal/deviation/options) leave the box — **never raw proprietary code or secrets**. The bridge
builds + logs the egress payload; `llm-gateway-cloud` pins no-log/ZDR providers, preferring
open-weight models. (Raw code never reaching the cloud judge also keeps the lane split clean.)

**Reliable structured output — constrained decoding.** Worker/local-judge structured calls use
**GBNF / JSON-schema constrained decoding** (llama.cpp via llama-swap, behind the gateway) so a
small model *cannot* emit invalid output, paired with **Instructor/Pydantic** validation in the
bridge — the direct fix for the GPT-5-MINI format-failure (TOOLING §3.2).

> **⚠️ Never probe model health (as-built constraint, C5).** The local gateway runs
> `background_health_checks: false` because an active probe = a real completion = a llama-swap
> **load = thrash** (this is what forced the 35B removal). The capability-floor test (P0) and the
> bridge's continuous monitoring must use **bounded real completions** + the side-effect-free
> **upstream `/health`** — never model health-pings. Apply the same rule to `llm-gateway-cloud`.

**Load-bearing principle either way:** the **gate + deterministic floor carry safety, not the
models** (governance §3, §4.2). Because the default is local (weak), the symbolic floor does
*more* work, and coordination lives in the **bridge**, not in agent-to-agent negotiation (§3.5).

### 3.5 Coordination lives in the bridge, not the models (small-model consequence)

The paper's GPT-5-MINI result — a weak model's *org was less effective than a single agent
because it couldn't follow the coordination protocol* — is a direct warning for our local stack.
So:

- **The `agent-bridge` (a Platform / Thinnest Viable Platform, per the TT analysis) owns
  coordination**: routing, wake, handoff contracts, gating, format validation. Agents do **not**
  negotiate protocol with each other.
- **Comms are bus-mediated, not direct agent-to-agent** — a deliberate position (observability =
  safety), and also a small-model necessity (weak models botch direct A2A).
- **Defensive I/O:** the bridge validates/repairs structured output and keeps each worker's tool
  surface minimal; charters/goals are short, sharp, repeated (small models drift from long ones).
- **"Org vs. single agent?" is an explicit per-task question** (governance §8): with weak models,
  fanning out can *lose* capability — sometimes one well-scoped local worker + a strong judge
  beats a fan-out the locals can't coordinate. Don't assume multi-agent wins.

### 3.6 Worker concurrency — a bounded pool on a shared backend (the real lift)

little-coder today is **single-task FIFO, one workspace, human-attach read-only** (design §12.4).
Our org needs **concurrent** workers, so:

- **Workers = a bounded pool of `(little-coder + open-terminal)` instance pairs.** Each pair keeps
  its own network-isolated workspace + `git-proxy` + `lc-egress` — **per-plane containment is
  preserved** (the safety surface is intact per instance). The `agent-bridge` assigns efforts →
  instances and owns the pool.
- **The ceiling is inference capacity, not container count.** `llama-cpp`/llama-swap is **shared
  across the whole stack** (OWUI interactive, OB1 jobs, mnemory, little-coder). Per little-coder
  §3.5/§12.5 the rule is **interactive-always-wins; background backs off.** So the fleet's
  concurrency budget = *parallel slots − a reserve for interactive/other services.*

**The GPU/context budget (operator-measured, `qwen3.6-27b`, KV cache @ Q4 — aggregate ≈ 250–260k
tokens):**

| Backend config | Per-slot context | Notes |
|----------------|------------------|-------|
| 2 parallel (current) | ~130k | more than little-coder needs; little headroom for more lanes |
| **3 parallel (preferred)** | **~83k** | operator's pick — good long-run headroom for OWUI/OB1/other services |
| 4 parallel (burst) | 64k | survivable; use when more lanes are genuinely needed |
| ~~5+ parallel~~ | ~~32k~~ | **floor breached — operator-confirmed unmanageable; do not** |

- **little-coder's own needs fit comfortably under 64–83k**, so the per-slot context isn't the
  binding constraint — *slot availability* is.
- **Fleet concurrency, recommended default:** with **3 parallel @ ~83k**, reserve ≥1 slot for
  interactive → the agent-org fleet runs **~1–2 concurrent workers**. Bumping to **4 @ 64k** for a
  burst yields **~2–3**. The bridge enforces this as a **configurable semaphore
  (`MAX_CONCURRENT_WORKERS`)** — never a hard pin to slot count.
- **⚠️ No live GPU-occupancy signal (C6, as-built).** `/slots` returns 404 on llama-swap (dead even
  for little-coder today), so the scheduler **cannot** read GPU occupancy to do dynamic
  "interactive backoff." v1 uses a **static, conservatively-sized semaphore** (leave the interactive
  reserve free by *configuration*, not by probing). Optional later: revive `/slots` via a LiteLLM
  `pass_through_endpoints` entry (a gateway-config change, not a bridge hack) for a real signal.
- **No second local model in the loop (audit §0).** Because llama-swap keeps one model resident
  and swapping thrashes the GPU, the judge runs on the **same `qwen36-27b`** as workers — so judge
  calls and worker calls contend for the *same* parallel slots (no separate model, no swap). If
  judgment goes to OpenRouter, it leaves the GPU budget entirely (off-box).
- **Worker-pool container math (audit §0).** Each pooled worker ≈ a `(little-coder + open-terminal)`
  pair (open-terminal is the per-instance isolated workspace — cannot be shared); a single
  `lc-egress`-style git-allowlist proxy can be **shared** across the pool. So N=2 workers ≈ 4–5
  worker containers + bridge + mattermost(+db) + ao-egress. The container/volume cost is another
  reason to keep N tiny.
- **This is the GPU enforcing "keep the org small" (governance §4.1)** and the "org vs. single
  agent?" discipline (§3.5). The inference budget *is* the org-size budget. (Open decision OD-8.)

**Idle-wait — the scheduler / inference-slot FSM (CANONICAL home; UX-FLOW §5 and governance §3.0(B)
reference this table).** Agents hold a slot only while *actively computing*; the bounded budget
only works because a blocked agent **releases its slot**. This is **machine (B) of the two
orthogonal state machines** (governance §3.0) — a *scheduling* state, **not** a safety state.

| Scheduler state | Holds a slot? | Entered when | Woken by |
|-----------------|---------------|--------------|----------|
| **computing** | ✅ | doing work | — |
| **waiting** | ❌ (slot freed) | voluntarily yields while a dependency is pending — an operator decision, dry-run, build, **or another agent's effort** | a **`finish` event** or a **timeout** |
| **suspended** | ❌ | parked `--session` (no current work queued) | a new assignment / @mention wake |

> **`frozen` is NOT in this table — it belongs to the *governance gate* (machine A, governance
> §3.0).** Freezing is a **brake**; `waiting`/`suspended` are **ordinary idleness**. Composition
> rule (governance §3.0): a `frozen` effort's agents are forced out of `computing` and the
> scheduler may not re-admit them until the **Human Operator** (hard-gate) or **PO** (steering)
> clears the gate. An agent can be `waiting` while its effort is perfectly `active`.

- **Dependency DAG (operator):** an agent blocked on another's output goes **waiting** (slot
  freed) and wakes on that effort's `finish` — so dependent efforts run **"linearly"** (waiter
  idle, not spinning) while **independent efforts parallelize** up to the slot budget.
- **"Together when their work touches":** overlapping efforts (same files/area) coordinate via the
  bus + handoff contract; a true collision is an **F4 cross-effort conflict → pause + escalate**,
  never blind parallel edits.
- **Reuses** Claude Code's wait/`ScheduleWakeup` + little-coder `--session` suspend/resume (a parked
  session costs no inference). The bridge owns the wait registry + wake-on-event. It also keeps
  **idle cloud (PM/judge) agents from burning OpenRouter tokens** while they wait.

### 3.7 Networks & egress (corrected against live compose, audit §0)

Two model lanes (§3.4) → two network paths. **Local stays air-gapped through the existing
`llm-gateway`; only the separate `llm-gateway-cloud` touches the internet, through `ao-egress`.**

```
 Human Op (tailnet) ──tailscale serve──▶ mattermost ─┐
                                                │ ao-net (internal, no internet)
   agent-bridge ◀─WebSocket/REST─▶ mattermost   │
   LOCAL  bridge + workers ──▶ ai-stack_llm-net ──▶ llama-cpp:8080 (alias) ──▶ llm-gateway
                                  (existing, AIR-GAPPED) ──▶ llama-cpp-upstream (qwen36-27b)
   CLOUD  bridge ──▶ llm-gateway-cloud ──▶ ao-egress ──default──▶ openrouter.ai (allowlist, no-log/ZDR)
   workers' git ──▶ shared lc-egress (git host allowlist)
```

- **`ao-net`** (internal, no internet): bridge ↔ Mattermost ↔ workers ↔ `llm-gateway-cloud` (control side).
- **`ai-stack_llm-net`** (external): bridge + workers reach the **existing `llm-gateway`** via the
  `llama-cpp` alias for local inference. **The local gateway has no egress — untouched.**
- **`llm-gateway-cloud`** is the **only** agent-org component on an internet path, and reaches it
  **only** through **`ao-egress`** (on `ao-net` + `default`), an allowlist proxy pinned to
  `openrouter.ai`. Single, audited egress for the privacy boundary (§3.4). Nothing else in
  agent-org touches the internet; the **local `llm-gateway` air-gap is preserved**.
- **Mattermost exposure:** tailnet via **`tailscale serve`** (operator step, mirrors the
  open_notebook :8443 serve pattern) — *not* the Cloudflared/Authelia portal in v1 (Mattermost
  Team Edition is dropping SSO; tailnet is simpler and private). Host-published on `127.0.0.1`.

---

## 4. Phases

Build order honors governance §9: **governance shape before throughput.** The gate, bus-only
comms, and charters land before we scale the fleet or optimize.

| Phase | Title | Output | Risk | Run by |
|-------|-------|--------|------|--------|
| **P0** | Platform spike **+ capability-floor test** | Mattermost + db + one bot up; a **GBNF-constrained** structured call to the **existing `llm-gateway`** (via `http://llama-cpp:8080`, no new gateway); `agent-bridge` echoes a mention; **measure `qwen36-27b` on instruction-following / structured-output / coordination → decide if a cloud judge is needed** (no model health-probes — C5) | low | dev build |
| **Pc** *(conditional)* | **Cloud lane — fires only if P0.5 mandates a cloud judge** | stand up the **separate** `llm-gateway-cloud` (+ spend DB) with `master_key`/per-role virtual keys/budgets + OpenRouter models + **`ao-egress`** (allowlist `openrouter.ai`, no-log/ZDR); flip judge/reviewer **profiles** `lane: cloud`; bridge builds + logs the **governance-summary-only** egress payload. **Leave the local `llm-gateway` air-gap untouched.** | med | author + operator |
| **P1** | Wake mechanic | bridge resumes a dormant `little-coder` session on @mention; one A→B hand-off in a thread, end-to-end | med | dev build |
| **P2** | **Escalation gate (core safety)** | CONCERN type, freeze/pause-until-cleared, operator-decision parse, **kill switch**, fail-safe default | **high value** | dev build |
| **P3** | Charters + grounding | charters as skills (floor/steering split); **hooks** enforce hard-rule #4; goal-injection on spawn/wake; versioned rule/goal store | med | dev build |
| **P4** | Plan-stop-gates + review | checkpoints in worker plan docs; **explain-intent** at each stop; differently-goaled reviewer → report to PM; self-report cadence | med | dev build |
| **P5** | Dynamic roles + **worker pool** + routing | **worker-instance pool + `MAX_CONCURRENT_WORKERS` scheduler w/ interactive backoff (§3.6)**; scope ledger; role-type (Human-Operator-gated) vs instance (PM) authority; "last-owner" provenance (git-blame v1 → ledger); channel taxonomy | med-high | dev build |
| **P6** | Audit + learning loop | full event log → Open Brain; suggestion pool; pattern surfacing; **propose-not-dispose** Human-Operator approval flow | med | dev build |
| **P7** | Mobile + hardening | Human-Operator mobile flow (join any channel, decide CONCERNs, kill switch); rate caps; tailnet exposure; CONCERN-card UX. *(Cloud LiteLLM moved to the conditional **Pc** phase so the alignment core isn't blocked on it — see below.)* | med | author + operator |

P0–P2 are the spine (prove the loop *and* that we can stop it). P3–P4 are the alignment
core. P5–P6 add scale + the temporal loop. P7 makes it operable from your phone.

> **Why `Pc` is its own conditional phase, not part of P7 (audit fix 2026-06-13).** The governance
> model runs the **PM/PO/reviewer on the cloud lane** (governance §1, UX-FLOW §1). If the **P0.5
> capability-floor test mandates a cloud judge**, the **alignment core (P3 charters/monitor, P4
> differently-goaled review) depends on cloud infra** — so building the cloud lane only at the very
> end (old P7) would block or silently degrade P3/P4. `Pc` therefore fires **immediately after P0**,
> *only when P0.5 mandates it*, so judgment infra exists before the alignment core needs it. If P0.5
> shows local 27B judgment is sufficient, **`Pc` is skipped entirely** and everything stays local.

> **These are *build* phases, not the runtime UX.** The user journey (intake → readiness-gate →
> plan presentation → ground/dry-run → execute → escalate) is in **[UX-FLOW.md](UX-FLOW.md)**. Its
> stages are *built* across these phases: the **readiness-gate / clarify-loop** and **plan
> presentation** land with grounding in **P3**; **ground + dry-run** (research + isolated rehearsal
> before any real change) lands with the stop-gates in **P4**; the **idle-wait DAG** with the pool
> scheduler in **P5**.

**P0 capability-floor gate:** the local-model measurement in P0 is a prerequisite, not a nicety
— it decides whether `JUDGE_MODEL` can stay local or must escalate to OpenRouter for judgment
roles (§3.4). Workers stay local regardless. If local judgment is too weak *and* OpenRouter is
not yet wired, judgment defaults to the **Human Operator** carrying more, never to trusting a weak
local monitor.

> **Gate before scale:** do **not** start P5 (multiple roles, fan-out) until P2 (stop) and
> P3 (charters/floor) are solid — the paper says more roles = more misalignment, so the
> brakes must exist before the fleet grows.

---

## 5. Component contracts (v1 sketch — will firm up in build)

### 5.1 `agent-bridge` (service)

- **Internal modules (SRP, §3.1.1):** event-gateway · governance-gate (FSM A) · scheduler (FSM B) ·
  scope-ledger · router/waker · model-router · audit-sink. The **governance-gate is isolated from
  WS/REST plumbing** so P2's safety tests target it deterministically.
- **Stack: Python (FastAPI + Pydantic + Instructor)** — resolves OD-2. Best structured-output/
  validation ecosystem (critical for weak models) + same language as little-coder's control-plane
  wrapper. Persistent WebSocket client to Mattermost + REST client + state DB (Postgres).
  (Diverges from OB1's Deno+Hono convention — accepted; recovery/3-place patterns still apply.)
- **Networks:** `ao-net` (internal) + external `ai-stack_llm-net` (reach the existing `llm-gateway`
  via the `llama-cpp` alias). **No direct internet** — cloud calls go through `llm-gateway-cloud` →
  `ao-egress` only (§3.7). No app-net.
- **Model calls (two lanes, §3.4):** *local* via `http://llama-cpp:8080` (the existing air-gapped
  `llm-gateway`); *cloud* (only where P0 mandates) via `llm-gateway-cloud`. Each call carries the
  **profile's caller-key** (C7) so the gateways' spend-logs attribute agent-org traffic by role
  (e.g. `agent-org-worker-auth`, `agent-org-judge`). Structured calls use GBNF + Instructor.
- **State (Postgres):** channel↔effort↔session map; **worker-instance pool registry**; scope
  ledger; rule/goal version store; gate state (frozen efforts, open CONCERNs). Persisted — a
  frozen effort must **stay** frozen across a bridge bounce (fail-safe).
- **Auth:** bot tokens for agent identities; bridge admin token; keys via env (no secrets in
  files).
- **Core handlers:**
  - `assign_effort(effort)` → acquire a pool instance under the `MAX_CONCURRENT_WORKERS`
    semaphore (honors interactive backoff, §3.6); queue if none free.
  - `on_mention(post)` → resolve target worker → wake/resume its session → deliver context.
  - `on_concern(...)` → freeze effort(+dependents) → post CONCERN to `#mgmt` → await decision (PO for steering, Human Operator for hard-gate).
  - `on_operator_decision(reply)` → parse approve/modify/abort → propagate → unfreeze.
  - `enforce_checkpoint(worker, plan)` → block past a stop-gate until review cleared (P4).
  - `kill_switch()` → freeze entire fleet.

### 5.2 Mattermost config

- Bot account per agent (`@po`, `@pm`, `@worker-*`, `@reviewer-*`); the **Human Operator** = system admin (join any).
- Channel taxonomy: `#mgmt` (Human Operator ⇄ PO ⇄ PM), `#effort-<name>` per work effort, `#incidents`,
  `#suggestions` (pool, §6), DMs for targeted fixes.
- No E2EE. CONCERN/decision rendered via a custom post type or attachment (plugin, P2/P7).

### 5.3 little-coder integration (the worker pool)

- **Pool:** N `(little-coder + open-terminal)` instance pairs (§3.6), N bounded by the inference
  budget. The bridge holds an instance registry and assigns an effort to a free instance.
- **Wake** = resume `--session <thread_id>` on the assigned instance. Context delivered on wake:
  current **goal** (constraints inline), loaded **charter skills** (Agent Skills + founding
  knowledge = floor), **steering** layer, and the **plan doc** (with stop-gates). Worker posts
  results/explanations back through the bridge.
- **Floor is already there:** each instance's `git-proxy` + `lc-egress` + sanitization filter +
  two-plane split enforce hard rules per-instance (TOOLING §2); the bridge adds gate-state +
  scope checks on top.
- No new side-channels — workers speak only via the bridge → Mattermost (bus-only, §5).

### 5.4 Role / model profiles (operator's C4 framing — the role primitive)

Roles are **not** distinct gateway models — they're **profiles** in an agent-org registry, layered
over a gateway model name (OWUI's "custom model" pattern). A profile is the natural home for the
governance charter + scope:

```jsonc
// agent-org model-profile (one per role; a versioned collection)
{
  "profile": "reviewer-ethics",
  "lane": "local",                       // local (llm-gateway) | cloud (llm-gateway-cloud)
  "model": "qwen36-27b",                 // an underlying name the chosen gateway already knows
  "system_prompt_ref": "charters/reviewer-ethics.md",  // = the charter (governance §4.2/§4.4)
  "temperature": 0.2,
  "tool_access": ["read", "grep"],       // = scope (governance §4.1 ledger)
  "caller_key": "agent-org-reviewer-ethics"  // analytics attribution (C7)
}
```

- **Adding a role = adding a profile.** No gateway change unless the *underlying model* is new
  (then it's a gateway-config edit — local rarely, cloud when adding an OpenRouter model).
- **Unifies with governance:** `system_prompt_ref` = the role's charter (floor/steering, §4.2–§4.4);
  `tool_access` = its scope (§4.1); the profile is what the bridge injects on spawn/wake (§4.3).
- **Switching a role local↔cloud is a one-field edit** (`lane` + `model`) — e.g. promote the judge
  to a cloud OpenRouter model if P0 says local judgment is too weak, without touching any worker.
  **Defaults:** PM/monitor, **planner**, reviewer → `cloud` (judgment, incl. plan generation —
  §3.4); workers → `local`. ⚠️ **Tune the local↔cloud boundary empirically** (operator) — stretch
  local as `qwen36-27b` proves capable; the cloud budget caps the rest.
- **Profile changes are versioned/audited** like rules (governance §4.2) — a profile *is* part of
  the floor/steering surface.

> Consider modelling these on OWUI's workspace-model schema for familiarity/reuse (operator's
> reference). v1 can be a simple versioned table/JSON the bridge reads; no new service.

---

## 6. Open decisions

Architecture-level (this plan); the **governance-level** open decisions live in
governance §8 (#1–#11) and are not duplicated here.

- **OD-1 — Compose topology.** Separate `agent-org` project (recommended, matches OB1) vs.
  fold into main ai-stack compose.
- **OD-2 — agent-bridge runtime — RESOLVED: Python (FastAPI + Pydantic + Instructor).** Best
  structured-output ecosystem for weak models + same language as little-coder's wrapper. Diverges
  from OB1's Deno+Hono convention; accepted.
- **OD-3 — Platform lock.** Commit to Mattermost now, or keep a thin adapter so Matrix/Zulio
  remain swappable? Recommend: build the bridge against a small `ChatAdapter` interface so the
  platform isn't load-bearing in our code (cheap insurance; OUTLINE keeps Matrix/Zulip alive).
- **OD-4 — Provenance "last owner" source.** git-blame/last-commit (v1, cheap) → ownership
  ledger (v1.5). Confirm v1 is git-blame.
- **OD-5 — CONCERN UX.** Custom Mattermost plugin (rich card, P7) vs. structured plain posts
  the bridge parses (v1). Recommend plain posts first, plugin later.
- **OD-6 — Cloud LiteLLM + OpenRouter egress — RESOLVED (operator C1 = option B).** The local
  `llm-gateway` stays **air-gapped**; OpenRouter is the **planned extension of LiteLLM built as a
  separate sibling `llm-gateway-cloud`** (its own `master_key`/keys/budgets + spend DB), reachable
  only via `ao-egress` → `openrouter.ai` (no-log/ZDR, open-weight). **Privacy boundary:** only
  governance-level summaries (claim/goal/deviation/options) leave the box — **never raw code or
  secrets**; the bridge builds + logs the egress payload. Analytics from the two lanes are
  **joined later, tagged `local`/`cloud`** (operator). Stand up the cloud gateway only when P0
  shows local judgment insufficient.
- **OD-7 — Coordination glue scope.** How much the bridge does deterministically (routing, format
  repair, handoff state) vs. what little is left to model judgment, given small-model fragility
  (§3.5). Lean maximal-deterministic.
- **OD-8 — Worker concurrency budget (§3.6) — direction set, value to confirm at P0.** Backend:
  **3 parallel @ ~83k** preferred (operator: best long-run headroom for OWUI/OB1/other services);
  **4 @ 64k** as a burst config; **never 32k** (operator-confirmed unmanageable). Aggregate KV ≈
  250–260k @ Q4 on `qwen3.6-27b`. Fleet `MAX_CONCURRENT_WORKERS` = slots − interactive reserve →
  **~1–2 workers at 3-parallel, ~2–3 at 4-parallel**, semaphore-enforced with interactive backoff.
  Confirm the exact reserve + whether the agent-org may request a temporary bump to 4-parallel.
- **OD-9 — Mattermost mobile push privacy (§3.7).** Mobile push uses either Mattermost's public
  **HPNS relay** (leaks notification metadata off-box) or a **self-hosted push proxy**, or neither
  (tailnet, open-app-to-see). For the privacy posture: prefer **self-hosted push proxy** or
  **tailnet-only manual** in v1; decide at P7.
- **OD-10 — Judge model is binary (§3.4) — direction set.** Local judge = **same `qwen36-27b`**
  (no swap thrash) **or** **OpenRouter** (off-GPU) — *never* local 35B. P0 capability-floor decides
  which. Confirm acceptable OpenRouter spend ceiling if it's needed.

---

## 7. Testing

- **P0:** post a mention → assert bridge receives the WS event and posts a reply.
- **P1:** dormant worker session is resumed by a mention; A→B hand-off lands in one thread.
- **P2 (safety-critical):** a CONCERN freezes the effort; no worker action proceeds while
  frozen; Human-Operator `approve/modify/abort` resumes correctly; **bridge restart keeps a frozen effort
  frozen**; kill switch halts all. Negative test: a refusal/objection **cannot** be routed to
  another worker (F3).
- **P3:** hard-rule #4 hook blocks an irreversible action without a cleared decision; a
  steering update reaches a worker on next turn; a floor change requires Human-Operator approval + version bump.
- **P4:** worker halts at a plan checkpoint; explanation surfaces an injected goal-drift case;
  re-ground → refactor → continue; a **same-goal** reviewer is rejected by config (must be
  differently-goaled).
- **P5:** new role *instance* by PM succeeds; new role *type* requires Human Operator (PO proposes); scope ledger denies
  self-granted scope.
- **P6:** every gate event lands in the audit trail + Open Brain; a suggestion enters the pool;
  a pattern produces a *proposal* that does **not** auto-apply.

---

## 8. Conventions honored

- **G1** — never commit/push (or merge to `main`) on the user's behalf without an explicit ask.
- **3-place change** — each new agent-org container (`mattermost`, `mattermost-db`, `agent-bridge`,
  each pooled `little-coder`/`open-terminal` worker instance, and — *only if P7 builds the cloud
  lane* — `llm-gateway-cloud` + `ao-egress`) updates compose **+** recovery scripts **+** stack-map
  together (`/stack-map` checks drift). The **local `llm-gateway` is already live** (registered by
  the LiteLLM work, not agent-org).
- **Stack-map baseline (audit §0).** `workspace-stacks.md` is ✅ reconciled as of 2026-06-13 —
  portal/backup planes **and** the `llm-gateway` flip — so the agent-org rows go onto an accurate
  baseline. Recovery scripts + CLAUDE.md were updated by the LiteLLM work itself.
- **No secrets in files** — bot tokens, admin keys, model keys via env only.
- **Governance-first** — gate + bus-only comms + charters before throughput (governance §9).
- **Operator owns deploy/exposure** — tailnet/mobile exposure and any cloud-model wiring are
  operator steps (🚀), authored here.
- **Reuse, don't reinvent** — skills/hooks for governance, Open Brain for audit/learning,
  little-coder sessions for wake. No parallel subsystems.
