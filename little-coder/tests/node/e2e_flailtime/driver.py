"""ao-flailtime end-to-end driver: the REAL `AgentRunner._run_cli` (real `_FlailWatcher`, real
`kill_process_group`) around the REAL little-coder launcher + bundled pi, with real command execution
(a stub ot-exec that runs the command with `sh -c`) and a scripted stub model (stub_model.py).

Which littlecoder is imported is whatever PYTHONPATH puts first (run.sh mounts the base or the tip
source at /lcsrc). Prints one JSON line: the trip (signal, detail), the watcher's clock (tip only),
and the ground truth measured outside the guard: model-server request time (stub log) and command
time (stub ot-exec log). Numbers only; no secrets are involved (the API key is the literal "stub").
"""
import json
import os
import sys
import time
from types import SimpleNamespace

import littlecoder
from littlecoder import agent
from littlecoder.config import FlailConfig

OUT = os.environ["E2E_OUT"]
SECONDS = int(os.environ.get("FLAIL_SECONDS", "20"))

seen = {}
Real = agent._FlailWatcher


class Recording(Real):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        seen["w"] = self


agent._FlailWatcher = Recording

cfg = FlailConfig(seconds=SECONDS, min_tool_calls=8, poll_seconds=2.0,
                  **({"clock_seconds": 0.5} if "clock_seconds" in FlailConfig.model_fields else {}))
records = []
runner = agent.AgentRunner.__new__(agent.AgentRunner)
runner.cfg = SimpleNamespace(flail=cfg, workspace=SimpleNamespace(path="/tmp/ws"))
runner.journals = SimpleNamespace(write=records.append)
state = SimpleNamespace(flail_guard=True, plan_only=False, signal=None, detail=None, answer=None,
                        agent_process=None)
ctx = SimpleNamespace(state=state, error=lambda kind, detail, tool=None: {"kind": kind, "tool": tool})
cmd = ["little-coder", "--model", "llamacpp/local-large", "--print", "--mode", "json",
       "--append-system-prompt", "/app/agent-knowledge/environment.md",
       "--exclude-tools", "ShellSession,ShellStart,BrowserNavigate,webfetch,websearch",
       "--no-approve", "--no-session", "Run the commands."]
pi_events = os.path.join(OUT, "pi.jsonl")
t0 = time.time()
rc = runner._run_cli(cmd, None, dict(os.environ), 600, ctx, pi_events)
wall = time.time() - t0


def spans(path, start_tag, end_tag):
    out, cur = [], None
    if not os.path.exists(path):
        return out
    for ln in open(path, encoding="utf-8"):
        p = ln.split()
        if p and p[0] == start_tag:
            cur = float(p[1])
        elif p and p[0] == end_tag and cur is not None:
            out.append((cur, float(p[1])))
            cur = None
    return out


reqs = []
for ln in open(os.path.join(OUT, "model.log"), encoding="utf-8"):
    p = ln.split()
    if p and p[0] == "req":
        reqs.append({"round": int(p[1]), "attempt": int(p[2]), "s": round(float(p[4]) - float(p[3]), 2),
                     "outcome": p[5]})
cmds = spans(os.path.join(OUT, "otexec.log"), "start", "end")
types = {}
for ln in open(pi_events, encoding="utf-8", errors="replace"):
    try:
        ev = json.loads(ln)
    except ValueError:
        continue
    k = ev.get("type")
    if k == "message_end" and isinstance(ev.get("message"), dict) and ev["message"].get("role") == "assistant":
        k = "assistant_end:" + str(ev["message"].get("stopReason"))
    types[k] = types.get(k, 0) + 1
w = seen.get("w")
clock = getattr(w, "clock", None)
res = {
    "littlecoder": littlecoder.__file__,
    "seconds_cfg": SECONDS,
    "rc": rc,
    "signal": state.signal,
    "detail": state.detail,
    "answer_has_marker": (state.answer or "").startswith(agent.FLAIL_MARKER),
    "wall_s": round(wall, 1),
    "clock": None if clock is None else {"model_s": round(clock.model_s, 1), "tool_s": round(clock.tool_s, 1)},
    "meter": None if w is None else {"calls": w.meter.calls, "edits": w.meter.edits, "novel": w.meter.novel},
    "truth": {
        "model_requests": len(reqs),
        "model_request_s": round(sum(r["s"] for r in reqs), 1),
        "failed_attempts": [r for r in reqs if r["outcome"] != "ok"],
        "commands": len(cmds),
        "command_s": round(sum(e - s for s, e in cmds), 1),
        "command_overlap": any(cmds[i + 1][0] < cmds[i][1] for i in range(len(cmds) - 1)),
    },
    "event_types": types,
}
print(json.dumps(res))
with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as fh:
    json.dump(res, fh, indent=1)
sys.exit(0)
