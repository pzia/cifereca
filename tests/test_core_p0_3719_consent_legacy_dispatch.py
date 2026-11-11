"""A configured canonical source wins over any historical source-wide opt-in."""
from pathlib import Path

import pytest

from cifereca.brokers import SynchronizationBrokerImpl, IdentityBrokerImpl
from cifereca.core.convergence_execution import CompletedDelivery
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, register


def _write_historical_bnf_choice(service, account, enabled: bool) -> bool:
    """Fixture: reproduce an existing *versioned* legacy switch, not a UI API."""
    from cifereca.core.source_auto_consent_bridge import _source_id
    from cifereca.core.source_sync_consent import scope_for_account

    key = "sync.source.bnf_auto." + _source_id(scope_for_account(account))
    service.storage.metadata_set_with_revision(
        key, "1" if enabled else "0", key + ".revision",
    )
    return enabled


@pytest.mark.parametrize('opening', [False, True])
@pytest.mark.parametrize('allowed', [False, True])
def test_bnf_configured_source_has_one_executor_even_with_legacy_on(
    connected_core, monkeypatch, tmp_path, opening, allowed,
):
    service, session, account, loan, state, downloaded = connected_core
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    session.identity_broker = IdentityBrokerImpl(service)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert _write_historical_bnf_choice(service, account, True)
    for module in ('sync_coordination', 'canonical_selected_offers', 'canonical_selected_delivery',
                   'convergence_execution', 'canonical_recovery'):
        monkeypatch.setattr('cifereca.core.' + module + '.acquisition_lock_file',
                            lambda: tmp_path / 'acquisition.lock')
    broker.set_source_sync_consent(sid, enabled=allowed, expected_revision=None)
    monkeypatch.setattr(service, '_acquire_active_loan_automatically',
                        lambda *_a, **_kw: pytest.fail('canonical source cannot use generic legacy loan worker'))
    calls = []
    def delivered(key, offer):
        calls.append(key)
        directory = service.storage.identity_get(account.identity_id)['directory_name']
        path = service.library_dir() / directory / 'bnf-canonical.pdf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(key, offer.delivery_offer_id, path, SHA, 'synthetic-bnf-completion')
    monkeypatch.setattr(service, '_canonical_right_delivery', delivered, raising=False)
    if opening:
        result = service.opening_sync()
        result = next(row for row in result['identities'] if row['identity_id'] == account.identity_id)
    else:
        result = service.synchronize_account_source(account)
    assert result['canonical_execution']['downloaded'] == int(allowed)
    assert len(calls) == int(allowed)
    assert downloaded == []  # only the delivery callback was simulated
    assert len(service.storage.placements()) == int(allowed)



def test_unversioned_bnf_legacy_on_cannot_trigger_canonical_get(
    connected_core, monkeypatch, tmp_path,
):
    service, session, account, loan, _state, downloaded = connected_core
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    session.identity_broker = IdentityBrokerImpl(service)
    from cifereca.core.source_auto_consent_bridge import _source_id
    from cifereca.core.source_sync_consent import scope_for_account
    switch = "sync.source.bnf_auto." + _source_id(scope_for_account(account))
    # Pre-versioned ON is not a durable, revocable loan admission.
    service.storage.metadata_set(switch, "1")
    calls = []
    monkeypatch.setattr(
        service, "_canonical_right_delivery",
        lambda *_a: calls.append(True) or pytest.fail(
            "unversioned BnF consent must not authorize delivery"
        ),
        raising=False,
    )
    for module in (
        "sync_coordination", "canonical_selected_offers",
        "canonical_selected_delivery", "convergence_execution",
        "canonical_recovery",
    ):
        monkeypatch.setattr(
            "cifereca.core." + module + ".acquisition_lock_file",
            lambda: tmp_path / "acquisition.lock",
        )
    service.synchronize_account_source(account)
    assert calls == [] and downloaded == []
    assert service.storage.placements() == []


@pytest.mark.parametrize("opening", [False, True])
def test_bnf_legacy_source_on_uses_single_canonical_executor_and_verified_placement(
    connected_core, monkeypatch, tmp_path, opening,
):
    """Legacy preference is consent only, never an archived supplier right."""
    service, session, account, loan, state, downloaded = connected_core
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    session.identity_broker = IdentityBrokerImpl(service)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert _write_historical_bnf_choice(service, account, True)
    for module in (
        "sync_coordination", "canonical_selected_offers",
        "canonical_selected_delivery", "convergence_execution",
        "canonical_recovery",
    ):
        monkeypatch.setattr(
            "cifereca.core." + module + ".acquisition_lock_file",
            lambda: tmp_path / "acquisition.lock",
        )
    calls = []

    def delivered(key, offer):
        calls.append(key)
        directory = service.storage.identity_get(account.identity_id)["directory_name"]
        path = service.library_dir() / directory / "bnf-verified-canonical.pdf"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA,
            "bnf-exact-provider-completion",
        )

    monkeypatch.setattr(
        service, "_canonical_right_delivery", delivered, raising=False,
    )
    if opening:
        result = service.opening_sync()
        result = next(row for row in result["identities"]
                      if row["identity_id"] == account.identity_id)
    else:
        result = service.synchronize_account_source(account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert len(calls) == 1 and downloaded == []
    assert len(service.storage.placements()) == 1

    if not opening:
        again = service.synchronize_account_source(account)
        assert again["canonical_execution"]["present_attested"] == 1
        assert again["canonical_execution"]["downloaded"] == 0
        assert len(calls) == 1


@pytest.mark.parametrize("mutation", ["off", "off_on"])
def test_bnf_legacy_consent_revoked_during_canonical_delivery_never_publishes(
    connected_core, monkeypatch, tmp_path, mutation,
):
    service, session, account, loan, _state, downloaded = connected_core
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    session.identity_broker = IdentityBrokerImpl(service)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert _write_historical_bnf_choice(service, account, True)
    for module in (
        "sync_coordination", "canonical_selected_offers",
        "canonical_selected_delivery", "convergence_execution",
        "canonical_recovery",
    ):
        monkeypatch.setattr(
            "cifereca.core." + module + ".acquisition_lock_file",
            lambda: tmp_path / "acquisition.lock",
        )
    calls = []

    def delivery(key, offer):
        calls.append(key)
        assert _write_historical_bnf_choice(service, account, False) is False
        if mutation == "off_on":
            assert _write_historical_bnf_choice(service, account, True) is True
        path = tmp_path / "ephemeral.pdf"
        path.write_bytes(BYTES)
        return CompletedDelivery(
            key, offer.delivery_offer_id, path, SHA,
            "bnf-completion-from-revoked-source",
        )

    monkeypatch.setattr(service, "_canonical_right_delivery", delivery, raising=False)
    result = service.synchronize_account_source(account)
    assert len(calls) == 1
    assert result["canonical_execution"]["downloaded"] == 0
    assert len(service.storage.placements()) == 0
    assert downloaded == []

def test_kobo_canonical_off_never_falls_back_after_missing_typed_inventory(tmp_path, monkeypatch):
    from tests.test_kobo_backup_guard import _service
    from cifereca.core import sync_coordination
    service = _service(tmp_path)
    session = service._sessions[service._session_key('kobo', 'vault-1')]
    account = session.accounts[0]
    service.accounts = [account]
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    service.storage.metadata_set("sync.source.materialization.on_start." + sid, '1')
    broker.set_source_sync_consent(sid, enabled=False, expected_revision=None)
    monkeypatch.setattr(sync_coordination, '_run_observed_canonical_convergence', lambda *_a, **_kw: None)
    monkeypatch.setattr(service, '_sync_kobo_backup_account_locked',
                        lambda *_a, **_kw: pytest.fail('OFF cannot fall back to Kobo legacy'), raising=False)
    try:
        service.opening_sync()
        assert service.storage.placements() == []
    finally:
        service.close()
