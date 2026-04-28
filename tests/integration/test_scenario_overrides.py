"""T054 — Scenario overrides change only the targeted period; unknown features rejected."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _post(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/forecast", json=payload)
    assert response.status_code == 200, response.text
    return dict(response.json())


def test_empty_overrides_matches_baseline(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    baseline = _post(app_client, sample_request_dict)
    with_empty = _post(app_client, {**sample_request_dict, "scenario_overrides": None})
    assert baseline["forecast"] == with_empty["forecast"]


def test_override_changes_only_targeted_period(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    baseline = _post(app_client, sample_request_dict)

    target_period = sample_request_dict["future_features"][4]["period"]
    overrides = {target_period: {"active_clients": 999}}
    with_override = _post(
        app_client, {**sample_request_dict, "scenario_overrides": overrides}
    )

    diffs = [
        (b["period"], b["point"], o["point"])
        for b, o in zip(baseline["forecast"], with_override["forecast"], strict=True)
        if b["period"] == target_period
    ]
    assert len(diffs) == 1
    period, baseline_point, override_point = diffs[0]
    assert period == target_period
    assert baseline_point != override_point


def test_unknown_feature_in_override_returns_400(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    target_period = sample_request_dict["future_features"][0]["period"]
    overrides = {target_period: {"made_up_feature": 42}}
    response = app_client.post(
        "/forecast", json={**sample_request_dict, "scenario_overrides": overrides}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"] == "bad_request"


def test_override_for_period_not_in_future_features_rejected(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    """Pydantic schema validator catches this before the route runs (FR-007)."""
    overrides = {"2099-01-01": {"active_clients": 1}}
    response = app_client.post(
        "/forecast", json={**sample_request_dict, "scenario_overrides": overrides}
    )
    assert response.status_code == 422, response.text
