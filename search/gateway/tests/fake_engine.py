"""The fake search engine (standard library only, so it runs in a bare python image as well).

Two protocols on one server, so the same engine can stand behind either adapter:
  GET /search?q=&format=json   SearXNG's JSON API shape (``results[].url/title/content/engine``)
  GET /query?q=&n=             the fake engine's own shape (``hits[].link/headline/summary``)
  GET /ping                    200
The real SearXNG image is pointed at ``/query`` through its generic ``json_engine`` in the docker
round trip, so SearXNG's adapter and the fake adapter answer from the same result set.

    python fake_engine.py [port] [results.json]   # serves DEFAULT_RESULTS (or the file's list of
                                                  # {url, title, snippet}) on 0.0.0.0:<port>
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

DEFAULT_RESULTS: list[dict[str, str]] = [
    {"url": "https://public.example.com/a", "title": "Result A", "snippet": "A plain snippet."},
    {"url": "https://other.example.com/b", "title": "Result B", "snippet": "Another snippet."},
    {"url": "javascript:alert(1)", "title": "Not a web result", "snippet": "dropped by adapters"},
    {"url": "https://third.example.com/c", "title": "Result C é", "snippet": "Café snippet."},
]


class EngineHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: Any) -> None:
        return None

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        results: list[dict[str, str]] = getattr(self.server, "results", DEFAULT_RESULTS)
        requests: list[dict[str, str]] = getattr(self.server, "requests", [])
        parts = urlsplit(self.path)
        qs = {k: v[0] for k, v in parse_qs(parts.query).items()}
        requests.append({"path": parts.path, **qs})
        if parts.path == "/search":
            if qs.get("format") != "json":
                self._send(403, b"{}")
                return
            payload = {
                "query": qs.get("q", ""),
                "results": [
                    {"url": r["url"], "title": r["title"], "content": r.get("snippet", ""),
                     "engine": "fake", "engines": ["fake"], "score": 1.0}
                    for r in results
                ],
                "unresponsive_engines": [],
            }
            self._send(200, json.dumps(payload).encode())
        elif parts.path == "/query":
            n = int(qs.get("n", "10") or "10")
            hits = [{"link": r["url"], "headline": r["title"], "summary": r.get("snippet", "")}
                    for r in results[:n]]
            self._send(200, json.dumps({"hits": hits}).encode())
        elif parts.path == "/ping":
            self._send(200, b'{"ok": true}')
        else:
            self._send(404, b"{}")


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    httpd = ThreadingHTTPServer(("0.0.0.0", port), EngineHandler)
    if len(sys.argv) > 2:
        with open(sys.argv[2], encoding="utf-8") as f:
            httpd.results = json.load(f)  # type: ignore[attr-defined]
    httpd.serve_forever()


if __name__ == "__main__":
    main()
