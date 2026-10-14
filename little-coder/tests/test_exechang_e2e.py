"""ao-exechang: the real-exec-path end-to-end (tests/node/e2e_exechang/run.sh) under pytest.

Opt-in: it starts two throwaway containers (a real open-terminal, `--network none`, and the
little-coder image joined to it), so it runs only with LC_E2E_DOCKER=1 and a reachable Docker.
The suites' usual DOCKER_HOST=tcp://127.0.0.1:1 makes it skip. Images: LC_E2E_LC_IMAGE /
LC_E2E_OT_IMAGE (default little-coder:local / little-coder-open-terminal:local; only run)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "node" / "e2e_exechang"


def _docker_ok() -> bool:
    if not shutil.which("docker") or not shutil.which("sh"):
        return False
    try:
        return subprocess.run(["docker", "version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(os.environ.get("LC_E2E_DOCKER") != "1", reason="opt-in: LC_E2E_DOCKER=1")
def test_real_exec_path_end_to_end():
    if not _docker_ok():
        pytest.skip("no reachable docker")
    run = "pytest"
    env = {**os.environ, "E2E_RUN": run}
    proc = subprocess.run(
        ["sh", str(HERE / "run.sh"), os.environ.get("LC_E2E_LC_IMAGE", "little-coder:local"),
         os.environ.get("LC_E2E_OT_IMAGE", "little-coder-open-terminal:local")],
        env=env, capture_output=True, text=True, timeout=1200,
    )
    results = json.loads((HERE / "out" / run / "results.json").read_text(encoding="utf-8"))
    failed = [r["check"] for r in results if not r["ok"]]
    assert proc.returncode == 0 and not failed, (failed, proc.stdout[-4000:])
    assert len(results) >= 45
