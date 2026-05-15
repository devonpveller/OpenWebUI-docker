"""Research → mnemory evidence persistence.

Called ONCE at the completion of a research run (never per iteration).
Writes the synthesised finding as a self-describing "evidence" memory:
the content is prefixed with a machine/LLM-readable provenance header so
that downstream consumers (OWUI filter, cloud gateway, any LLM) can judge
credibility and staleness WITHOUT needing mnemory to return metadata
(mnemory's recall/search responses drop labels/categories — the header is
the only channel that always survives).

Header format (one line, prepended to the stored statement):

    ⟦EV:research | date:YYYY-MM-DD | vol:<tier> | revalidate:<N>d |
     src:<count> | run:<kind>⟧

Epistemic contract (taught to the LLM elsewhere): a fresh EV:research
memory may be stated as fact with sources; once age exceeds the
revalidate window it must be downgraded to an "educated guess (re-
validation due)". Volatility tier is classified by the model from the
finding itself.
"""
from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, Optional

import httpx

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")

_DEFAULT_VOL_DAYS = {"fast": 7, "medium": 180, "slow": 1095}

_CLASSIFY_SYS = (
    "You compress a completed research answer into one durable evidence "
    "record. Return STRICT JSON only:\n"
    '{"claim": "<=600 char standalone factual summary of the verified '
    'finding, no hedging, no markdown>", "volatility": "fast|medium|slow", '
    '"topic": "<2-4 word topic slug>"}\n'
    "volatility = how fast this knowledge goes stale:\n"
    "- fast: news, prices, releases, current status, people's current "
    "roles, anything time-sensitive\n"
    "- medium: tools/APIs/libraries, best practices, organisations, "
    "evolving technical guidance\n"
    "- slow: science, mathematics, peer-reviewed results, history, "
    "established definitions"
)


def _vol_map(spec: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for part in (spec or "").split(","):
        if ":" in part:
            k, _, v = part.partition(":")
            try:
                out[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return out or dict(_DEFAULT_VOL_DAYS)


async def persist_research_evidence(
    valves: Any,
    sub_agent: Any,
    *,
    query: str,
    answer: str,
    user: Optional[Dict],
    request: Any,
    kind: str,
    event_emitter=None,
) -> None:
    """Best-effort: never raises, never blocks the research result."""
    if not getattr(valves, "evidence_memory_enabled", True):
        return
    api_key = getattr(valves, "mnemory_api_key", "") or ""
    base = (getattr(valves, "mnemory_url", "") or "").rstrip("/")
    if not base or not api_key:
        return
    if not answer or len(answer.strip()) < 200:
        return  # not a real synthesis (e.g. STOP/budget directive)

    user = user or {}
    user_id = (user.get("email") or user.get("id")
               or getattr(valves, "mnemory_user_id", "") or "")
    if not user_id:
        return

    async def _emit(msg: str) -> None:
        if event_emitter:
            try:
                await event_emitter({"type": "status",
                                     "data": {"description": msg,
                                              "done": False}})
            except Exception:
                pass

    # 1. Compress + classify volatility (one cheap LLM call).
    claim, volatility, topic = answer.strip()[:600], "medium", "research"
    try:
        parsed = await sub_agent.run_json(
            _CLASSIFY_SYS,
            f"RESEARCH QUESTION:\n{query}\n\nANSWER:\n{answer[:6000]}",
            request, user,
        )
        if isinstance(parsed, dict):
            claim = (parsed.get("claim") or claim).strip()[:600]
            v = str(parsed.get("volatility", "")).lower().strip()
            if v in ("fast", "medium", "slow"):
                volatility = v
            topic = (parsed.get("topic") or topic).strip()[:40] or "research"
    except Exception:
        pass

    vol_days = _vol_map(getattr(valves, "evidence_volatility_days", ""))
    revalidate = vol_days.get(volatility, _DEFAULT_VOL_DAYS.get(volatility, 180))
    today = datetime.date.today().isoformat()
    src_urls = list(dict.fromkeys(_URL_RE.findall(answer or "")))
    q_short = re.sub(r"\s+", " ", query).strip()[:80]

    header = (
        f"⟦EV:research | date:{today} | vol:{volatility} | "
        f"revalidate:{revalidate}d | src:{len(src_urls)} | run:{kind} | "
        f'q:"{q_short}"⟧'
    )
    content = f"{header}\n{claim}"

    payload = {
        "content": content,
        "memory_type": "fact",
        "categories": ["technical", f"research:{topic}"],
        "importance": "high",
        "infer": False,  # store the verified statement + header verbatim
        "role": "user",
        "event_date": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "labels": {
            "evidence": "research",
            "volatility": volatility,
            "revalidate_days": str(revalidate),
            "share": "cloud",          # research is evidence, not personal
            "source": "deep-research",
            "run_kind": kind,
            "researched_on": today,
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-User-Id": user_id,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{base}/api/memories",
                                   headers=headers, json=payload)
            if r.status_code != 200:
                await _emit(f"🧠 evidence memory skipped ({r.status_code})")
                return
            data = r.json()
            mem_id = None
            res = data.get("results") if isinstance(data, dict) else None
            if isinstance(res, list) and res:
                mem_id = res[0].get("id")
            mem_id = mem_id or (data.get("id") if isinstance(data, dict)
                                else None)

            # 2. Attach the full report + sources as a slow-memory artifact.
            if mem_id:
                artifact = (
                    f"# Research evidence — {q_short}\n\n"
                    f"Researched: {today} | volatility: {volatility} | "
                    f"revalidate after: {revalidate}d | run: {kind}\n\n"
                    f"{answer}\n"
                )
                try:
                    await client.post(
                        f"{base}/api/memories/{mem_id}/artifacts",
                        headers=headers,
                        json={"content": artifact,
                              "content_type": "text/markdown",
                              "filename": f"research-{today}.md"},
                    )
                except Exception:
                    pass
            await _emit(
                f"🧠 saved as research evidence (vol:{volatility}, "
                f"revalidate {revalidate}d)")
    except Exception as exc:
        await _emit(f"🧠 evidence memory error: {type(exc).__name__}")
