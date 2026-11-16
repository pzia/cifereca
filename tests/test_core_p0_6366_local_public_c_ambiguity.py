"""#6366: local-origin public C is only admitted for unambiguous evidence.

Tests exercise the real v14 completed loan OriginLinks, SQLite public C and
Library/Recent row projections. No filename/title/SHA/provider-only join.
"""
from __future__ import annotations

from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
from cifereca.storage import Storage
from tests.test_core_p0_6366_loan_origin_public_c_local_views import (
    loan_origin, public_source, COVER_A, COVER_B,
)
from tests.test_core_p0_6366_library_recent_public_c import _views
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)


def test_unequal_competing_multi_source_c_never_binds_local_artifact(tmp_path):
    db = Storage(tmp_path / "unequal-origin-conflict.sqlite")
    try:
        a, path = loan_origin(
            db, tmp_path, "seller-a", "edition-a", "card-a",
        )
        b, _ = loan_origin(
            db, tmp_path, "seller-b", "edition-b", "card-b", path=path,
        )
        c, _ = loan_origin(
            db, tmp_path, "seller-c", "edition-c", "card-c", path=path,
        )
        assert a.artifact_id == b.artifact_id == c.artifact_id
        sources = (
            public_source(db, "seller-a", "edition-a", COVER_A),
            public_source(db, "seller-b", "edition-b", COVER_B),
            public_source(
                db, "seller-c", "edition-c",
                "https://covers.example.org/third-source.jpg",
            ),
        )
        for source in sources:
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:larger-relationship",
                source=source, relation_proof_id="strong:fixture:larger",
            )
        for source in sources[:2]:
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:smaller-relationship",
                source=source, relation_proof_id="strong:fixture:smaller",
            )
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_one_origin_record_with_two_public_editions_revokes_previous_link(tmp_path):
    db = Storage(tmp_path / "ambiguous-record.sqlite")
    try:
        a, path = loan_origin(
            db, tmp_path, "seller-a", "edition-a", "card-a",
        )
        source = public_source(db, "seller-a", "edition-a", COVER_A)
        assert source is not None
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" in lib
        assert "_public_canonical_cover_link" in recent

        # The completed OriginLink knows only (seller-a, edition-a);
        # it cannot distinguish docbase or source revision in public C.
        distinct = persist(db, verified_public(
            source_provider="seller-a", record_id="edition-a",
            docbase="different-collection", revision_id="revision-v2",
            title="Le même titre", thumbnail_url=COVER_B,
        ), at=101)
        persist_exact_public_search_cover(db, distinct)

        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_refreshed_profile_placement_and_epub_evidence_revoke_old_c(tmp_path):
    db = Storage(tmp_path / "stale-row-proofs.sqlite")
    try:
        a, path = loan_origin(
            db, tmp_path, "seller-a", "edition-a", "card-a",
        )
        public_source(db, "seller-a", "edition-a", COVER_A)
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" in lib

        project_local_origin_public_c(db, [lib, recent], identity_id="B")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent

        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" in lib
        lib["_exact_local_cover_evidence"] = object()
        recent["placement_id"] = "placement:missing"
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent
    finally:
        db.close()


def test_oversize_worker_snapshot_does_not_leave_prior_c_proof():
    stale = {"_public_canonical_cover_link": object()}
    project_local_origin_public_c(
        None, [stale] * 10001, identity_id="A",
    )
    assert "_public_canonical_cover_link" not in stale
