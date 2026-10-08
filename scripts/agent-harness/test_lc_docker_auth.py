"""ao-dauth round 2 (X1): the quadrant's little-coder transport sends the daemon token.

`quadrant/lc_docker.py::api()` runs curl inside the little-coder container via `docker exec`.
Since ao-dauth the daemon refuses every route but /health without
`Authorization: Bearer <LC_DAEMON_TOKEN>`. These tests prove the header is built INSIDE the
container from its own env - the token never appears in the docker argv - and that the in-container
script really hands curl that header (run under a local bash with a stub `curl`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from quadrant import lc_docker as L  # noqa: E402

TOKEN = "-".join(["fake", "quadrant", "lc", "7c1e"])


def _capture(monkeypatch, stdout="{}\n200"):
    seen = {}

    def fake_run(args, **kw):
        seen["args"] = args
        seen["input"] = kw.get("input")
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(L._proc, "run", fake_run)
    return seen


def test_api_builds_the_header_inside_the_container(monkeypatch):
    monkeypatch.setenv("LC_DAEMON_TOKEN", TOKEN)   # even if the HOST had one, it is not used
    seen = _capture(monkeypatch)
    L.api("lc-x", "http://localhost:8090", "POST", "/tasks", {"prompt": "p"})
    args = seen["args"]
    assert args[:6] == ["docker", "exec", "-i", "lc-x", "bash", "-c"]
    assert args[6] == L._AUTH_CURL and "${LC_DAEMON_TOKEN" in args[6]
    assert not any(TOKEN in a for a in args)
    assert "--data-binary" in args and "@-" in args          # body still on stdin
    assert seen["input"] == '{"prompt": "p"}'
    assert args[-1] == "http://localhost:8090/tasks"


def test_api_refusal_surfaces(monkeypatch):
    _capture(monkeypatch, stdout='{"detail":"daemon token required"}\n401')
    with pytest.raises(L.LcDockerError, match="HTTP 401"):
        L.api("lc-x", "http://localhost:8090", "GET", "/tasks")


_BASH = shutil.which("bash")


@pytest.mark.skipif(_BASH is None, reason="no bash on this host")
def test_in_container_script_hands_curl_the_header(tmp_path):
    """Run the exact script under bash with a stub curl that prints what it was given."""
    stub = tmp_path / "curl"
    stub.write_text('#!/bin/bash\nwhile [ $# -gt 0 ]; do\n  if [ "$1" = "-H" ]; then\n'
                    '    case "$2" in @*) echo "HDRFILE:$(cat "${2#@}")";; *) echo "HDR:$2";; esac\n'
                    '    shift 2; continue; fi\n  echo "ARG:$1"; shift\ndone\n', encoding="utf-8",
                    newline="\n")
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{tmp_path}{os.pathsep}{os.environ.get('PATH', '')}",
               LC_DAEMON_TOKEN=TOKEN)
    out = subprocess.run([_BASH, "-c", L._AUTH_CURL, "lc-quadrant", "-X", "GET",
                          "http://localhost:8090/tasks"], capture_output=True, text=True, env=env)
    if out.returncode != 0 and "HDRFILE" not in out.stdout:
        pytest.skip(f"local bash cannot run the stub: {out.stderr[:200]}")
    assert f"HDRFILE:Authorization: Bearer {TOKEN}" in out.stdout
    assert "ARG:http://localhost:8090/tasks" in out.stdout
    env.pop("LC_DAEMON_TOKEN")
    out = subprocess.run([_BASH, "-c", L._AUTH_CURL, "lc-quadrant", "-X", "GET", "u"],
                         capture_output=True, text=True, env=env)
    assert "HDRFILE:Authorization: Bearer unset" in out.stdout
