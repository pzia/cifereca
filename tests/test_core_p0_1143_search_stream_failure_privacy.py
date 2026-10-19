"""P0 #1143: failed virtual streams, callbacks and mirroring never echo secrets.

All search providers are offline fakes. No live search, account information,
network connection or library mutation is performed by these tests.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration as orch
from tests.test_core_0968_provider_stream import Collection, _library, event


PRIVATE = "https://private.invalid/loan?token=SECRET_SEARCH_STREAM"


class HostileException(RuntimeError):
    def __str__(self):
        raise AssertionError("Provider exception text cannot be logged")

    def __repr__(self):
        raise AssertionError("Provider exception repr cannot be logged")


HostileException.__name__ = "SECRET_SEARCH_STREAM"


def throw_private(*_args, **_kwargs):
    raise HostileException(PRIVATE)


def test_stream_failure_never_returns_descriptor_or_private_exception_class(
    monkeypatch,
):
    monkeypatch.setattr(orch, "consume_provider_stream", throw_private)
    session = NS(
        provider_id="virtual.secret",
        descriptor=NS(display_name=PRIVATE),
        search_stream=lambda *_args, **_kw: (),
    )
    result = orch._search_virtual_stream_session(
        None, session, "book", "all", field="all", content_types=(),
        generation=1, publish=lambda _: None, cancelled=lambda: False,
        control=None,
    )
    assert result == (
        [], ["Recherche en flux indisponible"],
        "StreamProviderFailed", "Recherche en flux indisponible",
    )
    assert PRIVATE not in repr(result)


def test_stream_callback_failure_logs_closed_code_without_provider_label(
    tmp_path,
):
    lib = _library(tmp_path, Collection([
        event("upsert", 1),
        event("done", 2),
    ]))
    try:
        summary = lib.search_progressive(
            "book", force=True, on_event=lambda _event: throw_private(),
        )
        assert summary.result_count == 1
        detail = [
            row["detail"] for row in lib.diagnostics.snapshot()
            if row["operation"] == "stream callback"
        ]
        assert detail == [
            {"code": "stream_callback_failed"},
            {"code": "stream_callback_failed"},
        ]
        visible = lib.diagnostics.text("test") + json.dumps(detail)
        assert "SECRET_SEARCH_STREAM" not in visible
    finally:
        lib.close()


def test_sync_and_progressive_search_mirror_log_code_without_exception_class(
    tmp_path, caplog,
):
    lib = _library(tmp_path, Collection([event("upsert", 1), event("done", 2)]))
    lib._mirror_remote_results = throw_private
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            rows = lib.search("book", force=True)
            summary = lib.search_progressive("book", force=True)
        assert rows
        assert summary.result_count == 1
        assert "TRACE catalog mirror search code=mirror_failed" in caplog.text
        assert "TRACE catalog mirror progressive search code=mirror_failed" in caplog.text
        assert "SECRET_SEARCH_STREAM" not in caplog.text
        assert "private.invalid" not in caplog.text
    finally:
        lib.close()
