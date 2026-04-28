"""Run-manifest builder. Captures the provenance fields required by Constitution Principle I."""

from __future__ import annotations

import hashlib
import json
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import Any

_TRACKED_LIBS: tuple[str, ...] = (
    "mlforecast",
    "lightgbm",
    "utilsforecast",
    "numpy",
    "pandas",
    "polars",
    "fastapi",
    "pydantic",
)


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def compute_data_hash(data: bytes) -> str:
    return _sha256_hex(data)


def compute_config_hash(config: dict[str, Any]) -> str:
    return _sha256_hex(_canonical_json(config))


def library_versions() -> dict[str, str]:
    versions: dict[str, str] = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    for lib in _TRACKED_LIBS:
        try:
            versions[lib] = pkg_version(lib)
        except PackageNotFoundError:
            versions[lib] = "unknown"
    return versions


def build_manifest(
    *,
    version: int,
    trained_at: str,
    training_window: dict[str, Any],
    feature_config: dict[str, Any],
    data_hash: str,
    metrics: dict[str, Any],
    git_sha: str,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "version": version,
        "trained_at": trained_at,
        "training_window": training_window,
        "feature_config": feature_config,
        "feature_config_hash": compute_config_hash(feature_config),
        "data_hash": data_hash,
        "metrics": metrics,
        "library_versions": library_versions(),
        "git_sha": git_sha,
    }
    base["manifest_hash"] = _sha256_hex(_canonical_json(base))
    return base
