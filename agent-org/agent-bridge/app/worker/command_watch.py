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

_UNIT = {"": 1.0, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}
_DUR_RE = re.compile(r"^(\d+(?:\.\d+)?)([smhd]?)$")
_SHELL_TOOLS = {"bash", "shell", "sh"}
# builtins that return at once: they add nothing to how long a chain can run
_INSTANT = {"cd", "export", "set", "unset", "pwd", "echo", "true", "false", ":", "exit", "pushd", "popd",
            "source", ".", "umask", "alias", "test", "["}
# stream filters after a pipe: they end when their producer ends
_FILTERS = {"tail", "head", "grep", "egrep", "fgrep", "wc", "sort", "uniq", "tee", "cat", "awk", "sed",
            "cut", "tr", "less", "more", "xargs"}
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.+-]{1,40}$")
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split(cmd: str) -> list[tuple[list[str], str]]:
    """Split a command line into (tokens, operator-that-ended-it) at the top level, honouring quotes and
    backslashes, so text inside quotes (`grep 'timeout 5'`) is never read as a `timeout` wrapper."""
    out: list[tuple[list[str], str]] = []
    toks: list[str] = []
    cur: list[str] = []
    have = False
    i, n = 0, len(cmd)
    def end_tok():
        nonlocal cur, have
        if have:
            toks.append("".join(cur))
        cur, have = [], False
    while i < n:
        c = cmd[i]
        if c == "\\" and i + 1 < n:
            cur.append(cmd[i + 1]); have = True; i += 2; continue
        if c in "'\"":
            j = cmd.find(c, i + 1)
            j = n if j < 0 else j
            cur.append(cmd[i + 1:j]); have = True; i = j + 1; continue
        if c in (" ", "\t"):
            end_tok(); i += 1; continue
        if c == "&" and ((i > 0 and cmd[i - 1] in "<>") or (i + 1 < n and cmd[i + 1] == ">")):
            cur.append(c); have = True; i += 1; continue          # a redirection (2>&1, &>f), not an operator
        if c in (";", "&", "|", "\n"):
            end_tok()
            op = c
            if c in "&|" and i + 1 < n and cmd[i + 1] == c:
                op = c * 2; i += 1
            out.append((toks, op)); toks = []
            i += 1; continue
        cur.append(c); have = True; i += 1
    end_tok()
    out.append((toks, ""))
    return [(t, o) for t, o in out if t]


def _timeout_of(toks: list[str]) -> tuple[float | None, list[str]]:
    """If `toks` (assignments already dropped) is a `timeout` wrapper: (declared seconds or None when
    it declares none/0/unparseable, the wrapped command's tokens). Else (None, toks)."""
    if not toks or toks[0].replace("\\", "/").rsplit("/", 1)[-1] != "timeout":
        return None, toks
    i = 1
    while i < len(toks) and toks[i].startswith("-") and toks[i] != "-":
        t = toks[i]
        if t in ("-k", "-s", "--kill-after", "--signal"):
            i += 2
        else:                                  # -k5, -sKILL, --kill-after=5, --foreground, -v ...
            i += 1
    m = _DUR_RE.match(toks[i]) if i < len(toks) else None
    if not m:
        return None, toks[i + 1:]
    secs = float(m.group(1)) * _UNIT[m.group(2)]
    return (secs if secs > 0 else None), toks[i + 1:]


def parse_command(cmd: str) -> tuple[list[list[float | None]], str]:
    """(plan, program) for a shell command line. `plan` is a list of sequential groups (`;`, `&&`, `||`,
    newline); each group is its pipeline stages' budgets: a positive number = that stage's own `timeout`,
    None = runs untimed (the caller applies its default), 0.0 = returns at once / a stream filter.
    `program` is the first real program's basename, only if it is a plain name (never arguments)."""
    groups: list[list[float | None]] = []
    program = ""
    stage_idx = 0
    for toks, op in _split(cmd):
        k = 0
        while k < len(toks) and _ASSIGN.match(toks[k]):
            k += 1
        toks = toks[k:]
        declared, inner = _timeout_of(toks)
        real = inner if toks and toks[0].replace("\\", "/").rsplit("/", 1)[-1] == "timeout" else toks
        while real and _ASSIGN.match(real[0]):
            real = real[1:]
        name = real[0].replace("\\", "/").rsplit("/", 1)[-1] if real else ""
        if not groups or stage_idx == 0:
            groups.append([])
        if name in _INSTANT or (stage_idx > 0 and name in _FILTERS):
            groups[-1].append(0.0)
        else:
            groups[-1].append(declared)
        if not program and name and name not in _INSTANT and _SAFE_NAME.match(name):
            program = name
        stage_idx = stage_idx + 1 if op == "|" else 0
    return groups, program


def budget_s(plan: list[list[float | None]], default_s: float) -> tuple[float, bool]:
    """(seconds this command may run, whether any part of it declared a timeout)."""
    total, declared = 0.0, False
    for g in plan:
        stage = [default_s if x is None else x for x in g]
        declared = declared or any(x for x in g if x)
        total += max(stage) if stage else 0.0
    return (total if total > 0 else default_s), declared


@dataclass
class OpenCommand:
    """A running command. The raw command line is NEVER kept: the daemon's pi event stream is not
    redacted, and anything stored here can reach an audit payload. Only the parsed budget plan and the
    program's plain name survive."""
    call_id: str
    plan: list[list[float | None]]
    program: str
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
                    cmd = args.get("command")
                    plan, program = parse_command(cmd if isinstance(cmd, str) else "")
                    t = args.get("timeout")
                    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
                        plan = [[float(t)]]            # the tool's own timeout argument governs
                    self.open[cid] = OpenCommand(cid, plan, program, now)
            elif kind == "tool_execution_end":
                self.open.pop(cid, None)


def judge_frozen(watch: CommandWatch | None, silent_s: float, now: datetime, *, silence_s: float,
                 default_timeout_s: float, grace_s: float, ceiling_s: float) -> tuple[bool, dict]:
    """A worker whose event offset is frozen: (hung?, audit info). EVERY open command is judged (a newer
    hung one is not protected by an older one's budget). `info["rule"]`: `command_running` (alive),
    `silent` (nothing running, silent for `silence_s`), `command_overran` (past its own timeout +
    grace) or `command_ceiling` (a command held the turn for the absolute ceiling). The info carries
    only non-sensitive facts: program name, seconds, rule."""
    cmds = list(watch.open.values()) if watch is not None else []
    if not cmds:
        if silent_s >= silence_s:
            return True, {"rule": "silent", "silent_s": int(silent_s)}
        return False, {"rule": "silent_grace"}
    verdict: tuple[bool, dict] | None = None
    for cmd in sorted(cmds, key=lambda c: c.seen_at):
        ran = (now - cmd.seen_at).total_seconds()
        budget, declared = budget_s(cmd.plan, default_timeout_s)
        info = {"program": cmd.program, "ran_s": int(ran), "timeout_s": int(budget), "declared": declared}
        if ceiling_s > 0 and ran >= ceiling_s:
            return True, {**info, "rule": "command_ceiling", "ceiling_s": int(ceiling_s)}
        if ran >= budget + grace_s:
            return True, {**info, "rule": "command_overran", "grace_s": int(grace_s)}
        verdict = verdict or (False, {**info, "rule": "command_running"})
    return verdict
