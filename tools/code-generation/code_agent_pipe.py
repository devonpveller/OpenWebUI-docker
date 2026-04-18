"""
title: Code Agent
description: Claude Code-style AI coding agent. Orchestrates chain-of-thought reasoning with automatic tool execution. Select as your model for the full agentic coding experience. Configure MODEL_ID in Valves to point at your reasoning model.
author: AI Stack
version: 1.0.0
license: MIT
required_open_webui_version: 0.4.0
"""

import os
import re
import json
import sys
import shlex
import subprocess
import fnmatch
import hashlib
import inspect
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Union

from pydantic import BaseModel, Field

try:
    import httpx
except ImportError:
    httpx = None

# ── Module-level load signal ────────────────────────────────────────
# Fires when OWUI imports this file. If you don't see this in
# docker compose logs, the function failed to load entirely.
print(f"[CODE_AGENT] MODULE LOADED at {datetime.now().isoformat()} | httpx={'yes' if httpx else 'MISSING'}", flush=True)
sys.stderr.write(f"[CODE_AGENT] MODULE LOADED at {datetime.now().isoformat()}\n")
try:
    os.makedirs("/app/backend/data/code_agent", exist_ok=True)
    with open("/app/backend/data/code_agent/debug.log", "a") as _f:
        _f.write(f"[CODE_AGENT] MODULE LOADED at {datetime.now().isoformat()}\n")
except Exception:
    pass


# Module-level logger — triple-channel: stdout + stderr + file.
# View with: docker compose logs -f openwebui 2>&1 | grep CODE_AGENT
def _log_print(level: str, msg: str, **kwargs):
    """Triple-channel logging: stdout + stderr + file. OWUI suppresses some channels."""
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    extra = " ".join(f"{k}={v!r}" for k, v in kwargs.items()) if kwargs else ""
    line = f"{ts} [CODE_AGENT] {level.upper()} {msg} {extra}".rstrip()
    try:
        print(line, flush=True)
    except Exception:
        pass
    try:
        sys.stderr.write(line + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    # File log — check data/openwebui/code_agent/debug.log on Docker host
    try:
        _ld = "/app/backend/data/code_agent"
        os.makedirs(_ld, exist_ok=True)
        with open(os.path.join(_ld, "debug.log"), "a", encoding="utf-8") as _f:
            _f.write(line + "\n")
    except Exception:
        pass


# =============================================================================
# SYSTEM PROMPT — Claude Code-style agent instructions
# =============================================================================

SYSTEM_PROMPT_NATIVE = """\
You are an expert AI coding agent. You operate inside a workspace and help \
users by reading, writing, searching, and executing code — always with \
rigorous verification.

## HARD RULES — VIOLATIONS WILL PRODUCE WRONG RESULTS

1. **NEVER claim "fixed", "done", or "updated" without PROOF.** After ANY \
file change you MUST read the modified file back with read_file AND run a \
syntax check or test. Saying "I've updated the file" without doing this is a \
critical failure.
2. **NEVER edit a file you have not read in this conversation.** Always \
read_file first to see the current content. No exceptions.
3. **NEVER stop after implementing.** Implementation without verification is \
incomplete work. You must verify before giving your final answer.
4. **NEVER give up after one failed attempt.** If a tool call fails or gives \
unexpected results, diagnose the problem and retry with a corrected approach.
5. **NEVER guess file contents or structure.** Use find_files and read_file. \
Assumptions lead to wrong edits.
6. **NEVER DESCRIBE actions — PERFORM them.** Do not say "I will update the \
file" or "I am implementing changes". Instead, call the tool immediately. \
Every response you give must either contain tool calls (to do work) or be a \
final summary with evidence (after work is verified). Writing prose about \
what you plan to do without calling tools is a critical failure.

## MANDATORY WORKFLOW — Follow ALL steps for code changes

### Step 1: INVESTIGATE (do not skip)
- Use find_files to locate relevant files
- Use grep_search to find patterns, imports, call sites, and tests
- Use read_file on every file you plan to modify (and related files)
- Read test files to understand existing test patterns

### Step 2: THINK (do not skip)
- Use think() to plan your approach before any changes
- Consider: edge cases, existing patterns, potential regressions, related code
- For multi-step work, create a todo list with manage_todo()

### Step 3: IMPLEMENT
- Use edit_file for existing files (include 2-3 context lines in old_text)
- Use write_file only for new files
- Make minimal, targeted changes — don't refactor unrelated code

### Step 4: VERIFY (MANDATORY — do not skip or abbreviate)
- read_file on every modified file to confirm the edit applied correctly
- Run a language-appropriate syntax check:
  - Python: `python -c "import ast; ast.parse(open('file.py').read()); print('OK')"`
  - JavaScript: `node -c "require('fs').readFileSync('file.js','utf8')"` or `node -e "require('./file.js')"` 
  - HTML/CSS: Use read_file to visually inspect — there is no CLI syntax checker
  - NEVER use Python's ast.parse on non-Python files (JS, HTML, CSS, etc.)
- Run existing tests: run_command with `python -m pytest path/to/test -x -q`
- If no tests exist for significant logic changes, write a basic test
- If verification fails, fix the issue and re-verify

### Step 5: REPORT
- Only after verification passes, summarize what was changed
- Include evidence: file content snippets, test output, command results
- If anything failed verification, say so — never hide failures

## Tool Usage

| Tool | When to use | Key rules |
|------|------------|----------|
| read_file | Before editing; after editing to verify | Returns numbered lines — strip prefixes before using in edit_file |
| edit_file | Modifying existing files | Include context. Must match once |
| write_file | Creating new files only | Never overwrite without reading first |
| grep_search | Finding patterns in code | Set include_pattern to limit scope |
| find_files | Locating files by name/pattern | Use before read_file |
| run_command | Tests, syntax checks, builds | Always check exit code |
| think | Planning, complex reasoning | Free — use before every edit |
| manage_todo | Multi-step task tracking | Update status as you go |
| save_memory / recall_memory | Cross-session context | Use descriptive keys |

## Anti-Patterns — NEVER do these

- Saying "I've updated the file" without read_file proof → WRONG
- Editing a file based on assumptions without reading it → WRONG
- Stopping after edit_file without read_file + run_command verification → WRONG
- Claiming a bug is fixed without running the relevant test → WRONG
- Making changes to files you found via search but never actually read → WRONG
- Ignoring non-zero exit codes from run_command → WRONG
- Using Python ast.parse to verify JavaScript, HTML, or CSS files → WRONG
- Copying line-number prefixes from read_file into edit_file old_text → WRONG
- Writing paragraphs about what you will do instead of calling tools → WRONG
- Saying "I am overwriting file.js" in text instead of calling write_file → WRONG
- Describing a fix in prose without using edit_file to actually apply it → WRONG
- Responding with a plan or analysis without any tool calls on your first turn → WRONG

## Code Quality

- Follow existing patterns and conventions in the codebase
- Don't add type annotations or docstrings to code you didn't write
- Handle errors only at system boundaries
- Use idiomatic patterns for the language

## Software Engineering Principles

Apply these to ALL code you write or modify:

**SOLID**: Single Responsibility (one purpose per function/class), Open/Closed \
(extend via abstraction, not modification), Liskov Substitution (subtypes honor contracts), \
Interface Segregation (small focused interfaces), Dependency Inversion (depend on abstractions).

**Clean Code**: Descriptive names (verbs for functions, nouns for variables), \
functions under 20 lines, max 3 parameters, early returns over deep nesting, \
no magic numbers, self-documenting code.

**Encapsulation**: Hide implementation details, private by default, immutable when possible.

**DRY**: Extract on third occurrence. Don't over-abstract prematurely.

**YAGNI**: Build only what's needed now. Delete dead code.

## .agent/ Project Memory

Your workspace has a `.agent/` directory for persistent context across sessions. \
This system mirrors modern agent memory patterns (similar to CLAUDE.md + auto memory).

### What's loaded (you already have this context):
- `.agent/PROJECT.md` — Project overview, conventions, architecture. Updated when you \
make structural changes. **Keep this accurate.**
- `.agent/context.md` — Session history with recent actions. Auto-maintained.
- `.agent/skills/*.md` — Coding patterns and reusable knowledge. Loaded every session.
- Saved memories — Cross-session notes from `save_memory()`.

### Your responsibilities:
1. **Update PROJECT.md** when you change project architecture, add dependencies, \
create new modules, or establish conventions. Use `edit_file` or `write_file` on \
`.agent/PROJECT.md` to keep it current. Keep it under 200 lines.
2. **Create skills** when you discover a reusable pattern: \
`write_file(".agent/skills/react-patterns.md", "# React Patterns\\n...")`. \
Skills are loaded in every future session automatically.
3. **Save memories** for cross-session context: \
`save_memory("api-conventions", "REST endpoints use camelCase...")`.
4. **Context is auto-saved** — session actions are logged automatically after each session.

### When to create/update:
- New file or folder created → update PROJECT.md file listing
- New dependency added → update PROJECT.md dependencies section
- Pattern discovered (naming, error handling, etc.) → create a skill
- Non-obvious decision made → save_memory("decisions", "chose X because...")

## Autonomous Documentation & Logging

You are expected to proactively maintain project documentation — not just when asked.

### On EVERY new project or first conversation:
1. Check if `PROJECT.md` or `README.md` exists — if not, **create one** with: project purpose, \
how to run it, file structure, and key decisions.
2. Call `recall_memory()` to check for existing project notes.

### After completing significant work:
1. **Update PROJECT.md** if you changed architecture or added files.
2. **Save conventions** — call `save_memory("conventions", ...)` for project patterns.
3. **Create skills** — write to `.agent/skills/` for reusable patterns.

### What to document automatically (no user prompt needed):
- Project setup instructions (how to run, build, test)
- File/folder purpose when creating project structure
- API contracts or interfaces between modules
- Configuration and environment requirements
- Known limitations or TODOs for future work
"""

SYSTEM_PROMPT_XML = (
    SYSTEM_PROMPT_NATIVE
    + """
## Tool Calling

To use a tool, output a tool_call block:

<tool_call>
{"name": "tool_name", "arguments": {"param1": "value1"}}
</tool_call>

You may make multiple tool calls in one response. After each tool executes, \
you will receive results in <tool_result> blocks. Continue reasoning with the \
results until you have a final answer.

When you are done and have the final answer, respond normally without any \
<tool_call> blocks.

## Available Tools

- read_file(file_path, start_line?, end_line?) — Read file with optional line range (1-based)
- write_file(file_path, content) — Create or overwrite a file
- edit_file(file_path, old_text, new_text) — Replace exact text (must match once)
- list_directory(path?) — List directory contents
- grep_search(pattern, path?, include_pattern?) — Search text/regex in files
- find_files(pattern, path?) — Find files by glob name
- run_command(command, working_dir?) — Execute shell command
- think(thought) — Extended thinking scratchpad
- manage_todo(action, task_id?, title?, status?) — Task tracking (add/update/list/clear)
- save_memory(key, content) — Save persistent note
- recall_memory(key?) — Recall note or list all
"""
)

# =============================================================================
# TOOL DEFINITIONS — OpenAI function-calling format
# =============================================================================

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read file contents. Use start_line/end_line for large files. Returns lines with line-number prefixes like '  42 | code here'. IMPORTANT: These prefixes are for display only — do NOT copy them into edit_file old_text/new_text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file (relative to workspace or absolute)"},
                    "start_line": {"type": "integer", "description": "First line (1-based, 0=beginning)", "default": 0},
                    "end_line": {"type": "integer", "description": "Last line (1-based, 0=end)", "default": 0},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file. Creates parent directories. Use edit_file for existing files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Destination path"},
                    "content": {"type": "string", "description": "Complete file content"},
                },
                "required": ["file_path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace exact text in a file. old_text must match the ACTUAL file content exactly (match once). Include 2-3 context lines. CRITICAL: Do NOT include line-number prefixes from read_file output (like '  42 | '). Use the raw code only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file"},
                    "old_text": {"type": "string", "description": "Exact text to find (must match once)"},
                    "new_text": {"type": "string", "description": "Replacement text"},
                },
                "required": ["file_path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List directory contents with sizes and type indicators.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path (default: workspace root)", "default": "."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep_search",
            "description": "Search for text/regex across files. Case-insensitive. Skips binaries and common non-code dirs.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Text or regex pattern"},
                    "path": {"type": "string", "description": "Search directory (default: workspace root)", "default": "."},
                    "include_pattern": {"type": "string", "description": "File glob filter (e.g. '*.py')", "default": ""},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files by name glob. Recursive. Use ** for deep matching.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', '**/test_*.js')"},
                    "path": {"type": "string", "description": "Search directory", "default": "."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command. Returns stdout, stderr, and exit code.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "working_dir": {"type": "string", "description": "Working directory (default: workspace root)", "default": ""},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": "Extended thinking scratchpad. Use to reason through complex problems before acting. Free and unlimited.",
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {"type": "string", "description": "Your reasoning and analysis"},
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "manage_todo",
            "description": "Task tracking. Actions: add (title required), update (task_id + status), list, clear. Status: not-started, in-progress, completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["add", "update", "list", "clear"]},
                    "task_id": {"type": "integer", "description": "Task ID for update", "default": 0},
                    "title": {"type": "string", "description": "Task title for add", "default": ""},
                    "status": {"type": "string", "enum": ["not-started", "in-progress", "completed"], "default": "not-started"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_memory",
            "description": "Save a persistent note across conversations. Use descriptive keys.",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key (alphanumeric, hyphens)"},
                    "content": {"type": "string", "description": "Content to store"},
                },
                "required": ["key", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recall_memory",
            "description": "Recall a memory by key, or list all memories (empty key).",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Memory key (empty = list all)", "default": ""},
                },
                "required": [],
            },
        },
    },
]


# =============================================================================
# DEFAULT SKILL — Seeded into .agent/skills/ on first run
# =============================================================================

_DEFAULT_CLEAN_CODE_SKILL = """\
# Clean Code & Software Engineering Principles

## Always Applies
These principles apply to ALL code you write or modify, in any language.

## SOLID Principles

- **Single Responsibility**: Each function/class/module does ONE thing well. If you can't describe what it does in one sentence without "and", split it.
- **Open/Closed**: Design for extension without modification. Use interfaces, callbacks, configuration — not editing existing working code to add features.
- **Liskov Substitution**: Subtypes must be substitutable for their base types. Don't override methods to throw "not supported" — that violates the contract.
- **Interface Segregation**: Don't force consumers to depend on methods they don't use. Prefer small, focused interfaces over large ones.
- **Dependency Inversion**: Depend on abstractions, not concretions. Pass dependencies in (constructor, function params) rather than creating them internally.

## Naming Conventions

- **Variables**: Descriptive nouns that reveal intent. `userCount` not `n`, `isVisible` not `flag`, `maxRetries` not `num`.
- **Functions**: Verb phrases that describe the action. `calculateTotal()` not `doStuff()`, `validateEmail()` not `check()`.
- **Booleans**: Prefix with `is`, `has`, `can`, `should`. `isValid` not `valid`, `hasPermission` not `permission`.
- **Constants**: UPPER_SNAKE_CASE with meaningful names. `MAX_RETRY_COUNT` not `MAX` or `CONST_3`.
- **Classes**: PascalCase nouns. `UserRepository` not `UR` or `UserRepoManagerHelper`.
- **Files**: Match the primary export. `user-service.js` contains `UserService`.
- **No abbreviations** unless universally understood (`id`, `url`, `html`). Write `configuration` not `cfg`, `message` not `msg`.

## Code Readability

- **Functions under 20 lines** — if longer, extract sub-functions with descriptive names.
- **Max 3 parameters** per function. Use an options/config object for more.
- **No magic numbers** — use named constants. `if (attempts > MAX_RETRIES)` not `if (attempts > 3)`.
- **Early returns** over deep nesting. Guard clauses at the top, happy path at the bottom.
- **Consistent formatting** — match the existing project style exactly.
- **Self-documenting code** — if you need a comment to explain *what* the code does, rename things instead. Comments explain *why*, not *what*.

## Encapsulation

- **Hide implementation details** — expose only what consumers need.
- **Private by default** — make fields/methods public only when there's a reason.
- **Immutable when possible** — prefer `const`/`readonly`/`final`. Mutate only when necessary.
- **Data + behavior together** — don't create "data bags" with separate "manager" classes. Put behavior on the objects that own the data.

## Error Handling

- **Fail fast** — validate inputs at boundaries, not deep inside.
- **Specific errors** — catch specific exceptions, not bare `catch`. Never swallow errors silently.
- **Error messages that help** — include what went wrong, what was expected, and how to fix it.
- **No error handling for impossible states** — if your types prevent it, don't check for it.

## DRY & YAGNI

- **Extract on the third occurrence** — first: write it, second: note it, third: extract.
- **Don't over-abstract** — premature abstraction is worse than duplication.
- **Build only what's needed now** — no speculative features.
- **Delete dead code** — commented-out code and unused functions are noise.

## Testing Mindset

- **Write testable code** — if it's hard to test, the design needs improvement.
- **Test behavior, not implementation** — tests should pass even if internals change.
- **Descriptive test names** — `test_expired_coupon_returns_zero_discount` not `test_coupon_3`.
"""


# =============================================================================
# TOOL ENGINE — Compact tool execution (shared by Pipe)
# =============================================================================


class _ToolEngine:
    """Executes tool calls against the file system."""

    def __init__(self, valves):
        self.v = valves
        self._todo_list: list[dict] = []
        self._todo_counter: int = 0

    # -- path helpers --

    def _resolve(self, file_path: str) -> str:
        ws = self.v.WORKSPACE_PATH
        resolved = os.path.realpath(
            file_path if os.path.isabs(file_path) else os.path.join(ws, file_path)
        )
        ws_real = os.path.realpath(ws)
        if not resolved.startswith(ws_real + os.sep) and resolved != ws_real:
            raise PermissionError(f"Path outside workspace: {file_path}")
        return resolved

    def _blocked(self, path: str) -> bool:
        patterns = [p.strip() for p in self.v.BLOCKED_PATHS.split(",") if p.strip()]
        bn = os.path.basename(path)
        return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(bn, p) for p in patterns)

    def _check_cmd(self, command: str) -> tuple:
        dangerous = [p.strip().lower() for p in self.v.DANGEROUS_PATTERNS.split(",") if p.strip()]
        if any(d in command.lower() for d in dangerous):
            return False, "Blocked dangerous pattern"
        try:
            parts = shlex.split(command)
        except ValueError:
            return False, "Invalid syntax"
        if not parts:
            return False, "Empty command"
        base = os.path.basename(parts[0])
        if self.v.SECURITY_MODE == "allowlist":
            allowed = {c.strip() for c in self.v.ALLOWED_COMMANDS.split(",") if c.strip()}
            if base not in allowed:
                return False, f"'{base}' not in allowlist"
        return True, "OK"

    # -- sandbox helpers --

    @property
    def _sandbox_active(self) -> bool:
        return bool(self.v.SANDBOX_URL)

    @property
    def _sandbox_full(self) -> bool:
        """True when ALL operations (files + commands) route through sandbox."""
        return self._sandbox_active and self.v.SANDBOX_ENABLED

    def _sandbox_headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.v.SANDBOX_API_KEY:
            h["Authorization"] = f"Bearer {self.v.SANDBOX_API_KEY}"
        return h

    def _sandbox_resolve(self, file_path: str) -> str:
        """Resolve a file path relative to the sandbox workspace root."""
        sw = self.v.SANDBOX_WORKSPACE
        if os.path.isabs(file_path):
            return file_path
        return f"{sw.rstrip('/')}/{file_path}"

    async def _sandbox_exec(self, command: str, working_dir: str = "") -> dict:
        """Execute a command in the sandbox container via HTTP POST.

        Tries common API patterns in order:
          POST {url}/execute
          POST {url}/api/execute
          POST {url}/api/v1/execute
        Returns dict with keys: stdout, stderr, exit_code.
        """
        if httpx is None:
            raise RuntimeError("httpx required for sandbox communication")

        base = self.v.SANDBOX_URL.rstrip("/")
        cwd = working_dir or self.v.SANDBOX_WORKSPACE
        payload = {
            "command": command,
            "timeout": self.v.COMMAND_TIMEOUT,
            "working_dir": cwd,
        }
        headers = self._sandbox_headers()
        timeout = float(self.v.COMMAND_TIMEOUT + 15)

        # Try multiple common API endpoint patterns
        endpoints = [
            f"{base}/execute",
            f"{base}/api/execute",
            f"{base}/api/v1/execute",
        ]
        last_error = None

        async with httpx.AsyncClient(timeout=timeout) as client:
            for url in endpoints:
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 404:
                        continue  # Try next endpoint
                    resp.raise_for_status()
                    data = resp.json()
                    # Normalize response — handle both {stdout,stderr,exit_code}
                    # and {output,exit_code} patterns
                    return {
                        "stdout": data.get("stdout", data.get("output", "")),
                        "stderr": data.get("stderr", ""),
                        "exit_code": data.get("exit_code", data.get("returncode", -1)),
                    }
                except httpx.HTTPStatusError as e:
                    last_error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
                    break  # Non-404 errors mean we found the endpoint but it failed
                except httpx.ConnectError as e:
                    last_error = f"Connection failed: {e}"
                    break  # Can't reach server at all
                except Exception as e:
                    last_error = str(e)
                    continue

        raise ConnectionError(
            f"Sandbox execute failed at {base}: {last_error}\n"
            "Verify SANDBOX_URL is correct and the terminal server is running."
        )

    async def _sandbox_file_read(self, file_path: str) -> str:
        """Read a file from the sandbox filesystem via HTTP."""
        base = self.v.SANDBOX_URL.rstrip("/")
        resolved = self._sandbox_resolve(file_path)
        headers = self._sandbox_headers()

        # Try dedicated file read API first
        endpoints = [
            (f"{base}/api/files/read", {"path": resolved}),
            (f"{base}/files/read", {"path": resolved}),
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for url, payload in endpoints:
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("content", "")
                except (httpx.HTTPStatusError, httpx.ConnectError):
                    continue

        # Fallback: use command execution to read the file
        result = await self._sandbox_exec(f"cat {shlex.quote(resolved)}")
        if result["exit_code"] != 0:
            raise FileNotFoundError(
                result["stderr"] or f"File not found in sandbox: {file_path}"
            )
        return result["stdout"]

    async def _sandbox_file_write(self, file_path: str, content: str) -> str:
        """Write a file to the sandbox filesystem via HTTP."""
        base = self.v.SANDBOX_URL.rstrip("/")
        resolved = self._sandbox_resolve(file_path)
        headers = self._sandbox_headers()

        # Try dedicated file write API first
        endpoints = [
            (f"{base}/api/files/write", {"path": resolved, "content": content}),
            (f"{base}/files/write", {"path": resolved, "content": content}),
        ]

        async with httpx.AsyncClient(timeout=30.0) as client:
            for url, payload in endpoints:
                try:
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 404:
                        continue
                    resp.raise_for_status()
                    data = resp.json()
                    return data.get("path", resolved)
                except (httpx.HTTPStatusError, httpx.ConnectError):
                    continue

        # Fallback: use command execution with heredoc
        # Use base64 to safely transfer content with special characters
        import base64
        b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        mkdir_cmd = f"mkdir -p $(dirname {shlex.quote(resolved)})"
        write_cmd = f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(resolved)}"
        result = await self._sandbox_exec(f"{mkdir_cmd} && {write_cmd}")
        if result["exit_code"] != 0:
            raise IOError(result["stderr"] or f"Failed to write file in sandbox: {file_path}")
        return resolved

    async def _sandbox_list_dir(self, path: str) -> str:
        """List directory contents in the sandbox."""
        resolved = self._sandbox_resolve(path)
        result = await self._sandbox_exec(
            f"ls -la {shlex.quote(resolved)} 2>/dev/null || echo 'Error: not a directory'"
        )
        return result["stdout"]

    async def _sandbox_grep(self, pattern: str, path: str, include_pattern: str = "") -> str:
        """Search for patterns in sandbox files."""
        resolved = self._sandbox_resolve(path)
        cmd = f"grep -rni {shlex.quote(pattern)} {shlex.quote(resolved)}"
        if include_pattern:
            cmd += f" --include={shlex.quote(include_pattern)}"
        cmd += " 2>/dev/null | head -50"
        result = await self._sandbox_exec(cmd)
        return result["stdout"] or f"No matches for '{pattern}'"

    async def _sandbox_find(self, pattern: str, path: str) -> str:
        """Find files by name in sandbox."""
        resolved = self._sandbox_resolve(path)
        cmd = f"find {shlex.quote(resolved)} -name {shlex.quote(pattern)} -type f 2>/dev/null | head -50"
        result = await self._sandbox_exec(cmd)
        return result["stdout"] or f"No files matching '{pattern}'"

    # -- tool implementations --

    async def execute(self, name: str, args: dict) -> str:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            _log_print("warning", f"Unknown tool requested: {name}")
            return f"Error: Unknown tool: {name}"
        try:
            # Strip unknown kwargs — models hallucinate extra parameters
            # (e.g. required_context=True on edit_file, old_text on write_file)
            sig = inspect.signature(fn)
            valid_params = set(sig.parameters.keys()) - {"self"}
            unknown = set(args.keys()) - valid_params
            if unknown:
                _log_print("warning", f"Tool '{name}' — stripping hallucinated args: {unknown}")
                args = {k: v for k, v in args.items() if k in valid_params}
            result = await fn(**args)
            return result
        except TypeError as e:
            _log_print("error", f"Tool '{name}' argument error: {e}")
            return f"Error: {name} argument error — {e}"
        except PermissionError as e:
            _log_print("warning", f"Tool '{name}' permission denied: {e}")
            return f"Error: {e}"
        except Exception as e:
            _log_print("error", f"Tool '{name}' unexpected error: {e}")
            return f"Error: {name} failed — {e}"

    async def _t_read_file(self, file_path: str, start_line: int = 0, end_line: int = 0) -> str:
        # ── Sandbox path ──
        if self._sandbox_full:
            try:
                raw = await self._sandbox_file_read(file_path)
                lines = raw.split("\n")
                total = len(lines)
                s = max(0, start_line - 1) if start_line > 0 else 0
                e = min(total, end_line) if end_line > 0 else total
                if (e - s) > self.v.MAX_READ_LINES:
                    e = s + self.v.MAX_READ_LINES
                numbered = [f"{i:4d} | {ln.rstrip()}" for i, ln in enumerate(lines[s:e], start=s + 1)]
                return f"[sandbox:{file_path} — {total} lines, showing {s+1}–{e}]\n" + "\n".join(numbered)
            except FileNotFoundError:
                return f"Error: Not found in sandbox: {file_path}"
            except Exception as e:
                return f"Error: Sandbox read failed — {e}"

        # ── Local path ──
        resolved = self._resolve(file_path)
        if self._blocked(resolved):
            return f"Error: Blocked: {file_path}"
        if not os.path.isfile(resolved):
            return f"Error: Not found: {file_path}"
        size_kb = os.path.getsize(resolved) / 1024
        if size_kb > self.v.MAX_FILE_SIZE_KB:
            return f"Error: File {size_kb:.0f}KB exceeds {self.v.MAX_FILE_SIZE_KB}KB limit. Use line range."
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        s = max(0, start_line - 1) if start_line > 0 else 0
        e = min(total, end_line) if end_line > 0 else total
        if (e - s) > self.v.MAX_READ_LINES:
            e = s + self.v.MAX_READ_LINES
        numbered = [f"{i:4d} | {ln.rstrip()}" for i, ln in enumerate(lines[s:e], start=s + 1)]
        return f"[{file_path} — {total} lines, showing {s+1}–{e}]\n" + "\n".join(numbered)

    async def _t_write_file(self, file_path: str, content: str) -> str:
        # ── Sandbox path ──
        if self._sandbox_full:
            try:
                await self._sandbox_file_write(file_path, content)
                lc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
                return f"Created in sandbox: {file_path} ({lc} lines, {len(content.encode('utf-8'))} bytes)"
            except Exception as e:
                return f"Error: Sandbox write failed — {e}"

        # ── Local path ──
        resolved = self._resolve(file_path)
        if self._blocked(resolved):
            return f"Error: Blocked: {file_path}"
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        existed = os.path.isfile(resolved)
        old_size = os.path.getsize(resolved) if existed else 0
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        new_size = os.path.getsize(resolved)
        lc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        result = f"{'Updated' if existed else 'Created'}: {file_path} ({lc} lines, {new_size} bytes)"
        # Warn if overwriting with drastically smaller content (likely truncated by token limit)
        if existed and old_size > 500 and new_size < old_size * 0.1:
            result += (
                f"\n\u26a0\ufe0f WARNING: File shrank from {old_size}B to {new_size}B "
                f"({new_size*100//old_size}% of original). This may indicate your content "
                "was truncated. Re-read the original with read_file and include ALL content."
            )
        return result

    @staticmethod
    def _strip_line_numbers(text: str) -> str:
        """Strip read_file line-number prefixes (e.g. '  42 | code') and headers if present."""
        lines = text.split("\n")
        # Strip read_file header line: [filename — N lines, showing X–Y]
        if lines and re.match(r"^\[.+ — \d+ lines", lines[0]):
            lines = lines[1:]
        if not lines:
            return text
        # Check if lines have the numbered prefix pattern — aggressive threshold
        numbered = sum(1 for ln in lines if re.match(r"^\s*\d+\s*\|\s?", ln))
        if numbered >= 1 and (numbered >= len(lines) * 0.3 or numbered >= 2):
            stripped = [re.sub(r"^\s*\d+\s*\|\s?", "", ln) for ln in lines]
            return "\n".join(stripped)
        return text

    async def _t_edit_file(self, file_path: str, old_text: str, new_text: str) -> str:
        # ── Sandbox path (read via sandbox, edit in memory, write back) ──
        if self._sandbox_full:
            try:
                content = await self._sandbox_file_read(file_path)
            except FileNotFoundError:
                return f"Error: Not found in sandbox: {file_path}"
            except Exception as e:
                return f"Error: Sandbox read failed — {e}"
            old_clean = self._strip_line_numbers(old_text)
            new_clean = self._strip_line_numbers(new_text)
            count = content.count(old_clean)
            if count == 0:
                first_line = old_clean.split("\n")[0].strip()
                if first_line and first_line in content:
                    return f"Error: old_text not found as exact block in {file_path}. First line exists but full match failed — check whitespace."
                return f"Error: old_text not found in {file_path}. Read the file first and copy the exact text."
            if count > 1:
                return f"Error: old_text matches {count} times in {file_path}. Add more context lines."
            try:
                await self._sandbox_file_write(file_path, content.replace(old_clean, new_clean, 1))
                return f"Edited in sandbox: {file_path} ({old_clean.count(chr(10))+1} → {new_clean.count(chr(10))+1} lines)"
            except Exception as e:
                return f"Error: Sandbox write failed — {e}"

        # ── Local path ──
        resolved = self._resolve(file_path)
        if self._blocked(resolved):
            return f"Error: Blocked: {file_path}"
        if not os.path.isfile(resolved):
            return f"Error: Not found: {file_path}"
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # Auto-strip read_file line-number prefixes if the model copied them
        old_clean = self._strip_line_numbers(old_text)
        new_clean = self._strip_line_numbers(new_text)
        was_stripped = old_clean != old_text or new_clean != new_text
        count = content.count(old_clean)
        if count == 0:
            hint = " (line-number prefixes were auto-stripped)" if was_stripped else ""
            # Try to find partial match for better error message
            first_line = old_clean.split("\n")[0].strip()
            if first_line and first_line in content:
                return f"Error: old_text not found as exact block in {file_path}{hint}. First line exists but full match failed — check whitespace and context."
            return f"Error: old_text not found in {file_path}{hint}. Read the file first and copy the exact text."
        if count > 1:
            return f"Error: old_text matches {count} times in {file_path}. Add more context lines."
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content.replace(old_clean, new_clean, 1))
        note = " (auto-stripped line numbers)" if was_stripped else ""
        return f"Edited: {file_path} ({old_clean.count(chr(10))+1} → {new_clean.count(chr(10))+1} lines){note}"

    async def _t_list_directory(self, path: str = ".") -> str:
        # ── Sandbox path ──
        if self._sandbox_full:
            try:
                raw = await self._sandbox_list_dir(path)
                return f"[sandbox:{path}]\n{raw}"
            except Exception as e:
                return f"Error: Sandbox list failed — {e}"

        # ── Local path ──
        resolved = self._resolve(path)
        if not os.path.isdir(resolved):
            return f"Error: Not a directory: {path}"
        entries = sorted(os.listdir(resolved))
        lines, dirs, files = [], 0, 0
        for e in entries:
            full = os.path.join(resolved, e)
            if os.path.isdir(full):
                lines.append(f"  {e}/")
                dirs += 1
            else:
                sz = os.path.getsize(full)
                h = f"{sz/(1024*1024):.1f}M" if sz >= 1024*1024 else f"{sz/1024:.1f}K" if sz >= 1024 else f"{sz}B"
                lines.append(f"  {e}  ({h})")
                files += 1
        return f"[{path} — {dirs} dirs, {files} files]\n" + "\n".join(lines)

    async def _t_grep_search(self, pattern: str, path: str = ".", include_pattern: str = "") -> str:
        # ── Sandbox path ──
        if self._sandbox_full:
            try:
                raw = await self._sandbox_grep(pattern, path, include_pattern)
                lines = raw.strip().split("\n") if raw.strip() else []
                if not lines or raw.startswith("No matches"):
                    return f"No matches for '{pattern}'"
                trunc = " (truncated)" if len(lines) >= 50 else ""
                return f"[{len(lines)} matches{trunc}]\n" + "\n".join(lines)
            except Exception as e:
                return f"Error: Sandbox search failed — {e}"

        # ── Local path ──
        resolved = self._resolve(path)
        if not os.path.isdir(resolved):
            return f"Error: Not a directory: {path}"
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return f"Error: Bad regex: {e}"
        skip = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
        results, searched = [], 0
        limit = self.v.MAX_SEARCH_RESULTS
        for root, dirs, files in os.walk(resolved):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for fn in files:
                if include_pattern and not fnmatch.fnmatch(fn, include_pattern):
                    continue
                fp = os.path.join(root, fn)
                try:
                    with open(fp, "r", encoding="utf-8", errors="strict") as f:
                        searched += 1
                        for num, line in enumerate(f, 1):
                            if rx.search(line):
                                results.append(f"{os.path.relpath(fp, resolved)}:{num}: {line.rstrip()}")
                                if len(results) >= limit:
                                    break
                except (UnicodeDecodeError, PermissionError, OSError):
                    continue
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
        if not results:
            return f"No matches for '{pattern}' ({searched} files searched)"
        trunc = f" (truncated)" if len(results) >= limit else ""
        return f"[{len(results)} matches{trunc}, {searched} files]\n" + "\n".join(results)

    async def _t_find_files(self, pattern: str, path: str = ".") -> str:
        # ── Sandbox path ──
        if self._sandbox_full:
            try:
                raw = await self._sandbox_find(pattern, path)
                lines = raw.strip().split("\n") if raw.strip() else []
                if not lines or raw.startswith("No files"):
                    return f"No files matching '{pattern}'"
                return f"[{len(lines)} files]\n" + "\n".join(f"  {m}" for m in lines)
            except Exception as e:
                return f"Error: Sandbox find failed — {e}"

        # ── Local path ──
        resolved = self._resolve(path)
        if not os.path.isdir(resolved):
            return f"Error: Not a directory: {path}"
        import glob as gm
        gp = os.path.join(resolved, pattern) if "**" in pattern else os.path.join(resolved, "**", pattern)
        matches = []
        for m in gm.glob(gp, recursive=True):
            rel = os.path.relpath(m, resolved)
            if any(p.startswith(".") for p in Path(rel).parts):
                continue
            matches.append(rel + ("/" if os.path.isdir(m) else ""))
            if len(matches) >= self.v.MAX_SEARCH_RESULTS:
                break
        if not matches:
            return f"No files matching '{pattern}'"
        return f"[{len(matches)} files]\n" + "\n".join(f"  {m}" for m in sorted(matches))

    async def _t_run_command(self, command: str, working_dir: str = "") -> str:
        ok, reason = self._check_cmd(command)
        if not ok:
            return f"Error: {reason}"
        # Detect anti-pattern: using Python ast.parse on non-Python files
        if "ast.parse" in command:
            non_py = re.search(r"open\(['\"]([^'\"]+\.(js|html|css|json|ts|jsx|tsx|vue|svelte))['\"]" , command)
            if non_py:
                fname, ext = non_py.group(1), non_py.group(2)
                return (
                    f"Error: Cannot use Python ast.parse on .{ext} files — it only works for Python.\n"
                    f"For JavaScript: node -e \"require('fs').readFileSync('{fname}','utf8')\"\n"
                    f"For HTML/CSS: use read_file to visually inspect the content."
                )
        # Detect literal example paths from system prompt
        if "path/to/test" in command:
            return (
                "Error: 'path/to/test' is an example placeholder, not a real path. "
                "Use find_files('*test*') to locate actual test files first."
            )

        # ── Sandbox execution path ──
        if self._sandbox_active:
            try:
                cwd = working_dir or self.v.SANDBOX_WORKSPACE
                result = await self._sandbox_exec(command, cwd)
                parts = []
                if result["stdout"]:
                    parts.append(result["stdout"])
                if result["stderr"]:
                    parts.append(f"[stderr]\n{result['stderr']}")
                out = "\n".join(parts) or "(no output)"
                if len(out) > 50_000:
                    out = out[:50_000] + "\n... (truncated)"
                result_str = f"[sandbox · exit code: {result['exit_code']}]\n{out}"
                if result["exit_code"] == 127:
                    result_str += (
                        "\n\nCommand not found in sandbox. Check that the required "
                        "tool is installed in your sandbox container."
                    )
                return result_str
            except ConnectionError as e:
                return f"Error: Sandbox unavailable — {e}"
            except Exception as e:
                return f"Error: Sandbox execution failed — {e}"

        # ── Local execution path ──
        cwd = self._resolve(working_dir) if working_dir else self.v.WORKSPACE_PATH
        if not os.path.isdir(cwd):
            return f"Error: Bad directory: {working_dir}"
        shell = self.v.SHELL if os.path.exists(self.v.SHELL) else None
        try:
            r = subprocess.run(command, shell=True, executable=shell, capture_output=True,
                               text=True, timeout=self.v.COMMAND_TIMEOUT, cwd=cwd)
            parts = []
            if r.stdout:
                parts.append(r.stdout)
            if r.stderr:
                parts.append(f"[stderr]\n{r.stderr}")
            out = "\n".join(parts) or "(no output)"
            if len(out) > 50_000:
                out = out[:50_000] + f"\n... (truncated)"
            result_str = f"[exit code: {r.returncode}]\n{out}"
            # Add guidance for common failures
            if r.returncode == 127:
                result_str += (
                    "\n\nThe command was not found. This container does not have "
                    "node/npm installed. For JavaScript validation, use read_file "
                    "to visually inspect the code, or write a Python script that "
                    "checks for syntax patterns."
                )
            return result_str
        except subprocess.TimeoutExpired:
            return f"Error: Timed out after {self.v.COMMAND_TIMEOUT}s"

    async def _t_think(self, thought: str) -> str:
        return f"Thought recorded ({len(thought)} chars). Proceed with your plan."

    async def _t_manage_todo(self, action: str, task_id: int = 0, title: str = "", status: str = "not-started") -> str:
        if action == "add":
            if not title:
                return "Error: title required"
            self._todo_counter += 1
            self._todo_list.append({"id": self._todo_counter, "title": title, "status": "not-started"})
        elif action == "update":
            if status not in ("not-started", "in-progress", "completed"):
                return f"Error: bad status '{status}'"
            found = False
            for t in self._todo_list:
                if t["id"] == task_id:
                    t["status"] = status
                    found = True
                    break
            if not found:
                return f"Error: task {task_id} not found"
        elif action == "clear":
            self._todo_list.clear()
            self._todo_counter = 0
            return "Cleared."
        elif action != "list":
            return "Error: use add/update/list/clear"
        if not self._todo_list:
            return "No tasks."
        icons = {"not-started": "○", "in-progress": "◐", "completed": "●"}
        lines = [f"  {icons.get(t['status'],'?')} [{t['id']}] {t['title']} ({t['status']})" for t in self._todo_list]
        done = sum(1 for t in self._todo_list if t["status"] == "completed")
        return f"Tasks:\n" + "\n".join(lines) + f"\n\nProgress: {done}/{len(self._todo_list)}"

    async def _t_save_memory(self, key: str, content: str) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", key).strip("-")
        if not safe:
            return "Error: invalid key"
        d = self.v.MEMORY_DIR
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"{safe}.md")
        if not os.path.realpath(p).startswith(os.path.realpath(d)):
            return "Error: invalid key"
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {key}\n_Updated: {datetime.now(timezone.utc).isoformat()}_\n\n{content}")
        return f"Saved: '{key}' ({len(content)} chars)"

    async def _t_recall_memory(self, key: str = "") -> str:
        d = self.v.MEMORY_DIR
        if not os.path.isdir(d):
            return "No memories yet."
        if not key:
            files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
            if not files:
                return "No memories yet."
            return "Memories:\n" + "\n".join(f"  • {f[:-3]}" for f in files)
        safe = re.sub(r"[^a-zA-Z0-9_-]", "-", key).strip("-")
        p = os.path.join(d, f"{safe}.md")
        if not os.path.realpath(p).startswith(os.path.realpath(d)):
            return "Error: invalid key"
        if not os.path.isfile(p):
            return f"'{key}' not found. Use recall_memory() to list all."
        with open(p, "r", encoding="utf-8") as f:
            return f.read()


# =============================================================================
# PIPE
# =============================================================================


class Pipe:
    """
    Code Agent — Claude Code-style AI coding agent for Open WebUI.

    Orchestrates a reasoning model with tool-calling in an agent loop.
    Select as your model, then configure MODEL_ID in Valves to point at
    the underlying LLM (e.g. Ollama, LM Studio, or any OpenAI-compatible endpoint).
    """

    class Valves(BaseModel):
        # -- LLM --
        API_BASE_URL: str = Field(
            default="http://ollama:11434/v1",
            description=(
                "OpenAI-compatible API base URL for the reasoning model. "
                "Examples: http://ollama:11434/v1, http://host.docker.internal:1234/v1"
            ),
        )
        API_KEY: str = Field(
            default="ollama",
            description="API key (use 'ollama' for local Ollama, or your API key).",
        )
        MODEL_ID: str = Field(
            default="",
            description="Model ID for reasoning (e.g. 'qwen2.5-coder:32b'). REQUIRED.",
        )

        # -- Agent behaviour --
        MAX_ITERATIONS: int = Field(
            default=50,
            description="Maximum tool-call loop iterations per request. Safety ceiling — agent exits naturally when done.",
        )
        TEMPERATURE: float = Field(
            default=0.1,
            description="LLM temperature (lower = more deterministic).",
        )
        MAX_TOKENS: int = Field(
            default=32768,
            description="Max tokens per LLM response. Increase for thinking models that use tokens for reasoning.",
        )
        TOOL_CALL_FORMAT: str = Field(
            default="auto",
            description=(
                "Tool calling format: "
                "'auto' (try native, fall back to XML), "
                "'native' (OpenAI function calling), "
                "'xml' (tool calls via XML tags in text)."
            ),
        )
        EMIT_STATUS: bool = Field(
            default=True,
            description="Show real-time status updates in the chat.",
        )
        DEBUG_LOG: bool = Field(
            default=True,
            description=(
                "Enable detailed debug logging to container stdout. "
                "View with: docker compose logs -f openwebui | grep code_agent"
            ),
        )
        REQUEST_TIMEOUT: int = Field(
            default=120,
            description="HTTP timeout in seconds for LLM API calls.",
        )

        # -- Security (mirrors Tool valves) --
        WORKSPACE_PATH: str = Field(
            default="/app/backend/data/code_agent/workspace",
            description="Root workspace for file operations. Isolated from OWUI internal data.",
        )
        SECURITY_MODE: str = Field(
            default="allowlist",
            description="'allowlist', 'confirm', or 'unrestricted'.",
        )
        BLOCKED_PATHS: str = Field(
            default=".env,.env.*,**/secrets/**,/etc/shadow,/etc/passwd,**/.git/config",
            description="Blocked path patterns.",
        )
        ALLOWED_COMMANDS: str = Field(
            default=(
                "ls,cat,head,tail,find,grep,rg,wc,diff,file,stat,du,df,pwd,"
                "echo,printf,sort,uniq,tr,cut,awk,sed,jq,"
                "git,python,python3,node,npm,npx,pip,pip3,"
                "cargo,go,make,cmake,gcc,g++,rustc,javac,java,dotnet,ruby,perl,"
                "bash,sh,curl,wget,tar,zip,unzip,gzip,gunzip"
            ),
            description="Allowed commands for allowlist mode.",
        )
        DANGEROUS_PATTERNS: str = Field(
            default="rm -rf /,rm -rf /*,mkfs,dd if=,:(){ :|:&,> /dev/sd,chmod 777 /,chown root",
            description="Always-blocked patterns.",
        )
        COMMAND_TIMEOUT: int = Field(default=30)
        MAX_READ_LINES: int = Field(default=500)
        MAX_SEARCH_RESULTS: int = Field(default=50)
        MAX_FILE_SIZE_KB: int = Field(default=1024)
        MEMORY_DIR: str = Field(
            default="/app/backend/data/code_agent/memory",
            description="Persistent memory directory.",
        )
        SHELL: str = Field(default="/bin/bash")

        # -- Sandbox / Open Terminal --
        SANDBOX_URL: str = Field(
            default="",
            description=(
                "URL of a sandbox terminal server for safe command & file operations. "
                "When set, run_command routes through this server instead of local subprocess. "
                "Example: http://localhost:8000"
            ),
        )
        SANDBOX_API_KEY: str = Field(
            default="",
            description="API key for sandbox server authentication (sent as Bearer token).",
        )
        SANDBOX_WORKSPACE: str = Field(
            default="/workspace",
            description=(
                "Root workspace path inside the sandbox container. "
                "File operations are relative to this path when sandbox is active."
            ),
        )
        SANDBOX_ENABLED: bool = Field(
            default=False,
            description=(
                "When True AND SANDBOX_URL is set, ALL tool operations (commands + files) "
                "route through the sandbox. When False, only run_command uses the sandbox "
                "while file operations remain local."
            ),
        )

        SYSTEM_PROMPT_PATH: str = Field(
            default="",
            description=(
                "Path to an external system prompt markdown file. "
                "If set and the file exists, its content replaces the built-in prompt. "
                "Example: /host_project/tools/code-generation/code-agent-system-prompt.md"
            ),
        )

    def __init__(self):
        self.valves = self.Valves()
        self._engine: Optional[_ToolEngine] = None
        self._emitter: Optional[Callable] = None

    def _get_engine(self) -> _ToolEngine:
        if self._engine is None:
            self._engine = _ToolEngine(self.valves)
        return self._engine

    def _log(self, level: str, msg: str, **kwargs):
        """Structured debug logging — stdout + stderr + file."""
        if not self.valves.DEBUG_LOG:
            return
        _log_print(level, msg, **kwargs)

    async def _debug_emit(self, msg: str):
        """Log debug info to file/stdout only. Does NOT emit to UI status stream
        to keep the user-facing status clean."""
        if not self.valves.DEBUG_LOG:
            return
        _log_print("debug", msg)

    # -- Pipe registration --

    def pipes(self) -> list[dict]:
        return [{"id": "code-agent", "name": "Code Agent"}]

    # -- Helpers --

    async def _emit(self, emitter, text: str, done: bool = False):
        if emitter and self.valves.EMIT_STATUS:
            await emitter({"type": "status", "data": {"description": text, "done": done}})

    @staticmethod
    def _describe_action(tool_name: str, tool_args: dict) -> str:
        """Human-readable description of a tool call for status display."""
        fp = tool_args.get("file_path", tool_args.get("path", ""))
        short_fp = os.path.basename(fp) if fp else ""
        if tool_name == "read_file":
            sl, el = tool_args.get("start_line", 0), tool_args.get("end_line", 0)
            rng = f" (lines {sl}-{el})" if sl or el else ""
            return f"Reading {short_fp}{rng}"
        elif tool_name == "write_file":
            c = tool_args.get("content", "")
            lc = c.count("\n") + 1 if c else 0
            return f"Writing {short_fp} ({lc} lines)"
        elif tool_name == "edit_file":
            old = tool_args.get("old_text", "")
            new = tool_args.get("new_text", "")
            ol = old.count("\n") + 1 if old else 0
            nl = new.count("\n") + 1 if new else 0
            return f"Editing {short_fp} ({ol}\u2192{nl} lines)"
        elif tool_name == "run_command":
            cmd = tool_args.get("command", "")[:60]
            return f"Running: {cmd}"
        elif tool_name == "find_files":
            return f"Scanning for {tool_args.get('pattern', '*')}"
        elif tool_name == "grep_search":
            return f"Searching: {tool_args.get('pattern', '')[:40]}"
        elif tool_name == "list_directory":
            return f"Listing {short_fp or 'workspace'}"
        elif tool_name == "think":
            t = tool_args.get("thought", "")
            preview = (t[:100] + "...") if len(t) > 100 else t
            return f"Thinking: {preview}"
        elif tool_name == "manage_todo":
            act = tool_args.get("action", "")
            title = tool_args.get("title", "")
            return f"Todo: {act}" + (f" \u2014 {title}" if title else "")
        return f"{tool_name}()"

    @staticmethod
    def _extract_intent(content: str) -> str:
        """Extract a brief intent statement from model content for status display.
        Returns first meaningful sentence, max ~80 chars."""
        if not content:
            return ""
        # Strip markdown formatting
        text = re.sub(r'[*_`#]+', '', content).strip()
        # Get first sentence (period, newline, or colon break)
        m = re.match(r'(.+?(?:[.!?:]\s|$))', text, re.DOTALL)
        if m:
            sentence = m.group(1).strip().rstrip(':')
            if len(sentence) > 80:
                sentence = sentence[:77] + "..."
            return sentence
        # Fallback: first 80 chars
        return text[:77] + "..." if len(text) > 80 else text

    @staticmethod
    def _describe_result(tool_name: str, result: str) -> str:
        """Brief human-readable result description for status display."""
        if result.startswith("Error:"):
            first = result.split("\n")[0][:80]
            # Add recovery hint based on error type
            if "old_text not found" in result:
                return f"\u2717 Edit failed \u2014 text mismatch, will re-read"
            elif "not found" in result.lower() or "does not exist" in result.lower():
                return f"\u2717 File not found"
            elif "Permission" in result:
                return f"\u2717 Permission denied"
            return f"\u2717 {first}"
        if tool_name == "read_file":
            m = re.match(r"\[.+ \u2014 (\d+) lines", result)
            return f"\u2713 Read {m.group(1)} lines" if m else "\u2713 Read OK"
        elif tool_name == "write_file":
            return f"\u2713 Written"
        elif tool_name == "edit_file":
            return f"\u2713 Edit applied"
        elif tool_name == "run_command":
            m = re.match(r"\[exit code: (\d+)\]", result)
            if m:
                code = int(m.group(1))
                if code == 0:
                    return "\u2713 Passed"
                elif code == 127:
                    return "\u2717 Command not found \u2014 not installed in container"
                else:
                    return f"\u2717 Failed (exit {code})"
            return "\u2713 Done"
        elif tool_name == "find_files":
            m = re.match(r"\[(\d+) files?\]", result)
            return f"\u2713 Found {m.group(1)} files" if m else "\u2713 Search done"
        elif tool_name == "grep_search":
            m = re.match(r"\[(\d+) match", result)
            return f"\u2713 {m.group(1)} matches" if m else "\u2713 No matches"
        elif tool_name == "think":
            return "\u2713 Planning complete"
        return f"\u2713 OK"

    def _get_system_prompt(self) -> str:
        # Try external file first
        if self.valves.SYSTEM_PROMPT_PATH:
            try:
                with open(self.valves.SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
                    external = f.read().strip()
                if external:
                    self._log("debug", "Loaded external system prompt",
                              path=self.valves.SYSTEM_PROMPT_PATH, chars=len(external))
                    fmt = self.valves.TOOL_CALL_FORMAT.lower()
                    if fmt == "xml":
                        return external + SYSTEM_PROMPT_XML.split("## Tool Calling", 1)[-1]
                    return external
            except (OSError, IOError) as e:
                self._log("warning", "Failed to load external prompt, using built-in",
                          path=self.valves.SYSTEM_PROMPT_PATH, error=str(e))
        fmt = self.valves.TOOL_CALL_FORMAT.lower()
        self._log("debug", "Using built-in system prompt", format=fmt)
        if fmt == "xml":
            base = SYSTEM_PROMPT_XML
        else:
            base = SYSTEM_PROMPT_NATIVE

        # Append .agent context, skills, and saved memories
        agent_context = self._load_agent_context()
        if agent_context:
            base += "\n\n" + agent_context

        # Append saved memories for cross-session continuity
        memories = self._load_saved_memories()
        if memories:
            base += "\n\n" + memories

        # Append sandbox context when configured
        if self.valves.SANDBOX_URL:
            sandbox_note = (
                "\n\n## Sandbox Environment\n"
                "A sandbox terminal server is configured for safe code execution.\n"
                "- `run_command` executes in the **sandbox container** (not the host).\n"
            )
            if self.valves.SANDBOX_ENABLED:
                sandbox_note += (
                    "- **Full sandbox mode**: ALL file operations (read, write, edit, list, "
                    "grep, find) also use the sandbox filesystem.\n"
                    f"- Sandbox workspace root: `{self.valves.SANDBOX_WORKSPACE}`\n"
                    "- `.agent/` memory and context still persist locally.\n"
                )
            else:
                sandbox_note += (
                    "- File tools (read_file, write_file, etc.) still operate on the **local** workspace.\n"
                    "- Only commands are sandboxed for safety.\n"
                )
            base += sandbox_note

        return base

    def _load_agent_context(self) -> str:
        """Load .agent/PROJECT.md, context.md, and skills/*.md from workspace."""
        ws = self.valves.WORKSPACE_PATH
        agent_dir = os.path.join(ws, ".agent")
        parts = []

        # Load PROJECT.md (equivalent to CLAUDE.md — project overview and conventions)
        project_path = os.path.join(agent_dir, "PROJECT.md")
        if os.path.isfile(project_path):
            try:
                with open(project_path, "r", encoding="utf-8") as f:
                    # Cap at 200 lines to keep context tight (matches Claude Code pattern)
                    project_lines = f.readlines()[:200]
                    project = "".join(project_lines).strip()
                if project:
                    parts.append(f"## Project (from .agent/PROJECT.md)\n\n{project}")
                    self._log("debug", "Loaded .agent/PROJECT.md",
                              chars=len(project), lines=len(project_lines))
            except (OSError, IOError):
                pass

        # Load context.md (session history — equivalent to auto memory)
        ctx_path = os.path.join(agent_dir, "context.md")
        if os.path.isfile(ctx_path):
            try:
                with open(ctx_path, "r", encoding="utf-8") as f:
                    ctx = f.read().strip()
                if ctx and "No sessions yet" not in ctx:
                    # Only include last ~3000 chars of session history to save context
                    if len(ctx) > 3000:
                        ctx = "...\n" + ctx[-3000:]
                    parts.append(f"## Recent Sessions (from .agent/context.md)\n\n{ctx}")
                    self._log("debug", "Loaded .agent/context.md", chars=len(ctx))
            except (OSError, IOError):
                pass

        # Load skills
        skills_dir = os.path.join(agent_dir, "skills")
        if os.path.isdir(skills_dir):
            skill_files = sorted(f for f in os.listdir(skills_dir)
                                 if f.endswith(".md") and f != "README.md")
            for sf in skill_files:
                try:
                    with open(os.path.join(skills_dir, sf), "r", encoding="utf-8") as f:
                        skill = f.read().strip()
                    if skill:
                        parts.append(f"## Skill: {sf[:-3]}\n\n{skill}")
                        self._log("debug", f"Loaded skill: {sf}", chars=len(skill))
                except (OSError, IOError):
                    pass

        if parts:
            return "\n---\n\n# Agent Context & Skills\n\n" + "\n\n---\n\n".join(parts)
        return ""

    def _load_saved_memories(self) -> str:
        """Load saved memories from MEMORY_DIR for cross-session continuity."""
        mem_dir = self.valves.MEMORY_DIR
        if not os.path.isdir(mem_dir):
            return ""
        files = sorted(f for f in os.listdir(mem_dir) if f.endswith(".md"))
        if not files:
            return ""
        parts = []
        total_chars = 0
        for mf in files[:10]:  # Cap at 10 memory files to avoid prompt bloat
            try:
                with open(os.path.join(mem_dir, mf), "r", encoding="utf-8") as f:
                    content = f.read().strip()
                if content and total_chars + len(content) < 4000:  # Cap total at 4k chars
                    parts.append(f"### {mf[:-3]}\n{content}")
                    total_chars += len(content)
                    self._log("debug", f"Loaded memory: {mf}", chars=len(content))
            except (OSError, IOError):
                pass
        if parts:
            return "# Saved Memories (from prior sessions)\n\n" + "\n\n".join(parts)
        return ""

    def _save_agent_context(self, actions_taken: list[str], edit_failures: dict, user_request: str = ""):
        """Auto-update .agent/context.md with accumulated session history."""
        agent_dir = os.path.join(self.valves.WORKSPACE_PATH, ".agent")
        ctx_path = os.path.join(agent_dir, "context.md")
        try:
            os.makedirs(agent_dir, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")

            # Build current session entry
            session_lines: list[str] = []
            session_lines.append(f"\n### {ts}\n")
            if user_request:
                session_lines.append(f"**Request:** {user_request[:200]}\n")
            if actions_taken:
                edits = [a for a in actions_taken if "OK" in a or a.startswith("write_file")]
                reads = [a for a in actions_taken if a.startswith("read_file") or a.startswith("grep_search")]
                cmds = [a for a in actions_taken if a.startswith("run_command")]
                summary_parts = []
                if edits:
                    summary_parts.append(f"{len(edits)} edits")
                if reads:
                    summary_parts.append(f"{len(reads)} reads")
                if cmds:
                    summary_parts.append(f"{len(cmds)} commands")
                if summary_parts:
                    session_lines.append(f"**Summary:** {', '.join(summary_parts)}\n")
                session_lines.append("**Actions:**\n")
                for a in actions_taken[-15:]:
                    session_lines.append(f"- {a}\n")

            failed = {f: c for f, c in edit_failures.items() if c >= 2}
            if failed:
                session_lines.append("**Issues:**\n")
                for f, c in failed.items():
                    session_lines.append(f"- `{f}` had {c} edit failures\n")

            # Load existing context and preserve session history (keep last 10 sessions)
            existing_sessions: list[str] = []
            workspace_section = ""
            if os.path.isfile(ctx_path):
                try:
                    with open(ctx_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                    # Extract previous session entries (marked by ### timestamp headers)
                    import re as _re
                    session_blocks = _re.split(r'(?=\n### \d{4}-\d{2}-\d{2})', old_content)
                    for block in session_blocks:
                        if block.strip().startswith("### 20"):
                            existing_sessions.append(block)
                    # Extract workspace files section if present
                    ws_match = _re.search(r'(## Workspace Files\n.+)', old_content, _re.DOTALL)
                    if ws_match:
                        workspace_section = ws_match.group(1)
                except (OSError, IOError):
                    pass

            # Keep only last 9 sessions + current = 10 total
            existing_sessions = existing_sessions[-9:]

            # Build full context file
            lines = [f"# Project Context\n", f"\n_Last updated: {ts}_\n"]
            lines.append("\n## Session History\n")
            for s in existing_sessions:
                lines.append(s)
            lines.extend(session_lines)

            # Refresh workspace file listing
            ws = self.valves.WORKSPACE_PATH
            if os.path.isdir(ws):
                all_files = []
                for root, dirs, files in os.walk(ws):
                    dirs[:] = [d for d in dirs if d not in ('.agent', '__pycache__', 'node_modules', '.git')]
                    for f in files:
                        rel = os.path.relpath(os.path.join(root, f), ws)
                        all_files.append(rel)
                if all_files:
                    lines.append("\n## Workspace Files\n")
                    for f in sorted(all_files)[:30]:
                        lines.append(f"- {f}\n")
                    if len(all_files) > 30:
                        lines.append(f"- ... and {len(all_files) - 30} more\n")

            with open(ctx_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            self._log("debug", "Saved .agent/context.md", actions=len(actions_taken),
                      total_sessions=len(existing_sessions) + 1)
        except (OSError, IOError) as e:
            self._log("warning", "Failed to save .agent/context.md", error=str(e))

    def _auto_save_session(self, engine, actions_taken: list[str],
                           edit_failures: dict, user_request: str):
        """Programmatic post-session memory save (runs synchronously, no LLM needed).
        Saves a session summary to MEMORY_DIR and refreshes PROJECT.md file listing."""
        try:
            ts_key = datetime.now().strftime("%Y%m%d-%H%M")
            edits = [a for a in actions_taken if "OK" in a or a.startswith("write_file")]
            reads = [a for a in actions_taken if a.startswith("read_file")]
            searches = [a for a in actions_taken if a.startswith("grep_search")]

            # Build meaningful session summary
            parts = [f"Request: {user_request[:150]}"]
            if edits:
                parts.append(f"Changed: {'; '.join(e[:60] for e in edits[:5])}")
            if reads:
                # Extract unique filenames from read actions
                read_files = set()
                for r in reads:
                    m = re.match(r"read_file\((.+?)\)", r)
                    if m:
                        read_files.add(os.path.basename(m.group(1)))
                if read_files:
                    parts.append(f"Analyzed: {', '.join(sorted(read_files)[:8])}")
            failed = {f: c for f, c in edit_failures.items() if c >= 2}
            if failed:
                parts.append(f"Issues: {', '.join(f'{f} ({c} failures)' for f, c in failed.items())}")

            brief = "\n".join(parts)

            # Save to MEMORY_DIR
            mem_dir = self.valves.MEMORY_DIR
            os.makedirs(mem_dir, exist_ok=True)
            mem_path = os.path.join(mem_dir, f"session-{ts_key}.md")
            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(f"# Session {ts_key}\n_Saved: {datetime.now(timezone.utc).isoformat()}_\n\n{brief}")
            self._log("debug", "Auto-saved session memory", key=f"session-{ts_key}")

            # Refresh PROJECT.md file listing if files were created
            if any(a.startswith("write_file") for a in actions_taken):
                project_md = os.path.join(self.valves.WORKSPACE_PATH, ".agent", "PROJECT.md")
                if os.path.isfile(project_md):
                    self._generate_project_md(project_md)
                    self._log("debug", "Refreshed PROJECT.md after file creation")

            # Prune old session memories (keep last 20)
            if os.path.isdir(mem_dir):
                session_files = sorted(
                    [f for f in os.listdir(mem_dir) if f.startswith("session-") and f.endswith(".md")],
                    reverse=True
                )
                for old_f in session_files[20:]:
                    try:
                        os.remove(os.path.join(mem_dir, old_f))
                    except OSError:
                        pass

        except Exception as e:
            self._log("warning", "Auto-save session failed", error=str(e))

    def _ensure_agent_dir(self):
        """Initialize .agent/ directory with project analysis on first run."""
        agent_dir = os.path.join(self.valves.WORKSPACE_PATH, ".agent")
        skills_dir = os.path.join(agent_dir, "skills")
        project_md = os.path.join(agent_dir, "PROJECT.md")
        was_new = not os.path.isdir(agent_dir)

        if was_new:
            try:
                os.makedirs(skills_dir, exist_ok=True)
                with open(os.path.join(agent_dir, "context.md"), "w", encoding="utf-8") as f:
                    f.write("# Session History\n\n_No sessions yet._\n")
                # Seed default clean-code skill
                with open(os.path.join(skills_dir, "clean-code.md"), "w", encoding="utf-8") as f:
                    f.write(_DEFAULT_CLEAN_CODE_SKILL)
                self._log("info", "Created .agent/ directory with default skills", path=agent_dir)
            except (OSError, IOError) as e:
                self._log("warning", "Failed to create .agent/", error=str(e))

        # Generate or refresh PROJECT.md by scanning the workspace
        if not os.path.isfile(project_md) or was_new:
            self._generate_project_md(project_md)

    def _generate_project_md(self, project_md_path: str):
        """Scan workspace and generate .agent/PROJECT.md with file listing and conventions."""
        ws = self.valves.WORKSPACE_PATH
        if not os.path.isdir(ws):
            return
        try:
            # Collect workspace structure
            all_files: list[str] = []
            extensions: dict[str, int] = {}
            for root, dirs, files in os.walk(ws):
                dirs[:] = [d for d in dirs
                           if d not in ('.agent', '__pycache__', 'node_modules', '.git',
                                        'venv', '.venv', 'dist', 'build')]
                for f in files:
                    rel = os.path.relpath(os.path.join(root, f), ws)
                    all_files.append(rel)
                    ext = os.path.splitext(f)[1].lower()
                    if ext:
                        extensions[ext] = extensions.get(ext, 0) + 1

            # Detect project type from file patterns
            project_type = "Unknown"
            has_files = {os.path.basename(f).lower() for f in all_files}
            if "package.json" in has_files:
                project_type = "JavaScript/Node.js"
            elif "requirements.txt" in has_files or "pyproject.toml" in has_files or "setup.py" in has_files:
                project_type = "Python"
            elif "cargo.toml" in has_files:
                project_type = "Rust"
            elif "go.mod" in has_files:
                project_type = "Go"
            elif "pom.xml" in has_files or "build.gradle" in has_files:
                project_type = "Java"
            elif any(f.endswith(".html") for f in all_files):
                project_type = "Web (HTML/CSS/JS)"

            # Detect common frameworks
            frameworks: list[str] = []
            for f in all_files:
                bn = os.path.basename(f).lower()
                if bn == "next.config.js" or bn == "next.config.mjs":
                    frameworks.append("Next.js")
                elif bn == "vite.config.js" or bn == "vite.config.ts":
                    frameworks.append("Vite")
                elif bn == "tailwind.config.js":
                    frameworks.append("Tailwind CSS")
                elif bn == "tsconfig.json":
                    frameworks.append("TypeScript")
                elif bn == "docker-compose.yml":
                    frameworks.append("Docker")
                elif bn == ".eslintrc.js" or bn == "eslint.config.js":
                    frameworks.append("ESLint")

            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            lines = [
                f"# Project Overview\n",
                f"\n_Auto-generated: {ts} | Update this file when architecture changes._\n",
                f"\n## Type\n\n{project_type}\n",
            ]
            if frameworks:
                lines.append(f"\n## Frameworks\n\n{', '.join(sorted(set(frameworks)))}\n")

            # Top extensions
            top_ext = sorted(extensions.items(), key=lambda x: -x[1])[:8]
            if top_ext:
                lines.append("\n## File Types\n")
                for ext, count in top_ext:
                    lines.append(f"- `{ext}`: {count} files\n")

            # File tree (max 40 files)
            lines.append("\n## Files\n")
            for f in sorted(all_files)[:40]:
                lines.append(f"- {f}\n")
            if len(all_files) > 40:
                lines.append(f"- ... and {len(all_files) - 40} more\n")

            lines.append("\n## Conventions\n\n_Add project conventions here as you discover them._\n")
            lines.append("\n## How to Run\n\n_Add build/run/test commands here._\n")

            with open(project_md_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            self._log("info", "Generated .agent/PROJECT.md",
                      files=len(all_files), type=project_type)
        except (OSError, IOError) as e:
            self._log("warning", "Failed to generate PROJECT.md", error=str(e))

    async def _call_llm(self, messages: list[dict], use_tools: bool = True) -> Optional[dict]:
        if httpx is None:
            self._log("error", "httpx not available")
            return None

        headers = {"Content-Type": "application/json"}
        if self.valves.API_KEY:
            headers["Authorization"] = f"Bearer {self.valves.API_KEY}"

        payload: dict[str, Any] = {
            "model": self.valves.MODEL_ID,
            "messages": messages,
            "temperature": self.valves.TEMPERATURE,
            "max_tokens": self.valves.MAX_TOKENS,
            "stream": False,
        }

        fmt = self.valves.TOOL_CALL_FORMAT.lower()
        if use_tools and fmt in ("auto", "native"):
            payload["tools"] = TOOL_DEFINITIONS

        url = f"{self.valves.API_BASE_URL}/chat/completions"
        self._log("debug", "LLM request",
                  url=url, model=self.valves.MODEL_ID,
                  msg_count=len(messages), use_tools=use_tools,
                  has_tool_defs="tools" in payload)

        try:
            async with httpx.AsyncClient(timeout=float(self.valves.REQUEST_TIMEOUT)) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                # Log response summary
                choices = data.get("choices", [])
                if choices:
                    m = choices[0].get("message", {})
                    tc = m.get("tool_calls", []) or []
                    content_len = len(m.get("content", "") or "")
                    self._log("debug", "LLM response",
                              content_chars=content_len,
                              tool_calls=len(tc),
                              finish=choices[0].get("finish_reason", "?"))
                    await self._debug_emit(f"LLM: {content_len}ch, {len(tc)} tools")
                else:
                    self._log("warning", "LLM returned empty choices", raw=str(data)[:500])
                    await self._debug_emit("LLM: empty choices!")
                return data
        except httpx.HTTPStatusError as e:
            body_text = e.response.text[:500] if e.response else "no body"
            self._log("error", "LLM HTTP error",
                      status=e.response.status_code, body=body_text)
            # If 400 with tools (model doesn't support), retry without
            if e.response.status_code == 400 and use_tools and fmt == "auto":
                self._log("info", "Retrying without tool definitions (model may not support tools)")
                payload.pop("tools", None)
                try:
                    async with httpx.AsyncClient(timeout=float(self.valves.REQUEST_TIMEOUT)) as client:
                        resp = await client.post(url, headers=headers, json=payload)
                        resp.raise_for_status()
                        return resp.json()
                except Exception as e2:
                    self._log("error", "LLM retry also failed", error=str(e2))
                    return None
            return None
        except httpx.ConnectError as e:
            self._log("error", "LLM connection failed — is the server running?",
                      url=url, error=str(e))
            return None
        except Exception as e:
            self._log("error", "LLM unexpected error", error=str(e), type=type(e).__name__)
            return None

    def _parse_xml_tool_calls(self, text: str) -> list[dict]:
        """Parse <tool_call>...</tool_call> blocks from text."""
        calls = []
        for match in re.finditer(r"<tool_call>\s*(.*?)\s*</tool_call>", text, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                name = data.get("name", "")
                args = data.get("arguments", {})
                if name:
                    call_id = f"xml_{hashlib.md5(f'{name}{time.time()}'.encode()).hexdigest()[:8]}"
                    calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)},
                    })
            except (json.JSONDecodeError, AttributeError):
                continue
        return calls

    def _strip_xml_tool_calls(self, text: str) -> str:
        """Remove <tool_call> blocks from text, keeping surrounding content."""
        return re.sub(r"<tool_call>\s*.*?\s*</tool_call>", "", text, flags=re.DOTALL).strip()

    # -- Main agent loop --

    # Compiled regex patterns for narration detection (class-level for performance)
    _NARRATION_PATTERNS = re.compile(
        r"(?:"
        # Future intent: "I will/I'll/I am going to [verb]"
        r"i(?:'ll|\s+will)\s+(?:now\s+)?(?:update|modify|fix|create|write|add|overwrite|rewrite|implement|change|restructure|make|replace|refactor|remove|delete|edit|save|apply)"
        r"|"
        # Present narration: "I am/I'm [verb]ing"
        r"i(?:'m|\s+am)\s+(?:now\s+)?(?:implementing|overwriting|updating|fixing|creating|writing|adding|rewriting|making|replacing|restructuring|editing|saving|applying|modifying|refactoring)"
        r"|"
        # Past-tense false claims: "I have/I've [verb]ed" (model claims it did something)
        r"i(?:'ve|\s+have)\s+(?:now\s+)?(?:implemented|overwritten|updated|fixed|created|written|added|rewritten|made|replaced|restructured|edited|saved|applied|modified|changed|refactored|removed|deleted)"
        r"|"
        # Planning narration
        r"(?:let me|here are the changes|changes i made|implementing the following|i(?:'m|\s+am) going to)"
        r")",
        re.IGNORECASE,
    )

    def _is_narrating_action(self, content: str) -> bool:
        """
        Detect when the model describes actions in prose instead of calling tools.
        Catches future intent, present narration, and past-tense false claims.
        Also detects large code blocks presented in text (should use write_file).
        """
        if not content or len(content) < 80:
            return False

        # Check for narration phrases via regex
        if self._NARRATION_PATTERNS.search(content):
            return True

        # Detect code blocks in prose — model is presenting code instead of writing it
        code_blocks = re.findall(r"```[\w]*\n.{100,}?```", content, re.DOTALL)
        if code_blocks and len(code_blocks) >= 1:
            # Large code block without tool calls = presenting code in text
            total_code_chars = sum(len(b) for b in code_blocks)
            if total_code_chars > 200:
                return True

        return False

    def _needs_verification(self, conversation: list[dict]) -> bool:
        """
        Check if the agent made file modifications during this loop but
        hasn't verified them (no subsequent read_file or successful command).
        Returns True if a verification nudge is warranted.
        """
        last_modify_idx = -1
        verified_after = False

        for i, msg in enumerate(conversation):
            role = msg.get("role", "")
            content = msg.get("content", "") or ""

            # Detect tool results indicating file modifications
            if role in ("tool", "user"):
                cl = content.lower()
                if any(kw in cl for kw in ["edited:", "created:", "updated:"]):
                    last_modify_idx = i
                    verified_after = False  # Need fresh verification
                # Detect verification evidence after a modification
                elif last_modify_idx >= 0 and i > last_modify_idx:
                    if any(sig in content for sig in [
                        "lines, showing",   # read_file output
                        "[exit code: 0]",   # successful run_command
                        "Syntax OK",        # explicit syntax check
                        "passed",           # pytest passed
                        "OK",               # generic success
                    ]):
                        verified_after = True

        return last_modify_idx >= 0 and not verified_after

    def _build_conversation(self, messages: list[dict]) -> list[dict]:
        """
        Build the conversation to send to the LLM.

        Key insight: OWUI sends the FULL chat history on every request, including
        previous assistant responses that were our final answers from prior agent
        loops. If we replay all of them, the model sees completed work and thinks
        there's nothing to do.

        Strategy: Keep the system prompt + a summary of earlier turns + the last
        user message, so the model has context but doesn't see stale tool results.
        """
        system = {"role": "system", "content": self._get_system_prompt()}

        # Separate user and assistant messages
        user_msgs = [m for m in messages if m.get("role") == "user"]
        asst_msgs = [m for m in messages if m.get("role") == "assistant"]

        self._log("debug", "Building conversation",
                  total_msgs=len(messages),
                  user_msgs=len(user_msgs),
                  asst_msgs=len(asst_msgs))

        if not user_msgs:
            return [system]

        conversation = [system]

        # For multi-turn: include prior user/assistant pairs as brief context
        # but only the text content (no tool call artifacts)
        if len(user_msgs) > 1:
            for m in messages[:-1]:  # Everything except the last message
                role = m.get("role", "")
                content = m.get("content", "")
                if role in ("user", "assistant") and content:
                    # Truncate long prior assistant answers to keep context lean
                    if role == "assistant" and len(content) > 500:
                        content = content[:500] + "\n... (truncated prior response)"
                    conversation.append({"role": role, "content": content})

        # Always include the full last user message
        last = messages[-1]
        conversation.append({"role": "user", "content": last.get("content", "")})

        self._log("debug", "Conversation built",
                  turns=len(conversation),
                  system_chars=len(system["content"]),
                  last_user_chars=len(last.get("content", "")))

        return conversation

    async def pipe(
        self,
        body: dict,
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        self._emitter = __event_emitter__
        self._log("info", "=" * 60)
        self._log("info", "PIPE INVOKED",
                  user=(__user__ or {}).get("name", "unknown"),
                  model_id=self.valves.MODEL_ID,
                  api_url=self.valves.API_BASE_URL,
                  tool_format=self.valves.TOOL_CALL_FORMAT,
                  security=self.valves.SECURITY_MODE,
                  workspace=self.valves.WORKSPACE_PATH)
        await self._debug_emit(
            f"Pipe active | model={self.valves.MODEL_ID} "
            f"fmt={self.valves.TOOL_CALL_FORMAT} url={self.valves.API_BASE_URL}"
        )

        # Ensure .agent/ directory exists with defaults
        self._ensure_agent_dir()

        # Validate configuration
        if not self.valves.MODEL_ID:
            self._log("error", "MODEL_ID not configured")
            return (
                "**Code Agent Error**: `MODEL_ID` is not configured.\n\n"
                "Go to **Admin Panel → Functions → Code Agent → Valves** and set "
                "`MODEL_ID` to your reasoning model (e.g. `qwen2.5-coder:32b`)."
            )
        if httpx is None:
            self._log("error", "httpx not installed")
            return "**Code Agent Error**: `httpx` library not available in this environment."

        engine = self._get_engine()
        messages = body.get("messages", [])

        self._log("debug", "Incoming messages from OWUI",
                  count=len(messages),
                  roles=[m.get("role") for m in messages],
                  last_content_preview=(messages[-1].get("content", "")[:200] if messages else "(empty)"))

        # Build clean conversation (avoids replaying stale tool history)
        conversation = self._build_conversation(messages)

        # Show startup context — what was loaded
        agent_base = os.path.join(self.valves.WORKSPACE_PATH, ".agent")
        startup_items: list[str] = []
        if os.path.isfile(os.path.join(agent_base, "PROJECT.md")):
            startup_items.append("project")
        skills_dir = os.path.join(agent_base, "skills")
        if os.path.isdir(skills_dir):
            skills = [f[:-3] for f in os.listdir(skills_dir)
                      if f.endswith(".md") and f != "README.md"]
            if skills:
                startup_items.append(f"skills: {', '.join(skills)}")
        mem_dir = self.valves.MEMORY_DIR
        if os.path.isdir(mem_dir):
            mem_count = sum(1 for f in os.listdir(mem_dir) if f.endswith(".md"))
            if mem_count:
                startup_items.append(f"{mem_count} memories")
        if startup_items:
            await self._emit(__event_emitter__,
                             f"\U0001f4da Loaded: {' | '.join(startup_items)}")
        # Show sandbox status
        if self.valves.SANDBOX_URL:
            mode = "full (commands + files)" if self.valves.SANDBOX_ENABLED else "commands only"
            await self._emit(__event_emitter__,
                             f"\U0001f512 Sandbox: {self.valves.SANDBOX_URL} ({mode})")

        last_content = ""
        nudge_count = 0
        MAX_NUDGES = 5
        tools_used_this_session = False  # Track if model ever successfully used tools
        edit_failures: dict[str, int] = {}  # file_path -> consecutive failure count
        consecutive_empty_length = 0  # Track finish=length with 0 content
        actions_taken: list[str] = []  # Brief log of tool actions for summary
        last_tool_call_sig: str = ""  # For duplicate call detection
        dup_call_count: int = 0
        user_request = messages[-1].get("content", "")[:200] if messages else ""
        last_action_ctx: str = ""  # Brief "what just happened" for thinking status
        self._doc_nudge_done = False  # Track post-completion documentation nudge

        try:
          for iteration in range(self.valves.MAX_ITERATIONS):
            self._log("info", f"--- Iteration {iteration + 1}/{self.valves.MAX_ITERATIONS} ---")
            step = iteration + 1
            # Show progress with context from last action
            edits_done = sum(1 for a in actions_taken if "OK" in a or a.startswith("write_file"))
            if iteration == 0:
                thinking_msg = "\u23f3 Analyzing request..."
            elif last_action_ctx:
                suffix = f" [{edits_done} edits]" if edits_done else ""
                thinking_msg = f"\u23f3 Step {step} \u00b7 {last_action_ctx}{suffix}"
            else:
                thinking_msg = f"\u23f3 Step {step} \u00b7 Thinking..."
            await self._emit(__event_emitter__, thinking_msg)

            # Call LLM
            response = await self._call_llm(conversation)
            if response is None:
                self._log("error", "LLM returned None — call failed")
                await self._emit(__event_emitter__, "LLM call failed", done=True)
                self._save_agent_context(actions_taken, edit_failures, user_request)
                if last_content:
                    return last_content + "\n\n*[LLM call failed, returning partial result]*"
                return (
                    "**Code Agent Error**: Failed to reach the reasoning model.\n\n"
                    f"Check that `{self.valves.API_BASE_URL}` is reachable and "
                    f"`{self.valves.MODEL_ID}` is a valid model."
                )

            # Extract assistant message
            choices = response.get("choices", [])
            if not choices:
                self._log("error", "LLM returned no choices")
                await self._emit(__event_emitter__, "Empty response", done=True)
                self._save_agent_context(actions_taken, edit_failures, user_request)
                return last_content or "Error: Empty response from model."

            msg = choices[0].get("message", {})
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", []) or []
            finish_reason = choices[0].get("finish_reason", "unknown")

            self._log("debug", "LLM message parsed",
                      has_content=bool(content),
                      content_preview=content[:200] if content else "(empty)",
                      native_tool_calls=len(tool_calls),
                      finish_reason=finish_reason)
            await self._debug_emit(
                f"Step {iteration+1}: {len(content)}ch, "
                f"{len(tool_calls)} tool_calls, finish={finish_reason}"
            )

            # Handle truncated responses (model hit max_tokens mid-thought)
            if finish_reason == "length" and not tool_calls:
                if not content:
                    # Model's thinking/reasoning consumed ALL tokens — nothing produced
                    consecutive_empty_length += 1
                    self._log("warning",
                              f"Empty length response #{consecutive_empty_length} — model thinking overflow")
                    await self._debug_emit(f"THINKING OVERFLOW #{consecutive_empty_length}")
                    if consecutive_empty_length >= 3:
                        # Model is stuck in thinking loops — force simpler approach
                        conversation.append({
                            "role": "user",
                            "content": (
                                "IMPORTANT: Your previous responses were cut off because your "
                                "reasoning used all available tokens. You MUST simplify your approach:\n"
                                "1. Do NOT try to rewrite entire files in one edit_file call\n"
                                "2. Make ONE small change at a time with edit_file (10-20 lines max)\n"
                                "3. Or use write_file to create the complete new file in one call\n"
                                "4. Call the tool NOW — do not plan or think further."
                            ),
                        })
                    else:
                        conversation.append({
                            "role": "user",
                            "content": (
                                "Your response was cut off (thinking used all tokens). "
                                "Take a SIMPLER action. Call ONE tool now — "
                                "do not do extended reasoning, just act."
                            ),
                        })
                else:
                    consecutive_empty_length = 0
                    self._log("warning", "Response truncated (max_tokens hit) — continuing")
                    conversation.append({"role": "assistant", "content": content})
                    conversation.append({
                        "role": "user",
                        "content": "Your response was cut off due to length. Continue from where you left off.",
                    })
                continue

            # Auto-detect XML tool calls if no native ones
            fmt = self.valves.TOOL_CALL_FORMAT.lower()
            if not tool_calls and content and fmt in ("auto", "xml"):
                xml_calls = self._parse_xml_tool_calls(content)
                if xml_calls:
                    self._log("debug", "Parsed XML tool calls", count=len(xml_calls))
                    tool_calls = xml_calls
                    content = self._strip_xml_tool_calls(content)

            # If no tool calls → check for narration and verification issues
            if not tool_calls:
                # Priority 1: Detect narration (model describing actions without doing them)
                # BUT skip if the agent already used tools — it's likely giving a legitimate summary
                is_narrating = (not tools_used_this_session) and self._is_narrating_action(content)
                # Priority 2: First iteration should almost always use tools
                is_first_with_no_action = (iteration == 0 and len(content) > 200)

                if nudge_count < MAX_NUDGES and (is_narrating or is_first_with_no_action):
                    nudge_count += 1
                    reason = "narrating actions" if is_narrating else "no tool use on first turn"
                    self._log("info",
                              f"Agent {reason} without tool calls — nudge {nudge_count}/{MAX_NUDGES}")
                    await self._debug_emit(f"NUDGE {nudge_count}/{MAX_NUDGES}: {reason}")
                    await self._emit(
                        __event_emitter__,
                        f"\u26a0\ufe0f Agent is describing instead of acting \u2014 redirecting...",
                    )
                    if content:
                        conversation.append({"role": "assistant", "content": content})
                    conversation.append({
                        "role": "user",
                        "content": (
                            "STOP. You wrote a description of what you intend to do, but you "
                            "did not actually call any tools. Do NOT describe actions — perform "
                            "them. Your response must contain actual tool calls.\n\n"
                            "Start by investigating the current state:\n"
                            "1. Use find_files to locate relevant files\n"
                            "2. Use read_file to read them\n"
                            "3. Use think() to plan your approach\n"
                            "4. Use edit_file or write_file to make changes\n"
                            "5. Use read_file and run_command to verify\n\n"
                            "Do this now. Call tools — do not write prose about your plan."
                        ),
                    })
                    continue

                # Priority 3: Agent made file changes but didn't verify
                if nudge_count < MAX_NUDGES and self._needs_verification(conversation):
                    nudge_count += 1
                    self._log("info",
                              f"Agent stopping without verification — nudge {nudge_count}/{MAX_NUDGES}")
                    await self._emit(
                        __event_emitter__,
                        f"\U0001f50d Verifying changes before finishing...",
                    )
                    if content:
                        conversation.append({"role": "assistant", "content": content})
                    conversation.append({
                        "role": "user",
                        "content": (
                            "STOP. You modified files but have not verified the changes. "
                            "Before giving your final answer you MUST:\n"
                            "1. read_file on every file you changed to confirm the edit is correct\n"
                            "2. run_command to do a syntax check or run relevant tests\n"
                            "Do this now."
                        ),
                    })
                    continue

                self._log("info", "No tool calls — returning final answer",
                          content_chars=len(content))
                await self._debug_emit(
                    f"Final answer: {len(content)}ch, "
                    f"{nudge_count} nudges, tools_used={tools_used_this_session}"
                )

                # Post-completion documentation pass: nudge model to save a memory
                # if it used tools but never called save_memory during the session
                did_doc = any("save_memory" in a or ".agent/" in a for a in actions_taken)
                if tools_used_this_session and not did_doc and not getattr(self, '_doc_nudge_done', False):
                    self._doc_nudge_done = True
                    edits = [a for a in actions_taken if "OK" in a or a.startswith("write_file")]
                    if edits:
                        new_files = [a for a in actions_taken if a.startswith("write_file")]
                        await self._emit(__event_emitter__, "\U0001f4dd Documenting session...")
                        if content:
                            conversation.append({"role": "assistant", "content": content})

                        doc_instruction = (
                            "Good — your coding work is done. Now document what you did:\n\n"
                            "1. Call save_memory with a 2-3 sentence summary:\n"
                            '   save_memory("summary", "Fixed settings menu bug in game.js by...'
                            '")\n'
                        )
                        if new_files:
                            doc_instruction += (
                                "2. You created new files — update .agent/PROJECT.md to reflect "
                                "the current file structure and any new conventions.\n"
                            )
                        doc_instruction += (
                            "\n3. If you discovered a reusable pattern, create a skill:\n"
                            '   write_file(".agent/skills/<name>.md", "# Pattern\\n...")\n\n'
                            "Keep it brief. Call save_memory now, then give your final answer."
                        )
                        conversation.append({"role": "user", "content": doc_instruction})
                        continue

                await self._emit(__event_emitter__, "\u2705 Complete", done=True)
                self._save_agent_context(actions_taken, edit_failures, user_request)
                # Programmatic auto-memory: save session summary + update PROJECT.md
                if actions_taken and tools_used_this_session:
                    self._auto_save_session(engine, actions_taken, edit_failures, user_request)
                return content if content else last_content or "Done (no response content)."

            # Show model's intent when it produces visible content alongside tool calls
            if content and tool_calls:
                intent = self._extract_intent(content)
                if intent:
                    await self._emit(__event_emitter__, f"\U0001f4ad {intent}")

            last_content = content
            tools_used_this_session = True

            # Append assistant message to conversation
            if msg.get("tool_calls"):
                conversation.append(msg)
            else:
                conversation.append({"role": "assistant", "content": content})

            # Execute each tool call
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_args_raw = func.get("arguments", "{}")
                tool_id = tc.get("id", "")

                if isinstance(tool_args_raw, str):
                    try:
                        tool_args = json.loads(tool_args_raw)
                    except json.JSONDecodeError:
                        self._log("warning", "Failed to parse tool args JSON",
                                  tool=tool_name, raw=tool_args_raw[:200])
                        tool_args = {}
                else:
                    tool_args = tool_args_raw

                self._log("info", f"TOOL CALL: {tool_name}", args=tool_args)
                action_desc = self._describe_action(tool_name, tool_args)
                # Pick icon based on tool type
                _tool_icons = {
                    "read_file": "\U0001f4c4", "write_file": "\u270d\ufe0f",
                    "edit_file": "\u270d\ufe0f", "run_command": "\u25b6",
                    "find_files": "\U0001f50d", "grep_search": "\U0001f50d",
                    "list_directory": "\U0001f4c2", "think": "\U0001f9e0",
                    "manage_todo": "\u2611\ufe0f",
                }
                icon = _tool_icons.get(tool_name, "\u2699\ufe0f")
                await self._emit(__event_emitter__, f"{icon} {action_desc}")

                # Duplicate call detection — prevent wasting iterations
                call_sig = json.dumps({"t": tool_name, "a": tool_args}, sort_keys=True)
                if call_sig == last_tool_call_sig:
                    dup_call_count += 1
                    if dup_call_count >= 2:
                        dup_guidance = (
                            f"Error: You have called {tool_name} with identical arguments "
                            f"{dup_call_count + 1} times in a row. STOP repeating this call. "
                            "Move on to your next action — edit_file, write_file, or run_command."
                        )
                        self._log("warning", "Duplicate tool call detected",
                                  tool=tool_name, count=dup_call_count + 1)
                        await self._emit(
                            __event_emitter__,
                            f"\u26a0\ufe0f Duplicate {tool_name} blocked \u2014 moving on",
                        )
                        if msg.get("tool_calls"):
                            conversation.append(msg)
                            conversation.append({
                                "role": "tool",
                                "tool_call_id": tool_id,
                                "content": dup_guidance,
                            })
                        else:
                            conversation.append({"role": "assistant", "content": content or ""})
                            conversation.append({
                                "role": "user",
                                "content": f"<tool_result name=\"{tool_name}\">\n{dup_guidance}\n</tool_result>",
                            })
                        continue
                else:
                    dup_call_count = 0
                last_tool_call_sig = call_sig

                # Execute
                t0 = time.time()
                result = await engine.execute(tool_name, tool_args)
                elapsed = time.time() - t0

                result_desc = self._describe_result(tool_name, result)
                self._log("info", f"TOOL RESULT: {tool_name}",
                          elapsed_s=round(elapsed, 2),
                          result_chars=len(result),
                          result_preview=result[:300])
                await self._debug_emit(f"Tool {tool_name}: {len(result)}ch in {elapsed:.1f}s")
                await self._emit(__event_emitter__, f"  {result_desc}")
                # Build concise context for next "Thinking..." status
                # e.g. "Read game.js", "Edited game.js ✓", "Searched isGameRunning"
                fp = tool_args.get("file_path", tool_args.get("path", ""))
                short_fp = os.path.basename(fp) if fp else ""
                if tool_name == "read_file":
                    last_action_ctx = f"Read {short_fp}" if short_fp else "Read file"
                elif tool_name in ("edit_file", "write_file"):
                    ok = "\u2713" if not result.startswith("Error:") else "\u2717"
                    last_action_ctx = f"{'Edited' if tool_name == 'edit_file' else 'Wrote'} {short_fp} {ok}"
                elif tool_name == "run_command":
                    cmd_short = tool_args.get("command", "")[:30]
                    ok = "\u2713" if "[exit code: 0]" in result else "\u2717"
                    last_action_ctx = f"Ran {cmd_short} {ok}"
                elif tool_name == "grep_search":
                    last_action_ctx = f"Searched {tool_args.get('pattern', '')[:25]}"
                elif tool_name == "find_files":
                    last_action_ctx = f"Found files"
                elif tool_name == "think":
                    last_action_ctx = "Planning next step"
                else:
                    last_action_ctx = f"{tool_name} done"

                # Track edit failures for stuck-loop detection
                if tool_name == "edit_file":
                    fp = tool_args.get("file_path", "")
                    if result.startswith("Error:"):
                        edit_failures[fp] = edit_failures.get(fp, 0) + 1
                        actions_taken.append(f"edit_file({fp}) FAILED")
                        if edit_failures[fp] >= 2:
                            result += (
                                "\n\n⚠️ edit_file has FAILED "
                                f"{edit_failures[fp]} times on {fp}. "
                                "STOP using edit_file for this file. Instead, use "
                                "write_file to rewrite the COMPLETE file with all changes. "
                                "Read the current file with read_file first, then call "
                                "write_file with the full updated content."
                            )
                    else:
                        edit_failures.pop(fp, None)  # Reset on success
                        actions_taken.append(f"edit_file({fp}) OK")
                elif tool_name == "write_file":
                    actions_taken.append(f"write_file({tool_args.get('file_path', '')})")
                elif tool_name == "run_command":
                    cmd_preview = tool_args.get("command", "")[:40]
                    actions_taken.append(f"run_command({cmd_preview})")
                elif tool_name == "read_file":
                    actions_taken.append(f"read_file({tool_args.get('file_path', '')})")
                elif tool_name == "grep_search":
                    actions_taken.append(f"grep_search({tool_args.get('pattern', '')[:30]})")
                elif tool_name not in ("think", "find_files", "list_directory"):
                    actions_taken.append(f"{tool_name}()")

                # Add result to conversation
                if msg.get("tool_calls"):
                    conversation.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result,
                    })
                else:
                    conversation.append({
                        "role": "user",
                        "content": f"<tool_result name=\"{tool_name}\">\n{result}\n</tool_result>",
                    })

            # After all tool results: inject a brief reminder to keep using tools.
            # This prevents the model from "forgetting" it can call tools after
            # seeing results and switching to narrative mode.
            tool_reminder = (
                "Tool results above. Continue using tools for your next action. "
                "Do NOT describe what you will do — call the tool directly. "
                "If you need to modify a file, call edit_file or write_file now. "
                "If you need to verify, call read_file or run_command now."
            )
            if msg.get("tool_calls"):
                # For native format, use a system message (some providers support it mid-conversation)
                conversation.append({"role": "system", "content": tool_reminder})
            else:
                # For XML format, use user message
                conversation.append({"role": "user", "content": tool_reminder})

          # Max iterations — include action summary for context on "continue"
          self._log("warning", "Max iterations reached", max=self.valves.MAX_ITERATIONS)
          await self._emit(__event_emitter__, "Max iterations reached", done=True)
          self._save_agent_context(actions_taken, edit_failures, user_request)
          summary_parts = []
          if actions_taken:
              summary_parts.append("**Actions taken:** " + "; ".join(actions_taken[-10:]))
          failed = {f: c for f, c in edit_failures.items() if c >= 2}
          if failed:
              summary_parts.append(
                  "**Stuck on:** " + ", ".join(f"`{f}` ({c} failures)" for f, c in failed.items())
                  + " — try asking me to rewrite these files from scratch."
              )
          summary = "\n\n".join(summary_parts) if summary_parts else ""
          return (
              (last_content or "")
              + "\n\n*[Reached maximum iterations. Continue the conversation for more steps.]*"
              + (f"\n\n{summary}" if summary else "")
          )

        except asyncio.CancelledError:
            self._log("info", "Agent interrupted by user",
                      iteration=iteration + 1, actions=len(actions_taken))
            await self._emit(__event_emitter__, "Interrupted", done=True)
            self._save_agent_context(actions_taken, edit_failures, user_request)
            summary = ""
            if actions_taken:
                summary = "\n\n**Progress before interruption:** " + "; ".join(actions_taken[-10:])
            return (
                (last_content or "")
                + "\n\n*[Interrupted. You can continue or redirect with a new message.]*"
                + summary
            )
