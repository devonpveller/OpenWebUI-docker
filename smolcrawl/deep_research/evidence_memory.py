"""Research → mnemory evidence layer.

Two responsibilities:

1. CACHE LOOKUP (before a research run): if the same research request was
   already answered, return the stored evidence + full report instead of
   re-running the pipeline. Fresh hits are served as-is; stale hits are
   served flagged. The choice to re-research is model-mediated (the
   returned text instructs the chat model to offer the user a refresh).

2. PERSISTENCE (after a completed run): store the synthesised finding as
   a self-describing "evidence" memory. The content is prefixed with a
   machine/LLM-readable provenance header because mnemory's recall/search
   responses drop labels/categories — the header is the only channel that
   always survives to downstream consumers.

       ⟦EV:research | date:YYYY-MM-DD | vol:<tier> | revalidate:<N>d |
        src:<count> | run:<kind> | q:"…"⟧

Same-request matching + supersede use a deterministic `research_key`
label (normalised hash of the query), so a refresh updates the existing
memory in place instead of creating duplicates.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import re
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("deep_research.evidence")

_URL_RE = re.compile(r"https?://[^\s\)\]\}>\"']+")
_DEFAULT_VOL_DAYS = {"fast": 7, "medium": 180, "slow": 1095}

# Tokens dropped when deriving the research_key so trivial phrasing
# differences ("research X", "use research on X", "X?") collapse together.
_KEY_STOP = {
    "the", "a", "an", "of", "to", "for", "is", "are", "was", "were", "what",
    "how", "why", "when", "who", "which", "vs", "versus", "and", "or", "in",
    "on", "at", "about", "please", "research", "use", "find", "tell", "me",
    "do", "does", "can", "you", "give", "info", "information", "into",
}

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


def compute_research_key(query: str) -> str:
    """Deterministic, order/phrasing-insensitive key for a research query."""
    text = re.sub(r"[^a-z0-9\s]", " ", (query or "").lower())
    toks = sorted(t for t in text.split() if t and t not in _KEY_STOP)
    if not toks:
        toks = sorted(re.sub(r"[^a-z0-9\s]", " ",
                             (query or "").lower()).split())
    return hashlib.sha1(" ".join(toks).encode()).hexdigest()[:20]


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


def parse_ev_header(content: str) -> Dict[str, Any]:
    """Extract date + revalidate window from the ⟦EV:research …⟧ header
    and compute staleness against today. Returns {} if no header."""
    if not content:
        return {}
    m = re.match(r"⟦EV:research\b([^⟧]*)⟧", content.strip())
    if not m:
        return {}
    body = m.group(1)
    info: Dict[str, Any] = {}
    d = re.search(r"date:(\d{4}-\d{2}-\d{2})", body)
    rv = re.search(r"revalidate:(\d+)d", body)
    info["date"] = d.group(1) if d else None
    info["revalidate_days"] = int(rv.group(1)) if rv else None
    info["is_stale"] = False
    info["due_date"] = None
    info["age_days"] = None
    if info["date"] and info["revalidate_days"] is not None:
        try:
            researched = datetime.date.fromisoformat(info["date"])
            today = datetime.date.today()
            age = (today - researched).days
            due = researched + datetime.timedelta(
                days=info["revalidate_days"])
            info["age_days"] = age
            info["due_date"] = due.isoformat()
            info["is_stale"] = today > due
        except ValueError:
            pass
    return info


def _mn_headers(valves: Any, user_id: str) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {getattr(valves, 'mnemory_api_key', '')}",
        "Content-Type": "application/json",
        "X-User-Id": user_id,
    }


def _resolve_user_id(valves: Any, user: Optional[Dict]) -> str:
    user = user or {}
    return (user.get("email") or user.get("id")
            or getattr(valves, "mnemory_user_id", "") or "")


async def lookup_research_evidence(
    valves: Any, *, query: str, user: Optional[Dict],
) -> Optional[Dict[str, Any]]:
    """Return the existing evidence memory for this exact research request
    (matched by research_key), with its full-report artifact, or None.
    Best-effort: any error → None (treated as cache miss)."""
    if not getattr(valves, "evidence_cache_enabled", True):
        return None
    base = (getattr(valves, "mnemory_url", "") or "").rstrip("/")
    api_key = getattr(valves, "mnemory_api_key", "") or ""
    user_id = _resolve_user_id(valves, user)
    if not base or not api_key or not user_id:
        return None
    key = compute_research_key(query)
    headers = _mn_headers(valves, user_id)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                f"{base}/api/memories/search", headers=headers,
                json={"query": query[:400], "limit": 3,
                      "labels": {"evidence": "research",
                                 "research_key": key}})
            if r.status_code != 200:
                return None
            results = (r.json() or {}).get("results") or []
            if not results:
                return None
            mem = results[0]
            mem_id = mem.get("id")
            content = mem.get("memory") or mem.get("content") or ""
            hdr = parse_ev_header(content)
            claim = content.split("⟧", 1)[-1].strip() or content
            # Deliberately do NOT fetch the full-report artifact: a cache
            # hit must stay lean. Injecting the multi-KB report inline
            # would re-process on every subsequent chat turn (the bloat
            # bug). The compact claim is the answer; the full report
            # stays archived as the artifact and refresh=True regenerates
            # it.
            return {"id": mem_id, "claim": claim,
                    "header": hdr, "research_key": key}
    except Exception:
        return None


def format_cached_research(cached: Dict[str, Any], tool_name: str) -> str:
    """Model-mediated cache-hit payload: the prior finding + a directive
    telling the chat model to present it and offer a refresh."""
    hdr = cached.get("header") or {}
    date = hdr.get("date") or "an earlier date"
    stale = bool(hdr.get("is_stale"))
    due = hdr.get("due_date")
    # Lean: the compact claim only — never the full report (bloat fix).
    body = (cached.get("claim") or "").strip()[:1200]
    archived = ("\n\n*(Full prior research report is archived as a "
                "mnemory artifact on this evidence memory; re-run with "
                "refresh=True to regenerate it.)*")
    if stale:
        status = (f"⚠️ STALE — re-validation was due {due}"
                  + (f" ({hdr.get('age_days')}d old)"
                     if hdr.get("age_days") is not None else ""))
        guidance = (
            "Present this as PRIOR research and explicitly tell the user it "
            "is STALE (last researched " + str(date) + ") and may be "
            "out of date. Recommend re-researching. If the user agrees / "
            "asks for fresh research, call "
            f"`{tool_name}(query=<same query>, refresh=True)` — that runs a "
            "new pass and supersedes this memory in place.")
    else:
        status = f"✅ FRESH — re-validate after {due}" if due else "✅ FRESH"
        guidance = (
            "Present this as previously-researched findings, noting it was "
            "researched on " + str(date) + " (still fresh). Do NOT silently "
            "re-run research. If the user wants it updated/re-researched, "
            f"call `{tool_name}(query=<same query>, refresh=True)` — that "
            "runs a new pass and supersedes this memory in place.")
    return (
        f"♻️ **RECALLED PRIOR RESEARCH** — this request was already "
        f"researched on {date}.\n"
        f"**Status:** {status}\n\n"
        f"{body}{archived}\n\n"
        "---\n"
        f"**Assistant instructions (do not echo this line to the user):** "
        f"{guidance}")


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
    """Persist a completed run. If an evidence memory with the same
    research_key exists, SUPERSEDE it in place; else insert. Best-effort:
    never raises, never blocks the research result."""
    if not getattr(valves, "evidence_memory_enabled", True):
        return
    api_key = getattr(valves, "mnemory_api_key", "") or ""
    base = (getattr(valves, "mnemory_url", "") or "").rstrip("/")
    if not base or not api_key:
        return
    if not answer or len(answer.strip()) < 200:
        return  # not a real synthesis (e.g. STOP/budget directive)
    user_id = _resolve_user_id(valves, user)
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

    claim, volatility, topic = answer.strip()[:600], "medium", "research"
    try:
        parsed = await sub_agent.run_json(
            _CLASSIFY_SYS,
            f"RESEARCH QUESTION:\n{query}\n\nANSWER:\n{answer[:6000]}",
            request, user or {})
        if isinstance(parsed, dict):
            claim = (parsed.get("claim") or claim).strip()[:600]
            v = str(parsed.get("volatility", "")).lower().strip()
            if v in ("fast", "medium", "slow"):
                volatility = v
            topic = (parsed.get("topic") or topic).strip()[:40] or "research"
    except Exception:
        pass

    vol_days = _vol_map(getattr(valves, "evidence_volatility_days", ""))
    revalidate = vol_days.get(volatility,
                              _DEFAULT_VOL_DAYS.get(volatility, 180))
    today = datetime.date.today().isoformat()
    key = compute_research_key(query)
    src_urls = list(dict.fromkeys(_URL_RE.findall(answer or "")))
    q_short = re.sub(r"\s+", " ", query).strip()[:80]

    header = (
        f"⟦EV:research | date:{today} | vol:{volatility} | "
        f"revalidate:{revalidate}d | src:{len(src_urls)} | run:{kind} | "
        f'q:"{q_short}"⟧'
    )
    body = {
        "content": f"{header}\n{claim}",
        "memory_type": "fact",
        "categories": ["technical", f"research:{topic}"],
        "importance": "high",
        "infer": False,
        "role": "user",
        "event_date": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "labels": {
            "evidence": "research",
            "research_key": key,
            "volatility": volatility,
            "revalidate_days": str(revalidate),
            "share": "cloud",
            "source": "deep-research",
            "run_kind": kind,
            "researched_on": today,
        },
    }
    headers = _mn_headers(valves, user_id)
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # Supersede in place if a prior memory with this key exists.
            existing_id = None
            try:
                s = await client.post(
                    f"{base}/api/memories/search", headers=headers,
                    json={"query": query[:400], "limit": 1,
                          "labels": {"evidence": "research",
                                     "research_key": key}})
                if s.status_code == 200:
                    res = (s.json() or {}).get("results") or []
                    if res:
                        existing_id = res[0].get("id")
            except Exception:
                pass

            fail_resp = None
            if existing_id:
                up = await client.put(
                    f"{base}/api/memories/{existing_id}",
                    headers=headers, json=body)
                ok = up.status_code == 200
                if not ok:
                    fail_resp = up
                mem_id = existing_id if ok else None
                verb = "superseded"
                # Drop old artifacts so the report doesn't accrete.
                if ok:
                    try:
                        la = await client.get(
                            f"{base}/api/memories/{existing_id}/artifacts",
                            headers=headers)
                        if la.status_code == 200:
                            arts = la.json() or {}
                            items = (arts.get("artifacts")
                                     or arts.get("results") or [])
                            for it in items:
                                aid = it.get("artifact_id") or it.get("id")
                                if aid:
                                    await client.delete(
                                        f"{base}/api/memories/"
                                        f"{existing_id}/artifacts/{aid}",
                                        headers=headers)
                    except Exception:
                        pass
            else:
                cr = await client.post(f"{base}/api/memories",
                                       headers=headers, json=body)
                ok = cr.status_code == 200
                if not ok:
                    fail_resp = cr
                verb = "saved"
                mem_id = None
                if ok:
                    data = cr.json()
                    res = (data.get("results")
                           if isinstance(data, dict) else None)
                    if isinstance(res, list) and res:
                        mem_id = res[0].get("id")
                    mem_id = mem_id or (data.get("id")
                                        if isinstance(data, dict) else None)

            if not ok:
                detail = ""
                if fail_resp is not None:
                    snippet = " ".join(
                        (fail_resp.text or "").split())[:300]
                    detail = (f" — HTTP {fail_resp.status_code} "
                              f"at {fail_resp.request.method} "
                              f"{fail_resp.request.url.path}: {snippet}")
                logger.warning(
                    "evidence memory %s failed%s", verb, detail or
                    " (no response captured)")
                await _emit(
                    f"🧠 evidence memory skipped (write failed{detail[:200]})")
                return
            if mem_id:
                artifact = (
                    f"# Research evidence — {q_short}\n\n"
                    f"Researched: {today} | volatility: {volatility} | "
                    f"revalidate after: {revalidate}d | run: {kind}\n\n"
                    f"{answer}\n")
                try:
                    await client.post(
                        f"{base}/api/memories/{mem_id}/artifacts",
                        headers=headers,
                        json={"content": artifact,
                              "content_type": "text/markdown",
                              "filename": f"research-{today}.md"})
                except Exception:
                    pass
            await _emit(
                f"🧠 research evidence {verb} (vol:{volatility}, "
                f"revalidate {revalidate}d)")
    except Exception as exc:
        await _emit(f"🧠 evidence memory error: {type(exc).__name__}")
