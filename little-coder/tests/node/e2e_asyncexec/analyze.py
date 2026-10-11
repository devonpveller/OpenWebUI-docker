"""ao-asyncexec e2e analysis: for a run dir, take every pi-stdout snapshot taken while a stub
ot-exec command was running, and report (a) whether the tool_execution_start of that call is in it
and (b) what the bridge's REAL CommandWatch + judge_frozen make of exactly those lines.
Run from agent-org/agent-bridge (imports app.worker.command_watch)."""
import glob, json, os, sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd())
from app.worker.command_watch import CommandWatch, judge_frozen

run = sys.argv[1]
spans, cur = [], None
for ln in open(os.path.join(run, "otexec.log")):
    p = ln.split()
    if p[0] == "start":
        cur = [float(p[1]), " ".join(p[2:])]
    elif p[0] == "end" and cur:
        spans.append((cur[0], float(p[1]), cur[1])); cur = None
print("commands:", [(c, round(e - s, 1)) for s, e, c in spans])
for s, e, c in spans:
    mid = [f for f in sorted(glob.glob(os.path.join(run, "snap-*.jsonl")))
           if s + 1.0 < float(os.path.basename(f).split("-", 2)[2][:-6]) < e - 0.5]
    if not mid:
        print(f"  {c!r}: no snapshot strictly inside the run"); continue
    for f in (mid[0], mid[len(mid) // 2], mid[-1]):
        lines = [x for x in open(f, encoding="utf-8").read().split("\n") if x]
        types = [json.loads(x).get("type") for x in lines]
        starts = [json.loads(x).get("toolCallId") for x in lines if json.loads(x).get("type") == "tool_execution_start"]
        ends = [json.loads(x).get("toolCallId") for x in lines if json.loads(x).get("type") == "tool_execution_end"]
        now = datetime.now(timezone.utc)
        cw = CommandWatch("t"); cw.feed(lines, now)
        hung, info = judge_frozen(cw, 600, now + timedelta(seconds=30), silence_s=300,
                                  default_timeout_s=1800, grace_s=120, ceiling_s=3600)
        t = float(os.path.basename(f).split("-", 2)[2][:-6])
        print(f"  {c!r} +{t - s:4.1f}s of {e - s:4.1f}s: {len(lines)} lines, last={types[-1] if types else None}, "
              f"starts={starts}, ends={ends}, open={sorted(cw.open)} -> hung={hung} rule={info['rule']}")
