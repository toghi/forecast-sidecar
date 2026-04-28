"""T045 — Trainer is idempotent on retry given the same `--output-version` (FR-014)."""

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
        "n_estimators": 30,
        "learning_rate": 0.1,
        "num_leaves": 15,
        "min_data_in_leaf": 2,
    },
    "calibration": {"n_windows": 5},
}


def _materialize_inputs(synthetic_series: Any) -> tuple[Path, Path]:
    s0 = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas()
    s0["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    tmp = Path(tempfile.mkdtemp(prefix="forecast-idem-"))
    h = tmp / "history.csv"
    s0.to_csv(h, index=False)
    c = tmp / "feature_config.json"
    c.write_text(json.dumps(_FEATURE_CONFIG))
    return h, c


def _invoke(history: Path, config: Path, version: int) -> Any:
    runner = CliRunner()
    return runner.invoke(
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


def test_rerunning_same_version_is_safe(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    history, config = _materialize_inputs(synthetic_series)

    r1 = _invoke(history, config, 1)
    assert r1.exit_code == 0, r1.output

    storage = GCSStorage(local_settings)
    pointer1 = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer1 is not None

    # Re-run with the same output-version. Should detect "latest already at
    # this version" and exit 5 (clean noop) — FR-014 idempotency.
    r2 = _invoke(history, config, 1)
    assert r2.exit_code == 5, r2.output

    pointer2 = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer2 is not None
    assert pointer2[0]["version"] == 1


def test_two_consecutive_versions_promote(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
) -> None:
    history, config = _materialize_inputs(synthetic_series)

    assert _invoke(history, config, 1).exit_code == 0
    assert _invoke(history, config, 2).exit_code == 0

    storage = GCSStorage(local_settings)
    pointer = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer is not None
    assert pointer[0]["version"] == 2
