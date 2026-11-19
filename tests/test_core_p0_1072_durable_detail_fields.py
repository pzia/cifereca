"""Same C row contains independent detail facts without replacing N2 evidence."""
from dataclasses import replace

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover, read_consolidated_notice_cover
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_cover_repository import persist_notice_cover, read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, request, set_policy
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation
from tests.test_core_p0_1072_purchase_field_comparison import modified
from tests.test_core_p0_6366_real_search_notice_c_ingestion import URL


def test_same_c_retains_new_and_conflicting_fields_across_restart_and_recompute(tmp_path):
    path = tmp_path / "detail.sqlite"
    db = Storage(path)
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        prior = modified(observation(candidate, at=99, cover=URL), author="Ancien", description=None)
        detail = modified(observation(candidate, at=101), author="Nouveau", description="Résumé nouveau")
        old = persist_notice_cover(db, prior, field_path=("search_result", "thumbnail_url"),
                                   cover_inventory_complete=True)
        initial = persist_exact_public_search_cover(db, old)
        ref, = request(db, snapshot, candidate)
        spec = journal.read_job(db, ref).spec

        def apply(incoming):
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                return publish_purchase_public_txn(sql, spec, snapshot, candidate, incoming, now=104)

        assert apply(detail).source == old
        current = read_consolidated_notice_cover(db, initial.document)
        assert current.epoch == initial.epoch + 1
        member, = current.sources
        assert member.source_revision == prior.source_revision
        assert member.public_bibliography.author == "Ancien"
        assert current.candidates == initial.candidates
        facts = {fact.field: fact for fact in member.public_detail_facts}
        assert facts["author"].value == "Nouveau"
        assert facts["description"].value == "Résumé nouveau"
        assert all(fact.observed_at == 101 and fact.source_revision == detail.source_revision
                   for fact in facts.values())
        assert apply(detail).source == old
        assert persist_exact_public_search_cover(db, old) == current
        newer = modified(observation(candidate, at=103), author="Dernier", description=None)
        apply(newer)
        latest = read_consolidated_notice_cover(db, initial.document)
        fields = {fact.field: fact for fact in latest.sources[0].public_detail_facts}
        assert fields["author"].value == "Dernier" and fields["author"].observed_at == 103
        assert fields["description"] == facts["description"]
        assert read_notice_cover(db, old.document) == old
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()
    db = Storage(path)
    try:
        assert read_consolidated_notice_cover(db, initial.document) == latest
        assert persist_exact_public_search_cover(db, old) == latest
    finally:
        db.close()
