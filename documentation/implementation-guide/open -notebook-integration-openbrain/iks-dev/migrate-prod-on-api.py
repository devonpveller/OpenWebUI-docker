#!/usr/bin/env python3
"""Migrate the LIVE upstream Open Notebook's notebooks/sources -> live OB1.

Runs INSIDE the fork container (iks-notebook): reads the prod ON over its HTTP
API (no SurrealDB creds) and writes OB1 via the fork's already-saved live
connection (OB1Settings override) using the canonical dedup path
(find_or_create_source — matches url/content_hash, never clobbers an existing
row). Idempotent + additive; sources tagged metadata.source='on-migration'.

  python migrate-prod-on-api.py            # dry-run (no writes)
  python migrate-prod-on-api.py --apply    # write to OB1
"""
import asyncio
import hashlib
import json
import os
import sys
from urllib.parse import quote

import httpx

ON_API = os.getenv("PROD_ON_API", "http://open_notebook:5055")


def _ctype(url, file_path):
    if url and ("youtube.com" in url or "youtu.be" in url):
        return "youtube_transcript"
    if url:
        return "web_article"
    if file_path and file_path.lower().endswith(".pdf"):
        return "pdf"
    return "manual"


async def main(apply: bool):
    from open_notebook.database import ob1_repository as ob1
    from open_notebook.domain.ob1_settings import OB1Settings

    # Point ob1_repository at LIVE (the saved Settings override) — this script
    # is a separate process, so it must load the override itself.
    s = await OB1Settings.get_instance()
    ob1.set_overrides(s.as_overrides())
    if "openbrain-db" not in (ob1.effective_url() or ""):
        print(f"REFUSING: OB1 not pointed at live (url={ob1.effective_url()})", file=sys.stderr)
        sys.exit(2)
    print(f"OB1 target: {ob1.effective_url()}  | mode: {'APPLY' if apply else 'DRY-RUN'}")

    pool = await ob1.get_pool()

    async with httpx.AsyncClient(base_url=ON_API, timeout=30.0) as cli:
        nbs = (await cli.get("/api/notebooks")).json()
        nbs = nbs if isinstance(nbs, list) else nbs.get("notebooks", [])
        print(f"prod ON: {len(nbs)} notebooks")

        tot_new = tot_dup = tot_link = 0
        for nb in nbs:
            name = nb.get("name") or "Untitled"
            desc = nb.get("description") or None
            # find-or-create thread by name
            trow = await pool.fetchrow("SELECT id::text AS id FROM threads WHERE name=$1 LIMIT 1", name)
            if trow:
                tid = trow["id"]; tnew = False
            elif apply:
                tid = (await pool.fetchrow(
                    "INSERT INTO threads(name, description) VALUES($1,$2) RETURNING id::text AS id",
                    name, desc))["id"]; tnew = True
            else:
                tid = "(new)"; tnew = True

            srcs = (await cli.get("/api/sources", params={"notebook_id": nb["id"]})).json()
            srcs = srcs if isinstance(srcs, list) else srcs.get("sources", [])
            print(f"  notebook {name!r:34} -> thread {tid} {'(new)' if tnew else '(exists)'} | {len(srcs)} sources")

            for sref in srcs:
                det = (await cli.get(f"/api/sources/{quote(str(sref['id']), safe='')}")).json()
                content = det.get("full_text") or ""
                asset = det.get("asset") or {}
                url = asset.get("url") or None
                fpath = asset.get("file_path") or None
                title = det.get("title") or url or "source"
                ctype = _ctype(url, fpath)
                chash = hashlib.md5(content.encode("utf-8")).hexdigest() if content else None
                meta = json.dumps({"source": "on-migration", "on_source_id": str(sref["id"])})

                if not apply:
                    print(f"      - {title[:46]!r:48} type={ctype} url={(url or '-')[:40]} len={len(content)}")
                    continue

                emb = ob1._vec(await ob1.embed(content)) if content else None
                row = await pool.fetchrow(
                    """SELECT id::text AS id, was_duplicate
                       FROM find_or_create_source($1,$2,$3,$4,$5,NULL,NULL,$6::vector,$7::jsonb)""",
                    url, content, chash, title, ctype, emb, meta)
                obid = row["id"]
                tot_dup += int(row["was_duplicate"]); tot_new += int(not row["was_duplicate"])
                await pool.execute(
                    "SELECT link_source_to_thread($1::uuid,$2::uuid,'deliberate',NULL,'confirmed')",
                    tid, obid)
                tot_link += 1

    print(f"\n{'APPLIED' if apply else 'DRY-RUN'}: new={tot_new} deduped={tot_dup} links={tot_link}")
    await ob1.close_current_pool()


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv))
