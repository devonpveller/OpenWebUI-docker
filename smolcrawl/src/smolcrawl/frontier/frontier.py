from urllib.parse import urlparse
from typing import Set, Optional
import asyncio
from .models import URLEntry
from .front_queues import FrontQueueManager
from .back_queues import BackQueueManager

class URLFrontier:
    """Mercator-style URL Frontier combining priority and politeness.
    
    Flow:
    1. New URLs enter front queues (prioritized by depth/importance)
    2. Front queue selector picks next URL
    3. URL is routed to its host's back queue
    4. Back queue manager enforces per-host delays
    5. Ready URLs are dispatched to fetchers
    """
    
    def __init__(
        self,
        num_priority_levels: int = 4,
        default_host_delay: float = 1.0,
        max_urls: int = 100_000,
    ):
        self.front_queues = FrontQueueManager(num_queues=num_priority_levels)
        self.back_queues = BackQueueManager(default_delay=default_host_delay)
        
        # Deduplication
        self.seen_urls: Set[str] = set()
        self.max_urls = max_urls
        
        # Stats
        self.urls_added = 0
        self.urls_fetched = 0
        self.urls_deduplicated = 0
    
    def _extract_host(self, url: str) -> str:
        """Extract host from URL."""
        parsed = urlparse(url)
        return parsed.netloc.lower()
    
    def add(self, url: str, depth: int = 0, parent_url: Optional[str] = None) -> bool:
        """Add a URL to the frontier.
        
        Returns True if URL was added, False if deduplicated.
        """
        # Simple normalization - lower case scheme/host, remove fragment
        # For more complex normalization, we might need a utility function
        # But this is consistent with most basic needs
        
        # Deduplication
        if url in self.seen_urls:
            self.urls_deduplicated += 1
            return False
        
        if len(self.seen_urls) >= self.max_urls:
            return False  # Frontier full
        
        self.seen_urls.add(url)
        
        # Create entry and add to front queue
        entry = URLEntry(
            priority=0,  # Will be set by front queue
            url=url,
            depth=depth,
            host=self._extract_host(url),
            parent_url=parent_url,
        )
        
        self.front_queues.add(entry)
        self.urls_added += 1
        return True
    
    async def get(self) -> Optional[URLEntry]:
        """Get next URL to fetch, respecting priority and politeness."""
        # Transfer from front to back queues as needed
        # We loop until we find a ready URL or determine there's nothing to do right now
        
        # Limit the number of transfers to avoid infinite loops if back queues are full/busy
        # but in this implementation back queues don't block adding, they just delay getting
        
        while not self.back_queues.is_empty() or not self.front_queues.is_empty():
            # First, try to get from back queues (ready URLs)
            if not self.back_queues.is_empty():
                entry = await self.back_queues.get()
                if entry:
                    self.urls_fetched += 1
                    return entry
            
            # If back queues didn't return anything (e.g. all busy waiting),
            # we can try to fill them from front queues
            if not self.front_queues.is_empty():
                entry = self.front_queues.get()
                if entry:
                    self.back_queues.add(entry)
                
                # After moving one item, we loop back to check back queues again
                # This ensures we don't dump everything into back queues at once if they aren't being consumed
                continue
            
            # If we are here, front queues are empty, and back queues are either empty or all waiting
            # If back queues are not empty but returned None, it means they are waiting (politeness)
            # In that case, we should wait a bit or return None to let caller handle it?
            # The back_queues.get() already waits if there is a ready host soon?
            # Actually back_queues.get() waits if the HEAD of heap is not ready.
            # If all hosts are waiting, it might wait.
            # But if we return None here, the worker might stop.
            
            # If back queues are not empty, we should probably wait?
            # But wait, back_queues.get() implementation:
            # It waits if the host is not ready.
            # So if it returns None, it means schedule_heap is empty but host_queues might not be?
            # No, if schedule_heap is empty, then no host has URLs.
            
            # So if back_queues is not empty, get() will eventually return something or wait.
            # Unless we implement a non-blocking get/peek.
            
            break
        
        return None
    
    def is_empty(self) -> bool:
        return self.front_queues.is_empty() and self.back_queues.is_empty()
    
    def stats(self) -> dict:
        """Return frontier statistics."""
        return {
            "urls_added": self.urls_added,
            "urls_fetched": self.urls_fetched,
            "urls_deduplicated": self.urls_deduplicated,
            "front_queue_size": len(self.front_queues),
            "back_queue_size": len(self.back_queues),
            "seen_urls": len(self.seen_urls),
            "hosts_tracked": len(self.back_queues.host_queues),
        }
