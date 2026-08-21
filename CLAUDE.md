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
| **Main** (`ai-stack`) | `docker compose ...` (root file includes `compose/<plane>.yml` since 2026-08-20) | core (`openwebui`, `tailscale`, `open-terminal`), inference (`llm-gateway` + `llm-gateway-db`/`-ui` — LiteLLM **front door**, holds the `llama-cpp`/`llama-cpp-embed` aliases; `llm-queue` — per-caller admission/priority; `llama-cpp-upstream`, `llama-cpp-embed-upstream` — real inference), memory (`mnemory`, `mnemory-gateway`), search (`vpn` — Mullvad engine egress; `tor` — page fetch; `redis`, `searxng`, `gateway`), coder (`little-coder`, `lc-egress`), aux (`smolcrawl-pipelines`, `surrealdb`, `open_notebook` — *being retired, still live for podcasts*), 12 backup sidecars. **31 default services.** |
| **Portal** (`portal`, own compose project since 2026-08-21) | `scripts/portal/portal-on.ps1` / `portal-off.ps1` (`portal/docker-compose.yml`) | 12 services (`caddy`, `authelia`, `cloudflared`, watchers/alerter/tripwire/cron + 2 backups). Internet-exposed auth front-end; attaches to `ai-stack_app-net` externally to reach openwebui/open_notebook — positioned to front more apps later. |
| **Open Brain** (`open-brain`) | `docker compose -f OB1/docker/docker-compose.yml ...` | ~24 `openbrain-*` containers (own project; attaches to `ai-stack_llm-net` as external). Bring up **after** `llm-gateway` is healthy; tear down before the main stack. |
| **agent-org** | `docker compose -f agent-org/docker/docker-compose.yml ...` | Mattermost (+db) + `agent-bridge` (the governed org bus, 700+ tests) + profile-gated `workers`/`cloud` slices. |
| **Recovery** | `scripts/recovery/emergency-recovery.ps1` (or `.bat`) | Ordered restart/repair across both projects — `recover` / `nuclear` / `gpu-reset`. Does **not** manage the Portal. |

Retired 2026-08-20 (CLEANUP-PLAN v3): `watchtower` (manual updates per
`documentation/runbooks/UPDATE-MANAGEMENT.md`), `search-mcpo` and `lc-mcpo`
(no consumers), Ollama and LM Studio remnants.

**Inference plane:** every service reaches inference through
`http://llama-cpp:8080` / `http://llama-cpp-embed:8080` — **network aliases on
`llm-gateway` (LiteLLM)**, which forwards through **`llm-queue`**
(hold-and-dispatch, per-caller lanes) to the `*-upstream` servers. **Never
route inference around LiteLLM**; only health/GPU/recovery probes may target
`*-upstream` directly. Enforced pre-commit by
`scripts/checks/check-llm-gateway-routing.ps1`. Gotchas: LiteLLM runs permissive (no
master_key — the virtual-keys cutover runbook is
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
- **Container rule:** adding/removing/moving a container = the compose plane
  file + recovery scripts (`emergency-recovery.ps1`/`.bat`) + the stack-map
  reference doc **together**. `/stack-map` checks for drift.
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
