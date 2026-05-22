# pi extension — route the agent's shell into open-terminal

This is the **one genuinely framework-dependent integration point** of the
Tool chapter (design §1.5, §3.4).

## The contract

little-coder runs in the control plane. Its file tools (Read/Write/Edit) act
on the shared `/workspace` volume directly — fine, that is not execution. Its
**command execution** must run in `open-terminal`, the network-isolated plane,
so that:

- everything the inner loop executes is bounded by open-terminal's egress
  allowlist (design §3.4), and
- every `git` call passes through the git-proxy (design §3.3).

The mechanism is the `ot-exec` shim (installed on `$PATH` in the agent image,
`littlecoder/otexec.py`). It is a drop-in `bash -c` replacement:

```
ot-exec -c "<command>"     # runs <command> in open-terminal, returns its
                           # stdout/stderr and exit code; also appends a
                           # JSON event to $LC_EVENT_STREAM for journaling
```

**The extension's only job:** make little-coder's shell/bash tool invoke
`ot-exec -c "<cmd>"` instead of a local shell.

## Why this is the integration point

`pi` (`@earendil-works/pi-coding-agent`) auto-discovers TypeScript extensions
from `.pi/extensions/`, but its extension API is not publicly documented. The
exact hook used to override the built-in `bash` tool must be confirmed against
the pinned upstream version. Everything else in the Tool chapter is
framework-independent and already tested.

[`open-terminal-exec.ts`](open-terminal-exec.ts) is a best-effort
implementation against the most plausible `pi` extension shape. When the agent
image is first built, verify it loads (`little-coder` logs discovered
extensions) and adjust the registration hook if needed.

## Fallbacks if the extension cannot be wired

1. **Shell shim** — point `pi`'s shell at `ot-exec` via configuration or the
   `SHELL` env var, if the pinned version honors one.
2. **Defence in depth holds regardless** — the `little-coder` container is
   itself on internal networks only (`lc-net` + `llm-net`, no internet). If
   execution ever runs locally instead of in open-terminal, it still cannot
   egress; it only loses the git-proxy and the per-command journaling. So a
   missing extension degrades instrumentation, it does not open the network.

## Discovery path

The agent image installs this directory into `~/.pi/extensions/` (see
`docker/entrypoint-agent.sh`). If the pinned little-coder version discovers
extensions from a different location, adjust that copy step.
