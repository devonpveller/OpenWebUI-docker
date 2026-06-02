# Baseline container inventory (Task 0.5)

Snapshot of the live topology **before** the Integrated Knowledge System
work, captured 2026-06-02. This is the drift reference for Phase 8.2: the
Phase-5 `suggestion-worker` is the only net-new container, so after
promotion the `open-brain` project must show **16** containers (15 here
+ 1), and the worker must appear in the recovery scripts + stack-map.

Captured via `docker ps -a --filter label=com.docker.compose.project=<p>`
(the `/stack-map` skill reads the same compose projects).

## Open Brain project (`open-brain`) — 15 containers

| Container | Image |
|-----------|-------|
| openbrain-db | pgvector/pgvector:pg16 |
| openbrain-mcp | openbrain-mcp-server:local |
| openbrain-ext | openbrain-ext-server:local |
| openbrain-gateway | openbrain-gateway:local |
| openbrain-mcpo | ghcr.io/open-webui/mcpo:latest |
| openbrain-mcpo-ext | ghcr.io/open-webui/mcpo:latest |
| openbrain-postgrest | postgrest/postgrest:v12.2.3 |
| openbrain-rest | caddy:2-alpine |
| openbrain-entity-worker | openbrain-entity-worker:local |
| openbrain-wiki | openbrain-wiki:local |
| openbrain-wiki-viewer | openbrain-wiki-viewer:local |
| openbrain-cron | openbrain-cron:local *(scheduled)* |
| openbrain-gmail-pull | denoland/deno:alpine *(scheduled)* |
| openbrain-gmail-prune | denoland/deno:alpine *(scheduled)* |
| openbrain-digest | denoland/deno:alpine *(scheduled)* |

**After promotion → expected 16** (adds `openbrain-suggestion-worker`).

The recovery script `scripts/emergency-recovery.ps1` `$Script:OB1Services`
list (15 entries) and `.claude/skills/stack-map/references/workspace-stacks.md`
must gain the same entry — the three-places rule (guardrail 6).

## Main stack project (`ai-stack`) — 40 containers

Source-data-relevant members (unchanged by this work, listed for context):
`open_notebook` (`lfnovo/open_notebook:v1-latest`), `surrealdb`
(`surrealdb/surrealdb:v2`), `openwebui`, `smolcrawl-pipelines`,
`llama-cpp`, `llama-cpp-embed`. The full set also includes the
portal/auth layer (authelia, caddy, cloudflared, portal-*), the
little-coder plane, the private-search plane, mnemory, and per-volume
backup sidecars.

> Phase 4 swaps `open_notebook`'s `image:` → a locally built tag of the
> fork (staged as a runbook diff, Task 4.6) — that is an image change,
> **not** a new container, so it does not change the count or trigger the
> three-places rule.

## How to re-check drift (Phase 8.2)

```bash
docker ps -a --filter label=com.docker.compose.project=open-brain --format '{{.Names}}' | sort | wc -l   # expect 16 post-promotion
```
Then run `/stack-map` and diff its inventory against this file.
