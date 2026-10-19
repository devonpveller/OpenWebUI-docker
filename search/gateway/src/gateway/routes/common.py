"""What every search surface shares: the screened search, and the fail-closed check."""

from __future__ import annotations

from fastapi import HTTPException, status

from gateway.deps import GateRuntime
from gateway.models import GateSearchResponse, ScreenedResult, SearchRequest
from gateway.rotation import RotationEngine


def require_screening(gate: GateRuntime) -> None:
    """A screened caller is never served unscreened: no intake key -> 503 (fail closed)."""
    if not gate.screening_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="screening is not configured; nothing is served unscreened",
        )


async def screened_search(engine: RotationEngine, gate: GateRuntime,
                          req: SearchRequest) -> GateSearchResponse:
    """Search, then hand every result (title + snippet) to intake; keep only what it labels clean.
    Raises AllProvidersFailed like engine.search."""
    require_screening(gate)
    resp = await engine.search(req)
    kept, withheld = await gate.screen.results(resp.results)
    return GateSearchResponse(
        query=resp.query,
        provider_used=resp.provider_used,
        cached=resp.cached,
        withheld=withheld,
        results=[
            ScreenedResult(
                title=s.title or str(r.url), url=r.url, snippet=s.text, fenced=s.fenced,
                labels=s.labels, published_at=r.published_at, score=r.score,
                source_engine=r.source_engine, provider=r.provider,
            )
            for r, s in kept
        ],
    )
