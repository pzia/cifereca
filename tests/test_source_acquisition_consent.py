"""#3171: durable exact-right consent, real SQLite and existing source Sync.

Delivery is synthetic; no actual provider preflight, network or ADEPT.
"""
from dataclasses import replace
import json

import pytest

from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.source_acquisition_consent import (
    consent_key, read_source_acquisition_consent, set_source_acquisition_consent,
)
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.services import LibraryService
from cifereca.storage import Storage
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA


def grant(service):
    persist_attested_source_references(service.storage, service.snapshot)
    LibraryService.set_canonical_acquisition_consent(
        service, service.account, service.source, allowed=True,
    )


def test_missing_and_legacy_global_preferences_never_authorize(rig):
    service, db, _ = rig
    persist_attested_source_references(db, service.snapshot)
    db.metadata_set("kobo_backup.enabled", "1")
    db.metadata_set("sync.source.materialization.on_start.any", "1")
    assert LibraryService._canonical_persisted_consent(service, service.account) == frozenset()


def test_consent_survives_reopen_and_revocation_is_fresh(rig):
    service, db, _ = rig
    grant(service)
    assert read_source_acquisition_consent(service, service.account) == frozenset({service.source})
    db.close()
    reopened = Storage(db.path)
    service.storage = reopened
    try:
        assert read_source_acquisition_consent(service, service.account) == frozenset({service.source})
        set_source_acquisition_consent(service, service.account, service.source, allowed=False)
        assert read_source_acquisition_consent(service, service.account) == frozenset()
    finally:
        reopened.close()


@pytest.mark.parametrize("change", [
    {"branch": "another-branch"}, {"edition_id": "another-edition"},
    {"right_id": "another-right"}, {"kind": "loan"},
])
def test_consent_does_not_cover_another_exact_reference(rig, change):
    service, db, _ = rig
    grant(service)
    other = replace(service.source, **change)
    assert consent_key(other) != consent_key(service.source)
    with pytest.raises(ValueError, match="positive_source_required"):
        set_source_acquisition_consent(service, service.account, other, allowed=True)
    assert read_source_acquisition_consent(service, service.account) == frozenset({service.source})


@pytest.mark.parametrize("field", ["identity_id", "provider_id", "credential_id", "account_id"])
def test_consent_is_not_reassigned_by_scope_change(rig, field):
    service, db, _ = rig
    grant(service)
    other = replace(service.source, scope=replace(service.source.scope, **{field: "foreign"}))
    with pytest.raises(ValueError, match="scope_changed"):
        set_source_acquisition_consent(service, service.account, other, allowed=True)
    assert read_source_acquisition_consent(service, service.account) == frozenset({service.source})


@pytest.mark.parametrize("tamper", ["disabled", "unbound", "inactive", "historical", "transfer"])
def test_stored_grant_cannot_override_current_core_binding_or_positive_A(rig, tamper):
    service, db, _ = rig
    grant(service)
    with db._lock, db._connect() as connection:
        if tamper == "disabled":
            connection.execute("UPDATE provider_connections SET enabled=0")
        elif tamper == "unbound":
            connection.execute("DELETE FROM provider_accounts")
        elif tamper == "inactive":
            connection.execute("UPDATE source_references SET state='inactive'")
        elif tamper == "historical":
            connection.execute("UPDATE source_references SET evidence_kind='historical_receipt'")
        else:
            connection.execute("UPDATE source_references SET synchronizable=0")
    assert read_source_acquisition_consent(service, service.account) == frozenset()


@pytest.mark.parametrize("value", ["1", "true", "{", "null", "[]", "{}"])
def test_malformed_or_unversioned_metadata_grants_nothing(rig, value):
    service, db, _ = rig
    grant(service)
    db.metadata_set(consent_key(service.source), value)
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_grant_requires_recorded_positive_source_and_boolean(rig):
    service, _, _ = rig
    with pytest.raises(ValueError, match="positive_source_required"):
        set_source_acquisition_consent(service, service.account, service.source, allowed=True)
    with pytest.raises(ValueError, match="invalid_acquisition_consent_request"):
        set_source_acquisition_consent(service, service.account, service.source, allowed=1)


def test_payload_scope_and_boolean_are_checked_independently_of_key(rig):
    service, db, _ = rig
    grant(service)
    key = consent_key(service.source)
    payload = json.loads(db.metadata_get(key))
    payload["allowed"] = 1
    db.metadata_set(key, json.dumps(payload))
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    payload["allowed"] = True
    payload["source"][0] = "foreign-profile"
    db.metadata_set(key, json.dumps(payload))
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_epoch_change_during_permission_write_rolls_back(rig):
    service, db, _ = rig
    persist_attested_source_references(db, service.snapshot)
    def rebound():
        service._account_context_epoch += 1
        return 1
    with db._lock, db._connect() as connection:
        connection.create_function("synthetic_rebind", 0, rebound)
        connection.execute("CREATE TRIGGER consent_rebind AFTER INSERT ON metadata "
                           "BEGIN SELECT synthetic_rebind(); END")
    with pytest.raises(ValueError, match="scope_changed"):
        set_source_acquisition_consent(service, service.account, service.source, allowed=True)
    assert db.metadata_get(consent_key(service.source)) is None


def test_persisted_consent_drives_existing_sync_once_and_is_revoked_during_delivery(rig):
    service, db, root = rig
    grant(service)
    service._canonical_persisted_consent = lambda account: LibraryService._canonical_persisted_consent(service, account)
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available", delivery_offer_id="exact-offer",
    ),))
    calls = []
    def deliver(key, offer):
        calls.append(key)
        path = root / "A" / "delivered.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(key, offer.delivery_offer_id, path, SHA, "completed-receipt")
    service._canonical_right_delivery = deliver
    assert synchronize_account_source(service, service.account)["canonical_execution"]["downloaded"] == 1
    assert synchronize_account_source(service, service.account)["canonical_execution"]["present_attested"] == 1
    assert len(calls) == 1 and refs(db)[1] == 1
    # Removal of physical bytes requires a fresh acquisition, whose in-flight
    # revocation must prevent a new publication despite the frozen admission.
    (root / "A" / "delivered.pdf").unlink()
    def revoked(key, offer):
        result = deliver(key, offer)
        set_source_acquisition_consent(service, service.account, service.source, allowed=False)
        return result
    service._canonical_right_delivery = revoked
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["blocked"] == 1
    assert len(calls) == 2 and refs(db)[1] == 1
    assert read_source_acquisition_consent(service, service.account) == frozenset()


@pytest.mark.parametrize("column,value", [("evidence_ref", ""), ("observed_at", None), ("observed_at", "invalid")])
def test_unproven_reference_cannot_support_grant_or_replay(rig, column, value):
    service, db, _ = rig
    grant(service)
    with db._lock, db._connect() as connection:
        connection.execute("UPDATE source_references SET " + column + "=?", (value,))
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    with pytest.raises(ValueError, match="positive_source_required"):
        set_source_acquisition_consent(service, service.account, service.source, allowed=True)


def test_disabled_source_can_revoke_a_prior_grant(rig):
    service, db, _ = rig
    grant(service)
    service.enabled = False
    with pytest.raises(ValueError, match="source_disabled"):
        set_source_acquisition_consent(service, service.account, service.source, allowed=True)
    set_source_acquisition_consent(service, service.account, service.source, allowed=False)
    service.enabled = True
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_ambiguous_account_registry_and_budget_overflow_never_grant(rig, monkeypatch):
    service, db, _ = rig
    grant(service)
    service.accounts.append(replace(service.account))
    assert read_source_acquisition_consent(service, service.account) == frozenset()
    service.accounts.pop()
    monkeypatch.setattr("cifereca.core.source_acquisition_consent._LIMIT", 0)
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_revocation_during_final_hash_prevents_atomic_publication(rig, monkeypatch):
    service, db, root = rig
    grant(service)
    service._canonical_persisted_consent = lambda account: LibraryService._canonical_persisted_consent(service, account)
    service._canonical_publication_consent_current = lambda connection, source: LibraryService._canonical_publication_consent_current(service, connection, source)
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available", delivery_offer_id="exact-offer",
    ),))
    def deliver(key, offer):
        path = root / "A" / "delivered.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(key, offer.delivery_offer_id, path, SHA, "completed-receipt")
    service._canonical_right_delivery = deliver
    from cifereca.core import canonical_publication
    real_publish = canonical_publication.publish_completed_acquisition
    def revoke_while_hashing(*args, **kwargs):
        from cifereca.catalog import LocalCatalog
        original_hash = LocalCatalog.sha256
        def hashing(path):
            digest = original_hash(path)
            set_source_acquisition_consent(service, service.account, service.source, allowed=False)
            return digest
        with monkeypatch.context() as patch:
            patch.setattr(LocalCatalog, "sha256", staticmethod(hashing))
            return real_publish(*args, **kwargs)
    monkeypatch.setattr("cifereca.core.convergence_execution.publish_completed_acquisition", revoke_while_hashing)
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0 and db.placements() == []
    assert read_source_acquisition_consent(service, service.account) == frozenset()


def test_final_transaction_guard_rolls_back_all_publication_rows(rig):
    service, db, root = rig
    grant(service)
    path = root / "A" / "delivered.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(BYTES)
    from cifereca.core.canonical_publication import publish_completed_acquisition
    checks = []
    def policy_guard(connection):
        checks.append(connection)
        return len(checks) == 1
    with pytest.raises(ValueError, match="canonical_publication_policy_changed"):
        publish_completed_acquisition(
            db, library_root=root, path=path, source=service.source,
            snapshot=service.snapshot, final_sha256=SHA,
            delivery_receipt_ref="completed-receipt", transaction_guard=policy_guard,
        )
    assert len(checks) == 2 and checks[0] is checks[1]
    assert refs(db)[1] == 0 and db.placements() == []
    with db._lock, db._connect() as connection:
        assert connection.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 0
    assert path.read_bytes() == BYTES
