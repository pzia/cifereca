"""#2385: a public multi-provider C projection remains readable after Core restart.

The read path must not require another Search ingestion or a provider request.
"""
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_attested_multi_provider_c_survives_restart_without_reingest(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
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

    def unexpected_ingest(*args, **kwargs):
        raise AssertionError("read-only restart must not re-ingest or fetch a source")

    monkeypatch.setattr(search, "_ingest_public_search_c", unexpected_ingest)
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        snapshot = read_consolidated_notice_cover(core.storage, document)
        assert snapshot is not None
        assert snapshot.document == document
        assert {source.provider_id for source in snapshot.sources} == {"seller-a", "seller-b"}
        assert {cover.url for cover in snapshot.candidates} == {A_URL, B_URL}
        assert snapshot.snapshot_complete
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
