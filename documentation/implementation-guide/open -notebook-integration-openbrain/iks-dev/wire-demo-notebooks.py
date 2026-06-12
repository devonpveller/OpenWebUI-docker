#!/usr/bin/env python3
"""Demo wiring: create an ON notebook for each OB1 thread and map them 1:1.

Makes the per-notebook triage panel demonstrable: each of the 4 scenario
threads becomes an Open Notebook notebook whose ob_thread_id points back at
the thread. Opening the notebook then shows that thread's suggestions in
context. Sandbox-only.

Run AFTER the fork is rebuilt + iks-notebook is up (so migration 15 has
defined the ob_thread_id field).
"""
import asyncio
import asyncpg
import httpx
from surrealdb import AsyncSurreal

OB1 = dict(host="127.0.0.1", port=18432, database="openbrain",
           user="postgres", password="iks_dev_only")
API = "http://127.0.0.1:15055/api"
SURREAL_URL = "ws://127.0.0.1:18003/rpc"


async def main():
    conn = await asyncpg.connect(**OB1)
    threads = await conn.fetch(
        "SELECT id::text AS id, name, description FROM threads ORDER BY name")
    await conn.close()

    db = AsyncSurreal(SURREAL_URL)
    await db.signin({"username": "root", "password": "root"})
    await db.use("open_notebook", "open_notebook")

    # Clear any prior demo notebooks so re-runs stay clean.
    await db.query("DELETE notebook;")

    async with httpx.AsyncClient(timeout=30) as client:
        for t in threads:
            r = await client.post(
                f"{API}/notebooks",
                json={"name": t["name"], "description": t["description"] or ""})
            r.raise_for_status()
            nbid = r.json()["id"]
            await db.query(f"UPDATE {nbid} SET ob_thread_id = '{t['id']}'")
            print(f"  {nbid}  <->  thread {t['name']}")
    await db.close()
    print("done — open a notebook to see its scoped suggestions")


if __name__ == "__main__":
    asyncio.run(main())
