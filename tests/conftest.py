"""Shared pytest fixtures: synthetic series, fake GCS, settings overrides."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
import pytest

from forecast_sidecar.config import Settings
from tests.fakes.gcs import FakeClient, patched_storage

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def synthetic_series() -> pl.DataFrame:
    """Deterministic 96-month by 3-series long-format frame.

    96 monthly periods provides headroom for both:
      - Production-style fits with `PredictionIntervals(n_windows=5, h=12)`
        (needs ``5 * 12 + 1 = 61`` samples).
      - Cross-validation's per-fold conformal calibration: smallest fold
        has ``96 - n_windows * h`` samples, which must satisfy the
        same floor.
    """
    rng = np.random.default_rng(seed=42)
    rows: list[dict[str, Any]] = []
    base = {"s_0": 1000.0, "s_1": 500.0, "s_2": 2000.0}
    growth = {"s_0": 25.0, "s_1": 12.0, "s_2": 40.0}
    seg = {"s_0": "smb", "s_1": "mid", "s_2": "ent"}
    region = {"s_0": "emea", "s_1": "amer", "s_2": "apac"}

    for sid in ("s_0", "s_1", "s_2"):
        for i in range(96):
            year = 2018 + i // 12
            month = (i % 12) + 1
            ds = f"{year}-{month:02d}-01"
            seasonal = 50 * np.sin(2 * np.pi * (month - 1) / 12)
            noise = rng.normal(0.0, 5.0)
            y = base[sid] + growth[sid] * i + seasonal + noise
            rows.append(
                {
                    "unique_id": sid,
                    "ds": ds,
                    "y": float(y),
                    "segment": seg[sid],
                    "region": region[sid],
                    "calls": 100 + i * 5,
                    "active_clients": 10 + i // 2,
                    "bizdev_id": "47",
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("ds").str.to_datetime())


@pytest.fixture
def sample_history_csv_bytes() -> bytes:
    return (FIXTURES / "sample_history.csv").read_bytes()


@pytest.fixture
def sample_feature_config() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_feature_config.json").read_text())


@pytest.fixture
def sample_request_dict() -> dict[str, Any]:
    return json.loads((FIXTURES / "sample_request.json").read_text())


@pytest.fixture
def fake_gcs(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    client = FakeClient()
    patched_storage(monkeypatch, client)
    return client


def _local_env() -> dict[str, str]:
    return {
        "FORECAST_BUCKET": "test-bucket",
        "EXPECTED_AUDIENCE": "http://localhost:8080",
        "ALLOWED_CALLERS": "",
        "AUTH_BYPASS": "1",
        "LOG_LEVEL": "debug",
        "FORECAST_ALLOW_FILE_URLS": "1",
    }


@pytest.fixture
def local_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    for k, v in _local_env().items():
        monkeypatch.setenv(k, v)
    from forecast_sidecar.config import get_settings

    get_settings.cache_clear()
    yield Settings()  # type: ignore[call-arg]
    get_settings.cache_clear()


@pytest.fixture
def cloud_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sets the env vars for a simulated cloud deployment."""
    monkeypatch.setenv("FORECAST_BUCKET", "prod-bucket")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast-sidecar-abc.run.app")
    monkeypatch.delenv("AUTH_BYPASS", raising=False)
    monkeypatch.delenv("ALLOWED_CALLERS", raising=False)
    from forecast_sidecar.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def app_client(local_settings: Settings, fake_gcs: object) -> Iterator[Any]:
    """A FastAPI TestClient with lifespan active and AUTH_BYPASS on."""
    from fastapi.testclient import TestClient

    from forecast_sidecar.main import app

    with TestClient(app) as client:
        yield client


COMPANY_ID = "00000000-0000-0000-0000-000000000001"
CO_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture
def seeded_storage(
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: pl.DataFrame,
) -> dict[str, Any]:
    """Train a small MLForecast on one synthetic series and seed it as v1
    in fake-GCS at the canonical company/CO path. Returns metadata."""
    from tests._helpers import history_for_single_series, train_and_seed_model

    unique_id = f"{COMPANY_ID}/{CO_ID}"
    history = history_for_single_series(synthetic_series, unique_id)
    _, metadata = train_and_seed_model(
        settings=local_settings,
        history=history,
        company_id=COMPANY_ID,
        computed_object_id=CO_ID,
        version=1,
    )
    return metadata
