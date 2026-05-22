"""
title: Little Coder
author: ai-stack
version: 0.1.0
license: MIT
description: Drive little-coder from OpenWebUI chat (Chapter 2 — OWUI pipeline).
  Plain messages trigger coding tasks; slash-commands (/project, /confirm, …)
  are operator actions, gated by the OpenWebUI user role.
required_open_webui_version: 0.5.0
"""

# little-coder — OWUI pipeline (design §12.6).
#
# This Pipe registers a "Little Coder" model in OpenWebUI. It is the chat-shaped
# surface for the control daemon:
#
#   - a plain message  → a task trigger (channel=owui, user_id = OWUI user).
#   - a /slash-command → an operator action, allowed only for OWUI users whose
#     role is in `operator_roles` (default: admin).
#
# Privilege separation (design §12.6): operator commands authenticate HERE, at
# the OpenWebUI surface (the user's OWUI role) — never at the MCP server. The
# MCP edge (lc-mcpo) carries task triggers only. The pipe reaches the daemon
# directly over the internal llm-net (the same trust model as mnemory).

import asyncio
import json
from typing import Optional

import aiohttp
from pydantic import BaseModel, Field

# Operator slash-commands (gated by OWUI role). `/help` and `/status` are open
# to any user — they are read-only.
_OPERATOR_CMDS = {"/project", "/confirm", "/pending", "/approve", "/reject", "/upstream"}
_OPEN_CMDS = {"/help", "/status"}
_TERMINAL = {"done", "abandoned", "rejected"}


def _cmd_mark(a: dict) -> str:
    """One-glyph status for a command the agent ran."""
    if a.get("ok"):
        return "✓"
    if a.get("denied"):
        return "⛔ blocked"
    return f"✗ exit {a.get('exit_code')}"

_HELP = """**Little Coder** — OpenWebUI surface

Send a plain message to trigger a coding task against the focused project.

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
            default=3.0, description="Task-status poll interval, seconds."
        )
        task_timeout_seconds: int = Field(
            default=2100,
            description="Give up waiting for a task after this many seconds.",
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
    ) -> str:
        message = self._last_user_text(body)
        if not message.strip():
            return _HELP

        if message.lstrip().startswith("/"):
            return await self._operator(message.strip(), __user__ or {})
        return await self._trigger(
            message, __user__ or {}, __metadata__ or {}, __event_emitter__
        )

    # -- task triggers -----------------------------------------------------

    async def _trigger(
        self, prompt: str, user: dict, metadata: dict, emit
    ) -> str:
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
            detail = data.get("detail", data)
            if "no project focused" in str(detail):
                return (
                    "⚠️ No project is focused. An operator must run "
                    "`/project <repo-url>` first."
                )
            return f"⚠️ Could not queue the task: {detail}"

        task_id = data["task_id"]
        await self._status(emit, f"Task {task_id[:12]}… queued")

        # Poll the daemon. The daemon reports live `activity` (the commands the
        # agent runs) so we can show the process unfold, not just a spinner.
        waited = 0.0
        seen_cmds = 0
        last_status = None
        while waited < self.valves.task_timeout_seconds:
            await asyncio.sleep(self.valves.poll_seconds)
            waited += self.valves.poll_seconds
            ok, state = await self._call("GET", f"/tasks/{task_id}")
            if not ok:
                await self._status(emit, "Lost contact with the daemon", done=True)
                return f"⚠️ Could not read task status: {state.get('detail', state)}"

            status = state.get("status")
            activity = state.get("activity") or []
            if len(activity) > seen_cmds:  # surface new commands as they run
                for a in activity[seen_cmds:]:
                    await self._status(
                        emit, f"⚙️ {_cmd_mark(a)}  {a.get('command', '')[:72]}"
                    )
                seen_cmds = len(activity)
            elif status != last_status:
                await self._status(emit, f"Agent {status}…")
            last_status = status

            if status in _TERMINAL:
                await self._status(
                    emit, f"Done — {len(activity)} command(s)", done=True
                )
                return self._render_result(task_id, state)

        await self._status(emit, "Timed out waiting", done=True)
        return (
            f"⌛ Task `{task_id}` is still running after "
            f"{self.valves.task_timeout_seconds}s — `/status` to check, "
            f"or `/confirm {task_id} …` once it lands."
        )

    @staticmethod
    def _render_result(task_id: str, state: dict) -> str:
        """The chat reply: the agent's actual answer, then a collapsible log of
        the commands it ran, then a one-line outcome footer."""
        answer = (state.get("answer") or "").strip()
        outcome = state.get("outcome")
        status = state.get("status")
        detail = state.get("detail", "")
        activity = state.get("activity") or []
        icon = {"pass": "✅", "fail": "❌", "unverified": "🔶"}.get(outcome, "▫️")

        parts: list[str] = []
        if answer:
            parts.append(answer)
        elif status == "abandoned":
            parts.append(f"⌛ The task was abandoned — {detail or 'see the daemon'}.")
        else:
            parts.append("_The agent finished but produced no text output._")

        if activity:
            rows = "\n".join(
                f"- `{a.get('command', '')}` — {_cmd_mark(a)}" for a in activity
            )
            parts.append(
                f"\n<details>\n<summary>🔧 {len(activity)} command(s) run "
                f"in open-terminal</summary>\n\n{rows}\n\n</details>"
            )

        footer = f"{icon} `{status}` · outcome `{outcome}` · task `{task_id}`"
        if outcome == "unverified":
            footer += f"\n*Confirm the real outcome:* `/confirm {task_id} pass|fail`"
        parts.append(f"\n---\n{footer}")
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
            # accepts `/project <url>` or `/project repo: <url>`
            link = args[-1] if args else ""
            if link.endswith(":"):
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
            pending = data.get("pending", [])
            return f"{len(pending)} pending artifact(s)."  # empty until Ch.4

        if cmd in ("/approve", "/reject"):
            if not args:
                return f"Usage: `{cmd} <artifact_id>`"
            verb = cmd.strip("/")
            ok, data = await self._call("POST", f"/admin/{verb}/{args[0]}")
            return (
                f"✅ {verb}d `{args[0]}`"
                if ok
                else f"⚠️ {data.get('detail', data)}"
            )

        if cmd == "/upstream":
            ok, data = await self._call("POST", "/admin/upstream/pull")
            return f"ℹ️ {data.get('detail', data)}"

        return f"Unknown command `{cmd}`. Try `/help`."

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _last_user_text(body: dict) -> str:
        for m in reversed(body.get("messages", [])):
            if m.get("role") != "user":
                continue
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

    @staticmethod
    async def _status(emit, text: str, done: bool = False) -> None:
        if emit:
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
                    return (resp.status < 400, data if isinstance(data, dict) else {"data": data})
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            return False, {"detail": f"daemon unreachable: {exc}"}
