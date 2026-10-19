"""The engine-adapter contract: ONE set of checks every engine adapter must pass.

Run in-process by tests/test_adapters.py (fake engine on loopback, both adapters), and inside the
gateway image by the docker round trip against the REAL engines:

    python adapter_contract.py <adapter name> <engine url>     # prints JSON, exit 0 = all pass

The engine behind <engine url> must serve fake_engine.DEFAULT_RESULTS (directly, or - for the real
SearXNG image - through its json_engine pointed at the fake engine). Standard library + the
gateway package only.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from gateway.models import SearchRequest
from gateway.providers.base import ProviderError, TransientProviderError
from gateway.rotation import build_provider

# fake_engine.DEFAULT_RESULTS minus the non-http one every adapter must drop
EXPECTED = {
    "https://public.example.com/a": ("Result A", "A plain snippet."),
    "https://other.example.com/b": ("Result B", "Another snippet."),
    "https://third.example.com/c": ("Result C é", "Café snippet."),
}
DEAD_URL = "http://127.0.0.1:9"  # discard port: nothing listens


async def run(name: str, url: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def check(what: str, ok: bool, detail: Any = "") -> None:
        checks.append({"check": what, "ok": bool(ok), "detail": "" if ok else str(detail)[:300]})

    p = build_provider(name, url, 30.0)
    try:
        check("health() is true for a live engine", await p.health())
        res = await p.search(SearchRequest(query="bread", max_results=10))
        got = {str(r.url): (r.title, r.snippet) for r in res}
        check("search returns exactly the engine's http(s) results (the javascript: one dropped)",
              set(got) == set(EXPECTED), sorted(got))
        check("titles and snippets survive the adapter (unicode included)",
              all(got.get(u) == v for u, v in EXPECTED.items()), got)
        check("every result names this adapter as its provider",
              all(r.provider == p.name for r in res), [r.provider for r in res])
        one = await p.search(SearchRequest(query="bread", max_results=1))
        check("max_results is honoured", len(one) == 1, len(one))
    except ProviderError as e:
        check("no provider error against a live engine", False, repr(e))
    finally:
        await p.aclose()

    dead = build_provider(name, DEAD_URL, 5.0)
    try:
        check("health() is false for a dead engine", not await dead.health())
        try:
            await dead.search(SearchRequest(query="bread"))
            check("a dead engine raises TransientProviderError", False, "no error")
        except TransientProviderError:
            check("a dead engine raises TransientProviderError", True)
        except Exception as e:  # noqa: BLE001
            check("a dead engine raises TransientProviderError", False, repr(e))
    finally:
        await dead.aclose()
    return {"adapter": name, "ok": all(c["ok"] for c in checks), "checks": checks}


def main() -> int:
    out = asyncio.run(run(sys.argv[1], sys.argv[2]))
    print(json.dumps(out))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
