# CUT-IN-READY — the transparent flip (T3)

**Status (2026-06-12):** Standup (T2) is **live and verified**. `llm-gateway` +
`llm-gateway-db` are running **alongside** the untouched `llama-cpp` /
`llama-cpp-embed`, permissive, with **no aliases** — nothing routes through the
gateway yet. This doc is the reviewed, ready-to-execute package for the cut-in.
**Operator wants to be present for the flip** — do not execute it autonomously.

Anchored to guide **§1A** (architecture) and **§1A.8** (reversible live canary).

## The principle (what changes vs what stays)

The flip renames the **real** inference servers to `*-upstream` and gives the
**gateway** the `llama-cpp` / `llama-cpp-embed` network aliases. Therefore:

- **Inference callers STAY** on `http://llama-cpp:8080` / `http://llama-cpp-embed:8080`
  — they now resolve to the gateway. **Do not touch** their base-URL env / config
  (mnemory env URLs, OB1 callers, little-coder config, OWUI, githelper, recipes).
- **Everything that must watch the REAL server FOLLOWS it to `*-upstream`**:
  health probes, GPU diagnostics, tailscale-serve, recovery scripts, and every
  `depends_on:` (compose errors on a dangling rename).

Rule of thumb for each hit: *is this emitting inference, or observing/managing the
server?* Inference → leave it. Observe/manage/`depends_on` → rename to `*-upstream`.

## Pre-flight checklist (do immediately before the flip)

1. **Maintenance window** — the recreate briefly interrupts ALL inference
   (seconds). Confirm no long job in flight (wiki recompile, batch extraction).
2. **Gateway healthy now:** `docker inspect --format '{{.State.Health.Status}}' llm-gateway` → `healthy`.
3. **Fresh backups** (the flip is git-revertible and touches no caller data, but
   take them anyway): run the on-demand backup sidecars —
   `docker exec openwebui-backup sh /scripts/backup.sh` (and mnemory / OB1 db as
   desired). Backups taken hours ago are fine; the flip doesn't write caller data.
4. **Branch:** confirm on `feature/litellm-proxy`, clean tree.

## Part A — mandatory flip edits (compose + gateway config)

### A1. `docker-compose.yml` — rename the two real servers

| Edit | From | To |
|---|---|---|
| service key (line ~249) | `  llama-cpp:` | `  llama-cpp-upstream:` |
| its `container_name` (~251) | `container_name: llama-cpp` | `container_name: llama-cpp-upstream` |
| service key (~465) | `  llama-cpp-embed:` | `  llama-cpp-embed-upstream:` |
| its `container_name` (~467) | `container_name: llama-cpp-embed` | `container_name: llama-cpp-embed-upstream` |

Everything else in those two blocks (image, GPU `device_ids`, ports
`127.0.0.1:8081`/`8082`, volumes, internal healthcheck on `localhost:8080`) stays
— the internal healthcheck moves with the container automatically.

### A2. `docker-compose.yml` — `llm-gateway` gets the aliases + upstream deps

```yaml
  llm-gateway:
    networks:
      llm-net:
        aliases:
          - llama-cpp           # callers' http://llama-cpp:8080 land here
          - llama-cpp-embed     # callers' http://llama-cpp-embed:8080 land here
    depends_on:                 # was llama-cpp / llama-cpp-embed
      llama-cpp-upstream:
        condition: service_healthy
      llama-cpp-embed-upstream:
        condition: service_healthy
      llm-gateway-db:
        condition: service_healthy
```
(Change `networks: [- llm-net]` to the mapping form above to carry `aliases`.)

### A3. `docker-compose.yml` — repoint every other `depends_on` (rename would dangle)

These are startup-ordering only (not inference). Point them at the gateway so
they wait for the full inference chain (gateway → upstreams):

| Service | `depends_on` line(s) | From | To |
|---|---|---|---|
| `open-terminal` | ~198 | `llama-cpp` | `llm-gateway` |
| `mnemory` | ~348, ~350 | `llama-cpp` + `llama-cpp-embed` | `llm-gateway` (single entry) |
| `little-coder` | ~910 | `llama-cpp` | `llm-gateway` |

> OB1 services (curator/research/mcp/entity-worker/wiki/suggestion/chunk/workbench)
> are a **separate** compose project on the external `ai-stack_llm-net` — they
> have **no** `depends_on` here and need **no** change; they resolve `llama-cpp`
> (→ gateway) at runtime. This is exactly why transparent interposition was chosen.

### A4. `docker-compose.yml` — tailscale serve env (observability of the real server)

The `tailscale` service env (lines ~125 / ~128) feeds `entrypoint.sh`, which
socat-proxies the tailnet `/llama-cpp` path to this host and probes its `/health`.
The gateway has no `/health` (only `/health/liveliness`), so this must follow the
real server:

| Line | From | To |
|---|---|---|
| ~125 | `LLAMA_CPP_HOST=${LLAMA_CPP_HOST:-llama-cpp}` | `…:-llama-cpp-upstream}` |
| ~128 | `LLAMA_CPP_EMBED_HOST=${LLAMA_CPP_EMBED_HOST:-llama-cpp-embed}` | `…:-llama-cpp-embed-upstream}` |

### A5. `config/litellm.config.yaml` — api_base → the renamed upstreams

All **7** `api_base` lines (else the gateway loops onto its own alias):

| From | To |
|---|---|
| `api_base: http://llama-cpp:8080/v1` (×4 chat) | `http://llama-cpp-upstream:8080/v1` |
| `api_base: http://llama-cpp-embed:8080/v1` (×3 embed) | `http://llama-cpp-embed-upstream:8080/v1` |

Also flip the standup comment ("STANDUP FORM … no aliases") to note the cut-in is applied.

## Part B — observability / recovery (SAME commit; three-place rule)

Mechanical rename of **container-name references** (not inference URLs):
`llama-cpp` → `llama-cpp-upstream`, `llama-cpp-embed` → `llama-cpp-embed-upstream`.

| File | Lines | What |
|---|---|---|
| `modules/system-health/service/system_health.py` | 38, 39, 93 | probe `host` + `expected_services` → `*-upstream` |
| `modules/gpu-status/service/gpu_status.py` | 239, 240, 315–318 | container map + `api` URL → `*-upstream` |
| `scripts/status_check.py` | (llama-cpp probes) | probe host/URL → `*-upstream` |
| `scripts/gpu_check.py` | (llama-cpp refs) | → `*-upstream` |
| `scripts/check-tailscale-health.ps1` | 148–149, 313–326 | serve-health `Name` + `Test-ServiceHealth` + `docker compose up -d llama-cpp` → `*-upstream` |
| `scripts/check-backup-coverage.ps1` | 60 | `Service='llama-cpp'` → `'llama-cpp-upstream'` |
| `scripts/emergency-recovery.ps1` / `.bat` | inventory + startup/shutdown order | rename to `*-upstream` **and ADD** `llm-gateway`, `llm-gateway-db`, `llm-gateway-backup` (order: `*-upstream` healthy → `llm-gateway-db` → `llm-gateway` → callers) |
| `docker-compose.yml` (lm-models-backup) | ~1261 | `HEALTH_TCP=…llama-cpp:8080` → `llama-cpp-upstream:8080` |
| `entrypoint.sh` (fallback defaults) | 233, 304 | optional — compose env (A4) is authoritative; update fallbacks for consistency |

**Leave alone (inference callers / wildcard):** `mnemory` env URLs (~331/333),
lm-models-backup is fine, `little-coder/config/*` (its base_url → gateway is the
point), `filters/githelper-pipe.py`, all `OB1/**` caller env, and
`scripts/breach-killswitch.ps1:82` (its `llama-cpp*` wildcard already matches
`llama-cpp-upstream`).

## Apply (the canary — guide §1A.8)

```powershell
# 0. pre-flight checklist above is satisfied; window open.
# 1. free the real-server names + GPUs (while compose still knows them):
docker compose stop llama-cpp llama-cpp-embed
docker compose rm -f llama-cpp llama-cpp-embed
#    >>> APPLY all Part A + Part B edits now <<<
# 2. commit the cut-in (restore point):
git add -A
git commit -m "[litellm] CUT-IN: transparent flip — gateway takes llama-cpp/-embed aliases"
# 3. bring it up: creates *-upstream, recreates llm-gateway w/ aliases + new api_base:
docker compose up -d --remove-orphans
```
Between step 1 and step 3 completing, inference is briefly down (the window).

## Verify (watch live)

- `docker compose ps` — `llama-cpp-upstream`, `llama-cpp-embed-upstream`,
  `llm-gateway` all healthy; old `llama-cpp`/`-embed` gone.
- **Ledger fills from real callers:** wait a few min, then
  `docker exec llm-gateway-db psql -U litellm -d litellm -c 'SELECT api_key, count(*) FROM "LiteLLM_SpendLogs" GROUP BY 1 ORDER BY 2 DESC;'`
  — expect multiple caller keys (the junk strings: `ollama`, `not-needed`, `llama`, …).
- **No bypass:** `docker logs llama-cpp-upstream --tail 50` shows the gateway as
  its only client.
- **Spot-check a caller:** an OWUI chat, an mnemory search, an OB1 research/extract.
- **Observability:** `system health` / `gpu status` pipes resolve `*-upstream` and pass.
- Nudge any caller stuck on a stale connection pool: `docker restart <caller>` (no config change).

## Rollback (invisible to callers)

```powershell
git revert --no-edit <cut-in-sha>
docker stop llama-cpp-upstream llama-cpp-embed-upstream
docker rm llama-cpp-upstream llama-cpp-embed-upstream
docker compose up -d --remove-orphans
```
The originals reclaim `llama-cpp` / `llama-cpp-embed`; the gateway returns to the
standup form (no aliases). Callers never changed, so they're none the wiser.
**Worst case** for any harder problem: this same revert — regroup afterward.

## After a clean soak (later, additive — NOT part of the flip)

- **Lazy keys (guide §1A.4):** per caller, on your schedule, change only its key
  env to a distinct string → clean per-caller ledger buckets. URL never changes.
- **Pipe module** (`llm-traffic`, guide §9) + capacity views (§15).
- Optional end-state: enable `master_key` + virtual keys + caps once all callers carry keys.
