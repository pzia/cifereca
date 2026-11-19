"""Real source decoder and publication port, no synthetic canonical proof."""
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.notice_c_purchase_field_delta import compare_exact_purchase_fields
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import persist_notice_cover, read_notice_cover
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_source_observation import admit_source_notice
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, request, set_policy
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation
from tests.test_core_p0_6366_real_search_notice_c_ingestion import URL


def modified(source, **changes):
    raw = source.fields_copy()
    raw["search_result"]["public_bibliography"].update(changes)
    return admit_source_notice(key=source.key, parser_id=source.parser_id,
                               parser_version=source.parser_version, status=source.status,
                               observed_at=source.observed_at, raw_fields=raw)


def test_comparison_keeps_original_per_field_evidence_and_no_conflict_winner(tmp_path):
    db = Storage(tmp_path / "field.sqlite")
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        prior = modified(observation(candidate, at=99, cover=URL),
                         author="Auteur retenu", publisher="Éditeur historique",
                         description=None, classification={"genres": ["Roman"]})
        incoming = modified(observation(candidate, at=101),
                            author="Autre auteur", publisher=None,
                            classification={"genres": ["Policier"], "keywords": ["mer"]})
        old = persist_notice_cover(db, prior, field_path=("search_result", "thumbnail_url"),
                                   cover_inventory_complete=True)
        canonical = persist_exact_public_search_cover(db, old)
        ref, = request(db, snapshot, candidate)
        spec = journal.read_job(db, ref).spec
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            result = publish_purchase_public_txn(sql, spec, snapshot, candidate, incoming, now=102)
        assert not result.created and result.source == old and result.document == canonical.document
        plan = {item.field: item for item in result.reconciliation}
        assert plan["title"].state == "agreement"
        assert plan["author"].state == "conflict"
        assert plan["author"].retained.value == "Auteur retenu"
        assert plan["author"].detail.value == "Autre auteur"
        assert plan["publisher"].state == "retained_only"
        assert plan["description"].state == "addition"
        assert plan["genres"].state == "conflict" and plan["keywords"].state == "addition"
        for fact in plan.values():
            if fact.retained is not None:
                assert fact.retained.observed_at == 99 and fact.retained.source_revision == prior.source_revision
            if fact.detail is not None:
                assert fact.detail.observed_at == 101 and fact.detail.source_revision == incoming.source_revision
        assert read_notice_cover(db, notice_cover_document_ref(prior.key)) == old
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        with pytest.raises(ValueError, match="scope"):
            compare_exact_purchase_fields(prior, replace(incoming, key=replace(
                incoming.key, edition_id="different")), candidate)
        with pytest.raises(ValueError):
            compare_exact_purchase_fields(prior, replace(incoming, source_revision=prior.source_revision), candidate)
    finally:
        db.close()
