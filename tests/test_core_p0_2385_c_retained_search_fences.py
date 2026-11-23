"""#2385: a retained Notice C is searchable without inventing freshness.

These tests mutate ONLY a disposable SQLite fixture, not real provider
records or user library state. Qualified Core eviction carries an advanced
CAS fence; manual/legacy loss does not.
"""
import pytest

from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_cover_repository import (
    _FENCE_PREFIX, _PREFIX, _opaque_key, evict_notice_cover,
)
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins


def _fixture(core, tmp_path):
    _real_origins(core.storage, tmp_path)
    a, b = _rows()
    pairs = search._ingest_public_search_c(
        core.library, [a, b], identity_id="alice", now=100,
        require_public_cover_witness=True,
    )
    assert len(pairs) == 2
    document = a._core_notice_canonical_cover_document_ref
    assert document == b._core_notice_canonical_cover_document_ref
    assert read_consolidated_notice_cover(core.storage, document) is not None
    return pairs, document


def _search(storage):
    return search_public_notice_c(
        storage, "Même titre", group_by_document=True, page_size=1, now=101,
    )


@pytest.mark.parametrize("delete_fences", [False, True])
def test_unqualified_source_loss_remains_searchable_but_stale(tmp_path, delete_fences):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pairs, document = _fixture(core, tmp_path)
        with core.storage._lock, core.storage._connect() as db:
            for _, source, _ in pairs:
                key = _opaque_key(source.document)
                assert key is not None and key.startswith(_PREFIX)
                fence_key = _FENCE_PREFIX + key[len(_PREFIX):]
                assert db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
                assert db.execute("SELECT 1 FROM metadata WHERE key=?", (fence_key,)).fetchone()
                # Simulated non-qualified deletion, TEST database only.
                db.execute("DELETE FROM metadata WHERE key=?", (key,))
                if delete_fences:
                    db.execute("DELETE FROM metadata WHERE key=?", (fence_key,))
        page = _search(core.storage)
        assert page.status == "partial"
        assert page.known_documents == 1
        assert page.known_matches == 2
        assert len(page.hits) == 1 and len(page.matching_sources) == 2
        assert all(hit.stale and hit.cover_state == "pending"
                   for hit in page.matching_sources)
        assert page.hits[0].document == document
        assert not page.has_more
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_core_qualified_ttl_eviction_retains_current_search_evidence(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pairs, document = _fixture(core, tmp_path)
        for _, source, _ in pairs:
            assert evict_notice_cover(
                core.storage, source.document, expected_epoch=source.epoch,
            ) is True
        page = _search(core.storage)
        assert page.status == "partial"
        assert page.known_matches == 2 and page.known_documents == 1
        assert len(page.hits) == 1 and len(page.matching_sources) == 2
        assert all(not hit.stale and hit.cover_state == "available"
                   for hit in page.matching_sources)
        assert page.hits[0].document == document
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
