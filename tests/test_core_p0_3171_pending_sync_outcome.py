"""A successful inventory is not a completed A→B Sync with pending rights."""
from dataclasses import replace

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_canonical_sync_stage import rig as explicit_rig
from tests.test_core_p0_3171_opening_canonical_rights import rig as opening_rig
from tests.test_core_p0_3171_opening_convergence_executor import _lock_paths


def successful(snapshot, *, offer):
    row = replace(snapshot.rights[0], delivery='available' if offer else 'unknown',
                  delivery_offer_id='synthetic-offer' if offer else '')
    return replace(snapshot, status='success', rights=(row,))


@pytest.mark.parametrize('offer', [False, True])
def test_explicit_inventory_success_with_pending_convergence_is_partial(explicit_rig, offer):
    service, db, root = explicit_rig
    service.snapshot = successful(service.snapshot, offer=offer)
    service._canonical_persisted_consent = lambda _: frozenset()
    service._canonical_right_delivery = lambda *_: pytest.fail('pending right cannot GET')
    result = synchronize_account_source(service, service.account)
    assert result['canonical_rights']['status'] == 'success'
    assert result['canonical_execution']['pending'] == 1
    assert result['canonical_execution']['failed'] == result['canonical_execution']['blocked'] == 0
    assert result['status'] == 'partial'
    assert db.placements() == []


@pytest.mark.parametrize('offer', [False, True])
def test_opening_inventory_success_with_pending_convergence_is_partial(tmp_path, monkeypatch, offer):
    service, account, source, snapshot, session, reads = opening_rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    session.rights_observation_snapshot = lambda _: successful(snapshot, offer=offer)
    service._canonical_right_delivery = lambda *_: pytest.fail('pending right cannot GET')
    try:
        result = service.opening_sync()
        own = next(row for row in result['identities'] if row['identity_id'] == 'default')
        assert own['canonical_execution']['pending'] == 1
        assert own['canonical_execution']['failed'] == own['canonical_execution']['blocked'] == 0
        assert own['errors'] == 0
        assert own['status'] == 'partial'
        assert service.storage.placements() == []
    finally:
        service.close()


def test_explicit_completed_convergence_and_second_sync_remain_successful(explicit_rig):
    from cifereca.core.convergence_execution import CompletedDelivery
    from tests.test_core_p0_3171_atomic_publication_v14 import BYTES, SHA
    service, db, root = explicit_rig
    service.snapshot = successful(service.snapshot, offer=True)
    service._canonical_persisted_consent = lambda _: frozenset({service.source})
    calls = []
    def deliver(key, right):
        calls.append(key)
        path = root / 'A' / 'completed.pdf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(BYTES)
        return CompletedDelivery(key, right.delivery_offer_id, path, SHA, 'independent-receipt')
    service._canonical_right_delivery = deliver
    for expected in ('downloaded', 'present_attested'):
        result = synchronize_account_source(service, service.account)
        assert result['status'] == 'ok'
        assert result['canonical_execution'][expected] == 1
        assert result['canonical_execution']['pending'] == 0
    assert calls == [service.source] and len(db.placements()) == 1


def _right_state(snapshot, state):
    snapshot = successful(snapshot, offer=False)
    if state == 'empty':
        return replace(snapshot, rights=())
    row = snapshot.rights[0]
    if state == 'inactive':
        row = replace(row, state='inactive')
    elif state == 'archived':
        row = replace(row, delivery='unavailable', delivery_unavailable_reason='archived')
    return replace(snapshot, rights=(row,))


@pytest.mark.parametrize('state', ['active', 'inactive', 'empty', 'archived'])
def test_no_delivery_route_reports_unsatisfied_right_without_false_pending(explicit_rig, state):
    from cifereca.core.canonical_sync_stage import persist_attested_source_references
    service, db, root = explicit_rig
    persist_attested_source_references(db, service.snapshot)
    service.snapshot = _right_state(service.snapshot, state)
    result = synchronize_account_source(service, service.account)
    assert 'canonical_execution' not in result
    assert result['canonical_rights']['status'] == 'success'
    assert result['canonical_rights']['pending'] == (1 if state == 'active' else 0)
    assert result['status'] == ('partial' if state == 'active' else 'ok')
    with db._lock, db._connect() as sql:
        assert sql.execute('SELECT state FROM source_references').fetchall() == [('active',)]
    assert db.placements() == []


@pytest.mark.parametrize('state', ['active', 'inactive', 'empty', 'archived'])
def test_opening_without_route_uses_plan_without_counting_inactive_history(tmp_path, monkeypatch, state):
    service, account, source, snapshot, session, reads = opening_rig(tmp_path, monkeypatch)
    _lock_paths(monkeypatch, tmp_path)
    session.rights_observation_snapshot = lambda _: _right_state(snapshot, state)
    try:
        result = service.opening_sync()
        own = next(row for row in result['identities'] if row['identity_id'] == 'default')
        assert 'canonical_execution' not in own
        assert own['errors'] == 0
        assert own['status'] == ('partial' if state == 'active' else 'success')
        assert service.storage.placements() == []
    finally:
        service.close()


def test_executor_reports_attested_inactive_right_as_history_not_pending(explicit_rig):
    service, db, root = explicit_rig
    service.snapshot = _right_state(service.snapshot, 'inactive')
    service._canonical_persisted_consent = lambda _: frozenset()
    service._canonical_right_delivery = lambda *_: pytest.fail('inactive right cannot GET')
    result = synchronize_account_source(service, service.account)
    assert result['status'] == 'ok'
    assert result['canonical_execution']['inactive'] == 1
    assert result['canonical_execution']['pending'] == 0
    assert db.placements() == []
