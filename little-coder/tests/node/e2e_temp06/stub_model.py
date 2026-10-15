"""Scripted OpenAI-compatible stub for the ao-temp06 end-to-end test (no GPU, no LiteLLM): answers
every chat request with a short final text, and appends each request body to REQUESTS (JSONL).
usage: stub_model.py PORT REQUESTS"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, REQUESTS = int(sys.argv[1]), sys.argv[2]


def chunk(delta, finish=None):
    return {"id": "stub-1", "object": "chat.completion.chunk", "created": int(time.time()),
            "model": "local-large", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "local-large"}, {"id": "local-small"}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        with open(REQUESTS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": time.time(), "body": body}) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for ev in (chunk({"role": "assistant", "content": "done"}), chunk({}, "stop"),
                   {"id": "stub-1", "object": "chat.completion.chunk", "choices": [],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}):
            self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
