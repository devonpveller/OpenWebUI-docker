"""LIVE recall probe - one real effort, against the real Open Brain (memory-plane PLAN §3).

WHAT THIS EXISTS TO ANSWER, and why nothing else could. PLAN §3's acceptance is "a confirmed
memory measurably appears in a worker brief; a pending one never does". Every proof on this
branch until now used `httpx.MockTransport`: the seams were exercised against a fake plane, so
the whole read path - the REST twin's routing, the bge-m3 embedding lane, the review gate in
SQL, the two-phase ranking - was assumed rather than run. `agent_memory_recall_traces` had
ZERO rows: recall had never executed against a real server, on any branch, ever.

So this runs the ORCHESTRATOR ITSELF - `_intake_or_dispatch`, the real seam-1 injection, the
real goal freeze, a real worker brief - with only the worker harness and the chat adapter
faked. There is no `transport=` override anywhere below: `orch.memory` speaks real HTTP to
`http://openbrain-mcp:8000`. That is the point.

RUN IT THROUGH `scripts/checks/smoke-agent-memory-live.ps1`, which plants nothing itself,
owns the cleanup, and asserts the corpus is back where it started. This file is the half that
has to run INSIDE a container on `obnet`, because openbrain-mcp publishes no host port.

Class-4 boundary (PLAN §C.2): the fixtures written here are SYNTHETIC and stamped `ops`. The
personal plane is never read, never written, and the wrapper fails the run if a personal row
appears. Nothing here is a real memory of anything.

Env: OB_URL, OB_KEY, OB_PROJECT, OB_TAG.
Prints one JSON object on stdout; exits non-zero on any failed assertion.
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, "/w/agent-org/agent-bridge")

import httpx  # noqa: E402

from app.adapters.chat import FakeChatAdapter  # noqa: E402
from app.config import Settings  # noqa: E402
from app.db import Database  # noqa: E402
from app.modules.model_router import FakeModelClient  # noqa: E402
from app.orchestrator import Orchestrator  # noqa: E402
from app.worker.harness import FakeHarness  # noqa: E402

BRIDGE = pathlib.Path("/w/agent-org/agent-bridge")
URL = os.environ["OB_URL"].rstrip("/")
KEY = os.environ["OB_KEY"]
PROJECT = os.environ.get("OB_PROJECT", "u6-live-smoke")
TAG = os.environ["OB_TAG"]

# The two fixtures. Same subject, so the ONLY thing separating them at recall time is the
# review gate - if the gate were missing, both would come back and the test would say so.
CONFIRMED_SUMMARY = (
    f"[{TAG}] the admission queue holds one lane per caller, so a 429 under fan-out is the "
    f"per-model concurrency limit and not a gateway fault"
)
PENDING_SUMMARY = (
    f"[{TAG}] the admission queue was rewritten to drop the per-caller lanes entirely, so a "
    f"429 under fan-out means the queue is disabled"
)
QUERY = "why does the admission queue return 429 under fan-out"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))


async def rpc(client: httpx.AsyncClient, tool: str, args: dict) -> dict:
    r = await client.post(
        URL + "/",
        headers={"x-brain-key": KEY, "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": tool, "arguments": args}},
    )
    body = r.text
    if body.lstrip().startswith("event:") or "data:" in body[:200]:
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[5:].strip()
                break
    return {"status": r.status_code, "body": json.loads(body) if body.strip() else {}}


async def rest(client: httpx.AsyncClient, path: str, payload: dict) -> dict:
    r = await client.post(URL + path, headers={"x-brain-key": KEY}, json=payload)
    return {"status": r.status_code,
            "body": r.json() if r.headers.get("content-type", "").startswith("application/json")
            else r.text}


def mcp_json(resp: dict):
    """The JSON an MCP tool returned, out of the text block it is wrapped in.

    A tool result is `{result:{content:[{type:"text",text:"<json>"}]}}`, so a substring
    check against the outer envelope is checking an ESCAPED string and will report
    whatever it likes. Parse it."""
    try:
        blocks = ((resp.get("body") or {}).get("result") or {}).get("content") or []
        return json.loads(blocks[0]["text"])
    except Exception:  # noqa: BLE001
        return None


async def plant(client: httpx.AsyncClient) -> dict:
    """Two synthetic ops memories through the LIVE writeback door; confirm exactly one."""
    ids = {}
    for label, summary in (("confirmed", CONFIRMED_SUMMARY), ("pending", PENDING_SUMMARY)):
        out = await rest(client, "/agent-memory/writeback", {
            "workspace_id": "ai-stack",
            "project_id": PROJECT,
            "summary": summary,
            "content": summary + " (synthetic fixture written by the U6 live recall smoke; "
                                 "it is deleted by the wrapper when the run finishes)",
            "memory_type": "lesson",
            "idempotency_key": f"{TAG}-{label}",
        })
        check(f"writeback[{label}] accepted by the live door", out["status"] == 200,
              json.dumps(out)[:400])
        ids[label] = ((out.get("body") or {}) if isinstance(out.get("body"), dict) else {}).get(
            "memory_id", "")
        check(f"writeback[{label}] returned a memory id", bool(ids[label]), str(out)[:200])

    rev = await rpc(client, "agent_memory_review", {
        "memory_id": ids["confirmed"], "action": "confirm",
        # `actor` is an OBJECT and it is REQUIRED - the schema refuses an audit row that
        # records a decision without recording who made it.
        "actor": {"label": "u6-live-smoke"},
        "note": "synthetic fixture, confirmed to prove the review gate",
    })
    text = json.dumps(rev)
    check("the review door moved ONE fixture pending -> confirmed",
          rev["status"] == 200 and "confirmed" in text and "isError" not in text, text[:400])

    # EXPOSURE IS ASSERTED, NOT ASSUMED (PLAN 1.1 + the class-4 line). The door stamps it,
    # and a fixture that landed on the PERSONAL plane would (a) be invisible to the default
    # recall scope, making the positive half of this smoke fail for a reason that has
    # nothing to do with the review gate, and (b) put a personal row on the live plane,
    # which this work is not allowed to do. It happened on the first run: the tag was a
    # 14-digit timestamp, `detectPii`'s payment-card pattern matches any 13-16 digit run,
    # and both fixtures were silently demoted to `personal`.
    for label, mid in ids.items():
        got = await rpc(client, "agent_memory_inspect", {"memory_id": mid})
        mem = (mcp_json(got) or {}).get("memory") or {}
        exposure = str((mem.get("metadata") or {}).get("exposure") or mem.get("exposure") or "")
        check(f"fixture[{label}] was stamped ops by the door, not personal",
              exposure == "ops", f"exposure={exposure!r} {json.dumps(mem)[:300]}")
    return ids


async def one_real_effort(db_url: str, tmp: pathlib.Path) -> dict:
    """A real effort through the real intake path, with a real plane behind the seam."""
    (tmp / "app.pem").write_text("dummy")
    s = Settings(
        _env_file=None, chat_adapter="fake",
        profiles_dir=str(BRIDGE / "profiles"), charters_dir=str(BRIDGE / "charters"),
        floor_dir=str(BRIDGE / "floor"), worker_instance_urls="http://w1:8090",
        max_concurrent_workers=1, database_url=db_url, project_survey_enabled=False,
        review_mode="off", plan_approval="off",
    )
    s.memory_recall_enabled = True
    s.memory_writeback_enabled = False
    s.openbrain_key = KEY
    s.openbrain_url = URL
    db = Database(db_url)
    orch = Orchestrator(s, db, FakeChatAdapter(), model_client=FakeModelClient(),
                        harness=FakeHarness())
    await orch.setup()
    # NO transport override. This is the whole point of the file.
    try:
        await orch.projects.add(PROJECT, "")
        eid, chan, root = await orch.router.open_effort("fix", project=PROJECT)
        orch.harness.output_queue = ["did the work", "pushed"]
        orch.harness.check_queue = [(0, "ok", False)]
        await orch._intake_or_dispatch(eid, chan, root, QUERY,
                                       reply_prefix="", mgmt_channel=chan)
        for _ in range(4):
            if orch._bg_tasks:
                await asyncio.gather(*list(orch._bg_tasks), return_exceptions=True)
        _v, goal, _st = await orch.charters.current_goal(eid)
        briefs = [w["prompt"] for w in orch.harness.wakes]
        return {"goal": goal, "briefs": briefs, "effort": eid}
    finally:
        for t in (orch._capacity_task, orch._stall_task, orch._reaper_task):
            if t is not None:
                t.cancel()
        await db.dispose()


async def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp())
    async with httpx.AsyncClient(timeout=120.0) as client:
        ids = await plant(client)
        if not all(ids.values()):
            print(json.dumps({"ok": False, "ids": ids,
                              "checks": [list(r) for r in results]}))
            return 1
        out = await one_real_effort(f"sqlite+aiosqlite:///{tmp / 'probe.db'}", tmp)

    goal, briefs = out["goal"], out["briefs"]
    brief = "\n".join(briefs)
    check("the effort dispatched a worker at all", bool(briefs), f"{len(briefs)} wakes")
    check("the versioned goal carries a recall block from the LIVE plane",
          "RELEVANT MEMORIES" in goal, goal[-600:])
    check("THE CONFIRMED MEMORY REACHED THE WORKER BRIEF",
          CONFIRMED_SUMMARY[:60] in brief, brief[-800:] if brief else "(no brief)")
    check("THE PENDING MEMORY NEVER DID", PENDING_SUMMARY[:60] not in brief, "")
    check("the pending memory is not in the versioned goal either",
          PENDING_SUMMARY[:60] not in goal, "")

    ok = all(r[1] for r in results)
    print(json.dumps({"ok": ok, "ids": ids, "checks": [list(r) for r in results],
                      "goal_tail": goal[-1200:]}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
