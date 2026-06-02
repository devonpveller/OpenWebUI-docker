#!/usr/bin/env python3
"""Load realistic demo scenarios into iks-db for the triage walkthrough.

Reads scenarios.json (authored by the iks-triage-scenarios workflow), clears
the synthetic seed, and inserts threads + sources + automatic/confirmed links.
Embeddings are left NULL — the suggestion worker's POST /suggest backfills
them via bge-m3 inside the container, then scores cross-thread suggestions.

Usage: python load-scenarios.py scenarios.json
"""
import asyncio, json, sys, hashlib
import asyncpg

CFG = dict(host="127.0.0.1", port=18432, database="openbrain", user="postgres", password="iks_dev_only")


async def main(path):
    data = json.load(open(path, encoding="utf-8"))
    conn = await asyncpg.connect(**CFG)
    try:
        # Fresh slate (throwaway sandbox): drop the synthetic seed + any suggestions.
        await conn.execute("DELETE FROM session_sources; DELETE FROM thread_sources; "
                           "DELETE FROM sessions; DELETE FROM sources; DELETE FROM threads;")
        n_threads = n_sources = n_bridge = 0
        for sc in data:
            th = sc["thread"]
            tid = await conn.fetchval(
                "INSERT INTO threads(name, description) VALUES($1,$2) RETURNING id",
                th["name"], th.get("guiding_question"))
            n_threads += 1
            for s in sc["sources"]:
                content = s["content"]
                chash = hashlib.md5(content.encode("utf-8")).hexdigest()
                meta = json.dumps({"demo": True, "is_bridge": bool(s.get("is_bridge"))})
                row = await conn.fetchrow(
                    "SELECT id::text AS id FROM find_or_create_source($1,$2,$3,$4,'web_article',NULL,$5,NULL,$6::jsonb)",
                    s.get("url"), content, chash, s["title"], s.get("domain"), meta)
                await conn.execute(
                    "SELECT link_source_to_thread($1::uuid,$2::uuid,'automatic',NULL,'confirmed')",
                    tid, row["id"])
                n_sources += 1
                n_bridge += int(bool(s.get("is_bridge")))
        print(f"loaded {n_threads} threads, {n_sources} sources ({n_bridge} bridge sources)")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "scenarios.json"))
