"""#3171: one guarded Sync stage with two synthetic providers and real SQLite.

No provider network. A fake GET is counted and may replace the Core session
in-flight; no file publication may survive that transition.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.convergence_execution import (
    CompletedDelivery, execute_admitted_convergence,
)
from cifereca.locking import InterProcessLock
from cifereca.models import Account
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, register, rows,
)


class FakeService:
    def __init__(self, storage, accounts):
        self.storage = storage
        self.accounts = list(accounts)
        self._sessions = {}
        self._account_context_epoch = 1
        self.enabled = True
        self.calls = 0
        for account in accounts:
            self._sessions[self._session_key(
                account.provider_id, account.credential_id,
            )] = NS(
                provider_id=account.provider_id,
                credential_id=account.credential_id,
                connected=True, _closed=False,
            )

    @staticmethod
    def _session_key(provider_id, credential_id):
        return provider_id, credential_id

    def sync_source_enabled(self, account):
        return self.enabled


@pytest.fixture
def rig(tmp_path, monkeypatch):
    storage = Storage(tmp_path / "store.sqlite")
    account = Account(
        identity_id="A", provider_id="seller-a", credential_id="card-A",
        unique_id="buyer-A",
    )
    register(storage, "A", "seller-a", "card-A", account="buyer-A")
    service = FakeService(storage, [account])
    source, snapshot = new_source(
        "A", provider="seller-a", credential="card-A", account="buyer-A",
    )
    offered = replace(snapshot, rights=(replace(
        snapshot.rights[0], delivery="available", delivery_offer_id="synthetic-offer",
    ),))
    root = tmp_path / "EBooks"
    lock_path = tmp_path / "state" / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.convergence_execution.acquisition_lock_file",
        lambda: lock_path,
    )
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
        lambda: lock_path,
    )
    try:
        yield service, account, source, offered, root, lock_path
    finally:
        storage.close()


def run(service, account, snapshot, root, lock_path, consent, deliver,
        consent_still_current=None):
    with InterProcessLock(lock_path) as lease:
        return execute_admitted_convergence(
            service, account, service._sessions[(
                account.provider_id, account.credential_id,
            )], snapshot, admitted_epoch=service._account_context_epoch,
            shared_lock=lease, library_root=root,
            persisted_consent=frozenset(consent),
            consent_still_current=consent_still_current or (
                lambda key: key in consent
            ),
            deliver=deliver,
        )


def fake_delivery(service, root, profile="A", *, on_get=None):
    def getter(source, observed_right):
        service.calls += 1
        if on_get is not None:
            on_get(source)
        path = root / profile / "book.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            source, observed_right.delivery_offer_id, path, SHA,
            "synthetic-completed-delivery-" + source.right_id,
        )
    return getter


def test_one_sync_download_then_second_sync_uses_verified_origin_without_get(rig):
    service, account, source, snapshot, root, lock_path = rig
    delivery = fake_delivery(service, root)
    first = run(service, account, snapshot, root, lock_path, {source}, delivery)
    assert [(row.status, row.reason) for row in first] == [
        ("downloaded", "canonical_delivery_committed"),
    ]
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.calls == 1
    before = service.storage.placements()
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("verified local file must avoid a new GET"),
    )
    assert second[0].status == "present_attested"
    assert second[0].reason == "exact_verified_origin"
    assert service.storage.placements() == before
    assert rows(service.storage) == (1, 1, 1, 1)


@pytest.mark.parametrize("condition", ["no_consent", "unknown_offer", "inactive_right"])
def test_without_admitted_offer_active_right_and_configured_consent_no_get(rig, condition):
    service, account, source, snapshot, root, lock_path = rig
    consent = frozenset({source})
    if condition == "no_consent":
        consent = frozenset()
    elif condition == "unknown_offer":
        snapshot = replace(snapshot, rights=(replace(
            snapshot.rights[0], delivery="unknown", delivery_offer_id="",
        ),))
    else:
        snapshot = replace(snapshot, rights=(replace(
            snapshot.rights[0], delivery="unknown", delivery_offer_id="",
            state="inactive",
        ),))
    result = run(
        service, account, snapshot, root, lock_path, consent,
        lambda *_: pytest.fail("missing consent/right/offer MUST deny GET"),
    )
    assert len(result) == 1 and result[0].status == "pending"
    assert rows(service.storage) == (0, 0, 0, 0)


def test_rebind_while_downloading_never_creates_a_foreign_origin(rig):
    service, account, source, snapshot, root, lock_path = rig
    session = service._sessions[("seller-a", "card-A")]
    def change_owner(_source):
        session.connected = False
    result = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root, on_get=change_owner),
    )
    assert result[0].status == "blocked"
    assert result[0].reason == "scope_rebound_after_get"
    assert rows(service.storage) == (0, 0, 0, 0)
    assert (root / "A" / "book.pdf").read_bytes() == BYTES


def test_core_epoch_changed_between_account_read_and_dispatch_denies_get(rig):
    service, account, source, snapshot, root, lock_path = rig
    service._account_context_epoch += 1
    with InterProcessLock(lock_path) as lease:
        with pytest.raises(ValueError, match="convergence_session_rebound"):
            execute_admitted_convergence(
                service, account, service._sessions[("seller-a", "card-A")],
                snapshot, admitted_epoch=1, shared_lock=lease, library_root=root,
                persisted_consent=frozenset({source}),
                consent_still_current=lambda _: True,
                deliver=lambda *_: pytest.fail("old epoch must not GET"),
            )
    assert rows(service.storage) == (0, 0, 0, 0)


def test_without_shared_core_lock_no_get_possible(rig):
    service, account, source, snapshot, root, lock_path = rig
    lease = InterProcessLock(lock_path)
    with pytest.raises(ValueError, match="convergence_core_admission_required"):
        execute_admitted_convergence(
            service, account, service._sessions[("seller-a", "card-A")],
            snapshot, admitted_epoch=1, shared_lock=lease, library_root=root,
            persisted_consent=frozenset({source}),
            consent_still_current=lambda _: True,
            deliver=lambda *_: pytest.fail("never GET without lock"),
        )


def test_two_interchangeable_provider_adapters_share_sha_not_profile_rights(rig):
    service, account_a, source_a, snap_a, root, lock_path = rig
    b = Account(
        identity_id="B", provider_id="seller-b", credential_id="card-B",
        unique_id="buyer-B",
    )
    register(service.storage, "B", "seller-b", "card-B", account="buyer-B")
    service.accounts.append(b)
    service._sessions[("seller-b", "card-B")] = NS(
        provider_id="seller-b", credential_id="card-B",
        connected=True, _closed=False,
    )
    source_b, snap_b = new_source(
        "B", provider="seller-b", credential="card-B", account="buyer-B",
    )
    snap_b = replace(snap_b, rights=(replace(
        snap_b.rights[0], delivery="available", delivery_offer_id="synthetic-offer-B",
    ),))
    assert run(
        service, account_a, snap_a, root, lock_path, {source_a},
        fake_delivery(service, root, profile="A"),
    )[0].status == "downloaded"
    assert run(
        service, b, snap_b, root, lock_path, {source_b},
        fake_delivery(service, root, profile="B"),
    )[0].status == "downloaded"
    assert rows(service.storage) == (1, 2, 2, 2)
    assert run(
        service, b, snap_b, root, lock_path, {source_b},
        lambda *_: pytest.fail("B already physically attested"),
    )[0].status == "present_attested"
    assert service.storage.entitlements() == []


@pytest.mark.parametrize("mismatch", ["foreign_source", "other_offer"])
def test_completed_delivery_foreign_right_or_offer_cannot_publish(rig, mismatch):
    service, account, source, snapshot, root, lock_path = rig

    def wrong(key, row):
        service.calls += 1
        path = root / "A" / "book.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(BYTES)
        foreign, _ = new_source(
            "A", provider="seller-a", credential="other-card",
            account="buyer-A",
        )
        return CompletedDelivery(
            foreign if mismatch == "foreign_source" else key,
            "other-offer" if mismatch == "other_offer" else row.delivery_offer_id,
            path, SHA, "unrelated-completed-receipt",
        )
    result = run(service, account, snapshot, root, lock_path, {source}, wrong)
    assert service.calls == 1
    assert result[0].status == "failed"
    assert rows(service.storage) == (0, 0, 0, 0)


def test_consent_revoked_during_get_blocks_canonical_persistence(rig):
    service, account, source, snapshot, root, lock_path = rig
    allowed = {source}
    def revoke(_key):
        allowed.clear()
    result = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root, on_get=revoke),
        consent_still_current=lambda key: key in allowed,
    )
    assert service.calls == 1
    assert result[0].status == "blocked"
    assert result[0].reason == "consent_revoked_after_get"
    assert rows(service.storage) == (0, 0, 0, 0)
    assert (root / "A" / "book.pdf").read_bytes() == BYTES


@pytest.mark.parametrize("when", ["before", "after"])
def test_consent_lookup_exception_is_never_approval(rig, when):
    service, account, source, snapshot, root, lock_path = rig
    checks = []
    def lookup(_key):
        checks.append(1)
        if when == "before" or len(checks) > 1:
            raise RuntimeError("synthetic unreadable consent store")
        return True
    result = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root), consent_still_current=lookup,
    )
    assert result[0].status == ("pending" if when == "before" else "blocked")
    assert service.calls == (0 if when == "before" else 1)
    assert rows(service.storage) == (0, 0, 0, 0)


def test_new_canonical_placement_is_indexed_without_full_library_scan(rig):
    service, account, source, snapshot, root, lock_path = rig
    delivered = fake_delivery(service, root)
    result = run(service, account, snapshot, root, lock_path, {source}, delivered)
    assert result[0].status == "downloaded"
    entries = service.storage.catalog_entries()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["artifact_id"] == "sha256:" + SHA
    assert entry["identity_id"] == "A"
    assert entry["local_state"] == "local"
    assert entry["placement_id"] == service.storage.placements()[0]["placement_id"]
    assert service.storage.entitlements() == []
    assert run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("idempotent second Sync must not GET"),
    )[0].status == "present_attested"
    assert service.storage.catalog_entries() == entries


def test_postcommit_catalogue_failure_does_not_rollback_origin_or_repeat_get(
    rig, monkeypatch,
):
    from cifereca.catalog import LocalCatalog

    service, account, source, snapshot, root, lock_path = rig
    def fail_index(*_args, **_kw):
        raise RuntimeError("synthetic catalogue projection interruption")
    monkeypatch.setattr(LocalCatalog, "index_path", fail_index)
    result = run(
        service, account, snapshot, root, lock_path, {source},
        fake_delivery(service, root),
    )
    assert result[0].status == "downloaded"
    assert rows(service.storage) == (1, 1, 1, 1)
    assert service.storage.catalog_entries() == []
    second = run(
        service, account, snapshot, root, lock_path, {source},
        lambda *_: pytest.fail("committed origin avoids a repeated GET"),
    )
    assert second[0].status == "present_attested"
    assert service.calls == 1
    assert rows(service.storage) == (1, 1, 1, 1)
