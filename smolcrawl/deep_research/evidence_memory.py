"""Research → open-brain `sources` layer.

Replaces the old mnemory misuse. Two responsibilities:

1. CACHE LOOKUP (before a research run): if the same request was already
   answered, return the stored synthesis instead of re-running. Fresh
   hits served as-is; stale hits served flagged. The re-research choice
   is model-mediated (the returned text instructs the chat model to
   offer a refresh).

2. PERSISTENCE (after a completed run): write the synthesised finding
   plus the gathered per-source rows into open-brain's `sources` table.

open-brain returns full structured rows, so volatility / staleness are
REAL columns (`volatility`, `revalidate_days`, `researched_on`) and the
mnemory-era ⟦EV:research⟧ self-describing header, label hacks, and
artifact archiving are GONE — they only ever existed to work around
mnemory dropping labels/dates on recall. Supersede-in-place is handled
server-side by a unique partial index on `research_key`.

A deterministic `research_key` (normalised hash of the query) ties the
synthesis + its sources together and drives cache hit / supersede.
"""
from __future__ import annotations

import datetime
import hashlib
import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("deep_research.evidence")

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


def _ob(valves: Any) -> tuple[str, str]:
    base = (getattr(valves, "openbrain_url", "") or "").rstrip("/")
    key = getattr(valves, "openbrain_key", "") or ""
    return base, key


def _normalize_sources(sources: Optional[List[Dict]]) -> List[Dict]:
    """Pipeline source dicts -> {url,title,content,domain}. Dedup by url."""
    out: List[Dict] = []
    seen: set = set()
    for s in (sources or []):
        if not isinstance(s, dict):
            continue
        url = (s.get("url") or "").strip()
        content = (s.get("content") or s.get("summary") or "").strip()
        if not url and not content:
            continue
        dedup = url or content[:80]
        if dedup in seen:
            continue
        seen.add(dedup)
        out.append({
            "url": url,
            "title": (s.get("title") or s.get("domain") or url or "")[:300],
            "content": content,
            "domain": s.get("domain") or "",
        })
    return out


async def lookup_research_evidence(
    valves: Any, *, query: str, user: Optional[Dict] = None,
) -> Optional[Dict[str, Any]]:
    """Return the current open-brain synthesis row for this exact request
    (research_key match) + computed staleness, or None. Best-effort: any
    error → None (treated as cache miss)."""
    if not getattr(valves, "evidence_cache_enabled", True):
        return None
    base, key = _ob(valves)
    if not base or not key:
        return None
    rkey = compute_research_key(query)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{base}/research/lookup",
                params={"key": rkey},
                headers={"x-brain-key": key})
            if r.status_code != 200:
                return None
            data = r.json() or {}
            if not data.get("found"):
                return None
            return {
                "id": data.get("id"),
                "claim": data.get("claim") or "",
                "researched_on": data.get("researched_on"),
                "is_stale": bool(data.get("is_stale")),
                "due_date": data.get("due_date"),
                "age_days": data.get("age_days"),
                "research_key": rkey,
            }
    except Exception:
        return None


def format_cached_research(cached: Dict[str, Any], tool_name: str) -> str:
    """Model-mediated cache-hit payload: prior finding + a directive
    telling the chat model to present it and offer a refresh."""
    date = cached.get("researched_on") or "an earlier date"
    stale = bool(cached.get("is_stale"))
    due = cached.get("due_date")
    body = (cached.get("claim") or "").strip()[:1200]
    if stale:
        status = (f"⚠️ STALE — re-validation was due {due}"
                  + (f" ({cached.get('age_days')}d old)"
                     if cached.get("age_days") is not None else ""))
        guidance = (
            "Present this as PRIOR research and explicitly tell the user it "
            "is STALE (last researched " + str(date) + ") and may be out of "
            "date. Recommend re-researching. If the user agrees, call "
            f"`{tool_name}(query=<same query>, refresh=True)` — that runs a "
            "new pass and supersedes this record in place.")
    else:
        status = f"✅ FRESH — re-validate after {due}" if due else "✅ FRESH"
        guidance = (
            "Present this as previously-researched findings, noting it was "
            "researched on " + str(date) + " (still fresh). Do NOT silently "
            "re-run research. If the user wants it updated, call "
            f"`{tool_name}(query=<same query>, refresh=True)` — that runs a "
            "new pass and supersedes this record in place.")
    return (
        f"♻️ **RECALLED PRIOR RESEARCH** — this request was already "
        f"researched on {date}.\n"
        f"**Status:** {status}\n\n"
        f"{body}\n\n"
        "*(Gathered sources for this question are stored in open-brain; "
        "re-run with refresh=True to regenerate.)*\n\n"
        "---\n"
        f"**Assistant instructions (do not echo this line to the user):** "
        f"{guidance}")


async def persist_research_evidence(
    valves: Any,
    sub_agent: Any,
    *,
    query: str,
    answer: str,
    user: Optional[Dict] = None,
    request: Any = None,
    kind: str,
    sources: Optional[List[Dict]] = None,
    event_emitter=None,
) -> None:
    """Persist a completed run to open-brain `sources`. The server
    supersedes the synthesis row in place (unique research_key index) and
    replaces the per-source rows. Best-effort: never raises, never blocks
    the research result."""
    if not getattr(valves, "evidence_memory_enabled", True):
        return
    base, key = _ob(valves)
    if not base or not key:
        return
    if not answer or len(answer.strip()) < 200:
        return  # not a real synthesis (e.g. STOP/budget directive)

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
    # Phase 3.2: an active thread (if the operator set one) auto-links the
    # gathered sources to that thread; empty => unthreaded inbox.
    active_thread = (getattr(valves, "active_thread_id", "") or "").strip() or None
    payload = {
        "research_key": compute_research_key(query),
        "query": query[:400],
        "claim": claim,
        "kind": kind,
        "volatility": volatility,
        "revalidate_days": revalidate,
        "notebook": topic,
        "thread_id": active_thread,
        "model": (getattr(valves, "research_model", "")
                  or getattr(valves, "model", "") or None),
        "sources": _normalize_sources(sources),
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            r = await client.post(
                f"{base}/research/persist",
                headers={"x-brain-key": key,
                         "Content-Type": "application/json"},
                json=payload)
            if r.status_code != 200:
                snippet = " ".join((r.text or "").split())[:200]
                logger.warning("research persist failed: HTTP %s %s",
                                r.status_code, snippet)
                await _emit(f"🧠 research persist skipped (HTTP {r.status_code})")
                return
            data = r.json() or {}
            where = (f"thread {data.get('thread_id')}" if data.get("threaded")
                     else "inbox (no active thread)")
            await _emit(
                f"🧠 research saved to open-brain (vol:{volatility}, "
                f"revalidate {revalidate}d, "
                f"{data.get('sources_written', 0)} sources -> {where})")
    except Exception as exc:
        await _emit(f"🧠 research persist error: {type(exc).__name__}")
