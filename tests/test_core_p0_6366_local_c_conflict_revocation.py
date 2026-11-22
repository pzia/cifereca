"""#6366: fail-closed Library/Recent C conflicts and replayed N2 bindings."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_library_recent_public_c import (
    _real_origins, _source, _views,
)


def _ready(db, tmp_path):
    result, _, path = _real_origins(db, tmp_path)
    first = _source(db, "seller-a", "edition-a", "https://covers.example.org/a.jpg")
    second = _source(db, "seller-b", "edition-b", "https://covers.example.org/b.jpg")
    persist_exact_public_search_cover(db, first)
    persist_exact_public_search_cover(db, second)
    for source in (first, second):
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:baseline-relation",
            source=source, relation_proof_id="strong:fixture:baseline",
        )
    return result, path, first, second


def test_two_conflicting_multisource_relations_fail_closed(tmp_path):
    db = Storage(tmp_path / "conflict.sqlite")
    try:
        result, path, first, second = _ready(db, tmp_path)
        for canonical in ("edition:claim-one", "edition:claim-two"):
            for source in (first, second):
                persist_consolidated_cover_source(
                    db, canonical_edition_id=canonical, source=source,
                    relation_proof_id="strong:fixture:" + canonical,
                )
        library, recent = _views(db, result.artifact_id, result.placement_id, path)
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_stale_link_is_revoked_after_owner_rotation_and_placement_change(tmp_path):
    db = Storage(tmp_path / "stale.sqlite")
    try:
        result, path, _, _ = _ready(db, tmp_path)
        library, recent = _views(db, result.artifact_id, result.placement_id, path)
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" in library
        project_local_origin_public_c(db, [library, recent], identity_id="B")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" not in recent
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" in library
        library["placement_id"] = "placement:removed"
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" in recent
    finally:
        db.close()


def test_epub_n1_revokes_earlier_public_c_n2(tmp_path):
    db = Storage(tmp_path / "priority.sqlite")
    try:
        result, path, _, _ = _ready(db, tmp_path)
        library, recent = _views(db, result.artifact_id, result.placement_id, path)
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" in library
        library["_exact_local_cover_evidence"] = object()
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" in recent
    finally:
        db.close()


@pytest.mark.parametrize("documentary_only", [False, True])
def test_origin_placement_and_c_are_read_under_one_sqlite_snapshot(tmp_path, monkeypatch,
                                                            documentary_only):
    db = Storage(tmp_path / "snapshot.sqlite")
    try:
        result, path, _, _ = _ready(db, tmp_path)
        library, recent = _views(db, result.artifact_id, result.placement_id, path)
        original = db._connect
        reads = []

        @contextmanager
        def traced():
            statements = []
            reads.append(statements)
            with original() as sql:
                sql.set_trace_callback(statements.append)
                try:
                    yield sql
                finally:
                    sql.set_trace_callback(None)

        monkeypatch.setattr(db, "_connect", traced)
        project_local_origin_public_c(db, [library, recent], identity_id="A",
                                      documentary_only=documentary_only)
        key = ("_public_canonical_document_link" if documentary_only
               else "_public_canonical_cover_link")
        assert library[key] == recent[key]
        assert len(reads) == 1
        statements = reads[0]
        begin = next(i for i, s in enumerate(statements) if s == "BEGIN")
        origins = next(i for i, s in enumerate(statements) if "JOIN origin_links" in s)
        placements = next(i for i, s in enumerate(statements)
                          if "SELECT artifact_id,placement_id FROM placements" in s)
        canonical = next(i for i, s in enumerate(statements)
                         if "SELECT m.key,m.value FROM metadata AS m" in s)
        assert begin < origins < placements < canonical

        # A subsequent reader must not retain a receipt from the old snapshot.
        with original() as sql:
            sql.execute("DELETE FROM origin_links WHERE identity_id=?", ("A",))
        reads.clear()
        project_local_origin_public_c(db, [library, recent], identity_id="A",
                                      documentary_only=documentary_only)
        assert key not in library and key not in recent
        assert len(reads) == 1
    finally:
        db.close()


@pytest.mark.parametrize("documentary_only", [False, True])
def test_corrupt_competing_c_claim_revokes_old_local_receipt(tmp_path, documentary_only):
    import json
    from cifereca.core.notice_c_cover_index import _db_key
    db = Storage(tmp_path / "corrupt-competing.sqlite")
    try:
        result, path, first, second = _ready(db, tmp_path)
        library, recent = _views(db, result.artifact_id, result.placement_id, path)
        key = ("_public_canonical_document_link" if documentary_only
               else "_public_canonical_cover_link")
        project_local_origin_public_c(db, [library, recent], identity_id="A",
                                      documentary_only=documentary_only)
        assert key in library and key in recent
        for source in (first, second):
            conflicting = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:corrupt-claim", source=source,
                relation_proof_id="strong:fixture:competing",
            )
        with db._lock, db._connect() as sql:
            raw, = sql.execute("SELECT value FROM metadata WHERE key=?",
                               (_db_key(conflicting.document),)).fetchone()
            value = json.loads(raw)
            value["epoch"] = False
            sql.execute("UPDATE metadata SET value=? WHERE key=?",
                        (json.dumps(value), _db_key(conflicting.document)))
        project_local_origin_public_c(db, [library, recent], identity_id="A",
                                      documentary_only=documentary_only)
        assert key not in library and key not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
