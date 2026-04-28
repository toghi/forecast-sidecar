"""T085 / SC-020 — `.env.example` MUST list every Settings field.

A new contributor cloning the repo + running `cp .env.example .env` MUST
get a `.env` that covers every variable the application reads. This test
catches drift in either direction: a new field added to Settings without
a corresponding `.env.example` line, or a stale `.env.example` line that
doesn't map to any Settings field."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

_KEY_RE = re.compile(r"^(?P<key>[A-Z][A-Z0-9_]*)\s*=", re.MULTILINE)


@pytest.fixture
def example_keys() -> set[str]:
    if not ENV_EXAMPLE.exists():
        msg = f"{ENV_EXAMPLE} missing"
        raise AssertionError(msg)
    text = ENV_EXAMPLE.read_text()
    return {m.group("key") for m in _KEY_RE.finditer(text)}


@pytest.fixture
def settings_keys() -> set[str]:
    from forecast_sidecar.config import Settings

    return {name.upper() for name in Settings.model_fields}


def test_every_settings_field_appears_in_env_example(
    settings_keys: set[str],
    example_keys: set[str],
) -> None:
    missing = settings_keys - example_keys
    assert not missing, f".env.example is missing entries for Settings fields: {sorted(missing)}"


def test_every_env_example_key_maps_to_a_settings_field(
    settings_keys: set[str],
    example_keys: set[str],
) -> None:
    """Inverse direction: `.env.example` should not document keys the app
    doesn't actually consume (otherwise it's lying to contributors)."""
    extras = example_keys - settings_keys
    assert not extras, f".env.example documents keys not in Settings: {sorted(extras)}"
