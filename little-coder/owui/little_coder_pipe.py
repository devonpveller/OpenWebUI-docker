"""
title: Little Coder
author: ai-stack
version: 0.2.0
license: MIT
description: Drive little-coder from OpenWebUI chat (Chapter 2 — OWUI pipeline).
  Plain messages trigger coding tasks and stream the agent's process live —
  thinking, tool calls, and answer. Slash-commands (/project, /confirm, …) are
  operator actions gated by the OpenWebUI user role.
required_open_webui_version: 0.5.0
"""

# little-coder — OWUI pipeline (design §12.6).
#
# This Pipe registers a "Little Coder" model in OpenWebUI. It is the
# chat-shaped surface for the control daemon:
#
#   - a plain message  → a task trigger (channel=owui, user_id = OWUI user).
#                        The agent's process streams into the chat live.
#   - a /slash-command → an operator action, allowed only for OWUI users whose
#                        role is in `operator_roles` (default: admin).
#   - OWUI "stop"      → interrupts (cancels) the running task (design §12.4
#                        abandonment — not a mid-task write).
#
# Privilege separation (design §12.6): operator commands authenticate HERE, at
# the OpenWebUI surface (the user's OWUI role) — never at the MCP server.

import asyncio
import contextlib
import json
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field

# Operator slash-commands — gated by OWUI role. `/help` and `/status` are
# open to any user (read-only) and handled before this set is checked.
_OPERATOR_CMDS = {"/project", "/confirm", "/pending", "/approve", "/reject", "/upstream"}

_HELP = """**Little Coder** — OpenWebUI surface

Send a plain message to trigger a coding task against the focused project.
The agent's thinking, commands, and answer stream into the chat as it works;
press **Stop** to interrupt a running task.

Operator commands (require an operator account):
- `/project <repo-url>` — switch the focused project
- `/confirm <task_id> <pass|fail|unverified>` — amend a task outcome
- `/pending` — list pending skill artifacts (empty until Chapter 4)
- `/approve <id>` · `/reject <id>` — artifact review (Chapter 4)
- `/upstream pull` — pull the fork-parent (Chapter 5)

Open to everyone:
- `/status` — daemon health and focused project
- `/help` — this message
"""


def _cmd_mark(a: dict) -> str:
    """One-glyph status for a command the agent ran."""
    if a.get("ok"):
        return "✓"
    if a.get("denied"):
        return "⛔ blocked"
    return f"✗ exit {a.get('exit_code')}"


class _Render:
    """Turns the agent's pi `--mode json` events into a live markdown stream.
    Stateful across polls — it tracks the current section so headers print
    once and a new reasoning turn re-opens the Thinking block."""

    def __init__(self, show_thinking: bool = True) -> None:
        self.show_thinking = show_thinking
        self.section = ""  # "" | thinking | tool | answer
        self._tools: set = set()

    def feed(self, ev: dict) -> str:
        if ev.get("type") != "message_update":
            return ""
        ame = ev.get("assistantMessageEvent") or {}
        t = ame.get("type")

        if t == "thinking_start":
            if self.show_thinking and self.section != "thinking":
                self.section = "thinking"
                return "\n\n🧠 *Thinking…*\n\n"
            return ""
        if t == "thinking_delta":
            return str(ame.get("delta", "")) if self.show_thinking else ""
        if t == "toolcall_end":
            tc = self._tool_call(ame.get("partial"))
            if tc and tc.get("id") not in self._tools:
                self._tools.add(tc.get("id"))
                self.section = "tool"
                args = tc.get("arguments") or {}
                shown = args.get("command") or json.dumps(args, ensure_ascii=False)
                return f"\n\n🔧 **`{tc.get('name', 'tool')}`** `{shown}`\n"
            return ""
        if t == "text_start":
            if self.section != "answer":
                self.section = "answer"
                return "\n\n---\n\n"
            return ""
        if t == "text_delta":
            return str(ame.get("delta", ""))
        return ""

    @staticmethod
    def _tool_call(partial) -> Optional[dict]:
        if not isinstance(partial, dict):
            return None
        for c in reversed(partial.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "toolCall":
                return c
        return None


class Pipe:
    class Valves(BaseModel):
        daemon_url: str = Field(
            default="http://little-coder:8090",
            description="little-coder control-daemon URL (reachable over llm-net).",
        )
        operator_roles: str = Field(
            default="admin",
            description="Comma-separated OWUI roles allowed to run operator "
            "slash-commands.",
        )
        poll_seconds: float = Field(
            default=1.0, description="Event-stream poll interval, seconds."
        )
        task_timeout_seconds: int = Field(
            default=2100,
            description="Give up waiting for a task after this many seconds.",
        )
        show_thinking: bool = Field(
            default=True,
            description="Stream the agent's reasoning into the chat.",
        )

    def __init__(self) -> None:
        self.valves = self.Valves()

    def pipes(self) -> list[dict]:
        return [{"id": "little-coder", "name": "Little Coder"}]

    # -- entry point -------------------------------------------------------

    async def pipe(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__=None,
        __metadata__: Optional[dict] = None,
        __task__=None,
    ):
        # OpenWebUI fires background generation calls (chat title, tags,
        # follow-ups, …) against the selected model. Those must NOT trigger a
        # real little-coder task — answer them cheaply and return.
        meta = __metadata__ or {}
        task_type = __task__ or meta.get("task")
        if task_type:
            yield self._meta_response(task_type, body)
            return

        message = self._last_user_text(body)
        if not message.strip():
            yield _HELP
            return

        if message.lstrip().startswith("/"):
            yield await self._operator(message.strip(), __user__ or {})
            return

        async for chunk in self._trigger_stream(
            message, __user__ or {}, meta, __event_emitter__
        ):
            yield chunk

    # -- task triggers — live streaming ------------------------------------

    async def _trigger_stream(self, prompt: str, user: dict, metadata: dict, emit):
        await self._status(emit, "Queueing task…")
        ok, data = await self._call(
            "POST",
            "/tasks",
            {
                "prompt": prompt,
                "channel": "owui",
                "user_id": user.get("email") or user.get("id") or "owui",
                "session_id": metadata.get("chat_id"),
            },
        )
        if not ok:
            await self._status(emit, "Failed", done=True)
            detail = str(data.get("detail", data))
            if "no project focused" in detail:
                yield (
                    "⚠️ No project is focused. An operator must run "
                    "`/project <repo-url>` first."
                )
            else:
                yield f"⚠️ Could not queue the task: {detail}"
            return

        task_id = data["task_id"]
        await self._status(emit, "Agent starting…")

        renderer = _Render(self.valves.show_thinking)
        offset = 0
        waited = 0.0
        done = False
        try:
            while waited < self.valves.task_timeout_seconds:
                await asyncio.sleep(self.valves.poll_seconds)
                waited += self.valves.poll_seconds
                ok, data = await self._call(
                    "GET", f"/tasks/{task_id}/events?offset={offset}"
                )
                if not ok:
                    await self._status(emit, "Lost contact", done=True)
                    yield f"\n\n⚠️ Lost contact with the daemon: {data.get('detail', data)}"
                    return
                offset = data.get("next_offset", offset)
                for raw in data.get("events", []):
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    chunk = renderer.feed(ev)
                    if chunk:
                        yield chunk
                if data.get("done"):
                    done = True
                    break
                await self._status(emit, "Agent working…")

            ok, final = await self._call("GET", f"/tasks/{task_id}")
            await self._status(emit, "Done", done=True)
            if not done:
                yield (
                    f"\n\n⌛ Still running after "
                    f"{self.valves.task_timeout_seconds}s — `/status` to check, "
                    f"or **Stop** to interrupt."
                )
            yield self._footer(task_id, final if ok else {})
        except asyncio.CancelledError:
            # OWUI "stop" — interrupt the task (design §12.4: abandonment).
            asyncio.ensure_future(self._cancel_quietly(task_id))
            await self._status(emit, "Interrupted", done=True)
            raise

    async def _cancel_quietly(self, task_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._call("POST", f"/tasks/{task_id}/cancel")

    @staticmethod
    def _footer(task_id: str, state: dict) -> str:
        """Closing block: the command log + a one-line outcome."""
        outcome = state.get("outcome")
        status = state.get("status", "?")
        activity = state.get("activity") or []
        icon = {"pass": "✅", "fail": "❌", "unverified": "🔶"}.get(outcome, "▫️")

        parts = ["\n\n---\n"]
        if activity:
            rows = "\n".join(
                f"- `{a.get('command', '')}` — {_cmd_mark(a)}" for a in activity
            )
            parts.append(
                f"<details>\n<summary>🔧 {len(activity)} command(s) run "
                f"in open-terminal</summary>\n\n{rows}\n\n</details>\n"
            )
        foot = f"{icon} `{status}` · outcome `{outcome}` · task `{task_id}`"
        if outcome == "unverified":
            foot += f"\n*Confirm the real outcome:* `/confirm {task_id} pass|fail`"
        parts.append(foot)
        return "\n".join(parts)

    # -- operator commands -------------------------------------------------

    async def _operator(self, message: str, user: dict) -> str:
        parts = message.split()
        cmd = parts[0].lower()
        args = parts[1:]

        if cmd == "/help":
            return _HELP
        if cmd == "/status":
            ok, data = await self._call("GET", "/health")
            return (
                f"```json\n{json.dumps(data, indent=2)}\n```"
                if ok
                else f"⚠️ daemon unreachable: {data.get('detail', data)}"
            )

        if cmd not in _OPERATOR_CMDS:
            return f"Unknown command `{cmd}`. Try `/help`."

        # Privilege separation (design §12.6): operator commands are gated by
        # the OpenWebUI user role — a regular user cannot escalate.
        roles = {r.strip() for r in self.valves.operator_roles.split(",")}
        if (user.get("role") or "") not in roles:
            return (
                f"⛔ `{cmd}` is an operator command. Your account "
                f"(`{user.get('role') or 'unknown'}`) is not an operator."
            )

        actor = user.get("email") or user.get("id") or "owui"

        if cmd == "/project":
            link = args[-1] if args else ""
            if link.endswith(":"):  # accepts `/project repo: <url>`
                link = ""
            if not link:
                return "Usage: `/project <repo-url>`"
            ok, data = await self._call(
                "POST", "/project", {"repo": link, "actor": actor}
            )
            return (
                f"✅ {data.get('action')}: focus = `{data.get('focus')}`"
                if ok
                else f"⚠️ {data.get('detail', data)}"
            )

        if cmd == "/confirm":
            if len(args) < 2 or args[1] not in ("pass", "fail", "unverified"):
                return "Usage: `/confirm <task_id> <pass|fail|unverified>`"
            ok, data = await self._call(
                "POST",
                f"/tasks/{args[0]}/confirm",
                {"outcome": args[1], "actor": actor},
            )
            return (
                f"✅ {data.get('detail', 'outcome amended')}"
                if ok
                else f"⚠️ {data.get('detail', data)}"
            )

        if cmd == "/pending":
            ok, data = await self._call("GET", "/admin/pending")
            if not ok:
                return f"⚠️ {data.get('detail', data)}"
            return f"{len(data.get('pending', []))} pending artifact(s)."

        if cmd in ("/approve", "/reject"):
            if not args:
                return f"Usage: `{cmd} <artifact_id>`"
            verb = cmd.strip("/")
            ok, data = await self._call("POST", f"/admin/{verb}/{args[0]}")
            return f"✅ {verb}d `{args[0]}`" if ok else f"⚠️ {data.get('detail', data)}"

        if cmd == "/upstream":
            ok, data = await self._call("POST", "/admin/upstream/pull")
            return f"ℹ️ {data.get('detail', data)}"

        return f"Unknown command `{cmd}`. Try `/help`."

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _message_text(m: dict) -> str:
        content = m.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            )
        return ""

    @classmethod
    def _last_user_text(cls, body: dict) -> str:
        for m in reversed(body.get("messages", [])):
            if m.get("role") == "user":
                return cls._message_text(m)
        return ""

    @classmethod
    def _first_user_text(cls, body: dict) -> str:
        for m in body.get("messages", []):
            if m.get("role") == "user":
                return cls._message_text(m)
        return ""

    def _meta_response(self, task_type, body: dict) -> str:
        """Cheap answer for an OWUI background generation call (title, tags,
        follow-ups). Never triggers a real task."""
        t = str(task_type).lower()
        if "tag" in t:
            return '{"tags": []}'
        if "title" in t:
            first = self._first_user_text(body).strip()
            return " ".join(first.split()[:8]) or "Little Coder"
        return ""

    @staticmethod
    async def _status(emit, text: str, done: bool = False) -> None:
        if emit:
            with contextlib.suppress(Exception):
                await emit(
                    {"type": "status", "data": {"description": text, "done": done}}
                )

    async def _call(
        self, method: str, path: str, body: Optional[dict] = None
    ) -> tuple[bool, dict]:
        """Call the control daemon. Returns (ok, json-or-error-dict)."""
        url = self.valves.daemon_url.rstrip("/") + path
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, json=body) as resp:
                    try:
                        data = await resp.json()
                    except aiohttp.ContentTypeError:
                        data = {"detail": await resp.text()}
                    return (
                        resp.status < 400,
                        data if isinstance(data, dict) else {"data": data},
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return False, {"detail": f"daemon unreachable: {exc}"}
