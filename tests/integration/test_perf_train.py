"""T090 / SC-006 — full training run p95 <= 60 s for ~24-month by 5-series workloads."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from statistics import quantiles
from typing import Any

import pytest
from click.testing import CliRunner

from forecast_sidecar.config import Settings
from forecast_sidecar.train_cli import main as train_cli
from tests.conftest import CO_ID, COMPANY_ID

pytestmark = [pytest.mark.slow, pytest.mark.integration]

_FEATURE_CONFIG: dict[str, Any] = {
    "freq": "MS",
    "target": "y",
    "horizon": 12,
    "min_history_periods": 24,
    "static_features": [],
    "historic_exog": ["calls"],
    "future_exog": ["active_clients"],
    "categorical_features": [],
    "lags": [1, 3, 6, 12],
    "date_features": ["month"],
    "lightgbm_params": {
        "n_estimators": 50,
        "learning_rate": 0.1,
        "num_leaves": 15,
        "min_data_in_leaf": 2,
    },
    "calibration": {"n_windows": 5},
}


def test_train_p95_under_60s(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    # Materialize one series of the synthetic fixture (~84 monthly periods).
    s0 = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas()
    s0["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    tmp = Path(tempfile.mkdtemp(prefix="t90-"))
    history = tmp / "h.csv"
    s0.to_csv(history, index=False)
    config = tmp / "c.json"
    config.write_text(json.dumps(_FEATURE_CONFIG))

    runner = CliRunner()
    durations_ms: list[float] = []
    n = 5
    for v in range(1, n + 1):
        t0 = time.perf_counter()
        result = runner.invoke(
            train_cli,
            [
                "--company-id",
                COMPANY_ID,
                "--computed-object-id",
                CO_ID,
                "--history-url",
                f"file://{history}",
                "--feature-config-url",
                f"file://{config}",
                "--output-version",
                str(v),
            ],
            catch_exceptions=False,
        )
        durations_ms.append((time.perf_counter() - t0) * 1000.0)
        assert result.exit_code == 0, result.output

    # 5 samples → "p95" approximates as the max.
    p95 = quantiles(durations_ms, n=100)[-1]
    print(f"\ntraining duration: max={max(durations_ms):.0f}ms p95~={p95:.0f}ms over {n} runs")

    assert max(durations_ms) < 60_000.0, (
        f"max training duration {max(durations_ms):.0f}ms exceeds 60s target (SC-006)"
    )
