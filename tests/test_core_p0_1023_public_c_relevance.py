"""#1023 — C-first relevance parity on the real persisted public C cursor.

Synthetic admitted notices, not a measurement of Kobo site or collection.
No supplier/session/rights access, no new index.
"""
import pytest

from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.storage import Storage
from tests.test_core_p0_6366_canonical_bibliography_retention import (
    _make_c, _drop_short_source,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public, persist


def test_public_c_relevance_before_and_after_source_eviction(tmp_path):
    db = Storage(tmp_path / "relevance.sqlite")
    try:
        title, strong = _make_c(db, verified_public(
            record_id="strong", title="Prix-du-Quai", author="Une autrice",
        ))
        split, weak = _make_c(db, verified_public(
            record_id="split", title="Le prix", description="Une enquête sur le quai",
        ))
        summary, last = _make_c(db, verified_public(
            record_id="summary", title="Autre titre", description="Prix du quai",
        ))
        absent, _ = _make_c(db, verified_public(
            record_id="absent", title="Le prix littéraire",
        ))

        def check():
            for query in ("prix du quai", "PRIX quai", "prix quai"):
                page = search_public_notice_c(db, query, now=100,
                                              group_by_document=True)
                assert page.status == "partial"
                assert page.known_matches == page.known_documents == 3
                assert [hit.document for hit in page.hits] == [
                    strong.document, weak.document, last.document,
                ]
                assert [hit.record_id for hit in page.hits] == [
                    "strong", "split", "summary",
                ]
            phrase = search_public_notice_c(db, '"prix du quai"', now=100)
            assert {hit.record_id for hit in phrase.hits} == {"strong", "summary"}
            # An explicitly targeted field keeps the old substring semantics,
            # never matches a summary or normalizes away the selected field.
            assert search_public_notice_c(db, "prix du quai", field="title", now=100).hits == ()
            assert [hit.record_id for hit in search_public_notice_c(
                db, "prix-du-quai", field="title", now=100,
            ).hits] == ["strong"]
            for index, expected in enumerate(("strong", "split", "summary")):
                page = search_public_notice_c(db, "prix du quai", now=100,
                                              page=index, page_size=1)
                assert [hit.record_id for hit in page.hits] == [expected]
                assert page.has_more == (index < 2)
            assert db.entitlements() == [] and db.acquisitions() == []
        check()
        for source in (title, split, summary, absent):
            _drop_short_source(db, source)
        check()
    finally:
        db.close()


@pytest.mark.parametrize("evicted", (False, True))
def test_group_does_not_join_terms_across_provider_contributions(tmp_path, evicted):
    db = Storage(tmp_path / "no-cross-source.sqlite")
    try:
        a = persist(db, verified_public(
            source_provider="seller-a", record_id="a",
            title="Le prix", description="Une cérémonie",
        ))
        b = persist(db, verified_public(
            source_provider="seller-b", record_id="b",
            title="Le quai", description="Une enquête",
        ))
        edition = a.observation.key.edition_id
        for source in (a, b):
            persist_consolidated_cover_source(
                db, canonical_edition_id=edition, source=source,
                relation_proof_id="strong:fixture:group",
            )
        if evicted:
            for source in (a, b):
                _drop_short_source(db, source)
        page = search_public_notice_c(db, "prix du quai", now=100,
                                     group_by_document=True)
        assert not page.hits
        assert page.status == "partial"
        assert page.known_documents == page.known_matches == 0
        assert [hit.provider_id for hit in search_public_notice_c(
            db, "quai", now=100, provider_ids=("seller-b",),
        ).hits] == ["seller-b"]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
