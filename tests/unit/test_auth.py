"""T035 — OIDC verification dependency."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException, Request

from forecast_sidecar import auth
from forecast_sidecar.config import Settings


def _make_request(headers: dict[str, str]) -> Request:
    raw_headers = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/forecast",
        "headers": raw_headers,
    }
    return Request(scope)


@pytest.fixture
def cloud_settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("FORECAST_BUCKET", "prod")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast.run.app")
    monkeypatch.setenv("ALLOWED_CALLERS", "go-api@example.iam.gserviceaccount.com")
    monkeypatch.delenv("AUTH_BYPASS", raising=False)
    return Settings()  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_bypass_returns_synthetic_claims(local_settings: Settings) -> None:
    request = _make_request({})
    claims = await auth.verify_oidc_token(request, local_settings)
    assert claims.aud == "local-bypass"


@pytest.mark.asyncio
async def test_missing_authorization_header_rejected(cloud_settings: Settings) -> None:
    request = _make_request({})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401
    assert ei.value.detail["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_non_bearer_scheme_rejected(cloud_settings: Settings) -> None:
    request = _make_request({"Authorization": "Basic dXNlcjpwYXNz"})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_with_matching_audience(
    monkeypatch: pytest.MonkeyPatch, cloud_settings: Settings
) -> None:
    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        return {
            "aud": audience,
            "email": "go-api@example.iam.gserviceaccount.com",
            "sub": "1234",
        }

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    request = _make_request({"Authorization": "Bearer fake.token.value"})
    claims = await auth.verify_oidc_token(request, cloud_settings)
    assert claims.email == "go-api@example.iam.gserviceaccount.com"


@pytest.mark.asyncio
async def test_audience_mismatch_rejected(
    monkeypatch: pytest.MonkeyPatch, cloud_settings: Settings
) -> None:
    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        return {"aud": "https://other.run.app", "email": "x@example.com", "sub": "1"}

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    request = _make_request({"Authorization": "Bearer fake"})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401
    assert ei.value.detail["detail"] == "audience mismatch"


@pytest.mark.asyncio
async def test_caller_not_in_allowlist_rejected(
    monkeypatch: pytest.MonkeyPatch, cloud_settings: Settings
) -> None:
    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        return {"aud": audience, "email": "stranger@evil.iam.gserviceaccount.com", "sub": "9"}

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    request = _make_request({"Authorization": "Bearer fake"})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401
    assert ei.value.detail["detail"] == "caller not in allow-list"


@pytest.mark.asyncio
async def test_google_verify_failure_returns_401(
    monkeypatch: pytest.MonkeyPatch, cloud_settings: Settings
) -> None:
    async def fake_verify(token: str, audience: str) -> dict[str, Any]:
        raise ValueError("expired token")

    monkeypatch.setattr(auth, "_verify_with_google", fake_verify)
    request = _make_request({"Authorization": "Bearer fake"})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_bypass_refused_when_audience_is_cloud(
    monkeypatch: pytest.MonkeyPatch, cloud_settings: Settings
) -> None:
    monkeypatch.setenv("AUTH_BYPASS", "1")
    # Settings already loaded; just verify bypass isn't honored on cloud audience
    request = _make_request({})
    with pytest.raises(HTTPException) as ei:
        await auth.verify_oidc_token(request, cloud_settings)
    assert ei.value.status_code == 401
