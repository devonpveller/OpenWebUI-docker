"""
title: Little Coder
author: ai-stack
version: 0.7.0
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
_OPERATOR_CMDS = {"/project", "/confirm", "/pending", "/approve", "/reject", "/upstream", "/observe", "/bootstrap-agents"}

# Slash-commands that may block the chat for many seconds — these get a
# "doing…" status emit before the daemon call so OWUI's status bar shows
# the operator the work is in progress. Map: cmd → status label.
_LONG_RUNNING_CMDS: dict[str, str] = {
    "/project": "Cloning project — this may take a while…",
    "/upstream": "Pulling fork-parent…",
    "/observe": "Running Observer iteration…",
}

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
- `/observe` — show the Observer report (`/observe iterate` runs a fresh meta pass first)
- `/bootstrap-agents [mode]` — explicitly trigger an AGENTS.md bootstrap for the focused repo. Modes: empty/`commit` (default — bootstrap + separate commit), `nocommit` (bootstrap, leave uncommitted; you commit or discard from a host shell), `revert` (undo bootstrap + drop `.no-agents-md` opt-out marker)

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


def _describe_tool(tc: dict) -> str:
    """A short one-line description of a tool call. NEVER dumps file content
    or other large argument values into the chat — a `write` call's content
    can be hundreds of lines."""
    name = tc.get("name", "tool")
    args = tc.get("arguments")
    if not isinstance(args, dict):
        return name
    if "command" in args:  # bash / shell
        cmd = str(args["command"]).replace("\n", " ⏎ ")
        return f"{name}: {cmd[:200]}"
    path = args.get("file_path") or args.get("path") or args.get("filePath")
    if path:  # file tools — path + a size hint, never the content
        if "content" in args:
            n = str(args["content"]).count("\n") + 1
            return f"{name}: {path}  ({n} lines)"
        if any(k in args for k in ("old_text", "new_text", "old_string")):
            return f"{name}: {path}  (edit)"
        return f"{name}: {path}"
    if "pattern" in args:
        return f"{name}: {args['pattern']}"
    if "query" in args:
        return f"{name}: {str(args['query'])[:120]}"
    if "url" in args:
        return f"{name}: {args['url']}"
    keys = ", ".join(sorted(str(k) for k in args))  # fallback — keys, not values
    return f"{name}({keys})"


class _Render:
    """Turns the agent's pi `--mode json` events into a live markdown stream.

    The process — thinking and tool calls — is wrapped in a `<think>` block,
    which OpenWebUI renders as a collapsible reasoning panel that auto-collapses
    when the answer begins. The agent's answer is the plain message body, so a
    copied message is the clean answer, not the whole transcript.

    Compaction tracking (design §3.1 follow-up): when pi runs in
    session mode (which little-coder does — see `agent.use_session`),
    pi auto-compacts the session as it grows past its context budget.
    We surface compaction events both as a status emit (operator sees
    "Compacting context…" in OWUI's status bar) and as a small line in
    the reasoning panel so the operator can SEE that summarization
    happened (otherwise it looks like the agent silently forgot)."""

    def __init__(self, show_thinking: bool = True) -> None:
        self.show_thinking = show_thinking
        self.think_open = False
        self.answering = False
        self._tools: set = set()
        # Track compaction events so the footer can render a count.
        self.compactions_seen = 0
        self.last_compaction: Optional[dict] = None

    def _open_think(self) -> str:
        if self.think_open or self.answering:
            return ""
        self.think_open = True
        return "<think>\n"

    def _close_think(self) -> str:
        if not self.think_open:
            return ""
        self.think_open = False
        return "\n</think>\n\n"

    def feed(self, ev: dict) -> str:
        # Compaction events — pi emits these in session mode (design
        # §3.1 follow-up). Track them so the footer reflects what
        # happened; render a short line in the reasoning panel so the
        # operator SEES the summarization (otherwise the agent looks
        # like it silently forgot earlier turns).
        ev_type = ev.get("type")
        if ev_type == "compaction_start":
            reason = ev.get("reason", "?")
            return self._open_think() + (
                f"\n🧠 Compacting context — older turns being summarized "
                f"(reason: {reason})…\n"
            )
        if ev_type == "compaction_end":
            self.compactions_seen += 1
            self.last_compaction = {
                "reason": ev.get("reason"),
                "aborted": ev.get("aborted", False),
                "result": ev.get("result") or {},
            }
            if ev.get("aborted"):
                return self._open_think() + "\n🧠 Compaction aborted.\n"
            tokens_before = (ev.get("result") or {}).get("tokensBefore")
            extra = (
                f" ({tokens_before} tokens compacted)" if tokens_before else ""
            )
            return self._open_think() + f"\n🧠 Context compacted{extra}.\n"

        if ev_type != "message_update":
            return ""
        ame = ev.get("assistantMessageEvent") or {}
        t = ame.get("type")

        if t == "thinking_delta":
            if not self.show_thinking:
                return ""
            return self._open_think() + str(ame.get("delta", ""))

        if t == "toolcall_end":
            tc = self._tool_call(ame.get("partial"))
            if not tc or tc.get("id") in self._tools:
                return ""
            self._tools.add(tc.get("id"))
            return self._open_think() + f"\n🔧 {_describe_tool(tc)}\n"

        if t == "text_delta":
            prefix = self._close_think() if not self.answering else ""
            self.answering = True
            return prefix + str(ame.get("delta", ""))

        return ""

    def finish(self) -> str:
        """Close the reasoning block if the agent produced no answer text."""
        return self._close_think()

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
            cmd_name = message.strip().split(None, 1)[0].lower()
            # `/bootstrap-agents` spawns a real task and STREAMS the
            # agent's progress like a normal chat trigger — it can't
            # be a single-string return. Route it through the
            # streaming path directly. Operator-role gating happens
            # in `_dispatch_bootstrap` before the trigger.
            if cmd_name == "/bootstrap-agents":
                async for chunk in self._dispatch_bootstrap(
                    message.strip(), __user__ or {}, meta, __event_emitter__
                ):
                    yield chunk
                return
            # Other slash-commands return a single chat reply.
            # Long-running ones get a status indicator so the chat
            # doesn't appear frozen during the daemon call.
            if cmd_name in _LONG_RUNNING_CMDS:
                label = _LONG_RUNNING_CMDS[cmd_name]
                await self._status(__event_emitter__, label)
            try:
                result = await self._operator(message.strip(), __user__ or {})
            finally:
                if cmd_name in _LONG_RUNNING_CMDS:
                    await self._status(__event_emitter__, "Done", done=True)
            yield result
            return

        # The daemon receives `session_id = chat_id` so the agent can
        # use its native session-per-chat continuity (design §3.1
        # follow-up — see daemon.py + agent.py for the wiring).
        async for chunk in self._trigger_stream(
            message, __user__ or {}, meta, __event_emitter__
        ):
            yield chunk

    # -- task triggers — live streaming ------------------------------------

    async def _trigger_stream(
        self,
        prompt: str,
        user: dict,
        metadata: dict,
        emit,
        *,
        existing_task_id: Optional[str] = None,
    ):
        """Trigger a task (or stream an already-triggered one) and
        render its progress into the chat.

        `existing_task_id` lets operator-action slash-commands
        (`/bootstrap-agents`, …) reuse the streaming machinery
        without re-triggering — they call the admin endpoint that
        spawns the task, then pass the returned task_id here. When
        None, behaves the original way: POST /tasks with `prompt`,
        then stream."""
        if existing_task_id is not None:
            task_id = existing_task_id
        else:
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
                # Track whether this batch saw a compaction so we can
                # emit a "Compacting context…" status line that lasts
                # for the operator to actually see in OWUI's status bar.
                compacted_in_batch = False
                compaction_in_flight = False
                for raw in data.get("events", []):
                    try:
                        ev = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("type") == "compaction_start":
                        compaction_in_flight = True
                    elif ev.get("type") == "compaction_end":
                        compaction_in_flight = False
                        if not ev.get("aborted"):
                            compacted_in_batch = True
                    chunk = renderer.feed(ev)
                    if chunk:
                        yield chunk
                if data.get("done"):
                    done = True
                    break
                # Distinguish queued vs compacting vs running. Compaction
                # is the noisiest of the three — show it explicitly so
                # the operator knows pi is summarizing rather than stuck.
                if data.get("status") == "queued":
                    await self._status(
                        emit, "⏳ Queued — another task is running (one at a time)…"
                    )
                elif compaction_in_flight:
                    await self._status(
                        emit, "🧠 Compacting session context (auto)…"
                    )
                elif compacted_in_batch:
                    await self._status(
                        emit, "🧠 Context compacted — continuing…"
                    )
                else:
                    await self._status(emit, "Agent working…")

            ok, final = await self._call("GET", f"/tasks/{task_id}")
            tail = renderer.finish()  # close the <think> block if still open
            if tail:
                yield tail
            await self._status(emit, "Done", done=True)
            if not done:
                yield (
                    f"\n\n⌛ Still running after "
                    f"{self.valves.task_timeout_seconds}s — `/status` to check, "
                    f"or **Stop** to interrupt."
                )
            yield self._footer(task_id, final if ok else {}, renderer)
        except asyncio.CancelledError:
            # OWUI "stop" — interrupt the task (design §12.4: abandonment).
            asyncio.ensure_future(self._cancel_quietly(task_id))
            await self._status(emit, "Interrupted", done=True)
            raise

    async def _cancel_quietly(self, task_id: str) -> None:
        with contextlib.suppress(Exception):
            await self._call("POST", f"/tasks/{task_id}/cancel")

    @staticmethod
    def _footer(task_id: str, state: dict, renderer: "_Render | None" = None) -> str:
        """Closing block: the command log + compaction note + one-line
        outcome. The renderer carries this-task compaction counts so
        the operator can see whether pi summarized older turns during
        the run (design §3.1 follow-up tracking)."""
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
        if renderer and renderer.compactions_seen > 0:
            last = renderer.last_compaction or {}
            result = last.get("result") or {}
            tokens = result.get("tokensBefore")
            extra = f" (last: {tokens} tokens compacted)" if tokens else ""
            parts.append(
                f"🧠 _Session context was compacted "
                f"{renderer.compactions_seen}× during this task_{extra}.\n"
            )
        foot = f"{icon} `{status}` · outcome `{outcome}` · task `{task_id}`"
        if outcome == "unverified":
            foot += f"\n*Confirm the real outcome:* `/confirm {task_id} pass|fail`"
        parts.append(foot)
        return "\n".join(parts)

    # -- operator-triggered bootstrap (slash command + task streaming) ----

    async def _dispatch_bootstrap(
        self, message: str, user: dict, metadata: dict, emit
    ):
        """`/bootstrap-agents [mode]` — operator-triggered AGENTS.md
        bootstrap (design §3.7 layer 3). Three modes — empty (default
        `commit`), `nocommit`, `revert`. Operator-role gated; routes
        through `/admin/bootstrap-agents` which spawns a task with
        the matching server-side prompt. We then stream the spawned
        task back to the chat via `_trigger_stream(existing_task_id=…)`."""
        parts = message.split()
        args = parts[1:]
        mode = (args[0].lower() if args else "commit").strip()
        if mode not in ("commit", "nocommit", "revert"):
            yield (
                f"⚠️ Unknown mode `{mode}`. Valid: "
                f"`commit` (default — bootstrap + separate commit), "
                f"`nocommit` (bootstrap, leave uncommitted), "
                f"`revert` (undo bootstrap + drop `.no-agents-md` opt-out)."
            )
            return

        # Operator-role gate (same posture as the other slash commands).
        roles = {r.strip() for r in self.valves.operator_roles.split(",")}
        if (user.get("role") or "") not in roles:
            yield (
                f"⛔ `/bootstrap-agents` is an operator command. Your "
                f"account (`{user.get('role') or 'unknown'}`) is not an "
                f"operator."
            )
            return

        actor = user.get("email") or user.get("id") or "owui"
        await self._status(emit, f"Triggering bootstrap-agents (mode={mode})…")
        ok, data = await self._call(
            "POST",
            "/admin/bootstrap-agents",
            {"mode": mode, "actor": actor},
        )
        if not ok:
            await self._status(emit, "Failed", done=True)
            detail = str(data.get("detail", data))
            if "no project focused" in detail:
                yield "⚠️ No project is focused. Run `/project <repo-url>` first."
            else:
                yield f"⚠️ Could not trigger bootstrap: {detail}"
            return

        task_id = data["task_id"]
        # Render a one-line preamble so the operator sees the mode +
        # task id even if the agent is slow to produce first output.
        yield f"**🚀 Bootstrap triggered** (mode `{mode}`, task `{task_id}`)\n\n"
        # Reuse the normal task-streaming machinery — same renderer,
        # same status emits, same footer.
        async for chunk in self._trigger_stream(
            prompt="", user=user, metadata=metadata, emit=emit,
            existing_task_id=task_id,
        ):
            yield chunk

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
            if not ok:
                return f"⚠️ {data.get('detail', data)}"
            action = data.get("action", "?")
            focus = data.get("focus", "?")
            if action == "clone":
                return f"✅ Cloned `{focus}` — workspace ready."
            if action == "switch":
                return f"✅ Switched focus to `{focus}` — workspace ready."
            if action == "noop":
                return f"ℹ️ Already focused on `{focus}`."
            return f"✅ {action}: focus = `{focus}`"

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
            return self._format_pending(data.get("pending", []))

        if cmd in ("/approve", "/reject"):
            if not args:
                return f"Usage: `{cmd} <artifact_id>`"
            verb = cmd.strip("/")
            ok, data = await self._call("POST", f"/admin/{verb}/{args[0]}")
            return f"✅ {verb}d `{args[0]}`" if ok else f"⚠️ {data.get('detail', data)}"

        if cmd == "/upstream":
            ok, data = await self._call("POST", "/admin/upstream/pull")
            return f"ℹ️ {data.get('detail', data)}"

        if cmd == "/observe":
            params = {}
            if args and args[0].lower() == "iterate":
                params["iterate"] = "true"
            ok, data = await self._call("GET", "/admin/observe", params=params)
            if not ok:
                return f"⚠️ {data.get('detail', data)}"
            if not data.get("enabled", False):
                return f"ℹ️ {data.get('note', 'Observer is disabled')}"
            return self._format_observe(data)

        return f"Unknown command `{cmd}`. Try `/help`."

    @staticmethod
    def _format_pending(rows: list) -> str:
        """Render the pending-skills list for chat. Body is truncated to
        keep the surface scannable — operator can fetch the full body
        via `lc admin pending --json` on the host."""
        if not rows:
            return "_No pending skill drafts._"
        out = [f"## {len(rows)} pending skill draft(s)\n"]
        for row in rows:
            cluster = row.get("cluster") or {}
            out.append(
                f"### `{row['id']}` — tier-{row['tier']} {row['kind']}: "
                f"**{row['name']}**"
            )
            out.append(
                f"- lang=`{row['lang']}` domain=`{row['domain']}` "
                f"task_shape=`{row['task_shape']}`"
            )
            if cluster:
                out.append(
                    f"- cluster: _{cluster.get('label')}_ "
                    f"(baseline_covers={cluster.get('baseline_covers')}, "
                    f"observed={cluster.get('observed')})"
                )
            out.append(f"- description: {row['description']}")
            body_preview = row["body"][:400].replace("\n", " ")
            out.append(f"- body preview: {body_preview}...")
            out.append(
                f"- review: `/approve {row['id']}` · `/reject {row['id']}`"
            )
            out.append("")
        return "\n".join(out)

    @staticmethod
    def _format_observe(report: dict) -> str:
        """Render the Observer report as Markdown for the chat surface.
        Mirrors the CLI's `render_text` shape but uses Markdown so OWUI
        renders the sections, tables, and IDs in monospace."""
        lines = ["## Observer report"]
        li = report.get("last_iteration")
        if li:
            lines.append(
                f"_last iteration {li['ts']} — "
                f"records={li['records_consumed']}, "
                f"clusters={li['clusters_total']}, "
                f"observed={li['occurrences_total']}, "
                f"unassigned={li['unassigned_total']}_"
            )
            if li.get("minted_cluster_ids"):
                lines.append(f"_minted this run: {len(li['minted_cluster_ids'])}_")
        else:
            lines.append("_no iteration has completed yet_")

        def section(title: str, rows: list, tier_hint: str) -> None:
            lines.append("")
            lines.append(f"### {title} ({len(rows)}) — _{tier_hint}_")
            if not rows:
                lines.append("_(none)_")
                return
            for r in rows:
                lines.append(
                    f"- `{r['cluster_id'][:8]}` **{r['label']}** "
                    f"— {r['lang']}|{r['task_shape']}, "
                    f"observed={r['observed']}"
                )
                if r.get("top_repos"):
                    repos = ", ".join(f"`{repo}`={n}" for repo, n in r["top_repos"])
                    lines.append(f"  - top repos: {repos}")

        section(
            "Knowledge gaps",
            report.get("knowledge_gaps") or [],
            "baseline silent — tier-0 candidates",
        )
        section(
            "Compliance gaps",
            report.get("compliance_gaps") or [],
            "baseline covers — tier-1 enforcement",
        )

        unassigned = report.get("unassigned") or []
        lines.append("")
        lines.append(f"### Unassigned scopes ({len(unassigned)})")
        if not unassigned:
            lines.append("_(none)_")
        else:
            for u in unassigned:
                lines.append(
                    f"- `{u['lang']}|{u['task_shape']}` — pool size **{u['size']}**"
                )
        return "\n".join(lines)

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
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> tuple[bool, dict]:
        """Call the control daemon. Returns (ok, json-or-error-dict).
        `params` is the query-string dict — required for endpoints like
        `/admin/observe?iterate=true` where the daemon parses a query
        parameter, not a request body."""
        url = self.valves.daemon_url.rstrip("/") + path
        timeout = aiohttp.ClientTimeout(total=60)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(
                    method, url, json=body, params=params
                ) as resp:
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
