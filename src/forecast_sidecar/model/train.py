"""Training pipeline. `validate_history` enforces the data contract from
the feature_config (FR-017, Constitution III). `run_fit_pipeline` builds
an `MLForecast` with `LGBMRegressor`, fits with conformal calibration,
runs CV for evaluation, applies the seasonal-naive gate (Constitution IV),
and returns the fitted model + assembled metadata."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import polars as pl
import yaml
from lightgbm import LGBMRegressor
from mlforecast import MLForecast
from mlforecast.utils import PredictionIntervals

from forecast_sidecar.manifest import compute_data_hash, library_versions
from forecast_sidecar.model.baselines import (
    BaselineGateResult,
    coverage,
    enforce_baseline_gate,
    mae,
    seasonal_naive_forecast,
    smape,
)
from forecast_sidecar.model.features import (
    build_mlforecast_kwargs,
    categorical_columns,
    infer_seasonality,
)


class BadHistoryError(Exception):
    """History data violates the feature_config contract (→ exit code 2)."""


_DEFAULT_LIGHTGBM_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "configs/lightgbm_defaults.yaml"
)

_FIXED_LIGHTGBM_PARAMS: dict[str, Any] = {
    "deterministic": True,
    "force_col_wise": True,
    "verbosity": -1,
}


def _load_lightgbm_defaults() -> dict[str, Any]:
    if not _DEFAULT_LIGHTGBM_CONFIG_PATH.exists():
        return {}
    with _DEFAULT_LIGHTGBM_CONFIG_PATH.open() as f:
        result: dict[str, Any] = yaml.safe_load(f) or {}
        return result


def _resolve_lightgbm_params(feature_config: dict[str, Any], *, seed: int) -> dict[str, Any]:
    params = _load_lightgbm_defaults()
    params.update(feature_config.get("lightgbm_params", {}))
    params.update(_FIXED_LIGHTGBM_PARAMS)
    params["seed"] = seed
    params["num_threads"] = 1
    return params


def validate_history(
    history: pl.DataFrame | pd.DataFrame,
    feature_config: dict[str, Any],
) -> pd.DataFrame:
    """Validate against the feature_config contract; return a pandas
    DataFrame ready for `fit`. Raises `BadHistoryError` on any violation."""
    if isinstance(history, pl.DataFrame):
        df = history.to_pandas()
    else:
        df = history.copy()

    target_col = feature_config["target"]
    required = {"unique_id", "ds", target_col}
    missing = sorted(required - set(df.columns))
    if missing:
        raise BadHistoryError(f"history is missing required columns: {missing}")

    df["ds"] = pd.to_datetime(df["ds"])

    if df[target_col].isna().any():
        bad = df.loc[df[target_col].isna(), ["unique_id", "ds"]].head(5).to_dict("records")
        raise BadHistoryError(f"target column {target_col!r} contains NaN; first 5: {bad}")

    if df.duplicated(subset=["unique_id", "ds"]).any():
        dup = df.loc[df.duplicated(subset=["unique_id", "ds"]), ["unique_id", "ds"]].head(5)
        raise BadHistoryError(f"duplicate (unique_id, ds) rows: {dup.to_dict('records')}")

    out_chunks: list[pd.DataFrame] = []
    min_periods = int(feature_config.get("min_history_periods", 1))
    for sid, group in df.groupby("unique_id", observed=True):
        group_sorted = group.sort_values("ds").reset_index(drop=True)
        if (group_sorted["ds"].diff().dropna() <= pd.Timedelta(0)).any():
            raise BadHistoryError(f"non-monotonic timestamps for unique_id={sid!r}")
        if len(group_sorted) < min_periods:
            raise BadHistoryError(
                f"unique_id={sid!r} has {len(group_sorted)} periods; "
                f"min_history_periods={min_periods}"
            )
        out_chunks.append(group_sorted)

    out = pd.concat(out_chunks, ignore_index=True)
    out = out.rename(columns={target_col: "y"} if target_col != "y" else {})

    for col in categorical_columns(feature_config):
        if col in out.columns:
            out[col] = out[col].astype("category")

    return out


def _filter_to_declared_columns(
    df: pd.DataFrame,
    feature_config: dict[str, Any],
) -> pd.DataFrame:
    """Keep only columns the feature_config declares: id/time/target +
    static_features + future_exog. Drops historic_exog (history-only) and
    anything undeclared so stray object-dtype columns can't leak into
    LightGBM."""
    keep: set[str] = {"unique_id", "ds", "y"}
    keep.update(feature_config.get("static_features", []))
    keep.update(feature_config.get("future_exog", []))
    cols_to_drop = [c for c in df.columns if c not in keep]
    if cols_to_drop:
        df = df.drop(columns=cols_to_drop)
    return df


def _evaluate_models(
    cv_df: pd.DataFrame,
    *,
    model_col: str,
    levels: tuple[int, ...] = (80, 95),
) -> dict[str, Any]:
    y_true = cv_df["y"].to_numpy()
    y_pred = cv_df[model_col].to_numpy()
    metrics: dict[str, Any] = {
        "mae": mae(y_true, y_pred),
        "smape": smape(y_true, y_pred),
    }
    for level in levels:
        lo_col = f"{model_col}-lo-{level}"
        hi_col = f"{model_col}-hi-{level}"
        if lo_col in cv_df.columns and hi_col in cv_df.columns:
            metrics[f"coverage_{level}"] = coverage(
                y_true, cv_df[lo_col].to_numpy(), cv_df[hi_col].to_numpy()
            )
    return metrics


def run_fit_pipeline(
    history: pl.DataFrame | pd.DataFrame,
    feature_config: dict[str, Any],
    *,
    seed: int = 42,
) -> tuple[MLForecast, dict[str, Any], BaselineGateResult]:
    """Validate, fit with intervals, evaluate vs baseline, and return
    `(model, model_metrics_block, gate_result)`. Caller assembles the
    final `metadata.json` with version + training_window + manifest hash."""
    df = validate_history(history, feature_config)
    df = _filter_to_declared_columns(df, feature_config)

    h = int(feature_config.get("horizon", 12))
    n_windows = int(feature_config.get("calibration", {}).get("n_windows", 10))
    season_length = infer_seasonality(feature_config["freq"])

    mlf_kwargs = build_mlforecast_kwargs(feature_config)
    static_features = list(feature_config.get("static_features", []))
    lgb_params = _resolve_lightgbm_params(feature_config, seed=seed)

    mlf = MLForecast(
        models=[LGBMRegressor(**lgb_params)],
        num_threads=1,
        **mlf_kwargs,
    )
    mlf.fit(
        df,
        static_features=static_features,
        prediction_intervals=PredictionIntervals(n_windows=n_windows, h=h),
    )

    cv_df = mlf.cross_validation(
        df=df,
        n_windows=n_windows,
        h=h,
        static_features=static_features,
        level=[80, 95],
    )

    model_col = next(
        (c for c in cv_df.columns if c not in {"unique_id", "ds", "cutoff", "y"} and "-" not in c),
        "LGBMRegressor",
    )
    model_metrics = _evaluate_models(cv_df, model_col=model_col)

    baseline_per_unique: list[dict[str, Any]] = []
    baseline_total_y: list[np.ndarray] = []
    baseline_total_pred: list[np.ndarray] = []
    for sid, group in df.groupby("unique_id", observed=True):
        baseline = seasonal_naive_forecast(group, h=h, season_length=season_length, target_col="y")
        actuals = cv_df[cv_df["unique_id"] == sid].sort_values("ds")
        if len(actuals) == 0:
            continue
        baseline_for_actuals = baseline.iloc[: len(actuals)]
        baseline_per_unique.append(
            {
                "unique_id": str(sid),
                "smape": smape(
                    actuals["y"].to_numpy()[: len(baseline_for_actuals)],
                    baseline_for_actuals["SeasonalNaive"].to_numpy(),
                ),
            }
        )
        baseline_total_y.append(actuals["y"].to_numpy()[: len(baseline_for_actuals)])
        baseline_total_pred.append(baseline_for_actuals["SeasonalNaive"].to_numpy())

    if baseline_total_y:
        y_concat = np.concatenate(baseline_total_y)
        p_concat = np.concatenate(baseline_total_pred)
        baseline_smape = smape(y_concat, p_concat)
        baseline_mae = mae(y_concat, p_concat)
    else:
        baseline_smape = 0.0
        baseline_mae = 0.0

    gate = enforce_baseline_gate(model_smape=model_metrics["smape"], baseline_smape=baseline_smape)

    metrics_block: dict[str, Any] = {
        "model": model_metrics,
        "baseline": {
            "name": "SeasonalNaive",
            "season_length": season_length,
            "smape": baseline_smape,
            "mae": baseline_mae,
        },
        "improvement_smape_pct": gate.improvement_pct * 100.0,
        "n_holdout_windows": n_windows,
        "per_series": [
            {
                "unique_id": str(sid),
                "model_smape": smape(
                    cv_df.loc[cv_df["unique_id"] == sid, "y"].to_numpy(),
                    cv_df.loc[cv_df["unique_id"] == sid, model_col].to_numpy(),
                ),
                "baseline_smape": next(
                    (b["smape"] for b in baseline_per_unique if b["unique_id"] == str(sid)),
                    None,
                ),
            }
            for sid in cv_df["unique_id"].unique()
        ],
    }

    return mlf, metrics_block, gate


def assemble_metadata(
    *,
    version: int,
    feature_config: dict[str, Any],
    metrics: dict[str, Any],
    history_bytes: bytes,
    git_sha: str,
    training_df: pd.DataFrame,
) -> dict[str, Any]:
    n_periods = int(training_df.groupby("unique_id", observed=True).size().min())
    n_series = int(training_df["unique_id"].nunique())
    return {
        "version": version,
        "trained_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "training_window": {
            "from": training_df["ds"].min().date().isoformat(),
            "to": training_df["ds"].max().date().isoformat(),
            "n_periods": n_periods,
            "n_series": n_series,
        },
        "feature_config": feature_config,
        "data_hash": compute_data_hash(history_bytes),
        "metrics": metrics,
        "library_versions": library_versions(),
        "git_sha": git_sha,
    }


__all__ = [
    "BadHistoryError",
    "assemble_metadata",
    "run_fit_pipeline",
    "validate_history",
]
