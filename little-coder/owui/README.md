# OWUI pipeline — Chapter 2

OpenWebUI integration for little-coder (design §12.6). Two pieces:

1. **`little_coder_pipe.py`** — an OWUI **Pipe function**. Registers a
   "Little Coder" model. Plain chat messages trigger coding tasks; `/`-commands
   are operator actions. This is the primary surface.
2. **`lc-mcpo` as an OpenAPI tool** — optional. Lets any tool-calling OWUI
   model trigger tasks. It exposes only `trigger_task` / `task_status` /
   `project_focus` — never operator actions.

## Privilege separation (design §12.6)

- **Task triggers** — any logged-in OWUI user. Journaled as `channel=owui`,
  `user_id = <the user's email>`.
- **Operator commands** (`/project`, `/confirm`, `/pending`, `/approve`,
  `/reject`, `/upstream`) — gated by the OWUI **user role** inside the pipe
  (`operator_roles` valve, default `admin`). A regular user cannot escalate.
- The MCP edge (`lc-mcpo`) carries triggers only; it has no operator surface.

## Install the Pipe

OpenWebUI → **Admin → Functions → ➕ New Function**. Paste the contents of
[`little_coder_pipe.py`](little_coder_pipe.py), save, and **enable** it.

A "Little Coder" model then appears in the model picker.

> **Updating the Pipe:** when `little_coder_pipe.py` changes, edit the existing
> function in **Admin → Functions → Little Coder**, replace the code, and save —
> no need to delete and re-create it.

Check the function's **Valves**:

| Valve                  | Default                      | Notes                                  |
| ---------------------- | ---------------------------- | -------------------------------------- |
| `daemon_url`           | `http://little-coder:8090`   | reachable from OWUI over `llm-net`     |
| `operator_roles`       | `admin`                      | OWUI roles allowed to run `/`-commands |
| `poll_seconds`         | `3`                          | task-status poll interval              |
| `task_timeout_seconds` | `2100`                       | give-up wait (just above the daemon's) |

## Usage

```
<plain message>                     trigger a coding task
/project https://github.com/me/repo  switch the focused project (operator)
/confirm <task_id> pass|fail         amend a task outcome (operator)
/pending /approve /reject            artifact review — operative in Chapter 4
/upstream pull                       fork-parent pull — operative in Chapter 5
/status   /help                      open to everyone
```

## Optional: register lc-mcpo as an OpenAPI tool

OpenWebUI → **Admin → Settings → Tools → ➕**, add:

- **URL:** `http://lc-mcpo:8002`
- **Auth:** Bearer — the value of `LC_MCPO_API_KEY` from `.env`.

Tool-calling models can then call `trigger_task` themselves. The Pipe does not
depend on this.

## Smoke test (chapter 2 stop point)

1. Select the **Little Coder** model; as an operator send `/project <repo>`.
2. As any user, send a coding task in chat; watch the status stream.
3. Confirm the journal attributes it: inside the container,
   `lc tasks` shows `channel = owui` and `user_id = <OWUI user>` for it.
