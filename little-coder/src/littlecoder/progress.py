"""Progress meter for the flail guard (ao-progress, 2026-10-10).

Principle (agent-org rework, 00-PRINCIPLES 1): measure PROGRESS, not activity. The flail guard
used to kill a coding turn at a fixed 25 read-only tool calls. In the C7 bake-off that killed 28
of 32 coding turns before their first edit, while bare little-coder on the same model made its
first edit at calls 14-35 in its passing runs. A turn that is still discovering new code is not
flailing; a turn that re-reads what it already saw, or re-runs a command that prints what it
already printed, is.

So the guard asks one question per finished tool call: did it bring back anything NEW?

- A result line is "seen" once any earlier result in the turn held the same line after
  normalization (whitespace collapsed, every digit run read as one number, so a re-run whose only
  change is a timing or a line number is not news).
- A call is NOVEL when its result holds at least `min_new_lines` unseen lines.
- A SHORT result (at most SHORT_RESULT_LINES non-blank lines: an empty grep, a silent command,
  `pytest one_file | tail -3`) is novel when the call's arguments are new, and stale when the
  same arguments already ran: a short answer to a new question is still an answer (a regression
  sweep prints "N passed in X s" for every file), the same question asked again is not.
- A successful edit/write resets the window: the turn moved, so the count starts over.

It trips on either rule:

- `stale`: the last `stale_calls` finished calls brought back nothing new.
- `cap`: `tool_calls` calls ran since the last successful edit (or the turn start), novel or not -
  a generous backstop so "always slightly new" can never become "never stop".

This module is OURS (the Python control plane in little-coder/src/littlecoder), not upstream
little-coder (the npm package the image installs). It reads pi's `--mode json` event stream; it
never changes the agent's loop.

What leaves this module is NUMBERS ONLY (ao-silence lesson): the trip detail names the rule and
counts. No command, path, argument or result text is stored or returned - lines and arguments are
kept as fixed-size hashes, and every count read from the stream is bounded.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

EDIT_TOOLS = ("edit", "write")

# Bounds on what one result may contribute, so a hostile or huge result cannot grow memory
# without limit. A result past these limits is judged on its first part only.
MAX_RESULT_CHARS = 400_000
MAX_LINES_PER_RESULT = 8_000
MAX_SEEN = 400_000          # distinct line hashes kept per turn; past it, nothing new is added
MAX_PENDING = 4_096         # open tool calls tracked (start without end)
# A result of at most this many non-blank lines is a SHORT answer (see _is_novel).
SHORT_RESULT_LINES = 6

_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")


def _norm(line: str) -> str:
    return _WS.sub(" ", _DIGITS.sub("0", line)).strip()


def _key(text: str) -> bytes:
    return hashlib.blake2b(text.encode("utf-8", "replace"), digest_size=8).digest()


def _bounded_int(value: int, cap: int = 10**9) -> int:
    """Counts reported out of this module are ints in [0, cap] - never a parsed stream value."""
    return max(0, min(int(value), cap))


@dataclass(frozen=True)
class Trip:
    rule: str          # "stale" | "cap"
    detail: str        # numbers only


class ProgressMeter:
    """Feed it tool starts and ends; ask it whether the turn has stopped making progress."""

    def __init__(self, stale_calls: int, cap: int, min_new_lines: int = 1) -> None:
        self.stale_calls = max(1, int(stale_calls))
        self.cap = max(1, int(cap))
        self.min_new_lines = max(1, int(min_new_lines))
        self._seen_lines: set[bytes] = set()
        self._seen_args: set[bytes] = set()
        self._pending: dict[str, tuple[str, bytes]] = {}
        # counters (all ints, all bounded on the way out)
        self.calls = 0                # tool starts this turn
        self.ended = 0                # tool ends judged this turn
        self.edits = 0                # successful edit/write ends this turn
        self.novel = 0                # novel non-edit ends this turn
        self.since_edit = 0           # tool starts since the last successful edit (or turn start)
        self.stale_run = 0            # consecutive stale ends since the last novel end / edit
        self.max_stale_run = 0
        # The FIRST trip, latched the moment it happens: the watcher scans every few seconds,
        # and a stale run that a later call ends inside one scan must still count.
        self.trip: Trip | None = None
        self.trip_at_call = 0

    # -- feeding -------------------------------------------------------------------------------

    def start(self, call_id: str, tool: str, args) -> None:
        tool = str(tool or "").lower()[:64]
        self.calls += 1
        self.since_edit += 1
        if len(self._pending) < MAX_PENDING:
            self._pending[str(call_id)[:128]] = (tool, _args_key(tool, args))
        self._latch()

    def end(self, call_id: str, tool: str, text: str, is_error: bool) -> None:
        tool_s, args_key = self._pending.pop(str(call_id)[:128], (None, None))
        tool = (tool_s or str(tool or "")).lower()[:64]
        self.ended += 1
        if tool in EDIT_TOOLS and not is_error:
            # The turn changed the workspace: the window starts over.
            self.edits += 1
            self.since_edit = 0
            self.stale_run = 0
            return
        if self._is_novel(text, args_key if args_key is not None else _key(tool)):
            self.novel += 1
            self.stale_run = 0
        else:
            self.stale_run += 1
            self.max_stale_run = max(self.max_stale_run, self.stale_run)
        self._latch()

    def _latch(self) -> None:
        if self.trip is None:
            self.trip = self._judge()
            if self.trip is not None:
                self.trip_at_call = self.calls

    def _is_novel(self, text: str, args_key: bytes) -> bool:
        if not isinstance(text, str):
            text = ""
        lines = []
        for raw in text[:MAX_RESULT_CHARS].splitlines()[:MAX_LINES_PER_RESULT]:
            n = _norm(raw)
            if n:
                lines.append(_key(n))
        if len(lines) <= SHORT_RESULT_LINES:
            # A short answer to a NEW question is news even when its lines were seen before:
            # `pytest one_more_file | tail -3` prints the same "0 passed in 0.0s" line for every
            # file of a regression sweep. The same question asked again is not.
            asked = args_key in self._seen_args
            if not asked and len(self._seen_args) < MAX_SEEN:
                self._seen_args.add(args_key)
            if not asked:
                self._absorb(lines)
                return True
        return self._absorb(lines) >= self.min_new_lines

    def _absorb(self, lines: list[bytes]) -> int:
        new = 0
        for k in lines:
            if k not in self._seen_lines:
                new += 1
                if len(self._seen_lines) < MAX_SEEN:
                    self._seen_lines.add(k)
        return new

    # -- judging -------------------------------------------------------------------------------

    def numbers(self) -> dict[str, int]:
        return {
            "calls": _bounded_int(self.calls),
            "since_edit": _bounded_int(self.since_edit),
            "edits": _bounded_int(self.edits),
            "novel": _bounded_int(self.novel),
            "stale_run": _bounded_int(self.stale_run),
            "stale_calls": _bounded_int(self.stale_calls),
            "cap": _bounded_int(self.cap),
        }

    def tripped(self) -> Trip | None:
        """The first trip of this turn (latched), or None."""
        return self.trip

    @property
    def open_calls(self) -> int:
        """Tool calls started and not yet ended (as far as the stream shows). The model clock
        (ModelClock) pauses while this is above zero."""
        return len(self._pending)

    def _judge(self) -> Trip | None:
        n = self.numbers()
        if self.stale_run >= self.stale_calls:
            return Trip("stale", (
                f"rule=stale: {n['stale_run']} calls in a row brought back nothing new "
                f"(window {n['stale_calls']}, novel {n['novel']}/{n['calls']}, "
                f"edits {n['edits']}, since edit {n['since_edit']})"))
        if self.since_edit >= self.cap:
            return Trip("cap", (
                f"rule=cap: {n['since_edit']} calls since the last edit "
                f"(cap {n['cap']}, novel {n['novel']}/{n['calls']}, edits {n['edits']})"))
        return None


class ModelClock:
    """MODEL time of a turn for the flail guard's time rule (ao-flailtime, 2026-10-13).

    The time rule used to read wall time since the turn started, so the worker's own test runs
    counted against it: in the C7c bake-off it ended 5 of 12 coding turns before any edit, each
    with 658-727 s of its 900 s spent inside the worker's own agent-bridge suite runs.

    What is counted: every second of the turn during which NO tool call is open. That is the
    model's time - the request waiting for admission, prompt processing, thinking and streaming,
    pi's own provider retries and their backoff, a call that errors or is aborted, compaction -
    plus pi's small bookkeeping between calls. What is not counted: the time from a tool call's
    `tool_execution_start` to its `tool_execution_end` (bash, read, edit, ...); a parallel batch
    counts once (the union, not the sum). Counting "not a tool" rather than "inside a model
    request" is deliberate: anything the stream cannot show to be a tool counts toward the rule,
    so the error goes toward tripping, never toward a turn that thinks forever.

    How it is measured: the caller samples it on a short tick with its own monotonic clock
    (`advance(now, tool_open)`), then feeds the events appended since the last tick. The interval
    since the last sample is attributed to the state known at its start (a tool open or not), so
    each start or end costs at most one tick of error; a tool that starts and ends inside one
    tick is counted as model time (at most one tick). The event stream carries
    no timestamps of its own for tool start/end; the clock is ours, the stream only says whether
    a call is open.

    What it is NOT: a bound on a hung tool. A tool that never ends keeps the clock paused; command
    timeouts (`LC_EXEC_TIMEOUT`), the bridge's running-command watch (ao-silence) and the task
    timeout bound that. It also needs the tool's start to be written WHILE the tool runs: a tool
    that blocks pi's event loop (the pre-ao-asyncexec spawnSync bash) is only seen after it
    ended, and its time is then counted as model time, as before this change."""

    def __init__(self, now: float) -> None:
        self.started = float(now)
        self._last = float(now)
        self.model_s = 0.0
        self.tool_s = 0.0

    def advance(self, now: float, tool_open: bool) -> None:
        """Attribute the interval since the last sample to the model or to tools. `tool_open` is
        the state at the START of the interval (sample before feeding new events)."""
        now = float(now)
        dt = now - self._last
        if dt <= 0:
            return               # a clock that did not move (or went back) adds nothing
        if tool_open:
            self.tool_s += dt
        else:
            self.model_s += dt
        self._last = now

    def numbers(self) -> dict[str, int]:
        return {
            "model_s": _bounded_int(self.model_s),
            "tool_s": _bounded_int(self.tool_s),
            "wall_s": _bounded_int(self.model_s + self.tool_s),
        }


def _args_key(tool: str, args) -> bytes:
    try:
        body = json.dumps(args, sort_keys=True, default=str)[:20_000]
    except (TypeError, ValueError):
        body = ""
    return _key(tool + "\0" + _WS.sub(" ", body).strip())


def result_text(result) -> tuple[str, bool]:
    """(text, is_error) from a pi `tool_execution_end` result / a session toolResult message.
    Tolerates any shape; only `text` parts are read."""
    is_error = False
    content = result
    if isinstance(result, dict):
        is_error = bool(result.get("isError") or result.get("is_error"))
        content = result.get("content", "")
    if isinstance(content, str):
        return content, is_error
    parts = []
    if isinstance(content, list):
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str):
                parts.append(c["text"])
    return "\n".join(parts), is_error


class EventFeed:
    """Incrementally feed a live pi `--mode json` events file into a ProgressMeter.

    Reads only what was appended since the last poll; a partial trailing line (the agent is
    mid-write) is left for the next poll. A line that does not parse is skipped, as the old
    counter did."""

    def __init__(self, path: str, meter: ProgressMeter) -> None:
        self.path = path
        self.meter = meter
        self._offset = 0

    def poll(self) -> None:
        try:
            with open(self.path, "rb") as fh:
                fh.seek(self._offset)
                chunk = fh.read()
        except OSError:
            return
        if not chunk:
            return
        end = chunk.rfind(b"\n")
        if end < 0:
            return  # only a partial line so far
        self._offset += end + 1
        for raw in chunk[: end + 1].splitlines():
            feed_event(self.meter, raw)


def feed_event(meter: ProgressMeter, raw) -> None:
    """Apply one pi event line (bytes or str) to the meter. Never raises on bad input."""
    if isinstance(raw, bytes):
        if b'"tool_execution_' not in raw:
            return
        raw = raw.decode("utf-8", "replace")
    elif '"tool_execution_' not in raw:
        return
    try:
        ev = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return
    if not isinstance(ev, dict):
        return
    et = ev.get("type")
    if et == "tool_execution_start":
        meter.start(ev.get("toolCallId", ""), ev.get("toolName", ""), ev.get("args"))
    elif et == "tool_execution_end":
        text, err = result_text(ev.get("result"))
        meter.end(ev.get("toolCallId", ""), ev.get("toolName", ""), text,
                  bool(ev.get("isError")) or err)
