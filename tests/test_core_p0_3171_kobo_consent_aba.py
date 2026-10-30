"""#3171 P0: Kobo ON->OFF->ON must not revive a pre-revocation GET.

Real SQLite and canonical A->B executor, synthetic authenticated provider.
"""
from dataclasses import replace
import sqlite3

import pytest

from cifereca.core import source_auto_consent_bridge as bridge
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.source_acquisition_consent import (
    consent_current_in_transaction, read_source_acquisition_consent,
)
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, new_source, register
from tests.test_core_p0_3171_canonical_sync_stage import rig


def _kobo(rig):
    service, db, root = rig
    account = replace(service.account, provider_id="kobo")
    service.account = account
    service.accounts = [account]
    service.session.provider_id = "kobo"
    service.session.accounts = [account]
    service._sessions = {service._session_key("kobo", account.credential_id): service.session}
    service.source, service.snapshot = new_source(
        "A", provider="kobo", credential=account.credential_id,
        account=account.unique_id,
    )
    service.snapshot = replace(
        service.snapshot, rights=(replace(
            service.snapshot.rights[0], delivery="available",
            delivery_offer_id="kobo-verified-private-offer",
        ),),
    )
    register(db, "A", "kobo", account.credential_id, account=account.unique_id)
    persist_attested_source_references(db, service.snapshot)
    service._canonical_persisted_consent = (
        lambda observed: read_source_acquisition_consent(service, observed)
    )
    service._canonical_publication_consent_current = (
        lambda conn, source: consent_current_in_transaction(conn, source)
    )
    sid = bridge._source_id(service.source.scope)
    policy = "sync.source.materialization.on_start." + sid
    return service, db, root, policy


def test_kobo_legacy_on_without_epoch_still_grants_but_reopt_changes_generation(rig):
    service, db, root, policy = _kobo(rig)
    db.metadata_set(policy, "1")
    assert bridge.consent_generation(db, service.source.scope) is None
    assert read_source_acquisition_consent(service, service.account) == frozenset({
        service.source,
    })
    db.metadata_set_with_revision(policy, "0", policy + ".revision")
    off = bridge.consent_generation(db, service.source.scope)
    assert len(off) == 32
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    db.metadata_set_with_revision(policy, "1", policy + ".revision")
    on = bridge.consent_generation(db, service.source.scope)
    assert on != off and len(on) == 32
    assert read_source_acquisition_consent(service, service.account) == frozenset({
        service.source,
    })
    with db._lock, db._connect() as conn:
        assert bridge.consent_generation_in_transaction(conn, service.source) == on


def test_kobo_off_on_while_delivery_in_flight_never_publishes_old_get(rig):
    service, db, root, policy = _kobo(rig)
    # Simulate a pre-upgrade persisted ON without a revision.
    db.metadata_set(policy, "1")
    calls = []

    def delivered(key, offer):
        calls.append(offer.delivery_offer_id)
        path = root / "A" / "old-attempt.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        db.metadata_set_with_revision(policy, "0", policy + ".revision")
        db.metadata_set_with_revision(policy, "1", policy + ".revision")
        assert read_source_acquisition_consent(service, service.account) == frozenset({
            service.source,
        })
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA, "stale-delivery-receipt",
        )

    service._canonical_right_delivery = delivered
    first = synchronize_account_source(service, service.account)
    assert calls == ["kobo-verified-private-offer"]
    assert first["canonical_execution"]["downloaded"] == 0
    assert db.placements() == []
    with db._lock, db._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM origin_links").fetchone()[0] == 0


def test_policy_and_generation_commit_or_rollback_as_one_sqlite_unit(rig):
    service, db, root, policy = _kobo(rig)
    revision = policy + ".revision"
    db.metadata_set_with_revision(policy, "1", revision)
    before = (db.metadata_get(policy), db.metadata_get(revision))
    with db._lock, db._connect() as conn:
        conn.execute(
            "CREATE TRIGGER refuse_toggle BEFORE UPDATE ON metadata "
            "WHEN OLD.key='" + policy + "' "
            "BEGIN SELECT RAISE(ABORT,'synthetic_toggle_failure'); END"
        )
    try:
        with pytest.raises(sqlite3.IntegrityError, match="synthetic_toggle_failure"):
            db.metadata_set_with_revision(policy, "0", revision)
        assert (db.metadata_get(policy), db.metadata_get(revision)) == before
        assert read_source_acquisition_consent(service, service.account) == frozenset({
            service.source,
        })
    finally:
        with db._lock, db._connect() as conn:
            conn.execute("DROP TRIGGER refuse_toggle")


def test_public_kobo_schedule_setter_increments_epoch_for_every_transition(tmp_path):
    from cifereca.brokers import SynchronizationBrokerImpl
    from tests.test_kobo_backup_guard import _service

    service = _service(tmp_path)
    try:
        session = service._sessions[service._session_key("kobo", "vault-1")]
        account = session.accounts[0]
        service.accounts = [account]
        broker = SynchronizationBrokerImpl(service)
        sid = broker._source_id(account)
        policy = "sync.source.materialization.on_start." + sid
        assert broker.set_local_materialization_schedule(sid, "on_start") == "on_start"
        first = service.storage.metadata_get(policy + ".revision")
        assert len(first) == 32
        assert broker.set_local_materialization_schedule(sid, "manual") == "manual"
        second = service.storage.metadata_get(policy + ".revision")
        assert second != first
        assert broker.set_local_materialization_schedule(sid, "on_start") == "on_start"
        third = service.storage.metadata_get(policy + ".revision")
        assert len({first, second, third}) == 3
        assert service.storage.metadata_get(policy) == "1"
    finally:
        service.close()
