"""FastAPI app skeleton. Routes are added in user-story phases (US1: /forecast,
US4: /healthz, /readyz). For Phase 2 the lifespan wires up structlog, Sentry,
GCS, and the model cache and exposes them via dependencies; an internal
`/healthz` stub is published so docker-compose health checks have a target."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from forecast_sidecar import __version__
from forecast_sidecar.cache import ModelCache
from forecast_sidecar.config import Settings, get_settings
from forecast_sidecar.observability import init_sentry, init_structlog
from forecast_sidecar.storage import GCSStorage


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_structlog(settings.log_level)
    init_sentry(settings.sentry_dsn, settings.sentry_environment, release=__version__)

    app.state.settings = settings
    app.state.storage = GCSStorage(settings)
    app.state.cache = ModelCache(settings)

    try:
        yield
    finally:
        pass


def create_app() -> FastAPI:
    return FastAPI(title="forecast-sidecar", version=__version__, lifespan=lifespan)


app = create_app()


def get_storage() -> GCSStorage:
    return app.state.storage  # type: ignore[no-any-return]


def get_cache() -> ModelCache:
    return app.state.cache  # type: ignore[no-any-return]


def get_app_settings() -> Settings:
    return app.state.settings  # type: ignore[no-any-return]


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness stub. The real US4 implementation (T058) returns
    `HealthResponse`; this minimal version unblocks compose health checks."""
    return {"status": "ok"}


__all__ = ["Depends", "app", "create_app", "get_app_settings", "get_cache", "get_storage"]
