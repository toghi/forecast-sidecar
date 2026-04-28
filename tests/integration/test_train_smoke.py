"""T044 — End-to-end trainer smoke test.

Drives `train_cli.main` against a synthetic-series fixture written to the
fake GCS as a `file://` URL, asserts the artifact tree + metadata content +
constitution-IV gate."""

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
        "n_estimators": 50,
        "learning_rate": 0.1,
        "num_leaves": 15,
        "min_data_in_leaf": 2,
    },
    "calibration": {"n_windows": 5},
}


@pytest.fixture
def history_csv_path(synthetic_series: Any) -> Path:
    """Materialize one series of the synthetic fixture as a CSV file."""
    s0 = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas()
    s0["unique_id"] = f"{COMPANY_ID}/{CO_ID}"

    tmpdir = tempfile.mkdtemp(prefix="forecast-history-")
    path = Path(tmpdir) / "history.csv"
    s0.to_csv(path, index=False)
    return path


@pytest.fixture
def feature_config_path() -> Path:
    tmpdir = tempfile.mkdtemp(prefix="forecast-config-")
    path = Path(tmpdir) / "feature_config.json"
    path.write_text(json.dumps(_FEATURE_CONFIG))
    return path


def _run_trainer(history: Path, config: Path, version: int) -> Any:
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


def test_smoke_writes_full_artifact_tree(
    local_settings: Settings,
    fake_gcs: object,
    history_csv_path: Path,
    feature_config_path: Path,
) -> None:
    result = _run_trainer(history_csv_path, feature_config_path, version=1)
    assert result.exit_code == 0, result.output

    storage = GCSStorage(local_settings)
    pointer = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer is not None
    payload, _ = pointer
    assert payload["version"] == 1

    metadata = storage.read_model_metadata(COMPANY_ID, CO_ID, 1)
    assert metadata is not None
    assert metadata["version"] == 1
    assert metadata["data_hash"].startswith("sha256:")
    assert "manifest_hash" not in metadata or metadata.get("manifest_hash", "").startswith(
        "sha256:"
    )
    assert metadata["library_versions"]["python"].startswith("3.11")
    assert metadata["library_versions"]["mlforecast"] != "unknown"

    metrics = metadata["metrics"]
    assert metrics["model"]["smape"] >= 0
    assert metrics["baseline"]["name"] == "SeasonalNaive"
    assert metrics["baseline"]["season_length"] == 12
    assert metrics["n_holdout_windows"] == 5


def test_smoke_passes_constitution_iv_gate(
    local_settings: Settings,
    fake_gcs: object,
    history_csv_path: Path,
    feature_config_path: Path,
) -> None:
    result = _run_trainer(history_csv_path, feature_config_path, version=1)
    assert result.exit_code == 0

    storage = GCSStorage(local_settings)
    metadata = storage.read_model_metadata(COMPANY_ID, CO_ID, 1)
    assert metadata is not None
    # SC-005: model must beat baseline by ≥ 10% sMAPE on the synthetic fixture.
    assert metadata["metrics"]["improvement_smape_pct"] >= 10.0, metadata["metrics"]
