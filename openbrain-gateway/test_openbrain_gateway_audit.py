"""The gateway keeps an append-only audit record with NO arguments or payloads.

One JSONL line per /mcp request - allowed or refused - naming the key id (never
the key), the JSON-RPC methods and tool names, the decision, the result status
and byte counts. A secret-looking value planted in the arguments, in
metadata_extra and in a wrong bearer token must not appear anywhere in the file.
The file rotates and stays bounded.

The upstream is a stub (httpx.MockTransport). Run: python -m pytest openbrain-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key-audit"
os.environ.setdefault("OPENBRAIN_URL", "http://stub-openbrain:8000")
os.environ["OPENBRAIN_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY

_spec = importlib.util.spec_from_file_location(
    "openbrain_gateway_app_audit", pathlib.Path(__file__).with_name("app.py"))
gw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gw)

AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}
SECRET = "sk-PLANTED-0123456789abcdefSECRET"


def _call(name, arguments, rid=1):
    return json.dumps({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})


@pytest.fixture
def audit(tmp_path, monkeypatch):
    path = tmp_path / "audit" / "cloud.jsonl"
    log = gw.AuditLog(str(path), 1 << 20, 2)
    monkeypatch.setattr(gw, "AUDIT", log)
    return path


@pytest.fixture
def upstream(monkeypatch):
    seen = {"n": 0, "reply": None}

    def handler(request: httpx.Request):
        seen["n"] += 1
        if seen["reply"] is not None:
            return seen["reply"]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                         "result": {"content": [{"type": "text", "text": "stored " + SECRET}]}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_allowed_call_one_line_no_payload(audit, upstream):
    body = _call("capture_thought", {"content": f"my api key is {SECRET}",
                                     "metadata_extra": {"note": SECRET}})
    r = TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    assert r.status_code == 200 and upstream["n"] == 1
    raw = audit.read_text(encoding="utf-8")
    assert SECRET not in raw and "my api key" not in raw and GATEWAY_KEY not in raw
    (rec,) = _lines(audit)
    assert rec["decision"] == "allowed" and rec["refusal"] is None
    assert rec["methods"] == ["tools/call"] and rec["tools"] == ["capture_thought"]
    assert rec["status"] == 200 and rec["upstream_status"] == 200 and rec["result"] == "ok"
    assert rec["key_id"] and rec["key_id"].startswith("cloud-")
    assert rec["req_bytes"] == len(body.encode()) and rec["resp_bytes"] > 0
    assert set(rec) == {"ts", "profile", "key_id", "client", "http_method", "methods", "tools",
                        "decision", "refusal", "status", "upstream_status", "result",
                        "req_bytes", "resp_bytes", "ms"}


def test_refused_tool_is_logged_as_refused(audit, upstream):
    r = TestClient(gw.app).post("/mcp", content=_call("thought_stats", {"x": SECRET}), headers=AUTH)
    assert r.status_code == 200 and upstream["n"] == 0
    (rec,) = _lines(audit)
    assert rec["decision"] == "refused" and rec["refusal"] == "tool_not_allowed"
    # a refused tool is not on the allow-list, so its name is NOT logged (round 2, F4)
    assert rec["tools"] == ["<unknown>"] and rec["upstream_status"] is None
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_bad_arguments_refusal(audit, upstream):
    r = TestClient(gw.app).post("/mcp", content=_call("search_thoughts", {"metadata_filter": [SECRET]}),
                                headers=AUTH)
    assert r.status_code == 200 and "error" in r.json() and upstream["n"] == 0
    (rec,) = _lines(audit)
    assert rec["refusal"] == "bad_arguments"
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_unauthorized_logged_without_the_presented_token(audit, upstream):
    h = {"authorization": f"Bearer {SECRET}", "content-type": "application/json"}
    r = TestClient(gw.app).post("/mcp", content=_call("search", {"query": "q"}), headers=h)
    assert r.status_code == 401
    (rec,) = _lines(audit)
    assert rec["refusal"] == "unauthorized" and rec["key_id"] is None and rec["tools"] == []
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_unparseable_body_refused_and_logged(audit, upstream):
    r = TestClient(gw.app).post("/mcp", content=b"\xef\xbb\xbf" + _call("search", {"q": SECRET}).encode(),
                                headers=AUTH)
    assert r.status_code == 400
    (rec,) = _lines(audit)
    assert rec["refusal"] == "body_refused" and rec["methods"] == []
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_too_large_logged(audit, upstream, monkeypatch):
    monkeypatch.setattr(gw, "MAX_BODY_BYTES", 64)
    r = TestClient(gw.app).post("/mcp", content=_call("search", {"query": SECRET * 4}), headers=AUTH)
    assert r.status_code == 413
    (rec,) = _lines(audit)
    assert rec["refusal"] == "too_large"
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_payload_cannot_ride_in_a_tool_name(audit, upstream):
    TestClient(gw.app).post("/mcp", content=_call(f"x {SECRET} y", {}), headers=AUTH)
    (rec,) = _lines(audit)
    assert rec["tools"] == ["<unknown>"]
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_sse_error_result_is_status_error(audit, upstream):
    upstream["reply"] = httpx.Response(
        200, headers={"content-type": "text/event-stream"},
        content=('event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"isError":true,'
                 '"content":[{"type":"text","text":"' + SECRET + '"}]}}\n\n').encode())
    TestClient(gw.app).post("/mcp", content=_call("ingest_url", {"url": "https://example.com/"}),
                            headers=AUTH)
    (rec,) = _lines(audit)
    assert rec["decision"] == "allowed" and rec["result"] == "error" and rec["tools"] == ["ingest_url"]
    assert SECRET not in audit.read_text(encoding="utf-8")


def test_one_line_per_request(audit, upstream):
    c = TestClient(gw.app)
    for i in range(5):
        c.post("/mcp", content=_call("search", {"query": "q"}, rid=i), headers=AUTH)
    c.post("/mcp", content=_call("thought_stats", {}), headers=AUTH)
    assert len(_lines(audit)) == 6


def test_rotation_is_bounded(tmp_path):
    path = tmp_path / "a.jsonl"
    log = gw.AuditLog(str(path), 400, 2)
    for i in range(200):
        log.write({"i": i, "pad": "x" * 40})
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == ["a.jsonl", "a.jsonl.1", "a.jsonl.2"]
    for p in tmp_path.iterdir():
        assert p.stat().st_size <= 400
    last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
    assert last["i"] == 199  # newest record is in the live file


def test_off_when_unset(tmp_path):
    log = gw.AuditLog("", 400, 2)
    log.write({"i": 1})
    assert list(tmp_path.iterdir()) == []


def test_unopenable_path_fails_at_start(tmp_path):
    d = tmp_path / "is-a-dir"
    d.mkdir()
    with pytest.raises(OSError):
        gw.AuditLog(str(d), 400, 2)


def test_key_id_never_contains_the_key():
    assert GATEWAY_KEY not in gw.KEY_ID


# -- round 2 (tester attempt 1, F4): identifier-shaped attacker names are not logged --
IDENT_SECRET = "sk-PLANTEDSECRET999"


def test_identifier_shaped_tool_name_not_logged(audit, upstream):
    TestClient(gw.app).post("/mcp", content=_call(IDENT_SECRET, {}), headers=AUTH)
    (rec,) = _lines(audit)
    assert rec["tools"] == ["<unknown>"] and rec["decision"] == "refused"
    assert IDENT_SECRET not in audit.read_text(encoding="utf-8")


def test_unknown_method_name_not_logged(audit, upstream):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": IDENT_SECRET, "params": {}})
    TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    (rec,) = _lines(audit)
    assert rec["methods"] == ["<unknown>"]
    assert IDENT_SECRET not in audit.read_text(encoding="utf-8")


def test_batch_of_unknown_names_not_logged(audit, upstream):
    batch = [{"jsonrpc": "2.0", "id": i, "method": "tools/call",
              "params": {"name": IDENT_SECRET, "arguments": {}}} for i in range(60)]
    TestClient(gw.app).post("/mcp", content=json.dumps(batch), headers=AUTH)
    (rec,) = _lines(audit)
    assert IDENT_SECRET not in audit.read_text(encoding="utf-8")
    assert set(rec["tools"]) == {"<unknown>"} and len(rec["tools"]) == 50


def test_known_names_still_logged(audit, upstream):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    TestClient(gw.app).post("/mcp", content=body, headers=AUTH)
    TestClient(gw.app).post("/mcp", content=_call("search_thoughts", {"query": "q"}), headers=AUTH)
    a, b = _lines(audit)
    assert a["methods"] == ["tools/list"]
    assert b["methods"] == ["tools/call"] and b["tools"] == ["search_thoughts"]
