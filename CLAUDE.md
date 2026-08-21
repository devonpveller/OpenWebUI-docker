# CLAUDE.md — ai-stack workspace

Self-hosted AI stack: Open WebUI + local llama.cpp inference behind a LiteLLM
gateway with an admission queue, a memory layer (mnemory + Open Brain), a
private search gateway, a self-improving coding agent with a governed
multi-agent org, and a gated internet portal.

## Stacks at a glance

Run the `/stack-map` skill (or read
[.claude/skills/stack-map/references/workspace-stacks.md](.claude/skills/stack-map/references/workspace-stacks.md))
for the full inventory — networks, ports, dependency order.

| Stack | Driven with | Contents |
|-------|-------------|----------|
| **Main** (`ai-stack`) | `docker compose ...` (root file includes `compose/<plane>.yml` since 2026-08-20) | Part K (2026-08-21, in progress) is dissolving this into per-plane projects; root becomes the **network anchor** (owns `llm-net`/`app-net`/`default`). **PURE NETWORK ANCHOR since K.5b — 0 services.** Owns `llm-net` / `app-net` / `default` (the `ai-stack_*` names every project attaches to externally); `docker compose up -d` here just creates networks. |
| **Inference** (`inference`, own project since 2026-08-21 K.1) | `docker compose -f inference/docker-compose.yml --env-file .env ...` (or `scripts/stack/stack.ps1`) | The LLM host: `llm-gateway` + `llm-gateway-db`/`-ui` — LiteLLM **front door**, holds the `llama-cpp`/`llama-cpp-embed` aliases on the anchor's `llm-net` (external); `llm-queue` — per-caller admission/priority; `llama-cpp-upstream`, `llama-cpp-embed-upstream` — real inference on its **native** `llm-backend-net`; `llm-gateway-backup`, `lm-models-backup`. **8 services.** |
| **Memory** (`memory`, own project since 2026-08-21 K.2) | `docker compose -f memory/docker-compose.yml --env-file .env ...` | `mnemory` (unified memory layer, llm-net only), `mnemory-cloud-gateway` (the ONLY cloud door, host :8060), `mnemory-backup`. **3 services.** |
| **Search** (`search`, own project since 2026-08-21 K.3) | `docker compose -f search/docker-compose.yml --env-file .env ...` | Private Search Gateway: `vpn` (Mullvad — ALL egress; HTTP proxy :8888), `redis`, `searxng`, `gateway` (host :8085). Owns `search-net`; `vpn`+`gateway` stay on `ai-stack_default` externally so OB1/OWUI DNS holds. **4 services.** |
| **Coder** (`coder`, own project since 2026-08-21 K.4) | `docker compose -f coder/docker-compose.yml --env-file .env ...` | little-coder control plane: `open-terminal` (executor — moved in from core), `little-coder` (daemon :8090; metrics host :9091), `lc-egress`, `little-coder-backup`. Owns `lc-net` + the 7 coder volumes. **4 services.** |
| **Frontend** (`frontend`, own project since 2026-08-21 K.5) | `docker compose -f frontend/docker-compose.yml --env-file .env ...` | `openwebui` (host :3000) + `tailscale` (netns companion — never restart openwebui alone; the project's depends_on encodes the order) + both backups. Images pinned `openwebui:local`/`tailscale:local` — rebuild deliberately only. **4 services.** |
| **Portal** (`portal`, own compose project since 2026-08-21) | `scripts/portal/portal-on.ps1` / `portal-off.ps1` (`portal/docker-compose.yml`) | 12 services (`caddy`, `authelia`, `cloudflared`, watchers/alerter/tripwire/cron + 2 backups). Internet-exposed auth front-end; attaches to `ai-stack_app-net` externally to reach openwebui/open_notebook — positioned to front more apps later. |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml ...` | ~29 containers: the `openbrain-*` fleet + its two backup sidecars + the **Open Notebook trio** (`surrealdb`, `open_notebook`, `open-notebook-backup` — moved in K.5b 2026-08-21; ON stays live until the wiki workbench matures). Attaches to `ai-stack_llm-net`/`app-net` externally. Bring up **after** `llm-gateway` is healthy; tear down before the planes it depends on. |
| **agent-org** | `docker compose -f agent-org/docker/docker-compose.yml ...` | Mattermost (+db) + `agent-bridge` (the governed org bus, 700+ tests) + profile-gated `workers`/`cloud` slices. |
| **Recovery** | `scripts/recovery/emergency-recovery.ps1` | Ordered restart/repair across ALL projects — `recover` / `nuclear` / `gpu-reset`. Does **not** manage the Portal. (The `.bat` twin was archived 2026-08-21 — redundant next to this + `stack.ps1` + the Mattermost/sysadmin channel.) |

Retired 2026-08-20 (CLEANUP-PLAN v3): `watchtower` (manual updates per
`documentation/runbooks/UPDATE-MANAGEMENT.md`), `search-mcpo` and `lc-mcpo`
(no consumers), Ollama and LM Studio remnants.

**Inference plane:** every service reaches inference through
`http://llama-cpp:8080` / `http://llama-cpp-embed:8080` — **network aliases on
`llm-gateway` (LiteLLM)**, which forwards through **`llm-queue`**
(hold-and-dispatch, per-caller lanes) to the `*-upstream` servers. The whole
plane is its own compose project since 2026-08-21 (K.1):
`inference/docker-compose.yml`, which owns `llm-backend-net` and attaches to
the anchor's `llm-net` externally — the alias contract is unchanged. **Never
route inference around LiteLLM**; only health/GPU/recovery probes may target
`*-upstream` directly. Enforced pre-commit by
`scripts/checks/check-llm-gateway-routing.ps1`. Gotchas: LiteLLM enforces per-caller
virtual keys since 2026-08-21 (J.1 — master_key + the x-ai-stack-caller
pre-call hook; every new consumer needs a key, see
`documentation/implementation-guide/LiteLLM-Proxy/J1-VIRTUAL-KEYS-CUTOVER.md`);
`background_health_checks: false` and never GET LiteLLM `/health` via the
alias (model-load thrash — use `/health/liveliness`); llama-swap uses
`--no-mmap` (GGUF mmap over the Windows bind mount hangs).

**Status pipe:** the OWUI "Server Status" pipe subsystem lives in
`status-pipe/` (orchestrator, router, modules, schemas, serve pipe) — the
ONLY code mount into the OWUI container. `owui/` holds the deploy-by-paste
snapshots + `manifest.csv` (file → OWUI id; skills included).

## Conventions

- **Git:** never commit or push on the user's behalf unless explicitly asked.
  Hooks live in `.githooks/` (`git config core.hooksPath .githooks`): secret
  guard, LF check, gateway-routing check.
- **Container rule:** adding/removing/moving a container = the plane compose
  file + recovery (`emergency-recovery.ps1` + `stack.ps1`) + the stack-map
  reference doc **together** — and the rest of the lifecycle surfaces
  (backups + restore catalog, watchdog, `stack.ps1 health` probe,
  stack-services.json, registry). The FULL checklist is
  `documentation/runbooks/SERVICE-LIFECYCLE.md`; `/stack-map` checks drift.
- **Archive, don't delete:** retired code goes to `scripts/archive/` (see its
  README provenance table), retired docs to `documentation/archive/`.
- **Verify against gitignored evidence** before declaring anything dead:
  `.env*` values and `backup/models/` OWUI exports are exactly where
  "zero references" verdicts die (`grep --no-ignore`, live `webui.db`).
- **Shell:** Windows + PowerShell 5.1 (ASCII no-BOM for scripts it parses);
  recovery scripts assume Docker Desktop. Never restart `openwebui` alone —
  `tailscale` shares its netns; order is openwebui → tailscale.
- **Lint:** `ruff check .` (F + E9 gate; subprojects carry their own configs).

## Pointers

- Stack topology / "what runs here?" → `/stack-map` skill
- Recovery after a crash or netns break → `scripts/recovery/emergency-recovery.ps1`
- Runbooks (updates, backups, incident response, out-of-band channel) →
  `documentation/runbooks/` + `documentation/sysadmin-out-of-band-channel.md`
- Per-feature status (shipped/draft) → `documentation/implementation-guide/README.md`
- The living cleanup/restructure plan → `CLEANUP-PLAN.md` (v3)
- little-coder design + workflow → `documentation/implementation-guide/little-coder/`
- Private search gateway → `search-gateway/README.md`

## OB1 submodule (since 2026-08-21)

OB1 is a **pinned git submodule** (`.gitmodules` → `devonpveller/OB1.git`,
branch `feature/integrated-knowledge-system`), not a loose nested repo. The
parent records exactly which OB1 commit is deployed.

- **Clone:** `git clone --recurse-submodules …`, or `git submodule update
  --init` in an existing checkout. Recovery scripts and `docker compose -f
  OB1/docker/docker-compose.yml …` are unaffected — the on-disk layout is
  identical.
- **The gitlink is real code — bump it via PR.** After landing OB1 changes:
  push them to OB1's remote FIRST (the pinned SHA must be reachable there, or
  a fresh `--recurse-submodules` clone breaks), then in the parent
  `git add OB1` + commit the new pointer with a message saying what moved.
  Never bump the gitlink to a commit that isn't on the OB1 remote.
- **openbrain-gateway** source lives HERE (`openbrain-gateway/`, beside its
  twin `mnemory-cloud-gateway`); OB1 consumes the prebuilt
  `openbrain-gateway:local` image. Rebuild it from this repo:
  `docker build -t openbrain-gateway:local ./openbrain-gateway`.
