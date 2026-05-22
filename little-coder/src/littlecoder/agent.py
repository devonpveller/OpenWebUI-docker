"""Agent integration — runs the upstream little-coder CLI and instruments it.

little-coder is a Node.js CLI; this wraps it without changing its inner loop
(design §3.1). It runs in the control plane with its file tools acting on the
shared `/workspace` volume; its command execution is routed to open-terminal
via `ot-exec`, so build / test / git run in the network-isolated plane and
pass the git-proxy (design §1.5, §3.3).

INTEGRATION POINTS (pinned to the upstream version when the agent image is
built): the exact CLI invocation — config `agent.*` — and the pi extension
that points little-coder's shell tool at `ot-exec` — see `pi-extension/`.
"""

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import tempfile

from .config import Config
from .journals import Journals
from .openterminal import OpenTerminalClient
from .tasks import TaskContext, digest


class TaskTimeout(RuntimeError):
    """The agent exceeded the per-channel abandoned-timeout (design §4.2)."""


def kill_process_group(proc) -> None:
    """Kill the agent and ALL its descendants. The agent is started in its own
    session (`start_new_session`), so its children — `ot-exec` and their own
    children — share its process-group id. Killing the whole group ensures a
    `communicate()` call is never left waiting on a surviving grandchild that
    still holds the stderr pipe open."""
    if proc is None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        with contextlib.suppress(Exception):
            proc.kill()


@dataclasses.dataclass
class AgentResult:
    outcome: str  # pass | fail | unverified
    signal: str | None
    exit_code: int | None
    commands_run: int


def _event_to_activity(ev: dict) -> dict:
    """One ot-exec command event → a compact activity record for the UI."""
    denied = bool(ev.get("git_proxy_denied"))
    code = ev.get("exit_code")
    return {
        "command": str(ev.get("command", ""))[:240],
        "exit_code": code,
        "ok": code == 0 and not denied,
        "denied": denied,
        "duration_ms": ev.get("duration_ms"),
        "stderr_tail": str(ev.get("stderr_tail", ""))[:500],
    }


def read_activity_file(path: str) -> list[dict]:
    """Parse the ot-exec event stream into command-activity records. Safe to
    call mid-task — a partial trailing line is skipped, so the daemon can read
    it live to report progress."""
    items: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(_event_to_activity(json.loads(line)))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return items


def extract_answer(pi_events_path: str) -> str:
    """Pull the agent's final answer text out of the pi `--mode json` stream.
    Prefer the `agent_end` event's final messages; fall back to the
    concatenated `text_delta` events."""
    deltas: list[str] = []
    try:
        with open(pi_events_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                etype = ev.get("type")
                if etype == "agent_end":
                    for msg in reversed(ev.get("messages") or []):
                        if msg.get("role") != "assistant":
                            continue
                        text = "".join(
                            c.get("text", "")
                            for c in msg.get("content") or []
                            if isinstance(c, dict) and c.get("type") == "text"
                        )
                        if text.strip():
                            return text.strip()
                elif etype == "message_update":
                    ame = ev.get("assistantMessageEvent") or {}
                    if ame.get("type") == "text_delta":
                        deltas.append(str(ame.get("delta", "")))
    except OSError:
        pass
    return "".join(deltas).strip()


class AgentRunner:
    """One method: `run_task`. Blocking — the daemon calls it off the event
    loop (`asyncio.to_thread`)."""

    def __init__(
        self, config: Config, journals: Journals, ot_client: OpenTerminalClient
    ) -> None:
        self.cfg = config
        self.journals = journals
        self.ot = ot_client

    def run_task(self, ctx: TaskContext, timeout: int) -> AgentResult:
        """Run one task to completion. Writes `tool_call` / `error` records;
        the daemon brackets the task with `task_started` / `task_ended`.
        Raises TaskTimeout if the agent outlives `timeout`."""
        fd, ot_events = tempfile.mkstemp(suffix=".jsonl", prefix="lc-ot-")
        os.close(fd)
        ctx.state.event_stream_path = ot_events  # ot-exec stream → journals
        # The pi --mode json events stream live to this file; the daemon
        # serves it to the chat surface, which reads it through to the end,
        # so it is NOT unlinked with the task (tmpfs clears it on restart).
        pi_events = os.path.join(
            tempfile.gettempdir(), f"lc-pi-{ctx.state.task_id}.jsonl"
        )
        ctx.state.events_path = pi_events
        try:
            env = self._build_env(ctx, ot_events)
            cmd, stdin_text = self._build_invocation(ctx.state.prompt)
            exit_code = self._run_cli(cmd, stdin_text, env, timeout, ctx, pi_events)
            commands = self._drain_events(ot_events, ctx)
            outcome, signal = self._verdict(ctx)
            return AgentResult(outcome, signal, exit_code, commands)
        finally:
            ctx.state.event_stream_path = None  # ot-exec file is removed
            with contextlib.suppress(OSError):
                os.unlink(ot_events)

    def _build_env(self, ctx: TaskContext, event_stream: str) -> dict[str, str]:
        st = ctx.state
        env = dict(os.environ)
        env.update(
            {
                # Task attribution — propagates to `ot-exec` subprocesses.
                "LC_TASK_ID": st.task_id,
                "LC_SESSION_ID": st.session_id,
                "LC_CHANNEL": st.channel,
                "LC_USER_ID": st.user_id,
                "LC_REPO": st.repo,
                "LC_EVENT_STREAM": event_stream,
                # Workspace plane.
                "LC_WORKSPACE": self.cfg.workspace.path,
                "LC_OPEN_TERMINAL_URL": self.cfg.workspace.open_terminal_url,
                "LC_OPEN_TERMINAL_KEY": os.environ.get(
                    self.cfg.workspace.open_terminal_key_env, ""
                ),
                "LC_EXEC_TIMEOUT": str(self.cfg.workspace.exec_timeout_seconds),
                # little-coder's llama-cpp provider.
                "LLAMACPP_BASE_URL": self.cfg.inference.base_url,
                "LLAMACPP_API_KEY": os.environ.get(
                    self.cfg.inference.api_key_env, "llama"
                ),
                # Disable little-coder's own bash command-whitelist gate: the
                # git-proxy + open-terminal network isolation are the policy
                # (design §3.3–§3.4). Its prefix list is redundant, blocks
                # `cd`, and — worst — only gates `bash`, which pushed the agent
                # to escape via ShellSession.
                "LITTLE_CODER_PERMISSION_MODE": "accept-all",
            }
        )
        return env

    def _build_invocation(self, prompt: str) -> tuple[list[str], str | None]:
        a = self.cfg.agent
        cmd = [*a.command, "--model", a.model, *a.extra_args]
        if a.prompt_mode == "arg":
            return [*cmd, prompt], None
        return cmd, prompt  # stdin

    def _run_cli(
        self,
        cmd: list[str],
        stdin_text: str | None,
        env: dict[str, str],
        timeout: int,
        ctx: TaskContext,
        pi_events: str,
    ) -> int | None:
        """Run the agent, streaming its `--mode json` events to `pi_events`
        as they are produced. The process handle is published on the task
        state so the daemon can cancel it."""
        try:
            events_fh = open(pi_events, "w", encoding="utf-8")
        except OSError as exc:
            self.journals.write(ctx.error("daemon_error", f"events file: {exc}"))
            return None
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=self.cfg.workspace.path,
                env=env,
                stdin=subprocess.PIPE if stdin_text else subprocess.DEVNULL,
                stdout=events_fh,  # NDJSON events stream straight to the file
                stderr=subprocess.PIPE,
                text=True,
                # Own session/process-group so cancel/timeout can kill the
                # agent AND its descendants (kill_process_group).
                start_new_session=True,
                # umask 000: files the agent edits must stay writable from the
                # open-terminal plane too (shared workspace volume).
                preexec_fn=(lambda: os.umask(0)) if os.name == "posix" else None,
            )
        except FileNotFoundError as exc:
            events_fh.close()
            self.journals.write(ctx.error("agent_missing", str(exc), tool="agent"))
            return 127

        ctx.state.agent_process = proc  # published so the daemon can cancel
        stderr = ""
        try:
            _, stderr = proc.communicate(input=stdin_text, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            kill_process_group(proc)
            with contextlib.suppress(Exception):
                proc.communicate()
            self.journals.write(ctx.error("timeout", f"agent exceeded {timeout}s"))
            raise TaskTimeout(f"agent exceeded {timeout}s") from exc
        finally:
            ctx.state.agent_process = None
            events_fh.close()

        rc = proc.returncode
        ctx.state.answer = extract_answer(pi_events)
        # rc < 0 ⇒ the process was killed (cancel / shutdown) — not a fault.
        if rc is not None and rc > 0:
            tail = (stderr or "")[-1000:]
            self.journals.write(
                ctx.error("agent_exit", f"exit {rc}: {tail}", tool="agent")
            )
        return rc

    def _drain_events(self, event_stream: str, ctx: TaskContext) -> int:
        """Convert `ot-exec`'s command events into the task's `activity` list
        and journal records. Each command becomes a `tool_call`; a non-zero
        exit or a git-proxy denial also becomes an `error` (design §4, §9.1)."""
        activity = read_activity_file(event_stream)
        ctx.state.activity = activity
        for item in activity:
            self.journals.write(
                ctx.tool_call(
                    tool="bash",
                    ok=item["ok"],
                    args_digest=digest(item["command"]),
                    duration_ms=item.get("duration_ms"),
                )
            )
            if item["denied"]:
                self.journals.write(
                    ctx.error("git_blocked", item["stderr_tail"], tool="git")
                )
            elif not item["ok"]:
                self.journals.write(
                    ctx.error(
                        "command_failed",
                        f"exit {item['exit_code']}: {item['stderr_tail']}",
                        tool="bash",
                    )
                )
        return len(activity)

    def _verdict(self, ctx: TaskContext) -> tuple[str, str | None]:
        """Outcome ∈ pass/fail/unverified — `pass`/`fail` only with a
        checkable signal (design §4.2). With no acceptance command the task
        is `unverified`; the operator confirms via `lc admin task confirm`."""
        acceptance = ctx.state.acceptance_command
        if not acceptance:
            return "unverified", None
        result = self.ot.execute(
            acceptance,
            cwd=self.cfg.workspace.path,
            timeout=self.cfg.workspace.exec_timeout_seconds,
        )
        self.journals.write(
            ctx.tool_call(
                tool="acceptance", ok=result.ok, args_digest=digest(acceptance)
            )
        )
        if result.ok:
            return "pass", "acceptance command exit 0"
        return "fail", f"acceptance command exit {result.exit_code}"
