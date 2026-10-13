"""ao-httplog: the bridge must not log outbound request URLs by default.

The check runs the bridge's REAL production logging setup: a fresh interpreter imports
`app.main` (which runs logging.basicConfig(INFO) + the httpx/httpcore quieting) and then makes
real httpx requests, through the real GitHub read functions in app/modules/capabilities.py,
against a local stub HTTP server (loopback only; no real GitHub, no network). A fresh process is
used because pytest installs its own root handlers/level, which would hide the production level.
Everything the child writes to stderr (the root handler) is the "log" asserted on.
"""

from __future__ import annotations

import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

BRIDGE = Path(__file__).resolve().parents[1]
REPO = "PLANTED-ghp_urlpathsecret"   # repo name carrying a planted token-like path segment
HEADER_TOKEN = "ghs_PLANTEDHEADERTOKEN"
OWNER = "test-owner"

CHILD = r"""
import asyncio, logging, sys, json
import app.main  # noqa: F401  - the production logging setup runs at import
from app.modules.capabilities import read_repo_state, read_default_branch_head
from app.modules.github_app import FakeGitHubApp

async def run(base):
    gh = FakeGitHubApp(token=sys.argv[2], owner=sys.argv[3])
    url = f"https://github.com/{sys.argv[3]}/{sys.argv[4]}"
    st = await read_repo_state(gh, url, api_base=base)
    hd = await read_default_branch_head(gh, url, api_base=base)
    return st, hd

st, hd = asyncio.run(run(sys.argv[1]))
logging.getLogger("agent_bridge.probe").info("BRIDGE-INFO-MARK")
logging.getLogger("agent_bridge.probe").warning("BRIDGE-WARN-MARK")
logging.getLogger("httpx").warning("HTTPX-WARN-MARK")
print(json.dumps({"readable": st.readable, "top": st.top_level, "head": hd}))
"""


class _Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep the stub's own stderr out of the child's... (separate proc anyway)
        pass

    def do_GET(self):
        path = self.path.split("?")[0]
        base = f"/repos/{OWNER}/{REPO}"
        if path == base:
            body, code = {"default_branch": "main"}, 200
        elif path == base + "/contents/.gitmodules":
            body, code = {}, 404
        elif path == base + "/contents":
            body, code = [{"name": "src", "type": "dir"}], 200
        elif path == base + "/branches/main":
            body, code = {"commit": {"sha": "abc123"}}, 200
        else:
            body, code = {"message": "nf"}, 404
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def stub():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


def _run(base: str, **env_extra: str) -> tuple[str, dict]:
    env = {k: v for k, v in os.environ.items() if k != "AO_LOG_HTTP_REQUESTS"}
    env.update(env_extra)
    env["DOCKER_HOST"] = "tcp://127.0.0.1:1"
    p = subprocess.run(
        [sys.executable, "-c", CHILD, base, HEADER_TOKEN, OWNER, REPO],
        cwd=BRIDGE, env=env, capture_output=True, text=True, timeout=60,
    )
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout.strip().splitlines()[-1])
    return p.stderr, out


def test_reads_work_through_the_stub(stub):
    _, out = _run(stub)
    assert out["readable"] is True and out["top"] == ["src/"]
    assert out["head"] == ["main", "abc123"]


def test_default_logs_no_request_url_or_token(stub):
    log, _ = _run(stub)
    assert REPO not in log
    assert "/repos/" not in log
    assert stub not in log
    assert "HTTP Request" not in log
    assert HEADER_TOKEN not in log


def test_opt_in_shows_requests(stub):
    log, _ = _run(stub, AO_LOG_HTTP_REQUESTS="1")
    assert f"/repos/{OWNER}/{REPO}" in log
    assert "HTTP Request" in log
    assert HEADER_TOKEN not in log   # headers are never logged, even when opted in


@pytest.mark.parametrize("flag", [None, "1"])
def test_bridge_and_warning_records_unaffected(stub, flag):
    extra = {"AO_LOG_HTTP_REQUESTS": flag} if flag else {}
    log, _ = _run(stub, **extra)
    assert "INFO:agent_bridge.probe:BRIDGE-INFO-MARK" in log
    assert "WARNING:agent_bridge.probe:BRIDGE-WARN-MARK" in log
    assert "WARNING:httpx:HTTPX-WARN-MARK" in log   # WARNING+ from the libraries still logs


@pytest.mark.parametrize("flag", ["0", "true", "yes", ""])
def test_only_exactly_1_enables(stub, flag):
    log, _ = _run(stub, AO_LOG_HTTP_REQUESTS=flag)
    assert "HTTP Request" not in log and REPO not in log
