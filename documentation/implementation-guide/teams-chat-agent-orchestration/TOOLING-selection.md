# Tooling Selection — what to borrow to build this

**Status:** tooling analysis / build input (2026-06-10)
**Reads against:** [PLAN](PLAN-teams-chat-agent-orchestration.md) · [SAFETY-AND-WORKFLOW-governance-model.md](SAFETY-AND-WORKFLOW-governance-model.md) · the three ANALYSIS docs · this workspace's stack-map + [little-coder design](../../little-coder/Self-improving-little-coder-design.md)

---

## 0. TL;DR — we are not assembling from scratch

The biggest finding: **most of the worker-side substrate already exists in this workspace.**
We should *borrow inward* before reaching for anything new.

> **Already here (reuse):** `little-coder` on the **`pi` framework** is the worker harness; it
> already uses the **Anthropic Agent Skills format** (= our charters), **`--session`** (= our
> wake mechanic), and an **OWUI Pipe + slash-command** operator surface. The **deterministic
> floor is built** — `git-proxy` + `lc-egress` + the **sanitization filter** + the two-plane
> split. A **learning loop exists** — `meta` + cohort/tier ladder + efficacy reversion + human
> gate, already *propose-not-dispose*. **Journals** are the audit trail. **Open Brain** is durable
> provenance + a suggestion-worker. **llama-swap (`qwen3.6-27b` / `-nothink`)** is `WORKER_MODEL`.
>
> **Genuinely new (build/adopt):** **Mattermost** (chat + mobile), the **`agent-bridge`**
> (orchestration + the governance gate), a **model gateway** (LiteLLM) for local + OpenRouter
> routing, and **GBNF/JSON-schema constrained decoding** for small-model reliability.
>
> **The real architectural lift (not the chat layer):** little-coder is **single-task FIFO,
> one workspace, human-attach read-only** (design §12.4). Our org needs **concurrent workers.**
> See §5 — this is the decision that most shapes the build.
>
> **Do NOT adopt:** a heavyweight LLM-agent-orchestration framework (CrewAI / AutoGen / Swarm /
> agent-mode LangGraph). They put **coordination inside the models** — exactly the small-model
> failure (GPT-5-MINI) we rejected. Our coordination is **deterministic, in the bridge** (§4).

---

## 1. Component → tool → reuse-or-new

| Layer | Best tool | Reuse / New | Why |
|------|-----------|-------------|-----|
| Chat platform + mobile | **Mattermost Team Edition** | **New** | OSS, free, no user cap; native iOS/Android; Bot accounts + WebSocket + slash commands + plugins (OUTLINE §3) |
| Worker harness | **`little-coder` / `pi`** | **Reuse** | Already the agent; `--session` wake; Agent Skills format = charters |
| Orchestration + gate | **`agent-bridge`** (custom) | **New** | The governance gate/state-machine; coordination is deterministic, not a model-agent framework (§4) |
| Deterministic floor (enforcement) | **`git-proxy` + `lc-egress` + sanitization filter + two-plane** | **Reuse/extend** | This *is* §4.2's "hook for enforcement," already built and fail-closed |
| Charters / steering | **Agent Skills (`SKILL.md`) + `--append-system-prompt` founding knowledge** | **Reuse** | little-coder already loads skills by tag/embedding/budget; floor = founding knowledge |
| Structured-output reliability | **GBNF / JSON-schema constrained decoding** (llama.cpp via llama-swap) + **Instructor**/**Pydantic-AI** (or **Zod**) | **New (mostly free)** | Token-level constraint so weak models *can't* emit invalid JSON — the GPT-5-MINI fix |
| Model gateway (local + OpenRouter) | **LiteLLM proxy** | **New** | One OpenAI-compatible endpoint; `WORKER_MODEL`→local, `JUDGE_MODEL`→OpenRouter; fallback, spend caps, single egress chokepoint |
| Local inference | **llama-swap / llama.cpp (`qwen3.6-27b`)** | **Reuse** | Existing `WORKER_MODEL`; serves GBNF natively |
| Judgment model (where mandatory) | **OpenRouter** (no-log/ZDR providers, open-weight) | **New** | Per the decided local-first stance; reached *through* LiteLLM |
| Audit trail | **Journals (`*.jsonl` + `audit.jsonl`)** + **Open Brain** mirror | **Reuse** | Append-only, schema-versioned, fail-closed; OB = durable/queryable provenance |
| Learning loop | **`meta` + cohort/tier + efficacy reversion + human gate** | **Reuse/extend** | Already propose-not-dispose (§6); extend, don't rebuild |
| Suggestion pool | **`openbrain-suggestion-worker`** pattern | **Reuse** | Cross-thread suggestion machinery already exists |
| Tool/permission gating | **mcpo / lc-mcpo** MCP→OpenAPI edge | **Reuse** | Existing keyed chokepoint pattern; optional **OPA/Rego** for declarative scope policy later |
| Scope ledger | **Postgres table** (v1) | **New (light)** | Authenticated delegation; OPA only if we want declarative policy |
| Provenance "last owner" | **git blame / git log** (v1) → ownership ledger | **Reuse** | Already permitted in git-proxy whitelist |
| Operator surface | **OWUI Pipe + slash commands** pattern → **Mattermost** equivalent | **Reuse pattern** | little-coder §12.6 already does `/approve`/`/pending`/`/confirm` |
| Mobile/tailnet access | **Tailscale** | **Reuse** | Already in stack; PO joins from phone |
| Deploy | **Docker Compose** project `agent-org` + recovery scripts + stack-map | **Reuse pattern** | 3-place-change convention |

---

## 2. Reuse first — what the workspace already gives us

These map our design onto things that **already run**, so the build shrinks to glue + the new layers.

- **Worker = `little-coder` (`pi`).** Per design §3.1, the agent is Node/`pi`; a Python wrapper
  handles journals/config/sanitization/git-proxy/MCP edge. **`--session <id>`** is exactly our
  wake mechanic (thread id → session). No new worker harness needed.
- **Charters = Agent Skills + founding knowledge.** little-coder already adopts the **Anthropic
  Agent Skills format** (`SKILL.md`, progressive disclosure) and loads **founding knowledge** via
  `--append-system-prompt` (design §3.7). Our floor/steering split (§4.2) lands as: founding
  knowledge + non-overridable skills = floor; per-session injected context = steering.
- **The floor already exists and is fail-closed.** `git-proxy` (whitelist/blocklist, read-only
  `.git/config`), `lc-egress` (tinyproxy default-deny), the **one-filter-all-outbound
  sanitization** (aborts on failure), and the **control/workspace two-plane split** (design §3.3,
  §3.4, §10.2). This is our §4.2 "hook for enforcement," already production-grade — we **extend**
  it (add gate-state + scope checks), not invent it.
- **Learning loop already exists — and it's already our shape.** `meta` + cohort/tier ladder +
  efficacy reversion + the **human gate** (design §5, §8, §12.6) is *propose, operator-disposes*
  with provenance and anti-self-poisoning controls (§10.4). Our §6 learning loop should **extend
  this**, not run a parallel one.
- **Audit = journals.** Append-only, fsync'd, schema-versioned, with a separate `audit.jsonl`
  for operator/lifecycle actions (design §4). Mirror the safety-critical subset to **Open Brain**.
- **Operator surface pattern exists.** OWUI Pipe + slash commands (`/approve`, `/pending`,
  `/confirm`, `/project`) with privilege separation (design §12.6). We mirror this onto Mattermost
  (the CONCERN card + PO decision = the same approve/reject shape).
- **`WORKER_MODEL` = `qwen3.6-27b` on llama-swap** (design §3.5). The P0 capability-floor test
  measures *these specific models* — they're the concrete subject of the small-vs-frontier analysis.

---

## 3. New tools to adopt (best-in-class, fit our constraints)

### 3.1 Mattermost (Team Edition) — chat + mobile
Free, open-source, **no user cap**; native iOS/Android; **Bot accounts**, **WebSocket API**,
slash commands, plugin framework. The most mature self-hosted option in 2026. (SSO is being
dropped from Team Edition — irrelevant; PO is admin, access is tailnet-gated.)

### 3.2 GBNF / JSON-schema constrained decoding — the small-model reliability win
**The single highest-leverage new technique.** Instead of hoping a 27B model emits valid JSON,
**constrain the decode to a grammar** so it *cannot* emit anything else — eliminating the
validate-retry loop at the token level. llama.cpp (which llama-swap fronts) supports **GBNF**
and JSON-schema→grammar natively. This is the direct structural fix for the GPT-5-MINI
"couldn't follow the format" failure that the small-vs-frontier analysis flagged.
- App-layer pairing: **Instructor** or **Pydantic-AI** (Python) for schema + bounded
  retry/repair; **Zod** if the bridge is Deno/TS. (Constrained decoding does the heavy lifting;
  these add typed validation + ergonomics.)

### 3.3 LiteLLM (proxy) — two lanes (UPDATED 2026-06-13: local gateway is LIVE)
LiteLLM is the **OpenAI-compatible** front door. **Local lane is already deployed** as the
air-gapped `llm-gateway` (`documentation/LiteLLM-Proxy/`, memory `litellm-proxy-status`): callers
hit `http://llama-cpp:8080` → gateway → `llama-cpp-upstream`; permissive, **no egress**, analytics
only. agent-org consumes it as-is. **Cloud lane is the planned extension** — a *separate*
`llm-gateway-cloud` (only if P0 mandates) routing `JUDGE_MODEL`→**OpenRouter**, where the **`master_key`
+ per-role budgets** (the *cost-tiered supervision* cap, governance §3) and the **single egress
chokepoint** (`ao-egress`, no-log/ZDR, open-weight) live — keeping the local gateway air-gapped
(PLAN §3.4 / OD-6).

### 3.4 `agent-bridge` — runtime recommendation (resolves PLAN OD-2)
**Recommend Python (FastAPI + Pydantic + Instructor).** Rationale: the **structured-output /
validation ecosystem is strongest in Python** (critical for weak models), and it's the **same
language as little-coder's control-plane wrapper** (tight wake/session integration). Tradeoff:
diverges from OB1's Deno+Hono service convention — accepted; the 3-place-change + recovery
patterns still apply. (Deno+Hono remains viable if service-convention consistency is valued more.)
- **Gate-state durability** (a frozen effort must *stay* frozen across a bridge restart): persist
  to **Postgres** (or SQLite v1). **Temporal** (durable workflow engine) is the "if we want
  bulletproof pause/resume workflows" option — likely overkill for v1; note and defer.
- **Mattermost client:** a maintained Python driver + raw WebSocket consumer.

### 3.5 Scope ledger / policy
**Postgres table v1** (authenticated delegation = who-may-touch-what, logged). **OPA/Rego** is
the upgrade if we want declarative, auditable policy ("NL ethics → machine-readable access
control," the scholarly point) — defer unless the policy surface grows.

---

## 4. What NOT to adopt — and why

- **Heavyweight LLM-agent-orchestration frameworks — CrewAI, AutoGen/AG2, OpenAI Swarm, LangGraph
  used as an agent swarm.** They put **coordination inside the models** (autonomous agent
  negotiation), which is precisely the small-model failure mode (GPT-5-MINI lost the org
  capability to coordination overhead). **Our coordination is deterministic, in the bridge** (§4,
  PLAN §3.5). *(LangGraph's deterministic state-graph could be borrowed as a library for the gate
  state machine — but a hand-rolled persisted state machine is lighter and clearer for v1.)*
- **A second chat platform / building our own.** Mattermost (or Matrix/Zulip) is solved.
- **Any mainstream consumer frontier LLM API.** Decided stance: OpenRouter only, where mandatory.
- **Per-agent end-to-end-encrypted chat.** Breaks the observability that *is* our safety control.
- **`meta` authoring its own rules / auto-deploy.** little-coder already rejects this (design §15);
  it matches our propose-not-dispose boundary. Keep it.

---

## 5. The real architectural lift: concurrency (single-worker → fleet)

little-coder today is **one task at a time, one `open-terminal`, FIFO across triggers,
human-attach read-only** (design §12.4; memory: `little-coder-test-task-collisions`). Our org
assumes **multiple concurrent domain workers** that hand off to each other. This is the gap that
most shapes the build — bigger than the chat layer.

**Options:**
- **A. Pool of worker instances (recommended).** Run N `(little-coder + open-terminal)` pairs —
  one per concurrent domain worker — and have the `agent-bridge` assign efforts → instances.
  Preserves the **per-plane containment** (each worker keeps its own network-isolated workspace,
  git-proxy, egress allowlist — the safety surface is intact). **Cost:** container + volume
  multiplication; GPU/`n_parallel` contention on llama-swap (design §3.5 notes `n_parallel=2`,
  interactive-wins) — so concurrency is bounded by inference capacity, which *reinforces* "keep
  the org small" (governance §4.1) and the per-task "org vs. single agent?" question.
- **B. Stay single-worker, serialize.** Defeats the cross-effort "wake the last owner" scenario
  and the fleet concept. Only viable as a v0 stepping-stone.
- **C. Per-effort ephemeral workers.** little-coder's design §15 explicitly **rejects
  per-session containers as the default substrate** (infra burden). Don't fight that.

**Recommendation:** **A**, sized by inference capacity. This is a **PLAN change** — "workers" =
a *bounded pool* of little-coder instances, and the bridge owns instance assignment + the
inference-capacity budget. *(New open decision — see §7.)*

---

## 6. The shortlist (if you build P0 tomorrow)

| Need | Pick |
|------|------|
| Chat + mobile | **Mattermost Team Edition** (Docker) |
| Worker | **little-coder / `pi`** (existing) via `--session` |
| Orchestration + gate | **`agent-bridge`** — **Python + FastAPI + Pydantic + Instructor** |
| Model routing | **LiteLLM** → local llama-swap (`WORKER_MODEL`) + OpenRouter (`JUDGE_MODEL`, no-log/ZDR, open-weight) |
| Reliable structured output | **llama.cpp GBNF / JSON-schema constrained decoding** + Instructor |
| Floor / enforcement | **existing** git-proxy + lc-egress + sanitization + two-plane (extend) |
| Charters | **Agent Skills format** + founding knowledge (existing) |
| Audit + learning | **journals + `meta`/cohort/tier** (existing) + **Open Brain** mirror |
| Gate-state store / scope ledger | **Postgres** (Temporal/OPA deferred) |
| Mobile/tailnet | **Tailscale** (existing) |

All MIT/Apache-class OSS where new (Mattermost TE, LiteLLM, FastAPI, Pydantic, Instructor,
llama.cpp, Postgres, Tailscale) — **verify current licenses at adopt time**; `pi`/little-coder
upstream license to confirm before any redistribution.

---

## 7. Net + the decisions this surfaces

- **The build is smaller than it looked** on the worker side (reuse little-coder's harness,
  floor, learning loop, audit, sessions) and **focused** on three new things: **Mattermost**, the
  **`agent-bridge` + gate**, and the **LiteLLM/local+OpenRouter + GBNF** model layer.
- **The dominant new decision is concurrency (§5)** — a pool of little-coder instances, bounded by
  inference capacity. This should become a PLAN update + a new open decision (governance §8):
  *"How many concurrent workers does our GPU/`n_parallel` budget actually support, and how does the
  bridge schedule efforts against it?"* — which loops straight back to the capability-floor test
  (P0) and "org vs. single agent?".
- **P0 now has a concrete shape:** Mattermost + bridge skeleton + **one** little-coder instance
  wired via `--session` + LiteLLM routing + a **GBNF-constrained** judge/structured call + the
  **capability-floor test on `qwen3.6-27b`** (does local judgment hold, or must `JUDGE_MODEL` go
  to OpenRouter?).

**Recommended next:** fold §5 (concurrency = bounded pool) and the LiteLLM/runtime picks into the
PLAN, and add the concurrency-budget open decision. Then P0 is fully specified.
