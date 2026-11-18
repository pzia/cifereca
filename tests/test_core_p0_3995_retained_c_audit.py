"""#3995: audit documentary proof after C retention, not a cache image verdict.

Real OriginLink/Placement, public C and source eviction in SQLite; scoped
Library read boundary supplied with the production projected fixture row.
No GTK, N3 materialization, provider network or rights mutation.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, read_consolidated_notice_cover,
)
from cifereca.core.notice_c_local_origin_projection import project_local_origin_public_c
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_library_recent_public_c import (
    _real_origins, _source, _views, A_URL, B_URL,
)
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_retained_documentary_audit_keeps_n1_separate_from_n3(tmp_path):
    filename = tmp_path / "audit-c.sqlite"
    edition = "edition:verified-audit-origins"
    db = Storage(filename)
    try:
        a, _, path = _real_origins(db, tmp_path)
        sources = (
            _source(db, "seller-a", "edition-a", A_URL),
            _source(db, "seller-b", "edition-b", B_URL),
        )
        for index, source in enumerate(sources):
            canonical = persist_consolidated_cover_source(
                db, canonical_edition_id=edition, source=source,
                relation_proof_id=f"strong:fixture:audit-{index}",
            )
        assert canonical.snapshot_complete
        row, _ = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [row], identity_id="A")
        link = row["_public_canonical_cover_link"]
        assert link.document == canonical.document
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
            assert read_notice_cover(db, source.document) is None
    finally:
        db.close()

    db = Storage(filename)
    try:
        retained = read_consolidated_notice_cover(db, link.document)
        assert retained.epoch == link.epoch
        row, _ = _views(db, a.artifact_id, a.placement_id, path)
        project_local_origin_public_c(db, [row], identity_id="A")
        assert row["_public_canonical_cover_link"] == link
        rows = [row]
        service = NS(
            storage=db, identity_by_id=lambda owner: object() if owner == "A" else None,
            _account_context_epoch=1, _source_context_epoch=1,
        )

        def library_entries(*, identity_id, scan):
            assert identity_id == "A" and scan is False
            return rows

        service.library_entries = library_entries
        broker = CatalogBrokerImpl(service)

        def audit():
            return broker.library_documentary_audit(identity_id="A", kind="covers")

        result = audit()
        assert result == {
            "schema": 1, "status": "partial", "entries": 1,
            "embedded": 0, "origin": 0, "catalog": 1,
            "ambiguous": 0, "missing": 0, "unknown": 0,
        }
        # No N2 registered document, image fetch, path or URI in this result.
        assert A_URL not in repr(result) and B_URL not in repr(result)
        assert "n2" not in result and "n3" not in result
        rows[0] = {**row, "_public_canonical_cover_link": replace(link, identity_id="B")}
        assert audit()["unknown"] == 1
        rows[0] = {**row, "_public_canonical_cover_link": replace(link, epoch=link.epoch + 1)}
        assert audit()["unknown"] == 1
        rows[0] = {**row, "_public_canonical_cover_link": replace(link, source_refs=link.source_refs[:1])}
        assert audit()["unknown"] == 1
        rows[0] = dict(row)
        rows[0].pop("_public_canonical_cover_link")
        # Same legacy thumbnail and physical source artifact, no C receipt.
        assert audit()["unknown"] == 1 and audit()["missing"] == 0
        rows[0] = row
        assert audit() == result
        assert broker.library_documentary_audit(identity_id="B", kind="covers")["status"] == "unavailable"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
