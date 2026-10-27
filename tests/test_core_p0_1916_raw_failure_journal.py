"""P0 #1916 / logging contract: private raw failure vs bounded Search UI.

Offline fake sessions, no HTTP, credentials or provider mutations.
"""
from __future__ import annotations

import logging

import httpx

from tests.test_core043_progress_cache_stats import _FakeSession, _service


SENTINEL = "PRIVATE_SEARCH_PROVIDER_ERROR_EXACT_VALUE"


def _broken_session():
    session = _FakeSession("synthetic-catalogue")

    def fail(*_args, **_kwargs):
        raise httpx.ReadTimeout(SENTINEL)

    session.search = fail
    return session


def test_synchronous_public_search_preserves_exception_in_raw_log(tmp_path, caplog):
    service = _service(tmp_path)
    try:
        service._sessions = {"source": _broken_session()}
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            rows = service.search("Never Existing Book", force=True)
        assert rows == []
        assert SENTINEL in caplog.text
        assert "TRACE provider search failure provider=synthetic-catalogue" in caplog.text
        assert SENTINEL not in repr(service.last_search_warnings)
        assert service.last_search_warnings
    finally:
        service.close()


def test_progressive_public_search_preserves_raw_failure_but_not_batch(
    tmp_path, caplog,
):
    service = _service(tmp_path)
    batches = []
    try:
        service._sessions = {"source": _broken_session()}
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            result = service.search_progressive(
                "Never Existing Book", force=True, on_batch=batches.append,
            )
        assert result.providers_failed == 1
        assert len(batches) == 1
        assert SENTINEL in caplog.text
        assert "TRACE provider search failure provider=synthetic-catalogue" in caplog.text
        assert SENTINEL not in repr((result, batches))
        assert batches[0].error_code == "SearchTemporarilyUnavailable"
        assert batches[0].result_status == "unavailable"
    finally:
        service.close()


def test_progressive_unhandled_worker_error_retains_raw_trace(
    tmp_path, caplog, monkeypatch,
):
    service = _service(tmp_path)
    batches = []
    try:
        service._sessions = {"source": _FakeSession("synthetic-catalogue")}

        def worker_explodes(*_args, **_kwargs):
            raise RuntimeError(SENTINEL)

        monkeypatch.setattr(service, "_search_provider_session", worker_explodes)
        with caplog.at_level(logging.WARNING, logger="cifereca"):
            result = service.search_progressive(
                "Never Existing Book", force=True, on_batch=batches.append,
            )
        assert result.providers_failed == 1
        assert len(batches) == 1
        assert SENTINEL in caplog.text
        assert "TRACE provider search worker failure provider=synthetic-catalogue" in caplog.text
        assert SENTINEL not in repr((result, batches))
        assert batches[0].error_code == "ProviderSearchFailed"
    finally:
        service.close()
