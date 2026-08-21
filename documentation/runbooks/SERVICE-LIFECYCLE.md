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
| 9 | **Status surfaces** | If operators should see it in OWUI's Server Status pipe: extend the relevant `status-pipe/` module (then re-paste per `status-pipe/README`). |
| 10 | **Docs** | Stack-map reference (`/stack-map` checks drift), `documentation/CONTAINER-REGISTRY.md` (purpose + why), CLAUDE.md counts if a project's size line changes. |

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

## The one-command checks

```powershell
.\scripts\stack\stack.ps1 health        # functional probes, all planes
powershell scripts\checks\check-backup-coverage.ps1   # every byte has a sidecar
# pre-commit runs: secrets, line endings, gateway routing, compose+ps1 parse
```
