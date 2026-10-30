"""A configured canonical source must never fall through to a legacy worker."""
from pathlib import Path

import pytest

from cifereca.brokers import SynchronizationBrokerImpl, IdentityBrokerImpl
from cifereca.core.convergence_execution import CompletedDelivery
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA, register


@pytest.mark.parametrize('opening', [False, True])
@pytest.mark.parametrize('allowed', [False, True])
def test_bnf_configured_source_has_one_executor_even_with_legacy_on(
    connected_core, monkeypatch, tmp_path, opening, allowed,
):
    from cifereca.core import bnf_auto_materialization
    service, session, account, loan, state, downloaded = connected_core
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    session.identity_broker = IdentityBrokerImpl(service)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert broker.set_bnf_auto_download(sid, True)
    for module in ('sync_coordination', 'canonical_selected_offers', 'canonical_selected_delivery',
                   'convergence_execution', 'canonical_recovery'):
        monkeypatch.setattr('cifereca.core.' + module + '.acquisition_lock_file',
                            lambda: tmp_path / 'acquisition.lock')
    broker.set_source_sync_consent(sid, enabled=allowed, expected_revision=None)
    monkeypatch.setattr(bnf_auto_materialization, 'materialize',
                        lambda *_a, **_kw: pytest.fail('canonical source cannot use BnF legacy worker'))
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


def test_kobo_canonical_off_never_falls_back_after_missing_typed_inventory(tmp_path, monkeypatch):
    from tests.test_kobo_backup_guard import _service
    from cifereca.core import sync_coordination
    from cifereca.core.source_materialization_schedule import policy_key
    service = _service(tmp_path)
    session = service._sessions[service._session_key('kobo', 'vault-1')]
    account = session.accounts[0]
    service.accounts = [account]
    register(service.storage, account.identity_id, account.provider_id,
             account.credential_id, account=account.unique_id)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    service.storage.metadata_set(policy_key(sid), '1')
    broker.set_source_sync_consent(sid, enabled=False, expected_revision=None)
    monkeypatch.setattr(sync_coordination, '_run_observed_canonical_convergence', lambda *_a, **_kw: None)
    monkeypatch.setattr(service, '_sync_kobo_backup_account_locked',
                        lambda *_a, **_kw: pytest.fail('OFF cannot fall back to Kobo legacy'))
    try:
        service.opening_sync()
        assert service.storage.placements() == []
    finally:
        service.close()
