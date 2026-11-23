"""#2385: a public C is documentary, not scoped to the Search caller identity."""
from cifereca.core import search_orchestration as search
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from tests.test_core_p0_4982_search_atomic_groups import _budget, _rows
from tests.test_core_p0_4982_notice_work_runtime import open_core
from tests.test_core_p0_6366_library_recent_public_c import _real_origins, A_URL, B_URL


def test_public_c_cold_read_independent_of_search_identity(tmp_path, monkeypatch):
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        pairs = search._ingest_public_search_c(
            core.library, [a, b], identity_id="profile-a", now=100,
            require_public_cover_witness=True,
        )
        assert len(pairs) == 2
        document = a._core_notice_canonical_cover_document_ref
        first = read_consolidated_notice_cover(core.storage, document)
        assert first is not None

    def forbidden(*args, **kwargs):
        raise AssertionError("a second identity must not trigger provider ingestion")

    monkeypatch.setattr(search, "_ingest_public_search_c", forbidden)
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        # The public C read API has no identity argument. Both profiles read
        # the same persisted public documentary reference, not private rights.
        for identity_id in ("profile-a", "profile-b"):
            assert identity_id
            current = read_consolidated_notice_cover(core.storage, document)
            assert current == first
            assert {source.provider_id for source in current.sources} == {
                "seller-a", "seller-b",
            }
            assert {cover.url for cover in current.candidates} == {A_URL, B_URL}
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_same_title_without_attested_origin_does_not_join_public_c(tmp_path):
    """A matching title is not evidence of canonical documentary identity."""
    from tests.test_core_p0_4982_search_repair import _admitted

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        unrelated = _admitted("Même titre", "unrelated-edition", source_provider="seller-c", thumbnail_url=A_URL)
        pairs = search._ingest_public_search_c(
            core.library, [a, b, unrelated], identity_id="profile-a", now=100,
            require_public_cover_witness=True,
        )
        assert len(pairs) == 3
        assert a._core_notice_canonical_cover_document_ref == b._core_notice_canonical_cover_document_ref
        assert unrelated._core_notice_canonical_cover_document_ref != a._core_notice_canonical_cover_document_ref
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_public_c_pagination_uses_document_group_not_provider_rows(tmp_path):
    """A canonical multi-provider edition must occupy one grouped page slot."""
    from cifereca.core.notice_c_public_search import search_public_notice_c

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        search._ingest_public_search_c(
            core.library, [a, b], identity_id="profile-a", now=100,
            require_public_cover_witness=True,
        )
        canonical = a._core_notice_canonical_cover_document_ref
        assert canonical == b._core_notice_canonical_cover_document_ref
        first = search_public_notice_c(
            core.storage, "Même titre", group_by_document=True,
            page=0, page_size=1, now=101,
        )
        second = search_public_notice_c(
            core.storage, "Même titre", group_by_document=True,
            page=1, page_size=1, now=101,
        )
        assert len(first.hits) == 1
        assert first.hits[0].document == canonical
        assert second.hits == ()
        assert not first.has_more
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_grouped_public_c_search_survives_restart_without_provider_fetch(tmp_path, monkeypatch):
    """Cold pagination reads the durable canonical C, never a viewer mirror."""
    from cifereca.core.notice_c_public_search import search_public_notice_c

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        search._ingest_public_search_c(
            core.library, [a, b], identity_id="profile-a", now=100,
            require_public_cover_witness=True,
        )
        expected = a._core_notice_canonical_cover_document_ref
        assert expected == b._core_notice_canonical_cover_document_ref

    def forbidden(*args, **kwargs):
        raise AssertionError("cold C pagination must not fetch or reingest providers")

    monkeypatch.setattr(search, "_ingest_public_search_c", forbidden)
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        first = search_public_notice_c(
            core.storage, "Même titre", group_by_document=True,
            page=0, page_size=1, now=101,
        )
        assert len(first.hits) == 1
        assert first.hits[0].document == expected
        assert first.known_documents == 1
        assert len(first.matching_sources) == 2
        assert not first.has_more
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []


def test_cold_grouped_pages_keep_two_distinct_documents_and_source_lineage(tmp_path, monkeypatch):
    """#2385: page boundaries count documents, never duplicate source rows."""
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from tests.test_core_p0_4982_search_repair import _admitted

    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        _real_origins(core.storage, tmp_path)
        a, b = _rows()
        unrelated = _admitted(
            "Même titre", "unrelated-edition", source_provider="seller-c",
            thumbnail_url=A_URL,
        )
        search._ingest_public_search_c(
            core.library, [a, b, unrelated], identity_id="profile-a", now=100,
            require_public_cover_witness=True,
        )
        grouped = a._core_notice_canonical_cover_document_ref
        distinct = unrelated._core_notice_canonical_cover_document_ref
        assert grouped != distinct

    def forbidden(*args, **kwargs):
        raise AssertionError("cold pagination must not reingest providers")

    monkeypatch.setattr(search, "_ingest_public_search_c", forbidden)
    with open_core(tmp_path, notice_work_admission=_budget()) as core:
        pages = [search_public_notice_c(
            core.storage, "Même titre", group_by_document=True,
            page=i, page_size=1, now=101,
        ) for i in range(3)]
        assert {page.hits[0].document for page in pages[:2]} == {grouped, distinct}
        assert pages[2].hits == ()
        assert pages[0].has_more
        assert not pages[1].has_more
        assert all(page.known_documents == 2 for page in pages)
        assert {hit.document for hit in pages[0].matching_sources + pages[1].matching_sources} == {grouped, distinct}
        assert core.storage.entitlements() == []
        assert core.storage.acquisitions() == []
