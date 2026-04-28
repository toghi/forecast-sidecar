"""T088 / SC-001 — warm-cache `/forecast` p99 ≤ 500 ms over 200 requests."""

from __future__ import annotations

import time
from statistics import quantiles
from typing import Any

import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def test_warm_cache_p99_under_500ms(
    app_client: TestClient,
    seeded_storage: dict[str, Any],  # noqa: ARG001
    sample_request_dict: dict[str, Any],
) -> None:
    # Warm-up: first request loads the model into cache.
    warm = app_client.post("/forecast", json=sample_request_dict)
    assert warm.status_code == 200

    n = 200
    latencies_ms: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        r = app_client.post("/forecast", json=sample_request_dict)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert r.status_code == 200

    # quantiles(n=100) returns 99 cut points; the last is the 99th percentile.
    p99 = quantiles(latencies_ms, n=100)[-1]
    p50 = quantiles(latencies_ms, n=100)[49]
    print(f"\nwarm-cache latency: p50={p50:.2f}ms p99={p99:.2f}ms over {n} reqs")

    # Spec target is 500 ms p99 on Cloud Run; in-process TestClient overhead
    # is much lower, so this is a comfortable bound on real performance.
    assert p99 < 500.0, f"p99={p99:.2f}ms exceeds 500ms target (SC-001)"
