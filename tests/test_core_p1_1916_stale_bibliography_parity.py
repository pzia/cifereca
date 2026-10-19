"""#1916/#1023: provider outage still searches safe indexed bibliographic facts."""
from __future__ import annotations

import time
import httpx
import pytest

from cifereca.core.known_catalog_fallback import known_public_fallback
from tests.test_core043_progress_cache_stats import _service, _FakeSession


SECRET = "https://private.invalid/download?token=SIGNED_SECRET"


def _stored(service, *, identity="alice", provider="bad", item="one", meta=None):
    details = {
        "source_collection": "catalog",
        "mirrored_at": int(time.time()),
        "medium": "digital",
        "awards": ["Prix du Quai des Orfèvres"],
        "keywords": ["enquête"],
        "summary": "Ce roman a gagné le Prix du Quai.",
        "rights": SECRET, "source_credential": "PRIVATE-ACCOUNT",
    }
    if meta:
        details.update(meta)
    service.storage.catalog_entry_put({
        "catalog_entry_id": f"known-{identity}-{provider}-{item}",
        "identity_id": identity, "provider_id": provider,
        "item_id": f"edition-{item}",
        "title": "Une histoire sans mention du prix",
        "author": "Auteur fictif",
        "media_format": "epub", "local_state": "remote_catalog",
        "metadata": details,
    })


def test_offline_outage_searches_attested_prize_and_subject_not_private_url(tmp_path):
    service = _service(tmp_path)
    try:
        _stored(service)
        _stored(service, identity="bob", item="foreign")
        _stored(service, provider="other", item="other")
        rows = known_public_fallback(
            service.storage, provider_id="bad", identity_id="alice",
            query="prix du quai", scope="all", field="all", content_types=(),
        )
        assert len(rows) == 1
        result = rows[0]
        assert result.record_id == "edition-one"
        assert result.metadata["awards"] == ["Prix du Quai des Orfèvres"]
        assert result.metadata["keywords"] == ["enquête"]
        assert result.metadata["search_provenance"] == "known_catalog_stale"
        assert result.owned is False and result.downloadable is False
        assert result.friendly_url is None and result.download_url is None
        assert result.thumbnail_url is None
        assert "PRIVATE-ACCOUNT" not in repr(result)
        assert "SIGNED_SECRET" not in repr(result)
        assert known_public_fallback(
            service.storage, provider_id="bad", identity_id="alice",
            query="enquête", scope="all", field="all", content_types=(),
        )[0].record_id == "edition-one"
        assert known_public_fallback(
            service.storage, provider_id="bad", identity_id=None,
            query="prix du quai", scope="all", field="all", content_types=(),
        ) == ()
    finally:
        service.close()


@pytest.mark.parametrize("dirty", [
    SECRET, "Bearer LEAK_ME", {"token": "LEAK_ME"},
    ["Prix du Quai", SECRET], ["Prix du Quai"] * 33,
])
def test_legacy_cached_metadata_never_returns_arbitrary_sensitive_award(
    tmp_path, dirty,
):
    service = _service(tmp_path)
    try:
        _stored(service, meta={"awards": dirty, "summary": "Texte public"})
        rows = known_public_fallback(
            service.storage, provider_id="bad", identity_id="alice",
            query="prix du quai", scope="all", field="all", content_types=(),
        )
        # No other field contains BOTH meaningful query terms (the title only
        # includes 'prix'; the summary is a sanitized harmless replacement).
        assert rows == ()
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_live_outage_uses_prize_only_stale_evidence_without_second_http_request(
    tmp_path, progressive,
):
    service = _service(tmp_path)
    try:
        _stored(service)
        session = _FakeSession("bad")
        calls = []
        def unavailable(*args, **kwargs):
            calls.append(1)
            raise httpx.ConnectTimeout("network token=SECRET-HTTP")
        session.search = unavailable
        service._sessions = {"bad": session}
        batches = []
        if progressive:
            summary = service.search_progressive(
                "prix du quai", force=True, on_batch=batches.append,
            )
            assert summary.result_count == summary.providers_failed == 1
            assert len(batches) == 1
            assert batches[0].error_code == "SearchTemporarilyUnavailable"
            results = list(batches[0].results)
        else:
            results = service.search("prix du quai", force=True)
            assert service.last_search_warnings
        assert calls == [1]
        assert [row.record_id for row in results] == ["edition-one"]
        assert results[0].metadata["search_provenance"] == "known_catalog_stale"
        assert not results[0].owned and not results[0].downloadable
        assert "SECRET-HTTP" not in repr(results) + repr(service.last_search_warnings)
        assert not any(row["namespace"] == "search"
                       for row in service.storage.cache_namespace_stats())
    finally:
        service.close()
