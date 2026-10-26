"""research-workbench P4a (rw-p4a-trees, D16): the research-branch lane.

The lane's concurrency is a configured SHARE of the plane's declared capacity
(LLM_QUEUE_SLOTS), floor 2, a warning when the capacity is not declared, and it is
the ONE number: a policy entry cannot hand-set a second cap for it. The research
service reads the derived value back through the GET-only /observe/queue/estimate.
"""

import asyncio
import json

import httpx
import pytest

from llm_queue.app_state import AppState
from llm_queue.config import Settings
from llm_queue.main import app
from llm_queue.policy import RESEARCH_BRANCH_KEY, research_branch_concurrency

LANE = "research-branch"


def _settings(monkeypatch, slots=None, share=None, policy_json=None):
    for k in ("LLM_QUEUE_SLOTS", "LLM_QUEUE_RESEARCH_BRANCH_SHARE", "LLM_QUEUE_POLICY_JSON"):
        monkeypatch.delenv(k, raising=False)
    if slots is not None:
        monkeypatch.setenv("LLM_QUEUE_SLOTS", str(slots))
    if share is not None:
        monkeypatch.setenv("LLM_QUEUE_RESEARCH_BRANCH_SHARE", str(share))
    if policy_json is not None:
        monkeypatch.setenv("LLM_QUEUE_POLICY_JSON", policy_json)
    return Settings()


def _lane(monkeypatch, **kw) -> int | None:
    """What a reload of llm-queue (a fresh process: fresh Settings + AppState) derives."""
    state = AppState(_settings(monkeypatch, **kw))
    return state.policy.classify(LANE).max_concurrency


def test_lane_key_is_research_branch():
    assert RESEARCH_BRANCH_KEY == LANE


def test_lane_is_a_share_of_declared_capacity(monkeypatch):
    s = _settings(monkeypatch, slots=6, share=0.5)
    n, warn = research_branch_concurrency(s)
    assert n == 3 and warn is None


def test_raising_slots_raises_the_lane_and_lowering_lowers_it(monkeypatch):
    low = _lane(monkeypatch, slots=3)
    high = _lane(monkeypatch, slots=9)
    back = _lane(monkeypatch, slots=3)
    assert high > low, f"raising LLM_QUEUE_SLOTS 3 -> 9 must raise the lane ({low} -> {high})"
    assert back == low and back < high, "lowering LLM_QUEUE_SLOTS lowers the lane again"


@pytest.mark.parametrize("raw", [None, ""])
def test_unset_capacity_yields_2_with_a_logged_warning(monkeypatch, capsys, raw):
    if raw is None:
        s = _settings(monkeypatch)
    else:
        s = _settings(monkeypatch)
        monkeypatch.setenv("LLM_QUEUE_SLOTS", raw)
        s = Settings()
    n, warn = research_branch_concurrency(s)
    assert n == 2
    assert warn and "LLM_QUEUE_SLOTS" in warn
    AppState(s)
    out = capsys.readouterr()
    assert "research_branch_capacity_unreadable" in (out.out + out.err)
    # the scheduler still has a P (unchanged default) - only the lane says it is guessing
    assert s.slots >= 1


@pytest.mark.parametrize("slots", [1, 2])
def test_the_lane_never_drops_to_1(monkeypatch, slots):
    assert _lane(monkeypatch, slots=slots, share=0.1) == 2


def test_no_second_cap_a_policy_entry_cannot_hand_set_the_lane(monkeypatch):
    pj = json.dumps({
        LANE: {"class": LANE, "rank": 3, "acceptable_wait_s": 1800, "max_concurrency": 7},
        "owui-chat": {"class": "owui-chat", "rank": 0, "acceptable_wait_s": 30},
    })
    n = _lane(monkeypatch, slots=4, share=0.5, policy_json=pj)
    assert n == 2, f"the lane is derived (4 x 0.5 = 2), never the hand-set 7 (got {n})"
    # and without the policy entry the lane still exists
    pj2 = json.dumps({"owui-chat": {"class": "owui-chat", "rank": 0, "acceptable_wait_s": 30}})
    assert _lane(monkeypatch, slots=4, share=0.5, policy_json=pj2) == 2


def test_lane_is_arbitrated_by_the_existing_priority_policy(monkeypatch):
    state = AppState(_settings(monkeypatch, slots=3))
    lane = state.policy.classify(LANE)
    chat = state.policy.classify("owui-chat")
    assert lane.rank > chat.rank, "chat outranks the branch lane (no reserved slice)"


def test_estimate_reports_the_lane_concurrency(monkeypatch):
    s = _settings(monkeypatch, slots=8, share=0.5)
    state = AppState(s)
    app.state.app = state

    async def go():
        tr = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://q") as c:
            r = await c.get("/observe/queue/estimate", params={"key": LANE})
            return r.status_code, r.json()

    status, body = asyncio.run(go())
    assert status == 200
    assert body["priority_class"] == LANE
    assert body["max_concurrency"] == 4
    assert body["capacity_declared"] is True
