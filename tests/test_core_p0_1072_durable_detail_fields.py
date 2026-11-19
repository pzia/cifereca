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


def test_detail_additions_reach_purchase_view_but_conflicts_are_not_selected(tmp_path):
    from contextlib import nullcontext
    from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
    from cifereca.core.notice_c_purchase_association import record_purchase_association_txn
    from cifereca.core.notice_c_purchase_descriptive_read import read_purchase_descriptive_notice
    from cifereca.core.notice_c_purchase_library_projection import (
        project_purchase_library_notices, purchase_library_notice,
    )
    from cifereca.core.search import filter_loaded_results
    from cifereca.gui.local_book_detail_sections import local_book_detail_sections

    path = tmp_path / "detail-view.sqlite"
    db = Storage(path)
    execution = PurchaseIntentExecution(db, clock=lambda: 104)
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        prior = modified(observation(candidate, at=99, cover=URL), author="Ancien", description=None)
        detail = modified(observation(candidate, at=101), author="Contradictoire",
                          description="Archipel violet")
        old = persist_notice_cover(db, prior, field_path=("search_result", "thumbnail_url"),
                                   cover_inventory_complete=True)
        persist_exact_public_search_cover(db, old)
        ref, = request(db, snapshot, candidate, now=104)
        assert execution.attest(ref, snapshot, candidate, hold_context=lambda: nullcontext(True),
                                execute=lambda *_: None, ttl_seconds=30)
        lease = execution.claim(ref, now=104, lease_seconds=30)
        assert lease is not None
        spec = journal.read_job(db, ref).spec
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            publication = publish_purchase_public_txn(sql, spec, snapshot, candidate, detail, now=104)
            record_purchase_association_txn(sql, lease, snapshot, candidate, publication,
                                            observed_at=101, now=104)
            journal._complete_job_txn(sql, lease, now=104)
    finally:
        execution.close()
        db.close()

    db = Storage(path)
    try:
        source = candidate.source
        result = read_purchase_descriptive_notice(db, source, identity_id=source.scope.identity_id)
        facts = {fact.field: fact for fact in result.facts}
        alternative, = result.alternatives
        assert facts["author"].value == "Ancien" and facts["author"].observed_at == 99
        assert facts["description"].value == "Archipel violet" and facts["description"].observed_at == 101
        assert alternative.field == "author" and alternative.value == "Contradictoire"
        assert alternative.source_revision == detail.source_revision
        assert read_purchase_descriptive_notice(db, source, identity_id="B") is None
        scope = source.scope
        row = {"key": "card", "identity_id": scope.identity_id, "provider_id": scope.provider_id,
               "item_id": source.edition_id, "source": "private_catalog_observation",
               "state": "remote_catalog", "local": False, "title": "Titre natif",
               "source_refs": [{"identity_id": scope.identity_id, "provider_id": scope.provider_id,
                                "credential_id": scope.credential_id, "account_id": scope.account_id,
                                "item_id": source.edition_id, "kind": "observation"}]}
        project_purchase_library_notices(db, [row], identity_id=scope.identity_id)
        packet = purchase_library_notice(row)
        assert packet.notice == result
        assert filter_loaded_results([row], "archipel violet") == [row]
        assert not filter_loaded_results([row], "Contradictoire", field="author")
        sections = dict(local_book_detail_sections(
            row, provider_display_name=str, availability_label=lambda _: "",
            identity_by_id=lambda _: None, sync_status_label=str,
            language_label=lambda _: "", purchase_notice=packet,
        ))
        assert ("Résumé", "Archipel violet") in sections["Fiche du catalogue public"]
        label, value = sections["Valeurs publiques contradictoires (non arbitrées)"][0]
        assert label == "Auteur" and "Contradictoire" in value and "00:01:41 UTC" in value
        assert not result.authorizes_acquisition and not result.proves_acquired_edition
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()
