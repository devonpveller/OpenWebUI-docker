"""Probe for smoke-openbrain-personal-lane.ps1 (item amp-owui-deny). Runs INSIDE the rig's
--internal network (in the rig's mcpo image, which ships python + httpx), because an internal
network publishes no host port. It only CALLS and REPORTS: one JSON object on stdout. The
PowerShell rig makes every assertion and every database count, so a probe bug cannot turn
into a PASS by itself.

  python personal_lane_probe.py owui   # the OWUI path: mcpo, and the key mcpo holds
  python personal_lane_probe.py ops    # the ops door (an openbrain-gateway, ops profile)
"""
from __future__ import annotations

import json
import os
import sys

import httpx

SEVEN = [
    "agent_memory_writeback",
    "agent_memory_recall",
    "agent_memory_review",
    "agent_memory_inspect",
    "agent_memory_list_review_queue",
    "agent_memory_report_usage",
    "agent_memory_recall_trace",
]
WS = "ws-amp-owui"
# Minimal, schema-valid-enough arguments per tool. On the personal lane none may reach a
# handler; on a working lane they exercise it.
ARGS = {
    "agent_memory_recall": {"workspace_id": WS, "query": "probe", "limit": 2},
    "agent_memory_review": {"memory_id": "00000000-0000-0000-0000-000000000000", "action": "reject",
                            "reviewer": "probe", "note": "probe"},
    "agent_memory_inspect": {"memory_id": "00000000-0000-0000-0000-000000000000"},
    "agent_memory_list_review_queue": {"workspace_id": WS},
    "agent_memory_report_usage": {"memory_id": "00000000-0000-0000-0000-000000000000", "used": False,
                                  "workspace_id": WS},
    "agent_memory_recall_trace": {"trace_id": "00000000-0000-0000-0000-000000000000"},
}


def writeback_args(path: str) -> dict:
    return {
        "workspace_id": WS,
        "project_id": "proj-amp-owui",
        "summary": f"amp-owui-deny probe via {path}",
        "content": f"a lesson written by the amp-owui-deny rig through the {path} path",
        "memory_type": "lesson",
        "idempotency_key": f"amp-owui-{path}",
    }


def parse(r: httpx.Response):
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        found = None
        for line in r.text.splitlines():
            if line.startswith("data:"):
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, (dict, list)):
                    found = msg
        return found
    try:
        return r.json()
    except ValueError:
        return r.text[:600]


def rpc(url: str, headers: dict, method: str, params: dict | None = None) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **headers}
    try:
        r = httpx.post(url, headers=h, json=body, timeout=60)
        return {"status": r.status_code, "msg": parse(r)}
    except Exception as exc:  # noqa: BLE001 - reported, judged by the rig
        return {"status": -1, "msg": str(exc)}


def tool_names(res: dict) -> list:
    msg = res.get("msg")
    if isinstance(msg, dict) and isinstance(msg.get("result"), dict):
        return [t.get("name") for t in msg["result"].get("tools", [])]
    return []


def owui() -> dict:
    mcpo = os.environ["MCPO_URL"].rstrip("/")
    bearer = {"Authorization": f"Bearer {os.environ['MCPO_API_KEY']}"}
    raw = os.environ["RAW_URL"]
    lane = {"x-brain-key": os.environ["LANE_KEY"]}
    out: dict = {}

    r = httpx.get(mcpo + "/openapi.json", headers=bearer, timeout=30)
    out["openapi_status"] = r.status_code
    paths = list((r.json() or {}).get("paths", {}).keys()) if r.status_code == 200 else []
    out["openapi_tools"] = sorted(p.strip("/") for p in paths)

    r = httpx.post(mcpo + "/agent_memory_writeback", headers=bearer, json=writeback_args("owui-mcpo"), timeout=60)
    out["mcpo_writeback"] = {"status": r.status_code, "body": r.text[:600]}

    r = httpx.post(mcpo + "/list_thoughts", headers=bearer, json={"limit": 2}, timeout=60)
    out["mcpo_list_thoughts"] = {"status": r.status_code, "body": r.text[:300]}

    # The same server, with the very credential mcpo holds, and no mcpo in between: what an
    # OWUI-side edit that pointed a tool server straight at openbrain-mcp would get.
    out["lane_tools"] = tool_names(rpc(raw, lane, "tools/list"))
    calls = {"agent_memory_writeback": rpc(raw, lane, "tools/call",
                                           {"name": "agent_memory_writeback",
                                            "arguments": writeback_args("owui-direct")})}
    for t in SEVEN[1:]:
        calls[t] = rpc(raw, lane, "tools/call", {"name": t, "arguments": ARGS[t]})
    out["lane_calls"] = calls
    # The REST twins with the same key: their guard is MCP_ACCESS_KEY only.
    rest = {}
    for path, body in (("/agent-memory/writeback", writeback_args("owui-rest")),
                       ("/agent-memory/recall", ARGS["agent_memory_recall"])):
        try:
            r = httpx.post(raw.rstrip("/") + path, headers=lane, json=body, timeout=60)
            rest[path] = r.status_code
        except Exception as exc:  # noqa: BLE001
            rest[path] = str(exc)
    out["lane_rest"] = rest
    out["lane_batch"] = None
    try:
        h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **lane}
        r = httpx.post(raw, headers=h, timeout=60, json=[
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
             "params": {"name": "agent_memory_writeback", "arguments": writeback_args("owui-batch")}},
        ])
        out["lane_batch"] = {"status": r.status_code, "msg": parse(r)}
    except Exception as exc:  # noqa: BLE001
        out["lane_batch"] = {"status": -1, "msg": str(exc)}
    return out


def ops() -> dict:
    gw = os.environ["GW_URL"]
    auth = {"Authorization": f"Bearer {os.environ['GW_KEY']}"}
    out: dict = {"gw_tools": tool_names(rpc(gw, auth, "tools/list"))}
    out["gw_writeback"] = rpc(gw, auth, "tools/call",
                              {"name": "agent_memory_writeback", "arguments": writeback_args("ops-door")})
    out["gw_queue"] = rpc(gw, auth, "tools/call",
                          {"name": "agent_memory_list_review_queue", "arguments": ARGS["agent_memory_list_review_queue"]})
    return out


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"owui": owui, "ops": ops}.get(stage)
    if not fn:
        print(json.dumps({"error": f"unknown stage {stage!r}"}))
        sys.exit(2)
    print(json.dumps(fn()))
