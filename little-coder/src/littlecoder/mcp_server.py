"""Stdio MCP server — the task-trigger surface behind `lc-mcpo` (design §3.1).

Built in Tool, **dormant** until Chapter 2 registers it as an OWUI tool. The
`lc-mcpo` service launches `python -m littlecoder.mcp_server` and re-exposes
it as OpenAPI, mirroring the `search-mcpo` pattern.

This server is a thin client: it forwards triggers to the control daemon's
HTTP API. There is exactly one agent and one FIFO queue (design §12.4) — the
MCP edge never spawns its own. It carries task triggers ONLY; operator
commands authenticate at their own surfaces, never here (design §12.6).
"""

from __future__ import annotations

import json
import os

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("little-coder")

_DAEMON = os.environ.get("LC_DAEMON_URL", "http://little-coder:8090")


def _call(method: str, path: str, **kwargs) -> str:
    try:
        with httpx.Client(base_url=_DAEMON, timeout=30.0) as c:
            resp = c.request(method, path, **kwargs)
        resp.raise_for_status()
        return json.dumps(resp.json())
    except httpx.HTTPStatusError as exc:
        return json.dumps({"error": exc.response.text, "status": exc.response.status_code})
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"control daemon unreachable: {exc}"})


@mcp.tool()
def trigger_task(
    prompt: str,
    acceptance_command: str | None = None,
    user_id: str = "owui",
) -> str:
    """Trigger a little-coder coding task against the focused project.

    Returns the task id and queue status. Poll `task_status` for the result
    (triggers are fire-and-await — design §4.2). `acceptance_command`, if
    given, is run after the task and its exit code decides pass/fail."""
    body: dict = {"prompt": prompt, "channel": "owui", "user_id": user_id}
    if acceptance_command:
        body["acceptance_command"] = acceptance_command
    return _call("POST", "/tasks", json=body)


@mcp.tool()
def task_status(task_id: str) -> str:
    """Get the current status and outcome of a task by id."""
    return _call("GET", f"/tasks/{task_id}")


@mcp.tool()
def project_focus() -> str:
    """Report which repository little-coder is currently focused on."""
    return _call("GET", "/focus")


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
