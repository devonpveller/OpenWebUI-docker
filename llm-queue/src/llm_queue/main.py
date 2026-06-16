"""FastAPI app entry. Builds the AppState once at startup (single in-process
authoritative queue, design §4.4) and wires the data + control + health routers."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .app_state import build_state
from .config import get_settings
from .logging import configure_logging, get_logger
from .routes import control, data, health, observe


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    log = get_logger("llm_queue.main")

    state = build_state()
    await state.start()
    app.state.app = state
    log.info(
        "llm_queue_started",
        slots=settings.slots,
        max_in_flight=settings.max_in_flight,
        backstop_depth=settings.backstop_depth,
        max_total_connections=settings.max_total_connections,
        enforce_budget=settings.enforce_budget,
        upstream=settings.upstream_base_url,
    )
    try:
        yield
    finally:
        # uvicorn's --timeout-graceful-shutdown stops accepting new connections
        # and lets in-flight requests finish before this runs (design §10.4 drain).
        await state.stop()
        log.info("llm_queue_stopped")


app = FastAPI(
    title="llm-queue",
    version="0.1.0",
    description="B2 front-ended inference admission controller (hold-and-dispatch).",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(data.router)
app.include_router(observe.router)  # GET-only; the ONLY routes bridged to llm-net
app.include_router(control.router)  # incl. mutating verbs — operator-only (§10.3.1)
