"""T043 — SeasonalNaive baseline + Constitution IV gate."""

from __future__ import annotations

import numpy as np
import pandas as pd

from forecast_sidecar.model.baselines import (
    coverage,
    enforce_baseline_gate,
    mae,
    seasonal_naive_forecast,
    smape,
)


def _toy_history(n_periods: int = 24) -> pd.DataFrame:
    rng = np.random.default_rng(seed=7)
    rows = []
    for i in range(n_periods):
        year = 2024 + i // 12
        month = (i % 12) + 1
        ds = f"{year}-{month:02d}-01"
        seasonal = 50 * np.sin(2 * np.pi * (month - 1) / 12)
        rows.append({"unique_id": "s_0", "ds": ds, "y": 100.0 + seasonal + rng.normal(0, 1.0)})
    df = pd.DataFrame(rows)
    df["ds"] = pd.to_datetime(df["ds"])
    return df


def test_seasonal_naive_returns_h_rows_per_series() -> None:
    df = _toy_history(n_periods=24)
    out = seasonal_naive_forecast(df, h=12, season_length=12)
    assert len(out) == 12
    assert set(out.columns) == {"unique_id", "ds", "SeasonalNaive"}


def test_seasonal_naive_repeats_last_season() -> None:
    df = _toy_history(n_periods=24)
    out = seasonal_naive_forecast(df, h=12, season_length=12)
    last_year = df.tail(12)["y"].to_numpy()
    np.testing.assert_array_almost_equal(out["SeasonalNaive"].to_numpy(), last_year)


def test_seasonal_naive_too_short_raises() -> None:
    df = _toy_history(n_periods=6)
    try:
        seasonal_naive_forecast(df, h=12, season_length=12)
    except ValueError as exc:
        assert "season_length" in str(exc)
    else:
        msg = "expected ValueError"
        raise AssertionError(msg)


def test_smape_zero_when_perfect() -> None:
    y = np.array([1.0, 2.0, 3.0])
    assert smape(y, y) == 0.0


def test_smape_symmetric() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([2.0, 1.0, 4.0])
    assert smape(a, b) == smape(b, a)


def test_mae_basic() -> None:
    y = np.array([1.0, 2.0, 3.0])
    p = np.array([1.5, 2.0, 2.0])
    assert mae(y, p) == (0.5 + 0.0 + 1.0) / 3


def test_coverage_full() -> None:
    y = np.array([5.0, 5.0])
    lo = np.array([4.0, 4.0])
    hi = np.array([6.0, 6.0])
    assert coverage(y, lo, hi) == 1.0


def test_coverage_partial() -> None:
    y = np.array([5.0, 10.0])
    lo = np.array([4.0, 4.0])
    hi = np.array([6.0, 6.0])
    assert coverage(y, lo, hi) == 0.5


def test_baseline_gate_passes_when_model_at_threshold() -> None:
    """Model must beat baseline by ≥ 10% on sMAPE."""
    result = enforce_baseline_gate(model_smape=0.09, baseline_smape=0.10)
    assert result.passed
    assert result.improvement_pct == 0.1


def test_baseline_gate_fails_when_improvement_below_threshold() -> None:
    result = enforce_baseline_gate(model_smape=0.095, baseline_smape=0.10)
    assert not result.passed
    assert result.improvement_pct < 0.10


def test_baseline_gate_fails_when_model_loses() -> None:
    result = enforce_baseline_gate(model_smape=0.12, baseline_smape=0.10)
    assert not result.passed
    assert result.improvement_pct < 0


def test_baseline_gate_passes_when_baseline_is_perfect() -> None:
    result = enforce_baseline_gate(model_smape=0.05, baseline_smape=0.0)
    assert result.passed
