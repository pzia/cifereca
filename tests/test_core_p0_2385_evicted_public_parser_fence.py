"""Public Search parser authority survives exact source eviction."""
from contextlib import closing
from dataclasses import replace
import json

import pytest

from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import (
    _fence_key, _opaque_key, evict_notice_cover, read_notice_cover,
    read_notice_cover_write_epoch, persist_notice_cover, persist_notice_cover_many,
)
from cifereca.core.notice_search_cover_producer import admit_public_search_cover, admit_public_search_catalogue
from cifereca.core.search_orchestration import (
    _CORE_PUBLIC_COVER_WITNESS, _public_cover_witness_coordinates,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public

FIELD = ("search_result", "thumbnail_url")


def fence(db, doc):
    with db._lock, db._connect() as sql:
        raw, = sql.execute("SELECT value FROM metadata WHERE key=?",
                           (_fence_key(_opaque_key(doc)),)).fetchone()
    return json.loads(raw)


def legacy(row, at):
    cached = replace(row)
    item = admit_public_search_cover(
        cached, identity_id="bob", observed_at=at, public_cover_url=cached.thumbnail_url,
    )
    assert item is not None and item.parser_version == "1"
    return item


@pytest.mark.parametrize("legacy_fence", [False, True])
def test_evicted_public_v2_refuses_v1_and_partial_without_losing_c(tmp_path, legacy_fence):
    path = tmp_path / "evicted.sqlite"
    row = verified_public(title="La forêt des loups")
    with closing(Storage(path)) as db:
        rich = persist(db, row, at=100)
        assert rich.observation.parser_version == "2"
        canonical = persist_exact_public_search_cover(db, rich)
        assert evict_notice_cover(db, rich.document, expected_epoch=rich.epoch)
        token = read_notice_cover_write_epoch(db, rich.document)
        assert token == rich.epoch + 1
        marker = fence(db, rich.document)
        assert marker["public_search_v2"] is True
        if legacy_fence:
            marker.pop("public_search_v2")
            with db._lock, db._connect() as sql:
                sql.execute("UPDATE metadata SET value=? WHERE key=?",
                            (json.dumps(marker), _fence_key(_opaque_key(rich.document))))
    with closing(Storage(path)) as db:
        plain = legacy(row, 101)
        assert plain.key == rich.observation.key
        assert persist_notice_cover(db, plain, field_path=FIELD,
                                    cover_inventory_complete=True, expected_epoch=token) is None
        assert fence(db, rich.document) == marker
        partial_row = verified_public(title=row.title, thumbnail_url=None)
        # Public no-image admission needs both independent same-call witnesses.
        assert admit_public_search_catalogue(
            partial_row, identity_id="alice", observed_at=102, public_cover_url=None,
        ) is None
        partial_row._core_same_call_public_cover_witness = (
            _CORE_PUBLIC_COVER_WITNESS, _public_cover_witness_coordinates(partial_row),
        )
        partial = admit_public_search_catalogue(
            partial_row, identity_id="alice", observed_at=102, public_cover_url=None,
        )
        assert partial is not None and partial.status == "partial"
        assert persist_notice_cover_many(
            db, [partial, legacy(row, 103)], field_path=FIELD, cover_inventory_complete=True,
        ) == [None, None]
        assert read_notice_cover(db, rich.document) is None
        assert read_notice_cover_write_epoch(db, rich.document) == token
        assert fence(db, rich.document)["last_seen_at"] == 102
        assert read_consolidated_notice_cover(db, canonical.document) == canonical
    with closing(Storage(path)) as db:
        hit, = search_public_notice_c(db, "loups", now=103).hits
        assert hit.document == canonical.document and hit.title == row.title
        refreshed = persist(db, row, at=104)
        assert refreshed.epoch == token + 1
        assert refreshed.observation.parser_version == "2"
        assert db.entitlements() == [] and db.acquisitions() == []


def test_v2_marker_without_c_and_v1_only_source_remain_distinct(tmp_path):
    with closing(Storage(tmp_path / "parser-only.sqlite")) as db:
        rich_row = verified_public(record_id="rich")
        rich = persist(db, rich_row)
        assert evict_notice_cover(db, rich.document, expected_epoch=rich.epoch)
        assert persist_notice_cover(db, legacy(rich_row, 101), field_path=FIELD,
                                    cover_inventory_complete=True) is None
        assert read_notice_cover(db, rich.document) is None

        other = verified_public(record_id="legacy")
        first = persist_notice_cover(db, legacy(other, 100), field_path=FIELD,
                                     cover_inventory_complete=True)
        assert evict_notice_cover(db, first.document, expected_epoch=first.epoch)
        other.thumbnail_url = "https://covers.example.org/updated.jpg"
        fresh = persist_notice_cover(db, legacy(other, 101), field_path=FIELD,
                                     cover_inventory_complete=True)
        assert fresh is not None and fresh.epoch == first.epoch + 2
        assert fresh.observation.parser_version == "1"
        assert "public_search_v2" not in fence(db, fresh.document)
