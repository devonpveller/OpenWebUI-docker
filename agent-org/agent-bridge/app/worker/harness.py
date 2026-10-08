"""WorkerHarness — the wake seam onto little-coder (TOOLING §2, PLAN §5.3).

Wake == resume `little-coder --session <thread_id>` on an assigned pool instance.
Concretely we drive the little-coder control daemon's HTTP API (verified surface,
`little-coder/src/littlecoder/daemon.py`):
    POST /tasks {prompt, channel, user_id, session_id, acceptance_command} -> {task_id,status}
    GET  /tasks/{task_id} -> {status: running|done|abandoned|rejected, ...}

The bridge injects the worker's *context on wake* into the prompt: the current goal
(constraints inline), the floor/steering, and the plan doc (§4.2/§4.3). Bus-only comms
are preserved — the worker's replies come back through the bridge, not a side-channel.

`FakeHarness` returns a canned result immediately so P0-P2 loops are testable without
a live daemon or GPU.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

# Called with each worker update so the bridge can stream it to the chat bus (observability,
# governance §5/§7). kind ∈ {"command","answer"}; payload is the activity record / final answer.
OnUpdate = Callable[[str, dict], Awaitable[None]]

log = logging.getLogger("agent_bridge.worker")


class WorkResult:
    def __init__(self, status: str, task_id: str, output: str = "",
                 commands: list[str] | None = None, model: str | None = None) -> None:
        self.status = status          # done | abandoned | rejected | error
        self.task_id = task_id
        self.output = output
        # P18 F18 — WHAT THE TURN ACTUALLY RAN, not what it says it ran. The daemon already
        # reports this as `activity` and the harness already streams it to the bus for
        # observability; keeping it on the result lets a gate check a claim against the record.
        # gym-016 produced three turns that reported a suite result after running only `git log`,
        # including one that said "28 tests" moments after a command printed 31. Each was true by
        # luck; the org had no way to know that.
        self.commands: list[str] = commands or []
        # ef-worker-model TF1 — the model the daemon says the task RAN (`model` on GET /tasks/<id>,
        # e.g. "llamacpp/local-small"). None = the daemon did not report one (older than
        # ef-worker-model, or the task was never read back): UNKNOWN, never the model that was sent.
        self.model: str | None = model
        # ao-loopguard — set when the bridge's loop guard stopped this turn (status "flail").
        self.loop_trip: LoopTrip | None = None

    @property
    def ok(self) -> bool:
        return self.status == "done"


def _command_texts(activity: list) -> list[str]:
    """The command strings out of a daemon `activity` list (P18 F18).

    Shape-tolerant on purpose: the daemon's item schema is not ours, and a gate that reads this
    must degrade to "no commands recorded" rather than raise. An empty list therefore means
    "cannot tell", never "ran nothing" — every caller has to treat it that way."""
    out: list[str] = []
    for item in activity or []:
        cmd = _one_command_text(item)
        if cmd:
            out.append(cmd)
    return out


def _one_command_text(item) -> str:
    """The command string of a single daemon activity `item` (same shape-tolerance as
    `_command_texts`). "" when the item carries no command (e.g. a non-command activity line).

    ao-checks: prefers `command_full` (little-coder's whole, redacted command) over `command`, which
    the daemon cuts at 240 chars for display - the org reads these as DATA (claims, findings, flail
    keys), and a command cut mid-token is not what ran. An older daemon sends only `command`."""
    if not isinstance(item, dict):
        return ""
    return (item.get("command_full") or item.get("command") or "").strip()


# F31.4b (gym-038) — a scratch-file path under a temp dir, the volatile token a flailing lens varies.
_TEMP_PATH_RE = re.compile(r"(?:/tmp|/var/tmp|/dev/shm)/\S+")


def _flail_key(cmd: str) -> str:
    """F31.4b (gym-038) — the flail-comparison key: the command with temp scratch-file paths
    collapsed to `<tmp>`. The original F31.4 compared raw command strings, which a lens defeats by
    re-running the SAME probe against a fresh scratch file each time — gym-038's goal lens looped
    `TODO_DB=/tmp/todo_eval_60.json … repl`, `…_61.json …`, `…_62.json …` 20+ times; the changing
    path made each string distinct, so the consecutive-repeat counter reset every turn and never
    tripped. Collapsing the temp path makes "the same probe modulo a scratch file" compare equal, so
    the guard catches it. The normalisation is deliberately SURGICAL — only temp paths, not all
    numbers — so a genuinely varied sweep (different subcommands, flags, ids) is never false-flailed;
    requiring `max_repeat` (=6) CONSECUTIVE normalised-identical commands keeps the bar high on top."""
    return _TEMP_PATH_RE.sub("<tmp>", cmd)


_WS_RE = re.compile(r"\s+")
# A lens's finding line (the `echo 'FINDING: ...' >> <findings file>` the review prompts require).
_FINDING_RE = re.compile(r"\bFINDING:")
# ...and only when the command WRITES it (an append/redirect or tee). Round 2: a
# `grep -c 'FINDING:' /tmp/lens-findings.txt` read-back contains the text but writes nothing.
_WRITES_RE = re.compile(r">>|\btee\b|(?<![0-9&])>(?!&|\s*/dev/)")


def _writes_finding(key: str) -> bool:
    return bool(_FINDING_RE.search(key) and _WRITES_RE.search(key))


# FakeHarness only: the fake's model of the daemon's `workspace_marker` (the real daemon fingerprints
# the workspace files; a fake has no files, so it treats a write-shaped command as changing them).
_FAKE_WRITE_RE = re.compile(
    r"\bsed\s+(?:-[a-zA-Z]*\s+)*-i|\bcat\s*>|\btee\b|>\s*(?!/tmp/|/dev/)[\w./-]+\.\w+|"
    r"open\([^)]*['\"][wa]['\"]|write_text\(|\bgit\s+(?:commit|apply|am)\b|\bpatch\b")


def _loop_key(cmd: str) -> str:
    """ao-loopguard: the key the loop guard compares. `_flail_key` (temp scratch paths collapsed)
    plus whitespace runs collapsed, so the same probe re-typed with different spacing is one key.
    Deliberately NOT a digit or line-range normalisation: paging through a file with
    `sed -n '250,600p'`, `'600,800p'`, ... is reading, not looping (gym-002 01M4BVAD commands 6-11)."""
    return _WS_RE.sub(" ", _flail_key(cmd)).strip()


class LoopGuard:
    """ao-loopguard (gym-002, 2026-10-07): the bridge-side policy that stops a looping or
    unproductive worker turn. Replaces F31.4's "N CONSECUTIVE identical commands" rule, which a
    strict A/B alternation evades (01M4BVAD ran two commands 38 times each, max consecutive run 1)
    and which never sees a turn that explores without output (01M4BN82: 382 commands, 0 findings).

    Rules, each off at 0:
      - `identical_run`: N consecutive identical commands (the F31.4 rule, kept);
      - `window_repeats`: one command occurs N times in the last `window` commands (a near-repeat);
      - `window_distinct`: a FULL window of `window` commands holds <= N distinct commands
        (alternation / a short cycle);
      - review turns only (`kind="review"`): `first_finding_by` (no FINDING line in the first N
        commands) and `finding_gap` (N commands since the last FINDING line).
    PROGRESS resets the repetition window: a NEW finding line written by the turn (review) or a
    change the daemon reports - an edit/write tool call (`edits`) or a change to the workspace's
    files (`workspace_marker`, which also sees `sed -i` / heredoc / script writes) (work). So a
    turn that keeps producing findings or edits is never stopped by the window rules - the threat
    model is a false stop of a productive turn.
    Kinds: `review` (lens sweep, Mode B), `work` (coding/fix turns: needs the daemon's progress
    fields; the real harness keeps it inert on a daemon that reports neither), and `readonly`
    (change-nothing turns: QA, verify, plan - the router gives them the identical-run rule only,
    because re-running the product's own read command between inputs is how they work)."""

    __slots__ = ("kind", "identical_run", "window", "window_distinct", "window_repeats",
                 "first_finding_by", "finding_gap")

    def __init__(self, kind: str = "review", *, identical_run: int = 6, window: int = 12,
                 window_distinct: int = 3, window_repeats: int = 4, first_finding_by: int = 0,
                 finding_gap: int = 0) -> None:
        self.kind = kind
        self.identical_run = max(0, int(identical_run))
        self.window = max(0, int(window))
        self.window_distinct = max(0, int(window_distinct))
        self.window_repeats = max(0, int(window_repeats))
        self.first_finding_by = max(0, int(first_finding_by)) if kind == "review" else 0
        self.finding_gap = max(0, int(finding_gap)) if kind == "review" else 0

    def __repr__(self) -> str:   # shows up in test failure output
        return (f"LoopGuard({self.kind!r}, run={self.identical_run}, window={self.window}, "
                f"distinct<={self.window_distinct}, repeats>={self.window_repeats}, "
                f"first_finding_by={self.first_finding_by}, gap={self.finding_gap})")


class LoopTrip:
    """Which loop-guard rule stopped a turn, and why (carried on `WorkResult.loop_trip`)."""

    __slots__ = ("guard", "reason", "commands", "sample")

    def __init__(self, guard: str, reason: str, commands: int, sample: str = "") -> None:
        self.guard = guard          # identical_run | window_repeats | window_distinct | no_finding | finding_gap
        self.reason = reason        # one plain sentence for the operator
        self.commands = commands    # commands the turn had run when it was stopped
        self.sample = sample        # the repeated command (repeat rules), "" otherwise

    def as_dict(self) -> dict:
        return {"guard": self.guard, "reason": self.reason, "commands": self.commands,
                "sample": self.sample[:200]}


class LoopWatch:
    """Feeds one turn's commands through a `LoopGuard`. Pure; the real and fake harness share it."""

    def __init__(self, guard: LoopGuard) -> None:
        self.g = guard
        self.n = 0                       # commands seen
        self.findings = 0
        self.last_finding_at = 0
        self._seen_findings: set[str] = set()
        self._recent: list[str] = []
        self._run_key = ""
        self._run = 0

    def progress(self) -> None:
        """The turn made progress (a new finding, or an edit): the repetition window starts over."""
        self._recent.clear()
        self._run_key, self._run = "", 0

    def feed(self, cmd: str) -> LoopTrip | None:
        g = self.g
        self.n += 1
        key = _loop_key(cmd or "")
        if g.kind == "review" and key and _writes_finding(cmd or ""):
            # Progress only when the finding is NEW: a turn echoing the same line forever is a loop.
            if key not in self._seen_findings:
                self._seen_findings.add(key)
                self.findings += 1
                self.last_finding_at = self.n
                self.progress()
                return None
        if key:
            if key == self._run_key:
                self._run += 1
            else:
                self._run_key, self._run = key, 1
            if g.identical_run and self._run >= g.identical_run:
                return LoopTrip("identical_run",
                                f"it ran the same command {self._run} times in a row", self.n, key)
            if g.window:
                self._recent.append(key)
                if len(self._recent) > g.window:
                    del self._recent[0]
                if g.window_repeats:
                    c = self._recent.count(key)
                    if c >= g.window_repeats:
                        return LoopTrip(
                            "window_repeats",
                            f"it ran the same command {c} times in its last "
                            f"{len(self._recent)} commands", self.n, key)
                if g.window_distinct and len(self._recent) >= g.window:
                    d = len(set(self._recent))
                    if d <= g.window_distinct:
                        return LoopTrip(
                            "window_distinct",
                            f"its last {g.window} commands cycled through only {d} distinct "
                            f"commands", self.n, key)
        if g.first_finding_by and not self.findings and self.n >= g.first_finding_by:
            return LoopTrip("no_finding",
                            f"{self.n} commands without writing a single FINDING", self.n)
        if g.finding_gap and self.findings and self.n - self.last_finding_at >= g.finding_gap:
            return LoopTrip("finding_gap",
                            f"{self.n - self.last_finding_at} commands since its last FINDING "
                            f"({self.findings} written)", self.n)
        return None


def _guard_for(loop_guard: LoopGuard | None, max_repeat: int) -> LoopGuard | None:
    """The effective guard: an explicit `loop_guard`, else F31.4's bare `max_repeat` (identical-run
    only, for callers that predate ao-loopguard), else None."""
    if loop_guard is not None:
        return loop_guard
    if max_repeat:
        return LoopGuard("review", identical_run=max_repeat, window=0)
    return None


def _ran_model(task: dict) -> str | None:
    """The model a daemon task view says ran (TF1), or None when it reports none (an older daemon)."""
    m = task.get("model") if isinstance(task, dict) else None
    return m if isinstance(m, str) and m else None


#: `set_project`'s success `detail` when the daemon reports `upstream_mismatch` (F3).
UPSTREAM_MISMATCH = "upstream_mismatch"


def _upstream_status(d: dict) -> tuple[bool, bool]:
    """(usable, mismatch) for a fork's `upstream` from a successful /project response (F3).

    The daemon reports the remote in one of three ways:
      - `upstream_ok`: it baked the remote (a clone, a switch, or a NOOP whose remote was missing);
      - `upstream_reauthed` (+ `upstream_mismatch`): a NOOP whose remote was already there, and it
        re-stored the remote's credential (ef-lc-upstream). False = that re-store failed, so a
        private parent's fetch will fail: a real failure;
      - neither, on a NOOP: the remote was already there and nothing needed doing (no
        upstream_token, or a daemon older than ef-lc-upstream). Usable: reading this as a failed
        bake was the spurious "didn't bake" warning on every same-effort reuse of a fork.
    Neither on a clone or a switch is not a shape the daemon produces: treated as a failed bake."""
    if "upstream_ok" in d:
        return bool(d["upstream_ok"]), False
    if "upstream_reauthed" in d:
        ok = bool(d["upstream_reauthed"])
        return ok, ok and bool(d.get("upstream_mismatch"))
    return d.get("action") == "noop", False


# little-coder's daemon validates `channel` against a fixed trigger-surface enum
# (batch/cli/owui/validation) — it is NOT the chat channel. The bridge is an automated
# trigger, so it uses "batch".
LC_TRIGGER_CHANNEL = "batch"


class WorkerHarness(Protocol):
    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False, max_repeat: int = 0,
        model: str | None = None, loop_guard: LoopGuard | None = None,
    ) -> WorkResult:
        """Resume a session and run one turn to completion; return the result. `on_update`
        streams the worker's commands + answer to the bus as it works (observability).
        `plan_only` runs the turn with edit/write tools EXCLUDED (headless plan mode) —
        the worker can explore and reply with a plan but cannot change a file.
        `flail_guard` arms the daemon's read-without-edit watchdog on this turn: a flailing
        turn is killed with a FLAIL-GUARD answer marker instead of burning the timeout.
        `model` (ef-worker-model) is the model role this turn runs on: the dispatching profile's
        model. The daemon runs it for this task only, or refuses it (422) when its allowlist does
        not name it; None = the daemon's own agent.model.
        `loop_guard` (ao-loopguard) is the bridge-side loop/no-progress policy for this turn (see
        `LoopGuard`); a tripped guard cancels the task and returns status "flail" with
        `WorkResult.loop_trip` set. `max_repeat` is its F31.4 predecessor (identical run only)."""
        ...

    async def set_project(
        self, base_url: str, repo: str, *, token: str | None = None,
        upstream: str | None = None, upstream_token: str | None = None,
        fresh: bool = False, recurse_submodules: bool = False,
    ) -> tuple[bool, str, bool | None]:
        """Focus the worker on a repo (little-coder clones it, bypassing the git-proxy). `token` is
        an optional per-project deploy token (multi-PAT). `upstream` (+ optional read-scoped
        `upstream_token`) bakes a fork's read-only parent remote after the clone. Returns
        (ok, detail, upstream_ok):
          - ok/detail: clone success + the daemon's clone error (e.g. "clone failed (exit 128)") so a
            clone failure is surfaced as a clear CLONE problem, not a phantom worker failure. On
            success `detail` is "" or UPSTREAM_MISMATCH (the workspace's `upstream` remote points at
            another URL than the one requested; its credential was stored for the remote's URL).
          - upstream_ok: whether the fork's `upstream` remote is usable (None when no upstream was
            requested). A clone can succeed while the upstream bake fails (unreachable parent) — that
            must be surfaced, not silently swallowed, or `git fetch upstream` fails mid-task. A
            NOOP re-focus whose remote was already there is usable (F3: not a failed bake)."""
        ...

    async def current_focus(self, base_url: str) -> str | None:
        """The repo the worker is currently focused on, or None."""
        ...

    async def add_submodule(
        self, base_url: str, url: str, path: str, *, token: str | None = None,
    ) -> tuple[bool, str]:
        """Add `url` as a submodule at `path` in the worker's currently-focused (composition) repo
        (operator-plane git — P-APL.1b). Returns (ok, detail). The worker must already be focused on
        the composition repo (its origin carries the push token)."""
        ...

    async def run_check(
        self, base_url: str, command: str, *, cwd: str | None = None, timeout: int = 600,
    ) -> tuple[int | None, str, bool]:
        """Deterministic VERIFICATION exec on the daemon (`/check`, 2026-07-08): one command,
        REAL exit code + combined output back — no model in the loop (an LLM 'verifier' burned
        its turn re-running builds and never reported). Returns (exit_code, output, timed_out).
        Raises on transport errors / a daemon without the route — the caller falls back."""
        ...

    async def cancel_task(self, base_url: str, task_id: str) -> bool:
        """Abandon a task the bridge stopped waiting for (poll timeout), so the daemon doesn't
        stay busy on an orphaned turn and 409 the next dispatch. Best-effort."""
        ...

    async def has_running_task(self, base_url: str) -> bool:
        """GROUND TRUTH from the daemon: is a task RUNNING right now? The bridge's in-memory
        'executing' markers die on restart (live 2026-07-11: a redeploy mid-task made the stall
        watchdog re-engage an effort whose worker was still working — the daemon 409'd it). The
        daemon's own task list survives, so restart-safe decisions ask IT. False on any error
        (fail-open: an unreachable daemon shouldn't freeze recovery forever)."""
        ...

    async def running_task_progress(
        self, base_url: str, since_offset: int = 0, since_task_id: str | None = None,
    ) -> tuple[str, int] | None:
        """LIVENESS (register #25): `(task_id, event_offset)` for the daemon's running task, else None.
        The offset advances on every agent-loop step, so a FROZEN offset across ticks = a hung turn —
        unlike `has_running_task`, which a hang holds True forever. None on any error / no running task."""
        ...


class LittleCoderHarness:
    """Drives the little-coder control daemon. One instance addresses many daemons
    (the pool) by their `base_url` — the scheduler owns which base_url is free."""

    def __init__(self, poll_interval_s: float = 3.0, poll_timeout_s: float = 1800.0, *,
                 daemon_token: str = "", transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.poll_interval = poll_interval_s
        self.poll_timeout = poll_timeout_s
        # ao-dauth: every daemon route but GET /health needs `Authorization: Bearer
        # <LC_DAEMON_TOKEN>`. Unset here -> no header -> the daemon refuses (fail closed).
        tok = (daemon_token or "").strip()
        self._headers = {"Authorization": f"Bearer {tok}"} if tok else {}
        self._transport = transport   # tests only

    def _client(self, base_url: str, timeout: float) -> httpx.AsyncClient:
        extra = {"transport": self._transport} if self._transport is not None else {}
        return httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout,
                                 headers=self._headers, **extra)

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False, max_repeat: int = 0,
        model: str | None = None, loop_guard: LoopGuard | None = None,
    ) -> WorkResult:
        """`max_repeat` (F31.4) — a BRIDGE-SIDE flail-guard for READ-ONLY turns (the lens sweep).
        The daemon's `flail_guard` keys on read-*without-edit*, so it can't police a lens, which
        never edits by design; a lens stuck repeating one command evades it, the offset-silence
        watchdog (its offset keeps advancing), and lens truncation (repeats don't grow the findings
        file), looping to the turn deadline (gym-035). When `max_repeat > 0`, this poll loop stops
        the turn after that many CONSECUTIVE identical commands; the caller salvages whatever
        findings streamed before the flail. 0 = off (every non-lens wake is unaffected).

        `loop_guard` (ao-loopguard) supersedes it: a sliding-window repeat/alternation rule plus,
        on review turns, a no-finding rule (see `LoopGuard`). A `kind="work"` guard counts the
        daemon's reported `edits` as progress and stays INERT on a daemon that reports none, so
        an edit-then-test coding loop is never mistaken for a repeat."""
        async with self._client(base_url, 60.0) as c:
            body = {
                "prompt": prompt,
                "channel": channel,
                "user_id": "agent-bridge",
                "session_id": session_id,
            }
            if plan_only:
                # Only sent when set, so an older daemon (no `plan_only` field) is untouched
                # by normal wakes and merely ignores the extra key on plan wakes.
                body["plan_only"] = True
            if flail_guard:
                body["flail_guard"] = True
            if model:
                # Same rule as plan_only: sent only when set. A daemon older than ef-worker-model
                # ignores the key and runs its agent.model, exactly as before this change.
                body["model"] = model
            r = await c.post("/tasks", json=body)
            r.raise_for_status()
            task_id = r.json()["task_id"]
            # Poll to terminal state (little-coder is async; the scheduler treats this whole
            # call as the agent's `computing` window — machine B §3.6). Stream each new command
            # the worker runs to the bus as it happens (observability — governance §5/§7).
            seen = 0
            waited = 0.0
            ran: str | None = None   # TF1 — the model the daemon reports for this task
            guard = _guard_for(loop_guard, max_repeat)
            watch = LoopWatch(guard) if guard else None
            edits_seen: int | None = None   # ao-loopguard — the daemon's edit count at the last poll
            marker_seen: str | None = None  # ...and its workspace fingerprint
            while waited < self.poll_timeout:
                await asyncio.sleep(self.poll_interval)
                waited += self.poll_interval
                s = (await c.get(f"/tasks/{task_id}")).json()
                ran = _ran_model(s)
                activity = s.get("activity") or []
                # A work-kind guard can only tell a loop from edit-then-test progress while the daemon
                # reports its `workspace_marker`: the edit-tool count alone is blind to `sed -i` /
                # heredoc / script edits (round 3: a daemon that reported `edits` but omitted the
                # marker - >20k files - had a bash-editing turn stopped). Without a marker this poll
                # the guard is inert (never a blind false stop). A snapshot that is already terminal
                # is not stopped: the turn ended on its own and its answer is the deliverable.
                has_signal = s.get("workspace_marker") is not None
                watching = (watch is not None and (guard.kind != "work" or has_signal)
                            and s.get("status", "") in ("queued", "running", "pending", ""))
                if watching and guard.kind == "work":
                    try:
                        edits_now = int(s.get("edits") or 0)
                    except (TypeError, ValueError):
                        edits_now = 0
                    marker_now = s.get("workspace_marker")
                    if ((edits_seen is not None and edits_now > edits_seen)
                            or (marker_seen is not None and marker_now is not None
                                and marker_now != marker_seen)):
                        watch.progress()          # the workspace changed since the last poll
                    edits_seen = edits_now
                    if marker_now is not None:
                        marker_seen = marker_now
                if len(activity) > seen:
                    for item in activity[seen:]:
                        if on_update:
                            try:
                                await on_update("command", item)
                            except Exception:  # noqa: BLE001 - streaming must never break the poll
                                pass
                        # F31.4 / ao-loopguard — a turn looping (one command, or a short cycle) or
                        # a review turn producing nothing: stop it (findings so far are salvaged by
                        # the caller from the streamed command list).
                        trip = watch.feed(_one_command_text(item)) if watching else None
                        if trip is not None:
                            try:
                                await self.cancel_task(base_url, task_id)
                            except Exception:  # noqa: BLE001 - cancel is best-effort
                                pass
                            answer = s.get("answer") or s.get("result", "") or ""
                            if not answer and guard.kind == "work":
                                answer = f"LOOP-GUARD: stopped after {trip.commands} commands - {trip.reason}"
                            res = WorkResult("flail", task_id, answer,
                                             commands=_command_texts(activity), model=_ran_model(s))
                            res.loop_trip = trip
                            return res
                    seen = len(activity)
                status = s.get("status", "")
                # Terminal = anything not still in-flight (done/abandoned/rejected/cancelled/…).
                if status not in ("queued", "running", "pending", ""):
                    answer = s.get("answer") or s.get("result", "")
                    if on_update:
                        try:
                            await on_update("answer", {"status": status, "answer": answer})
                        except Exception:  # noqa: BLE001
                            pass
                    return WorkResult(status, task_id, answer,
                                      commands=_command_texts(activity), model=_ran_model(s))
            return WorkResult("error", task_id, "poll timeout", model=ran)

    async def set_project(
        self, base_url: str, repo: str, *, token: str | None = None,
        upstream: str | None = None, upstream_token: str | None = None,
        fresh: bool = False, recurse_submodules: bool = False,
    ) -> tuple[bool, str, bool | None]:
        # little-coder clones via the REAL git binary (bypasses the git-proxy) — the
        # supported "operator action" workspace-setup path (§12.3). Clone can be slow. A per-request
        # `token` (if given) overrides the pool's global LC_DEPLOY_TOKEN for this project. `upstream`
        # bakes a fork's read-only parent remote AFTER the clone (re-applied every focus, since the
        # workspace is wiped on switch). `recurse_submodules`: populate the full nested tree — a
        # composition build needs it and the worker can't init it (proxy denies `submodule`).
        body: dict[str, Any] = {"repo": repo, "actor": "agent-bridge"}
        if token:
            body["token"] = token
        if fresh:
            body["fresh"] = True
        if recurse_submodules:
            body["recurse_submodules"] = True
        if upstream:
            body["upstream"] = upstream
            if upstream_token:
                body["upstream_token"] = upstream_token
        async with self._client(base_url, 1800.0) as c:
            r = await c.post("/project", json=body)
            if r.status_code < 400:
                # Clone succeeded. None when no upstream was requested → nothing to warn.
                upstream_ok: bool | None = None
                note = ""
                if upstream:
                    try:
                        upstream_ok, mismatch = _upstream_status(r.json())
                    except Exception:  # noqa: BLE001 - non-JSON body → treat as bake-unknown/failed
                        upstream_ok, mismatch = False, False
                    note = UPSTREAM_MISMATCH if mismatch else ""
                return True, note, upstream_ok
            detail = ""
            try:
                detail = (r.json().get("detail") or "")
            except Exception:  # noqa: BLE001 - non-JSON body
                detail = r.text or ""
            return False, detail.strip()[:200], None

    async def current_focus(self, base_url: str) -> str | None:
        async with self._client(base_url, 30.0) as c:
            h = (await c.get("/health")).json()
            return h.get("focus")

    async def add_submodule(
        self, base_url: str, url: str, path: str, *, token: str | None = None,
    ) -> tuple[bool, str]:
        body: dict[str, Any] = {"url": url, "path": path, "actor": "agent-bridge"}
        if token:
            body["token"] = token
        async with self._client(base_url, 1800.0) as c:
            r = await c.post("/project/submodule", json=body)
            if r.status_code < 400:
                return True, ""
            detail = ""
            try:
                detail = (r.json().get("detail") or "")
            except Exception:  # noqa: BLE001 - non-JSON body
                detail = r.text or ""
            return False, detail.strip()[:200]

    async def run_check(
        self, base_url: str, command: str, *, cwd: str | None = None, timeout: int = 600,
    ) -> tuple[int | None, str, bool]:
        async with self._client(base_url, float(timeout) + 90.0) as c:
            r = await c.post("/check", json={"command": command, "cwd": cwd,
                                             "timeout": timeout, "actor": "agent-bridge"})
            r.raise_for_status()   # 404 = an old daemon without /check → the caller falls back
            d = r.json()
            return d.get("exit_code"), d.get("output") or "", bool(d.get("timed_out"))

    async def cancel_task(self, base_url: str, task_id: str) -> bool:
        """Abandon a task the bridge stopped waiting for (poll timeout) — otherwise the daemon
        stays busy running an ORPHANED turn and the next dispatch 409s (live 2026-07-08: both
        burn-down part turns outlived the poll window and would have zombie-blocked round 2)."""
        try:
            async with self._client(base_url, 30.0) as c:
                r = await c.post(f"/tasks/{task_id}/cancel")
                return r.status_code < 400
        except httpx.HTTPError:
            return False

    async def has_running_task(self, base_url: str) -> bool:
        """Restart-safe ground truth: does this daemon report a RUNNING task? (see Protocol doc)."""
        try:
            async with self._client(base_url, 15.0) as c:
                r = await c.get("/tasks")
                if r.status_code != 200:
                    return False
                tasks = (r.json() or {}).get("tasks", [])
                return any(t.get("status") == "running" for t in tasks)
        except (httpx.HTTPError, ValueError):
            return False

    async def running_task_progress(
        self, base_url: str, since_offset: int = 0, since_task_id: str | None = None,
    ) -> tuple[str, int] | None:
        """Worker-LIVENESS signal (register #25): `(task_id, event_offset)` for this daemon's running
        task, or `None` if it reports no running task. The offset is the daemon's per-agent-step event
        count (`/tasks/{id}/events` `next_offset`) — it advances on generation / tool / edit, unlike the
        shell-only `activity` array, so a FROZEN offset across ticks is the true signature of a hung
        turn (the stall sweep decides silence from the delta over time). `since_offset` is the last
        offset the caller saw, so the daemon returns only the new events; the returned offset is still
        the running total. `since_task_id` is the task that offset belongs to: an offset is only
        meaningful for ITS task, so when the daemon's running task is a different one the probe starts
        from 0 (ao-wd-offset: a new task used to inherit the previous task's offset, read as frozen,
        and was cancelled as "silent"). `since_task_id=None` keeps the legacy "trust the offset"."""
        try:
            async with self._client(base_url, 15.0) as c:
                r = await c.get("/tasks")
                if r.status_code != 200:
                    return None
                running = next((t for t in (r.json() or {}).get("tasks", [])
                                if t.get("status") == "running"), None)
                tid = running and running.get("task_id")
                if not tid:
                    return None
                if since_task_id is not None and tid != since_task_id:
                    since_offset = 0                # the offset belongs to another task
                e = await c.get(f"/tasks/{tid}/events", params={"offset": max(0, int(since_offset))})
                if e.status_code != 200:            # daemon without the /events route → offset unknown
                    # FAIL-SAFE: report as advancing so an unobservable-but-running daemon is never
                    # mistaken for hung. Never kill a worker whose progress you cannot actually watch.
                    return (tid, int(since_offset) + 1)
                off = int((e.json() or {}).get("next_offset", since_offset))
                return (tid, off)
        except (httpx.HTTPError, ValueError, TypeError):
            return None


#: FakeHarness `stream_commands` entry standing for a file edit (an edit/write tool call).
FAKE_EDIT = "<fake-edit>"


class FakeHarness:
    """Deterministic in-memory worker for tests. Records every wake."""

    def __init__(
        self, result_status: str = "done", *, stream_commands: list[str] | None = None,
        output: str = "ok",
    ) -> None:
        self.result_status = result_status
        self.output = output          # the WorkResult output; set a 503 marker to simulate a shed
        # Optional per-wake output sequence: each wake pops the next entry (falls back to `output`
        # when empty) — lets a test script distinct step/publish/check responses.
        self.output_queue: list[str] = []
        self.wakes: list[dict[str, Any]] = []
        # Deterministic /check results: each run_check pops (exit_code, output, timed_out).
        # EMPTY by default → run_check raises like a daemon without the route, so tests exercise
        # the LLM-verifier fallback unless they explicitly queue deterministic results.
        self.check_queue: list[tuple[int | None, str, bool]] = []
        self.checks: list[dict[str, Any]] = []        # every run_check call
        self.cancelled: list[tuple[str, str]] = []    # every cancel_task (base_url, task_id)
        self.projects: dict[str, str] = {}
        self.focus_calls: list[dict[str, Any]] = []   # every set_project (base_url, repo, token)
        self.tokens: dict[str, str] = {}
        self.upstreams: dict[str, str] = {}
        self.upstream_tokens: dict[str, str] = {}
        # Set to a non-empty error string to simulate a clone/set_project failure (e.g. a private
        # or missing repo the deploy token can't access → the daemon returns "clone failed").
        self.set_project_fails = ""
        # Set to an error string to fail the NEXT set_project ONCE then self-clear — simulates a
        # TRANSIENT verify-focus collision (a fresh focus succeeds on retry), so tests can exercise
        # the deterministic-check retry-before-LLM-fallback path.
        self.set_project_fail_once = ""
        # Set True to simulate a clone that SUCCEEDS but whose fork `upstream` bake FAILS (an
        # unreachable/private parent) → set_project returns (True, "", False) so the bridge warns.
        self.upstream_fails = False
        # Set True to simulate a NOOP re-focus whose existing `upstream` remote points at another
        # URL than the one requested (the daemon's `upstream_mismatch`, F3): set_project returns
        # (True, UPSTREAM_MISMATCH, True).
        self.upstream_mismatch = False
        # Submodules added via add_submodule: list of (base_url, url, path). `submodule_fails` (an
        # error string) simulates a failed submodule add.
        self.submodules: list[tuple[str, str, str]] = []
        self.submodule_fails = ""
        # Optional command lines to stream via on_update("command", ...) before the answer, so a
        # test can exercise the real activity-streaming path (Fix 1). Default None = no commands.
        self.stream_commands = stream_commands
        # Simulate a WEDGED worker (409 busy) or an UNREACHABLE one (transport error) by base_url, so
        # tests can exercise the quarantine + retry-elsewhere path. wake() raises for a matching url.
        self.busy_urls: set[str] = set()
        self.down_urls: set[str] = set()
        # Worker-liveness (register #25): a busy worker's per-step event offset. `running_task_progress`
        # returns (task_id, offset) for a busy_url; a test freezes the offset to simulate a HANG or bumps
        # it to simulate progress. Defaults: offset 0, task id "fake-task".
        self.progress_offsets: dict[str, int] = {}
        self.progress_task_ids: dict[str, str] = {}
        self.progress_totals: dict[str, int] = {}     # if set: emulate the daemon's line-count semantics
        self.progress_calls: list[tuple[str, str, int]] = []   # (url, task_id, effective since_offset)
        # Optional answer text streamed via on_update (default "ok") — set long text to exercise
        # the answer-chunking path.
        self.answer_text: str | None = None
        # ao-loopguard — whether this fake daemon reports its `workspace_marker` (a little-coder older
        # than ao-loopguard, or one whose workspace scan gave up, does not; a work guard is then inert).
        self.reports_edits = True
        # The daemon's answer text at the moment a non-work guard stops a turn (default: none yet).
        self.flail_answer = ""

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False, max_repeat: int = 0,
        model: str | None = None, loop_guard: LoopGuard | None = None,
    ) -> WorkResult:
        self.wakes.append(
            {"base_url": base_url, "session_id": session_id, "prompt": prompt,
             "plan_only": plan_only, "flail_guard": flail_guard, "max_repeat": max_repeat,
             "model": model, "loop_guard": loop_guard}
        )
        if base_url in self.busy_urls:
            req = httpx.Request("POST", base_url.rstrip("/") + "/tasks")
            raise httpx.HTTPStatusError(
                "409 Conflict", request=req, response=httpx.Response(409, request=req))
        if base_url in self.down_urls:
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", base_url))
        out = self.output_queue.pop(0) if self.output_queue else self.output
        stream = list(self.stream_commands or [])
        # ao-loopguard — a `FAKE_EDIT` entry is a file edit (an edit/write tool call), which the
        # daemon counts but never reports as a command; it is not streamed.
        cmds = [c for c in stream if c != FAKE_EDIT]
        # F31.4 / ao-loopguard — mirror the real harness's loop guard (the same `LoopWatch`) so
        # tests exercise it: stop at the tripping command and return a "flail" result.
        guard = _guard_for(loop_guard, max_repeat)
        if guard is not None and (guard.kind != "work" or self.reports_edits):
            watch = LoopWatch(guard)
            streamed: list[str] = []
            for cmd in stream:
                if cmd == FAKE_EDIT:
                    watch.progress()
                    continue
                streamed.append(cmd)
                trip = watch.feed(cmd)
                if trip is None and guard.kind == "work" and _FAKE_WRITE_RE.search(cmd):
                    watch.progress()            # the fake's workspace_marker moved
                if trip is None:
                    continue
                if on_update:
                    for c in streamed:
                        await on_update("command", {"command": c, "ok": True})
                answer = (self.flail_answer if guard.kind != "work" else
                          f"LOOP-GUARD: stopped after {trip.commands} commands - {trip.reason}")
                res = WorkResult("flail", task_id=f"fake-{len(self.wakes)}",
                                 output=answer, commands=streamed)
                res.loop_trip = trip
                return res
        if on_update:
            for cmd in cmds:
                await on_update("command", {"command": cmd, "ok": True})
            await on_update("answer", {"status": self.result_status,
                                       "answer": self.answer_text or "ok"})
        # Like a daemon that runs what it is sent: reports the sent model, or None (agent.model,
        # which a fake does not know) when none was sent.
        return WorkResult(self.result_status, task_id=f"fake-{len(self.wakes)}", output=out,
                          commands=cmds, model=model)

    async def set_project(
        self, base_url: str, repo: str, *, token: str | None = None,
        upstream: str | None = None, upstream_token: str | None = None,
        fresh: bool = False, recurse_submodules: bool = False,
    ) -> tuple[bool, str, bool | None]:
        if self.set_project_fail_once:  # transient collision: fail once, then self-heal on retry
            detail, self.set_project_fail_once = self.set_project_fail_once, ""
            return False, detail, None
        if self.set_project_fails:  # simulate a clone failure (private/missing repo)
            return False, self.set_project_fails, None
        self.focus_calls.append({"base_url": base_url, "repo": repo, "token": token,
                                 "fresh": fresh, "recurse_submodules": recurse_submodules})
        self.projects[base_url] = repo
        if token:
            self.tokens[base_url] = token
        upstream_ok: bool | None = None
        if upstream:
            self.upstreams[base_url] = upstream
            if upstream_token:
                self.upstream_tokens[base_url] = upstream_token
            upstream_ok = not self.upstream_fails  # clone ok, but the bake may have failed
            if upstream_ok and self.upstream_mismatch:
                return True, UPSTREAM_MISMATCH, True
        return True, "", upstream_ok

    async def current_focus(self, base_url: str) -> str | None:
        return self.projects.get(base_url)

    async def add_submodule(
        self, base_url: str, url: str, path: str, *, token: str | None = None,
    ) -> tuple[bool, str]:
        if self.submodule_fails:
            return False, self.submodule_fails
        self.submodules.append((base_url, url, path))
        return True, ""

    async def run_check(
        self, base_url: str, command: str, *, cwd: str | None = None, timeout: int = 600,
    ) -> tuple[int | None, str, bool]:
        self.checks.append({"base_url": base_url, "command": command, "cwd": cwd,
                            "timeout": timeout})
        if not self.check_queue:
            # like a daemon without the /check route — callers fall back to the LLM verifier
            raise RuntimeError("no /check on this daemon (fake: queue check_queue results)")
        return self.check_queue.pop(0)

    async def cancel_task(self, base_url: str, task_id: str) -> bool:
        self.cancelled.append((base_url, task_id))
        return True

    async def has_running_task(self, base_url: str) -> bool:
        # tests mark daemons busy via `busy_urls` (restart-safety: the stall sweep defers to them)
        return base_url in getattr(self, "busy_urls", set())

    async def running_task_progress(
        self, base_url: str, since_offset: int = 0, since_task_id: str | None = None,
    ) -> tuple[str, int] | None:
        # register #25: (task_id, event_offset) for a busy worker; None if idle. A test freezes the
        # offset to simulate a hang or bumps it to simulate progress.
        if base_url not in getattr(self, "busy_urls", set()):
            return None
        tid = self.progress_task_ids.get(base_url, "fake-task")
        if since_task_id is not None and tid != since_task_id:
            since_offset = 0                        # same rule as the real client
        self.progress_calls.append((base_url, tid, since_offset))
        total = self.progress_totals.get(base_url)
        if total is not None:                       # emulate the daemon: next_offset = offset + len(lines[offset:])
            return (tid, since_offset + max(0, total - since_offset))
        return (tid, self.progress_offsets.get(base_url, 0))
