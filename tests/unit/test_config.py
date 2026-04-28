"""T035a — FR-041 startup gate semantics."""

from __future__ import annotations

import pytest

from forecast_sidecar.config import ConfigurationError, Settings


def test_local_audience_with_empty_allowlist_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "local")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "http://localhost:8080")
    monkeypatch.delenv("ALLOWED_CALLERS", raising=False)
    monkeypatch.delenv("AUTH_BYPASS", raising=False)
    s = Settings()  # type: ignore[call-arg]
    assert s.is_local_audience
    assert s.allowed_callers == frozenset()


def test_cloud_audience_with_empty_allowlist_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "prod")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast.run.app")
    monkeypatch.delenv("ALLOWED_CALLERS", raising=False)
    monkeypatch.delenv("AUTH_BYPASS", raising=False)
    with pytest.raises(ConfigurationError) as ei:
        Settings()  # type: ignore[call-arg]
    assert "ALLOWED_CALLERS" in str(ei.value)


def test_cloud_audience_with_allowlist_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "prod")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast.run.app")
    monkeypatch.setenv("ALLOWED_CALLERS", "a@x.iam.gserviceaccount.com,b@x.iam.gserviceaccount.com")
    s = Settings()  # type: ignore[call-arg]
    assert s.allowed_callers == frozenset(
        {"a@x.iam.gserviceaccount.com", "b@x.iam.gserviceaccount.com"}
    )


def test_auth_bypass_refused_with_cloud_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "prod")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "https://forecast.run.app")
    monkeypatch.setenv("ALLOWED_CALLERS", "go-api@x.iam.gserviceaccount.com")
    monkeypatch.setenv("AUTH_BYPASS", "1")
    with pytest.raises(ConfigurationError) as ei:
        Settings()  # type: ignore[call-arg]
    assert "AUTH_BYPASS" in str(ei.value)


def test_auth_bypass_allowed_with_local_audience_and_debug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "local")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "http://localhost:8080")
    monkeypatch.setenv("AUTH_BYPASS", "1")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings()  # type: ignore[call-arg]
    assert s.auth_bypass


def test_auth_bypass_refused_when_log_level_not_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORECAST_BUCKET", "local")
    monkeypatch.setenv("EXPECTED_AUDIENCE", "http://localhost:8080")
    monkeypatch.setenv("AUTH_BYPASS", "1")
    monkeypatch.setenv("LOG_LEVEL", "info")
    with pytest.raises(ConfigurationError):
        Settings()  # type: ignore[call-arg]
