"""Real Core service/SQLite: source scope survives concurrent UI selection.

Provider calls and PDF bytes are synthetic. The provider adapter must itself
use IdentityBroker.accounts(identity_id); Core cannot repair an unscoped call.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Event
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import IdentityBrokerImpl
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from cifereca.core.source_context import source_context_epoch
from cifereca.models import Account
from tests.test_sync_state_contract import _service
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, register,
)


@pytest.fixture
def context(tmp_path, monkeypatch):
    service = _service(tmp_path)
    service.accounts = []
    scopes = {}
    for identity in service.identities:
        owner = identity.identity_id
        account = Account(identity_id=owner, provider_id="seller",
                          credential_id="card-" + owner, unique_id="same-buyer")
        register(service.storage, owner, "seller", account.credential_id,
                 account=account.unique_id)
        service.storage.identity_put({"identity_id": owner,
                                      "directory_name": identity.directory_name,
                                      "label": identity.label})
        key, snapshot = new_source(owner, provider="seller",
                                  credential=account.credential_id,
                                  account=account.unique_id)
        snapshot = replace(snapshot, status="success", coverage="complete",
                           pagination_complete=True,
                           observed_at=snapshot.rights[0].observed_at,
                           snapshot_ref="synthetic-complete-inventory")
        session = NS(provider_id="seller", credential_id=account.credential_id,
                     connected=True, _closed=False, accounts=[account],
                     capabilities=NS(loans=False, personal_library=False))
        session.rights_observation_snapshot = lambda a, snap=snapshot: snap
        service.accounts.append(account)
        service._sessions[service._session_key("seller", account.credential_id)] = session
        scopes[owner] = (account, session, key, snapshot)
    lock = tmp_path / "state" / "acquisition.lock"
    for module in ("sync_coordination", "canonical_selected_offers",
                   "canonical_selected_delivery", "convergence_execution",
                   "canonical_recovery"):
        monkeypatch.setattr("cifereca.core." + module + ".acquisition_lock_file",
                            lambda: lock)
    try:
        yield service, scopes
    finally:
        service.close()


@pytest.mark.parametrize("selected", ["default", "alex"])
@pytest.mark.parametrize("synced", ["default", "alex"])
def test_scoped_observation_and_broker_ignore_displayed_profile(context, selected, synced):
    service, scopes = context
    service.select_identity(selected)
    account, session, key, expected = scopes[synced]
    refs = IdentityBrokerImpl(service).accounts(synced)
    assert [(r.identity_id, r.credential_id, r.account_id) for r in refs] == [
        (synced, account.credential_id, account.unique_id),
    ]
    assert read_account_rights_snapshot(service, account) == expected
    assert service.current_identity.identity_id == selected


def test_concurrent_identity_reads_survive_ui_round_trip(context):
    service, scopes = context
    ready, resume = Barrier(3), Event()
    for account, session, key, expected in scopes.values():
        def read(_account, snap=expected):
            ready.wait(timeout=5)
            assert resume.wait(timeout=5)
            return snap
        session.rights_observation_snapshot = read
    generation = source_context_epoch(service)
    ui_generation = service._account_context_epoch
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(read_account_rights_snapshot, service, row[0])
                   for row in scopes.values()]
        try:
            ready.wait(timeout=5)
            service.select_identity("alex")
            service.select_identity("default")
        finally:
            resume.set()
        results = [f.result(timeout=5) for f in futures]
    assert service._account_context_epoch > ui_generation
    assert source_context_epoch(service) == generation
    assert results == [row[3] for row in scopes.values()]
    assert service.storage.acquisitions() == service.storage.placements() == []


def test_global_opening_sync_keeps_both_source_scopes_during_selection_changes(context, monkeypatch):
    service, scopes = context
    monkeypatch.setattr(service, "_loans_for_account", lambda *a, **kw: [])
    calls = []
    for owner, (account, session, key, expected) in scopes.items():
        def read(_account, identity=owner, snap=expected):
            calls.append(identity)
            service.select_identity("alex" if identity == "default" else "default")
            return snap
        session.rights_observation_snapshot = read
    summary = service.opening_sync()
    assert calls == ["default", "alex"]
    for row in summary["identities"]:
        assert row["canonical_rights"]["observed"] == 1
        assert row["canonical_rights"].get("failed", 0) == 0
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute("SELECT identity_id,credential_id FROM source_references "
                          "ORDER BY identity_id").fetchall() == [
            ("alex", "card-alex"), ("default", "card-default"),
        ]
    assert service.storage.acquisitions() == service.storage.placements() == []


@pytest.mark.parametrize("phase", ["rights", "offer", "candidate", "commit"])
def test_full_sync_selection_change_does_not_revoke_exact_delivery(context, phase):
    service, scopes = context
    account, session, source, snapshot = scopes["default"]
    persist_attested_source_references(service.storage, snapshot)
    service.set_canonical_acquisition_consent(account, source, allowed=True)
    deliveries = []
    selections = []

    def select():
        selections.append(phase)
        service.select_identity("alex")
        return 0

    def read(_account):
        if phase == "rights":
            select()
        return snapshot

    def offer(_account, **kwargs):
        if phase == "offer":
            select()
        return replace(snapshot, coverage="partial", rights=(replace(snapshot.rights[0],
                       delivery="available", delivery_offer_id="exact-offer"),))

    def candidate(_account, *, target_dir, **kwargs):
        deliveries.append(account.identity_id)
        if phase == "candidate":
            select()
        path = target_dir / "received.pdf"
        path.write_bytes(BYTES)
        return path, "https://supplier.invalid/synthetic.pdf"

    session.rights_observation_snapshot = read
    session.rights_selected_delivery_offer_snapshot = offer
    session.rights_selected_delivery_candidate = candidate
    if phase == "commit":
        with service.storage._lock, service.storage._connect() as db:
            db.create_function("select_during_publication", 0, select)
            db.execute("CREATE TRIGGER select_ui AFTER INSERT ON origin_links "
                       "BEGIN SELECT select_during_publication(); END")
    try:
        first = service.synchronize_account_source(account)
        assert first["canonical_execution"]["downloaded"] == 1, first
        assert selections and deliveries == ["default"]
        assert service.current_identity.identity_id == "alex"
        placement, = service.storage.placements()
        assert placement["identity_id"] == "default"
        assert service.last_loans == []
        second = service.synchronize_account_source(account)
        assert second["canonical_execution"]["present_attested"] == 1
        assert second["canonical_execution"]["downloaded"] == 0
        assert deliveries == ["default"]
        assert service.storage.placements() == [placement]
    finally:
        if phase == "commit":
            with service.storage._lock, service.storage._connect() as db:
                db.execute("DROP TRIGGER select_ui")
                db.create_function("select_during_publication", 0, None)


def test_real_disconnect_revokes_source_generation_before_session_close(context):
    service, scopes = context
    account, session, key, snapshot = scopes["default"]
    generation = source_context_epoch(service)
    closed = []
    session.close = lambda: closed.append(source_context_epoch(service))
    service.disconnect_configured()
    assert closed == [generation + 1]
    assert read_account_rights_snapshot(service, account).status == "failed"


def test_disconnect_and_restore_same_objects_cannot_reuse_inflight_source(context):
    service, scopes = context
    account, session, key, snapshot = scopes["default"]
    accounts = service.accounts
    sessions = dict(service._sessions)

    def read(_account):
        service.disconnect_configured()
        # Simulate a reconnect restoring identical registry objects and
        # coordinates. Source generation must still revoke the old read.
        service.accounts = accounts
        service._sessions.update(sessions)
        return snapshot

    session.rights_observation_snapshot = read
    result = read_account_rights_snapshot(service, account)
    assert result.status == "failed" and result.rights == ()
    assert service.storage.acquisitions() == service.storage.placements() == []
