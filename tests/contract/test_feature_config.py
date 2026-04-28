"""T033 — sample feature_config.json validates against contracts/feature_config.schema.json."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = REPO_ROOT / "specs/001-forecast-sidecar-mvp/contracts/feature_config.schema.json"
FIXTURE = REPO_ROOT / "tests/fixtures/sample_feature_config.json"


@pytest.fixture
def schema() -> dict[str, Any]:
    return json.loads(SCHEMA.read_text())


@pytest.fixture
def fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text())


def test_sample_validates(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    jsonschema.validate(fixture, schema)


def test_missing_required_field_rejected(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    bad = {**fixture}
    del bad["freq"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_unknown_lag_transform_rejected(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    bad = {**fixture}
    bad["lag_transforms"] = {"1": [{"name": "totally_made_up", "window_size": 3}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_unsupported_freq_rejected(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    bad = {**fixture, "freq": "fortnightly"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_negative_lag_rejected(schema: dict[str, Any], fixture: dict[str, Any]) -> None:
    bad = {**fixture, "lags": [-1, 1]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
