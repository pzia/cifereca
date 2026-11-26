"""#1023: route an explicit ISBN query through the existing C-first task."""
from contextlib import closing

from cifereca.core import notice_c_public_search as cursor
from cifereca.storage import Storage
from tests.test_core_p0_1023_retained_isbn_observation import _row
from tests.test_core_p0_6366_c_first_known_search import _known
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def test_known_isbn_delegates_exact_query_scope_and_providers_to_c(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "isbn-route.sqlite")) as db:
        source = persist(db, _row("record-a", ["9780306406157"]))
        page = cursor.search_public_notice_c(db, "même titre", group_by_document=True, now=100)
        calls = []

        def controlled_cursor(storage, query, **kwargs):
            assert storage is db
            calls.append((query, kwargs))
            return page

        monkeypatch.setattr(cursor, "search_public_notice_c", controlled_cursor)
        known = _known(db, "0-306-40615-2", field="isbn", provider_ids=("bnf",))
        assert len(known) == 1 and known[0]["_cover_document_ref"] == source.document
        assert known[0]["source"] == "notice_c_public"
        assert known[0]["identity_id"] == "alice"
        assert calls == [("0-306-40615-2", {
            "field": "isbn", "scope": "all", "provider_ids": ("bnf",),
            "group_by_document": True, "content_types": (),
            "age": None, "age_kind": None,
        })]
        assert db.entitlements() == [] and db.acquisitions() == []
