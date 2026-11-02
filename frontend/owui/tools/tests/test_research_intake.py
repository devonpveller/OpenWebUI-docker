"""Tests for frontend/owui/tools/research_intake.py, rw-p5a-intake - no OWUI, no live service.

The OWUI inlet of the research workspace's add: the tool calls the SAME add the intake panel does
(openbrain-workbench POST /workbench/intake/add) with Open WebUI's service key and its user's Authentik
subject in x-on-behalf-of, sends only the link (and the refinery, which it asks for when none is named),
and fetches nothing itself. A stdlib http.server bound to 127.0.0.1 stands in for the workbench; a socket
guard fails the run if anything connects anywhere but 127.0.0.1.

    python -m unittest discover -s frontend/owui/tools/tests -v
"""

import asyncio
import http.server
import importlib.util
import json
import os
import socket
import sys
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("research_intake_tool", os.path.join(HERE, "..", "research_intake.py"))
ri = importlib.util.module_from_spec(_spec)
sys.modules["research_intake_tool"] = ri
_spec.loader.exec_module(ri)

_real_connect = socket.socket.connect


def _guard(self, addr):
    host = addr[0] if isinstance(addr, tuple) else addr
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise AssertionError(f"the test tried to reach {addr}")
    return _real_connect(self, addr)


REFINERIES = [{"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1", "title": "Motion sickness"}]


class Workbench(http.server.BaseHTTPRequestHandler):
    seen: list = []
    final = "held"

    def _send(self, status, obj):
        out = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Workbench.seen.append(("POST", self.path, body, self.headers.get("x-brain-key"), self.headers.get("x-on-behalf-of")))
        if self.path != "/workbench/intake/add":
            return self._send(404, {"error": "not found"})
        if not body.get("refinery_id"):
            return self._send(400, {"error": "choose the refinery it joins", "needs": "refinery_id", "refineries": REFINERIES})
        return self._send(202, {"job_id": "a0000000-0000-4000-8000-000000000001", "kind": "add", "status": "queued", "origin": "owui"})

    def do_GET(self):  # noqa: N802
        Workbench.seen.append(("GET", self.path, None, self.headers.get("x-brain-key"), self.headers.get("x-on-behalf-of")))
        detail = {"error": "the video has no transcript that can be read"} if Workbench.final == "failed" else {}
        return self._send(200, {"job": {"kind": "add", "status": Workbench.final, "detail": detail}})

    def log_message(self, *a):  # quiet
        pass


class ResearchIntakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socket.socket.connect = _guard
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Workbench)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        socket.socket.connect = _real_connect

    def setUp(self):
        Workbench.seen = []
        Workbench.final = "held"
        self.t = ri.Tools()
        self.t.valves.workbench_url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.t.valves.service_key = "owui-service-key"
        self.t.valves.wait_sec = 2
        self.user = {"id": "u1", "oauth": {"oidc": {"sub": "authentik-sub-a"}}}

    def run_tool(self, **kw):
        return asyncio.run(self.t.add_research_source(__user__=self.user, **kw))

    def test_asks_which_refinery_and_creates_none(self):
        out = self.run_tool(url="https://www.ncbi.nlm.nih.gov/pubmed/40128952")
        self.assertIn("Which refinery does it join?", out)
        self.assertIn("Motion sickness", out)
        self.assertEqual([s[1] for s in Workbench.seen], ["/workbench/intake/add"])

    def test_the_same_add_as_the_panel_with_owuis_key_and_the_users_subject(self):
        out = self.run_tool(url="https://youtu.be/dQw4w9WgXcQ", refinery="motion sickness")
        self.assertIn("Held in your refinery", out)
        posts = [s for s in Workbench.seen if s[0] == "POST"]
        self.assertEqual(posts[-1][2], {"url": "https://youtu.be/dQw4w9WgXcQ", "refinery_id": REFINERIES[0]["id"]})
        for s in Workbench.seen:
            self.assertEqual((s[3], s[4]), ("owui-service-key", "authentik-sub-a"))
        self.assertTrue(all(s[1].startswith("/workbench/intake/") for s in Workbench.seen), "only the intake add and its job")

    def test_a_declared_type_outside_the_three_is_refused_before_any_call(self):
        out = self.run_tool(url="https://e.org/a.docx", refinery=REFINERIES[0]["id"], content_type="docx")
        self.assertIn("Not a content type", out)
        self.assertEqual(Workbench.seen, [])

    def test_a_failed_add_says_why(self):
        Workbench.final = "failed"
        out = self.run_tool(url="https://youtu.be/dQw4w9WgXcQ", refinery=REFINERIES[0]["id"], content_type="youtube_transcript")
        self.assertIn("no transcript", out)

    def test_no_subject_no_add(self):
        self.user = {"id": "u2"}
        out = self.run_tool(url="https://e.org/a", refinery=REFINERIES[0]["id"])
        self.assertIn("no Authentik sign-in link", out)
        self.assertEqual(Workbench.seen, [])

    def test_the_tool_fetches_nothing_itself(self):
        with open(os.path.join(HERE, "..", "research_intake.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("ingest_url", src)
        self.assertEqual(src.count("s.post("), 1)
        self.assertEqual(src.count("s.get("), 1)
        self.assertIn("/workbench/intake/add", src)


if __name__ == "__main__":
    unittest.main()
