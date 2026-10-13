"""Scripted OpenAI-compatible stub model for the ao-flailtime end-to-end test (no GPU, no LiteLLM).

Each request is one ROUND of a scenario (round = number of tool results already in the request divided
by the calls per round). A round streams `think` seconds of reasoning (reasoning_content deltas, one
every 0.25 s), then the round's bash tool call(s); after the last round it answers "done". Every
request is logged as `req <round> <attempt> <t_start> <t_end> <outcome>` (time.time()) so the driver
can compare the guard's model clock with what the model server actually spent.

Scenarios:
  tooltime  10 rounds: think 1 s, then `sleep 6; echo step N`          (tool time >> model time)
  thinking  14 rounds: think 3 s, then `echo probe N`                  (model time, instant tools)
  batch      6 rounds: think 0.5 s, then TWO calls `sleep 4; echo a N` + `sleep 4; echo b N`
  retry     10 rounds: think 1 s, then `sleep 5; echo r N`; the FIRST attempt of each round fails:
            even rounds with HTTP 503 after a 2 s hold, odd rounds by a stream that sends 2 s of
            reasoning and then drops the connection (an aborted call). pi retries both.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, SCENARIO = int(sys.argv[1]), sys.argv[2]
LOG = sys.argv[3]

SPEC = {
    "tooltime": dict(rounds=10, think=1.0, calls=lambda n: [f"sleep 6; echo step {n}"]),
    "thinking": dict(rounds=14, think=3.0, calls=lambda n: [f"echo probe {n}"]),
    "batch": dict(rounds=6, think=0.5, calls=lambda n: [f"sleep 4; echo a {n}", f"sleep 4; echo b {n}"]),
    "retry": dict(rounds=10, think=1.0, calls=lambda n: [f"sleep 5; echo r {n}"]),
}[SCENARIO]
PER_ROUND = len(SPEC["calls"](0))
ATTEMPTS: dict[int, int] = {}


def log(*parts):
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(" ".join(str(p) for p in parts) + "\n")


def chunk(delta, finish=None):
    return {"id": "stub-1", "object": "chat.completion.chunk", "created": int(time.time()), "model": "local-large",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


class H(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "local-large"}, {"id": "local-small"}]}).encode())

    def _sse(self, obj):
        self.wfile.write(b"data: " + json.dumps(obj).encode() + b"\n\n")
        self.wfile.flush()

    def _think(self, secs, rnd):
        end = time.time() + secs
        i = 0
        while time.time() < end:
            self._sse(chunk({"role": "assistant", "reasoning_content": f"Round {rnd} step {i}: weighing option {i * 7 % 13}. "}))
            i += 1
            time.sleep(0.25)

    def do_POST(self):
        t0 = time.time()
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        msgs = body.get("messages", [])
        rnd = sum(1 for m in msgs if m.get("role") == "tool") // PER_ROUND
        ATTEMPTS[rnd] = ATTEMPTS.get(rnd, 0) + 1
        attempt = ATTEMPTS[rnd]
        if SCENARIO == "retry" and attempt == 1 and rnd < SPEC["rounds"]:
            if rnd % 2 == 0:
                time.sleep(2.0)
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"stub overloaded"}}')
                log("req", rnd, attempt, f"{t0:.3f}", f"{time.time():.3f}", "http503")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self._think(2.0, rnd)
            log("req", rnd, attempt, f"{t0:.3f}", f"{time.time():.3f}", "dropped")
            self.close_connection = True
            return                      # no finish_reason, no [DONE]: the stream is cut
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        if rnd >= SPEC["rounds"]:
            self._sse(chunk({"role": "assistant", "content": "done"}))
            self._sse(chunk({}, "stop"))
        else:
            self._think(SPEC["think"], rnd)
            tcs = [{"index": i, "id": f"call_{rnd}_{i}", "type": "function",
                    "function": {"name": "bash", "arguments": json.dumps({"command": c})}}
                   for i, c in enumerate(SPEC["calls"](rnd))]
            self._sse(chunk({"role": "assistant", "content": None, "tool_calls": tcs}))
            self._sse(chunk({}, "tool_calls"))
        self._sse({"id": "stub-1", "object": "chat.completion.chunk", "choices": [],
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()
        log("req", rnd, attempt, f"{t0:.3f}", f"{time.time():.3f}", "ok")


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
