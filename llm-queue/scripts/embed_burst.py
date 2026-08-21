"""Embed burst — confirm fronting the PLAIN-llama.cpp embed upstream with the
queue doesn't regress high-volume embedding bursts (OB1 backfill pattern, P4).

  docker exec -i llm-queue python - http://llm-gateway:8080 60 < embed_burst.py
"""

import asyncio
import sys

import httpx

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://llm-gateway:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 60

BODY = {"model": "bge-m3", "input": "the quick brown fox embeds over the lazy queue"}


async def one(client):
    try:
        r = await client.post(f"{BASE}/v1/embeddings", json=BODY,
                              headers={"Authorization": "Bearer not-needed"})
        emb = r.json().get("data", [{}])[0].get("embedding", [])
        ok = r.status_code == 200 and len(emb) == 1024
        return r.status_code, ok
    except Exception:  # noqa: BLE001
        return -1, False


async def main():
    async with httpx.AsyncClient(timeout=600.0) as client:
        results = await asyncio.gather(*(one(client) for _ in range(N)))
    codes = {}
    good = 0
    for code, ok in results:
        codes[code] = codes.get(code, 0) + 1
        good += ok
    print(f"target={BASE} embed_burst={N}")
    print("status tally:", dict(sorted(codes.items())))
    verdict = 'PASS — no regression' if good == N else 'CHECK'
    print(f"valid 1024-dim embeddings: {good}/{N}  →  {verdict}")


asyncio.run(main())
