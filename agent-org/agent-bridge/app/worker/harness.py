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
from typing import Any, Protocol

import httpx

log = logging.getLogger("agent_bridge.worker")


class WorkResult:
    def __init__(self, status: str, task_id: str, output: str = "") -> None:
        self.status = status          # done | abandoned | rejected | error
        self.task_id = task_id
        self.output = output

    @property
    def ok(self) -> bool:
        return self.status == "done"


class WorkerHarness(Protocol):
    async def wake(
        self, base_url: str, session_id: str, prompt: str, *, channel: str = "agent-org"
    ) -> WorkResult:
        """Resume a session and run one turn to completion; return the result."""
        ...


class LittleCoderHarness:
    """Drives the little-coder control daemon. One instance addresses many daemons
    (the pool) by their `base_url` — the scheduler owns which base_url is free."""

    def __init__(self, poll_interval_s: float = 3.0, poll_timeout_s: float = 1800.0) -> None:
        self.poll_interval = poll_interval_s
        self.poll_timeout = poll_timeout_s

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *, channel: str = "agent-org"
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
            # Poll to terminal state (little-coder is async; the scheduler treats
            # this whole call as the agent's `computing` window — machine B §3.6).
            waited = 0.0
            while waited < self.poll_timeout:
                await asyncio.sleep(self.poll_interval)
                waited += self.poll_interval
                s = (await c.get(f"/tasks/{task_id}")).json()
                status = s.get("status", "")
                if status in ("done", "abandoned", "rejected"):
                    return WorkResult(status, task_id, s.get("result", ""))
            return WorkResult("error", task_id, "poll timeout")


class FakeHarness:
    """Deterministic in-memory worker for tests. Records every wake."""

    def __init__(self, result_status: str = "done") -> None:
        self.result_status = result_status
        self.wakes: list[dict[str, Any]] = []

    async def wake(
        self, base_url: str, session_id: str, prompt: str, *, channel: str = "agent-org"
    ) -> WorkResult:
        self.wakes.append(
            {"base_url": base_url, "session_id": session_id, "prompt": prompt}
        )
        return WorkResult(self.result_status, task_id=f"fake-{len(self.wakes)}", output="ok")
