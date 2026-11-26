"""#1023 C7: explicit local C fields remain available during public outage."""
from contextlib import closing

from cifereca.core.known_catalog_fallback import known_public_fallback_page
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_1023_retained_isbn_observation import _row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_offline_c_explicit_fields_after_eviction_restart(tmp_path):
    path = tmp_path / "explicit-offline.sqlite"
    with closing(Storage(path)) as db:
        rows = (
            _row("isbn-a", ["9780306406157"]),
            _row("isbn-b", ["0306406152"]),
            verified_public(record_id="9780306406157", title="Même titre"),
            verified_public(record_id="subject-a", title="Même titre",
                            metadata={"subjects": ["Astronomie"]}),
        )
        for row in rows:
            source = persist(db, row)
            persist_exact_public_search_cover(db, source)
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        assert db.catalog_entries() == []
    with closing(Storage(path)) as db:
        def page(field, query, **options):
            args = dict(provider_id="bnf", identity_id="alice", scope="all",
                        field=field, query=query, content_types=(), now=100)
            args.update(options)
            return known_public_fallback_page(db, **args)

        for viewer in ("alice", "bob"):
            first = page("isbn", "0-306-40615-2", identity_id=viewer, page_size=1)
            second = page("isbn", "9780306406157", identity_id=viewer, page_size=1, page=1)
            assert first.status == second.status == "known_catalog_stale"
            assert first.matched_count == second.matched_count == 2
            assert first.has_more and not second.has_more
            assert {first.results[0].record_id, second.results[0].record_id} == {"isbn-a", "isbn-b"}
            subject = page("subject", "astronomie", identity_id=viewer)
            assert subject.matched_count == 1 and not subject.has_more
            assert subject.results[0].record_id == "subject-a"
            # The actual GTK remote DTO projection and QueryState filter
            # must retain the same results, rather than lose them on render.
            from cifereca.gui.query_model import QueryState, result_row
            isbn_rows = [result_row(row) for row in (*first.results, *second.results)]
            for query in ("9780306406157", "0306406152", "0-306-40615-2"):
                assert len(QueryState(text=query, field="isbn").filter(isbn_rows)) == 2
            assert not QueryState(text="9780306406158", field="isbn").filter(isbn_rows)
            subject_rows = [result_row(row) for row in subject.results]
            assert len(QueryState(text="astronomie", field="subject").filter(subject_rows)) == 1
            assert not QueryState(text="cuisine", field="subject").filter(subject_rows)
            for result in (*first.results, *second.results, *subject.results):
                assert result.metadata["notice_c_query_status"] == "partial"
                assert result.metadata["search_provenance"] == "known_catalog_stale"
                assert not result.owned and not result.downloadable
                assert result.thumbnail_url is result.friendly_url is result.download_url is None
        assert page("isbn", "9780306406158").status == "no_known_results"
        assert page("isbn", "9780306406157", provider_id="foreign").results == ()
        assert page("isbn", "9780306406157", page_size=1, page=2).status == "page_out_of_range"
        assert db.entitlements() == [] and db.acquisitions() == []
