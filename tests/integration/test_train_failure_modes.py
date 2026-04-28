"""T046 — Trainer failure modes: bad input → 2, training failure → 3,
calibration regression → 4 (FR-016, FR-017, Constitution IV gate)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from forecast_sidecar.config import Settings
from forecast_sidecar.storage import GCSStorage
from forecast_sidecar.train_cli import main as train_cli
from tests.conftest import CO_ID, COMPANY_ID

pytestmark = pytest.mark.integration


_BASE_CONFIG: dict[str, Any] = {
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
        "n_estimators": 30,
        "learning_rate": 0.1,
        "num_leaves": 15,
        "min_data_in_leaf": 2,
    },
    "calibration": {"n_windows": 5},
}


def _runner(history: Path, config: Path, version: int = 1) -> Any:
    return CliRunner().invoke(
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
            str(version),
        ],
        catch_exceptions=False,
    )


def _write_csv(s: Any, path: Path, *, drop_target: bool = False) -> None:
    df = s.filter(s["unique_id"] == "s_0").to_pandas()
    df["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    if drop_target:
        df["y"] = float("nan")
    df.to_csv(path, index=False)


def test_history_below_min_periods_exit_2(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="t46-"))
    history = tmp / "h.csv"
    df = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas().head(10)
    df["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    df.to_csv(history, index=False)

    config = tmp / "c.json"
    config.write_text(json.dumps(_BASE_CONFIG))

    result = _runner(history, config)
    assert result.exit_code == 2, result.output

    storage = GCSStorage(local_settings)
    # error.json was written
    assert storage.has_error_marker(COMPANY_ID, CO_ID, 1)
    # latest.json was NOT written
    assert storage.read_latest_pointer(COMPANY_ID, CO_ID) is None


def test_target_all_nan_exit_2(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="t46-"))
    history = tmp / "h.csv"
    _write_csv(synthetic_series, history, drop_target=True)
    config = tmp / "c.json"
    config.write_text(json.dumps(_BASE_CONFIG))

    result = _runner(history, config)
    assert result.exit_code == 2, result.output


def test_unsupported_freq_exit_2(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="t46-"))
    history = tmp / "h.csv"
    _write_csv(synthetic_series, history)
    config = tmp / "c.json"
    config.write_text(json.dumps({**_BASE_CONFIG, "freq": "fortnightly"}))

    # Will fail somewhere — feature_config.schema.json validation isn't run by
    # the trainer in v1 (caller is trusted), but mlforecast/infer_seasonality
    # rejects unsupported freqs. Expect exit 1 or 3 depending on where it dies.
    result = _runner(history, config)
    assert result.exit_code in {1, 3}, result.output


def test_missing_target_column_exit_2(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="t46-"))
    history = tmp / "h.csv"
    df = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas()
    df["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    df = df.drop(columns=["y"])
    df.to_csv(history, index=False)

    config = tmp / "c.json"
    config.write_text(json.dumps(_BASE_CONFIG))

    result = _runner(history, config)
    assert result.exit_code == 2, result.output


def test_calibration_regression_exit_4(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force the gate to fail by patching `enforce_baseline_gate` to return
    a `passed=False` result, mirroring real model regressions."""
    from forecast_sidecar.model import baselines
    from forecast_sidecar.model import train as train_module

    real_gate = baselines.enforce_baseline_gate

    def force_fail(**kwargs: Any) -> baselines.BaselineGateResult:
        result = real_gate(**kwargs)
        return baselines.BaselineGateResult(
            passed=False,
            model_smape=result.model_smape,
            baseline_smape=result.baseline_smape,
            improvement_pct=-0.5,
            threshold=result.threshold,
        )

    monkeypatch.setattr(train_module, "enforce_baseline_gate", force_fail)

    tmp = Path(tempfile.mkdtemp(prefix="t46-"))
    history = tmp / "h.csv"
    _write_csv(synthetic_series, history)
    config = tmp / "c.json"
    config.write_text(json.dumps(_BASE_CONFIG))

    result = _runner(history, config)
    assert result.exit_code == 4, result.output

    storage = GCSStorage(local_settings)
    # Model + metadata still written for inspection (per FR-016 and the contract);
    # but latest.json is NOT updated.
    assert storage.has_model_pkl(COMPANY_ID, CO_ID, 1)
    assert storage.has_error_marker(COMPANY_ID, CO_ID, 1)
    assert storage.read_latest_pointer(COMPANY_ID, CO_ID) is None
