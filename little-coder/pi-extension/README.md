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
- **It must never block pi's event loop** (ao-asyncexec, 2026-10-11). The
  command runs with an async `spawn`, never `spawnSync`: pi's `--mode json`
  writer flushes one line per write callback, so a synchronous spawn kept the
  call's `tool_execution_start` out of the events file until the command had
  ended, and the agent-bridge's command watch, the daemon's tool counters and
  the flail guard saw no running command. The tool is registered with
  `executionMode: "sequential"` (pi-agent-core runs a batch containing such a
  call one call at a time, each start emitted just before its call), which keeps
  spawnSync's one-command-at-a-time behaviour; without it two calls in one
  assistant message would run concurrently in the same workspace. Unchanged:
  the 64 MB output cap (stdout + stderr together; past it the child gets
  SIGTERM, exit 1), the result text and exit codes, and no timeout of its own
  (ot-exec applies `LC_EXEC_TIMEOUT`, exit 124; see the next point). New: pi's abort signal stops
  the command. Contract test: `tests/node/open_terminal_exec.test.mjs` (run by
  `tests/test_open_terminal_exec_ext.py`).
- **A command never holds a turn past its bound** (ao-exechang, 2026-10-14).
  ot-exec (`littlecoder/otexec.py`) runs the command through a small wrapper
  in open-terminal: the command gets stdin from `/dev/null`, every process it
  starts carries `LC_EXEC_ID=<id>`, and its exit status is written to
  `/tmp/lc-exec-<id>.rc`. So a command that leaves a background job holding
  the terminal (`( ... ) &`, `cmd &`) returns when the command itself ends,
  with a note, instead of when the job ends (bake-off C7d: 1,270 s). On
  `LC_EXEC_TIMEOUT` ot-exec takes the output so far, then a reaper in
  open-terminal freezes, SIGTERMs (+SIGCONT) and SIGKILLs the command's
  process group and session, every process carrying its `LC_EXEC_ID`, and
  their descendants (stopped processes, SIGTERM-ignorers, setsid escapees),
  counts survivors, and only then ot-exec exits 124 with a fixed message:
  `[ot-exec] timed out after N s, killed: <command>`, exactly what was
  checked and killed, and how to proceed (bounded: deadline + 14 s at most).
  pi records an error only when a tool THROWS (a returned `isError: true` is a
  success to it: every 1,800-s timeout in the bake-off reached the model as
  "(no output)"), so the extension throws on that timeout: the model gets the
  output and the message as an error result. A pi abort SIGTERMs ot-exec,
  which kills the command in open-terminal the same way before exiting 143.
  Backstop: an ot-exec still running `LC_EXEC_TIMEOUT` + 120 s
  (`LC_EXEC_BACKSTOP_GRACE`) after it started is stopped (SIGTERM, SIGKILL
  30 s later) and the call fails with a fixed message. agent-org workers run
  with a 900-s bound (`agent-org/scripts/gen-worker-configs.py`); the
  interactive little-coder keeps 1,800 s. Real-path test:
  `tests/node/e2e_exechang/` (a real open-terminal container, the real ot-exec,
  pi and this extension).
- `docker/entrypoint-agent.sh` installs it into little-coder's own
  `.pi/extensions/` directory (alongside the bundled extensions: 35 in 1.20.0,
  of which the entrypoint removes `shell-session`, `bg-shell`, `browser` and
  `browser-extract-retention`). The launcher passes every
  `.pi/extensions/*/index.ts` to pi as `--extension`, so ours loads with the
  bundled set. Re-verified on 1.20.0 (bundled pi 0.83.0) on 2026-09-30: the
  model is offered our `bash` (open-terminal description), and a real task's
  commands ran in a disposable open-terminal.
- 1.12.0+ also has a user extension directory
  (`~/.config/little-coder/extensions/`, loaded AFTER the bundled set). We do
  not use it: the package-dir install is what has been verified, and pi's
  built-in `bash` is what we override, which the bundled set does not
  register.

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

## No extension here may read thinking

The image build (`docker/remove-thinking-cap.mjs`, ao-thinkon) scans this dir along with
little-coder's bundled extension set and fails if any file reads thinking (`thinking_delta`, a
`"thinking"` content block, `reasoning_content`, ...). A thought is bounded by the server's
reasoning budget only; see [../README.md](../README.md#thinking-no-client-side-cap).
