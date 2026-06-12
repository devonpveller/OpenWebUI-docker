#!/usr/bin/env python3
"""Step 1 - Extract + Audit (READ-ONLY) of OWUI knowledge collections.

Reads a read-only copy of webui.db and emits:
  manifest.csv    one row per (collection, file) link
  collections.csv per-collection rollup
  orphans.csv     files referenced by a collection but missing, and files that
                  carry a collection_name but appear in no knowledge.file_ids
  summary.txt     totals, content-length histogram, duplicate + empty stats,
                  empty-collection list, low-volume-collection list, and a
                  survival preview at candidate MIN_CONTENT_CHARS thresholds

Never writes to OWUI. Pure stdlib.
"""
import sqlite3, json, hashlib, csv, collections, sys, os

DB = sys.argv[1] if len(sys.argv) > 1 else "webui.live.db"
THRESHOLD_PREVIEW = [0, 50, 100, 200, 500, 1000]  # for the survival preview only
MIN_FILES_PER_COLLECTION = 2                       # low-volume flag (advisory)

def jload(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

# --- index every file ------------------------------------------------------
files = {}
for r in con.execute("SELECT id, filename, data, meta FROM file"):
    data, meta = jload(r["data"]), jload(r["meta"])
    content = (data.get("content") or "")
    stripped = content.strip()
    files[r["id"]] = {
        "filename": r["filename"] or meta.get("name", ""),
        "chars": len(stripped),
        "md5": hashlib.md5(stripped.encode("utf-8", "ignore")).hexdigest() if stripped else "",
        "ctype": meta.get("content_type", ""),
        "collection_name": meta.get("collection_name", ""),
    }

# --- walk collections ------------------------------------------------------
# Live-DB linkage: knowledge.data is empty; membership is
#   file.meta.collection_name == knowledge.id
# (the other ~1060 distinct collection_name values are per-chat file uploads,
#  i.e. NOT curated knowledge collections — those are out of scope.)
rows = []                              # manifest rows (collection x file)
percoll = collections.defaultdict(list)
orphans = []                           # files naming a non-knowledge collection
coll_meta = {}

def origin_of(d):
    dl = (d or "").lower()
    if dl.startswith("auto-synced by smolcrawl"):
        return "smolcrawl"          # reproducible web-scrape mirror
    if "auto-synced" in dl:
        return "appsync"            # auto-synced app context (e.g. GAPS task mgr)
    return "authored"              # human-curated

knowledge_ids = {}
for k in con.execute("SELECT id, name, description FROM knowledge"):
    knowledge_ids[k["id"]] = k["name"]
    coll_meta[k["id"]] = {"name": k["name"], "description": (k["description"] or ""),
                          "origin": origin_of(k["description"])}
    percoll.setdefault(k["id"], [])    # ensure empty collections are visible

for fid, f in files.items():
    cn = f["collection_name"]
    if cn in knowledge_ids:            # belongs to a curated knowledge collection
        row = {"collection_id": cn, "collection_name": knowledge_ids[cn], "file_id": fid,
               "filename": f["filename"], "content_chars": f["chars"],
               "content_md5": f["md5"], "owui_content_type": f["ctype"],
               "origin": coll_meta[cn]["origin"]}
        rows.append(row)
        percoll[cn].append(row)
    elif cn:                           # names some other (chat-upload) collection
        orphans.append({"kind": "non_knowledge_collection", "collection_id": "",
                        "collection_name": cn, "file_id": fid,
                        "detail": f"collection_name not a knowledge.id, chars={f['chars']}"})

# --- write manifest.csv ----------------------------------------------------
man_fields = ["collection_id", "collection_name", "file_id", "filename",
              "content_chars", "content_md5", "owui_content_type", "origin"]
with open("manifest.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=man_fields); w.writeheader(); w.writerows(rows)

# --- per-collection rollup -------------------------------------------------
with open("collections.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["collection_id", "collection_name", "files", "nonempty_files",
                "survive_ge_200", "total_chars"])
    for cid, fs in sorted(percoll.items(), key=lambda kv: coll_meta.get(kv[0], {}).get("name", "")):
        nonempty = [r for r in fs if r["content_chars"] > 0]
        ge200 = [r for r in fs if r["content_chars"] >= 200]
        w.writerow([cid, coll_meta.get(cid, {}).get("name", ""), len(fs),
                    len(nonempty), len(ge200), sum(r["content_chars"] for r in fs)])

with open("orphans.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=["kind", "collection_id", "collection_name", "file_id", "detail"])
    w.writeheader(); w.writerows(orphans)

# --- summary ---------------------------------------------------------------
chars = [r["content_chars"] for r in rows]
empty = sum(1 for c in chars if c == 0)
md5s = [r["content_md5"] for r in rows if r["content_md5"]]
dup_total = len(md5s) - len(set(md5s))
empty_colls = [coll_meta[cid]["name"] for cid, fs in percoll.items()
               if not any(r["content_chars"] >= 200 for r in fs)]
low_vol = [(coll_meta[cid]["name"], sum(1 for r in fs if r["content_chars"] >= 200))
           for cid, fs in percoll.items()
           if 0 < sum(1 for r in fs if r["content_chars"] >= 200) < MIN_FILES_PER_COLLECTION]

def hist(vals):
    buckets = [(0, 0), (1, 99), (100, 199), (200, 499), (500, 999),
               (1000, 4999), (5000, 19999), (20000, 10**12)]
    out = []
    for lo, hi in buckets:
        n = sum(1 for v in vals if lo <= v <= hi)
        label = f"{lo}" if lo == hi else (f">={lo}" if hi > 10**11 else f"{lo}-{hi}")
        out.append(f"  {label:>12} chars : {n}")
    return "\n".join(out)

lines = []
lines.append("OWUI knowledge migration — AUDIT summary")
lines.append("=" * 48)
lines.append(f"DB copy            : {os.path.abspath(DB)}")
lines.append(f"Collections        : {len(coll_meta)}")
lines.append(f"File-links (rows)  : {len(rows)}")
lines.append(f"Distinct files     : {len(set(r['file_id'] for r in rows))}")
lines.append(f"Total files in DB  : {len(files)}")
lines.append(f"Empty content rows : {empty}")
lines.append(f"Duplicate content  : {dup_total} (rows sharing an md5 with an earlier row)")
lines.append("")
lines.append("Content-length histogram (per file-link):")
lines.append(hist(chars))
lines.append("")
lines.append("Survival preview by MIN_CONTENT_CHARS:")
for t in THRESHOLD_PREVIEW:
    lines.append(f"  >= {t:>5} chars : {sum(1 for c in chars if c >= t)} rows survive")
lines.append("")
lines.append(f"Empty collections (0 files >=200 chars): {len(empty_colls)}")
for n in sorted(empty_colls):
    lines.append(f"  - {n}")
lines.append("")
lines.append(f"Low-volume collections (1 file >=200 chars): {len(low_vol)}")
for n, c in sorted(low_vol):
    lines.append(f"  - {n} ({c})")
lines.append("")
lines.append(f"Orphan findings    : {len(orphans)} (see orphans.csv)")
summary = "\n".join(lines)
open("summary.txt", "w", encoding="utf-8").write(summary + "\n")
print(summary)
