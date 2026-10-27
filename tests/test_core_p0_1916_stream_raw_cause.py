"""P0 #1916: the raw search journal retains real stream failures.

The progressive UI still receives only closed machine codes, not exception
messages. Synthetic provider streams only; no HTTP, download or auth.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

import httpx
import pytest

from cifereca.core import search_orchestration as orch
from tests.test_core_0968_provider_stream import (
    Collection, PID, _library, event,
)


SENTINEL = "SYNTHETIC_ORIGINAL_STREAM_ERROR"


@pytest.mark.parametrize("failure,reason", [
    (lambda: RuntimeError(SENTINEL), "StreamProviderFailed"),
    (lambda: httpx.ReadTimeout(SENTINEL), "SearchTemporarilyUnavailable"),
])
def test_stream_failure_preserves_raw_exception_but_not_public_batch(
    tmp_path, caplog, failure, reason,
):
    collection = Collection([])
    lib = _library(tmp_path, collection)
    source = collection.plugin.session
    calls = []

    def iteration(*_args, **_kwargs):
        calls.append("one_stream")
        raise failure()
        yield  # yield makes this an iterator failure, not opening failure.

    source.search_stream = iteration
    batches = []
    try:
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            result = lib.search_progressive(
                "Synthetic book", force=True, on_batch=batches.append,
            )
        assert calls == ["one_stream"]
        assert source.legacy_calls == 0
        assert result.providers_failed == 1
        assert len(batches) == 1
        assert batches[0].error_code == reason
        assert batches[0].result_status == "unavailable"
        assert SENTINEL not in repr((result, batches, lib.last_search_warnings))
        assert "TRACE provider search stream failure" in caplog.text
        assert f"provider_id={PID}" in caplog.text
        assert "stage=stream_delivery" in caplog.text
        assert SENTINEL in caplog.text
    finally:
        lib.close()


def test_malformed_stream_reset_preserves_original_validation_cause(
    tmp_path, caplog,
):
    collection = Collection([
        event("upsert", 2), event("done", 3),
    ])
    lib = _library(tmp_path, collection)
    source = collection.plugin.session
    batches = []
    try:
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            result = lib.search_progressive(
                "Synthetic book", force=True, on_batch=batches.append,
            )
        assert result.result_count == 1
        assert source.stream_calls == 1
        assert source.legacy_calls == 1  # Existing explicit reset/resync only.
        assert batches[0].results[0].record_id == "fallback"
        assert "stage=stream_validation" in caplog.text
        assert "SearchStreamFailure" in caplog.text
        assert "TRACE provider search stream failure" in caplog.text
    finally:
        lib.close()


def test_hostile_error_formatter_does_not_override_stream_closed_outcome(
    monkeypatch, caplog,
):
    # Existing provider error tests deliberately override __str__/__repr__.
    # Raw trace handling must never break the closed business outcome.
    from tests.test_core_p0_1143_search_stream_failure_privacy import (
        HostileException,
    )

    def broken(*_args, **_kwargs):
        raise HostileException(SENTINEL)

    monkeypatch.setattr(orch, "consume_provider_stream", broken)
    source = NS(
        provider_id="synthetic.hostile",
        descriptor=NS(display_name="Synthetic"),
        search_stream=lambda *_args, **_kwargs: (),
    )
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        result = orch._search_virtual_stream_session(
            None, source, "Book", "all", field="all", content_types=(),
            generation=1, publish=lambda _: None, cancelled=lambda: False,
            control=None,
        )
    assert result == (
        [], ["Recherche en flux indisponible"],
        "StreamProviderFailed", "Recherche en flux indisponible",
    )
    assert SENTINEL in caplog.text
    assert "exception_type=" in caplog.text
    assert "stage=stream_delivery" in caplog.text
    assert SENTINEL not in repr(result)
