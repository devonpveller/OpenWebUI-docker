"""
Iterative RAG research with term expansion and deduplication.

Single Responsibility: Only handles querying knowledge collections and expanding terms.
Encapsulation: OWUI API details and chunk deduplication are internal.
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

from .models import IterationResult, RetrievedChunk, ResearchSession, Valves
from .sub_agent import SubAgent
from .context_budget import condense_iterations

# Internal OWUI imports — available when Tool runs inside OWUI process.
# Guarded by try/except for Pipeline deployments (separate container).
try:
    from open_webui.retrieval.utils import (
        query_collection as _owui_query_collection,
        query_collection_with_hybrid_search as _owui_query_hybrid,
    )
    from open_webui.config import RAG_EMBEDDING_QUERY_PREFIX

    _HAS_OWUI_INTERNALS = True
except ImportError:
    _HAS_OWUI_INTERNALS = False

logger = logging.getLogger("deep_research.rag_research")

_EXPANSION_SYSTEM_PROMPT = """\
You are a research assistant analyzing RAG retrieval results.

Compare the retrieved content against the RESEARCH ANCHOR provided.
Identify:
1. What aspects of the query these results address well
2. What specific aspects of the original query remain UNCOVERED
3. New search terms that target the uncovered aspects (use the user's terminology)
4. Adjacent concepts discovered that are still relevant to the original query

Return JSON: {"terms": ["terms targeting gaps"], "concepts": ["relevant concepts found"], "summary": "2-3 paragraph summary", "uncovered": ["aspects of original query not yet addressed"]}\
"""

_CONTINUE_SYSTEM_PROMPT = """\
Evaluate whether another research iteration would be valuable.
Continue if: key aspects of the original query remain uncovered, OR \
promising new terms haven't been explored yet.
Stop if: the original query's main concepts are well-covered.

Return JSON: {"continue": true/false, "rationale": "one sentence", "uncovered": ["remaining gaps if any"]}\
"""


class RagResearcher:
    """Performs iterative RAG queries across OWUI knowledge collections.

    Queries multiple collections with expanding search terms, deduplicates
    chunks, and uses an LLM sub-agent for term expansion and continue
    decisions.

    When a ``request`` object is available (OWUI Tool deployment), API
    calls go through the ASGI app directly — no network needed. Falls
    back to HTTP via ``owui_base_url`` for Pipeline deployments.
    """

    def __init__(self, valves: Valves, sub_agent: SubAgent):
        self._valves = valves
        self._sub_agent = sub_agent

    # ------------------------------------------------------------------
    # Internal HTTP / ASGI transport
    # ------------------------------------------------------------------

    def _build_auth_headers(self, request: Any = None) -> Dict:
        """Build auth headers from Valves or forward from request."""
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self._valves.owui_api_key:
            headers["Authorization"] = f"Bearer {self._valves.owui_api_key}"
        elif request:
            auth = getattr(request, "headers", {})
            if hasattr(auth, "get"):
                val = auth.get("authorization", "")
                if val:
                    headers["Authorization"] = val
        return headers

    async def _get(
        self, path: str, request: Any = None
    ) -> httpx.Response:
        """GET an OWUI API endpoint, preferring internal ASGI transport."""
        headers = self._build_auth_headers(request)
        cookies = dict(request.cookies) if request and hasattr(request, "cookies") else {}

        # Try 1: ASGI transport (Tool runs inside OWUI — no network)
        if request and hasattr(request, "app"):
            try:
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://internal",
                    headers=headers,
                    cookies=cookies,
                    timeout=30.0,
                ) as client:
                    resp = await client.get(path)
                    resp.raise_for_status()
                    return resp
            except Exception as e:
                logger.debug(
                    "ASGI transport GET %s failed, falling back to HTTP: %s",
                    path, e,
                )

        # Try 2: HTTP to configured owui_base_url
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{self._valves.owui_base_url}{path}",
                headers=headers,
            )
            resp.raise_for_status()
            return resp

    async def _post(
        self, path: str, json_body: Dict, request: Any = None
    ) -> httpx.Response:
        """POST to an OWUI API endpoint, preferring internal ASGI transport."""
        headers = self._build_auth_headers(request)
        cookies = dict(request.cookies) if request and hasattr(request, "cookies") else {}

        # Try 1: ASGI transport
        if request and hasattr(request, "app"):
            try:
                transport = httpx.ASGITransport(app=request.app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://internal",
                    headers=headers,
                    cookies=cookies,
                    timeout=30.0,
                ) as client:
                    resp = await client.post(path, json=json_body)
                    resp.raise_for_status()
                    return resp
            except Exception as e:
                logger.debug(
                    "ASGI transport POST %s failed, falling back to HTTP: %s",
                    path, e,
                )

        # Try 2: HTTP fallback
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{self._valves.owui_base_url}{path}",
                headers=headers,
                json=json_body,
            )
            resp.raise_for_status()
            return resp

    # ------------------------------------------------------------------
    # Collection listing & querying
    # ------------------------------------------------------------------

    async def list_collections(self, request: Any = None) -> tuple:
        """List all knowledge collections from OWUI.

        Args:
            request: OWUI ``__request__`` object. When provided, the call
                goes through the ASGI app directly (no network).

        Returns:
            Tuple of (list_of_collections, error_string).
            On success error_string is empty. On failure the list is empty
            and error_string describes the problem.
        """
        try:
            response = await self._get("/api/v1/knowledge/", request)
            data = response.json()

            # Handle both list and paginated dict responses
            if isinstance(data, dict):
                return data.get("items", []), ""
            return data, ""

        except httpx.HTTPStatusError as e:
            msg = f"OWUI API returned HTTP {e.response.status_code}"
            logger.error("Failed to list collections: %s", msg)
            return [], msg
        except httpx.ConnectError as e:
            msg = f"Cannot connect to OWUI at {self._valves.owui_base_url}: {e}"
            logger.error("Failed to list collections: %s", msg)
            return [], msg
        except Exception as e:
            msg = f"Failed to list collections: {e}"
            logger.error(msg)
            return [], msg

    async def query_collection(
        self,
        collection_id: str,
        query: str,
        collection_name: str = "",
        k_override: Optional[int] = None,
        request: Any = None,
        file_ids: Optional[List[str]] = None,
    ) -> List[RetrievedChunk]:
        """Query a knowledge collection via OWUI's retrieval internals.

        Primary path: imports OWUI's ``query_collection`` directly —
        zero HTTP overhead, same code path as OWUI's own chat.
        Fallback: ASGI POST to ``/api/v1/retrieval/query/collection``.

        OWUI stores vector embeddings per-file under collection names
        ``file-{file_id}``.  When ``file_ids`` is provided, each file
        is prefixed accordingly.  When omitted, ``collection_id`` (the
        KB UUID) is used directly.

        Args:
            collection_id: UUID of the knowledge base (used for attribution).
            query: Natural language query string.
            collection_name: Human-readable name for logging.
            k_override: Override the default top-k value.
            request: OWUI ``__request__`` object for internal transport.
            file_ids: File UUIDs belonging to this KB. When provided,
                each file is queried as ``file-{file_id}``.

        Returns:
            List of RetrievedChunk objects.
        """
        effective_k = k_override if k_override is not None else self._valves.top_k_per_collection

        # Build vector-store collection names (OWUI convention: file-{uuid})
        if file_ids:
            target_names = [f"file-{fid}" for fid in file_ids]
        else:
            target_names = [collection_id]

        # --- Primary: OWUI internal import (Tool runs inside OWUI) ---
        if (
            _HAS_OWUI_INTERNALS
            and request
            and hasattr(request, "app")
            and hasattr(request.app.state, "EMBEDDING_FUNCTION")
        ):
            try:
                embedding_fn = (
                    lambda query_texts, prefix: request.app.state.EMBEDDING_FUNCTION(
                        query_texts, prefix=prefix
                    )
                )
                result = await _owui_query_collection(
                    request,
                    collection_names=target_names,
                    queries=[query],
                    embedding_function=embedding_fn,
                    k=effective_k,
                )
                chunks = self._parse_retrieval_response(
                    result, collection_id, collection_name
                )
                logger.debug(
                    "Internal query OK: %d chunks from %s (%d targets)",
                    len(chunks), collection_name or collection_id,
                    len(target_names),
                )
                return chunks
            except Exception as e:
                logger.warning(
                    "Internal query_collection failed for %s, "
                    "falling back to ASGI: %s",
                    collection_name or collection_id, e,
                )

        # --- Fallback: ASGI / HTTP to /api/v1/retrieval/query/collection ---
        try:
            response = await self._post(
                "/api/v1/retrieval/query/collection",
                {
                    "collection_names": target_names,
                    "query": query,
                    "k": effective_k,
                    "r": 0.0,
                },
                request,
            )
            return self._parse_retrieval_response(
                response.json(), collection_id, collection_name
            )
        except (httpx.HTTPError, Exception) as e:
            logger.debug(
                "ASGI/HTTP query failed for %s (KB %s): %s",
                target_names, collection_id, e,
            )
            return []

    async def run_iteration(
        self,
        session: ResearchSession,
        search_terms: List[str],
        collection_ids: List[str],
        collection_names: Dict[str, str],
        iteration_number: int,
        request: Any,
        user: Dict,
        k_override: Optional[int] = None,
        file_ids_map: Optional[Dict[str, List[str]]] = None,
    ) -> IterationResult:
        """Execute a single research iteration: query + summarize.

        Args:
            session: The active research session (for deduplication state).
            search_terms: Terms to query across collections.
            collection_ids: UUIDs of knowledge bases to search.
            collection_names: Mapping of KB ID to human-readable name.
            iteration_number: Current iteration index (1-based).
            request: OWUI __request__ object.
            user: OWUI __user__ dict.
            k_override: Override the default top-k value.
            file_ids_map: Mapping of KB ID to its file UUIDs. When provided,
                queries are made against individual file collections instead
                of the KB UUID directly.

        Returns:
            IterationResult with findings and LLM summary.
        """
        all_chunks: List[RetrievedChunk] = []
        new_chunks: List[RetrievedChunk] = []

        # Query each (term × collection) pair. Pairs are independent → run
        # them concurrently, bounded so the single embedding server isn't
        # overloaded. Results are processed in submission order so chunk
        # deduplication stays deterministic.
        limit = max(1, getattr(self._valves, "max_parallel_queries", 5))
        sem = asyncio.Semaphore(limit)
        pairs = [
            (term, col_id)
            for term in search_terms
            for col_id in collection_ids
        ]

        async def _query(term: str, col_id: str):
            async with sem:
                return await self.query_collection(
                    collection_id=col_id,
                    query=term,
                    collection_name=collection_names.get(col_id, col_id),
                    k_override=k_override,
                    request=request,
                    file_ids=(file_ids_map or {}).get(col_id),
                )

        results = await asyncio.gather(
            *[_query(term, col_id) for term, col_id in pairs],
            return_exceptions=True,
        )
        for (_, col_id), chunks in zip(pairs, results):
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                all_chunks.append(chunk)
                if session.add_seen_chunk(col_id, chunk.chunk_hash):
                    new_chunks.append(chunk)

        # Build context for LLM summarization — cap total chunk text
        # to fit within the prompt budget alongside the anchor and overhead
        from .context_budget import usable_budget_chars
        max_chunks = getattr(self._valves, 'max_chunks_per_iteration', 10)
        chunk_budget = usable_budget_chars(
            self._valves.max_prompt_tokens
        ) - len(session.anchor) - 1000  # reserve for anchor + system prompt

        chunk_parts = []
        used = 0
        for c in new_chunks[:max_chunks]:
            part = f"**[{c.collection_name}]** ({c.source})\n{c.content}"
            if used + len(part) > chunk_budget and chunk_parts:
                chunk_parts.append(
                    f"*[{len(new_chunks) - len(chunk_parts)} more chunk(s) "
                    f"omitted — details in journal]*"
                )
                break
            chunk_parts.append(part)
            used += len(part)
        chunk_text = "\n\n---\n\n".join(chunk_parts)

        # Get LLM summary + new concepts
        summary = ""
        new_concepts: List[str] = []

        if new_chunks:
            try:
                result = await self._sub_agent.run_json(
                    system_prompt=_EXPANSION_SYSTEM_PROMPT,
                    user_prompt=(
                        f"{session.anchor}\n\n"
                        f"Search terms used: {', '.join(search_terms)}\n\n"
                        f"Retrieved content ({len(new_chunks)} new chunks):\n\n"
                        f"{chunk_text}"
                    ),
                    request=request,
                    user=user,
                )
                summary = result.get("summary", "")
                new_concepts = result.get("concepts", [])
            except (ValueError, Exception) as e:
                logger.warning("Expansion analysis failed: %s", e)
                summary = f"Found {len(new_chunks)} new chunks across {len(collection_ids)} collections."

        iteration = IterationResult(
            iteration_number=iteration_number,
            search_terms=search_terms,
            collections_queried=[
                collection_names.get(c, c) for c in collection_ids
            ],
            chunks_found=len(all_chunks),
            new_chunks=len(new_chunks),
            summary=summary,
            new_concepts=new_concepts,
        )

        session.iterations.append(iteration)
        return iteration

    async def expand_terms(
        self,
        session: ResearchSession,
        current_terms: List[str],
        request: Any,
        user: Dict,
    ) -> List[str]:
        """Use LLM to generate expanded search terms from accumulated findings.

        Args:
            session: The research session with iteration history.
            current_terms: Current search terms to extend.
            request: OWUI __request__ object.
            user: OWUI __user__ dict.

        Returns:
            List of new search terms for the next iteration.
        """
        iteration_context = condense_iterations(session.iterations)

        try:
            result = await self._sub_agent.run_json(
                system_prompt=_EXPANSION_SYSTEM_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Previous search terms: {', '.join(current_terms)}\n\n"
                    f"Findings so far:\n{iteration_context}\n\n"
                    f"Suggest new search terms that address uncovered aspects "
                    f"per the anchor above."
                ),
                request=request,
                user=user,
            )
            return result.get("terms", current_terms)
        except (ValueError, Exception) as e:
            logger.warning("Term expansion failed: %s", e)
            return current_terms

    async def should_continue(
        self,
        session: ResearchSession,
        request: Any,
        user: Dict,
    ) -> bool:
        """Ask LLM whether another iteration would yield meaningful results.

        Args:
            session: The research session with iteration history.
            request: OWUI __request__ object.
            user: OWUI __user__ dict.

        Returns:
            True if the LLM recommends continuing.
        """
        iteration_context = condense_iterations(session.iterations)

        try:
            result = await self._sub_agent.run_json(
                system_prompt=_CONTINUE_SYSTEM_PROMPT,
                user_prompt=(
                    f"{session.anchor}\n\n"
                    f"Iteration results:\n{iteration_context}"
                ),
                request=request,
                user=user,
            )
            should = result.get("continue", False)
            rationale = result.get("rationale", "")
            logger.info(
                "Continue decision: %s — %s", should, rationale
            )
            return bool(should)
        except (ValueError, Exception) as e:
            logger.warning("Continue decision failed: %s", e)
            return False

    @staticmethod
    def _parse_retrieval_response(
        data: Dict,
        collection_id: str,
        collection_name: str,
    ) -> List[RetrievedChunk]:
        """Parse OWUI retrieval API response into RetrievedChunk objects."""
        chunks = []
        documents = data.get("documents", [[]])
        metadatas = data.get("metadatas", [[]])
        distances = data.get("distances", [[]])

        if not documents or not documents[0]:
            return chunks

        for i, doc_text in enumerate(documents[0]):
            metadata = metadatas[0][i] if i < len(metadatas[0]) else {}
            distance = distances[0][i] if i < len(distances[0]) else 0.0

            chunks.append(
                RetrievedChunk(
                    content=doc_text,
                    collection_id=collection_id,
                    collection_name=collection_name,
                    source=metadata.get("source", ""),
                    distance=distance,
                )
            )

        return chunks
