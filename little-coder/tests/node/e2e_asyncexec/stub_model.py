"""Scripted OpenAI-compatible stub model for the ao-asyncexec end-to-end test (no GPU, no LiteLLM).
First request of a turn (no tool result yet): reply with the scenario's bash tool call(s).
Any later request: reply with a short final text. Streams SSE like an OpenAI-compatible server."""
import json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, SCENARIO = int(sys.argv[1]), sys.argv[2]
CALLS = {
    "sleep20": [("call_sleep20", "sleep 20")],
    "two": [("call_first", "sleep 6; echo A"), ("call_second", "echo B")],
}[SCENARIO]


def chunk(delta, finish=None):
    return {"id": "stub-1", "object": "chat.completion.chunk", "created": int(time.time()), "model": "local-large",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        sys.stderr.write("%s %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "local-large"}, {"id": "local-small"}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs = body.get("messages", [])
        has_tool = any(m.get("role") == "tool" for m in msgs)
        sys.stderr.write("request: %d messages, tool results: %s, stream=%s\n" % (len(msgs), has_tool, body.get("stream")))
        if has_tool:
            events = [chunk({"role": "assistant", "content": "done"}), chunk({}, "stop")]
        else:
            tcs = [{"index": i, "id": cid, "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": cmd})}}
                   for i, (cid, cmd) in enumerate(CALLS)]
            events = [chunk({"role": "assistant", "content": None, "tool_calls": tcs}), chunk({}, "tool_calls")]
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream"); self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for ev in events:
            self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
        usage = {"id": "stub-1", "object": "chat.completion.chunk", "choices": [],
                 "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
        self.wfile.write(b"data: " + json.dumps(usage).encode() + b"\n\n")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
