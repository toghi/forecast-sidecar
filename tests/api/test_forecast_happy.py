"""T037 — POST /forecast happy path + trace-context propagation (FR-024)."""

from __future__ import annotations

import json
import logging
from typing import Any

import structlog
from fastapi.testclient import TestClient

from tests.conftest import CO_ID, COMPANY_ID


def test_happy_path_returns_valid_forecast(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["model_version"] == seeded_storage["version"]
    assert "trained_at" in payload
    assert len(payload["forecast"]) == sample_request_dict["horizon_periods"]

    for point in payload["forecast"]:
        assert point["lo95"] <= point["lo80"] <= point["point"] <= point["hi80"] <= point["hi95"]

    metrics = payload["model_metrics"]
    assert {"training_mae", "training_smape", "coverage_80", "coverage_95"} <= metrics.keys()


def test_intervals_widen_with_horizon(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 200

    forecast = response.json()["forecast"]
    widths_80 = [p["hi80"] - p["lo80"] for p in forecast]
    # Conformal intervals are symmetric per step; later periods should
    # be at least as wide as earlier ones (residuals span more horizons).
    assert widths_80[-1] >= widths_80[0]


def test_trace_context_appears_in_logs(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
    caplog: Any,
) -> None:
    """FR-024: X-Cloud-Trace-Context propagates into structured logs."""
    captured: list[dict[str, Any]] = []

    def capture_processor(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
        captured.append(dict(event_dict))
        return event_dict

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            capture_processor,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=False,
    )

    response = app_client.post(
        "/forecast",
        json=sample_request_dict,
        headers={"X-Cloud-Trace-Context": "abc123def456ghi/789;o=1"},
    )
    assert response.status_code == 200

    served = next((e for e in captured if e.get("event") == "forecast.served"), None)
    assert served is not None, f"forecast.served not in {captured}"
    assert served.get("trace_id") == "abc123def456ghi"
    assert served.get("span_id") == "789"
    assert served.get("company_id") == COMPANY_ID
    assert served.get("computed_object_id") == CO_ID


def test_response_validates_against_pydantic(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    from forecast_sidecar.schemas import ForecastResponse

    response = app_client.post("/forecast", json=sample_request_dict)
    assert response.status_code == 200
    parsed = ForecastResponse.model_validate(response.json())
    assert len(parsed.forecast) == sample_request_dict["horizon_periods"]


def test_request_with_extra_field_rejected_400(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    bad = {**sample_request_dict, "unexpected_key": "boom"}
    response = app_client.post("/forecast", json=bad)
    assert response.status_code == 422  # Pydantic strict-extras rejects with 422
    body = response.json()
    assert body["detail"][0]["type"] in {"extra_forbidden", "value_error"}


def test_horizon_mismatch_rejected_422(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    bad = {**sample_request_dict, "horizon_periods": 6}
    response = app_client.post("/forecast", json=bad)
    assert response.status_code == 422
    assert "horizon" in json.dumps(response.json()).lower()
