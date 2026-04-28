"""Pydantic v2 wire schemas (data-model.md §1)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    """Allows arbitrary feature columns alongside the declared ones."""

    model_config = ConfigDict(extra="allow")


class FuturePeriodFeatures(_Open):
    period: date


class ForecastRequest(_Strict):
    company_id: UUID
    computed_object_id: UUID
    model_version: int | None = Field(default=None, ge=1)
    horizon_periods: int = Field(ge=1, le=60)
    future_features: list[FuturePeriodFeatures] = Field(min_length=1)
    scenario_overrides: dict[str, dict[str, Any]] | None = None

    @model_validator(mode="after")
    def _validate_horizon_match(self) -> ForecastRequest:
        if len(self.future_features) != self.horizon_periods:
            msg = (
                f"future_features row count ({len(self.future_features)}) "
                f"!= horizon_periods ({self.horizon_periods})"
            )
            raise ValueError(msg)

        periods = [pf.period for pf in self.future_features]
        if len(set(periods)) != len(periods):
            msg = "future_features periods must be unique"
            raise ValueError(msg)
        if periods != sorted(periods):
            msg = "future_features periods must be strictly increasing"
            raise ValueError(msg)

        if self.scenario_overrides:
            allowed = {p.isoformat() for p in periods}
            unknown = set(self.scenario_overrides.keys()) - allowed
            if unknown:
                msg = f"scenario_overrides keys not in future_features periods: {sorted(unknown)}"
                raise ValueError(msg)

        return self


class ForecastPoint(_Strict):
    period: date
    point: float
    lo80: float
    hi80: float
    lo95: float
    hi95: float

    @model_validator(mode="after")
    def _validate_interval_ordering(self) -> ForecastPoint:
        if not (self.lo95 <= self.lo80 <= self.point <= self.hi80 <= self.hi95):
            msg = (
                f"interval invariant violated for period {self.period}: "
                f"lo95={self.lo95} lo80={self.lo80} point={self.point} "
                f"hi80={self.hi80} hi95={self.hi95}"
            )
            raise ValueError(msg)
        return self


class ModelMetricsSummary(_Strict):
    training_mae: float
    training_smape: float
    coverage_80: float
    coverage_95: float


class ForecastResponse(_Strict):
    model_version: int
    trained_at: datetime
    forecast: list[ForecastPoint]
    model_metrics: ModelMetricsSummary


class ErrorResponse(_Strict):
    error: Literal[
        "invalid_token",
        "bad_request",
        "not_yet_trained",
        "model_not_found",
        "model_not_ready",
        "storage_unavailable",
    ]
    detail: str
    missing_columns: list[str] | None = None
    expected_rows: int | None = None
    actual_rows: int | None = None


class HealthResponse(_Strict):
    status: Literal["ok"] = "ok"


class ReadyResponse(_Strict):
    status: Literal["ok", "unavailable"]
    gcs_reachable: bool
    models_cached: int = Field(ge=0)
