# coder — the little-coder control plane

Compose project **`coder`** (`coder/docker-compose.yml`), split out of the root
`ai-stack` project on 2026-08-21 (CLEANUP-PLAN Part K.4). Four services, two
project-owned networks (one internal, one bridge) plus the external anchor seam,
six volumes, one loopback host port.

This is the self-improving coding agent: a **control plane that decides** and a
**workspace plane that executes**, kept in separate containers on purpose. The
boundary between them is the safety surface, not an implementation detail.

Design reference: `documentation/implementation-guide/little-coder/Self-improving-little-coder-design.md`
(sections cited below). Source tree: `little-coder/` — all four images build from it.

---

## Services

| Service | Image | Role |
|---|---|---|
| `little-coder` | `little-coder:local` | **Control plane — DECIDES.** The daemon (HTTP `:8090`) that receives triggers, plans, and runs the inner loop, and owns all accumulated expertise. Prometheus metrics on `:9090`. |
| `open-terminal` | `little-coder-open-terminal:local` | **Workspace plane — EXECUTES.** `POST /execute` (`:8000`, API-key'd). Every build / test / git command the agent issues runs here, never in the control plane. |
| `lc-egress` | `little-coder-egress:local` | tinyproxy forward proxy (`:8888`), **default-deny by host**. The only route from this plane to the internet. |
| `little-coder-backup` | `alpine:3.21` | Sleep-loop sidecar; nightly tar of the state volumes into `../backups/little-coder`. |

### The control-plane / executor split (read this before changing anything)

`little-coder` decides; `open-terminal` executes. Concretely (design §3.3, §3.4):

- The agent's **only** execution path is `bash -> open-terminal -> git-proxy`.
  Upstream tools that would run commands locally and unrouted (`shell-session`,
  `browser`) are removed at image build, and `permission-gate` is `accept-all`
  so nothing prompts around the routing.
- `git` inside `open-terminal` **is** the git-proxy wrapper — the real binary is
  relocated to `/usr/bin/git.real`. There is no raw-git fallback, and
  `core.hooksPath` is baked at image build to an empty operator-owned directory,
  so whatever a hostile repo lands in any `.git/hooks/` is never executed by git.
  Both of those are hard, image-level properties.
- **`.git/config` protection is policy, not a mount — do not describe it as
  read-only.** There is no `:ro` mount anywhere near `.git`; the workspace volume
  is read-write in both containers. What exists is *partial closure* (design
  §3.3): git-proxy denies `git config` writes at the command level
  (`git_proxy.py`, `blocklist:config-write` — and its own denial message repeats
  the "read-only" phrasing, which is where that claim propagates from), plus a
  workspace-edge bash filter against the obvious direct-write bypasses
  (`>`/`>>`/`tee`/`cp`/`mv`/`sed -i`/…). The design records the residual
  explicitly: **`open-terminal` runs as root, so a `python -c '...write...'`, a
  base64-obfuscated path, or a renamed utility still reaches `.git/config`.**
  Full closure needs `CAP_DAC_OVERRIDE` dropped or a uid split, and is deferred.
  Acceptable for the current friendly-upstream workload; **not** a containment
  property to rely on for genuinely hostile repos.
- Both containers mount `little-coder-workspace`, so the agent **edits files
  directly** while **execution** stays inside the isolated plane. The volume
  crosses container uids, which is why both images set `git safe.directory` to
  `*` and write with `umask 000`.
- Pagers are neutralized at image level in `open-terminal` (`core.pager cat`,
  `PAGER`/`GIT_PAGER`/`MANPAGER=cat`, `LESS=-FRX`). A single `git log -n 10`
  used to hang a whole task before that.

The switch is `LC_ROUTE_EXEC` (default `1`). See gotcha 3 for why turning it off
is not the harmless fallback it looks like.

---

## Network and port posture

| Network | Compose name | Type | Carries |
|---|---|---|---|
| `lc-net` | `coder_lc-net` | **native, `internal: true`** | control plane <-> executor <-> egress. No internet. |
| `llm-net` | `ai-stack_llm-net` | **external**, owned by the root anchor, `internal: true` | inference via the `llama-cpp` alias on LiteLLM, and the inbound seam for OWUI's `little_coder` pipe. |
| `default` | `coder_default` | project-local bridge | `lc-egress`'s internet leg. |

**Host ports — the whole plane publishes exactly one:**

```
127.0.0.1:9091 -> little-coder:9090      # Prometheus metrics, loopback only
```

**Deliberately not exposed, and why:**

- **`little-coder:8090`** — the daemon's task/health API. It accepts work. It is
  reachable only from `lc-net` and `ai-stack_llm-net` peers; OWUI's pipe calls
  `http://little-coder:8090` over `llm-net` (`owui/pipes/little_coder.py`).
  Publishing it would put a task-execution endpoint on the host's TCP stack.
  Probe it with `docker exec little-coder curl -fsS localhost:8090/health`.
- **`open-terminal:8000`** — arbitrary command execution, API-key'd but still a
  shell. Never published. `stack-watchdog.ps1` probes it the only way that is
  left — `docker exec open-terminal curl … localhost:8000/health`
  (`Test-OpenTerminalHealth`).
  **`stack.ps1 health` does NOT probe it.** `open-terminal` appears in
  `scripts/stack/stack.ps1` exactly once, in the project registry's `Note`
  string; the coder probe curls `little-coder:8090/health`, and that endpoint
  reports only the daemon's own state — status, version, focus, queue depth,
  in-flight count (`little-coder/src/littlecoder/daemon.py`, `@app.get("/health")`).
  It never touches the executor. **So a dead `open-terminal` passes
  `stack.ps1 health` green.** Check it directly before trusting a green sweep,
  and see gotcha 1 for why the watchdog detects the failure but cannot repair it.
- **`lc-egress:8888`** — proxy only, reachable from `lc-net` alone.
- Note that `llm-net` is itself `internal: true`, so publishing a port on a
  service attached only to it would be inert anyway.

**Egress containment.** `open-terminal` carries
`HTTP(S)_PROXY=http://lc-egress:8888` with
`NO_PROXY=llama-cpp,little-coder,lc-egress,localhost,127.0.0.1`. Both of its
networks are internal, so the proxy is its *only* internet path, and `lc-egress`
permits only hosts matching `little-coder/docker/egress-allowlist.txt`
(`FilterDefaultDeny Yes`; `github.com` + `githubusercontent.com` shipped), with
`CONNECT` restricted to ports **443 and 22**.

`little-coder` itself has **no internet at all** — both of its networks are
internal and it carries no proxy variables.

---

## Volumes

Six named volumes, all `coder_little-coder-*` (migrated from the `ai-stack_*`
volumes at the K.4 split; data was copied, not recreated).

| Volume | Holds | Live state? | Backed up |
|---|---|---|---|
| `little-coder-journals` | the three journals + `audit.jsonl` (design §4) | **yes — accumulated, append-only** | yes |
| `little-coder-skill` | the artifact / skill library (§7) | **yes — accumulated** | yes |
| `little-coder-cohorts` | derived counters + repro corpora (§5) | **yes — accumulated** | yes |
| `little-coder-polyglot` | canonical Polyglot clone (§8) | **yes — accumulated** | yes |
| `little-coder-sessions` | pi session files, one per OWUI chat / CLI channel | **yes — per-session continuity** | yes |
| `little-coder-workspace` | the focused project clone, **shared with `open-terminal`** | project-scoped, re-clonable | **no, by design** |

The first four are the **expertise volumes**. `little-coder-workspace` is
different in kind — `/project` switching wipes and re-clones it (§12.3), so it is
intentionally excluded from backup; the exemption is recorded in
`scripts/checks/check-backup-coverage.ps1`.

`docker compose up -d --build` recreates containers but **preserves volumes**.
Treating any expertise volume as inside-container state silently wipes
accumulated expertise on the first rebuild.

Restore procedure: `documentation/runbooks/restore-from-snapshot.md` (the
tar-based services section).

---

## Bringing it up and down

Preferred — the workspace driver, which knows the dependency order and always
passes the single root `.env`:

```powershell
.\scripts\stack\stack.ps1 up coder
.\scripts\stack\stack.ps1 down coder
.\scripts\stack\stack.ps1 restart coder
.\scripts\stack\stack.ps1 status coder
.\scripts\stack\stack.ps1 health            # functional probes, all planes
```

Manual, **from the repository root**:

```powershell
docker compose -f coder/docker-compose.yml --env-file .env up -d
docker compose -f coder/docker-compose.yml --env-file .env ps
docker compose -f coder/docker-compose.yml --env-file .env down
```

Two different env mechanisms are in play and you need both:

- **`--env-file .env`** feeds `${...}` **interpolation in the compose file**,
  including the `${OPEN_TERMINAL_API_KEY:?...}` guard that makes a bare `up` fail
  loud instead of starting a keyless executor. It resolves relative to your
  **current directory** — which is why you run this from the repo root.
- **`env_file: ../.env`** in the service definitions injects variables **into the
  containers**. It resolves relative to the compose file, so it finds the root
  `.env` regardless of cwd.

Crash recovery (health gates, GPU repair, ordered restart across every project)
is `scripts/recovery/emergency-recovery.ps1`, not this file.

---

## Where it sits in the dependency order

`scripts/stack/stack.ps1` order — top to bottom on `up`, reversed on `down`:

```
anchor -> inference -> frontend -> memory -> search -> CODER -> ob1 -> agent-org
```

- **Needs first:** the root **anchor** (it creates `ai-stack_llm-net`; without it
  the external network reference fails outright) and the **inference** plane's
  `llm-gateway` answering on the `llama-cpp` alias.
- There is **no cross-project `depends_on`** — compose cannot express one. Order
  is enforced by `stack.ps1` / `emergency-recovery.ps1`, and the services retry
  until the gateway answers (same posture as OB1).
- **Inside** the plane, `little-coder` has
  `depends_on: open-terminal (service_healthy)`. `lc-egress` has **no**
  `depends_on` and nothing waits for it; tinyproxy starts fast and requests
  retry, but a very early clone can race it.
- **Nothing in the stack blocks on `coder`.** The watchdog classes the whole
  plane as auxiliary / non-critical. Taking it down does not take anything else
  down — but see gotcha 2 about images.

---

## Gotchas (each verified against this tree, 2026-08-28)

1. **Bare `docker compose` against the root project is a no-op — FIXED in the
   watchdog on 2026-08-28, still live elsewhere.** The root project has been a
   pure network anchor with **0 services** since K.5b
   (`docker compose --env-file .env config --services` at the root prints
   nothing), so any `docker compose <verb> <service>` without
   `-f <plane>/docker-compose.yml` silently does nothing — and it fails
   *silently*, because an un-redirected native stderr does not throw under
   `$ErrorActionPreference = "Stop"` in PS 5.1 and the old code never read
   `$LASTEXITCODE`.

   In `scripts/checks/stack-watchdog.ps1` this is **fixed**: every repair now
   routes through `Invoke-PlaneCompose`, which resolves the owning compose
   project from `scripts/lib/stack-services.json` (whose container→project rows
   are machine-verified against the rendered compose configs by the pre-commit
   `check-project-configs.ps1`). The four former call sites — L477
   `Repair-OpenTerminal`, L511 `Confirm-AuxiliaryContainer` (**generic**: 19 call
   sites including `little-coder`, `lc-egress` and `little-coder-backup`), and
   L612/L615 `Repair-LlamaCppEmbed` — were **22 dead self-heal paths** between
   2026-08-21 and 2026-08-28. `scripts/checks/check-watchdog-repair-targets.ps1`
   is the regression guard.

   The same pass fixed a second defect in the same half-migration: two call sites
   passed the compose SERVICE keys `redis` and `gateway` to a helper that looks
   containers up by NAME with `docker inspect`, so `search-redis` and
   `search-gateway` could never read healthy either.

   **`scripts/recovery/emergency-recovery.ps1` still has this defect class** — 16
   bare `docker compose` invocations, of which ~10 name a service against the
   root project. That is a separate item; what those calls do to that script's
   control flow has not been verified. Manual restart of this plane's executor
   remains
   `docker compose -f coder/docker-compose.yml --env-file .env up -d open-terminal`.
   The `stack.ps1 health` gap noted under `open-terminal:8000` above is also
   still open: the health sweep cannot see this container at all.

2. **agent-org consumes this plane's IMAGES, not its daemon.** `ao-worker-N` is
   `image: little-coder:local` with **no build stanza**, and `ao-ot-N` is
   `${AO_OTn_IMAGE:-little-coder-open-terminal:local}`. So a `--build` here
   retags `:local` and silently changes what the agent-org worker pool runs on
   its next recreate. Plain `up -d` does not rebuild an existing image; keep it
   that way unless the retag is what you mean.

   **And the coupling runs BOTH ways.** agent-org's `ao-git-egress` (profile
   `workers`) and `ao-egress` (profile `cloud`) each carry their own
   `build: {context: ../../little-coder, dockerfile: docker/Dockerfile.egress}`
   producing **`little-coder-egress:local`** — the exact tag this plane's
   `lc-egress` runs. So a `--build` in *agent-org* silently changes what
   `lc-egress` runs on its next recreate, including the egress allowlist that is
   this plane's blast-radius boundary. Neither project pins a digest and neither
   warns about the other.

   Conversely, agent-org does **not** call this plane's daemon — grepping
   `agent-org/` for `little-coder:8090`, `LC_DAEMON_URL`, `daemon_url` and
   `http://little-coder` finds no caller.

3. **`LC_ROUTE_EXEC=0` is not a harmless fallback.** It moves execution into the
   `little-coder` container, which has **no internet on either of its internal
   networks** and does **not** go through the git-proxy. You lose the egress
   allowlist and the git chokepoint at the same time. Leave it at `1`.

4. **`.env` defines `OPEN_TERMINAL_API_KEY` twice, with different values.**
   Compose takes the last one. Everything agrees today because the interpolation
   and the `env_file` injection read the same file in the same order — but
   rotating the key by editing only the first occurrence produces a 401 that
   looks like a code bug. `grep -n "^OPEN_TERMINAL_API_KEY=" .env` before you
   touch it.

5. **`LLAMACPP_API_KEY` has no default; `LC_LLAMA_API_KEY` does.** The compose
   file sets `LLAMACPP_API_KEY=${LC_LLAMA_API_KEY}` (bare) alongside
   `LC_LLAMA_API_KEY=${LC_LLAMA_API_KEY:-llama}`. With `LC_LLAMA_API_KEY` unset
   the agent boots with an **empty** bearer for the gateway — a 401 since the J.1
   master-key flip — while the sibling variable claims `llama`. Set
   `LC_LLAMA_API_KEY` explicitly in `.env`.

6. **A clone from a non-allowlisted forge fails like network flakiness.**
   `lc-egress` is `FilterDefaultDeny Yes` and only `github.com` /
   `githubusercontent.com` ship enabled, with `CONNECT` limited to 443 and 22.
   Add the host to `little-coder/docker/egress-allowlist.txt` and **rebuild**
   `lc-egress` — a restart is not enough, the allowlist is `COPY`d into the image.

7. **Config is a read-only bind mount read at boot.** `../little-coder/config` is
   mounted `:ro` at `/app/config`; editing `little-coder.config.yaml` or
   `models.json` on the host requires a container restart. Nothing watches them.

8. **Green healthchecks say nothing about reachability.** Both healthchecks curl
   `localhost` inside their own container (`:8000/health`, `:8090/health`), so
   they stay green while `llm-net` DNS, the gateway, or the virtual key is
   broken. `stack.ps1 health` is the functional probe.

9. **`LITTLE_CODER_NO_CTX_PROBE=1` is deliberate — but the compose comment
   next to it is stale about the number.** The 1.9.x llama-cpp provider probes
   the server's `/props` for the live context window; our `base_url` is the
   LiteLLM gateway, which does not forward it, so the probe fails silently on
   every task and the provider falls back to the window declared in
   `little-coder/config/models.json`. That declared value is **90000**, not the
   131072 the compose comment claims. `models.json`'s own `_comment` is explicit:
   the window must stay under the `qwen36-27b` per-request lane
   (`ctx-size / n_parallel` — 90000 for the 98304 lane since 2026-07-09b), and
   the **pre-2026-07-09 value 131072 was ABOVE the then-current 87552 lane, a
   latent overflow bug.** Skipping the probe is still right; do not "restore"
   131072 on the strength of the comment. Changing the window requires a
   `little-coder` restart (gotcha 7).

10. **The backup can succeed with no artifact.** `backup/little-coder-backup.sh`
    exits 0 when `/data` is missing or empty (a deliberate precheck), so
    "exited 0" is not proof of a tarball. `stack-watchdog.ps1` guards this by
    age-checking `./backups/little-coder` (`MaxAgeHours = 52`). Retention is
    count-based via `LC_BACKUP_RETAIN_COUNT` (default 2); the interval is
    `LITTLE_CODER_BACKUP_INTERVAL` seconds (default 86400, a sleep-loop rather
    than cron, because crond misses fire windows across Docker Desktop VM clock
    jumps).

11. **Do not `ConvertFrom-Json` this plane's rendered config in PS 5.1.**
    `docker compose ... config --format json` emits `HTTP_PROXY` and `http_proxy`
    in the same map; PowerShell's JSON parser is case-insensitive and dies with
    `DuplicateKeysInJsonString`. Read the YAML output instead.

12. **The three `:local` images have no registry path.** `little-coder:local`,
    `little-coder-open-terminal:local` and `little-coder-egress:local` are built
    from `little-coder/` and exist nowhere else — losing one means a rebuild, not
    a pull, and see gotcha 2 for who else that rebuild affects. (The backup
    sidecar is the exception: `alpine:3.21` is an ordinary upstream tag and does
    pull normally.)

---

## Where the docs and this compose file disagree

The compose file is the source of truth. These are live discrepancies at the time
of writing, listed so the next person does not "correct" the file to match a
stale summary. Where a wrong claim is repeated in several files, **every**
location is named — fixing one and leaving the others is how these survive.

Two things about this table's shape, learned the hard way:

- **The lists below are derived from a repo-wide sweep, not from memory.** Every
  earlier attempt at this table under-named, because it enumerated the locations
  someone already knew about. Before trusting a row, re-run the sweep in the
  plane's test plan (case 17b) — phrasings drift and new restatements get added.
- **`CLEANUP-PLAN.md`'s K.4 block (around lines 941–948) is the ancestor of most
  of these.** It is the Part K.4 execution record, and `CLAUDE.md`'s Pointers
  section calls it "the living cleanup/restructure plan". The stack-map
  blockquote, the `CLAUDE.md` row and the compose comments are all downstream
  restatements of it. **Fix the ancestor or they grow back.** (Line numbers here
  were accurate on 2026-08-28; grep the quoted phrase, not the number.)

| Claim | Where | Reality |
|---|---|---|
| "the 7 coder volumes" | **2 places**: `CLAUDE.md` (the Coder row); `CLEANUP-PLAN.md` K.4 — "7 volumes copied to coder_little-coder-*" (≈943, the **ancestor**) | **6.** `docker compose -f coder/docker-compose.yml --env-file .env config` renders `coder_little-coder-{journals,skill,cohorts,polyglot,sessions,workspace}`. |
| "expertise x5 + sessions + workspace" | **2 places**: `.claude/skills/stack-map/references/workspace-stacks.md` (≈188); `CLEANUP-PLAN.md` K.4 (≈944, the **ancestor** — same sentence as the row above, so one edit fixes both) | **4 expertise volumes** (journals, skill, cohorts, polyglot — design §3.6) plus sessions and workspace = 6. |
| "The five expertise volumes" | this compose file's own `volumes:` comment | Same as above: four. |
| The backup covers "the four expertise volumes" | **3 places**: this compose file's `little-coder-backup` comment (≈148); `backup/little-coder-backup.sh`'s header (≈3) — **the script that actually does the tarring**; and, inverted, `documentation/runbooks/backup-restore-runbook.md` (≈36) which calls it "volume tar (**5 expertise vols**)" | It tars **five volumes**, of which **four** are expertise volumes — `sessions` is the fifth and is not an expertise volume. The compose comment and the script header undercount the volumes; the restore runbook overcounts the *expertise* ones. The mounts win: `tar czf … -C /data .` takes whatever is under `/data`, and compose puts five volumes there. |
| `little-coder-backup` networks: "—" | stack-map table | It declares no `networks:`, so compose attaches it to `coder_default` (the internet-capable project bridge). |
| agent-org reaches this plane's **daemon** | **seven places**: this compose file's header comment (≈14) and its `lc-mcpo` retirement note (≈130); `CLEANUP-PLAN.md` K.4 — "OWUI pipe + agent-org reach the daemon there" (≈945, the **ancestor**); the stack-map's `coder` blockquote (≈189); and **three** rows in `documentation/CONTAINER-REGISTRY.md` — the `little-coder` row (≈71), the door table's "OWUI / agent-org → little-coder:8090" (≈211), and the retired-services row for `lc-mcpo`, "both real callers use the daemon directly" (≈232) | Only OWUI does. agent-org runs its own pooled worker pairs and depends on this plane's **images** (both directions — gotcha 2). Widened greps for `little-coder:8090`, `LC_DAEMON_URL`, `daemon_url` and `http://little-coder` across `agent-org/` find no caller. |
| `contextWindow` falls back to **131072**, "which is what we want anyway" | this compose file's `LITTLE_CODER_NO_CTX_PROBE` comment | `little-coder/config/models.json` declares **90000**, and its `_comment` records 131072 as a *latent overflow bug* (it exceeded the then-current 87552 lane). See gotcha 9. |
| "`.git/config`, `.git/hooks/`, `.git/info/` are **mounted read-only** to the agent" | **4 places**: design §3.3's headline sentence (≈118, the **ancestor** — though its own next paragraph retracts it); §13's threat-model line (≈470, which at least flags "enforcement gap noted in §3.3"); git-proxy's denial message (`git_proxy.py` ≈288, `blocklist:config-write`); and `little-coder/tests/test_git_proxy.py`'s docstring (≈3), which describes the mount as existing | There is no such mount — the workspace is read-write in both containers. §3.3's own "Enforcement status" paragraph downgrades this to *partial closure* with an explicit root-write residual. Treat the headline as intent, the residual as the fact. |

---

## Changing this plane

Adding, removing, or moving a container here is never a one-file edit. The full
checklist is `documentation/runbooks/SERVICE-LIFECYCLE.md`; at minimum it is this
compose file **plus** `scripts/recovery/emergency-recovery.ps1`
(`$Script:CoderServices`), `scripts/stack/stack.ps1` (the project registry and the
`health` probe), `scripts/checks/stack-watchdog.ps1`,
`scripts/checks/check-backup-coverage.ps1`, the backup/restore runbooks, and
`.claude/skills/stack-map/references/workspace-stacks.md`. Run `/stack-map` to
check for drift.

The routing rule applies here like everywhere else: **never route inference
around LiteLLM.** Both containers reach it through the `llama-cpp` alias on
`llm-net`; `scripts/checks/check-llm-gateway-routing.ps1` enforces this
pre-commit.
