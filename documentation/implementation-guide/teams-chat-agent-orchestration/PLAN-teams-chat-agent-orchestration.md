# Plan — Teams-style Chat for Agent Orchestration (governed multi-agent org)

**Status:** 📝 DESIGN (initial — expect change during build)
**Owner:** ai-stack
**Branch:** TBD new feature branch; **no `main` merge** without explicit ask (G1).
**Companion docs:**
- [OUTLINE-teams-chat-agent-orchestration.md](OUTLINE-teams-chat-agent-orchestration.md) — platform/tooling selection
- [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) — **the governing spec; this plan implements it**
- [Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md](Ai-Organizations-Are-More-Effective-But-Less-Aligned-Than-Individual-Agents.md) — source paper (arXiv:2604.10290)

> **Read order:** the governance doc is the spec. This plan is *how we build it*. Where they
> disagree, the governance doc wins and this plan gets corrected.

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

**Non-goals (this plan)**
- Not building a chat app — adopting one.
- No public/cloud exposure of the chat server in v1 (tailnet/LAN only; reuse tailscale).
- No E2EE on agent channels (observability is a safety requirement, governance §5/§7).
- Not throughput-optimizing before the governance shape works (governance §9 build order).
- No autonomous rule self-modification — the learning loop **proposes**, the PO disposes.

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

- New containers: `mattermost`, `mattermost-db` (Postgres), `agent-bridge`.
  (Reuse existing `little-coder` + `tailscale`; no new inference containers.)
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
| Rules-as-skills, floor/steering split (§4.2) | charters/hard-rules = always-on skills; **hooks** enforce the floor (irreversible-action gate) |
| Goal grounding, constraints-inline (§4.3) | PM composes worker goals; bridge injects on spawn/wake; versioned |
| Plan-stop-gates + explain-intent (§4.5) | checkpoints in the worker's plan doc; bridge blocks past a checkpoint until review cleared |
| Peer review by differently-goaled agents (§4.4) | bridge spawns reviewer(s) with an ethics/whole-picture goal → report to PM, not self-approve |
| Scope ledger / role-expansion authority (§4.1) | bridge ledger; new role *type* = PO-gated, new *instance* = PM |
| Audit trail + learning loop (§5, §6) | bridge logs every event; mirror to Open Brain; suggestion pool; **propose-not-dispose** |
| Kill switch / model-by-role (§3, §7) | bridge global freeze; PM/monitor model configurable (cloud Claude vs local) |

### 3.4 Model assignment (governance F7 / §2.1)

Config knob from day one: `PM_MODEL` and `WORKER_MODEL` are independent. Default workers →
local `llama-cpp`; **PM/monitor → strongest aligned model we can afford** (metered cloud
Claude via the existing openbrain cloud gateway is on the table). The monitor is the
highest-leverage alignment point, and local workers sit on the worse end of the paper's
model-dependence finding — so the gate, not the models, carries safety. (Open decision §8 #6.)

---

## 4. Phases

Build order honors governance §9: **governance shape before throughput.** The gate, bus-only
comms, and charters land before we scale the fleet or optimize.

| Phase | Title | Output | Risk | Run by |
|-------|-------|--------|------|--------|
| **P0** | Platform spike | Mattermost + db + one bot up; `agent-bridge` echoes a mention (post→event→reply) | low | dev build |
| **P1** | Wake mechanic | bridge resumes a dormant `little-coder` session on @mention; one A→B hand-off in a thread, end-to-end | med | dev build |
| **P2** | **Escalation gate (core safety)** | CONCERN type, freeze/pause-until-cleared, PO-decision parse, **kill switch**, fail-safe default | **high value** | dev build |
| **P3** | Charters + grounding | charters as skills (floor/steering split); **hooks** enforce hard-rule #4; goal-injection on spawn/wake; versioned rule/goal store | med | dev build |
| **P4** | Plan-stop-gates + review | checkpoints in worker plan docs; **explain-intent** at each stop; differently-goaled reviewer → report to PM; self-report cadence | med | dev build |
| **P5** | Dynamic roles + routing | scope ledger; role-type (PO) vs instance (PM) authority; "last-owner" provenance (git-blame v1 → ledger); channel taxonomy | med | dev build |
| **P6** | Audit + learning loop | full event log → Open Brain; suggestion pool; pattern surfacing; **propose-not-dispose** PO approval flow | med | dev build |
| **P7** | Mobile + hardening | PO mobile flow (join any channel, decide CONCERNs, kill switch); rate caps; model-by-role; tailnet exposure | med | author + operator |

P0–P2 are the spine (prove the loop *and* that we can stop it). P3–P4 are the alignment
core. P5–P6 add scale + the temporal loop. P7 makes it operable from your phone.

> **Gate before scale:** do **not** start P5 (multiple roles, fan-out) until P2 (stop) and
> P3 (charters/floor) are solid — the paper says more roles = more misalignment, so the
> brakes must exist before the fleet grows.

---

## 5. Component contracts (v1 sketch — will firm up in build)

### 5.1 `agent-bridge` (service)

- **Stack:** runtime TBD (Deno+Hono to match OB1 services, or Python to match little-coder
  tooling — OD-2). Persistent WebSocket client to Mattermost + REST client + small state DB.
- **Networks:** `agent-org` internal + external `ai-stack_llm-net` (local models) + reach to
  Mattermost. No browser/app-net.
- **State:** channel↔effort↔session map; scope ledger; rule/goal version store; gate state
  (which efforts are frozen, open CONCERNs). Persisted (survives restart) — a frozen effort
  must **stay** frozen across a bridge bounce (fail-safe).
- **Auth:** bot tokens for agent identities; bridge admin token; keys via env (no secrets in
  files).
- **Core handlers:**
  - `on_mention(post)` → resolve target worker → wake/resume session → deliver context.
  - `on_concern(...)` → freeze effort(+dependents) → post CONCERN to `#mgmt` → await PO.
  - `on_po_decision(reply)` → parse approve/modify/abort → propagate → unfreeze.
  - `enforce_checkpoint(worker, plan)` → block past a stop-gate until review cleared (P4).
  - `kill_switch()` → freeze entire fleet.

### 5.2 Mattermost config

- Bot account per agent (`@pm`, `@worker-*`, `@reviewer-*`); PO = system admin (join any).
- Channel taxonomy: `#mgmt` (PO⇄PM), `#effort-<name>` per work effort, `#incidents`,
  `#suggestions` (pool, §6), DMs for targeted fixes.
- No E2EE. CONCERN/decision rendered via a custom post type or attachment (plugin, P2/P7).

### 5.3 little-coder integration

- Wake = resume `--session <thread_id>`. Context delivered on wake: current **goal**
  (constraints inline), loaded **charter skills** (floor), **steering** layer, and the
  **plan doc** (with stop-gates). Worker posts results/explanations back through the bridge.
- No new side-channels — worker speaks only via the bridge → Mattermost (bus-only, §5).

---

## 6. Open decisions

Architecture-level (this plan); the **governance-level** open decisions live in
governance §8 (#1–#11) and are not duplicated here.

- **OD-1 — Compose topology.** Separate `agent-org` project (recommended, matches OB1) vs.
  fold into main ai-stack compose.
- **OD-2 — agent-bridge runtime.** Deno+Hono (matches OB1 services) vs. Python (matches
  little-coder). Lean Deno for service consistency; revisit if tight little-coder coupling
  wins.
- **OD-3 — Platform lock.** Commit to Mattermost now, or keep a thin adapter so Matrix/Zulio
  remain swappable? Recommend: build the bridge against a small `ChatAdapter` interface so the
  platform isn't load-bearing in our code (cheap insurance; OUTLINE keeps Matrix/Zulip alive).
- **OD-4 — Provenance "last owner" source.** git-blame/last-commit (v1, cheap) → ownership
  ledger (v1.5). Confirm v1 is git-blame.
- **OD-5 — CONCERN UX.** Custom Mattermost plugin (rich card, P7) vs. structured plain posts
  the bridge parses (v1). Recommend plain posts first, plugin later.

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
- **3-place change** — each new container (`mattermost`, `mattermost-db`, `agent-bridge`)
  updates compose **+** recovery scripts **+** stack-map together (`/stack-map` checks drift).
- **No secrets in files** — bot tokens, admin keys, model keys via env only.
- **Governance-first** — gate + bus-only comms + charters before throughput (governance §9).
- **Operator owns deploy/exposure** — tailnet/mobile exposure and any cloud-model wiring are
  operator steps (🚀), authored here.
- **Reuse, don't reinvent** — skills/hooks for governance, Open Brain for audit/learning,
  little-coder sessions for wake. No parallel subsystems.
