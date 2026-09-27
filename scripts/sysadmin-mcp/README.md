# sysadmin-mcp — ai-stack systems-administrator

A dependency-free (stdlib-only) MCP server + gated executors that let an AI **systems-administrator
persona** operate this stack through semantic, safety-gated tools. Capability #1 is disk-prune
(motivated by the 2026-07-26 near-full-C: incident). Design:
[../../../documentation-plans-ai-stack/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md](../../../documentation-plans-ai-stack/implementation-guide/disk-prune-watcher/DESIGN-systems-administrator.md).

## Files
| File | Role |
|------|------|
| `server.py` | MCP stdio server (registered as `sysadmin` in repo `.mcp.json`) — 10 tools |
| `sysadmin.py` | read-only probes (disk_report, container/stack/logs/volume) |
| `executor.py` | gated reclaim (`reclaim_plan`/`reclaim_execute`): plan stored under its `confirm_token`, execute removes only that listed set with every item re-checked; idle+recency-guarded ao-worker `/tmp`, log truncation |
| `docker_reclaim.py` | the docker categories of that reclaim - unused image tags, build cache, orphaned ANONYMOUS volumes - under the rules in [Reclaim rules](#reclaim-rules-operator-decision-2026-09-27); every docker mutation passes one chokepoint (`_mutate`) |
| `image-keep.txt` | the keep-list: glob patterns (each with a reason) for image tags reclaim never removes |
| `auto_reclaim.py` | the AUTOMATIC docker reclaim: plan + execute in one run, one summary line out; called hourly by `scripts/maintenance/disk-guard.ps1` when C: is low |
| `compaction.py` | gated vhdx compaction (`compact_plan`/`compact_execute`/`compact_status`) |
| `compact-vhdx.ps1` | the elevated compaction body (runs as a RunLevel-Highest task) |
| `compact-lib.ps1` | pure decision helpers for the above (`Get-ReclaimVerdict`) — no elevation, no Docker, so the judgement is testable without 15 min of downtime |
| `test-compact-lib.ps1` | 17 checks on that verdict; case 1 is the real 2026-09-13 under-reclaim |
| `check_disk.py` | weekly detector → posts a `#sysadmin` alert when a threshold trips |
| `register-sysadmin-tasks.ps1` | ONE-TIME (elevated): registers the compaction task + weekly detector |
| `charter.md` | the @sysadmin persona charter (appended to the bridge's system prompt) |
| `sysadmin-bridge-launch.ps1` / `register-sysadmin-bridge.ps1` | run/register the persona (2nd bridge instance) |
| `config.json` | thresholds + machine facts + channel/operators |
| `test_*.py` | unit parsers + volume-age classification + live probes + stdio round-trip + fail-closed gates + source/ordering guards; `test_docker_reclaim.py` drives the reclaim against an in-memory fake docker (no daemon). Run ONLY in a container - see [Run the tests](#run-the-tests---in-a-disposable-container-only) |
| `_testguard.py` / `_testsite/sitecustomize.py` | test-only: the container check (exit 2 on a host) and the process-wide guard every test process and its Python children run |

## Tools (surface)
Read-only: `disk_report`, `container_status`, `stack_health`, `container_logs`, `volume_report`,
`reclaim_plan`, `compact_plan`, `compact_status`.
Gated/mutating: `reclaim_execute(confirm_token)`, `compact_execute(confirm_token)`.

## Gating (belt + suspenders)
1. **Belt (human):** in a bridge session, mutating `mcp__sysadmin__*` tools fall through the
   existing fail-closed approval relay (`--permission-prompt-tool`) → operator approve/deny.
2. **Suspenders (deterministic, in-code):** plan-bound `confirm_token` covering exactly the listed
   set, per-item re-validation at execute, idle+recency worker checks, a mutation chokepoint that
   admits only `image rm <tag|id>`, `volume rm <64-hex id>` and `builder prune -af --filter
   until=<N>h` (named volumes never; source-guarded by tests), compaction requires warranted +
   registered task.

## Run the tests - in a disposable container ONLY

**Rule (2026-09-27, after two incidents in which a sysadmin test reached the real host while the code
under test was broken: a host prune at 13:00, and a real VHDX compaction at 15:19):** every
sysadmin-mcp test, mutation and meta-test runs inside a disposable Linux container. The container has
no docker socket, no Windows tools and the code mounted read-only. There, `schtasks`, `wsl`,
`powershell.exe` and the host daemon do not exist, so a broken guard fails harmlessly. **The test
modules exit 2 on any host by design**: `_testguard.install()` requires POSIX, `/.dockerenv` and
`ACSR_IN_CONTAINER=1`.

From the repo root in Git Bash (`MSYS_NO_PATHCONV=1`; `<repo>` = this checkout's Windows path):

```sh
# every Python suite: no network, no socket, code read-only (copied to /tmp inside so state/ is writable)
docker run --rm --network none -e ACSR_IN_CONTAINER=1 -e DOCKER_HOST=tcp://127.0.0.1:1 \
  -e PYTHONDONTWRITEBYTECODE=1 -v "<repo>:/w:ro" python:3.12-slim sh -c '
  mkdir -p /tmp/w && cp -r /w/scripts /w/stack.manifest.toml /tmp/w/ && cd /tmp/w/scripts/sysadmin-mcp &&
  for t in test_docker_reclaim.py test_sysadmin.py test_executor.py test_compaction.py \
           test_check_backups.py test_telegram_listener.py; do python $t || exit 1; done'

# the PowerShell pure-logic tests
docker run --rm --network none -v "<repo>:/w:ro" mcr.microsoft.com/powershell:7.4-ubuntu-22.04 \
  pwsh -NoProfile -File /w/scripts/sysadmin-mcp/test-compact-lib.ps1
```

**LIVE sections against a real daemon** - only a disposable DinD, driven from a container that shares
a private network with it and has NO docker socket (never `-v /var/run/docker.sock`):
(the LIVE section expects at least one running container; the recipe starts a throwaway one)

```sh
docker network create acsr-net
docker run -d --rm --privileged --name acsr-dind --network acsr-net --network-alias dind \
  -e DOCKER_TLS_CERTDIR= docker:27-dind
docker run --rm --network acsr-net -e DOCKER_HOST=tcp://dind:2375 -e ACSR_IN_CONTAINER=1 \
  -v "<repo>:/w:ro" docker:27-cli sh -c '
  apk add --no-cache python3 >/dev/null && until docker info >/dev/null 2>&1; do sleep 2; done &&
  docker run -d --name llm-gateway alpine:3.21 sleep 3600 >/dev/null &&
  mkdir -p /tmp/w && cp -r /w/scripts /w/stack.manifest.toml /tmp/w/ && cd /tmp/w/scripts/sysadmin-mcp &&
  python3 test_sysadmin.py'
docker stop acsr-dind && docker network rm acsr-net
```

**Defence in depth: the in-code guard.** Every test module also installs `_testguard.py` at import,
and Python children started the ordinary way inherit it (`_testsite/sitecustomize.py`; `server.py`
honours `ACSR_TESTGUARD`). A child started with `-I` / `-E` / `-S`, or with a scrubbed `env=`, is NOT
covered; that is why the container is the barrier and the guard only defence in depth. What it does:
- `DOCKER_HOST` defaults to the dead endpoint `tcp://127.0.0.1:1` and is printed first.
- Every docker / wsl / schtasks / PowerShell / Windows-directory program start is checked against an
  EXACT read-only allowlist (hermetic suites allow none) and recorded with its pid in
  `ACSR_CALL_LOG`.
- Meta-tests run a canary child first and abort if that child is not guarded.

The module docstring lists every allowed shape.

Checks that need real Windows are skipped or faked in the container:
- test_telegram_listener's real PowerShell child test (SKIP);
- test_sysadmin's `C:` free-space probe (asserts the probe fails soft instead);
- `disk-guard.ps1` itself (its call to `auto_reclaim.py` is checked from source).

## Activate the weekly detector + arm compaction (one elevated run)
```
powershell -File scripts/sysadmin-mcp/register-sysadmin-tasks.ps1   # run elevated
```
Registers `AI-Stack Sysadmin Compact VHDX` (on-demand, RunLevel Highest) and
`AI-Stack Sysadmin Disk Check` (weekly Sunday 09:00).

## Activate the @sysadmin persona (operator prerequisites)
1. Create a Mattermost bot `bot-sysadmin`; add its token to `agent-org/docker/.env` as
   `SYSADMIN_MM_BOT_TOKEN=...`.
2. Create the `#sysadmin` channel; add bot-sysadmin + the operator; put its 26-char id in
   `config.json → sysadmin_channel_id`.
3. Run elevated: `powershell -File scripts/sysadmin-mcp/register-sysadmin-bridge.ps1`, then
   `schtasks /run /tn sysadmin-bridge`.

## Compaction: trim first, then judge the result (2026-09-13)
- **`fstrim` runs BEFORE the engine stop**, while the docker-desktop distro is up and the disk
  mounted. `Optimize-VHD` cannot read ext4 — it reclaims only blocks the guest has already
  discarded, and `/mnt/docker-desktop-disk` is mounted `rw,relatime` with no `discard`. Without
  the trim, a run returns whatever Docker Desktop's own periodic trim happened to mark: on
  2026-09-13 that was **9.9 GB of a measured 54.4 GB**, reported as success.
- **The result is judged against its own target.** `trapped_before_gb`, `fstrim_ok` and
  `shortfall_gb` are result fields, and `Get-ReclaimVerdict` sets `ok=false` with a named reason
  when the return misses proportionally (`-MinReclaimFraction`, default 0.5) *and* by more than
  `-ShortfallGraceGb` (default 5). Both conditions are required so a small target missed by a
  small amount is not an incident.
- **WARN and ACT are different numbers.** `vhdx_trapped_warn_gb` (60) decides when `disk_report`
  mentions compaction; `vhdx_compact_min_gb` (20) decides when `compact_execute` will run. They
  were the same key until 47.8 GB trapped left the stack simultaneously "HEALTHY" and refused.

## Reclaim rules (operator decision 2026-09-27)
`reclaim_plan` lists, and `reclaim_execute` removes, ONLY:
- **Image tags** where ALL hold: no container, running or stopped, uses the image id; no plane's
  compose render names the `repo:tag` (every plane in `stack.manifest.toml` - OB1 and agent-org
  included - rendered with `--profile *`; a `repo@sha256:` reference protects the image carrying
  that digest); the image was created 14 days ago or more (`thresholds.image_min_age_days`); the
  tag matches no pattern in `image-keep.txt`. An image id with a protected tag (compose or
  keep-list) is never removed through another tag. Untagged images (dangling, or pulled by digest
  only) are removed by id when no container uses them and no compose render pins them by digest;
  docker CLI 29 hides them from a plain `docker images`, so the inventory adds `docker images
  --filter dangling=true`. Removal is `docker image rm <repo:tag>` (or `<id>`), never with `-f`,
  never `image prune -a`.
- **Build cache** older than 168h: `docker builder prune -af --filter until=168h`
  (`thresholds.builder_keep_hours`), so the last week of cache stays for rebuilds.
- **Anonymous volumes** where ALL hold: the name is 64 lowercase hex and no compose render
  names a volume by that name (a render keeps a top-level volume only when a service uses it, so
  a declared-but-unused 64-hex name, or one made by hand with `docker volume create`, counts as
  anonymous); no container, running or stopped, references it - checked per volume with `docker ps -a --filter volume=<id>` on top of
  every container's mounts; CreatedAt is 7 days ago or more (`thresholds.anon_volume_min_age_days`).
  Removal is `docker volume rm <id>`, one id at a time. **Named volumes are never removed** - they
  are listed as skipped, report-only.

The plan shows each category's count, estimated bytes and largest entries, and every skip with its
reason (in use / compose-named / too new / keep-list / shares id with a protected tag / named
volume). Its `confirm_token` is a hash of the listed set; the plan is stored under it in
`state/reclaim-plans/` for `thresholds.reclaim_plan_ttl_hours` (24). Execute re-checks every
listed item and SKIPS anything now in use, re-pointed or gone; items
that became eligible after the plan are not touched. Freed bytes are reported per category from
`docker system df` before/after. If the container list, the keep-list (missing, unreadable or
without a single pattern) or ANY compose render cannot be read, the affected category removes
nothing and the plan says why. At execute every threshold is the stricter of the stored plan's and
`config.json`'s, so a hand-edited plan file cannot lower one.

**Container logs** are truncated only when a FRESH scan at execute still finds them oversized AND
their path is exactly `<mount>/data/docker/containers/<id>/<id>-json.log`; any other path a stored
plan lists is reported in `skipped_logs`, never touched. The confirm token is an unkeyed hash:
integrity only, not authentication, so no stored-plan content is trusted without re-checking.

Space freed this way is freed inside the Docker vhdx: C: gets it back only at the next compaction.

The same docker reclaim runs **automatically** from the hourly `AI-Stack Disk Guard` task
(`scripts/maintenance/disk-guard.ps1` -> `auto_reclaim.py`) when C: free is under its warn line;
its #sysadmin alert carries the per-category freed bytes. `python auto_reclaim.py --plan` prints
the listed set without removing anything.

## Safety notes
- Never `docker volume prune`, `docker image prune -a` or `docker system prune`; never a named
  volume. `volume_report` is report-only and flags protected data volumes.
- **A protected name is not proof of life.** `volume_report` splits `DO_NOT_PRUNE` (protected and
  recently written, or of unknown age) from `dangling_protected_cold` (protected name, no
  container references it, nothing written for `volume_orphan_cold_days`+). Cold entries are
  orphan *candidates*: verify contents against the live volume and back up before removing. If
  the age probe fails, nothing is classified cold — the conservative side.
- Reclaim clears only IDLE ao-worker `/tmp/lc-*.jsonl` (busy workers skipped); logs are truncated,
  not deleted; images, build cache and anonymous volumes follow [Reclaim rules](#reclaim-rules-operator-decision-2026-09-27).
- Compaction takes the whole stack down ~10–15 min; it pauses/re-arms the health watchdog and
  verifies the stack returns. Only run in a quiet window (no active ao-worker effort).
- Runtime state (audit log, stored reclaim plans, compaction result, alert throttle) lives in `state/` (gitignored).
