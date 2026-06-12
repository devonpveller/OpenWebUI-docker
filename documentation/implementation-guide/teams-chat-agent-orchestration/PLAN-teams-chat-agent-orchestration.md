# Plan — Teams-style Chat for Agent Orchestration (governed multi-agent org)

**Status:** 📝 DESIGN — **audited against the live compose 2026-06-11** (see §0).
**Owner:** ai-stack
**Branch:** TBD new feature branch; **no `main` merge** without explicit ask (G1).
**Companion docs:**
- [OUTLINE-teams-chat-agent-orchestration.md](OUTLINE-teams-chat-agent-orchestration.md) — platform/tooling selection
- [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) — **the governing spec; this plan implements it**
- [Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md) — source paper (arXiv:2604.10290)

> **Read order:** the governance doc is the spec. This plan is *how we build it*. Where they
> disagree, the governance doc wins and this plan gets corrected.

---

## 0. Workspace audit (2026-06-11)

Audited the PLAN against the live `docker-compose.yml` + `docker-compose.override.yml` +
`config/llama-swap.config.yaml` + OB1 compose. Confirmed, corrected, and newly-found:

**Confirmed accurate**
- External-network pattern: a separate project (OB1 is `name: open-brain`) attaches to
  **`ai-stack_llm-net`** as `external: true`. agent-org will do the same.
- `little-coder` + `open-terminal` + `lc-mcpo` + **`lc-egress`** all present; little-coder calls
  `llama-cpp` directly over `llm-net`, uses the 6 named volumes incl. `little-coder-sessions`.
- llama-swap: `qwen36-27b` at **`N_PARALLEL=2`, ctx 262144 (256k), KV @ `q4_0`** → 2×128k lanes.
  Exactly the §3.6 budget. Single GPU (device 0).
- LiteLLM is **not yet running** (docs-only under `documentation/LiteLLM-Proxy/`).

**Corrected (load-bearing)**
1. **`llm-net` is `internal: true` — NO internet.** The PLAN had the bridge/LiteLLM reaching
   OpenRouter, which is impossible over `llm-net`. **Fix:** OpenRouter egress goes through a
   dedicated **allowlist proxy** (`ao-egress`, mirroring `lc-egress`), and **LiteLLM is the only
   component with that egress path** — making it the literal single egress chokepoint (§3.7).
2. **No local 35B judge — it would cause swap thrash.** The config has a second model
   (`qwen36-35b-a3b`) **but llama-swap keeps only ONE model resident and the stack deliberately
   pins services to the same model "to avoid swap thrash"** (config L12–19). So `JUDGE_MODEL`
   local = **the same `qwen36-27b` as workers** (no swap); if that's too weak, escalate to
   **OpenRouter** (off-GPU) — *not* local 35B. (§3.4 revised.)
3. **LiteLLM is a prerequisite, not part of this build.** Per operator: it'll be delivered by the
   existing `documentation/LiteLLM-Proxy/` plan *before* agent-org. agent-org **consumes** it.

**Newly found (folded in)**
- **Stale stack-map — ✅ REFRESHED 2026-06-11.** The live compose had a whole **Authelia + Caddy +
  Cloudflared auth portal** (`edge-net`/`auth-net`/`notify-net`, `integrity-tripwire`, `portal-*`)
  + many backup sidecars + the 35B model, none of which were in
  `.claude/skills/stack-map/references/workspace-stacks.md`. Now reconciled, so the agent-org
  3-place change adds onto an accurate baseline.
- **Mattermost mobile push is a privacy surface** (HPNS relay vs self-hosted push proxy) — new
  OD-9. The existing portal is an alt exposure path but Team Edition is dropping SSO, so **tailnet
  (tailscale serve) stays the v1 path** (OD-9).
- **Worker-pool container math:** each pooled worker ≈ a full `(little-coder + open-terminal)`
  pair; `lc-egress` (git allowlist) can be **shared** across the pool, but each `open-terminal`
  must stay per-instance (it *is* the isolated workspace). Reinforces small N (§3.6).

---

## 1. Problem / goal

Stand up a self-hosted, mobile-accessible chat platform that doubles as the **coordination
fabric for a fleet of coding agents**, organized as a company:

- **PO (you, human)** ⇄ **PM orchestrator agent** ⇄ **domain workers** (`little-coder`).
- Agents are first-class chat participants; a worker that hits an error outside its scope
  **messages the last owner**, which **wakes** that worker to fix it and **reply in-thread**.
- Every agent exchange is **observable** and the **PO can join any channel/DM** to correct
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
  governance gate** (escalation, freeze, PO-decision).
- Reuse `little-coder`'s existing `--session <id>` per-chat resume — the session id becomes
  the chat **thread id** (memory: `little-coder-per-chat-sessions`).
- Implement governance as **skills + hooks** (reuse `.claude/skills/`), not a parallel system.
- Full **observability + audit** (mirror to Open Brain), PO **kill switch**, fail-safe pause.
- **Local-first model posture.** Everything runs on local `llama-cpp` by default. A larger
  model is used **only where a step is *mandatorily* beyond local capability** (judgment roles —
  see §3.4), and only via **OpenRouter** (privacy-respecting external routing), **never** a
  mainstream consumer frontier service. *(Grounded in the small-vs-frontier analysis.)*

**Non-goals (this plan)**
- Not building a chat app — adopting one.
- No public/cloud exposure of the chat server in v1 (tailnet/LAN only; reuse tailscale).
- No E2EE on agent channels (observability is a safety requirement, governance §5/§7).
- Not throughput-optimizing before the governance shape works (governance §9 build order).
- No autonomous rule self-modification — the learning loop **proposes**, the PO disposes.
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
        ▲  PO joins any channel (mobile)└─────────┬──────────┘
        │                                         │ audit + learning
        │                                         ▼
        └────────────────────────────  Open Brain (audit trail, patterns, suggestion pool)
```

- **agent-bridge** is the new heart: it holds the channel↔effort↔session map, the **scope
  ledger**, the **rule/goal version store**, and the **escalation-gate state machine**
  (freeze / CONCERN / PO-decision / resume). Governance §3, §5.
- **Mattermost** supplies identities (bot accounts per agent), the real-time event bus (wake
  trigger), threads (hand-offs), channels (work efforts), and the mobile apps.
- **little-coder** workers are unchanged in spirit — woken via session resume; they receive
  goals + skills (charters) as injected context; they post via the bridge.

### 3.2 Deployment shape — new compose project

Mirror the **Open Brain** pattern: a **separate compose project** (`agent-org`) rather than
bolting onto the main `ai-stack` compose, so it has its own lifecycle and resource envelope.
It attaches to the main stack's `ai-stack_llm-net` (external) for local-model access, exactly
as OB1 does (CLAUDE.md "Stacks at a glance").

- New containers (project `name: agent-org`): `mattermost`, `mattermost-db` (Postgres),
  `agent-bridge`, **`ao-egress`** (OpenRouter allowlist proxy, mirrors `lc-egress`, §3.7), and a
  **bounded pool of worker instances** — N × `(little-coder + open-terminal)` pairs (§3.6), with a
  **shared** `lc-egress`-style git-allowlist proxy across the pool.
- **Prerequisite (not built here):** **LiteLLM** — delivered by the existing
  `documentation/LiteLLM-Proxy/` plan *before* agent-org; agent-org consumes it as the model
  gateway (§3.4). If LiteLLM isn't up at build time, P0 is blocked on it.
- Attach to external **`ai-stack_llm-net`** (reach `llama-cpp`); reuse existing
  `little-coder`/`open-terminal` images + `tailscale`. **No new inference containers** — workers +
  judge share the existing single-GPU `llama-cpp`/llama-swap backend.
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
| CONCERN + PO decision (§3, §7) | structured Mattermost post type + bridge parser; PO replies from mobile |
| Bus-only comms (§5) | workers have **no** channel but Mattermost; bridge is the only transport |
| Rules-as-skills, floor/steering split (§4.2) | charters = little-coder **Agent Skills** + founding knowledge; floor **already enforced** by existing `git-proxy` + `lc-egress` + sanitization filter + two-plane split (extend, don't reinvent — TOOLING §2) |
| Worker harness + wake | **reuse** little-coder/`pi` + `--session <thread_id>` (TOOLING §2) |
| Learning loop (§6) | **extend** little-coder's existing `meta` + cohort/tier + efficacy-reversion + human gate (already propose-not-dispose) |
| Worker concurrency | **bounded pool** of `(little-coder + open-terminal)` pairs, scheduled by the bridge against shared-inference capacity (§3.6) |
| Goal grounding, constraints-inline (§4.3) | PM composes worker goals; bridge injects on spawn/wake; versioned |
| Plan-stop-gates + explain-intent (§4.5) | checkpoints in the worker's plan doc; bridge blocks past a checkpoint until review cleared |
| Peer review by differently-goaled agents (§4.4) | bridge spawns reviewer(s) with an ethics/whole-picture goal → report to PM, not self-approve |
| Scope ledger / role-expansion authority (§4.1) | bridge ledger; new role *type* = PO-gated, new *instance* = PM |
| Audit trail + learning loop (§5, §6) | bridge logs every event; mirror to Open Brain; suggestion pool; **propose-not-dispose** |
| Kill switch / model-by-role (§3, §7) | bridge global freeze; `WORKER_MODEL` local, `JUDGE_MODEL` local-first → OpenRouter only if mandated (§3.4) |
| Deterministic coordination (small-model analysis) | the **bridge** does routing/wake/handoff/gating; agents barely negotiate — weak models can't coordinate reliably (§3.5) |

### 3.4 Model assignment — local-first, OpenRouter-where-mandatory (governance F7 / §2.1)

**Decided stance (operator):** local-first; a larger model is used **only where mandatory**,
and that larger model is reached via **OpenRouter**, never a mainstream consumer frontier
service. This resolves the prior open decision (was: "cloud Claude on the table") toward a
**local-default, OpenRouter-fallback hybrid**.

Independent config knobs from day one:

| Knob | Default | Notes |
|------|---------|-------|
| `WORKER_MODEL` | **`qwen36-27b` (local)** | execution layer; always local |
| `JUDGE_MODEL` (PM/monitor, reviewer, goal-grounding) | **`qwen36-27b` (same model, local) first**; **OpenRouter** large model **only if the P0 capability-floor test shows 27B judgment is insufficient** | see swap-thrash note below |
| `OPENROUTER_*` | unset until needed | API key + **provider routing pinned to no-log / ZDR**, prefer **open-weight** large models; reached only via `ao-egress` (§3.7) |

> **⚠️ Single-GPU swap-thrash constraint (audit §0).** llama-swap keeps **one model resident at a
> time** and the stack deliberately pins services to the **same** model to avoid swap thrash
> (`config/llama-swap.config.yaml` L12–19). A second model (`qwen36-35b-a3b`) exists locally but
> **cannot co-reside** with the 27B workers — using it for judgment would thrash the GPU. So the
> local judge is **the same `qwen36-27b` as the workers** (zero swap). If 27B-as-judge proves too
> weak (P0), the next step is **OpenRouter (off-GPU)** — *not* local 35B. This makes the model
> decision binary: **all-local same-model, or OpenRouter for judgment.**

**Why a larger model may be *mandatory* (not merely nice):** the small-vs-frontier analysis +
the paper's weak-model data (GPT-5-MINI lost the org capability benefit to coordination
failure; GPT-4.1 held a low ethics floor) show that **judgment roles** — the PM/monitor, the
reviewer, goal-grounding/decomposition — are exactly where small local models fail worst, and
where capability buys *safety*. So *if* the P0 capability-floor test shows our local models
can't reliably do judgment, those roles (and only those) escalate to an OpenRouter large model.
**Workers stay local regardless.**

**Privacy boundary (mandatory before any OpenRouter call — see OD-6):** the judgment layer must
operate on **governance-level summaries** (the claim, the goal, the deviation, candidate
options), **not raw proprietary code or secrets**, so the off-box surface is minimal. The bridge
constructs and logs exactly what leaves. OpenRouter routing is pinned to no-log/ZDR providers.

**Routing mechanism — LiteLLM (prerequisite, audit §0).** LiteLLM is the single
OpenAI-compatible endpoint: `WORKER_MODEL`/`JUDGE_MODEL` (both `qwen36-27b` locally) → llama-swap;
`JUDGE_MODEL` → **OpenRouter** by alias only if P0 mandates. It gives us **fallback chains,
per-role spend caps** (these *are* the cost-tiered continuous-supervision budget, governance §3),
**usage logging** (audit), and — combined with `ao-egress` (§3.7) — the **single egress
chokepoint** for the OpenRouter privacy boundary. **LiteLLM is not built yet**; it is delivered by
the existing `documentation/LiteLLM-Proxy/` plan *before* agent-org (memory: `litellm-proxy-status`),
so it's a **build prerequisite**, not part of this plan. OpenRouter routing is pinned to
**no-log/ZDR**, preferring **open-weight** large models.

**Reliable structured output — constrained decoding.** Worker/judge structured calls use
**GBNF / JSON-schema constrained decoding** (llama.cpp via llama-swap) so a small model *cannot*
emit invalid output, paired with **Instructor/Pydantic** validation in the bridge. This is the
direct fix for the GPT-5-MINI format-failure (TOOLING §3.2).

**Load-bearing principle either way:** the **gate + deterministic floor carry safety, not the
models** (governance §3, §4.2). Because our default is local (weak), the symbolic floor does
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
  (`MAX_CONCURRENT_WORKERS`)** that honors interactive backoff — never a hard pin to slot count.
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

### 3.7 Networks & egress (corrected against live compose, audit §0)

`ai-stack_llm-net` is **`internal: true` — no internet.** OpenRouter (the only external
dependency) must reach the internet through a **controlled chokepoint**, exactly as the stack
already does for every other egress (`lc-egress` → git host, `tor` → search, `notify-net` →
Gmail, `cloudflared` → portal). So:

```
 PO (tailnet) ──tailscale serve──▶ mattermost ─┐
                                                │ ao-net (internal)
   agent-bridge ◀─WebSocket/REST─▶ mattermost   │
   agent-bridge ──▶ LiteLLM ──local──▶ ai-stack_llm-net ──▶ llama-cpp (qwen36-27b)
                       └──OpenRouter──▶ ao-egress ──default──▶ openrouter.ai (allowlisted, no-log/ZDR)
   workers (little-coder pool) ──▶ ai-stack_llm-net ──▶ llama-cpp ; git via shared lc-egress
```

- **`ao-net`** (internal, no internet): bridge ↔ Mattermost ↔ workers ↔ LiteLLM.
- **`ai-stack_llm-net`** (external): LiteLLM + workers reach `llama-cpp`.
- **`ao-egress`** (on `ao-net` + `default`): the **only** component with an internet route, an
  allowlist proxy pinned to `openrouter.ai`. **LiteLLM is the only client of it** → single,
  audited egress point for the privacy boundary (§3.4). Nothing else in agent-org touches the
  internet.
- **Mattermost exposure:** tailnet via **`tailscale serve`** (operator step, mirrors the
  open_notebook :8443 serve pattern) — *not* the Cloudflared/Authelia portal in v1 (Mattermost
  Team Edition is dropping SSO; tailnet is simpler and private). Host-published on `127.0.0.1`.

---

## 4. Phases

Build order honors governance §9: **governance shape before throughput.** The gate, bus-only
comms, and charters land before we scale the fleet or optimize.

| Phase | Title | Output | Risk | Run by |
|-------|-------|--------|------|--------|
| **P0** | Platform spike **+ capability-floor test** | Mattermost + db + one bot up; `litellm` gateway routing local + a **GBNF-constrained** structured call; `agent-bridge` echoes a mention; **measure local models on instruction-following / structured-output / coordination → decide which (if any) judgment roles must use OpenRouter** | low | dev build |
| **P1** | Wake mechanic | bridge resumes a dormant `little-coder` session on @mention; one A→B hand-off in a thread, end-to-end | med | dev build |
| **P2** | **Escalation gate (core safety)** | CONCERN type, freeze/pause-until-cleared, PO-decision parse, **kill switch**, fail-safe default | **high value** | dev build |
| **P3** | Charters + grounding | charters as skills (floor/steering split); **hooks** enforce hard-rule #4; goal-injection on spawn/wake; versioned rule/goal store | med | dev build |
| **P4** | Plan-stop-gates + review | checkpoints in worker plan docs; **explain-intent** at each stop; differently-goaled reviewer → report to PM; self-report cadence | med | dev build |
| **P5** | Dynamic roles + **worker pool** + routing | **worker-instance pool + `MAX_CONCURRENT_WORKERS` scheduler w/ interactive backoff (§3.6)**; scope ledger; role-type (PO) vs instance (PM) authority; "last-owner" provenance (git-blame v1 → ledger); channel taxonomy | med-high | dev build |
| **P6** | Audit + learning loop | full event log → Open Brain; suggestion pool; pattern surfacing; **propose-not-dispose** PO approval flow | med | dev build |
| **P7** | Mobile + hardening | PO mobile flow (join any channel, decide CONCERNs, kill switch); rate caps; model-by-role; tailnet exposure | med | author + operator |

P0–P2 are the spine (prove the loop *and* that we can stop it). P3–P4 are the alignment
core. P5–P6 add scale + the temporal loop. P7 makes it operable from your phone.

**P0 capability-floor gate:** the local-model measurement in P0 is a prerequisite, not a nicety
— it decides whether `JUDGE_MODEL` can stay local or must escalate to OpenRouter for judgment
roles (§3.4). Workers stay local regardless. If local judgment is too weak *and* OpenRouter is
not yet wired, judgment defaults to the **human PO** carrying more, never to trusting a weak
local monitor.

> **Gate before scale:** do **not** start P5 (multiple roles, fan-out) until P2 (stop) and
> P3 (charters/floor) are solid — the paper says more roles = more misalignment, so the
> brakes must exist before the fleet grows.

---

## 5. Component contracts (v1 sketch — will firm up in build)

### 5.1 `agent-bridge` (service)

- **Stack: Python (FastAPI + Pydantic + Instructor)** — resolves OD-2. Best structured-output/
  validation ecosystem (critical for weak models) + same language as little-coder's control-plane
  wrapper. Persistent WebSocket client to Mattermost + REST client + state DB (Postgres).
  (Diverges from OB1's Deno+Hono convention — accepted; recovery/3-place patterns still apply.)
- **Networks:** `ao-net` (internal) + external `ai-stack_llm-net` (reach LiteLLM/llama-swap).
  **No direct internet** — the bridge never calls OpenRouter itself; all model calls go through
  LiteLLM, whose OpenRouter path is the only egress (via `ao-egress`, §3.7). No app-net.
- **Model calls:** all via the `litellm` gateway (`WORKER_MODEL`/`JUDGE_MODEL` aliases, §3.4);
  structured calls use GBNF/JSON-schema constrained decoding + Instructor validation.
- **State (Postgres):** channel↔effort↔session map; **worker-instance pool registry**; scope
  ledger; rule/goal version store; gate state (frozen efforts, open CONCERNs). Persisted — a
  frozen effort must **stay** frozen across a bridge bounce (fail-safe).
- **Auth:** bot tokens for agent identities; bridge admin token; keys via env (no secrets in
  files).
- **Core handlers:**
  - `assign_effort(effort)` → acquire a pool instance under the `MAX_CONCURRENT_WORKERS`
    semaphore (honors interactive backoff, §3.6); queue if none free.
  - `on_mention(post)` → resolve target worker → wake/resume its session → deliver context.
  - `on_concern(...)` → freeze effort(+dependents) → post CONCERN to `#mgmt` → await PO.
  - `on_po_decision(reply)` → parse approve/modify/abort → propagate → unfreeze.
  - `enforce_checkpoint(worker, plan)` → block past a stop-gate until review cleared (P4).
  - `kill_switch()` → freeze entire fleet.

### 5.2 Mattermost config

- Bot account per agent (`@pm`, `@worker-*`, `@reviewer-*`); PO = system admin (join any).
- Channel taxonomy: `#mgmt` (PO⇄PM), `#effort-<name>` per work effort, `#incidents`,
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
- **OD-6 — OpenRouter privacy boundary (when `JUDGE_MODEL` goes external).** Define exactly what
  may leave the box: governance-level summaries (claim / goal / deviation / options) — **not raw
  proprietary code or secrets**. Pin OpenRouter to **no-log / ZDR** providers; prefer **open-weight**
  large models. The bridge builds + logs the egress payload. (Reuse `lc-egress`-style control if
  it fits.) *Resolved direction:* local-first, OpenRouter only where the P0 capability-floor test
  proves local judgment insufficient.
- **OD-7 — Coordination glue scope.** How much the bridge does deterministically (routing, format
  repair, handoff state) vs. what little is left to model judgment, given small-model fragility
  (§3.5). Lean maximal-deterministic.
- **OD-9 — Mattermost mobile push privacy (§3.7).** Mobile push uses either Mattermost's public
  **HPNS relay** (leaks notification metadata off-box) or a **self-hosted push proxy**, or neither
  (tailnet, open-app-to-see). For the privacy posture: prefer **self-hosted push proxy** or
  **tailnet-only manual** in v1; decide at P7.
- **OD-10 — Judge model is binary (§3.4) — direction set.** Local judge = **same `qwen36-27b`**
  (no swap thrash) **or** **OpenRouter** (off-GPU) — *never* local 35B. P0 capability-floor decides
  which. Confirm acceptable OpenRouter spend ceiling if it's needed.
- **OD-8 — Worker concurrency budget (§3.6) — direction set, value to confirm at P0.** Backend:
  **3 parallel @ ~83k** preferred (operator: best long-run headroom for OWUI/OB1/other services);
  **4 @ 64k** as a burst config; **never 32k** (operator-confirmed unmanageable). Aggregate KV ≈
  250–260k @ Q4 on `qwen3.6-27b`. Fleet `MAX_CONCURRENT_WORKERS` = slots − interactive reserve →
  **~1–2 workers at 3-parallel, ~2–3 at 4-parallel**, semaphore-enforced with interactive backoff.
  Confirm the exact reserve + whether the agent-org may request a temporary bump to 4-parallel.

---

## 7. Testing

- **P0:** post a mention → assert bridge receives the WS event and posts a reply.
- **P1:** dormant worker session is resumed by a mention; A→B hand-off lands in one thread.
- **P2 (safety-critical):** a CONCERN freezes the effort; no worker action proceeds while
  frozen; PO `approve/modify/abort` resumes correctly; **bridge restart keeps a frozen effort
  frozen**; kill switch halts all. Negative test: a refusal/objection **cannot** be routed to
  another worker (F3).
- **P3:** hard-rule #4 hook blocks an irreversible action without a cleared decision; a
  steering update reaches a worker on next turn; a floor change requires PO + version bump.
- **P4:** worker halts at a plan checkpoint; explanation surfaces an injected goal-drift case;
  re-ground → refactor → continue; a **same-goal** reviewer is rejected by config (must be
  differently-goaled).
- **P5:** new role *instance* by PM succeeds; new role *type* requires PO; scope ledger denies
  self-granted scope.
- **P6:** every gate event lands in the audit trail + Open Brain; a suggestion enters the pool;
  a pattern produces a *proposal* that does **not** auto-apply.

---

## 8. Conventions honored

- **G1** — never commit/push (or merge to `main`) on the user's behalf without an explicit ask.
- **3-place change** — each new container (`mattermost`, `mattermost-db`, `agent-bridge`,
  `ao-egress`, and each pooled `little-coder`/`open-terminal` worker instance) updates compose **+**
  recovery scripts **+** stack-map together (`/stack-map` checks drift). **LiteLLM** is registered
  by its own (prerequisite) plan, not here.
- **Stack-map baseline — ✅ refreshed 2026-06-11 (audit §0).** `workspace-stacks.md` now reflects
  the live portal/auth slice, backup sidecars, and the `qwen36-35b-a3b` model — so the agent-org
  3-place change adds onto an accurate baseline. (Note: the **recovery scripts** and **CLAUDE.md
  "stacks at a glance"** table may still lag — verify those when registering agent-org.)
- **No secrets in files** — bot tokens, admin keys, model keys via env only.
- **Governance-first** — gate + bus-only comms + charters before throughput (governance §9).
- **Operator owns deploy/exposure** — tailnet/mobile exposure and any cloud-model wiring are
  operator steps (🚀), authored here.
- **Reuse, don't reinvent** — skills/hooks for governance, Open Brain for audit/learning,
  little-coder sessions for wake. No parallel subsystems.
