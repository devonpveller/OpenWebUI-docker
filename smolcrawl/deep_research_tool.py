"""
title: Deep Research
author: smolcrawl
date: 2026-04-12
version: 1.0
license: MIT
description: Iterative RAG research with LLM-guided domain discovery, web search exploration, and chain-of-thought synthesis. Provides research() for quick exploration and deep_research() for full knowledge building.
requirements: httpx, pydantic
"""

# =============================================================================
#  Deep Research Function for Open WebUI
#
#  Self-contained single-file deployment. Source modules live in
#  integrations/open-webui/deep_research/ for development.
#
#  Install: OWUI Workspace → Tools → (+) → Paste this file
# =============================================================================

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, Field

# Internal OWUI imports — available when Tool runs inside OWUI process.
# Guarded for Pipeline deployments (separate container).
try:
    from open_webui.retrieval.utils import (
        query_collection as _owui_query_collection,
        query_collection_with_hybrid_search as _owui_query_hybrid,
    )
    from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX

    _HAS_OWUI_INTERNALS = True
except ImportError:
    _HAS_OWUI_INTERNALS = False

logger = logging.getLogger("deep_research")


# =============================================================================
#  Models
# =============================================================================


class ResearchPhase(str, Enum):
    INITIALIZING = "initializing"
    DISCOVERING = "discovering"
    AWAITING_APPROVAL = "awaiting_approval"
    CRAWLING = "crawling"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class DiscoveredDomain:
    url: str
    domain: str
    score: float
    rationale: str
    already_covered: bool = False
    existing_collection_id: Optional[str] = None


@dataclass
class CrawlResult:
    domain: str
    kb_name: str
    kb_id: str = ""
    pages_crawled: int = 0
    success: bool = False
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class RetrievedChunk:
    content: str
    collection_id: str
    collection_name: str
    source: str = ""
    distance: float = 0.0

    @property
    def chunk_hash(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]


@dataclass
class IterationResult:
    iteration_number: int
    search_terms: List[str]
    collections_queried: List[str]
    chunks_found: int
    new_chunks: int
    summary: str = ""
    new_concepts: List[str] = field(default_factory=list)


@dataclass
class ResearchSession:
    session_id: str
    query: str
    session_dir: str
    phase: ResearchPhase = ResearchPhase.INITIALIZING
    discovered_domains: List[DiscoveredDomain] = field(default_factory=list)
    crawl_results: List[CrawlResult] = field(default_factory=list)
    iterations: List[IterationResult] = field(default_factory=list)
    relevant_collection_ids: List[str] = field(default_factory=list)
    seen_chunk_keys: set = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.utcnow)
    model_id: str = ""
    anchor: str = ""

    def add_seen_chunk(self, collection_id: str, chunk_hash: str) -> bool:
        key = (collection_id, chunk_hash)
        if key in self.seen_chunk_keys:
            return False
        self.seen_chunk_keys.add(key)
        return True


# =============================================================================
#  Journal (Fileshed-compatible storage)
# =============================================================================


class _Journal:
    def __init__(self, valves):
        self._v = valves

    def resolve_session_dir(self, user_id: str, slug: str, namespace: str = "deep-research") -> str:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{ts}-{slug}"
        if self._v.fileshed_compatible and user_id:
            return os.path.join(self._v.storage_base_path, "users", user_id, "Storage", "data", namespace, name)
        return os.path.join(self._v.storage_base_path, namespace, name)

    def write_entry(self, session_dir: str, filename: str, content: str) -> str:
        if not self._v.save_journal:
            return ""
        path = os.path.join(session_dir, filename)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def read_entry(self, session_dir: str, filename: str) -> str:
        path = os.path.join(session_dir, filename)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    def write_prompt(self, session: ResearchSession, model_id: str) -> None:
        self.write_entry(session.session_dir, "00-prompt.md",
            f"# Research Session\n\n**Query:** {session.query}\n"
            f"**Timestamp:** {session.created_at.isoformat()}\n"
            f"**Model:** {model_id}\n**Session ID:** {session.session_id}\n")

    def write_anchor(self, session: ResearchSession) -> None:
        self.write_entry(session.session_dir, "00-anchor.md",
            f"# Research Anchor\n\n```\n{session.anchor}\n```\n\n"
            f"This anchor was extracted at session start and is threaded through "
            f"every search, analysis, and synthesis prompt to prevent drift.\n")

    def write_domains(self, session: ResearchSession, existing: List[Dict]) -> None:
        lines = ["# Domain Discovery\n"]
        if existing:
            lines.append("## Existing Collections\n")
            for c in existing:
                fc = len(c.get("data", {}).get("file_ids", []))
                lines.append(f"- **{c['name']}** ({fc} files): {c.get('description', 'No description')}\n")
            lines.append("")
        lines.append("## Discovered Domains\n")
        for i, d in enumerate(session.discovered_domains, 1):
            st = "✅ Already covered" if d.already_covered else "🆕 New"
            lines.append(f"{i}. [{d.score:.2f}] **{d.domain}**\n   {d.rationale}\n   Status: {st}\n")
        self.write_entry(session.session_dir, "01-domains.md", "\n".join(lines))

    def write_crawl_status(self, session: ResearchSession) -> None:
        lines = ["# Crawl Status\n"]
        for r in session.crawl_results:
            s = "✅" if r.success else "❌"
            lines.append(f"## {s} {r.domain}\n\n- **KB Name:** {r.kb_name}\n- **Pages:** {r.pages_crawled}\n- **Duration:** {r.duration_seconds:.1f}s\n")
            if r.error:
                lines.append(f"- **Error:** {r.error}\n")
        self.write_entry(session.session_dir, "02-crawl-status.md", "\n".join(lines))

    def write_iteration(self, session: ResearchSession, it: IterationResult) -> None:
        fn = f"{it.iteration_number + 2:02d}-iteration-{it.iteration_number}.md"
        lines = [f"# Iteration {it.iteration_number}\n", "## Search Terms\n",
                 ", ".join(f"`{t}`" for t in it.search_terms) + "\n",
                 "\n## Collections Queried\n", ", ".join(it.collections_queried) + "\n",
                 f"\n## Results\n- Chunks found: {it.chunks_found}\n- New: {it.new_chunks}\n",
                 f"\n## Summary\n\n{it.summary}\n"]
        if it.new_concepts:
            lines.append("\n## New Concepts\n\n")
            lines.extend(f"- {c}\n" for c in it.new_concepts)
        self.write_entry(session.session_dir, fn, "\n".join(lines))

    def write_synthesis(self, session: ResearchSession, content: str) -> None:
        fn = f"{len(session.iterations) + 3:02d}-synthesis.md"
        self.write_entry(session.session_dir, fn, f"# Synthesis\n\n{content}\n")

    def write_manifest(self, session: ResearchSession) -> None:
        m = {"session_id": session.session_id, "query": session.query,
             "created_at": session.created_at.isoformat(), "phase": session.phase.value,
             "model_id": session.model_id,
             "domains": [{"domain": d.domain, "url": d.url, "score": d.score} for d in session.discovered_domains],
             "crawls": [{"domain": c.domain, "kb_name": c.kb_name, "pages_crawled": c.pages_crawled, "success": c.success} for c in session.crawl_results],
             "iterations": [{"number": i.iteration_number, "terms": i.search_terms, "chunk_count": i.chunks_found} for i in session.iterations],
             "seen_chunks": len(session.seen_chunk_keys)}
        self.write_entry(session.session_dir, "manifest.json", json.dumps(m, indent=2))

    @staticmethod
    def slugify(text: str, max_length: int = 40) -> str:
        slug = re.sub(r"[^\w\s-]", "", text.lower())
        slug = re.sub(r"[\s_]+", "-", slug).strip("-")
        return slug[:max_length]


# =============================================================================
#  Sub-Agent (internal LLM calls)
# =============================================================================


_ANCHOR_PROMPT = """\
Extract a structured research anchor from the user's query.
This anchor will guide all subsequent search, analysis, and synthesis steps.

Return JSON:
{"key_concepts": ["specific concepts/terms the user mentioned"],
 "intent": "one sentence: what the user wants to learn or accomplish",
 "scope_in": ["topics that ARE in scope"],
 "scope_out": ["adjacent topics that are NOT being asked about"],
 "must_cover": ["terms/phrases from the query that MUST appear in results"],
 "initial_search_terms": ["3-5 diverse web search queries designed to find \
authoritative sources. Include: (1) the raw query, (2) an official-docs query \
like 'X official documentation' or 'X getting started', (3) a technical \
definition query like 'what is X framework'. Optimize for search engines, \
not conversational phrasing."]}

Be precise — use the user's exact words for key_concepts and must_cover. \
For initial_search_terms, rewrite the query into effective web search phrases.\
"""


async def _extract_anchor(sa: '_SubAgent', query: str, request, user: Dict) -> tuple:
    """Run one LLM call to distil the query into a reusable anchor block.

    Returns:
        Tuple of (anchor_string, initial_search_terms).
    """
    try:
        r = await sa.run_json(_ANCHOR_PROMPT, query, request, user)
    except Exception:
        return (
            f"RESEARCH ANCHOR\nQuery: {query}\n"
            f"Key concepts: (extraction failed — use query as-is)",
            [query],
        )
    lines = ["RESEARCH ANCHOR", f"Query: {query}"]
    if r.get("key_concepts"):
        lines.append(f"Key concepts: {', '.join(r['key_concepts'])}")
    if r.get("intent"):
        lines.append(f"Intent: {r['intent']}")
    if r.get("must_cover"):
        lines.append(f"Must cover: {', '.join(r['must_cover'])}")
    if r.get("scope_in"):
        lines.append(f"In scope: {', '.join(r['scope_in'])}")
    if r.get("scope_out"):
        lines.append(f"Out of scope: {', '.join(r['scope_out'])}")

    search_terms = r.get("initial_search_terms", [])
    if not search_terms:
        search_terms = [query]
    if query not in search_terms:
        search_terms.insert(0, query)

    return "\n".join(lines), search_terms


# _WEB_SEARCH_LIST_PROMPT removed — we now call search_web() directly


# --- Context budget management ---
# Prevents context window overflow by condensing old iteration history
# into compact summaries and capping source lists by authority.
# Full details are always persisted to the journal.

_CB_CHARS_PER_TOKEN = 4
_CB_RESPONSE_RESERVE = 4000


def _cb_usable_budget_chars(max_prompt_tokens: int) -> int:
    """Character budget after reserving space for model response."""
    return max(max_prompt_tokens - _CB_RESPONSE_RESERVE, 2000) * _CB_CHARS_PER_TOKEN


def _cb_condense_iterations(iterations: list, recent_full: int = 2) -> str:
    """Compress iteration history — recent in full, older condensed."""
    if not iterations:
        return "No iterations completed yet."
    parts = []
    cutoff = max(0, len(iterations) - recent_full)
    if cutoff > 0:
        total_new = sum(it.new_chunks for it in iterations[:cutoff])
        total_found = sum(it.chunks_found for it in iterations[:cutoff])
        all_concepts = []
        for it in iterations[:cutoff]:
            all_concepts.extend(it.new_concepts[:3])
        unique_concepts = list(dict.fromkeys(all_concepts))[:10]
        parts.append(f"**Prior iterations (1\u2013{cutoff}):** "
                     f"{total_found} chunks retrieved, {total_new} new")
        if unique_concepts:
            parts.append(f"  Concepts: {', '.join(unique_concepts)}")
        last_old = iterations[cutoff - 1]
        if last_old.summary:
            parts.append(f"  Last finding: {last_old.summary[:200]}")
        parts.append("")
    for it in iterations[cutoff:]:
        parts.append(
            f"**Iteration {it.iteration_number}** "
            f"(terms: {', '.join(it.search_terms)}): {it.summary}\n"
            f"New chunks: {it.new_chunks}, "
            f"Concepts: {', '.join(it.new_concepts)}"
        )
    return "\n\n".join(parts)


def _cb_cap_sources(sources: List[Dict], budget_chars: int) -> tuple:
    """Select highest-authority sources within budget. Returns (selected, omitted_count)."""
    sorted_src = sorted(sources, key=lambda s: s.get("authority", 0.5), reverse=True)
    selected, used = [], 0
    for s in sorted_src:
        entry_len = (len(s.get("title", "")) + len(s.get("url", ""))
                     + len(s.get("domain", "")) + len(s.get("summary", "")) + 80)
        if used + entry_len > budget_chars and selected:
            break
        selected.append(s)
        used += entry_len
    return selected, len(sources) - len(selected)


def _cb_build_iteration_text(summaries: List[str], budget_chars: int) -> str:
    """Build iteration section for synthesis — most recent first, capped."""
    if not summaries:
        return ""
    parts, used = [], 0
    for i in range(len(summaries) - 1, -1, -1):
        entry = f"# Iteration {i + 1} Findings\n\n{summaries[i]}\n"
        if used + len(entry) > budget_chars and parts:
            parts.append(f"*[{i + 1} earlier iteration(s) available in journal]*\n")
            break
        parts.append(entry)
        used += len(entry)
    parts.reverse()
    return "\n\n".join(parts)


class _SubAgent:
    def __init__(self, model_id: str, max_prompt_tokens: int = 6000,
                 nothink_suffix: str = ""):
        self._model_id = model_id
        self._max_prompt_chars = max_prompt_tokens * 4
        # When set (e.g. ":nothink"), mechanical JSON calls route to the
        # reasoning-disabled alias of the SAME model — llama-swap does not
        # reload, it just skips thinking-token generation.
        self._nothink_suffix = nothink_suffix or ""

    async def run(self, system_prompt: str, user_prompt: str, request, user: Dict,
                  json_mode: bool = False, nothink: bool = False) -> str:
        from open_webui.utils.chat import generate_chat_completion
        from open_webui.models.users import UserModel

        if json_mode:
            sys_msg = ("You are a JSON data extraction API. "
                       "Respond with ONLY valid JSON. "
                       "No explanations, no markdown fences, no commentary.")
        else:
            sys_msg = "Follow the user's instructions precisely."

        combined = (
            f"INSTRUCTIONS (follow these exactly):\n{system_prompt}\n\n"
            f"---\nINPUT:\n{user_prompt}"
        )

        # Truncate if prompt exceeds budget
        total_chars = len(sys_msg) + len(combined)
        if total_chars > self._max_prompt_chars:
            budget = self._max_prompt_chars - len(sys_msg) - 100
            if budget > len(system_prompt) + 200:
                combined = combined[:budget] + (
                    "\n\n[... content truncated to fit context window ...]"
                )
            else:
                combined = combined[:max(budget, 500)] + (
                    "\n\n[... content truncated to fit context window ...]"
                )
            logger.warning(
                "Truncated prompt: %d\u2192%d chars (~%d\u2192%d tokens, budget %d tokens). "
                "Increase max_prompt_tokens or reduce research scope.",
                total_chars, len(sys_msg) + len(combined),
                total_chars // 4, (len(sys_msg) + len(combined)) // 4,
                self._max_prompt_chars // 4,
            )

        use_model = self._model_id
        if (nothink and self._nothink_suffix
                and not self._model_id.endswith(self._nothink_suffix)):
            use_model = f"{self._model_id}{self._nothink_suffix}"

        def _form(model_id: str) -> Dict:
            return {
                "model": model_id,
                "messages": [
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": combined},
                ],
                "stream": False,
                "metadata": {"task": "deep_research_sub_agent"},
            }

        try:
            response = await generate_chat_completion(
                request=request, form_data=_form(use_model),
                user=UserModel(**user), bypass_filter=True,
            )
        except Exception as e:
            if use_model == self._model_id:
                raise
            logger.warning(
                "nothink alias '%s' failed (%s) — retrying with base model",
                use_model, e,
            )
            response = await generate_chat_completion(
                request=request, form_data=_form(self._model_id),
                user=UserModel(**user), bypass_filter=True,
            )
        return response["choices"][0]["message"]["content"]

    async def run_json(self, system_prompt: str, user_prompt: str, request, user: Dict) -> Any:
        """Call LLM and parse response as JSON."""
        raw = await self.run(system_prompt, user_prompt, request, user,
                             json_mode=True, nothink=True)
        try:
            return _parse_json(raw)
        except ValueError:
            logger.warning("JSON parse failed. Raw response (first 500 chars): %s",
                           raw[:500] if raw else "<empty>")
            raise

    @staticmethod
    def resolve_model_id(metadata: Optional[Dict], model: Optional[Dict]) -> str:
        return (((metadata or {}).get("model") or {}).get("id", "")
                or (model or {}).get("id", ""))


def _parse_json(text: str) -> Any:
    text = text.strip()
    # Attempt 1: Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Attempt 2: Markdown code fence
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass
    # Attempt 3: Find first JSON array or object in the text
    for pattern in [r'(\[\s*\{.*\}\s*\])', r'(\{.*\})']:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Could not parse JSON from LLM response: {text[:300]}...")


# =============================================================================
#  Domain Discovery
# =============================================================================

_DISCOVERY_PROMPT = """\
You are a research librarian. Given a research topic, search the web to \
identify the most authoritative documentation sources.

IMPORTANT: Cover ALL specific concepts mentioned in the query. If the \
user mentions a specific term, technique, or proper noun, find sources \
that address it directly — do not substitute broader topics.

Return a JSON array of objects, each with:
- "url": full URL of the documentation root
- "domain": the domain name
- "score": relevance 0.0-1.0
- "rationale": one-sentence explanation of how this addresses the query

Focus on: official docs, API references, tutorials, community wikis.
Exclude: social media, forums, video-only, paywalled sites.
Return at most {max_domains} domains, ordered by score descending.
Respond ONLY with valid JSON.\
"""

_RANKING_PROMPT = """\
Given a list of knowledge collections and a research query, return a JSON \
array of collection IDs that are relevant. Respond ONLY with a JSON array.\
"""


class _Discovery:
    def __init__(self, valves, sub_agent: _SubAgent):
        self._v = valves
        self._sa = sub_agent

    async def discover_domains(self, query: str, request, user: Dict) -> List[DiscoveredDomain]:
        """Discover domains via direct web search + LLM ranking."""
        from open_webui.routers.retrieval import search_web
        from starlette.concurrency import run_in_threadpool

        engine = getattr(request.app.state.config, "WEB_SEARCH_ENGINE", "")
        if not engine:
            logger.warning("No WEB_SEARCH_ENGINE configured — skipping domain discovery")
            return []

        try:
            results = await run_in_threadpool(
                search_web, request, engine,
                f"authoritative documentation for {query}")
        except Exception as e:
            logger.error("Domain discovery search failed: %s", e)
            return []

        if not results:
            return []

        # Format search results for LLM ranking
        listing = "\n".join(
            f"- {r.link} | {r.title or '(no title)'} | {r.snippet or '(no snippet)'}"
            for r in results[:20])

        try:
            data = await self._sa.run_json(
                _DISCOVERY_PROMPT.format(max_domains=self._v.max_domains),
                f"Research query: {query}\n\nWeb search results:\n{listing}",
                request, user)
        except Exception as e:
            logger.error("Domain discovery LLM ranking failed: %s", e)
            # Fall back to raw search results as domains
            data = []
            seen = set()
            for r in results[:self._v.max_domains]:
                try:
                    domain = urlparse(r.link).netloc
                except Exception:
                    continue
                if domain not in seen:
                    seen.add(domain)
                    data.append({
                        "url": r.link, "domain": domain,
                        "score": 0.5, "rationale": r.snippet or ""})
        return self._parse(data)

    async def rank_collections(self, query: str, collections: List[Dict], request, user: Dict) -> List[str]:
        if not collections:
            return []
        summaries = "\n".join(
            f"- ID: {c['id']} | Name: {c['name']} | Desc: {c.get('description', 'None')} | Files: {len(c.get('data', {}).get('file_ids', []))}"
            for c in collections)
        try:
            result = await self._sa.run_json(_RANKING_PROMPT,
                f"Query: {query}\n\nAvailable collections:\n{summaries}", request, user)
        except Exception as e:
            logger.error("Collection ranking failed: %s", e)
            return []
        valid = {c["id"] for c in collections}
        return [r for r in (result if isinstance(result, list) else []) if r in valid]

    def check_coverage(self, domains: List[DiscoveredDomain], collections: List[Dict]) -> List[DiscoveredDomain]:
        hints = set()
        for c in collections:
            for t in (c.get("name", "").lower(), c.get("description", "").lower()):
                hints.add(t)
                for w in t.split():
                    if "." in w and len(w) > 4:
                        hints.add(w)
        for d in domains:
            dl = d.domain.lower()
            for h in hints:
                if dl in h or h in dl:
                    d.already_covered = True
                    for c in collections:
                        if dl in c.get("name", "").lower():
                            d.existing_collection_id = c["id"]
                            break
                    break
        return domains

    @staticmethod
    def format_approval(domains: List[DiscoveredDomain], existing: List[Dict]) -> str:
        lines = []
        if existing:
            lines.append(f"📚 Found **{len(existing)}** existing relevant collection(s).\n")
        lines.append(f"🌐 Discovered **{len(domains)}** relevant domain(s):\n")
        for i, d in enumerate(domains, 1):
            cov = " *(already in KB)*" if d.already_covered else ""
            lines.append(f" {i}. **[{d.score:.2f}] {d.domain}**{cov}\n    {d.rationale}\n")
        lines.append('\nReply with numbers to approve (e.g., "1,2,3"), "all", or "skip".\nYou can also add domains: "1,2 + docs.example.com"')
        return "\n".join(lines)

    @staticmethod
    def parse_approval(selection: str, domains: List[DiscoveredDomain], additional: str = "") -> List[DiscoveredDomain]:
        sel = selection.strip().lower()
        approved = []
        if sel == "all":
            approved = [d for d in domains if not d.already_covered]
        elif sel != "skip":
            main = sel.split("+")[0].strip()
            for p in main.replace(" ", ",").split(","):
                p = p.strip()
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(domains):
                        approved.append(domains[idx])
        if additional:
            for ds in additional.strip().split():
                ds = ds.strip().strip(",")
                if ds and "." in ds:
                    approved.append(DiscoveredDomain(url=f"https://{ds}/", domain=ds, score=0.0, rationale="User-specified"))
        return approved

    @staticmethod
    def _parse(data) -> List[DiscoveredDomain]:
        if not isinstance(data, list):
            return []
        domains = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                url = item.get("url", "")
                domain = item.get("domain", "") or urlparse(url).netloc
                if not url and domain:
                    url = f"https://{domain}/"
                domains.append(DiscoveredDomain(url=url, domain=domain, score=float(item.get("score", 0.5)), rationale=str(item.get("rationale", ""))))
            except (TypeError, ValueError):
                continue
        domains.sort(key=lambda d: d.score, reverse=True)
        return domains


# =============================================================================
#  Crawl Integration (SmolCrawl container HTTP client)
# =============================================================================


class _CrawlClient:
    def __init__(self, valves):
        self._v = valves

    async def trigger_crawl(self, domain: str, kb_name: str, event_emitter=None) -> CrawlResult:
        start = time.monotonic()
        url = domain if domain.startswith("http") else f"https://{domain}/"
        result = CrawlResult(domain=domain, kb_name=kb_name)
        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.post(
                    f"{self._v.smolcrawl_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._v.smolcrawl_api_key}", "Content-Type": "application/json"},
                    json={"model": "smolcrawl-knowledge-builder",
                          "messages": [{"role": "user", "content": f"crawl {url} into {kb_name}"}],
                          "stream": False})
                resp.raise_for_status()
                content = resp.json().get("choices", [{}])[0].get("message", {}).get("content", "")
                result.success = True
                result.pages_crawled = self._extract_pages(content)
                if event_emitter:
                    await event_emitter({"type": "status", "data": {"description": f"✅ Crawled {domain}: {result.pages_crawled} pages", "done": False}})
        except httpx.HTTPStatusError as e:
            result.error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("Crawl failed for %s: %s", domain, result.error)
        except httpx.RequestError as e:
            result.error = f"Connection error: {e}"
            logger.error("Crawl connection failed for %s: %s", domain, e)
        except Exception as e:
            result.error = str(e)
            logger.error("Crawl error for %s: %s", domain, e)
        result.duration_seconds = time.monotonic() - start
        return result

    @staticmethod
    def _extract_pages(content: str) -> int:
        m = re.search(r"(\d+)\s*pages?\s*crawled", content, re.IGNORECASE)
        if m:
            return int(m.group(1))
        m = re.search(r"Crawled\s*\*?\*?(\d+)\*?\*?", content)
        if m:
            return int(m.group(1))
        return 0


# =============================================================================
#  RAG Research (iterative collection queries)
# =============================================================================

_EXPANSION_PROMPT = """\
You are a research assistant analyzing RAG retrieval results.

Compare the retrieved content against the ORIGINAL query. Identify:
1. What aspects of the query these results address well
2. What specific aspects of the original query remain UNCOVERED
3. New search terms that target the uncovered aspects (use the user’s terminology)
4. Adjacent concepts discovered that are still relevant to the original query

Return JSON: {"terms": ["terms targeting gaps"], "concepts": ["relevant concepts found"], "summary": "2-3 paragraph summary", "uncovered": ["aspects of original query not yet addressed"]}\
"""

_CONTINUE_PROMPT = """\
Evaluate whether another research iteration would be valuable.
Continue if: key aspects of the original query remain uncovered, OR \
promising new terms haven’t been explored yet.
Stop if: the original query’s main concepts are well-covered.

Return JSON: {"continue": true/false, "rationale": "one sentence", "uncovered": ["remaining gaps if any"]}\
"""


class _RagResearcher:
    def __init__(self, valves, sub_agent: _SubAgent):
        self._v = valves
        self._sa = sub_agent

    # ------------------------------------------------------------------
    # Internal HTTP / ASGI transport
    # ------------------------------------------------------------------

    def _build_auth_headers(self, request=None) -> Dict:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self._v.owui_api_key:
            headers["Authorization"] = f"Bearer {self._v.owui_api_key}"
        elif request:
            auth = getattr(request, "headers", {})
            if hasattr(auth, "get"):
                val = auth.get("authorization", "")
                if val:
                    headers["Authorization"] = val
        return headers

    async def _get(self, path: str, request=None) -> httpx.Response:
        headers = self._build_auth_headers(request)
        cookies = dict(request.cookies) if request and hasattr(request, "cookies") else {}
        if request and hasattr(request, "app"):
            try:
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://internal",
                                             headers=headers, cookies=cookies, timeout=30.0) as client:
                    resp = await client.get(path)
                    resp.raise_for_status()
                    return resp
            except Exception as e:
                logger.debug("ASGI GET %s failed, falling back to HTTP: %s", path, e)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(f"{self._v.owui_base_url}{path}", headers=headers)
            resp.raise_for_status()
            return resp

    async def _post(self, path: str, json_body: Dict, request=None) -> httpx.Response:
        headers = self._build_auth_headers(request)
        cookies = dict(request.cookies) if request and hasattr(request, "cookies") else {}
        if request and hasattr(request, "app"):
            try:
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://internal",
                                             headers=headers, cookies=cookies, timeout=30.0) as client:
                    resp = await client.post(path, json=json_body)
                    resp.raise_for_status()
                    return resp
            except Exception as e:
                logger.debug("ASGI POST %s failed, falling back to HTTP: %s", path, e)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(f"{self._v.owui_base_url}{path}", headers=headers, json=json_body)
            resp.raise_for_status()
            return resp

    # ------------------------------------------------------------------
    # Collection listing & querying
    # ------------------------------------------------------------------

    async def list_collections(self, request=None) -> tuple:
        try:
            resp = await self._get("/api/v1/knowledge/", request)
            data = resp.json()
            items = data.get("items", []) if isinstance(data, dict) else data
            return items, ""
        except httpx.HTTPStatusError as e:
            msg = f"OWUI API returned HTTP {e.response.status_code}"
            logger.error("Failed to list collections: %s", msg)
            return [], msg
        except httpx.ConnectError as e:
            msg = f"Cannot connect to OWUI at {self._v.owui_base_url}: {e}"
            logger.error("Failed to list collections: %s", msg)
            return [], msg
        except Exception as e:
            msg = f"Failed to list collections: {e}"
            logger.error(msg)
            return [], msg

    async def query_collection(self, col_id: str, query: str, col_name: str = "",
                               k_override: int = None, request=None,
                               file_ids: List[str] = None) -> List[RetrievedChunk]:
        effective_k = k_override if k_override is not None else self._v.top_k_per_collection

        # Build vector-store collection names (OWUI convention: file-{uuid})
        if file_ids:
            target_names = [f"file-{fid}" for fid in file_ids]
        else:
            target_names = [col_id]

        # --- Primary: OWUI internal import (Tool runs inside OWUI) ---
        if (_HAS_OWUI_INTERNALS and request
                and hasattr(request, "app")
                and hasattr(request.app.state, "EMBEDDING_FUNCTION")):
            try:
                embedding_fn = (
                    lambda query_texts, prefix: request.app.state.EMBEDDING_FUNCTION(
                        query_texts, prefix=prefix
                    )
                )
                result = await _owui_query_collection(
                    request,
                    collection_names=target_names,
                    queries=[query],
                    embedding_function=embedding_fn,
                    k=effective_k,
                )
                chunks = self._parse_retrieval(result, col_id, col_name)
                logger.debug("Internal query OK: %d chunks from %s (%d targets)",
                             len(chunks), col_name or col_id, len(target_names))
                return chunks
            except Exception as e:
                logger.warning("Internal query_collection failed for %s, "
                               "falling back to ASGI: %s", col_name or col_id, e)

        # --- Fallback: ASGI / HTTP to /api/v1/retrieval/query/collection ---
        try:
            resp = await self._post("/api/v1/retrieval/query/collection",
                {"collection_names": target_names, "query": query, "k": effective_k, "r": 0.0}, request)
            return self._parse_retrieval(resp.json(), col_id, col_name)
        except Exception as e:
            logger.debug("ASGI/HTTP query failed for %s (KB %s): %s", target_names, col_id, e)
            return []

    async def run_iteration(self, session: ResearchSession, terms: List[str],
                            col_ids: List[str], col_names: Dict[str, str],
                            iter_num: int, request, user: Dict,
                            k_override: int = None,
                            file_ids_map: Dict[str, List[str]] = None) -> IterationResult:
        all_chunks, new_chunks = [], []

        # Query each (term × collection) pair concurrently, bounded so the
        # single embedding server isn't overloaded. Results processed in
        # submission order so chunk dedup stays deterministic.
        limit = max(1, getattr(self._v, "max_parallel_queries", 5))
        sem = asyncio.Semaphore(limit)
        pairs = [(term, cid) for term in terms for cid in col_ids]

        async def _query(term, cid):
            async with sem:
                return await self.query_collection(
                    cid, term, col_names.get(cid, cid),
                    k_override=k_override, request=request,
                    file_ids=(file_ids_map or {}).get(cid))

        results = await asyncio.gather(
            *[_query(t, c) for t, c in pairs], return_exceptions=True)
        for (_, cid), chunks in zip(pairs, results):
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                all_chunks.append(chunk)
                if session.add_seen_chunk(cid, chunk.chunk_hash):
                    new_chunks.append(chunk)

        summary, concepts = "", []
        if new_chunks:
            max_ch = getattr(self._v, 'max_chunks_per_iteration', 10)
            # Cap chunk text to fit within prompt budget
            chunk_budget = _cb_usable_budget_chars(
                self._v.max_prompt_tokens
            ) - len(session.anchor) - 1000
            chunk_parts = []
            used = 0
            for c in new_chunks[:max_ch]:
                part = f"**[{c.collection_name}]** ({c.source})\n{c.content}"
                if used + len(part) > chunk_budget and chunk_parts:
                    chunk_parts.append(
                        f"*[{len(new_chunks) - len(chunk_parts)} more chunk(s) "
                        f"omitted \u2014 details in journal]*"
                    )
                    break
                chunk_parts.append(part)
                used += len(part)
            ctx = "\n\n---\n\n".join(chunk_parts)
            try:
                r = await self._sa.run_json(_EXPANSION_PROMPT,
                    f"{session.anchor}\n\nSearch terms used: {', '.join(terms)}\n\nRetrieved ({len(new_chunks)} new chunks):\n\n{ctx}",
                    request, user)
                summary = r.get("summary", "")
                concepts = r.get("concepts", [])
            except Exception:
                summary = f"Found {len(new_chunks)} new chunks."

        it = IterationResult(iter_num, terms, [col_names.get(c, c) for c in col_ids],
                             len(all_chunks), len(new_chunks), summary, concepts)
        session.iterations.append(it)
        return it

    async def expand_terms(self, session: ResearchSession, current: List[str], request, user: Dict) -> List[str]:
        sums = _cb_condense_iterations(session.iterations)
        try:
            r = await self._sa.run_json(_EXPANSION_PROMPT,
                f"{session.anchor}\n\nPrevious terms: {', '.join(current)}\nFindings:\n{sums}\n\nSuggest new search terms that address uncovered aspects per the anchor above.",
                request, user)
            return r.get("terms", current)
        except Exception:
            return current

    async def should_continue(self, session: ResearchSession, request, user: Dict) -> bool:
        sums = _cb_condense_iterations(session.iterations)
        try:
            r = await self._sa.run_json(_CONTINUE_PROMPT, f"{session.anchor}\n\n{sums}", request, user)
            return bool(r.get("continue", False))
        except Exception:
            return False

    @staticmethod
    def _parse_retrieval(data: Dict, col_id: str, col_name: str) -> List[RetrievedChunk]:
        docs = data.get("documents", [[]])
        metas = data.get("metadatas", [[]])
        dists = data.get("distances", [[]])
        if not docs or not docs[0]:
            return []
        return [RetrievedChunk(content=docs[0][i], collection_id=col_id, collection_name=col_name,
                               source=(metas[0][i] if i < len(metas[0]) else {}).get("source", ""),
                               distance=(dists[0][i] if i < len(dists[0]) else 0.0))
                for i in range(len(docs[0]))]


# =============================================================================
#  Synthesis
# =============================================================================

_SYNTHESIS_PROMPT = """\
You are a source-grounded research synthesizer. Only make claims supported \
by the provided evidence.

## Rules
1. Every factual claim must reference a Collected Source by number [Source N]. \
No evidence = no claim — note it as a gap instead.
2. ZERO fabricated URLs. Only use URLs from the Collected Sources list verbatim.
3. Do not fill gaps from training data. State gaps explicitly.
4. Tag claims: [SOURCED] (directly stated), [INFERRED] (reasonable inference), \
[UNCERTAIN] (poorly supported). Never include [FABRICATED] claims.
5. Answer ONLY what the Research Anchor asks. Match requested format/depth.

## Output Structure

### Reasoning
Step-by-step analysis referencing specific sources by number.

### Answer
Evidence-grounded answer with confidence tags on each factual claim.

### Confidence Assessment
- Evidence quality: strong/moderate/thin/insufficient
- Source diversity and notable gaps

### Sources
ONLY URLs from Collected Sources. Format: 1. [Source N] Title — URL

### Gaps & Limitations
Uncovered aspects, conflicts, recommended follow-ups.\
"""

_VERIFICATION_PROMPT = """\
You are a factual accuracy reviewer. Given a research synthesis and the \
original source data it was built from, identify problems.

Check for: 1) Fabricated URLs not in Collected Sources, 2) Unsupported claims, \
3) Technology misidentification, 4) Generic template content, 5) Scope mismatch, \
6) Fabricated examples/code/commands.

Return JSON:
{"issues": [{"type": "fabricated_url|unsupported_claim|misidentification|generic_template|scope_mismatch|fabricated_example",
"severity": "critical|warning", "detail": "description", "location": "quote first 100 chars"}],
"url_check": {"urls_in_synthesis": [], "urls_in_sources": [], "fabricated": []},
"overall_credibility": "high|medium|low|very_low",
"recommendation": "pass|revise|flag_for_user"}\
"""

_REMEDIATION_PROMPT = """\
You are a factual accuracy editor. You will receive:
1. A research synthesis (markdown)
2. A list of verified issues found by a reviewer

Your job: rewrite the synthesis with ALL fabricated or unsupported content \
removed or corrected. Rules:
- DELETE sentences/bullet-points that contain fabricated examples, names, or claims.
- Do NOT replace removed content with new invented content.
- If removing content leaves a section empty, replace it with: \
"*[Removed: insufficient evidence]*"
- If a claim was flagged as unsupported, add "[UNVERIFIED]" before it \
rather than deleting, unless the claim is clearly fabricated.
- Keep ALL content that was NOT flagged — do not rewrite or rephrase it.
- Preserve the original markdown structure (headings, lists, formatting).
- Return ONLY the corrected synthesis markdown. No commentary.\
"""


class _Synthesizer:
    def __init__(self, valves, sub_agent: _SubAgent, journal: _Journal):
        self._v = valves
        self._sa = sub_agent
        self._j = journal

    async def synthesize(self, session: ResearchSession, request, user: Dict,
                          relevant_sources: List[Dict] = None,
                          trail_sources: List[Dict] = None,
                          event_emitter: Callable = None) -> str:
        prompt_md = self._j.read_entry(session.session_dir, "00-prompt.md")
        iter_mds = []
        for it in session.iterations:
            fn = f"{it.iteration_number + 2:02d}-iteration-{it.iteration_number}.md"
            c = self._j.read_entry(session.session_dir, fn)
            if c:
                iter_mds.append(c)

        all_sources = (relevant_sources or []) + (trail_sources or [])
        known_urls, known_domains = self._extract_known_urls(all_sources)

        # --- Budget-aware synthesis prompt construction ---
        budget_chars = _cb_usable_budget_chars(self._v.max_prompt_tokens)

        # Priority 1: Anchor + query + instructions (always included)
        header = f"# Research Anchor\n\n{session.anchor}\n"
        header += f"\n\n# Original Query\n\n{session.query}\n"

        source_count = len(all_sources)
        instructions = (
            "\n---\n\n## Synthesis Instructions\n\n"
            f"You have {source_count} source(s) to work with.\n"
            "- Address EVERY item in the Research Anchor's 'must_cover' list.\n"
            "- For items NOT covered by any source, list them in Gaps.\n"
            "- In Sources, list ONLY URLs that appear verbatim in Collected Sources.\n"
            "- If evidence is insufficient, produce a SHORTER answer that honestly "
            "reflects what the evidence supports. Do NOT pad with general knowledge.\n"
            "- Tag each factual claim: [SOURCED], [INFERRED], or [UNCERTAIN]."
        )

        fixed_chars = len(header) + len(instructions) + 200
        remaining = budget_chars - fixed_chars

        if remaining < 1000:
            parts = [header, instructions]
        else:
            source_budget = int(remaining * 0.55)
            iteration_budget = int(remaining * 0.35)
            context_budget = int(remaining * 0.10)

            # Priority 2: Sources (capped by authority)
            if all_sources:
                src_header = ("# Collected Sources (EXHAUSTIVE LIST)\n\n"
                              "These are the ONLY sources found during research. "
                              "Your answer must be built EXCLUSIVELY from this evidence. "
                              "Reference sources by number [Source N]. "
                              "The Sources section of your answer must ONLY contain URLs "
                              "from this list \u2014 copied exactly, character for character.\n\n")
                selected, omitted = _cb_cap_sources(all_sources, source_budget - len(src_header) - 100)
                src_entries = []
                for i, s in enumerate(selected, 1):
                    src_entries.append(
                        f"[Source {i}] **{s.get('title', 'Untitled')}**\n"
                        f"   - URL: {s.get('url', 'N/A')}\n"
                        f"   - Domain: {s.get('domain', '')}\n"
                        f"   - Summary: {s.get('summary', '')}\n"
                    )
                if omitted > 0:
                    src_entries.append(
                        f"\n*[{omitted} additional source(s) omitted due to context "
                        f"limit \u2014 full list in journal]*\n"
                    )
                source_section = src_header + "\n".join(src_entries)
            else:
                source_section = ("# Collected Sources\n\n"
                                  "**NO sources were collected.** Your synthesis must state "
                                  "that the research found no relevant sources. Do NOT "
                                  "generate an answer from general knowledge.\n")

            # Priority 3: Iteration summaries (most recent first, capped)
            iteration_section = _cb_build_iteration_text(iter_mds, iteration_budget)

            # Priority 4: Session context (lowest priority)
            context_section = ""
            if prompt_md and context_budget > 200:
                context_section = f"# Context\n\n{prompt_md[:context_budget]}\n"

            # Assemble \u2014 instructions right after anchor so they survive truncation
            parts = [header, instructions]
            if context_section:
                parts.append(context_section)
            if iteration_section:
                parts.append(iteration_section)
            parts.append(source_section)

        try:
            answer = await self._sa.run(_SYNTHESIS_PROMPT, "\n\n".join(parts), request, user)

            # Post-synthesis: programmatic URL scrubbing
            await _emit(event_emitter, "🔗 Validating URLs against collected sources...")
            answer, scrubbed = self._scrub_fabricated_urls(answer, known_urls, known_domains)
            if scrubbed:
                logger.warning("Scrubbed %d fabricated URL(s)", len(scrubbed))
                await _emit(event_emitter, f"⚠️ Removed {len(scrubbed)} fabricated URL(s)")
            else:
                await _emit(event_emitter, "✅ All URLs verified against sources")

            # Post-synthesis: LLM verification pass (skippable for small models)
            if self._v.skip_verification:
                logger.info("Skipping LLM verification (skip_verification=True)")
                await _emit(event_emitter, "⏭️ Verification skipped (small model mode)")
                verification = {"issues": [], "overall_credibility": "unverified", "recommendation": "pass"}
                issues = []
                critical_issues = []
                warning_issues = []
                credibility = "unverified"
            else:
                await _emit(event_emitter, "🔍 Running credibility verification (checking claims, terminology, scope)...")
                verification = await self._verify(answer, all_sources, session, request, user)
                issues = verification.get("issues", [])
                critical_issues = [i for i in issues if isinstance(i, dict) and i.get("severity") == "critical"]
                warning_issues = [i for i in issues if isinstance(i, dict) and i.get("severity") == "warning"]

                # Derive credibility from issues when LLM returns unknown/missing
                credibility = verification.get("overall_credibility", "unknown")
                if credibility in ("unknown", "", None):
                    credibility = self._derive_credibility(len(critical_issues), len(warning_issues), len(all_sources))
                    verification["overall_credibility"] = credibility

                if critical_issues:
                    await _emit(event_emitter, f"🔴 Verification: {len(critical_issues)} critical issue(s), credibility={credibility}")
                    for ci in critical_issues:
                        detail = ci.get("detail", ci.get("type", "unknown issue"))
                        await _emit(event_emitter, f"   ⚠️ {detail[:200]}")
                elif warning_issues:
                    await _emit(event_emitter, f"🟡 Verification: {len(warning_issues)} warning(s), credibility={credibility}")
                    for wi in warning_issues:
                        detail = wi.get("detail", wi.get("type", "unknown"))
                        await _emit(event_emitter, f"   🟡 {detail[:200]}")
                elif credibility in ("unknown", "very_low"):
                    await _emit(event_emitter, f"⚪ Verification inconclusive — credibility={credibility}")
                else:
                    await _emit(event_emitter, f"✅ Verification passed — credibility={credibility}")

            # Write verification results to journal
            self._write_verification_journal(session, verification, scrubbed, all_sources)

            # Remediate fabricated content if critical issues found
            if critical_issues:
                await _emit(event_emitter, f"\U0001f9f9 Removing {len(critical_issues)} fabricated/unsupported claim(s) from synthesis...")
                answer = await self._remediate_synthesis(answer, critical_issues + warning_issues, request, user)
                await _emit(event_emitter, "\u2705 Synthesis cleaned \u2014 fabricated content removed")

            # Append credibility report
            report = self._credibility_report(verification, scrubbed, all_sources)
            if report:
                answer += report

            self._j.write_synthesis(session, answer)
            self._j.write_manifest(session)
            return answer
        except Exception as e:
            logger.error("Synthesis failed: %s", e)
            fb = self._fallback(session)
            self._j.write_synthesis(session, fb)
            self._j.write_manifest(session)
            return fb

    def _write_verification_journal(self, session: ResearchSession,
                                     verification: Dict, scrubbed: List[str],
                                     sources: List[Dict]) -> None:
        """Write verification results as a separate journal entry."""
        lines = ["# Verification Report\n"]
        credibility = verification.get("overall_credibility", "unknown")
        lines.append(f"**Overall credibility:** {credibility}\n")
        lines.append(f"**Recommendation:** {verification.get('recommendation', 'unknown')}\n")
        lines.append(f"**Sources checked:** {len(sources)}\n")

        if scrubbed:
            lines.append(f"\n## Fabricated URLs Removed ({len(scrubbed)})\n")
            for url in scrubbed:
                lines.append(f"- {url}\n")

        issues = verification.get("issues", [])
        if issues:
            lines.append(f"\n## Issues Found ({len(issues)})\n")
            for issue in issues:
                if isinstance(issue, dict):
                    sev = issue.get("severity", "?")
                    itype = issue.get("type", "?")
                    detail = issue.get("detail", "")
                    loc = issue.get("location", "")[:100]
                    lines.append(f"- **[{sev}] {itype}**: {detail}\n")
                    if loc:
                        lines.append(f"  > {loc}\n")
        else:
            lines.append("\n## No issues found\n")

        url_check = verification.get("url_check", {})
        if url_check:
            fab = url_check.get("fabricated", [])
            if fab:
                lines.append(f"\n## URL Cross-Check\n")
                lines.append(f"- URLs in synthesis: {len(url_check.get('urls_in_synthesis', []))}\n")
                lines.append(f"- URLs in sources: {len(url_check.get('urls_in_sources', []))}\n")
                lines.append(f"- Fabricated (LLM-detected): {len(fab)}\n")

        fn = f"{len(session.iterations) + 3:02d}-verification.md"
        self._j.write_entry(session.session_dir, fn, "\n".join(lines))

    @staticmethod
    def _derive_credibility(critical_count: int, warning_count: int, source_count: int) -> str:
        """Compute credibility from issue counts when the LLM didn't provide one."""
        if source_count == 0:
            return "very_low"
        if critical_count >= 2:
            return "low"
        if critical_count == 1:
            return "low" if warning_count else "medium"
        if warning_count >= 3:
            return "medium"
        if warning_count >= 1:
            return "medium"
        return "high" if source_count >= 3 else "medium"

    async def _remediate_synthesis(self, synthesis: str, issues: List[Dict],
                                    request, user: Dict) -> str:
        """Rewrite synthesis to remove fabricated/unsupported content.

        Uses the LLM to surgically remove flagged content while preserving
        everything that was not flagged.
        """
        issue_list = "\n".join(
            f"- [{i.get('severity', '?')}] {i.get('type', '?')}: "
            f"{i.get('detail', '')} | Location: \"{i.get('location', '')[:150]}\""
            for i in issues if isinstance(i, dict)
        )
        user_prompt = (
            f"# Issues Found by Reviewer\n\n{issue_list}\n\n"
            f"# Synthesis to Clean\n\n{synthesis}"
        )
        try:
            cleaned = await self._sa.run(
                _REMEDIATION_PROMPT, user_prompt, request, user
            )
            if cleaned and len(cleaned) > 100:
                return cleaned
            logger.warning("Remediation returned too-short result, keeping original")
            return synthesis
        except Exception as e:
            logger.warning("Remediation pass failed: %s", e)
            return synthesis

    @staticmethod
    def _extract_known_urls(sources: List[Dict]) -> tuple:
        """Build known URL set AND known domain set from collected sources.

        Returns (known_urls: set, known_domains: set) where:
        - known_urls contains exact URLs with trailing-slash variants
        - known_domains contains netlocs from all source URLs
        """
        urls = set()
        domains = set()
        for s in sources:
            url = s.get("url", "")
            if url and url != "N/A":
                urls.add(url)
                stripped = url.rstrip("/")
                urls.add(stripped)
                urls.add(stripped + "/")
                try:
                    netloc = urlparse(url).netloc
                    if netloc:
                        domains.add(netloc.lower())
                except Exception:
                    pass
            # Also include domain field directly (covers knowledge-collection:// sources)
            domain = s.get("domain", "")
            if domain and "://" not in domain:
                domains.add(domain.lower())
        return urls, domains

    @staticmethod
    def _extract_urls_from_text(text: str) -> List[str]:
        import re as _re
        patterns = [
            r'\[.*?\]\((https?://[^\s\)]+)\)',
            r'(?<!\()(https?://[^\s\)\]>"]+)',
        ]
        found = []
        for pat in patterns:
            for match in _re.finditer(pat, text):
                url = match.group(1) if match.lastindex else match.group(0)
                found.append(url)
        return list(dict.fromkeys(found))

    @staticmethod
    def _scrub_fabricated_urls(text: str, known_urls: set, known_domains: set = None) -> tuple:
        import re as _re
        if not known_urls and not known_domains:
            return text, []
        known_domains = known_domains or set()
        urls_in_text = _Synthesizer._extract_urls_from_text(text)
        fabricated = []
        for url in urls_in_text:
            url_clean = url.rstrip("/")
            # Check 1: exact URL match (with trailing slash variants)
            if url in known_urls or url_clean in known_urls or url_clean + "/" in known_urls:
                continue
            # Check 2: URL is a sub-path or fragment of a known URL
            # e.g. known: https://docs.example.com/page → allow https://docs.example.com/page#section
            if any(url_clean.startswith(k.rstrip("/")) for k in known_urls if k.startswith("http")):
                continue
            # Check 3: URL domain matches a known source domain
            # This prevents scrubbing URLs the LLM found in RAG chunks from known sources
            try:
                url_domain = urlparse(url).netloc.lower()
                if url_domain and url_domain in known_domains:
                    continue
            except Exception:
                pass
            fabricated.append(url)
        if not fabricated:
            return text, []
        cleaned = text
        for url in fabricated:
            cleaned = _re.sub(
                r'\[([^\]]*)\]\(' + _re.escape(url) + r'\)',
                r'[\1] *(URL removed — not found in collected sources)*',
                cleaned,
            )
            cleaned = cleaned.replace(url, f"~~{url}~~ *(fabricated — not in collected sources)*")
        return cleaned, fabricated

    async def _verify(self, synthesis: str, sources: List[Dict],
                       session: ResearchSession, request, user: Dict) -> Dict:
        if not sources:
            return {"issues": [], "overall_credibility": "very_low", "recommendation": "flag_for_user"}
        source_list = "\n".join(
            f"[Source {i}] {s.get('title', '?')} — {s.get('url', 'N/A')}"
            for i, s in enumerate(sources, 1)
        )
        try:
            result = await self._sa.run_json(
                _VERIFICATION_PROMPT,
                f"# Research Anchor\n{session.anchor}\n\n# Collected Sources\n{source_list}\n\n# Synthesis to Verify\n{synthesis}",
                request, user,
            )
            if isinstance(result, dict):
                return result
            return {"issues": [], "overall_credibility": "medium", "recommendation": "pass"}
        except Exception as e:
            logger.warning("Verification failed: %s", e)
            return {"issues": [], "overall_credibility": "unknown", "recommendation": "pass"}

    @staticmethod
    def _credibility_report(verification: Dict, scrubbed: List[str], sources: List[Dict]) -> str:
        parts = []
        credibility = verification.get("overall_credibility", "unknown")
        issues = verification.get("issues", [])
        critical = [i for i in issues if isinstance(i, dict) and i.get("severity") == "critical"]
        warnings = [i for i in issues if isinstance(i, dict) and i.get("severity") == "warning"]

        parts.append("\n\n---\n\n## Research Credibility Report\n")
        source_count = len(sources)
        if source_count == 0:
            parts.append("⚠️ **No sources collected.** This synthesis has no evidentiary basis.\n")
        else:
            domains = len(set(s.get("domain", "") for s in sources if s.get("domain")))
            parts.append(f"- **Evidence basis:** {source_count} source(s) from {domains} domain(s)\n")

        labels = {
            "high": "🟢 High — claims well-supported by diverse sources",
            "medium": "🟡 Medium — some claims supported, gaps remain",
            "low": "🟠 Low — thin evidence, significant gaps",
            "very_low": "🔴 Very Low — insufficient evidence for reliable conclusions",
            "unknown": "⚪ Unknown — verification could not be completed",
        }
        parts.append(f"- **Credibility:** {labels.get(credibility, credibility)}\n")

        if scrubbed:
            parts.append(f"\n### ⚠️ Fabricated URLs Removed ({len(scrubbed)})\n")
            for url in scrubbed:
                parts.append(f"- ~~{url}~~\n")

        if critical:
            parts.append(f"\n### 🔴 Critical Issues ({len(critical)})\n")
            for i in critical:
                parts.append(f"- **{i.get('type', '?')}**: {i.get('detail', '')}\n")

        if warnings:
            parts.append(f"\n### 🟡 Warnings ({len(warnings)})\n")
            for i in warnings:
                parts.append(f"- **{i.get('type', '?')}**: {i.get('detail', '')}\n")

        rec = verification.get("recommendation", "pass")
        if rec == "revise":
            parts.append("\n**⚠️ Recommendation:** Cross-check key claims before relying on them.\n")
        elif rec == "flag_for_user":
            parts.append("\n**🔴 Recommendation:** Evidence insufficient. Consider `deep_research()` or refine query.\n")

        return "".join(parts)

    @staticmethod
    def _fallback(session: ResearchSession) -> str:
        lines = [f"# Research Summary (Fallback)\n\n**Query:** {session.query}\n"]
        for it in session.iterations:
            lines.append(f"\n## Iteration {it.iteration_number}\n- Terms: {', '.join(it.search_terms)}\n- Chunks: {it.chunks_found} (new: {it.new_chunks})\n")
            if it.summary:
                lines.append(f"\n{it.summary}\n")
        lines.append("\n*Full LLM synthesis unavailable — raw summaries above.*")
        return "\n".join(lines)


# =============================================================================
#  Quick Research (web-search only)
# =============================================================================

# _WEB_SEARCH_PROMPT removed — search results now come directly from search_web()

_RELEVANCE_GATE_PROMPT = """\
You are a strict relevance AND credibility judge. Given a RESEARCH ANCHOR \
and a list of web search results, judge each result on TWO axes.

**Axis 1 — Relevance:**
- "relevant": addresses the anchor's topic area, key concepts, or must_cover \
items — even if only partially. Err on the side of inclusion.
- "trail": tangentially related — broader field but not the specific topic.
- "drop": completely off-topic, about a different subject entirely.

**Axis 2 — Source Authority (0.0–1.0):**
- 1.0: Official documentation, primary project source, RFC/spec
- 0.8: Established tech publications (MDN, DigitalOcean, etc.)
- 0.6: Reputable blog posts, Stack Overflow accepted answers
- 0.4: Forum posts, personal blogs, undated content
- 0.2: Content farms, AI-generated summaries, aggregator sites
- 0.0: Obvious spam, placeholder, or fabricated content

Return JSON array in the same order as the input:
[{{"index": 0, "verdict": "relevant"|"trail"|"drop", "authority": 0.0-1.0, \
"reason": "one sentence"}}]

IMPORTANT: You are judging based on short search snippets, not full articles. \
Be generous with relevance — if the title or snippet plausibly relates to the \
anchor, mark it "relevant". Only "drop" truly unrelated results.
Respond ONLY with valid JSON.\
"""

_EXTRACT_TOPICS_PROMPT = """\
You are a research strategist. Given a RESEARCH ANCHOR and a set of \
relevant sources that were just confirmed to match the user's query, \
extract:

1. **Deeper topics**: specific sub-topics, techniques, or terms mentioned \
IN the relevant sources that would yield even more targeted results if \
searched directly.
2. **Adjacent leads**: related topics from 'trail' sources that could \
connect to relevant material if pursued one level deeper.

Return JSON:
{{"deeper_terms": ["specific term from source content to search next"],
  "adjacent_leads": ["terms from trail sources worth pursuing"],
  "covered_so_far": ["anchor concepts now fully covered"]}}

Stay anchored \u2014 only suggest terms that serve the user's original query.\
"""

_PIVOT_PROMPT = """\
The previous web search returned NO results relevant to the RESEARCH ANCHOR.
Generate 3-5 completely different search terms to approach the topic from \
new angles. Strategies:
- Use different terminology or synonyms for the same concepts
- Search for the PROBLEM the user is trying to solve, not the solution
- Try specific authors, tools, frameworks, or competing projects
- Search for academic/research terms instead of marketing terms
- Try contrasting viewpoints: "limitations of X" or "alternatives to X"
- Do NOT just rephrase the same query \u2014 genuinely pivot

Return JSON: {{"terms": ["term1", "term2", "term3"], "strategy": "one sentence"}}\
"""

_ANALYSIS_PROMPT = """\
Analyze collected web sources against the RESEARCH ANCHOR.

1. Summarize what the sources cover well.
2. Identify which specific aspects of the anchor are NOT yet \
addressed (gaps). Be precise \u2014 quote the anchor's must_cover items.
3. Assess source authority: note if ALL sources are from forums, blogs, \
or user-generated content with NO official documentation or primary \
project pages. If so, flag "missing_official_sources" as a gap.
4. Suggest search terms that would specifically fill those gaps. If \
official sources are missing, include terms like \
"<project> official documentation" or "<project> getting started guide".

Return JSON:
{{"summary":"2-3 paragraphs","gaps":["specific unaddressed aspects"],\
"has_official_source":true|false,\
"covered_aspects":["aspects well-covered"],\
"new_terms":["terms targeting the gaps"],\
"new_concepts":["concepts discovered"]}}\
"""


class _QuickResearcher:
    """Goal-driven research: iterate until min_relevant_sources are found.

    Flow per iteration:
    1. Web search with current terms
    2. Relevance gate: classify each result as relevant / trail / drop
    3. If relevant hits found -> extract deeper topics from them
       If no relevant hits -> pivot to completely different search terms
    4. Repeat until enough relevant sources accumulated or max_iterations hit
    5. Synthesize using ALL sources (relevant + trail chain)
    """

    def __init__(self, valves, sa: _SubAgent, j: _Journal, synth: _Synthesizer):
        self._v = valves
        self._sa = sa
        self._j = j
        self._synth = synth
        # Populated by run() so the orchestration layer (Tools.research)
        # can maintain a per-chat coverage/gap ledger across calls.
        self.last_covered: List[str] = []
        self.last_gaps: List[str] = []
        self.last_slug: str = ""
        self.last_sources: List[Dict] = []

    async def run(self, query: str, user_id: str, request, user: Dict, model_id: str, emitter=None) -> str:
        slug = _Journal.slugify(query)
        sdir = self._j.resolve_session_dir(user_id, slug, namespace="research")
        session = ResearchSession(session_id=f"research-{slug}", query=query, session_dir=sdir, model_id=model_id)
        self._j.write_prompt(session, model_id)
        await _emit(emitter, "\U0001f4cb Research session started")

        # Extract anchor once -- threads through every subsequent prompt
        anchor_result = await _extract_anchor(self._sa, query, request, user)
        session.anchor, initial_terms = anchor_result
        self._j.write_anchor(session)
        await _emit(emitter, "\U0001f3af Research anchor extracted")

        session.phase = ResearchPhase.RESEARCHING
        relevant_sources: List[Dict] = []       # confirmed anchor-matching
        trail_sources: List[Dict] = []           # led us toward relevant hits
        seen_urls: set = set()
        search_terms = initial_terms  # Use anchor-generated diverse terms
        tried_terms: set = set()
        target = self._v.min_relevant_sources
        consecutive_misses = 0
        rel_count = 0
        accumulated_covered: List[str] = []
        last_gaps: List[str] = []
        self.last_slug = slug

        for n in range(1, self._v.max_iterations + 1):
            # --- Step 1: Web search ---
            new_terms = [t for t in search_terms if t not in tried_terms]
            if not new_terms and n > 1:
                await _emit(emitter, f"\u2705 No new terms to explore \u2014 {len(relevant_sources)} relevant, {len(trail_sources)} trail collected")
                break
            tried_terms.update(new_terms)

            # Search each term individually — OR-joining fails on most
            # engines. Terms are independent → run them concurrently.
            raw = []
            search_batches = await asyncio.gather(
                *[self._web_search(session, term, request, user)
                  for term in new_terms[:3]],  # cap at 3 searches per iter
                return_exceptions=True,
            )
            for hits in search_batches:
                if isinstance(hits, list):
                    raw.extend(hits)
            pre_dedup = len(raw)
            raw = [r for r in raw if r.get("url", "") not in seen_urls]
            seen_urls.update(r.get("url", "") for r in raw)

            if not raw:
                consecutive_misses += 1
                dedup_note = f" ({pre_dedup} already seen)" if pre_dedup > 0 else ""
                it = IterationResult(n, new_terms, ["web_search"], 0, 0, f"No new results{dedup_note}.", [])
                session.iterations.append(it)
                self._j.write_iteration(session, it)
                if consecutive_misses >= 3:
                    await _emit(emitter, f"\u26a0\ufe0f {consecutive_misses} consecutive misses \u2014 proceeding with {rel_count} relevant")
                    break
                await _emit(emitter, f"\U0001f504 Iter {n}: 0 new results{dedup_note} \u2014 pivoting")
                search_terms = await self._pivot(session, tried_terms, request, user)
                continue

            # --- Step 2: Relevance gate ---
            rel, trail, dropped = await self._relevance_gate(session, raw, request, user)

            relevant_sources.extend(rel)
            trail_sources.extend(trail)
            all_kept = rel + trail
            await self._store_sources(session, all_kept, n)

            rel_count = len(relevant_sources)
            summary = ""

            # --- Step 3: Branch based on whether we got relevant hits ---
            if rel:
                consecutive_misses = 0
                # Extract deeper topics from the relevant sources
                extraction = await self._extract_topics(session, rel, trail, request, user)
                deeper = extraction.get("deeper_terms", [])
                adjacent = extraction.get("adjacent_leads", [])
                covered = extraction.get("covered_so_far", [])
                accumulated_covered.extend(c for c in covered if c not in accumulated_covered)
                summary = (f"Found {len(rel)} relevant, {len(trail)} trail, dropped {dropped}. "
                           f"Covered: {', '.join(covered[:3])}. Deeper: {', '.join(deeper[:3])}.")
                search_terms = deeper + adjacent  # dive deeper
                await _emit(emitter, f"\U0001f3af Iter {n}: +{len(rel)} relevant ({rel_count} total), +{len(trail)} trail \u2014 diving deeper")
            else:
                # Trail sources indicate on-topic results — only a full miss
                # when we get zero trail AND zero relevant
                if trail:
                    consecutive_misses = max(0, consecutive_misses)  # don't increment
                    summary = f"No direct hits but {len(trail)} trail, dropped {dropped}. Refining."
                    # Use trail content to inform next search instead of hard pivot
                    extraction = await self._extract_topics(session, [], trail, request, user)
                    search_terms = extraction.get("deeper_terms", []) + extraction.get("adjacent_leads", [])
                    if not search_terms:
                        search_terms = await self._pivot(session, tried_terms, request, user)
                    await _emit(emitter, f"\U0001f504 Iter {n}: 0 relevant, {len(trail)} trail \u2014 refining")
                else:
                    consecutive_misses += 1
                    summary = f"No results relevant to anchor ({len(raw)} searched, all dropped). Pivoting (miss {consecutive_misses})."
                    search_terms = await self._pivot(session, tried_terms, request, user)
                    await _emit(emitter, f"\U0001f504 Iter {n}: 0 relevant ({len(raw)} dropped) \u2014 pivoting ({consecutive_misses})")

            it = IterationResult(n, new_terms, ["web_search"], len(raw), len(all_kept), summary, [])
            session.iterations.append(it)
            self._j.write_iteration(session, it)

            # --- Step 4: Check goal ---
            if rel_count >= target:
                # Have enough sources, but check for gaps before stopping
                analysis = await self._analyze(session, request, user)
                gaps = analysis.get("gaps", [])
                last_gaps = gaps
                accumulated_covered.extend(
                    c for c in analysis.get("covered_aspects", [])
                    if c not in accumulated_covered
                )
                gap_terms = analysis.get("new_terms", [])
                has_official = analysis.get("has_official_source", True)

                # Continue if: explicit gaps with terms, OR no official source yet
                should_continue = n < self._v.max_iterations and (
                    (gaps and gap_terms) or not has_official
                )
                if should_continue:
                    if not has_official and gap_terms:
                        search_terms = gap_terms
                    elif gap_terms:
                        search_terms = gap_terms
                    else:
                        search_terms = await self._pivot(session, tried_terms, request, user)

                    reason_parts = []
                    if gaps:
                        reason_parts.append(", ".join(gaps[:2]))
                    if not has_official:
                        reason_parts.append("no official documentation found")
                    await _emit(emitter, f"\u2705 {rel_count}/{target} sources but gaps remain: {'; '.join(reason_parts)} \u2014 continuing")
                else:
                    await _emit(emitter, f"\u2705 Target reached: {rel_count}/{target} relevant sources")
                    if gaps:
                        await _emit(emitter, f"\U0001f50d Remaining gaps: {', '.join(gaps[:3])}")
                    break

            if consecutive_misses >= 3:
                await _emit(emitter, f"\u26a0\ufe0f 3 consecutive misses \u2014 proceeding with {rel_count} relevant")
                break

        # --- Final analysis (only if not already done in loop) ---
        if not (rel_count >= target):
            analysis = await self._analyze(session, request, user)
            last_gaps = analysis.get("gaps", []) or last_gaps
            accumulated_covered.extend(
                c for c in analysis.get("covered_aspects", [])
                if c not in accumulated_covered
            )
            if analysis.get("gaps"):
                await _emit(emitter, f"\U0001f50d Remaining gaps: {', '.join(analysis['gaps'][:3])}")

        # --- Synthesize ---
        session.phase = ResearchPhase.SYNTHESIZING
        await _emit(emitter, f"\U0001f9e0 Synthesizing ({len(relevant_sources)} relevant + {len(trail_sources)} trail sources)...")
        answer = await self._synth.synthesize(session, request, user,
                                               relevant_sources=relevant_sources,
                                               trail_sources=trail_sources,
                                               event_emitter=emitter)
        session.phase = ResearchPhase.COMPLETE
        await _emit(emitter, f"\U0001f4c1 Journal: research/{slug}/", done=True)

        if len(relevant_sources) < target:
            answer += (f"\n\n---\n\n\u26a0\ufe0f *Only {len(relevant_sources)}/{target} relevant sources found. "
                       f"Consider `deep_research()` to crawl authoritative domains.*")

        # Expose this run's coverage/gap markers for the per-chat ledger.
        self.last_covered = accumulated_covered
        self.last_gaps = last_gaps
        self.last_sources = relevant_sources + trail_sources
        return answer

    # --- Search helpers ---

    async def _web_search(self, session, query, request, user):
        """Call OWUI's search_web() directly — bypasses the LLM entirely."""
        from open_webui.routers.retrieval import search_web
        from starlette.concurrency import run_in_threadpool

        engine = getattr(request.app.state.config, "WEB_SEARCH_ENGINE", "")
        if not engine:
            logger.warning("No WEB_SEARCH_ENGINE configured in OWUI admin settings")
            return []

        try:
            results = await run_in_threadpool(search_web, request, engine, query)
        except Exception as e:
            logger.warning("search_web failed for '%s': %s", query[:80], e)
            return []

        parsed = []
        for r in results[:self._v.max_web_results]:
            domain = ""
            try:
                domain = urlparse(r.link).netloc
            except Exception:
                pass
            parsed.append({
                "url": r.link,
                "title": r.title or "",
                "summary": r.snippet or "",
                "domain": domain,
            })

        logger.info("Direct web search for '%s': %d results", query[:60], len(parsed))
        return parsed

    # --- Relevance gate: returns (relevant, trail, drop_count) ---

    async def _relevance_gate(self, session, sources, request, user):
        if not sources:
            return [], [], 0
        summaries = "\n".join(
            f"{i}. [{s.get('domain','')}] {s.get('title','?')}: {s.get('summary','')[:150]}"
            for i, s in enumerate(sources))
        try:
            verdicts = await self._sa.run_json(_RELEVANCE_GATE_PROMPT,
                f"{session.anchor}\n\nResults to judge:\n{summaries}", request, user)
            if not isinstance(verdicts, list):
                return sources, [], 0  # can't parse -- keep all as relevant
            relevant, trail = [], []
            for v in verdicts:
                if not isinstance(v, dict):
                    continue
                idx = v.get("index", -1)
                if 0 <= idx < len(sources):
                    verdict = v.get("verdict", "drop")
                    authority = v.get("authority", 0.5)
                    sources[idx]["authority"] = authority
                    sources[idx]["gate_reason"] = v.get("reason", "")
                    if verdict == "relevant":
                        relevant.append(sources[idx])
                    elif verdict == "trail":
                        trail.append(sources[idx])
            dropped = len(sources) - len(relevant) - len(trail)
            relevant.sort(key=lambda s: s.get("authority", 0.5), reverse=True)
            logger.info("Relevance gate: %d relevant, %d trail, %d dropped", len(relevant), len(trail), dropped)
            return relevant, trail, dropped
        except Exception:
            return sources, [], 0

    # --- Extract deeper topics from relevant hits ---

    async def _extract_topics(self, session, relevant, trail, request, user):
        rel_text = "\n\n".join(
            f"**[{s.get('domain','')}] {s.get('title','?')}**\n{s.get('summary','')}"
            for s in relevant[:10])
        trail_text = "\n\n".join(
            f"**[{s.get('domain','')}] {s.get('title','?')}**\n{s.get('summary','')}"
            for s in trail[:5])
        try:
            return await self._sa.run_json(_EXTRACT_TOPICS_PROMPT,
                f"{session.anchor}\n\n## Relevant Sources\n{rel_text}\n\n## Trail Sources\n{trail_text}",
                request, user)
        except Exception:
            return {"deeper_terms": [], "adjacent_leads": [], "covered_so_far": []}

    # --- Pivot: generate completely new terms when nothing relevant found ---

    async def _pivot(self, session, tried_terms, request, user):
        tried_str = ", ".join(sorted(tried_terms)[:20])
        iters = _cb_condense_iterations(session.iterations)
        try:
            r = await self._sa.run_json(_PIVOT_PROMPT,
                f"{session.anchor}\n\nAlready tried: {tried_str}\nResults so far:\n{iters}",
                request, user)
            return r.get("terms", [])
        except Exception:
            return []

    # --- Storage ---

    async def _store_sources(self, session, sources, iteration=0):
        sdir = os.path.join(session.session_dir, "sources")
        os.makedirs(sdir, exist_ok=True)
        prefix = f"iter{iteration}-" if iteration else ""
        for i, s in enumerate(sources, 1):
            domain = s.get("domain", "unknown")
            with open(os.path.join(sdir, f"{prefix}{domain}-{i}.md"), "w", encoding="utf-8") as f:
                f.write(f"# {s.get('title', domain)}\n\n[Source: {s.get('url', '')}]\n[Relevance: {s.get('relevance', 0.0)}]\n\n## Content\n\n{s.get('summary', '')}\n")
        idx = ["# Sources\n"] + [f"{i}. **{s.get('title', '?')}** ({s.get('domain', '')}) \u2014 [{s.get('url', '')}]({s.get('url', '')})\n   {s.get('summary', '')[:200]}\n" for i, s in enumerate(sources, 1)]
        self._j.write_entry(session.session_dir, f"sources-iter{iteration}.md", "\n".join(idx))

    # --- Final analysis ---

    async def _analyze(self, session, request, user):
        sdir = os.path.join(session.session_dir, "sources")
        texts = []
        if os.path.isdir(sdir):
            for fn in sorted(os.listdir(sdir)):
                fp = os.path.join(sdir, fn)
                if os.path.isfile(fp):
                    with open(fp, "r", encoding="utf-8") as f:
                        texts.append(f.read())
        if not texts:
            return {"summary": "No sources.", "gaps": ["entire query uncovered"], "new_terms": [], "covered_aspects": []}
        # Cap source text to fit context budget
        budget = _cb_usable_budget_chars(self._v.max_prompt_tokens)
        anchor_overhead = len(session.anchor) + 500
        source_budget = budget - anchor_overhead
        capped = []
        used = 0
        for t in texts:
            if used + len(t) > source_budget and capped:
                break
            capped.append(t)
            used += len(t)
        try:
            return await self._sa.run_json(_ANALYSIS_PROMPT,
                f"{session.anchor}\n\nSources ({len(capped)}/{len(texts)}):\n\n" + "\n\n---\n\n".join(capped), request, user)
        except Exception:
            return {"summary": f"Found {len(texts)} sources.", "gaps": [], "new_terms": [], "covered_aspects": []}


# =============================================================================
#  Knowledge Researcher (RAG-only, no crawling)
# =============================================================================

_KR_COLLECTION_RELEVANCE_PROMPT = """\
You are evaluating which existing knowledge collections are relevant to a \
research query. For each collection, assess whether its content would likely \
contain useful information.

Given:
- A RESEARCH ANCHOR describing what the user needs
- A list of available knowledge collections with names, descriptions, and file counts

Return JSON:
{"relevant": [
    {"id": "collection_id", "name": "collection name",
     "relevance": "high|medium|low",
     "rationale": "one sentence explaining why"}
],
"strategy": "one sentence describing search strategy across these collections"}

Only include collections rated medium or high relevance.
Respond ONLY with valid JSON.\
"""

_KR_GAP_ANALYSIS_PROMPT = """\
You are a research gap analyst. Given a RESEARCH ANCHOR and the findings \
accumulated so far from knowledge collection queries, identify:

1. What aspects of the original query are well-covered by retrieved evidence
2. What specific aspects remain UNCOVERED (gaps)
3. Whether the gaps could plausibly be filled by more targeted queries to \
the same collections, or whether entirely new knowledge sources are needed

Return JSON:
{"covered": ["aspects well addressed"],
 "gaps": ["specific uncovered aspects"],
 "exhausted": true/false,
 "gap_search_terms": ["terms that might close gaps within existing collections"],
 "external_topics": ["topics requiring NEW knowledge sources to address"],
 "confidence": "high|medium|low"}\
"""

_KR_SOURCE_RECOMMENDATION_PROMPT = """\
Based on research gaps identified, recommend web sources the user should \
crawl to build knowledge collections that would address missing information.

Given:
- The original research query and anchor
- Specific gaps that existing collections cannot address
- Web search results for those gap topics

Return JSON:
{"recommendations": [
    {"url": "https://example.com/docs",
     "domain": "example.com",
     "title": "Source title",
     "rationale": "Why this source would fill the gap",
     "gap_addressed": "Which specific gap this helps with",
     "priority": "high|medium|low"}
],
"crawl_suggestion": "Natural language recommendation for which domains to \
crawl and why"}\
"""


class _KnowledgeResearcher:
    """Iterative RAG-only research across existing OWUI knowledge collections."""

    _MAX_STALE = 3
    _SMALL_FILE_THRESHOLD = 5
    _SMALL_K_MULTIPLIER = 3

    def __init__(self, valves, sa: _SubAgent, j: _Journal, synth: _Synthesizer):
        self._v = valves
        self._sa = sa
        self._j = j
        self._synth = synth
        self._rag = _RagResearcher(valves, sa)
        self.last_sources: List[Dict] = []

    async def run(self, query: str, user_id: str, request, user: Dict, model_id: str, emitter=None, target_collection: str = "") -> str:
        slug = _Journal.slugify(query)
        sdir = self._j.resolve_session_dir(user_id, slug, namespace="knowledge-research")
        session = ResearchSession(session_id=f"kr-{slug}", query=query, session_dir=sdir, model_id=model_id)
        self._j.write_prompt(session, model_id)
        await _emit(emitter, "📋 Knowledge research started")

        # --- Step 1: Extract anchor ---
        anchor_result = await _extract_anchor(self._sa, query, request, user)
        session.anchor, initial_terms = anchor_result
        self._j.write_anchor(session)
        await _emit(emitter, "🎯 Research anchor extracted")

        # --- Step 2: Discover relevant collections ---
        session.phase = ResearchPhase.DISCOVERING
        if target_collection:
            await _emit(emitter, f"📌 Targeting collection: {target_collection}")

        all_cols, api_error = await self._rag.list_collections(request)

        if api_error:
            await _emit(emitter, f"❌ OWUI API error: {api_error}")
            session.phase = ResearchPhase.FAILED
            answer = (
                f"# Knowledge Research Failed\n\n"
                f"Could not retrieve knowledge collections from OWUI.\n\n"
                f"**Error:** {api_error}\n\n"
                f"**Troubleshooting:**\n"
                f"- Check that `owui_base_url` is correct in Valves\n"
                f"- Check that `owui_api_key` is set and valid\n"
                f"- Verify OWUI is running and accessible\n"
            )
            self._j.write_entry(session.session_dir, "synthesis.md", answer)
            self._j.write_manifest(session)
            await _emit(emitter, f"📁 Journal: knowledge-research/{slug}/", done=True)
            return answer

        if not all_cols:
            await _emit(emitter, "⚠️ No knowledge collections found — searching for source recommendations")
            recs = await self._recommend_sources(session, ["No existing knowledge collections"], initial_terms, request, user)
            session.phase = ResearchPhase.COMPLETE
            answer = self._build_empty_response(session, recs)
            self._j.write_entry(session.session_dir, "synthesis.md", answer)
            self._j.write_manifest(session)
            await _emit(emitter, f"📁 Journal: knowledge-research/{slug}/", done=True)
            return answer

        # User-specified collection or LLM-ranked selection
        if target_collection:
            relevant = self._find_collection_by_name(target_collection, all_cols)
            if not relevant:
                available = ", ".join(f"'{c['name']}'" for c in all_cols[:15])
                await _emit(emitter, f"⚠️ Collection '{target_collection}' not found")
                session.phase = ResearchPhase.COMPLETE
                answer = (
                    f"# Collection Not Found\n\n"
                    f"No collection matching **{target_collection}** was found.\n\n"
                    f"**Available collections:** {available}\n\n"
                    f"*Specify one of the above names, or omit the "
                    f"collection parameter to auto-select.*\n"
                )
                self._j.write_entry(session.session_dir, "synthesis.md", answer)
                self._j.write_manifest(session)
                await _emit(emitter, f"📁 Journal: knowledge-research/{slug}/", done=True)
                return answer
            await _emit(emitter, f"📌 Using specified collection: {relevant[0]['name']}")
        else:
            relevant = await self._rank_collections(session, all_cols, request, user)

        if not relevant:
            await _emit(emitter, "⚠️ No relevant collections identified — searching for source recommendations")
            recs = await self._recommend_sources(session, [f"No collections relevant to: {query}"], initial_terms, request, user)
            session.phase = ResearchPhase.COMPLETE
            answer = self._build_empty_response(session, recs)
            self._j.write_entry(session.session_dir, "synthesis.md", answer)
            self._j.write_manifest(session)
            await _emit(emitter, f"📁 Journal: knowledge-research/{slug}/", done=True)
            return answer

        col_ids = [r["id"] for r in relevant]
        col_map = {r["id"]: r["name"] for r in relevant}
        session.relevant_collection_ids = col_ids

        # Build file-level query targets (OWUI stores embeddings per-file)
        file_ids_map = {}
        for r in relevant:
            fids = r.get("data", {}).get("file_ids", [])
            if fids:
                file_ids_map[r["id"]] = fids

        # Compute adaptive k based on collection sizes
        effective_k = self._compute_adaptive_k(relevant)

        self._j.write_entry(session.session_dir, "01-collections.md",
                            self._fmt_collections(relevant, all_cols))
        total_files = sum(len(c.get("data", {}).get("file_ids", [])) for c in relevant)
        k_label = (
            f"deep retrieval (k={effective_k})"
            if effective_k > self._v.top_k_per_collection
            else f"standard RAG (k={effective_k})"
        )
        await _emit(emitter, f"📚 {len(relevant)}/{len(all_cols)} collection(s) selected ({total_files} files) — {k_label}")

        # --- Step 3: Iterative RAG ---
        session.phase = ResearchPhase.RESEARCHING
        terms = initial_terms
        stale = 0

        for n in range(1, self._v.max_iterations + 1):
            await _emit(emitter, f"🔍 Iteration {n}: querying {len(col_ids)} collection(s)...")
            it = await self._rag.run_iteration(session, terms, col_ids, col_map, n, request, user, k_override=effective_k, file_ids_map=file_ids_map)
            self._j.write_iteration(session, it)

            if it.new_chunks == 0:
                stale += 1
            else:
                stale = 0

            stale_note = f" (stale: {stale}/{self._MAX_STALE})" if stale > 0 else ""
            await _emit(emitter, f"📚 Iter {n}: {it.new_chunks} new chunk(s){stale_note}")

            if stale >= self._MAX_STALE:
                await _emit(emitter, f"⚠️ {self._MAX_STALE} stale iterations — analyzing gaps")
                break

            terms = await self._rag.expand_terms(session, terms, request, user)

            if n >= self._v.fixed_iterations:
                if n >= self._v.max_iterations:
                    break
                if not await self._rag.should_continue(session, request, user):
                    await _emit(emitter, "✅ Knowledge sufficiently explored")
                    break

        # --- Step 4: Gap analysis ---
        gap = await self._analyze_gaps(session, request, user)
        gaps = gap.get("gaps", [])
        exhausted = gap.get("exhausted", False)
        external = gap.get("external_topics", [])

        gf = len(session.iterations) + 3
        self._j.write_entry(session.session_dir, f"{gf:02d}-gap-analysis.md", self._fmt_gaps(gap))
        if gaps:
            await _emit(emitter, f"🔎 Gaps identified: {', '.join(gaps[:3])}")

        # --- Step 5: Web search recommendations (if gaps + exhausted) ---
        recs = None
        if gaps and (exhausted or stale >= self._MAX_STALE):
            await _emit(emitter, f"🌐 Searching for sources to fill {len(gaps)} gap(s)...")
            gap_terms = gap.get("gap_search_terms", []) or external or gaps
            recs = await self._recommend_sources(session, gaps, gap_terms, request, user)
            rc = len((recs or {}).get("recommendations", []))
            if rc:
                self._j.write_entry(session.session_dir, f"{gf + 1:02d}-recommendations.md",
                                    self._fmt_recs_journal(recs))
                await _emit(emitter, f"📡 Found {rc} source recommendation(s)")

        # --- Step 6: Synthesis + Validation ---
        session.phase = ResearchPhase.SYNTHESIZING
        await _emit(emitter, "🧠 Synthesizing findings...")

        rag_sources = self._build_rag_sources(session, col_map)
        await _emit(emitter, "🔒 Starting validation pipeline (URL check → credibility → remediation)...")
        answer = await self._synth.synthesize(session, request, user,
                                               relevant_sources=rag_sources,
                                               event_emitter=emitter)
        if recs:
            answer += self._fmt_recs_section(recs)

        session.phase = ResearchPhase.COMPLETE
        self._j.write_manifest(session)
        await _emit(emitter, f"📁 Journal: knowledge-research/{slug}/", done=True)
        self.last_sources = rag_sources
        return answer

    # --- Collection selection helpers ---

    @staticmethod
    def _find_collection_by_name(name, collections):
        target = name.strip().lower()
        # Pass 1: exact match
        for c in collections:
            if c.get("name", "").strip().lower() == target:
                col = c.copy()
                col["_relevance"] = "high"
                col["_rationale"] = "User-specified collection (exact match)"
                return [col]
        # Pass 2: substring match (either direction)
        for c in collections:
            col_name = c.get("name", "").strip().lower()
            if target in col_name or col_name in target:
                col = c.copy()
                col["_relevance"] = "high"
                col["_rationale"] = f"User-specified collection (partial match: '{c.get('name', '')}')"
                return [col]
        return []

    def _compute_adaptive_k(self, collections):
        total_files = sum(len(c.get("data", {}).get("file_ids", [])) for c in collections)
        base_k = self._v.top_k_per_collection
        if total_files <= self._SMALL_FILE_THRESHOLD:
            return base_k * self._SMALL_K_MULTIPLIER
        return base_k

    # --- Collection ranking ---

    async def _rank_collections(self, session, collections, request, user):
        summaries = "\n".join(
            f"- ID: {c['id']} | Name: {c['name']} | "
            f"Description: {c.get('description', 'None')} | "
            f"Files: {len(c.get('data', {}).get('file_ids', []))}"
            for c in collections)
        try:
            r = await self._sa.run_json(_KR_COLLECTION_RELEVANCE_PROMPT,
                f"{session.anchor}\n\nAvailable collections:\n{summaries}", request, user)
        except Exception as e:
            logger.error("Collection ranking failed: %s", e)
            return []
        valid = {c["id"]: c for c in collections}
        ranked = []
        for entry in r.get("relevant", []):
            cid = entry.get("id", "")
            if cid in valid:
                col = valid[cid].copy()
                col["_relevance"] = entry.get("relevance", "medium")
                col["_rationale"] = entry.get("rationale", "")
                ranked.append(col)
        return ranked[:self._v.max_collections]

    # --- Gap analysis ---

    async def _analyze_gaps(self, session, request, user):
        sums = _cb_condense_iterations(session.iterations)
        try:
            return await self._sa.run_json(_KR_GAP_ANALYSIS_PROMPT,
                f"{session.anchor}\n\nCollections: {', '.join(session.relevant_collection_ids)}\n\n"
                f"Iterations:\n{sums}", request, user)
        except Exception:
            return {"covered": [], "gaps": ["analysis failed"], "exhausted": True,
                    "gap_search_terms": [], "external_topics": [], "confidence": "low"}

    # --- Source recommendations ---

    async def _recommend_sources(self, session, gaps, search_terms, request, user):
        from open_webui.routers.retrieval import search_web
        from starlette.concurrency import run_in_threadpool

        engine = getattr(request.app.state.config, "WEB_SEARCH_ENGINE", "")
        if not engine:
            return {"recommendations": [], "crawl_suggestion": "No web search engine configured."}

        terms = search_terms[:5]

        async def _search(term):
            try:
                return await run_in_threadpool(search_web, request, engine, term)
            except Exception as e:
                logger.warning("Web search for '%s' failed: %s", term, e)
                return []

        batches = await asyncio.gather(
            *[_search(t) for t in terms], return_exceptions=True)
        all_results, seen = [], set()
        for results in batches:
            if not isinstance(results, list):
                continue
            for r in results:
                if r.link not in seen:
                    seen.add(r.link)
                    all_results.append(r)
        if not all_results:
            return {"recommendations": [], "crawl_suggestion": "Web search returned no results for gap topics."}

        listing = "\n".join(f"- {r.link} | {r.title or ''} | {r.snippet or ''}" for r in all_results[:20])
        gap_text = "\n".join(f"- {g}" for g in gaps)
        try:
            return await self._sa.run_json(_KR_SOURCE_RECOMMENDATION_PROMPT,
                f"{session.anchor}\n\nKnowledge gaps:\n{gap_text}\n\nWeb search results:\n{listing}",
                request, user)
        except Exception:
            recs, seen_d = [], set()
            for r in all_results[:5]:
                try:
                    d = urlparse(r.link).netloc
                except Exception:
                    continue
                if d not in seen_d:
                    seen_d.add(d)
                    recs.append({"url": r.link, "domain": d, "title": r.title or "",
                                 "rationale": r.snippet or "", "gap_addressed": "general", "priority": "medium"})
            return {"recommendations": recs, "crawl_suggestion": "Consider crawling these domains."}

    # --- Source building ---

    @staticmethod
    def _build_rag_sources(session, col_map):
        col_sums: Dict[str, List[str]] = {}
        for it in session.iterations:
            for cn in it.collections_queried:
                col_sums.setdefault(cn, []).append(it.summary or f"Iteration {it.iteration_number}")
        sources = []
        for cid, cname in col_map.items():
            sums = col_sums.get(cname, [])
            combined = " | ".join(s[:200] for s in sums[:3])
            sources.append({"title": cname, "url": f"knowledge-collection://{cid}",
                            "domain": cname, "summary": combined or "Queried but no summary available"})
        return sources

    # --- Formatting ---

    @staticmethod
    def _fmt_collections(relevant, all_cols):
        lines = ["# Knowledge Collections\n", f"**Total:** {len(all_cols)}\n",
                 f"**Selected:** {len(relevant)}\n", "\n## Selected\n"]
        for c in relevant:
            fc = len(c.get("data", {}).get("file_ids", []))
            lines.append(f"- **{c['name']}** [{c.get('_relevance', '?')}] ({fc} files)\n  {c.get('_rationale', '')}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_gaps(analysis):
        lines = ["# Gap Analysis\n"]
        for key, label in [("covered", "## Well Covered"), ("gaps", "## Gaps"), ("external_topics", "## Needs External Sources")]:
            items = analysis.get(key, [])
            if items:
                lines.append(f"\n{label}\n")
                lines.extend(f"- {i}\n" for i in items)
        lines.append(f"\n**Exhausted:** {analysis.get('exhausted', False)}\n**Confidence:** {analysis.get('confidence', 'unknown')}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_recs_journal(recs):
        lines = ["# Source Recommendations\n"]
        sug = recs.get("crawl_suggestion", "")
        if sug:
            lines.append(f"{sug}\n")
        for i, r in enumerate(recs.get("recommendations", []), 1):
            lines.append(f"{i}. **{r.get('title', r.get('domain', '?'))}** [{r.get('priority', 'medium')}]\n"
                         f"   URL: {r.get('url', '')}\n   Gap: {r.get('gap_addressed', '')}\n"
                         f"   Rationale: {r.get('rationale', '')}\n")
        return "\n".join(lines)

    @staticmethod
    def _fmt_recs_section(recs):
        items = recs.get("recommendations", [])
        sug = recs.get("crawl_suggestion", "")
        if not items and not sug:
            return ""
        lines = ["\n\n---\n", "## 📡 Recommended Sources for Knowledge Building\n"]
        if sug:
            lines.append(f"{sug}\n")
        if items:
            lines.append("\n| Priority | Domain | Gap Addressed | Rationale |")
            lines.append("|----------|--------|---------------|-----------|")
            for r in items:
                lines.append(f"| {r.get('priority', 'medium')} | [{r.get('domain', '')}]({r.get('url', '')}) "
                             f"| {r.get('gap_addressed', '')} | {r.get('rationale', '')[:100]} |")
        lines.append("\n*💡 Use `deep_research()` to automatically crawl these sources into knowledge collections, "
                     "or manually trigger a crawl in the SmolCrawl pipeline.*\n")
        return "\n".join(lines)

    @staticmethod
    def _build_empty_response(session, recs):
        lines = [f"# Knowledge Research: {session.query}\n",
                 "No existing knowledge collections were found relevant to this query.\n"]
        sug = recs.get("crawl_suggestion", "")
        if sug:
            lines.append(f"\n{sug}\n")
        for r in recs.get("recommendations", []):
            lines.append(f"- **[{r.get('domain', '')}]({r.get('url', '')})** [{r.get('priority', 'medium')}]\n"
                         f"  {r.get('rationale', '')}\n  Gap: {r.get('gap_addressed', '')}\n")
        lines.append("\n*Use `deep_research()` to crawl these domains into knowledge collections, "
                     "then re-run `knowledge_research()` to query them.*\n")
        return "\n".join(lines)


#  Status emitter helper
# =============================================================================


async def _emit(emitter, msg: str, done: bool = False):
    if emitter:
        await emitter({"type": "status", "data": {"description": msg, "done": done}})


# =============================================================================
#  Per-chat research ledger — graceful, resumable cap on research() fan-out
#
#  research() is a single-shot tool the chat model calls via native function
#  calling. On broad "list everything / comparison matrix" prompts the model
#  decomposes into one research() call PER ITEM and never converges, blowing
#  the model's context and OWUI's stream timeout. This ledger caps the number
#  of calls per conversation and, when the cap is hit, returns a STOP
#  directive plus a coverage/gap summary and a copy-paste continuation handle
#  so the user can knowingly go deeper instead of silently truncating.
# =============================================================================

import re as _re

_RESEARCH_LEDGER: Dict[str, Dict[str, Any]] = {}
_CONTINUE_RE = _re.compile(r"^\s*research\s+continue\s*:\s*", _re.IGNORECASE)


def _chat_key(chat_id: str, user: Dict) -> str:
    return chat_id or (user or {}).get("id", "") or "default"


def _dedup(seq: List[str], limit: Optional[int] = None) -> List[str]:
    out: List[str] = []
    for s in seq:
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)
        if limit and len(out) >= limit:
            break
    return out


def _coverage_block(ledger: Dict[str, Any], topic: str) -> str:
    covered = _dedup(ledger.get("covered", []), 12)
    gaps = _dedup(ledger.get("gaps", []), 12)
    cov = "\n".join(f"- {c}" for c in covered) or "- (none recorded yet)"
    gp = "\n".join(f"- {g}" for g in gaps) or "- (none recorded — coverage looks complete)"
    gap_hint = "; ".join(gaps[:3]) if gaps else topic
    cont = f"research continue: {topic} — focus: {gap_hint}"
    return (
        "\n\n---\n\n### 🔍 Research Coverage & Next Steps\n\n"
        f"**Topic:** {topic}\n\n"
        f"**Covered so far ({len(covered)}):**\n{cov}\n\n"
        f"**Not yet researched — open gaps ({len(gaps)}):**\n{gp}\n\n"
        "These gaps were left open to keep this response responsive rather "
        "than looping indefinitely. You can accept the findings as-is (with "
        "the gaps noted), or go deeper later *without re-covering the above*.\n\n"
        "**To continue, copy-paste this as your next message:**\n\n"
        f"```\n{cont}\n```\n"
    )


def _stop_payload(ledger: Dict[str, Any], topic: str, used: int, budget: int,
                   ran: bool) -> str:
    """Tool-result text that makes the chat model stop fanning out and
    present a final, gap-aware answer to the user."""
    did = (
        "The findings gathered earlier in this turn are sufficient to answer now."
        if not ran else
        "This was the final research pass allowed for this conversation."
    )
    return (
        f"⛔ **RESEARCH BUDGET REACHED** — {used}/{budget} research calls used "
        f"in this conversation.\n\n"
        "**Assistant instructions (do not echo this line to the user):** Do NOT "
        "call the `research` tool again in this turn. " + did + " Using only the "
        "research already returned above, write the user's final answer now "
        "(e.g. the requested list / comparison chart). Then append the "
        "\"Research Coverage & Next Steps\" section below verbatim so the user "
        "can decide whether to dig deeper."
        + _coverage_block(ledger, topic)
    )


# =============================================================================
#  Research → open-brain `sources` persistence (runs once on completion)
#  Mirror of deep_research/evidence_memory.py — keep in sync.
#  open-brain returns full structured rows, so the mnemory-era
#  ⟦EV:research⟧ header / label / artifact workarounds are GONE.
# =============================================================================

_EV_DEFAULT_VOL_DAYS = {"fast": 7, "medium": 180, "slow": 1095}
_EV_KEY_STOP = {
    "the", "a", "an", "of", "to", "for", "is", "are", "was", "were", "what",
    "how", "why", "when", "who", "which", "vs", "versus", "and", "or", "in",
    "on", "at", "about", "please", "research", "use", "find", "tell", "me",
    "do", "does", "can", "you", "give", "info", "information", "into",
}
_EV_CLASSIFY_SYS = (
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


def _compute_research_key(query: str) -> str:
    """Deterministic, order/phrasing-insensitive key for a research query."""
    text = re.sub(r"[^a-z0-9\s]", " ", (query or "").lower())
    toks = sorted(t for t in text.split() if t and t not in _EV_KEY_STOP)
    if not toks:
        toks = sorted(re.sub(r"[^a-z0-9\s]", " ",
                             (query or "").lower()).split())
    return hashlib.sha1(" ".join(toks).encode()).hexdigest()[:20]


def _ev_vol_map(spec: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for part in (spec or "").split(","):
        if ":" in part:
            k, _, v = part.partition(":")
            try:
                out[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return out or dict(_EV_DEFAULT_VOL_DAYS)


def _ev_ob(valves):
    base = (getattr(valves, "openbrain_url", "") or "").rstrip("/")
    key = getattr(valves, "openbrain_key", "") or ""
    return base, key


def _ev_normalize_sources(sources):
    """Pipeline source dicts -> {url,title,content,domain}. Dedup by url."""
    out = []
    seen = set()
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


async def _lookup_research_evidence(valves, *, query, user=None):
    """Current open-brain synthesis row for this request + staleness, or
    None. Best-effort: any error → None (cache miss)."""
    if not getattr(valves, "evidence_cache_enabled", True):
        return None
    base, key = _ev_ob(valves)
    if not base or not key:
        return None
    rkey = _compute_research_key(query)
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


def _format_cached_research(cached, tool_name: str) -> str:
    """Model-mediated cache-hit payload: prior finding + a directive."""
    date = cached.get("researched_on") or "an earlier date"
    stale = bool(cached.get("is_stale"))
    due = cached.get("due_date")
    # `claim` here is the stored synthesis source content — i.e. the FULL
    # detailed research result (the persist endpoint stores the full synthesis
    # as content). Return it in full on recall; do NOT truncate.
    body = (cached.get("claim") or "").strip()
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


async def _persist_research_evidence(valves, sub_agent, *, query, answer,
                                     user=None, request=None, kind,
                                     sources=None, event_emitter=None):
    """Persist a completed run to open-brain `sources`. Server supersedes
    the synthesis row in place + replaces per-source rows. Best-effort:
    never raises, never blocks the research result."""
    if not getattr(valves, "evidence_memory_enabled", True):
        return
    base, key = _ev_ob(valves)
    if not base or not key:
        return
    if not answer or len(answer.strip()) < 200:
        return  # not a real synthesis (e.g. STOP/budget directive)

    async def _emit(msg):
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
            _EV_CLASSIFY_SYS,
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

    vol_days = _ev_vol_map(getattr(valves, "evidence_volatility_days", ""))
    revalidate = vol_days.get(volatility,
                              _EV_DEFAULT_VOL_DAYS.get(volatility, 180))
    # Phase 3.2: an active thread (if the operator set one) auto-links the
    # gathered sources to that thread; empty => unthreaded inbox.
    active_thread = (getattr(valves, "active_thread_id", "") or "").strip() or None
    payload = {
        "research_key": _compute_research_key(query),
        "query": query[:400],
        # `claim` = short standalone summary (for the topical embedding + cache
        # display). `synthesis` = the FULL detailed research result, stored as
        # the open-brain source content (so robust findings are preserved, not a
        # one-paragraph summary).
        "claim": claim,
        "synthesis": (answer or "").strip(),
        "kind": kind,
        "volatility": volatility,
        "revalidate_days": revalidate,
        # `notebook` is the legacy label the direct /research/persist still
        # stamps; `topic_hint` is the same value, named for the curator (which
        # treats it only as a HINT — the thread DECISION is the curator's).
        "notebook": topic,
        "topic_hint": topic,
        # An explicit active_thread_id is honored as-is; the curator bypasses
        # its resolver when a thread_id is supplied (deliberate placement).
        "thread_id": active_thread,
        "model": (getattr(valves, "research_model", "")
                  or getattr(valves, "model", "") or None),
        "sources": _ev_normalize_sources(sources),
    }

    async def _direct_persist():
        """Fallback: write straight to /research/persist (pre-curator path)."""
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

    # Curator-first: when the managed thread inlet is enabled, hand the package
    # to the curator so OB1 resolves the thread (de-fragmentation). Fall back to
    # a direct persist on ANY failure (or when disabled) so research output is
    # never lost (best-effort, never blocks the result).
    use_curator = getattr(valves, "use_managed_thread_inlet", True)
    curator = (getattr(valves, "curator_url", "") or "").rstrip("/")
    if use_curator and curator:
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.post(
                    f"{curator}/ingest/research-package",
                    headers={"x-brain-key": key,
                             "Content-Type": "application/json"},
                    json=payload)
                if r.status_code == 200:
                    data = r.json() or {}
                    p = data.get("persist") or {}
                    decision = data.get("thread_decision", "?")
                    tname = data.get("thread_name") or data.get("thread_id")
                    await _emit(
                        f"🧠 research curated into open-brain (vol:{volatility}, "
                        f"revalidate {revalidate}d, "
                        f"{p.get('sources_written', 0)} sources -> "
                        f"thread '{tname}' [{decision}])")
                    return
                snippet = " ".join((r.text or "").split())[:200]
                logger.warning("research curate failed: HTTP %s %s -> "
                               "falling back to direct persist",
                               r.status_code, snippet)
        except Exception as exc:
            logger.warning("research curate error: %s -> falling back to "
                           "direct persist", type(exc).__name__)

    try:
        await _direct_persist()
    except Exception as exc:
        await _emit(f"🧠 research persist error: {type(exc).__name__}")



# =============================================================================
#  Tools (main entry point for OWUI)
# =============================================================================


class Tools:
    """Deep Research Tools for Open WebUI.

    Three tool methods:
    - research(query): Quick web-search-based exploration
    - knowledge_research(query): Iterative RAG across existing knowledge collections
    - deep_research(query): Full pipeline — discover, crawl, RAG, synthesize
    """

    class Valves(BaseModel):
        smolcrawl_url: str = Field(default="http://smolcrawl-pipelines:9099", description="SmolCrawl pipeline container URL")
        smolcrawl_api_key: str = Field(default="0p3n-w3bu!", description="Pipelines server API key")
        owui_base_url: str = Field(default="http://openwebui:8080", description="Open WebUI API base URL")
        owui_api_key: str = Field(default="", description="Bearer token for OWUI API")
        max_iterations: int = Field(default=3, ge=1, le=15, description="Hard cap on research iterations")
        fixed_iterations: int = Field(default=1, ge=1, le=5, description="Guaranteed iterations before continue-decision")
        min_relevant_sources: int = Field(default=3, ge=1, le=30, description="Target: stop researching once this many anchor-relevant sources are found")
        max_research_calls_per_chat: int = Field(default=5, ge=1, le=50, description="Max research() calls the chat model may make per conversation before a graceful stop directive is returned (prevents unbounded per-item fan-out on broad survey prompts). The user can resume past this with a 'research continue:' prompt.")
        max_web_results: int = Field(default=5, ge=1, le=50, description="Max web search results per query")
        sub_agent_nothink: bool = Field(default=True, description="Route mechanical sub-agent JSON calls (anchor, relevance gate, topic extraction, gap analysis, etc.) to the reasoning-disabled model alias for speed. With llama-swap this is the SAME model process (no reload) — it only skips thinking-token generation. Falls back to base model if the alias is unavailable.")
        nothink_suffix: str = Field(default=":nothink", description="Suffix appended to the chat model id to address its reasoning-disabled alias (matches the llama-swap setParamsByID '${MODEL_ID}:nothink' entry). Empty disables nothink routing.")
        max_parallel_queries: int = Field(default=5, ge=1, le=32, description="Max concurrent RAG collection/file queries per iteration. Parallelizes the term×collection fan-out without overloading the single embedding server.")
        include_sources: bool = Field(default=True, description="Append source references to answer")
        top_k_per_collection: int = Field(default=3, ge=1, le=20, description="Chunks per collection per query")
        max_collections: int = Field(default=5, ge=1, le=50, description="Max collections to search")
        max_domains: int = Field(default=3, ge=1, le=20, description="Max domains to discover")
        auto_approve_domains: bool = Field(default=True, description="Auto-approve all non-covered domains (skip manual approval)")
        max_prompt_tokens: int = Field(default=120000, ge=1000, le=262144, description="Token budget for SubAgent prompts. MUST be <= the model's per-request context lane minus response headroom. llama-swap qwen36-27b runs --parallel 2 over 262144 ctx => 131072-token lane; 120000 leaves ~11k for the answer. Raise only if you also lower parallelism. Set far lower for small models (e.g. 28000 for 32k ctx).")
        max_chunks_per_iteration: int = Field(default=10, ge=1, le=50, description="Max RAG chunks included in LLM summarization per iteration")
        skip_verification: bool = Field(default=False, description="Skip LLM verification/remediation passes (saves 2 LLM calls, faster for small models)")
        fileshed_compatible: bool = Field(default=True, description="Write journal to Fileshed Storage zone")
        storage_base_path: str = Field(default="/app/backend/data/user_files", description="Fileshed storage base path")
        save_journal: bool = Field(default=True, description="Persist research journal to disk")
        evidence_memory_enabled: bool = Field(default=True, description="On completion of a research run, persist the verified finding + its gathered sources to open-brain `sources` (structured rows; not user-personal data). Never writes per-iteration queries.")
        evidence_memory_quick_research: bool = Field(default=True, description="Persist evidence from quick research() too. Default ON so the cache covers every research entrypoint regardless of which tool first ran the question.")
        evidence_cache_enabled: bool = Field(default=True, description="Before a research run, check open-brain for prior research on the SAME request (research_key match). On hit, return the stored finding instead of researching (stale flagged). refresh=True bypasses and supersedes in place.")
        evidence_volatility_days: str = Field(default="fast:7,medium:180,slow:1095", description="Re-validation windows per volatility tier; past the window the LLM downgrades the fact to an educated guess.")
        openbrain_url: str = Field(default="http://openbrain-mcp:8000", description="open-brain MCP base URL (llm-net, trusted writer path). Research synthesis + sources persist here; the mnemory misuse is fully retired.")
        openbrain_key: str = Field(default="", description="open-brain MCP_ACCESS_KEY (x-brain-key). MUST be set to the OB1 docker .env MCP_ACCESS_KEY or research persistence/cache is skipped (graceful).")
        use_managed_thread_inlet: bool = Field(default=True, description="Route completed research through the OB1 research-thread inlet service (the curator) so threads are MANAGED — each run is resolved onto the best existing thread (de-fragmentation) before the write. Set FALSE to use the original unmanaged OB1 inlet instead (a direct /research/persist; sources land in the unthreaded inbox / whatever notebook string the run produced). Default TRUE. When TRUE but the curator is unreachable, the run still falls back to the direct inlet so research output is never blocked.")
        curator_url: str = Field(default="http://openbrain-curator:8000", description="open-brain research-curator (managed thread inlet) base URL, used when use_managed_thread_inlet is TRUE. A completed run is POSTed here as a 'package'; the curator resolves it onto the best existing thread and delegates the write to /research/persist. On any failure the run falls back to a direct /research/persist. Reuses openbrain_key as x-brain-key.")
        active_thread_id: str = Field(default="", description="Optional open-brain research thread UUID. When set, sources gathered by a research run are auto-linked to this thread (link_type=automatic, confirmed); empty = sources land in the unthreaded inbox (still recorded as a session). Create/list threads via the open-brain create_thread/list_threads MCP tools.")

    def __init__(self):
        self.valves = self.Valves()

    async def research(
        self, query: str, refresh: bool = False,
        __user__: dict = None, __metadata__: dict = None, __event_emitter__=None,
        __request__=None, __model__: dict = None, __event_call__=None,
        __chat_id__: str = "", __message_id__: str = "",
    ) -> str:
        """Quick research on a topic using web search. Stores findings to
        Fileshed and iteratively expands search terms internally.

        CACHE: if this exact request was researched before, the stored
        finding is returned instead of researching again (stale flagged).
        Set refresh=True ONLY when the user explicitly asks to re-research
        / update — it supersedes the cached evidence in place.

        IMPORTANT — call this ONCE per distinct research need. For a broad,
        list, survey, "all available X", or comparison-matrix request, pass
        the ENTIRE scope as a single query (e.g. "market analysis of all
        augmented reality headsets available today, with specs and pricing
        for each"). Do NOT decompose into one research() call per item — the
        tool expands search terms on its own, and a per-conversation budget
        will return a STOP directive once the budget is reached. If that
        directive appears, do not call research() again: answer the user
        with what was gathered and relay the coverage/gap summary verbatim.

        Args:
            query: The research question or topic to explore. For broad
                requests, state the full scope in one query.
            refresh: Force a fresh run, bypassing the cache and
                superseding prior evidence. Use only on explicit request.
        """
        mid = _SubAgent.resolve_model_id(__metadata__, __model__)
        key = _chat_key(__chat_id__, __user__)
        ledger = _RESEARCH_LEDGER.setdefault(
            key, {"count": 0, "covered": [], "gaps": [], "topic": ""}
        )
        budget = self.valves.max_research_calls_per_chat

        # Explicit user-driven continuation: resets the per-chat budget,
        # retains covered markers so we don't re-research them.
        cont = _CONTINUE_RE.match(query or "")
        if cont:
            query = (query[cont.end():].strip()
                     or ledger.get("topic") or query)
            ledger["count"] = 0
            ledger["gaps"] = []
            await _emit(__event_emitter__,
                        "🔁 Resuming research — prior coverage retained")

        if not ledger.get("topic"):
            ledger["topic"] = query
        topic = ledger.get("topic") or query

        # Cache check (skip on explicit refresh or a continuation). A hit
        # returns the stored finding without spending the per-chat budget.
        if not refresh and not cont:
            cached = await _lookup_research_evidence(
                self.valves, query=query, user=__user__ or {})
            if cached:
                await _emit(__event_emitter__,
                            "♻️ Found prior research for this request — "
                            "recalling", done=True)
                return _format_cached_research(cached, "research")

        # Hard stop: budget already spent and this is not a continuation.
        # Do NOT run another expensive pass — hand the model what it needs
        # to finalize plus a gap-aware continuation handle.
        if ledger["count"] >= budget:
            await _emit(__event_emitter__,
                        f"⛔ Research budget reached ({ledger['count']}/{budget}) "
                        "— returning coverage summary", done=True)
            return _stop_payload(ledger, topic, ledger["count"], budget,
                                 ran=False)

        # Bias the run away from already-covered ground.
        effective_query = query
        covered = _dedup(ledger.get("covered", []), 8)
        focus = _dedup(ledger.get("gaps", []), 5)
        if covered or focus:
            hint = []
            if covered:
                hint.append("Already researched (do not repeat): "
                            + "; ".join(covered))
            if focus:
                hint.append("Prioritize these open gaps: " + "; ".join(focus))
            effective_query = f"{query}\n\n({' | '.join(hint)})"

        sa = _SubAgent(mid, self.valves.max_prompt_tokens,
                       nothink_suffix=(self.valves.nothink_suffix
                                       if self.valves.sub_agent_nothink else ""))
        j = _Journal(self.valves)
        syn = _Synthesizer(self.valves, sa, j)
        researcher = _QuickResearcher(self.valves, sa, j, syn)
        answer = await researcher.run(
            effective_query, (__user__ or {}).get("id", ""),
            __request__, __user__ or {}, mid, __event_emitter__)

        # Update the per-chat ledger with this run's coverage/gaps.
        ledger["count"] += 1
        ledger["covered"] = _dedup(
            ledger["covered"] + (researcher.last_covered or []))
        ledger["gaps"] = _dedup([
            g for g in (ledger["gaps"] + (researcher.last_gaps or []))
            if g not in ledger["covered"]
        ])

        used = ledger["count"]
        if used >= budget:
            # Final allowed pass completed — append the stop directive so the
            # model finalizes instead of calling research() again.
            answer += "\n\n" + _stop_payload(
                ledger, topic, used, budget, ran=True)
        elif used == budget - 1:
            answer += (
                f"\n\n---\n\n*ℹ️ {used}/{budget} research calls used this "
                "conversation. One remains before a stop directive — make it "
                "count, or finalize now.*")
        if self.valves.evidence_memory_quick_research:
            try:
                await _persist_research_evidence(
                    self.valves, sa, query=query, answer=answer,
                    user=__user__ or {}, request=__request__,
                    kind="research",
                    sources=getattr(researcher, "last_sources", []),
                    event_emitter=__event_emitter__)
            except Exception:
                pass
        return answer

    async def knowledge_research(
        self, query: str, collection: str = "", refresh: bool = False,
        __user__: dict = None, __metadata__: dict = None, __event_emitter__=None,
        __request__=None, __model__: dict = None, __event_call__=None,
        __chat_id__: str = "", __message_id__: str = "",
    ) -> str:
        """Research a topic using existing knowledge collections.

        CACHE: a prior result for the same request is returned instead of
        re-querying (stale flagged). Set refresh=True only on explicit
        user request to re-research — it supersedes the cached evidence.

        Identifies which knowledge collections are relevant to the query,
        then iteratively queries them with expanding search terms to close
        information gaps. If the knowledge base cannot fully answer the
        query, recommends external sources to crawl.

        Use this when you already have knowledge collections and want to
        query them deeply before resorting to web search or crawling.

        Args:
            query: The research question or topic to investigate.
            collection: Optional name of a specific knowledge collection
                to query. When provided, skips auto-detection and uses
                this collection exclusively.
            refresh: Force a fresh run, bypassing the cache and
                superseding prior evidence. Use only on explicit request.
        """
        mid = _SubAgent.resolve_model_id(__metadata__, __model__)
        if not refresh:
            cached = await _lookup_research_evidence(
                self.valves, query=query, user=__user__ or {})
            if cached:
                await _emit(__event_emitter__,
                            "♻️ Found prior research for this request — "
                            "recalling", done=True)
                return _format_cached_research(cached, "knowledge_research")
        sa = _SubAgent(mid, self.valves.max_prompt_tokens,
                       nothink_suffix=(self.valves.nothink_suffix
                                       if self.valves.sub_agent_nothink else ""))
        j = _Journal(self.valves)
        syn = _Synthesizer(self.valves, sa, j)
        researcher = _KnowledgeResearcher(self.valves, sa, j, syn)
        answer = await researcher.run(
            query, (__user__ or {}).get("id", ""), __request__, __user__ or {}, mid, __event_emitter__,
            target_collection=collection)
        try:
            await _persist_research_evidence(
                self.valves, sa, query=query, answer=answer,
                user=__user__ or {}, request=__request__,
                kind="knowledge_research",
                sources=getattr(researcher, "last_sources", []),
                event_emitter=__event_emitter__)
        except Exception:
            pass
        return answer

    async def deep_research(
        self, query: str, refresh: bool = False,
        __user__: dict = None, __metadata__: dict = None, __event_emitter__=None,
        __request__=None, __model__: dict = None, __event_call__=None,
        __chat_id__: str = "", __message_id__: str = "",
    ) -> str:
        """Deep research on a topic — the full hybrid pipeline.

        Starts by querying existing knowledge collections (knowledge_research).
        If the knowledge base cannot answer the query, identifies sources via
        web search (research), crawls them into new collections, then queries
        the expanded knowledge base again before synthesising.

        Pipeline: knowledge_research → gap analysis → web search →
        crawl → knowledge_research → synthesize → verify.

        CACHE: a prior result for the same request is returned instead of
        re-running the (expensive) pipeline; stale results are flagged.
        Set refresh=True only when the user explicitly asks to
        re-research / update — it supersedes the cached evidence in place.

        Args:
            query: The research question or topic to investigate.
            refresh: Force a fresh run, bypassing the cache and
                superseding prior evidence. Use only on explicit request.
        """
        mid = _SubAgent.resolve_model_id(__metadata__, __model__)
        if not refresh:
            cached = await _lookup_research_evidence(
                self.valves, query=query, user=__user__ or {})
            if cached:
                await _emit(__event_emitter__,
                            "♻️ Found prior research for this request — "
                            "recalling", done=True)
                return _format_cached_research(cached, "deep_research")
        sa = _SubAgent(mid, self.valves.max_prompt_tokens,
                       nothink_suffix=(self.valves.nothink_suffix
                                       if self.valves.sub_agent_nothink else ""))
        j = _Journal(self.valves)
        rag = _RagResearcher(self.valves, sa)
        crawl = _CrawlClient(self.valves)
        synth = _Synthesizer(self.valves, sa, j)
        kr = _KnowledgeResearcher(self.valves, sa, j, synth)

        slug = _Journal.slugify(query)
        sdir = j.resolve_session_dir((__user__ or {}).get("id", ""), slug)
        session = ResearchSession(session_id=str(_uuid.uuid4()), query=query, session_dir=sdir, model_id=mid)
        j.write_prompt(session, mid)
        await _emit(__event_emitter__, "📋 Deep research started")

        # Extract anchor once — threads through all subsequent prompts
        anchor_result = await _extract_anchor(sa, query, __request__, __user__ or {})
        session.anchor, initial_terms = anchor_result
        j.write_anchor(session)
        await _emit(__event_emitter__, "🎯 Research anchor extracted")

        # =============================================================
        #  Phase 1: Knowledge Research — query existing collections
        # =============================================================
        session.phase = ResearchPhase.DISCOVERING
        all_cols, _ = await rag.list_collections(__request__)
        relevant = await kr._rank_collections(session, all_cols, __request__, __user__ or {})
        col_ids = [r["id"] for r in relevant]
        col_map = {r["id"]: r["name"] for r in relevant}
        session.relevant_collection_ids = list(col_ids)

        # Build file-level query targets (OWUI stores embeddings per-file)
        dr_file_ids_map = {}
        for r in relevant:
            fids = r.get("data", {}).get("file_ids", [])
            if fids:
                dr_file_ids_map[r["id"]] = fids

        await _emit(__event_emitter__, f"📚 {len(relevant)}/{len(all_cols)} collection(s) relevant")

        has_existing = bool(col_ids)
        stale = 0
        terms = initial_terms

        if has_existing:
            session.phase = ResearchPhase.RESEARCHING
            await _emit(__event_emitter__, "🔍 Phase 1: Querying existing knowledge...")
            for n in range(1, self.valves.max_iterations + 1):
                await _emit(__event_emitter__, f"🔍 KR iter {n}: querying {len(col_ids)} collection(s)...")
                it = await rag.run_iteration(session, terms, col_ids, col_map, n, __request__, __user__ or {}, file_ids_map=dr_file_ids_map)
                j.write_iteration(session, it)

                if it.new_chunks == 0:
                    stale += 1
                else:
                    stale = 0

                stale_note = f" (stale: {stale}/3)" if stale > 0 else ""
                await _emit(__event_emitter__, f"📚 KR iter {n}: {it.new_chunks} new chunk(s){stale_note}")

                if stale >= 3:
                    await _emit(__event_emitter__, "⚠️ Existing knowledge exhausted — analyzing gaps")
                    break

                terms = await rag.expand_terms(session, terms, __request__, __user__ or {})
                if n >= self.valves.fixed_iterations:
                    if n >= self.valves.max_iterations:
                        break
                    if not await rag.should_continue(session, __request__, __user__ or {}):
                        await _emit(__event_emitter__, "✅ Phase 1 complete — checking for gaps")
                        break

        # =============================================================
        #  Phase 2: Gap analysis — decide if we need external sources
        # =============================================================
        gap = await kr._analyze_gaps(session, __request__, __user__ or {})
        gaps = gap.get("gaps", [])
        exhausted = gap.get("exhausted", not has_existing)
        external = gap.get("external_topics", [])

        gf = len(session.iterations) + 3
        j.write_entry(session.session_dir, f"{gf:02d}-gap-analysis.md", kr._fmt_gaps(gap))

        needs_external = not has_existing or (gaps and (exhausted or stale >= 3))
        if gaps:
            await _emit(__event_emitter__, f"🔎 Gaps: {', '.join(gaps[:3])}" + (" — searching the web" if needs_external else ""))

        # =============================================================
        #  Phase 3: Web search → identify authoritative sources
        # =============================================================
        discovered = []
        if needs_external:
            await _emit(__event_emitter__, "🌐 Phase 2: Searching for authoritative sources...")
            gap_terms = gap.get("gap_search_terms", []) or external or gaps or initial_terms
            recs = await kr._recommend_sources(session, gaps, gap_terms, __request__, __user__ or {})
            discovered = recs.get("recommendations", [])
            if discovered:
                j.write_entry(session.session_dir, f"{gf + 1:02d}-source-discovery.md", kr._fmt_recs_journal(recs))
                await _emit(__event_emitter__, f"📡 Found {len(discovered)} source(s) to crawl")

        # =============================================================
        #  Phase 4: Crawl discovered sources into knowledge collections
        # =============================================================
        if discovered:
            session.phase = ResearchPhase.CRAWLING
            seen_d: set = set()
            targets = []
            for src in discovered:
                d = src.get("domain", "")
                if d and d not in seen_d:
                    seen_d.add(d)
                    targets.append(src)

            names = ", ".join(s.get("domain", "") for s in targets[:5])
            await _emit(__event_emitter__, f"🕷️ Crawling {len(targets)} domain(s): {names}")

            for src in targets[:self.valves.max_domains]:
                d = src.get("domain", "")
                kb = f"SmolCrawl - {d}"
                r = await crawl.trigger_crawl(d, kb, __event_emitter__)
                session.crawl_results.append(r)
                if r.success and r.kb_id:
                    session.relevant_collection_ids.append(r.kb_id)

            j.write_crawl_status(session)
            ok = sum(1 for r in session.crawl_results if r.success)
            await _emit(__event_emitter__, f"✅ Crawled {ok}/{len(targets)} domain(s)")

            # Refresh collections
            all_cols, _ = await rag.list_collections(__request__)
            col_map = {c["id"]: c["name"] for c in all_cols}

            # Refresh file_ids_map with newly crawled collections
            dr_file_ids_map = {}
            for c in all_cols:
                fids = c.get("data", {}).get("file_ids", [])
                if fids:
                    dr_file_ids_map[c["id"]] = fids
            for cr in session.crawl_results:
                if cr.success:
                    for c in all_cols:
                        if c["name"] == cr.kb_name:
                            if c["id"] not in session.relevant_collection_ids:
                                session.relevant_collection_ids.append(c["id"])
                            cr.kb_id = c["id"]
                            break

        # =============================================================
        #  Phase 5: Knowledge Research pass 2 — query expanded KBs
        # =============================================================
        new_ids = [cid for cid in session.relevant_collection_ids if cid not in col_ids]
        if new_ids or (discovered and not has_existing):
            session.phase = ResearchPhase.RESEARCHING
            all_ids = session.relevant_collection_ids
            await _emit(__event_emitter__, f"🔍 Phase 3: Querying {len(all_ids)} collection(s) (+{len(new_ids)} new)...")

            p2_terms = initial_terms + gaps[:3]
            p2_stale = 0
            start = len(session.iterations) + 1

            for n in range(start, start + self.valves.max_iterations):
                await _emit(__event_emitter__, f"🔍 KR iter {n}: querying {len(all_ids)} collection(s)...")
                it = await rag.run_iteration(session, p2_terms, all_ids, col_map, n, __request__, __user__ or {}, file_ids_map=dr_file_ids_map)
                j.write_iteration(session, it)

                if it.new_chunks == 0:
                    p2_stale += 1
                else:
                    p2_stale = 0

                await _emit(__event_emitter__, f"📚 KR iter {n}: {it.new_chunks} new chunk(s)")
                if p2_stale >= 3:
                    break

                p2_terms = await rag.expand_terms(session, p2_terms, __request__, __user__ or {})
                if (n - start + 1) >= self.valves.fixed_iterations:
                    if not await rag.should_continue(session, __request__, __user__ or {}):
                        await _emit(__event_emitter__, "✅ Phase 3 complete")
                        break

        # =============================================================
        #  Phase 6: Synthesize + verify
        # =============================================================
        session.phase = ResearchPhase.SYNTHESIZING
        await _emit(__event_emitter__, "🧠 Synthesizing findings...")
        rag_sources = kr._build_rag_sources(session, col_map)
        answer = await synth.synthesize(session, __request__, __user__ or {},
                                        relevant_sources=rag_sources,
                                        event_emitter=__event_emitter__)
        session.phase = ResearchPhase.COMPLETE
        j.write_manifest(session)
        await _emit(__event_emitter__, f"📁 Journal: deep-research/{slug}/", done=True)
        try:
            await _persist_research_evidence(
                self.valves, sa, query=query, answer=answer,
                user=__user__ or {}, request=__request__,
                kind="deep_research", sources=rag_sources,
                event_emitter=__event_emitter__)
        except Exception:
            pass
        return answer
