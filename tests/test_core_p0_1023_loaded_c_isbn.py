"""#1023: ISBN loaded filtering retains only sealed, exact C evidence."""
from contextlib import closing
from dataclasses import replace

from cifereca.core.document_filter import filter_loaded_with_documentary_proofs
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.search import filter_loaded_results
from cifereca.storage import Storage
from tests.test_core_p0_1023_retained_isbn_observation import _row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def test_loaded_c_isbn_uses_sealed_primary_not_metadata_or_opaque_id(tmp_path):
    with closing(Storage(tmp_path / "loaded-isbn.sqlite")) as db:
        persist(db, _row("record-a", ["9780306406157"]))
        persist(db, _row("9780306406157", ["9780140328721"]))
        page = search_public_notice_c(db, "même titre", now=100)
        rows = merge_public_notice_c_known_rows([], page, identity_id="alice")
        for query in ("9780306406157", "0306406152", "978-0-306-40615-7"):
            assert [r["item_id"] for r in filter_loaded_results(
                rows, query, field="isbn",
            )] == ["record-a"]
        assert not filter_loaded_results(rows, "978030", field="isbn")
        first = next(r for r in rows if r["item_id"] == "record-a")
        forged = {k: v for k, v in first.items() if not k.startswith("_core_")}
        forged["metadata"] = dict(first["metadata"], isbn="9780306406157")
        assert not filter_loaded_results([forged], "9780306406157", field="isbn")
        # A mutated frozen observation must not authorize another match.
        hit = next(h for h in page.hits if h.record_id == "record-a")
        object.__setattr__(hit.isbn_observation, "isbns", ("9780140328721",))
        assert not filter_loaded_results([first], "9780140328721", field="isbn")
        assert not filter_loaded_results([first], "9780306406157", field="isbn")
        assert db.entitlements() == [] and db.acquisitions() == []


def test_grouped_loaded_isbn_does_not_mix_independent_sources(tmp_path):
    with closing(Storage(tmp_path / "group-isbn.sqlite")) as db:
        persist(db, _row("record-a", ["9780306406157"]))
        persist(db, _row("record-b", ["9780140328721"]))
        page = search_public_notice_c(db, "même titre", now=100)
        first, second = sorted(page.hits, key=lambda hit: hit.record_id)
        # Construct the same grouped page shape as the canonical cursor:
        # the source contributions stay independent under a single C ref.
        second = replace(second, document=first.document, epoch=first.epoch)
        grouped = replace(page, hits=(first,), matching_sources=(first, second),
                          known_documents=1)
        row, = merge_public_notice_c_known_rows([], grouped, identity_id="alice")
        matches = filter_loaded_with_documentary_proofs([row], "9780140328721", field="isbn")
        assert len(matches) == 1 and matches[0]["item_id"] == "record-a"
        object.__setattr__(second.isbn_observation, "isbns", ("9780306406157",))
        assert not filter_loaded_with_documentary_proofs(
            [row], "9780140328721", field="isbn",
        )
        assert filter_loaded_with_documentary_proofs(
            [row], "9780306406157", field="isbn",
        )
