"""#1023 C1: exact attested ISBN in all-fields C index and loaded ranking."""
from contextlib import closing

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.search import filter_loaded_results, local_search_evidence, local_match_explanation
from cifereca.storage import Storage
from tests.test_core_p0_1023_retained_isbn_observation import _row
from tests.test_core_p0_6366_c_first_known_search import _known
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_all_fields_isbn_exact_and_ranked_after_restart(tmp_path):
    path = tmp_path / "all-isbn.sqlite"
    with closing(Storage(path)) as db:
        rows = (
            _row("isbn-a", ["9780306406157"]),
            _row("isbn-b", ["0306406152"]),
            _row("foreign-proof", ["9780306406157"], record_id="other"),
            verified_public(record_id="9780306406157", title="Même titre"),
            verified_public(record_id="title", title="Étude 9780306406157"),
        )
        for row in rows:
            source = persist(db, row)
            persist_exact_public_search_cover(db, source)
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        loaded = _known(db, "même titre")
        for query in ("9780306406157", "0306406152", "978-0-306-40615-7"):
            selected = filter_loaded_results(loaded, query, field="all")
            assert {row["item_id"] for row in selected} == {"isbn-a", "isbn-b"}
            assert all(local_search_evidence(row, query) == (10, ("identifier",))
                       for row in selected)
            assert all(local_match_explanation(row, query) == "Correspondance : Identifiant"
                       for row in selected)
            page = search_public_notice_c(db, query, now=100, field="all", group_by_document=True)
            expected = {"isbn-a", "isbn-b"} | ({"title"} if query == "9780306406157" else set())
            assert {hit.record_id for hit in page.hits} == expected
            assert page.known_matches == page.known_documents == len(expected)
            assert page.status == "partial"
        ranked = _known(db, "9780306406157")
        assert ranked[0]["item_id"] == "title"
        assert {row["item_id"] for row in ranked[1:]} == {"isbn-a", "isbn-b"}
        assert filter_loaded_results(loaded, "978030", field="all") == []
        forged = next(row for row in loaded if row["item_id"] == "9780306406157")
        forged["metadata"]["isbn"] = "9780306406157"
        assert local_search_evidence(forged, "9780306406157") == (0, ())
        assert db.entitlements() == [] and db.acquisitions() == []
