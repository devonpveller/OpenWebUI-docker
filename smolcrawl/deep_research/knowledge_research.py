"""
Knowledge-focused iterative RAG research using existing OWUI collections.

Identifies relevant knowledge collections, iteratively queries them with
expanding search terms, and falls back to web search for source
recommendations when local knowledge is exhausted.

Single Responsibility: Orchestrates RAG-only research across existing collections.
Dependency Inversion: Composes RagResearcher, SubAgent, Journal, Synthesizer.
"""

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

from .journal import ResearchJournal
from .models import (
    IterationResult,
    ResearchPhase,
    ResearchSession,
    RetrievedChunk,
    Valves,
)
from .rag_research import RagResearcher
from .sub_agent import SubAgent, extract_anchor
from .synthesis import Synthesizer
from .context_budget import condense_iterations

logger = logging.getLogger("deep_research.knowledge_research")

# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_COLLECTION_RELEVANCE_PROMPT = """\
You are evaluating which existing knowledge collections are relevant to a \
research query. For each collection, assess whether its content would likely \
contain information useful.

Given:
- A RESEARCH ANCHOR describing what the user needs
- A list of available knowledge collections with names, descriptions, \
and file counts

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

_GAP_ANALYSIS_PROMPT = """\
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

_SOURCE_RECOMMENDATION_PROMPT = """\
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


class KnowledgeResearcher:
    """Iterative RAG-only research across existing OWUI knowledge collections.

    Flow:
    1. Extract research anchor from query
    2. Discover and rank relevant knowledge collections
    3. Iterative RAG with term expansion and stale-iteration tracking
    4. Gap analysis — determine what's covered vs uncovered
    5. If gaps remain after stale iterations → web search for source recs
    6. Synthesize findings with verification pipeline
    7. Persist journal to Fileshed (knowledge-research/ namespace)

    Unlike deep_research(), this tool never triggers crawling. It works
    exclusively with existing knowledge and recommends sources when gaps
    are detected.
    """

    MAX_STALE_ITERATIONS = 3
    SMALL_COLLECTION_FILE_THRESHOLD = 5
    SMALL_COLLECTION_K_MULTIPLIER = 3

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
        self._rag = RagResearcher(valves, sub_agent)

    async def run(
        self,
        query: str,
        user_id: str,
        request: Any,
        user: Dict,
        model_id: str,
        event_emitter: Optional[Callable] = None,
        target_collection: str = "",
    ) -> str:
        """Execute knowledge research pipeline.

        Args:
            query: The user's research question.
            user_id: OWUI user ID for Fileshed path scoping.
            request: OWUI __request__ object.
            user: OWUI __user__ dict.
            model_id: The active LLM model ID.
            event_emitter: OWUI event emitter for status updates.
            target_collection: Optional collection name to query directly.
                When specified, skips LLM-based collection ranking and
                uses this collection exclusively.

        Returns:
            Synthesized answer with source references and recommendations.
        """
        # Initialize session
        slug = ResearchJournal.slugify(query)
        session_dir = self._journal.resolve_session_dir(
            user_id, slug, namespace="knowledge-research"
        )
        session = ResearchSession(
            session_id=f"kr-{slug}",
            query=query,
            session_dir=session_dir,
            phase=ResearchPhase.INITIALIZING,
            model_id=model_id,
        )
        self._journal.write_prompt(session, model_id)
        await self._emit(event_emitter, "📋 Knowledge research started")

        # --- Step 1: Extract anchor ---
        anchor_result = await extract_anchor(
            self._sub_agent, query, request, user
        )
        session.anchor, initial_terms = anchor_result
        self._journal.write_anchor(session)
        await self._emit(event_emitter, "🎯 Research anchor extracted")

        # --- Step 2: Discover relevant collections ---
        session.phase = ResearchPhase.DISCOVERING
        if target_collection:
            await self._emit(
                event_emitter,
                f"📌 Targeting collection: {target_collection}",
            )

        all_collections, api_error = await self._rag.list_collections(request)

        if api_error:
            await self._emit(
                event_emitter,
                f"❌ OWUI API error: {api_error}",
            )
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
            self._journal.write_synthesis(session, answer)
            self._journal.write_manifest(session)
            await self._emit(
                event_emitter,
                f"📁 Journal: knowledge-research/{slug}/",
                done=True,
            )
            return answer

        if not all_collections:
            await self._emit(
                event_emitter,
                "⚠️ No knowledge collections found — searching for source recommendations",
            )
            recommendations = await self._recommend_sources(
                session, ["No existing knowledge collections"],
                initial_terms, request, user, event_emitter,
            )
            session.phase = ResearchPhase.COMPLETE
            answer = self._build_empty_response(session, recommendations)
            self._journal.write_synthesis(session, answer)
            self._journal.write_manifest(session)
            await self._emit(
                event_emitter,
                f"📁 Journal: knowledge-research/{slug}/",
                done=True,
            )
            return answer

        # User-specified collection or LLM-ranked selection
        if target_collection:
            relevant = self._find_collection_by_name(
                target_collection, all_collections
            )
            if not relevant:
                available = ", ".join(
                    f"'{c['name']}'" for c in all_collections[:15]
                )
                await self._emit(
                    event_emitter,
                    f"⚠️ Collection '{target_collection}' not found",
                )
                session.phase = ResearchPhase.COMPLETE
                answer = (
                    f"# Collection Not Found\n\n"
                    f"No collection matching **{target_collection}** was found.\n\n"
                    f"**Available collections:** {available}\n\n"
                    f"*Specify one of the above names, or omit the "
                    f"collection parameter to auto-select.*\n"
                )
                self._journal.write_synthesis(session, answer)
                self._journal.write_manifest(session)
                await self._emit(
                    event_emitter,
                    f"📁 Journal: knowledge-research/{slug}/",
                    done=True,
                )
                return answer
            await self._emit(
                event_emitter,
                f"📌 Using specified collection: {relevant[0]['name']}",
            )
        else:
            relevant = await self._rank_collections(
                session, all_collections, request, user
            )

        if not relevant:
            await self._emit(
                event_emitter,
                "⚠️ No relevant collections identified — searching for source recommendations",
            )
            recommendations = await self._recommend_sources(
                session,
                [f"No collections relevant to: {query}"],
                initial_terms, request, user, event_emitter,
            )
            session.phase = ResearchPhase.COMPLETE
            answer = self._build_empty_response(session, recommendations)
            self._journal.write_synthesis(session, answer)
            self._journal.write_manifest(session)
            await self._emit(
                event_emitter,
                f"📁 Journal: knowledge-research/{slug}/",
                done=True,
            )
            return answer

        collection_ids = [r["id"] for r in relevant]
        collection_map = {r["id"]: r["name"] for r in relevant}
        session.relevant_collection_ids = collection_ids

        # Build file-level query targets — OWUI stores vector embeddings
        # per-file, so we must query each file_id individually.
        file_ids_map: Dict[str, List[str]] = {}
        for r in relevant:
            fids = r.get("data", {}).get("file_ids", [])
            if fids:
                file_ids_map[r["id"]] = fids

        # Compute adaptive k based on collection sizes
        effective_k = self._compute_adaptive_k(relevant)

        self._journal.write_entry(
            session.session_dir,
            "01-collections.md",
            self._format_collections_journal(relevant, all_collections),
        )

        total_files = sum(
            len(c.get("data", {}).get("file_ids", []))
            for c in relevant
        )
        k_label = (
            f"deep retrieval (k={effective_k})"
            if effective_k > self._valves.top_k_per_collection
            else f"standard RAG (k={effective_k})"
        )
        await self._emit(
            event_emitter,
            f"📚 {len(relevant)}/{len(all_collections)} collection(s) "
            f"selected ({total_files} files) — {k_label}",
        )

        # --- Step 3: Iterative RAG ---
        session.phase = ResearchPhase.RESEARCHING
        search_terms = initial_terms
        consecutive_stale = 0

        for iter_num in range(1, self._valves.max_iterations + 1):
            await self._emit(
                event_emitter,
                f"🔍 Iteration {iter_num}: querying "
                f"{len(collection_ids)} collection(s)...",
            )

            iteration = await self._rag.run_iteration(
                session=session,
                search_terms=search_terms,
                collection_ids=collection_ids,
                collection_names=collection_map,
                iteration_number=iter_num,
                request=request,
                user=user,
                k_override=effective_k,
                file_ids_map=file_ids_map,
            )
            self._journal.write_iteration(session, iteration)

            # Track stale iterations (no new chunks)
            if iteration.new_chunks == 0:
                consecutive_stale += 1
            else:
                consecutive_stale = 0

            stale_note = (
                f" (stale: {consecutive_stale}/{self.MAX_STALE_ITERATIONS})"
                if consecutive_stale > 0 else ""
            )
            await self._emit(
                event_emitter,
                f"📚 Iter {iter_num}: {iteration.new_chunks} new chunk(s)"
                + stale_note,
            )

            # Check stale threshold — knowledge exhausted
            if consecutive_stale >= self.MAX_STALE_ITERATIONS:
                await self._emit(
                    event_emitter,
                    f"⚠️ {self.MAX_STALE_ITERATIONS} stale iterations "
                    f"— analyzing gaps",
                )
                break

            # Expand terms for next iteration
            search_terms = await self._rag.expand_terms(
                session, search_terms, request, user
            )

            # Continue decision after guaranteed iterations
            if iter_num >= self._valves.fixed_iterations:
                if iter_num >= self._valves.max_iterations:
                    break
                should = await self._rag.should_continue(
                    session, request, user
                )
                if not should:
                    await self._emit(
                        event_emitter,
                        "✅ Knowledge sufficiently explored",
                    )
                    break

        # --- Step 4: Gap analysis ---
        gap_analysis = await self._analyze_gaps(session, request, user)
        gaps = gap_analysis.get("gaps", [])
        exhausted = gap_analysis.get("exhausted", False)
        external_topics = gap_analysis.get("external_topics", [])

        gap_file_num = len(session.iterations) + 3
        self._journal.write_entry(
            session.session_dir,
            f"{gap_file_num:02d}-gap-analysis.md",
            self._format_gap_journal(gap_analysis),
        )

        if gaps:
            await self._emit(
                event_emitter,
                f"🔎 Gaps identified: {', '.join(gaps[:3])}",
            )

        # --- Step 5: Web search recommendations (if gaps + exhausted) ---
        recommendations = None
        if gaps and (exhausted or consecutive_stale >= self.MAX_STALE_ITERATIONS):
            await self._emit(
                event_emitter,
                f"🌐 Searching for sources to fill {len(gaps)} gap(s)...",
            )
            gap_terms = gap_analysis.get("gap_search_terms", [])
            if not gap_terms:
                gap_terms = external_topics or gaps
            recommendations = await self._recommend_sources(
                session, gaps, gap_terms, request, user, event_emitter
            )
            rec_count = len(
                (recommendations or {}).get("recommendations", [])
            )
            if rec_count:
                rec_file_num = gap_file_num + 1
                self._journal.write_entry(
                    session.session_dir,
                    f"{rec_file_num:02d}-recommendations.md",
                    self._format_recommendations_journal(recommendations),
                )
                await self._emit(
                    event_emitter,
                    f"📡 Found {rec_count} source recommendation(s)",
                )

        # --- Step 6: Synthesis + Validation ---
        session.phase = ResearchPhase.SYNTHESIZING
        await self._emit(event_emitter, "🧠 Synthesizing findings...")

        # Build source entries from collections for the synthesizer
        rag_sources = self._build_rag_sources(session, collection_map)

        await self._emit(
            event_emitter,
            "🔒 Starting validation pipeline (URL check → credibility → remediation)...",
        )
        answer = await self._synthesizer.synthesize(
            session, request, user,
            relevant_sources=rag_sources,
            event_emitter=event_emitter,
        )

        # Append recommendations section
        if recommendations:
            answer += self._format_recommendations_section(recommendations)

        session.phase = ResearchPhase.COMPLETE
        self._journal.write_manifest(session)

        await self._emit(
            event_emitter,
            f"📁 Journal: knowledge-research/{slug}/",
            done=True,
        )

        return answer

    # ------------------------------------------------------------------
    # Collection selection helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_collection_by_name(
        name: str,
        collections: List[Dict],
    ) -> List[Dict]:
        """Find a collection by name (case-insensitive, with partial fallback).

        Matching priority:
        1. Exact match (case-insensitive)
        2. Substring match — collection name contains the target or vice versa

        Returns a single-element list for consistency with _rank_collections.
        """
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

    def _compute_adaptive_k(self, collections: List[Dict]) -> int:
        """Compute effective top-k based on total collection size.

        Small collections (few files) get a higher k to retrieve more
        of their content. Large collections use the configured default.
        """
        total_files = sum(
            len(c.get("data", {}).get("file_ids", []))
            for c in collections
        )
        base_k = self._valves.top_k_per_collection
        if total_files <= self.SMALL_COLLECTION_FILE_THRESHOLD:
            return base_k * self.SMALL_COLLECTION_K_MULTIPLIER
        return base_k

    # ------------------------------------------------------------------
    # Collection ranking
    # ------------------------------------------------------------------

    async def _rank_collections(
        self,
        session: ResearchSession,
        collections: List[Dict],
        request: Any,
        user: Dict,
    ) -> List[Dict]:
        """Use LLM to rank existing collections by relevance to the query."""
        summaries = "\n".join(
            f"- ID: {c['id']} | Name: {c['name']} | "
            f"Description: {c.get('description', 'None')} | "
            f"Files: {len(c.get('data', {}).get('file_ids', []))}"
            for c in collections
        )

        try:
            result = await self._sub_agent.run_json(
                system_prompt=_COLLECTION_RELEVANCE_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Available collections:\n{summaries}"
                ),
                request=request,
                user=user,
            )
        except (ValueError, Exception) as e:
            logger.error("Collection ranking failed: %s", e)
            return []

        relevant_entries = result.get("relevant", [])
        strategy = result.get("strategy", "")
        if strategy:
            logger.info("Collection strategy: %s", strategy)

        # Map back to full collection dicts with ranking metadata
        valid_ids = {c["id"]: c for c in collections}
        ranked = []
        for entry in relevant_entries:
            col_id = entry.get("id", "")
            if col_id in valid_ids:
                col = valid_ids[col_id].copy()
                col["_relevance"] = entry.get("relevance", "medium")
                col["_rationale"] = entry.get("rationale", "")
                ranked.append(col)

        return ranked[: self._valves.max_collections]

    # ------------------------------------------------------------------
    # Gap analysis
    # ------------------------------------------------------------------

    async def _analyze_gaps(
        self,
        session: ResearchSession,
        request: Any,
        user: Dict,
    ) -> Dict:
        """Analyze what aspects are covered vs uncovered after RAG iterations."""
        iteration_context = condense_iterations(session.iterations)

        try:
            return await self._sub_agent.run_json(
                system_prompt=_GAP_ANALYSIS_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Collections searched: "
                    f"{', '.join(session.relevant_collection_ids)}\n\n"
                    f"Iteration results:\n{iteration_context}"
                ),
                request=request,
                user=user,
            )
        except (ValueError, Exception) as e:
            logger.error("Gap analysis failed: %s", e)
            return {
                "covered": [],
                "gaps": ["analysis failed"],
                "exhausted": True,
                "gap_search_terms": [],
                "external_topics": [],
                "confidence": "low",
            }

    # ------------------------------------------------------------------
    # Source recommendations (web search fallback)
    # ------------------------------------------------------------------

    async def _recommend_sources(
        self,
        session: ResearchSession,
        gaps: List[str],
        search_terms: List[str],
        request: Any,
        user: Dict,
        event_emitter: Optional[Callable] = None,
    ) -> Dict:
        """Search the web for sources that could fill knowledge gaps."""
        from open_webui.routers.retrieval import search_web
        from starlette.concurrency import run_in_threadpool

        engine = getattr(
            request.app.state.config, "WEB_SEARCH_ENGINE", ""
        )
        if not engine:
            logger.warning(
                "No WEB_SEARCH_ENGINE configured — cannot recommend sources"
            )
            return {
                "recommendations": [],
                "crawl_suggestion": (
                    "No web search engine configured. "
                    "Configure one in OWUI admin to enable source discovery."
                ),
            }

        # Search for each gap term — terms are independent, run concurrently.
        terms = search_terms[:5]

        async def _search(term):
            try:
                return await run_in_threadpool(
                    search_web, request, engine, term
                )
            except Exception as e:
                logger.warning("Web search for '%s' failed: %s", term, e)
                return []

        batches = await asyncio.gather(
            *[_search(t) for t in terms], return_exceptions=True
        )
        all_results = []
        seen_urls: set = set()
        for results in batches:
            if not isinstance(results, list):
                continue
            for r in results:
                if r.link not in seen_urls:
                    seen_urls.add(r.link)
                    all_results.append(r)

        if not all_results:
            return {
                "recommendations": [],
                "crawl_suggestion": (
                    "Web search returned no results for gap topics."
                ),
            }

        # Format for LLM ranking
        listing = "\n".join(
            f"- {r.link} | {r.title or '(no title)'} | "
            f"{r.snippet or '(no snippet)'}"
            for r in all_results[:20]
        )
        gap_text = "\n".join(f"- {g}" for g in gaps)

        try:
            return await self._sub_agent.run_json(
                system_prompt=_SOURCE_RECOMMENDATION_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Knowledge gaps:\n{gap_text}\n\n"
                    f"Web search results:\n{listing}"
                ),
                request=request,
                user=user,
            )
        except (ValueError, Exception) as e:
            logger.error("Source recommendation failed: %s", e)
            # Fall back to raw results
            recs = []
            seen_domains: set = set()
            for r in all_results[:5]:
                try:
                    domain = urlparse(r.link).netloc
                except Exception:
                    continue
                if domain not in seen_domains:
                    seen_domains.add(domain)
                    recs.append({
                        "url": r.link,
                        "domain": domain,
                        "title": r.title or "",
                        "rationale": r.snippet or "",
                        "gap_addressed": "general",
                        "priority": "medium",
                    })
            return {
                "recommendations": recs,
                "crawl_suggestion": (
                    "Consider crawling these domains to build "
                    "knowledge collections."
                ),
            }

    # ------------------------------------------------------------------
    # Source building for synthesis
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rag_sources(
        session: ResearchSession,
        collection_map: Dict[str, str],
    ) -> List[Dict]:
        """Build source entries from RAG iteration data for the synthesizer.

        Creates one source per collection with a combined summary
        drawn from all iterations that queried it.
        """
        # Aggregate iteration summaries by collection
        col_summaries: Dict[str, List[str]] = {}
        for iteration in session.iterations:
            for col_name in iteration.collections_queried:
                col_summaries.setdefault(col_name, []).append(
                    iteration.summary or f"Iteration {iteration.iteration_number}"
                )

        sources = []
        for col_id, col_name in collection_map.items():
            summaries = col_summaries.get(col_name, [])
            combined = " | ".join(s[:200] for s in summaries[:3])
            sources.append({
                "title": col_name,
                "url": f"knowledge-collection://{col_id}",
                "domain": col_name,
                "summary": combined or "Queried but no summary available",
            })

        return sources

    # ------------------------------------------------------------------
    # Journal formatting
    # ------------------------------------------------------------------

    @staticmethod
    def _format_collections_journal(
        relevant: List[Dict],
        all_collections: List[Dict],
    ) -> str:
        """Format collection selection for journal entry."""
        lines = [
            "# Knowledge Collections\n",
            f"**Total available:** {len(all_collections)}\n",
            f"**Selected as relevant:** {len(relevant)}\n",
            "\n## Selected Collections\n",
        ]
        for col in relevant:
            file_count = len(col.get("data", {}).get("file_ids", []))
            relevance = col.get("_relevance", "unknown")
            rationale = col.get("_rationale", "")
            lines.append(
                f"- **{col['name']}** [{relevance}] ({file_count} files)\n"
                f"  {rationale}\n"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_gap_journal(analysis: Dict) -> str:
        """Format gap analysis for journal entry."""
        lines = ["# Gap Analysis\n"]

        covered = analysis.get("covered", [])
        if covered:
            lines.append("## Well Covered\n")
            lines.extend(f"- {c}\n" for c in covered)

        gaps = analysis.get("gaps", [])
        if gaps:
            lines.append("\n## Gaps\n")
            lines.extend(f"- {g}\n" for g in gaps)

        external = analysis.get("external_topics", [])
        if external:
            lines.append("\n## Needs External Sources\n")
            lines.extend(f"- {t}\n" for t in external)

        exhausted = analysis.get("exhausted", False)
        confidence = analysis.get("confidence", "unknown")
        lines.append(
            f"\n**Exhausted:** {exhausted}\n"
            f"**Confidence:** {confidence}\n"
        )
        return "\n".join(lines)

    @staticmethod
    def _format_recommendations_journal(recommendations: Dict) -> str:
        """Format source recommendations for journal entry."""
        recs = recommendations.get("recommendations", [])
        suggestion = recommendations.get("crawl_suggestion", "")
        lines = ["# Source Recommendations\n"]

        if suggestion:
            lines.append(f"{suggestion}\n")

        if recs:
            lines.append("\n## Recommended Sources\n")
            for i, r in enumerate(recs, 1):
                lines.append(
                    f"{i}. **{r.get('title', r.get('domain', 'Unknown'))}** "
                    f"[{r.get('priority', 'medium')}]\n"
                    f"   URL: {r.get('url', '')}\n"
                    f"   Gap: {r.get('gap_addressed', '')}\n"
                    f"   Rationale: {r.get('rationale', '')}\n"
                )

        return "\n".join(lines)

    @staticmethod
    def _format_recommendations_section(recommendations: Dict) -> str:
        """Format source recommendations as markdown appended to synthesis."""
        recs = recommendations.get("recommendations", [])
        suggestion = recommendations.get("crawl_suggestion", "")

        if not recs and not suggestion:
            return ""

        lines = [
            "\n\n---\n",
            "## 📡 Recommended Sources for Knowledge Building\n",
        ]

        if suggestion:
            lines.append(f"{suggestion}\n")

        if recs:
            lines.append(
                "\n| Priority | Domain | Gap Addressed | Rationale |"
            )
            lines.append(
                "|----------|--------|---------------|-----------|"
            )
            for r in recs:
                priority = r.get("priority", "medium")
                domain = r.get("domain", "")
                url = r.get("url", "")
                gap = r.get("gap_addressed", "")
                rationale = r.get("rationale", "")[:100]
                lines.append(
                    f"| {priority} | [{domain}]({url}) "
                    f"| {gap} | {rationale} |"
                )

        lines.append(
            "\n*💡 Use `deep_research()` to automatically crawl these "
            "sources into knowledge collections, or manually trigger a "
            "crawl in the SmolCrawl pipeline.*\n"
        )

        return "\n".join(lines)

    @staticmethod
    def _build_empty_response(
        session: ResearchSession,
        recommendations: Dict,
    ) -> str:
        """Build response when no relevant collections exist."""
        lines = [
            f"# Knowledge Research: {session.query}\n",
            "No existing knowledge collections were found relevant to "
            "this query.\n",
        ]

        suggestion = recommendations.get("crawl_suggestion", "")
        if suggestion:
            lines.append(f"\n{suggestion}\n")

        recs = recommendations.get("recommendations", [])
        if recs:
            lines.append("\n## Recommended Sources to Crawl\n")
            for r in recs:
                lines.append(
                    f"- **[{r.get('domain', '')}]({r.get('url', '')})** "
                    f"[{r.get('priority', 'medium')}]\n"
                    f"  {r.get('rationale', '')}\n"
                    f"  Gap: {r.get('gap_addressed', '')}\n"
                )

        lines.append(
            "\n*Use `deep_research()` to crawl these domains into "
            "knowledge collections, then re-run `knowledge_research()` "
            "to query them.*\n"
        )

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit(
        emitter: Optional[Callable],
        message: str,
        done: bool = False,
    ) -> None:
        """Emit a status update through OWUI's event emitter."""
        if emitter:
            await emitter({
                "type": "status",
                "data": {"description": message, "done": done},
            })
