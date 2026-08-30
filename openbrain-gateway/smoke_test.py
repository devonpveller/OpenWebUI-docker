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

BOTH PROFILES (memory-plane PLAN §1.4). This image now runs as more than one door:

  --profile cloud   :8061, the existing door. Unchanged behaviour.
  --profile ops     :8062, agent-memory tools for host processes.
  --defaults        no server needed. Asserts that app.py's env-configurable
                    allowlists and forced filter/stamp still DEFAULT to exactly
                    what the cloud door had before they were parameterized.

The --defaults check is the one that matters most. The cloud door is live, and §1.4
requires the existing instance to need no env change, so a drifted default is a
containment failure rather than a bug - and it is the kind that shows up as data on the
wrong side of a boundary, not as an error.

Run from the host: python openbrain-gateway/smoke_test.py
                     [--profile cloud|ops] [--defaults] [--boundaries]
"""
import json
import os
import sys
import subprocess
import uuid

import httpx

PROFILE = "cloud"
for _i, _a in enumerate(sys.argv):
    if _a == "--profile" and _i + 1 < len(sys.argv):
        PROFILE = sys.argv[_i + 1]

_PORTS = {"cloud": 8061, "ops": 8062}
if PROFILE not in _PORTS:
    sys.exit(f"unknown profile {PROFILE!r} - expected one of {sorted(_PORTS)}")
GATEWAY = f"http://127.0.0.1:{_PORTS[PROFILE]}"

# Each door has its OWN key. Reading one key for both would make a pass on the cloud door
# look like a pass on the ops door.
_KEY_VAR = "OPENBRAIN_GATEWAY_KEY" if PROFILE == "cloud" else "OPS_GATEWAY_KEY"
KEY = os.environ.get(_KEY_VAR, "")
if not KEY and "--defaults" not in sys.argv:
    sys.exit(
        f"{_KEY_VAR} is not set. Export it from OB1/docker/.env "
        "before running (never hardcode it here - this file is tracked)."
    )

PASS = "OK"
FAIL = "FAIL"

# A COLD MODEL LANE IS NOT A FAILURE.
#
# capture_thought runs metadata extraction through the chat lane, and a cold
# qwen36-27b takes ~28s to answer its first request (measured: 27.7s from inside
# openbrain-mcp; the embedding lane, by contrast, answers in 77ms). At the old 30s
# client timeout this test failed on the first run after any inference restart and
# passed on the second - which is the cry-wolf failure: whoever runs it learns that
# a red result means "run it again".
#
# 120s is chosen to clear a cold load with room, not to hide a hang. A real hang
# still fails, four times slower.
CHAT_LANE_TIMEOUT = 120.0


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


def check_defaults():
    """Import app.py with NO gateway env overrides and assert the cloud values.

    §1.4: "defaults preserving current cloud behavior byte-for-byte (existing instance needs
    no env change)". These literals are the ones that were hardcoded before the
    parameterization; if an edit ever changes a default, this fails rather than the cloud
    door quietly serving a different allow-list.
    """
    import importlib

    failures = []

    def check(ok, label, detail=""):
        print(f"  [{PASS if ok else FAIL}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    # Clear every gateway knob, then supply only the three the module requires to import.
    saved = {}
    for var in list(os.environ):
        if var.startswith("GATEWAY_") or var in ("SHARE_LABEL_VALUE",):
            saved[var] = os.environ.pop(var)
    os.environ.setdefault("OPENBRAIN_URL", "http://openbrain-mcp:8000")
    os.environ.setdefault("OPENBRAIN_KEY", "x")
    os.environ.setdefault("GATEWAY_KEY", "x")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        app = importlib.import_module("app")
        importlib.reload(app)
        print("[defaults] app.py with no gateway env set")
        check(app.READ_TOOLS == {"search", "fetch", "search_thoughts", "list_thoughts"},
              "READ_TOOLS default unchanged", str(sorted(app.READ_TOOLS)))
        check(app.WRITE_TOOLS == {"capture_thought", "ingest_url", "ingest_urls"},
              "WRITE_TOOLS default unchanged", str(sorted(app.WRITE_TOOLS)))
        check(app.ALLOWED_TOOLS == app.READ_TOOLS | app.WRITE_TOOLS,
              "ALLOWED_TOOLS is still the union")
        check(app.READ_FILTER_FIELD == "share" and app.READ_FILTER_VALUE == "cloud",
              "forced read filter is still share=cloud",
              f"{app.READ_FILTER_FIELD}={app.READ_FILTER_VALUE}")
        check(app.WRITE_ORIGIN == "cloud" and app.WRITE_STAMP_FIELD == "share"
              and app.WRITE_STAMP_VALUE == "cloud",
              "forced write stamp is still origin=cloud share=cloud")
        check(app.GATEWAY_PROFILE == "cloud", "profile defaults to cloud")

        # And the functions, not just the constants - a default is only preserved if the
        # thing that uses it produces the same output.
        check(app._force_read_filter({}) == {"metadata_filter": {"share": "cloud"}},
              "_force_read_filter output byte-for-byte")
        check(app._force_write_extra({}) == {"metadata_extra": {"origin": "cloud", "share": "cloud"}},
              "_force_write_extra output byte-for-byte")
        # A caller-supplied value must still be overridden, not merged around.
        check(app._force_read_filter({"metadata_filter": {"share": "local"}})
              == {"metadata_filter": {"share": "cloud"}},
              "a pivot attempt is still overridden")

        # An EMPTY override means empty, not "fall back to the default" - a profile that
        # allows no writes has to be able to say so.
        os.environ["GATEWAY_WRITE_TOOLS"] = ""
        importlib.reload(app)
        check(app.WRITE_TOOLS == set(), "an empty override means EMPTY, not default")
    finally:
        os.environ.pop("GATEWAY_WRITE_TOOLS", None)
        os.environ.update(saved)

    print()
    if failures:
        print(f"{len(failures)} DEFAULT CHECK(S) FAILED: {failures}")
        return 1
    print("cloud defaults preserved byte-for-byte")
    return 0


def check_boundaries():
    """The CROSS-DOOR negatives and positives (PLAN §1.3, §1.4).

    These are the assertions that make the two doors mean something, and none of them can be
    made from inside one door: each is about what the OTHER door must not do.

      - agent_memory_* is DENIED on the cloud door (:8061). The cloud allow-list is
        default-deny, so this should hold automatically - which is exactly why it is worth
        asserting, because "automatic" is what nobody checks.
      - the cloud door's tools/list does not even ADVERTISE them.
      - the ops door (:8062) advertises ONLY agent_memory_* - no search, no fetch, no
        capture_thought. It is not a second cloud door.
      - cloud search_thoughts does not surface agent-memory thoughts: those are written
        with no share='cloud' label, and the cloud door forces share=cloud on reads.

    Needs BOTH keys and both doors up.
    """
    failures = []

    def check(ok, label, detail=""):
        print(f"  [{PASS if ok else FAIL}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    cloud_key = os.environ.get("OPENBRAIN_GATEWAY_KEY", "")
    ops_key = os.environ.get("OPS_GATEWAY_KEY", "")
    if not cloud_key or not ops_key:
        sys.exit("--boundaries needs BOTH OPENBRAIN_GATEWAY_KEY and OPS_GATEWAY_KEY exported.")

    AGENT_MEMORY = [
        "agent_memory_writeback", "agent_memory_recall", "agent_memory_review",
        "agent_memory_list_review_queue", "agent_memory_inspect",
        "agent_memory_recall_trace", "agent_memory_report_usage",
    ]

    def tools_on(url, key):
        with httpx.Client(timeout=CHAT_LANE_TIMEOUT) as c:
            r = c.post(
                f"{url}/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
            )
        body = parse_response(r)
        return {t["name"] for t in (body.get("result", {}).get("tools") or [])}

    def call_on(url, key, tool):
        with httpx.Client(timeout=CHAT_LANE_TIMEOUT) as c:
            r = c.post(
                f"{url}/mcp",
                content=json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                    "params": {"name": tool, "arguments": {}}}),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                         "Accept": "application/json, text/event-stream"},
            )
        return parse_response(r)

    print("[boundaries] the cloud door must NOT reach agent-memory")
    cloud_tools = tools_on("http://127.0.0.1:8061", cloud_key)
    leaked = sorted(set(AGENT_MEMORY) & cloud_tools)
    check(not leaked, "cloud tools/list does not advertise agent_memory_*", str(leaked))
    denied = call_on("http://127.0.0.1:8061", cloud_key, "agent_memory_recall")
    check(denied.get("error", {}).get("code") == -32601,
          "agent_memory_recall on the cloud door is DENIED (-32601)",
          json.dumps(denied)[:160])

    print("[boundaries] the ops door serves agent-memory AND NOTHING ELSE")
    ops_tools = tools_on("http://127.0.0.1:8062", ops_key)
    check(ops_tools <= set(AGENT_MEMORY),
          "ops tools/list advertises only agent_memory_*", str(sorted(ops_tools - set(AGENT_MEMORY))))
    check("agent_memory_recall" in ops_tools, "ops door advertises agent_memory_recall")
    for forbidden in ("search", "fetch", "capture_thought", "search_thoughts"):
        check(forbidden not in ops_tools, f"ops door does not advertise {forbidden}")
        err = call_on("http://127.0.0.1:8062", ops_key, forbidden)
        check(err.get("error", {}).get("code") == -32601,
              f"{forbidden} on the ops door is DENIED (-32601)")

    print("[boundaries] the two keys are not interchangeable")
    with httpx.Client(timeout=CHAT_LANE_TIMEOUT) as c:
        r = c.post("http://127.0.0.1:8062/mcp",
                   content=json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/list"}),
                   headers={"Authorization": f"Bearer {cloud_key}",
                            "Content-Type": "application/json",
                            "Accept": "application/json, text/event-stream"})
    check(r.status_code == 401, "the CLOUD key is rejected by the ops door", f"status={r.status_code}")

    print()
    if failures:
        print(f"{len(failures)} BOUNDARY CHECK(S) FAILED: {failures}")
        return 1
    print("both doors hold their boundaries")
    return 0


def main():
    if "--defaults" in sys.argv:
        return check_defaults()
    if "--boundaries" in sys.argv:
        return check_boundaries()

    failures = []

    def check(ok, label, detail=""):
        mark = PASS if ok else FAIL
        print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    with httpx.Client(timeout=CHAT_LANE_TIMEOUT) as c:
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
    # sys.exit(main()), not a bare main(). The live path already calls sys.exit(1) itself,
    # but check_defaults RETURNS its code - so a bare call would print failures and exit 0,
    # and CI would read a failed default check as a pass. A check that cannot fail the
    # process is not a check.
    sys.exit(main() or 0)
