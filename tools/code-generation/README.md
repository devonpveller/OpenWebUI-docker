# Code Agent — Claude Code-style Toolset for Open WebUI

A modular, expandable toolset that replicates Claude Code's chain-of-thought reasoning and tool-calling workflow inside Open WebUI. Works with any OpenAI-compatible model (Ollama, LM Studio, vLLM, etc.).

## Files

| File                  | Type          | Purpose                                                                  |
| --------------------- | ------------- | ------------------------------------------------------------------------ |
| `code_agent_tools.py` | **OWUI Tool** | Individual tool functions the model calls via native function calling    |
| `code_agent_pipe.py`  | **OWUI Pipe** | Full agentic loop — wraps any model with Claude Code-style orchestration |

## Capabilities

| Tool             | Description                                                         |
| ---------------- | ------------------------------------------------------------------- |
| `read_file`      | Read files with optional line ranges, returns numbered lines        |
| `write_file`     | Create new files or overwrite existing (auto-creates dirs)          |
| `edit_file`      | Surgical diff-based editing — replace exact text                    |
| `list_directory` | Browse directory contents with sizes                                |
| `grep_search`    | Regex/text search across files (skips binaries, node_modules, etc.) |
| `find_files`     | Locate files by glob pattern (recursive)                            |
| `run_command`    | Execute shell commands with configurable security                   |
| `think`          | Extended thinking scratchpad for complex reasoning                  |
| `manage_todo`    | Task tracking with add/update/list/clear                            |
| `save_memory`    | Persistent cross-conversation memory                                |
| `recall_memory`  | Recall or list saved memories                                       |

---

## Setup

### Option A: Tool Only (Simplest)

Use with models that support **native function calling** (most Ollama models, OpenAI, etc.).

1. **Upload the Tool**
   - Open WebUI → **Workspace** → **Tools** → **+** (Create)
   - Paste the entire contents of `code_agent_tools.py`
   - Save

2. **Enable Native Function Calling**
   - **Admin Panel** → **Settings** → **Models** → select your model
   - **Advanced Parameters** → **Function Calling** → set to **"Native"**
   - OR per-chat: **Chat Controls** (gear icon) → **Advanced Params** → **Function Calling** → **"Native"**

3. **Assign the Tool**
   - In a chat, click the **+** button next to the message input
   - Select **Code Agent Tools**
   - Start chatting — the model will automatically call tools when needed

4. **Configure Valves** (optional)
   - **Workspace** → **Tools** → **Code Agent Tools** → gear icon
   - Set `WORKSPACE_PATH` to your project directory (e.g., `/host_project` if using the docker-compose mount)
   - Adjust `SECURITY_MODE`, `ALLOWED_COMMANDS`, etc.

### Option B: Pipe Only (Full Agent)

Use when you want the complete Claude Code experience with automatic tool orchestration. Works with **any** model, including those without native function calling.

1. **Upload the Pipe**
   - Open WebUI → **Workspace** → **Functions** → **+** (Create)
   - Paste the entire contents of `code_agent_pipe.py`
   - Save

2. **Configure Valves** (required)
   - **Workspace** → **Functions** → **Code Agent** → gear icon
   - Set **`MODEL_ID`** — the model that powers reasoning:
     - Ollama: `qwen2.5-coder:32b`, `deepseek-coder-v2:16b`, `codellama:34b`
     - LM Studio: `lm-studio/model-name`
   - Set **`API_BASE_URL`**:
     - Ollama (Docker): `http://ollama:11434/v1`
     - Ollama (host): `http://host.docker.internal:11434/v1`
     - LM Studio: `http://host.docker.internal:1234/v1`
   - Set **`WORKSPACE_PATH`** to your project directory
   - Set **`API_KEY`** (use `ollama` for Ollama, or your actual key)

3. **Select as Model**
   - In a new chat, open the model selector
   - Choose **"Code Agent"** from the list
   - Start chatting — the pipe handles the full think→search→plan→act→verify loop

### Option C: Both (Recommended)

Get the best of both worlds — the Pipe provides the system prompt and orchestration, the Tool provides capabilities for direct model use.

1. Upload both files (follow steps from Options A and B)
2. Use the **Pipe** for complex multi-step coding tasks
3. Use the **Tool** with your regular model for quick one-off operations

---

## Configuration Reference

### Security Modes (Valve: `SECURITY_MODE`)

| Mode           | Behavior                                    | Use Case                     |
| -------------- | ------------------------------------------- | ---------------------------- |
| `allowlist`    | Only commands in `ALLOWED_COMMANDS` can run | Production, shared instances |
| `confirm`      | All commands allowed but logged             | Development with audit trail |
| `unrestricted` | No command restrictions                     | Local development only       |

### Tool Calling Formats (Pipe only, Valve: `TOOL_CALL_FORMAT`)

| Format   | Behavior                                      | Best For                                      |
| -------- | --------------------------------------------- | --------------------------------------------- |
| `auto`   | Try native function calling, fall back to XML | Most situations                               |
| `native` | OpenAI-style function calling only            | Models with good function calling (Qwen, GPT) |
| `xml`    | Tool calls via XML tags in model output       | Older models, custom setups                   |

### Key Valves

| Valve                | Default                 | Description                       |
| -------------------- | ----------------------- | --------------------------------- |
| `WORKSPACE_PATH`     | `/app/backend/data`     | Root directory for all operations |
| `MAX_ITERATIONS`     | `25`                    | Max agent loop steps (Pipe only)  |
| `COMMAND_TIMEOUT`    | `30`                    | Seconds before command is killed  |
| `MAX_READ_LINES`     | `500`                   | Lines per read_file call          |
| `MAX_SEARCH_RESULTS` | `50`                    | Results per search                |
| `MEMORY_DIR`         | `.../code_agent/memory` | Persistent memory storage         |

---

## Docker Compose Integration

If using the AI Stack docker-compose setup, the project is already mounted at `/host_project:ro`. For **read-write** access (needed for write_file, edit_file, run_command), update the mount:

```yaml
# docker-compose.yml — openwebui service
volumes:
  - .:/host_project # Remove :ro for read-write access
  # OR mount a dedicated workspace:
  - ./workspace:/workspace # Dedicated writable workspace
```

Then set `WORKSPACE_PATH` to `/host_project` or `/workspace` in the Valves.

---

## Extending with New Tools

### Adding a Tool to code_agent_tools.py

1. Add an `async def` method to the `Tools` class:

```python
async def my_new_tool(
    self,
    param1: str,
    param2: int = 0,
    __user__: dict = {},
    __event_emitter__: Callable[[dict], Any] = None,
) -> str:
    """
    Description of what this tool does. This docstring becomes
    the function description the model sees.

    :param param1: What param1 is for.
    :param param2: What param2 is for.
    :return: What the tool returns.
    """
    await self._emit(__event_emitter__, "Working...")
    # ... implementation ...
    await self._emit(__event_emitter__, "Done", done=True)
    return "result"
```

2. OWUI auto-discovers public methods — no registration needed.

### Adding a Tool to code_agent_pipe.py

1. Add the tool definition to `TOOL_DEFINITIONS`:

```python
{
    "type": "function",
    "function": {
        "name": "my_new_tool",
        "description": "What it does",
        "parameters": {
            "type": "object",
            "properties": {
                "param1": {"type": "string", "description": "..."},
                "param2": {"type": "integer", "default": 0},
            },
            "required": ["param1"],
        },
    },
},
```

2. Add the implementation to `_ToolEngine`:

```python
async def _t_my_new_tool(self, param1: str, param2: int = 0) -> str:
    # implementation
    return "result"
```

The engine auto-dispatches based on `_t_{name}` method naming.

---

## Troubleshooting

### "MODEL_ID not configured"

Set the `MODEL_ID` valve in **Workspace → Functions → Code Agent → Valves**.

### Tools not being called

- Ensure **Native Function Calling** is enabled for your model
- Check the model supports function calling (most Qwen, DeepSeek, Llama 3.1+ do)
- Try the Pipe instead — it handles tool calling for any model

### "Command not in allowlist"

Add the command to `ALLOWED_COMMANDS` valve, or switch `SECURITY_MODE` to `confirm`.

### "Path outside workspace"

The tool prevents path traversal. Set `WORKSPACE_PATH` to a directory that contains your project files.

### Agent loops too many times

Reduce `MAX_ITERATIONS` or check that the model is producing coherent tool calls. Some smaller models may loop on the same tool call.

### LLM call fails

- Verify `API_BASE_URL` is reachable from the container (use `http://ollama:11434/v1` for Ollama in Docker)
- Check `MODEL_ID` matches an available model (`curl http://ollama:11434/v1/models`)
- For LM Studio: use `http://host.docker.internal:1234/v1`

### httpx not available

The OWUI container should include httpx. If not: `pip install httpx` inside the container.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Option A: Tool + Native Function Calling                │
│                                                         │
│  User ──→ Model ──→ code_agent_tools.py ──→ Model ──→ User │
│            (OWUI handles tool loop)                     │
└─────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────┐
│ Option B: Pipe (Full Agent)                             │
│                                                         │
│  User ──→ code_agent_pipe.py ──→ LLM API ──┐           │
│             │ ← tool_calls ─────────────────┘           │
│             │ → execute tools                           │
│             │ → send results back to LLM                │
│             │ ← final answer ───→ User                  │
│             └── (loops until done or max iterations)    │
└─────────────────────────────────────────────────────────┘
```

The Pipe includes a self-contained `_ToolEngine` class with all tool implementations, so it works independently without the Tool file. Both files are fully self-contained for OWUI upload.
