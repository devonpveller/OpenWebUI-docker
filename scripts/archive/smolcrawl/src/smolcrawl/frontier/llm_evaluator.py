"""
LLM-based link evaluation for cross-domain crawl discovery.

Evaluates outbound links found during crawling using an LLM to score
relevance to the crawl goal. Scored links are queued for user approval
before entering the URL frontier.

Single Responsibility: Only handles link evaluation and scoring.
Open/Closed: EvaluatorConfig allows extension without modifying core logic.
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("smolcrawl.frontier.llm_evaluator")

_EVALUATION_SYSTEM_PROMPT = """\
You are a web crawl advisor. Given a crawl goal and a list of outbound \
URLs discovered during crawling, score each URL's relevance to the goal \
from 0.0 (irrelevant) to 1.0 (highly relevant).

Crawl Goal: {crawl_goal}
Current crawl seed: {seed_url}
Already crawling domains: {current_domains}

For each URL, respond in JSON array format:
[
  {{"url": "...", "score": 0.8, "rationale": "Official API reference"}},
  {{"url": "...", "score": 0.2, "rationale": "Marketing page"}}
]

Only include URLs scoring >= 0.4. Omit clearly irrelevant links \
(social media, ads, tracking, login pages).
Respond ONLY with valid JSON.\
"""


@dataclass(frozen=True)
class EvaluatedLink:
    """A cross-domain link scored by the LLM for crawl relevance."""
    url: str
    score: float
    rationale: str
    source_page: str
    domain: str


@dataclass
class EvaluatorConfig:
    """Configuration for the LLM link evaluator."""
    llm_base_url: str = "http://openwebui:8080"
    llm_api_key: str = ""
    model_id: str = ""
    batch_size: int = 20
    min_score: float = 0.6
    crawl_goal: str = ""
    max_approval_prompts: int = 3


class LinkBuffer:
    """Accumulates cross-domain links until a batch is ready for evaluation.

    Thread-safe buffer that collects outbound links and flushes them
    to the LLM evaluator when the batch size is reached.
    """

    def __init__(self, batch_size: int = 20):
        self._batch_size = batch_size
        self._buffer: List[Dict[str, str]] = []
        self._seen_urls: set = set()

    @property
    def size(self) -> int:
        return len(self._buffer)

    @property
    def is_ready(self) -> bool:
        return len(self._buffer) >= self._batch_size

    def add(self, url: str, source_page: str) -> bool:
        """Add a link to the buffer. Returns True if it was new."""
        if url in self._seen_urls:
            return False
        self._seen_urls.add(url)
        self._buffer.append({"url": url, "source_page": source_page})
        return True

    def flush(self) -> List[Dict[str, str]]:
        """Remove and return all buffered links."""
        batch = self._buffer[:]
        self._buffer.clear()
        return batch

    def clear(self) -> None:
        self._buffer.clear()


class LlmLinkEvaluator:
    """Evaluates cross-domain links using an LLM for relevance scoring.

    Receives batches of outbound URLs, sends them to an LLM with the
    crawl goal for scoring, and returns scored links above the threshold.
    """

    def __init__(self, config: EvaluatorConfig):
        self._config = config
        self._buffer = LinkBuffer(config.batch_size)
        self._approval_queue: List[EvaluatedLink] = []
        self._approval_count = 0

    @property
    def pending_approvals(self) -> List[EvaluatedLink]:
        return list(self._approval_queue)

    @property
    def approval_count(self) -> int:
        return self._approval_count

    def add_links(self, urls: List[str], source_page: str) -> None:
        """Add discovered cross-domain links to the evaluation buffer.

        Args:
            urls: List of outbound URLs found on a page.
            source_page: URL of the page where these links were found.
        """
        for url in urls:
            self._buffer.add(url, source_page)

    async def evaluate_if_ready(
        self,
        seed_url: str,
        current_domains: List[str],
        llm_caller: Optional[Callable] = None,
    ) -> List[EvaluatedLink]:
        """Evaluate buffered links if the batch is full.

        Args:
            seed_url: The original crawl seed URL.
            current_domains: Domains already being crawled.
            llm_caller: Async callable that takes (system_prompt, user_prompt)
                        and returns the LLM response text.

        Returns:
            List of newly evaluated links above min_score threshold.
        """
        if not self._buffer.is_ready:
            return []

        batch = self._buffer.flush()
        return await self._evaluate_batch(
            batch, seed_url, current_domains, llm_caller
        )

    async def evaluate_remaining(
        self,
        seed_url: str,
        current_domains: List[str],
        llm_caller: Optional[Callable] = None,
    ) -> List[EvaluatedLink]:
        """Evaluate any remaining buffered links at end of crawl.

        Args:
            seed_url: The original crawl seed URL.
            current_domains: Domains already being crawled.
            llm_caller: Async callable for LLM invocation.

        Returns:
            List of evaluated links above threshold.
        """
        if self._buffer.size == 0:
            return []

        batch = self._buffer.flush()
        return await self._evaluate_batch(
            batch, seed_url, current_domains, llm_caller
        )

    def get_approval_prompt(self) -> str:
        """Format the approval queue as a user-facing prompt.

        Returns:
            Formatted markdown string presenting scored links.
        """
        if not self._approval_queue:
            return ""

        lines = [
            f"📡 Crawl discovered {len(self._approval_queue)} "
            f"potentially relevant external link(s):\n"
        ]

        for i, link in enumerate(self._approval_queue, 1):
            lines.append(
                f" {i}. **[{link.score:.2f}] {link.domain}**\n"
                f"    {link.rationale}\n"
                f"    Found on: {link.source_page}\n"
            )

        lines.append(
            '\nReply with numbers to approve (e.g., "1,2") '
            'or "all" / "skip":'
        )

        self._approval_count += 1
        return "\n".join(lines)

    def parse_approval(self, selection: str) -> List[str]:
        """Parse user approval and return approved URLs.

        Args:
            selection: User input like "1,2", "all", or "skip".

        Returns:
            List of approved URLs to add to the frontier.
        """
        selection = selection.strip().lower()
        approved_urls = []

        if selection == "skip":
            self._approval_queue.clear()
            return approved_urls

        if selection == "all":
            approved_urls = [link.url for link in self._approval_queue]
            self._approval_queue.clear()
            return approved_urls

        for part in selection.replace(" ", ",").split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(self._approval_queue):
                    approved_urls.append(self._approval_queue[idx].url)

        self._approval_queue.clear()
        return approved_urls

    def has_remaining_prompts(self) -> bool:
        """Check if we can still interrupt the user with approval prompts."""
        return self._approval_count < self._config.max_approval_prompts

    async def _evaluate_batch(
        self,
        batch: List[Dict[str, str]],
        seed_url: str,
        current_domains: List[str],
        llm_caller: Optional[Callable],
    ) -> List[EvaluatedLink]:
        """Send a batch of links to the LLM for evaluation."""
        if not batch or not llm_caller:
            return []

        url_list = "\n".join(
            f"- {item['url']} (found on {item['source_page']})"
            for item in batch
        )

        system_prompt = _EVALUATION_SYSTEM_PROMPT.format(
            crawl_goal=self._config.crawl_goal,
            seed_url=seed_url,
            current_domains=", ".join(current_domains),
        )

        try:
            response_text = await llm_caller(
                system_prompt,
                f"Evaluate these discovered outbound links:\n\n{url_list}",
            )
            evaluated = self._parse_evaluation(response_text, batch)

            # Queue links above threshold for user approval
            qualifying = [
                link for link in evaluated
                if link.score >= self._config.min_score
            ]
            self._approval_queue.extend(qualifying)

            return qualifying

        except Exception as e:
            logger.error("LLM evaluation failed: %s", e)
            return []

    def _parse_evaluation(
        self,
        response_text: str,
        batch: List[Dict[str, str]],
    ) -> List[EvaluatedLink]:
        """Parse the LLM's JSON response into EvaluatedLink objects."""
        # Build source page lookup
        source_map = {item["url"]: item["source_page"] for item in batch}

        # Try to extract JSON
        text = response_text.strip()
        data = None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(
                r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL
            )
            if match:
                try:
                    data = json.loads(match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        if not isinstance(data, list):
            logger.warning("Could not parse LLM evaluation response")
            return []

        links = []
        for item in data:
            if not isinstance(item, dict):
                continue
            url = item.get("url", "")
            try:
                parsed = urlparse(url)
                links.append(
                    EvaluatedLink(
                        url=url,
                        score=float(item.get("score", 0.0)),
                        rationale=str(item.get("rationale", "")),
                        source_page=source_map.get(url, ""),
                        domain=parsed.netloc,
                    )
                )
            except (TypeError, ValueError) as e:
                logger.warning("Skipping malformed link entry: %s", e)

        return links


def partition_links(
    links: List[str],
    seed_domain: str,
) -> tuple:
    """Partition discovered links into same-domain and cross-domain.

    Args:
        links: List of absolute URLs discovered on a page.
        seed_domain: The domain of the crawl seed URL.

    Returns:
        Tuple of (same_domain_links, cross_domain_links).
    """
    same_domain = []
    cross_domain = []

    for link in links:
        try:
            parsed = urlparse(link)
            if parsed.netloc == seed_domain or not parsed.netloc:
                same_domain.append(link)
            else:
                cross_domain.append(link)
        except Exception:
            continue

    return same_domain, cross_domain
