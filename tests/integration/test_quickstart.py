"""T092 / SC-012 / SC-014 — end-to-end quickstart validation.

Runs the documented quickstart commands inline (rather than spawning
docker compose) so this test runs in CI without a docker daemon.
The flow: train via the CLI → call /forecast → assert a valid response.
This verifies the README/quickstart contract is internally consistent.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

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


def test_quickstart_train_then_forecast(
    app_client: TestClient,
    local_settings: Settings,  # noqa: ARG001
    fake_gcs: object,  # noqa: ARG001
    synthetic_series: Any,
    sample_request_dict: dict[str, Any],
) -> None:
    """Mirror of the README §3 / quickstart §3 recipe: cp .env.example
    .env, train via train_cli on a fixture, then POST /forecast and get
    a valid response."""
    # 1. Stage history + feature_config (the README uses tests/fixtures/).
    s0 = synthetic_series.filter(synthetic_series["unique_id"] == "s_0").to_pandas()
    s0["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    tmp = Path(tempfile.mkdtemp(prefix="quickstart-"))
    history = tmp / "history.csv"
    s0.to_csv(history, index=False)
    config = tmp / "feature_config.json"
    config.write_text(json.dumps(_FEATURE_CONFIG))

    # 2. Train (the README's `python -m forecast_sidecar.train_cli ...`).
    runner = CliRunner()
    result = runner.invoke(
        train_cli,
        [
            "--company-id", COMPANY_ID,
            "--computed-object-id", CO_ID,
            "--history-url", f"file://{history}",
            "--feature-config-url", f"file://{config}",
            "--output-version", "1",
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output

    # 3. Call /forecast (the README's `curl -d @sample_request.json`).
    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["model_version"] == 1
    assert len(payload["forecast"]) == sample_request_dict["horizon_periods"]
    for point in payload["forecast"]:
        assert (
            point["lo95"] <= point["lo80"] <= point["point"] <= point["hi80"] <= point["hi95"]
        )
