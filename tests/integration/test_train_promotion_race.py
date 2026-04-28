"""T047 — Promotion CAS race: loser exits 5 cleanly, latest.json never torn."""

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


_CONFIG: dict[str, Any] = {
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


def _materialize(s: Any) -> tuple[Path, Path]:
    df = s.filter(s["unique_id"] == "s_0").to_pandas()
    df["unique_id"] = f"{COMPANY_ID}/{CO_ID}"
    tmp = Path(tempfile.mkdtemp(prefix="t47-"))
    h = tmp / "h.csv"
    df.to_csv(h, index=False)
    c = tmp / "c.json"
    c.write_text(json.dumps(_CONFIG))
    return h, c


def _invoke(history: Path, config: Path, version: int) -> Any:
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


def test_simulated_race_loser_exits_5(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-promote v3 by writing latest.json directly, then run the trainer
    asking for v2 (already eclipsed). The trainer should detect this in the
    pre-promotion check and exit 5 cleanly."""
    history, config = _materialize(synthetic_series)

    # Run a real v1 first to seed the bucket so the trainer's pre-flight CAS
    # has something to compare against.
    assert _invoke(history, config, 1).exit_code == 0

    # Manually advance latest.json to v3 (simulating another writer who got
    # there ahead of us).
    storage = GCSStorage(local_settings)
    pointer = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer is not None
    _, gen = pointer
    storage.write_latest_pointer_cas(
        COMPANY_ID,
        CO_ID,
        {
            "version": 3,
            "trained_at": "2026-04-29T00:00:00Z",
            "model_path": f"forecasts/{COMPANY_ID}/{CO_ID}/v3/model.pkl",
        },
        expected_generation=gen,
    )

    # Now ask the trainer to promote v2 — it should detect that latest is at
    # v3 and exit 5 without overwriting.
    result = _invoke(history, config, 2)
    assert result.exit_code == 5, result.output

    # Latest should still name v3.
    pointer = storage.read_latest_pointer(COMPANY_ID, CO_ID)
    assert pointer is not None
    assert pointer[0]["version"] == 3
