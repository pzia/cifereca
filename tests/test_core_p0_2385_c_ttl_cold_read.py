"""#2385: a canonical public C survives source-observation TTL eviction and restart."""
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_cover_repository import _opaque_key
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_cold_c_retains_exact_public_sources_after_observation_eviction(tmp_path, monkeypatch):
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
        # Simulate TTL eviction of exact source payloads, retaining canonical
        # C and the durable source fences; do not delete any canonical row.
        with core.storage._lock, core.storage._connect() as db:
            for _, source, _ in pairs:
                key = _opaque_key(source.document)
                assert key is not None
                assert db.execute("SELECT 1 FROM metadata WHERE key=?", (key,)).fetchone()
                db.execute("DELETE FROM metadata WHERE key=?", (key,))
        assert read_consolidated_notice_cover(core.storage, document) is not None

    def forbidden(*args, **kwargs):
        raise AssertionError("cold C projection must not reingest sources")

    monkeypatch.setattr(search, "_ingest_public_search_c", forbidden)
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        retained = read_consolidated_notice_cover(core.storage, document)
        assert retained is not None
        assert retained.snapshot_complete
        assert {source.provider_id for source in retained.sources} == {"seller-a", "seller-b"}
        assert {cover.url for cover in retained.candidates} == {A_URL, B_URL}
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
