"""#3995: audit documentary proof after C retention, not a cache image verdict.

Real OriginLink/Placement, public C and source eviction in SQLite; scoped
Library read boundary supplied with the production projected fixture row.
No GTK, N3 materialization, provider network or rights mutation.
"""
import pytest

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
        calls = []
        def observe(packet):
            assert packet == link
            calls.append(packet)
            return "selected", "catalog", "signature_valid"
        service._documentary_cover_observer = observe
        runtime = broker.library_cover_audit(identity_id="A")
        assert runtime["catalog"] == runtime["n2"]["catalog"] == 1
        assert runtime["n3"]["signature_valid"] == 1 and calls == [link]
        service._documentary_cover_observer = lambda packet: (
            "selected", "catalog", "digest_valid"
        )
        runtime = broker.library_cover_audit(identity_id="A")
        assert runtime["n2"]["catalog"] == runtime["n3"]["unknown"] == 1
        service._documentary_cover_observer = observe
        rows[0] = {**row, "_public_canonical_cover_link": replace(link, epoch=link.epoch + 1)}
        runtime = broker.library_cover_audit(identity_id="A")
        assert runtime["unknown"] == runtime["n2"]["unknown"] == runtime["n3"]["unknown"] == 1
        assert calls == [link]
        rows[0] = row
        rows[:] = [row] * 130
        runtime = broker.library_cover_audit(identity_id="A")
        assert runtime["entries"] == 130
        assert runtime["n2"]["catalog"] == runtime["n3"]["signature_valid"] == 128
        assert runtime["n2"]["unknown"] == runtime["n3"]["unknown"] == 2
        rows[:] = [row]
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



@pytest.mark.parametrize("raw_a,raw_b,verdict", [
    (("fra", "eng"), ("eng", "fre"), "concordant"),
    (("fra",), ("eng",), "divergent"),
    (("fra",), (), "partial"),
    ((), (), "unknown"),
])
def test_retained_language_audit_recipes(tmp_path, raw_a, raw_b, verdict):
    """Exact source evidence survives source eviction, not a mirror assertion."""
    tags = {"fra": "fr", "fre": "fr", "eng": "en"}
    filename = tmp_path / "language-c.sqlite"
    db = Storage(filename)
    try:
        a, _, path = _real_origins(db, tmp_path)
        saved = []
        for provider, record, url, raw in (
            ("seller-a", "edition-a", A_URL, raw_a),
            ("seller-b", "edition-b", B_URL, raw_b),
        ):
            row = verified_public(source_provider=provider, record_id=record,
                                  title="Un ouvrage commun", thumbnail_url=url)
            if raw:
                row.metadata = {
                    "languages": [tags[tag] for tag in raw],
                    "bibliography_evidence": {"languages": {
                        "raw_tags": list(raw), "source": provider + ".fixture",
                        "source_field": "dc.language", "scope": row.scenario,
                        "confidence": "explicit_record_field", "freshness": "search_response",
                    }},
                }
            else:
                row.metadata = {"languages": ["fr"]}  # not an attested declaration
            saved.append(persist(db, row, at=100))
        for index, source in enumerate(saved):
            canonical = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:verified-language-origins",
                source=source, relation_proof_id=f"strong:fixture:audit-{index}",
            )
        for source in saved:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
            assert read_notice_cover(db, source.document) is None
    finally:
        db.close()

    db = Storage(filename)
    try:
        row, _ = _views(db, a.artifact_id, a.placement_id, path)
        row["metadata"] = {"language": "FR"}
        project_local_origin_public_c(db, [row], identity_id="A")
        link = row["_public_canonical_cover_link"]
        assert link.document == canonical.document
        retained = read_consolidated_notice_cover(db, link.document)
        by_provider = {source.provider_id: source.public_bibliography.language_observation
                       for source in retained.sources}
        for provider, raw in (("seller-a", raw_a), ("seller-b", raw_b)):
            observation = by_provider[provider]
            if raw:
                assert observation.raw_tags == raw
                assert observation.languages == tuple(tags[tag] for tag in raw)
            else:
                assert observation is None
        rows = [row]
        def library_entries(*, identity_id, scan):
            assert identity_id == "A" and scan is False
            return rows
        service = NS(storage=db, identity_by_id=lambda owner: object() if owner == "A" else None,
                     _account_context_epoch=1, _source_context_epoch=1,
                     library_entries=library_entries)
        broker = CatalogBrokerImpl(service)
        def audit():
            return broker.library_language_audit(identity_id="A")
        result = audit()
        assert result["schema"] == 2 and result["status"] == "partial"
        assert result["entries"] == result["noncanonical"] == 1
        assert result["provenance"][verdict] == 1
        assert sum(result["provenance"].values()) == 1
        assert A_URL not in repr(result) and str(path) not in repr(result)
        # A real audit reattests the documentary relation instead of
        # consuming a receipt offered by the visual projection.
        from cifereca.core.library_documentary_audit import _language_provenance
        for wrong in (replace(link, identity_id="B"), replace(link, epoch=link.epoch + 1),
                      replace(link, source_refs=link.source_refs[:1])):
            rows[0] = {**row, "_public_canonical_document_link": wrong}
            assert _language_provenance(db, rows[0], "A") == "unknown"
            assert audit() == result
            assert rows[0]["_public_canonical_document_link"] is wrong
        embedded = object()
        rows[0] = {**row, "_exact_local_cover_evidence": embedded}
        rows[0].pop("_public_canonical_cover_link")
        assert audit() == result
        assert rows[0]["_exact_local_cover_evidence"] is embedded
        assert "_public_canonical_document_link" not in rows[0]
        assert "_public_canonical_cover_link" not in rows[0]
        rows[0]["_library_view_owner"] = "B"
        assert audit()["provenance"]["unknown"] == 1
        rows[0] = row
        assert audit() == result
        assert broker.library_language_audit(identity_id="B")["status"] == "unavailable"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()



def test_single_language_declaration_is_not_multisource_concordance(tmp_path):
    from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover

    filename = tmp_path / "single-language.sqlite"
    db = Storage(filename)
    try:
        a, _, path = _real_origins(db, tmp_path)
        def declared(provider, record, url):
            row = verified_public(source_provider=provider, record_id=record,
                                  title="Même titre", thumbnail_url=url)
            row.metadata = {
                "languages": ["fr"],
                "bibliography_evidence": {"languages": {
                    "raw_tags": ["fre"], "source": provider + ".fixture",
                    "source_field": "dc.language", "scope": row.scenario,
                    "confidence": "explicit_record_field", "freshness": "search_response",
                }},
            }
            return persist(db, row, at=100)
        first = declared("seller-a", "edition-a", A_URL)
        own = persist_exact_public_search_cover(db, first)
        assert own.sources[0].public_bibliography.language_observation.languages == ("fr",)
        assert evict_notice_cover(db, first.document, expected_epoch=first.epoch)
    finally:
        db.close()
    db = Storage(filename)
    try:
        row, _ = _views(db, a.artifact_id, a.placement_id, path)
        service = NS(storage=db, identity_by_id=lambda owner: object() if owner == "A" else None,
                     _account_context_epoch=1, _source_context_epoch=1,
                     library_entries=lambda **kwargs: [row])
        broker = CatalogBrokerImpl(service)
        result = broker.library_language_audit(identity_id="A")
        assert result["entries"] == 1
        assert result["provenance"] == {
            "concordant": 0, "divergent": 0, "partial": 1, "unknown": 0,
        }
        assert "_public_canonical_document_link" not in row
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_retained_metadata_audit_exact_sources_and_no_value_selection(tmp_path):
    import importlib.util
    from pathlib import Path
    from cifereca.plugin_api import MaintenanceContext
    filename = tmp_path / "metadata-c.sqlite"
    db = Storage(filename)
    try:
        a, _, path = _real_origins(db, tmp_path)
        saved = []
        for index, (provider, record, title, author, publisher, url) in enumerate((
            ("seller-a", "edition-a", "Titre témoin A", "Auteur A", "Éditeur", A_URL),
            ("seller-b", "edition-b", "Titre témoin B", None, "Éditeur", B_URL),
        )):
            row = verified_public(source_provider=provider, record_id=record, title=title,
                                  author=author, publisher=publisher, thumbnail_url=url)
            source = persist(db, row, at=100)
            saved.append(source)
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:metadata-audit", source=source,
                relation_proof_id=f"strong:fixture:metadata-{index}",
            )
        for source in saved:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    finally:
        db.close()
    db = Storage(filename)
    try:
        row, _ = _views(db, a.artifact_id, a.placement_id, path)
        service = NS(storage=db, identity_by_id=lambda owner: object() if owner == "A" else None,
                     _account_context_epoch=1, _source_context_epoch=1,
                     library_entries=lambda **kwargs: [row])
        broker = CatalogBrokerImpl(service)
        result = broker.library_documentary_audit(identity_id="A", kind="metadata")
        assert result["entries"] == result["divergent"] == 1
        assert result["fields"]["title"]["divergent"] == 1
        assert result["fields"]["author"]["complement"] == 1
        assert result["fields"]["publisher"]["observed"] == 1
        assert "Titre témoin" not in repr(result)
        assert "_public_canonical_document_link" not in row
        source_file = (Path(__file__).resolve().parents[1] / "src/plugins"
                       / "maintenance-library-audit/cifereca_library_audits.py")
        spec = importlib.util.spec_from_file_location("metadata_audit_fixture", source_file)
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        tool = plugin.MetadataAuditTool().activate(NS(broker=lambda key: broker))
        plan = tool.plan(MaintenanceContext(scope="library", identity_id="A"))
        assert plan.facts == result and "Titre témoin" not in repr(plan)
        control = NS(check_cancelled=lambda: None, report=lambda **kw: None)
        accepted = tool.run(plan, control)
        assert accepted.ok and accepted.changed == 0
        row["_library_view_owner"] = "B"
        assert broker.library_documentary_audit(identity_id="A", kind="metadata")["unknown"] == 1
        assert broker.library_documentary_audit(identity_id="B", kind="metadata")["status"] == "unavailable"
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
