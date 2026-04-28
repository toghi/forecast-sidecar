"""Shared test helpers (not auto-collected by pytest)."""

from __future__ import annotations

import io
import json
from typing import Any

import joblib
import pandas as pd
from lightgbm import LGBMRegressor
from mlforecast import MLForecast
from mlforecast.utils import PredictionIntervals

from forecast_sidecar.config import Settings
from forecast_sidecar.storage import GCSStorage


def _build_model(history_df: pd.DataFrame, *, h: int, n_windows: int) -> MLForecast:
    mlf = MLForecast(
        models=[
            LGBMRegressor(
                deterministic=True,
                seed=42,
                num_threads=1,
                verbosity=-1,
                n_estimators=50,
                learning_rate=0.1,
                num_leaves=15,
                min_data_in_leaf=2,
            )
        ],
        freq="MS",
        lags=[1, 3, 6, 12],
        date_features=["month"],
        num_threads=1,
    )
    mlf.fit(
        history_df,
        static_features=[],
        prediction_intervals=PredictionIntervals(n_windows=n_windows, h=h),
    )
    return mlf


def train_and_seed_model(
    *,
    settings: Settings,
    history: pd.DataFrame,
    company_id: str,
    computed_object_id: str,
    version: int = 1,
    h: int = 12,
    n_windows: int = 5,
    feature_config_overrides: dict[str, Any] | None = None,
) -> tuple[MLForecast, dict[str, Any]]:
    """Train a small MLForecast on the provided history and seed it into
    GCS at v{version}/, plus a `latest.json` pointer."""
    history = history.copy()
    history["unique_id"] = f"{company_id}/{computed_object_id}"
    # Phase-3 fixture trains a deliberately small model: lags + date_features
    # + one numeric future_exog (active_clients). Phase 4's real trainer
    # consumes feature_config.json to wire up categoricals/static features.
    cols_to_drop = [c for c in ("calls", "segment", "region", "bizdev_id") if c in history.columns]
    if cols_to_drop:
        history = history.drop(columns=cols_to_drop)

    mlf = _build_model(history, h=h, n_windows=n_windows)

    feature_config = {
        "freq": "MS",
        "target": "y",
        "horizon": h,
        "min_history_periods": 18,
        "static_features": [],
        "historic_exog": [],
        "future_exog": ["active_clients"],
        "categorical_features": [],
        "lags": [1, 3, 6, 12],
        "date_features": ["month"],
    }
    if feature_config_overrides:
        feature_config.update(feature_config_overrides)

    metadata: dict[str, Any] = {
        "version": version,
        "trained_at": "2026-04-29T00:00:00Z",
        "training_window": {
            "from": "2024-01-01",
            "to": "2025-12-01",
            "n_periods": len(history) // history["unique_id"].nunique(),
            "n_series": int(history["unique_id"].nunique()),
        },
        "feature_config": feature_config,
        "metrics": {
            "model": {"mae": 12.34, "smape": 0.087, "coverage_80": 0.81, "coverage_95": 0.94},
            "baseline": {"name": "SeasonalNaive", "season_length": 12, "smape": 0.105},
        },
        "library_versions": {"python": "3.11", "mlforecast": "1.0", "lightgbm": "4.5"},
        "git_sha": "test-sha",
    }

    buf = io.BytesIO()
    joblib.dump(mlf, buf)

    storage = GCSStorage(settings)
    storage.write_model_bundle(
        company_id,
        computed_object_id,
        version,
        model_bytes=buf.getvalue(),
        metadata=metadata,
    )
    existing = storage.read_latest_pointer(company_id, computed_object_id)
    expected_gen = existing[1] if existing is not None else 0
    storage.write_latest_pointer_cas(
        company_id,
        computed_object_id,
        {
            "version": version,
            "trained_at": metadata["trained_at"],
            "model_path": f"forecasts/{company_id}/{computed_object_id}/v{version}/model.pkl",
        },
        expected_generation=expected_gen,
    )
    return mlf, metadata


def history_for_single_series(synthetic_series_pl: Any, unique_id: str) -> pd.DataFrame:
    """Take the synthetic_series fixture (polars), filter to one series, and
    return as pandas with the canonical (unique_id, ds, y, ...) shape."""
    s0 = synthetic_series_pl.filter(synthetic_series_pl["unique_id"] == "s_0").to_pandas()
    s0 = s0.rename(columns={})
    s0["unique_id"] = unique_id
    s0["ds"] = pd.to_datetime(s0["ds"])
    return s0


def load_sample_request(path: Any) -> dict[str, Any]:
    return json.loads(path.read_text())
