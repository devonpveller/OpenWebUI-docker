"""Shared FastAPI dependencies: settings, rotation engine, bearer auth.

No global mutable state except the rotation engine on app.state (spec §11).
"""

from __future__ import annotations

import secrets

from fastapi import Depends, Header, HTTPException, Request, status

from gateway.config import Settings, get_settings
from gateway.rotation import RotationEngine


def get_engine(request: Request) -> RotationEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover - only if lifespan failed
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="rotation engine not initialized",
        )
    return engine


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_bearer(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Spec §4.4.2: every endpoint except /healthz, /readyz and the
    network-isolated SearXNG-compat shim requires a valid bearer token."""
    token = _bearer_token(authorization)
    if token is None or not secrets.compare_digest(token, settings.gateway_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
