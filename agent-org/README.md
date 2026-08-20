# agent-org — governed multi-agent chat orchestration

A self-hosted, mobile-accessible **Microsoft-Teams-style chat platform that doubles as the
coordination fabric for a governed fleet of coding agents** (Human Operator → PO → PM →
`little-coder` workers). It implements the design corpus in
[`documentation/implementation-guide/teams-chat-agent-orchestration/`](../documentation/implementation-guide/teams-chat-agent-orchestration/),
grounded in *"AI Organizations are More Effective but Less Aligned than Individual Agents"*
(arXiv:2604.10290). **Safety leads:** the escalation gate + bus-only comms + charters are the
spine; capability rides on top.

> **Precedence:** the governance spec wins. See
> [`SAFETY-AND-WORKFLOW-governance-model.md`](../documentation/implementation-guide/teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md).
> This directory is the *build*; [`IMPLEMENTATION-NOTES.md`](IMPLEMENTATION-NOTES.md) is the
> authoritative "what's built / what's operator-gated" record.

## Layout

```
agent-org/
  docker/docker-compose.yml     # the `agent-org` compose project (P0.1 / R.1)
  docker/.env.example           # env template (no secrets in git)
  config/litellm-cloud.config.yaml   # Pc cloud LiteLLM (CONDITIONAL)
  agent-bridge/                 # the orchestration + governance-gate service (Python/FastAPI)
    app/                        # SRP modules (PLAN §3.1.1)
      modules/governance_gate.py  #   machine A — the escalation gate (safety spine, P2)
      modules/scheduler.py        #   machine B — worker pool + idle-wait FSM (P5)
      modules/{event_gateway,router,scope_ledger,model_router,audit_sink,...}.py
      orchestrator.py             #   thin glue: CONCERN posting, decision parsing, monitor
      main.py                     #   FastAPI control surface + lifespan
    profiles/                   # role = model profile registry (C4 / Pc.3)
    charters/                   # role charters = the profiles' system_prompt_refs (§4)
    floor/                      # hard-rules.md (immutable floor) + stop-gate-enforcement.md
    hooks/pretooluse_floor.py   # the deterministic floor hook (hard-rule #4, P3.3)
    tests/                      # 55 deterministic tests (no infra needed)
```

Charters are also delivered to workers as Agent Skills under
[`.claude/skills/agent-org-{floor,worker,reviewer}/`](../.claude/skills/) (P3.1).

## Architecture in one diagram

```
  Mattermost  ──WS events──▶  agent-bridge  ──spawn/resume──▶  little-coder workers
  (chat+app)  ◀──REST post──   router · GATE (freeze/CONCERN/clear) · scheduler
      ▲ Human Op joins any channel (mobile)          │ audit + learning
      └──────────────────────────────────  Open Brain (audit mirror, patterns, suggestions)
```

- **Local inference** is reached ONLY via the `llama-cpp` alias on the existing air-gapped
  `llm-gateway` (`http://llama-cpp:8080`). Never route around LiteLLM; never probe model health.
- **Cloud inference** (judgment roles, CONDITIONAL) goes through a *separate*
  `llm-gateway-cloud` → `ao-egress` → openrouter.ai. That air-gap split is preserved.

## Bring-up (operator)

Prereqs: the main `ai-stack` is up (so `ai-stack_llm-net` + `llm-gateway`/`llama-cpp` exist).
Bring agent-org up **after** it. The recovery scripts do this automatically (agent-org last).

```bash
cp agent-org/docker/.env.example agent-org/docker/.env   # then fill in the passwords
docker compose -f agent-org/docker/docker-compose.yml up -d   # default plane (P0.1)
```

### P0.2 — Mattermost bot (one-time)
1. Open `http://127.0.0.1:8065`, create the admin account + a team, and an `#mgmt` channel.
2. System Console → Integrations → **Bot Accounts** → create `@pm` (or `@bridge`) →
   copy its access token → put it in `agent-org/docker/.env` as `AO_MATTERMOST_BOT_TOKEN`.
3. `docker compose -f agent-org/docker/docker-compose.yml up -d agent-bridge` to reload.
4. Verify: `curl -fsS http://127.0.0.1:8830/health` → `{"status":"ok"}`.

### P0.3b / P0.5 — capability-floor test (🚩 decision-gate)
Measure `qwen36-27b` on instruction-following / structured-output (GBNF) / coordination via
**bounded real completions** (never a health-probe — C5). Decide the **binary** judge
question: is 27B-as-judge good enough, or must the judge profiles move to the cloud lane?
See `IMPLEMENTATION-NOTES.md` → "P0.5" for the exact procedure.
- **27B judge OK →** stay all-local (profiles ship `lane: local`). **Skip Pc.**
- **27B judge too weak →** build **Pc** (below) and flip judgment profiles to cloud.

### P5 — worker pool (after P2+P3 pass their safety tests)
```bash
# set AO_WORKER_INSTANCE_URLS + AO_MAX_CONCURRENT_WORKERS in .env first
docker compose -f agent-org/docker/docker-compose.yml --profile workers up -d
```
⚠️ Concurrency is a static, conservatively-sized semaphore (no live GPU signal — C6). Default
1 worker at 3-parallel @ ~83k; the GPU is the org-size budget (governance §4.1).

### Pc — cloud lane (CONDITIONAL — only if P0.5 mandates a cloud judge)
1. Set the OpenRouter spend ceiling (Pc.0, operator) + fill `OPENROUTER_API_KEY`,
   `AO_CLOUD_*` in `.env`; pin no-log/ZDR providers in `config/litellm-cloud.config.yaml`.
2. `docker compose -f agent-org/docker/docker-compose.yml --profile cloud up -d`
3. Provision one **virtual key + per-role budget** per judgment profile on the cloud gateway
   (the cost-tier cap): `curl -X POST http://llm-gateway-cloud:4000/key/generate -H
   'Authorization: Bearer $MASTER' -d '{"key_alias":"agent-org-pm","max_budget":...}'`.
4. Flip judgment profiles to cloud (no code change — Pc.3):
   `AO_CLOUD_ENABLED=true` + `curl -X POST http://127.0.0.1:8830/profiles/lane -d
   '{"name":"pm","lane":"cloud"}'` (repeat for po/planner/reviewer-*). **Workers stay local.**

### P7 — mobile + exposure (operator)
Install the Mattermost mobile app; expose the server **tailnet-only** via `tailscale serve`
(no public exposure; no E2EE on agent channels — observability is the safety control). See
[`docs/P7-mobile-and-exposure.md`](docs/P7-mobile-and-exposure.md).

## Operator control surface

The chat bus is the primary surface; the bridge also exposes an HTTP control plane on
`127.0.0.1:8830` (loopback) for tooling + the floor hook:

| Action | From chat (#mgmt) | From HTTP |
|--------|-------------------|-----------|
| Decide a CONCERN | `approve\|modify\|abort <effort_id> [note]` | `POST /decision` |
| Global kill switch | `kill` / `unkill` | `POST /kill-switch {on}` |
| Create an effort/channel | — | `POST /effort {name}` |
| Inspect gate state | — | `GET /state/{effort_id}` |
| Audit replay | — | `GET /audit?effort_id=` |
| Flip a profile lane | — | `POST /profiles/lane {name,lane}` |
| Suggestion pool | post to `#suggestions` | `GET /suggestions` |

## Tests

```bash
cd agent-org/agent-bridge && pip install -e .[test] && pytest -q   # 55 tests, no infra
```

## Conventions honored
- **G1** — never commit/push or merge to `main` without an explicit ask.
- **3-place change** — every container is in compose **+** `scripts/emergency-recovery.ps1`/`.bat`
  **+** `.claude/skills/stack-map/references/workspace-stacks.md` (run `/stack-map`).
- **No secrets in files** — bot tokens / DB passwords / model keys via env only.
- **Reuse, don't reinvent** — little-coder for workers, its floor (git-proxy/lc-egress) for
  enforcement, Open Brain for audit/learning, the existing `llm-gateway` for local inference.
