"""
title: Deep Research (thin client)
author: ai-stack / Open Brain
version: 1.1.0
description: >
  Thin OWUI client for the shared Open Brain research engine (Research Engine
  P5). Submits the query to openbrain-research `POST /research`, polls the job,
  streams progress (including its position in the engine's one-at-a-time queue),
  and renders the GROUNDED synthesis inline. The engine runs research jobs
  sequentially, so a job may wait in a queue behind others first; this tool shows
  that queue position and keeps the chat blocked on the single call until the
  result is ready — the model simply waits, it does not get a partial result to
  act on. ALL the harness logic
  (discover → stage full content → reuse grounded claims → gap analysis →
  synthesize → enforce grounding → curate) lives server-side; this tool carries
  none of it. Replaces the heavy in-tool harness once openbrain-research is
  deployed and reachable from OWUI.

  Grounding guarantees (enforced server-side, see GROUNDING-MODEL.md): the stored
  synthesis is verbatim, only cited sources are linked, and nothing ungrounded is
  stored or reused — a premature stop degrades to honest [GAP]s, never fabrication.
"""

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import aiohttp
from pydantic import BaseModel, Field


class Tools:
    class Valves(BaseModel):
        research_url: str = Field(
            default="http://host.docker.internal:8818",
            description="Base URL of the openbrain-research service (loopback 8818 on the OB1 host, or http://openbrain-research:8000 if OWUI shares its network).",
        )
        brain_key: str = Field(
            default="",
            description="MCP_ACCESS_KEY — authenticates the research request (must match the OB1 stack key).",
        )
        poll_interval_sec: float = Field(
            default=2.0, description="How often to poll the job for progress."
        )
        max_wait_sec: int = Field(
            default=3600,
            description="Block up to this many seconds for the job to finish. The engine runs research one-at-a-time, so a job may wait in a queue first; the chat shows its queue position while waiting and returns the synthesis inline when done. Only past this ceiling does it give up (the job still finishes server-side and is cached, so asking again retrieves it). Raise it if you queue many jobs at once.",
        )
        confidence_floor: float = Field(
            default=0.50,
            description="Reuse floor: claims below this are re-researched, not reused.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def deep_research(
        self,
        query: str,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Run a grounded research effort via the shared Open Brain research engine.

        :param query: The research question.
        :return: The grounded synthesis (markdown) with cited sources + any gaps.
        """
        v = self.valves
        base = v.research_url.rstrip("/")
        headers = {"Content-Type": "application/json", "x-brain-key": v.brain_key}

        async def emit(desc: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__(
                    {
                        "type": "status",
                        "data": {"description": desc, "done": done},
                    }
                )

        if not query or not query.strip():
            return "Please provide a research question."

        try:
            async with aiohttp.ClientSession() as session:
                # 1. Submit the job (OD-3 async job+poll).
                await emit("Submitting research request…")
                submit_body = {
                    "query": query.strip(),
                    "origin": "owui",
                    "options": {"confidence_floor": v.confidence_floor},
                }
                async with session.post(
                    f"{base}/research", headers=headers, json=submit_body
                ) as r:
                    if r.status == 401:
                        return "Research engine rejected the request (check brain_key valve)."
                    if r.status >= 400:
                        return f"Research engine error {r.status}: {(await r.text())[:300]}"
                    job = await r.json()
                job_id = job.get("job_id")
                if not job_id:
                    return (
                        f"Research engine returned no job id: {json.dumps(job)[:300]}"
                    )

                # 2. Poll until terminal (or max_wait). The engine runs research
                # jobs ONE AT A TIME, so this job may sit in a queue behind others
                # first — show its queue position while waiting and keep blocking
                # until the grounded synthesis is ready (the call doesn't return a
                # partial result, so the model just waits and can't interfere).
                waited = 0.0
                result = None
                last_q = None
                while waited < v.max_wait_sec:
                    await asyncio.sleep(v.poll_interval_sec)
                    waited += v.poll_interval_sec
                    async with session.get(
                        f"{base}/research/jobs/{job_id}", headers=headers
                    ) as r:
                        if r.status >= 400:
                            return f"Research engine error polling job {job_id}: {r.status}"
                        st = await r.json()
                    status = st.get("status")
                    if status == "done":
                        result = st.get("result") or {}
                        break
                    if status == "error":
                        return f"Research failed: {st.get('error', 'unknown error')}"
                    if status == "cancelled":
                        return "Research was cancelled."
                    if status == "queued":
                        # Behind other jobs — surface position (queue_position /
                        # queue_depth are absent on an un-upgraded backend → generic).
                        pos, depth = st.get("queue_position"), st.get("queue_depth")
                        msg = (
                            f"Queued — position {pos} of {depth}; waiting for the current research to finish…"
                            if pos
                            else "Queued — waiting for the current research to finish…"
                        )
                        if msg != last_q:
                            await emit(msg)
                            last_q = msg
                    else:  # running
                        prog = st.get("progress") or {}
                        if prog.get("message"):
                            await emit(f"{prog.get('phase', 'working')}: {prog['message']}")
                if result is None:
                    return f"Research is still running (job {job_id}); it will finish server-side. Ask again to retrieve it (the result is cached)."

            # 3. Render the grounded synthesis.
            await emit("Done.", done=True)
            return _render(result)

        except aiohttp.ClientError as e:
            return (
                f"Could not reach the research engine at {base} ({e}). "
                f"Is openbrain-research deployed and reachable from OWUI? "
                f"(set the research_url valve)."
            )
        except Exception as e:  # noqa: BLE001 — surface, never crash the chat
            return f"Unexpected error talking to the research engine: {e}"


def _render(result: dict[str, Any]) -> str:
    """Render the job result into a chat-friendly grounded answer."""
    synthesis = (result.get("synthesis") or "").strip() or "(no synthesis produced)"
    parts = [synthesis]

    cited = result.get("cited_sources") or []
    if cited:
        lines = ["\n\n---\n\n**Sources** (only those the synthesis cited):"]
        for i, s in enumerate(cited, 1):
            title = s.get("title") or s.get("url") or f"Source {i}"
            url = s.get("url")
            lines.append(f"{i}. [{title}]({url})" if url else f"{i}. {title}")
        parts.append("\n".join(lines))

    gaps = result.get("gaps") or []
    backstop = result.get("backstop")
    incomplete = bool(gaps) or (backstop and backstop != "complete")

    if gaps:
        parts.append(
            "\n\n**Open gaps** (NOT grounded — recorded for a future run):\n"
            + "\n".join(f"- {g}" for g in gaps)
        )

    # Directive to the calling model — keeps it from "finishing" with fabricated
    # content. The engine is the only grounded path; gaps are pursued by calling
    # it again, never filled from the model's own knowledge or other tools.
    if incomplete:
        reason = (
            f"stopped early ({backstop})"
            if backstop and backstop != "complete"
            else "left gaps open"
        )
        parts.append(
            f"\n\n> ⚠ This research is grounded but INCOMPLETE — it {reason}. The open "
            f"gaps above are not answered by any source. Do NOT fill them from your own "
            f"knowledge or other web/fetch tools (that fabricates). To pursue a gap, call "
            f"deep_research again with a query targeting it; otherwise present the gaps as "
            f"open unknowns."
        )

    reuse_ratio = result.get("reuse_ratio")
    foot = []
    if reuse_ratio is not None:
        foot.append(f"coverage {round(float(reuse_ratio) * 100)}%")
    if backstop and backstop != "complete":
        foot.append(f"stopped early: {backstop}")
    if foot:
        parts.append(f"\n\n_— {' · '.join(foot)}_")

    return "\n".join(parts)
