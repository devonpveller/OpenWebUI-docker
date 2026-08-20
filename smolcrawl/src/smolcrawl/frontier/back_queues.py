from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, List
import heapq
import time
import asyncio
from .models import URLEntry

@dataclass
class HostQueue:
    """Queue for a single host with timing information."""
    next_fetch_time: float = field()
    host: str = field()
    urls: deque = field(default_factory=deque)
    
    def add(self, entry: URLEntry) -> None:
        self.urls.append(entry)
    
    def get(self) -> Optional[URLEntry]:
        return self.urls.popleft() if self.urls else None
    
    def is_empty(self) -> bool:
        return len(self.urls) == 0

    def __lt__(self, other):
        if not isinstance(other, HostQueue):
            return NotImplemented
        return self.next_fetch_time < other.next_fetch_time


class BackQueueManager:
    """Manages per-host queues with politeness delays.
    
    Each host gets its own queue with a minimum delay between requests.
    A heap tracks which host is ready for the next request.
    """
    
    def __init__(
        self,
        default_delay: float = 1.0,
        host_delays: Optional[Dict[str, float]] = None,
    ):
        """
        Args:
            default_delay: Default seconds between requests to same host
            host_delays: Custom delays for specific hosts
        """
        self.default_delay = default_delay
        self.host_delays = host_delays or {}
        
        # Host -> HostQueue mapping
        self.host_queues: Dict[str, HostQueue] = {}
        
        # Min-heap of (next_fetch_time, host) for scheduling
        self.schedule_heap: List[HostQueue] = []
        
        # Track hosts currently in heap (to avoid duplicates)
        self.hosts_in_heap: Set[str] = set()
        
        self.total_urls = 0
    
    def _get_delay(self, host: str) -> float:
        """Get the politeness delay for a host."""
        return self.host_delays.get(host, self.default_delay)
    
    def add(self, entry: URLEntry) -> None:
        """Add a URL to its host's queue."""
        host = entry.host
        
        if host not in self.host_queues:
            # New host - create queue, ready immediately
            host_queue = HostQueue(
                next_fetch_time=time.time(),
                host=host,
            )
            self.host_queues[host] = host_queue
        
        self.host_queues[host].add(entry)
        self.total_urls += 1
        
        # Add to heap if not already scheduled
        if host not in self.hosts_in_heap:
            heapq.heappush(self.schedule_heap, self.host_queues[host])
            self.hosts_in_heap.add(host)
    
    async def get(self) -> Optional[URLEntry]:
        """Get next URL respecting politeness delays.
        
        Waits if necessary until a host is ready.
        Returns None if all queues are empty.
        """
        while self.schedule_heap:
            # Peek at next ready host
            host_queue = self.schedule_heap[0]
            
            # Wait if not ready yet
            now = time.time()
            if host_queue.next_fetch_time > now:
                wait_time = host_queue.next_fetch_time - now
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
            
            # Pop from heap
            heapq.heappop(self.schedule_heap)
            self.hosts_in_heap.discard(host_queue.host)
            
            # Get URL from host queue
            entry = host_queue.get()
            if entry is None:
                continue  # Queue was empty, try next host
            
            self.total_urls -= 1
            
            # Reschedule host if more URLs remain
            if not host_queue.is_empty():
                host_queue.next_fetch_time = time.time() + self._get_delay(host_queue.host)
                heapq.heappush(self.schedule_heap, host_queue)
                self.hosts_in_heap.add(host_queue.host)
            
            return entry
        
        return None
    
    def __len__(self) -> int:
        return self.total_urls
    
    def is_empty(self) -> bool:
        return self.total_urls == 0
