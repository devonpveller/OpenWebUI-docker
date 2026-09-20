---
name: stack-map
description: |
  Identify and map the Docker stacks, compose projects, container planes,
  networks, and ports in this ai-stack workspace. Use when the user asks
  what stacks or containers exist, what is deployed, where a service runs,
  how the stacks connect, or wants a topology / inventory. Also use BEFORE
  editing a docker-compose file or the emergency-recovery scripts, so changes
  stay in sync with the real stack layout.
author: ai-stack
version: 1.0.0
---

# Stack Map

## Problem

This workspace runs **nine separate Docker Compose projects** plus a recovery
layer. New containers are added in waves (memory layer, search gateway,
little-coder plane, Open Brain), and it is easy to miss one — which silently
breaks the recovery scripts and confuses anyone reasoning about the system.

This skill produces a correct, current map of every stack and keeps that map
honest against the real compose files.

## Trigger Conditions

Invoke when the user:

- asks what stacks / containers / services exist, or what is deployed
- asks where a service runs, what network it is on, or what port it uses
- asks how the stacks connect or for a topology / inventory / diagram
- is about to edit any `<plane>/docker-compose.yml`, `stack.manifest.toml`, or
  the `scripts/recovery/emergency-recovery.ps1` script (verify the map first)
- reports a container that "isn't covered" by recovery or backups

## The nine compose projects

Since Part K (2026-08-21) the workspace is **one compose project per plane**,
around a root project that owns only the shared networks. The authoritative
list is `stack.manifest.toml`, and `python scripts/stack/stack.py list` prints
it.

1. **`ai-stack`** — `docker-compose.yml`: the network ANCHOR. **Zero
   services**; it owns `ai-stack_llm-net` / `app-net` / `default`, which every
   other project attaches to `external: true`.
2. **`frontend`**, 3. **`inference`**, 4. **`memory`**, 5. **`search`**,
   6. **`coder`**, 7. **`portal`** — `<plane>/docker-compose.yml`, each with
   its own `.env`, `.env.example` and `README.md`.
8. **`open-brain`** — `OB1/docker/docker-compose.yml` (a pinned submodule).
9. **`agent-org`** — `agent-org/docker/docker-compose.yml`.

Two concerns are not compose projects: the **driver**
(`scripts/stack/stack.py`, which reads the manifest; `stack.ps1` is a shim) and
the **recovery stack** (`scripts/recovery/emergency-recovery.ps1`).

## Process

1. **Read the source of truth.** Start at `stack.manifest.toml` for the plane
   list, then open each plane's compose file (`inference/` also `include:`s
   four files under `inference/compose/`). **Render rather than grep**, and
   pass every profile: `docker compose -f <plane>/docker-compose.yml --profile
   <each> config --services` — a profiled service is invisible to a default
   render, and that is how a checker starts checking nothing. Enumerate every
   `services:` entry, its `container_name`, `networks`, published `ports`,
   `depends_on`, `profiles` and `volumes`.
2. **Group by plane**, one per compose project. See the quick map below and the
   full table in
   [references/workspace-stacks.md](references/workspace-stacks.md); each
   plane's own README carries the detail.
3. **Diff against the curated reference.** If the live compose files contain a
   service the reference does not (or vice versa), the reference is stale —
   report the drift and offer to update
   [references/workspace-stacks.md](references/workspace-stacks.md).
4. **Optional live state.** If Docker is available and the user wants current
   status, run `python scripts/stack/stack.py status --all` (one `ps` per
   plane, in dependency order) and, for the generated inventory,
   `python scripts/stack/stack.py inventory --check`.
5. **Report** grouped by stack and plane: container, role, network(s), host
   port. Call out anything internal-only (no host port) and any container
   missing from the recovery scripts' inventory.

## Quick map

| Project | Containers |
|---------|-----------|
| `ai-stack` (anchor) | **none** — networks only |
| `frontend` | `openwebui` (service `openwebui` under `gpu`, `openwebui-stock` under `stock`), `tailscale` `[tailscale]`, `openwebui-backup`, `tailscale-backup` `[tailscale]` |
| `inference` | `llm-gateway`, `llm-gateway-db`, `llm-gateway-ui`, `llm-gateway-backup`, and under `[local]`: `llama-cpp-upstream`, `llama-cpp-embed-upstream`, `llm-queue`, `lm-models-backup` |
| `memory` | `mnemory`, `mnemory-cloud-gateway`, `mnemory-backup` |
| `search` | `search-vpn`, `search-redis`, `searxng`, `search-gateway` |
| `coder` | `open-terminal`, `little-coder`, `lc-egress`, `little-coder-backup` |
| `portal` | `portal-init`, `caddy`, `authelia`, `portal-alerter`, `authelia-watcher`, `authelia-notif-bridge`, `integrity-tripwire`, `portal-cron`, `caddy-backup`, `authelia-backup`, and under `[internet]`: `cloudflared`, `tunnel-watcher` |
| `open-brain` | 30 with every profile (`openbrain-*` fleet + backups + the Open Notebook trio) |
| `agent-org` | `mattermost`, `mattermost-db`, `agent-bridge`, `agent-bridge-db`, 2 db backups, and the `[workers]` / `[cloud]` slices |
| Driver / recovery (not compose projects) | `scripts/stack/stack.py` (+ the `stack.ps1` shim), `scripts/recovery/emergency-recovery.ps1` |

Retired, and NOT to be re-added to this map: `watchtower`, `tor` (superseded by
the Mullvad `vpn`), `search-mcpo`, `lc-mcpo`, `smolcrawl-pipelines`, and the
bare `llama-cpp` / `llama-cpp-embed` container names — those are now network
ALIASES on `llm-gateway`, not containers.

## Consistency rule

When a container is added to or removed from a compose file, these must change
together:

1. the plane's compose file,
2. `stack.manifest.toml` — the `[planes.*]` table, its `ports` and `profiles`,
3. the recovery script's per-plane inventory
   (`$Script:<Plane>Services` in `emergency-recovery.ps1`),
4. `scripts/lib/stack-services.curated.json`, followed by
   `python scripts/stack/stack.py inventory --write`,
5. [references/workspace-stacks.md](references/workspace-stacks.md) and the
   plane's own `README.md`.

If you change one, flag the rest. The FULL checklist — backups, watchdog,
health probe, restore catalog, registry — is
`documentation/runbooks/SERVICE-LIFECYCLE.md`.

## Output

A grouped inventory the reader can act on: which stacks exist, which compose
project each container belongs to, how to drive each project, and any drift
between the compose files, the recovery scripts, and the reference doc.

## Notes

- The map is nine projects, not one — a plain `docker compose` command at the
  repo root touches the ANCHOR, which owns no services, so it starts nothing.
- `modules/emergency-recovery/` is a separate OWUI guidance module and is
  currently stale (its config still references the disabled `ollama`
  container). It is not part of the live recovery path.
- `tailscale` has no network of its own — it shares `openwebui`'s namespace
  via `network_mode: service:openwebui`.
