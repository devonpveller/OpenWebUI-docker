"""ao-temp06: worker requests carry temperature 0.6 through the real pi (tests/node/e2e_temp06/run.sh).

Opt-in: it starts one throwaway container (the little-coder image, `--network none`), so it runs only
with LC_E2E_DOCKER=1 and a reachable Docker. The suites' usual DOCKER_HOST=tcp://127.0.0.1:1 makes it
skip. Image: LC_E2E_LC_IMAGE (default little-coder:local; only run)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent / "node" / "e2e_temp06"


def _docker_ok() -> bool:
    if not shutil.which("docker") or not shutil.which("sh"):
        return False
    try:
        return subprocess.run(["docker", "version"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(os.environ.get("LC_E2E_DOCKER") != "1", reason="opt-in: LC_E2E_DOCKER=1")
def test_worker_temperature_end_to_end():
    if not _docker_ok():
        pytest.skip("no reachable docker")
    run = "pytest"
    proc = subprocess.run(
        ["sh", str(HERE / "run.sh"), os.environ.get("LC_E2E_LC_IMAGE", "little-coder:local")],
        env={**os.environ, "E2E_RUN": run}, capture_output=True, text=True, timeout=900,
    )
    results = json.loads((HERE / "out" / run / "results.json").read_text(encoding="utf-8"))
    failed = [r["check"] for r in results if not r["ok"]]
    assert proc.returncode == 0 and not failed, (failed, proc.stdout[-4000:])
    assert len(results) >= 5
