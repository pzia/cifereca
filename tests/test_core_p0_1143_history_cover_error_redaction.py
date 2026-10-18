"""P0 #1143: historical cover diagnostics are closed, redacted and cache-safe.

Real Core library service and SQLite cache, synthetic provider session:
no provider network, acquisition, DRM or GUI involved.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

from cifereca.diagnostics import Diagnostics
from cifereca.services import LibraryService
from cifereca.storage import Storage
from tests.test_history_covers import DummyConfig, _Session, result


PRIVATE = "https://reader.private.invalid/LOAN/book.acsm?auth=PRIVATE_HISTORY_COVER"
DEFAULT = "https://public.example.invalid/fallback.png"


class HostileFailure(RuntimeError):
    def __str__(self):
        raise AssertionError("Never stringify provider exception for Diagnostics")

    def __repr__(self):
        raise AssertionError("Never repr provider exception for Diagnostics")


def _service(tmp_path):
    return LibraryService(
        DummyConfig(),
        Storage(tmp_path / "history.sqlite3"),
        Diagnostics(level="detailed"),
    )


def _rows(service, operation):
    return [
        row for row in service.diagnostics.snapshot()
        if row["category"] == "COVER" and row["operation"] == operation
    ]


def test_search_provider_exception_exports_fixed_phase_without_sensitive_details(tmp_path):
    service = _service(tmp_path)
    calls = Mock(side_effect=HostileFailure(PRIVATE))
    service._session_for_account = lambda *_a, **_kw: _Session(calls)
    try:
        url, state = service.resolve_history_thumbnail(
            "SYRACUSE", "123", "Propriété", "Auteur", "2020", DEFAULT,
        )
        assert (url, state) == (DEFAULT, "error")
        assert calls.call_count > 0
        attempts = _rows(service, "history search attempt")
        assert len(attempts) == calls.call_count
        assert all(row["status"] == "error"
                   and row["detail"]["code"] == "history_cover_search_failed"
                   and type(row["detail"]["attempt"]) is int
                   for row in attempts)
        summary = _rows(service, "history search")
        assert summary[-1]["detail"]["code"] == "history_cover_search_failed"
        assert summary[-1]["detail"]["attempts"] == calls.call_count
        assert summary[-1]["detail"]["result"] == "error"
        assert PRIVATE not in json.dumps(service.diagnostics.snapshot())
        assert "PRIVATE_HISTORY_COVER" not in service.diagnostics.text("test")
        before = calls.call_count
        second = service.resolve_history_thumbnail(
            "SYRACUSE", "123", "Propriété", "Auteur", "2020", DEFAULT,
        )
        assert second == (DEFAULT, "error")
        assert calls.call_count == before  # negative cache without another GET
    finally:
        service.close()


def test_cover_detail_failure_is_code_only_and_uses_existing_fallback(tmp_path):
    service = _service(tmp_path)
    row = result("123", "Stonehenge", "Patrick Sobral", "2020", None)
    search = Mock(return_value=[row])
    holdings = Mock(side_effect=HostileFailure(PRIVATE))
    service._session_for_account = lambda *_a, **_kw: _Session(search, holdings)
    try:
        url, state = service.resolve_history_thumbnail(
            "SYRACUSE", "123", "Stonehenge", "Sobral, Patrick", "2020", DEFAULT,
        )
        assert (url, state) == (DEFAULT, "no_cover")
        holdings.assert_called_once()
        detail = _rows(service, "history detail cover")
        assert len(detail) == 1
        assert detail[0]["detail"] == {"code": "history_cover_detail_failed"}
        assert detail[0]["status"] == "error"
        assert "PRIVATE_HISTORY_COVER" not in service.diagnostics.text("test")
        assert "private.invalid" not in service.diagnostics.text("test")
        assert not _rows(service, "history search attempt")
    finally:
        service.close()
