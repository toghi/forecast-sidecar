"""T089 / SC-002 — cold-load `/forecast` p99 ≤ 3 s.

Cold load = cache miss → GCS read → joblib.load → predict. Force a miss
on every request by invalidating the cache key between calls."""

from __future__ import annotations

import time
from statistics import quantiles
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.conftest import CO_ID, COMPANY_ID

pytestmark = [pytest.mark.slow, pytest.mark.integration]


def test_cold_load_p99_under_3s(
    app_client: TestClient,
    seeded_storage: dict[str, Any],
    sample_request_dict: dict[str, Any],
) -> None:
    cache = app_client.app.state.cache

    n = 30
    latencies_ms: list[float] = []
    for _ in range(n):
        # Force cold load: drop both cache entries before each request.
        cache.models.invalidate((COMPANY_ID, CO_ID, 1))
        cache.latest_pointer.invalidate((COMPANY_ID, CO_ID))

        t0 = time.perf_counter()
        r = app_client.post("/forecast", json=sample_request_dict)
        latencies_ms.append((time.perf_counter() - t0) * 1000.0)
        assert r.status_code == 200

    p99 = quantiles(latencies_ms, n=100)[-1]
    p50 = quantiles(latencies_ms, n=100)[49]
    print(f"\ncold-load latency: p50={p50:.2f}ms p99={p99:.2f}ms over {n} reqs")

    assert p99 < 3000.0, f"p99={p99:.2f}ms exceeds 3000ms target (SC-002)"
