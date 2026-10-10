"""ao-silence: what the stall sweep can see of a worker's RUNNING COMMAND.

The sweep's liveness signal is the daemon's per-agent-step event offset. One long `bash` call emits
no agent-loop events while it runs, so a legitimate 9-minute test run under `timeout 900` looked
frozen and the turn was cancelled at `worker_silence_s` (C7, 2026-10-10: 4 turns). The daemon's
`/tasks/{id}/events` stream does carry the pi `tool_execution_start` / `tool_execution_end` pair for
every call, so the bridge can tell "no tool running and silent" (hung) from "a command is executing
within its own timeout" (alive). This module folds that stream into the open commands and judges
them; it holds no clock of its own (the caller passes `now`) so it is trivially testable."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

# `timeout [flags] DURATION cmd`: flags with a value (-k/-s) must not be mistaken for the duration.
_TIMEOUT_RE = re.compile(
    r"(?<![\w./-])timeout\s+"
    r"(?:(?:-k\s+\S+|--kill-after[=\s]\S+|-s\s+\S+|--signal[=\s]\S+|--foreground|--preserve-status|-v|--verbose)\s+)*"
    r"(\d+(?:\.\d+)?)([smhd]?)(?![\w.])")
_UNIT = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_SHELL_TOOLS = {"bash", "shell", "sh"}


def declared_timeout_s(tool: str, args: dict) -> float | None:
    """The timeout the command declares for itself: the tool's own `timeout` argument (seconds), else
    the sum of the `timeout N` wrappers in the command line (a chain runs them one after another).
    None when it declares none (the caller applies its default)."""
    t = args.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        return float(t)
    cmd = args.get("command")
    if not isinstance(cmd, str):
        return None
    found = [float(n) * _UNIT[u] for n, u in _TIMEOUT_RE.findall(cmd)]
    return sum(found) if found else None


@dataclass
class OpenCommand:
    call_id: str
    command: str
    timeout_s: float | None            # declared; None = none declared
    seen_at: datetime                  # when the bridge first saw it start (a tick, so >= the true start)


@dataclass
class CommandWatch:
    """Per-worker fold of the pi event stream: the commands started and not yet ended."""
    task_id: str
    offset: int = 0
    open: dict[str, OpenCommand] = field(default_factory=dict)

    def feed(self, lines: list[str], now: datetime) -> None:
        for ln in lines:
            if '"tool_execution_' not in ln:
                continue
            try:
                ev = json.loads(ln)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            kind, cid = ev.get("type"), str(ev.get("toolCallId", ""))
            if not cid:
                continue
            if kind == "tool_execution_start":
                tool = str(ev.get("toolName", "")).lower()
                args = ev.get("args") if isinstance(ev.get("args"), dict) else {}
                # only shell-like calls can legitimately run long; read/grep/edit that never end are hangs
                if tool in _SHELL_TOOLS or "command" in args:
                    self.open[cid] = OpenCommand(cid, str(args.get("command", ""))[:300],
                                                 declared_timeout_s(tool, args), now)
            elif kind == "tool_execution_end":
                self.open.pop(cid, None)

    def oldest(self) -> OpenCommand | None:
        return min(self.open.values(), key=lambda c: c.seen_at) if self.open else None


def judge_frozen(watch: CommandWatch | None, silent_s: float, now: datetime, *, silence_s: float,
                 default_timeout_s: float, grace_s: float, ceiling_s: float) -> tuple[bool, dict]:
    """A worker whose event offset is frozen: (hung?, audit info). `info["rule"]` names the rule:
    `command_running` (alive: a command is executing inside its timeout + grace), `silent` (no
    command running, silent for `silence_s`), `command_overran` (past its own timeout + grace) or
    `command_ceiling` (a command held the turn for the absolute ceiling)."""
    cmd = watch.oldest() if watch is not None else None
    if cmd is None:
        if silent_s >= silence_s:
            return True, {"rule": "silent", "silent_s": int(silent_s)}
        return False, {"rule": "silent_grace"}
    ran = (now - cmd.seen_at).total_seconds()
    declared = cmd.timeout_s if cmd.timeout_s is not None else default_timeout_s
    info = {"command": cmd.command, "ran_s": int(ran), "timeout_s": int(declared),
            "declared": cmd.timeout_s is not None}
    if ceiling_s > 0 and ran >= ceiling_s:
        return True, {**info, "rule": "command_ceiling", "ceiling_s": int(ceiling_s)}
    if ran >= declared + grace_s:
        return True, {**info, "rule": "command_overran", "grace_s": int(grace_s)}
    return False, {**info, "rule": "command_running"}
