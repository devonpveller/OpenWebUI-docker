"""ao-asyncexec: the open-terminal-exec pi extension's bash tool must not block pi's event loop.

Runs the Node contract test (tests/node/open_terminal_exec.test.mjs) against the real extension
file with a stub `ot-exec`: the event loop keeps ticking while a command runs, the tool is
registered `executionMode: "sequential"`, pi's abort signal stops the command, and the output cap,
exit codes, ot-exec's timeout exit and the "(no output)" text are what spawnSync gave. No container,
no network, no model. Needs `node` (>= 22.6 for --experimental-strip-types) on PATH."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEST = _ROOT / "tests" / "node" / "open_terminal_exec.test.mjs"


def _node() -> str | None:
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    try:
        major, minor = (int(x) for x in out.lstrip("v").split(".")[:2])
    except ValueError:
        return None
    return node if (major, minor) >= (22, 6) else None


def test_open_terminal_exec_node_contract():
    node = _node()
    if node is None:
        pytest.skip("node >= 22.6 not on PATH: the extension contract test cannot run here")
    env = {k: v for k, v in os.environ.items() if k != "OTX_EXTENSION"}
    proc = subprocess.run(
        [node, "--experimental-strip-types", "--test", str(_TEST)],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-6000:] + proc.stderr[-2000:]
    assert "# fail 0" in proc.stdout and "# pass 23" in proc.stdout, proc.stdout[-3000:]
