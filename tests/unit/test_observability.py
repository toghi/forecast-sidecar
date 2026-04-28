"""T018 sub-test — Sentry scope tagging (FR-025 / SC-008) + trace-context parsing."""

from __future__ import annotations

import sentry_sdk
from sentry_sdk import capture_exception

from forecast_sidecar.observability import (
    extract_trace_context,
    init_sentry,
    tag_sentry_scope,
)


def test_extract_trace_context_full() -> None:
    headers = {"X-Cloud-Trace-Context": "abc123def456/789;o=1"}
    ctx = extract_trace_context(headers)
    assert ctx == {"trace_id": "abc123def456", "span_id": "789", "trace_sampled": "1"}


def test_extract_trace_context_no_options() -> None:
    headers = {"x-cloud-trace-context": "abc/123"}
    ctx = extract_trace_context(headers)
    assert ctx["trace_id"] == "abc"
    assert ctx["span_id"] == "123"
    assert ctx["trace_sampled"] == "0"


def test_extract_trace_context_missing() -> None:
    assert extract_trace_context({}) == {}


def test_extract_trace_context_malformed() -> None:
    assert extract_trace_context({"X-Cloud-Trace-Context": "garbage"}) == {}


def test_init_sentry_noop_without_dsn() -> None:
    init_sentry(None, "test", "0.0.0")  # should not raise


def test_tag_sentry_scope_attaches_tags_to_event() -> None:
    captured: list[dict[str, object]] = []

    def transport(event: dict[str, object]) -> None:
        captured.append(event)

    sentry_sdk.init(
        dsn="https://public@example.invalid/1",
        transport=transport,
        environment="test",
        release="test",
        traces_sample_rate=0.0,
    )

    with tag_sentry_scope(
        company_id="00000000-0000-0000-0000-000000000001",
        computed_object_id="00000000-0000-0000-0000-000000000002",
        mode="service",
    ):
        try:
            raise RuntimeError("boom")
        except RuntimeError as e:
            capture_exception(e)

    assert captured, "Sentry should have captured at least one event"
    tags = captured[0].get("tags") or {}
    assert tags.get("company_id") == "00000000-0000-0000-0000-000000000001"
    assert tags.get("computed_object_id") == "00000000-0000-0000-0000-000000000002"
    assert tags.get("mode") == "service"
