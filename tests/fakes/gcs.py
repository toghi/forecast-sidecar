"""In-memory GCS stand-in implementing only the surface `storage.py` uses.
Mirrors GCS's generation/precondition semantics so atomic CAS can be tested
without a real cluster."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from google.api_core import exceptions as gcp_exc


@dataclass
class _FakeBlobState:
    data: bytes
    generation: int


@dataclass
class FakeBucket:
    name: str
    _store: dict[str, _FakeBlobState] = field(default_factory=dict)
    _gen_counter: int = 0

    def blob(self, path: str) -> FakeBlob:
        return FakeBlob(self, path)

    def exists(self) -> bool:
        return True

    def _next_gen(self) -> int:
        self._gen_counter += 1
        return self._gen_counter


@dataclass
class FakeBlob:
    bucket: FakeBucket
    name: str
    _generation: int | None = None

    @property
    def generation(self) -> int | None:
        return self._generation

    def exists(self) -> bool:
        return self.name in self.bucket._store

    def reload(self) -> None:
        state = self.bucket._store.get(self.name)
        if state is None:
            raise gcp_exc.NotFound(f"{self.name} not found")
        self._generation = state.generation

    def download_as_bytes(self) -> bytes:
        state = self.bucket._store.get(self.name)
        if state is None:
            raise gcp_exc.NotFound(f"{self.name} not found")
        self._generation = state.generation
        return state.data

    def upload_from_string(
        self,
        data: str | bytes,
        *,
        content_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None:
        del content_type
        payload = data.encode("utf-8") if isinstance(data, str) else data

        existing = self.bucket._store.get(self.name)
        existing_gen = existing.generation if existing else 0

        if if_generation_match is not None and if_generation_match != existing_gen:
            raise gcp_exc.PreconditionFailed(
                f"generation mismatch: expected {if_generation_match}, got {existing_gen}"
            )

        new_gen = self.bucket._next_gen()
        self.bucket._store[self.name] = _FakeBlobState(payload, new_gen)
        self._generation = new_gen

    def delete(self) -> None:
        if self.name not in self.bucket._store:
            raise gcp_exc.NotFound(f"{self.name} not found")
        del self.bucket._store[self.name]


@dataclass
class _FakeListIterator:
    blobs: list[FakeBlob]
    prefixes: set[str]

    def __iter__(self) -> Iterator[FakeBlob]:
        return iter(self.blobs)


class FakeClient:
    def __init__(self) -> None:
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))

    def list_blobs(
        self,
        bucket: FakeBucket,
        *,
        prefix: str = "",
        delimiter: str | None = None,
    ) -> _FakeListIterator:
        blobs: list[FakeBlob] = []
        prefixes: set[str] = set()
        for path in sorted(bucket._store):
            if not path.startswith(prefix):
                continue
            tail = path[len(prefix) :]
            if delimiter and delimiter in tail:
                idx = tail.index(delimiter) + len(delimiter)
                prefixes.add(prefix + tail[:idx])
                continue
            b = FakeBlob(bucket, path)
            b._generation = bucket._store[path].generation
            blobs.append(b)
        return _FakeListIterator(blobs=blobs, prefixes=prefixes)


def patched_storage(monkeypatch: Any, fake_client: FakeClient) -> None:
    """Monkey-patch `google.cloud.storage.Client` so `GCSStorage` builds the fake."""
    from google.cloud import storage as gcs

    monkeypatch.setattr(gcs, "Client", lambda **_: fake_client)
