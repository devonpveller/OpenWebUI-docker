"""grounding — ground an effort's assumptions via the shared openbrain-research service (P4.0a).

UX-FLOW Stage 4 step (a): *before touching real code*, ground the plan's assumptions against
Open Brain's grounded-claim corpus. This is **advisory** context injected as steering, **not** a
gate — the risk-gated dry-run (execution_gate) is the gate. So grounding is **best-effort + OFF by
default** (like the OB audit mirror): if the research service is slow/unavailable, the effort
proceeds without it rather than blocking.

Contract (verified against `owui/tools/deep_research.py` + the digest/podcast clients):
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


def _is_gap(text: str) -> bool:
    """The research engine signals 'the corpus/web had nothing usable on this' with a `[GAP]` marker
    (or a 'provided sources do not contain' phrasing). Such a result has no real answer — treat it as
    UNGROUNDED so a caller never injects a non-answer (or the stale/irrelevant reused claims that ride
    along with a gap) as if it were grounded fix context."""
    t = (text or "").strip().lower()
    return (t.startswith("[gap]") or "do not contain information" in t
            or "provided sources do not contain" in t or not t)


def _extract(result: dict[str, Any]) -> tuple[str, list[str]]:
    """Pull the SESSION REPORT + validated-claim strings out of a research result, tolerant of shape.
    The openbrain-research result carries the written report as `synthesis` (a distilled answer) AND
    `prose` (the fuller narrative report), and the VALIDATED claims under `reuse_claims`
    (reused/validated from the corpus, shaped `{id, text}`) — NOT `claims`/`grounded_claims`. Reading
    only `synthesis`/`claims` silently dropped the fuller report and EVERY validated claim (operator
    2026-07-12: 'the research returned the sources and research data but not the session report which
    contains the validated claims and useful information'). Read the fuller written output + all claim
    shapes, so a schema tweak never silently loses the report again."""
    synthesis = (result.get("synthesis") or "").strip()
    prose = (result.get("prose") or "").strip()
    summary = prose if len(prose) > len(synthesis) else synthesis
    if not summary:
        summary = (result.get("summary") or result.get("report")
                   or result.get("session_report") or result.get("final_report") or "").strip()
    claims: list[str] = []
    seen: set[str] = set()
    for key in ("claims", "grounded_claims", "reuse_claims", "validated_claims"):
        raw = result.get(key)
        if not isinstance(raw, list):
            continue
        for c in raw:
            if isinstance(c, str):
                t = c
            elif isinstance(c, dict):
                t = c.get("text") or c.get("claim") or c.get("statement") or ""
            else:
                t = ""
            t = str(t).strip()
            if t and t not in seen:
                seen.add(t)
                claims.append(t)
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


# The research engine's error prefix for a run that FINISHED but whose report the curator could
# not file (OB1 research-service lib.ts classifyCuratorOutcome). Kept as a fallback signal only:
# the primary one is the full `result` the engine writes on that path and never on a crash.
_UNFILED_PREFIX = "curator: the research completed but was not filed"


def classify_job(st: dict[str, Any]) -> str:
    """How a polled research job stands: "done" | "unfiled" | "crashed" | "cancelled" | the live
    status ("queued"/"running"/...). C5 (oh-health, 2026-10-07).

    The engine writes status='error' for TWO different things (research-service index.ts):
      - a CRASH (the run threw): `error` = the exception, `result` never written (NULL);
      - FINISHED BUT NOT FILED (the curator honesty gate): the full report IS in `result`, and
        `error` starts "curator: the research completed but was NOT filed into Open Brain".
    Lumping them threw a real, finished report away exactly like a crash. A `result` that holds
    a written report, or that error prefix, means "unfiled"; anything else is a crash."""
    status = st.get("status")
    if status != "error":
        return str(status or "")
    result = st.get("result")
    if isinstance(result, dict):
        summary, _ = _extract(result)
        if summary or str(result.get("rendered") or "").strip():
            return "unfiled"
    if str(st.get("error") or "").strip().lower().startswith(_UNFILED_PREFIX):
        return "unfiled"
    return "crashed"


def _not_saved_line(job_id: str, st: dict[str, Any]) -> str:
    err = str(st.get("error") or "").strip()
    reason = err.split(" - ", 1)[1] if " - " in err else (err or "unknown")
    return (f"> \u26a0 Not saved to Open Brain: research job `{job_id}` finished, but filing its "
            f"report failed ({reason[:200]}). The answer below is real; it is not in the corpus.")


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
    """Drives the openbrain-research async job API (POST + state-driven poll)."""

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.transport: httpx.BaseTransport | None = None   # injectable for tests

    async def ground(self, question: str, *, context: str = "") -> GroundingResult:
        base = self.s.research_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        query = question if not context else f"{question}\n\nContext:\n{context}"
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as c:
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
                    state = classify_job(st)
                    if state in ("done", "unfiled"):
                        # "unfiled": the run FINISHED and its report is real - only filing it into
                        # Open Brain failed. Use it (as the OWUI tool does, behind its banner) and
                        # say so via `outcome`, instead of discarding it like a crash.
                        summary, claims = _extract(st.get("result") or {})
                        if _is_gap(summary):
                            # the engine found nothing usable — not grounded (and don't inject the
                            # stale reused claims a gap drags along). The caller escalates honestly.
                            log.info("grounding job %s returned a GAP — treating as ungrounded", job_id)
                            return GroundingResult(grounded=False, job_id=job_id, outcome="gap")
                        if state == "unfiled":
                            log.warning("grounding job %s finished but was NOT filed into Open "
                                        "Brain: %s", job_id, st.get("error"))
                        return GroundingResult(
                            grounded=True, claims=claims, summary=summary, job_id=job_id,
                            outcome="filed" if state == "done" else "unfiled",
                        )
                    if state in ("crashed", "cancelled"):
                        log.warning("grounding job %s ended %s: %s", job_id, state, st.get("error"))
                        return GroundingResult(grounded=False, job_id=job_id, outcome=state)
                log.info("grounding job %s still running past %ss — proceeding without it",
                         job_id, self.s.grounding_timeout_s)
                return GroundingResult(grounded=False, job_id=job_id, outcome="timeout")
        except Exception as exc:  # noqa: BLE001 - grounding is advisory, never blocks execution
            log.warning("grounding call failed (proceeding without): %s", exc)
            return GroundingResult(grounded=False, outcome="unreachable")

    async def advise(self, question: str, *, context: str = "",
                     on_progress=None) -> AdvisoryAnswer:
        """Run a full research job for an operator's design question and return the synthesis + its
        cited sources.

        STATE-DRIVEN, not time-gated (operator-caught: a fixed timeout abandoned a job ~35 s before
        it finished): the loop decides from the JOB'S OWN STATE each poll —
          - `done`            → grounded answer (or reason="empty" if the synthesis is blank);
          - `error/cancelled` → reason="failed" (a real failure, fall back now);
          - alive (queued/running) → KEEP WAITING; `on_progress(state)` fires on every state change
            + a ~5-min heartbeat so the caller can keep the operator informed instead of silent;
          - engine unreachable → bounded consecutive-failure retries (a poll blip ≠ a dead job);
            still down after that → reason="unreachable";
          - `advisory_timeout_s` remains ONLY as a runaway backstop (job claims alive for hours) →
            reason="backstop"."""
        base = self.s.research_url.rstrip("/")
        headers = {"Content-Type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        query = question if not context else f"{question}\n\nContext:\n{context}"
        poll = self.s.grounding_poll_interval_s
        try:
            async with httpx.AsyncClient(timeout=30.0, transport=self.transport) as c:
                r = await c.post(
                    f"{base}/research",
                    headers=headers,
                    json={"query": query.strip(), "origin": "agent-org-advisory"},
                )
                r.raise_for_status()
                job_id = r.json().get("job_id")
                if not job_id:
                    return AdvisoryAnswer(grounded=False, reason="failed")
                waited = 0.0
                unreachable = 0
                last_sig: tuple = ()
                since_progress = 0.0
                while waited < self.s.advisory_timeout_s:   # runaway BACKSTOP, not a decision gate
                    await asyncio.sleep(poll)
                    waited += poll
                    since_progress += poll
                    try:
                        st = (await c.get(f"{base}/research/jobs/{job_id}", headers=headers)).json()
                        unreachable = 0
                    except Exception as exc:  # noqa: BLE001 — a poll blip must not kill a live job
                        unreachable += 1
                        if unreachable >= 12:   # ~1 min of consecutive failures at the 5s prod poll
                            log.warning("advisory job %s: engine unreachable: %s", job_id, exc)
                            return AdvisoryAnswer(grounded=False, job_id=job_id, reason="unreachable")
                        continue
                    status = st.get("status")
                    state = classify_job(st)
                    if state in ("done", "unfiled"):
                        result = st.get("result") or {}
                        synthesis, claims = _extract(result)
                        if _is_gap(synthesis):
                            return AdvisoryAnswer(grounded=False, job_id=job_id, reason="empty")
                        # Fold the validated claims into the answer so the operator's synthesis
                        # carries the "session report" content, not just the distilled line.
                        answer = synthesis
                        if claims:
                            answer += "\n\n**Grounded claims:**\n" + "\n".join(
                                f"- {c}" for c in claims[:12])
                        reason = ""
                        if state == "unfiled":
                            # C5: a finished report the engine failed to FILE is still the answer;
                            # the operator is told it is not in Open Brain, never handed a crash.
                            log.warning("advisory job %s finished but was NOT filed: %s",
                                        job_id, st.get("error"))
                            answer = _not_saved_line(job_id, st) + "\n\n" + answer
                            reason = "unfiled"
                        return AdvisoryAnswer(
                            grounded=True, answer=answer,
                            sources=_extract_sources(result), job_id=job_id, reason=reason,
                        )
                    if state in ("crashed", "cancelled"):
                        log.warning("advisory job %s ended %s: %s", job_id, state, st.get("error"))
                        return AdvisoryAnswer(grounded=False, job_id=job_id, reason="failed")
                    # alive (queued/running) → keep waiting. Surface progress ONLY on a MEANINGFUL
                    # transition — the status or the engine's discrete `progress.phase` (e.g.
                    # gather → synthesize) — or a 10-min heartbeat. NEVER on the churning
                    # message/counters (operator-caught: keying on the whole progress dict posted
                    # ~8 updates/min — noise, not transparency).
                    prog = st.get("progress") or {}
                    phase = (prog.get("phase") or "") if isinstance(prog, dict) else ""
                    sig = (status, st.get("queue_position"), phase)
                    if on_progress and (sig != last_sig or since_progress >= 600.0):
                        last_sig = sig
                        since_progress = 0.0
                        try:
                            await on_progress({"status": status,
                                               "queue_position": st.get("queue_position"),
                                               "phase": phase,
                                               "message": (prog.get("message") or ""
                                                           ) if isinstance(prog, dict) else "",
                                               "waited_s": int(waited)})
                        except Exception:  # noqa: BLE001 — progress UX must never kill the poll
                            pass
                log.warning("advisory job %s hit the runaway backstop (%ss) still alive",
                            job_id, self.s.advisory_timeout_s)
                return AdvisoryAnswer(grounded=False, job_id=job_id, reason="backstop")
        except Exception as exc:  # noqa: BLE001 - degrade to a labelled local answer, never crash
            log.warning("advisory research call failed: %s", exc)
            return AdvisoryAnswer(grounded=False, reason="unreachable")


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

    async def advise(self, question: str, *, context: str = "",
                     on_progress=None) -> AdvisoryAnswer:
        self.advice_calls.append(question)
        if on_progress is not None:   # exercise the transparency path once
            await on_progress({"status": "running", "queue_position": None, "waited_s": 0})
        return self.advice


def build_grounding(settings: Settings) -> Grounding:
    """Fake in the `fake` chat-adapter/dev mode; the real research client otherwise."""
    if settings.chat_adapter == "fake":
        return FakeGrounding()
    return OpenBrainResearchGrounding(settings)
