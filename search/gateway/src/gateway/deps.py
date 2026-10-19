"""Shared FastAPI dependencies: settings, rotation engine, callers, the gate's runtime.

No global mutable state except what the lifespan puts on app.state (spec §11).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request, status

from gateway.callers import Caller, CallerRegistry
from gateway.egress.fetch import Fetcher
from gateway.rotation import RotationEngine
from gateway.screen.gate import Screen


@dataclass
class GateRuntime:
    callers: CallerRegistry
    fetcher: Fetcher
    screen: Screen
    screening_configured: bool


def get_engine(request: Request) -> RotationEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover - only if lifespan failed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rotation engine not initialized",
        )
    return engine


def get_gate(request: Request) -> GateRuntime:
    gate = getattr(request.app.state, "gate", None)
    if gate is None:  # pragma: no cover - only if lifespan failed
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "gate not initialized")
    return gate


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="missing or invalid bearer token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def optional_caller(
    authorization: str | None = Header(default=None),
    gate: GateRuntime = Depends(get_gate),
) -> Caller | None:
    token = _bearer_token(authorization)
    if token is None:
        return None
    caller = gate.callers.identify(token)
    if caller is None:
        raise _unauthorized()
    return caller


def require_caller(caller: Caller | None = Depends(optional_caller)) -> Caller:
    """Any keyed caller: a per-caller key (screened) or GATEWAY_API_KEY (the legacy lane)."""
    if caller is None:
        raise _unauthorized()
    return caller


def require_screened(caller: Caller = Depends(require_caller)) -> Caller:
    """The gate's own routes serve per-caller keys only: there is no unscreened lane to them."""
    if not caller.screened:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this route needs a per-caller key (GATE_CALLER_KEYS_FILE)",
        )
    return caller

