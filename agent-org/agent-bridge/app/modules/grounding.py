"""grounding — ground an effort's assumptions via the shared openbrain-research service (P4.0a).

UX-FLOW Stage 4 step (a): *before touching real code*, ground the plan's assumptions against
Open Brain's grounded-claim corpus. This is **advisory** context injected as steering, **not** a
gate — the risk-gated dry-run (execution_gate) is the gate. So grounding is **best-effort + OFF by
default** (like the OB audit mirror): if the research service is slow/unavailable, the effort
proceeds without it rather than blocking.

Contract (verified against `smolcrawl/deep_research_thin_client.py` + the digest/podcast clients):
  POST /research {query, origin, options}     -> {job_id}
  GET  /research/jobs/{job_id}                -> {status, progress, result:{synthesis, cited_sources, ...}}
Reach the service BY CONTAINER NAME on `ai-stack_llm-net` (`http://openbrain-research:8000`) — the
:8818 host port is loopback-only and unreachable from a container.

The seam is a small Protocol so the whole flow is testable with `FakeGrounding` (no OB1/GPU).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import httpx

from ..config import Settings
from ..schemas import AdvisoryAnswer, GroundingResult

log = logging.getLogger("agent_bridge.grounding")


def _extract(result: dict[str, Any]) -> tuple[str, list[str]]:
    """Pull a summary + a flat list of grounded-claim strings out of a research result,
    tolerant of shape (claims may be strings or {text}/{claim} dicts)."""
    summary = (result.get("synthesis") or result.get("summary") or "").strip()
    claims: list[str] = []
    raw = result.get("claims") or result.get("grounded_claims") or []
    if isinstance(raw, list):
        for c in raw:
            if isinstance(c, str):
                claims.append(c)
            elif isinstance(c, dict):
                t = c.get("text") or c.get("claim") or c.get("statement")
                if t:
                    claims.append(str(t))
    return summary, claims


def _extract_sources(result: dict[str, Any]) -> list[str]:
    """Pull cited source URLs/titles out of a research result, tolerant of shape (a source may be a
    bare URL string or a {url,title}/{link}/{source} dict). De-duplicated, order preserved."""
    raw = (result.get("cited_sources") or result.get("sources")
           or result.get("citations") or [])
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for s in raw:
            label = ""
            if isinstance(s, str):
                label = s.strip()
            elif isinstance(s, dict):
                url = (s.get("url") or s.get("link") or s.get("href") or "").strip()
                title = (s.get("title") or s.get("name") or "").strip()
                label = f"{title} — {url}" if title and url else (url or title)
            if label and label not in seen:
                seen.add(label)
                out.append(label)
    return out


class Grounding(Protocol):
    async def ground(self, question: str, *, context: str = "") -> GroundingResult:
        """Submit a grounding question; return grounded claims (best-effort)."""
        ...

    async def advise(self, question: str, *, context: str = "") -> AdvisoryAnswer:
        """Answer a design/architecture question with a research-grounded, CITED synthesis (Tier 2).
        `grounded=False` on the result means research was unavailable — the caller falls back to a
        clearly-labelled ungrounded local answer."""
        ...


class OpenBrainResearchGrounding:
    """Drives the openbrain-research async job API (POST + poll), bounded by a timeout."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings

    async def ground(self, question: str, *, context: str = "") -> GroundingResult:
        base = self.s.research_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        query = question if not context else f"{question}\n\nContext:\n{context}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(
                    f"{base}/research",
                    headers=headers,
                    json={"query": query.strip(), "origin": "agent-org"},
                )
                r.raise_for_status()
                job_id = r.json().get("job_id")
                if not job_id:
                    return GroundingResult(grounded=False)
                waited = 0.0
                while waited < self.s.grounding_timeout_s:
                    await asyncio.sleep(self.s.grounding_poll_interval_s)
                    waited += self.s.grounding_poll_interval_s
                    st = (await c.get(f"{base}/research/jobs/{job_id}", headers=headers)).json()
                    status = st.get("status")
                    if status == "done":
                        summary, claims = _extract(st.get("result") or {})
                        return GroundingResult(
                            grounded=True, claims=claims, summary=summary, job_id=job_id
                        )
                    if status in ("error", "cancelled"):
                        log.warning("grounding job %s ended %s", job_id, status)
                        return GroundingResult(grounded=False, job_id=job_id)
                log.info("grounding job %s still running past %ss — proceeding without it",
                         job_id, self.s.grounding_timeout_s)
                return GroundingResult(grounded=False, job_id=job_id)
        except Exception as exc:  # noqa: BLE001 - grounding is advisory, never blocks execution
            log.warning("grounding call failed (proceeding without): %s", exc)
            return GroundingResult(grounded=False)

    async def advise(self, question: str, *, context: str = "") -> AdvisoryAnswer:
        """Run a full research job for an operator's design question and return the synthesis + its
        cited sources. Uses the (longer) advisory timeout — the operator is actively waiting. On any
        failure/timeout returns grounded=False so the caller degrades to a labelled local answer."""
        base = self.s.research_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        query = question if not context else f"{question}\n\nContext:\n{context}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as c:
                r = await c.post(
                    f"{base}/research",
                    headers=headers,
                    json={"query": query.strip(), "origin": "agent-org-advisory"},
                )
                r.raise_for_status()
                job_id = r.json().get("job_id")
                if not job_id:
                    return AdvisoryAnswer(grounded=False)
                waited = 0.0
                while waited < self.s.advisory_timeout_s:
                    await asyncio.sleep(self.s.grounding_poll_interval_s)
                    waited += self.s.grounding_poll_interval_s
                    st = (await c.get(f"{base}/research/jobs/{job_id}", headers=headers)).json()
                    status = st.get("status")
                    if status == "done":
                        result = st.get("result") or {}
                        synthesis, _claims = _extract(result)
                        if not synthesis:
                            return AdvisoryAnswer(grounded=False, job_id=job_id)
                        return AdvisoryAnswer(
                            grounded=True, answer=synthesis,
                            sources=_extract_sources(result), job_id=job_id,
                        )
                    if status in ("error", "cancelled"):
                        log.warning("advisory job %s ended %s", job_id, status)
                        return AdvisoryAnswer(grounded=False, job_id=job_id)
                log.info("advisory job %s still running past %ss", job_id, self.s.advisory_timeout_s)
                return AdvisoryAnswer(grounded=False, job_id=job_id)
        except Exception as exc:  # noqa: BLE001 - degrade to a labelled local answer, never crash
            log.warning("advisory research call failed: %s", exc)
            return AdvisoryAnswer(grounded=False)


class FakeGrounding:
    """Deterministic in-memory grounding for tests/dev. Records every question; returns a
    canned grounded result so the ground→inject→dispatch flow is exercisable without OB1."""

    def __init__(
        self, result: GroundingResult | None = None,
        advice: AdvisoryAnswer | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.advice_calls: list[str] = []
        self.result = result or GroundingResult(
            grounded=True, claims=["(fake grounded claim)"], summary="fake synthesis"
        )
        # Default: a grounded, cited advisory answer. Set `advice=AdvisoryAnswer(grounded=False)` to
        # exercise the local-model fallback path.
        self.advice = advice or AdvisoryAnswer(
            grounded=True, answer="fake grounded advice",
            sources=["Example Source — https://example.com/a"],
        )

    async def ground(self, question: str, *, context: str = "") -> GroundingResult:
        self.calls.append(question)
        return self.result

    async def advise(self, question: str, *, context: str = "") -> AdvisoryAnswer:
        self.advice_calls.append(question)
        return self.advice


def build_grounding(settings: Settings) -> Grounding:
    """Fake in the `fake` chat-adapter/dev mode; the real research client otherwise."""
    if settings.chat_adapter == "fake":
        return FakeGrounding()
    return OpenBrainResearchGrounding(settings)
