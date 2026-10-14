"""Scripted OpenAI-compatible stub model for the ao-exechang end-to-end test (no GPU, no LiteLLM).
First request of a turn (no tool result yet): reply with one `bash` tool call running COMMAND.
Any later request: reply with a short final text, so the turn ends on its own. Every request body
is appended to REQUESTS (JSONL): what the model RECEIVED, including the tool result.
usage: stub_model.py PORT REQUESTS COMMAND"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, REQUESTS, COMMAND = int(sys.argv[1]), sys.argv[2], sys.argv[3]


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
        msgs = body.get("messages", [])
        if any(m.get("role") == "tool" for m in msgs):
            events = [chunk({"role": "assistant", "content": "done"}), chunk({}, "stop")]
        else:
            tc = {"index": 0, "id": "call_e2e", "type": "function",
                  "function": {"name": "bash", "arguments": json.dumps({"command": COMMAND})}}
            events = [chunk({"role": "assistant", "content": None, "tool_calls": [tc]}),
                      chunk({}, "tool_calls")]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for ev in events:
            self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
        usage = {"id": "stub-1", "object": "chat.completion.chunk", "choices": [],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
