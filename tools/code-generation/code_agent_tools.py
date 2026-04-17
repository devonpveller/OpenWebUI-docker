"""
title: Code Agent Tools
description: Claude Code-style development tools for AI coding agents. Provides file read/write/edit, search, terminal execution, chain-of-thought planning, task tracking, and persistent memory. Enable Native Function Calling in model settings. Pair with Code Agent pipe for full agentic experience.
author: AI Stack
version: 1.0.0
license: MIT
required_open_webui_version: 0.4.0

SETUP:
  Admin Panel > Settings > Models > [Select Model] > Advanced Parameters > Function Calling > "Native"
  OR per chat: Chat Controls > Advanced Params > Function Calling > "Native"
"""

import os
import re
import json
import shlex
import subprocess
import fnmatch
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class Tools:
    """
    Code Agent Tools — Claude Code-style development capabilities for Open WebUI.

    Provides file operations, code search, command execution, structured
    planning, task tracking, and persistent cross-conversation memory.
    """

    class Valves(BaseModel):
        WORKSPACE_PATH: str = Field(
            default="/app/backend/data",
            description=(
                "Root workspace directory. All file paths resolve relative to this. "
                "For container use, point to the mounted project directory."
            ),
        )
        SECURITY_MODE: str = Field(
            default="allowlist",
            description=(
                "Security mode for command execution. "
                "'allowlist': only pre-approved commands (safest). "
                "'confirm': all commands allowed but logged. "
                "'unrestricted': no restrictions (development only)."
            ),
        )
        BLOCKED_PATHS: str = Field(
            default=".env,.env.*,**/secrets/**,/etc/shadow,/etc/passwd,**/.git/config",
            description="Comma-separated glob patterns for blocked file paths.",
        )
        ALLOWED_COMMANDS: str = Field(
            default=(
                "ls,cat,head,tail,find,grep,rg,wc,diff,file,stat,du,df,pwd,"
                "echo,printf,sort,uniq,tr,cut,awk,sed,jq,"
                "git,python,python3,node,npm,npx,pip,pip3,"
                "cargo,go,make,cmake,gcc,g++,rustc,javac,java,dotnet,ruby,perl,"
                "bash,sh,curl,wget,tar,zip,unzip,gzip,gunzip"
            ),
            description="Comma-separated allowed commands (allowlist mode only).",
        )
        DANGEROUS_PATTERNS: str = Field(
            default="rm -rf /,rm -rf /*,mkfs,dd if=,:(){ :|:&,> /dev/sd,chmod 777 /,chown root",
            description="Comma-separated patterns blocked regardless of security mode.",
        )
        COMMAND_TIMEOUT: int = Field(
            default=30,
            description="Maximum seconds per command execution.",
        )
        MAX_READ_LINES: int = Field(
            default=500,
            description="Maximum lines returned per file read.",
        )
        MAX_SEARCH_RESULTS: int = Field(
            default=50,
            description="Maximum results per search operation.",
        )
        MAX_FILE_SIZE_KB: int = Field(
            default=1024,
            description="Maximum file size (KB) for read operations.",
        )
        MEMORY_DIR: str = Field(
            default="/app/backend/data/code_agent/memory",
            description="Directory for persistent cross-conversation memory.",
        )
        SHELL: str = Field(
            default="/bin/bash",
            description="Shell executable for command execution.",
        )

    class UserValves(BaseModel):
        USER_WORKSPACE: str = Field(
            default="",
            description="Per-user workspace override. Leave empty for admin default.",
        )
        USER_SECURITY_MODE: str = Field(
            default="",
            description="Per-user security mode override. Options: allowlist, confirm, unrestricted.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self._todo_list: list[dict] = []
        self._todo_counter: int = 0

    # =========================================================================
    # INTERNAL HELPERS
    # =========================================================================

    def _get_workspace(self, __user__: dict = {}) -> str:
        user_valves = __user__.get("valves", None)
        if user_valves and getattr(user_valves, "USER_WORKSPACE", ""):
            return user_valves.USER_WORKSPACE
        return self.valves.WORKSPACE_PATH

    def _get_security_mode(self, __user__: dict = {}) -> str:
        user_valves = __user__.get("valves", None)
        if user_valves:
            mode = getattr(user_valves, "USER_SECURITY_MODE", "").strip().lower()
            if mode in ("allowlist", "confirm", "unrestricted"):
                return mode
        return self.valves.SECURITY_MODE

    def _resolve_path(self, file_path: str, __user__: dict = {}) -> str:
        workspace = self._get_workspace(__user__)
        if os.path.isabs(file_path):
            resolved = os.path.realpath(file_path)
        else:
            resolved = os.path.realpath(os.path.join(workspace, file_path))
        ws_real = os.path.realpath(workspace)
        if not resolved.startswith(ws_real + os.sep) and resolved != ws_real:
            raise PermissionError(
                f"Path '{file_path}' resolves outside workspace '{workspace}'"
            )
        return resolved

    def _is_blocked(self, file_path: str) -> bool:
        patterns = [p.strip() for p in self.valves.BLOCKED_PATHS.split(",") if p.strip()]
        basename = os.path.basename(file_path)
        for pattern in patterns:
            if fnmatch.fnmatch(file_path, pattern) or fnmatch.fnmatch(basename, pattern):
                return True
        return False

    def _validate_command(self, command: str, __user__: dict = {}) -> tuple:
        dangerous = [
            p.strip().lower()
            for p in self.valves.DANGEROUS_PATTERNS.split(",")
            if p.strip()
        ]
        cmd_lower = command.lower().strip()
        for pattern in dangerous:
            if pattern in cmd_lower:
                return False, f"Blocked dangerous pattern: '{pattern}'"
        try:
            parts = shlex.split(command)
        except ValueError as e:
            return False, f"Invalid command syntax: {e}"
        if not parts:
            return False, "Empty command"
        base_cmd = os.path.basename(parts[0])
        mode = self._get_security_mode(__user__)
        if mode == "allowlist":
            allowed = [c.strip() for c in self.valves.ALLOWED_COMMANDS.split(",") if c.strip()]
            if base_cmd not in allowed:
                return False, f"Command '{base_cmd}' not in allowlist. Allowed: {', '.join(sorted(allowed))}"
        return True, "OK"

    async def _emit(self, emitter, description: str, done: bool = False):
        if emitter:
            await emitter(
                {"type": "status", "data": {"description": description, "done": done}}
            )

    # =========================================================================
    # FILE OPERATIONS
    # =========================================================================

    async def read_file(
        self,
        file_path: str,
        start_line: int = 0,
        end_line: int = 0,
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Read file contents with optional line range. Line numbers are 1-based.
        Use start_line/end_line to read specific sections of large files.
        Returns numbered lines for precise editing reference.

        :param file_path: Path to file (relative to workspace or absolute).
        :param start_line: First line to read (1-based, 0 = beginning).
        :param end_line: Last line to read (1-based, 0 = end of file).
        :return: File contents with line numbers.
        """
        try:
            await self._emit(__event_emitter__, f"Reading {file_path}...")
            resolved = self._resolve_path(file_path, __user__)

            if self._is_blocked(resolved):
                await self._emit(__event_emitter__, "Blocked", done=True)
                return f"Error: Access to '{file_path}' is blocked by security policy."

            if not os.path.isfile(resolved):
                await self._emit(__event_emitter__, "Not found", done=True)
                return f"Error: File not found: {file_path}"

            size_kb = os.path.getsize(resolved) / 1024
            if size_kb > self.valves.MAX_FILE_SIZE_KB:
                await self._emit(__event_emitter__, "Too large", done=True)
                return (
                    f"Error: File is {size_kb:.0f}KB (limit {self.valves.MAX_FILE_SIZE_KB}KB). "
                    f"Use start_line/end_line to read a section."
                )

            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total = len(lines)
            s = max(0, start_line - 1) if start_line > 0 else 0
            e = min(total, end_line) if end_line > 0 else total
            if (e - s) > self.valves.MAX_READ_LINES:
                e = s + self.valves.MAX_READ_LINES

            numbered = [f"{i:4d} | {ln.rstrip()}" for i, ln in enumerate(lines[s:e], start=s + 1)]
            info = f"[{file_path} — {total} lines, showing {s + 1}–{e}]"

            await self._emit(__event_emitter__, f"Read {e - s} lines", done=True)
            return f"{info}\n" + "\n".join(numbered)

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error reading file: {e}"

    async def write_file(
        self,
        file_path: str,
        content: str,
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Create a new file or overwrite an existing file with the given content.
        Creates parent directories automatically.
        Prefer edit_file for modifying existing files.

        :param file_path: Destination path (relative to workspace or absolute).
        :param content: Complete file content to write.
        :return: Confirmation with path and size.
        """
        try:
            await self._emit(__event_emitter__, f"Writing {file_path}...")
            resolved = self._resolve_path(file_path, __user__)

            if self._is_blocked(resolved):
                await self._emit(__event_emitter__, "Blocked", done=True)
                return f"Error: Writing to '{file_path}' is blocked by security policy."

            os.makedirs(os.path.dirname(resolved), exist_ok=True)
            existed = os.path.isfile(resolved)

            with open(resolved, "w", encoding="utf-8") as f:
                f.write(content)

            size = os.path.getsize(resolved)
            line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            action = "Updated" if existed else "Created"

            await self._emit(__event_emitter__, f"{action} {file_path}", done=True)
            return f"{action}: {file_path} ({line_count} lines, {size} bytes)"

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error writing file: {e}"

    async def edit_file(
        self,
        file_path: str,
        old_text: str,
        new_text: str,
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Replace exact text in a file. old_text must match exactly one location
        (including whitespace and indentation). Include 2–3 lines of surrounding
        context for precision. Use for surgical edits to existing files.

        :param file_path: Path to the file to edit.
        :param old_text: Exact text to find and replace (must match once).
        :param new_text: Replacement text.
        :return: Confirmation with change summary.
        """
        try:
            await self._emit(__event_emitter__, f"Editing {file_path}...")
            resolved = self._resolve_path(file_path, __user__)

            if self._is_blocked(resolved):
                await self._emit(__event_emitter__, "Blocked", done=True)
                return f"Error: Editing '{file_path}' is blocked by security policy."

            if not os.path.isfile(resolved):
                await self._emit(__event_emitter__, "Not found", done=True)
                return f"Error: File not found: {file_path}"

            with open(resolved, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            count = content.count(old_text)
            if count == 0:
                await self._emit(__event_emitter__, "Text not found", done=True)
                return (
                    f"Error: old_text not found in {file_path}. "
                    "Verify exact whitespace and content match."
                )
            if count > 1:
                await self._emit(__event_emitter__, "Ambiguous match", done=True)
                return (
                    f"Error: old_text found {count} times in {file_path}. "
                    "Include more surrounding context to match exactly once."
                )

            new_content = content.replace(old_text, new_text, 1)

            with open(resolved, "w", encoding="utf-8") as f:
                f.write(new_content)

            old_lines = old_text.count("\n") + 1
            new_lines = new_text.count("\n") + 1

            await self._emit(__event_emitter__, f"Edited {file_path}", done=True)
            return f"Edited: {file_path} (replaced {old_lines} lines with {new_lines} lines)"

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error editing file: {e}"

    async def list_directory(
        self,
        path: str = ".",
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        List contents of a directory with type indicators and sizes.
        Directories end with '/'. Hidden files included.

        :param path: Directory path (relative to workspace). Defaults to workspace root.
        :return: Directory listing.
        """
        try:
            await self._emit(__event_emitter__, f"Listing {path}...")
            resolved = self._resolve_path(path, __user__)

            if not os.path.isdir(resolved):
                await self._emit(__event_emitter__, "Not a directory", done=True)
                return f"Error: Not a directory: {path}"

            entries = sorted(os.listdir(resolved))
            result = []
            dirs = files = 0

            for entry in entries:
                full = os.path.join(resolved, entry)
                if os.path.isdir(full):
                    result.append(f"  {entry}/")
                    dirs += 1
                else:
                    size = os.path.getsize(full)
                    if size >= 1024 * 1024:
                        sz = f"{size / (1024 * 1024):.1f}M"
                    elif size >= 1024:
                        sz = f"{size / 1024:.1f}K"
                    else:
                        sz = f"{size}B"
                    result.append(f"  {entry}  ({sz})")
                    files += 1

            header = f"[{path} — {dirs} directories, {files} files]"
            await self._emit(__event_emitter__, f"{dirs + files} entries", done=True)
            return header + "\n" + "\n".join(result)

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error listing directory: {e}"

    # =========================================================================
    # SEARCH
    # =========================================================================

    async def grep_search(
        self,
        pattern: str,
        path: str = ".",
        include_pattern: str = "",
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Search for a text or regex pattern across files. Case-insensitive.
        Returns matching lines with file:line references.
        Skips binary files, node_modules, .git, __pycache__, venv.

        :param pattern: Text or regex pattern to search for.
        :param path: Directory to search (relative to workspace). Default: workspace root.
        :param include_pattern: Filter files by glob (e.g. '*.py', '*.js').
        :return: Matching lines in file:line: format.
        """
        try:
            await self._emit(__event_emitter__, f"Searching for '{pattern}'...")
            resolved = self._resolve_path(path, __user__)

            if not os.path.isdir(resolved):
                await self._emit(__event_emitter__, "Not a directory", done=True)
                return f"Error: Search path is not a directory: {path}"

            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                await self._emit(__event_emitter__, "Invalid regex", done=True)
                return f"Error: Invalid regex pattern: {e}"

            skip_dirs = {".git", "node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".tox"}
            results = []
            files_searched = 0
            limit = self.valves.MAX_SEARCH_RESULTS

            for root, dirs, files in os.walk(resolved):
                dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
                for fname in files:
                    if include_pattern and not fnmatch.fnmatch(fname, include_pattern):
                        continue
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, resolved)
                    try:
                        with open(fpath, "r", encoding="utf-8", errors="strict") as f:
                            files_searched += 1
                            for line_num, line in enumerate(f, 1):
                                if regex.search(line):
                                    results.append(f"{rel}:{line_num}: {line.rstrip()}")
                                    if len(results) >= limit:
                                        break
                    except (UnicodeDecodeError, PermissionError, OSError):
                        continue
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break

            if not results:
                await self._emit(__event_emitter__, "No matches", done=True)
                return f"No matches for '{pattern}' in {path} ({files_searched} files searched)"

            trunc = f" (truncated at {limit})" if len(results) >= limit else ""
            header = f"[{len(results)} matches{trunc} across {files_searched} files]"
            await self._emit(__event_emitter__, f"{len(results)} matches", done=True)
            return header + "\n" + "\n".join(results)

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error searching: {e}"

    async def find_files(
        self,
        pattern: str,
        path: str = ".",
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Find files by name using glob matching. Searches recursively.
        Use ** for recursive directory matching, * for wildcards.

        :param pattern: Glob pattern (e.g. '*.py', '**/test_*.js', 'README*').
        :param path: Directory to search (relative to workspace).
        :return: List of matching file paths.
        """
        try:
            await self._emit(__event_emitter__, f"Finding '{pattern}'...")
            resolved = self._resolve_path(path, __user__)

            if not os.path.isdir(resolved):
                await self._emit(__event_emitter__, "Not a directory", done=True)
                return f"Error: Not a directory: {path}"

            import glob as glob_mod

            glob_path = os.path.join(resolved, pattern) if "**" in pattern else os.path.join(resolved, "**", pattern)
            matches = []
            for match in glob_mod.glob(glob_path, recursive=True):
                rel = os.path.relpath(match, resolved)
                if any(part.startswith(".") for part in Path(rel).parts):
                    continue
                suffix = "/" if os.path.isdir(match) else ""
                matches.append(rel + suffix)
                if len(matches) >= self.valves.MAX_SEARCH_RESULTS:
                    break

            if not matches:
                await self._emit(__event_emitter__, "No files found", done=True)
                return f"No files matching '{pattern}' in {path}"

            trunc = f" (truncated at {self.valves.MAX_SEARCH_RESULTS})" if len(matches) >= self.valves.MAX_SEARCH_RESULTS else ""
            header = f"[{len(matches)} files{trunc}]"
            await self._emit(__event_emitter__, f"{len(matches)} files", done=True)
            return header + "\n" + "\n".join(f"  {m}" for m in sorted(matches))

        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error finding files: {e}"

    # =========================================================================
    # TERMINAL
    # =========================================================================

    async def run_command(
        self,
        command: str,
        working_dir: str = "",
        __user__: dict = {},
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Execute a shell command and return its output. Use for building, testing,
        linting, git operations, and other development tasks.

        :param command: Shell command to execute.
        :param working_dir: Working directory (relative to workspace). Empty = workspace root.
        :return: Command stdout/stderr and exit code.
        """
        try:
            await self._emit(__event_emitter__, f"Running: {command[:80]}...")

            allowed, reason = self._validate_command(command, __user__)
            if not allowed:
                await self._emit(__event_emitter__, "Blocked", done=True)
                return f"Error: {reason}"

            workspace = self._get_workspace(__user__)
            cwd = self._resolve_path(working_dir, __user__) if working_dir else workspace
            if not os.path.isdir(cwd):
                await self._emit(__event_emitter__, "Bad directory", done=True)
                return f"Error: Working directory not found: {working_dir}"

            shell = self.valves.SHELL if os.path.exists(self.valves.SHELL) else None

            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    executable=shell,
                    capture_output=True,
                    text=True,
                    timeout=self.valves.COMMAND_TIMEOUT,
                    cwd=cwd,
                )

                parts = []
                if result.stdout:
                    parts.append(result.stdout)
                if result.stderr:
                    parts.append(f"[stderr]\n{result.stderr}")
                output = "\n".join(parts) if parts else "(no output)"

                max_chars = 50_000
                if len(output) > max_chars:
                    output = output[:max_chars] + f"\n... (truncated, {len(output)} total chars)"

                await self._emit(
                    __event_emitter__,
                    f"Exit {result.returncode}",
                    done=True,
                )
                return f"[exit code: {result.returncode}]\n{output}"

            except subprocess.TimeoutExpired:
                await self._emit(__event_emitter__, "Timed out", done=True)
                return f"Error: Command timed out after {self.valves.COMMAND_TIMEOUT}s"

        except PermissionError as e:
            await self._emit(__event_emitter__, "Permission denied", done=True)
            return f"Error: {e}"
        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error running command: {e}"

    # =========================================================================
    # PLANNING & THINKING
    # =========================================================================

    async def think(
        self,
        thought: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Use this tool to think through complex problems step-by-step before acting.
        Record your reasoning, analysis, and planned approach here.
        Thoughts are logged but not shown directly to the user.
        Call this BEFORE taking action on complex or multi-step tasks.

        :param thought: Your detailed reasoning and analysis.
        :return: Acknowledgment (proceed with your plan).
        """
        await self._emit(__event_emitter__, "Thinking...", done=True)
        return f"Thought recorded ({len(thought)} chars). Proceed with your plan."

    async def manage_todo(
        self,
        action: str,
        task_id: int = 0,
        title: str = "",
        status: str = "not-started",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Manage a task list for tracking multi-step work.
        Use to break down complex tasks and track progress.

        :param action: 'add' (requires title), 'update' (requires task_id + status), 'list', 'clear'.
        :param task_id: Task ID for 'update' action.
        :param title: Task title for 'add' action.
        :param status: Status for 'update': 'not-started', 'in-progress', 'completed'.
        :return: Current task list.
        """
        if action == "add":
            if not title:
                return "Error: 'title' required for add action."
            self._todo_counter += 1
            self._todo_list.append(
                {"id": self._todo_counter, "title": title, "status": "not-started"}
            )
        elif action == "update":
            if status not in ("not-started", "in-progress", "completed"):
                return f"Error: Invalid status '{status}'. Use: not-started, in-progress, completed"
            found = False
            for task in self._todo_list:
                if task["id"] == task_id:
                    task["status"] = status
                    found = True
                    break
            if not found:
                return f"Error: Task {task_id} not found."
        elif action == "clear":
            self._todo_list.clear()
            self._todo_counter = 0
            return "Task list cleared."
        elif action != "list":
            return "Error: Invalid action. Use: add, update, list, clear"

        if not self._todo_list:
            return "No tasks. Use action='add' with a title to create tasks."

        icons = {"not-started": "○", "in-progress": "◐", "completed": "●"}
        lines = ["Tasks:"]
        for t in self._todo_list:
            lines.append(f"  {icons.get(t['status'], '?')} [{t['id']}] {t['title']} ({t['status']})")
        done = sum(1 for t in self._todo_list if t["status"] == "completed")
        lines.append(f"\nProgress: {done}/{len(self._todo_list)} completed")

        await self._emit(
            __event_emitter__, f"Tasks: {done}/{len(self._todo_list)}", done=True
        )
        return "\n".join(lines)

    # =========================================================================
    # MEMORY
    # =========================================================================

    async def save_memory(
        self,
        key: str,
        content: str,
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Save a note to persistent memory that survives across conversations.
        Use descriptive keys like 'project-structure', 'api-patterns', 'debug-notes'.

        :param key: Memory key (alphanumeric, hyphens, underscores).
        :param content: Content to store.
        :return: Confirmation.
        """
        try:
            await self._emit(__event_emitter__, f"Saving memory: {key}...")
            safe_key = re.sub(r"[^a-zA-Z0-9_-]", "-", key).strip("-")
            if not safe_key:
                return "Error: Invalid key. Use alphanumeric characters, hyphens, or underscores."

            mem_dir = self.valves.MEMORY_DIR
            os.makedirs(mem_dir, exist_ok=True)

            mem_path = os.path.join(mem_dir, f"{safe_key}.md")
            if not os.path.realpath(mem_path).startswith(os.path.realpath(mem_dir)):
                return "Error: Invalid memory key."

            with open(mem_path, "w", encoding="utf-8") as f:
                f.write(f"# {key}\n")
                f.write(f"_Updated: {datetime.now(timezone.utc).isoformat()}_\n\n")
                f.write(content)

            await self._emit(__event_emitter__, f"Saved: {key}", done=True)
            return f"Memory saved: '{key}' ({len(content)} chars)"

        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error saving memory: {e}"

    async def recall_memory(
        self,
        key: str = "",
        __event_emitter__: Callable[[dict], Any] = None,
    ) -> str:
        """
        Recall a saved memory by key, or list all saved memories.
        Use at the start of tasks to check for relevant context from previous sessions.

        :param key: Memory key to recall. Empty = list all memories.
        :return: Memory content or index of all memories.
        """
        try:
            mem_dir = self.valves.MEMORY_DIR
            if not os.path.isdir(mem_dir):
                return "No memories saved yet."

            if not key:
                files = sorted(f for f in os.listdir(mem_dir) if f.endswith(".md"))
                if not files:
                    return "No memories saved yet."
                lines = ["Saved memories:"]
                for f in files:
                    name = f[:-3]
                    fpath = os.path.join(mem_dir, f)
                    size = os.path.getsize(fpath)
                    mtime = datetime.fromtimestamp(
                        os.path.getmtime(fpath), tz=timezone.utc
                    )
                    lines.append(
                        f"  • {name} ({size}B, updated {mtime.strftime('%Y-%m-%d %H:%M')})"
                    )
                await self._emit(__event_emitter__, f"{len(files)} memories", done=True)
                return "\n".join(lines)

            safe_key = re.sub(r"[^a-zA-Z0-9_-]", "-", key).strip("-")
            mem_path = os.path.join(mem_dir, f"{safe_key}.md")
            if not os.path.realpath(mem_path).startswith(os.path.realpath(mem_dir)):
                return "Error: Invalid memory key."
            if not os.path.isfile(mem_path):
                return f"Memory '{key}' not found. Call recall_memory() with no key to list all."

            with open(mem_path, "r", encoding="utf-8") as f:
                content = f.read()

            await self._emit(__event_emitter__, f"Recalled: {key}", done=True)
            return content

        except Exception as e:
            await self._emit(__event_emitter__, "Error", done=True)
            return f"Error recalling memory: {e}"
