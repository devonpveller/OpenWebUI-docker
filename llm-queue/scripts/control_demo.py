"""Live dynamic-control demo (design §4.2 / P3): bump a waiting request's
priority and watch it move to the front of the waiting heap.

  docker exec -i llm-queue python - http://localhost:8080 < control_demo.py
"""

import asyncio
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
MODEL = "qwen36-27b:nothink"


def body():
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write three sentences about queues."}],
        "max_tokens": 96,
        "stream": False,
    }


async def fire(client, key):
    try:
        await client.post(
            f"{BASE}/v1/chat/completions", json=body(), headers={"Authorization": f"Bearer {key}"}
        )
    except Exception:
        pass


async def main():
    async with httpx.AsyncClient(timeout=600.0) as client:
        # Saturate with batch ob-entity (rank 3) so a queue forms.
        bg = [asyncio.create_task(fire(client, "ob-entity")) for _ in range(15)]
        # Let the queue build.
        await asyncio.sleep(1.5)

        snap = (await client.get(f"{BASE}/queue")).json()["models"]["qwen36-27b"]
        waiting = snap["waiting"]
        if not waiting:
            print("no waiters formed — try more load")
            await asyncio.gather(*bg)
            return
        # Pick the LAST waiter (worst position) and bump it to rank 0.
        victim = waiting[-1]["id"]
        print(f"before: {len(waiting)} waiting; bumping LAST id={victim[:6]} "
              f"(rank {waiting[-1]['prio']}, pos {len(waiting)})")
        r = await client.post(f"{BASE}/queue/{victim}/priority", json={"rank": 0})
        print("bump result:", r.status_code, r.json())

        snap2 = (await client.get(f"{BASE}/queue")).json()["models"]["qwen36-27b"]
        order = [(w["id"][:6], w["prio"]) for w in snap2["waiting"]]
        print("after waiting order (id, rank):", order[:6])
        if order and order[0][0] == victim[:6]:
            print(f"\nPASS — id={victim[:6]} jumped to the FRONT of the waiting heap")
        else:
            pos = next((i for i, o in enumerate(order) if o[0] == victim[:6]), None)
            print(f"\nid={victim[:6]} now at waiting position {pos} (rank 0)")

        await asyncio.gather(*bg)


asyncio.run(main())
