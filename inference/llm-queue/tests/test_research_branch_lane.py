"""research-workbench P4a (rw-p4a-trees, D16 as amended by the operator 2026-10-26).

The research-branch lane is ALL of the plane's declared slots (LLM_QUEUE_SLOTS): no share, no floor
and no second cap of its own. An undeclared capacity runs the lane at the queue's default slot count
with a logged warning. How much research actually runs is the queue's decision (priority classes;
the chat reservation and class-level research cap are item llmq-research-share). The research
service reads the lane back through the GET-only /observe/queue/estimate.
"""

import asyncio
import json

import httpx
import pytest

from llm_queue.app_state import AppState
from llm_queue.config import DEFAULT_SLOTS, Settings
from llm_queue.main import app
from llm_queue.policy import RESEARCH_BRANCH_KEY, research_branch_concurrency

LANE = "research-branch"


def _settings(monkeypatch, slots=None, policy_json=None, extra=None):
    for k in ("LLM_QUEUE_SLOTS", "LLM_QUEUE_RESEARCH_BRANCH_SHARE", "LLM_QUEUE_POLICY_JSON"):
        monkeypatch.delenv(k, raising=False)
    if slots is not None:
        monkeypatch.setenv("LLM_QUEUE_SLOTS", str(slots))
    if policy_json is not None:
        monkeypatch.setenv("LLM_QUEUE_POLICY_JSON", policy_json)
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)
    return Settings()


def _lane(monkeypatch, **kw) -> int | None:
    """What a reload of llm-queue (a fresh process: fresh Settings + AppState) derives."""
    state = AppState(_settings(monkeypatch, **kw))
    return state.policy.classify(LANE).max_concurrency


def test_lane_key_is_research_branch():
    assert RESEARCH_BRANCH_KEY == LANE


@pytest.mark.parametrize("slots", [1, 2, 3, 6, 9])
def test_lane_is_all_of_the_declared_slots(monkeypatch, slots):
    n, warn = research_branch_concurrency(_settings(monkeypatch, slots=slots))
    assert (n, warn) == (slots, None)
    assert _lane(monkeypatch, slots=slots) == slots, "no share, no floor"


def test_raising_slots_raises_the_lane_and_lowering_lowers_it(monkeypatch):
    low = _lane(monkeypatch, slots=3)
    high = _lane(monkeypatch, slots=9)
    back = _lane(monkeypatch, slots=3)
    assert (low, high, back) == (3, 9, 3)


def test_a_share_setting_is_not_a_cap(monkeypatch):
    # the round-1 share knob is gone: setting it changes nothing
    assert _lane(monkeypatch, slots=4, extra={"LLM_QUEUE_RESEARCH_BRANCH_SHARE": "0.25"}) == 4


@pytest.mark.parametrize("raw", [None, ""])
def test_unset_capacity_yields_the_queue_default_with_a_logged_warning(monkeypatch, capsys, raw):
    s = _settings(monkeypatch)
    if raw is not None:
        monkeypatch.setenv("LLM_QUEUE_SLOTS", raw)
        s = Settings()
    n, warn = research_branch_concurrency(s)
    assert n == DEFAULT_SLOTS == s.slots
    assert warn and "LLM_QUEUE_SLOTS" in warn
    AppState(s)
    out = capsys.readouterr()
    assert "research_branch_capacity_unreadable" in (out.out + out.err)


def test_no_second_cap_a_policy_entry_cannot_hand_set_the_lane(monkeypatch):
    pj = json.dumps({
        LANE: {"class": LANE, "rank": 3, "acceptable_wait_s": 1800, "max_concurrency": 1},
        "owui-chat": {"class": "owui-chat", "rank": 0, "acceptable_wait_s": 30},
    })
    assert _lane(monkeypatch, slots=4, policy_json=pj) == 4, "never the hand-set 1"
    pj2 = json.dumps({"owui-chat": {"class": "owui-chat", "rank": 0, "acceptable_wait_s": 30}})
    assert _lane(monkeypatch, slots=4, policy_json=pj2) == 4


def test_lane_is_arbitrated_by_the_existing_priority_policy(monkeypatch):
    state = AppState(_settings(monkeypatch, slots=3))
    assert state.policy.classify(LANE).rank > state.policy.classify("owui-chat").rank


def test_estimate_reports_the_lane_concurrency(monkeypatch):
    state = AppState(_settings(monkeypatch, slots=8))
    app.state.app = state

    async def go():
        tr = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=tr, base_url="http://q") as c:
            r = await c.get("/observe/queue/estimate", params={"key": LANE})
            return r.status_code, r.json()

    status, body = asyncio.run(go())
    assert status == 200
    assert body["priority_class"] == "ob-research", "a key of the ob-research class"
    assert body["max_concurrency"] == 8
    assert body["capacity_declared"] is True


@pytest.mark.parametrize("slots", [1, 2, 4, 8])
def test_one_cap_per_class_the_research_class_cap_is_the_lane(monkeypatch, slots):
    # union with llmq-research-share: ob-research, ob-podcast and research-branch are ONE class with
    # ONE cap; P4a sets that class cap to the lane (= all slots), never a per-key override.
    state = AppState(_settings(monkeypatch, slots=slots))
    classes = {k: state.policy.classify(k) for k in ("ob-research", "ob-podcast", LANE)}
    assert {c.name for c in classes.values()} == {"ob-research"}
    assert {c.max_concurrency for c in classes.values()} == {slots}, classes
    for k, c in state.policy.classes.items():
        if c.name == "ob-research":
            assert c.max_concurrency == slots, k
