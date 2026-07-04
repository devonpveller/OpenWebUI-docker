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
    ) -> WorkResult:
        """Resume a session and run one turn to completion; return the result. `on_update`
        streams the worker's commands + answer to the bus as it works (observability)."""
        ...

    async def set_project(
        self, base_url: str, repo: str, *, token: str | None = None,
        upstream: str | None = None, upstream_token: str | None = None,
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


class LittleCoderHarness:
    """Drives the little-coder control daemon. One instance addresses many daemons
    (the pool) by their `base_url` — the scheduler owns which base_url is free."""

    def __init__(self, poll_interval_s: float = 3.0, poll_timeout_s: float = 1800.0) -> None:
        self.poll_interval = poll_interval_s
        self.poll_timeout = poll_timeout_s

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
    ) -> WorkResult:
        async with httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=60.0) as c:
            r = await c.post(
                "/tasks",
                json={
                    "prompt": prompt,
                    "channel": channel,
                    "user_id": "agent-bridge",
                    "session_id": session_id,
                },
            )
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
    ) -> tuple[bool, str, bool | None]:
        # little-coder clones via the REAL git binary (bypasses the git-proxy) — the
        # supported "operator action" workspace-setup path (§12.3). Clone can be slow. A per-request
        # `token` (if given) overrides the pool's global LC_DEPLOY_TOKEN for this project. `upstream`
        # bakes a fork's read-only parent remote AFTER the clone (re-applied every focus, since the
        # workspace is wiped on switch).
        body: dict[str, Any] = {"repo": repo, "actor": "agent-bridge"}
        if token:
            body["token"] = token
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


class FakeHarness:
    """Deterministic in-memory worker for tests. Records every wake."""

    def __init__(
        self, result_status: str = "done", *, stream_commands: list[str] | None = None,
        output: str = "ok",
    ) -> None:
        self.result_status = result_status
        self.output = output          # the WorkResult output; set a 503 marker to simulate a shed
        self.wakes: list[dict[str, Any]] = []
        self.projects: dict[str, str] = {}
        self.tokens: dict[str, str] = {}
        self.upstreams: dict[str, str] = {}
        self.upstream_tokens: dict[str, str] = {}
        # Set to a non-empty error string to simulate a clone/set_project failure (e.g. a private
        # or missing repo the deploy token can't access → the daemon returns "clone failed").
        self.set_project_fails = ""
        # Set True to simulate a clone that SUCCEEDS but whose fork `upstream` bake FAILS (an
        # unreachable/private parent) → set_project returns (True, "", False) so the bridge warns.
        self.upstream_fails = False
        # Optional command lines to stream via on_update("command", ...) before the answer, so a
        # test can exercise the real activity-streaming path (Fix 1). Default None = no commands.
        self.stream_commands = stream_commands

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *,
        channel: str = LC_TRIGGER_CHANNEL, on_update: OnUpdate | None = None,
    ) -> WorkResult:
        self.wakes.append(
            {"base_url": base_url, "session_id": session_id, "prompt": prompt}
        )
        if on_update:
            for cmd in self.stream_commands or []:
                await on_update("command", {"command": cmd, "ok": True})
            await on_update("answer", {"status": self.result_status, "answer": "ok"})
        return WorkResult(self.result_status, task_id=f"fake-{len(self.wakes)}", output=self.output)

    async def set_project(
        self, base_url: str, repo: str, *, token: str | None = None,
        upstream: str | None = None, upstream_token: str | None = None,
    ) -> tuple[bool, str, bool | None]:
        if self.set_project_fails:  # simulate a clone failure (private/missing repo)
            return False, self.set_project_fails, None
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
