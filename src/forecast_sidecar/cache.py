"""In-process model cache (FR-022). Two-tier TTL design (research R5):
the long cache holds deserialized `MLForecast` objects keyed on
`(company, co, version)`; the short cache resolves "latest" → version
with a 60s TTL so promotions take effect within ~1 minute."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, Generic, TypeVar

from cachetools import TTLCache

from forecast_sidecar.config import Settings

ModelKey = tuple[str, str, int]
LatestKey = tuple[str, str]

T = TypeVar("T")


class _SingleflightCache(Generic[T]):
    """Wraps a `cachetools.TTLCache` with a per-key `asyncio.Lock` so two
    concurrent misses on the same key only fetch once."""

    def __init__(self, *, maxsize: int, ttl: int) -> None:
        # Use time.time so freezegun-style fixtures can advance the clock in tests.
        self._cache: TTLCache[Any, T] = TTLCache(maxsize=maxsize, ttl=ttl, timer=time.time)
        self._locks: dict[Any, asyncio.Lock] = {}

    def __len__(self) -> int:
        return len(self._cache)

    def get(self, key: Any) -> T | None:
        return self._cache.get(key)

    def set(self, key: Any, value: T) -> None:
        self._cache[key] = value

    def invalidate(self, key: Any) -> None:
        self._cache.pop(key, None)
        self._locks.pop(key, None)

    def clear(self) -> None:
        self._cache.clear()
        self._locks.clear()

    async def get_or_fetch(self, key: Any, fetch: Callable[[], Awaitable[T]]) -> T:
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            value = await fetch()
            self._cache[key] = value
            return value


class ModelCache:
    def __init__(self, settings: Settings) -> None:
        self.models: _SingleflightCache[Any] = _SingleflightCache(
            maxsize=settings.model_cache_size,
            ttl=settings.model_cache_ttl_seconds,
        )
        self.latest_pointer: _SingleflightCache[int] = _SingleflightCache(
            maxsize=settings.model_cache_size,
            ttl=settings.latest_pointer_ttl_seconds,
        )

    @property
    def model_count(self) -> int:
        return len(self.models)
