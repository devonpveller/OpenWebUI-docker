"""Tests for frontend/owui/tools/deep_research.py, rw-identity - no OWUI, no live service.

The tool sends its user's Authentik subject (Open WebUI's user record, `oauth` JSON,
`<provider>.sub`) as `on_behalf_of`, so research started in Open WebUI lands in that user's
research front end. A stdlib http.server bound to 127.0.0.1 stands in for openbrain-research and
records each POST /research body. A socket guard fails the run if anything connects anywhere but
127.0.0.1.

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
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("deep_research_tool", os.path.join(HERE, "..", "deep_research.py"))
dr = importlib.util.module_from_spec(_spec)
sys.modules["deep_research_tool"] = dr
_spec.loader.exec_module(dr)

_real_connect = socket.socket.connect


def _guard(self, addr):
    host = addr[0] if isinstance(addr, tuple) else addr
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise AssertionError(f"the test tried to reach {addr}")
    return _real_connect(self, addr)


class Engine(http.server.BaseHTTPRequestHandler):
    bodies: list = []
    refuse_obo = False  # answer 403 to a body naming a user (a key that is not Open WebUI's)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        Engine.bodies.append(body)
        status = 403 if Engine.refuse_obo and "on_behalf_of" in body else 202
        out = json.dumps({"job_id": "j-1", "status": "queued", "callback_armed": True} if status == 202 else {"error": "refused"}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):  # quiet
        pass


class OnBehalfOf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        socket.socket.connect = _guard
        cls.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Engine)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        socket.socket.connect = _real_connect
        sys.modules.pop("open_webui.models.users", None)

    def setUp(self):
        Engine.bodies = []
        Engine.refuse_obo = False
        sys.modules.pop("open_webui.models.users", None)

    def run_tool(self, user, provider=None):
        t = dr.Tools()
        t.valves.research_url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        t.valves.brain_key = "owui-caller-key"
        if provider:
            t.valves.oauth_provider = provider
        return asyncio.run(t.deep_research("what is the boiling point", __user__=user, __chat_id__="c-1", __message_id__="m-1"))

    def test_sends_the_users_authentik_subject_from_the_user_record(self):
        self.run_tool({"id": "u1", "email": "a@x", "oauth": {"oidc": {"sub": "ak-alice-0f9e"}}})
        self.assertEqual(Engine.bodies[-1]["on_behalf_of"], "ak-alice-0f9e")
        self.assertEqual(Engine.bodies[-1]["origin"], "owui")

    def test_reads_the_record_by_id_when_user_carries_no_oauth(self):
        class Rec:
            oauth = {"oidc": {"sub": "ak-bob-1234"}}

        async def get_user_by_id(uid):
            return Rec() if uid == "u2" else None

        mod = types.ModuleType("open_webui.models.users")
        mod.Users = types.SimpleNamespace(get_user_by_id=get_user_by_id)
        sys.modules["open_webui.models.users"] = mod
        self.run_tool({"id": "u2", "email": "b@x"})
        self.assertEqual(Engine.bodies[-1]["on_behalf_of"], "ak-bob-1234")

    def test_the_provider_valve_and_the_legacy_oauth_sub_shape(self):
        self.run_tool({"id": "u3", "oauth": {"authentik": {"sub": "ak-carol"}}}, provider="authentik")
        self.assertEqual(Engine.bodies[-1]["on_behalf_of"], "ak-carol")
        self.run_tool({"id": "u4", "oauth_sub": "oidc@ak-dave"})
        self.assertEqual(Engine.bodies[-1]["on_behalf_of"], "ak-dave")

    def test_a_local_account_without_an_authentik_link_sends_none(self):
        self.run_tool({"id": "u5", "email": "local@x"})
        self.assertNotIn("on_behalf_of", Engine.bodies[-1])
        self.run_tool({"id": "u6", "oauth": {"google": {"sub": "g-1"}}})
        self.assertNotIn("on_behalf_of", Engine.bodies[-1])
        self.run_tool(None)
        self.assertNotIn("on_behalf_of", Engine.bodies[-1])

    def test_a_key_that_may_not_name_a_user_still_runs_the_research_filed_as_the_tool(self):
        Engine.refuse_obo = True
        out = self.run_tool({"id": "u1", "oauth": {"oidc": {"sub": "ak-alice"}}})
        self.assertEqual(len(Engine.bodies), 2)
        self.assertEqual(Engine.bodies[0]["on_behalf_of"], "ak-alice")
        self.assertNotIn("on_behalf_of", Engine.bodies[1])
        self.assertIn("j-1", out)


if __name__ == "__main__":
    unittest.main()
