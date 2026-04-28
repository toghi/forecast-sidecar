"""Structured logging + Sentry + trace-context bootstrap.

`init_structlog` and `init_sentry` are called once from the FastAPI lifespan;
`extract_trace_context` parses Cloud Run's `X-Cloud-Trace-Context` header so
log lines + Sentry events tie back to upstream Go API requests (FR-024).
`tag_sentry_scope` wraps a block so unhandled exceptions carry the
company / CO / mode tags required by FR-025 / SC-008."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Literal

import sentry_sdk
import structlog


def init_structlog(level: str) -> None:
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=log_level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        cache_logger_on_first_use=True,
    )


def init_sentry(dsn: str | None, environment: str, release: str) -> None:
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=0.0,
        send_default_pii=False,
    )


_TRACE_CTX_RE = re.compile(r"^(?P<trace>[0-9a-fA-F]+)/(?P<span>\d+)(?:;o=(?P<sampled>[01]))?")


def extract_trace_context(headers: Mapping[str, str]) -> dict[str, str]:
    raw = headers.get("x-cloud-trace-context") or headers.get("X-Cloud-Trace-Context")
    if not raw:
        return {}
    m = _TRACE_CTX_RE.match(raw)
    if not m:
        return {}
    return {
        "trace_id": m.group("trace"),
        "span_id": m.group("span"),
        "trace_sampled": m.group("sampled") or "0",
    }


@contextmanager
def tag_sentry_scope(
    *,
    company_id: str,
    computed_object_id: str,
    mode: Literal["service", "train"],
    extra: dict[str, Any] | None = None,
) -> Iterator[None]:
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("company_id", company_id)
        scope.set_tag("computed_object_id", computed_object_id)
        scope.set_tag("mode", mode)
        if extra:
            for k, v in extra.items():
                scope.set_extra(k, v)
        yield
