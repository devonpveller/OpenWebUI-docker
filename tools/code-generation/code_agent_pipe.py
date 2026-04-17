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
import logging
import shlex
import subprocess
import fnmatch
import hashlib
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

# Module-level logger — writes to OWUI container logs (docker compose logs openwebui)
log = logging.getLogger("code_agent")
log.setLevel(logging.DEBUG)
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter(
        "%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    ))
    log.addHandler(_h)


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
- Run syntax/import check: run_command with e.g. \
  `python -c "import ast; ast.parse(open('file.py').read()); print('OK')"`
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
| read_file | Before editing; after editing to verify | Use line ranges for >200 lines |
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
- Writing paragraphs about what you will do instead of calling tools → WRONG
- Saying "I am overwriting file.js" in text instead of calling write_file → WRONG
- Describing a fix in prose without using edit_file to actually apply it → WRONG
- Responding with a plan or analysis without any tool calls on your first turn → WRONG

## Code Quality

- Follow existing patterns and conventions in the codebase
- Don't add type annotations or docstrings to code you didn't write
- Handle errors only at system boundaries
- Use idiomatic patterns for the language
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
            "description": "Read file contents. Use start_line/end_line for large files. Returns numbered lines.",
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
            "description": "Replace exact text in a file. old_text must match once. Include 2-3 context lines.",
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

    # -- tool implementations --

    async def execute(self, name: str, args: dict) -> str:
        fn = getattr(self, f"_t_{name}", None)
        if fn is None:
            log.warning(f"Unknown tool requested: {name}")
            return f"Unknown tool: {name}"
        try:
            result = await fn(**args)
            return result
        except TypeError as e:
            log.error(f"Tool '{name}' argument error: {e}", exc_info=True)
            return f"Tool argument error: {e}"
        except PermissionError as e:
            log.warning(f"Tool '{name}' permission denied: {e}")
            return f"Error: {e}"
        except Exception as e:
            log.error(f"Tool '{name}' unexpected error: {e}", exc_info=True)
            return f"Tool error ({name}): {e}"

    async def _t_read_file(self, file_path: str, start_line: int = 0, end_line: int = 0) -> str:
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
        resolved = self._resolve(file_path)
        if self._blocked(resolved):
            return f"Error: Blocked: {file_path}"
        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        existed = os.path.isfile(resolved)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)
        lc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"{'Updated' if existed else 'Created'}: {file_path} ({lc} lines, {os.path.getsize(resolved)} bytes)"

    async def _t_edit_file(self, file_path: str, old_text: str, new_text: str) -> str:
        resolved = self._resolve(file_path)
        if self._blocked(resolved):
            return f"Error: Blocked: {file_path}"
        if not os.path.isfile(resolved):
            return f"Error: Not found: {file_path}"
        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        count = content.count(old_text)
        if count == 0:
            return f"Error: old_text not found in {file_path}."
        if count > 1:
            return f"Error: old_text matches {count} times. Add more context."
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content.replace(old_text, new_text, 1))
        return f"Edited: {file_path} ({old_text.count(chr(10))+1} → {new_text.count(chr(10))+1} lines)"

    async def _t_list_directory(self, path: str = ".") -> str:
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
            return f"[exit code: {r.returncode}]\n{out}"
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
            default=25,
            description="Maximum tool-call loop iterations per request.",
        )
        TEMPERATURE: float = Field(
            default=0.1,
            description="LLM temperature (lower = more deterministic).",
        )
        MAX_TOKENS: int = Field(
            default=16384,
            description="Max tokens per LLM response.",
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
            default="/app/backend/data",
            description="Root workspace for file operations.",
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

    def _get_engine(self) -> _ToolEngine:
        if self._engine is None:
            self._engine = _ToolEngine(self.valves)
        return self._engine

    def _log(self, level: str, msg: str, **kwargs):
        """Structured debug logging. Only emits when DEBUG_LOG is True."""
        if not self.valves.DEBUG_LOG:
            return
        extra = " ".join(f"{k}={v!r}" for k, v in kwargs.items()) if kwargs else ""
        full = f"{msg} {extra}".strip()
        getattr(log, level, log.info)(full)

    # -- Pipe registration --

    def pipes(self) -> list[dict]:
        return [{"id": "code-agent", "name": "Code Agent"}]

    # -- Helpers --

    async def _emit(self, emitter, text: str, done: bool = False):
        if emitter and self.valves.EMIT_STATUS:
            await emitter({"type": "status", "data": {"description": text, "done": done}})

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
            return SYSTEM_PROMPT_XML
        return SYSTEM_PROMPT_NATIVE

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
                else:
                    self._log("warning", "LLM returned empty choices", raw=str(data)[:500])
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

    def _is_narrating_action(self, content: str) -> bool:
        """
        Detect when the model describes actions in prose instead of calling tools.
        Returns True if the content looks like narration of intended actions.
        """
        if not content or len(content) < 80:
            return False
        cl = content.lower()
        narration_phrases = [
            "i am implementing", "i am overwriting", "i will ",
            "i'm going to", "let me ", "i'm implementing",
            "i am making", "i am updating", "i am fixing",
            "i'll now", "i am now", "i have fixed",
            "i've added", "i've updated", "i've modified",
            "i've changed", "here are the changes",
            "implementing the following", "i will overwrite",
            "i will update", "i will modify", "i will fix",
            "i will create", "i will write", "i will add",
            "i am adding", "changes i made", "i am rewriting",
            "i'm overwriting", "i'm updating", "i'm fixing",
            "i'm rewriting", "i'm adding", "i'm creating",
        ]
        return any(phrase in cl for phrase in narration_phrases)

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
        self._log("info", "=" * 60)
        self._log("info", "PIPE INVOKED",
                  user=(__user__ or {}).get("name", "unknown"),
                  model_id=self.valves.MODEL_ID,
                  api_url=self.valves.API_BASE_URL,
                  tool_format=self.valves.TOOL_CALL_FORMAT,
                  security=self.valves.SECURITY_MODE,
                  workspace=self.valves.WORKSPACE_PATH)

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

        last_content = ""
        nudge_count = 0
        MAX_NUDGES = 3

        for iteration in range(self.valves.MAX_ITERATIONS):
            self._log("info", f"--- Iteration {iteration + 1}/{self.valves.MAX_ITERATIONS} ---")
            await self._emit(
                __event_emitter__,
                f"Thinking... (step {iteration + 1}/{self.valves.MAX_ITERATIONS})",
            )

            # Call LLM
            response = await self._call_llm(conversation)
            if response is None:
                self._log("error", "LLM returned None — call failed")
                await self._emit(__event_emitter__, "LLM call failed", done=True)
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

            # Handle truncated responses (model hit max_tokens mid-thought)
            if finish_reason == "length" and not tool_calls:
                self._log("warning", "Response truncated (max_tokens hit) — continuing")
                if content:
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
                is_narrating = self._is_narrating_action(content)
                # Priority 2: First iteration should almost always use tools
                is_first_with_no_action = (iteration == 0 and len(content) > 200)

                if nudge_count < MAX_NUDGES and (is_narrating or is_first_with_no_action):
                    nudge_count += 1
                    reason = "narrating actions" if is_narrating else "no tool use on first turn"
                    self._log("info",
                              f"Agent {reason} without tool calls — nudge {nudge_count}/{MAX_NUDGES}")
                    await self._emit(
                        __event_emitter__,
                        f"Redirecting to use tools... (nudge {nudge_count})",
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
                        f"Verifying changes... (nudge {nudge_count})",
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
                await self._emit(__event_emitter__, "Done", done=True)
                return content if content else last_content or "Done (no response content)."

            last_content = content

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
                await self._emit(
                    __event_emitter__,
                    f"→ {tool_name}({', '.join(f'{k}={repr(v)[:50]}' for k, v in tool_args.items())})",
                )

                # Execute
                t0 = time.time()
                result = await engine.execute(tool_name, tool_args)
                elapsed = time.time() - t0

                self._log("info", f"TOOL RESULT: {tool_name}",
                          elapsed_s=round(elapsed, 2),
                          result_chars=len(result),
                          result_preview=result[:300])

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

        # Max iterations
        self._log("warning", "Max iterations reached", max=self.valves.MAX_ITERATIONS)
        await self._emit(__event_emitter__, "Max iterations reached", done=True)
        return (
            (last_content or "")
            + "\n\n*[Reached maximum iterations. Continue the conversation for more steps.]*"
        )
