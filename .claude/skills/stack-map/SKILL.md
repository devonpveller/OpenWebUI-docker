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

This workspace runs **two separate Docker Compose projects** plus a recovery
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
- is about to edit `docker-compose.yml`, `OB1/docker/docker-compose.yml`, or
  the `scripts/recovery/emergency-recovery.*` scripts (verify the map first)
- reports a container that "isn't covered" by recovery or backups

## The two compose projects

1. **`ai-stack`** — `docker-compose.yml` (network anchor + aux; plane projects split out under Part K, 2026-08-21).
   Driven with plain `docker compose ...`. Holds the core, memory, search,
   coder, and aux planes.
2. **`open-brain`** — `OB1/docker/docker-compose.yml`. Driven with
   `docker compose -f OB1/docker/docker-compose.yml ...`. Attaches to the
   main stack's `ai-stack_llm-net` as an **external** network, so it depends
   on the main stack and is recovered last.

A third concern — the **recovery stack** (`scripts/recovery/emergency-recovery.ps1` /
`.bat`) — is not a compose project; it orchestrates both of the above.

## Process

1. **Read the source of truth.** Open `docker-compose.yml`,
   the `<plane>/docker-compose.yml` projects, and `OB1/docker/docker-compose.yml`.
   Enumerate every `services:` entry, its `container_name`, `networks`,
   published `ports`, `depends_on`, and `volumes`.
2. **Group by plane.** Map each container to a plane: core, memory, search,
   coder, aux (main project) or the open-brain project. See the quick map
   below and the full table in
   [references/workspace-stacks.md](references/workspace-stacks.md).
3. **Diff against the curated reference.** If the live compose files contain a
   service the reference does not (or vice versa), the reference is stale —
   report the drift and offer to update
   [references/workspace-stacks.md](references/workspace-stacks.md).
4. **Optional live state.** If Docker is available and the user wants current
   status, run `docker compose ps` and
   `docker compose -f OB1/docker/docker-compose.yml ps`.
5. **Report** grouped by stack and plane: container, role, network(s), host
   port. Call out anything internal-only (no host port) and any container
   missing from the recovery scripts' inventory.

## Quick map

| Stack / plane | Containers |
|---------------|-----------|
| Main · core | `openwebui`, `tailscale`, `llama-cpp`, `llama-cpp-embed`, `watchtower` |
| Main · memory | `mnemory`, `mnemory-cloud-gateway`, `mnemory-backup` |
| Main · search | `tor`, `redis`, `searxng`, `gateway`, `mcpo` |
| Main · coder | `open-terminal`, `little-coder`, `lc-egress`, `little-coder-backup` |
| Main · aux | `smolcrawl-pipelines`, `surrealdb`, `open_notebook`, `openwebui-backup` |
| OB1 (`open-brain`) | `openbrain-db`, `openbrain-mcp`, `openbrain-ext`, `openbrain-mcpo`, `openbrain-mcpo-ext`, `openbrain-postgrest`, `openbrain-rest`, `openbrain-entity-worker`, `openbrain-wiki`, `openbrain-wiki-viewer` |
| Recovery stack | `scripts/recovery/emergency-recovery.ps1`, `scripts/recovery/emergency-recovery.bat` |

## Consistency rule

When a container is added to or removed from a compose file, three places
must change together:

1. the compose file itself,
2. the recovery scripts' service inventory and shutdown/startup sequences
   (`MainStackServices` / `OB1Services` in `emergency-recovery.ps1`; the
   matching `docker compose` lines in `.bat`),
3. [references/workspace-stacks.md](references/workspace-stacks.md).

If you change one, flag the other two.

## Output

A grouped inventory the reader can act on: which stacks exist, which compose
project each container belongs to, how to drive each project, and any drift
between the compose files, the recovery scripts, and the reference doc.

## Notes

- The map is two projects, not one — never assume a plain `docker compose`
  command touches OB1.
- `modules/emergency-recovery/` is a separate OWUI guidance module and is
  currently stale (its config still references the disabled `ollama`
  container). It is not part of the live recovery path.
- `tailscale` has no network of its own — it shares `openwebui`'s namespace
  via `network_mode: service:openwebui`.
