# agent-knowledge — founding knowledge for the agent

Knowledge the agent should *have*, not *rediscover*. Each file here is
appended to little-coder's system prompt via `--append-system-prompt`
(wired in `config/little-coder.config.yaml` → `agent.extra_args`).

| File | Purpose |
| --- | --- |
| `environment.md` | The operating environment — bash runs in open-terminal, the git-proxy whitelist/blocklist, `/workspace`, no ShellSession/Browser, network limits. Stops the agent burning tokens probing its own constraints every task. |
| `engineering-principles.md` | SOLID, encapsulation, naming, patterns, DRY/YAGNI — the craft baseline applied to all code. |

## Relationship to the design's skill library (§7)

This is **founding knowledge**, deliberately kept separate from the
self-improvement skill library (design §7, the `little-coder-skill/` volume):

- Founding knowledge is the **baseline** — operator-authored, always in
  context, loaded through little-coder's *own* `--append-system-prompt` flag
  (so design §3.1, "the inner loop is unchanged from upstream", holds).
- The §7 skill library is the **learned layer** — `meta` drafts
  cluster-tagged, augmenter-selected artifacts from Chapter 4 onward.

A solid baseline means `meta` learns the *subtle* craft gaps rather than
re-teaching the agent that the git-proxy blocks force-push — it raises the
floor so self-improvement targets the ceiling (helps design §13 preflight).

These files are loaded as guidance; they are never executed (design §10.4).
