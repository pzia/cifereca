"""#3171: optional typed A admitted inside existing source Sync, no second loop.

Real SQLite v14; provider snapshots and PDF bytes synthetic only.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
import sqlite3

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.canonical_sync_stage import observe_canonical_rights_in_sync
from cifereca.models import Account
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, register, target, publish,
)


class Service:
    def __init__(self, storage, root, *, typed=True):
        self.storage = storage
        self.root = root
        self.account = Account(
            identity_id="A", provider_id="seller-a",
            credential_id="card-a", unique_id="buyer-a",
        )
        self.accounts = [self.account]
        self._account_context_epoch = 1
        self.enabled = True
        self._sessions = {}
        self.events = []
        self.read_calls = 0
        self.source, self.snapshot = new_source(
            "A", provider="seller-a", credential="card-a", account="buyer-a",
        )
        session = NS(
            provider_id="seller-a", credential_id="card-a",
            connected=True, _closed=False,
        )
        if typed:
            def read(_account):
                self.read_calls += 1
                return self.snapshot
            session.rights_observation_snapshot = read
        self.session = session
        self._sessions[self._session_key("seller-a", "card-a")] = session
        self._synchronize_account_source_impl = lambda account, deep=False, control=None: {
            "provider": account.provider_id, "status": "ok",
        }
        self.registry = None
        self.diagnostics = NS(add_core_source_sync=lambda *args, **kwargs: None)

    @staticmethod
    def _session_key(provider, credential):
        return provider + ":" + credential

    def _session_for_account(self, account):
        return self._sessions[self._session_key(
            account.provider_id, account.credential_id,
        )]

    def library_dir(self):
        return self.root

    def _record_action(self, identity, operation, status, **kwargs):
        self.events.append((identity, operation, status))

    def sync_source_enabled(self, account):
        return self.enabled


@pytest.fixture
def rig(tmp_path, monkeypatch):
    db = Storage(tmp_path / "db.sqlite")
    assert db.schema_version() == 14
    register(db, "A", "seller-a", "card-a", account="buyer-a")
    root = tmp_path / "EBooks"
    service = Service(db, root)
    monkeypatch.setattr(
        "cifereca.core.sync_coordination.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    monkeypatch.setattr(
        "cifereca.core.convergence_execution.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    try:
        yield service, db, root
    finally:
        db.close()


def refs(db):
    with db._lock, db._connect() as connection:
        refs = connection.execute(
            "SELECT identity_id,provider_id,credential_id,account_id,"
            "branch_id,edition_id,right_id,reference_kind,state "
            "FROM source_references ORDER BY source_reference_id"
        ).fetchall()
        links = connection.execute(
            "SELECT count(*) FROM origin_links"
        ).fetchone()[0]
    return refs, links


def test_typed_A_saved_in_same_existing_source_sync_once_and_without_get(rig):
    service, db, root = rig
    first = synchronize_account_source(service, service.account)
    assert first["status"] == "partial"
    assert service.read_calls == 1
    assert first["canonical_rights"] == {
        "status": "partial", "observed": 1, "attested_active": 1,
        "source_references_changed": 1, "present_attested": 0,
        "pending": 1, "read_only_plan": True, "acquisition_authorized": False,
    }
    saved, links = refs(db)
    assert saved == [(
        "A", "seller-a", "card-a", "buyer-a",
        "purchases", "edition", "purchase", "purchase", "active",
    )]
    assert links == 0 and db.placements() == []
    assert db.entitlements() == [] and db.acquisitions() == []
    second = synchronize_account_source(service, service.account)
    assert service.read_calls == 2
    assert second["canonical_rights"]["source_references_changed"] == 0
    assert refs(db) == (saved, 0)


def test_zero_rights_and_partial_snapshot_never_revoke_prior_positive(rig):
    service, db, root = rig
    synchronize_account_source(service, service.account)
    prior, _ = refs(db)
    service.snapshot = replace(service.snapshot, rights=())
    result = synchronize_account_source(service, service.account)
    assert result["canonical_rights"]["observed"] == 0
    assert result["canonical_rights"]["source_references_changed"] == 0
    assert refs(db) == (prior, 0)


def test_completed_delivery_origin_is_present_on_next_source_sync(rig):
    service, db, root = rig
    synchronize_account_source(service, service.account)
    file = target(root.parent, "A")
    publish(db, root, file, service.source, service.snapshot)
    summary = synchronize_account_source(service, service.account)
    assert summary["canonical_rights"]["present_attested"] == 1
    assert summary["canonical_rights"]["pending"] == 0
    assert len(db.placements()) == 1
    assert db.placements()[0]["identity_id"] == "A"


def test_reader_failure_preserves_positive_and_marks_source_partial(rig):
    service, db, root = rig
    synchronize_account_source(service, service.account)
    before, _ = refs(db)
    service.snapshot = replace(
        service.snapshot, rights=(), status="failed", coverage="unknown",
    )
    result = synchronize_account_source(service, service.account)
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert refs(db) == (before, 0)


def test_missing_optional_producer_leaves_existing_source_sync_unchanged(rig):
    service, db, root = rig
    del service.session.rights_observation_snapshot
    result = synchronize_account_source(service, service.account)
    assert result["status"] == "ok"
    assert "canonical_rights" not in result
    assert service.read_calls == 0 and refs(db) == ([], 0)


def test_rebound_during_provider_read_cannot_publish_new_reference(rig):
    service, db, root = rig
    def rebound(_account):
        service._account_context_epoch += 1
        return service.snapshot
    service.session.rights_observation_snapshot = rebound
    with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
        synchronize_account_source(service, service.account)
    assert refs(db) == ([], 0)


def test_sql_error_rolls_back_full_positive_batch_without_origin(rig):
    service, db, root = rig
    extra = replace(
        service.snapshot.rights[0], edition_id="edition-second",
        right_id="purchase-second",
    )
    service.snapshot = replace(
        service.snapshot, rights=service.snapshot.rights + (extra,),
    )
    with db._lock, db._connect() as conn:
        conn.execute(
            "CREATE TRIGGER synthetic_abort BEFORE INSERT ON source_references "
            "WHEN NEW.edition_id='edition-second' BEGIN "
            "SELECT RAISE(ABORT,'synthetic_rights_failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_rights_failure"):
        synchronize_account_source(service, service.account)
    assert refs(db) == ([], 0)
    with db._lock, db._connect() as conn:
        conn.execute("DROP TRIGGER synthetic_abort")
    outcome = synchronize_account_source(service, service.account)
    assert outcome["canonical_rights"]["source_references_changed"] == 2
    assert len(refs(db)[0]) == 2


def test_unverified_historical_receipt_never_promoted_to_canonical_right(rig):
    service, db, root = rig
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], evidence_kind="historical_receipt",
    ),))
    result = synchronize_account_source(service, service.account)
    assert result["canonical_rights"]["attested_active"] == 0
    assert refs(db) == ([], 0)


def test_existing_source_sync_executes_one_authorized_get_then_no_second_get(rig):
    service, db, root = rig
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available",
        delivery_offer_id="exact-synthetic-offer",
    ),))
    consent = {service.source}
    service._canonical_persisted_consent = lambda _: frozenset(consent)
    downloads = []
    def completed(key, offer):
        downloads.append((key, offer.delivery_offer_id))
        path = root / "A" / "delivered.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA,
            "receipt-for-the-completed-synthetic-delivery",
        )
    service._canonical_right_delivery = completed

    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert first["canonical_execution"]["present_attested"] == 0
    assert first["files_materialized"] == 1
    assert len(downloads) == 1
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 1
    assert len(db.placements()) == 1
    assert db.entitlements() == []
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["downloaded"] == 0
    assert second["canonical_execution"]["present_attested"] == 1
    assert second.get("files_materialized", 0) == 0
    assert len(downloads) == 1
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 1


def test_source_sync_stays_read_only_without_core_persisted_consent_route(rig):
    service, db, root = rig
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available",
        delivery_offer_id="exact-synthetic-offer",
    ),))
    service._canonical_right_delivery = lambda *_: pytest.fail(
        "no persisted consent provider, so no GET",
    )
    result = synchronize_account_source(service, service.account)
    assert "canonical_execution" not in result
    assert result["canonical_rights"]["attested_active"] == 1
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 0
    assert db.placements() == []


def test_real_source_sync_consent_revocation_during_get_blocks_origin(rig):
    service, db, root = rig
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available",
        delivery_offer_id="exact-synthetic-offer",
    ),))
    consent = {service.source}
    service._canonical_persisted_consent = lambda _: frozenset(consent)
    def completed(key, offer):
        consent.clear()
        path = root / "A" / "downloaded-but-no-right.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA,
            "synthetic-uncommitted-receipt",
        )
    service._canonical_right_delivery = completed
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["blocked"] == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["status"] == "partial"
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 0
    assert db.placements() == []


def test_core_executor_rejects_mismatch_between_delivered_offer_and_approved_right(rig):
    service, db, root = rig
    service.snapshot = replace(service.snapshot, rights=(replace(
        service.snapshot.rights[0], delivery="available",
        delivery_offer_id="exact-synthetic-offer",
    ),))
    service._canonical_persisted_consent = lambda _: frozenset({service.source})
    def wrong(_key, _offer):
        path = root / "A" / "wrong-offer.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            service.source, "unapproved-offer", path, SHA,
            "synthetic-unapproved-receipt",
        )
    service._canonical_right_delivery = wrong
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["failed"] == 1
    assert result["status"] == "partial"
    assert len(refs(db)[0]) == 1 and refs(db)[1] == 0
    assert db.placements() == []


def test_disabled_source_mid_read_does_not_publish_A(rig):
    service, db, root = rig
    def disabled(_account):
        service.enabled = False
        return service.snapshot
    service.session.rights_observation_snapshot = disabled
    with pytest.raises(ValueError, match="canonical_rights_source_rebound"):
        synchronize_account_source(service, service.account)
    assert refs(db) == ([], 0)
    assert db.placements() == []


def test_disabled_source_before_typed_read_never_invokes_provider(rig):
    service, db, root = rig
    service.enabled = False
    with pytest.raises(ValueError, match="canonical_rights_source_disabled"):
        observe_canonical_rights_in_sync(service, service.account)
    assert service.read_calls == 0
    assert refs(db) == ([], 0)


def test_connection_disabled_in_sql_during_read_refuses_A_publication(rig):
    service, db, root = rig
    def disabled(_account):
        with db._lock, db._connect() as conn:
            conn.execute(
                "UPDATE provider_connections SET enabled=0 "
                "WHERE provider_id='seller-a' AND credential_id='card-a'"
            )
        return service.snapshot
    service.session.rights_observation_snapshot = disabled
    result = synchronize_account_source(service, service.account)
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert result["canonical_rights"]["source_references_changed"] == 0
    assert refs(db) == ([], 0)
    assert db.placements() == []


def test_removed_profile_cannot_receive_new_A_through_stale_connection(rig):
    service, db, root = rig
    with db._lock, db._connect() as conn:
        conn.execute("DELETE FROM identities WHERE identity_id='A'")
    result = synchronize_account_source(service, service.account)
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert result["canonical_rights"]["source_references_changed"] == 0
    assert refs(db) == ([], 0)


def test_typed_session_without_registered_upstream_account_does_not_break_legacy_sync(rig):
    service, db, root = rig
    with db._lock, db._connect() as conn:
        conn.execute(
            "DELETE FROM provider_accounts WHERE identity_id='A' "
            "AND provider_id='seller-a' AND credential_id='card-a'"
        )
    result = synchronize_account_source(service, service.account)
    assert service.read_calls == 1
    assert result["provider"] == "seller-a"
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert result["canonical_rights"]["source_references_changed"] == 0
    assert refs(db) == ([], 0)
    assert db.entitlements() == [] and db.placements() == []


def test_sessionless_local_source_without_canonical_reader_preserves_exact_result(rig):
    service, db, root = rig
    service.accounts = [Account(
        identity_id="A", provider_id="local",
        credential_id="local", unique_id="local",
    )]
    service._sessions.clear()
    result = synchronize_account_source(service, service.accounts[0])
    assert result == {"provider": "local", "status": "ok"}
    assert refs(db) == ([], 0)
