"""Only positive public provenance can authorize an offline public fallback."""
from dataclasses import replace

import httpx
import pytest

from cifereca.core.known_catalog_fallback import known_public_fallback
from tests.test_core043_progress_cache_stats import _service, _FakeSession
from tests.test_core_p1_1916_stale_public_fallback import _known


def fallback(service):
    return known_public_fallback(
        service.storage, provider_id="bad", identity_id="alice",
        query="Maritime", scope="digital", field="title", content_types=(),
    )


@pytest.mark.parametrize("collection", [
    None, "", "unknown", "archived", "borrowed", "wishlist", "mybooks",
    " library ", " LOANS ", ["catalog"], {"visibility": "public"}, True, 42,
])
def test_missing_unknown_private_or_malformed_provenance_is_never_public(tmp_path, collection):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime PRIVATE", collection=collection)
        before = service.storage.catalog_entries()
        assert fallback(service) == ()
        assert service.storage.catalog_entries() == before
    finally:
        service.close()


@pytest.mark.parametrize("collection", ["catalog", "public", "public_catalog", " PUBLIC "])
def test_explicit_public_provenance_keeps_a_readonly_stale_notice(tmp_path, collection):
    service = _service(tmp_path)
    try:
        _known(service, provider="bad", title="Maritime public", collection=collection)
        rows = fallback(service)
        assert len(rows) == 1
        assert rows[0].metadata["search_provenance"] == "known_catalog_stale"
        assert rows[0].owned is rows[0].downloadable is False
        assert rows[0].source_collection == "catalog"
    finally:
        service.close()


@pytest.mark.parametrize("collection", ["archived", None, " library "])
@pytest.mark.parametrize("include_public", [False, True])
def test_mirror_then_outage_does_not_promote_ambiguous_or_personal_notices(
    tmp_path, collection, include_public,
):
    service = _service(tmp_path)
    calls, batches = [], []
    try:
        session = _FakeSession("bad")
        template = session.search("Maritime", "all")[0]
        private = replace(template, record_id="private", title="Maritime PRIVATE",
                          source_collection=collection, owned=False)
        entries = [private]
        if include_public:
            entries.append(replace(template, record_id="public", title="Maritime public",
                                   source_collection="public", owned=False))
        service._mirror_remote_results(entries)
        # A non-owned private result can have remote_catalog state: that
        # state alone is not evidence of visibility to the public.
        before = service.storage.catalog_entries()
        assert {row["local_state"] for row in before} == {"remote_catalog"}
        def unavailable(*args, **kwargs):
            calls.append("network")
            raise httpx.ReadTimeout("PRIVATE upstream URL")
        session.search = unavailable
        service._sessions = {"bad": session}
        summary = service.search_progressive("Maritime", force=True, on_batch=batches.append)
        assert len(calls) == len(batches) == 1
        assert summary.providers_failed == 1
        assert summary.result_count == int(include_public)
        assert [row.record_id for row in batches[0].results] == (["public"] if include_public else [])
        assert "PRIVATE" not in str(batches) + str(summary)
        assert service.storage.catalog_entries() == before
        assert not any(row["namespace"] == "search" for row in service.storage.cache_namespace_stats())
        if not include_public:
            assert "aucun résultat connu" in batches[0].warnings[0]
    finally:
        service.close()
