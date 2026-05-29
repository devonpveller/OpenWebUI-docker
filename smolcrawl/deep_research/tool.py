"""
title: Deep Research
author: smolcrawl
date: 2026-04-12
version: 1.0
license: MIT
description: Iterative RAG research with LLM-guided domain discovery, web search exploration, and chain-of-thought synthesis. Provides research() for quick exploration and deep_research() for full knowledge building.
requirements: httpx, pydantic
"""

import logging
import uuid
from typing import Any, Callable, Dict, Optional

from .crawl_integration import CrawlClient
from .journal import ResearchJournal
from .knowledge_research import KnowledgeResearcher
from .models import (
    ResearchPhase,
    ResearchSession,
    Valves,
)
from .chat_ledger import (
    CONTINUE_RE,
    RESEARCH_LEDGER,
    chat_key,
    dedup,
    stop_payload,
)
from .rag_research import RagResearcher
from .research import QuickResearcher
from .sub_agent import SubAgent, extract_anchor
from .synthesis import Synthesizer

logger = logging.getLogger("deep_research")


class Tools:
    """Deep Research Tools for Open WebUI.

    Three tool methods:
    - research(query): Quick web-search-based exploration
    - knowledge_research(query): Iterative RAG across existing knowledge collections
    - deep_research(query): Full pipeline — discover, crawl, RAG, synthesize

    Designed as an OWUI Tool (class Tools) that runs inside the user's
    selected LLM context with native function calling.
    """

    class Valves(Valves):
        """Re-export Valves at the class level for OWUI discovery."""
        pass

    def __init__(self):
        self.valves = self.Valves()

    # --- Public Tool Methods ---

    async def research(
        self,
        query: str,
        __user__: dict = None,
        __metadata__: dict = None,
        __event_emitter__=None,
        __request__=None,
        __model__: dict = None,
        __event_call__=None,
        __chat_id__: str = "",
        __message_id__: str = "",
    ) -> str:
        """Quick research on a topic using web search.

        Stores findings to Fileshed and iteratively expands search terms
        internally. Faster than deep_research — use this to scope a topic.

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
        """
        model_id = SubAgent.resolve_model_id(__metadata__, __model__)
        user_id = (__user__ or {}).get("id", "")

        key = chat_key(__chat_id__, __user__)
        ledger = RESEARCH_LEDGER.setdefault(
            key, {"count": 0, "covered": [], "gaps": [], "topic": ""}
        )
        budget = self.valves.max_research_calls_per_chat

        # Explicit user-driven continuation: resets the per-chat budget,
        # retains covered markers so we don't re-research them.
        cont = CONTINUE_RE.match(query or "")
        if cont:
            query = (query[cont.end():].strip()
                     or ledger.get("topic") or query)
            ledger["count"] = 0
            ledger["gaps"] = []
            await self._emit_status(
                __event_emitter__,
                "🔁 Resuming research — prior coverage retained",
            )

        if not ledger.get("topic"):
            ledger["topic"] = query
        topic = ledger.get("topic") or query

        # Hard stop: budget already spent and this is not a continuation.
        if ledger["count"] >= budget:
            await self._emit_status(
                __event_emitter__,
                f"⛔ Research budget reached ({ledger['count']}/{budget}) "
                "— returning coverage summary",
                done=True,
            )
            return stop_payload(
                ledger, topic, ledger["count"], budget, ran=False
            )

        # Bias the run away from already-covered ground.
        effective_query = query
        covered = dedup(ledger.get("covered", []), 8)
        focus = dedup(ledger.get("gaps", []), 5)
        if covered or focus:
            hint = []
            if covered:
                hint.append("Already researched (do not repeat): "
                            + "; ".join(covered))
            if focus:
                hint.append("Prioritize these open gaps: " + "; ".join(focus))
            effective_query = f"{query}\n\n({' | '.join(hint)})"

        sub_agent = SubAgent(
            model_id, self.valves.max_prompt_tokens,
            nothink_suffix=(self.valves.nothink_suffix
                            if self.valves.sub_agent_nothink else ""),
        )
        journal = ResearchJournal(self.valves)
        researcher = QuickResearcher(self.valves, sub_agent, journal)

        answer = await researcher.run(
            query=effective_query,
            user_id=user_id,
            request=__request__,
            user=__user__ or {},
            model_id=model_id,
            event_emitter=__event_emitter__,
        )

        ledger["count"] += 1
        ledger["covered"] = dedup(
            ledger["covered"] + (researcher.last_covered or [])
        )
        ledger["gaps"] = dedup([
            g for g in (ledger["gaps"] + (researcher.last_gaps or []))
            if g not in ledger["covered"]
        ])

        used = ledger["count"]
        if used >= budget:
            answer += "\n\n" + stop_payload(
                ledger, topic, used, budget, ran=True
            )
        elif used == budget - 1:
            answer += (
                f"\n\n---\n\n*ℹ️ {used}/{budget} research calls used this "
                "conversation. One remains before a stop directive — make it "
                "count, or finalize now.*"
            )
        return answer

    async def knowledge_research(
        self,
        query: str,
        collection: str = "",
        __user__: dict = None,
        __metadata__: dict = None,
        __event_emitter__=None,
        __request__=None,
        __model__: dict = None,
        __event_call__=None,
        __chat_id__: str = "",
        __message_id__: str = "",
    ) -> str:
        """Research a topic using existing knowledge collections.

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
        """
        model_id = SubAgent.resolve_model_id(__metadata__, __model__)
        user_id = (__user__ or {}).get("id", "")

        sub_agent = SubAgent(
            model_id, self.valves.max_prompt_tokens,
            nothink_suffix=(self.valves.nothink_suffix
                            if self.valves.sub_agent_nothink else ""),
        )
        journal = ResearchJournal(self.valves)
        researcher = KnowledgeResearcher(self.valves, sub_agent, journal)

        return await researcher.run(
            query=query,
            user_id=user_id,
            request=__request__,
            user=__user__ or {},
            model_id=model_id,
            event_emitter=__event_emitter__,
            target_collection=collection,
        )

    async def deep_research(
        self,
        query: str,
        __user__: dict = None,
        __metadata__: dict = None,
        __event_emitter__=None,
        __request__=None,
        __model__: dict = None,
        __event_call__=None,
        __chat_id__: str = "",
        __message_id__: str = "",
    ) -> str:
        """Deep research on a topic — the full hybrid pipeline.

        Starts by querying existing knowledge collections (knowledge_research).
        If the knowledge base cannot answer the query, identifies sources via
        web search (research), crawls them into new collections, then queries
        the expanded knowledge base again before synthesising.

        Pipeline: knowledge_research → gap analysis → web search →
        crawl → knowledge_research → synthesize → verify.

        Args:
            query: The research question or topic to investigate.
        """
        model_id = SubAgent.resolve_model_id(__metadata__, __model__)
        user_id = (__user__ or {}).get("id", "")

        sub_agent = SubAgent(
            model_id, self.valves.max_prompt_tokens,
            nothink_suffix=(self.valves.nothink_suffix
                            if self.valves.sub_agent_nothink else ""),
        )
        journal = ResearchJournal(self.valves)
        rag = RagResearcher(self.valves, sub_agent)
        crawl_client = CrawlClient(self.valves)
        synthesizer = Synthesizer(self.valves, sub_agent, journal)

        # Initialize session
        slug = ResearchJournal.slugify(query)
        session_dir = journal.resolve_session_dir(user_id, slug)
        session = ResearchSession(
            session_id=str(uuid.uuid4()),
            query=query,
            session_dir=session_dir,
            model_id=model_id,
        )
        journal.write_prompt(session, model_id)

        await self._emit_status(
            __event_emitter__, "📋 Deep research started"
        )

        # Extract anchor once — threads through all subsequent prompts
        anchor_result = await extract_anchor(
            sub_agent, query, __request__, __user__ or {}
        )
        session.anchor, initial_terms = anchor_result
        journal.write_anchor(session)
        await self._emit_status(
            __event_emitter__, "🎯 Research anchor extracted"
        )

        # =============================================================
        #  Phase 1: Knowledge Research — query existing collections
        # =============================================================
        session.phase = ResearchPhase.DISCOVERING
        all_collections, _ = await rag.list_collections(__request__)

        # Rank existing collections
        kr = KnowledgeResearcher(self.valves, sub_agent, journal)
        relevant = await kr._rank_collections(
            session, all_collections, __request__, __user__ or {}
        )
        collection_ids = [r["id"] for r in relevant]
        collection_map = {r["id"]: r["name"] for r in relevant}
        session.relevant_collection_ids = list(collection_ids)

        # Build file-level query targets (OWUI stores embeddings per-file)
        file_ids_map = {}
        for r in relevant:
            fids = r.get("data", {}).get("file_ids", [])
            if fids:
                file_ids_map[r["id"]] = fids

        await self._emit_status(
            __event_emitter__,
            f"📚 {len(relevant)}/{len(all_collections)} collection(s) relevant",
        )

        # Iterative RAG pass 1 — explore existing knowledge
        has_existing_knowledge = bool(collection_ids)
        consecutive_stale = 0
        search_terms = initial_terms

        if has_existing_knowledge:
            session.phase = ResearchPhase.RESEARCHING
            await self._emit_status(
                __event_emitter__,
                "🔍 Phase 1: Querying existing knowledge...",
            )

            for iter_num in range(1, self.valves.max_iterations + 1):
                await self._emit_status(
                    __event_emitter__,
                    f"🔍 KR iter {iter_num}: querying "
                    f"{len(collection_ids)} collection(s)...",
                )

                iteration = await rag.run_iteration(
                    session=session,
                    search_terms=search_terms,
                    collection_ids=collection_ids,
                    collection_names=collection_map,
                    iteration_number=iter_num,
                    request=__request__,
                    user=__user__ or {},
                    file_ids_map=file_ids_map,
                )
                journal.write_iteration(session, iteration)

                if iteration.new_chunks == 0:
                    consecutive_stale += 1
                else:
                    consecutive_stale = 0

                stale_note = (
                    f" (stale: {consecutive_stale}/3)"
                    if consecutive_stale > 0 else ""
                )
                await self._emit_status(
                    __event_emitter__,
                    f"📚 KR iter {iter_num}: {iteration.new_chunks} "
                    f"new chunk(s){stale_note}",
                )

                if consecutive_stale >= 3:
                    await self._emit_status(
                        __event_emitter__,
                        "⚠️ Existing knowledge exhausted — analyzing gaps",
                    )
                    break

                search_terms = await rag.expand_terms(
                    session, search_terms, __request__, __user__ or {}
                )

                if iter_num >= self.valves.fixed_iterations:
                    if iter_num >= self.valves.max_iterations:
                        break
                    if not await rag.should_continue(
                        session, __request__, __user__ or {}
                    ):
                        await self._emit_status(
                            __event_emitter__,
                            "✅ Phase 1 complete — checking for gaps",
                        )
                        break

        # =============================================================
        #  Phase 2: Gap analysis — decide if we need external sources
        # =============================================================
        gap_analysis = await kr._analyze_gaps(
            session, __request__, __user__ or {}
        )
        gaps = gap_analysis.get("gaps", [])
        exhausted = gap_analysis.get("exhausted", not has_existing_knowledge)
        external_topics = gap_analysis.get("external_topics", [])

        gap_file_num = len(session.iterations) + 3
        journal.write_entry(
            session.session_dir,
            f"{gap_file_num:02d}-gap-analysis.md",
            kr._format_gap_journal(gap_analysis),
        )

        needs_external = (
            not has_existing_knowledge
            or (gaps and (exhausted or consecutive_stale >= 3))
        )

        if gaps:
            await self._emit_status(
                __event_emitter__,
                f"🔎 Gaps: {', '.join(gaps[:3])}"
                + (" — searching the web" if needs_external else ""),
            )

        # =============================================================
        #  Phase 3: Web search → identify authoritative sources
        # =============================================================
        discovered_sources = []
        if needs_external:
            await self._emit_status(
                __event_emitter__,
                "🌐 Phase 2: Searching for authoritative sources...",
            )

            gap_terms = gap_analysis.get("gap_search_terms", [])
            if not gap_terms:
                gap_terms = external_topics or gaps or initial_terms

            recommendations = await kr._recommend_sources(
                session, gaps, gap_terms,
                __request__, __user__ or {}, __event_emitter__,
            )
            discovered_sources = recommendations.get("recommendations", [])

            if discovered_sources:
                rec_file_num = gap_file_num + 1
                journal.write_entry(
                    session.session_dir,
                    f"{rec_file_num:02d}-source-discovery.md",
                    kr._format_recommendations_journal(recommendations),
                )
                await self._emit_status(
                    __event_emitter__,
                    f"📡 Found {len(discovered_sources)} source(s) to crawl",
                )

        # =============================================================
        #  Phase 4: Crawl discovered sources into knowledge collections
        # =============================================================
        if discovered_sources:
            session.phase = ResearchPhase.CRAWLING

            # Deduplicate by domain
            seen_domains: set = set()
            crawl_targets = []
            for src in discovered_sources:
                domain = src.get("domain", "")
                if domain and domain not in seen_domains:
                    seen_domains.add(domain)
                    crawl_targets.append(src)

            names = ", ".join(s.get("domain", "") for s in crawl_targets[:5])
            await self._emit_status(
                __event_emitter__,
                f"🕷️ Crawling {len(crawl_targets)} domain(s): {names}",
            )

            for src in crawl_targets[:self.valves.max_domains]:
                domain = src.get("domain", "")
                kb_name = f"SmolCrawl - {domain}"
                result = await crawl_client.trigger_crawl_streaming(
                    domain=domain,
                    kb_name=kb_name,
                    event_emitter=__event_emitter__,
                )
                session.crawl_results.append(result)

                if result.success and result.kb_id:
                    session.relevant_collection_ids.append(result.kb_id)

            journal.write_crawl_status(session)

            successful = sum(1 for r in session.crawl_results if r.success)
            await self._emit_status(
                __event_emitter__,
                f"✅ Crawled {successful}/{len(crawl_targets)} domain(s)",
            )

            # Refresh collection list to pick up newly created KBs
            all_collections, _ = await rag.list_collections(__request__)
            collection_map = {c["id"]: c["name"] for c in all_collections}

            # Refresh file_ids_map with newly crawled collections
            file_ids_map = {}
            for col in all_collections:
                fids = col.get("data", {}).get("file_ids", [])
                if fids:
                    file_ids_map[col["id"]] = fids

            for result in session.crawl_results:
                if result.success:
                    for col in all_collections:
                        if col["name"] == result.kb_name:
                            if col["id"] not in session.relevant_collection_ids:
                                session.relevant_collection_ids.append(
                                    col["id"]
                                )
                            result.kb_id = col["id"]
                            break

        # =============================================================
        #  Phase 5: Knowledge Research pass 2 — query expanded KBs
        # =============================================================
        new_collection_ids = [
            cid for cid in session.relevant_collection_ids
            if cid not in collection_ids
        ]

        if new_collection_ids or (discovered_sources and not has_existing_knowledge):
            session.phase = ResearchPhase.RESEARCHING
            # Query all relevant collections (old + new)
            all_relevant_ids = session.relevant_collection_ids
            await self._emit_status(
                __event_emitter__,
                f"🔍 Phase 3: Querying {len(all_relevant_ids)} collection(s) "
                f"(+{len(new_collection_ids)} new)...",
            )

            # Reset search terms for pass 2 using anchor + discovered gaps
            pass2_terms = initial_terms + gaps[:3]
            pass2_stale = 0
            start_iter = len(session.iterations) + 1

            for iter_num in range(
                start_iter,
                start_iter + self.valves.max_iterations,
            ):
                await self._emit_status(
                    __event_emitter__,
                    f"🔍 KR iter {iter_num}: querying "
                    f"{len(all_relevant_ids)} collection(s)...",
                )

                iteration = await rag.run_iteration(
                    session=session,
                    search_terms=pass2_terms,
                    collection_ids=all_relevant_ids,
                    collection_names=collection_map,
                    iteration_number=iter_num,
                    request=__request__,
                    user=__user__ or {},
                    file_ids_map=file_ids_map,
                )
                journal.write_iteration(session, iteration)

                if iteration.new_chunks == 0:
                    pass2_stale += 1
                else:
                    pass2_stale = 0

                await self._emit_status(
                    __event_emitter__,
                    f"📚 KR iter {iter_num}: {iteration.new_chunks} "
                    f"new chunk(s)",
                )

                if pass2_stale >= 3:
                    break

                pass2_terms = await rag.expand_terms(
                    session, pass2_terms, __request__, __user__ or {}
                )

                if (iter_num - start_iter + 1) >= self.valves.fixed_iterations:
                    if not await rag.should_continue(
                        session, __request__, __user__ or {}
                    ):
                        await self._emit_status(
                            __event_emitter__,
                            "✅ Phase 3 complete",
                        )
                        break

        # =============================================================
        #  Phase 6: Synthesize + verify
        # =============================================================
        session.phase = ResearchPhase.SYNTHESIZING
        await self._emit_status(
            __event_emitter__, "🧠 Synthesizing findings..."
        )

        rag_sources = kr._build_rag_sources(session, collection_map)
        answer = await synthesizer.synthesize(
            session, __request__, __user__ or {},
            relevant_sources=rag_sources,
            event_emitter=__event_emitter__,
        )

        session.phase = ResearchPhase.COMPLETE
        journal.write_manifest(session)
        await self._emit_status(
            __event_emitter__,
            f"📁 Journal: deep-research/{slug}/",
            done=True,
        )

        return answer

    # --- Private Helpers ---

    @staticmethod
    async def _emit_status(
        event_emitter: Optional[Callable],
        message: str,
        done: bool = False,
    ) -> None:
        """Emit a status update through OWUI's event emitter."""
        if event_emitter:
            await event_emitter(
                {
                    "type": "status",
                    "data": {"description": message, "done": done},
                }
            )
