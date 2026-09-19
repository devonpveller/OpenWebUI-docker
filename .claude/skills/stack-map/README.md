# Stack Map

A Claude skill that identifies and maps every Docker stack in this `ai-stack`
workspace — so anyone (human or agent) can answer "what runs here?" without
re-reading every compose file.

## What it does

The workspace is **nine separate Docker Compose projects**, one per plane,
plus a driver and a recovery layer:

- **`ai-stack`** — `docker-compose.yml`: the network ANCHOR, **zero services**.
- **`frontend`, `inference`, `memory`, `search`, `coder`, `portal`** —
  `<plane>/docker-compose.yml`, each with its own `.env` and `README.md`.
- **`open-brain`** — `OB1/docker/docker-compose.yml` (a pinned submodule; 30
  containers with every profile).
- **`agent-org`** — `agent-org/docker/docker-compose.yml`.
- **Driver** — `python scripts/stack/stack.py`, reading `stack.manifest.toml`
  (`scripts/stack/stack.ps1` is a shim over it).
- **Recovery stack** — `scripts/recovery/emergency-recovery.ps1`: orchestrated
  restart/repair across every project.

The skill reads the live compose files, groups containers by plane, and
reports a current inventory — networks, ports, and dependency order included.
It also flags drift between the compose files, the recovery scripts, and the
curated reference.

## When it triggers

- "What stacks / containers are in this workspace?"
- "Where does `mnemory` run? What network is it on?"
- "Show me the topology."
- Before editing any `<plane>/docker-compose.yml`, `stack.manifest.toml`, or
  `emergency-recovery.ps1`.

## Files

| File | Purpose |
|------|---------|
| `SKILL.md` | Trigger conditions + the mapping procedure |
| `references/workspace-stacks.md` | Full curated inventory — every container, network, port, and the cross-stack startup order |
| `metadata.json` | Skill metadata |

## Keeping it accurate

The compose files are the source of truth for what EXISTS;
`stack.manifest.toml` is the declaration of what each plane needs, surfaces and
gates. When a container is added or removed, the compose file, the manifest,
`emergency-recovery.ps1`'s per-plane inventory,
`scripts/lib/stack-services.curated.json` (then `stack.py inventory --write`),
`references/workspace-stacks.md` and the plane's own README change together.
The skill checks for that drift each time it runs; the full lifecycle
checklist is `documentation/runbooks/SERVICE-LIFECYCLE.md`.

## Invoke

Type `/stack-map`, or just ask a question that matches the triggers above.
