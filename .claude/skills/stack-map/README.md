# Stack Map

A Claude skill that identifies and maps every Docker stack in this `ai-stack`
workspace — so anyone (human or agent) can answer "what runs here?" without
re-reading every compose file.

## What it does

The workspace is **two separate Docker Compose projects** plus a recovery
layer:

- **`ai-stack`** — `docker-compose.yml`: core, memory, search, coder, and aux
  planes (~22 containers).
- **`open-brain`** — `OB1/docker/docker-compose.yml`: the Open Brain memory
  system (~10 containers), a separate project that attaches to the main
  stack's network.
- **Recovery stack** — `scripts/recovery/emergency-recovery.{ps1,bat}`: orchestrated
  restart/repair for both projects.

The skill reads the live compose files, groups containers by plane, and
reports a current inventory — networks, ports, and dependency order included.
It also flags drift between the compose files, the recovery scripts, and the
curated reference.

## When it triggers

- "What stacks / containers are in this workspace?"
- "Where does `mnemory` run? What network is it on?"
- "Show me the topology."
- Before editing `docker-compose.yml`, `OB1/docker/docker-compose.yml`, or the
  `emergency-recovery` scripts.

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Trigger conditions + the mapping procedure |
| `references/workspace-stacks.md` | Full curated inventory — every container, network, port, and the cross-stack startup order |
| `metadata.json` | Skill metadata |

## Keeping it accurate

The compose files are the source of truth. When a container is added or
removed, update three places together: the compose file, the recovery scripts'
service inventory, and `references/workspace-stacks.md`. The skill checks for
this drift each time it runs.

## Invoke

Type `/stack-map`, or just ask a question that matches the triggers above.
