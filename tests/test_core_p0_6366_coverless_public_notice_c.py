"""#6366: real same-call public bibliography must persist even with 0 covers."""
from __future__ import annotations

from dataclasses import replace

from cifereca.core.notice_c_cover_index import (
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import (
    merge_public_notice_c_known_rows,
)
from cifereca.core.notice_cover_repository import read_notice_cover, _opaque_key
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_6366_same_call_public_cover_writer import (
    witnessed, mirror,
)
from tests.test_core_p0_6366_real_search_notice_c_ingestion import (
    public_row, URL,
)
from types import SimpleNamespace as NS


def test_coverless_public_supplier_result_persists_searchable_c_with_unknown_cover(
    tmp_path,
):
    db = Storage(tmp_path / "coverless.sqlite")
    try:
        row = witnessed(public_row(
            record_id="no-image", thumbnail_url=None,
            title="Le livre sans image",
        ))
        assert row.thumbnail_url is None
        assert mirror(db, [row]) == 1
        exact = row._core_notice_cover_document_ref
        source = read_notice_cover(db, exact)
        assert source.observation.parser_version == "2"
        assert source.observation.status == "partial"
        assert source.attribute.state == "pending"
        assert source.attribute.candidates == ()
        canonical = row._core_notice_canonical_cover_document_ref
        saved = read_consolidated_notice_cover(db, canonical)
        assert len(saved.sources) == 1
        assert saved.sources[0].public_bibliography.title == "Le livre sans image"
        assert saved.sources[0].state == "pending"
        assert saved.candidates == () and saved.snapshot_complete is False
        page = search_public_notice_c(db, "sans image", now=100)
        assert len(page.hits) == 1 and page.hits[0].document == canonical
        assert page.hits[0].cover_state == "pending"
        known = merge_public_notice_c_known_rows([], page, identity_id="bob")
        assert known[0]["_cover_document_ref"] == canonical
        assert known[0]["thumbnail_url"] is None
        assert known[0]["state"] == "remote_catalog"
        assert cached_notice_cover_snapshot(
            NS(storage=db), canonical,
        ).snapshot_complete is False
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as conn:
            conn.execute("DELETE FROM metadata WHERE key=?", (
                _opaque_key(exact),
            ))
        assert read_notice_cover(db, exact) is None
        assert len(search_public_notice_c(db, "sans image", now=100).hits) == 1
        assert read_consolidated_notice_cover(db, canonical).candidates == ()
    finally:
        db.close()


def test_coverless_c_upgrades_only_on_same_exact_source_image(tmp_path, monkeypatch):
    from cifereca.core import search_orchestration
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "upgrade.sqlite")
    try:
        first = witnessed(public_row(record_id="later", thumbnail_url=None))
        assert mirror(db, [first]) == 1
        canonical = first._core_notice_canonical_cover_document_ref
        assert read_consolidated_notice_cover(db, canonical).epoch == 1
        clock[0] = 201
        later = witnessed(public_row(record_id="later", thumbnail_url=URL))
        assert mirror(db, [later]) == 1
        assert later._core_notice_canonical_cover_document_ref == canonical
        saved = read_consolidated_notice_cover(db, canonical)
        assert saved.epoch == 2
        assert [c.url for c in saved.candidates] == [URL]
        assert saved.sources[0].state == "available"
        assert search_public_notice_c(db, "Même titre").hits[0].document == canonical
        assert db.entitlements() == []
    finally:
        db.close()


def test_coverless_forged_or_private_row_cannot_publish_c(tmp_path):
    db = Storage(tmp_path / "rejected.sqlite")
    try:
        raw = public_row(thumbnail_url=None)
        assert mirror(db, [raw]) == 1
        assert not hasattr(raw, "_core_notice_canonical_cover_document_ref")
        cloned = replace(witnessed(raw))
        assert mirror(db, [cloned]) == 1
        assert not hasattr(cloned, "_core_notice_canonical_cover_document_ref")
        private = witnessed(raw)
        private.source_account_id = "account"
        assert mirror(db, [private]) == 1
        assert not hasattr(private, "_core_notice_canonical_cover_document_ref")
        assert search_public_notice_c(db, "Même titre").hits == ()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
