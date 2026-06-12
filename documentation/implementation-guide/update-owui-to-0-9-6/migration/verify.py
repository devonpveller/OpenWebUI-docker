import os, json, urllib.request
URL=os.environ.get("OPENBRAIN_MCP_URL","http://openbrain-mcp:8000"); KEY=os.environ["MCP_ACCESS_KEY"]
def rpc(method,params,notif=False):
    p={"jsonrpc":"2.0","method":method,"params":params}
    if not notif: p["id"]=1
    req=urllib.request.Request(URL,data=json.dumps(p).encode(),
        headers={"x-brain-key":KEY,"content-type":"application/json","accept":"application/json, text/event-stream"},method="POST")
    with urllib.request.urlopen(req,timeout=120) as r:
        ct=r.headers.get("content-type",""); raw=r.read().decode()
        if notif: return None
        if "text/event-stream" in ct:
            o=None
            for ln in raw.splitlines():
                if ln.startswith("data:"): o=json.loads(ln[5:].strip())
            return o
        return json.loads(raw) if raw.strip() else None
def call(t,a):
    env=rpc("tools/call",{"name":t,"arguments":a}); res=env.get("result",env)
    for c in res.get("content",[]):
        if c.get("type")=="text":
            try: return json.loads(c["text"])
            except: return {"_text":c["text"]}
    return res
rpc("initialize",{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"verify","version":"1"}})
rpc("notifications/initialized",{},notif=True)
MARK="owui_collection_id:"
threads=call("list_threads",{"status":"all"}).get("threads",[])
mig=[t for t in threads if MARK in (t.get("description") or "")]
total=sum(t.get("source_count",0) for t in mig)
print(f"migrated threads: {len(mig)}   total source_count: {total}")
for t in sorted(mig,key=lambda t:-t.get("source_count",0)):
    print(f"  {t['source_count']:>3}  {t['name'][:42]}")
print("\n=== search spot-check ===")
for q in ["eurorack module manual","unreal blueprint api","self soothing strategies"]:
    res=call("search",{"query":q,"limit":2})
    txt=res.get("_text") if isinstance(res,dict) else str(res)
    print(f"[{q}] -> {str(txt)[:160]}")
