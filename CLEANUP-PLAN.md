# ai-stack — comprehensive restructure, cleanup & security plan

**Date:** 2026-07-19. Supersedes the 2026-06-20 plan (its Phase 0 was executed; its
unfinished Phases 1–3 are folded into Parts B, C and G below).
**Status:** PLAN ONLY — nothing in this document has been executed.
**Evidence base:** seven parallel read-only surveys of the full workspace
(deploy plane, agent-org, scripts, OB1, root services, security posture, docs)
on 2026-07-19. File:line references are from that snapshot.

Discipline for every phase (unchanged from v1):

- **Archive (`git mv`), don't delete**, anything with history or inbound links;
  explain every removal in the commit message (what / why / what replaces it).
- **Verify a file is dead before moving it** (the live-vs-dead evidence in this
  plan is a starting point, not a substitute for the check at execution time).
- **Container rule:** adding/removing/moving a container or its files = change
  the compose file + the recovery scripts + the stack-map doc **together**.
- One part (or sub-part) per branch/PR. Never batch a security fix with a
  restructure.

---

## Executive summary

The workspace is ~771 tracked files across two compose projects (46-service
main stack + 23-container OB1) plus a profile-gated portal plane, an agent-org
orchestration stack, and a host-level recovery/bridge layer. A year of
AI-driven, experiment-at-a-time evolution left it in a recognizable state:

**What is healthy** (worth protecting, not rebuilding):

- Network posture: **every published port on every compose file binds to
  127.0.0.1**; ingress is only Tailscale serve + Cloudflare Tunnel behind
  Authelia. No `privileged:`, one docker.sock mount total.
- Secret hygiene at the git layer: all real `.env`/key/token files are
  gitignored and verified untracked; **git history contains no secret values**
  (the two historical `.env` commits are empty files) — with **one critical
  exception** (Part A.1).
- agent-org's 26 single-responsibility modules, centralized pydantic config,
  and 604 fully-mocked tests; little-coder's 38-file test suite; llm-queue and
  search-gateway are cleanly structured.

**The five structural problems:**

1. **Monoliths.** `agent-org/agent-bridge/app/orchestrator.py` is 11,758 lines
   (~200 methods on one class — 58% of the subsystem, against its own "thin
   glue" docstring). `owui/tools/fileshed.py` 11,000; `entrypoint.sh` 1,181
   (same logic triplicated ×7 services); `bridge.py` 1,775;
   `docker-compose.yml` 2,206 lines with zero YAML anchors.
2. **Duplication instead of shared foundations.** The service inventory is
   restated in ~7 scripts; the two privacy gateways are near-verbatim copies;
   machine-generated `health()` boilerplate is pasted across every capability
   module; three near-identical Mattermost token loaders; 13 copy-pasted
   backup sidecars in two inconsistent flavors; OB1's six Deno workers each
   hand-roll the same DB/embed/LLM plumbing.
3. **Dead strata from retired experiments.** Ollama (retired) still lives in
   compose env, entrypoint.sh serve logic, `modules/emergency-recovery`
   restart sequences, config stubs, and all three top-level docs. The LM Studio
   proxy path (4 scripts + hardcoded link-local IP), the legacy per-capability
   OWUI pipes, the 10-months-cold `tools/` migration toolkit, and 4 of 6 files
   in `test/` are dead or broken.
4. **Documentation describes a stack that no longer exists.** `README.md`
   (1,362 lines) and `.github/copilot-instructions.md` still lead with Ollama
   and cite `ai_stack_router.py` — a file that exists nowhere. The real stack
   (LiteLLM front door + llama-cpp upstreams + ~45 other services) is largely
   undocumented at the top level.
5. **Security debt** — small in count, high in leverage: one committed live
   token, the whole-repo mount into the internet-exposed OWUI container, and
   Watchtower's host-root-equivalent docker.sock with an unpinned image.

**Submodule story (Part H):** OB1 is already an independent fork repo nested
inside the tree and gitignored — the main repo has *zero provenance* of which
OB1 commit it runs. Converting it (and later little-coder) to proper git
submodules gives pinned provenance with almost no operational change.

---

## Part A — Immediate security remediation (do before anything else)

### A.1 CRITICAL — committed live bearer token
`.vscode/mcp.json:14` contains a live Open Brain gateway key (`gw-…`) in an
`Authorization: Bearer` header, tracked and present in history (commits
`cff94e9`, `05f868b`).

1. **Rotate the key** at the Open Brain gateway (`OPENBRAIN_GATEWAY_KEY` in
   `OB1/docker/.env` + wherever clients hold it).
2. `git rm --cached .vscode/mcp.json`; add `.vscode/mcp.json` to `.gitignore`;
   keep a `.vscode/mcp.json.example` with the env-indirection shape.
3. History scrub (git-filter-repo/BFG) is **optional after rotation** — the
   repo remote is private; decide deliberately (Decision D-1).

### A.2 HIGH — whole-repo mount into the internet-exposed frontend
`docker-compose.yml:18` mounts `.:/host_project:ro` into `openwebui` — the
container fronted by Cloudflare/Authelia that executes user pipes. The mount
includes every on-disk secret (`.env`, `secrets/`, `OB1/**/.env`,
`config/authelia/users_database.yml`, tailscale certs, the GitHub App `.pem`).

- **Interim fix (small, do now):** replace the single mount with narrow ro
  mounts of only what the pipe chain reads: `./core`, `./modules`,
  `./schemas`, `./scripts` (for `/host_scripts` dispatch), `./system-prompts`.
  Verified consumers: `core/router.py:23-27`,
  `scripts/ai_pipes/unified_openwebui_pipe.py:174,276`,
  `owui/pipes/githelper.py:102`,
  `modules/emergency-recovery/service/emergency_recovery.py:1236`.
- **Durable fix:** Part G.1 consolidates the pipe subsystem into one directory
  so the mount becomes a single folder by construction.

### A.3 HIGH/MED — Watchtower
`docker-compose.yml:90-96`: docker.sock (`:ro` does not de-privilege the API —
host-root-equivalent) + a second whole-repo mount (`.:/compose-dir:ro`) + an
unpinned `containrrr/watchtower:latest`, while it updates only `openwebui`.

- Pin the image to a digest; drop the repo mount if not strictly required.
- **Decision D-2:** given `UPDATE-MANAGEMENT.md` and the autonomous-updates
  design both exist, consider retiring Watchtower for an explicit update
  runbook/script and removing the docker.sock mount entirely.

### A.4 MEDIUM/LOW items

| Item | Evidence | Action |
|---|---|---|
| Runtime state committed in git | ~21 `mcp-*.json` + `approvals.jsonl` + `audit.jsonl` tracked under `scripts/claude-sessions-bridge/state/` (added before the ignore rule) | `git rm --cached -r` the tracked state files (ignore rule already exists) |
| `.claude/settings.local.json` both tracked and ignored | `.gitignore:49` vs `git ls-files` | `git rm --cached`; contents are non-secret |
| Hardcoded `SURREAL_USER/PASSWORD` (root/root) in `open-notebook-backup` | `docker-compose.yml:1357-1358` (runtime services use `${VAR}`) | Switch the sidecar to `${SURREAL_USER}`/`${SURREAL_PASSWORD}`; keep the network-isolation note |
| Permissive LiteLLM (no `master_key`) on internal `llm-net` | `config/litellm.config.yaml`; net `internal: true` (`docker-compose.yml:2163`) | Not host-reachable; risk = any compromised container gets unmetered LLM access. **Decision D-3:** add per-caller keys (llm-queue already tracks callers) or explicitly accept + document in SECURITY.md |
| `.env.example` drift | Missing: `MULLVAD_WG_*` (compose 946-948), `LITELLM_UI_MASTER_KEY/USERNAME/PASSWORD` (749-751), `WORKBENCH_KEY` (1719), `MNEMORY_GATEWAY_KEY`/`MNEMORY_CLOUD_USER` (396-397), `SMOLCRAWL_API_KEY` (779) | Add keys (no values) with one-line comments |
| SECURITY.md scope gap | Omits A.1–A.3; otherwise verified accurate | Update after A.1–A.3 land |
| Redaction test fixture | `little-coder/tests/test_sanitize.py:108` realistic-format GitHub PAT | 30-second confirm it was never a real token |
| Secrets layout duplication | `config/alerter/{credentials,token}.json` duplicated/shadowed by `secrets/google/portal-alerter/*` (compose mounts the latter, 1603-1604) | Pick `secrets/` as the single home; remove the `config/alerter` copies |

---

## Part B — Quick wins: cruft, junk, drift (low risk; days, not weeks)

### B.1 Git hygiene bugs (silent but live)
- **`backup/` allowlist rot — untracked files are LIVE infrastructure:**
  `.gitignore` re-includes only 8 scripts, but compose mounts/builds newer
  untracked ones — `generic-tar-backup.sh` (13 sidecars),
  `pg-backup.sh` (agent-org compose), `open-notebook-backup.sh`,
  `openbrain-db-backup.sh`, `openbrain-wiki-backup.sh`,
  `Dockerfile{,.postgres,.surreal}`. **A fresh clone cannot run backups.**
  Extend the re-include list and commit them.
- Commit the pending agent-org work cluster (P12–P15 docs, gym-010 ground
  truth, two test files) — it is coherent work, not clutter.
- Branch sprawl: 15 local branches; prune merged/dead ones (Decision D-7).

### B.2 Junk and stale artifacts
- Empty botched-redirect dirs: `config/authelia;C/`, `config/caddy/Caddyfile;C/`,
  `OB1/integrations/kubernetes-deployment;C/` — delete.
- `data-backup/` — one 20 GB OpenWebUI snapshot from 2026-03-20, superseded by
  the sidecar system (`backups/` holds current artifacts). Delete after a
  one-glance confirm (Decision D-4).
- Root `gym-*.log` (5 files) → `logs/` (they're already gitignored); point the
  gym runner's log path there.
- `logs/tailscale-health.log` is 5.3 MB with no rotation — add rotation in
  `check-tailscale-health.ps1`.
- Config stubs `config/ollama.conf`, `config/openwebui.conf` (1-line comments,
  never mounted) — archive.
- `data/openwebui/` — leftover from the move to the `openwebui-data` named
  volume; confirm empty/stale, then delete.

### B.3 Deploy-drift fixes (redeploy, don't rewrite)
- `owui/pipes/server_status.py` (deployed snapshot) lags its build source —
  missing the `llm-traffic` panel (`core/router.py:522-528` exists). Rebuild
  from `scripts/ai_pipes/unified_openwebui_pipe.py` and re-paste.
- ~~`owui/tools/deep_research.py` defaults to unreachable
  `host.docker.internal:8818`; re-paste the fixed client.~~ **RESOLVED
  2026-08-20.** It was never actually drifted — the *stored valve* has always
  been `http://openbrain-research:8000`, which overrides the in-code default
  (see `owui/README.md`). The "fixed client" this pointed at
  (`smolcrawl/deep_research_thin_client.py`) was a v1.0.0 snapshot of the same
  tool and has been retired; `owui/tools/deep_research.py` is the only copy.

### B.4 Dead code — archive with evidence

| Set | Evidence of death | Action |
|---|---|---|
| Ollama remnants | Service commented out (`docker-compose.yml:216-254`) but referenced by: `OLLAMA_HOST` env (compose:26), entrypoint.sh serve+monitor blocks (136-146, 678, 1001-1067, 1160), `modules/emergency-recovery/service/emergency_recovery.py:55,248,315-316,434,476`, `.env.example:1-2` | Remove all remnants in one "retire ollama" commit; update recovery module sequences + stack-map together |
| LM Studio proxy path | `Fix-LMStudioTailscale.ps1`, `fix-lmstudio-tailscale.sh`, `lmstudio-proxy-supervisor.sh`, `lmstudio_fix.py` (v1) — all target `169.254.83.107`, absent from `scripts/lib/stack-services.json`; only `lmstudio_fix_v2.py` is registered (`emergency_recovery.py:48`) | **Decision D-5:** if LM Studio is still an occasional host inference target keep v2 + entrypoint block; archive v1 + the 3 proxy scripts either way |
| Legacy per-capability pipes | `scripts/ai_pipes/{gpu_status,system_health,emergency_recovery,custom_tools,help}_pipe.py` + `openwebui_pipe_template.py` + `scripts/templates/` + `scripts/utilities/` — README labels them superseded; only residual use is one broken-ish test import. **Exception: `tailscale_serve_pipe.py` is LIVE** (dispatched by `modules/custom-tools/service/custom_tools.py:361,365`) | Archive the dead ones; keep `tailscale_serve_pipe.py` (move it under the module that calls it in Part G.1) |
| `tools/` migration toolkit | `migration_tool.py`, `refactor_orchestrator.py`, `scaffold_generator.py`, `validation_tool.py` + `modules/migration_report.json` — last touched 2025-09-26, zero imports/compose refs | Archive the set (keep `tools/owui-knowledge-to-openbrain/` — separate live-ish tool) |
| `modules/custom-tools` v1/v2 twins | `tailscale_serve_admin.py` superseded by `_v2` (only v2 registered) | Archive v1; rename `_v2` → intent-named (from old plan Phase 1) |
| `test/` broken tests | 4 of 6 files have `sys.path` pointing at nonexistent `test/modules`, `test/core`, `test/scripts` dirs; one tests the disabled ollama container | Fix or archive per file; root `test_pipe_lmstudio.py` is the *functional* twin — move it into `test/`, delete the broken copy |
| Orphaned paste artifacts | root `skills/*.md` (3 files, zero references), `system-prompts/general-system-prompt.md` | Move into `documentation/` as reference or archive |
| Misfiled agent-org docs | `agent-org/docs/PLAN-bridge-two-lane-attention.md` + `PROPOSAL-follow-subscription-signal-filtering.md` are about `scripts/claude-sessions-bridge/` | `git mv` to `scripts/claude-sessions-bridge/docs/` |

---

## Part C — Documentation truth pass (no code risk)

1. **`README.md` rewrite** (1,362 lines, "Last Updated October 6, 2025"). It
   describes a ~5-service Ollama stack; cites `ai_stack_router.py` ×7 (file
   exists nowhere), `data/ollama/`, dead port 11434, nonexistent
   `core/openwebui_adapter.py` etc. Replace with a **short** README: what the
   stack is, the two-project + portal topology table (reuse CLAUDE.md's),
   quickstart, pointers to `/stack-map`, runbooks, SECURITY.md. Salvage the
   good Tailscale/GPU debugging content into `documentation/runbooks/`.
2. **`.github/copilot-instructions.md`** — half-updated (accurate LiteLLM
   section grafted onto an Ollama-era doc; self-contradicts on Watchtower ×3).
   Either regenerate from CLAUDE.md + stack-map or delete (Decision D-6).
3. **`UPDATE-QUICK-START.md`** — claims OWUI v0.6.41 / Ollama 0.13.3 (actual:
   v0.9.6 / removed). Refresh or fold into `UPDATE-MANAGEMENT.md`.
4. **`documentation/` restructure** (old plan Phase 2, still valid):
   - `documentation/archive/` ← the Sept-2025 pipe-era fix notes
     (`PIPE_*`, `JSON_ISSUE_FIXED`, `TIMESTAMP_FIX_APPLIED`, etc.), `Links.md`,
     `Readme.md` scratch stub.
   - `documentation/runbooks/` ← incident-response, monitoring-access,
     backup-conventions, restore-from-snapshot, UPDATE-MANAGEMENT,
     tailscale-serve guides (+ salvaged README content).
   - `implementation-guide/` → tag each subfolder `shipped/` vs `proposed/`
     per the verified table from the survey (LiteLLM-Proxy, little-coder,
     web-search, auth front-end, owui-0.9.6, Systems-of-structured-data =
     shipped; Jupyter, level-4-autonomy, podcast expansion, WSL governance,
     project-lifecycle = never built; teams-chat-orchestration,
     claude-code-mattermost-bridge, research-engine = built elsewhere/partial).
   - Move the **runnable dev code** out of docs:
     `documentation/implementation-guide/open -notebook-integration-openbrain/iks-dev/`
     (compose file, `seed.sql`, `*.py`, `__pycache__`) → a real code home;
     fix the leading-space folder name.
   - Add `documentation/README.md` index; adopt kebab-case names.
5. **agent-org docs**: split the P-series into `docs/design/` (ORCHESTRATION-
   DESIGN, still-open proposals) vs `docs/log/` (built/historical records);
   fix `agent-bridge/README.md` "55 tests" → current count.

---

## Part D — Deploy plane refactor

### D.1 Modularize `docker-compose.yml` (2,206 lines, 46 services)
Use `include:` (already proven in OB1's compose) to split by plane, keeping
`docker compose` UX identical:

```
docker-compose.yml            # thin root: include list + networks + volumes
compose/core.yml              # openwebui, tailscale, watchtower(?)
compose/inference.yml         # llm-gateway(+db,+ui), llama-cpp-*, llm-queue
compose/memory.yml            # mnemory, mnemory-gateway
compose/search.yml            # vpn, tor, redis, searxng, gateway, mcpo
compose/coder.yml             # open-terminal, little-coder, lc-mcpo, lc-egress
compose/aux.yml               # smolcrawl, surrealdb, open_notebook
compose/backups.yml           # all 13 backup sidecars
compose/portal.yml            # the whole profiles:[internet] slice
```

While splitting, introduce the missing shared blocks:
- `x-hardening` anchor (the portal quartet is copy-pasted ~12×), `x-watchtower-
  disable` label (~45×), `x-healthcheck-http` template, `x-backup-sidecar`
  template (13 near-identical inline cron scripts; also unify the
  `crond`-on-alpine vs `supercronic` split).
- Move the ~15-line inline `CMD-SHELL` slot-watchdog healthcheck
  (`docker-compose.yml:331`) into a mounted script.
- Update `emergency-recovery.ps1/.bat` + stack-map in the same PR (container
  rule) — service names/behavior do not change, only file layout.

### D.2 Rewrite `entrypoint.sh` (1,181 → ~300 lines)
Seven near-identical `setup_*_serve()` functions + an LM Studio inline block +
a 450-line monitor loop restate each service's socat/serve logic **three
times**. Replace with one data-driven table (`name, local_port, target,
ts_port, enabled_flag`) and one loop for setup/monitor/reconnect. Drop the dead
Ollama serve path; make cross-stack routes (Mattermost, Quartz wiki, LiteLLM
UI) config-flagged since their targets live in other compose projects.

### D.3 Single source of truth for the service inventory
`scripts/lib/stack-services.json` is canonical but restated in
`emergency-recovery.ps1` (5 arrays), `.bat`, `check-tailscale-health.ps1`,
`check-backup-coverage.ps1`, `restore-from-snapshot.ps1`, `portal-off.ps1`,
`status_check.py`. Make the PowerShell scripts load the JSON (PS5.1
`ConvertFrom-Json` note: PSCustomObject, ASCII-only no-BOM), and add a drift
check (Part I) comparing the JSON against compose.

### D.4 Windows host-path portability
Hardcoded host paths to parameterize via `.env`:
`C:\Users\yamao\.lmstudio\models` (compose 271, 1482),
`D:\Open WebUI\open-notebook\*` (806, 878, 1367), OB1's `D:\_data` mounts
(scheduled compose 72, 102, 133, 191), `d:/Open WebUI/ai-stack/...` literals in
`scripts/notify-mattermost.sh:18,21`, `scripts/ai_pipes/config.json:5,7`, and
`.claude/settings.local.json` hooks.

---

## Part E — Code quality: dedup and shared foundations

1. **Unify the twin privacy gateways.** `openbrain-gateway/app.py` says it
   itself: "Modelled on ../mnemory-gateway/app.py … identical." Extract one
   small `privacy-gateway` package (Starlette app factory: allow-list, label
   forcing, SSE proxy, health) with two thin configs. Their only divergence is
   `labels` vs `metadata`.
2. **One Mattermost token loader.** The env-file token resolution block is
   triplicated across `bridge.py:80-119`, `approval_server.py:54-88`,
   `mattermost-mcp/server.py:46-109`. Extract `scripts/lib/mm_auth.py`.
3. **Collapse the `health()` scaffolding** in `modules/*` (twin
   instance+module-level functions machine-generated in 7 modules) into one
   base class in `core/`.
4. **OB1 workers shared lib** (in the OB1 repo, not here): 6 Deno services
   each hand-roll pg pool + embedding + LLM fetch; factor into one shared
   module. Also split `integrations/kubernetes-deployment/index.ts` (1,804
   lines — the MCP server) and `docker/extensions-server/index.ts` (1,564).
5. **Convention, not code:** adopt one health-endpoint convention
   (`/healthz`) across the five hand-rolled variants when each service is
   next touched — not worth a big-bang change.

---

## Part F — Monolith decomposition

### F.1 `orchestrator.py` (11,758 lines) — the flagship refactor
The 604 mocked tests make this tractable. The class's responsibility clusters
map ~1:1 onto existing `modules/` peers; extract in dependency order, one
extraction per PR, tests green after each:

1. Pending-decision store + rehydrate (11299–11415) — smallest, isolated.
2. Handoff protocol (10584–10851).
3. Worker plan gate / flail replan (10858–11134).
4. Drain loop + QA lenses (9493–10103).
5. Burndown campaign (6860–7813).
6. Delivery pipeline: gates + PR + merge + D-pipeline (7814–8907) — largest.
7. Intake/NL classification (2998–3959; `nl_intake` alone is ~765 lines).
8. Project onboarding/repo resolution (2356–2745).
9. NL admin inlets → a command registry (the `_nl_*` scatter + `_handle_command`).

Target end-state: orchestrator.py < ~2,000 lines of true wiring/glue.
**Cadence guard:** do extractions *between* gym iterations, never mid-round;
each extraction is a mechanical move-plus-delegate, no behavior change.

### F.2 `bridge.py` (1,775 lines)
Split into poller / session table / follow registry / turn worker modules.
Do this **after** the pending zombie-session fixes land (uncommitted work is
in flight there right now).

### F.3 OWUI plugin monoliths (`fileshed.py` 11,000; `superpowers_tool.py`
3,592; `code_agent.py` 3,018)
Single-file is structural (OWUI paste-deploy), so decomposing the deployed
artifact is pointless. If/when these need real maintenance, adopt the
`server_status.py` pattern: modular source + build step → flattened snapshot.
Low priority; `code_agent.py` is marked inactive — candidate for archive.

### F.4 smolcrawl `deep_research/`
~10k LOC, 8 files over 500 lines, **zero tests** — the least-covered live
service. Before any refactor: add smoke tests around the pipeline entry
points. Otherwise leave it alone until it next breaks or needs a feature.

---

## Part G — Repo restructure (taxonomy)

### G.1 Consolidate the status-pipe subsystem (old Phase 3a — still the
right move, now with a security payoff)
One logical component spans four top-level dirs (`scripts/ai_pipes/` + `core/`
+ `modules/` + `schemas/`), reached via the whole-repo mount that A.2 flags.
Consolidate:

```
status-pipe/
  orchestrator.py      (was scripts/ai_pipes/unified_openwebui_pipe.py)
  router.py            (was core/router.py)
  modules/             (was top-level modules/ — minus retired ollama logic)
  schemas/             (was top-level schemas/)
  tailscale_serve/     (was scripts/ai_pipes/tailscale_serve_pipe.py — live)
  tests/               (the salvaged test/ files, converted to pytest)
```

Then the OWUI mount becomes `./status-pipe:/host_project:ro` (+ `./scripts`
for recovery dispatch + `./system-prompts`) — the A.2 durable fix falls out
for free. Touches: `ROUTER_SCRIPT_PATH`/module paths in the pipe,
`emergency_recovery.py` script registry, compose mounts, doc references, and
the `owui/pipes/server_status.py` rebuild.

### G.2 Sort `scripts/` (old Phase 1, refined)
```
scripts/
  recovery/    emergency-recovery.ps1/.bat, quick-fixes.bat, the py backends,
               simple-monitor.ps1, install-service.ps1
  portal/      portal-*.ps1, breach-killswitch.ps1, access-query.ps1
  backup/      backup-to-nas.ps1, restore-from-snapshot.ps1, check-backup-*,
               install-nas-backup-task.ps1, set-nas-credential.ps1
  checks/      check-*.ps1, validate-lineendings.ps1
  bridges/     claude-sessions-bridge/, mattermost-mcp/, notify-mattermost.sh
  lib/         stack-services.json, portal-alerter-client.ps1, mm_auth.py (E.2)
  archive/     dead LM Studio scripts, lmstudio_fix.py v1, legacy pipe
               templates/utilities (or move to documentation/archive)
```
Path consumers to update in the same PR: compose mounts
(`post-update-hook.sh`), `.mcp.json`, `.claude/settings.local.json` hooks,
`emergency_recovery.py` registry, Scheduled Tasks (bridge, NAS backup,
monitor), docs. This is mechanical but wide — do it as its own PR with a
grep-verified reference sweep.

### G.3 Service dirs stay at root (old Phase 3b — recommend **against** for now)
Moving `little-coder/`, `llm-queue/`, etc. under `services/` rewrites build
contexts, recovery paths, agent-org's `../../little-coder` references, and the
stack-map for purely cosmetic gain. Skip unless/until the submodule split
(Part H) makes it moot per-service.

---

## Part H — Submodule strategy

Principle: a directory earns submodule status when it (a) has its own life
cycle/remote, (b) is consumed as a unit, and (c) benefits from pinned
provenance in the parent. Three tiers:

### H.1 OB1 — convert now (it is already 90% there)
Today OB1 is a nested independent repo (fork of `NateBJones-Projects/OB1`,
origin `devonpveller/OB1`, branch `feature/integrated-knowledge-system`),
gitignored by the parent → **the main repo has no record of which OB1 commit
is deployed**. Conversion plan:

1. Resolve the two parent-tree couplings:
   - `OB1/docker/docker-compose.yml:152` builds from `../../openbrain-gateway`
     → **move `openbrain-gateway/` into OB1** (it is OB1's front door; its twin
     stays in main as `mnemory-gateway` until E.1 unifies them) or publish a
     prebuilt image.
   - `:571` mounts `../../secrets/openbrain-wiki-deploy_key` → parameterize the
     secret path via env so the parent can point it anywhere.
2. Remove `OB1/` from `.gitignore`; `git submodule add https://github.com/devonpveller/OB1.git OB1`
   pinned to the current commit. On-disk layout is unchanged — compose
   commands, recovery scripts, and external networks all keep working.
3. Adopt the **bump-via-PR rule** for gitlink updates (a submodule pointer
   change is a real change: review it like code; never let automation bump a
   gitlink to a commit unreachable on the remote).
4. Document the two-repo workflow in CLAUDE.md (clone with
   `--recurse-submodules`; recovery scripts unaffected).

### H.2 little-coder — strong candidate, second
Self-contained (own pyproject, 3 Dockerfiles, README, 38-test suite, its own
version cadence 1.9.x) and consumed as a unit by **two** stacks: main compose
(4 build contexts) and agent-org (builds worker images from
`../../little-coder`; `gen-worker-configs.py` reads its config). Split to its
own repo + submodule at the same path — all `../../little-coder` references
keep resolving. Do after A/B (so the split repo starts clean), before the
agent-org decision.

### H.3 agent-org — candidate, but **defer**
Nearly self-contained (own compose, tests, charters; couplings are only
`../../.env`, `../../backup/pg-backup.sh`, `../../backups/`, and the
little-coder build context). But it is the hottest dev area (daily gym
iterations, uncommitted work in flight) and the orchestrator decomposition
(F.1) should land first — splitting a repo mid-refactor doubles the friction.
Revisit when P-series iteration slows.

### H.4 Stays in the main repo
- `scripts/` bridges (host-level operational glue, coupled to this machine's
  Scheduled Tasks and agent-org's env file).
- `owui/` plugin snapshots (deploy artifacts of *this* deployment).
- `status-pipe/` (G.1), `search-gateway/`, `llm-queue/`, `mnemory-gateway/`,
  `smolcrawl/` — deployment-specific services with no independent consumers;
  submodule overhead would exceed the benefit.
- The orchestration gym is already a separate repo (`ai-orchestration-gym`) —
  optionally add it as a submodule too for the same provenance reason, low
  priority.

---

## Part I — Guardrails: keep it clean (extensibility)

1. **CI (GitHub Actions):** on PR — run the three real pytest suites
   (agent-bridge ~604 tests, little-coder, llm-queue, search-gateway);
   `docker compose config -q` on every compose file (root, portal profile,
   agent-org, OB1); `scripts/check-llm-gateway-routing.ps1` equivalent;
   a secret scanner (gitleaks) — it would have caught A.1.
2. **Pre-commit:** extend the existing routing-check hook with ruff (a
   `.ruff_cache` exists but no config — add `ruff.toml`) and gitleaks.
3. **Drift checks as scripts, not prose:** a `checks/verify-stack-map.ps1`
   that diffs `stack-services.json` ↔ compose service lists ↔ recovery-script
   arrays (D.3), runnable by CI and by the `/stack-map` skill.
4. **Conventions doc** (one page in `documentation/`): where a new service
   goes, the 3-places container rule, health endpoint convention, kebab-case
   filenames, "archive don't delete", secrets live only in `.env`/`secrets/`,
   design docs vs logs split.
5. **OB1 upstream flow:** with H.1 done, document the fork-sync routine
   (fetch upstream → rebase/merge on the feature branch → bump gitlink via PR).

---

## Execution order

| Phase | Parts | Risk | Depends on |
|---|---|---|---|
| 1. Security now | A.1, A.2 interim, A.3 pin, A.4 git-hygiene rows | Low (surgical) | — |
| 2. Quick wins | B.1–B.4 | Low | — |
| 3. Docs truth pass | C | None (docs only) | ideally after 2 (so docs describe the cleaned state) |
| 4. Deploy plane | D.1–D.4 | Medium (compose churn; container rule applies) | 2 (ollama retired first) |
| 5. Shared foundations | E.1–E.3 | Low-medium | — |
| 6. Status-pipe consolidation | G.1 (+ A.2 durable) | Medium | 2 (dead pipes archived), 4 helps |
| 7. Scripts reorg | G.2 | Medium (wide path sweep) | 6 (ai_pipes moved out first) |
| 8. OB1 submodule | H.1 | Low-medium | A.1 (key rotated first) |
| 9. Orchestrator decomposition | F.1 (9 PRs) | Medium per-PR, high total value | test suite green; between gym rounds |
| 10. little-coder split | H.2 | Medium | 1–2 |
| 11. Guardrails | I | Low | incrementally, starting phase 1 (gitleaks first) |

Phases 1–3 are a weekend-sized effort and remove most of the risk and
confusion. Phases 4–8 are each a contained PR-series. Phase 9 is the long
game, paced by the gym cadence.

---

## Decisions needed (sign-off checklist)

- **D-1:** After rotating the leaked gateway key — scrub git history too, or
  accept rotated-and-removed? (History scrub rewrites all SHAs.)
- **D-2:** Retire Watchtower (explicit update runbook instead) or keep it
  pinned with narrowed mounts?
- **D-3:** LiteLLM `llm-net` — add per-caller keys, or formally accept the
  permissive-inside-internal-network posture in SECURITY.md?
- **D-4:** Delete the 20 GB `data-backup/` March snapshot?
- **D-5:** Is LM Studio still a live occasional inference target (keep v2
  path + entrypoint block) or fully retired (archive all of it)?
- **D-6:** `.github/copilot-instructions.md` — regenerate or delete?
- **D-7:** Prune the 15 local branches to main + develop + current feature?
- **D-8:** Submodule green-light: OB1 now; little-coder after quick wins;
  agent-org deferred — agree with the tiering?
