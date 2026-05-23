"""ot-exec — run a command in the open-terminal workspace plane.

little-coder's command-execution tool is wired (via the pi extension in
pi-extension/) to call `ot-exec` instead of a local shell, so every
build / test / git command runs in the network-isolated plane and passes
through the git-proxy (design §1.5, §3.3, §3.4). ot-exec is a drop-in
`bash -c` replacement:  `ot-exec -c "<command>"`.

When `LC_EVENT_STREAM` is set, ot-exec appends one JSON event per command.
That stream is the journaling seam: the AgentRunner turns it into
`tool_call` / `error` records (design §4) — no need to parse the agent's
free-text output.

Environment:
  LC_OPEN_TERMINAL_URL / LC_OPEN_TERMINAL_KEY  open-terminal endpoint + key
  LC_WORKSPACE        working directory for the command (default /workspace)
  LC_EXEC_TIMEOUT     per-command timeout, seconds
  LC_EVENT_STREAM     optional path to append command events to
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import git_artifact_filter as artifact_filter
from .journals import utc_now
from .openterminal import OpenTerminalClient, OpenTerminalError


def _emit_filter_denial(command: str, decision: artifact_filter.Decision) -> None:
    """Journal a workspace-edge denial via the existing event stream so the
    daemon sees it through the same path as a git-proxy denial.

    Symmetric with `_emit_event`, but no real `ExecResult` exists — the
    command never reached open-terminal. `git_proxy_denied: True` flips the
    activity record's `denied` flag and the daemon writes a `git_blocked`
    error (design §3.3, plan open item #9)."""
    path = os.environ.get("LC_EVENT_STREAM")
    if not path:
        return
    line = (
        f"{artifact_filter.DENY_MARKER} (ot-exec:{decision.rule}) "
        f"— {decision.reason}"
    )
    event = {
        "ts": utc_now(),
        "kind": "command",
        "command": command,
        "exit_code": artifact_filter.EXIT_DENIED,
        "status": "done",
        "duration_ms": 0,
        "timed_out": False,
        "git_proxy_denied": True,
        "stderr_tail": line,
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass  # journaling is best-effort; the stderr marker is the primary signal


def _emit_event(command: str, result, duration_ms: int) -> None:
    path = os.environ.get("LC_EVENT_STREAM")
    if not path:
        return
    event = {
        "ts": utc_now(),
        "kind": "command",
        "command": command,
        "exit_code": result.exit_code,
        "status": result.status,
        "duration_ms": duration_ms,
        "timed_out": result.timed_out,
        "git_proxy_denied": result.git_proxy_denied,
        "stderr_tail": result.stderr[-2000:],
    }
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass  # journaling is best-effort; the command result still stands


def _parse_args(argv: list[str]) -> str:
    """bash-compatible: `ot-exec -c "<cmd>"` or `ot-exec <cmd...>`."""
    if not argv:
        return ""
    if argv[0] == "-c":
        return argv[1] if len(argv) > 1 else ""
    return " ".join(argv)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = _parse_args(argv)
    if not command.strip():
        sys.stderr.write("ot-exec: no command given\n")
        return 2

    # Workspace-edge artifact filter — symmetric with the git-proxy
    # (design §3.3, plan open item #9). Blocks the obvious bash bypass
    # paths to `.git/config` / `.git/hooks/` / `.git/info/` before the
    # command crosses into open-terminal. Pure-classify; see module
    # docstring for the residual root-bypass.
    decision = artifact_filter.classify(command)
    if decision.action == "deny":
        marker = (
            f"{artifact_filter.DENY_MARKER} (ot-exec:{decision.rule}) "
            f"— {decision.reason}"
        )
        sys.stderr.write(marker + "\n")
        _emit_filter_denial(command, decision)
        return artifact_filter.EXIT_DENIED

    client = OpenTerminalClient(
        base_url=os.environ.get("LC_OPEN_TERMINAL_URL", "http://open-terminal:8000"),
        api_key=os.environ.get("LC_OPEN_TERMINAL_KEY", ""),
        default_cwd=os.environ.get("LC_WORKSPACE", "/workspace"),
        default_timeout=int(os.environ.get("LC_EXEC_TIMEOUT", "1800")),
    )
    started = time.monotonic()
    # umask 000: the workspace volume is shared by two containers with
    # different uids, so files the agent creates must be group/other-writable
    # for the other plane to use them (integration-tasks Decision Log).
    try:
        result = client.execute(f"umask 000; {command}")
    except OpenTerminalError as exc:
        sys.stderr.write(f"ot-exec: open-terminal unreachable: {exc}\n")
        return 125
    duration_ms = int((time.monotonic() - started) * 1000)
    _emit_event(command, result, duration_ms)

    if result.stdout:
        sys.stdout.write(result.stdout)
        if not result.stdout.endswith("\n"):
            sys.stdout.write("\n")
    if result.stderr:
        sys.stderr.write(result.stderr)
        if not result.stderr.endswith("\n"):
            sys.stderr.write("\n")

    if result.timed_out:
        return 124
    return result.exit_code if result.exit_code is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
