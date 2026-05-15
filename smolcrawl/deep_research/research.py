"""
Goal-driven web-search research (research() method logic).

Single Responsibility: Handles the lightweight research flow that uses
web search + Fileshed. Iterates until min_relevant_sources are
accumulated, using relevance gating and topic extraction to dive deeper.
"""

import asyncio
import logging
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .journal import ResearchJournal
from .models import IterationResult, ResearchPhase, ResearchSession, Valves
from .sub_agent import SubAgent, extract_anchor
from .synthesis import Synthesizer
from .context_budget import condense_iterations, usable_budget_chars

logger = logging.getLogger("deep_research.research")

# Web search results now come directly from OWUI's search_web() function.

_RELEVANCE_GATE_PROMPT = """\
You are a strict relevance AND credibility judge. Given a RESEARCH ANCHOR \
and a list of web search results, judge each result on TWO axes.

**Axis 1 — Relevance:**
- "relevant": addresses the anchor's topic area, key concepts, or must_cover \
items — even if only partially. A page title or snippet that mentions the \
core subject IS relevant. Err on the side of inclusion.
- "trail": tangentially related — about the broader field but not the \
specific topic. Also use for contrasting viewpoints or adjacent tools.
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

_ANALYSIS_SYSTEM_PROMPT = """\
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


class QuickResearcher:
    """Goal-driven research: iterate until min_relevant_sources are found.

    Flow per iteration:
    1. Web search with current terms
    2. Relevance gate: classify each result as relevant / trail / drop
    3. If relevant hits found -> extract deeper topics from them
       If no relevant hits -> pivot to completely different search terms
    4. Repeat until enough relevant sources accumulated or max_iterations hit
    5. Synthesize using ALL sources (relevant + trail chain)
    """

    def __init__(
        self,
        valves: Valves,
        sub_agent: SubAgent,
        journal: ResearchJournal,
    ):
        self._valves = valves
        self._sub_agent = sub_agent
        self._journal = journal
        self._synthesizer = Synthesizer(valves, sub_agent, journal)
        # Populated by run() so the orchestration layer (Tools.research)
        # can maintain a per-chat coverage/gap ledger across calls.
        self.last_covered: List[str] = []
        self.last_gaps: List[str] = []
        self.last_slug: str = ""

    async def run(
        self,
        query: str,
        user_id: str,
        request: Any,
        user: Dict,
        model_id: str,
        event_emitter: Optional[Callable] = None,
    ) -> str:
        slug = ResearchJournal.slugify(query)
        session_dir = self._journal.resolve_session_dir(
            user_id, slug, namespace="research"
        )
        session = ResearchSession(
            session_id=f"research-{slug}",
            query=query,
            session_dir=session_dir,
            phase=ResearchPhase.INITIALIZING,
            model_id=model_id,
        )
        self._journal.write_prompt(session, model_id)
        await self._emit_status(event_emitter, "\U0001f4cb Research session started")

        # Extract anchor once -- threads through every subsequent prompt
        anchor_result = await extract_anchor(
            self._sub_agent, query, request, user
        )
        session.anchor, initial_terms = anchor_result
        self._journal.write_anchor(session)
        await self._emit_status(event_emitter, "\U0001f3af Research anchor extracted")

        session.phase = ResearchPhase.RESEARCHING
        relevant_sources: List[Dict] = []
        trail_sources: List[Dict] = []
        seen_urls: set = set()
        search_terms = initial_terms  # Use anchor-generated diverse terms
        tried_terms: set = set()
        target = self._valves.min_relevant_sources
        consecutive_misses = 0
        rel_count = 0
        accumulated_covered: List[str] = []
        last_gaps: List[str] = []
        self.last_slug = slug

        for n in range(1, self._valves.max_iterations + 1):
            # --- Step 1: Web search ---
            new_terms = [t for t in search_terms if t not in tried_terms]
            if not new_terms and n > 1:
                await self._emit_status(
                    event_emitter,
                    f"\u2705 No new terms to explore \u2014 {len(relevant_sources)} relevant, {len(trail_sources)} trail collected",
                )
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
                it = IterationResult(
                    n, new_terms, ["web_search"], 0, 0,
                    f"No new results{dedup_note}.", [],
                )
                session.iterations.append(it)
                self._journal.write_iteration(session, it)
                if consecutive_misses >= 3:
                    await self._emit_status(
                        event_emitter,
                        f"\u26a0\ufe0f {consecutive_misses} consecutive misses \u2014 proceeding with {rel_count} relevant",
                    )
                    break
                await self._emit_status(
                    event_emitter,
                    f"\U0001f504 Iter {n}: 0 new results{dedup_note} \u2014 pivoting",
                )
                search_terms = await self._pivot(session, tried_terms, request, user)
                continue

            # --- Step 2: Relevance gate ---
            rel, trail, dropped = await self._relevance_gate(
                session, raw, request, user
            )
            relevant_sources.extend(rel)
            trail_sources.extend(trail)
            all_kept = rel + trail
            await self._store_sources(session, all_kept, n)

            rel_count = len(relevant_sources)
            summary = ""

            # --- Step 3: Branch on relevance ---
            if rel:
                consecutive_misses = 0
                extraction = await self._extract_topics(
                    session, rel, trail, request, user
                )
                deeper = extraction.get("deeper_terms", [])
                adjacent = extraction.get("adjacent_leads", [])
                covered = extraction.get("covered_so_far", [])
                accumulated_covered.extend(
                    c for c in covered if c not in accumulated_covered
                )
                summary = (
                    f"Found {len(rel)} relevant, {len(trail)} trail, dropped {dropped}. "
                    f"Covered: {', '.join(covered[:3])}. Deeper: {', '.join(deeper[:3])}."
                )
                search_terms = deeper + adjacent
                await self._emit_status(
                    event_emitter,
                    f"\U0001f3af Iter {n}: +{len(rel)} relevant ({rel_count} total), "
                    f"+{len(trail)} trail \u2014 diving deeper",
                )
            else:
                # Trail sources indicate on-topic results — only a full miss
                # when we get zero trail AND zero relevant
                if trail:
                    consecutive_misses = max(0, consecutive_misses)  # don't increment
                    summary = f"No direct hits but {len(trail)} trail, dropped {dropped}. Refining."
                    extraction = await self._extract_topics(
                        session, [], trail, request, user
                    )
                    search_terms = extraction.get("deeper_terms", []) + extraction.get("adjacent_leads", [])
                    if not search_terms:
                        search_terms = await self._pivot(session, tried_terms, request, user)
                    await self._emit_status(
                        event_emitter,
                        f"\U0001f504 Iter {n}: 0 relevant, {len(trail)} trail \u2014 refining",
                    )
                else:
                    consecutive_misses += 1
                    summary = f"No results relevant to anchor ({len(raw)} searched, all dropped). Pivoting (miss {consecutive_misses})."
                    search_terms = await self._pivot(session, tried_terms, request, user)
                    await self._emit_status(
                        event_emitter,
                        f"\U0001f504 Iter {n}: 0 relevant ({len(raw)} dropped) \u2014 pivoting ({consecutive_misses})",
                    )

            it = IterationResult(n, new_terms, ["web_search"], len(raw), len(all_kept), summary, [])
            session.iterations.append(it)
            self._journal.write_iteration(session, it)

            # --- Step 4: Check goal ---
            if rel_count >= target:
                # Have enough sources, but check for gaps before stopping
                analysis = await self._analyze_sources(session, request, user)
                gaps = analysis.get("gaps", [])
                last_gaps = gaps
                accumulated_covered.extend(
                    c for c in analysis.get("covered_aspects", [])
                    if c not in accumulated_covered
                )
                gap_terms = analysis.get("new_terms", [])
                has_official = analysis.get("has_official_source", True)

                # Continue if: explicit gaps with terms, OR no official source yet
                should_continue = n < self._valves.max_iterations and (
                    (gaps and gap_terms)
                    or not has_official
                )
                if should_continue:
                    if not has_official and gap_terms:
                        # Prioritize official-source queries
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
                    await self._emit_status(
                        event_emitter,
                        f"\u2705 {rel_count}/{target} sources but gaps remain: "
                        f"{'; '.join(reason_parts)} \u2014 continuing",
                    )
                else:
                    await self._emit_status(
                        event_emitter,
                        f"\u2705 Target reached: {rel_count}/{target} relevant sources",
                    )
                    if gaps:
                        await self._emit_status(
                            event_emitter,
                            f"\U0001f50d Remaining gaps: {', '.join(gaps[:3])}",
                        )
                    break

            if consecutive_misses >= 3:
                await self._emit_status(
                    event_emitter,
                    f"\u26a0\ufe0f 3 consecutive misses \u2014 proceeding with {rel_count} relevant",
                )
                break

        # --- Final analysis (only if not already done in loop) ---
        if not (rel_count >= target):
            analysis = await self._analyze_sources(session, request, user)
            last_gaps = analysis.get("gaps", []) or last_gaps
            accumulated_covered.extend(
                c for c in analysis.get("covered_aspects", [])
                if c not in accumulated_covered
            )
            if analysis.get("gaps"):
                await self._emit_status(
                    event_emitter,
                    f"\U0001f50d Remaining gaps: {', '.join(analysis['gaps'][:3])}",
                )

        # --- Synthesize ---
        session.phase = ResearchPhase.SYNTHESIZING
        await self._emit_status(
            event_emitter,
            f"\U0001f9e0 Synthesizing ({len(relevant_sources)} relevant "
            f"+ {len(trail_sources)} trail sources)...",
        )
        answer = await self._synthesizer.synthesize(
            session, request, user,
            relevant_sources=relevant_sources,
            trail_sources=trail_sources,
            event_emitter=event_emitter,
        )
        session.phase = ResearchPhase.COMPLETE
        await self._emit_status(
            event_emitter, f"\U0001f4c1 Journal: research/{slug}/", done=True
        )

        if len(relevant_sources) < target:
            answer += (
                f"\n\n---\n\n\u26a0\ufe0f *Only {len(relevant_sources)}/{target} "
                f"relevant sources found. Consider `deep_research()` to crawl "
                f"authoritative domains.*"
            )

        # Expose this run's coverage/gap markers for the per-chat ledger.
        self.last_covered = accumulated_covered
        self.last_gaps = last_gaps
        return answer

    # --- Search helpers ---

    async def _web_search(
        self, session: ResearchSession, query: str, request: Any, user: Dict
    ) -> List[Dict]:
        """Call OWUI's search_web() directly — bypasses the LLM entirely."""
        from open_webui.routers.retrieval import search_web
        from starlette.concurrency import run_in_threadpool
        from urllib.parse import urlparse

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
        for r in results[:self._valves.max_web_results]:
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

    # --- Relevance gate ---

    async def _relevance_gate(
        self,
        session: ResearchSession,
        sources: List[Dict],
        request: Any,
        user: Dict,
    ) -> tuple:
        """Returns (relevant, trail, drop_count)."""
        if not sources:
            return [], [], 0
        summaries = "\n".join(
            f"{i}. [{s.get('domain','')}] {s.get('title','?')}: "
            f"{s.get('summary','')[:150]}"
            for i, s in enumerate(sources)
        )
        try:
            verdicts = await self._sub_agent.run_json(
                system_prompt=_RELEVANCE_GATE_PROMPT,
                user_prompt=f"{session.anchor}\n\nResults to judge:\n{summaries}",
                request=request,
                user=user,
            )
            if not isinstance(verdicts, list):
                return sources, [], 0
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
            # Sort relevant sources by authority (highest first)
            relevant.sort(key=lambda s: s.get("authority", 0.5), reverse=True)
            logger.info(
                "Relevance gate: %d relevant, %d trail, %d dropped",
                len(relevant), len(trail), dropped,
            )
            return relevant, trail, dropped
        except Exception:
            return sources, [], 0

    # --- Extract deeper topics ---

    async def _extract_topics(
        self,
        session: ResearchSession,
        relevant: List[Dict],
        trail: List[Dict],
        request: Any,
        user: Dict,
    ) -> Dict:
        rel_text = "\n\n".join(
            f"**[{s.get('domain','')}] {s.get('title','?')}**\n{s.get('summary','')}"
            for s in relevant[:10]
        )
        trail_text = "\n\n".join(
            f"**[{s.get('domain','')}] {s.get('title','?')}**\n{s.get('summary','')}"
            for s in trail[:5]
        )
        try:
            return await self._sub_agent.run_json(
                system_prompt=_EXTRACT_TOPICS_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"## Relevant Sources\n{rel_text}\n\n"
                    f"## Trail Sources\n{trail_text}"
                ),
                request=request,
                user=user,
            )
        except Exception:
            return {"deeper_terms": [], "adjacent_leads": [], "covered_so_far": []}

    # --- Pivot ---

    async def _pivot(
        self,
        session: ResearchSession,
        tried_terms: set,
        request: Any,
        user: Dict,
    ) -> List[str]:
        tried_str = ", ".join(sorted(tried_terms)[:20])
        iters = condense_iterations(session.iterations)
        try:
            r = await self._sub_agent.run_json(
                system_prompt=_PIVOT_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Already tried: {tried_str}\n"
                    f"Results so far:\n{iters}"
                ),
                request=request,
                user=user,
            )
            return r.get("terms", [])
        except Exception:
            return []

    # --- Storage ---

    async def _store_sources(
        self,
        session: ResearchSession,
        sources: List[Dict],
        iteration: int = 0,
    ) -> None:
        sources_dir = os.path.join(session.session_dir, "sources")
        os.makedirs(sources_dir, exist_ok=True)
        prefix = f"iter{iteration}-" if iteration else ""

        for i, source in enumerate(sources, 1):
            domain = source.get("domain", "unknown")
            filename = f"{prefix}{domain}-{i}.md"
            content = (
                f"# {source.get('title', domain)}\n\n"
                f"[Source URL: {source.get('url', '')}]\n"
                f"[Retrieved: {datetime.now().isoformat()}]\n"
                f"[Relevance: {source.get('relevance', 0.0)}]\n\n"
                f"## Content\n\n"
                f"{source.get('summary', 'No content available.')}\n"
            )
            path = os.path.join(sources_dir, filename)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        index_lines = ["# Sources\n"] + [
            f"{i}. **{s.get('title', '?')}** ({s.get('domain', '')}) "
            f"\u2014 {s.get('relevance', 0):.2f}\n"
            for i, s in enumerate(sources, 1)
        ]
        self._journal.write_entry(
            session.session_dir,
            f"sources-iter{iteration}.md",
            "\n".join(index_lines),
        )

    # --- Final analysis ---

    async def _analyze_sources(
        self,
        session: ResearchSession,
        request: Any,
        user: Dict,
    ) -> Dict:
        sources_dir = os.path.join(session.session_dir, "sources")
        texts = []
        if os.path.isdir(sources_dir):
            for fn in sorted(os.listdir(sources_dir)):
                fp = os.path.join(sources_dir, fn)
                if os.path.isfile(fp):
                    with open(fp, "r", encoding="utf-8") as f:
                        texts.append(f.read())
        if not texts:
            return {
                "summary": "No sources.",
                "gaps": ["entire query uncovered"],
                "new_terms": [],
                "covered_aspects": [],
            }
        # Cap source text to fit context budget
        budget = usable_budget_chars(self._valves.max_prompt_tokens)
        anchor_overhead = len(session.anchor) + 500
        source_budget = budget - anchor_overhead
        capped_texts = []
        used = 0
        for t in texts:
            if used + len(t) > source_budget and capped_texts:
                break
            capped_texts.append(t)
            used += len(t)
        try:
            return await self._sub_agent.run_json(
                system_prompt=_ANALYSIS_SYSTEM_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Sources ({len(capped_texts)}/{len(texts)}):\n\n"
                    + "\n\n---\n\n".join(capped_texts)
                ),
                request=request,
                user=user,
            )
        except Exception:
            return {
                "summary": f"Found {len(texts)} sources.",
                "gaps": [],
                "new_terms": [],
                "covered_aspects": [],
            }

    @staticmethod
    async def _emit_status(
        event_emitter: Optional[Callable],
        message: str,
        done: bool = False,
    ) -> None:
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {"description": message, "done": done},
                }
            )
