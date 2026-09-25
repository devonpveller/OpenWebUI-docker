from collections import deque
from typing import List, Optional
import random
from .models import URLEntry

class FrontQueueManager:
    """Manages priority-based front queues for URL scheduling.
    
    URLs are distributed across queues by priority level.
    Lower priority number = higher importance = processed first.
    """
    
    def __init__(self, num_queues: int = 4, priority_weights: Optional[List[float]] = None):
        """
        Args:
            num_queues: Number of priority levels (default 4)
            priority_weights: Probability weights for queue selection
                              Default: [0.5, 0.3, 0.15, 0.05] (favor high priority)
        """
        self.num_queues = num_queues
        self.queues: List[deque] = [deque() for _ in range(num_queues)]
        self.priority_weights = priority_weights or self._default_weights(num_queues)
        self.total_urls = 0
    
    def _default_weights(self, n: int) -> List[float]:
        """Generate decreasing weights: [0.5, 0.3, 0.15, 0.05, ...]"""
        weights = [1.0 / (2 ** (i + 1)) for i in range(n)]
        # Normalize to sum to 1.0
        total = sum(weights)
        return [w / total for w in weights]
    
    def _depth_to_priority(self, depth: int) -> int:
        """Map crawl depth to priority queue index.
        
        Depth 0-1  -> Priority 0 (highest)
        Depth 2-3  -> Priority 1
        Depth 4-5  -> Priority 2
        Depth 6+   -> Priority 3 (lowest)
        """
        priority = min(depth // 2, self.num_queues - 1)
        return priority
    
    def add(self, entry: URLEntry) -> None:
        """Add a URL entry to the appropriate priority queue."""
        priority = self._depth_to_priority(entry.depth)
        entry.priority = priority
        self.queues[priority].append(entry)
        self.total_urls += 1
    
    def get(self) -> Optional[URLEntry]:
        """Get next URL using weighted random selection.
        
        Higher priority queues are selected more frequently,
        but lower priority queues still get some attention.
        """
        
        # Find non-empty queues
        available = [(i, q) for i, q in enumerate(self.queues) if q]
        if not available:
            return None
        
        # Weighted selection among available queues
        indices = [i for i, _ in available]
        weights = [self.priority_weights[i] for i in indices]
        
        # Normalize weights for available queues
        total_weight = sum(weights)
        normalized = [w / total_weight for w in weights]
        
        selected_idx = random.choices(indices, weights=normalized, k=1)[0]
        entry = self.queues[selected_idx].popleft()
        self.total_urls -= 1
        return entry
    
    def __len__(self) -> int:
        return self.total_urls
    
    def is_empty(self) -> bool:
        return self.total_urls == 0
