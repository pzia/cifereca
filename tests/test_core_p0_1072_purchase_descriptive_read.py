"""Read only qualified descriptive metadata, not a purchased edition proof."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from cifereca.core.notice_c_purchase_library_projection import project_purchase_library_notices, purchase_library_notice
from cifereca.gui.library_rows import project_library_rows

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_purchase_execution import PurchaseIntentExecution
from cifereca.core.notice_c_purchase_publication import publish_purchase_public_txn
from cifereca.core.notice_c_purchase_association import record_purchase_association_txn
from cifereca.core.notice_c_purchase_descriptive_read import read_purchase_descriptive_notice
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.storage import Storage
from tests.test_core_p0_1072_durable_purchase_intent import prepare, set_policy, request
from tests.test_core_p0_1072_nondestructive_purchase_publication import observation


@contextmanager
def allowed():
    yield True


def test_public_descriptive_facts_keep_source_provenance_not_association_time(tmp_path):
    path = tmp_path / "description.sqlite"
    db = Storage(path)
    execution = PurchaseIntentExecution(db, clock=lambda: 100)
    try:
        snapshot, candidate = prepare(db)
        persist_attested_source_references(db, snapshot)
        set_policy(db, "true")
        ref, = request(db, snapshot, candidate)
        assert execution.attest(ref, snapshot, candidate, hold_context=allowed,
                                execute=lambda *_: None, ttl_seconds=30)
        lease = execution.claim(ref, now=100, lease_seconds=30)
        assert lease is not None
        spec = journal.read_job(db, ref).spec
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN IMMEDIATE")
            published = publish_purchase_public_txn(
                sql, spec, snapshot, candidate, observation(candidate, at=98), now=100,
            )
            record_purchase_association_txn(
                sql, lease, snapshot, candidate, published, observed_at=99, now=100,
            )
            journal._complete_job_txn(sql, lease, now=100)
        assert read_purchase_descriptive_notice(db, candidate.source, identity_id="A") is None
        persist_exact_public_search_cover(db, published.source)
        set_policy(db, "false")
    finally:
        execution.close()
        db.close()

    db = Storage(path)
    try:
        result = read_purchase_descriptive_notice(db, candidate.source, identity_id="A")
        assert result is not None and result.document == published.document
        assert result.association_observed_at == 99
        assert not result.proves_acquired_edition and not result.authorizes_acquisition
        facts = {f.field: f.value for f in result.facts}
        assert facts["title"] == candidate.title and facts["description"] == "Résumé public"
        assert set(facts) <= {"title", "author", "publisher", "description", "published_at"}
        assert all(f.observed_at == 98 and f.provider_id == "seller-a"
                   and f.source_ref == published.source.document.source_key
                   and f.source_revision == published.source.observation.source_revision
                   for f in result.facts)
        source = candidate.source
        scope = source.scope
        card = {
            "key": "observation:fixture", "identity_id": scope.identity_id,
            "provider_id": scope.provider_id, "item_id": source.edition_id,
            "source": "private_catalog_observation", "state": "remote_catalog",
            "local": False, "title": "Titre natif", "author": "",
            "source_refs": [{
                "identity_id": scope.identity_id, "provider_id": scope.provider_id,
                "credential_id": scope.credential_id, "account_id": scope.account_id,
                "item_id": source.edition_id, "kind": "observation",
            }], "metadata": {"description": "Résumé local"}, "possessions": [],
        }
        original = deepcopy(card)
        foreign = deepcopy(card)
        foreign["identity_id"] = "B"
        foreign["key"] = "observation:foreign"
        project_purchase_library_notices(db, [card, foreign], identity_id="A")
        packet = purchase_library_notice(card)
        assert packet is not None and packet.notice == result
        assert purchase_library_notice(foreign) is None
        assert {k: v for k, v in card.items() if k != "_purchase_library_notice"} == original
        rows, objects = project_library_rows(
            [card], row_factory=lambda *args: SimpleNamespace(values=args),
            provider_display_name=str, availability_label=lambda _: "",
            backup_status_label=str, format_date=str,
        )
        assert rows[0]._purchase_library_notice == packet
        assert rows[0].values[1] == "Titre natif" and objects[card["key"]] is card
        # Duplicate key cannot claim the other row's proof.
        duplicate = deepcopy(card)
        project_purchase_library_notices(db, [card, duplicate], identity_id="A")
        assert purchase_library_notice(card) is None and purchase_library_notice(duplicate) is None
        project_purchase_library_notices(db, [card], identity_id="A")
        assert purchase_library_notice(card) == packet
        assert read_purchase_descriptive_notice(db, candidate.source, identity_id="B") is None
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE provider_connections SET enabled=0 WHERE credential_id='card-a'")
        assert read_purchase_descriptive_notice(db, candidate.source, identity_id="A") is None
        project_purchase_library_notices(db, [card], identity_id="A")
        assert purchase_library_notice(card) is None and card == original
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
    finally:
        db.close()
