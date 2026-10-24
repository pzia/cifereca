"""A delivered exact purchase must remain intelligible to the read-only preview."""
import pytest
from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_numilog_owned_library_download import case


def test_pending_then_complete_receipt_projects_exact_entitlement_without_http(case):
    broker = SynchronizationBrokerImpl(case.service)
    source = broker._source_id(case.account)
    acquired = case.service.acquire_direct_item(case.item)
    receipt = case.service.storage.acquisition_get(acquired.metadata['numilog_checkpoint_id'])
    assert receipt['entitlement_id'] == case.right['entitlement_id']
    pending = broker.preview_local_materialization(source, verify_local_files=False)
    assert pending.ok and pending.facts['counts']['attention_required'] == 1
    assert pending.facts['counts']['needs_reconciliation'] == 0
    case.service.resume_numilog_materialization(receipt['acquisition_id'])
    completed = broker.preview_local_materialization(source, verify_local_files=True)
    counts = completed.facts['counts']
    assert completed.ok and counts['already_local_linked'] == counts['already_local_verified'] == 1
    assert counts['attention_required'] == counts['needs_reconciliation'] == 0
    assert counts['potentially_missing'] == 0
    assert case.calls == [case.item.record_id] and case.inventory == []


@pytest.mark.parametrize('mutation', ['missing','duplicate','public','foreign','loan','revoked','expiry'])
def test_receipt_never_guesses_a_local_entitlement_binding(case, mutation):
    storage = case.service.storage
    right = case.right
    if mutation == 'missing':
        with storage._connect() as db:db.execute('DELETE FROM entitlements')
    if mutation == 'duplicate':storage.entitlement_put({**right,'entitlement_id':'another'})
    if mutation == 'public':storage.entitlement_put({**right,'metadata':{'source':'public_catalog'}})
    # Legacy inconsistent/cross-identity states are deliberately seeded via
    # SQLite. The production entitlement_put() must reject both mutations.
    if mutation == 'foreign':
        with storage._connect() as db:
            db.execute("UPDATE entitlements SET identity_id=? WHERE entitlement_id=?",
                       ('alex', right['entitlement_id']))
    if mutation == 'loan':
        with storage._connect() as db:
            db.execute("UPDATE entitlements SET entitlement_type=?, loan_id=? WHERE entitlement_id=?",
                       ('loan', 'fictional', right['entitlement_id']))
    if mutation == 'revoked':storage.entitlement_put({**right,'state':'revoked'})
    if mutation == 'expiry':storage.entitlement_put({**right,'expires_at':'2030-01-01'})
    acquired = case.service.acquire_direct_item(case.item)
    row = storage.acquisition_get(acquired.metadata['numilog_checkpoint_id'])
    assert row['entitlement_id'] is None
    assert len(case.calls) == 1


def test_competing_ledger_inserted_after_delivery_is_not_overwritten_or_duplicated(case, monkeypatch):
    from cifereca.providers import ProviderConfigurationError
    def competing(*_a):
        case.service.storage.acquisition_put({'acquisition_id':'competing', 'provider_id':'numilog',
            'credential_id':case.account.credential_id, 'account_id':case.account.unique_id,
            'identity_id':case.account.identity_id, 'item_id':case.item.record_id,
            'status':'attention_required'})
    monkeypatch.setattr(case.service.acquisition, 'run_post_download', competing)
    with pytest.raises(ProviderConfigurationError):case.service.acquire_direct_item(case.item)
    assert [r['acquisition_id'] for r in case.service.storage.acquisitions(provider_id='numilog')] == ['competing']
    assert len(case.calls) == 1
