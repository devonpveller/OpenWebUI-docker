# pi extension — route the agent's shell into open-terminal

Routes little-coder's command execution into the `open-terminal` plane so the
inner loop is network-isolated and every `git` call passes the git-proxy
(design §1.5, §3.3, §3.4).

## Status: verified working

`open-terminal-exec/index.ts` overrides the built-in `bash` tool. It was
validated end-to-end on 2026-05-22: an agent task's command was confirmed
running inside open-terminal (journaled as a `bash` tool_call sourced from the
`ot-exec` event stream).

## How it works

- The extension registers a tool named `bash`, overriding pi's built-in. Its
  `execute` runs the command through `ot-exec` — the shim
  (`littlecoder/otexec.py`) that POSTs to open-terminal's `POST /execute`.
- The pi extension API (`pi.registerTool({ name, label, description,
  parameters, async execute(id, args) → { content:[{type,text}], details,
  isError } })`) matches the bundled little-coder extensions (e.g.
  `extra-tools`). The file uses only `import type` + node builtins, so it has
  no runtime dependency to resolve.
- `docker/entrypoint-agent.sh` installs it into little-coder's own
  `.pi/extensions/` directory (alongside the 21 bundled extensions) so pi
  discovers it and module resolution works.

## The switch

Gated by `LC_ROUTE_EXEC` (compose env, **default 1**):

- `LC_ROUTE_EXEC=1` — extension installed; the agent's commands run in
  open-terminal, through the git-proxy.
- `LC_ROUTE_EXEC=0` — extension not installed; the agent uses pi's built-in
  `bash`, which runs inside the `little-coder` container. That container is on
  internal networks only (no internet), so execution is still contained — it
  just bypasses the open-terminal plane and the git-proxy. This is the
  fallback if a future upstream change breaks the override.

## If a future little-coder version changes the extension API

Symptom: the agent loops on `bash` calls. Read a bundled extension for the
current API:

```
docker exec little-coder sh -c 'cat $(npm root -g)/little-coder/.pi/extensions/extra-tools/index.ts'
```

Then adjust `open-terminal-exec/index.ts` to match, or set `LC_ROUTE_EXEC=0`
while you do.
