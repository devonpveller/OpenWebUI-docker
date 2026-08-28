# coder — the little-coder control plane

Compose project **`coder`** (`coder/docker-compose.yml`), split out of the root
`ai-stack` project on 2026-08-21 (CLEANUP-PLAN Part K.4). Four services, one
plane-native network, six volumes, one loopback host port.

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
  relocated to `/usr/bin/git.real`. There is no raw-git fallback. `.git/config`,
  `.git/hooks/` and `.git/info/` are read-only to the agent, and
  `core.hooksPath` is baked to an empty operator-owned directory so a hostile
  repo's hooks are never executed.
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
  shell. Never published; `stack-watchdog.ps1` and `stack.ps1 health` both probe
  it via `docker exec` for exactly this reason.
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
| `little-coder-cohorts` | derived counters + repro corpora (§5) | derived, rebuildable | yes |
| `little-coder-polyglot` | canonical Polyglot clone (§8) | rebuildable | yes |
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

1. **`docker compose up -d open-terminal` from the repo root does nothing.** The
   root project has been a pure network anchor with **0 services** since K.5b —
   `docker compose --env-file .env config --services` at the root prints nothing.
   `Repair-OpenTerminal` in `scripts/checks/stack-watchdog.ps1` still issues
   exactly that command, so the watchdog's open-terminal self-heal is a no-op.
   Restart it with
   `docker compose -f coder/docker-compose.yml --env-file .env up -d open-terminal`.
   *(Not fixed here — this was a documentation-only change.)*

2. **agent-org consumes this plane's IMAGES, not its daemon.** `ao-worker-N` is
   `image: little-coder:local` with **no build stanza**, and `ao-ot-N` is
   `${AO_OTn_IMAGE:-little-coder-open-terminal:local}`. So a `--build` here
   retags `:local` and silently changes what the agent-org worker pool runs on
   its next recreate. Plain `up -d` does not rebuild an existing image; keep it
   that way unless the retag is what you mean. Conversely, agent-org does **not**
   call `little-coder:8090` — grep finds no such caller.

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

9. **`LITTLE_CODER_NO_CTX_PROBE=1` is deliberate.** The 1.9.x llama-cpp provider
   probes the server's `/props` for the live context window; our `base_url` is the
   LiteLLM gateway, which does not forward it. The probe fails silently on every
   task and falls back to `models.json`'s declared 131072 — which is the value we
   want anyway. The flag just removes the futile round trip and its log noise.

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

12. **Images are `:local` with no registry path.** There is nothing to pull. A
    lost image means a rebuild from `little-coder/` — and see gotcha 2 for who
    else that affects.

---

## Where the docs and this compose file disagree

The compose file is the source of truth. These are live discrepancies at the time
of writing, listed so the next person does not "correct" the file to match a
stale summary:

| Claim | Where | Reality |
|---|---|---|
| "the 7 coder volumes" | `CLAUDE.md` | **6.** `docker compose -f coder/docker-compose.yml --env-file .env config` renders `coder_little-coder-{journals,skill,cohorts,polyglot,sessions,workspace}`. |
| "expertise x5 + sessions + workspace" | `.claude/skills/stack-map/references/workspace-stacks.md` | **4 expertise volumes** (journals, skill, cohorts, polyglot — design §3.6) plus sessions and workspace = 6. |
| "The five expertise volumes" | this compose file's own `volumes:` comment | Same as above: four. |
| "Nightly backup of the four little-coder expertise volumes" | this compose file's `little-coder-backup` comment | It mounts **five** — the four expertise volumes **and** `sessions`. The mounts win. |
| `little-coder-backup` networks: "—" | stack-map table | It declares no `networks:`, so compose attaches it to `coder_default` (the internet-capable project bridge). |
| "OWUI's `little_coder` pipe **and agent-org** reach the daemon over [llm-net]" | this compose file's header comment | Only OWUI does. agent-org runs its own pooled worker pairs and depends on this plane's **images**, not its daemon. |

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
