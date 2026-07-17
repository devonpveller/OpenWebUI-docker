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
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

import httpx

# Called with each worker update so the bridge can stream it to the chat bus (observability,
# governance §5/§7). kind ∈ {"command","answer"}; payload is the activity record / final answer.
OnUpdate = Callable[[str, dict], Awaitable[None]]

log = logging.getLogger("agent_bridge.worker")


class WorkResult:
    def __init__(self, status: str, task_id: str, output: str = "") -> None:
        self.status = status          # done | abandoned | rejected | error
        self.task_id = task_id
        self.output = output

    @property
    def ok(self) -> bool:
        return self.status == "done"


# little-coder's daemon validates `channel` against a fixed trigger-surface enum
# (batch/cli/owui/validation) — it is NOT the chat channel. The bridge is an automated
# trigger, so it uses "batch".
LC_TRIGGER_CHANNEL = "batch"


class WorkerHarness(Protocol):
    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False,
    ) -> WorkResult:
        """Resume a session and run one turn to completion; return the result. `on_update`
        streams the worker's commands + answer to the bus as it works (observability).
        `plan_only` runs the turn with edit/write tools EXCLUDED (headless plan mode) —
        the worker can explore and reply with a plan but cannot change a file.
        `flail_guard` arms the daemon's read-without-edit watchdog on this turn: a flailing
        turn is killed with a FLAIL-GUARD answer marker instead of burning the timeout."""
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
            clone failure is surfaced as a clear CLONE problem, not a phantom worker failure.
          - upstream_ok: whether the fork's `upstream` remote baked (None when no upstream was
            requested). A clone can succeed while the upstream bake fails (unreachable parent) — that
            must be surfaced, not silently swallowed, or `git fetch upstream` fails mid-task."""
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
        self, base_url: str, since_offset: int = 0,
    ) -> tuple[str, int] | None:
        """LIVENESS (register #25): `(task_id, event_offset)` for the daemon's running task, else None.
        The offset advances on every agent-loop step, so a FROZEN offset across ticks = a hung turn —
        unlike `has_running_task`, which a hang holds True forever. None on any error / no running task."""
        ...


class LittleCoderHarness:
    """Drives the little-coder control daemon. One instance addresses many daemons
    (the pool) by their `base_url` — the scheduler owns which base_url is free."""

    def __init__(self, poll_interval_s: float = 3.0, poll_timeout_s: float = 1800.0) -> None:
        self.poll_interval = poll_interval_s
        self.poll_timeout = poll_timeout_s

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False,
    ) -> WorkResult:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0) as c:
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
            r = await c.post("/tasks", json=body)
            r.raise_for_status()
            task_id = r.json()["task_id"]
            # Poll to terminal state (little-coder is async; the scheduler treats this whole
            # call as the agent's `computing` window — machine B §3.6). Stream each new command
            # the worker runs to the bus as it happens (observability — governance §5/§7).
            seen = 0
            waited = 0.0
            while waited < self.poll_timeout:
                await asyncio.sleep(self.poll_interval)
                waited += self.poll_interval
                s = (await c.get(f"/tasks/{task_id}")).json()
                activity = s.get("activity") or []
                if on_update and len(activity) > seen:
                    for item in activity[seen:]:
                        try:
                            await on_update("command", item)
                        except Exception:  # noqa: BLE001 - streaming must never break the poll
                            pass
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
                    return WorkResult(status, task_id, answer)
            return WorkResult("error", task_id, "poll timeout")

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
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=1800.0) as c:
            r = await c.post("/project", json=body)
            if r.status_code < 400:
                # Clone succeeded. The daemon reports `upstream_ok` in the body ONLY when it
                # attempted a bake (fork case); None when no upstream was requested → nothing to warn.
                upstream_ok: bool | None = None
                if upstream:
                    try:
                        upstream_ok = bool(r.json().get("upstream_ok"))
                    except Exception:  # noqa: BLE001 - non-JSON body → treat as bake-unknown/failed
                        upstream_ok = False
                return True, "", upstream_ok
            detail = ""
            try:
                detail = (r.json().get("detail") or "")
            except Exception:  # noqa: BLE001 - non-JSON body
                detail = r.text or ""
            return False, detail.strip()[:200], None

    async def current_focus(self, base_url: str) -> str | None:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as c:
            h = (await c.get("/health")).json()
            return h.get("focus")

    async def add_submodule(
        self, base_url: str, url: str, path: str, *, token: str | None = None,
    ) -> tuple[bool, str]:
        body: dict[str, Any] = {"url": url, "path": path, "actor": "agent-bridge"}
        if token:
            body["token"] = token
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=1800.0) as c:
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
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"),
                                     timeout=float(timeout) + 90.0) as c:
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
            async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0) as c:
                r = await c.post(f"/tasks/{task_id}/cancel")
                return r.status_code < 400
        except httpx.HTTPError:
            return False

    async def has_running_task(self, base_url: str) -> bool:
        """Restart-safe ground truth: does this daemon report a RUNNING task? (see Protocol doc)."""
        try:
            async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15.0) as c:
                r = await c.get("/tasks")
                if r.status_code != 200:
                    return False
                tasks = (r.json() or {}).get("tasks", [])
                return any(t.get("status") == "running" for t in tasks)
        except (httpx.HTTPError, ValueError):
            return False

    async def running_task_progress(
        self, base_url: str, since_offset: int = 0,
    ) -> tuple[str, int] | None:
        """Worker-LIVENESS signal (register #25): `(task_id, event_offset)` for this daemon's running
        task, or `None` if it reports no running task. The offset is the daemon's per-agent-step event
        count (`/tasks/{id}/events` `next_offset`) — it advances on generation / tool / edit, unlike the
        shell-only `activity` array, so a FROZEN offset across ticks is the true signature of a hung
        turn (the stall sweep decides silence from the delta over time). `since_offset` is the last
        offset the caller saw, so the daemon returns only the new events; the returned offset is still
        the running total."""
        try:
            async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15.0) as c:
                r = await c.get("/tasks")
                if r.status_code != 200:
                    return None
                running = next((t for t in (r.json() or {}).get("tasks", [])
                                if t.get("status") == "running"), None)
                tid = running and running.get("task_id")
                if not tid:
                    return None
                e = await c.get(f"/tasks/{tid}/events", params={"offset": max(0, int(since_offset))})
                if e.status_code != 200:            # daemon without the /events route → offset unknown
                    # FAIL-SAFE: report as advancing so an unobservable-but-running daemon is never
                    # mistaken for hung. Never kill a worker whose progress you cannot actually watch.
                    return (tid, int(since_offset) + 1)
                off = int((e.json() or {}).get("next_offset", since_offset))
                return (tid, off)
        except (httpx.HTTPError, ValueError, TypeError):
            return None


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
        # Optional answer text streamed via on_update (default "ok") — set long text to exercise
        # the answer-chunking path.
        self.answer_text: str | None = None

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
        plan_only: bool = False, flail_guard: bool = False,
    ) -> WorkResult:
        self.wakes.append(
            {"base_url": base_url, "session_id": session_id, "prompt": prompt,
             "plan_only": plan_only, "flail_guard": flail_guard}
        )
        if base_url in self.busy_urls:
            req = httpx.Request("POST", base_url.rstrip("/") + "/tasks")
            raise httpx.HTTPStatusError(
                "409 Conflict", request=req, response=httpx.Response(409, request=req))
        if base_url in self.down_urls:
            raise httpx.ConnectError("connection refused", request=httpx.Request("POST", base_url))
        out = self.output_queue.pop(0) if self.output_queue else self.output
        if on_update:
            for cmd in self.stream_commands or []:
                await on_update("command", {"command": cmd, "ok": True})
            await on_update("answer", {"status": self.result_status,
                                       "answer": self.answer_text or "ok"})
        return WorkResult(self.result_status, task_id=f"fake-{len(self.wakes)}", output=out)

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
        self, base_url: str, since_offset: int = 0,
    ) -> tuple[str, int] | None:
        # register #25: (task_id, event_offset) for a busy worker; None if idle. A test freezes the
        # offset to simulate a hang or bumps it to simulate progress.
        if base_url not in getattr(self, "busy_urls", set()):
            return None
        tid = self.progress_task_ids.get(base_url, "fake-task")
        return (tid, self.progress_offsets.get(base_url, 0))
