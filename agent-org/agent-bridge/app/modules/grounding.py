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
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol

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


# ── Deadline-bounded research jobs (knowledge package, kp-research) ─────────────────────────
# Contract read 2026-10-14 from OB1 integrations/research-service (index.ts + README + jobs.ts):
#   POST /research {query, origin, deadline_at?, gap_research?, seed_sources?, options?}
#        -> 202 {job_id: <uuid>, status: "queued", deadline_at?}; 400 on a deadline_at that is not
#           ISO-8601 WITH a zone; 401 without the key.
#   GET  /research/jobs/<uuid> -> {id, status: queued|running|done|error|cancelled,
#        progress:{phase,message,code?, model_down?}, result, error, model:{state,...}}
#   POST /research/jobs/<uuid>/cancel {reason?} -> 200 cancelled | 202 cancelling |
#        409 {filing: true} (the curator is already filing: the job ends with its real outcome)
#        | 200 already terminal | 404.
# `origin` is stored only when it is one of owui|agent|notebook|manual (anything else is stored
# as "owui"), so this client sends "agent". `gap_research` is honoured by the engine ONLY in
# article mode (harness.ts gapResearch) - the client passes it through, it does not pretend it
# changes a default-mode run.
RESEARCH_ORIGIN = "agent"
_JOB_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_LIVE = ("queued", "running")
MAX_BODY_BYTES = 4 * 1024 * 1024        # a job row (result incl. prose/rendered) is ~100-300 KB
MAX_SUBMIT_BODY_BYTES = 64 * 1024       # the 202 submit body is ~150 bytes
MAX_POLL_FAILURES = 3                   # consecutive failed polls -> "unreachable"
CANCEL_TIMEOUT_S = 5.0                  # a cancel sent BEFORE the deadline (unreachable/model down)
CANCEL_GRACE_S = 0.5                    # a cancel sent AT the deadline: run_job ends <= budget + this


@dataclass(frozen=True)
class ResearchJob:
    """How one deadline-bounded research job ended.

    state (closed set): done | unfiled | crashed | cancelled | timeout | unreachable | model_down
    | rejected | unauthorized | malformed | failed. Only `done` and `unfiled` carry a `result`; a
    caller decides what an unfiled result is worth (the knowledge package treats it as OPEN)."""

    state: str
    job_id: str | None = None
    result: dict[str, Any] | None = None
    cancel_sent: bool = False


def _iso_z(dt: datetime) -> str:
    """The engine refuses a zone-less deadline (jobs.ts ISO_WITH_ZONE); always send UTC `Z`."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _read_json_bounded(resp: httpx.Response, cap: int = MAX_BODY_BYTES) -> Any:
    """Read a streamed response body up to `cap` bytes; a larger body is refused (C6), never cut
    and parsed. Raises ValueError on an oversize or non-JSON body."""
    buf = bytearray()
    async for chunk in resp.aiter_bytes():
        buf.extend(chunk)
        if len(buf) > cap:
            raise ValueError("body over cap")
    return json.loads(bytes(buf).decode("utf-8"))


def _job_model_down(st: dict[str, Any]) -> bool:
    """Did THIS job stop because the model is down? Only the job's own fields count: the
    `progress.code` / `progress.model_down` that pauseForModelDown writes on it, or an `error`
    starting `model_down:`. The row's `model` key is the engine's PROCESS-WIDE latch snapshot
    (llm.ts ModelHealth.snapshot) and says nothing about why this job crashed or was cancelled."""
    prog = st.get("progress") if isinstance(st.get("progress"), dict) else {}
    return (prog.get("code") == "model_down" or prog.get("model_down") is True
            or str(st.get("error") or "").startswith("model_down:"))


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
        # Time seams for the deadline-bounded `run_job` (tests drive a fake clock).
        self.clock: Callable[[], float] = time.monotonic
        self.sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
        self.now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.s.research_key:
            headers["x-brain-key"] = self.s.research_key
        return headers

    async def run_job(
        self, query: str, *, budget_s: float, gap_research: str | None = None,
        seed_sources: list[dict[str, str]] | None = None,
        contract: Mapping[str, Any] | None = None,
    ) -> ResearchJob:
        """Submit ONE research job with a caller deadline and wait for it, never past the deadline.

        - `deadline_at` = now + budget_s is sent to the engine, so a queued job past it is never
          started and a running one is aborted engine-side even if this process dies.
        - The local wait is bounded by the same budget on a monotonic clock AND by a poll count
          (C6); every HTTP call is bounded by the time left (httpx timeout AND asyncio.wait_for,
          so the bound does not depend on the transport); no poll is sent after the deadline.
        - On the deadline the job is cancelled with a CANCEL_GRACE_S bound, so the call returns
          within budget_s + CANCEL_GRACE_S even when the engine is wedged.
        - Every failure maps to a closed state; no engine text is returned except `result`.
        - `contract` (kp-contract): when given, sent as `options.contract` - the engine's per-job
          source scope (classes and/or allow/deny entries, OB1 research-service contract.ts), which
          it applies to search hits, fetches, redirects, KB recall and reused claims. Not given =
          the request body is exactly as before. A malformed contract fails the job engine-side."""
        start = self.clock()
        end = start + max(0.0, budget_s)
        poll = max(0.01, float(self.s.grounding_poll_interval_s))
        max_polls = int(math.ceil(max(0.0, budget_s) / poll)) + 2
        base = self.s.research_url.rstrip("/")
        headers = self._headers()
        body: dict[str, Any] = {
            "query": query.strip(), "origin": RESEARCH_ORIGIN,
            "deadline_at": _iso_z(self.now() + timedelta(seconds=max(1.0, budget_s))),
        }
        if gap_research is not None:
            if gap_research != "preliminary":   # the only value the engine accepts (index.ts)
                raise ValueError("gap_research must be 'preliminary' or None")
            body["gap_research"] = gap_research
        if seed_sources:
            body["seed_sources"] = [
                {"url": str(s.get("url") or ""), "title": str(s.get("title") or ""),
                 "content": str(s.get("content") or "")} for s in seed_sources]
        if contract is not None:
            # a JSON copy: what is sent is what was passed at call time, nothing aliased
            body["options"] = {"contract": json.loads(json.dumps(dict(contract)))}

        def left() -> float:
            return end - self.clock()

        def early_cancel_t() -> float:
            # a cancel before the deadline may use the time left plus the grace, at most 5 s
            return min(CANCEL_TIMEOUT_S, max(0.0, left()) + CANCEL_GRACE_S)

        job_id: str | None = None
        try:
            async with httpx.AsyncClient(transport=self.transport) as c:
                if left() <= 0:
                    return ResearchJob("timeout")

                async def submit(t: float) -> tuple[int, Any]:
                    async with c.stream("POST", f"{base}/research", headers=headers, json=body,
                                        timeout=t) as rr:
                        if rr.status_code >= 400:
                            return rr.status_code, None
                        return rr.status_code, await _read_json_bounded(rr, MAX_SUBMIT_BODY_BYTES)

                try:
                    t = min(30.0, left())
                    code, sub = await asyncio.wait_for(submit(t), timeout=t)
                except asyncio.TimeoutError:
                    return ResearchJob("timeout" if left() <= 0 else "unreachable")
                except httpx.HTTPError:
                    return ResearchJob("unreachable")
                except (ValueError, UnicodeDecodeError):
                    return ResearchJob("malformed")
                if code >= 500:
                    return ResearchJob("unreachable")
                if code in (401, 403):
                    return ResearchJob("unauthorized")
                if code >= 400:
                    return ResearchJob("rejected")
                jid = sub.get("job_id") if isinstance(sub, dict) else None
                if not isinstance(jid, str) or not _JOB_ID_RE.match(jid):
                    return ResearchJob("malformed")   # never interpolate an unchecked id into a URL
                job_id = jid
                fails = 0

                async def get_status(t: float) -> tuple[int, Any]:
                    async with c.stream("GET", f"{base}/research/jobs/{job_id}",
                                        headers=headers, timeout=t) as pr:
                        if pr.status_code >= 400:
                            return pr.status_code, None
                        return pr.status_code, await _read_json_bounded(pr)

                for _ in range(max_polls):
                    if left() <= 0:
                        break
                    await self.sleep(min(poll, max(0.0, left())))
                    if left() <= 0:
                        break                      # never poll after the deadline
                    try:
                        # floor 0.05 s: the post-sleep check above is THE guard against a poll
                        # after the deadline, not a zero wait_for that happens to cancel it
                        t = min(30.0, max(0.05, left()))
                        code, st = await asyncio.wait_for(get_status(t), timeout=t)
                    except (ValueError, UnicodeDecodeError):
                        sent = await self._cancel(c, base, headers, job_id, "malformed reply",
                                                  early_cancel_t())
                        return ResearchJob("malformed", job_id, cancel_sent=sent)
                    except (httpx.HTTPError, asyncio.TimeoutError):
                        code, st = -1, None
                    if code in (401, 403):
                        return ResearchJob("unauthorized", job_id)
                    if code == 404:
                        return ResearchJob("failed", job_id)
                    if code < 200 or code >= 400:
                        fails += 1
                        if fails >= MAX_POLL_FAILURES:
                            sent = await self._cancel(c, base, headers, job_id, "unreachable",
                                                      early_cancel_t())
                            return ResearchJob("unreachable", job_id, cancel_sent=sent)
                        continue
                    fails = 0
                    if not isinstance(st, dict):
                        sent = await self._cancel(c, base, headers, job_id, "malformed reply",
                                                  early_cancel_t())
                        return ResearchJob("malformed", job_id, cancel_sent=sent)
                    status = st.get("status")
                    if status in _LIVE:
                        if _job_model_down(st):
                            sent = await self._cancel(c, base, headers, job_id, "model down",
                                                      early_cancel_t())
                            return ResearchJob("model_down", job_id, cancel_sent=sent)
                        continue
                    state = classify_job(st)
                    if state in ("done", "unfiled"):
                        res = st.get("result")
                        if not isinstance(res, dict):
                            return ResearchJob("malformed", job_id)
                        return ResearchJob(state, job_id, res)
                    if state == "cancelled":
                        prog = st.get("progress") if isinstance(st.get("progress"), dict) else {}
                        if prog.get("code") == "deadline":
                            return ResearchJob("timeout", job_id)
                        if _job_model_down(st):
                            return ResearchJob("model_down", job_id)
                        return ResearchJob("cancelled", job_id)
                    if state == "crashed":
                        return ResearchJob("model_down" if _job_model_down(st) else "crashed",
                                           job_id)
                    return ResearchJob("malformed", job_id)   # a status outside the closed set
                sent = await self._cancel(c, base, headers, job_id, "deadline", CANCEL_GRACE_S)
                return ResearchJob("timeout", job_id, cancel_sent=sent)
        except asyncio.CancelledError:
            if job_id:
                try:
                    async with httpx.AsyncClient(transport=self.transport) as c2:
                        await asyncio.shield(self._cancel(c2, base, headers, job_id,
                                                          "caller gone", CANCEL_GRACE_S))
                except BaseException:  # noqa: BLE001 - best effort; the engine deadline still holds
                    pass
            raise
        except Exception as exc:  # noqa: BLE001 - every failure is a closed state, never a crash
            log.warning("research job %s failed (%s)", job_id, type(exc).__name__)
            return ResearchJob("failed", job_id)

    async def _cancel(self, c: httpx.AsyncClient, base: str, headers: dict[str, str],
                      job_id: str, reason: str, timeout_s: float) -> bool:
        """Best-effort cancel bounded by `timeout_s`. True = the engine acknowledged
        (200/202/409). The engine-side deadline_at still ends the job if this fails."""
        t = max(0.05, timeout_s)
        try:
            r = await asyncio.wait_for(
                c.post(f"{base}/research/jobs/{job_id}/cancel", headers=headers,
                       json={"reason": f"agent-org knowledge package: {reason}"}, timeout=t),
                timeout=t)
            return r.status_code in (200, 202, 409)
        except Exception:  # noqa: BLE001
            return False

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
