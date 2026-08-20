#!/usr/bin/env python3
"""One-time migration: Open Notebook (SurrealDB) sources -> OB1 Postgres.

Plan Task 4.5. For PROMOTION ONLY — the agent never runs this against prod;
the operator runs it from the promotion runbook (Phase 8.3), backup first.

What it does (idempotent, dedup-aware):
  1. For each SurrealDB `notebook`, find-or-create a matching OB1 `thread`
     (matched by name) and remember notebook_id -> thread_id.
  2. For each `source`, find_or_create_source() in OB1 (dedup on url /
     content_hash) and link it to its notebook's thread
     (link_type='deliberate', confirmed). Re-runs fold onto existing rows.
  3. Sources reachable from multiple notebooks get one row, linked to each
     thread (additive — concept §2.4).

DEFAULT IS --dry-run: it reads SurrealDB and PRINTS the plan, writing
NOTHING. Pass --apply to actually write to OB1. Never deletes anything.

Env:
  SURREAL_URL (ws://host:8000/rpc), SURREAL_USER, SURREAL_PASSWORD,
  SURREAL_NAMESPACE, SURREAL_DATABASE
  OB1_DB_HOST, OB1_DB_PORT, OB1_DB_NAME, OB1_DB_USER, OB1_DB_PASSWORD

Usage:
  python migrate-on-sources.py            # dry-run (safe)
  python migrate-on-sources.py --apply    # write to OB1
"""
import argparse
import asyncio
import hashlib
import os
import sys

try:
    import asyncpg
    from surrealdb import AsyncSurreal
except Exception as exc:  # pragma: no cover
    print(f"missing dependency: {exc}\n  pip install asyncpg surrealdb", file=sys.stderr)
    sys.exit(2)


def _surreal_cfg():
    return dict(
        url=os.getenv("SURREAL_URL", "ws://localhost:8000/rpc"),
        user=os.getenv("SURREAL_USER", "root"),
        password=os.getenv("SURREAL_PASSWORD", "root"),
        namespace=os.getenv("SURREAL_NAMESPACE", "open_notebook"),
        database=os.getenv("SURREAL_DATABASE", "open_notebook"),
    )


def _ob1_cfg():
    return dict(
        host=os.getenv("OB1_DB_HOST", "127.0.0.1"),
        port=int(os.getenv("OB1_DB_PORT", "5432")),
        database=os.getenv("OB1_DB_NAME", "openbrain"),
        user=os.getenv("OB1_DB_USER", "postgres"),
        password=os.getenv("OB1_DB_PASSWORD", ""),
    )


async def read_surreal():
    cfg = _surreal_cfg()
    db = AsyncSurreal(cfg["url"])
    await db.signin({"username": cfg["user"], "password": cfg["password"]})
    await db.use(cfg["namespace"], cfg["database"])
    notebooks = await db.query("SELECT id, name, description FROM notebook")
    sources = await db.query(
        "SELECT id, title, full_text, asset FROM source"
    )
    # notebook<-source links via the reference edge
    refs = await db.query("SELECT in AS source, out AS notebook FROM reference")
    await db.close()

    def rows(x):
        # surreal returns either a list, or [{"result": [...]}] across versions
        if isinstance(x, list) and x and isinstance(x[0], dict) and "result" in x[0]:
            return x[0]["result"]
        return x or []

    return rows(notebooks), rows(sources), rows(refs)


async def find_or_create_thread(conn, name, description, apply):
    row = await conn.fetchrow("SELECT id::text AS id FROM threads WHERE name=$1 LIMIT 1", name)
    if row:
        return row["id"], False
    if not apply:
        return "(dry-run-new-thread)", True
    row = await conn.fetchrow(
        "INSERT INTO threads(name, description) VALUES($1,$2) RETURNING id::text AS id",
        name, description or None,
    )
    return row["id"], True


async def migrate(apply):
    notebooks, sources, refs = await read_surreal()
    print(f"SurrealDB: {len(notebooks)} notebooks, {len(sources)} sources, {len(refs)} links")

    # index: source_id -> [notebook_id...]
    src_to_nbs = {}
    for r in refs:
        s = str(r.get("source")); n = str(r.get("notebook"))
        src_to_nbs.setdefault(s, []).append(n)

    conn = await asyncpg.connect(**_ob1_cfg())
    try:
        nb_thread = {}
        for nb in notebooks:
            tid, created = await find_or_create_thread(conn, nb.get("name", "Untitled"),
                                                       nb.get("description"), apply)
            nb_thread[str(nb["id"])] = tid
            print(f"  notebook {nb.get('name')!r:40} -> thread {tid} {'(new)' if created else '(exists)'}")

        created_src = dup_src = linked = 0
        for s in sources:
            sid = str(s["id"])
            content = s.get("full_text") or ""
            asset = s.get("asset") or {}
            url = (asset.get("url") if isinstance(asset, dict) else None)
            title = s.get("title") or (url or "source")
            chash = hashlib.md5(content.encode("utf-8")).hexdigest() if content else None
            targets = [nb_thread[n] for n in src_to_nbs.get(sid, []) if n in nb_thread]

            if not apply:
                print(f"  source {title[:48]!r:50} url={url or '-'} -> {len(targets)} thread(s)")
                continue

            row = await conn.fetchrow(
                """SELECT id::text AS id, was_duplicate
                   FROM find_or_create_source($1,$2,$3,$4,'web_article',NULL,NULL,NULL,$5::jsonb)""",
                url, content, chash, title,
                '{"source":"on-migration","on_source_id":"%s"}' % sid,
            )
            obid = row["id"]
            dup_src += int(row["was_duplicate"]); created_src += int(not row["was_duplicate"])
            for tid in targets:
                await conn.execute(
                    "SELECT link_source_to_thread($1::uuid,$2::uuid,'deliberate',NULL,'confirmed')",
                    tid, obid)
                linked += 1

        if apply:
            print(f"\nAPPLIED: {created_src} new sources, {dup_src} deduped, {linked} thread links.")
        else:
            print("\nDRY-RUN complete. No writes performed. Re-run with --apply to migrate.")
    finally:
        await conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually write to OB1 (default: dry-run)")
    args = ap.parse_args()
    if args.apply:
        print("!! --apply: writing to OB1. Ensure you have a fresh openbrain-db backup. !!")
    asyncio.run(migrate(args.apply))
