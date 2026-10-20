"""#1916: fixed source-level provenance, independent of untrusted warning text."""
from __future__ import annotations

from types import SimpleNamespace as NS

import httpx

from cifereca.core.search_orchestration import _search_batch_status
from cifereca.plugin_api import CatalogSearchBatch
from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


def test_optional_trailing_public_model_field_preserves_legacy_calls():
    legacy = CatalogSearchBatch("provider.public", "Public", (), (), "", "", False, 19)
    assert legacy.result_status == "live" and legacy.elapsed_ms == 19


def test_only_core_classifies_stale_partial_and_unavailable():
    live = NS(metadata={})
    stale = NS(metadata={"search_provenance": "known_catalog_stale"})
    hostile = NS(metadata={"search_provenance": "fallback_global", "private": "SECRET"})
    assert _search_batch_status([live], [], "") == "live"
    assert _search_batch_status([hostile], [], "") == "live"
    assert _search_batch_status([live], ["PRIVATE_URL"], "") == "partial"
    assert _search_batch_status([live], ["PRIVATE_URL"], "ProviderSearchFailed") == "partial"
    assert _search_batch_status([], [], "SearchAccessDenied") == "unavailable"
    assert _search_batch_status([stale], ["PRIVATE"], "SearchTemporarilyUnavailable") == "known_catalog_stale"
    assert _search_batch_status([live], [], "", cached=True) == "cached"
    assert _search_batch_status([stale], [], "", cached=True) == "known_catalog_stale"
    assert _search_batch_status([live, stale], [], "", cached=True) == "known_catalog_stale"


def test_progressive_source_batches_publish_status_without_exposing_errors(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        service._sessions = {
            "good": _FakeSession("good"),
            "broken": _FakeSession("broken", fail=True),
        }
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        batches = []
        summary = service.search_progressive(
            "Progressive Book", force=True, on_batch=batches.append,
        )
        assert {batch.provider_id: batch.result_status for batch in batches} == {
            "good": "live", "broken": "unavailable",
        }
        assert summary.providers_failed == 1
        assert summary.result_count == 1
        assert "PRIVATE" not in repr([b.result_status for b in batches])
    finally:
        service.close()


def test_progressive_real_cache_is_not_misrepresented_as_live(tmp_path, monkeypatch):
    service = _service(tmp_path)
    try:
        provider = _FakeSession("cached")
        service._sessions = {"cached": provider}
        monkeypatch.setattr(service, "_mirror_remote_results", lambda rows, **kwargs: 0)
        initial = []
        service.search_progressive("Progressive Book", force=True, on_batch=initial.append)
        assert initial[0].result_status == "live"
        seen = []
        service.search_progressive("Progressive Book", force=False, on_batch=seen.append)
        assert len(seen) == 1 and seen[0].cached is True
        assert seen[0].result_status == "cached"
    finally:
        service.close()


def test_progressive_outage_preserves_stale_public_provenance(tmp_path):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", key="one", title="Maritime Book")
        provider = _FakeSession("bad")
        url = "https://private.invalid/?token=SHOULD_NOT_ESCAPE"
        provider.search = lambda *_a, **_kw: (_ for _ in ()).throw(httpx.ConnectError(url))
        service._sessions = {"bad": provider}
        batches = []
        service.search_progressive("Maritime Book", force=True, on_batch=batches.append)
        assert len(batches) == 1
        assert batches[0].result_status == "known_catalog_stale"
        assert batches[0].results[0].metadata["search_provenance"] == "known_catalog_stale"
        assert "SHOULD_NOT_ESCAPE" not in repr(batches)
    finally:
        service.close()
