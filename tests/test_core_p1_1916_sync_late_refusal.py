"""#1916: the synchronous path must not hide late authentication errors."""
from __future__ import annotations

import httpx
import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


@pytest.mark.parametrize("status,warning,stale", [
    (401, "recherche en ligne indisponible", False),
    (403, "recherche en ligne indisponible", False),
    (429, "recherche en ligne indisponible", True),
    (503, "recherche en ligne indisponible", True),
])
def test_synchronous_late_http_refusal_cannot_become_cacheable_zero(
    tmp_path, monkeypatch, status, warning, stale,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        calls = []
        private = "https://private.invalid/search?auth=NEVER_LOG"

        def search(query, scope, **kwargs):
            calls.append(query)
            if query == "valid-but-empty":
                return []
            request = httpx.Request("GET", private)
            raise httpx.HTTPStatusError(
                private, request=request,
                response=httpx.Response(status, request=request),
            )

        session.search = search
        service._sessions = {"bad": session}
        monkeypatch.setattr(service, "_search_query_variants", lambda _q: [
            "valid-but-empty", "restricted-route", "must-not-retry",
        ])
        rows = service.search("Maritime Book", force=True)
        assert calls == ["valid-but-empty", "restricted-route"]
        assert len(rows) == int(stale)
        if stale:
            assert rows[0].metadata["search_provenance"] == "known_catalog_stale"
            assert rows[0].owned is False and rows[0].downloadable is False
        assert service.last_search_warnings
        assert any(warning in row for row in service.last_search_warnings)
        assert "NEVER_LOG" not in str(rows) + str(service.last_search_warnings)
        assert not any(row["namespace"] == "search"
                       for row in service.storage.cache_namespace_stats())
    finally:
        service.close()


def test_late_access_denial_keeps_other_live_provider_and_does_not_cache(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        good, bad = _FakeSession("good"), _FakeSession("bad")
        bad_calls = []
        def blocked(query, scope, **kwargs):
            bad_calls.append(query)
            if query == "valid-but-empty":
                return []
            req = httpx.Request("GET", "https://private.invalid/?auth=SECRET")
            raise httpx.HTTPStatusError(
                "SECRET", request=req,
                response=httpx.Response(403, request=req),
            )
        bad.search = blocked
        service._sessions = {"good": good, "bad": bad}
        monkeypatch.setattr(service, "_search_query_variants", lambda _q: [
            "valid-but-empty", "forbidden", "should-not-call",
        ])
        rows = service.search("Progressive Book", force=True)
        assert any(row.source_provider == "good" for row in rows)
        assert all(row.source_provider != "bad" for row in rows)
        assert bad_calls == ["valid-but-empty", "forbidden"]
        assert any("indisponible" in warning for warning in service.last_search_warnings)
        assert "SECRET" not in str(rows) + str(service.last_search_warnings)
        assert not any(row["namespace"] == "search"
                       for row in service.storage.cache_namespace_stats())
    finally:
        service.close()


def test_true_empty_200_remains_zero_and_can_be_cached(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        session = _FakeSession("bad")
        calls = []
        session.search = lambda query, scope, **_kw: calls.append(query) or []
        service._sessions = {"bad": session}
        monkeypatch.setattr(service, "_search_query_variants", lambda _q: [
            "valid-but-empty", "also-empty",
        ])
        assert service.search("Maritime Book", force=True) == []
        assert calls == ["valid-but-empty", "also-empty"]
        assert service.last_search_warnings == []
        assert any(row["namespace"] == "search"
                   for row in service.storage.cache_namespace_stats())
    finally:
        service.close()
