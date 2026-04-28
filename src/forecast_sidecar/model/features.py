"""Map a per-(company, CO) `feature_config.json` to mlforecast constructor
kwargs. Constitution Principle II: temporal feature engineering routes
exclusively through mlforecast primitives."""

from __future__ import annotations

from typing import Any

# Minimal seasonality lookup; covers the freqs we declare in
# `contracts/feature_config.schema.json`. Used by SeasonalNaive (Constitution IV)
# and as a default for any callers that need seasonality.
_FREQ_TO_SEASONALITY: dict[str, int] = {
    "H": 24,
    "D": 7,
    "W-MON": 52,
    "W-SUN": 52,
    "MS": 12,
    "M": 12,
    "QS": 4,
    "Q": 4,
    "AS": 1,
    "A": 1,
}


def infer_seasonality(freq: str) -> int:
    if freq not in _FREQ_TO_SEASONALITY:
        msg = f"unsupported freq: {freq}"
        raise ValueError(msg)
    return _FREQ_TO_SEASONALITY[freq]


def categorical_columns(feature_config: dict[str, Any]) -> list[str]:
    return list(feature_config.get("categorical_features", []))


def _build_lag_transforms(raw: dict[str, Any]) -> dict[int, list[Any]]:
    if not raw:
        return {}
    from mlforecast.lag_transforms import (
        ExpandingMean,
        ExponentiallyWeightedMean,
        RollingMax,
        RollingMean,
        RollingMin,
        RollingStd,
    )

    name_to_class: dict[str, Any] = {
        "rolling_mean": RollingMean,
        "rolling_std": RollingStd,
        "rolling_min": RollingMin,
        "rolling_max": RollingMax,
        "expanding_mean": ExpandingMean,
        "ewm_mean": ExponentiallyWeightedMean,
    }

    out: dict[int, list[Any]] = {}
    for lag_str, transforms in raw.items():
        lag_int = int(lag_str)
        instances: list[Any] = []
        for spec in transforms:
            cls = name_to_class[spec["name"]]
            kwargs = {k: v for k, v in spec.items() if k != "name"}
            instances.append(cls(**kwargs))
        out[lag_int] = instances
    return out


def _build_target_transforms(raw: list[dict[str, Any]]) -> list[Any]:
    if not raw:
        return []
    from mlforecast.target_transforms import (
        Differences,
        LocalBoxCox,
        LocalMinMaxScaler,
        LocalStandardScaler,
    )

    name_to_class: dict[str, Any] = {
        "Differences": Differences,
        "LocalStandardScaler": LocalStandardScaler,
        "LocalMinMaxScaler": LocalMinMaxScaler,
        "LocalBoxCox": LocalBoxCox,
    }

    out: list[Any] = []
    for spec in raw:
        cls = name_to_class[spec["name"]]
        args = spec.get("args", [])
        out.append(cls(*args))
    return out


def build_mlforecast_kwargs(feature_config: dict[str, Any]) -> dict[str, Any]:
    """Translate the JSON feature_config into kwargs for `MLForecast(...)`.
    Caller adds `models=...` separately."""
    return {
        "freq": feature_config["freq"],
        "lags": list(feature_config.get("lags", [])),
        "lag_transforms": _build_lag_transforms(feature_config.get("lag_transforms", {})),
        "date_features": list(feature_config.get("date_features", [])),
        "target_transforms": _build_target_transforms(feature_config.get("target_transforms", [])),
    }


__all__ = [
    "build_mlforecast_kwargs",
    "categorical_columns",
    "infer_seasonality",
]
