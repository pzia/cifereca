"""Live C routing must not adopt a contradictory retained source snapshot."""
from contextlib import closing
import json

import pytest

from cifereca.core.notice_c_cover_index import (
    _db_key, persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


@pytest.mark.parametrize("field,value", [
    ("publisher", "Autre éditeur"),
    ("media_format", "PDF"),
    ("description", "Autre résumé"),
    ("published_at", "2024-02-03"),
    ("observed_at", 99),
])
def test_contradictory_retained_member_cannot_reroute_live_source(tmp_path, field, value):
    with closing(Storage(tmp_path / "exact.sqlite")) as db:
        live = persist(db, verified_public(
            title="Les loups", publisher="Éditeur témoin",
            description="Résumé témoin", published_at="2025-01-02",
        ), at=100)
        canonical = persist_exact_public_search_cover(db, live)
        assert canonical.document != live.document
        original, = search_public_notice_c(db, "loups", now=100).hits
        assert original.document == canonical.document

        # Structurally valid but inconsistent retained facts. The source
        # coordinates and epoch are deliberately left intact.
        with db._lock, db._connect() as sql:
            key = _db_key(canonical.document)
            payload = json.loads(sql.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()[0])
            member = payload["sources"][live.document.source_key]
            if field == "observed_at":
                member[field] = value
            else:
                member["public_bibliography"][field] = value
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        (json.dumps(payload), key))
        assert read_consolidated_notice_cover(db, canonical.document) is not None
        hit, = search_public_notice_c(db, "loups", now=100).hits
        assert hit.document == live.document
        assert hit.epoch == live.epoch
        assert hit.publisher == original.publisher
        assert hit.media_format == original.media_format
        assert hit.description == original.description
        assert hit.published_at == original.published_at
        assert hit.observed_at == original.observed_at
        assert db.entitlements() == [] and db.acquisitions() == []


def test_seen_again_without_content_change_keeps_exact_c_route(tmp_path):
    with closing(Storage(tmp_path / "replay.sqlite")) as db:
        first = persist(db, verified_public(title="Les loups"), at=100)
        canonical = persist_exact_public_search_cover(db, first)
        persist(db, verified_public(title="Les loups"), at=120)
        hit, = search_public_notice_c(db, "loups", now=120).hits
        assert hit.document == canonical.document
        assert hit.observed_at == 100
        assert hit.last_seen_at == 120


@pytest.mark.parametrize("changes", [
    {"source_revision": "foreign-revision"},
    {"urls": ["https://covers.example.org/foreign.jpg"]},
    {"state": "missing", "urls": []},
    {"state": "pending", "urls": []},
])
@pytest.mark.parametrize("grouped", [False, True])
def test_live_c_route_requires_exact_cover_source_proof(tmp_path, changes, grouped):
    filename = tmp_path / "cover-proof.sqlite"
    with closing(Storage(filename)) as db:
        live = persist(db, verified_public(
            title="Les loups", thumbnail_url="https://covers.example.org/original.jpg",
        ), at=100)
        canonical = persist_exact_public_search_cover(db, live)
        query = dict(now=100, group_by_document=grouped)
        original, = search_public_notice_c(db, "loups", **query).hits
        assert original.document == canonical.document
        with db._lock, db._connect() as sql:
            key = _db_key(canonical.document)
            payload = json.loads(sql.execute(
                "SELECT value FROM metadata WHERE key=?", (key,),
            ).fetchone()[0])
            payload["sources"][live.document.source_key].update(changes)
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        (json.dumps(payload), key))
        assert read_consolidated_notice_cover(db, canonical.document) is not None
        hit, = search_public_notice_c(db, "loups", **query).hits
        assert hit.document == live.document
        assert hit.epoch == live.epoch
        assert hit.cover_state == "available"
        assert hit.title == original.title
    with closing(Storage(filename)) as db:
        assert search_public_notice_c(db, "loups", **query).hits == (hit,)
        assert db.entitlements() == [] and db.acquisitions() == []


@pytest.mark.parametrize("damage", ["malformed_fence", "epoch", "proof", "invalid_row"])
@pytest.mark.parametrize("grouped", [False, True])
def test_invalid_live_source_does_not_reappear_from_retained_c(tmp_path, damage, grouped):
    from cifereca.core.notice_cover_repository import _fence_key, _opaque_key

    filename = tmp_path / "invalid-live.sqlite"
    with closing(Storage(filename)) as db:
        bad = persist(db, verified_public(record_id="bad-source", title="Les loups"), at=100)
        good = persist(db, verified_public(record_id="good-source", title="Les loups"), at=100)
        bad_c = persist_exact_public_search_cover(db, bad)
        good_c = persist_exact_public_search_cover(db, good)
        query = dict(now=100, group_by_document=grouped)
        assert {hit.document for hit in search_public_notice_c(db, "loups", **query).hits} == {
            bad_c.document, good_c.document,
        }
        key = _opaque_key(bad.document)
        with db._lock, db._connect() as sql:
            if damage == "invalid_row":
                sql.execute("UPDATE metadata SET value=? WHERE key=?", ("{", key))
            else:
                key = _fence_key(key)
                fence = json.loads(sql.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,),
                ).fetchone()[0])
                if damage == "malformed_fence":
                    fence = {}
                elif damage == "epoch":
                    fence["epoch"] += 1
                else:
                    fence["proof"] = "0" * 64
                sql.execute("UPDATE metadata SET value=? WHERE key=?",
                            (json.dumps(fence), key))
        # Both retained canonical rows still exist. The invalid present
        # source is not a retention eviction, and must not be replayed.
        assert read_consolidated_notice_cover(db, bad_c.document) == bad_c
        page = search_public_notice_c(db, "loups", **query)
        assert page.known_matches == 1 and page.unindexed_sources >= 1
        hit, = page.hits
        assert hit.document == good_c.document
    with closing(Storage(filename)) as db:
        assert search_public_notice_c(db, "loups", **query).hits == (hit,)
        assert db.entitlements() == [] and db.acquisitions() == []
