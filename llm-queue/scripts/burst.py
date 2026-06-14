"""Burst verifier for llm-queue (design B2, P1 acceptance).

Fires N concurrent chat completions at a base URL and tallies status codes,
printing one rejection body so we can see the structured 429 (design §4.5).

Run INSIDE a container that can reach the target, e.g.:
  docker exec -i llm-queue python - <this  http://localhost:8080  24
  docker exec -i openwebui python - <this  http://llama-cpp:8080  48   # via LiteLLM
"""

import asyncio
import json
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 24
MODEL = sys.argv[3] if len(sys.argv) > 3 else "qwen36-27b:nothink"

BODY = {
    "model": MODEL,
    "messages": [{"role": "user", "content": "Write one short sentence about queues."}],
    "max_tokens": 48,
    "stream": False,
}


async def one(client, i):
    try:
        r = await client.post(
            f"{BASE}/v1/chat/completions",
            json=BODY,
            headers={"Authorization": "Bearer owui-chat"},
        )
        return r.status_code, r.text
    except Exception as exc:  # noqa: BLE001
        return -1, str(exc)


async def main():
    async with httpx.AsyncClient(timeout=600.0) as client:
        results = await asyncio.gather(*(one(client, i) for i in range(N)))
    codes = {}
    sample_reject = None
    for code, body in results:
        codes[code] = codes.get(code, 0) + 1
        if code in (429, 503) and sample_reject is None:
            sample_reject = body
    print(f"target={BASE} burst={N}")
    print("status tally:", dict(sorted(codes.items())))
    if sample_reject:
        try:
            print("rejection body:", json.dumps(json.loads(sample_reject), indent=2))
        except Exception:  # noqa: BLE001
            print("rejection body (raw):", sample_reject[:400])
    else:
        print("rejection body: (none — all admitted)")


asyncio.run(main())
