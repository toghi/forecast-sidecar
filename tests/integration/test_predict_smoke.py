"""T039 — End-to-end predict smoke + cache-pickup test (SC-009)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from forecast_sidecar.config import Settings
from tests._helpers import history_for_single_series, train_and_seed_model
from tests.conftest import CO_ID, COMPANY_ID

pytestmark = pytest.mark.integration


def test_train_and_predict_end_to_end(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["model_version"] == 1
    assert len(payload["forecast"]) == sample_request_dict["horizon_periods"]


def test_cache_picks_up_promoted_version_after_ttl(
    app_client: TestClient,
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
    sample_request_dict: dict[str, Any],
) -> None:
    """SC-009: once the latest-pointer TTL elapses, a forecast (with
    `model_version` omitted) resolves to the newly-promoted version with
    no service restart.

    TTL elapsing is simulated here by invalidating the latest-pointer cache
    entry directly — this is exactly what `cachetools.TTLCache` does after
    the configured TTL is exceeded. The TTL/eviction semantics themselves
    are covered by `tests/unit/test_cache.py::test_ttl_expiry`.
    """
    unique_id = f"{COMPANY_ID}/{CO_ID}"
    history = history_for_single_series(synthetic_series, unique_id)

    train_and_seed_model(
        settings=local_settings,
        history=history,
        company_id=COMPANY_ID,
        computed_object_id=CO_ID,
        version=1,
    )
    r1 = app_client.post("/forecast", json=sample_request_dict)
    assert r1.status_code == 200
    assert r1.json()["model_version"] == 1

    train_and_seed_model(
        settings=local_settings,
        history=history,
        company_id=COMPANY_ID,
        computed_object_id=CO_ID,
        version=2,
    )

    # While the latest-pointer cache is still warm: caller sees v1.
    r2 = app_client.post("/forecast", json=sample_request_dict)
    assert r2.status_code == 200
    assert r2.json()["model_version"] == 1

    # Simulate the TTL elapsing.
    cache = app_client.app.state.cache
    cache.latest_pointer.invalidate((COMPANY_ID, CO_ID))

    r3 = app_client.post("/forecast", json=sample_request_dict)
    assert r3.status_code == 200
    assert r3.json()["model_version"] == 2
