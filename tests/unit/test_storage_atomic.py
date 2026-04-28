"""T032 — atomic CAS semantics on `latest.json` (research R4)."""

from __future__ import annotations

import json

import pytest

from forecast_sidecar.config import Settings
from forecast_sidecar.storage import GCSStorage
from tests.fakes.gcs import FakeClient


@pytest.fixture
def storage(local_settings: Settings, fake_gcs: FakeClient) -> GCSStorage:
    return GCSStorage(local_settings)


def test_first_write_succeeds_with_generation_zero(storage: GCSStorage) -> None:
    ok = storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 1, "trained_at": "t"}, expected_generation=0
    )
    assert ok is True
    pointer = storage.read_latest_pointer("co1", "obj1")
    assert pointer is not None
    payload, generation = pointer
    assert payload["version"] == 1
    assert generation > 0


def test_second_write_with_zero_precondition_fails(storage: GCSStorage) -> None:
    storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 1, "trained_at": "t"}, expected_generation=0
    )
    ok = storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 2, "trained_at": "t"}, expected_generation=0
    )
    assert ok is False  # precondition failed: object already exists


def test_update_with_correct_generation_succeeds(storage: GCSStorage) -> None:
    storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 1, "trained_at": "t"}, expected_generation=0
    )
    pointer = storage.read_latest_pointer("co1", "obj1")
    assert pointer is not None
    _, gen = pointer
    ok = storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 2, "trained_at": "t"}, expected_generation=gen
    )
    assert ok is True
    new_pointer = storage.read_latest_pointer("co1", "obj1")
    assert new_pointer is not None
    new_payload, new_gen = new_pointer
    assert new_payload["version"] == 2
    assert new_gen > gen


def test_update_with_stale_generation_fails_idempotently(storage: GCSStorage) -> None:
    """Two writers race the same starting generation; loser fails cleanly."""
    storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 1, "trained_at": "t"}, expected_generation=0
    )
    pointer = storage.read_latest_pointer("co1", "obj1")
    assert pointer is not None
    _, gen = pointer

    # Writer A wins
    assert storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 2, "trained_at": "t"}, expected_generation=gen
    )
    # Writer B was using the same starting gen — must fail
    assert not storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 2, "trained_at": "t"}, expected_generation=gen
    )

    pointer = storage.read_latest_pointer("co1", "obj1")
    assert pointer is not None
    payload, _ = pointer
    assert payload["version"] == 2


def test_read_missing_pointer_returns_none(storage: GCSStorage) -> None:
    assert storage.read_latest_pointer("c", "o") is None


def test_write_and_list_versions(storage: GCSStorage) -> None:
    storage.write_model_bundle("co1", "obj1", 1, model_bytes=b"model-1", metadata={"version": 1})
    storage.write_model_bundle("co1", "obj1", 3, model_bytes=b"model-3", metadata={"version": 3})
    storage.write_model_bundle("co1", "obj1", 2, model_bytes=b"model-2", metadata={"version": 2})
    assert storage.list_versions("co1", "obj1") == [1, 2, 3]


def test_delete_version_dir_removes_artifacts(storage: GCSStorage) -> None:
    storage.write_model_bundle("co1", "obj1", 1, model_bytes=b"m", metadata={"version": 1})
    storage.write_model_bundle("co1", "obj1", 2, model_bytes=b"m", metadata={"version": 2})
    storage.delete_version_dir("co1", "obj1", 1)
    assert storage.list_versions("co1", "obj1") == [2]


def test_pointer_payload_is_json(storage: GCSStorage) -> None:
    storage.write_latest_pointer_cas(
        "co1", "obj1", {"version": 7, "trained_at": "2026-04-29T00:00:00Z"}, expected_generation=0
    )
    pointer = storage.read_latest_pointer("co1", "obj1")
    assert pointer is not None
    payload, _ = pointer
    assert json.dumps(payload, sort_keys=True)
