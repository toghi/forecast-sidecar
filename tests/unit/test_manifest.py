"""T030 — manifest hashing + library version capture."""

from __future__ import annotations

from forecast_sidecar.manifest import (
    build_manifest,
    compute_config_hash,
    compute_data_hash,
    library_versions,
)


def test_data_hash_is_deterministic_and_distinct() -> None:
    a = compute_data_hash(b"hello world")
    b = compute_data_hash(b"hello world")
    c = compute_data_hash(b"hello world!")
    assert a == b
    assert a != c
    assert a.startswith("sha256:")


def test_config_hash_canonicalizes_key_order() -> None:
    h1 = compute_config_hash({"freq": "MS", "target": "y", "lags": [1, 2]})
    h2 = compute_config_hash({"target": "y", "lags": [1, 2], "freq": "MS"})
    assert h1 == h2


def test_library_versions_includes_python_and_core_deps() -> None:
    versions = library_versions()
    assert "python" in versions
    assert versions["python"].startswith("3.11")
    for required in ("mlforecast", "lightgbm", "polars", "pandas"):
        assert required in versions
        assert versions[required] != "unknown", f"{required} should be installed"


def test_build_manifest_self_hashes() -> None:
    m = build_manifest(
        version=1,
        trained_at="2026-04-29T12:00:00Z",
        training_window={"from": "2024-01-01", "to": "2025-12-01", "n_periods": 24, "n_series": 3},
        feature_config={"freq": "MS", "target": "y"},
        data_hash="sha256:abc123",
        metrics={"model": {}, "baseline": {}},
        git_sha="deadbeef",
    )
    assert "manifest_hash" in m
    assert m["manifest_hash"].startswith("sha256:")
    assert "library_versions" in m
    assert m["feature_config_hash"].startswith("sha256:")
