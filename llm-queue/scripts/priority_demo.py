"""Live priority demo (design §8c): under contention, interactive owui-chat
should WAIT LESS than batch ob-entity, even though it arrives later.

Fires a saturating batch of ob-entity (rank 3), then a few owui-chat (rank 0)
just after, all concurrent, and reports the mean queue-wait per class from the
X-Queue-Wait response header.

  docker exec -i llm-queue python - http://localhost:8080 < priority_demo.py
"""

import asyncio
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
MODEL = "qwen36-27b:nothink"


def body(tokens=20):
    return {
        "model": MODEL,
        "messages": [{"role": "user", "content": "Write one short sentence about scheduling."}],
        "max_tokens": tokens,
        "stream": False,
    }


async def call(client, key, delay, tokens=20):
    if delay:
        await asyncio.sleep(delay)
    r = await client.post(
        f"{BASE}/v1/chat/completions", json=body(tokens), headers={"Authorization": f"Bearer {key}"}
    )
    return key, r.status_code, float(r.headers.get("X-Queue-Wait", -1))


async def main():
    async with httpx.AsyncClient(timeout=600.0) as client:
        # Warm the rolling-T metric so the estimate reflects real completion time
        # (~3-5s) instead of the pessimistic 30s initial — otherwise the budget
        # gate rejects even rank-0 owui-chat. (Warm-up is the demo's setup, not
        # part of the measured contention.)
        await asyncio.gather(*(call(client, "warmup", 0.0, tokens=12) for _ in range(8)))

        tasks = []
        # 9 batch ob-entity arrive first (saturate slots + fill the queue).
        for _ in range(9):
            tasks.append(call(client, "ob-entity", 0.0))
        # 3 interactive owui-chat arrive a hair later — they must jump the queue.
        for _ in range(3):
            tasks.append(call(client, "owui-chat", 0.15))
        results = await asyncio.gather(*tasks)

    waits = {}
    for key, code, wait in results:
        if code == 200 and wait >= 0:
            waits.setdefault(key, []).append(wait)
    for key, ws in sorted(waits.items()):
        print(f"{key:12} served={len(ws):2d}  mean_wait={sum(ws)/len(ws):6.2f}s  max={max(ws):6.2f}s")
    if "owui-chat" in waits and "ob-entity" in waits:
        owui = sum(waits["owui-chat"]) / len(waits["owui-chat"])
        batch = sum(waits["ob-entity"]) / len(waits["ob-entity"])
        verdict = "PASS — interactive jumped ahead" if owui < batch else "FAIL"
        print(f"\nowui-chat mean {owui:.2f}s vs ob-entity mean {batch:.2f}s  →  {verdict}")


asyncio.run(main())
