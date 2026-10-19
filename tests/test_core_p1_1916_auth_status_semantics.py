"""#1916: access refusals are never reported as a public service outage."""
from __future__ import annotations

import httpx
import pytest

from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


@pytest.mark.parametrize("http_status,expected_code,expected_label", [
    (401, "SearchAuthenticationRequired", "authentification requise"),
    (403, "SearchAccessDenied", "accès refusé"),
    (429, "SearchTemporarilyUnavailable", "recherche en ligne indisponible"),
    (503, "SearchTemporarilyUnavailable", "recherche en ligne indisponible"),
])
@pytest.mark.parametrize("late", [False, True])
def test_progressive_search_explains_failure_without_claiming_wrong_scope(
    tmp_path, monkeypatch, http_status, expected_code, expected_label, late,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        s = _FakeSession("bad")
        calls = []
        private_url = "https://example.invalid/search?token=PRIVATE_VALUE"

        def search(variant, scope, **kwargs):
            calls.append(variant)
            if late and variant == "first-empty":
                return []
            req = httpx.Request("GET", private_url)
            raise httpx.HTTPStatusError(
                private_url, request=req,
                response=httpx.Response(http_status, request=req),
            )
        s.search = search
        service._sessions = {"bad": s}
        monkeypatch.setattr(
            service, "_search_query_variants", lambda query:
            ["first-empty", "failing-second", "not-requested"],
        )
        batches = []
        summary = service.search_progressive(
            "Maritime Book", force=True, on_batch=batches.append,
        )
        assert calls == (["first-empty", "failing-second"] if late else ["first-empty"])
        assert summary.providers_failed == 1
        assert len(batches) == 1
        batch = batches[0]
        assert batch.error_code == expected_code
        assert batch.error_message == expected_label
        assert any(expected_label in warning for warning in batch.warnings)
        if http_status in {401, 403}:
            assert not batch.results
            assert all("indisponible" not in warning for warning in batch.warnings)
        else:
            assert len(batch.results) == 1
            assert batch.results[0].metadata["search_provenance"] == "known_catalog_stale"
            assert batch.results[0].owned is False
            assert batch.results[0].downloadable is False
        assert "PRIVATE_VALUE" not in repr((batch, summary))
        assert not any(row["namespace"] == "search"
                       for row in service.storage.cache_namespace_stats())
    finally:
        service.close()


@pytest.mark.parametrize("http_status,expected_label", [
    (401, "authentification requise"),
    (403, "accès refusé"),
])
def test_sync_search_refusal_does_not_claim_outage_or_cache(
    tmp_path, http_status, expected_label,
):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime Book")
        session = _FakeSession("bad")
        calls = []
        def search(variant, scope, **kwargs):
            calls.append(variant)
            req = httpx.Request("GET", "https://example.invalid/private?auth=NOPE")
            raise httpx.HTTPStatusError("NOPE", request=req,
                                        response=httpx.Response(http_status, request=req))
        session.search = search
        service._sessions = {"bad": session}
        assert service.search("Maritime Book", force=True) == []
        assert len(calls) == 1
        assert service.last_search_warnings == [f"Bad: {expected_label}"]
        assert "NOPE" not in repr(service.last_search_warnings)
        assert not any(row["namespace"] == "search"
                       for row in service.storage.cache_namespace_stats())
    finally:
        service.close()
