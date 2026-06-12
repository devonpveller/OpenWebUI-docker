#!/usr/bin/env python3
"""Swap the post-migration FileShed (tool) + Copy Research Note (function) content
with the async-ported versions. Backs up the live rows first; verifies after.
Run INSIDE the openwebui container: python3 /tmp/redeploy.py
"""
import sqlite3, re, sys

DB = "/app/backend/data/webui.db"
JOBS = [
    ("tool",     "fileshed",          "/tmp/fileshed.patched.py", "/tmp/fileshed.preswap.py",
     r"(?<!await )\bGroups\.[a-z_]+\("),
    ("function", "copy_research_note", "/tmp/crn.patched.py",      "/tmp/crn.preswap.py",
     r"(?<!await )\b(?:Chats|Notes)\.(?:get_chat_by_id|get_note_by_id)\("),
]

con = sqlite3.connect(DB)
for table, rid, patched_path, backup_path, bare_re in JOBS:
    row = con.execute(f"select content from {table} where id=?", (rid,)).fetchone()
    if not row:
        print(f"!! {table}:{rid} NOT FOUND — skipping"); continue
    live = row[0]
    open(backup_path, "w", encoding="utf-8").write(live)
    patched = open(patched_path, "r", encoding="utf-8").read()
    bare_live = len(re.findall(bare_re, live))
    bare_patched = len(re.findall(bare_re, patched))
    print(f"[{table}:{rid}] live_len={len(live)} patched_len={len(patched)} "
          f"| bare-unawaited calls live={bare_live} patched={bare_patched}")
    if bare_patched != 0:
        print(f"   ABORT {rid}: patched still has {bare_patched} un-awaited calls"); sys.exit(2)
    con.execute(f"update {table} set content=? where id=?", (patched, rid))
    print(f"   updated {table}:{rid} content")
con.commit()

# verify
print("\n=== post-update verification ===")
for table, rid, *_ , bare_re in JOBS:
    c = con.execute(f"select content from {table} where id=?", (rid,)).fetchone()[0]
    bare = len(re.findall(bare_re, c))
    awaited = len(re.findall(r"await ", c))
    print(f"[{table}:{rid}] now: bare-unawaited={bare}  (expect 0)  | total 'await ' tokens={awaited}")
con.close()
print("DONE")
