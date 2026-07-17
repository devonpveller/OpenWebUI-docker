"""Watch one agent-org effort to a terminal state and print WHY it ended.

P9 Phase 0 needed this because the gym runner is coupled to the Claude session that launched it:
when that process exits the runner dies, while the org (Docker-resident) keeps building. The
factory outlived its observer and nothing noticed. This watcher only reads the org's own audit, so
it can be re-armed at any time against a round already in flight.

Silence is not success: it exits on the FAILURE states too (undelivered, closure-invariant, a
re-raised human gate), because a watcher that only greps for the happy path looks identical to a
hung one.

Usage: python gym-watch-effort.py <effort_id> [poll_seconds]
"""

from __future__ import annotations

import json
import subprocess
import sys
import time

DONE = {"delivery_pr_opened", "effort_undelivered", "closure_invariant_failed",
        "org_build_unverifiable", "effort_abandoned"}

PROBE = r"""
import urllib.request, json
eid = %r
d = json.load(urllib.request.urlopen('http://localhost:8000/audit?effort_id=' + eid + '&limit=400'))
ev = d.get('events', d) if isinstance(d, dict) else d
s = json.load(urllib.request.urlopen('http://localhost:8000/scheduler'))
inst = [i for i in s['instances'] if i.get('effort_id') == eid]
print(json.dumps({
    'kinds': [e.get('kind') for e in ev],
    'last': [e.get('kind') for e in ev[-3:]],
    'n': len(ev),
    'state': (inst[0]['state'] if inst else 'none'),
    'waiting_on': (inst[0].get('waiting_on') if inst else None),
}))
"""


def probe(eid):
    out = subprocess.run(
        ["docker", "exec", "agent-bridge", "python", "-c", PROBE % eid],
        capture_output=True, text=True, timeout=90)
    for line in out.stdout.strip().splitlines()[::-1]:
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError("probe failed: %s" % (out.stderr or out.stdout)[:200])


def main():
    eid = sys.argv[1]
    poll = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    seen = 0
    while True:
        try:
            p = probe(eid)
        except Exception as exc:                      # a transient docker/socket blip is not an end
            print("probe error (continuing): %s" % str(exc)[:120], flush=True)
            time.sleep(poll)
            continue
        if p["n"] != seen:
            print("[%s] events=%-3d state=%-10s last=%s"
                  % (time.strftime("%H:%M:%S"), p["n"], p["state"], ",".join(p["last"])), flush=True)
            seen = p["n"]
        # a re-raised human gate is a STOP: the round is parked, not progressing
        if p["waiting_on"]:
            print("WAITING ON HUMAN: %s" % json.dumps(p["waiting_on"]), flush=True)
            return
        hit = DONE.intersection(p["kinds"])
        if hit:
            print("TERMINAL: %s" % ",".join(sorted(hit)), flush=True)
            return
        time.sleep(poll)


if __name__ == "__main__":
    main()
