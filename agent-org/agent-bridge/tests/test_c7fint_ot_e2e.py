"""c7f-int - OFFLINE end-to-end on the REAL worker image (opt-in): the WIP hold on a head the real
carry pushed, with the integrated bridge (design line + development).

ao-replancarry's and ao-preflight2's e2e files exercise the plan-turn check and the carry on the
real image; the WIP hold (`_finish_effort` refusing a head that is only a carried WIP commit) was
proven on host checkouts only. Here the C7e shape runs on two real containers (the replancarry
rig: `--network none --rm`, origin = a bare repo on a labelled volume), the real carry pushes the
stopped turn's work to `agent/<effort>`, and then the delivery chokepoint is handed exactly the
head that is on that origin - after a simulated bridge restart (carried heads re-read from the
audit log). It must be HELD (no PR, not done); a real commit on top of the WIP must not be.

Run (agent-org/agent-bridge, Docker reachable):
  PYTHONIOENCODING=utf-8 AO_E2E_OT_IMAGE=little-coder-open-terminal:dotnet8-gui \
      AO_E2E_TEST_LABEL=ao-c7fint python -m pytest -q -s tests/test_c7fint_ot_e2e.py
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.capabilities import BranchDelivery
from test_ao_replancarry_ot_e2e import (
    CODING_FILE,
    IMAGE,
    _orch,
    _origin_ref,
    _run,
)
import test_ao_replancarry_ot_e2e as _rig

pytestmark = pytest.mark.skipif(not IMAGE, reason="opt-in: set AO_E2E_OT_IMAGE")
# the replancarry rig's module-scoped `pool` fixture (two real containers + a bare origin)
globals()["pool"] = _rig.pool


def _landed(eid: str, head: str) -> BranchDelivery:
    return BranchDelivery(verifiable=True, exists=True, ahead=1, files_changed=1, head_sha=head,
                          branch=f"agent/{eid}")


def _record(orch) -> dict:
    rec: dict = {"prs": [], "cards": []}

    async def open_pr(effort_id, repo, branch, *, merge_id=None, verified_sha="", body_extra=""):
        rec["prs"].append((branch, verified_sha))
        return ""
    orch._open_delivery_pr = open_pr
    orig_card = orch.router.update_effort_card

    async def card(effort_id, state, *a, **kw):
        rec["cards"].append(state)
        return await orig_card(effort_id, state, *a, **kw)
    orch.router.update_effort_card = card
    return rec


async def test_e2e_a_head_that_is_only_the_real_carried_wip_is_held(db_url, pool):
    orch, db, trace = await _orch(db_url, pool, plan_on_w2=True)
    try:
        eid = await _run(orch, db, "c7fint-wip-hold")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried and carried["pushed"] and carried["files"] == [CODING_FILE], carried
        head = _origin_ref(pool, f"agent/{eid}")
        assert head == carried["commit"]
        rec = _record(orch)
        orch._carried_heads.clear()            # a bridge restart: re-read from the audit log
        n0 = await orch._event_count(eid, "delivery_wip_only_held")
        await orch._finish_effort(eid, SimpleNamespace(status="done", output="all green"),
                                  delivery=_landed(eid, head))
        print(f"\n[e2e c7fint wip-hold] effort={eid} head={head[:12]} rec={rec}")
        assert rec["prs"] == [], rec
        assert "done" not in rec["cards"] and "needs-attention" in rec["cards"], rec
        assert await orch._event_count(eid, "delivery_wip_only_held") == n0 + 1
    finally:
        await db.dispose()


async def test_e2e_real_work_on_top_of_the_real_carried_wip_is_not_held(db_url, pool):
    orch, db, trace = await _orch(db_url, pool, plan_on_w2=True)
    try:
        eid = await _run(orch, db, "c7fint-wip-ontop")
        carried = await orch._last_event_payload(eid, "stopped_turn_work_carried")
        assert carried and carried["pushed"], carried
        w2 = pool[1]
        w2.operator(
            "cd /workspace && G=/usr/bin/git.real && printf 'def run():\\n    return 2\\n' > "
            "inference/llm-queue/src/queue.py && $G add -A && $G -c user.name=w "
            "-c user.email=w@x.invalid commit -q -m fix && "
            f"$G push -q /origin/repo.git HEAD:refs/heads/agent/{eid}")
        head = _origin_ref(pool, f"agent/{eid}")
        assert head and head != carried["commit"]
        orch._carried_heads.clear()
        await orch._load_carried_heads(eid)
        assert not orch._wip_only(eid, _landed(eid, head))
        assert orch._wip_only(eid, _landed(eid, carried["commit"]))
    finally:
        await db.dispose()
