"""#6366: owner-local C must reject conflicting exact canonical relationships.

Use real completed OriginLinks, Placements, persisted public source C rows.
An overlap of independent valid coordinates is *not* permission to select
the largest of two incompatible canonical editions.
"""
from __future__ import annotations

from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_local_origin_projection import (
    project_local_origin_public_c, valid_local_public_c_link,
)
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    new_source, publish, register,
)
from tests.test_core_p0_6366_library_recent_public_c import (
    _real_origins, _source, _views, A_URL, B_URL,
)


def test_unequal_competing_c_revoke_existing_library_and_recent_proofs(tmp_path):
    db = Storage(tmp_path / "conflicting-local-c.sqlite")
    try:
        a, b, path = _real_origins(db, tmp_path)
        third, third_rights = new_source(
            "A", provider="seller-c", credential="card-c",
            edition="edition-c", right="right-c",
        )
        register(db, "A", "seller-c", "card-c")
        c = publish(db, tmp_path / "EBooks", path, third, third_rights)
        assert a.artifact_id == b.artifact_id == c.artifact_id

        sources = (
            _source(db, "seller-a", "edition-a", A_URL),
            _source(db, "seller-b", "edition-b", B_URL),
            _source(db, "seller-c", "edition-c",
                    "https://covers.example.org/source-c.jpg"),
        )
        for source in sources:
            three = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:three-proven-sources",
                source=source, relation_proof_id="strong:fixture:three",
            )

        lib, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        previous = lib["_public_canonical_cover_link"]
        assert previous.document == three.document
        assert recent["_public_canonical_cover_link"] == previous

        for source in sources[:2]:
            two = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:two-competing-sources",
                source=source, relation_proof_id="strong:fixture:two",
            )
        assert two.document != three.document

        # The two C records each have legitimate, exact local origins. More
        # sources do not prove that the smaller conflicting C is erroneous.
        project_local_origin_public_c(db, [lib, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in lib
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_reprojecting_foreign_or_stale_rows_revokes_earlier_c_link(tmp_path):
    db = Storage(tmp_path / "revocation-local-c.sqlite")
    try:
        a, _b, path = _real_origins(db, tmp_path)
        source = _source(db, "seller-a", "edition-a", A_URL)
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:one-exact-source",
            source=source, relation_proof_id="strong:fixture:a",
        )
        library, recent = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        original = library["_public_canonical_cover_link"]
        assert valid_local_public_c_link(
            original, identity_id="A",
            artifact_id=a.artifact_id, placement_id=a.placement_id,
        )
        library["identity_id"] = "B"
        recent["placement_id"] = "revoked-placement"
        project_local_origin_public_c(db, [library, recent], identity_id="A")
        assert "_public_canonical_cover_link" not in library
        assert "_public_canonical_cover_link" not in recent
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
