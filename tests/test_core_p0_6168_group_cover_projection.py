"""#6168/#2385: one attested multi-provider C retains both cover candidates."""
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_two_attested_providers_one_c_two_retained_covers(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        pairs = search._ingest_public_search_c(
            core.library, [a, b], identity_id="alice", now=100,
            require_public_cover_witness=True,
        )
        assert len(pairs) == 2
        refs = {row._core_notice_canonical_cover_document_ref for row in (a, b)}
        assert len(refs) == 1
        document, = refs
        snapshot = read_consolidated_notice_cover(core.storage, document)
        assert snapshot is not None
        assert {source.provider_id for source in snapshot.sources} == {"seller-a", "seller-b"}
        assert {cover.url for cover in snapshot.candidates} == {A_URL, B_URL}
        assert snapshot.snapshot_complete
