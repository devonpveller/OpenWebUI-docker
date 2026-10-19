"""The round trip's client: runs a plan of HTTP requests from INSIDE a test network and writes the
answers as JSON (the runner asserts on them). Runs in the gateway image (httpx is installed).

    python gate_client.py <plan.json> <out.json>
plan: [{"name", "method", "url", "headers"?, "json"?, "params"?, "timeout"?}]
out:  {name: {"status", "body" (parsed JSON or text), "elapsed_s"}}
"""

from __future__ import annotations

import json
import sys
import time

import httpx


def main() -> None:
    plan = json.load(open(sys.argv[1], encoding="utf-8"))
    out = {}
    with httpx.Client(trust_env=False) as c:
        for step in plan:
            t0 = time.monotonic()
            try:
                r = c.request(step["method"], step["url"], headers=step.get("headers"),
                              json=step.get("json"), params=step.get("params"),
                              timeout=step.get("timeout", 120))
                try:
                    body = r.json()
                except ValueError:
                    body = r.text
                out[step["name"]] = {"status": r.status_code, "body": body,
                                     "elapsed_s": round(time.monotonic() - t0, 3)}
            except httpx.HTTPError as e:
                out[step["name"]] = {"status": None, "body": repr(e),
                                     "elapsed_s": round(time.monotonic() - t0, 3)}
    with open(sys.argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f)


if __name__ == "__main__":
    main()
