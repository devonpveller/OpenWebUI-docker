"""
SmolCrawl container HTTP client for triggering crawls.

Single Responsibility: Only handles HTTP communication with the SmolCrawl pipeline.
Encapsulation: HTTP details and streaming are internal.
"""

import logging
import time
from typing import Any, Callable, Optional

import httpx

from .models import CrawlResult, Valves

logger = logging.getLogger("deep_research.crawl_integration")


class CrawlClient:
    """HTTP client for triggering crawls via the SmolCrawl pipeline container.

    Communicates with the SmolCrawl Pipelines server using the standard
    OpenAI-compatible chat completions endpoint.
    """

    def __init__(self, valves: Valves):
        self._valves = valves

    async def trigger_crawl(
        self,
        domain: str,
        kb_name: str,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> CrawlResult:
        """Trigger a crawl of a domain into an OWUI knowledge collection.

        Sends a chat completion request to the SmolCrawl pipeline container,
        which runs crawl -> augment -> upload deterministically.

        Args:
            domain: The domain to crawl (e.g. "docs.example.com").
            kb_name: Name for the OWUI knowledge collection.
            on_progress: Optional callback for streaming progress messages.

        Returns:
            CrawlResult with success/failure details.
        """
        start_time = time.monotonic()
        url = domain if domain.startswith("http") else f"https://{domain}/"

        result = CrawlResult(
            domain=domain,
            kb_name=kb_name,
        )

        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                response = await client.post(
                    f"{self._valves.smolcrawl_url}/v1/chat/completions",
                    headers={
                        "Authorization": (
                            f"Bearer {self._valves.smolcrawl_api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "smolcrawl-knowledge-builder",
                        "messages": [
                            {
                                "role": "user",
                                "content": f"crawl {url} into {kb_name}",
                            }
                        ],
                        "stream": False,
                    },
                )
                response.raise_for_status()

                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )

                result.success = True
                result.pages_crawled = self._extract_page_count(content)

                if on_progress:
                    on_progress(
                        f"✅ Crawled {domain}: {result.pages_crawled} pages"
                    )

        except httpx.HTTPStatusError as e:
            result.error = f"HTTP {e.response.status_code}: {e.response.text[:200]}"
            logger.error("Crawl failed for %s: %s", domain, result.error)
            if on_progress:
                on_progress(f"❌ Crawl failed for {domain}: {result.error}")

        except httpx.RequestError as e:
            result.error = f"Connection error: {e}"
            logger.error("Crawl connection failed for %s: %s", domain, e)
            if on_progress:
                on_progress(f"❌ Cannot reach SmolCrawl for {domain}: {e}")

        except Exception as e:
            result.error = f"Unexpected error: {e}"
            logger.error("Crawl unexpected error for %s: %s", domain, e)
            if on_progress:
                on_progress(f"❌ Unexpected error crawling {domain}: {e}")

        result.duration_seconds = time.monotonic() - start_time
        return result

    async def trigger_crawl_streaming(
        self,
        domain: str,
        kb_name: str,
        event_emitter: Any,
    ) -> CrawlResult:
        """Trigger a crawl with SSE progress streaming to the user.

        Uses the streaming endpoint of the SmolCrawl pipeline to relay
        progress messages through OWUI's event emitter.

        Args:
            domain: The domain to crawl.
            kb_name: Name for the knowledge collection.
            event_emitter: OWUI's __event_emitter__ for streaming status.

        Returns:
            CrawlResult with success/failure details.
        """
        start_time = time.monotonic()
        url = domain if domain.startswith("http") else f"https://{domain}/"

        result = CrawlResult(domain=domain, kb_name=kb_name)
        accumulated_content = ""

        try:
            async with httpx.AsyncClient(timeout=600.0) as client:
                async with client.stream(
                    "POST",
                    f"{self._valves.smolcrawl_url}/v1/chat/completions",
                    headers={
                        "Authorization": (
                            f"Bearer {self._valves.smolcrawl_api_key}"
                        ),
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "smolcrawl-knowledge-builder",
                        "messages": [
                            {
                                "role": "user",
                                "content": f"crawl {url} into {kb_name}",
                            }
                        ],
                        "stream": True,
                    },
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        chunk = line[6:]
                        if chunk == "[DONE]":
                            break
                        accumulated_content += chunk

                        if event_emitter:
                            await event_emitter(
                                {
                                    "type": "status",
                                    "data": {
                                        "description": (
                                            f"Crawling {domain}..."
                                        ),
                                        "done": False,
                                    },
                                }
                            )

            result.success = True
            result.pages_crawled = self._extract_page_count(
                accumulated_content
            )

        except (httpx.HTTPStatusError, httpx.RequestError, Exception) as e:
            result.error = str(e)
            logger.error("Streaming crawl failed for %s: %s", domain, e)

        result.duration_seconds = time.monotonic() - start_time
        return result

    @staticmethod
    def _extract_page_count(content: str) -> int:
        """Extract page count from SmolCrawl pipeline response text."""
        import re

        match = re.search(r"(\d+)\s*pages?\s*crawled", content, re.IGNORECASE)
        if match:
            return int(match.group(1))

        match = re.search(r"Crawled\s*\*?\*?(\d+)\*?\*?", content)
        if match:
            return int(match.group(1))

        return 0
