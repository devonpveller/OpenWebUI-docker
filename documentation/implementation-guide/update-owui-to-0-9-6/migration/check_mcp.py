import os, json, urllib.request
URL=os.environ.get("OPENBRAIN_MCP_URL","http://openbrain-mcp:8000")
KEY=os.environ["MCP_ACCESS_KEY"]
SID=None
def rpc(method, params, notif=False):
    global SID
    payload={"jsonrpc":"2.0","method":method,"params":params}
    if not notif: payload["id"]=1
    data=json.dumps(payload).encode()
    h={"x-brain-key":KEY,"content-type":"application/json",
       "accept":"application/json, text/event-stream"}
    if SID: h["mcp-session-id"]=SID
    req=urllib.request.Request(URL, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        sid=r.headers.get("mcp-session-id")
        if sid: SID=sid
        ct=r.headers.get("content-type","")
        raw=r.read().decode()
        if notif: return r.status, None
        if "text/event-stream" in ct:           # parse SSE: take last data: line
            obj=None
            for line in raw.splitlines():
                if line.startswith("data:"):
                    obj=json.loads(line[5:].strip())
            return r.status, obj
        return r.status, (json.loads(raw) if raw.strip() else None)
try:
    st,body=rpc("initialize",{"protocolVersion":"2025-06-18",
        "capabilities":{},"clientInfo":{"name":"owui-migration","version":"1"}})
    print("initialize HTTP",st,"session",SID)
    try: rpc("notifications/initialized",{},notif=True)
    except Exception as e: print("  (initialized notif:",repr(e)[:80],")")
    st,body=rpc("tools/list",{})
    tools=sorted(t["name"] for t in body.get("result",{}).get("tools",[]))
    print("TOOL COUNT",len(tools))
    for need in ["create_thread","capture_with_thread","list_threads","get_thread_sources","ingest_url"]:
        print(("  OK " if need in tools else "  MISSING ")+need)
    print("ALL:",tools)
except urllib.error.HTTPError as e:
    print("HTTPError",e.code,e.read().decode()[:300])
except Exception as e:
    print("ERR",repr(e))
