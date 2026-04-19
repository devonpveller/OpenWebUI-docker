"""
Domain discovery via web search and LLM scoring.

Single Responsibility: Discovers and scores relevant domains for a research query.
Interface Segregation: Exposes only discover_domains() to callers.
"""

import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .models import DiscoveredDomain, Valves
from .sub_agent import SubAgent

logger = logging.getLogger("deep_research.domain_discovery")

_DISCOVERY_SYSTEM_PROMPT = """\
You are a research librarian. Given a research topic, search the web to \
identify the most authoritative documentation sources.

Return a JSON array of objects, each with:
- "url": full URL of the documentation root (e.g. "https://docs.example.com/")
- "domain": the domain name (e.g. "docs.example.com")
- "score": relevance score from 0.0 (irrelevant) to 1.0 (highly relevant)
- "rationale": one-sentence explanation of why this source is relevant

Focus on: official docs, API references, tutorials, community wikis, \
technical blogs with substantive content.
Exclude: social media, forums with low signal-to-noise, video-only content, \
paywalled sites.

Return at most {max_domains} domains, ordered by score descending.
Respond ONLY with valid JSON — no markdown, no explanation outside the array.\
"""

_RANKING_SYSTEM_PROMPT = """\
You are evaluating which existing knowledge collections are relevant to a \
research query. Given a list of collections and the query, return a JSON \
array of collection IDs that are relevant.

Respond ONLY with a JSON array of ID strings. Example: ["id1", "id2"]\
"""


class DomainDiscovery:
    """Discovers relevant domains for a research topic using web search.

    Uses a sub-agent with web search enabled to find authoritative documentation
    sources, then scores them by relevance.
    """

    def __init__(self, valves: Valves, sub_agent: SubAgent):
        self._valves = valves
        self._sub_agent = sub_agent

    async def discover_domains(
        self,
        query: str,
        request: Any,
        user: Dict,
    ) -> List[DiscoveredDomain]:
        """Search the web for domains relevant to the research query.

        Calls OWUI's search_web() directly, then uses LLM to rank/score.
        """
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

        system_prompt = _DISCOVERY_SYSTEM_PROMPT.format(
            max_domains=self._valves.max_domains,
        )

        try:
            result = await self._sub_agent.run_json(
                system_prompt=system_prompt,
                user_prompt=f"Research query: {query}\n\nWeb search results:\n{listing}",
                request=request,
                user=user,
            )
        except Exception as e:
            logger.error("Domain discovery LLM ranking failed: %s", e)
            # Fall back to raw search results as domains
            result = []
            seen = set()
            for r in results[:self._valves.max_domains]:
                try:
                    domain = urlparse(r.link).netloc
                except Exception:
                    continue
                if domain not in seen:
                    seen.add(domain)
                    result.append({
                        "url": r.link, "domain": domain,
                        "score": 0.5, "rationale": r.snippet or ""})

        return self._parse_domains(result)

    async def rank_existing_collections(
        self,
        query: str,
        collections: List[Dict],
        request: Any,
        user: Dict,
    ) -> List[str]:
        """Use LLM to select which existing collections are relevant.

        Args:
            query: The research question.
            collections: List of collection dicts from OWUI API.
            request: OWUI __request__ object.
            user: OWUI __user__ dict.

        Returns:
            List of relevant collection IDs.
        """
        if not collections:
            return []

        summaries = "\n".join(
            f"- ID: {c['id']} | Name: {c['name']} | "
            f"Description: {c.get('description', 'None')} | "
            f"Files: {len(c.get('data', {}).get('file_ids', []))}"
            for c in collections
        )

        try:
            result = await self._sub_agent.run_json(
                system_prompt=_RANKING_SYSTEM_PROMPT,
                user_prompt=(
                    f"Query: {query}\n\nAvailable collections:\n{summaries}"
                ),
                request=request,
                user=user,
            )
        except (ValueError, Exception) as e:
            logger.error("Collection ranking failed: %s", e)
            return []

        if isinstance(result, list):
            valid_ids = {c["id"] for c in collections}
            return [rid for rid in result if rid in valid_ids]
        return []

    def check_domain_coverage(
        self,
        domains: List[DiscoveredDomain],
        collections: List[Dict],
    ) -> List[DiscoveredDomain]:
        """Mark domains already covered by existing knowledge collections.

        Compares discovered domain names against collection names/descriptions
        for overlap detection.

        Args:
            domains: List of discovered domains.
            collections: List of existing collection dicts.

        Returns:
            The same domains list with already_covered flags updated.
        """
        collection_hints = set()
        for col in collections:
            name_lower = col.get("name", "").lower()
            desc_lower = col.get("description", "").lower()
            collection_hints.add(name_lower)
            collection_hints.add(desc_lower)
            # Extract domain-like tokens
            for text in (name_lower, desc_lower):
                for word in text.split():
                    if "." in word and len(word) > 4:
                        collection_hints.add(word)

        for domain in domains:
            domain_lower = domain.domain.lower()
            for hint in collection_hints:
                if domain_lower in hint or hint in domain_lower:
                    domain.already_covered = True
                    # Try to find the matching collection ID
                    for col in collections:
                        if domain_lower in col.get("name", "").lower():
                            domain.existing_collection_id = col["id"]
                            break
                    break

        return domains

    @staticmethod
    def _parse_domains(data: Any) -> List[DiscoveredDomain]:
        """Parse LLM JSON response into DiscoveredDomain objects."""
        if not isinstance(data, list):
            logger.warning("Expected list from LLM, got: %s", type(data))
            return []

        domains = []
        for item in data:
            if not isinstance(item, dict):
                continue
            try:
                url = item.get("url", "")
                domain = item.get("domain", "")
                if not domain and url:
                    domain = urlparse(url).netloc
                if not url and domain:
                    url = f"https://{domain}/"

                domains.append(
                    DiscoveredDomain(
                        url=url,
                        domain=domain,
                        score=float(item.get("score", 0.5)),
                        rationale=str(item.get("rationale", "")),
                    )
                )
            except (TypeError, ValueError) as e:
                logger.warning("Skipping malformed domain entry: %s", e)
                continue

        # Sort by score descending
        domains.sort(key=lambda d: d.score, reverse=True)
        return domains

    @staticmethod
    def format_approval_message(
        domains: List[DiscoveredDomain],
        existing_collections: List[Dict],
    ) -> str:
        """Format domain list as a user-facing approval prompt.

        Args:
            domains: List of discovered domains to present.
            existing_collections: List of already-available collections.

        Returns:
            Formatted markdown string for display in chat.
        """
        lines = []

        if existing_collections:
            relevant_count = len(existing_collections)
            lines.append(
                f"📚 Found **{relevant_count}** existing knowledge "
                f"collection(s) that may be relevant.\n"
            )

        lines.append(
            f"🌐 Discovered **{len(domains)}** relevant domain(s):\n"
        )

        for i, domain in enumerate(domains, 1):
            covered = " *(already in KB)*" if domain.already_covered else ""
            lines.append(
                f" {i}. **[{domain.score:.2f}] {domain.domain}**{covered}\n"
                f"    {domain.rationale}\n"
            )

        lines.append(
            '\nReply with numbers to approve (e.g., "1,2,3"), '
            '"all", or "skip" to use existing collections only.\n'
            'You can also add domains: "1,2 + docs.example.com"'
        )

        return "\n".join(lines)

    @staticmethod
    def parse_approval(
        selection: str,
        domains: List[DiscoveredDomain],
        additional_domains: str = "",
    ) -> List[DiscoveredDomain]:
        """Parse the user's approval selection into a list of domains.

        Args:
            selection: User input like "1,2,3", "all", or "skip".
            domains: The original discovered domains list.
            additional_domains: Space-separated extra domain names.

        Returns:
            List of approved DiscoveredDomain objects.
        """
        selection = selection.strip().lower()
        approved = []

        if selection == "skip":
            pass
        elif selection == "all":
            approved = [d for d in domains if not d.already_covered]
        else:
            # Parse comma-separated numbers, ignoring any "+ ..." suffix
            main_part = selection.split("+")[0].strip()
            for part in main_part.replace(" ", ",").split(","):
                part = part.strip()
                if part.isdigit():
                    idx = int(part) - 1
                    if 0 <= idx < len(domains):
                        approved.append(domains[idx])

        # Parse additional domains
        if additional_domains:
            for domain_str in additional_domains.strip().split():
                domain_str = domain_str.strip().strip(",")
                if domain_str and "." in domain_str:
                    approved.append(
                        DiscoveredDomain(
                            url=f"https://{domain_str}/",
                            domain=domain_str,
                            score=0.0,
                            rationale="User-specified domain",
                        )
                    )

        return approved
