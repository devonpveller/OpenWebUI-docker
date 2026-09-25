from dataclasses import dataclass, field
from typing import Optional
import time

@dataclass
class URLEntry:
    """A URL with metadata for frontier scheduling."""
    # Note: Removed order=True to avoid potential python/pytest version conflicts.
    # Implemented __lt__ manually.
    # Fields ordered to respect defaults (non-defaults first).
    
    # Non-default fields
    priority: int = field()
    url: str = field()
    
    # Default fields
    # Secondary sort key
    timestamp: float = field(default_factory=time.time)
    
    depth: int = field(default=0)
    host: str = field(default="")
    parent_url: Optional[str] = field(default=None)
    
    # Retry tracking
    retries: int = field(default=0)
    last_error: Optional[str] = field(default=None)

    def __lt__(self, other):
        if not isinstance(other, URLEntry):
            return NotImplemented
        if self.priority != other.priority:
            return self.priority < other.priority
        return self.timestamp < other.timestamp
