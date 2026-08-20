"""End-to-end smoke for openbrain-gateway.

Tests (cloud client perspective):
  1. /health responds ok (no auth)
  2. Wrong Bearer -> 401
  3. tools/list returns only the cloud allow-list (no thought_stats,
     no extension tools)
  4. Blocked tool (thought_stats) returns JSON-RPC -32601
  5. capture_thought succeeds and the stored row has metadata.origin=
     cloud, share=cloud (verified directly in postgres)
  6. search_thoughts returns the cloud-stamped row
  7. Pivot attack: caller sends metadata_filter={"share":"local"} ->
     gateway overrides to share=cloud, no leak

Run from the host: python openbrain-gateway/smoke_test.py
"""
import json
import os
import sys
import subprocess
import uuid

import httpx

GATEWAY = "http://127.0.0.1:8061"
KEY = os.environ.get("OPENBRAIN_GATEWAY_KEY", "")
if not KEY:
    sys.exit(
        "OPENBRAIN_GATEWAY_KEY is not set. Export it from OB1/docker/.env "
        "before running (never hardcode it here - this file is tracked)."
    )

PASS = "OK"
FAIL = "FAIL"


def jrpc(client, sid, method, params=None, id_=None):
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    headers = {
        "Authorization": f"Bearer {KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if sid:
        headers["mcp-session-id"] = sid
    r = client.post(f"{GATEWAY}/mcp", content=json.dumps(body), headers=headers)
    return r


def parse_sse(text):
    # Some MCP servers respond with event-stream; we want the data lines.
    out = []
    for line in text.splitlines():
        if line.startswith("data:"):
            try:
                out.append(json.loads(line[5:].strip()))
            except Exception:
                pass
    return out


def parse_response(r):
    ct = r.headers.get("content-type", "")
    if "text/event-stream" in ct:
        msgs = parse_sse(r.text)
        return msgs[0] if msgs else None
    try:
        return r.json()
    except Exception:
        return None


def main():
    failures = []

    def check(ok, label, detail=""):
        mark = PASS if ok else FAIL
        print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    with httpx.Client(timeout=30.0) as c:
        # 1. health
        print("[1] /health (no auth)")
        r = c.get(f"{GATEWAY}/health")
        check(r.status_code == 200 and r.text.strip() == "ok",
              "200 ok", f"status={r.status_code} body={r.text!r}")

        # 2. wrong bearer
        print("[2] wrong Bearer -> 401")
        r = c.post(
            f"{GATEWAY}/mcp",
            content=json.dumps({"jsonrpc": "2.0", "id": 0,
                                "method": "tools/list"}),
            headers={"Authorization": "Bearer wrong-key",
                     "Content-Type": "application/json",
                     "Accept": "application/json"})
        check(r.status_code == 401, "401 unauthorized",
              f"status={r.status_code}")

        # MCP handshake
        print("[3] initialize handshake")
        r = jrpc(c, None, "initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0.1"}
        }, id_=1)
        sid = r.headers.get("mcp-session-id")
        msg = parse_response(r)
        check(r.status_code == 200 and msg and "result" in msg,
              "initialize ok",
              f"status={r.status_code} sid={sid!r}")
        # openbrain-mcp runs StreamableHTTPTransport per-request (stateless),
        # so no mcp-session-id is returned and subsequent calls don't need
        # one. The initialized notification is still polite to send.
        jrpc(c, sid, "notifications/initialized", {})

        # 4. tools/list filtered
        print("[4] tools/list filtered to allow-list")
        r = jrpc(c, sid, "tools/list", {}, id_=2)
        msg = parse_response(r)
        tools = (msg or {}).get("result", {}).get("tools", []) or []
        names = sorted(t.get("name") for t in tools)
        expected = sorted(["search", "fetch", "search_thoughts",
                           "list_thoughts", "capture_thought",
                           "ingest_url", "ingest_urls"])
        check(names == expected, "exact cloud allow-list",
              f"got={names}")
        check("thought_stats" not in names, "thought_stats absent")

        # 5. blocked tool
        print("[5] thought_stats -> -32601")
        r = jrpc(c, sid, "tools/call",
                 {"name": "thought_stats", "arguments": {}}, id_=3)
        msg = parse_response(r)
        err = (msg or {}).get("error") or {}
        check(err.get("code") == -32601,
              "JSON-RPC -32601 for blocked tool",
              f"err={err}")

        # 6. capture_thought
        marker = f"smoke-{uuid.uuid4().hex[:8]}"
        print(f"[6] capture_thought (marker={marker})")
        r = jrpc(c, sid, "tools/call",
                 {"name": "capture_thought",
                  "arguments": {"content": f"Cloud privacy smoke test {marker}"}},
                 id_=4)
        msg = parse_response(r)
        result = (msg or {}).get("result") or {}
        is_err = result.get("isError", False)
        check(not is_err and "content" in result,
              "capture_thought succeeded",
              f"result={result}")

        # 7. verify row in postgres has metadata.share=cloud
        print("[7] DB check: stored row has metadata.share=cloud")
        sql = (
            "SELECT metadata->>'share' AS share, "
            "metadata->>'origin' AS origin "
            "FROM thoughts WHERE content ILIKE '%" + marker + "%' "
            "ORDER BY created_at DESC LIMIT 1;"
        )
        out = subprocess.run(
            ["docker", "exec", "openbrain-db", "psql", "-U", "postgres",
             "-d", "openbrain", "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=15)
        row = (out.stdout or "").strip()
        check(row == "cloud|cloud",
              "metadata.share=cloud, metadata.origin=cloud",
              f"row={row!r} stderr={out.stderr.strip()!r}")

        # 8. search_thoughts finds it (cloud-zone read)
        print("[8] search_thoughts finds the cloud-stamped row")
        r = jrpc(c, sid, "tools/call",
                 {"name": "search_thoughts",
                  "arguments": {"query": marker, "threshold": 0.3,
                                "limit": 5}}, id_=5)
        msg = parse_response(r)
        result = (msg or {}).get("result") or {}
        text = ""
        for blk in result.get("content", []):
            if blk.get("type") == "text":
                text += blk.get("text", "")
        check(marker in text, "marker appears in cloud read",
              f"len={len(text)}")

        # 9. pivot attack: client tries to override share to "local"
        print("[9] pivot attack: client metadata_filter share=local")
        r = jrpc(c, sid, "tools/call",
                 {"name": "search_thoughts",
                  "arguments": {"query": marker, "threshold": 0.3,
                                "limit": 5,
                                "metadata_filter": {"share": "local"}}},
                 id_=6)
        msg = parse_response(r)
        result = (msg or {}).get("result") or {}
        text = ""
        for blk in result.get("content", []):
            if blk.get("type") == "text":
                text += blk.get("text", "")
        # The cloud-stamped marker should STILL appear because gateway
        # forces share=cloud regardless of what the caller asks for.
        check(marker in text,
              "gateway forced share=cloud despite caller override",
              f"len={len(text)}")

        # 10. local zone unaffected: count thoughts visible inside obnet
        # vs cloud-visible. Internal direct query bypasses the gateway.
        print("[10] local zone: direct internal SQL sees all thoughts "
              "(no metadata.share filter)")
        out = subprocess.run(
            ["docker", "exec", "openbrain-db", "psql", "-U", "postgres",
             "-d", "openbrain", "-t", "-A", "-c",
             "SELECT COUNT(*) FROM thoughts;"],
            capture_output=True, text=True, timeout=15)
        total = int((out.stdout or "0").strip() or 0)
        out2 = subprocess.run(
            ["docker", "exec", "openbrain-db", "psql", "-U", "postgres",
             "-d", "openbrain", "-t", "-A", "-c",
             "SELECT COUNT(*) FROM thoughts WHERE metadata @> "
             "'{\"share\":\"cloud\"}'::jsonb;"],
            capture_output=True, text=True, timeout=15)
        cloud_count = int((out2.stdout or "0").strip() or 0)
        check(total >= cloud_count and total >= 1,
              f"local sees {total} thoughts, cloud-visible={cloud_count}")

    print()
    if failures:
        print(f"{FAIL} {len(failures)} failed:")
        for f in failures:
            print(f"   - {f}")
        sys.exit(1)
    print(f"{PASS} all smoke tests passed")


if __name__ == "__main__":
    main()
