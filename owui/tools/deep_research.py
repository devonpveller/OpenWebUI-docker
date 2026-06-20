"""
title: Deep Research (thin client)
author: ai-stack / Open Brain
version: 1.2.0
description: >
  Thin OWUI client for the shared Open Brain research engine (Research Engine
  P5). Submits the query to openbrain-research `POST /research`, then waits a
  SHORT bounded window for it to finish and renders the GROUNDED synthesis
  inline. The engine SERIALIZES research jobs (one at a time, so a burst can't
  flood the inference plane), so a job may sit in a queue behind others — far
  longer than a chat should block. When the job doesn't finish within the inline
  window, this tool returns a TICKET (job id + queue position + ETA) and ends the
  turn; the job keeps running server-side and its result is saved to Open Brain.
  Retrieve it later with the companion `research_status` tool (no args = list your
  recent jobs; pass a job id = fetch that result). ALL harness logic lives
  server-side; this tool carries none of it.

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
        inline_wait_sec: int = Field(
            default=90,
            description="How long to block the chat waiting for the job to finish before handing back a ticket. Covers the fast paths (cache/reuse hits, or a job at the front of an empty queue). Past this the job keeps running server-side and is retrieved later with research_status — the chat is never held open for a long queue wait.",
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

                # 2. Wait a SHORT bounded window. The engine serializes jobs, so a
                # job may queue behind others for far longer than a chat should
                # block; we only wait out the fast paths here. If it doesn't finish
                # in time, hand back a ticket (below) instead of holding the turn.
                waited = 0.0
                result = None
                last_msg = None
                st: dict[str, Any] = {}
                while waited < v.inline_wait_sec:
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
                        # Behind other jobs — surface position/ETA (fields absent on
                        # an un-upgraded backend → generic message).
                        pos, depth, eta = (
                            st.get("queue_position"),
                            st.get("queue_depth"),
                            st.get("eta_seconds"),
                        )
                        msg = (
                            f"Queued — position {pos} of {depth}"
                            + (f" (est. {_fmt_eta(eta)})" if eta else "")
                            + "…"
                            if pos
                            else "Queued — waiting for the current research to finish…"
                        )
                        if msg != last_msg:
                            await emit(msg)
                            last_msg = msg
                    else:  # running
                        prog = st.get("progress") or {}
                        if prog.get("message"):
                            await emit(f"{prog.get('phase', 'working')}: {prog['message']}")

            # 3a. Didn't finish within the inline window → return a ticket; the job
            # keeps running server-side and is retrieved later with research_status.
            if result is None:
                await emit("Still running server-side — returning a ticket.", done=True)
                return _ticket(job_id, st)

            # 3b. Finished in time → render the grounded synthesis inline.
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

    async def research_status(
        self,
        job_id: str = "",
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
        __user__: Optional[dict] = None,
    ) -> str:
        """
        Check on research submitted earlier. The engine serializes long research
        jobs, so deep_research may hand back a ticket instead of waiting; use this
        to retrieve the result once it has finished server-side.

        :param job_id: A specific research job id (from a ticket) to fetch. Leave empty to LIST your recent jobs and their status.
        :return: The grounded synthesis if the job is done; otherwise its queue status; or a list of recent jobs.
        """
        v = self.valves
        base = v.research_url.rstrip("/")
        headers = {"Content-Type": "application/json", "x-brain-key": v.brain_key}
        jid = (job_id or "").strip()
        try:
            async with aiohttp.ClientSession() as session:
                if jid and jid.lower() != "latest":
                    async with session.get(
                        f"{base}/research/jobs/{jid}", headers=headers
                    ) as r:
                        if r.status == 404:
                            return f"No research job `{jid}` found."
                        if r.status >= 400:
                            return f"Research engine error {r.status} fetching job {jid}."
                        st = await r.json()
                    status = st.get("status")
                    if status == "done":
                        return _render(st.get("result") or {})
                    if status == "error":
                        return f"Research job `{jid}` failed: {st.get('error', 'unknown error')}"
                    if status == "cancelled":
                        return f"Research job `{jid}` was cancelled."
                    return _ticket(jid, st)  # still queued/running
                # No id (or "latest") → list recent jobs so the user can pick one.
                async with session.get(
                    f"{base}/research/jobs?limit=10", headers=headers
                ) as r:
                    if r.status >= 400:
                        return f"Research engine error {r.status} listing jobs."
                    data = await r.json()
                return _render_list(data.get("jobs") or [])
        except aiohttp.ClientError as e:
            return (
                f"Could not reach the research engine at {base} ({e}). "
                f"Is openbrain-research deployed and reachable from OWUI?"
            )
        except Exception as e:  # noqa: BLE001 — surface, never crash the chat
            return f"Unexpected error talking to the research engine: {e}"


def _fmt_eta(seconds: Any) -> str:
    """Humanize an ETA in seconds → '~7m' / '~2h 10m' ('' if unknown)."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return ""
    if s <= 0:
        return ""
    m = s // 60
    return f"~{m // 60}h {m % 60}m" if m >= 60 else f"~{m}m"


def _ticket(job_id: str, st: dict[str, Any]) -> str:
    """A short receipt for a job that didn't finish inline — it runs server-side."""
    status = st.get("status") or "running"
    pos, depth, eta = st.get("queue_position"), st.get("queue_depth"), st.get("eta_seconds")
    where = f"queued — position {pos} of {depth}" if pos else status
    eta_s = f" · est. {_fmt_eta(eta)}" if eta else ""
    return (
        f"🔬 Research accepted (job `{job_id}`) — {where}{eta_s}.\n\n"
        f"It's running server-side and its grounded result will be saved to Open Brain "
        f"when complete; this chat won't wait. To retrieve it, call **research_status** "
        f"(no arguments lists recent jobs; pass `{job_id}` for this one).\n\n"
        f"> Do NOT answer the question from your own knowledge or other web/fetch tools — "
        f"that fabricates. Only the grounded Open Brain result counts."
    )


def _render_list(jobs: list[dict[str, Any]]) -> str:
    """Render a compact list of recent research jobs for the pull tool."""
    if not jobs:
        return "No recent research jobs."
    icon = {"done": "✅", "running": "⏳", "queued": "🕒", "error": "❌", "cancelled": "⚪"}
    lines = ["**Recent research jobs:**"]
    for j in jobs:
        st = j.get("status") or "?"
        short_id = (j.get("id") or "")[:8]
        query = (j.get("query") or "").strip().replace("\n", " ")
        tail = ""
        if st == "queued" and j.get("queue_position"):
            tail = f" · position {j['queue_position']}"
        elif st == "done" and j.get("finished_at"):
            tail = f" · {j['finished_at'][:16].replace('T', ' ')}"
        lines.append(f"- {icon.get(st, '•')} `{short_id}` {query}{tail}")
    lines.append("\n_Pass a job id to **research_status** to fetch its grounded result._")
    return "\n".join(lines)


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
