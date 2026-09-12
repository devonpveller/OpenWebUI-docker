"""
title: Deep Research (thin client)
author: ai-stack / Open Brain
version: 1.5.4
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
import re
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
                        # A run can fail in two different ways and they are not
                        # the same to the reader (incident 2026-08-31). If the
                        # research itself died there is nothing to show. But a
                        # run that produced a report and then failed to FILE it
                        # into Open Brain now reports status='error' too — and
                        # throwing its report away would lose minutes of real
                        # work the user waited for. Show the findings, and say
                        # plainly that they were not saved.
                        result = st.get("result") or {}
                        if result.get("rendered") or result.get("synthesis"):
                            not_saved = (
                                "> **Not saved to Open Brain.** This research completed and the "
                                "findings below are real, but filing them failed, so they are NOT "
                                "searchable and will not appear in the wiki.\n>\n"
                                f"> Reason: {st.get('error', 'unknown error')}\n>\n"
                                f"> The full result is retained on job `{job_id}` and can be replayed.\n\n"
                            )
                            await emit("Done (not saved).", done=True)
                            return not_saved + _render(result)
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
        f"YOUR ONLY VALID RESPONSE NOW: reply with EXACTLY this line and nothing "
        f"else, then stop:\n"
        f"_Researching — this message will be replaced by the grounded report when "
        f"the engine finishes._\n"
        f"(The engine REPLACES that exact line with the report, so any other wording "
        f"stays above the findings forever.)\n"
        f"- Do NOT answer the question from your own knowledge - that is the exact "
        f"fabrication this engine exists to prevent, and it will be archived above "
        f"the real answer.\n"
        f"- Do NOT reach for web search, fetch, or any other tool to fill the wait.\n"
        f"- Do NOT call deep_research again for this question - the engine runs jobs "
        f"one at a time, so a duplicate only queues behind this one and doubles the wait."
    )


def _incomplete_directive(result: dict[str, Any]) -> str:
    """
    The one machine-addressed line an incomplete run emits.

    BYTE-IDENTICAL to lib.ts `incompleteDirective` - the plan's parity case
    compares the two renderers' whole output, and this line is the only part of
    it that is written for the model rather than the reader. An HTML comment:
    invisible in the chat, in context on the next turn, impossible to mistake
    for part of the report.
    """
    ns = result.get("needs_status")
    if isinstance(ns, list):
        open_n = sum(1 for n in ns if not (isinstance(n, dict) and n.get("status") == "answered"))
    else:
        open_n = len(result.get("gaps") or [])
    backstop = result.get("backstop")
    why = backstop if backstop and backstop != "complete" else "gaps_open"
    return (
        f"<!-- engine: incomplete ({why}); {open_n} need(s) not fully answered; "
        f"do not fill them from your own knowledge - call deep_research with a query "
        f"targeting the open question -->"
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

    # The "Open gaps (NOT grounded)" block and the INCOMPLETE banner that used to
    # sit here are GONE, in step with lib.ts renderResult. The block printed the
    # report's own limitations a second time and labelled them "not grounded"
    # even for needs the report had answered in part; the banner was a paragraph
    # addressed to a model, printed where a person reads. The directive they
    # carried is now one machine-addressed line at the very end - see
    # _incomplete_directive, which is byte-identical to the TypeScript renderer.

    # Footer parity with lib.ts renderResult (research-trust 2026-09-11).
    # `coverage NN%` is GONE from both renderers: it was 1 - gap_ratio over
    # synthesis LINES, printed where a reader looks for how much of the QUESTION
    # was answered. Job ce398d06 printed "coverage 22%" having answered 0 of 6
    # needs. A job recorded before this change carries no needs_status and now
    # gets no coverage number at all, rather than the old misleading one.
    foot = []
    needs_status = result.get("needs_status")
    if isinstance(needs_status, list) and needs_status:
        answered = sum(
            1 for n in needs_status
            if isinstance(n, dict) and n.get("status") == "answered"
        )
        # Parity with report.ts coverageFooter(). `partial` exists because dry
        # run 1f2ff740 printed "needs answered 0 of 6" beside a report stating
        # findings from 11 cited sources: the judge marks a need open, the
        # synthesis grounds lines about it, and both can be true. The footer
        # must say the second number or it contradicts its own body.
        partial = sum(
            1 for n in needs_status
            if isinstance(n, dict) and n.get("status") == "partial"
        )
        foot.append(
            f"needs answered {answered} of {len(needs_status)}"
            + (f" ({partial} partly)" if partial else "")
        )
        # Parity with report.ts coverageFooter(): the gap-closing pass states
        # what it cost and what it bought, including when it bought nothing.
        gp = result.get("gap_pass")
        if isinstance(gp, dict):
            foot.append(
                f"gap-closing pass: +{gp.get('added', 0)} sources, needs answered "
                f"{gp.get('answeredBefore', 0)} of {gp.get('total', 0)} -> "
                f"{gp.get('answeredAfter', 0)} of {gp.get('total', 0)}"
            )
        rec = result.get("search_record")
        if isinstance(rec, dict) and isinstance(rec.get("fetched"), int):
            # PARITY with report.ts coverageFooter()/searchHealthLabel(). Both
            # renderers must say the same thing in the same words: this file is
            # re-pasted into Open WebUI by hand, so a divergence here is a
            # divergence the operator cannot see. `offtopic` was added to the
            # TypeScript side and not to this one, which left the two disagreeing
            # in both wording ("collapsed" vs "junk") and content (no DEGRADED
            # line at all) — tester, X4.
            junk = int(rec.get("collapsed") or 0) + int(rec.get("offtopic") or 0)
            hit_bits = [f"{rec.get('hits', 0)} hits"]
            if junk:
                hit_bits.append(f"{junk} junk")
            foot.append(
                f"sources {rec.get('relevant', 0)} relevant of {rec['fetched']} "
                f"fetched ({', '.join(hit_bits)})"
            )
            ok_calls = int(rec.get("ok") or 0)
            empty_calls = int(rec.get("empty") or 0)
            degraded = (junk > 0 and junk >= ok_calls) or (
                ok_calls == 0 and (junk > 0 or empty_calls > 0)
            )
            if degraded:
                foot.append(
                    f"search: DEGRADED ({junk} of {junk + ok_calls + empty_calls} "
                    f"searches returned junk)"
                )
            # Parity with report.ts coverageFooter(): say when the entity gate
            # could not be applied. A reader who sees "search: ok" is entitled
            # to know it was decided by the weaker overlap rule.
            missing = int(rec.get("entity_missing") or 0)
            rejected = int(rec.get("entity_rejected") or 0)
            no_gate = missing + rejected
            if no_gate:
                why = (
                    f"{rejected} rejected the run's subject"
                    if rejected
                    else f"{missing} had no subject to check"
                )
                foot.append(
                    f"entity gate: {no_gate} search(es) judged without it ({why})"
                )
            # Not part of no_gate: these searches were refused BY the gate
            # rather than judged without it. Parity with report.ts.
            unfloored = int(rec.get("unfloored") or 0)
            if unfloored:
                foot.append(
                    f"entity gate refused {unfloored} search(es): "
                    f"the query had fewer than two content words"
                )
    # Parity with report.ts coverageFooter(): the rendered report was checked
    # sentence by sentence against the lines it cites, and this says how much of
    # it had to be corrected. The reader this document is written for never sees
    # a job row - a tester found a hedge turned into an absolute by READING the
    # report, and this line is what tells the next reader the machine looked.
    rf = result.get("render_fidelity")
    if isinstance(rf, dict):
        checked = int(rf.get("checked") or 0)
        if checked > 0:
            corrected = int(rf.get("rewritten") or 0) + int(rf.get("replaced") or 0)
            # N OF M, and the units nothing looked at: a coverage number with no
            # denominator is one a reader cannot reproduce from the document.
            units = int(rf.get("units") or checked)
            unchecked = int(rf.get("unchecked") or 0)
            foot.append(
                f"render checked: {checked} of {units}, {corrected} corrected, "
                f"{unchecked} unchecked"
            )
            # Parity with report.ts coverageFooter(): names the evidence never
            # used, removed before the reader saw them. Only when there were any.
            # Parity with report.ts coverageFooter(): sentences the check
            # declined to touch because a correction would have inverted them.
            held = int(rf.get("polarity_skipped") or 0)
            if held:
                by_default = int(rf.get("polarity_default") or 0)
                foot.append(f"polarity: {held} left as written ({by_default} by default)")
            blocked = rf.get("names_blocked") or []
            if isinstance(blocked, list) and blocked:
                foot.append(f"names: {len(blocked)} blocked")
        elif rf.get("error"):
            foot.append("render check: not run")
    if backstop and backstop != "complete":
        foot.append(f"stopped early: {backstop}")
    # The harness stamps this footer onto `prose`; do not print it twice.
    if foot and re.search(r"needs answered \d+ of \d+", "\n".join(parts)):
        foot = []
    if foot:
        parts.append(f"\n\n_— {' · '.join(foot)}_")

    if incomplete:
        parts.append("\n\n" + _incomplete_directive(result))

    return "\n".join(parts)
