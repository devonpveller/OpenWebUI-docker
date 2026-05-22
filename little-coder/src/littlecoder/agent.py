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

import dataclasses
import json
import os
import subprocess
import tempfile

from .config import Config
from .journals import Journals
from .openterminal import OpenTerminalClient
from .tasks import TaskContext, digest


class TaskTimeout(RuntimeError):
    """The agent exceeded the per-channel abandoned-timeout (design §4.2)."""


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
        fd, event_stream = tempfile.mkstemp(suffix=".jsonl", prefix="lc-events-")
        os.close(fd)
        # The daemon reads this path live to report progress (read_activity_file).
        ctx.state.event_stream_path = event_stream
        try:
            env = self._build_env(ctx, event_stream)
            cmd, stdin_text = self._build_invocation(ctx.state.prompt)
            exit_code = self._run_cli(cmd, stdin_text, env, timeout, ctx)
            commands = self._drain_events(event_stream, ctx)
            outcome, signal = self._verdict(ctx)
            return AgentResult(outcome, signal, exit_code, commands)
        finally:
            ctx.state.event_stream_path = None  # file is about to be removed
            try:
                os.unlink(event_stream)
            except OSError:
                pass

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
    ) -> int | None:
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.cfg.workspace.path,
                env=env,
                input=stdin_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                # umask 000: files the agent edits must stay writable from
                # the open-terminal plane too (shared workspace volume).
                preexec_fn=(lambda: os.umask(0)) if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired as exc:
            self.journals.write(ctx.error("timeout", f"agent exceeded {timeout}s"))
            raise TaskTimeout(f"agent exceeded {timeout}s") from exc
        except FileNotFoundError as exc:
            # The little-coder binary is missing — a deploy fault, not a task
            # fault. Journal it and let the daemon end the task `unverified`.
            self.journals.write(ctx.error("agent_missing", str(exc), tool="agent"))
            return 127
        # The agent's stdout IS its answer (`--mode text --print`). Surface it
        # — this is what the operator actually asked to see.
        ctx.state.answer = (proc.stdout or "").strip()
        if proc.returncode != 0:
            tail = (proc.stderr or "")[-1000:]
            self.journals.write(
                ctx.error("agent_exit", f"exit {proc.returncode}: {tail}", tool="agent")
            )
        return proc.returncode

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
