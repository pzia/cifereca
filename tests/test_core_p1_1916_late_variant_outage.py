"""#1916: a late query variant outage is not a confirmed empty public search."""
from __future__ import annotations

import httpx
import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


@pytest.mark.parametrize("failure,expected_code,fallback", [
    ("transport", "SearchTemporarilyUnavailable", True),
    ("http_408", "SearchTemporarilyUnavailable", True),
    ("http_429", "SearchTemporarilyUnavailable", True),
    ("http_503", "SearchTemporarilyUnavailable", True),
    ("http_403", "SearchAccessDenied", False),
    ("legitimate_zero", "", False),
])
def test_progressive_reports_late_outage_without_retrying_same_failed_service(
    tmp_path, monkeypatch, failure, expected_code, fallback,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", key="public", title="Maritime Book")
        session = _FakeSession("bad")
        calls = []
        private = "https://private.invalid/search?token=NEVER_PUBLISH"

        def search(query, scope, **_kw):
            calls.append(query)
            if query == "first-valid-empty" or failure == "legitimate_zero":
                return []
            if failure == "transport":
                raise httpx.ConnectError(private)
            status = {"http_408": 408, "http_429": 429, "http_503": 503, "http_403": 403}[failure]
            req = httpx.Request("GET", private)
            raise httpx.HTTPStatusError(
                private, request=req, response=httpx.Response(status, request=req),
            )

        session.search = search
        service._sessions = {"bad": session}
        monkeypatch.setattr(
            service, "_search_query_variants",
            lambda _query: ["first-valid-empty", "later-outage", "must-not-retry"],
        )
        batches = []
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        assert calls == (
            ["first-valid-empty", "later-outage", "must-not-retry"]
            if failure == "legitimate_zero"
            else ["first-valid-empty", "later-outage"]
        )
        assert len(batches) == 1
        assert batches[0].error_code == expected_code
        assert summary.providers_failed == int(bool(expected_code))
        assert summary.result_count == int(fallback)
        assert bool(batches[0].results) == fallback
        if fallback:
            result = batches[0].results[0]
            assert result.metadata["search_provenance"] == "known_catalog_stale"
            assert result.owned is False and result.downloadable is False
            assert "résultats connus" in batches[0].warnings[0]
        elif expected_code:
            assert batches[0].warnings
        assert "NEVER_PUBLISH" not in str(batches) + str(summary)
        if expected_code:
            # Failed, partial or stale batches must never be cached as a
            # successful fresh search. A true all-zero 200 remains cacheable.
            assert not any(row["namespace"] == "search"
                           for row in service.storage.cache_namespace_stats())
    finally:
        service.close()


def test_synchronous_and_progressive_search_do_not_disagree_about_late_outage(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        calls = []
        def search(query, *_args, **_kw):
            calls.append(query)
            if query == "first-valid-empty":
                return []
            raise httpx.ReadTimeout("PRIVATE_ACSM_URL")
        session.search = search
        service._sessions = {"bad": session}
        monkeypatch.setattr(service, "_search_query_variants", lambda _query: [
            "first-valid-empty", "later-timeout", "should-not-call",
        ])
        progressive = service.search_progressive("Maritime Book", force=True)
        assert progressive.result_count == progressive.providers_failed == 1
        assert calls == ["first-valid-empty", "later-timeout"]
        calls.clear()
        direct = service.search("Maritime Book", force=True)
        assert len(direct) == 1
        assert direct[0].metadata["search_provenance"] == "known_catalog_stale"
        assert calls == ["first-valid-empty", "later-timeout"]
        assert "PRIVATE_ACSM_URL" not in str(service.last_search_warnings)
    finally:
        service.close()
