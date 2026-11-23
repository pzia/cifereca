"""#2385: same display title is not documentary identity for public C."""
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_search_repair import _admitted
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_identical_title_unrelated_edition_never_joins_attested_c(tmp_path):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        unrelated = _admitted(
            "Même titre", "unrelated-edition", source_provider="seller-c",
            thumbnail_url="https://covers.example.org/unrelated-c.jpg",
        )
        pairs = search._ingest_public_search_c(
            core.library, [a, b, unrelated], identity_id="alice", now=100,
            require_public_cover_witness=True,
        )
        assert len(pairs) == 3
        joined = a._core_notice_canonical_cover_document_ref
        assert joined == b._core_notice_canonical_cover_document_ref
        other = unrelated._core_notice_canonical_cover_document_ref
        assert other != joined
        common = read_consolidated_notice_cover(core.storage, joined)
        separate = read_consolidated_notice_cover(core.storage, other)
        assert common is not None and separate is not None
        assert {s.provider_id for s in common.sources} == {"seller-a", "seller-b"}
        assert {c.url for c in common.candidates} == {A_URL, B_URL}
        assert {s.provider_id for s in separate.sources} == {"seller-c"}
        assert {c.url for c in separate.candidates} == {
            "https://covers.example.org/unrelated-c.jpg",
        }
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
