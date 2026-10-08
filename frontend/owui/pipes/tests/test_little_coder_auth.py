"""ao-dauth - the Little Coder pipe sends the daemon token (no OWUI, no live daemon).

A stdlib http.server on 127.0.0.1 stands in for the little-coder daemon and enforces the bearer
the way the real one does (GET /health public; anything else 401 without it, 503 when the
daemon has no token). It records every request's method, path and Authorization header.

Run (from the repo root):

    python -m unittest discover -s frontend/owui/pipes/tests -v
"""

import asyncio
import http.server
import importlib.util
import json
import os
import threading
import unittest
from pathlib import Path

PIPE = Path(__file__).resolve().parents[1] / "little_coder.py"
TOKEN = "-".join(["fake", "owui", "lc", "t0k3n", "5e6f"])


def _load_pipe():
    spec = importlib.util.spec_from_file_location("lc_pipe_under_test", PIPE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeDaemon(http.server.BaseHTTPRequestHandler):
    token = TOKEN
    seen: list = []

    def log_message(self, *a):
        pass

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        auth = self.headers.get("Authorization")
        type(self).seen.append((self.command, self.path.split("?")[0], auth))
        public = self.command == "GET" and self.path == "/health"
        if not public and type(self).token is None:
            code, body = 503, {"detail": "not configured"}
        elif not public and auth != f"Bearer {type(self).token}":
            code, body = 401, {"detail": "daemon token required"}
        elif self.path.startswith("/tasks") and self.command == "POST":
            code, body = 200, {"task_id": "T1", "status": "queued"}
        else:
            code, body = 200, {"status": "ok", "pending": [], "tasks": []}
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _answer
    do_POST = _answer


class PipeSendsTheDaemonToken(unittest.TestCase):
    def setUp(self):
        _FakeDaemon.seen = []
        _FakeDaemon.token = TOKEN
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeDaemon)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.mod = _load_pipe()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()

    def _pipe(self, token):
        p = self.mod.Pipe()
        p.valves.daemon_url = self.url
        p.valves.daemon_token = token
        return p

    def _calls(self, p):
        async def go():
            out = []
            for method, path, body in [("GET", "/health", None), ("POST", "/tasks", {"prompt": "x"}),
                                       ("GET", "/tasks/T1", None), ("POST", "/tasks/T1/cancel", None),
                                       ("GET", "/admin/pending", None),
                                       ("POST", "/admin/approve/abc", None),
                                       ("POST", "/project", {"repo": "r"})]:
                out.append(await p._call(method, path, body))
            await p._cancel_quietly("T1")
            return out
        return asyncio.run(go())

    def test_every_call_carries_the_bearer(self):
        results = self._calls(self._pipe(TOKEN))
        self.assertTrue(all(ok for ok, _ in results), results)
        self.assertEqual(len(_FakeDaemon.seen), 8)
        for method, path, auth in _FakeDaemon.seen:
            self.assertEqual(auth, f"Bearer {TOKEN}", (method, path))

    def test_blank_valve_sends_no_header_and_is_refused(self):
        results = self._calls(self._pipe("   "))
        self.assertTrue(results[0][0])                    # /health is public
        self.assertFalse(any(ok for ok, _ in results[1:]))
        self.assertTrue(all(auth is None for _m, _p, auth in _FakeDaemon.seen))

    def test_refusal_detail_never_echoes_the_token(self):
        ok, data = asyncio.run(self._pipe(TOKEN + "x")._call("GET", "/tasks"))
        self.assertFalse(ok)
        self.assertNotIn(TOKEN, json.dumps(data))

    def test_valve_defaults_from_the_environment(self):
        old = os.environ.get("LC_DAEMON_TOKEN")
        os.environ["LC_DAEMON_TOKEN"] = TOKEN
        try:
            self.assertEqual(self.mod.Pipe().valves.daemon_token, TOKEN)
        finally:
            if old is None:
                os.environ.pop("LC_DAEMON_TOKEN", None)
            else:
                os.environ["LC_DAEMON_TOKEN"] = old
        os.environ.pop("LC_DAEMON_TOKEN", None)
        try:
            self.assertEqual(self.mod.Pipe().valves.daemon_token, "")
        finally:
            if old is not None:
                os.environ["LC_DAEMON_TOKEN"] = old


if __name__ == "__main__":
    unittest.main()
