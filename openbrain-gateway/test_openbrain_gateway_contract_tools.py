"""rw-p0-contract: the retrieval contract's read tools on the cloud door.

The anchor adds the contract's MCP tools (OB1 integrations/kubernetes-deployment/
contract-tools.json, generated from the workbench's OpenAPI document) to ALLOWED_TOOLS.
What must hold:
  * every contract tool is allowed, listed and POLICED AS A READ: the forced share=cloud
    filter reaches the tool whatever the caller sends (the workbench fails closed on it);
  * the pre-contract READ_TOOLS / WRITE_TOOLS defaults are unchanged;
  * GATEWAY_CONTRACT_TOOLS="" removes them (the ops door sets it);
  * no write is among them (D14: reads only).

The upstream is a stub (httpx.MockTransport). Run: python -m pytest openbrain-gateway -q
"""
import importlib.util
import json
import os
import pathlib

import httpx
import pytest
from starlette.testclient import TestClient

GATEWAY_KEY = "gw-test-key"
os.environ.setdefault("OPENBRAIN_URL", "http://stub-openbrain:8000")
os.environ["OPENBRAIN_KEY"] = "upstream-test-key"
os.environ["GATEWAY_KEY"] = GATEWAY_KEY

_APP = pathlib.Path(__file__).with_name("app.py")
_MANIFEST = pathlib.Path(__file__).parents[1] / "OB1/integrations/kubernetes-deployment/contract-tools.json"
AUTH = {"authorization": f"Bearer {GATEWAY_KEY}", "content-type": "application/json"}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _APP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gw = _load("openbrain_gateway_app_contract")


def _manifest_tools():
    if not _MANIFEST.exists():
        pytest.skip("OB1 submodule not checked out")
    return {t["tool"] for t in json.loads(_MANIFEST.read_text(encoding="utf-8"))}


def _call(name, arguments, rpc_id=1):
    return json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
                       "params": {"name": name, "arguments": arguments}})


@pytest.fixture
def upstream(monkeypatch):
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.content)
        body = json.loads(request.content)
        if body.get("method") == "tools/list":
            tools = [{"name": n, "inputSchema": {}} for n in sorted(_manifest_tools())] + [
                {"name": "thought_stats", "inputSchema": {}}, {"name": "agent_memory_recall", "inputSchema": {}}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": tools}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body.get("id"), "result": {}})

    real = httpx.AsyncClient

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(gw.httpx, "AsyncClient", factory)
    return seen


def test_every_contract_tool_is_allowed_and_the_old_defaults_are_unchanged():
    tools = _manifest_tools()
    assert len(tools) >= 20
    assert tools == gw.CONTRACT_TOOLS, sorted(tools ^ gw.CONTRACT_TOOLS)
    assert tools <= gw.ALLOWED_TOOLS
    assert gw.READ_TOOLS == {"search", "fetch", "search_thoughts", "list_thoughts"}
    assert gw.WRITE_TOOLS == {"capture_thought", "ingest_url", "ingest_urls"}
    assert not (gw.CONTRACT_TOOLS & gw.WRITE_TOOLS)


def test_tools_list_advertises_the_contract_tools_and_still_hides_the_rest(upstream):
    r = TestClient(gw.app).post("/mcp", content=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}),
                                headers=AUTH)
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["result"]["tools"]}
    assert _manifest_tools() <= names
    assert "thought_stats" not in names and "agent_memory_recall" not in names


@pytest.mark.parametrize("sent", [None, {}, {"share": "personal"}, {"share": "cloud", "x": 1}])
def test_a_contract_call_carries_the_forced_cloud_filter(upstream, sent):
    args = {"id": "11111111-1111-4111-8111-111111111111"}
    if sent is not None:
        args["metadata_filter"] = sent
    r = TestClient(gw.app).post("/mcp", content=_call("contract_reference", args), headers=AUTH)
    assert r.status_code == 200
    fwd = json.loads(upstream[-1])["params"]["arguments"]
    assert fwd["metadata_filter"]["share"] == "cloud"


def test_a_non_object_filter_on_a_contract_tool_is_refused(upstream):
    r = TestClient(gw.app).post("/mcp", content=_call("contract_search", {"q": "x", "metadata_filter": [1]}),
                                headers=AUTH)
    assert r.json()["error"]["code"] == -32602
    assert upstream == []


def test_contract_tools_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("GATEWAY_CONTRACT_TOOLS", "")
    off = _load("openbrain_gateway_app_contract_off")
    assert off.CONTRACT_TOOLS == set()
    assert "contract_reference" not in off.ALLOWED_TOOLS


def test_no_contract_tool_is_a_write():
    manifest = json.loads(_MANIFEST.read_text(encoding="utf-8")) if _MANIFEST.exists() else []
    for t in manifest:
        assert t["operationId"] not in {"suppress", "sever", "erase", "admission", "liftTombstone", "setHidden",
                                         "addUserSource", "removeUserSource", "writeSetting", "steer"}, t["tool"]
