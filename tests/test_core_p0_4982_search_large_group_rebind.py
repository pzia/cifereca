"""Search group rebinding beyond one bounded source writer chunk."""
from cifereca.core import notice_c_group_batch_publication as batch
from cifereca.core import search_orchestration as search
from cifereca.core import library_projection
from tests.test_core_p0_4982_search_atomic_groups import (
    _budget, _rows, _ingest, _job_refs, _forbid,
)
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_4982_search_repair import _admitted
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL


def test_large_progressive_rebind_keeps_exact_group_with_one_graph_snapshot(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        independent = [_admitted("Témoin indépendant", f"unrelated-{i:04d}")
                       for i in range(260)]
        first = _ingest(core, [a, *independent])
        second = _ingest(core, [b])
        assert len(first) == 261 and len(second) == 1
        own_a = first[0][2].document
        shared = b._core_notice_canonical_cover_document_ref
        assert shared != own_a
        original = library_projection._read_provider_origin_coordinates
        snapshots = []
        def counted(db, **kwargs):
            assert db.in_transaction
            snapshots.append(True)
            return original(db, **kwargs)
        monkeypatch.setattr(library_projection, "_read_provider_origin_coordinates", counted)
        monkeypatch.setattr(batch, "_persist_origin_linked_public_cover_group_txn", _forbid)
        search._reconcile_progressive_public_c(
            core.storage, first + second, restore_own=True, read_only=True,
        )
        assert snapshots == [True]
        assert a._core_notice_canonical_cover_document_ref == shared
        assert b._core_notice_canonical_cover_document_ref == shared
        assert all(row._core_notice_canonical_cover_document_ref == own.document
                   for row, _, own in first[1:])
        assert _job_refs(core.storage) == ()
        assert core.brokers["tasks"]._executor is None


def test_older_duplicate_does_not_hide_current_source_or_receive_new_group_stamp(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        earlier = _ingest(core, [a])
        other = _ingest(core, [b])
        updated = _admitted("Même titre", "edition-a", source_provider="seller-a",
                            thumbnail_url=A_URL, description="Nouveau résumé")
        recent = _ingest(core, [updated], now=101)
        assert earlier[0][1].document == recent[0][1].document
        assert earlier[0][1].epoch < recent[0][1].epoch
        shared = updated._core_notice_canonical_cover_document_ref
        assert shared != recent[0][2].document
        # Adversarial ordering: the outdated duplicate is last.
        search._reconcile_progressive_public_c(
            core.storage, recent + other + earlier, restore_own=True, read_only=True,
        )
        assert updated._core_notice_canonical_cover_document_ref == shared
        assert b._core_notice_canonical_cover_document_ref == shared
        assert a._core_notice_canonical_cover_document_ref == earlier[0][2].document
        assert not hasattr(a, "_core_notice_cross_provider_proof")
        assert _job_refs(core.storage) == ()
        assert core.storage.entitlements() == [] and core.storage.acquisitions() == []
