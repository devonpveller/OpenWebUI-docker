# little-coder — control plane

Containerized deployment of [little-coder](https://github.com/itayinbarr/little-coder)
that accumulates expertise from its own work. This directory holds the
**control-plane wrapper**: journals, config, sanitization, git-proxy, the CLI
operator surface, and the MCP edge.

> **Source of truth:** [`../documentation/little-coder/Self-improving-little-coder-design.md`](../documentation/little-coder/Self-improving-little-coder-design.md).
> Build sequencing: [`integration-plan.md`](../documentation/little-coder/integration-plan.md).
> Status: [`integration-tasks.md`](../documentation/little-coder/integration-tasks.md).

## Current chapter: 1 — Tool

A working little-coder driven from the CLI. No OWUI surface, no `meta` outer
loop. Journals record quietly with the full envelope; the sanitization filter
runs in shadow mode; named volumes are the persistence boundary.

## Two planes

| Plane     | Container      | Role                                                        |
| --------- | -------------- | ----------------------------------------------------------- |
| Control   | `little-coder` | Decides. Runs the agent, owns journals + the FIFO queue.    |
| Workspace | `open-terminal`| Executes. Own network, egress-allowlisted. Repo lives here. |

The two share the `little-coder-workspace` named volume: the agent edits files
on it directly; build/test/git commands run inside `open-terminal`, the
network-isolated plane. `git` inside `open-terminal` **is** the git-proxy.

## Layout

```
little-coder/
├── config/little-coder.config.yaml   # centralized typed config (mounted ro)
├── config/little-coder.schema.json   # JSON schema for the config
├── src/littlecoder/                  # Python control-plane package
├── git-proxy/                        # git wrapper (whitelist/blocklist)
├── pi-extension/                     # routes the agent's exec into open-terminal
├── docker/                           # Dockerfiles
└── tests/                            # pytest suite
```

## Language note

Upstream little-coder is a **Node.js** CLI built on the `pi` agent framework —
not Python. The agent container is Node-based; this control-plane wrapper is
Python, mirroring the repo's `search-mcpo` / `mnemory-gateway` pattern. The
`agent.py` filename in design §6 is a Chapter-5 illustration only.

## Operator action items (cannot be automated)

Before deploying with self-improvement chapters, the operator must:

1. Create the **private** self-improvement git remote (design §10.6) and set
   `LC_SELF_REMOTE_URL` in `.env`.
2. Provision a fine-grained PAT scoped to `contents:write` on that remote only
   and set `LC_SELF_REMOTE_PAT` in `.env`.

Unused until Chapter 4+, but the credential chain is wired now so later
chapters do not retrofit it.
