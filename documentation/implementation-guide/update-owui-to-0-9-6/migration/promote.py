#!/usr/bin/env python3
"""Step 3 - Promote promote.csv into Open Brain (OB1).

Talks to the openbrain-mcp Streamable-HTTP MCP server with stdlib only (no pip):
  initialize handshake -> notifications/initialized -> tools/call, parsing the
  result envelope result.content[0].text (JSON string) and SSE responses.

Modes:
  --dry-run   OFFLINE. Reads promote.csv + webui.live.db, prints the plan. No network.
  --check     Handshake + list_threads, prints existing marker reuse map.
  (apply)     REAL run. Per collection: reuse-or-create a thread (idempotent via an
              [owui_collection_id:<id>] marker in the thread description), then
              capture_with_thread per file. Logs each row to promote.log; re-runs
              skip file_ids already logged ok (OB1 content-hash dedup is the backstop).

Run apply on the ai-stack_llm-net network so http://openbrain-mcp:8000 resolves:
  docker run --rm --network ai-stack_llm-net -e MCP_ACCESS_KEY=$key `
    -v "<migration dir>:/work" -w /work python:3.12-slim python promote.py
"""
import csv, sys, os, json, sqlite3, datetime, urllib.request, urllib.error

DRY   = "--dry-run" in sys.argv
CHECK = "--check" in sys.argv
URL   = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8000")
KEY   = os.environ.get("MCP_ACCESS_KEY", "")
DBP   = os.environ.get("WEBUI_DB", "webui.live.db")
MIGRATED_ON = os.environ.get("MIGRATED_ON", datetime.date.today().isoformat())
MARK  = "owui_collection_id:"          # idempotency marker embedded in thread.description
_SID  = {"id": None}

# ---------------------------------------------------------------- MCP client
def _rpc(method, params, notif=False):
    payload = {"jsonrpc": "2.0", "method": method, "params": params}
    if not notif:
        payload["id"] = 1
    h = {"x-brain-key": KEY, "content-type": "application/json",
         "accept": "application/json, text/event-stream"}
    if _SID["id"]:
        h["mcp-session-id"] = _SID["id"]
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        sid = r.headers.get("mcp-session-id")
        if sid:
            _SID["id"] = sid
        if notif:
            return None
        ct, raw = r.headers.get("content-type", ""), r.read().decode()
        if "text/event-stream" in ct:
            obj = None
            for line in raw.splitlines():
                if line.startswith("data:"):
                    obj = json.loads(line[5:].strip())
            return obj
        return json.loads(raw) if raw.strip() else None

def mcp_init():
    _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                        "clientInfo": {"name": "owui-migration", "version": "1"}})
    try:
        _rpc("notifications/initialized", {}, notif=True)
    except Exception:
        pass

def call(tool, args):
    """tools/call -> parsed inner object (dict)."""
    env = _rpc("tools/call", {"name": tool, "arguments": args})
    if env is None:
        raise RuntimeError("empty response")
    if "error" in env:
        raise RuntimeError(json.dumps(env["error"])[:300])
    res = env.get("result", env)
    if res.get("isError"):
        raise RuntimeError("tool error: " + json.dumps(res.get("content"))[:300])
    content = res.get("content") or []
    for c in content:
        if c.get("type") == "text":
            txt = c.get("text", "")
            try:
                return json.loads(txt)
            except Exception:
                return {"_text": txt}
    return res

# ---------------------------------------------------------------- content read
def content_for(file_id, con):
    row = con.execute("SELECT data FROM file WHERE id=?", (file_id,)).fetchone()
    if not row or not row[0]:
        return ""
    try:
        return (json.loads(row[0]) or {}).get("content") or ""
    except Exception:
        return ""

# ---------------------------------------------------------------- load contract
rows = list(csv.DictReader(open("promote.csv", encoding="utf-8")))
by_coll = {}
for r in rows:
    by_coll.setdefault((r["collection_id"], r["collection_name"]), []).append(r)
con = sqlite3.connect(DBP)

# ---------------------------------------------------------------- dry-run
if DRY:
    tot_files = tot_chars = 0
    for (cid, cname), fs in sorted(by_coll.items(), key=lambda kv: kv[0][1].lower()):
        chars = sum(int(f["content_chars"]) for f in fs)
        tot_files += len(fs); tot_chars += chars
        print(f"THREAD  <{cname}>  [{MARK}{cid}]  + {len(fs)} sources, ~{chars:,} chars")
    print("-" * 60)
    print(f"PLAN: {len(by_coll)} threads, {tot_files} sources, ~{tot_chars:,} chars")
    print(f"content_type=manual  provenance=owui-migration  migrated_on={MIGRATED_ON}")
    print("(offline plan - no network calls made)")
    sys.exit(0)

if not KEY:
    sys.exit("MCP_ACCESS_KEY not set - refusing to talk to OB1.")
mcp_init()

# existing threads -> marker map (idempotent reuse)
existing = {}
for t in call("list_threads", {"status": "all"}).get("threads", []):
    desc = t.get("description") or ""
    if MARK in desc:
        cid = desc.split(MARK, 1)[1].split("]", 1)[0].strip()
        existing[cid] = t["id"]

if CHECK:
    print(f"OB1 reachable. existing migrated threads (by marker): {len(existing)}")
    for cid, tid in existing.items():
        print(f"  {cid} -> {tid}")
    sys.exit(0)

# resume set from prior log
done = set()
if os.path.exists("promote.log"):
    for line in open("promote.log", encoding="utf-8"):
        try:
            ev = json.loads(line)
            if ev.get("ok"):
                done.add(ev["file_id"])
        except Exception:
            pass

log = open("promote.log", "a", encoding="utf-8")
def emit(ev): log.write(json.dumps(ev) + "\n"); log.flush()

created = linked = deduped = skipped = failed = 0
for (cid, cname), fs in sorted(by_coll.items(), key=lambda kv: kv[0][1].lower()):
    tid = existing.get(cid)
    if not tid:
        desc = f"Migrated from Open WebUI knowledge collection. [{MARK}{cid}]"
        tid = call("create_thread", {"name": cname, "description": desc})["id"]
        existing[cid] = tid; created += 1
        emit({"event": "create_thread", "collection_id": cid, "thread_id": tid, "name": cname})
        print(f"thread created: {cname} -> {tid}")
    for f in fs:
        fid = f["file_id"]
        if fid in done:
            skipped += 1; continue
        content = content_for(fid, con)
        if not content.strip():
            emit({"file_id": fid, "ok": False, "err": "empty_at_read"}); failed += 1; continue
        try:
            res = call("capture_with_thread", {
                "thread_id": tid, "content": content,
                "title": f["filename"], "content_type": "manual",
                "metadata_extra": {
                    "source": "owui-migration", "owui_file_id": fid,
                    "owui_collection_id": cid, "owui_collection_name": cname,
                    "owui_content_type": f["owui_content_type"], "migrated_on": MIGRATED_ON,
                }})
            dup = bool(res.get("was_duplicate"))
            deduped += dup; linked += 1
            emit({"file_id": fid, "ok": True, "source_id": res.get("source_id"),
                  "thread_id": tid, "was_duplicate": dup})
        except Exception as e:
            failed += 1
            emit({"file_id": fid, "ok": False, "err": str(e)[:300]})
            print(f"  FAIL {fid} ({f['filename'][:40]}): {str(e)[:120]}")
print("-" * 60)
print(f"threads_created={created}  sources_linked={linked} (dedup={deduped})  "
      f"skipped_resume={skipped}  failed={failed}")
print("full per-file record in promote.log")
