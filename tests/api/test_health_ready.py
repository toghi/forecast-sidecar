"""T057 — Liveness + readiness probes (FR-018 unauthenticated, US4)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient


def test_healthz_no_auth_no_dependency(app_client: TestClient) -> None:
    """`/healthz` MUST return 200 without consulting any dependency and
    without authentication."""
    response = app_client.post("/forecast", json={})  # warm the app
    del response  # we just care that the app booted

    r = app_client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_healthz_no_auth_required(app_client: TestClient) -> None:
    """Even with no Authorization header at all, /healthz works."""
    r = app_client.get("/healthz")
    assert r.status_code == 200


def test_readyz_returns_ok_when_gcs_reachable(
    app_client: TestClient,
    fake_gcs: object,
) -> None:
    r = app_client.get("/readyz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["gcs_reachable"] is True
    assert body["models_cached"] == 0


def test_readyz_reports_cached_count(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    """After one /forecast call, the cache should have one model."""
    r1 = app_client.post("/forecast", json=sample_request_dict)
    assert r1.status_code == 200

    r2 = app_client.get("/readyz")
    assert r2.status_code == 200
    body = r2.json()
    assert body["models_cached"] >= 1


def test_readyz_returns_503_when_gcs_unreachable(
    app_client: TestClient,
    fake_gcs: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from forecast_sidecar.storage import GCSStorage

    monkeypatch.setattr(GCSStorage, "reachable", lambda self: False)
    r = app_client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable"
    assert body["gcs_reachable"] is False
