"""
title: Deep Research (thin client)
author: ai-stack / Open Brain
version: 1.2.0
description: >
  Thin OWUI client for the shared Open Brain research engine (Research Engine
  P5). Submits the query to openbrain-research `POST /research`. ALL the harness logic
  (discover → stage full content → reuse grounded claims → gap analysis →
  synthesize → enforce grounding → curate) lives server-side; this tool carries
  none of it. Replaces the heavy in-tool harness once openbrain-research is
  deployed and reachable from OWUI.

  Two return paths:

  - ASYNC (default). The tool passes this chat + message id to the engine, returns
    immediately, and the engine POSTs the finished report back into this message
    when the job terminates. Open WebUI persists that write whether or not a
    browser is attached, so the report lands even if the tab was closed hours ago.
    This is the only path that works for runs longer than a chat turn, and it
    stops a deep research job from pinning a chat open for an hour.
  - BLOCKING (fallback). Poll until terminal, showing queue position and progress,
    and render inline. Used when the engine has no OWUI credentials configured
    (`callback_armed: false`), in a temporary chat (no durable message to write
    to), or when the `async_callback` valve is off.

  The async path costs the one safety property blocking gave for free: the model
  regains the floor with no findings in hand. `_handoff_notice` is what holds that
  line — read it before loosening anything here.

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
            default="http://openbrain-research:8000",
            description="Base URL of the openbrain-research service (loopback 8818 on the OB1 host, or http://openbrain-research:8000 if OWUI shares its network).",
        )
        brain_key: str = Field(
            default="",
            description="MCP_ACCESS_KEY — authenticates the research request (must match the OB1 stack key).",
        )
        poll_interval_sec: float = Field(
            default=2.0, description="How often to poll the job for progress."
        )
        async_callback: bool = Field(
            default=True,
            description=(
                "Hand off instead of blocking: submit the job, return immediately, and let the "
                "engine POST the finished report back into this chat message when it is done. "
                "Requires OWUI_BASE_URL + OWUI_API_KEY on openbrain-research; if either is unset "
                "the engine reports callback_armed=false and this tool falls back to blocking. "
                "Turn OFF to force the old behaviour (the model waits, holding the turn open)."
            ),
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
        __chat_id__: Optional[str] = None,
        __message_id__: Optional[str] = None,
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
                # Async handoff: name the message the engine should write the
                # finished report into. Only the IDs travel — the engine holds the
                # OWUI base URL and key itself, so this can't be aimed elsewhere.
                # OWUI injects both ids for native tools (utils/middleware.py
                # extra_params).
                #
                # Only a SAVED chat may hand off. Open WebUI persists a callback
                # event to the message row only when the chat id carries no
                # special prefix (utils/chat_id.py is_saved_chat_id); for a
                # `temporary:`/`local:` chat the event is socket-only, so a report
                # that arrives after the reader looks away is gone for good — and
                # `channel:` ids are not addressable by this endpoint at all
                # (it resolves the id against the chats table first, and 401s).
                # Those chats keep the blocking path, where the result is returned
                # inside the turn and cannot be missed.
                saved_chat = bool(__chat_id__) and not str(__chat_id__).startswith(
                    ("temporary:", "local:", "channel:")
                )
                want_callback = bool(v.async_callback and saved_chat and __message_id__)
                if want_callback:
                    submit_body["callback"] = {
                        "chat_id": __chat_id__,
                        "message_id": __message_id__,
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

                # 1b. Hand off — but ONLY on the engine's word. `callback_armed`
                # is false when the engine has no OWUI credentials configured;
                # returning early on that promise would strand the run silently.
                # An un-upgraded engine omits the field entirely => also false.
                if want_callback and job.get("callback_armed") is True:
                    await emit("Researching in the background…")
                    return _handoff_notice(job_id)

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
                            await emit(
                                f"{prog.get('phase', 'working')}: {prog['message']}"
                            )
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


def _handoff_notice(job_id: str) -> str:
    """
    What the model sees the instant a job is handed off.

    This is the whole safety surface of the async path. On the blocking path the
    model physically could not speak between calling the tool and receiving a
    grounded report; now it gets the floor with nothing in hand, at exactly the
    moment it is most likely to be helpful from its own weights instead. The
    report will be appended to THIS message later by the engine, so anything the
    model writes now sits permanently above the real findings — a fabrication
    here is not transient, it is archived and re-read as context next turn.

    So: state the contract, and give it one legal action (stop).
    """
    return (
        f"RESEARCH HANDED OFF - job `{job_id}` is running in the background. "
        f"No findings exist yet; this tool returned nothing to summarise.\n\n"
        f"The grounded report will be appended to this very message when the engine "
        f"finishes (minutes to hours). The user does not need to stay on this page.\n\n"
        f"YOUR ONLY VALID RESPONSE NOW: one short line telling the user research is "
        f"running and will appear here when done. Then stop.\n"
        f"- Do NOT answer the question from your own knowledge - that is the exact "
        f"fabrication this engine exists to prevent, and it will be archived above "
        f"the real answer.\n"
        f"- Do NOT reach for web search, fetch, or any other tool to fill the wait.\n"
        f"- Do NOT call deep_research again for this question - the engine runs jobs "
        f"one at a time, so a duplicate only queues behind this one and doubles the wait."
    )


def _render(result: dict[str, Any]) -> str:
    """
    Render the job result into a chat-friendly grounded answer.

    The engine now renders this server-side and stores it as `result.rendered`
    (lib.ts renderResult) so the async callback and this synchronous path emit
    identical bytes. The logic below is the fallback for jobs cached before that
    field existed; keep the two in step if either changes.
    """
    rendered = result.get("rendered")
    if isinstance(rendered, str) and rendered.strip():
        return rendered

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
