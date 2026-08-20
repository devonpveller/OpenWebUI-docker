"""State-driven advisory polling (operator-caught: a fixed timeout abandoned a research job ~35 s
before it finished). The loop decides from the JOB'S OWN STATE — done → grounded; error → failed;
alive → keep waiting (with transparent progress callbacks); engine down → bounded retries; the
timeout survives ONLY as a runaway backstop. Mocked engine; tiny poll interval."""

from __future__ import annotations

import httpx

from app.config import Settings
from app.modules.grounding import OpenBrainResearchGrounding


def _settings(**over):
    base = dict(_env_file=None, chat_adapter="fake", grounding_poll_interval_s=0.01,
                advisory_timeout_s=2.0)
    base.update(over)
    return Settings(**base)


def _engine(statuses: list[dict], submitted: dict):
    """Mock research engine: POST /research → job id; each status GET pops the next scripted state
    (the last one repeats)."""
    seq = list(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        p = request.url.path
        if p.endswith("/research") and request.method == "POST":
            submitted["hit"] = True
            return httpx.Response(200, json={"job_id": "job-1"})
        if "/research/jobs/" in p:
            st = seq.pop(0) if len(seq) > 1 else seq[0]
            return httpx.Response(200, json=st)
        return httpx.Response(404)

    return httpx.MockTransport(handler)


async def test_slow_job_is_waited_for_not_abandoned():
    """A job that is queued → running for MANY polls then done must return grounded — the old
    fixed gate would have given up. Progress callbacks fire on state changes (transparency)."""
    g = OpenBrainResearchGrounding(_settings())
    states = ([{"status": "queued", "queue_position": 2}] * 3
              + [{"status": "running"}] * 60
              + [{"status": "done", "result": {"synthesis": "grounded!", "cited_sources": ["s1"]}}])
    g.transport = _engine(states, {})
    seen: list[dict] = []

    async def prog(state):
        seen.append(state)

    ans = await g.advise("q", on_progress=prog)
    assert ans.grounded and ans.answer == "grounded!" and ans.sources == ["s1"]
    assert any(s["status"] == "queued" for s in seen)      # transitions surfaced to the operator
    assert any(s["status"] == "running" for s in seen)


async def test_progress_fires_on_phase_transitions_not_churning_counters():
    """LIVE regression (~8 posts/min): the engine's progress.message/counters churn EVERY poll — the
    callback must key on the discrete `progress.phase` (+ status/queue) only, so the operator gets
    one update per MILESTONE (gather → synthesize), not one per poll."""
    g = OpenBrainResearchGrounding(_settings())
    states = (
        [{"status": "running", "progress": {"phase": "gather", "message": f"staged {i}"}}
         for i in range(30)]                                   # 30 polls, SAME phase, churning msg
        + [{"status": "running", "progress": {"phase": "synthesize", "message": "writing"}}] * 10
        + [{"status": "done", "result": {"synthesis": "ok", "cited_sources": []}}]
    )
    g.transport = _engine(states, {})
    seen: list[dict] = []

    async def prog(state):
        seen.append(state)

    ans = await g.advise("q", on_progress=prog)
    assert ans.grounded
    assert len(seen) == 2                                      # gather + synthesize — NOT ~40
    assert seen[0]["phase"] == "gather" and seen[1]["phase"] == "synthesize"


async def test_failed_job_reports_failed_not_unreachable():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine([{"status": "running"}, {"status": "error"}], {})
    ans = await g.advise("q")
    assert not ans.grounded and ans.reason == "failed"     # truthful: the JOB failed


async def test_engine_down_mid_job_reports_unreachable_after_bounded_retries():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/research"):
            return httpx.Response(200, json={"job_id": "job-1"})
        calls["n"] += 1
        raise httpx.ConnectError("down", request=request)

    g = OpenBrainResearchGrounding(_settings())
    g.transport = httpx.MockTransport(handler)
    ans = await g.advise("q")
    assert not ans.grounded and ans.reason == "unreachable"
    assert calls["n"] >= 12                                 # retried, didn't die on the first blip


async def test_runaway_backstop_reports_backstop():
    g = OpenBrainResearchGrounding(_settings(advisory_timeout_s=0.05))
    g.transport = _engine([{"status": "running"}], {})
    ans = await g.advise("q")
    assert not ans.grounded and ans.reason == "backstop"    # job claimed alive forever → backstop


async def test_done_with_empty_synthesis_reports_empty():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine([{"status": "done", "result": {"synthesis": ""}}], {})
    ans = await g.advise("q")
    assert not ans.grounded and ans.reason == "empty"
