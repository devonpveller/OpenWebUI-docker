# agent-org — governed multi-agent chat orchestration

A self-hosted, mobile-accessible **Microsoft-Teams-style chat platform that doubles as the
coordination fabric for a governed fleet of coding agents** (Human Operator → PO → PM →
`little-coder` workers). It implements the design corpus in
[`../documentation-plans-ai-stack/implementation-guide/teams-chat-agent-orchestration/`](../../documentation-plans-ai-stack/implementation-guide/teams-chat-agent-orchestration/),
grounded in *"AI Organizations are More Effective but Less Aligned than Individual Agents"*
(arXiv:2604.10290). **Safety leads:** the escalation gate + bus-only comms + charters are the
spine; capability rides on top.

> **Precedence:** the governance spec wins. See
> [`SAFETY-AND-WORKFLOW-governance-model.md`](../../documentation-plans-ai-stack/implementation-guide/teams-chat-agent-orchestration/SAFETY-AND-WORKFLOW-governance-model.md).
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
    tests/                      # 865 test functions (AST count, 2026-09-19)
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

## Posture: local-first, cloud-capable

The stack-wide posture is stack-layers **D14** — private, local-first,
cloud-capable, with every cloud-capable component present and inert; the full
cross-plane table is in the root [`README.md`](../README.md). This plane holds
**four** of them, and a default `docker compose -f agent-org/docker/docker-compose.yml
up -d` starts none, because `docker/.env.example` sets no `COMPOSE_PROFILES`
line at all and both slices are profile-gated.

| Component | What it does when off | What turns it on | Where it egresses |
|---|---|---|---|
| **`llm-gateway-cloud` + `llm-gateway-cloud-db`** (a second LiteLLM, config [`config/litellm-cloud.config.yaml`](config/litellm-cloud.config.yaml)) | `profiles: ["cloud"]` — it does not render, and `agent-bridge` keeps every role on the local lane because `AO_CLOUD_ENABLED` defaults to `false`. | The `cloud` profile, plus `OPENROUTER_API_KEY`, `AO_CLOUD_DB_PASSWORD` and `AO_CLOUD_MASTER_KEY` in `docker/.env`; then `AO_CLOUD_ENABLED=true` and a `POST /profiles/lane` per judgment role (Pc.3, below). | It has no internet leg: `ao-net` plus `ao-cloud-egress-net` (`internal: true`), with `HTTP_PROXY`/`HTTPS_PROXY` set to `http://ao-egress:8888`. |
| **`ao-egress`** | `profiles: ["cloud"]`. | Same profile. | The one dual-homed container in the cloud lane: `ao-cloud-egress-net` plus the project's `default` bridge. **Read the allowlist warning below before relying on it.** |
| **`ao-git-egress`**, with the `ao-worker-*` / `ao-ot-*` pool that is proxied through it | `profiles: ["workers"]` — neither the proxy nor the pool renders. | The `workers` profile. | A default-deny tinyproxy (`FilterDefaultDeny Yes`) whose filter is `/egress/egress-allowlist.txt` on the shared `ao-egress-config` volume. [`docker/egress/egress-reload.sh`](docker/egress/egress-reload.sh) seeds it with `github.com` + `githubusercontent.com` and SIGHUPs tinyproxy whenever `agent-bridge` rewrites it, which is how `/project add` and `/egress allow` change worker scope from chat with no rebuild. |
| **The GitHub App** (the capability plane's root of trust) | `Settings.github_app_enabled` in [`agent-bridge/app/config.py`](agent-bridge/app/config.py) is false unless `github_app_id` is set **and** the private key file is readable, so every capability call is gated off and the bridge otherwise runs normally. | `AO_GITHUB_APP_ID` + `AO_GITHUB_APP_OWNER` in `docker/.env` (they are not in `.env.example` — this plane omits names the compose file gives a `${VAR:-}` default) and a `.pem` at `agent-bridge/secrets/github-app-key.pem`, which is gitignored and mounted read-only. | `https://api.github.com` **directly from `ao-net`**, which is an ordinary bridge — this path does not go through `ao-egress`. |

**`ao-egress`'s allowlist does not match its documentation, and it fails
closed.** `AO_EGRESS_ALLOWLIST` (default `openrouter.ai`) is set on that service
in `docker/docker-compose.yml`, but `ao-egress` builds from
`../../little-coder/docker/Dockerfile.egress` and runs that image's `CMD`
unchanged: tinyproxy against `/etc/tinyproxy/egress-allowlist.txt`, COPYd in at
build time from `little-coder/docker/egress-allowlist.txt`. Nothing in that
image reads `EGRESS_ALLOWLIST`, so the effective allowlist is the baked
`github.com` / `githubusercontent.com` pair and `openrouter.ai` would be
DENIED. `ao-git-egress` is unaffected — it overrides both the conf file and the
command. Turning the cloud lane on therefore needs the allowlist wired the way
`ao-git-egress` wires it, or the pattern added to the image. Recorded in
[`../documentation/notes/stack-layers-sl-docs-posture-findings.md`](../documentation/notes/stack-layers-sl-docs-posture-findings.md).

**What is NOT in this plane.** `agent-bridge` reaches local inference through the
`llama-cpp` alias on the main stack's `llm-gateway`, which sits on two
`internal: true` networks and has no egress of its own; the repo-wide rule is
that its cloud model group stays listed-but-unreachable
(`inference/config/litellm/model_list/cloud.openrouter.yaml`), and
`config/litellm-cloud.config.yaml` says not to add OpenRouter to it. Two
gateways, two lanes. `ao-net` is a plain bridge so host port publishing works;
"no cloud" there is enforced at the application layer — no cloud credential is
set on any default service.

## Bring-up (operator)

Prereqs: the main `ai-stack` is up (so `ai-stack_llm-net` + `llm-gateway`/`llama-cpp` exist).
Bring agent-org up **after** it. The recovery scripts do this automatically (agent-org last).

```bash
cp agent-org/docker/.env.example agent-org/docker/.env   # then fill in the passwords
docker compose -f agent-org/docker/docker-compose.yml up -d   # default plane (P0.1)
```

### Environment — one file, `agent-org/docker/.env`

Compose loads it NATIVELY from the project directory, so nothing passes
`--env-file` and your shell's cwd does not matter. **Every variable a service in this
plane needs SET is declared there**, and `docker/.env.example` is the template to copy.
Not every interpolated name is an assignment in it: the ones the compose file gives a
`${VAR:-default}` are deliberately absent, and `AO_OT1_IMAGE` / `AO_OT2_IMAGE` - the
env-template hot-swap selectors for the two open-terminal sidecars - are documented
there as commented optional overrides rather than as values you must fill in.

Since 2026-09-19 that includes the worker pool. `ao-worker-1` / `ao-worker-2` used
to carry `env_file: ../../.env`, a wildcard grant of the whole ROOT `.env`; they
have no `env_file:` key at all now. The two variables the `little-coder:local`
image reads that the wildcard was carrying are named in each service's
`environment:` block and interpolated from this plane's own file:

| name | why the pool needs it |
|---|---|
| `LC_DEPLOY_TOKEN` | the clone path's global fallback deploy token for private work repos (a per-project `LC_<ORG>_TOKEN` or `AO_TOKEN_X` overrides it) |
| `LC_LLAMA_API_KEY` | the name little-coder itself reads for its inference bearer (`config.py` `inference.api_key_env`). The `LLAMACPP_API_KEY` the compose file also sets is a different container name, read by `pi` — it does not cover this one |

`coder/.env` declares the same two names for the MAIN stack's little-coder, with
its own values: D10 says a value two planes read is declared in EACH, not shared.

After changing either value, **recreate the pool** - a running worker keeps the
environment it started with:

```bash
docker compose -f agent-org/docker/docker-compose.yml --profile workers up -d --force-recreate ao-worker-1 ao-worker-2
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
cd agent-org/agent-bridge && pip install -e .[test] && pytest -q   # 865 test fns (AST, 2026-09-19)
```

## Conventions honored
- **G1** — never commit/push or merge to `main` without an explicit ask.
- **3-place change** — every container is in compose **+** `scripts/recovery/emergency-recovery.ps1`
  **+** `.claude/skills/stack-map/references/workspace-stacks.md` (run `/stack-map`).
- **No secrets in files** — bot tokens / DB passwords / model keys via env only.
- **Reuse, don't reinvent** — little-coder for workers, its floor (git-proxy/lc-egress) for
  enforcement, Open Brain for audit/learning, the existing `llm-gateway` for local inference.
