"""C5 (oh-health): a research job that CRASHED is not a research job that FINISHED BUT WAS NOT FILED.

What openbrain-research's `GET /research/jobs/{id}` can distinguish today (read 2026-10-07,
OB1 b0033a1, integrations/research-service/index.ts + lib.ts classifyCuratorOutcome):

  * crashed  - the run threw (contract refusal, LLM/DB failure ...). The catch path writes
               status='error', error=<the exception text>, and NEVER writes `result`: it stays
               NULL, so the poll returns `result: null`.
  * unfiled  - the run finished and produced a real report, but the curator could not file it
               into Open Brain. The honesty gate writes status='error' WITH the full `result`
               (synthesis, prose, cited_sources, reuse_claims, rendered ...), progress.phase
               'error' with `curator=FAILED` in progress.message, and an error that starts
               "curator: the research completed but was NOT filed into Open Brain - ".
  * partial  - sources filed, claims not: status='done' with a "curator PARTIAL" error. Already
               a usable report, unchanged here.

Before this item both grounding paths read only `status`, so an unfiled job's real report was
thrown away exactly like a crash. Mocked engine; no OB1, no docker.
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.modules.grounding import OpenBrainResearchGrounding

UNFILED = {
    "status": "error",
    "error": "curator: the research completed but was NOT filed into Open Brain - "
             "curator POST failed: connection refused",
    "progress": {"phase": "error", "message": "outcome=answered backstop=complete curator=FAILED"},
    "result": {"synthesis": "bcrypt with cost 12 is the current guidance",
               "reuse_claims": [{"id": "c1", "text": "bcrypt cost factor 12"}],
               "cited_sources": [{"title": "OWASP", "url": "https://owasp.org/x"}]},
}
CRASHED = {
    "status": "error",
    "error": "chat() failed: 503 from llm gateway",
    "progress": {"phase": "gather", "message": "searching"},
    "result": None,
}


def _settings(**over):
    base = dict(_env_file=None, chat_adapter="fake", grounding_poll_interval_s=0.01,
                advisory_timeout_s=2.0, grounding_timeout_s=2.0)
    base.update(over)
    return Settings(**base)


def _engine(final: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/research") and request.method == "POST":
            return httpx.Response(200, json={"job_id": "job-1"})
        if "/research/jobs/" in request.url.path:
            return httpx.Response(200, json=final)
        return httpx.Response(404)
    return httpx.MockTransport(handler)


def test_classify_job_separates_crashed_from_unfiled():
    from app.modules.grounding import classify_job

    assert classify_job(UNFILED) == "unfiled"
    assert classify_job(CRASHED) == "crashed"
    # the error prefix alone is enough (a result column trimmed by a future schema change)
    assert classify_job({"status": "error", "result": None, "error": UNFILED["error"]}) == "unfiled"
    # a result with no report in it is not a finished run
    assert classify_job({"status": "error", "result": {"synthesis": "", "prose": ""},
                         "error": "boom"}) == "crashed"
    assert classify_job({"status": "error"}) == "crashed"
    assert classify_job({"status": "done", "result": {"synthesis": "x"}}) == "done"
    assert classify_job({"status": "cancelled"}) == "cancelled"
    assert classify_job({"status": "running"}) == "running"


async def test_ground_uses_the_unfiled_report_and_says_it_was_not_filed():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine(UNFILED)
    res = await g.ground("how should passwords be hashed?")
    assert res.grounded and res.outcome == "unfiled"
    assert "bcrypt" in res.summary and "bcrypt cost factor 12" in res.claims
    assert res.job_id == "job-1"


async def test_ground_crash_stays_ungrounded_and_says_crashed():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine(CRASHED)
    res = await g.ground("how should passwords be hashed?")
    assert not res.grounded and res.outcome == "crashed"


async def test_advise_unfiled_returns_the_report_labelled_not_saved():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine(UNFILED)
    ans = await g.advise("q")
    assert ans.grounded and ans.reason == "unfiled"
    assert "bcrypt with cost 12" in ans.answer
    assert "Not saved to Open Brain" in ans.answer and "job-1" in ans.answer
    assert ans.sources == ["OWASP — https://owasp.org/x"]


async def test_advise_crash_still_reports_failed():
    g = OpenBrainResearchGrounding(_settings())
    g.transport = _engine(CRASHED)
    ans = await g.advise("q")
    assert not ans.grounded and ans.reason == "failed"
