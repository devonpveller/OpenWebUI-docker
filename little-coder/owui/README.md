# OWUI pipeline — Chapter 2

OpenWebUI integration for little-coder (design §12.6). Two pieces:

1. **Little Coder Pipe** — an OWUI **Pipe function**. Registers a
   "Little Coder" model. Plain chat messages trigger coding tasks; `/`-commands
   are operator actions. This is the primary surface.
   > The deploy-by-paste source is centralized at
   > [`owui/pipes/little_coder.py`](../../owui/pipes/little_coder.py) (canonical,
   > == live deployment). This folder holds the little-coder **service** docs.
2. **`lc-mcpo` as an OpenAPI tool** — optional. Lets any tool-calling OWUI
   model trigger tasks. It exposes only `trigger_task` / `task_status` /
   `project_focus` — never operator actions.

## Privilege separation (design §12.6)

- **Task triggers** — any logged-in OWUI user. Journaled as `channel=owui`,
  `user_id = <the user's email>`.
- **Operator commands** (`/project`, `/confirm`, `/pending`, `/approve`,
  `/reject`, `/upstream`, `/observe`) — gated by the OWUI **user role** inside
  the pipe (`operator_roles` valve, default `admin`). A regular user cannot
  escalate.
- The MCP edge (`lc-mcpo`) carries triggers only; it has no operator surface.

## Install the Pipe

OpenWebUI → **Admin → Functions → ➕ New Function**. Paste the contents of
[`owui/pipes/little_coder.py`](../../owui/pipes/little_coder.py), save, and **enable** it.

A "Little Coder" model then appears in the model picker.

> **Updating the Pipe:** when `owui/pipes/little_coder.py` changes, edit the existing
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
/observe [iterate]                   Observer report (operator, Chapter 3 —
                                     `iterate` runs a fresh meta pass first)
/status   /help                      open to everyone
```

## Live streaming & interruption

A plain message streams the agent's process into the chat as it happens — its
**🧠 thinking**, each **🔧 tool call**, and the **answer**, token by token
(the agent runs with pi `--mode json`; the daemon serves the event stream).
Set the `show_thinking` valve to `false` to hide reasoning.

Press OpenWebUI's **Stop** button to interrupt a running task — the pipe calls
`/tasks/{id}/cancel` and the daemon kills the agent (and all its child
processes). This is operator-triggered abandonment, consistent with design
§12.4; it is *not* a mid-task write into the agent.

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
