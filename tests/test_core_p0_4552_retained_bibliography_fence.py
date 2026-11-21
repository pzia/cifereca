"""#4552: retained bibliography cannot replay a superseded source revision."""
from contextlib import closing

import pytest

from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import (
    _fence_key, _opaque_key, evict_notice_cover,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("eviction", ["qualified", "legacy", "corrupt"])
def test_superseded_retained_source_does_not_reappear_after_eviction(tmp_path, grouped, eviction):
    path = tmp_path / "retained-fence.sqlite"
    query = dict(now=102, group_by_document=grouped)
    with closing(Storage(path)) as db:
        old = persist(db, verified_public(record_id="old", title="Loups anciens"), at=100)
        canonical = persist_exact_public_search_cover(db, old)
        good = persist(db, verified_public(record_id="good", title="Loups attestés"), at=100)
        independent = persist_exact_public_search_cover(db, good)
        assert evict_notice_cover(db, good.document, expected_epoch=good.epoch)
        assert len(search_public_notice_c(db, "loups", **query).hits) == 2
        current = persist(db, verified_public(record_id="old", title="Titre corrigé"), at=101)
        assert current.document == old.document and current.epoch > old.epoch
        if eviction == "legacy":
            with db._lock, db._connect() as sql:
                sql.execute("DELETE FROM metadata WHERE key=?", (_opaque_key(current.document),))
        else:
            assert evict_notice_cover(db, current.document, expected_epoch=current.epoch)
            if eviction == "corrupt":
                with db._lock, db._connect() as sql:
                    sql.execute("UPDATE metadata SET value=? WHERE key=?",
                                ("{}", _fence_key(_opaque_key(current.document))))
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
        page = search_public_notice_c(db, "loups", **query)
        assert page.known_matches == 1 and page.unindexed_sources >= 1
        assert tuple(h.document for h in page.hits) == (independent.document,)
    with closing(Storage(path)) as db:
        assert search_public_notice_c(db, "loups", **query) == page
        assert db.entitlements() == [] and db.acquisitions() == []


def test_same_revision_qualified_eviction_preserves_retained_bibliography(tmp_path):
    with closing(Storage(tmp_path / "same.sqlite")) as db:
        row = verified_public(title="Loups attestés")
        source = persist(db, row, at=100)
        canonical = persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        hit, = search_public_notice_c(db, "loups", now=101).hits
        assert hit.document == canonical.document
        assert hit.title == row.title
