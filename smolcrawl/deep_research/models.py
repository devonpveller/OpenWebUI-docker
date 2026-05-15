"""
Data models, configuration, and valve definitions for Deep Research.

Single Responsibility: Only defines data structures and validation.
Open/Closed: Extend via subclassing or adding new fields.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ResearchPhase(str, Enum):
    """Tracks current phase of a research session."""
    INITIALIZING = "initializing"
    DISCOVERING = "discovering"
    AWAITING_APPROVAL = "awaiting_approval"
    CRAWLING = "crawling"
    RESEARCHING = "researching"
    SYNTHESIZING = "synthesizing"
    COMPLETE = "complete"
    FAILED = "failed"


class Valves(BaseModel):
    """User-configurable settings for the Deep Research function."""

    # SmolCrawl container connection (deep_research only)
    smolcrawl_url: str = Field(
        default="http://smolcrawl-pipelines:9099",
        description="SmolCrawl pipeline container URL",
    )
    smolcrawl_api_key: str = Field(
        default="0p3n-w3bu!",
        description="Pipelines server API key",
    )

    # OWUI API
    owui_base_url: str = Field(
        default="http://openwebui:8080",
        description="Open WebUI API base URL",
    )
    owui_api_key: str = Field(
        default="",
        description="Bearer token for OWUI API",
    )

    # Research settings (shared by research + deep_research)
    max_iterations: int = Field(
        default=3,
        ge=1,
        le=15,
        description="Hard cap on research iterations",
    )
    fixed_iterations: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Guaranteed iterations before continue-decision",
    )
    min_relevant_sources: int = Field(
        default=3,
        ge=1,
        le=30,
        description="Target: stop researching once this many anchor-relevant sources are found",
    )
    max_research_calls_per_chat: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Max research() calls the chat model may make per "
                    "conversation before a graceful stop directive is "
                    "returned (prevents unbounded per-item fan-out on broad "
                    "survey prompts). The user can resume past this with a "
                    "'research continue:' prompt.",
    )
    max_web_results: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Max web search results per query",
    )
    sub_agent_nothink: bool = Field(
        default=True,
        description="Route mechanical sub-agent JSON calls (anchor, "
                    "relevance gate, topic extraction, gap analysis, etc.) "
                    "to the reasoning-disabled model alias for speed. With "
                    "llama-swap this is the SAME model process (no reload) — "
                    "it only skips thinking-token generation. Falls back to "
                    "the base model if the alias is unavailable.",
    )
    nothink_suffix: str = Field(
        default=":nothink",
        description="Suffix appended to the chat model id to address its "
                    "reasoning-disabled alias (matches the llama-swap "
                    "setParamsByID '${MODEL_ID}:nothink' entry). Set empty "
                    "to disable nothink routing regardless of "
                    "sub_agent_nothink.",
    )
    max_parallel_queries: int = Field(
        default=5,
        ge=1,
        le=32,
        description="Max concurrent RAG collection/file queries per "
                    "iteration. Parallelizes the term×collection fan-out "
                    "without overloading the single embedding server.",
    )
    include_sources: bool = Field(
        default=True,
        description="Append source references to final answer",
    )

    # Research → mnemory evidence persistence (runs once on completion)
    evidence_memory_enabled: bool = Field(
        default=True,
        description="On completion of a research run, persist the verified "
                    "finding to mnemory as a self-describing EV:research "
                    "evidence memory (with provenance header + sources "
                    "artifact). Never writes per-iteration queries.",
    )
    evidence_memory_quick_research: bool = Field(
        default=True,
        description="Persist evidence from quick research() too. Default "
                    "ON so the cache covers every research entrypoint "
                    "(a later 'use research' on the same question hits "
                    "the cache regardless of which tool first ran it).",
    )
    evidence_cache_enabled: bool = Field(
        default=True,
        description="Before a research run, check mnemory for a prior "
                    "EV:research memory for the SAME request "
                    "(research_key match). On hit, return the stored "
                    "finding + report instead of researching (stale hits "
                    "flagged). refresh=True bypasses the cache and "
                    "supersedes the memory in place.",
    )
    mnemory_url: str = Field(
        default="http://mnemory:8050",
        description="mnemory base URL (reached directly on the internal "
                    "llm-net; trusted writer path).",
    )
    mnemory_api_key: str = Field(
        default="mN3m0ry!-mcp",
        description="mnemory API key for the evidence writer. Empty "
                    "disables persistence.",
    )
    mnemory_user_id: str = Field(
        default="",
        description="Fallback mnemory X-User-Id when the OWUI user has no "
                    "email/id (else evidence is skipped).",
    )
    evidence_volatility_days: str = Field(
        default="fast:7,medium:180,slow:1095",
        description="Re-validation windows per volatility tier. Past the "
                    "window the LLM downgrades the fact to an educated "
                    "guess (re-validation due).",
    )

    # Deep research specific
    top_k_per_collection: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Chunks retrieved per collection per query",
    )
    max_collections: int = Field(
        default=5,
        ge=1,
        le=50,
        description="Max collections to search",
    )
    max_domains: int = Field(
        default=3,
        ge=1,
        le=20,
        description="Max domains to discover via web search",
    )
    auto_approve_domains: bool = Field(
        default=True,
        description="Auto-approve all non-covered domains (skip manual approval)",
    )

    # Context management
    max_prompt_tokens: int = Field(
        default=196000,
        ge=1000,
        le=262144,
        description="Token budget for SubAgent prompts. Should be your model's "
                    "context window minus ~4000 (response reserve). "
                    "Default 196000 suits Qwen3 235B/30B-A3B 200k context models. "
                    "Set lower for smaller models (e.g. 28000 for 32k context, "
                    "4000 for 8k context). Max 262144 for 256k models.",
    )
    max_chunks_per_iteration: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max RAG chunks included in LLM summarization per iteration",
    )
    skip_verification: bool = Field(
        default=False,
        description="Skip LLM verification/remediation passes (saves 2 LLM calls, "
                    "faster for small models)",
    )

    # Fileshed integration
    fileshed_compatible: bool = Field(
        default=True,
        description="Write journal to Fileshed Storage zone",
    )
    storage_base_path: str = Field(
        default="/app/backend/data/user_files",
        description="Base path for Fileshed-compatible storage",
    )
    save_journal: bool = Field(
        default=True,
        description="Persist research journal to disk",
    )


@dataclass
class DiscoveredDomain:
    """A domain discovered via web search with LLM scoring."""
    url: str
    domain: str
    score: float
    rationale: str
    already_covered: bool = False
    existing_collection_id: Optional[str] = None


@dataclass
class CrawlResult:
    """Result of crawling a single domain via SmolCrawl."""
    domain: str
    kb_name: str
    kb_id: str = ""
    pages_crawled: int = 0
    success: bool = False
    error: str = ""
    duration_seconds: float = 0.0


@dataclass
class RetrievedChunk:
    """A single RAG chunk retrieved from a knowledge collection."""
    content: str
    collection_id: str
    collection_name: str
    source: str = ""
    distance: float = 0.0

    @property
    def chunk_hash(self) -> str:
        """Content-based hash for deduplication."""
        return hashlib.sha256(self.content.encode()).hexdigest()[:16]


@dataclass
class IterationResult:
    """Result of a single RAG research iteration."""
    iteration_number: int
    search_terms: List[str]
    collections_queried: List[str]
    chunks_found: int
    new_chunks: int
    summary: str = ""
    new_concepts: List[str] = field(default_factory=list)


@dataclass
class ResearchSession:
    """Mutable state for an in-progress research session."""
    session_id: str
    query: str
    session_dir: str
    phase: ResearchPhase = ResearchPhase.INITIALIZING
    discovered_domains: List[DiscoveredDomain] = field(default_factory=list)
    crawl_results: List[CrawlResult] = field(default_factory=list)
    iterations: List[IterationResult] = field(default_factory=list)
    relevant_collection_ids: List[str] = field(default_factory=list)
    seen_chunk_keys: set = field(default_factory=set)
    created_at: datetime = field(default_factory=datetime.utcnow)
    model_id: str = ""
    anchor: str = ""

    def add_seen_chunk(self, collection_id: str, chunk_hash: str) -> bool:
        """Track a chunk as seen. Returns True if it was new."""
        key = (collection_id, chunk_hash)
        if key in self.seen_chunk_keys:
            return False
        self.seen_chunk_keys.add(key)
        return True
