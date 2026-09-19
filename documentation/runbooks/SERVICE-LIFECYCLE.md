# Service lifecycle checklist — adding, changing, or removing a service

> Status: LIVE · created 2026-08-21 (Part K.8, operator request). This is the
> full form of the CLAUDE.md "container rule". Any agent or human making a
> service-level change works through this list — it is what keeps the
> sysadmin plane, status surfaces, and recovery systems telling the truth.

Since Part K the workspace is compose projects around the `ai-stack` network
anchor. "A service" always lives in exactly one project
(`frontend/ inference/ memory/ search/ coder/ portal/ OB1/docker/
agent-org/docker/`).

## When you ADD a service (or move one between projects)

Work top to bottom; every row is a file or system that will silently lie if
skipped.

| # | Surface | What to do |
|---|---------|------------|
| 1 | **Plane compose file** | Define it in the right project's `docker-compose.yml`. Shared seams (`ai-stack_llm-net` / `app-net` / `default`) attach `external: true` by name; plane-internal networks stay native. Add a `${VAR:?}` fail-loud guard if it carries a credential. Inference consumers use the `llama-cpp` aliases — NEVER `*-upstream`. |
| 2 | **`.env` + `.env.example`** | Real value in `.env`; placeholder in `.env.example` (CI renders every compose file against the example — an empty value trips `:?` guards and fails CI, which is intentional). |
| 3 | **LiteLLM virtual key** | If it calls inference: issue an `sk-` key via llm-gateway-ui, set `metadata.lane`, wire the env var (J1-VIRTUAL-KEYS-CUTOVER.md). Remember OWUI-style apps may hold keys in MORE than one config slot. |
| 4 | **Recovery** | `scripts/recovery/emergency-recovery.ps1`: add to the project's service list (`$Script:<Plane>Services` / `OB1Services` / `AgentOrgServices`) so status tables and health gates see it. New plane project ⇒ a `Start-/Stop-PlaneStack` call in the recover/nuclear paths + `stack.ps1`'s `$Projects` registry. |
| 5 | **stack.ps1 health** | If the service has a meaningful probe (host port or exec-able health endpoint), add a `Probe` line to the `health` action. Uptime is watched remotely — probes are how absence gets noticed. |
| 6 | **StackWatchdog** | `scripts/checks/stack-watchdog.ps1`: add tailnet-serve rows if it gets a serve route; recovery hooks if the watchdog should repair it. (Its `Test-ServiceHealth` already falls back to container-name lookup for non-root projects.) |
| 7 | **Backups** | Stateful data ⇒ a backup sidecar IN THE SAME PROJECT (runbooks/backup-conventions.md): script in `backup/` (or the project's own `backup/` dir for submodules), sleep-loop for interval tars / supercronic for cron-timed dumps, sha256 sentinel, output under `./backups/<name>/`. Then: `scripts/sysadmin-mcp/check_backups.py` `_EXPECTED` row, `scripts/checks/check-backup-coverage.ps1` volume map, and a restore entry in BOTH `scripts/backup/restore-from-snapshot.ps1` (catalog) and `runbooks/restore-from-snapshot.md`. |
| 8 | **Sysadmin plane** | `scripts/lib/stack-services.json`: add the row with the correct `project` field (feeds the sysadmin MCP's stack_health/container tooling). If the service writes big logs, give it json-file caps in compose so the disk rotation stays boring. |
| 8a | **Profile?** | If the service sits behind a compose `profiles:` key, say so in FOUR places or it becomes invisible. (1) `scripts/lib/stack-services.json`: the row's `profile` field + the project's `profiles` list. (2) `stack.manifest.toml`: the `[planes.<plane>.profiles.<name>]` table, the product that pulls it (`profiles` for an engine the product needs, `surfaces` for something `--headless` may drop), and `requires` if the profile's service calls an engine that lives in ANOTHER profile — otherwise it comes up healthy and silently produces nothing. (3) `scripts/checks/check-project-configs.ps1`: the render target must pass the profile, or the coverage assertion fails the commit (it compares rendered containers against ALL inventory rows for the project, deliberately not against the profiles the target passes — deriving the expectation from the argument list makes the guard police itself). (4) **Who reaches it from another project**: `portal/config/caddy/Caddyfile`, `status-pipe/modules/`, `frontend/entrypoint.sh` routes. Profiled planes today: `inference` (`local`), `agent-org` (`workers`, `cloud`), `portal` (`internet`), `ob1` (`research`, `wiki`, `notebook`, `idea-refinery`). |
| 8b | **Who reaches the profiled service, really** | Enumerate cross-profile references from the **rendered** config (`docker compose … config --format json`, every profile on), matching each profiled service's name against every `environment` value — plus `depends_on`, `network_mode`, `links`, network aliases and shared named volumes. **A grep over the compose files under-reports**: values delivered by `env_file:` never appear in the compose text, and a service may hard-code the same URL as a default anyway. `sl-ob1-profiles` grepped, found two such references, wrote "two" into three documents, and its tester rendered and found six. A `depends_on` that crosses into a profile is a start-time failure and must be removed or made `required: false`; an environment URL that crosses is a *silent* failure and must at minimum be documented at its line with what goes dead. |
| 9 | **Status surfaces** | If operators should see it in OWUI's Server Status pipe: extend the relevant `status-pipe/` module (then re-paste per `status-pipe/README`). |
| 10 | **Docs** | Stack-map reference (`/stack-map` checks drift), `documentation/CONTAINER-REGISTRY.md` (purpose + why), CLAUDE.md counts if a project's size line changes. |
| 11 | **Deploy door (`build:` services)** | A service built from source (today: the OB1 integrations) is deployed by `scripts/stack/ob1-deploy.ps1 -Service <svc> [-Recreate <dependent,...>]`, never by `docker compose up -d` alone: the door refuses when OB1 on disk is not the gitlink, builds with `--build-arg OB1_SHA=<pin>`, force-recreates the named dependents, waits for healthy and refuses success over a restart loop (runbooks/UPDATE-MANAGEMENT.md "Everything else"). Give the Dockerfile `ARG OB1_SHA=` + `LABEL org.opencontainers.image.revision="$OB1_SHA"` (copy research-curator's) so the label is not empty, and a compose `healthcheck:` - without one the door can only wait for "running". |

## When you EXPOSE a service (portal or tailnet)

- **Portal (internet)**: the whole recipe lives at the top of
  `portal/config/caddy/Caddyfile` next to the `(authelia_gate)` snippet — app-net
  attach → vhost (copy the litellm one) → hub card → Cloudflare hostname
  (operator, dashboard) → validate + reload. Then re-accept the tripwire
  baseline (`docker exec integrity-tripwire sh /scripts/tripwire.sh accept`)
  or the next integrity check alerts on your own change.
- **Tailnet**: add a row to the data-driven route table in `entrypoint.sh`
  (name|enabled|host|port|ts_port|path|local_port|probe|attempts|verify) and
  rebuild/restart the tailscale container. Probe LiteLLM-fronted targets
  with `/health/liveliness`, never `/health` (thrash + 401).

## When you REMOVE a service

Everything above in reverse, plus the house rules: **archive, don't delete**
(`scripts/archive/` + its README provenance table); verify death against
gitignored evidence (`.env*`, live `webui.db`, `backup/models/`) before
declaring zero references; excise recovery/watchdog lines BLOCK-WISE (regex
sweeps have produced bare `docker compose up -d` lines that hit the whole
stack); keep old backup archives on the NAS even when the target is gone.

## When you CHANGE auth, networks, or volumes

- **Auth flips** (new key regime, master-key style changes): audit EVERY
  credential slot — the J.1 cutover missed OWUI's second connection and its
  RAG embedding key, and the tailscale entrypoint's unauthenticated probe.
  Grep configs AND the live DBs.
- **Volume moves**: data copied to the new project-prefixed volume; restore
  catalog + runbook updated in the same commit; old volume deleted only
  after an operator-approved soak.
- **Network moves**: check EXTERNAL consumers before removing any
  `ai-stack_*` network — OB1/portal/agent-org attach by literal name
  (`docker network inspect <net>` shows who is holding it).

## When you PROFILE-GATE a service

Four planes are profile-gated today: `frontend` (`stock` | `gpu` | `tailscale`,
2026-09-19), `portal` (`internet`), `agent-org` (`workers`, `cloud`) and OB1
(`idea-refinery`). A profiled service is INVISIBLE to anything that renders the
plane without its profile, and that is exactly how a checker starts checking
nothing. So, in the same commit:

- **Decide where the profile set comes from and say it out loud.** compose
  reads `COMPOSE_PROFILES` from the `--env-file`, and a `--profile` flag on the
  command line REPLACES that value rather than adding to it. `.env.example`
  carries the fresh-clone set; the operator's `.env` carries this host's. **A
  plane whose default profile set is not the operator's deployment needs a line
  in `.env` before the next `up`** — without it `up -d` starts whatever has no
  profile, quietly.
- **Every renderer needs the profiles.** `check-project-configs.ps1` renders the
  plane twice (default and profiled) and diffs the PROFILED render against
  `scripts/lib/stack-services.json`, because the default render is a subset and
  would stop covering the gated rows. `check-watchdog-repair-targets.ps1`
  renders with the project's own `env_file`, so it only resolves gated services
  on a host whose `.env` sets them — which is the right answer there: if the
  watchdog cannot start a container it claims to repair, that IS a failure.
  **Run it after editing a host's `.env`; it is the check that says whether the
  profile set is right.**
- **Naming a service on the command line does NOT reliably get you past an
  inactive profile.** It activates only THAT service's own profile. If the
  service you name references another one — `depends_on`, `network_mode:
  service:x`, `volumes_from`, `links` — and that one is behind a different
  profile, compose refuses to load the project at all and every verb exits 1
  with `no such service: x`. `--no-deps` does not help: the reference resolves
  at project load, before dependencies are considered. Measured on
  `frontend` 2026-09-19: `config|ps|up|stop|start|restart|rm tailscale` all
  exit 1 without `COMPOSE_PROFILES`, while the same verbs on `openwebui`
  succeed, because the gpu definition names nothing outside its own profile.
  So **a repair path that names a cross-referencing service needs the profile
  set present**, either in the host's `.env` or passed by the caller —
  `scripts/backup/restore-from-snapshot.ps1` does the latter for every profiled
  plane it drives (`internet`, `workers`, and now `gpu`+`tailscale`), because
  its catalog describes one host's deployment by design. A generic driver
  should NOT hard-code profiles — on `frontend` that would start a CUDA build
  and reserve a GPU on a `stock` host — it should CHECK and say so;
  `emergency-recovery.ps1`'s `Confirm-FrontendProfiles` is that shape.
- **Every OBSERVER needs a guard, and it must fail OPEN.** A probe or repair
  aimed at a service the deployment does not have must SKIP and say so, never
  FAIL and never repair. Decide from the rendered project
  (`docker compose -f <plane> --env-file .env config --services`) rather than
  parsing `.env` — that is compose's own answer after it has applied every
  source and precedence rule. If the render cannot be read, or the container is
  running while the render denies it, keep checking and log why: a checker that
  goes silent on its own uncertainty is worse than one that cries wolf. Live
  example: `Test-TailscaleDeployed` in `scripts/checks/stack-watchdog.ps1` and
  the matching skip in `scripts/stack/stack.ps1`'s `health` action.
- **Two definitions of one container is a legitimate shape, and compose polices
  it.** `frontend` has `openwebui` (gpu) and `openwebui-stock` (stock) sharing
  one `container_name` and one data volume, because compose can gate a SERVICE
  on a profile but cannot gate a FIELD — `build:` and
  `deploy.resources.reservations.devices` cannot be blanked by an unset
  variable. Activating both profiles is refused by `docker compose config`
  ("container name ... is already in use"), so the mistake is loud. A sidecar
  that must run in BOTH deployments (`openwebui-backup`) names both in
  `depends_on` with `required: false` — without it compose rejects the whole
  project whenever the other definition is off, and `required: false` does not
  weaken the wait on the one that IS active.

## The one-command checks

```powershell
.\scripts\stack\stack.ps1 health        # functional probes, all planes
powershell scripts\stack\ob1-deploy.ps1 -Service <svc> -WhatIfOnly   # what a build: deploy WOULD do
powershell scripts\checks\check-backup-coverage.ps1   # every byte has a sidecar
# pre-commit runs: secrets, line endings, gateway routing, compose+ps1 parse
```
