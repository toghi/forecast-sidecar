"""GCS storage layer. Encapsulates the artifact tree at
`gs://{bucket}/forecasts/{company}/{co}/v{N}/{model.pkl,metadata.json,error.json}`
plus the atomic `latest.json` pointer. Supports `file://` URLs for local
development (gated by `FORECAST_ALLOW_FILE_URLS`)."""

from __future__ import annotations

import io
import json
import re
from functools import cached_property
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import polars as pl
from google.api_core import exceptions as gcp_exc
from google.cloud import storage as gcs

from forecast_sidecar.config import Settings


class StorageError(Exception):
    """Base for all storage-layer errors."""


class StorageUnavailableError(StorageError):
    """GCS is unreachable / transient failure (→ HTTP 503)."""


class NotYetTrainedError(StorageError):
    """No latest.json + no explicit version → 404 not_yet_trained."""


class ModelNotFoundError(StorageError):
    """Explicit model_version missing in storage → 404 model_not_found."""


class ModelNotReadyError(StorageError):
    """`v{N}/error.json` is present and `model.pkl` is missing (→ 409 model_not_ready)."""


class FileUrlNotAllowedError(StorageError):
    """`file://` URL passed but `FORECAST_ALLOW_FILE_URLS` is not set."""


_VERSION_DIR_RE = re.compile(r"^v(\d+)/$")


def _gs_prefix(company: str, co: str) -> str:
    return f"forecasts/{company}/{co}/"


def _version_prefix(company: str, co: str, version: int) -> str:
    return f"{_gs_prefix(company, co)}v{version}/"


class GCSStorage:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @cached_property
    def client(self) -> Any:
        kwargs: dict[str, Any] = {}
        if self._settings.gcs_fake_host:
            kwargs["client_options"] = {"api_endpoint": self._settings.gcs_fake_host}
        return gcs.Client(**kwargs)

    @cached_property
    def bucket(self) -> Any:
        return self.client.bucket(self._settings.forecast_bucket)

    # ------------------------------------------------------------------ URLs

    def fetch_url_bytes(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme == "gs":
            try:
                blob = self.client.bucket(parsed.netloc).blob(parsed.path.lstrip("/"))
                return bytes(blob.download_as_bytes())
            except gcp_exc.NotFound as exc:
                raise ModelNotFoundError(f"object not found: {url}") from exc
            except gcp_exc.ServiceUnavailable as exc:
                raise StorageUnavailableError(str(exc)) from exc
        if parsed.scheme == "file":
            if not self._settings.forecast_allow_file_urls:
                raise FileUrlNotAllowedError("FORECAST_ALLOW_FILE_URLS is not set")
            return Path(parsed.path).read_bytes()
        raise StorageError(f"unsupported URL scheme: {parsed.scheme}")

    def read_history_csv(self, url: str) -> pl.DataFrame:
        raw = self.fetch_url_bytes(url)
        return pl.read_csv(io.BytesIO(raw))

    def read_feature_config(self, url: str) -> dict[str, Any]:
        raw = self.fetch_url_bytes(url)
        result: dict[str, Any] = json.loads(raw)
        return result

    # ------------------------------------------------------------------ pointers

    def read_latest_pointer(self, company: str, co: str) -> tuple[dict[str, Any], int] | None:
        blob = self.bucket.blob(f"{_gs_prefix(company, co)}latest.json")
        try:
            blob.reload()
        except gcp_exc.NotFound:
            return None
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc
        try:
            payload: dict[str, Any] = json.loads(blob.download_as_bytes())
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return payload, int(blob.generation or 0)

    def write_latest_pointer_cas(
        self,
        company: str,
        co: str,
        payload: dict[str, Any],
        *,
        expected_generation: int,
    ) -> bool:
        blob = self.bucket.blob(f"{_gs_prefix(company, co)}latest.json")
        try:
            blob.upload_from_string(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                content_type="application/json",
                if_generation_match=expected_generation,
            )
        except gcp_exc.PreconditionFailed:
            return False
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return True

    # ------------------------------------------------------------------ artifacts

    def has_error_marker(self, company: str, co: str, version: int) -> bool:
        return self._exists(f"{_version_prefix(company, co, version)}error.json")

    def has_model_pkl(self, company: str, co: str, version: int) -> bool:
        return self._exists(f"{_version_prefix(company, co, version)}model.pkl")

    def read_model_metadata(self, company: str, co: str, version: int) -> dict[str, Any] | None:
        blob = self.bucket.blob(f"{_version_prefix(company, co, version)}metadata.json")
        try:
            blob.reload()
            payload: dict[str, Any] = json.loads(blob.download_as_bytes())
        except gcp_exc.NotFound:
            return None
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc
        return payload

    def read_model_pkl(self, company: str, co: str, version: int) -> bytes:
        blob = self.bucket.blob(f"{_version_prefix(company, co, version)}model.pkl")
        try:
            return bytes(blob.download_as_bytes())
        except gcp_exc.NotFound as exc:
            if self.has_error_marker(company, co, version):
                raise ModelNotReadyError(f"v{version} has error.json but no model.pkl") from exc
            raise ModelNotFoundError(f"v{version}/model.pkl missing") from exc
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc

    def write_model_bundle(
        self,
        company: str,
        co: str,
        version: int,
        *,
        model_bytes: bytes,
        metadata: dict[str, Any],
    ) -> None:
        prefix = _version_prefix(company, co, version)
        self.bucket.blob(f"{prefix}model.pkl").upload_from_string(
            model_bytes, content_type="application/octet-stream"
        )
        self.bucket.blob(f"{prefix}metadata.json").upload_from_string(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            content_type="application/json",
        )

    def write_error_marker(
        self, company: str, co: str, version: int, payload: dict[str, Any]
    ) -> None:
        self.bucket.blob(f"{_version_prefix(company, co, version)}error.json").upload_from_string(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            content_type="application/json",
        )

    # ------------------------------------------------------------------ versions

    def list_versions(self, company: str, co: str) -> list[int]:
        prefix = _gs_prefix(company, co)
        try:
            iterator = self.client.list_blobs(self.bucket, prefix=prefix, delimiter="/")
            list(iterator)
            seen: set[int] = set()
            for sub in getattr(iterator, "prefixes", []):
                tail = sub[len(prefix) :]
                m = _VERSION_DIR_RE.match(tail)
                if m:
                    seen.add(int(m.group(1)))
            return sorted(seen)
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc

    def delete_version_dir(self, company: str, co: str, version: int) -> None:
        prefix = _version_prefix(company, co, version)
        try:
            for blob in self.client.list_blobs(self.bucket, prefix=prefix):
                blob.delete()
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc

    # ------------------------------------------------------------------ probes

    def reachable(self) -> bool:
        try:
            self.bucket.exists()
            return True
        except gcp_exc.GoogleAPICallError:
            return False
        except Exception:
            return False

    # ------------------------------------------------------------------ helpers

    def _exists(self, path: str) -> bool:
        try:
            return bool(self.bucket.blob(path).exists())
        except gcp_exc.ServiceUnavailable as exc:
            raise StorageUnavailableError(str(exc)) from exc
