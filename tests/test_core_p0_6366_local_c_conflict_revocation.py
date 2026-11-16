"""#6366: fail-closed Library/Recent C conflicts and replayed N2 bindings."""
from __future__ import annotations

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
