"""T034 — feature_config → mlforecast kwargs."""

from __future__ import annotations

from typing import Any

from forecast_sidecar.model.features import (
    build_mlforecast_kwargs,
    categorical_columns,
    infer_seasonality,
)


def test_seasonality_for_monthly_is_12() -> None:
    assert infer_seasonality("MS") == 12
    assert infer_seasonality("M") == 12


def test_seasonality_for_daily_is_7() -> None:
    assert infer_seasonality("D") == 7


def test_seasonality_for_quarterly_is_4() -> None:
    assert infer_seasonality("QS") == 4


def test_categorical_columns_extracted(sample_feature_config: dict[str, Any]) -> None:
    assert categorical_columns(sample_feature_config) == ["segment", "region", "bizdev_id"]


def test_kwargs_passes_lags_through(sample_feature_config: dict[str, Any]) -> None:
    kw = build_mlforecast_kwargs(sample_feature_config)
    assert kw["freq"] == "MS"
    assert kw["lags"] == [1, 3, 6, 12]
    assert kw["date_features"] == ["month", "quarter"]


def test_kwargs_builds_lag_transform_instances(sample_feature_config: dict[str, Any]) -> None:
    kw = build_mlforecast_kwargs(sample_feature_config)
    assert 1 in kw["lag_transforms"]
    transforms = kw["lag_transforms"][1]
    # Two RollingMean transforms with different windows
    assert len(transforms) == 2
    types = {type(t).__name__ for t in transforms}
    assert types == {"RollingMean"}


def test_kwargs_builds_target_transforms(sample_feature_config: dict[str, Any]) -> None:
    kw = build_mlforecast_kwargs(sample_feature_config)
    transforms = kw["target_transforms"]
    assert len(transforms) == 1
    assert type(transforms[0]).__name__ == "Differences"


def test_empty_optional_fields_yield_empty_collections() -> None:
    minimal = {
        "freq": "D",
        "lags": [1, 7],
    }
    kw = build_mlforecast_kwargs(minimal)
    assert kw["lag_transforms"] == {}
    assert kw["date_features"] == []
    assert kw["target_transforms"] == []
