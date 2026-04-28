"""Seasonal-naive baseline + the constitution-IV "model must beat baseline
by ≥ 10% sMAPE" gate (SC-005)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

_EPS = 1e-9


def seasonal_naive_forecast(
    history: pd.DataFrame,
    *,
    h: int,
    season_length: int,
    id_col: str = "unique_id",
    time_col: str = "ds",
    target_col: str = "y",
) -> pd.DataFrame:
    """Per `unique_id`, forecast `h` future steps as the value `season_length`
    steps before each predicted step."""
    rows: list[dict[str, Any]] = []
    for sid, group in history.groupby(id_col, observed=True):
        group_sorted = group.sort_values(time_col).reset_index(drop=True)
        if len(group_sorted) < season_length:
            msg = f"series {sid!r} too short for season_length={season_length}"
            raise ValueError(msg)
        season_tail = group_sorted[target_col].to_numpy()[-season_length:]
        last_ds = group_sorted[time_col].iloc[-1]
        freq = pd.infer_freq(group_sorted[time_col]) or _infer_offset(group_sorted[time_col])
        future_ds = pd.date_range(start=last_ds, periods=h + 1, freq=freq)[1:]
        for step, ds_value in enumerate(future_ds):
            rows.append(
                {
                    id_col: sid,
                    time_col: ds_value,
                    "SeasonalNaive": float(season_tail[step % season_length]),
                }
            )
    return pd.DataFrame(rows)


def _infer_offset(ds: pd.Series) -> str:
    if len(ds) < 2:
        return "MS"
    delta = ds.iloc[-1] - ds.iloc[-2]
    days = delta.days
    if days <= 1:
        return "D"
    if days <= 7:
        return "W-MON"
    if days <= 31:
        return "MS"
    return "QS"


def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Symmetric Mean Absolute Percentage Error (utilsforecast convention,
    range 0..1)."""
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    denom = np.abs(yt) + np.abs(yp)
    mask = denom > _EPS
    if not mask.any():
        return 0.0
    return float(np.mean(np.abs(yt[mask] - yp[mask]) / denom[mask]))


def mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    yt = np.asarray(y_true, dtype=float)
    yp = np.asarray(y_pred, dtype=float)
    return float(np.mean(np.abs(yt - yp)))


def coverage(y_true: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    yt = np.asarray(y_true, dtype=float)
    inside = (yt >= np.asarray(lo, dtype=float)) & (yt <= np.asarray(hi, dtype=float))
    return float(inside.mean())


@dataclass(frozen=True)
class BaselineGateResult:
    passed: bool
    model_smape: float
    baseline_smape: float
    improvement_pct: float
    threshold: float


def enforce_baseline_gate(
    *,
    model_smape: float,
    baseline_smape: float,
    threshold: float = 0.10,
) -> BaselineGateResult:
    """Constitution IV gate: model must beat the seasonal-naive baseline
    by at least `threshold` (default 10%) on sMAPE.
    `improvement_pct` = (baseline - model) / baseline.
    `passed` is True iff `model_smape <= baseline_smape * (1 - threshold)`.
    """
    if baseline_smape <= _EPS:
        # Baseline is already perfect; nothing to beat.
        return BaselineGateResult(
            passed=True,
            model_smape=model_smape,
            baseline_smape=baseline_smape,
            improvement_pct=0.0,
            threshold=threshold,
        )
    improvement = (baseline_smape - model_smape) / baseline_smape
    passed = model_smape <= baseline_smape * (1 - threshold)
    return BaselineGateResult(
        passed=passed,
        model_smape=model_smape,
        baseline_smape=baseline_smape,
        improvement_pct=improvement,
        threshold=threshold,
    )


__all__ = [
    "BaselineGateResult",
    "coverage",
    "enforce_baseline_gate",
    "mae",
    "seasonal_naive_forecast",
    "smape",
]
