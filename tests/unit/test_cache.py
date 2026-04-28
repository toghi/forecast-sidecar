"""T031 — cache TTL, LRU eviction, singleflight."""

from __future__ import annotations

import asyncio

import pytest
from freezegun import freeze_time

from forecast_sidecar.cache import _SingleflightCache


def test_set_and_get() -> None:
    c: _SingleflightCache[int] = _SingleflightCache(maxsize=8, ttl=60)
    c.set("a", 1)
    assert c.get("a") == 1


def test_lru_eviction_when_size_exceeded() -> None:
    c: _SingleflightCache[int] = _SingleflightCache(maxsize=2, ttl=600)
    c.set("a", 1)
    c.set("b", 2)
    c.set("c", 3)
    assert c.get("a") is None  # LRU evicted
    assert c.get("b") == 2
    assert c.get("c") == 3


def test_ttl_expiry() -> None:
    with freeze_time("2026-04-29 00:00:00") as frozen:
        c: _SingleflightCache[int] = _SingleflightCache(maxsize=4, ttl=60)
        c.set("k", 99)
        frozen.tick(delta=30)
        assert c.get("k") == 99
        frozen.tick(delta=31)  # total 61s
        assert c.get("k") is None


def test_singleflight_serializes_concurrent_misses() -> None:
    c: _SingleflightCache[str] = _SingleflightCache(maxsize=4, ttl=60)
    fetch_count = 0

    async def fetch() -> str:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.01)
        return "value"

    async def run() -> None:
        results = await asyncio.gather(*[c.get_or_fetch("k", fetch) for _ in range(5)])
        assert all(r == "value" for r in results)

    asyncio.run(run())
    assert fetch_count == 1


def test_invalidate_drops_entry_and_lock() -> None:
    c: _SingleflightCache[int] = _SingleflightCache(maxsize=4, ttl=60)
    c.set("k", 1)
    c.invalidate("k")
    assert c.get("k") is None


@pytest.mark.parametrize("size", [1, 50])
def test_size_param_respected(size: int) -> None:
    c: _SingleflightCache[int] = _SingleflightCache(maxsize=size, ttl=60)
    for i in range(size + 5):
        c.set(i, i)
    assert len(c) == size
