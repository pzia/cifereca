"""The loaded public C filter must not change explicit index semantics."""
from contextlib import closing

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.search import filter_loaded_results, local_search_evidence
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_c_loaded_fields_keep_exact_index_semantics_after_restart(tmp_path):
    path = tmp_path / "fields.sqlite"
    isbn_shaped_record = "9780306406157"
    with closing(Storage(path)) as db:
        source = persist(db, verified_public(
            record_id=isbn_shaped_record, title="Le prix-du-quai",
            author="Anne Claire", publisher="Éditions du Nord",
        ))
        canonical = persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        page = search_public_notice_c(db, "prix", group_by_document=True, now=100)
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert row["_cover_document_ref"] == canonical.document
        assert row["item_id"] == isbn_shaped_record
        for field, query, match in (
            ("title", "prix-du-quai", True),
            ("title", "prix quai", False),
            ("author", "Anne Claire", True),
            ("author", "Claire Anne", False),
            ("publisher", "éditions du nord", True),
            ("publisher", "prix", False),
            ("publisher", "Anne", False),
        ):
            assert bool(search_public_notice_c(db, query, field=field, now=100).hits) is match
            assert bool(filter_loaded_results([row], query, field=field)) is match
        assert filter_loaded_results([row], isbn_shaped_record, field="isbn") == []
        assert filter_loaded_results([row], isbn_shaped_record, field="all") == []
        assert local_search_evidence(row, isbn_shaped_record) == (0, ())
        assert db.entitlements() == [] and db.acquisitions() == []


def test_explicit_publisher_is_not_a_generic_all_fields_match():
    row = {"title": "Nord", "author": "Anne", "metadata": {"publisher": "Sud"}}
    assert filter_loaded_results([row], "sud", field="publisher") == [row]
    assert filter_loaded_results([row], "nord", field="publisher") == []
    assert filter_loaded_results([row], "Anne", field="publisher") == []
