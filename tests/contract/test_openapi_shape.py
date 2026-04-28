"""T036 — ForecastResponse fixture validates against contracts/openapi.yaml schema."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OPENAPI = REPO_ROOT / "specs/001-forecast-sidecar-mvp/contracts/openapi.yaml"


def _resolve_ref(spec: dict[str, Any], ref: str) -> dict[str, Any]:
    parts = ref.lstrip("#/").split("/")
    node: Any = spec
    for p in parts:
        node = node[p]
    return node  # type: ignore[no-any-return]


def _inline_refs(node: Any, spec: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node and len(node) == 1:
            return _inline_refs(_resolve_ref(spec, node["$ref"]), spec)
        return {k: _inline_refs(v, spec) for k, v in node.items()}
    if isinstance(node, list):
        return [_inline_refs(v, spec) for v in node]
    return node


@pytest.fixture
def forecast_response_schema() -> dict[str, Any]:
    spec = yaml.safe_load(OPENAPI.read_text())
    raw = spec["components"]["schemas"]["ForecastResponse"]
    return _inline_refs(raw, spec)  # type: ignore[no-any-return]


def _sample_response() -> dict[str, Any]:
    return {
        "model_version": 1,
        "trained_at": datetime(2026, 4, 29, 0, 0, 0, tzinfo=UTC).isoformat(),
        "forecast": [
            {
                "period": date(2026, 1, 1).isoformat(),
                "point": 1500.0,
                "lo80": 1400.0,
                "hi80": 1600.0,
                "lo95": 1300.0,
                "hi95": 1700.0,
            }
        ],
        "model_metrics": {
            "training_mae": 12.5,
            "training_smape": 0.08,
            "coverage_80": 0.81,
            "coverage_95": 0.94,
        },
    }


def test_sample_validates(forecast_response_schema: dict[str, Any]) -> None:
    jsonschema.validate(_sample_response(), forecast_response_schema)


def test_extra_property_rejected_by_strict_schema(
    forecast_response_schema: dict[str, Any],
) -> None:
    payload = _sample_response()
    payload["unexpected"] = "nope"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, forecast_response_schema)


def test_missing_required_field_rejected(forecast_response_schema: dict[str, Any]) -> None:
    payload = _sample_response()
    del payload["forecast"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(payload, forecast_response_schema)


def test_pydantic_response_round_trips_through_schema(
    forecast_response_schema: dict[str, Any],
) -> None:
    """Build a ForecastResponse from Pydantic, dump-mode-json, validate."""
    from forecast_sidecar.schemas import (
        ForecastPoint,
        ForecastResponse,
        ModelMetricsSummary,
    )

    resp = ForecastResponse(
        model_version=2,
        trained_at=datetime(2026, 4, 29, 12, 0, 0, tzinfo=UTC),
        forecast=[
            ForecastPoint(
                period=date(2026, 1, 1),
                point=100.0,
                lo80=90.0,
                hi80=110.0,
                lo95=80.0,
                hi95=120.0,
            )
        ],
        model_metrics=ModelMetricsSummary(
            training_mae=1.0,
            training_smape=0.05,
            coverage_80=0.82,
            coverage_95=0.95,
        ),
    )
    dumped = resp.model_dump(mode="json")
    jsonschema.validate(dumped, forecast_response_schema)
