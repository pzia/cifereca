"""#6397: versioned source extension survives canonical retention.

The fixture explicitly supplies a trusted parsed observation; automatic
Search admission and click authorization are deliberately not under test.
"""
from contextlib import closing
import json

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_source_observation import admit_source_notice
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_cover_repository import persist_notice_cover, evict_notice_cover
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, read_consolidated_notice_cover, _db_key,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import verified_public


def source(db, provider, record, links, *, now=100):
    row = verified_public(source_provider=provider, record_id=record)
    initial = admit_public_search_catalogue(
        row, identity_id="alice", observed_at=now, public_cover_url=row.thumbnail_url,
    )
    raw = initial.fields_copy()
    if links is not None:
        raw["search_result"]["public_web_references"] = {"schema": 1, "links": links}
    obs = admit_source_notice(
        key=initial.key, parser_id=initial.parser_id, parser_version=initial.parser_version,
        observed_at=now, raw_fields=raw, status=initial.status,
    )
    return persist_notice_cover(
        db, obs, field_path=("search_result", "thumbnail_url"), cover_inventory_complete=True,
    )


def link(provider, url):
    return {"url": url, "evidence": f"{provider}.public_search_product_card"}


def publish(db, target, item):
    return persist_consolidated_cover_source(
        db, canonical_edition_id=target, source=item, relation_proof_id="fixture:exact",
    )


def test_public_links_keep_provenance_after_eviction_and_restart(tmp_path):
    filename = tmp_path / "web.sqlite"
    with closing(Storage(filename)) as db:
        a = source(db, "seller-a", "a", [
            link("seller-a", "https://books.example.org/title/2"),
            link("seller-a", "https://books.example.org/title/1"),
        ])
        b = source(db, "seller-b", "b", [
            link("seller-b", "https://books.example.org/title/1"),
        ])
        c = source(db, "seller-c", "c", None)
        target = a.observation.key.edition_id
        publish(db, target, a)
        publish(db, target, b)
        joint = publish(db, target, c)
        refs = joint.catalogue_web_references
        assert len(refs) == 3
        assert {(ref.provider_id, ref.record_id) for ref in refs} == {
            ("seller-a", "a"), ("seller-b", "b"),
        }
        assert len({ref.url for ref in refs}) == 2
        assert all(ref.access_mode == "public" and ref.observed_at == 100 for ref in refs)
        by_ref = {item.document.source_key: item for item in (a, b, c)}
        assert all(ref.source_revision == by_ref[ref.source_ref].observation.source_revision
                   and ref.edition_id == by_ref[ref.source_ref].observation.key.edition_id
                   for ref in refs)
        fixed = source(db, "seller-a", "a", [
            link("seller-a", "https://books.example.org/title/corrected"),
        ], now=101)
        updated = publish(db, target, fixed)
        assert len(updated.catalogue_web_references) == 2
        assert {ref.url for ref in updated.catalogue_web_references} == {
            "https://books.example.org/title/corrected", "https://books.example.org/title/1",
        }
        for item in (fixed, b, c):
            assert evict_notice_cover(db, item.document, expected_epoch=item.epoch)
        assert db.entitlements() == [] and db.acquisitions() == []
    with closing(Storage(filename)) as db:
        assert read_consolidated_notice_cover(db, updated.document) == updated
        assert db.entitlements() == [] and db.acquisitions() == []


@pytest.mark.parametrize("bad", [
    link("other", "https://books.example.org/title/1"),
    link("seller-a", "https://books.example.org/search"),
    link("seller-a", "https://books.example.org/book.epub"),
    link("seller-a", "http://books.example.org/title/1"),
    link("seller-a", "https://127.0.0.1/title/1"),
])
def test_bad_extension_never_reaches_canonical_row(tmp_path, bad):
    with closing(Storage(tmp_path / "bad.sqlite")) as db:
        a = source(db, "seller-a", "a", None)
        good = publish(db, a.observation.key.edition_id, a)
        altered = source(db, "seller-a", "a", [bad], now=101)
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            publish(db, a.observation.key.edition_id, altered)
        assert read_consolidated_notice_cover(db, good.document) == good


def test_canonical_decoder_rechecks_wire_evidence(tmp_path):
    with closing(Storage(tmp_path / "tamper.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", "https://books.example.org/title/1")])
        c = publish(db, a.observation.key.edition_id, a)
        with db._lock, db._connect() as sql:
            key = _db_key(c.document)
            raw = json.loads(sql.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()[0])
            item = next(iter(raw["sources"].values()))
            item["public_web_references"]["links"][0]["url"] += "?token=secret"
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            read_consolidated_notice_cover(db, c.document)
