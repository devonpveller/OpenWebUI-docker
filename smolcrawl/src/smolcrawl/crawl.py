"""
SmolCrawl - Lightweight Web Crawler
===================================
Async web crawler using httpx + BeautifulSoup for recursive crawling.
No heavy dependencies - replaces crawlee for simpler, more compatible crawling.
"""

from typing import Callable, List, Optional, Set
from urllib.parse import urljoin, urlparse
import asyncio
import time

from loguru import logger
from bs4 import BeautifulSoup
import httpx
import readabilipy.simple_json
import markdownify
import os
import platform

from .utils import get_cache
from .db import Page


# Apply Windows fix for readabilipy if needed
if platform.system() == 'Windows':
    import subprocess
    import readabilipy.utils
    import readabilipy.simple_json

    def _patched_have_npm():
        """Patched version of have_npm that works on Windows"""
        try:
            cp = subprocess.run(
                ["npm", "version"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                shell=True  # This is the key fix for Windows
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False
        return cp.returncode == 0

    def _patched_run_npm_install():
        """Patched version of run_npm_install that works on Windows"""
        import readabilipy.simple_json
        
        # Get the javascript directory path
        jsdir = os.path.join(os.path.dirname(readabilipy.simple_json.__file__), 'javascript')
        
        try:
            subprocess.run(
                ["npm", "install"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=True,
                shell=True,  # This is the key fix for Windows
                cwd=jsdir  # Run in the javascript directory where package.json is
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def _patched_have_node():
        """Check that we can run node and have a new enough version (Windows-compatible)"""
        try:
            cp = subprocess.run(
                ['node', '-v'], 
                stdout=subprocess.PIPE, 
                stderr=subprocess.PIPE, 
                check=False,
                shell=True  # This is the key fix for Windows
            )
        except FileNotFoundError:
            return False
        if not cp.returncode == 0:
            return False
        major = int(cp.stdout.split(b'.')[0].lstrip(b'v'))
        if major < 10:
            return False
        # check that this package has a node_modules dir in the javascript
        # directory, if it doesn't, it wasn't installed with Node support
        jsdir = os.path.join(os.path.dirname(readabilipy.simple_json.__file__), 'javascript')
        node_modules = os.path.join(jsdir, 'node_modules')
        if not os.path.exists(node_modules):
            # Try installing node dependencies.
            readabilipy.simple_json.run_npm_install()
        return os.path.exists(node_modules)

    # Apply the monkey patches
    readabilipy.utils.have_npm = _patched_have_npm
    readabilipy.utils.run_npm_install = _patched_run_npm_install
    readabilipy.simple_json.have_node = _patched_have_node
    readabilipy.simple_json.run_npm_install = _patched_run_npm_install
    
    logger.debug("Applied Windows fix for readabilipy Node.js detection")


def replace_domain(url: str, domain_override: Optional[str]) -> str:
    if domain_override:
        parsed_url = urlparse(url)
        new_netloc = urlparse(domain_override).netloc

        # Reconstruct the URL with the new netloc
        new_url = parsed_url._replace(netloc=new_netloc).geturl()
        return new_url
    return url


def extract_content_from_html(html: str) -> str:
    """Extract and convert HTML content to Markdown format.

    Args:
        html: Raw HTML content to process

    Returns:
        Simplified markdown version of the content
    """
    ret = readabilipy.simple_json.simple_json_from_html_string(
        html, use_readability=True
    )
    if not ret["content"]:
        return "<error>Page failed to be simplified from HTML</error>"
    content = markdownify.markdownify(
        ret["content"],
        heading_style=markdownify.ATX,
    )
    return content


def extract_from_html(url: str, raw_html: str) -> Page | None:
    """Extract a Page object from raw HTML content.
    
    Args:
        url: The URL the HTML was fetched from
        raw_html: The raw HTML content
        
    Returns:
        Page object or None if extraction failed
    """
    try:
        if "<title>" in raw_html:
            title = raw_html.split("<title>")[1].split("</title>")[0]
        else:
            title = ""
        content = extract_content_from_html(raw_html)
        return Page(url=url, title=title, content=content, raw_html=raw_html)
    except Exception as e:
        logger.error(f"Error extracting from HTML: {e}")
        return None


def is_same_domain(url: str, base_url: str) -> bool:
    """Check if URL belongs to the same domain as the base URL."""
    try:
        url_parsed = urlparse(url)
        base_parsed = urlparse(base_url)
        return url_parsed.netloc == base_parsed.netloc
    except Exception:
        return False


def normalize_url(url: str) -> str:
    """Normalize URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
    # Remove fragment, normalize path
    normalized = parsed._replace(fragment='')
    result = normalized.geturl().rstrip('/')
    return result


def extract_links(html: str, base_url: str) -> Set[str]:
    """Extract all valid links from HTML content.
    
    Args:
        html: Raw HTML content
        base_url: Base URL for resolving relative links
        
    Returns:
        Set of normalized absolute URLs from the same domain
    """
    links = set()
    try:
        soup = BeautifulSoup(html, 'lxml')
        for anchor in soup.find_all('a', href=True):
            href = anchor['href']
            
            # Skip empty, javascript, mailto, tel links
            if not href or href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
                continue
                
            # Convert to absolute URL
            absolute_url = urljoin(base_url, href)
            
            # Only include same-domain URLs
            if is_same_domain(absolute_url, base_url):
                # Normalize and add
                normalized = normalize_url(absolute_url)
                if normalized:
                    links.add(normalized)
    except Exception as e:
        logger.warning(f"Error extracting links from {base_url}: {e}")
    
    return links


from .frontier import URLFrontier, URLEntry

class SmolCrawler:
    """Mercator-style web crawler with URL frontier.
    
    Features:
        - BFS traversal with depth prioritization
        - Per-host rate limiting (politeness)
        - Async HTTP requests with connection pooling
        - Disk caching of results
    """
    
    def __init__(
        self,
        max_pages: int = 500,
        max_concurrent: int = 10,
        delay: float = 0.1, # Default host delay
        timeout: float = 30.0,
        max_retries: int = 3,
        num_priority_levels: int = 4,
        host_delays: Optional[dict] = None,
        user_agent: str = "SmolCrawl/2.0",
        on_page_crawled: Optional[Callable[[int, str], None]] = None,
    ):
        self.max_pages = max_pages
        self.max_concurrent = max_concurrent
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        
        # Mercator-style frontier
        # Use provided delay as default host delay
        self.frontier = URLFrontier(
            num_priority_levels=num_priority_levels,
            default_host_delay=delay,
            max_urls=max_pages * 20, # Allow buffer for frontier
        )
        
        # Apply custom host delays if provided
        if host_delays:
            self.frontier.back_queues.host_delays.update(host_delays)
            
        # Results
        self.pages: List[Page] = []
        self.on_page_crawled = on_page_crawled
        
        # Concurrency control
        self.semaphore = asyncio.Semaphore(max_concurrent)
        
        # Keep track of active workers to know when to stop
        self.active_workers = 0
        
    async def _worker(self, client: httpx.AsyncClient, base_url: str):
        """Worker that fetches URLs from frontier."""
        while len(self.pages) < self.max_pages:
            # Get next URL from frontier
            # Note: This might wait if back queues are enforcing politeness
            entry = await self.frontier.get()
            
            if entry is None:
                # Frontier empty or all hosts busy?
                # If frontier is empty but we have active workers, we should wait
                # because they might discover new links.
                if self.active_workers > 0:
                    await asyncio.sleep(0.1)
                    continue
                else:
                    break
            
            self.active_workers += 1
            try:
                await self._process_url(client, entry, base_url)
            finally:
                self.active_workers -= 1
                
    async def _fetch_url(self, client: httpx.AsyncClient, url: str) -> Optional[str]:
        """Fetch a URL with retries."""
        for attempt in range(self.max_retries):
            try:
                # Politeness is handled by frontier/back_queues before we get here
                response = await client.get(url, follow_redirects=True)
                
                content_type = response.headers.get('content-type', '')
                if 'text/html' not in content_type.lower():
                    logger.debug(f"Skipping non-HTML: {url} ({content_type})")
                    return None
                    
                response.raise_for_status()
                return response.text
                
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                # Log warning but don't spam for routine timeouts
                if attempt == self.max_retries - 1:
                    logger.warning(f"Failed to fetch {url}: {e}")
                
            except Exception as e:
                logger.error(f"Error fetching {url}: {e}")
            
            if attempt < self.max_retries - 1:
                await asyncio.sleep(1.0 * (attempt + 1))
                
        return None

    async def _process_url(
        self, 
        client: httpx.AsyncClient, 
        entry: URLEntry, 
        base_url: str
    ):
        """Fetch and process a single URL."""
        if len(self.pages) >= self.max_pages:
            return

        html = await self._fetch_url(client, entry.url)
        if not html:
            return
        
        # Extract content
        page = extract_from_html(entry.url, html)
        if page:
            self.pages.append(page)
            # Log with depth info
            logger.info(f"[D{entry.depth}] Scraped: {page.title[:40]}... ({entry.url})")

            # Notify callback
            if self.on_page_crawled:
                try:
                    self.on_page_crawled(len(self.pages), entry.url)
                except Exception:
                    pass  # Don't let callback errors stop crawling

            # Progress update
            if len(self.pages) % 10 == 0:
                logger.info(f"Progress: {len(self.pages)} pages")
        
        # Discover links and add to frontier (depth + 1)
        if len(self.pages) < self.max_pages:
            links = extract_links(html, entry.url)
            for link in links:
                if is_same_domain(link, base_url):
                    self.frontier.add(
                        url=link,
                        depth=entry.depth + 1,
                        parent_url=entry.url,
                    )

    async def crawl(self, start_url: str) -> List[Page]:
        """Start crawling from the given URL."""
        logger.info(f"Starting Mercator crawl of {start_url} (max {self.max_pages})")
        start_time = time.time()
        
        # Seed the frontier
        self.frontier.add(start_url, depth=0)
        
        # Configure client
        headers = {"User-Agent": self.user_agent}
        limits = httpx.Limits(max_connections=self.max_concurrent * 2)
        
        async with httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            limits=limits,
            http2=True,
        ) as client:
            
            # Run workers
            workers = [
                asyncio.create_task(self._worker(client, start_url))
                for _ in range(self.max_concurrent)
            ]
            
            await asyncio.gather(*workers, return_exceptions=True)
        
        elapsed = time.time() - start_time
        stats = self.frontier.stats()
        logger.success(
            f"Crawled {len(self.pages)} pages in {elapsed:.1f}s | "
            f"Deduped: {stats['urls_deduplicated']} | "
            f"Hosts: {stats['hosts_tracked']}"
        )
        
        return self.pages


async def crawl_target(
    target_url: str,
    max_pages: int = 500,
    max_concurrent: int = 10,
    delay: float = 0.1,
    use_cache: bool = True,
    on_page_crawled: Optional[Callable[[int, str], None]] = None,
) -> List[Page]:
    """Crawl a target URL and return extracted pages."""
    logger.info(f"Starting crawl of {target_url}")
    cache = get_cache("crawl")

    # Check cache first
    if use_cache:
        cached_pages = cache.get(target_url)
        if cached_pages:
            logger.info(f"Using cached results for {target_url} ({len(cached_pages)} pages)")
            pages = [Page(**page) for page in cached_pages]
            # Still notify callback for cached pages so progress is visible
            if on_page_crawled:
                for i, page in enumerate(pages, 1):
                    try:
                        on_page_crawled(i, page.url)
                    except Exception:
                        pass
            return pages

    # Run the crawler
    crawler = SmolCrawler(
        max_pages=max_pages,
        max_concurrent=max_concurrent,
        delay=delay,
        on_page_crawled=on_page_crawled,
    )
    pages = await crawler.crawl(target_url)

    # Cache results
    if use_cache and pages:
        pages_as_dicts = [page.model_dump() for page in pages]
        logger.debug(f"Caching {len(pages)} pages for {target_url}")
        cache.set(target_url, pages_as_dicts, expire=72.0 * 3600)
    
    return pages


# Convenience function for sync usage
def crawl_target_sync(
    target_url: str,
    max_pages: int = 500,
    max_concurrent: int = 10,
    delay: float = 0.1,
    use_cache: bool = True,
    on_page_crawled: Optional[Callable[[int, str], None]] = None,
) -> List[Page]:
    return asyncio.run(crawl_target(
        target_url,
        max_pages=max_pages,
        max_concurrent=max_concurrent,
        delay=delay,
        use_cache=use_cache,
        on_page_crawled=on_page_crawled,
    ))
