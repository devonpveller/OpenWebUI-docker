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
import threading
import time

from .config import Config
from .journals import Journals
from .openterminal import OpenTerminalClient
from .sanitize import redact_secrets
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
    """One ot-exec command event → a compact activity record for the UI. The command + its stderr
    are streamed to the operator's chat and journaled, so a deploy token that surfaced in either
    (e.g. `git remote -v`, a clone/fetch auth error) is masked here before it leaves the worker."""
    denied = bool(ev.get("git_proxy_denied"))
    code = ev.get("exit_code")
    # Redact BEFORE truncating so a token can't survive as a fragment split across the length cap.
    return {
        "command": redact_secrets(str(ev.get("command", "")))[:240],
        "exit_code": code,
        "ok": code == 0 and not denied,
        "denied": denied,
        "duration_ms": ev.get("duration_ms"),
        "stderr_tail": redact_secrets(str(ev.get("stderr_tail", "")))[:500],
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
                            return redact_secrets(text.strip())  # never echo a token in the answer
                elif etype == "message_update":
                    ame = ev.get("assistantMessageEvent") or {}
                    if ame.get("type") == "text_delta":
                        deltas.append(str(ame.get("delta", "")))
    except OSError:
        pass
    return redact_secrets("".join(deltas).strip())


FLAIL_MARKER = "FLAIL-GUARD:"

# Tool executions that CHANGE the workspace. `bash` is deliberately not counted as an edit —
# it's the exploration surface here (grep/find/build probes route through it), and a worker
# genuinely progressing edits via the edit/write tools.
_EDIT_TOOLS = ("edit", "write")


def count_tool_executions(pi_events_path: str) -> tuple[int, int]:
    """(total, edit_write) `tool_execution_start` counts from the live pi `--mode json` stream.
    Safe mid-run — a partial trailing line is skipped (same contract as read_activity_file)."""
    total = edits = 0
    try:
        with open(pi_events_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or '"tool_execution_start"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") != "tool_execution_start":
                    continue
                total += 1
                if str(ev.get("toolName", "")).lower() in _EDIT_TOOLS:
                    edits += 1
    except OSError:
        pass
    return total, edits


def flail_tripped(total: int, edits: int, elapsed_s: float, cfg) -> str | None:
    """WHY this turn is flailing (operator 2026-07-14: "too many thinking turns or time
    iterating on read without editing anything"), or None. Trips only with ZERO edit/write
    executions; the time trip additionally requires `min_tool_calls` of activity so a slow,
    thoughtful turn with a couple of reads is never killed on elapsed time alone."""
    if edits > 0:
        return None
    if total >= cfg.tool_calls:
        return f"{total} read-only tool calls with zero file edits"
    if elapsed_s >= cfg.seconds and total >= cfg.min_tool_calls:
        return (f"{int(elapsed_s // 60)} min iterating on reads ({total} tool calls) "
                f"with zero file edits")
    return None


class _FlailWatcher(threading.Thread):
    """Watches a running agent turn for read-without-edit flailing and kills it when it trips
    (opt-in via the task's `flail_guard`). The kill is reported through the answer: `run_task`
    prefixes the FLAIL-GUARD marker so the bridge can fork a fresh session and re-plan instead
    of treating this as an ordinary failure."""

    def __init__(self, proc, pi_events: str, cfg, ctx: "TaskContext") -> None:
        super().__init__(daemon=True, name="flail-watcher")
        self.proc = proc
        self.pi_events = pi_events
        self.cfg = cfg
        self.ctx = ctx
        self._stop = threading.Event()
        self.started_at = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.wait(self.cfg.poll_seconds):
            if self.proc.poll() is not None:
                return
            total, edits = count_tool_executions(self.pi_events)
            reason = flail_tripped(total, edits, time.monotonic() - self.started_at, self.cfg)
            if reason is None:
                continue
            self.ctx.state.signal = "flail_guard"
            self.ctx.state.detail = reason
            kill_process_group(self.proc)
            return


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
            cmd, stdin_text = self._build_invocation(ctx.state.prompt, ctx)
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

    def _build_invocation(
        self, prompt: str, ctx: "TaskContext"
    ) -> tuple[list[str], str | None]:
        """Compose pi's argv.

        Session handling (design §3.1 follow-up): when
        `agent.use_session=True`, pass `--session <id>` + `--session-dir
        <dir>` so pi loads the per-chat session and handles compaction
        natively. When False, pass `--no-session` (Chapter-1–3 strict
        statelessness). The session id comes from the task envelope:
        OWUI passes the chat_id as session_id; CLI uses a per-channel
        default. Switching channels DOES NOT cross-contaminate sessions.

        IMPORTANT: any `--no-session` or `--session*` flag the operator
        left in `extra_args` is FILTERED so the daemon's wiring wins —
        the design lock is "the daemon owns session policy", not "any
        config wins".

        PLAN-ONLY turns (agent-org bridge, 2026-07-14): when the task's
        `plan_only` flag is set, `edit,write` are merged into the
        `--exclude-tools` denylist so the turn can explore and PLAN but
        cannot change a file — the headless equivalent of the upstream
        plan-mode extension's edit guard ("plan mode produces a plan,
        not changes"). The config's own denylist is preserved (merged,
        not replaced); the daemon owns the flag either way."""
        a = self.cfg.agent
        plan_only = bool(getattr(ctx.state, "plan_only", False))
        # Strip session-related flags + their values from extra_args
        # so we own the policy here. pi's session flags are:
        #   --no-session   (no value)
        #   --continue / -c (no value — we don't use)
        #   --session / --session-dir / --fork  (each takes 1 value)
        # On a plan-only turn, `--exclude-tools` is ALSO lifted out of
        # extra_args (its value captured) so the merged list below is
        # the single authoritative denylist.
        filtered_extra: list[str] = []
        exclude_tools = ""
        skip_next = False
        capture_exclude = False
        for arg in a.extra_args:
            if skip_next:
                if capture_exclude:
                    exclude_tools = arg
                    capture_exclude = False
                skip_next = False
                continue
            if arg in ("--no-session", "-c", "--continue"):
                continue
            if arg in ("--session", "--session-dir", "--fork", "-r", "--resume"):
                skip_next = True
                continue
            if plan_only and arg in ("--exclude-tools", "-xt"):
                skip_next = True
                capture_exclude = True
                continue
            filtered_extra.append(arg)

        cmd = [*a.command, "--model", a.model, *filtered_extra]
        if plan_only:
            merged = ",".join(x for x in (exclude_tools, "edit,write") if x)
            cmd.extend(["--exclude-tools", merged])

        if a.use_session:
            session_path = self._session_path_for(ctx)
            # Pass the FULL PATH (not a bare id) so pi's
            # resolveSessionPath treats it as `type: path` →
            # SessionManager.open(path) which creates the file if
            # missing. A bare id triggers id-lookup which fails on
            # a fresh chat with `No session found matching <id>`.
            cmd.extend([
                "--session-dir", a.session_dir,
                "--session", session_path,
            ])
        else:
            cmd.append("--no-session")

        if a.prompt_mode == "arg":
            return [*cmd, prompt], None
        return cmd, prompt  # stdin

    def _session_path_for(self, ctx: "TaskContext") -> str:
        """Resolve the session FILE PATH for this trigger.

        OWUI passes the chat_id as `session_id`; CLI may pass nothing
        and we fall back to the per-channel default. The path is
        `<session_dir>/<safe-id>.jsonl`. Returning a path (not a bare
        id) is what tells pi's resolveSessionPath to treat the arg as
        a file and create-or-open it (`SessionManager.open(path)`) —
        bare-id mode does an id-LOOKUP which fails on a fresh chat
        with `No session found matching <id>`.
        """
        st = ctx.state
        raw = (st.session_id or "").strip()
        if not raw:
            raw = self.cfg.agent.default_session_ids.get(
                st.channel, f"{st.channel}-default"
            )
        # Path-safety: pi will use this as a filename component, so
        # restrict to a conservative whitelist. Anything else maps to
        # `_`. We DON'T hash — operator-readable session dirs are
        # friendlier for debugging ("which chat is this session for?").
        import os as _os
        import re as _re
        safe = _re.sub(r"[^A-Za-z0-9._-]+", "_", raw)[:128]
        return _os.path.join(self.cfg.agent.session_dir, f"{safe}.jsonl")

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
        # FLAIL GUARD (opt-in per task; never on a plan-only turn — those are read-only by
        # design): a coding turn stuck reading without editing is killed and marked, so the
        # bridge re-plans from a fresh session instead of burning the whole timeout.
        watcher = None
        if getattr(ctx.state, "flail_guard", False) and not getattr(ctx.state, "plan_only", False):
            watcher = _FlailWatcher(proc, pi_events, self.cfg.flail, ctx)
            watcher.start()
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
            if watcher is not None:
                watcher.stop()

        rc = proc.returncode
        ctx.state.answer = extract_answer(pi_events)
        if ctx.state.signal == "flail_guard":
            # The marker rides the ANSWER so it reaches the bridge through the normal task
            # surface (WorkResult.output) regardless of how the killed run's status lands.
            ctx.state.answer = (
                f"{FLAIL_MARKER} stopped the turn — {ctx.state.detail}. The approach wasn't "
                f"converging; a fresh plan is needed.\n\n{ctx.state.answer}"
            ).strip()
            self.journals.write(ctx.error("flail_guard", ctx.state.detail, tool="agent"))
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
