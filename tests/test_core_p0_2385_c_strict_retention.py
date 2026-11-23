"""#2385: retained C must never claim *current* N2 without source epochs.

All mutations here are on a disposable SQLite fixture only, never a real
library. An authorized eviction is different from an unqualified DELETE.
"""
import pytest

from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import (
    read_consolidated_notice_cover, read_current_consolidated_public_cover,
)
from cifereca.core.notice_cover_repository import (
    _FENCE_PREFIX, _PREFIX, _opaque_key, evict_notice_cover,
)
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins


def _seed(core, tmp_path):
    _real_origins(core.storage, tmp_path)
    a, b = _rows()
    pairs = search._ingest_public_search_c(
        core.library, [a, b], identity_id="alice", now=100,
        require_public_cover_witness=True,
    )
    assert len(pairs) == 2
    doc = a._core_notice_canonical_cover_document_ref
    assert doc == b._core_notice_canonical_cover_document_ref
    canonical = read_consolidated_notice_cover(core.storage, doc)
    assert canonical is not None and len(canonical.sources) == 2
    assert read_current_consolidated_public_cover(core.storage, doc) == canonical
    return pairs, doc, canonical


@pytest.mark.parametrize("remove_fence", [False, True])
def test_unqualified_source_loss_does_not_leave_false_current_cover(tmp_path, remove_fence):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pairs, document, canonical = _seed(core, tmp_path)
        source = pairs[0][1]
        with core.storage._lock, core.storage._connect() as db:
            key = _opaque_key(source.document)
            assert key is not None and key.startswith(_PREFIX)
            fence_key = _FENCE_PREFIX + key[len(_PREFIX):]
            assert db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
            assert db.execute("SELECT 1 FROM metadata WHERE key=?", (fence_key,)).fetchone()
            # Simulate legacy/manual loss of an observation in TEST DB only.
            # This is NOT a qualified Core TTL eviction.
            db.execute("DELETE FROM metadata WHERE key=?", (key,))
            if remove_fence:
                db.execute("DELETE FROM metadata WHERE key=?", (fence_key,))
        # C still retains the provenance; the strict N2 freshness path must
        # not use the unverified contribution to make a current cover choice.
        assert read_consolidated_notice_cover(core.storage, document) == canonical
        assert read_current_consolidated_public_cover(core.storage, document) is None
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_qualified_source_evictions_keep_durable_c_readable_as_current(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pairs, document, canonical = _seed(core, tmp_path)
        for _, source, _ in pairs:
            assert evict_notice_cover(
                core.storage, source.document, expected_epoch=source.epoch,
            ) is True
        # The retained source epochs are still attested by the advanced,
        # evicted=True durable CAS fences; no network or reparsing is needed.
        assert read_consolidated_notice_cover(core.storage, document) == canonical
        assert read_current_consolidated_public_cover(core.storage, document) == canonical
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
