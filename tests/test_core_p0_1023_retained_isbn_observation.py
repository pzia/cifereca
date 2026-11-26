"""#1023: retain exact record ISBN evidence without deriving edition links."""
import json
from contextlib import closing

import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def _row(record, values, **changes):
    proof = {
        "source": "bnf.sru", "source_field": "dc.identifier",
        "scope": "search", "record_id": record,
        "confidence": "explicit_record_field", "freshness": "search_response",
        "relationship": "record_identifier", **changes,
    }
    return verified_public(
        record_id=record, title="Même titre",
        metadata={"isbns": values, "bibliography_evidence": {"isbns": proof}},
    )


def test_isbn_observation_survives_eviction_and_restart_without_join(tmp_path):
    path = tmp_path / "isbn.sqlite"
    values = ["9780306406157", "0306406152"]
    with closing(Storage(path)) as db:
        first = persist(db, _row("edition-a", values))
        second = persist(db, _row("edition-b", values))
        foreign = persist(db, _row("edition-c", values, record_id="foreign"))
        canonical = [index.persist_exact_public_search_cover(db, source)
                     for source in (first, second, foreign)]
        assert len({c.document for c in canonical}) == 3
        def check(storage):
            page = search_public_notice_c(storage, "même titre", now=100,
                                          group_by_document=True)
            assert page.known_matches == page.known_documents == 3
            hits = {hit.record_id: hit for hit in page.hits}
            assert hits["edition-a"].isbn_observation.isbns == tuple(values)
            assert hits["edition-b"].isbn_observation.record_id == "edition-b"
            assert hits["edition-c"].isbn_observation is None
            assert hits["edition-a"].document != hits["edition-b"].document
            assert storage.entitlements() == [] and storage.acquisitions() == []
        check(db)
        for source in (first, second, foreign):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        check(db)
        retained = index.read_consolidated_notice_cover(db, canonical[0].document)
        assert retained.sources[0].public_bibliography.isbn_observation.isbns == tuple(values)
        key = index._db_key(retained.document)
        raw = json.loads(db.metadata_get(key))
        raw["sources"][first.document.source_key]["public_bibliography"]["isbn_observation"]["record_id"] = "foreign"
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            index.read_consolidated_notice_cover(db, retained.document)
        page = search_public_notice_c(db, "même titre", now=100,
                                      group_by_document=True)
        assert "edition-a" not in {hit.record_id for hit in page.hits}


def test_strict_isbn_cursor_matches_only_attested_records_before_pagination(tmp_path):
    path = tmp_path / "isbn-search.sqlite"
    with closing(Storage(path)) as db:
        rows = (
            _row("edition-a", ["9780306406157"]),
            _row("edition-b", ["0306406152"]),
            _row("foreign-proof", ["9780306406157"], record_id="other"),
            _row("other-isbn", ["9780140328721"]),
            verified_public(record_id="9780306406157", title="Même titre"),
        )
        saved = [persist(db, row) for row in rows]
        for source in saved:
            index.persist_exact_public_search_cover(db, source)

        def check(storage):
            for query in ("9780306406157", "978-0-306-40615-7",
                          "0306406152", "0-306-40615-2"):
                page = search_public_notice_c(storage, query, field="isbn",
                                              page_size=1, now=100,
                                              group_by_document=True)
                assert page.status == "partial" and page.has_more
                assert page.known_matches == page.known_documents == 2
                next_page = search_public_notice_c(storage, query, field="isbn",
                                                   page_size=1, page=1, now=100,
                                                   group_by_document=True)
                assert not next_page.has_more
                assert {page.hits[0].record_id, next_page.hits[0].record_id} == {
                    "edition-a", "edition-b",
                }
                assert page.hits[0].document != next_page.hits[0].document
            for query in ("978030", "9780306406158", "4006381333931"):
                assert not search_public_notice_c(storage, query, field="isbn",
                                                  now=100).hits
            assert not search_public_notice_c(storage, "9780306406157",
                                              field="isbn", provider_ids=("other",),
                                              now=100).hits
            assert storage.entitlements() == [] and storage.acquisitions() == []

        check(db)
        for source in saved:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        check(db)
