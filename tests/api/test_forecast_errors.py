"""T038 — POST /forecast error class taxonomy (FR-006: 6 distinct codes).

Includes the FR-040 defense-in-depth assertion (allowlisted SA token but
no Authorization header → 401)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from google.api_core import exceptions as gcp_exc

from forecast_sidecar.config import Settings
from forecast_sidecar.main import app
from tests._helpers import history_for_single_series, train_and_seed_model
from tests.conftest import CO_ID, COMPANY_ID

# ---- 401: invalid_token ------------------------------------------------------


@pytest.fixture
def cloud_client(monkeypatch: pytest.MonkeyPatch, fake_gcs: object) -> TestClient:
    """Cloud-style settings (no AUTH_BYPASS, allow-list mandatory)."""
    monkeypatch.setenv("FORECAST_BUCKET", "prod-bucket")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast.run.app")
    monkeypatch.setenv("ALLOWED_CALLERS", "go-api@example.iam.gserviceaccount.com")
    monkeypatch.setenv("LOG_LEVEL", "info")
    monkeypatch.delenv("AUTH_BYPASS", raising=False)
    from forecast_sidecar.config import get_settings

    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client
    get_settings.cache_clear()


def test_missing_authorization_header_returns_401(
    cloud_client: TestClient,
    sample_request_dict: dict[str, Any],
) -> None:
    """FR-040 defense-in-depth: even an internally reachable request needs OIDC."""
    response = cloud_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 401
    body = response.json()
    assert body["error"] == "invalid_token"


def test_bearer_token_with_bad_audience_returns_401(
    cloud_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sample_request_dict: dict[str, Any],
) -> None:
    from forecast_sidecar import auth

    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        return {"aud": "https://other.run.app", "email": "x@x.com", "sub": "1"}

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    response = cloud_client.post(
        "/forecast",
        json=sample_request_dict,
        headers={"Authorization": "Bearer something"},
    )
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"


def test_caller_not_in_allowlist_returns_401(
    cloud_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    sample_request_dict: dict[str, Any],
) -> None:
    from forecast_sidecar import auth

    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        return {"aud": audience, "email": "stranger@evil.iam.gserviceaccount.com", "sub": "1"}

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    response = cloud_client.post(
        "/forecast",
        json=sample_request_dict,
        headers={"Authorization": "Bearer something"},
    )
    assert response.status_code == 401


# ---- 404: not_yet_trained vs model_not_found --------------------------------


def test_not_yet_trained_when_no_latest_pointer(
    app_client: TestClient,
    sample_request_dict: dict[str, Any],
) -> None:
    """Brand-new (company, CO) — no `latest.json` yet."""
    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 404
    assert response.json()["error"] == "not_yet_trained"


def test_model_not_found_when_explicit_version_missing(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    """`latest.json` exists at v=1, but the request asks for v=99."""
    payload = {**sample_request_dict, "model_version": 99}
    response = app_client.post("/forecast", json=payload)
    assert response.status_code == 404
    assert response.json()["error"] == "model_not_found"


# ---- 409: model_not_ready ----------------------------------------------------


def test_model_not_ready_when_only_error_marker_present(
    app_client: TestClient,
    local_settings: Settings,
    fake_gcs: object,
    sample_request_dict: dict[str, Any],
) -> None:
    from forecast_sidecar.storage import GCSStorage

    storage = GCSStorage(local_settings)
    # Seed a successful v1 so latest.json points somewhere; explicit v=2 fails.
    storage.write_latest_pointer_cas(
        COMPANY_ID,
        CO_ID,
        {"version": 1, "trained_at": "t", "model_path": "..."},
        expected_generation=0,
    )
    storage.write_error_marker(
        COMPANY_ID,
        CO_ID,
        version=2,
        payload={"version": 2, "exit_code": 3, "phase": "fit", "error_type": "ValueError"},
    )

    payload = {**sample_request_dict, "model_version": 2}
    response = app_client.post("/forecast", json=payload)
    assert response.status_code == 409
    assert response.json()["error"] == "model_not_ready"


# ---- 400: bad_request (missing future_exog column) --------------------------


def test_missing_future_exog_column_returns_400(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    """The model expects `active_clients` and `bizdev_id`. Drop one."""
    bad = {
        **sample_request_dict,
        "future_features": [
            {"period": pf["period"], "bizdev_id": pf["bizdev_id"]}
            for pf in sample_request_dict["future_features"]
        ],
    }
    response = app_client.post("/forecast", json=bad)
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "bad_request"
    assert "missing_columns" in body
    assert "active_clients" in body["missing_columns"]


# ---- 503: storage_unavailable -----------------------------------------------


def test_storage_unavailable_returns_503(
    app_client: TestClient,
    local_settings: Settings,
    fake_gcs: object,
    monkeypatch: pytest.MonkeyPatch,
    synthetic_series: Any,
    sample_request_dict: dict[str, Any],
) -> None:
    """Train + seed first, then make the GCS read fail."""
    unique_id = f"{COMPANY_ID}/{CO_ID}"
    history = history_for_single_series(synthetic_series, unique_id)
    train_and_seed_model(
        settings=local_settings,
        history=history,
        company_id=COMPANY_ID,
        computed_object_id=CO_ID,
        version=1,
    )

    # Now make any subsequent reads raise ServiceUnavailable.
    from tests.fakes.gcs import FakeBlob

    def boom(self: FakeBlob) -> None:
        raise gcp_exc.ServiceUnavailable("simulated outage")

    monkeypatch.setattr(FakeBlob, "reload", boom)

    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 503
    assert response.json()["error"] == "storage_unavailable"
