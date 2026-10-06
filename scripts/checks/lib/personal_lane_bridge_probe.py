"""The agent-bridge path for smoke-openbrain-personal-lane.ps1 (item amp-owui-deny).

Runs agent-bridge's OWN client code (app/modules/openbrain_memory.py, mounted from the tree
under test) against the rig's openbrain-mcp, holding the full key as agent-bridge does
(AO_OPENBRAIN_KEY = MCP_ACCESS_KEY). The image only supplies the dependencies. Reports one
JSON object; the rig asserts.
"""
from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

from app.modules.openbrain_memory import OpenBrainMemory


async def main() -> dict:
    s = SimpleNamespace(
        openbrain_url=os.environ["RAW_URL"],
        openbrain_key=os.environ["FULL_KEY"],
        memory_writeback_enabled=True,
        memory_recall_enabled=True,
    )
    m = OpenBrainMemory(s)
    out: dict = {}
    out["write"] = await m.write({
        "workspace_id": "ws-amp-owui",
        "project_id": "proj-amp-owui",
        "summary": "amp-owui-deny probe via agent-bridge",
        "content": "a lesson written by the amp-owui-deny rig through agent-bridge's own client",
        "memory_type": "lesson",
        "idempotency_key": "amp-owui-agent-bridge",
    })
    trace, items = await m.recall_traced(project="proj-amp-owui", query="amp-owui-deny probe lesson")
    out["recall_trace_id"] = trace
    out["recall_items"] = len(items)
    # report_usage needs a memory id; any id works for the wire check only if it exists, so
    # the rig passes the id of the row the write above created.
    mid = os.environ.get("USAGE_MEMORY_ID", "")
    out["report_usage"] = (await m.report_usage(memory_id=mid, used=False, trace_id=trace)) if mid else None
    return out


if __name__ == "__main__":
    print(json.dumps(asyncio.run(main())))
