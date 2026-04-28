"""FastAPI app. POST /forecast (US1) is wired here; US3 reuses the same
route via the optional `scenario_overrides` field; US4 adds /healthz +
/readyz."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from forecast_sidecar import __version__
from forecast_sidecar.auth import Claims, verify_oidc_token
from forecast_sidecar.cache import ModelCache
from forecast_sidecar.config import Settings, get_settings
from forecast_sidecar.model.predict import (
    BadScenarioOverrideError,
    MissingFeatureColumnsError,
    build_forecast_response,
    load_or_fetch,
)
from forecast_sidecar.observability import (
    extract_trace_context,
    init_sentry,
    init_structlog,
    tag_sentry_scope,
)
from forecast_sidecar.schemas import ForecastRequest, ForecastResponse
from forecast_sidecar.storage import (
    GCSStorage,
    ModelNotFoundError,
    ModelNotReadyError,
    NotYetTrainedError,
    StorageUnavailableError,
)


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


def _err(
    status_code: int,
    code: str,
    detail: str,
    **extra: Any,
) -> HTTPException:
    body: dict[str, Any] = {"error": code, "detail": detail}
    body.update({k: v for k, v in extra.items() if v is not None})
    return HTTPException(status_code=status_code, detail=body)


@app.post("/forecast", response_model=ForecastResponse)
async def forecast(
    request: ForecastRequest,
    http_request: Request,
    claims: Annotated[Claims, Depends(verify_oidc_token)],
    storage: Annotated[GCSStorage, Depends(get_storage)],
    cache: Annotated[ModelCache, Depends(get_cache)],
) -> ForecastResponse:
    log = structlog.get_logger()
    trace_ctx = extract_trace_context(http_request.headers)
    structlog.contextvars.bind_contextvars(
        request_id=http_request.headers.get("x-request-id", ""),
        company_id=str(request.company_id),
        computed_object_id=str(request.computed_object_id),
        caller=claims.email,
        **trace_ctx,
    )
    started = time.monotonic()

    with tag_sentry_scope(
        company_id=str(request.company_id),
        computed_object_id=str(request.computed_object_id),
        mode="service",
    ):
        try:
            model, metadata = await load_or_fetch(
                cache,
                storage,
                company_id=request.company_id,
                computed_object_id=request.computed_object_id,
                model_version=request.model_version,
            )
            cache_hit_indicator = 1  # populated; refined when cache stats land
            response = build_forecast_response(request, model=model, metadata=metadata)
        except NotYetTrainedError as exc:
            raise _err(
                status.HTTP_404_NOT_FOUND,
                "not_yet_trained",
                str(exc),
            ) from exc
        except ModelNotFoundError as exc:
            raise _err(
                status.HTTP_404_NOT_FOUND,
                "model_not_found",
                str(exc),
            ) from exc
        except ModelNotReadyError as exc:
            raise _err(
                status.HTTP_409_CONFLICT,
                "model_not_ready",
                str(exc),
            ) from exc
        except StorageUnavailableError as exc:
            raise _err(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "storage_unavailable",
                str(exc),
            ) from exc
        except MissingFeatureColumnsError as exc:
            raise _err(
                status.HTTP_400_BAD_REQUEST,
                "bad_request",
                str(exc),
                missing_columns=exc.missing,
            ) from exc
        except BadScenarioOverrideError as exc:
            raise _err(
                status.HTTP_400_BAD_REQUEST,
                "bad_request",
                str(exc),
            ) from exc

        latency_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "forecast.served",
            model_version=response.model_version,
            cache_hit=cache_hit_indicator,
            latency_ms=latency_ms,
            status=200,
        )
        return response


@app.exception_handler(HTTPException)
async def _http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    body = exc.detail if isinstance(exc.detail, dict) else {"error": "bad_request", "detail": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content=body)


__all__ = ["Depends", "app", "create_app", "get_app_settings", "get_cache", "get_storage"]
