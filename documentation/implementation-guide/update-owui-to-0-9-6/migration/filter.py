#!/usr/bin/env python3
"""Step 2 - Filter / Curate. Transforms manifest.csv into:

  promote.csv   survivors (the CONTRACT for Step 3 — edit by hand to remove more)
  rejected.csv  everything dropped, with a reason column
  review.csv    advisory flags (low-volume collections) — still promoted

Gates (defaults; override via CLI: filter.py [MIN_CONTENT_CHARS] [MIN_FILES]):
  empty                : content_chars == 0                       -> reject
  low_context          : content_chars < MIN_CONTENT_CHARS        -> reject
  dup_in_collection    : same (collection_id, content_md5) repeat -> reject (keep 1)
  low_volume_collection: surviving files in collection < MIN_FILES -> review (advisory)

Cross-collection duplicates are LEFT IN promote.csv on purpose: OB1's
find_or_create_source collapses identical content to ONE source and just adds the
second thread link, so each promote row maps cleanly to one capture_with_thread call.
Empty collections fall out naturally (zero surviving rows).
"""
import csv, sys, collections

# positional: MIN_CONTENT_CHARS MIN_FILES ; flag: --origins=authored,appsync
MIN_CONTENT_CHARS = 200
MIN_FILES = 2
ORIGINS = {"authored", "appsync", "smolcrawl"}   # default: keep all origins
pos = []
for a in sys.argv[1:]:
    if a.startswith("--origins="):
        ORIGINS = {x.strip() for x in a.split("=", 1)[1].split(",") if x.strip()}
    else:
        pos.append(a)
if len(pos) > 0: MIN_CONTENT_CHARS = int(pos[0])
if len(pos) > 1: MIN_FILES = int(pos[1])

rows = list(csv.DictReader(open("manifest.csv", encoding="utf-8")))
promote, rejected = [], []
seen_in_coll = set()                       # (collection_id, md5)
by_coll = collections.defaultdict(list)

for r in rows:
    if r.get("origin", "authored") not in ORIGINS:
        rejected.append({**r, "reason": f"origin_excluded({r.get('origin')})"}); continue
    n = int(r["content_chars"])
    if n == 0:
        rejected.append({**r, "reason": "empty"}); continue
    if n < MIN_CONTENT_CHARS:
        rejected.append({**r, "reason": "low_context"}); continue
    key = (r["collection_id"], r["content_md5"])
    if r["content_md5"] and key in seen_in_coll:
        rejected.append({**r, "reason": "dup_in_collection"}); continue
    seen_in_coll.add(key)
    promote.append(r)
    by_coll[r["collection_id"]].append(r)

# advisory: collections that end up with very few survivors
review = []
for cid, fs in by_coll.items():
    if len(fs) < MIN_FILES:
        for f in fs:
            review.append({**f, "reason": f"low_volume_collection ({len(fs)} file)"})

# name-collision guard: distinct collection_ids sharing a name that BOTH survive
name_to_ids = collections.defaultdict(set)
for r in promote:
    name_to_ids[r["collection_name"]].add(r["collection_id"])
collisions = {n: ids for n, ids in name_to_ids.items() if len(ids) > 1}

man_fields = list(rows[0].keys())
def dump(path, data, extra):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=man_fields + extra); w.writeheader(); w.writerows(data)

dump("promote.csv", promote, [])
dump("rejected.csv", rejected, ["reason"])
dump("review.csv", review, ["reason"])

rej_by_reason = collections.Counter(r["reason"] for r in rejected)
print(f"MIN_CONTENT_CHARS={MIN_CONTENT_CHARS}  MIN_FILES={MIN_FILES}  ORIGINS={sorted(ORIGINS)}")
print(f"input rows        : {len(rows)}")
print(f"PROMOTE           : {len(promote)} files across {len(by_coll)} collections")
print(f"rejected          : {len(rejected)}  {dict(rej_by_reason)}")
print(f"review (advisory) : {len(review)} files in {len(set(r['collection_id'] for r in review))} low-volume collections")
if collisions:
    print(f"NAME COLLISIONS (distinct ids, same name, both promoted): {collisions}")
else:
    print("name collisions   : none among promoted collections")
