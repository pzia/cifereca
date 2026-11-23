"""#4980/#6168: canonical N2 read-through survives exact source eviction."""
from types import SimpleNamespace
from cifereca.core import search_orchestration as search
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_canonical_n2_keeps_two_public_cover_options_after_source_ttl(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        pairs = search._ingest_public_search_c(
            core.library, [a, b], identity_id="alice", now=100,
            require_public_cover_witness=True,
        )
        assert len(pairs) == 2
        document = a._core_notice_canonical_cover_document_ref
        assert b._core_notice_canonical_cover_document_ref == document
        reader = SimpleNamespace(storage=core.storage)
        before = cached_notice_cover_snapshot(reader, document)
        assert before is not None and {c.url for c in before.candidates} == {A_URL, B_URL}
        for _, source, _ in pairs:
            assert evict_notice_cover(core.storage, source.document, expected_epoch=source.epoch)
        after = cached_notice_cover_snapshot(reader, document)
        assert after is not None
        assert after.scope == before.scope
        assert after.revision == before.revision
        assert after.candidates == before.candidates
