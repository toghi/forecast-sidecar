"""Inference path. `load_or_fetch` resolves a model from cache or GCS;
`build_forecast_response` runs `MLForecast.predict` with optional scenario
overrides and reshapes the per-(unique_id, ds) result into the wire schema.

Each (company, CO) model is trained on a single `unique_id` derived from
the company/CO pair; the inference response collapses to one
`ForecastPoint` per requested period."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any
from uuid import UUID

import joblib
import pandas as pd

from forecast_sidecar.cache import ModelCache
from forecast_sidecar.schemas import (
    ForecastPoint,
    ForecastRequest,
    ForecastResponse,
    ModelMetricsSummary,
)
from forecast_sidecar.storage import (
    GCSStorage,
    ModelNotFoundError,
    ModelNotReadyError,
    NotYetTrainedError,
)


class MissingFeatureColumnsError(Exception):
    """Future-features payload is missing columns the model needs (FR-004)."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"missing required future_exog columns: {missing}")


class BadScenarioOverrideError(Exception):
    """Scenario override references a feature not in the model's future_exog."""

    def __init__(self, unknown: list[str]) -> None:
        self.unknown = unknown
        super().__init__(f"scenario_overrides reference unknown features: {unknown}")


def unique_id_for(company_id: UUID, computed_object_id: UUID) -> str:
    return f"{company_id}/{computed_object_id}"


async def load_or_fetch(
    cache: ModelCache,
    storage: GCSStorage,
    *,
    company_id: UUID,
    computed_object_id: UUID,
    model_version: int | None,
) -> tuple[Any, dict[str, Any]]:
    company = str(company_id)
    co = str(computed_object_id)

    if model_version is None:
        latest_key = (company, co)

        async def fetch_latest() -> int:
            pointer = storage.read_latest_pointer(company, co)
            if pointer is None:
                raise NotYetTrainedError(f"no model trained for ({company}, {co}) yet")
            payload, _ = pointer
            return int(payload["version"])

        version = await cache.latest_pointer.get_or_fetch(latest_key, fetch_latest)
    else:
        version = int(model_version)

    model_key = (company, co, version)

    async def fetch_model() -> tuple[Any, dict[str, Any]]:
        if storage.has_error_marker(company, co, version) and not storage.has_model_pkl(
            company, co, version
        ):
            raise ModelNotReadyError(f"v{version} has error.json but no model.pkl")
        try:
            model_bytes = storage.read_model_pkl(company, co, version)
        except ModelNotFoundError:
            if model_version is None:
                raise NotYetTrainedError(
                    f"latest pointer named v{version} but artifact is missing"
                ) from None
            raise

        metadata = storage.read_model_metadata(company, co, version)
        if metadata is None:
            raise ModelNotFoundError(f"v{version}/metadata.json missing")
        model = joblib.load(io.BytesIO(model_bytes))
        return model, metadata

    result: tuple[Any, dict[str, Any]] = await cache.models.get_or_fetch(model_key, fetch_model)
    return result


def _build_future_df(
    request: ForecastRequest,
    *,
    unique_id: str,
    future_exog: list[str],
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for pf in request.future_features:
        row: dict[str, Any] = {"unique_id": unique_id, "ds": pd.Timestamp(pf.period)}
        extras = pf.model_dump(exclude={"period"})
        for col in future_exog:
            if col not in extras:
                continue
            row[col] = extras[col]
        rows.append(row)

    if request.scenario_overrides:
        for period_iso, overrides in request.scenario_overrides.items():
            for row in rows:
                if row["ds"] == pd.Timestamp(period_iso):
                    for k, v in overrides.items():
                        row[k] = v

    return pd.DataFrame(rows)


def _validate_future_features(
    request: ForecastRequest,
    metadata: dict[str, Any],
) -> list[str]:
    feature_config = metadata.get("feature_config", {})
    future_exog: list[str] = list(feature_config.get("future_exog", []))

    if request.future_features:
        provided = set(request.future_features[0].model_dump(exclude={"period"}).keys())
        missing = [c for c in future_exog if c not in provided]
        if missing:
            raise MissingFeatureColumnsError(missing)

    if request.scenario_overrides:
        unknown: set[str] = set()
        for overrides in request.scenario_overrides.values():
            unknown.update(k for k in overrides if k not in future_exog)
        if unknown:
            raise BadScenarioOverrideError(sorted(unknown))

    return future_exog


def build_forecast_response(
    request: ForecastRequest,
    *,
    model: Any,
    metadata: dict[str, Any],
) -> ForecastResponse:
    future_exog = _validate_future_features(request, metadata)
    unique_id = unique_id_for(request.company_id, request.computed_object_id)
    future_df = _build_future_df(request, unique_id=unique_id, future_exog=future_exog)

    raw = model.predict(h=request.horizon_periods, level=[80, 95], X_df=future_df)
    raw = raw.sort_values("ds").reset_index(drop=True)

    model_col = next(
        (c for c in raw.columns if c not in {"unique_id", "ds"} and "-" not in c),
        None,
    )
    if model_col is None:
        msg = f"could not infer point-forecast column from {list(raw.columns)}"
        raise RuntimeError(msg)

    points: list[ForecastPoint] = []
    for _, row in raw.iterrows():
        period = pd.Timestamp(row["ds"]).date()
        points.append(
            ForecastPoint(
                period=period,
                point=float(row[model_col]),
                lo80=float(row[f"{model_col}-lo-80"]),
                hi80=float(row[f"{model_col}-hi-80"]),
                lo95=float(row[f"{model_col}-lo-95"]),
                hi95=float(row[f"{model_col}-hi-95"]),
            )
        )

    metrics_blob = metadata.get("metrics", {}).get("model", {})
    summary = ModelMetricsSummary(
        training_mae=float(metrics_blob.get("mae", 0.0)),
        training_smape=float(metrics_blob.get("smape", 0.0)),
        coverage_80=float(metrics_blob.get("coverage_80", 0.0)),
        coverage_95=float(metrics_blob.get("coverage_95", 0.0)),
    )

    trained_at_raw = metadata.get("trained_at")
    if isinstance(trained_at_raw, str):
        trained_at = datetime.fromisoformat(trained_at_raw.replace("Z", "+00:00"))
    elif isinstance(trained_at_raw, datetime):
        trained_at = trained_at_raw
    else:
        trained_at = datetime.fromtimestamp(0)

    return ForecastResponse(
        model_version=int(metadata["version"]),
        trained_at=trained_at,
        forecast=points,
        model_metrics=summary,
    )


__all__ = [
    "BadScenarioOverrideError",
    "MissingFeatureColumnsError",
    "build_forecast_response",
    "load_or_fetch",
    "unique_id_for",
]
