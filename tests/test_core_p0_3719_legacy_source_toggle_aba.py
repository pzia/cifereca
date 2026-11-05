"""#3719: a legacy source toggle must revoke the in-flight acquisition epoch.

All tests run with synthetic accounts and SQLite v14; no provider network.
The generic versioned source consent and the Numilog atomic toggle already
own their own epochs and are intentionally not rewritten here.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.source_auto_consent_bridge import (
    _consent_generation_key, consent_generation,
)
from cifereca.core.source_sync_consent import scope_for_account
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.storage import Storage
from tests.test_core_0978_source_state import fake_broker
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_kobo_consent_aba import _kobo


@pytest.mark.parametrize(("provider", "has_legacy_epoch"), [
    ("kobo", True), ("bnf-pret", True),
    ("paris", False), ("fictional", False),
])
def test_legacy_source_switch_uses_existing_acquisition_generation(
    tmp_path, provider, has_legacy_epoch,
):
    storage = Storage(tmp_path / "toggle.sqlite")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        account.provider_id = provider
        scope = scope_for_account(account)
        sid = broker._source_id(account)
        revision_key = _consent_generation_key(scope)
        assert (revision_key is not None) is has_legacy_epoch
        assert consent_generation(storage, scope) is None
        first = broker.set_enabled(sid, False)
        assert first.enabled is False
        assert storage.metadata_get(broker._preference_key(sid)) == "0"
        disabled_revision = consent_generation(storage, scope)
        if has_legacy_epoch:
            assert isinstance(disabled_revision, str) and len(disabled_revision) == 32
            assert storage.metadata_get(revision_key) == disabled_revision
        else:
            assert disabled_revision is None
        second = broker.set_enabled(sid, True)
        assert second.enabled is True
        assert storage.metadata_get(broker._preference_key(sid)) == "1"
        if has_legacy_epoch:
            assert consent_generation(storage, scope) != disabled_revision
            assert len(consent_generation(storage, scope)) == 32
        else:
            assert consent_generation(storage, scope) is None
    finally:
        storage.close()


@pytest.mark.parametrize("provider", ["kobo", "bnf-pret"])
def test_failed_legacy_source_switch_rolls_back_epoch_and_boolean(
    tmp_path, provider,
):
    storage = Storage(tmp_path / "rollback.sqlite")
    try:
        broker = fake_broker(storage)
        account = broker.service.accounts[0]
        account.provider_id = provider
        scope = scope_for_account(account)
        sid = broker._source_id(account)
        broker.set_enabled(sid, True)
        preference_key = broker._preference_key(sid)
        initial = (storage.metadata_get(preference_key), consent_generation(storage, scope))
        assert initial[0] == "1" and len(initial[1]) == 32
        with storage._lock, storage._connect() as db:
            db.execute(
                "CREATE TRIGGER block_source_off BEFORE UPDATE ON metadata "
                "WHEN OLD.key='" + preference_key + "' "
                "BEGIN SELECT RAISE(ABORT,'synthetic_source_toggle_failure'); END"
            )
        try:
            with pytest.raises(sqlite3.IntegrityError, match="synthetic_source_toggle_failure"):
                broker.set_enabled(sid, False)
            assert (storage.metadata_get(preference_key),
                    consent_generation(storage, scope)) == initial
        finally:
            with storage._lock, storage._connect() as db:
                db.execute("DROP TRIGGER block_source_off")
    finally:
        storage.close()


def test_kobo_legacy_on_off_on_mid_get_cannot_revive_previous_consent(rig):
    service, storage, root, materialization_policy = _kobo(rig)
    storage.metadata_set(materialization_policy, "1")
    assert consent_generation(storage, service.source.scope) is None
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(service.account)
    calls = []

    def delivery(key, offer):
        calls.append(offer.delivery_offer_id)
        path = root / "A" / "stale-legacy-attempt.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        broker.set_enabled(sid, False)
        broker.set_enabled(sid, True)
        # The materialization opt-in remains ON; only the independent source
        # switch was cycled. The original GET must not inherit the new epoch.
        assert storage.metadata_get(materialization_policy) == "1"
        assert consent_generation(storage, service.source.scope) is not None
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA, "stale-legacy-receipt",
        )

    service._canonical_right_delivery = delivery
    result = synchronize_account_source(service, service.account)
    assert calls == ["kobo-verified-private-offer"]
    assert result["canonical_execution"]["downloaded"] == 0
    assert storage.placements() == [] and refs(storage)[1] == 0
