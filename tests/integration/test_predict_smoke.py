"""T039 — End-to-end predict smoke + cache-pickup test (SC-009 via freezegun)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from freezegun import freeze_time

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


def test_cache_picks_up_promoted_version_within_ttl(
    app_client: TestClient,
    local_settings: Settings,
    fake_gcs: object,
    synthetic_series: Any,
    sample_request_dict: dict[str, Any],
) -> None:
    """SC-009: after promotion of v2, the next forecast (with model_version
    omitted) resolves to v2 within `LATEST_POINTER_TTL_SECONDS` (60s default)
    without restart."""
    unique_id = f"{COMPANY_ID}/{CO_ID}"
    history = history_for_single_series(synthetic_series, unique_id)

    with freeze_time("2026-04-29 00:00:00"):
        # Seed v1 and warm the cache.
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

        # Promote v2.
        train_and_seed_model(
            settings=local_settings,
            history=history,
            company_id=COMPANY_ID,
            computed_object_id=CO_ID,
            version=2,
        )

        # Within the same TTL window: still resolves to v1 (latest pointer cached).
        r2 = app_client.post("/forecast", json=sample_request_dict)
        assert r2.status_code == 200
        assert r2.json()["model_version"] == 1

    # Past LATEST_POINTER_TTL_SECONDS (default 60s) → cache miss → re-resolves.
    with freeze_time("2026-04-29 00:02:00"):
        r3 = app_client.post("/forecast", json=sample_request_dict)
        assert r3.status_code == 200
        assert r3.json()["model_version"] == 2
