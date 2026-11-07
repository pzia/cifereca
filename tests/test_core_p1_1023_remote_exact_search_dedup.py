"""#1023: supplier notices must survive both search deduplication stages.

Exercise real Core search facades and the SQLite mirror, without network/GTK.
A provider may legitimately reuse its record ID for different account,
document-base or collection coordinates. These are NOT matching editions.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.search_orchestration import _search_result_scope_key
from tests.test_catalog_083 import _service
from tests.test_core043_progress_cache_stats import _FakeSession
from tests.test_core_p0_1354_mirrored_source_scope import notice


# #4909: this is a provider-agnostic exact-key contract. Do not use Paris:
# authenticated foreign Paris credentials now enter a separate read-only
# catalogue projection and cannot represent multiple accounts in one session.
_PROVIDER = "fixture-shared-catalogue"


def _notices():
    base = replace(notice(), source_provider=_PROVIDER)
    return [
        base,
        replace(notice(credential="reader-B"), source_provider=_PROVIDER),
        replace(notice(docbase="EDITION-2"), source_provider=_PROVIDER),
        replace(notice(collection="catalogue-B"), source_provider=_PROVIDER),
        # A real repeat of the same exact account + edition remains a duplicate.
        replace(base, id=base.id + ":repeated"),
    ]


def _scopes(rows):
    return {
        (r.source_provider, r.source_credential, r.docbase,
         r.record_id, r.source_collection) for r in rows
    }


@pytest.mark.parametrize("progressive", [False, True])
def test_supplier_id_collisions_survive_fresh_query_batches_and_sqlite(
    tmp_path, progressive,
):
    service = _service(str(tmp_path))
    try:
        session = _FakeSession(_PROVIDER)
        received = _notices()
        calls = []
        def search(query, scope, *, field="all", content_types=()):
            calls.append((query, scope, field, content_types))
            return received
        session.search = search
        service._sessions = {_PROVIDER: session}
        batches = []

        if progressive:
            summary = service.search_progressive(
                "livre partagé", force=True, provider_id=_PROVIDER,
                on_batch=batches.append,
            )
            assert not summary.cancelled
            assert summary.providers_failed == 0
            assert summary.result_count == 4
            assert len(batches) == 1
            assert _scopes(batches[0].results) == _scopes(received)
            assert len(batches[0].results) == 4
            rows = service.last_search
        else:
            rows = service.search(
                "livre partagé", force=True, provider_id=_PROVIDER,
            )
        assert calls, "must execute the selected provider, not only SQLite"
        assert len(rows) == 4
        assert _scopes(rows) == _scopes(received)
        assert all(r.owned is None and r.downloadable is None for r in rows)

        saved = service.storage.catalog_entries(
            provider_id=_PROVIDER, identity_id="default",
        )
        assert len(saved) == 4
        assert len({r["catalog_entry_id"] for r in saved}) == 4
        assert {
            (r["metadata"]["source_credential"], r["metadata"]["docbase"],
             r["metadata"]["source_collection"])
            for r in saved
        } == {
            ("reader-A", "EDITION-1", "catalogue-A"),
            ("reader-B", "EDITION-1", "catalogue-A"),
            ("reader-A", "EDITION-2", "catalogue-A"),
            ("reader-A", "EDITION-1", "catalogue-B"),
        }
        known = service.search_known_catalog(
            "livre partagé", identity_id="default", provider_ids=[_PROVIDER],
        )
        assert len(known) == 4
        assert service.storage.entitlements() == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()


def test_exact_dedup_key_matches_mirror_collection_absence_convention():
    base = notice()
    no_collection = replace(base, source_collection=None)
    empty_collection = replace(base, source_collection="")
    other_collection = replace(base, source_collection="collection-2")
    assert _search_result_scope_key(no_collection) == _search_result_scope_key(empty_collection)
    assert _search_result_scope_key(no_collection) != _search_result_scope_key(other_collection)
    assert _search_result_scope_key(base) != _search_result_scope_key(
        replace(base, source_credential="reader-B")
    )
