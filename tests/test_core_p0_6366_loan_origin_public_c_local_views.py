"""#6366: delivered loan OriginLink -> public C -> Library/Recent list/detail.

Only a verified completed provider acquisition of the exact owner Placement
can attach a public catalogue C. A remote loan/identical hash/title is not
an entitlement and never creates a local or public edition relation.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, persist_consolidated_cover_source,
)
from cifereca.core.notice_c_local_origin_projection import (
    project_local_origin_public_c, valid_local_public_c_link,
)
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.gui.notice_cover_cached_reader import cached_notice_cover_snapshot
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register, target,
)
from tests.test_core_p0_6366_library_recent_public_c import (
    _views, library_view, recent_view, _render_library_detail,
    _render_recent_detail,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    verified_public, persist,
)

COVER_A = "https://covers.example.org/loan-source-a.jpg"
COVER_B = "https://covers.example.org/loan-source-b.jpg"


def loan_origin(db, tmp_path, provider, edition, credential, *,
                identity="A", path=None):
    source, rights = new_source(
        identity, provider=provider, credential=credential,
        edition=edition, right="loan:" + edition,
        kind="loan", branch="loans",
    )
    register(db, identity, provider, credential)
    path = path or target(tmp_path, identity)
    result = publish(db, tmp_path / "EBooks", path, source, rights)
    return result, path


def public_source(db, provider, record, url):
    source = persist(db, verified_public(
        source_provider=provider, record_id=record,
        title="Le livre emprunté", thumbnail_url=url,
    ))
    persist_exact_public_search_cover(db, source)
    return source


def test_delivered_loan_uses_same_public_c_in_library_and_recent(tmp_path):
    filename = tmp_path / "loan-local-c.sqlite"
    db = Storage(filename)
    result, path = loan_origin(
        db, tmp_path, "seller-loan", "edition-loan", "card-loan",
    )
    source = public_source(db, "seller-loan", "edition-loan", COVER_A)
    canonical = persist_exact_public_search_cover(db, source)
    try:
        lib, recent = _views(
            db, result.artifact_id, result.placement_id, path,
        )
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        proof = lib["_public_canonical_cover_link"]
        assert proof == recent["_public_canonical_cover_link"]
        assert proof.document == canonical.document
        assert valid_local_public_c_link(
            proof, identity_id="A", artifact_id=result.artifact_id,
            placement_id=result.placement_id,
        )
        assert library_view(lib)._cover_document_ref == proof.document
        assert recent_view(recent)._cover_document_ref == proof.document
        _render_library_detail(lib, proof)
        _render_recent_detail(recent, proof)
        n2 = cached_notice_cover_snapshot(NS(storage=db), proof.document)
        assert n2 is not None
        assert [candidate.url for candidate in n2.candidates] == [COVER_A]
        assert db.entitlements() == [] and db.acquisitions() == []
        with db._lock, db._connect() as sql:
            sql.execute(
                "DELETE FROM metadata WHERE key=?", (_opaque_key(source.document),),
            )
    finally:
        db.close()
    reopened = Storage(filename)
    try:
        lib, recent = _views(
            reopened, result.artifact_id, result.placement_id, path,
        )
        project_local_origin_public_c(reopened, [lib, recent], identity_id="A")
        assert lib["_public_canonical_cover_link"].document == canonical.document
        assert recent["_public_canonical_cover_link"].document == canonical.document
    finally:
        reopened.close()


def test_two_completed_provider_loan_origins_share_verified_multi_source_c(tmp_path):
    db = Storage(tmp_path / "loan-multisource.sqlite")
    try:
        a, path = loan_origin(
            db, tmp_path, "seller-a", "edition-a", "card-a",
        )
        b, _ = loan_origin(
            db, tmp_path, "seller-b", "edition-b", "card-b", path=path,
        )
        first = public_source(db, "seller-a", "edition-a", COVER_A)
        second = public_source(db, "seller-b", "edition-b", COVER_B)
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-loan-source-pair",
            source=first, relation_proof_id="strong:fixture:a",
        )
        joint = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-loan-source-pair",
            source=second, relation_proof_id="strong:fixture:b",
        )
        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        proof = lib["_public_canonical_cover_link"]
        assert a.artifact_id == b.artifact_id
        assert proof.document == joint.document
        assert proof == recent["_public_canonical_cover_link"]
        assert set(candidate.url for candidate in
                   cached_notice_cover_snapshot(NS(storage=db), proof.document).candidates) == {
                       COVER_A, COVER_B,
                   }
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_cross_profile_same_sha_cannot_claim_other_loan_origin(tmp_path):
    db = Storage(tmp_path / "loan-other-profile.sqlite")
    try:
        a, path_a = loan_origin(
            db, tmp_path, "seller-a", "edition-a", "card-a",
        )
        loan_origin(
            db, tmp_path, "seller-b", "edition-b", "card-b",
            identity="B",
        )
        first = public_source(db, "seller-a", "edition-a", COVER_A)
        second = public_source(db, "seller-b", "edition-b", COVER_B)
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:untrusted-mixed-owners",
            source=first, relation_proof_id="strong:fixture:a",
        )
        joint = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:untrusted-mixed-owners",
            source=second, relation_proof_id="strong:fixture:b",
        )
        local, recent = _views(db, a.artifact_id, a.placement_id, path_a)
        project_local_origin_public_c(db, [local, recent], identity_id="A")
        # The single-source self-C is still eligible; the mixed-owner
        # two-source relation is not, even though the physical SHA matches.
        assert local["_public_canonical_cover_link"].document != joint.document
        assert recent["_public_canonical_cover_link"].document != joint.document
        assert db.entitlements() == []
    finally:
        db.close()


def test_no_materialized_placement_or_higher_local_epub_never_borrows_c(tmp_path):
    db = Storage(tmp_path / "loan-remote.sqlite")
    try:
        public_source(db, "seller-loan", "edition-loan", COVER_A)
        unowned = {
            "identity_id": "A", "artifact_id": "sha256:" + "a" * 64,
            "placement_id": "not-present", "local": True,
            "title": "Le livre emprunté",
        }
        project_local_origin_public_c(db, [unowned], identity_id="A")
        assert "_public_canonical_cover_link" not in unowned

        result, path = loan_origin(
            db, tmp_path, "seller-loan", "edition-loan", "card-loan",
        )
        library, recent = _views(
            db, result.artifact_id, result.placement_id, path,
        )
        library["_exact_local_cover_evidence"] = object()
        recent["_exact_local_cover_evidence"] = object()
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == []
    finally:
        db.close()
